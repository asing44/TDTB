"""config_write.py — the one place the app writes the tracked repo config.

``app_config`` never writes; this module is the deliberate, narrow exception.
It owns exactly two edits of ``config/tdtb.json``
(``app_config.repo_config_path()``):

* ``save_anchored_start`` — persist a changed ``Start`` for ONE
  ``anchored_blocks`` entry;
* ``save_micro_adventure_pool`` — replace the ``micro_adventure_pool`` array
  from the pool editor, with ``read_micro_adventure_pool`` for its read side.

Both edits are surgical text replacements, not load/dump round trips: the file
is hand-edited and reviewed in git, so key order, indentation, line endings,
unicode escapes and the trailing newline must survive exactly. Only the bytes
of the targeted value change. The result is re-parsed and compared against the
expected document before it is written, and the write is temp-file +
``os.replace`` in the same directory so a reader never sees a partial file.

Refused, never worked around:
  * an unknown block name (calendar rows are not config rows) — nothing to edit;
  * an unparseable time;
  * a machine-local ``$TDTB_HOME/config.json`` that defines ``anchored_blocks``
    or ``micro_adventure_pool`` — it replaces the repo value wholesale
    (``app_config`` layering, lists included), so editing the repo file would
    change nothing the app can see;
  * a pool payload outside the documented bounds, or a repo config with no
    ``micro_adventure_pool`` key at all.

``app_config.load_document`` re-reads both files on every call (no cache), so a
successful write is visible to the running app immediately.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from json.decoder import scanstring
from pathlib import Path
from typing import Any

import app_config

#: Serializes read-modify-write within this process. The app is a single
#: process; cross-process safety comes from the atomic rename.
_WRITE_LOCK = threading.Lock()

_ANCHORED_KEY = "anchored_blocks"
_POOL_KEY = "micro_adventure_pool"
_TWELVE_HOUR = re.compile(r"^(\d{1,2}):(\d{2})\s*([AaPp][Mm])$")
_TWENTY_FOUR_HOUR = re.compile(r"^(\d{2}):(\d{2})$")

#: Bounds for the pool editor (fail closed: over-limit input is refused, never
#: truncated). A pool is a hand-maintained list of a dozen-ish ideas, so these
#: are generous ceilings that still keep one payload from bloating the config.
POOL_MAX_ITEMS = 200
POOL_MAX_ID_CHARS = 64
POOL_MAX_IDEA_CHARS = 300
POOL_MAX_CATEGORY_CHARS = 64
POOL_MAX_EFFORT_CHARS = 64

#: Pool ids are the stable identity that streak/history key on, so the editor
#: may not rewrite or normalize them: they must already be 1-64 characters of
#: letters, digits, ``-`` or ``_``. Arbitrary text is refused.
_POOL_ID = re.compile(r"^[A-Za-z0-9_-]{1,%d}$" % POOL_MAX_ID_CHARS)
_POOL_FIELDS = ("id", "idea", "category", "effort", "active")


class ConfigWriteError(Exception):
    """Base for refusals this module raises on purpose."""


class InvalidStartError(ConfigWriteError, ValueError):
    """The requested time is not ``HH:MM`` (24h) or ``H:MM AM/PM``."""


class UnknownBlockError(ConfigWriteError):
    """No ``anchored_blocks`` entry in the repo config has that ``Block``."""


class ShadowedByLocalConfigError(ConfigWriteError):
    """The machine-local override defines ``anchored_blocks``, so the repo
    list is not the one the app reads."""


class NotEditableError(ConfigWriteError):
    """The entry exists but has no string ``Start`` to replace."""


class InvalidPoolError(ConfigWriteError, ValueError):
    """The submitted pool is outside the documented shape or bounds."""


class PoolNotFoundError(ConfigWriteError):
    """The repo config has no ``micro_adventure_pool`` array to edit."""


class PoolShadowedByLocalConfigError(ConfigWriteError):
    """The machine-local override defines ``micro_adventure_pool``, so the
    repo list is not the one the app reads."""


def normalize_start(raw: Any) -> str:
    """Return ``raw`` formatted like the config's entries (``"5:45 PM"``).

    Accepts two-digit 24-hour ``HH:MM`` or 12-hour ``H:MM AM/PM`` (case
    insensitive). Anything else raises :class:`InvalidStartError`."""
    if not isinstance(raw, str):
        raise InvalidStartError("start must be a time string")
    text = raw.strip()
    twelve = _TWELVE_HOUR.match(text)
    if twelve:
        hour, minute = int(twelve.group(1)), int(twelve.group(2))
        if not 1 <= hour <= 12 or minute > 59:
            raise InvalidStartError(f"not a valid time: {raw!r}")
        return f"{hour}:{minute:02d} {twelve.group(3).upper()}"
    twenty_four = _TWENTY_FOUR_HOUR.match(text)
    if twenty_four:
        hour, minute = int(twenty_four.group(1)), int(twenty_four.group(2))
        if hour > 23 or minute > 59:
            raise InvalidStartError(f"not a valid time: {raw!r}")
        suffix = "AM" if hour < 12 else "PM"
        return f"{hour % 12 or 12}:{minute:02d} {suffix}"
    raise InvalidStartError(
        f"not a valid time: {raw!r} (use HH:MM or H:MM AM/PM)"
    )


# ---------------------------------------------------------------------------
# Span scanner — locates values in the original text without re-serializing
# ---------------------------------------------------------------------------

_SCALAR = re.compile(r"-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|true|false|null")


def _skip_ws(text: str, i: int) -> int:
    while i < len(text) and text[i] in " \t\r\n":
        i += 1
    return i


def _scan(text: str, i: int) -> tuple[int, Any]:
    """Scan the JSON value at ``i`` (already past whitespace).

    Returns ``(end, node)``. ``node`` is ``("object", members)`` with
    ``members`` a list of ``(key, start, end, node)``, ``("array", items)``
    with ``items`` a list of ``(start, end, node)``, ``("string", value)`` or
    ``("scalar", None)``."""
    ch = text[i]
    if ch == '"':
        value, end = scanstring(text, i + 1)
        return end, ("string", value)
    if ch == "{":
        members: list[tuple[str, int, int, Any]] = []
        i = _skip_ws(text, i + 1)
        if text[i] == "}":
            return i + 1, ("object", members)
        while True:
            i = _skip_ws(text, i)
            key, i = scanstring(text, i + 1)
            i = _skip_ws(text, i)
            i = _skip_ws(text, i + 1)  # past ':'
            start = i
            i, node = _scan(text, i)
            members.append((key, start, i, node))
            i = _skip_ws(text, i)
            if text[i] == ",":
                i += 1
                continue
            return i + 1, ("object", members)
    if ch == "[":
        items: list[tuple[int, int, Any]] = []
        i = _skip_ws(text, i + 1)
        if text[i] == "]":
            return i + 1, ("array", items)
        while True:
            i = _skip_ws(text, i)
            start = i
            i, node = _scan(text, i)
            items.append((start, i, node))
            i = _skip_ws(text, i)
            if text[i] == ",":
                i += 1
                continue
            return i + 1, ("array", items)
    match = _SCALAR.match(text, i)
    if not match:
        raise ValueError(f"unexpected JSON at offset {i}")
    return match.end(), ("scalar", None)


def _last_member(members: list, key: str) -> tuple[int, int, Any] | None:
    """The member a JSON parser would keep for ``key`` (last duplicate wins)."""
    found = None
    for name, start, end, node in members:
        if name == key:
            found = (start, end, node)
    return found


def _start_replacements(text: str, block: str) -> list[tuple[int, int, str]]:
    """``(start, end, old_value)`` spans of the ``Start`` string of every
    ``anchored_blocks`` entry named ``block``."""
    root_end, root = _scan(text, _skip_ws(text, 0))
    if root[0] != "object":
        return []
    anchored = _last_member(root[1], _ANCHORED_KEY)
    if anchored is None or anchored[2][0] != "array":
        return []
    spans: list[tuple[int, int, str]] = []
    for _item_start, _item_end, entry in anchored[2][1]:
        if entry[0] != "object":
            continue
        name = _last_member(entry[1], "Block")
        if name is None or name[2] != ("string", block):
            continue
        start = _last_member(entry[1], "Start")
        if start is None or start[2][0] != "string":
            raise NotEditableError(
                f"anchored block {block!r} has no Start to change in the repo config"
            )
        spans.append((start[0], start[1], start[2][1]))
    return spans


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def _local_override_defines(key: str) -> bool:
    local = app_config.read_document_file(app_config.config_path())
    return local is not None and key in local


def _local_override_defines_anchored() -> bool:
    return _local_override_defines(_ANCHORED_KEY)


def _local_override_defines_pool() -> bool:
    return _local_override_defines(_POOL_KEY)


def _atomic_write(target: Path, data: bytes) -> None:
    fd, temp_name = tempfile.mkstemp(
        dir=target.parent, prefix=f".{target.name}.", suffix=".tmp"
    )
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp, target.stat().st_mode & 0o7777)
        os.replace(temp, target)
    except BaseException:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass
        raise


def save_anchored_start(block: str, start: str) -> dict[str, str]:
    """Persist ``start`` as the ``Start`` of the repo config's anchored block
    named ``block``. Returns ``{"block", "start", "previous_start"}`` with
    ``start`` normalized (``"5:45 PM"`` style).

    Raises :class:`InvalidStartError`, :class:`UnknownBlockError`,
    :class:`ShadowedByLocalConfigError` or :class:`NotEditableError` without
    touching the file; ``OSError`` for a read/write failure (the original
    bytes are preserved)."""
    normalized = normalize_start(start)
    with _WRITE_LOCK:
        if _local_override_defines_anchored():
            raise ShadowedByLocalConfigError(
                "The machine-local config.json under the TDTB home defines "
                "anchored_blocks and overrides the repo config, so saving "
                "there would have no effect. Remove anchored_blocks from the "
                "local override (or edit the time there) and try again."
            )
        target = app_config.repo_config_path().resolve()
        try:
            raw = target.read_bytes()
        except FileNotFoundError as exc:
            raise UnknownBlockError(
                f"anchored block {block!r} is not in the repo config"
            ) from exc
        text = raw.decode("utf-8")
        try:
            spans = _start_replacements(text, block)
        except ValueError as exc:  # unparseable file: refuse rather than guess
            raise NotEditableError("the repo config could not be read as JSON") from exc
        if not spans:
            raise UnknownBlockError(f"anchored block {block!r} is not in the repo config")
        previous = spans[-1][2]
        if all(old == normalized for _s, _e, old in spans):
            return {"block": block, "start": normalized, "previous_start": previous}

        literal = json.dumps(normalized, ensure_ascii=False)
        updated = text
        for span_start, span_end, _old in sorted(spans, reverse=True):
            updated = updated[:span_start] + literal + updated[span_end:]

        # Belt and braces: the surgical edit must equal the intended document.
        expected = json.loads(text)
        for entry in expected[_ANCHORED_KEY]:
            if isinstance(entry, dict) and entry.get("Block") == block:
                entry["Start"] = normalized
        if json.loads(updated) != expected:
            raise NotEditableError("the edit would have changed more than the Start value")

        _atomic_write(target, updated.encode("utf-8"))
        return {"block": block, "start": normalized, "previous_start": previous}


# ---------------------------------------------------------------------------
# Micro-adventure pool: validation, rendering, read and write
# ---------------------------------------------------------------------------

def _pool_text(
    value: Any, index: int, field: str, max_chars: int, *, required: bool
) -> str:
    """One trimmed string field of a submitted pool item (content-free errors)."""
    if not isinstance(value, str):
        raise InvalidPoolError(f"pool item {index} field {field} must be a string")
    text = value.strip()
    if required and not text:
        raise InvalidPoolError(f"pool item {index} field {field} must not be blank")
    if len(text) > max_chars:
        raise InvalidPoolError(
            f"pool item {index} field {field} is longer than {max_chars} characters"
        )
    return text


def normalize_pool_items(items: Any) -> list[dict[str, Any]]:
    """Validate a submitted pool and return it in editor shape.

    Every item must be an object carrying exactly ``id``, ``idea``,
    ``category``, ``effort`` and ``active``: id/idea are non-empty strings
    (id also matching :data:`_POOL_ID`), category/effort strings that may be
    empty, active a real boolean. Ids are unique and preserved verbatim (they
    key streak/history). Over-limit payloads are refused, never truncated.
    Raises :class:`InvalidPoolError` with content-free messages."""
    if not isinstance(items, list):
        raise InvalidPoolError("the pool must be a list of items")
    if not items:
        raise InvalidPoolError("the pool must contain at least one item")
    if len(items) > POOL_MAX_ITEMS:
        raise InvalidPoolError(f"the pool accepts at most {POOL_MAX_ITEMS} items")
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            raise InvalidPoolError(f"pool item {index} must be an object")
        if set(item) - set(_POOL_FIELDS):
            raise InvalidPoolError(
                f"pool item {index} has fields this editor does not accept"
            )
        missing = [field for field in _POOL_FIELDS if field not in item]
        if missing:
            raise InvalidPoolError(f"pool item {index} is missing a required field")
        ident = item["id"]
        if not isinstance(ident, str) or not _POOL_ID.match(ident.strip()):
            raise InvalidPoolError(
                f"pool item {index} id must be 1-{POOL_MAX_ID_CHARS} characters of "
                "letters, digits, '-' or '_'"
            )
        ident = ident.strip()
        if ident in seen:
            raise InvalidPoolError(
                f"pool item {index} repeats an id already used by an earlier item"
            )
        seen.add(ident)
        active = item["active"]
        if not isinstance(active, bool):
            raise InvalidPoolError(
                f"pool item {index} field active must be true or false"
            )
        normalized.append(
            {
                "id": ident,
                "idea": _pool_text(item["idea"], index, "idea", POOL_MAX_IDEA_CHARS, required=True),
                "category": _pool_text(
                    item["category"], index, "category", POOL_MAX_CATEGORY_CHARS, required=False
                ),
                "effort": _pool_text(
                    item["effort"], index, "effort", POOL_MAX_EFFORT_CHARS, required=False
                ),
                "active": active,
            }
        )
    return normalized


def _config_pool_rows(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Editor items in the repo config's own key order and casing."""
    return [
        {
            "ID": item["id"],
            "Idea": item["idea"],
            "Category": item["category"],
            "Effort": item["effort"],
            "Active": item["active"],
        }
        for item in items
    ]


def _pool_array_span(text: str) -> tuple[int, int, str] | None:
    """``(start, end, base_indent)`` of the top-level ``micro_adventure_pool``
    array VALUE, or ``None`` when the key is absent or is not an array.

    ``base_indent`` is the leading whitespace of the key's own line, so the
    replacement can keep nesting at the same depth. Raises ``ValueError`` /
    ``IndexError`` when ``text`` is not parseable JSON."""
    _root_end, root = _scan(text, _skip_ws(text, 0))
    if root[0] != "object":
        raise ValueError("the repo config root is not an object")
    member = _last_member(root[1], _POOL_KEY)
    if member is None or member[2][0] != "array":
        return None
    start, end = member[0], member[1]
    line_start = text.rfind("\n", 0, start) + 1
    prefix = text[line_start:start]
    indent = prefix[: len(prefix) - len(prefix.lstrip(" \t"))]
    return start, end, indent


def _render_pool_array(
    items: list[dict[str, Any]], base_indent: str, newline: str = "\n"
) -> str:
    """The array value rendered in the file's own style: one object per line at
    ``base_indent + 2``, fields at ``base_indent + 4``, field order
    ID/Idea/Category/Effort/Active, JSON booleans, ``ensure_ascii=False`` so
    non-ASCII text stays readable in the reviewed diff."""
    item_indent = base_indent + "  "
    field_indent = base_indent + "    "
    lines = ["["]
    for index, item in enumerate(items):
        lines.append(item_indent + "{")
        fields = (
            ("ID", item["id"]),
            ("Idea", item["idea"]),
            ("Category", item["category"]),
            ("Effort", item["effort"]),
            ("Active", item["active"]),
        )
        for position, (key, value) in enumerate(fields):
            comma = "" if position == len(fields) - 1 else ","
            rendered = json.dumps(value, ensure_ascii=False)
            lines.append(f'{field_indent}"{key}": {rendered}{comma}')
        lines.append(item_indent + "}" + ("," if index < len(items) - 1 else ""))
    lines.append(base_indent + "]")
    return newline.join(lines)


def _export_pool_item(row: dict[str, Any]) -> dict[str, Any]:
    """One config pool row in editor shape. Read-side leniency mirrors
    ``micro_adventure.parse_pool``: a missing/unrecognized ``Active`` reads as
    active and a blank ``Effort`` reads as ``"low"``, so a round trip through
    the editor never silently flips an idea off."""
    ident = row.get("ID")
    idea = row.get("Idea")
    category = row.get("Category")
    effort = row.get("Effort")
    active = row.get("Active")
    return {
        "id": ident.strip() if isinstance(ident, str) else "",
        "idea": idea.strip() if isinstance(idea, str) else "",
        "category": category.strip() if isinstance(category, str) else "",
        "effort": effort.strip() if isinstance(effort, str) and effort.strip() else "low",
        "active": active if isinstance(active, bool) else True,
    }


def read_micro_adventure_pool() -> list[dict[str, Any]]:
    """The ``micro_adventure_pool`` array of the TRACKED repo config, in editor
    shape, with every entry (inactive and beyond the live cap included).

    This is the pool editor's source of truth, so it applies no Capacities
    merge, no rotation window and no live-pool cap. Raises
    :class:`PoolNotFoundError` when the key is absent, :class:`NotEditableError`
    when the file cannot be read as JSON, and ``OSError`` on a read failure."""
    target = app_config.repo_config_path().resolve()
    try:
        text = target.read_bytes().decode("utf-8")
    except FileNotFoundError as exc:
        raise PoolNotFoundError(
            "the repo config does not define micro_adventure_pool"
        ) from exc
    except (OSError, UnicodeDecodeError) as exc:
        raise NotEditableError("the repo config could not be read as JSON") from exc
    try:
        document = json.loads(text)
    except ValueError as exc:
        raise NotEditableError("the repo config could not be read as JSON") from exc
    pool = document.get(_POOL_KEY) if isinstance(document, dict) else None
    if not isinstance(pool, list):
        raise PoolNotFoundError("the repo config does not define micro_adventure_pool")
    return [_export_pool_item(row) for row in pool if isinstance(row, dict)]


def save_micro_adventure_pool(items: Any) -> list[dict[str, Any]]:
    """Replace the repo config's ``micro_adventure_pool`` array with ``items``.

    Returns the pool as saved, in editor shape. Raises
    :class:`InvalidPoolError` (bad shape/bounds),
    :class:`PoolNotFoundError` (no key to edit),
    :class:`PoolShadowedByLocalConfigError` (a machine-local override defines
    the key) or :class:`NotEditableError` (unparseable config, or the surgical
    edit would have changed more than the pool) without touching the file;
    ``OSError`` for a write failure (the original bytes are preserved)."""
    normalized = normalize_pool_items(items)
    rows = _config_pool_rows(normalized)
    with _WRITE_LOCK:
        if _local_override_defines_pool():
            raise PoolShadowedByLocalConfigError(
                "The machine-local config.json under the TDTB home defines "
                "micro_adventure_pool and overrides the repo config, so saving "
                "there would have no effect. Remove micro_adventure_pool from "
                "the local override (or edit the pool there) and try again."
            )
        target = app_config.repo_config_path().resolve()
        try:
            text = target.read_bytes().decode("utf-8")
        except FileNotFoundError as exc:
            raise PoolNotFoundError(
                "the repo config does not define micro_adventure_pool"
            ) from exc
        except (OSError, UnicodeDecodeError) as exc:
            raise NotEditableError("the repo config could not be read as JSON") from exc
        try:
            span = _pool_array_span(text)
        except (ValueError, IndexError) as exc:
            raise NotEditableError("the repo config could not be read as JSON") from exc
        if span is None:
            raise PoolNotFoundError(
                "the repo config does not define micro_adventure_pool"
            )
        start, end, base_indent = span
        newline = "\r\n" if "\r\n" in text else "\n"
        literal = _render_pool_array(normalized, base_indent, newline)
        updated = text[:start] + literal + text[end:]

        # Belt and braces: the surgical edit must equal the intended document.
        try:
            expected = json.loads(text)
            actual = json.loads(updated)
        except ValueError as exc:
            raise NotEditableError("the repo config could not be read as JSON") from exc
        if not isinstance(expected, dict):
            raise NotEditableError("the repo config could not be read as JSON")
        expected[_POOL_KEY] = rows
        if actual != expected:
            raise NotEditableError("the edit would have changed more than the pool")

        _atomic_write(target, updated.encode("utf-8"))
        return normalized
