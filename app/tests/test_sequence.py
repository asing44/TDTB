"""Tests for sequence.py — T12 server-side sequence-proposal validation.

Belt to judgment.py's suspenders: re-validates an already-schema-valid
SequenceProposal against zone compatibility, latest_start, the hard
morning-workout ban (+ Press before_work exception), and structural
invariants (overlap, chronological order, never-bump completeness).
"""
from __future__ import annotations

import pytest

from sequence import (ValidationResult, is_workout_item, merge_immutable_rows,
                      merge_pinned_rows, recurring_auto_pins,
                      canonicalize_sequence_ids, placement_window_rows,
                      validate_pinned_rows, validate_sequence)


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

CONFIG = {
    "presets": [
        {"Name": "Summits", "Type": "interval", "Blocks": 1, "Priority": 4, "Zone": "any"},
        {
            "Name": "Press",
            "Type": "workout",
            "Blocks": 1,
            "Priority": 3,
            "Zone": "before_work",
        },
    ],
    "sections": {
        "Template Blocks": {
            "Trinoor Hours": [
                {"Slot": "Morning", "Start": "8:30 AM", "End": "12:30 PM"},
                {"Slot": "Afternoon", "Start": "1:30 PM", "End": "5:00 PM"},
            ],
        },
    },
}


def _anchored(overlap_allowed=False, block="Morning Routine", start="7:45 AM", end=None, block_type="hard", duration="80m"):
    return {
        "Block": block,
        "Type": block_type,
        "Start": start,
        "End": end or "—",
        "Duration": duration,
        "overlap_allowed": overlap_allowed,
    }


def _item(id_="task-1", zone="any", latest_start=None):
    row = {"id": id_, "zone": zone}
    if latest_start:
        row["latest_start"] = latest_start
    return row


def _seq_row(id_, start, end, zone="any"):
    return {"id": id_, "start": start, "end": end, "zone": zone}


# ---------------------------------------------------------------------------
# is_workout_item
# ---------------------------------------------------------------------------

class TestIsWorkoutItem:
    def test_id_contains_workout(self):
        assert is_workout_item({"id": "morning-workout", "zone": "any"}) is True

    def test_zone_workout(self):
        assert is_workout_item({"id": "gym-session", "zone": "workout"}) is True

    def test_type_field_workout(self):
        assert is_workout_item({"id": "x", "type": "exercise"}) is True

    def test_non_workout(self):
        assert is_workout_item({"id": "read-book", "zone": "evening"}) is False


# ---------------------------------------------------------------------------
# Hard rejection — morning workout
# ---------------------------------------------------------------------------

class TestMorningWorkoutHardRejection:
    def test_workout_before_noon_rejected(self):
        proposal = {"sequence": [_seq_row("morning-workout", "07:00", "07:45", zone="any")]}
        result = validate_sequence(
            proposal, [_item("morning-workout")], [], CONFIG
        )
        assert result.ok is False
        assert any("workout" in e.lower() for e in result.hard_errors)

    def test_selected_session_rows_are_exact_immutable_windows(self):
        items = [{
            "id": "Mint Morning · 08:30",
            "name": "Mint Morning · 08:30",
            "zone": "work_hours",
            "placement_window": {"start": "08:30", "end": "09:00"},
            "source": "schedulable",
            "mint_session": True,
        }]
        assert placement_window_rows(items) == [{
            "id": "Mint Morning · 08:30",
            "start": "08:30",
            "end": "09:00",
            "zone": "work_hours",
            "source": "schedulable",
            "mint_session": True,
        }]

    def test_model_dropped_leading_icon_is_reconciled_to_canonical_id(self):
        proposal = {"sequence": [{
            "id": "Water the Creeping Pilea",
            "start": "13:00",
            "end": "13:30",
            "zone": "any",
        }]}
        normalized = canonicalize_sequence_ids(proposal, [{
            "id": "💧 Water the Creeping Pilea",
            "name": "💧 Water the Creeping Pilea",
        }])
        assert normalized["sequence"][0]["id"] == "💧 Water the Creeping Pilea"

    def test_workout_at_noon_or_later_allowed(self):
        proposal = {"sequence": [_seq_row("evening-workout", "12:00", "12:45")]}
        result = validate_sequence(
            proposal, [_item("evening-workout")], [], CONFIG
        )
        assert result.ok is True
        assert result.hard_errors == []

    def test_press_before_work_exception_passes(self):
        proposal = {"sequence": [_seq_row("Press", "06:00", "06:45", zone="before_work")]}
        result = validate_sequence(proposal, [_item("Press", zone="before_work")], [], CONFIG)
        assert result.ok is True
        assert result.hard_errors == []

    def test_other_workout_not_named_press_still_rejected_before_noon(self):
        proposal = {"sequence": [_seq_row("Press Clone", "06:00", "06:45", zone="before_work")]}
        item = {"id": "Press Clone", "zone": "before_work", "type": "workout"}
        result = validate_sequence(proposal, [item], [], CONFIG)
        assert result.ok is False

    def test_press_after_noon_also_fine(self):
        proposal = {"sequence": [_seq_row("Press", "13:00", "13:45", zone="before_work")]}
        result = validate_sequence(proposal, [_item("Press", zone="before_work")], [], CONFIG)
        assert result.ok is True

    def test_selected_mint_session_must_stay_inside_window(self):
        item = {
            "id": "Mint Morning",
            "zone": "work_hours",
            "placement_window": {"start": "08:30", "end": "12:30"},
        }
        result = validate_sequence(
            {"sequence": [_seq_row("Mint Morning", "13:00", "13:30")]},
            [], [], CONFIG, optional_ids={"Mint Morning"}, optional_items=[item],
        )
        assert result.ok is False
        assert "selected Mint session window" in result.hard_errors[0]


# ---------------------------------------------------------------------------
# Zone violations — soft (warnings)
# ---------------------------------------------------------------------------

class TestZoneWarnings:
    def test_before_work_item_placed_in_afternoon_warns(self):
        proposal = {"sequence": [_seq_row("t1", "14:00", "14:30", zone="before_work")]}
        result = validate_sequence(
            proposal, [_item("t1", zone="before_work")], [], CONFIG
        )
        assert result.ok is True
        assert any(w["kind"] == "zone_violation" and w["id"] == "t1" for w in result.warnings)

    def test_compatible_zone_no_warning(self):
        proposal = {"sequence": [_seq_row("t1", "06:00", "06:30", zone="before_work")]}
        result = validate_sequence(
            proposal, [_item("t1", zone="before_work")], [], CONFIG
        )
        assert result.ok is True
        assert result.warnings == []

    def test_any_zone_never_warns(self):
        proposal = {"sequence": [_seq_row("t1", "20:00", "20:30", zone="any")]}
        result = validate_sequence(proposal, [_item("t1", zone="any")], [], CONFIG)
        assert result.warnings == []

    def test_zone_violation_is_soft_not_rejected(self):
        proposal = {"sequence": [_seq_row("t1", "14:00", "14:30", zone="before_work")]}
        result = validate_sequence(
            proposal, [_item("t1", zone="before_work")], [], CONFIG
        )
        assert result.ok is True
        assert result.hard_errors == []


# ---------------------------------------------------------------------------
# latest_start violations — soft (warnings)
# ---------------------------------------------------------------------------

class TestLatestStartWarnings:
    def test_start_after_latest_start_warns(self):
        proposal = {"sequence": [_seq_row("t1", "10:00", "10:30")]}
        result = validate_sequence(
            proposal, [_item("t1", latest_start="09:00")], [], CONFIG
        )
        assert result.ok is True
        assert any(
            w["kind"] == "latest_start_violation" and w["id"] == "t1" for w in result.warnings
        )

    def test_start_at_or_before_latest_start_no_warning(self):
        proposal = {"sequence": [_seq_row("t1", "08:30", "09:00")]}
        result = validate_sequence(
            proposal, [_item("t1", latest_start="09:00")], [], CONFIG
        )
        assert result.warnings == []

    def test_never_bump_still_placed_despite_violation(self):
        proposal = {"sequence": [_seq_row("t1", "10:00", "10:30")]}
        result = validate_sequence(
            proposal, [_item("t1", latest_start="09:00")], [], CONFIG
        )
        assert result.ok is True
        assert len(proposal["sequence"]) == 1


# ---------------------------------------------------------------------------
# Structural — overlaps, order, HH:MM, never-bump completeness
# ---------------------------------------------------------------------------

class TestStructural:
    def test_permanent_semantic_rules_are_hard_validation_errors(self):
        assigned = [
            {"name": "Parent work", "blocks": 2},
            {"name": "Child work", "blocks": 1, "relates_to": "[[Parent work]]"},
            {"name": "Systems one", "tags": ["systems"]},
            {"name": "Systems two", "tags": ["systems"]},
        ]
        proposal = {
            "sequence": [
                _seq_row("Parent work", "14:00", "15:00"),
                _seq_row("Child work", "15:00", "15:30"),
                _seq_row("Systems one", "16:00", "16:30"),
                _seq_row("Systems two", "16:30", "17:00"),
            ],
            "overlap_grants": [],
        }
        result = validate_sequence(
            proposal, assigned, [], CONFIG, planning_config_fingerprint="fp-current"
        )
        assert result.ok is False
        assert any("must stay within" in error for error in result.hard_errors)
        assert any("same start time" in error for error in result.hard_errors)

    def test_overlap_with_non_permeable_anchored_block_is_acceptable_defect(self):
        anchored = [_anchored(overlap_allowed=False, block="Morning Routine", start="07:45")]
        proposal = {"sequence": [_seq_row("t1", "07:50", "08:10")]}
        result = validate_sequence(proposal, [_item("t1")], anchored, CONFIG)
        assert result.ok is True
        assert any(w.get("rule") == "unexpected_overlap" for w in result.warnings)

    def test_exact_current_grant_is_informational_allowed_overlap(self):
        anchored = [_anchored(overlap_allowed=False, block="Morning Routine", start="07:45")]
        proposal = {"sequence": [_seq_row("t1", "07:50", "08:10")],
                    "overlap_grants": [{
                        "primary_id": "t1", "companion_id": "Morning Routine",
                        "primary_interval": {"start": "07:50", "end": "08:10"},
                        "companion_interval": {"start": "07:45", "end": "09:05"},
                        "reason": "intentional companion work",
                        "planning_config_fingerprint": "fp-current",
                    }]}
        result = validate_sequence(
            proposal, [_item("t1")], anchored, CONFIG,
            planning_config_fingerprint="fp-current",
        )
        assert result.ok is True
        assert any(w.get("rule") == "allowed_overlap" for w in result.warnings)

    def test_stale_grant_does_not_suppress_unexpected_overlap(self):
        anchored = [_anchored(overlap_allowed=False, block="Morning Routine", start="07:45")]
        proposal = {"sequence": [_seq_row("t1", "07:50", "08:10")],
                    "overlap_grants": [{
                        "primary_id": "t1", "companion_id": "Morning Routine",
                        "primary_interval": {"start": "07:50", "end": "08:10"},
                        "companion_interval": {"start": "07:45", "end": "09:05"},
                        "reason": "intentional", "planning_config_fingerprint": "old",
                    }]}
        result = validate_sequence(
            proposal, [_item("t1")], anchored, CONFIG,
            planning_config_fingerprint="new",
        )
        assert any(w.get("rule") == "unexpected_overlap" for w in result.warnings)

    def test_overlap_with_permeable_anchored_block_allowed(self):
        anchored = [
            {
                "Block": "Live",
                "Type": "window",
                "Start": "12:00 PM",
                "End": "8:00 PM",
                "overlap_allowed": True,
            }
        ]
        proposal = {"sequence": [_seq_row("t1", "13:00", "13:30")]}
        result = validate_sequence(proposal, [_item("t1")], anchored, CONFIG)
        assert result.ok is True

    def test_overlap_with_nonpermeable_window_block_is_soft(self):
        # ISS-6: a Type:window non-permeable block (Foods Breakfast — a 45m
        # floater in an 08:30-13:00 window) is a placement WINDOW, not a wall.
        # A task inside the window is a soft flag, not a hard error, and the
        # window stays schedulable. (SOT: "don't block windows; flag overlaps".)
        anchored = [_anchored(overlap_allowed=False, block="Foods Breakfast",
                              start="8:30 AM", end="1:00 PM",
                              block_type="window", duration="45m")]
        proposal = {"sequence": [_seq_row("t1", "09:50", "10:50")]}
        result = validate_sequence(proposal, [_item("t1")], anchored, CONFIG)
        assert result.ok is True
        assert not any("overlap" in e.lower() for e in result.hard_errors)
        assert any(
            "Foods Breakfast" in (str(w.get("detail", "")) + str(w.get("id", "")))
            for w in result.warnings
        )

    def test_overflow_tail_overlap_is_soft(self):
        # G16 (2026-07-14): over-capacity must degrade, never 422. A row
        # starting at/after effective_eod may overlap a non-permeable block —
        # soft "overflow" flag, user resolves on the timeline.
        anchored = [_anchored(overlap_allowed=False, block="Night Routine",
                              start="11:00 PM", end="11:45 PM")]
        proposal = {"sequence": [_seq_row("t1", "23:15", "23:30")]}
        result = validate_sequence(
            proposal, [_item("t1")], anchored, CONFIG,
            time_frame={"anchor": "16:00", "effective_eod": "23:00"})
        assert result.ok is True
        assert any(w.get("rule") == "overflow_overlap" for w in result.warnings)

    def test_pre_eod_overlap_is_acceptable_defect_with_time_frame(self):
        anchored = [_anchored(overlap_allowed=False, block="Gym together",
                              start="7:00 PM", end="9:00 PM")]
        proposal = {"sequence": [_seq_row("t1", "19:00", "20:15")]}
        result = validate_sequence(
            proposal, [_item("t1")], anchored, CONFIG,
            time_frame={"anchor": "16:00", "effective_eod": "23:00"})
        assert result.ok is True
        assert any(w.get("rule") == "unexpected_overlap" for w in result.warnings)

    def test_nonpermeable_hard_block_is_reported_not_silently_allowed(self):
        anchored = [_anchored(overlap_allowed=False, block="Morning Routine",
                              start="07:45", block_type="hard", duration="80m")]
        proposal = {"sequence": [_seq_row("t1", "07:50", "08:10")]}
        result = validate_sequence(proposal, [_item("t1")], anchored, CONFIG)
        assert result.ok is True
        assert any(w.get("rule") == "unexpected_overlap" for w in result.warnings)

    def test_chronological_order_required(self):
        proposal = {
            "sequence": [
                _seq_row("t1", "10:00", "10:30"),
                _seq_row("t2", "09:00", "09:30"),
            ]
        }
        result = validate_sequence(
            proposal, [_item("t1"), _item("t2")], [], CONFIG
        )
        assert result.ok is False
        assert any("chronological" in e.lower() for e in result.hard_errors)

    def test_invalid_hhmm_rejected(self):
        proposal = {"sequence": [_seq_row("t1", "9:00", "09:30")]}
        result = validate_sequence(proposal, [_item("t1")], [], CONFIG)
        assert result.ok is False

    def test_end_before_start_rejected(self):
        proposal = {"sequence": [_seq_row("t1", "10:00", "09:30")]}
        result = validate_sequence(proposal, [_item("t1")], [], CONFIG)
        assert result.ok is False

    def test_missing_assigned_item_rejected_never_bump(self):
        proposal = {"sequence": [_seq_row("t1", "10:00", "10:30")]}
        result = validate_sequence(
            proposal, [_item("t1"), _item("t2")], [], CONFIG
        )
        assert result.ok is False
        assert any("t2" in e for e in result.hard_errors)

    def test_extra_item_not_in_assigned_rejected(self):
        proposal = {
            "sequence": [
                _seq_row("t1", "10:00", "10:30"),
                _seq_row("ghost", "11:00", "11:30"),
            ]
        }
        result = validate_sequence(proposal, [_item("t1")], [], CONFIG)
        assert result.ok is False

    def test_duplicate_item_rejected(self):
        proposal = {
            "sequence": [
                _seq_row("t1", "09:00", "09:30"),
                _seq_row("t1", "10:00", "10:30"),
            ]
        }
        result = validate_sequence(proposal, [_item("t1")], [], CONFIG)
        assert result.ok is False

    def test_all_assigned_present_exactly_once_passes(self):
        proposal = {
            "sequence": [
                _seq_row("t1", "09:00", "09:30"),
                _seq_row("t2", "10:00", "10:30"),
            ]
        }
        result = validate_sequence(
            proposal, [_item("t1"), _item("t2")], [], CONFIG
        )
        assert result.ok is True
        assert result.hard_errors == []


# ---------------------------------------------------------------------------
# FEEDBACK-01 — chronological final-sequence ordering (2026-08-12)
# ---------------------------------------------------------------------------

class TestFeedback01Chronology:
    """The final sequence must be chronological after overflow and
    immutable-row merges, and validation must reject any descending final
    sequence with a deterministic error. The reported live rejection named
    'Log hours' ('17:15' < preceding '23:15')."""

    def test_reported_17_15_after_23_15_rejected_deterministically(self):
        proposal = {
            "sequence": [
                _seq_row("Pick up prescription", "23:15", "23:45"),
                _seq_row("Log hours", "17:15", "17:45"),
            ]
        }
        result = validate_sequence(
            proposal,
            [_item("Pick up prescription"), _item("Log hours")],
            [], CONFIG,
        )
        assert result.ok is False
        assert any(
            "chronological" in e.lower()
            and "Log hours" in e
            and "17:15" in e
            and "23:15" in e
            for e in result.hard_errors
        )

    def test_merge_immutable_rows_is_chronological_after_replace(self):
        # A model copy at 23:15 replaced by an immutable 17:15 pin must still
        # yield a chronological output after the merge — the "after
        # immutable-row merges" ordering contract.
        model_rows = [
            {"id": "Deep Work", "start": "23:15", "end": "23:45", "zone": None},
            {"id": "Log hours", "start": "23:15", "end": "23:45", "zone": None},
        ]
        immutable = [{
            "id": "Log hours", "start": "17:15", "end": "17:45", "zone": "any",
        }]
        merged = merge_immutable_rows(model_rows, immutable)
        assert [row["id"] for row in merged] == ["Log hours", "Deep Work"]
        assert [row["start"] for row in merged] == ["17:15", "23:15"]
        assert merged[0] is immutable[0]


# ---------------------------------------------------------------------------
# ValidationResult shape
# ---------------------------------------------------------------------------

class TestPinnedRows:
    def test_rejects_duplicate_foreign_malformed_and_overlapping_pins(self):
        assigned = [_item("A"), _item("B")]
        errors = validate_pinned_rows([
            {"id": "A", "start": "09:00", "end": "10:00", "zone": "any"},
            {"id": "A", "start": "09:30", "end": "10:30", "zone": "any"},
            {"id": "foreign", "start": "11:00", "end": "11:30", "zone": "any"},
            {"id": "B", "start": "bad", "end": "12:00", "zone": "any"},
        ], assigned)
        assert any("duplicate" in error for error in errors)
        assert any("foreign" in error for error in errors)
        assert any("malformed" in error for error in errors)
        assert any("overlap" in error for error in errors)

    def test_merge_preserves_exact_pin_and_replaces_model_copy(self):
        pin = {"id": "A", "start": "09:00", "end": "09:30",
               "zone": "any", "metadata": {"source": "manual"}}
        merged = merge_pinned_rows([
            {"id": "A", "start": "14:00", "end": "14:30", "zone": "any"},
            {"id": "B", "start": "10:00", "end": "10:30", "zone": "any"},
        ], [pin])
        assert merged[0] is pin
        assert [row["id"] for row in merged] == ["A", "B"]

class TestValidationResultShape:
    def test_result_is_dict_like_with_required_keys(self):
        proposal = {"sequence": [_seq_row("t1", "09:00", "09:30")]}
        result = validate_sequence(proposal, [_item("t1")], [], CONFIG)
        assert isinstance(result, ValidationResult)
        assert hasattr(result, "ok")
        assert hasattr(result, "hard_errors")
        assert hasattr(result, "warnings")

    def test_as_dict_round_trip(self):
        proposal = {"sequence": [_seq_row("t1", "09:00", "09:30")]}
        result = validate_sequence(proposal, [_item("t1")], [], CONFIG)
        d = result.as_dict()
        assert set(d.keys()) == {"ok", "hard_errors", "warnings"}


# ---------------------------------------------------------------------------
# Calendar busy blocks (gather-parity T5) — external_sources shape is a hard
# wall: no Type key, no overlap_allowed → non-permeable in validation.
# FEEDBACK-02 (2026-08-13): a non-permeable calendar block is a HARD wall —
# overlap rejects the sequence deterministically instead of a soft defect.
# ---------------------------------------------------------------------------

class TestCalendarBusyBlocks:
    BUSY = {"Block": "Dentist", "Start": "09:00", "End": "10:00", "source": "calendar"}

    def test_overlap_with_calendar_block_is_hard_rejection(self):
        proposal = {"sequence": [_seq_row("t1", "09:30", "10:15")]}
        result = validate_sequence(proposal, [_item("t1")], [self.BUSY], CONFIG)
        assert result.ok is False
        assert any("Dentist" in e and "overlap" in e.lower()
                   for e in result.hard_errors)

    def test_no_overlap_passes(self):
        proposal = {"sequence": [_seq_row("t1", "10:30", "11:00")]}
        result = validate_sequence(proposal, [_item("t1")], [self.BUSY], CONFIG)
        assert result.ok is True
        assert result.hard_errors == []


# ---------------------------------------------------------------------------
# FEEDBACK-02 — non-permeable calendar events are hard walls
# ---------------------------------------------------------------------------

class TestCalendarHardWalls:
    """FF-CAL-03 contract (2026-08-13): imported calendar events classified
    fixed or work block task placement in their occupied intervals. A sequence
    overlapping such a wall is rejected deterministically; it is never a soft
    acceptable defect. Permeable windows, exact overlap grants, and
    ignored/quarantined (contract 17 excluded) rows keep their old behavior.
    Config-backed anchored blocks stay on the LD26 acceptable-defect path."""

    def _calendar(self, block="Cooking", start="20:30", end="21:00",
                  capacity_class="fixed"):
        return {
            "Block": block,
            "Start": start,
            "End": end,
            "source": "calendar",
            "capacity_class": capacity_class,
        }

    def test_fixed_calendar_block_overlap_is_hard(self):
        anchored = [self._calendar()]
        proposal = {"sequence": [_seq_row("t1", "20:35", "21:05")]}
        result = validate_sequence(proposal, [_item("t1")], anchored, CONFIG)
        assert result.ok is False
        assert any("Cooking" in e and "overlap" in e.lower()
                   for e in result.hard_errors)

    def test_work_class_calendar_block_overlap_is_hard(self):
        # A work-class meeting is still busy time — the planner must not place
        # assigned work inside it.
        anchored = [self._calendar(block="Trinoor sync", start="09:00",
                                   end="10:00", capacity_class="work")]
        proposal = {"sequence": [_seq_row("t1", "09:30", "10:15")]}
        result = validate_sequence(proposal, [_item("t1")], anchored, CONFIG)
        assert result.ok is False
        assert any("Trinoor sync" in e for e in result.hard_errors)

    def test_calendar_block_without_class_defaults_to_hard_wall(self):
        # Unidentified calendars keep the historical fixed default (contract
        # 17 default when no inventory) — still a hard wall.
        anchored = [{"Block": "Dentist", "Start": "09:00", "End": "10:00",
                     "source": "calendar"}]
        proposal = {"sequence": [_seq_row("t1", "09:30", "10:15")]}
        result = validate_sequence(proposal, [_item("t1")], anchored, CONFIG)
        assert result.ok is False

    def test_ignored_calendar_block_is_no_wall(self):
        anchored = [self._calendar(capacity_class="ignored")]
        proposal = {"sequence": [_seq_row("t1", "20:35", "21:05")]}
        result = validate_sequence(proposal, [_item("t1")], anchored, CONFIG)
        assert result.ok is True
        assert result.hard_errors == []
        assert not any(w.get("rule") == "unexpected_overlap"
                       for w in result.warnings)

    def test_quarantined_calendar_block_is_no_wall(self):
        # Frozen contract 17: a known-but-unreviewed calendar is excluded from
        # planning — it must not silently become a hard wall either.
        anchored = [self._calendar(capacity_class="quarantined")]
        proposal = {"sequence": [_seq_row("t1", "20:35", "21:05")]}
        result = validate_sequence(proposal, [_item("t1")], anchored, CONFIG)
        assert result.ok is True
        assert result.hard_errors == []

    def test_exact_grant_keeps_calendar_overlap_allowed(self):
        anchored = [self._calendar()]
        proposal = {"sequence": [_seq_row("t1", "20:35", "21:05")],
                    "overlap_grants": [{
                        "primary_id": "t1", "companion_id": "Cooking",
                        "primary_interval": {"start": "20:35", "end": "21:05"},
                        "companion_interval": {"start": "20:30", "end": "21:00"},
                        "reason": "cook alongside",
                        "planning_config_fingerprint": "fp-current",
                    }]}
        result = validate_sequence(
            proposal, [_item("t1")], anchored, CONFIG,
            planning_config_fingerprint="fp-current",
        )
        assert result.ok is True
        assert any(w.get("rule") == "allowed_overlap" for w in result.warnings)

    def test_calendar_wall_overlap_in_overflow_tail_is_hard(self):
        # G16 overflow softness applies to config blocks and movable work, NOT
        # to non-permeable calendar walls: a wall overlap is explicit
        # infeasibility wherever the row sits.
        anchored = [self._calendar(block="Night event", start="23:15",
                                   end="23:45")]
        proposal = {"sequence": [_seq_row("t1", "23:20", "23:40")]}
        result = validate_sequence(
            proposal, [_item("t1")], anchored, CONFIG,
            time_frame={"anchor": "16:00", "effective_eod": "23:00"},
        )
        assert result.ok is False
        assert any("Night event" in e for e in result.hard_errors)

    def test_config_backed_block_overlap_stays_acceptable_defect(self):
        # LD26 carve-out: non-calendar (config-backed) anchored blocks keep
        # the acceptable-defect reading — only imported calendar walls harden.
        anchored = [_anchored(overlap_allowed=False, block="Morning Routine",
                              start="07:45")]
        proposal = {"sequence": [_seq_row("t1", "07:50", "08:10")]}
        result = validate_sequence(proposal, [_item("t1")], anchored, CONFIG)
        assert result.ok is True
        assert any(w.get("rule") == "unexpected_overlap" for w in result.warnings)


# ---------------------------------------------------------------------------
# FEEDBACK-03 — explicit infeasibility around overflow (2026-08-14)
# ---------------------------------------------------------------------------

class TestFeedback03OverflowInfeasibility:
    """FF-CAL-03 remainder: the frontend overflow places dropped rows only
    into verified free gaps and reports explicit infeasibility when nothing
    fits. These pins document the backend half of that contract: an overlap
    with an immutable pinned row is never silent, and a staged sequence that
    omits a row the overflow could not place is rejected loudly, naming the
    missing row — never silently accepted."""

    def test_row_overlapping_immutable_pinned_row_is_never_silent(self):
        # A work row over a pinned row (Type: hard, pinned: true — the shape
        # the frontend overflow must avoid) yields a named warning, never a
        # clean pass.
        anchored = [{
            "Block": "Note Processing", "Type": "hard",
            "Start": "17:30", "End": "18:00", "pinned": True,
        }]
        proposal = {"sequence": [_seq_row("t1", "17:35", "18:05")]}
        result = validate_sequence(proposal, [_item("t1")], anchored, CONFIG)
        assert result.ok is True
        assert any(
            "Note Processing" in str(w)
            and w.get("rule") == "unexpected_overlap"
            for w in result.warnings
        )

    def test_merged_sequence_after_infeasible_overflow_never_validates_clean(self):
        # The overflow could not place 't2', so the staged sequence omits it.
        # The server must refuse that loudly (never-bump names the row) rather
        # than return ok for a plan missing an assigned item.
        proposal = {"sequence": [_seq_row("t1", "10:00", "10:30")]}
        result = validate_sequence(
            proposal, [_item("t1"), _item("t2")], [], CONFIG
        )
        assert result.ok is False
        assert any("t2" in e and "missing" in e for e in result.hard_errors)


class TestTimeFrameConstraints:
    """T7 (ui-parity): rows validate within [anchor, effective_eod] when a
    time_frame is supplied; past placement (start < anchor) is HARD, past-EOD
    is a soft ⚠ warning (never-bump places it anyway); backdrop rows exempt."""

    FRAME = {"anchor": "14:00", "effective_eod": "21:00"}

    def _res(self, rows, assigned=None, frame=FRAME):
        assigned = assigned if assigned is not None else [{"id": "X"}]
        return validate_sequence(
            {"sequence": rows}, assigned, [], {}, time_frame=frame)

    def test_row_before_anchor_is_soft_warning(self):
        """2026-07-21 (Adam, T14 run): past placement demoted HARD -> soft
        warning so a proposal always comes back; the cockpit surfaces it as
        an LD24 acceptable defect instead of blocking the sequence."""
        r = self._res([{"id": "X", "start": "13:00", "end": "13:30", "zone": "any"}])
        assert r.ok is True
        assert any("before anchor" in str(w) for w in r.warnings)
        assert not r.hard_errors

    def test_row_past_eod_is_soft_warning(self):
        r = self._res([{"id": "X", "start": "20:45", "end": "21:30", "zone": "any"}])
        assert r.ok is True
        assert any("past EOD" in str(w) for w in r.warnings)

    def test_row_inside_frame_is_clean(self):
        r = self._res([{"id": "X", "start": "14:00", "end": "14:30", "zone": "any"}])
        assert r.ok is True and not r.warnings

    def test_backdrop_rows_fully_exempt(self):
        rows = [
            {"id": "X", "start": "14:00", "end": "14:30", "zone": "any"},
            {"id": "🟡 Trinoor : Morning", "start": "08:30", "end": "12:30",
             "zone": "work_hours", "backdrop": True},
        ]
        r = self._res(rows)
        assert r.ok is True, r.hard_errors     # no chronology/extras/anchor errors
        assert not r.warnings

    def test_no_frame_keeps_legacy_behavior(self):
        r = validate_sequence(
            {"sequence": [{"id": "X", "start": "06:00", "end": "06:30",
                            "zone": "any"}]}, [{"id": "X"}], [], {})
        assert r.ok is True


# ---------------------------------------------------------------------------
# T27 — server-authoritative recurring auto-pins
# ---------------------------------------------------------------------------

class TestRecurringAutoPins:
    def _row(self, **kw):
        base = {"id": "LOOTS", "name": "LOOTS", "blocks": 0.5,
                "is_recurring": True, "scheduled_start": "12:30"}
        base.update(kw)
        return base

    def test_timed_recurring_row_pins_at_native_time(self):
        [pin] = recurring_auto_pins([self._row()])
        assert pin["id"] == "LOOTS"
        assert pin["start"] == "12:30" and pin["end"] == "12:45"
        assert pin["zone"] is None

    def test_fractional_blocks_pin_exact_minutes(self):
        [pin] = recurring_auto_pins(
            [self._row(blocks=1 / 6)])  # 5-minute task
        assert pin["start"] == "12:30" and pin["end"] == "12:35"

    def test_missing_blocks_defaults_to_one_block(self):
        row = self._row()
        del row["blocks"]
        [pin] = recurring_auto_pins([row])
        assert pin["end"] == "13:00"

    def test_non_recurring_and_untimed_and_all_day_skipped(self):
        rows = [
            self._row(id="a", is_recurring=False),
            self._row(id="b", scheduled_start=None),
            self._row(id="c", scheduled_start="nope"),
            self._row(id="d", blocks=0),  # explicit All day — no timeline row
        ]
        assert recurring_auto_pins(rows) == []

    def test_exclude_ids_respected(self):
        assert recurring_auto_pins(
            [self._row()], exclude_ids={"LOOTS"}) == []

    def test_id_falls_back_to_name(self):
        row = self._row()
        del row["id"]
        [pin] = recurring_auto_pins([row])
        assert pin["id"] == "LOOTS"
