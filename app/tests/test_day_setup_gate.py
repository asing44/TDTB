"""FEEDBACK-24 — Day Setup confirmation gate.

The Day Setup gate must be satisfied ONLY by a successful POST /day-setup for
the current planning day. Skeleton runstate keys (gather materialisation),
Drop-from-plan writes, commit-ledger persists, and any other unrelated
runstate write must NEVER satisfy it — and the external write paths
(/commit?mode=live, /runtime-actions apply/undo) must fail closed with an
actionable status when the confirmation is missing.
"""
from __future__ import annotations

import sys
from datetime import date, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))
import main as main_mod  # noqa: E402
import runstate  # noqa: E402
import shadow  # noqa: E402
import tdtb_gather as gather  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent))
from test_main_api import (  # noqa: E402
    FakeLiveStore,
    FakeLiveTodoist,
    LIVE_DIGEST,
    LIVE_SEQUENCE,
    _fake_live_state,
)

GATE_DETAIL = "Day Setup not confirmed"


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    v = tmp_path / "vault-root"
    v.mkdir()
    return v


@pytest.fixture
def client(vault) -> TestClient:
    app = main_mod.create_app(vault_root=vault)
    c = TestClient(app)
    c.app_token = app.state.token
    return c


def _auth(client: TestClient) -> dict:
    return {"X-TDTB-Token": client.app_token}


def _today() -> date:
    return gather.effective_date(datetime.now())


class TestConfirmationSource:
    """Only a successful POST /day-setup confirms; unrelated writes never do."""

    def test_fresh_vault_not_confirmed(self, vault):
        assert runstate.is_day_setup_confirmed(vault, _today()) is False

    def test_skeleton_runstate_not_confirmed(self, client, vault):
        # /gather materialises the full skeleton — must not confirm.
        r = client.post("/gather", headers=_auth(client))
        assert r.status_code == 200
        assert runstate.is_day_setup_confirmed(vault, _today()) is False

    def test_drop_write_not_confirmed(self, vault):
        runstate.update_runstate(vault, _today(), {
            "dropped": [{"identity": "vault:x", "name": "X",
                         "dropped_at": "2026-08-14T00:00:00Z"}],
        })
        assert runstate.is_day_setup_confirmed(vault, _today()) is False

    def test_commit_ledger_write_not_confirmed(self, vault):
        runstate.update_runstate(vault, _today(), {
            "commit_ledger": {"surfaces": {"todoist": {"status": "ok"}}},
        })
        assert runstate.is_day_setup_confirmed(vault, _today()) is False

    def test_billed_ledger_write_not_confirmed(self, vault):
        runstate.update_runstate(vault, _today(), {"billed_calls": 2})
        assert runstate.is_day_setup_confirmed(vault, _today()) is False

    def test_sequence_side_effect_write_not_confirmed(self, vault):
        # /sequence persists overlap_grants / pinned_rows / fingerprint —
        # an unrelated runstate write that must not confirm either.
        runstate.update_runstate(vault, _today(), {
            "overlap_grants": [], "pinned_rows": [],
            "planning_config_fingerprint": "x" * 64,
        })
        assert runstate.is_day_setup_confirmed(vault, _today()) is False

    def test_legacy_note_without_key_not_confirmed(self, vault):
        # A pre-change runstate note carries no key — fail closed.
        state = runstate.build_runstate({})
        state.pop(runstate.DAY_SETUP_CONFIRMED_KEY, None)
        runstate.write_runstate(vault, _today(), state)
        assert runstate.is_day_setup_confirmed(vault, _today()) is False

    def test_confirmation_scoped_to_planning_day(self, vault):
        today = _today()
        other_day = date(2020, 1, 1)
        runstate.write_runstate(
            vault, other_day,
            runstate.build_runstate({runstate.DAY_SETUP_CONFIRMED_KEY: True}),
        )
        assert runstate.is_day_setup_confirmed(vault, other_day) is True
        assert runstate.is_day_setup_confirmed(vault, today) is False

    def test_day_setup_post_is_the_only_confirmation(self, client, vault):
        r = client.post("/day-setup", json={"anchor": "09:00"},
                        headers=_auth(client))
        assert r.status_code == 200
        assert r.json()["day_setup_confirmed"] is True
        assert runstate.is_day_setup_confirmed(vault, _today()) is True

    def test_plan_inputs_exposes_confirmation(self, client, vault):
        before = client.get("/plan-inputs").json()
        assert before["day_setup_confirmed"] is False
        client.post("/day-setup", json={"anchor": "09:00"}, headers=_auth(client))
        after = client.get("/plan-inputs").json()
        assert after["day_setup_confirmed"] is True


class TestWritePathFailClosed:
    def _seed_live(self, vault: Path) -> None:
        (vault / "P").mkdir(parents=True, exist_ok=True)
        (vault / "P/Garage.md").write_text(
            "---\nassigned: false\n---\nbody\n", encoding="utf-8"
        )
        (vault / "30 - Daily").mkdir(parents=True, exist_ok=True)
        (vault / "30 - Daily/2026-07-12.md").write_text(
            "# Journal\n", encoding="utf-8"
        )

    def test_live_commit_fails_closed_without_setup(self, client, vault, monkeypatch):
        self._seed_live(vault)
        monkeypatch.setattr(gather, "effective_date", lambda now: date(2026, 7, 12))
        before = {p: p.read_bytes() for p in vault.rglob("*") if p.is_file()}
        r = client.post(
            "/commit?mode=live", headers=_auth(client),
            json={"digest": LIVE_DIGEST, "sequence": LIVE_SEQUENCE, "config": {}},
        )
        assert r.status_code == 409
        assert GATE_DETAIL in r.json()["detail"]
        after = {p: p.read_bytes() for p in vault.rglob("*") if p.is_file()}
        assert before == after  # nothing written

    def test_live_commit_succeeds_after_day_setup(self, client, vault, monkeypatch):
        self._seed_live(vault)
        monkeypatch.setattr(gather, "effective_date", lambda now: date(2026, 7, 12))
        monkeypatch.setattr(shadow, "gather_live_state", _fake_live_state)
        client.app.state.build_commit_clients = (
            lambda v, cfg: (FakeLiveTodoist(), FakeLiveStore())
        )
        client.post("/day-setup", json={"anchor": "09:00"}, headers=_auth(client))
        r = client.post(
            "/commit?mode=live", headers=_auth(client),
            json={"digest": LIVE_DIGEST, "sequence": LIVE_SEQUENCE, "config": {}},
        )
        assert r.status_code == 200
        assert r.json()["ok"] is True

    def test_runtime_actions_fail_closed_without_setup(self, client, vault):
        r = client.post("/runtime-actions", headers=_auth(client),
                        json={"verb": "complete", "target": "Press"})
        assert r.status_code == 409
        assert GATE_DETAIL in r.json()["detail"]

    def test_runtime_actions_undo_fail_closed_without_setup(self, client, vault):
        r = client.post("/runtime-actions/abc/undo", headers=_auth(client))
        assert r.status_code == 409
        assert GATE_DETAIL in r.json()["detail"]
