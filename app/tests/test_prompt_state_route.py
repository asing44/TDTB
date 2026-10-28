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
def client() -> TestClient:
    app = main_mod.create_app()
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

def test_captures_only_save_does_not_confirm(client):
    r = client.post("/day-setup", json={
        "captures": {"intention": "synthetic intent",
                     "megan_nicety": "synthetic nicety"},
    }, headers=_auth(client))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["day_setup_confirmed"] is False
    assert runstate.is_day_setup_confirmed(_today()) is False
    assert _drafts() == {
        "intention": "synthetic intent",
        "megan_nicety": "synthetic nicety",
    }
    assert body["day_setup"]["intention"] == "synthetic intent"


def test_captures_only_save_echoes_clean_optins(client):
    # Backward compatibility: a clean (absent) store still echoes the all-false
    # default at revision 0, which the UI hydrates as available.
    r = client.post("/day-setup", json={"captures": {"intention": "x"}},
                    headers=_auth(client))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["optins"] == {
        "intention": False, "megan_nicety": False, "stoic_intention": False,
    }
    assert body["optins_revision"] == 0


def test_captures_only_save_with_corrupt_optins_echoes_unavailable(client):
    """A corrupt opt-in store + captures-only save must not confirm the day,
    must not fabricate an all-false opt-in echo, and must preserve the bytes."""
    target = prompt_state.optins_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    corrupt = (
        '{"version": 1, "revision": 0, "optins": {"intention": true},'
        ' "optins": {}}'
    )
    target.write_text(corrupt, encoding="utf-8")

    r = client.post("/day-setup", json={
        "captures": {"intention": "synthetic intent"},
    }, headers=_auth(client))
    assert r.status_code == 200, r.text
    body = r.json()
    # The echo omits the opt-in block: prefs stay unavailable, never all-false.
    assert "optins" not in body
    assert "optins_revision" not in body
    assert main_mod._PROMPT_OPTINS_READ_WARNING in body.get("prompt_warnings", [])
    # No day confirmation and no write of the corrupt bytes.
    assert body["day_setup_confirmed"] is False
    assert runstate.is_day_setup_confirmed(_today()) is False
    assert target.read_text(encoding="utf-8") == corrupt
    # The draft still saved.
    assert _drafts() == {"intention": "synthetic intent"}


def test_optins_only_save_does_not_confirm(client):
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
    assert runstate.is_day_setup_confirmed(_today()) is False


def test_nonprompt_save_still_confirms(client):
    r = client.post("/day-setup", json={"anchor": "09:00"}, headers=_auth(client))
    assert r.status_code == 200
    assert r.json()["day_setup_confirmed"] is True
    assert runstate.is_day_setup_confirmed(_today()) is True


def test_empty_body_still_confirms(client):
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

def test_read_overrides_inert_legacy_runstate_prompt_text(client):
    today = _today()
    runstate.update_runstate(today, {
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


# ---------------------------------------------------------------------------
# Day-scoped description-skip flags through the same Day Setup seam
# ---------------------------------------------------------------------------

SKIPS_OFF = {"intention": False, "megan_nicety": False, "stoic_intention": False}


def _skips() -> dict:
    return dict(
        prompt_state.load_description_skips(_today().isoformat()).skips
    )


def test_description_skips_persist_and_echo(client):
    r = client.post("/day-setup", json={
        "description_skips": {"intention": False, "megan_nicety": True,
                              "stoic_intention": False},
    }, headers=_auth(client))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["day_setup"]["description_skips"] == {
        "intention": False, "megan_nicety": True, "stoic_intention": False,
    }
    assert _skips() == {
        "intention": False, "megan_nicety": True, "stoic_intention": False,
    }


def test_description_skips_only_save_does_not_confirm_or_write_runstate(client):
    runstate_path = runstate.runstate_read_path(_today())
    assert runstate_path.is_file() is False

    r = client.post("/day-setup", json={
        "description_skips": {"stoic_intention": True},
    }, headers=_auth(client))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["day_setup_confirmed"] is False
    assert runstate.is_day_setup_confirmed(_today()) is False
    # No dated runstate note was materialized by a prompt-only save.
    assert runstate_path.is_file() is False


def test_description_skips_map_is_a_full_replace_per_save(client):
    client.post("/day-setup", json={
        "description_skips": {"megan_nicety": True, "stoic_intention": True},
    }, headers=_auth(client))
    r = client.post("/day-setup", json={
        "description_skips": {"megan_nicety": True},
    }, headers=_auth(client))
    assert r.status_code == 200, r.text
    assert _skips() == {
        "intention": False, "megan_nicety": True, "stoic_intention": False,
    }


def test_description_skips_omitted_preserves_and_echoes_current_state(client):
    client.post("/day-setup", json={
        "description_skips": {"stoic_intention": True},
    }, headers=_auth(client))
    # A captures-only save omits the flag map: the stored flags are preserved
    # and still echoed so the checkbox keeps its state.
    r = client.post("/day-setup", json={"captures": {"intention": "synthetic"}},
                    headers=_auth(client))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["day_setup_confirmed"] is False
    assert body["day_setup"]["description_skips"]["stoic_intention"] is True
    assert _skips()["stoic_intention"] is True


def test_description_skips_non_boolean_is_422(client):
    r = client.post("/day-setup", json={
        "description_skips": {"megan_nicety": 1},
    }, headers=_auth(client))
    assert r.status_code == 422
    assert not prompt_state.description_skips_path(_today().isoformat()).exists()


def test_description_skips_custom_prompt_key_is_422_content_free(client):
    key = "custom-m1abcde-xy9"
    r = client.post("/day-setup", json={
        "description_skips": {key: True},
    }, headers=_auth(client))
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "prompt_description_skips_invalid"
    assert not prompt_state.description_skips_path(_today().isoformat()).exists()


def test_description_skips_corrupt_store_is_409_and_preserved(client):
    target = prompt_state.description_skips_path(_today().isoformat())
    target.parent.mkdir(parents=True, exist_ok=True)
    corrupt = '{"version": 1, "day": "2026-01-01", "skips": {"megan_nicety": true}, "skips": {}}'
    target.write_text(corrupt, encoding="utf-8")

    r = client.post("/day-setup", json={
        "description_skips": {"megan_nicety": True},
    }, headers=_auth(client))
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "prompt_description_skips_storage_error"
    assert target.read_text(encoding="utf-8") == corrupt
    assert runstate.is_day_setup_confirmed(_today()) is False


def test_description_skips_corrupt_store_omits_the_echo(client):
    target = prompt_state.description_skips_path(_today().isoformat())
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("{not json", encoding="utf-8")

    r = client.post("/day-setup", json={"captures": {"intention": "synthetic"}},
                    headers=_auth(client))
    assert r.status_code == 200, r.text
    body = r.json()
    # Unreadable flags are never fabricated as all-false.
    assert body["day_setup"]["description_skips"] is None
    assert main_mod._PROMPT_DESCRIPTION_SKIPS_READ_WARNING in body.get(
        "prompt_warnings", []
    )
    assert target.read_text(encoding="utf-8") == "{not json"


def test_description_skips_all_false_leaves_the_echo_empty(client):
    client.post("/day-setup", json={
        "description_skips": {"stoic_intention": True},
    }, headers=_auth(client))
    r = client.post("/day-setup", json={
        "description_skips": {"stoic_intention": False},
    }, headers=_auth(client))
    assert r.status_code == 200, r.text
    # Nothing is skipped, so no flag map is echoed (the key is present only as
    # the usual ``None`` default), and an empty Day Setup stays empty.
    assert r.json()["day_setup"]["description_skips"] is None
    assert _skips() == SKIPS_OFF


# ---------------------------------------------------------------------------
# Day-scoped disabled-prompt flags through the same Day Setup seam
# ---------------------------------------------------------------------------

DISABLED_OFF = {"intention": False, "megan_nicety": False,
                "stoic_intention": False}


def _disabled() -> dict:
    return dict(prompt_state.load_prompt_disabled(_today().isoformat()).disabled)


def test_prompt_disabled_persist_and_echo(client):
    r = client.post("/day-setup", json={
        "prompt_disabled": {"intention": False, "megan_nicety": True,
                            "stoic_intention": False},
    }, headers=_auth(client))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["day_setup"]["prompt_disabled"] == {
        "intention": False, "megan_nicety": True, "stoic_intention": False,
    }
    assert _disabled() == {
        "intention": False, "megan_nicety": True, "stoic_intention": False,
    }


def test_prompt_disabled_only_save_does_not_confirm_or_write_runstate(client):
    runstate_path = runstate.runstate_read_path(_today())
    assert runstate_path.is_file() is False

    r = client.post("/day-setup", json={
        "prompt_disabled": {"megan_nicety": True},
    }, headers=_auth(client))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["day_setup_confirmed"] is False
    assert runstate.is_day_setup_confirmed(_today()) is False
    # A flags-only save is prompt-only: it never materializes a runstate note.
    assert runstate_path.is_file() is False


def test_prompt_disabled_all_enabled_echoes_nothing(client):
    r = client.post("/day-setup", json={
        "prompt_disabled": {"intention": False, "megan_nicety": False,
                            "stoic_intention": False},
    }, headers=_auth(client))
    assert r.status_code == 200, r.text
    # Nothing is disabled, so no flag map is echoed and an otherwise empty Day
    # Setup stays empty.
    assert r.json()["day_setup"]["prompt_disabled"] is None
    assert _disabled() == DISABLED_OFF

    read = client.get("/plan-inputs", headers=_auth(client))
    assert read.status_code == 200, read.text
    assert read.json()["day_setup"].get("prompt_disabled") is None


def test_prompt_disabled_plan_inputs_echoes_set_flags(client):
    client.post("/day-setup", json={
        "prompt_disabled": {"megan_nicety": True, "stoic_intention": True},
    }, headers=_auth(client))
    read = client.get("/plan-inputs", headers=_auth(client))
    assert read.status_code == 200, read.text
    assert read.json()["day_setup"]["prompt_disabled"] == {
        "intention": False, "megan_nicety": True, "stoic_intention": True,
    }


def test_prompt_disabled_map_is_a_full_replace_per_save(client):
    client.post("/day-setup", json={
        "prompt_disabled": {"megan_nicety": True, "stoic_intention": True},
    }, headers=_auth(client))
    r = client.post("/day-setup", json={
        "prompt_disabled": {"megan_nicety": True},
    }, headers=_auth(client))
    assert r.status_code == 200, r.text
    assert _disabled() == {
        "intention": False, "megan_nicety": True, "stoic_intention": False,
    }


def test_prompt_disabled_omitted_preserves_and_echoes_current_state(client):
    client.post("/day-setup", json={
        "prompt_disabled": {"stoic_intention": True},
    }, headers=_auth(client))
    # A captures-only save omits the flag map: stored flags are preserved and
    # still echoed so the toggle keeps its state.
    r = client.post("/day-setup", json={"captures": {"intention": "synthetic"}},
                    headers=_auth(client))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["day_setup_confirmed"] is False
    assert body["day_setup"]["prompt_disabled"]["stoic_intention"] is True
    assert _disabled()["stoic_intention"] is True


def test_prompt_disabled_non_boolean_is_422(client):
    r = client.post("/day-setup", json={
        "prompt_disabled": {"megan_nicety": 1},
    }, headers=_auth(client))
    assert r.status_code == 422
    assert not prompt_state.prompt_disabled_path(
        _today().isoformat()).exists()


def test_prompt_disabled_unknown_prompt_key_is_422_content_free(client):
    key = "custom-m1abcde-xy9"
    r = client.post("/day-setup", json={
        "prompt_disabled": {key: True},
    }, headers=_auth(client))
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "prompt_disabled_invalid"
    assert not prompt_state.prompt_disabled_path(
        _today().isoformat()).exists()


def test_prompt_disabled_corrupt_store_is_409_and_preserved(client):
    target = prompt_state.prompt_disabled_path(_today().isoformat())
    target.parent.mkdir(parents=True, exist_ok=True)
    corrupt = (
        '{"version": 1, "day": "2026-01-01", "disabled": {"megan_nicety": true},'
        ' "disabled": {}}'
    )
    target.write_text(corrupt, encoding="utf-8")

    r = client.post("/day-setup", json={
        "prompt_disabled": {"megan_nicety": True},
    }, headers=_auth(client))
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "prompt_disabled_storage_error"
    assert target.read_text(encoding="utf-8") == corrupt
    assert runstate.is_day_setup_confirmed(_today()) is False
