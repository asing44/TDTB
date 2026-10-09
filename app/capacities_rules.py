"""capacities_rules.py — typed per-type Capacities inclusion rules (U3a).

The Capacities-first migration gives every configured Capacities structure its
own editable nested inclusion rule (R26). This module owns the three pieces of
that rule model that are pure and local:

1. **The rule grammar** — a recursive predicate ``{"all": [...]}``,
   ``{"any": [...]}``, ``{"not": {...}}`` or a leaf
   ``{"prop": <id>, "op": <op>, "values": [...]}``, mirroring the shape and
   validation style of :mod:`producer_rules`. Unlike the producer, an EMPTY
   ``all``/``any`` is meaningful here (``MATCH``/``NO_MATCH``), and the
   user-regex ``matches`` operator is deliberately absent (KTD5): no arbitrary
   user code or regex execution is added.

2. **Three-state evaluation** — :func:`evaluate` returns ``MATCH``,
   ``NO_MATCH`` or ``UNKNOWN``, never a bare bool, and combines children with
   Kleene logic. A presence predicate (``exists``/``truthy``) is decided from
   the record. A value comparison on a property the record does not CARRY is
   ``UNKNOWN`` rather than a silent false: surfacing an under-evaluated object
   is preferred over omitting an eligible one (R29/R31). The leaf comparison
   helpers are reused from :mod:`producer_rules` so the vocabulary cannot
   drift.

3. **Active/draft persistence** — a strict, versioned, machine-local store
   following the ``SourceRecord`` pattern in :mod:`capacities_builder`
   (validated construction, duplicate-key-rejecting decode, optimistic
   ``revision``, atomic write). A rule that is valid for the DISCOVERED type
   shape becomes ``active``; a merely invalid rule is kept as ``draft`` and the
   previously active rule is untouched (AE21/R33).

The store is machine-local only: ``app_config.state_dir()/capacities-rules.json``.
It never reads a credential, calls a provider, or writes the vault.

Deviations from the producer's own validators are deliberate and covered by
tests:

* ``matches`` is rejected outright (KTD5).
* empty ``all``/``any`` arrays are accepted and evaluate to ``MATCH``/``NO_MATCH``.
* ``exists`` is true whenever the record CARRIES the property (a carried JSON
  ``null`` counts as present); ``truthy`` then applies ordinary truthiness.
"""
from __future__ import annotations

import copy
import enum
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import app_config
import capacities_cache_io
from producer_rules import (
    _MISSING,
    _as_date,
    _as_number,
    _lookup,
    _resolve_value,
)

# ---------------------------------------------------------------------------
# Paths, schema, and grammar constants
# ---------------------------------------------------------------------------

STATE_FILENAME = "capacities-rules.json"
STATE_LOCK_FILENAME = "capacities-rules.lock"
RULES_SCHEMA_VERSION = 1

#: The operators a leaf may use. ``matches`` is intentionally NOT here: the
#: typed model deliberately adds no user regex execution (KTD5).
OPS = frozenset({"eq", "in", "exists", "truthy", "lt", "gt", "before", "after"})

#: Ops that read a non-empty ``values`` array.
VALUE_OPS = frozenset({"eq", "in", "lt", "gt", "before", "after"})
#: Ops decidable from the record alone (never UNKNOWN).
PRESENCE_OPS = frozenset({"exists", "truthy"})
#: Ops requiring a numeric / date-ish property kind.
NUMBER_OPS = frozenset({"lt", "gt"})
DATE_OPS = frozenset({"before", "after"})
EQUALITY_OPS = frozenset({"eq", "in"})

#: Property kinds a comparison operator is compatible with. The kinds are the
#: ones ``capacities_adapter`` discovers and flattens.
NUMBER_KINDS = frozenset({"number"})
DATE_KINDS = frozenset({"date"})
VALUE_KINDS = frozenset(
    {"title", "text", "richText", "number", "boolean", "url", "date", "label", "entity"}
)

#: Bounded-reason ceiling so a validation message can never be unbounded.
MAX_REASON = 240

_TOP_KEYS = frozenset({"version", "revision", "space_id", "structures"})
_STRUCTURE_KEYS = frozenset(
    {"structure_id", "active", "draft", "fallback_minutes"}
)
_STRUCTURE_REQUIRED_KEYS = frozenset({"structure_id"})


# ---------------------------------------------------------------------------
# Three-state result
# ---------------------------------------------------------------------------

class Evaluation(enum.Enum):
    """The three-state result of evaluating a rule against a record.

    Deliberately NOT a bool: an under-evaluated object (a value comparison on
    a property the record does not carry) is ``UNKNOWN``, distinct from a
    decided ``NO_MATCH`` (R29/R31)."""

    MATCH = "match"
    NO_MATCH = "no_match"
    UNKNOWN = "unknown"


#: Module-level aliases for ergonomics (``state is MATCH``).
MATCH = Evaluation.MATCH
NO_MATCH = Evaluation.NO_MATCH
UNKNOWN = Evaluation.UNKNOWN

_NEGATE = {MATCH: NO_MATCH, NO_MATCH: MATCH, UNKNOWN: UNKNOWN}


# ---------------------------------------------------------------------------
# Errors and records
# ---------------------------------------------------------------------------

class RulesStoreError(Exception):
    """Capacities rules storage could not be read or written.

    Callers fail closed and preserve the existing durable bytes."""


class RulesFormatError(RulesStoreError):
    """Existing Capacities rules storage is malformed or unsupported.

    Raised for an unparseable file, duplicate JSON keys, unknown/missing keys,
    strict-type violations, an unsupported version, or a stored predicate that
    is not a well-formed rule. Callers must never overwrite the offending
    bytes."""


class RulesConflictError(RulesStoreError):
    """The caller's ``expected_revision`` is stale — a concurrent save won.

    Carries both revisions so the caller can answer with them. The message
    stays bounded and names neither a path nor any payload content."""

    def __init__(self, *, expected_revision: int, current_revision: int) -> None:
        self.expected_revision = expected_revision
        self.current_revision = current_revision
        super().__init__(
            "capacities rules changed since it was read "
            f"(stored revision {current_revision}, expected {expected_revision})"
        )


@dataclass(frozen=True)
class RuleStructureRecord:
    """One per-structure rule entry: the active rule, the latest draft, and
    the operator's estimated fallback duration (minutes)."""

    structure_id: str
    active: dict[str, Any] | None = None
    draft: dict[str, Any] | None = None
    fallback_minutes: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "structure_id", _valid_id(self.structure_id))
        if self.fallback_minutes is not None:
            if type(self.fallback_minutes) is not int or self.fallback_minutes < 0:
                raise ValueError(
                    "fallback_minutes must be a nonnegative integer or None"
                )
        for name in ("active", "draft"):
            rule = getattr(self, name)
            if rule is None:
                continue
            if not isinstance(rule, dict):
                raise ValueError(f"{name} must be a predicate object or None")
            problem = validate_rule(rule, None)
            if problem:
                raise ValueError(f"{name} rule is invalid: {problem}")
            object.__setattr__(self, name, copy.deepcopy(rule))

    def as_dict(self) -> dict[str, Any]:
        return {
            "structure_id": self.structure_id,
            "active": copy.deepcopy(self.active),
            "draft": copy.deepcopy(self.draft),
            "fallback_minutes": self.fallback_minutes,
        }


@dataclass(frozen=True)
class RulesRecord:
    """The full persisted Capacities rules record for one space."""

    space_id: str
    structures: tuple[RuleStructureRecord, ...] = ()
    revision: int = 0
    version: int = RULES_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if type(self.version) is not int or self.version != RULES_SCHEMA_VERSION:
            raise ValueError(f"unsupported version {self.version!r}")
        if type(self.revision) is not int or self.revision < 0:
            raise ValueError("revision must be a nonnegative integer")
        object.__setattr__(self, "space_id", _valid_id(self.space_id))
        structures = tuple(self.structures)
        seen: set[str] = set()
        for record in structures:
            if not isinstance(record, RuleStructureRecord):
                raise ValueError("structures must contain RuleStructureRecord")
            if record.structure_id in seen:
                raise ValueError(f"duplicate structure id {record.structure_id!r}")
            seen.add(record.structure_id)
        object.__setattr__(self, "structures", structures)

    def structure(self, structure_id: str) -> RuleStructureRecord | None:
        for record in self.structures:
            if record.structure_id == structure_id:
                return record
        return None

    def effective_rule(self, structure_id: str) -> dict[str, Any] | None:
        """The active rule for a structure, or ``None`` when none is stored.

        ``None`` is the compatibility seam the next slice fills: a structure
        with no active rule must be distinguishable from a structure whose
        stored rule is ``{"all": []}`` (which matches everything)."""
        record = self.structure(structure_id)
        return None if record is None else record.active

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "revision": self.revision,
            "space_id": self.space_id,
            "structures": [record.as_dict() for record in self.structures],
        }


@dataclass(frozen=True)
class RuleSaveResult:
    """The outcome of one :func:`save_rule` call.

    ``valid`` is False when the rule could not be activated; ``reason`` is the
    bounded validation message and ``active`` is the unchanged prior rule."""

    structure_id: str
    valid: bool
    reason: str | None
    active: dict[str, Any] | None
    draft: dict[str, Any] | None
    fallback_minutes: int | None
    revision: int


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

def rules_path() -> Path:
    """The store path: ``<app home>/state/capacities-rules.json``.

    Machine-local, never a vault path, and resolved through
    ``app_config.state_dir()`` so ``TDTB_HOME`` relocates it in tests."""
    return app_config.state_dir() / STATE_FILENAME


def lock_path() -> Path:
    """The lock-file path, beside the record in the app home."""
    return app_config.state_dir() / STATE_LOCK_FILENAME


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def schema_from_definitions(definitions: Any) -> dict[str, str]:
    """Normalize discovered property definitions to ``{property_id: kind}``.

    Accepts the adapter's ``_definitions`` output (a mapping of property id to
    a definition carrying ``id`` and ``type``), a raw definition list, or an
    already-normalized ``{property_id: kind}`` mapping. Unknown shapes are
    ignored, so a partial discovery yields a partial schema rather than an
    error."""
    return _normalize_schema(definitions) or {}


def _normalize_schema(schema: Any) -> dict[str, str] | None:
    if schema is None:
        return None
    normalized: dict[str, str] = {}
    if isinstance(schema, Mapping):
        for key, value in schema.items():
            if isinstance(value, str):
                normalized[str(key)] = value.strip()
                continue
            if isinstance(value, Mapping):
                prop_id = value.get("id") or key
                kind = value.get("type") or value.get("kind")
            else:
                prop_id, kind = key, None
            prop_id = str(prop_id).strip()
            if not prop_id:
                continue
            normalized[prop_id] = "" if kind is None else str(kind).strip()
        return normalized
    if isinstance(schema, (list, tuple)):
        for definition in schema:
            if not isinstance(definition, Mapping):
                continue
            prop_id = definition.get("id") or definition.get("structureId")
            if not isinstance(prop_id, str) or not prop_id.strip():
                continue
            kind = definition.get("type")
            normalized[prop_id.strip()] = "" if kind is None else str(kind).strip()
        return normalized
    return normalized


def validate_rule(rule: Any, schema: Any = None) -> str | None:
    """Return a bounded reason when ``rule`` is invalid, else ``None``.

    When ``schema`` is supplied (discovered property definitions or an already
    normalized ``{property_id: kind}`` mapping), every referenced property
    must exist and every operator must be compatible with the property kind.
    With ``schema=None`` only the predicate's shape is checked — that is what
    lets the store hold a draft that references a removed property (AE21).

    Never raises for a merely invalid rule."""
    kinds = _normalize_schema(schema)
    return _validate_node(rule, kinds, "rule")


def _reason(message: str) -> str:
    return " ".join(str(message).split())[:MAX_REASON]


def _validate_node(node: Any, kinds: dict[str, str] | None, path: str) -> str | None:
    if not isinstance(node, Mapping):
        return _reason(f"{path} must be a predicate object")

    combinators = [key for key in ("all", "any", "not") if key in node]
    has_leaf = "prop" in node or "op" in node
    if combinators and has_leaf:
        return _reason(f"{path} mixes a combinator with a leaf")
    if len(combinators) > 1:
        return _reason(f"{path} must use exactly one of all/any/not")

    if combinators:
        key = combinators[0]
        value = node[key]
        if key == "not":
            if not isinstance(value, Mapping):
                return _reason(f"{path}.not must be a predicate object")
            return _validate_node(value, kinds, f"{path}.not")
        if not isinstance(value, list):
            return _reason(f"{path}.{key} must be an array of predicates")
        for index, child in enumerate(value):
            problem = _validate_node(child, kinds, f"{path}.{key}[{index}]")
            if problem:
                return problem
        return None

    if not has_leaf:
        return _reason(f"{path} is not a predicate (needs all/any/not or prop+op)")

    prop = node.get("prop")
    if not isinstance(prop, str) or not prop.strip():
        return _reason(f"{path}.prop must be a non-empty string")

    op = node.get("op")
    if op == "matches":
        return _reason(f"{path}.op 'matches' is not supported")
    if op not in OPS:
        return _reason(f"{path}.op must be one of {sorted(OPS)} (got {op!r})")

    values = node.get("values")
    if op in VALUE_OPS:
        if not isinstance(values, list) or not values:
            return _reason(
                f"{path}.values must be a non-empty array for op {op!r}"
            )
    elif values is not None and not isinstance(values, list):
        return _reason(f"{path}.values must be an array when present")

    if kinds is not None:
        if prop not in kinds:
            return _reason(f"{path} references unknown property {prop!r}")
        kind = kinds[prop]
        if op in NUMBER_OPS and kind not in NUMBER_KINDS:
            return _reason(
                f"{path} op {op!r} requires a numeric property; "
                f"{prop!r} is {kind or 'unknown'!r}"
            )
        if op in DATE_OPS and kind not in DATE_KINDS:
            return _reason(
                f"{path} op {op!r} requires a date property; "
                f"{prop!r} is {kind or 'unknown'!r}"
            )
        if op in EQUALITY_OPS and kind not in VALUE_KINDS:
            return _reason(
                f"{path} op {op!r} requires a value-bearing property; "
                f"{prop!r} is {kind or 'unknown'!r}"
            )
    return None


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------

def evaluate(rule: Any, record: Any, *, logical_day: str) -> Evaluation:
    """Evaluate ``rule`` against ``record`` with Kleene three-state logic.

    ``logical_day`` resolves a ``$today`` value token exactly as
    :func:`producer_rules._resolve_value` does, so a date comparison against
    the run's logical day keeps working."""
    return _evaluate_node(rule, record, logical_day)


def _evaluate_node(node: Any, record: Any, logical_day: str) -> Evaluation:
    if not isinstance(node, Mapping):
        raise ValueError("rule predicate must be a mapping")

    if "all" in node:
        children = node["all"]
        if not isinstance(children, list):
            raise ValueError("'all' must be an array of predicates")
        results = [_evaluate_node(child, record, logical_day) for child in children]
        if any(result is NO_MATCH for result in results):
            return NO_MATCH
        if all(result is MATCH for result in results):
            return MATCH
        return UNKNOWN

    if "any" in node:
        children = node["any"]
        if not isinstance(children, list):
            raise ValueError("'any' must be an array of predicates")
        results = [_evaluate_node(child, record, logical_day) for child in children]
        if any(result is MATCH for result in results):
            return MATCH
        if all(result is NO_MATCH for result in results):
            return NO_MATCH
        return UNKNOWN

    if "not" in node:
        return _NEGATE[_evaluate_node(node["not"], record, logical_day)]

    return _evaluate_leaf(node, record, logical_day)


def _evaluate_leaf(node: Mapping[str, Any], record: Any, logical_day: str) -> Evaluation:
    prop = node.get("prop")
    op = node.get("op")
    if not isinstance(prop, str) or not prop:
        raise ValueError("leaf predicate needs a non-empty 'prop'")

    actual = _lookup(record, prop)

    # Presence predicates are decided from the record alone.
    if op == "exists":
        return MATCH if actual is not _MISSING else NO_MATCH
    if op == "truthy":
        return MATCH if actual is not _MISSING and bool(actual) else NO_MATCH

    # A value comparison on a property the record does not CARRY is UNKNOWN,
    # not false: an under-evaluated object is surfaced, never silently omitted.
    if actual is _MISSING:
        return UNKNOWN

    values = [_resolve_value(value, logical_day) for value in node.get("values") or []]
    if op in VALUE_OPS and not values:
        return UNKNOWN

    if op == "eq":
        return MATCH if actual == values[0] else NO_MATCH
    if op == "in":
        if isinstance(actual, (list, tuple)):
            return MATCH if any(item in values for item in actual) else NO_MATCH
        return MATCH if actual in values else NO_MATCH
    if op in NUMBER_OPS:
        left, right = _as_number(actual), _as_number(values[0])
        if left is None or right is None:
            return NO_MATCH
        return MATCH if (left < right if op == "lt" else left > right) else NO_MATCH
    if op in DATE_OPS:
        left, right = _as_date(actual), _as_date(values[0])
        if left is None or right is None:
            return NO_MATCH
        return MATCH if (left < right if op == "before" else left > right) else NO_MATCH

    raise ValueError(f"unsupported rule operator {op!r}")


# ---------------------------------------------------------------------------
# Strict decoding
# ---------------------------------------------------------------------------

def _valid_id(value: Any) -> str:
    """Validate a non-empty, whitespace-free identifier string."""
    if not isinstance(value, str) or not value:
        raise ValueError(f"{value!r} is not a valid id")
    if value != value.strip() or any(char.isspace() for char in value):
        raise ValueError(f"{value!r} is not a valid id")
    return value


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """``object_pairs_hook`` that rejects duplicate keys at every level."""
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def _decode_structure(row: Any) -> RuleStructureRecord:
    if not isinstance(row, dict):
        raise RulesFormatError(
            "capacities rules structure record must be a JSON object"
        )
    if set(row) - _STRUCTURE_KEYS or not _STRUCTURE_REQUIRED_KEYS <= set(row):
        raise RulesFormatError(
            "capacities rules structure record has unknown or missing keys"
        )
    try:
        return RuleStructureRecord(
            structure_id=row["structure_id"],
            active=row.get("active"),
            draft=row.get("draft"),
            fallback_minutes=row.get("fallback_minutes"),
        )
    except (ValueError, TypeError) as exc:
        raise RulesFormatError(
            "capacities rules structure record is malformed"
        ) from exc


def _decode(data: Any) -> RulesRecord:
    if not isinstance(data, dict):
        raise RulesFormatError("capacities rules record must be a JSON object")
    if set(data) != _TOP_KEYS:
        raise RulesFormatError(
            "capacities rules record has unknown or missing top-level keys"
        )
    version = data["version"]
    if type(version) is not int or version != RULES_SCHEMA_VERSION:
        raise RulesFormatError(
            f"capacities rules record version {version!r} is unsupported"
        )
    revision = data["revision"]
    if type(revision) is not int or revision < 0:
        raise RulesFormatError(
            "capacities rules record revision must be a nonnegative integer"
        )
    structures_raw = data["structures"]
    if not isinstance(structures_raw, list):
        raise RulesFormatError("capacities rules record structures must be a list")
    structures = tuple(_decode_structure(row) for row in structures_raw)
    try:
        return RulesRecord(
            space_id=data["space_id"],
            structures=structures,
            revision=revision,
            version=version,
        )
    except ValueError as exc:
        raise RulesFormatError("capacities rules record is malformed") from exc


def _load_strict(path: Path) -> RulesRecord:
    """Strict read of an existing file. Raises on malformed/unsupported data
    or I/O failure — never repairs, defaults, or erases."""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RulesStoreError("capacities rules record is unreadable") from exc
    try:
        data = json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
    except ValueError as exc:
        raise RulesFormatError(
            "capacities rules record file is malformed"
        ) from exc
    return _decode(data)


# ---------------------------------------------------------------------------
# Locking + atomic write
# ---------------------------------------------------------------------------

def _store_lock(key: str | Path):
    return capacities_cache_io.store_lock(key)


def _acquire_path_lock(path: str | Path):
    return capacities_cache_io.acquire_path_lock(path)


def _release_lock_file(fh: Any) -> None:
    capacities_cache_io.release_lock_file(fh)


def _atomic_write_json(path: str | Path, data: dict[str, Any]) -> None:
    capacities_cache_io.atomic_write_json(path, data)


def _lock_beside(target: Path) -> Path:
    return target.parent / STATE_LOCK_FILENAME


# ---------------------------------------------------------------------------
# Public store API
# ---------------------------------------------------------------------------

def empty_rules(space_id: str) -> RulesRecord:
    """The empty default rule set for a space (creating nothing)."""
    return RulesRecord(space_id=space_id, structures=())


def load_rules(space_id: str, *, path: str | Path | None = None) -> RulesRecord:
    """Read the persisted rule set for ``space_id``.

    An absent file returns the empty default WITHOUT creating anything — the
    silent, opt-in "no Capacities rules configured" signal. Malformed or
    unsupported storage raises :class:`RulesFormatError`; an unreadable file
    raises :class:`RulesStoreError`. Reads never write.

    Reads are space-scoped: a document whose ``space_id`` differs from the
    requested space yields the empty default rather than another space's
    rules."""
    target = Path(path) if path is not None else rules_path()
    try:
        exists = target.is_file()
    except OSError as exc:
        raise RulesStoreError("capacities rules record is unreadable") from exc
    if not exists:
        return empty_rules(space_id)
    record = _load_strict(target)
    if record.space_id != space_id:
        return empty_rules(space_id)
    return record


def effective_rule(
    space_id: str, structure_id: str, *, path: str | Path | None = None
) -> dict[str, Any] | None:
    """The active rule for ``structure_id``, or ``None`` when none is stored.

    The derived-from-legacy-controls compatibility path is deliberately NOT
    part of this slice; this exposes the ``None`` so a later slice can fill
    it."""
    return load_rules(space_id, path=path).effective_rule(structure_id)


def _current_for_save(space_id: str, target: Path) -> RulesRecord | None:
    """The stored record when it belongs to ``space_id``; else ``None``.

    A document for a different space is treated as absent so a save for the
    requested space does not read or overwrite another space's rules."""
    try:
        exists = target.is_file()
    except OSError as exc:
        raise RulesStoreError("capacities rules record is unreadable") from exc
    if not exists:
        return None
    record = _load_strict(target)
    if record.space_id != space_id:
        return None
    return record


def _upsert_structure(
    current: RulesRecord | None, entry: RuleStructureRecord
) -> list[RuleStructureRecord]:
    """Return ``current``'s structures with ``entry`` replacing (or joining)
    the entry for its structure id, preserving the other structures' order."""
    structures: list[RuleStructureRecord] = []
    replaced = False
    for record in current.structures if current is not None else ():
        if record.structure_id == entry.structure_id:
            structures.append(entry)
            replaced = True
        else:
            structures.append(record)
    if not replaced:
        structures.append(entry)
    return structures


def save_rule(
    space_id: str,
    structure_id: str,
    rule: dict[str, Any],
    *,
    fallback_minutes: int | None = None,
    expected_revision: int = 0,
    schema: Any = None,
    path: str | Path | None = None,
) -> RuleSaveResult:
    """Validate and persist one per-type rule.

    A VALID rule becomes ``active`` (and is also recorded as ``draft``). An
    INVALID rule is stored as ``draft`` only, the previously active rule is
    untouched, and the returned :class:`RuleSaveResult` names the reason
    (AE21/R33). ``fallback_minutes`` is written as supplied (``None`` clears
    it).

    A stale ``expected_revision`` raises :class:`RulesConflictError` and
    preserves the original bytes. The saved document's ``revision`` is the
    stored revision plus one."""
    if type(expected_revision) is not int or expected_revision < 0:
        raise ValueError("expected_revision must be a nonnegative integer")
    if fallback_minutes is not None and (
        type(fallback_minutes) is not int or fallback_minutes < 0
    ):
        raise ValueError("fallback_minutes must be a nonnegative integer or None")
    _valid_id(space_id)
    _valid_id(structure_id)
    if not isinstance(rule, dict):
        raise ValueError("rule must be a predicate object")

    problem = validate_rule(rule, schema)
    valid = problem is None

    target = Path(path) if path is not None else rules_path()
    with _store_lock(target):
        fh = _acquire_path_lock(_lock_beside(target))
        try:
            current = _current_for_save(space_id, target)
            current_revision = current.revision if current is not None else 0
            if current_revision != expected_revision:
                raise RulesConflictError(
                    expected_revision=expected_revision,
                    current_revision=current_revision,
                )
            existing = current.structure(structure_id) if current is not None else None
            previous_active = existing.active if existing is not None else None
            # RuleStructureRecord validates and deep-copies both rules, so the
            # caller's dict is never aliased into the record.
            entry = RuleStructureRecord(
                structure_id=structure_id,
                active=rule if valid else previous_active,
                draft=rule,
                fallback_minutes=fallback_minutes,
            )
            structures = _upsert_structure(current, entry)
            saved = RulesRecord(
                space_id=space_id,
                structures=structures,
                revision=current_revision + 1,
            )
            _atomic_write_json(target, saved.as_dict())
        finally:
            _release_lock_file(fh)

    return RuleSaveResult(
        structure_id=structure_id,
        valid=valid,
        reason=problem,
        active=entry.active,
        draft=entry.draft,
        fallback_minutes=entry.fallback_minutes,
        revision=saved.revision,
    )
