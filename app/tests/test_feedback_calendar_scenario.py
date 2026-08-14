"""FEEDBACK-05 (2026-08-14): end-to-end regression fixture for the reported
calendar scenario.

The live screenshot day carried Cooking at 20:30, dinner, trivia, Steelers,
assigned rows overlapping those blocks, and only 2 available blocks versus 16
included blocks. This module pins the BACKEND half of that reported shape with
deterministic synthetic fixtures and fake providers:

- every named event classifies per explicit source rules (Cooking -> fixed,
  DCP Bark Bar trivia -> work, Steelers vs Packers -> quarantined, Foods
  Dinner -> config window, never a calendar event);
- capacity accounting: Cooking counted exactly once, trivia inside the work
  envelope, quarantined Steelers cost ZERO and never become a hidden wall;
- non-permeable calendar walls hard-reject assigned overlap, while the dinner
  window stays permeable;
- over-assignment (2 available vs 16 included) produces explicit diagnostics
  (never-bump names the missing row; a pinned-row overlap is a named warning),
  never silent placement;
- the final sequence is chronological (merge_immutable_rows sorts; validation
  rejects descending order deterministically);
- zero calendar source writer calls — the fake store records only read verbs.
"""
from __future__ import annotations

from datetime import datetime

import external_sources as ext
import main as main_mod
from sequence import (
    merge_immutable_rows,
    validate_sequence,
)

TODAY = datetime(2026, 7, 14).date()


def _event(
    name: str,
    start: str,
    end: str,
    capacity_class: str,
) -> dict:
    return {
        "Block": name,
        "Start": start,
        "End": end,
        "source": "calendar",
        "calendar_id": f"CAL-{name}",
        "calendar_title": name,
        "capacity_class": capacity_class,
    }


def _seq_row(id_: str, start: str, end: str, zone: str = "any"):
    return {"id": id_, "start": start, "end": end, "zone": zone}


def _item(id_: str, zone: str = "any"):
    return {"id": id_, "zone": zone}


class _FakeCalendar:
    def __init__(self, title: str, identifier: str):
        self.title = title
        self.identifier = identifier


class _FakeStore:
    """Read-only EventStore fake that records every method call so the test
    can assert zero calendar WRITER verbs ever fire. Only read methods exist —
    a write attempt fails loudly instead of silently mutating."""

    def __init__(self, events: list[dict], calendars: list[_FakeCalendar]):
        self._events = events
        self._calendars = calendars
        self.calls: list[str] = []

    def auth_status(self) -> str:
        self.calls.append("auth_status")
        return "authorized"

    def calendars(self) -> list[_FakeCalendar]:
        self.calls.append("calendars")
        return self._calendars

    def query_events(self, start, end, calendar_ids):
        self.calls.append("query_events")
        return self._events

    # Calendar writers are intentionally ABSENT. A production path that tries
    # to mutate the source would raise AttributeError here — the regression
    # proof that the fixture pipeline never calls one.
    def save_event(self):
        raise AssertionError("calendar writer save_event must never be called")

    def update_event(self):
        raise AssertionError("calendar writer update_event must never be called")

    def delete_event(self):
        raise AssertionError("calendar writer delete_event must never be called")


class TestFeedback05FixtureClassification:
    """FF-CAL-01/FF-06 fixture titles from the reported screenshot: Cooking,
    dinner, trivia, Steelers. Classification follows EXPLICIT config rules;
    a known-but-unclassified title stays quarantined (contract 17)."""

    def _fixture(self) -> tuple[_FakeStore, dict]:
        calendars = [
            _FakeCalendar("Cooking", "CAL-COOK"),
            _FakeCalendar("DCP Bark Bar trivia", "CAL-TRIV"),
            _FakeCalendar("Sports", "CAL-SPORT"),
        ]
        cfg = {
            "calendar_ids": {},
            "calendar_capacity_classes": [
                {"BusyCal title": "Cooking", "Class": "fixed"},
                {"BusyCal title": "DCP Bark Bar trivia", "Class": "work"},
            ],
        }
        events = [
            {"title": "Cooking", "calendar_id": "CAL-COOK",
             "start": datetime(2026, 7, 14, 20, 30), "end": datetime(2026, 7, 14, 21, 0)},
            {"title": "DCP Bark Bar trivia", "calendar_id": "CAL-TRIV",
             "start": datetime(2026, 7, 14, 19, 0), "end": datetime(2026, 7, 14, 20, 0)},
            {"title": "Steelers vs Packers", "calendar_id": "CAL-SPORT",
             "start": datetime(2026, 7, 14, 20, 0), "end": datetime(2026, 7, 14, 22, 0)},
        ]
        return _FakeStore(events, calendars), cfg

    def test_fixture_events_classify_per_explicit_rules(self):
        store, cfg = self._fixture()
        blocks, warnings = ext.fetch_calendar_busy(store, cfg, TODAY)
        assert warnings == []
        assert {b["Block"]: b["capacity_class"] for b in blocks} == {
            "Cooking": "fixed",
            "DCP Bark Bar trivia": "work",
            "Steelers vs Packers": "quarantined",
        }
        # fetch_calendar_busy is a READ pipeline: only read verbs may fire.
        assert store.calls == ["auth_status", "calendars", "query_events"]

    def test_dinner_is_a_config_window_never_a_calendar_event(self):
        # Foods Dinner lives in config as a permeable window, not as a
        # calendar busy row — the fixture must not invent an event for it.
        store, cfg = self._fixture()
        blocks, _ = ext.fetch_calendar_busy(store, cfg, TODAY)
        assert "Foods Dinner" not in [b["Block"] for b in blocks]


class TestFeedback05CapacityAccounting:
    """The reported day: only 2 available blocks versus 16 included blocks.
    Cooking counts as fixed exactly once; DCP trivia rides the work envelope;
    quarantined Steelers costs zero and never inflates work or mint."""

    def _frame(self, busy: list[dict], *, extra_selected: int = 0):
        return main_mod._capacity_frame(
            {
                "Defaults": {
                    "eod": "23:00",
                    "anchor.round_to_minutes": 15,
                    "buffering.off_pct": 0,
                },
                "Anchored Lifestyle Blocks": [
                    {"Block": "Suds", "Type": "hard", "Start": "17:45",
                     "End": "18:15", "Duration": "30m"},
                    {"Block": "Foods Dinner", "Type": "window", "Start": "18:00",
                     "End": "20:30", "Duration": "60m"},
                    {"Block": "Night Routine", "Type": "hard", "Start": "23:00",
                     "End": "23:45", "Duration": "45m"},
                ],
            },
            {"anchor": "17:15", "eod": "23:00", "buffering": "off"},
            busy,
            {"est_minutes": 30, "done": 0, "outstanding": 1},
            {"effective_allotment_minutes": 60},
            now=datetime(2026, 7, 14, 17, 10),
            extra_selected_blocks=extra_selected,
        )

    def test_two_available_vs_sixteen_included_is_explicit_overassignment(self):
        busy = [
            _event("Cooking", "20:30", "21:00", "fixed"),
            _event("DCP Bark Bar trivia", "19:00", "20:00", "work"),
            _event("Steelers vs Packers", "20:00", "22:00", "quarantined"),
        ]
        _time, cap = self._frame(busy, extra_selected=16)
        assert cap.total == 11  # 17:15 -> 23:00 frame
        assert cap.fixed == 1  # Cooking only; Steelers quarantined never fixed
        assert cap.available_for_selection == 2
        assert cap.selected == 16
        assert cap.free == -14
        assert cap.overassigned is True
        assert "over" in cap.remaining

    def test_quarantined_steelers_costs_zero_capacity(self):
        busy = [
            _event("Cooking", "20:30", "21:00", "fixed"),
            _event("DCP Bark Bar trivia", "19:00", "20:00", "work"),
            # Steelers 20:00-22:00 overlaps trivia's end AND Cooking's start —
            # it must not inflate fixed, work_busy, or mint.
            _event("Steelers vs Packers", "20:00", "22:00", "quarantined"),
        ]
        _time, cap = self._frame(busy)
        assert cap.fixed == 1
        assert cap.work_busy == 2  # trivia only, 60 min
        assert cap.work_overflow == 0
        assert cap.mint == 2  # max(allotment 2, work_busy 2); Steelers adds 0


class TestFeedback05WallSafety:
    """No assigned row may overlap a non-permeable calendar wall. Cooking
    (fixed) and DCP trivia (work) are hard walls; the Foods Dinner config
    window stays permeable; quarantined Steelers is no wall at all."""

    WALLS = [
        _event("Cooking", "20:30", "21:00", "fixed"),
        _event("DCP Bark Bar trivia", "19:00", "20:00", "work"),
        _event("Steelers vs Packers", "20:00", "22:00", "quarantined"),
        {"Block": "Foods Dinner", "Type": "window", "Start": "18:00",
         "End": "20:30", "Duration": "60m"},
    ]

    FRAME = {"anchor": "17:15", "effective_eod": "23:00"}

    def _res(self, rows, assigned=None):
        assigned = assigned if assigned is not None else [_item(r["id"]) for r in rows]
        return validate_sequence(
            {"sequence": rows}, assigned, list(self.WALLS), {},
            time_frame=self.FRAME,
        )

    def test_row_over_cooking_fixed_wall_is_hard_rejection(self):
        r = self._res([_seq_row("t1", "20:35", "21:05")])
        assert r.ok is False
        assert any("Cooking" in e and "overlap" in e.lower()
                   for e in r.hard_errors)

    def test_row_over_trivia_work_wall_is_hard_rejection(self):
        r = self._res([_seq_row("t1", "19:15", "19:45")])
        assert r.ok is False
        assert any("DCP Bark Bar trivia" in e for e in r.hard_errors)

    def test_row_over_quarantined_steelers_is_no_wall(self):
        # Steelers 20:00-22:00 is quarantined (contract 17): a row may cross
        # it — it is excluded from planning, never a hidden hard wall. The
        # row ends exactly at Cooking's start so only Steelers is crossed.
        r = self._res([_seq_row("t1", "20:00", "20:30")])
        assert r.ok is True
        assert r.hard_errors == []
        assert not any(w.get("rule") == "unexpected_overlap"
                       for w in r.warnings)

    def test_row_inside_dinner_window_is_soft_advisory_only(self):
        # Foods Dinner is a permeable window: placement inside is a soft flag,
        # never a hard wall.
        r = self._res([_seq_row("t1", "18:30", "19:00")])
        assert r.ok is True
        assert any(w.get("kind") == "window-overlap" or "window" in str(w)
                   for w in r.warnings)

    def test_row_beside_all_walls_validates_clean(self):
        # The honest non-overlapping outcome for the reported evening.
        r = self._res([
            _seq_row("t1", "17:30", "18:30"),
            _seq_row("t2", "20:05", "20:30"),
            _seq_row("t3", "21:00", "21:30"),
        ])
        assert r.ok is True
        assert r.hard_errors == []


class TestFeedback05ExplicitInfeasibility:
    """Over-assignment must be LOUD: a staged sequence that omits a row the
    overflow could not place is rejected with never-bump naming the row; a row
    overlapping an immutable pin is a named warning. Nothing is silently
    accepted."""

    def test_missing_assigned_row_is_never_silent(self):
        proposal = {"sequence": [_seq_row("Magic Mirror", "10:00", "11:30")]}
        result = validate_sequence(
            proposal,
            [_item("Magic Mirror"), _item("Log hours")],
            [], {},
        )
        assert result.ok is False
        assert any("Log hours" in e and "missing" in e
                   for e in result.hard_errors)

    def test_row_over_immutable_pinned_row_is_named_warning(self):
        anchored = [{
            "Block": "Log hours", "Type": "hard",
            "Start": "17:15", "End": "17:45", "pinned": True,
        }]
        proposal = {"sequence": [_seq_row("t1", "17:20", "17:50")]}
        result = validate_sequence(proposal, [_item("t1")], anchored, {})
        assert result.ok is True
        assert any(
            "Log hours" in str(w) and w.get("rule") == "unexpected_overlap"
            for w in result.warnings
        )


class TestFeedback05Chronology:
    """The final sequence must be chronological: merge_immutable_rows sorts by
    (start, id) and validation rejects any descending final order."""

    def test_merge_immutable_rows_is_chronological_after_replace(self):
        model_rows = [
            {"id": "Deep Work", "start": "23:15", "end": "23:45", "zone": None},
            {"id": "Log hours", "start": "23:15", "end": "23:45", "zone": None},
        ]
        immutable = [{
            "id": "Log hours", "start": "17:15", "end": "17:45", "zone": "any",
        }]
        merged = merge_immutable_rows(model_rows, immutable)
        assert [row["id"] for row in merged] == ["Log hours", "Deep Work"]
        assert [row["start"] for row in merged] == ["17:15", "23:15"]
        assert merged[0] is immutable[0]

    def test_descending_final_sequence_rejected_deterministically(self):
        proposal = {
            "sequence": [
                _seq_row("Pick up prescription", "23:15", "23:45"),
                _seq_row("Log hours", "17:15", "17:45"),
            ]
        }
        result = validate_sequence(
            proposal,
            [_item("Pick up prescription"), _item("Log hours")],
            [], {},
        )
        assert result.ok is False
        assert any(
            "chronological" in e.lower()
            and "Log hours" in e
            and "17:15" in e
            and "23:15" in e
            for e in result.hard_errors
        )


class TestFeedback05ZeroCalendarWriters:
    """The whole reported-scenario pipeline (read + classify + capacity +
    validate) must never touch a calendar writer — the fake store's ledger
    records exactly the three read verbs and nothing else."""

    def test_reported_scenario_pipeline_records_only_read_calls(self):
        calendars = [
            _FakeCalendar("Cooking", "CAL-COOK"),
            _FakeCalendar("DCP Bark Bar trivia", "CAL-TRIV"),
            _FakeCalendar("Sports", "CAL-SPORT"),
        ]
        cfg = {
            "calendar_ids": {},
            "calendar_capacity_classes": [
                {"BusyCal title": "Cooking", "Class": "fixed"},
                {"BusyCal title": "DCP Bark Bar trivia", "Class": "work"},
            ],
        }
        events = [
            {"title": "Cooking", "calendar_id": "CAL-COOK",
             "start": datetime(2026, 7, 14, 20, 30), "end": datetime(2026, 7, 14, 21, 0)},
            {"title": "DCP Bark Bar trivia", "calendar_id": "CAL-TRIV",
             "start": datetime(2026, 7, 14, 19, 0), "end": datetime(2026, 7, 14, 20, 0)},
            {"title": "Steelers vs Packers", "calendar_id": "CAL-SPORT",
             "start": datetime(2026, 7, 14, 20, 0), "end": datetime(2026, 7, 14, 22, 0)},
        ]
        store = _FakeStore(events, calendars)
        blocks, _ = ext.fetch_calendar_busy(store, cfg, TODAY)
        main_mod._capacity_frame(
            {
                "Defaults": {
                    "eod": "23:00",
                    "anchor.round_to_minutes": 15,
                    "buffering.off_pct": 0,
                },
                "Anchored Lifestyle Blocks": [],
            },
            {"anchor": "17:15", "eod": "23:00", "buffering": "off"},
            blocks,
            {"est_minutes": 0, "done": 0, "outstanding": 0},
            {"effective_allotment_minutes": 60},
            now=datetime(2026, 7, 14, 17, 10),
            extra_selected_blocks=16,
        )
        # Only reads may ever fire; a writer method does not exist on the fake
        # and would raise if a production path reached for one.
        assert store.calls == ["auth_status", "calendars", "query_events"]
