"""Refresh wiring for page-body child hiding (test-first).

The adapter hides a Task embedded in an admitted parent's page body (pinned in
``test_capacities_embedded_children.py``), and the write path that proves it is
this one: the refresh must actually *fetch* a page body for the two embedding
parent structures, cache it with the content it belongs to, and reuse it when
the content is reused. A page body is a display detail, so every refresh-level
behaviour here is fail-open:

* only Project and Continual spend a read on a page body;
* a body fetched fresh is cached inside the content record, so a later refresh
  that reuses that content reuses the body with no second read;
* a failed body read leaves the object read intact, hides nothing, and adds
  exactly one aggregated warning per refresh;
* a run with no page-body source at all is not a failure and says nothing.

Everything is local and fake: no credential, no provider, no live MCP call.
"""
from __future__ import annotations

from datetime import date
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

import capacities_adapter as ca  # noqa: E402
import capacities_embedded as ce  # noqa: E402
import capacities_refresh as rr  # noqa: E402

from capacities_refresh_helpers import (  # noqa: E402
    SPACE,
    Clock,
    FakeProvider,
    Sleeper,
    split_provider,
    _content,
    _mapping,
    _row,
    _store,
    _structures,
)

DAY = date(2026, 11, 20)
TASKS = "RootTask"
PROJECT = "0d194525-c5a1-4af5-bb62-202b83006b5e"
CONTINUAL = "e16f8eeb-1d66-4f21-8d3a-adaeff0a75be"
OTHER = "T2"

PARENT_ID = "73372e1a-0b2f-40ad-9f0d-fcae292fa265"
CONTINUAL_ID = "9c4a4a8e-2a6a-4a4a-8a4a-000000000002"
CHILD_ID = "85803b64-d85a-4fac-99b3-f0b214641e46"
OTHER_ID = "11111111-2222-3333-4444-555555555555"


def _body(*child_ids: str) -> dict:
    """One page body embedding each child in an EntityBlock."""
    return {
        "body": [
            {"type": "EntityBlock", "entityId": child_id} for child_id in child_ids
        ]
    }


class BlocksProvider(FakeProvider):
    """The refresh's fake provider, plus a page-body seam and its call log.

    ``blocks_error`` makes every page-body read fail, which is the fail-open
    path; an object with no entry in ``blocks`` answers an empty body (a real
    object with nothing embedded in it).
    """

    def __init__(
        self,
        structures,
        pages,
        objects=None,
        *,
        blocks=None,
        blocks_error=None,
    ) -> None:
        super().__init__(structures, pages, objects)
        self._blocks = dict(blocks or {})
        self.blocks_error = blocks_error
        self.blocks_calls: list[str] = []

    def get_object_blocks(self, object_id):
        self.blocks_calls.append(object_id)
        if self.blocks_error is not None:
            raise self.blocks_error
        return {"blocks": self._blocks.get(object_id, {})}


def _provider(
    objects: dict[str, list[str]],
    *,
    structures=None,
    blocks=None,
    blocks_error=None,
    titles=None,
    unassigned=(),
):
    """A two-fake provider whose types each list one page."""
    structures = structures if structures is not None else [*objects]
    pages = {}
    content = {}
    for type_id, ids in objects.items():
        pages[(type_id, None)] = {
            "objects": [_row(object_id, type_id) for object_id in ids],
            "next_cursor": None,
        }
        for object_id in ids:
            record = _content(
                object_id, type_id, assigned=object_id not in set(unassigned)
            )
            if titles and object_id in titles:
                record["properties"]["title"]["title"]["value"] = titles[object_id]
            content[object_id] = record
    return BlocksProvider(
        _structures(*structures),
        pages,
        content,
        blocks=blocks,
        blocks_error=blocks_error,
    )


def _coordinator(tmp_path, provider, *, mappings):
    clock = Clock()
    store = _store(tmp_path)
    return (
        rr.RefreshCoordinator(
            root=tmp_path / "state",
            store=store,
            provider=provider,
            space_id=SPACE,
            mappings=tuple(_mapping(type_id) for type_id in mappings),
            revision_supplier=lambda: 1,
            scope_key="all",
            max_pages=20,
            clock=clock,
            sleeper=Sleeper(clock),
        ),
        store,
    )


def _adapter(provider, *, mappings):
    return ca.CapacitiesAdapter(
        provider,
        ca.CapacitiesConfig(SPACE, tuple(_mapping(type_id) for type_id in mappings)),
    )


def _cached_objects(store):
    snapshot = store.load_snapshot("all")
    assert snapshot is not None
    return [store.get(member.object_id, member.type_key).content for member in snapshot.members]


def _plan(provider, store, *, mappings):
    """The rows a plan read would project from the generation just published."""
    return _adapter(provider, mappings=mappings).items_for_day_from_objects(
        DAY, _cached_objects(store)
    )


def _mappings():
    return (TASKS, PROJECT, CONTINUAL)


def _page_warnings(status):
    return [line for line in status["warnings"] if "could not be read" in line]


# ---------------------------------------------------------------------------
# Which structures spend a page-body read
# ---------------------------------------------------------------------------


def test_page_bodies_are_read_only_for_project_and_continual(tmp_path):
    provider = _provider(
        {
            TASKS: [CHILD_ID],
            PROJECT: [PARENT_ID],
            CONTINUAL: [CONTINUAL_ID],
            OTHER: [OTHER_ID],
        },
        blocks={PARENT_ID: _body(CHILD_ID), CONTINUAL_ID: _body()},
    )
    coordinator, store = _coordinator(
        tmp_path, provider, mappings=(TASKS, PROJECT, CONTINUAL, OTHER)
    )

    coordinator.start()
    status = coordinator.wait(timeout=5)

    assert status["phase"] == "complete", status
    assert set(provider.blocks_calls) == {PARENT_ID, CONTINUAL_ID}
    assert store.get(PARENT_ID, PROJECT).content["blocks"] == _body(CHILD_ID)
    assert store.get(CONTINUAL_ID, CONTINUAL).content["blocks"] == _body()
    # A task row and a non-parent type never cost a page-body read at all.
    assert CHILD_ID not in provider.blocks_calls
    assert OTHER_ID not in provider.blocks_calls
    assert "blocks" not in store.get(OTHER_ID, OTHER).content
    assert "blocks" not in store.get(CHILD_ID, TASKS).content
    assert OTHER_ID in provider.get_calls


def test_a_reused_content_record_keeps_its_cached_blocks(tmp_path):
    provider = _provider(
        {TASKS: [CHILD_ID], PROJECT: [PARENT_ID]},
        blocks={PARENT_ID: _body(CHILD_ID)},
    )
    coordinator, store = _coordinator(tmp_path, provider, mappings=(TASKS, PROJECT))
    # Warm cache: both objects were read on an earlier refresh, and that read
    # already carried the parent's body.
    store.put(CHILD_ID, TASKS, _content(CHILD_ID, TASKS))
    store.put(
        PARENT_ID,
        PROJECT,
        {**_content(PARENT_ID, PROJECT), "blocks": _body(CHILD_ID)},
    )

    coordinator.start()
    status = coordinator.wait(timeout=5)

    assert status["phase"] == "complete", status
    # Reuse means neither a content read nor a page-body read.
    assert provider.get_calls == []
    assert provider.blocks_calls == []
    assert store.get(PARENT_ID, PROJECT).content["blocks"] == _body(CHILD_ID)
    assert _page_warnings(status) == []


# ---------------------------------------------------------------------------
# Fail-open: a body read never costs the object read
# ---------------------------------------------------------------------------


def test_a_failed_page_body_read_keeps_the_object_and_warns_once(tmp_path):
    provider = _provider(
        {TASKS: [CHILD_ID], PROJECT: [PARENT_ID]},
        blocks_error=RuntimeError("blocks read failed"),
    )
    coordinator, store = _coordinator(tmp_path, provider, mappings=(TASKS, PROJECT))

    coordinator.start()
    status = coordinator.wait(timeout=5)

    assert status["phase"] == "complete", status
    assert status["outcome"] == "published"
    # The object read survived...
    assert store.get(PARENT_ID, PROJECT) is not None
    assert store.get(CHILD_ID, TASKS) is not None
    # ...and its missing body hides nothing.
    assert "blocks" not in store.get(PARENT_ID, PROJECT).content
    assert _page_warnings(status) == [ce.parent_pages_warning(1)]
    # Nothing hidden: both rows reach the plan.
    result = _plan(provider, store, mappings=(TASKS, PROJECT))
    assert ca.DROP_REASON_EMBEDDED_IN_PARENT not in result.drop_tally.get(TASKS, {})


def test_one_warning_line_aggregates_every_unread_parent(tmp_path):
    provider = _provider(
        {TASKS: [CHILD_ID], PROJECT: [PARENT_ID], CONTINUAL: [CONTINUAL_ID]},
        blocks_error=RuntimeError("blocks read failed"),
    )
    coordinator, store = _coordinator(tmp_path, provider, mappings=_mappings())

    coordinator.start()
    status = coordinator.wait(timeout=5)

    assert status["phase"] == "complete", status
    assert _page_warnings(status) == [ce.parent_pages_warning(2)]
    assert status["warnings"].count(ce.parent_pages_warning(2)) == 1


def test_a_run_without_a_page_body_source_warns_nothing(tmp_path):
    provider = FakeProvider(
        _structures(TASKS, PROJECT),
        {
            (TASKS, None): {"objects": [_row(CHILD_ID, TASKS)], "next_cursor": None},
            (PROJECT, None): {"objects": [_row(PARENT_ID, PROJECT)], "next_cursor": None},
        },
        {
            CHILD_ID: _content(CHILD_ID, TASKS),
            PARENT_ID: _content(PARENT_ID, PROJECT),
        },
    )
    coordinator, store = _coordinator(tmp_path, provider, mappings=(TASKS, PROJECT))

    coordinator.start()
    status = coordinator.wait(timeout=5)

    assert status["phase"] == "complete", status
    assert _page_warnings(status) == []
    assert "blocks" not in store.get(PARENT_ID, PROJECT).content


# ---------------------------------------------------------------------------
# End to end: the Garage Buildout example through refresh + adapter
# ---------------------------------------------------------------------------


def _garage_buildout(tmp_path, *, parent_assigned: bool):
    provider = _provider(
        {TASKS: [CHILD_ID], PROJECT: [PARENT_ID]},
        blocks={PARENT_ID: _body(CHILD_ID)},
        titles={
            PARENT_ID: "Garage Buildout",
            CHILD_ID: "Above pegboard shelves of decor Garage Buildout",
        },
        unassigned=() if parent_assigned else (PARENT_ID,),
    )
    coordinator, store = _coordinator(tmp_path, provider, mappings=(TASKS, PROJECT))
    coordinator.start()
    status = coordinator.wait(timeout=5)
    assert status["phase"] == "complete", status
    return provider, store


def test_the_garage_buildout_child_task_is_dropped_after_a_refresh(tmp_path):
    provider, store = _garage_buildout(tmp_path, parent_assigned=True)

    result = _plan(provider, store, mappings=(TASKS, PROJECT))

    assert [row["name"] for row in result.items] == ["Garage Buildout"]
    assert result.drop_tally[TASKS][ca.DROP_REASON_EMBEDDED_IN_PARENT] == 1
    hidden = [
        line
        for line in result.warnings
        if ca.DROP_REASON_EMBEDDED_IN_PARENT in line and "hidden" in line
    ]
    assert len(hidden) == 1
    assert PARENT_ID in hidden[0]


def test_the_child_task_returns_when_its_parent_is_not_admitted(tmp_path):
    provider, store = _garage_buildout(tmp_path, parent_assigned=False)

    result = _plan(provider, store, mappings=(TASKS, PROJECT))

    assert "Above pegboard shelves of decor Garage Buildout" in [
        row["name"] for row in result.items
    ]
    assert ca.DROP_REASON_EMBEDDED_IN_PARENT not in result.drop_tally.get(TASKS, {})


# ---------------------------------------------------------------------------
# The split (MCP) path: a page body is a paced MCP read, and a failure stops it
# ---------------------------------------------------------------------------


def test_a_page_body_is_read_over_the_mcp_path_and_cached(tmp_path):
    provider, _rest, mcp = split_provider(
        objects={TASKS: [CHILD_ID], PROJECT: [PARENT_ID]},
        structures=_structures(TASKS, PROJECT),
        blocks={PARENT_ID: _body(CHILD_ID)},
    )
    coordinator, store = _coordinator(tmp_path, provider, mappings=(TASKS, PROJECT))
    provider.bind(coordinator.pacer, coordinator.cancel_event)

    coordinator.start()
    status = coordinator.wait(timeout=5)

    assert status["phase"] == "complete", status
    assert mcp.blocks_calls == [PARENT_ID]
    assert store.get(PARENT_ID, PROJECT).content["blocks"] == _body(CHILD_ID)
    assert _page_warnings(status) == []


def test_a_failed_mcp_page_body_read_is_attempted_once(tmp_path):
    provider, _rest, mcp = split_provider(
        objects={
            TASKS: [CHILD_ID],
            PROJECT: [PARENT_ID],
            CONTINUAL: [CONTINUAL_ID],
        },
        structures=_structures(TASKS, PROJECT, CONTINUAL),
        blocks={PARENT_ID: _body(CHILD_ID), CONTINUAL_ID: _body()},
    )
    mcp.blocks_error = RuntimeError("blocks tool failed")
    coordinator, store = _coordinator(tmp_path, provider, mappings=_mappings())
    provider.bind(coordinator.pacer, coordinator.cancel_event)

    coordinator.start()
    status = coordinator.wait(timeout=5)

    assert status["phase"] == "complete", status
    # One failure disables the read for the rest of the run: no second call.
    assert len(mcp.blocks_calls) == 1
    assert "blocks" not in store.get(PARENT_ID, PROJECT).content
    assert "blocks" not in store.get(CONTINUAL_ID, CONTINUAL).content
    assert _page_warnings(status) == [ce.parent_pages_warning(2)]
