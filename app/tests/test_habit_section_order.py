"""test_habit_section_order.py — habits follow their Todoist section order.

The habits list (Set up day card + Habits drawer rows) is rendered in the
artifact's ``habits.items`` array order, untouched by the frontend. The
producer therefore orders those items after the habits' Todoist project: the
``habits.sections`` list (``todoist_find-sections`` order) first, then each
task's in-section position (``childOrder``). With no section/order data the
fetched order is preserved exactly.
"""
from __future__ import annotations

from datetime import date, timedelta

import producer_rules as pr

TODAY = date(2026, 10, 16)
LOGICAL_DAY = TODAY.isoformat()
FUTURE = (TODAY + timedelta(days=1)).isoformat()


def _habit_entry(tasks, completed=(), **knobs) -> dict:
    entry = {
        "status": "ok",
        "read_at": "2026-10-16T08:00:00-05:00",
        "tasks": list(tasks),
        "completed": list(completed),
    }
    entry.update(knobs)
    return entry


def _ids(entry, **kwargs) -> list[str]:
    result = pr.compute_habit_summary(entry, **kwargs)
    return [item["id"] for item in result.get("items", [])]


# ---------------------------------------------------------------------------
# Section order
# ---------------------------------------------------------------------------

class TestHabitSectionOrder:
    def test_sections_out_of_input_order_are_sorted(self):
        entry = _habit_entry(
            [
                {"id": "b", "content": "B", "sectionId": "s2", "childOrder": 0},
                {"id": "a", "content": "A", "sectionId": "s1", "childOrder": 0},
            ],
            sections=[{"id": "s1", "name": "One"}, {"id": "s2", "name": "Two"}],
        )
        assert _ids(entry) == ["a", "b"]

    def test_explicit_section_order_beats_list_position(self):
        entry = _habit_entry(
            [
                {"id": "a", "content": "A", "sectionId": "s1", "childOrder": 0},
                {"id": "b", "content": "B", "sectionId": "s2", "childOrder": 0},
            ],
            sections=[
                {"id": "s1", "name": "One", "order": 1},
                {"id": "s2", "name": "Two", "order": 0},
            ],
        )
        assert _ids(entry) == ["b", "a"]

    def test_within_section_child_order_decides(self):
        entry = _habit_entry(
            [
                {"id": "late", "sectionId": "s1", "childOrder": 50},
                {"id": "mid", "sectionId": "s1", "childOrder": 25},
                {"id": "early", "sectionId": "s1", "childOrder": 10},
            ],
            sections=[{"id": "s1", "name": "One"}],
        )
        assert _ids(entry) == ["early", "mid", "late"]

    def test_missing_child_order_falls_back_to_input_position(self):
        entry = _habit_entry(
            [
                {"id": "y", "sectionId": "s1", "childOrder": 5},
                {"id": "x", "sectionId": "s1"},  # input position 1 -> before 5
            ],
            sections=[{"id": "s1", "name": "One"}],
        )
        assert _ids(entry) == ["x", "y"]

    def test_sectionless_tasks_come_first(self):
        entry = _habit_entry(
            [
                {"id": "s", "sectionId": "s1", "childOrder": 0},
                {"id": "none"},
            ],
            sections=[{"id": "s1", "name": "One"}],
        )
        assert _ids(entry) == ["none", "s"]

    def test_null_section_id_is_sectionless(self):
        entry = _habit_entry(
            [
                {"id": "s", "sectionId": "s1", "childOrder": 0},
                {"id": "null", "sectionId": None},
            ],
            sections=[{"id": "s1", "name": "One"}],
        )
        assert _ids(entry) == ["null", "s"]

    def test_unknown_section_ranks_after_known_ones(self):
        entry = _habit_entry(
            [
                {"id": "unk", "sectionId": "sX", "childOrder": 0},
                {"id": "known", "sectionId": "s1", "childOrder": 0},
                {"id": "none"},
            ],
            sections=[{"id": "s1", "name": "One"}],
        )
        assert _ids(entry) == ["none", "known", "unk"]

    def test_snake_and_camel_case_are_both_read(self):
        entry = _habit_entry(
            [
                {"id": "a", "section_id": "s2", "child_order": "1"},
                {"id": "b", "sectionId": "s1", "childOrder": 0},
            ],
            sections=[{"id": "s1", "name": "One"}, {"id": "s2", "name": "Two"}],
        )
        assert _ids(entry) == ["b", "a"]

    def test_string_and_int_ids_match(self):
        entry = _habit_entry(
            [
                {"id": "a", "sectionId": 7, "childOrder": "0"},
                {"id": "b", "sectionId": "7", "childOrder": "1"},
            ],
            sections=[{"id": "7", "name": "Seven"}],
        )
        assert _ids(entry) == ["a", "b"]


# ---------------------------------------------------------------------------
# Degradation — no usable section data leaves the fetched order intact
# ---------------------------------------------------------------------------

class TestHabitSectionOrderDegradation:
    def test_no_section_or_order_data_keeps_input_order(self):
        entry = _habit_entry(
            [
                {"id": "c", "content": "C"},
                {"id": "a", "content": "A"},
                {"id": "b", "content": "B"},
            ]
        )
        assert _ids(entry) == ["c", "a", "b"]

    def test_non_list_sections_degrade_to_input_order(self):
        entry = _habit_entry(
            [
                {"id": "b", "content": "B", "sectionId": "s1"},
                {"id": "a", "content": "A", "sectionId": "s1"},
            ],
            sections="not-a-list",
        )
        assert _ids(entry) == ["b", "a"]

    def test_sections_missing_ids_degrade_to_input_order(self):
        entry = _habit_entry(
            [
                {"id": "b", "content": "B", "sectionId": "s1"},
                {"id": "a", "content": "A", "sectionId": "s1"},
            ],
            sections=[{"name": "No id"}, {"name": "Also no id"}],
        )
        assert _ids(entry) == ["b", "a"]

    def test_malformed_section_entries_are_skipped(self):
        entry = _habit_entry(
            [
                {"id": "b", "content": "B", "sectionId": "s2"},
                {"id": "a", "content": "A", "sectionId": "s1"},
            ],
            sections=[{"name": "No id"}, "junk", {"id": "s1"}, {"id": "s2"}],
        )
        assert _ids(entry) == ["a", "b"]


# ---------------------------------------------------------------------------
# Filtering — ordering is over ALL tasks; only future rows are dropped
# ---------------------------------------------------------------------------

class TestHabitSectionOrderWithFiltering:
    def test_future_rows_are_removed_and_completed_rows_stay_in_order(self):
        entry = _habit_entry(
            [
                {"id": "future", "content": "Future", "sectionId": "s1",
                 "childOrder": 0, "dueDate": FUTURE},
                {"id": "done", "content": "Done", "sectionId": "s1",
                 "childOrder": 5, "dueDate": LOGICAL_DAY},
                {"id": "a", "content": "A", "sectionId": "s1",
                 "childOrder": 10, "dueDate": LOGICAL_DAY},
                {"id": "b", "content": "B", "sectionId": "s2",
                 "childOrder": 0, "dueDate": LOGICAL_DAY},
            ],
            completed=[{"task_id": "done"}],
            sections=[{"id": "s1", "name": "One"}, {"id": "s2", "name": "Two"}],
        )
        # Completion is not read: the completed row is still a habit, so it
        # keeps its section order and only the future row is gone.
        assert _ids(entry, logical_day=LOGICAL_DAY) == ["done", "a", "b"]
