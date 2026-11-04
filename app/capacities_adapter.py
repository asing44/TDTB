"""Application-owned Capacities REST source adapter.

The adapter is deliberately contract-driven: structure definitions and
per-structure mappings are supplied by TDTB configuration, candidate objects
are enumerated with bounded pagination, and eligibility is evaluated locally
from typed properties.  It does not use the Capacities MCP server, title-based
matching, or a generic synchronization model. The optional settings-declared
``Assigned On`` marker is READ-ONLY: a date/datetime property whose civil
date falls on or before the read's logical day is source-assigned, and TDTB
never writes it.

Status vocabulary has exactly ONE resolver (:func:`_status_resolution`) and
exactly ONE live-set reader (:func:`_label_identifiers`). A type's open
vocabulary is the requested open names resolved against the options
Capacities currently defines, compared by label NAME, so a renamed option id
with an unchanged name still matches while a renamed label drops out. When a
type has no resolvable open vocabulary the mapped status decides nothing: the
row is kept as an unassigned review candidate and the type is reported once
per read, never silently closed.
"""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field, replace
from datetime import date, datetime
import hashlib
import json
from typing import Any, Callable, Protocol, Sequence
import uuid

from capacities_assignment import (
    MODE_ASSIGNED,
    MODE_ASSIGNED_ON,
    AssignmentCandidate,
    AssignmentSettings,
    evaluate_assignment,
)
import capacities_rules
from producer_rules import _capacities_predicate_record
import tag_exclusions

try:
    import httpx as _httpx
except ImportError:  # pragma: no cover — the app declares httpx as a dependency
    _PROVIDER_TIMEOUT_TYPES: tuple[type[BaseException], ...] = (TimeoutError,)
else:
    _PROVIDER_TIMEOUT_TYPES = (TimeoutError, _httpx.TimeoutException)


class CapacitiesContractError(RuntimeError):
    """The provider response or configured mapping is unsafe to consume.

    ``detail`` is an optional short, closed-format diagnostic assembled only
    from configured structure/property ids and code-authored phrases. It is
    set for configuration-contract failures so a refresh job can name the
    offending id without echoing the exception message (which may carry
    provider payload content). Response-shape failures leave it ``None``.
    """

    def __init__(self, message: str = "", *, detail: str | None = None) -> None:
        super().__init__(message)
        self.detail = detail


class CapacitiesWriteError(RuntimeError):
    """A guarded completion update could not be verified."""


class CapacitiesRateLimited(RuntimeError):
    """The provider refused the request because a rate limit was exceeded.

    The live API allows 30 requests per minute, and because its structure
    listing carries no typed properties, every property-based decision costs a
    per-object content read. Exceeding the limit is an expected operating
    condition rather than a defect, so it is modelled separately: the read
    degrades to the objects already evaluated instead of failing outright.

    ``retry_after`` carries the transport's ``Retry-After`` value when the
    provider supplied one; it is pacing metadata only and never a credential
    or response body.
    """

    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class CapacitiesProvider(Protocol):
    """Small provider seam used by both the REST client and deterministic fakes."""

    def fetch_structures(self) -> Sequence[dict[str, Any]] | dict[str, Any]: ...

    def list_objects(self, structure_id: str, cursor: str | None = None) -> dict[str, Any]: ...

    def get_object(self, object_id: str) -> dict[str, Any]: ...

    def patch_object(self, object_id: str, properties: dict[str, Any]) -> dict[str, Any]: ...


@dataclass(frozen=True)
class StructureMapping:
    """Explicit property ownership for one Capacities structure.

    A custom structure normally requires ``assignment_property`` plus a
    non-empty ``assignment_values`` so an object cannot enter TDTB merely
    because it exists in a Capacities space. Alternatively — and this is what
    lets a structure be *offered* in Settings before it is enabled — a custom
    structure may map ``open_status_property`` instead. Mapping a status
    property includes nothing by itself: the evaluator still requires an
    Active-enabled structure or a source assignment, so an enumerated custom
    structure stays silent until the user enables it. Native ``RootTask``/``Task``
    structures have no writable source Assigned marker, so they rely on the
    native Auto rule and must map their status property. A mapping that still
    supplies ``assignment_property`` keeps the legacy explicit-marker seam.

    ``date_property`` maps the source due date and stays a day-projection
    filter; a multi-day range on it is a window described by its end. A source
    assignment (boolean marker or Assigned On date) outranks that filter.
    ``deadline_property`` maps the source deadline the evaluator
    horizon-checks. The mapped ``open_status_property`` supplies both the
    open/closed safety signal and the status token the native Auto rule reads.
    Optional fields are capabilities, not guesses: absent mappings never cause
    TDTB to write a similarly named source field.
    """

    structure_id: str
    assignment_property: str | None = None
    assignment_values: frozenset[str] = frozenset()
    title_property: str = "title"
    date_property: str | None = None
    deadline_property: str | None = None
    open_status_property: str | None = None
    #: The REQUESTED open status names this type admits on, when the caller
    #: supplies no version-2 settings document. It is never a live vocabulary
    #: snapshot: the effective open set is derived at projection time as
    #: requested-open ∩ the live label set, compared by label NAME.
    open_status_values: frozenset[str] = frozenset()
    duration_property: str | None = None
    completion_property: str | None = None
    completion_value: str | None = None
    #: Builder-resolved settings declaration: the raw Capacities property id
    #: whose boolean ``true`` means "assigned at TDTB level". When set, the
    #: adapter reads this instead of ``assignment_property`` /
    #: ``assignment_values``. It deliberately does NOT participate in
    #: ``_structure_can_contribute`` — the declaration is an assignment check
    #: only and must never gate enumeration. A declaration the structure does
    #: not define is ignored with one bounded warning; the mapping's own
    #: marker then applies as the fallback.
    assigned_property: str | None = None
    #: Builder-resolved settings declaration: the raw Capacities DATE/DATETIME
    #: property id whose civil date (in the app's local timezone) must fall on
    #: or before the read's logical day for the object to read source-assigned.
    #: When set it takes PRECEDENCE over ``assigned_property`` for this
    #: structure: the boolean marker is ignored entirely. Unlike
    #: ``assigned_property``, a valid declaration is itself an enumeration path
    #: (``_structure_can_contribute``): the structure may have no legacy
    #: marker, no Active enable, and no stored rule, so its objects must still
    #: be listed and hydrated for the date property to be readable. The marker
    #: value then decides object by object; an empty or future date follows the
    #: ordinary unassigned/pool rules. A declaration the structure does not
    #: define, or one that is not date-typed, is dropped during contract
    #: resolution and therefore never enumerates anything. The marker is
    #: READ-ONLY — TDTB never writes it.
    assigned_on_property: str | None = None
    #: Diagnostic-only counterpart: the source record's own ``Assigned On``
    #: value BEFORE the builder resolved a settings declaration onto
    #: ``assigned_on_property``. When both are set and differ, the declaration
    #: shadowed the record mapping and ``_ensure_contract`` emits one bounded
    #: warning. Never used for precedence.
    assigned_on_record_property: str | None = None


@dataclass(frozen=True)
class CapacitiesConfig:
    space_id: str
    mappings: tuple[StructureMapping, ...]
    max_pages: int = 20
    #: Shared object-content cache. Content already cached is reused without
    #: spending the read budget, so repeated refreshes converge on full
    #: coverage instead of re-reading the same first N objects every time and
    #: starving the rest.
    content_cache: Any = None
    #: Content-read budget for one read. The live API allows 30 requests per
    #: minute and its structure listing carries no typed properties, so every
    #: property-based decision costs one content read. A cold read spends this
    #: plus one listing per structure (20 + 3 = 23), leaving room for a second
    #: refresh inside the same minute before the window fills; objects left
    #: unevaluated are reported, never silently dropped. Warm reads are nearly
    #: free because content is served from the shared cache.
    max_content_reads: int = 20
    #: Assignment policy seam. Defaults to the default-enabled policy; the
    #: settings store owns persistence and the caller passes it in. The adapter
    #: never reads local files or credentials itself.
    assignment_settings: AssignmentSettings = field(default_factory=AssignmentSettings)
    #: Optional version-2 settings document (``capacities_settings
    #: .CapacitiesSettings``), duck-typed: only ``open_statuses_for(sid)`` and
    #: ``status_admission_enabled(sid)`` are read, so the adapter still opens
    #: no file. When supplied it is the per-type status vocabulary authority —
    #: a type's own ``statuses``, ``()`` for disabled status admission, else
    #: the inherited ``statuses.open``. When absent the mapping's
    #: ``open_status_values`` carries the same requested names.
    settings: Any = None
    #: Optional stored per-type rules record (``capacities_rules.RulesRecord``)
    #: for this space. When a structure has an ACTIVE stored rule, that rule is
    #: the SINGLE eligibility authority for its objects: the legacy
    #: ``evaluate_assignment`` decision is not ANDed in. ``None`` — or a record
    #: for another space, or a draft-only entry — keeps the legacy decision
    #: unchanged (KTD5).
    rules: capacities_rules.RulesRecord | None = None
    #: Optional pre-selection tag-exclusion policy
    #: (``tag_exclusions.ExclusionPolicy``). When supplied, projected
    #: candidates are filtered through the shared matcher after projection, on
    #: both the source-assigned and unassigned surfaces; ``None`` keeps the
    #: legacy projection unchanged. The adapter never reads settings storage or
    #: a credential itself.
    exclusion_policy: tag_exclusions.ExclusionPolicy | None = None


@dataclass(frozen=True)
class StructureEnumeration:
    """One structure's raw listing rows for the paced coordinator.

    ``complete=False`` means pagination did not finish (page bound, repeated
    cursor, or a malformed page). An incomplete enumeration is explicit
    evidence that a type's membership cannot be trusted, never a truncated
    success; the rows collected so far are diagnostic only.
    """

    structure_id: str
    rows: tuple[dict[str, Any], ...]
    pages: int
    complete: bool
    reason: str | None = None


@dataclass
class CapacitiesReadResult:
    items: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    pages: int = 0
    #: Run-local coverage accounting over *distinct* listed identities. These
    #: are deliberately not item counts: an evaluated object may still be
    #: filtered out as ineligible, and ``source_counts.capacities`` keeps
    #: counting accepted items only. ``deferred`` covers rows skipped by the
    #: content-read budget or a provider refusal; ``malformed`` covers rows
    #: that were read but could not be projected, and is never folded into
    #: either of the other two.
    evaluated: int = 0
    deferred: int = 0
    malformed: int = 0
    #: One entry per malformed identity, for the additive intake block. The
    #: count above stays the coverage authority; this is the named surface.
    skipped: tuple[SkippedObject, ...] = ()
    #: Per-structure drop accounting for this read: structure id -> {reason
    #: code: count of dropped objects}. Every gate that can drop a projected
    #: row without failing it counts here, so the run reports what it removed
    #: instead of losing it silently. Sorted on the way out, deduped per
    #: identity, and content-free: counts and closed reason codes only.
    drop_tally: dict[str, dict[str, int]] = field(default_factory=dict)
    #: Structures skipped before any listing (structure id -> reason code).
    #: The count of their objects is deliberately absent: nothing was listed,
    #: so no count exists and none is invented.
    structures_not_read: dict[str, str] = field(default_factory=dict)
    #: Per-structure REVIEW accounting for this read: objects KEPT as unassigned
    #: review candidates (structure id -> {reason code: count}). It is not a
    #: drop tally: these objects stay on the pool surface. It exists so an
    #: undecidable status vocabulary is reported once per type instead of once
    #: per row.
    review_tally: dict[str, dict[str, int]] = field(default_factory=dict)


@dataclass(frozen=True)
class CompletionOutcome:
    status: str
    object_id: str
    completion_property: str | None = None
    before_property: dict[str, Any] | None = None
    after_fingerprint: str | None = None


class _MalformedObject(ValueError):
    """A listed object could not be projected into a plan row.

    ``reason`` is the small fixed operator-facing bucket for the cause; the
    message stays the existing raw diagnostic (the text the warning prints
    after ``not plannable:``). ``None`` means the cause was not classified and
    maps to the ``Not plannable`` fallback.
    """

    def __init__(self, message: str, *, reason: str | None = None) -> None:
        super().__init__(message)
        self.reason = reason


#: The fixed skip-reason vocabulary the additive intake block reports.
_SKIP_REASON_TITLE = "No title"
_SKIP_REASON_DURATION = "Duration is not a number"
_SKIP_REASON_DATE = "Start date is not valid"
_SKIP_REASON_UNREADABLE = "Unreadable object"
_SKIP_REASON_UNKNOWN = "Not plannable"


@dataclass(frozen=True)
class SkippedObject:
    """One malformed listed object, named for the operator.

    ``title`` is a safe display value only: a non-empty trimmed string capped
    at 120 chars, or ``None`` when neither the mapped title property nor the
    listed object's own title can be read. ``detail`` is the raw projection
    failure message, unchanged from the warning.
    """

    identity: str
    title: str | None
    reason: str
    detail: str
    structure_id: str | None
    structure_name: str | None
    #: Find-it clues so the operator can locate a title-less object in
    #: Capacities. All three are best-effort and additive; extraction never
    #: raises and an unreadable shape degrades to the empty value.
    labels: tuple[str, ...] = ()
    updated_at: str | None = None
    hint: str | None = None


def _text(value: Any) -> str:
    return str(value or "").strip()


def _normalized(value: Any) -> str:
    return " ".join(_text(value).casefold().split())


def _status_token(tokens: set[str], settings: AssignmentSettings) -> str | None:
    """Pick the normalized status token the native Auto rule reads.

    Prefers a token the evaluator treats as Active so a multi-value label still
    matches; otherwise returns the lexicographically first token so the value
    is deterministic rather than set-order dependent.
    """
    if not tokens:
        return None
    active = {_normalized(value) for value in settings.active_statuses}
    ordered = sorted(tokens)
    for token in ordered:
        if token in active:
            return token
    return ordered[0]


def _structure_id(row: dict[str, Any]) -> str:
    return _text(row.get("id") or row.get("structureId"))


def _detail_token(value: Any) -> str:
    """One identifier-shaped token for a bounded contract diagnostic."""
    token = _text(value)
    if not token:
        return "?"
    return "".join(
        char if char.isalnum() or char in "_.-" else "?" for char in token[:64]
    )


def _contract_detail(
    *, structure: Any = None, prop: Any = None, phrase: str | None = None
) -> str:
    """Closed-format detail for a configured contract failure.

    Only configured structure/property identifiers and code-authored phrases
    are ever included, so the detail can be rendered in a job warning without
    leaking source content, titles, or values. Response-shape failures do not
    pass a detail at all.
    """
    parts: list[str] = []
    if phrase:
        parts.append(phrase)
    if structure is not None:
        parts.append(f"structure={_detail_token(structure)}")
    if prop is not None:
        parts.append(f"property={_detail_token(prop)}")
    return " ".join(parts)


def _definitions(row: dict[str, Any]) -> dict[str, dict[str, Any]]:
    raw = row.get("propertyDefinitions")
    if isinstance(raw, dict):
        values = []
        for prop_id, definition in raw.items():
            if isinstance(definition, dict):
                values.append({"id": prop_id, **definition})
    elif isinstance(raw, list):
        values = raw
    else:
        raise CapacitiesContractError(
            f"structure {_structure_id(row)!r} has malformed propertyDefinitions"
        )
    result: dict[str, dict[str, Any]] = {}
    for definition in values:
        if not isinstance(definition, dict) or not _structure_id(definition):
            raise CapacitiesContractError(
                f"structure {_structure_id(row)!r} has malformed property definition"
            )
        prop_id = _structure_id(definition)
        if prop_id in result:
            raise CapacitiesContractError(
                f"structure {_structure_id(row)!r} repeats property {prop_id!r}"
            )
        result[prop_id] = definition
    return result


#: Names a dead settings declaration may be recovered from. Recovery is by
#: NAME because Capacities lets an operator rename or recreate a property, which
#: invalidates a declaration that still names the old id. Matching is exact on
#: the normalized (casefolded, whitespace-collapsed) name so "Assigned",
#: "assigned", and "Assigned  To Me" all count, while "Assigned By" does not.
_ASSIGNED_NAMES = frozenset({"assigned", "assigned to me"})
_ASSIGNED_TYPES = frozenset({"boolean"})
_ASSIGNED_ON_NAMES = frozenset({"assigned on"})
_ASSIGNED_ON_TYPES = frozenset({"date", "datetime"})
#: Bound on the quoted property name a declaration warning may carry.
_PROPERTY_LABEL_MAX_CHARS = 40


def _single_property_by_name(
    definitions: dict[str, dict[str, Any]],
    names: frozenset[str],
    kinds: frozenset[str],
) -> str | None:
    """The one property whose type is in ``kinds`` and whose normalized name is
    in ``names``; ``None`` for zero or several.

    A name fallback is only safe when the structure offers exactly one
    candidate: several candidates mean any choice would be a guess, so the
    caller keeps its ignore-and-warn behaviour instead.
    """
    matches = [
        prop_id
        for prop_id, definition in definitions.items()
        if _text(definition.get("type")) in kinds
        and _normalized(definition.get("name")) in names
    ]
    return matches[0] if len(matches) == 1 else None


def _property_label(
    definitions: dict[str, dict[str, Any]], prop_id: str, default: str
) -> str:
    """The quoted, length-bounded display name of ``prop_id`` for a warning."""
    name = _text(definitions[prop_id].get("name"))[:_PROPERTY_LABEL_MAX_CHARS]
    return f"'{name or default}'"


def _label_identifiers(definition: Any) -> set[str]:
    """Normalized LIVE identifiers of one label property's ``labelSet``.

    The ONLY live-set reader in this module: a status option Capacities still
    defines is decided here and nowhere else. A member contributes its ``id``
    and its display ``name``, so a configured or carried value may legitimately
    be either form and ``_normalized`` comparison covers case and whitespace.
    The read never raises: a missing, empty, or malformed label set simply
    knows no live option identity, which is a real answer ("no live
    vocabulary") rather than a contract failure.
    """
    if not isinstance(definition, Mapping):
        return set()
    options = definition.get("labelSet")
    if not isinstance(options, list):
        return set()
    result: set[str] = set()
    for option in options:
        if not isinstance(option, dict):
            continue
        for key in ("id", "name"):
            ident = _text(option.get(key))
            if ident:
                result.add(_normalized(ident))
    return result


def _property_payload(prop: Any, prop_id: str) -> tuple[str, Any]:
    if not isinstance(prop, dict):
        raise _MalformedObject(
            f"property {prop_id!r} is not an object", reason=_SKIP_REASON_UNREADABLE
        )
    kind = _text(prop.get("type"))
    if not kind or kind not in prop:
        raise _MalformedObject(
            f"property {prop_id!r} has no typed payload", reason=_SKIP_REASON_UNREADABLE
        )
    return kind, prop[kind]


def _property_tokens(prop: Any, prop_id: str) -> set[str]:
    kind, payload = _property_payload(prop, prop_id)
    values: list[Any] = []
    if kind in {"label", "entity"}:
        if not isinstance(payload, list):
            raise _MalformedObject(
                f"property {prop_id!r} has a non-list {kind} payload",
                reason=_SKIP_REASON_UNREADABLE,
            )
        for value in payload:
            if isinstance(value, dict):
                values.extend(value.get(key) for key in ("id", "name") if value.get(key) is not None)
            else:
                values.append(value)
    elif kind in {"title", "text", "number", "boolean", "url", "richText"}:
        if isinstance(payload, dict) and "value" in payload:
            values.append(payload["value"])
        else:
            values.append(payload)
    elif kind == "date":
        if not isinstance(payload, dict):
            raise _MalformedObject(
                f"property {prop_id!r} has a non-object date payload",
                reason=_SKIP_REASON_UNREADABLE,
            )
        values.append(payload.get("start"))
    else:
        raise _MalformedObject(
            f"property {prop_id!r} has unsupported type {kind!r}",
            reason=_SKIP_REASON_UNREADABLE,
        )
    return {_normalized(value) for value in values if value is not None and _text(value)}


def _status_display_tokens(prop: Any, prop_id: str) -> set[str]:
    """Normalized status tokens keyed on the label display NAME.

    For a label/entity payload each option contributes its ``name``; the
    ``id`` is used ONLY when no name is present. This is what lets the native
    Active rule and the custom Active pull match ``Active`` even when the
    label id differs (the native Task's Active option is id ``in-progress``
    with name ``Active``). Non-label payloads fall back to the generic token
    extractor.
    """
    kind, payload = _property_payload(prop, prop_id)
    if kind not in {"label", "entity"}:
        return _property_tokens(prop, prop_id)
    if not isinstance(payload, list):
        raise _MalformedObject(
            f"property {prop_id!r} has a non-list {kind} payload",
            reason=_SKIP_REASON_UNREADABLE,
        )
    values: list[Any] = []
    for value in payload:
        if isinstance(value, dict):
            name = value.get("name")
            if name is not None and _text(name):
                values.append(name)
            elif value.get("id") is not None:
                values.append(value.get("id"))
        else:
            values.append(value)
    return {_normalized(value) for value in values if value is not None and _text(value)}


_RULE_TEXT_KINDS = frozenset({"title", "text", "url", "richText"})


def _rule_scalar(payload: Any) -> tuple[Any, bool]:
    """Unwrap an optional ``{"value": …}`` typed payload for a scalar kind.

    Returns ``(value, malformed)``. A dict without a ``value`` key is
    malformed; any other payload is used as-is. The rule path never
    stringifies the wrapper itself.
    """
    if isinstance(payload, dict):
        if "value" not in payload:
            return None, True
        return payload["value"], False
    return payload, False


def _rule_status_tokens(prop: Any, prop_id: str) -> tuple[set[str], bool]:
    """Strict ``(tokens, unreadable)`` extraction for rule-path classification.

    The legacy ``_property_tokens`` flattener normalizes every value with
    ``str()``, so a malformed member (``[{id: {…}}]``, ``[5]``, ``[[…]]``,
    ``[True]``, ``{foo: active}``) becomes a readable-looking non-open token
    and the object is silently classified closed. This rule-only helper
    validates the declared typed shape instead and reports whether any part
    of the payload could not be read as its declared type. A dict member with
    a present non-null ``id`` or ``name`` that is not a string is unreadable
    even when its peer is a valid string; valid string tokens are still
    retained. Tokens keep the
    legacy normalized forms for valid payloads (``True`` → ``"true"``,
    ``45`` → ``"45"``), and the falsy scalars the legacy path dropped
    (``False``, ``0``) still produce no token, so configured vocabularies
    are never re-guessed.
    """
    if not isinstance(prop, dict):
        return set(), True
    kind = _text(prop.get("type"))
    if not kind or kind not in prop:
        return set(), True
    payload = prop[kind]
    tokens: set[str] = set()
    unreadable = False
    if kind in {"label", "entity"}:
        if not isinstance(payload, list):
            return set(), True
        for member in payload:
            if isinstance(member, str):
                token = _normalized(member)
                if token:
                    tokens.add(token)
            elif isinstance(member, dict):
                readable = False
                for key in ("id", "name"):
                    value = member.get(key)
                    if isinstance(value, str):
                        readable = True
                        token = _normalized(value)
                        if token:
                            tokens.add(token)
                    elif value is not None:
                        unreadable = True
                if not readable:
                    unreadable = True
            else:
                unreadable = True
    elif kind in _RULE_TEXT_KINDS:
        value, malformed = _rule_scalar(payload)
        if malformed:
            unreadable = True
        elif value is not None:
            if not isinstance(value, str):
                unreadable = True
            else:
                token = _normalized(value)
                if token:
                    tokens.add(token)
    elif kind == "number":
        value, malformed = _rule_scalar(payload)
        if malformed or isinstance(value, bool) or not isinstance(value, (int, float)):
            unreadable = True
        else:
            token = _normalized(value)
            if token:
                tokens.add(token)
    elif kind == "boolean":
        value, malformed = _rule_scalar(payload)
        if malformed or not isinstance(value, bool):
            unreadable = True
        else:
            token = _normalized(value)
            if token:
                tokens.add(token)
    elif kind == "date":
        if not isinstance(payload, dict):
            return set(), True
        start = payload.get("start")
        if start is not None:
            if not isinstance(start, str):
                unreadable = True
            else:
                token = _normalized(start)
                if token:
                    tokens.add(token)
    else:
        return set(), True
    return tokens, unreadable


def _rule_status_state(resolution: StatusResolution) -> str:
    """The stored-rule status state for one resolution.

    The rule path consumes the same single resolution as the legacy path and
    only renames it: ``open``/``closed`` are decided, and every other outcome
    (``empty``, unreadable, dead members, no resolvable vocabulary) is UNKNOWN
    so the row stays an unassigned review candidate instead of being excluded
    as a known-closed status.
    """
    return {
        _STATUS_RESOLUTION_OPEN: "open",
        _STATUS_RESOLUTION_CLOSED: "closed",
    }.get(resolution.state, "unknown")


#: Status resolution outcomes for one mapped status payload.
_STATUS_RESOLUTION_EMPTY = "empty"
_STATUS_RESOLUTION_OPEN = "open"
_STATUS_RESOLUTION_CLOSED = "closed"
_STATUS_RESOLUTION_UNKNOWN = "unknown"


@dataclass(frozen=True)
class StatusVocabulary:
    """One type's status vocabulary, resolved against the live label set.

    ``requested`` is the configured open names (the per-type ``statuses``, else
    the inherited ``statuses.open``), ``live`` is the option identity set
    Capacities currently defines, and ``open`` is their intersection — the
    derived open vocabulary every decision uses. ``admission_enabled`` says
    whether the type consults status at all (``()`` declarations and the
    non-status modes do not), and ``resolvable`` asks the projection's real
    question: an enabled type whose requested names still exist in the live
    label set.

    The requested names come from the settings document when one is supplied;
    the mapping's ``open_status_values`` is only the fallback carrier for
    callers that build the adapter without settings.
    """

    requested: frozenset[str] = frozenset()
    open: frozenset[str] = frozenset()
    live: frozenset[str] = frozenset()
    admission_enabled: bool = False
    resolvable: bool = False


@dataclass(frozen=True)
class StatusResolution:
    """One mapped status payload resolved by the single resolver.

    ``state`` is one of the ``_STATUS_RESOLUTION_*`` outcomes. ``dead`` means
    every readable member names an option the live label set does not define
    (Capacities still sends the deleted option id, with that id repeated as
    its display name). ``no_vocabulary`` means the TYPE has no resolvable open
    vocabulary at all, so no member can be decided open or closed.

    Both flags are review conditions: the row stays an unassigned review
    candidate instead of being silently closed.
    """

    state: str = _STATUS_RESOLUTION_UNKNOWN
    dead: bool = False
    no_vocabulary: bool = False


#: A status payload that decides nothing: absent, unreadable, or ambiguous.
_STATUS_UNRESOLVED = StatusResolution(state=_STATUS_RESOLUTION_UNKNOWN)


def _status_legacy_tokens(payload: Sequence[Any]) -> set[str] | None:
    """Lenient id+name tokens for a payload with no live label set.

    Deliberately the pre-resolver reading (``_property_tokens`` semantics):
    the token set carries both the ``id`` and the ``name`` of each member and
    stringifies whatever it finds, because a structure that publishes no label
    set gives nothing to resolve against. ``None`` means the payload is not a
    member list at all.
    """
    tokens: set[str] = set()
    for member in payload:
        if isinstance(member, dict):
            for key in ("id", "name"):
                value = member.get(key)
                if value is not None and _text(value):
                    tokens.add(_normalized(value))
        elif _text(member):
            tokens.add(_normalized(member))
    return tokens


def _status_member_tokens(member: Any) -> tuple[set[str], set[str]] | None:
    """``(identity, name)`` tokens for one status payload member.

    ``identity`` carries both the ``id`` and the display ``name``: a member
    resolves against the live label set when EITHER form is still defined, so a
    renamed option id with an unchanged name still matches and a carrier id
    that still exists is not mistaken for a deleted option.

    ``name`` is the NAME-preferring comparison form (the ``id`` only when the
    member carries no name), exactly like ``_status_display_tokens``: that is
    what makes open-vocabulary membership a label-NAME question, so a renamed
    label drops out of the open set.

    ``None`` means the member cannot be read as a label reference at all (a
    non-string value, a nested structure, a scalar, or a non-string id/name),
    which is the same strictness ``_rule_status_tokens`` applies on the rule
    path.
    """
    if isinstance(member, str):
        token = _normalized(member)
        return ({token}, {token}) if token else None
    if not isinstance(member, dict):
        return None
    name = member.get("name")
    ident = member.get("id")
    identity: set[str] = set()
    for value in (name, ident):
        if value is None:
            continue
        if not isinstance(value, str) or not _text(value):
            return None
        token = _normalized(value)
        if token:
            identity.add(token)
    if not identity:
        return None
    preferred = _normalized(name) or _normalized(ident)
    return identity, {preferred} if preferred else identity
    if isinstance(member, str):
        token = _normalized(member)
        return {token} if token else None
    if not isinstance(member, dict):
        return None
    name = member.get("name")
    ident = member.get("id")
    for value in (name, ident):
        if value is not None and (not isinstance(value, str) or not _text(value)):
            return None
    token = _normalized(name) or _normalized(ident)
    return {token} if token else None


def _typed_status_tokens(prop: Any) -> set[str] | None:
    """Strict typed tokens for a NON-label status payload, or ``None``.

    A mapped status property can carry a typed scalar instead of a label list.
    ``None`` means the payload cannot be read as its declared type (or carries
    no token at all), which stays UNKNOWN rather than being guessed closed.
    """
    kind = _text(prop.get("type"))
    if not kind or kind not in prop:
        return None
    tokens, unreadable = _rule_status_tokens(prop, kind)
    if unreadable or not tokens:
        return None
    return tokens


def _status_resolution(
    prop: Any,
    vocabulary: StatusVocabulary,
) -> StatusResolution:
    """THE status resolver: one mapped payload against one type's vocabulary.

    ``vocabulary`` already carries the live label set (``_label_identifiers``)
    and the requested-open names resolved into it, so this function never
    reads configuration or the structures contract itself.

    The payload reads:

    * ``no_vocabulary`` — the type is enabled and publishes a live label set,
      but none of the requested open names still exists in it, so no member
      can be decided open or closed. Every row is then undecided, never
      closed;
    * ``empty`` — readable payload with no members (the shipped absent-signal
      rule stays with the caller);
    * ``open`` — at least one resolved member is in the open vocabulary;
    * ``closed`` — resolved members only, none open;
    * ``unknown`` — absent, unreadable, or every member dead.

    A mixed payload keeps the stronger resolved evidence: open + dead reads
    open, and resolved non-open + dead reads closed. Nothing is called dead
    without a live option identity to compare against: a structure that
    publishes no label set keeps the pre-resolver reading, where the payload's
    own tokens are compared with the requested open names.
    """
    if prop is None or not isinstance(prop, dict):
        return _STATUS_UNRESOLVED
    kind = _text(prop.get("type"))
    if not kind or kind not in prop:
        return _STATUS_UNRESOLVED
    payload = prop[kind]
    if not vocabulary.live:
        # No live label set is known, so nothing can be called dead and no
        # vocabulary verdict is possible. Keep the pre-resolver reading
        # verbatim: compare the payload's own tokens with the requested open
        # names. A non-label typed payload carries no label reference at all, so
        # its strict typed scalar is the token (the rule path's reading).
        if kind in {"label", "entity"}:
            if not isinstance(payload, list):
                return _STATUS_UNRESOLVED
            if not payload:
                return StatusResolution(state=_STATUS_RESOLUTION_EMPTY)
            tokens = _status_legacy_tokens(payload)
        else:
            tokens = _typed_status_tokens(prop)
        if not tokens:
            return _STATUS_UNRESOLVED
        return StatusResolution(
            state=(
                _STATUS_RESOLUTION_OPEN
                if tokens.intersection(vocabulary.requested)
                else _STATUS_RESOLUTION_CLOSED
            )
        )
    if not vocabulary.resolvable:
        # Type-level verdict: the live label set is known but no requested open
        # name still exists in it, so no member of this type can be decided.
        return StatusResolution(
            state=_STATUS_RESOLUTION_UNKNOWN, no_vocabulary=True
        )
    if kind not in {"label", "entity"}:
        # A typed scalar on a label-mapped status is still readable evidence;
        # it names no option, so it is compared with the requested names.
        tokens = _typed_status_tokens(prop)
        if not tokens:
            return _STATUS_UNRESOLVED
        return StatusResolution(
            state=(
                _STATUS_RESOLUTION_OPEN
                if tokens.intersection(vocabulary.requested)
                else _STATUS_RESOLUTION_CLOSED
            )
        )
    if not isinstance(payload, list):
        return _STATUS_UNRESOLVED
    if not payload:
        return StatusResolution(state=_STATUS_RESOLUTION_EMPTY)
    resolved = 0
    dead = 0
    open_members = 0
    for member in payload:
        parsed = _status_member_tokens(member)
        if parsed is None:
            return _STATUS_UNRESOLVED
        identity, name_tokens = parsed
        if identity.intersection(vocabulary.live):
            resolved += 1
            if name_tokens.intersection(vocabulary.open):
                open_members += 1
        else:
            dead += 1
    if not dead:
        return StatusResolution(
            state=(
                _STATUS_RESOLUTION_OPEN
                if open_members
                else _STATUS_RESOLUTION_CLOSED
            )
        )
    if not resolved:
        return StatusResolution(state=_STATUS_RESOLUTION_UNKNOWN, dead=True)
    return StatusResolution(
        state=(
            _STATUS_RESOLUTION_OPEN if open_members else _STATUS_RESOLUTION_CLOSED
        )
    )


def _classify_completion(
    prop: Any,
    prop_id: str,
    completion_value: str | None,
    open_values: frozenset[str] | None = None,
) -> str:
    """Unknown / closed classification for a mapped completion.

    A distinct completion field (``open_values is None``) has no vocabulary
    beyond the configured ``completion_value``: only that readable token is a
    known closed state, and every other value — including an absent, empty,
    or unreadable payload — is UNKNOWN rather than a guessed exclusion
    (R27/R29).

    A same-field completion (``open_values`` supplied, because the mapping
    shares the open-status property) checks the configured completed value
    FIRST, so a mixed ``[active, done]`` payload is excluded even though
    ``active`` intersects the open values. Readable completed evidence wins
    even when another member is unreadable; without it, any unreadable member
    or an empty payload is UNKNOWN, and only a fully readable payload falls
    back to the open vocabulary.
    """
    if prop is None:
        return "unknown"
    tokens, unreadable = _rule_status_tokens(prop, prop_id)
    if completion_value is not None and _normalized(completion_value) in tokens:
        return "closed"
    if open_values is None:
        return "unknown"
    if unreadable or not tokens:
        return "unknown"
    if tokens.intersection({_normalized(value) for value in open_values}):
        return "open"
    return "closed"


#: Conservative completed vocabulary for the open-status safety guard. A
#: structure's live label set can lag the stored mapping, so the settings
#: Active-status relief must never open a row whose status payload also carries
#: one of these readable completion tokens. The mapping's own
#: ``completion_value`` is added when it is configured.
_COMPLETED_STATUS_TOKENS = frozenset(
    {
        "done",
        "completed",
        "complete",
        "dropped",
        "cancelled",
        "canceled",
        "closed",
        "archived",
    }
)


def _completed_status_tokens(mapping: StructureMapping) -> frozenset[str]:
    """Normalized tokens that mean completed for one structure's status."""
    tokens = set(_COMPLETED_STATUS_TOKENS)
    if mapping.completion_value:
        tokens.add(_normalized(mapping.completion_value))
    return frozenset(tokens)


#: The modes that admit on their own source marker (an ``Assigned`` boolean or
#: an ``Assigned On`` date) instead of on the open status vocabulary. For these
#: modes the open list never decides anything, but a status the owner lists as
#: completed is still readable closed evidence.
_ASSIGNED_STATUS_MODES = frozenset({MODE_ASSIGNED, MODE_ASSIGNED_ON})


def _completed_status_names(settings: Any) -> frozenset[str]:
    """Normalized completed status NAMES from the settings document.

    ``statuses.completed`` is the owner's shared closed vocabulary — the same
    statuses their own Capacities query excludes per type. No document (a
    caller that builds the evaluator seam directly) carries no vocabulary at
    all, so nothing can be called completed.
    """
    vocabulary = getattr(settings, "statuses", None)
    names = getattr(vocabulary, "completed", None)
    if not names:
        return frozenset()
    return frozenset(
        _normalized(name) for name in names if _text(name)
    )


def _status_label_names(prop: Any) -> set[str]:
    """Normalized label NAMES one mapped status payload carries.

    Name-preferring by construction, exactly like the single resolver's
    comparison form: a member contributes its display ``name`` and only falls
    back to its ``id`` when it carries no name, so a coincidental option id can
    never decide a row. Anything that cannot be read contributes nothing, which
    keeps an absent, empty, or unreadable status UNKNOWN rather than closed.
    """
    if not isinstance(prop, dict):
        return set()
    kind = _text(prop.get("type"))
    if not kind or kind not in prop:
        return set()
    payload = prop[kind]
    if kind not in {"label", "entity"}:
        # A typed scalar on a label-mapped status is still readable evidence.
        return set(_typed_status_tokens(prop) or ())
    if not isinstance(payload, list):
        return set()
    names: set[str] = set()
    for member in payload:
        if isinstance(member, str):
            value: Any = member
        elif isinstance(member, dict):
            value = member.get("name")
            if not isinstance(value, str) or not _text(value):
                value = member.get("id")
                if not isinstance(value, str):
                    continue
        else:
            continue
        token = _normalized(value)
        if token:
            names.add(token)
    return names


#: Stable reason codes for the review-warning surface (U3b-3). One code is
#: emitted per UNKNOWN tri-state on an active-rule row; ``unavailable`` (no
#: mapped property) and a manual unassigned candidate are decided states and
#: never produce a code.
REVIEW_REASON_RULE_UNKNOWN = "rule_unknown"
REVIEW_REASON_STATUS_UNKNOWN = "status_unknown"
REVIEW_REASON_COMPLETION_UNKNOWN = "completion_unknown"

#: Bound on how many review-candidate identities one warning names. The exact
#: count is always reported; naming every identity would make the warning grow
#: with the unknown set and flood the job surface.
MAX_REVIEW_WARNING_IDENTITIES = 3


def _review_reasons(
    rule_state: capacities_rules.Evaluation | None,
    status_state: str | None,
    completion_state: str | None,
) -> list[str]:
    """Stable review codes for an active-rule row, in a fixed order.

    Only the three UNKNOWN tri-states are review reasons: ``unavailable`` is a
    decided mapping gap and a manual unassigned candidate is a decided MATCH,
    so neither fabricates a reason.
    """
    reasons: list[str] = []
    if rule_state is capacities_rules.UNKNOWN:
        reasons.append(REVIEW_REASON_RULE_UNKNOWN)
    if status_state == "unknown":
        reasons.append(REVIEW_REASON_STATUS_UNKNOWN)
    if completion_state == "unknown":
        reasons.append(REVIEW_REASON_COMPLETION_UNKNOWN)
    return reasons


def _review_warning(review_rows: Sequence[tuple[str, Sequence[str]]]) -> str:
    """One bounded, content-free summary of the rows needing review.

    Names the exact count, at most ``MAX_REVIEW_WARNING_IDENTITIES``
    identities with their reason codes, a ``+N more`` remainder when bounded,
    and the Rescan suggestion. Identity and reason codes are structured
    metadata; no property value or raw object content is ever included.
    """
    shown = review_rows[:MAX_REVIEW_WARNING_IDENTITIES]
    parts = [
        f"{identity} ({', '.join(reasons)})" if reasons else identity
        for identity, reasons in shown
    ]
    remaining = len(review_rows) - len(shown)
    if remaining > 0:
        parts.append(f"+{remaining} more")
    return (
        f"Capacities review — {len(review_rows)} unassigned candidate(s) not "
        f"fully evaluated: {'; '.join(parts)}. Rescan Capacities to evaluate "
        "them fully."
    )


#: The drop-reason vocabulary a read reports. Every code here is already the
#: code the deciding surface uses — the assignment evaluator, the status /
#: completion classifier, the stored-rule evaluator, or the refresh coordinator
#: — so the tally never invents a second name for one cause.
DROP_REASON_CLOSED_STATUS = "closed-source-status"
DROP_REASON_FUTURE_DATE = "future-date"
DROP_REASON_NO_ASSIGNMENT = "no-effective-assignment"
DROP_REASON_EXCLUDED = "tdtb-excluded"
DROP_REASON_NO_RULE_MATCH = "no-rule-match"
DROP_REASON_MALFORMED = "malformed"
DROP_REASON_UNREADABLE = "unreadable"
DROP_REASON_STRUCTURE_NOT_ELIGIBLE = "structure-not-eligible"
DROP_REASON_STRUCTURE_UNMAPPED = "structure-unmapped"
#: Not a drop: the type has no open vocabulary that resolves against the live
#: label set, so its mapped status decided nothing and every affected row was
#: KEPT as an unassigned review candidate. It lives in the review tally, never
#: in the drop tally.
DROP_REASON_NO_STATUS_VOCABULARY = "no-status-vocabulary"

#: At most this many distinct reason codes are named on one structure's line.
MAX_DROP_REASONS_PER_LINE = 6

#: Evaluator reason codes mapped onto the tally vocabulary. The tally reports
#: the reason that actually decided the drop, never a generic "ineligible".
_DECISION_DROP_VOCABULARY = {
    "closed-source-status": DROP_REASON_CLOSED_STATUS,
    "no-effective-assignment": DROP_REASON_NO_ASSIGNMENT,
    "tdtb-excluded": DROP_REASON_EXCLUDED,
    "malformed-source-row": DROP_REASON_MALFORMED,
    "cross-space-row": DROP_REASON_MALFORMED,
    "missing-stable-identity": DROP_REASON_MALFORMED,
    "unstable-identity": DROP_REASON_MALFORMED,
}


def _decision_drop_reason(decision: Any) -> str:
    """The tally code for an ineligible evaluator decision."""
    for code in decision.reason_codes:
        reason = _DECISION_DROP_VOCABULARY.get(code)
        if reason is not None:
            return reason
    return DROP_REASON_NO_ASSIGNMENT


def structure_label(
    structures: Any, structure_id: str
) -> str:
    """Operator-facing label for one structure: its contract title, else its id.

    A configuration id (``custom-project``) and a display title (``Project``)
    are both owner-owned metadata, so either is content-free; the title is used
    when the contract carries one because that is what the operator sees in
    Capacities.
    """
    return _structure_display_name(structures, structure_id) or structure_id


def _ordered_reason_counts(counts: Mapping[str, int]) -> list[tuple[str, int]]:
    """Positive counts, largest first then by code, so the line is stable."""
    return sorted(
        ((code, count) for code, count in counts.items() if count > 0),
        key=lambda item: (-item[1], item[0]),
    )


def _reason_listing(counts: Mapping[str, int]) -> str:
    """Bounded ``2 code, 1 code`` listing plus an ``and N more`` remainder."""
    ordered = _ordered_reason_counts(counts)
    shown = ordered[:MAX_DROP_REASONS_PER_LINE]
    parts = [f"{count} {code}" for code, count in shown]
    remaining = len(ordered) - len(shown)
    if remaining > 0:
        parts.append(f"and {remaining} more")
    return ", ".join(parts)


def drop_tally_warning(label: str, counts: Mapping[str, int]) -> str:
    """One aggregated, bounded, content-free line for one structure's drops.

    The empty string means nothing was counted, so a caller can filter it out
    without a second check. The line names counts and closed reason codes only:
    never a title, an id, or a property value.
    """
    total = sum(count for _, count in _ordered_reason_counts(counts))
    if total < 1:
        return ""
    noun = "object" if total == 1 else "objects"
    return (
        f"Capacities type {label!r}: {total} {noun} dropped — "
        f"{_reason_listing(counts)}"
    )


def review_tally_warning(label: str, counts: Mapping[str, int]) -> str:
    """One aggregated, bounded, content-free line for one type's review rows.

    Shares the drop tally's machinery (``_ordered_reason_counts`` /
    ``_reason_listing``) but states the opposite outcome: these objects were
    KEPT as unassigned review candidates, never dropped. The empty string
    means nothing was counted. Counts and closed reason codes only — never a
    title, an id, or a property value.
    """
    total = sum(count for _, count in _ordered_reason_counts(counts))
    if total < 1:
        return ""
    noun = "object" if total == 1 else "objects"
    return (
        f"Capacities type {label!r}: {total} {noun} kept as unassigned review "
        f"candidate(s) — {_reason_listing(counts)}"
    )


def retained_scope_warning(label: str, counts: Mapping[str, int]) -> str:
    """One line for a structure kept from the previous generation, unlisted.

    A dropped enumeration scope is not a drop of the objects themselves: they
    stay in the published generation with their original timestamps. Say that,
    with the count and the same reason vocabulary, instead of leaving the
    exclusion aggregate and anonymous.
    """
    total = sum(count for _, count in _ordered_reason_counts(counts))
    if total < 1:
        return ""
    noun = "object" if total == 1 else "objects"
    return (
        f"Capacities type {label!r}: {total} {noun} retained with their "
        f"original timestamps; not refreshed — {_reason_listing(counts)}"
    )


def structure_not_read_warning(label: str, reason: str) -> str:
    """One line for a configured structure skipped before any listing."""
    return (
        f"Capacities type {label!r} was not read — {reason}; "
        "its objects were never listed."
    )


def _tag_exclusion_warning(report: dict[str, Any]) -> str:
    """One bounded, content-free summary of rows removed by tag exclusions.

    Names the exact removed count and at most ``MAX_REVIEW_WARNING_IDENTITIES``
    stable identities — never a row title, a tag title, or raw object content.
    """
    decisions = report.get("decisions") or []
    shown = decisions[:MAX_REVIEW_WARNING_IDENTITIES]
    parts = [str(decision.get("identity") or "<unknown>") for decision in shown]
    remaining = len(decisions) - len(shown)
    if remaining > 0:
        parts.append(f"+{remaining} more")
    total = int((report.get("excluded_counts") or {}).get("total", 0))
    return (
        f"Capacities tag exclusions removed {total} candidate(s) from this "
        f"refresh: {'; '.join(parts)}."
    )


#: Capacities' tag property id on task-like structures. Tags are typed
#: ``entity`` references to ``RootTag`` objects; the flat top-level ``tags``
#: title array the API also returns is presentation only and never identity.
#: A mapping-driven tag property name is a later concern — this slice reads
#: the canonical property directly, exactly like the live contract does.
TAGS_PROPERTY = "tags"

#: Capacities' canonical tag-object structure. Enumerated for the settings
#: catalog with bounded pagination; it is not a planning mapping and never
#: contributes candidate rows.
ROOT_TAG_STRUCTURE = "RootTag"

#: One bounded, content-free warning when the tag-title catalog could not be
#: read in full. It names no tag id and no tag title, and it is emitted at most
#: once per projection run — never once per row.
TAG_TITLE_WARNING = (
    "Capacities tag titles are incomplete — some tag references have no "
    "display title. Refresh sources to retry the tag catalog."
)


def _canonical_tag_id(tag_id: str) -> str:
    """The canonical identity form of one provider tag id.

    A UUID id is lowercased (the exclusion store holds lowercase canonical
    ids); any other id is kept exactly as received. Identity normalization
    only — a value is never guessed, truncated, or substituted.
    """
    try:
        uuid.UUID(tag_id)
    except ValueError:
        return tag_id
    return tag_id.lower()


def _capacities_tag_refs(
    prop: Any, flat_tags: Any, space_id: str
) -> tuple[list[dict[str, str]] | None, str | None]:
    """Extract structured tag references from a hydrated object.

    Returns ``(refs, None)`` when the payload is usable (possibly empty), or
    ``(None, reason)`` when it cannot be evaluated by identity. The typed
    entity's ``id`` AND ``title`` are preserved directly — the generic
    ``_property_tokens`` helper flattens the pair into loose tokens and drops
    the title, which would destroy structured identity. The flat top-level
    ``tags`` title array is never used to derive identity: a non-empty array
    without a typed property is title-only metadata. An id that parses as a
    UUID is lowercased; any other id is kept exactly as received.
    """
    if prop is None:
        if flat_tags is None or (isinstance(flat_tags, list) and not flat_tags):
            return [], None
        return None, "tags are title-only without typed identities"
    if not isinstance(prop, dict):
        return None, "tags property is malformed"
    kind = _text(prop.get("type"))
    if kind != "entity":
        return None, "tags property is not an entity property"
    payload = prop.get("entity")
    if not isinstance(payload, list):
        return None, "tags property has a non-list entity payload"
    refs: list[dict[str, str]] = []
    seen: set[str] = set()
    for entry in payload:
        if not isinstance(entry, dict):
            return None, "tags property contains a malformed reference"
        tag_id = _text(entry.get("id"))
        if not tag_id:
            return None, "tags property contains a reference without an id"
        # Exclusions hold lowercase canonical ids and match exactly. Hex case
        # never changes a UUID's identity, so lowercasing can only add matches.
        tag_id = _canonical_tag_id(tag_id)
        if tag_id in seen:
            continue
        seen.add(tag_id)
        title = _text(entry.get("title")) or _text(entry.get("name"))
        refs.append({"space_id": space_id, "tag_id": tag_id, "title": title})
    return refs, None


def _property_text(prop: Any, prop_id: str) -> str:
    kind, payload = _property_payload(prop, prop_id)
    if kind not in {"title", "text", "richText"}:
        raise _MalformedObject(
            f"title property {prop_id!r} is not text", reason=_SKIP_REASON_TITLE
        )
    value = payload.get("value") if isinstance(payload, dict) else payload
    text = _text(value)
    if not text:
        raise _MalformedObject(
            f"property {prop_id!r} has no usable value", reason=_SKIP_REASON_TITLE
        )
    return text


def _property_number(prop: Any, prop_id: str) -> int | float:
    kind, payload = _property_payload(prop, prop_id)
    if kind not in {"number", "text"}:
        raise _MalformedObject(
            f"duration property {prop_id!r} is not numeric",
            reason=_SKIP_REASON_DURATION,
        )
    value = payload.get("value") if isinstance(payload, dict) else payload
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise _MalformedObject(
            f"duration property {prop_id!r} is not numeric",
            reason=_SKIP_REASON_DURATION,
        ) from None
    if number < 0:
        raise _MalformedObject(
            f"duration property {prop_id!r} is negative", reason=_SKIP_REASON_DURATION
        )
    return int(number) if number.is_integer() else number


def _property_date(prop: Any, prop_id: str) -> date | None:
    kind, payload = _property_payload(prop, prop_id)
    if kind != "date" or not isinstance(payload, dict):
        raise _MalformedObject(
            f"date property {prop_id!r} is not a date", reason=_SKIP_REASON_DATE
        )
    start = payload.get("start")
    if start in (None, ""):
        return None
    raw = _text(start)
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        raise _MalformedObject(
            f"date property {prop_id!r} has invalid start", reason=_SKIP_REASON_DATE
        ) from None


def _property_date_end(prop: Any, prop_id: str) -> date | None:
    """Range end of a date property, or ``None``.

    A Capacities date property may carry a multi-day range as
    ``{"start": ..., "end": ...}``. The end is strictly advisory: the start
    already carries the contracted date, so an absent, empty, non-date, or
    unparseable end is ignored rather than failing the object. Callers compare
    it against the start to decide whether a range is real.
    """
    if not isinstance(prop, dict) or _text(prop.get("type")) != "date":
        return None
    payload = prop.get("date")
    if not isinstance(payload, dict):
        return None
    end = payload.get("end")
    if end in (None, ""):
        return None
    try:
        return date.fromisoformat(_text(end)[:10])
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Assigned On (read-only settings-declared date marker)
# ---------------------------------------------------------------------------
# The source-assigned signal can come from a DATE/DATETIME property instead
# of a boolean: the object is source-assigned only when the value's civil date
# falls on or before the read's logical day; a future date is not assigned
# yet. The reading must never raise — an absent, empty, malformed, or
# unmapped-typed value is a definitive negative — and the
# projection records parse failures so at most one content-free diagnostic per
# structure reaches the warnings surface. TDTB never writes this property.

_ASSIGNED_ON_MATCH = "match"
_ASSIGNED_ON_NOMATCH = "nomatch"
_ASSIGNED_ON_UNPARSED = "unparsed"
_ASSIGNED_ON_ABSENT = "absent"


def _assigned_on_raw(value: Any) -> str | None:
    """Best-effort raw string from known Capacities date payload shapes.

    Tolerates a plain string, a ``{"start": ...}`` object, and a list/range
    whose first element carries the start. Anything else is ``None``.
    """
    current = value
    for _ in range(4):
        if isinstance(current, str):
            return current
        if isinstance(current, dict):
            current = current.get("start")
        elif isinstance(current, (list, tuple)):
            current = current[0] if current else None
        else:
            return None
    return None


def _parse_assigned_on(raw: str) -> date | None:
    """Civil date for a Capacities date/datetime string, or ``None``.

    A date-only ``YYYY-MM-DD`` value compares as that civil date with no
    timezone shifting. A datetime with an offset or ``Z`` converts to the
    app's local timezone (the same host-local convention
    ``producer_rules._parse_iso`` uses); a naive datetime is treated as local
    time. An unparseable value returns ``None`` rather than raising.
    """
    text = raw.strip()
    if not text:
        return None
    if len(text) == 10:
        try:
            return date.fromisoformat(text)
        except ValueError:
            pass
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone().date()


def _assigned_on_state(prop: Any, prop_id: str, logical_day: date) -> str:
    """Classify one mapped Assigned On value against the logical day.

    Never raises: a missing property is ``absent``, an empty payload is
    ``absent``, an unsupported property kind or unparseable value is
    ``unparsed``, and a parsed value is ``match`` when its civil date is on or
    before ``logical_day`` (a future date is ``nomatch``).
    """
    if prop is None:
        return _ASSIGNED_ON_ABSENT
    if not isinstance(prop, dict):
        return _ASSIGNED_ON_UNPARSED
    kind = _text(prop.get("type"))
    if kind not in {"date", "datetime"}:
        return _ASSIGNED_ON_UNPARSED
    raw = _assigned_on_raw(prop.get(kind))
    if raw is None or not raw.strip():
        return _ASSIGNED_ON_ABSENT
    parsed = _parse_assigned_on(raw)
    if parsed is None:
        return _ASSIGNED_ON_UNPARSED
    return _ASSIGNED_ON_MATCH if parsed <= logical_day else _ASSIGNED_ON_NOMATCH


def _skip_reason(exc: _MalformedObject) -> str:
    """The fixed operator-facing bucket for a projection failure."""
    return getattr(exc, "reason", None) or _SKIP_REASON_UNKNOWN


def _readable_title(obj: dict[str, Any], mapping: StructureMapping) -> str | None:
    """Best-effort display title for a malformed object; never raises.

    The mapped title property is tried first, then the object's own listed
    title. A title that is not readable stays ``None``. The value is trimmed
    and capped at 120 chars so a hostile payload cannot bloat the additive
    intake block.
    """
    text = ""
    properties = obj.get("properties")
    if isinstance(properties, dict):
        prop = properties.get(mapping.title_property)
        if prop is not None:
            try:
                text = _property_text(prop, mapping.title_property)
            except _MalformedObject:
                text = ""
    if not text:
        text = _text(obj.get("title"))
    text = _text(text)
    return text[:120] if text else None


#: Find-it clue caps for the additive skipped surface.
_SKIP_LABEL_LIMIT = 6
_SKIP_LABEL_CHARS = 40
_SKIP_HINT_TOKENS = 3
_SKIP_HINT_CHARS = 80


def _skipped_labels(obj: dict[str, Any]) -> tuple[str, ...]:
    """Label display names from every ``label``-typed property; never raises.

    Names are trimmed and capped at 40 chars, de-duplicated in first-seen
    order, and limited to six so a hostile payload cannot bloat the additive
    block. A member without a readable string name contributes nothing.
    """
    properties = obj.get("properties")
    if not isinstance(properties, dict):
        return ()
    names: list[str] = []
    seen: set[str] = set()
    for prop in properties.values():
        if not isinstance(prop, dict) or _text(prop.get("type")) != "label":
            continue
        members = prop.get("label")
        if not isinstance(members, list):
            continue
        for member in members:
            name = member.get("name") if isinstance(member, dict) else member
            if not isinstance(name, str):
                continue
            text = _text(name)[:_SKIP_LABEL_CHARS]
            if not text or text in seen:
                continue
            seen.add(text)
            names.append(text)
            if len(names) >= _SKIP_LABEL_LIMIT:
                return tuple(names)
    return tuple(names)


def _skipped_updated_at(obj: dict[str, Any]) -> str | None:
    """The ``lastUpdatedAt`` value when readable as a non-empty string.

    The property is keyed on an id starting with ``lastUpdatedAt`` and carries
    ``{"lastUpdatedAt": {"value": "…"}, "type": "lastUpdatedAt"}``. Any other
    shape contributes nothing rather than raising.
    """
    properties = obj.get("properties")
    if not isinstance(properties, dict):
        return None
    for key, prop in properties.items():
        if not isinstance(prop, dict):
            continue
        typed = _text(prop.get("type")) == "lastUpdatedAt"
        keyed = isinstance(key, str) and key.startswith("lastUpdatedAt")
        if not typed and not keyed:
            continue
        payload = prop.get("lastUpdatedAt")
        if not isinstance(payload, dict):
            continue
        value = payload.get("value")
        if isinstance(value, str):
            text = value.strip()
            if text:
                return text
    return None


def _skipped_hint(obj: dict[str, Any]) -> str | None:
    """Up to three visible block text tokens joined by ``' · '``; never raises.

    ``blocks`` is a dict of block lists; each block carries a ``tokens`` list.
    Only non-empty string ``text`` values count, so markup or entity tokens
    without text are skipped. The joined result is capped at 80 chars.
    """
    blocks = obj.get("blocks")
    if not isinstance(blocks, dict):
        return None
    tokens: list[str] = []
    for block_list in blocks.values():
        if not isinstance(block_list, list):
            continue
        for block in block_list:
            if not isinstance(block, dict):
                continue
            members = block.get("tokens")
            if not isinstance(members, list):
                continue
            for token in members:
                if not isinstance(token, dict):
                    continue
                text = token.get("text")
                if not isinstance(text, str):
                    continue
                text = _text(text)
                if not text:
                    continue
                tokens.append(text)
                if len(tokens) >= _SKIP_HINT_TOKENS:
                    break
            if len(tokens) >= _SKIP_HINT_TOKENS:
                break
        if len(tokens) >= _SKIP_HINT_TOKENS:
            break
    if not tokens:
        return None
    return " · ".join(tokens)[:_SKIP_HINT_CHARS]


def _structure_rows(structures: Any) -> list[Any]:
    """Structure rows from either payload shape the provider/cache can hand us.

    The structures endpoint answers either a mapping keyed by structure id or a
    list of rows, and a dict may also wrap either shape under ``structures``;
    the label lookup must not assume one of them.
    """
    if isinstance(structures, dict):
        inner = structures.get("structures")
        if isinstance(inner, (dict, list)):
            return _structure_rows(inner)
        return list(structures.values())
    if isinstance(structures, list):
        return structures
    return []


def _structure_display_name(
    structures: Any, structure_id: str
) -> str | None:
    """The cached structures contract's display name, or ``None``."""
    if not structures or not structure_id:
        return None
    for row in _structure_rows(structures):
        if not isinstance(row, dict) or _text(row.get("id")) != structure_id:
            continue
        name = _text(row.get("title")) or _text(row.get("name"))
        if name:
            return name
    return None


def _fingerprint(obj: dict[str, Any]) -> str:
    payload = {
        "id": obj.get("id"),
        "spaceId": obj.get("spaceId"),
        "structureId": obj.get("structureId"),
        "properties": obj.get("properties"),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _same_json(left: Any, right: Any) -> bool:
    return json.dumps(left, sort_keys=True, separators=(",", ":"), ensure_ascii=True) == json.dumps(
        right, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )


def _is_provider_timeout(exc: BaseException) -> bool:
    return isinstance(exc, _PROVIDER_TIMEOUT_TYPES)


class CapacitiesAdapter:
    def __init__(
        self,
        provider: CapacitiesProvider,
        config: CapacitiesConfig,
        *,
        tag_titles_sink: Callable[[dict[str, str], bool], None] | None = None,
    ) -> None:
        if not _text(config.space_id):
            raise CapacitiesContractError(
                "Capacities space_id is required",
                detail=_contract_detail(phrase="space_id required"),
            )
        if not config.mappings:
            raise CapacitiesContractError(
                "at least one Capacities structure mapping is required",
                detail=_contract_detail(phrase="at least one mapping required"),
            )
        if config.max_pages < 1:
            raise CapacitiesContractError(
                "max_pages must be positive",
                detail=_contract_detail(phrase="max_pages must be positive"),
            )
        if config.max_content_reads < 0:
            raise CapacitiesContractError(
                "max_content_reads must be non-negative",
                detail=_contract_detail(phrase="max_content_reads must be non-negative"),
            )
        self.provider = provider
        self.config = config
        self._structure_defs: dict[str, dict[str, Any]] | None = None
        self._mappings: dict[str, StructureMapping] | None = None
        # Bounded contract diagnostics recorded once when the mappings are
        # resolved; carried into every read result's warnings.
        self._contract_warnings: list[str] = []
        # Per-read hydration budget, reset by ``items_for_day``.
        self._content_reads_left = config.max_content_reads
        #: Distinct listed identities deferred this run (content-read budget
        #: or provider refusal). Identity-keyed so a repeated row cannot
        #: inflate coverage.
        self._deferred_identities: set[str] = set()
        self._rate_limited = False
        #: Per-read drop accounting, keyed by structure id and reset by every
        #: projection entry point. ``_drop_counts`` counts dropped objects per
        #: reason code; ``_structures_not_read`` names a structure the
        #: enumeration gate skipped before any listing.
        self._drop_counts: dict[str, dict[str, int]] = {}
        self._structures_not_read: dict[str, str] = {}
        #: Per-read REVIEW accounting: objects KEPT as unassigned review
        #: candidates because a mapped status could not be decided. Reset by
        #: every projection entry point, so a type reports its undecidable
        #: vocabulary once per read instead of once per row.
        self._review_counts: dict[str, dict[str, int]] = {}
        #: Per-read resolved status vocabularies (structure id -> vocabulary),
        #: reset by every projection entry point. The live label set and the
        #: requested open names cannot change inside one read.
        self._vocabularies: dict[str, StatusVocabulary] = {}
        #: Per-read Assigned On diagnostics, keyed by structure id and reset
        #: by every projection entry point: at most one content-free warning
        #: per structure reaches the warnings surface.
        self._assigned_on_unparsed: set[str] = set()
        #: Structures whose marker read as assigned this projection
        #: (bookkeeping only; no warning is derived from it).
        self._assigned_on_matched: set[str] = set()
        #: Tag display titles (canonical tag id -> title) resolved from the
        #: space's RootTag catalog. ``None`` means "not read yet": the read is
        #: lazy and happens at most once per adapter instance, so a projection
        #: whose tag references already carry titles never lists the catalog.
        #: Display metadata only; it is never identity and never a fingerprint
        #: input.
        self._tag_titles: dict[str, str] | None = None
        #: Whether the resolved catalog was partial or unreadable. Kept so the
        #: one bounded warning can be emitted per projection run without
        #: re-reading the catalog.
        self._tag_catalog_incomplete = False
        #: Per-run guard: at most one tag-title warning per projection run.
        self._tag_title_warned = False
        #: Optional machine-local sink for the resolved tag-title map, bound by
        #: the refresh coordinator (the paced provider path). It receives
        #: ``(titles, complete)`` and must never raise; ``None`` means this
        #: adapter persists nothing, which is what every offline replay and
        #: every test wants. Titles written here are disposable display
        #: metadata — never identity, never a fingerprint, never in the
        #: published generation or the object cache.
        self._tag_titles_sink = tag_titles_sink

    def close(self) -> None:
        """Close an injected transport when it owns a closeable client."""
        close = getattr(self.provider, "close", None)
        if callable(close):
            close()

    def _ensure_contract(self) -> None:
        if self._structure_defs is not None and self._mappings is not None:
            return
        raw = self.provider.fetch_structures()
        if isinstance(raw, dict):
            raw = raw.get("structures")
        if not isinstance(raw, (list, tuple)):
            raise CapacitiesContractError("Capacities structures response is malformed")
        structures: dict[str, dict[str, Any]] = {}
        for row in raw:
            if not isinstance(row, dict) or not _structure_id(row):
                raise CapacitiesContractError("Capacities structures response has malformed row")
            sid = _structure_id(row)
            if sid in structures:
                raise CapacitiesContractError(f"duplicate Capacities structure {sid!r}")
            structures[sid] = {**row, "_definitions": _definitions(row)}

        mappings: dict[str, StructureMapping] = {}
        declaration_warnings: list[str] = []
        for mapping in self.config.mappings:
            sid = _text(mapping.structure_id)
            if not sid or sid in mappings:
                raise CapacitiesContractError(
                    f"duplicate or empty mapping for {sid!r}",
                    detail=_contract_detail(structure=sid),
                )
            structure = structures.get(sid)
            if structure is None:
                raise CapacitiesContractError(
                    f"configured Capacities structure {sid!r} is unavailable",
                    detail=_contract_detail(structure=sid),
                )
            definitions = structure["_definitions"]
            # A legacy assignment marker the structure no longer defines is
            # ALWAYS fail-open, whether or not Assigned On is mapped: the
            # operator removed the boolean property in Capacities, and
            # refusing the read would strand every mapped object over a seam
            # that no longer exists. Marker and accepted values are dropped
            # together with one bounded, content-free warning naming only the
            # ids. A structure left without a marker, a status property, and
            # an active/stored rule is then simply not an assignment source:
            # it is not enumerated (unless native, Active-enabled,
            # stored-rule, or Assigned On-mapped) and cannot fail the run.
            vanished_assignment = bool(
                mapping.assignment_property
                and mapping.assignment_property not in definitions
            )
            if vanished_assignment:
                declaration_warnings.append(
                    "Legacy assignment property "
                    f"{mapping.assignment_property!r} for structure {sid!r} is "
                    "no longer defined in Capacities; ignored. Open Settings > "
                    "Repair connections to map Assigned To Me again."
                )
                mapping = replace(
                    mapping,
                    assignment_property=None,
                    assignment_values=frozenset(),
                )
            # Any mapping that was configured without a source Assigned marker
            # must map a status property. For a custom structure that mapping
            # does NOT include anything on its own: the evaluator still
            # requires the structure to be Active-enabled (or the object
            # source-assigned). Requiring Active-enabled membership here would
            # be circular, because a structure has to be enumerable before the
            # Settings drawer can offer it for enabling. A mapping whose only
            # marker vanished above is exempt: it is treated as unable to
            # contribute rather than as a fresh misconfiguration.
            if (
                not vanished_assignment
                and not mapping.assignment_property
                and not mapping.open_status_property
                and not mapping.assigned_on_property
            ):
                # A stored ACTIVE rule is the structure's single eligibility
                # authority, so neither legacy mapping is required: the rule
                # (and the operator's explicit selection) can admit objects
                # the legacy seams cannot describe, and missing mappings are
                # manual-assignment candidates rather than a contract failure.
                # No record, a draft-only entry, and a record for another
                # space keep the legacy requirement unchanged. A mapped
                # Assigned On declaration satisfies the requirement by itself:
                # it needs no legacy marker and no status property.
                if self._active_rule(sid) is None:
                    if sid in self.config.assignment_settings.native_task_structures:
                        raise CapacitiesContractError(
                            f"native mapping {sid!r} requires a mapped status property",
                            detail=_contract_detail(
                                structure=sid,
                                phrase="status mapping required",
                            ),
                        )
                    raise CapacitiesContractError(
                        f"mapping {sid!r} requires an assignment property or a "
                        "mapped status property",
                        detail=_contract_detail(
                            structure=sid,
                            phrase="assignment or status mapping required",
                        ),
                    )
            if mapping.assignment_property and not mapping.assignment_values:
                raise CapacitiesContractError(
                    f"mapping {sid!r} has no assignment values",
                    detail=_contract_detail(
                        structure=sid, prop=mapping.assignment_property
                    ),
                )
            if mapping.assignment_values and not mapping.assignment_property:
                raise CapacitiesContractError(
                    f"mapping {sid!r} has assignment values without an assignment property",
                    detail=_contract_detail(structure=sid),
                )
            required = [mapping.title_property]
            if mapping.assignment_property:
                required.append(mapping.assignment_property)
            optional = [
                mapping.date_property,
                mapping.deadline_property,
                mapping.open_status_property,
                mapping.duration_property,
                mapping.completion_property,
            ]
            for prop_id in [*required, *(p for p in optional if p)]:
                if prop_id not in definitions:
                    raise CapacitiesContractError(
                        f"mapping {sid!r} references unknown property {prop_id!r}",
                        detail=_contract_detail(structure=sid, prop=prop_id),
                    )
            # A settings-declared assignment property the structure does not
            # define is a mistyped declaration, not a contract failure. Left
            # in place it would read neutral forever, so assignment would
            # silently never work for this structure. Record the diagnostic
            # ONCE here — the contract build runs once per adapter, and the
            # list carries exactly one entry per (structure, property) — then
            # ignore the declaration by substitution so the mapping's own
            # assignment marker / values apply as the fallback. A bad
            # declaration must never raise, fail the read, or change which
            # structures are enumerated: it is an assignment check only.
            if mapping.assigned_property and mapping.assigned_property not in definitions:
                # Capacities lets an operator rename or recreate a property, and
                # a rename breaks a declaration that still names the old id. The
                # recovered mapping is deliberately narrow: exactly one live
                # boolean named "Assigned"/"Assigned To Me" is unambiguous, so
                # it is used and reported; zero or several mean guessing would
                # be wrong, so the declaration stays ignored.
                resolved = _single_property_by_name(
                    definitions, _ASSIGNED_NAMES, _ASSIGNED_TYPES
                )
                if resolved is None:
                    declaration_warnings.append(
                        "ignored Capacities assignment declaration for structure "
                        f"{sid!r}: unknown property {mapping.assigned_property!r}; "
                        "no single 'Assigned' property was found, so choose one "
                        "in Settings."
                    )
                    mapping = replace(mapping, assigned_property=None)
                else:
                    declaration_warnings.append(
                        "Capacities assignment declaration for structure "
                        f"{sid!r} pointed at property {mapping.assigned_property!r}, "
                        "which no longer exists; using "
                        f"{_property_label(definitions, resolved, 'Assigned')} "
                        f"({resolved!r}) instead. Re-save Settings to keep it."
                    )
                    mapping = replace(mapping, assigned_property=resolved)
            # The Assigned On declaration follows the same fail-open contract:
            # unknown or non-date declarations are ignored with one bounded
            # warning so the mapping's own seams keep working, and nothing
            # changes which structures are enumerated. The same single
            # "Assigned On" date property recovers a renamed declaration.
            if (
                mapping.assigned_on_property
                and mapping.assigned_on_property not in definitions
            ):
                resolved = _single_property_by_name(
                    definitions, _ASSIGNED_ON_NAMES, _ASSIGNED_ON_TYPES
                )
                if resolved is None:
                    declaration_warnings.append(
                        "ignored Capacities Assigned On declaration for structure "
                        f"{sid!r}: unknown property {mapping.assigned_on_property!r}; "
                        "no single 'Assigned On' property was found, so choose one "
                        "in Settings."
                    )
                    mapping = replace(mapping, assigned_on_property=None)
                else:
                    declaration_warnings.append(
                        "Capacities Assigned On declaration for structure "
                        f"{sid!r} pointed at property {mapping.assigned_on_property!r}, "
                        "which no longer exists; using "
                        f"{_property_label(definitions, resolved, 'Assigned On')} "
                        f"({resolved!r}) instead. Re-save Settings to keep it."
                    )
                    mapping = replace(mapping, assigned_on_property=resolved)
            # A settings declaration that overrode a DIFFERENT record-level
            # value shadows the operator's record mapping. The declaration
            # still wins, but the override is surfaced once as a bounded note
            # so a stale declaration cannot silently mask a record edit.
            if (
                mapping.assigned_on_property
                and _text(definitions[mapping.assigned_on_property].get("type"))
                not in _ASSIGNED_ON_TYPES
            ):
                declaration_warnings.append(
                    "ignored Capacities Assigned On declaration for structure "
                    f"{sid!r}: property {mapping.assigned_on_property!r} is not a date"
                )
                mapping = replace(mapping, assigned_on_property=None)
            # Emitted AFTER declaration resolution so it names the property that
            # is actually read.
            if (
                mapping.assigned_on_record_property
                and mapping.assigned_on_property
                and mapping.assigned_on_record_property
                != mapping.assigned_on_property
            ):
                declaration_warnings.append(
                    "settings Assigned On declaration for structure "
                    f"{sid!r} shadows the source record mapping "
                    f"{mapping.assigned_on_record_property!r}; using "
                    f"{mapping.assigned_on_property!r}"
                )
            # Both declaration kinds for ONE structure is a silent-loss seam:
            # ``_project_object`` branches on the date declaration first, so
            # the boolean is never read and its ``true`` cannot admit
            # anything. The runtime keeps date-wins precedence (unchanged);
            # this is the only diagnostic for that pairing, and it is emitted
            # once per structure because the loop visits each mapped id
            # exactly once. It names the structure id only — no source content
            # is available or wanted here.
            if mapping.assigned_property and mapping.assigned_on_property:
                declaration_warnings.append(
                    "Capacities structure "
                    f"{sid!r} declares both an Assigned checkbox and an "
                    "Assigned On date; the Assigned On date takes precedence "
                    "and the Assigned checkbox is ignored. Clear one of the two "
                    "mappings in Settings."
                )
            # Mapped but with no requested open names is NOT a contract
            # failure: ``statuses: []`` disables status admission for the type
            # (the owner's RootTask date-only decision), so the mapped status
            # simply decides nothing. The projection owns that state.
            if mapping.completion_property:
                if not mapping.completion_value:
                    raise CapacitiesContractError(
                        f"mapping {sid!r} has no completion value",
                        detail=_contract_detail(
                            structure=sid, prop=mapping.completion_property
                        ),
                    )
                completion = definitions[mapping.completion_property]
                if completion.get("writable") is not True:
                    raise CapacitiesContractError(
                        f"completion property {mapping.completion_property!r} on {sid!r} is not writable",
                        detail=_contract_detail(
                            structure=sid, prop=mapping.completion_property
                        ),
                    )
                if _text(completion.get("type")) != "label":
                    raise CapacitiesContractError(
                        f"completion property {mapping.completion_property!r} on {sid!r} is not a label",
                        detail=_contract_detail(
                            structure=sid, prop=mapping.completion_property
                        ),
                    )
                if _normalized(mapping.completion_value) not in _label_identifiers(
                    completion
                ):
                    raise CapacitiesContractError(
                        f"completion value {mapping.completion_value!r} is not defined for {sid!r}",
                        detail=_contract_detail(
                            structure=sid, prop=mapping.completion_property
                        ),
                    )
            mappings[sid] = mapping
        # Fail closed on an allowlist that silently omits the canonical native
        # structure: without it every native Capacities Task would vanish with
        # no warning, which is exactly the silent-absence failure this adapter
        # exists to prevent. A deliberate native-free configuration must be an
        # explicit, reviewed decision rather than an omission.
        if "RootTask" not in mappings:
            raise CapacitiesContractError(
                "RootTask must be explicitly mapped",
                detail=_contract_detail(phrase="RootTask missing"),
            )
        self._structure_defs = structures
        self._mappings = mappings
        self._contract_warnings = declaration_warnings

    def _enumerate_listing(
        self, structure_id: str
    ) -> tuple[list[dict[str, Any]], int, bool, str | None]:
        """List one structure's raw rows with explicit pagination outcome.

        Returns ``(rows, pages, complete, reason)``. Pagination rails (the
        defensive ``max_pages`` bound and repeated-cursor detection) return a
        non-complete outcome rather than treating a stopped listing as a
        finished one. Provider/transport exceptions still propagate; only the
        pagination rails are modelled as an outcome.
        """
        rows: list[dict[str, Any]] = []
        cursor: str | None = None
        seen_cursors: set[str] = set()
        pages = 0
        while True:
            if pages >= self.config.max_pages:
                return rows, pages, False, "page_bound"
            page = self.provider.list_objects(structure_id, cursor)
            if not isinstance(page, dict) or not isinstance(page.get("objects"), list):
                return rows, pages, False, "malformed_page"
            rows.extend(page["objects"])
            pages += 1
            next_cursor = page.get("next_cursor", page.get("nextCursor"))
            if next_cursor in (None, ""):
                return rows, pages, True, None
            next_cursor = _text(next_cursor)
            if next_cursor in seen_cursors:
                return rows, pages, False, "repeated_cursor"
            seen_cursors.add(next_cursor)
            cursor = next_cursor

    def enumerate_structure(self, structure_id: str) -> StructureEnumeration:
        """Public, hydration-free listing seam for the paced coordinator."""
        rows, pages, complete, reason = self._enumerate_listing(structure_id)
        return StructureEnumeration(
            structure_id=structure_id,
            rows=tuple(rows),
            pages=pages,
            complete=complete,
            reason=reason,
        )

    def ensure_contract(self) -> None:
        """Public contract-resolution seam (one paced structures request)."""
        self._ensure_contract()

    def can_contribute(self, mapping: StructureMapping) -> bool:
        """Public enumeration-gate seam for the paced coordinator.

        The coordinator holds the stored mapping objects, while the contract
        build may have substituted one after dropping a vanished legacy
        assignment marker; the resolved mapping governs enumeration so a
        structure left with no assignment source is not listed.
        """
        resolved = (self._mappings or {}).get(mapping.structure_id, mapping)
        return self._structure_can_contribute(resolved)

    def _list_objects(self, structure_id: str) -> tuple[list[dict[str, Any]], int]:
        rows, pages, complete, reason = self._enumerate_listing(structure_id)
        if not complete:
            if reason == "page_bound":
                raise CapacitiesContractError(
                    f"Capacities pagination exceeded max_pages for {structure_id!r}",
                    detail=_contract_detail(
                        structure=structure_id, phrase="page bound"
                    ),
                )
            if reason == "repeated_cursor":
                raise CapacitiesContractError(
                    f"Capacities pagination repeated cursor for {structure_id!r}",
                    detail=_contract_detail(
                        structure=structure_id, phrase="repeated cursor"
                    ),
                )
            raise CapacitiesContractError(
                f"Capacities object page for {structure_id!r} is malformed",
                detail=_contract_detail(
                    structure=structure_id, phrase="malformed page"
                ),
            )
        objects: list[dict[str, Any]] = []
        for row in rows:
            hydrated = self._hydrate_object(row)
            if hydrated is None:
                # Deferred this run: the content-read budget was spent or
                # the provider refused. Record the identity so coverage
                # is counted over distinct listed rows.
                object_id = _text(row.get("id")) if isinstance(row, dict) else ""
                if object_id:
                    self._deferred_identities.add(f"{structure_id}:{object_id}")
                continue
            objects.append(hydrated)
        return objects, pages

    def _hydrate_object(self, row: Any) -> dict[str, Any] | None:
        """Fill typed properties into a listed object row.

        The current API's structure listing returns only ``id``,
        ``structureId``, and ``title`` — no typed properties — so every
        property-based eligibility decision needs one content read per listed
        object. That is an inherent N+1 for a scoped listing, bounded by the
        same ``max_pages`` limit as the enumeration itself. A row that already
        carries properties (test fakes, or a future listing that includes them)
        is returned untouched, so no extra call is made.
        """
        if not isinstance(row, dict):
            return row
        # Hydrate only when the listing omitted properties entirely. A row that
        # *has* a properties key is left alone so a malformed payload still
        # fails closed as a malformed object rather than triggering a fetch.
        if "properties" in row:
            return row
        object_id = _text(row.get("id"))
        if not object_id:
            return row
        # Content already cached is free: reuse it without spending budget.
        # Without this, every read spends its budget on the same first N
        # objects and the remainder is never evaluated.
        cache = self.config.content_cache
        if cache is not None:
            cached = cache.get(object_id)
            if isinstance(cached, dict) and isinstance(cached.get("properties"), dict):
                return {**row, **cached}
        if self._content_reads_left <= 0:
            # Out of budget: report the row as unevaluated rather than letting
            # the provider rate-limit the whole read.
            return None
        if self._rate_limited:
            # Already refused once this read; do not spend more requests.
            return None
        self._content_reads_left -= 1
        try:
            content = self.provider.get_object(object_id)
        except CapacitiesRateLimited:
            # Degrade the read instead of discarding every object evaluated so
            # far: the caller reports the remainder as unevaluated.
            self._rate_limited = True
            return None
        if not isinstance(content, dict) or not isinstance(content.get("properties"), dict):
            # Return the row unchanged so the projection rejects it as malformed
            # and the failure stays scoped to this one object.
            return row
        if cache is not None:
            cache.put(object_id, content)
        # Content wins for the fields it owns; the listing still supplies
        # anything it alone carried (for example the title).
        return {**row, **content}

    def _property_definition(
        self, structure_id: str, prop_id: str
    ) -> dict[str, Any] | None:
        """The live property definition for one mapped property, or ``None``.

        ``_ensure_contract`` stamps every structure row with its resolved
        ``_definitions`` map, and that definition carries the live ``labelSet``
        a status payload is resolved against. A missing structure, property, or
        definition is ``None``: no live option identity is known, so nothing is
        ever classified dead from it.
        """
        structures = self._structure_defs or {}
        structure = structures.get(structure_id)
        if not isinstance(structure, dict):
            return None
        definitions = structure.get("_definitions")
        if not isinstance(definitions, Mapping):
            return None
        definition = definitions.get(prop_id)
        return definition if isinstance(definition, dict) else None

    def _project_object(
        self, obj: dict[str, Any], mapping: StructureMapping, logical_day: date
    ) -> dict[str, Any] | None:
        if not isinstance(obj, dict):
            raise _MalformedObject(
                "object row is not an object", reason=_SKIP_REASON_UNREADABLE
            )
        object_id = _text(obj.get("id"))
        if not object_id:
            raise _MalformedObject("object has no id", reason=_SKIP_REASON_UNREADABLE)
        if _text(obj.get("structureId")) != mapping.structure_id:
            raise _MalformedObject(
                f"object {object_id!r} has the wrong structure",
                reason=_SKIP_REASON_UNREADABLE,
            )
        # The space guarantee comes from the space-scoped enumeration, not from
        # the payload: neither the scoped listing nor the object-content read
        # returns ``spaceId``. A row that *does* carry one must agree, so a
        # cross-space row is still rejected rather than trusted.
        payload_space = _text(obj.get("spaceId"))
        if payload_space and payload_space != self.config.space_id:
            raise _MalformedObject(
                f"object {object_id!r} is not in the configured space",
                reason=_SKIP_REASON_UNREADABLE,
            )
        properties = obj.get("properties")
        if not isinstance(properties, dict):
            raise _MalformedObject(
                f"object {object_id!r} has malformed properties",
                reason=_SKIP_REASON_UNREADABLE,
            )

        identity = f"capacities:{self.config.space_id}:{mapping.structure_id}:{object_id}"

        # A stored ACTIVE rule is decided up front: on that path the legacy
        # status/completion classification is replaced by a three-state
        # classification where an unreadable mapped value is UNKNOWN rather
        # than a failed object, and a missing assignment mapping is a manual
        # candidate rather than a contract failure (KTD5, R27/R29).
        active_rule = self._active_rule(mapping.structure_id)
        has_assignment_mapping = bool(
            mapping.assignment_property
            or mapping.assigned_property
            or mapping.assigned_on_property
        )

        # Source-assigned signal. A mapped assignment property that is present
        # is a definitive source boolean; a missing property is neutral, not a
        # negative. Native structures may omit the mapping entirely and rely on
        # the native Auto rule. The evaluator, not this projection, decides
        # precedence and eligibility.
        source_assigned: bool | None = None
        if mapping.assigned_on_property:
            # A builder-resolved settings declaration names a DATE/DATETIME
            # property whose civil date, converted to the app's local
            # timezone, must fall on or before this read's logical day. The
            # declaration TAKES PRECEDENCE: a boolean ``assigned_property``
            # for the same structure is ignored entirely. A future date,
            # empty, absent, or unparseable value is a definitive negative. An
            # unparseable value is counted so at most one content-free line
            # per structure reaches the warnings surface; the marker is
            # read-only and TDTB never writes it.
            state = _assigned_on_state(
                properties.get(mapping.assigned_on_property),
                mapping.assigned_on_property,
                logical_day,
            )
            source_assigned = state == _ASSIGNED_ON_MATCH
            if state == _ASSIGNED_ON_UNPARSED:
                self._assigned_on_unparsed.add(mapping.structure_id)
            elif source_assigned:
                self._assigned_on_matched.add(mapping.structure_id)
        elif mapping.assigned_property:
            # A builder-resolved settings declaration names a boolean property
            # whose ``true`` is the source-assigned signal. This is a separate
            # read from the mapping's own declaration below, so the declared
            # property cannot reach ``assignment_property`` and cannot change
            # the enumeration gate. Present ``true`` is a definitive positive,
            # present ``false`` a definitive negative, and an absent property
            # stays neutral.
            assignment = properties.get(mapping.assigned_property)
            if assignment is not None:
                tokens = _property_tokens(assignment, mapping.assigned_property)
                source_assigned = "true" in tokens
        elif mapping.assignment_property:
            assignment = properties.get(mapping.assignment_property)
            if assignment is not None:
                tokens = _property_tokens(assignment, mapping.assignment_property)
                source_assigned = bool(
                    tokens.intersection(
                        {_normalized(value) for value in mapping.assignment_values}
                    )
                )

        # Open/closed status is a shared safety signal. On the legacy path a
        # missing mapped status property fails closed rather than silently
        # opening, and the mapped status property also supplies the token the
        # native Auto rule checks. On the stored-rule path the same mapping is
        # classified open / unknown / closed: an absent, empty, or unreadable
        # value is UNKNOWN and keeps the row as an unassigned candidate, while
        # a readable non-open value stays a hard exclusion.
        status: str | None = None
        status_is_open: bool | None = None
        status_state: str | None = None
        # The settings Active statuses are the user's own statement of which
        # source statuses mean open work. This is the legacy evaluator seam and
        # stays exactly as it was: for a native task/project structure a
        # readable status named by ``active_statuses`` counts as open, so the
        # gate stays a safety filter for completed/dropped evidence rather than
        # a vocabulary membership test. That relief reads the FULL status token
        # set, not just the preferred display token: a mixed
        # ``[Done, In Progress]`` payload, or one label whose id is ``done`` and
        # name is ``In Progress``, still carries readable completed evidence and
        # must stay closed. The completed vocabulary is the mapping's declared
        # value plus a conservative built-in list, so an unmapped completion
        # value cannot make it vacuous. A non-native structure keeps failing
        # closed.
        active_status_values = {
            _normalized(value)
            for value in self.config.assignment_settings.active_statuses
        }
        native_status_structure = (
            mapping.structure_id
            in self.config.assignment_settings.native_task_structures
        )
        # ONE resolver decides the mapped status. The type's open vocabulary is
        # the requested open names resolved against the LIVE label set
        # (``_vocabulary``), so a deleted option, an unresolvable vocabulary,
        # and an unreadable payload are all UNKNOWN — never closed — and the row
        # is kept as an unassigned review candidate.
        vocabulary = self._vocabulary(mapping) if mapping.open_status_property else None
        declared_mode = self.config.assignment_settings.mode_of(mapping.structure_id)
        closed_status_names = (
            _completed_status_names(self.config.settings)
            if declared_mode in _ASSIGNED_STATUS_MODES
            else frozenset()
        )
        resolution = StatusResolution()
        vocabulary_unknown = False
        dead_status_member = False
        #: A status the projection cannot decide at all — a deleted option, an
        #: unresolvable vocabulary, or a custom structure with an absent/empty
        #: payload. UNKNOWN is never closed.
        unresolved_status = False
        if mapping.open_status_property and vocabulary is not None:
            status_prop = properties.get(mapping.open_status_property)
            if not vocabulary.admission_enabled:
                # ``statuses: []``: this type never admits on status, so the
                # mapped status is not a vocabulary question at all — it is not
                # resolved, reviewed, or tallied.
                status_is_open = None
                if active_rule is not None:
                    status_state = "unavailable"
                if closed_status_names and status_prop is not None:
                    # An ``assigned``/``assignedOn`` type admits on its own
                    # source marker, so its status never admits or excludes by
                    # the open vocabulary. It is still CLOSED evidence when the
                    # status NAME is one the owner lists as completed: their
                    # own Capacities query excludes those per type, so an
                    # Assigned boolean must not resurrect a Printed, Purchased,
                    # or Completed row. Absent, empty, and unreadable stay
                    # UNKNOWN (never closed), and a name outside the completed
                    # list stays admitted.
                    if _status_label_names(status_prop).intersection(
                        closed_status_names
                    ):
                        status_is_open = False
                        if active_rule is not None:
                            status_state = "closed"
            else:
                resolution = _status_resolution(status_prop, vocabulary)
                dead_status_member = resolution.dead
                vocabulary_unknown = resolution.no_vocabulary
            if vocabulary_unknown:
                # Counted once per affected object and reported once per type
                # per read by ``_review_warnings``.
                self._note_review(
                    mapping.structure_id, DROP_REASON_NO_STATUS_VOCABULARY
                )
            if not vocabulary.admission_enabled:
                # Already settled above: a disabled type decides nothing here.
                pass
            elif active_rule is not None:
                status_state = _rule_status_state(resolution)
            elif status_prop is None:
                # A missing mapped status value is an absent signal, not closed
                # evidence. On a native structure it stays UNKNOWN so the
                # evaluator can admit the row on its own due/deadline/source
                # signal. A custom structure carries the SAME absent signal:
                # unknown never means closed, so the row is kept as an
                # unassigned review candidate with ``status_unknown`` instead
                # of disappearing as closed-source-status.
                status_is_open = None
                if not native_status_structure:
                    unresolved_status = True
            elif vocabulary_unknown or dead_status_member:
                # Undecided status: the projection keeps the row as an
                # unassigned review candidate instead of dropping it as closed.
                # ``None`` is not closed evidence, so the evaluator's status
                # gate does not reject it.
                status_is_open = None
            else:
                # The resolver decides open/closed by label NAME; the status
                # token the evaluator reads is the same name-preferring form, so
                # ``Active`` matches even when the option id differs.
                if resolution.state in {
                    _STATUS_RESOLUTION_EMPTY,
                    _STATUS_RESOLUTION_UNKNOWN,
                }:
                    # Present but empty or unreadable payload: the same absent
                    # signal as a missing property, so it stays UNKNOWN rather
                    # than closed. A native structure or a row the source
                    # explicitly marks assigned keeps that absent signal as-is;
                    # a custom row that is not source-assigned stays an
                    # unassigned review candidate (``status_unknown``). A
                    # readable closed status never reaches here, so the
                    # fail-closed gate still drops completed/dropped rows.
                    status_is_open = None
                    if not native_status_structure and source_assigned is not True:
                        unresolved_status = True
                else:
                    display_tokens = _status_display_tokens(
                        status_prop, mapping.open_status_property
                    )
                    status = _status_token(
                        display_tokens, self.config.assignment_settings
                    )
                    status_tokens = _property_tokens(
                        status_prop, mapping.open_status_property
                    )
                    status_is_open = (
                        resolution.state == _STATUS_RESOLUTION_OPEN
                    ) or (
                        native_status_structure
                        and status is not None
                        and status in active_status_values
                        and not status_tokens.intersection(
                            _completed_status_tokens(mapping)
                        )
                    )
        elif active_rule is not None:
            status_state = "unavailable"

        # A deleted status option or an unresolvable type vocabulary is
        # undecided for the same reason an absent or empty payload is: none
        # carries readable closed evidence. One flag drives the
        # unassigned-candidate treatment on both paths.
        unresolved_status = (
            unresolved_status or dead_status_member or vocabulary_unknown
        )

        # Completion availability on the stored-rule path. A mapping that
        # shares the open-status field checks the configured completed value
        # first — a mixed ``[active, done]`` payload is closed — and only
        # falls back to the open vocabulary when no completed token is
        # readable; a distinct completion field has only ``completion_value``
        # known, so every other readable value — and every absent, empty, or
        # unreadable value — is UNKNOWN rather than a guessed closed state
        # (R27/R29).
        # The direct refresh (rules supplied, no active rule) reuses the same
        # strict classifier as an override on the legacy admission decision;
        # the legacy path (``rules is None``) never reads completion here.
        completion_state: str | None = None
        direct_completion = active_rule is None and self.config.rules is not None
        if active_rule is not None or direct_completion:
            if not mapping.completion_property:
                completion_state = "unavailable"
            elif (
                mapping.open_status_property
                and mapping.completion_property == mapping.open_status_property
            ):
                completion_state = _classify_completion(
                    status_prop,
                    mapping.completion_property,
                    mapping.completion_value,
                    open_values=(
                        vocabulary.open if vocabulary is not None else frozenset()
                    ),
                )
            else:
                completion_state = _classify_completion(
                    properties.get(mapping.completion_property),
                    mapping.completion_property,
                    mapping.completion_value,
                )
            if (
                (dead_status_member or vocabulary_unknown)
                and mapping.completion_property == mapping.open_status_property
            ):
                # The completion field IS the unresolvable status payload, so
                # it carries no readable completed evidence either. Without
                # this the strict classifier would call the dead value closed
                # and drop the row before the review surface ever saw it.
                completion_state = "unknown"

        # ``date_property`` remains a day-projection filter, but a MULTI-DAY
        # range (``end`` strictly after ``start``) is a window rather than a
        # single date. A window that has not begun is still a future date and
        # keeps the ordinary drop; a window still open today, or already
        # wholly past, is described by its END so a long-running range is
        # never read as overdue on its start. The evaluator owns the native
        # deadline horizon from ``deadline_property``. An object the source
        # explicitly marks assigned is today's work whatever its own date
        # property says, so that declaration — from either the boolean
        # ``assigned_property``/``assignment_property`` or the Assigned On
        # date — outranks the day filter; every other signal keeps the drop.
        due: date | None = None
        window_end: date | None = None
        if mapping.date_property:
            date_prop = properties.get(mapping.date_property)
            if date_prop is not None:
                due = _property_date(date_prop, mapping.date_property)
                if due is not None:
                    end = _property_date_end(date_prop, mapping.date_property)
                    if end is not None and end > due:
                        window_end = end
                if due is not None and due > logical_day and source_assigned is not True:
                    self._note_drop(mapping.structure_id, DROP_REASON_FUTURE_DATE)
                    return None

        deadline: date | None = None
        if mapping.deadline_property:
            deadline_prop = properties.get(mapping.deadline_property)
            if deadline_prop is not None:
                deadline = _property_date(deadline_prop, mapping.deadline_property)

        # The row's displayed ``deadline`` is the evaluator-visible due date for
        # every single-day value, and the range END for a multi-day window, so
        # the digest row never reports a window's start as its deadline.
        display_deadline: date | None = due
        if window_end is not None:
            display_deadline = window_end
            if window_end >= logical_day:
                # Active window: the object is not due today, it is in
                # progress. A mapped ``deadline_property`` still wins as the
                # evaluator's deadline; with none mapped the window end is the
                # natural deadline.
                due = None
                if not mapping.deadline_property:
                    deadline = window_end
            else:
                # Wholly past window: genuinely overdue, measured by its end.
                due = window_end

        title_prop = properties.get(mapping.title_property)
        if title_prop is None:
            raise _MalformedObject(
                f"object {object_id!r} has no title", reason=_SKIP_REASON_TITLE
            )
        try:
            title = _property_text(title_prop, mapping.title_property)
        except _MalformedObject:
            # Live Capacities objects can carry an empty typed title while the
            # object's own top-level ``title`` holds the real name; hydration
            # already preserved it, so no extra provider request is spent.
            # Only the canonical ``title`` property falls back: a custom mapped
            # title property stays authoritative and its emptiness still fails
            # closed instead of silently borrowing another field.
            if mapping.title_property != "title":
                raise
            title = _text(obj.get("title"))
            if not title:
                # Neither source has usable text: keep the original failure
                # and its diagnostic rather than projecting an empty name.
                raise
        if not title:
            raise _MalformedObject(
                f"object {object_id!r} has an empty title", reason=_SKIP_REASON_TITLE
            )

        rule_state: capacities_rules.Evaluation | None = None
        if active_rule is not None:
            # A stored active rule is the SINGLE eligibility authority for
            # this structure (KTD5): the legacy assignment decision is not
            # ANDed in, so a matching rule admits an object the source does
            # not mark assigned. A known-closed mapped status still overrides
            # inclusion, and an UNKNOWN evaluation is retained as an
            # unassigned row rather than silently omitted (R29/R31).
            rule_state = capacities_rules.evaluate(
                active_rule,
                _capacities_predicate_record(obj),
                logical_day=logical_day.isoformat(),
            )
            if rule_state is capacities_rules.NO_MATCH:
                self._note_drop(mapping.structure_id, DROP_REASON_NO_RULE_MATCH)
                return None
            if status_state == "closed" or completion_state == "closed":
                self._note_drop(mapping.structure_id, DROP_REASON_CLOSED_STATUS)
                return None
            # A TDTB stable-identity exclusion still wins over the stored
            # rule exactly as it wins over Auto on the legacy path: the rule
            # is not a bypass. Source Assigned remains the only signal that
            # outranks an exclusion, so an excluded identity is dropped
            # unless the source explicitly marks it assigned.
            if (
                identity in self.config.assignment_settings.excluded_identities
                and source_assigned is not True
            ):
                self._note_drop(mapping.structure_id, DROP_REASON_EXCLUDED)
                return None
            # A missing assignment mapping is manual assignment: the row is
            # an eligible candidate, never auto-assigned. An UNKNOWN status
            # or completion keeps the rule decision truthful but still forces
            # the row unassigned so the later warning surface can review it
            # (R27/R29).
            assigned = (
                rule_state is capacities_rules.MATCH
                and has_assignment_mapping
                and status_state != "unknown"
                and completion_state != "unknown"
            )
        else:
            decision = evaluate_assignment(
                AssignmentCandidate(
                    identity=identity,
                    source_assigned=source_assigned,
                    status=status,
                    status_is_open=status_is_open,
                    due=due,
                    deadline=deadline,
                ),
                logical_day=logical_day,
                settings=self.config.assignment_settings,
            )
            if direct_completion and completion_state == "closed":
                # Direct-refresh completion override (U3b-4): a readable
                # completed value is a hard exclusion even when source-assigned.
                self._note_drop(mapping.structure_id, DROP_REASON_CLOSED_STATUS)
                return None
            if unresolved_status:
                # An undecided status (a deleted option, or a custom structure
                # with an absent/empty payload) is never auto-assigned, on any
                # path (D2), and it is never silently dropped: the row is a
                # review candidate that reports ``status_unknown``. A
                # stable-identity exclusion still wins, exactly as it does on
                # the stored-rule path, and the outcome is reported as
                # unassigned either way.
                if decision.provenance.exclusion_matched:
                    self._note_drop(mapping.structure_id, DROP_REASON_EXCLUDED)
                    return None
                assigned = False
            elif not decision.eligible:
                self._note_drop(
                    mapping.structure_id, _decision_drop_reason(decision)
                )
                return None
            else:
                assigned = True
                if direct_completion and completion_state == "unknown":
                    # An UNKNOWN completion keeps the row as an unassigned
                    # warning candidate; the override only narrows, it never
                    # admits a row the legacy evaluator rejected.
                    assigned = False

        # Per-type fallback duration: used when the duration mapping is
        # absent or the mapped property is not carried; a present mapped
        # value still wins, and a zero fallback is a real value.
        fallback_minutes: int | None = None
        if active_rule is not None and self.config.rules is not None:
            structure_record = self.config.rules.structure(mapping.structure_id)
            if structure_record is not None:
                fallback_minutes = structure_record.fallback_minutes
        duration: int | float = 30 if fallback_minutes is None else fallback_minutes
        if mapping.duration_property:
            duration_prop = properties.get(mapping.duration_property)
            if duration_prop is not None:
                duration = _property_number(duration_prop, mapping.duration_property)

        path = f"capacities://{self.config.space_id}/{object_id}"
        blocks = duration / 30
        if isinstance(blocks, float) and blocks.is_integer():
            blocks = int(blocks)
        # Structured tag references ride the candidate row for the
        # pre-selection exclusion matcher. Identity is (space_id, tag_id);
        # ``title`` is current display metadata only. ``None`` means the
        # payload could not be evaluated by identity and the matcher decides
        # (blocking while an applicable exclusion is active).
        tag_refs, tag_error = _capacities_tag_refs(
            properties.get(TAGS_PROPERTY), obj.get("tags"), self.config.space_id
        )
        if tag_refs:
            # Display fill only: an id the payload already carries a title for
            # wins, and a blank title is filled from the space's RootTag
            # catalog. Identity (space_id, tag_id) is decided above and never
            # changes here.
            tag_refs = self._fill_tag_titles(tag_refs)
        row = {
            "id": title,
            "name": title,
            "path": path,
            "identity": identity,
            "source": "capacities",
            "types": [mapping.structure_id],
            "urgency": None,
            "deadline": display_deadline.isoformat() if display_deadline else None,
            "priority_score": 0,
            "assigned": assigned,
            "duration": duration,
            "duration_minutes": duration,
            "blocks": blocks,
            "capacities_id": object_id,
            "capacities_space_id": self.config.space_id,
            "capacities_structure_id": mapping.structure_id,
            "capacities_completion_supported": bool(mapping.completion_property),
            "source_fingerprint": _fingerprint(obj),
            "capacities_tags": tag_refs,
        }
        if active_rule is not None:
            # Structured stored-rule state: the tri-state outcome plus the
            # rules revision it was decided under, so a downstream slice can
            # surface an unassigned warning candidate without re-deciding.
            row["capacities_rule"] = {
                "state": rule_state.value,
                "revision": int(getattr(self.config.rules, "revision", 0)),
            }
            # Additive adapter-only availability metadata for the later
            # warning-candidate surface (U3b-3/U4): UNKNOWN is not eligible
            # but stays visible, and closed values never reach a row. The
            # review reasons are a stable list so a consumer never parses the
            # warning text to decide whether the row is fully evaluated.
            row["capacities_status_state"] = status_state
            row["capacities_completion_state"] = completion_state
            row["capacities_review_reasons"] = _review_reasons(
                rule_state, status_state, completion_state
            )
        else:
            # Compact serialization of the evaluator decision, never a title
            # or a policy key. Downstream index allowlists may ignore it.
            row["capacities_assignment"] = {
                "mode": decision.mode.value,
                "reasons": list(decision.reason_codes),
                "source_assigned": decision.provenance.source_assigned,
                "excluded": decision.provenance.exclusion_matched,
            }
            if direct_completion:
                # Rule-less direct rows carry only the completion availability
                # and its review reason; no stored rule state is fabricated.
                row["capacities_completion_state"] = completion_state
            if unresolved_status:
                # Mirror exactly how the stored-rule path represents an
                # unresolved status: the same state token and the same stable
                # review reason, so one consumer renders both paths alike.
                row["capacities_status_state"] = "unknown"
            if unresolved_status or direct_completion:
                row["capacities_review_reasons"] = _review_reasons(
                    None,
                    "unknown" if unresolved_status else None,
                    completion_state if direct_completion else None,
                )
        if tag_error is not None:
            row["capacities_tags_error"] = tag_error
        return row

    def _tag_catalog_titles(self) -> dict[str, str]:
        """Canonical tag id -> display title, from the space's RootTag catalog.

        Read at most once per adapter instance, and only when a projection
        actually needs it (a tag reference that carries no title of its own).
        The catalog is advisory display metadata: every failure mode degrades
        to an empty or partial mapping plus one bounded warning, never to a
        failed or differently-admitted projection.

        A provider that does not declare object enumeration — the offline
        intake replay seam is one — is never asked. Refusing to read there is
        the correct behaviour, not a degradation, so it stays silent and the
        tag titles simply stay blank. An offline adapter is instead SEEDED from
        the machine-local tag-title cache by its caller, which fills the same
        memo this method populates and therefore never reaches the catalog.

        When a sink is bound (the refresh coordinator), a successfully read
        catalog is handed to it for the next offline replay: complete replaces
        the stored map, partial merges additively so a partial read never
        deletes a known id. Freshness is the next refresh — a tag renamed in
        Capacities is rewritten then — and no sink failure is ever raised here.
        """
        if self._tag_titles is not None:
            return self._tag_titles
        if getattr(self.provider, "supports_object_listing", False) is not True:
            self._tag_titles = {}
            self._tag_catalog_incomplete = False
            return self._tag_titles
        titles: dict[str, str] = {}
        incomplete = True
        readable = False
        try:
            catalog = self.list_tags()
        except Exception:  # noqa: BLE001 — advisory display boundary degrades
            catalog = None
        if isinstance(catalog, dict):
            readable = True
            incomplete = _text(catalog.get("status")) != "complete"
            for row in catalog.get("tags") or []:
                if not isinstance(row, dict):
                    continue
                tag_id = _text(row.get("id"))
                title = _text(row.get("title"))
                if not tag_id or not title:
                    continue
                titles.setdefault(_canonical_tag_id(tag_id), title)
        self._tag_titles = titles
        self._tag_catalog_incomplete = incomplete
        if readable and self._tag_titles_sink is not None:
            try:
                self._tag_titles_sink(dict(titles), not incomplete)
            except Exception:  # noqa: BLE001 — persistence is advisory only
                pass
        return titles

    def seed_tag_titles(self, titles: Any) -> None:
        """Seed the display-title map before projection (offline replay).

        The caller reads the machine-local tag-title cache and hands it here so
        a projection over cached objects fills titles with no provider call at
        all. An empty map is a real answer (\"nothing cached\") and is only
        recorded when it is non-empty: a replay adapter then goes through the
        ordinary capability gate and still never lists anything.
        """
        cleaned: dict[str, str] = {}
        if isinstance(titles, Mapping):
            for tag_id, title in titles.items():
                clean_id = _text(tag_id)
                clean_title = _text(title)
                if not clean_id or not clean_title:
                    continue
                cleaned.setdefault(_canonical_tag_id(clean_id), clean_title)
        if cleaned:
            self._tag_titles = cleaned
            self._tag_catalog_incomplete = False

    def _fill_tag_titles(
        self, refs: list[dict[str, str]]
    ) -> list[dict[str, str]]:
        """Fill blank tag titles in place from the catalog; carried titles win."""
        titles: dict[str, str] | None = None
        for ref in refs:
            if ref.get("title"):
                continue
            if titles is None:
                titles = self._tag_catalog_titles()
                if not titles:
                    break
            title = titles.get(ref["tag_id"])
            if title:
                ref["title"] = title
        return refs

    def _project_objects(
        self, logical_day: date, objects: Sequence[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], list[str], set[str], set[str], list[SkippedObject]]:
        """Project listed objects and account for every distinct identity.

        Returns ``(items, warnings, evaluated, malformed, skipped)``.
        ``evaluated`` holds the identities that projected successfully —
        including objects the projection then filtered out as ineligible,
        which are evaluated even though they never become items. ``malformed``
        holds the identities whose projection failed; a malformed row with no
        usable identity is reported by its warning alone, because there is
        nothing to count it once by. Both sets are keyed on the listed
        identity so repeated rows cannot inflate coverage. ``skipped`` is the
        named, display-only surface: one entry per malformed object, keyed on
        the bare object id (the same id the warning already prints).
        """
        self._ensure_contract()
        assert self._mappings is not None
        # One bounded tag-title warning per projection run, never one per row.
        self._tag_title_warned = False
        items: list[dict[str, Any]] = []
        warnings: list[str] = []
        evaluated: set[str] = set()
        malformed: set[str] = set()
        skipped: list[SkippedObject] = []
        seen_skipped: set[str] = set()
        for obj in objects:
            structure_id = _text(obj.get("structureId")) if isinstance(obj, dict) else ""
            mapping = self._mappings.get(structure_id)
            if mapping is None:
                warnings.append(
                    f"ignored Capacities object without an allowlisted structure: {structure_id or '<missing>'}"
                )
                continue
            listed_id = _text(obj.get("id")) if isinstance(obj, dict) else ""
            identity = f"{structure_id}:{listed_id}" if listed_id else ""
            try:
                row = self._project_object(obj, mapping, logical_day)
            except _MalformedObject as exc:
                if identity:
                    malformed.add(identity)
                warnings.append(
                    f"skipped Capacities object {listed_id or '<unknown>'} — not plannable: {exc}"
                )
                # A repeat of the same malformed identity keeps one named
                # entry, mirroring the coverage set. An identity-less row still
                # gets an entry so the operator sees every failure.
                if not listed_id or listed_id not in seen_skipped:
                    if listed_id:
                        seen_skipped.add(listed_id)
                    readable = isinstance(obj, dict)
                    skipped.append(
                        SkippedObject(
                            identity=listed_id,
                            title=(
                                _readable_title(obj, mapping)
                                if readable
                                else None
                            ),
                            reason=_skip_reason(exc),
                            detail=str(exc),
                            structure_id=structure_id or None,
                            structure_name=_structure_display_name(
                                self._structure_defs, structure_id
                            ),
                            labels=_skipped_labels(obj) if readable else (),
                            updated_at=(
                                _skipped_updated_at(obj) if readable else None
                            ),
                            hint=_skipped_hint(obj) if readable else None,
                        )
                    )
                continue
            if identity:
                evaluated.add(identity)
            if row is not None:
                items.append(row)
        if self._tag_catalog_incomplete and not self._tag_title_warned:
            # Exactly one content-free warning for the run: no tag id, no tag
            # title, and no row identity. A partial catalog still filled every
            # title it could read.
            self._tag_title_warned = True
            warnings.append(TAG_TITLE_WARNING)
        policy = self.config.exclusion_policy
        if policy is not None:
            # The shared matcher is the single decision authority; the adapter
            # only splits the projected candidates into the two digest
            # surfaces. A match removes a source-assigned MATCH and an UNKNOWN
            # unassigned candidate alike, and the matcher's structured block
            # (unusable applicable metadata) propagates rather than being
            # silently omitted.
            assigned_items = [row for row in items if row.get("assigned")]
            pool_items = [row for row in items if not row.get("assigned")]
            kept_assigned, kept_pool, exclusion_report = (
                tag_exclusions.apply_tag_exclusions(
                    assigned_items, pool_items, policy
                )
            )
            items = [*kept_assigned, *kept_pool]
            warnings.extend(exclusion_report["warnings"])
            if exclusion_report["excluded_counts"]["total"]:
                warnings.append(_tag_exclusion_warning(exclusion_report))
        # Recompute the review surface from the SURVIVING items so an excluded
        # UNKNOWN row cannot produce a misleading review warning. Every review
        # row stayed an item, so the warning is a supplementary surface on a
        # complete result, never a partial success; the summary is bounded and
        # carries no raw content.
        review_rows = [
            (row["identity"], list(row.get("capacities_review_reasons") or ()))
            for row in items
            if row.get("capacities_review_reasons")
        ]
        if review_rows:
            warnings.append(_review_warning(review_rows))
        items.sort(key=lambda row: (_normalized(row["name"]), row["identity"]))
        return items, warnings, evaluated, malformed, skipped

    def items_for_day_from_objects(
        self, logical_day: date, objects: Sequence[dict[str, Any]]
    ) -> CapacitiesReadResult:
        # Every projection entry point resets the per-read Assigned On
        # diagnostics: a stale flag from an earlier read on the same adapter
        # must not suppress or fabricate a warning on this one.
        self._assigned_on_unparsed = set()
        self._assigned_on_matched = set()
        self._drop_counts = {}
        self._review_counts = {}
        self._vocabularies = {}
        self._structures_not_read = {}
        items, warnings, evaluated, malformed, skipped = self._project_objects(
            logical_day, objects
        )
        warnings = [
            *self._contract_warnings,
            *warnings,
            *self._assigned_on_warnings(),
            *self._drop_warnings(),
            *self._review_warnings(),
        ]
        return CapacitiesReadResult(
            items=items,
            warnings=warnings,
            evaluated=len(evaluated),
            malformed=len(malformed),
            skipped=tuple(skipped),
            drop_tally=self._drop_tally(),
            review_tally=self._review_tally(),
            structures_not_read=dict(sorted(self._structures_not_read.items())),
        )

    def _active_rule(self, structure_id: str) -> dict[str, Any] | None:
        """The stored ACTIVE rule for one structure, or ``None``.

        ``None`` covers every compatibility case at once: no rules record
        supplied, a record for a different space (a stale or misrouted
        document), no entry for this structure, and an entry with no active
        rule (draft-only). ``{"all": []}`` is a real active rule and is
        deliberately distinct from ``None``."""
        record = self.config.rules
        if record is None:
            return None
        if getattr(record, "space_id", None) != self.config.space_id:
            return None
        return record.effective_rule(structure_id)

    def _structure_can_contribute(self, mapping: StructureMapping) -> bool:
        """Whether any object from this structure could ever be eligible.

        When the caller supplied the version-2 settings document, its per-type
        ``mode`` is the SINGLE enumeration authority: every mode other than
        ``off`` admits its objects somehow, so the structure is always
        enumerated, and ``off`` never is. The mapping-based fallbacks below
        apply only to callers that build the adapter without settings.

        A structure configured as native is always evaluated (its Auto rules
        are the only inclusion path). A custom structure with a source
        assignment property can contribute a source-assigned row. A custom
        structure mapped only by the read-only Assigned On declaration also
        contributes: with no legacy marker, Active enable, or stored rule the
        declaration is its only assignment path, and its objects must be
        listed so the date property can be read — the marker value then decides
        object by object, with an empty or future date following the ordinary
        unassigned/pool rules. A structure with an ACTIVE stored rule
        contributes even without a legacy assignment property or Active
        enable, because the rule is its eligibility path. A custom structure
        with none of those can never include anything, so it is not enumerated
        and its objects are never hydrated — which matters because the live API
        allows 30 requests per minute and enumeration plus hydration is
        otherwise an N+1 burst.
        """
        mode_of = getattr(self.config.settings, "mode_of", None)
        if callable(mode_of):
            return mode_of(mapping.structure_id) != "off"
        if mapping.structure_id in self.config.assignment_settings.native_task_structures:
            return True
        if mapping.assignment_property:
            return True
        if mapping.assigned_on_property:
            return True
        if mapping.structure_id in self.config.assignment_settings.active_structures:
            return True
        return self._active_rule(mapping.structure_id) is not None

    def _requested_open(
        self, mapping: StructureMapping
    ) -> tuple[frozenset[str], bool]:
        """One type's REQUESTED open names and whether status admission is on.

        The version-2 settings document is the authority when the caller
        supplied one: the type's own ``statuses``, ``()`` for disabled status
        admission, else the inherited ``statuses.open``. Without it the
        mapping's ``open_status_values`` carries the same requested names, and
        an empty request means status admission is off for the type.
        """
        settings = self.config.settings
        reader = getattr(settings, "open_statuses_for", None)
        if callable(reader):
            names = {
                _normalized(value) for value in (reader(mapping.structure_id) or ())
            }
            names.discard("")
            enabled_reader = getattr(settings, "status_admission_enabled", None)
            enabled = (
                bool(enabled_reader(mapping.structure_id))
                if callable(enabled_reader)
                else bool(names)
            )
            return frozenset(names), enabled
        names = {_normalized(value) for value in mapping.open_status_values}
        names.discard("")
        return frozenset(names), bool(names)

    def _vocabulary(self, mapping: StructureMapping) -> StatusVocabulary:
        """Resolve one type's open vocabulary against the LIVE label set.

        The derived open set is requested-open ∩ live-label-set, computed here
        rather than trusted from a stored snapshot: a renamed option id with an
        unchanged name still matches, and a renamed label drops out. Cached per
        read because neither input can change inside one.
        """
        cached = self._vocabularies.get(mapping.structure_id)
        if cached is not None:
            return cached
        requested, enabled = self._requested_open(mapping)
        live = (
            frozenset(
                _label_identifiers(
                    self._property_definition(
                        mapping.structure_id, mapping.open_status_property
                    )
                )
            )
            if mapping.open_status_property
            else frozenset()
        )
        resolved_open = frozenset(token for token in requested if token in live)
        vocabulary = StatusVocabulary(
            requested=requested,
            open=resolved_open,
            live=live,
            admission_enabled=enabled,
            resolvable=enabled and bool(live) and bool(resolved_open),
        )
        self._vocabularies[mapping.structure_id] = vocabulary
        return vocabulary

    def _note_review(self, structure_id: str, reason: str) -> None:
        """Count one row KEPT as an unassigned review candidate."""
        if not structure_id:
            return
        bucket = self._review_counts.setdefault(structure_id, {})
        bucket[reason] = bucket.get(reason, 0) + 1

    def _review_tally(self) -> dict[str, dict[str, int]]:
        """Deterministic snapshot of this read's per-structure review counts."""
        return {
            structure_id: {
                code: self._review_counts[structure_id][code]
                for code in sorted(self._review_counts[structure_id])
            }
            for structure_id in sorted(self._review_counts)
        }

    def _review_warnings(self) -> list[str]:
        """One bounded line per structure whose rows needed review."""
        warnings = [
            review_tally_warning(
                structure_label(self._structure_defs, structure_id),
                self._review_counts[structure_id],
            )
            for structure_id in sorted(self._review_counts)
        ]
        return [warning for warning in warnings if warning]

    def _note_drop(self, structure_id: str, reason: str) -> None:
        """Count one dropped object under its structure and reason code."""
        if not structure_id:
            return
        bucket = self._drop_counts.setdefault(structure_id, {})
        bucket[reason] = bucket.get(reason, 0) + 1

    def _drop_tally(self) -> dict[str, dict[str, int]]:
        """Deterministic snapshot of this read's per-structure drop counts."""
        return {
            structure_id: {
                code: self._drop_counts[structure_id][code]
                for code in sorted(self._drop_counts[structure_id])
            }
            for structure_id in sorted(self._drop_counts)
        }

    def _drop_warnings(self) -> list[str]:
        """One aggregated line per structure this read dropped or skipped.

        Exactly one line per structure, sorted, deduped, and bounded by
        ``MAX_DROP_REASONS_PER_LINE``. A structure that was never listed gets
        its own line because no object count exists to aggregate over.
        """
        warnings = [
            drop_tally_warning(
                structure_label(self._structure_defs, structure_id),
                self._drop_counts[structure_id],
            )
            for structure_id in sorted(self._drop_counts)
        ]
        warnings.extend(
            structure_not_read_warning(
                structure_label(self._structure_defs, structure_id), reason
            )
            for structure_id, reason in sorted(self._structures_not_read.items())
        )
        return [warning for warning in warnings if warning]

    def _assigned_on_warnings(self) -> list[str]:
        """At most one content-free diagnostic per mapped Assigned On structure.

        A structure whose mapped values never parsed reports the parse
        failure. The line carries only the structure id — never a property
        value — so the warning surface cannot leak source content.
        """
        warnings: list[str] = []
        for mapping in (self._mappings or {}).values():
            structure_id = mapping.structure_id
            if not mapping.assigned_on_property:
                continue
            if structure_id in self._assigned_on_unparsed:
                warnings.append(
                    "Capacities Assigned On values could not be parsed for "
                    f"structure {structure_id!r}; treated as not assigned"
                )
            # A mapped structure with nothing assigned (today or earlier) is
            # the normal case, not a fault: no "never matched" line, which
            # would fire on every read and bury real warnings.
        return warnings

    def _drain_cache_warnings(self) -> list[str]:
        """Collect content-cache diagnostics recorded since the last read.

        The cache is factory-owned and outlives one adapter instance, so it
        carries its diagnostics; the adapter's warnings list is how they reach
        ``source_warnings``. A cache without this seam (explicit ``None`` or a
        test double) contributes nothing.
        """
        cache = self.config.content_cache
        drain = getattr(cache, "drain_warnings", None)
        if not callable(drain):
            return []
        try:
            messages = drain()
        except Exception:  # noqa: BLE001 — diagnostics must not break a read
            return []
        if not isinstance(messages, (list, tuple)):
            return []
        return [str(message) for message in messages if message]

    def items_for_day(self, logical_day: date) -> CapacitiesReadResult:
        self._ensure_contract()
        assert self._mappings is not None
        self._content_reads_left = self.config.max_content_reads
        self._deferred_identities = set()
        self._rate_limited = False
        self._assigned_on_unparsed = set()
        self._assigned_on_matched = set()
        self._drop_counts = {}
        self._review_counts = {}
        self._vocabularies = {}
        self._structures_not_read = {}
        all_items: list[dict[str, Any]] = []
        warnings: list[str] = list(self._contract_warnings)
        evaluated: set[str] = set()
        malformed: set[str] = set()
        skipped: list[SkippedObject] = []
        page_count = 0
        for structure_id, mapping in self._mappings.items():
            if not self._structure_can_contribute(mapping):
                # Silent before this tally: the structure's objects were never
                # listed or hydrated, so nothing about them reached the result.
                self._structures_not_read[
                    structure_id
                ] = DROP_REASON_STRUCTURE_NOT_ELIGIBLE
                continue
            objects, pages = self._list_objects(structure_id)
            page_count += pages
            (
                items,
                batch_warnings,
                batch_evaluated,
                batch_malformed,
                batch_skipped,
            ) = self._project_objects(logical_day, objects)
            all_items.extend(items)
            warnings.extend(batch_warnings)
            evaluated |= batch_evaluated
            malformed |= batch_malformed
            skipped.extend(batch_skipped)
        # Run-local coverage over distinct listed identities. A duplicate row
        # can report one identity as evaluated first and deferred later once
        # the budget ran out; the stronger outcome wins so neither counter is
        # inflated by the repeat.
        malformed -= evaluated
        deferred = self._deferred_identities - evaluated - malformed
        if self._rate_limited:
            warnings.append(
                "Capacities partial — "
                f"{len(evaluated)} evaluated · {len(deferred)} deferred across "
                "contributing structures. Provider rate limit (30 requests per "
                "minute) reached. Wait at least a minute, then Refresh sources "
                "to continue."
            )
        elif deferred:
            warnings.append(
                "Capacities partial — "
                f"{len(evaluated)} evaluated · {len(deferred)} deferred across "
                "contributing structures. Content-read budget reached. Wait at "
                "least a minute, then Refresh sources to continue."
            )
        warnings.extend(self._assigned_on_warnings())
        warnings.extend(self._drop_warnings())
        warnings.extend(self._review_warnings())
        warnings.extend(self._drain_cache_warnings())
        all_items.sort(key=lambda row: (_normalized(row["name"]), row["identity"]))
        return CapacitiesReadResult(
            items=all_items,
            warnings=warnings,
            pages=page_count,
            evaluated=len(evaluated),
            deferred=len(deferred),
            malformed=len(malformed),
            skipped=tuple(skipped),
            drop_tally=self._drop_tally(),
            review_tally=self._review_tally(),
            structures_not_read=dict(sorted(self._structures_not_read.items())),
        )

    def list_tags(self) -> dict[str, Any]:
        """Enumerate the space's canonical ``RootTag`` objects for the catalog.

        Deliberately independent of the configured structure mappings: the
        settings drawer's tag catalog must be able to answer even while a
        mapping is broken, and it is never derived from filtered digest rows.
        Pagination is cursor-based and bounded by ``max_pages``; a partial
        listing reports ``status="partial"`` with bounded warnings rather
        than silently truncating. The first page failing raises so the caller
        can degrade the whole catalog; later failures keep what was read.
        """
        tags: list[dict[str, str]] = []
        warnings: list[str] = []
        seen_ids: set[str] = set()
        seen_cursors: set[str] = set()
        cursor: str | None = None
        pages = 0
        status = "complete"
        while True:
            if pages >= self.config.max_pages:
                status = "partial"
                warnings.append(
                    f"tag catalog stopped after {self.config.max_pages} page(s) "
                    "— the listing is incomplete"
                )
                break
            try:
                page = self.provider.list_objects(ROOT_TAG_STRUCTURE, cursor)
            except CapacitiesRateLimited:
                status = "partial"
                warnings.append(
                    "tag catalog hit the provider rate limit — the listing is "
                    "incomplete"
                )
                break
            except Exception as exc:  # noqa: BLE001 — provider boundary
                if not tags and pages == 0:
                    raise
                status = "partial"
                warnings.append(
                    f"tag catalog read failed ({exc}) — the listing is incomplete"
                )
                break
            if not isinstance(page, dict) or not isinstance(page.get("objects"), list):
                if not tags and pages == 0:
                    raise CapacitiesContractError("Capacities tag catalog page is malformed")
                status = "partial"
                warnings.append(
                    "tag catalog page is malformed — the listing is incomplete"
                )
                break
            for row in page["objects"]:
                if not isinstance(row, dict):
                    status = "partial"
                    warnings.append("skipped a malformed Capacities tag row")
                    continue
                tag_id = _text(row.get("id"))
                title = _text(row.get("title"))
                if not tag_id or not title:
                    status = "partial"
                    warnings.append("skipped a Capacities tag row without an id and title")
                    continue
                if tag_id in seen_ids:
                    continue
                seen_ids.add(tag_id)
                tags.append({"id": tag_id, "title": title})
            pages += 1
            next_cursor = page.get("next_cursor", page.get("nextCursor"))
            if next_cursor in (None, ""):
                break
            next_cursor = _text(next_cursor)
            if next_cursor in seen_cursors:
                status = "partial"
                warnings.append(
                    "tag catalog repeated a pagination cursor — the listing is "
                    "incomplete"
                )
                break
            seen_cursors.add(next_cursor)
            cursor = next_cursor
        tags.sort(key=lambda row: (_normalized(row["title"]), row["id"]))
        return {
            "status": status,
            "space_id": self.config.space_id,
            "tags": tags,
            "warnings": warnings,
        }

    def complete(
        self, object_id: str, *, expected_fingerprint: str | None = None
    ) -> CompletionOutcome:
        self._ensure_contract()
        assert self._mappings is not None and self._structure_defs is not None
        object_id = _text(object_id)
        if not object_id:
            raise CapacitiesWriteError("completion requires an object id")
        current = self.provider.get_object(object_id)
        structure_id = _text(current.get("structureId"))
        mapping = self._mappings.get(structure_id)
        if mapping is None or not mapping.completion_property or not mapping.completion_value:
            raise CapacitiesWriteError(
                f"completion is unsupported for Capacities object {object_id!r}"
            )
        if _text(current.get("spaceId")) not in {"", self.config.space_id}:
            raise CapacitiesWriteError("completion object belongs to another space")
        if not _text(expected_fingerprint):
            raise CapacitiesWriteError(
                "Capacities completion requires a source fingerprint baseline"
            )
        if _fingerprint(current) != expected_fingerprint:
            raise CapacitiesWriteError("Capacities source changed since the planning baseline")

        completion_property = mapping.completion_property
        current_prop = (current.get("properties") or {}).get(completion_property)
        if current_prop is None:
            raise CapacitiesWriteError("completion property is missing from source object")
        before_property = deepcopy(current_prop)
        if _normalized(mapping.completion_value) in _property_tokens(
            current_prop, completion_property
        ):
            return CompletionOutcome(
                "already_complete",
                object_id,
                completion_property=completion_property,
                before_property=before_property,
                after_fingerprint=_fingerprint(current),
            )

        definition = self._structure_defs[structure_id]["_definitions"][completion_property]
        patch = {
            completion_property: {
                "type": _text(definition.get("type")),
                "label": [{"id": mapping.completion_value}],
            }
        }
        try:
            self.provider.patch_object(object_id, patch)
        except Exception as exc:  # noqa: BLE001 — provider boundary
            if _is_provider_timeout(exc):
                after = self.provider.get_object(object_id)
                if self._is_completed(after, mapping):
                    return CompletionOutcome(
                        "completed_after_timeout",
                        object_id,
                        completion_property=completion_property,
                        before_property=before_property,
                        after_fingerprint=_fingerprint(after),
                    )
                raise CapacitiesWriteError(
                    "Capacities completion timed out without verified readback"
                ) from exc
            raise CapacitiesWriteError(f"Capacities completion failed: {exc}") from exc

        after = self.provider.get_object(object_id)
        if not self._is_completed(after, mapping):
            raise CapacitiesWriteError("Capacities completion write did not read back")
        return CompletionOutcome(
            "completed",
            object_id,
            completion_property=completion_property,
            before_property=before_property,
            after_fingerprint=_fingerprint(after),
        )

    def restore_completion(
        self,
        object_id: str,
        *,
        completion_property: str | None,
        before_property: dict[str, Any] | None,
        expected_fingerprint: str | None,
    ) -> CompletionOutcome:
        """Restore one completion property after a journaled undo.

        The current object must still match the post-completion fingerprint;
        otherwise the source changed after TDTB's write and the undo refuses to
        overwrite it. The PATCH contains only the mapped completion property,
        preserving all unspecified Capacities properties.
        """
        self._ensure_contract()
        assert self._mappings is not None
        object_id = _text(object_id)
        if not object_id or not completion_property or not isinstance(before_property, dict):
            raise CapacitiesWriteError("completion undo has incomplete before-image")
        if not expected_fingerprint:
            raise CapacitiesWriteError("completion undo has no post-write fingerprint")

        current = self.provider.get_object(object_id)
        structure_id = _text(current.get("structureId"))
        mapping = self._mappings.get(structure_id)
        if mapping is None or mapping.completion_property != completion_property:
            raise CapacitiesWriteError("completion undo mapping is unavailable")
        if _text(current.get("spaceId")) not in {"", self.config.space_id}:
            raise CapacitiesWriteError("completion undo object belongs to another space")
        properties = current.get("properties")
        if not isinstance(properties, dict):
            raise CapacitiesWriteError("completion undo object has malformed properties")
        current_prop = properties.get(completion_property)
        if _same_json(current_prop, before_property):
            return CompletionOutcome("already_restored", object_id)
        if _fingerprint(current) != expected_fingerprint:
            raise CapacitiesWriteError("Capacities source changed since completion")

        patch = {completion_property: deepcopy(before_property)}
        try:
            self.provider.patch_object(object_id, patch)
        except Exception as exc:  # noqa: BLE001 — provider boundary
            if _is_provider_timeout(exc):
                after = self.provider.get_object(object_id)
                after_prop = (after.get("properties") or {}).get(completion_property)
                if _same_json(after_prop, before_property):
                    return CompletionOutcome("restored_after_timeout", object_id)
                raise CapacitiesWriteError(
                    "Capacities completion undo timed out without verified readback"
                ) from exc
            raise CapacitiesWriteError(f"Capacities completion undo failed: {exc}") from exc

        after = self.provider.get_object(object_id)
        after_prop = (after.get("properties") or {}).get(completion_property)
        if not _same_json(after_prop, before_property):
            raise CapacitiesWriteError("Capacities completion undo did not read back")
        return CompletionOutcome("restored", object_id)

    @staticmethod
    def _is_completed(obj: dict[str, Any], mapping: StructureMapping) -> bool:
        if not mapping.completion_property or not mapping.completion_value:
            return False
        properties = obj.get("properties")
        if not isinstance(properties, dict):
            return False
        prop = properties.get(mapping.completion_property)
        if prop is None:
            return False
        try:
            return _normalized(mapping.completion_value) in _property_tokens(
                prop, mapping.completion_property
            )
        except _MalformedObject:
            return False


class CapacitiesRestClient:
    """Thin current-API transport; the adapter owns all business filtering."""

    #: This transport really can enumerate objects, so the adapter may read the
    #: advisory RootTag title catalog through it. A replay seam that answers
    #: from a published generation declares nothing and is therefore never
    #: asked — the adapter's safe default.
    supports_object_listing = True

    @property
    def base_url(self) -> str:
        """The provider base URL this client was built for (no credentials)."""
        return str(self._client.base_url)

    def __init__(
        self,
        token: str,
        *,
        space_id: str,
        base_url: str = "https://api.capacities.io",
        timeout: float = 10.0,
        headers: dict[str, str] | None = None,
        transport: Any = None,
        content_cache: Any = None,
        structures_observer: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        if not _text(token):
            raise ValueError("Capacities API token is required")
        if not _text(space_id):
            raise ValueError("Capacities space id is required")
        import httpx

        request_headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
            **(headers or {}),
        }
        self._space_id = _text(space_id)
        self._content_cache = content_cache
        #: Optional best-effort rate-metadata sink. The paced coordinator
        #: registers ``(endpoint, headers)`` here; header facts are advisory
        #: and their absence never fails a read.
        self._rate_observer: Callable[[str, Any], None] | None = None
        #: Best-effort sink for the parsed ``/space/structures`` payload. The
        #: builder binds it to the machine-local title cache; ``None`` means a
        #: transport-only client. A per-call ``observer`` argument overrides
        #: it.
        self._structures_observer = structures_observer
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers=request_headers,
            timeout=timeout,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def set_rate_observer(self, observer: Callable[[str, Any], None] | None) -> None:
        """Register the paced coordinator's rate-metadata sink (best-effort)."""
        self._rate_observer = observer if callable(observer) else None

    def _observe(self, endpoint: str, response: Any) -> None:
        observer = self._rate_observer
        if observer is None:
            return
        try:
            headers = getattr(response, "headers", None) or {}
            observer(endpoint, dict(headers))
        except Exception:  # noqa: BLE001 — observation never changes a read
            pass

    def _json(self, response: Any) -> dict[str, Any]:
        if getattr(response, "status_code", None) == 429:
            retry_after = None
            headers = getattr(response, "headers", None) or {}
            raw = headers.get("Retry-After") or headers.get("retry-after")
            if raw is not None:
                try:
                    retry_after = max(0.0, float(str(raw).strip()))
                except (TypeError, ValueError):
                    retry_after = None
            raise CapacitiesRateLimited(
                "the Capacities API rate limit (30 requests per minute) was exceeded",
                retry_after=retry_after,
            )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise CapacitiesContractError("Capacities API returned a non-object response")
        return payload

    def fetch_structures(
        self,
        observer: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        """Read the space's structure definitions, best-effort observing them.

        Space scoping is token-implied. Unlike :meth:`list_objects`, this
        endpoint takes no ``spaceId`` parameter, so the request carries no
        explicit space scope and the payload cannot be used to verify one.
        Every current read already relies on that; this call inherits the
        reliance rather than adding it, and it is recorded here so the next
        reader sees it before assuming the response is space-verified.

        ``observer`` — or this client's ``structures_observer`` when the call
        passes none — receives the parsed payload after a successful parse.
        It is a best-effort sink: any failure inside it is swallowed here,
        because observation must never change or break a read.
        """
        response = self._client.get("/space/structures")
        self._observe("structures", response)
        payload = self._json(response)
        sink = observer if observer is not None else self._structures_observer
        if sink is not None:
            try:
                sink(payload)
            except Exception:  # noqa: BLE001 — observation must never affect a read
                pass
        return payload

    def list_objects(self, structure_id: str, cursor: str | None = None) -> dict[str, Any]:
        """List one structure's objects inside the configured space.

        Object listing is space-scoped. That scoping is load-bearing twice
        over, and both facts were established against the live API rather than
        from documentation:

        * Without ``spaceId`` the current API answers with a legacy envelope
          whose ``objects`` list is empty, so a listing that omits the space
          silently reports zero objects for every structure.
        * Neither the scoped listing nor the object-content endpoint returns
          ``spaceId``, so the space guarantee has to come from the request
          scope; the payload cannot be used to verify it.

        The scoped response uses the modern ``{results, nextCursor, hasMore}``
        envelope, so ``results`` is preferred with the legacy ``objects`` key
        kept as a fallback for older or faked providers.
        """
        params: dict[str, str] = {"id": structure_id, "spaceId": self._space_id}
        if cursor:
            params["cursor"] = cursor
        response = self._client.get("/objects/structure", params=params)
        self._observe("listing", response)
        payload = self._json(response)
        results = payload.get("results")
        if results is None:
            results = payload.get("objects", [])
        return {
            "objects": results,
            "next_cursor": payload.get("nextCursor", payload.get("next_cursor")),
        }

    def get_object(self, object_id: str) -> dict[str, Any]:
        """Read one object's typed content, reusing a recent cached read.

        The listing carries no properties, so this is the only source of typed
        data and it costs one request against a 30-per-minute budget. A short
        cache keeps repeated refreshes affordable: without it, two refreshes in
        the same minute would exceed the limit before any content changed.
        """
        if self._content_cache is not None:
            cached = self._content_cache.get(object_id)
            if cached is not None:
                return cached
        response = self._client.get("/object", params={"id": object_id})
        self._observe("content", response)
        content = self._json(response)
        if self._content_cache is not None:
            self._content_cache.put(object_id, content)
        return content

    def patch_object(self, object_id: str, properties: dict[str, Any]) -> dict[str, Any]:
        response = self._client.patch(
            "/object",
            json={"id": object_id, "properties": properties},
        )
        self._observe("content", response)
        return self._json(response)
