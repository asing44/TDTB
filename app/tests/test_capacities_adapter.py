"""Contract tests for the application-owned Capacities source adapter."""
from __future__ import annotations

from datetime import date, timedelta
import sys
from pathlib import Path

import pytest

import httpx

sys.path.insert(0, str(Path(__file__).parent.parent))

from capacities_adapter import (  # noqa: E402
    CapacitiesAdapter,
    CapacitiesConfig,
    CapacitiesContractError,
    CapacitiesRateLimited,
    CapacitiesWriteError,
    CapacitiesRestClient,
    StructureMapping,
)
from capacities_assignment import AssignmentSettings  # noqa: E402


SPACE = "space-1"
TODAY = date(2026, 9, 29)


def _prop(kind: str, key: str, value):
    return {"type": kind, key: value}


def _definition(prop_id: str, kind: str, *, writable: bool = True, labels=None):
    row = {
        "id": prop_id,
        "name": prop_id.title(),
        "type": kind,
        "writable": writable,
    }
    if labels is not None:
        row["labelSet"] = [{"id": key, "name": name} for key, name in labels]
    return row


def _structures():
    return [
        {
            "id": "RootTask",
            "title": "Task",
            "propertyDefinitions": [
                _definition("title", "title"),
                _definition("assigned", "boolean"),
                _definition("due", "date"),
                _definition("duration", "number"),
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
                _definition("tdtb", "label", labels=[("yes", "TDTB"), ("no", "No")]),
                _definition("date", "date"),
                _definition("minutes", "number"),
                _definition(
                    "state",
                    "label",
                    labels=[("active", "Active"), ("done", "Done")],
                ),
            ],
        },
    ]


def _object(object_id: str, structure_id: str, properties: dict, *, space=SPACE):
    return {
        "id": object_id,
        "spaceId": space,
        "structureId": structure_id,
        "properties": properties,
    }


def _mapping():
    return (
        StructureMapping(
            structure_id="RootTask",
            assignment_property="assigned",
            assignment_values=frozenset({"true"}),
            date_property="due",
            open_status_property="status",
            open_status_values=frozenset({"open"}),
            duration_property="duration",
            completion_property="status",
            completion_value="done",
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
    )


class FakeProvider:
    def __init__(self, structures, pages, objects=None):
        self._structures = structures
        self._pages = pages
        self._objects = dict(objects or {})
        self.list_calls = []
        self.patch_calls = []
        self.timeout_after_patch = False
        self.patch_exception = None

    def fetch_structures(self):
        return self._structures

    def list_objects(self, structure_id, cursor=None):
        self.list_calls.append((structure_id, cursor))
        return self._pages.get((structure_id, cursor), {"objects": [], "next_cursor": None})

    def get_object(self, object_id):
        return self._objects[object_id]

    def patch_object(self, object_id, properties):
        self.patch_calls.append((object_id, properties))
        self._objects[object_id] = {
            **self._objects[object_id],
            "properties": {
                **self._objects[object_id]["properties"],
                **properties,
            },
        }
        if self.patch_exception is not None:
            raise self.patch_exception
        if self.timeout_after_patch:
            raise TimeoutError("simulated timeout after provider accepted patch")
        return self._objects[object_id]


def _adapter(provider, mappings=None, **kwargs):
    return CapacitiesAdapter(
        provider,
        CapacitiesConfig(
            space_id=SPACE,
            mappings=tuple(mappings or _mapping()),
            **kwargs,
        ),
    )


def test_enumerates_allowlisted_structures_filters_typed_properties_and_projects_rows():
    provider = FakeProvider(
        _structures(),
        {
            ("RootTask", None): {
                "objects": [
                    _object(
                        "task-today",
                        "RootTask",
                        {
                            "title": _prop("title", "title", {"value": "Write brief"}),
                            "assigned": _prop("boolean", "boolean", True),
                            "due": _prop("date", "date", {
                                "dateResolution": "day",
                                "start": "2026-09-29T00:00:00.000Z",
                                "end": None,
                            }),
                            "duration": _prop("number", "number", {"value": 45}),
                            "status": _prop("label", "label", [{"id": "open", "name": "Open"}]),
                        },
                    ),
                    _object(
                        "task-future",
                        "RootTask",
                        {
                            "title": _prop("title", "title", {"value": "Tomorrow"}),
                            "assigned": _prop("boolean", "boolean", True),
                            "due": _prop("date", "date", {
                                "dateResolution": "day",
                                "start": "2026-09-30T00:00:00.000Z",
                            }),
                            "status": _prop("label", "label", [{"id": "open"}]),
                        },
                    ),
                    _object(
                        "task-done",
                        "RootTask",
                        {
                            "title": _prop("title", "title", {"value": "Done"}),
                            "assigned": _prop("boolean", "boolean", True),
                            "status": _prop("label", "label", [{"id": "done"}]),
                        },
                    ),
                ],
                "next_cursor": "page-2",
            },
            ("RootTask", "page-2"): {
                "objects": [
                    _object(
                        "task-unassigned",
                        "RootTask",
                        {
                            "title": _prop("title", "title", {"value": "Not assigned"}),
                            "assigned": _prop("boolean", "boolean", False),
                            "status": _prop("label", "label", [{"id": "open"}]),
                        },
                    ),
                ],
                "next_cursor": None,
            },
            ("custom-project", None): {
                "objects": [
                    _object(
                        "project-today",
                        "custom-project",
                        {
                            "title": _prop("title", "title", {"value": "Ship project"}),
                            "tdtb": _prop("label", "label", [{"id": "yes"}]),
                            "date": _prop("date", "date", {
                                "dateResolution": "day",
                                "start": "2026-09-28T00:00:00.000Z",
                            }),
                            "minutes": _prop("number", "number", {"value": 60}),
                            "state": _prop("label", "label", [{"id": "active"}]),
                        },
                    ),
                ],
                "next_cursor": None,
            },
        },
    )

    result = _adapter(provider).items_for_day(TODAY)

    assert [row["name"] for row in result.items] == ["Ship project", "Write brief"]
    assert result.items[0]["source"] == "capacities"
    assert result.items[0]["identity"] == "capacities:space-1:custom-project:project-today"
    assert result.items[0]["path"] == "capacities://space-1/project-today"
    assert result.items[1]["duration_minutes"] == 45
    assert result.items[1]["blocks"] == 1.5
    assert provider.list_calls == [
        ("RootTask", None),
        ("RootTask", "page-2"),
        ("custom-project", None),
    ]


def test_unknown_allowlisted_structure_fails_closed_before_listing():
    provider = FakeProvider(_structures(), {})
    with pytest.raises(CapacitiesContractError, match="custom-missing"):
        _adapter(
            provider,
            mappings=(*_mapping(), StructureMapping(
                structure_id="custom-missing",
                assignment_property="assigned",
                assignment_values=frozenset({"true"}),
            )),
        ).items_for_day(TODAY)
    assert provider.list_calls == []


def test_incomplete_pagination_does_not_return_partial_items():
    provider = FakeProvider(
        _structures(),
        {
            ("RootTask", None): {
                "objects": [],
                "next_cursor": "cursor-that-never-resolves",
            },
        },
    )
    with pytest.raises(CapacitiesContractError, match="pagination"):
        _adapter(
            provider,
            mappings=(_mapping()[0],),
            max_pages=1,
        ).items_for_day(TODAY)


def test_malformed_object_is_skipped_with_diagnostic_and_other_items_survive():
    provider = FakeProvider(
        _structures(),
        {
            ("RootTask", None): {
                "objects": [
                    {"id": "broken", "structureId": "RootTask", "properties": []},
                    _object(
                        "valid",
                        "RootTask",
                        {
                            "title": _prop("title", "title", {"value": "Valid"}),
                            "assigned": _prop("boolean", "boolean", True),
                            "status": _prop("label", "label", [{"id": "open"}]),
                        },
                    ),
                ],
                "next_cursor": None,
            },
        },
    )

    result = _adapter(provider, mappings=(_mapping()[0],)).items_for_day(TODAY)

    assert [item["name"] for item in result.items] == ["Valid"]
    assert any("broken" in warning for warning in result.warnings)


def test_completion_rereads_checks_baseline_patches_only_mapped_property_and_reads_back():
    obj = _object(
        "task-1",
        "RootTask",
        {
            "title": _prop("title", "title", {"value": "Ship"}),
            "assigned": _prop("boolean", "boolean", True),
            "status": _prop("label", "label", [{"id": "open"}]),
        },
    )
    provider = FakeProvider(_structures(), {}, {"task-1": obj})
    adapter = _adapter(provider, mappings=(_mapping()[0],))
    row = adapter.items_for_day_from_objects(TODAY, [obj]).items[0]

    outcome = adapter.complete(
        "task-1",
        expected_fingerprint=row["source_fingerprint"],
    )

    assert outcome.status == "completed"
    assert provider.patch_calls == [
        ("task-1", {"status": {"type": "label", "label": [{"id": "done"}]}})
    ]


def test_completion_can_restore_the_exact_mapped_property_after_undo():
    obj = _object(
        "task-1",
        "RootTask",
        {
            "title": _prop("title", "title", {"value": "Ship"}),
            "assigned": _prop("boolean", "boolean", True),
            "status": _prop("label", "label", [{"id": "open"}]),
        },
    )
    provider = FakeProvider(_structures(), {}, {"task-1": obj})
    adapter = _adapter(provider, mappings=(_mapping()[0],))
    row = adapter.items_for_day_from_objects(TODAY, [obj]).items[0]

    completed = adapter.complete(
        "task-1",
        expected_fingerprint=row["source_fingerprint"],
    )
    restored = adapter.restore_completion(
        "task-1",
        completion_property=completed.completion_property,
        before_property=completed.before_property,
        expected_fingerprint=completed.after_fingerprint,
    )

    assert restored.status == "restored"
    assert provider.patch_calls == [
        ("task-1", {"status": {"type": "label", "label": [{"id": "done"}]}}),
        ("task-1", {"status": {"type": "label", "label": [{"id": "open"}]}}),
    ]


def test_completion_rejects_changed_source_without_writing():
    obj = _object(
        "task-1",
        "RootTask",
        {
            "title": _prop("title", "title", {"value": "Ship"}),
            "assigned": _prop("boolean", "boolean", True),
            "status": _prop("label", "label", [{"id": "open"}]),
        },
    )
    provider = FakeProvider(_structures(), {}, {"task-1": obj})
    adapter = _adapter(provider, mappings=(_mapping()[0],))
    row = adapter.items_for_day_from_objects(TODAY, [obj]).items[0]
    provider._objects["task-1"]["properties"]["title"] = _prop(
        "title", "title", {"value": "Changed externally"}
    )

    with pytest.raises(CapacitiesWriteError, match="changed"):
        adapter.complete("task-1", expected_fingerprint=row["source_fingerprint"])
    assert provider.patch_calls == []


def test_completion_requires_a_source_fingerprint_baseline():
    obj = _object(
        "task-1",
        "RootTask",
        {
            "title": _prop("title", "title", {"value": "Ship"}),
            "assigned": _prop("boolean", "boolean", True),
            "status": _prop("label", "label", [{"id": "open"}]),
        },
    )
    provider = FakeProvider(_structures(), {}, {"task-1": obj})
    adapter = _adapter(provider, mappings=(_mapping()[0],))

    with pytest.raises(CapacitiesWriteError, match="fingerprint baseline"):
        adapter.complete("task-1")
    assert provider.patch_calls == []


def test_completion_timeout_after_success_is_reconciled_by_readback():
    obj = _object(
        "task-1",
        "RootTask",
        {
            "title": _prop("title", "title", {"value": "Ship"}),
            "assigned": _prop("boolean", "boolean", True),
            "status": _prop("label", "label", [{"id": "open"}]),
        },
    )
    import httpx

    provider = FakeProvider(_structures(), {}, {"task-1": obj})
    provider.patch_exception = httpx.ReadTimeout("simulated timeout after provider accepted patch")
    adapter = _adapter(provider, mappings=(_mapping()[0],))
    row = adapter.items_for_day_from_objects(TODAY, [obj]).items[0]

    outcome = adapter.complete(
        "task-1",
        expected_fingerprint=row["source_fingerprint"],
    )

    assert outcome.status == "completed_after_timeout"
    assert len(provider.patch_calls) == 1


def test_rest_client_uses_current_object_endpoints_and_bearer_auth():
    import httpx

    calls = []

    def handler(request):
        calls.append(request)
        if request.url.path == "/space/structures":
            return httpx.Response(200, json={"structures": []})
        if request.url.path == "/objects/structure":
            return httpx.Response(200, json={"objects": [], "nextCursor": "next"})
        if request.url.path == "/object" and request.method == "GET":
            return httpx.Response(200, json={"id": "object-1"})
        if request.url.path == "/object" and request.method == "PATCH":
            return httpx.Response(200, json={"id": "object-1"})
        return httpx.Response(404)

    client = CapacitiesRestClient(
        "token-value",
        space_id="space-1",
        base_url="https://example.test",
        transport=httpx.MockTransport(handler),
    )
    try:
        assert client.fetch_structures() == {"structures": []}
        assert client.list_objects("RootTask", "cursor-1") == {
            "objects": [],
            "next_cursor": "next",
        }
        assert client.get_object("object-1") == {"id": "object-1"}
        assert client.patch_object("object-1", {"status": {"type": "label"}}) == {
            "id": "object-1"
        }
    finally:
        client.close()

    assert all(request.headers["authorization"] == "Bearer token-value" for request in calls)
    assert calls[1].url.params["id"] == "RootTask"
    assert calls[1].url.params["cursor"] == "cursor-1"
    # Space scoping is load-bearing: an unscoped listing silently reports zero
    # objects for every structure on the live API.
    assert calls[1].url.params["spaceId"] == "space-1"
    assert calls[3].content == b'{"id":"object-1","properties":{"status":{"type":"label"}}}'


# --------------------------------------------------------------------------
# Native Auto projection: no source assignment property required
# --------------------------------------------------------------------------

CUSTOM_IDENTITY = f"capacities:{SPACE}:custom-project:project-1"


def _native_structures():
    return [
        {
            "id": "RootTask",
            "title": "Task",
            "propertyDefinitions": [
                _definition("title", "title"),
                _definition("assigned", "boolean"),
                _definition(
                    "status",
                    "label",
                    labels=[
                        ("active", "Active"),
                        ("open", "Open"),
                        ("done", "Done"),
                        ("dropped", "Dropped"),
                    ],
                ),
                _definition("due", "date"),
                _definition("deadline", "date"),
                _definition("duration", "number"),
            ],
        },
    ]


def _native_mapping(**overrides):
    base = dict(
        structure_id="RootTask",
        title_property="title",
        date_property="due",
        deadline_property="deadline",
        open_status_property="status",
        open_status_values=frozenset({"active", "open"}),
        duration_property="duration",
    )
    base.update(overrides)
    return StructureMapping(**base)


def _native_object(object_id, *, status="open", due=None, deadline=None, title=None):
    properties = {
        "title": _prop("title", "title", {"value": title or object_id}),
        "status": _prop("label", "label", [{"id": status}]),
    }
    if due is not None:
        properties["due"] = _prop(
            "date", "date",
            {"dateResolution": "day", "start": f"{due.isoformat()}T00:00:00.000Z"},
        )
    if deadline is not None:
        properties["deadline"] = _prop(
            "date", "date",
            {"dateResolution": "day", "start": f"{deadline.isoformat()}T00:00:00.000Z"},
        )
    return _object(object_id, "RootTask", properties)


def _native_provider(objects):
    return FakeProvider(
        _native_structures(),
        {("RootTask", None): {"objects": list(objects), "next_cursor": None}},
    )


def _custom_provider(objects):
    return FakeProvider(
        _structures(),
        {("custom-project", None): {"objects": list(objects), "next_cursor": None}},
    )


def test_native_mapping_without_assignment_property_projects_auto_candidates():
    provider = _native_provider(
        [
            _native_object("active", status="active"),
            _native_object("due-today", due=TODAY),
            _native_object("overdue", due=TODAY - timedelta(days=5)),
            _native_object("deadline-edge", deadline=TODAY + timedelta(days=2)),
        ]
    )

    result = _adapter(provider, mappings=(_native_mapping(),)).items_for_day(TODAY)

    assert [row["capacities_id"] for row in result.items] == [
        "active",
        "deadline-edge",
        "due-today",
        "overdue",
    ]
    assert all(row["capacities_assignment"]["mode"] == "auto" for row in result.items)


def test_native_mapping_rejects_future_out_of_horizon_and_closed_rows():
    provider = _native_provider(
        [
            _native_object("future-due", due=TODAY + timedelta(days=1)),
            _native_object("out-of-horizon", deadline=TODAY + timedelta(days=3)),
            _native_object("completed", status="done"),
            _native_object("dropped", status="dropped"),
            _native_object("no-signal"),
        ]
    )

    result = _adapter(provider, mappings=(_native_mapping(),)).items_for_day(TODAY)

    assert result.items == []
    assert result.warnings == []


def test_native_mapping_all_rules_disabled_projects_nothing():
    provider = _native_provider(
        [
            _native_object("active", status="active"),
            _native_object("due-today", due=TODAY),
            _native_object("deadline-edge", deadline=TODAY + timedelta(days=2)),
        ]
    )
    settings = AssignmentSettings(
        active_enabled=False, due_enabled=False, deadline_enabled=False
    )

    result = _adapter(
        provider, mappings=(_native_mapping(),), assignment_settings=settings
    ).items_for_day(TODAY)

    assert result.items == []


def test_native_deadline_horizon_is_settings_driven_and_inclusive():
    provider = _native_provider(
        [_native_object("deadline-edge", deadline=TODAY + timedelta(days=2))]
    )
    settings = AssignmentSettings(deadline_horizon_days=1)

    result = _adapter(
        provider, mappings=(_native_mapping(),), assignment_settings=settings
    ).items_for_day(TODAY)

    assert result.items == []


def test_legacy_native_marker_is_positive_but_absent_marker_uses_native_auto():
    provider = _native_provider(
        [
            _object(
                "marked",
                "RootTask",
                {
                    "title": _prop("title", "title", {"value": "Marked"}),
                    "assigned": _prop("boolean", "boolean", True),
                    "status": _prop("label", "label", [{"id": "open"}]),
                },
            ),
            _native_object("active-no-marker", status="active"),
        ]
    )
    mapping = _native_mapping(
        assignment_property="assigned",
        assignment_values=frozenset({"true"}),
    )

    result = _adapter(provider, mappings=(mapping,)).items_for_day(TODAY)

    modes = {
        row["capacities_id"]: row["capacities_assignment"]["mode"]
        for row in result.items
    }
    assert modes == {"marked": "assigned", "active-no-marker": "auto"}


def test_native_projection_preserves_stable_identity_and_decision_metadata():
    provider = _native_provider(
        [_native_object("task-42", status="active", title="Active task")]
    )

    result = _adapter(provider, mappings=(_native_mapping(),)).items_for_day(TODAY)

    row = result.items[0]
    assert row["identity"] == "capacities:space-1:RootTask:task-42"
    assert row["path"] == "capacities://space-1/task-42"
    assert row["name"] == "Active task"
    assert row["assigned"] is True
    assert row["capacities_assignment"] == {
        "mode": "auto",
        "reasons": ["auto-status-active"],
        "source_assigned": None,
        "excluded": False,
    }


def test_reads_never_patch_or_mutate_the_provider():
    provider = _native_provider([_native_object("task-1", status="active")])

    result = _adapter(provider, mappings=(_native_mapping(),)).items_for_day(TODAY)

    assert len(result.items) == 1
    assert provider.patch_calls == []
    assert provider.list_calls == [("RootTask", None)]


# --------------------------------------------------------------------------
# Custom inclusion: explicit source assignment, no implicit Auto
# --------------------------------------------------------------------------


def test_custom_assigned_true_is_accepted_even_when_tdtb_excluded():
    provider = _custom_provider(
        [
            _object(
                "project-1",
                "custom-project",
                {
                    "title": _prop("title", "title", {"value": "Assigned despite exclusion"}),
                    "tdtb": _prop("label", "label", [{"id": "yes"}]),
                    "date": _prop("date", "date", {"start": "2026-09-29T00:00:00.000Z"}),
                    "state": _prop("label", "label", [{"id": "active"}]),
                },
            ),
        ]
    )
    settings = AssignmentSettings(excluded_identities=frozenset({CUSTOM_IDENTITY}))

    result = _adapter(provider, assignment_settings=settings).items_for_day(TODAY)

    assert [row["capacities_id"] for row in result.items] == ["project-1"]
    row = result.items[0]
    assert row["identity"] == CUSTOM_IDENTITY
    assert row["capacities_assignment"] == {
        "mode": "assigned",
        "reasons": ["source-assigned"],
        "source_assigned": True,
        "excluded": False,
    }


def test_custom_unassigned_or_absent_is_not_projected_even_when_open_and_due():
    provider = _custom_provider(
        [
            _object(
                "project-false",
                "custom-project",
                {
                    "title": _prop("title", "title", {"value": "Explicitly not assigned"}),
                    "tdtb": _prop("label", "label", [{"id": "no"}]),
                    "date": _prop("date", "date", {"start": "2026-09-29T00:00:00.000Z"}),
                    "state": _prop("label", "label", [{"id": "active"}]),
                },
            ),
            _object(
                "project-absent",
                "custom-project",
                {
                    "title": _prop("title", "title", {"value": "No assignment marker"}),
                    "date": _prop("date", "date", {"start": "2026-09-29T00:00:00.000Z"}),
                    "state": _prop("label", "label", [{"id": "active"}]),
                },
            ),
        ]
    )

    result = _adapter(provider).items_for_day(TODAY)

    assert result.items == []
    assert result.warnings == []


def test_custom_excluded_but_unassigned_is_not_projected():
    provider = _custom_provider(
        [
            _object(
                "project-1",
                "custom-project",
                {
                    "title": _prop("title", "title", {"value": "Excluded"}),
                    "tdtb": _prop("label", "label", [{"id": "no"}]),
                    "state": _prop("label", "label", [{"id": "active"}]),
                },
            ),
        ]
    )
    settings = AssignmentSettings(excluded_identities=frozenset({CUSTOM_IDENTITY}))

    result = _adapter(provider, assignment_settings=settings).items_for_day(TODAY)

    assert result.items == []


def test_native_mapping_without_assignment_marker_requires_status_mapping():
    provider = _native_provider([])
    mapping = _native_mapping(open_status_property=None, open_status_values=frozenset())

    with pytest.raises(CapacitiesContractError, match="mapped status property"):
        _adapter(provider, mappings=(mapping,)).items_for_day(TODAY)

    assert provider.list_calls == []


def test_missing_space_identity_is_accepted_because_enumeration_is_scoped():
    """A row without ``spaceId`` is legitimate on the live API.

    Neither the scoped structure listing nor the object-content read returns
    ``spaceId``, so the space guarantee comes from the scoped request rather
    than the payload. Requiring the field made every live row look
    cross-space and emptied the source. A payload that *does* carry a
    disagreeing value is still rejected; see
    ``test_contradictory_payload_space_is_rejected``.
    """
    provider = _native_provider([{
        "id": "scoped-row",
        "structureId": "RootTask",
        "properties": {
            "title": _prop("title", "title", {"value": "Scoped row"}),
            "status": _prop("label", "label", [{"id": "active"}]),
        },
    }])

    result = _adapter(provider, mappings=(_native_mapping(),)).items_for_day(TODAY)

    assert [item["name"] for item in result.items] == ["Scoped row"]
    assert result.warnings == []


def test_non_native_mapping_without_assignment_property_fails_closed():
    provider = FakeProvider(_structures(), {})

    with pytest.raises(CapacitiesContractError, match="assignment property"):
        _adapter(
            provider,
            mappings=(
                _mapping()[0],
                StructureMapping(structure_id="custom-project", title_property="title"),
            ),
        ).items_for_day(TODAY)

    assert provider.list_calls == []


def test_configured_native_structure_is_enumerated_and_evaluated_by_the_native_rule():
    """Moving a structure into ``native_task_structures`` is honoured by both
    the enumeration gate and the evaluator's native Auto rule."""
    provider = _custom_provider(
        [
            _object(
                "project-1",
                "custom-project",
                {
                    "title": _prop("title", "title", {"value": "Active project"}),
                    "state": _prop("label", "label", [{"id": "active"}]),
                },
            ),
        ]
    )
    mapping = StructureMapping(
        structure_id="custom-project",
        title_property="title",
        open_status_property="state",
        open_status_values=frozenset({"active"}),
    )
    settings = AssignmentSettings(
        native_task_structures=frozenset({"RootTask", "custom-project"})
    )

    result = _adapter(
        provider,
        mappings=(_native_mapping(deadline_property=None), mapping),
        assignment_settings=settings,
    ).items_for_day(TODAY)

    assert [row["capacities_id"] for row in result.items] == ["project-1"]
    assert result.items[0]["capacities_assignment"]["mode"] == "auto"
    assert ("custom-project", None) in provider.list_calls


def test_contract_validation_follows_the_configured_native_set():
    """The native-vs-custom error branch at contract validation follows the
    configured set. Both branches still fail closed for a mapping with neither
    an assignment property nor a status property; the configured set decides
    which requirement the operator is told to satisfy."""
    bad_mapping = StructureMapping(structure_id="custom-project", title_property="title")

    custom_provider = FakeProvider(_structures(), {})
    with pytest.raises(CapacitiesContractError, match="assignment property"):
        _adapter(
            custom_provider, mappings=(_mapping()[0], bad_mapping)
        ).items_for_day(TODAY)

    native_provider = FakeProvider(_structures(), {})
    with pytest.raises(CapacitiesContractError, match="native mapping"):
        _adapter(
            native_provider,
            mappings=(_mapping()[0], bad_mapping),
            assignment_settings=AssignmentSettings(
                native_task_structures=frozenset({"RootTask", "custom-project"})
            ),
        ).items_for_day(TODAY)

    assert custom_provider.list_calls == []
    assert native_provider.list_calls == []


# --------------------------------------------------------------------------
# Custom Active pull: an Active-enabled custom structure can be satisfied by
# either an assignment property or a mapped status property.
# --------------------------------------------------------------------------


ACTIVE_STRUCTURE = "custom-project"


def _active_mapping(**overrides):
    base = dict(
        structure_id=ACTIVE_STRUCTURE,
        title_property="title",
        open_status_property="state",
        open_status_values=frozenset({"active"}),
        duration_property="minutes",
    )
    base.update(overrides)
    return StructureMapping(**base)


def _active_settings():
    return AssignmentSettings(active_structures=frozenset({ACTIVE_STRUCTURE}))


def test_custom_active_enabled_mapping_without_assignment_projects_active_object():
    provider = _custom_provider(
        [
            _object(
                "project-1",
                ACTIVE_STRUCTURE,
                {
                    "title": _prop("title", "title", {"value": "Active project"}),
                    "state": _prop("label", "label", [{"id": "active", "name": "Active"}]),
                },
            ),
        ]
    )

    result = _adapter(
        provider, mappings=(_native_mapping(deadline_property=None), _active_mapping()), assignment_settings=_active_settings()
    ).items_for_day(TODAY)

    assert [row["capacities_id"] for row in result.items] == ["project-1"]
    assert result.items[0]["capacities_assignment"] == {
        "mode": "auto",
        "reasons": ["auto-custom-status-active"],
        "source_assigned": None,
        "excluded": False,
    }


def test_custom_active_enabled_mapping_requires_the_active_status():
    provider = _custom_provider(
        [
            _object(
                "project-1",
                ACTIVE_STRUCTURE,
                {
                    "title": _prop("title", "title", {"value": "Planning"}),
                    "state": _prop("label", "label", [{"id": "planning", "name": "Planning"}]),
                },
            ),
        ]
    )

    result = _adapter(
        provider,
        mappings=(_native_mapping(deadline_property=None), _active_mapping(open_status_values=frozenset({"planning"}))),
        assignment_settings=_active_settings(),
    ).items_for_day(TODAY)

    assert result.items == []
    assert result.warnings == []


def test_custom_active_pull_respects_the_label_name_not_the_label_id():
    # ``named-active`` carries label id ``in-progress`` but name ``Active`` and
    # must match. ``named-progress`` carries label id ``active`` but name
    # ``In Progress`` and must NOT match: the rule keys on the typed label
    # name, never the id and never frontmatter text.
    provider = _custom_provider(
        [
            _object(
                "named-active",
                ACTIVE_STRUCTURE,
                {
                    "title": _prop("title", "title", {"value": "Named Active"}),
                    "state": _prop("label", "label", [{"id": "in-progress", "name": "Active"}]),
                },
            ),
            _object(
                "named-progress",
                ACTIVE_STRUCTURE,
                {
                    "title": _prop("title", "title", {"value": "Named Progress"}),
                    "state": _prop("label", "label", [{"id": "active", "name": "In Progress"}]),
                },
            ),
        ]
    )
    mapping = _active_mapping(open_status_values=frozenset({"active", "in progress"}))

    result = _adapter(
        provider, mappings=(_native_mapping(deadline_property=None), mapping), assignment_settings=_active_settings()
    ).items_for_day(TODAY)

    assert [row["capacities_id"] for row in result.items] == ["named-active"]
    assert result.items[0]["capacities_assignment"]["reasons"] == [
        "auto-custom-status-active"
    ]


def test_native_active_label_matches_by_name_not_id():
    provider = _native_provider(
        [
            _object(
                "task-active",
                "RootTask",
                {
                    "title": _prop("title", "title", {"value": "Active task"}),
                    "status": _prop("label", "label", [{"id": "in-progress", "name": "Active"}]),
                },
            ),
            _object(
                "task-progress",
                "RootTask",
                {
                    "title": _prop("title", "title", {"value": "In progress task"}),
                    "status": _prop("label", "label", [{"id": "active", "name": "In Progress"}]),
                },
            ),
        ]
    )

    result = _adapter(provider, mappings=(_native_mapping(),)).items_for_day(TODAY)

    assert [row["capacities_id"] for row in result.items] == ["task-active"]
    assert result.items[0]["capacities_assignment"]["reasons"] == ["auto-status-active"]


def test_active_enabled_mapping_without_assignment_or_status_property_fails_closed():
    provider = FakeProvider(_structures(), {})
    mapping = StructureMapping(structure_id=ACTIVE_STRUCTURE, title_property="title")

    with pytest.raises(CapacitiesContractError, match="assignment property"):
        _adapter(
            provider,
            mappings=(_mapping()[0], mapping),
            assignment_settings=_active_settings(),
        ).items_for_day(TODAY)

    assert provider.list_calls == []


def test_structure_without_an_active_option_is_never_included_by_the_active_pull():
    # Adventure offers Inbox/Planning/Scheduled/Completed/Dropped and no Active
    # option, so even when (mis)enabled no value satisfies the Active pull.
    structures = [
        {
            "id": "RootTask",
            "title": "Task",
            "propertyDefinitions": [
                _definition("title", "title"),
                _definition("status", "label", labels=[("active", "Active")]),
            ],
        },
        {
            "id": "adventure",
            "title": "Adventure",
            "propertyDefinitions": [
                _definition("title", "title"),
                _definition(
                    "state",
                    "label",
                    labels=[
                        ("inbox", "Inbox"),
                        ("planning", "Planning"),
                        ("scheduled", "Scheduled"),
                        ("done", "Completed"),
                        ("dropped", "Dropped"),
                    ],
                ),
            ],
        },
    ]
    provider = FakeProvider(
        structures,
        {
            ("adventure", None): {
                "objects": [
                    _object(
                        "adv-1",
                        "adventure",
                        {
                            "title": _prop("title", "title", {"value": "Trip"}),
                            "state": _prop(
                                "label", "label", [{"id": "scheduled", "name": "Scheduled"}]
                            ),
                        },
                    )
                ],
                "next_cursor": None,
            }
        },
    )
    mappings = (
        StructureMapping(
            structure_id="RootTask",
            title_property="title",
            open_status_property="status",
            open_status_values=frozenset({"active"}),
        ),
        StructureMapping(
            structure_id="adventure",
            title_property="title",
            open_status_property="state",
            open_status_values=frozenset({"inbox", "planning", "scheduled"}),
        ),
    )
    settings = AssignmentSettings(active_structures=frozenset({"adventure"}))

    result = _adapter(
        provider, mappings=mappings, assignment_settings=settings
    ).items_for_day(TODAY)

    assert result.items == []


def test_custom_excluded_active_enabled_object_is_not_projected():
    provider = _custom_provider(
        [
            _object(
                "project-1",
                ACTIVE_STRUCTURE,
                {
                    "title": _prop("title", "title", {"value": "Excluded Active"}),
                    "state": _prop("label", "label", [{"id": "active", "name": "Active"}]),
                },
            ),
        ]
    )
    settings = AssignmentSettings(
        excluded_identities=frozenset({CUSTOM_IDENTITY}),
        active_structures=frozenset({ACTIVE_STRUCTURE}),
    )

    result = _adapter(
        provider, mappings=(_native_mapping(deadline_property=None), _active_mapping()), assignment_settings=settings
    ).items_for_day(TODAY)

    assert result.items == []


def test_custom_status_mapping_is_not_enumerated_before_it_is_active_enabled():
    """A custom structure with no assignment property cannot contribute.

    Nothing from it can ever be eligible until Settings Active-enables it, so
    it is not enumerated and its objects are never hydrated. That matters
    because the live API allows 30 requests per minute and enumeration plus
    hydration is an N+1 burst. The Settings drawer learns which structures
    exist from the mapping record, not from enumeration, so nothing is lost.
    """
    provider = _custom_provider(
        [
            _object(
                "project-1",
                ACTIVE_STRUCTURE,
                {
                    "title": _prop("title", "title", {"value": "Active project"}),
                    "state": _prop("label", "label", [{"id": "active", "name": "Active"}]),
                },
            ),
        ]
    )

    result = _adapter(
        provider,
        mappings=(_native_mapping(deadline_property=None), _active_mapping()),
    ).items_for_day(TODAY)

    assert result.items == []
    assert result.warnings == []
    assert ("custom-project", None) not in provider.list_calls


def test_content_read_budget_bounds_hydration_and_reports_the_remainder():
    """A read must not exceed the provider's rate limit.

    Unevaluated objects are reported, never silently dropped.
    """
    listed = [
        {"id": f"task-{n}", "structureId": "RootTask", "title": f"Task {n}"}
        for n in range(1, 4)
    ]
    provider = FakeProvider(
        _native_structures(),
        {("RootTask", None): {"objects": listed, "next_cursor": None}},
        objects={
            f"task-{n}": _native_object(f"task-{n}", status="active")
            for n in range(1, 4)
        },
    )

    result = _adapter(
        provider,
        mappings=(_native_mapping(),),
        max_content_reads=1,
    ).items_for_day(TODAY)

    assert len(result.items) == 1
    assert result.evaluated == 1
    assert result.deferred == 2
    assert result.warnings == [
        "Capacities partial — 1 evaluated · 2 deferred across contributing "
        "structures. Content-read budget reached. Wait at least a minute, then "
        "Refresh sources to continue."
    ]


# --------------------------------------------------------------------------
# Live REST contract: space scoping and per-object property hydration
# --------------------------------------------------------------------------

def test_rest_client_requires_a_space_id():
    with pytest.raises(ValueError):
        CapacitiesRestClient("token-value", space_id="   ")


def test_rest_client_prefers_the_modern_results_envelope():
    """The scoped live endpoint answers with ``results``/``nextCursor``."""
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            200,
            json={"results": [{"id": "a"}], "nextCursor": "c2", "hasMore": True},
        )

    client = CapacitiesRestClient(
        "token-value",
        space_id="space-9",
        base_url="https://example.test",
        transport=httpx.MockTransport(handler),
    )
    try:
        assert client.list_objects("RootTask") == {
            "objects": [{"id": "a"}],
            "next_cursor": "c2",
        }
    finally:
        client.close()

    assert calls[0].url.path == "/objects/structure"
    assert calls[0].url.params["spaceId"] == "space-9"


def test_property_less_listing_rows_are_hydrated_from_object_content():
    """The live listing carries id/structureId/title only.

    Eligibility needs typed properties, so the adapter must fetch each listed
    object's content. Without hydration every live row would look malformed and
    the source would report nothing.
    """
    listed = [{"id": "task-1", "structureId": "RootTask", "title": "Hydrated"}]
    provider = FakeProvider(
        _native_structures(),
        {("RootTask", None): {"objects": listed, "next_cursor": None}},
        objects={"task-1": _native_object("task-1", status="active")},
    )

    result = _adapter(provider, mappings=(_native_mapping(),)).items_for_day(TODAY)

    assert [row["capacities_id"] for row in result.items] == ["task-1"]
    assert result.warnings == []


def test_empty_typed_title_falls_back_to_the_top_level_listing_title():
    """Live objects can carry the name at top level while the typed title is empty.

    Verified against live objects whose ``properties.title.title.value`` is an
    empty string while the object's top-level ``title`` holds the real name
    (for example ``Habit``). The listing row preserves that top-level title
    through hydration, so the projection must read it instead of dropping the
    whole object as malformed.
    """
    listed = [{"id": "task-1", "structureId": "RootTask", "title": "Habit"}]
    content = _native_object("task-1", status="active")
    content["properties"]["title"] = _prop("title", "title", {"value": ""})
    provider = FakeProvider(
        _native_structures(),
        {("RootTask", None): {"objects": listed, "next_cursor": None}},
        objects={"task-1": content},
    )

    result = _adapter(provider, mappings=(_native_mapping(),)).items_for_day(TODAY)

    assert [row["name"] for row in result.items] == ["Habit"]
    assert result.warnings == []
    assert not any("not plannable" in warning for warning in result.warnings)


def test_custom_title_property_stays_authoritative_when_empty():
    """A custom mapped title property must fail closed, not borrow the listing title.

    The top-level fallback is scoped to the canonical ``title`` property. A
    mapping that deliberately points elsewhere must not silently substitute a
    different field when its own title property is empty.
    """
    structures = _native_structures()
    structures[0]["propertyDefinitions"].append(_definition("headline", "title"))
    provider = FakeProvider(structures, {})
    obj = _native_object("task-1", status="active")
    obj["title"] = "Listing name"
    obj["properties"]["headline"] = _prop("title", "headline", {"value": ""})

    result = _adapter(
        provider, mappings=(_native_mapping(title_property="headline"),)
    ).items_for_day_from_objects(TODAY, [obj])

    assert result.items == []
    assert any("not plannable" in w and "headline" in w for w in result.warnings)


def test_missing_typed_and_top_level_title_still_skips_as_malformed():
    """With neither source usable, the original malformed reason is preserved."""
    provider = FakeProvider(_native_structures(), {})
    obj = _native_object("task-1", status="active")
    obj["properties"]["title"] = _prop("title", "title", {"value": ""})

    result = _adapter(provider, mappings=(_native_mapping(),)).items_for_day_from_objects(
        TODAY, [obj]
    )

    assert result.items == []
    assert any(
        "not plannable" in w and "no usable value" in w for w in result.warnings
    )


def test_contradictory_payload_space_is_rejected():
    """A payload space that disagrees with the configured one is not trusted."""
    provider = _custom_provider(
        [
            _object(
                "project-1",
                ACTIVE_STRUCTURE,
                {
                    "title": _prop("title", "title", {"value": "Elsewhere"}),
                    "state": _prop("label", "label", [{"id": "active", "name": "Active"}]),
                },
                space="some-other-space",
            ),
        ]
    )

    result = _adapter(
        provider,
        mappings=(_native_mapping(deadline_property=None), _active_mapping()),
        assignment_settings=_active_settings(),
    ).items_for_day(TODAY)

    assert result.items == []
    assert any("not in the configured space" in w for w in result.warnings)


class _RateLimitedProvider:
    """Lists every row, then refuses content reads past a budget of ``allow``."""

    def __init__(self, structure_id, object_ids, allow):
        self._structure_id = structure_id
        self._object_ids = list(object_ids)
        self._allow = allow
        self.reads = 0

    def fetch_structures(self):
        return _native_structures()

    def list_objects(self, structure_id, cursor=None):
        rows = [
            {"id": oid, "structureId": self._structure_id, "title": f"Task {oid}"}
            for oid in self._object_ids
        ]
        return {"objects": rows, "next_cursor": None}

    def get_object(self, object_id):
        if self.reads >= self._allow:
            self.reads += 1
            raise CapacitiesRateLimited("rate limit (30 requests per minute) exceeded")
        self.reads += 1
        return _native_object(object_id, status="active")

    def patch_object(self, object_id, properties):
        raise AssertionError("the read path must not write")


def test_rate_limited_read_keeps_what_it_already_read():
    """A 429 must degrade, not discard the whole read.

    The provider allows 30 requests per minute, so a rate limit is a normal
    operating condition. Failing the source outright threw away every object
    already evaluated; the read now returns those and reports the remainder.
    """
    provider = _RateLimitedProvider("RootTask", ["a", "b", "c", "d"], allow=2)

    result = _adapter(
        provider,
        mappings=(_native_mapping(),),
        max_content_reads=10,
    ).items_for_day(TODAY)

    assert len(result.items) == 2
    assert result.evaluated == 2
    assert result.deferred == 2
    assert result.warnings == [
        "Capacities partial — 2 evaluated · 2 deferred across contributing "
        "structures. Provider rate limit (30 requests per minute) reached. "
        "Wait at least a minute, then Refresh sources to continue."
    ]


def _object_transport(calls):
    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, json={"id": "x", "properties": {}})

    return httpx.MockTransport(handler)


def test_repeated_content_reads_reuse_the_cache():
    calls = []
    cache = _Cache()
    client = CapacitiesRestClient(
        "token",
        space_id=SPACE,
        transport=_object_transport(calls),
        content_cache=cache,
    )
    try:
        first = client.get_object("a")
        second = client.get_object("a")
    finally:
        client.close()

    assert first == second
    assert len(calls) == 1, calls


def test_content_cache_expires_so_edits_converge():
    calls = []
    cache = _Cache(ttl=0.0)
    client = CapacitiesRestClient(
        "token",
        space_id=SPACE,
        transport=_object_transport(calls),
        content_cache=cache,
    )
    try:
        client.get_object("a")
        client.get_object("a")
    finally:
        client.close()

    assert len(calls) == 2, calls


def test_client_raises_a_typed_error_on_a_rate_limit():
    def handler(request):
        return httpx.Response(429, json={"code": "cap_rate_limited"})

    client = CapacitiesRestClient(
        "token", space_id=SPACE, transport=httpx.MockTransport(handler)
    )
    try:
        with pytest.raises(CapacitiesRateLimited):
            client.get_object("a")
    finally:
        client.close()


class _Cache:
    """Minimal cache double mirroring ``capacities_builder``'s contract."""

    def __init__(self, ttl=300.0):
        self._ttl = ttl
        self._entries = {}

    def get(self, key):
        entry = self._entries.get(key)
        if entry is None or self._ttl <= 0:
            return None
        return entry

    def put(self, key, value):
        self._entries[key] = value


def test_cached_content_does_not_consume_the_read_budget():
    """Refreshes must converge on full coverage, not starve the tail.

    Budgeting cached content starved whatever fell outside the first N objects:
    every read spent its allowance on the same rows and the remainder was never
    evaluated. Cached content is free, so a second read reaches the rest.
    """
    provider = _RateLimitedProvider("RootTask", ["a", "b", "c", "d"], allow=99)
    cache = _Cache()
    adapter = _adapter(
        provider,
        mappings=(_native_mapping(),),
        max_content_reads=2,
        content_cache=cache,
    )

    first = adapter.items_for_day(TODAY)
    assert len(first.items) == 2
    assert first.evaluated == 2
    assert first.deferred == 2
    assert any("2 evaluated · 2 deferred" in w for w in first.warnings)

    # The two already-fetched objects are now cached, so the same budget
    # reaches the rows that were skipped.
    second = adapter.items_for_day(TODAY)
    assert len(second.items) == 4
    assert second.evaluated == 4
    assert second.deferred == 0
    assert not any("Capacities partial" in w for w in second.warnings)


# ---------------------------------------------------------------------------
# Coverage accounting over distinct listed identities
# ---------------------------------------------------------------------------
# The cockpit warning must state how much of the source was covered, not only
# how much was deferred. Accounting is run-local and identity-keyed: repeated
# listed rows never inflate a count, and malformed rows are reported
# separately instead of being folded into evaluated or deferred. ``evaluated``
# counts successfully projected objects — including ones the projection then
# filtered out as ineligible — so it is deliberately NOT the accepted-item
# count that ``source_counts.capacities`` reports.


def _unhydrated_rows(object_ids):
    """Listing rows as the live API returns them: no typed properties."""
    return [
        {"id": object_id, "structureId": "RootTask", "title": f"Task {object_id}"}
        for object_id in object_ids
    ]


def _native_contents(object_ids):
    return {
        object_id: _native_object(object_id, status="active")
        for object_id in object_ids
    }


def test_many_object_cold_run_reports_evaluated_and_deferred_separately():
    """A cold run spends one read per listed row; the warning names both counts.

    With the 20-read default budget, 24 rows evaluate 20 and defer 4, and the
    warning says so instead of only naming the deferred remainder.
    """
    object_ids = [f"task-{n}" for n in range(1, 25)]
    provider = FakeProvider(
        _native_structures(),
        {
            ("RootTask", None): {
                "objects": _unhydrated_rows(object_ids),
                "next_cursor": None,
            }
        },
        objects=_native_contents(object_ids),
    )

    result = _adapter(provider, mappings=(_native_mapping(),)).items_for_day(TODAY)

    assert result.evaluated == 20
    assert result.deferred == 4
    assert result.malformed == 0
    assert len(result.items) == 20
    assert result.warnings == [
        "Capacities partial — 20 evaluated · 4 deferred across contributing "
        "structures. Content-read budget reached. Wait at least a minute, then "
        "Refresh sources to continue."
    ]


def test_pagination_across_pages_aggregates_coverage():
    provider = FakeProvider(
        _native_structures(),
        {
            ("RootTask", None): {
                "objects": _unhydrated_rows(["a", "b"]),
                "next_cursor": "page-2",
            },
            ("RootTask", "page-2"): {
                "objects": _unhydrated_rows(["c", "d"]),
                "next_cursor": None,
            },
        },
        objects=_native_contents(["a", "b", "c", "d"]),
    )

    result = _adapter(
        provider, mappings=(_native_mapping(),), max_content_reads=3
    ).items_for_day(TODAY)

    assert result.pages == 2
    assert result.evaluated == 3
    assert result.deferred == 1
    assert any("3 evaluated · 1 deferred" in w for w in result.warnings)


def test_duplicate_listed_rows_do_not_inflate_coverage():
    """A repeated row is one identity, even when the budget splits its outcome.

    ``a`` is evaluated on its first row; the duplicate and ``b`` are deferred
    once the budget is spent. ``a`` must not also count as deferred.
    """
    provider = FakeProvider(
        _native_structures(),
        {
            ("RootTask", None): {
                "objects": _unhydrated_rows(["a", "a", "b"]),
                "next_cursor": None,
            }
        },
        objects=_native_contents(["a", "b"]),
    )

    result = _adapter(
        provider, mappings=(_native_mapping(),), max_content_reads=1
    ).items_for_day(TODAY)

    assert result.evaluated == 1
    assert result.deferred == 1
    assert any("1 evaluated · 1 deferred" in w for w in result.warnings)


def test_duplicate_rows_carrying_properties_count_one_evaluated_identity():
    provider = _native_provider([
        _native_object("a", status="active"),
        _native_object("a", status="active"),
        _native_object("b", status="active"),
    ])

    result = _adapter(provider, mappings=(_native_mapping(),)).items_for_day(TODAY)

    assert result.evaluated == 2
    assert result.deferred == 0
    assert result.warnings == []


def test_malformed_rows_are_counted_separately_from_coverage():
    """Malformed rows never inflate coverage, and identity-less rows only warn."""
    provider = FakeProvider(
        _native_structures(),
        {
            ("RootTask", None): {
                "objects": [
                    {"id": "broken", "structureId": "RootTask", "properties": []},
                    {"structureId": "RootTask", "properties": []},
                    "junk",
                    _native_object("valid", status="active"),
                ],
                "next_cursor": None,
            }
        },
    )

    result = _adapter(provider, mappings=(_native_mapping(),)).items_for_day(TODAY)

    assert result.evaluated == 1
    assert result.deferred == 0
    assert result.malformed == 1
    assert [row["capacities_id"] for row in result.items] == ["valid"]
    assert any("broken" in w and "not plannable" in w for w in result.warnings)
    assert any("<unknown>" in w and "not plannable" in w for w in result.warnings)
    assert any("without an allowlisted structure" in w for w in result.warnings)
    assert not any("Capacities partial" in w for w in result.warnings)


def test_evaluated_objects_are_distinct_from_the_eligible_item_count():
    """Filtered-out objects are evaluated; only eligible ones become items."""
    provider = _native_provider([
        _native_object("active", status="active"),
        _native_object("done", status="done"),
        _native_object("dropped", status="dropped"),
    ])

    result = _adapter(provider, mappings=(_native_mapping(),)).items_for_day(TODAY)

    assert [row["capacities_id"] for row in result.items] == ["active"]
    assert result.evaluated == 3
    assert result.deferred == 0
    assert result.malformed == 0
    assert result.warnings == []


def test_batch_projection_reports_evaluated_and_malformed_counts():
    provider = FakeProvider(_native_structures(), {})

    result = _adapter(provider, mappings=(_native_mapping(),)).items_for_day_from_objects(
        TODAY,
        [
            _native_object("active", status="active"),
            _native_object("done", status="done"),
            {"id": "broken", "structureId": "RootTask", "properties": []},
        ],
    )

    assert result.evaluated == 2
    assert result.deferred == 0
    assert result.malformed == 1


# ---------------------------------------------------------------------------
# Typed tag projection + RootTag catalog (tag-exclusion slice)
# ---------------------------------------------------------------------------
# ASSUMPTION RECORD (do not claim live REST parity): no fixture or recorded
# response in this repo proves that the live REST ``/object`` response carries
# the typed ``tags`` property, or that ``/objects/structure`` returns usable
# ``id``+``title`` rows for ``RootTag``. The fixtures below assert the
# EXPECTED shape from the live contract description
# (``properties.tags.entity = [{"id", "title"}]`` plus a flat top-level
# ``tags`` title array); live parity remains unproven until a recorded
# response lands.

TAG_A = "5a25370b-f9a0-40cf-bc3a-0cab4744913c"
TAG_B = "0d194525-c5a1-4af5-bb62-202b83006b5e"


def _tagged_native_object(object_id, *, tags=None, flat_tags=None, **kwargs):
    obj = _native_object(object_id, status="active", **kwargs)
    if tags is not None:
        obj["properties"]["tags"] = _prop("entity", "entity", tags)
    if flat_tags is not None:
        obj["tags"] = flat_tags
    return obj


def test_typed_entity_tags_project_structured_identity_and_title():
    provider = _native_provider([
        _tagged_native_object(
            "task-1",
            tags=[{"id": TAG_A, "title": "habituals"}],
            flat_tags=["habituals"],
        ),
    ])

    result = _adapter(provider, mappings=(_native_mapping(),)).items_for_day(TODAY)

    row = result.items[0]
    assert row["capacities_tags"] == [
        {"space_id": SPACE, "tag_id": TAG_A, "title": "habituals"},
    ]
    assert "capacities_tags_error" not in row


def test_tag_projection_keeps_the_typed_title_and_ignores_flat_titles():
    """The trap: the generic token helper drops ``title``; identity must come
    from the typed entity pair, never from the flat title array."""
    provider = _native_provider([
        _tagged_native_object(
            "task-1",
            tags=[{"id": TAG_A, "title": "habituals"}],
            flat_tags=["stale display", "habituals"],
        ),
    ])

    row = _adapter(provider, mappings=(_native_mapping(),)).items_for_day(TODAY).items[0]

    assert row["capacities_tags"] == [
        {"space_id": SPACE, "tag_id": TAG_A, "title": "habituals"},
    ]


def test_empty_typed_tags_project_an_empty_list():
    provider = _native_provider([
        _tagged_native_object("task-1", tags=[], flat_tags=[]),
    ])

    row = _adapter(provider, mappings=(_native_mapping(),)).items_for_day(TODAY).items[0]

    assert row["capacities_tags"] == []
    assert "capacities_tags_error" not in row


def test_no_tag_property_at_all_projects_an_empty_list():
    provider = _native_provider([_native_object("task-1", status="active")])

    row = _adapter(provider, mappings=(_native_mapping(),)).items_for_day(TODAY).items[0]

    assert row["capacities_tags"] == []


def test_title_only_flat_tags_are_unusable_not_identity():
    provider = _native_provider([
        _tagged_native_object("task-1", flat_tags=["habituals"]),
    ])

    result = _adapter(provider, mappings=(_native_mapping(),)).items_for_day(TODAY)

    row = result.items[0]
    assert row["capacities_tags"] is None
    assert "title-only" in row["capacities_tags_error"]


def test_malformed_entity_payload_is_unusable_without_dropping_the_row():
    provider = _native_provider([
        _tagged_native_object("task-1", tags=[{"title": "no id"}]),
    ])

    result = _adapter(provider, mappings=(_native_mapping(),)).items_for_day(TODAY)

    assert len(result.items) == 1
    assert result.items[0]["capacities_tags"] is None
    assert "without an id" in result.items[0]["capacities_tags_error"]


def test_non_entity_tags_property_is_unusable():
    provider = _native_provider([
        _tagged_native_object("task-1", flat_tags=["habituals"]),
    ])
    # A label-typed ``tags`` property cannot carry RootTag identity.
    provider._pages[("RootTask", None)]["objects"][0]["properties"]["tags"] = _prop(
        "label", "label", [{"id": "x", "name": "habituals"}]
    )

    row = _adapter(provider, mappings=(_native_mapping(),)).items_for_day(TODAY).items[0]

    assert row["capacities_tags"] is None
    assert "not an entity" in row["capacities_tags_error"]


def test_duplicate_tag_references_deduplicate_by_stable_id():
    provider = _native_provider([
        _tagged_native_object(
            "task-1",
            tags=[
                {"id": TAG_A, "title": "habituals"},
                {"id": TAG_A, "title": "habituals renamed"},
            ],
        ),
    ])

    row = _adapter(provider, mappings=(_native_mapping(),)).items_for_day(TODAY).items[0]

    assert row["capacities_tags"] == [
        {"space_id": SPACE, "tag_id": TAG_A, "title": "habituals"},
    ]


class _RootTagProvider(FakeProvider):
    """FakeProvider whose RootTag listing is scripted per page."""

    def __init__(self, pages):
        super().__init__(_native_structures(), pages)


class _RateLimitedListProvider(_RootTagProvider):
    def list_objects(self, structure_id, cursor=None):
        raise CapacitiesRateLimited("the Capacities API rate limit was exceeded")


def test_root_tag_catalog_paginates_dedupes_and_sorts():
    provider = _RootTagProvider({
        ("RootTag", None): {
            "objects": [
                {"id": "b-tag", "structureId": "RootTag", "title": "zeta"},
                {"id": "a-tag", "structureId": "RootTag", "title": "Alpha"},
            ],
            "next_cursor": "page-2",
        },
        ("RootTag", "page-2"): {
            "objects": [
                {"id": "c-tag", "structureId": "RootTag", "title": "habituals"},
            ],
            "next_cursor": None,
        },
    })

    result = _adapter(provider).list_tags()

    assert result == {
        "status": "complete",
        "space_id": SPACE,
        "tags": [
            {"id": "a-tag", "title": "Alpha"},
            {"id": "c-tag", "title": "habituals"},
            {"id": "b-tag", "title": "zeta"},
        ],
        "warnings": [],
    }
    assert ("RootTag", None) in provider.list_calls
    assert ("RootTag", "page-2") in provider.list_calls


def test_root_tag_catalog_is_bounded_by_max_pages():
    provider = _RootTagProvider({
        ("RootTag", None): {
            "objects": [{"id": "a-tag", "title": "A"}],
            "next_cursor": "more",
        },
    })

    result = _adapter(provider, max_pages=1).list_tags()

    assert result["status"] == "partial"
    assert result["tags"] == [{"id": "a-tag", "title": "A"}]
    assert any("incomplete" in w for w in result["warnings"])


def test_root_tag_catalog_degrades_on_a_rate_limit():
    result = _adapter(_RateLimitedListProvider({})).list_tags()

    assert result["status"] == "partial"
    assert result["tags"] == []
    assert any("rate limit" in w for w in result["warnings"])


def test_root_tag_catalog_skips_malformed_rows_and_reports_partial():
    provider = _RootTagProvider({
        ("RootTag", None): {
            "objects": [
                "junk",
                {"id": "", "title": "no id"},
                {"id": "a-tag", "title": ""},
                {"id": "b-tag", "title": "Beta"},
            ],
            "next_cursor": None,
        },
    })

    result = _adapter(provider).list_tags()

    assert result["status"] == "partial"
    assert result["tags"] == [{"id": "b-tag", "title": "Beta"}]
    assert len(result["warnings"]) >= 2


def test_root_tag_catalog_raises_when_the_first_page_is_malformed():
    provider = _RootTagProvider({("RootTag", None): {"objects": "nope"}})

    with pytest.raises(CapacitiesContractError):
        _adapter(provider).list_tags()


def test_root_tag_catalog_raises_when_the_first_page_fails():
    class _BoomProvider(_RootTagProvider):
        def list_objects(self, structure_id, cursor=None):
            raise RuntimeError("provider down")

    with pytest.raises(RuntimeError):
        _adapter(_BoomProvider({})).list_tags()


def test_root_tag_catalog_stops_on_a_repeated_cursor():
    provider = _RootTagProvider({
        ("RootTag", None): {
            "objects": [{"id": "a-tag", "title": "A"}],
            "next_cursor": "same",
        },
        ("RootTag", "same"): {
            "objects": [{"id": "b-tag", "title": "B"}],
            "next_cursor": "same",
        },
    })

    result = _adapter(provider).list_tags()

    assert result["status"] == "partial"
    assert [t["id"] for t in result["tags"]] == ["a-tag", "b-tag"]
    assert any("cursor" in w for w in result["warnings"])


# ---------------------------------------------------------------------------
# Settings-declared source assignment (``assigned_structures``)
# ---------------------------------------------------------------------------
# The builder resolves the settings/mapping precedence and hands the adapter a
# pre-resolved ``StructureMapping.assigned_property``: the raw property ID whose
# boolean ``true`` means "assigned at TDTB level", with ``true`` implied. The
# adapter reads that field INSTEAD of ``assignment_property`` /
# ``assignment_values`` — a boolean payload needs no new reading logic — while
# keeping the three-valued source signal: a present ``true`` is a definitive
# positive, a present ``false`` is a definitive negative (the property yields
# no tokens), and a MISSING property stays neutral rather than negative. The
# declaration additionally must not change which structures are enumerated.

SETTINGS_ASSIGNED_PROPERTY = "settings-assigned"
MAPPING_ASSIGNED_PROPERTY = "tdtb"
OVERRIDE_STRUCTURE = "custom-project"


def _override_structures():
    """Structure definitions where each row also carries the boolean marker a
    settings declaration would name."""
    return [
        {
            **row,
            "propertyDefinitions": [
                *row["propertyDefinitions"],
                _definition(SETTINGS_ASSIGNED_PROPERTY, "boolean"),
            ],
        }
        for row in _structures()
    ]


def _override_provider(objects):
    return FakeProvider(
        _override_structures(),
        {(OVERRIDE_STRUCTURE, None): {"objects": list(objects), "next_cursor": None}},
    )


def _override_mapping(**overrides):
    base = dict(
        structure_id=OVERRIDE_STRUCTURE,
        title_property="title",
        open_status_property="state",
        open_status_values=frozenset({"active"}),
        assigned_property=SETTINGS_ASSIGNED_PROPERTY,
    )
    base.update(overrides)
    return StructureMapping(**base)


def _override_object(object_id, properties):
    return _object(
        object_id,
        OVERRIDE_STRUCTURE,
        {
            "title": _prop("title", "title", {"value": "Declared row"}),
            **properties,
        },
    )


def _override_adapter(provider, mapping, **kwargs):
    return _adapter(
        provider,
        mappings=(_native_mapping(deadline_property=None), mapping),
        **kwargs,
    )


def test_declared_assignment_property_boolean_true_is_source_assigned():
    # The structure is enumerated because it is Active-enabled, NOT because of
    # the declaration: the operator chose "assignment check only", so the
    # declaration must not change which structures are enumerated. The row
    # carries an open status because a closed/absent mapped status blocks the
    # evaluator before any assignment signal is considered.
    provider = _override_provider([
        _override_object(
            "project-1",
            {
                "state": _prop(
                    "label", "label", [{"id": "active", "name": "Active"}]
                ),
                SETTINGS_ASSIGNED_PROPERTY: _prop("boolean", "boolean", True),
            },
        ),
    ])

    result = _override_adapter(
        provider, _override_mapping(), assignment_settings=_active_settings()
    ).items_for_day(TODAY)

    assert [row["capacities_id"] for row in result.items] == ["project-1"]
    assert result.items[0]["capacities_assignment"] == {
        "mode": "assigned",
        "reasons": ["source-assigned"],
        "source_assigned": True,
        "excluded": False,
    }


def test_declared_assignment_property_overrides_the_mapping_declaration():
    # The mapping's own marker MATCHES (``tdtb`` is ``yes``), but the
    # settings-declared boolean is ``false``: the declaration wins for this
    # structure, so the object is not source-assigned and nothing else pulls
    # it in.
    provider = _override_provider([
        _override_object(
            "project-1",
            {
                MAPPING_ASSIGNED_PROPERTY: _prop(
                    "label", "label", [{"id": "yes", "name": "TDTB"}]
                ),
                SETTINGS_ASSIGNED_PROPERTY: _prop("boolean", "boolean", False),
            },
        ),
    ])
    mapping = _override_mapping(
        assignment_property=MAPPING_ASSIGNED_PROPERTY,
        assignment_values=frozenset({"yes"}),
    )

    result = _override_adapter(provider, mapping).items_for_day(TODAY)

    assert result.items == []


def test_mapping_declaration_still_applies_when_no_override_is_resolved():
    # The same object WITHOUT a resolved declaration: the mapping's own marker
    # is the assignment signal, exactly as before this slice.
    provider = _override_provider([
        _override_object(
            "project-1",
            {
                "state": _prop(
                    "label", "label", [{"id": "active", "name": "Active"}]
                ),
                MAPPING_ASSIGNED_PROPERTY: _prop(
                    "label", "label", [{"id": "yes", "name": "TDTB"}]
                ),
                SETTINGS_ASSIGNED_PROPERTY: _prop("boolean", "boolean", False),
            },
        ),
    ])
    mapping = _override_mapping(
        assigned_property=None,
        assignment_property=MAPPING_ASSIGNED_PROPERTY,
        assignment_values=frozenset({"yes"}),
    )

    result = _override_adapter(provider, mapping).items_for_day(TODAY)

    assert [row["capacities_id"] for row in result.items] == ["project-1"]
    assert result.items[0]["capacities_assignment"]["source_assigned"] is True


def test_declared_assignment_property_absent_stays_neutral_not_negative():
    # Active-enabled so the row is projected by the custom Auto pull; the
    # source signal must then read neutral (``None``), never ``False``.
    provider = _override_provider([
        _override_object(
            "project-1",
            {"state": _prop("label", "label", [{"id": "active", "name": "Active"}])},
        ),
    ])

    result = _override_adapter(
        provider,
        _override_mapping(),
        assignment_settings=_active_settings(),
    ).items_for_day(TODAY)

    assert [row["capacities_id"] for row in result.items] == ["project-1"]
    assert result.items[0]["capacities_assignment"] == {
        "mode": "auto",
        "reasons": ["auto-custom-status-active"],
        "source_assigned": None,
        "excluded": False,
    }


def test_declared_assignment_property_false_is_negative_not_neutral():
    provider = _override_provider([
        _override_object(
            "project-1",
            {
                "state": _prop("label", "label", [{"id": "active", "name": "Active"}]),
                SETTINGS_ASSIGNED_PROPERTY: _prop("boolean", "boolean", False),
            },
        ),
    ])

    result = _override_adapter(
        provider,
        _override_mapping(),
        assignment_settings=_active_settings(),
    ).items_for_day(TODAY)

    assert [row["capacities_id"] for row in result.items] == ["project-1"]
    assignment = result.items[0]["capacities_assignment"]
    assert assignment["source_assigned"] is False
    assert assignment["mode"] == "auto"


@pytest.mark.parametrize("declared", [None, SETTINGS_ASSIGNED_PROPERTY])
def test_declared_assignment_property_does_not_change_enumeration(declared):
    # The structure has no assignment property and is not Active-enabled, so
    # it is never enumerated. Resolving a declaration for it must not add a
    # listing request: the operator chose "assignment check only".
    provider = _override_provider([])

    result = _override_adapter(provider, _override_mapping(assigned_property=declared)).items_for_day(TODAY)

    assert provider.list_calls == [("RootTask", None)]
    assert result.items == []


# -- a declaration the structure does not define -----------------------------
# The declaration names a raw property id without the structure's own
# definition backing it. Left unchecked it would read neutral forever, so a
# mistyped declaration would silently never mark anything assigned. The
# adapter reports the undeclared property ONCE per (structure, property) and
# then ignores the declaration: the mapping's own marker applies as the
# fallback, and the bad declaration never drops the structure from the read.

UNDEFINED_ASSIGNED_PROPERTY = "ghost-assigned"


def _declaration_warning(structure_id=OVERRIDE_STRUCTURE):
    return (
        "ignored Capacities assignment declaration for structure "
        f"{structure_id!r}: unknown property {UNDEFINED_ASSIGNED_PROPERTY!r}"
    )


def test_unknown_declared_assignment_property_warns_and_falls_back_to_mapping():
    # The declared property is not in the structure definitions, so the
    # mapping's own ``tdtb`` marker is the assignment signal that applies and
    # the object is still enumerated and projected exactly as before.
    provider = _override_provider([
        _override_object(
            "project-1",
            {
                "state": _prop("label", "label", [{"id": "active", "name": "Active"}]),
                MAPPING_ASSIGNED_PROPERTY: _prop(
                    "label", "label", [{"id": "yes", "name": "TDTB"}]
                ),
            },
        ),
    ])
    mapping = _override_mapping(
        assigned_property=UNDEFINED_ASSIGNED_PROPERTY,
        assignment_property=MAPPING_ASSIGNED_PROPERTY,
        assignment_values=frozenset({"yes"}),
    )

    result = _override_adapter(provider, mapping).items_for_day(TODAY)

    assert _declaration_warning() in result.warnings
    assert ("custom-project", None) in provider.list_calls
    assert [row["capacities_id"] for row in result.items] == ["project-1"]
    assert result.items[0]["capacities_assignment"] == {
        "mode": "assigned",
        "reasons": ["source-assigned"],
        "source_assigned": True,
        "excluded": False,
    }


def test_known_declared_assignment_property_emits_no_diagnostic():
    # A declaration the structure DOES define stays authoritative and must not
    # be reported: a false positive here would make every valid declaration
    # look broken to the operator.
    provider = _override_provider([
        _override_object(
            "project-1",
            {
                "state": _prop("label", "label", [{"id": "active", "name": "Active"}]),
                SETTINGS_ASSIGNED_PROPERTY: _prop("boolean", "boolean", True),
            },
        ),
    ])

    result = _override_adapter(
        provider, _override_mapping(), assignment_settings=_active_settings()
    ).items_for_day(TODAY)

    assert result.warnings == []
    assert result.items[0]["capacities_assignment"]["source_assigned"] is True


def test_unknown_declaration_falls_back_to_the_mapping_declaration():
    # The object's ``tdtb`` label is ``no``: the mapping's own marker is
    # present and does not match, so the signal is a definitive negative. A
    # bogus declaration left in place would have read the absent ghost
    # property and reported neutral (``None``) instead.
    provider = _override_provider([
        _override_object(
            "project-1",
            {
                "state": _prop("label", "label", [{"id": "active", "name": "Active"}]),
                MAPPING_ASSIGNED_PROPERTY: _prop(
                    "label", "label", [{"id": "no", "name": "No"}]
                ),
            },
        ),
    ])
    mapping = _override_mapping(
        assigned_property=UNDEFINED_ASSIGNED_PROPERTY,
        assignment_property=MAPPING_ASSIGNED_PROPERTY,
        assignment_values=frozenset({"yes"}),
    )

    result = _override_adapter(
        provider, mapping, assignment_settings=_active_settings()
    ).items_for_day(TODAY)

    assert [row["capacities_id"] for row in result.items] == ["project-1"]
    assert result.items[0]["capacities_assignment"]["source_assigned"] is False
    assert result.warnings == [_declaration_warning()]


def test_unknown_declaration_warns_once_for_multiple_projected_objects():
    # A space can hold dozens of objects of one structure; the diagnostic is
    # a configuration fact, so it is reported once per (structure, property)
    # rather than once per projected object.
    provider = _override_provider([
        _override_object(
            f"project-{index}",
            {
                "state": _prop("label", "label", [{"id": "active", "name": "Active"}]),
                MAPPING_ASSIGNED_PROPERTY: _prop(
                    "label", "label", [{"id": "yes", "name": "TDTB"}]
                ),
            },
        )
        for index in range(3)
    ])
    mapping = _override_mapping(
        assigned_property=UNDEFINED_ASSIGNED_PROPERTY,
        assignment_property=MAPPING_ASSIGNED_PROPERTY,
        assignment_values=frozenset({"yes"}),
    )

    result = _override_adapter(provider, mapping).items_for_day(TODAY)

    assert len(result.items) == 3
    assert result.warnings.count(_declaration_warning()) == 1
