"""test_batch_undo.py — B4 batch undo with per-step conflict checks.

``undo_batch`` reverses a batch's APPLIED items in reverse order, calling
``runtime_actions.step_conflict`` before each reverse so a later edit to any
one item is reported as a conflict and left alone while the others revert.
A step kind with no safe pre-reverse check is reported ``non_reversible``;
items that never applied (failed/refused) are skipped; a second call reports
everything as already undone/conflict without re-reversing.

No network, no EventKit, no real writes — fake clients only, and the app-home
fixture (conftest) isolates every store.
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import date
from pathlib import Path

import pytest

import exclusion_settings as es
import runstate
import runtime_actions as ra
import runtime_batches as rb

TODAY = date(2026, 8, 13)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class FakeTodoist:
    def __init__(self, tasks: dict[str, dict] | None = None,
                 fail_get: set[str] | None = None) -> None:
        self.tasks = tasks or {}
        self.fail_get = set(fail_get or ())
        self.calls: list[tuple] = []

    def get_task(self, task_id: str) -> dict:
        self.calls.append(("get_task", task_id))
        if task_id in self.fail_get or task_id not in self.tasks:
            raise KeyError(task_id)
        return json.loads(json.dumps(self.tasks[task_id]))

    def close_task(self, task_id: str) -> None:
        self.calls.append(("close_task", task_id))
        self.tasks[task_id]["is_completed"] = True

    def reopen_task(self, task_id: str) -> None:
        self.calls.append(("reopen_task", task_id))
        self.tasks[task_id]["is_completed"] = False

    def reschedule_task(self, task_id: str, due_string: str) -> dict:
        self.calls.append(("reschedule_task", task_id, due_string))
        self.tasks[task_id]["due"] = {"string": due_string}
        return json.loads(json.dumps(self.tasks[task_id]))

    def reschedule_task_datetime(self, task_id: str, due_datetime: str) -> dict:
        self.calls.append(("reschedule_task_datetime", task_id, due_datetime))
        due = dict(self.tasks[task_id].get("due") or {})
        due.update({"datetime": due_datetime, "date": None})
        self.tasks[task_id]["due"] = due
        return json.loads(json.dumps(self.tasks[task_id]))

    def reschedule_task_date(self, task_id: str, due_date: str) -> dict:
        self.calls.append(("reschedule_task_date", task_id, due_date))
        due = dict(self.tasks[task_id].get("due") or {})
        due.update({"date": due_date, "datetime": None})
        self.tasks[task_id]["due"] = due
        return json.loads(json.dumps(self.tasks[task_id]))

    def clear_task_date(self, task_id: str) -> dict:
        self.calls.append(("clear_task_date", task_id))
        self.tasks[task_id]["due"] = None
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
# Manifest + fixtures
# ---------------------------------------------------------------------------

MANIFEST = [
    {"step": "B", "system": "todoist", "action": "schedule", "name": "Alpha",
     "id_or_path": "task-a", "time": "10:00", "duration_min": 30,
     "routing": "\u2014"},
    {"step": "B", "system": "todoist", "action": "schedule", "name": "Beta",
     "id_or_path": "task-b", "time": "11:00", "duration_min": 30,
     "routing": "\u2014"},
    {"step": "B", "system": "todoist", "action": "schedule", "name": "Gamma",
     "id_or_path": "task-c", "time": "12:00", "duration_min": 30,
     "routing": "\u2014"},
    # Delta carries both a Todoist row and an owned Blocks event, so
    # skip_today plans a calendar.delete + todoist.clear_time item whose
    # reverse has no safe conflict check.
    {"step": "B", "system": "todoist", "action": "schedule", "name": "Delta",
     "id_or_path": "task-d", "time": "14:00", "duration_min": 30,
     "routing": "\u2014"},
    {"step": "E", "system": "calendar", "action": "create-event",
     "name": "Delta", "id_or_path": "ev-d", "time": "14:00",
     "duration_min": 30, "routing": "Blocks"},
]

DUE = {"date": "2026-08-13", "datetime": None}


def _task(task_id: str, content: str) -> dict:
    return {"id": task_id, "content": content, "project_id": "p1",
            "is_completed": False, "completed_at": None, "completed_count": 0,
            "due": dict(DUE)}


def make_todoist(fail_get: set[str] | None = None) -> FakeTodoist:
    return FakeTodoist(
        {f"task-{s}": _task(f"task-{s}", name)
         for s, name in (("a", "Alpha"), ("b", "Beta"),
                         ("c", "Gamma"), ("d", "Delta"))},
        fail_get=fail_get,
    )


def make_store() -> FakeStore:
    return FakeStore({
        "ev-d": {"id": "ev-d", "title": "Delta", "start": "2026-08-13T14:00:00",
                 "end": "2026-08-13T14:30:00", "calendar_id": "cal-blocks",
                 "notes": None},
    })


@pytest.fixture()
def plan(tmp_path: Path) -> Path:
    runstate.write_runstate(
        TODAY, runstate.build_runstate({"plan_manifest": MANIFEST}))
    return tmp_path


def items(*names: str, verb: str = "done") -> list[dict]:
    return [{"verb": verb, "target": name, "args": {}} for name in names]


def _reopen_counts(fake: FakeTodoist) -> Counter:
    return Counter(c[1] for c in fake.calls if c[0] == "reopen_task")


def _identities(day: date = TODAY) -> list[str]:
    return [str(d.get("identity")) for d in runstate.read_dropped(day)]


def _status(batch_id: str, index: int) -> str | None:
    journal = ra.load_journal(TODAY)
    batch = next(b for b in journal["batches"] if b["id"] == batch_id)
    action_id = batch["items"][index]["action_id"]
    action = next(a for a in journal["actions"] if a["id"] == action_id)
    return action["status"]


# ---------------------------------------------------------------------------
# step_conflict: the pure pre-reverse check
# ---------------------------------------------------------------------------

class TestStepConflict:
    def test_checked_kinds_are_a_closed_set(self):
        assert {"todoist.close", "todoist.unassign", "runstate.drop",
                "runstate.undrop", "exclusions.add",
                "capacities.complete"} == ra.CONFLICT_CHECKED_KINDS

    def test_unknown_step_kind_is_not_reversible(self):
        with pytest.raises(ra.StepNotReversible):
            ra.step_conflict({"kind": "calendar.delete", "event_id": "ev-d"},
                             TODAY)

    def test_close_conflicts_once_the_task_is_reopened(self):
        fake = make_todoist()
        step = {"kind": "todoist.close", "task_id": "task-a"}
        fake.tasks["task-a"]["is_completed"] = True
        assert ra.step_conflict(step, TODAY, todoist=fake) is None
        fake.tasks["task-a"]["is_completed"] = False
        assert ra.step_conflict(step, TODAY, todoist=fake)

    def test_unassign_conflicts_when_the_due_changed(self):
        fake = make_todoist()
        step = {"kind": "todoist.unassign", "task_id": "task-a",
                "after": {"task": {"due": None}}}
        fake.tasks["task-a"]["due"] = None
        assert ra.step_conflict(step, TODAY, todoist=fake) is None
        fake.tasks["task-a"]["due"] = {"date": "2026-09-01"}
        assert ra.step_conflict(step, TODAY, todoist=fake)

    def test_capacities_complete_defers_to_the_fingerprint_guard(self):
        assert ra.step_conflict(
            {"kind": "capacities.complete", "object_id": "o1"}, TODAY) is None

    def test_drop_conflicts_once_the_identity_is_gone(self, plan: Path):
        step = {"kind": "runstate.drop", "identity": "todoist:task-a"}
        assert ra.step_conflict(step, TODAY)  # never dropped
        ra.apply_action(TODAY, "drop_from_plan", "Alpha")
        assert ra.step_conflict(step, TODAY) is None

    def test_undrop_conflicts_once_the_identity_is_back(self, plan: Path):
        ra.apply_action(TODAY, "drop_from_plan", "Alpha")
        ra.apply_action(TODAY, "restore_today", "todoist:task-a")
        step = {"kind": "runstate.undrop", "identity": "todoist:task-a"}
        assert ra.step_conflict(step, TODAY) is None  # still absent -> safe
        # A later edit re-drops the identity, so reversing the undrop would
        # clobber it.
        runstate.update_runstate(TODAY, {"dropped": [
            {"identity": "todoist:task-a", "name": "Alpha",
             "dropped_at": "2026-08-13T00:00:00Z"}]})
        assert ra.step_conflict(step, TODAY)  # identity is back -> conflict


# ---------------------------------------------------------------------------
# The three happy/sad proof cases
# ---------------------------------------------------------------------------

class TestUndoBatch:
    def test_three_item_batch_all_reverted(self, plan: Path):
        fake = make_todoist()
        applied = rb.apply_batch(
            TODAY, items("Alpha", "Beta", "Gamma"), todoist=fake)
        assert [r["status"] for r in applied["results"]] == [
            "applied", "applied", "applied"]

        out = rb.undo_batch(TODAY, applied["batch_id"], todoist=fake)

        assert [r["target"] for r in out["reverted"]] == [
            "Gamma", "Beta", "Alpha"]  # reverse order
        assert out["conflicts"] == []
        assert out["non_reversible"] == []
        assert [fake.tasks[f"task-{s}"]["is_completed"]
                for s in ("a", "b", "c")] == [False, False, False]
        for i in range(3):
            assert _status(applied["batch_id"], i) == "undone"

    def test_later_edit_to_one_item_is_conflict_and_left_alone(self, plan: Path):
        fake = make_todoist()
        applied = rb.apply_batch(
            TODAY, items("Alpha", "Beta", "Gamma"), todoist=fake)
        # A later, unrelated edit: Beta was reopened by someone else.
        fake.tasks["task-b"]["is_completed"] = False

        out = rb.undo_batch(TODAY, applied["batch_id"], todoist=fake)

        reverted_targets = [r["target"] for r in out["reverted"]]
        assert reverted_targets == ["Gamma", "Alpha"]
        assert [c["target"] for c in out["conflicts"]] == ["Beta"]
        assert out["conflicts"][0]["reason"]
        assert out["non_reversible"] == []
        # Alpha and Gamma reverted; Beta left exactly as the later edit made it.
        assert fake.tasks["task-a"]["is_completed"] is False
        assert fake.tasks["task-c"]["is_completed"] is False
        assert fake.tasks["task-b"]["is_completed"] is False

    def test_unassign_due_changed_is_a_conflict(self, plan: Path):
        fake = make_todoist()
        applied = rb.apply_batch(
            TODAY, items("Alpha", "Beta", verb="unassign"), todoist=fake)
        assert fake.tasks["task-a"]["due"] is None
        assert fake.tasks["task-b"]["due"] is None
        # A later edit changes Beta's due away from the applied state.
        fake.tasks["task-b"]["due"] = {"date": "2026-09-01", "datetime": None}

        out = rb.undo_batch(TODAY, applied["batch_id"], todoist=fake)

        assert [r["target"] for r in out["reverted"]] == ["Alpha"]
        assert [c["target"] for c in out["conflicts"]] == ["Beta"]
        # Alpha restored to its pre-unassign due; Beta keeps the later edit.
        assert fake.tasks["task-a"]["due"] == DUE
        assert fake.tasks["task-b"]["due"] == {
            "date": "2026-09-01", "datetime": None}

    def test_non_reversible_step_is_reported_and_left_alone(self, plan: Path):
        store = make_store()
        fake = make_todoist()
        applied = rb.apply_batch(
            TODAY, items("Delta", verb="skip_today"),
            todoist=fake, store=store)
        assert [r["status"] for r in applied["results"]] == ["applied"]
        assert "ev-d" not in store.events

        out = rb.undo_batch(
            TODAY, applied["batch_id"], todoist=fake, store=store)

        assert out["reverted"] == []
        assert out["conflicts"] == []
        assert [n["target"] for n in out["non_reversible"]] == ["Delta"]
        assert out["non_reversible"][0]["reason"]
        # Nothing was reversed: the event stays deleted, the task stays "today".
        assert "ev-d" not in store.events
        assert fake.tasks["task-d"]["due"] == {"string": "today"}

    def test_failed_item_is_skipped(self, plan: Path):
        fake = make_todoist(fail_get={"task-b"})
        applied = rb.apply_batch(
            TODAY, items("Alpha", "Beta", "Gamma"), todoist=fake)
        assert [r["status"] for r in applied["results"]] == [
            "applied", "failed", "applied"]

        out = rb.undo_batch(TODAY, applied["batch_id"], todoist=fake)

        assert [r["target"] for r in out["reverted"]] == ["Gamma", "Alpha"]
        # the failed item appears in no bucket at all
        assert all(b["item"] != 1 for b in out["conflicts"])
        assert all(b["item"] != 1 for b in out["non_reversible"])
        assert [r["item"] for r in out["reverted"]] == [2, 0]

    def test_double_undo_is_idempotent(self, plan: Path):
        fake = make_todoist()
        applied = rb.apply_batch(
            TODAY, items("Alpha", "Beta", "Gamma"), todoist=fake)
        first = rb.undo_batch(TODAY, applied["batch_id"], todoist=fake)
        assert len(first["reverted"]) == 3
        reopens = _reopen_counts(fake)

        second = rb.undo_batch(TODAY, applied["batch_id"], todoist=fake)

        assert second["reverted"] == []
        assert [c["target"] for c in second["conflicts"]] == [
            "Gamma", "Beta", "Alpha"]
        assert all("already undone" in c["reason"] for c in second["conflicts"])
        assert _reopen_counts(fake) == reopens  # no re-reversing
        assert fake.tasks["task-a"]["is_completed"] is False

    def test_unknown_batch_is_a_clean_error(self, plan: Path):
        fake = make_todoist()
        with pytest.raises(ra.RuntimeActionError):
            rb.undo_batch(TODAY, "rb-does-not-exist", todoist=fake)


# ---------------------------------------------------------------------------
# Isolation: unrelated later edits/skips survive
# ---------------------------------------------------------------------------

class TestIsolation:
    def test_later_skip_outside_the_batch_is_untouched(self, plan: Path):
        applied = rb.apply_batch(
            TODAY, items("Alpha", "Beta", verb="drop_from_plan"))
        # A later Skip today, outside the batch.
        ra.apply_action(TODAY, "drop_from_plan", "Gamma")

        out = rb.undo_batch(TODAY, applied["batch_id"])

        assert [r["target"] for r in out["reverted"]] == ["Beta", "Alpha"]
        assert _identities() == ["todoist:task-c"]

    def test_drop_identity_removed_later_is_a_conflict(self, plan: Path):
        applied = rb.apply_batch(
            TODAY, items("Alpha", "Beta", verb="drop_from_plan"))
        # A later Restore today removes Alpha's drop, outside the batch.
        ra.apply_action(TODAY, "restore_today", "todoist:task-a")

        out = rb.undo_batch(TODAY, applied["batch_id"])

        assert [r["target"] for r in out["reverted"]] == ["Beta"]
        assert [c["target"] for c in out["conflicts"]] == ["Alpha"]
        assert _identities() == []

    def test_hide_always_batch_reversed(self, plan: Path):
        applied = rb.apply_batch(
            TODAY, items("Alpha", "Beta", verb="hide_always"))
        assert {e.id for e in es.read_settings().settings.todoist} == {
            "task-a", "task-b"}

        out = rb.undo_batch(TODAY, applied["batch_id"])

        assert [r["target"] for r in out["reverted"]] == ["Beta", "Alpha"]
        assert es.read_settings().settings.todoist == ()

    def test_hide_always_later_removal_is_a_conflict(self, plan: Path):
        applied = rb.apply_batch(
            TODAY, items("Alpha", "Beta", verb="hide_always"))
        # A later edit removes Beta's exclusion, outside the batch.
        es.save_settings(expected_revision=es.read_settings().settings.revision,
                         todoist=[es.TodoistExclusion(
                             id="task-a", name="Alpha",
                             added_at="2026-08-13T00:00:00Z")])

        out = rb.undo_batch(TODAY, applied["batch_id"])

        assert [r["target"] for r in out["reverted"]] == ["Alpha"]
        assert [c["target"] for c in out["conflicts"]] == ["Beta"]
        assert es.read_settings().settings.todoist == ()


# ---------------------------------------------------------------------------
# Journal shape
# ---------------------------------------------------------------------------

class TestJournalShape:
    def test_undo_report_and_undone_at_are_journaled(self, plan: Path):
        fake = make_todoist()
        applied = rb.apply_batch(TODAY, items("Alpha"), todoist=fake)
        report = rb.undo_batch(TODAY, applied["batch_id"], todoist=fake)

        batch = next(b for b in ra.load_journal(TODAY)["batches"]
                     if b["id"] == applied["batch_id"])
        assert batch["undone_at"] is not None
        assert batch["undo_report"]["reverted"] == report["reverted"]

    def test_all_failed_batch_is_fully_accounted(self, plan: Path):
        fake = make_todoist(fail_get={"task-a"})
        applied = rb.apply_batch(TODAY, items("Alpha"), todoist=fake)
        assert applied["results"][0]["status"] == "failed"

        out = rb.undo_batch(TODAY, applied["batch_id"], todoist=fake)

        assert out["reverted"] == []
        batch = next(b for b in ra.load_journal(TODAY)["batches"]
                     if b["id"] == applied["batch_id"])
        assert batch["undone_at"] is not None
