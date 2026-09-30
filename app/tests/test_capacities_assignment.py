"""Public-interface tests for the pure effective-assignment evaluator.

The evaluator is a public seam: these tests exercise it only through
``AssignmentCandidate``, ``AssignmentSettings``, ``evaluate_assignment``, and
``parse_capacities_identity``. Everything is fake and in-process; no provider,
credential, network, or live source is touched.
"""
from __future__ import annotations

from datetime import date, timedelta
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from capacities_assignment import (  # noqa: E402
    AssignmentCandidate,
    AssignmentMode,
    AssignmentSettings,
    evaluate_assignment,
    parse_capacities_identity,
)


SPACE = "space-1"
TODAY = date(2026, 9, 29)

NATIVE = f"capacities:{SPACE}:RootTask:task-1"
CUSTOM = f"capacities:{SPACE}:custom-project:project-1"


def _native(**kwargs) -> AssignmentCandidate:
    return AssignmentCandidate(identity=NATIVE, **kwargs)


def _custom(**kwargs) -> AssignmentCandidate:
    return AssignmentCandidate(identity=CUSTOM, **kwargs)


def _eval(candidate, *, day=TODAY, settings=None):
    return evaluate_assignment(candidate, logical_day=day, settings=settings)


# --------------------------------------------------------------------------
# Precedence: source Assigned > TDTB Excluded > TDTB Auto
# --------------------------------------------------------------------------


def test_source_assigned_true_wins_over_exclusion():
    decision = _eval(
        _custom(source_assigned=True, status_is_open=True),
        settings=AssignmentSettings(excluded_identities=frozenset({CUSTOM})),
    )

    assert decision.mode is AssignmentMode.ASSIGNED
    assert decision.eligible is True
    assert decision.reason_codes == ("source-assigned",)
    assert decision.provenance.exclusion_matched is False
    assert decision.provenance.source_assigned is True


def test_source_assigned_false_with_exclusion_is_excluded():
    decision = _eval(
        _custom(source_assigned=False, status_is_open=True),
        settings=AssignmentSettings(excluded_identities=frozenset({CUSTOM})),
    )

    assert decision.mode is AssignmentMode.EXCLUDED
    assert decision.eligible is False
    assert decision.reason_codes == ("tdtb-excluded",)
    assert decision.provenance.exclusion_matched is True


def test_source_assigned_absent_with_exclusion_is_excluded():
    decision = _eval(
        _custom(status_is_open=True),
        settings=AssignmentSettings(excluded_identities=frozenset({CUSTOM})),
    )

    assert decision.mode is AssignmentMode.EXCLUDED
    assert decision.eligible is False
    assert decision.provenance.source_assigned is None


def test_source_assigned_false_without_exclusion_is_not_eligible():
    decision = _eval(_custom(source_assigned=False, status_is_open=True))

    assert decision.mode is AssignmentMode.NONE
    assert decision.eligible is False
    assert decision.reason_codes == ("no-effective-assignment",)
    assert decision.provenance.auto_conditions == ()


def test_exclusion_set_is_accepted_directly_as_the_settings_seam():
    decision = _eval(_custom(status_is_open=True), settings=frozenset({CUSTOM}))

    assert decision.mode is AssignmentMode.EXCLUDED
    assert decision.reason_codes == ("tdtb-excluded",)


# --------------------------------------------------------------------------
# Native Task Auto: status / due / deadline are OR conditions
# --------------------------------------------------------------------------


def test_native_auto_matches_on_active_status_alone():
    decision = _eval(_native(status="Active", status_is_open=True))

    assert decision.mode is AssignmentMode.AUTO
    assert decision.eligible is True
    assert decision.provenance.auto_conditions == ("status-active",)
    assert decision.reason_codes == ("auto-status-active",)
    assert decision.provenance.native_task is True


def test_native_auto_does_not_match_open_but_not_active_status_without_dates():
    decision = _eval(_native(status="open", status_is_open=True))

    assert decision.mode is AssignmentMode.NONE
    assert decision.eligible is False
    assert decision.reason_codes == ("no-effective-assignment",)


def test_native_auto_matches_due_today_and_overdue():
    today = _eval(_native(status="open", status_is_open=True, due=TODAY))
    overdue = _eval(_native(status="open", status_is_open=True, due=TODAY - timedelta(days=3)))

    assert today.provenance.auto_conditions == ("due-today-or-overdue",)
    assert overdue.provenance.auto_conditions == ("due-today-or-overdue",)
    assert today.eligible is True and overdue.eligible is True


def test_native_auto_does_not_match_a_future_due_date():
    decision = _eval(
        _native(status="open", status_is_open=True, due=TODAY + timedelta(days=1))
    )

    assert decision.mode is AssignmentMode.NONE
    assert decision.eligible is False


def test_native_auto_matches_deadline_within_the_inclusive_two_day_horizon():
    same_day = _eval(_native(status="open", status_is_open=True, deadline=TODAY))
    plus_one = _eval(_native(status="open", status_is_open=True, deadline=TODAY + timedelta(days=1)))
    plus_two = _eval(_native(status="open", status_is_open=True, deadline=TODAY + timedelta(days=2)))
    overdue = _eval(_native(status="open", status_is_open=True, deadline=TODAY - timedelta(days=4)))

    for decision in (same_day, plus_one, plus_two, overdue):
        assert decision.mode is AssignmentMode.AUTO
        assert decision.provenance.auto_conditions == ("deadline-within-horizon",)


def test_native_auto_does_not_match_deadline_beyond_the_horizon():
    decision = _eval(
        _native(status="open", status_is_open=True, deadline=TODAY + timedelta(days=3))
    )

    assert decision.mode is AssignmentMode.NONE
    assert decision.eligible is False


def test_native_auto_missing_dates_do_not_match():
    decision = _eval(_native(status="open", status_is_open=True, due=None, deadline=None))

    assert decision.mode is AssignmentMode.NONE
    assert decision.provenance.auto_conditions == ()


def test_native_auto_conditions_are_or_ed_and_report_every_match():
    decision = _eval(
        _native(
            status="active",
            status_is_open=True,
            due=TODAY - timedelta(days=1),
            deadline=TODAY + timedelta(days=2),
        )
    )

    assert decision.mode is AssignmentMode.AUTO
    assert decision.provenance.auto_conditions == (
        "status-active",
        "due-today-or-overdue",
        "deadline-within-horizon",
    )
    assert decision.reason_codes == (
        "auto-status-active",
        "auto-due-today-or-overdue",
        "auto-deadline-within-horizon",
    )


def test_native_active_status_matches_even_with_a_future_due_date():
    decision = _eval(
        _native(status="active", status_is_open=True, due=TODAY + timedelta(days=9))
    )

    assert decision.mode is AssignmentMode.AUTO
    assert decision.provenance.auto_conditions == ("status-active",)


# --------------------------------------------------------------------------
# Logical-day boundary
# --------------------------------------------------------------------------


def test_logical_day_boundary_moves_the_due_and_deadline_window():
    due_tomorrow = _native(
        status="open", status_is_open=True, due=TODAY + timedelta(days=1)
    )
    deadline_two_days_out = _native(
        status="open", status_is_open=True, deadline=TODAY + timedelta(days=2)
    )

    assert _eval(due_tomorrow).mode is AssignmentMode.NONE
    assert _eval(due_tomorrow, day=TODAY + timedelta(days=1)).mode is AssignmentMode.AUTO

    # deadline == logical_day + 2 is the inclusive edge and moves with the day.
    assert _eval(deadline_two_days_out).mode is AssignmentMode.AUTO
    assert _eval(deadline_two_days_out, day=TODAY + timedelta(days=1)).mode is AssignmentMode.AUTO
    assert _eval(deadline_two_days_out, day=TODAY - timedelta(days=1)).mode is AssignmentMode.NONE


def test_logical_day_is_recorded_in_provenance():
    decision = _eval(_native(status="active", status_is_open=True))

    assert decision.provenance.logical_day == TODAY.isoformat()


# --------------------------------------------------------------------------
# Open-status / safety filter
# --------------------------------------------------------------------------


def test_closed_source_status_is_not_eligible_even_with_a_stale_due_date():
    decision = _eval(
        _native(
            status="done",
            status_is_open=False,
            due=TODAY - timedelta(days=30),
            deadline=TODAY - timedelta(days=30),
        )
    )

    assert decision.mode is AssignmentMode.NONE
    assert decision.eligible is False
    assert decision.reason_codes == ("closed-source-status",)
    assert decision.provenance.auto_conditions == ()


def test_closed_source_status_blocks_explicit_assignment():
    decision = _eval(
        _custom(source_assigned=True, status="dropped", status_is_open=False),
        settings=AssignmentSettings(excluded_identities=frozenset({CUSTOM})),
    )

    assert decision.mode is AssignmentMode.NONE
    assert decision.reason_codes == ("closed-source-status",)


def test_malformed_source_row_is_not_eligible():
    decision = _eval(_custom(source_assigned=True, well_formed=False, status_is_open=True))

    assert decision.mode is AssignmentMode.NONE
    assert decision.eligible is False
    assert decision.reason_codes == ("malformed-source-row",)


def test_cross_space_row_is_not_eligible():
    decision = _eval(
        _custom(source_assigned=True, space_matches=False, status_is_open=True)
    )

    assert decision.mode is AssignmentMode.NONE
    assert decision.reason_codes == ("cross-space-row",)


# --------------------------------------------------------------------------
# Stable identity requirements
# --------------------------------------------------------------------------


def test_missing_identity_fails_closed():
    decision = _eval(
        AssignmentCandidate(identity="", source_assigned=True, status_is_open=True)
    )

    assert decision.mode is AssignmentMode.NONE
    assert decision.reason_codes == ("missing-stable-identity",)


@pytest.mark.parametrize(
    "identity",
    ["Write brief", "task-1", "todoist:1:2:3", "capacities:space-1:RootTask", "capacities:space-1:RootTask: task-1"],
)
def test_unstable_identity_fails_closed(identity):
    decision = _eval(
        AssignmentCandidate(identity=identity, source_assigned=True, status_is_open=True)
    )

    assert decision.mode is AssignmentMode.NONE
    assert decision.reason_codes == ("unstable-identity",)


def test_exclusion_keys_on_qualified_identity_not_object_id_or_title():
    bare_object_id = _eval(
        _native(status="active", status_is_open=True),
        settings=frozenset({"task-1"}),
    )
    title = _eval(
        _native(status="active", status_is_open=True),
        settings=frozenset({"Write brief"}),
    )
    qualified = _eval(
        _native(status="active", status_is_open=True),
        settings=frozenset({NATIVE}),
    )

    assert bare_object_id.mode is AssignmentMode.AUTO
    assert title.mode is AssignmentMode.AUTO
    assert qualified.mode is AssignmentMode.EXCLUDED


def test_surrounding_whitespace_on_the_identity_is_normalized():
    decision = _eval(
        AssignmentCandidate(
            identity=f"  {NATIVE}  ", source_assigned=True, status_is_open=True
        )
    )

    assert decision.mode is AssignmentMode.ASSIGNED
    assert decision.identity == NATIVE


def test_parse_capacities_identity_round_trips_components():
    parsed = parse_capacities_identity(NATIVE)

    assert parsed is not None
    assert parsed.space_id == SPACE
    assert parsed.structure_id == "RootTask"
    assert parsed.object_id == "task-1"
    assert parsed.qualified == NATIVE
    assert parse_capacities_identity("Write brief") is None


# --------------------------------------------------------------------------
# Missing / malformed values and custom type policy
# --------------------------------------------------------------------------


def test_custom_object_without_open_status_or_dates_is_not_eligible():
    decision = _eval(_custom())

    assert decision.mode is AssignmentMode.NONE
    assert decision.reason_codes == ("no-effective-assignment",)


def test_custom_open_status_does_not_implicitly_assign():
    # Custom-object Auto is not yet specified: an open status is a safety
    # pass-through, not an inclusion signal.
    decision = _eval(_custom(status="active", status_is_open=True))

    assert decision.mode is AssignmentMode.NONE
    assert decision.eligible is False
    assert decision.reason_codes == ("no-effective-assignment",)
    assert decision.provenance.native_task is False
    assert decision.provenance.auto_conditions == ()


def test_custom_dates_do_not_implicitly_assign():
    candidates = (
        _custom(due=TODAY),
        _custom(deadline=TODAY),
        _custom(
            status="active",
            status_is_open=True,
            due=TODAY - timedelta(days=5),
            deadline=TODAY + timedelta(days=1),
        ),
    )

    for candidate in candidates:
        decision = _eval(candidate)
        assert decision.mode is AssignmentMode.NONE
        assert decision.eligible is False
        assert decision.reason_codes == ("no-effective-assignment",)
        assert decision.provenance.auto_conditions == ()


def test_custom_dates_and_open_status_still_yield_to_exclusion():
    # Precedence is unchanged: exclusion is evaluated before the (absent)
    # custom Auto policy, so an excluded custom row is Excluded, not None.
    decision = _eval(
        _custom(status="active", status_is_open=True, due=TODAY),
        settings=frozenset({CUSTOM}),
    )

    assert decision.mode is AssignmentMode.EXCLUDED
    assert decision.eligible is False
    assert decision.provenance.exclusion_matched is True


def test_settings_can_override_the_native_task_structures():
    settings = AssignmentSettings(native_task_structures=frozenset({"custom-project"}))

    decision = _eval(_custom(status="active", status_is_open=True), settings=settings)

    assert decision.provenance.native_task is True
    assert decision.provenance.auto_conditions == ("status-active",)


def test_settings_can_override_the_deadline_horizon():
    settings = AssignmentSettings(deadline_horizon_days=5)
    decision = _eval(
        _native(status="open", status_is_open=True, deadline=TODAY + timedelta(days=5)),
        settings=settings,
    )

    assert decision.mode is AssignmentMode.AUTO


# --------------------------------------------------------------------------
# Purity and explanation payload
# --------------------------------------------------------------------------


def test_evaluation_is_pure_and_repeatable():
    candidate = _native(status="active", status_is_open=True, due=TODAY)
    settings = AssignmentSettings(excluded_identities=frozenset({CUSTOM}))

    first = _eval(candidate, settings=settings)
    second = _eval(candidate, settings=settings)

    assert first == second
    assert candidate == _native(status="active", status_is_open=True, due=TODAY)
    assert settings.excluded_identities == frozenset({CUSTOM})


def test_every_decision_carries_a_non_empty_explanation_and_provenance():
    decision = _eval(_native(status="open", status_is_open=True, due=TODAY))

    assert decision.reasons
    assert all(reason.code and reason.detail and reason.origin for reason in decision.reasons)
    assert decision.provenance.identity == NATIVE
    assert decision.provenance.source == "capacities"
    assert decision.provenance.structure_id == "RootTask"
    assert decision.provenance.object_id == "task-1"


def test_eligible_is_true_exactly_for_assigned_and_auto_modes():
    assigned = _eval(_custom(source_assigned=True, status_is_open=True))
    auto = _eval(_native(status="active", status_is_open=True))
    excluded = _eval(_custom(status_is_open=True), settings=frozenset({CUSTOM}))
    none = _eval(_native(status="open", status_is_open=True))

    assert (assigned.mode, assigned.eligible) == (AssignmentMode.ASSIGNED, True)
    assert (auto.mode, auto.eligible) == (AssignmentMode.AUTO, True)
    assert (excluded.mode, excluded.eligible) == (AssignmentMode.EXCLUDED, False)
    assert (none.mode, none.eligible) == (AssignmentMode.NONE, False)


def test_invalid_settings_type_fails_loudly():
    with pytest.raises(TypeError):
        evaluate_assignment(_native(), logical_day=TODAY, settings="task-1")


# --------------------------------------------------------------------------
# Compatibility with existing adapter rows
# --------------------------------------------------------------------------


def _prop(kind: str, key: str, value):
    return {"type": kind, key: value}


def _definition(prop_id: str, kind: str, *, labels=None):
    row = {"id": prop_id, "name": prop_id.title(), "type": kind, "writable": True}
    if labels is not None:
        row["labelSet"] = [{"id": key, "name": name} for key, name in labels]
    return row


def _custom_object():
    return {
        "id": "project-1",
        "spaceId": SPACE,
        "structureId": "custom-project",
        "properties": {
            "title": _prop("title", "title", {"value": "Ship project"}),
            "tdtb": _prop("label", "label", [{"id": "yes"}]),
            "date": _prop("date", "date", {"dateResolution": "day", "start": "2026-09-29T00:00:00.000Z"}),
            "minutes": _prop("number", "number", {"value": 60}),
            "state": _prop("label", "label", [{"id": "active"}]),
        },
    }


def test_decision_is_compatible_with_an_adapter_projected_row():
    from capacities_adapter import (
        CapacitiesAdapter,
        CapacitiesConfig,
        StructureMapping,
    )

    structures = [
        {
            "id": "RootTask",
            "title": "Task",
            "propertyDefinitions": [_definition("title", "title"), _definition("status", "label")],
        },
        {
            "id": "custom-project",
            "title": "Project",
            "propertyDefinitions": [
                _definition("title", "title"),
                _definition("tdtb", "label", labels=[("yes", "TDTB")]),
                _definition("date", "date"),
                _definition("minutes", "number"),
                _definition("state", "label", labels=[("active", "Active")]),
            ],
        },
    ]

    class _Provider:
        def fetch_structures(self):
            return structures

        def list_objects(self, structure_id, cursor=None):
            return {"objects": [], "next_cursor": None}

        def get_object(self, object_id):
            raise AssertionError("read-only compatibility check")

        def patch_object(self, object_id, properties):
            raise AssertionError("read-only compatibility check")

    adapter = CapacitiesAdapter(
        _Provider(),
        CapacitiesConfig(
            space_id=SPACE,
            mappings=(
                StructureMapping(
                    structure_id="RootTask",
                    assignment_property="title",
                    assignment_values=frozenset({"task"}),
                    title_property="title",
                ),
                StructureMapping(
                    structure_id="custom-project",
                    assignment_property="tdtb",
                    assignment_values=frozenset({"yes"}),
                    date_property="date",
                    open_status_property="state",
                    open_status_values=frozenset({"active"}),
                    duration_property="minutes",
                ),
            ),
        ),
    )

    row = adapter.items_for_day_from_objects(TODAY, [_custom_object()]).items[0]
    assert row["assigned"] is True
    assert row["identity"] == CUSTOM

    decision = _eval(
        AssignmentCandidate(
            identity=row["identity"],
            source_assigned=True,
            status="active",
            status_is_open=True,
            due=TODAY,
        )
    )

    assert decision.identity == row["identity"]
    assert decision.eligible is row["assigned"]
    assert decision.provenance.source == row["source"]
    assert decision.provenance.space_id == row["capacities_space_id"]
    assert decision.provenance.structure_id == row["capacities_structure_id"]
    assert decision.provenance.object_id == row["capacities_id"]
    assert decision.provenance.structure_id == row["types"][0]
