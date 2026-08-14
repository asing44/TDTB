"""deferrals.py — T1 defer-with-memory (allocator rewrite, locked decision 5).

A rolling, vault-cached record of "I pushed this to another day", used to bias
the digest's ranked pool upward the next time the item shows up. The locked
*effect* is one line: **deferred yesterday ⇒ ranks higher today.** Everything
below is the schema and decay policy chosen to deliver it.

Store
-----
``00 - META/Cache/tdtb-deferrals.json`` — rolling (not dated; deferral memory
is explicitly cross-day), written atomically (tmp + ``os.replace``) under a
module lock, same single-writer discipline as ``runstate``/the runtime journal.
Shape::

    {"version": 1,
     "items": {"<key>": {"count": int,
                         "last_deferred": "YYYY-MM-DD",
                         "name": str, "path": str, "todoist_id": str}}}

A missing, unreadable, or wrong-shaped file degrades to ``{}`` — this cache is
an enhancement, never a dependency (same rule as the recent-selections cache).

Identity keys
-------------
``path:`` > ``todoist:`` > ``name:`` (casefolded), first available wins. Vault
path is the most stable identity the digest carries; Todoist ids are stable but
absent for vault rows; the name fallback keeps prompt-items and ad-hoc rows
addressable.

Decay / expiry
--------------
``bias_for(entry, today)`` maps an entry to an integer 0..``MAX_BIAS``:

- ``raw = min(count, MAX_BIAS)`` — repeated deferral raises the nudge, bounded.
- one step of decay per ``DECAY_INTERVAL_DAYS`` since ``last_deferred``.
- zero past ``TTL_DAYS``; entries older than that are pruned on load.

The cap is deliberately small: a deferral is a nudge, not an override. With
``MAX_BIAS = 2`` a deferred item can climb at most two urgency tiers, so a
4-urgency item is never displaced by a 1-urgency item someone keeps skipping.

Gate: TDD — tests/test_deferrals.py.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import date
from pathlib import Path
from typing import Any

import runstate

DEFERRALS_REL_PATH = f"{runstate.CACHE_DIR_REL}/tdtb-deferrals.json"
SCHEMA_VERSION = 1

MAX_BIAS = 2
DECAY_INTERVAL_DAYS = 7
TTL_DAYS = 14

_STORE_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------

def deferral_key(name: str = "", path: str = "", todoist_id: str = "") -> str:
    """Stable identity for one deferrable item. Raises on a blank identity."""
    if path:
        return f"path:{path}"
    if todoist_id:
        return f"todoist:{todoist_id}"
    if name:
        return f"name:{name.casefold()}"
    raise ValueError("deferral_key needs one of path / todoist_id / name")


def key_for_item(item: dict[str, Any]) -> str:
    """``deferral_key`` over a digest/pool item (or a resolved action target)."""
    return deferral_key(
        name=str(item.get("name") or ""),
        path=str(item.get("path") or item.get("vault_path") or ""),
        todoist_id=str(item.get("todoist_id") or ""),
    )


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------

def _read_raw(vault_root: Path | str) -> dict[str, Any]:
    path = Path(vault_root) / DEFERRALS_REL_PATH
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    items = data.get("items")
    if not isinstance(items, dict):
        return {}
    return {k: v for k, v in items.items() if isinstance(v, dict)}


def _write_raw(vault_root: Path | str, items: dict[str, Any]) -> Path:
    out_path = Path(vault_root) / DEFERRALS_REL_PATH
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps({"version": SCHEMA_VERSION, "items": items},
                   indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    os.replace(tmp, out_path)
    return out_path


def _parse_day(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def load_deferrals(
    vault_root: Path | str,
    today: date | None = None,
) -> dict[str, dict[str, Any]]:
    """Read the store. With ``today``, expired entries are pruned from the
    returned map (the file itself is pruned lazily, on the next write)."""
    items = _read_raw(vault_root)
    if today is None:
        return items
    kept: dict[str, dict[str, Any]] = {}
    for key, entry in items.items():
        last = _parse_day(entry.get("last_deferred"))
        if last is None:
            continue
        if (today - last).days > TTL_DAYS:
            continue
        kept[key] = entry
    return kept


def record_deferral(
    vault_root: Path | str,
    item: dict[str, Any],
    on_date: date,
) -> dict[str, Any]:
    """Increment (or create) the item's deferral entry. Returns the new entry."""
    key = key_for_item(item)
    with _STORE_LOCK:
        items = _read_raw(vault_root)
        prior = items.get(key) or {}
        try:
            count = int(prior.get("count") or 0)
        except (TypeError, ValueError):
            count = 0
        entry = {
            "count": count + 1,
            "last_deferred": str(on_date),
            "name": str(item.get("name") or prior.get("name") or ""),
            "path": str(item.get("path") or item.get("vault_path")
                        or prior.get("path") or ""),
            "todoist_id": str(item.get("todoist_id") or prior.get("todoist_id") or ""),
        }
        items[key] = entry
        _write_raw(vault_root, items)
        return entry


def set_entry(
    vault_root: Path | str,
    key: str,
    entry: dict[str, Any] | None,
) -> None:
    """Force one key to an exact value (or remove it) — the undo path for the
    ``defer`` runtime verb, restoring a journaled before-image byte-for-byte."""
    with _STORE_LOCK:
        items = _read_raw(vault_root)
        if entry is None:
            items.pop(key, None)
        else:
            items[key] = entry
        _write_raw(vault_root, items)


# ---------------------------------------------------------------------------
# Bias
# ---------------------------------------------------------------------------

def bias_for(entry: dict[str, Any], today: date) -> int:
    """Integer 0..MAX_BIAS nudge for one entry, decayed for age."""
    try:
        count = int(entry.get("count") or 0)
    except (TypeError, ValueError):
        return 0
    if count <= 0:
        return 0
    last = _parse_day(entry.get("last_deferred"))
    if last is None:
        return 0
    days = max(0, (today - last).days)  # clock skew / future date ⇒ treat as today
    if days > TTL_DAYS:
        return 0
    return max(0, min(count, MAX_BIAS) - days // DECAY_INTERVAL_DAYS)


def bias_map(vault_root: Path | str, today: date) -> dict[str, int]:
    """``{identity key: bias}`` for every non-zero entry — what ``rank_pool``
    consumes. Empty (never raising) when the cache is absent or unreadable."""
    out: dict[str, int] = {}
    for key, entry in load_deferrals(vault_root, today=today).items():
        bias = bias_for(entry, today)
        if bias:
            out[key] = bias
    return out
