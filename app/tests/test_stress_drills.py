"""Failure drills for page-body child hiding (stress, not acceptance).

``test_capacities_embedded_child_refresh.py`` already proves the happy path and
the two ordinary failure paths (a raising page-body tool, and a run with no
page-body seam at all). This module drills the shapes that file does not: a
timeout instead of a generic error, malformed page-body payloads, hostile block
trees (unknown types, 60 levels of nesting, a 2000-child page), a parent that
disappears from the listing, a parent that loses admission between two
refreshes, a cache written before this feature existed, and a source that
declares page bodies unsupported.

Every drill asserts the same three invariants, because they are the whole
safety story for a display-only decision:

* fail open - an unreadable or malformed body hides nothing;
* bounded - a failed read costs at most one aggregated warning line, and a
  successful one costs none, however hostile the payload;
* contained - no exception escapes the refresh, and the published generation
  still carries every object read.

Everything is local and fake: no credential, no provider, no live MCP call.
"""
from __future__ import annotations

from datetime import date
import sys
import time
from pathlib import Path

import pytest

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
    """A fake provider with a page-body seam returning a raw payload.

    ``payloads`` maps an object id to the WHOLE payload
    ``get_object_blocks`` answers (so a drill can hand back ``None``, a list,
    or a body-less mapping), and ``blocks_error`` makes every read raise.
    """

    def __init__(
        self,
        structures,
        pages,
        objects=None,
        *,
        payloads=None,
        blocks_error=None,
    ) -> None:
        super().__init__(structures, pages, objects)
        self.payloads = dict(payloads or {})
        self.blocks_error = blocks_error
        self.blocks_calls: list[str] = []

    def get_object_blocks(self, object_id):
        self.blocks_calls.append(object_id)
        if self.blocks_error is not None:
            raise self.blocks_error
        if object_id in self.payloads:
            return self.payloads[object_id]
        return {"blocks": {}}

    def set_assigned(self, object_id, assigned):
        """Flip one cached object's Assigned flag for the next refresh."""
        content = self._objects[object_id]
        content["properties"]["assigned"] = {"type": "boolean", "boolean": assigned}


def _provider(
    objects: dict[str, list[str]],
    *,
    structures=None,
    payloads=None,
    blocks_error=None,
    unassigned=(),
    provider_class=BlocksProvider,
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
            content[object_id] = _content(
                object_id, type_id, assigned=object_id not in set(unassigned)
            )
    return provider_class(
        _structures(*structures),
        pages,
        content,
        payloads=payloads,
        blocks_error=blocks_error,
    )


def _coordinator(tmp_path, provider, *, mappings, store=None):
    store = store if store is not None else _store(tmp_path)
    clock = Clock()
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
    return [
        store.get(member.object_id, member.type_key).content
        for member in snapshot.members
    ]


def _plan(provider, store, *, mappings):
    return _adapter(provider, mappings=mappings).items_for_day_from_objects(
        DAY, _cached_objects(store)
    )


def _page_warnings(status):
    return [line for line in status["warnings"] if "could not be read" in line]


def _refresh(tmp_path, provider, *, mappings, store=None, mode="refresh"):
    coordinator, store = _coordinator(
        tmp_path, provider, mappings=mappings, store=store
    )
    coordinator.start(mode=mode)
    status = coordinator.wait(timeout=15)
    return status, store


# ---------------------------------------------------------------------------
# A page-body read that times out (the transport-duration failure class)
# ---------------------------------------------------------------------------


def test_a_timed_out_page_body_read_is_fail_open_and_bounded(tmp_path):
    provider = _provider(
        {TASKS: [CHILD_ID], PROJECT: [PARENT_ID]},
        blocks_error=TimeoutError("readObjectBlocks timed out"),
    )

    status, store = _refresh(tmp_path, provider, mappings=(TASKS, PROJECT))

    assert status["phase"] == "complete", status
    assert status["outcome"] == "published"
    # The object read survived the timeout, and its missing body hides nothing.
    assert store.get(PARENT_ID, PROJECT) is not None
    assert "blocks" not in store.get(PARENT_ID, PROJECT).content
    assert _page_warnings(status) == [ce.parent_pages_warning(1)]
    result = _plan(provider, store, mappings=(TASKS, PROJECT))
    assert CHILD_ID in [row["capacities_id"] for row in result.items]
    assert ca.DROP_REASON_EMBEDDED_IN_PARENT not in result.drop_tally.get(TASKS, {})


# ---------------------------------------------------------------------------
# Malformed page-body payloads
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({}, id="no-blocks-key"),
        pytest.param({"blocks": None}, id="blocks-null"),
        pytest.param({"blocks": []}, id="blocks-list-not-dict"),
        pytest.param({"blocks": "body"}, id="blocks-string"),
        pytest.param({"blocks": 7}, id="blocks-number"),
        pytest.param(None, id="payload-none"),
        pytest.param("nope", id="payload-string"),
        pytest.param([], id="payload-list"),
    ],
)
def test_a_malformed_page_body_payload_hides_nothing(tmp_path, payload):
    provider = _provider(
        {TASKS: [CHILD_ID], PROJECT: [PARENT_ID]},
        payloads={PARENT_ID: payload},
    )

    status, store = _refresh(tmp_path, provider, mappings=(TASKS, PROJECT))

    assert status["phase"] == "complete", status
    assert status["outcome"] == "published"
    assert "blocks" not in store.get(PARENT_ID, PROJECT).content
    assert _page_warnings(status) == [ce.parent_pages_warning(1)]
    result = _plan(provider, store, mappings=(TASKS, PROJECT))
    assert CHILD_ID in [row["capacities_id"] for row in result.items]
    assert ca.DROP_REASON_EMBEDDED_IN_PARENT not in result.drop_tally.get(TASKS, {})


def test_a_malformed_body_on_one_parent_does_not_warn_for_a_good_one(tmp_path):
    provider = _provider(
        {TASKS: [CHILD_ID, OTHER_ID], PROJECT: [PARENT_ID], CONTINUAL: [CONTINUAL_ID]},
        payloads={PARENT_ID: {}, CONTINUAL_ID: {"blocks": _body()}},
    )

    status, store = _refresh(
        tmp_path, provider, mappings=(TASKS, PROJECT, CONTINUAL)
    )

    assert status["phase"] == "complete", status
    # The readable body is still cached: one bad parent does not blind the rest.
    assert store.get(CONTINUAL_ID, CONTINUAL).content["blocks"] == _body()
    assert "blocks" not in store.get(PARENT_ID, PROJECT).content
    assert _page_warnings(status) == [ce.parent_pages_warning(1)]


# ---------------------------------------------------------------------------
# Hostile block trees
# ---------------------------------------------------------------------------


def test_unknown_block_types_and_link_tokens_never_hide_a_task(tmp_path):
    body = {
        "body": [
            {"type": "TextBlock", "tokens": [{"type": "LinkToken", "objectId": CHILD_ID}]},
            {"type": "CheckboxBlock", "checked": False},
            {"type": "GridBlock", "columns": [[{"type": "QuoteBlock"}]]},
            {"type": "WeirdBlockFromTheFuture", "payload": {"entityId": CHILD_ID}},
        ]
    }
    provider = _provider(
        {TASKS: [CHILD_ID], PROJECT: [PARENT_ID]}, payloads={PARENT_ID: {"blocks": body}}
    )

    status, store = _refresh(tmp_path, provider, mappings=(TASKS, PROJECT))

    assert status["phase"] == "complete", status
    assert store.get(PARENT_ID, PROJECT).content["blocks"] == body
    assert _page_warnings(status) == []
    result = _plan(provider, store, mappings=(TASKS, PROJECT))
    assert CHILD_ID in [row["capacities_id"] for row in result.items]


def _nested_body(depth: int, leaf: dict) -> dict:
    """A ``depth``-level nested block tree whose innermost value is ``leaf``."""
    node = leaf
    for _ in range(depth):
        node = {"type": "BlockGroup", "blocks": [node]}
    return {"body": [node]}


def test_a_deeply_nested_page_body_still_finds_its_child(tmp_path):
    body = _nested_body(60, {"type": "EntityBlock", "entityId": CHILD_ID})
    provider = _provider(
        {TASKS: [CHILD_ID], PROJECT: [PARENT_ID]}, payloads={PARENT_ID: {"blocks": body}}
    )

    status, store = _refresh(tmp_path, provider, mappings=(TASKS, PROJECT))

    assert status["phase"] == "complete", status
    assert _page_warnings(status) == []
    # 60 levels down is still membership: nesting depth must not fail closed.
    result = _plan(provider, store, mappings=(TASKS, PROJECT))
    assert CHILD_ID not in [row["capacities_id"] for row in result.items]
    assert result.drop_tally[TASKS][ca.DROP_REASON_EMBEDDED_IN_PARENT] == 1


def test_a_2000_child_page_body_stays_bounded_and_completes(tmp_path):
    huge = [f"child-{index:04d}" for index in range(2000)]
    provider = _provider(
        {TASKS: [CHILD_ID], PROJECT: [PARENT_ID]},
        payloads={PARENT_ID: {"blocks": _body(*huge, CHILD_ID)}},
    )

    started = time.monotonic()
    status, store = _refresh(tmp_path, provider, mappings=(TASKS, PROJECT))
    elapsed = time.monotonic() - started

    assert status["phase"] == "complete", status
    assert status["outcome"] == "published"
    cached = store.get(PARENT_ID, PROJECT).content["blocks"]
    assert len(ce.embedded_entity_ids(cached)) == 2001
    # One parent, one warning line at most, and the drain stays quick.
    assert _page_warnings(status) == []
    assert elapsed < 10, elapsed
    result = _plan(provider, store, mappings=(TASKS, PROJECT))
    # Only the listed object is a row: the 2000 phantom ids hide nothing.
    assert result.drop_tally[TASKS][ca.DROP_REASON_EMBEDDED_IN_PARENT] == 1


# ---------------------------------------------------------------------------
# Parent lifecycle
# ---------------------------------------------------------------------------


def test_a_child_of_a_parent_that_left_the_listing_stays_visible(tmp_path):
    # The parent was read (and its body cached) on an earlier generation, then
    # the owner deleted it: this refresh lists only the task.
    store = _store(tmp_path)
    store.put(
        PARENT_ID,
        PROJECT,
        {**_content(PARENT_ID, PROJECT), "blocks": _body(CHILD_ID)},
    )
    store.put(CHILD_ID, TASKS, _content(CHILD_ID, TASKS))
    provider = _provider({TASKS: [CHILD_ID]}, structures=[TASKS, PROJECT])

    status, store = _refresh(
        tmp_path, provider, mappings=(TASKS, PROJECT), store=store
    )

    assert status["phase"] == "complete", status
    assert _page_warnings(status) == []
    result = _plan(provider, store, mappings=(TASKS, PROJECT))
    # No listed parent means no membership: the stale body cannot hide a task.
    assert CHILD_ID in [row["capacities_id"] for row in result.items]
    assert ca.DROP_REASON_EMBEDDED_IN_PARENT not in result.drop_tally.get(TASKS, {})


def test_a_child_reappears_when_its_parent_loses_admission_between_refreshes(
    tmp_path,
):
    provider = _provider(
        {TASKS: [CHILD_ID], PROJECT: [PARENT_ID]},
        payloads={PARENT_ID: {"blocks": _body(CHILD_ID)}},
    )
    status, store = _refresh(tmp_path, provider, mappings=(TASKS, PROJECT))
    assert status["phase"] == "complete", status
    first = _plan(provider, store, mappings=(TASKS, PROJECT))
    assert CHILD_ID not in [row["capacities_id"] for row in first.items]
    assert first.drop_tally[TASKS][ca.DROP_REASON_EMBEDDED_IN_PARENT] == 1

    # The owner unassigns the parent; the body still embeds the child.
    provider.set_assigned(PARENT_ID, False)
    status, store = _refresh(
        tmp_path, provider, mappings=(TASKS, PROJECT), store=store, mode="rescan"
    )
    assert status["phase"] == "complete", status
    assert store.get(PARENT_ID, PROJECT).content["blocks"] == _body(CHILD_ID)

    second = _plan(provider, store, mappings=(TASKS, PROJECT))
    assert CHILD_ID in [row["capacities_id"] for row in second.items]
    assert ca.DROP_REASON_EMBEDDED_IN_PARENT not in second.drop_tally.get(TASKS, {})


# ---------------------------------------------------------------------------
# A body on the wrong structure, and a source that cannot read bodies at all
# ---------------------------------------------------------------------------


def test_a_task_embedding_another_task_is_never_hidden_by_the_refresh(tmp_path):
    # A page body is a Project/Continual detail; the refresh must not even ask
    # for a task's body, so a task that embeds another task cannot hide it.
    provider = _provider(
        {TASKS: [CHILD_ID, OTHER_ID]},
        payloads={OTHER_ID: {"blocks": _body(CHILD_ID)}},
        structures=[TASKS],
    )

    status, store = _refresh(tmp_path, provider, mappings=(TASKS,))

    assert status["phase"] == "complete", status
    assert provider.blocks_calls == []
    assert "blocks" not in store.get(OTHER_ID, TASKS).content
    assert _page_warnings(status) == []
    result = _plan(provider, store, mappings=(TASKS,))
    assert sorted(row["capacities_id"] for row in result.items) == sorted(
        [CHILD_ID, OTHER_ID]
    )


def test_a_source_that_declares_no_page_bodies_is_silent(tmp_path):
    class NoBodyProvider(BlocksProvider):
        """A source whose page-body seam exists but is unavailable this run."""

        supports_object_blocks = False

    provider = _provider(
        {TASKS: [CHILD_ID], PROJECT: [PARENT_ID]},
        payloads={PARENT_ID: {"blocks": _body(CHILD_ID)}},
        provider_class=NoBodyProvider,
    )

    status, store = _refresh(tmp_path, provider, mappings=(TASKS, PROJECT))

    assert status["phase"] == "complete", status
    # Declaring the seam unavailable is not a failure: no read, no warning.
    assert provider.blocks_calls == []
    assert "blocks" not in store.get(PARENT_ID, PROJECT).content
    assert _page_warnings(status) == []
    result = _plan(provider, store, mappings=(TASKS, PROJECT))
    assert CHILD_ID in [row["capacities_id"] for row in result.items]


def test_a_cache_written_before_the_feature_hides_nothing_and_warns_nothing(
    tmp_path,
):
    store = _store(tmp_path)
    # Both records were published by the pre-feature build: no ``blocks`` key.
    store.put(CHILD_ID, TASKS, _content(CHILD_ID, TASKS))
    store.put(PARENT_ID, PROJECT, _content(PARENT_ID, PROJECT))
    provider = _provider(
        {TASKS: [CHILD_ID], PROJECT: [PARENT_ID]},
        payloads={PARENT_ID: {"blocks": _body(CHILD_ID)}},
    )

    status, store = _refresh(
        tmp_path, provider, mappings=(TASKS, PROJECT), store=store
    )

    assert status["phase"] == "complete", status
    # Reused content reuses its (absent) body: no second read, nothing hidden.
    assert provider.blocks_calls == []
    assert "blocks" not in store.get(PARENT_ID, PROJECT).content
    assert _page_warnings(status) == []
    result = _plan(provider, store, mappings=(TASKS, PROJECT))
    assert CHILD_ID in [row["capacities_id"] for row in result.items]
    assert ca.DROP_REASON_EMBEDDED_IN_PARENT not in result.drop_tally.get(TASKS, {})
