"""test_runtime_actions.py — T20 reversible runtime item actions.

Journal core + verb planners + apply/compensate/undo engine, all against
fake clients and a tmp vault. No network, no EventKit, no real writes.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

import runstate
import runtime_actions as ra
from capacities_adapter import CompletionOutcome


TODAY = date(2026, 7, 25)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class FakeTodoist:
    def __init__(self, tasks: dict[str, dict] | None = None) -> None:
        self.tasks = tasks or {}
        self.calls: list[tuple] = []

    def get_task(self, task_id: str) -> dict:
        self.calls.append(("get_task", task_id))
        if task_id not in self.tasks:
            raise KeyError(task_id)
        return json.loads(json.dumps(self.tasks[task_id]))

    def close_task(self, task_id: str) -> None:
        self.calls.append(("close_task", task_id))
        self.tasks[task_id]["is_completed"] = True

    def reopen_task(self, task_id: str) -> None:
        self.calls.append(("reopen_task", task_id))
        self.tasks[task_id]["is_completed"] = False

    def delete_task(self, task_id: str) -> None:
        self.calls.append(("delete_task", task_id))
        del self.tasks[task_id]

    def create_task(self, content: str, project_id: str | None = None, **fields) -> dict:
        self.calls.append(("create_task", content))
        new_id = f"new-{len(self.tasks) + 1}"
        task = {"id": new_id, "content": content, "project_id": project_id, **fields}
        self.tasks[new_id] = task
        return json.loads(json.dumps(task))

    def update_task(self, task_id: str, **fields) -> dict:
        self.calls.append(("update_task", task_id, fields))
        self.tasks[task_id].update(fields)
        return json.loads(json.dumps(self.tasks[task_id]))

    def reschedule_task(self, task_id: str, due_string: str) -> dict:
        self.calls.append(("reschedule_task", task_id, due_string))
        self.tasks[task_id]["due"] = {"string": due_string}
        return json.loads(json.dumps(self.tasks[task_id]))

    def reschedule_task_datetime(self, task_id: str, due_datetime: str) -> dict:
        self.calls.append(("reschedule_task_datetime", task_id, due_datetime))
        self.tasks[task_id]["due"] = {"datetime": due_datetime}
        return json.loads(json.dumps(self.tasks[task_id]))


class FakeStore:
    def __init__(self, events: dict[str, dict] | None = None) -> None:
        self.events = events or {}
        self.calls: list[tuple] = []

    def get_event(self, event_id: str) -> dict | None:
        self.calls.append(("get_event", event_id))
        ev = self.events.get(event_id)
        return json.loads(json.dumps(ev)) if ev else None

    def delete_event(self, event_id: str) -> bool:
        self.calls.append(("delete_event", event_id))
        return self.events.pop(event_id, None) is not None

    def create_event(self, spec) -> str:
        self.calls.append(("create_event", spec.title))
        new_id = f"ev-new-{len(self.events) + 1}"
        self.events[new_id] = {
            "id": new_id, "title": spec.title,
            "start": spec.start.isoformat(), "end": spec.end.isoformat(),
            "calendar_id": spec.calendar_id, "notes": spec.notes,
        }
        return new_id

    def update_event(self, event_id: str, start=None, end=None) -> bool:
        self.calls.append(("update_event", event_id, start, end))
        ev = self.events.get(event_id)
        if ev is None:
            return False
        if start is not None:
            ev["start"] = start.isoformat()
        if end is not None:
            ev["end"] = end.isoformat()
        return True


class FakeCapacities:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.closed = False

    def complete(self, object_id: str, *, expected_fingerprint: str | None = None):
        self.calls.append(("complete", object_id, expected_fingerprint))
        return CompletionOutcome(
            "completed",
            object_id,
            completion_property="status",
            before_property={"type": "label", "label": [{"id": "open"}]},
            after_fingerprint="after-fingerprint",
        )

    def restore_completion(
        self,
        object_id: str,
        *,
        completion_property,
        before_property,
        expected_fingerprint,
    ):
        self.calls.append((
            "restore",
            object_id,
            completion_property,
            before_property,
            expected_fingerprint,
        ))
        return CompletionOutcome("restored", object_id)

    def close(self) -> None:
        self.closed = True


# ---------------------------------------------------------------------------
# Vault + runstate fixtures
# ---------------------------------------------------------------------------

NOTE_TEXT = """---
type: [task]
status: in-progress
assigned: true
---

# Press

body stays byte-identical
"""

CAPTURE_TEXT = """---
type: [capture]
status: open
---

capture body
"""


def manifest_rows() -> list[dict]:
    return [
        {"step": "B", "system": "todoist", "action": "schedule", "name": "Press",
         "id_or_path": "task-1", "time": "16:00", "duration_min": 30, "routing": "—"},
        {"step": "E", "system": "calendar", "action": "create-event", "name": "Press",
         "id_or_path": "ev-1", "time": "16:00", "duration_min": 30, "routing": "Blocks"},
    ]


def legacy_vault_rows() -> list[dict]:
    """A pre-cutover manifest row for a vault note (V3: its verbs are gone)."""
    return [
        {"step": "C", "system": "vault", "action": "set-flag", "name": "Legacy",
         "id_or_path": "Projects/Press.md", "time": None, "duration_min": 0, "routing": "—"},
    ]


@pytest.fixture()
def vault(tmp_path: Path) -> Path:
    (tmp_path / "Projects").mkdir()
    (tmp_path / "Projects" / "Press.md").write_text(NOTE_TEXT, encoding="utf-8")
    (tmp_path / "Projects" / "Cap.md").write_text(CAPTURE_TEXT, encoding="utf-8")
    state = runstate.build_runstate(
        {"plan_manifest": manifest_rows() + legacy_vault_rows()})
    runstate.write_runstate(TODAY, state)
    return tmp_path


def fresh_clients() -> tuple[FakeTodoist, FakeStore]:
    todoist = FakeTodoist({
        "task-1": {"id": "task-1", "content": "Press", "project_id": "p1",
                   "is_completed": False,
                   "due": {"datetime": "2026-07-25T16:00:00"}},
    })
    store = FakeStore({
        "ev-1": {"id": "ev-1", "title": "Press", "start": "2026-07-25T16:00:00",
                 "end": "2026-07-25T16:30:00", "calendar_id": "cal-blocks",
                 "notes": None},
    })
    return todoist, store


# ---------------------------------------------------------------------------
# Journal core
# ---------------------------------------------------------------------------

class TestJournalCore:
    def test_empty_load(self, vault: Path):
        j = ra.load_journal(TODAY)
        assert j["valid_date"] == str(TODAY)
        assert j["actions"] == []

    def test_apply_appends_and_roundtrips(self, vault: Path):
        todoist, store = fresh_clients()
        action = ra.apply_action(
            TODAY, "complete", "Press", {}, todoist=todoist, store=store)
        assert action["status"] == "applied"
        j = ra.load_journal(TODAY)
        assert len(j["actions"]) == 1
        assert j["actions"][0]["id"] == action["id"]
        # journal file is valid JSON on disk
        raw = json.loads(ra.journal_path(TODAY).read_text())
        assert raw["actions"][0]["verb"] == "complete"

    def test_idempotent_repeat_returns_existing(self, vault: Path):
        todoist, store = fresh_clients()
        first = ra.apply_action(
            TODAY, "complete", "Press", {}, todoist=todoist, store=store)
        calls_after_first = list(todoist.calls)
        second = ra.apply_action(
            TODAY, "complete", "Press", {}, todoist=todoist, store=store)
        assert second["id"] == first["id"]
        assert second.get("duplicate") is True
        assert todoist.calls == calls_after_first  # no new writes

    def test_find_action_by_id(self, vault: Path):
        todoist, store = fresh_clients()
        action = ra.apply_action(
            TODAY, "complete", "Press", {}, todoist=todoist, store=store)
        j = ra.load_journal(TODAY)
        assert ra.find_action(j, action["id"])["verb"] == "complete"
        assert ra.find_action(j, "ra-nope") is None


# ---------------------------------------------------------------------------
# Verbs
# ---------------------------------------------------------------------------

class TestComplete:
    def test_complete_routes_a_capacities_item_through_the_journal(self, vault: Path):
        runstate.write_digest_index(TODAY, [{
            "name": "Ship project",
            "path": "capacities://space-1/object-1",
            "identity": "capacities:space-1:custom-project:object-1",
            "source": "capacities",
            "capacities_id": "object-1",
            "capacities_space_id": "space-1",
            "capacities_structure_id": "custom-project",
            "capacities_completion_supported": True,
            "source_fingerprint": "before-fingerprint",
        }])
        capacities = FakeCapacities()

        action = ra.apply_action(
            TODAY,
            "complete",
            "Ship project",
            {},
            capacities=capacities,
        )

        assert action["status"] == "applied"
        assert capacities.calls == [
            ("complete", "object-1", "before-fingerprint"),
        ]
        undone = ra.undo_action(
            TODAY, action["id"], capacities=capacities
        )
        assert undone["status"] == "undone"
        assert capacities.calls[-1][0:3] == (
            "restore", "object-1", "status"
        )

    def test_complete_refuses_capacities_without_a_fingerprint_baseline(self, vault: Path):
        runstate.write_digest_index(TODAY, [{
            "name": "Ship project",
            "path": "capacities://space-1/object-1",
            "identity": "capacities:space-1:custom-project:object-1",
            "source": "capacities",
            "capacities_id": "object-1",
            "capacities_completion_supported": True,
        }])
        capacities = FakeCapacities()

        with pytest.raises(ra.RuntimeActionError, match="fingerprint baseline"):
            ra.apply_action(
                TODAY, "complete", "Ship project", {}, capacities=capacities
            )

        assert capacities.calls == []
        assert ra.load_journal(TODAY)["actions"] == []

    def test_complete_closes_todoist_and_never_touches_the_vault(self, vault: Path):
        todoist, store = fresh_clients()
        action = ra.apply_action(
            TODAY, "complete", "Press", {}, todoist=todoist, store=store)
        assert action["status"] == "applied"
        assert todoist.tasks["task-1"]["is_completed"] is True
        # V3: the only step is the Todoist close (before-image captured)
        assert [s["kind"] for s in action["steps"]] == ["todoist.close"]
        assert action["steps"][0]["before"]["task"]["id"] == "task-1"
        assert (vault / "Projects" / "Press.md").read_text() == NOTE_TEXT

    def test_undo_complete_reopens_and_leaves_the_vault_alone(self, vault: Path):
        todoist, store = fresh_clients()
        action = ra.apply_action(
            TODAY, "complete", "Press", {}, todoist=todoist, store=store)
        undone = ra.undo_action(TODAY, action["id"],
                                todoist=todoist, store=store)
        assert undone["status"] == "undone"
        assert ("reopen_task", "task-1") in todoist.calls
        assert (vault / "Projects" / "Press.md").read_text() == NOTE_TEXT


class TestSkipAndRemove:
    @pytest.mark.parametrize("verb", ["skip_today", "remove_from_today"])
    def test_removes_owned_event_and_clears_time(self, vault: Path, verb: str):
        todoist, store = fresh_clients()
        action = ra.apply_action(
            TODAY, verb, "Press", {}, todoist=todoist, store=store)
        assert action["status"] == "applied"
        assert "ev-1" not in store.events
        assert ("reschedule_task", "task-1", "today") in todoist.calls
        # source preserved
        assert "task-1" in todoist.tasks
        assert (vault / "Projects" / "Press.md").read_text() == NOTE_TEXT

    def test_recurring_task_skips_due_write_entirely(self, vault: Path):
        # T27 guard carried into T20: recurring due is pattern-owned — the
        # all-day/no-time intent never writes due_string; only the owned
        # event is removed.
        todoist, store = fresh_clients()
        todoist.tasks["task-1"]["due"] = {
            "datetime": "2026-07-25T16:00:00", "is_recurring": True,
            "string": "every day at 16:00",
        }
        action = ra.apply_action(
            TODAY, "skip_today", "Press", {}, todoist=todoist, store=store)
        assert action["status"] == "applied"
        assert "ev-1" not in store.events
        assert not any(c[0].startswith("reschedule") for c in todoist.calls)
        assert todoist.tasks["task-1"]["due"]["string"] == "every day at 16:00"
        undone = ra.undo_action(TODAY, action["id"],
                                todoist=todoist, store=store)
        assert undone["status"] == "undone"
        assert not any(c[0].startswith("reschedule") for c in todoist.calls)

    def test_undo_restores_event_and_due(self, vault: Path):
        todoist, store = fresh_clients()
        action = ra.apply_action(
            TODAY, "skip_today", "Press", {}, todoist=todoist, store=store)
        undone = ra.undo_action(TODAY, action["id"],
                                todoist=todoist, store=store)
        assert undone["status"] == "undone"
        # event recreated (new id) with original interval
        assert any(e["start"] == "2026-07-25T16:00:00" for e in store.events.values())
        assert ("reschedule_task_datetime", "task-1", "2026-07-25T16:00:00") in todoist.calls


class TestDurationAndMove:
    def test_duration_edit_resizes_event_and_updates_todoist(self, vault: Path):
        todoist, store = fresh_clients()
        action = ra.apply_action(
            TODAY, "duration_edit", "Press", {"blocks": 2},
            todoist=todoist, store=store)
        assert action["status"] == "applied"
        assert store.events["ev-1"]["end"] == "2026-07-25T17:00:00"
        assert ("update_task", "task-1", {"duration": 60, "duration_unit": "minute"}) in todoist.calls

    def test_move_resize_updates_event_and_undoes(self, vault: Path):
        todoist, store = fresh_clients()
        action = ra.apply_action(
            TODAY, "move_resize", "Press",
            {"start": "17:00", "end": "17:45"}, todoist=todoist, store=store)
        assert store.events["ev-1"]["start"] == "2026-07-25T17:00:00"
        assert store.events["ev-1"]["end"] == "2026-07-25T17:45:00"
        undone = ra.undo_action(TODAY, action["id"],
                                todoist=todoist, store=store)
        assert undone["status"] == "undone"
        assert store.events["ev-1"]["start"] == "2026-07-25T16:00:00"
        assert store.events["ev-1"]["end"] == "2026-07-25T16:30:00"


class TestDeletePermanent:
    def test_deletes_todoist_and_event_never_the_vault(self, vault: Path):
        todoist, store = fresh_clients()
        action = ra.apply_action(
            TODAY, "delete_permanent", "Press", {},
            todoist=todoist, store=store)
        assert action["status"] == "applied"
        assert "task-1" not in todoist.tasks
        assert "ev-1" not in store.events
        assert (vault / "Projects" / "Press.md").read_text() == NOTE_TEXT
        assert not (vault / ".trash").exists()

    def test_undo_restores_all_surfaces(self, vault: Path):
        todoist, store = fresh_clients()
        action = ra.apply_action(
            TODAY, "delete_permanent", "Press", {},
            todoist=todoist, store=store)
        undone = ra.undo_action(TODAY, action["id"],
                                todoist=todoist, store=store)
        assert undone["status"] == "undone"
        # todoist task recreated from before-image (new id recorded)
        assert any(t["content"] == "Press" for t in todoist.tasks.values())
        assert (vault / "Projects" / "Press.md").read_text() == NOTE_TEXT
        assert any(e["title"] == "Press" for e in store.events.values())


class TestRetiredNoteRows:
    """A stale manifest row from the retired note source resolves to no source:
    every source verb takes its ordinary "no ... source" refusal (no journal
    entry, no side effect), the row never blocks a real source on the same
    name, and an old journal still loads."""

    SOURCE_VERBS = ["complete", "done", "assign", "unassign", "delete", "delete_permanent"]

    @pytest.mark.parametrize("verb", SOURCE_VERBS)
    def test_row_with_no_source_is_refused(self, vault: Path, verb: str):
        todoist, store = fresh_clients()
        before = list(todoist.calls)
        with pytest.raises(ra.RuntimeActionError):
            ra.apply_action(TODAY, verb, "Legacy", {},
                            todoist=todoist, store=store)
        assert todoist.calls == before
        assert ra.load_journal(TODAY)["actions"] == []

    def test_a_retired_row_does_not_block_a_real_source_of_the_same_name(self, vault: Path):
        state = runstate.build_runstate(
            {"plan_manifest": manifest_rows() + [dict(legacy_vault_rows()[0], name="Press")]})
        runstate.write_runstate(TODAY, state)
        assert "vault_path" not in ra.resolve_target(TODAY, "Press")
        todoist, store = fresh_clients()
        action = ra.apply_action(TODAY, "complete", "Press", {},
                                 todoist=todoist, store=store)
        assert action["status"] == "applied"
        assert todoist.tasks["task-1"]["is_completed"] is True

    def test_non_source_verbs_still_work_on_such_a_row(self, vault: Path):
        action = ra.apply_action(TODAY, "drop_from_plan", "Legacy", {},
                                 todoist=None, store=None)
        assert action["status"] == "applied"

    def test_removed_helpers_and_step_kinds_are_gone(self):
        for name in ("DONE_VALUE_BY_FILECLASS", "DEFAULT_DONE_VALUE", "TRASH_DIR_REL",
                     "_set_status", "_set_assigned_false", "_fileclass_of", "_atomic_write",
                     "VAULT_REMOVED_VERBS", "REMOVED_STEP_KINDS", "_removed_vault_action"):
            assert not hasattr(ra, name), name

    def test_an_old_journal_step_of_a_removed_kind_loads_and_fails_its_undo(self, vault: Path):
        journal = {"valid_date": str(TODAY), "actions": [{
            "id": "ra-old-1", "date": str(TODAY), "verb": "delete_permanent",
            "target": {"name": "Legacy"}, "args": {}, "idempotency_key": "k",
            "status": "applied", "created_at": "x", "finished_at": "x",
            "undone_at": None, "error": None,
            "steps": [{"kind": "vault.trash", "surface": "vault",
                       "path": "Projects/Press.md", "applied": True,
                       "trash_path": ".trash/Press.md",
                       "before": {"text": NOTE_TEXT, "path": "Projects/Press.md"}}],
        }]}
        ra._write_journal(TODAY, journal)
        assert ra.find_action(ra.load_journal(TODAY), "ra-old-1") is not None
        with pytest.raises(ra.RuntimeActionError, match="journal holds per-step errors"):
            ra.undo_action(TODAY, "ra-old-1", todoist=None, store=None)
        entry = ra.find_action(ra.load_journal(TODAY), "ra-old-1")
        assert entry["status"] == "undo_failed"
        assert "unknown step kind" in entry["steps"][0]["undo_error"]
        assert not (vault / ".trash").exists()


# ---------------------------------------------------------------------------
# Failure semantics
# ---------------------------------------------------------------------------

class TestFailures:
    def test_mid_apply_failure_compensates_applied_steps(self, vault: Path):
        todoist, store = fresh_clients()

        def boom(*a, **k):
            raise RuntimeError("calendar down mid-write")
        store.delete_event = boom  # type: ignore[assignment]
        # skip_today plans todoist.clear_time BEFORE calendar.delete? Ensure
        # deterministic order: calendar first. So make todoist fail instead.
        todoist.reschedule_task = boom  # type: ignore[assignment]
        action = ra.apply_action(
            TODAY, "skip_today", "Press", {}, todoist=todoist, store=store)
        assert action["status"] in ("compensated", "failed")
        if action["status"] == "compensated":
            # any applied step must be compensated back
            for step in action["steps"]:
                if step.get("applied"):
                    assert step.get("compensated") is True

    def test_unavailable_surface_fails_closed_before_any_write(self, vault: Path):
        todoist, _ = fresh_clients()
        calls_before = list(todoist.calls)
        with pytest.raises(ra.RuntimeActionError, match="surface unavailable: calendar"):
            ra.apply_action(TODAY, "skip_today", "Press", {},
                            todoist=todoist, store=None)
        assert todoist.calls == calls_before
        assert ra.load_journal(TODAY)["actions"] == []

    def test_unknown_verb_rejected(self, vault: Path):
        with pytest.raises(ra.RuntimeActionError, match="unknown verb"):
            ra.apply_action(TODAY, "explode", "Press", {},
                            todoist=None, store=None)

    def test_unknown_target_rejected(self, vault: Path):
        todoist, store = fresh_clients()
        with pytest.raises(ra.RuntimeActionError, match="not in today's plan"):
            ra.apply_action(TODAY, "complete", "Ghost", {},
                            todoist=todoist, store=store)

    def test_undo_requires_applied_status(self, vault: Path):
        todoist, store = fresh_clients()
        action = ra.apply_action(
            TODAY, "complete", "Press", {}, todoist=todoist, store=store)
        ra.undo_action(TODAY, action["id"], todoist=todoist, store=store)
        with pytest.raises(ra.RuntimeActionError, match="not undoable"):
            ra.undo_action(TODAY, action["id"], todoist=todoist, store=store)

    def test_undo_unknown_id_rejected(self, vault: Path):
        with pytest.raises(ra.RuntimeActionError, match="unknown action"):
            ra.undo_action(TODAY, "ra-nope", todoist=None, store=None)
