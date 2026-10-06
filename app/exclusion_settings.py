"""Versioned local persistence for the TDTB tag-exclusion policy.

This module owns the ONE local, vault-scoped store the tag-exclusion settings
API reads and writes::

    00 - META/Cache/tdtb-exclusion-settings.json

It persists only the editable policy — a set of stable Capacities tag
identities the planner must exclude before selection — plus a server-owned
schema ``version`` and monotonic ``revision``. It deliberately persists no
titles, no source properties, and no effective-planning results: the matcher
in :mod:`tag_exclusions` stays the single source of truth for decisions.

Contract:

- Missing file is safe: :func:`read_settings` returns the default policy
  (revision ``0``, no tag exclusions) and ``persisted=False`` WITHOUT creating
  a file.
- Malformed or unsupported existing storage fails closed: reads raise
  :class:`ExclusionSettingsFormatError`, and writes refuse while preserving
  the original bytes. Nothing is silently defaulted or erased.
- The schema carries an ``exclusions`` dimension object whose only supported
  dimension today is ``tags``. Unknown dimensions are rejected, never
  silently accepted — a future dimension must be an explicit schema change.
- Tag identities are stable Capacities identities: ``source`` is exactly
  ``capacities``, ``space_id`` is a non-empty whitespace-free string, and
  ``tag_id`` is a canonical UUID. Titles, ``#``-prefixed display names,
  case variants, and bare ids are never accepted as identity.
- Values are strict: revision is a nonnegative integer (bools, floats, and
  strings are rejected) and duplicate JSON keys are rejected at every level.
- Writes serialize read-modify-write under a per-vault process lock plus an
  advisory ``flock`` on a vault-scoped lock file, then replace atomically
  (unique same-directory temp + flush/fsync + ``os.replace``). A save whose
  ``expected_revision`` no longer matches the stored revision raises
  :class:`ExclusionSettingsConflictError` and leaves the bytes untouched.

The store is pure Python with no provider, credential, or network access.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import fcntl as _fcntl  # POSIX advisory file locks (macOS/Linux)
except ImportError:  # pragma: no cover — non-POSIX fallback
    _fcntl = None

import runstate

# ---------------------------------------------------------------------------
# Paths and schema constants
# ---------------------------------------------------------------------------
# The cache and lock live beneath ``00 - META/Cache`` under the RESOLVED vault
# root — paths derive only from the caller-supplied vault_root, never from cwd,
# env, or display names. The temp name is unique per write (mkstemp), so it can
# never collide with another writer's runstate/cache temp.

SETTINGS_REL_PATH = f"{runstate.CACHE_DIR_REL}/tdtb-exclusion-settings.json"
LOCK_REL_PATH = f"{runstate.CACHE_DIR_REL}/tdtb-exclusion-settings.lock"
SCHEMA_VERSION = 1

#: The only supported exclusion dimension today. The dimension object is a
#: closed schema: adding a dimension later is an explicit, reviewed change.
EXCLUSION_DIMENSIONS = frozenset({"tags"})

#: The only supported exclusion source today. Identity is stable and
#: source-qualified; a bare title or a ``#``-prefixed display name is never an
#: identity.
EXCLUSION_SOURCES = frozenset({"capacities"})

_TOP_KEYS = frozenset({"version", "revision", "exclusions"})
_ENTRY_KEYS = frozenset({"source", "space_id", "tag_id"})


class ExclusionSettingsStoreError(Exception):
    """Tag-exclusion settings storage failure — callers fail closed and
    preserve the existing durable bytes."""


class ExclusionSettingsFormatError(ExclusionSettingsStoreError):
    """Existing tag-exclusion storage is malformed or unsupported.

    Raised for an unparseable file, duplicate JSON keys, unknown/missing keys,
    unknown exclusion dimensions, strict-type violations, unsupported
    versions, or invalid tag identities. Callers must never overwrite the
    offending bytes."""


class ExclusionSettingsConflictError(ExclusionSettingsStoreError):
    """The caller's ``expected_revision`` is stale — a concurrent save won."""


def canonical_tag_id(value: Any) -> str:
    """Validate and return a canonical UUID tag id.

    Accepts only the canonical lowercase hyphenated UUID form. Uppercase,
    braced, URN, and hyphen-less spellings are distinct strings and are
    rejected rather than silently normalized — a non-canonical spelling would
    become an identity that never matches the source.
    """
    if not isinstance(value, str):
        raise ValueError(f"{value!r} is not a canonical tag id")
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError, TypeError) as exc:
        raise ValueError(f"{value!r} is not a canonical tag id") from exc
    if str(parsed) != value:
        raise ValueError(f"{value!r} is not a canonical tag id")
    return value


def canonical_space_id(value: Any) -> str:
    """Validate and return a non-empty, whitespace-free space id."""
    if not isinstance(value, str) or not value:
        raise ValueError(f"{value!r} is not a valid space id")
    if value != value.strip() or any(char.isspace() for char in value):
        raise ValueError(f"{value!r} is not a valid space id")
    return value


def canonical_source(value: Any) -> str:
    """Validate and return a supported source name (exact casing)."""
    if not isinstance(value, str) or value not in EXCLUSION_SOURCES:
        raise ValueError(f"{value!r} is not a supported exclusion source")
    return value


@dataclass(frozen=True)
class TagExclusion:
    """One stable tag identity the planner must exclude, any-match.

    Construction is strict and happens BEFORE any file access: an unsupported
    source, an empty/whitespace space, or a non-canonical UUID raises
    ``ValueError``.
    """

    source: str
    space_id: str
    tag_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "source", canonical_source(self.source))
        object.__setattr__(self, "space_id", canonical_space_id(self.space_id))
        object.__setattr__(self, "tag_id", canonical_tag_id(self.tag_id))

    def as_dict(self) -> dict[str, str]:
        return {
            "source": self.source,
            "space_id": self.space_id,
            "tag_id": self.tag_id,
        }


def _sort_key(entry: TagExclusion) -> tuple[str, str, str]:
    return (entry.source, entry.space_id, entry.tag_id)


@dataclass(frozen=True)
class ExclusionSettings:
    """The full persisted tag-exclusion policy.

    ``revision`` is server-owned and monotonic; ``tags`` holds stable tag
    identities. Construction is strict and deduplicates nothing silently:
    duplicate identities raise rather than collapsing to one entry.
    """

    revision: int = 0
    tags: tuple[TagExclusion, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if type(self.revision) is not int or self.revision < 0:
            raise ValueError("revision must be a nonnegative integer")
        entries: list[TagExclusion] = []
        seen: set[tuple[str, str, str]] = set()
        for entry in self.tags:
            if not isinstance(entry, TagExclusion):
                raise ValueError("tags must contain TagExclusion entries")
            key = _sort_key(entry)
            if key in seen:
                raise ValueError("duplicate tag exclusion identity")
            seen.add(key)
            entries.append(entry)
        entries.sort(key=_sort_key)
        object.__setattr__(self, "tags", tuple(entries))

    def as_dict(self) -> dict[str, Any]:
        """The exact persisted JSON shape (sorted, deterministic)."""
        return {
            "version": SCHEMA_VERSION,
            "revision": self.revision,
            "exclusions": {
                "tags": [entry.as_dict() for entry in self.tags],
            },
        }


@dataclass(frozen=True)
class ExclusionSettingsRead:
    """Result of a settings read: the policy plus whether a file existed."""

    settings: ExclusionSettings
    persisted: bool


def settings_path(vault_root: str | Path) -> Path:
    """The versioned JSON settings path beneath the resolved vault root."""
    return Path(vault_root) / SETTINGS_REL_PATH


def lock_path(vault_root: str | Path) -> Path:
    """The lock-file path beneath the resolved vault root."""
    return Path(vault_root) / LOCK_REL_PATH


def _tag_exclusion_from_mapping(value: Any) -> TagExclusion:
    """Build a strict :class:`TagExclusion` from a mapping or entry."""
    if isinstance(value, TagExclusion):
        return value
    if not isinstance(value, dict):
        raise ValueError(f"{value!r} is not a tag exclusion entry")
    if set(value) != _ENTRY_KEYS:
        raise ValueError("tag exclusion entries have unknown or missing keys")
    return TagExclusion(
        source=value["source"],
        space_id=value["space_id"],
        tag_id=value["tag_id"],
    )


# ---------------------------------------------------------------------------
# Strict decoding
# ---------------------------------------------------------------------------

def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """``object_pairs_hook`` that rejects duplicate keys at every level."""
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def _decode(data: Any) -> ExclusionSettings:
    """Decode a parsed JSON document into strict settings or raise.

    Raises :class:`ExclusionSettingsFormatError` for any unknown/missing key,
    unknown exclusion dimension, strict type violation, unsupported version,
    or invalid tag identity.
    """
    if not isinstance(data, dict):
        raise ExclusionSettingsFormatError("exclusion settings must be a JSON object")
    if set(data) != _TOP_KEYS:
        raise ExclusionSettingsFormatError(
            "exclusion settings has unknown or missing top-level keys"
        )
    version = data["version"]
    if type(version) is not int or version != SCHEMA_VERSION:
        raise ExclusionSettingsFormatError(
            f"exclusion settings version {version!r} is unsupported"
        )
    revision = data["revision"]
    if type(revision) is not int or revision < 0:
        raise ExclusionSettingsFormatError(
            "exclusion settings revision must be a nonnegative integer"
        )

    exclusions = data["exclusions"]
    if not isinstance(exclusions, dict) or set(exclusions) != EXCLUSION_DIMENSIONS:
        raise ExclusionSettingsFormatError(
            "exclusion settings has an unknown or missing exclusion dimension"
        )
    tags_raw = exclusions["tags"]
    if not isinstance(tags_raw, list):
        raise ExclusionSettingsFormatError(
            "exclusion settings tags must be a list"
        )
    tags: list[TagExclusion] = []
    for row in tags_raw:
        try:
            tags.append(_tag_exclusion_from_mapping(row))
        except ValueError as exc:
            raise ExclusionSettingsFormatError(
                "exclusion settings has an invalid tag exclusion"
            ) from exc
    try:
        return ExclusionSettings(revision=revision, tags=tuple(tags))
    except ValueError as exc:
        raise ExclusionSettingsFormatError(
            "exclusion settings is malformed"
        ) from exc


def _load_strict(path: Path) -> ExclusionSettings:
    """Strict read of an existing file. Raises on malformed/unsupported data
    or I/O failure — never repairs, defaults, or erases."""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ExclusionSettingsStoreError(
            "exclusion settings storage is unreadable"
        ) from exc
    try:
        data = json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
    except ValueError as exc:
        raise ExclusionSettingsFormatError(
            "exclusion settings file is malformed"
        ) from exc
    return _decode(data)


def read_settings(vault_root: str | Path) -> ExclusionSettingsRead:
    """Read the vault-scoped tag-exclusion settings.

    A missing file returns the default (empty) policy with ``persisted=False``
    and creates nothing. Malformed/unsupported storage raises
    :class:`ExclusionSettingsFormatError`; an unreadable file raises
    :class:`ExclusionSettingsStoreError`. Reads never write.
    """
    path = settings_path(vault_root)
    try:
        exists = os.path.lexists(path)
    except (OSError, TypeError, ValueError) as exc:  # pragma: no cover — defensive
        raise ExclusionSettingsStoreError(
            "exclusion settings storage is unreadable"
        ) from exc
    if not exists:
        return ExclusionSettingsRead(settings=ExclusionSettings(), persisted=False)
    return ExclusionSettingsRead(settings=_load_strict(path), persisted=True)


# ---------------------------------------------------------------------------
# Locking + atomic write
# ---------------------------------------------------------------------------
# Per-vault-root process-local RMW locks (same single-process convention as
# ``capacities_settings`` / ``runstate``); the flock on the vault-scoped lock
# file adds cross-process serialization for the same bytes.

_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _store_lock(vault_root: str | Path) -> threading.Lock:
    key = str(Path(vault_root).resolve())
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.Lock())


def _acquire_lock_file(vault_root: str | Path) -> Any:
    """Advisory exclusive flock on the vault-scoped lock file. Returns the
    open file handle (released on close). Raises on failure — callers fail
    closed with no settings mutation."""
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


def _atomic_write_json(path: str | Path, data: dict[str, Any]) -> None:
    """Write ``data`` atomically: a unique temp file in the same directory,
    flushed and fsynced, then ``os.replace``. Never leaves a torn file and
    never uses a shared temp name."""
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


def save_settings(
    vault_root: str | Path,
    *,
    expected_revision: int,
    exclusions: Any = (),
) -> ExclusionSettings:
    """Explicitly replace the whole tag-exclusion policy.

    The full editable policy is supplied (full replacement), while ``version``
    and ``revision`` stay server-owned: the saved revision is the stored
    revision plus one. Inputs are validated BEFORE any file access.

    Under the per-vault lock the current file is read, its revision compared
    with ``expected_revision``, and only then is the file replaced atomically.
    A stale revision raises :class:`ExclusionSettingsConflictError`; malformed
    storage raises :class:`ExclusionSettingsFormatError`; both preserve the
    original bytes. Lock and write failures raise and likewise leave the bytes
    untouched.
    """
    if type(expected_revision) is not int or expected_revision < 0:
        raise ValueError("expected_revision must be a nonnegative integer")
    if isinstance(exclusions, (str, bytes)):
        raise ValueError("exclusions must be a collection of tag entries")
    entries: list[TagExclusion] = []
    seen: set[tuple[str, str, str]] = set()
    for value in exclusions:
        entry = _tag_exclusion_from_mapping(value)
        key = _sort_key(entry)
        if key in seen:
            raise ValueError("duplicate tag exclusion identity")
        seen.add(key)
        entries.append(entry)

    root = Path(vault_root)
    with _store_lock(root):
        fh = _acquire_lock_file(root)
        try:
            current = read_settings(root).settings
            if current.revision != expected_revision:
                raise ExclusionSettingsConflictError(
                    "tag exclusion settings changed since they were read "
                    f"(stored revision {current.revision}, expected "
                    f"{expected_revision})"
                )
            saved = ExclusionSettings(
                revision=current.revision + 1,
                tags=tuple(entries),
            )
            _atomic_write_json(settings_path(root), saved.as_dict())
        finally:
            _release_lock_file(fh)
    return saved
