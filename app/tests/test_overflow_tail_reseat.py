"""Overflow-tail re-seat — over-capacity must degrade, never 422.

Live defect (2026-10-15, 19:12): with anchor 19:15 and effective EOD 23:45 the
day carried 16 blocks of load against 9 blocks of room. The model followed its
prompt and parked the overflow items "at or after effective_eod" — but same-day
HH:MM cannot represent a crossing, so it emitted ``start == end == "23:59"``.
``validate_sequence`` then hard-failed ``non_positive_interval`` and
``never_bump``, and the whole plan was REJECTED (422) instead of degrading.

G16 contract: an over-capacity day must degrade — the overflow tail is soft.
These tests pin the deterministic re-seat of unrepresentable tail rows.
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import judgment  # noqa: E402
import planning  # noqa: E402
import sequence as sequence_module  # noqa: E402
from sequence import validate_sequence  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

import main as main_mod  # noqa: E402
import runstate as rs  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent.parent / "gather"))
import tdtb_gather as gather  # noqa: E402


# The live frame: 9 blocks of room (19:15 → 23:45), overflow parked at 23:59.
FRAME = {"anchor": "19:15", "effective_eod": "23:45"}
OVERFLOW_IDS = ("Semi-Monthly Adventure", "WALL\u22c5E-SUCC clean", "update gifster")


def _item(id_, blocks=1, **extra):
    return {"id": id_, "name": id_, "blocks": blocks, **extra}


def _row(id_, start, end, zone="any"):
    return {"id": id_, "start": start, "end": end, "zone": zone}


def _live_scenario():
    """Three placed rows plus the three overflow rows parked at 23:59."""
    assigned = [
        _item("Placed 1"), _item("Placed 2"), _item("Placed 3"),
        *[_item(item_id) for item_id in OVERFLOW_IDS],
    ]
    sequence = [
        _row("Placed 1", "19:15", "19:45"),
        _row("Placed 2", "19:45", "20:15"),
        _row("Placed 3", "20:15", "20:45"),
        *[_row(item_id, "23:59", "23:59") for item_id in OVERFLOW_IDS],
    ]
    return {"sequence": sequence}, assigned


def _warnings_by_rule(result, rule):
    return [w for w in result.warnings if isinstance(w, dict) and w.get("rule") == rule]


def _has_hard(result, needle):
    return any(needle in error for error in result.hard_errors)


class TestReseatLiveScenario:
    def test_overcapacity_degrades_instead_of_rejecting(self):
        proposal, assigned = _live_scenario()
        result = validate_sequence(
            proposal, assigned, [], {}, time_frame=FRAME)

        assert result.ok is True
        assert result.hard_errors == []
        # never-bump: every assigned item still present exactly once
        ids = [row["id"] for row in proposal["sequence"]]
        assert sorted(ids) == sorted(a["id"] for a in assigned)
        # the returned plan carries representable, re-seated times
        for item_id in OVERFLOW_IDS:
            row = next(r for r in proposal["sequence"] if r["id"] == item_id)
            assert (row["start"], row["end"]) == ("23:29", "23:59")
        reseated = _warnings_by_rule(result, "overflow_reseated")
        assert {w["id"] for w in reseated} == set(OVERFLOW_IDS)
        assert all("did not fit before midnight" in w["detail"] for w in reseated)
        # the existing soft past-EOD warning still fires for each parked row
        assert {w["id"] for w in _warnings_by_rule(result, "past_eod")} == set(
            OVERFLOW_IDS
        )
        assert not _has_hard(result, "not after start")


class TestReseatRule:
    def test_two_block_item_reseats_to_full_duration(self):
        proposal = {"sequence": [_row("Deep", "23:59", "23:59")]}
        result = validate_sequence(
            proposal, [_item("Deep", blocks=2)], [], {}, time_frame=FRAME)
        assert result.ok is True
        assert (proposal["sequence"][0]["start"],
                proposal["sequence"][0]["end"]) == ("22:59", "23:59")

    def test_late_truncation_is_legal_and_not_reseated(self):
        # start 23:45 with a 30-min budget truncates to 23:59 — representable,
        # so it is NOT a re-seat case (matches today's contract).
        proposal = {"sequence": [_row("Late", "23:45", "23:59")]}
        result = validate_sequence(
            proposal, [_item("Late")], [], {}, time_frame=FRAME)
        assert result.ok is True
        assert not _warnings_by_rule(result, "overflow_reseated")
        assert _warnings_by_rule(result, "past_eod")

    def test_reseat_keeps_chronological_order(self):
        # A preceding valid row starting later than the full-duration target
        # must not be overtaken; the re-seated row clamps to the prior start.
        proposal = {"sequence": [
            _row("A", "23:45", "23:59"),
            _row("B", "23:59", "23:59"),
        ]}
        result = validate_sequence(
            proposal, [_item("A"), _item("B")], [], {}, time_frame=FRAME)
        assert result.ok is True
        assert not _has_hard(result, "not in chronological start order")
        row_b = next(r for r in proposal["sequence"] if r["id"] == "B")
        assert (row_b["start"], row_b["end"]) == ("23:45", "23:59")

    def test_reseat_is_idempotent(self):
        proposal, assigned = _live_scenario()
        first = validate_sequence(proposal, assigned, [], {}, time_frame=FRAME)
        assert first.ok is True
        second = validate_sequence(proposal, assigned, [], {}, time_frame=FRAME)
        assert second.ok is True
        assert not _warnings_by_rule(second, "overflow_reseated")

    def test_reseat_against_pre_eod_hard_wall_stays_soft(self):
        # A config-backed hard wall before EOD would normally be an
        # acceptable-defect warning; a re-seated tail row overlapping it must
        # stay a warning, never a rejection.
        anchored = [{
            "Block": "Night Routine", "Type": "hard",
            "Start": "23:00", "End": "23:45", "Duration": "45m",
            "overlap_allowed": False,
        }]
        proposal = {"sequence": [_row("A", "23:59", "23:59")]}
        result = validate_sequence(
            proposal, [_item("A")], anchored, {}, time_frame=FRAME)
        assert result.ok is True
        assert result.hard_errors == []
        assert any(
            w.get("rule") in ("unexpected_overlap", "overflow_overlap")
            for w in result.warnings
        )

    def test_reseat_repairs_systems_group_overlap_grant(self):
        assigned = [
            _item("S1", tags=["systems"]),
            _item("S2", tags=["systems"]),
        ]
        grant = {
            "primary_id": "S1", "companion_id": "S2",
            "primary_interval": {"start": "23:59", "end": "23:59"},
            "companion_interval": {"start": "23:59", "end": "23:59"},
            "reason": "systems period", "planning_config_fingerprint": "",
        }
        proposal = {
            "sequence": [_row("S1", "23:59", "23:59"), _row("S2", "23:59", "23:59")],
            "overlap_grants": [grant],
        }
        result = validate_sequence(
            proposal, assigned, [], {}, time_frame=FRAME)
        assert result.ok is True, result.hard_errors
        assert (proposal["sequence"][0]["start"],
                proposal["sequence"][0]["end"]) == ("23:29", "23:59")
        assert (proposal["sequence"][1]["start"],
                proposal["sequence"][1]["end"]) == ("23:29", "23:59")


class TestReseatDoesNotWiden:
    def test_reversed_ordinary_range_still_invalid(self):
        proposal = {"sequence": [_row("A", "14:00", "13:00")]}
        result = validate_sequence(
            proposal, [_item("A")], [], {}, time_frame=FRAME)
        assert result.ok is False
        assert _has_hard(result, "not after start")

    def test_zero_length_before_eod_still_invalid(self):
        proposal = {"sequence": [_row("A", "14:00", "14:00")]}
        result = validate_sequence(
            proposal, [_item("A")], [], {}, time_frame=FRAME)
        assert result.ok is False
        assert _has_hard(result, "not after start")

    def test_non_assigned_zero_length_tail_row_still_rejected(self):
        proposal = {"sequence": [_row("ghost", "23:59", "23:59")]}
        result = validate_sequence(
            proposal, [_item("A")], [], {}, time_frame=FRAME)
        assert result.ok is False
        assert _has_hard(result, "not after start")
        assert not _warnings_by_rule(result, "overflow_reseated")

    def test_no_time_frame_keeps_legacy_rejection(self):
        proposal = {"sequence": [_row("A", "23:59", "23:59")]}
        result = validate_sequence(proposal, [_item("A")], [], {})
        assert result.ok is False
        assert _has_hard(result, "not after start")


class TestJudgmentRelaxation:
    def test_semantic_tail_zero_length_no_longer_raises(self):
        # semantic-checks callers must not burn a billed retry on the parked row
        judgment._validate_sequence_proposal(
            {"sequence": [_row("O1", "23:59", "23:59")]},
            config={"time": {"effective_eod": "23:45"}},
            assigned=[{"name": "O1", "blocks": 1}],
        )

    def test_semantic_reversed_ordinary_range_still_raises(self):
        with pytest.raises(ValueError):
            judgment._validate_sequence_proposal(
                {"sequence": [_row("A", "14:00", "13:00")]},
                config={"time": {"effective_eod": "23:45"}},
                assigned=[{"name": "A", "blocks": 1}],
            )

    def test_semantic_zero_length_before_eod_still_raises(self):
        with pytest.raises(ValueError):
            judgment._validate_sequence_proposal(
                {"sequence": [_row("A", "14:00", "14:00")]},
                config={"time": {"effective_eod": "23:45"}},
                assigned=[{"name": "A", "blocks": 1}],
            )


class TestPlannerBoundary:
    def test_run_proposal_returns_reseated_sequence(self):
        assigned = [_item(item_id) for item_id in OVERFLOW_IDS]
        inputs = {
            "assigned": assigned,
            "config": {},
            "anchored_blocks": [],
            "day_setup": {"schedulable": {}},
            "today": datetime.now().date(),
            "time_frame": dict(FRAME),
            "day_semantics": {},
            "planning_config_fingerprint": "fp",
            "pinned_rows": [],
        }
        prepared = planning.prepare_sequence(inputs, mode="proposal")

        def _park_all(movable, config, anchored_blocks, ctx=None):
            return {"sequence": [
                {"id": a.get("id") or a.get("name"),
                 "start": "23:59", "end": "23:59", "zone": "any"}
                for a in movable
            ], "overlap_grants": []}

        outcome = planning.run_proposal(prepared, _park_all)
        assert outcome.validation.ok is True, outcome.validation.hard_errors
        assert outcome.result is not None
        for row in outcome.result["sequence"]:
            if row.get("id") in OVERFLOW_IDS:
                assert (row["start"], row["end"]) == ("23:29", "23:59")
        assert any(
            w.get("rule") == "overflow_reseated"
            for w in outcome.result["warnings"]
        )


# ---------------------------------------------------------------------------
# HTTP routes
# ---------------------------------------------------------------------------

@pytest.fixture
def vault(tmp_path) -> Path:
    return tmp_path / "vault-root"


@pytest.fixture
def client(vault) -> TestClient:
    vault.mkdir()
    app = main_mod.create_app()
    c = TestClient(app)
    c.app_token = app.state.token
    return c


def _auth(client: TestClient) -> dict:
    return {"X-TDTB-Token": client.app_token}


def _pin_frame() -> None:
    today = gather.effective_date(datetime.now())
    rs.write_runstate(today, rs.build_runstate(
        {"anchor": "19:15", "eod": "23:45"}))


class TestValidateSequenceRoute:
    def test_overflow_tail_degrades_over_http(self, client):
        _pin_frame()
        proposal, assigned = _live_scenario()
        body = {
            "sequence": proposal["sequence"],
            "assigned": assigned,
            "anchored_blocks": [],
            "config": {},
        }
        r = client.post("/validate-sequence", headers=_auth(client), json=body)
        assert r.status_code == 200, r.text
        payload = r.json()
        assert payload["ok"] is True
        assert payload["hard_errors"] == []
        assert any(
            w.get("rule") == "overflow_reseated" for w in payload["warnings"]
        )


class TestSequenceRoute:
    def test_stubbed_proposer_reseats_overflow_over_http(self, client, monkeypatch):
        _pin_frame()

        def _propose(assigned, config, anchored_blocks, ctx=None):
            rows = []
            cursor = 19 * 60 + 15
            for a in assigned:
                rid = a.get("id") or a.get("name")
                if rid in OVERFLOW_IDS:
                    rows.append({"id": rid, "start": "23:59", "end": "23:59",
                                 "zone": "any"})
                    continue
                rows.append({
                    "id": rid,
                    "start": f"{cursor // 60:02d}:{cursor % 60:02d}",
                    "end": f"{(cursor + 30) // 60:02d}:{(cursor + 30) % 60:02d}",
                    "zone": "any",
                })
                cursor += 30
            return {"sequence": rows}

        monkeypatch.setattr(judgment, "propose_sequence", _propose)
        r = client.post(
            "/sequence",
            headers=_auth(client),
            json={
                "assigned": [{"id": item_id} for item_id in OVERFLOW_IDS],
                "config": {},
                "anchored_blocks": [],
            },
        )
        assert r.status_code == 200, r.text
        payload = r.json()
        for item_id in OVERFLOW_IDS:
            row = next(r_ for r_ in payload["sequence"] if r_["id"] == item_id)
            assert (row["start"], row["end"]) == ("23:29", "23:59")
        assert any(
            w.get("rule") == "overflow_reseated" for w in payload["warnings"]
        )
