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
clients (``TodoistClient``, ``EventStore``, ``vault_root``) so tests never touch
the network or EventKit. Orchestration — ordered dispatch, a persisted success
ledger, partial-failure honesty, and resume — is **T15's** job (``run_commit``
here is only a thin sequential driver for the manual-verify gate).

``recent-selections`` is NOT a writer here — it is a post-commit action
(``runstate.append_recent_selection``) outside this write contract, invoked by
the orchestrator after a clean run.
"""
from __future__ import annotations

import json
import stat
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol
from zoneinfo import ZoneInfo

_GATHER_DIR = str(Path(__file__).parent / "gather")
if _GATHER_DIR not in sys.path:
    sys.path.insert(0, _GATHER_DIR)

import tdtb_gather as gather  # noqa: E402  (path-shimmed import, see inventory.py)

import calendar_bridge  # noqa: E402
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
    surface: str          # todoist | calendar | vault
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
    # vault
    path: str | None = None
    # date-only Todoist work / captures
    due_all_day_today: bool = False   # due_string "today", no time
    payload: dict[str, Any] | None = None  # B6: the captures key/value map

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
            "path": self.path,
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
    # non-due failure (readback/silent-drop/calendar/vault).
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
    config-less path still routes PHEP correctly (spec § 3.2: all vault efforts
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

    Partition invariant: every todoist/calendar/vault intent gets
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
        elif m.system == "vault":
            intents.append(_plan_vault(m, e, config))
        else:  # pragma: no cover — manifest builder only emits the three above
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
    all_day = m.action in ("capture-nicety", "schedule-all-day")
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
    # CREATE: route to PHEP project (vault efforts) or Todoist Inbox (None).
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


def _plan_vault(m: ManifestEntry, e: ShadowDiffEntry, config: Any = None) -> WriteIntent:
    op = "noop" if e.classification == NOOP else "update" if e.classification == UPDATE else "create"
    payload = None
    if m.action == "frontmatter-captures" and isinstance(config, dict):
        payload = dict(config.get("captures") or {})
    return WriteIntent(
        step=m.step, surface="vault", op=op, name=m.name, path=m.id_or_path,
        payload=payload,
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


def write_todoist(intents: list[WriteIntent], client: TodoistLike) -> WriterResult:
    """Step A: retime existing / create new timed Todoist tasks.

    Idempotency: re-queries the live ``today`` filter and matches creates by
    content — a create whose task already exists collapses to a no-op (never a
    second task). Reconciliation: every touched task is re-read and its due
    time asserted against intent; an intent with no surviving task is a silent
    drop → ``ok = False``.
    """
    todo = [i for i in intents if i.surface == "todoist"]
    result = WriterResult(step="A", surface="todoist")
    if not todo:
        return result

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
# 2c. write_frontmatter_flips — Step C
# ---------------------------------------------------------------------------

def _set_assigned_true(text: str) -> tuple[str, str]:
    """Return ``(new_text, outcome)`` with ``assigned: true`` set in the
    frontmatter, preserving key order and every other byte.

    ``outcome`` ∈ ``{"noop", "updated", "created"}``. Operates on raw text — it
    edits or inserts a single line inside the ``---`` fence and never
    re-serializes YAML, so unrelated keys, comments, quoting, and the body are
    byte-identical afterward. Returns ``(text, "conflict")`` if there is no
    frontmatter fence to edit."""
    if not text.startswith("---"):
        return text, "conflict"
    end = text.find("\n---", 3)
    if end == -1:
        return text, "conflict"

    fence = text[3:end]            # between opening --- and closing ---
    rest = text[end:]              # closing \n--- ... body

    lines = fence.split("\n")
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("assigned:"):
            value = stripped.split(":", 1)[1].strip().lower()
            if value == "true":
                return text, "noop"
            # preserve leading indentation of the original key line
            indent = line[: len(line) - len(line.lstrip())]
            lines[idx] = f"{indent}assigned: true"
            return "---" + "\n".join(lines) + rest, "updated"

    # key absent — insert as the last frontmatter line (append, order-preserving)
    if lines and lines[-1] == "":
        lines.insert(len(lines) - 1, "assigned: true")
    else:
        lines.append("assigned: true")
    return "---" + "\n".join(lines) + rest, "created"


def write_frontmatter_flips(
    intents: list[WriteIntent], vault_root: Path | str
) -> WriterResult:
    """Step C: flip ``assigned: true`` on each selected vault note.

    Byte-surgical (see ``_set_assigned_true``). Idempotent: a note already
    ``assigned: true`` is left untouched (no-op, bytes unchanged).
    Reconciliation: re-parse each note and assert ``assigned is True``; a
    missing target or a flip that didn't take is ``ok = False``, never a crash.
    """
    vault_root = Path(vault_root)
    flips = [i for i in intents if i.surface == "vault" and i.step == "C"]
    result = WriterResult(step="C", surface="vault")
    if not flips:
        return result

    for i in flips:
        p = vault_root / i.path
        if not p.is_file():
            result.fail(f"vault: target missing: {i.path}")
            continue
        text = p.read_text(encoding="utf-8")
        new_text, outcome = _set_assigned_true(text)
        if outcome == "conflict":
            result.fail(f"vault: no frontmatter fence in {i.path}")
            continue
        if outcome == "noop":
            result.noops.append(i.path)
        else:
            p.write_text(new_text, encoding="utf-8")
            (result.updated if outcome == "updated" else result.created).append(i.path)

    # -- reconciliation: re-parse, assert assigned is True --------------------
    reconciled = 0
    for i in flips:
        p = vault_root / i.path
        if not p.is_file():
            continue
        fm = gather.parse_frontmatter(p.read_text(encoding="utf-8")) or {}
        if fm.get("assigned") is True:
            reconciled += 1
        else:
            result.fail(f"vault: {i.path} not assigned:true after write")
    result.reconciliation = {"count_expected": len(flips), "count_reconciled": reconciled}
    if reconciled != len(flips) and result.error is None:
        result.fail(f"vault: flip count mismatch (expected {len(flips)}, reconciled {reconciled})")
    return result


# ---------------------------------------------------------------------------
# 2d. write_daily_note — Step B
# ---------------------------------------------------------------------------

_PLAN_HEADER = "# TDTB Plan"


def _patch_plan_section(text: str, plan_body: str) -> str:
    """Replace the ``# TDTB Plan`` section if present, else append it.

    A present section runs from its ``# TDTB Plan`` header up to the next
    top-level ``# `` header (or EOF). Idempotent in the sense that re-patching
    replaces rather than duplicates — never two plan sections."""
    section = f"{_PLAN_HEADER}\n{plan_body}".rstrip() + "\n"
    idx = text.find(_PLAN_HEADER)
    if idx == -1:
        sep = "" if text.endswith("\n") or text == "" else "\n"
        joiner = "\n" if text else ""
        return f"{text}{sep}{joiner}{section}"
    # find the next top-level header after the plan header
    after = text.find("\n# ", idx + len(_PLAN_HEADER))
    if after == -1:
        return text[:idx] + section
    return text[:idx] + section + text[after + 1:]


def _resolve_daily_note(vault_root: Path, today: date) -> Path | None:
    daily_dir = vault_root / "30 - Daily"
    try:
        directory_mode = daily_dir.stat().st_mode
    except FileNotFoundError:
        return None
    if not stat.S_ISDIR(directory_mode):
        return None
    for candidate in (f"{today.isoformat()}.md", today.strftime("%b %d, %Y") + ".md"):
        p = daily_dir / candidate
        try:
            candidate_mode = p.stat().st_mode
        except FileNotFoundError:
            continue
        if stat.S_ISREG(candidate_mode):
            return p
    return None


def write_daily_note(
    intents: list[WriteIntent],
    vault_root: Path | str,
    plan_body: str,
    today: date | None = None,
) -> WriterResult:
    """Step B: patch the ``# TDTB Plan`` section into today's daily note.

    ``plan_body`` is the rendered plan text (the caller renders it from the
    manifest — rendering is not a writer concern). Idempotent: re-patching
    replaces the section, never duplicating it. Reconciliation: re-read the note
    and assert the section is present with the intended body.
    """
    vault_root = Path(vault_root)
    today = today or date.today()
    patch = [i for i in intents if i.surface == "vault" and i.step == "B"]
    result = WriterResult(step="B", surface="vault")
    if not patch:
        return result

    try:
        note = _resolve_daily_note(vault_root, today)
    except OSError as exc:
        result.fail(f"vault: today's daily note lookup failed: {exc}")
        return result
    if note is None:
        result.reconciliation = {
            "daily_note_present": False,
            "skipped": True,
            "reason": "optional daily note absent",
            "date": today.isoformat(),
        }
        return result

    try:
        text = note.read_text(encoding="utf-8")
    except OSError as exc:
        result.fail(f"vault: today's daily note read failed: {exc}")
        return result
    new_text = _patch_plan_section(text, plan_body)
    already = new_text == text
    if not already:
        try:
            note.write_text(new_text, encoding="utf-8")
        except OSError as exc:
            result.fail(f"vault: today's daily note write failed: {exc}")
            return result
    (result.noops if already else result.updated).append(str(note.name))

    # -- reconciliation: section present with intended body -------------------
    try:
        back = note.read_text(encoding="utf-8")
    except OSError as exc:
        result.fail(f"vault: today's daily note readback failed: {exc}")
        return result
    expected_section = f"{_PLAN_HEADER}\n{plan_body}".rstrip()
    if expected_section in back and back.count(_PLAN_HEADER) == 1:
        result.reconciliation = {"section_present": True, "count": 1}
    else:
        result.fail("vault: # TDTB Plan section not reconciled after patch")
        result.reconciliation = {"section_present": _PLAN_HEADER in back,
                                 "count": back.count(_PLAN_HEADER)}
    return result


# ---------------------------------------------------------------------------
# 2e. write_captures_frontmatter — B6
# ---------------------------------------------------------------------------

_CAPTURE_KEYS = ("intention", "megan_nicety", "stoic_intention")


def _merge_frontmatter_keys(text: str, additions: dict[str, str]) -> tuple[str, list[str]]:
    """Add missing keys into the note's YAML frontmatter block. Never
    overwrites an existing key (skill B6: don't overwrite; merge in one
    call). Returns (new_text, keys_added). A note with no frontmatter gains
    a fresh block at the top."""
    lines = text.split("\n")
    if lines and lines[0].strip() == "---":
        try:
            end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
        except StopIteration:
            end = None
    else:
        end = None

    if end is None:
        existing_keys: set[str] = set()
        body = text
        fm_lines: list[str] = []
    else:
        fm_lines = lines[1:end]
        existing_keys = {ln.split(":", 1)[0].strip() for ln in fm_lines if ":" in ln}
        body = "\n".join(lines[end + 1:])

    added = [k for k in additions if k not in existing_keys and str(additions[k]).strip()]
    if not added:
        return text, []
    new_fm = fm_lines + [f"{k}: {json.dumps(str(additions[k]))}" for k in added]
    return "---\n" + "\n".join(new_fm) + "\n---\n" + body, added


def write_captures_frontmatter(
    intents: list[WriteIntent],
    vault_root: Path | str,
    today: date | None = None,
) -> WriterResult:
    """B6: merge Phase-1 captures (intention / megan_nicety / stoic_intention)
    into today's daily-note frontmatter. Missing keys only — an existing value
    is NEVER overwritten; all keys land in one write. Reconciliation: re-read
    and assert every intended key is present."""
    vault_root = Path(vault_root)
    today = today or date.today()
    rows = [i for i in intents if i.surface == "vault" and i.step == "B6"]
    result = WriterResult(step="B6", surface="vault")
    if not rows:
        return result

    try:
        note = _resolve_daily_note(vault_root, today)
    except OSError as exc:
        result.fail(f"vault: today's daily note lookup failed: {exc}")
        return result
    if note is None:
        result.reconciliation = {
            "daily_note_present": False,
            "skipped": True,
            "reason": "optional daily note absent; B6 captures skipped",
            "date": today.isoformat(),
        }
        return result

    captures = {k: v for i in rows for k, v in (i.payload or {}).items()
                 if k in _CAPTURE_KEYS and str(v).strip()}
    try:
        text = note.read_text(encoding="utf-8")
    except OSError as exc:
        result.fail(f"vault: today's daily note read failed: {exc}")
        return result
    new_text, added = _merge_frontmatter_keys(text, captures)
    if added:
        try:
            note.write_text(new_text, encoding="utf-8")
        except OSError as exc:
            result.fail(f"vault: today's daily note write failed: {exc}")
            return result
        result.updated.append(str(note.name))
    else:
        result.noops.append(str(note.name))

    # -- reconciliation: every intended key present in frontmatter ------------
    try:
        back = note.read_text(encoding="utf-8")
    except OSError as exc:
        result.fail(f"vault: today's daily note readback failed: {exc}")
        return result
    back_fm = back.split("\n---\n")[0] if back.startswith("---") else ""
    missing = [k for k in captures if f"{k}:" not in back_fm]
    result.reconciliation = {"keys_added": added, "keys_missing": missing}
    if missing:
        result.fail(f"vault: B6 keys not reconciled: {missing}")
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
    vault_root: Path | str | None = None,
    plan_body: str = "",
    today: date | None = None,
) -> list[WriterResult]:
    """Run every writer over ``intents`` in a fixed order and collect results.

    Deliberately minimal — no ledger, no resume, no cross-surface stop logic.
    That failure-safe orchestration is T15; this driver exists only for the
    T14 manual-verify gate (run the writers end-to-end against a real vault +
    Terminal-granted calendar). A writer whose surface has no client/root is
    skipped."""
    results: list[WriterResult] = []
    if todoist is not None:
        results.append(write_todoist(intents, todoist))
    if vault_root is not None:
        results.append(write_frontmatter_flips(intents, vault_root))
        results.append(write_daily_note(intents, vault_root, plan_body, today))
        results.append(write_captures_frontmatter(intents, vault_root, today))
    if store is not None:
        results.append(write_calendar(intents, store, today))
    return results
