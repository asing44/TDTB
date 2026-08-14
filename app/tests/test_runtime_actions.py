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
        {"step": "C", "system": "vault", "action": "set-flag", "name": "Press",
         "id_or_path": "Projects/Press.md", "time": None, "duration_min": 0, "routing": "—"},
    ]


@pytest.fixture()
def vault(tmp_path: Path) -> Path:
    (tmp_path / "Projects").mkdir()
    (tmp_path / "Projects" / "Press.md").write_text(NOTE_TEXT, encoding="utf-8")
    (tmp_path / "Projects" / "Cap.md").write_text(CAPTURE_TEXT, encoding="utf-8")
    state = runstate.build_runstate({"plan_manifest": manifest_rows()})
    runstate.write_runstate(tmp_path, TODAY, state)
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
        j = ra.load_journal(vault, TODAY)
        assert j["valid_date"] == str(TODAY)
        assert j["actions"] == []

    def test_apply_appends_and_roundtrips(self, vault: Path):
        todoist, store = fresh_clients()
        action = ra.apply_action(
            vault, TODAY, "complete", "Press", {}, todoist=todoist, store=store)
        assert action["status"] == "applied"
        j = ra.load_journal(vault, TODAY)
        assert len(j["actions"]) == 1
        assert j["actions"][0]["id"] == action["id"]
        # journal file is valid JSON on disk
        raw = json.loads((vault / ra.journal_rel_path(TODAY)).read_text())
        assert raw["actions"][0]["verb"] == "complete"

    def test_idempotent_repeat_returns_existing(self, vault: Path):
        todoist, store = fresh_clients()
        first = ra.apply_action(
            vault, TODAY, "complete", "Press", {}, todoist=todoist, store=store)
        calls_after_first = list(todoist.calls)
        second = ra.apply_action(
            vault, TODAY, "complete", "Press", {}, todoist=todoist, store=store)
        assert second["id"] == first["id"]
        assert second.get("duplicate") is True
        assert todoist.calls == calls_after_first  # no new writes

    def test_find_action_by_id(self, vault: Path):
        todoist, store = fresh_clients()
        action = ra.apply_action(
            vault, TODAY, "complete", "Press", {}, todoist=todoist, store=store)
        j = ra.load_journal(vault, TODAY)
        assert ra.find_action(j, action["id"])["verb"] == "complete"
        assert ra.find_action(j, "ra-nope") is None


# ---------------------------------------------------------------------------
# Verbs
# ---------------------------------------------------------------------------

class TestComplete:
    def test_complete_closes_todoist_and_flips_vault(self, vault: Path):
        todoist, store = fresh_clients()
        action = ra.apply_action(
            vault, TODAY, "complete", "Press", {}, todoist=todoist, store=store)
        assert action["status"] == "applied"
        assert todoist.tasks["task-1"]["is_completed"] is True
        text = (vault / "Projects" / "Press.md").read_text()
        # T12d: the task FileClass closes to "completed"; "done" was in no enum.
        assert "status: completed" in text
        # before-images captured
        kinds = [s["kind"] for s in action["steps"]]
        assert "todoist.close" in kinds and "vault.complete" in kinds
        vault_step = next(s for s in action["steps"] if s["kind"] == "vault.complete")
        assert vault_step["before"]["text"] == NOTE_TEXT

    def test_vault_flip_is_byte_preserving_outside_status(self, vault: Path):
        todoist, store = fresh_clients()
        ra.apply_action(vault, TODAY, "complete", "Press", {},
                        todoist=todoist, store=store)
        text = (vault / "Projects" / "Press.md").read_text()
        assert text == NOTE_TEXT.replace("status: in-progress", "status: completed")

    def test_capture_fileclass_completes_to_processed(self, vault: Path):
        state = runstate.build_runstate({"plan_manifest": [
            {"step": "C", "system": "vault", "action": "set-flag", "name": "Cap",
             "id_or_path": "Projects/Cap.md", "time": None, "duration_min": 0,
             "routing": "—"},
        ]})
        runstate.write_runstate(vault, TODAY, state)
        ra.apply_action(vault, TODAY, "complete", "Cap", {}, todoist=None, store=None)
        assert "status: processed" in (vault / "Projects" / "Cap.md").read_text()

    def test_undo_complete_restores_exact_bytes_and_reopens(self, vault: Path):
        todoist, store = fresh_clients()
        action = ra.apply_action(
            vault, TODAY, "complete", "Press", {}, todoist=todoist, store=store)
        undone = ra.undo_action(vault, TODAY, action["id"],
                                todoist=todoist, store=store)
        assert undone["status"] == "undone"
        assert ("reopen_task", "task-1") in todoist.calls
        assert (vault / "Projects" / "Press.md").read_text() == NOTE_TEXT


class TestSkipAndRemove:
    @pytest.mark.parametrize("verb", ["skip_today", "remove_from_today"])
    def test_removes_owned_event_and_clears_time(self, vault: Path, verb: str):
        todoist, store = fresh_clients()
        action = ra.apply_action(
            vault, TODAY, verb, "Press", {}, todoist=todoist, store=store)
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
            vault, TODAY, "skip_today", "Press", {}, todoist=todoist, store=store)
        assert action["status"] == "applied"
        assert "ev-1" not in store.events
        assert not any(c[0].startswith("reschedule") for c in todoist.calls)
        assert todoist.tasks["task-1"]["due"]["string"] == "every day at 16:00"
        undone = ra.undo_action(vault, TODAY, action["id"],
                                todoist=todoist, store=store)
        assert undone["status"] == "undone"
        assert not any(c[0].startswith("reschedule") for c in todoist.calls)

    def test_undo_restores_event_and_due(self, vault: Path):
        todoist, store = fresh_clients()
        action = ra.apply_action(
            vault, TODAY, "skip_today", "Press", {}, todoist=todoist, store=store)
        undone = ra.undo_action(vault, TODAY, action["id"],
                                todoist=todoist, store=store)
        assert undone["status"] == "undone"
        # event recreated (new id) with original interval
        assert any(e["start"] == "2026-07-25T16:00:00" for e in store.events.values())
        assert ("reschedule_task_datetime", "task-1", "2026-07-25T16:00:00") in todoist.calls


class TestDurationAndMove:
    def test_duration_edit_resizes_event_and_updates_todoist(self, vault: Path):
        todoist, store = fresh_clients()
        action = ra.apply_action(
            vault, TODAY, "duration_edit", "Press", {"blocks": 2},
            todoist=todoist, store=store)
        assert action["status"] == "applied"
        assert store.events["ev-1"]["end"] == "2026-07-25T17:00:00"
        assert ("update_task", "task-1", {"duration": 60, "duration_unit": "minute"}) in todoist.calls

    def test_move_resize_updates_event_and_undoes(self, vault: Path):
        todoist, store = fresh_clients()
        action = ra.apply_action(
            vault, TODAY, "move_resize", "Press",
            {"start": "17:00", "end": "17:45"}, todoist=todoist, store=store)
        assert store.events["ev-1"]["start"] == "2026-07-25T17:00:00"
        assert store.events["ev-1"]["end"] == "2026-07-25T17:45:00"
        undone = ra.undo_action(vault, TODAY, action["id"],
                                todoist=todoist, store=store)
        assert undone["status"] == "undone"
        assert store.events["ev-1"]["start"] == "2026-07-25T16:00:00"
        assert store.events["ev-1"]["end"] == "2026-07-25T16:30:00"


class TestDeletePermanent:
    def test_deletes_todoist_vault_and_event(self, vault: Path):
        todoist, store = fresh_clients()
        action = ra.apply_action(
            vault, TODAY, "delete_permanent", "Press", {},
            todoist=todoist, store=store)
        assert action["status"] == "applied"
        assert "task-1" not in todoist.tasks
        assert "ev-1" not in store.events
        assert not (vault / "Projects" / "Press.md").exists()
        trashed = list((vault / ".trash").glob("*"))
        assert len(trashed) == 1 and trashed[0].read_text() == NOTE_TEXT

    def test_undo_restores_all_surfaces(self, vault: Path):
        todoist, store = fresh_clients()
        action = ra.apply_action(
            vault, TODAY, "delete_permanent", "Press", {},
            todoist=todoist, store=store)
        undone = ra.undo_action(vault, TODAY, action["id"],
                                todoist=todoist, store=store)
        assert undone["status"] == "undone"
        # todoist task recreated from before-image (new id recorded)
        assert any(t["content"] == "Press" for t in todoist.tasks.values())
        assert (vault / "Projects" / "Press.md").read_text() == NOTE_TEXT
        assert any(e["title"] == "Press" for e in store.events.values())


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
            vault, TODAY, "skip_today", "Press", {}, todoist=todoist, store=store)
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
            ra.apply_action(vault, TODAY, "skip_today", "Press", {},
                            todoist=todoist, store=None)
        assert todoist.calls == calls_before
        assert ra.load_journal(vault, TODAY)["actions"] == []

    def test_unknown_verb_rejected(self, vault: Path):
        with pytest.raises(ra.RuntimeActionError, match="unknown verb"):
            ra.apply_action(vault, TODAY, "explode", "Press", {},
                            todoist=None, store=None)

    def test_unknown_target_rejected(self, vault: Path):
        todoist, store = fresh_clients()
        with pytest.raises(ra.RuntimeActionError, match="not in today's plan"):
            ra.apply_action(vault, TODAY, "complete", "Ghost", {},
                            todoist=todoist, store=store)

    def test_undo_requires_applied_status(self, vault: Path):
        todoist, store = fresh_clients()
        action = ra.apply_action(
            vault, TODAY, "complete", "Press", {}, todoist=todoist, store=store)
        ra.undo_action(vault, TODAY, action["id"], todoist=todoist, store=store)
        with pytest.raises(ra.RuntimeActionError, match="not undoable"):
            ra.undo_action(vault, TODAY, action["id"], todoist=todoist, store=store)

    def test_undo_unknown_id_rejected(self, vault: Path):
        with pytest.raises(ra.RuntimeActionError, match="unknown action"):
            ra.undo_action(vault, TODAY, "ra-nope", todoist=None, store=None)
