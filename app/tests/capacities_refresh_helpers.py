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
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import capacities_adapter as ca  # noqa: E402
import capacities_builder as cb  # noqa: E402
import capacities_read_source as rs  # noqa: E402
import capacities_refresh as rr  # noqa: E402


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
        #: Rate-limit observers the seam forwarded (names only, never used).
        self.observers: list[object] = []

    def set_rate_observer(self, observer) -> None:
        self.observers.append(observer)

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


# ---------------------------------------------------------------------------
# MCP read plan U5 - split-source doubles
# ---------------------------------------------------------------------------


class FakeMcp:
    """An ``McpProvider`` double over the same content the REST double serves.

    ``behaviors`` is consumed one entry per MCP call, so a failure sequence is
    deterministic in MCP call order whatever index each call claimed. Content
    is already in the REST shape: the real provider normalizes it (KTD5), and
    these tests are about pacing, failover, and parity rather than that
    normalization.
    """

    def __init__(
        self,
        contents=None,
        *,
        begin_error: BaseException | None = None,
        behaviors=None,
    ) -> None:
        self._lock = threading.Lock()
        self._contents = dict(contents or {})
        self.behaviors: list[BaseException | None] = list(behaviors or [])
        self.begin_error = begin_error
        self.begin_calls = 0
        self.end_calls = 0
        self.calls: list[tuple[str, str | None]] = []
        #: Fires after each MCP read, which is how a test gates the REST worker
        #: behind the first MCP claim without a sleep.
        self.on_get = None

    def begin_run(self) -> None:
        self.begin_calls += 1
        if self.begin_error is not None:
            raise self.begin_error

    def end_run(self) -> None:
        self.end_calls += 1

    def get_object(self, object_id: str, *, structure_id: str | None = None) -> dict:
        with self._lock:
            self.calls.append((object_id, structure_id))
            behavior = self.behaviors.pop(0) if self.behaviors else None
        try:
            if behavior is not None:
                raise behavior
            return self._contents[object_id]
        finally:
            if self.on_get is not None:
                self.on_get(object_id)

    @property
    def ids(self) -> list[str]:
        with self._lock:
            return [object_id for object_id, _ in self.calls]


class SplitProvider:
    """The U6 provider wiring, built from the production pieces.

    ``PacedProvider`` can only build the split provider's two paced callables
    once the coordinator's pacer exists, so :meth:`bind` is the test's stand-in
    for the builder: it constructs the real ``ReadSourceSplitter`` around the
    REST double with :func:`capacities_refresh.paced_content_callables` and the
    coordinator's own cancel event, exactly as U6 must. ``primed`` records what
    the coordinator primed, in order.
    """

    #: The real splitter's declaration, mirrored so the seam skips content
    #: pacing from construction time onwards (R4).
    paces_content_reads = True

    def __init__(self, rest, mcp=None) -> None:
        self.rest = rest
        self.mcp = mcp
        self.splitter = None
        self.primed: list[tuple[str, str | None]] = []
        self._pacer = None
        self._cancel_event = None

    # -- wiring ---------------------------------------------------------

    def bind(self, pacer, cancel_event=None) -> None:
        """Build the real splitter from the coordinator's pacer and event."""
        self._pacer = pacer
        self._cancel_event = (
            cancel_event if cancel_event is not None else self._cancel_event
        )
        content_reader, mcp_pace = rr.paced_content_callables(pacer, self.rest)
        self.splitter = rs.ReadSourceSplitter(
            self.rest,
            mcp=self.mcp,
            content_reader=content_reader,
            mcp_pace=mcp_pace,
            cancel_event=self._cancel_event,
        )

    def set_cancel_event(self, cancel_event) -> None:
        """The U6 hook ``PacedProvider`` forwards to (see its docstring)."""
        self._cancel_event = cancel_event
        if self.splitter is not None:
            self.splitter.cancel_event = cancel_event
            self.splitter.queue.cancel_event = cancel_event

    def _bound(self):
        assert self.splitter is not None, "bind() the split provider first"
        return self.splitter

    # -- provider surface ----------------------------------------------

    @property
    def base_url(self):
        return self.rest.base_url

    @property
    def supports_object_listing(self):
        return self.rest.supports_object_listing

    def set_rate_observer(self, observer) -> None:
        self.rest.set_rate_observer(observer)

    def fetch_structures(self):
        return self.rest.fetch_structures()

    def list_objects(self, structure_id, cursor=None):
        return self.rest.list_objects(structure_id, cursor)

    def patch_object(self, object_id, properties):
        return self.rest.patch_object(object_id, properties)

    def get_object(self, object_id, *, for_completion: bool = False):
        return self._bound().get_object(object_id, for_completion=for_completion)

    def prime(self, entries) -> None:
        self.primed.extend(tuple(entry) for entry in entries)
        self._bound().prime(entries)

    def begin_run(self) -> None:
        self._bound().begin_run()

    def end_run(self) -> None:
        self._bound().end_run()

    @property
    def read_source(self) -> str:
        return self.splitter.read_source if self.splitter is not None else "rest"

    def read_counts(self):
        if self.splitter is None:
            return {
                "rest": 0,
                "mcp": 0,
                "fell_back": False,
                "rest_elapsed": 0.0,
                "mcp_elapsed": 0.0,
            }
        return self.splitter.read_counts()

    def close(self) -> None:
        if self.splitter is not None:
            self.splitter.close()
        else:
            self.rest.close()


def split_provider(*, objects, structures=None, behaviors=None, begin_error=None):
    """A REST double plus an MCP double over the same objects, as one provider."""
    rest = _paged_provider(objects=objects, structures=structures)
    contents = {}
    for type_id, ids in objects.items():
        for object_id in ids:
            contents[object_id] = _content(object_id, type_id)
    mcp = FakeMcp(contents, behaviors=behaviors, begin_error=begin_error)
    return SplitProvider(rest, mcp), rest, mcp


def gate_rest_behind_mcp(rest, mcp) -> None:
    """Hold the REST worker's first read until the MCP worker has read once.

    Without this, a fast REST worker can drain a small primed batch before the
    MCP thread ever claims, which would make "both sources served a read" a
    race instead of a fact. The wait is bounded, so a run that never reaches
    MCP still finishes.
    """
    first_mcp = threading.Event()
    mcp.on_get = lambda object_id: first_mcp.set()
    rest.on_get = lambda object_id: first_mcp.wait(2.0)
