"""app_config.py — machine-local TDTB app config (`~/.config/tdtb/config.json`).

S0 of the Capacities-first migration: the app gains a single machine-local
home for its own configuration, resolved by `app_home()` and overridable with
the `TDTB_HOME` env var. This module owns exactly two things:

  - the **home resolver** (`app_home`, `config_path`, `state_dir`) that later
    slices reuse for every app-owned store, and
  - the **config.json v1 schema** — the canonical on-disk document, plus the
    bidirectional translation to and from the markdown-shaped section dict the
    rest of the app already consumes.

The document is::

    {
      "version": 1,
      "calendar": {"titles": [...], "capacity_classes": [...], "disabled": [...]},
      "ignore": {"todoist_ids": [...], "paths": [...], "names": [...]},
      "presets": [...],
      "anchored_blocks": [...],
      "colors": [...],
      "micro_adventure_pool": [...],
      "habits": {"source_directory": ..., ...},
      "todoist": {"read_query": {"assigned": ..., "quick": ...}}
    }

Record sections (presets, anchored_blocks, colors, calendar.titles,
capacity_classes, micro_adventure_pool) carry the vault config's own row
shape verbatim, so the translation is lossless and the operator can hand-edit
the same fields the vault table exposed. `habits` and `todoist.read_query`
re-home dot-notation `Defaults` keys under their own sections.

Dual-read contract: `load_sections()` returns `{}` — never raises — for a
missing, unreadable, corrupt, or unsupported-version file. A `{}` result means
"config.json is not in play", so `config_reader.read_config` falls back to the
vault exactly as before. A populated result is overlaid on top of the vault
parse, so a migrated config.json changes nothing it does not explicitly carry.

This module never writes config.json; that is the one-shot
`tools/migrate_vault_config.py`, run explicitly by a human.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

#: Env var that overrides the app home. Mirrors the vault-free precedent in
#: ``capacities_structure_titles.py`` (``~/.config/tdtb``), one root with one
#: override for every app-owned store.
TDTB_HOME_ENV = "TDTB_HOME"

#: Machine-local, never vault-local. A sibling of the Capacities title cache.
DEFAULT_APP_HOME = Path.home() / ".config" / "tdtb"

CONFIG_FILENAME = "config.json"
STATE_DIRNAME = "state"
CONFIG_VERSION = 1

#: Row fields that name a disabled calendar (mirrors
#: ``calendar_bridge.normalize_disabled_calendars``).
_DISABLED_FIELDS = ("Title", "BusyCal title", "Calendar title", "Identifier")


# ---------------------------------------------------------------------------
# Home resolver — the one reusable helper later slices use
# ---------------------------------------------------------------------------

def app_home() -> Path:
    """Return the app home: ``$TDTB_HOME`` if set and non-empty, else
    ``~/.config/tdtb``. Read at call time so tests and the operator can flip
    it without a reimport."""
    override = os.environ.get(TDTB_HOME_ENV)
    if override:
        return Path(override).expanduser()
    return DEFAULT_APP_HOME


def config_path() -> Path:
    """Path to the canonical ``config.json`` under the app home."""
    return app_home() / CONFIG_FILENAME


def state_dir() -> Path:
    """Path to ``<app_home>/state/`` — the future home of the app-owned stores
    (runstate, exclusions, capacities settings, capacities source, deferrals).

    S0 computes the path only; it does not create the directory or move any
    store."""
    return app_home() / STATE_DIRNAME


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def load_document(path: str | Path | None = None) -> dict[str, Any] | None:
    """Read and validate ``config.json``. Returns the parsed document, or
    ``None`` for a missing / unreadable / corrupt / unsupported-version file.
    Never raises."""
    target = Path(path) if path is not None else config_path()
    try:
        if not target.exists():
            return None
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("version") != CONFIG_VERSION:
        return None
    return data


def load_sections(path: str | Path | None = None) -> dict[str, Any]:
    """Return the markdown-shaped sections dict implied by ``config.json``.

    ``{}`` when the file is absent or unusable, which is the signal to
    ``config_reader.read_config`` to fall back to the vault."""
    document = load_document(path)
    if document is None:
        return {}
    return sections_from_document(document)


# ---------------------------------------------------------------------------
# config.json -> markdown-shaped sections
# ---------------------------------------------------------------------------

def sections_from_document(document: dict[str, Any]) -> dict[str, Any]:
    """Translate a config.json v1 document into the section dict the app's
    consumers already read (``TdtbConfig.sections``)."""
    if not isinstance(document, dict):
        return {}
    out: dict[str, Any] = {}

    calendar = document.get("calendar")
    if isinstance(calendar, dict):
        if "titles" in calendar:
            out["Calendar Titles"] = calendar["titles"]
        if "capacity_classes" in calendar:
            out["Calendar Capacity Classes"] = calendar["capacity_classes"]
        if "disabled" in calendar:
            out["Disabled Calendars"] = [
                {"Title": str(value)} for value in _as_list(calendar["disabled"])
            ]

    if "ignore" in document:
        out["Ignore List"] = _ignore_section(document["ignore"])
    if "presets" in document:
        out["Presets"] = document["presets"]
    if "anchored_blocks" in document:
        out["Anchored Lifestyle Blocks"] = document["anchored_blocks"]
    if "colors" in document:
        out["Color Palette"] = document["colors"]
    if "micro_adventure_pool" in document:
        out["Micro-Adventures"] = {"Pool": document["micro_adventure_pool"]}

    habits = document.get("habits")
    if isinstance(habits, dict) and habits:
        out["Defaults"] = _expand_dot_notation(
            {f"habits.{key}": value for key, value in habits.items()}
        )

    todoist = document.get("todoist")
    read_query = todoist.get("read_query") if isinstance(todoist, dict) else None
    if isinstance(read_query, dict):
        flat = {f"todoist.read_query.{key}": value for key, value in read_query.items()}
        out["Defaults"] = {**(out.get("Defaults") or {}), **_expand_dot_notation(flat)}

    return out


# ---------------------------------------------------------------------------
# markdown-shaped sections -> config.json (used by the one-shot migration)
# ---------------------------------------------------------------------------

def document_from_sections(sections: dict[str, Any]) -> dict[str, Any]:
    """Translate a parsed vault-config section dict into a config.json v1
    document. Lossless for every section config.json v1 carries."""
    document: dict[str, Any] = {"version": CONFIG_VERSION}

    calendar: dict[str, Any] = {}
    if "Calendar Titles" in sections:
        calendar["titles"] = sections["Calendar Titles"]
    if "Calendar Capacity Classes" in sections:
        calendar["capacity_classes"] = sections["Calendar Capacity Classes"]
    if "Disabled Calendars" in sections:
        calendar["disabled"] = _disabled_values(sections["Disabled Calendars"])
    if calendar:
        document["calendar"] = calendar

    if "Ignore List" in sections:
        document["ignore"] = _ignore_document(sections["Ignore List"])
    if "Presets" in sections:
        document["presets"] = sections["Presets"]
    if "Anchored Lifestyle Blocks" in sections:
        document["anchored_blocks"] = sections["Anchored Lifestyle Blocks"]
    if "Color Palette" in sections:
        document["colors"] = sections["Color Palette"]

    micro = sections.get("Micro-Adventures")
    if isinstance(micro, dict) and "Pool" in micro:
        document["micro_adventure_pool"] = micro["Pool"]

    defaults = sections.get("Defaults")
    if isinstance(defaults, dict):
        habits = {
            key.split(".", 1)[1]: value
            for key, value in defaults.items()
            if key.startswith("habits.") and "." in key
        }
        if habits:
            document["habits"] = habits
        read_query = {
            key.rsplit(".", 1)[1]: value
            for key, value in defaults.items()
            if key.startswith("todoist.read_query.") and "." in key
        }
        if read_query:
            document["todoist"] = {"read_query": read_query}

    return document


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value is None:
        return []
    return [value]


def _expand_dot_notation(flat: dict[str, Any]) -> dict[str, Any]:
    """Mirror of ``config_reader._expand_dot_notation`` (kept local to avoid a
    circular import): keep the dotted keys and add a nested-dict view."""
    nested: dict[str, Any] = dict(flat)
    for key, value in flat.items():
        if "." in key:
            parts = key.split(".")
            cursor = nested
            for part in parts[:-1]:
                cursor = cursor.setdefault(part, {})
                if not isinstance(cursor, dict):
                    break
            else:
                cursor[parts[-1]] = value
    return nested


def _disabled_values(section: Any) -> list[str]:
    """Extract every title/identifier a ``## Disabled Calendars`` section names,
    mirroring ``normalize_disabled_calendars``'s field coverage."""
    if isinstance(section, str):
        return [section]
    if isinstance(section, dict):
        return [str(key) for key, flag in section.items() if flag]
    if not isinstance(section, list):
        return []
    out: list[str] = []
    for entry in section:
        if isinstance(entry, dict):
            out.extend(str(entry[field]) for field in _DISABLED_FIELDS if entry.get(field))
        elif entry not in (None, ""):
            out.append(str(entry))
    return out


def _ignore_document(section: Any) -> dict[str, list[str]]:
    """Flatten an ``## Ignore List`` section into the canonical
    ``{todoist_ids, paths, names}`` lists (same matching rules as
    ``TdtbConfig.get_ignore_list``)."""
    out: dict[str, list[str]] = {"todoist_ids": [], "paths": [], "names": []}
    if not isinstance(section, dict):
        return out
    for sub_name, rows in section.items():
        if not isinstance(rows, list):
            continue
        key = str(sub_name).casefold()
        for row in rows:
            if not isinstance(row, dict):
                continue
            if "todoist" in key:
                value = str(row.get("ID") or "").strip()
                if value and value != "—":
                    out["todoist_ids"].append(value)
            elif "obsidian" in key or "path" in key:
                value = str(row.get("Path") or "").strip()
                if value and value != "—":
                    out["paths"].append(value)
            else:
                value = str(row.get("Name") or "").strip()
                if value and value != "—":
                    out["names"].append(value.casefold())
    return out


def _ignore_section(ignore: Any) -> dict[str, Any]:
    """Rebuild the ``## Ignore List`` subsection shape from the canonical
    lists, so ``get_ignore_list`` reads it back unchanged."""
    if not isinstance(ignore, dict):
        return {}
    return {
        "Todoist (by ID)": [{"ID": str(v)} for v in _as_list(ignore.get("todoist_ids"))],
        "Obsidian (by path)": [{"Path": str(v)} for v in _as_list(ignore.get("paths"))],
        "Names": [{"Name": str(v)} for v in _as_list(ignore.get("names"))],
    }
