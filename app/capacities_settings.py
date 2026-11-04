"""Versioned local persistence for the TDTB Capacities assignment policy.

This module owns the ONE app-owned store the Capacities settings API reads and
writes: ``<app home>/state/capacities-settings.json``
(``app_config.state_dir()``, Capacities-first S1); the state dir is the only
location.

Schema version 2 — one declaration per structure
------------------------------------------------
The policy is a single ``types`` document: one entry per Capacities structure
id, each declaring exactly ONE admission mode, so a structure can no longer
carry two contradictory declarations.

::

    { "version": 2, "revision": <int>,
      "statuses": { "open": ["In Progress"], "completed": ["Completed", "Dropped"] },
      "types": { "<structure_id>": { "mode": "off|active|assigned|assignedOn|native",
                                    "property": "<raw property id>" | null,
                                    "statuses": ["<name>"] | null } },
      "native": { "deadline_horizon_days": 14 },
      "excluded": { "<identity>": true } }

Mode semantics (evaluated by :mod:`capacities_assignment`, never here):

- ``off`` never admits.
- ``active`` admits when the status label NAME resolves into the structure's
  open set.
- ``assigned`` admits when the declared boolean property reads ``true``.
- ``assignedOn`` admits when the declared date property's civil day is on or
  before the logical day.
- ``native`` admits on status-open OR due <= logical day OR deadline <=
  logical day + ``native.deadline_horizon_days``.

``types[sid].property`` is REQUIRED iff the mode is ``assigned`` or
``assignedOn`` and FORBIDDEN (a validation error) for every other mode.

``types[sid].statuses`` is optional and deliberately distinguishes three
states: absent/``null`` inherits ``statuses.open``; ``[]`` DISABLES status
admission for that structure (dates/assignment only — the owner's
``RootTask`` decision: tasks stay date-only); a non-empty list is that
structure's own open names.

What else is persisted: the status vocabulary (``statuses.open`` /
``statuses.completed``), the native horizon, and the stable-identity exclusion
set. It deliberately persists no titles, source properties, credentials,
structure mappings, or effective-assignment results: the evaluator in
:mod:`capacities_assignment` stays the single source of truth for decisions.

Contract
--------
- Missing file is safe: :func:`read_settings` returns the default policy
  (revision ``0``, ``statuses.open`` = ``{"active"}``, ``statuses.completed`` =
  ``{"Completed", "Dropped"}``, no type declarations, horizon ``2``, no
  exclusions) and ``persisted=False`` WITHOUT creating a file.
- Reads use the state-dir file only. Writes always land at the state-dir path.
  The ``.lock`` file is a runtime artifact created fresh at the new location
  and is never migrated.
- Malformed or unsupported existing storage fails closed: reads raise
  :class:`SettingsFormatError`, and writes refuse while preserving the original
  bytes. Nothing is silently defaulted or erased.
- Exclusion keys must be canonical, stable Capacities identities
  (``capacities:{space}:{structure}:{object}``) accepted by the existing
  evaluator parser. Bare ids, titles, whitespace aliases, non-canonical casing,
  malformed identities, and duplicate JSON keys are rejected.
- Values are strict: booleans are real booleans, and revision/horizon are
  nonnegative integers (bools, floats, and strings are rejected).
- Writes serialize read-modify-write under a process lock plus an advisory
  ``flock`` on the app-home lock file, then replace atomically (unique
  same-directory temp + flush/fsync + ``os.replace``). A save whose
  ``expected_revision`` no longer matches the stored revision raises
  :class:`SettingsConflictError` and leaves the bytes untouched.

Version 1 migration (lazy on read, rewrite on save)
---------------------------------------------------
Version 1 was extended ADDITIVELY five times (``active_structures``,
``native_task_structures``, ``active_statuses``, ``assigned_structures``,
``assigned_on_structures``) without a version bump, precisely so an already
saved file never became unsupported. Version 2 is the first real shape change,
and it keeps that precedent's promise: an existing version-1 file is NOT
rejected. :func:`_decode` accepts version 1, translates it in memory to the
version-2 shape (``revision`` carried verbatim), and the next
:func:`save_settings` rewrites the file as version 2.

Translation is generic over the version-1 fields — ``native_task_structures``,
``active_structures``, ``assigned_structures``, ``assigned_on_structures``,
``active_statuses``, and ``native_task_auto`` — so any version-1 file migrates
coherently, not just the owner's current one:

- a structure with a source ``assigned`` boolean becomes ``assigned`` (the
  narrowest explicit gate),
- otherwise a structure with an ``assignedOn`` date becomes ``assignedOn``
  (version 1 refused to persist both maps for one structure, so this is only
  reachable in hand-edited bytes),
- otherwise native membership wins as ``native``,
- otherwise an ``Active``-enabled structure becomes ``active``.

Migrated ``native`` declarations carry ``statuses: []``: version 1's three
native toggles collapse into the single ``native`` mode, and the owner's
decision is that native task structures stay date-only (``RootTask`` is never
status-admitted). This is also why a structure that was both native and
declared ``assigned`` in version 1 migrates to ``assigned``: it keeps an
explicit boolean gate and loses its native date rules, per that decision.
``active_statuses`` becomes ``statuses.open`` verbatim and the built-in
completed vocabulary seeds ``statuses.completed``.

Every save that replaces a version-1 file first writes a sibling
``capacities-settings.json.bak-v1-<timestamp>`` byte-for-byte backup of the
previous file (flushed and fsynced BEFORE the atomic replace). A backup
failure aborts the save with the original file intact.

The store is pure Python with no provider, credential, or network access.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import fcntl as _fcntl  # POSIX advisory file locks (macOS/Linux)
except ImportError:  # pragma: no cover — non-POSIX fallback
    _fcntl = None

import app_config
from capacities_assignment import (
    DEFAULT_ACTIVE_STATUSES,
    DEFAULT_DEADLINE_HORIZON_DAYS,
    NATIVE_TASK_STRUCTURES,
    parse_capacities_identity,
)

# ---------------------------------------------------------------------------
# Paths and schema constants
# ---------------------------------------------------------------------------
# The store lives under ``<app home>/state/`` and its lock beside it.

STATE_FILENAME = "capacities-settings.json"
STATE_LOCK_FILENAME = "capacities-settings.lock"
SCHEMA_VERSION = 2
#: The previous on-disk shape. Read-only support: accepted by :func:`_decode`
#: and translated in memory; never written again.
SCHEMA_VERSION_V1 = 1

#: Sibling backup written before a version-1 file is replaced. The infix keeps
#: the backup out of every settings reader (only ``STATE_FILENAME`` is read).
BACKUP_INFIX = ".bak-v1-"

#: The five admission modes. ``off`` is the default for an undeclared
#: structure, so the settings document stays sparse.
TYPE_MODES = frozenset({"off", "active", "assigned", "assignedOn", "native"})
#: Modes whose declaration is meaningless without a raw provider property id.
PROPERTY_MODES = frozenset({"assigned", "assignedOn"})
#: Modes whose admission decision consults the status condition at all. The
#: other modes never admit on status, so a declared ``statuses`` list is
#: persisted and validated but not applicable to them.
STATUS_MODES = frozenset({"active", "native"})

#: ``statuses.open`` default: the single ``active`` label the evaluator has
#: always used (imported from the evaluator so the two can never drift).
DEFAULT_OPEN_STATUSES = DEFAULT_ACTIVE_STATUSES
#: Built-in completed vocabulary. Statuses are stored as configured; the
#: adapter/evaluator normalizes at comparison time.
DEFAULT_COMPLETED_STATUSES = frozenset({"Completed", "Dropped"})

_V2_TOP_KEYS = frozenset(
    {"version", "revision", "statuses", "types", "native", "excluded"}
)
_V2_STATUS_KEYS = frozenset({"open", "completed"})
_V2_NATIVE_KEYS = frozenset({"deadline_horizon_days"})
_V2_TYPE_KEYS = frozenset({"mode", "property", "statuses"})

# Version-1 shapes, retained only for strict decoding of an existing file.
# ``active_structures``, ``native_task_structures``, ``active_statuses``,
# ``assigned_structures``, and ``assigned_on_structures`` were added
# ADDITIVELY to schema version 1, deliberately without bumping the version at
# the time. Each key is therefore optional on read — a legacy file without it
# means the documented default (no custom structure Active-enabled, the
# built-in native task structures, the single ``active`` status, no
# settings-declared assignment property) — while it was emitted on every
# write. Strictness is unchanged: each key, when present, is validated
# exactly.
_V1_ADDITIVE_KEYS = frozenset(
    {
        "active_structures",
        "native_task_structures",
        "active_statuses",
        "assigned_structures",
        "assigned_on_structures",
    }
)
_V1_TOP_KEYS = frozenset(
    {"version", "revision", "native_task_auto", "excluded", "active_structures"}
) | _V1_ADDITIVE_KEYS
_V1_REQUIRED_TOP_KEYS = _V1_TOP_KEYS - _V1_ADDITIVE_KEYS
_V1_NATIVE_KEYS = frozenset(
    {"active_enabled", "due_enabled", "deadline_enabled", "deadline_horizon_days"}
)
_V1_ENABLED_KEYS = ("active_enabled", "due_enabled", "deadline_enabled")


class SettingsStoreError(Exception):
    """Capacities settings storage failure — callers fail closed and preserve
    the existing durable bytes."""


class SettingsFormatError(SettingsStoreError):
    """Existing Capacities settings storage is malformed or unsupported.

    Raised for an unparseable file, duplicate JSON keys, unknown/missing keys,
    strict-type violations, unsupported versions, unknown admission modes,
    mode/property contradictions, or invalid exclusion identities. Callers
    must never overwrite the offending bytes."""


class SettingsConflictError(SettingsStoreError):
    """The caller's ``expected_revision`` is stale — a concurrent save won.

    The settings API maps this to a 409-class conflict response; the durable
    bytes and the stored revision are untouched."""


# ---------------------------------------------------------------------------
# Strict model
# ---------------------------------------------------------------------------


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


def canonical_structure_id(value: Any) -> str:
    """Validate and return a Capacities structure id.

    A structure id must be a non-empty, whitespace-free string so no
    declaration can be keyed by an empty id, a whitespace alias, or a
    non-string value. Any violation raises ``ValueError``.
    """
    if not isinstance(value, str) or not value:
        raise ValueError(f"{value!r} is not a valid structure id")
    if value != value.strip() or any(char.isspace() for char in value):
        raise ValueError(f"{value!r} is not a valid structure id")
    return value


#: Historical name kept for callers that validate a structure id.
canonical_active_structure_id = canonical_structure_id


def canonical_property_id(value: Any, structure_id: str) -> str:
    """Validate and return a RAW provider property id.

    The id is an opaque provider identifier: it is never normalized,
    case-folded, or trimmed. It must be a non-empty string."""
    if not isinstance(value, str) or not value:
        raise ValueError(
            f"structure {structure_id!r} has an invalid property id {value!r}"
        )
    return value


def canonical_admission_value(value: Any) -> str:
    """Validate and return one status name.

    The value must be a non-empty string and is kept exactly as given: a
    status label may legitimately contain whitespace (``In Progress``), and
    the evaluator normalizes at comparison time, so the persisted form is
    never pre-normalized. Any other value raises ``ValueError``.
    """
    if not isinstance(value, str) or not value:
        raise ValueError(f"{value!r} is not a valid configured value")
    return value


def canonical_unique_admission_values(value: Any, key: str) -> tuple[str, ...]:
    """Validate an iterable of unique, non-empty strings for one key.

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


def canonical_assignment_declarations(
    value: Any, key: str = "assigned_structures"
) -> dict[str, str]:
    """Validate a version-1 ``{structure_id: property_id}`` declaration map.

    Retained because a version-1 file still has to be read strictly before it
    is translated. Every key must be a canonical, non-empty structure id and
    every value a non-empty string, kept exactly as declared. Any other value
    raises ``ValueError`` before any file access.
    """
    if not isinstance(value, Mapping):
        raise ValueError(
            f"{key} must be an object mapping structure ids to property ids"
        )
    result: dict[str, str] = {}
    for structure_id, property_id in value.items():
        try:
            canonical_id = canonical_structure_id(structure_id)
        except ValueError as exc:
            raise ValueError(f"{key} has an invalid structure id") from exc
        result[canonical_id] = canonical_property_id(property_id, canonical_id)
    return result


@dataclass(frozen=True)
class NativePolicy:
    """The ``native`` block: the inclusive deadline horizon only.

    Version 1's three native toggles are gone — the ``native`` mode admits on
    every native condition (status-open, due, deadline) and per-structure
    ``statuses`` decides whether the status condition applies. The horizon is
    the inclusive ``deadline <= logical_day + N`` window.
    """

    deadline_horizon_days: int = DEFAULT_DEADLINE_HORIZON_DAYS

    def __post_init__(self) -> None:
        if (
            type(self.deadline_horizon_days) is not int
            or self.deadline_horizon_days < 0
        ):
            raise ValueError("deadline_horizon_days must be a nonnegative integer")

    def as_dict(self) -> dict[str, Any]:
        return {"deadline_horizon_days": self.deadline_horizon_days}


@dataclass(frozen=True)
class StatusVocabulary:
    """The ``statuses`` block: open and completed label vocabularies.

    ``open`` is the default inherited by every type declaration that does not
    declare its own names. ``completed`` is the closed vocabulary. Values are
    stored exactly as configured and compared normalized by the evaluator.
    """

    open: frozenset[str] = DEFAULT_OPEN_STATUSES
    completed: frozenset[str] = DEFAULT_COMPLETED_STATUSES

    def __post_init__(self) -> None:
        for name in ("open", "completed"):
            values = canonical_unique_admission_values(getattr(self, name), name)
            object.__setattr__(self, name, frozenset(values))

    def as_dict(self) -> dict[str, Any]:
        return {
            "open": sorted(self.open),
            "completed": sorted(self.completed),
        }


@dataclass(frozen=True)
class TypeDeclaration:
    """One structure's declaration: exactly one admission mode.

    ``property_id`` (the persisted ``property`` key) is required iff the mode
    is ``assigned``/``assignedOn`` and forbidden otherwise. ``statuses`` is
    tri-state: ``None`` inherits the top-level open vocabulary, ``()``
    disables status admission for this structure, and non-empty names are that
    structure's own open set.
    """

    mode: str = "off"
    property_id: str | None = None
    statuses: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.mode, str) or self.mode not in TYPE_MODES:
            raise ValueError(
                f"{self.mode!r} is not a supported type mode "
                f"(expected one of {sorted(TYPE_MODES)})"
            )
        if self.mode in PROPERTY_MODES:
            if not isinstance(self.property_id, str) or not self.property_id:
                raise ValueError(
                    f"mode {self.mode!r} requires a non-empty raw property id, "
                    f"got {self.property_id!r}"
                )
        elif self.property_id is not None:
            raise ValueError(
                f"mode {self.mode!r} must not declare a property"
            )
        if self.statuses is None:
            return
        names = canonical_unique_admission_values(self.statuses, "statuses")
        object.__setattr__(self, "statuses", tuple(sorted(names)))

    @property
    def requires_property(self) -> bool:
        """Whether this mode is meaningful only with a declared property."""
        return self.mode in PROPERTY_MODES

    @property
    def uses_status(self) -> bool:
        """Whether this mode consults the status condition at all."""
        return self.mode in STATUS_MODES

    @property
    def status_admission_enabled(self) -> bool:
        """Whether a status label can admit an object of this structure.

        Only the ``active`` and ``native`` modes admit on status at all. For
        them, inherited (``None``) and explicitly non-empty name lists both
        admit, while ``()`` — the ``RootTask`` date-only decision — does not.
        Every other mode reports ``False``: its decision never reads status.
        """
        return self.uses_status and (
            self.statuses is None or len(self.statuses) > 0
        )

    def open_statuses(self, inherited: Any = DEFAULT_OPEN_STATUSES) -> frozenset[str]:
        """The effective open set this declaration contributes.

        ``None`` inherits ``inherited``; ``()`` yields the empty set, i.e.
        status admission is disabled; non-empty names are returned verbatim.
        A mode that never admits on status contributes the empty set.
        """
        if not self.uses_status:
            return frozenset()
        if self.statuses is None:
            return frozenset(inherited)
        return frozenset(self.statuses)

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "property": self.property_id,
            "statuses": None if self.statuses is None else list(self.statuses),
        }


def to_status_vocabulary(value: Any = None) -> StatusVocabulary:
    """Coerce a settings-API ``statuses`` value to :class:`StatusVocabulary`.

    Accepts an existing :class:`StatusVocabulary`, ``None`` (the documented
    defaults), or a mapping with ``open``/``completed`` keys. Anything else
    raises ``ValueError`` before any file access."""
    if isinstance(value, StatusVocabulary):
        return value
    if value is None:
        return StatusVocabulary()
    if not isinstance(value, Mapping):
        raise ValueError("statuses must be an object with open/completed lists")
    unknown = {key for key in value if key not in _V2_STATUS_KEYS}
    if unknown:
        raise ValueError(
            f"statuses has unknown keys {sorted(str(key) for key in unknown)}"
        )
    return StatusVocabulary(
        open=value.get("open", DEFAULT_OPEN_STATUSES),
        completed=value.get("completed", DEFAULT_COMPLETED_STATUSES),
    )


def to_native_policy(value: Any = None) -> NativePolicy:
    """Coerce a settings-API ``native`` value to :class:`NativePolicy`."""
    if isinstance(value, NativePolicy):
        return value
    if value is None:
        return NativePolicy()
    if not isinstance(value, Mapping):
        raise ValueError("native must be an object with deadline_horizon_days")
    unknown = {key for key in value if key not in _V2_NATIVE_KEYS}
    if unknown:
        raise ValueError(
            f"native has unknown keys {sorted(str(key) for key in unknown)}"
        )
    if "deadline_horizon_days" not in value:
        raise ValueError("native requires deadline_horizon_days")
    return NativePolicy(deadline_horizon_days=value["deadline_horizon_days"])


def type_declaration_from_mapping(
    value: Any, structure_id: str
) -> TypeDeclaration:
    """Build one strict :class:`TypeDeclaration` from a settings-API mapping.

    ``mode`` is required; ``property`` and ``statuses`` are optional. An
    unknown key, a missing mode, or an unknown mode raises ``ValueError``."""
    if not isinstance(value, Mapping):
        raise ValueError(
            f"type declaration for {structure_id!r} must be an object"
        )
    unknown = {key for key in value if key not in _V2_TYPE_KEYS}
    if unknown:
        raise ValueError(
            f"type declaration for {structure_id!r} has unknown keys "
            f"{sorted(str(key) for key in unknown)}"
        )
    if "mode" not in value:
        raise ValueError(
            f"type declaration for {structure_id!r} requires a mode"
        )
    mode = value["mode"]
    if not isinstance(mode, str) or mode not in TYPE_MODES:
        raise ValueError(
            f"type declaration for {structure_id!r} has an unknown mode "
            f"{mode!r} (expected one of {sorted(TYPE_MODES)})"
        )
    property_id = value.get("property")
    statuses = value.get("statuses")
    if statuses is not None:
        if not isinstance(statuses, list):
            raise ValueError(
                f"type declaration for {structure_id!r} has non-list statuses"
            )
    try:
        return TypeDeclaration(
            mode=mode,
            property_id=property_id,
            statuses=None if statuses is None else tuple(statuses),
        )
    except ValueError as exc:
        raise ValueError(
            f"type declaration for {structure_id!r} is invalid: {exc}"
        ) from exc


def canonical_type_declarations(value: Any = None) -> dict[str, TypeDeclaration]:
    """Validate the whole ``types`` document.

    Keys are canonical structure ids; values are :class:`TypeDeclaration`
    instances or the equivalent mappings. A non-mapping, an invalid structure
    id, or an invalid declaration raises ``ValueError`` before any file
    access."""
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError(
            "types must be an object mapping structure ids to declarations"
        )
    result: dict[str, TypeDeclaration] = {}
    for structure_id, declaration in value.items():
        canonical_id = canonical_structure_id(structure_id)
        if isinstance(declaration, TypeDeclaration):
            result[canonical_id] = declaration
        else:
            result[canonical_id] = type_declaration_from_mapping(
                declaration, canonical_id
            )
    return result


@dataclass(frozen=True)
class CapacitiesSettings:
    """The full persisted Capacities admission policy (schema version 2).

    ``revision`` is server-owned and monotonic; ``excluded`` holds canonical
    stable Capacities identities. Construction is strict: an invalid
    identity, revision, horizon, structure id, mode, property, or status value
    raises ``ValueError`` before any I/O.
    """

    revision: int = 0
    #: Open/completed label vocabularies. ``statuses.open`` is what a type
    #: declaration inherits when it declares no names of its own.
    statuses: StatusVocabulary = field(default_factory=StatusVocabulary)
    #: ``{structure_id: TypeDeclaration}``. A structure absent here is ``off``:
    #: it is never admitted by policy.
    types: dict[str, TypeDeclaration] = field(default_factory=dict)
    #: The native adaptive window (deadline horizon in days).
    native: NativePolicy = field(default_factory=NativePolicy)
    #: Canonical stable identities explicitly excluded from the plan.
    excluded: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if type(self.revision) is not int or self.revision < 0:
            raise ValueError("revision must be a nonnegative integer")
        if not isinstance(self.statuses, StatusVocabulary):
            raise ValueError("statuses must be a StatusVocabulary")
        if not isinstance(self.native, NativePolicy):
            raise ValueError("native must be a NativePolicy")
        object.__setattr__(self, "types", canonical_type_declarations(self.types))
        object.__setattr__(
            self,
            "excluded",
            frozenset(canonical_exclusion_identity(value) for value in self.excluded),
        )

    @property
    def version(self) -> int:
        """The schema version this model persists."""
        return SCHEMA_VERSION

    # -- accessors ---------------------------------------------------------

    def type_declaration(self, structure_id: str) -> TypeDeclaration:
        """The declaration for ``structure_id``; ``off`` when undeclared."""
        return self.types.get(
            structure_id, TypeDeclaration(mode="off")
        )

    def mode_of(self, structure_id: str) -> str:
        """The admission mode for ``structure_id`` (``"off"`` when absent)."""
        return self.type_declaration(structure_id).mode

    def structure_ids(self, mode: str | None = None) -> tuple[str, ...]:
        """Sorted structure ids, optionally filtered to one mode."""
        return tuple(
            sorted(
                structure_id
                for structure_id, declaration in self.types.items()
                if mode is None or declaration.mode == mode
            )
        )

    def open_statuses_for(self, structure_id: str) -> frozenset[str]:
        """The effective open status set for one structure.

        Returns the structure's own names, the shared ``statuses.open`` when it
        inherits, or the empty set when status admission is disabled (``[]``)
        or when the mode never admits on status at all.
        """
        return self.type_declaration(structure_id).open_statuses(self.statuses.open)

    def status_admission_enabled(self, structure_id: str) -> bool:
        """Whether a status label can admit an object of this structure."""
        return self.type_declaration(structure_id).status_admission_enabled

    def as_dict(self) -> dict[str, Any]:
        """The exact persisted JSON shape (sorted, deterministic)."""
        return {
            "version": SCHEMA_VERSION,
            "revision": self.revision,
            "statuses": self.statuses.as_dict(),
            "types": {
                structure_id: declaration.as_dict()
                for structure_id, declaration in sorted(self.types.items())
            },
            "native": self.native.as_dict(),
            "excluded": {identity: True for identity in sorted(self.excluded)},
        }


@dataclass(frozen=True)
class SettingsRead:
    """Result of a settings read: the policy plus whether a file existed."""

    settings: CapacitiesSettings
    persisted: bool
    #: The on-disk schema version that produced ``settings`` (``None`` when no
    #: file existed). ``1`` means the document was translated in memory and
    #: will be rewritten as version 2 by the next save.
    source_version: int | None = None


def settings_path() -> Path:
    """The active store path: ``<app home>/state/capacities-settings.json``.
    The store is machine-local and derives only from
    ``app_config.state_dir()``."""
    return app_config.state_dir() / STATE_FILENAME


def lock_path() -> Path:
    """The active lock-file path, beside the store in the app home.

    A 0-byte runtime artifact created on demand; never migrated."""
    return app_config.state_dir() / STATE_LOCK_FILENAME


# ---------------------------------------------------------------------------
# Version 1 -> version 2 translation
# ---------------------------------------------------------------------------


def migrate_v1_policy(
    *,
    revision: int,
    excluded: Any = (),
    native_task_structures: Any = NATIVE_TASK_STRUCTURES,
    active_structures: Any = (),
    active_statuses: Any = DEFAULT_OPEN_STATUSES,
    assigned_structures: Any = None,
    assigned_on_structures: Any = None,
    deadline_horizon_days: int = DEFAULT_DEADLINE_HORIZON_DAYS,
) -> CapacitiesSettings:
    """Translate decoded version-1 fields into the version-2 document.

    Precedence per structure id, widest evidence last: a declared ``assigned``
    boolean wins (the narrowest explicit gate), then a declared ``assignedOn``
    date, then native membership, then ``Active``-enabled membership. Native
    declarations carry ``statuses: []`` — version 1's three native toggles
    collapse into the single ``native`` mode and the owner's decision keeps
    native task structures date-only. ``active_statuses`` becomes
    ``statuses.open`` verbatim and the built-in completed vocabulary seeds
    ``statuses.completed``. ``revision`` is carried verbatim.
    """
    assigned = canonical_assignment_declarations(
        assigned_structures or {}, key="assigned_structures"
    )
    assigned_on = canonical_assignment_declarations(
        assigned_on_structures or {}, key="assigned_on_structures"
    )
    native_ids = frozenset(
        canonical_unique_admission_values(
            native_task_structures, "native_task_structures"
        )
    )
    if isinstance(active_structures, (str, bytes)):
        raise ValueError("active_structures must be a collection of structure ids")
    active_ids = frozenset(
        canonical_structure_id(value) for value in active_structures
    )

    types: dict[str, TypeDeclaration] = {}
    for structure_id in sorted(assigned):
        types[structure_id] = TypeDeclaration(
            mode="assigned", property_id=assigned[structure_id]
        )
    for structure_id in sorted(assigned_on):
        if structure_id in types:
            # Version 1 refused to persist both declarations for one structure;
            # hand-edited bytes can still carry both, and the boolean is the
            # narrower gate, so the ``assigned`` declaration is kept.
            continue
        types[structure_id] = TypeDeclaration(
            mode="assignedOn", property_id=assigned_on[structure_id]
        )
    for structure_id in sorted(native_ids):
        if structure_id in types:
            continue
        types[structure_id] = TypeDeclaration(mode="native", statuses=())
    for structure_id in sorted(active_ids):
        if structure_id in types:
            continue
        types[structure_id] = TypeDeclaration(mode="active")

    return CapacitiesSettings(
        revision=revision,
        statuses=StatusVocabulary(
            open=frozenset(
                canonical_unique_admission_values(active_statuses, "active_statuses")
            ),
            completed=DEFAULT_COMPLETED_STATUSES,
        ),
        types=types,
        native=NativePolicy(deadline_horizon_days=deadline_horizon_days),
        excluded=frozenset(canonical_exclusion_identity(value) for value in excluded),
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


def _decode(data: Any) -> CapacitiesSettings:
    """Decode a parsed JSON document into strict settings or raise.

    Accepts schema version 2 natively and schema version 1 by translating it
    in memory. Raises :class:`SettingsFormatError` for any unknown/missing
    key, strict type violation, unsupported version, unknown admission mode,
    mode/property contradiction, or invalid exclusion identity.
    """
    if not isinstance(data, dict):
        raise SettingsFormatError("capacities settings must be a JSON object")
    version = data.get("version")
    if type(version) is not int:
        raise SettingsFormatError(
            f"capacities settings version {version!r} is unsupported"
        )
    if version == SCHEMA_VERSION_V1:
        return _decode_v1(data)
    if version != SCHEMA_VERSION:
        raise SettingsFormatError(
            f"capacities settings version {version!r} is unsupported"
        )
    return _decode_v2(data)


def _decode_revision(data: dict[str, Any]) -> int:
    revision = data["revision"]
    if type(revision) is not int or revision < 0:
        raise SettingsFormatError(
            "capacities settings revision must be a nonnegative integer"
        )
    return revision


def _decode_excluded(data: dict[str, Any]) -> frozenset[str]:
    raw = data["excluded"]
    if not isinstance(raw, dict):
        raise SettingsFormatError("capacities settings excluded must be an object")
    excluded: set[str] = set()
    for key, flag in raw.items():
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
    return frozenset(excluded)


def _decode_statuses(data: dict[str, Any]) -> StatusVocabulary:
    raw = data["statuses"]
    if not isinstance(raw, dict) or set(raw) != _V2_STATUS_KEYS:
        raise SettingsFormatError(
            "capacities settings statuses must have exactly open and completed"
        )
    values: dict[str, frozenset[str]] = {}
    for name in ("open", "completed"):
        entries = raw[name]
        if not isinstance(entries, list):
            raise SettingsFormatError(
                f"capacities settings statuses.{name} must be a list"
            )
        try:
            values[name] = frozenset(
                canonical_unique_admission_values(entries, f"statuses.{name}")
            )
        except ValueError as exc:
            raise SettingsFormatError(
                f"capacities settings statuses.{name} has an invalid entry"
            ) from exc
    return StatusVocabulary(open=values["open"], completed=values["completed"])


def _decode_native(data: dict[str, Any]) -> NativePolicy:
    raw = data["native"]
    if not isinstance(raw, dict):
        raise SettingsFormatError("capacities settings native must be an object")
    if set(raw) != _V2_NATIVE_KEYS:
        raise SettingsFormatError(
            "capacities settings native is malformed (expected exactly "
            "deadline_horizon_days)"
        )
    horizon = raw["deadline_horizon_days"]
    if type(horizon) is not int or horizon < 0:
        raise SettingsFormatError(
            "capacities settings deadline_horizon_days must be a nonnegative integer"
        )
    return NativePolicy(deadline_horizon_days=horizon)


def _decode_types(data: dict[str, Any]) -> dict[str, TypeDeclaration]:
    raw = data["types"]
    if not isinstance(raw, dict):
        raise SettingsFormatError("capacities settings types must be an object")
    types: dict[str, TypeDeclaration] = {}
    for structure_id, declaration in raw.items():
        try:
            canonical_id = canonical_structure_id(structure_id)
        except ValueError as exc:
            raise SettingsFormatError(
                "capacities settings has an invalid structure id"
            ) from exc
        if not isinstance(declaration, dict):
            raise SettingsFormatError(
                f"capacities settings type {canonical_id!r} must be an object"
            )
        if set(declaration) - _V2_TYPE_KEYS or "mode" not in declaration:
            raise SettingsFormatError(
                f"capacities settings type {canonical_id!r} has unknown or "
                "missing keys"
            )
        mode = declaration["mode"]
        if not isinstance(mode, str) or mode not in TYPE_MODES:
            raise SettingsFormatError(
                f"capacities settings type {canonical_id!r} has an unknown mode "
                f"{mode!r}; expected one of {sorted(TYPE_MODES)}"
            )
        property_id = declaration.get("property")
        statuses = declaration.get("statuses")
        if statuses is not None:
            if not isinstance(statuses, list):
                raise SettingsFormatError(
                    f"capacities settings type {canonical_id!r} statuses must be "
                    "a list or null"
                )
            try:
                statuses = canonical_unique_admission_values(
                    statuses, f"type {canonical_id!r} statuses"
                )
            except ValueError as exc:
                raise SettingsFormatError(
                    f"capacities settings type {canonical_id!r} has an invalid "
                    "status entry"
                ) from exc
        if mode in PROPERTY_MODES:
            if property_id is None:
                raise SettingsFormatError(
                    f"capacities settings type {canonical_id!r} mode {mode!r} "
                    "requires a property id"
                )
            if not isinstance(property_id, str) or not property_id:
                raise SettingsFormatError(
                    f"capacities settings type {canonical_id!r} has an invalid "
                    "property id"
                )
        elif property_id is not None:
            raise SettingsFormatError(
                f"capacities settings type {canonical_id!r} mode {mode!r} must "
                "not declare a property id"
            )
        types[canonical_id] = TypeDeclaration(
            mode=mode, property_id=property_id, statuses=statuses
        )
    return types


def _decode_v2(data: dict[str, Any]) -> CapacitiesSettings:
    """Strict decode of a version-2 document."""
    if set(data) != _V2_TOP_KEYS:
        raise SettingsFormatError(
            "capacities settings has unknown or missing top-level keys"
        )
    return CapacitiesSettings(
        revision=_decode_revision(data),
        statuses=_decode_statuses(data),
        types=_decode_types(data),
        native=_decode_native(data),
        excluded=_decode_excluded(data),
    )


def _decode_v1(data: dict[str, Any]) -> CapacitiesSettings:
    """Strict decode of a version-1 document, translated to version 2.

    Version-1 strictness is unchanged: unknown or missing keys, strict types,
    malformed declarations, and invalid exclusion identities all still fail
    closed. The native toggles are validated and then collapse into the single
    ``native`` mode (see :func:`migrate_v1_policy`)."""
    if set(data) - _V1_TOP_KEYS or not _V1_REQUIRED_TOP_KEYS <= set(data):
        raise SettingsFormatError(
            "capacities settings has unknown or missing top-level keys"
        )
    revision = _decode_revision(data)

    native = data["native_task_auto"]
    if not isinstance(native, dict) or set(native) != _V1_NATIVE_KEYS:
        raise SettingsFormatError(
            "capacities settings native_task_auto is malformed"
        )
    for key in _V1_ENABLED_KEYS:
        if type(native[key]) is not bool:
            raise SettingsFormatError(
                f"capacities settings {key} must be a boolean"
            )
    horizon = native["deadline_horizon_days"]
    if type(horizon) is not int or horizon < 0:
        raise SettingsFormatError(
            "capacities settings deadline_horizon_days must be a nonnegative integer"
        )

    excluded = _decode_excluded(data)

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
            active.add(canonical_structure_id(key))
        except ValueError as exc:
            raise SettingsFormatError(
                "capacities settings has an invalid active structure id"
            ) from exc

    native_structures: set[str] = set(NATIVE_TASK_STRUCTURES)
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

    statuses: set[str] = set(DEFAULT_OPEN_STATUSES)
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

    assigned_raw = data.get("assigned_structures", {})
    if not isinstance(assigned_raw, dict):
        raise SettingsFormatError(
            "capacities settings assigned_structures must be an object"
        )
    try:
        assigned = canonical_assignment_declarations(assigned_raw)
    except ValueError as exc:
        raise SettingsFormatError(
            "capacities settings has an invalid assignment declaration"
        ) from exc

    assigned_on_raw = data.get("assigned_on_structures", {})
    if not isinstance(assigned_on_raw, dict):
        raise SettingsFormatError(
            "capacities settings assigned_on_structures must be an object"
        )
    try:
        assigned_on = canonical_assignment_declarations(
            assigned_on_raw, key="assigned_on_structures"
        )
    except ValueError as exc:
        raise SettingsFormatError(
            "capacities settings has an invalid Assigned On declaration"
        ) from exc

    return migrate_v1_policy(
        revision=revision,
        excluded=excluded,
        native_task_structures=native_structures,
        active_structures=active,
        active_statuses=statuses,
        assigned_structures=assigned,
        assigned_on_structures=assigned_on,
        deadline_horizon_days=horizon,
    )


@dataclass(frozen=True)
class _LoadedDocument:
    """A strict decode of the on-disk document plus what it was."""

    settings: CapacitiesSettings
    raw: bytes
    version: int


def _load_document(path: Path) -> _LoadedDocument:
    """Strict read of an existing file. Raises on malformed/unsupported data
    or I/O failure — never repairs, defaults, or erases."""
    try:
        raw = Path(path).read_bytes()
    except OSError as exc:
        raise SettingsStoreError(
            "capacities settings storage is unreadable"
        ) from exc
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SettingsFormatError(
            "capacities settings file is malformed"
        ) from exc
    try:
        data = json.loads(text, object_pairs_hook=_reject_duplicate_keys)
    except ValueError as exc:
        raise SettingsFormatError(
            "capacities settings file is malformed"
        ) from exc
    settings = _decode(data)
    return _LoadedDocument(
        settings=settings, raw=raw, version=data["version"]
    )


def _load_document_optional() -> _LoadedDocument | None:
    """Strict read, or ``None`` when no file exists."""
    try:
        path = settings_path()
        if not os.path.lexists(path):
            return None
    except (OSError, TypeError, ValueError) as exc:  # pragma: no cover — defensive
        raise SettingsStoreError(
            "capacities settings storage is unreadable"
        ) from exc
    return _load_document(path)


def read_settings() -> SettingsRead:
    """Read the app-owned Capacities settings.

    A missing file returns the default policy with ``persisted=False`` and
    creates nothing. A version-1 file is translated in memory to the
    version-2 shape (``source_version`` reports ``1``); nothing is written by
    a read. Malformed/unsupported storage raises
    :class:`SettingsFormatError`; an unreadable file raises
    :class:`SettingsStoreError`.

    The state-dir file is the only location.
    """
    document = _load_document_optional()
    if document is None:
        return SettingsRead(
            settings=CapacitiesSettings(),
            persisted=False,
            source_version=None,
        )
    return SettingsRead(
        settings=document.settings,
        persisted=True,
        source_version=document.version,
    )


# ---------------------------------------------------------------------------
# Locking + atomic write
# ---------------------------------------------------------------------------
# Process-local RMW locks (same single-process convention as
# ``duration_memory`` / ``runstate`` G26); the flock on the app-home lock file
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


def _backup_stamp() -> str:
    """The migration-backup timestamp (UTC, microsecond resolution)."""
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def backup_path(stamp: str | None = None) -> Path:
    """The sibling backup path for a version-1 migration save."""
    target = settings_path()
    return target.with_name(
        f"{STATE_FILENAME}{BACKUP_INFIX}{stamp or _backup_stamp()}"
    )


def _write_v1_backup(raw: bytes) -> Path:
    """Write and fsync a byte-for-byte backup of the version-1 document.

    Called under the store lock, BEFORE the atomic replace, so the previous
    bytes survive the migration. A failure raises and aborts the save with the
    original file still in place."""
    base = backup_path()
    target = base
    attempt = 0
    while True:
        try:
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            break
        except FileExistsError:
            attempt += 1
            if attempt > 100:
                raise
            target = base.with_name(f"{base.name}-{attempt}")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(raw)
            fh.flush()
            os.fsync(fh.fileno())
    except BaseException:
        try:
            target.unlink()
        except OSError:
            pass
        raise
    return target


def save_settings(
    *,
    expected_revision: int,
    statuses: Any = None,
    types: Any = None,
    native: Any = None,
    excluded: Any = (),
) -> CapacitiesSettings:
    """Explicitly replace the whole Capacities admission policy as version 2.

    The full editable policy is supplied (full replacement), while ``version``
    and ``revision`` stay server-owned: the saved revision is the stored
    revision plus one. Inputs are validated BEFORE any file access, so a
    rejected save leaves the stored bytes and revision untouched.

    ``statuses`` accepts a :class:`StatusVocabulary` or an ``open``/
    ``completed`` mapping (``None`` means the documented default vocabulary).
    ``types`` accepts ``{structure_id: TypeDeclaration | mapping}`` and
    defaults to no declarations, i.e. every structure is ``off``. ``native``
    accepts a :class:`NativePolicy` or a ``deadline_horizon_days`` mapping.
    Omitted inputs therefore replace to the documented defaults, exactly like
    every other full-replacement input.

    Under the process lock the current file is read, its revision compared
    with ``expected_revision``, and only then is the file replaced atomically.
    A stale revision raises :class:`SettingsConflictError`; malformed storage
    raises :class:`SettingsFormatError`; both preserve the original bytes. Lock
    and write failures raise and likewise leave the bytes untouched.

    When the file being replaced is a version-1 document, a sibling
    ``capacities-settings.json.bak-v1-<timestamp>`` backup of the previous
    bytes is written and fsynced before the replace. A backup failure aborts
    the save with the original file intact.
    """
    if type(expected_revision) is not int or expected_revision < 0:
        raise ValueError("expected_revision must be a nonnegative integer")
    vocabulary = to_status_vocabulary(statuses)
    native_policy = to_native_policy(native)
    declarations = canonical_type_declarations(types)
    identities = frozenset(canonical_exclusion_identity(value) for value in excluded)

    with _store_lock():
        fh = _acquire_lock_file()
        try:
            current = _load_document_optional()
            stored_revision = current.settings.revision if current is not None else 0
            if stored_revision != expected_revision:
                raise SettingsConflictError(
                    "capacities settings changed since they were read "
                    f"(stored revision {stored_revision}, expected "
                    f"{expected_revision})"
                )
            saved = CapacitiesSettings(
                revision=stored_revision + 1,
                statuses=vocabulary,
                types=declarations,
                native=native_policy,
                excluded=identities,
            )
            if current is not None and current.version == SCHEMA_VERSION_V1:
                _write_v1_backup(current.raw)
            _atomic_write_json(settings_path(), saved.as_dict())
        finally:
            _release_lock_file(fh)
    return saved
