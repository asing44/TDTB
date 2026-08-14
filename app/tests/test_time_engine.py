"""Tests for time_engine.py — anchor pin, effective-EOD scan, short-circuit.

Pure module, injectable clock (a datetime is passed in; the module never calls
datetime.now itself). Contract: SKILL.md 0.2 (anchor = live clock rounded UP to
anchor.round_to_minutes; override wins) and 0.4 hard-stop detection (fixed
commitment starting within the 2-hour window before config eod pins
effective_eod to that start).
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import time_engine  # noqa: E402

NOW = datetime(2026, 7, 14, 14, 7)


def frame(**kw):
    args = dict(now=NOW, config_eod="23:59", round_to_minutes=15, busy_events=[])
    args.update(kw)
    return time_engine.compute_time_frame(**args)


class TestAnchorPin:
    def test_rounds_up_to_next_boundary(self):
        assert frame().anchor == "14:15"

    def test_exact_boundary_stays(self):
        assert frame(now=datetime(2026, 7, 14, 14, 15)).anchor == "14:15"

    def test_round_to_30(self):
        assert frame(round_to_minutes=30).anchor == "14:30"

    def test_override_wins_over_clock(self):
        assert frame(anchor_override="16:00").anchor == "16:00"


class TestEffectiveEod:
    def test_no_busy_events_uses_config_eod(self):
        f = frame()
        assert f.effective_eod == "23:59" and f.eod_note is None

    def test_commitment_in_window_pins_effective_eod(self):
        f = frame(busy_events=[{"start": "22:30", "title": "Concert"}])
        assert f.effective_eod == "22:30"
        assert "Concert" in (f.eod_note or "")

    def test_commitment_outside_window_ignored(self):
        f = frame(busy_events=[{"start": "19:00", "title": "Dinner out"}])
        assert f.effective_eod == "23:59" and f.eod_note is None

    def test_earliest_in_window_wins(self):
        f = frame(busy_events=[{"start": "23:00", "title": "B"},
                               {"start": "22:15", "title": "A"}])
        assert f.effective_eod == "22:15" and "A" in f.eod_note

    def test_eod_override_wins_and_suppresses_scan(self):
        f = frame(eod_override="21:00",
                  busy_events=[{"start": "22:30", "title": "Concert"}])
        assert f.effective_eod == "21:00" and f.eod_note is None


class TestTotalAndShortCircuit:
    def test_total_blocks_floored(self):
        # 14:15 -> 23:59 = 584 min -> 19 whole blocks
        f = frame()
        assert f.total_blocks == 19 and f.no_time_left is False

    def test_anchor_at_eod_short_circuits(self):
        f = frame(anchor_override="21:00", eod_override="21:00")
        assert f.total_blocks == 0 and f.no_time_left is True

    def test_anchor_past_eod_short_circuits(self):
        f = frame(now=datetime(2026, 7, 14, 22, 40), eod_override="21:00")
        assert f.total_blocks <= 0 and f.no_time_left is True

    def test_sub_block_remainder_is_no_time(self):
        f = frame(anchor_override="21:00", eod_override="21:20")
        assert f.total_blocks == 0 and f.no_time_left is True


class TestAsDict:
    def test_as_dict_shape(self):
        d = frame().as_dict()
        assert set(d) >= {"now", "anchor", "effective_eod", "eod_note",
                          "config_eod", "total_blocks", "no_time_left"}
        assert d["now"] == "14:07"


class TestToHhmm:
    def test_12h_pm(self):
        assert time_engine.to_hhmm("11:59 PM") == "23:59"

    def test_12h_am(self):
        assert time_engine.to_hhmm("7:45 AM") == "07:45"

    def test_noon_and_midnight(self):
        assert time_engine.to_hhmm("12:00 PM") == "12:00"
        assert time_engine.to_hhmm("12:15 AM") == "00:15"

    def test_24h_passthrough(self):
        assert time_engine.to_hhmm("16:20") == "16:20"

    def test_junk_is_none(self):
        assert time_engine.to_hhmm("—") is None
        assert time_engine.to_hhmm(None) is None
        assert time_engine.to_hhmm("") is None
