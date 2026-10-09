"""Route tests for the local Capacities direct-refresh job API (U2).

The status route is a tokenless, local, read-only job/coverage read. The
start/cancel routes are token-guarded. No test here reads a credential or
contacts a provider: a fake provider and an injected coordinator factory are
used throughout, and a poisoned provider proves status makes no provider call.
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

import main as main_mod  # noqa: E402
import capacities_refresh as rr  # noqa: E402
from capacities_refresh_helpers import (  # noqa: E402
    Clock,
    FakeProvider,
    PRIMARY,
    SPACE,
    Sleeper,
    _mapping,
    _paged_provider,
    _store,
)


@pytest.fixture
def vault(tmp_path) -> Path:
    root = tmp_path / "vault-root"
    root.mkdir()
    return root


def _factory(tmp_path, provider, *, release: threading.Event | None = None):
    def build(vault_path, config):
        if release is not None:
            provider.on_fetch = lambda: release.wait(timeout=5)
        store = _store(tmp_path)
        return rr.RefreshCoordinator(
            root=tmp_path / "state",
            store=store,
            provider=provider,
            space_id=SPACE,
            mappings=(_mapping(PRIMARY),),
            revision_supplier=lambda: 1,
            scope_key="all",
            clock=Clock(),
            sleeper=Sleeper(Clock()),
        )

    return build


def _client(tmp_path, provider, *, release=None) -> TestClient:
    app = main_mod.create_app(vault_root=tmp_path / "vault")
    app.state.build_refresh_coordinator = _factory(tmp_path, provider, release=release)
    client = TestClient(app)
    client.app_token = app.state.token
    return client


def _auth(client: TestClient) -> dict:
    return {"X-TDTB-Token": client.app_token}


def test_status_is_tokenless_and_reports_no_initial_job(tmp_path):
    client = _client(tmp_path, _paged_provider(objects={"T1": ["a"], "T2": []}))

    response = client.get("/capacities/refresh/status")

    assert response.status_code == 200
    body = response.json()
    assert body["configured"] is True
    assert body["job"] is None
    assert body["snapshot"]["present"] is False


def test_start_requires_token(tmp_path):
    client = _client(tmp_path, _paged_provider(objects={"T1": ["a"], "T2": []}))

    response = client.post("/capacities/refresh/start", json={"mode": "refresh"})

    assert response.status_code == 403


def test_cancel_requires_token(tmp_path):
    client = _client(tmp_path, _paged_provider(objects={"T1": ["a"], "T2": []}))

    response = client.post("/capacities/refresh/cancel")

    assert response.status_code == 403


def test_start_and_cancel_round_trip(tmp_path):
    release = threading.Event()
    client = _client(
        tmp_path, _paged_provider(objects={"T1": ["a"], "T2": []}), release=release
    )

    started = client.post(
        "/capacities/refresh/start",
        headers=_auth(client),
        json={"mode": "refresh", "scope": "all"},
    )
    assert started.status_code == 200
    assert started.json()["phase"] in {"listing", "hydrating", "evaluating", "publishing"}

    cancelled = client.post("/capacities/refresh/cancel", headers=_auth(client))
    assert cancelled.status_code == 200
    assert cancelled.json()["phase"] in {"cancelled", "complete", "failed"}
    release.set()
    # Drain the background thread.
    client.get("/capacities/refresh/status")


def test_second_start_while_running_is_409(tmp_path):
    release = threading.Event()
    client = _client(
        tmp_path, _paged_provider(objects={"T1": ["a"], "T2": []}), release=release
    )
    client.post(
        "/capacities/refresh/start",
        headers=_auth(client),
        json={"mode": "refresh"},
    )

    response = client.post(
        "/capacities/refresh/start",
        headers=_auth(client),
        json={"mode": "refresh"},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "capacities_refresh_busy"
    release.set()


def test_status_never_calls_the_provider(tmp_path):
    provider = _paged_provider(objects={"T1": [], "T2": []})
    client = _client(tmp_path, provider)

    for _ in range(3):
        response = client.get("/capacities/refresh/status")
        assert response.status_code == 200

    assert provider.fetch_calls == 0
    assert provider.list_calls == []
    assert provider.get_calls == []


def test_unconfigured_source_is_503_on_start(tmp_path):
    app = main_mod.create_app(vault_root=tmp_path / "vault")
    app.state.build_refresh_coordinator = lambda vault_path, config: None
    client = TestClient(app)

    response = client.post(
        "/capacities/refresh/start",
        headers={"X-TDTB-Token": app.state.token},
        json={"mode": "refresh"},
    )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "capacities_refresh_unconfigured"
    assert client.get("/capacities/refresh/status").json()["configured"] is False


def test_unresolved_vault_is_503(tmp_path, monkeypatch):
    monkeypatch.delenv("TDTB_VAULT_ROOT", raising=False)
    app = main_mod.create_app()
    app.state.build_refresh_coordinator = lambda vault_path, config: None
    client = TestClient(app)

    assert client.get("/capacities/refresh/status").status_code == 503


@pytest.mark.parametrize(
    "body",
    [
        {"mode": "bogus"},
        {"mode": "refresh", "scope": 5},
        {"scope": "all"},
        {},
        {"mode": "refresh", "scope": "all", "extra": 1},
    ],
)
def test_malformed_start_body_is_422(tmp_path, body):
    client = _client(tmp_path, _paged_provider(objects={"T1": ["a"], "T2": []}))

    response = client.post(
        "/capacities/refresh/start", headers=_auth(client), json=body
    )

    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Cached-coordinator freshness across a configuration save
# ---------------------------------------------------------------------------

def _counting_factory(client, builds):
    """Wrap the injected factory so each build is observable."""
    inner = client.app.state.build_refresh_coordinator

    def build(vault_path, config):
        builds.append(vault_path)
        return inner(vault_path, config)

    return build


def _revision_stub(monkeypatch, holder):
    monkeypatch.setattr(
        main_mod.capacities_builder,
        "refresh_config_revision",
        lambda vault: holder["value"],
    )


def test_start_rebuilds_an_idle_coordinator_when_the_config_revision_changed(
    tmp_path, monkeypatch
):
    """A settings or mapping save between runs must reach the next start.

    The coordinator captures its mappings and evaluation settings at build
    time. A cached instance would otherwise run the *new* revision with the
    *old* scope and publish it as current, so start replaces an idle instance
    whose revision moved, while an unchanged revision reuses it.
    """
    builds: list = []
    revision = {"value": 1}
    _revision_stub(monkeypatch, revision)
    client = _client(tmp_path, _paged_provider(objects={"T1": ["a"], "T2": []}))
    client.app.state.build_refresh_coordinator = _counting_factory(client, builds)

    first = client.post(
        "/capacities/refresh/start",
        headers=_auth(client),
        json={"mode": "refresh", "scope": "all"},
    )
    assert first.status_code == 200
    assert len(builds) == 1
    client.app.state.refresh_coordinator.wait(timeout=5)

    revision["value"] = 2
    second = client.post(
        "/capacities/refresh/start",
        headers=_auth(client),
        json={"mode": "refresh", "scope": "all"},
    )
    assert second.status_code == 200
    assert len(builds) == 2
    client.app.state.refresh_coordinator.wait(timeout=5)

    third = client.post(
        "/capacities/refresh/start",
        headers=_auth(client),
        json={"mode": "refresh", "scope": "all"},
    )
    assert third.status_code == 200
    assert len(builds) == 2
    client.app.state.refresh_coordinator.wait(timeout=5)


def test_a_live_job_keeps_its_coordinator_across_a_config_change(
    tmp_path, monkeypatch
):
    """A live job must keep the instance that owns its thread and locks.

    Rebuilding under a running job would orphan it: cancel and status would
    reach a second instance with no job, and the live thread would keep
    running unreachable. The change is instead rejected at publication by the
    job's own revision guard.
    """
    builds: list = []
    revision = {"value": 1}
    release = threading.Event()
    _revision_stub(monkeypatch, revision)
    client = _client(
        tmp_path,
        _paged_provider(objects={"T1": ["a"], "T2": []}),
        release=release,
    )
    client.app.state.build_refresh_coordinator = _counting_factory(client, builds)

    started = client.post(
        "/capacities/refresh/start",
        headers=_auth(client),
        json={"mode": "refresh", "scope": "all"},
    )
    assert started.status_code == 200

    revision["value"] = 2
    again = client.post(
        "/capacities/refresh/start",
        headers=_auth(client),
        json={"mode": "refresh", "scope": "all"},
    )
    assert again.status_code == 409
    assert len(builds) == 1

    cancelled = client.post("/capacities/refresh/cancel", headers=_auth(client))
    assert cancelled.status_code == 200
    assert cancelled.json()["phase"] == "cancelled"
    release.set()
    # Drain the background thread.
    client.get("/capacities/refresh/status")
