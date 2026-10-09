"""Application-owned Capacities REST source adapter.

The adapter is deliberately contract-driven: structure definitions and
per-structure mappings are supplied by TDTB configuration, candidate objects
are enumerated with bounded pagination, and eligibility is evaluated locally
from typed properties.  It does not use the Capacities MCP server, title-based
matching, or a generic synchronization model.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field, replace
from datetime import date
import hashlib
import json
from typing import Any, Callable, Protocol, Sequence

from capacities_assignment import (
    AssignmentCandidate,
    AssignmentSettings,
    evaluate_assignment,
)
import capacities_rules
from producer_rules import _capacities_predicate_record

try:
    import httpx as _httpx
except ImportError:  # pragma: no cover — the app declares httpx as a dependency
    _PROVIDER_TIMEOUT_TYPES: tuple[type[BaseException], ...] = (TimeoutError,)
else:
    _PROVIDER_TIMEOUT_TYPES = (TimeoutError, _httpx.TimeoutException)


class CapacitiesContractError(RuntimeError):
    """The provider response or configured mapping is unsafe to consume."""


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
    filter. ``deadline_property`` maps the source deadline the evaluator
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
    #: never reads vault files or credentials itself.
    assignment_settings: AssignmentSettings = field(default_factory=AssignmentSettings)
    #: Optional stored per-type rules record (``capacities_rules.RulesRecord``)
    #: for this space. When a structure has an ACTIVE stored rule, that rule is
    #: the SINGLE eligibility authority for its objects: the legacy
    #: ``evaluate_assignment`` decision is not ANDed in. ``None`` — or a record
    #: for another space, or a draft-only entry — keeps the legacy decision
    #: unchanged (KTD5).
    rules: capacities_rules.RulesRecord | None = None


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


@dataclass(frozen=True)
class CompletionOutcome:
    status: str
    object_id: str
    completion_property: str | None = None
    before_property: dict[str, Any] | None = None
    after_fingerprint: str | None = None


class _MalformedObject(ValueError):
    pass


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


def _label_options(definition: dict[str, Any]) -> set[str]:
    options = definition.get("labelSet") or []
    if not isinstance(options, list):
        raise CapacitiesContractError(
            f"label property {_structure_id(definition)!r} has malformed labelSet"
        )
    result: set[str] = set()
    for option in options:
        if not isinstance(option, dict) or not _text(option.get("id")):
            raise CapacitiesContractError(
                f"label property {_structure_id(definition)!r} has malformed option"
            )
        result.add(_normalized(option["id"]))
    return result


def _property_payload(prop: Any, prop_id: str) -> tuple[str, Any]:
    if not isinstance(prop, dict):
        raise _MalformedObject(f"property {prop_id!r} is not an object")
    kind = _text(prop.get("type"))
    if not kind or kind not in prop:
        raise _MalformedObject(f"property {prop_id!r} has no typed payload")
    return kind, prop[kind]


def _property_tokens(prop: Any, prop_id: str) -> set[str]:
    kind, payload = _property_payload(prop, prop_id)
    values: list[Any] = []
    if kind in {"label", "entity"}:
        if not isinstance(payload, list):
            raise _MalformedObject(f"property {prop_id!r} has a non-list {kind} payload")
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
            raise _MalformedObject(f"property {prop_id!r} has a non-object date payload")
        values.append(payload.get("start"))
    else:
        raise _MalformedObject(f"property {prop_id!r} has unsupported type {kind!r}")
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
        raise _MalformedObject(f"property {prop_id!r} has a non-list {kind} payload")
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
    of the payload could not be read as its declared type. Tokens keep the
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


def _classify_open_status(
    prop: Any, prop_id: str, open_values: frozenset[str]
) -> str:
    """Open / unknown / closed classification for a mapped status.

    Used on the stored-rule path, where an under-evaluated status must keep
    the object visible as an unassigned candidate instead of failing the
    object (legacy) or silently excluding it. The payload is read strictly
    (``_rule_status_tokens``): absent, empty, and unreadable payloads are
    UNKNOWN, and only a fully readable payload with no open match is a
    known-closed hard exclusion.
    """
    if prop is None:
        return "unknown"
    tokens, unreadable = _rule_status_tokens(prop, prop_id)
    if unreadable or not tokens:
        return "unknown"
    if tokens.intersection({_normalized(value) for value in open_values}):
        return "open"
    return "closed"


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
    without a typed property is title-only metadata.
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
        if tag_id in seen:
            continue
        seen.add(tag_id)
        title = _text(entry.get("title")) or _text(entry.get("name"))
        refs.append({"space_id": space_id, "tag_id": tag_id, "title": title})
    return refs, None


def _property_text(prop: Any, prop_id: str) -> str:
    kind, payload = _property_payload(prop, prop_id)
    if kind not in {"title", "text", "richText"}:
        raise _MalformedObject(f"title property {prop_id!r} is not text")
    value = payload.get("value") if isinstance(payload, dict) else payload
    text = _text(value)
    if not text:
        raise _MalformedObject(f"property {prop_id!r} has no usable value")
    return text


def _property_number(prop: Any, prop_id: str) -> int | float:
    kind, payload = _property_payload(prop, prop_id)
    if kind not in {"number", "text"}:
        raise _MalformedObject(f"duration property {prop_id!r} is not numeric")
    value = payload.get("value") if isinstance(payload, dict) else payload
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise _MalformedObject(f"duration property {prop_id!r} is not numeric") from None
    if number < 0:
        raise _MalformedObject(f"duration property {prop_id!r} is negative")
    return int(number) if number.is_integer() else number


def _property_date(prop: Any, prop_id: str) -> date | None:
    kind, payload = _property_payload(prop, prop_id)
    if kind != "date" or not isinstance(payload, dict):
        raise _MalformedObject(f"date property {prop_id!r} is not a date")
    start = payload.get("start")
    if start in (None, ""):
        return None
    raw = _text(start)
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        raise _MalformedObject(f"date property {prop_id!r} has invalid start") from None


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
    def __init__(self, provider: CapacitiesProvider, config: CapacitiesConfig) -> None:
        if not _text(config.space_id):
            raise CapacitiesContractError("Capacities space_id is required")
        if not config.mappings:
            raise CapacitiesContractError("at least one Capacities structure mapping is required")
        if config.max_pages < 1:
            raise CapacitiesContractError("max_pages must be positive")
        if config.max_content_reads < 0:
            raise CapacitiesContractError("max_content_reads must be non-negative")
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
                raise CapacitiesContractError(f"duplicate or empty mapping for {sid!r}")
            structure = structures.get(sid)
            if structure is None:
                raise CapacitiesContractError(f"configured Capacities structure {sid!r} is unavailable")
            definitions = structure["_definitions"]
            # Any mapping without a source Assigned marker must map a status
            # property. For a custom structure that mapping does NOT include
            # anything on its own: the evaluator still requires the structure
            # to be Active-enabled (or the object source-assigned). Requiring
            # Active-enabled membership here would be circular, because a
            # structure has to be enumerable before the Settings drawer can
            # offer it for enabling.
            if not mapping.assignment_property and not mapping.open_status_property:
                # A stored ACTIVE rule is the structure's single eligibility
                # authority, so neither legacy mapping is required: the rule
                # (and the operator's explicit selection) can admit objects
                # the legacy seams cannot describe, and missing mappings are
                # manual-assignment candidates rather than a contract failure.
                # No record, a draft-only entry, and a record for another
                # space keep the legacy requirement unchanged.
                if self._active_rule(sid) is None:
                    if sid in self.config.assignment_settings.native_task_structures:
                        raise CapacitiesContractError(
                            f"native mapping {sid!r} requires a mapped status property"
                        )
                    raise CapacitiesContractError(
                        f"mapping {sid!r} requires an assignment property or a "
                        "mapped status property"
                    )
            if mapping.assignment_property and not mapping.assignment_values:
                raise CapacitiesContractError(f"mapping {sid!r} has no assignment values")
            if mapping.assignment_values and not mapping.assignment_property:
                raise CapacitiesContractError(
                    f"mapping {sid!r} has assignment values without an assignment property"
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
                        f"mapping {sid!r} references unknown property {prop_id!r}"
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
                declaration_warnings.append(
                    "ignored Capacities assignment declaration for structure "
                    f"{sid!r}: unknown property {mapping.assigned_property!r}"
                )
                mapping = replace(mapping, assigned_property=None)
            if mapping.open_status_property and not mapping.open_status_values:
                raise CapacitiesContractError(f"mapping {sid!r} has no open status values")
            if mapping.completion_property:
                if not mapping.completion_value:
                    raise CapacitiesContractError(f"mapping {sid!r} has no completion value")
                completion = definitions[mapping.completion_property]
                if completion.get("writable") is not True:
                    raise CapacitiesContractError(
                        f"completion property {mapping.completion_property!r} on {sid!r} is not writable"
                    )
                if _text(completion.get("type")) != "label":
                    raise CapacitiesContractError(
                        f"completion property {mapping.completion_property!r} on {sid!r} is not a label"
                    )
                if _normalized(mapping.completion_value) not in _label_options(completion):
                    raise CapacitiesContractError(
                        f"completion value {mapping.completion_value!r} is not defined for {sid!r}"
                    )
            mappings[sid] = mapping
        # Fail closed on an allowlist that silently omits the canonical native
        # structure: without it every native Capacities Task would vanish with
        # no warning, which is exactly the silent-absence failure this adapter
        # exists to prevent. A deliberate native-free configuration must be an
        # explicit, reviewed decision rather than an omission.
        if "RootTask" not in mappings:
            raise CapacitiesContractError("RootTask must be explicitly mapped")
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
        """Public enumeration-gate seam for the paced coordinator."""
        return self._structure_can_contribute(mapping)

    def _list_objects(self, structure_id: str) -> tuple[list[dict[str, Any]], int]:
        rows, pages, complete, reason = self._enumerate_listing(structure_id)
        if not complete:
            if reason == "page_bound":
                raise CapacitiesContractError(
                    f"Capacities pagination exceeded max_pages for {structure_id!r}"
                )
            if reason == "repeated_cursor":
                raise CapacitiesContractError(
                    f"Capacities pagination repeated cursor for {structure_id!r}"
                )
            raise CapacitiesContractError(
                f"Capacities object page for {structure_id!r} is malformed"
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

    def _project_object(
        self, obj: dict[str, Any], mapping: StructureMapping, logical_day: date
    ) -> dict[str, Any] | None:
        if not isinstance(obj, dict):
            raise _MalformedObject("object row is not an object")
        object_id = _text(obj.get("id"))
        if not object_id:
            raise _MalformedObject("object has no id")
        if _text(obj.get("structureId")) != mapping.structure_id:
            raise _MalformedObject(f"object {object_id!r} has the wrong structure")
        # The space guarantee comes from the space-scoped enumeration, not from
        # the payload: neither the scoped listing nor the object-content read
        # returns ``spaceId``. A row that *does* carry one must agree, so a
        # cross-space row is still rejected rather than trusted.
        payload_space = _text(obj.get("spaceId"))
        if payload_space and payload_space != self.config.space_id:
            raise _MalformedObject(f"object {object_id!r} is not in the configured space")
        properties = obj.get("properties")
        if not isinstance(properties, dict):
            raise _MalformedObject(f"object {object_id!r} has malformed properties")

        identity = f"capacities:{self.config.space_id}:{mapping.structure_id}:{object_id}"

        # A stored ACTIVE rule is decided up front: on that path the legacy
        # status/completion classification is replaced by a three-state
        # classification where an unreadable mapped value is UNKNOWN rather
        # than a failed object, and a missing assignment mapping is a manual
        # candidate rather than a contract failure (KTD5, R27/R29).
        active_rule = self._active_rule(mapping.structure_id)
        has_assignment_mapping = bool(
            mapping.assignment_property or mapping.assigned_property
        )

        # Source-assigned signal. A mapped assignment property that is present
        # is a definitive source boolean; a missing property is neutral, not a
        # negative. Native structures may omit the mapping entirely and rely on
        # the native Auto rule. The evaluator, not this projection, decides
        # precedence and eligibility.
        source_assigned: bool | None = None
        if mapping.assigned_property:
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
        if mapping.open_status_property:
            status_prop = properties.get(mapping.open_status_property)
            if active_rule is not None:
                status_state = _classify_open_status(
                    status_prop,
                    mapping.open_status_property,
                    mapping.open_status_values,
                )
            elif status_prop is None:
                status_is_open = False
            else:
                # The open/closed safety classification keeps matching the
                # mixed id+name tokens; the status token the evaluator reads
                # is keyed on the label display NAME (id only when absent), so
                # ``Active`` matches by name even when the id differs.
                status_tokens = _property_tokens(status_prop, mapping.open_status_property)
                display_tokens = _status_display_tokens(
                    status_prop, mapping.open_status_property
                )
                status = _status_token(display_tokens, self.config.assignment_settings)
                status_is_open = bool(
                    status_tokens.intersection(
                        {_normalized(value) for value in mapping.open_status_values}
                    )
                )
        elif active_rule is not None:
            status_state = "unavailable"

        # Completion availability on the stored-rule path. A mapping that
        # shares the open-status field checks the configured completed value
        # first — a mixed ``[active, done]`` payload is closed — and only
        # falls back to the open vocabulary when no completed token is
        # readable; a distinct completion field has only ``completion_value``
        # known, so every other readable value — and every absent, empty, or
        # unreadable value — is UNKNOWN rather than a guessed closed state
        # (R27/R29).
        completion_state: str | None = None
        if active_rule is not None:
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
                    open_values=mapping.open_status_values,
                )
            else:
                completion_state = _classify_completion(
                    properties.get(mapping.completion_property),
                    mapping.completion_property,
                    mapping.completion_value,
                )

        # ``date_property`` remains a day-projection filter: a future date means
        # the object is not part of today's candidate set. The evaluator owns
        # the native deadline horizon from ``deadline_property``.
        due: date | None = None
        if mapping.date_property:
            date_prop = properties.get(mapping.date_property)
            if date_prop is not None:
                due = _property_date(date_prop, mapping.date_property)
                if due is not None and due > logical_day:
                    return None

        deadline: date | None = None
        if mapping.deadline_property:
            deadline_prop = properties.get(mapping.deadline_property)
            if deadline_prop is not None:
                deadline = _property_date(deadline_prop, mapping.deadline_property)

        title_prop = properties.get(mapping.title_property)
        if title_prop is None:
            raise _MalformedObject(f"object {object_id!r} has no title")
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
            raise _MalformedObject(f"object {object_id!r} has an empty title")

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
                return None
            if status_state == "closed" or completion_state == "closed":
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
            if not decision.eligible:
                return None
            assigned = True

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
        row = {
            "id": title,
            "name": title,
            "path": path,
            "identity": identity,
            "source": "capacities",
            "types": [mapping.structure_id],
            "urgency": None,
            "deadline": due.isoformat() if due else None,
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
            # but stays visible, and closed values never reach a row.
            row["capacities_status_state"] = status_state
            row["capacities_completion_state"] = completion_state
        else:
            # Compact serialization of the evaluator decision, never a title
            # or a policy key. Downstream index allowlists may ignore it.
            row["capacities_assignment"] = {
                "mode": decision.mode.value,
                "reasons": list(decision.reason_codes),
                "source_assigned": decision.provenance.source_assigned,
                "excluded": decision.provenance.exclusion_matched,
            }
        if tag_error is not None:
            row["capacities_tags_error"] = tag_error
        return row

    def _project_objects(
        self, logical_day: date, objects: Sequence[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], list[str], set[str], set[str]]:
        """Project listed objects and account for every distinct identity.

        Returns ``(items, warnings, evaluated, malformed)``. ``evaluated``
        holds the identities that projected successfully — including objects
        the projection then filtered out as ineligible, which are evaluated
        even though they never become items. ``malformed`` holds the
        identities whose projection failed; a malformed row with no usable
        identity is reported by its warning alone, because there is nothing
        to count it once by. Both sets are keyed on the listed identity so
        repeated rows cannot inflate coverage.
        """
        self._ensure_contract()
        assert self._mappings is not None
        items: list[dict[str, Any]] = []
        warnings: list[str] = []
        evaluated: set[str] = set()
        malformed: set[str] = set()
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
                continue
            if identity:
                evaluated.add(identity)
            if row is not None:
                items.append(row)
        items.sort(key=lambda row: (_normalized(row["name"]), row["identity"]))
        return items, warnings, evaluated, malformed

    def items_for_day_from_objects(
        self, logical_day: date, objects: Sequence[dict[str, Any]]
    ) -> CapacitiesReadResult:
        items, warnings, evaluated, malformed = self._project_objects(logical_day, objects)
        warnings = [*self._contract_warnings, *warnings]
        return CapacitiesReadResult(
            items=items,
            warnings=warnings,
            evaluated=len(evaluated),
            malformed=len(malformed),
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

        A structure configured as native is always evaluated (its Auto rules
        are the only inclusion path). A custom structure with a source
        assignment property can contribute a source-assigned row. A structure
        with an ACTIVE stored rule contributes even without a legacy
        assignment property or Active enable, because the rule is its
        eligibility path. A custom structure with none of those can never
        include anything, so it is not enumerated and its objects are never
        hydrated — which matters because the live API allows 30 requests per
        minute and enumeration plus hydration is otherwise an N+1 burst.
        """
        if mapping.structure_id in self.config.assignment_settings.native_task_structures:
            return True
        if mapping.assignment_property:
            return True
        if mapping.structure_id in self.config.assignment_settings.active_structures:
            return True
        return self._active_rule(mapping.structure_id) is not None

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
        all_items: list[dict[str, Any]] = []
        warnings: list[str] = list(self._contract_warnings)
        evaluated: set[str] = set()
        malformed: set[str] = set()
        page_count = 0
        for structure_id, mapping in self._mappings.items():
            if not self._structure_can_contribute(mapping):
                continue
            objects, pages = self._list_objects(structure_id)
            page_count += pages
            items, batch_warnings, batch_evaluated, batch_malformed = (
                self._project_objects(logical_day, objects)
            )
            all_items.extend(items)
            warnings.extend(batch_warnings)
            evaluated |= batch_evaluated
            malformed |= batch_malformed
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
        warnings.extend(self._drain_cache_warnings())
        all_items.sort(key=lambda row: (_normalized(row["name"]), row["identity"]))
        return CapacitiesReadResult(
            items=all_items,
            warnings=warnings,
            pages=page_count,
            evaluated=len(evaluated),
            deferred=len(deferred),
            malformed=len(malformed),
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
