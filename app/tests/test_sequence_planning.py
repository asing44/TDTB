"""Candidate-B sequence planning boundary tests.

The adapter in these tests is deliberately deterministic: the planning layer
must not discover source state or make a provider call while it prepares a
request, and proposal/revalidation must remain separate contracts.
"""
from __future__ import annotations

import copy
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import planning  # noqa: E402


def _inputs(**overrides):
    value = {
        "assigned": [{"id": "A", "name": "A", "blocks": 1}],
        "config": {},
        "anchored_blocks": [],
        "day_setup": {"schedulable": {}},
        "today": date(2026, 8, 17),
        "time_frame": {"anchor": "09:00", "effective_eod": "23:00"},
        "day_semantics": {},
        "planning_config_fingerprint": "fp",
        "pinned_rows": [],
        "overlap_grants": [],
    }
    value.update(overrides)
    return value


def _adapter(rows):
    class Adapter:
        def __init__(self):
            self.calls = []

        def __call__(self, assigned, config, anchored_blocks, ctx=None):
            self.calls.append((assigned, config, anchored_blocks))
            return {"sequence": rows, "overlap_grants": []}

    return Adapter()


def test_preparation_is_pure_and_does_not_call_adapter(monkeypatch):
    inputs = _inputs()
    before = copy.deepcopy(inputs)
    adapter = _adapter([])
    prepared = planning.prepare_sequence(inputs, mode="proposal")
    assert adapter.calls == []
    assert inputs == before
    assert prepared.movable_assigned[0]["id"] == "A"


def test_stale_pinned_row_is_rejected_before_adapter():
    adapter = _adapter([])
    with pytest.raises(planning.PlanningError, match="pinned-row validation failed"):
        prepared = planning.prepare_sequence(
            _inputs(pinned_rows=[{
                "id": "foreign", "start": "09:00", "end": "09:30",
            }]),
            mode="proposal",
        )
        planning.run_proposal(prepared, adapter)
    assert adapter.calls == []


@pytest.mark.parametrize("start,end", [("11:30", "11:45"), ("12:30", "13:00")])
def test_mismatched_recurring_pin_is_rejected_before_adapter(start, end):
    adapter = _adapter([])
    inputs = _inputs(
        assigned=[{
            "id": "A", "name": "A", "blocks": 0.5,
            "is_recurring": True, "scheduled_start": "12:30",
        }],
        pinned_rows=[{"id": "A", "start": start, "end": end, "zone": None}],
    )
    with pytest.raises(planning.PlanningError, match="recurring pinned row"):
        prepared = planning.prepare_sequence(inputs, mode="proposal")
        planning.run_proposal(prepared, adapter)
    assert adapter.calls == []


def test_stale_mint_wall_is_rejected_before_adapter():
    adapter = _adapter([])
    inputs = _inputs(
        config={"Template Blocks": {"Trinoor Hours": [
            {"Slot": "Afternoon", "Start": "1:30 PM", "End": "5:00 PM"},
        ]}},
        day_setup={"schedulable": {"minting": {
            "on": True, "sessions": ["mint:afternoon:14:00"],
        }}},
        anchored_blocks=[{
            "Block": "OPPD", "source": "calendar", "Start": "14:00",
            "End": "14:30", "capacity_class": "fixed",
        }],
    )
    with pytest.raises(planning.PlanningError, match="selected Mint sessions"):
        prepared = planning.prepare_sequence(inputs, mode="proposal")
        planning.run_proposal(prepared, adapter)
    assert adapter.calls == []


def test_proposal_requires_injected_schedulable_rows_and_returns_effective_pins():
    inputs = _inputs(
        config={"Template Blocks": {"Trinoor Hours": [
            {"Slot": "Morning", "Start": "8:30 AM", "End": "12:30 PM"},
        ]}},
        day_setup={"schedulable": {"qt": {"on": True, "n": 1}}},
        pinned_rows=[],
    )
    adapter = _adapter([
        {"id": "A", "start": "09:00", "end": "09:30", "zone": "any"},
        {"id": "Minting", "start": "09:30", "end": "10:30", "zone": "any"},
        {"id": "Quick Tasks", "start": "10:30", "end": "11:00", "zone": "any"},
    ])
    outcome = planning.run_proposal(planning.prepare_sequence(inputs, mode="proposal"), adapter)
    assert outcome.result is not None
    assert outcome.result["sequence"][2]["id"] == "Quick Tasks"
    assert outcome.result["pinned_rows"] == []


def test_run_proposal_records_model_id_repair_in_warnings():
    canonical = "WALL\u22c5E-OS Capacities Buildout"
    mangled = "WALL" + chr(0x12) + "c5E-OS Capacities Buildout"
    inputs = _inputs(assigned=[{"id": canonical, "name": canonical, "blocks": 1}])
    prepared = planning.prepare_sequence(inputs, mode="proposal")
    rows = []
    start = 9 * 60
    for item in prepared.assigned:
        item_id = item.get("id") or item.get("name")
        duration = int((item.get("blocks") or 1) * 30)
        rows.append({
            "id": mangled if item_id == canonical else item_id,
            "start": f"{start // 60:02d}:{start % 60:02d}",
            "end": f"{(start + duration) // 60:02d}:{(start + duration) % 60:02d}",
            "zone": "any",
        })
        start += duration
    outcome = planning.run_proposal(prepared, _adapter(rows))
    assert outcome.result is not None
    assert outcome.result["sequence"][0]["id"] == canonical
    assert any(
        warning.get("rule") == "sequence_id_repaired"
        for warning in outcome.validation.warnings
    )
    assert any(
        warning.get("rule") == "sequence_id_repaired"
        for warning in outcome.result["warnings"]
    )


def test_run_proposal_repairs_mangled_overlap_grant_id():
    canonical_wall = "WALL\u22c5E-OS Capacities Buildout"
    mangled_wall = "WALL" + chr(0x12) + "c5E-OS Capacities Buildout"
    inputs = _inputs(
        assigned=[{"id": "task-1", "name": "task-1", "blocks": 1}],
        day_setup={"schedulable": {"qt": {"on": False},
                                  "minting": {"on": False}}},
        anchored_blocks=[{
            "Block": canonical_wall, "Type": "hard", "Start": "10:00",
            "End": "11:00", "overlap_allowed": False,
        }],
    )
    prepared = planning.prepare_sequence(inputs, mode="proposal")

    def adapter(assigned, config, anchored_blocks, ctx=None):
        return {
            "sequence": [{"id": "task-1", "start": "10:30",
                          "end": "11:30", "zone": "any"}],
            "overlap_grants": [{
                "primary_id": "task-1",
                "companion_id": mangled_wall,
                "primary_interval": {"start": "10:30", "end": "11:30"},
                "companion_interval": {"start": "10:00", "end": "11:00"},
                "reason": "intentional",
                "planning_config_fingerprint": "fp",
            }],
        }

    outcome = planning.run_proposal(prepared, adapter)
    assert outcome.result is not None
    assert outcome.result["overlap_grants"][0]["companion_id"] == canonical_wall
    assert any(
        warning.get("rule") == "allowed_overlap"
        for warning in outcome.validation.warnings
    )
    assert any(
        warning.get("rule") == "overlap_grant_id_repaired"
        for warning in outcome.validation.warnings
    )


def test_run_proposal_never_creates_grant_for_ambiguous_mangled_id():
    canonical_wall = "WALL\u22c5E-OS Capacities Buildout"
    other_wall = "WALL\u00b7E-OS Capacities Buildout"
    mangled_wall = "WALL" + chr(0x12) + "c5E-OS Capacities Buildout"
    inputs = _inputs(
        assigned=[{"id": "task-1", "name": "task-1", "blocks": 1}],
        day_setup={"schedulable": {"qt": {"on": False},
                                  "minting": {"on": False}}},
        anchored_blocks=[
            {"Block": canonical_wall, "Type": "hard", "Start": "10:00",
             "End": "11:00", "overlap_allowed": False},
            {"Block": other_wall, "Type": "hard", "Start": "10:00",
             "End": "11:00", "overlap_allowed": False},
        ],
    )
    prepared = planning.prepare_sequence(inputs, mode="proposal")

    def adapter(assigned, config, anchored_blocks, ctx=None):
        return {
            "sequence": [{"id": "task-1", "start": "10:30",
                          "end": "11:30", "zone": "any"}],
            "overlap_grants": [{
                "primary_id": "task-1",
                "companion_id": mangled_wall,
                "primary_interval": {"start": "10:30", "end": "11:30"},
                "companion_interval": {"start": "10:00", "end": "11:00"},
                "reason": "intentional",
                "planning_config_fingerprint": "fp",
            }],
        }

    outcome = planning.run_proposal(prepared, adapter)
    assert outcome.result is not None
    assert outcome.result["overlap_grants"][0]["companion_id"] == mangled_wall
    assert not any(
        warning.get("rule") == "allowed_overlap"
        for warning in outcome.validation.warnings
    )


def test_revalidation_keeps_injected_rows_optional_and_preserves_descending_order():
    inputs = _inputs(
        assigned=[],
        day_setup={"schedulable": {"qt": {"on": True, "n": 1}}},
        sequence=[
            {"id": "Quick Tasks", "start": "10:00", "end": "10:30", "zone": "any"},
            {"id": "Quick Tasks", "start": "09:00", "end": "09:30", "zone": "any"},
        ],
    )
    prepared = planning.prepare_sequence(inputs, mode="revalidation")
    assert prepared.optional_ids
    result = planning.validate_revalidation(prepared)
    assert result.ok is False
    assert any("chronological" in error for error in result.hard_errors)


def test_revalidation_restores_omitted_native_pin_and_matches_snapshot():
    native_pin = {"id": "A", "start": "12:00", "end": "12:30", "zone": None}
    inputs = _inputs(
        assigned=[{
            "id": "A", "is_recurring": True, "blocks": 1,
            "scheduled_start": "12:00",
        }],
        pinned_rows=[],
        snapshot={
            "pinned_rows": [native_pin],
            "overlap_grants": [],
            "planning_config_fingerprint": "fp",
        },
        sequence=[native_pin],
    )
    prepared = planning.prepare_sequence(inputs, mode="revalidation")
    assert prepared.pinned_rows == [native_pin]
    assert prepared.effective_pins == [native_pin]
    assert planning.validate_revalidation(prepared).ok is True


def test_revalidation_rejects_moved_native_pin_restored_from_server_rows():
    native_pin = {"id": "A", "start": "12:00", "end": "12:30", "zone": None}
    prepared = planning.prepare_sequence(
        _inputs(
            assigned=[{
                "id": "A", "is_recurring": True, "blocks": 1,
                "scheduled_start": "12:00",
            }],
            pinned_rows=[],
            snapshot={
                "pinned_rows": [native_pin],
                "overlap_grants": [],
                "planning_config_fingerprint": "fp",
            },
            sequence=[{
                "id": "A", "start": "10:00", "end": "10:30", "zone": "any",
            }],
        ),
        mode="revalidation",
    )
    result = planning.validate_revalidation(prepared)
    assert result.ok is False
    assert any("immutable snapshot" in error for error in result.hard_errors)


def test_revalidation_receives_effective_anchored_set_without_reapplying_setup():
    anchored = [{"Block": "Effective wall", "Start": "10:00", "End": "11:00",
                 "skip_today": False}]
    prepared = planning.prepare_sequence(
        _inputs(anchored_blocks=anchored), mode="revalidation"
    )
    assert prepared.anchored_blocks == anchored


def test_selected_mint_interval_remains_a_hard_wall():
    mint = {
        "id": "Mint Afternoon · 14:00", "name": "Mint Afternoon · 14:00",
        "blocks": 1, "mint_session": True, "source": "schedulable",
        "placement_window": {"start": "14:00", "end": "14:30"},
    }
    inputs = _inputs(assigned=[{"id": "task", "name": "task", "blocks": 1}],
                     day_setup={"schedulable": {"minting": {"on": True,
                         "sessions": ["mint:afternoon:14:00"]}}},
                     config={"Template Blocks": {"Trinoor Hours": [
                         {"Slot": "Afternoon", "Start": "1:30 PM", "End": "5:00 PM"},
                     ]}},
                     sequence=[{"id": "task", "start": "14:15", "end": "14:45", "zone": "any"}])
    result = planning.validate_revalidation(planning.prepare_sequence(inputs, mode="revalidation"))
    assert result.ok is False
    assert any("selected Mint session" in error for error in result.hard_errors)


def test_invalid_post_judgment_proposal_is_returned_as_rejected_proposal():
    prepared = planning.prepare_sequence(_inputs(), mode="proposal")
    outcome = planning.run_proposal(prepared, _adapter([
        {"id": "A", "start": "14:00", "end": "13:30", "zone": "any"},
    ]))
    assert outcome.rejected_proposal is not None
    assert outcome.result is None
    assert outcome.validation.ok is False


# ---------------------------------------------------------------------------
# 2026-10-13 clock-anchor staleness.
#
# A successful proposal must not go stale merely because the wall clock
# advanced. Snapshots now record the anchor *intent* (clock vs explicit
# override) instead of the moving clock anchor, while still comparing the
# stable effective/config EOD and every legacy snapshot field.
# ---------------------------------------------------------------------------

_CLOCK_FRAME = {
    "anchor": "15:30", "effective_eod": "23:00", "config_eod": "23:59",
}
_NEW_CLOCK_SNAPSHOT = {
    "anchor_source": "clock", "anchor_override": None,
    "effective_eod": "23:00", "config_eod": "23:59",
}
_VALID_ROW = {"id": "A", "start": "15:30", "end": "16:00", "zone": "any"}


def _revalidation(*, day_setup=None, time_frame=None, snapshot=None,
                  config=None, sequence=None, assigned=None):
    return planning.prepare_sequence(_inputs(
        assigned=assigned if assigned is not None else [
            {"id": "A", "name": "A", "blocks": 1}],
        config=config if config is not None else {},
        day_setup=day_setup if day_setup is not None else {},
        time_frame=time_frame or dict(_CLOCK_FRAME),
        snapshot=snapshot if snapshot is not None else {},
        sequence=sequence if sequence is not None else [dict(_VALID_ROW)],
    ), mode="revalidation")


def _snapshot_with_frame(frame, *, fp="fp"):
    return {
        "time_frame": frame,
        "pinned_rows": [],
        "overlap_grants": [],
        "planning_config_fingerprint": fp,
    }


def test_run_proposal_records_clock_intent_without_the_volatile_anchor():
    prepared = planning.prepare_sequence(_inputs(
        day_setup={"schedulable": {"qt": {"on": False}}},
        time_frame={"anchor": "15:15", "effective_eod": "23:00",
                    "config_eod": "23:59"},
    ), mode="proposal")
    outcome = planning.run_proposal(prepared, _adapter([
        {"id": "A", "start": "15:15", "end": "15:45", "zone": "any"},
    ]))
    assert outcome.result is not None
    assert outcome.snapshot["time_frame"] == {
        "anchor_source": "clock",
        "anchor_override": None,
        "effective_eod": "23:00",
        "config_eod": "23:59",
    }


def test_run_proposal_records_override_intent_from_day_setup_anchor():
    prepared = planning.prepare_sequence(_inputs(
        day_setup={"anchor": "7:45 AM", "schedulable": {"qt": {"on": False}}},
        time_frame={"anchor": "07:45", "effective_eod": "23:00",
                    "config_eod": "23:59"},
    ), mode="proposal")
    outcome = planning.run_proposal(prepared, _adapter([
        {"id": "A", "start": "07:45", "end": "08:15", "zone": "any"},
    ]))
    assert outcome.snapshot["time_frame"]["anchor_source"] == "override"
    assert outcome.snapshot["time_frame"]["anchor_override"] == "07:45"


def test_revalidation_new_clock_snapshot_survives_clock_advance():
    prepared = _revalidation(
        day_setup={},
        time_frame=dict(_CLOCK_FRAME),
        snapshot=_snapshot_with_frame(dict(_NEW_CLOCK_SNAPSHOT)),
    )
    assert planning.validate_revalidation(prepared).ok is True


def test_revalidation_new_clock_snapshot_stale_when_override_added():
    prepared = _revalidation(
        day_setup={"anchor": "09:00"},
        time_frame={"anchor": "09:00", "effective_eod": "23:00",
                    "config_eod": "23:59"},
        snapshot=_snapshot_with_frame(dict(_NEW_CLOCK_SNAPSHOT)),
        sequence=[{"id": "A", "start": "09:00", "end": "09:30",
                   "zone": "any"}],
    )
    assert planning.validate_revalidation(prepared).ok is False


def test_revalidation_new_override_snapshot_stale_when_override_changed():
    prepared = _revalidation(
        day_setup={"anchor": "10:00"},
        time_frame={"anchor": "10:00", "effective_eod": "23:00",
                    "config_eod": "23:59"},
        snapshot=_snapshot_with_frame({
            "anchor_source": "override", "anchor_override": "09:00",
            "effective_eod": "23:00", "config_eod": "23:59",
        }),
        sequence=[{"id": "A", "start": "10:00", "end": "10:30",
                   "zone": "any"}],
    )
    assert planning.validate_revalidation(prepared).ok is False


def test_revalidation_new_override_snapshot_stale_when_override_removed():
    prepared = _revalidation(
        day_setup={},
        time_frame=dict(_CLOCK_FRAME),
        snapshot=_snapshot_with_frame({
            "anchor_source": "override", "anchor_override": "09:00",
            "effective_eod": "23:00", "config_eod": "23:59",
        }),
    )
    assert planning.validate_revalidation(prepared).ok is False


def test_revalidation_new_clock_snapshot_stale_on_effective_eod_change():
    prepared = _revalidation(
        day_setup={},
        time_frame={"anchor": "15:30", "effective_eod": "18:00",
                    "config_eod": "23:59"},
        snapshot=_snapshot_with_frame(dict(_NEW_CLOCK_SNAPSHOT)),
    )
    assert planning.validate_revalidation(prepared).ok is False


def test_revalidation_new_clock_snapshot_stale_on_config_eod_change():
    prepared = _revalidation(
        day_setup={},
        time_frame={"anchor": "15:30", "effective_eod": "23:00",
                    "config_eod": "21:00"},
        snapshot=_snapshot_with_frame(dict(_NEW_CLOCK_SNAPSHOT)),
    )
    assert planning.validate_revalidation(prepared).ok is False


def test_revalidation_legacy_clock_anchor_aligned_and_past_is_accepted():
    prepared = _revalidation(
        day_setup={},
        time_frame=dict(_CLOCK_FRAME),
        snapshot=_snapshot_with_frame({
            "anchor": "15:15", "effective_eod": "23:00",
            "config_eod": "23:59",
        }),
    )
    assert planning.validate_revalidation(prepared).ok is True


def test_revalidation_legacy_clock_anchor_ahead_of_live_anchor_is_rejected():
    prepared = _revalidation(
        day_setup={},
        time_frame=dict(_CLOCK_FRAME),
        snapshot=_snapshot_with_frame({
            "anchor": "16:00", "effective_eod": "23:00",
            "config_eod": "23:59",
        }),
    )
    assert planning.validate_revalidation(prepared).ok is False


def test_revalidation_legacy_clock_anchor_off_grid_is_rejected():
    prepared = _revalidation(
        day_setup={},
        config={"Defaults": {"anchor.round_to_minutes": 30}},
        time_frame=dict(_CLOCK_FRAME),
        snapshot=_snapshot_with_frame({
            "anchor": "15:15", "effective_eod": "23:00",
            "config_eod": "23:59",
        }),
    )
    assert planning.validate_revalidation(prepared).ok is False


def test_revalidation_legacy_clock_anchor_malformed_is_rejected():
    prepared = _revalidation(
        day_setup={},
        time_frame=dict(_CLOCK_FRAME),
        snapshot=_snapshot_with_frame({
            "anchor": "later", "effective_eod": "23:00",
            "config_eod": "23:59",
        }),
    )
    assert planning.validate_revalidation(prepared).ok is False


def test_revalidation_legacy_anchor_missing_is_rejected():
    prepared = _revalidation(
        day_setup={},
        time_frame=dict(_CLOCK_FRAME),
        snapshot=_snapshot_with_frame({
            "effective_eod": "23:00", "config_eod": "23:59",
        }),
    )
    assert planning.validate_revalidation(prepared).ok is False


def test_revalidation_legacy_override_snapshot_equal_is_accepted():
    prepared = _revalidation(
        day_setup={"anchor": "09:00"},
        time_frame={"anchor": "09:00", "effective_eod": "23:00",
                    "config_eod": "23:59"},
        snapshot=_snapshot_with_frame({
            "anchor": "09:00", "effective_eod": "23:00",
            "config_eod": "23:59",
        }),
        sequence=[{"id": "A", "start": "09:00", "end": "09:30",
                   "zone": "any"}],
    )
    assert planning.validate_revalidation(prepared).ok is True


def test_revalidation_legacy_override_snapshot_different_is_rejected():
    prepared = _revalidation(
        day_setup={"anchor": "10:00"},
        time_frame={"anchor": "10:00", "effective_eod": "23:00",
                    "config_eod": "23:59"},
        snapshot=_snapshot_with_frame({
            "anchor": "09:00", "effective_eod": "23:00",
            "config_eod": "23:59",
        }),
        sequence=[{"id": "A", "start": "10:00", "end": "10:30",
                   "zone": "any"}],
    )
    assert planning.validate_revalidation(prepared).ok is False


def test_revalidation_empty_snapshot_remains_exempt():
    prepared = _revalidation(snapshot={})
    assert planning.validate_revalidation(prepared).ok is True


def test_revalidation_still_flags_placement_past_before_live_anchor():
    # The live frame still feeds placement validation: a row ahead of the
    # stored snapshot but before the advanced clock anchor is a soft
    # acceptable defect, not a stale snapshot.
    prepared = _revalidation(
        day_setup={},
        time_frame=dict(_CLOCK_FRAME),
        snapshot=_snapshot_with_frame(dict(_NEW_CLOCK_SNAPSHOT)),
        sequence=[{"id": "A", "start": "09:00", "end": "09:30",
                   "zone": "any"}],
    )
    result = planning.validate_revalidation(prepared)
    assert result.ok is True
    assert any(
        isinstance(w, dict) and w.get("rule") == "placement_past"
        for w in result.warnings
    )


def test_default_mint_fill_disappears_past_last_session_fails_closed():
    # Characterization only (2026-10-13), not a repair: the default Mint fill
    # is derived from the live anchor, so once the clock passes the last
    # window session the aggregate Minting row is no longer injected and a
    # stored plan that still contains it revalidates as a foreign row.
    config = {"Template Blocks": {"Trinoor Hours": [
        {"Slot": "Morning", "Start": "8:30 AM", "End": "12:30 PM"},
        {"Slot": "Afternoon", "Start": "1:30 PM", "End": "5:00 PM"},
    ]}}
    mint_row = {"id": "Minting", "start": "16:30", "end": "17:00",
                "zone": "work_hours"}

    def run(anchor):
        inputs = _inputs(
            assigned=[],
            config=config,
            day_setup={},
            time_frame={"anchor": anchor, "effective_eod": "23:00",
                        "config_eod": "23:59"},
            snapshot={},
            sequence=[mint_row],
        )
        prepared = planning.prepare_sequence(inputs, mode="revalidation")
        return prepared, planning.validate_revalidation(prepared)

    on_grid, on_result = run("16:30")
    assert any(block.get("name") == "Minting" for block in on_grid.blocks)
    assert on_result.ok is True

    past_last, past_result = run("16:45")
    assert all(block.get("name") != "Minting" for block in past_last.blocks)
    assert past_result.ok is False
    assert any("Minting" in error for error in past_result.hard_errors)


# ---------------------------------------------------------------------------
# Deterministic repair in the planning boundary (2026-10-27).
#
# A metadata-derived calendar companion and its exact overlap grant are server
# numbers, not model judgment, so both paths repair them instead of rejecting:
# the proposal path before validation, the revalidation path before validation
# and after the snapshot comparison (the repair is never a user mutation).
# ---------------------------------------------------------------------------

_DINNER_ASSIGNED = [{"id": "Foods Dinner", "name": "Foods Dinner", "blocks": 3}]
_DINNER_CALENDAR = [{
    "Block": "Dinner", "source": "calendar", "Start": "18:00", "End": "19:30",
    "capacity_class": "fixed",
}]


def _dinner_inputs(**overrides):
    value = _inputs(
        assigned=copy.deepcopy(_DINNER_ASSIGNED),
        anchored_blocks=copy.deepcopy(_DINNER_CALENDAR),
        day_setup={"schedulable": {"qt": {"on": False},
                                  "minting": {"on": False}}},
    )
    value.update(overrides)
    return value


def test_run_proposal_auto_aligns_calendar_companion_and_records_the_repair():
    prepared = planning.prepare_sequence(_dinner_inputs(), mode="proposal")
    outcome = planning.run_proposal(prepared, _adapter([
        {"id": "Foods Dinner", "start": "18:00", "end": "19:00", "zone": "any"},
    ]))

    assert outcome.result is not None, outcome.validation.hard_errors
    [row] = [r for r in outcome.result["sequence"] if r["id"] == "Foods Dinner"]
    assert (row["start"], row["end"]) == ("18:00", "19:30")
    [grant] = outcome.result["overlap_grants"]
    assert (grant["primary_id"], grant["companion_id"]) == ("Foods Dinner", "Dinner")
    assert any(
        warning.get("rule") == "calendar_companion_aligned"
        and 'Auto-aligned "Foods Dinner" to "Dinner" 18:00\u201319:30'
            == warning.get("detail")
        for warning in outcome.validation.warnings
    )
    assert any(
        warning.get("rule") == "calendar_companion_aligned"
        for warning in outcome.result["warnings"]
    )
    assert outcome.validation.ok is True


def test_run_proposal_keeps_a_pinned_calendar_companion_blocker_actionable():
    pin = {"id": "Foods Dinner", "start": "18:00", "end": "19:00", "zone": "any"}
    prepared = planning.prepare_sequence(
        _dinner_inputs(pinned_rows=[dict(pin)]), mode="proposal"
    )
    outcome = planning.run_proposal(prepared, _adapter([
        {"id": "Foods Dinner", "start": "18:00", "end": "19:00", "zone": "any"},
    ]))

    assert outcome.result is None
    assert any(
        "must match" in error and "unpin" in error
        for error in outcome.validation.hard_errors
    )
    assert not any(
        note.get("rule") == "calendar_companion_aligned"
        for note in outcome.validation.warnings
    )


def test_revalidation_auto_aligns_calendar_companion_and_returns_the_rows():
    prepared = planning.prepare_sequence(_dinner_inputs(
        sequence=[{"id": "Foods Dinner", "start": "18:00", "end": "19:00",
                   "zone": "any"}],
    ), mode="revalidation")
    result = planning.validate_revalidation(prepared)

    assert result.ok is True, result.hard_errors
    assert result.sequence == [{"id": "Foods Dinner", "start": "18:00",
                                "end": "19:30", "zone": "any"}]
    assert len(result.overlap_grants) == 1
    payload = result.as_dict()
    assert payload["sequence"] == result.sequence
    assert payload["overlap_grants"] == result.overlap_grants
    assert any(
        note.get("rule") == "calendar_companion_aligned"
        for note in result.warnings
    )


def test_revalidation_leaves_an_unaltered_layout_without_a_sequence_payload():
    prepared = _revalidation()
    result = planning.validate_revalidation(prepared)

    assert result.ok is True
    assert result.sequence == []
    assert result.overlap_grants == []
    assert set(result.as_dict()) == {"ok", "hard_errors", "warnings"}


def test_revalidation_keeps_a_pinned_companion_blocker_and_the_pinned_interval():
    pin = {"id": "Foods Dinner", "start": "18:00", "end": "19:00", "zone": "any"}
    prepared = planning.prepare_sequence(_dinner_inputs(
        pinned_rows=[copy.deepcopy(pin)],
        snapshot={"pinned_rows": [copy.deepcopy(pin)], "overlap_grants": [],
                  "planning_config_fingerprint": "fp"},
        sequence=[copy.deepcopy(pin)],
    ), mode="revalidation")
    result = planning.validate_revalidation(prepared)

    assert result.ok is False
    assert result.sequence == []
    assert any(
        "must match" in error and "unpin" in error for error in result.hard_errors
    )


def test_revalidation_does_not_resurrect_a_stale_grant():
    stale = {
        "primary_id": "Foods Dinner", "companion_id": "Dinner",
        "primary_interval": {"start": "18:00", "end": "19:00"},
        "companion_interval": {"start": "18:00", "end": "19:30"},
        "reason": "stale consent", "planning_config_fingerprint": "old-fp",
    }
    prepared = planning.prepare_sequence(_dinner_inputs(
        overlap_grants=[stale],
        snapshot={"pinned_rows": [], "overlap_grants": [stale],
                  "planning_config_fingerprint": "fp"},
        sequence=[{"id": "Foods Dinner", "start": "18:00", "end": "19:00",
                   "zone": "any"}],
    ), mode="revalidation")
    result = planning.validate_revalidation(prepared)

    assert result.ok is True, result.hard_errors
    fingerprints = [grant["planning_config_fingerprint"]
                    for grant in result.overlap_grants]
    assert fingerprints == ["old-fp", "fp"]


def test_repaired_proposal_is_exactly_the_committed_body_sequence():
    """The rows/grants that validated are the ones /commit receives.

    /commit takes its rows from the client body (``body.sequence.sequence``)
    and its grants from ``body.overlap_grants``; both are the proposal result
    verbatim, so the repaired layout is what gets written. Revalidating that
    body must also stay clean, proving the commit-side chain agrees with the
    repaired proposal it was handed.
    """
    prepared = planning.prepare_sequence(_dinner_inputs(), mode="proposal")
    outcome = planning.run_proposal(prepared, _adapter([
        {"id": "Foods Dinner", "start": "18:00", "end": "19:00", "zone": "any"},
    ]))
    assert outcome.result is not None

    commit_body = {
        "sequence": {"sequence": copy.deepcopy(outcome.result["sequence"])},
        "overlap_grants": copy.deepcopy(outcome.result["overlap_grants"]),
        "pinned_rows": copy.deepcopy(outcome.result.get("pinned_rows") or []),
        "planning_config_fingerprint": outcome.snapshot["planning_config_fingerprint"],
    }

    committed_rows = [
        row for row in commit_body["sequence"]["sequence"]
        if row["id"] == "Foods Dinner"
    ]
    assert [(row["start"], row["end"]) for row in committed_rows] == [
        ("18:00", "19:30"),
    ]
    assert len(commit_body["overlap_grants"]) == 1

    revalidated = planning.prepare_sequence(_dinner_inputs(
        sequence=commit_body["sequence"]["sequence"],
        overlap_grants=commit_body["overlap_grants"],
        snapshot=outcome.snapshot,
    ), mode="revalidation")
    result = planning.validate_revalidation(revalidated)
    assert result.ok is True, result.hard_errors
    assert result.sequence == []
