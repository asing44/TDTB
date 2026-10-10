"""U5 backend B1 — prompt drafts/opt-ins wired into Day Setup reads/saves.

The morning prompts are private local runtime state (``prompt_state.py``).
These route tests pin:

- captures persist to the exact-logical-day local draft store, never the
  legacy runstate prompt keys (which stay INERT);
- a draft-only / opt-in-only save never confirms Day Setup, while a
  non-prompt field (or the pre-U5 bare ``{}`` save) still does;
- the additive opt-in map round-trips with optimistic conflict 409 and
  validation 422;
- reads override inert legacy runstate prompt text with local values.

Fixture-only: the autouse ``_isolated_app_home`` fixture (conftest) puts
``TDTB_HOME`` in a per-test tmp dir, so no real machine state, vault, provider,
or credential is touched. Every prompt string is synthetic.
"""
from __future__ import annotations

import sys
from datetime import date, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))
import main as main_mod  # noqa: E402
import prompt_state  # noqa: E402
import runstate  # noqa: E402
import tdtb_gather as gather  # noqa: E402


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


def _drafts() -> dict:
    return dict(prompt_state.load_drafts(_today().isoformat()).drafts)


# ---------------------------------------------------------------------------
# Local storage + no day_setup_confirmed on prompt-only saves
# ---------------------------------------------------------------------------

def test_captures_only_save_does_not_confirm(client, vault):
    r = client.post("/day-setup", json={
        "captures": {"intention": "synthetic intent",
                     "megan_nicety": "synthetic nicety"},
    }, headers=_auth(client))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["day_setup_confirmed"] is False
    assert runstate.is_day_setup_confirmed(vault, _today()) is False
    assert _drafts() == {
        "intention": "synthetic intent",
        "megan_nicety": "synthetic nicety",
    }
    assert body["day_setup"]["intention"] == "synthetic intent"


def test_optins_only_save_does_not_confirm(client, vault):
    r = client.post("/day-setup", json={
        "optins": {"intention": True, "megan_nicety": False,
                   "stoic_intention": False},
        "optins_revision": 0,
    }, headers=_auth(client))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["day_setup_confirmed"] is False
    assert body["optins_revision"] == 1
    assert body["optins"] == {
        "intention": True, "megan_nicety": False, "stoic_intention": False,
    }
    assert runstate.is_day_setup_confirmed(vault, _today()) is False


def test_nonprompt_save_still_confirms(client, vault):
    r = client.post("/day-setup", json={"anchor": "09:00"}, headers=_auth(client))
    assert r.status_code == 200
    assert r.json()["day_setup_confirmed"] is True
    assert runstate.is_day_setup_confirmed(vault, _today()) is True


def test_empty_body_still_confirms(client, vault):
    # Backward compatibility: the pre-U5 bare save still confirms.
    r = client.post("/day-setup", json={}, headers=_auth(client))
    assert r.status_code == 200
    assert r.json()["day_setup_confirmed"] is True


def test_capture_patch_omit_preserves_and_empty_clears(client):
    client.post("/day-setup", json={"captures": {
        "intention": "A", "megan_nicety": "B"}}, headers=_auth(client))
    # Omitted key preserves; an explicit empty string clears.
    client.post("/day-setup", json={"captures": {"megan_nicety": ""}},
                headers=_auth(client))
    assert _drafts() == {"intention": "A"}


# ---------------------------------------------------------------------------
# Reads override inert legacy runstate prompt text
# ---------------------------------------------------------------------------

def test_read_overrides_inert_legacy_runstate_prompt_text(client, vault):
    today = _today()
    runstate.update_runstate(vault, today, {
        "intention": "LEGACY-INERT-TEXT",
        "megan_nicety": "LEGACY-INERT-TEXT",
        "stoic_intention": "LEGACY-INERT-TEXT",
    })
    # No local drafts yet -> the legacy text is never echoed.
    r = client.get("/plan-inputs")
    assert r.status_code == 200, r.text
    day_setup = r.json()["day_setup"]
    assert day_setup.get("intention", "") == ""
    assert day_setup.get("megan_nicety", "") == ""
    assert day_setup.get("stoic_intention", "") == ""
    # After a local save, reads surface the local value.
    client.post("/day-setup", json={"captures": {"intention": "local intent"}},
                headers=_auth(client))
    day_setup = client.get("/plan-inputs").json()["day_setup"]
    assert day_setup["intention"] == "local intent"


# ---------------------------------------------------------------------------
# Additive opt-ins: conflict 409 / validation 422
# ---------------------------------------------------------------------------

def test_optins_conflict_is_409_with_revisions(client):
    client.post("/day-setup", json={
        "optins": {"intention": True}, "optins_revision": 0,
    }, headers=_auth(client))
    r = client.post("/day-setup", json={
        "optins": {"megan_nicety": True}, "optins_revision": 0,
    }, headers=_auth(client))
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail["code"] == "prompt_optins_conflict"
    assert detail["expected_revision"] == 0
    assert detail["current_revision"] == 1


def test_optins_missing_revision_is_422(client):
    r = client.post("/day-setup", json={"optins": {"intention": True}},
                    headers=_auth(client))
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "prompt_optins_invalid"


def test_optins_invalid_value_is_422(client):
    r = client.post("/day-setup", json={
        "optins": {"intention": 1}, "optins_revision": 0,
    }, headers=_auth(client))
    assert r.status_code == 422
    assert not prompt_state.optins_path().exists()


def test_capture_invalid_type_is_422(client):
    r = client.post("/day-setup", json={
        "captures": {"intention": ["not", "a", "string"]},
    }, headers=_auth(client))
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "prompt_drafts_invalid"
    assert not prompt_state.drafts_path(_today().isoformat()).exists()


def test_prompt_content_never_in_error(client):
    secret = "SYNTHETIC-SECRET-7c1f"
    r = client.post("/day-setup", json={"captures": {"intention": [secret]}},
                    headers=_auth(client))
    assert r.status_code == 422
    assert secret not in r.text
