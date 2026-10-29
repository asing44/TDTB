"""capacities_tag_titles.py — machine-local Capacities tag-title cache.

Capacities returns tag references on an object's ``tags`` property as
``{"id": ...}`` with no display title (the cached object payloads carry no
title either), while the space's ``RootTag`` catalog is the only place a tag's
current title lives. Planning rows are projected from that cached generation
by the OFFLINE direct intake, which must never call the provider, so the
refresh's catalog read has to survive the run that performed it.

This cache carries exactly that: disposable ``tag id -> display title``
metadata, kept OUTSIDE the mapping record, the object cache, and the published
generation. It is written only when a refresh has already read the catalog
through the paced provider seam, and read only when an offline replay seeds an
adapter before projecting. Titles are display-only and never identity: a
missing or stale entry can only change what a chip prints.

Freshness: a tag renamed in Capacities shows its new name after the next
Refresh, because a complete catalog replaces this namespace's map. A partial
catalog merges additively so a partial read never deletes a known title.

The on-disk document is::

    {
      "version": 1,
      "namespaces": {
        "<sha256 hex of space + base url>": {
          "titles": {"<tag id>": "<display title>"}
        }
      }
    }

The namespace key is the same separator-based digest the structure-title and
content caches use, so two spaces or two provider base URLs never share
entries. ``remember_titles`` replaces (or additively merges) only its own
namespace and preserves every other one. There is deliberately no TTL.

The cache is disposable and must never break a refresh, a planning read, or a
settings write: ``read_titles`` returns ``{}`` for a missing, unreadable,
corrupt, unsupported, or malformed file and never writes, repairs, or creates
anything — not even the lock file. ``remember_titles`` returns ``False`` when
it cannot persist and never raises. Nothing here reads a credential, and no
diagnostic carries a path or a tag title.
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

#: Machine-local, a sibling of the structure-title cache and of the content
#: cache: same private root, never a repository or a synced tree.
DEFAULT_TAG_TITLES_CACHE_PATH = (
    Path.home() / ".config" / "tdtb" / "tdtb-capacities-tag-titles.json"
)
TAG_TITLES_SCHEMA_VERSION = 1

#: The provider origin assumed when a caller cannot observe one. The offline
#: replay seam has no base URL of its own, so the reader and the writer resolve
#: the namespace through the same default and agree in the standard
#: deployment. This mirrors how the settings route reads structure titles.
DEFAULT_PROVIDER_BASE_URL = "https://api.capacities.io"

_OWNER_ONLY_FILE_MODE = 0o600
_PRIVATE_DIR_MODE = 0o700


def _resolve_path(path: str | Path | None) -> Path:
    return Path(path) if path is not None else DEFAULT_TAG_TITLES_CACHE_PATH


def _lock_file_for(path: Path) -> Path:
    return path.with_name(path.name + ".lock")


def provider_base_url(provider: Any = None) -> str:
    """The provider base URL this seam talks to, or the documented default.

    Resolution never guesses beyond one delegation hop (``provider`` then its
    ``raw`` transport, the paced seam's own shape). A seam that exposes no base
    URL — the offline replay provider is one — yields the default, which is the
    same value the standard deployment writes under.
    """
    for candidate in (provider, getattr(provider, "raw", None)):
        value = getattr(candidate, "base_url", None)
        if isinstance(value, str) and value.strip():
            return value
    return DEFAULT_PROVIDER_BASE_URL


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
    """Tag id -> non-blank title, dropping anything unusable.

    A blank or whitespace-only title is never stored: the consumer would print
    an empty chip and the payload's own blank is already the fallback.
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
    fresh: dict[str, Any] = {"version": TAG_TITLES_SCHEMA_VERSION, "namespaces": {}}
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
    if type(version) is not int or version != TAG_TITLES_SCHEMA_VERSION:
        return fresh
    namespaces = document.get("namespaces")
    if not isinstance(namespaces, dict):
        return fresh
    return {"version": TAG_TITLES_SCHEMA_VERSION, "namespaces": namespaces}


def read_titles(
    space_id: str,
    base_url: str,
    *,
    path: str | Path | None = None,
) -> dict[str, str]:
    """Return the last observed tag titles for this namespace.

    A missing file, an unreadable or corrupt file, an unsupported version,
    another namespace, or a malformed namespace entry all yield ``{}`` and
    never raise. This function never writes, repairs, or creates anything.
    """
    cache_path = _resolve_path(path)
    try:
        namespace = cache_namespace(space_id, base_url)
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
    if type(version) is not int or version != TAG_TITLES_SCHEMA_VERSION:
        return {}
    namespaces = document.get("namespaces")
    if not isinstance(namespaces, dict):
        return {}
    entry = namespaces.get(namespace)
    if not isinstance(entry, dict):
        return {}
    return _clean_titles(entry.get("titles"))


def remember_titles(
    space_id: str,
    base_url: str,
    titles: Mapping[Any, Any],
    *,
    merge: bool = False,
    path: str | Path | None = None,
) -> bool:
    """Write this namespace's tag-title snapshot to the machine-local cache.

    ``merge=False`` (a complete catalog) replaces the namespace's map, which is
    how a renamed tag reaches the next offline replay. ``merge=True`` (a
    partial catalog) adds and updates without deleting an id it did not see, so
    a partial read can never lose a known title. Every other namespace is
    preserved either way.

    Returns ``True`` on success and ``False`` for any failure — an unusable
    cache path, an unwritable directory, a failing atomic write, or an invalid
    ``titles`` argument — and never raises: a cache problem must never block a
    refresh or a planning read. No diagnostic here carries a path.
    """
    if not isinstance(titles, Mapping):
        return False
    snapshot = _clean_titles(titles)
    cache_path = _resolve_path(path)
    lock_file = _lock_file_for(cache_path)
    try:
        namespace = cache_namespace(space_id, base_url)
        _ensure_private_directory(cache_path.parent)
        _ensure_owner_only_file(lock_file)
        with store_lock(cache_path):
            handle = acquire_path_lock(lock_file)
            try:
                document = _load_document(cache_path)
                namespaces = document["namespaces"]
                if merge:
                    existing = namespaces.get(namespace)
                    carried: dict[str, str] = {}
                    if isinstance(existing, dict):
                        carried = dict(_clean_titles(existing.get("titles")))
                    carried.update(snapshot)
                    namespaces[namespace] = {"titles": carried}
                else:
                    namespaces[namespace] = {"titles": snapshot}
                atomic_write_json(cache_path, document)
            finally:
                release_lock_file(handle)
    except Exception:  # noqa: BLE001 — a cache problem must never block callers
        return False
    return True
