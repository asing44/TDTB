"""Remaining-anchor population — anchored blocks must always appear.

User decision (2026-10-15): "always populate anchor blocks still remaining in
days". The model is told every anchored_block MUST appear, but nothing enforced
it. ``sequence.populate_remaining_anchors`` deterministically injects any
still-remaining, non-calendar, non-elapsed anchored block absent from the
sequence, for both /sequence and /validate-sequence, with a soft
``anchor_populated`` warning.
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import judgment  # noqa: E402
import sequence as sequence_module  # noqa: E402
from sequence import validate_sequence  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

import main as main_mod  # noqa: E402
import runstate as rs  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent.parent / "gather"))
import tdtb_gather as gather  # noqa: E402


FRAME = {"anchor": "19:15", "effective_eod": "23:45"}


def _item(id_, blocks=1, **extra):
    return {"id": id_, "name": id_, "blocks": blocks, **extra}


def _row(id_, start, end, zone="any"):
    return {"id": id_, "start": start, "end": end, "zone": zone}


def _hard(block, start, end, **extra):
    return {"Block": block, "Type": "hard", "Start": start, "End": end,
            "Duration": "30m", **extra}


def _populated(result):
    return [w for w in result.warnings
            if isinstance(w, dict) and w.get("rule") == "anchor_populated"]


def _placed_work():
    return {"sequence": [_row("Work", "19:15", "19:45")]}, [_item("Work")]


class TestPopulateRemainingAnchors:
    def test_missing_anchor_is_added_with_warning(self):
        proposal, assigned = _placed_work()
        anchored = [
            _hard("Dinner", "20:00", "20:30"),
            _hard("Night Routine", "22:00", "22:30"),
        ]
        result = validate_sequence(
            proposal, assigned, anchored, {}, time_frame=FRAME)
        assert result.ok is True, result.hard_errors
        rows = {r["id"]: r for r in proposal["sequence"]}
        assert (rows["Dinner"]["start"], rows["Dinner"]["end"]) == ("20:00", "20:30")
        assert (rows["Night Routine"]["start"],
                rows["Night Routine"]["end"]) == ("22:00", "22:30")
        assert {w["id"] for w in _populated(result)} == {"Dinner", "Night Routine"}
        assert all("was missing from the proposal" in w["detail"]
                   for w in _populated(result))
        # chronological, stable
        starts = [r["start"] for r in proposal["sequence"]]
        assert starts == sorted(starts)

    def test_present_anchor_is_untouched(self):
        proposal = {"sequence": [
            _row("Work", "19:15", "19:45"),
            _row("Dinner", "20:00", "20:30"),
        ]}
        assigned = [_item("Work")]
        anchored = [_hard("Dinner", "20:00", "20:30")]
        result = validate_sequence(
            proposal, assigned, anchored, {}, time_frame=FRAME)
        assert result.ok is True, result.hard_errors
        assert not _populated(result)
        assert [r["id"] for r in proposal["sequence"]].count("Dinner") == 1

    def test_off_skip_elapsed_calendar_ignored_are_not_injected(self):
        proposal, assigned = _placed_work()
        anchored = [
            _hard("Off Block", "20:00", "20:30", on=False),
            _hard("Skipped Block", "20:00", "20:30", skip_today=True),
            _hard("Breakfast", "07:00", "07:30"),  # ended before the anchor
            {"Block": "Meeting", "Type": "hard", "Start": "20:00",
             "End": "20:30", "Duration": "30m", "source": "calendar"},
            _hard("Ignored Block", "20:00", "20:30", capacity_class="ignored"),
            _hard("Quarantined Block", "20:00", "20:30",
                  capacity_class="quarantined"),
        ]
        result = validate_sequence(
            proposal, assigned, anchored, {}, time_frame=FRAME)
        assert result.ok is True, result.hard_errors
        assert not _populated(result)
        ids = {r["id"] for r in proposal["sequence"]}
        assert ids == {"Work"}

    def test_minting_and_trinoor_zone_rows_are_not_injected(self):
        proposal, assigned = _placed_work()
        anchored = [
            _hard("Minting", "20:00", "20:30"),
            _hard("🟡 Trinoor : AM", "20:00", "21:00"),
        ]
        result = validate_sequence(
            proposal, assigned, anchored, {}, time_frame=FRAME)
        assert result.ok is True, result.hard_errors
        assert not _populated(result)
        assert {r["id"] for r in proposal["sequence"]} == {"Work"}

    def test_window_block_uses_start_plus_duration_footprint(self):
        proposal, assigned = _placed_work()
        anchored = [{
            "Block": "Foods Dinner", "Type": "window", "Start": "18:00",
            "End": "20:30", "Duration": "60m",
        }]
        result = validate_sequence(
            proposal, assigned, anchored, {}, time_frame=FRAME)
        assert result.ok is True, result.hard_errors
        row = next(r for r in proposal["sequence"] if r["id"] == "Foods Dinner")
        assert (row["start"], row["end"]) == ("18:00", "19:00")

    def test_overlap_allowed_block_injects_as_backdrop(self):
        proposal, assigned = _placed_work()
        anchored = [_hard("Backdrop Zone", "20:00", "21:00",
                          overlap_allowed=True)]
        result = validate_sequence(
            proposal, assigned, anchored, {}, time_frame=FRAME)
        assert result.ok is True, result.hard_errors
        row = next(r for r in proposal["sequence"] if r["id"] == "Backdrop Zone")
        assert row.get("backdrop") is True
        # permeable backdrop rows are validated-out, but still injected once
        assert _populated(result)

    def test_population_is_idempotent(self):
        proposal, assigned = _placed_work()
        anchored = [_hard("Dinner", "20:00", "20:30")]
        first = validate_sequence(
            proposal, assigned, anchored, {}, time_frame=FRAME)
        assert first.ok is True
        assert _populated(first)
        second = validate_sequence(
            proposal, assigned, anchored, {}, time_frame=FRAME)
        assert second.ok is True
        assert not _populated(second)
        assert [r["id"] for r in proposal["sequence"]].count("Dinner") == 1

    def test_injected_workout_anchor_does_not_create_hard_error(self):
        # A morning workout anchor is a source wall; injection must not trip
        # the movable-work morning-workout HARD ban.
        proposal = {"sequence": [_row("Work", "07:00", "07:30")]}
        assigned = [_item("Work")]
        anchored = [_hard("Workout Routine", "08:00", "09:00")]
        result = validate_sequence(
            proposal, assigned, anchored, {},
            time_frame={"anchor": "07:00", "effective_eod": "23:00"})
        assert result.ok is True, result.hard_errors
        assert _populated(result)

    def test_no_anchor_frame_skips_population(self):
        proposal, assigned = _placed_work()
        anchored = [_hard("Dinner", "20:00", "20:30")]
        result = validate_sequence(proposal, assigned, anchored, {})
        assert result.ok is True
        assert not _populated(result)
        assert {r["id"] for r in proposal["sequence"]} == {"Work"}


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


_DINNER = {"Block": "Dinner", "Type": "hard", "Start": "20:00",
           "End": "20:30", "Duration": "30m"}


class TestValidateSequenceRoute:
    def test_missing_anchor_is_populated_over_http(self, client):
        _pin_frame()
        body = {
            "sequence": [_row("Work", "19:15", "19:45")],
            "assigned": [_item("Work")],
            "anchored_blocks": [_DINNER],
            "config": {},
        }
        r = client.post("/validate-sequence", headers=_auth(client), json=body)
        assert r.status_code == 200, r.text
        payload = r.json()
        assert payload["ok"] is True
        assert any(w.get("rule") == "anchor_populated" and w.get("id") == "Dinner"
                   for w in payload["warnings"])


class TestSequenceRoute:
    def test_stubbed_proposer_populates_missing_anchor_over_http(
        self, client, monkeypatch
    ):
        _pin_frame()

        def _propose(assigned, config, anchored_blocks, ctx=None):
            return {"sequence": [
                {"id": a.get("id") or a.get("name"), "start": "19:15",
                 "end": "19:45", "zone": "any"} for a in assigned
            ]}

        monkeypatch.setattr(judgment, "propose_sequence", _propose)
        r = client.post(
            "/sequence",
            headers=_auth(client),
            json={
                "assigned": [{"id": "Work", "name": "Work", "blocks": 1}],
                "config": {},
                "anchored_blocks": [_DINNER],
            },
        )
        assert r.status_code == 200, r.text
        payload = r.json()
        row = next(r_ for r_ in payload["sequence"] if r_["id"] == "Dinner")
        assert (row["start"], row["end"]) == ("20:00", "20:30")
        assert any(w.get("rule") == "anchor_populated"
                   for w in payload["warnings"])
