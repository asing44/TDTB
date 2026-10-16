"""Overflow parking must respect anchored spans (asing44/TDTB#15).

Live defect (2026-10-16): the overflow park packed straight from
``OVERFLOW_PARK_TIME`` (17:00) and was exempt from anchored-block overlap, so
an overcommitted day stacked parked rows on top of anchors. The commit record
that day read ``Backpack 17:00 with Quick Tasks 17:00``, ``System
Improvements 17:30 with Sudsing 17:30``, and ``WALL-E-OS Capacities Buildout
18:00 with Foods Dinner 18:00-19:00``.

Operator decision (settled 2026-10-16): anchors are NOT shareable. Parked
overflow rows fill FREE gaps and skip anchor spans; whatever still does not
fit goes past end of day with a visible soft warning. Never-bump holds — every
item stays in the returned sequence, none dropped.

The day's shape reproduced here: anchors Sudsing 17:30-18:00, Foods Dinner
18:00-19:00, Night Routine 22:00-22:45; four 30-minute midday calendar walls
filling Mint Afternoon 15:00-17:00; the Trinoor Afternoon 13:30-17:00 zone
framing; Quick Tasks fixed at 17:00-17:30; and the overflow tail parked by the
model at 23:59.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from sequence import validate_sequence  # noqa: E402

FRAME = {"anchor": "13:30", "effective_eod": "23:59"}

# Anchor spans the overflow park must never share a minute with.
ANCHORS = [
    {"Block": "Sudsing", "Type": "hard", "Start": "17:30", "End": "18:00",
     "Duration": "30m", "overlap_allowed": False},
    {"Block": "Foods Dinner", "Type": "hard", "Start": "18:00", "End": "19:00",
     "Duration": "1h", "overlap_allowed": False},
    {"Block": "Night Routine", "Type": "hard", "Start": "22:00", "End": "22:45",
     "Duration": "45m", "overlap_allowed": False},
    # Four 30-minute calendar hard walls filling Mint Afternoon 15:00-17:00.
    {"Block": "Mint Afternoon · 15:00", "Type": "hard", "Start": "15:00",
     "End": "15:30", "source": "calendar", "capacity_class": "fixed",
     "overlap_allowed": False},
    {"Block": "Mint Afternoon · 15:30", "Type": "hard", "Start": "15:30",
     "End": "16:00", "source": "calendar", "capacity_class": "fixed",
     "overlap_allowed": False},
    {"Block": "Mint Afternoon · 16:00", "Type": "hard", "Start": "16:00",
     "End": "16:30", "source": "calendar", "capacity_class": "fixed",
     "overlap_allowed": False},
    {"Block": "Mint Afternoon · 16:30", "Type": "hard", "Start": "16:30",
     "End": "17:00", "source": "calendar", "capacity_class": "fixed",
     "overlap_allowed": False},
]

# The overflow rows the model parked at the last representable minute.
PARKED_IDS = (
    "Backpack",
    "System Improvements",
    "WALL-E-OS Capacities Buildout",
    "Inbox Zero",
    "Weekly Review",
    "Groceries Order",
    "Reading",
    "Stretch",
)


def _item(item_id, blocks=1):
    return {"id": item_id, "name": item_id, "blocks": blocks}


def _row(row_id, start, end, zone="any", **extra):
    return {"id": row_id, "start": start, "end": end, "zone": zone, **extra}


def _minutes(hhmm):
    hours, minutes = str(hhmm).split(":")
    return int(hours) * 60 + int(minutes)


def _spans_overlap(a, b):
    return a[0] < b[1] and b[0] < a[1]


def _anchor_spans():
    return [
        (_minutes(b["Start"]), _minutes(b["End"])) for b in ANCHORS
    ]


def _overflow_shape(extra_parked=()):
    """Return (proposal, assigned) for the 2026-10-16 shape."""
    assigned = [_item("Quick Tasks"), _item("Backpack")]
    assigned += [_item(i) for i in PARKED_IDS[1:]]
    assigned += [_item(i) for i in extra_parked]
    sequence = [
        # Trinoor Afternoon zone framing (permeable backdrop, not a placement).
        _row("🟡 Trinoor : Afternoon", "13:30", "17:00",
             zone="work_hours", backdrop=True),
        _row("Quick Tasks", "17:00", "17:30"),
        _row("Backpack", "23:59", "23:59"),
        *[_row(i, "23:59", "23:59") for i in PARKED_IDS[1:]],
        *[_row(i, "23:59", "23:59") for i in extra_parked],
    ]
    return {"sequence": sequence}, assigned


def _by_id(proposal):
    return {
        row["id"]: row for row in proposal["sequence"]
        if isinstance(row, dict) and not row.get("backdrop")
    }


def _warnings_by_rule(result, rule):
    return [w for w in result.warnings
            if isinstance(w, dict) and w.get("rule") == rule]


class TestOverflowRespectsAnchors:
    """First acceptance item: reproduce the 2026-10-16 stacking."""

    def test_parked_overflow_never_shares_time_with_an_anchor(self):
        proposal, assigned = _overflow_shape()
        result = validate_sequence(
            proposal, assigned, list(ANCHORS), {}, time_frame=FRAME,
        )

        assert result.ok is True, result.hard_errors
        # never-bump: every assigned item present exactly once, none dropped
        output_ids = list(_by_id(proposal))
        for item in assigned:
            assert output_ids.count(item["id"]) == 1, item["id"]
        # every parked row fills a free gap: no shared minute, no shared start
        parked = _warnings_by_rule(result, "overflow_reseated")
        assert {w["id"] for w in parked} == set(PARKED_IDS)
        anchor_spans = _anchor_spans()
        quick_tasks = _by_id(proposal)["Quick Tasks"]
        fixed_span = (_minutes(quick_tasks["start"]), _minutes(quick_tasks["end"]))
        for warning in parked:
            row = _by_id(proposal)[warning["id"]]
            span = (_minutes(row["start"]), _minutes(row["end"]))
            for anchor in anchor_spans:
                assert not _spans_overlap(span, anchor), (
                    f"{row['id']} {row['start']}-{row['end']} shares time with "
                    f"anchor {anchor}"
                )
            assert row["start"] != quick_tasks["start"]
            assert not _spans_overlap(span, fixed_span)
        # the park is deterministic: packed in original order, full duration
        placed = {row_id: (_by_id(proposal)[row_id]["start"],
                           _by_id(proposal)[row_id]["end"])
                  for row_id in PARKED_IDS}
        assert placed == {
            "Backpack": ("19:00", "19:30"),
            "System Improvements": ("19:30", "20:00"),
            "WALL-E-OS Capacities Buildout": ("20:00", "20:30"),
            "Inbox Zero": ("20:30", "21:00"),
            "Weekly Review": ("21:00", "21:30"),
            "Groceries Order": ("21:30", "22:00"),
            "Reading": ("22:45", "23:15"),
            "Stretch": ("23:15", "23:45"),
        }


class TestOverflowSpill:
    """A row that fits no free gap goes past end of day, loudly."""

    EXTRA = ("Admin Sweep", "Notes Cleanup", "Plan Tomorrow", "Backlog Triage")

    def test_unfittable_rows_go_past_eod_with_a_visible_warning(self):
        proposal, assigned = _overflow_shape(extra_parked=self.EXTRA)
        result = validate_sequence(
            proposal, assigned, list(ANCHORS), {}, time_frame=FRAME,
        )

        assert result.ok is True, result.hard_errors
        output_ids = list(_by_id(proposal))
        for item in assigned:
            assert output_ids.count(item["id"]) == 1, item["id"]
        rows = _by_id(proposal)
        spilled = _warnings_by_rule(result, "overflow_past_eod")
        assert {w["id"] for w in spilled} == set(self.EXTRA)
        for warning in spilled:
            assert "past end of day" in warning["detail"]
            row = rows[warning["id"]]
            assert (row["start"], row["end"]) == ("23:59", "23:59")
        # the rows that did fit still never touch an anchor
        for warning in _warnings_by_rule(result, "overflow_reseated"):
            row = rows[warning["id"]]
            span = (_minutes(row["start"]), _minutes(row["end"]))
            for anchor in _anchor_spans():
                assert not _spans_overlap(span, anchor)

    def test_past_eod_rows_are_not_rewritten_on_revalidation(self):
        # A second pass must not move the spilled rows into a parked gap the
        # first pass already filled.
        proposal, assigned = _overflow_shape(extra_parked=self.EXTRA)
        first = validate_sequence(
            proposal, assigned, list(ANCHORS), {}, time_frame=FRAME,
        )
        assert first.ok is True
        placed_before = _by_id(proposal)
        times_before = {k: (v["start"], v["end"]) for k, v in placed_before.items()}
        second = validate_sequence(
            proposal, assigned, list(ANCHORS), {}, time_frame=FRAME,
        )
        assert second.ok is True, second.hard_errors
        assert not _warnings_by_rule(second, "overflow_reseated")
        assert {k: (v["start"], v["end"]) for k, v in _by_id(proposal).items()} == times_before


class TestOverflowParkKeepsDurationAndOrder:
    def test_multi_block_row_parks_at_full_duration_after_the_anchor(self):
        anchors = [{
            "Block": "Evening Block", "Type": "hard",
            "Start": "17:00", "End": "18:00", "Duration": "1h",
            "overlap_allowed": False,
        }]
        proposal = {"sequence": [_row("Deep", "23:59", "23:59")]}
        result = validate_sequence(
            proposal, [_item("Deep", blocks=2)], anchors, {}, time_frame=FRAME,
        )
        assert result.ok is True, result.hard_errors
        row = _by_id(proposal)["Deep"]
        assert (row["start"], row["end"]) == ("18:00", "19:00")

    def test_hard_anchor_overlap_is_no_longer_an_overflow_exemption(self):
        # Re-seated rows are no longer exempt from the anchored-block overlap
        # reading: a parked row that somehow shared an anchor span reports the
        # ordinary acceptable-defect rule, never a tailored overflow pass.
        anchors = [{
            "Block": "Evening Block", "Type": "hard",
            "Start": "17:00", "End": "17:30", "Duration": "30m",
            "overlap_allowed": False,
        }]
        proposal = {"sequence": [_row("A", "23:59", "23:59")]}
        result = validate_sequence(
            proposal, [_item("A")], anchors, {}, time_frame=FRAME,
        )
        assert result.ok is True
        row = _by_id(proposal)["A"]
        assert (row["start"], row["end"]) == ("17:30", "18:00")
        assert not [
            w for w in result.warnings
            if w.get("rule") == "overflow_overlap"
            and "Evening Block" in str(w.get("detail"))
        ]
