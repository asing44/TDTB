"""GET /commit/status — the secret-free in-flight commit projection that the
operator's post-commit restart hook reads (``hooks/post-commit``).

Covers idle, in-flight shadow and live commits (observed both from inside the
request and over HTTP), normal completion, and the error path. Nothing here
touches a live service, Todoist, Calendar, the judgment SDK, or the operator's
real app home (``conftest`` isolates ``TDTB_HOME``).
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))
import main as main_mod  # noqa: E402

IDLE = {"in_flight": False, "mode": None, "since": None}
BODY = {"digest": {"assigned": []}, "sequence": {"sequence": []}, "config": {}}


@pytest.fixture
def client() -> TestClient:
    app = main_mod.create_app()
    c = TestClient(app)
    c.app_token = app.state.token
    c.app = app
    return c


def _auth(client: TestClient) -> dict:
    return {"X-TDTB-Token": client.app_token}


class _BlockedGather:
    """Stands in for ``shadow.gather_live_state``: parks the request inside
    the commit path until the test releases it, then fails the way a real
    shadow-state error does. That keeps the in-flight window deterministic and
    also exercises the ``finally`` release on an exception path."""

    def __init__(self, flight: "main_mod._CommitFlight") -> None:
        self._flight = flight
        self.entered = threading.Event()
        self.release = threading.Event()
        self.snapshots: list[dict] = []

    def __call__(self, config):  # noqa: ANN001, ANN204 — mirrors the real seam
        # Recorded from INSIDE the request, so the assertion cannot be
        # satisfied by a stale or post-hoc write.
        self.snapshots.append(self._flight.snapshot())
        self.entered.set()
        assert self.release.wait(10), "the test never released the blocked commit"
        raise main_mod.shadow.ShadowStateError("blocked for the commit-status test")


def _run_in_thread(fn):  # noqa: ANN001, ANN202
    box: dict = {}

    def run() -> None:
        box["result"] = fn()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread, box


class TestCommitStatusIdle:

    def test_idle_payload_is_tokenless_and_exact(self, client):
        r = client.get("/commit/status")  # no X-TDTB-Token: tokenless like /health
        assert r.status_code == 200
        assert r.json() == IDLE
        assert set(r.json()) == {"in_flight", "mode", "since"}

    def test_idle_after_a_refused_bare_commit(self, client):
        # A bare POST /commit 501s, but it still passes through the in-flight
        # window — which must be closed again by the time the call returns.
        r = client.post("/commit", headers=_auth(client))
        assert r.status_code == 501
        assert client.get("/commit/status").json() == IDLE


class TestCommitStatusInFlight:

    def test_shadow_commit_reports_in_flight(self, client, monkeypatch):
        flight = client.app.state.commit_flight
        blocked = _BlockedGather(flight)
        monkeypatch.setattr(main_mod.shadow, "gather_live_state", blocked)

        thread, box = _run_in_thread(
            lambda: client.post("/commit?mode=shadow", headers=_auth(client), json=BODY)
        )
        assert blocked.entered.wait(10), "the shadow commit never reached the gather step"

        # As seen from inside the request.
        assert blocked.snapshots[0]["in_flight"] is True
        assert blocked.snapshots[0]["mode"] == "shadow"
        assert blocked.snapshots[0]["since"].endswith("Z")

        # As the restart hook sees it, over HTTP, while the request is parked.
        live_view = client.get("/commit/status").json()
        assert live_view["in_flight"] is True
        assert live_view["mode"] == "shadow"
        assert live_view["since"] == blocked.snapshots[0]["since"]

        blocked.release.set()
        thread.join(10)
        assert box["result"].status_code == 502
        # Released on the error path.
        assert client.get("/commit/status").json() == IDLE

    def test_live_commit_reports_in_flight_and_leaves_g25_lock_intact(
        self, client, monkeypatch,
    ):
        confirmed = client.post(
            "/day-setup", json={}, headers={"X-TDTB-Token": client.app_token},
        )
        assert confirmed.status_code == 200

        blocked = _BlockedGather(client.app.state.commit_flight)
        monkeypatch.setattr(main_mod.shadow, "gather_live_state", blocked)

        thread, box = _run_in_thread(
            lambda: client.post("/commit?mode=live", headers=_auth(client), json=BODY)
        )
        assert blocked.entered.wait(10), "the live commit never reached the gather step"

        assert blocked.snapshots[0]["in_flight"] is True
        assert blocked.snapshots[0]["mode"] == "live"
        assert client.get("/commit/status").json()["mode"] == "live"
        # G25 semantics are unchanged: the parked live commit still owns the
        # live-commit lock, and the status projection never touched it.
        assert client.app.state.live_commit_lock.locked() is True

        blocked.release.set()
        thread.join(10)
        assert box["result"].status_code == 502
        assert client.app.state.live_commit_lock.locked() is False
        assert client.get("/commit/status").json() == IDLE

    def test_overlapping_commits_keep_the_projection_open_until_the_last(
        self, client, monkeypatch,
    ):
        flight = client.app.state.commit_flight
        first = _BlockedGather(flight)
        monkeypatch.setattr(main_mod.shadow, "gather_live_state", first)
        thread_a, box_a = _run_in_thread(
            lambda: client.post("/commit?mode=shadow", headers=_auth(client), json=BODY)
        )
        assert first.entered.wait(10)

        second = _BlockedGather(flight)
        monkeypatch.setattr(main_mod.shadow, "gather_live_state", second)
        thread_b, box_b = _run_in_thread(
            lambda: client.post("/commit?mode=shadow", headers=_auth(client), json=BODY)
        )
        assert second.entered.wait(10)
        # The outer window is still the one that started it.
        assert client.get("/commit/status").json()["since"] == first.snapshots[0]["since"]

        first.release.set()
        thread_a.join(10)
        assert box_a["result"].status_code == 502
        # One commit finished, one is still parked: still in flight.
        assert client.get("/commit/status").json()["in_flight"] is True

        second.release.set()
        thread_b.join(10)
        assert box_b["result"].status_code == 502
        assert client.get("/commit/status").json() == IDLE
