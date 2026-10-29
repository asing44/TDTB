"""capacities_schema.py — pure schema fingerprint and content-free diff.

Capacities is owner-mutated: a status option can be deleted, a property
retyped, or a whole structure added between two refreshes, and the app then
reads a contract that no longer describes what the operator sees. This module
turns "did the owner change Capacities?" into two deterministic, side-effect
free operations over the structures payload the adapter already reads:

``fingerprint(payload)``
    A sha256 hex digest of identity and type only — structure ids, property
    ids, property types, and option ids. Ordering, display names, titles,
    timestamps, counts, and writability flags are excluded, so reordering the
    payload or renaming anything in Capacities leaves the digest unchanged.

``diff(previous, current, ...)``
    Every difference between two payloads, classified as added / removed /
    retyped / renamed, plus how many recorded open status values no longer
    resolve against the current option ids and names. Names are only ever
    *compared*; the resulting :class:`SchemaDiff` carries ids and counts so it
    can be logged, stored, or rendered without leaking titles or values.

Payload shape (the one ``CapacitiesAdapter._ensure_contract`` reads)::

    {"structures": [
        {"id": "<structure id>",
         "title": "<display title>",
         "propertyDefinitions": [
             {"id": "<property id>", "name": "<label>", "type": "label",
              "writable": true,
              "labelSet": [{"id": "<option id>", "name": "<label>"}]}]}]}

A bare ``list`` of rows is accepted, and so is a document that wraps the
payload one level deeper (the stamped contract record in
``capacities_refresh_state`` stores it under ``"structures"``). A structure's
``propertyDefinitions`` may also be a mapping keyed by property id; the
definition's own ``id`` wins, which is what the adapter's ``{"id": key,
**definition}`` merge does. Structure ids fall back to ``structureId`` exactly
like the adapter.

Robustness contract: nothing here raises on malformed input. A row or
definition that cannot be read is skipped and marked in
``normalize_structures(...)["malformed"]`` (position/identifier tokens only,
never a title or a value), so one broken row can never break a refresh.

Purity contract: this module imports only the standard library, opens no
file, performs no network call, and reads no configuration or credential.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

#: Bumped when the shape of :func:`to_dict` changes incompatibly.
SCHEMA_VERSION = 1

SEVERITY_NONE = "none"
SEVERITY_INFO = "info"
SEVERITY_WARN = "warn"

#: Bounded unwrap depth for a payload document that nests ``structures``.
_MAX_UNWRAP = 3

_SUMMARY_PREFIX = "Capacities changed since the last refresh: "
_SUMMARY_SUFFIX = ". Review the mapping in Connections."


# ---------------------------------------------------------------------------
# Small tolerant readers (mirroring the adapter's own helpers)
# ---------------------------------------------------------------------------


def _text(value: Any) -> str:
    """Trimmed string form; ``None`` and empty values become ``""``.

    Identical to ``capacities_adapter._text`` so ids normalize the same way
    the adapter keys them.
    """
    return str(value or "").strip()


def _normalized(value: Any) -> str:
    """Casefolded, whitespace-collapsed comparison form (adapter parity)."""
    return " ".join(_text(value).casefold().split())


def _structure_id(row: Mapping[str, Any]) -> str:
    return _text(row.get("id") or row.get("structureId"))


def _structure_rows(payload: Any) -> tuple[list[Any], bool]:
    """Best-effort structure rows from a payload. Never raises.

    Returns ``(rows, ok)``. ``ok`` is False when the input was not a rows
    container at all (the caller marks the payload itself as malformed).
    """
    current = payload
    depth = 0
    while (
        isinstance(current, Mapping)
        and "structures" in current
        and depth < _MAX_UNWRAP
    ):
        current = current.get("structures")
        depth += 1
    if isinstance(current, (list, tuple)):
        return list(current), True
    if current is None:
        return [], True
    return [], False


def _label_options(
    structure_id: str, prop_id: str, definition: Mapping[str, Any]
) -> tuple[tuple[str, ...], dict[str, str], list[str]]:
    """Canonical option ids, their display names, and any malformed markers."""
    raw = definition.get("labelSet")
    if raw is None:
        return (), {}, []
    if not isinstance(raw, (list, tuple)):
        return (), {}, [f"labelset:{structure_id}.{prop_id}"]
    names: dict[str, str] = {}
    markers: list[str] = []
    for entry in raw:
        if not isinstance(entry, Mapping):
            markers.append(f"option:{structure_id}.{prop_id}")
            continue
        option_id = _text(entry.get("id"))
        if not option_id:
            markers.append(f"option:{structure_id}.{prop_id}")
            continue
        if option_id in names:
            markers.append(f"duplicate-option:{structure_id}.{prop_id}.{option_id}")
            continue
        names[option_id] = _text(entry.get("name"))
    return tuple(sorted(names)), names, markers


def _structure_properties(
    structure_id: str, row: Mapping[str, Any]
) -> tuple[dict[str, dict[str, Any]], dict[str, Any], list[str]]:
    """Canonical properties, display names, and malformed markers for one row."""
    canonical: dict[str, dict[str, Any]] = {}
    names: dict[str, Any] = {"properties": {}, "options": {}}
    markers: list[str] = []
    raw = row.get("propertyDefinitions")

    pairs: list[tuple[str, Mapping[str, Any]]] = []
    if isinstance(raw, Mapping):
        for key, definition in raw.items():
            if not isinstance(definition, Mapping):
                markers.append(f"definition:{structure_id}")
                continue
            prop_id = _text(
                definition.get("id") or definition.get("structureId") or _text(key)
            )
            pairs.append((prop_id, definition))
    elif isinstance(raw, (list, tuple)):
        for entry in raw:
            if not isinstance(entry, Mapping):
                markers.append(f"definition:{structure_id}")
                continue
            pairs.append((_text(entry.get("id") or entry.get("structureId")), entry))
    else:
        markers.append(f"definitions:{structure_id}")
        return canonical, names, markers

    for prop_id, definition in pairs:
        if not prop_id:
            markers.append(f"definition:{structure_id}")
            continue
        if prop_id in canonical:
            markers.append(f"duplicate-property:{structure_id}.{prop_id}")
            continue
        options, option_names, option_markers = _label_options(
            structure_id, prop_id, definition
        )
        canonical[prop_id] = {
            "type": _normalized(definition.get("type")),
            "options": options,
        }
        names["properties"][prop_id] = _text(definition.get("name"))
        if option_names:
            names["options"][prop_id] = option_names
        markers.extend(option_markers)
    return canonical, names, markers


# ---------------------------------------------------------------------------
# normalize / fingerprint
# ---------------------------------------------------------------------------


def normalize_structures(payload: Any) -> dict[str, Any]:
    """Canonical, deterministic view of a Capacities structures payload.

    Returns ``{"structures", "display_names", "structure_names", "malformed"}``:

    - ``structures``: ``{structure id: {property id: {"type": str,
      "options": tuple(sorted option ids)}}}`` — identity and type only.
    - ``display_names``: ``{structure id: {"properties": {property id: name},
      "options": {property id: {option id: name}}}}``. Names are display
      metadata: compared by :func:`diff`, never fingerprinted.
    - ``structure_names``: ``{structure id: display title}``.
    - ``malformed``: sorted markers for rows/definitions that were skipped.
      Markers are positions or identifiers (``"#2"``,
      ``"duplicate-structure:A"``), never a title or a value.

    Never raises. ``None`` and an empty payload both normalize to no
    structures.
    """
    markers: list[str] = []
    rows, rows_ok = _structure_rows(payload)
    if not rows_ok:
        markers.append("payload")

    structures: dict[str, dict[str, Any]] = {}
    display_names: dict[str, dict[str, Any]] = {}
    structure_names: dict[str, str] = {}
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            markers.append(f"#{index}")
            continue
        structure_id = _structure_id(row)
        if not structure_id:
            markers.append(f"#{index}")
            continue
        if structure_id in structures:
            markers.append(f"duplicate-structure:{structure_id}")
            continue
        canonical, names, row_markers = _structure_properties(structure_id, row)
        structures[structure_id] = canonical
        display_names[structure_id] = names
        structure_names[structure_id] = _text(row.get("title"))
        markers.extend(row_markers)

    return {
        "structures": structures,
        "display_names": display_names,
        "structure_names": structure_names,
        "malformed": tuple(sorted(markers)),
    }


def _canonical_json(structures: Mapping[str, Any]) -> str:
    return json.dumps(structures, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _fingerprint_of(structures: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(structures).encode("utf-8")).hexdigest()


def fingerprint(payload: Any) -> str:
    """sha256 hex of the identity/type canonical form of ``payload``.

    Excludes ordering, display names and titles, timestamps, counts, and
    writability/permission flags: two payloads that differ only by those
    produce the same digest. ``None`` hashes the same as an empty payload.
    """
    return _fingerprint_of(normalize_structures(payload)["structures"])


# ---------------------------------------------------------------------------
# diff
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SchemaDiff:
    """A content-free description of what changed between two payloads.

    Every field carries structure/property/option **ids** and counts only; no
    name, title, value, or object data appears anywhere in it.
    """

    baseline: bool = False
    structures_added: tuple[str, ...] = ()
    structures_removed: tuple[str, ...] = ()
    properties_added: tuple[tuple[str, str], ...] = ()
    properties_removed: tuple[tuple[str, str], ...] = ()
    properties_retyped: tuple[tuple[str, str, str, str], ...] = ()
    properties_renamed: tuple[tuple[str, str], ...] = ()
    options_added: tuple[tuple[str, str, str], ...] = ()
    options_removed: tuple[tuple[str, str, str], ...] = ()
    options_renamed: tuple[tuple[str, str, str], ...] = ()
    unresolved_open_values: dict[str, tuple[int, int]] = field(default_factory=dict)
    severity: str = SEVERITY_NONE
    fingerprint_before: str = ""
    fingerprint_after: str = ""


def _mapped_ids(mapped_structure_ids: Any) -> frozenset[str] | None:
    if mapped_structure_ids is None:
        return None
    if isinstance(mapped_structure_ids, str):
        return frozenset({_text(mapped_structure_ids)})
    try:
        return frozenset(_text(value) for value in mapped_structure_ids)
    except TypeError:
        return frozenset()


def _open_values_by_structure(open_status_values: Any) -> dict[str, tuple[Any, ...]]:
    if not isinstance(open_status_values, Mapping):
        return {}
    values_by_structure: dict[str, tuple[Any, ...]] = {}
    for key, values in open_status_values.items():
        structure_id = _text(key)
        if not structure_id or structure_id in values_by_structure:
            continue
        if isinstance(values, (str, bytes)) or not isinstance(values, Iterable):
            values = ()
        try:
            values_by_structure[structure_id] = tuple(values)
        except TypeError:
            values_by_structure[structure_id] = ()
    return values_by_structure


def _resolvable_tokens(
    canonical: Mapping[str, Any], names: Mapping[str, Any]
) -> set[str]:
    """Normalized tokens an open status value may equal for one structure.

    Option ids and option names of every label-bearing property (any property
    with a ``labelSet``), compared case-insensitively the way the adapter's
    own ``_normalized`` comparison works.
    """
    option_names = names.get("options") if isinstance(names, Mapping) else None
    tokens: set[str] = set()
    for prop_id, entry in canonical.items():
        for option_id in entry.get("options") or ():
            token = _normalized(option_id)
            if token:
                tokens.add(token)
            if isinstance(option_names, Mapping):
                name = option_names.get(prop_id)
                if isinstance(name, Mapping):
                    token = _normalized(name.get(option_id))
                    if token:
                        tokens.add(token)
    return tokens


def _property_name(names: Mapping[str, Any], prop_id: str) -> str:
    properties = names.get("properties") if isinstance(names, Mapping) else None
    if not isinstance(properties, Mapping):
        return ""
    return _text(properties.get(prop_id))


def _option_name(names: Mapping[str, Any], prop_id: str, option_id: str) -> str:
    options = names.get("options") if isinstance(names, Mapping) else None
    if not isinstance(options, Mapping):
        return ""
    per_property = options.get(prop_id)
    if not isinstance(per_property, Mapping):
        return ""
    return _text(per_property.get(option_id))


def diff(
    previous: Any,
    current: Any,
    *,
    mapped_structure_ids: Any = None,
    open_status_values: Any = None,
) -> SchemaDiff:
    """Classify every schema difference between ``previous`` and ``current``.

    ``previous`` may be ``None`` when no baseline was ever stamped: the result
    is then empty with ``baseline=True`` and severity ``"none"``.

    ``mapped_structure_ids`` bounds what can *warn*: a removed or retyped
    property, a removed option, a no-longer-resolving open value, or a removed
    structure is ``"warn"`` only when it belongs to a mapped structure.
    ``None`` means every structure is mapped. Added structures, properties and
    options, and name-only renames are always ``"info"``.

    ``open_status_values`` is ``{structure id: [values]}``; a value resolves
    when it equals an option id or an option name (case-insensitive) of any
    label-bearing property of that structure. Structures absent from
    ``current``, and ids not present in the payload, are omitted.
    """
    after = normalize_structures(current)
    after_structures = after["structures"]
    after_fingerprint = _fingerprint_of(after_structures)

    if previous is None:
        return SchemaDiff(
            baseline=True,
            severity=SEVERITY_NONE,
            fingerprint_before="",
            fingerprint_after=after_fingerprint,
        )

    before = normalize_structures(previous)
    before_structures = before["structures"]
    mapped = _mapped_ids(mapped_structure_ids)

    before_ids = set(before_structures)
    after_ids = set(after_structures)
    structures_added = tuple(sorted(after_ids - before_ids))
    structures_removed = tuple(sorted(before_ids - after_ids))

    properties_added: list[tuple[str, str]] = []
    properties_removed: list[tuple[str, str]] = []
    properties_retyped: list[tuple[str, str, str, str]] = []
    properties_renamed: list[tuple[str, str]] = []
    options_added: list[tuple[str, str, str]] = []
    options_removed: list[tuple[str, str, str]] = []
    options_renamed: list[tuple[str, str, str]] = []

    for structure_id in sorted(before_ids & after_ids):
        before_props = before_structures[structure_id]
        after_props = after_structures[structure_id]
        before_names = before["display_names"].get(structure_id) or {}
        after_names = after["display_names"].get(structure_id) or {}

        for prop_id in sorted(set(after_props) - set(before_props)):
            properties_added.append((structure_id, prop_id))
        for prop_id in sorted(set(before_props) - set(after_props)):
            properties_removed.append((structure_id, prop_id))
        for prop_id in sorted(set(before_props) & set(after_props)):
            old = before_props[prop_id]
            new = after_props[prop_id]
            if old.get("type") != new.get("type"):
                properties_retyped.append(
                    (structure_id, prop_id, _text(old.get("type")), _text(new.get("type")))
                )
            if _property_name(before_names, prop_id) != _property_name(after_names, prop_id):
                properties_renamed.append((structure_id, prop_id))
            old_options = set(old.get("options") or ())
            new_options = set(new.get("options") or ())
            for option_id in sorted(new_options - old_options):
                options_added.append((structure_id, prop_id, option_id))
            for option_id in sorted(old_options - new_options):
                options_removed.append((structure_id, prop_id, option_id))
            for option_id in sorted(old_options & new_options):
                if _option_name(before_names, prop_id, option_id) != _option_name(
                    after_names, prop_id, option_id
                ):
                    options_renamed.append((structure_id, prop_id, option_id))

    unresolved: dict[str, tuple[int, int]] = {}
    for structure_id, values in sorted(
        _open_values_by_structure(open_status_values).items()
    ):
        canonical = after_structures.get(structure_id)
        if canonical is None:
            continue
        tokens = _resolvable_tokens(canonical, after["display_names"].get(structure_id) or {})
        unresolved_count = sum(
            1 for value in values if _normalized(value) not in tokens
        )
        unresolved[structure_id] = (unresolved_count, len(values))

    def is_mapped(structure_id: str) -> bool:
        return mapped is None or structure_id in mapped

    warn = any(is_mapped(structure_id) for structure_id in structures_removed)
    warn = warn or any(is_mapped(structure_id) for structure_id, _ in properties_removed)
    warn = warn or any(
        is_mapped(structure_id) for structure_id, _, _, _ in properties_retyped
    )
    warn = warn or any(
        is_mapped(structure_id) for structure_id, _, _ in options_removed
    )
    warn = warn or any(
        is_mapped(structure_id) and unresolved_count > 0
        for structure_id, (unresolved_count, _) in unresolved.items()
    )

    changed = bool(
        structures_added
        or structures_removed
        or properties_added
        or properties_removed
        or properties_retyped
        or properties_renamed
        or options_added
        or options_removed
        or options_renamed
        or any(unresolved_count for unresolved_count, _ in unresolved.values())
    )

    if warn:
        severity = SEVERITY_WARN
    elif changed:
        severity = SEVERITY_INFO
    else:
        severity = SEVERITY_NONE

    return SchemaDiff(
        baseline=False,
        structures_added=structures_added,
        structures_removed=structures_removed,
        properties_added=tuple(properties_added),
        properties_removed=tuple(properties_removed),
        properties_retyped=tuple(properties_retyped),
        properties_renamed=tuple(properties_renamed),
        options_added=tuple(options_added),
        options_removed=tuple(options_removed),
        options_renamed=tuple(options_renamed),
        unresolved_open_values=unresolved,
        severity=severity,
        fingerprint_before=_fingerprint_of(before_structures),
        fingerprint_after=after_fingerprint,
    )


# ---------------------------------------------------------------------------
# Presentation
# ---------------------------------------------------------------------------


def _plural(count: int, singular: str, plural: str) -> str:
    return f"{count} {singular if count == 1 else plural}"


def summary_text(result: SchemaDiff) -> str:
    """One short operator sentence built from counts; ``""`` when unchanged."""
    if result.severity == SEVERITY_NONE:
        return ""
    clauses: list[str] = []
    for count, singular, plural in (
        (len(result.options_removed), "status option removed", "status options removed"),
        (len(result.options_added), "status option added", "status options added"),
        (len(result.structures_removed), "type removed", "types removed"),
        (len(result.structures_added), "new type", "new types"),
        (len(result.properties_removed), "property removed", "properties removed"),
        (len(result.properties_added), "property added", "properties added"),
        (len(result.properties_retyped), "property retyped", "properties retyped"),
        (len(result.properties_renamed), "property renamed", "properties renamed"),
        (len(result.options_renamed), "status option renamed", "status options renamed"),
    ):
        if count:
            clauses.append(_plural(count, singular, plural))
    unresolved_total = sum(
        unresolved_count
        for unresolved_count, _total in result.unresolved_open_values.values()
    )
    if unresolved_total:
        clauses.append(
            _plural(
                unresolved_total,
                "open status value no longer resolves",
                "open status values no longer resolve",
            )
        )
    if not clauses:
        clauses.append("schema changed")
    return _SUMMARY_PREFIX + ", ".join(clauses) + _SUMMARY_SUFFIX


def to_dict(result: SchemaDiff) -> dict[str, Any]:
    """JSON-safe, content-free document for logging, storage, or a route body.

    Ids and counts only: no name, title, value, or object data. The
    ``schema_version`` makes a later format change detectable.
    """
    unresolved = {
        structure_id: [unresolved_count, total]
        for structure_id, (unresolved_count, total) in sorted(
            result.unresolved_open_values.items()
        )
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "severity": result.severity,
        "baseline": result.baseline,
        "fingerprint_before": result.fingerprint_before,
        "fingerprint_after": result.fingerprint_after,
        "counts": {
            "structures_added": len(result.structures_added),
            "structures_removed": len(result.structures_removed),
            "properties_added": len(result.properties_added),
            "properties_removed": len(result.properties_removed),
            "properties_retyped": len(result.properties_retyped),
            "properties_renamed": len(result.properties_renamed),
            "options_added": len(result.options_added),
            "options_removed": len(result.options_removed),
            "options_renamed": len(result.options_renamed),
            "unresolved_open_values": sum(
                unresolved_count for unresolved_count, _total in unresolved.values()
            ),
        },
        "structures_added": list(result.structures_added),
        "structures_removed": list(result.structures_removed),
        "properties_added": [list(row) for row in result.properties_added],
        "properties_removed": [list(row) for row in result.properties_removed],
        "properties_retyped": [list(row) for row in result.properties_retyped],
        "properties_renamed": [list(row) for row in result.properties_renamed],
        "options_added": [list(row) for row in result.options_added],
        "options_removed": [list(row) for row in result.options_removed],
        "options_renamed": [list(row) for row in result.options_renamed],
        "unresolved_open_values": unresolved,
    }
