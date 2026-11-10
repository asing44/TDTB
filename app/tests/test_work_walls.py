"""Work blocks are walls for every item that is not itself work (2026-11-09).

Live defect that day: Trinoor Morning 08:30-12:30 and Afternoon 13:30-17:00 are
the day's work blocks, but the planner placed a barefoot walk at 12:00-12:30
(inside Morning) and two items at 16:30/16:45 (inside Afternoon). The 🟡 zone
rows reach ``validate_sequence`` as ``backdrop`` rows, which it skipped
entirely, so a work block was no wall at all; the calendar reading of a work
block is only a soft ``calendar_overlap``.

Owner decision: a work block IS a wall for every item not zoned ``work_hours``.
Such items move to the first free gap at/after their own start (the walk to
12:30, i.e. the lunch gap), avoiding the walls, other anchors, and each other;
a row that fits no same-day gap is handed to the 5:00 PM overflow park.
Explicit placements (pins, native timed pins, exact overlap grants) and
work-zoned items keep their placement, and the re-seat is a fixed point.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from sequence import validate_sequence, work_walls  # noqa: E402

FRAME = {"anchor": "08:00", "effective_eod": "23:59"}

# Today's work blocks, exactly as the zone layer emits them.
ZONE_ROWS = [
    {"id": "🟡 Trinoor : Morning", "start": "08:30", "end": "12:30",
     "zone": "work_hours", "backdrop": True},
    {"id": "🟡 Trinoor : Afternoon", "start": "13:30", "end": "17:00",
     "zone": "work_hours", "backdrop": True},
]

# A real fixed commitment after the afternoon block: moved rows must clear it.
ANCHORS = [
    {"Block": "Sudsing", "Type": "hard", "Start": "17:00", "End": "17:30",
     "Duration": "30m", "overlap_allowed": False},
]

WALK = "Barefoot Walk"
LATE_ONE = "Email Triage"
LATE_TWO = "Inbox Sweep"
WORK_ITEM = "Work Standup"
GRANTED_ITEM = "Granted Item"
PINNED_ITEM = "Pinned Item"
NATIVE_ITEM = "Native Call"


def _item(item_id, blocks=1, **extra):
    return {"id": item_id, "name": item_id, "blocks": blocks, **extra}


def _row(row_id, start, end, zone="any", **extra):
    return {"id": row_id, "start": start, "end": end, "zone": zone, **extra}


def _minutes(hhmm):
    hours, minutes = str(hhmm).split(":")
    return int(hours) * 60 + int(minutes)


def _span(row):
    return (_minutes(row["start"]), _minutes(row["end"]))


def _spans_overlap(a, b):
    return a[0] < b[1] and b[0] < a[1]


def _by_id(proposal):
    return {
        row["id"]: row for row in proposal["sequence"]
        if isinstance(row, dict) and not row.get("backdrop")
    }


def _warnings_by_rule(result, rule):
    return [w for w in result.warnings
            if isinstance(w, dict) and w.get("rule") == rule]


def _day_shape():
    """Return (proposal, assigned) for the 2026-11-09 shape."""
    assigned = [
        _item(WALK),
        _item(LATE_ONE),
        _item(LATE_TWO),
        _item(WORK_ITEM, zone="work_hours"),
        _item(GRANTED_ITEM),
        _item(PINNED_ITEM),
        _item(NATIVE_ITEM, source="todoist", scheduled_start="11:00"),
    ]
    sequence = [
        dict(ZONE_ROWS[0]),
        dict(ZONE_ROWS[1]),
        # The model put the walk inside Morning, and two items inside Afternoon.
        _row(WALK, "12:00", "12:30"),
        _row(LATE_ONE, "16:30", "17:00"),
        _row(LATE_TWO, "16:45", "17:15"),
        # Work-zoned item: work belongs INSIDE the work block.
        _row(WORK_ITEM, "14:00", "14:30", zone="work_hours"),
        # An exact grant names this row, its interval, and the wall.
        _row(GRANTED_ITEM, "15:00", "15:30"),
        # An explicit pin is an explicit placement.
        _row(PINNED_ITEM, "14:30", "15:00", pinned=True),
        # A native Todoist time derives a server-side pin.
        _row(NATIVE_ITEM, "11:00", "11:30"),
    ]
    proposal = {
        "sequence": sequence,
        "pinned_rows": [
            {"id": PINNED_ITEM, "start": "14:30", "end": "15:00", "zone": None},
        ],
        "overlap_grants": [{
            "primary_id": GRANTED_ITEM,
            "companion_id": ZONE_ROWS[1]["id"],
            "primary_interval": {"start": "15:00", "end": "15:30"},
            "companion_interval": {"start": "13:30", "end": "17:00"},
            "reason": "standup with the work block",
            "planning_config_fingerprint": "fp",
        }],
    }
    return proposal, assigned


class TestWorkWallSpans:
    def test_zone_rows_and_calendar_work_blocks_are_walls(self):
        sequence = [dict(ZONE_ROWS[0]), dict(ZONE_ROWS[1])]
        anchors = [
            # capacity class work -> a wall
            {"Block": "Trinoor Standup", "Type": "hard", "Start": "09:00",
             "End": "10:00", "source": "calendar", "capacity_class": "work"},
            # permeable -> not a wall
            {"Block": "Live", "Type": "hard", "Start": "10:00", "End": "11:00",
             "source": "calendar", "capacity_class": "work",
             "overlap_allowed": True},
            # fixed calendar -> not a wall (tolerated, flagged)
            {"Block": "Foods Dinner", "Type": "hard", "Start": "18:00",
             "End": "19:00", "source": "calendar", "capacity_class": "fixed"},
        ]
        assert work_walls(sequence, anchors) == [
            (8 * 60 + 30, 12 * 60 + 30),
            (13 * 60 + 30, 17 * 60),
            (9 * 60, 10 * 60),
        ]

    def test_no_work_blocks_means_no_walls(self):
        assert work_walls([_row("A", "12:00", "12:30")], []) == []


class TestTodayShape:
    def test_walk_moves_into_the_lunch_gap(self):
        proposal, assigned = _day_shape()
        result = validate_sequence(
            proposal, assigned, list(ANCHORS), {}, time_frame=FRAME,
        )

        assert result.ok is True, result.hard_errors
        rows = _by_id(proposal)
        assert (rows[WALK]["start"], rows[WALK]["end"]) == ("12:30", "13:00")
        # the zone framing itself never moves: it is the wall
        for zone_row in ZONE_ROWS:
            row = next(
                r for r in proposal["sequence"]
                if isinstance(r, dict) and r["id"] == zone_row["id"]
            )
            assert (row["start"], row["end"]) == (zone_row["start"], zone_row["end"])

    def test_late_items_leave_the_afternoon_block(self):
        proposal, assigned = _day_shape()
        result = validate_sequence(
            proposal, assigned, list(ANCHORS), {}, time_frame=FRAME,
        )

        assert result.ok is True, result.hard_errors
        rows = _by_id(proposal)
        wall = (_minutes("13:30"), _minutes("17:00"))
        anchor = (_minutes(ANCHORS[0]["Start"]), _minutes(ANCHORS[0]["End"]))
        for row_id in (LATE_ONE, LATE_TWO):
            span = _span(rows[row_id])
            assert not _spans_overlap(span, wall), (row_id, rows[row_id])
            assert not _spans_overlap(span, anchor), (row_id, rows[row_id])
            assert span[0] >= _minutes("16:30"), (row_id, rows[row_id])
        assert not _spans_overlap(_span(rows[LATE_ONE]), _span(rows[LATE_TWO]))
        # deterministic: first free gap after the anchor, then the next one
        assert (rows[LATE_ONE]["start"], rows[LATE_ONE]["end"]) == ("17:30", "18:00")
        assert (rows[LATE_TWO]["start"], rows[LATE_TWO]["end"]) == ("18:00", "18:30")

    def test_every_moved_row_is_reported_once(self):
        proposal, assigned = _day_shape()
        result = validate_sequence(
            proposal, assigned, list(ANCHORS), {}, time_frame=FRAME,
        )
        assert result.ok is True, result.hard_errors
        reported = _warnings_by_rule(result, "work_wall_reseated")
        assert {w["id"] for w in reported} == {WALK, LATE_ONE, LATE_TWO}
        for warning in reported:
            assert "work block" in warning["detail"]

    def test_never_bump_holds(self):
        proposal, assigned = _day_shape()
        result = validate_sequence(
            proposal, assigned, list(ANCHORS), {}, time_frame=FRAME,
        )
        assert result.ok is True, result.hard_errors
        output_ids = list(_by_id(proposal))
        for item in assigned:
            assert output_ids.count(item["id"]) == 1, item["id"]

    def test_exempt_placements_keep_their_slot(self):
        proposal, assigned = _day_shape()
        result = validate_sequence(
            proposal, assigned, list(ANCHORS), {}, time_frame=FRAME,
        )
        assert result.ok is True, result.hard_errors
        rows = _by_id(proposal)
        assert (rows[WORK_ITEM]["start"], rows[WORK_ITEM]["end"]) == ("14:00", "14:30")
        assert (rows[GRANTED_ITEM]["start"], rows[GRANTED_ITEM]["end"]) == (
            "15:00", "15:30")
        assert (rows[PINNED_ITEM]["start"], rows[PINNED_ITEM]["end"]) == (
            "14:30", "15:00")
        assert (rows[NATIVE_ITEM]["start"], rows[NATIVE_ITEM]["end"]) == (
            "11:00", "11:30")
        assert {w["id"] for w in _warnings_by_rule(result, "work_wall_reseated")} \
            == {WALK, LATE_ONE, LATE_TWO}

    def test_second_pass_moves_nothing(self):
        proposal, assigned = _day_shape()
        first = validate_sequence(
            proposal, assigned, list(ANCHORS), {}, time_frame=FRAME,
        )
        assert first.ok is True, first.hard_errors
        times_before = {k: (v["start"], v["end"]) for k, v in _by_id(proposal).items()}
        second = validate_sequence(
            proposal, assigned, list(ANCHORS), {}, time_frame=FRAME,
        )
        assert second.ok is True, second.hard_errors
        assert {
            k: (v["start"], v["end"]) for k, v in _by_id(proposal).items()
        } == times_before
        assert {w["id"] for w in _warnings_by_rule(second, "work_wall_reseated")} \
            == {w["id"] for w in _warnings_by_rule(first, "work_wall_reseated")}

    def test_owner_edit_is_never_re_moved(self):
        proposal, assigned = _day_shape()
        first = validate_sequence(
            proposal, assigned, list(ANCHORS), {}, time_frame=FRAME,
        )
        assert first.ok is True, first.hard_errors
        # The owner drags the walk back inside Morning; the marker rides along.
        walk = _by_id(proposal)[WALK]
        walk["start"], walk["end"] = "12:00", "12:30"
        second = validate_sequence(
            proposal, assigned, list(ANCHORS), {}, time_frame=FRAME,
        )
        assert second.ok is True, second.hard_errors
        assert (_by_id(proposal)[WALK]["start"],
                _by_id(proposal)[WALK]["end"]) == ("12:00", "12:30")
        assert WALK not in {
            w["id"] for w in _warnings_by_rule(second, "work_wall_reseated")
        }
        # the rows the re-seat still owns are unchanged and still reported
        assert {w["id"] for w in _warnings_by_rule(second, "work_wall_reseated")} \
            == {LATE_ONE, LATE_TWO}


class TestCalendarWorkWall:
    def test_calendar_work_block_is_a_wall(self):
        anchors = [{
            "Block": "Trinoor Standup", "Type": "hard", "Start": "09:00",
            "End": "10:00", "source": "calendar", "capacity_class": "work",
            "overlap_allowed": False,
        }]
        proposal = {"sequence": [_row("Desk Reset", "09:15", "09:45")]}
        result = validate_sequence(
            proposal, [_item("Desk Reset")], anchors, {}, time_frame=FRAME,
        )
        assert result.ok is True, result.hard_errors
        row = _by_id(proposal)["Desk Reset"]
        assert (row["start"], row["end"]) == ("10:00", "10:30")

    def test_non_work_calendar_overlap_is_still_only_flagged(self):
        anchors = [{
            "Block": "Foods Dinner", "Type": "hard", "Start": "18:00",
            "End": "19:00", "source": "calendar", "capacity_class": "fixed",
            "overlap_allowed": False,
        }]
        proposal = {"sequence": [_row("Desk Reset", "18:00", "18:30")]}
        result = validate_sequence(
            proposal, [_item("Desk Reset")], anchors, {}, time_frame=FRAME,
        )
        assert result.ok is True, result.hard_errors
        row = _by_id(proposal)["Desk Reset"]
        assert (row["start"], row["end"]) == ("18:00", "18:30")
        overlaps = _warnings_by_rule(result, "calendar_overlap")
        assert [w["id"] for w in overlaps] == ["Desk Reset"]
        assert not _warnings_by_rule(result, "work_wall_reseated")


class TestNoGapParksAtFive:
    def test_row_with_no_same_day_gap_parks_at_five_pm(self):
        wall = {"id": "🟡 Trinoor : Afternoon", "start": "13:30",
                "end": "23:30", "zone": "work_hours", "backdrop": True}
        proposal = {"sequence": [dict(wall), _row("Late Task", "14:00", "14:30")]}
        result = validate_sequence(
            proposal, [_item("Late Task")], [], {}, time_frame=FRAME,
        )
        assert result.ok is True, result.hard_errors
        row = _by_id(proposal)["Late Task"]
        assert (row["start"], row["end"]) == ("17:00", "17:30")
        warnings = _warnings_by_rule(result, "work_wall_reseated")
        assert [w["id"] for w in warnings] == ["Late Task"]
        assert "5:00 PM" in warnings[0]["detail"]

    def test_park_handoff_is_idempotent(self):
        wall = {"id": "🟡 Trinoor : Afternoon", "start": "13:30",
                "end": "23:30", "zone": "work_hours", "backdrop": True}
        proposal = {"sequence": [dict(wall), _row("Late Task", "14:00", "14:30")]}
        assigned = [_item("Late Task")]
        first = validate_sequence(proposal, assigned, [], {}, time_frame=FRAME)
        assert first.ok is True, first.hard_errors
        times_before = {k: (v["start"], v["end"]) for k, v in _by_id(proposal).items()}
        second = validate_sequence(proposal, assigned, [], {}, time_frame=FRAME)
        assert second.ok is True, second.hard_errors
        assert {
            k: (v["start"], v["end"]) for k, v in _by_id(proposal).items()
        } == times_before
        assert {w["id"] for w in _warnings_by_rule(second, "work_wall_reseated")} \
            == {"Late Task"}

    def test_full_park_stack_keeps_the_row_and_flags_it(self):
        wall = {"id": "🟡 Trinoor : Afternoon", "start": "13:30",
                "end": "23:30", "zone": "work_hours", "backdrop": True}
        # Ten 30-minute rows cannot fit the 17:00 -> 23:59 park stack either.
        assigned = [_item(f"Row {n}") for n in range(14)]
        sequence = [dict(wall)] + [
            _row(f"Row {n}", "14:00", "14:30") for n in range(14)
        ]
        proposal = {"sequence": sequence}
        result = validate_sequence(proposal, assigned, [], {}, time_frame=FRAME)
        assert result.ok is True, result.hard_errors
        rows = _by_id(proposal)
        # 13 rows fill the 5:00 PM stack; the 14th has no room even there, so
        # it keeps its incoming placement and is still flagged (never dropped).
        parked = [_span(rows[f"Row {n}"]) for n in range(13)]
        assert parked == [
            (_minutes("17:00") + 30 * n, _minutes("17:00") + 30 * (n + 1))
            for n in range(13)
        ]
        assert _span(rows["Row 13"]) == (_minutes("14:00"), _minutes("14:30"))
        assert len(_warnings_by_rule(result, "work_wall_reseated")) == 14
