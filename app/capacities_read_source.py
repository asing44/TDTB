"""Ordered-commit content-read machinery for the Capacities read phase (KTD1).

The coordinator reads content one object at a time, in its own priority order
(type order, then the listed row order inside a type), and admits that sequence
directly: ``_read_or_reuse`` stores content, publishes interim generations, and
compares fingerprints in the same order it reads. That order is authoritative
(R11), so any machinery that puts more than one content source behind the same
provider must hand results back in exactly that sequence.

:class:`ContentCommitQueue` is that machinery. A type batch is *primed* with the
priority-ordered ids the coordinator is about to read. Workers (one per source;
REST only in this unit) claim indices from a lock-guarded cursor, read the
claimed object, and store the result or the raised exception in a slot array.
:meth:`ContentCommitQueue.get` releases slots strictly in primed order: it
returns (or re-raises) index *i* only after every index below *i* has been
released, and it blocks only while its own slot is still pending.

Why ordered release is enough for admission parity:

- Every primed id is claimed exactly once, and the claim cursor hands out the
  primed ids in primed order, so the set and the sequence of source reads are
  the primed sequence whatever the worker count. With a single REST worker this
  is literally today's sequential read sequence: same calls, same order.
- The consumer can only observe slot *i* after slots *0..i-1* have been
  released, so worker completion order can never reach admission, interim
  publishing, or fingerprint comparison.
- An exception is stored in its own slot and re-raised to the consumer for that
  id alone, so the coordinator's existing per-object handling is untouched:
  unreadable-object tracking (bf9c48a) and the one-shot transient retry
  (d9c28cb) still see one failure for one object.
- A request for an id that is not (or is no longer) an unreleased primed entry -
  the coordinator's retry of a failed read, or an adapter read outside the read
  phase - falls through to a direct source read on the calling thread. That is
  the same second call the retry makes today, so retry call counts are
  unchanged, and out-of-phase adapter reads never wait on the queue.

Batch boundaries follow per-type completion: a primed batch never spans two
types, and the next batch is primed only after the current one is released.

Cancellation: setting ``cancel_event`` stops every worker at its next claim and
unblocks pending ``get_object`` callers by raising the run-level cancel signal
(:class:`capacities_refresh.RefreshCancelled`), so the coordinator's existing
cancel path is unchanged. ``end_run`` and ``close`` join every worker, so no
worker thread outlives a run, a cancel, or a failure (R10).

:class:`ReadSourceSplitter` is the REST-only shell the coordinator's provider
seam wraps. It implements the provider protocol, delegates everything except
content reads straight to the REST client, and serves content reads from the
queue when the current type batch is primed, else with a direct REST read. U4b
adds the MCP worker and requeue-on-source-loss on top of this shell; U5 wires
pacing through the injected content reader.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable, Iterable

import capacities_refresh

#: Result of one slot, or the exception the read raised. ``None`` error with a
#: ``None`` value is a legitimate (if malformed) REST payload, so the slot keeps
#: the error separately rather than overloading ``None``.
class _Slot:
    """One primed index's outcome, filled by whichever worker claimed it."""

    __slots__ = ("done", "value", "error")

    def __init__(self) -> None:
        self.done = False
        self.value: Any = None
        self.error: BaseException | None = None


#: How long a blocked caller waits before re-checking ``cancel_event``, which
#: is a plain event owned by the coordinator and cannot notify this queue.
_WAIT_SLICE = 0.05

#: A content read takes ``(object_id, structure_id)``. ``structure_id`` is the
#: REST listing row's structure id, or ``None`` for a direct read that has no
#: primed entry; U4b needs it for MCP normalization (U3).
ReadContent = Callable[[str, "str | None"], dict[str, Any]]


def _normalize_entries(
    entries: Iterable[Any],
) -> list[tuple[str, str | None]]:
    """Normalize primed entries to ``(object_id, structure_id)`` pairs.

    A bare id string is accepted and carries no structure id; that keeps a
    structure-less caller (and these tests) honest without a second shape.
    """
    normalized: list[tuple[str, str | None]] = []
    for entry in entries:
        if isinstance(entry, str):
            object_id, structure_id = entry, None
        else:
            try:
                object_id, structure_id = entry
            except (TypeError, ValueError) as exc:  # not a pair
                raise TypeError(
                    "a primed entry must be an object id or an "
                    "(object_id, structure_id) pair"
                ) from exc
        if not isinstance(object_id, str) or not object_id:
            raise ValueError("a primed entry needs a non-empty object id")
        if structure_id is not None and not isinstance(structure_id, str):
            raise ValueError("a primed structure id must be a string or None")
        normalized.append((object_id, structure_id))
    return normalized


class ContentCommitQueue:
    """A primed, ordered-commit content-read queue (R11, KTD1).

    ``read_content(object_id, structure_id)`` performs one source read. The
    queue owns no transport of its own; a source arrives as that callable.

    Public surface:

    - :meth:`prime` - the priority-ordered ids for the current type batch;
    - :meth:`get` - the ordered-commit release of one id;
    - :meth:`begin_run`, :meth:`end_run`, :meth:`close` - run lifecycle;
    - :meth:`read_counts` - per-source counts and elapsed seconds;
    - :attr:`live_worker_count` - threads still alive (R10 evidence).

    U4b/U5 extension points, protected and stable:

    - ``_start_worker(reader, source=...)`` starts one worker for a source;
    - ``_requeue(index)`` puts a failed claim back for another worker;
    - :attr:`_fell_back` is reported by :meth:`read_counts`.
    """

    def __init__(
        self,
        read_content: ReadContent,
        *,
        cancel_event: threading.Event | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._read_content = read_content
        self.cancel_event = (
            cancel_event if cancel_event is not None else threading.Event()
        )
        self._clock = clock
        self._cond = threading.Condition()
        #: The current batch, in primed (priority) order.
        self._entries: list[tuple[str, str | None]] = []
        self._slots: list[_Slot] = []
        #: The next index a worker may claim; each index is claimed once.
        self._claim_cursor = 0
        #: The next index the consumer may receive; advances in primed order.
        self._release_cursor = 0
        #: Indices a worker refused (U4b's requeue-on-source-loss) awaiting a
        #: fresh claim, ahead of the cursor.
        self._requeued: list[int] = []
        self._batch_active = False
        self._closed = False
        #: Set while a run is ending so every worker exits at its next claim.
        self._stopping = False
        self._workers: list[tuple[threading.Thread, str]] = []
        self._reads: dict[str, int] = {"rest": 0, "mcp": 0}
        self._elapsed: dict[str, float] = {"rest": 0.0, "mcp": 0.0}
        #: Whether MCP was abandoned this run (U4b sets it); reported, never
        #: inferred here, because only the MCP worker sees source loss.
        self._fell_back = False

    # -- lifecycle ------------------------------------------------------

    def begin_run(self) -> None:
        """Reset per-run state. Safe to call before the first :meth:`prime`."""
        with self._cond:
            if self._closed:
                raise RuntimeError("a closed content queue cannot begin another run")
            self._entries = []
            self._slots = []
            self._claim_cursor = 0
            self._release_cursor = 0
            self._requeued = []
            self._batch_active = False
            self._stopping = False
            self._reads = {"rest": 0, "mcp": 0}
            self._elapsed = {"rest": 0.0, "mcp": 0.0}
            self._fell_back = False
            self._workers = [entry for entry in self._workers if entry[0].is_alive()]
            self._cond.notify_all()

    def end_run(self) -> None:
        """Stop and join every worker, then drop the batch state.

        Counters survive so a metrics read after the read phase stays truthful
        (R7). A worker blocked in a source read returns on its own (the
        transport owns that timeout); the join is therefore unbounded and a
        surviving worker is impossible rather than reported (R10).
        """
        with self._cond:
            self._batch_active = False
            self._stopping = True
            self._cond.notify_all()
        self._join_workers()
        with self._cond:
            self._entries = []
            self._slots = []
            self._claim_cursor = 0
            self._release_cursor = 0
            self._requeued = []
            self._stopping = False

    def close(self) -> None:
        """Terminal shutdown: :meth:`end_run` and refuse further runs."""
        self.end_run()
        with self._cond:
            self._closed = True
            self._cond.notify_all()

    # -- priming and claiming ------------------------------------------

    def prime(self, entries: Iterable[Any]) -> None:
        """Record the priority-ordered ids for the current type batch.

        ``entries`` holds ``(object_id, structure_id)`` pairs; a bare id string
        is accepted with no structure id. A batch never spans two types, so
        priming while the current batch is still unreleased is a programming
        error and raises ``RuntimeError`` rather than mixing orders. Priming an
        empty batch is a no-op: nothing is read and every :meth:`get` falls
        through to a direct source read.
        """
        normalized = _normalize_entries(entries)
        with self._cond:
            if self._closed:
                raise RuntimeError("a closed content queue cannot be primed")
            if self._batch_active and self._release_cursor < len(self._entries):
                raise RuntimeError(
                    "cannot prime a new batch while the current batch is "
                    "unreleased"
                )
            self._entries = normalized
            self._slots = [_Slot() for _ in normalized]
            self._claim_cursor = 0
            self._release_cursor = 0
            self._requeued = []
            self._batch_active = bool(normalized)
            self._cond.notify_all()
        if normalized:
            self._ensure_default_worker()

    def _claim(self) -> int | None:
        """The next index to read, or ``None`` when the batch is drained."""
        with self._cond:
            while True:
                if self._closed or self._stopping or self.cancel_event.is_set():
                    self._cond.notify_all()
                    return None
                if self._requeued:
                    index = self._requeued.pop(0)
                    # A requeued index is claimable again; its slot is empty.
                    self._cond.notify_all()
                    return index
                if self._batch_active and self._claim_cursor < len(self._entries):
                    index = self._claim_cursor
                    self._claim_cursor += 1
                    return index
                # Nothing to read right now: wait for the next batch, a
                # requeue, or cancellation (which cannot notify us).
                self._cond.wait(_WAIT_SLICE)

    def _requeue(self, index: int) -> None:
        """Put one claimed index back for a fresh claim (U4b fallback path).

        The index keeps its primed position, so admission order is untouched:
        only which source reads it changes. The slot stays empty, so a consumer
        waiting on it keeps waiting. U4b must stop the source that lost the read
        before calling this, or that worker can claim the index again.
        """
        with self._cond:
            self._requeued.append(index)
            self._cond.notify_all()

    # -- workers --------------------------------------------------------

    def _ensure_default_worker(self) -> None:
        """Start the single REST worker for a batch, if none is alive."""
        with self._cond:
            alive = [entry for entry in self._workers if entry[0].is_alive()]
            self._workers = alive
            if any(source == "rest" for _, source in alive):
                return
        self._start_worker(self._read_content, source="rest")

    def _start_worker(self, read_content: ReadContent, *, source: str) -> None:
        """Start one worker reading for ``source`` (U4b adds ``"mcp"``).

        Workers share the claim cursor, so a source is only ever handed indices
        no other source has claimed. ``source`` names the counter and elapsed
        bucket the worker reports under, so at most one worker per source keeps
        the per-source accounting unambiguous.
        """
        thread = threading.Thread(
            target=self._worker_loop,
            args=(read_content, source),
            name=f"capacities-{source}-content",
            daemon=True,
        )
        with self._cond:
            self._workers.append((thread, source))
        thread.start()

    def _worker_loop(self, read_content: ReadContent, source: str) -> None:
        """Claim, read, store - until the batch is drained or cancelled."""
        while True:
            index = self._claim()
            if index is None:
                return
            with self._cond:
                if index >= len(self._entries):
                    continue
                object_id, structure_id = self._entries[index]
            started = self._clock()
            value: Any = None
            error: BaseException | None = None
            try:
                value = read_content(object_id, structure_id)
            except Exception as exc:  # noqa: BLE001 - stored for the consumer
                error = exc
            elapsed = max(0.0, self._clock() - started)
            with self._cond:
                self._reads[source] = self._reads.get(source, 0) + 1
                self._elapsed[source] = self._elapsed.get(source, 0.0) + elapsed
                if index < len(self._slots):
                    slot = self._slots[index]
                    if not slot.done:
                        slot.value = value
                        slot.error = error
                        slot.done = True
                self._cond.notify_all()

    def _join_workers(self) -> None:
        with self._cond:
            workers = [entry[0] for entry in self._workers]
        current = threading.current_thread()
        for thread in workers:
            if thread is not current:
                thread.join()
        with self._cond:
            self._workers = [entry for entry in self._workers if entry[0].is_alive()]

    @property
    def live_worker_count(self) -> int:
        """Threads still running for this queue (must reach 0 after a run)."""
        with self._cond:
            return self.live_worker_count_locked()

    def live_worker_count_locked(self) -> int:
        return sum(1 for entry in self._workers if entry[0].is_alive())

    # -- consumption ----------------------------------------------------

    def get(self, object_id: str) -> dict[str, Any]:
        """Release one object's result in primed order.

        Blocks while this object's slot is still pending, and additionally
        while an earlier slot is unreleased, so completion order never reaches
        the caller. Re-raises the exception the read raised for this id. An id
        outside the unreleased batch is read directly on the calling thread.
        """
        with self._cond:
            index = self._unreleased_index_locked(object_id)
        if index is None:
            return self._read_direct(object_id)
        with self._cond:
            while True:
                if self._closed or self.cancel_event.is_set():
                    raise capacities_refresh.RefreshCancelled("cancelled")
                if index >= len(self._slots):
                    # end_run dropped the batch under a waiting caller.
                    raise capacities_refresh.RefreshCancelled("cancelled")
                slot = self._slots[index]
                if index == self._release_cursor and slot.done:
                    self._release_cursor = index + 1
                    if self._release_cursor >= len(self._entries):
                        self._batch_active = False
                    self._cond.notify_all()
                    break
                self._cond.wait(_WAIT_SLICE)
        if slot.error is not None:
            raise slot.error
        return slot.value

    def _unreleased_index_locked(self, object_id: str) -> int | None:
        for index in range(self._release_cursor, len(self._entries)):
            if self._entries[index][0] == object_id:
                return index
        return None

    def _read_direct(self, object_id: str) -> dict[str, Any]:
        """One out-of-batch read on the calling thread (retry or adapter)."""
        started = self._clock()
        try:
            return self._read_content(object_id, None)
        finally:
            with self._cond:
                self._reads["rest"] += 1
                self._elapsed["rest"] += max(0.0, self._clock() - started)

    # -- reporting ------------------------------------------------------

    def read_counts(self) -> dict[str, Any]:
        """Per-source read counts and elapsed read seconds for this run (R7).

        Shaped so U4b adds nothing new: ``mcp`` is already present and stays
        zero until an MCP worker exists, and ``fell_back`` is the MCP worker's
        own report (source loss), never a guess made here.
        """
        with self._cond:
            return {
                "rest": self._reads.get("rest", 0),
                "mcp": self._reads.get("mcp", 0),
                "fell_back": bool(self._fell_back),
                "rest_elapsed": round(self._elapsed.get("rest", 0.0), 6),
                "mcp_elapsed": round(self._elapsed.get("mcp", 0.0), 6),
            }


class ReadSourceSplitter:
    """The content-read splitter shell: REST-only today, two sources in U4b.

    Implements the same provider surface the coordinator wraps, so ``PacedProvider``
    can wrap it unchanged: structures, listing, patch, ``base_url``,
    ``supports_object_listing``, and ``set_rate_observer`` delegate straight to
    the REST client, and only ``get_object`` is served from the queue.

    ``content_reader`` is the REST content read the queue's worker uses; the
    default is the REST client's own ``get_object``. U5 supplies a paced reader
    here (the worker is the only issuer of REST content requests once a batch is
    primed), which is why :attr:`paces_content_reads` is True: the provider seam
    above must not pace a second time. A read outside the primed batch - the
    coordinator's one-shot retry, or an adapter read - goes through the same
    reader on the calling thread, so it is paced the same way.

    ``get_object`` keeps the provider protocol's signature (``object_id`` only).
    U4b gets the structure id from the primed entry, which is why :meth:`prime`
    already takes ``(object_id, structure_id)`` pairs. A
    ``for_completion=True`` bypass (KTD1's REST-only completion re-read) is a
    U4b/U5 addition; today the completion re-read simply is not primed, so it
    already takes the direct REST path.
    """

    #: Content reads are paced inside this splitter's reader, not by the seam
    #: above it (U5).
    paces_content_reads = True

    def __init__(
        self,
        rest: Any,
        *,
        content_reader: Callable[[str], dict[str, Any]] | None = None,
        cancel_event: threading.Event | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.rest = rest
        self.cancel_event = (
            cancel_event if cancel_event is not None else threading.Event()
        )
        self._content_reader = content_reader or rest.get_object
        self.queue = ContentCommitQueue(
            self._read_rest, cancel_event=self.cancel_event, clock=clock
        )
        #: Which source served this run (``rest`` until U4b reports ``mixed``).
        self.read_source = "rest"

    # -- delegated provider surface -------------------------------------

    @property
    def base_url(self) -> str | None:
        return getattr(self.rest, "base_url", None)

    @property
    def supports_object_listing(self) -> bool:
        return getattr(self.rest, "supports_object_listing", False) is True

    def set_rate_observer(self, observer: Any) -> None:
        """Forward the header observer to REST (``capacities_refresh.py:863``)."""
        setter = getattr(self.rest, "set_rate_observer", None)
        if callable(setter):
            setter(observer)

    def fetch_structures(self) -> Any:
        return self.rest.fetch_structures()

    def list_objects(self, structure_id: str, cursor: str | None = None) -> Any:
        return self.rest.list_objects(structure_id, cursor)

    def patch_object(self, object_id: str, properties: dict[str, Any]) -> Any:
        return self.rest.patch_object(object_id, properties)

    def close(self) -> None:
        """Stop the workers, then best-effort close the REST client."""
        self.queue.close()
        closer = getattr(self.rest, "close", None)
        if callable(closer):
            try:
                closer()
            except Exception:  # noqa: BLE001 - best-effort close
                pass

    # -- content reads ---------------------------------------------------

    def _read_rest(self, object_id: str, structure_id: str | None) -> dict[str, Any]:
        """The queue's REST read; the normalizer part of ``structure_id`` is unused."""
        return self._content_reader(object_id)

    def prime(self, entries: Iterable[Any]) -> None:
        """Prime the current type batch's ordered ids (see :meth:`ContentCommitQueue.prime`)."""
        self.queue.prime(entries)

    def get_object(self, object_id: str) -> dict[str, Any]:
        """Serve one content read: from the primed batch, else direct REST."""
        return self.queue.get(object_id)

    def read_counts(self) -> dict[str, Any]:
        return self.queue.read_counts()

    # -- lifecycle -------------------------------------------------------

    def begin_run(self) -> None:
        self.queue.begin_run()
        self.read_source = "rest"

    def end_run(self) -> None:
        self.queue.end_run()
