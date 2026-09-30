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
import os
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

try:
    import fcntl as _fcntl  # POSIX advisory file locks (macOS/Linux)
except ImportError:  # pragma: no cover — non-POSIX fallback
    _fcntl = None

import runstate
from capacities_adapter import (
    CapacitiesAdapter,
    CapacitiesConfig,
    CapacitiesRestClient,
    StructureMapping,
)
from capacities_settings import read_settings
# The host-local credential slot is owned by shadow.py; import it so the
# Todoist and Capacities credentials cannot diverge.
from shadow import TOKEN_ENV_PATH

# ---------------------------------------------------------------------------
# Paths, schema, and credential constants
# ---------------------------------------------------------------------------
# The record lives beneath ``00 - META/Cache`` under the caller-supplied vault
# root — never cwd, env, or a display name. The temp name is unique per write
# (mkstemp), so it cannot collide with another writer.

SOURCE_REL_PATH = f"{runstate.CACHE_DIR_REL}/tdtb-capacities-source.json"
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
    """Validate a list of non-empty, whitespace-free string values."""
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{value!r} is not a list of values")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item:
            raise ValueError(f"{item!r} is not a valid value")
        if item != item.strip() or any(char.isspace() for char in item):
            raise ValueError(f"{item!r} is not a valid value")
        result.append(item)
    if len(set(result)) != len(result):
        raise ValueError("value list contains duplicates")
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


def source_path(vault_root: str | Path) -> Path:
    """The versioned JSON source-record path beneath the resolved vault root."""
    return Path(vault_root) / SOURCE_REL_PATH


def lock_path(vault_root: str | Path) -> Path:
    """The lock-file path beneath the resolved vault root."""
    return Path(vault_root) / LOCK_REL_PATH


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


def read_source(vault_root: str | Path) -> SourceRecord | None:
    """Read the vault-scoped Capacities structural mapping record.

    A missing file returns ``None`` WITHOUT creating anything — that is the
    silent, opt-in "Capacities is not configured" signal. Malformed/unsupported
    storage raises :class:`CapacitiesSourceFormatError`; an unreadable file
    raises :class:`CapacitiesSourceStoreError`. Reads never write.
    """
    path = source_path(vault_root)
    try:
        exists = os.path.lexists(path)
    except (OSError, TypeError, ValueError) as exc:  # pragma: no cover — defensive
        raise CapacitiesSourceStoreError(
            "capacities source record is unreadable"
        ) from exc
    if not exists:
        return None
    return _load_strict(path)


# ---------------------------------------------------------------------------
# Locking + atomic write
# ---------------------------------------------------------------------------
# Per-vault-root process-local locks (same single-process convention as
# ``capacities_settings``); the flock on the vault-scoped lock file adds
# cross-process serialization for the same bytes.

_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _store_lock(vault_root: str | Path) -> threading.Lock:
    key = str(Path(vault_root).resolve())
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.Lock())


def _acquire_lock_file(vault_root: str | Path) -> Any:
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
    :class:`CapacitiesSourceStoreError` and preserves the original bytes; an
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
                raise CapacitiesSourceStoreError(
                    "capacities source record changed since it was read "
                    f"(stored revision {current_revision}, expected "
                    f"{expected_revision})"
                )
            saved = SourceRecord(
                space_id=candidate.space_id,
                structures=candidate.structures,
                revision=current_revision + 1,
            )
            _atomic_write_json(source_path(root), saved.as_dict())
        finally:
            _release_lock_file(fh)
    return saved


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

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

    assignment_settings = (
        read_settings(vault_root).settings.to_assignment_settings()
    )
    token = load_capacities_token(cfg.token_path)
    effective_transport = transport if transport is not None else cfg.transport

    client = CapacitiesRestClient(
        token,
        base_url=cfg.base_url,
        timeout=cfg.timeout,
        transport=effective_transport,
    )
    return CapacitiesAdapter(
        client,
        CapacitiesConfig(
            space_id=record.space_id,
            mappings=record.to_mappings(),
            max_pages=cfg.max_pages,
            assignment_settings=assignment_settings,
        ),
    )