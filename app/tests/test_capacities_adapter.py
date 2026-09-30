"""Contract tests for the application-owned Capacities source adapter."""
from __future__ import annotations

from datetime import date, timedelta
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from capacities_adapter import (  # noqa: E402
    CapacitiesAdapter,
    CapacitiesConfig,
    CapacitiesContractError,
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


def test_missing_space_identity_is_skipped_fail_closed():
    provider = _native_provider([{
        "id": "missing-space",
        "structureId": "RootTask",
        "properties": {
            "title": _prop("title", "title", {"value": "Missing space"}),
            "status": _prop("label", "label", [{"id": "active"}]),
        },
    }])

    result = _adapter(provider, mappings=(_native_mapping(),)).items_for_day(TODAY)

    assert result.items == []
    assert any("missing-space" in warning for warning in result.warnings)


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
