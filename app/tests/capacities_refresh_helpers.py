"""Shared fakes and builders for the U2 Capacities refresh tests.

These live outside ``test_capacities_refresh.py`` so the route tests can import
the same fakes without importing a collected test module (which pytest loads
under a ``tests.``-qualified name). Everything here is local and fake: a
deterministic provider, an injected monotonic clock and sleeper (no real
``time.sleep``), a ``tmp_path`` state root, and no credential, provider, or
``$HOME`` read.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import capacities_adapter as ca  # noqa: E402
import capacities_builder as cb  # noqa: E402


SPACE = "space-1"
#: The adapter's contract requires the canonical native structure to be mapped
#: explicitly, so the primary test type is ``RootTask``.
PRIMARY = "RootTask"
TYPES = (PRIMARY, "T2")


class Clock:
    """Deterministic monotonic clock (seconds)."""

    def __init__(self, now: float = 0.0) -> None:
        self.now = float(now)

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += float(seconds)


class Sleeper:
    """Deterministic sleeper: records the request, advances the fake clock."""

    def __init__(self, clock: Clock) -> None:
        self.clock = clock
        self.calls: list[float] = []
        self.on_sleep = None

    def __call__(self, seconds: float, cancel_event) -> None:
        self.calls.append(float(seconds))
        if self.on_sleep is not None:
            self.on_sleep(seconds)
        self.clock.advance(seconds)


def _definition(prop_id: str, kind: str, labels=None) -> dict:
    row = {"id": prop_id, "type": kind}
    if labels is not None:
        row["labelSet"] = [{"id": i, "name": n} for i, n in labels]
    return row


def _structures(*type_ids: str) -> list[dict]:
    return [
        {
            "id": type_id,
            "title": type_id,
            "propertyDefinitions": [
                _definition("title", "title"),
                _definition("assigned", "boolean"),
                _definition("status", "label", [("active", "Active"), ("done", "Done")]),
            ],
        }
        for type_id in type_ids
    ]


def _mapping(type_id: str) -> ca.StructureMapping:
    return ca.StructureMapping(
        structure_id=type_id,
        assignment_property="assigned",
        assignment_values=frozenset({"true"}),
        open_status_property="status",
        open_status_values=frozenset({"active"}),
        title_property="title",
    )


def _row(object_id: str, type_id: str) -> dict:
    return {"id": object_id, "structureId": type_id, "title": object_id}


def _content(object_id: str, type_id: str, *, assigned: bool = True) -> dict:
    return {
        "id": object_id,
        "structureId": type_id,
        "properties": {
            "title": {"type": "title", "title": {"value": object_id.title()}},
            "assigned": {"type": "boolean", "boolean": assigned},
            "status": {"type": "label", "label": [{"id": "active", "name": "Active"}]},
        },
    }


class FakeProvider:
    """Deterministic provider with call counters and injectable failure hooks."""

    def __init__(self, structures, pages, objects=None) -> None:
        self._structures = structures
        self._pages = dict(pages)
        self._objects = dict(objects or {})
        self.fetch_calls = 0
        self.list_calls: list[tuple[str, object]] = []
        self.get_calls: list[str] = []
        self.on_fetch = None
        self.on_list = None
        self.on_get = None
        self.list_error = None
        self.get_error = None
        self.fail_gets: dict[str, Exception] = {}

    def fetch_structures(self):
        self.fetch_calls += 1
        if self.on_fetch is not None:
            self.on_fetch()
        if self.list_error is not None:
            raise self.list_error
        return self._structures

    def list_objects(self, structure_id, cursor=None):
        self.list_calls.append((structure_id, cursor))
        if self.on_list is not None:
            self.on_list(structure_id, cursor)
        if self.list_error is not None:
            raise self.list_error
        page = self._pages.get((structure_id, cursor))
        if isinstance(page, Exception):
            raise page
        return page if page is not None else {"objects": [], "next_cursor": None}

    def get_object(self, object_id):
        self.get_calls.append(object_id)
        if self.on_get is not None:
            self.on_get(object_id)
        if object_id in self.fail_gets:
            raise self.fail_gets[object_id]
        if self.get_error is not None:
            raise self.get_error
        return self._objects[object_id]

    def patch_object(self, object_id, properties):
        raise AssertionError("the refresh coordinator must never write")


def _store(tmp_path):
    return cb.build_refresh_state(
        tmp_path / "vault",
        SPACE,
        cb.CapacitiesBuilderConfig(refresh_state_path=tmp_path / "state"),
    )


def _paged_provider(*, objects, structures=None, pages=None):
    """A provider whose types each list one page unless ``pages`` overrides."""
    structures = structures if structures is not None else _structures(*TYPES)
    page_map = dict(pages or {})
    content = {}
    for type_id, ids in objects.items():
        if (type_id, None) not in page_map:
            page_map[(type_id, None)] = {
                "objects": [_row(i, type_id) for i in ids],
                "next_cursor": None,
            }
        for object_id in ids:
            content[object_id] = _content(object_id, type_id)
    return FakeProvider(structures, page_map, content)
