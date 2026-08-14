"""Route tests for /validate-sequence (T16) — a thin deterministic wrapper over
the FROZEN sequence.validate_sequence. No LLM, no writes: the timeline view
calls this on every drag-end to get fresh {ok, hard_errors, warnings} without
re-proposing via /sequence (which is an Agent SDK call). These tests assert the
route faithfully passes the frozen validator's verdict through, and is
token-guarded like every other mutating-shaped route.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))
import main as main_mod  # noqa: E402
import runstate as rs  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent.parent / "gather"))
import tdtb_gather as gather  # noqa: E402
from datetime import datetime


def _pin_frame(vault: Path):
    """Pin the T7 time frame deterministically (anchor 00:00, eod 23:59) so
    clock-relative past-placement checks never fire in these route tests."""
    today = gather.effective_date(datetime.now())
    rs.write_runstate(vault, today, rs.build_runstate(
        {"anchor": "00:00", "eod": "23:59"}))


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


class TestValidateSequenceRoute:
    def test_requires_token(self, client):
        r = client.post(
            "/validate-sequence",
            json={"sequence": [], "assigned": [], "anchored_blocks": [], "config": {}},
        )
        assert r.status_code == 403

    def test_ok_no_violations(self, client, vault):
        _pin_frame(vault)
        r = client.post(
            "/validate-sequence",
            headers=_auth(client),
            json={
                "sequence": [{"id": "A", "start": "13:00", "end": "13:30", "zone": "any"}],
                "assigned": [{"id": "A", "zone": "any"}],
                "anchored_blocks": [],
                "config": {},
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert body["hard_errors"] == []
        assert body["warnings"] == []

    def test_revalidation_requires_same_pin_grant_fingerprint_snapshot(self, client, vault):
        _pin_frame(vault)
        today = gather.effective_date(datetime.now())
        pin = {"id": "A", "start": "13:00", "end": "13:30", "zone": "any"}
        rs.update_runstate(vault, today, {
            "pinned_rows": [pin], "overlap_grants": [],
            "planning_config_fingerprint": "fp-current",
        })
        payload = {
            "sequence": [pin], "assigned": [{"id": "A", "zone": "any"}],
            "anchored_blocks": [], "config": {}, "pinned_rows": [pin],
            "overlap_grants": [], "planning_config_fingerprint": "fp-current",
        }
        assert client.post("/validate-sequence", headers=_auth(client),
                           json=payload).json()["ok"] is True
        payload["planning_config_fingerprint"] = "stale"
        stale = client.post("/validate-sequence", headers=_auth(client), json=payload)
        assert stale.json()["hard_errors"] == ["planning snapshot is stale"]

    def test_warning_passthrough_soft_zone(self, client, vault):
        # work_hours default window is 08:30-17:00; 07:00 is outside -> SOFT
        # zone_violation warning, ok stays True (never gates).
        _pin_frame(vault)
        r = client.post(
            "/validate-sequence",
            headers=_auth(client),
            json={
                "sequence": [{"id": "t1", "start": "07:00", "end": "07:30", "zone": "work_hours"}],
                "assigned": [{"id": "t1", "zone": "work_hours"}],
                "anchored_blocks": [],
                "config": {},
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert any(
            w["kind"] == "zone_violation" and w["id"] == "t1" for w in body["warnings"]
        )

    def test_injected_schedulable_rows_are_not_extras(self, client, vault):
        # /sequence injects Minting/QT/Shivery into its allowlist before
        # validating; this route must mirror that or drag-time revalidation
        # of a proposal flags the injected rows as foreign (2026-07-15 bug).
        today = gather.effective_date(datetime.now())
        rs.write_runstate(vault, today, rs.build_runstate({
            "anchor": "00:00", "eod": "23:59",
            "schedulable": {"qt": {"on": True, "n": 1},
                            "shivery": {"on": True, "n": 1}}}))
        r = client.post(
            "/validate-sequence",
            headers=_auth(client),
            json={
                "sequence": [
                    {"id": "Quick Tasks", "start": "13:00", "end": "13:30", "zone": "any"},
                    {"id": "Shivery Jigs", "start": "14:00", "end": "14:30", "zone": "any"},
                ],
                "assigned": [],
                "anchored_blocks": [],
                "config": {},
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body["hard_errors"] == []
        assert body["ok"] is True

    def test_qt_absorbed_items_optional_either_way(self, client, vault):
        # A 🚀10min item folds into the QT block: an LLM proposal leaves it
        # unplaced, a manual layout places it directly — both must validate.
        today = gather.effective_date(datetime.now())
        rs.write_runstate(vault, today, rs.build_runstate({
            "anchor": "00:00", "eod": "23:59",
            "schedulable": {"qt": {"on": True, "n": 1}}}))
        assigned = [{"id": "Water plants", "name": "Water plants",
                     "labels": ["🚀10min"], "zone": "any"}]
        for seq in (
            [{"id": "Quick Tasks", "start": "13:00", "end": "13:30", "zone": "any"}],
            [{"id": "Quick Tasks", "start": "13:00", "end": "13:30", "zone": "any"},
             {"id": "Water plants", "start": "14:00", "end": "14:30", "zone": "any"}],
        ):
            r = client.post(
                "/validate-sequence", headers=_auth(client),
                json={"sequence": seq, "assigned": assigned,
                      "anchored_blocks": [], "config": {}},
            )
            assert r.status_code == 200
            assert r.json()["hard_errors"] == []

    def test_hard_error_passthrough_structural(self, client):
        # end <= start is a HARD structural invariant -> ok False.
        r = client.post(
            "/validate-sequence",
            headers=_auth(client),
            json={
                "sequence": [{"id": "t1", "start": "09:00", "end": "08:00", "zone": "any"}],
                "assigned": [{"id": "t1", "zone": "any"}],
                "anchored_blocks": [],
                "config": {},
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is False
        assert body["hard_errors"]

    def test_empty_sequence_edge(self, client):
        # Empty sequence + empty assigned: a list is still a list, nothing to
        # violate -> ok True (validate_sequence has no non-empty requirement;
        # that belongs to judgment's proposal schema, not the re-validate pass).
        r = client.post(
            "/validate-sequence",
            headers=_auth(client),
            json={"sequence": [], "assigned": [], "anchored_blocks": [], "config": {}},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert body["hard_errors"] == []
