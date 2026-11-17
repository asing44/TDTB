"""Page-body child hiding for Capacities Tasks (test-first).

The owner's model: a Capacities Task embedded in the page body of a parent
object that is itself admitted on this refresh is *not* a separate plan row —
the parent is the assigned unit. These tests pin the five behaviours that
define it:

* membership is an ``EntityBlock`` embed at any depth (top level, a
  ``GridBlock``'s ``columns``, a nested toggle's ``blocks``), while an inline
  ``LinkToken`` mention is a reference and hides nothing;
* a child is hidden only while its parent is admitted on the same read, and it
  comes straight back when the parent is not admitted;
* an owner selection is promoted from the unassigned surface, so an unassigned
  embedded child is never hidden;
* an unread page body hides nothing and reports one bounded warning;
* the hide is a reported drop with the new reason code, naming the parent.
"""
from __future__ import annotations

from datetime import date
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import capacities_adapter as ca  # noqa: E402
import capacities_embedded as ce  # noqa: E402

DAY = date(2026, 11, 20)
SPACE = "space-1"
TASKS = "RootTask"
PROJECTS = "custom-project"

PARENT_ID = "73372e1a-0b2f-40ad-9f0d-fcae292fa265"
CHILD_ID = "85803b64-d85a-4fac-99b3-f0b214641e46"


def _prop(kind: str, key: str, value):
    return {"type": kind, key: value}


def _definition(prop_id: str, kind: str, *, labels=None):
    row = {"id": prop_id, "name": prop_id.title(), "type": kind, "writable": True}
    if labels is not None:
        row["labelSet"] = [{"id": key, "name": name} for key, name in labels]
    return row


def _structures():
    return [
        {
            "id": TASKS,
            "title": "Task",
            "propertyDefinitions": [
                _definition("title", "title"),
                _definition("assigned", "boolean"),
                _definition("status", "label", labels=[("open", "Open"), ("done", "Done")]),
            ],
        },
        {
            "id": PROJECTS,
            "title": "Project",
            "propertyDefinitions": [
                _definition("title", "title"),
                _definition("tdtb", "boolean"),
                _definition("state", "label", labels=[("active", "Active"), ("done", "Done")]),
            ],
        },
    ]


def _mappings():
    return (
        ca.StructureMapping(
            structure_id=TASKS,
            assignment_property="assigned",
            assignment_values=frozenset({"true"}),
            open_status_property="status",
            open_status_values=frozenset({"open"}),
        ),
        ca.StructureMapping(
            structure_id=PROJECTS,
            assignment_property="tdtb",
            assignment_values=frozenset({"true"}),
            open_status_property="state",
            open_status_values=frozenset({"active"}),
        ),
    )


class FakeProvider:
    def __init__(self):
        self.structures = _structures()

    def fetch_structures(self):
        return self.structures

    def list_objects(self, structure_id, cursor=None):
        return {"objects": [], "next_cursor": None}

    def get_object(self, object_id):  # pragma: no cover - not reached here
        raise AssertionError("the adapter must not read content here")

    def patch_object(self, object_id, properties):  # pragma: no cover
        raise AssertionError("no write is expected")


def _adapter():
    return ca.CapacitiesAdapter(
        FakeProvider(), ca.CapacitiesConfig(SPACE, _mappings())
    )


def _task(object_id: str, title: str, *, assigned: bool = True, status: str = "open"):
    return {
        "id": object_id,
        "spaceId": SPACE,
        "structureId": TASKS,
        "properties": {
            "title": _prop("title", "title", {"value": title}),
            "assigned": _prop("boolean", "boolean", assigned),
            "status": _prop("label", "label", [{"id": status, "name": status.title()}]),
        },
    }


def _project(object_id: str, title: str, *, assigned: bool = True, blocks=None):
    obj = {
        "id": object_id,
        "spaceId": SPACE,
        "structureId": PROJECTS,
        "properties": {
            "title": _prop("title", "title", {"value": title}),
            "tdtb": _prop("boolean", "boolean", assigned),
            "state": _prop("label", "label", [{"id": "active", "name": "Active"}]),
        },
    }
    if blocks is not None:
        obj["blocks"] = blocks
    return obj


# ---------------------------------------------------------------------------
# EntityBlock collection at any depth
# ---------------------------------------------------------------------------


def test_top_level_entity_blocks_are_membership():
    body = {"body": [{"type": "EntityBlock", "entityId": "a"}]}
    assert ce.embedded_entity_ids(body) == ("a",)


def test_grid_columns_and_nested_blocks_are_membership():
    body = {
        "body": [
            {
                "type": "GridBlock",
                "columns": [
                    [{"type": "EntityBlock", "entityId": "a"}],
                    [
                        {
                            "type": "TextBlock",
                            "tokens": [{"type": "text", "text": "toggle"}],
                            "blocks": [
                                {
                                    "type": "GroupBlock",
                                    "blocks": [
                                        {"type": "EntityBlock", "entityId": "b"}
                                    ],
                                }
                            ],
                        }
                    ],
                ],
            }
        ]
    }
    assert ce.embedded_entity_ids(body) == ("a", "b")


def test_link_tokens_are_references_not_membership():
    body = {
        "body": [
            {
                "type": "TextBlock",
                "tokens": [
                    {"type": "text", "text": "see "},
                    {"type": "LinkToken", "entityId": "mentioned-task"},
                ],
            },
            {"type": "EntityBlock", "entityId": "embedded-task"},
        ]
    }
    assert ce.embedded_entity_ids(body) == ("embedded-task",)


def test_non_mapping_and_absent_bodies_contribute_nothing():
    assert ce.embedded_entity_ids(None) == ()
    assert ce.embedded_entity_ids("text") == ()
    assert ce.embedded_child_map([{"id": "p"}]) == {}


def test_embedded_child_map_keeps_every_parent_once_in_read_order():
    embed = {"b": [{"type": "EntityBlock", "entityId": CHILD_ID},
                   {"type": "EntityBlock", "entityId": CHILD_ID}]}
    objects = [
        {"id": PARENT_ID, "title": "Garage Buildout", "blocks": embed},
        {"id": "other", "title": "Other", "blocks": embed},
    ]
    assert ce.embedded_child_map(objects) == {
        CHILD_ID: [(PARENT_ID, "Garage Buildout"), ("other", "Other")]
    }


# ---------------------------------------------------------------------------
# Admission: hidden with an admitted parent, shown without one
# ---------------------------------------------------------------------------


def test_child_embedded_in_an_admitted_parent_is_hidden_and_reported():
    adapter = _adapter()
    result = adapter.items_for_day_from_objects(
        DAY,
        [
            _project(PARENT_ID, "Garage Buildout", blocks={"body": [{"type": "EntityBlock", "entityId": CHILD_ID}]}),
            _task(CHILD_ID, "Above pegboard shrlvings of decor Garage Buildout"),
        ],
    )
    assert [row["name"] for row in result.items] == ["Garage Buildout"]
    assert result.drop_tally[TASKS][ca.DROP_REASON_EMBEDDED_IN_PARENT] == 1
    hidden_lines = [
        line for line in result.warnings if ca.DROP_REASON_EMBEDDED_IN_PARENT in line and "hidden" in line
    ]
    assert len(hidden_lines) == 1
    assert PARENT_ID in hidden_lines[0]
    assert "Garage Buildout" in hidden_lines[0]


def test_child_whose_parent_is_not_admitted_stays_visible():
    adapter = _adapter()
    result = adapter.items_for_day_from_objects(
        DAY,
        [
            _project(PARENT_ID, "Garage Buildout", assigned=False, blocks={"body": [{"type": "EntityBlock", "entityId": CHILD_ID}]}),
            _task(CHILD_ID, "Orphaned child"),
        ],
    )
    # The parent is not admitted on this read (its status gate closed), so its
    # embedded task is admitted exactly as today.
    assert sorted(row["name"] for row in result.items) == ["Orphaned child"]
    assert ca.DROP_REASON_EMBEDDED_IN_PARENT not in result.drop_tally.get(TASKS, {})


def test_child_in_two_parents_is_hidden_when_the_later_one_is_admitted():
    adapter = _adapter()
    embed = {"body": [{"type": "EntityBlock", "entityId": CHILD_ID}]}
    result = adapter.items_for_day_from_objects(
        DAY,
        [
            _project("idle-parent", "Idle Project", assigned=False, blocks=embed),
            _project(PARENT_ID, "Garage Buildout", blocks=embed),
            _task(CHILD_ID, "Shared child"),
        ],
    )
    assert [row["name"] for row in result.items] == ["Garage Buildout"]
    hidden = [
        line for line in result.warnings
        if ca.DROP_REASON_EMBEDDED_IN_PARENT in line and "hidden" in line
    ]
    assert len(hidden) == 1
    assert PARENT_ID in hidden[0] and "idle-parent" not in hidden[0]


def test_task_not_embedded_anywhere_is_untouched():
    adapter = _adapter()
    result = adapter.items_for_day_from_objects(
        DAY,
        [
            _project(PARENT_ID, "Garage Buildout", blocks={"body": [{"type": "EntityBlock", "entityId": "another"}]}),
            _task(CHILD_ID, "Unrelated task"),
        ],
    )
    assert sorted(row["name"] for row in result.items) == [
        "Garage Buildout",
        "Unrelated task",
    ]


def test_only_an_entity_block_mention_does_not_hide_a_task():
    adapter = _adapter()
    result = adapter.items_for_day_from_objects(
        DAY,
        [
            _project(
                PARENT_ID,
                "Garage Buildout",
                blocks={
                    "body": [
                        {
                            "type": "TextBlock",
                            "tokens": [{"type": "LinkToken", "entityId": CHILD_ID}],
                        }
                    ]
                },
            ),
            _task(CHILD_ID, "Mentioned task"),
        ],
    )
    assert sorted(row["name"] for row in result.items) == [
        "Garage Buildout",
        "Mentioned task",
    ]


def test_owner_selected_child_is_never_hidden():
    # A selection promotes a row from the UNASSIGNED surface (main.py's
    # resolve_selections), so an embedded child the source does not assign stays
    # a candidate and is never hidden: the hide stage removes only rows that are
    # assigned on this read, and an explicit owner pick therefore outranks it.
    adapter = _adapter()
    objects = [
        _project(PARENT_ID, "Garage Buildout", blocks={"body": [{"type": "EntityBlock", "entityId": CHILD_ID}]}),
        _task(CHILD_ID, "Child the owner picked", assigned=False),
    ]
    assigned_child = {
        "name": "Assigned child",
        "assigned": True,
        "capacities_id": CHILD_ID,
        "capacities_structure_id": TASKS,
    }
    picked_child = {
        "name": "Child the owner picked",
        "assigned": False,
        "capacities_id": CHILD_ID,
        "capacities_structure_id": TASKS,
    }
    parent = {
        "name": "Garage Buildout",
        "assigned": True,
        "capacities_id": PARENT_ID,
        "capacities_structure_id": PROJECTS,
    }
    kept, tally = adapter._hide_embedded_children(
        [parent, picked_child], objects
    )
    assert [row["name"] for row in kept] == ["Garage Buildout", "Child the owner picked"]
    assert tally == {}

    kept, tally = adapter._hide_embedded_children([parent, assigned_child], objects)
    assert [row["name"] for row in kept] == ["Garage Buildout"]
    assert tally == {(TASKS, PARENT_ID, "Garage Buildout"): 1}


# ---------------------------------------------------------------------------
# Fail open
# ---------------------------------------------------------------------------


class _BrokenReader:
    def __call__(self, object_id):
        raise RuntimeError("body read failed")


def test_a_failed_body_read_hides_nothing_and_warns():
    blocks, warning = ce.read_blocks(_BrokenReader(), CHILD_ID)
    assert blocks is None
    assert warning == ce.BLOCKS_READ_WARNING


def test_a_body_read_without_a_blocks_mapping_fails_open():
    blocks, warning = ce.read_blocks(lambda object_id: {"id": object_id}, CHILD_ID)
    assert blocks is None
    assert warning == ce.BLOCKS_READ_WARNING


def test_no_reader_is_not_a_failure():
    assert ce.read_blocks(None, CHILD_ID) == (None, None)


def test_a_usable_body_read_returns_the_body():
    body = {"body": [{"type": "EntityBlock", "entityId": CHILD_ID}]}
    assert ce.read_blocks(lambda object_id: {"blocks": body}, PARENT_ID) == (body, None)


def test_a_parent_without_a_body_hides_nothing():
    adapter = _adapter()
    result = adapter.items_for_day_from_objects(
        DAY,
        [
            _project(PARENT_ID, "Garage Buildout"),
            _task(CHILD_ID, "Task with no readable parent body"),
        ],
    )
    assert sorted(row["name"] for row in result.items) == [
        "Garage Buildout",
        "Task with no readable parent body",
    ]
    assert ca.DROP_REASON_EMBEDDED_IN_PARENT not in result.drop_tally.get(TASKS, {})
