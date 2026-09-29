"""Application-owned Capacities REST source adapter.

The adapter is deliberately contract-driven: structure definitions and
per-structure mappings are supplied by TDTB configuration, candidate objects
are enumerated with bounded pagination, and eligibility is evaluated locally
from typed properties.  It does not use the Capacities MCP server, title-based
matching, or a generic synchronization model.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import date
import hashlib
import json
from typing import Any, Protocol, Sequence

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


class CapacitiesProvider(Protocol):
    """Small provider seam used by both the REST client and deterministic fakes."""

    def fetch_structures(self) -> Sequence[dict[str, Any]] | dict[str, Any]: ...

    def list_objects(self, structure_id: str, cursor: str | None = None) -> dict[str, Any]: ...

    def get_object(self, object_id: str) -> dict[str, Any]: ...

    def patch_object(self, object_id: str, properties: dict[str, Any]) -> dict[str, Any]: ...


@dataclass(frozen=True)
class StructureMapping:
    """Explicit property ownership for one Capacities structure.

    ``assignment_property`` and ``assignment_values`` are required so an
    object cannot enter TDTB merely because it exists in a Capacities space.
    Optional fields are capabilities, not guesses: absent mappings never cause
    TDTB to write a similarly named source field.
    """

    structure_id: str
    assignment_property: str
    assignment_values: frozenset[str]
    title_property: str = "title"
    date_property: str | None = None
    open_status_property: str | None = None
    open_status_values: frozenset[str] = frozenset()
    duration_property: str | None = None
    completion_property: str | None = None
    completion_value: str | None = None


@dataclass(frozen=True)
class CapacitiesConfig:
    space_id: str
    mappings: tuple[StructureMapping, ...]
    max_pages: int = 20


@dataclass
class CapacitiesReadResult:
    items: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    pages: int = 0


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
        self.provider = provider
        self.config = config
        self._structure_defs: dict[str, dict[str, Any]] | None = None
        self._mappings: dict[str, StructureMapping] | None = None

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
        for mapping in self.config.mappings:
            sid = _text(mapping.structure_id)
            if not sid or sid in mappings:
                raise CapacitiesContractError(f"duplicate or empty mapping for {sid!r}")
            structure = structures.get(sid)
            if structure is None:
                raise CapacitiesContractError(f"configured Capacities structure {sid!r} is unavailable")
            definitions = structure["_definitions"]
            required = [mapping.title_property, mapping.assignment_property]
            optional = [
                mapping.date_property,
                mapping.open_status_property,
                mapping.duration_property,
                mapping.completion_property,
            ]
            for prop_id in [*required, *(p for p in optional if p)]:
                if prop_id not in definitions:
                    raise CapacitiesContractError(
                        f"mapping {sid!r} references unknown property {prop_id!r}"
                    )
            if not mapping.assignment_values:
                raise CapacitiesContractError(f"mapping {sid!r} has no assignment values")
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
        if "RootTask" not in mappings:
            raise CapacitiesContractError("RootTask must be explicitly mapped")
        self._structure_defs = structures
        self._mappings = mappings

    def _list_objects(self, structure_id: str) -> tuple[list[dict[str, Any]], int]:
        objects: list[dict[str, Any]] = []
        cursor: str | None = None
        seen_cursors: set[str] = set()
        pages = 0
        while True:
            if pages >= self.config.max_pages:
                raise CapacitiesContractError(
                    f"Capacities pagination exceeded max_pages for {structure_id!r}"
                )
            page = self.provider.list_objects(structure_id, cursor)
            if not isinstance(page, dict) or not isinstance(page.get("objects"), list):
                raise CapacitiesContractError(
                    f"Capacities object page for {structure_id!r} is malformed"
                )
            objects.extend(page["objects"])
            pages += 1
            next_cursor = page.get("next_cursor", page.get("nextCursor"))
            if next_cursor in (None, ""):
                return objects, pages
            next_cursor = _text(next_cursor)
            if next_cursor in seen_cursors:
                raise CapacitiesContractError(
                    f"Capacities pagination repeated cursor for {structure_id!r}"
                )
            seen_cursors.add(next_cursor)
            cursor = next_cursor

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
        space_id = _text(obj.get("spaceId"))
        if space_id and space_id != self.config.space_id:
            raise _MalformedObject(f"object {object_id!r} belongs to another space")
        properties = obj.get("properties")
        if not isinstance(properties, dict):
            raise _MalformedObject(f"object {object_id!r} has malformed properties")

        assignment = properties.get(mapping.assignment_property)
        if assignment is None:
            return None
        assignment_values = _property_tokens(assignment, mapping.assignment_property)
        if not assignment_values.intersection(
            {_normalized(value) for value in mapping.assignment_values}
        ):
            return None

        if mapping.open_status_property:
            status = properties.get(mapping.open_status_property)
            if status is None:
                return None
            status_values = _property_tokens(status, mapping.open_status_property)
            if not status_values.intersection(
                {_normalized(value) for value in mapping.open_status_values}
            ):
                return None

        logical_date: date | None = None
        if mapping.date_property:
            date_prop = properties.get(mapping.date_property)
            if date_prop is not None:
                logical_date = _property_date(date_prop, mapping.date_property)
                if logical_date is not None and logical_date > logical_day:
                    return None

        title_prop = properties.get(mapping.title_property)
        if title_prop is None:
            raise _MalformedObject(f"object {object_id!r} has no title")
        title = _property_text(title_prop, mapping.title_property)
        if not title:
            raise _MalformedObject(f"object {object_id!r} has an empty title")

        duration: int | float = 30
        if mapping.duration_property:
            duration_prop = properties.get(mapping.duration_property)
            if duration_prop is not None:
                duration = _property_number(duration_prop, mapping.duration_property)

        identity = f"capacities:{self.config.space_id}:{mapping.structure_id}:{object_id}"
        path = f"capacities://{self.config.space_id}/{object_id}"
        blocks = duration / 30
        if isinstance(blocks, float) and blocks.is_integer():
            blocks = int(blocks)
        return {
            "id": title,
            "name": title,
            "path": path,
            "identity": identity,
            "source": "capacities",
            "types": [mapping.structure_id],
            "urgency": None,
            "deadline": logical_date.isoformat() if logical_date else None,
            "priority_score": 0,
            "assigned": True,
            "duration": duration,
            "duration_minutes": duration,
            "blocks": blocks,
            "capacities_id": object_id,
            "capacities_space_id": self.config.space_id,
            "capacities_structure_id": mapping.structure_id,
            "capacities_completion_supported": bool(mapping.completion_property),
            "source_fingerprint": _fingerprint(obj),
        }

    def items_for_day_from_objects(
        self, logical_day: date, objects: Sequence[dict[str, Any]]
    ) -> CapacitiesReadResult:
        self._ensure_contract()
        assert self._mappings is not None
        result = CapacitiesReadResult()
        for obj in objects:
            structure_id = _text(obj.get("structureId")) if isinstance(obj, dict) else ""
            mapping = self._mappings.get(structure_id)
            if mapping is None:
                result.warnings.append(
                    f"ignored Capacities object without an allowlisted structure: {structure_id or '<missing>'}"
                )
                continue
            try:
                row = self._project_object(obj, mapping, logical_day)
            except _MalformedObject as exc:
                object_id = _text(obj.get("id")) if isinstance(obj, dict) else "<unknown>"
                result.warnings.append(f"skipped malformed Capacities object {object_id}: {exc}")
                continue
            if row is not None:
                result.items.append(row)
        result.items.sort(key=lambda row: (_normalized(row["name"]), row["identity"]))
        return result

    def items_for_day(self, logical_day: date) -> CapacitiesReadResult:
        self._ensure_contract()
        assert self._mappings is not None
        all_items: list[dict[str, Any]] = []
        warnings: list[str] = []
        page_count = 0
        for structure_id in self._mappings:
            objects, pages = self._list_objects(structure_id)
            page_count += pages
            projected = self.items_for_day_from_objects(logical_day, objects)
            all_items.extend(projected.items)
            warnings.extend(projected.warnings)
        all_items.sort(key=lambda row: (_normalized(row["name"]), row["identity"]))
        return CapacitiesReadResult(all_items, warnings, page_count)

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
        base_url: str = "https://api.capacities.io",
        timeout: float = 10.0,
        headers: dict[str, str] | None = None,
        transport: Any = None,
    ) -> None:
        if not _text(token):
            raise ValueError("Capacities API token is required")
        import httpx

        request_headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
            **(headers or {}),
        }
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers=request_headers,
            timeout=timeout,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def _json(self, response: Any) -> dict[str, Any]:
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise CapacitiesContractError("Capacities API returned a non-object response")
        return payload

    def fetch_structures(self) -> dict[str, Any]:
        return self._json(self._client.get("/space/structures"))

    def list_objects(self, structure_id: str, cursor: str | None = None) -> dict[str, Any]:
        params: dict[str, str] = {"id": structure_id}
        if cursor:
            params["cursor"] = cursor
        payload = self._json(self._client.get("/objects/structure", params=params))
        return {
            "objects": payload.get("objects", []),
            "next_cursor": payload.get("nextCursor", payload.get("next_cursor")),
        }

    def get_object(self, object_id: str) -> dict[str, Any]:
        return self._json(self._client.get("/object", params={"id": object_id}))

    def patch_object(self, object_id: str, properties: dict[str, Any]) -> dict[str, Any]:
        return self._json(
            self._client.patch(
                "/object",
                json={"id": object_id, "properties": properties},
            )
        )
