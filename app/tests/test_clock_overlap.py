"""Clock-overlap contract — overlapping fixed claims count once.

Calendar events, work meetings and anchored blocks all claim wall-clock time.
Two claims on the same minutes are one claim on the day, so the segments keep
their own sizes (the operator asked to still see Calendar 2h and Anchors 3h)
and the shared minutes come back as a single `overlap` credit against the
free/task-room arithmetic.

A flexible window anchor (``Type: window`` with an End) only collides when its
window has no room left, so a dinner window that can slide past an evening
meeting is never credited — over-crediting free time would be a new bug.
"""
from __future__ import annotations

from datetime import datetime

import capacity
import main as main_mod

TODAY = datetime(2026, 7, 14)


def _event(name: str, start: str, end: str, capacity_class: str = "fixed") -> dict:
    return {
        "Block": name,
        "Start": start,
        "End": end,
        "source": "calendar",
        "calendar_id": f"CAL-{name}",
        "calendar_title": name,
        "capacity_class": capacity_class,
    }


def _anchor(name: str, start: str, duration: str, *, end: str | None = None,
            kind: str = "hard") -> dict:
    spec = {"Block": name, "Start": start, "Duration": duration, "Type": kind}
    if end:
        spec["End"] = end
    return spec


def _frame(busy: list[dict], anchors: list[dict], *, work_minutes: int = 0,
           habits: dict | None = None):
    config = {
        "Defaults": {
            "eod": "20:00",
            "anchor.round_to_minutes": 15,
            "buffering.off_pct": 0,
        },
        "Anchored Lifestyle Blocks": anchors,
    }
    return main_mod._capacity_frame(
        config,
        {"anchor": "08:00", "eod": "20:00", "buffering": "off"},
        busy,
        habits or {"est_minutes": 0, "done": 0, "outstanding": 0},
        {"effective_allotment_minutes": work_minutes},
        now=TODAY,
    )


def test_rigid_anchor_inside_a_meeting_is_counted_once():
    # 08:00–20:00 frame = 24 blk. The meeting and Sudsing share 09:00–09:30.
    _time, cap = _frame(
        [_event("Meegy cooking", "09:00", "10:00")],
        [_anchor("Sudsing", "9:00 AM", "30m")],
    )

    assert cap.fixed == 2
    assert cap.anchored == 1
    assert cap.overlap == 1
    assert cap.free == 22  # 24 − 2 − 1 + 1
    assert cap.available_for_selection == 22


def test_overlapping_anchors_count_once():
    _time, cap = _frame(
        [],
        [_anchor("Sudsing", "9:00 AM", "30m"), _anchor("Live", "9:00 AM", "30m")],
    )

    assert cap.anchored == 2
    assert cap.overlap == 1
    assert cap.free == 23


def test_flexible_window_anchor_slides_into_its_free_room_and_is_not_credited():
    # The dinner window is 09:00–11:00 (60m needed): the meeting eats 09:00–10:00
    # but the window still holds a free hour, so nothing overlaps.
    _time, cap = _frame(
        [_event("Meegy cooking", "09:00", "10:00")],
        [_anchor("Foods Dinner", "9:00 AM", "60m", end="11:00 AM", kind="window")],
    )

    assert cap.fixed == 2
    assert cap.anchored == 2
    assert cap.overlap == 0
    assert cap.free == 20


def test_flexible_window_anchor_with_no_room_left_is_credited():
    # 09:00–10:00 window, 60m needed, and the meeting fills the whole window.
    _time, cap = _frame(
        [_event("Meegy cooking", "09:00", "10:00")],
        [_anchor("Foods Dinner", "9:00 AM", "60m", end="10:00 AM", kind="window")],
    )

    assert cap.overlap == 2
    assert cap.free == 22  # 24 − 2 − 2 + 2


def test_work_meeting_overlapping_a_fixed_event_counts_once():
    _time, cap = _frame(
        [
            _event("Standup", "09:00", "10:00", "work"),
            _event("Dentist", "09:00", "10:00"),
        ],
        [],
        work_minutes=0,
    )

    assert cap.fixed == 2
    assert cap.mint == 2
    assert cap.overlap == 2
    assert cap.free == 22  # 24 − 2 − 2 + 2


def test_abstract_work_allotment_is_never_credited_as_overlap():
    # An 8-block work reservation with no meetings is a reservation, not clock
    # time: it must keep costing the full envelope.
    _time, cap = _frame([], [], work_minutes=240)

    assert cap.mint == 8
    assert cap.overlap == 0
    assert cap.free == 16


def test_meetings_inside_the_allotment_keep_the_envelope_and_credit_nothing():
    _time, cap = _frame([_event("Standup", "09:00", "10:00", "work")], [],
                        work_minutes=240)

    assert cap.mint == 8
    assert cap.overlap == 0
    assert cap.free == 16


def test_no_overlap_leaves_the_capacity_numbers_untouched():
    _time, cap = _frame(
        [_event("Meegy cooking", "09:00", "10:00")],
        [_anchor("Sudsing", "6:00 PM", "30m")],
    )

    assert cap.fixed == 2
    assert cap.anchored == 1
    assert cap.overlap == 0
    assert cap.free == 21


def test_anchor_fully_outside_the_frame_claims_no_overlap_credit():
    # Nightly anchor at 23:00 sits past the 20:00 eod: it costs nothing and
    # cannot borrow free time from a meeting it never meets.
    _time, cap = _frame(
        [_event("Meegy cooking", "19:00", "20:00")],
        [_anchor("Night Routine", "11:00 PM", "45m")],
    )

    assert cap.anchored == 0
    assert cap.overlap == 0


def test_anchor_without_a_start_costs_capacity_but_credits_nothing():
    # A configured anchor with no parseable Start still costs its blocks, but it
    # has no clock position to share: crediting it would invent free time.
    spec = {"Block": "Morning Routines", "Duration": "30m", "Type": "hard"}
    _time, cap = _frame([_event("Meegy cooking", "09:00", "10:00")], [spec])

    assert cap.fixed == 2
    assert cap.anchored == 1
    assert cap.overlap == 0
    assert cap.free == 21


def test_anchor_without_a_start_credits_nothing_on_an_empty_day():
    spec = {"Block": "Morning Routines", "Duration": "45m", "Type": "hard"}
    _time, cap = _frame([], [spec])

    assert cap.anchored == 2
    assert cap.overlap == 0
    assert cap.free == 22


def test_overlap_is_part_of_the_canonical_capacity_readout():
    c = capacity.compute_capacity(
        total=24, fixed=2, anchored=3, habits=0, mint=0, selected=0,
        buffering_pct=0.0, overlap=2,
    )

    assert c.overlap == 2
    assert c.free == 21  # 24 − 2 − 3 + 2
    assert c.available_for_selection == 21
    assert c.as_dict()["overlap"] == 2
    assert "Shared 2" in c.legend


def test_overlap_never_floors_free_below_the_clock_union():
    # Segments 24 blk of claims on a 24 blk day, 6 of them shared.
    c = capacity.compute_capacity(
        total=24, fixed=12, anchored=12, habits=0, mint=0, selected=0,
        buffering_pct=0.0, overlap=6,
    )

    assert c.free == 6
    assert c.overassigned is False
