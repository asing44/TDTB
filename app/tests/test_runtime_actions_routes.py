"""Route tests for /runtime-actions (T20) — fakes injected via
app.state.build_commit_clients, no live clients, tmp vault."""
from __future__ import annotations

import sys
from datetime import date, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))
import main as main_mod  # noqa: E402
import runstate  # noqa: E402
import tdtb_gather as gather  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent))
from test_runtime_actions import (  # noqa: E402
    FakeStore, FakeTodoist, NOTE_TEXT, fresh_clients, manifest_rows,
)


@pytest.fixture
def vault(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "vault-root"
    (root / "Projects").mkdir(parents=True)
    (root / "Projects" / "Press.md").write_text(NOTE_TEXT, encoding="utf-8")
    today = gather.effective_date(datetime.now())
    state = runstate.build_runstate({
        "plan_manifest": manifest_rows(),
        # FEEDBACK-24: route verbs are gated on an explicit Day Setup confirm.
        "day_setup_confirmed": True,
    })
    runstate.write_runstate(root, today, state)
    return root


@pytest.fixture
def harness(vault) -> tuple[TestClient, FakeTodoist, FakeStore]:
    app = main_mod.create_app(vault_root=vault)
    todoist, store = fresh_clients()
    app.state.build_commit_clients = lambda v, config: (todoist, store)
    c = TestClient(app)
    c.app_token = app.state.token
    return c, todoist, store


def _auth(client: TestClient) -> dict:
    return {"X-TDTB-Token": client.app_token}


class TestRuntimeActionRoutes:
    def test_token_required(self, harness):
        client, _, _ = harness
        assert client.post("/runtime-actions",
                           json={"verb": "complete", "target": "Press"}).status_code in (401, 403)
        assert client.get("/runtime-actions").status_code in (401, 403)

    def test_apply_and_list(self, harness):
        client, todoist, _ = harness
        r = client.post("/runtime-actions", headers=_auth(client),
                        json={"verb": "complete", "target": "Press"})
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "applied"
        assert todoist.tasks["task-1"]["is_completed"] is True
        listing = client.get("/runtime-actions", headers=_auth(client)).json()
        assert len(listing["actions"]) == 1

    def test_undo_roundtrip(self, harness):
        client, todoist, _ = harness
        action = client.post("/runtime-actions", headers=_auth(client),
                             json={"verb": "complete", "target": "Press"}).json()
        r = client.post(f"/runtime-actions/{action['id']}/undo", headers=_auth(client))
        assert r.status_code == 200
        assert r.json()["status"] == "undone"
        assert todoist.tasks["task-1"]["is_completed"] is False

    def test_unknown_target_is_422(self, harness):
        client, _, _ = harness
        r = client.post("/runtime-actions", headers=_auth(client),
                        json={"verb": "complete", "target": "Ghost"})
        assert r.status_code == 422
        assert "not in today's plan" in r.json()["detail"]

    def test_unavailable_surface_is_503(self, vault):
        app = main_mod.create_app(vault_root=vault)
        todoist, _ = fresh_clients()
        app.state.build_commit_clients = lambda v, config: (todoist, None)
        client = TestClient(app)
        client.app_token = app.state.token
        r = client.post("/runtime-actions", headers=_auth(client),
                        json={"verb": "skip_today", "target": "Press"})
        assert r.status_code == 503
        assert "surface unavailable: calendar" in r.json()["detail"]

    def test_idempotent_repeat_returns_duplicate(self, harness):
        client, _, _ = harness
        first = client.post("/runtime-actions", headers=_auth(client),
                            json={"verb": "complete", "target": "Press"}).json()
        second = client.post("/runtime-actions", headers=_auth(client),
                             json={"verb": "complete", "target": "Press"}).json()
        assert second["id"] == first["id"]
        assert second["duplicate"] is True
