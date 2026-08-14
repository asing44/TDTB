from placement_rules import derive_constraints, validate_constraints


def _grant(primary_id, primary, companion_id, companion, fingerprint="fp"):
    return {
        "primary_id": primary_id,
        "companion_id": companion_id,
        "primary_interval": {"start": primary[0], "end": primary[1]},
        "companion_interval": {"start": companion[0], "end": companion[1]},
        "reason": "intentional semantic overlap",
        "planning_config_fingerprint": fingerprint,
    }


def test_constraints_are_metadata_driven_and_name_agnostic():
    assigned = [
        {"name": "Parent work", "blocks": 2},
        {"name": "Child work", "blocks": 1, "relates_to": "[[Parent work]]"},
        {"name": "Systems one", "tags": ["#systems"]},
        {"name": "Systems two", "labels": ["systems"]},
    ]
    anchored = [
        {"Block": "Dinner context", "Type": "window"},
        {"Block": "Dinner reservation", "source": "calendar",
         "Start": "18:30", "End": "20:00"},
    ]
    constraints = derive_constraints(assigned + [{"name": "Dinner context"}], anchored)
    assert any(c["kind"] == "parent_child" for c in constraints)
    assert any(c["kind"] == "systems_group" for c in constraints)
    meal = next(c for c in constraints if c["kind"] == "calendar_companion")
    assert meal["item_id"] == "Dinner context"
    assert meal["effective_duration_minutes"] == 90


def test_ambiguous_calendar_matches_are_not_selected():
    constraints = derive_constraints(
        [{"name": "Dinner context"}],
        [
            {"Block": "Dinner one", "source": "calendar", "Start": "18:00", "End": "19:00"},
            {"Block": "Dinner two", "source": "calendar", "Start": "20:00", "End": "21:00"},
        ],
    )
    assert not any(c["kind"] == "calendar_companion" for c in constraints)


def test_validate_constraints_accepts_parent_systems_and_event_span():
    assigned = [
        {"name": "Parent work", "blocks": 2},
        {"name": "Child work", "blocks": 1, "relates_to": "[[Parent work]]"},
        {"name": "Systems one", "tags": ["systems"]},
        {"name": "Systems two", "tags": ["systems"]},
        {"name": "Dinner context", "blocks": 1},
    ]
    anchored = [
        {"Block": "Dinner reservation", "source": "calendar",
         "Start": "18:30", "End": "20:00"},
    ]
    constraints = derive_constraints(assigned, anchored)
    proposal = {
        "sequence": [
            {"id": "Parent work", "start": "14:00", "end": "15:00", "zone": "any"},
            {"id": "Child work", "start": "14:00", "end": "14:30", "zone": "any"},
            {"id": "Systems one", "start": "16:00", "end": "16:30", "zone": "any"},
            {"id": "Systems two", "start": "16:00", "end": "17:00", "zone": "any"},
            {"id": "Dinner context", "start": "18:30", "end": "20:00", "zone": "any"},
        ],
        "overlap_grants": [
            _grant("Child work", ("14:00", "14:30"), "Parent work", ("14:00", "15:00")),
            _grant("Systems one", ("16:00", "16:30"), "Systems two", ("16:00", "17:00")),
            _grant("Dinner context", ("18:30", "20:00"), "Dinner reservation", ("18:30", "20:00")),
        ],
    }
    assert validate_constraints(proposal, constraints, planning_config_fingerprint="fp") == []


def test_validate_constraints_rejects_wrong_permanent_placements():
    assigned = [
        {"name": "Parent work", "blocks": 2},
        {"name": "Child work", "blocks": 1, "relates_to": "[[Parent work]]"},
        {"name": "Systems one", "tags": ["systems"]},
        {"name": "Systems two", "tags": ["systems"]},
    ]
    constraints = derive_constraints(assigned, [])
    proposal = {
        "sequence": [
            {"id": "Parent work", "start": "14:00", "end": "15:00", "zone": "any"},
            {"id": "Child work", "start": "15:00", "end": "15:30", "zone": "any"},
            {"id": "Systems one", "start": "16:00", "end": "16:30", "zone": "any"},
            {"id": "Systems two", "start": "16:30", "end": "17:00", "zone": "any"},
        ],
        "overlap_grants": [],
    }
    errors = validate_constraints(proposal, constraints, planning_config_fingerprint="fp")
    assert any("must stay within" in error for error in errors)
    assert any("same start time" in error for error in errors)
    assert any("missing exact overlap_grant" in error for error in errors)
