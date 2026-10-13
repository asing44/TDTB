"""Named skipped Capacities objects in the direct-intake block (test-first).

The cockpit warning ``skipped Capacities object <uuid> — not plannable`` names
only a bare id, so the operator cannot tell which task it is. This slice adds a
structured ``skipped`` list to the additive ``capacities_intake`` block: one
entry per malformed object with a safe display title, a small fixed reason, the
existing raw detail, and the object's structure id/name.

Everything here is offline: the unit tests use the existing adapter fakes, and
the endpoint tests reuse the S3 direct-intake builders. No provider,
credential, or network read is involved.
"""
from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))

import capacities_intake as ci  # noqa: E402
import main as main_mod  # noqa: E402
from capacities_adapter import SkippedObject  # noqa: E402
from tests.test_capacities_adapter import (  # noqa: E402
    FakeProvider,
    SPACE,
    TODAY,
    _adapter,
    _object,
    _prop,
    _structures,
)
from tests.test_plan_inputs_sources import (  # noqa: E402
    DIRECT_ROOT,
    _CallRecorder,
    _direct_client,
    _publish_direct,
    _save_direct_source,
    _set_sources,
    _write_fresh_artifact,
    vault,  # noqa: E402,F401  (fixture)
)

CONTRACT_KEYS = {
    "identity",
    "title",
    "reason",
    "detail",
    "structure_id",
    "structure_name",
}


def _task(object_id, properties):
    return _object(object_id, "RootTask", properties)


def _base_properties(**overrides):
    properties = {
        "title": _prop("title", "title", {"value": "Run report"}),
        "assigned": _prop("boolean", "boolean", True),
        "status": _prop("label", "label", [{"id": "open", "name": "Open"}]),
    }
    properties.update(overrides)
    return properties


def _direct_object_raw(object_id, properties):
    return {
        "id": object_id,
        "structureId": DIRECT_ROOT,
        "properties": properties,
    }


# ---------------------------------------------------------------------------
# (a) unit level: reason, title, detail per malformed cause
# ---------------------------------------------------------------------------

def test_empty_title_object_is_skipped_with_no_title():
    obj = _task(
        "empty-title",
        _base_properties(title=_prop("title", "title", {"value": ""})),
    )
    provider = FakeProvider(_structures(), {})

    result = _adapter(provider).items_for_day_from_objects(TODAY, [obj])

    assert [entry.identity for entry in result.skipped] == ["empty-title"]
    entry = result.skipped[0]
    assert entry.title is None
    assert entry.reason == "No title"
    assert entry.detail == "property 'title' has no usable value"
    assert entry.structure_id == "RootTask"
    assert entry.structure_name == "Task"


def test_null_duration_keeps_the_readable_title():
    obj = _task(
        "null-duration",
        _base_properties(
            title=_prop("title", "title", {"value": "3D/W A on {date:LL}"}),
            duration=_prop("number", "number", {"value": None}),
        ),
    )
    provider = FakeProvider(_structures(), {})

    result = _adapter(provider).items_for_day_from_objects(TODAY, [obj])

    entry = result.skipped[0]
    assert entry.identity == "null-duration"
    assert entry.title == "3D/W A on {date:LL}"
    assert entry.reason == "Duration is not a number"
    assert "not numeric" in entry.detail
    assert entry.structure_id == "RootTask"
    assert entry.structure_name == "Task"


def test_invalid_start_date_is_skipped_as_a_start_date_problem():
    obj = _task(
        "bad-date",
        _base_properties(
            due=_prop("date", "date", {"start": "not-a-date"}),
        ),
    )
    provider = FakeProvider(_structures(), {})

    result = _adapter(provider).items_for_day_from_objects(TODAY, [obj])

    entry = result.skipped[0]
    assert entry.title == "Run report"
    assert entry.reason == "Start date is not valid"
    assert "invalid start" in entry.detail


def test_unreadable_payload_is_skipped_as_unreadable_object():
    obj = _task("bad-shape", ["not", "a", "property", "map"])
    provider = FakeProvider(_structures(), {})

    result = _adapter(provider).items_for_day_from_objects(TODAY, [obj])

    entry = result.skipped[0]
    assert entry.reason == "Unreadable object"
    assert entry.title is None


def test_skipped_list_is_empty_when_every_object_projects():
    obj = _task("valid", _base_properties())
    provider = FakeProvider(_structures(), {})

    result = _adapter(provider).items_for_day_from_objects(TODAY, [obj])

    assert result.skipped == ()


def test_title_reader_never_raises_on_hostile_property_shapes():
    obj = _task(
        "hostile",
        _base_properties(title={"type": "title"}),
    )
    provider = FakeProvider(_structures(), {})

    result = _adapter(provider).items_for_day_from_objects(TODAY, [obj])

    assert result.skipped[0].title is None
    assert result.skipped[0].reason == "Unreadable object"


# ---------------------------------------------------------------------------
# (b) the /plan-inputs block carries the skipped contract
# ---------------------------------------------------------------------------

def test_direct_block_includes_named_skipped_objects(vault):  # noqa: F811
    _set_sources(mode="artifact", capacities_intake="direct")
    _save_direct_source()
    _publish_direct([
        _direct_object_raw("obj-ok", {
            "title": {"type": "title", "title": {"value": "Keep me"}},
            "assigned": {"type": "boolean", "boolean": True},
            "status": {"type": "label", "label": [{"id": "active", "name": "Active"}]},
        }),
        _direct_object_raw("obj-empty", {
            "title": {"type": "title", "title": {"value": ""}},
            "assigned": {"type": "boolean", "boolean": True},
            "status": {"type": "label", "label": [{"id": "active", "name": "Active"}]},
        }),
        _direct_object_raw("obj-date", {
            "title": {"type": "title", "title": {"value": "Fix date"}},
            "assigned": {"type": "boolean", "boolean": True},
            "status": {"type": "label", "label": [{"id": "active", "name": "Active"}]},
            "due": {"type": "date", "date": {"start": "bogus"}},
        }),
    ])
    client = _direct_client(
        build_clients=_CallRecorder(), build_capacities=_CallRecorder(),
    )

    body = client.get("/plan-inputs").json()

    block = body["capacities_intake"]
    assert len(block["skipped"]) == 2
    assert set(block["skipped"][0]) == CONTRACT_KEYS
    by_identity = {entry["identity"]: entry for entry in block["skipped"]}
    assert by_identity["obj-empty"] == {
        "identity": "obj-empty",
        "title": None,
        "reason": "No title",
        "detail": "property 'title' has no usable value",
        "structure_id": DIRECT_ROOT,
        "structure_name": "RootTask",
    }
    assert by_identity["obj-date"]["title"] == "Fix date"
    assert by_identity["obj-date"]["reason"] == "Start date is not valid"
    assert by_identity["obj-date"]["structure_id"] == DIRECT_ROOT
    assert by_identity["obj-date"]["structure_name"] == "RootTask"


# ---------------------------------------------------------------------------
# (c) cap at 50 and stable sort order
# ---------------------------------------------------------------------------

def _skipped(identity):
    return SkippedObject(
        identity=identity,
        title=None,
        reason="No title",
        detail="property 'title' has no usable value",
        structure_id="RootTask",
        structure_name="Task",
    )


def test_skipped_entries_are_sorted_by_identity_and_capped_at_50():
    coverage = ci.DirectCoverage(
        skipped=tuple(_skipped(f"obj-{index:03d}") for index in range(80, 0, -1))
    )

    block = main_mod._capacities_intake_block(
        {"assigned": [], "suggested": []}, coverage, [], None
    )

    identities = [entry["identity"] for entry in block["skipped"]]
    assert len(identities) == 50
    assert identities == sorted(identities)
    assert identities[0] == "obj-001"
    assert identities[-1] == "obj-050"


# ---------------------------------------------------------------------------
# (d) legacy intake keeps its shape: no block, no skipped
# ---------------------------------------------------------------------------

def test_legacy_intake_response_has_no_skipped_surface(vault):  # noqa: F811
    _set_sources(mode="artifact", capacities_intake="legacy")
    _write_fresh_artifact([
        {
            "name": "Artifact cap",
            "source": "capacities",
            "path": "capacities://Artifact cap",
            "identity": "capacities:space-1:RootTask:obj-a",
            "assigned": True,
            "duration_minutes": 30,
            "blocks": 1,
        },
    ])
    client = _direct_client(
        build_clients=_CallRecorder(), build_capacities=_CallRecorder(),
    )

    body = client.get("/plan-inputs").json()

    assert "capacities_intake" not in body
    assert "skipped" not in body
