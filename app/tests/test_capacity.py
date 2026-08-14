"""Tests for capacity.py — the skill's canonical 6-segment model (757–778).

Invariants under test: segment deduction order, buffer formula with max(0,…)
clamps, free SIGNED and never clamped (OVERASSIGNED fires on free < 0),
canonical readout strings (remaining / ratio / legend / counters).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import capacity  # noqa: E402


def calc(**kw):
    args = dict(total=19, fixed=2, anchored=5, habits=1, mint=0, selected=6,
                buffering_pct=0.10, deep_count=1, mixed_count=2,
                caps={"deep": 2, "mixed": 4}, habits_note="habits: 3 done · 2 left")
    args.update(kw)
    return capacity.compute_capacity(**args)


class TestSegments:
    def test_mint_is_its_own_segment_before_selected_and_buffer(self):
        c = calc(mint=2, selected=4)
        assert c.mint == 2
        assert c.buffer == 1  # ceil((19-2-5-1-2) * .10)
        assert c.free == 4
        assert "Habits 1 · Mint 2 · Selected 4" in c.legend

    def test_buffer_formula(self):
        # raw_remaining = 19-2-5-1 = 11; ceil(11*0.10) = 2
        c = calc()
        assert c.buffer == 2
        assert c.available_for_selection == 9

    def test_free_signed_positive(self):
        c = calc()  # 19-2-5-1-2-6 = 3
        assert c.free == 3 and c.overassigned is False

    def test_free_signed_negative_overassigned(self):
        c = calc(selected=12)  # free = -3
        assert c.free == -3 and c.overassigned is True

    def test_overcommitted_morning_clamps_buffer_not_free(self):
        c = calc(total=4, fixed=3, anchored=3, habits=1, selected=0)
        assert c.buffer == 0            # raw_remaining clamped to 0
        assert c.free == -3             # free stays signed

    def test_buffer_off(self):
        assert calc(buffering_pct=0).buffer == 0


class TestReadout:
    def test_remaining_positive(self):
        # free=3 -> 90min -> "1hr 30min"
        assert calc().remaining == "⬆ 1hr 30min left · 3 blk"

    def test_remaining_zero(self):
        c = calc(selected=9)
        assert c.remaining == "⬆ fully booked · 0 blk left"

    def test_remaining_over(self):
        c = calc(selected=12)
        assert c.remaining == "⚠ 1hr 30min over · 3 blk"

    def test_hrs_min_under_hour(self):
        assert calc(selected=8).remaining == "⬆ 30min left · 1 blk"

    def test_half_block_readout_is_fifteen_minutes(self):
        c = calc(selected=8.5)
        assert c.remaining == "⬆ 15min left · 0.5 blk"
        assert "Selected 8.5" in c.legend
        assert "Free 0.5" in c.legend
        assert ".0" not in c.legend

    def test_hrs_min_whole_hours(self):
        assert calc(selected=5).remaining == "⬆ 2hr left · 4 blk"

    def test_ratio(self):
        assert calc().ratio == "16 / 19 blk"

    def test_legend(self):
        assert calc().legend == ("Fixed 2 · Anchored 5 · Habits 1 · Mint 0 · Selected 6 "
                                 "· Buffer 2 · Free 3 · Total 19 (habits: 3 done · 2 left)")

    def test_legend_without_habits_note(self):
        c = calc(habits_note=None)
        assert c.legend.endswith("Total 19")

    def test_counters(self):
        assert calc().counters == "deep: 1 / 2 · mixed: 2 / 4"


class TestAsDict:
    def test_shape(self):
        d = calc().as_dict()
        assert set(d) >= {"total", "fixed", "anchored", "habits", "mint",
                          "selected", "buffer", "free", "overassigned",
                          "available_for_selection", "remaining", "ratio",
                          "legend", "counters"}
