"""capacities_selections.py — durable per-identity selection acknowledgements
(U3c-1) plus the pure promotion resolver.

An operator's "keep this Capacities object visible even though it is not
auto-assigned" decision must survive a cache refresh, a rules edit, and a
type-mapping change. This module owns the two pure/local pieces of that model:

1. **The selection store** — a strict, versioned, machine-local document
   ``<app home>/state/capacities-selections.json`` following the
   :mod:`capacities_rules` store pattern (validated construction,
   duplicate-key-rejecting decode, optimistic ``revision``, atomic write,
   cross-process lock). It records, per canonical Capacities identity, the
   rules revision the decision was made under and whether it was
   acknowledged. The selection is IDENTITY-ONLY: the adapter's row
   ``source_fingerprint`` exists but is deliberately not persisted, because a
   refresh must not invalidate a durable operator decision.

2. **The pure resolver** — :func:`resolve_selections` maps stored records onto
   cached rows and returns the rows to promote plus bounded, content-free
   notices. It performs no I/O, computes no exclusions (the caller supplies
   ``hard_excluded``), never fabricates a row from stored data, and never
   rewrites a promoted row's evaluation fields: an UNKNOWN row stays UNKNOWN
   with its reasons unchanged (AE19/AE20 — no false evaluation claim).

Identity validation is delegated to
:func:`capacities_settings.canonical_exclusion_identity`: it both validates
and returns the canonical ``capacities:{space}:{structure}:{object}`` string,
raising ``ValueError`` for a title, path, bare id, non-canonical casing, or
whitespace alias — the exact semantics the save boundary needs, and the same
helper that already guards exclusion identities, so the two surfaces cannot
drift. The identity's space component must additionally match the requested
space; a different-space identity is rejected on save.

The store is machine-local only: it never reads a credential, calls a
provider, or writes the vault.
"""
from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import app_config
import capacities_cache_io
from capacities_settings import canonical_exclusion_identity

# ---------------------------------------------------------------------------
# Paths, schema, and notice codes
# ---------------------------------------------------------------------------

STATE_FILENAME = "capacities-selections.json"
STATE_LOCK_FILENAME = "capacities-selections.lock"
SELECTIONS_SCHEMA_VERSION = 1

#: A stored selection was excluded by the caller (tag-excluded, completed, or
#: dropped): it is not promoted; the caller removes it from its surface.
NOTICE_EXCLUDED = "excluded"
#: The identity's structure is no longer mapped: the record is retained but
#: not promoted while the type stays disabled.
NOTICE_TYPE_DISABLED = "type_disabled"
#: No cached row carries the identity: nothing is promoted, the record stays.
NOTICE_NOT_CACHED = "not_cached"
#: The row was promoted, but the decision was made under a different rules
#: revision than the caller's current one (a warning, not a removal).
NOTICE_RULE_CHANGED = "rule_changed"

_TOP_KEYS = frozenset({"version", "revision", "space_id", "selections"})
_SELECTION_KEYS = frozenset({"identity", "rules_revision", "acknowledged"})


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class SelectionStoreError(Exception):
    """Capacities selections storage could not be read or written.

    Callers fail closed and preserve the existing durable bytes."""


class SelectionFormatError(SelectionStoreError):
    """Existing Capacities selections storage is malformed or unsupported.

    Raised for an unparseable file, duplicate JSON keys, unknown/missing keys,
    strict-type violations, an unsupported version, or an internally
    inconsistent record. Callers must never overwrite the offending bytes."""


class SelectionConflict(SelectionStoreError):
    """The caller's ``expected_revision`` is stale — a concurrent save won.

    Carries both revisions so the caller can answer with them. The message
    stays bounded and names neither a path nor any payload content."""

    def __init__(self, *, expected_revision: int, current_revision: int) -> None:
        self.expected_revision = expected_revision
        self.current_revision = current_revision
        super().__init__(
            "capacities selections changed since they were read "
            f"(stored revision {current_revision}, expected {expected_revision})"
        )


class SelectionValidationError(SelectionStoreError):
    """The caller's selection input is invalid; nothing was written.

    Raised for a non-canonical identity, an identity from a different space, a
    duplicate identity, a non-int or negative ``rules_revision``, a non-bool
    ``acknowledged``, or an entry with unknown/missing keys."""


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------

def _valid_id(value: Any) -> str:
    """Validate a non-empty, whitespace-free identifier string."""
    if not isinstance(value, str) or not value:
        raise ValueError(f"{value!r} is not a valid id")
    if value != value.strip() or any(char.isspace() for char in value):
        raise ValueError(f"{value!r} is not a valid id")
    return value


def _identity_parts(identity: str) -> tuple[str, str]:
    """The ``(space_id, structure_id)`` of a canonical identity.

    ``canonical_exclusion_identity`` guarantees the four-part form, so this is
    a pure split rather than a second parse."""
    parts = identity.split(":", 3)
    if len(parts) < 4:
        raise ValueError(f"{identity!r} is not a canonical Capacities identity")
    return parts[1], parts[2]


@dataclass(frozen=True)
class SelectionRecord:
    """One stored selection: identity, the rules revision it was made under,
    and whether the operator acknowledged it."""

    identity: str
    rules_revision: int = 0
    acknowledged: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "identity", canonical_exclusion_identity(self.identity)
        )
        if type(self.rules_revision) is not int or self.rules_revision < 0:
            raise ValueError("rules_revision must be a nonnegative integer")
        if type(self.acknowledged) is not bool:
            raise ValueError("acknowledged must be a boolean")

    def as_dict(self) -> dict[str, Any]:
        return {
            "identity": self.identity,
            "rules_revision": self.rules_revision,
            "acknowledged": self.acknowledged,
        }


@dataclass(frozen=True)
class SelectionsRecord:
    """The full persisted selections document for one space."""

    space_id: str
    selections: tuple[SelectionRecord, ...] = ()
    revision: int = 0
    version: int = SELECTIONS_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if type(self.version) is not int or self.version != SELECTIONS_SCHEMA_VERSION:
            raise ValueError(f"unsupported version {self.version!r}")
        if type(self.revision) is not int or self.revision < 0:
            raise ValueError("revision must be a nonnegative integer")
        object.__setattr__(self, "space_id", _valid_id(self.space_id))
        selections = tuple(self.selections)
        seen: set[str] = set()
        for record in selections:
            if not isinstance(record, SelectionRecord):
                raise ValueError("selections must contain SelectionRecord entries")
            space_id, _structure_id = _identity_parts(record.identity)
            if space_id != self.space_id:
                raise ValueError(
                    "selection identity belongs to a different space"
                )
            if record.identity in seen:
                raise ValueError(
                    f"duplicate selection identity {record.identity!r}"
                )
            seen.add(record.identity)
        object.__setattr__(self, "selections", selections)

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "revision": self.revision,
            "space_id": self.space_id,
            "selections": [record.as_dict() for record in self.selections],
        }


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

def selections_path() -> Path:
    """The store path: ``<app home>/state/capacities-selections.json``.

    Machine-local, never a vault path, and resolved through
    ``app_config.state_dir()`` so ``TDTB_HOME`` relocates it in tests."""
    return app_config.state_dir() / STATE_FILENAME


def lock_path() -> Path:
    """The lock-file path, beside the record in the app home."""
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


def _decode_selection(row: Any) -> SelectionRecord:
    if not isinstance(row, dict):
        raise SelectionFormatError(
            "capacities selections entry must be a JSON object"
        )
    if set(row) != _SELECTION_KEYS:
        raise SelectionFormatError(
            "capacities selections entry has unknown or missing keys"
        )
    try:
        return SelectionRecord(
            identity=row["identity"],
            rules_revision=row["rules_revision"],
            acknowledged=row["acknowledged"],
        )
    except ValueError as exc:
        raise SelectionFormatError(
            "capacities selections entry is malformed"
        ) from exc


def _decode(data: Any) -> SelectionsRecord:
    if not isinstance(data, dict):
        raise SelectionFormatError(
            "capacities selections record must be a JSON object"
        )
    if set(data) != _TOP_KEYS:
        raise SelectionFormatError(
            "capacities selections record has unknown or missing top-level keys"
        )
    version = data["version"]
    if type(version) is not int or version != SELECTIONS_SCHEMA_VERSION:
        raise SelectionFormatError(
            f"capacities selections record version {version!r} is unsupported"
        )
    revision = data["revision"]
    if type(revision) is not int or revision < 0:
        raise SelectionFormatError(
            "capacities selections record revision must be a nonnegative integer"
        )
    selections_raw = data["selections"]
    if not isinstance(selections_raw, list):
        raise SelectionFormatError(
            "capacities selections record selections must be a list"
        )
    selections = tuple(_decode_selection(row) for row in selections_raw)
    try:
        return SelectionsRecord(
            space_id=data["space_id"],
            selections=selections,
            revision=revision,
            version=version,
        )
    except ValueError as exc:
        raise SelectionFormatError(
            "capacities selections record is malformed"
        ) from exc


def _load_strict(path: Path) -> SelectionsRecord:
    """Strict read of an existing file. Raises on malformed/unsupported data
    or I/O failure — never repairs, defaults, or erases."""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SelectionStoreError(
            "capacities selections record is unreadable"
        ) from exc
    try:
        data = json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
    except ValueError as exc:
        raise SelectionFormatError(
            "capacities selections record file is malformed"
        ) from exc
    return _decode(data)


# ---------------------------------------------------------------------------
# Public store API
# ---------------------------------------------------------------------------

def empty_selections(space_id: str) -> SelectionsRecord:
    """The empty default selection set for a space (creating nothing)."""
    return SelectionsRecord(space_id=space_id)


def load_selections(
    space_id: str, *, path: str | Path | None = None
) -> SelectionsRecord:
    """Read the persisted selections for ``space_id``.

    An absent file returns the empty default WITHOUT creating anything.
    Malformed or unsupported storage raises :class:`SelectionFormatError`; an
    unreadable file raises :class:`SelectionStoreError`. Reads never write.

    Reads are space-scoped: a document whose ``space_id`` differs from the
    requested space yields the empty default rather than another space's
    selections."""
    target = Path(path) if path is not None else selections_path()
    try:
        exists = target.is_file()
    except OSError as exc:
        raise SelectionStoreError(
            "capacities selections record is unreadable"
        ) from exc
    if not exists:
        return empty_selections(space_id)
    record = _load_strict(target)
    if record.space_id != space_id:
        return empty_selections(space_id)
    return record


def _current_for_save(space_id: str, target: Path) -> SelectionsRecord | None:
    """The stored record when it belongs to ``space_id``; else ``None``.

    A document for a different space is treated as absent for READ purposes,
    so this save never merges into another space's selections. The save is a
    full replacement, so a space change discards the previous space's
    selections; the conflict token for a foreign-space save starts at zero
    rather than at the stored revision (the :mod:`capacities_rules` store
    behaves the same way)."""
    try:
        exists = target.is_file()
    except OSError as exc:
        raise SelectionStoreError(
            "capacities selections record is unreadable"
        ) from exc
    if not exists:
        return None
    record = _load_strict(target)
    if record.space_id != space_id:
        return None
    return record


def _record_from_entry(entry: Any) -> SelectionRecord:
    """Validate one save entry (a mapping or an already-built record).

    A mapping must carry exactly the three stored keys; every value is
    strictly type-checked by :class:`SelectionRecord`."""
    if isinstance(entry, SelectionRecord):
        return entry
    if not isinstance(entry, Mapping):
        raise ValueError("selection entry must be a JSON object")
    if set(entry) != _SELECTION_KEYS:
        raise ValueError("selection entry has unknown or missing keys")
    return SelectionRecord(
        identity=entry["identity"],
        rules_revision=entry["rules_revision"],
        acknowledged=entry["acknowledged"],
    )


def save_selections(
    *,
    space_id: str,
    expected_revision: int,
    selections: Iterable[SelectionRecord | Mapping[str, Any]],
) -> SelectionsRecord:
    """Validate and persist the full selection set for ``space_id``.

    Full replacement: the stored document becomes exactly ``selections``. A
    stale ``expected_revision`` raises :class:`SelectionConflict` and preserves
    the original bytes. Invalid input raises
    :class:`SelectionValidationError` before any file access. The saved
    document's ``revision`` is the stored revision plus one (0 → 1 on the
    first save)."""
    if type(expected_revision) is not int or expected_revision < 0:
        raise ValueError("expected_revision must be a nonnegative integer")
    _valid_id(space_id)
    try:
        records = tuple(_record_from_entry(entry) for entry in selections)
    except TypeError as exc:
        raise SelectionValidationError(
            "selections must be an iterable of selection entries"
        ) from exc
    except ValueError as exc:
        raise SelectionValidationError(str(exc)) from exc
    try:
        replacement = SelectionsRecord(space_id=space_id, selections=records)
    except ValueError as exc:
        raise SelectionValidationError(str(exc)) from exc

    target = selections_path()
    with capacities_cache_io.store_lock(target):
        fh = capacities_cache_io.acquire_path_lock(lock_path())
        try:
            current = _current_for_save(space_id, target)
            current_revision = current.revision if current is not None else 0
            if current_revision != expected_revision:
                raise SelectionConflict(
                    expected_revision=expected_revision,
                    current_revision=current_revision,
                )
            saved = SelectionsRecord(
                space_id=space_id,
                selections=replacement.selections,
                revision=current_revision + 1,
            )
            capacities_cache_io.atomic_write_json(target, saved.as_dict())
        finally:
            capacities_cache_io.release_lock_file(fh)

    return saved


# ---------------------------------------------------------------------------
# Pure resolver
# ---------------------------------------------------------------------------

def resolve_selections(
    selections: Iterable[SelectionRecord] | SelectionsRecord,
    cached_rows: Iterable[Mapping[str, Any]],
    *,
    rules_revision: int,
    mapped_structures: set[str],
    hard_excluded: frozenset[str],
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Map stored selections onto cached rows: ``(promoted_rows, notices)``.

    Pure and read-only. ``hard_excluded`` is computed by the CALLER (tag
    exclusions plus completed or dropped identities); this function never
    derives an exclusion itself. ``mapped_structures`` holds the structure ids
    still mapped. ``rules_revision`` is the caller's current rules revision.

    Per stored selection, in order:

    1. identity in ``hard_excluded`` — not promoted; the caller removes it
       from its surface, notice ``excluded`` even when a cached row exists;
    2. structure not in ``mapped_structures`` — retained as a disabled-type
       record, not promoted, notice ``type_disabled``;
    3. no cached row with the identity — not promoted, notice ``not_cached``,
       the record stays stored;
    4. cached row present but the decision was made under a different rules
       revision — promoted, notice ``rule_changed``;
    5. otherwise — promoted, no notice.

    A promoted row is a SHALLOW COPY of the cached row; its evaluation fields
    (``capacities_rule``, ``capacities_completion_state``,
    ``capacities_review_reasons``) are never rewritten, so an UNKNOWN row
    stays UNKNOWN with its reasons unchanged. Rows are only ever taken from
    ``cached_rows`` — never fabricated from stored data. Notices carry only a
    code and a canonical identity: no titles, no payloads, no credentials."""
    if isinstance(selections, SelectionsRecord):
        selections = selections.selections
    rows_by_identity: dict[str, Mapping[str, Any]] = {}
    for row in cached_rows:
        if not isinstance(row, Mapping):
            continue
        identity = row.get("identity")
        if isinstance(identity, str) and identity not in rows_by_identity:
            rows_by_identity[identity] = row

    promoted_rows: list[dict[str, Any]] = []
    notices: list[dict[str, str]] = []
    for record in selections:
        if not isinstance(record, SelectionRecord):
            raise ValueError("selections must contain SelectionRecord entries")
        identity = record.identity
        if identity in hard_excluded:
            notices.append({"code": NOTICE_EXCLUDED, "identity": identity})
            continue
        _space_id, structure_id = _identity_parts(identity)
        if structure_id not in mapped_structures:
            notices.append({"code": NOTICE_TYPE_DISABLED, "identity": identity})
            continue
        row = rows_by_identity.get(identity)
        if row is None:
            notices.append({"code": NOTICE_NOT_CACHED, "identity": identity})
            continue
        promoted_rows.append(dict(row))
        if rules_revision != record.rules_revision:
            notices.append({"code": NOTICE_RULE_CHANGED, "identity": identity})

    return promoted_rows, notices
