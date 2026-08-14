"""G24/G25/G26 (T12 stress audit) — billed-call ledger, live-commit
in-flight lock, runstate RMW locking. judgment SDK is never hit live."""
from __future__ import annotations

import sys
import threading
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))
import judgment  # noqa: E402
import main as main_mod  # noqa: E402
import orchestrate  # noqa: E402
import runstate  # noqa: E402

TODAY = date(2026, 7, 16)


@pytest.fixture
def vault(tmp_path) -> Path:
    v = tmp_path / "vault-root"
    v.mkdir()
    return v


@pytest.fixture
def client(vault) -> TestClient:
    app = main_mod.create_app(vault_root=vault)
    c = TestClient(app)
    c.app_token = app.state.token
    c.app = app
    return c


def _auth(client: TestClient) -> dict:
    return {"X-TDTB-Token": client.app_token}


def _today() -> date:
    import gather.tdtb_gather as _g  # noqa: F401 — path already shimmed
    from datetime import datetime
    return main_mod.gather.effective_date(datetime.now())


# ---------------------------------------------------------------------------
# G26 — runstate RMW locking
# ---------------------------------------------------------------------------

class TestRunstateRmw:
    def test_billed_calls_in_skeleton(self):
        assert runstate.build_runstate()["billed_calls"] == 0

    def test_read_runstate_roundtrip(self, vault):
        runstate.write_runstate(vault, TODAY, runstate.build_runstate({"anchor": "08:15"}))
        state = runstate.read_runstate(vault, TODAY)
        assert state["anchor"] == "08:15"
        assert runstate.read_runstate(vault, date(2026, 1, 1)) is None

    def test_update_runstate_merges_dict(self, vault):
        runstate.write_runstate(vault, TODAY, runstate.build_runstate({"anchor": "08:15"}))
        state = runstate.update_runstate(vault, TODAY, {"eod": "22:00"})
        assert state["anchor"] == "08:15" and state["eod"] == "22:00"
        again = runstate.read_runstate(vault, TODAY)
        assert again["anchor"] == "08:15" and again["eod"] == "22:00"

    def test_update_runstate_callable(self, vault):
        runstate.update_runstate(vault, TODAY, lambda s: s.__setitem__("billed_calls", 3))
        assert runstate.read_runstate(vault, TODAY)["billed_calls"] == 3

    def test_concurrent_updates_no_lost_writes(self, vault):
        n_threads, n_iter = 8, 25

        def bump():
            for _ in range(n_iter):
                runstate.update_runstate(
                    vault, TODAY,
                    lambda s: s.__setitem__("billed_calls", s.get("billed_calls", 0) + 1),
                )

        threads = [threading.Thread(target=bump) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert runstate.read_runstate(vault, TODAY)["billed_calls"] == n_threads * n_iter

    def test_orchestrate_persist_preserves_concurrent_writes(self, vault, monkeypatch):
        """The ledger persist must not clobber a concurrent /day-setup write
        landed after run entry (the stale base_state overwrite, G26)."""
        runstate.write_runstate(vault, TODAY, runstate.build_runstate({"anchor": "07:00"}))

        real_empty = orchestrate._empty_entry

        def entry_with_concurrent_write(key, **kw):
            # Simulate another request writing Day Setup mid-run (between the
            # orchestrator's run-entry state read and its ledger persists).
            runstate.update_runstate(vault, TODAY, {"anchor": "09:30"})
            return real_empty(key, **kw)

        monkeypatch.setattr(orchestrate, "_empty_entry", entry_with_concurrent_write)

        report = orchestrate.run_orchestrated(
            [], vault_root=vault, today=TODAY,
        )
        assert report["ok"]
        final = runstate.read_runstate(vault, TODAY)
        assert final["anchor"] == "09:30", "concurrent Day Setup write was clobbered"
        assert final["commit_ledger"], "ledger itself must still persist"


# ---------------------------------------------------------------------------
# G24 — billed-call ledger, charged per real SDK attempt
# ---------------------------------------------------------------------------

class TestBilledLedger:
    def test_charge_fires_per_sdk_attempt(self, monkeypatch):
        """A validation-failure retry is a second real SDK call — the ledger
        hook must see BOTH attempts, not one logical call."""
        charges: list[str] = []
        attempts = {"n": 0}

        async def fake_query(system_prompt, user_prompt):
            attempts["n"] += 1
            if attempts["n"] == 1:
                return "not json at all"
            return '{"ops": []}'

        monkeypatch.setattr(judgment, "_run_query", fake_query)
        ctx = judgment.RunContext(charge=charges.append)
        judgment.adjust_freetext("noop", {"assigned": []}, ctx=ctx)
        assert attempts["n"] == 2
        assert len(charges) == 2

    def test_budget_exceeded_not_swallowed_by_retry(self, monkeypatch):
        """charge raising BudgetExceededError aborts before any SDK call and
        is never eaten by the retry loop."""
        def refuse(label):
            raise judgment.BudgetExceededError("billed budget spent (4/4)")

        async def boom(system_prompt, user_prompt):  # pragma: no cover
            raise AssertionError("SDK must not be reached past a spent budget")

        monkeypatch.setattr(judgment, "_run_query", boom)
        ctx = judgment.RunContext(charge=refuse)
        with pytest.raises(judgment.BudgetExceededError):
            judgment.adjust_freetext("noop", {"assigned": []}, ctx=ctx)

    def test_adjust_route_429_at_cap(self, client, vault):
        runstate.write_runstate(
            vault, _today(), runstate.build_runstate({"billed_calls": main_mod.BILLED_CAP}))
        r = client.post(
            "/adjust", headers=_auth(client),
            json={"instruction": "x", "digest": {"assigned": []}},
        )
        assert r.status_code == 429
        assert "budget" in str(r.json()["detail"]).lower()

    def test_sequence_route_429_at_cap(self, client, vault):
        runstate.write_runstate(
            vault, _today(), runstate.build_runstate({"billed_calls": main_mod.BILLED_CAP}))
        r = client.post(
            "/sequence", headers=_auth(client),
            json={"assigned": [], "config": {}, "anchored_blocks": []},
        )
        assert r.status_code == 429

    def test_adjust_route_wires_persistent_charge(self, client, vault, monkeypatch):
        """The ctx the route passes must persist charges into today's note."""
        seen: dict = {}

        def fake_adjust(instruction, digest, ctx=None):
            seen["ctx"] = ctx
            ctx.charge("adjust attempt 1")
            ctx.charge("adjust attempt 2")
            return {"ops": []}

        monkeypatch.setattr(judgment, "adjust_freetext", fake_adjust)
        r = client.post(
            "/adjust", headers=_auth(client),
            json={"instruction": "x", "digest": {"assigned": []}},
        )
        assert r.status_code == 200
        assert runstate.read_runstate(vault, _today())["billed_calls"] == 2

    def test_charge_mid_request_hits_cap_maps_429(self, client, vault, monkeypatch):
        """Budget exhausted BY the retry inside a request → 429, not 502."""
        runstate.write_runstate(
            vault, _today(),
            runstate.build_runstate({"billed_calls": main_mod.BILLED_CAP - 1}))

        def fake_adjust(instruction, digest, ctx=None):
            ctx.charge("attempt 1")   # spends the last unit
            ctx.charge("attempt 2")   # must raise
            return {"ops": []}  # pragma: no cover

        monkeypatch.setattr(judgment, "adjust_freetext", fake_adjust)
        r = client.post(
            "/adjust", headers=_auth(client),
            json={"instruction": "x", "digest": {"assigned": []}},
        )
        assert r.status_code == 429
        assert runstate.read_runstate(vault, _today())["billed_calls"] == main_mod.BILLED_CAP

    def test_billed_ledger_get(self, client, vault):
        runstate.write_runstate(
            vault, _today(), runstate.build_runstate({"billed_calls": 2}))
        r = client.get("/billed-ledger")
        assert r.status_code == 200
        body = r.json()
        assert body["spent"] == 2
        assert body["cap"] == main_mod.BILLED_CAP
        assert body["remaining"] == main_mod.BILLED_CAP - 2

    def test_billed_ledger_get_no_note(self, client):
        r = client.get("/billed-ledger")
        assert r.status_code == 200
        assert r.json()["spent"] == 0


# ---------------------------------------------------------------------------
# G25 — live-commit in-flight lock
# ---------------------------------------------------------------------------

class TestLiveCommitLock:
    BODY = {"digest": {"assigned": []}, "sequence": {"sequence": []}, "config": {}}

    def test_concurrent_live_commit_409(self, client):
        assert client.app.state.live_commit_lock.acquire(blocking=False)
        try:
            r = client.post("/commit?mode=live", headers=_auth(client), json=self.BODY)
            assert r.status_code == 409
            assert "in flight" in str(r.json()["detail"]).lower()
        finally:
            client.app.state.live_commit_lock.release()

    def test_shadow_mode_ignores_lock(self, client):
        assert client.app.state.live_commit_lock.acquire(blocking=False)
        try:
            r = client.post("/commit?mode=shadow", headers=_auth(client), json=self.BODY)
            assert r.status_code != 409
        finally:
            client.app.state.live_commit_lock.release()

    def test_lock_released_on_error_path(self, client, monkeypatch):
        def raise_state_error(config, vault):
            raise main_mod.shadow.ShadowStateError("boom")

        monkeypatch.setattr(main_mod.shadow, "gather_live_state", raise_state_error)
        # FEEDBACK-24: live commit is gated on an explicit Day Setup confirm —
        # seed it so the error path under test is the shadow-state error.
        assert client.post("/day-setup", json={},
                           headers={"X-TDTB-Token": client.app_token}).status_code == 200
        r = client.post("/commit?mode=live", headers=_auth(client), json=self.BODY)
        assert r.status_code == 502
        assert not client.app.state.live_commit_lock.locked()
