"""capacities_builder.py — production factory for the Capacities source adapter.

This module is the ONLY place that reads the host-local Capacities credential
and the vault-local structural mapping record, then assembles a
:class:`capacities_adapter.CapacitiesAdapter`. It preserves the documented
separation in ``capacities_adapter``: the adapter itself never reads vault files
or credentials — the caller supplies both, and this builder is that caller.

Two persisted surfaces feed the factory:

1. The vault-local, versioned structural mapping record::

       00 - META/Cache/tdtb-capacities-source.json

   It maps each Capacities structure to an explicit ``StructureMapping`` and
   names the Capacities ``space_id``. It mirrors the established store pattern
   (``capacities_settings``): strict validation, an explicit schema ``version``,
   a ``revision`` field, atomic write (unique same-directory temp + flush/fsync
   + ``os.replace``), and fail-closed reads that never mutate the offending
   bytes.

2. The host-local credential slot owned by ``shadow.TOKEN_ENV_PATH``
   (``~/.config/tdtb/env``), the SAME file the Todoist token lives in. The
   path constant is imported rather than duplicated so the two credentials
   cannot drift.

A third persisted surface is cache state, not configuration: the durable
object-content cache at
``~/.config/tdtb/tdtb-capacities-content-cache.json``. It is machine-local so
raw task content never enters a synced or backed-up vault, is namespaced by
vault root + space + provider base URL, stores raw content with the
wall-clock UTC fetch time (never assignment decisions, and never a monotonic
reading), and is treated as disposable and silently replaced when unusable.

Error semantics are the point of this slice: an ABSENT mapping record means
"Capacities is not configured" and the factory returns ``None`` silently — a
user who does not use Capacities must see no warning. A PRESENT but broken
record or credential raises visibly, because a configured source with a broken
credential must never be silently empty.

The factory makes no network calls: the transport is injectable so tests can
exercise the whole path with a deterministic fake.
"""
from __future__ import annotations

import json
import math
import os
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator

import app_config
import capacities_cache_io
import capacities_refresh_state
import runstate
from capacities_adapter import (
    CapacitiesAdapter,
    CapacitiesConfig,
    CapacitiesContractError,
    CapacitiesRestClient,
    StructureMapping,
    _definitions,
    _structure_id,
    _text,
)
from capacities_settings import read_settings
from capacities_structure_titles import remember_titles
# The host-local credential slot is owned by shadow.py; import it so the
# Todoist and Capacities credentials cannot diverge.
from shadow import TOKEN_ENV_PATH

# ---------------------------------------------------------------------------
# Paths, schema, and credential constants
# ---------------------------------------------------------------------------
# S1: the record lives at ``<app home>/state/capacities-source.json``
# (``app_config.state_dir()``) and its lock beside it. The ``*_REL_PATH``
# constants below are the frozen pre-S1 vault locations, kept only as the
# read-only fallback and as the migration tool's source of truth. The temp
# name is unique per write (mkstemp), so it cannot collide with another
# writer.

STATE_FILENAME = "capacities-source.json"
STATE_LOCK_FILENAME = "capacities-source.lock"
SOURCE_REL_PATH = f"{runstate.CACHE_DIR_REL}/tdtb-capacities-source.json"

#: How long a per-object content read stays reusable. The provider allows 30
#: requests per minute; this TTL is what lets consecutive refreshes inside one
#: minute reuse content instead of spending the budget again.
CONTENT_CACHE_TTL_SECONDS = 300.0
#: Durable content-cache bounds. The entry bound matches the in-memory cache;
#: the byte bound caps the serialized machine-local file. Bounds are enforced
#: by pruning expired entries first and then the oldest ones; an individually
#: oversized entry is skipped instead of growing the file past the cap.
CONTENT_CACHE_MAX_ENTRIES = 4096
CONTENT_CACHE_MAX_BYTES = 16 * 1024 * 1024
CONTENT_CACHE_SCHEMA_VERSION = 1
#: Machine-local (never vault-local) so raw task content stays out of any
#: synced or backed-up tree. Same established machine-local root as the
#: credential slot (``shadow.TOKEN_ENV_PATH`` -> ``~/.config/tdtb/env``).
DEFAULT_CONTENT_CACHE_PATH = (
    Path.home() / ".config" / "tdtb" / "tdtb-capacities-content-cache.json"
)
#: S1-style state directory for the direct-refresh store (U1): per-object
#: content plus the complete-generation pointer. Machine-local for the same
#: reason as the legacy cache, but with its own root so the legacy reader's
#: bytes and lifetime stay untouched.
REFRESH_STATE_DIRNAME = "capacities-refresh"
#: Sentinel selecting the factory-managed, namespace-scoped durable cache.
#: Keeping a distinct identity lets an explicit ``None`` (or any test cache)
#: keep its exact current meaning.
_DEFAULT_CONTENT_CACHE = object()
LOCK_REL_PATH = f"{runstate.CACHE_DIR_REL}/tdtb-capacities-source.lock"
SCHEMA_VERSION = 1

#: The shared host-local credential slot (same file as the Todoist token).
DEFAULT_TOKEN_PATH = TOKEN_ENV_PATH
TOKEN_KEY = "CAPACITIES_API_TOKEN"
TOKEN_KEY_ALT = "CAPACITIES_REST_TOKEN"

CAPACITIES_BASE_URL = "https://api.capacities.io"
DEFAULT_MAX_PAGES = 20
DEFAULT_TIMEOUT = 10.0

_TOP_KEYS = frozenset({"version", "revision", "space_id", "structures"})
_STRUCTURE_KEYS = frozenset(
    {
        "structure_id",
        "title_property",
        "status_property",
        "open_status_values",
        "date_property",
        "deadline_property",
        "duration_property",
        "assignment_property",
        "assignment_values",
        "completion_property",
        "completion_value",
    }
)
#: structure_id is mandatory; title_property defaults to "title" (the
#: StructureMapping default) when omitted.
_STRUCTURE_REQUIRED_KEYS = frozenset({"structure_id"})
_PROPERTY_NAME_KEYS = (
    "title_property",
    "status_property",
    "date_property",
    "deadline_property",
    "duration_property",
    "assignment_property",
    "completion_property",
)
_VALUE_LIST_KEYS = ("open_status_values", "assignment_values")


class CapacitiesTokenError(Exception):
    """The Capacities credential file is missing, unreadable, loosely
    permissioned, or lacks the expected key. The message never carries the
    token value or an absolute machine path."""


class CapacitiesSourceStoreError(Exception):
    """Capacities structural mapping storage could not be read or written.
    Callers fail closed and preserve the existing durable bytes."""


class CapacitiesSourceFormatError(CapacitiesSourceStoreError):
    """Existing Capacities mapping storage is malformed or unsupported.

    Raised for an unparseable file, duplicate JSON keys, unknown/missing keys,
    strict-type violations, invalid ids, duplicate structure ids, an empty
    ``structures`` list, or an unsupported version. Callers must never
    overwrite the offending bytes."""


class CapacitiesSourceConflictError(CapacitiesSourceStoreError):
    """The caller's ``expected_revision`` is stale — a concurrent save won.

    Carries both revisions so the caller can answer with them. The message
    stays bounded and names neither a path nor any payload content.
    """

    def __init__(
        self, *, expected_revision: int, current_revision: int
    ) -> None:
        self.expected_revision = expected_revision
        self.current_revision = current_revision
        super().__init__(
            "capacities source record changed since it was read "
            f"(stored revision {current_revision}, expected "
            f"{expected_revision})"
        )


# ---------------------------------------------------------------------------
# Credential read
# ---------------------------------------------------------------------------

def load_capacities_token(path: str | Path = DEFAULT_TOKEN_PATH) -> str:
    """Parse KEY=VALUE lines from ``path`` and return the Capacities API token.

    Mirrors :func:`todoist_client.load_token`: blank lines and ``#`` comments
    are ignored; ``CAPACITIES_API_TOKEN`` is preferred and
    ``CAPACITIES_REST_TOKEN`` is accepted as an alias. A file whose permissions
    are looser than 0600 is rejected — a required security control.

    Raises :class:`CapacitiesTokenError` when the file is missing, unreadable,
    loosely permissioned, or neither key is present. The message never includes
    the token value or an absolute machine path, and a partial/empty token is
    never returned.
    """
    p = Path(path).expanduser()
    if not p.is_file():
        raise CapacitiesTokenError("capacities token file not found")

    try:
        mode = os.stat(p).st_mode & 0o777
    except OSError as exc:
        raise CapacitiesTokenError("capacities token file is unreadable") from exc
    if mode & 0o077:
        raise CapacitiesTokenError(
            f"capacities token file has permissions {oct(mode)}; "
            "expected 0600 or stricter"
        )

    try:
        text = p.read_text(encoding="utf-8")
    except OSError as exc:
        raise CapacitiesTokenError("capacities token file is unreadable") from exc

    values: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()

    token = values.get(TOKEN_KEY) or values.get(TOKEN_KEY_ALT)
    if not token:
        raise CapacitiesTokenError(
            f"{TOKEN_KEY} (or {TOKEN_KEY_ALT}) not found in capacities token file"
        )
    return token


# ---------------------------------------------------------------------------
# Strict model
# ---------------------------------------------------------------------------

def _valid_id(value: Any) -> str:
    """Validate a non-empty, whitespace-free identifier string."""
    if not isinstance(value, str) or not value:
        raise ValueError(f"{value!r} is not a valid id")
    if value != value.strip() or any(char.isspace() for char in value):
        raise ValueError(f"{value!r} is not a valid id")
    return value


def _valid_optional_property(value: Any) -> str | None:
    """Validate an optional property name (``None`` when absent/null)."""
    if value is None:
        return None
    return _valid_id(value)


def _valid_value_list(value: Any) -> tuple[str, ...]:
    """Validate a list of non-empty status/property values.

    Values are compared after normalization (casefold plus collapsed internal
    whitespace), and real Capacities status vocabularies contain multi-word
    names such as ``On Hold``, so a single internal space is allowed. Empty,
    whitespace-only, and leading/trailing-whitespace values are rejected, other
    whitespace characters (tabs, newlines) are rejected, and duplicates are
    rejected after normalization so two spellings of one status cannot both be
    stored.
    """
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{value!r} is not a list of values")
    result: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{item!r} is not a valid value")
        if item != item.strip():
            raise ValueError(f"{item!r} is not a valid value")
        if any(char.isspace() and char != " " for char in item):
            raise ValueError(f"{item!r} is not a valid value")
        normalized = " ".join(item.casefold().split())
        if normalized in seen:
            raise ValueError("value list contains duplicates")
        seen.add(normalized)
        result.append(item)
    return tuple(result)


def _valid_optional_value(value: Any) -> str | None:
    if value is None:
        return None
    return _valid_id(value)


@dataclass(frozen=True)
class SourceStructureRecord:
    """One per-structure mapping record in the persisted source JSON."""

    structure_id: str
    title_property: str = "title"
    status_property: str | None = None
    open_status_values: tuple[str, ...] = ()
    date_property: str | None = None
    deadline_property: str | None = None
    duration_property: str | None = None
    assignment_property: str | None = None
    assignment_values: tuple[str, ...] = ()
    completion_property: str | None = None
    completion_value: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "structure_id", _valid_id(self.structure_id))
        object.__setattr__(
            self, "title_property", _valid_id(self.title_property)
        )
        for name in (
            "status_property",
            "date_property",
            "deadline_property",
            "duration_property",
            "assignment_property",
            "completion_property",
        ):
            object.__setattr__(
                self, name, _valid_optional_property(getattr(self, name))
            )
        object.__setattr__(
            self, "completion_value", _valid_optional_value(self.completion_value)
        )
        for name in _VALUE_LIST_KEYS:
            object.__setattr__(
                self, name, _valid_value_list(getattr(self, name))
            )

    def to_structure_mapping(self) -> StructureMapping:
        """Bridge to the adapter's mapping record.

        The record's ``status_property`` is the adapter's
        ``open_status_property`` — one mapped label property supplies both the
        open/closed safety signal and the native Auto status token.
        """
        return StructureMapping(
            structure_id=self.structure_id,
            assignment_property=self.assignment_property,
            assignment_values=frozenset(self.assignment_values),
            title_property=self.title_property,
            date_property=self.date_property,
            deadline_property=self.deadline_property,
            open_status_property=self.status_property,
            open_status_values=frozenset(self.open_status_values),
            duration_property=self.duration_property,
            completion_property=self.completion_property,
            completion_value=self.completion_value,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "structure_id": self.structure_id,
            "title_property": self.title_property,
            "status_property": self.status_property,
            "open_status_values": list(self.open_status_values),
            "date_property": self.date_property,
            "deadline_property": self.deadline_property,
            "duration_property": self.duration_property,
            "assignment_property": self.assignment_property,
            "assignment_values": list(self.assignment_values),
            "completion_property": self.completion_property,
            "completion_value": self.completion_value,
        }


@dataclass(frozen=True)
class SourceRecord:
    """The full persisted Capacities structural mapping record."""

    space_id: str
    structures: tuple[SourceStructureRecord, ...]
    revision: int = 0
    version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if type(self.version) is not int or self.version != SCHEMA_VERSION:
            raise ValueError(f"unsupported version {self.version!r}")
        if type(self.revision) is not int or self.revision < 0:
            raise ValueError("revision must be a nonnegative integer")
        object.__setattr__(self, "space_id", _valid_id(self.space_id))
        structures = tuple(self.structures)
        if not structures:
            raise ValueError("structures must not be empty")
        seen: set[str] = set()
        for record in structures:
            if not isinstance(record, SourceStructureRecord):
                raise ValueError("structures must contain SourceStructureRecord")
            if record.structure_id in seen:
                raise ValueError(
                    f"duplicate structure id {record.structure_id!r}"
                )
            seen.add(record.structure_id)
        object.__setattr__(self, "structures", structures)

    def to_mappings(self) -> tuple[StructureMapping, ...]:
        return tuple(record.to_structure_mapping() for record in self.structures)

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "revision": self.revision,
            "space_id": self.space_id,
            "structures": [record.as_dict() for record in self.structures],
        }


def source_path(vault_root: str | Path | None = None) -> Path:
    """The active store path: ``<app home>/state/capacities-source.json``.

    ``vault_root`` is accepted for call-site compatibility and deliberately
    ignored — the record is machine-local after S1 and derives only from
    ``app_config.state_dir()``, never from the vault."""
    return app_config.state_dir() / STATE_FILENAME


def legacy_source_path(vault_root: str | Path) -> Path:
    """The frozen pre-S1 vault path — read-only fallback."""
    return Path(vault_root) / SOURCE_REL_PATH


def lock_path(vault_root: str | Path | None = None) -> Path:
    """The active lock-file path, beside the record in the app home.

    A 0-byte runtime artifact created on demand; never migrated."""
    return app_config.state_dir() / STATE_LOCK_FILENAME


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


def _decode_structure(row: Any) -> SourceStructureRecord:
    if not isinstance(row, dict):
        raise CapacitiesSourceFormatError(
            "capacities source structure record must be a JSON object"
        )
    if set(row) - _STRUCTURE_KEYS or not _STRUCTURE_REQUIRED_KEYS <= set(row):
        raise CapacitiesSourceFormatError(
            "capacities source structure record has unknown or missing keys"
        )
    try:
        kwargs: dict[str, Any] = {
            "structure_id": row["structure_id"],
            "title_property": row.get("title_property", "title"),
        }
        for name in (
            "status_property",
            "date_property",
            "deadline_property",
            "duration_property",
            "assignment_property",
            "completion_property",
            "completion_value",
        ):
            kwargs[name] = row.get(name)
        for name in _VALUE_LIST_KEYS:
            kwargs[name] = row.get(name, [])
        return SourceStructureRecord(**kwargs)
    except (ValueError, TypeError) as exc:
        raise CapacitiesSourceFormatError(
            "capacities source structure record is malformed"
        ) from exc


def _decode(data: Any) -> SourceRecord:
    """Decode a parsed JSON document into a strict record or raise."""
    if not isinstance(data, dict):
        raise CapacitiesSourceFormatError(
            "capacities source record must be a JSON object"
        )
    if set(data) != _TOP_KEYS:
        raise CapacitiesSourceFormatError(
            "capacities source record has unknown or missing top-level keys"
        )
    version = data["version"]
    if type(version) is not int or version != SCHEMA_VERSION:
        raise CapacitiesSourceFormatError(
            f"capacities source record version {version!r} is unsupported"
        )
    revision = data["revision"]
    if type(revision) is not int or revision < 0:
        raise CapacitiesSourceFormatError(
            "capacities source record revision must be a nonnegative integer"
        )
    structures_raw = data["structures"]
    if not isinstance(structures_raw, list) or not structures_raw:
        raise CapacitiesSourceFormatError(
            "capacities source record structures must be a non-empty list"
        )
    structures = tuple(_decode_structure(row) for row in structures_raw)
    try:
        return SourceRecord(
            space_id=data["space_id"],
            structures=structures,
            revision=revision,
            version=version,
        )
    except ValueError as exc:
        raise CapacitiesSourceFormatError(
            "capacities source record is malformed"
        ) from exc


def _load_strict(path: Path) -> SourceRecord:
    """Strict read of an existing file. Raises on malformed/unsupported data
    or I/O failure — never repairs, defaults, or erases."""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CapacitiesSourceStoreError(
            "capacities source record is unreadable"
        ) from exc
    try:
        data = json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
    except ValueError as exc:
        raise CapacitiesSourceFormatError(
            "capacities source record file is malformed"
        ) from exc
    return _decode(data)


def parse_source_json(raw: str | bytes) -> Any:
    """Parse JSON text with duplicate-key rejection at every level.

    The same rule the file store applies to persisted bytes, exposed for the
    HTTP boundary so a duplicate key in a request is rejected before any
    route validation runs.
    """
    return json.loads(raw, object_pairs_hook=_reject_duplicate_keys)


def decode_source_payload(*, space_id: Any, structures: Any) -> SourceRecord:
    """Strictly decode the editable fields of a save request.

    ``version`` and ``revision`` are server-owned and absent from a payload;
    the returned record therefore carries the default version and revision
    ``0`` (``save_source`` supplies the stored revision plus one). Structure
    rows pass through the exact row decoder the file store applies on read,
    and the record through :class:`SourceRecord`'s strict constructor, so the
    accepted request shape cannot drift from the stored shape. Raises
    :class:`CapacitiesSourceFormatError` for any invalid payload.
    """
    if not isinstance(structures, (list, tuple)):
        raise CapacitiesSourceFormatError(
            "capacities source structures must be a JSON list"
        )
    decoded = tuple(_decode_structure(row) for row in structures)
    try:
        return SourceRecord(space_id=space_id, structures=decoded)
    except ValueError as exc:
        raise CapacitiesSourceFormatError(
            "capacities source record is malformed"
        ) from exc


def read_source(vault_root: str | Path) -> SourceRecord | None:
    """Read the app-owned Capacities structural mapping record.

    A missing file returns ``None`` WITHOUT creating anything — that is the
    silent, opt-in "Capacities is not configured" signal. Malformed/unsupported
    storage raises :class:`CapacitiesSourceFormatError`; an unreadable file
    raises :class:`CapacitiesSourceStoreError`. Reads never write.

    S1: the state-dir record wins; the frozen vault copy is the read-only
    fallback when no state file exists yet.
    """
    try:
        path = next(
            (p for p in (source_path(), legacy_source_path(vault_root))
             if os.path.lexists(p)),
            None,
        )
    except (OSError, TypeError, ValueError) as exc:  # pragma: no cover — defensive
        raise CapacitiesSourceStoreError(
            "capacities source record is unreadable"
        ) from exc
    if path is None:
        return None
    return _load_strict(path)


# ---------------------------------------------------------------------------
# Locking + atomic write
# ---------------------------------------------------------------------------
# The primitives live in ``capacities_cache_io`` so cache modules imported by
# this builder can share them without an import cycle. These wrappers keep
# every existing caller and test name working unchanged; the behaviour lives
# in exactly one place.
#
# Per-vault-root process-local locks (same single-process convention as
# ``capacities_settings``); the flock on the app-home lock file adds
# cross-process serialization for the same bytes.


def _store_lock(vault_root: str | Path) -> threading.Lock:
    return capacities_cache_io.store_lock(vault_root)


def _acquire_path_lock(path: str | Path) -> Any:
    """Open ``path`` and take the POSIX advisory lock (no-op off POSIX)."""
    return capacities_cache_io.acquire_path_lock(path)


def _acquire_lock_file(vault_root: str | Path) -> Any:
    return _acquire_path_lock(lock_path())


def _release_lock_file(fh: Any) -> None:
    capacities_cache_io.release_lock_file(fh)


def _atomic_write_json(path: str | Path, data: dict[str, Any]) -> None:
    """Write ``data`` atomically: a unique temp file in the same directory,
    flushed and fsynced, then ``os.replace``."""
    capacities_cache_io.atomic_write_json(path, data)


def save_source(
    vault_root: str | Path,
    *,
    expected_revision: int,
    space_id: str,
    structures: Iterable[SourceStructureRecord],
) -> SourceRecord:
    """Explicitly replace the whole Capacities structural mapping record.

    The full record is supplied; ``version`` and ``revision`` stay
    server-owned (the saved revision is the stored revision plus one). Inputs
    are validated BEFORE any file access. A stale ``expected_revision`` raises
    :class:`CapacitiesSourceConflictError` (a
    :class:`CapacitiesSourceStoreError`) and preserves the original bytes; an
    absent file is treated as revision ``0``.
    """
    if type(expected_revision) is not int or expected_revision < 0:
        raise ValueError("expected_revision must be a nonnegative integer")
    candidate = SourceRecord(space_id=space_id, structures=tuple(structures))

    root = Path(vault_root)
    with _store_lock(root):
        fh = _acquire_lock_file(root)
        try:
            current = read_source(root)
            current_revision = current.revision if current is not None else 0
            if current_revision != expected_revision:
                raise CapacitiesSourceConflictError(
                    expected_revision=expected_revision,
                    current_revision=current_revision,
                )
            saved = SourceRecord(
                space_id=candidate.space_id,
                structures=candidate.structures,
                revision=current_revision + 1,
            )
            _atomic_write_json(source_path(), saved.as_dict())
        finally:
            _release_lock_file(fh)
    return saved


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

class _ContentCache:
    """TTL cache for per-object content reads, with optional durable backing.

    The provider allows 30 requests per minute and its structure listing
    carries no typed properties, so every property-based decision costs a
    per-object content read. Without a cache, two refreshes inside the same
    minute exhaust the budget and the second read degrades — even though
    nothing changed. Entries expire so a planning surface still converges on
    edits within the TTL rather than serving stale content indefinitely.

    With ``persist_path`` + ``namespace`` the cache also survives a process
    restart: successful inserts are serialized to a versioned, machine-local
    JSON document through the same atomic-write primitive as the structural
    mapping record. Freshness is anchored to the wall-clock UTC epoch of the
    actual fetch, never to a monotonic reading: ``time.monotonic`` does not
    survive a restart, so persisting it would make every entry read as
    expired. On load an entry is accepted only when ``0 <= age < ttl``; the
    in-memory deadline is derived from the remaining lifetime, and both the
    absolute age and that monotonic deadline are checked while the entry is in
    use. A restart, a cache hit, or a disk rewrite therefore never renews an
    entry's freshness.

    Diagnostics are recorded once each and drained by the adapter into its
    read warnings, so a degraded cache is visible without ever embedding
    content, credentials, or machine paths.
    """

    def __init__(
        self,
        ttl_seconds: float,
        max_entries: int = CONTENT_CACHE_MAX_ENTRIES,
        *,
        persist_path: str | Path | None = None,
        namespace: str | None = None,
        max_bytes: int = CONTENT_CACHE_MAX_BYTES,
        monotonic_clock: Callable[[], float] = time.monotonic,
        epoch_clock: Callable[[], float] = time.time,
    ) -> None:
        if persist_path is not None and not namespace:
            raise ValueError("a durable content cache requires a namespace")
        self._ttl = float(ttl_seconds)
        self._max_entries = int(max_entries)
        self._max_bytes = int(max_bytes)
        self._persist_path = (
            Path(persist_path) if persist_path is not None else None
        )
        self._namespace = str(namespace) if namespace else ""
        self._monotonic_clock = monotonic_clock
        self._epoch_clock = epoch_clock
        # object_id -> (fetched_at UTC epoch, monotonic deadline, raw content)
        self._entries: dict[str, tuple[float, float, dict[str, Any]]] = {}
        self._lock = threading.Lock()
        self._persist_lock = threading.Lock()
        self._warnings: list[str] = []
        self._warning_keys: set[str] = set()
        self._warn_lock = threading.Lock()
        if self._persist_path is not None:
            self._merge_entries(self._read_disk_entries())

    # -- reads ----------------------------------------------------------

    def get(self, object_id: str) -> dict[str, Any] | None:
        now_mono = self._monotonic_clock()
        now_epoch = self._epoch_clock()
        with self._lock:
            entry = self._entries.get(object_id)
            if entry is None:
                return None
            fetched_at, deadline, content = entry
            if not self._is_fresh(fetched_at, deadline, now_epoch, now_mono):
                self._entries.pop(object_id, None)
                return None
            return content

    def _is_fresh(
        self, fetched_at: float, deadline: float, now_epoch: float, now_mono: float
    ) -> bool:
        age = now_epoch - fetched_at
        # Reject future timestamps (clock moved backwards, or a tampered file)
        # and anything at/over the TTL. The monotonic deadline derived from the
        # remaining lifetime guards the in-use window when the wall clock moves.
        if age < 0.0 or age >= self._ttl:
            return False
        return now_mono < deadline

    # -- writes ---------------------------------------------------------

    def put(self, object_id: str, content: dict[str, Any]) -> None:
        now_epoch = self._epoch_clock()
        now_mono = self._monotonic_clock()
        with self._persist_lock:
            with self._lock:
                if len(self._entries) >= self._max_entries:
                    self._prune(now_epoch, now_mono)
                self._entries[object_id] = (
                    now_epoch,
                    now_mono + self._ttl,
                    content,
                )
            if self._persist_path is not None:
                self._persist(now_epoch, now_mono)

    def _prune(self, now_epoch: float, now_mono: float) -> None:
        expired = [
            key
            for key, (fetched_at, deadline, _) in self._entries.items()
            if not self._is_fresh(fetched_at, deadline, now_epoch, now_mono)
        ]
        for key in expired:
            self._entries.pop(key, None)
        if len(self._entries) >= self._max_entries:
            # Bound memory even when everything is still fresh: drop the
            # oldest half rather than growing without limit.
            ordered = sorted(self._entries.items(), key=lambda item: item[1][0])
            for key, _ in ordered[: max(1, len(ordered) // 2)]:
                self._entries.pop(key, None)

    # -- durability -----------------------------------------------------

    def _persist(self, now_epoch: float, now_mono: float) -> None:
        """Read/merge/prune/write under the content-cache lock.

        The on-disk document is read back and merged so a writer working from
        a stale snapshot (another process, or a request that raced) cannot
        clobber newer progress. A failure keeps the prior file and the working
        in-memory cache, and reports that restart progress is not durable.
        """
        try:
            with self._file_lock():
                self._merge_entries(self._read_disk_entries())
                document = self._document(now_epoch, now_mono)
                if self._serialized_size(document["entries"]) > self._max_bytes:
                    # Only reachable when the document frame alone exceeds the
                    # cap; never write over it.
                    self._warn(
                        "persist-oversize",
                        "Capacities content cache: could not persist progress "
                        "because the size limit is smaller than the cache "
                        "document; restart progress is not durable, but cached "
                        "reads still work in this process.",
                    )
                    return
                _atomic_write_json(self._persist_path, document)
        except Exception as exc:  # noqa: BLE001 — cache IO must never break a read
            self._warn(
                "persist-failed",
                "Capacities content cache: could not persist progress "
                f"({type(exc).__name__}); restart progress is not durable, but "
                "cached reads still work in this process.",
            )

    @contextmanager
    def _file_lock(self) -> Iterator[None]:
        """A dedicated cross-process lock, separate from the store lock."""
        path = self._persist_path.with_name(self._persist_path.name + ".lock")
        handle = _acquire_path_lock(path)
        try:
            yield
        finally:
            _release_lock_file(handle)

    def _merge_entries(
        self, entries: list[tuple[str, float, float, dict[str, Any]]]
    ) -> None:
        if not entries:
            return
        with self._lock:
            for object_id, fetched_at, deadline, content in entries:
                existing = self._entries.get(object_id)
                if existing is not None and existing[0] >= fetched_at:
                    continue
                self._entries[object_id] = (fetched_at, deadline, content)
            if len(self._entries) > self._max_entries:
                ordered = sorted(
                    self._entries.items(), key=lambda item: item[1][0], reverse=True
                )
                for key, _ in ordered[self._max_entries :]:
                    self._entries.pop(key, None)

    def _read_disk_entries(self) -> list[tuple[str, float, float, dict[str, Any]]]:
        """Validated, still-fresh entries from the persisted document.

        Absent file = cold start (no warning). Unusable data (unreadable,
        unsupported version, another namespace, oversized, malformed) is never
        served and is reported once with a sanitized diagnostic; the next
        successful insert atomically replaces it with fresh validated data.
        """
        path = self._persist_path
        try:
            size = path.stat().st_size
        except FileNotFoundError:
            return []
        except OSError as exc:
            self._warn(
                "read-failed",
                "Capacities content cache: could not read the stored cache "
                f"({type(exc).__name__}); restart progress will be rebuilt "
                "from fresh reads.",
            )
            return []
        if size > self._max_bytes:
            self._warn(
                "oversize",
                "Capacities content cache: ignored a stored cache over the "
                "size limit; restart progress will be rebuilt from fresh reads.",
            )
            return []
        try:
            raw = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            self._warn(
                "read-failed",
                "Capacities content cache: could not read the stored cache "
                f"({type(exc).__name__}); restart progress will be rebuilt "
                "from fresh reads.",
            )
            return []
        try:
            document = json.loads(raw)
        except ValueError:
            document = None
        if not isinstance(document, dict):
            self._warn(
                "corrupt",
                "Capacities content cache: ignored an unreadable stored cache; "
                "restart progress will be rebuilt from fresh reads.",
            )
            return []
        if document.get("version") != CONTENT_CACHE_SCHEMA_VERSION:
            self._warn(
                "version",
                "Capacities content cache: ignored a stored cache with an "
                "unsupported format version; restart progress will be rebuilt "
                "from fresh reads.",
            )
            return []
        if document.get("namespace") != self._namespace:
            self._warn(
                "namespace",
                "Capacities content cache: ignored a stored cache for a "
                "different vault or space; restart progress will be rebuilt "
                "from fresh reads.",
            )
            return []
        raw_entries = document.get("entries")
        if not isinstance(raw_entries, list):
            self._warn(
                "corrupt",
                "Capacities content cache: ignored an unreadable stored cache; "
                "restart progress will be rebuilt from fresh reads.",
            )
            return []
        now_epoch = self._epoch_clock()
        now_mono = self._monotonic_clock()
        valid: list[tuple[str, float, float, dict[str, Any]]] = []
        rejected = 0
        for raw_entry in raw_entries:
            parsed = self._validate_entry(raw_entry)
            if parsed is None:
                rejected += 1
                continue
            object_id, fetched_at, content = parsed
            age = now_epoch - fetched_at
            if age < 0.0:
                # A future fetch time is a clock anomaly or tampering, not aging.
                rejected += 1
                continue
            if age >= self._ttl:
                continue  # routinely expired: silence, not a diagnostic
            valid.append(
                (object_id, fetched_at, now_mono + (self._ttl - age), content)
            )
        if rejected:
            self._warn(
                "invalid-entries",
                "Capacities content cache: ignored invalid stored entries; "
                "those objects will be re-read.",
            )
        return valid

    @staticmethod
    def _validate_entry(raw: Any) -> tuple[str, float, dict[str, Any]] | None:
        if not isinstance(raw, dict):
            return None
        object_id = raw.get("object_id")
        if not isinstance(object_id, str) or not object_id.strip():
            return None
        fetched_at = raw.get("fetched_at")
        if isinstance(fetched_at, bool) or not isinstance(fetched_at, (int, float)):
            return None
        fetched_at = float(fetched_at)
        if not math.isfinite(fetched_at):
            return None
        content = raw.get("content")
        if not isinstance(content, dict):
            return None
        return object_id, fetched_at, content

    def _document(self, now_epoch: float, now_mono: float) -> dict[str, Any]:
        with self._lock:
            snapshot = list(self._entries.items())
        fresh = [
            (object_id, fetched_at, content)
            for object_id, (fetched_at, deadline, content) in snapshot
            if self._is_fresh(fetched_at, deadline, now_epoch, now_mono)
        ]
        fresh.sort(key=lambda item: item[1], reverse=True)
        del fresh[self._max_entries :]
        return {
            "version": CONTENT_CACHE_SCHEMA_VERSION,
            "namespace": self._namespace,
            "entries": self._entries_within_byte_cap(fresh),
        }

    def _entries_within_byte_cap(
        self, fresh: list[tuple[str, float, dict[str, Any]]]
    ) -> list[dict[str, Any]]:
        kept: list[dict[str, Any]] = []
        used = self._serialized_size([])
        for object_id, fetched_at, content in fresh:
            entry = {
                "object_id": object_id,
                "fetched_at": fetched_at,
                "content": content,
            }
            blob = json.dumps(entry, indent=2, sort_keys=True).encode("utf-8")
            # Conservative per-entry cost: the nested document adds indentation
            # the top-level serialization lacks, plus the array separator.
            cost = len(blob) + 2 * blob.count(b"\n") + 64
            if used + cost > self._max_bytes:
                # Skip an individually oversized entry instead of letting the
                # file grow without bound; older smaller entries may still fit.
                continue
            kept.append(entry)
            used += cost
        while kept and self._serialized_size(kept) > self._max_bytes:
            if len(kept) == 1:
                kept = []
                break
            kept = kept[: len(kept) // 2]
        return kept

    def _serialized_size(self, entries: list[dict[str, Any]]) -> int:
        document = {
            "version": CONTENT_CACHE_SCHEMA_VERSION,
            "namespace": self._namespace,
            "entries": entries,
        }
        return len(json.dumps(document, indent=2, sort_keys=True).encode("utf-8"))

    # -- diagnostics ----------------------------------------------------

    def _warn(self, key: str, message: str) -> None:
        with self._warn_lock:
            if key in self._warning_keys:
                return
            self._warning_keys.add(key)
            self._warnings.append(message)

    def drain_warnings(self) -> list[str]:
        """Return (and clear) diagnostics recorded since the last drain."""
        with self._warn_lock:
            drained = list(self._warnings)
            self._warnings.clear()
            self._warning_keys.clear()
        return drained


def _content_cache_namespace(
    vault_root: str | Path, space_id: str, base_url: str
) -> str:
    """Stable digest identifying the vault + space + provider scope."""
    return capacities_cache_io.cache_namespace(vault_root, space_id, base_url)


#: Namespace -> shared cache, so every adapter rebuild for the same vault +
#: space + provider reuses the same durable cache instance. The cache must
#: outlive one adapter: every request builds and closes its own adapter.
_CACHE_REGISTRY: dict[str, _ContentCache] = {}
_CACHE_REGISTRY_LOCK = threading.Lock()


def _resolve_content_cache(
    config: CapacitiesBuilderConfig, vault_root: str | Path, space_id: str
) -> Any:
    """Resolve the configured cache seam.

    The default sentinel means "the factory-managed, namespace-scoped durable
    cache"; an explicit instance (or ``None``) is used exactly as given, which
    keeps tests and private caches deterministic.
    """
    if config.content_cache is not _DEFAULT_CONTENT_CACHE:
        return config.content_cache
    namespace = _content_cache_namespace(vault_root, space_id, config.base_url)
    persist_path = (
        Path(config.cache_path)
        if config.cache_path is not None
        else DEFAULT_CONTENT_CACHE_PATH
    )
    with _CACHE_REGISTRY_LOCK:
        cache = _CACHE_REGISTRY.get(namespace)
        if cache is None:
            cache = _ContentCache(
                CONTENT_CACHE_TTL_SECONDS,
                CONTENT_CACHE_MAX_ENTRIES,
                persist_path=persist_path,
                namespace=namespace,
            )
            _CACHE_REGISTRY[namespace] = cache
        return cache


@dataclass(frozen=True)
class CapacitiesBuilderConfig:
    """Explicit, injectable builder configuration.

    ``token_path`` defaults to the shared host-local credential slot; the
    ``transport`` seam lets tests exercise the whole path with a fake provider
    and zero network calls.
    """

    token_path: Path = DEFAULT_TOKEN_PATH
    max_pages: int = DEFAULT_MAX_PAGES
    base_url: str = CAPACITIES_BASE_URL
    timeout: float = DEFAULT_TIMEOUT
    transport: Any = None
    #: Object-content cache. The default sentinel resolves to the
    #: factory-managed namespace-scoped durable cache; an explicit instance or
    #: ``None`` is used as-is so tests can keep reads deterministic.
    content_cache: Any = _DEFAULT_CONTENT_CACHE
    #: Machine-local durable-cache path. ``None`` uses
    #: ``DEFAULT_CONTENT_CACHE_PATH``; tests inject a ``tmp_path``.
    cache_path: Path | None = None
    #: Machine-local direct-refresh store root. ``None`` uses
    #: ``refresh_state_dir()``; tests inject a ``tmp_path``.
    refresh_state_path: Path | None = None


def _resolve_assigned_structures(
    mappings: Iterable[StructureMapping],
    declarations: dict[str, str],
) -> tuple[StructureMapping, ...]:
    """Resolve the settings declaration onto the source mappings.

    This is the only place the two persisted surfaces — the vault-local
    source record and the settings store — meet, so the precedence lives
    here: a structure declared in ``assigned_structures`` reads the declared
    raw property id with ``true`` implied, and every other structure keeps
    the mapping's own ``assignment_property`` / ``assignment_values``
    untouched. A declaration for a structure the source record does not map
    is ignored.

    Resolution sets ONLY ``assigned_property``. ``assignment_property`` stays
    exactly as stored, so the declaration cannot reach the adapter's
    enumeration gate (``_structure_can_contribute``) — the operator chose an
    assignment check only, and a misconfiguration must never drop work from
    the plan.
    """
    if not declarations:
        return tuple(mappings)
    return tuple(
        replace(mapping, assigned_property=declarations[mapping.structure_id])
        if mapping.structure_id in declarations
        else mapping
        for mapping in mappings
    )


def _structure_titles(payload: Any) -> dict[str, str]:
    """Structure id -> nonblank display title from a structures payload.

    Best-effort by construction: anything that is not a usable row is
    silently skipped, because this feeds a disposable cache and must never
    turn into a failure of the read that carried the payload. A blank title
    is dropped rather than stored, so the consumer keeps its ID fallback.
    """
    rows = payload.get("structures") if isinstance(payload, dict) else payload
    if not isinstance(rows, (list, tuple)):
        return {}
    titles: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        structure_id = _structure_id(row)
        title = _text(row.get("title"))
        if structure_id and title:
            titles[structure_id] = title
    return titles


def _structure_title_observer(
    vault_root: str | Path, space_id: str, base_url: str
) -> Callable[[Any], None]:
    """Build the best-effort sink that persists observed structure titles.

    The sink calls ``remember_titles``, which never raises and returns
    ``False`` on any failure, so binding it adds no failure path to a read.
    The namespace is exactly the (vault root, space, base URL) triple the
    client was built for, so one vault, space, or provider URL can never see
    another's titles. It makes no provider call of its own: it only ever
    sees a payload a read already fetched.
    """

    def observe(payload: Any) -> None:
        remember_titles(
            vault_root, space_id, base_url, _structure_titles(payload)
        )

    return observe


def build_capacities_adapter(
    vault_root: str | Path,
    config: CapacitiesBuilderConfig | None = None,
    *,
    transport: Any = None,
) -> CapacitiesAdapter | None:
    """Construct a Capacities adapter from explicit config + host-local slot.

    Order of operations (error semantics are the contract):

    1. Read the vault-local mapping record. ABSENT -> return ``None`` silently;
       a user who does not use Capacities sees no warning.
    2. A PRESENT but malformed/unsupported record raises a bounded error that
       carries no credential and no absolute machine path.
    3. Read the assignment policy via ``capacities_settings`` and pass it as
       ``assignment_settings``.
    4. Read the credential from the host-local slot. A record that exists but
       has no usable token raises — a configured source with a broken
       credential must be visible, not silently empty.
    5. Construct ``CapacitiesRestClient`` and the ``CapacitiesAdapter``.

    No network call is made; the transport is injected.
    """
    cfg = config if config is not None else CapacitiesBuilderConfig()
    record = read_source(vault_root)
    if record is None:
        return None

    settings = read_settings(vault_root).settings
    assignment_settings = settings.to_assignment_settings()
    token = load_capacities_token(cfg.token_path)
    effective_transport = transport if transport is not None else cfg.transport
    content_cache = _resolve_content_cache(cfg, vault_root, record.space_id)

    client = CapacitiesRestClient(
        token,
        space_id=record.space_id,
        base_url=cfg.base_url,
        timeout=cfg.timeout,
        transport=effective_transport,
        content_cache=content_cache,
        structures_observer=_structure_title_observer(
            vault_root, record.space_id, cfg.base_url
        ),
    )
    return CapacitiesAdapter(
        client,
        CapacitiesConfig(
            space_id=record.space_id,
            mappings=_resolve_assigned_structures(
                record.to_mappings(), settings.assigned_structures
            ),
            max_pages=cfg.max_pages,
            assignment_settings=assignment_settings,
            content_cache=content_cache,
        ),
    )


# ---------------------------------------------------------------------------
# Direct-refresh state store (U1)
# ---------------------------------------------------------------------------
# The legacy ``_ContentCache`` above keeps its TTL/bound/merge contract for the
# read path that already shipped. The direct-refresh coordinator (U2) instead
# uses ``capacities_refresh_state``: versioned per-object persistence with no
# TTL, plus a separate atomic complete-generation pointer. These helpers are
# the only composition seam; they add no behavior to the legacy reader.


def refresh_state_dir() -> Path:
    """``<app home>/state/capacities-refresh/`` — the direct-refresh root."""
    return app_config.state_dir() / REFRESH_STATE_DIRNAME


def build_refresh_state(
    vault_root: str | Path,
    space_id: str,
    config: CapacitiesBuilderConfig | None = None,
) -> capacities_refresh_state.RefreshStateStore:
    """Resolve the direct-refresh store for one vault + space + provider.

    Namespacing reuses the same machine-local root convention as the legacy
    cache (``app_config.state_dir()``) but a distinct directory. The provider
    origin — not the full base URL — is the identity component, so a path-only
    base-URL change cannot orphan stored objects.
    """
    cfg = config if config is not None else CapacitiesBuilderConfig()
    root = (
        Path(cfg.refresh_state_path)
        if cfg.refresh_state_path is not None
        else refresh_state_dir()
    )
    return capacities_refresh_state.RefreshStateStore(
        root,
        origin=capacities_refresh_state.provider_origin(cfg.base_url),
        space_id=space_id,
        vault_root=vault_root,
    )


def import_legacy_content_cache(
    store: capacities_refresh_state.RefreshStateStore,
    vault_root: str | Path,
    space_id: str,
    config: CapacitiesBuilderConfig | None = None,
    *,
    object_types: Any,
    legacy_path: str | Path | None = None,
) -> capacities_refresh_state.LegacyImport:
    """Import compatible legacy cache entries into the new store, read-only.

    The legacy namespace digest is computed exactly as
    :func:`_resolve_content_cache` does, so only a document written for this
    vault + space + provider is eligible. ``legacy_path`` defaults to the
    configured cache path (or ``DEFAULT_CONTENT_CACHE_PATH``), matching the
    legacy reader's own resolution.
    """
    cfg = config if config is not None else CapacitiesBuilderConfig()
    if legacy_path is not None:
        path = Path(legacy_path)
    elif cfg.cache_path is not None:
        path = Path(cfg.cache_path)
    else:
        path = DEFAULT_CONTENT_CACHE_PATH
    expected_namespace = _content_cache_namespace(vault_root, space_id, cfg.base_url)
    return capacities_refresh_state.import_legacy_entries(
        store,
        path,
        expected_namespace=expected_namespace,
        object_types=object_types,
    )


# ---------------------------------------------------------------------------
# Structure discovery for the mapping editor
# ---------------------------------------------------------------------------
# The mapping record is operator-owned and hand-edited today. Discovery
# feeds the editor that will replace the hand-editing, so it must work while
# no valid mapping exists: it reads the provider only, normalizes the space's
# structures into the catalog below, and returns nothing raw. Titles and
# property names are display metadata, never identity — every key here is an
# opaque provider id.


@dataclass(frozen=True)
class CatalogLabelOption:
    """One selectable value of a label property, for display only."""

    id: str
    title: str


@dataclass(frozen=True)
class CatalogProperty:
    """One property definition normalized for the mapping editor.

    ``writable`` is true only for a provider value of literal ``true``; an
    absent or non-boolean value reads as not writable, because the editor
    must never offer a write the provider cannot accept.
    """

    property_id: str
    title: str
    type: str
    writable: bool
    label_options: tuple[CatalogLabelOption, ...] = ()


@dataclass(frozen=True)
class CatalogStructure:
    """One Capacities structure and its properties, for the mapping editor."""

    structure_id: str
    title: str
    properties: tuple[CatalogProperty, ...] = ()


def _catalog_label_options(
    definition: dict[str, Any], property_id: str
) -> tuple[CatalogLabelOption, ...]:
    """Normalize a label property's ``labelSet`` in provider order.

    Provider order is the operator's own option order, so it is preserved.
    A missing set is an empty set; a malformed one is a contract failure,
    matching the adapter's own label parsing.
    """
    raw = definition.get("labelSet")
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise CapacitiesContractError(
            f"label property {property_id!r} has malformed labelSet"
        )
    options: list[CatalogLabelOption] = []
    for option in raw:
        if not isinstance(option, dict):
            raise CapacitiesContractError(
                f"label property {property_id!r} has malformed option"
            )
        option_id = _structure_id(option)
        if not option_id:
            raise CapacitiesContractError(
                f"label property {property_id!r} has malformed option"
            )
        options.append(
            CatalogLabelOption(
                id=option_id,
                title=_text(option.get("name")) or option_id,
            )
        )
    return tuple(options)


def _catalog_property(
    property_id: str, definition: dict[str, Any], structure_id: str
) -> CatalogProperty:
    """Normalize one property definition; the catalog promises a typed entry."""
    kind = _text(definition.get("type"))
    if not kind:
        raise CapacitiesContractError(
            f"structure {structure_id!r} property {property_id!r} has no type"
        )
    return CatalogProperty(
        property_id=property_id,
        title=_text(definition.get("name")) or property_id,
        type=kind,
        writable=definition.get("writable") is True,
        label_options=(
            _catalog_label_options(definition, property_id)
            if kind == "label"
            else ()
        ),
    )


def _catalog_structures(payload: Any) -> tuple[CatalogStructure, ...]:
    """Normalize a structures payload into the editor catalog, sorted.

    Works for the current envelope (a dict with a ``structures`` key) and a
    bare list. Provider documents never leave this function: the catalog
    carries only ids, display strings, types, a writability flag, and label
    options. A malformed row, a duplicate structure, or a definition without
    a type is a contract failure, mirroring the adapter's own checks.
    """
    rows = payload.get("structures") if isinstance(payload, dict) else payload
    if not isinstance(rows, (list, tuple)):
        raise CapacitiesContractError(
            "Capacities structures response is malformed"
        )
    catalog: list[CatalogStructure] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict) or not _structure_id(row):
            raise CapacitiesContractError(
                "Capacities structures response has malformed row"
            )
        structure_id = _structure_id(row)
        if structure_id in seen:
            raise CapacitiesContractError(
                f"duplicate Capacities structure {structure_id!r}"
            )
        seen.add(structure_id)
        definitions = _definitions(row)
        catalog.append(
            CatalogStructure(
                structure_id=structure_id,
                title=_text(row.get("title")) or structure_id,
                properties=tuple(
                    _catalog_property(prop_id, definition, structure_id)
                    for prop_id, definition in definitions.items()
                ),
            )
        )
    catalog.sort(key=lambda structure: structure.structure_id)
    return tuple(catalog)


def discover_capacities_source(
    vault_root: str | Path,
    space_id: str,
    config: CapacitiesBuilderConfig | None = None,
) -> tuple[CatalogStructure, ...]:
    """Read the space's structures for the mapping editor.

    Deliberately independent of the vault-local mapping record and the
    assignment settings: the editor exists to build a mapping, so discovery
    has to work while no valid mapping exists. Only the REST client is
    constructed — the full adapter factory requires the mapping and raises
    ``RootTask must be explicitly mapped`` without it. Exactly one structures
    request is made and the client is closed either way. The payload also
    passes through the same best-effort title sink a normal read uses, so an
    explicit discovery refreshes the machine-local title cache.
    """
    cfg = config if config is not None else CapacitiesBuilderConfig()
    token = load_capacities_token(cfg.token_path)
    client = CapacitiesRestClient(
        token,
        space_id=space_id,
        base_url=cfg.base_url,
        timeout=cfg.timeout,
        transport=cfg.transport,
        structures_observer=_structure_title_observer(
            vault_root, space_id, cfg.base_url
        ),
    )
    try:
        payload = client.fetch_structures()
    finally:
        client.close()
    return _catalog_structures(payload)