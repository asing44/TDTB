"""A status-only (``active``) type never admits an undecidable status.

The live Project type is configured ``mode: active`` with one open status name
(``In Progress``). Its status property is a live label set, so the type can
decide every row that carries a readable option. A row that carries no option
at all (an empty payload), or an option the live label set no longer defines
(``Initialize``/``Idle`` are Phase labels, not status options), has no
admission signal left in a status-only mode.

Such a row must NOT be admitted, and it must not be silently dropped either:
it stays on the existing review surface with ``status_unknown`` so the
operator can give it a status. It is dropped from the candidate surface under
the evaluator's own ``no-effective-assignment`` reason, because a candidate row
is exactly what an "Okay to plan all" confirm promotes into the day's plan.

Before the fix the undecidable row was kept as an unassigned candidate, which
put it on the cockpit's Plan tasks pool.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import capacities_adapter as ca  # noqa: E402
from tests.test_capacities_adapter import (  # noqa: E402
    TODAY,
    FakeProvider,
    _adapter,
    _definition,
    _object,
    _prop,
    _settings,
)

PROJECT = "custom-project"
#: The contract fails closed without an explicit native mapping; the fixture
#: declares it and leaves the type undeclared in settings, so it is ``off``
#: and contributes no rows to these assertions.
NATIVE = "RootTask"
OPEN_NAME = "In Progress"
DEAD_INITIALIZE = "initialize-option"
DEAD_IDLE = "idle-option"

#: The live status label set: neither ``Initialize`` nor ``Idle`` exists here.
LIVE_STATUSES = [
    ("todo", "Todo"),
    ("started", "Started"),
    ("in-progress", "In Progress"),
    ("on-hold", "On Hold"),
    ("completed", "Completed"),
    ("dropped", "Dropped"),
]


def _structures():
    return [
        {
            "id": NATIVE,
            "title": "Task",
            "propertyDefinitions": [
                _definition("title", "title"),
                _definition(
                    "status", "label", labels=[("open", "Open"), ("done", "Done")]
                ),
            ],
        },
        {
            "id": PROJECT,
            "title": "Project",
            "propertyDefinitions": [
                _definition("title", "title"),
                _definition("status", "label", labels=LIVE_STATUSES),
                _definition(
                    "phase",
                    "label",
                    labels=[(DEAD_INITIALIZE, "Initialize"), (DEAD_IDLE, "Idle")],
                ),
            ],
        }
    ]


def _native_mapping():
    return ca.StructureMapping(
        structure_id=NATIVE,
        title_property="title",
        open_status_property="status",
    )


def _project_mapping():
    return ca.StructureMapping(
        structure_id=PROJECT,
        title_property="title",
        open_status_property="status",
    )


def _active_settings():
    return _settings({PROJECT: ("active", (OPEN_NAME,))})


def _status(*members):
    return _prop(
        "label",
        "label",
        [{"id": ident, "name": name} for ident, name in members],
    )


def _project_object(object_id, *, status_members):
    properties = {
        "title": _prop("title", "title", {"value": object_id}),
    }
    if status_members is not None:
        properties["status"] = _status(*status_members)
    return _object(object_id, PROJECT, properties)


def _read(objects):
    provider = FakeProvider(
        _structures(),
        {(PROJECT, None): {"objects": list(objects), "next_cursor": None}},
    )
    return _adapter(
        provider,
        mappings=(_native_mapping(), _project_mapping()),
        assignment_settings=_active_settings(),
        # The version-2 document is the per-type status-vocabulary authority;
        # ``AssignmentSettings`` is the same document's evaluator projection and
        # exposes the two read-only methods the adapter duck-types.
        settings=_active_settings(),
    ).items_for_day_from_objects(TODAY, list(objects))


def _objects():
    return [
        # The one row a status-only mode can decide: its label NAME is open.
        _project_object("in-progress", status_members=[("in-progress", OPEN_NAME)]),
        # No option at all: the live shape of the leaking Project rows.
        _project_object("no-status", status_members=[]),
        # Options the live status label set does not define.
        _project_object("initialize", status_members=[(DEAD_INITIALIZE, "Initialize")]),
        _project_object("idle", status_members=[(DEAD_IDLE, "Idle")]),
    ]


def _review_warnings(result):
    return [
        warning for warning in result.warnings if warning.startswith("Capacities review")
    ]


def _status_review_warnings(result):
    return [
        warning for warning in result.warnings if "status_unknown" in warning
    ]


def test_active_mode_admits_only_the_open_status_row():
    result = _read(_objects())

    assert [row["name"] for row in result.items] == ["in-progress"]
    assert result.items[0]["assigned"] is True
    # No undecidable row is a candidate: an undecidable row on the pool is what
    # an "Okay to plan all" confirm promotes into the day's plan.
    assert [row["capacities_id"] for row in result.items] == ["in-progress"]


def test_active_mode_surfaces_undecidable_status_rows_for_review():
    result = _read(_objects())

    # Not admitted: the evaluator's own verdict for a row this status-only
    # type cannot decide, counted once per affected object.
    assert result.drop_tally == {PROJECT: {"no-effective-assignment": 3}}
    # Still reviewable: the operator-facing reason survives the drop, so these
    # rows stay on the review surface instead of disappearing silently.
    assert result.review_tally == {PROJECT: {"status_unknown": 3}}
    warnings = _status_review_warnings(result)
    assert len(warnings) == 1
    assert warnings[0] == (
        "Capacities type 'Project': 3 objects not admitted — 3 status_unknown; "
        "set the source status to admit it"
    )
    # The items-derived review line lists candidates only, so a dropped row
    # cannot be named there as an unassigned candidate.
    assert _review_warnings(result) == []


def test_active_mode_keeps_a_closed_status_excluded():
    # Control: a status this type can DECIDE stays excluded exactly as before.
    # The readable closed option is not an undecidable row, so it never reaches
    # the review surface.
    result = _read(
        [_project_object("done", status_members=[("completed", "Completed")])]
    )

    assert result.items == []
    assert result.drop_tally == {PROJECT: {"closed-source-status": 1}}
    assert result.review_tally == {}
