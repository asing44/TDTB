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
        # the returned plan parks the overflow tail at 17:00, packed in order
        expected = {
            "Semi-Monthly Adventure": ("17:00", "17:30"),
            "WALL\u22c5E-SUCC clean": ("17:30", "18:00"),
            "update gifster": ("18:00", "18:30"),
        }
        for item_id, span in expected.items():
            row = next(r for r in proposal["sequence"] if r["id"] == item_id)
            assert (row["start"], row["end"]) == span
        # re-sorted chronologically (stable): parked rows now precede the day
        starts = [r["start"] for r in proposal["sequence"]]
        assert starts == sorted(starts)
        reseated = _warnings_by_rule(result, "overflow_reseated")
        assert {w["id"] for w in reseated} == set(OVERFLOW_IDS)
        assert all("exceeds the day's capacity" in w["detail"] for w in reseated)
        # parked at 17:00, before the 19:15 anchor: soft past-placement only
        assert {w["id"] for w in _warnings_by_rule(result, "placement_past")} == set(
            OVERFLOW_IDS
        )
        assert not _has_hard(result, "not after start")


class TestReseatRule:
    def test_two_block_item_parks_at_full_duration(self):
        proposal = {"sequence": [_row("Deep", "23:59", "23:59")]}
        result = validate_sequence(
            proposal, [_item("Deep", blocks=2)], [], {}, time_frame=FRAME)
        assert result.ok is True
        assert (proposal["sequence"][0]["start"],
                proposal["sequence"][0]["end"]) == ("17:00", "18:00")

    def test_valid_length_tail_row_is_also_reseated(self):
        # A representable tail row (23:45-23:59, start >= effective EOD) still
        # counts as overflow and is parked at 17:00.
        proposal = {"sequence": [_row("Late", "23:45", "23:59")]}
        result = validate_sequence(
            proposal, [_item("Late")], [], {}, time_frame=FRAME)
        assert result.ok is True
        assert (proposal["sequence"][0]["start"],
                proposal["sequence"][0]["end"]) == ("17:00", "17:30")
        assert _warnings_by_rule(result, "overflow_reseated")

    def test_reseat_packs_consecutively_and_stays_chronological(self):
        # Parked rows pack 17:00, 17:30, ... in original order even when the
        # original rows were ordered earlier than the placed day.
        proposal = {"sequence": [
            _row("A", "23:45", "23:59"),
            _row("B", "23:59", "23:59"),
        ]}
        result = validate_sequence(
            proposal, [_item("A"), _item("B")], [], {}, time_frame=FRAME)
        assert result.ok is True
        assert not _has_hard(result, "not in chronological start order")
        by_id = {r["id"]: r for r in proposal["sequence"]}
        assert (by_id["A"]["start"], by_id["A"]["end"]) == ("17:00", "17:30")
        assert (by_id["B"]["start"], by_id["B"]["end"]) == ("17:30", "18:00")

    def test_parking_spill_goes_past_eod(self):
        # 15 one-block overflow rows cannot all pack before 23:59. The first
        # 13 fill the free gaps (17:00-23:30, full duration, packed in
        # order); the remaining two keep their same-day past-end-of-day
        # marker (23:59-23:59) with a visible soft overflow_past_eod warning
        # and stay in the sequence (never-bump). This replaces the pre
        # 2026-10-16 clamping that piled the trailing rows on 23:59 — see
        # asing44/TDTB#15 and app/tests/test_overflow_anchors.py.
        ids = [f"O{i}" for i in range(15)]
        proposal = {"sequence": [_row(i, "23:59", "23:59") for i in ids]}
        result = validate_sequence(
            proposal, [_item(i) for i in ids], [], {}, time_frame=FRAME)
        assert result.ok is True, result.hard_errors
        by_id = {row["id"]: row for row in proposal["sequence"]}
        assert sorted(by_id) == sorted(ids)  # never-bump
        placed = ids[:13]
        spilled = ids[13:]
        for offset, item_id in enumerate(placed):
            start_min = int(by_id[item_id]["start"][:2]) * 60 + int(
                by_id[item_id]["start"][3:])
            end_min = int(by_id[item_id]["end"][:2]) * 60 + int(
                by_id[item_id]["end"][3:])
            assert (start_min, end_min) == (17 * 60 + 30 * offset,
                                            17 * 60 + 30 * (offset + 1))
        for item_id in spilled:
            assert (by_id[item_id]["start"], by_id[item_id]["end"]) == (
                "23:59", "23:59")
        assert {w["id"] for w in _warnings_by_rule(result, "overflow_past_eod")} \
            == set(spilled)
        assert len(_warnings_by_rule(result, "overflow_reseated")) == len(placed)

    def test_reseat_is_idempotent(self):
        proposal, assigned = _live_scenario()
        first = validate_sequence(proposal, assigned, [], {}, time_frame=FRAME)
        assert first.ok is True
        second = validate_sequence(proposal, assigned, [], {}, time_frame=FRAME)
        assert second.ok is True
        assert not _warnings_by_rule(second, "overflow_reseated")

    def test_parked_row_skips_anchor_span(self):
        # asing44/TDTB#15 (2026-10-16, operator decision) reverses the earlier
        # overflow exemption from anchored-block overlap (commit 4add629 era):
        # anchors are NOT shareable. The parked row now starts after the
        # 17:00-17:30 anchored block instead of sharing it, so no
        # overflow_overlap naming the pair is emitted.
        anchored = [{
            "Block": "Evening Block", "Type": "hard",
            "Start": "17:00", "End": "17:30", "Duration": "30m",
            "overlap_allowed": False,
        }]
        proposal = {"sequence": [_row("A", "23:59", "23:59")]}
        result = validate_sequence(
            proposal, [_item("A")], anchored, {}, time_frame=FRAME)
        assert result.ok is True
        assert result.hard_errors == []
        row = next(r for r in proposal["sequence"] if r["id"] == "A")
        assert (row["start"], row["end"]) == ("17:30", "18:00")
        assert not any(
            w.get("rule") == "overflow_overlap"
            and "Evening Block" in (str(w.get("detail")) + str(w.get("affected_rows")))
            for w in result.warnings
        )
        assert _warnings_by_rule(result, "overflow_reseated")

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
        by_id = {r["id"]: r for r in proposal["sequence"]}
        assert (by_id["S1"]["start"], by_id["S1"]["end"]) == ("17:00", "17:30")
        assert (by_id["S2"]["start"], by_id["S2"]["end"]) == ("17:30", "18:00")
        # the systems same-start rule broke because the park is consecutive:
        # it degrades to a soft overflow flag instead of re-billing the model.
        assert any(w.get("rule") == "overflow_overlap"
                   for w in result.warnings)


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
        parked = [row for row in outcome.result["sequence"]
                  if row.get("id") in OVERFLOW_IDS]
        # An injected "Quick Tasks" row occupies 17:00-17:30; the three park
        # rows follow consecutively in order.
        assert [(row["start"], row["end"]) for row in parked] == [
            ("17:30", "18:00"), ("18:00", "18:30"), ("18:30", "19:00")]
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
        parked = [row for row in payload["sequence"]
                  if row["id"] in OVERFLOW_IDS]
        assert [(row["start"], row["end"]) for row in parked] == [
            ("17:00", "17:30"), ("17:30", "18:00"), ("18:00", "18:30")]
        assert any(
            w.get("rule") == "overflow_reseated" for w in payload["warnings"]
        )
