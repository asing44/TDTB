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
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import capacities_adapter as ca  # noqa: E402
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
):
    clock = clock or Clock()
    sleeper = sleeper if sleeper is not None else Sleeper(clock)
    store = _store(tmp_path)
    frozen = (
        {}
        if configuration_revision is None
        else {"configuration_revision": configuration_revision}
    )
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
    assert contract.generation == store.load_snapshot("all").generation == 1
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
    assert contract.generation == 2
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
    assert kept.generation == 1
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
    assert kept.generation == 1
    assert kept.structures == previous.structures


# ---------------------------------------------------------------------------
# Persisted-settings compatibility — legacy files and Assigned On declarations
# ---------------------------------------------------------------------------

def _legacy_settings_doc(**overrides) -> dict:
    """A version-1 save written BEFORE ``assigned_on_structures`` existed."""
    doc = {
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
    doc.update(overrides)
    return doc


def _write_settings(doc: dict) -> None:
    path = cs.settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc), encoding="utf-8")


def _settings_refresh(tmp_path, provider, *, exclusion_policy=None):
    """Run one full refresh through the settings-resolved mappings."""
    settings = cs.read_settings().settings
    mappings = cb._resolve_assigned_structures(
        tuple(_mapping(t) for t in TYPES),
        settings.assigned_structures,
        settings.assigned_on_structures,
    )
    coordinator, store, _, _ = _coordinator(
        tmp_path,
        provider,
        mappings=mappings,
        assignment_settings=settings.to_assignment_settings(),
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
    """A pre-Assigned-On settings file is valid on read and cannot fail a run."""
    _write_settings(_legacy_settings_doc())
    provider = _paged_provider(objects={PRIMARY: ["a"], "T2": ["c"]})

    status = _settings_refresh(tmp_path, provider)

    assert status["phase"] == "complete", status
    assert status["outcome"] == "published"
    assert cs.read_settings().settings.assigned_on_structures == {}
    assert cs.read_settings().settings.assigned_structures == {}


def test_assigned_on_mapped_to_date_property_completes(tmp_path):
    """A saved Assigned On declaration resolves and never gates the run."""
    _write_settings(
        _legacy_settings_doc(
            assigned_on_structures={PRIMARY: "assigned_on"},
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
        _legacy_settings_doc(
            assigned_on_structures={PRIMARY: property_id},
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
        _legacy_settings_doc(
            assigned_on_structures={PRIMARY: "assigned_on", "T2": "assigned_on"},
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
        "longer defined; ignored because Assigned On is mapped" in warnings
    )
    assert (
        "Legacy assignment property 'assigned' for structure 'T2' is no "
        "longer defined; ignored because Assigned On is mapped" in warnings
    )


def test_vanished_legacy_assignment_property_fails_with_detail_without_assigned_on(
    tmp_path,
):
    """Without Assigned On the same state stays a strict contract failure, and
    the diagnostic names the vanished property id."""
    _write_settings(_legacy_settings_doc())
    provider = _paged_provider(
        objects={PRIMARY: ["a"], "T2": ["c"]},
        structures=_structures_without_legacy_assignment(*TYPES),
    )

    status = _settings_refresh(tmp_path, provider)

    assert status["phase"] == "failed", status
    assert (
        "Cause: CapacitiesContractError "
        "(structure=RootTask property=assigned)" in status["warnings"]
    )
    assert "Where: list" in status["warnings"]


def test_assigned_on_only_mapping_without_status_or_assignment_property_completes(
    tmp_path,
):
    """An Assigned On-mapped structure needs no legacy marker and no status
    property: the declaration alone satisfies the contract."""
    _write_settings(
        _legacy_settings_doc(
            assigned_on_structures={"custom-project": "assigned_on"},
        )
    )
    settings = cs.read_settings().settings
    mappings = cb._resolve_assigned_structures(
        (
            _mapping(PRIMARY),
            ca.StructureMapping(structure_id="custom-project", title_property="title"),
        ),
        settings.assigned_structures,
        settings.assigned_on_structures,
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
        assignment_settings=settings.to_assignment_settings(),
    )
    coordinator.start()
    status = coordinator.wait(timeout=5)

    assert status["phase"] == "complete", status
    assert provider.list_calls == [(PRIMARY, None)]


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
