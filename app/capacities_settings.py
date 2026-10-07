"""Versioned local persistence for the TDTB Capacities assignment policy.

This module owns the ONE local, vault-scoped store the Capacities settings API
reads and writes::

    00 - META/Cache/tdtb-capacities-settings.json

It persists only the editable assignment policy — the native Auto toggles,
the stable-identity exclusion set, the custom structures whose ``Active``
status is an inclusion signal, and the two configured admission inputs (which
structures participate in the native Auto rules and which status values
satisfy the native status condition) — plus a server-owned schema ``version``
and monotonic ``revision``. It deliberately persists no titles, source
properties, credentials, structure mappings, or effective-assignment results:
the evaluator in :mod:`capacities_assignment` stays the single source of
truth for decisions.

Contract:

- Missing file is safe: :func:`read_settings` returns the default policy
  (revision ``0``, every native rule enabled, horizon ``2``, no exclusions) and
  ``persisted=False`` WITHOUT creating a file.
- Malformed or unsupported existing storage fails closed: reads raise
  :class:`SettingsFormatError`, and writes refuse while preserving the original
  bytes. Nothing is silently defaulted or erased.
- Exclusion keys must be canonical, stable Capacities identities
  (``capacities:{space}:{structure}:{object}``) accepted by the existing
  evaluator parser. Bare ids, titles, whitespace aliases, non-canonical casing,
  malformed identities, and duplicate JSON keys are rejected.
- Values are strict: booleans are real booleans, and revision/horizon are
  nonnegative integers (bools, floats, and strings are rejected).
- Writes serialize read-modify-write under a per-vault process lock plus an
  advisory ``flock`` on a vault-scoped lock file, then replace atomically
  (unique same-directory temp + flush/fsync + ``os.replace``). A save whose
  ``expected_revision`` no longer matches the stored revision raises
  :class:`SettingsConflictError` and leaves the bytes untouched.

The store is pure Python with no provider, credential, or network access.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import fcntl as _fcntl  # POSIX advisory file locks (macOS/Linux)
except ImportError:  # pragma: no cover — non-POSIX fallback
    _fcntl = None

import runstate
from capacities_assignment import (
    DEFAULT_ACTIVE_STATUSES,
    DEFAULT_DEADLINE_HORIZON_DAYS,
    NATIVE_TASK_STRUCTURES,
    AssignmentSettings,
    parse_capacities_identity,
)

# ---------------------------------------------------------------------------
# Paths and schema constants
# ---------------------------------------------------------------------------
# The cache and lock live beneath ``00 - META/Cache`` under the RESOLVED vault
# root — paths derive only from the caller-supplied vault_root, never from cwd,
# env, or display names. The temp name is unique per write (mkstemp), so it can
# never collide with another writer's runstate/cache temp.

SETTINGS_REL_PATH = f"{runstate.CACHE_DIR_REL}/tdtb-capacities-settings.json"
LOCK_REL_PATH = f"{runstate.CACHE_DIR_REL}/tdtb-capacities-settings.lock"
SCHEMA_VERSION = 1

# ``active_structures``, ``native_task_structures``, and ``active_statuses``
# were added ADDITIVELY to schema version 1, deliberately without bumping
# SCHEMA_VERSION. A version bump would make every already-saved version-1 file
# "unsupported" and fail closed (reads would raise and writes would refuse),
# locking a user out of the settings they already own. Instead each key is
# optional on read — a legacy version-1 file without it means the documented
# default (no custom structure Active-enabled, the built-in native task
# structures, the single ``active`` status) — while every freshly written file
# emits it. Strictness is unchanged: each key, when present, is validated
# exactly.
_ADDITIVE_KEYS = frozenset(
    {"active_structures", "native_task_structures", "active_statuses"}
)
_TOP_KEYS = frozenset(
    {"version", "revision", "native_task_auto", "excluded", "active_structures"}
) | _ADDITIVE_KEYS
_REQUIRED_TOP_KEYS = _TOP_KEYS - _ADDITIVE_KEYS
_NATIVE_KEYS = frozenset(
    {"active_enabled", "due_enabled", "deadline_enabled", "deadline_horizon_days"}
)
_ENABLED_KEYS = ("active_enabled", "due_enabled", "deadline_enabled")


class SettingsStoreError(Exception):
    """Capacities settings storage failure — callers fail closed and preserve
    the existing durable bytes."""


class SettingsFormatError(SettingsStoreError):
    """Existing Capacities settings storage is malformed or unsupported.

    Raised for an unparseable file, duplicate JSON keys, unknown/missing keys,
    strict-type violations, unsupported versions, or invalid exclusion
    identities. Callers must never overwrite the offending bytes."""


class SettingsConflictError(SettingsStoreError):
    """The caller's ``expected_revision`` is stale — a concurrent save won."""


@dataclass(frozen=True)
class NativeTaskAutoPolicy:
    """Default-enabled native ``RootTask``/``Task`` Auto toggles.

    Each toggle gates exactly one native Auto condition; the horizon is the
    inclusive ``deadline <= logical_day + N`` window. Strict construction:
    the toggles must be real booleans and the horizon a nonnegative integer.
    """

    active_enabled: bool = True
    due_enabled: bool = True
    deadline_enabled: bool = True
    deadline_horizon_days: int = DEFAULT_DEADLINE_HORIZON_DAYS

    def __post_init__(self) -> None:
        for name in _ENABLED_KEYS:
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"{name} must be a boolean")
        if type(self.deadline_horizon_days) is not int or self.deadline_horizon_days < 0:
            raise ValueError("deadline_horizon_days must be a nonnegative integer")

    def as_dict(self) -> dict[str, Any]:
        return {
            "active_enabled": self.active_enabled,
            "due_enabled": self.due_enabled,
            "deadline_enabled": self.deadline_enabled,
            "deadline_horizon_days": self.deadline_horizon_days,
        }


@dataclass(frozen=True)
class CapacitiesSettings:
    """The full persisted Capacities assignment policy.

    ``revision`` is server-owned and monotonic; ``excluded`` holds canonical
    stable Capacities identities. Construction is strict: an invalid identity,
    revision, native policy, structure id, status value, or duplicate entry
    raises ``ValueError`` before any I/O.
    """

    revision: int = 0
    native_task_auto: NativeTaskAutoPolicy = field(default_factory=NativeTaskAutoPolicy)
    excluded: frozenset[str] = frozenset()
    #: Custom structures whose ``Active`` typed status label is an inclusion
    #: signal (additive to schema version 1; absence means the empty set).
    active_structures: frozenset[str] = frozenset()
    #: Structures that participate in the native Auto rules (additive to
    #: schema version 1; absence means the built-in native task structures).
    #: An empty set is legitimate: every structure then falls to the custom
    #: path.
    native_task_structures: frozenset[str] = NATIVE_TASK_STRUCTURES
    #: Status values that satisfy the native Auto status condition, stored
    #: exactly as configured — the evaluator normalizes at comparison time, so
    #: the persisted form is never pre-normalized (additive to schema version
    #: 1; absence means ``{"active"}``).
    active_statuses: frozenset[str] = DEFAULT_ACTIVE_STATUSES

    def __post_init__(self) -> None:
        if type(self.revision) is not int or self.revision < 0:
            raise ValueError("revision must be a nonnegative integer")
        if not isinstance(self.native_task_auto, NativeTaskAutoPolicy):
            raise ValueError("native_task_auto must be a NativeTaskAutoPolicy")
        if isinstance(self.active_structures, (str, bytes)):
            raise ValueError("active_structures must be a collection of structure ids")
        object.__setattr__(
            self,
            "excluded",
            frozenset(canonical_exclusion_identity(value) for value in self.excluded),
        )
        object.__setattr__(
            self,
            "active_structures",
            frozenset(
                canonical_active_structure_id(value)
                for value in self.active_structures
            ),
        )
        for name in ("native_task_structures", "active_statuses"):
            values = canonical_unique_admission_values(getattr(self, name), name)
            object.__setattr__(self, name, frozenset(values))

    def as_dict(self) -> dict[str, Any]:
        """The exact persisted JSON shape (sorted, deterministic)."""
        return {
            "version": SCHEMA_VERSION,
            "revision": self.revision,
            "native_task_auto": self.native_task_auto.as_dict(),
            "excluded": {identity: True for identity in sorted(self.excluded)},
            "active_structures": {
                structure_id: True for structure_id in sorted(self.active_structures)
            },
            "native_task_structures": sorted(self.native_task_structures),
            "active_statuses": sorted(self.active_statuses),
        }

    def to_assignment_settings(self) -> AssignmentSettings:
        """Bridge to the pure evaluator's settings seam."""
        return AssignmentSettings(
            excluded_identities=self.excluded,
            deadline_horizon_days=self.native_task_auto.deadline_horizon_days,
            active_enabled=self.native_task_auto.active_enabled,
            due_enabled=self.native_task_auto.due_enabled,
            deadline_enabled=self.native_task_auto.deadline_enabled,
            active_structures=self.active_structures,
            native_task_structures=self.native_task_structures,
            active_statuses=self.active_statuses,
        )


@dataclass(frozen=True)
class SettingsRead:
    """Result of a settings read: the policy plus whether a file existed."""

    settings: CapacitiesSettings
    persisted: bool


def settings_path(vault_root: str | Path) -> Path:
    """The versioned JSON settings path beneath the resolved vault root."""
    return Path(vault_root) / SETTINGS_REL_PATH


def lock_path(vault_root: str | Path) -> Path:
    """The lock-file path beneath the resolved vault root."""
    return Path(vault_root) / LOCK_REL_PATH


def canonical_exclusion_identity(value: Any) -> str:
    """Validate and return a canonical stable Capacities identity.

    Accepts only ``capacities:{space}:{structure}:{object}`` with no
    surrounding whitespace and canonical source casing. A bare id, title,
    whitespace alias, or non-Capacities value raises ``ValueError``.
    """
    text = value if isinstance(value, str) else str(value if value is not None else "")
    parsed = parse_capacities_identity(text)
    if parsed is None or parsed.qualified != text:
        raise ValueError(f"{text!r} is not a canonical Capacities identity")
    return parsed.qualified


def canonical_active_structure_id(value: Any) -> str:
    """Validate and return an Active-enabled custom structure id.

    A structure id must be a non-empty, whitespace-free string so the
    Active-enabled set can never be populated by an empty id, a whitespace
    alias, or a non-string value. Any violation raises ``ValueError``.
    """
    if not isinstance(value, str) or not value:
        raise ValueError(f"{value!r} is not a valid structure id")
    if value != value.strip() or any(char.isspace() for char in value):
        raise ValueError(f"{value!r} is not a valid structure id")
    return value


def canonical_admission_value(value: Any) -> str:
    """Validate and return one configured native structure id or status name.

    The value must be a non-empty string and is kept exactly as given: a
    status name may legitimately contain whitespace (``In Progress``), and the
    evaluator normalizes at comparison time, so the persisted form is never
    pre-normalized. Any other value raises ``ValueError``.
    """
    if not isinstance(value, str) or not value:
        raise ValueError(f"{value!r} is not a valid configured value")
    return value


def canonical_unique_admission_values(value: Any, key: str) -> tuple[str, ...]:
    """Validate an iterable of unique, non-empty strings for one additive key.

    A string/bytes value, a non-iterable, an empty or non-string entry, or a
    duplicate entry raises ``ValueError`` before any file access. Duplicates
    are rejected rather than silently collapsed so a malformed write can never
    masquerade as a valid configuration.
    """
    if isinstance(value, (str, bytes)):
        raise ValueError(f"{key} must be a collection of strings")
    try:
        items = list(value)
    except TypeError:
        raise ValueError(f"{key} must be a collection of strings") from None
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        text = canonical_admission_value(item)
        if text in seen:
            raise ValueError(f"{key} has a duplicate entry {text!r}")
        seen.add(text)
        result.append(text)
    return tuple(result)


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


def _decode(data: Any) -> CapacitiesSettings:
    """Decode a parsed JSON document into strict settings or raise.

    Raises :class:`SettingsFormatError` for any unknown/missing key, strict
    type violation, unsupported version, or invalid exclusion identity.
    """
    if not isinstance(data, dict):
        raise SettingsFormatError("capacities settings must be a JSON object")
    if set(data) - _TOP_KEYS or not _REQUIRED_TOP_KEYS <= set(data):
        raise SettingsFormatError(
            "capacities settings has unknown or missing top-level keys"
        )
    version = data["version"]
    if type(version) is not int or version != SCHEMA_VERSION:
        raise SettingsFormatError(
            f"capacities settings version {version!r} is unsupported"
        )
    revision = data["revision"]
    if type(revision) is not int or revision < 0:
        raise SettingsFormatError(
            "capacities settings revision must be a nonnegative integer"
        )

    native = data["native_task_auto"]
    if not isinstance(native, dict) or set(native) != _NATIVE_KEYS:
        raise SettingsFormatError(
            "capacities settings native_task_auto is malformed"
        )
    enabled: dict[str, bool] = {}
    for key in _ENABLED_KEYS:
        value = native[key]
        if type(value) is not bool:
            raise SettingsFormatError(f"capacities settings {key} must be a boolean")
        enabled[key] = value
    horizon = native["deadline_horizon_days"]
    if type(horizon) is not int or horizon < 0:
        raise SettingsFormatError(
            "capacities settings deadline_horizon_days must be a nonnegative integer"
        )

    excluded_raw = data["excluded"]
    if not isinstance(excluded_raw, dict):
        raise SettingsFormatError("capacities settings excluded must be an object")
    excluded: set[str] = set()
    for key, flag in excluded_raw.items():
        if flag is not True:
            raise SettingsFormatError(
                "capacities settings exclusion flags must be true"
            )
        try:
            excluded.add(canonical_exclusion_identity(key))
        except ValueError as exc:
            raise SettingsFormatError(
                "capacities settings has an invalid exclusion identity"
            ) from exc

    # ``active_structures`` is optional on read: a legacy version-1 file that
    # predates the additive key means the empty set.
    active_raw = data.get("active_structures", {})
    if not isinstance(active_raw, dict):
        raise SettingsFormatError(
            "capacities settings active_structures must be an object"
        )
    active: set[str] = set()
    for key, flag in active_raw.items():
        if flag is not True:
            raise SettingsFormatError(
                "capacities settings active structure flags must be true"
            )
        try:
            active.add(canonical_active_structure_id(key))
        except ValueError as exc:
            raise SettingsFormatError(
                "capacities settings has an invalid active structure id"
            ) from exc

    # ``native_task_structures`` and ``active_statuses`` are optional on read:
    # a legacy version-1 file that predates either additive key means the
    # documented default. A present value is a JSON list of unique, non-empty
    # strings and is validated exactly like every other key.
    native_structures = set(NATIVE_TASK_STRUCTURES)
    if "native_task_structures" in data:
        raw_structures = data["native_task_structures"]
        if not isinstance(raw_structures, list):
            raise SettingsFormatError(
                "capacities settings native_task_structures must be a list"
            )
        try:
            native_structures = set(
                canonical_unique_admission_values(
                    raw_structures, "native_task_structures"
                )
            )
        except ValueError as exc:
            raise SettingsFormatError(
                "capacities settings native_task_structures has an invalid entry"
            ) from exc
    statuses = set(DEFAULT_ACTIVE_STATUSES)
    if "active_statuses" in data:
        raw_statuses = data["active_statuses"]
        if not isinstance(raw_statuses, list):
            raise SettingsFormatError(
                "capacities settings active_statuses must be a list"
            )
        try:
            statuses = set(
                canonical_unique_admission_values(raw_statuses, "active_statuses")
            )
        except ValueError as exc:
            raise SettingsFormatError(
                "capacities settings active_statuses has an invalid entry"
            ) from exc

    return CapacitiesSettings(
        revision=revision,
        native_task_auto=NativeTaskAutoPolicy(
            active_enabled=enabled["active_enabled"],
            due_enabled=enabled["due_enabled"],
            deadline_enabled=enabled["deadline_enabled"],
            deadline_horizon_days=horizon,
        ),
        excluded=frozenset(excluded),
        active_structures=frozenset(active),
        native_task_structures=frozenset(native_structures),
        active_statuses=frozenset(statuses),
    )


def _load_strict(path: Path) -> CapacitiesSettings:
    """Strict read of an existing file. Raises on malformed/unsupported data
    or I/O failure — never repairs, defaults, or erases."""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SettingsStoreError(
            "capacities settings storage is unreadable"
        ) from exc
    try:
        data = json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
    except ValueError as exc:
        raise SettingsFormatError(
            "capacities settings file is malformed"
        ) from exc
    return _decode(data)


def read_settings(vault_root: str | Path) -> SettingsRead:
    """Read the vault-scoped Capacities settings.

    A missing file returns the default-enabled policy with ``persisted=False``
    and creates nothing. Malformed/unsupported storage raises
    :class:`SettingsFormatError`; an unreadable file raises
    :class:`SettingsStoreError`. Reads never write.
    """
    path = settings_path(vault_root)
    try:
        exists = os.path.lexists(path)
    except (OSError, TypeError, ValueError) as exc:  # pragma: no cover — defensive
        raise SettingsStoreError(
            "capacities settings storage is unreadable"
        ) from exc
    if not exists:
        return SettingsRead(settings=CapacitiesSettings(), persisted=False)
    return SettingsRead(settings=_load_strict(path), persisted=True)


# ---------------------------------------------------------------------------
# Locking + atomic write
# ---------------------------------------------------------------------------
# Per-vault-root process-local RMW locks (same single-process convention as
# ``duration_memory`` / ``runstate`` G26); the flock on the vault-scoped lock
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
    native_task_auto: NativeTaskAutoPolicy,
    excluded: Any = (),
    active_structures: Any = (),
    native_task_structures: Any = NATIVE_TASK_STRUCTURES,
    active_statuses: Any = DEFAULT_ACTIVE_STATUSES,
) -> CapacitiesSettings:
    """Explicitly replace the whole Capacities assignment policy.

    The full editable policy is supplied (full replacement), while ``version``
    and ``revision`` stay server-owned: the saved revision is the stored
    revision plus one. Inputs are validated BEFORE any file access.

    The two additive admission inputs default to the documented policy
    (the built-in native task structures and the single ``active`` status), so
    omitting them is a full replacement to that default — exactly like an
    omitted ``active_structures``. An empty ``native_task_structures`` is a
    legitimate configuration: nothing is native and every structure falls to
    the custom path.

    Under the per-vault lock the current file is read, its revision compared
    with ``expected_revision``, and only then is the file replaced atomically.
    A stale revision raises :class:`SettingsConflictError`; malformed storage
    raises :class:`SettingsFormatError`; both preserve the original bytes. Lock
    and write failures raise and likewise leave the bytes untouched.
    """
    if type(expected_revision) is not int or expected_revision < 0:
        raise ValueError("expected_revision must be a nonnegative integer")
    if not isinstance(native_task_auto, NativeTaskAutoPolicy):
        raise ValueError("native_task_auto must be a NativeTaskAutoPolicy")
    if isinstance(active_structures, (str, bytes)):
        raise ValueError("active_structures must be a collection of structure ids")
    identities = frozenset(canonical_exclusion_identity(value) for value in excluded)
    active_ids = frozenset(
        canonical_active_structure_id(value) for value in active_structures
    )
    native_ids = frozenset(
        canonical_unique_admission_values(
            native_task_structures, "native_task_structures"
        )
    )
    status_values = frozenset(
        canonical_unique_admission_values(active_statuses, "active_statuses")
    )

    root = Path(vault_root)
    with _store_lock(root):
        fh = _acquire_lock_file(root)
        try:
            current = read_settings(root).settings
            if current.revision != expected_revision:
                raise SettingsConflictError(
                    "capacities settings changed since they were read "
                    f"(stored revision {current.revision}, expected "
                    f"{expected_revision})"
                )
            saved = CapacitiesSettings(
                revision=current.revision + 1,
                native_task_auto=native_task_auto,
                excluded=identities,
                active_structures=active_ids,
                native_task_structures=native_ids,
                active_statuses=status_values,
            )
            _atomic_write_json(settings_path(root), saved.as_dict())
        finally:
            _release_lock_file(fh)
    return saved
