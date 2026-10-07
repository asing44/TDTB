"""capacities_structure_titles.py — machine-local Capacities title cache.

Capacities returns a display title for every structure, but the vault-local
mapping record stores only structure IDs and deliberately persists no
provider metadata: the mapping is operator-owned and revision-checked, while
a title is disposable, last-observed display metadata. This cache keeps those
titles OUTSIDE the vault, in the same machine-local root as the credential
slot and the content cache, so nothing lands in a synced or backed-up tree.

The on-disk document is::

    {
      "version": 1,
      "namespaces": {
        "<sha256 hex of vault root + space + base url>": {
          "titles": {"<structure id>": "<display title>"}
        }
      }
    }

The namespace key is the same separator-based digest the content cache uses,
so two different vaults, spaces, or provider base URLs never share entries.
``remember_titles`` replaces only its own namespace and preserves every other
one. There is deliberately no TTL: this is last-observed display metadata,
refreshed only when a read already happens.

The cache is disposable and must never break a read or a settings write:
``read_titles`` returns ``{}`` for a missing, unreadable, corrupt,
unsupported, or malformed file and never writes, repairs, or creates
anything — not even the lock file. ``remember_titles`` returns ``False``
when it cannot persist and never raises. Titles are display-only and never
identity, so nothing here treats a title as a key.
"""
from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from capacities_cache_io import (
    acquire_path_lock,
    atomic_write_json,
    cache_namespace,
    release_lock_file,
    store_lock,
)

#: Machine-local (never vault-local), a sibling of the content cache at
#: ``DEFAULT_CONTENT_CACHE_PATH`` and of the credential slot.
DEFAULT_TITLES_CACHE_PATH = (
    Path.home() / ".config" / "tdtb" / "tdtb-capacities-structure-titles.json"
)
TITLES_SCHEMA_VERSION = 1

_OWNER_ONLY_FILE_MODE = 0o600
_PRIVATE_DIR_MODE = 0o700


def _resolve_path(path: str | Path | None) -> Path:
    return Path(path) if path is not None else DEFAULT_TITLES_CACHE_PATH


def _lock_file_for(path: Path) -> Path:
    return path.with_name(path.name + ".lock")


def _ensure_private_directory(directory: Path) -> None:
    """Create ``directory`` and any missing ancestors owner-only (0700).

    An existing directory keeps its own mode: only directories this call
    creates are hardened, so a caller-supplied parent such as ``~/.config``
    is never rewritten.
    """
    missing: list[Path] = []
    current = directory
    while not current.exists():
        missing.append(current)
        parent = current.parent
        if parent == current:
            break
        current = parent
    for target in reversed(missing):
        try:
            os.mkdir(target, _PRIVATE_DIR_MODE)
        except FileExistsError:
            continue  # a concurrent creator owns that directory's mode
        os.chmod(target, _PRIVATE_DIR_MODE)


def _ensure_owner_only_file(path: Path) -> None:
    """Create ``path`` if absent and force owner-only (0600) file mode."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT, _OWNER_ONLY_FILE_MODE)
    os.close(fd)
    os.chmod(path, _OWNER_ONLY_FILE_MODE)


def _clean_titles(titles: Any) -> dict[str, str]:
    """Structure id -> non-blank title, dropping anything unusable.

    A blank or whitespace-only title must never be stored: the consumer
    falls back to the structure ID when a title is absent, and a stored
    blank would defeat that fallback.
    """
    cleaned: dict[str, str] = {}
    if not isinstance(titles, Mapping):
        return cleaned
    for key, value in titles.items():
        if not isinstance(key, str) or not key.strip():
            continue
        if not isinstance(value, str) or not value.strip():
            continue
        cleaned[key] = value
    return cleaned


def _load_document(path: Path) -> dict[str, Any]:
    """The usable stored document, or a fresh valid frame.

    Corrupt, unreadable, or unsupported-version storage is treated as a cold
    start: ``remember_titles`` may replace it (only a read is forbidden to).
    Other namespaces are carried through as parsed values, so re-serialization
    leaves them untouched.
    """
    fresh: dict[str, Any] = {"version": TITLES_SCHEMA_VERSION, "namespaces": {}}
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return fresh
    try:
        document = json.loads(raw)
    except ValueError:
        return fresh
    if not isinstance(document, dict):
        return fresh
    version = document.get("version")
    if type(version) is not int or version != TITLES_SCHEMA_VERSION:
        return fresh
    namespaces = document.get("namespaces")
    if not isinstance(namespaces, dict):
        return fresh
    return {"version": TITLES_SCHEMA_VERSION, "namespaces": namespaces}


def read_titles(
    vault_root: str | Path,
    space_id: str,
    base_url: str,
    *,
    path: str | Path | None = None,
) -> dict[str, str]:
    """Return the last observed structure titles for this namespace.

    A missing file, an unreadable or corrupt file, an unsupported version,
    another namespace, or a malformed namespace entry all yield ``{}`` and
    never raise. This function never writes, repairs, or creates anything.
    """
    cache_path = _resolve_path(path)
    try:
        namespace = cache_namespace(vault_root, space_id, base_url)
        raw = cache_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError, TypeError, ValueError):
        return {}
    try:
        document = json.loads(raw)
    except ValueError:
        return {}
    if not isinstance(document, dict):
        return {}
    version = document.get("version")
    if type(version) is not int or version != TITLES_SCHEMA_VERSION:
        return {}
    namespaces = document.get("namespaces")
    if not isinstance(namespaces, dict):
        return {}
    entry = namespaces.get(namespace)
    if not isinstance(entry, dict):
        return {}
    return _clean_titles(entry.get("titles"))


def remember_titles(
    vault_root: str | Path,
    space_id: str,
    base_url: str,
    titles: Mapping[Any, Any],
    *,
    path: str | Path | None = None,
) -> bool:
    """Replace this namespace's title snapshot in the machine-local cache.

    Every other namespace is preserved. Returns ``True`` on success and
    ``False`` for any failure — an unusable cache path, an unwritable
    directory, a failing atomic write, or an invalid ``titles`` argument —
    and never raises: a cache problem must never block planning or settings.
    No diagnostic here carries a path.
    """
    if not isinstance(titles, Mapping):
        return False
    snapshot = _clean_titles(titles)
    cache_path = _resolve_path(path)
    lock_file = _lock_file_for(cache_path)
    try:
        namespace = cache_namespace(vault_root, space_id, base_url)
        _ensure_private_directory(cache_path.parent)
        _ensure_owner_only_file(lock_file)
        with store_lock(cache_path):
            handle = acquire_path_lock(lock_file)
            try:
                document = _load_document(cache_path)
                document["namespaces"][namespace] = {"titles": snapshot}
                atomic_write_json(cache_path, document)
            finally:
                release_lock_file(handle)
    except Exception:  # noqa: BLE001 — a cache problem must never block callers
        return False
    return True
