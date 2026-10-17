"""test_runtime_batches.py — B3 batch apply + retry over runtime_actions.

A batch is a group of single-item actions (the same verbs ``apply_action``
already owns) executed sequentially, each under its own ``day_lock``. The
batch assigns one ``batch_id``, tags every journal action it creates with it,
and records a journal-level ``batches[]`` entry so a later retry can address
the unit without re-running successes.

Proofs: a forced failure in the middle keeps the successes and never stops a
later item; retry re-invokes ONLY the failed item and succeeds once the cause
is cleared; an empty batch is handled; a duplicate item is reported; the
journal batch shape and its action tags hold; a legacy journal (no
``batches[]``, no ``batch_id``) keeps working.

No network, no EventKit, no real writes — one fake Todoist client.
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import date
from pathlib import Path

import pytest

import runstate
import runtime_actions as ra
import runtime_batches as rb

TODAY = date(2026, 8, 13)


# ---------------------------------------------------------------------------
# Fake client
# ---------------------------------------------------------------------------

class FaultyTodoist:
    """Minimal Todoist fake. ``fail_get`` names tasks whose ``get_task``
    raises, so an apply step fails before it writes anything (status
    ``failed``, no compensation needed). Removing a name clears the cause."""

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


def _task(task_id: str, content: str) -> dict:
    return {"id": task_id, "content": content, "project_id": "p1",
            "is_completed": False, "completed_count": 0, "due": None}


def make_todoist(fail_get: set[str] | None = None) -> FaultyTodoist:
    return FaultyTodoist(
        {f"task-{suffix}": _task(f"task-{suffix}", name)
         for suffix, name in (("a", "Alpha"), ("b", "Beta"), ("c", "Gamma"))},
        fail_get=fail_get,
    )


def _close_counts(fake: FaultyTodoist) -> Counter:
    return Counter(c[1] for c in fake.calls if c[0] == "close_task")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def manifest_rows() -> list[dict]:
    return [
        {"step": "B", "system": "todoist", "action": "schedule", "name": name,
         "id_or_path": f"task-{suffix}", "time": time, "duration_min": 30,
         "routing": "—"}
        for suffix, name, time in (
            ("a", "Alpha", "10:00"),
            ("b", "Beta", "11:00"),
            ("c", "Gamma", "12:00"),
        )
    ]


@pytest.fixture()
def plan(tmp_path: Path) -> Path:
    runstate.write_runstate(
        TODAY, runstate.build_runstate({"plan_manifest": manifest_rows()}))
    return tmp_path


def items(*names: str) -> list[dict]:
    return [{"verb": "done", "target": name, "args": {}} for name in names]


# ---------------------------------------------------------------------------
# apply_batch: successes kept, failure never stops later items
# ---------------------------------------------------------------------------

class TestApplyBatch:
    def test_three_items_one_failure_keeps_the_successes(self, plan: Path):
        fake = make_todoist(fail_get={"task-b"})
        out = rb.apply_batch(TODAY, items("Alpha", "Beta", "Gamma"), todoist=fake)

        assert out["batch_id"]
        assert [r["status"] for r in out["results"]] == [
            "applied", "failed", "applied"]
        assert [r["index"] for r in out["results"]] == [0, 1, 2]
        # the middle failure did not stop the later item
        assert fake.tasks["task-a"]["is_completed"] is True
        assert fake.tasks["task-c"]["is_completed"] is True
        assert fake.tasks["task-b"]["is_completed"] is False
        assert out["results"][1]["error"]

    def test_refused_item_is_reported_and_does_not_stop_later_items(self, plan: Path):
        fake = make_todoist()
        out = rb.apply_batch(
            TODAY, items("Alpha", "Missing", "Gamma"), todoist=fake)
        assert [r["status"] for r in out["results"]] == [
            "applied", "refused", "applied"]
        assert out["results"][1]["error"]
        assert fake.tasks["task-c"]["is_completed"] is True


# ---------------------------------------------------------------------------
# retry_batch: only the unfinished items are re-invoked
# ---------------------------------------------------------------------------

class TestRetryBatch:
    def test_retry_runs_only_the_failed_item(self, plan: Path):
        fake = make_todoist(fail_get={"task-b"})
        out = rb.apply_batch(TODAY, items("Alpha", "Beta", "Gamma"), todoist=fake)
        before = _close_counts(fake)

        fake.fail_get.discard("task-b")
        retried = rb.retry_batch(TODAY, out["batch_id"], todoist=fake)

        assert retried["batch_id"] == out["batch_id"]
        assert [r["status"] for r in retried["results"]] == [
            "applied", "applied", "applied"]
        after = _close_counts(fake)
        assert after["task-b"] == before["task-b"] + 1
        # the already-applied items were not re-invoked at all
        assert after["task-a"] == before["task-a"]
        assert after["task-c"] == before["task-c"]
        assert fake.tasks["task-b"]["is_completed"] is True

    def test_retry_reports_already_applied_items_without_reinvoking(self, plan: Path):
        fake = make_todoist()
        out = rb.apply_batch(TODAY, items("Alpha"), todoist=fake)
        calls = len(fake.calls)
        retried = rb.retry_batch(TODAY, out["batch_id"], todoist=fake)
        assert [r["status"] for r in retried["results"]] == ["applied"]
        assert len(fake.calls) == calls

    def test_retry_of_refused_item_succeeds_after_cause_cleared(self, plan: Path):
        fake = make_todoist()
        out = rb.apply_batch(TODAY, items("Alpha", "Missing"), todoist=fake)
        assert out["results"][1]["status"] == "refused"

        runstate.write_runstate(TODAY, runstate.build_runstate(
            {"plan_manifest": manifest_rows() + [
                {"step": "B", "system": "todoist", "action": "schedule",
                 "name": "Missing", "id_or_path": "task-c", "time": "13:00",
                 "duration_min": 30, "routing": "—"},
            ]}))
        retried = rb.retry_batch(TODAY, out["batch_id"], todoist=fake)
        assert [r["status"] for r in retried["results"]] == ["applied", "applied"]

    def test_unknown_batch_is_a_clean_error(self, plan: Path):
        fake = make_todoist()
        with pytest.raises(ra.RuntimeActionError):
            rb.retry_batch(TODAY, "rb-does-not-exist", todoist=fake)


# ---------------------------------------------------------------------------
# Empty batch and duplicates
# ---------------------------------------------------------------------------

class TestBatchEdges:
    def test_empty_batch_is_handled(self, plan: Path):
        fake = make_todoist()
        out = rb.apply_batch(TODAY, [], todoist=fake)
        assert out["results"] == []
        assert out["batch_id"]
        retried = rb.retry_batch(TODAY, out["batch_id"], todoist=fake)
        assert retried["results"] == []
        assert fake.calls == []

    def test_duplicate_item_is_reported(self, plan: Path):
        fake = make_todoist()
        out = rb.apply_batch(TODAY, items("Alpha", "Alpha"), todoist=fake)
        assert [r["status"] for r in out["results"]] == ["applied", "duplicate"]
        assert _close_counts(fake)["task-a"] == 1

    def test_duplicate_against_a_prior_action_is_reported(self, plan: Path):
        fake = make_todoist()
        ra.apply_action(TODAY, "done", "Alpha", todoist=fake)
        out = rb.apply_batch(TODAY, items("Alpha"), todoist=fake)
        assert [r["status"] for r in out["results"]] == ["duplicate"]
        assert _close_counts(fake)["task-a"] == 1


# ---------------------------------------------------------------------------
# Journal shape, tagging, and legacy compatibility
# ---------------------------------------------------------------------------

class TestJournalShape:
    def test_batch_entry_and_action_tags(self, plan: Path):
        fake = make_todoist(fail_get={"task-b"})
        out = rb.apply_batch(TODAY, items("Alpha", "Beta", "Gamma"), todoist=fake)

        journal = ra.load_journal(TODAY)
        batches = journal["batches"]
        assert len(batches) == 1
        batch = batches[0]
        assert {"id", "item_action_ids", "created_at",
                "undone_at", "undo_report"} <= set(batch)
        assert batch["id"] == out["batch_id"]
        assert batch["undone_at"] is None
        assert batch["undo_report"] is None

        tagged = [a for a in journal["actions"]
                  if a.get("batch_id") == out["batch_id"]]
        assert len(tagged) == 3
        assert set(batch["item_action_ids"]) == {a["id"] for a in tagged}

    def test_legacy_journal_without_batches_keeps_working(self, plan: Path):
        legacy = {
            "valid_date": str(TODAY),
            "actions": [{
                "id": "ra-legacy-1", "date": str(TODAY), "verb": "done",
                "target": {"name": "Alpha"}, "args": {},
                "idempotency_key": "legacy", "status": "applied", "steps": [],
                "created_at": "2026-08-13T00:00:00Z",
                "finished_at": "2026-08-13T00:00:00Z",
                "undone_at": None, "error": None,
            }],
        }
        path = ra.journal_path(TODAY)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(legacy), encoding="utf-8")

        loaded = ra.load_journal(TODAY)
        assert loaded["actions"][0]["id"] == "ra-legacy-1"
        assert "batch_id" not in loaded["actions"][0]

        fake = make_todoist()
        out = rb.apply_batch(TODAY, items("Gamma"), todoist=fake)
        reloaded = ra.load_journal(TODAY)
        assert len(reloaded["batches"]) == 1
        assert reloaded["batches"][0]["id"] == out["batch_id"]
        assert fake.tasks["task-c"]["is_completed"] is True

    def test_unbatched_action_has_no_batch_id_key(self, plan: Path):
        fake = make_todoist()
        action = ra.apply_action(TODAY, "done", "Alpha", todoist=fake)
        assert "batch_id" not in action
