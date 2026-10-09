"""Fallback duration and completion/status availability on the stored-rule path.

Test-first for the second U3b slice:

* a structure whose only eligibility path is a stored ACTIVE rule may omit
  the legacy assignment/status mappings entirely — the contract accepts it,
  the object stays a manual-assignment candidate (``assigned`` False), and
  the per-type fallback duration supplies an unmapped or absent duration;
* the legacy path (no rules record, a draft-only entry, or a record for
  another space) still fails the contract exactly as before;
* on the ACTIVE RULE path only, a mapped status is classified
  open / unknown / closed: absent, empty, or unreadable values stay UNKNOWN
  and unassigned instead of failing the object or silently excluding it,
  while a readable non-open value remains a hard exclusion;
* a mapped completion excludes only its configured completed value when it
  is a distinct field; every other readable value (and absent, empty, or
  unreadable ones) is UNKNOWN until an open vocabulary is mapped. A
  same-field completion mirrors the status classification, which does carry
  an open vocabulary. UNKNOWN status or completion forces ``assigned`` False
  even when the rule MATCHes and the source marks the object assigned.
"""
from __future__ import annotations

from datetime import date

import pytest

import capacities_adapter as ca
import capacities_rules as cr
from tests.test_capacities_adapter import (
    FakeProvider,
    _definition,
    _object,
    _prop,
)

SPACE = "space-1"
DAY = date(2026, 9, 29)
RULE = {"prop": "minutes", "op": "gt", "values": [10]}
REVISION = 2


def _fixture_structures():
    """Full synthetic provider schema for the rule-path fallback surfaces.

    ``RootTask`` carries the native status vocabulary; ``custom-project``
    carries an assignment marker, an open-status vocabulary, and a distinct
    completion property whose label set holds one completed value plus one
    value that is neither the completed value nor an open status.
    """
    return [
        {
            "id": "RootTask",
            "title": "Task",
            "propertyDefinitions": [
                _definition("title", "title"),
                _definition("minutes", "number"),
                _definition(
                    "status",
                    "label",
                    labels=[("open", "Open"), ("done", "Done")],
                ),
            ],
        },
        {
            "id": "custom-project",
            "title": "Project",
            "propertyDefinitions": [
                _definition("title", "title"),
                _definition("minutes", "number"),
                _definition("tdtb", "label", labels=[("yes", "TDTB"), ("no", "No")]),
                _definition(
                    "state",
                    "label",
                    labels=[("active", "Active"), ("done", "Done")],
                ),
                _definition(
                    "completion",
                    "label",
                    labels=[("complete", "Complete"), ("started", "Started")],
                ),
            ],
        },
    ]


def _legacy_root_mapping():
    """A RootTask mapping valid on the legacy path, so a custom mapping is
    the only contract subject under test."""
    return ca.StructureMapping(
        "RootTask",
        open_status_property="status",
        open_status_values=frozenset({"open"}),
    )


def _title_only_mapping(structure_id):
    return ca.StructureMapping(structure_id)


def _status_mapping(**overrides):
    fields = {
        "assignment_property": "tdtb",
        "assignment_values": frozenset({"yes"}),
        "open_status_property": "state",
        "open_status_values": frozenset({"active"}),
    }
    fields.update(overrides)
    return ca.StructureMapping("custom-project", **fields)


def _rules(*entries, space=SPACE):
    return cr.RulesRecord(space, tuple(entries), revision=REVISION)


def _custom_rule(fallback=None, active=RULE):
    return cr.RuleStructureRecord(
        "custom-project", active=active, fallback_minutes=fallback
    )


def _adapter(mappings, record):
    return ca.CapacitiesAdapter(
        FakeProvider(_fixture_structures(), {}),
        ca.CapacitiesConfig(SPACE, tuple(mappings), rules=record),
    )


def _payload(value):
    """A label payload from a token, or a raw payload dict passed through."""
    if isinstance(value, dict):
        return value
    return _prop("label", "label", [{"id": value, "name": value}])


def _custom_object(
    *,
    minutes=20,
    tdtb="yes",
    state="active",
    completion=None,
    object_id="obj-1",
):
    properties = {"title": _prop("title", "title", "Sample")}
    if minutes is not None:
        properties["minutes"] = _prop("number", "number", minutes)
    if tdtb is not None:
        properties["tdtb"] = _payload(tdtb)
    if state is not None:
        properties["state"] = _payload(state)
    if completion is not None:
        properties["completion"] = _payload(completion)
    return _object(object_id, "custom-project", properties)


# ---------------------------------------------------------------------------
# Active rules allow title-only mappings; missing mappings are manual candidates
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("structure_id", ["RootTask", "custom-project"])
def test_active_rule_allows_title_only_mapping(structure_id):
    """A stored rule is a sufficient contract for a mapping with neither a
    source assignment marker nor a mapped status, native or custom."""
    mappings = [_title_only_mapping("RootTask"), _title_only_mapping("custom-project")]
    record = _rules(
        cr.RuleStructureRecord("RootTask", active=RULE),
        _custom_rule(),
    )
    properties = {
        "title": _prop("title", "title", "Sample"),
        "minutes": _prop("number", "number", 20),
    }
    obj = _object("obj-1", structure_id, properties)

    result = _adapter(mappings, record).items_for_day_from_objects(DAY, [obj])

    assert len(result.items) == 1
    row = result.items[0]
    # Manual assignment: eligible, retained, and deliberately not assigned.
    assert row["assigned"] is False
    assert row["capacities_rule"] == {"state": "match", "revision": REVISION}
    assert row["capacities_status_state"] == "unavailable"
    assert row["capacities_completion_state"] == "unavailable"
    assert row["capacities_completion_supported"] is False
    assert row["duration"] == 30


@pytest.mark.parametrize(
    "record",
    [
        pytest.param(None, id="no-rules"),
        pytest.param(
            _rules(cr.RuleStructureRecord("custom-project", draft=RULE)),
            id="draft-only",
        ),
        pytest.param(
            _rules(
                cr.RuleStructureRecord("custom-project", active=RULE),
                space="other-space",
            ),
            id="other-space",
        ),
    ],
)
def test_title_only_mapping_without_active_rule_still_raises(record):
    """No record, a draft-only entry, or a record for another space keeps the
    legacy contract requirement instead of silently admitting the structure."""
    mappings = [_legacy_root_mapping(), _title_only_mapping("custom-project")]
    with pytest.raises(
        ca.CapacitiesContractError,
        match="requires an assignment property or a mapped status property",
    ):
        _adapter(mappings, record).items_for_day_from_objects(DAY, [])


# ---------------------------------------------------------------------------
# Per-type fallback duration
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "fallback,expected",
    [
        pytest.param(None, 30, id="no-fallback-defaults-to-30"),
        pytest.param(45, 45, id="custom-fallback"),
        pytest.param(0, 0, id="zero-fallback-is-valid"),
    ],
)
def test_fallback_duration_used_when_duration_unmapped(fallback, expected):
    mappings = [_legacy_root_mapping(), _title_only_mapping("custom-project")]
    record = _rules(_custom_rule(fallback=fallback))

    result = _adapter(mappings, record).items_for_day_from_objects(
        DAY, [_custom_object()]
    )

    assert len(result.items) == 1
    row = result.items[0]
    assert row["duration"] == expected
    assert row["duration_minutes"] == expected


def test_mapped_duration_wins_over_fallback():
    mappings = [
        _legacy_root_mapping(),
        ca.StructureMapping("custom-project", duration_property="minutes"),
    ]
    record = _rules(_custom_rule(fallback=45))

    result = _adapter(mappings, record).items_for_day_from_objects(
        DAY, [_custom_object(minutes=60)]
    )

    assert len(result.items) == 1
    assert result.items[0]["duration"] == 60


def test_mapped_duration_absent_uses_fallback():
    mappings = [
        _legacy_root_mapping(),
        ca.StructureMapping("custom-project", duration_property="minutes"),
    ]
    record = _rules(_custom_rule(fallback=45))

    result = _adapter(mappings, record).items_for_day_from_objects(
        DAY, [_custom_object(minutes=None)]
    )

    assert len(result.items) == 1
    assert result.items[0]["duration"] == 45


# ---------------------------------------------------------------------------
# Completion availability
# ---------------------------------------------------------------------------

def test_unmapped_completion_is_unavailable_and_admits():
    mappings = [_legacy_root_mapping(), _status_mapping()]
    record = _rules(_custom_rule())

    result = _adapter(mappings, record).items_for_day_from_objects(
        DAY, [_custom_object()]
    )

    assert len(result.items) == 1
    row = result.items[0]
    assert row["assigned"] is True
    assert row["capacities_status_state"] == "open"
    assert row["capacities_completion_supported"] is False
    assert row["capacities_completion_state"] == "unavailable"


def test_same_field_completion_open_admits_and_done_excludes():
    """When completion maps the status field, the mapped open vocabulary is
    available, so an open value stays a normal MATCH and a non-open value is
    a known-closed hard exclusion."""
    mapping = _status_mapping(completion_property="state", completion_value="done")
    mappings = [_legacy_root_mapping(), mapping]
    record = _rules(_custom_rule())

    result = _adapter(mappings, record).items_for_day_from_objects(
        DAY,
        [
            _custom_object(object_id="obj-open"),
            _custom_object(state="done", object_id="obj-done"),
        ],
    )

    assert [row["capacities_id"] for row in result.items] == ["obj-open"]
    row = result.items[0]
    assert row["assigned"] is True
    assert row["capacities_completion_supported"] is True
    assert row["capacities_status_state"] == "open"
    assert row["capacities_completion_state"] == "open"


def test_distinct_completion_target_excludes_even_with_match_and_source():
    """Only the configured completed value is a known closed state on a
    distinct completion field; it excludes despite a MATCH and source true."""
    mapping = _status_mapping(completion_property="completion", completion_value="complete")
    mappings = [_legacy_root_mapping(), mapping]
    record = _rules(_custom_rule())

    result = _adapter(mappings, record).items_for_day_from_objects(
        DAY, [_custom_object(completion="complete")]
    )

    assert result.items == []


@pytest.mark.parametrize(
    "completion",
    [
        pytest.param("started", id="other-readable-value"),
        pytest.param(None, id="missing"),
        pytest.param({"type": "label", "label": []}, id="empty"),
        pytest.param({"type": "label", "label": "started"}, id="unreadable"),
    ],
)
def test_distinct_completion_unknown_values_retain_unassigned(completion):
    """A distinct completion field has no open vocabulary: every readable
    value other than the completed value, and every absent/empty/unreadable
    value, is UNKNOWN and stays an unassigned warning candidate."""
    mapping = _status_mapping(completion_property="completion", completion_value="complete")
    mappings = [_legacy_root_mapping(), mapping]
    record = _rules(_custom_rule())

    result = _adapter(mappings, record).items_for_day_from_objects(
        DAY, [_custom_object(completion=completion)]
    )

    assert len(result.items) == 1
    row = result.items[0]
    assert row["assigned"] is False
    assert row["capacities_rule"] == {"state": "match", "revision": REVISION}
    assert row["capacities_status_state"] == "open"
    assert row["capacities_completion_state"] == "unknown"
    assert result.malformed == 0


# ---------------------------------------------------------------------------
# Status availability on the active-rule path
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "state",
    [
        pytest.param(None, id="missing"),
        pytest.param({"type": "label", "label": []}, id="empty"),
        pytest.param({"type": "label", "label": "active"}, id="unreadable"),
    ],
)
def test_unknown_status_is_retained_unassigned(state):
    """An absent, empty, or unreadable mapped status is UNKNOWN on the rule
    path: the row is retained unassigned instead of failing the object or
    silently excluding it, and the rule state stays truthful."""
    mappings = [_legacy_root_mapping(), _status_mapping()]
    record = _rules(_custom_rule())

    result = _adapter(mappings, record).items_for_day_from_objects(
        DAY, [_custom_object(state=state)]
    )

    assert len(result.items) == 1
    row = result.items[0]
    assert row["assigned"] is False
    assert row["capacities_rule"] == {"state": "match", "revision": REVISION}
    assert row["capacities_status_state"] == "unknown"
    assert row["capacities_completion_state"] == "unavailable"
    assert result.malformed == 0


def test_known_closed_status_excludes_even_with_match_and_source():
    """A readable non-open status stays a hard exclusion on the rule path,
    whatever the rule and source assignment say."""
    mappings = [_legacy_root_mapping(), _status_mapping()]
    record = _rules(_custom_rule())

    result = _adapter(mappings, record).items_for_day_from_objects(
        DAY,
        [
            _custom_object(object_id="obj-open"),
            _custom_object(state="done", object_id="obj-done"),
        ],
    )

    assert [row["capacities_id"] for row in result.items] == ["obj-open"]


# ---------------------------------------------------------------------------
# The new metadata is scoped to the active-rule path
# ---------------------------------------------------------------------------

def test_legacy_rows_carry_no_rule_path_state_metadata():
    """Without an active rule the legacy row shape is unchanged."""
    mappings = [_legacy_root_mapping(), _status_mapping()]
    result = _adapter(mappings, None).items_for_day_from_objects(
        DAY, [_custom_object()]
    )

    assert len(result.items) == 1
    row = result.items[0]
    assert row["capacities_assignment"]["source_assigned"] is True
    assert "capacities_rule" not in row
    assert "capacities_status_state" not in row
    assert "capacities_completion_state" not in row
