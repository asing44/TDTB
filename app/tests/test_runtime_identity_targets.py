"""test_runtime_identity_targets.py — row-verb targets by stable identity.

The Plan tasks rows address their source verbs by the row's STABLE IDENTITY
(``todoist:<task id>``, a Capacities identity, a calendar identity), because
that is what survives a rename and what the row's own guard checks. The app
resolves every target from its OWN derived rows, so the identity is only ever
a lookup key into today's ``plan_manifest`` / ``digest_index`` — never an
instruction to address an arbitrary provider id.

Proofs (reported red against the name-only resolver):
- a staging-phase row (digest index only, no commit) accepts its stable
  identity for ``drop_from_plan`` (Skip today) and records the identity in the
  dated ``dropped[]`` list;
- a committed row (plan manifest) accepts its stable identity for
  ``hide_always`` and ``unassign``;
- a batch carries identity targets for both verbs and applies them;
- an identity that is NOT in today's derived rows still fails closed.

No network, no EventKit, no real writes.
"""
from __future__ import annotations

import json
from datetime import date

import pytest

import runstate
import runtime_actions as ra
import runtime_batches as rb

TODAY = date(2026, 10, 19)
TASK_ID = "6hm23jmp5mVvmCCp"
IDENTITY = f"todoist:{TASK_ID}"


class IdentityTodoist:
    """Minimal Todoist fake: one task with a due shape the verbs can advance."""

    def __init__(self, task: dict) -> None:
        self.tasks = {task["id"]: json.loads(json.dumps(task))}
        self.calls: list[tuple] = []

    def get_task(self, task_id: str) -> dict:
        self.calls.append(("get_task", task_id))
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


def _recurring_task() -> dict:
    return {
        "id": TASK_ID,
        "content": "Trash day",
        "is_completed": False,
        "completed_count": 0,
        "due": {
            "date": "2026-10-18T15:30:00",
            "is_recurring": True,
            "string": "every day at 3:30pm",
        },
    }


def _plan_task() -> dict:
    return {
        "id": TASK_ID,
        "content": "Talk about visiting Grandma sometime",
        "is_completed": False,
        "completed_count": 0,
        "due": None,
    }


def _index_row(name: str) -> dict:
    return {
        "name": name,
        "source": "todoist",
        "surface": "assigned",
        "todoist_id": TASK_ID,
        "path": f"todoist://{TASK_ID}",
        "is_recurring": True,
        "duration_minutes": 30,
    }


def _manifest_row(name: str) -> dict:
    return {
        "step": "B", "system": "todoist", "action": "schedule", "name": name,
        "id_or_path": TASK_ID, "time": "15:30", "duration_min": 30,
        "routing": "—",
    }


class TestStagingPhaseIdentity:
    def test_skip_today_accepts_the_stable_identity(self):
        runstate.write_digest_index(TODAY, [_index_row("Trash day")])

        action = ra.apply_action(
            TODAY, "drop_from_plan", IDENTITY, {}, todoist=None, store=None)

        assert action["status"] == "applied"
        dropped = [d["identity"] for d in runstate.read_dropped(TODAY)]
        assert dropped == [IDENTITY]

    def test_hide_always_accepts_the_stable_identity(self):
        runstate.write_digest_index(
            TODAY, [_index_row("Talk about visiting Grandma sometime")])

        action = ra.apply_action(TODAY, "hide_always", IDENTITY, {})

        assert action["status"] == "applied"

    def test_unknown_identity_still_fails_closed(self):
        runstate.write_digest_index(TODAY, [_index_row("Trash day")])

        with pytest.raises(ra.RuntimeActionError) as exc:
            ra.apply_action(TODAY, "drop_from_plan", "todoist:not-in-today", {})

        assert "not in today's plan manifest or digest" in str(exc.value)


class TestCommittedPhaseIdentity:
    def test_skip_occurrence_advances_the_recurring_due(self):
        runstate.write_runstate(
            TODAY,
            runstate.build_runstate({"plan_manifest": [_manifest_row("Trash day")]}),
        )
        todoist = IdentityTodoist(_recurring_task())

        action = ra.apply_action(
            TODAY, "unassign", IDENTITY, {}, todoist=todoist)

        assert action["status"] == "applied"
        assert action["target"]["todoist_id"] == TASK_ID
        assert (
            "reschedule_task_datetime",
            TASK_ID,
            "2026-10-19T15:30:00",
        ) in todoist.calls

    def test_unassign_in_source_clears_a_one_off_date(self):
        runstate.write_runstate(
            TODAY,
            runstate.build_runstate({
                "plan_manifest": [
                    _manifest_row("Talk about visiting Grandma sometime")
                ],
            }),
        )
        todoist = IdentityTodoist(_plan_task())

        action = ra.apply_action(
            TODAY, "unassign", IDENTITY, {}, todoist=todoist)

        assert action["status"] == "applied"
        assert ("clear_task_date", TASK_ID) in todoist.calls


class TestBatchIdentityTargets:
    def test_a_batch_applies_identity_targets(self):
        runstate.write_digest_index(TODAY, [_index_row("Trash day")])
        todoist = IdentityTodoist(_recurring_task())

        out = rb.apply_batch(
            TODAY,
            [
                {"verb": "unassign", "target": IDENTITY, "args": {}},
                {"verb": "hide_always", "target": IDENTITY, "args": {}},
            ],
            todoist=todoist,
        )

        assert [r["status"] for r in out["results"]] == ["applied", "applied"]
