"""A dead Capacities status option reads UNKNOWN, never closed.

Capacities keeps sending a status label whose option was deleted from the live
structure: the payload member carries the dead option id and uses that same id
as its display name, while the structure's live ``labelSet`` no longer defines
it. The object then carries no readable open OR closed evidence, so it must be
retained as an unassigned review candidate (``status_unknown``) instead of
being silently dropped as closed.

The tests below pin every branch of that rule on both projection paths:

* the legacy path (no stored rule) keeps the row with ``assigned`` False,
  ``capacities_status_state`` ``unknown``, and the ``status_unknown`` review
  reason, even though ``evaluate_assignment`` would otherwise reject a custom
  row with no Assigned On value;
* a dead status is NEVER auto-assigned: a source-assigned row (Assigned On on
  or before the logical day) is a review candidate too;
* a dead status with no Assigned On and no Active enable is still surfaced;
* the stored-rule path reports the same UNKNOWN state and review reason;
* resolved closed stays closed, mixed ``[Completed, dead]`` stays closed, and
  mixed ``[open, dead]`` stays open;
* empty status payloads, the shipped admit-when-assigned-today rule, the
  native Active relief, tag-identity exclusions, and the future-date gate all
  keep their existing behaviour;
* a status property with no live label set detects nothing (unchanged);
* the offline intake replay classifies exactly like the live path.

Every fixture is local and offline: no provider, credential, or vault read.
"""
from __future__ import annotations

from datetime import timedelta
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import capacities_adapter as ca  # noqa: E402
import capacities_intake as ci  # noqa: E402
import capacities_rules as cr  # noqa: E402
from capacities_assignment import AssignmentSettings  # noqa: E402
from tests.test_capacities_adapter import (  # noqa: E402
    SPACE,
    TODAY,
    FakeProvider,
    _adapter,
    _definition,
    _object,
    _prop,
)

EVENT = "custom-event"
NATIVE = "RootTask"
EVENT_IDENTITY = f"capacities:{SPACE}:{EVENT}:event-1"

#: The dead option id Capacities still sends after the option was deleted.
DEAD_ID = "8df2920e-6b1d-4c33-9f0a-2f6f3c1d5a70"

#: A deleted option arrives with its id repeated as the display name.
_DEAD_MEMBER = (DEAD_ID, DEAD_ID)

#: The live label sets: the dead option id is absent from both.
LIVE_EVENT_STATUSES = [
    ("todo", "Todo"),
    ("planning", "Planning"),
    ("scheduled", "Scheduled"),
    ("completed", "Completed"),
    ("dropped", "Dropped"),
]
LIVE_NATIVE_STATUSES = [
    ("open", "Open"),
    ("in-progress", "Active"),
    ("done", "Done"),
]

REVIEW_PREFIX = "Capacities review"


def _structures(
    event_statuses=LIVE_EVENT_STATUSES, native_statuses=LIVE_NATIVE_STATUSES
):
    """Synthetic live contract: a native task plus one custom Event type.

    ``event_statuses=None`` omits the status ``labelSet`` entirely and ``[]``
    gives it an empty one; both mean "no live option identity is known", which
    is the no-dead-detection seam.
    """

    def status_definition(labels):
        if labels is None:
            return _definition("status", "label")
        return _definition("status", "label", labels=labels)

    return [
        {
            "id": NATIVE,
            "title": "Task",
            "propertyDefinitions": [
                _definition("title", "title"),
                status_definition(native_statuses),
            ],
        },
        {
            "id": EVENT,
            "title": "Event",
            "propertyDefinitions": [
                _definition("title", "title"),
                _definition("due", "date"),
                _definition("assigned_on", "date"),
                _definition("tdtb", "label", labels=[("yes", "TDTB"), ("no", "No")]),
                status_definition(event_statuses),
            ],
        },
    ]


def _event_mapping(**overrides):
    base = dict(
        structure_id=EVENT,
        title_property="title",
        open_status_property="status",
        open_status_values=frozenset({"todo", "planning", "scheduled"}),
        assigned_on_property="assigned_on",
        date_property="due",
    )
    base.update(overrides)
    return ca.StructureMapping(**base)


def _native_mapping(**overrides):
    base = dict(
        structure_id=NATIVE,
        title_property="title",
        open_status_property="status",
        open_status_values=frozenset({"open", "in-progress"}),
    )
    base.update(overrides)
    return ca.StructureMapping(**base)


def _native_settings(**overrides):
    base = dict(
        active_statuses=frozenset({"Active"}),
        native_task_structures=frozenset({NATIVE}),
    )
    base.update(overrides)
    return AssignmentSettings(**base)


def _provider(objects, *, structure_id=EVENT, structures=None):
    return FakeProvider(
        structures or _structures(),
        {(structure_id, None): {"objects": list(objects), "next_cursor": None}},
    )


def _status(*members):
    return _prop(
        "label", "label", [{"id": ident, "name": name} for ident, name in members]
    )


def _event_object(
    object_id="event-1",
    *,
    status=(_DEAD_MEMBER,),
    assigned_on=None,
    due=None,
):
    properties = {"title": _prop("title", "title", {"value": "Launch event"})}
    if status is not None:
        properties["status"] = _status(*status)
    if assigned_on is not None:
        properties["assigned_on"] = _prop(
            "date", "date", {"start": assigned_on.isoformat()}
        )
    if due is not None:
        properties["due"] = _prop("date", "date", {"start": due.isoformat()})
    return _object(object_id, EVENT, properties)


def _native_object(object_id="task-1", *, status=(_DEAD_MEMBER,)):
    properties = {"title": _prop("title", "title", {"value": "Native task"})}
    if status is not None:
        properties["status"] = _status(*status)
    return _object(object_id, NATIVE, properties)


def _all_mappings(*mappings):
    """The native mapping is a contract requirement; the Event type is the
    structure under test."""
    if not mappings:
        return (_native_mapping(),)
    return (_native_mapping(), *mappings)


def _read(objects, mappings, **kwargs):
    return _adapter(
        _provider(objects), mappings=_all_mappings(*mappings), **kwargs
    ).items_for_day(TODAY)


def _review_warnings(result):
    return [
        warning for warning in result.warnings if warning.startswith(REVIEW_PREFIX)
    ]


def _signature(result):
    return sorted(
        (
            row["capacities_id"],
            row["assigned"],
            row.get("capacities_status_state"),
            tuple(row.get("capacities_review_reasons") or ()),
        )
        for row in result.items
    )


# ---------------------------------------------------------------------------
# Red case: a dead-only status on a custom type is a review candidate
# ---------------------------------------------------------------------------

def test_dead_only_status_is_an_unassigned_review_candidate():
    result = _read([_event_object()], (_event_mapping(),))

    assert [row["capacities_id"] for row in result.items] == ["event-1"]
    row = result.items[0]
    assert row["assigned"] is False
    assert row["capacities_status_state"] == "unknown"
    assert row["capacities_review_reasons"] == ["status_unknown"]
    assert result.malformed == 0


def test_dead_only_status_produces_one_bounded_content_free_review_warning():
    result = _read([_event_object()], (_event_mapping(),))

    warnings = _review_warnings(result)
    assert len(warnings) == 1
    assert EVENT_IDENTITY in warnings[0]
    assert "status_unknown" in warnings[0]
    assert "Launch event" not in warnings[0]
    assert DEAD_ID not in warnings[0]


def test_dead_status_with_assigned_on_today_is_not_auto_assigned():
    # D2: a dead option may be a renamed Done, so Assigned On on or before the
    # logical day earns review, never an automatic assignment.
    result = _read([_event_object(assigned_on=TODAY)], (_event_mapping(),))

    assert [row["capacities_id"] for row in result.items] == ["event-1"]
    row = result.items[0]
    assert row["assigned"] is False
    assert row["capacities_status_state"] == "unknown"
    assert row["capacities_review_reasons"] == ["status_unknown"]


def test_dead_status_without_assigned_on_or_active_status_is_still_surfaced():
    # D1: the row is surfaced without an Assigned On value and without an
    # Active status. Both enumerated shapes a custom type can have are covered:
    # a negative legacy assignment marker and an Active-enabled structure whose
    # status is not Active. The legacy evaluator rejects both as ineligible; a
    # dead status must still reach the review surface.
    marker_mapping = _event_mapping(
        assigned_on_property=None,
        date_property=None,
        assignment_property="tdtb",
        assignment_values=frozenset({"yes"}),
    )
    enable_mapping = _event_mapping(assigned_on_property=None, date_property=None)
    for mapping, settings in (
        (marker_mapping, None),
        (enable_mapping, AssignmentSettings(active_structures=frozenset({EVENT}))),
    ):
        kwargs = {} if settings is None else {"assignment_settings": settings}

        result = _read([_event_object()], (mapping,), **kwargs)

        assert [row["capacities_id"] for row in result.items] == ["event-1"]
        row = result.items[0]
        assert row["assigned"] is False
        assert row["capacities_status_state"] == "unknown"
        assert row["capacities_review_reasons"] == ["status_unknown"]


def test_stored_rule_path_reads_a_dead_status_as_unknown():
    record = cr.RulesRecord(
        SPACE,
        (cr.RuleStructureRecord(EVENT, active={"all": []}),),
        revision=3,
    )

    result = _read([_event_object()], (_event_mapping(),), rules=record)

    assert [row["capacities_id"] for row in result.items] == ["event-1"]
    row = result.items[0]
    assert row["capacities_rule"] == {"state": "match", "revision": 3}
    assert row["capacities_status_state"] == "unknown"
    assert row["capacities_review_reasons"] == ["status_unknown"]
    assert row["assigned"] is False
    assert len(_review_warnings(result)) == 1


@pytest.mark.parametrize(
    "record",
    [
        pytest.param(
            cr.RulesRecord(
                SPACE,
                (cr.RuleStructureRecord(EVENT, active={"all": []}),),
                revision=3,
            ),
            id="stored-rule",
        ),
        pytest.param(
            cr.RulesRecord(
                SPACE,
                (cr.RuleStructureRecord(EVENT, draft={"all": []}),),
                revision=1,
            ),
            id="direct-refresh",
        ),
    ],
)
def test_shared_completion_field_with_a_dead_status_stays_review(record):
    # The completion field IS the dead payload, so it carries no readable
    # completed evidence: without this the strict completion classifier would
    # call the dead value closed and drop the row before review.
    mapping = _event_mapping(
        completion_property="status", completion_value="completed"
    )

    result = _read([_event_object()], (mapping,), rules=record)

    assert [row["capacities_id"] for row in result.items] == ["event-1"]
    row = result.items[0]
    assert row["assigned"] is False
    assert row["capacities_status_state"] == "unknown"
    assert row["capacities_completion_state"] == "unknown"
    assert row["capacities_review_reasons"] == [
        "status_unknown",
        "completion_unknown",
    ]


# ---------------------------------------------------------------------------
# Resolved statuses keep their meaning
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "status",
    [
        pytest.param((("completed", "Completed"),), id="resolved-completed"),
        pytest.param(("dropped", "Dropped"), id="resolved-dropped"),
        pytest.param(
            (("completed", "Completed"), _DEAD_MEMBER), id="mixed-completed-and-dead"
        ),
        pytest.param(
            (("dropped", "Dropped"), _DEAD_MEMBER), id="mixed-dropped-and-dead"
        ),
    ],
)
def test_resolved_closed_status_stays_dropped(status):
    if isinstance(status[0], str):
        status = (status,)
    result = _read([_event_object(status=status)], (_event_mapping(),))

    assert result.items == []


def test_mixed_open_and_dead_status_stays_open():
    result = _read(
        [
            _event_object(
                status=(("planning", "Planning"), _DEAD_MEMBER),
                assigned_on=TODAY,
            )
        ],
        (_event_mapping(),),
    )

    assert [row["capacities_id"] for row in result.items] == ["event-1"]
    row = result.items[0]
    assert row["assigned"] is True
    # Open is a decided state: no review reason is fabricated for it.
    assert "capacities_review_reasons" not in row
    assert "capacities_status_state" not in row


# ---------------------------------------------------------------------------
# Empty status payloads keep their shipped behaviour
# ---------------------------------------------------------------------------

def test_empty_status_without_assigned_on_stays_dropped():
    result = _read([_event_object(status=())], (_event_mapping(),))

    assert result.items == []


def test_empty_status_with_assigned_on_today_is_still_admitted():
    result = _read([_event_object(status=(), assigned_on=TODAY)], (_event_mapping(),))

    assert [row["capacities_id"] for row in result.items] == ["event-1"]
    row = result.items[0]
    assert row["assigned"] is True
    assert "capacities_review_reasons" not in row
    assert "capacities_status_state" not in row


# ---------------------------------------------------------------------------
# Native task: dead statuses go to review, the Active relief is unchanged
# ---------------------------------------------------------------------------

def test_native_task_with_a_dead_status_is_a_review_candidate():
    result = _adapter(
        _provider([_native_object()], structure_id=NATIVE),
        mappings=(_native_mapping(),),
        assignment_settings=_native_settings(),
    ).items_for_day(TODAY)

    assert [row["capacities_id"] for row in result.items] == ["task-1"]
    row = result.items[0]
    assert row["assigned"] is False
    assert row["capacities_status_state"] == "unknown"
    assert row["capacities_review_reasons"] == ["status_unknown"]


def test_native_active_relief_is_unchanged_for_a_resolved_option():
    result = _adapter(
        _provider(
            [_native_object(status=(("in-progress", "Active"),))],
            structure_id=NATIVE,
        ),
        mappings=(_native_mapping(),),
        assignment_settings=_native_settings(),
    ).items_for_day(TODAY)

    assert [row["capacities_id"] for row in result.items] == ["task-1"]
    row = result.items[0]
    assert row["assigned"] is True
    assert row["capacities_assignment"]["mode"] == "auto"
    assert row["capacities_assignment"]["reasons"] == ["auto-status-active"]
    assert "capacities_review_reasons" not in row


# ---------------------------------------------------------------------------
# No live label set means no dead detection
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "event_statuses",
    [
        pytest.param(None, id="no-label-set"),
        pytest.param([], id="empty-label-set"),
    ],
)
def test_status_property_without_a_live_label_set_detects_nothing(event_statuses):
    result = _adapter(
        _provider(
            [_event_object()], structures=_structures(event_statuses=event_statuses)
        ),
        mappings=_all_mappings(_event_mapping()),
    ).items_for_day(TODAY)

    # Unchanged behaviour: with no live option identity to compare against the
    # readable non-open payload is still a closed exclusion.
    assert result.items == []


# ---------------------------------------------------------------------------
# Offline intake replay classifies exactly like the live path
# ---------------------------------------------------------------------------

def test_offline_replay_classifies_a_dead_status_like_the_live_path():
    objects = [
        _event_object(),
        _event_object(
            "event-2", status=(("planning", "Planning"),), assigned_on=TODAY
        ),
        _event_object("event-3", status=(("completed", "Completed"),)),
    ]

    live = _read(objects, (_event_mapping(),))
    replay = ca.CapacitiesAdapter(
        ci._OfflineProvider(_structures()),
        ca.CapacitiesConfig(
            space_id=SPACE, mappings=_all_mappings(_event_mapping())
        ),
    ).items_for_day_from_objects(TODAY, list(objects))

    expected = [
        ("event-1", False, "unknown", ("status_unknown",)),
        ("event-2", True, None, ()),
    ]
    assert _signature(replay) == expected
    assert _signature(live) == expected


# ---------------------------------------------------------------------------
# Exclusions and the future-date gate still apply
# ---------------------------------------------------------------------------

def test_excluded_identity_with_a_dead_status_is_still_dropped():
    settings = AssignmentSettings(excluded_identities=frozenset({EVENT_IDENTITY}))

    result = _read([_event_object()], (_event_mapping(),), assignment_settings=settings)

    assert result.items == []


def test_future_dated_row_with_a_dead_status_is_still_dropped():
    result = _read(
        [_event_object(due=TODAY + timedelta(days=3))], (_event_mapping(),)
    )

    assert result.items == []


# ---------------------------------------------------------------------------
# The stored-rule path keeps the same gates and never auto-assigns a dead row
# ---------------------------------------------------------------------------

def _matching_rule():
    return cr.RulesRecord(
        SPACE,
        (cr.RuleStructureRecord(EVENT, active={"all": []}),),
        revision=3,
    )


def test_stored_rule_path_never_auto_assigns_a_dead_status():
    # D2 on the rule path: a MATCH under a structure that carries an
    # assignment mapping still reads UNKNOWN and stays unassigned for review.
    mapping = _event_mapping(
        assignment_property="tdtb", assignment_values=frozenset({"yes"})
    )

    result = _read(
        [_event_object(assigned_on=TODAY)], (mapping,), rules=_matching_rule()
    )

    assert [row["capacities_id"] for row in result.items] == ["event-1"]
    row = result.items[0]
    assert row["assigned"] is False
    assert row["capacities_status_state"] == "unknown"
    assert row["capacities_review_reasons"] == ["status_unknown"]


def test_stored_rule_path_still_drops_an_excluded_dead_row():
    settings = AssignmentSettings(excluded_identities=frozenset({EVENT_IDENTITY}))

    result = _read(
        [_event_object()],
        (_event_mapping(),),
        rules=_matching_rule(),
        assignment_settings=settings,
    )

    assert result.items == []


def test_stored_rule_path_still_drops_a_future_dated_dead_row():
    result = _read(
        [_event_object(due=TODAY + timedelta(days=3))],
        (_event_mapping(),),
        rules=_matching_rule(),
    )

    assert result.items == []
