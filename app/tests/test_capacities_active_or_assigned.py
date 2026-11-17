"""The union admission mode ``activeOrAssigned`` (test-first).

The owner curates assignment in Capacities, so a type may admit on EITHER of
two signals: the open status NAME the declaration names, or its declared
boolean checkbox reading ``true``. That union is one decision, never a sum:

* open status + unchecked admits;
* not-open status + checked admits — a readable label that is merely not open
  (``Background``) is not completed evidence, so the checked box still wins;
* not-open status + unchecked is dropped with the same reason it is dropped for
  a status-only type today (``closed-source-status``);
* open status + checked admits once, on one row;
* a declared property the live schema does not define degrades to status-only
  behaviour with one bounded warning and never drops the open rows;
* the owner's completed vocabulary stays a hard exclusion.

Settings validation, round-trip, and the adapter's enumeration gate are pinned
here too, including the end-to-end page-body child hiding that depends on the
admitted set.
"""
from __future__ import annotations

from datetime import date
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import capacities_adapter as ca  # noqa: E402
import capacities_assignment as cas  # noqa: E402
import capacities_settings as cset  # noqa: E402

DAY = date(2026, 11, 20)
SPACE = "space-owner"
PROJECTS = "0d194525-c5a1-4af5-bb62-202b83006b5e"  # Project
ROOT_TASK = "RootTask"
ASSIGNED_PROP = "6cdb0e7b-62fd-492f-beef-bbf867a3f475"
STATUS_PROP = "f240c060-5da1-45b9-a4cc-4b9b3bb51c72"
PARENT_ID = "73372e1a-0b2f-40ad-9f0d-fcae292fa265"  # Garage Buildout
CHILD_ID = "85803b64-d85a-4fac-99b3-f0b214641e46"

MODE = cset.CAPACITIES_MODE_ACTIVE_OR_ASSIGNED


def _identity(structure_id: str = PROJECTS, object_id: str = "obj-1") -> str:
    return f"capacities:{SPACE}:{structure_id}:{object_id}"


def _settings(
    *,
    mode: str = MODE,
    property_id: str | None = ASSIGNED_PROP,
    statuses=("In Progress",),
):
    declaration: dict[str, object] = {"mode": mode, "property": property_id}
    if statuses is not None:
        declaration["statuses"] = list(statuses)
    return cset.CapacitiesSettings(
        statuses=cset.StatusVocabulary(
            open=frozenset({"In Progress"}),
            completed=frozenset({"Completed", "Dropped"}),
        ),
        types={
            PROJECTS: declaration,
            ROOT_TASK: {"mode": "native", "property": None, "statuses": ["Open"]},
        },
    )


# ---------------------------------------------------------------------------
# Evaluator: the union decides once
# ---------------------------------------------------------------------------


def test_in_progress_with_unchecked_box_is_admitted_on_status():
    settings = cas.AssignmentSettings.from_v2(_settings())
    decision = cas.evaluate_assignment(
        cas.AssignmentCandidate(
            identity=_identity(),
            status="In Progress",
            assigned_property=False,
        ),
        logical_day=DAY,
        settings=settings,
    )
    assert decision.eligible is True
    assert decision.provenance.auto_conditions == ("status-active",)


def test_background_with_checked_box_is_admitted_on_the_property():
    settings = cas.AssignmentSettings.from_v2(_settings())
    decision = cas.evaluate_assignment(
        cas.AssignmentCandidate(
            identity=_identity(),
            status="Background",
            assigned_property=True,
        ),
        logical_day=DAY,
        settings=settings,
    )
    assert decision.eligible is True
    assert decision.provenance.auto_conditions == ("assigned-property",)


def test_background_with_unchecked_box_is_not_admitted():
    settings = cas.AssignmentSettings.from_v2(_settings())
    decision = cas.evaluate_assignment(
        cas.AssignmentCandidate(
            identity=_identity(),
            status="Background",
            assigned_property=False,
        ),
        logical_day=DAY,
        settings=settings,
    )
    assert decision.eligible is False
    assert decision.reason_codes == ("no-effective-assignment",)


def test_in_progress_with_checked_box_is_admitted_once():
    settings = cas.AssignmentSettings.from_v2(_settings())
    decision = cas.evaluate_assignment(
        cas.AssignmentCandidate(
            identity=_identity(),
            status="In Progress",
            assigned_property=True,
        ),
        logical_day=DAY,
        settings=settings,
    )
    assert decision.eligible is True
    # One decision, both union members reported once, in deterministic order.
    assert decision.provenance.auto_conditions == ("status-active", "assigned-property")
    assert decision.mode is cas.AssignmentMode.AUTO


def test_missing_status_vocabulary_still_admits_on_the_property():
    settings = cas.AssignmentSettings.from_v2(_settings(statuses=["In Progress"]))
    decision = cas.evaluate_assignment(
        cas.AssignmentCandidate(
            identity=_identity(),
            status=None,
            assigned_property=True,
        ),
        logical_day=DAY,
        settings=settings,
    )
    assert decision.eligible is True


# ---------------------------------------------------------------------------
# Settings validation and round-trip
# ---------------------------------------------------------------------------


def test_settings_accept_the_union_mode():
    settings = _settings()
    declaration = settings.types[PROJECTS]
    assert declaration.mode == MODE
    assert declaration.requires_property is True
    assert declaration.uses_status is True
    assert declaration.status_admission_enabled is True
    assert declaration.open_statuses() == frozenset({"In Progress"})


@pytest.mark.parametrize("statuses", [None, ()])
def test_type_declaration_rejects_the_union_mode_without_statuses(statuses):
    with pytest.raises(ValueError, match="non-empty statuses"):
        cset.TypeDeclaration(
            mode=MODE, property_id=ASSIGNED_PROP, statuses=statuses
        )


def test_type_declaration_rejects_the_union_mode_without_a_property():
    with pytest.raises(ValueError, match="requires a non-empty raw property id"):
        cset.TypeDeclaration(mode=MODE, property_id=None, statuses=("In Progress",))


def _document(**declaration_overrides):
    declaration = {
        "mode": MODE,
        "property": ASSIGNED_PROP,
        "statuses": ["In Progress"],
        **declaration_overrides,
    }
    return {
        "version": cset.SCHEMA_VERSION,
        "revision": 7,
        "excluded": {},
        "native": {"deadline_horizon_days": 14},
        "read_source": "auto",
        "statuses": {
            "open": ["In Progress"],
            "completed": ["Completed", "Dropped"],
        },
        "types": {PROJECTS: declaration},
    }


def test_settings_document_round_trips_with_the_union_mode(tmp_path):
    document = _document()
    decoded = cset._decode(document)
    assert decoded.as_dict() == document


@pytest.mark.parametrize(
    "overrides",
    [
        {"statuses": None},
        {"statuses": []},
        {"property": None},
    ],
)
def test_settings_document_rejects_incomplete_union_declarations(overrides):
    with pytest.raises(cset.SettingsFormatError):
        cset._decode(_document(**overrides))


# ---------------------------------------------------------------------------
# Adapter end-to-end
# ---------------------------------------------------------------------------


def _prop(kind: str, key: str, value):
    return {"type": kind, key: value}


def _definition(prop_id: str, kind: str, *, labels=None, name=None):
    row = {
        "id": prop_id,
        "name": name or prop_id.title(),
        "type": kind,
        "writable": True,
    }
    if labels is not None:
        row["labelSet"] = [{"id": key, "name": label} for key, label in labels]
    return row


def _structures():
    return [
        {
            "id": PROJECTS,
            "title": "Project",
            "propertyDefinitions": [
                _definition("title", "title"),
                _definition(ASSIGNED_PROP, "boolean"),
                _definition(
                    STATUS_PROP,
                    "label",
                    labels=[
                        ("in-progress", "In Progress"),
                        ("background", "Background"),
                        ("completed", "Completed"),
                    ],
                ),
            ],
        },
        {
            "id": ROOT_TASK,
            "title": "Task",
            "propertyDefinitions": [
                _definition("title", "title"),
                _definition("assigned", "boolean"),
                _definition(
                    "status",
                    "label",
                    labels=[("open", "Open"), ("done", "Done")],
                ),
            ],
        },
    ]


def _mappings(*, assigned_property: str | None = ASSIGNED_PROP):
    return (
        ca.StructureMapping(
            structure_id=PROJECTS,
            assigned_property=assigned_property,
            open_status_property=STATUS_PROP,
        ),
        ca.StructureMapping(
            structure_id=ROOT_TASK,
            assignment_property="assigned",
            assignment_values=frozenset({"true"}),
            open_status_property="status",
        ),
    )


class FakeProvider:
    def __init__(self):
        self.structures = _structures()

    def fetch_structures(self):
        return self.structures

    def list_objects(self, structure_id, cursor=None):  # pragma: no cover
        return {"objects": [], "next_cursor": None}

    def get_object(self, object_id):  # pragma: no cover
        raise AssertionError("the adapter must not read content here")

    def patch_object(self, object_id, properties):  # pragma: no cover
        raise AssertionError("no write is expected")


def _read(objects, *, settings=None, assigned_property: str | None = ASSIGNED_PROP):
    settings = settings if settings is not None else _settings()
    adapter = ca.CapacitiesAdapter(
        FakeProvider(),
        ca.CapacitiesConfig(
            space_id=SPACE,
            mappings=_mappings(assigned_property=assigned_property),
            assignment_settings=cas.AssignmentSettings.from_v2(settings),
            settings=settings,
        ),
    )
    return adapter.items_for_day_from_objects(DAY, objects)


def _project(object_id: str, title: str, *, assigned: bool, status: str, blocks=None):
    obj = {
        "id": object_id,
        "spaceId": SPACE,
        "structureId": PROJECTS,
        "properties": {
            "title": _prop("title", "title", {"value": title}),
            ASSIGNED_PROP: _prop("boolean", "boolean", assigned),
            STATUS_PROP: _prop(
                "label", "label", [{"id": status.lower(), "name": status}]
            ),
        },
    }
    if blocks is not None:
        obj["blocks"] = blocks
    return obj


def _task(object_id: str, title: str):
    return {
        "id": object_id,
        "spaceId": SPACE,
        "structureId": ROOT_TASK,
        "properties": {
            "title": _prop("title", "title", {"value": title}),
            "assigned": _prop("boolean", "boolean", True),
            "status": _prop("label", "label", [{"id": "open", "name": "Open"}]),
        },
    }


def test_adapter_admits_a_background_project_with_the_box_checked():
    result = _read(
        [_project(PARENT_ID, "Garage Buildout", assigned=True, status="Background")]
    )
    assert [row["name"] for row in result.items] == ["Garage Buildout"]
    assert ca.DROP_REASON_CLOSED_STATUS not in result.drop_tally.get(PROJECTS, {})


def test_adapter_admits_an_in_progress_project_with_the_box_unchecked():
    result = _read(
        [_project(PARENT_ID, "Garage Buildout", assigned=False, status="In Progress")]
    )
    assert [row["name"] for row in result.items] == ["Garage Buildout"]


def test_adapter_admits_an_in_progress_project_with_the_box_checked_once():
    result = _read(
        [_project(PARENT_ID, "Garage Buildout", assigned=True, status="In Progress")]
    )
    assert [row["name"] for row in result.items] == ["Garage Buildout"]


def test_adapter_drops_a_background_project_with_the_box_unchecked_as_today():
    result = _read(
        [_project(PARENT_ID, "Garage Buildout", assigned=False, status="Background")]
    )
    assert [row["name"] for row in result.items] == []
    # Exactly today's status-only drop reason, not a new one.
    assert result.drop_tally[PROJECTS][ca.DROP_REASON_CLOSED_STATUS] == 1


def test_adapter_completed_status_is_still_closed_even_when_checked():
    result = _read(
        [_project(PARENT_ID, "Garage Buildout", assigned=True, status="Completed")]
    )
    assert [row["name"] for row in result.items] == []
    assert result.drop_tally[PROJECTS][ca.DROP_REASON_CLOSED_STATUS] == 1


def test_adapter_unknown_property_degrades_to_status_only_with_one_warning():
    result = _read(
        [
            _project(
                PARENT_ID, "Garage Buildout", assigned=True, status="In Progress"
            )
        ],
        assigned_property="missing-assigned-property",
    )
    assert [row["name"] for row in result.items] == ["Garage Buildout"]
    warnings = [line for line in result.warnings if "unknown property" in line]
    assert len(warnings) == 1
    assert PROJECTS in warnings[0]


def test_adapter_hides_the_child_of_a_project_admitted_by_the_checkbox():
    embed = {"body": [{"type": "EntityBlock", "entityId": CHILD_ID}]}
    result = _read(
        [
            _project(
                PARENT_ID,
                "Garage Buildout",
                assigned=True,
                status="Background",
                blocks=embed,
            ),
            _task(CHILD_ID, "Above pegboard shelves"),
        ]
    )
    assert [row["name"] for row in result.items] == ["Garage Buildout"]
    assert result.drop_tally[ROOT_TASK][ca.DROP_REASON_EMBEDDED_IN_PARENT] == 1


def test_adapter_keeps_the_child_visible_when_the_project_is_not_admitted():
    embed = {"body": [{"type": "EntityBlock", "entityId": CHILD_ID}]}
    result = _read(
        [
            _project(
                PARENT_ID,
                "Garage Buildout",
                assigned=False,
                status="Background",
                blocks=embed,
            ),
            _task(CHILD_ID, "Above pegboard shelves"),
        ]
    )
    assert [row["name"] for row in result.items] == ["Above pegboard shelves"]
    assert ca.DROP_REASON_EMBEDDED_IN_PARENT not in result.drop_tally.get(ROOT_TASK, {})
