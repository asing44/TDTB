"""V3 gate: Commit and the runtime verbs no longer write the Obsidian vault.

The vault root in every test below is a read-only, empty directory. A Commit
(live and shadow/dry) that still patched the daily note, flipped ``assigned:``
or moved a note to ``.trash`` would raise there or leave a file behind, so
these tests pin the V3 contract:

  - live Commit writes Todoist and the calendar only; the result has just the
    ``todoist`` and ``calendar`` surfaces (no ``vault_flips`` / ``daily_note``);
  - shadow Commit plans Step A/D/E rows only (no Step B/C vault rows) and
    reports no ``vault`` surface;
  - the Live "done" state is read from the state-dir history, never from a
    daily note.
"""
from __future__ import annotations

import stat
import sys
from datetime import date, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "gather"))

import main as main_mod  # noqa: E402
import micro_adventure as ma  # noqa: E402
import runstate as rs  # noqa: E402
import shadow  # noqa: E402
import tdtb_gather as gather  # noqa: E402
from tests.config_markdown import install_config_markdown  # noqa: E402
from tests.test_main_api import (  # noqa: E402
    LIVE_DIGEST,
    LIVE_SEQUENCE,
    FakeLiveStore,
    FakeLiveTodoist,
    _fake_live_state,
)

TODAY = date(2026, 7, 12)

CONFIG_MD = (
    "## Defaults\n"
    "| Key | Value |\n|---|---|\n| eod | 11:59 PM |\n\n"
    "## Template Blocks\n"
    "### Trinoor Hours\n"
    "| Slot | Start | End |\n|---|---|---|\n"
    "| Morning | 12:00 AM | 11:59 PM |\n"
)


@pytest.fixture
def ro_vault(tmp_path: Path):
    root = tmp_path / "ro-vault"
    root.mkdir()
    root.chmod(stat.S_IRUSR | stat.S_IXUSR)
    yield root
    root.chmod(stat.S_IRWXU)


def _assert_vault_untouched(root: Path) -> None:
    assert list(root.rglob("*")) == [], "something was written under the vault root"


@pytest.fixture
def client(ro_vault: Path, monkeypatch) -> TestClient:
    install_config_markdown(CONFIG_MD)
    monkeypatch.setattr(gather, "effective_date", lambda now: TODAY)
    monkeypatch.setattr(shadow, "gather_live_state", _fake_live_state)
    rs.write_digest_index(
        ro_vault, TODAY,
        [{"name": "Garage", "todoist_id": "", "path": "P/Garage.md", "surface": "assigned"}],
    )
    app = main_mod.create_app(vault_root=ro_vault)
    app.state.build_commit_clients = lambda v, cfg: (FakeLiveTodoist(), FakeLiveStore())
    c = TestClient(app)
    c.app_token = app.state.token
    assert c.post("/day-setup", json={"anchor": "09:00"},
                  headers={"X-TDTB-Token": app.state.token}).status_code == 200
    return c


def _auth(client: TestClient) -> dict:
    return {"X-TDTB-Token": client.app_token}


def test_live_commit_writes_nothing_under_the_vault_and_returns_a_valid_result(
        client: TestClient, ro_vault: Path) -> None:
    r = client.post(
        "/commit?mode=live", headers=_auth(client),
        json={"digest": LIVE_DIGEST, "sequence": LIVE_SEQUENCE, "config": {}},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["resumed"] is False
    assert set(body["surfaces"]) == {"todoist", "calendar"}
    assert body["surfaces"]["todoist"]["status"] == "ok"
    assert len(body["surfaces"]["todoist"]["created"]) == 1
    assert body["failed"] == []
    assert body["verify_failures"] == []
    for key in ("landed", "verify_details", "today"):
        assert key in body
    _assert_vault_untouched(ro_vault)


def test_live_commit_resume_keeps_the_vault_untouched(client: TestClient, ro_vault: Path) -> None:
    payload = {"digest": LIVE_DIGEST, "sequence": LIVE_SEQUENCE, "config": {}}
    assert client.post("/commit?mode=live", headers=_auth(client), json=payload).status_code == 200
    r = client.post("/commit?mode=live&resume=true", headers=_auth(client), json=payload)
    assert r.status_code == 200, r.text
    assert r.json()["resumed"] is True
    assert set(r.json()["surfaces"]) == {"todoist", "calendar"}
    _assert_vault_untouched(ro_vault)


def test_shadow_commit_has_no_vault_rows_and_writes_nothing(
        client: TestClient, ro_vault: Path) -> None:
    r = client.post(
        "/commit?mode=shadow", headers=_auth(client),
        json={"digest": LIVE_DIGEST, "sequence": LIVE_SEQUENCE, "config": {}},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert {"entries", "counts", "unavailable_surfaces"} <= set(body)
    assert body["entries"]
    assert all(e["manifest"]["system"] in {"todoist", "calendar"} for e in body["entries"])
    assert {e["manifest"]["step"] for e in body["entries"]}.isdisjoint({"B", "C"})
    assert "vault" not in body["unavailable_surfaces"]
    assert body["counts"]["conflict"] == 0
    _assert_vault_untouched(ro_vault)


def test_live_commit_persists_the_plan_manifest_without_vault_rows(
        client: TestClient, ro_vault: Path) -> None:
    client.post("/commit?mode=live", headers=_auth(client),
                json={"digest": LIVE_DIGEST, "sequence": LIVE_SEQUENCE, "config": {}})
    state = rs.read_runstate(ro_vault, TODAY) or {}
    assert state["commit_ledger"]["surfaces"].keys() == {"todoist", "calendar"}
    assert all(r.get("system") != "vault" for r in state.get("plan_manifest") or [])


# --------------------------------------------------------------------------- #
# The Live "done" state comes from the state-dir history
# --------------------------------------------------------------------------- #
def _write_log(history_yaml: str) -> None:
    path = ma.history_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\ndescription: log\nschema_version: 1\nhistory:\n" + history_yaml + "---\n",
        encoding="utf-8",
    )


def _state(ro_vault: Path) -> dict:
    return main_mod._micro_adventure_state(ro_vault, {}, TODAY)[0]


def test_unanswered_prior_entry_is_pending_even_with_a_ticked_daily_note(ro_vault: Path) -> None:
    _write_log("  - date: 2026-07-11\n    id: ma07\n    idea: Watch sunset\n    done:\n")
    # an old-style daily note with a ticked Live box must not be consulted
    assert not (ro_vault / "30 - Daily").exists()
    state = _state(ro_vault)
    assert state["pending_confirm"] == {
        "date": "2026-07-11", "id": "ma07", "idea": "Watch sunset"}
    _assert_vault_untouched(ro_vault)


def test_done_true_in_state_history_clears_the_pending_prompt(ro_vault: Path) -> None:
    _write_log("  - date: 2026-07-11\n    id: ma07\n    idea: Watch sunset\n    done: true\n")
    state = _state(ro_vault)
    assert state["pending_confirm"] is None
    assert state["streak"] == 1  # the done flag from the state-dir log counts
    _assert_vault_untouched(ro_vault)


def test_loose_end_route_answer_is_what_the_next_read_sees(
        client: TestClient, ro_vault: Path) -> None:
    _write_log("  - date: 2026-07-11\n    id: ma07\n    idea: Watch sunset\n    done:\n")
    assert _state(ro_vault)["pending_confirm"] is not None
    r = client.post("/micro-adventure/loose-end", headers=_auth(client),
                    json={"date": "2026-07-11", "id": "ma07", "done": True})
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "date": "2026-07-11", "id": "ma07", "done": True}
    assert _state(ro_vault)["pending_confirm"] is None
    assert ma.read_history(ma.history_path())[0].done is True
    _assert_vault_untouched(ro_vault)


def test_loose_end_dismiss_records_not_done(client: TestClient, ro_vault: Path) -> None:
    _write_log("  - date: 2026-07-11\n    id: ma07\n    idea: Watch sunset\n    done:\n")
    r = client.post("/micro-adventure/loose-end", headers=_auth(client),
                    json={"date": "2026-07-11", "id": "ma07", "done": False})
    assert r.status_code == 200, r.text
    assert ma.read_history(ma.history_path())[0].done is False
    assert _state(ro_vault)["pending_confirm"] is None
    _assert_vault_untouched(ro_vault)


def test_the_daily_note_probe_is_gone() -> None:
    assert not hasattr(main_mod, "_daily_note_live_probe")
    assert not hasattr(ma, "daily_note_live_done")
