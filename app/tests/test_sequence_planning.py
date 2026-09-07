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
