"""Public-interface tests for the pure effective-assignment evaluator.

The evaluator is a public seam: these tests exercise it only through
``AssignmentCandidate``, ``TypeMode``, ``AssignmentSettings``,
``evaluate_assignment``, and ``parse_capacities_identity``. Everything is fake
and in-process; no provider, credential, network, or live source is touched.

Admission is mode-driven (schema version 2): one ``mode`` per structure and
exactly the signals that mode declares. The tables below walk every mode
through admit / reject / unknown so a mode can never silently inherit another
mode's signal.
"""
from __future__ import annotations

from datetime import date, timedelta
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import capacities_settings as cs  # noqa: E402
from capacities_assignment import (  # noqa: E402
    AssignmentCandidate,
    AssignmentMode,
    AssignmentSettings,
    TypeMode,
    evaluate_assignment,
    parse_capacities_identity,
)


SPACE = "space-1"
TODAY = date(2026, 9, 29)
ROOT = "RootTask"
CUSTOM = "custom-project"

NATIVE = f"capacities:{SPACE}:RootTask:task-1"
CUSTOM_ID = f"capacities:{SPACE}:custom-project:obj-1"


def _cand(structure: str = CUSTOM, **kwargs) -> AssignmentCandidate:
    return AssignmentCandidate(
        identity=f"capacities:{SPACE}:{structure}:obj-1", **kwargs
    )


def _native(**kwargs) -> AssignmentCandidate:
    return AssignmentCandidate(identity=NATIVE, **kwargs)


def _settings(types=None, *, open_statuses=None, horizon=None, excluded=None):
    kwargs: dict = {"types": {} if types is None else types}
    if open_statuses is not None:
        kwargs["open_statuses"] = open_statuses
    if horizon is not None:
        kwargs["deadline_horizon_days"] = horizon
    if excluded is not None:
        kwargs["excluded_identities"] = excluded
    return AssignmentSettings(**kwargs)


def _eval(candidate, *, day=TODAY, settings=None):
    return evaluate_assignment(candidate, logical_day=day, settings=settings)


# --------------------------------------------------------------------------
# Precedence: source Assigned > TDTB Excluded > declared mode
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "mode",
    [
        "off",
        "active",
        "assigned",
        "assignedOn",
        "native",
    ],
)
def test_source_assigned_true_wins_over_exclusion_in_every_mode(mode):
    settings = _settings({CUSTOM: _declared(mode)}, excluded=frozenset({CUSTOM_ID}))
    candidate = _cand(
        source_assigned=True,
        status="Active",
        status_is_open=True,
        due=TODAY,
        deadline=TODAY,
        assigned_property=True,
        assigned_on=TODAY,
    )

    decision = _eval(candidate, settings=settings)

    assert decision.mode is AssignmentMode.ASSIGNED
    assert decision.eligible is True
    assert decision.reason_codes == ("source-assigned",)
    assert decision.provenance.exclusion_matched is False
    assert decision.provenance.source_assigned is True


@pytest.mark.parametrize(
    "mode",
    ["off", "active", "assigned", "assignedOn", "native"],
)
def test_exclusion_outranks_the_declared_mode(mode):
    settings = _settings({CUSTOM: _declared(mode)}, excluded=frozenset({CUSTOM_ID}))
    candidate = _cand(
        source_assigned=False,
        status="Active",
        status_is_open=True,
        due=TODAY,
        deadline=TODAY,
        assigned_property=True,
        assigned_on=TODAY,
    )

    decision = _eval(candidate, settings=settings)

    assert decision.mode is AssignmentMode.EXCLUDED
    assert decision.eligible is False
    assert decision.reason_codes == ("tdtb-excluded",)
    assert decision.provenance.exclusion_matched is True


def test_exclusion_set_is_accepted_directly_as_the_settings_seam():
    decision = _eval(_cand(status_is_open=True), settings=frozenset({CUSTOM_ID}))

    assert decision.mode is AssignmentMode.EXCLUDED
    assert decision.reason_codes == ("tdtb-excluded",)


def test_exclusion_keys_on_qualified_identity_not_object_id_or_title():
    settings = _settings({ROOT: "native"}, excluded=frozenset({NATIVE}))
    candidate = _native(status_is_open=True, due=TODAY)

    assert _eval(candidate, settings=frozenset({"task-1"})).mode is AssignmentMode.NONE
    assert _eval(candidate, settings=frozenset({"Write brief"})).mode is AssignmentMode.NONE
    assert _eval(candidate, settings=settings).mode is AssignmentMode.EXCLUDED


# --------------------------------------------------------------------------
# Mode table: admit / reject / unknown per mode
# --------------------------------------------------------------------------
# Each row is (mode declaration, candidate signals, expected mode, expected
# reason codes). The candidate always carries every signal, so a row proves
# the mode reads only its own declaration and never a neighbouring one.


MODE_TABLE = [
    pytest.param(
        TypeMode(mode="off"),
        {},
        AssignmentMode.NONE,
        ("mode-off",),
        id="off-ignores-every-signal",
    ),
    pytest.param(
        TypeMode(mode="active"),
        {"status": "Active", "status_is_open": True},
        AssignmentMode.AUTO,
        ("auto-status-active",),
        id="active-admits-open-name",
    ),
    pytest.param(
        TypeMode(mode="active"),
        {"status": "Inbox", "status_is_open": True},
        AssignmentMode.NONE,
        ("no-effective-assignment",),
        id="active-rejects-other-name",
    ),
    pytest.param(
        TypeMode(mode="active"),
        {"status_is_open": True, "assigned_property": True, "assigned_on": TODAY, "due": TODAY},
        AssignmentMode.NONE,
        ("no-effective-assignment",),
        id="active-unknown-status-never-admits",
    ),
    pytest.param(
        TypeMode(mode="assigned", property_id="tdtb"),
        {"assigned_property": True, "status_is_open": True},
        AssignmentMode.AUTO,
        ("auto-assigned-property",),
        id="assigned-admits-true-property",
    ),
    pytest.param(
        TypeMode(mode="assigned", property_id="tdtb"),
        {"assigned_property": False, "status_is_open": True},
        AssignmentMode.NONE,
        ("no-effective-assignment",),
        id="assigned-rejects-false-property",
    ),
    pytest.param(
        TypeMode(mode="assigned", property_id="tdtb"),
        {"status_is_open": True, "assigned_on": TODAY, "due": TODAY, "status": "Active"},
        AssignmentMode.NONE,
        ("no-effective-assignment",),
        id="assigned-unknown-property-never-admits",
    ),
    pytest.param(
        TypeMode(mode="assignedOn", property_id="date"),
        {"assigned_on": TODAY, "status_is_open": True},
        AssignmentMode.AUTO,
        ("auto-assigned-on",),
        id="assigned-on-admits-today",
    ),
    pytest.param(
        TypeMode(mode="assignedOn", property_id="date"),
        {"assigned_on": TODAY - timedelta(days=9), "status_is_open": True},
        AssignmentMode.AUTO,
        ("auto-assigned-on",),
        id="assigned-on-admits-past",
    ),
    pytest.param(
        TypeMode(mode="assignedOn", property_id="date"),
        {"assigned_on": TODAY + timedelta(days=1), "status_is_open": True},
        AssignmentMode.NONE,
        ("no-effective-assignment",),
        id="assigned-on-rejects-future",
    ),
    pytest.param(
        TypeMode(mode="assignedOn", property_id="date"),
        {"status_is_open": True, "due": TODAY, "assigned_property": True},
        AssignmentMode.NONE,
        ("no-effective-assignment",),
        id="assigned-on-unknown-date-never-admits",
    ),
    pytest.param(
        TypeMode(mode="native"),
        {"status": "Active", "status_is_open": True},
        AssignmentMode.AUTO,
        ("auto-status-active",),
        id="native-admits-open-status",
    ),
    pytest.param(
        TypeMode(mode="native"),
        {"status_is_open": True, "due": TODAY},
        AssignmentMode.AUTO,
        ("auto-due-today-or-overdue",),
        id="native-admits-due-today",
    ),
    pytest.param(
        TypeMode(mode="native"),
        {"status_is_open": True, "deadline": TODAY + timedelta(days=2)},
        AssignmentMode.AUTO,
        ("auto-deadline-within-horizon",),
        id="native-admits-deadline-at-horizon",
    ),
    pytest.param(
        TypeMode(mode="native"),
        {"status_is_open": True},
        AssignmentMode.NONE,
        ("no-effective-assignment",),
        id="native-unknown-signals-never-admit",
    ),
]


@pytest.mark.parametrize("declaration,candidate_kwargs,expected_mode,expected_codes", MODE_TABLE)
def test_mode_reads_only_its_own_declared_signals(
    declaration, candidate_kwargs, expected_mode, expected_codes
):
    settings = _settings({CUSTOM: declaration})

    decision = _eval(_cand(**candidate_kwargs), settings=settings)

    assert decision.mode is expected_mode
    assert decision.reason_codes == expected_codes
    assert decision.eligible is (expected_mode is AssignmentMode.AUTO)
    assert decision.provenance.type_mode == declaration.mode


def test_undeclared_structure_reports_mode_off_and_is_never_admitted():
    decision = _eval(
        _cand(status="Active", status_is_open=True, due=TODAY, assigned_property=True),
        settings=_settings({ROOT: "native"}),
    )

    assert decision.mode is AssignmentMode.NONE
    assert decision.reason_codes == ("mode-off",)
    assert decision.provenance.type_mode == "off"


def test_property_modes_report_their_condition_in_provenance():
    assigned = _eval(
        _cand(assigned_property=True, status_is_open=True),
        settings=_settings({CUSTOM: _declared("assigned")}),
    )
    assigned_on = _eval(
        _cand(assigned_on=TODAY - timedelta(days=2), status_is_open=True),
        settings=_settings({CUSTOM: _declared("assignedOn")}),
    )

    assert assigned.provenance.auto_conditions == ("assigned-property",)
    assert assigned_on.provenance.auto_conditions == ("assigned-on-or-before-day",)
    assert assigned.provenance.native_task is False


def test_plain_mode_strings_are_accepted_as_shorthand():
    settings = _settings({ROOT: "native"})

    decision = _eval(
        _native(status_is_open=True, due=TODAY), settings=settings
    )

    assert settings.mode_of(ROOT) == "native"
    assert settings.types[ROOT] == TypeMode(mode="native")
    assert decision.mode is AssignmentMode.AUTO


# --------------------------------------------------------------------------
# Status admission is tri-state: inherit / disabled / own names
# --------------------------------------------------------------------------


def test_inherited_status_names_match_the_shared_vocabulary():
    settings = _settings(
        {CUSTOM: TypeMode(mode="active")},
        open_statuses=frozenset({"In Progress"}),
    )

    decision = _eval(_cand(status="in  progress", status_is_open=True), settings=settings)

    assert decision.mode is AssignmentMode.AUTO
    assert decision.reason_codes == ("auto-status-active",)
    assert settings.open_statuses_for(CUSTOM) == frozenset({"In Progress"})


def test_disabled_status_admits_nothing_on_status():
    settings = _settings({CUSTOM: TypeMode(mode="active", open_statuses=frozenset())})

    decision = _eval(_cand(status="Active", status_is_open=True), settings=settings)

    assert settings.status_admission_enabled(CUSTOM) is False
    assert decision.mode is AssignmentMode.NONE
    assert decision.reason_codes == ("no-status-vocabulary",)


def test_own_names_override_and_ignore_the_shared_vocabulary():
    settings = _settings(
        {CUSTOM: TypeMode(mode="active", open_statuses=frozenset({"Started"}))},
        open_statuses=frozenset({"Active"}),
    )

    own = _eval(_cand(status="Started", status_is_open=True), settings=settings)
    shared = _eval(_cand(status="Active", status_is_open=True), settings=settings)

    assert settings.open_statuses_for(CUSTOM) == frozenset({"Started"})
    assert own.mode is AssignmentMode.AUTO
    assert shared.mode is AssignmentMode.NONE


def test_configured_names_are_normalized_only_at_comparison_time():
    settings = _settings(
        {CUSTOM: TypeMode(mode="active", open_statuses=frozenset({"In   Progress"}))}
    )

    decision = _eval(_cand(status="in progress", status_is_open=True), settings=settings)

    assert decision.mode is AssignmentMode.AUTO
    assert settings.types[CUSTOM].open_statuses == frozenset({"In   Progress"})


def test_a_non_status_mode_resolves_no_open_names_at_all():
    settings = _settings(
        {
            CUSTOM: TypeMode(mode="assigned", property_id="tdtb"),
            ROOT: "native",
        }
    )

    assert settings.open_statuses_for(CUSTOM) == frozenset()
    assert settings.status_admission_enabled(CUSTOM) is False
    assert settings.open_statuses_for(ROOT) == settings.open_statuses


def test_a_missing_status_signal_never_reads_as_open():
    settings = _settings(
        {CUSTOM: TypeMode(mode="active", open_statuses=frozenset({"Active"}))}
    )

    decision = _eval(_cand(status_is_open=True), settings=settings)

    assert decision.mode is AssignmentMode.NONE
    assert decision.reason_codes == ("no-effective-assignment",)


# --------------------------------------------------------------------------
# Native is an OR of status-open / due / deadline, and date-only when disabled
# --------------------------------------------------------------------------


def test_native_conditions_are_or_ed_and_report_every_match():
    settings = _settings({ROOT: "native"})

    decision = _eval(
        _native(
            status="active",
            status_is_open=True,
            due=TODAY - timedelta(days=1),
            deadline=TODAY + timedelta(days=2),
        ),
        settings=settings,
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


def test_native_with_disabled_status_is_dates_only():
    # The owner's ``RootTask`` decision: tasks stay date-only, so a status label
    # never admits and the dates still do.
    settings = _settings({ROOT: TypeMode(mode="native", open_statuses=frozenset())})

    by_status = _eval(_native(status="Active", status_is_open=True), settings=settings)
    by_due = _eval(_native(status="Active", status_is_open=True, due=TODAY), settings=settings)
    by_deadline = _eval(
        _native(
            status="Active",
            status_is_open=True,
            deadline=TODAY + timedelta(days=2),
        ),
        settings=settings,
    )

    assert by_status.mode is AssignmentMode.NONE
    assert by_status.reason_codes == ("no-effective-assignment",)
    assert by_due.reason_codes == ("auto-due-today-or-overdue",)
    assert by_deadline.reason_codes == ("auto-deadline-within-horizon",)


def test_native_does_not_match_a_future_due_date():
    settings = _settings({ROOT: TypeMode(mode="native", open_statuses=frozenset())})

    decision = _eval(
        _native(status_is_open=True, due=TODAY + timedelta(days=1)), settings=settings
    )

    assert decision.mode is AssignmentMode.NONE


def test_native_own_status_names_replace_the_shared_vocabulary():
    settings = _settings(
        {ROOT: TypeMode(mode="native", open_statuses=frozenset({"In Progress"}))},
        open_statuses=frozenset({"Active"}),
    )

    own = _eval(_native(status="In Progress", status_is_open=True), settings=settings)
    shared = _eval(_native(status="Active", status_is_open=True), settings=settings)

    assert own.reason_codes == ("auto-status-active",)
    assert shared.reason_codes == ("no-effective-assignment",)


# --------------------------------------------------------------------------
# Deadline horizon boundary
# --------------------------------------------------------------------------


def test_deadline_horizon_is_inclusive_and_fails_closed_past_it():
    settings = _settings({ROOT: TypeMode(mode="native", open_statuses=frozenset())})

    for offset, eligible in ((0, True), (2, True), (3, False)):
        decision = _eval(
            _native(status_is_open=True, deadline=TODAY + timedelta(days=offset)),
            settings=settings,
        )
        assert decision.eligible is eligible, offset


def test_deadline_horizon_follows_the_configured_value():
    settings = _settings(
        {ROOT: TypeMode(mode="native", open_statuses=frozenset())}, horizon=5
    )

    at_edge = _eval(
        _native(status_is_open=True, deadline=TODAY + timedelta(days=5)), settings=settings
    )
    beyond = _eval(
        _native(status_is_open=True, deadline=TODAY + timedelta(days=6)), settings=settings
    )

    assert at_edge.mode is AssignmentMode.AUTO
    assert beyond.mode is AssignmentMode.NONE


def test_logical_day_moves_the_due_and_deadline_window():
    settings = _settings({ROOT: TypeMode(mode="native", open_statuses=frozenset())})
    due_tomorrow = _native(status_is_open=True, due=TODAY + timedelta(days=1))
    deadline_two_days_out = _native(
        status_is_open=True, deadline=TODAY + timedelta(days=2)
    )

    assert _eval(due_tomorrow, settings=settings).mode is AssignmentMode.NONE
    assert (
        _eval(due_tomorrow, day=TODAY + timedelta(days=1), settings=settings).mode
        is AssignmentMode.AUTO
    )
    assert (
        _eval(deadline_two_days_out, day=TODAY - timedelta(days=1), settings=settings).mode
        is AssignmentMode.NONE
    )
    assert _eval(deadline_two_days_out, settings=settings).mode is AssignmentMode.AUTO


def test_an_enormous_horizon_cannot_overflow_the_date_window():
    settings = _settings(
        {ROOT: TypeMode(mode="native", open_statuses=frozenset())}, horizon=10 ** 12
    )

    decision = _eval(
        _native(status_is_open=True, deadline=date.max), day=date.min, settings=settings
    )

    assert decision.mode is AssignmentMode.AUTO
    assert decision.provenance.auto_conditions == ("deadline-within-horizon",)


def test_logical_day_is_recorded_in_provenance():
    decision = _eval(_native(status_is_open=True, due=TODAY), settings=_settings({ROOT: "native"}))

    assert decision.provenance.logical_day == TODAY.isoformat()


# --------------------------------------------------------------------------
# Safety preconditions: closed evidence drops on every mode, unknown never does
# --------------------------------------------------------------------------


def _declared(mode: str) -> TypeMode:
    """One declaration per mode, with the property each one requires."""
    if mode == "assigned":
        return TypeMode(mode=mode, property_id="tdtb")
    if mode == "assignedOn":
        return TypeMode(mode=mode, property_id="date")
    return TypeMode(mode=mode)


@pytest.mark.parametrize("mode", ["off", "active", "assigned", "assignedOn", "native"])
def test_closed_source_status_drops_even_with_a_matching_mode(mode):
    settings = _settings({CUSTOM: _declared(mode)})
    candidate = _cand(
        source_assigned=True,
        status="Completed",
        status_is_open=False,
        due=TODAY - timedelta(days=30),
        deadline=TODAY - timedelta(days=30),
        assigned_property=True,
        assigned_on=TODAY,
    )

    decision = _eval(candidate, settings=settings)

    assert decision.mode is AssignmentMode.NONE
    assert decision.eligible is False
    assert decision.reason_codes == ("closed-source-status",)
    assert decision.provenance.auto_conditions == ()


def test_unknown_status_stays_an_unassigned_candidate_never_a_drop():
    settings = _settings({CUSTOM: TypeMode(mode="active", open_statuses=frozenset({"Active"}))})

    decision = _eval(
        _cand(status="Planning", status_is_open=True), settings=settings
    )

    assert decision.mode is AssignmentMode.NONE
    assert decision.eligible is False
    assert decision.reason_codes == ("no-effective-assignment",)


def test_an_unreadable_status_is_kept_as_a_candidate():
    settings = _settings({CUSTOM: TypeMode(mode="native", open_statuses=frozenset({"Active"}))})

    decision = _eval(_cand(status_is_open=None), settings=settings)

    assert decision.mode is AssignmentMode.NONE
    assert decision.reason_codes == ("no-effective-assignment",)


def test_malformed_source_row_is_not_eligible():
    settings = _settings({CUSTOM: "native"}, excluded=frozenset({CUSTOM_ID}))

    decision = _eval(
        _cand(well_formed=False, source_assigned=True, status_is_open=True),
        settings=settings,
    )

    assert decision.mode is AssignmentMode.NONE
    assert decision.reason_codes == ("malformed-source-row",)


def test_cross_space_row_is_not_eligible():
    settings = _settings({CUSTOM: "native"})

    decision = _eval(
        _cand(source_assigned=True, space_matches=False, status_is_open=True),
        settings=settings,
    )

    assert decision.mode is AssignmentMode.NONE
    assert decision.reason_codes == ("cross-space-row",)


def test_missing_identity_fails_closed_before_any_mode_is_read():
    decision = _eval(
        AssignmentCandidate(identity="", source_assigned=True, status_is_open=True),
        settings=_settings({CUSTOM: "native"}),
    )

    assert decision.mode is AssignmentMode.NONE
    assert decision.reason_codes == ("missing-stable-identity",)


@pytest.mark.parametrize(
    "identity",
    [
        "Write brief",
        "task-1",
        "todoist:1:2:3",
        "capacities:space-1:RootTask",
        "capacities:space-1:RootTask: task-1",
    ],
)
def test_unstable_identity_fails_closed(identity):
    decision = _eval(
        AssignmentCandidate(identity=identity, source_assigned=True, status_is_open=True)
    )

    assert decision.mode is AssignmentMode.NONE
    assert decision.reason_codes == ("unstable-identity",)


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
# Structural validation of the declared seam
# --------------------------------------------------------------------------


@pytest.mark.parametrize("mode", ["custom", "", "assignedon", "native "])
def test_an_unknown_mode_is_rejected_at_construction(mode):
    with pytest.raises(ValueError):
        TypeMode(mode=mode)


def test_a_property_is_required_exactly_for_the_property_modes():
    with pytest.raises(ValueError):
        TypeMode(mode="assigned")
    with pytest.raises(ValueError):
        TypeMode(mode="assignedOn")
    with pytest.raises(ValueError):
        TypeMode(mode="native", property_id="tdtb")
    assert TypeMode(mode="assigned", property_id="tdtb").property_id == "tdtb"


def test_a_negative_or_non_integer_horizon_is_rejected():
    with pytest.raises(ValueError):
        AssignmentSettings(deadline_horizon_days=-1)
    with pytest.raises(ValueError):
        AssignmentSettings(deadline_horizon_days=True)


def test_structure_ids_are_enumerable_per_mode():
    settings = _settings(
        {
            "b-active": "active",
            "a-native": "native",
            "c-assigned": TypeMode(mode="assigned", property_id="tdtb"),
        }
    )

    assert settings.structure_ids() == ("a-native", "b-active", "c-assigned")
    assert settings.structure_ids("native") == ("a-native",)
    assert settings.mode_of("missing") == "off"


# --------------------------------------------------------------------------
# End to end from a version-2 settings document
# --------------------------------------------------------------------------


def _v2_settings():
    return cs.CapacitiesSettings(
        revision=4,
        statuses=cs.StatusVocabulary(open=frozenset({"In Progress"}), completed=frozenset({"Completed"})),
        types={
            ROOT: cs.TypeDeclaration(mode="native", statuses=()),
            "project": cs.TypeDeclaration(mode="active"),
            "task-list": cs.TypeDeclaration(mode="active", statuses=("Doing",)),
            "bill": cs.TypeDeclaration(mode="assigned", property_id="paid"),
            "event": cs.TypeDeclaration(mode="assignedOn", property_id="when"),
            "archive": cs.TypeDeclaration(mode="off"),
        },
        native=cs.NativePolicy(deadline_horizon_days=3),
        excluded=frozenset({f"capacities:{SPACE}:bill:obj-1"}),
    )


def test_a_v2_document_becomes_the_evaluator_seam_end_to_end():
    settings = AssignmentSettings.from_v2(_v2_settings())

    assert settings.deadline_horizon_days == 3
    assert settings.open_statuses == frozenset({"In Progress"})
    assert settings.excluded_identities == frozenset({f"capacities:{SPACE}:bill:obj-1"})
    # ``off`` declarations are dropped: an absent structure and an ``off``
    # structure are indistinguishable, and neither is ever enumerated.
    assert "archive" not in settings.types
    assert settings.mode_of("archive") == "off"
    assert settings.structure_ids() == ("RootTask", "bill", "event", "project", "task-list")

    # native: date-only (statuses disabled by the document)
    assert _eval(
        _cand(ROOT, status="In Progress", status_is_open=True), settings=settings
    ).reason_codes == ("no-effective-assignment",)
    assert _eval(
        _cand(ROOT, status_is_open=True, deadline=TODAY + timedelta(days=3)),
        settings=settings,
    ).reason_codes == ("auto-deadline-within-horizon",)
    assert _eval(
        _cand(ROOT, status_is_open=True, deadline=TODAY + timedelta(days=4)),
        settings=settings,
    ).mode is AssignmentMode.NONE

    # active: inherits the document vocabulary
    assert _eval(
        _cand("project", status="In Progress", status_is_open=True), settings=settings
    ).reason_codes == ("auto-status-active",)
    # active: own names replace it
    assert _eval(
        _cand("task-list", status="In Progress", status_is_open=True), settings=settings
    ).reason_codes == ("no-effective-assignment",)
    assert _eval(
        _cand("task-list", status="Doing", status_is_open=True), settings=settings
    ).reason_codes == ("auto-status-active",)

    # assigned: boolean property, and the document's exclusion still wins
    assert _eval(
        _cand("bill", assigned_property=True, status_is_open=True), settings=settings
    ).mode is AssignmentMode.EXCLUDED
    assert _eval(
        _cand("other-bill", assigned_property=True, status_is_open=True), settings=settings
    ).mode is AssignmentMode.NONE

    # assignedOn: date on or before the logical day
    assert _eval(
        _cand("event", assigned_on=TODAY, status_is_open=True), settings=settings
    ).reason_codes == ("auto-assigned-on",)
    assert _eval(
        _cand("event", assigned_on=TODAY + timedelta(days=1), status_is_open=True),
        settings=settings,
    ).mode is AssignmentMode.NONE


def test_from_v2_carries_the_declared_property_ids_for_the_adapter():
    settings = AssignmentSettings.from_v2(_v2_settings())

    assert settings.types["bill"].property_id == "paid"
    assert settings.types["event"].property_id == "when"
    assert settings.types["project"].property_id is None


def test_from_v2_of_a_default_document_admits_nothing():
    # A sparse document declares no structures, so every structure is ``off``.
    settings = AssignmentSettings.from_v2(cs.CapacitiesSettings())

    decision = _eval(
        _cand(status="Active", status_is_open=True, due=TODAY), settings=settings
    )

    assert settings.types == {}
    assert settings.deadline_horizon_days == cs.NativePolicy().deadline_horizon_days
    assert settings.open_statuses == cs.StatusVocabulary().open
    assert decision.reason_codes == ("mode-off",)


# --------------------------------------------------------------------------
# Purity and explanation payload
# --------------------------------------------------------------------------


def test_evaluation_is_pure_and_repeatable():
    candidate = _native(status="active", status_is_open=True, due=TODAY)
    settings = _settings({ROOT: "native"}, excluded=frozenset({CUSTOM_ID}))

    first = _eval(candidate, settings=settings)
    second = _eval(candidate, settings=settings)

    assert first == second
    assert candidate == _native(status="active", status_is_open=True, due=TODAY)
    assert settings.excluded_identities == frozenset({CUSTOM_ID})
    assert settings.mode_of(ROOT) == "native"


def test_every_decision_carries_a_non_empty_explanation_and_provenance():
    decision = _eval(_native(status_is_open=True, due=TODAY), settings=_settings({ROOT: "native"}))

    assert decision.reasons
    assert all(reason.code and reason.detail and reason.origin for reason in decision.reasons)
    assert decision.provenance.identity == NATIVE
    assert decision.provenance.source == "capacities"
    assert decision.provenance.structure_id == ROOT
    assert decision.provenance.object_id == "task-1"
    assert decision.provenance.native_task is True


def test_eligible_is_true_exactly_for_assigned_and_auto_modes():
    settings = _settings({ROOT: "native"}, excluded=frozenset({CUSTOM_ID}))

    assigned = _eval(_cand(source_assigned=True, status_is_open=True), settings=settings)
    auto = _eval(_native(status_is_open=True, due=TODAY), settings=settings)
    excluded = _eval(_cand(status_is_open=True), settings=settings)
    none = _eval(_native(status_is_open=True), settings=settings)

    assert (assigned.mode, assigned.eligible) == (AssignmentMode.ASSIGNED, True)
    assert (auto.mode, auto.eligible) == (AssignmentMode.AUTO, True)
    assert (excluded.mode, excluded.eligible) == (AssignmentMode.EXCLUDED, False)
    assert (none.mode, none.eligible) == (AssignmentMode.NONE, False)


def test_invalid_settings_type_fails_loudly():
    with pytest.raises(TypeError):
        evaluate_assignment(_native(), logical_day=TODAY, settings="task-1")
