"""Stable-ID duration memory and deterministic duration-source resolution.

Frozen plan items 10-12 (``Plans Link/2026-08-09-tdtb-planning-ui-reliability.md``):

- Precedence (item 11): saved user memory -> deterministic tag mapping ->
  Todoist native or exact named preset -> contract-defined type field ->
  default. Same-precedence tag collisions fail visibly.
- The resolver returns ``(value_minutes, source_label)`` where source_label is
  one of ``remembered`` / ``tag:<name>`` / ``native`` / ``preset`` / ``type`` /
  ``default``.
- Memory is keyed by canonical source identity (Todoist task id via
  ``todoist:<id>``; normalized vault path), survives recurring occurrences,
  uses atomic writes (tmp + ``os.replace`` when a path is supplied), and never
  rewrites Todoist/vault source duration fields. Name alone is not an
  identity (item 12).

The default store is in-memory so unit tests never touch disk; callers opt
into persistence by passing ``path`` (under the existing TDTB cache
discipline). ``resolve_duration`` is pure — it never writes anything.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

# Deterministic tag mapping: a label matching ``dur<N>`` means N minutes.
_TAG_DURATION_RE = re.compile(r"^dur(\d+)$", re.IGNORECASE)

# Contract-defined per-type duration fields (the vault FileClass owns the
# contract; press notes carry ``duration_min`` in minutes). Mirrors
# ``main._TYPE_DURATION_FIELDS`` — duplicated here so the resolver stays a
# pure, importable seam.
_TYPE_DURATION_FIELDS: dict[str, str] = {"press": "duration_min"}

DEFAULT_MINUTES = 30

# In-memory default store (tests / callers without a persistence path).
_store: dict[str, int] = {}


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------

def normalize_identity(identity: str) -> str:
    """Validate and normalize a canonical source identity.

    Valid identities: ``todoist:<id>`` (Todoist task id), ``vault:<path>``
    or any path-shaped value containing ``/`` (normalized vault path). A bare
    display name is not an identity and raises ``ValueError``.
    """
    text = str(identity or "").strip()
    if text.startswith("todoist:"):
        tail = text[len("todoist:"):].strip()
        if tail:
            return f"todoist:{tail}"
    if text.startswith("vault:"):
        tail = text[len("vault:"):].strip()
        if tail:
            return f"vault:{tail}"
    if "/" in text:
        return text
    raise ValueError(
        f"{identity!r} is not a stable source identity "
        "(name alone is not an identity)"
    )


def item_identity(item: dict[str, Any]) -> str | None:
    """Canonical identity for a digest/assigned row, or None when absent."""
    todoist_id = item.get("todoist_id")
    if todoist_id not in (None, ""):
        return f"todoist:{todoist_id}"
    path = item.get("path")
    if path and not str(path).startswith("todoist://"):
        return str(path)
    return None


# ---------------------------------------------------------------------------
# Memory store (atomic when a path is supplied)
# ---------------------------------------------------------------------------

def _load(path: str | Path | None) -> dict[str, int]:
    if path is None:
        return _store
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _atomic_write_json(path: str | Path, data: dict[str, Any]) -> None:
    """Write ``data`` atomically: temp file in the same directory, then
    ``os.replace``. Never leaves a torn cache file."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=f".{p.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, sort_keys=True)
        os.replace(tmp, p)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_memory(identity: str, path: str | Path | None = None) -> int | None:
    """Return remembered minutes for a canonical identity, or None."""
    return _load(path).get(normalize_identity(identity))


def store_memory(
    identity: str, minutes: int, path: str | Path | None = None
) -> int:
    """Persist remembered minutes for a canonical identity.

    Raises ``ValueError`` for a name-only identity (item 12) or a negative
    duration. With ``path`` the write is atomic; without it the in-memory
    default store is updated.
    """
    key = normalize_identity(identity)
    value = int(minutes)
    if value < 0:
        raise ValueError(f"duration minutes must be >= 0, got {minutes}")
    store = dict(_load(path))
    store[key] = value
    if path is None:
        _store.clear()
        _store.update(store)
    else:
        _atomic_write_json(path, store)
    return value


# ---------------------------------------------------------------------------
# Deterministic source resolution
# ---------------------------------------------------------------------------

def _duration_tag(item: dict[str, Any]) -> tuple[str | None, int | None]:
    """The deterministic tag mapping for an item's labels.

    Returns ``(tag_name, minutes)``; more than one DISTINCT duration among
    matching tags raises ``ValueError`` (same-precedence collision, item 11).
    """
    matches: list[tuple[str, int]] = []
    for label in item.get("labels") or []:
        m = _TAG_DURATION_RE.match(str(label).strip())
        if m:
            matches.append((str(label).strip(), int(m.group(1))))
    if not matches:
        return None, None
    distinct = {minutes for _, minutes in matches}
    if len(distinct) > 1:
        raise ValueError(
            "same-precedence duration tag collision: "
            + ", ".join(tag for tag, _ in matches)
        )
    return matches[0][0], distinct.pop()


def _native_minutes(item: dict[str, Any]) -> int | None:
    """Todoist-native duration in minutes: raw ``{"unit", "amount"}`` shape
    or the already-parsed bare minutes the read seam emits. None when
    absent/unparseable; explicit zero is honored."""
    dur = item.get("duration")
    if isinstance(dur, dict):
        amount = dur.get("amount")
        unit = str(dur.get("unit") or "").casefold()
        if unit == "minute" and amount is not None:
            return int(amount)
        if unit == "day" and amount is not None:
            return int(amount) * 24 * 60
        return None
    if isinstance(dur, bool) or dur is None:
        return None
    if isinstance(dur, (int, float)):
        return int(dur)
    return None


def _preset_minutes(name: str, presets: list[dict[str, Any]] | None) -> int | float | None:
    """Minutes from the ``## Presets`` row whose Name matches (case/
    whitespace-insensitive); None on no match or unparseable Blocks.
    Fractional blocks stay fractional minutes (2.5 blocks -> 75.0)."""
    key = str(name or "").strip().casefold()
    if not key:
        return None
    for row in presets or []:
        if not isinstance(row, dict):
            continue
        row_name = str(row.get("Name") or row.get("name") or "").strip().casefold()
        if row_name != key:
            continue
        try:
            blocks = float(str(row.get("Blocks") or row.get("blocks")).strip())
        except (TypeError, ValueError):
            return None
        minutes = blocks * 30
        return int(minutes) if minutes.is_integer() else minutes
    return None


def _type_minutes(item: dict[str, Any], fm: dict[str, Any] | None) -> int | None:
    """Minutes from the contract-defined per-type frontmatter field."""
    if not isinstance(fm, dict):
        return None
    for t in item.get("types") or []:
        field = _TYPE_DURATION_FIELDS.get(str(t))
        if field is None:
            continue
        raw = fm.get(field)
        if raw is None:
            continue
        import time_engine

        return time_engine.duration_minutes(raw)
    return None


def resolve_duration(
    item: dict[str, Any],
    presets: list[dict[str, Any]] | None = None,
    fm: dict[str, Any] | None = None,
    memory: dict[str, int] | None = None,
) -> tuple[int | float, str]:
    """Resolve a row's duration minutes and the winning source label.

    Locked precedence (frozen item 11): remembered memory -> deterministic
    tag mapping -> Todoist native or exact named preset -> contract-defined
    type field -> default. Same-precedence tag collisions raise ValueError.
    Pure — never writes source duration fields or the memory store.
    """
    identity = item_identity(item)
    if identity and memory and identity in memory:
        return int(memory[identity]), "remembered"

    tag, tag_minutes = _duration_tag(item)
    if tag is not None and tag_minutes is not None:
        return tag_minutes, f"tag:{tag}"

    native = _native_minutes(item)
    if native is not None:
        return native, "native"

    preset = _preset_minutes(str(item.get("name") or ""), presets)
    if preset is not None:
        return preset, "preset"

    typed = _type_minutes(item, fm)
    if typed is not None:
        return typed, "type"

    return DEFAULT_MINUTES, "default"
