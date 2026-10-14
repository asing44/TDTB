"""commit.py — T14: live commit writers A–E (pure, idempotent, self-verifying).

Executes the Phase-5 write contract that ``shadow.py`` (T13) previews. Consumes
a ``ShadowDiff`` (the manifest already classified against live state) and
performs the real writes, then reads back and reconciles each surface against
intent.

Guarantees (council-mandated, folded from the 2026-07-12 verdict):

  - **UPDATE/CREATE partition invariant.** Every write intent is exactly one of
    ``create`` / ``update`` / ``noop`` — derived from the ``ShadowDiff``
    classification, never re-guessed. A ``create`` carries no live id; an
    ``update`` always does. ``plan_writes`` asserts this.
  - **Idempotent.** Every writer re-checks live state at write time
    (check-before-write); a create whose target already exists collapses to a
    no-op, so re-invocation never double-creates.
  - **Pre-write calendar-ID assertion.** ``plan_writes`` fails (never writes) on
    an unresolved calendar target; ``write_calendar`` additionally calls
    ``calendar_bridge.assert_write_target`` immediately before EVERY event write.
  - **Post-write reconciliation.** After each writer, read back what landed and
    diff against intent — item-count match (silent-drop guard, 2026-06-23
    class) and id/surface match (wrong-surface guard, 2026-06-08 class). A
    mismatch yields ``WriterResult.ok = False`` with a surfaced reason; success
    is never silent.

Layering mirrors ``calendar_bridge`` / ``shadow``: the pure planner
(``plan_writes``) is unit-tested with fakes; the writer functions take injected
clients (``TodoistClient``, ``EventStore``) so tests never touch
the network or EventKit. Orchestration — ordered dispatch, a persisted success
ledger, partial-failure honesty, and resume — is **T15's** job (``run_commit``
here is only a thin sequential driver for the manual-verify gate).

``recent-selections`` is NOT a writer here — it is a post-commit action
(``runstate.append_recent_selection``) outside this write contract, invoked by
the orchestrator after a clean run.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol
from zoneinfo import ZoneInfo

_GATHER_DIR = str(Path(__file__).parent / "gather")
if _GATHER_DIR not in sys.path:
    sys.path.insert(0, _GATHER_DIR)

import calendar_bridge  # noqa: E402
import capacities_mirror  # noqa: E402
from shadow import (  # noqa: E402
    CONFLICT,
    CREATE,
    NOOP,
    UNAVAILABLE,
    UPDATE,
    ManifestEntry,
    ShadowDiff,
    ShadowDiffEntry,
)

# Fallback PHEP project id (config-first; this mirrors config_reader's
# FALLBACK_TODOIST_PROJECTS so a config-less test path still routes PHEP).
_FALLBACK_PHEP_PROJECT_ID = "6fgXPMw28j7cRFMH"


class CommitPlanError(Exception):
    """A write intent could not be safely planned — an unresolved calendar
    target, a would-write against an ``unavailable`` surface, or a ``conflict``
    row (missing target). Raised at plan time so nothing is written blind."""


# ---------------------------------------------------------------------------
# Injected-client protocols (structural — the real TodoistClient / EventStore
# satisfy these; tests pass fakes).
# ---------------------------------------------------------------------------

class TodoistLike(Protocol):
    def get_filter_tasks(self, filter_id_or_query: str, limit: int | None = ...) -> list[dict]: ...
    def get_task(self, task_id: str) -> dict: ...
    def create_task(self, content: str, project_id: str | None = ..., **fields: Any) -> dict: ...
    def reschedule_task(self, task_id: str, due_string: str) -> dict: ...
    def reschedule_task_datetime(self, task_id: str, due_datetime: str) -> dict: ...


class EventStoreLike(Protocol):
    def calendars(self) -> list[calendar_bridge.CalendarInfo]: ...
    def query_events(self, start: datetime, end: datetime,
                     calendar_ids: list[str] | None = ...) -> list[dict]: ...
    def create_event(self, spec: calendar_bridge.EventSpec) -> str: ...
    def get_event(self, event_id: str) -> dict | None: ...


# ---------------------------------------------------------------------------
# Data shapes
# ---------------------------------------------------------------------------

@dataclass
class WriteIntent:
    """One concrete write, fully resolved and partitioned. ``op`` is the
    partition axis: exactly one of create/update/noop."""
    step: str            # A | B | C | D | D′ | E
    surface: str          # todoist | calendar
    op: str               # create | update | noop
    name: str
    # todoist
    task_id: str | None = None
    project_id: str | None = None
    due_time: str | None = None
    duration_min: int = 0
    # recurring retimes must use the datetime form or the pattern is wiped
    is_recurring: bool = False
    due_datetime: str | None = None  # "YYYY-MM-DDTHH:MM:SS", set when is_recurring
    # pinned recurring no-op: live time intentionally differs from the planned
    # slot (the pattern owns the time) — reconciliation must not assert due
    pinned: bool = False
    # Server-derived native time for protected Todoist rows. When present,
    # reconciliation verifies the native due rather than the planned slot.
    native_start: str | None = None
    # Internal provenance/authorization markers.  ``native_start`` is also
    # carried on explicitly authorized rows so planning can retain the
    # server-derived source value without making the row immutable.
    native_provenance: bool = False
    retiming_authorized: bool | None = None
    # calendar
    calendar_id: str | None = None
    start: datetime | None = None
    end: datetime | None = None
    # date-only Todoist work / captures
    due_all_day_today: bool = False   # due_string "today", no time
    payload: dict[str, Any] | None = None  # B6: the captures key/value map
    # Capacities mirror lane (contract change 2026-10-13): a PHEP Todoist
    # create mirroring a Capacities calendar block. The receipt store keys on
    # ``capacities_identity``; ``description`` carries the capacities link.
    capacities_mirror: bool = False
    capacities_identity: str | None = None
    description: str | None = None

    def as_dict(self) -> dict[str, Any]:
        d = {
            "step": self.step, "surface": self.surface, "op": self.op,
            "name": self.name, "task_id": self.task_id, "project_id": self.project_id,
            "due_time": self.due_time, "duration_min": self.duration_min,
            "is_recurring": self.is_recurring, "due_datetime": self.due_datetime,
            "pinned": self.pinned, "native_start": self.native_start,
            "calendar_id": self.calendar_id,
            "start": self.start.isoformat() if self.start else None,
            "end": self.end.isoformat() if self.end else None,
        }
        return d


@dataclass
class DueReading:
    """Normalized reading of a Todoist ``due`` object for verification.

    ``local_hhmm`` is the wall-clock value to compare against local intent: a
    UTC-anchored fixed due is converted through the due's ``timezone``
    (zoneinfo); a floating local due passes through unchanged. ``raw`` and
    ``timezone`` carry the canonical wire values for machine fields.
    ``error`` is set — never raised — when the due cannot be compared safely
    (fail closed instead of guessing, FEEDBACK-23)."""
    local_hhmm: str | None = None
    raw: str | None = None
    timezone: str | None = None
    error: str | None = None


@dataclass
class WriterResult:
    """Outcome of one writer over its intents — the reconciliation verdict.

    ``ok`` is the honest-success flag: True only when every intended write
    landed AND read back matching intent. ``created``/``updated``/``noops``
    hold the ids (or paths) actually touched; ``reconciliation`` carries the
    count/id evidence; ``error`` names the first mismatch when ``ok`` is False.
    """
    step: str
    surface: str
    ok: bool = True
    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    noops: list[str] = field(default_factory=list)
    reconciliation: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    # D1 (ui-parity T9): one entry PER write whose read-back contradicted
    # intent — never collapsed to just the first error. Any entry blocks a
    # bake-in PASS.
    verify_failures: list[str] = field(default_factory=list)
    # FEEDBACK-23: structured per-failure records, one per verify_failures
    # entry (kept in lockstep). "due" records carry machine-canonical values
    # — 24h HH:MM intent/live, raw ISO due, IANA timezone — so the wire never
    # depends on display-formatted strings; display strings live in
    # verify_failures. "plain" records carry the original message for any
    # non-due failure (readback/silent-drop/calendar).
    verify_details: list[dict[str, Any]] = field(default_factory=list)
    # T19: intent name → landed id, so post-commit consumers (micro-adventure
    # history) can recover a specific row's id without re-querying by content.
    touched: dict[str, str] = field(default_factory=dict)

    def fail(self, msg: str) -> None:
        """Record one per-write verification failure (D1)."""
        self.ok = False
        self.error = self.error or msg
        self.verify_failures.append(msg)
        self.verify_details.append({"kind": "plain", "message": msg})

    def fail_calendar(
        self,
        name: str,
        mismatches: list[dict[str, Any]],
        message: str,
    ) -> None:
        """Record one calendar-readback verification failure with
        machine-canonical detail (FEEDBACK-26).

        ``mismatches`` carries canonical intent/live values (ISO datetimes,
        minutes) so machine consumers never parse display text; ``message``
        is the user-facing display string (12-hour formatted)."""
        self.ok = False
        self.error = self.error or message
        self.verify_failures.append(message)
        self.verify_details.append({
            "kind": "calendar",
            "name": name,
            "mismatches": mismatches,
            "message": message,
        })

    def fail_due(
        self,
        name: str,
        intent_hhmm: str | None,
        reading: DueReading,
        reason: str,
        message: str,
    ) -> None:
        """Record one due-verification failure with machine-canonical detail.

        ``message`` is the user-facing display string (12-hour formatted, no
        raw 24-hour values); the structured record keeps canonical 24h and
        raw ISO/timezone values so machine consumers never parse display
        text (FEEDBACK-23)."""
        self.ok = False
        self.error = self.error or message
        self.verify_failures.append(message)
        self.verify_details.append({
            "kind": "due",
            "name": name,
            "intent": intent_hhmm,
            "live": reading.local_hhmm,
            "live_raw": reading.raw,
            "live_timezone": reading.timezone,
            "reason": reason,
            "message": message,
        })

    def as_dict(self) -> dict[str, Any]:
        return {
            "step": self.step, "surface": self.surface, "ok": self.ok,
            "created": self.created, "updated": self.updated, "noops": self.noops,
            "reconciliation": self.reconciliation, "error": self.error,
            "verify_failures": self.verify_failures,
            "verify_details": self.verify_details, "touched": self.touched,
        }


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _hhmm_to_dt(day: date, hhmm: str | None) -> datetime | None:
    if not hhmm:
        return None
    try:
        h, m = (int(p) for p in hhmm.split(":"))
    except (ValueError, AttributeError):
        return None
    return datetime(day.year, day.month, day.day, h, m)


def _due_string(hhmm: str | None) -> str | None:
    """Todoist natural-language due for a timed task today. ``None`` time means
    no due (shouldn't occur for Step A rows, which always carry a start)."""
    return f"today at {hhmm}" if hhmm else None


def _canonical_hhmm(value: Any) -> str | None:
    """Return a canonical local ``HH:MM`` value when ``value`` is parseable."""
    if isinstance(value, datetime):
        return value.strftime("%H:%M")
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if "T" in text:
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed.strftime("%H:%M")
    parts = text.split(":")
    if len(parts) != 2:
        return None
    try:
        hour, minute = (int(part) for part in parts)
    except ValueError:
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return f"{hour:02d}:{minute:02d}"


def _native_start_value(value: Any) -> str | None:
    """Keep an invalid native value visible so verification can fail closed."""
    if value is None:
        return None
    return _canonical_hhmm(value) or str(value).strip()


def _native_start_from_detail(detail: dict[str, Any]) -> str | None:
    """Recover the server-derived native start from current diff shapes.

    The active shadow contract carries a protected row's native time as
    ``detail['due_time']['live']``. Newer callers may expose the same value as
    ``native_start`` (or an equivalent live/native key), so accept both forms
    without treating an ordinary update's planned due as native timing.
    """
    for key in ("native_start", "native_time", "native_due_time", "live_start"):
        if detail.get(key) is not None:
            value = detail[key]
            return _native_start_value(value)
    due_time = detail.get("due_time")
    if isinstance(due_time, dict) and due_time.get("live") is not None:
        value = due_time["live"]
        return _native_start_value(value)
    return None


def _config_phep_project_id(config: Any) -> str:
    """PHEP project id, config-first with the FALLBACK_TODOIST_PROJECTS default.

    Accepts a TdtbConfig (``get_todoist_project_id``) or a plain dict
    (``todoist_projects`` / ``projects`` map), degrading to the fallback id so a
    config-less path still routes PHEP correctly (spec § 3.2: all project efforts
    round-trip through PHEP)."""
    getter = getattr(config, "get_todoist_project_id", None)
    if callable(getter):
        try:
            cv = getter("PHEP")
            val = getattr(cv, "value", cv)
            if val:
                return str(val)
        except Exception:  # noqa: BLE001 — config miss degrades to fallback
            pass
    if isinstance(config, dict):
        for key in ("todoist_projects", "projects"):
            m = config.get(key)
            if isinstance(m, dict) and m.get("PHEP"):
                return str(m["PHEP"])
    return _FALLBACK_PHEP_PROJECT_ID


# ---------------------------------------------------------------------------
# 1. plan_writes — pure
# ---------------------------------------------------------------------------

def plan_writes(
    diff: ShadowDiff,
    resolved_calendar_ids: dict[str, str],
    config: Any = None,
    today: date | None = None,
) -> list[WriteIntent]:
    """Turn a classified ``ShadowDiff`` into concrete, partitioned write
    intents. **Pure** — no I/O.

    Partition invariant: every todoist/calendar intent gets
    ``op ∈ {create, update, noop}`` straight from the diff classification —
    ``CREATE`` → create (no live id), ``UPDATE`` → update (live id present),
    ``NOOP`` → noop. Nothing re-guesses the split.

    Safety refusals (raise ``CommitPlanError`` — write nothing blind):
      - any ``UNAVAILABLE`` entry (the surface degraded on read; writing risks
        a double-create),
      - any ``CONFLICT`` entry (target missing / contradicted),
      - a calendar row whose ``routing`` logical name is absent from
        ``resolved_calendar_ids`` (pre-write calendar-ID assertion at plan time).
    """
    today = today or date.today()
    intents: list[WriteIntent] = []
    # G29b: collect EVERY unplannable item — don't stop at the first — so a
    # multi-item refusal names every offending row, not just one.
    problems: list[str] = []

    for e in diff.entries:
        m = e.manifest
        cls = e.classification

        if cls == UNAVAILABLE:
            problems.append(
                f"{m.step}/{m.name}: {m.system} surface unavailable "
                "(would write blind — refusing)"
            )
            continue
        if cls == CONFLICT:
            problems.append(
                f"{m.step}/{m.name}: conflict "
                f"({e.detail.get('reason', 'unspecified')})"
            )
            continue

        if m.system == "todoist":
            intents.append(_plan_todoist(m, e, config, today))
        elif m.system == "calendar":
            cal_id = _resolve_calendar_id(m.routing, resolved_calendar_ids)
            if cal_id is None:
                problems.append(
                    f"{m.step}/{m.name}: calendar target {m.routing!r} "
                    f"unresolved (have: {sorted(resolved_calendar_ids)})"
                )
                continue
            intents.append(_plan_calendar(m, e, cal_id, today))
        else:  # any unknown surface (e.g. a retired source) is refused
            problems.append(f"{m.step}/{m.name}: unknown surface {m.system!r}")

    if problems:
        # G29b blast-radius summary: atomicity means EVERY write on EVERY
        # surface in this diff is blocked, not just the unplannable rows —
        # the refusal message must say so, not just name the first offender.
        todoist_n = sum(1 for e in diff.entries if e.manifest.system == "todoist")
        calendar_n = sum(1 for e in diff.entries if e.manifest.system == "calendar")
        detail = "; ".join(problems)
        raise CommitPlanError(
            f"cannot plan {len(problems)} item(s) — refusing entire commit: "
            f"{todoist_n} todoist writes + {calendar_n} calendar writes blocked. "
            f"Unplannable: {detail}"
        )

    _assert_partition(intents)
    return intents


def _plan_todoist(m: ManifestEntry, e: ShadowDiffEntry, config: Any, today: date) -> WriteIntent:
    if m.capacities:
        # S6b defense: a Capacities row is plan-only. shadow.build_plan_manifest
        # emits no Todoist entry for one, so this is unreachable by construction;
        # if it ever fires, refuse the whole commit rather than create a task.
        raise CommitPlanError(
            f"{m.step}/{m.name}: Capacities row reached the Todoist writer — refusing commit"
        )
    if m.action == "capture-nicety":
        # U5 B2: the legacy automatic capture-nicety export lane is retired.
        # The dedicated Commit-only prompt export lane (prompt_export.py) is
        # the only path that may turn a prompt into a task, so a crafted or
        # externally reachable retired capture row fails the whole commit
        # closed rather than silently writing prompt text to Todoist.
        raise CommitPlanError(
            f"{m.step}/{m.name}: retired capture-nicety action — refusing commit"
        )
    if m.capacities_mirror and e.classification == NOOP:
        # The mirror lane may only CREATE. A same-name live task (any time,
        # any project) is a true no-op: it is never retimed, claimed, or
        # receipted. ``write_todoist`` drops this intent before any provider
        # interaction, so the preview and the commit agree.
        return WriteIntent(
            step=m.step, surface="todoist", op="noop", name=m.name,
            due_time=m.time, duration_min=m.duration_min,
            capacities_mirror=True,
            capacities_identity=m.capacities_identity,
        )
    all_day = m.action == "schedule-all-day"
    detail_recurring = e.detail.get("is_recurring")
    recurring = (
        bool(detail_recurring)
        if detail_recurring is not None
        else bool(m.is_recurring)
    )
    native_marker = bool(
        e.detail.get("pinned_native")
        or e.detail.get("native_protected")
        or m.native_protected
        or m.native_start is not None
        or any(
            key in e.detail
            for key in ("native_start", "native_time", "native_due_time", "live_start")
        )
    )
    native_provenance = native_marker
    # ManifestEntry.native_start is server-derived and outranks the live
    # diagnostic copy in detail['due_time']['live'].  The latter is only a
    # compatibility fallback for older diff shapes.
    native_start = (
        _native_start_value(m.native_start)
        if m.native_start is not None
        else (_native_start_from_detail(e.detail) if native_marker else None)
    )
    if native_marker and native_start is None:
        # A protected no-op with no mismatch detail has no separate live-time
        # field. Its server-derived manifest time is the native time in that
        # diff shape; retain it as an explicit verification target.
        native_start = _canonical_hhmm(m.time) or (
            str(m.time).strip() if m.time is not None else None
        )
    authorization = e.detail.get("retiming_authorized")
    if authorization is None:
        authorization = m.retiming_authorized
    if authorization is None:
        authorization = e.detail.get("allow_time_adjustment")
    if authorization is None:
        authorization = m.allow_time_adjustment
    retiming_authorized = authorization is True
    native_protected = bool(
        e.detail.get("pinned_native")
        or e.detail.get("native_protected")
        or m.native_protected
        or (native_provenance and not retiming_authorized)
    )
    legacy_pinned = bool(
        e.detail.get("pinned") or e.detail.get("pinned_recurring")
    )
    pinned = bool(
        native_protected or (legacy_pinned and not native_provenance)
    )
    if e.classification == UPDATE:
        dt = _hhmm_to_dt(today, m.time) if recurring else None
        return WriteIntent(
            step=m.step, surface="todoist", op="update", name=m.name,
            task_id=e.detail.get("task_id"),
            due_time=m.time, duration_min=m.duration_min,
            is_recurring=recurring,
            due_datetime=dt.strftime("%Y-%m-%dT%H:%M:%S") if dt else None,
            pinned=pinned,
            native_start=native_start,
            native_provenance=native_provenance,
            retiming_authorized=retiming_authorized,
            due_all_day_today=all_day,
        )
    if e.classification == NOOP:
        return WriteIntent(
            step=m.step, surface="todoist", op="noop", name=m.name,
            # T21: carry the live handle so disambiguated display names
            # ("X (Todoist)") resolve by id at write time, not by content.
            task_id=e.detail.get("task_id"),
            due_time=m.time, duration_min=m.duration_min,
            is_recurring=recurring,
            pinned=pinned,
            native_start=native_start,
            native_provenance=native_provenance,
            retiming_authorized=retiming_authorized,
            due_all_day_today=all_day,
        )
    # CREATE: route to PHEP project (project efforts) or Todoist Inbox (None).
    if m.capacities_mirror:
        # The Capacities mirror is always PHEP — resolved from the server
        # config, never from any client-supplied type/project.
        due_dt = _hhmm_to_dt(today, m.time)
        link = str(m.capacities_link or m.capacities_identity or m.id_or_path).strip()
        return WriteIntent(
            step=m.step, surface="todoist", op="create", name=m.name,
            project_id=_config_phep_project_id(config),
            due_time=m.time, duration_min=m.duration_min,
            due_datetime=due_dt.strftime("%Y-%m-%dT%H:%M:%S") if due_dt else None,
            capacities_mirror=True,
            capacities_identity=m.capacities_identity,
            description=f"Capacities: {link}" if link else None,
        )
    project_id = _config_phep_project_id(config) if m.routing == "PHEP" else None
    return WriteIntent(
        step=m.step, surface="todoist", op="create", name=m.name,
        project_id=project_id, due_time=m.time, duration_min=m.duration_min,
        # All-day work and nicety captures use bare "today": no time, no
        # duration, and never a BusyCal block.
        due_all_day_today=all_day,
    )


# G29b glyph normalization: config's § Calendar Event Classes and live
# manifest/routing data have been observed using visually-identical but
# code-point-distinct "white square" glyphs for the same calendar class
# (config: "◽ Blocks" U+25FD; live: "⬜ Blocks" U+2B1C) — an exact-string
# lookup missed the match entirely. Fold the small, explicit set of
# visually-equivalent white-square glyphs to one canonical marker before
# comparing. Deliberately NOT a Unicode-wide fold — U+2B1B (⬛ black square)
# carries different meaning and is never included.
_WHITE_SQUARE_GLYPHS = ("◽", "⬜", "□")  # ◽ ⬜ □


def _normalize_calendar_glyph(name: str) -> str:
    if name and name[0] in _WHITE_SQUARE_GLYPHS:
        return _WHITE_SQUARE_GLYPHS[0] + name[1:]
    return name


def _resolve_calendar_id(logical: str, resolved: dict[str, str]) -> str | None:
    """Resolve a routing name to a live calendar id — exact match first,
    then a glyph-normalized fallback match in either direction (G29b)."""
    cal_id = resolved.get(logical)
    if cal_id is not None:
        return cal_id
    target = _normalize_calendar_glyph(logical)
    for key, val in resolved.items():
        if _normalize_calendar_glyph(key) == target:
            return val
    return None


def _plan_calendar(
    m: ManifestEntry, e: ShadowDiffEntry, cal_id: str, today: date
) -> WriteIntent:
    op = "noop" if e.classification == NOOP else "create"
    start = _hhmm_to_dt(today, m.time)
    end = start + timedelta(minutes=m.duration_min) if start else None
    return WriteIntent(
        step=m.step, surface="calendar", op=op, name=m.name,
        due_time=m.time, duration_min=m.duration_min, start=start, end=end,
        calendar_id=cal_id,
    )


def _assert_partition(intents: list[WriteIntent]) -> None:
    """Enforce the UPDATE/CREATE partition invariant: op is one of the three,
    and an update carries a live handle while a create does not."""
    for i in intents:
        if i.op not in ("create", "update", "noop"):
            raise CommitPlanError(f"invalid op {i.op!r} for {i.name}")
        if i.surface == "todoist" and i.op == "update" and not i.task_id:
            raise CommitPlanError(f"todoist update {i.name} has no task_id")
        if i.surface == "todoist" and i.op == "create" and i.task_id:
            raise CommitPlanError(f"todoist create {i.name} carries a task_id")


# ---------------------------------------------------------------------------
# 2a. write_todoist — Step A
# ---------------------------------------------------------------------------

def _display12h(hhmm: str | None) -> str:
    """Authoritative 12-hour display mirror of the frontend display12h:
    "7 PM" for whole hours, "7:30 PM" otherwise, "—" when absent."""
    if not hhmm:
        return "—"
    try:
        h, m = (int(p) for p in hhmm.split(":"))
    except (ValueError, AttributeError):
        return hhmm
    suffix = "AM" if h < 12 else "PM"
    hour = h % 12 or 12
    return f"{hour} {suffix}" if m == 0 else f"{hour}:{m:02d} {suffix}"


def _todoist_due_reading(task: dict[str, Any]) -> DueReading:
    """Normalize a Todoist due for verification (FEEDBACK-23).

    Todoist fixed due datetimes are UTC-anchored (``due.date`` ends in ``Z``
    or carries an offset); converting them to the local wall clock REQUIRES
    the due's ``timezone`` — when it is absent or unknown the reading fails
    closed with an actionable error rather than guessing a zone. Floating
    local dues (no offset) carry their own wall time and are compared as-is.
    All-day dues (no time component) have no comparable time.
    """
    if not isinstance(task, dict):
        return DueReading(error="invalid task readback")
    due = task.get("due")
    if due is None:
        due = {}
    elif not isinstance(due, dict):
        return DueReading(raw=due, error="invalid due")
    raw = due.get("date") or due.get("datetime")
    tz = due.get("timezone") or None
    if not raw:
        return DueReading(raw=raw, timezone=tz)
    text = str(raw).strip()
    if "T" not in text:  # all-day due — no time component
        try:
            date.fromisoformat(text)
        except ValueError:
            return DueReading(raw=raw, timezone=tz, error="unparseable due date")
        return DueReading(raw=raw, timezone=tz)
    try:
        tz_text = text[:-1] + "+00:00" if text.endswith("Z") else text
        dt = datetime.fromisoformat(tz_text)
    except (TypeError, ValueError, OverflowError):
        return DueReading(raw=raw, timezone=tz, error="unparseable due datetime")
    if dt.tzinfo is None:
        # floating local due — the wall time IS the user's local time
        return DueReading(local_hhmm=dt.strftime("%H:%M"), raw=raw, timezone=tz)
    # fixed (UTC-anchored) due — the timezone is REQUIRED to convert safely
    if not tz:
        return DueReading(raw=raw, timezone=tz,
                          error="missing timezone for UTC due")
    try:
        zone = ZoneInfo(tz)
    except (KeyError, TypeError, ValueError):
        return DueReading(raw=raw, timezone=tz,
                          error=f"unknown timezone {tz!r}")
    try:
        local = dt.astimezone(zone)
    except (OverflowError, ValueError):
        return DueReading(raw=raw, timezone=tz,
                          error="unparseable due datetime")
    return DueReading(local_hhmm=local.strftime("%H:%M"), raw=raw, timezone=tz)


def _todoist_due_hhmm(task: dict[str, Any]) -> str | None:
    """Normalized local HH:MM for a Todoist due (FEEDBACK-23).

    UTC-anchored fixed dues are converted through the due's timezone;
    floating local dues pass through. None when all-day, absent, unparseable,
    or when the required timezone data is missing — the caller's structured
    reading carries the actionable reason for the fail-closed case."""
    return _todoist_due_reading(task).local_hhmm


def _is_date_only_landing(reading: DueReading) -> bool:
    """True only for the provider's date-only reschedule landing."""
    if not reading.raw or "T" in str(reading.raw):
        return False
    try:
        date.fromisoformat(str(reading.raw))
    except ValueError:
        return False
    return reading.error is None


def _has_native_provenance(intent: WriteIntent) -> bool:
    """Whether an intent came from server-native timing metadata."""
    return intent.native_provenance or intent.native_start is not None


def _is_protected_native_intent(intent: WriteIntent) -> bool:
    """Whether the write boundary must leave the native due untouched."""
    if intent.pinned:
        return True
    return (
        _has_native_provenance(intent)
        and intent.retiming_authorized is not True
    )


def _todoist_is_recurring(task: dict[str, Any]) -> bool:
    due = task.get("due") if isinstance(task, dict) else None
    return isinstance(due, dict) and bool(due.get("is_recurring"))


def _match_by_content(
    name: str, tasks: list[dict[str, Any]], claimed: set[str] | None = None
) -> dict[str, Any] | None:
    target = (name or "").strip().lower()
    for t in tasks:
        # T21: a task another intent of this run owns is never a content hit —
        # otherwise a same-named create collapses onto it (double-write).
        if claimed and str(t.get("id")) in claimed:
            continue
        if (t.get("content") or "").strip().lower() == target:
            return t
    return None


def _record_mirror_review(day: date, identity: str, reason: str) -> None:
    """Record a bounded, content-free ``needs_review`` receipt, never raising.

    A failure to update the durable state is itself fail-closed: the pending
    receipt (if any) blocks a blind retry, so the outcome is surfaced by the
    caller's WriterResult rather than swallowed."""
    try:
        capacities_mirror.fail_mirror(
            day=day.isoformat(), identity=identity, reason=reason)
    except capacities_mirror.MirrorStoreError:
        pass


def write_todoist(
    intents: list[WriteIntent], client: TodoistLike, today: date | None = None
) -> WriterResult:
    """Step A: retime existing / create new timed Todoist tasks.

    Idempotency: re-queries the live ``today`` filter and matches creates by
    content — a create whose task already exists collapses to a no-op (never a
    second task). The Capacities mirror lane is the exception: its live-task
    no-op is decided by the shadow diff, and a mirror NOOP intent is dropped
    here before any provider call (a same-name task is never retimed, claimed,
    or receipted). Reconciliation: every touched task is re-read and its due
    time asserted against intent; an intent with no surviving task is a silent
    drop → ``ok = False``.
    """
    todo = [
        i for i in intents
        if i.surface == "todoist"
        and not (i.capacities_mirror and i.op == "noop")
    ]
    result = WriterResult(step="A", surface="todoist")
    if not todo:
        return result
    day = today or date.today()

    # G30: match shadow.py's gather_live_state filter — "today" alone
    # excludes OVERDUE tasks (due yesterday or earlier, still open), so the
    # create-side idempotency snapshot would miss them entirely and a
    # still-open overdue task (real todoist id, real content) would
    # misclassify as would-create and get DUPLICATED on live commit.
    live = client.get_filter_tasks("today | overdue")
    touched = result.touched  # intent name -> task_id we expect to read back
    landed: dict[int, str] = {}

    def record_landing(intent: WriteIntent, task_id: Any) -> None:
        """Record one landed task for this intent and its public name map."""
        if task_id is None or str(task_id) == "":
            return
        tid = str(task_id)
        landed[id(intent)] = tid
        touched[intent.name] = tid

    # T21: tasks with a live handle in this run are claimed up front; content
    # matching (noop resolution, create idempotency) never touches them.
    claimed: set[str] = {str(i.task_id) for i in todo if i.task_id}

    for i in todo:
        if i.op == "noop":
            if i.task_id:
                result.noops.append(i.task_id)
                record_landing(i, i.task_id)
                continue
            match = _match_by_content(i.name, live, claimed)
            if match:
                mid = str(match.get("id", ""))
                claimed.add(mid)
                result.noops.append(mid)
                record_landing(i, mid)
            continue
        if i.op == "update":
            if _is_protected_native_intent(i):
                # Protected native timing is an intentional write no-op.  It
                # still enters the single reconciliation pass below; do not
                # read it here as well or result accounting would double-count.
                if i.task_id:
                    result.noops.append(i.task_id)
                    record_landing(i, i.task_id)
                continue
            if i.is_recurring:
                # T27: a recurring due is pattern-owned — due_string in any
                # form (incl. "today") wipes the recurrence. Time moves use
                # the datetime form; an all-day/no-time intent skips the due
                # write entirely and the task keeps its own live due.
                if i.due_datetime:
                    client.reschedule_task_datetime(i.task_id, i.due_datetime)
                    # reschedule can return success yet land date-only —
                    # verify the TIME landed and retry once with datetime
                    back = client.get_task(i.task_id)
                    first_reading = _todoist_due_reading(back)
                    if i.due_time and _is_date_only_landing(first_reading):
                        client.reschedule_task_datetime(i.task_id, i.due_datetime)
            elif i.due_all_day_today:
                client.reschedule_task(i.task_id, "today")
            else:
                client.reschedule_task(i.task_id, _due_string(i.due_time))
            result.updated.append(i.task_id)
            record_landing(i, i.task_id)
            continue
        # create — check-before-write for idempotency (claimed tasks excluded)
        existing = _match_by_content(i.name, live, claimed)
        if existing is not None:
            eid = str(existing.get("id", ""))
            claimed.add(eid)
            result.noops.append(eid)
            record_landing(i, eid)
            continue
        if i.capacities_mirror:
            # Capacities mirror lane: the write-ahead receipt, not the live
            # snapshot, is the idempotency authority. A receipt-store read
            # failure fails closed (no create); an unconfirmed prior attempt
            # is never blind-retried. A failure here is surfaced on this
            # surface only and never touches the calendar writer.
            identity = i.capacities_identity or i.name
            try:
                outcome = capacities_mirror.begin_mirror(
                    day=day.isoformat(), identity=identity)
            except capacities_mirror.MirrorStoreError:
                result.fail(
                    f"capacities mirror: receipt store unavailable for "
                    f"{i.name!r} — not created (fail closed)"
                )
                continue
            if outcome.action == capacities_mirror.SKIP_DONE:
                tid = outcome.receipt.task_id or ""
                if tid:
                    claimed.add(tid)
                    result.noops.append(tid)
                    record_landing(i, tid)
                continue
            if outcome.action == capacities_mirror.NEEDS_REVIEW:
                result.fail(
                    f"capacities mirror: {i.name!r} has an unconfirmed prior "
                    "attempt — needs review, not re-created"
                )
                continue
            try:
                created = client.create_task(
                    i.name, i.project_id,
                    due_datetime=i.due_datetime,
                    description=i.description,
                    duration=i.duration_min or None,
                    duration_unit="minute" if i.duration_min else None,
                )
            except Exception:  # noqa: BLE001 — bounded, content-free failure
                _record_mirror_review(day, identity, "provider create failed")
                result.fail(
                    f"capacities mirror: create failed for {i.name!r} "
                    "(provider error) — task not confirmed"
                )
                continue
            cid = str(created.get("id") or "") if isinstance(created, dict) else ""
            if not cid:
                _record_mirror_review(day, identity, "provider returned no task id")
                result.fail(
                    f"capacities mirror: create returned no task id for {i.name!r}"
                )
                continue
            try:
                capacities_mirror.complete_mirror(
                    day=day.isoformat(), identity=identity, task_id=cid)
            except capacities_mirror.MirrorStoreError:
                # The task landed but its id could not be persisted. The
                # pending receipt still blocks a duplicate; surface the gap.
                result.fail(
                    f"capacities mirror: created {i.name!r} but could not "
                    "persist its id — needs review"
                )
            claimed.add(cid)
            result.created.append(cid)
            record_landing(i, cid)
            continue
        created = client.create_task(
            i.name, i.project_id,
            due_string="today" if i.due_all_day_today else _due_string(i.due_time),
            duration=i.duration_min or None,
            duration_unit="minute" if i.duration_min else None,
        )
        cid = created.get("id", "")
        claimed.add(cid)
        result.created.append(cid)
        record_landing(i, cid)

    # -- reconciliation: read back, assert count + due time -------------------
    expected = len(todo)
    found = 0
    for i in todo:
        # Use the intent identity rather than only its display name.  This
        # keeps one no-op/readback accounting record per planned row even when
        # two rows happen to share a name.
        tid = landed.get(id(i))
        if not tid:
            result.fail(f"todoist: no task landed for {i.name!r} (silent drop)")
            continue
        try:
            back = client.get_task(tid)
        except Exception as exc:  # noqa: BLE001
            result.fail(f"todoist: readback failed for {i.name!r}: {exc}")
            continue
        found += 1
        protected = _is_protected_native_intent(i)
        if protected and not _has_native_provenance(i):
            # Legacy direct pinned intents did not carry the native time. Keep
            # their existence-only reconciliation only when no native
            # provenance is present. Planned manifest-native rows never take
            # this path.
            continue
        reading = _todoist_due_reading(back)
        got = reading.local_hhmm

        if reading.error:
            # FEEDBACK-23 fail closed: no timezone/parse data, cannot compare
            # safely — actionable structured reason, never a guessed zone.
            prefix = "native due" if protected else "due"
            result.fail_due(
                i.name,
                _canonical_hhmm(i.native_start) if protected else i.due_time,
                reading,
                reason=reading.error,
                message=(
                    f"todoist: {i.name!r} {prefix} cannot be verified "
                    f"({reading.error})"
                ),
            )
            continue

        if protected:
            expected_native = _canonical_hhmm(i.native_start)
            if expected_native is None:
                result.fail_due(
                    i.name, i.native_start, reading,
                    reason="unparseable native start",
                    message=(
                        f"todoist: {i.name!r} native due cannot be verified "
                        "(unparseable native start)"
                    ),
                )
                continue
            if got is None:
                result.fail_due(
                    i.name, expected_native, reading,
                    reason="missing due time",
                    message=(
                        f"todoist: {i.name!r} native due mismatch "
                        f"(intent {_display12h(expected_native)}, live {_display12h(got)})"
                    ),
                )
                continue
            if i.is_recurring and not _todoist_is_recurring(back):
                result.fail_due(
                    i.name, expected_native, reading,
                    reason="missing recurrence",
                    message=(
                        f"todoist: {i.name!r} recurrence marker missing "
                        "for protected native due"
                    ),
                )
                continue
            if got != expected_native:
                result.fail_due(
                    i.name, expected_native, reading,
                    reason="native mismatch",
                    message=(
                        f"todoist: {i.name!r} native due mismatch "
                        f"(intent {_display12h(expected_native)}, live {_display12h(got)})"
                    ),
                )
            continue

        if i.is_recurring and i.due_datetime:
            if not _todoist_is_recurring(back):
                result.fail_due(
                    i.name, i.due_time, reading,
                    reason="missing recurrence",
                    message=(
                        f"todoist: {i.name!r} recurrence marker missing "
                        "after datetime retime"
                    ),
                )
                continue
        if (i.due_all_day_today and got is not None and not i.pinned
                and not i.is_recurring):
            # T27: an all-day-shaped recurring row keeps its live pattern
            # time — no due write happened, so a timed readback is correct.
            result.fail_due(
                i.name, i.due_time, reading, reason="expected all-day",
                message=(
                    f"todoist: {i.name!r} due mismatch "
                    f"(intent all-day, live {_display12h(got)})"
                ),
            )
        if i.due_time and got is None:
            # A timed intent must not pass when the provider silently lands an
            # all-day/absent due. Keep the established mismatch wording while
            # preserving a machine-readable fail-closed reason.
            result.fail_due(
                i.name, i.due_time, reading,
                reason=(
                    "missing due time"
                    if i.is_recurring or _has_native_provenance(i)
                    else "mismatch"
                ),
                message=(
                    f"todoist: {i.name!r} due mismatch "
                    f"(intent {_display12h(i.due_time)}, live {_display12h(got)})"
                ),
            )
            continue
        if i.due_time and got != i.due_time:
            result.fail_due(
                i.name, i.due_time, reading, reason="mismatch",
                message=(
                    f"todoist: {i.name!r} due mismatch "
                    f"(intent {_display12h(i.due_time)}, live {_display12h(got)})"
                ),
            )
    result.reconciliation = {"count_expected": expected, "count_found": found}
    if found != expected and result.error is None:
        result.fail(f"todoist: count mismatch (expected {expected}, found {found})")
    return result


# ---------------------------------------------------------------------------
# 2b. write_calendar — Steps D / D′ / E
# ---------------------------------------------------------------------------

def _event_start_hhmm(event: dict[str, Any]) -> str | None:
    start = event.get("start")
    if isinstance(start, datetime):
        return start.strftime("%H:%M")
    if isinstance(start, str) and len(start) >= 5:
        return start[11:16] if "T" in start else start[:5]
    return None


def _match_event(
    name: str,
    hhmm: str | None,
    events: list[dict[str, Any]],
    calendar_id: str | None = None,
) -> dict[str, Any] | None:
    """Match a calendar event by title (+start time) for idempotency/noop
    resolution.

    ``calendar_id`` scopes the match to the TDTB-owned output calendar for
    this intent (FEEDBACK-26): an imported/read-only SOURCE event that happens
    to share the title+time must never be treated as our own write — otherwise
    the intended create collapses into a no-op and lands nowhere, and a wrong
    event reads back as success."""
    for ev in events:
        if (ev.get("title") or "") != name:
            continue
        if calendar_id is not None and (ev.get("calendar_id") or "") != calendar_id:
            continue
        if hhmm is None or _event_start_hhmm(ev) == hhmm:
            return ev
    return None


def write_calendar(
    intents: list[WriteIntent], store: EventStoreLike, today: date | None = None
) -> WriterResult:
    """Steps D/D′/E: create schedulable / Trinoor / anchored block events.

    Pre-write ID assertion (``assert_write_target``) runs immediately before
    every create. Idempotency: re-queries today's events and matches by
    title+start — an event that already exists collapses to a no-op.
    Reconciliation: each created event is re-read and its **calendar_id**
    asserted against intent (wrong-surface guard, 2026-06-08 class) plus
    title/start; count is checked against intent.
    """
    cal = [i for i in intents if i.surface == "calendar"]
    result = WriterResult(step="D/E", surface="calendar")
    if not cal:
        return result

    today = today or date.today()
    window_start = datetime(today.year, today.month, today.day)
    window_end = window_start + timedelta(days=1)
    live_events = store.query_events(window_start, window_end)
    live_cals = store.calendars()
    touched: dict[str, str] = {}

    for i in cal:
        if i.op == "noop":
            match = _match_event(i.name, i.due_time, live_events, i.calendar_id)
            if match:
                result.noops.append(match.get("id", ""))
                touched[i.name] = match.get("id", "")
            continue
        # create — idempotency check first (scoped to the TDTB-owned output
        # calendar so a same-title source event never collapses our write)
        existing = _match_event(i.name, i.due_time, live_events, i.calendar_id)
        if existing is not None:
            result.noops.append(existing.get("id", ""))
            touched[i.name] = existing.get("id", "")
            continue
        try:
            calendar_bridge.assert_write_target(i.calendar_id, live_cals)
            spec = calendar_bridge.EventSpec(
                title=i.name, start=i.start, end=i.end, calendar_id=i.calendar_id,
            )
            eid = store.create_event(spec)
        except calendar_bridge.CalendarWriteError as exc:
            result.fail(f"calendar: write blocked for {i.name!r}: {exc}")
            continue
        result.created.append(eid)
        touched[i.name] = eid

    # -- reconciliation: read back, assert FULL interval identity -------------
    # FEEDBACK-26: title/calendar_id-only reconciliation passed a provider
    # drift (intended 13:00-14:00 read back as 13:30-14:30). Every write now
    # verifies title + calendar_id + start + end + duration via the shared
    # pure helper, with structured machine-canonical mismatch records.
    expected = len(cal)
    found = 0
    for i in cal:
        eid = touched.get(i.name)
        if not eid:
            result.fail(f"calendar: no event landed for {i.name!r} (silent drop)")
            continue
        back = store.get_event(eid)
        if back is None:
            result.fail(f"calendar: readback missing for {i.name!r}")
            continue
        found += 1
        spec = calendar_bridge.EventSpec(
            title=i.name, start=i.start, end=i.end, calendar_id=i.calendar_id,
        )
        mismatches = calendar_bridge.event_readback_mismatches(spec, back)
        if not mismatches:
            continue
        fields = {m["field"] for m in mismatches}
        if "calendar_id" in fields:
            message = (
                f"calendar: {i.name!r} landed on wrong calendar "
                f"(intent {i.calendar_id}, live {back.get('calendar_id')})"
            )
        else:
            live_end_raw = back.get("end")
            live_end_hhmm = (
                live_end_raw.strftime("%H:%M")
                if isinstance(live_end_raw, datetime)
                else (live_end_raw[11:16] if isinstance(live_end_raw, str) and "T" in live_end_raw
                      else (live_end_raw[:5] if isinstance(live_end_raw, str) else None))
            )
            message = (
                f"calendar: {i.name!r} readback interval mismatch "
                f"(intent {_display12h(i.due_time)}–"
                f"{_display12h(i.end.strftime('%H:%M') if i.end else None)}, "
                f"live {_display12h(_event_start_hhmm(back))}–"
                f"{_display12h(live_end_hhmm)})"
            )
        result.fail_calendar(i.name, mismatches, message)
    result.reconciliation = {"count_expected": expected, "count_found": found}
    # T19 parity with write_todoist: the landed-id map crosses on the result
    # so post-commit consumers can recover a row's id without re-querying.
    result.touched.update(touched)
    if found != expected and result.error is None:
        result.fail(f"calendar: count mismatch (expected {expected}, found {found})")
    return result


# ---------------------------------------------------------------------------
# 3. run_commit — thin sequential driver (T15 replaces with a ledgered,
#    resumable, partial-failure-honest orchestrator).
# ---------------------------------------------------------------------------

def run_commit(
    intents: list[WriteIntent],
    *,
    todoist: TodoistLike | None = None,
    store: EventStoreLike | None = None,
    today: date | None = None,
) -> list[WriterResult]:
    """Run every writer over ``intents`` in a fixed order and collect results.

    Deliberately minimal — no ledger, no resume, no cross-surface stop logic.
    That failure-safe orchestration is T15; this driver exists only for the
    T14 manual-verify gate (run the writers end-to-end against real Todoist +
    Terminal-granted calendar). A writer whose surface has no client/root is
    skipped."""
    results: list[WriterResult] = []
    if todoist is not None:
        results.append(write_todoist(intents, todoist, today))
    if store is not None:
        results.append(write_calendar(intents, store, today))
    return results
