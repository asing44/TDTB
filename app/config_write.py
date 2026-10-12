"""config_write.py — the one place the app writes the tracked repo config.

``app_config`` never writes; this module is the deliberate, narrow exception:
persist a changed ``Start`` for ONE ``anchored_blocks`` entry in
``config/tdtb.json`` (``app_config.repo_config_path()``).

The edit is a surgical text replacement, not a load/dump round trip: the file
is hand-edited and reviewed in git, so key order, indentation, line endings,
unicode escapes and the trailing newline must survive exactly. Only the bytes
of the targeted ``Start`` string literal change. The result is re-parsed and
compared against the expected document before it is written, and the write is
temp-file + ``os.replace`` in the same directory so a reader never sees a
partial file.

Refused, never worked around:
  * an unknown block name (calendar rows are not config rows) — nothing to edit;
  * an unparseable time;
  * a machine-local ``$TDTB_HOME/config.json`` that defines ``anchored_blocks``
    — it replaces the repo list wholesale (``app_config`` layering), so editing
    the repo file would change nothing the app can see.

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
_TWELVE_HOUR = re.compile(r"^(\d{1,2}):(\d{2})\s*([AaPp][Mm])$")
_TWENTY_FOUR_HOUR = re.compile(r"^(\d{2}):(\d{2})$")


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

def _local_override_defines_anchored() -> bool:
    local = app_config.read_document_file(app_config.config_path())
    return local is not None and _ANCHORED_KEY in local


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
