"""capacities_cache_io.py — shared machine-local cache IO primitives.

Dependency-free (standard library only) helpers shared by the vault-local
Capacities stores and the machine-local Capacities caches. They live in their
own module because a cache module that ``capacities_builder`` imports needs
them without importing the builder back (an import cycle).

The semantics are the ones the persistent content cache shipped with:

* an atomic same-directory temp write — a unique temp name, JSON serialized
  deterministically, flushed and fsynced, then ``os.replace``;
* a per-key process-local lock paired with a POSIX advisory lock file for
  cross-process serialization, and a release that never raises;
* the separator-based namespace digest that keeps two vaults, two spaces, or
  two provider base URLs from ever sharing cache entries.

Nothing here reads a credential, configuration, or vault file: the caller
owns the path and the data.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Any

try:
    import fcntl as _fcntl  # POSIX advisory file locks (macOS/Linux)
except ImportError:  # pragma: no cover — non-POSIX fallback
    _fcntl = None

#: Per-key process-local locks (the same single-process convention as
#: ``capacities_settings``); the flock on the lock file adds cross-process
#: serialization for the same bytes.
_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def store_lock(key: str | Path) -> threading.Lock:
    """The process-local lock for ``key`` (a resolved path string)."""
    resolved = str(Path(key).resolve())
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(resolved, threading.Lock())


def acquire_path_lock(path: str | Path) -> Any:
    """Open ``path`` and take the POSIX advisory lock (no-op off POSIX)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fh = open(p, "a+", encoding="utf-8")
    try:
        if _fcntl is not None:
            _fcntl.flock(fh.fileno(), _fcntl.LOCK_EX)
    except BaseException:
        fh.close()
        raise
    return fh


def release_lock_file(fh: Any) -> None:
    """Release the advisory lock on ``fh`` and close it, never raising."""
    try:
        if _fcntl is not None:
            _fcntl.flock(fh.fileno(), _fcntl.LOCK_UN)
    except OSError:
        pass
    try:
        fh.close()
    except OSError:
        pass


def atomic_write_json(path: str | Path, data: dict[str, Any]) -> None:
    """Write ``data`` atomically: a unique temp file in the same directory,
    flushed and fsynced, then ``os.replace``."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=f".{p.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, p)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def cache_namespace(
    vault_root: str | Path, space_id: str, base_url: str
) -> str:
    """Stable digest identifying the vault + space + provider scope.

    Stored instead of the raw triple so the machine-local file and every
    diagnostic stay free of absolute paths; equality is all the cache needs.
    """
    material = "\x1f".join(
        (str(Path(vault_root).resolve()), str(space_id), str(base_url).rstrip("/"))
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()
