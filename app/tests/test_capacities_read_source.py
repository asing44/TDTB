"""Contract tests for the ordered-commit content-read queue and splitter shell.

The queue exists so that any mix of content sources is indistinguishable from
today's sequential REST read phase (R1, R10, R11). These tests prove that with
REST alone - which is exactly what U4a ships - plus the two invariants that make
it safe to add a second source later: ordered release and a single claim per
primed id.

Everything here is offline and deterministic. Where the test needs to observe a
*blocked* caller it waits at most 50 ms on a handshake event, never on a real
sleep longer than that, so a scheduling hiccup cannot turn into a slow suite.
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import capacities_read_source as read_source  # noqa: E402
import capacities_refresh  # noqa: E402


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeRest:
    """A REST client double: records content reads in call order."""

    base_url = "https://rest.example.invalid"
    supports_object_listing = True

    def __init__(self, contents: dict[str, dict] | None = None) -> None:
        self._lock = threading.Lock()
        self._contents = dict(contents or {})
        self.calls: list[str] = []
        self.listing_calls: list[tuple[str, str | None]] = []
        self.patch_calls: list[tuple[str, dict]] = []
        self.observers: list[object] = []
        self.closed = False
        self.structures: dict[str, object] = {"structures": [{"id": "st-1"}]}

    def content(self, object_id: str) -> dict:
        with self._lock:
            self.calls.append(object_id)
        if object_id in self._contents:
            return self._contents[object_id]
        return {"id": object_id, "structureId": "st-1", "properties": {"n": object_id}}

    def get_object(self, object_id: str) -> dict:
        return self.content(object_id)

    def fetch_structures(self) -> dict:
        return self.structures

    def list_objects(self, structure_id: str, cursor: str | None = None) -> dict:
        self.listing_calls.append((structure_id, cursor))
        return {"structureId": structure_id, "objects": [], "nextCursor": cursor}

    def patch_object(self, object_id: str, properties: dict) -> dict:
        self.patch_calls.append((object_id, properties))
        return {"id": object_id, "properties": properties}

    def set_rate_observer(self, observer: object) -> None:
        self.observers.append(observer)

    def close(self) -> None:
        self.closed = True


class StepClock:
    """A monotonic fake clock that advances a fixed step on every read."""

    def __init__(self, step: float) -> None:
        self._now = 0.0
        self._step = step

    def __call__(self) -> float:
        value = self._now
        self._now += self._step
        return value


class Boom(RuntimeError):
    """A deterministic per-object read failure."""


def _wait_for(predicate, timeout: float = 2.0) -> bool:
    """Poll ``predicate`` without a long blocking sleep."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def _start(target, *args) -> threading.Thread:
    thread = threading.Thread(target=target, args=args, daemon=True)
    thread.start()
    return thread


# ---------------------------------------------------------------------------
# REST-only equivalence with today's sequential reads
# ---------------------------------------------------------------------------


def test_rest_only_results_and_call_order_match_a_sequential_read() -> None:
    entries = [("obj-a", "st-1"), ("obj-b", "st-1"), ("obj-c", "st-1")]
    contents = {
        "obj-a": {"id": "obj-a", "structureId": "st-1", "properties": {"v": 1}},
        "obj-b": {"id": "obj-b", "structureId": "st-1", "properties": {"v": 2}},
        "obj-c": {"id": "obj-c", "structureId": "st-1", "properties": {"v": 3}},
    }
    sequential = FakeRest(contents)
    expected = [sequential.get_object(object_id) for object_id, _ in entries]
    expected_order = list(sequential.calls)

    rest = FakeRest(contents)
    splitter = read_source.ReadSourceSplitter(rest)
    splitter.begin_run()
    splitter.prime(entries)
    got = [splitter.get_object(object_id) for object_id, _ in entries]
    splitter.end_run()

    assert got == expected
    assert rest.calls == expected_order
    counts = splitter.read_counts()
    assert counts["rest"] == 3
    assert counts["mcp"] == 0
    assert counts["fell_back"] is False
    assert counts["rest_elapsed"] >= 0.0
    assert counts["mcp_elapsed"] == 0.0
    assert splitter.queue.live_worker_count == 0


def test_out_of_order_completion_is_released_in_primed_order() -> None:
    started = threading.Event()
    hold = threading.Event()

    def slow_rest(object_id: str, structure_id: str | None) -> dict:
        if object_id == "obj-a":
            started.set()
            assert hold.wait(2.0)
        return {"id": object_id, "properties": {"source": "rest"}}

    queue = read_source.ContentCommitQueue(slow_rest)
    queue.begin_run()
    queue.prime([("obj-a", "st-1"), ("obj-b", "st-1")])
    # The REST worker holds index 0; a second source finishes index 1 first.
    assert started.wait(2.0)

    def second_source(object_id: str, structure_id: str | None) -> dict:
        return {"id": object_id, "properties": {"source": "second"}}

    queue._start_worker(second_source, source="mcp")
    assert _wait_for(lambda: queue.read_counts()["mcp"] == 1)

    released: dict[str, dict] = {}
    first_done = threading.Event()
    second_done = threading.Event()

    def take(object_id: str, done: threading.Event) -> None:
        try:
            released[object_id] = queue.get(object_id)
        finally:
            done.set()

    later = _start(take, "obj-b", second_done)
    # Index 1 is complete, but index 0 is pending: nothing is released yet.
    assert not second_done.wait(0.05)
    earlier = _start(take, "obj-a", first_done)
    assert not first_done.wait(0.05)
    assert not second_done.is_set()

    hold.set()
    assert first_done.wait(2.0)
    assert second_done.wait(2.0)
    later.join(2.0)
    earlier.join(2.0)
    queue.end_run()

    assert released["obj-a"] == {"id": "obj-a", "properties": {"source": "rest"}}
    assert released["obj-b"] == {"id": "obj-b", "properties": {"source": "second"}}
    assert queue.live_worker_count == 0


def test_each_primed_id_is_claimed_exactly_once() -> None:
    lock = threading.Lock()
    calls: list[str] = []

    def reader(object_id: str, structure_id: str | None) -> dict:
        with lock:
            calls.append(object_id)
        return {"id": object_id, "properties": {}}

    ids = [f"obj-{index}" for index in range(8)]
    queue = read_source.ContentCommitQueue(reader)
    queue.begin_run()
    queue.prime([(object_id, "st-1") for object_id in ids])
    queue._start_worker(reader, source="mcp")
    got = [queue.get(object_id)["id"] for object_id in ids]
    queue.end_run()

    assert got == ids
    assert sorted(calls) == sorted(ids)
    counts = queue.read_counts()
    assert counts["rest"] + counts["mcp"] == len(ids)
    assert queue.live_worker_count == 0


# ---------------------------------------------------------------------------
# Cancellation and lifecycle
# ---------------------------------------------------------------------------


def test_cancel_mid_batch_unblocks_the_consumer_and_leaves_no_worker() -> None:
    cancel_event = threading.Event()
    entered = threading.Event()
    hold = threading.Event()

    def reader(object_id: str, structure_id: str | None) -> dict:
        entered.set()
        assert hold.wait(2.0)
        return {"id": object_id, "properties": {}}

    queue = read_source.ContentCommitQueue(reader, cancel_event=cancel_event)
    queue.begin_run()
    queue.prime([("obj-a", "st-1"), ("obj-b", "st-1")])
    assert entered.wait(2.0)

    errors: list[BaseException] = []

    def consume() -> None:
        try:
            queue.get("obj-a")
        except BaseException as exc:  # noqa: BLE001 - the test records it
            errors.append(exc)

    consumer = _start(consume)
    cancel_event.set()
    consumer.join(2.0)
    assert not consumer.is_alive()
    assert len(errors) == 1
    assert isinstance(errors[0], capacities_refresh.RefreshCancelled)

    hold.set()
    queue.end_run()
    assert queue.live_worker_count == 0

    # A primed batch is never read once the run is cancelled: the worker the
    # prime starts exits at its first claim instead of issuing a read.
    cancel_event.clear()
    queue.begin_run()
    cancel_event.set()
    queue.prime([("obj-c", "st-1")])
    assert _wait_for(lambda: queue.live_worker_count == 0)
    queue.end_run()


def test_an_empty_primed_batch_is_a_no_op() -> None:
    rest = FakeRest()
    splitter = read_source.ReadSourceSplitter(rest)
    splitter.begin_run()
    splitter.prime([])

    assert splitter.queue.live_worker_count == 0
    assert splitter.get_object("obj-a") == {
        "id": "obj-a",
        "structureId": "st-1",
        "properties": {"n": "obj-a"},
    }
    splitter.end_run()

    assert rest.calls == ["obj-a"]
    assert splitter.read_counts()["rest"] == 1
    assert splitter.queue.live_worker_count == 0


def test_priming_a_new_batch_before_release_is_refused() -> None:
    queue = read_source.ContentCommitQueue(lambda object_id, structure_id: {})
    queue.begin_run()
    with pytest.raises(RuntimeError):
        queue.prime([("obj-a", "st-1"), ("obj-b", "st-1")])
        queue.prime([("obj-c", "st-1")])
    queue.end_run()


# ---------------------------------------------------------------------------
# Counts, elapsed, and per-object failures
# ---------------------------------------------------------------------------


def test_read_counts_and_elapsed_are_accurate() -> None:
    clock = StepClock(0.25)
    rest = FakeRest()
    splitter = read_source.ReadSourceSplitter(rest, clock=clock)
    splitter.begin_run()
    splitter.prime([("obj-a", "st-1"), ("obj-b", "st-1")])
    for object_id in ("obj-a", "obj-b"):
        splitter.get_object(object_id)
    splitter.end_run()

    assert splitter.read_counts() == {
        "rest": 2,
        "mcp": 0,
        "fell_back": False,
        "rest_elapsed": 0.5,
        "mcp_elapsed": 0.0,
    }

    # Counters survive end_run for the metrics read, and begin_run resets them.
    splitter.begin_run()
    assert splitter.read_counts() == {
        "rest": 0,
        "mcp": 0,
        "fell_back": False,
        "rest_elapsed": 0.0,
        "mcp_elapsed": 0.0,
    }
    splitter.end_run()


def test_an_exception_is_surfaced_for_its_own_id_and_later_ids_still_read() -> None:
    attempts: dict[str, int] = {}
    order: list[str] = []
    lock = threading.Lock()

    def reader(object_id: str, structure_id: str | None) -> dict:
        with lock:
            order.append(object_id)
            attempts[object_id] = attempts.get(object_id, 0) + 1
            attempt = attempts[object_id]
        if object_id == "obj-b" and attempt == 1:
            raise Boom("deterministic failure")
        return {"id": object_id, "properties": {}}

    queue = read_source.ContentCommitQueue(reader)
    queue.begin_run()
    queue.prime([("obj-a", "st-1"), ("obj-b", "st-1"), ("obj-c", "st-1")])

    assert queue.get("obj-a") == {"id": "obj-a", "properties": {}}
    with pytest.raises(Boom):
        queue.get("obj-b")
    # A failed object never blocks the rest of the batch.
    assert queue.get("obj-c") == {"id": "obj-c", "properties": {}}
    assert queue.read_counts()["rest"] == 3

    # The coordinator's one-shot retry re-asks for the same id: that is a
    # direct read on the calling thread, so the retry still costs one call.
    assert queue.get("obj-b") == {"id": "obj-b", "properties": {}}
    assert queue.read_counts()["rest"] == 4
    assert order == ["obj-a", "obj-b", "obj-c", "obj-b"]
    queue.end_run()
    assert queue.live_worker_count == 0


# ---------------------------------------------------------------------------
# Splitter shell: delegation and the content-read seam
# ---------------------------------------------------------------------------


def test_splitter_delegates_everything_but_content_reads() -> None:
    rest = FakeRest()
    splitter = read_source.ReadSourceSplitter(rest)

    def observer(endpoint: str, headers: object) -> None:
        return None

    splitter.set_rate_observer(observer)
    assert rest.observers == [observer]
    assert splitter.base_url == rest.base_url
    assert splitter.supports_object_listing is True
    assert splitter.fetch_structures() == rest.structures
    assert splitter.list_objects("st-1", "cursor-1") == {
        "structureId": "st-1",
        "objects": [],
        "nextCursor": "cursor-1",
    }
    assert rest.listing_calls == [("st-1", "cursor-1")]
    assert splitter.patch_object("obj-a", {"x": 1})["id"] == "obj-a"
    assert rest.patch_calls == [("obj-a", {"x": 1})]
    # No primed batch: a content read is a direct REST read, never queued.
    assert splitter.get_object("obj-a")["id"] == "obj-a"
    assert rest.calls == ["obj-a"]
    assert splitter.paces_content_reads is True
    assert splitter.read_source == "rest"

    splitter.close()
    assert rest.closed is True


def test_an_injected_content_reader_is_the_only_rest_read_path() -> None:
    rest = FakeRest()
    seen: list[str] = []

    def paced(object_id: str) -> dict:
        seen.append(object_id)
        return {"id": object_id, "properties": {"paced": True}}

    splitter = read_source.ReadSourceSplitter(rest, content_reader=paced)
    splitter.begin_run()
    splitter.prime([("obj-a", "st-1")])
    assert splitter.get_object("obj-a") == {
        "id": "obj-a",
        "properties": {"paced": True},
    }
    splitter.end_run()

    assert seen == ["obj-a"]
    assert rest.calls == []
    assert rest.listing_calls == []


def test_the_structure_id_travels_with_the_primed_entry() -> None:
    """U4b's MCP worker needs the listing row's structure id per object."""
    seen: list[tuple[str, str | None]] = []

    def reader(object_id: str, structure_id: str | None) -> dict:
        seen.append((object_id, structure_id))
        return {"id": object_id, "properties": {}}

    queue = read_source.ContentCommitQueue(reader)
    queue.begin_run()
    queue.prime([("obj-a", "st-1"), ("obj-b", "st-2"), "obj-c"])
    assert queue.get("obj-a")["id"] == "obj-a"
    assert queue.get("obj-b")["id"] == "obj-b"
    assert queue.get("obj-c")["id"] == "obj-c"
    queue.end_run()

    assert seen == [("obj-a", "st-1"), ("obj-b", "st-2"), ("obj-c", None)]
