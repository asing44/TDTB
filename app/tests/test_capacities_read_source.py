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

import capacities_adapter  # noqa: E402
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


class FakeMcp:
    """An ``McpProvider`` double: records reads, replays failures per call.

    ``behaviors`` is consumed one entry per MCP call, so the failure sequence is
    deterministic in MCP call order whatever index each call claimed. ``hook``
    fires after each call's outcome, which is how the tests take a deterministic
    checkpoint in the middle of a run.
    """

    def __init__(
        self,
        contents: dict[str, dict] | None = None,
        *,
        begin_error: BaseException | None = None,
        behaviors: list[BaseException | None] | None = None,
    ) -> None:
        self._lock = threading.Lock()
        self._contents = dict(contents or {})
        self.calls: list[tuple[str, str | None]] = []
        self.behaviors: list[BaseException | None] = list(behaviors or [])
        self.begin_error = begin_error
        self.begin_calls = 0
        self.end_calls = 0
        self.closed = False
        self.hook = None
        #: Set by a test to the splitter, so ``end_run`` can report live workers.
        self.observe = None
        self.live_workers_at_end_run: list[int | None] = []

    def begin_run(self) -> None:
        self.begin_calls += 1
        if self.begin_error is not None:
            raise self.begin_error

    def end_run(self) -> None:
        self.end_calls += 1
        if self.observe is not None:
            self.live_workers_at_end_run.append(self.observe.queue.live_worker_count)

    def close(self) -> None:
        self.closed = True
        self.end_run()

    def get_object(self, object_id: str, *, structure_id: str) -> dict:
        with self._lock:
            self.calls.append((object_id, structure_id))
            index = len(self.calls)
            behavior = self.behaviors.pop(0) if self.behaviors else None
        try:
            if behavior is not None:
                raise behavior
            return self._contents.get(
                object_id,
                {
                    "id": object_id,
                    "structureId": structure_id,
                    "properties": {"source": "mcp"},
                },
            )
        finally:
            if self.hook is not None:
                self.hook(index)

    @property
    def call_count(self) -> int:
        with self._lock:
            return len(self.calls)

    @property
    def ids(self) -> list[str]:
        with self._lock:
            return [object_id for object_id, _ in self.calls]


def _unavailable(retry_after: float | None = None) -> BaseException:
    return capacities_adapter.CapacitiesReadSourceUnavailable(
        "", retry_after=retry_after
    )


def _contract_error() -> BaseException:
    return capacities_adapter.CapacitiesContractError("mcp payload rejected")


def _mcp_rest(contents: dict[str, dict] | None = None) -> FakeRest:
    """A REST double whose payloads are distinguishable from MCP's."""
    rest = FakeRest(contents)
    for object_id, value in (contents or {}).items():
        rest._contents[object_id] = dict(value, properties={"source": "rest"})
    return rest


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


# ---------------------------------------------------------------------------
# U4b: MCP worker, requeue on failure, circuit breaker
# ---------------------------------------------------------------------------


class GatedRest(FakeRest):
    """A REST double whose leading content reads wait for a gate.

    ``hold`` counts how many leading content reads block, so a test can pin the
    REST worker inside one read while the MCP worker claims the following ids.
    """

    def __init__(self, gate: threading.Event, hold: int = 1) -> None:
        super().__init__()
        self.gate = gate
        self.hold = hold
        self.entered = threading.Event()

    def get_object(self, object_id: str) -> dict:
        wait = self.hold > 0
        if wait:
            self.hold -= 1
        if wait:
            self.entered.set()
            assert self.gate.wait(2.0)
        return self.content(object_id)


def _consume_in_threads(
    splitter: read_source.ReadSourceSplitter,
    ids: list[str],
) -> tuple[dict[str, dict], dict[str, threading.Event], list[BaseException]]:
    """Ask for every id from its own thread, in reverse primed order."""

    released: dict[str, dict] = {}
    done = {object_id: threading.Event() for object_id in ids}
    errors: list[BaseException] = []
    lock = threading.Lock()

    def take(object_id: str) -> None:
        try:
            value = splitter.get_object(object_id)
            with lock:
                released[object_id] = value
        except BaseException as exc:  # noqa: BLE001 - the test records it
            with lock:
                errors.append(exc)
        finally:
            done[object_id].set()

    for object_id in reversed(ids):
        _start(take, object_id)
    return released, done, errors


def test_listing_structures_patch_and_the_observer_stay_on_rest() -> None:
    rest = FakeRest()
    mcp = FakeMcp()
    splitter = read_source.ReadSourceSplitter(rest, mcp=mcp)
    splitter.begin_run()

    def observer(endpoint: str, headers: object) -> None:
        return None

    splitter.set_rate_observer(observer)
    assert rest.observers == [observer]
    assert splitter.base_url == rest.base_url
    assert splitter.supports_object_listing is True
    assert splitter.fetch_structures() == rest.structures
    splitter.list_objects("st-1", "cursor-1")
    splitter.patch_object("obj-a", {"x": 1})
    assert rest.listing_calls == [("st-1", "cursor-1")]
    assert rest.patch_calls == [("obj-a", {"x": 1})]
    assert mcp.calls == []
    assert mcp.begin_calls == 1

    splitter.prime([("obj-a", "st-1")])
    splitter.get_object("obj-a")
    splitter.end_run()
    assert mcp.end_calls == 1
    assert mcp.calls == [] or all(len(call) == 2 for call in mcp.calls)


def test_the_completion_reread_and_a_retry_read_stay_on_rest() -> None:
    rest = FakeRest()
    mcp = FakeMcp()
    splitter = read_source.ReadSourceSplitter(rest, mcp=mcp)
    splitter.begin_run()
    splitter.prime([("obj-a", "st-1")])
    assert splitter.get_object("obj-a")["id"] == "obj-a"
    mcp_calls_before = mcp.call_count

    completion = splitter.get_object("obj-a", for_completion=True)
    retry = splitter.get_object("obj-unprimed")
    splitter.end_run()

    # Both bypass the queue and go straight to REST, MCP healthy or not.
    assert completion == {"id": "obj-a", "structureId": "st-1", "properties": {"n": "obj-a"}}
    assert retry["id"] == "obj-unprimed"
    assert mcp.call_count == mcp_calls_before
    assert rest.calls[-2:] == ["obj-a", "obj-unprimed"]


def test_mcp_failing_on_its_second_claim_requeues_that_id_to_rest() -> None:
    ids = [f"obj-{index}" for index in range(5)]
    entries = [(object_id, "st-1") for object_id in ids]
    gate = threading.Event()
    rest = GatedRest(gate)
    mcp = FakeMcp(behaviors=[None, _unavailable()])
    splitter = read_source.ReadSourceSplitter(rest, mcp=mcp)
    splitter.begin_run()
    splitter.prime(entries)
    assert rest.entered.wait(2.0)
    assert _wait_for(lambda: mcp.call_count == 2)
    assert _wait_for(lambda: splitter._mcp_active is False)
    gate.set()

    got = [splitter.get_object(object_id) for object_id in ids]
    splitter.end_run()

    assert [value["id"] for value in got] == ids
    # MCP claimed the two ids REST could not (it was pinned in index 0).
    assert mcp.ids == ["obj-1", "obj-2"]
    # The lost read went back to REST: every id was read exactly once.
    assert rest.calls == ["obj-0", "obj-2", "obj-3", "obj-4"]
    assert sorted(rest.calls + mcp.ids[:1]) == sorted(ids)
    counts = splitter.read_counts()
    assert counts["rest"] == 4
    assert counts["mcp"] == 1
    assert counts["fell_back"] is True
    assert splitter.read_source == "split"
    assert splitter.queue.live_worker_count == 0


def test_mcp_unavailable_at_begin_run_runs_rest_only() -> None:
    rest = FakeRest()
    mcp = FakeMcp(begin_error=_unavailable())
    splitter = read_source.ReadSourceSplitter(rest, mcp=mcp)
    splitter.begin_run()
    splitter.prime([("obj-a", "st-1"), ("obj-b", "st-1")])
    got = [splitter.get_object(object_id) for object_id in ("obj-a", "obj-b")]
    splitter.end_run()

    assert [value["id"] for value in got] == ["obj-a", "obj-b"]
    assert rest.calls == ["obj-a", "obj-b"]
    assert mcp.calls == []
    assert splitter.read_source == "rest"
    counts = splitter.read_counts()
    assert counts == {
        "rest": 2,
        "mcp": 0,
        "fell_back": True,
        "rest_elapsed": counts["rest_elapsed"],
        "mcp_elapsed": 0.0,
    }
    assert mcp.end_calls == 1
    assert splitter.queue.live_worker_count == 0


def test_a_throttle_leaves_the_rest_worker_and_its_accounting_untouched() -> None:
    ids = [f"obj-{index}" for index in range(3)]
    entries = [(object_id, "st-1") for object_id in ids]
    gate = threading.Event()
    rest = GatedRest(gate)
    mcp = FakeMcp(behaviors=[_unavailable(retry_after=39.0)])
    splitter = read_source.ReadSourceSplitter(rest, mcp=mcp)
    splitter.begin_run()
    splitter.prime(entries)
    assert rest.entered.wait(2.0)
    assert _wait_for(lambda: mcp.call_count == 1)
    gate.set()

    got = [splitter.get_object(object_id) for object_id in ids]
    splitter.end_run()

    assert [value["id"] for value in got] == ids
    assert all(value["properties"] == {"n": value["id"]} for value in got)
    assert rest.calls == ids
    assert mcp.call_count == 1
    counts = splitter.read_counts()
    assert counts["rest"] == 3
    assert counts["mcp"] == 0
    assert counts["fell_back"] is True
    assert "rate_limited" not in counts
    assert "retried_reads" not in counts


def test_a_contract_error_from_mcp_is_requeued_to_rest() -> None:
    ids = [f"obj-{index}" for index in range(2)]
    entries = [(object_id, "st-1") for object_id in ids]
    gate = threading.Event()
    rest = GatedRest(gate)
    mcp = FakeMcp(behaviors=[_contract_error()])
    splitter = read_source.ReadSourceSplitter(rest, mcp=mcp)
    splitter.begin_run()
    splitter.prime(entries)
    assert rest.entered.wait(2.0)
    assert _wait_for(lambda: mcp.call_count == 1)
    gate.set()

    got = [splitter.get_object(object_id) for object_id in ids]
    # One failure is below the breaker, so MCP stays on and did not fall back.
    assert splitter._mcp_active is True
    assert splitter.read_counts()["fell_back"] is False
    splitter.end_run()

    # The refused object is read by REST exactly once; nothing is lost.
    assert mcp.ids == ["obj-1"]
    assert rest.calls == ["obj-0", "obj-1"]
    assert sorted(rest.calls) == sorted(ids)
    assert got[1]["properties"] == {"n": "obj-1"}


def test_three_consecutive_mcp_failures_stop_the_worker() -> None:
    ids = [f"obj-{index}" for index in range(12)]
    entries = [(object_id, "st-1") for object_id in ids]
    gate = threading.Event()
    rest = GatedRest(gate)
    failures = [_contract_error(), _contract_error(), None, _contract_error(), _contract_error()]
    mcp = FakeMcp(behaviors=failures + [_contract_error()] * 3)
    splitter = read_source.ReadSourceSplitter(rest, mcp=mcp)

    paused = threading.Event()
    resume = threading.Event()

    def hook(call_index: int) -> None:
        if call_index == 5:
            paused.set()
            assert resume.wait(2.0)

    mcp.hook = hook
    splitter.begin_run()
    splitter.prime(entries)
    assert rest.entered.wait(2.0)
    assert paused.wait(2.0)
    # Two failures, a success, then two more: still under the limit.
    assert mcp.call_count == 5
    assert splitter._mcp_active is True
    assert splitter.read_counts()["fell_back"] is False
    resume.set()

    # The next failure is the third consecutive one, so the worker stops.
    assert _wait_for(lambda: mcp.call_count == 6)
    assert _wait_for(lambda: splitter._mcp_active is False)
    assert splitter.read_counts()["fell_back"] is True
    gate.set()

    got = [splitter.get_object(object_id) for object_id in ids]
    splitter.end_run()

    assert [value["id"] for value in got] == ids
    assert mcp.call_count == 6
    # Only MCP's third call was served; every refused id was re-read by REST.
    assert mcp.ids[2] not in rest.calls
    assert sorted(rest.calls + [mcp.ids[2]]) == sorted(ids)
    assert len(rest.calls) == len(ids) - 1


def test_cancel_during_an_mcp_paced_wait_leaves_no_worker() -> None:
    cancel_event = threading.Event()
    gate = threading.Event()
    rest = GatedRest(gate)
    mcp = FakeMcp()

    def pace() -> None:
        deadline = time.monotonic() + 2.0
        while not cancel_event.is_set() and time.monotonic() < deadline:
            time.sleep(0.01)

    splitter = read_source.ReadSourceSplitter(
        rest, mcp=mcp, mcp_pace=pace, cancel_event=cancel_event
    )
    splitter.begin_run()
    splitter.prime([("obj-a", "st-1"), ("obj-b", "st-1")])
    assert rest.entered.wait(2.0)

    errors: list[BaseException] = []

    def consume() -> None:
        try:
            splitter.get_object("obj-a")
        except BaseException as exc:  # noqa: BLE001 - the test records it
            errors.append(exc)

    consumer = _start(consume)
    cancel_event.set()
    consumer.join(2.0)
    assert not consumer.is_alive()
    assert len(errors) == 1
    assert isinstance(errors[0], capacities_refresh.RefreshCancelled)

    gate.set()
    assert _wait_for(lambda: splitter.queue.live_worker_count == 0)
    splitter.end_run()
    assert splitter.queue.live_worker_count == 0
    assert mcp.end_calls == 1


def test_ordering_holds_when_mcp_is_faster_than_rest() -> None:
    ids = ["obj-0", "obj-1", "obj-2"]
    gate = threading.Event()
    rest = GatedRest(gate)
    mcp = FakeMcp()
    splitter = read_source.ReadSourceSplitter(rest, mcp=mcp)
    splitter.begin_run()
    splitter.prime([(object_id, "st-1") for object_id in ids])
    assert rest.entered.wait(2.0)
    assert _wait_for(lambda: mcp.call_count == 2)

    released, done, errors = _consume_in_threads(splitter, ids)
    # MCP is done, REST is pinned: nothing may be released out of order.
    assert not any(event.wait(0.05) for event in done.values())
    gate.set()
    assert _wait_for(lambda: all(event.is_set() for event in done.values()))
    splitter.end_run()

    assert errors == []
    assert [released[object_id]["id"] for object_id in ids] == ids
    assert released["obj-0"]["properties"] == {"n": "obj-0"}
    assert splitter.read_counts()["mcp"] == 2


def test_ordering_holds_when_mcp_is_slower_than_rest() -> None:
    ids = ["obj-0", "obj-1", "obj-2"]
    mcp_started = threading.Event()
    mcp_release = threading.Event()

    class RestWaitsForMcp(FakeRest):
        def __init__(self) -> None:
            super().__init__()
            self.hold = 1

        def get_object(self, object_id: str) -> dict:
            if self.hold > 0:
                self.hold -= 1
                assert mcp_started.wait(2.0)
            return self.content(object_id)

    class HeldMcp(FakeMcp):
        """An MCP double that pins its first read until the test releases it."""

        def __init__(self) -> None:
            super().__init__()
            self.hold = 1

        def get_object(self, object_id: str, *, structure_id: str) -> dict:
            wait = self.hold > 0
            if wait:
                self.hold -= 1
            with self._lock:
                self.calls.append((object_id, structure_id))
            mcp_started.set()
            if wait:
                assert mcp_release.wait(2.0)
            return {
                "id": object_id,
                "structureId": structure_id,
                "properties": {"source": "mcp"},
            }

    rest = RestWaitsForMcp()
    mcp = HeldMcp()
    splitter = read_source.ReadSourceSplitter(rest, mcp=mcp)
    splitter.begin_run()
    splitter.prime([(object_id, "st-1") for object_id in ids])
    assert mcp_started.wait(2.0)
    assert _wait_for(lambda: len(rest.calls) == 2 and mcp.call_count == 1)
    held_id = mcp.ids[0]

    released, done, errors = _consume_in_threads(splitter, ids)
    # REST finished two ids, but MCP's held id blocks every later release.
    assert done[rest.calls[0]].wait(2.0)
    assert not done[held_id].wait(0.05)
    assert not done[rest.calls[1]].wait(0.05)
    mcp_release.set()
    assert _wait_for(lambda: all(event.is_set() for event in done.values()))
    splitter.end_run()

    assert errors == []
    assert [released[object_id]["id"] for object_id in ids] == ids
    assert released[held_id]["properties"]["source"] == "mcp"
    assert sorted(rest.calls + mcp.ids) == sorted(ids)


def test_begin_run_resets_per_run_state_and_end_run_closes_mcp() -> None:
    rest = FakeRest()
    mcp = FakeMcp(begin_error=_unavailable())
    splitter = read_source.ReadSourceSplitter(rest, mcp=mcp)
    mcp.observe = splitter

    splitter.begin_run()
    splitter.prime([("obj-a", "st-1")])
    assert splitter.get_object("obj-a")["id"] == "obj-a"
    splitter.end_run()
    assert splitter.read_counts()["fell_back"] is True
    assert splitter.read_source == "rest"
    assert mcp.end_calls == 1
    # end_run joins the workers before it closes MCP.
    assert mcp.live_workers_at_end_run == [0]

    mcp.begin_error = None
    splitter.begin_run()
    assert splitter.read_counts() == {
        "rest": 0,
        "mcp": 0,
        "fell_back": False,
        "rest_elapsed": 0.0,
        "mcp_elapsed": 0.0,
    }
    splitter.prime([("obj-a", "st-1")])
    assert splitter.get_object("obj-a")["id"] == "obj-a"
    splitter.end_run()

    assert mcp.begin_calls == 2
    assert splitter.read_counts()["fell_back"] is False
    assert splitter.read_counts()["rest"] + splitter.read_counts()["mcp"] == 1
    assert mcp.live_workers_at_end_run == [0, 0]


def test_both_sources_are_used_and_the_release_matches_a_rest_only_run() -> None:
    ids = [f"obj-{index}" for index in range(6)]
    entries = [(object_id, "st-1") for object_id in ids]
    gate = threading.Event()
    rest = GatedRest(gate)
    mcp = FakeMcp()
    splitter = read_source.ReadSourceSplitter(rest, mcp=mcp)
    splitter.begin_run()
    splitter.prime(entries)
    assert rest.entered.wait(2.0)
    assert _wait_for(lambda: mcp.call_count == 5)
    gate.set()

    got = [splitter.get_object(object_id) for object_id in ids]
    splitter.end_run()

    counts = splitter.read_counts()
    assert counts["rest"] == 1
    assert counts["mcp"] == 5
    assert counts["rest"] + counts["mcp"] == len(ids)
    assert counts["rest_elapsed"] >= 0.0
    assert counts["mcp_elapsed"] >= 0.0
    assert splitter.read_source == "split"

    baseline_rest = FakeRest()
    baseline = read_source.ReadSourceSplitter(baseline_rest)
    baseline.begin_run()
    baseline.prime(entries)
    expected = [baseline.get_object(object_id) for object_id in ids]
    baseline.end_run()
    assert [(value["id"], value["structureId"]) for value in got] == [
        (value["id"], value["structureId"]) for value in expected
    ]


def test_a_primed_batch_never_restarts_mcp_after_the_run_lost_it() -> None:
    """D1: 'MCP is off for the run' is an invariant, not a racing flag."""
    gate = threading.Event()
    rest = GatedRest(gate)
    mcp = FakeMcp(behaviors=[_unavailable()])
    splitter = read_source.ReadSourceSplitter(rest, mcp=mcp)
    splitter.begin_run()
    splitter.prime([("a-1", "st-1"), ("a-2", "st-1")])
    # REST is pinned inside a-1 while MCP claims and loses a-2.
    assert _wait_for(lambda: mcp.call_count == 1)
    assert _wait_for(lambda: splitter.read_counts()["fell_back"])
    gate.set()
    assert splitter.get_object("a-1")["id"] == "a-1"
    assert splitter.get_object("a-2")["id"] == "a-2"
    assert splitter.queue.source_allowed("mcp") is False

    # A stale 'active' flag must not bring the worker back for the next batch.
    splitter._mcp_active = True
    splitter.prime([("b-1", "st-1"), ("b-2", "st-1")])
    got = [splitter.get_object(object_id)["id"] for object_id in ("b-1", "b-2")]
    splitter.end_run()

    assert got == ["b-1", "b-2"]
    assert mcp.call_count == 1
    assert splitter.queue.live_worker_count == 0

    # begin_run is the only place that re-enables the source.
    splitter.begin_run()
    assert splitter.queue.source_allowed("mcp") is True
    splitter.end_run()


def test_a_non_exception_escape_requeues_the_claim_instead_of_stranding_it() -> None:
    """D2: a worker that dies between claim and fill must not hang the consumer."""
    rest = FakeRest()
    mcp = FakeMcp(behaviors=[KeyboardInterrupt()])
    splitter = read_source.ReadSourceSplitter(rest, mcp=mcp)
    splitter.begin_run()
    splitter.prime([("a-1", "st-1"), ("a-2", "st-1")])
    got: list[str] = []
    consumer = _start(
        lambda: got.extend(splitter.get_object(i)["id"] for i in ("a-1", "a-2"))
    )
    consumer.join(2.0)
    hung = consumer.is_alive()
    splitter.end_run()

    assert not hung
    assert got == ["a-1", "a-2"]
    assert splitter.queue.live_worker_count == 0


def test_end_run_closes_mcp_even_when_joining_the_workers_raises() -> None:
    """I2: provider shutdown still happens when the queue's own end_run fails."""
    rest = FakeRest()
    mcp = FakeMcp()
    splitter = read_source.ReadSourceSplitter(rest, mcp=mcp)
    splitter.begin_run()

    def broken() -> None:
        raise RuntimeError("join failed")

    splitter.queue.end_run = broken  # type: ignore[method-assign]
    with pytest.raises(RuntimeError):
        splitter.end_run()
    assert mcp.end_calls == 1
