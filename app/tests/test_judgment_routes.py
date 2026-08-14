"""Route tests for /adjust and /sequence (T11) — judgment.* is mocked, no
live SDK calls under pytest."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))
import main as main_mod  # noqa: E402
import judgment  # noqa: E402


@pytest.fixture
def vault(tmp_path) -> Path:
    return tmp_path / "vault-root"


@pytest.fixture
def client(vault) -> TestClient:
    vault.mkdir()
    app = main_mod.create_app(vault_root=vault)
    c = TestClient(app)
    c.app_token = app.state.token
    return c


def _auth(client: TestClient) -> dict:
    return {"X-TDTB-Token": client.app_token}


class TestAdjustRoute:
    def test_success(self, client, monkeypatch):
        monkeypatch.setattr(
            judgment, "adjust_freetext",
            lambda instruction, digest, ctx=None: {
                "ops": [{"op": "complete", "id": "Beta", "args": {}}]
            },
        )
        r = client.post(
            "/adjust",
            headers=_auth(client),
            json={"instruction": "mark Beta done", "digest": {"assigned": []}},
        )
        assert r.status_code == 200
        assert r.json()["ops"][0]["op"] == "complete"

    def test_judgment_error_returns_502(self, client, monkeypatch):
        def _raise(instruction, digest, ctx=None):
            raise judgment.JudgmentError("boom")

        monkeypatch.setattr(judgment, "adjust_freetext", _raise)
        r = client.post(
            "/adjust",
            headers=_auth(client),
            json={"instruction": "do something", "digest": {"assigned": []}},
        )
        assert r.status_code == 502
        assert "boom" in r.json()["detail"]

    def test_requires_token(self, client):
        r = client.post("/adjust", json={"instruction": "x", "digest": {}})
        assert r.status_code == 403


class TestSequenceRoute:
    def test_success(self, client, monkeypatch):
        # Fake proposer must place EVERY assigned item (never-bump) — the
        # route now injects schedulable blocks (QT/Minting, ui-parity T5)
        # into the assigned set before proposing.
        def _place_all(assigned, config, anchored_blocks, ctx=None):
            # start well past "now" so the T7 past-placement check (anchor =
            # live clock) can't fire regardless of when the suite runs
            rows, t = [], 23 * 60 - 60
            for a in assigned:
                dur = int(a.get("duration") or 30)
                rows.append({"id": a.get("id") or a.get("name"),
                             "start": f"{t // 60:02d}:{t % 60:02d}",
                             "end": f"{(t + dur) // 60:02d}:{(t + dur) % 60:02d}",
                             "zone": "any"})
                t += dur
            return {"sequence": rows}

        monkeypatch.setattr(judgment, "propose_sequence", _place_all)
        r = client.post(
            "/sequence",
            headers=_auth(client),
            json={"assigned": [{"id": "A"}], "config": {}, "anchored_blocks": []},
        )
        assert r.status_code == 200
        assert r.json()["sequence"][0]["id"] == "A"

    def test_judgment_error_returns_502(self, client, monkeypatch):
        def _raise(assigned, config, anchored_blocks, ctx=None):
            raise judgment.JudgmentError("workout before noon")

        monkeypatch.setattr(judgment, "propose_sequence", _raise)
        r = client.post(
            "/sequence",
            headers=_auth(client),
            json={"assigned": [], "config": {}, "anchored_blocks": []},
        )
        assert r.status_code == 502
        assert "workout before noon" in r.json()["detail"]

    def test_requires_token(self, client):
        r = client.post("/sequence", json={"assigned": [], "config": {}, "anchored_blocks": []})
        assert r.status_code == 403


class TestRejectedProposalSurvives422:
    """T12 qualification (2026-07-26): a hard validation failure discarded the
    proposal outright — but by that point the SDK call has been made and the
    billed ledger charged, so the user pays for a plan they never see. The
    rejection stands; the body rides along so the client can show it read-only
    and the user can read what the call bought."""

    def _bad_proposal(self, assigned, config, anchored_blocks, ctx=None):
        # Places a row in the past, which the server-side validator rejects
        # hard regardless of when the suite runs.
        return {"sequence": [{"id": assigned[0].get("id") if assigned else "A",
                              "start": "00:05", "end": "00:35", "zone": "any"}]}

    def test_422_carries_the_rejected_proposal(self, client, monkeypatch):
        monkeypatch.setattr(judgment, "propose_sequence", self._bad_proposal)
        r = client.post(
            "/sequence",
            headers=_auth(client),
            json={"assigned": [{"id": "A"}], "config": {}, "anchored_blocks": []},
        )
        assert r.status_code == 422
        detail = r.json()["detail"]
        assert detail["message"] == "sequence validation failed"
        assert detail["hard_errors"]
        rejected = detail["rejected_proposal"]
        assert rejected["sequence"][0]["id"] == "A"

    def test_rejection_still_stands(self, client, monkeypatch):
        """Carrying the body must not soften the verdict — a hard failure is
        never a committable plan."""
        monkeypatch.setattr(judgment, "propose_sequence", self._bad_proposal)
        r = client.post(
            "/sequence",
            headers=_auth(client),
            json={"assigned": [{"id": "A"}], "config": {}, "anchored_blocks": []},
        )
        assert r.status_code == 422
        assert "sequence" not in r.json()  # not a success body
