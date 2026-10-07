"""Contract tests for the machine-local Capacities structure title cache.

The vault-local mapping record stores only structure IDs, so the settings
drawer shows raw UUIDs. The title cache is disposable display metadata that
must never couple to the operator-owned mapping: it lives outside the vault,
it never raises, and a broken file must degrade to "no titles" rather than
block a read or a settings write.
"""
from __future__ import annotations

import json
import os
import stat
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import capacities_builder as cb  # noqa: E402
import capacities_cache_io as cache_io  # noqa: E402
import capacities_structure_titles as titles  # noqa: E402

BASE_URL = "https://api.capacities.io"


def _document(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _stored_titles(path: Path, vault, space, base=BASE_URL) -> dict:
    namespace = cache_io.cache_namespace(vault, space, base)
    return _document(path)["namespaces"][namespace]["titles"]


def test_round_trip_and_namespace_isolation(tmp_path):
    path = tmp_path / "titles.json"
    vault_a, vault_b = tmp_path / "vault-a", tmp_path / "vault-b"

    assert (
        titles.remember_titles(vault_a, "space-1", BASE_URL, {"s1": "One"}, path=path)
        is True
    )
    assert titles.read_titles(vault_a, "space-1", BASE_URL, path=path) == {"s1": "One"}
    # A trailing slash is the same provider scope, not a new namespace.
    assert titles.read_titles(vault_a, "space-1", BASE_URL + "/", path=path) == {
        "s1": "One"
    }
    # Another vault, another space, or another provider URL sees nothing.
    assert titles.read_titles(vault_b, "space-1", BASE_URL, path=path) == {}
    assert titles.read_titles(vault_a, "space-2", BASE_URL, path=path) == {}
    assert titles.read_titles(vault_a, "space-1", "https://other.example", path=path) == {}


def test_rewriting_one_namespace_keeps_the_document_byte_identical(tmp_path):
    path = tmp_path / "titles.json"
    vault_a, vault_b = tmp_path / "vault-a", tmp_path / "vault-b"

    assert titles.remember_titles(vault_a, "space-1", BASE_URL, {"s1": "One"}, path=path)
    assert titles.remember_titles(vault_b, "space-1", BASE_URL, {"s2": "Two"}, path=path)
    before = path.read_bytes()

    assert (
        titles.remember_titles(vault_a, "space-1", BASE_URL, {"s1": "One"}, path=path)
        is True
    )

    # Byte-for-byte: rewriting one namespace moved nothing else in the file.
    assert path.read_bytes() == before
    assert titles.read_titles(vault_b, "space-1", BASE_URL, path=path) == {"s2": "Two"}
    assert set(_document(path)["namespaces"]) == {
        cache_io.cache_namespace(vault_a, "space-1", BASE_URL),
        cache_io.cache_namespace(vault_b, "space-1", BASE_URL),
    }


def test_failing_atomic_write_returns_false_and_preserves_previous_bytes(
    tmp_path, monkeypatch
):
    path = tmp_path / "titles.json"
    vault = tmp_path / "vault-a"
    assert titles.remember_titles(vault, "space-1", BASE_URL, {"s1": "One"}, path=path)
    before = path.read_bytes()

    def _boom(_path, _data):
        raise OSError("disk is full")

    monkeypatch.setattr(titles, "atomic_write_json", _boom)
    assert (
        titles.remember_titles(vault, "space-1", BASE_URL, {"s1": "Two"}, path=path)
        is False
    )

    assert path.read_bytes() == before
    assert titles.read_titles(vault, "space-1", BASE_URL, path=path) == {"s1": "One"}


def test_replace_failure_after_a_complete_temp_write_preserves_previous_bytes(
    tmp_path, monkeypatch
):
    path = tmp_path / "titles.json"
    vault = tmp_path / "vault-a"
    assert titles.remember_titles(vault, "space-1", BASE_URL, {"s1": "One"}, path=path)
    before = path.read_bytes()

    def _boom(*_args, **_kwargs):
        raise OSError("replace failed")

    # Fail the real atomic primitive after it has written and fsynced the
    # temp file but before the replace. The prior bytes must survive.
    monkeypatch.setattr(cache_io.os, "replace", _boom)
    assert (
        titles.remember_titles(vault, "space-1", BASE_URL, {"s1": "Two"}, path=path)
        is False
    )

    assert path.read_bytes() == before
    assert list(tmp_path.glob(".*.tmp")) == []
    assert titles.read_titles(vault, "space-1", BASE_URL, path=path) == {"s1": "One"}


def test_cache_file_lock_file_and_created_directories_are_owner_only(tmp_path):
    path = tmp_path / "fresh" / "nested" / "titles.json"
    assert (
        titles.remember_titles(
            tmp_path / "vault-a", "space-1", BASE_URL, {"s1": "One"}, path=path
        )
        is True
    )

    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE((tmp_path / "fresh").stat().st_mode) == 0o700
    assert stat.S_IMODE((tmp_path / "fresh" / "nested").stat().st_mode) == 0o700
    lock = path.with_name(path.name + ".lock")
    assert lock.is_file()
    assert stat.S_IMODE(lock.stat().st_mode) == 0o600


def test_blank_and_whitespace_titles_are_not_remembered(tmp_path):
    path = tmp_path / "titles.json"
    vault = tmp_path / "vault-a"

    assert (
        titles.remember_titles(
            vault,
            "space-1",
            BASE_URL,
            {"a": "Alpha", "b": "   ", "c": "", "d": "\t\n", "e": None},
            path=path,
        )
        is True
    )

    assert titles.read_titles(vault, "space-1", BASE_URL, path=path) == {"a": "Alpha"}
    assert set(_stored_titles(path, vault, "space-1")) == {"a"}


def test_blank_titles_already_stored_are_ignored_on_read(tmp_path):
    path = tmp_path / "titles.json"
    vault = tmp_path / "vault-a"
    namespace = cache_io.cache_namespace(vault, "space-1", BASE_URL)
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "namespaces": {
                    namespace: {"titles": {"a": "Alpha", "b": "  ", "c": ""}}
                },
            }
        ),
        encoding="utf-8",
    )

    assert titles.read_titles(vault, "space-1", BASE_URL, path=path) == {"a": "Alpha"}


def test_unwritable_cache_path_returns_false_without_raising(tmp_path):
    locked = tmp_path / "locked"
    locked.mkdir()
    path = locked / "titles.json"
    os.chmod(locked, 0o500)
    try:
        assert (
            titles.remember_titles(
                tmp_path / "vault-a", "space-1", BASE_URL, {"s1": "One"}, path=path
            )
            is False
        )
    finally:
        os.chmod(locked, 0o700)

    assert not path.exists()
    assert list(locked.iterdir()) == []


def test_missing_file_reads_empty_and_creates_nothing(tmp_path):
    path = tmp_path / "titles.json"

    assert titles.read_titles(tmp_path / "vault-a", "space-1", BASE_URL, path=path) == {}
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "payload",
    [
        b"",
        b"not json at all",
        b"[]",
        b'{"version": 2, "namespaces": {}}',
        b'{"version": true, "namespaces": {}}',
        b'{"version": 1}',
        b'{"version": 1, "namespaces": "nope"}',
    ],
)
def test_corrupt_and_unsupported_documents_read_empty_and_are_not_rewritten(
    tmp_path, payload
):
    path = tmp_path / "titles.json"
    path.write_bytes(payload)
    before = path.read_bytes()

    assert titles.read_titles(tmp_path / "vault-a", "space-1", BASE_URL, path=path) == {}
    assert path.read_bytes() == before
    assert list(tmp_path.iterdir()) == [path]


def test_malformed_namespace_entry_reads_empty(tmp_path):
    path = tmp_path / "titles.json"
    vault = tmp_path / "vault-a"
    namespace = cache_io.cache_namespace(vault, "space-1", BASE_URL)

    for entry in ("nope", [], {"titles": "nope"}, {"titles": 3}, {}):
        path.write_text(
            json.dumps({"version": 1, "namespaces": {namespace: entry}}),
            encoding="utf-8",
        )
        assert titles.read_titles(vault, "space-1", BASE_URL, path=path) == {}

    # A namespace for a different scope is not this scope's namespace.
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "namespaces": {
                    cache_io.cache_namespace(vault, "space-2", BASE_URL): {
                        "titles": {"s1": "One"}
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    assert titles.read_titles(vault, "space-1", BASE_URL, path=path) == {}


def test_remember_repairs_a_corrupt_document(tmp_path):
    path = tmp_path / "titles.json"
    vault = tmp_path / "vault-a"
    path.write_bytes(b"garbage")

    assert (
        titles.remember_titles(vault, "space-1", BASE_URL, {"s1": "One"}, path=path)
        is True
    )

    assert _document(path) == {
        "version": titles.TITLES_SCHEMA_VERSION,
        "namespaces": {
            cache_io.cache_namespace(vault, "space-1", BASE_URL): {
                "titles": {"s1": "One"}
            }
        },
    }
    assert titles.read_titles(vault, "space-1", BASE_URL, path=path) == {"s1": "One"}


def test_invalid_titles_argument_returns_false_and_keeps_the_file(tmp_path):
    path = tmp_path / "titles.json"
    vault = tmp_path / "vault-a"
    assert titles.remember_titles(vault, "space-1", BASE_URL, {"s1": "One"}, path=path)
    before = path.read_bytes()

    assert (
        titles.remember_titles(vault, "space-1", BASE_URL, None, path=path) is False
    )
    assert (
        titles.remember_titles(vault, "space-1", BASE_URL, ["s1"], path=path) is False
    )
    assert path.read_bytes() == before


def test_concurrent_writers_keep_every_namespace(tmp_path):
    path = tmp_path / "titles.json"
    errors = []

    def worker(index):
        try:
            ok = titles.remember_titles(
                tmp_path / f"vault-{index}",
                "space-1",
                BASE_URL,
                {f"s{index}": f"Title {index}"},
                path=path,
            )
            if not ok:
                errors.append(RuntimeError(f"writer {index} returned False"))
        except Exception as exc:  # noqa: BLE001 — surfaced through the assertion
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(index,)) for index in range(16)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert len(_document(path)["namespaces"]) == 16
    for index in range(16):
        assert titles.read_titles(
            tmp_path / f"vault-{index}", "space-1", BASE_URL, path=path
        ) == {f"s{index}": f"Title {index}"}


def test_default_path_is_machine_local_and_sibling_of_the_content_cache():
    default = titles.DEFAULT_TITLES_CACHE_PATH
    assert default == (
        Path.home() / ".config" / "tdtb" / "tdtb-capacities-structure-titles.json"
    )
    assert default.parent == cb.DEFAULT_CONTENT_CACHE_PATH.parent
    assert default != cb.DEFAULT_CONTENT_CACHE_PATH
    assert titles.TITLES_SCHEMA_VERSION == 1
