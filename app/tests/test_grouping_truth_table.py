"""P6-01 backend grouping truth table.

These fixtures intentionally exercise the public grouping seams without
touching providers or route writers.  A few cases are expected to remain red
until P6-02 adds the missing semantic-group metadata and validation rules.
"""
from __future__ import annotations

from collections import Counter
import sys
from pathlib import Path

import external_sources as ext
import placement_rules
import sequence

sys.path.insert(0, str(Path(__file__).parent))
from test_external_sources import CFG_T5, FakeStore, MONDAY, TODAY, _event


CONFIG = {
    "Template Blocks": {
        "Trinoor Hours": [
            {"Slot": "Morning", "Start": "8:30 AM", "End": "12:30 PM"},
            {"Slot": "Afternoon", "Start": "1:30 PM", "End": "5:00 PM"},
        ],
    },
}


def _row(item_id: str, start: str, end: str) -> dict[str, str]:
    return {"id": item_id, "start": start, "end": end, "zone": "any"}


def _grant(
    primary_id: str,
    primary: tuple[str, str],
    companion_id: str,
    companion: tuple[str, str],
    fingerprint: str = "fp",
) -> dict:
    return {
        "primary_id": primary_id,
        "companion_id": companion_id,
        "primary_interval": {"start": primary[0], "end": primary[1]},
        "companion_interval": {"start": companion[0], "end": companion[1]},
        "reason": "intentional semantic overlap",
        "planning_config_fingerprint": fingerprint,
    }


def _mint_items(selected_ids: list[str]) -> list[dict]:
    items, _zones, _notes = ext.build_schedulable_blocks(
        CFG_T5,
        {"schedulable": {"minting": {"on": True, "sessions": selected_ids}}},
        MONDAY,
        "09:00",
    )
    return [item for item in items if item.get("mint_session")]


def test_parent_child_relation_accepts_containment_with_shared_start_and_exact_grant():
    assigned = [
        {"id": "Parent work", "name": "Parent work", "blocks": 2},
        {
            "id": "Child work",
            "name": "Child work",
            "blocks": 1,
            "relates_to": "[[Parent work]]",
        },
    ]
    constraints = placement_rules.derive_constraints(assigned, [])
    proposal = {
        "sequence": [
            _row("Parent work", "13:00", "14:00"),
            _row("Child work", "13:00", "13:30"),
        ],
        "overlap_grants": [
            _grant("Child work", ("13:00", "13:30"), "Parent work", ("13:00", "14:00")),
        ],
    }

    assert [constraint["kind"] for constraint in constraints] == ["parent_child"]
    assert placement_rules.validate_constraints(
        proposal, constraints, planning_config_fingerprint="fp"
    ) == []


def test_parent_child_relation_rejects_a_late_child_even_when_contained():
    assigned = [
        {"id": "Parent work", "name": "Parent work", "blocks": 2},
        {
            "id": "Child work",
            "name": "Child work",
            "blocks": 1,
            "relates_to": "[[Parent work]]",
        },
    ]
    constraints = placement_rules.derive_constraints(assigned, [])
    errors = placement_rules.validate_constraints(
        {
            "sequence": [
                _row("Parent work", "13:00", "15:00"),
                _row("Child work", "13:30", "14:00"),
            ],
            "overlap_grants": [
                _grant("Child work", ("13:30", "14:00"), "Parent work", ("13:00", "15:00")),
            ],
        },
        constraints,
        planning_config_fingerprint="fp",
    )

    assert any("same start" in error.lower() for error in errors)


def test_parent_child_relation_requires_an_exact_overlap_grant():
    assigned = [
        {"id": "Parent work", "name": "Parent work"},
        {"id": "Child work", "name": "Child work", "relates_to": "[[Parent work]]"},
    ]
    constraints = placement_rules.derive_constraints(assigned, [])
    errors = placement_rules.validate_constraints(
        {
            "sequence": [
                _row("Parent work", "13:00", "14:00"),
                _row("Child work", "13:00", "13:30"),
            ],
            "overlap_grants": [],
        },
        constraints,
    )

    assert errors == [
        "parent/child placement: missing exact overlap_grant for 'Child work' and 'Parent work'"
    ]


def test_equivalent_calendar_companion_uses_exact_event_span_as_duration_override():
    assigned = [{"id": "Walk Meegy", "name": "Walk Meegy", "duration_minutes": 30}]
    anchored = [{
        "Block": "Walk Meegy at Forest Park",
        "source": "calendar",
        "Start": "8:00 AM",
        "End": "9:00 AM",
    }]
    constraints = placement_rules.derive_constraints(assigned, anchored)
    [companion] = [c for c in constraints if c["kind"] == "calendar_companion"]

    assert companion["item_id"] == "Walk Meegy"
    assert companion["event_id"] == "Walk Meegy at Forest Park"
    assert companion["event_interval"] == {"start": "08:00", "end": "09:00"}
    assert companion["source_duration_minutes"] == 30
    assert companion["effective_duration_minutes"] == 60
    assert placement_rules.effective_duration_overrides(constraints) == {
        "Walk Meegy": 60
    }
    assert placement_rules.validate_constraints(
        {
            "sequence": [_row("Walk Meegy", "08:00", "09:00")],
            "overlap_grants": [
                _grant(
                    "Walk Meegy",
                    ("08:00", "09:00"),
                    "Walk Meegy at Forest Park",
                    ("08:00", "09:00"),
                ),
            ],
        },
        constraints,
        planning_config_fingerprint="fp",
    ) == []


def test_selected_mint_rows_and_walls_share_identity_and_canonical_order():
    options = ext.mint_session_options(CFG_T5)
    # Deliberately reverse the source selection.  The projected rows and walls
    # must use the stable option order, not the order of a stale UI payload.
    selected = [options[10]["id"], options[8]["id"]]
    items = _mint_items(selected)
    rows = sequence.placement_window_rows(items)
    walls = sequence.selected_mint_walls(items)

    assert [row["mint_session_id"] for row in rows] == [
        options[8]["id"],
        options[10]["id"],
    ]
    assert [wall[0] for wall in walls] == [row["id"] for row in rows]
    def to_minutes(value: str) -> int:
        hour, minute = value.split(":")
        return int(hour) * 60 + int(minute)

    expected_walls = [
        (to_minutes(options[8]["start"]), to_minutes(options[8]["end"])),
        (to_minutes(options[10]["start"]), to_minutes(options[10]["end"])),
    ]
    assert [wall[1] for wall in walls] == expected_walls


def test_selected_mint_row_and_wall_deduplicate_the_same_source_identity():
    options = ext.mint_session_options(CFG_T5)
    [mint] = _mint_items([options[0]["id"]])
    duplicated_items = [dict(mint), dict(mint)]

    rows = sequence.placement_window_rows(duplicated_items)
    walls = sequence.selected_mint_walls(duplicated_items)

    assert len(rows) == 1
    assert len(walls) == 1
    assert rows[0]["id"] == walls[0][0]
    assert rows[0]["mint_session_id"] == mint["mint_session_id"]


def test_related_non_system_tag_forms_a_shared_start_group_and_validates_grants():
    assigned = [
        {"id": "Focus one", "name": "Focus one", "tags": ["focus"]},
        {"id": "Focus two", "name": "Focus two", "labels": ["#focus"]},
    ]
    constraints = placement_rules.derive_constraints(assigned, [])
    [related] = [c for c in constraints if c["kind"] == "related_group"]

    assert related["tag"] == "focus"
    assert related["item_ids"] == ["Focus one", "Focus two"]
    assert related["require_same_start"] is True
    assert placement_rules.validate_constraints(
        {
            "sequence": [
                _row("Focus one", "13:00", "13:30"),
                _row("Focus two", "13:30", "14:00"),
            ],
            "overlap_grants": [],
        },
        constraints,
    )


def test_duration_tags_do_not_form_shared_start_groups():
    assigned = [
        {"id": "Water plants", "name": "Water plants", "tags": ["🚀10min"]},
        {"id": "Weigh self", "name": "Weigh self", "labels": ["🚀 10 min"]},
        {"id": "Quick cleanup", "name": "Quick cleanup", "tags": ["dur10"]},
    ]

    constraints = placement_rules.derive_constraints(assigned, [])

    assert not any(constraint["kind"] == "related_group" for constraint in constraints)


def test_multi_kind_item_has_one_constraint_per_kind_one_sequence_row_and_one_pair_grant():
    parent = {"id": "Parent project", "name": "Parent project", "blocks": 4}
    child = {
        "id": "Walk systems child",
        "name": "Walk systems child",
        "relates_to": "[[Parent project]]",
        "tags": ["systems", "focus"],
        "blocks": 1,
    }
    peer = {
        "id": "Peer task",
        "name": "Peer task",
        "tags": ["systems", "focus"],
        "blocks": 1,
    }
    event = {
        "Block": "Walk systems child at Park",
        "source": "calendar",
        "Start": "13:00",
        "End": "14:00",
        "capacity_class": "ignored",
    }
    assigned = [parent, child, peer]
    constraints = placement_rules.derive_constraints(assigned, [event])
    sequence_rows = [
        _row("Parent project", "13:00", "15:00"),
        _row("Walk systems child", "13:00", "14:00"),
        _row("Peer task", "13:00", "14:00"),
    ]

    assert Counter(constraint["kind"] for constraint in constraints) == Counter({
        "parent_child": 1,
        "calendar_companion": 1,
        "systems_group": 1,
        "related_group": 1,
    })
    assert [row["id"] for row in sequence_rows].count(child["id"]) == 1

    grants = [
        _grant("Walk systems child", ("13:00", "14:00"), "Parent project", ("13:00", "15:00"), ""),
        _grant("Walk systems child", ("13:00", "14:00"), "Peer task", ("13:00", "14:00"), ""),
        _grant("Walk systems child", ("13:00", "14:00"), "Walk systems child at Park", ("13:00", "14:00"), ""),
    ]
    assert len([
        grant for grant in grants
        if {grant["primary_id"], grant["companion_id"]}
        == {"Walk systems child", "Peer task"}
    ]) == 1

    result = sequence.validate_sequence(
        {
            "sequence": sequence_rows,
            "overlap_grants": grants,
        },
        assigned,
        [event],
        CONFIG,
        planning_config_fingerprint="",
    )
    assert result.ok is True
    assert [row["id"] for row in sequence_rows].count("Walk systems child") == 1
    assert not any(
        diagnostic.get("rule") == "duplicate_sequence_row"
        for diagnostic in result.diagnostics
    )
    assert not any(
        diagnostic.get("rule") in {"calendar_overlap", "allowed_overlap"}
        for diagnostic in result.diagnostics
    )


def test_grouping_constraint_order_is_independent_of_source_arrival_order():
    assigned = [
        {"id": "Parent", "name": "Parent"},
        {"id": "Child walk", "name": "Child walk", "relates_to": "[[Parent]]"},
        {"id": "Systems A", "name": "Systems A", "tags": ["systems"]},
        {"id": "Systems B", "name": "Systems B", "tags": ["systems"]},
    ]
    anchored = [{
        "Block": "Child walk at Park",
        "source": "calendar",
        "Start": "13:00",
        "End": "14:00",
    }]

    first = placement_rules.derive_constraints(assigned, anchored)
    second = placement_rules.derive_constraints(list(reversed(assigned)), anchored)

    assert first == second


def test_repeated_validation_is_deterministic_and_duplicate_mint_wall_has_one_effect():
    options = ext.mint_session_options(CFG_T5)
    [mint] = _mint_items([options[0]["id"]])
    task = {"id": "task-1", "name": "task-1", "zone": "any"}
    proposal = {
        "sequence": [
            _row(mint["id"], "08:30", "09:00"),
            _row("task-1", "08:45", "09:15"),
        ],
        "overlap_grants": [],
    }
    assigned = [dict(mint), task]
    kwargs = {
        "optional_items": [dict(mint)],
        "planning_config_fingerprint": "",
    }
    first = sequence.validate_sequence(proposal, assigned, [], CONFIG, **kwargs)
    second = sequence.validate_sequence(proposal, assigned, [], CONFIG, **kwargs)

    assert first.as_dict() == second.as_dict()
    assert len([error for error in first.hard_errors if "Mint" in error]) == 1
    assert sum(
        diagnostic.get("rule") == "mint_overlap"
        for diagnostic in first.diagnostics
    ) == 1


def test_duplicate_calendar_source_event_counts_once_before_companion_grouping():
    first = _event("Walk Meegy at Forest Park", start="08:00", end="09:00")
    first["event_id"] = "calendar-event-1"
    duplicate = dict(first)
    blocks, _warnings = ext.fetch_calendar_busy(
        FakeStore([first, duplicate]), {}, TODAY
    )

    constraints = placement_rules.derive_constraints(
        [{"id": "Walk Meegy", "name": "Walk Meegy", "duration_minutes": 30}],
        blocks,
    )

    assert len(blocks) == 1
    assert len([c for c in constraints if c["kind"] == "calendar_companion"]) == 1
