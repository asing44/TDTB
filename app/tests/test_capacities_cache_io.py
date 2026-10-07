"""Tests for the cache IO primitives shared by the builder and caches.

The primitives used to live inside ``capacities_builder``; they now live in
``capacities_cache_io`` so cache modules the builder imports can reuse them
without an import cycle. These tests pin the extraction: the namespace
algorithm is unchanged and the builder keeps thin delegating wrappers.
"""
from __future__ import annotations

import hashlib
import json
import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import capacities_builder as cb  # noqa: E402
import capacities_cache_io as cache_io  # noqa: E402


def test_namespace_digest_matches_the_pinned_algorithm(tmp_path):
    vault = tmp_path / "vault-a"
    base_url = "https://api.capacities.io"
    material = "\x1f".join((str(vault.resolve()), "space-1", base_url))
    expected = hashlib.sha256(material.encode("utf-8")).hexdigest()

    assert cache_io.cache_namespace(vault, "space-1", base_url) == expected
    # The provider URL is normalized exactly as before: trailing slashes are
    # stripped, so a trailing slash is the same namespace.
    assert cache_io.cache_namespace(vault, "space-1", base_url + "/") == expected


def test_namespace_separates_vault_space_and_base_url(tmp_path):
    base_url = "https://api.capacities.io"
    base = cache_io.cache_namespace(tmp_path, "space-1", base_url)

    assert base != cache_io.cache_namespace(tmp_path / "elsewhere", "space-1", base_url)
    assert base != cache_io.cache_namespace(tmp_path, "space-2", base_url)
    assert base != cache_io.cache_namespace(tmp_path, "space-1", "https://other.example")


def test_builder_wrappers_delegate_to_the_shared_primitives(tmp_path, monkeypatch):
    calls = []
    sentinel = object()

    def _record(name):
        def fake(*args, **kwargs):
            calls.append((name, args))
            return sentinel

        return fake

    monkeypatch.setattr(cache_io, "cache_namespace", _record("cache_namespace"))
    monkeypatch.setattr(cache_io, "store_lock", _record("store_lock"))
    monkeypatch.setattr(cache_io, "acquire_path_lock", _record("acquire_path_lock"))
    monkeypatch.setattr(cache_io, "release_lock_file", _record("release_lock_file"))
    monkeypatch.setattr(cache_io, "atomic_write_json", _record("atomic_write_json"))

    assert cb._content_cache_namespace(tmp_path, "space-1", "https://api") is sentinel
    assert cb._store_lock(tmp_path) is sentinel
    assert cb._acquire_path_lock(tmp_path / "x.lock") is sentinel
    assert cb._acquire_lock_file(tmp_path) is sentinel
    assert cb._release_lock_file(sentinel) is None
    assert cb._atomic_write_json(tmp_path / "x.json", {"a": 1}) is None

    assert [name for name, _ in calls] == [
        "cache_namespace",
        "store_lock",
        "acquire_path_lock",
        "acquire_path_lock",  # _acquire_lock_file delegates through the path form
        "release_lock_file",
        "atomic_write_json",
    ]


def test_shared_atomic_write_replaces_bytes_and_is_owner_only(tmp_path):
    path = tmp_path / "doc.json"

    cache_io.atomic_write_json(path, {"a": 1})
    assert json.loads(path.read_text(encoding="utf-8")) == {"a": 1}
    assert stat.S_IMODE(path.stat().st_mode) == 0o600

    cache_io.atomic_write_json(path, {"a": 2, "b": [1, 2]})
    assert json.loads(path.read_text(encoding="utf-8")) == {"a": 2, "b": [1, 2]}
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert list(tmp_path.glob(".*.tmp")) == []


def test_a_failed_shared_atomic_write_preserves_the_previous_bytes(
    tmp_path, monkeypatch
):
    path = tmp_path / "doc.json"
    cache_io.atomic_write_json(path, {"a": 1})
    before = path.read_bytes()

    def _boom(*_args, **_kwargs):
        raise OSError("replace failed")

    monkeypatch.setattr(cache_io.os, "replace", _boom)
    try:
        cache_io.atomic_write_json(path, {"a": 2})
    except OSError:
        pass
    else:  # pragma: no cover — the primitive must surface the failure
        raise AssertionError("expected the atomic write to raise")

    assert path.read_bytes() == before
    assert list(tmp_path.glob(".*.tmp")) == []
