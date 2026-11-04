"""``assigned``/``assignedOn`` types honour the owner's completed statuses.

Live evidence: Print objects carry ``Assigned = true`` and status ``Printed``,
Purchase objects carry ``Purchased``, and both appeared in the plan even though
the owner's own Capacities query excludes those statuses per type. The version-2
``statuses.completed`` block is the shared closed vocabulary, so an
``assigned``/``assignedOn`` row whose status NAME is listed there is closed
source truth and is dropped through the existing ``closed-source-status`` tally
path, even though its Assigned boolean is true.

The rule is name-based and never turns the mode into a status-admission test:
an absent, empty, or unreadable status stays NOT closed, and a status that is
neither completed nor in any open list stays admitted. ``active``/``native``/
``off`` modes are unchanged; those live on the open-vocabulary resolver.
"""
from __future__ import annotations

from datetime import date
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from capacities_adapter import (  # noqa: E402
    CapacitiesAdapter,
    CapacitiesConfig,
    StructureMapping,
)
from capacities_assignment import AssignmentSettings  # noqa: E402
import capacities_settings as cset  # noqa: E402


SPACE = "space-assigned"
TODAY = date(2026, 11, 4)

PRINT = "print-structure"
PURCHASE = "purchase-structure"

#: Shared status option set for both structures: the ids deliberately differ
#: from the names so every case proves the comparison is name-based.
STATUS_OPTIONS = (
    ("id-printing", "Printing"),
    ("id-printed", "Printed"),
    ("id-completed", "Completed"),
    ("id-dropped", "Dropped"),
    ("id-researching", "Researching"),
    ("id-purchased", "Purchased"),
)


def _definition(prop_id: str, kind: str, *, labels=None):
    row = {
        "id": prop_id,
        "name": prop_id.title(),
        "type": kind,
        "writable": True,
    }
    if labels is not None:
        row["labelSet"] = [{"id": key, "name": name} for key, name in labels]
    return row


ROOT_TASK = "RootTask"


def _structures():
    native = {
        "id": ROOT_TASK,
        "title": "Task",
        "propertyDefinitions": [
            _definition("title", "title"),
            _definition("status", "label", labels=STATUS_OPTIONS),
        ],
    }
    return [native] + [
        {
            "id": structure_id,
            "title": structure_id.title(),
            "propertyDefinitions": [
                _definition("title", "title"),
                _definition("assigned", "boolean"),
                _definition("assigned-on", "date"),
                _definition("status", "label", labels=STATUS_OPTIONS),
            ],
        }
        for structure_id in (PRINT, PURCHASE)
    ]


class _Provider:
    """Read-only provider: the projection never writes during a day read."""

    def __init__(self, structures, objects):
        self._structures = structures
        self._objects = list(objects)

    def fetch_structures(self):
        return self._structures

    def list_objects(self, structure_id, cursor=None):
        rows = [row for row in self._objects if row["structureId"] == structure_id]
        return {"objects": rows, "next_cursor": None}

    def get_object(self, object_id):
        for row in self._objects:
            if row["id"] == object_id:
                return row
        raise KeyError(object_id)

    def patch_object(self, object_id, properties):
        raise AssertionError("a day read must never write to the source")


def _member(name: str, *, option_id: str | None = None) -> dict:
    return {"id": option_id or f"id-{name.casefold()}", "name": name}


def _object(
    object_id: str,
    structure_id: str = PRINT,
    *,
    status: object = "Printed",
    assigned: bool = True,
    assigned_on: dict | None = None,
) -> dict:
    properties: dict[str, object] = {
        "title": {"type": "title", "title": {"value": object_id}},
        "assigned": {"type": "boolean", "boolean": assigned},
        "status": {"type": "label", "label": [_member(status)]}
        if isinstance(status, str)
        else status,
    }
    if assigned_on is not None:
        properties["assigned-on"] = {"type": "date", "date": assigned_on}
    return {
        "id": object_id,
        "spaceId": SPACE,
        "structureId": structure_id,
        "properties": properties,
    }


def _mapping(structure_id: str = PRINT, mode: str = "assigned") -> StructureMapping:
    """The builder-resolved mapping a declaration produces."""
    if mode == "assignedOn":
        return StructureMapping(
            structure_id=structure_id,
            assigned_on_property="assigned-on",
            open_status_property="status",
        )
    return StructureMapping(
        structure_id=structure_id,
        assigned_property="assigned",
        open_status_property="status",
    )


def _settings_document(
    structure_id: str = PRINT,
    *,
    mode: str = "assigned",
    completed=("Completed", "Dropped"),
    statuses=None,
) -> cset.CapacitiesSettings:
    declaration: dict[str, object] = {
        "mode": mode,
        "property": "assigned-on" if mode == "assignedOn" else "assigned",
    }
    if statuses is not None:
        declaration["statuses"] = list(statuses)
    return cset.CapacitiesSettings(
        statuses=cset.StatusVocabulary(
            open=frozenset({"In Progress"}), completed=frozenset(completed)
        ),
        types={structure_id: declaration},
    )


def _read(
    objects,
    *,
    structure_id: str = PRINT,
    mode: str = "assigned",
    completed=("Completed", "Dropped"),
    statuses=None,
):
    settings = _settings_document(
        structure_id, mode=mode, completed=completed, statuses=statuses
    )
    adapter = CapacitiesAdapter(
        _Provider(_structures(), objects),
        CapacitiesConfig(
            space_id=SPACE,
            mappings=(
                # The canonical native structure must be explicitly mapped;
                # it is undeclared here, so it contributes nothing.
                StructureMapping(structure_id=ROOT_TASK, open_status_property="status"),
                _mapping(structure_id, mode),
            ),
            assignment_settings=AssignmentSettings.from_v2(settings),
            settings=settings,
        ),
    )
    return adapter.items_for_day(TODAY)


# ---------------------------------------------------------------------------
# The owner's closed vocabulary decides an assigned-mode row
# ---------------------------------------------------------------------------


def test_printed_with_assigned_boolean_is_closed_when_owner_lists_printed():
    result = _read([_object("Spider PRANK")], completed=("Completed", "Dropped", "Printed"))

    assert result.items == []
    assert result.drop_tally == {PRINT: {"closed-source-status": 1}}
    assert result.review_tally == {}


def test_printed_with_assigned_boolean_is_admitted_when_list_lacks_printed():
    result = _read([_object("Spider PRANK")], completed=("Completed", "Dropped"))

    assert [row["capacities_id"] for row in result.items] == ["Spider PRANK"]
    assert result.items[0]["assigned"] is True
    assert result.drop_tally == {}
    assert result.review_tally == {}


def test_purchased_with_assigned_boolean_is_closed_when_owner_lists_purchased():
    result = _read(
        [_object("Pelet Shitbox", PURCHASE, status="Purchased")],
        structure_id=PURCHASE,
        completed=("Purchased", "Dropped"),
    )

    assert result.items == []
    assert result.drop_tally == {PURCHASE: {"closed-source-status": 1}}


def test_purchased_with_assigned_boolean_is_admitted_when_list_lacks_purchased():
    result = _read(
        [_object("Pelet Shitbox", PURCHASE, status="Purchased")],
        structure_id=PURCHASE,
        completed=("Completed", "Dropped"),
    )

    assert [row["capacities_id"] for row in result.items] == ["Pelet Shitbox"]
    assert result.drop_tally == {}


def test_researching_status_stays_admitted():
    # Assigned mode must not become status-gated by the open list: a status
    # that is neither completed nor open is still admitted by the boolean.
    result = _read(
        [_object("Pelet Shitbox", PURCHASE, status="Researching")],
        structure_id=PURCHASE,
        completed=("Completed", "Dropped", "Purchased"),
        statuses=("In Progress",),
    )

    assert [row["capacities_id"] for row in result.items] == ["Pelet Shitbox"]
    assert result.items[0]["assigned"] is True
    assert result.drop_tally == {}


@pytest.mark.parametrize(
    "status_payload",
    [
        pytest.param({"type": "label", "label": []}, id="empty-label-payload"),
        pytest.param({"type": "label", "label": "Printed"}, id="unreadable-payload"),
        pytest.param({"type": "label", "label": [{"id": None, "name": None}]}, id="null-member"),
    ],
)
def test_absent_empty_or_unreadable_status_is_never_closed(status_payload):
    result = _read(
        [_object("no-status", status=status_payload)],
        completed=("Printed", "Completed", "Dropped"),
    )

    assert [row["capacities_id"] for row in result.items] == ["no-status"]
    assert result.items[0]["assigned"] is True
    assert result.drop_tally == {}


def test_missing_status_property_is_never_closed():
    obj = _object("no-property")
    del obj["properties"]["status"]

    result = _read([obj], completed=("Printed", "Completed", "Dropped"))

    assert [row["capacities_id"] for row in result.items] == ["no-property"]
    assert result.drop_tally == {}


def test_assigned_on_mode_honours_a_completed_status():
    result = _read(
        [
            _object(
                "ghost-tealight",
                status="Printed",
                assigned=False,
                assigned_on={"start": TODAY.isoformat()},
            )
        ],
        mode="assignedOn",
        completed=("Printed",),
    )

    assert result.items == []
    assert result.drop_tally == {PRINT: {"closed-source-status": 1}}


def test_assigned_on_mode_admits_a_non_completed_status():
    result = _read(
        [
            _object(
                "ghost-tealight",
                status="Printing",
                assigned=False,
                assigned_on={"start": TODAY.isoformat()},
            )
        ],
        mode="assignedOn",
        completed=("Printed",),
    )

    assert [row["capacities_id"] for row in result.items] == ["ghost-tealight"]
    assert result.drop_tally == {}


@pytest.mark.parametrize(
    "name",
    ["Printed", "printed", "PRINTED", "  pRiNtEd  ", "printed\t"],
)
def test_completed_status_matching_is_case_and_whitespace_insensitive(name):
    result = _read(
        [_object("variant", status=name)],
        completed=(" Printed ",),
    )

    assert result.items == []
    assert result.drop_tally == {PRINT: {"closed-source-status": 1}}


def test_completed_status_is_decided_without_per_type_open_sets():
    # ``statuses: []`` disables status admission for the type; the closed
    # vocabulary is the shared one, so a completed name still drops the row.
    result = _read(
        [_object("printed-row")],
        completed=("Printed",),
        statuses=(),
    )

    assert result.items == []
    assert result.drop_tally == {PRINT: {"closed-source-status": 1}}


def test_tally_counts_only_the_completed_rows_in_a_mixed_read():
    result = _read(
        [
            _object("printed-row", status="Printed"),
            _object("printing-row", status="Printing"),
        ],
        completed=("Printed",),
    )

    assert [row["capacities_id"] for row in result.items] == ["printing-row"]
    assert result.drop_tally == {PRINT: {"closed-source-status": 1}}
    assert any("closed-source-status" in line for line in result.warnings)
