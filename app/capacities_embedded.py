"""capacities_embedded.py — page-body parent→child membership for Capacities.

A Capacities Task carries no property that points at a parent, and a Project or
Continual carries no ``tasks`` property: the only parent→child link is the
parent's page body. This module owns the two pure pieces of reading that link:

1. :func:`embedded_entity_ids` — every ``EntityBlock`` embed in one page body,
   at any nesting depth (a ``GridBlock`` puts its cells in ``columns``, a
   toggle or a group nests further ``blocks``). An inline ``LinkToken`` inside
   a ``TextBlock`` is a *reference*, not membership, and is deliberately never
   collected: a task merely mentioned in a note is not the note's child.
2. :func:`embedded_child_map` — ``child_id -> (parent_id, parent_title)`` over
   the objects one refresh already hydrated, so the adapter can ask "is this
   task embedded in a parent that is admitted on this same read?".

:func:`read_blocks` is the fail-open read seam: a page body that cannot be
read answers ``None`` plus one short warning, and the caller must then hide
nothing. Hiding is a display decision, so an unread body can only ever fail
open.

``blocks`` here is the object's body, never the row's duration in blocks: the
adapter's projected row already spends that key on ``duration / 30``.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

#: The block type that embeds another object as a child of this page.
ENTITY_BLOCK = "entityblock"

#: The fixed, content-free warning a failed page-body read contributes.
BLOCKS_READ_WARNING = "page body unreadable"

#: The two structures whose page bodies can embed a child task: Project and
#: Continual. A refresh reads a page body for these structures only, because no
#: other structure is an embedding parent in the owner's Capacities model; the
#: ids are the live structure ids, the same way ``NATIVE_TASK_STRUCTURES`` names
#: the native task structure by id rather than by display name.
EMBEDDING_PARENT_STRUCTURES = frozenset(
    {
        "0d194525-c5a1-4af5-bb62-202b83006b5e",  # Project
        "e16f8eeb-1d66-4f21-8d3a-adaeff0a75be",  # Continual
    }
)


def _text(value: Any) -> str | None:
    """A trimmed string, or ``None`` for anything else."""
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _walk(value: Any) -> Iterable[dict[str, Any]]:
    """Every mapping at any depth under ``value``, in a stable document order."""
    stack: list[Any] = [value]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            yield current
            stack.extend(reversed(list(current.values())))
        elif isinstance(current, list):
            stack.extend(reversed(current))


def embedded_entity_ids(blocks: Any) -> tuple[str, ...]:
    """Entity ids of every ``EntityBlock`` embed in one page body.

    ``blocks`` is the body mapping (``{"<propertyId>": [block, ...]}``) or any
    block list inside it; a non-mapping contributes nothing. Depth is
    unlimited, an ``entityId`` is collected once in first-seen order, and a
    ``LinkToken`` or any other token type is ignored.
    """
    if not isinstance(blocks, (dict, list)):
        return ()
    found: list[str] = []
    seen: set[str] = set()
    for block in _walk(blocks):
        kind = block.get("type")
        if not isinstance(kind, str) or kind.strip().casefold() != ENTITY_BLOCK:
            continue
        entity_id = _text(block.get("entityId"))
        if entity_id is None or entity_id in seen:
            continue
        seen.add(entity_id)
        found.append(entity_id)
    return tuple(found)


def _object_title(obj: dict[str, Any]) -> str:
    """A display title for the parent, from the top level or the title property."""
    title = _text(obj.get("title"))
    if title:
        return title
    properties = obj.get("properties")
    if not isinstance(properties, dict):
        return ""
    for key, prop in properties.items():
        if not isinstance(prop, dict):
            continue
        if key != "title" and _text(prop.get("type")) != "title":
            continue
        payload = prop.get("title")
        if isinstance(payload, dict):
            value = _text(payload.get("value"))
        else:
            value = _text(payload)
        if value:
            return value
    return ""


def embedded_child_map(
    objects: Iterable[Any],
) -> dict[str, tuple[str, str]]:
    """``child_id -> (parent_id, parent_title)`` from one read's objects.

    An object contributes only when its body mapping is present; a parent is
    recorded once and a self-embed is dropped. The first parent that embeds a
    child wins, which keeps the answer deterministic when two parents embed the
    same task.
    """
    children: dict[str, tuple[str, str]] = {}
    for obj in objects or ():
        if not isinstance(obj, dict):
            continue
        parent_id = _text(obj.get("id"))
        if parent_id is None:
            continue
        body = obj.get("blocks")
        if not isinstance(body, dict):
            continue
        title = _object_title(obj)
        for child in embedded_entity_ids(body):
            if child == parent_id or child in children:
                continue
            children[child] = (parent_id, title)
    return children


def parent_pages_warning(count: int) -> str:
    """One bounded line for the parent page bodies this refresh could not read.

    Aggregated on purpose: a failed page-body read hides nothing, so the only
    thing the operator must learn is how many parents kept their children
    visible. No object id, title, or exception text is ever included, and the
    line is empty when every read succeeded.
    """
    if count < 1:
        return ""
    noun = "parent page" if count == 1 else "parent pages"
    return (
        f"{count} {noun} could not be read; their child tasks were left visible."
    )


def read_blocks(
    reader: Callable[[str], Any] | None, object_id: str
) -> tuple[dict[str, Any] | None, str | None]:
    """One fail-open page-body read: ``(blocks, warning)``.

    ``blocks`` is ``None`` when nothing usable was read, and ``warning`` is
    then :data:`BLOCKS_READ_WARNING` for a reader that exists but failed (so
    the caller can add one bounded line), or ``None`` when no reader is bound
    at all (an ordinary REST-only run, which is not a failure). Never raises:
    a failed page body may not cost the run, and it may not hide anything.
    """
    if not callable(reader):
        return None, None
    try:
        payload = reader(object_id)
    except Exception:  # noqa: BLE001 — a body read never fails a run
        return None, BLOCKS_READ_WARNING
    body = payload.get("blocks") if isinstance(payload, dict) else None
    if not isinstance(body, dict):
        return None, BLOCKS_READ_WARNING
    return body, None
