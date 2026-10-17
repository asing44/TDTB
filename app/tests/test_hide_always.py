"""test_hide_always.py — journaled "Hide always" (slice B2).

``hide_always`` is the journaled, undoable replacement for the unjournaled
controller-side ``alwaysExcludeTodoist`` / ``alwaysExcludeCapacitiesObject``
path. It plans exactly one step, ``exclusions.add``:

* a Todoist row appends a persistent Todoist exclusion (id + title) to
  ``exclusion_settings``;
* a Capacities row adds its canonical stable identity to the
  ``capacities_settings`` exclusion set.

Both stores keep their own revision guard: read current, write with that
revision, retry ONCE on a stale-revision conflict, then fail cleanly. Reverse
removes ONLY the entry this action added (identity-scoped) and leaves every
other exclusion untouched; a repeat is a duplicate/no-op.

These tests are pure-local: no Todoist / Calendar / Capacities client is
injected, and only the two app-owned settings stores plus runstate are read.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

import capacities_assignment as ca
import capacities_settings as cs
import exclusion_settings as es
import runstate
import runtime_actions as ra
import tag_exclusions
import todoist_exclusions

TODAY = date(2026, 8, 13)
CAP_A = "capacities:space-1:custom-project:obj-1"
CAP_OTHER = "capacities:space-1:custom-project:obj-2"


def two_item_manifest() -> list[dict]:
    """One committed Todoist row and one committed Capacities row."""
    return [
        {"step": "B", "system": "todoist", "action": "schedule", "name": "Alpha",
         "id_or_path": "task-a", "time": "10:00", "duration_min": 30,
         "routing": "\u2014"},
        {"step": "B", "system": "capacities", "action": "schedule",
         "name": "Cap Alpha", "id_or_path": "obj-1", "time": "11:00",
         "duration_min": 30, "routing": "\u2014", "identity": CAP_A},
    ]


@pytest.fixture()
def plan() -> None:
    runstate.write_runstate(
        TODAY, runstate.build_runstate({"plan_manifest": two_item_manifest()}))


def _seed_todoist(entry_id: str = "task-b", name: str = "Beta") -> None:
    es.save_settings(expected_revision=0, todoist=[
        es.TodoistExclusion(id=entry_id, name=name,
                            added_at="2026-08-13T00:00:00Z")])


def _seed_capacities(identity: str = CAP_OTHER) -> None:
    cs.save_settings(
        expected_revision=0,
        native_task_auto=cs.NativeTaskAutoPolicy(),
        excluded={identity},
    )


def _todoist_ids() -> set[str]:
    return {entry.id for entry in es.read_settings().settings.todoist}


# ---------------------------------------------------------------------------
# Verb catalogue + step planning (pure)
# ---------------------------------------------------------------------------

def test_hide_always_is_a_known_verb():
    assert "hide_always" in ra.VERBS
    assert "hide_always" in ra.STAGING_VERBS


def test_plan_steps_emits_exclusions_add_for_todoist():
    steps = ra.plan_steps(
        "hide_always",
        {"phase": "committed", "name": "Alpha", "todoist_id": "task-a"},
        {}, TODAY)
    assert [s["kind"] for s in steps] == ["exclusions.add"]
    assert steps[0]["dimension"] == "todoist"
    assert steps[0]["entry"] == {"id": "task-a", "name": "Alpha"}


def test_plan_steps_emits_exclusions_add_for_capacities():
    steps = ra.plan_steps(
        "hide_always",
        {"phase": "committed", "name": "Cap Alpha", "capacities_id": "obj-1",
         "identity": CAP_A, "source": "capacities"},
        {}, TODAY)
    assert [s["kind"] for s in steps] == ["exclusions.add"]
    assert steps[0]["dimension"] == "capacities"
    assert steps[0]["identity"] == CAP_A


def test_hide_of_a_row_with_no_hideable_source_is_a_clean_refusal():
    with pytest.raises(ra.RuntimeActionError):
        ra.plan_steps(
            "hide_always", {"phase": "committed", "name": "Morning Routine"},
            {}, TODAY)


# ---------------------------------------------------------------------------
# Todoist: hide / reverse is identity-scoped and leaves others intact
# ---------------------------------------------------------------------------

class TestTodoistHide:
    def test_hide_then_reverse_removes_only_its_entry(self, plan: None):
        _seed_todoist()
        action = ra.apply_action(TODAY, "hide_always", "Alpha")
        assert action["status"] == "applied"
        assert _todoist_ids() == {"task-a", "task-b"}

        ra.undo_action(TODAY, action["id"])
        assert _todoist_ids() == {"task-b"}

    def test_hide_is_journaled_under_the_new_verb(self, plan: None):
        action = ra.apply_action(TODAY, "hide_always", "Alpha")
        entry = next(a for a in ra.load_journal(TODAY)["actions"]
                     if a["id"] == action["id"])
        assert entry["verb"] == "hide_always"
        assert entry["steps"][0]["kind"] == "exclusions.add"
        assert entry["steps"][0]["applied"] is True

    def test_repeat_hide_is_a_duplicate_no_op(self, plan: None):
        first = ra.apply_action(TODAY, "hide_always", "Alpha")
        again = ra.apply_action(TODAY, "hide_always", "Alpha")
        assert again.get("duplicate") is True
        assert again["id"] == first["id"]
        assert _todoist_ids() == {"task-a"}

    def test_hide_of_already_excluded_entry_keeps_it_after_reverse(self, plan: None):
        _seed_todoist(entry_id="task-a", name="Alpha")
        action = ra.apply_action(TODAY, "hide_always", "Alpha")
        assert action["status"] == "applied"
        assert _todoist_ids() == {"task-a"}
        ra.undo_action(TODAY, action["id"])
        assert _todoist_ids() == {"task-a"}

    def test_hidden_todoist_row_leaves_plan_inputs_filtering(self, plan: None):
        ra.apply_action(TODAY, "hide_always", "Alpha")
        policy = tag_exclusions.ExclusionPolicy.from_read(es.read_settings())
        kept, _pool, report = todoist_exclusions.apply_todoist_exclusions(
            [{"source": "todoist", "todoist_id": "task-a", "name": "Alpha"}],
            [], policy)
        assert kept == []
        assert report["excluded_counts"]["assigned"] == 1


# ---------------------------------------------------------------------------
# Capacities: hide / reverse is identity-scoped and leaves others intact
# ---------------------------------------------------------------------------

class TestCapacitiesHide:
    def test_hide_then_reverse_removes_only_its_entry(self, plan: None):
        _seed_capacities()
        action = ra.apply_action(TODAY, "hide_always", "Cap Alpha")
        assert action["status"] == "applied"
        assert cs.read_settings().settings.excluded == {CAP_A, CAP_OTHER}

        ra.undo_action(TODAY, action["id"])
        assert cs.read_settings().settings.excluded == {CAP_OTHER}

    def test_repeat_hide_is_a_duplicate_no_op(self, plan: None):
        first = ra.apply_action(TODAY, "hide_always", "Cap Alpha")
        again = ra.apply_action(TODAY, "hide_always", "Cap Alpha")
        assert again.get("duplicate") is True
        assert again["id"] == first["id"]
        assert cs.read_settings().settings.excluded == {CAP_A}

    def test_hide_of_already_excluded_entry_keeps_it_after_reverse(self, plan: None):
        _seed_capacities(identity=CAP_A)
        action = ra.apply_action(TODAY, "hide_always", "Cap Alpha")
        assert action["status"] == "applied"
        ra.undo_action(TODAY, action["id"])
        assert cs.read_settings().settings.excluded == {CAP_A}

    def test_hidden_capacities_row_leaves_plan_inputs_filtering(self, plan: None):
        ra.apply_action(TODAY, "hide_always", "Cap Alpha")
        settings = cs.read_settings().settings.to_assignment_settings()
        decision = ca.evaluate_assignment(
            ca.AssignmentCandidate(identity=CAP_A, status_is_open=True),
            logical_day=TODAY, settings=settings)
        assert decision.mode == ca.AssignmentMode.EXCLUDED


# ---------------------------------------------------------------------------
# Revision/409 guard: retry exactly once, then fail cleanly
# ---------------------------------------------------------------------------

class TestRevisionGuard:
    def test_todoist_conflict_retried_once_then_succeeds(self, plan, monkeypatch):
        real_save = es.save_settings
        attempts: list[int] = []

        def flaky(**kwargs):
            attempts.append(kwargs["expected_revision"])
            if len(attempts) == 1:
                raise es.ExclusionSettingsConflictError("stale revision")
            return real_save(**kwargs)

        monkeypatch.setattr(es, "save_settings", flaky)
        action = ra.apply_action(TODAY, "hide_always", "Alpha")
        assert action["status"] == "applied"
        assert len(attempts) == 2
        assert _todoist_ids() == {"task-a"}

    def test_todoist_conflict_twice_is_a_clean_failure(self, plan, monkeypatch):
        calls: list[int] = []

        def always_conflict(**kwargs):
            calls.append(1)
            raise es.ExclusionSettingsConflictError("stale revision")

        monkeypatch.setattr(es, "save_settings", always_conflict)
        action = ra.apply_action(TODAY, "hide_always", "Alpha")
        assert action["status"] == "failed"
        assert len(calls) == 2
        assert es.read_settings().settings.todoist == ()
        assert es.read_settings().persisted is False

    def test_capacities_conflict_retried_once_then_succeeds(self, plan, monkeypatch):
        real_save = cs.save_settings
        attempts: list[int] = []

        def flaky(**kwargs):
            attempts.append(kwargs["expected_revision"])
            if len(attempts) == 1:
                raise cs.SettingsConflictError("stale revision")
            return real_save(**kwargs)

        monkeypatch.setattr(cs, "save_settings", flaky)
        action = ra.apply_action(TODAY, "hide_always", "Cap Alpha")
        assert action["status"] == "applied"
        assert len(attempts) == 2
        assert cs.read_settings().settings.excluded == {CAP_A}

    def test_capacities_conflict_twice_is_a_clean_failure(self, plan, monkeypatch):
        def always_conflict(**kwargs):
            raise cs.SettingsConflictError("stale revision")

        monkeypatch.setattr(cs, "save_settings", always_conflict)
        action = ra.apply_action(TODAY, "hide_always", "Cap Alpha")
        assert action["status"] == "failed"
        assert cs.read_settings().settings.excluded == frozenset()
        assert cs.read_settings().persisted is False
