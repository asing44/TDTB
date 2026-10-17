"""Route tests for /runtime-batches (B5) — fakes injected through
``app.state.build_commit_clients`` exactly as ``test_runtime_actions_routes``
does: no live clients, no network, no real source writes.

Proofs: all three endpoints refuse without Day Setup; apply reports per-item
statuses when one item fails and never stops later items; retry re-invokes only
the unfinished items; undo returns the conflict report; an unknown batch is a
422; and ``restore_today``/``hide_always`` are reachable through both the
single-action route and the batch route.
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))
import exclusion_settings as es  # noqa: E402
import main as main_mod  # noqa: E402
import runstate  # noqa: E402
import tdtb_gather as gather  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent))
from test_runtime_actions import FakeStore  # noqa: E402
from test_runtime_batches import (  # noqa: E402
    FaultyTodoist,
    make_todoist,
    manifest_rows,
)


def _today():
    return gather.effective_date(datetime.now())


def _write_state(confirmed: bool = True) -> None:
    runstate.write_runstate(_today(), runstate.build_runstate({
        "plan_manifest": manifest_rows(),
        # FEEDBACK-24: batch verbs write external state — same closed gate.
        "day_setup_confirmed": confirmed,
    }))


@pytest.fixture
def vault(tmp_path) -> Path:
    root = tmp_path / "vault-root"
    (root / "Projects").mkdir(parents=True)
    _write_state()
    return root


def _client(todoist=None, store=None) -> TestClient:
    app = main_mod.create_app()
    app.state.build_commit_clients = lambda config: (todoist, store)
    client = TestClient(app)
    client.app_token = app.state.token
    return client


@pytest.fixture
def harness(vault) -> tuple[TestClient, FaultyTodoist, FakeStore]:
    todoist = make_todoist()
    store = FakeStore({})
    client = _client(todoist, store)
    return client, todoist, store


def _auth(client: TestClient) -> dict:
    return {"X-TDTB-Token": client.app_token}


def _batch(*names: str, verb: str = "done") -> dict:
    return {"items": [{"verb": verb, "target": n, "args": {}} for n in names]}


def _statuses(body: dict) -> list:
    return [r["status"] for r in body["results"]]


class TestDaySetupGate:
    def test_all_three_endpoints_refuse_without_day_setup(self, vault):
        _write_state(confirmed=False)
        client = _client(make_todoist(), FakeStore({}))
        headers = _auth(client)

        assert client.post("/runtime-batches", headers=headers,
                           json=_batch("Alpha")).status_code == 409
        assert client.post("/runtime-batches/rb-none/retry",
                           headers=headers).status_code == 409
        assert client.post("/runtime-batches/rb-none/undo",
                           headers=headers).status_code == 409


class TestApplyBatchRoute:
    def test_one_forced_failure_returns_per_item_statuses(self, vault):
        todoist = make_todoist(fail_get={"task-b"})
        client = _client(todoist, FakeStore({}))
        r = client.post("/runtime-batches", headers=_auth(client),
                        json=_batch("Alpha", "Beta", "Gamma"))
        assert r.status_code == 200
        body = r.json()
        assert body["batch_id"].startswith("rb-")
        assert _statuses(body) == ["applied", "failed", "applied"]
        assert [item["index"] for item in body["results"]] == [0, 1, 2]
        assert body["results"][1]["error"]
        # the middle failure did not stop the later item
        assert todoist.tasks["task-c"]["is_completed"] is True

    def test_missing_items_is_422(self, harness):
        client, _, _ = harness
        r = client.post("/runtime-batches", headers=_auth(client), json={})
        assert r.status_code == 422

    def test_unknown_target_is_reported_refused(self, harness):
        client, _, _ = harness
        r = client.post("/runtime-batches", headers=_auth(client),
                        json=_batch("Ghost"))
        assert r.status_code == 200
        assert _statuses(r.json()) == ["refused"]
        assert r.json()["results"][0]["error"]


class TestRetryBatchRoute:
    def test_retry_reruns_only_the_failed_item(self, vault):
        todoist = make_todoist(fail_get={"task-b"})
        client = _client(todoist, FakeStore({}))
        headers = _auth(client)
        applied = client.post("/runtime-batches", headers=headers,
                              json=_batch("Alpha", "Beta", "Gamma")).json()
        assert _statuses(applied) == ["applied", "failed", "applied"]

        closes_before = [c for c in todoist.calls if c[0] == "close_task"]
        todoist.fail_get.discard("task-b")
        retried = client.post(f"/runtime-batches/{applied['batch_id']}/retry",
                              headers=headers)
        assert retried.status_code == 200
        body = retried.json()
        assert body["batch_id"] == applied["batch_id"]
        assert _statuses(body) == ["applied", "applied", "applied"]
        closes_after = [c for c in todoist.calls if c[0] == "close_task"]
        # only Beta was re-invoked; already-applied items were not touched
        assert len(closes_after) == len(closes_before) + 1
        assert closes_after[-1] == ("close_task", "task-b")

    def test_unknown_batch_is_422(self, harness):
        client, _, _ = harness
        r = client.post("/runtime-batches/rb-missing/retry",
                        headers=_auth(client))
        assert r.status_code == 422
        assert "unknown batch" in r.json()["detail"]


class TestUndoBatchRoute:
    def test_undo_returns_conflict_report(self, vault):
        todoist = make_todoist()
        client = _client(todoist, FakeStore({}))
        headers = _auth(client)
        applied = client.post("/runtime-batches", headers=headers,
                              json=_batch("Alpha", "Beta")).json()
        # A later edit outside the batch reopens Alpha: its reverse conflicts.
        todoist.tasks["task-a"]["is_completed"] = False

        r = client.post(f"/runtime-batches/{applied['batch_id']}/undo",
                        headers=headers)
        assert r.status_code == 200
        report = r.json()
        assert report["batch_id"] == applied["batch_id"]
        assert [item["target"] for item in report["reverted"]] == ["Beta"]
        assert [item["target"] for item in report["conflicts"]] == ["Alpha"]
        assert report["conflicts"][0]["reason"]
        assert report["non_reversible"] == []
        assert todoist.tasks["task-b"]["is_completed"] is False

    def test_unknown_batch_is_422(self, harness):
        client, _, _ = harness
        r = client.post("/runtime-batches/rb-missing/undo",
                        headers=_auth(client))
        assert r.status_code == 422
        assert "unknown batch" in r.json()["detail"]


class TestVerbParity:
    def test_restore_today_reachable_via_both_routes(self, harness):
        client, _, _ = harness
        headers = _auth(client)

        dropped = client.post("/runtime-actions", headers=headers,
                              json={"verb": "drop_from_plan",
                                    "target": "Alpha"})
        assert dropped.status_code == 200
        single = client.post("/runtime-actions", headers=headers,
                             json={"verb": "restore_today",
                                   "target": "todoist:task-a"})
        assert single.status_code == 200
        assert single.json()["status"] == "applied"

        # Drop a second item, then restore it through the batch route instead.
        client.post("/runtime-actions", headers=headers,
                    json={"verb": "drop_from_plan", "target": "Beta"})
        batched = client.post(
            "/runtime-batches", headers=headers,
            json={"items": [{"verb": "restore_today",
                             "target": "todoist:task-b", "args": {}}]})
        assert batched.status_code == 200
        assert _statuses(batched.json()) == ["applied"]

    def test_hide_always_reachable_via_both_routes(self, harness):
        client, _, _ = harness
        headers = _auth(client)

        single = client.post("/runtime-actions", headers=headers,
                             json={"verb": "hide_always", "target": "Alpha"})
        assert single.status_code == 200
        assert single.json()["status"] == "applied"
        assert {e.id for e in es.read_settings().settings.todoist} == {"task-a"}

        batched = client.post("/runtime-batches", headers=headers,
                              json=_batch("Beta", verb="hide_always"))
        assert batched.status_code == 200
        assert _statuses(batched.json()) == ["applied"]
        assert {e.id for e in es.read_settings().settings.todoist} == {
            "task-a", "task-b"}


class TestBatchAuth:
    def test_token_required(self, harness):
        client, _, _ = harness
        assert client.post("/runtime-batches",
                           json=_batch("Alpha")).status_code in (401, 403)
        assert client.post("/runtime-batches/rb-x/retry").status_code in (401, 403)
        assert client.post("/runtime-batches/rb-x/undo").status_code in (401, 403)
