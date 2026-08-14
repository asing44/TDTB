"""EventKit calendar bridge — TDTB app pilot.

Ported from the 2026-07-12 spike probe (proven: consent, enumerate,
write→readback→delete). Consent binds to the launching process; the pilot is
Terminal-launched so the grant is Terminal's (spec § 3.3, plan locked decision 4).

Layering: pure functions (`resolve_calendar_ids`, `assert_write_target`) take a
plain list of `CalendarInfo` and are unit-tested with fakes; the `EventStore`
class owns all pyobjc calls and is exercised only by the manual-verify gate.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

AUTH_STATUS = {0: "notDetermined", 1: "restricted", 2: "denied",
               3: "fullAccess", 4: "writeOnly"}
# Capacity classes: ``fixed``/``work`` count against capacity; ``ignored``
# costs zero but stays visible (own-write rows, user-dismissed calendars);
# ``quarantined`` is the default state of a KNOWN calendar title the user has
# not classified yet — excluded from capacity and planning until explicitly
# reviewed (frozen contract 17).
CAPACITY_CLASS_QUARANTINED = "quarantined"
CAPACITY_CLASSES = frozenset({"fixed", "work", "ignored", CAPACITY_CLASS_QUARANTINED})


@dataclass(frozen=True)
class CalendarInfo:
    title: str
    identifier: str
    writable: bool
    source: str


@dataclass(frozen=True)
class EventSpec:
    title: str
    start: datetime
    end: datetime
    calendar_id: str
    notes: str | None = None


class CalendarResolutionError(Exception):
    """A logical calendar name could not be resolved to a live calendar."""


class CalendarWriteError(Exception):
    """A write was attempted against an unresolved/read-only/unknown target."""


# ---------------------------------------------------------------- pure logic

def resolve_calendar_ids(
    title_map: dict[str, str], calendars: list[CalendarInfo]
) -> tuple[dict[str, str], list[str]]:
    """Map logical names -> live calendar identifiers by exact title match.

    Returns (resolved {logical: identifier}, failures [logical names]).
    Never guesses on a miss — the caller surfaces failures (skill Phase 0.1
    Step 4a semantics: propose a fix, don't silently auto-repair).
    """
    by_title: dict[str, CalendarInfo] = {}
    for cal in calendars:
        # first-writable-wins on duplicate titles (read-only dupes exist live,
        # e.g. two 'Birthdays' calendars)
        if cal.title not in by_title or (
            cal.writable and not by_title[cal.title].writable
        ):
            by_title[cal.title] = cal

    resolved: dict[str, str] = {}
    failures: list[str] = []
    for logical, title in title_map.items():
        cal = by_title.get(title)
        if cal is None:
            failures.append(logical)
        else:
            resolved[logical] = cal.identifier
    return resolved, failures


def normalize_title_map(raw: Any) -> dict[str, str]:
    """Normalize a config ``## Calendar Titles`` value into ``{logical: title}``.

    Accepts three shapes:
      - the list-of-rows shape ``config_reader`` emits
        (``[{"Logical name": ..., "BusyCal title": ..., "Role": ...}, ...]``),
      - a pre-built ``{logical: title}`` dict (tests / a degraded config path),
      - anything else, which normalizes to ``{}`` rather than raising — a
        malformed or absent config section degrades to "no calendars
        resolvable", never a crash.
    """
    if isinstance(raw, list):
        return {
            row["Logical name"]: row["BusyCal title"]
            for row in raw
            if isinstance(row, dict) and row.get("Logical name") and row.get("BusyCal title")
        }
    if isinstance(raw, dict):
        return dict(raw)
    return {}


def normalize_capacity_class_map(raw: Any) -> dict[str, str]:
    """Normalize ``## Calendar Capacity Classes`` to ``{title: class}``.

    Titles are matched exactly against the live EventKit calendar inventory.
    Invalid classes are dropped so the read path falls back to the
    ``quarantined`` default (or ``fixed`` for unidentified calendars) instead
    of inventing a fifth capacity behavior. ``quarantined`` is a recognized
    class so a reviewed-but-excluded title round-trips on the wire.
    """
    if isinstance(raw, list):
        pairs = (
            (
                row.get("BusyCal title") or row.get("Calendar title"),
                row.get("Class"),
            )
            for row in raw
            if isinstance(row, dict)
        )
    elif isinstance(raw, dict):
        pairs = raw.items()
    else:
        return {}

    out: dict[str, str] = {}
    for title, raw_class in pairs:
        title_s = str(title or "").strip()
        class_s = str(raw_class or "").strip().casefold()
        if title_s and class_s in CAPACITY_CLASSES:
            out[title_s] = class_s
    return out


def resolve_titles_to_ids(
    title_map: dict[str, str], calendars: list[CalendarInfo]
) -> tuple[dict[str, str], list[str]]:
    """Resolve to a TITLE-keyed map ``{display title: identifier}`` — the key
    shape ``commit.plan_writes`` / ``_plan_calendar`` consumes (manifest
    routing is always the display title, e.g. ``"⬜ Blocks"``, never the
    logical name, e.g. ``"blocks"``).

    Delegates the actual logical->id resolution to ``resolve_calendar_ids``,
    then re-keys the result by title using ``title_map``. A logical name that
    resolved but whose title has since gone missing from ``title_map`` (should
    not happen, but the mapping isn't atomic) is defensively dropped rather
    than raising or emitting a bogus key.

    Returns ``(resolved {title: identifier}, failures [logical names])`` — the
    same failures list ``resolve_calendar_ids`` produces, unchanged (failures
    are reported by logical name, the value the config table names them by).
    """
    resolved_by_logical, failures = resolve_calendar_ids(title_map, calendars)
    resolved_by_title: dict[str, str] = {}
    for logical, identifier in resolved_by_logical.items():
        title = title_map.get(logical)
        if title is None:  # defensive: resolved-out but title vanished
            continue
        resolved_by_title[title] = identifier
    return resolved_by_title, failures


def assert_write_target(
    calendar_id: str, calendars: list[CalendarInfo]
) -> CalendarInfo:
    """Pre-write ID assertion (plan T4/T14 invariant): the target must exist
    in the live set and be writable, else CalendarWriteError. Run immediately
    before EVERY event write."""
    for cal in calendars:
        if cal.identifier == calendar_id:
            if not cal.writable:
                raise CalendarWriteError(
                    f"calendar {cal.title!r} ({calendar_id}) is read-only"
                )
            return cal
    raise CalendarWriteError(f"calendar id {calendar_id} not in live set")


def _as_datetime(value: Any) -> datetime | None:
    """Coerce a read-back event time to datetime for comparison (None when
    unparseable — the caller records a fail-closed mismatch rather than
    guessing)."""
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            text = value[:-1] + "+00:00" if value.endswith("Z") else value
            return datetime.fromisoformat(text)
        except ValueError:
            return None
    return None


def event_readback_mismatches(
    spec: EventSpec, event: dict[str, Any]
) -> list[dict[str, Any]]:
    """Compare an intended calendar write against a read-back event.

    Returns a list of structured mismatch records — EMPTY only when the full
    write identity (title, calendar_id, start, end, duration) matches. Each
    record carries canonical intent/live values (ISO datetimes, minutes) so
    machine consumers never parse display text (FEEDBACK-26, the calendar
    analog of the Todoist due reading). A live value that cannot be compared
    (missing/unparseable time) fails closed as an explicit record.

    ``spec`` is the intended write (``EventSpec``); ``event`` is what the
    store read back (the EventStoreLike ``get_event`` shape).
    """
    mismatches: list[dict[str, Any]] = []

    if (event.get("title") or "") != spec.title:
        mismatches.append({
            "field": "title",
            "intent": spec.title,
            "live": event.get("title"),
        })
    if (event.get("calendar_id") or "") != spec.calendar_id:
        mismatches.append({
            "field": "calendar_id",
            "intent": spec.calendar_id,
            "live": event.get("calendar_id"),
        })

    live_start = _as_datetime(event.get("start"))
    live_end = _as_datetime(event.get("end"))
    if live_start is None or live_end is None:
        mismatches.append({
            "field": "interval",
            "intent": {"start": spec.start.isoformat(), "end": spec.end.isoformat()},
            "live": {
                "start": event.get("start"),
                "end": event.get("end"),
            },
        })
        return mismatches

    if live_start != spec.start:
        mismatches.append({
            "field": "start",
            "intent": spec.start.isoformat(),
            "live": live_start.isoformat(),
        })
    if live_end != spec.end:
        mismatches.append({
            "field": "end",
            "intent": spec.end.isoformat(),
            "live": live_end.isoformat(),
        })
    intent_duration = int((spec.end - spec.start).total_seconds() // 60)
    live_duration = int((live_end - live_start).total_seconds() // 60)
    if live_duration != intent_duration:
        mismatches.append({
            "field": "duration_min",
            "intent": intent_duration,
            "live": live_duration,
        })
    return mismatches


# ------------------------------------------------------------- objc layer

class EventStore:
    """Thin wrapper over EKEventStore. Instantiate once per process."""

    def __init__(self) -> None:
        from EventKit import EKEventStore  # lazy: tests never import pyobjc
        self._ek = __import__("EventKit")
        self._store = EKEventStore.alloc().init()

    # -- auth ------------------------------------------------------------
    def auth_status(self) -> str:
        status = self._ek.EKEventStore.authorizationStatusForEntityType_(
            self._ek.EKEntityTypeEvent
        )
        return AUTH_STATUS.get(status, f"unknown({status})")

    def request_access(self, timeout_s: float = 30.0) -> bool:
        """Request full access, pumping the runloop until the completion
        fires (probe-proven pattern). Returns granted."""
        from Foundation import NSDate, NSRunLoop

        done = threading.Event()
        result = {"granted": False}

        def handler(granted: bool, error: object) -> None:
            result["granted"] = bool(granted)
            done.set()

        store = self._store
        if store.respondsToSelector_(b"requestFullAccessToEventsWithCompletion:"):
            store.requestFullAccessToEventsWithCompletion_(handler)
        else:  # pre-Sonoma
            store.requestAccessToEntityType_completion_(
                self._ek.EKEntityTypeEvent, handler
            )

        loop = NSRunLoop.currentRunLoop()
        deadline = time.time() + timeout_s
        while not done.is_set() and time.time() < deadline:
            loop.runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(0.1))
        return result["granted"]

    # -- read ------------------------------------------------------------
    def calendars(self) -> list[CalendarInfo]:
        cals = self._store.calendarsForEntityType_(self._ek.EKEntityTypeEvent)
        return [
            CalendarInfo(
                title=c.title(),
                identifier=c.calendarIdentifier(),
                writable=bool(c.allowsContentModifications()),
                source=c.source().title() if c.source() else "?",
            )
            for c in cals
        ]

    def query_events(
        self, start: datetime, end: datetime, calendar_ids: list[str] | None = None
    ) -> list[dict]:
        """Events in [start, end); optionally scoped to calendar identifiers.
        Times returned as aware-naive local datetimes via NSDate timestamps."""
        from Foundation import NSDate

        ek = self._ek
        store = self._store
        ns_start = NSDate.dateWithTimeIntervalSince1970_(start.timestamp())
        ns_end = NSDate.dateWithTimeIntervalSince1970_(end.timestamp())
        cal_objs = None
        if calendar_ids is not None:
            wanted = set(calendar_ids)
            cal_objs = [
                c for c in store.calendarsForEntityType_(ek.EKEntityTypeEvent)
                if c.calendarIdentifier() in wanted
            ]
        pred = store.predicateForEventsWithStartDate_endDate_calendars_(
            ns_start, ns_end, cal_objs
        )
        events = store.eventsMatchingPredicate_(pred) or []
        return [
            {
                "id": e.eventIdentifier(),
                "title": e.title(),
                "start": datetime.fromtimestamp(
                    e.startDate().timeIntervalSince1970()
                ),
                "end": datetime.fromtimestamp(e.endDate().timeIntervalSince1970()),
                "calendar_id": e.calendar().calendarIdentifier(),
                "all_day": bool(e.isAllDay()),
            }
            for e in events
        ]

    # -- write -----------------------------------------------------------
    def create_event(self, spec: EventSpec) -> str:
        """Create an event; returns its eventIdentifier. Asserts the target
        against the live calendar set immediately before writing."""
        from Foundation import NSDate

        ek = self._ek
        live = self.calendars()
        assert_write_target(spec.calendar_id, live)

        target = next(
            c for c in self._store.calendarsForEntityType_(ek.EKEntityTypeEvent)
            if c.calendarIdentifier() == spec.calendar_id
        )
        ev = ek.EKEvent.eventWithEventStore_(self._store)
        ev.setTitle_(spec.title)
        ev.setStartDate_(NSDate.dateWithTimeIntervalSince1970_(spec.start.timestamp()))
        ev.setEndDate_(NSDate.dateWithTimeIntervalSince1970_(spec.end.timestamp()))
        ev.setCalendar_(target)
        if spec.notes:
            ev.setNotes_(spec.notes)

        ok, err = self._store.saveEvent_span_error_(ev, ek.EKSpanThisEvent, None)
        if not ok:
            raise CalendarWriteError(f"save failed: {err}")
        return ev.eventIdentifier()

    def get_event(self, event_id: str) -> dict | None:
        e = self._store.eventWithIdentifier_(event_id)
        if e is None:
            return None
        return {
            "id": e.eventIdentifier(),
            "title": e.title(),
            "start": datetime.fromtimestamp(e.startDate().timeIntervalSince1970()),
            "end": datetime.fromtimestamp(e.endDate().timeIntervalSince1970()),
            "calendar_id": e.calendar().calendarIdentifier(),
        }

    def update_event(
        self, event_id: str, start: datetime | None = None,
        end: datetime | None = None,
    ) -> bool:
        """T20: retime/resize an owned event in place. Returns False when the
        event no longer exists; raises CalendarWriteError on a failed save."""
        from Foundation import NSDate

        ek = self._ek
        e = self._store.eventWithIdentifier_(event_id)
        if e is None:
            return False
        if start is not None:
            e.setStartDate_(NSDate.dateWithTimeIntervalSince1970_(start.timestamp()))
        if end is not None:
            e.setEndDate_(NSDate.dateWithTimeIntervalSince1970_(end.timestamp()))
        ok, err = self._store.saveEvent_span_error_(e, ek.EKSpanThisEvent, None)
        if not ok:
            raise CalendarWriteError(f"update failed: {err}")
        return True

    def delete_event(self, event_id: str) -> bool:
        ek = self._ek
        e = self._store.eventWithIdentifier_(event_id)
        if e is None:
            return False
        ok, err = self._store.removeEvent_span_error_(e, ek.EKSpanThisEvent, None)
        if not ok:
            raise CalendarWriteError(f"delete failed: {err}")
        return True


# ----------------------------------------------------- shared store (T14)

_shared_store: EventStore | None = None
_shared_store_lock = threading.Lock()


def shared_store() -> EventStore:
    """Process-wide EventStore singleton (T14 Option A, 2026-07-23).

    The 2026-07-23 live commit failed closed (HTTP 422) because a second
    EKEventStore instance saw zero calendars while the GET-proven
    /plan-inputs store in the same process was healthy. One shared
    construction eliminates that divergence class: preflight, shadow, and
    live planning must all call this instead of EventStore().
    """
    global _shared_store
    if _shared_store is None:
        with _shared_store_lock:
            if _shared_store is None:
                _shared_store = EventStore()
    return _shared_store


def has_writable_calendar(store: Any) -> bool:
    """True when *store* exposes at least one writable calendar.

    Absent, degraded, or erroring stores are all False — callers fail
    closed before planning any calendar write.
    """
    if store is None:
        return False
    try:
        return any(getattr(c, "writable", False) for c in store.calendars())
    except Exception:  # noqa: BLE001 — a store that can't enumerate is degraded
        return False
