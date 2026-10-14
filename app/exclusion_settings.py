"""Versioned local persistence for the TDTB exclusion policy.

This module owns the ONE app-owned store the exclusion settings API reads
and writes: ``<app home>/state/exclusions.json`` (``app_config.state_dir()``,
Capacities-first S1); the state dir is the only location.

It persists only the editable policy — stable Capacities tag identities and
persistent Todoist task exclusions the planner must drop before selection —
plus a server-owned schema ``version`` and monotonic ``revision``. It
deliberately persists no source properties and no effective-planning
results: the matchers in :mod:`tag_exclusions` and :mod:`todoist_exclusions`
stay the single source of truth for decisions.

Contract:

- Missing file is safe: :func:`read_settings` returns the default policy
  (revision ``0``, no tag exclusions) and ``persisted=False`` WITHOUT creating
  a file.
- Reads use the state-dir file only.
- Writes always land at the state-dir path.
- The ``.lock`` file is a runtime artifact created fresh at the new location
  and is never migrated.
- Malformed or unsupported existing storage fails closed: reads raise
  :class:`ExclusionSettingsFormatError`, and writes refuse while preserving
  the original bytes. Nothing is silently defaulted or erased.
- The schema carries a closed ``exclusions`` dimension object with
  ``tags`` (required on read; a legacy tags-only file is valid) and
  ``todoist`` (additive to schema version 1, optional on read, always
  emitted on write). Unknown dimensions are rejected, never silently
  accepted — a future dimension must be an explicit schema change.
- Tag identities are stable Capacities identities: ``source`` is exactly
  ``capacities``, ``space_id`` is a non-empty whitespace-free string, and
  ``tag_id`` is a canonical UUID. Titles, ``#``-prefixed display names,
  case variants, and bare ids are never accepted as identity.
- Values are strict: revision is a nonnegative integer (bools, floats, and
  strings are rejected) and duplicate JSON keys are rejected at every level.
- Writes serialize read-modify-write under a process lock plus an advisory
  ``flock`` on the app-home lock file, then replace atomically (unique
  same-directory temp + flush/fsync + ``os.replace``). A save whose
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
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    import fcntl as _fcntl  # POSIX advisory file locks (macOS/Linux)
except ImportError:  # pragma: no cover — non-POSIX fallback
    _fcntl = None

import app_config

# ---------------------------------------------------------------------------
# Paths and schema constants
# ---------------------------------------------------------------------------
# The store lives under ``<app home>/state/`` and its lock beside it.

STATE_FILENAME = "exclusions.json"
STATE_LOCK_FILENAME = "exclusions.lock"
SCHEMA_VERSION = 1

#: The supported exclusion dimensions. The dimension object is a closed
#: schema: adding a dimension is an explicit, reviewed change. ``todoist`` was
#: added ADDITIVELY to schema version 1, deliberately without bumping
#: SCHEMA_VERSION (same precedent as ``capacities_settings``): a version bump
#: would make every already-saved version-1 file "unsupported" and fail
#: closed, locking a user out of settings they already own. A legacy
#: tags-only file reads as an empty ``todoist`` dimension; every freshly
#: written file emits it.
EXCLUSION_DIMENSIONS = frozenset({"tags", "todoist"})
_REQUIRED_DIMENSIONS = frozenset({"tags"})

#: Bounds for the Todoist dimension (the tag dimension predates bounds; the
#: new dimension is bounded on count and on every string).
MAX_TODOIST_EXCLUSIONS = 200
MAX_TODOIST_ID_LENGTH = 128
MAX_TODOIST_NAME_LENGTH = 300
MAX_TODOIST_ADDED_AT_LENGTH = 64

#: The only supported exclusion source today. Identity is stable and
#: source-qualified; a bare title or a ``#``-prefixed display name is never an
#: identity.
EXCLUSION_SOURCES = frozenset({"capacities"})

_TOP_KEYS = frozenset({"version", "revision", "exclusions"})
_ENTRY_KEYS = frozenset({"source", "space_id", "tag_id"})
_TODOIST_ENTRY_KEYS = frozenset({"id", "name", "added_at"})


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


def normalize_todoist_name(value: Any) -> str:
    """Canonical match form of a Todoist task title: casefolded with runs of
    whitespace collapsed to single spaces. Non-strings normalize to ``""`` so
    a malformed row can never match a configured name."""
    if not isinstance(value, str):
        return ""
    return " ".join(value.split()).casefold()


def canonical_todoist_id(value: Any) -> str:
    """Validate and return a bounded, whitespace-free Todoist task id.

    Strictly a string: a numeric JSON id is a different type and is rejected
    rather than coerced."""
    if not isinstance(value, str) or not value:
        raise ValueError(f"{value!r} is not a valid Todoist task id")
    if len(value) > MAX_TODOIST_ID_LENGTH:
        raise ValueError("Todoist task id is too long")
    if value != value.strip() or any(char.isspace() for char in value):
        raise ValueError(f"{value!r} is not a valid Todoist task id")
    return value


def canonical_todoist_name(value: Any) -> str:
    """Validate and return a bounded, non-blank Todoist display title."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{value!r} is not a valid Todoist task name")
    if len(value) > MAX_TODOIST_NAME_LENGTH:
        raise ValueError("Todoist task name is too long")
    return value


def canonical_todoist_added_at(value: Any) -> str:
    """Validate and return an ISO-8601 timestamp string."""
    if not isinstance(value, str) or not value:
        raise ValueError(f"{value!r} is not a valid added_at timestamp")
    if len(value) > MAX_TODOIST_ADDED_AT_LENGTH:
        raise ValueError("added_at timestamp is too long")
    try:
        datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{value!r} is not a valid added_at timestamp") from exc
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


@dataclass(frozen=True)
class TodoistExclusion:
    """One persistent Todoist task exclusion.

    ``id`` is the Todoist task id and ``name`` the display title at add time;
    a recurring series may carry a different id for a later occurrence, which
    is why the title is persisted and matched by normalized-name equality.
    ``added_at`` is the client-supplied ISO-8601 instant the entry was added;
    full-replacement saves carry existing timestamps forward.
    """

    id: str
    name: str
    added_at: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", canonical_todoist_id(self.id))
        object.__setattr__(self, "name", canonical_todoist_name(self.name))
        object.__setattr__(self, "added_at", canonical_todoist_added_at(self.added_at))

    def as_dict(self) -> dict[str, str]:
        return {"id": self.id, "name": self.name, "added_at": self.added_at}


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
    todoist: tuple[TodoistExclusion, ...] = field(default_factory=tuple)

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
        todoist_entries: list[TodoistExclusion] = []
        seen_ids: set[str] = set()
        seen_names: set[str] = set()
        for entry in self.todoist:
            if not isinstance(entry, TodoistExclusion):
                raise ValueError("todoist must contain TodoistExclusion entries")
            if entry.id in seen_ids:
                raise ValueError("duplicate Todoist exclusion id")
            normalized = normalize_todoist_name(entry.name)
            if normalized in seen_names:
                raise ValueError("duplicate Todoist exclusion name")
            seen_ids.add(entry.id)
            seen_names.add(normalized)
            todoist_entries.append(entry)
        todoist_entries.sort(key=lambda e: (e.id, e.name, e.added_at))
        object.__setattr__(self, "todoist", tuple(todoist_entries))

    def as_dict(self) -> dict[str, Any]:
        """The exact persisted JSON shape (sorted, deterministic)."""
        return {
            "version": SCHEMA_VERSION,
            "revision": self.revision,
            "exclusions": {
                "tags": [entry.as_dict() for entry in self.tags],
                "todoist": [entry.as_dict() for entry in self.todoist],
            },
        }


@dataclass(frozen=True)
class ExclusionSettingsRead:
    """Result of a settings read: the policy plus whether a file existed."""

    settings: ExclusionSettings
    persisted: bool


def settings_path() -> Path:
    """The active store path: ``<app home>/state/exclusions.json``.
    The store is machine-local and derives only from
    ``app_config.state_dir()``."""
    return app_config.state_dir() / STATE_FILENAME


def lock_path() -> Path:
    """The active lock-file path, beside the store in the app home.

    A 0-byte runtime artifact created on demand; never migrated."""
    return app_config.state_dir() / STATE_LOCK_FILENAME


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


def _todoist_exclusion_from_mapping(value: Any) -> TodoistExclusion:
    """Build a strict :class:`TodoistExclusion` from a mapping or entry."""
    if isinstance(value, TodoistExclusion):
        return value
    if not isinstance(value, dict):
        raise ValueError(f"{value!r} is not a Todoist exclusion entry")
    if set(value) != _TODOIST_ENTRY_KEYS:
        raise ValueError("Todoist exclusion entries have unknown or missing keys")
    return TodoistExclusion(
        id=value["id"],
        name=value["name"],
        added_at=value["added_at"],
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
    if (
        not isinstance(exclusions, dict)
        or set(exclusions) - EXCLUSION_DIMENSIONS
        or not set(exclusions) >= _REQUIRED_DIMENSIONS
    ):
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
    todoist_raw = exclusions.get("todoist", [])
    if not isinstance(todoist_raw, list):
        raise ExclusionSettingsFormatError(
            "exclusion settings todoist must be a list"
        )
    if len(todoist_raw) > MAX_TODOIST_EXCLUSIONS:
        raise ExclusionSettingsFormatError(
            "exclusion settings has too many Todoist exclusions"
        )
    todoist: list[TodoistExclusion] = []
    for row in todoist_raw:
        try:
            todoist.append(_todoist_exclusion_from_mapping(row))
        except ValueError as exc:
            raise ExclusionSettingsFormatError(
                "exclusion settings has an invalid Todoist exclusion"
            ) from exc
    try:
        return ExclusionSettings(
            revision=revision, tags=tuple(tags), todoist=tuple(todoist)
        )
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


def read_settings() -> ExclusionSettingsRead:
    """Read the app-owned tag-exclusion settings.

    A missing file returns the default (empty) policy with ``persisted=False``
    and creates nothing. Malformed/unsupported storage raises
    :class:`ExclusionSettingsFormatError`; an unreadable file raises
    :class:`ExclusionSettingsStoreError`. Reads never write.

    The state-dir file is the only location.
    """
    try:
        path = settings_path() if os.path.lexists(settings_path()) else None
    except (OSError, TypeError, ValueError) as exc:  # pragma: no cover — defensive
        raise ExclusionSettingsStoreError(
            "exclusion settings storage is unreadable"
        ) from exc
    if path is None:
        return ExclusionSettingsRead(settings=ExclusionSettings(), persisted=False)
    return ExclusionSettingsRead(settings=_load_strict(path), persisted=True)


# ---------------------------------------------------------------------------
# Locking + atomic write
# ---------------------------------------------------------------------------
# Process-local RMW locks (same single-process convention as
# ``capacities_settings`` / ``runstate``); the flock on the app-home lock file
# adds cross-process serialization for the same bytes.

_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _store_lock() -> threading.Lock:
    """The process-local RMW lock. Keyed on the resolved lock-file path; the
    durable bytes are shared machine-wide by ``lock_path()`` and the flock."""
    key = str(lock_path().resolve())
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.Lock())


def _acquire_lock_file() -> Any:
    """Advisory exclusive flock on the app-home lock file. Returns the open
    file handle (released on close). Raises on failure — callers fail closed
    with no settings mutation."""
    path = lock_path()
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
    *,
    expected_revision: int,
    exclusions: Any = (),
    todoist: Any = (),
) -> ExclusionSettings:
    """Explicitly replace the whole exclusion policy (tags + Todoist).

    The full editable policy is supplied (full replacement): ``exclusions``
    carries the tag identities and ``todoist`` the persistent Todoist task
    entries, so a tags-only call clears the Todoist dimension. ``version`` and
    ``revision`` stay server-owned: the saved revision is the stored revision
    plus one. Inputs are validated BEFORE any file access.

    Under the store lock the current file is read, its revision compared with
    ``expected_revision``, and only then is the file replaced atomically. A
    stale revision raises :class:`ExclusionSettingsConflictError`; malformed
    storage raises :class:`ExclusionSettingsFormatError`; both preserve the
    original bytes. Lock and write failures raise and likewise leave the bytes
    untouched. The save lands in the app home.
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
    if isinstance(todoist, (str, bytes)):
        raise ValueError("todoist must be a collection of Todoist entries")
    todoist_entries: list[TodoistExclusion] = []
    seen_ids: set[str] = set()
    seen_names: set[str] = set()
    for value in todoist:
        entry = _todoist_exclusion_from_mapping(value)
        if entry.id in seen_ids:
            raise ValueError("duplicate Todoist exclusion id")
        normalized = normalize_todoist_name(entry.name)
        if normalized in seen_names:
            raise ValueError("duplicate Todoist exclusion name")
        seen_ids.add(entry.id)
        seen_names.add(normalized)
        todoist_entries.append(entry)
    if len(todoist_entries) > MAX_TODOIST_EXCLUSIONS:
        raise ValueError("too many Todoist exclusions")

    with _store_lock():
        fh = _acquire_lock_file()
        try:
            current = read_settings().settings
            if current.revision != expected_revision:
                raise ExclusionSettingsConflictError(
                    "tag exclusion settings changed since they were read "
                    f"(stored revision {current.revision}, expected "
                    f"{expected_revision})"
                )
            saved = ExclusionSettings(
                revision=current.revision + 1,
                tags=tuple(entries),
                todoist=tuple(todoist_entries),
            )
            _atomic_write_json(settings_path(), saved.as_dict())
        finally:
            _release_lock_file(fh)
    return saved
