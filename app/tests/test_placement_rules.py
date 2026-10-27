from placement_rules import (
    derive_constraints,
    effective_duration_overrides,
    repair_deterministic,
    validate_constraints,
)


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


def test_positive_meegy_semantic_match():
    # Shared activity "walk" plus person "Meegy" is sufficient companion
    # evidence: exactly ONE intended companion override. The event spans
    # 08:00-09:00, so the 30-minute task inherits the 60-minute event span.
    constraints = derive_constraints(
        [{"name": "Walk Meegy", "duration_minutes": 30}],
        [{"Block": "Walk Meegy at Forest Park", "source": "calendar",
          "Start": "08:00", "End": "09:00"}],
    )
    companions = [c for c in constraints if c["kind"] == "calendar_companion"]
    assert len(companions) == 1
    companion = companions[0]
    assert companion["item_id"] == "Walk Meegy"
    assert companion["event_id"] == "Walk Meegy at Forest Park"
    assert companion["event_interval"] == {"start": "08:00", "end": "09:00"}
    assert companion["effective_duration_minutes"] == 60
    assert companion["source_duration_minutes"] == 30
    assert effective_duration_overrides(constraints) == {"Walk Meegy": 60}


def test_meegy_cooking_person_token_does_not_match():
    # "Meegy" alone is a shared person token, not shared activity semantics:
    # cooking dinner must NOT inherit the vet appointment's span. No companion
    # constraint and no duration override (task stays at its own 30 minutes).
    assigned = [{"name": "Cook dinner with Meegy", "duration_minutes": 30}]
    anchored = [{"Block": "Meegy vet appointment", "source": "calendar",
                 "Start": "17:00", "End": "18:30"}]
    constraints = derive_constraints(assigned, anchored)
    assert not any(c["kind"] == "calendar_companion" for c in constraints)
    assert effective_duration_overrides(constraints) == {}


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


# ---------------------------------------------------------------------------
# Deterministic repair (2026-10-27).
#
# The metadata-derived relationships are already consented by the rule itself:
# the model only copies server numbers. So an exact mismatch or a missing
# exact grant is repaired deterministically instead of reaching the user as a
# hard blocker. A blocker survives only when repair is unsafe (a pinned row)
# or impossible (a structural violation this module never repairs).
# ---------------------------------------------------------------------------

def _dinner_constraints_and_proposal(*, row_start="18:00", row_end="19:00",
                                     grants=None, extra_rows=None,
                                     assigned=None, anchored=None):
    assigned = assigned if assigned is not None else [
        {"id": "Foods Dinner", "name": "Foods Dinner", "blocks": 2},
    ]
    anchored = anchored if anchored is not None else [{
        "Block": "Dinner", "source": "calendar",
        "Start": "18:00", "End": "19:30",
    }]
    constraints = derive_constraints(assigned, anchored)
    rows = [{"id": "Foods Dinner", "start": row_start, "end": row_end,
             "zone": "any"}]
    rows.extend(extra_rows or [])
    return constraints, {"sequence": rows, "overlap_grants": list(grants or [])}


def _grants_of(repair):
    return repair.overlap_grants


def test_repair_aligns_calendar_companion_and_synthesizes_the_exact_grant():
    constraints, proposal = _dinner_constraints_and_proposal()
    repair = repair_deterministic(
        proposal, constraints, planning_config_fingerprint="fp"
    )

    assert repair.sequence == [
        {"id": "Foods Dinner", "start": "18:00", "end": "19:30", "zone": "any"},
    ]
    [grant] = _grants_of(repair)
    assert grant["primary_id"] == "Foods Dinner"
    assert grant["companion_id"] == "Dinner"
    assert grant["primary_interval"] == {"start": "18:00", "end": "19:30"}
    assert grant["companion_interval"] == {"start": "18:00", "end": "19:30"}
    assert grant["planning_config_fingerprint"] == "fp"
    assert isinstance(grant["reason"], str) and grant["reason"]
    assert [note["rule"] for note in repair.notes] == [
        "calendar_companion_aligned", "overlap_grant_synthesized",
    ]
    assert repair.notes[0]["detail"] == (
        'Auto-aligned "Foods Dinner" to "Dinner" 18:00\u201319:30'
    )
    assert validate_constraints(
        {"sequence": repair.sequence, "overlap_grants": repair.overlap_grants},
        constraints,
        planning_config_fingerprint="fp",
    ) == []


def test_repair_keeps_a_pinned_calendar_companion_blocker_actionable():
    constraints, proposal = _dinner_constraints_and_proposal()
    repair = repair_deterministic(
        proposal, constraints, planning_config_fingerprint="fp",
        protected_ids={"Foods Dinner"},
    )

    assert repair.notes == []
    assert repair.sequence == proposal["sequence"]
    assert repair.overlap_grants == []
    errors = validate_constraints(
        {"sequence": repair.sequence, "overlap_grants": repair.overlap_grants},
        constraints,
        planning_config_fingerprint="fp",
    )
    assert any("must match" in error and "unpin" in error for error in errors)


def test_repair_synthesizes_grants_for_every_group_kind_when_rows_overlap():
    assigned = [
        {"id": "Parent work", "name": "Parent work", "blocks": 4},
        {"id": "Child work", "name": "Child work", "blocks": 1,
         "relates_to": "[[Parent work]]"},
        {"id": "Systems one", "name": "Systems one", "tags": ["systems"]},
        {"id": "Systems two", "name": "Systems two", "tags": ["systems"]},
        {"id": "Focus one", "name": "Focus one", "tags": ["focus"]},
        {"id": "Focus two", "name": "Focus two", "tags": ["focus"]},
    ]
    constraints = derive_constraints(assigned, [])
    proposal = {"sequence": [
        {"id": "Parent work", "start": "13:00", "end": "15:00", "zone": "any"},
        {"id": "Child work", "start": "13:00", "end": "13:30", "zone": "any"},
        {"id": "Systems one", "start": "16:00", "end": "16:30", "zone": "any"},
        {"id": "Systems two", "start": "16:00", "end": "17:00", "zone": "any"},
        {"id": "Focus one", "start": "17:00", "end": "17:30", "zone": "any"},
        {"id": "Focus two", "start": "17:00", "end": "18:00", "zone": "any"},
    ], "overlap_grants": []}

    repair = repair_deterministic(
        proposal, constraints, planning_config_fingerprint="fp"
    )

    assert repair.sequence == proposal["sequence"]
    assert len(_grants_of(repair)) == 3
    assert all(note["rule"] == "overlap_grant_synthesized"
               for note in repair.notes)
    assert validate_constraints(
        {"sequence": repair.sequence, "overlap_grants": repair.overlap_grants},
        constraints,
        planning_config_fingerprint="fp",
    ) == []


def test_repair_never_grants_a_non_overlapping_pair():
    # Two systems rows that do not overlap at all: the same-start blocker is
    # structural and stays; a grant would document nothing, so none is made.
    assigned = [
        {"id": "Systems one", "name": "Systems one", "tags": ["systems"]},
        {"id": "Systems two", "name": "Systems two", "tags": ["systems"]},
    ]
    constraints = derive_constraints(assigned, [])
    proposal = {"sequence": [
        {"id": "Systems one", "start": "16:00", "end": "16:30", "zone": "any"},
        {"id": "Systems two", "start": "16:30", "end": "17:00", "zone": "any"},
    ], "overlap_grants": []}

    repair = repair_deterministic(
        proposal, constraints, planning_config_fingerprint="fp"
    )

    assert repair.sequence == proposal["sequence"]
    assert repair.overlap_grants == []
    assert repair.notes == []
    errors = validate_constraints(
        {"sequence": repair.sequence, "overlap_grants": repair.overlap_grants},
        constraints,
        planning_config_fingerprint="fp",
    )
    assert any("must share the same start time" in error for error in errors)


def test_repair_leaves_structural_and_mint_placements_untouched():
    assigned = [
        {"id": "Bad time", "name": "Bad time"},
        {"id": "Reversed", "name": "Reversed"},
        {"id": "Mint Afternoon \u00b7 14:00", "name": "Mint Afternoon \u00b7 14:00",
         "tags": ["systems"], "mint_session": True},
    ]
    constraints = derive_constraints(assigned, [])
    proposal = {"sequence": [
        {"id": "Bad time", "start": "7pm", "end": "20:00", "zone": "any"},
        {"id": "Reversed", "start": "14:00", "end": "13:00", "zone": "any"},
        {"id": "Mint Afternoon \u00b7 14:00", "start": "15:00", "end": "15:30",
         "zone": "any"},
    ], "overlap_grants": []}

    repair = repair_deterministic(
        proposal, constraints, planning_config_fingerprint="fp"
    )

    assert repair.notes == []
    assert repair.sequence == proposal["sequence"]
    assert repair.overlap_grants == []


def test_repair_is_idempotent():
    constraints, proposal = _dinner_constraints_and_proposal()
    first = repair_deterministic(
        proposal, constraints, planning_config_fingerprint="fp"
    )
    second = repair_deterministic(
        {"sequence": first.sequence, "overlap_grants": first.overlap_grants},
        constraints, planning_config_fingerprint="fp",
    )

    assert first.notes
    assert second.notes == []
    assert second.sequence == first.sequence
    assert second.overlap_grants == first.overlap_grants


def test_repair_does_not_honor_a_stale_fingerprint_grant():
    stale = _grant("Foods Dinner", ("18:00", "19:00"),
                   "Dinner", ("18:00", "19:30"), fingerprint="stale-fp")
    constraints, proposal = _dinner_constraints_and_proposal(grants=[stale])
    assert validate_constraints(
        proposal, constraints, planning_config_fingerprint="fp"
    ), "a mismatched-fingerprint grant must never satisfy the constraint"

    repair = repair_deterministic(
        proposal, constraints, planning_config_fingerprint="fp"
    )

    assert stale in repair.overlap_grants
    assert [grant["planning_config_fingerprint"]
            for grant in repair.overlap_grants] == ["stale-fp", "fp"]
    assert validate_constraints(
        {"sequence": repair.sequence, "overlap_grants": repair.overlap_grants},
        constraints,
        planning_config_fingerprint="fp",
    ) == []


def test_repair_reanchors_an_existing_grant_instead_of_adding_a_second():
    # The row moves to the event span, so the grant that consented to the old
    # interval must follow it. Exactly one consent record per pair survives,
    # which also keeps repeated revalidations from accumulating grants.
    grant = _grant("Foods Dinner", ("18:00", "19:00"),
                   "Dinner", ("18:00", "19:30"))
    constraints, proposal = _dinner_constraints_and_proposal(grants=[grant])
    repair = repair_deterministic(
        proposal, constraints, planning_config_fingerprint="fp"
    )

    assert repair.overlap_grants == [{
        **grant,
        "primary_interval": {"start": "18:00", "end": "19:30"},
        "companion_interval": {"start": "18:00", "end": "19:30"},
    }]
    assert [note["rule"] for note in repair.notes] == [
        "calendar_companion_aligned",
        "overlap_grant_realigned",
    ]
    assert validate_constraints(
        {"sequence": repair.sequence, "overlap_grants": repair.overlap_grants},
        constraints,
        planning_config_fingerprint="fp",
    ) == []


def test_repair_does_not_break_chronological_order():
    # Snapping this row to the event's 18:00 start would place it before its
    # predecessor, which is a separate hard invariant. The row keeps its
    # placement (and the actionable interval blocker) instead of trading one
    # blocker for another; the grant still records the actual overlap.
    constraints, proposal = _dinner_constraints_and_proposal(
        row_start="18:30", row_end="19:00",
        extra_rows=[{"id": "Early", "start": "18:10", "end": "18:30",
                     "zone": "any"}],
        assigned=[
            {"id": "Early", "name": "Early"},
            {"id": "Foods Dinner", "name": "Foods Dinner", "blocks": 2},
        ],
    )
    # Keep source order chronological, as the validator requires.
    proposal["sequence"] = [proposal["sequence"][1], proposal["sequence"][0]]

    repair = repair_deterministic(
        proposal, constraints, planning_config_fingerprint="fp"
    )

    assert repair.sequence == proposal["sequence"]
    assert [note["rule"] for note in repair.notes] == [
        "overlap_grant_synthesized",
    ]
    [grant] = repair.overlap_grants
    assert grant["primary_interval"] == {"start": "18:30", "end": "19:00"}
    errors = validate_constraints(
        {"sequence": repair.sequence, "overlap_grants": repair.overlap_grants},
        constraints,
        planning_config_fingerprint="fp",
    )
    assert any("must match" in error for error in errors)


def test_repair_is_total_on_malformed_input():
    for proposal in ({}, {"sequence": None}, {"sequence": [None, 5]},
                     {"sequence": [{"id": "X"}]},
                     {"sequence": [{"id": "X", "start": "18:00"}]}):
        repair = repair_deterministic(
            proposal,
            [{"kind": "calendar_companion"},
             {"kind": "systems_group", "item_ids": None},
             {"kind": "parent_child"}],
            planning_config_fingerprint=None,
        )
        assert isinstance(repair.sequence, list)
        assert isinstance(repair.overlap_grants, list)
        assert repair.notes == []
