"""Focused durability/publication tests for the direct-refresh state store.

Everything here is local and fake: ``tmp_path``, an injected epoch clock, and
no provider, credential, real cache, or ``$HOME`` read. The store under test is
``capacities_refresh_state`` (U1); the legacy ``_ContentCache`` tests live in
``test_capacities_builder.py`` and keep their own meaning.
"""
from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import capacities_builder as cb  # noqa: E402  (legacy TTL pin only)
import capacities_refresh_state as rs  # noqa: E402


SPACE = "space-1"
ORIGIN = "https://api.capacities.io"
SCOPE = "all"


class _Clock:
    """A deterministic wall-clock stand-in (seconds, UTC epoch)."""

    def __init__(self, now: float = 1_000_000.0) -> None:
        self.now = float(now)

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += float(seconds)


def _store(tmp_path, *, root=None, space_id=SPACE, clock=None, **kwargs):
    return rs.RefreshStateStore(
        root if root is not None else tmp_path / "refresh-state",
        origin=ORIGIN,
        space_id=space_id,
        vault_root=tmp_path,
        epoch_clock=clock if clock is not None else _Clock(),
        **kwargs,
    )


def _listing(type_key, object_ids, checked_at, *, complete=True):
    return rs.TypeListing(
        type_key=type_key,
        object_ids=tuple(object_ids),
        listing_checked_at=checked_at,
        complete=complete,
    )


def _install(store, *, revision=1, expected=0, types=("T",), listings=(),
             retained=(), unreadable=(), scope=SCOPE):
    return store.install_generation(
        rs.SnapshotEvidence(
            scope_key=scope,
            revision=revision,
            required_types=tuple(types),
            listings=tuple(listings),
            retained_types=tuple(retained),
            unreadable_objects=tuple(unreadable),
        ),
        expected_generation=expected,
    )


def _pointer(root: Path) -> Path:
    pointers = sorted((root / rs.SNAPSHOTS_DIRNAME).glob("*.json"))
    assert len(pointers) == 1, pointers
    return pointers[0]


# ---------------------------------------------------------------------------
# AE1 — cached properties do not expire on the legacy TTL
# ---------------------------------------------------------------------------

def test_cached_properties_survive_the_old_ttl_with_their_original_read_time(tmp_path):
    clock = _Clock()
    store = _store(tmp_path, clock=clock)
    store.put("o1", "T", {"Status": "Active"}, content_read_at=clock.now)

    clock.advance(cb.CONTENT_CACHE_TTL_SECONDS * 10)

    cached = store.get("o1", "T")
    assert cached is not None
    assert cached.content == {"Status": "Active"}
    assert cached.content_read_at == 1_000_000.0
    assert cached.listing_checked_at is None
    # A read never renews the recorded content-read time.
    assert store.get("o1", "T").content_read_at == 1_000_000.0


def test_object_identity_is_namespaced_by_provider_space_type_and_object(tmp_path):
    digest = rs.object_namespace(ORIGIN, SPACE, "T", "o1")
    assert digest == rs.object_namespace(ORIGIN, SPACE, "T", "o1")
    for other in (
        rs.object_namespace("https://api.example.com", SPACE, "T", "o1"),
        rs.object_namespace(ORIGIN, "space-2", "T", "o1"),
        rs.object_namespace(ORIGIN, SPACE, "T2", "o1"),
        rs.object_namespace(ORIGIN, SPACE, "T", "o2"),
    ):
        assert digest != other


# ---------------------------------------------------------------------------
# AE6 — removal only at a complete-generation installation
# ---------------------------------------------------------------------------

def test_incomplete_listing_never_removes_a_published_member(tmp_path):
    store = _store(tmp_path)
    store.put("o1", "T", {"a": 1}, content_read_at=1.0)
    store.put("o2", "T", {"a": 2}, content_read_at=2.0)
    first = _install(store, listings=[_listing("T", ["o1", "o2"], 10.0)])
    assert first.generation == 1
    assert first.has("o2", "T")

    with pytest.raises(rs.RefreshStateIncompleteError):
        _install(
            store,
            revision=2,
            expected=1,
            listings=[_listing("T", ["o1"], 11.0, complete=False)],
        )

    kept = store.load_snapshot(SCOPE)
    assert kept.generation == 1
    assert kept.has("o2", "T")


def test_complete_absence_removes_membership_only_at_installation(tmp_path):
    store = _store(tmp_path)
    store.put("o1", "T", {"a": 1}, content_read_at=1.0)
    store.put("o2", "T", {"a": 2}, content_read_at=2.0)
    _install(store, listings=[_listing("T", ["o1", "o2"], 10.0)])

    # A complete listing without o2 publishes the confirmed removal.
    second = _install(
        store, revision=2, expected=1, listings=[_listing("T", ["o1"], 12.0)]
    )
    assert second.generation == 2
    assert second.has("o1", "T")
    assert not second.has("o2", "T")
    # Removal is membership-only: the retained read is not destroyed here.
    assert store.get("o2", "T").content == {"a": 2}


def test_a_listed_member_without_a_cached_read_cannot_install(tmp_path):
    store = _store(tmp_path)
    store.put("o1", "T", {"a": 1}, content_read_at=1.0)

    with pytest.raises(rs.RefreshStateIncompleteError):
        _install(store, listings=[_listing("T", ["o1", "never-read"], 10.0)])

    assert store.load_snapshot(SCOPE) is None


# ---------------------------------------------------------------------------
# Corrupt schema / foreign namespace / storage bound preserve the prior result
# ---------------------------------------------------------------------------

def test_unusable_pointer_blocks_installation_and_preserves_its_bytes(tmp_path):
    store = _store(tmp_path)
    store.put("o1", "T", {"a": 1}, content_read_at=1.0)
    _install(store, listings=[_listing("T", ["o1"], 10.0)])
    pointer = _pointer(tmp_path / "refresh-state")
    pointer.write_text("{not json", encoding="utf-8")

    with pytest.raises(rs.RefreshStateFormatError):
        _install(
            store, revision=2, expected=1, listings=[_listing("T", ["o1"], 11.0)]
        )

    assert pointer.read_bytes() == b"{not json"
    assert any("pointer" in warning.lower() for warning in store.drain_warnings())
    # The unusable bytes are only ever dropped by an explicit repair call.
    assert store.clear_unusable_snapshot(SCOPE) is True
    assert not pointer.exists()


def test_a_pointer_with_a_foreign_namespace_is_never_served_or_overwritten(
    tmp_path,
):
    root = tmp_path / "refresh-state"
    store = _store(tmp_path)
    store.put("o1", "T", {"a": 1}, content_read_at=1.0)
    _install(store, listings=[_listing("T", ["o1"], 10.0)])
    pointer = _pointer(root)
    document = json.loads(pointer.read_text(encoding="utf-8"))
    document["namespace"] = "some-other-vault-or-provider"
    pointer.write_text(json.dumps(document), encoding="utf-8")
    foreign_bytes = pointer.read_bytes()

    assert store.load_snapshot(SCOPE) is None
    assert any("different vault" in warning.lower() for warning in store.drain_warnings())
    with pytest.raises(rs.RefreshStateFormatError):
        _install(store, revision=2, expected=1, listings=[_listing("T", ["o1"], 11.0)])
    assert pointer.read_bytes() == foreign_bytes

    # A different space resolves a different pointer path, so it never even
    # looks at (or warns about) another scope's bytes.
    other = rs.RefreshStateStore(
        root, origin=ORIGIN, space_id="space-2", vault_root=tmp_path
    )
    assert other.load_snapshot(SCOPE) is None
    assert other.drain_warnings() == []


def test_storage_bound_refuses_new_objects_and_preserves_the_previous_generation(
    tmp_path,
):
    store = _store(tmp_path, max_objects=1)
    store.put("o1", "T", {"a": 1}, content_read_at=1.0)
    snapshot = _install(store, listings=[_listing("T", ["o1"], 10.0)])

    with pytest.raises(rs.RefreshStateStorageError):
        store.put("o2", "T", {"a": 2}, content_read_at=2.0)

    # No silent eviction: the published member and its read survive.
    assert store.object_count() == 1
    assert store.get("o1", "T").content == {"a": 1}
    assert store.load_snapshot(SCOPE).generation == snapshot.generation
    assert any("limit" in warning.lower() for warning in store.drain_warnings())


def test_storage_byte_bound_refuses_an_oversized_object(tmp_path):
    store = _store(tmp_path, max_bytes=256)

    with pytest.raises(rs.RefreshStateStorageError):
        store.put("o1", "T", {"blob": "x" * 4000}, content_read_at=1.0)

    assert store.object_count() == 0


# ---------------------------------------------------------------------------
# AE22 — interruption keeps the snapshot and every successful read
# ---------------------------------------------------------------------------

def test_interrupted_run_keeps_the_snapshot_and_retained_reads(tmp_path):
    clock = _Clock()
    root = tmp_path / "refresh-state"
    first = _store(tmp_path, root=root, clock=clock)
    first.put("o1", "T", {"a": 1}, content_read_at=1.0)
    _install(first, listings=[_listing("T", ["o1"], 10.0)])
    first.note_run_started(SCOPE, revision=2, started_at=clock.now)
    first.put("o2", "T", {"a": 2}, content_read_at=2.0)

    # The process dies here; a fresh store re-reads the same durable bytes.
    restarted = _store(tmp_path, root=root, clock=clock)

    published = restarted.load_snapshot(SCOPE)
    assert published.generation == 1
    assert published.has("o1", "T")
    assert not published.has("o2", "T")
    assert restarted.get("o2", "T").content_read_at == 2.0

    marker = restarted.read_unfinished_run(SCOPE)
    assert marker is not None
    assert (marker.scope_key, marker.revision) == (SCOPE, 2)
    assert restarted.unfinished_runs() == (marker,)
    restarted.note_run_finished(SCOPE)
    assert restarted.read_unfinished_run(SCOPE) is None
    assert restarted.unfinished_runs() == ()


def test_failed_reads_prevent_a_partial_pointer_without_losing_reads(tmp_path):
    store = _store(tmp_path)
    store.put("o1", "T", {"a": 1}, content_read_at=1.0)
    _install(store, listings=[_listing("T", ["o1"], 10.0)])

    store.put("o3", "T", {"a": 3}, content_read_at=3.0)
    with pytest.raises(rs.RefreshStateIncompleteError):
        _install(
            store,
            revision=2,
            expected=1,
            listings=[_listing("T", ["o1", "o3"], 11.0)],
            unreadable=("o3",),
        )

    assert store.load_snapshot(SCOPE).generation == 1
    assert store.get("o3", "T").content == {"a": 3}


# ---------------------------------------------------------------------------
# Guarded generation installation
# ---------------------------------------------------------------------------

def test_a_stale_configuration_revision_cannot_overwrite_a_newer_pointer(
    tmp_path,
):
    store = _store(tmp_path)
    store.put("o1", "T", {"a": 1}, content_read_at=1.0)
    _install(store, revision=5, listings=[_listing("T", ["o1"], 10.0)])

    with pytest.raises(rs.SnapshotConflictError):
        _install(
            store, revision=4, expected=1, listings=[_listing("T", ["o1"], 11.0)]
        )
    with pytest.raises(rs.SnapshotConflictError):
        _install(
            store, revision=6, expected=0, listings=[_listing("T", ["o1"], 11.0)]
        )

    final = store.load_snapshot(SCOPE)
    assert (final.generation, final.revision) == (1, 5)


def test_racing_installations_leave_one_complete_generation(tmp_path):
    root = tmp_path / "refresh-state"
    first = _store(tmp_path, root=root)
    second = _store(tmp_path, root=root)
    for store in (first, second):
        store.put("o1", "T", {"a": 1}, content_read_at=1.0)

    barrier = threading.Barrier(2)
    results: list[object] = []
    lock = threading.Lock()

    def race(store):
        barrier.wait()
        try:
            outcome = _install(store, listings=[_listing("T", ["o1"], 10.0)])
        except rs.SnapshotConflictError as exc:
            outcome = exc
        with lock:
            results.append(outcome)

    threads = [threading.Thread(target=race, args=(store,)) for store in (first, second)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sum(isinstance(item, rs.CompleteSnapshot) for item in results) == 1
    assert sum(isinstance(item, rs.SnapshotConflictError) for item in results) == 1

    pointer = _pointer(root)
    document = json.loads(pointer.read_text(encoding="utf-8"))
    assert document["generation"] == 1
    assert len(document["members"]) == 1


def test_rescan_combines_with_retained_prior_types(tmp_path):
    store = _store(tmp_path)
    store.put("a", "T1", {"v": "a"}, content_read_at=1.0)
    store.put("b", "T2", {"v": "b"}, content_read_at=1.0)
    _install(
        store,
        types=("T1", "T2"),
        listings=[_listing("T1", ["a"], 10.0), _listing("T2", ["b"], 11.0)],
    )

    store.put("a", "T1", {"v": "a2"}, content_read_at=20.0)
    second = _install(
        store,
        revision=1,
        expected=1,
        types=("T1",),
        listings=[_listing("T1", ["a"], 21.0)],
        retained=("T2",),
    )
    assert second.has("a", "T1")
    assert second.has("b", "T2")
    assert second.check_time("T2") == 11.0
    assert store.get("a", "T1").content == {"v": "a2"}

    # Dropping a prior type without retaining it is refused, never silent.
    with pytest.raises(rs.RefreshStateIncompleteError):
        _install(
            store,
            revision=1,
            expected=2,
            types=("T1",),
            listings=[_listing("T1", ["a"], 22.0)],
        )


# ---------------------------------------------------------------------------
# Optional, read-only legacy cache import
# ---------------------------------------------------------------------------

def test_legacy_import_preserves_timestamps_and_leaves_the_source_untouched(
    tmp_path,
):
    store = _store(tmp_path)
    legacy = tmp_path / "content-cache.json"
    legacy.write_text(
        json.dumps(
            {
                "version": 1,
                "namespace": "legacy-ns",
                "entries": [
                    {"object_id": "o1", "fetched_at": 1000.0, "content": {"a": 1}},
                    {"object_id": "oX", "fetched_at": 1000.0, "content": {"a": 9}},
                    {"object_id": "o2", "fetched_at": "nope", "content": {}},
                ],
            }
        ),
        encoding="utf-8",
    )
    before = legacy.read_bytes()

    result = rs.import_legacy_entries(
        store, legacy, expected_namespace="legacy-ns", object_types={"o1": "T"}
    )

    assert (result.imported, result.skipped_unknown, result.skipped_invalid) == (
        1,
        1,
        1,
    )
    assert legacy.read_bytes() == before
    cached = store.get("o1", "T")
    assert cached.content == {"a": 1}
    assert cached.content_read_at == 1000.0
    assert cached.listing_checked_at is None


def test_legacy_import_refuses_a_foreign_document_and_never_copies_its_path(
    tmp_path,
):
    store = _store(tmp_path)
    legacy = tmp_path / "legacy.json"
    legacy.write_text(
        json.dumps(
            {
                "version": 1,
                "namespace": "other-scope",
                "entries": [{"object_id": "o1", "fetched_at": 1.0, "content": {}}],
            }
        ),
        encoding="utf-8",
    )

    result = rs.import_legacy_entries(
        store, legacy, expected_namespace="legacy-ns", object_types={"o1": "T"}
    )

    assert result.imported == 0
    assert result.warnings
    joined = " ".join(result.warnings)
    assert "other-scope" not in joined
    assert str(legacy) not in joined
    assert store.get("o1", "T") is None

    absent = rs.import_legacy_entries(
        store, tmp_path / "absent.json", expected_namespace="legacy-ns", object_types={}
    )
    assert absent == rs.LegacyImport()
