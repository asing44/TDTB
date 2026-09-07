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

    def test_public_validator_compares_equivalent_time_encodings(self, client, vault):
        _pin_frame(vault)
        r = client.post(
            "/validate-sequence",
            headers=_auth(client),
            json={
                "sequence": [{
                    "id": "A", "start": "9:00 AM", "end": "9:30 AM", "zone": "any",
                }],
                "assigned": [{"id": "A", "zone": "any"}],
                "anchored_blocks": [],
                "config": {},
            },
        )

        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert body["hard_errors"] == []

    def test_public_validator_exposes_structured_calendar_diagnostic_additively(
        self, client, vault
    ):
        _pin_frame(vault)
        r = client.post(
            "/validate-sequence",
            headers=_auth(client),
            json={
                "sequence": [{"id": "A", "start": "09:30", "end": "10:30", "zone": "any"}],
                "assigned": [{"id": "A", "zone": "any"}],
                "anchored_blocks": [{
                    "Block": "Dentist",
                    "Start": "9:00 AM",
                    "End": "10:00 AM",
                    "source": "calendar",
                    "capacity_class": "fixed",
                }],
                "config": {},
            },
        )

        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is False
        assert any("Dentist" in error and "09:00-10:00" in error
                   for error in body["hard_errors"])
        diagnostic = next(
            item for item in body["diagnostics"] if item["severity"] == "error"
        )
        assert diagnostic["affected_rows"] == ["A", "Dentist"]
        assert diagnostic["intervals"] == [
            {"id": "A", "start": "09:30", "end": "10:30"},
            {"id": "Dentist", "start": "09:00", "end": "10:00"},
        ]

    def test_public_validator_keeps_server_native_pin_immutable(self, client, vault):
        _pin_frame(vault)
        today = gather.effective_date(datetime.now())
        native_pin = {
            "id": "Native task", "start": "09:00", "end": "09:30", "zone": None,
        }
        rs.write_digest_index(vault, today, [{
            "name": "Native task",
            "todoist_id": "todo-1",
            "surface": "assigned",
            "source": "todoist",
            "blocks": "1",
            "scheduled_start": "09:00",
            "is_recurring": "false",
        }])
        rs.update_runstate(vault, today, {
            "pinned_rows": [native_pin],
            "overlap_grants": [],
            "planning_config_fingerprint": "",
        })
        r = client.post(
            "/validate-sequence",
            headers=_auth(client),
            json={
                "sequence": [{
                    "id": "Native task", "start": "10:00", "end": "10:30", "zone": "any",
                }],
                "assigned": [{"name": "Native task", "todoist_id": "todo-1"}],
                "anchored_blocks": [],
                "config": {},
                "pinned_rows": [native_pin],
            },
        )

        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is False
        assert body["hard_errors"] == ["pinned rows changed from immutable snapshot"]

    def test_stale_pinned_rows_are_rejected_but_empty_snapshot_remains_exempt(
        self, client, vault
    ):
        _pin_frame(vault)
        today = gather.effective_date(datetime.now())
        stored_pin = {"id": "A", "start": "12:00", "end": "12:30", "zone": "any"}
        submitted_pin = {"id": "A", "start": "13:00", "end": "13:30", "zone": "any"}
        rs.update_runstate(vault, today, {
            "pinned_rows": [stored_pin],
            "overlap_grants": [],
            "planning_config_fingerprint": "fp-current",
        })
        stale = client.post(
            "/validate-sequence",
            headers=_auth(client),
            json={
                "sequence": [submitted_pin],
                "assigned": [{"id": "A", "zone": "any"}],
                "anchored_blocks": [],
                "config": {},
                "pinned_rows": [submitted_pin],
                "overlap_grants": [],
                "planning_config_fingerprint": "fp-current",
            },
        )
        assert stale.status_code == 200
        assert stale.json()["hard_errors"] == ["planning snapshot is stale"]

        # Clear the persisted validation metadata before exercising the legacy
        # empty-snapshot exemption; the stale attempt above must not turn the
        # next request into a second stale-snapshot case.
        rs.update_runstate(vault, today, {
            "pinned_rows": [],
            "overlap_grants": [],
            "planning_config_fingerprint": "",
        })
        current = client.post(
            "/validate-sequence",
            headers=_auth(client),
            json={
                "sequence": [{"id": "A", "start": "13:00", "end": "13:30", "zone": "any"}],
                "assigned": [{"id": "A", "zone": "any"}],
                "anchored_blocks": [],
                "config": {},
            },
        )
        assert current.json()["ok"] is True

    def test_stale_overlap_grants_are_rejected(self, client, vault):
        _pin_frame(vault)
        today = gather.effective_date(datetime.now())
        stored_grant = {
            "primary_id": "A", "companion_id": "Wall",
            "primary_interval": {"start": "12:00", "end": "12:30"},
            "companion_interval": {"start": "12:00", "end": "13:00"},
            "planning_config_fingerprint": "fp-current",
        }
        submitted_grant = {
            **stored_grant,
            "primary_interval": {"start": "13:00", "end": "13:30"},
        }
        rs.update_runstate(vault, today, {
            "pinned_rows": [],
            "overlap_grants": [stored_grant],
            "planning_config_fingerprint": "fp-current",
        })
        r = client.post(
            "/validate-sequence",
            headers=_auth(client),
            json={
                "sequence": [{"id": "A", "start": "13:00", "end": "13:30", "zone": "any"}],
                "assigned": [{"id": "A", "zone": "any"}],
                "anchored_blocks": [],
                "config": {},
                "pinned_rows": [],
                "overlap_grants": [submitted_grant],
                "planning_config_fingerprint": "fp-current",
            },
        )

        assert r.status_code == 200
        assert r.json()["hard_errors"] == ["planning snapshot is stale"]

    def test_changed_anchor_makes_nonempty_validation_snapshot_stale(self, client, vault):
        today = gather.effective_date(datetime.now())
        rs.write_runstate(vault, today, rs.build_runstate({
            "anchor": "10:00",
            "eod": "23:00",
            # The saved validation snapshot was made before the anchor moved.
            "time_frame": {"anchor": "09:00", "effective_eod": "23:00"},
            "pinned_rows": [],
            "overlap_grants": [],
            "planning_config_fingerprint": "fp-current",
        }))
        r = client.post(
            "/validate-sequence",
            headers=_auth(client),
            json={
                "sequence": [{"id": "A", "start": "10:00", "end": "10:30", "zone": "any"}],
                "assigned": [{"id": "A", "zone": "any"}],
                "anchored_blocks": [],
                "config": {},
                "pinned_rows": [],
                "overlap_grants": [],
                "planning_config_fingerprint": "fp-current",
            },
        )

        assert r.status_code == 200
        assert r.json()["hard_errors"] == ["planning snapshot is stale"]
