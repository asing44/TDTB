"""Focused behavior tests for the paced direct Capacities refresh coordinator (U2).

Everything here is local and fake: a deterministic provider, an injected
monotonic clock and sleeper (no real ``time.sleep``), a ``tmp_path`` state
root, and no credential, provider, or ``$HOME`` read. The durable store under
test is U1 (``capacities_refresh_state``); this file owns the acquisition,
pacing, single-flight, cancellation, freshness, and publication behavior.
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import capacities_adapter as ca  # noqa: E402
import capacities_builder as cb  # noqa: E402
import capacities_refresh as rr  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent))
from capacities_refresh_helpers import (  # noqa: E402
    PRIMARY,
    SPACE,
    TYPES,
    Clock,
    Sleeper,
    _content,
    _mapping,
    _paged_provider,
    _row,
    _store,
)


class Revision:
    def __init__(self, value: int = 1) -> None:
        self.value = value

    def __call__(self):
        return self.value


def _coordinator(
    tmp_path,
    provider,
    *,
    mappings=TYPES,
    clock=None,
    sleeper=None,
    revision=None,
    max_pages=20,
    max_retries=3,
    config_guard=None,
):
    clock = clock or Clock()
    sleeper = sleeper if sleeper is not None else Sleeper(clock)
    store = _store(tmp_path)
    return (
        rr.RefreshCoordinator(
            root=tmp_path / "state",
            store=store,
            provider=provider,
            space_id=SPACE,
            mappings=tuple(_mapping(t) for t in mappings),
            revision_supplier=revision or Revision(),
            scope_key="all",
            max_pages=max_pages,
            clock=clock,
            sleeper=sleeper,
            max_retries=max_retries,
            config_guard=config_guard,
        ),
        store,
        clock,
        sleeper,
    )


# ---------------------------------------------------------------------------
# AE1/AE2 — warm reuse and single-object cold read
# ---------------------------------------------------------------------------

def test_warm_refresh_reads_no_content_and_publishes_complete(tmp_path):
    provider = _paged_provider(objects={PRIMARY: ["a", "b"], "T2": ["c"]})
    coordinator, store, _, _ = _coordinator(tmp_path, provider)
    # Pre-seed the U1 store as a prior warm cache.
    for object_id, type_id in (("a", PRIMARY), ("b", PRIMARY), ("c", "T2")):
        store.put(object_id, type_id, _content(object_id, type_id))

    coordinator.start()
    status = coordinator.wait(timeout=5)

    assert status["phase"] == "complete", status
    assert status["outcome"] == "published"
    assert provider.get_calls == []
    snapshot = store.load_snapshot("all")
    assert snapshot is not None and snapshot.generation == 1
    assert {m.object_id for m in snapshot.members} == {"a", "b", "c"}


def test_one_new_object_costs_exactly_one_content_read(tmp_path):
    provider = _paged_provider(objects={PRIMARY: ["a", "b"], "T2": ["c"]})
    coordinator, store, _, _ = _coordinator(tmp_path, provider)
    store.put("a", PRIMARY, _content("a", PRIMARY))
    store.put("c", "T2", _content("c", "T2"))

    coordinator.start()
    status = coordinator.wait(timeout=5)

    assert status["phase"] == "complete"
    assert provider.get_calls == ["b"]
    snapshot = store.load_snapshot("all")
    assert {m.object_id for m in snapshot.members} == {"a", "b", "c"}


# ---------------------------------------------------------------------------
# AE4 — scoped Rescan
# ---------------------------------------------------------------------------

def test_rescan_one_type_rereads_only_that_type(tmp_path):
    provider = _paged_provider(objects={PRIMARY: ["a", "b"], "T2": ["c"]})
    coordinator, store, _, _ = _coordinator(tmp_path, provider)
    coordinator.start()
    coordinator.wait(timeout=5)
    assert provider.get_calls == ["a", "b", "c"]

    # Second run: scoped Rescan of the primary type only.
    coordinator.start(mode="rescan", scope=PRIMARY)
    status = coordinator.wait(timeout=5)

    assert status["phase"] == "complete"
    # The rescan re-read the primary type's two objects and did not re-read T2.
    assert set(provider.get_calls[-2:]) == {"a", "b"}
    assert provider.get_calls.count("c") == 1
    snapshot = store.load_snapshot("all")
    assert {m.object_id for m in snapshot.members} == {"a", "b", "c"}


def test_rescan_all_rereads_all_configured_types(tmp_path):
    provider = _paged_provider(objects={PRIMARY: ["a"], "T2": ["c"]})
    coordinator, store, _, _ = _coordinator(tmp_path, provider)
    coordinator.start()
    coordinator.wait(timeout=5)

    coordinator.start(mode="rescan", scope="all")
    coordinator.wait(timeout=5)

    assert provider.get_calls.count("a") == 2
    assert provider.get_calls.count("c") == 2


# ---------------------------------------------------------------------------
# AE5 — incomplete listing never publishes truncated membership
# ---------------------------------------------------------------------------

def test_page_two_failure_installs_nothing(tmp_path):
    pages = {
        (PRIMARY, None): {"objects": [_row("a", PRIMARY)], "next_cursor": "p2"},
        (PRIMARY, "p2"): RuntimeError("boom"),
    }
    provider = _paged_provider(objects={PRIMARY: ["a"], "T2": ["c"]}, pages=pages)
    coordinator, store, _, _ = _coordinator(tmp_path, provider)

    coordinator.start()
    status = coordinator.wait(timeout=5)

    assert status["phase"] == "failed"
    assert status["outcome"] == "noCapacities"
    assert status["warnings"]
    assert store.load_snapshot("all") is None


def test_repeated_cursor_installs_nothing(tmp_path):
    pages = {
        (PRIMARY, None): {"objects": [_row("a", PRIMARY)], "next_cursor": "loop"},
        (PRIMARY, "loop"): {"objects": [_row("a", PRIMARY)], "next_cursor": "loop"},
    }
    provider = _paged_provider(objects={PRIMARY: ["a"], "T2": []}, pages=pages)
    coordinator, store, _, _ = _coordinator(tmp_path, provider)

    coordinator.start()
    status = coordinator.wait(timeout=5)

    assert status["phase"] == "failed"
    assert store.load_snapshot("all") is None


def test_page_bound_installs_nothing(tmp_path):
    pages = {
        (PRIMARY, None): {"objects": [_row("a", PRIMARY)], "next_cursor": "p2"},
        (PRIMARY, "p2"): {"objects": [_row("a", PRIMARY)], "next_cursor": "p3"},
    }
    provider = _paged_provider(objects={PRIMARY: ["a"], "T2": []}, pages=pages)
    coordinator, store, _, _ = _coordinator(tmp_path, provider, max_pages=1)

    coordinator.start()
    status = coordinator.wait(timeout=5)

    assert status["phase"] == "failed"
    assert store.load_snapshot("all") is None


# ---------------------------------------------------------------------------
# AE7 — cold import spans paced windows with visible progress
# ---------------------------------------------------------------------------

def test_cold_import_spans_paced_windows_and_reports_progress(tmp_path):
    clock = Clock()
    sleeper = Sleeper(clock)
    objects = {PRIMARY: [f"p-{i}" for i in range(5)], "T2": ["t-0"]}
    provider = _paged_provider(objects=objects)
    coordinator, store, _, _ = _coordinator(
        tmp_path, provider, clock=clock, sleeper=sleeper
    )
    coordinator.pacer.policies["content"] = rr.EndpointPolicy(limit=2, window=100.0)

    coordinator.start()
    status = coordinator.wait(timeout=5)

    assert status["phase"] == "complete"
    assert len(provider.get_calls) == 6
    # Two-per-window pacing forced at least two waits to advance the clock.
    assert len(sleeper.calls) >= 2
    assert all(call > 0 for call in sleeper.calls)
    assert status["job"]["progress"]["read"] == 6


def test_status_query_makes_no_provider_calls(tmp_path):
    provider = _paged_provider(objects={PRIMARY: ["a"], "T2": ["c"]})
    coordinator, _, _, _ = _coordinator(tmp_path, provider)
    coordinator.start()
    coordinator.wait(timeout=5)
    before = (provider.fetch_calls, len(provider.list_calls), len(provider.get_calls))

    for _ in range(3):
        coordinator.status()

    assert (provider.fetch_calls, len(provider.list_calls), len(provider.get_calls)) == before


# ---------------------------------------------------------------------------
# Pacing — 429 / retry-after / reset honors the fake clock
# ---------------------------------------------------------------------------

def test_rate_limit_retry_honors_retry_after_and_eventually_publishes(tmp_path):
    clock = Clock()
    sleeper = Sleeper(clock)
    provider = _paged_provider(objects={PRIMARY: ["a"], "T2": ["c"]})
    attempts = {"n": 0}

    def flaky(object_id):
        if object_id == "a" and attempts["n"] < 2:
            attempts["n"] += 1
            raise ca.CapacitiesRateLimited("slow down", retry_after=1.5)

    provider.on_get = flaky
    coordinator, store, _, _ = _coordinator(
        tmp_path, provider, clock=clock, sleeper=sleeper
    )

    coordinator.start()
    status = coordinator.wait(timeout=5)

    assert status["phase"] == "complete"
    assert provider.get_calls.count("a") == 3
    assert sleeper.calls.count(1.5) == 2
    assert store.load_snapshot("all") is not None


def test_rate_limit_retries_are_bounded_and_fail_truthfully(tmp_path):
    clock = Clock()
    sleeper = Sleeper(clock)
    provider = _paged_provider(objects={PRIMARY: ["a"], "T2": []})
    provider.get_error = ca.CapacitiesRateLimited("nope", retry_after=2.0)
    coordinator, store, _, _ = _coordinator(
        tmp_path, provider, clock=clock, sleeper=sleeper, max_retries=2
    )

    coordinator.start()
    status = coordinator.wait(timeout=5)

    assert status["phase"] == "failed"
    assert status["outcome"] == "noCapacities"
    assert provider.get_calls.count("a") == 3  # 1 attempt + 2 retries
    assert store.load_snapshot("all") is None
    # No provider message or path leaks into the warning.
    assert all("nope" not in w for w in status["warnings"])


def test_rate_reset_headers_start_a_conservative_cooldown(tmp_path):
    clock = Clock()
    sleeper = Sleeper(clock)
    coordinator, _, _, _ = _coordinator(
        tmp_path,
        _paged_provider(objects={PRIMARY: [], "T2": []}),
        clock=clock,
        sleeper=sleeper,
    )
    pacer = coordinator.pacer

    pacer.observe("content", {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "7"})
    pacer.before_request("content")

    assert sleeper.calls and sleeper.calls[0] == pytest.approx(7.0)


# ---------------------------------------------------------------------------
# AE22 — cancellation retains successful reads and the prior snapshot
# ---------------------------------------------------------------------------

def test_cancel_during_hydration_retains_reads_and_keeps_snapshot(tmp_path):
    provider = _paged_provider(objects={PRIMARY: ["a", "b", "c"], "T2": ["z"]})
    coordinator, store, _, _ = _coordinator(tmp_path, provider)

    # Establish a prior complete generation.
    coordinator.start()
    first = coordinator.wait(timeout=5)
    assert first["phase"] == "complete"
    assert store.load_snapshot("all").generation == 1
    store.put("a", PRIMARY, _content("a", PRIMARY))

    def cancel_after_first(object_id):
        if object_id == "b":
            coordinator.cancel()

    provider.on_get = cancel_after_first
    coordinator.start(mode="rescan", scope=PRIMARY)
    status = coordinator.wait(timeout=5)

    assert status["phase"] == "cancelled"
    assert status["outcome"] == "cancelled"
    # The read for "b" succeeded before cancel was requested; it persisted.
    assert store.get("b", PRIMARY) is not None
    # The prior published generation is untouched.
    snapshot = store.load_snapshot("all")
    assert snapshot is not None and snapshot.generation == 1


def test_cancel_interrupts_rate_limit_backoff(tmp_path):
    clock = Clock()
    sleeper = Sleeper(clock)
    provider = _paged_provider(objects={PRIMARY: ["a"], "T2": []})
    provider.get_error = ca.CapacitiesRateLimited("slow", retry_after=5.0)
    coordinator, store, _, _ = _coordinator(
        tmp_path, provider, clock=clock, sleeper=sleeper, max_retries=5
    )
    sleeper.on_sleep = lambda seconds: coordinator.cancel()

    coordinator.start()
    status = coordinator.wait(timeout=5)

    assert status["phase"] == "cancelled"
    assert store.load_snapshot("all") is None


# ---------------------------------------------------------------------------
# Freshness / revision guard
# ---------------------------------------------------------------------------

def test_config_change_mid_job_blocks_stale_publication(tmp_path):
    provider = _paged_provider(objects={PRIMARY: ["a"], "T2": ["c"]})
    coordinator, store, _, _ = _coordinator(tmp_path, provider)
    # The supplier returns the start revision once, then a newer revision.
    values = iter([1, 2])
    coordinator.revision_supplier = lambda: next(values, 2)

    coordinator.start()
    status = coordinator.wait(timeout=5)

    assert status["phase"] == "failed"
    assert status["outcome"] == "staleConfiguration"
    assert store.load_snapshot("all") is None


def test_publication_guard_wraps_the_freshness_check(tmp_path):
    provider = _paged_provider(objects={PRIMARY: ["a"], "T2": []})
    events: list[str] = []

    class Guard:
        def __enter__(self):
            events.append("enter")
            return None

        def __exit__(self, *exc):
            events.append("exit")
            return False

    coordinator, store, _, _ = _coordinator(
        tmp_path, provider, config_guard=lambda: Guard()
    )

    coordinator.start()
    status = coordinator.wait(timeout=5)

    assert status["phase"] == "complete"
    assert events == ["enter", "exit"]


# ---------------------------------------------------------------------------
# Single-flight
# ---------------------------------------------------------------------------

def test_two_racing_starts_admit_one_job(tmp_path):
    release = threading.Event()
    provider = _paged_provider(objects={PRIMARY: ["a"], "T2": []})
    provider.on_fetch = lambda: release.wait(timeout=5)
    coordinator, _, _, _ = _coordinator(tmp_path, provider)

    coordinator.start()
    with pytest.raises(rr.RefreshBusyError):
        coordinator.start()
    release.set()
    coordinator.wait(timeout=5)


def test_is_running_tracks_the_job_thread(tmp_path):
    """The route seam's rebuild guard needs a truthful liveness read.

    A cached coordinator may only be replaced while its job thread is dead:
    swapping the instance under a live job would orphan the thread and leave
    cancel/status on a second instance that does not own the job.
    """
    release = threading.Event()
    provider = _paged_provider(objects={PRIMARY: ["a"], "T2": []})
    provider.on_fetch = lambda: release.wait(timeout=5)
    coordinator, _, _, _ = _coordinator(tmp_path, provider)

    assert coordinator.is_running() is False
    coordinator.start()
    assert coordinator.is_running() is True
    release.set()
    coordinator.wait(timeout=5)
    assert coordinator.is_running() is False


def test_a_foreign_process_lock_blocks_start(tmp_path):
    provider = _paged_provider(objects={PRIMARY: ["a"], "T2": []})
    coordinator, _, _, _ = _coordinator(tmp_path, provider)
    lock_path = coordinator.job_lock_path

    import fcntl

    handle = open(lock_path, "a+")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(rr.RefreshBusyError):
            coordinator.start()
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


# ---------------------------------------------------------------------------
# Restart interruption
# ---------------------------------------------------------------------------

def test_restart_reports_interruption_and_keeps_snapshot_usable(tmp_path):
    provider = _paged_provider(objects={PRIMARY: ["a"], "T2": ["c"]})
    coordinator, store, _, _ = _coordinator(tmp_path, provider)
    coordinator.start()
    coordinator.wait(timeout=5)
    generation = store.load_snapshot("all").generation

    # Simulate a died process: an unfinished run marker is left behind.
    store.note_run_started("all", 1, started_at=1_000.0)

    restarted, store2, _, _ = _coordinator(tmp_path, provider)
    status = restarted.status()

    assert status["phase"] == "interrupted"
    assert status["outcome"] == "interrupted"
    assert status["snapshot"]["present"] is True
    assert status["snapshot"]["generation"] == generation


# ---------------------------------------------------------------------------
# Truthful failure without secrets
# ---------------------------------------------------------------------------

def test_unreadable_content_fails_without_install_but_persists_reads(tmp_path):
    provider = _paged_provider(objects={PRIMARY: ["a", "b"], "T2": []})
    provider.fail_gets["b"] = FileNotFoundError("404")
    coordinator, store, _, _ = _coordinator(tmp_path, provider)

    coordinator.start()
    status = coordinator.wait(timeout=5)

    assert status["phase"] == "failed"
    assert status["outcome"] == "noCapacities"
    assert store.get("a", PRIMARY) is not None  # successful read kept
    assert store.load_snapshot("all") is None


def test_provider_failure_warning_does_not_leak_the_exception_text(tmp_path):
    provider = _paged_provider(objects={PRIMARY: ["a"], "T2": []})
    provider.list_error = RuntimeError("Bearer super-secret-token")
    coordinator, store, _, _ = _coordinator(tmp_path, provider)

    coordinator.start()
    status = coordinator.wait(timeout=5)

    assert status["phase"] == "failed"
    assert all("super-secret-token" not in w for w in status["warnings"])


def test_malformed_required_content_is_not_treated_as_unknown(tmp_path):
    provider = _paged_provider(objects={PRIMARY: ["a"], "T2": []})
    provider._objects["a"] = {"id": "a", "structureId": PRIMARY}  # no properties
    coordinator, store, _, _ = _coordinator(tmp_path, provider)

    coordinator.start()
    status = coordinator.wait(timeout=5)

    assert status["phase"] == "failed"
    assert store.load_snapshot("all") is None
