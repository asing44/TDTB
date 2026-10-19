"""runtime_actions.py — T20 reversible runtime item actions.

Single-item verbs over today's plan: complete, done, skip_today,
remove_from_today, duration_edit, move_resize, delete_permanent, delete,
defer, assign, drop_from_plan, restore_today, unassign, hide_always. Every
action runs through a dated, ID-addressed journal with exact before-images,
per-step applied
markers, reverse compensation on mid-apply failure, and one-step undo.

IMP-05 final source-action semantics (frozen reliability plan, action table):

- ``done`` — the final name of ``complete``: Todoist closes only the current
  occurrence (recurring advances normally). The already-created TDTB block
  stays historical.
- ``drop_from_plan`` — a date-scoped runstate-only exclusion. It touches no
  Todoist/calendar, removes the row from current planning only, is
  idempotent per date, and is undoable (eligible again).
- ``restore_today`` — the identity-addressed reverse of a Drop: it removes
  one identity from today's skipped list (``runstate.undrop``), is journaled
  like every other verb, and is undoable (the saved entry is re-added).
- ``unassign`` — a non-recurring Todoist task has its date cleared; a
  recurring task's due occurrence advances WITHOUT completing it, preserving the recurrence string/type and proving unchanged
  completion counters by read-back. Never closes, never touches deferrals.
- ``delete`` — the final name of ``delete_permanent``: every linked source
  target, atomically where feasible, plus only TDTB-owned calendar blocks
  (imported events are never in the manifest and are untouchable).

``defer`` (allocator-rewrite T1) is the one verb with no post-commit
precondition: it records a deferral in the rolling ``deferrals`` store and
best-effort un-schedules whatever derived records exist, so it is legal from
the staging queue as well as from the committed plan.

Todoist, Calendar, Capacities, state-dir and settings-store (exclusion)
steps exist. A target that
resolves to no such source (e.g. a stale manifest row from a retired source)
takes the ordinary "no ... source" refusal in ``plan_steps``; a journal entry
from before the retirement that names a step kind this module no longer knows
fails its undo with an "unknown step kind" error (it never crashes the load).

Contracts (locked decision 41 + T20 scope amendment 2026-07-25):

- Targets resolve from today's runstate ``plan_manifest`` — the app only ever
  touches artifacts it committed (Todoist rows it scheduled, owned Blocks
  calendar events). Imported calendar entries never
  appear in the manifest and are therefore untouchable here.
- ``complete`` updates the authoritative source (Todoist close / Capacities
  completion) — derived artifacts stay.
- ``skip_today`` / ``remove_from_today`` preserve assignment and source
  existence: they delete only today's owned Blocks event and clear the
  generated time-of-day (due date stays today).
- ``duration_edit`` / ``move_resize`` update only the intended derived
  records (owned event interval; Todoist duration field).
- ``delete_permanent`` deletes the source (Todoist delete) and
  today's owned event — journaled with full before-images so undo can
  recreate (recreated Todoist tasks/events get NEW ids, recorded in-step).
- Surfaces are checked before ANY write: a verb whose plan needs an
  unavailable client fails closed with no journal entry and no side effect.

The journal lives in the state dir
(``<app home>/state/runtime-journal/<date>.json``), is written atomically
(tmp + os.replace) and mutated only under ``runstate.day_lock`` — same
single-writer discipline as every other dated write.
"""

from __future__ import annotations

import calendar
import hashlib
import json
import os
import re
import secrets
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import app_config
import calendar_bridge
import capacities_settings
import deferrals
import exclusion_settings
import runstate

VERBS = (
    "complete",
    "done",
    "skip_today",
    "remove_from_today",
    "duration_edit",
    "move_resize",
    "delete_permanent",
    "delete",
    "defer",
    "assign",
    "drop_from_plan",
    "restore_today",
    "unassign",
    "hide_always",
)

# T2: the verbs legal against a staging-phase (pre-commit) target. The rest
# act on derived records — an owned Blocks event, a generated time-of-day —
# that only exist after /commit, so they refuse rather than silently editing a
# source the app hasn't placed yet.
#
# IMP-05 (frozen action semantics): the four final intents are available
# before AND after commit. ``done``/``delete`` are the final names for
# complete/delete_permanent; ``drop_from_plan`` is runstate-only and
# ``unassign`` acts on the sources, none of which require a placed derived
# record.
STAGING_VERBS = (
    "complete", "done",
    "delete_permanent", "delete",
    "defer", "assign",
    "drop_from_plan", "restore_today", "unassign", "hide_always",
)

# `external_sources` gives every Todoist row this synthetic path. It is an
# identity, not a location — nothing may join it to a filesystem root.
TODOIST_PATH_PREFIX = "todoist://"
CAPACITIES_PATH_PREFIX = "capacities://"
# Unit 2: a schedulable all-day calendar row's ``calendar://`` URI is a source
# identity marker. The original all-day event is NEVER touched; only runstate
# exclusions (drop/unassign) act on it.
CALENDAR_PATH_PREFIX = "calendar://"

class RuntimeActionError(Exception):
    """Refused/failed runtime action — message is the user-facing detail."""


# ---------------------------------------------------------------------------
# Journal
# ---------------------------------------------------------------------------

STATE_JOURNAL_DIRNAME = "runtime-journal"


def journal_path(valid_date: date) -> Path:
    """``<app home>/state/runtime-journal/<date>.json`` (computed only)."""
    return app_config.state_dir() / STATE_JOURNAL_DIRNAME / f"{valid_date}.json"


def load_journal(valid_date: date) -> dict[str, Any]:
    """Read the dated journal from the state dir."""
    path = journal_path(valid_date)
    if not path.exists():
        return {"valid_date": str(valid_date), "actions": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"valid_date": str(valid_date), "actions": []}
    if not isinstance(data, dict) or not isinstance(data.get("actions"), list):
        return {"valid_date": str(valid_date), "actions": []}
    return data


def _write_journal(valid_date: date, journal: dict) -> None:
    path = journal_path(valid_date)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(journal, indent=2, ensure_ascii=False, default=str),
                   encoding="utf-8")
    os.replace(tmp, path)


def find_action(journal: dict, action_id: str) -> dict | None:
    for action in journal.get("actions", []):
        if action.get("id") == action_id:
            return action
    return None


def idempotency_key(valid_date: date, verb: str, target: str, args: dict) -> str:
    payload = f"{valid_date}|{verb}|{target}|{json.dumps(args, sort_keys=True)}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _new_action_id(valid_date: date, journal: dict) -> str:
    return f"ra-{valid_date}-{len(journal['actions']) + 1}-{secrets.token_hex(3)}"


def _now_iso(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Target resolution (from today's committed plan_manifest)
# ---------------------------------------------------------------------------

def _row_stable_identity(row: dict[str, Any]) -> str:
    """The stable identity of ONE server-derived manifest or index row.

    Row verbs from the UI address their row by this identity rather than by
    its (rename-prone) plan name, so resolution has to recognise the string it
    sent. The identity is derived HERE, from the server's own row: a client
    that names an id the app never derived still resolves to nothing.

    Todoist rows key on ``todoist:<task id>``; a row that already carries a
    canonical ``identity`` (Capacities, calendar) uses it verbatim. The name is
    never used — the caller's name lookup covers that axis already.
    """
    tid = str(row.get("todoist_id") or "").strip()
    if tid:
        return f"todoist:{tid}"
    system = str(row.get("system") or row.get("source") or "").strip().casefold()
    identity = str(row.get("identity") or "").strip()
    if identity:
        return identity
    path = str(row.get("path") or row.get("id_or_path") or "").strip()
    if not path:
        return ""
    if system == "todoist":
        return f"todoist:{path}"
    if system in ("capacities", "calendar"):
        return path if path.startswith(f"{system}:") else f"{system}:{path}"
    return path


def _matches_row(row: dict[str, Any], target: str) -> bool:
    """A derived row answers to its plan NAME or to its stable identity."""
    if row.get("name") == target:
        return True
    identity = _row_stable_identity(row)
    return bool(identity) and identity == target


def _resolve_from_digest_index(
    valid_date: date, name: str,
) -> dict[str, Any] | None:
    """T2 staging-phase fallback: resolve from the digest identity index the
    server itself wrote at /plan-inputs. Returns None when the name isn't in
    today's digest — an unknown item is still refused, exactly as post-commit.

    The staging index has no ``system`` column, so ``external_sources``'
    synthetic ``todoist://<id>`` pseudo-path has to be recognised by shape —
    the same guard `shadow.py` applies at its own two sites. A row whose path
    is neither Todoist nor Capacities (a retired note path) resolves to no
    source, so every source verb refuses it with its ordinary "no ... source"
    error.
    """
    rows = [r for r in runstate.read_digest_index(valid_date)
            if isinstance(r, dict) and _matches_row(r, name)]
    if not rows:
        return None
    out: dict[str, Any] = {
        "name": str(rows[0].get("name") or name), "phase": "staging"}
    for row in rows:
        path = str(row.get("path") or "")
        source = str(row.get("source") or "").strip().casefold()
        if source == "calendar" or path.startswith(CALENDAR_PATH_PREFIX):
            # Unit 2: a calendar row resolves to its canonical source identity
            # only. Never the source event id — no code path may touch the
            # original all-day event; the index deliberately drops event_id.
            if "identity" not in out:
                for key in ("identity", "path"):
                    if row.get(key) is not None:
                        out[key] = row[key]
                out["source"] = "calendar"
            continue
        if source == "capacities" or path.startswith(CAPACITIES_PATH_PREFIX):
            # A Capacities URI is a source identity, not a file path. Copy
            # only server-derived provider metadata into the guarded action.
            if path.startswith(CAPACITIES_PATH_PREFIX) and "capacities_id" not in out:
                for key in (
                    "identity", "capacities_id", "capacities_space_id",
                    "capacities_structure_id", "capacities_completion_supported",
                    "source_fingerprint",
                ):
                    if row.get(key) is not None:
                        out[key] = row[key]
                out["source"] = "capacities"
            continue
        if row.get("todoist_id") and "todoist_id" not in out:
            out["todoist_id"] = str(row["todoist_id"])
            for key in ("duration_minutes", "is_recurring"):
                if row.get(key) is not None and key not in out:
                    out[key] = row[key]
        elif path.startswith(TODOIST_PATH_PREFIX) and "todoist_id" not in out:
            # A pseudo-path with no explicit id still identifies the task.
            out["todoist_id"] = path[len(TODOIST_PATH_PREFIX):]
    return out


def resolve_target(valid_date: date, name: str) -> dict[str, Any]:
    """Resolve a plan-item name to its source artifacts.

    Post-commit (``plan_manifest`` present) resolution is unchanged and still
    authoritative — the app only touches artifacts it committed. When the name
    has no manifest row, T2 falls back to today's ``digest_index``, which is
    what makes complete/delete/defer usable from the staging queue before any
    commit exists. The resolved dict carries ``phase`` so the verb planner can
    refuse verbs that only make sense against placed artifacts.

    ``name`` is either the row's plan name OR its stable identity
    (``todoist:<task id>`` / a Capacities or calendar identity): the UI's row
    verbs address a row by identity so a rename cannot retarget them. Both
    forms resolve against the SAME server-derived rows, so the client still
    never addresses an artifact the app did not derive itself.
    """
    state = runstate.read_runstate(valid_date) or {}
    manifest = [r for r in state.get("plan_manifest") or []
                if isinstance(r, dict)]
    rows = [r for r in manifest if _matches_row(r, name)]
    if not rows:
        staged = _resolve_from_digest_index(valid_date, name)
        if staged is not None:
            return staged
        raise RuntimeActionError(
            f"{name!r}: not in today's plan manifest or digest")
    out: dict[str, Any] = {
        "name": str(rows[0].get("name") or name), "phase": "committed"}
    for row in rows:
        if "minutes" not in out:
            try:
                _m = int(row.get("duration_min"))
            except (TypeError, ValueError):
                _m = 0
            if _m > 0:
                out["minutes"] = _m
        system = row.get("system")
        if system == "todoist" and "todoist_id" not in out:
            out["todoist_id"] = str(row.get("id_or_path"))
        elif system == "calendar" and "event_id" not in out:
            out["event_id"] = str(row.get("id_or_path"))
            out["event_time"] = row.get("time")
            out["event_duration_min"] = int(row.get("duration_min") or 0)
        elif system == "capacities" and "capacities_id" not in out:
            out["source"] = "capacities"
            out["capacities_id"] = str(row.get("id_or_path"))
            for key in (
                "identity", "capacities_space_id", "capacities_structure_id",
                "capacities_completion_supported", "source_fingerprint",
            ):
                if row.get(key) is not None:
                    out[key] = row[key]
    return out


def drop_identity_of(target_or_row: dict[str, Any]) -> str:
    """Canonical source identity for a Drop exclusion.

    Todoist rows are ``todoist:<task id>``; Capacities rows carry their own
    identity. The ``todoist://`` pseudo-path is an identity marker, not a
    location. Falls back to the name/id for rows with no usable source
    identity (the manifest is name-keyed).
    """
    tid = target_or_row.get("todoist_id")
    if tid not in (None, ""):
        return f"todoist:{tid}"
    source = str(target_or_row.get("source") or "").strip().casefold()
    identity = str(target_or_row.get("identity") or "").strip()
    path = str(target_or_row.get("path") or "")
    if source == "capacities" or path.startswith(CAPACITIES_PATH_PREFIX):
        return identity or path
    if source == "calendar" or path.startswith(CALENDAR_PATH_PREFIX):
        return identity or path
    return str(target_or_row.get("name") or target_or_row.get("id") or "")


def resolve_dropped_target(valid_date: date, identity: str) -> dict[str, Any]:
    """Resolve a Restore-today target from today's dropped list by identity.

    Dropped rows are deliberately absent from the digest index (it is built
    from the FILTERED digest), so Restore today resolves from
    ``runstate.read_dropped`` instead. An unknown identity is a clean refusal:
    nothing is written and no journal entry is created.
    """
    key = str(identity or "").strip()
    if not key:
        raise RuntimeActionError("restore_today requires a dropped identity")
    for entry in runstate.read_dropped(valid_date):
        if str(entry.get("identity") or "") == key:
            return {
                "phase": "dropped",
                "identity": key,
                "name": entry.get("name") or "",
                "entry": entry,
            }
    raise RuntimeActionError(f"{key!r}: not in today's skipped list")


# ---------------------------------------------------------------------------
# Step planning
# ---------------------------------------------------------------------------

def _hhmm_to_dt(valid_date: date, hhmm: str) -> datetime:
    h, m = hhmm.split(":")
    return datetime(valid_date.year, valid_date.month, valid_date.day, int(h), int(m))


def _iso(v: Any) -> Any:
    return v.isoformat() if isinstance(v, datetime) else v


def _event_snapshot(store: Any, event_id: str) -> dict | None:
    ev = store.get_event(event_id)
    if ev is None:
        return None
    return {k: _iso(v) for k, v in ev.items()}


def _drop_source_of(target: dict) -> str:
    """Canonical source name (``todoist``/``capacities``/``calendar``) for a
    Drop snapshot, from the resolved target or its pseudo-path. "" when the
    row has no recognizable source."""
    source = str(target.get("source") or "").strip().casefold()
    if source in ("todoist", "capacities", "calendar"):
        return source
    path = str(target.get("path") or "")
    if target.get("todoist_id") or path.startswith(TODOIST_PATH_PREFIX):
        return "todoist"
    if target.get("capacities_id") or path.startswith(CAPACITIES_PATH_PREFIX):
        return "capacities"
    if target.get("event_id") or path.startswith(CALENDAR_PATH_PREFIX):
        return "calendar"
    return ""


def _drop_minutes(target: dict) -> int | None:
    """Best-effort duration (minutes) for a Drop snapshot; None when unknown."""
    for key in ("minutes", "duration_min", "duration_minutes",
                "event_duration_min"):
        value = target.get(key)
        if (isinstance(value, (int, float)) and not isinstance(value, bool)
                and value > 0):
            return int(value)
    blocks = target.get("blocks")
    if (isinstance(blocks, (int, float)) and not isinstance(blocks, bool)
            and blocks > 0):
        return int(blocks) * 30
    return None


def _drop_snapshot(target: dict) -> dict[str, Any]:
    """Optional chip/duration fields stored on the dropped entry.

    ``source`` names the source chip, ``kind`` is a render hint (a Capacities
    structure id, an explicit ``kind``, or a recurring marker), and ``minutes``
    is the duration. Every field is optional: a legacy entry without them is
    still a valid Drop, and one is still valid with none of them.
    """
    snapshot: dict[str, Any] = {}
    source = _drop_source_of(target)
    if source:
        snapshot["source"] = source
    kind = ""
    if target.get("capacities_structure_id"):
        kind = str(target["capacities_structure_id"])
    elif target.get("kind"):
        kind = str(target["kind"])
    elif target.get("is_recurring"):
        kind = "recurring"
    if kind:
        snapshot["kind"] = kind
    minutes = _drop_minutes(target)
    if minutes is not None:
        snapshot["minutes"] = minutes
    return snapshot


def _runstate_drop_step(target: dict, valid_date: date) -> dict:
    """One runstate-only Drop step keyed by the row's canonical identity.

    Carries ``identity``/``path``/``source`` so a calendar or Capacities row
    keys on its source identity rather than its name (Unit 2 decision b), and
    the optional ``{source, kind, minutes}`` snapshot the Skipped list renders.
    """
    item = {k: target.get(k) for k in
            ("name", "todoist_id", "id", "identity", "path", "source")}
    item.update(_drop_snapshot(target))
    return {
        "kind": "runstate.drop",
        "surface": "runstate",
        "system": "runstate",
        "item": item,
        "date": str(valid_date),
    }


def plan_steps(verb: str, target: dict, args: dict, valid_date: date) -> list[dict]:
    """Pure: verb + resolved target -> ordered step specs (no clients)."""
    if target.get("phase") == "staging" and verb not in STAGING_VERBS:
        raise RuntimeActionError(
            f"{verb!r} needs a committed plan item — "
            f"{target.get('name')!r} is still staged")
    steps: list[dict] = []
    if verb in ("complete", "done"):
        if target.get("todoist_id"):
            steps.append({"kind": "todoist.close", "surface": "todoist",
                          "task_id": target["todoist_id"]})
        if target.get("capacities_id") and target.get("capacities_completion_supported") is True:
            fingerprint = str(target.get("source_fingerprint") or "").strip()
            if not fingerprint:
                raise RuntimeActionError(
                    f"{target['name']!r}: Capacities completion requires "
                    "a source fingerprint baseline"
                )
            steps.append({
                "kind": "capacities.complete", "surface": "capacities",
                "object_id": str(target["capacities_id"]),
                "expected_fingerprint": fingerprint,
            })
        if not steps:
            raise RuntimeActionError(
                f"{target['name']!r}: no completable source in today's manifest")
    elif verb == "drop_from_plan":
        # IMP-05: a date-scoped TDTB exclusion. One runstate-only step — the
        # identity is the stable source identity (todoist:<id> / capacities /
        # calendar) so the same name across surfaces dedupes; the dated runstate
        # note scopes it to today, making the item eligible again tomorrow.
        steps.append(_runstate_drop_step(target, valid_date))
    elif verb == "restore_today":
        # B1: the reverse of a durable Skip today. One runstate-only step
        # addressed by identity — dropped rows are not in the digest index.
        identity = str(target.get("identity") or "").strip()
        if not identity:
            raise RuntimeActionError(
                "restore_today requires a dropped identity")
        steps.append({
            "kind": "runstate.undrop",
            "surface": "runstate",
            "system": "runstate",
            "identity": identity,
            "date": str(valid_date),
        })
    elif verb == "hide_always":
        # B2: the journaled replacement for the controller-side
        # alwaysExclude path. A Todoist row appends a persistent Todoist
        # exclusion; a Capacities row adds its canonical stable identity to
        # the Capacities exclusion set. Pure-local settings-store steps (no
        # provider client), so the verb is legal before and after commit; the
        # row then leaves plan-inputs through the EXISTING exclusion filters.
        todoist_id = target.get("todoist_id")
        source = str(target.get("source") or "").strip().casefold()
        if todoist_id:
            name = str(target.get("name") or "").strip()
            if not name:
                raise RuntimeActionError(
                    f"{target.get('name')!r}: hide_always requires a "
                    "Todoist task title")
            steps.append({
                "kind": "exclusions.add",
                "surface": "settings",
                "system": "settings",
                "dimension": "todoist",
                "identity": f"todoist:{todoist_id}",
                "entry": {"id": str(todoist_id), "name": name},
            })
        elif source == "capacities" or target.get("capacities_id"):
            identity = str(target.get("identity") or "").strip()
            if not identity:
                raise RuntimeActionError(
                    f"{target.get('name')!r}: hide_always requires a "
                    "Capacities identity")
            steps.append({
                "kind": "exclusions.add",
                "surface": "settings",
                "system": "settings",
                "dimension": "capacities",
                "identity": identity,
            })
        else:
            raise RuntimeActionError(
                f"{target.get('name')!r}: no hideable source in today's plan")
    elif verb == "unassign":
        # Unit 2 decision b: a calendar row has no source write path, so
        # unassign is a runstate-only Drop keyed by the calendar identity. The
        # original all-day event is never touched.
        source = str(target.get("source") or "").strip().casefold()
        path = str(target.get("path") or "")
        if source == "calendar" or path.startswith(CALENDAR_PATH_PREFIX):
            steps.append(_runstate_drop_step(target, valid_date))
        else:
            # IMP-05: Todoist date clear (non-recurring) or
            # non-completing due advance (recurring). No deferral-memory nudge.
            task_id = target.get("todoist_id") or target.get("id")
            if task_id:
                steps.append({"kind": "todoist.unassign", "surface": "todoist",
                              "system": "todoist", "task_id": str(task_id),
                              "close": False})
            if not steps:
                raise RuntimeActionError(
                    f"{target['name']!r}: no unassignable source")
    elif verb in ("skip_today", "remove_from_today"):
        if target.get("event_id"):
            steps.append({"kind": "calendar.delete", "surface": "calendar",
                          "event_id": target["event_id"]})
        if target.get("todoist_id"):
            steps.append({"kind": "todoist.clear_time", "surface": "todoist",
                          "task_id": target["todoist_id"]})
        if not steps:
            raise RuntimeActionError(
                f"{target['name']!r}: nothing scheduled today to remove")
    elif verb == "duration_edit":
        blocks = args.get("blocks")
        if not isinstance(blocks, int) or blocks < 1:
            raise RuntimeActionError("duration_edit requires integer blocks >= 1")
        minutes = blocks * 30
        if target.get("event_id"):
            steps.append({"kind": "calendar.resize", "surface": "calendar",
                          "event_id": target["event_id"], "minutes": minutes})
        if target.get("todoist_id"):
            steps.append({"kind": "todoist.duration", "surface": "todoist",
                          "task_id": target["todoist_id"], "minutes": minutes})
        if not steps:
            raise RuntimeActionError(
                f"{target['name']!r}: no derived records to resize")
    elif verb == "move_resize":
        start, end = args.get("start"), args.get("end")
        if not start or not end:
            raise RuntimeActionError("move_resize requires start and end (HH:MM)")
        if not target.get("event_id"):
            raise RuntimeActionError(
                f"{target['name']!r}: no owned Blocks event to move")
        steps.append({"kind": "calendar.move", "surface": "calendar",
                      "event_id": target["event_id"],
                      "start": start, "end": end})
    elif verb == "assign":
        # T9 forgot-strip: one-tap "yes, today after all". The inverse of the
        # signal that surfaced the row — a Todoist row gets pulled to
        # today. Journaled and undoable like every other verb.
        if target.get("todoist_id"):
            steps.append({"kind": "todoist.assign_today", "surface": "todoist",
                          "task_id": target["todoist_id"]})
        if not steps:
            raise RuntimeActionError(
                f"{target['name']!r}: no assignable source")
    elif verb == "defer":
        # T1 defer-with-memory. The record ALWAYS happens (that is the verb);
        # un-scheduling today is best-effort on whatever derived records the
        # target actually has, so `defer` is legal pre-commit (nothing placed
        # yet) as well as post-commit — unlike remove_from_today it never
        # refuses an item with no scheduled artifacts.
        steps.append({"kind": "deferrals.record", "surface": "runstate",
                      "item": {k: target.get(k) for k in
                               ("name", "todoist_id")}})
        if target.get("event_id"):
            steps.append({"kind": "calendar.delete", "surface": "calendar",
                          "event_id": target["event_id"]})
        if target.get("todoist_id"):
            steps.append({"kind": "todoist.clear_time", "surface": "todoist",
                          "task_id": target["todoist_id"]})
    elif verb in ("delete_permanent", "delete"):
        if target.get("event_id"):
            steps.append({"kind": "calendar.delete", "surface": "calendar",
                          "event_id": target["event_id"]})
        if target.get("todoist_id"):
            steps.append({"kind": "todoist.delete", "surface": "todoist",
                          "task_id": target["todoist_id"]})
        if not steps:
            raise RuntimeActionError(
                f"{target['name']!r}: nothing to delete in today's manifest")
    else:
        raise RuntimeActionError(f"unknown verb {verb!r}")
    return steps


# ---------------------------------------------------------------------------
# Step execution — apply / reverse (compensation and undo share reverse ops)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Hide always — persistent settings-store writes (revision-guarded)
# ---------------------------------------------------------------------------
#
# Both settings stores share one RMW discipline: read the current policy,
# write with that revision, and raise a typed conflict when another writer
# won. Hide always retries such a conflict ONCE against the freshly read
# revision (the single retry the controller-side alwaysExclude path had) and
# then fails cleanly — a stale write is refused by the store BEFORE it touches
# the durable bytes, so a clean failure leaves them intact.


def _conflict_retried_once(attempt: int, exc: Exception, what: str) -> None:
    """Raise the clean failure once the single conflict retry is spent."""
    if attempt >= 1:
        raise RuntimeActionError(
            f"{what} changed concurrently; retried once and still "
            f"conflicted — nothing was written") from exc


def _todoist_exclusion_present(current: Any, entry: Any) -> bool:
    """Whether the policy already excludes this task by id OR normalized
    title (the same any-match rule :mod:`todoist_exclusions` evaluates)."""
    normalized = exclusion_settings.normalize_todoist_name(entry.name)
    return any(
        row.id == entry.id
        or exclusion_settings.normalize_todoist_name(row.name) == normalized
        for row in current.todoist
    )


def _add_todoist_exclusion(step: dict) -> None:
    data = step["entry"]
    entry = exclusion_settings.TodoistExclusion(
        id=data["id"], name=data["name"], added_at=data["added_at"])
    for attempt in (0, 1):
        current = exclusion_settings.read_settings().settings
        if _todoist_exclusion_present(current, entry):
            # Already excluded: no write, and reverse must leave it alone.
            step["already_present"] = True
            return
        step["before"] = {
            "revision": current.revision,
            "todoist": [row.as_dict() for row in current.todoist],
        }
        try:
            saved = exclusion_settings.save_settings(
                expected_revision=current.revision,
                exclusions=current.tags,
                todoist=list(current.todoist) + [entry],
            )
        except exclusion_settings.ExclusionSettingsConflictError as exc:
            _conflict_retried_once(attempt, exc, "Todoist exclusion settings")
            continue
        step["after"] = {"revision": saved.revision}
        return


def _remove_todoist_exclusion(step: dict) -> None:
    if step.get("already_present"):
        return
    entry_id = step["entry"]["id"]
    for attempt in (0, 1):
        current = exclusion_settings.read_settings().settings
        remaining = [row for row in current.todoist if row.id != entry_id]
        if len(remaining) == len(current.todoist):
            return  # already gone — nothing to remove
        try:
            exclusion_settings.save_settings(
                expected_revision=current.revision,
                exclusions=current.tags,
                todoist=remaining,
            )
        except exclusion_settings.ExclusionSettingsConflictError as exc:
            _conflict_retried_once(attempt, exc, "Todoist exclusion settings")
            continue
        return


def _add_capacities_exclusion(step: dict) -> None:
    identity = step["identity"]
    for attempt in (0, 1):
        current = capacities_settings.read_settings().settings
        if identity in current.excluded:
            step["already_present"] = True
            return
        step["before"] = {
            "revision": current.revision,
            "excluded": sorted(current.excluded),
        }
        try:
            saved = capacities_settings.save_settings(
                expected_revision=current.revision,
                native_task_auto=current.native_task_auto,
                excluded=current.excluded | {identity},
                active_structures=current.active_structures,
                native_task_structures=current.native_task_structures,
                active_statuses=current.active_statuses,
                assigned_structures=dict(current.assigned_structures),
                assigned_on_structures=dict(current.assigned_on_structures),
            )
        except capacities_settings.SettingsConflictError as exc:
            _conflict_retried_once(attempt, exc, "Capacities settings")
            continue
        step["after"] = {"revision": saved.revision}
        return


def _remove_capacities_exclusion(step: dict) -> None:
    if step.get("already_present"):
        return
    identity = step["identity"]
    for attempt in (0, 1):
        current = capacities_settings.read_settings().settings
        if identity not in current.excluded:
            return  # already gone — nothing to remove
        try:
            capacities_settings.save_settings(
                expected_revision=current.revision,
                native_task_auto=current.native_task_auto,
                excluded=current.excluded - {identity},
                active_structures=current.active_structures,
                native_task_structures=current.native_task_structures,
                active_statuses=current.active_statuses,
                assigned_structures=dict(current.assigned_structures),
                assigned_on_structures=dict(current.assigned_on_structures),
            )
        except capacities_settings.SettingsConflictError as exc:
            _conflict_retried_once(attempt, exc, "Capacities settings")
            continue
        return


def _apply_exclusions_add(step: dict) -> None:
    dimension = step.get("dimension")
    if dimension == "todoist":
        step["entry"].setdefault("added_at", _now_iso())
        _add_todoist_exclusion(step)
    elif dimension == "capacities":
        _add_capacities_exclusion(step)
    else:  # pragma: no cover — plan_steps only emits the dimensions above
        raise RuntimeActionError(f"unknown exclusion dimension {dimension!r}")


def _reverse_exclusions_add(step: dict) -> None:
    dimension = step.get("dimension")
    if dimension == "todoist":
        _remove_todoist_exclusion(step)
    elif dimension == "capacities":
        _remove_capacities_exclusion(step)
    else:  # pragma: no cover
        raise RuntimeActionError(f"unknown exclusion dimension {dimension!r}")


def _apply_step(step: dict, valid_date: date,
                todoist: Any, store: Any, capacities: Any) -> None:
    kind = step["kind"]
    if kind == "todoist.close":
        step["before"] = {"task": todoist.get_task(step["task_id"])}
        todoist.close_task(step["task_id"])
    elif kind == "capacities.complete":
        outcome = capacities.complete(
            step["object_id"],
            expected_fingerprint=step.get("expected_fingerprint"),
        )
        status = getattr(outcome, "status", None)
        if status not in {"already_complete", "completed", "completed_after_timeout"}:
            raise RuntimeActionError(
                f"Capacities completion returned unsupported status {status!r}"
            )
        step["outcome"] = {
            "status": status,
            "object_id": getattr(outcome, "object_id", step["object_id"]),
            "completion_property": getattr(outcome, "completion_property", None),
            "before_property": getattr(outcome, "before_property", None),
            "after_fingerprint": getattr(outcome, "after_fingerprint", None),
        }
    elif kind == "todoist.clear_time":
        task = todoist.get_task(step["task_id"])
        step["before"] = {"task": task}
        # T27 guard: a recurring due is pattern-owned — due_string in any form
        # (incl. "today") wipes the recurrence. The all-day/no-time intent on a
        # recurring task skips the due write entirely; the event removal above
        # already un-schedules today.
        if (task.get("due") or {}).get("is_recurring"):
            step["skipped_recurring"] = True
        else:
            todoist.reschedule_task(step["task_id"], "today")
    elif kind == "todoist.duration":
        step["before"] = {"task": todoist.get_task(step["task_id"])}
        todoist.update_task(step["task_id"], duration=step["minutes"],
                            duration_unit="minute")
    elif kind == "todoist.delete":
        step["before"] = {"task": todoist.get_task(step["task_id"])}
        todoist.delete_task(step["task_id"])
    elif kind == "calendar.delete":
        snap = _event_snapshot(store, step["event_id"])
        if snap is None:
            raise RuntimeActionError(
                f"owned event {step['event_id']!r} not found live")
        step["before"] = {"event": snap}
        store.delete_event(step["event_id"])
    elif kind in ("calendar.resize", "calendar.move"):
        snap = _event_snapshot(store, step["event_id"])
        if snap is None:
            raise RuntimeActionError(
                f"owned event {step['event_id']!r} not found live")
        step["before"] = {"event": snap}
        if kind == "calendar.resize":
            start_dt = datetime.fromisoformat(snap["start"])
            end_dt = start_dt + _minutes(step["minutes"])
            store.update_event(step["event_id"], end=end_dt)
        else:
            store.update_event(
                step["event_id"],
                start=_hhmm_to_dt(valid_date, step["start"]),
                end=_hhmm_to_dt(valid_date, step["end"]),
            )
    elif kind == "todoist.assign_today":
        task = todoist.get_task(step["task_id"])
        step["before"] = {"task": task}
        # T27 guard, same as clear_time: a recurring due is pattern-owned and
        # any due_string write wipes the recurrence. A recurring task already
        # recurs onto today when it's due — there is nothing to assign.
        if (task.get("due") or {}).get("is_recurring"):
            step["skipped_recurring"] = True
        else:
            todoist.reschedule_task(step["task_id"], "today")
    elif kind == "todoist.unassign":
        # IMP-05 Unassign: non-recurring clears the date; recurring advances
        # the due occurrence WITHOUT completing (non-completing due update
        # that preserves the recurrence string/type), then proves unchanged
        # completion counters by read-back. An unsupported pattern fails
        # closed before any write; a completion change after the write is
        # compensated back by the engine.
        task = todoist.get_task(step["task_id"])
        step["before"] = {"task": task}
        due = task.get("due") or {}
        if due.get("is_recurring"):
            advance = _next_recurring_due(task)  # raises -> fail closed
            if "T" in advance:
                todoist.reschedule_task_datetime(step["task_id"], advance)
            else:
                todoist.reschedule_task_date(step["task_id"], advance)
            step["advance"] = advance
        else:
            todoist.clear_task_date(step["task_id"])
        after = todoist.get_task(step["task_id"])
        step["after"] = {"task": after}
        proof = _completion_proof(task, after)
        step["readback"] = proof
        if proof["completed"] or proof["changed"]:
            step["applied"] = True  # due write happened — mark so it reverses
            raise RuntimeActionError(
                "unassign would change completion history — fail closed; "
                "defer the recurring task in Todoist instead")
    elif kind == "runstate.drop":
        # IMP-05 Drop from plan: date-scoped runstate exclusion. Identity is
        # stable so a repeat of the SAME generation is a no-op; the dated note
        # scopes it to today. Every transition mints a fresh ownership witness
        # so a later cycle is distinguishable from this one.
        # The caller (apply_action / undo_action) already holds
        # ``runstate.day_lock``, so the RMW must NOT go through
        # ``runstate.update_runstate`` (non-reentrant lock -> deadlock).
        item = step.get("item") or {}
        identity = drop_identity_of(item)
        step["identity"] = identity
        step["before"] = {"dropped": runstate.read_dropped(valid_date)}
        owner = secrets.token_hex(8)
        observed: dict[str, Any] = {}

        def _append_drop(state: dict[str, Any]) -> None:
            cycles = dict(_drop_cycles_of(state))
            prior = _cycle_witness(cycles.get(identity) or {})
            dropped = [d for d in (state.get("dropped") or [])
                       if d.get("identity") != identity]
            entry = {"identity": identity,
                     "name": item.get("name") or "",
                     "dropped_at": _now_iso()}
            for key in ("source", "kind", "minutes"):
                if item.get(key) is not None:
                    entry[key] = item[key]
            dropped.append(entry)
            state["dropped"] = dropped
            cycle = {"owner": owner, "counter": prior["counter"] + 1}
            cycles[identity] = cycle
            state[DROP_CYCLES_KEY] = cycles
            observed["prior"] = prior
            observed["after"] = dict(cycle)

        _locked_runstate_rmw(valid_date, _append_drop)
        step["before"]["cycle"] = observed["prior"]
        step["after"] = observed["after"]
    elif kind == "runstate.undrop":
        # Restore today: remove exactly the identity the caller addressed.
        identity = str(step.get("identity") or "").strip()
        dropped = runstate.read_dropped(valid_date)
        entry = next(
            (d for d in dropped if str(d.get("identity") or "") == identity),
            None,
        )
        if entry is None:
            raise RuntimeActionError(
                f"{identity!r}: not in today's skipped list")
        step["identity"] = identity
        step["before"] = {"entry": entry, "dropped": dropped}
        observed_undrop: dict[str, Any] = {}

        def _remove_drop(state: dict[str, Any]) -> None:
            cycles = dict(_drop_cycles_of(state))
            prior = _cycle_witness(cycles.get(identity) or {})
            state["dropped"] = [
                d for d in (state.get("dropped") or [])
                if d.get("identity") != identity
            ]
            cycle = {"owner": "", "counter": prior["counter"] + 1}
            cycles[identity] = cycle
            state[DROP_CYCLES_KEY] = cycles
            observed_undrop["prior"] = prior
            observed_undrop["after"] = dict(cycle)

        _locked_runstate_rmw(valid_date, _remove_drop)
        step["before"]["cycle"] = observed_undrop["prior"]
        step["after"] = observed_undrop["after"]
    elif kind == "deferrals.record":
        item = step["item"]
        key = deferrals.key_for_item(item)
        step["key"] = key
        step["before"] = {"entry": deferrals.load_deferrals().get(key)}
        step["entry"] = deferrals.record_deferral(item, valid_date)
    elif kind == "exclusions.add":
        # B2: persistent exclusion add in the app-owned settings stores.
        # Pure-local (no provider client); the store's own revision guard is
        # retried once here so a transient concurrent write is absorbed.
        _apply_exclusions_add(step)
    else:  # pragma: no cover — plan_steps only emits the kinds above
        raise RuntimeActionError(f"unknown step kind {kind!r}")
    step["applied"] = True


def _reverse_step(step: dict, valid_date: date,
                  todoist: Any, store: Any, capacities: Any) -> None:
    """Exact reverse of one applied step, from its before-image."""
    kind = step["kind"]
    before = step.get("before") or {}
    if kind == "todoist.close":
        todoist.reopen_task(step["task_id"])
    elif kind == "capacities.complete":
        outcome = step.get("outcome") or {}
        if outcome.get("status") == "already_complete":
            return
        restored = capacities.restore_completion(
            step["object_id"],
            completion_property=outcome.get("completion_property"),
            before_property=outcome.get("before_property"),
            expected_fingerprint=outcome.get("after_fingerprint"),
        )
        step["undo_outcome"] = {
            "status": getattr(restored, "status", None),
            "object_id": getattr(restored, "object_id", step["object_id"]),
        }
    elif kind == "todoist.clear_time":
        if step.get("skipped_recurring"):
            return  # nothing was written — nothing to reverse
        due = (before.get("task") or {}).get("due") or {}
        if due.get("datetime"):
            todoist.reschedule_task_datetime(step["task_id"], due["datetime"])
        elif due.get("string"):
            todoist.reschedule_task(step["task_id"], due["string"])
    elif kind == "todoist.duration":
        prior = (before.get("task") or {}).get("duration")
        prior_minutes = prior.get("amount") if isinstance(prior, dict) else prior
        todoist.update_task(step["task_id"],
                            duration=prior_minutes, duration_unit="minute")
    elif kind == "todoist.delete":
        task = before.get("task") or {}
        created = todoist.create_task(
            task.get("content") or step["task_id"],
            project_id=task.get("project_id"),
            **({"description": task["description"]} if task.get("description") else {}),
        )
        step["recreated_task_id"] = created.get("id")
    elif kind == "calendar.delete":
        ev = before.get("event") or {}
        new_id = store.create_event(calendar_bridge.EventSpec(
            title=ev.get("title") or step["event_id"],
            start=datetime.fromisoformat(ev["start"]),
            end=datetime.fromisoformat(ev["end"]),
            calendar_id=ev.get("calendar_id") or "",
            notes=ev.get("notes"),
        ))
        step["recreated_event_id"] = new_id
    elif kind in ("calendar.resize", "calendar.move"):
        ev = before.get("event") or {}
        store.update_event(step["event_id"],
                           start=datetime.fromisoformat(ev["start"]),
                           end=datetime.fromisoformat(ev["end"]))
    elif kind == "todoist.assign_today":
        if step.get("skipped_recurring"):
            return  # nothing was written — nothing to reverse
        due = (before.get("task") or {}).get("due") or {}
        if due.get("datetime"):
            todoist.reschedule_task_datetime(step["task_id"], due["datetime"])
        elif due.get("string"):
            todoist.reschedule_task(step["task_id"], due["string"])
    elif kind == "todoist.unassign":
        due = (before.get("task") or {}).get("due") or {}
        if due.get("datetime"):
            todoist.reschedule_task_datetime(step["task_id"], due["datetime"])
        elif due.get("date"):
            if "T" in str(due["date"]):
                todoist.reschedule_task_datetime(step["task_id"], due["date"])
            else:
                todoist.reschedule_task_date(step["task_id"], due["date"])
        elif due.get("string"):
            todoist.reschedule_task(step["task_id"], due["string"])
        # no due at all — the clear was reversed by not writing anything
    elif kind == "runstate.drop":
        # B1 fix: remove ONLY this drop's own identity, and only while this
        # step's own generation still owns it. Restoring the whole before-image
        # would clobber a later Skip today; a bare identity check would clobber
        # a later Skip today of the SAME identity.
        identity = str(step.get("identity") or "")
        recorded = _recorded_witness(step)
        prior_cycle = step.get("before", {}).get("cycle")

        def _remove_own_drop(state: dict[str, Any]) -> None:
            cycles = dict(_drop_cycles_of(state))
            live = _cycle_witness(cycles.get(identity) or {})
            present = any(d.get("identity") == identity
                          for d in (state.get("dropped") or []))
            if not present:
                return  # nothing live to reverse — leave the state alone
            if recorded is None:
                if live["counter"] > 1:
                    raise RuntimeActionError(
                        f"{identity!r}: drop ownership is not recorded for "
                        "this step and a later skip/restore cycle ran — "
                        "refusing to reverse")
            elif live != recorded:
                raise RuntimeActionError(
                    f"{identity!r}: a later skip/restore cycle owns this "
                    "identity — refusing to reverse an older drop")
            state["dropped"] = [
                d for d in (state.get("dropped") or [])
                if d.get("identity") != identity
            ]
            if isinstance(prior_cycle, dict) and "counter" in prior_cycle:
                cycles[identity] = _cycle_witness(prior_cycle)
            else:
                cycles[identity] = {"owner": "",
                                    "counter": live["counter"] + 1}
            state[DROP_CYCLES_KEY] = cycles

        _locked_runstate_rmw(valid_date, _remove_own_drop)
    elif kind == "runstate.undrop":
        # Reverse of Restore today: re-add the saved dropped entry verbatim,
        # but ONLY while this restore's own post-image (identity absent, same
        # witness) still holds. A later skip/restore cycle must never be
        # clobbered by resurrecting a stale entry.
        entry = before.get("entry") or {}
        identity = str(step.get("identity") or entry.get("identity") or "")
        recorded = _recorded_witness(step)
        prior_cycle = before.get("cycle")

        def _re_add_drop(state: dict[str, Any]) -> None:
            cycles = dict(_drop_cycles_of(state))
            live = _cycle_witness(cycles.get(identity) or {})
            present = any(d.get("identity") == identity
                          for d in (state.get("dropped") or []))
            if present:
                raise RuntimeActionError(
                    f"{identity!r}: identity is dropped again — a later "
                    "skip owns it; refusing to reverse the restore")
            if recorded is None:
                if live["counter"]:
                    raise RuntimeActionError(
                        f"{identity!r}: restore ownership is not recorded "
                        "for this step and a later skip/restore cycle ran — "
                        "refusing to reverse")
                restored = {"owner": secrets.token_hex(8),
                            "counter": live["counter"] + 1}
            elif live != recorded:
                raise RuntimeActionError(
                    f"{identity!r}: a later skip/restore cycle superseded "
                    "this restore — refusing to reverse")
            else:
                restored = _cycle_witness(
                    prior_cycle if isinstance(prior_cycle, dict) else {})
                if not restored["owner"]:
                    restored["owner"] = secrets.token_hex(8)
                if not restored["counter"]:
                    restored["counter"] = live["counter"] + 1
            dropped = [d for d in (state.get("dropped") or [])
                       if d.get("identity") != identity]
            dropped.append(entry)
            state["dropped"] = dropped
            cycles[identity] = restored
            state[DROP_CYCLES_KEY] = cycles

        _locked_runstate_rmw(valid_date, _re_add_drop)
    elif kind == "deferrals.record":
        deferrals.set_entry(step["key"], before.get("entry"))
    elif kind == "exclusions.add":
        # Reverse removes ONLY the entry this action added; a pre-existing
        # exclusion (``already_present``) is never touched.
        _reverse_exclusions_add(step)
    else:  # pragma: no cover
        raise RuntimeActionError(f"unknown step kind {kind!r}")


def _minutes(n: int):
    return timedelta(minutes=n)


# ---------------------------------------------------------------------------
# Recurring Unassign — non-completing due advance (frozen action table)
# ---------------------------------------------------------------------------
#
# Todoist has no supported "postpone recurring occurrence" endpoint; close is
# a completion operation. The locked contract therefore advances the due
# occurrence through a non-completing due update that preserves the recurrence
# string/type, and proves unchanged completion counters by read-back. When the
# pattern cannot be advanced deterministically, the apply fails closed BEFORE
# any write — never close, never clear the date, never masquerade as Done.

def _freq_interval(freq: str, interval: int) -> tuple[int, int, int]:
    """(days, months, years) advance for an RRULE/plain frequency."""
    if freq == "DAILY":
        return (interval, 0, 0)
    if freq == "WEEKLY":
        return (interval * 7, 0, 0)
    if freq == "MONTHLY":
        return (0, interval, 0)
    if freq == "YEARLY":
        return (0, 0, interval)
    raise RuntimeActionError(
        f"unsupported recurrence frequency {freq!r} — fail closed; "
        "defer the recurring task in Todoist instead")


def _recurrence_interval(due: dict[str, Any]) -> tuple[int, int, int]:
    """The per-occurrence advance for a due dict, from its rrule or string.

    Supports RRULE (``FREQ=DAILY|WEEKLY|MONTHLY|YEARLY`` with optional
    ``INTERVAL=N``) and the plain "every N day|week|month|year" forms.
    Anything else raises ``RuntimeActionError`` (fail closed).
    """
    rrule = str(due.get("rrule") or "").upper()
    freq_m = re.search(r"FREQ=(\w+)", rrule)
    if freq_m:
        interval = 1
        int_m = re.search(r"INTERVAL=(\d+)", rrule)
        if int_m:
            interval = max(1, int(int_m.group(1)))
        return _freq_interval(freq_m.group(1), interval)
    text = str(due.get("string") or "").lower()
    m = re.search(r"every\s+(\d+)?\s*(day|week|month|year)s?", text)
    if not m:
        raise RuntimeActionError(
            f"unsupported recurring pattern "
            f"{due.get('string') or due.get('rrule')!r} — fail closed; "
            "defer the recurring task in Todoist instead")
    interval = int(m.group(1) or 1)
    unit = {"day": "DAILY", "week": "WEEKLY",
            "month": "MONTHLY", "year": "YEARLY"}[m.group(2)]
    return _freq_interval(unit, interval)


def _add_months(dt: datetime, months: int) -> datetime:
    """Calendar-safe month arithmetic (clamp the day to the target month)."""
    month_index = dt.month - 1 + months
    year = dt.year + month_index // 12
    month = month_index % 12 + 1
    day = min(dt.day, calendar.monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day)


def _next_recurring_due(task: dict[str, Any]) -> str:
    """The next occurrence's due value (datetime or date ISO, matching the
    task's due shape) — or ``RuntimeActionError`` when not determinable.

    "every weekday" advances to the next weekday; all other supported
    patterns advance by their interval. The due shape is preserved:
    a datetime due advances as a datetime, a date-only due as a date.
    """
    due = task.get("due") or {}
    raw = due.get("datetime") or due.get("date")
    # Todoist also returns floating timed dues in `date`, without a
    # `datetime` key. Preserve the value's time-bearing shape in both cases.
    is_dt = bool(due.get("datetime")) or "T" in str(raw)
    if not raw:
        raise RuntimeActionError(
            "recurring task has no parseable due to advance — fail closed; "
            "defer the recurring task in Todoist instead")
    try:
        if is_dt:
            anchor = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            if anchor.tzinfo is not None:
                anchor = anchor.replace(tzinfo=None)
        else:
            anchor = datetime.strptime(str(raw), "%Y-%m-%d")
    except ValueError as exc:
        raise RuntimeActionError(
            f"recurring due {raw!r} is not parseable — fail closed; "
            "defer the recurring task in Todoist instead") from exc
    text = str(due.get("string") or "").lower()
    if "weekday" in text:
        nxt = anchor + timedelta(days=1)
        while nxt.weekday() >= 5:
            nxt += timedelta(days=1)
    else:
        days, months, years = _recurrence_interval(due)
        nxt = anchor
        if months or years:
            nxt = _add_months(nxt, months + years * 12)
        nxt = nxt + timedelta(days=days)
    if is_dt:
        return nxt.strftime("%Y-%m-%dT%H:%M:%S")
    return nxt.strftime("%Y-%m-%d")


def _completion_proof(before_task: dict[str, Any],
                      after_task: dict[str, Any]) -> dict[str, Any]:
    """Read-back evidence that Unassign never completed the task.

    Compares the completion/checked fields across the advance and verifies
    the recurrence string/type survived. Any completion change means the
    server misbehaved — the caller fails closed and compensates.
    """
    fields = ("is_completed", "completed_at", "completed_count", "checked")
    changed: dict[str, Any] = {}
    for field in fields:
        b, a = before_task.get(field), after_task.get(field)
        if b != a:
            changed[field] = {"before": b, "after": a}
    before_due = before_task.get("due") or {}
    after_due = after_task.get("due") or {}
    recurrence_preserved = (
        (before_due.get("string") or before_due.get("rrule"))
        == (after_due.get("string") or after_due.get("rrule"))
        and before_due.get("is_recurring") == after_due.get("is_recurring")
    )
    return {
        "completed": bool(after_task.get("is_completed"))
        or bool(after_task.get("completed_at")),
        "changed": changed,
        "recurrence_preserved": recurrence_preserved,
    }


def _locked_runstate_rmw(valid_date: date,
                         mutator: Any) -> None:
    """Run-state RMW for callers that ALREADY hold ``runstate.day_lock``.

    ``runstate.update_runstate`` takes the per-day lock itself, and
    ``threading.Lock`` is not reentrant — calling it from inside
    ``apply_action``/``undo_action`` (which hold the same lock) deadlocks.
    This performs the same read-modify-write under the caller-held lock.
    """
    state = runstate.build_runstate(
        runstate.read_runstate(valid_date) or None)
    mutator(state)
    runstate.write_runstate(valid_date, state)


_SURFACE_CLIENT = {"todoist": "todoist", "calendar": "store",
                   "capacities": "capacities", "runstate": None}


def _check_surfaces(
    steps: list[dict], todoist: Any, store: Any, capacities: Any
) -> None:
    missing: list[str] = []
    for step in steps:
        surface = step["surface"]
        if surface == "todoist" and todoist is None:
            missing.append("todoist")
        elif surface == "calendar" and store is None:
            missing.append("calendar")
        elif surface == "capacities" and capacities is None:
            missing.append("capacities")
    if missing:
        raise RuntimeActionError(
            "surface unavailable: " + ", ".join(sorted(set(missing))))


# ---------------------------------------------------------------------------
# Batch-undo conflict checks
# ---------------------------------------------------------------------------
#
# A batch undo reverses whole items only when each applied step's POST-apply
# state still holds, so a later edit to any one of them is reported as a
# conflict and left alone rather than clobbered. Each of these kinds therefore
# has a safe, non-mutating pre-reverse check. A kind absent from the set is
# reported ``non_reversible`` by the caller: there is no safe way to prove a
# later edit did not touch it, and a blind reverse is exactly what batch undo
# must never do.

CONFLICT_CHECKED_KINDS = frozenset({
    "todoist.close",
    "todoist.unassign",
    "runstate.drop",
    "runstate.undrop",
    "exclusions.add",
    "capacities.complete",
})


class StepNotReversible(RuntimeActionError):
    """The step kind has no safe pre-reverse conflict check."""


def _drop_has_identity(valid_date: date, identity: Any) -> bool:
    key = str(identity or "")
    return any(str(d.get("identity") or "") == key
               for d in runstate.read_dropped(valid_date))


# ---------------------------------------------------------------------------
# Drop/Restore cycle ownership (generation witness)
# ---------------------------------------------------------------------------
#
# Skip today and Restore today are two TRANSITIONS of one identity's in-day
# membership, so identity presence alone cannot say which cycle a journal step
# belongs to: after ``drop -> restore -> drop`` the identity is present again, and
# a presence-only check would authorize an undo of the FIRST drop to delete the
# SECOND drop's entry. Each runstate transition therefore mints a witness —
# a monotonic per-identity ``counter`` plus the ``owner`` token of the cycle
# currently holding the entry ("" when the identity is restored) — persisted in
# the run-state note under ``drop_cycles``. ``runtime_actions`` owns that key end
# to end; ``runstate`` has no default for it, so a note (or a journal step)
# without one reads as legacy and its reverse fails closed.
DROP_CYCLES_KEY = "drop_cycles"


def _drop_cycles_of(state: dict[str, Any]) -> dict[str, Any]:
    raw = state.get(DROP_CYCLES_KEY)
    return raw if isinstance(raw, dict) else {}


def _live_drop_cycle(valid_date: date, identity: Any) -> dict[str, Any]:
    """The persisted witness for ``identity`` (``{}`` when none is recorded)."""
    cycle = _drop_cycles_of(runstate.read_runstate(valid_date) or {}).get(
        str(identity or ""))
    return cycle if isinstance(cycle, dict) else {}


def _cycle_witness(cycle: dict[str, Any]) -> dict[str, Any]:
    """Normalised ``{owner, counter}`` witness from a persisted/mapped cycle."""
    return {"owner": str(cycle.get("owner") or ""),
            "counter": int(cycle.get("counter") or 0)}


def _recorded_witness(step: dict) -> dict[str, Any] | None:
    """The witness an applied step wrote (``None`` for a legacy step)."""
    after = step.get("after")
    if not isinstance(after, dict) or "counter" not in after:
        return None
    return _cycle_witness(after)


def _drop_step_conflict(step: dict, valid_date: date) -> str | None:
    """Is this drop step's own generation still the live one?"""
    identity = str(step.get("identity") or "")
    if not _drop_has_identity(valid_date, identity):
        return "drop identity no longer present"
    recorded = _recorded_witness(step)
    live = _cycle_witness(_live_drop_cycle(valid_date, identity))
    if recorded is None:
        # Legacy step with no witness. Only an identity whose FIRST transition
        # is still live is provably this step's own effect; any higher counter
        # means a later cycle ran, so ownership cannot be proved.
        if live["counter"] <= 1:
            return None
        return ("drop ownership is not recorded for this step and a later "
                "skip/restore cycle ran — refusing to reverse")
    if live != recorded:
        return "a later skip/restore cycle owns this identity"
    return None


def _undrop_step_conflict(step: dict, valid_date: date) -> str | None:
    """Is this Restore step's own post-image (identity absent) still live?"""
    identity = str(step.get("identity") or "")
    if _drop_has_identity(valid_date, identity):
        return "drop identity is present again (a later skip owns it)"
    recorded = _recorded_witness(step)
    if recorded is None:
        # Legacy step: an absent identity is the pre-fix contract (still safe
        # to report clear); the reverse itself still refuses when a cycle was
        # recorded after it (see ``_reverse_step``).
        return None
    live = _cycle_witness(_live_drop_cycle(valid_date, identity))
    if live != recorded:
        return "a later skip/restore cycle superseded this restore"
    return None


def _exclusion_add_conflict(step: dict) -> str | None:
    dimension = step.get("dimension")
    if dimension == "todoist":
        if step.get("already_present"):
            return None
        entry_id = (step.get("entry") or {}).get("id")
        current = exclusion_settings.read_settings().settings
        if not any(row.id == entry_id for row in current.todoist):
            return "exclusion entry no longer present"
        return None
    if dimension == "capacities":
        if step.get("already_present"):
            return None
        current = capacities_settings.read_settings().settings
        if step.get("identity") not in current.excluded:
            return "exclusion entry no longer present"
        return None
    return f"unknown exclusion dimension {dimension!r}"


def step_conflict(
    step: dict,
    valid_date: date,
    *,
    todoist: Any = None,
    store: Any = None,
    capacities: Any = None,
) -> str | None:
    """The post-apply state check a batch undo runs BEFORE reversing ``step``.

    Returns ``None`` when the step's applied state still holds and reversing
    it is safe, or a human-readable reason string when a later edit changed
    that state (so reversing would clobber the edit). Raises
    :class:`StepNotReversible` for a kind with no safe check.

    ``capacities.complete`` returns ``None`` here: its independent guard is
    the fingerprint check inside ``restore_completion``, which refuses a
    changed source itself during the reverse.
    """
    kind = step.get("kind")
    if kind not in CONFLICT_CHECKED_KINDS:
        raise StepNotReversible(
            f"no safe conflict check for step kind {kind!r}")
    if kind == "todoist.close":
        if todoist is None:
            return "todoist surface unavailable"
        task = todoist.get_task(step["task_id"])
        if not (task.get("is_completed") or task.get("completed_at")):
            return "task is no longer closed"
        return None
    if kind == "todoist.unassign":
        if todoist is None:
            return "todoist surface unavailable"
        task = todoist.get_task(step["task_id"])
        after_due = ((step.get("after") or {}).get("task") or {}).get("due")
        if task.get("due") != after_due:
            return "task due changed after unassign"
        return None
    if kind == "capacities.complete":
        return None
    if kind == "runstate.drop":
        return _drop_step_conflict(step, valid_date)
    if kind == "runstate.undrop":
        return _undrop_step_conflict(step, valid_date)
    return _exclusion_add_conflict(step)


# ---------------------------------------------------------------------------
# Public engine
# ---------------------------------------------------------------------------

# Verbs whose repeated request is a new cycle rather than a retry: they
# toggle one date-scoped membership, so the constant idempotency key alone
# cannot tell a retry from a re-skip after a restore.
CYCLE_VERBS = frozenset({"drop_from_plan", "restore_today"})


def _cycle_effect_holds(action: dict, valid_date: date) -> bool:
    """True when the run-state still shows this action's own effect.

    Used by the Drop/Restore duplicate check: the earlier action answers a
    repeat request only while its generation (or its restored state) is still
    live. Any other action keeps the plain idempotency-key contract.
    """
    for step in action.get("steps") or []:
        kind = step.get("kind")
        if kind == "runstate.drop":
            return _drop_step_conflict(step, valid_date) is None
        if kind == "runstate.undrop":
            return _undrop_step_conflict(step, valid_date) is None
    return False


def apply_action(
    valid_date: date,
    verb: str,
    target_name: str,
    args: dict | None = None,
    *,
    todoist: Any = None,
    store: Any = None,
    capacities: Any = None,
    now: datetime | None = None,
    batch_id: str | None = None,
) -> dict[str, Any]:
    """Apply one runtime verb to one plan item; returns the journal entry.

    Journal-write-ahead: the entry (with planned steps) is persisted BEFORE
    the first write, updated after apply/compensation, so a crash mid-action
    always leaves an inspectable record.

    ``batch_id`` (B3) is an optional tag: when supplied, the journal entry
    carries it so a batch apply can address the actions it created. Unbatched
    actions keep their existing shape (no ``batch_id`` key).
    """
    args = args or {}
    if verb not in VERBS:
        raise RuntimeActionError(f"unknown verb {verb!r}")

    with runstate.day_lock(valid_date):
        journal = load_journal(valid_date)
        key = idempotency_key(valid_date, verb, target_name, args)
        if verb in CYCLE_VERBS:
            # Drop/Restore are repeated legitimately: the SAME request is a
            # duplicate only while its own effect still holds. The newest
            # matching action decides — a stale historic one is never
            # resurrected as the answer to a new cycle.
            for existing in reversed(journal["actions"]):
                if (existing.get("idempotency_key") == key
                        and existing.get("status") in ("applied", "partial")):
                    if _cycle_effect_holds(existing, valid_date):
                        dup = dict(existing)
                        dup["duplicate"] = True
                        return dup
                    break
        else:
            for existing in journal["actions"]:
                if (existing.get("idempotency_key") == key
                        and existing.get("status") in ("applied", "partial")):
                    dup = dict(existing)
                    dup["duplicate"] = True
                    return dup

        # Resolution runs inside the lock and AFTER the duplicate check, so a
        # repeated restore_today (whose dropped entry is already gone) still
        # reads as a duplicate of its applied action rather than a refusal.
        # Dropped rows are absent from the digest index, so restore_today
        # resolves from the dropped list by identity instead of the manifest.
        target = (resolve_dropped_target(valid_date, target_name)
                  if verb == "restore_today"
                  else resolve_target(valid_date, target_name))
        steps = plan_steps(verb, target, args, valid_date)
        _check_surfaces(steps, todoist, store, capacities)

        action: dict[str, Any] = {
            "id": _new_action_id(valid_date, journal),
            "date": str(valid_date),
            "verb": verb,
            "target": target,
            "args": args,
            "idempotency_key": key,
            "status": "pending",
            "steps": steps,
            "created_at": _now_iso(now),
            "finished_at": None,
            "undone_at": None,
            "error": None,
        }
        if batch_id is not None:
            action["batch_id"] = batch_id
        journal["actions"].append(action)
        _write_journal(valid_date, journal)

        try:
            for step in steps:
                _apply_step(
                    step, valid_date, todoist, store, capacities
                )
                _write_journal(valid_date, journal)
            action["status"] = "applied"
        except Exception as exc:  # noqa: BLE001 — every failure is journaled
            action["error"] = str(exc)
            applied = [s for s in steps if s.get("applied")]
            comp_failed = False
            for step in reversed(applied):
                try:
                    _reverse_step(
                        step, valid_date, todoist, store, capacities
                    )
                    step["compensated"] = True
                except Exception as comp_exc:  # noqa: BLE001
                    step["compensation_error"] = str(comp_exc)
                    comp_failed = True
            if not applied:
                action["status"] = "failed"
            elif comp_failed:
                action["status"] = "partial"
            else:
                action["status"] = "compensated"
        action["finished_at"] = _now_iso(now)
        _write_journal(valid_date, journal)
        return action


def undo_action(
    valid_date: date,
    action_id: str,
    *,
    todoist: Any = None,
    store: Any = None,
    capacities: Any = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Reverse one previously applied action from its exact before-images."""
    with runstate.day_lock(valid_date):
        journal = load_journal(valid_date)
        action = find_action(journal, action_id)
        if action is None:
            raise RuntimeActionError(f"unknown action {action_id!r}")
        if action.get("status") != "applied":
            raise RuntimeActionError(
                f"action {action_id!r} is not undoable "
                f"(status {action.get('status')!r})")
        steps = [s for s in action["steps"] if s.get("applied")]
        _check_surfaces(steps, todoist, store, capacities)
        undo_failed = False
        for step in reversed(steps):
            try:
                _reverse_step(
                    step, valid_date, todoist, store, capacities
                )
            except Exception as exc:  # noqa: BLE001
                step["undo_error"] = str(exc)
                undo_failed = True
        action["status"] = "undo_failed" if undo_failed else "undone"
        action["undone_at"] = _now_iso(now)
        _write_journal(valid_date, journal)
        if undo_failed:
            raise RuntimeActionError(
                f"undo of {action_id!r} failed on one or more steps — "
                "journal holds per-step errors")
        return action


# ---------------------------------------------------------------------------
# KTD1/KTD2: journal-derived Unassigned rows and Re-assign
# ---------------------------------------------------------------------------
#
# TDTB keeps the rows it unassigned for today in the journal rather than in a
# second state store. The journal is already dated, so the list expires at
# rollover by construction, and it records every path that unassigns — the
# Source menu, a batch-of-one and a multi-row batch share the same verb.


def _applied_unassign_step(action: dict[str, Any]) -> dict[str, Any] | None:
    """The ``todoist.unassign`` step an applied unassign action really ran.

    A Todoist-less unassign (a calendar drop) has no such step, and a failed,
    compensated or undone action keeps its step but is not a live exclusion,
    so neither is listed.
    """
    if action.get("verb") != "unassign":
        return None
    if action.get("status") != "applied":
        return None
    for step in action.get("steps") or []:
        if not isinstance(step, dict):
            continue
        if step.get("kind") == "todoist.unassign" and step.get("applied"):
            return step
    return None


def _unassigned_row(action: dict[str, Any],
                    step: dict[str, Any]) -> dict[str, Any] | None:
    """The KTD2 wire row for one applied Todoist unassign."""
    task_id = str(step.get("task_id") or "").strip()
    if not task_id:
        return None
    before_task = ((step.get("before") or {}).get("task") or {})
    due = before_task.get("due") or {}
    target = action.get("target") or {}
    row: dict[str, Any] = {
        "identity": f"todoist:{task_id}",
        "name": str(target.get("name") or before_task.get("content") or ""),
        "action_id": str(action.get("id") or ""),
        "source": "todoist",
        "recurring": bool(due.get("is_recurring")),
    }
    minutes = target.get("minutes")
    if minutes is None:
        duration = before_task.get("duration")
        if (isinstance(duration, int)
                and str(before_task.get("duration_unit") or "minute")
                == "minute"):
            minutes = duration
    try:
        if minutes is not None and int(minutes) > 0:
            row["minutes"] = int(minutes)
    except (TypeError, ValueError):
        pass
    return row


def unassigned_today(valid_date: date) -> list[dict[str, Any]]:
    """KTD2: today's Unassigned Todoist rows, derived from the journal.

    One row per identity, newest applied unassign wins. Undone entries drop
    out, so a Re-assign empties the row here without any extra bookkeeping.
    """
    rows: dict[str, dict[str, Any]] = {}
    for action in load_journal(valid_date).get("actions") or []:
        if not isinstance(action, dict):
            continue
        step = _applied_unassign_step(action)
        if step is None:
            continue
        row = _unassigned_row(action, step)
        if row is None:
            continue
        rows.pop(row["identity"], None)
        rows[row["identity"]] = row
    return list(rows.values())


def _newest_unassign_for_identity(
    valid_date: date, identity: str,
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """The newest applied Todoist unassign of ``identity`` in today's journal."""
    journal = load_journal(valid_date)
    for action in reversed(journal.get("actions") or []):
        if not isinstance(action, dict):
            continue
        step = _applied_unassign_step(action)
        if step is None:
            continue
        if f"todoist:{step.get('task_id')}" != identity:
            continue
        return action, step
    return None


def reassign(
    valid_date: date,
    identity: str,
    *,
    todoist: Any = None,
    store: Any = None,
    capacities: Any = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """KTD1: re-assign a Todoist row by reversing its own journaled unassign.

    The ``assign`` verb is deliberately NOT used: it no-ops on a recurring
    task, it flattens a datetime due to "today", and it leaves the earlier
    unassign ``applied``, which would make the next unassign a silent
    duplicate. Undo restores the exact before-image instead, and an ``undone``
    action lets the next unassign run as a real second write.

    A due date edited in Todoist after the unassign is refused here with the
    batch-undo conflict reason, before anything is written.
    """
    key = str(identity or "").strip()
    if not key:
        raise RuntimeActionError("reassign requires a Todoist identity")
    found = _newest_unassign_for_identity(valid_date, key)
    if found is None:
        raise RuntimeActionError(
            f"{key!r}: no unassigned Todoist row in today's journal")
    action, step = found
    if todoist is None:
        raise RuntimeActionError("surface unavailable: todoist")
    conflict = step_conflict(step, valid_date, todoist=todoist, store=store,
                             capacities=capacities)
    if conflict:
        raise RuntimeActionError(conflict)
    # undo_action takes the day lock itself — never call it while holding it.
    return undo_action(
        valid_date, str(action["id"]),
        todoist=todoist, store=store, capacities=capacities, now=now,
    )
