"""V4 gate: the service neither needs nor touches a vault root.

Every test runs twice: once with ``TDTB_VAULT_ROOT`` unset (the new installs)
and once with it pointing at a READ-ONLY, EMPTY directory (the operator's
installed launchd plist still exports it until they reinstall). The variable
must be ignored: every route answers, and nothing is ever written under the
directory.
"""
from __future__ import annotations

import re
import stat
import sys
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "gather"))

import main as main_mod  # noqa: E402
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

APP_DIR = Path(__file__).resolve().parent.parent
TODAY = date(2026, 7, 12)
TOKEN_HEADER = "X-TDTB-Token"

CONFIG_MD = (
    "## Defaults\n"
    "| Key | Value |\n|---|---|\n| eod | 11:59 PM |\n\n"
    "## Template Blocks\n"
    "### Trinoor Hours\n"
    "| Slot | Start | End |\n|---|---|---|\n"
    "| Morning | 12:00 AM | 11:59 PM |\n"
)


@pytest.fixture(params=["unset", "set-to-read-only-empty-dir"])
def vault_env(request, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """``None`` when the variable is unset, else the read-only empty dir."""
    if request.param == "unset":
        monkeypatch.delenv("TDTB_VAULT_ROOT", raising=False)
        yield None
        return
    root = tmp_path / "ro-vault"
    root.mkdir()
    root.chmod(stat.S_IRUSR | stat.S_IXUSR)
    monkeypatch.setenv("TDTB_VAULT_ROOT", str(root))
    yield root
    root.chmod(stat.S_IRWXU)


def _assert_untouched(root: Path | None) -> None:
    if root is not None:
        assert list(root.rglob("*")) == [], "something was written under TDTB_VAULT_ROOT"


@pytest.fixture
def client(vault_env, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    install_config_markdown(CONFIG_MD)
    monkeypatch.setattr(gather, "effective_date", lambda now: TODAY)
    monkeypatch.setattr(shadow, "gather_live_state", _fake_live_state)
    app = main_mod.create_app()
    app.state.build_read_clients = lambda cfg: (None, None)
    app.state.build_calendar_store = lambda cfg: None
    app.state.build_capacities_adapter = lambda cfg: None
    app.state.build_commit_clients = lambda cfg: (FakeLiveTodoist(), FakeLiveStore())
    c = TestClient(app)
    c.app_token = app.state.token
    return c


def _auth(client: TestClient) -> dict:
    return {TOKEN_HEADER: client.app_token}


def test_create_app_takes_no_vault_root() -> None:
    with pytest.raises(TypeError):
        main_mod.create_app(vault_root="/anywhere")  # type: ignore[call-arg]
    assert not hasattr(main_mod, "VAULT_ROOT_ENV")


def test_tokenless_reads_answer(client: TestClient, vault_env) -> None:
    assert client.get("/health").status_code == 200
    assert client.get("/config").status_code == 200
    # /version answers 200 or a bounded 503 (no git checkout in a sandbox),
    # never a vault-root 503.
    version = client.get("/version")
    assert "vault" not in version.text.lower()
    plan = client.get("/plan-inputs")
    assert plan.status_code == 200, plan.text
    body = plan.json()
    assert set(body) >= {"digest", "config", "anchored_blocks", "source_counts"}
    assert body["source_counts"]["vault"] == 0  # wire field kept for the UI (V5 drops it)
    assert not [w for w in body.get("source_warnings") or [] if "vault" in w.lower()]
    _assert_untouched(vault_env)


def test_day_setup_and_gather_write_only_to_the_state_dir(client: TestClient, vault_env) -> None:
    saved = client.post("/day-setup", json={"anchor": "09:00"}, headers=_auth(client))
    assert saved.status_code == 200, saved.text
    gathered = client.post("/gather", headers=_auth(client))
    assert gathered.status_code == 200, gathered.text
    run_data = gathered.json()
    assert run_data["pool_count"] == 0 and run_data["assigned_count"] == 0
    assert rs.state_runstate_path(TODAY).is_file()
    _assert_untouched(vault_env)


def test_digest_route_serves_without_a_vault(client: TestClient, vault_env) -> None:
    r = client.post("/digest", headers=_auth(client),
                    json={"pool_items": [], "assigned_items": [], "today": str(TODAY)})
    assert r.status_code == 200, r.text
    _assert_untouched(vault_env)


def test_shadow_and_live_commit_run_without_a_vault(client: TestClient, vault_env) -> None:
    rs.write_digest_index(
        TODAY,
        [{"name": "Garage", "todoist_id": "", "path": "P/Garage.md", "surface": "assigned"}],
    )
    assert client.post("/day-setup", json={"anchor": "09:00"},
                       headers=_auth(client)).status_code == 200
    payload = {"digest": LIVE_DIGEST, "sequence": LIVE_SEQUENCE, "config": {}}

    shadow_run = client.post("/commit?mode=shadow", headers=_auth(client), json=payload)
    assert shadow_run.status_code == 200, shadow_run.text
    assert shadow_run.json()["entries"]

    live = client.post("/commit?mode=live", headers=_auth(client), json=payload)
    assert live.status_code == 200, live.text
    assert set(live.json()["surfaces"]) == {"todoist", "calendar"}
    _assert_untouched(vault_env)


def test_app_source_never_reads_the_vault_root_variable() -> None:
    """Static guard: no module under app/ resolves, requires or names a vault
    root any more (tests and the built static assets are excluded)."""
    offenders = []
    for path in sorted(APP_DIR.glob("*.py")) + sorted((APP_DIR / "gather").glob("*.py")):
        text = path.read_text(encoding="utf-8")
        if re.search(r"TDTB_VAULT_ROOT|resolve_vault_root|\bvault_root\b", text):
            offenders.append(path.name)
    assert offenders == []
