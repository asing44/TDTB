"""Focused behavior tests for the paced direct Capacities refresh coordinator (U2).

Everything here is local and fake: a deterministic provider, an injected
monotonic clock and sleeper (no real ``time.sleep``), a ``tmp_path`` state
root, and no credential, provider, or ``$HOME`` read. The durable store under
test is U1 (``capacities_refresh_state``); this file owns the acquisition,
pacing, single-flight, cancellation, freshness, and publication behavior.
"""
from __future__ import annotations

import json
import logging
import sys
import threading
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import capacities_adapter as ca  # noqa: E402
import capacities_assignment as cas  # noqa: E402
import capacities_builder as cb  # noqa: E402
import capacities_refresh as rr  # noqa: E402
import capacities_settings as cs  # noqa: E402
import exclusion_settings as es  # noqa: E402
import tag_exclusions as tx  # noqa: E402

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
    _structures,
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
    configuration_revision=None,
    assignment_settings=None,
    exclusion_policy=None,
    wall_clock=None,
    content_ttl=None,
    rate_limit_budget_seconds=None,
    max_backoff=None,
    type_order=None,
):
    clock = clock or Clock()
    sleeper = sleeper if sleeper is not None else Sleeper(clock)
    store = _store(tmp_path)
    frozen = (
        {}
        if configuration_revision is None
        else {"configuration_revision": configuration_revision}
    )
    # Only pass the wall clock / TTL overrides the caller supplied so the
    # coordinator's own defaults keep applying elsewhere.
    overrides = {}
    if wall_clock is not None:
        overrides["wall_clock"] = wall_clock
    if content_ttl is not None:
        overrides["content_ttl"] = content_ttl
    if rate_limit_budget_seconds is not None:
        overrides["rate_limit_budget_seconds"] = rate_limit_budget_seconds
    if max_backoff is not None:
        overrides["max_backoff"] = max_backoff
    if type_order is not None:
        overrides["type_order"] = type_order
    return (
        rr.RefreshCoordinator(
            root=tmp_path / "state",
            store=store,
            provider=provider,
            space_id=SPACE,
            mappings=tuple(
                m if isinstance(m, ca.StructureMapping) else _mapping(m)
                for m in mappings
            ),
            revision_supplier=revision or Revision(),
            scope_key="all",
            max_pages=max_pages,
            clock=clock,
            sleeper=sleeper,
            max_retries=max_retries,
            config_guard=config_guard,
            assignment_settings=assignment_settings,
            exclusion_policy=exclusion_policy,
            **frozen,
            **overrides,
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
# Refresh read-through TTL — stale content is re-read, fresh content is reused
# ---------------------------------------------------------------------------

def test_default_refresh_content_ttl_is_ten_minutes():
    assert rr.DEFAULT_CONTENT_TTL_SECONDS == 600.0


def test_warm_refresh_reverifies_content_older_than_ttl(tmp_path):
    wall = Clock(now=10_000.0)
    provider = _paged_provider(objects={PRIMARY: ["a", "b"], "T2": []})
    # The provider holds the edited content the stale cache is missing.
    provider._objects["a"] = _content("a", PRIMARY, assigned=False)
    coordinator, store, _, _ = _coordinator(
        tmp_path, provider, wall_clock=wall, content_ttl=600.0
    )
    # 'a' was cached long before the TTL; 'b' was cached just now.
    store.put("a", PRIMARY, _content("a", PRIMARY), content_read_at=9_000.0)
    store.put("b", PRIMARY, _content("b", PRIMARY), content_read_at=10_000.0)

    coordinator.start()
    status = coordinator.wait(timeout=5)

    assert status["phase"] == "complete", status
    assert provider.get_calls == ["a"]
    cached = store.get("a", PRIMARY)
    assert cached.content == _content("a", PRIMARY, assigned=False)
    assert cached.content_read_at == 10_000.0  # renewed by the re-read
    snapshot = store.load_snapshot("all")
    assert snapshot is not None and snapshot.generation == 1
    assert {m.object_id for m in snapshot.members} == {"a", "b"}
    # Only the stale object cost a read; the fresh one was reused.
    assert status["job"]["progress"]["read"] == 1
    assert status["job"]["progress"]["types"][PRIMARY]["read"] == 1


def test_warm_refresh_reuses_content_within_the_ttl(tmp_path):
    wall = Clock(now=10_000.0)
    provider = _paged_provider(objects={PRIMARY: ["a"], "T2": ["c"]})
    coordinator, store, _, _ = _coordinator(
        tmp_path, provider, wall_clock=wall, content_ttl=600.0
    )
    store.put("a", PRIMARY, _content("a", PRIMARY), content_read_at=9_500.0)
    store.put("c", "T2", _content("c", "T2"), content_read_at=10_000.0)

    coordinator.start()
    status = coordinator.wait(timeout=5)

    assert status["phase"] == "complete", status
    assert provider.get_calls == []
    assert status["job"]["progress"]["read"] == 0
    assert {m.object_id for m in store.load_snapshot("all").members} == {"a", "c"}


def test_refresh_publishes_the_changed_property_from_a_stale_reread(tmp_path):
    wall = Clock(now=10_000.0)
    provider = _paged_provider(objects={PRIMARY: ["a"], "T2": []})
    provider._objects["a"]["properties"]["title"]["title"]["value"] = "Renamed"
    coordinator, store, _, _ = _coordinator(
        tmp_path, provider, wall_clock=wall, content_ttl=600.0
    )
    store.put("a", PRIMARY, _content("a", PRIMARY), content_read_at=9_000.0)
    assert (
        store.get("a", PRIMARY).content["properties"]["title"]["title"]["value"]
        == "A"
    )

    coordinator.start()
    status = coordinator.wait(timeout=5)

    assert status["phase"] == "complete", status
    snapshot = store.load_snapshot("all")
    assert snapshot is not None and snapshot.has("a", PRIMARY)
    # The installed generation points at this cached read (read_direct_intake
    # projects exactly ``store.get(member)``), so the edit reaches TDTB.
    assert (
        store.get("a", PRIMARY).content["properties"]["title"]["title"]["value"]
        == "Renamed"
    )


def test_rescan_rereads_fresh_content_ignoring_the_ttl(tmp_path):
    wall = Clock(now=10_000.0)
    provider = _paged_provider(objects={PRIMARY: ["a", "b"], "T2": []})
    coordinator, store, _, _ = _coordinator(
        tmp_path, provider, wall_clock=wall, content_ttl=600.0
    )
    # Both objects are well inside the TTL, yet a Rescan must still read them.
    store.put("a", PRIMARY, _content("a", PRIMARY), content_read_at=10_000.0)
    store.put("b", PRIMARY, _content("b", PRIMARY), content_read_at=10_000.0)

    coordinator.start(mode="rescan", scope=PRIMARY)
    status = coordinator.wait(timeout=5)

    assert status["phase"] == "complete", status
    assert set(provider.get_calls) == {"a", "b"}
    assert status["job"]["progress"]["read"] == 2


def test_stale_reread_failure_keeps_the_prior_generation(tmp_path):
    wall = Clock(now=10_000.0)
    provider = _paged_provider(objects={PRIMARY: ["a"], "T2": []})
    coordinator, store, _, _ = _coordinator(
        tmp_path, provider, wall_clock=wall, content_ttl=600.0
    )
    # Establish one complete generation, then age its cached content past the
    # TTL and make the required re-read fail.
    coordinator.start()
    assert coordinator.wait(timeout=5)["phase"] == "complete"
    prior = store.load_snapshot("all")
    assert prior is not None and prior.generation == 1
    store.put("a", PRIMARY, _content("a", PRIMARY), content_read_at=9_000.0)
    provider.get_error = RuntimeError("boom")

    coordinator.start()
    status = coordinator.wait(timeout=5)

    assert status["phase"] == "failed"
    assert status["outcome"] == "noCapacities"
    # No partial generation: the previous complete result is preserved.
    kept = store.load_snapshot("all")
    assert kept is not None and kept.generation == prior.generation


# ---------------------------------------------------------------------------
# AE4 — scoped Rescan
# ---------------------------------------------------------------------------

def test_rescan_one_type_rereads_only_that_type(tmp_path):
    provider = _paged_provider(objects={PRIMARY: ["a", "b"], "T2": ["c"]})
    coordinator, store, _, _ = _coordinator(tmp_path, provider)
    coordinator.start()
    coordinator.wait(timeout=5)
    # KTD7's priority order: the one-read type finishes first, then the two-read
    # primary type, and each listing is read in its own order.
    assert provider.get_calls == ["c", "a", "b"]

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
    prior_generation = store.load_snapshot("all").generation
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
    assert snapshot is not None and snapshot.generation == prior_generation


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


def test_frozen_configuration_revision_blocks_a_save_between_build_and_start(
    tmp_path,
):
    provider = _paged_provider(objects={PRIMARY: ["a"], "T2": ["c"]})
    # The coordinator was built against revision 1; a save lands before start,
    # so the live supplier would report 2.
    coordinator, store, _, _ = _coordinator(
        tmp_path, provider, configuration_revision=1, revision=Revision(2)
    )

    coordinator.start()
    status = coordinator.wait(timeout=5)

    assert status["phase"] == "failed", status
    assert status["outcome"] == "staleConfiguration", status
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


# ---------------------------------------------------------------------------
# U4 S0 — the structure contract rides only a published generation
# ---------------------------------------------------------------------------

def _renamed_structures():
    structures = _structures(*TYPES)
    structures[0]["title"] = "Renamed"
    return structures


def test_a_published_refresh_persists_its_structure_contract(tmp_path):
    provider = _paged_provider(objects={PRIMARY: ["a"], "T2": ["c"]})
    coordinator, store, _, _ = _coordinator(tmp_path, provider)

    coordinator.start()
    status = coordinator.wait(timeout=5)

    assert status["phase"] == "complete", status
    contract = store.load_structure_contract("all")
    assert contract is not None
    # The contract rides the complete publication, whichever generation number
    # the run's interim publications reached first.
    published = store.load_snapshot("all")
    assert published.partial is False
    assert contract.generation == published.generation
    assert contract.structures == _structures(*TYPES)


def test_a_single_type_rescan_stores_the_full_structure_contract(tmp_path):
    provider = _paged_provider(objects={PRIMARY: ["a"], "T2": ["c"]})
    coordinator, store, _, _ = _coordinator(tmp_path, provider)
    coordinator.start()
    coordinator.wait(timeout=5)

    # The adapter resolves its contract once, so this rescan reuses it. The
    # stored payload must still cover every type, not only the rescoped one.
    coordinator.start(mode="rescan", scope=PRIMARY)
    status = coordinator.wait(timeout=5)

    assert status["phase"] == "complete", status
    contract = store.load_structure_contract("all")
    assert contract is not None
    assert contract.generation == store.load_snapshot("all").generation
    assert contract.structures == _structures(*TYPES)


def test_a_cancelled_run_keeps_the_previous_generations_contract(tmp_path):
    first_provider = _paged_provider(objects={PRIMARY: ["a", "b"], "T2": ["c"]})
    first, store, _, _ = _coordinator(tmp_path, first_provider)
    first.start()
    assert first.wait(timeout=5)["phase"] == "complete"
    previous = store.load_structure_contract("all")
    assert previous is not None

    # A later run fetches a changed payload, then is cancelled before
    # publication: the previous generation's contract must stay in place.
    changed = _paged_provider(
        objects={PRIMARY: ["a", "b"], "T2": ["c"]}, structures=_renamed_structures()
    )
    second, _, _, _ = _coordinator(tmp_path, changed)
    changed.on_get = lambda object_id: second.cancel()

    second.start(mode="rescan", scope=PRIMARY)
    status = second.wait(timeout=5)

    assert status["phase"] == "cancelled", status
    assert changed.fetch_calls == 1
    kept = store.load_structure_contract("all")
    assert kept is not None
    assert kept.generation == previous.generation
    assert kept.structures == previous.structures


def test_a_refused_refresh_keeps_the_previous_generations_contract(tmp_path):
    first_provider = _paged_provider(objects={PRIMARY: ["a"], "T2": ["c"]})
    first, store, _, _ = _coordinator(tmp_path, first_provider)
    first.start()
    assert first.wait(timeout=5)["phase"] == "complete"
    previous = store.load_structure_contract("all")
    assert previous is not None

    # The configuration moves between start and publication, so the run fetches
    # a changed payload and is refused before it installs anything.
    changed = _paged_provider(
        objects={PRIMARY: ["a"], "T2": ["c"]}, structures=_renamed_structures()
    )
    values = iter([1, 2])
    second, _, _, _ = _coordinator(tmp_path, changed, revision=lambda: next(values, 2))

    second.start()
    status = second.wait(timeout=5)

    assert status["phase"] == "failed", status
    assert status["outcome"] == "staleConfiguration", status
    kept = store.load_structure_contract("all")
    assert kept is not None
    assert kept.generation == previous.generation
    assert kept.structures == previous.structures


# ---------------------------------------------------------------------------
# Persisted-settings compatibility — legacy files and Assigned On declarations
# ---------------------------------------------------------------------------

def _v2_settings_doc(types: dict | None = None, **overrides) -> dict:
    """A version-2 settings document with optional per-type declarations."""
    doc = {
        "version": 2,
        "revision": 3,
        "statuses": {"open": ["active"], "completed": ["Completed", "Dropped"]},
        "types": dict(types or {}),
        "native": {"deadline_horizon_days": 2},
        "excluded": {},
    }
    doc.update(overrides)
    return doc


def _legacy_v1_settings_doc() -> dict:
    """A version-1 save written BEFORE ``assigned_on_structures`` existed."""
    return {
        "version": 1,
        "revision": 3,
        "native_task_auto": {
            "active_enabled": True,
            "due_enabled": True,
            "deadline_enabled": True,
            "deadline_horizon_days": 2,
        },
        "excluded": {},
        "active_structures": {},
    }


def _write_settings(doc: dict) -> None:
    path = cs.settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc), encoding="utf-8")


def _settings_refresh(tmp_path, provider, *, exclusion_policy=None):
    """Run one full refresh through the settings-resolved mappings."""
    settings = cs.read_settings().settings
    mappings = cb._resolve_assigned_structures(
        tuple(_mapping(t) for t in TYPES),
        settings,
    )
    coordinator, store, _, _ = _coordinator(
        tmp_path,
        provider,
        mappings=mappings,
        assignment_settings=cas.AssignmentSettings.from_v2(settings),
        exclusion_policy=exclusion_policy,
    )
    coordinator.start()
    return coordinator.wait(timeout=5)


def _structures_with_assigned_on(*type_ids: str) -> list[dict]:
    structures = _structures(*type_ids)
    for row in structures:
        row["propertyDefinitions"].append({"id": "assigned_on", "type": "date"})
    return structures


def test_legacy_settings_without_assigned_on_structures_completes(tmp_path):
    """A pre-Assigned-On version-1 settings file is valid on read and cannot
    fail a run: it translates to a document with no type declarations."""
    _write_settings(_legacy_v1_settings_doc())
    provider = _paged_provider(objects={PRIMARY: ["a"], "T2": ["c"]})

    status = _settings_refresh(tmp_path, provider)

    assert status["phase"] == "complete", status
    assert status["outcome"] == "published"
    # Version 1's native task structures translate to ``native`` declarations;
    # an undeclared structure stays off.
    assert cs.read_settings().settings.mode_of(PRIMARY) == "native"
    assert cs.read_settings().settings.mode_of("T2") == "off"


def test_assigned_on_mapped_to_date_property_completes(tmp_path):
    """A saved Assigned On declaration resolves and never gates the run."""
    _write_settings(
        _v2_settings_doc(
            {PRIMARY: {"mode": "assignedOn", "property": "assigned_on"}},
        )
    )
    provider = _paged_provider(
        objects={PRIMARY: ["a"], "T2": ["c"]},
        structures=_structures_with_assigned_on(*TYPES),
    )
    provider._objects["a"]["properties"]["assigned_on"] = {
        "type": "date",
        "date": {"start": "2026-10-13"},
    }

    status = _settings_refresh(tmp_path, provider)

    assert status["phase"] == "complete", status


@pytest.mark.parametrize("property_id", ["assigned", "ghost-property"])
def test_assigned_on_declaration_non_date_or_unknown_still_completes(
    tmp_path, property_id
):
    """A mistyped Assigned On declaration is ignored with one bounded warning
    and cannot fail the refresh (fail-open contract)."""
    _write_settings(
        _v2_settings_doc(
            {PRIMARY: {"mode": "assignedOn", "property": property_id}},
        )
    )
    provider = _paged_provider(objects={PRIMARY: ["a"], "T2": ["c"]})

    status = _settings_refresh(tmp_path, provider)

    assert status["phase"] == "complete", status
    ignored = [
        warning
        for warning in status["warnings"]
        if "ignored Capacities Assigned On declaration" in warning
    ]
    assert len(ignored) == 1
    assert property_id in ignored[0]


def _structures_without_legacy_assignment(*type_ids: str) -> list[dict]:
    """Definitions after the operator removed the boolean Assigned property,
    keeping the Assigned On date declaration available."""
    structures = _structures_with_assigned_on(*type_ids)
    for row in structures:
        row["propertyDefinitions"] = [
            definition
            for definition in row["propertyDefinitions"]
            if definition["id"] != "assigned"
        ]
    return structures


def test_vanished_legacy_assignment_property_completes_when_assigned_on_mapped(
    tmp_path,
):
    """The boolean Assigned marker vanished from the definitions while the
    persisted legacy mapping still names it. With Assigned On mapped, the
    legacy marker and its values are dropped fail-open with one bounded
    warning per structure, and the refresh still completes."""
    _write_settings(
        _v2_settings_doc(
            {
                PRIMARY: {"mode": "assignedOn", "property": "assigned_on"},
                "T2": {"mode": "assignedOn", "property": "assigned_on"},
            },
        )
    )
    provider = _paged_provider(
        objects={PRIMARY: ["a"], "T2": ["c"]},
        structures=_structures_without_legacy_assignment(*TYPES),
    )

    status = _settings_refresh(tmp_path, provider)

    assert status["phase"] == "complete", status
    warnings = [
        warning
        for warning in status["warnings"]
        if warning.startswith("Legacy assignment property")
    ]
    assert len(warnings) == 2
    assert (
        "Legacy assignment property 'assigned' for structure 'RootTask' is no "
        "longer defined in Capacities; ignored. Open Settings > Repair "
        "connections to map Assigned To Me again." in warnings
    )
    assert (
        "Legacy assignment property 'assigned' for structure 'T2' is no "
        "longer defined in Capacities; ignored. Open Settings > Repair "
        "connections to map Assigned To Me again." in warnings
    )


def test_vanished_legacy_assignment_property_completes_without_assigned_on(
    tmp_path,
):
    """Without Assigned On the vanished marker is still dropped fail-open with
    the same bounded warning, and the mapping's status seam keeps the
    structure contributing: the refresh completes."""
    _write_settings(
        _v2_settings_doc({PRIMARY: {"mode": "native", "statuses": []}})
    )
    provider = _paged_provider(
        objects={PRIMARY: ["a"], "T2": ["c"]},
        structures=_structures_without_legacy_assignment(*TYPES),
    )

    status = _settings_refresh(tmp_path, provider)

    assert status["phase"] == "complete", status
    warnings = [
        warning
        for warning in status["warnings"]
        if warning.startswith("Legacy assignment property")
    ]
    assert len(warnings) == 2
    assert (
        "Legacy assignment property 'assigned' for structure 'RootTask' is no "
        "longer defined in Capacities; ignored. Open Settings > Repair "
        "connections to map Assigned To Me again." in warnings
    )


def test_vanished_marker_without_status_or_rule_skips_structure_with_warning(
    tmp_path,
):
    """After the marker and any status property are gone and no rule or Active
    enable exists, the structure is not an assignment source: it is not
    enumerated, one warning names the vanished marker, and the refresh still
    completes."""
    structures = _structures(PRIMARY)
    structures.append(
        {
            "id": "custom-project",
            "title": "Custom",
            "propertyDefinitions": [{"id": "title", "type": "title"}],
        }
    )
    provider = _paged_provider(
        objects={PRIMARY: ["a"], "custom-project": ["c"]},
        structures=structures,
    )
    coordinator, _, _, _ = _coordinator(
        tmp_path,
        provider,
        mappings=(
            _mapping(PRIMARY),
            ca.StructureMapping(
                structure_id="custom-project",
                assignment_property="assigned",
                assignment_values=frozenset({"true"}),
                title_property="title",
            ),
        ),
    )
    coordinator.start()
    status = coordinator.wait(timeout=5)

    assert status["phase"] == "complete", status
    assert ("custom-project", None) not in provider.list_calls
    assert [
        warning
        for warning in status["warnings"]
        if warning.startswith("Legacy assignment property")
    ] == [
        "Legacy assignment property 'assigned' for structure 'custom-project' "
        "is no longer defined in Capacities; ignored. Open Settings > Repair "
        "connections to map Assigned To Me again."
    ]


@pytest.mark.parametrize("mode", ["refresh", "rescan"])
def test_vanished_marker_retains_prior_members_without_claiming_freshness(tmp_path, mode):
    custom = "custom-project"
    mappings = (
        _mapping(PRIMARY),
        ca.StructureMapping(
            structure_id=custom, assignment_property="assigned",
            assignment_values=frozenset({"true"}), title_property="title",
        ),
    )
    first = _paged_provider(objects={PRIMARY: ["a"], custom: ["c"]},
                            structures=_structures(PRIMARY, custom))
    coordinator, store, _, _ = _coordinator(tmp_path, first, mappings=mappings)
    coordinator.start()
    assert coordinator.wait(timeout=5)["phase"] == "complete"
    prior = store.load_snapshot("all")

    structures = _structures(PRIMARY)
    structures.append({"id": custom, "title": "Custom",
                       "propertyDefinitions": [{"id": "title", "type": "title"}]})
    provider = _paged_provider(objects={PRIMARY: ["a"], custom: ["c"]},
                               structures=structures)
    coordinator, store, _, _ = _coordinator(tmp_path, provider, mappings=mappings)
    coordinator.start(mode=mode)
    status = coordinator.wait(timeout=5)

    assert status["phase"] == "complete", status
    snapshot = store.load_snapshot("all")
    assert snapshot.generation == prior.generation + 1
    assert {m.object_id for m in snapshot.members} == {"a", "c"}
    assert snapshot.check_time(custom) == prior.check_time(custom)
    assert next(m for m in snapshot.members if m.object_id == "c") == next(
        m for m in prior.members if m.object_id == "c"
    )
    assert provider.list_calls == [(PRIMARY, None)]
    assert any("retained" in w and "not refreshed" in w for w in status["warnings"])


def test_truly_empty_mapping_still_fails_the_refresh(tmp_path):
    """A mapping that never configured an assignment property or a status
    property is genuine misconfiguration: the refresh still fails with the
    id-only contract detail."""
    provider = _paged_provider(objects={PRIMARY: ["a"], "T2": ["c"]})
    coordinator, _, _, _ = _coordinator(
        tmp_path,
        provider,
        mappings=(
            _mapping(PRIMARY),
            ca.StructureMapping(structure_id="T2", title_property="title"),
        ),
    )
    coordinator.start()
    status = coordinator.wait(timeout=5)

    assert status["phase"] == "failed", status
    assert (
        "Cause: CapacitiesContractError "
        "(assignment or status mapping required structure=T2)" in status["warnings"]
    )
    assert "Where: list" in status["warnings"]


def test_assigned_on_only_mapping_without_status_or_assignment_property_completes(
    tmp_path,
):
    """An Assigned On-mapped structure needs no legacy marker and no status
    property: the declaration alone satisfies the contract, and it is also the
    enumeration path, so the structure is listed for the date read."""
    _write_settings(
        _v2_settings_doc(
            {"custom-project": {"mode": "assignedOn", "property": "assigned_on"}},
        )
    )
    settings = cs.read_settings().settings
    mappings = cb._resolve_assigned_structures(
        (
            _mapping(PRIMARY),
            ca.StructureMapping(structure_id="custom-project", title_property="title"),
        ),
        settings,
    )
    structures = _structures(PRIMARY)
    structures.append(
        {
            "id": "custom-project",
            "title": "Custom",
            "propertyDefinitions": [
                {"id": "title", "type": "title"},
                {"id": "assigned_on", "type": "date"},
            ],
        }
    )
    provider = _paged_provider(
        objects={PRIMARY: ["a"], "custom-project": []},
        structures=structures,
    )
    coordinator, _, _, _ = _coordinator(
        tmp_path,
        provider,
        mappings=mappings,
        assignment_settings=cas.AssignmentSettings.from_v2(settings),
    )
    coordinator.start()
    status = coordinator.wait(timeout=5)

    assert status["phase"] == "complete", status
    assert provider.list_calls == [(PRIMARY, None), ("custom-project", None)]


def test_legacy_exclusions_without_todoist_dimension_completes(tmp_path):
    """A tags-only exclusions file reads as an empty Todoist dimension and the
    refresh still publishes with the policy applied."""
    path = es.settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "revision": 3,
                "exclusions": {"tags": []},
            }
        ),
        encoding="utf-8",
    )
    policy = tx.ExclusionPolicy.from_read(es.read_settings())
    assert policy.todoist == ()
    provider = _paged_provider(objects={PRIMARY: ["a"], "T2": ["c"]})

    status = _settings_refresh(tmp_path, provider, exclusion_policy=policy)

    assert status["phase"] == "complete", status


# ---------------------------------------------------------------------------
# Bounded failure cause + stage — diagnosable without leaking content
# ---------------------------------------------------------------------------

def test_unexpected_failure_records_class_and_stage_without_message(
    tmp_path, caplog
):
    provider = _paged_provider(objects={PRIMARY: ["a"], "T2": []})
    provider.list_error = KeyError("secret-key-text")
    coordinator, _, _, _ = _coordinator(tmp_path, provider, max_retries=0)

    caplog.set_level(logging.WARNING, logger="tdtb.capacities_refresh")
    coordinator.start()
    status = coordinator.wait(timeout=5)

    assert status["phase"] == "failed"
    assert rr.FAILURE_WARNING in status["warnings"]
    assert "Cause: KeyError" in status["warnings"]
    assert "Where: list" in status["warnings"]
    assert all("secret-key-text" not in warning for warning in status["warnings"])
    messages = [
        record.getMessage()
        for record in caplog.records
        if record.name == "tdtb.capacities_refresh"
    ]
    assert any("KeyError" in message for message in messages)
    assert all("secret-key-text" not in message for message in messages)


def test_rate_limited_failure_records_retry_after_and_stage(tmp_path, caplog):
    provider = _paged_provider(objects={PRIMARY: ["a"], "T2": []})
    provider.list_error = ca.CapacitiesRateLimited(
        "secret retry text", retry_after=60.0
    )
    coordinator, _, _, sleeper = _coordinator(tmp_path, provider, max_retries=0)

    caplog.set_level(logging.WARNING, logger="tdtb.capacities_refresh")
    coordinator.start()
    status = coordinator.wait(timeout=5)

    assert status["phase"] == "failed"
    assert "Cause: CapacitiesRateLimited (retry after 60s)" in status["warnings"]
    assert "Where: list" in status["warnings"]
    assert all("secret retry text" not in warning for warning in status["warnings"])
    assert sleeper.calls == []
    messages = [
        record.getMessage()
        for record in caplog.records
        if record.name == "tdtb.capacities_refresh"
    ]
    assert any("CapacitiesRateLimited" in message for message in messages)
    assert all("secret retry text" not in message for message in messages)


def test_contract_failure_records_class_and_stage(tmp_path):
    provider = _paged_provider(objects={PRIMARY: ["a"], "T2": []})
    provider.list_error = ca.CapacitiesContractError("secret contract text")
    coordinator, _, _, _ = _coordinator(tmp_path, provider, max_retries=0)

    coordinator.start()
    status = coordinator.wait(timeout=5)

    assert status["phase"] == "failed"
    assert "Cause: CapacitiesContractError" in status["warnings"]
    assert "Where: list" in status["warnings"]
    assert all(
        "secret contract text" not in warning for warning in status["warnings"]
    )


def test_contract_failure_records_bounded_identifier_detail(tmp_path):
    provider = _paged_provider(objects={PRIMARY: ["a"], "T2": []})
    provider.list_error = ca.CapacitiesContractError(
        "secret contract text",
        detail="structure=RootTask property=assigned",
    )
    coordinator, _, _, _ = _coordinator(tmp_path, provider, max_retries=0)

    coordinator.start()
    status = coordinator.wait(timeout=5)

    assert status["phase"] == "failed"
    assert (
        "Cause: CapacitiesContractError (structure=RootTask property=assigned)"
        in status["warnings"]
    )
    assert all(
        "secret contract text" not in warning for warning in status["warnings"]
    )


def test_contract_detail_is_bounded_and_payload_shaped_errors_have_none():
    assert (
        rr._failure_cause_line(
            ca.CapacitiesContractError("secret", detail="p" * 500)
        )
        == "Cause: CapacitiesContractError (" + "p" * 200 + ")"
    )
    assert (
        rr._failure_cause_line(
            ca.CapacitiesContractError("Capacities structures response is malformed")
        )
        == "Cause: CapacitiesContractError"
    )


def test_assemble_failure_records_stage_without_message(tmp_path):
    provider = _paged_provider(objects={PRIMARY: ["a"], "T2": ["c"]})
    coordinator, _, _, _ = _coordinator(tmp_path, provider)

    def boom(logical_day, objects):
        raise RuntimeError("secret assemble text")

    coordinator.adapter.items_for_day_from_objects = boom
    coordinator.start()
    status = coordinator.wait(timeout=5)

    assert status["phase"] == "failed"
    assert "Cause: RuntimeError" in status["warnings"]
    assert "Where: assemble" in status["warnings"]
    assert all(
        "secret assemble text" not in warning for warning in status["warnings"]
    )


def test_install_failure_records_stage_without_message(tmp_path):
    provider = _paged_provider(objects={PRIMARY: ["a"], "T2": []})
    coordinator, _, _, _ = _coordinator(
        tmp_path, provider, configuration_revision=1
    )

    def boom():
        raise RuntimeError("secret install text")

    coordinator.revision_supplier = boom
    coordinator.start()
    status = coordinator.wait(timeout=5)

    assert status["phase"] == "failed"
    assert "Cause: RuntimeError" in status["warnings"]
    assert "Where: install" in status["warnings"]
    assert all(
        "secret install text" not in warning for warning in status["warnings"]
    )


# ---------------------------------------------------------------------------
# U1 (KTD3/KTD4) — budgeted rate-limit waits with a visible resume target
# ---------------------------------------------------------------------------

def test_default_budget_waits_out_five_rate_limits_then_publishes(tmp_path):
    clock = Clock()
    sleeper = Sleeper(clock)
    provider = _paged_provider(objects={PRIMARY: ["a"], "T2": []})
    attempts = {"n": 0}

    def flaky(object_id):
        if object_id == "a" and attempts["n"] < 5:
            attempts["n"] += 1
            raise ca.CapacitiesRateLimited("slow down", retry_after=40.0)

    provider.on_get = flaky
    coordinator, store, _, _ = _coordinator(
        tmp_path,
        provider,
        clock=clock,
        sleeper=sleeper,
        max_retries=None,
        wall_clock=lambda: 1000.0,
    )
    seen = []
    sleeper.on_sleep = lambda seconds: seen.append(coordinator.status()["progress"])

    coordinator.start()
    status = coordinator.wait(timeout=5)

    assert status["phase"] == "complete", status
    assert status["outcome"] == "published"
    # Five waits of exactly the announced retry-after, then a sixth read.
    assert sleeper.calls.count(40.0) == 5
    assert provider.get_calls.count("a") == 6
    assert store.load_snapshot("all") is not None
    # The wait was visible while it was happening, with a wall-clock resume.
    assert len(seen) == 5
    for progress in seen:
        assert progress["waiting"] == {
            "reason": "rateLimited",
            "endpoint": rr.ENDPOINT_CONTENT,
            "retry_after": 40.0,
            "resume_at": 1040.0,
        }
    assert status["progress"]["waiting"] is None


def test_rate_limit_budget_exhaustion_fails_truthfully_and_keeps_reads(tmp_path):
    clock = Clock()
    sleeper = Sleeper(clock)
    provider = _paged_provider(objects={PRIMARY: ["a", "b"], "T2": []})
    provider.fail_gets = {"b": ca.CapacitiesRateLimited("busy", retry_after=60.0)}
    coordinator, store, _, _ = _coordinator(
        tmp_path,
        provider,
        clock=clock,
        sleeper=sleeper,
        max_retries=None,
        rate_limit_budget_seconds=100.0,
    )

    coordinator.start()
    status = coordinator.wait(timeout=5)

    assert status["phase"] == "failed"
    assert status["outcome"] == "noCapacities"
    assert rr.FAILURE_WARNING in status["warnings"]
    assert "Cause: CapacitiesRateLimited (retry after 60s)" in status["warnings"]
    assert "Where: read" in status["warnings"]
    # One 60s wait fits the 100s budget; the second does not and fails.
    assert sleeper.calls == [60.0]
    # The successful read from before the exhausted wait is still cached.
    assert store.get("a", PRIMARY) is not None
    assert store.load_snapshot("all") is None
    assert status["progress"]["waiting"] is None


def test_retry_after_sixty_seconds_is_honored_not_clamped_to_thirty(tmp_path):
    clock = Clock()
    sleeper = Sleeper(clock)
    provider = _paged_provider(objects={PRIMARY: ["a"], "T2": []})
    attempts = {"n": 0}

    def flaky(object_id):
        if object_id == "a" and attempts["n"] == 0:
            attempts["n"] += 1
            raise ca.CapacitiesRateLimited("slow down", retry_after=60.0)

    provider.on_get = flaky
    coordinator, _, _, _ = _coordinator(
        tmp_path, provider, clock=clock, sleeper=sleeper, max_retries=None
    )

    coordinator.start()
    status = coordinator.wait(timeout=5)

    assert status["phase"] == "complete"
    assert sleeper.calls == [60.0]


def test_progress_to_read_counts_uncached_stale_and_rescan(tmp_path):
    clock = Clock()
    sleeper = Sleeper(clock)
    wall = {"now": 1000.0}
    provider = _paged_provider(objects={PRIMARY: ["a", "b"], "T2": ["c"]})
    coordinator, store, _, _ = _coordinator(
        tmp_path,
        provider,
        clock=clock,
        sleeper=sleeper,
        wall_clock=lambda: wall["now"],
    )
    # "a" and "c" are warm at the fake wall clock; "b" is uncached.
    for object_id, type_key in (("a", PRIMARY), ("c", "T2")):
        store.put(
            object_id,
            type_key,
            _content(object_id, type_key),
            content_read_at=wall["now"],
        )

    coordinator.start()
    first = coordinator.wait(timeout=5)
    assert first["progress"]["to_read"] == 1

    # A back-to-back refresh inside the TTL needs no provider read.
    coordinator.start()
    warm = coordinator.wait(timeout=5)
    assert warm["progress"]["to_read"] == 0
    assert warm["progress"]["read"] == 0

    # Past the TTL every listed object is stale again.
    wall["now"] += rr.DEFAULT_CONTENT_TTL_SECONDS + 1
    coordinator.start()
    stale = coordinator.wait(timeout=5)
    assert stale["progress"]["to_read"] == 3

    # A Rescan ignores the TTL and targets every listed object.
    wall["now"] += 1
    coordinator.start(mode="rescan")
    rescanned = coordinator.wait(timeout=5)
    assert rescanned["progress"]["to_read"] == 3
    assert rescanned["progress"]["read"] == 3


def test_cancel_during_long_rate_limit_wait_keeps_prior_generation(tmp_path):
    clock = Clock()
    sleeper = Sleeper(clock)
    provider = _paged_provider(objects={PRIMARY: ["a"], "T2": []})
    coordinator, store, _, _ = _coordinator(
        tmp_path, provider, clock=clock, sleeper=sleeper, max_retries=None
    )

    coordinator.start()
    first = coordinator.wait(timeout=5)
    assert first["phase"] == "complete"
    prior = store.load_snapshot("all")
    assert prior is not None and prior.generation == 1

    provider.get_error = ca.CapacitiesRateLimited("busy", retry_after=120.0)
    sleeper.on_sleep = lambda seconds: coordinator.cancel()

    coordinator.start(mode="rescan")
    status = coordinator.wait(timeout=5)

    assert status["phase"] == "cancelled"
    assert status["outcome"] == "cancelled"
    snapshot = store.load_snapshot("all")
    assert snapshot is not None and snapshot.generation == 1
    assert status["progress"]["waiting"] is None


# ---------------------------------------------------------------------------
# Latency plan U1 (KTD1) — one tolerant RateLimit parser
# ---------------------------------------------------------------------------

def test_parse_rate_limit_reads_the_combined_draft_header():
    observation = rr.parse_rate_limit({"RateLimit": "limit=5, remaining=4, reset=58"})

    assert observation == rr.RateLimitObservation(
        limit=5, remaining=4, reset_seconds=58.0
    )


def test_parse_rate_limit_reads_the_structured_field_form():
    observation = rr.parse_rate_limit({"RateLimit": '"default";q=5;r=4;t=58'})

    assert observation == rr.RateLimitObservation(
        limit=5, remaining=4, reset_seconds=58.0
    )


def test_parse_rate_limit_reads_the_structured_field_form_without_a_limit():
    observation = rr.parse_rate_limit({"ratelimit": '"default";r=4;t=58'})

    assert observation == rr.RateLimitObservation(
        limit=None, remaining=4, reset_seconds=58.0
    )


def test_parse_rate_limit_reads_the_split_x_ratelimit_headers():
    observation = rr.parse_rate_limit(
        {
            "X-RateLimit-Limit": "30",
            "X-RateLimit-Remaining": "12",
            "X-RateLimit-Reset": "42",
        }
    )

    assert observation == rr.RateLimitObservation(
        limit=30, remaining=12, reset_seconds=42.0
    )


def test_parse_rate_limit_reads_the_split_ratelimit_headers():
    observation = rr.parse_rate_limit(
        {
            "ratelimit-limit": "30",
            "ratelimit-remaining": "3",
            "ratelimit-reset": "17",
        }
    )

    assert observation == rr.RateLimitObservation(
        limit=30, remaining=3, reset_seconds=17.0
    )


def test_parse_rate_limit_reads_a_zero_remaining_split_header():
    """The cooldown shape read today stays a usable observation."""
    observation = rr.parse_rate_limit(
        {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "7"}
    )

    assert observation == rr.RateLimitObservation(
        limit=None, remaining=0, reset_seconds=7.0
    )


def test_parse_rate_limit_takes_the_lowest_remaining_policy():
    observation = rr.parse_rate_limit(
        {
            "RateLimit": (
                '"default";q=30;r=7;t=58, "content";q=5;r=2;t=40'
            )
        }
    )

    assert observation == rr.RateLimitObservation(
        limit=5, remaining=2, reset_seconds=40.0
    )


@pytest.mark.parametrize(
    "headers",
    [
        pytest.param({}, id="no headers"),
        pytest.param(None, id="no mapping"),
        pytest.param({"RateLimit": "nonsense"}, id="garbage value"),
        pytest.param({"RateLimit": "limit=5, reset=58"}, id="missing remaining"),
        pytest.param(
            {"RateLimit": "limit=5, remaining=four, reset=58"},
            id="non-integer remaining",
        ),
        pytest.param(
            {"RateLimit": "limit=5, remaining=-1, reset=58"},
            id="negative remaining",
        ),
        pytest.param(
            {"RateLimit": "limit=5, remaining=4, reset=abc"},
            id="non-integer reset",
        ),
        pytest.param(
            {"RateLimit": "limit=5, remaining=4, reset=121"},
            id="reset above twice the fallback window",
        ),
        pytest.param(
            {"RateLimit": "limit=oops, remaining=4, reset=58"},
            id="garbage limit",
        ),
        pytest.param({"Retry-After": "5"}, id="retry-after only"),
        pytest.param(
            {"RateLimit": "limit=5, remaining=4"}, id="missing reset"
        ),
    ],
)
def test_parse_rate_limit_rejects_unusable_headers(headers):
    assert rr.parse_rate_limit(headers) is None


# ---------------------------------------------------------------------------
# Latency plan U1 (KTD2/KTD3) — the header budget replaces the fixed window
# ---------------------------------------------------------------------------

def _pacer(clock, sleeper=None, **overrides):
    sleeper = sleeper if sleeper is not None else Sleeper(clock)
    return rr.ProviderPacer(clock=clock, sleeper=sleeper, **overrides), sleeper


def test_observe_records_the_observed_limit_for_content():
    clock = Clock()
    pacer, _ = _pacer(clock)

    pacer.observe(rr.ENDPOINT_CONTENT, {"RateLimit": "limit=5, remaining=4, reset=58"})

    assert pacer.observed_limits[rr.ENDPOINT_CONTENT] == {
        "limit": 5,
        "window": 58.0,
    }


def test_begin_run_resets_the_rate_limited_count_and_observed_limit():
    clock = Clock()
    pacer, _ = _pacer(clock)
    pacer.observe(rr.ENDPOINT_CONTENT, {"RateLimit": "limit=5, remaining=4, reset=58"})
    pacer.rate_limited = 3

    pacer.begin_run()

    assert pacer.rate_limited == 0
    assert pacer.observed_limits == {}


def test_ae1_the_header_budget_paces_the_third_request():
    """AE1: remaining=2 lets two reads out, then waits out the reset."""
    clock = Clock()
    waits = []
    pacer, sleeper = _pacer(
        clock,
        on_wait=lambda endpoint, seconds, reason: waits.append(
            (endpoint, seconds, reason)
        ),
    )
    pacer.observe(
        rr.ENDPOINT_CONTENT, {"RateLimit": "limit=30, remaining=2, reset=40"}
    )

    pacer.before_request(rr.ENDPOINT_CONTENT)
    pacer.before_request(rr.ENDPOINT_CONTENT)

    assert sleeper.calls == []
    pacer.before_request(rr.ENDPOINT_CONTENT)

    assert sleeper.calls == [pytest.approx(40.0)]
    # A budget wait reports itself the way a reset-header cooldown does, then
    # clears once the wait is over.
    assert waits[0] == (
        rr.ENDPOINT_CONTENT,
        pytest.approx(40.0),
        rr.RATE_LIMIT_WAIT_REASON,
    )
    assert waits[-1] == (rr.ENDPOINT_CONTENT, 0.0, None)


def test_ae2_no_header_keeps_the_thirty_per_minute_window():
    """AE2: with no usable header the documented 30/60 ceiling applies."""
    clock = Clock()
    pacer, sleeper = _pacer(clock)

    for _ in range(30):
        pacer.before_request(rr.ENDPOINT_CONTENT)

    assert sleeper.calls == []
    pacer.before_request(rr.ENDPOINT_CONTENT)

    assert sleeper.calls == [pytest.approx(60.0)]


def test_an_unusable_header_keeps_the_thirty_per_minute_window():
    clock = Clock()
    pacer, sleeper = _pacer(clock)
    pacer.observe(rr.ENDPOINT_CONTENT, {"RateLimit": "limit=5, remaining=4, reset=999"})

    for _ in range(30):
        pacer.before_request(rr.ENDPOINT_CONTENT)

    assert sleeper.calls == []
    pacer.before_request(rr.ENDPOINT_CONTENT)
    assert sleeper.calls == [pytest.approx(60.0)]


def test_a_generous_header_budget_raises_throughput_above_the_fallback():
    clock = Clock()
    pacer, sleeper = _pacer(clock)
    pacer.observe(
        rr.ENDPOINT_CONTENT, {"RateLimit": "limit=120, remaining=100, reset=60"}
    )

    for _ in range(40):
        pacer.before_request(rr.ENDPOINT_CONTENT)

    assert sleeper.calls == []


def test_a_tight_header_budget_lowers_throughput_below_the_fallback():
    clock = Clock()
    pacer, sleeper = _pacer(clock)
    pacer.observe(rr.ENDPOINT_CONTENT, {"RateLimit": "limit=5, remaining=5, reset=60"})

    for _ in range(5):
        pacer.before_request(rr.ENDPOINT_CONTENT)

    assert sleeper.calls == []
    pacer.before_request(rr.ENDPOINT_CONTENT)

    assert sleeper.calls == [pytest.approx(60.0)]


def test_an_expired_budget_falls_back_to_the_fixed_window():
    clock = Clock()
    pacer, sleeper = _pacer(clock)
    pacer.observe(rr.ENDPOINT_CONTENT, {"RateLimit": "limit=5, remaining=5, reset=10"})
    # The reset passes with no further observation, so the budget is stale.
    clock.advance(11)

    for _ in range(30):
        pacer.before_request(rr.ENDPOINT_CONTENT)

    assert sleeper.calls == []
    pacer.before_request(rr.ENDPOINT_CONTENT)

    assert sleeper.calls == [pytest.approx(60.0)]


def test_a_429_still_retries_and_is_counted_once(tmp_path):
    clock = Clock()
    sleeper = Sleeper(clock)
    provider = _paged_provider(objects={PRIMARY: ["a"], "T2": []})
    attempts = {"n": 0}

    def flaky(object_id):
        if object_id == "a" and attempts["n"] < 1:
            attempts["n"] += 1
            raise ca.CapacitiesRateLimited("slow down", retry_after=1.5)

    provider.on_get = flaky
    coordinator, store, _, _ = _coordinator(
        tmp_path, provider, clock=clock, sleeper=sleeper
    )

    coordinator.start()
    status = coordinator.wait(timeout=5)

    assert status["phase"] == "complete"
    assert status["outcome"] == "published"
    assert sleeper.calls == [1.5]
    assert coordinator.pacer.rate_limited == 1
    assert store.load_snapshot("all") is not None


# ---------------------------------------------------------------------------
# Latency plan U2 (KTD4) — run metrics in the job document
# ---------------------------------------------------------------------------

METRIC_KEYS = {
    "reads",
    "read_started_at",
    "read_finished_at",
    "read_rate_per_minute",
    "elapsed_seconds",
    "cold",
    "rate_limited",
    "observed_limit",
}


def _assert_metrics_shape(metrics):
    """Every metric is a number, boolean, or null — never a string."""
    assert set(metrics) == METRIC_KEYS
    for key in (
        "reads",
        "read_started_at",
        "read_finished_at",
        "read_rate_per_minute",
        "elapsed_seconds",
        "rate_limited",
    ):
        value = metrics[key]
        assert value is None or type(value) in (int, float), key
    assert type(metrics["cold"]) is bool
    observed = metrics["observed_limit"]
    assert observed is None or (
        set(observed) == {"limit", "window"}
        and type(observed["limit"]) is int
        and type(observed["window"]) in (int, float)
    )


def test_ae3_a_cold_run_records_its_reads_timings_and_rate(tmp_path):
    """AE3: a cold run records what the 5-minute target is checked against."""
    wall = Clock(now=1000.0)
    provider = _paged_provider(objects={PRIMARY: ["a", "b"], "T2": ["c"]})
    provider.on_get = lambda object_id: wall.advance(30.0)
    coordinator, _, _, _ = _coordinator(tmp_path, provider, wall_clock=wall)

    coordinator.start()
    status = coordinator.wait(timeout=5)

    assert status["phase"] == "complete", status
    metrics = status["progress"]["metrics"]
    _assert_metrics_shape(metrics)
    assert metrics["reads"] == 3
    assert metrics["reads"] == status["progress"]["read"]
    assert metrics["cold"] is True
    assert metrics["read_started_at"] == 1030.0
    assert metrics["read_finished_at"] == 1090.0
    # Three reads over one minute of read time.
    assert metrics["read_rate_per_minute"] == pytest.approx(3.0)
    assert metrics["elapsed_seconds"] == pytest.approx(90.0)
    assert metrics["rate_limited"] == 0
    assert metrics["observed_limit"] is None
    # The block survives a read back from the durable document.
    document = json.loads(coordinator._job_path().read_text(encoding="utf-8"))
    assert document["version"] == rr.JOB_SCHEMA_VERSION
    assert document["job"]["progress"]["metrics"] == metrics


def test_a_warm_run_reports_zero_reads_and_a_null_rate(tmp_path):
    provider = _paged_provider(objects={PRIMARY: ["a", "b"], "T2": ["c"]})
    coordinator, store, _, _ = _coordinator(tmp_path, provider)
    for object_id, type_id in (("a", PRIMARY), ("b", PRIMARY), ("c", "T2")):
        store.put(object_id, type_id, _content(object_id, type_id))

    coordinator.start()
    status = coordinator.wait(timeout=5)

    assert status["phase"] == "complete", status
    metrics = status["progress"]["metrics"]
    _assert_metrics_shape(metrics)
    assert metrics["reads"] == 0
    assert metrics["cold"] is False
    assert metrics["read_started_at"] is None
    assert metrics["read_finished_at"] is None
    assert metrics["read_rate_per_minute"] is None
    assert metrics["elapsed_seconds"] >= 0


def test_a_run_with_one_recovered_429_records_the_count(tmp_path):
    clock = Clock()
    sleeper = Sleeper(clock)
    provider = _paged_provider(objects={PRIMARY: ["a", "b"], "T2": []})
    attempts = {"n": 0}

    def flaky(object_id):
        if object_id == "a" and attempts["n"] < 1:
            attempts["n"] += 1
            raise ca.CapacitiesRateLimited("slow down", retry_after=1.5)

    provider.on_get = flaky
    coordinator, _, _, _ = _coordinator(
        tmp_path, provider, clock=clock, sleeper=sleeper
    )

    coordinator.start()
    status = coordinator.wait(timeout=5)

    assert status["outcome"] == "published"
    metrics = status["progress"]["metrics"]
    _assert_metrics_shape(metrics)
    assert metrics["rate_limited"] == 1


def test_the_observed_limit_reaches_the_metrics(tmp_path):
    provider = _paged_provider(objects={PRIMARY: ["a"], "T2": []})
    coordinator, _, _, _ = _coordinator(tmp_path, provider)
    pacer = coordinator.pacer

    def observe(object_id):
        pacer.observe(
            rr.ENDPOINT_CONTENT, {"RateLimit": "limit=5, remaining=4, reset=58"}
        )

    provider.on_get = observe

    coordinator.start()
    status = coordinator.wait(timeout=5)

    metrics = status["progress"]["metrics"]
    _assert_metrics_shape(metrics)
    assert metrics["observed_limit"] == {"limit": 5, "window": 58.0}


def test_a_failed_run_still_records_elapsed_time_and_only_numbers(tmp_path):
    provider = _paged_provider(objects={PRIMARY: ["a"], "T2": []})
    provider.get_error = ca.CapacitiesContractError("secret contract text")
    coordinator, _, _, _ = _coordinator(tmp_path, provider, max_retries=0)

    coordinator.start()
    status = coordinator.wait(timeout=5)

    assert status["phase"] == "failed"
    metrics = status["progress"]["metrics"]
    _assert_metrics_shape(metrics)
    assert type(metrics["elapsed_seconds"]) is float
    assert metrics["read_rate_per_minute"] is None
    # Nothing derived from an exception reached the block.
    assert "secret" not in json.dumps(metrics)


def test_a_cancelled_run_still_records_elapsed_time(tmp_path):
    provider = _paged_provider(objects={PRIMARY: ["a", "b"], "T2": []})
    coordinator, _, _, _ = _coordinator(tmp_path, provider)

    def cancel_during_read(object_id):
        if object_id == "b":
            coordinator.cancel()

    provider.on_get = cancel_during_read

    coordinator.start()
    status = coordinator.wait(timeout=5)

    assert status["phase"] == "cancelled"
    metrics = status["progress"]["metrics"]
    _assert_metrics_shape(metrics)
    assert type(metrics["elapsed_seconds"]) is float
    assert metrics["read_started_at"] is not None


# ---------------------------------------------------------------------------
# Latency plan U6 (R6/R8/R9, KTD6/KTD7) — per-type acquisition and publish
# ---------------------------------------------------------------------------

def _snapshot_when_seen(store, object_id, *, wanted, seen):
    """Record the installed snapshot the first time ``wanted`` is read.

    The provider hook fires at the top of the read for that object, so the
    snapshot it captures is exactly what the plan showed after the previous
    type finished and before this one was read.
    """
    if object_id == wanted:
        seen.append(store.load_snapshot("all"))


def test_an_interim_generation_is_published_before_a_later_type_is_read(tmp_path):
    """R6/AE4: the plan is published as each type finishes, not only at the end."""
    provider = _paged_provider(objects={PRIMARY: ["a"], "T2": ["c", "d"]})
    coordinator, store, _, _ = _coordinator(tmp_path, provider)
    seen = []
    provider.on_get = lambda object_id: _snapshot_when_seen(
        store, object_id, wanted="c", seen=seen
    )

    coordinator.start()
    status = coordinator.wait(timeout=5)

    assert status["phase"] == "complete", status
    # Read counts order the run: one read for the primary type, two for T2. By
    # the time T2's first object is read, the primary type has been published.
    assert provider.get_calls == ["a", "c", "d"]
    assert len(seen) == 1
    interim = seen[0]
    assert interim is not None
    assert interim.partial is True
    assert interim.unchecked_types == ("T2",)
    assert {(m.object_id, m.type_key) for m in interim.members} == {("a", PRIMARY)}
    # The finished read replaces the interim whole, with no member merged in.
    final = store.load_snapshot("all")
    assert final.partial is False and final.unchecked_types == ()
    assert final.generation == interim.generation + 1
    assert {(m.object_id, m.type_key) for m in final.members} == {
        ("a", PRIMARY),
        ("c", "T2"),
        ("d", "T2"),
    }


def test_types_are_read_smallest_read_count_first(tmp_path):
    """KTD7: the cheapest type finishes (and publishes) soonest."""
    provider = _paged_provider(objects={PRIMARY: ["a", "b"], "T2": ["c"]})
    coordinator, _, _, _ = _coordinator(tmp_path, provider)

    coordinator.start()
    assert coordinator.wait(timeout=5)["phase"] == "complete"

    assert provider.get_calls == ["c", "a", "b"]


def test_the_publish_order_is_deterministic_for_equal_read_counts(tmp_path):
    """KTD7: equal read counts fall back to the type key, never to run order."""
    provider = _paged_provider(objects={PRIMARY: ["a"], "T2": ["c"]})
    coordinator, _, _, _ = _coordinator(tmp_path, provider)

    coordinator.start()
    assert coordinator.wait(timeout=5)["phase"] == "complete"
    assert provider.get_calls == ["a", "c"]

    provider.get_calls.clear()
    coordinator.start(mode="rescan")
    assert coordinator.wait(timeout=5)["phase"] == "complete"
    assert provider.get_calls == ["a", "c"]


def test_the_operator_can_override_the_publish_order(tmp_path):
    """KTD7: the order is operator-adjustable, and the rest still sort by size."""
    provider = _paged_provider(objects={PRIMARY: ["a"], "T2": ["c", "d"]})
    coordinator, _, _, _ = _coordinator(
        tmp_path, provider, type_order=("T2",)
    )

    coordinator.start()
    assert coordinator.wait(timeout=5)["phase"] == "complete"

    assert provider.get_calls == ["c", "d", "a"]


def test_an_interim_install_keeps_the_unread_types_published_members(tmp_path):
    """R8/KTD6: unchecked types keep their last published state."""
    provider = _paged_provider(objects={PRIMARY: ["a"], "T2": ["c", "d"]})
    coordinator, store, _, _ = _coordinator(tmp_path, provider)
    coordinator.start()
    assert coordinator.wait(timeout=5)["phase"] == "complete"
    baseline = store.load_snapshot("all")
    assert baseline is not None

    seen = []
    provider.on_get = lambda object_id: _snapshot_when_seen(
        store, object_id, wanted="c", seen=seen
    )
    coordinator.start(mode="rescan")
    status = coordinator.wait(timeout=5)

    assert status["phase"] == "complete", status
    interim = seen[0]
    assert interim.partial is True
    assert interim.unchecked_types == ("T2",)
    assert {m.object_id for m in interim.members_of_type("T2")} == {"c", "d"}
    # The retained type keeps its own check time: it was not checked again.
    assert interim.check_time("T2") == baseline.check_time("T2")


def test_a_warm_refresh_publishes_no_interim_generation(tmp_path):
    """R6: a type whose content was entirely reused has nothing newer to show."""
    provider = _paged_provider(objects={PRIMARY: ["a"], "T2": ["c"]})
    coordinator, store, _, _ = _coordinator(tmp_path, provider)
    for object_id, type_key in (("a", PRIMARY), ("c", "T2")):
        store.put(object_id, type_key, _content(object_id, type_key))

    coordinator.start()
    status = coordinator.wait(timeout=5)

    assert status["phase"] == "complete", status
    snapshot = store.load_snapshot("all")
    assert snapshot.generation == 1
    assert snapshot.partial is False
    assert status["progress"]["publication"]["interim_count"] == 0


def test_the_complete_install_clears_partial_and_drops_unconfirmed_members(tmp_path):
    """AE5: no member survives that the complete read did not confirm."""
    provider = _paged_provider(objects={PRIMARY: ["a"], "T2": ["c", "d"]})
    coordinator, store, _, _ = _coordinator(tmp_path, provider)
    coordinator.start()
    assert coordinator.wait(timeout=5)["phase"] == "complete"

    # The second run lists one fewer T2 object than the interim retained.
    narrowed = _paged_provider(
        objects={PRIMARY: ["a"], "T2": ["c", "d"]},
        pages={
            ("T2", None): {
                "objects": [_row("c", "T2")],
                "next_cursor": None,
            }
        },
    )
    second, _, _, _ = _coordinator(tmp_path, narrowed)
    seen = []
    narrowed.on_get = lambda object_id: _snapshot_when_seen(
        store, object_id, wanted="c", seen=seen
    )

    second.start(mode="rescan")
    status = second.wait(timeout=5)

    assert status["phase"] == "complete", status
    interim = seen[0]
    assert interim.partial is True
    assert {m.object_id for m in interim.members_of_type("T2")} == {"c", "d"}
    final = store.load_snapshot("all")
    assert final.partial is False and final.unchecked_types == ()
    assert {(m.object_id, m.type_key) for m in final.members} == {
        ("a", PRIMARY),
        ("c", "T2"),
    }
    assert status["progress"]["publication"]["partial"] is False
    assert status["progress"]["unchecked_types"] == []


def test_the_status_snapshot_reports_partial_and_unchecked_types(tmp_path):
    """R7/R9: the status snapshot says which types are still unchecked.

    The snapshot summary is what the Settings panel reads, so the interim
    generation must be visible there as ``partial`` with the unchecked type
    keys named, and the complete install must clear both.
    """
    provider = _paged_provider(objects={PRIMARY: ["a"], "T2": ["c", "d"]})
    coordinator, _, _, _ = _coordinator(tmp_path, provider)
    during = []

    def observe(object_id):
        if object_id == "c":
            during.append(coordinator.status())

    provider.on_get = observe

    coordinator.start()
    status = coordinator.wait(timeout=5)

    assert status["phase"] == "complete", status
    assert len(during) == 1
    interim = during[0]["snapshot"]
    assert interim["present"] is True
    assert interim["partial"] is True
    assert interim["unchecked_types"] == ["T2"]
    # No unchecked type is claimed as checked, and none is invented.
    assert interim["type_check_times"] == {PRIMARY: interim["type_check_times"][PRIMARY]}
    final = status["snapshot"]
    assert final["partial"] is False
    assert final["unchecked_types"] == []
    # A missing snapshot still answers both keys, so no reader has to guess.
    absent = rr._snapshot_summary(None)
    assert absent["partial"] is False
    assert absent["unchecked_types"] == []


def test_cancellation_after_an_interim_leaves_it_partial_with_a_warning(tmp_path):
    """R8: a run that stops after an interim leaves it in place, marked."""
    provider = _paged_provider(objects={PRIMARY: ["a"], "T2": ["c", "d"]})
    coordinator, store, _, _ = _coordinator(tmp_path, provider)

    def cancel_on_the_later_type(object_id):
        if object_id == "c":
            coordinator.cancel()

    provider.on_get = cancel_on_the_later_type

    coordinator.start()
    status = coordinator.wait(timeout=5)

    assert status["phase"] == "cancelled"
    assert status["outcome"] == "cancelled"
    snapshot = store.load_snapshot("all")
    assert snapshot is not None
    assert snapshot.partial is True
    assert snapshot.unchecked_types == ("T2",)
    assert {m.object_id for m in snapshot.members} == {"a"}
    assert status["progress"]["publication"]["partial"] is True
    assert status["progress"]["publication"]["unchecked_types"] == ["T2"]
    assert rr.INTERIM_RETAINED_WARNING in status["warnings"]
    assert rr.CANCEL_WARNING in status["warnings"]


def test_staleness_after_an_interim_leaves_the_interim_in_place(tmp_path):
    """R8: a configuration change after an interim keeps the interim truthful."""
    provider = _paged_provider(objects={PRIMARY: ["a"], "T2": ["c", "d"]})
    values = iter([1, 1, 2])
    coordinator, store, _, _ = _coordinator(
        tmp_path, provider, revision=lambda: next(values, 2)
    )

    coordinator.start()
    status = coordinator.wait(timeout=5)

    assert status["phase"] == "failed", status
    assert status["outcome"] == "staleConfiguration", status
    snapshot = store.load_snapshot("all")
    assert snapshot is not None
    assert snapshot.partial is True
    assert snapshot.unchecked_types == ("T2",)
    assert rr.STALE_WARNING in status["warnings"]
    assert rr.INTERIM_RETAINED_WARNING in status["warnings"]


def test_a_failure_after_an_interim_leaves_the_interim_in_place(tmp_path):
    """R8: a failed required read still leaves the interim, never a complete one."""
    provider = _paged_provider(objects={PRIMARY: ["a"], "T2": ["c", "d"]})
    provider.fail_gets["d"] = FileNotFoundError("404")
    coordinator, store, _, _ = _coordinator(tmp_path, provider)

    coordinator.start()
    status = coordinator.wait(timeout=5)

    assert status["phase"] == "failed"
    assert status["outcome"] == "noCapacities"
    snapshot = store.load_snapshot("all")
    assert snapshot is not None
    assert snapshot.partial is True
    assert snapshot.unchecked_types == ("T2",)
    assert {m.object_id for m in snapshot.members} == {"a"}
    assert rr.INTERIM_RETAINED_WARNING in status["warnings"]


def test_single_flight_holds_across_the_interim_publication(tmp_path):
    """R3: the whole run, interim install included, is one single-flight job."""
    provider = _paged_provider(objects={PRIMARY: ["a"], "T2": ["c", "d"]})
    coordinator, store, _, _ = _coordinator(tmp_path, provider)
    other, _, _, _ = _coordinator(tmp_path, provider)
    attempts = []

    def contend(object_id):
        if object_id != "c":
            return
        assert store.load_snapshot("all").partial is True
        for candidate in (coordinator, other):
            try:
                candidate.start()
            except rr.RefreshBusyError:
                attempts.append("busy")

    provider.on_get = contend

    coordinator.start()
    status = coordinator.wait(timeout=5)

    assert status["phase"] == "complete", status
    assert attempts == ["busy", "busy"]


def test_progress_names_the_unchecked_types_while_the_read_continues(tmp_path):
    """R7/R9: the job names the types still unchecked, and stays truthful."""
    provider = _paged_provider(objects={PRIMARY: ["a"], "T2": ["c", "d"]})
    coordinator, store, _, _ = _coordinator(tmp_path, provider)
    during = []

    def observe(object_id):
        if object_id == "c":
            during.append(coordinator.status())

    provider.on_get = observe

    coordinator.start()
    status = coordinator.wait(timeout=5)

    assert status["phase"] == "complete", status
    assert len(during) == 1
    published = during[0]["progress"]["publication"]
    assert published["partial"] is True
    assert published["interim_count"] == 1
    assert published["unchecked_types"] == ["T2"]
    assert published["completed_types"] == [PRIMARY]
    assert during[0]["progress"]["unchecked_types"] == ["T2"]
    # The other progress fields stay truthful during and after the run.
    assert during[0]["progress"]["read"] == 1
    assert during[0]["progress"]["to_read"] == 3
    final = status["progress"]["publication"]
    assert final["partial"] is False
    assert final["interim_count"] == 1
    assert status["progress"]["unchecked_types"] == []
    # Only type keys, counts, times, and booleans: no content, no diagnostics.
    assert "secret" not in json.dumps(status["job"])


def _parity_evidence(tmp_path, *, header=None):
    """One cold run's evidence for the R5 parity guard.

    Admission is a pure function of the installed members and their cached
    content, so a run is compared by three things: which members the published
    generation holds, which rows that generation admits for the day, and the
    exact order content was read in.
    """
    provider = _paged_provider(objects={PRIMARY: ["a", "b"], "T2": ["c", "d"]})
    coordinator, store, _, sleeper = _coordinator(tmp_path, provider)
    # Pin the logical day so the admitted-row comparison cannot depend on the
    # calendar day the test happens to run on.
    coordinator.logical_day = date(2026, 11, 5)
    if header is not None:
        pacer = coordinator.pacer

        def observe(object_id):
            pacer.observe(rr.ENDPOINT_CONTENT, header)

        provider.on_get = observe

    coordinator.start()
    status = coordinator.wait(timeout=5)
    assert status["outcome"] == "published", status

    snapshot = store.load_snapshot("all")
    assert snapshot is not None
    merged = []
    for member in snapshot.members:
        cached = store.get(member.object_id, member.type_key)
        assert cached is not None
        merged.append({**_row(member.object_id, member.type_key), **cached.content})
    admitted = coordinator.adapter.items_for_day_from_objects(
        coordinator.logical_day, merged
    )
    return {
        "members": sorted(member.object_id for member in snapshot.members),
        "items": sorted(
            str(item["id"] if isinstance(item, dict) else item.id)
            for item in admitted.items
        ),
        "reads": list(provider.get_calls),
        "partial": snapshot.partial,
        "unchecked": tuple(snapshot.unchecked_types),
        "waits": len(sleeper.calls),
    }


def test_the_admitted_rows_and_read_order_survive_any_pacing_budget(tmp_path):
    """R5: pacing and per-type publishing cannot change what gets planned.

    The same cold data runs under the conservative fallback window, a generous
    observed budget, and a tight one that forces waits. Every condition must
    publish the same members and admit the same rows, in the same read order —
    and each of those runs ends on a complete generation, not an interim one.
    """
    fallback = _parity_evidence(tmp_path / "fallback")
    generous = _parity_evidence(
        tmp_path / "generous", header={"RateLimit": "limit=120, remaining=100, reset=60"}
    )
    tight = _parity_evidence(
        tmp_path / "tight", header={"RateLimit": "limit=1, remaining=0, reset=60"}
    )

    for name, run in (("fallback", fallback), ("generous", generous), ("tight", tight)):
        assert run["partial"] is False, name
        assert run["unchecked"] == (), name
        assert run["members"] == fallback["members"], name
        assert run["items"] == fallback["items"], name
        assert run["reads"] == fallback["reads"], name

    # The three conditions must genuinely differ in pacing, or the comparison
    # above proves nothing about pacing at all.
    assert generous["waits"] == 0, generous
    assert tight["waits"] > 0, tight

    # A guard that compared two empty results would prove nothing.
    assert fallback["items"] == ["A", "B", "C", "D"], fallback
    assert fallback["reads"] == ["a", "b", "c", "d"], fallback
