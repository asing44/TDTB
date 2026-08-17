"""Stable-ID duration memory and deterministic duration-source resolution.

Frozen plan items 10-12 (``Plans Link/2026-08-09-tdtb-planning-ui-reliability.md``):

- Precedence (item 11): saved user memory -> deterministic tag mapping ->
  Todoist native or exact named preset -> contract-defined type field ->
  default. Same-precedence tag collisions fail visibly.
- The resolver returns ``(value_minutes, source_label)`` where source_label is
  one of ``remembered`` / ``tag:<name>`` / ``native`` / ``preset`` / ``type`` /
  ``default``.
- Memory is keyed by canonical source identity (Todoist task id via
  ``todoist:<id>``; normalized vault path), survives recurring occurrences,
  uses atomic writes (tmp + ``os.replace`` when a path is supplied), and never
  rewrites Todoist/vault source duration fields. Name alone is not an
  identity (item 12).

The default store is in-memory so unit tests never touch disk; callers opt
into persistence by passing ``path`` (under the existing TDTB cache
discipline). ``resolve_duration`` is pure — it never writes anything.

FT-01 MVP (vault-scoped persistence):
- ``save_memory`` / ``reset_memory`` / ``read_vault_memory`` persist a
  versioned JSON cache beneath ``00 - META/Cache`` under the RESOLVED
  ``vault_root`` — paths derive only from the caller-supplied vault root.
- Values are strict nonnegative integers divisible by 5 (``DURATION_STEP_MINUTES``);
  bools, fractions, negatives, and off-grid values raise ``ValueError``
  before any file access.
- Writes serialize read-modify-write under a per-vault lock and replace
  atomically (tmp + ``os.replace``). Lock/read/write failures raise
  ``MemoryStoreError``/``OSError`` and never replace known durable bytes.
- Reads (missing/corrupt/unsupported) fall back to ``{}`` with no repair;
  the GET /plan-inputs overlay applies only valid remembered values.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from pathlib import Path
from typing import Any

try:
    import fcntl as _fcntl  # POSIX advisory file locks (macOS/Linux)
except ImportError:  # pragma: no cover — non-POSIX fallback
    _fcntl = None

import runstate

# Deterministic tag mapping: a label matching ``dur<N>`` means N minutes.
_TAG_DURATION_RE = re.compile(r"^dur(\d+)$", re.IGNORECASE)

# Contract-defined per-type duration fields (the vault FileClass owns the
# contract; press notes carry ``duration_min`` in minutes). Mirrors
# ``main._TYPE_DURATION_FIELDS`` — duplicated here so the resolver stays a
# pure, importable seam.
_TYPE_DURATION_FIELDS: dict[str, str] = {"press": "duration_min"}

DEFAULT_MINUTES = 30

# In-memory default store (tests / callers without a persistence path).
_store: dict[str, int] = {}

# ---------------------------------------------------------------------------
# FT-01 MVP: vault-scoped versioned cache constants
# ---------------------------------------------------------------------------
# The cache and lock live beneath ``00 - META/Cache`` under the RESOLVED
# vault root — paths derive only from the caller-supplied vault_root, never
# from cwd, env, or display names.

CACHE_DIR_REL = runstate.CACHE_DIR_REL  # "00 - META/Cache"
CACHE_REL_PATH = f"{CACHE_DIR_REL}/tdtb-duration-memory.json"
LOCK_REL_PATH = f"{CACHE_DIR_REL}/tdtb-duration-memory.lock"
SCHEMA_VERSION = 1
DURATION_STEP_MINUTES = 5


class MemoryStoreError(Exception):
    """Duration-memory cache read/write failure — callers fail closed and
    preserve existing durable bytes (FT-01)."""


def cache_path(vault_root: str | Path) -> Path:
    """The versioned JSON cache path beneath the resolved vault root."""
    return Path(vault_root) / CACHE_REL_PATH


def lock_path(vault_root: str | Path) -> Path:
    """The lock-file path beneath the resolved vault root."""
    return Path(vault_root) / LOCK_REL_PATH


def validate_duration_minutes(value: Any) -> int:
    """Strict FT-01 validation: a JSON integer >= 0 divisible by 5.

    Rejects bools (which are ints in Python), floats/fractions (no
    coercion), negatives, and off-grid values. Returns the validated int.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(
            f"duration minutes must be an integer, got {type(value).__name__}"
        )
    if value < 0:
        raise ValueError(f"duration minutes must be >= 0, got {value}")
    if value % DURATION_STEP_MINUTES != 0:
        raise ValueError(
            f"duration minutes must be divisible by {DURATION_STEP_MINUTES}, "
            f"got {value}"
        )
    return value


def _encode(items: dict[str, int]) -> dict[str, Any]:
    return {"version": SCHEMA_VERSION, "items": dict(sorted(items.items()))}


# Per-vault-root process-local RMW locks (same single-process convention as
# ``runstate`` G26 / ``deferrals._STORE_LOCK``); the flock on the vault-scoped
# lock file adds cross-process serialization for the same bytes.
_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _store_lock(vault_root: str | Path) -> threading.Lock:
    key = str(Path(vault_root).resolve())
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.Lock())


def _acquire_lock_file(vault_root: str | Path) -> Any:
    """Advisory exclusive flock on the vault-scoped lock file. Returns the
    open file handle (released on close). Raises on failure — callers fail
    closed with no cache mutation."""
    path = lock_path(vault_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(path, "a+", encoding="utf-8")
    try:
        if _fcntl is not None:
            _fcntl.flock(fh.fileno(), _fcntl.LOCK_EX)
    except BaseException:
        fh.close()
        raise
    return fh


def _release_lock_file(fh: Any) -> None:
    try:
        if _fcntl is not None:
            _fcntl.flock(fh.fileno(), _fcntl.LOCK_UN)
    except OSError:
        pass
    try:
        fh.close()
    except OSError:
        pass


def _load_for_write(vault_root: str | Path) -> dict[str, int]:
    """Strict read used ONLY by the write path. Missing file → {} (fresh
    cache). Corrupt / unsupported / any-invalid-entry data raises
    ``MemoryStoreError`` so the write fails closed and never replaces
    known durable bytes."""
    path = cache_path(vault_root)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        # FT-05 F3: bounded client-safe message — no absolute vault/cache
        # path. The original cause (with the path) stays in the exception
        # chain for server-side diagnostics.
        raise MemoryStoreError(
            "duration-memory cache unreadable — refusing to overwrite"
        ) from exc
    if not isinstance(data, dict):
        raise MemoryStoreError(
            "duration-memory cache has unsupported shape — refusing to overwrite"
        )
    if data.get("version") != SCHEMA_VERSION:
        raise MemoryStoreError(
            f"duration-memory cache version {data.get('version')!r} is "
            "unsupported — refusing to overwrite"
        )
    items = data.get("items")
    if not isinstance(items, dict):
        raise MemoryStoreError(
            "duration-memory cache items has unsupported shape — refusing to overwrite"
        )
    out: dict[str, int] = {}
    for key, raw in items.items():
        try:
            norm = normalize_identity(str(key))
            value = validate_duration_minutes(raw)
        except (TypeError, ValueError) as exc:
            raise MemoryStoreError(
                "duration-memory cache contains an invalid entry — refusing "
                "to overwrite"
            ) from exc
        out[norm] = value
    return out


def read_vault_memory(vault_root: str | Path) -> dict[str, int]:
    """Vault-scoped remembered durations.

    Missing, corrupt, or unsupported cache data falls back to ``{}`` with
    NO repair — reads never write. Invalid entries in an otherwise-valid
    file are skipped (the caller overlays only valid remembered values).
    """
    path = cache_path(vault_root)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict) or data.get("version") != SCHEMA_VERSION:
        return {}
    items = data.get("items")
    if not isinstance(items, dict):
        return {}
    out: dict[str, int] = {}
    for key, raw in items.items():
        try:
            norm = normalize_identity(str(key))
            value = validate_duration_minutes(raw)
        except (TypeError, ValueError):
            continue
        out[norm] = value
    return out


def save_memory(
    vault_root: str | Path, identity: str, minutes: int
) -> int:
    """Persist a strictly-validated remembered duration for one canonical
    identity, vault-scoped, under the cache lock with an atomic replace.

    Validation (identity + value) happens BEFORE any file access; a lock,
    read, or write failure raises and never replaces existing durable bytes
    (fail closed). Returns the persisted value.
    """
    key = normalize_identity(identity)
    value = validate_duration_minutes(minutes)
    root = Path(vault_root)
    with _store_lock(root):
        fh = _acquire_lock_file(root)
        try:
            items = _load_for_write(root)
            items[key] = value
            _atomic_write_json(cache_path(root), _encode(items))
        finally:
            _release_lock_file(fh)
    return value


def reset_memory(vault_root: str | Path, identity: str) -> bool:
    """Remove the remembered duration for one canonical identity, vault-
    scoped, under the cache lock with an atomic replace.

    Returns True when an entry was removed. A missing cache is a no-op
    (returns False without creating the cache file); corrupt or unsupported
    data raises ``MemoryStoreError`` without replacing the bytes.
    """
    key = normalize_identity(identity)
    root = Path(vault_root)
    with _store_lock(root):
        fh = _acquire_lock_file(root)
        try:
            items = _load_for_write(root)
            if key not in items:
                return False
            del items[key]
            _atomic_write_json(cache_path(root), _encode(items))
            return True
        finally:
            _release_lock_file(fh)


def apply_remembered_overlay(
    row: dict[str, Any], memory: dict[str, int] | None
) -> dict[str, Any]:
    """Overlay a valid remembered duration onto a digest row IN PLACE:
    sets ``blocks`` (exact minutes/30 conversion — int when integral, float
    otherwise, e.g. 45 -> 1.5), ``duration_source`` = ``remembered``, and
    ``duration_minutes``. No-op for rows without a canonical identity or
    without a valid remembered value. Pure — never writes the cache, calls
    billed endpoints, or touches upstream sources.

    FT-05 F1: the projection must preserve the exact remembered minutes —
    a 45-minute value stays 1.5 blocks, never a 30-minute-grid ceiling of 2.
    """
    identity = item_identity(row)
    if not identity:
        return row
    raw = (memory or {}).get(identity)
    if raw is None:
        return row
    try:
        minutes = validate_duration_minutes(raw)
    except (TypeError, ValueError):
        return row  # invalid remembered entry never overlays
    blocks = minutes / 30
    row["blocks"] = int(blocks) if blocks.is_integer() else blocks
    row["duration_source"] = "remembered"
    row["duration_minutes"] = minutes
    return row


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------

def normalize_identity(identity: str) -> str:
    """Validate and normalize a canonical source identity.

    Valid identities: ``todoist:<id>`` (Todoist task id), ``vault:<path>``
    or any path-shaped value containing ``/`` (normalized vault path). A bare
    display name is not an identity and raises ``ValueError``.
    """
    text = str(identity or "").strip()
    if text.startswith("todoist:"):
        tail = text[len("todoist:"):].strip()
        if tail:
            return f"todoist:{tail}"
    if text.startswith("vault:"):
        tail = text[len("vault:"):].strip()
        if tail:
            return f"vault:{tail}"
    if "/" in text:
        return text
    raise ValueError(
        f"{identity!r} is not a stable source identity "
        "(name alone is not an identity)"
    )


def item_identity(item: dict[str, Any]) -> str | None:
    """Canonical identity for a digest/assigned row, or None when absent."""
    todoist_id = item.get("todoist_id")
    if todoist_id not in (None, ""):
        return f"todoist:{todoist_id}"
    path = item.get("path")
    if path and not str(path).startswith("todoist://"):
        return str(path)
    return None


# ---------------------------------------------------------------------------
# Memory store (atomic when a path is supplied)
# ---------------------------------------------------------------------------

def _load(path: str | Path | None) -> dict[str, int]:
    if path is None:
        return _store
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _atomic_write_json(path: str | Path, data: dict[str, Any]) -> None:
    """Write ``data`` atomically: temp file in the same directory, then
    ``os.replace``. Never leaves a torn cache file."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=f".{p.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, sort_keys=True)
        os.replace(tmp, p)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_memory(identity: str, path: str | Path | None = None) -> int | None:
    """Return remembered minutes for a canonical identity, or None."""
    return _load(path).get(normalize_identity(identity))


def store_memory(
    identity: str, minutes: int, path: str | Path | None = None
) -> int:
    """Persist remembered minutes for a canonical identity.

    Raises ``ValueError`` for a name-only identity (item 12) or a negative
    duration. With ``path`` the write is atomic; without it the in-memory
    default store is updated.
    """
    key = normalize_identity(identity)
    value = int(minutes)
    if value < 0:
        raise ValueError(f"duration minutes must be >= 0, got {minutes}")
    store = dict(_load(path))
    store[key] = value
    if path is None:
        _store.clear()
        _store.update(store)
    else:
        _atomic_write_json(path, store)
    return value


# ---------------------------------------------------------------------------
# Deterministic source resolution
# ---------------------------------------------------------------------------

def _duration_tag(item: dict[str, Any]) -> tuple[str | None, int | None]:
    """The deterministic tag mapping for an item's labels.

    Returns ``(tag_name, minutes)``; more than one DISTINCT duration among
    matching tags raises ``ValueError`` (same-precedence collision, item 11).
    """
    matches: list[tuple[str, int]] = []
    for label in item.get("labels") or []:
        m = _TAG_DURATION_RE.match(str(label).strip())
        if m:
            matches.append((str(label).strip(), int(m.group(1))))
    if not matches:
        return None, None
    distinct = {minutes for _, minutes in matches}
    if len(distinct) > 1:
        raise ValueError(
            "same-precedence duration tag collision: "
            + ", ".join(tag for tag, _ in matches)
        )
    return matches[0][0], distinct.pop()


def _native_minutes(item: dict[str, Any]) -> int | None:
    """Todoist-native duration in minutes: raw ``{"unit", "amount"}`` shape
    or the already-parsed bare minutes the read seam emits. None when
    absent/unparseable; explicit zero is honored."""
    dur = item.get("duration")
    if isinstance(dur, dict):
        amount = dur.get("amount")
        unit = str(dur.get("unit") or "").casefold()
        if unit == "minute" and amount is not None:
            return int(amount)
        if unit == "day" and amount is not None:
            return int(amount) * 24 * 60
        return None
    if isinstance(dur, bool) or dur is None:
        return None
    if isinstance(dur, (int, float)):
        return int(dur)
    return None


def _preset_minutes(name: str, presets: list[dict[str, Any]] | None) -> int | float | None:
    """Minutes from the ``## Presets`` row whose Name matches (case/
    whitespace-insensitive); None on no match or unparseable Blocks.
    Fractional blocks stay fractional minutes (2.5 blocks -> 75.0)."""
    key = str(name or "").strip().casefold()
    if not key:
        return None
    for row in presets or []:
        if not isinstance(row, dict):
            continue
        row_name = str(row.get("Name") or row.get("name") or "").strip().casefold()
        if row_name != key:
            continue
        try:
            blocks = float(str(row.get("Blocks") or row.get("blocks")).strip())
        except (TypeError, ValueError):
            return None
        minutes = blocks * 30
        return int(minutes) if minutes.is_integer() else minutes
    return None


def _type_minutes(item: dict[str, Any], fm: dict[str, Any] | None) -> int | None:
    """Minutes from the contract-defined per-type frontmatter field."""
    if not isinstance(fm, dict):
        return None
    for t in item.get("types") or []:
        field = _TYPE_DURATION_FIELDS.get(str(t))
        if field is None:
            continue
        raw = fm.get(field)
        if raw is None:
            continue
        import time_engine

        return time_engine.duration_minutes(raw)
    return None


def resolve_duration(
    item: dict[str, Any],
    presets: list[dict[str, Any]] | None = None,
    fm: dict[str, Any] | None = None,
    memory: dict[str, int] | None = None,
) -> tuple[int | float, str]:
    """Resolve a row's duration minutes and the winning source label.

    Locked precedence (frozen item 11): remembered memory -> deterministic
    tag mapping -> Todoist native or exact named preset -> contract-defined
    type field -> default. Same-precedence tag collisions raise ValueError.
    Pure — never writes source duration fields or the memory store.
    """
    identity = item_identity(item)
    if identity and memory and identity in memory:
        return int(memory[identity]), "remembered"

    tag, tag_minutes = _duration_tag(item)
    if tag is not None and tag_minutes is not None:
        return tag_minutes, f"tag:{tag}"

    native = _native_minutes(item)
    if native is not None:
        return native, "native"

    preset = _preset_minutes(str(item.get("name") or ""), presets)
    if preset is not None:
        return preset, "preset"

    typed = _type_minutes(item, fm)
    if typed is not None:
        return typed, "type"

    return DEFAULT_MINUTES, "default"
