"""Read-only drift detection between today's committed blocks and Calendar.

Reports how today's live Calendar has drifted from what TDTB committed today
(GitHub issue asing44/TDTB#19, part A — backend only). It never writes,
adopts, repairs, or re-commits anything: the only Calendar access is one
read query and the inventory read the ownership scope already needs.

Data source is today's run-state ``commit_ledger`` — read directly and by ID
the same way ``bake_in_run._ledger_todoist_ids`` reads its Todoist surface
(never ``gather.load_runstate``, which reads the strictly-prior note):

- ``surfaces.calendar.touched`` maps artifact name -> event id for every
  calendar artifact the run created **or matched as a no-op**. That map is
  the committed-artifact set: an id with no live event is provably
  ``missing``.
- ``estimation[]`` rows (surface ``calendar``, op ``create``/``update``,
  ``planned_duration_min`` > 0) carry the intent ``time`` (HH:MM) that
  ``commit._plan_calendar`` set from the manifest. The end was never stored,
  so it is reconstructed exactly as the commit path computed it:
  ``end = time + planned_duration_min``. Only such rows can produce a
  ``moved`` verdict. A row known only by id (a no-op match, or any row
  without a positive interval) is compared for EXIST/ABSENT only and is never
  charged a moved interval it never recorded.

``compare`` is deliberately pure — plain data in, report out, no I/O — so the
matching rules are unit-testable without EventKit. The ``/calendar-drift``
route wires today's ledger and a single read-only Calendar query around it.
No EventKit write call is reachable from this module.
"""
from __future__ import annotations

import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent / "gather"))

import commit  # noqa: E402  (_hhmm_to_dt — identical parse to _plan_calendar)
import runstate  # noqa: E402
import tdtb_gather as gather  # noqa: E402

STATUS_OK = "ok"
STATUS_CALENDAR_UNAVAILABLE = "calendar-unavailable"
REASON_NO_COMMIT = "no commit today"

#: op values whose estimation row carries a usable committed interval. A
#: ``noop`` row is deliberately absent from the ledger's estimation list
#: (see orchestrate.py), so it stays id-only.
_INTERVAL_OPS = ("create", "update")


def read_ledger(day: date) -> dict[str, Any] | None:
    """Today's ``commit_ledger`` dict from the dated run-state note, or None.

    Reads the exact-date note directly (like ``bake_in_run._ledger_todoist_ids``
    and ``orchestrate.py``); a missing note, unparseable JSON block, or absent
    ledger all degrade to None rather than raising.
    """
    path = runstate.runstate_read_path(day)
    if not path.exists():
        return None
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    data = gather._extract_json_block(text)
    if not data:
        return None
    ledger = data.get("commit_ledger")
    return ledger if isinstance(ledger, dict) else None


def ledger_touched(ledger: dict[str, Any] | None) -> dict[str, str]:
    """``{name: event_id}`` for the calendar artifacts the run committed.

    Empty when there is no ledger, no calendar surface, or no touched map —
    the caller treats that as "no commit today". Pure.
    """
    surface = ((ledger or {}).get("surfaces") or {}).get("calendar")
    if not isinstance(surface, dict):
        return {}
    touched = surface.get("touched")
    if not isinstance(touched, dict):
        return {}
    return {
        str(name): str(eid)
        for name, eid in touched.items()
        if name and eid
    }


def _committed_intervals(
    day: date, ledger: dict[str, Any] | None
) -> dict[str, tuple[datetime, datetime]]:
    """``{name: (start, end)}`` reconstructed from estimation rows. Pure."""
    out: dict[str, tuple[datetime, datetime]] = {}
    for row in (ledger or {}).get("estimation") or []:
        if not isinstance(row, dict):
            continue
        if row.get("surface") != "calendar" or row.get("op") not in _INTERVAL_OPS:
            continue
        try:
            duration = float(row.get("planned_duration_min") or 0)
        except (TypeError, ValueError):
            continue
        if duration <= 0:
            continue
        name = row.get("name")
        start = commit._hhmm_to_dt(day, row.get("time"))
        if not name or start is None:
            continue
        out[str(name)] = (start, start + timedelta(minutes=duration))
    return out


def _iso(value: Any) -> str | None:
    """Naive-local ISO-8601 seconds for a datetime/date/str; None otherwise."""
    if isinstance(value, datetime):
        return value.replace(microsecond=0).isoformat()
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day).isoformat()
    if isinstance(value, str):
        return value
    return None


def _same_time(left: Any, right: Any) -> bool:
    if left is None or right is None:
        return left is None and right is None
    return _iso(left) == _iso(right)


def _on_day(value: Any, day: date) -> bool:
    if isinstance(value, datetime):
        return value.date() == day
    if isinstance(value, str) and len(value) >= 10:
        return value[:10] == day.isoformat()
    return False


def _interval(interval: tuple[datetime, datetime]) -> dict[str, str | None]:
    start, end = interval
    return {"start": _iso(start), "end": _iso(end)}


def no_commit_report(day: date, checked_at: str) -> dict[str, Any]:
    """The empty report for a day with no committed calendar artifacts. Pure."""
    return {
        "status": STATUS_OK,
        "day": str(day),
        "hasCommit": False,
        "reason": REASON_NO_COMMIT,
        "moved": [],
        "missing": [],
        "added": [],
        "checkedAt": checked_at,
    }


def unavailable_report(
    day: date, checked_at: str, reason: str
) -> dict[str, Any]:
    """The 200-body degrade for an unreadable Calendar. Pure."""
    return {
        "status": STATUS_CALENDAR_UNAVAILABLE,
        "day": str(day),
        "hasCommit": True,
        "reason": reason,
        "moved": [],
        "missing": [],
        "added": [],
        "checkedAt": checked_at,
    }


def compare(
    day: date,
    ledger: dict[str, Any] | None,
    live_events: list[dict[str, Any]] | None,
    own_calendar_ids: set[str] | None,
    checked_at: str = "",
) -> dict[str, Any]:
    """Compare the day's committed calendar artifacts against live events.

    Pure: no I/O, no store, no writing. ``ledger`` is the ``commit_ledger``
    dict (or None); ``live_events`` are the day-window query rows;
    ``own_calendar_ids`` is the resolved set of TDTB-owned write calendars
    (``shadow._own_calendar_ids``). All-day events are ignored.
    """
    touched = ledger_touched(ledger)
    if not touched:
        return no_commit_report(day, checked_at)

    intervals = _committed_intervals(day, ledger)
    live_by_id: dict[str, dict[str, Any]] = {}
    for event in live_events or []:
        if isinstance(event, dict) and event.get("id") is not None:
            live_by_id[str(event["id"])] = event

    touched_ids = set(touched.values())
    own = {str(c) for c in (own_calendar_ids or set())}

    moved: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    for name, event_id in touched.items():
        live = live_by_id.get(event_id)
        interval = intervals.get(name)
        if live is None:
            missing.append({
                "name": name,
                "id": event_id,
                "committed": _interval(interval) if interval else None,
            })
            continue
        if interval is None:
            # Id-only artifact (a no-op match or a zero-duration row): the
            # ledger never recorded an interval, so EXIST is the whole
            # verdict — never invent a moved interval for it.
            continue
        if (
            _same_time(live.get("start"), interval[0])
            and _same_time(live.get("end"), interval[1])
        ):
            continue
        moved.append({
            "name": name,
            "id": event_id,
            "committed": _interval(interval),
            "live": {"start": _iso(live.get("start")), "end": _iso(live.get("end"))},
        })

    # Added: an owned-looking live event on the logical day that the ledger
    # does not name. Ownership is the same surface runtime_actions.py offers
    # move_resize/delete on — a TDTB-owned Blocks calendar event — resolved
    # here as membership in the configured own-write calendar ids
    # (shadow._own_calendar_ids). An ordinary foreign calendar event is NEVER
    # drift: TDTB only ever writes to its own Block calendars, so anything
    # outside that set is the operator's own entry, not a change to our plan.
    added: list[dict[str, Any]] = []
    for event in live_events or []:
        if not isinstance(event, dict) or event.get("all_day"):
            continue
        event_id = event.get("id")
        if event_id is None or str(event_id) in touched_ids:
            continue
        if str(event.get("calendar_id") or "") not in own:
            continue
        if not _on_day(event.get("start"), day):
            continue
        added.append({
            "name": event.get("title"),
            "id": str(event_id),
            "live": {"start": _iso(event.get("start")), "end": _iso(event.get("end"))},
        })

    return {
        "status": STATUS_OK,
        "day": str(day),
        "hasCommit": True,
        "reason": None,
        "moved": moved,
        "missing": missing,
        "added": added,
        "checkedAt": checked_at,
    }
