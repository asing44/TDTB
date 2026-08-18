"""T12j calendar-class capacity contract.

Calendar events retain their source identity. Only true fixed events enter the
fixed segment; work meetings share one work envelope with the configured
allotment, and ignored calendars remain visible while costing zero.
"""
from __future__ import annotations

from datetime import datetime

import external_sources as ext
import main as main_mod

TODAY = datetime(2026, 7, 14).date()


def _event(
    name: str,
    start: str,
    end: str,
    capacity_class: str,
    *,
    skip_today: bool = False,
) -> dict:
    return {
        "Block": name,
        "Start": start,
        "End": end,
        "source": "calendar",
        "calendar_id": f"CAL-{name}",
        "calendar_title": name,
        "capacity_class": capacity_class,
        **({"skip_today": True} if skip_today else {}),
    }


def _frame(busy: list[dict], work_minutes: int = 90):
    return main_mod._capacity_frame(
        {
            "Defaults": {
                "eod": "20:00",
                "anchor.round_to_minutes": 15,
                "buffering.off_pct": 0,
            },
            "Anchored Lifestyle Blocks": [],
        },
        {"anchor": "08:00", "eod": "20:00", "buffering": "off"},
        busy,
        {"est_minutes": 0, "done": 0, "outstanding": 0},
        {"effective_allotment_minutes": work_minutes},
        now=datetime(2026, 7, 14, 8, 0),
    )


def test_work_meetings_union_with_the_work_allotment_without_double_counting():
    busy = [
        _event("Trinoor A", "09:00", "10:00", "work"),
        _event("Trinoor B", "09:30", "11:00", "work"),
        _event("Dentist", "11:00", "11:30", "fixed"),
        _event("Session focus", "12:00", "13:00", "ignored"),
    ]
    _time, cap = _frame(busy, work_minutes=90)

    assert cap.fixed == 1
    assert cap.mint == 4  # 09:00–11:00 union, not 2 + 3 event blocks
    assert cap.work_busy == 4
    assert cap.work_overflow == 1


def test_work_allotment_remains_the_work_total_when_meetings_fit_inside_it():
    _time, cap = _frame(
        [_event("Work meeting", "09:30", "10:20", "work")],
        work_minutes=240,
    )
    assert cap.fixed == 0
    assert cap.mint == 8
    assert cap.work_busy == 2
    assert cap.work_overflow == 0


def test_work_union_is_clipped_to_the_active_frame_and_skip_today_is_independent():
    busy = [
        _event("Elapsed", "06:00", "07:00", "work"),
        _event("Crosses anchor", "07:30", "08:30", "work"),
        _event("Dismissed", "09:00", "11:00", "work", skip_today=True),
    ]
    _time, cap = _frame(busy, work_minutes=0)
    assert cap.work_busy == 1
    assert cap.mint == 1
    assert cap.work_overflow == 1
    assert cap.fixed == 0


def test_ignored_calendar_neither_pins_capacity_nor_counts_as_fixed():
    _time, cap = _frame(
        [_event("Session focus", "18:00", "19:00", "ignored")],
        work_minutes=0,
    )
    assert cap.fixed == 0
    assert cap.mint == 0


def test_quarantined_unknown_calendar_excluded_from_capacity():
    # Frozen contract 17: an explicitly quarantined calendar stays excluded
    # and must not count as fixed or work capacity (FEEDBACK-27 kept the
    # exclusion for configured quarantined titles; unlisted defaults fixed).
    _time, cap = _frame(
        [_event("Mystery", "09:00", "11:00", "quarantined")],
        work_minutes=90,
    )
    assert cap.fixed == 0
    assert cap.work_busy == 0
    assert cap.mint == 3  # allotment untouched by the quarantined row


# ---------------------------------------------------------------------------
# FEEDBACK-04 — fixture-title classification follows explicit config rules
# ---------------------------------------------------------------------------

class _FakeCalendar:
    def __init__(self, title: str, identifier: str):
        self.title = title
        self.identifier = identifier


class _FakeStore:
    """Minimal EventStore fake for fetch_calendar_busy: authorized, one
    inventory of calendars, one day of events (dicts, like EventKit rows)."""

    def __init__(self, events: list[dict], calendars: list[_FakeCalendar]):
        self._events = events
        self._calendars = calendars

    def auth_status(self) -> str:
        return "authorized"

    def calendars(self) -> list[_FakeCalendar]:
        return self._calendars

    def query_events(self, start, end, calendar_ids):
        return self._events


class TestFeedback04FixtureClassification:
    """FF-CAL-01/FF-06 fixture titles (2026-08-14): Cooking, trivia, Steelers,
    and dinner must classify per EXPLICIT source/class rules, never by label
    guessing. Cooking is a configured fixed calendar; Trivia Night is a
    configured work calendar; Steelers Game is an EXPLICITLY quarantined
    Sports title (contract 17 exclusion kept — unlisted/unclassified timed
    calendars default fixed per FEEDBACK-27). Dinner is a config window, not
    a calendar event — no fixture event for it. Quarantined rows cost zero
    capacity and stay on the wire so the UI can explain why."""

    def _fixture(self) -> tuple[_FakeStore, dict]:
        calendars = [
            _FakeCalendar("Cooking", "CAL-COOK"),
            _FakeCalendar("Trivia Night", "CAL-TRIV"),
            _FakeCalendar("Sports", "CAL-SPORT"),
        ]
        cfg = {
            "calendar_ids": {},
            "calendar_capacity_classes": [
                {"BusyCal title": "Cooking", "Class": "fixed"},
                {"BusyCal title": "Trivia Night", "Class": "work"},
                {"BusyCal title": "Sports", "Class": "quarantined"},
            ],
        }
        events = [
            {"title": "Cooking", "calendar_id": "CAL-COOK",
             "start": datetime(2026, 7, 14, 20, 30), "end": datetime(2026, 7, 14, 21, 0)},
            {"title": "Trivia Night", "calendar_id": "CAL-TRIV",
             "start": datetime(2026, 7, 14, 19, 0), "end": datetime(2026, 7, 14, 20, 0)},
            {"title": "Steelers Game", "calendar_id": "CAL-SPORT",
             "start": datetime(2026, 7, 14, 20, 0), "end": datetime(2026, 7, 14, 22, 0)},
        ]
        return _FakeStore(events, calendars), cfg

    def test_fixture_titles_classify_per_explicit_rules(self):
        store, cfg = self._fixture()
        blocks, warnings = ext.fetch_calendar_busy(store, cfg, TODAY)
        assert warnings == []
        assert {b["Block"]: b["capacity_class"] for b in blocks} == {
            "Cooking": "fixed",
            "Trivia Night": "work",
            "Steelers Game": "quarantined",
        }

    def test_fixture_capacity_counts_fixed_and_work_once_and_never_quarantined(self):
        store, cfg = self._fixture()
        blocks, _ = ext.fetch_calendar_busy(store, cfg, TODAY)
        _time, cap = main_mod._capacity_frame(
            {
                "Defaults": {
                    "eod": "22:00",
                    "anchor.round_to_minutes": 15,
                    "buffering.off_pct": 0,
                },
                "Anchored Lifestyle Blocks": [],
            },
            {"anchor": "08:00", "eod": "22:00", "buffering": "off"},
            blocks,
            {"est_minutes": 0, "done": 0, "outstanding": 0},
            {"effective_allotment_minutes": 90},
            now=datetime(2026, 7, 14, 8, 0),
        )
        # Cooking only: fixed accounted exactly once (30 min → 1 block).
        assert cap.fixed == 1
        # Trivia Night inside the work envelope: 2 busy blocks, 0 overflow —
        # the quarantined Steelers 20:00-22:00 overlap must NOT inflate work.
        assert cap.work_busy == 2
        assert cap.work_overflow == 0
        assert cap.mint == 3  # max(allotment 3, work_busy 2)
