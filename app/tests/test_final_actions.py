"""test_final_actions.py — IMP-05 final source-action engine.

Done / Drop from plan / Unassign / Delete with recurring-task safeguards,
pre/post-commit availability, compensation, and wire behavior. Fake clients
and a tmp vault only — no live Todoist, Calendar, or vault writes.

Authority: frozen reliability plan action-semantics table
(``Plans Link/2026-08-09-tdtb-planning-ui-reliability.md``) and the IMP-03
Red tests in ``test_locked_contract_red.py``.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import main as main_mod
import runstate
import runtime_actions as ra

TODAY = date(2026, 8, 13)

NOTE_TEXT = """---
type: [task]
status: in-progress
assigned: true
---

# Press

body stays byte-identical
"""


class FakeTodoist:
    """Task-shaped fake modeling Todoist due semantics: a datetime/date
    update preserves the rest of the ``due`` dict (recurrence string/type),
    exactly like the real API's non-completing due advance."""

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
        due = dict(self.tasks[task_id].get("due") or {})
        due.update({"string": due_string, "datetime": None, "date": None})
        self.tasks[task_id]["due"] = due
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


def manifest_rows(name: str = "Press", vault_rel: str = "Projects/Press.md") -> list[dict]:
    return [
        {"step": "B", "system": "todoist", "action": "schedule", "name": name,
         "id_or_path": "task-1", "time": "16:00", "duration_min": 30, "routing": "—"},
        {"step": "E", "system": "calendar", "action": "create-event", "name": name,
         "id_or_path": "ev-1", "time": "16:00", "duration_min": 30, "routing": "Blocks"},
        {"step": "C", "system": "vault", "action": "set-flag", "name": name,
         "id_or_path": vault_rel, "time": None, "duration_min": 0, "routing": "—"},
    ]


def manifest_vault_only(name: str = "Press", vault_rel: str = "Projects/Press.md") -> list[dict]:
    """A committed target with NO linked Todoist/calendar row — vault unassign
    tests must prove the assigned:false flip without a Todoist client."""
    return [
        {"step": "C", "system": "vault", "action": "set-flag", "name": name,
         "id_or_path": vault_rel, "time": None, "duration_min": 0, "routing": "—"},
    ]


@pytest.fixture()
def vault(tmp_path: Path) -> Path:
    (tmp_path / "Projects").mkdir()
    (tmp_path / "Projects" / "Press.md").write_text(NOTE_TEXT, encoding="utf-8")
    state = runstate.build_runstate({"plan_manifest": manifest_rows()})
    runstate.write_runstate(tmp_path, TODAY, state)
    return tmp_path


def fresh_clients() -> tuple[FakeTodoist, FakeStore]:
    todoist = FakeTodoist({
        "task-1": {"id": "task-1", "content": "Press", "project_id": "p1",
                   "is_completed": False, "completed_count": 0,
                   "due": {"datetime": "2026-08-13T16:00:00"}},
    })
    store = FakeStore({
        "ev-1": {"id": "ev-1", "title": "Press", "start": "2026-08-13T16:00:00",
                 "end": "2026-08-13T16:30:00", "calendar_id": "cal-blocks",
                 "notes": None},
    })
    return todoist, store


def recurring_task(due: dict | None = None) -> FakeTodoist:
    return FakeTodoist({
        "task-1": {"id": "task-1", "content": "Press", "project_id": "p1",
                   "is_completed": False, "completed_count": 0,
                   "due": due if due is not None else {
                       "datetime": "2026-08-13T16:00:00", "is_recurring": True,
                       "string": "every day at 16:00"}},
    })


# ---------------------------------------------------------------------------
# Verb catalogue + availability
# ---------------------------------------------------------------------------

class TestVerbCatalogue:
    def test_final_intents_in_verbs(self):
        assert {"done", "drop_from_plan", "unassign", "delete"} <= set(ra.VERBS)

    def test_all_four_are_staging_verbs(self):
        assert {"done", "drop_from_plan", "unassign", "delete"} <= set(ra.STAGING_VERBS)

    def test_drop_plans_runstate_only(self):
        steps = ra.plan_steps(
            "drop_from_plan",
            {"phase": "committed", "name": "Press", "id": "x"}, {}, TODAY)
        assert steps
        assert all(s.get("system") == "runstate" for s in steps)

    def test_unassign_plans_non_closing_todoist_step(self):
        steps = ra.plan_steps(
            "unassign",
            {"phase": "committed", "name": "Press", "id": "x",
             "is_recurring": True}, {}, TODAY)
        assert any(s.get("system") == "todoist" and not s.get("close") for s in steps)

    def test_done_plans_close_and_vault_complete(self):
        target = {"phase": "committed", "name": "Press",
                  "todoist_id": "task-1", "vault_path": "Projects/Press.md"}
        steps = ra.plan_steps("done", target, {}, TODAY)
        kinds = [s["kind"] for s in steps]
        assert "todoist.close" in kinds and "vault.complete" in kinds

    def test_delete_plans_all_linked_targets(self):
        target = {"phase": "committed", "name": "Press", "todoist_id": "task-1",
                  "vault_path": "Projects/Press.md", "event_id": "ev-1"}
        steps = ra.plan_steps("delete", target, {}, TODAY)
        kinds = [s["kind"] for s in steps]
        assert "todoist.delete" in kinds and "vault.trash" in kinds
        assert "calendar.delete" in kinds


# ---------------------------------------------------------------------------
# Done / Delete
# ---------------------------------------------------------------------------

class TestDone:
    def test_done_closes_current_occurrence_and_flips_vault(self, vault: Path):
        todoist, store = fresh_clients()
        action = ra.apply_action(vault, TODAY, "done", "Press", {},
                                 todoist=todoist, store=store)
        assert action["status"] == "applied"
        assert todoist.tasks["task-1"]["is_completed"] is True
        assert "status: completed" in (vault / "Projects" / "Press.md").read_text()
        # already-created TDTB block stays historical
        assert "ev-1" in store.events

    def test_done_undo_reopens_and_restores_bytes(self, vault: Path):
        todoist, store = fresh_clients()
        action = ra.apply_action(vault, TODAY, "done", "Press", {},
                                 todoist=todoist, store=store)
        undone = ra.undo_action(vault, TODAY, action["id"],
                                todoist=todoist, store=store)
        assert undone["status"] == "undone"
        assert todoist.tasks["task-1"]["is_completed"] is False
        assert (vault / "Projects" / "Press.md").read_text() == NOTE_TEXT


class TestDelete:
    def test_delete_removes_every_linked_target(self, vault: Path):
        todoist, store = fresh_clients()
        action = ra.apply_action(vault, TODAY, "delete", "Press", {},
                                 todoist=todoist, store=store)
        assert action["status"] == "applied"
        assert "task-1" not in todoist.tasks
        assert "ev-1" not in store.events
        assert not (vault / "Projects" / "Press.md").exists()

    def test_delete_undo_restores_all_surfaces(self, vault: Path):
        todoist, store = fresh_clients()
        action = ra.apply_action(vault, TODAY, "delete", "Press", {},
                                 todoist=todoist, store=store)
        undone = ra.undo_action(vault, TODAY, action["id"],
                                todoist=todoist, store=store)
        assert undone["status"] == "undone"
        assert any(t["content"] == "Press" for t in todoist.tasks.values())
        assert any(e["title"] == "Press" for e in store.events.values())
        assert (vault / "Projects" / "Press.md").read_text() == NOTE_TEXT


# ---------------------------------------------------------------------------
# Drop from plan — date-scoped runstate exclusion
# ---------------------------------------------------------------------------

class TestDropFromPlan:
    def test_drop_writes_runstate_only(self, vault: Path):
        todoist, store = fresh_clients()
        calls_before = list(todoist.calls) + list(store.calls)
        action = ra.apply_action(vault, TODAY, "drop_from_plan", "Press", {},
                                 todoist=todoist, store=store)
        assert action["status"] == "applied"
        # no source touched
        assert todoist.calls == []
        assert store.calls == []
        assert (vault / "Projects" / "Press.md").read_text() == NOTE_TEXT
        assert "task-1" in todoist.tasks and "ev-1" in store.events
        dropped = runstate.read_dropped(vault, TODAY)
        assert any(d.get("identity") == "todoist:task-1" for d in dropped)

    def test_drop_is_idempotent_per_date(self, vault: Path):
        ra.apply_action(vault, TODAY, "drop_from_plan", "Press")
        dup = ra.apply_action(vault, TODAY, "drop_from_plan", "Press")
        assert dup.get("duplicate") is True
        assert len(runstate.read_dropped(vault, TODAY)) == 1

    def test_drop_undo_restores_eligibility(self, vault: Path):
        action = ra.apply_action(vault, TODAY, "drop_from_plan", "Press")
        ra.undo_action(vault, TODAY, action["id"])
        assert runstate.read_dropped(vault, TODAY) == []

    def test_drop_is_date_scoped(self, vault: Path):
        ra.apply_action(vault, TODAY, "drop_from_plan", "Press")
        tomorrow = date(2026, 8, 14)
        assert runstate.read_dropped(vault, tomorrow) == []

    def test_drop_filters_digest_and_exposes_dropped_today(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        vault = tmp_path / "vault-root"
        (vault / "50 - Operations" / "Projects").mkdir(parents=True)
        (vault / "50 - Operations" / "Projects" / "Press.md").write_text(
            "---\ntype: [project]\nstatus: active\nassigned: true\n---\n\n# Press\n",
            encoding="utf-8")
        monkeypatch.setattr(main_mod.gather, "effective_date", lambda _n: TODAY)
        app = main_mod.create_app(vault_root=vault)
        client = TestClient(app)
        client.headers.update({"X-TDTB-Token": app.state.token})
        first = client.get("/plan-inputs").json()
        assert any(i["name"] == "Press" for i in first["digest"]["assigned"])
        # drop resolves from the persisted identity index (staging phase)
        assert ra.resolve_target(vault, TODAY, "Press")["phase"] == "staging"
        action = ra.apply_action(vault, TODAY, "drop_from_plan", "Press")
        assert action["status"] == "applied"
        second = client.get("/plan-inputs").json()
        assert not any(i["name"] == "Press" for i in second["digest"]["assigned"])
        dropped = second.get("dropped_today") or []
        assert any(d.get("identity", "").endswith("Press.md") for d in dropped)


# ---------------------------------------------------------------------------
# Unassign — vault assigned:false, Todoist clear / non-completing advance
# ---------------------------------------------------------------------------

class TestUnassignVault:
    @pytest.fixture()
    def vault_only(self, tmp_path: Path) -> Path:
        root = tmp_path / "vault-only"
        (root / "Projects").mkdir(parents=True)
        (root / "Projects" / "Press.md").write_text(NOTE_TEXT, encoding="utf-8")
        runstate.write_runstate(
            root, TODAY,
            runstate.build_runstate({"plan_manifest": manifest_vault_only()}))
        return root

    def test_unassign_flips_assigned_false_byte_preserving(self, vault_only: Path):
        action = ra.apply_action(vault_only, TODAY, "unassign", "Press", {},
                                 todoist=None, store=None)
        assert action["status"] == "applied"
        text = (vault_only / "Projects" / "Press.md").read_text()
        assert "assigned: false" in text
        assert text == NOTE_TEXT.replace("assigned: true", "assigned: false")
        # no deferral-memory ranking nudge
        import deferrals
        assert deferrals.load_deferrals(vault_only) == {}

    def test_unassign_vault_undo_restores_bytes(self, vault_only: Path):
        action = ra.apply_action(vault_only, TODAY, "unassign", "Press", {},
                                 todoist=None, store=None)
        ra.undo_action(vault_only, TODAY, action["id"])
        assert (vault_only / "Projects" / "Press.md").read_text() == NOTE_TEXT

    def test_unassign_after_unassign_is_noop_then_duplicate(self, vault_only: Path):
        ra.apply_action(vault_only, TODAY, "unassign", "Press", {},
                        todoist=None, store=None)
        dup = ra.apply_action(vault_only, TODAY, "unassign", "Press", {},
                              todoist=None, store=None)
        assert dup.get("duplicate") is True

    def test_linked_unassign_fails_closed_without_all_surfaces(self, vault: Path):
        # linked Todoist + vault identity: one missing surface applies nothing
        with pytest.raises(ra.RuntimeActionError, match="surface unavailable: todoist"):
            ra.apply_action(vault, TODAY, "unassign", "Press", {},
                            todoist=None, store=None)
        assert (vault / "Projects" / "Press.md").read_text() == NOTE_TEXT


class TestUnassignTodoist:
    def test_non_recurring_clears_the_date(self, vault: Path):
        todoist, _ = fresh_clients()
        action = ra.apply_action(vault, TODAY, "unassign", "Press", {},
                                 todoist=todoist, store=None)
        assert action["status"] == "applied"
        assert ("clear_task_date", "task-1") in todoist.calls
        assert todoist.tasks["task-1"]["due"] is None
        assert not any(c[0].startswith("close") for c in todoist.calls)
        assert todoist.tasks["task-1"]["is_completed"] is False

    def test_non_recurring_undo_restores_the_due(self, vault: Path):
        todoist, _ = fresh_clients()
        action = ra.apply_action(vault, TODAY, "unassign", "Press", {},
                                 todoist=todoist, store=None)
        ra.undo_action(vault, TODAY, action["id"], todoist=todoist)
        assert todoist.tasks["task-1"]["due"]["datetime"] == "2026-08-13T16:00:00"

    def test_recurring_advances_without_completing_and_proves_readback(
        self, vault: Path,
    ):
        # recurring target with a vault row too — the whole linked set
        state = runstate.build_runstate({"plan_manifest": manifest_rows()})
        runstate.write_runstate(vault, TODAY, state)
        todoist = recurring_task()
        action = ra.apply_action(vault, TODAY, "unassign", "Press", {},
                                 todoist=todoist, store=None)
        assert action["status"] == "applied"
        # advanced to the NEXT occurrence, never closed
        assert not any(c[0].startswith("close") for c in todoist.calls)
        assert todoist.tasks["task-1"]["is_completed"] is False
        assert todoist.tasks["task-1"]["completed_count"] == 0
        due = todoist.tasks["task-1"]["due"]
        assert due["datetime"] == "2026-08-14T16:00:00"
        # recurrence string/type preserved
        assert due["string"] == "every day at 16:00"
        assert due["is_recurring"] is True
        # read-back proof recorded in the journal step
        step = next(s for s in action["steps"] if s["kind"] == "todoist.unassign")
        assert step["readback"]["completed"] is False
        assert step["readback"]["changed"] == {}
        assert step["readback"]["recurrence_preserved"] is True

    def test_recurring_undo_restores_prior_due(self, vault: Path):
        state = runstate.build_runstate({"plan_manifest": manifest_rows()})
        runstate.write_runstate(vault, TODAY, state)
        todoist = recurring_task()
        action = ra.apply_action(vault, TODAY, "unassign", "Press", {},
                                 todoist=todoist, store=None)
        ra.undo_action(vault, TODAY, action["id"], todoist=todoist)
        due = todoist.tasks["task-1"]["due"]
        assert due["datetime"] == "2026-08-13T16:00:00"
        assert due["string"] == "every day at 16:00"

    def test_recurring_date_only_advance_uses_due_date(self, vault: Path):
        state = runstate.build_runstate({"plan_manifest": manifest_rows()})
        runstate.write_runstate(vault, TODAY, state)
        todoist = recurring_task({"date": "2026-08-13", "is_recurring": True,
                                  "string": "every 2 days"})
        action = ra.apply_action(vault, TODAY, "unassign", "Press", {},
                                 todoist=todoist, store=None)
        assert action["status"] == "applied"
        assert ("reschedule_task_date", "task-1", "2026-08-15") in todoist.calls

    def test_unsupported_recurrence_fails_closed_with_no_write(self, vault: Path):
        state = runstate.build_runstate({"plan_manifest": manifest_rows()})
        runstate.write_runstate(vault, TODAY, state)
        todoist = recurring_task({"datetime": "2026-08-13T16:00:00",
                                  "is_recurring": True, "string": "every nope"})
        action = ra.apply_action(vault, TODAY, "unassign", "Press", {},
                                 todoist=todoist, store=None)
        # apply-time refusal is journaled, never applied: the linked vault
        # step (if any) is compensated back, so no net source write remains
        assert action["status"] in ("compensated", "failed")
        # no WRITE call reached the task (reads like get_task are fine)
        assert not any(c[0] in ("reschedule_task", "reschedule_task_datetime",
                                "reschedule_task_date", "clear_task_date",
                                "close_task", "update_task") for c in todoist.calls)
        assert todoist.tasks["task-1"]["due"]["string"] == "every nope"
        assert not any(s.get("applied") and not s.get("compensated")
                       for s in action["steps"])
        # the vault row was flipped then compensated back to exact bytes
        assert (vault / "Projects" / "Press.md").read_text() == NOTE_TEXT
        journal = ra.load_journal(vault, TODAY)
        assert journal["actions"][0]["status"] in ("compensated", "failed")

    def test_readback_completion_change_compensates(self, vault: Path):
        state = runstate.build_runstate({"plan_manifest": manifest_rows()})
        runstate.write_runstate(vault, TODAY, state)
        todoist = recurring_task()
        original_reschedule = todoist.reschedule_task_datetime

        def corrupt(task_id: str, due_datetime: str) -> dict:
            # server misbehaves: advancing marks the task completed
            todoist.tasks[task_id]["is_completed"] = True
            return original_reschedule(task_id, due_datetime)

        todoist.reschedule_task_datetime = corrupt
        action = ra.apply_action(vault, TODAY, "unassign", "Press", {},
                                 todoist=todoist, store=None)
        assert action["status"] in ("compensated", "failed")
        # the due write was reversed back to the before-image
        assert todoist.tasks["task-1"]["due"]["datetime"] == "2026-08-13T16:00:00"
        step = next(s for s in action["steps"] if s["kind"] == "todoist.unassign")
        assert step.get("applied") is True
        assert step.get("compensated") is True

    def test_unassign_does_not_touch_deferrals(self, vault: Path):
        import deferrals
        todoist, _ = fresh_clients()
        ra.apply_action(vault, TODAY, "unassign", "Press", {},
                        todoist=todoist, store=None)
        assert deferrals.load_deferrals(vault) == {}


# ---------------------------------------------------------------------------
# Wire behavior — /runtime-actions for the four final verbs
# ---------------------------------------------------------------------------

@pytest.fixture()
def route_vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault-root"
    (vault / "Projects").mkdir(parents=True)
    (vault / "Projects" / "Press.md").write_text(NOTE_TEXT, encoding="utf-8")
    runstate.write_runstate(vault, TODAY,
                            runstate.build_runstate({
                                "plan_manifest": manifest_rows(),
                                # FEEDBACK-24: route verbs are gated on an
                                # explicit Day Setup confirm.
                                "day_setup_confirmed": True,
                            }))
    return vault


@pytest.fixture()
def harness(route_vault: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(main_mod.gather, "effective_date", lambda _n: TODAY)
    app = main_mod.create_app(vault_root=route_vault)
    todoist, store = fresh_clients()
    app.state.build_commit_clients = lambda v, config: (todoist, store)
    client = TestClient(app)
    client.app_token = app.state.token
    return client, todoist, store


def _auth(client: TestClient) -> dict:
    return {"X-TDTB-Token": client.app_token}


class TestWire:
    def test_route_done_applies_and_lists(self, harness):
        client, todoist, _ = harness
        r = client.post("/runtime-actions", headers=_auth(client),
                        json={"verb": "done", "target": "Press"})
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "applied"
        assert todoist.tasks["task-1"]["is_completed"] is True
        assert len(client.get("/runtime-actions", headers=_auth(client))
                   .json()["actions"]) == 1

    def test_route_drop_undo_roundtrip(self, harness):
        client, _, _ = harness
        action = client.post("/runtime-actions", headers=_auth(client),
                             json={"verb": "drop_from_plan", "target": "Press"}).json()
        assert action["status"] == "applied"
        assert runstate.read_dropped(Path(client.app.state.vault_root), TODAY)
        r = client.post(f"/runtime-actions/{action['id']}/undo", headers=_auth(client))
        assert r.status_code == 200
        assert r.json()["status"] == "undone"
        assert runstate.read_dropped(Path(client.app.state.vault_root), TODAY) == []

    def test_route_unassign_non_recurring(self, harness):
        client, todoist, _ = harness
        r = client.post("/runtime-actions", headers=_auth(client),
                        json={"verb": "unassign", "target": "Press"})
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "applied"
        assert todoist.tasks["task-1"]["due"] is None
        text = (Path(client.app.state.vault_root) / "Projects" / "Press.md").read_text()
        assert "assigned: false" in text

    def test_route_delete(self, harness):
        client, todoist, store = harness
        r = client.post("/runtime-actions", headers=_auth(client),
                        json={"verb": "delete", "target": "Press"})
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "applied"
        assert "task-1" not in todoist.tasks
        assert "ev-1" not in store.events

    def test_route_unknown_verb_is_422(self, harness):
        client, _, _ = harness
        r = client.post("/runtime-actions", headers=_auth(client),
                        json={"verb": "explode", "target": "Press"})
        assert r.status_code == 422
