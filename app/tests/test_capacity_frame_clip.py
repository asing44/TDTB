"""Day-capacity frame clipping contract.

The 'Day capacity' segments describe the time the operator is actually
planning. Fixed calendar capacity is the frame-covered union of fixed events
(two meetings sharing clock time consume that time once) and anchored blocks
count only the time they still hold inside the frame — an elapsed anchor
(Morning Routine seen from an afternoon anchor) or one at/after the hard stop
costs zero. Anchors with no parseable times keep their configured cost.
"""
from __future__ import annotations

from datetime import datetime

import main as main_mod

TODAY = datetime(2026, 10, 29).date()  # Thursday, the owner's reported day


def _event(
    name: str,
    start: str | None,
    end: str | None,
    capacity_class: str = "fixed",
    **extra: object,
) -> dict:
    block = {
        "Block": name,
        "source": "calendar",
        "calendar_id": f"CAL-{name}",
        "calendar_title": name,
        "capacity_class": capacity_class,
    }
    if start is not None:
        block["Start"] = start
    if end is not None:
        block["End"] = end
    block.update(extra)
    return block


def _anchor(
    name: str,
    *,
    start: str | None = None,
    end: str | None = None,
    duration: str | None = None,
    kind: str = "hard",
) -> dict:
    spec: dict = {"Block": name, "Type": kind}
    if start is not None:
        spec["Start"] = start
    if end is not None:
        spec["End"] = end
    if duration is not None:
        spec["Duration"] = duration
    return spec


def _frame(
    busy: list[dict],
    anchored: list[dict] | tuple = (),
    *,
    anchor: str = "14:30",
    eod: str = "23:45",
    now: datetime | None = None,
    work_minutes: int = 0,
):
    return main_mod._capacity_frame(
        {
            "Defaults": {
                "eod": eod,
                "anchor.round_to_minutes": 15,
                "buffering.off_pct": 0,
            },
            "Anchored Lifestyle Blocks": list(anchored),
        },
        {"anchor": anchor, "eod": eod, "buffering": "off"},
        busy,
        {"est_minutes": 0, "done": 0, "outstanding": 0},
        {"effective_allotment_minutes": work_minutes},
        now=now or datetime(2026, 10, 29, 14, 30),
    )


# ---------------------------------------------------------------------------
# Fixed calendar segment — union, clipped to the frame
# ---------------------------------------------------------------------------

def test_overlapping_fixed_events_count_their_union_once():
    busy = [
        _event("Team sync", "17:00", "18:00"),
        _event("Design review", "17:30", "18:30"),
    ]
    _time, cap = _frame(busy)
    assert cap.fixed == 3  # 17:00–18:30 union, not 2 + 2


def test_fixed_event_wholly_before_the_anchor_costs_nothing():
    _time, cap = _frame([_event("Breakfast", "09:00", "10:00")])
    assert cap.fixed == 0


def test_fixed_event_straddling_the_anchor_is_clipped():
    _time, cap = _frame([_event("Workshop", "14:00", "15:00")])
    assert cap.fixed == 1  # only 14:30–15:00 is still plannable


def test_all_day_fixed_event_costs_nothing():
    busy = [_event("Anniversary", None, None, all_day=True)]
    _time, cap = _frame(busy)
    assert cap.fixed == 0


def test_dismissed_fixed_event_still_costs_nothing():
    busy = [_event("Dismissed", "17:00", "18:00", skip_today=True)]
    _time, cap = _frame(busy)
    assert cap.fixed == 0


# ---------------------------------------------------------------------------
# Anchored segment — only time still inside the frame
# ---------------------------------------------------------------------------

def test_elapsed_anchored_spec_costs_nothing():
    anchored = [_anchor("Morning Routine", start="7:30 AM", end="9:30 AM",
                        duration="30m")]
    _time, cap = _frame([], anchored)
    assert cap.anchored == 0


def test_anchored_spec_after_the_hard_stop_costs_nothing():
    anchored = [_anchor("Wind Down", start="11:00 PM", end="11:50 PM",
                        duration="30m")]
    _time, cap = _frame([], anchored, eod="22:00")
    assert cap.anchored == 0


def test_in_frame_anchored_spec_costs_its_duration():
    anchored = [_anchor("Stretch", start="4:00 PM", duration="1h")]
    _time, cap = _frame([], anchored)
    assert cap.anchored == 2


def test_anchored_spec_without_times_keeps_its_configured_cost():
    anchored = [_anchor("Sudsing", duration="30m")]
    _time, cap = _frame([], anchored)
    assert cap.anchored == 1


def test_flexible_window_is_capped_to_the_remaining_window_overlap():
    # Live's window is 12:00–20:00 and it can use 2h, but at a 19:00 anchor
    # only one hour of its window is still ahead — one fewer hour is gone.
    anchored = [_anchor("Live", start="12:00 PM", end="8:00 PM",
                        duration="2h", kind="window")]
    _time, cap = _frame([], anchored, anchor="19:00", eod="23:00",
                        now=datetime(2026, 10, 29, 19, 0))
    assert cap.anchored == 2


def test_midnight_wrapping_window_counts_only_the_frame_side():
    # 23:00–00:30 crosses midnight: only 23:00–23:45 is inside the frame.
    anchored = [_anchor("Night cap", start="11:00 PM", end="12:30 AM",
                        duration="90m", kind="window")]
    _time, cap = _frame([], anchored)
    assert cap.anchored == 2  # 45 in-frame minutes, not the full 90


# ---------------------------------------------------------------------------
# End-to-end — the owner's reported afternoon frame
# ---------------------------------------------------------------------------

def test_owner_scenario_counts_only_in_frame_time():
    busy = [
        _event("Team", "17:00", "17:30"),
        _event("Adam Cook", "17:30", "18:00"),
        _event("Meegy", "18:30", "22:00"),
        _event("Blue Jackets", "19:00", "22:00"),
    ]
    anchored = [
        _anchor("Morning Routine", start="7:30 AM", end="9:30 AM",
                duration="30m"),
        _anchor("Live", start="12:00 PM", end="8:00 PM", duration="30m",
                kind="window"),
        _anchor("Sudsing", duration="30m"),
        _anchor("Night Routine", start="10:30 PM", end="11:45 PM",
                duration="1h"),
    ]
    frame, cap = _frame(
        busy, anchored, now=datetime(2026, 10, 29, 14, 30),
    )
    assert frame.anchor == "14:30"
    assert frame.effective_eod == "23:45"
    assert frame.total_blocks == 18
    # 17:00–18:00 plus 18:30–22:00 (Blue Jackets fully inside Meegy) = 270m.
    assert cap.fixed == 9
    # Morning Routine is elapsed (0); Live uses 1 of its 30m;
    # Sudsing has no times (1); Night Routine holds 22:30–23:45 (2).
    assert cap.anchored == 4
