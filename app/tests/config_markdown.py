"""config_markdown.py — TEST-ONLY helper: install a markdown config fixture as the repo config.

The vault note `00 - META/Skill-Configs/tdtb-bridger.md` is no longer a
config source (vault removal V1). Many tests were written against a markdown
table fixture of that note; rather than hand-translate every table to JSON,
they keep the readable fixture and call ``install_config_markdown``, which
parses the markdown (the old parser, kept here and nowhere in the app),
translates it to a config document with ``document_from_sections`` and writes
it to the per-test repo-config path (``$TDTB_REPO_CONFIG``, set by conftest).
The app itself only ever reads JSON.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent))
import app_config  # noqa: E402
import day_semantics  # noqa: E402


_HEADING_RE = re.compile(r"^(#{2,3})\s+(.*\S)\s*$")
_TABLE_ROW_RE = re.compile(r"^\|(.+)\|\s*$")
_TABLE_SEP_RE = re.compile(r"^\|[\s:|-]+\|\s*$")


def _split_table_cells(line: str) -> list[str]:
    inner = line.strip()
    if inner.startswith("|"):
        inner = inner[1:]
    if inner.endswith("|"):
        inner = inner[:-1]
    return [c.strip() for c in inner.split("|")]


def _coerce_scalar(raw: str) -> Any:
    """Best-effort type coercion for a table cell: bool/int/float/None-ish, else str."""
    s = raw.strip()
    # Strip a single layer of inline-code backticks (`ID123`) — the live
    # config wraps IDs in backticks for Obsidian rendering; the ID string
    # itself, not the markdown, is the value callers want.
    if len(s) >= 2 and s.startswith("`") and s.endswith("`"):
        s = s[1:-1].strip()
    if s in ("", "—", "-", "–"):
        return None
    low = s.lower()
    # NOTE: "on"/"off" are deliberately NOT coerced to bool here — the
    # Schedulable Defaults `State` column uses on/off as a status label
    # (schema notes: "State (Schedulable defaults) is on / off"), and
    # round-tripping it as a string is what config-driven display/writeback
    # expects. `overlap_allowed` (yes/no) is the boolean-shaped field.
    if low in ("yes", "true"):
        return True
    if low in ("no", "false"):
        return False
    try:
        if re.fullmatch(r"-?\d+", s):
            return int(s)
        if re.fullmatch(r"-?\d*\.\d+", s):
            return float(s)
    except ValueError:
        pass
    return s


def _expand_dot_notation(flat: dict[str, Any]) -> dict[str, Any]:
    """Keep dot-notation keys as-is in the flat dict (lookups use the dotted
    key directly per `get()`), while also expanding a nested-dict mirror for
    callers that prefer nested access. Both views share the same values.
    """
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


def _parse_kv_table(rows: list[list[str]]) -> dict[str, Any]:
    """Parse a two-column `| Key | Value |`-style table into a flat dict.

    Any table where the first header cell is not literally "Key" still parses
    generically: first column becomes the dict key, second becomes the value.
    Extra columns beyond two are ignored for kv tables.
    """
    if not rows:
        return {}
    header = [h.lower() for h in rows[0]]
    data_rows = rows[1:]
    flat: dict[str, Any] = {}
    for row in data_rows:
        if len(row) < 2:
            continue
        key = row[0].strip()
        if not key:
            continue
        value = _coerce_scalar(row[1])
        flat[key] = value
    return _expand_dot_notation(flat)


def _parse_record_table(rows: list[list[str]]) -> list[dict[str, Any]]:
    """Parse a multi-column table (e.g. Presets, Anchored Lifestyle Blocks)
    into a list of row-dicts keyed by header name.
    """
    if len(rows) < 1:
        return []
    header = rows[0]
    records: list[dict[str, Any]] = []
    for row in rows[1:]:
        record: dict[str, Any] = {}
        for i, col_name in enumerate(header):
            if not col_name:
                continue
            cell = row[i] if i < len(row) else ""
            record[col_name] = _coerce_scalar(cell)
        records.append(record)
    return records


_KV_SECTIONS = {"Defaults", "Ranking Criteria"}
# Sections whose table is record-shaped (list of dicts).
_RECORD_SECTIONS = {
    "Schedulable Defaults",
    "Anchored Lifestyle Blocks",
    "Presets",
}


def _looks_like_kv_table(rows: list[list[str]]) -> bool:
    """Heuristic: only a table with an explicit 'Key' + 'Value' header pair
    is treated as key/value; everything else (incl. other 2-column tables
    like `Todoist Projects`'s `Project | ID`) parses as records. A bare
    column-count check is too ambiguous — several live-config tables are
    2-column but semantically record-shaped (each row is a named entity).
    """
    if not rows:
        return False
    header = [h.lower() for h in rows[0]]
    return "key" in header and "value" in header


def _parse_block(lines: list[str], section_name_hint: str | None) -> Any:
    """Parse the body of a section/subsection: one or more tables, plus any
    plain-text lines (kept as a '_body' fallback, unused by lookups today
    but preserved so nothing is silently dropped).
    """
    tables: list[list[list[str]]] = []
    current_table: list[list[str]] = []
    body_lines: list[str] = []

    for line in lines:
        if _TABLE_SEP_RE.match(line):
            continue
        m = _TABLE_ROW_RE.match(line)
        if m:
            current_table.append(_split_table_cells(line))
        else:
            if current_table:
                tables.append(current_table)
                current_table = []
            if line.strip():
                body_lines.append(line)
    if current_table:
        tables.append(current_table)

    if not tables:
        return {"_body": "\n".join(body_lines)} if body_lines else {}

    # Multiple tables in one section (e.g. Overlap Permissions has two
    # sub-tables under different headers) — merge kv tables, collect record
    # tables under a synthetic list. Most sections have exactly one table.
    if len(tables) == 1:
        rows = tables[0]
        if section_name_hint in _RECORD_SECTIONS:
            return _parse_record_table(rows)
        if section_name_hint in _KV_SECTIONS:
            return _parse_kv_table(rows)
        return _parse_kv_table(rows) if _looks_like_kv_table(rows) else _parse_record_table(rows)

    # Multiple tables and no explicit hint: return a list of parsed tables,
    # auto-detecting kv vs record per-table.
    parsed: list[Any] = []
    for rows in tables:
        if _looks_like_kv_table(rows):
            parsed.append(_parse_kv_table(rows))
        else:
            parsed.append(_parse_record_table(rows))
    return parsed if len(parsed) > 1 else parsed[0]


def parse_config_markdown(text: str) -> dict[str, Any]:
    """Parse the full config markdown into a `{section_name: parsed_body}` dict.

    `## ` headings are top-level sections. `### ` headings nest as a keyed
    sub-dict on the parent section (e.g. `Template Blocks` → `{"Trinoor
    Hours": [...], "Press (Gym)": [...]}`). Table rows within a section
    become dicts (key/value) or lists of dicts (records) depending on shape.
    """
    lines = text.splitlines()
    sections: dict[str, Any] = {}

    # Locate every heading with its line index.
    headings: list[tuple[int, int, str]] = []  # (level, line_idx, name)
    for i, line in enumerate(lines):
        m = _HEADING_RE.match(line)
        if m:
            level = len(m.group(1))
            headings.append((level, i, m.group(2).strip()))

    # Only consider level-2 and level-3 headings for section parsing.
    h2_indices = [idx for idx, h in enumerate(headings) if h[0] == 2]

    for hi, h_idx in enumerate(h2_indices):
        level, start_line, name = headings[h_idx]
        # end_line is the start of the NEXT level-2 heading (not just the next
        # heading overall, which could be one of this section's own level-3
        # sub-headings and would truncate the section to nothing).
        end_line = headings[h2_indices[hi + 1]][1] if hi + 1 < len(h2_indices) else len(lines)

        # Find any level-3 sub-headings within this section's line range.
        sub_headings = [
            h for h in headings
            if h[0] == 3 and start_line < h[1] < end_line
        ]

        if sub_headings:
            sub_dict: dict[str, Any] = {}
            # Body before the first sub-heading (rare, but don't drop it).
            pre_body = lines[start_line + 1 : sub_headings[0][1]]
            pre_parsed = _parse_block(pre_body, name)
            if pre_parsed:
                sub_dict.update(pre_parsed) if isinstance(pre_parsed, dict) else None

            for si, (slevel, sline, sname) in enumerate(sub_headings):
                s_end = sub_headings[si + 1][1] if si + 1 < len(sub_headings) else end_line
                sub_lines = lines[sline + 1 : s_end]
                sub_dict[sname] = _parse_block(sub_lines, sname)

            sections[name] = sub_dict
        else:
            body_lines = lines[start_line + 1 : end_line]
            sections[name] = _parse_block(body_lines, name)

    return sections



# ---------------------------------------------------------------------------
# markdown-shaped sections -> config document
# ---------------------------------------------------------------------------

#: ``Defaults`` keys that were retired (the vault habit read is gone).
_RETIRED_DEFAULT_KEYS = frozenset({"habits.source_directory"})
_DISABLED_FIELDS = ("Title", "BusyCal title", "Calendar title", "Identifier")


def _disabled_values(section: Any) -> list[str]:
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


def document_from_sections(
    sections: dict[str, Any], raw_text: str = ""
) -> dict[str, Any]:
    """Translate parsed markdown sections into a config document (the inverse
    of ``app_config.sections_from_document``)."""
    document: dict[str, Any] = {"version": app_config.CONFIG_VERSION}

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
    if "Schedulable Defaults" in sections:
        document["schedulable_defaults"] = sections["Schedulable Defaults"]
    if "Day Presets" in sections:
        document["day_presets"] = sections["Day Presets"]
    if "Template Blocks" in sections:
        document["template_blocks"] = sections["Template Blocks"]
    if "Ranking Criteria" in sections:
        document["ranking_criteria"] = sections["Ranking Criteria"]
    if "Color Palette" in sections:
        document["colors"] = sections["Color Palette"]

    overlap = day_semantics._extract_overlap_raw(raw_text)
    if overlap:
        document["overlap_permissions"] = overlap

    placement = sections.get("Placement Context")
    if isinstance(placement, dict) and placement.get("_body"):
        document["placement_context"] = str(placement["_body"])

    reference = sections.get("Schema Reference") or sections.get("Reference IDs")
    if isinstance(reference, dict):
        ref: dict[str, Any] = {}
        if "Todoist Projects" in reference:
            ref["todoist_projects"] = reference["Todoist Projects"]
        if "Todoist Filters" in reference:
            ref["todoist_filters"] = reference["Todoist Filters"]
        if ref:
            document["schema_reference"] = ref

    micro = sections.get("Micro-Adventures")
    if isinstance(micro, dict):
        if "Pool" in micro:
            document["micro_adventure_pool"] = micro["Pool"]
        rotation = micro.get("Rotation")
        if isinstance(rotation, dict):
            flat = {
                key.split(".", 1)[1]: value
                for key, value in rotation.items()
                if key.startswith("rotation.")
            }
            if flat:
                document["micro_adventure_rotation"] = flat

    defaults = sections.get("Defaults")
    if isinstance(defaults, dict):
        nested: dict[str, Any] = {}
        habits: dict[str, Any] = {}
        read_query: dict[str, Any] = {}
        for key, value in defaults.items():
            if isinstance(value, dict) or key in _RETIRED_DEFAULT_KEYS:
                continue  # nested mirror of the dotted keys, or retired
            if key.startswith("habits."):
                habits[key.split(".", 1)[1]] = value
            elif key.startswith("todoist.read_query."):
                read_query[key.rsplit(".", 1)[1]] = value
            else:
                cursor = nested
                *parents, leaf = key.split(".")
                for part in parents:
                    cursor = cursor.setdefault(part, {})
                cursor[leaf] = value
        if nested:
            document["defaults"] = nested
        if habits:
            document["habits"] = habits
        if read_query:
            document["todoist"] = {"read_query": read_query}

    return document


def install_config_markdown(text: str, *, replace: bool = True) -> Path:
    """Parse ``text`` and write it as the repo config at ``$TDTB_REPO_CONFIG``
    (the per-test path conftest sets). ``replace=False`` layers it over any
    document already installed."""
    target = app_config.repo_config_path()
    document = document_from_sections(parse_config_markdown(text), text)
    if not replace and target.exists():
        document = app_config._layer(json.loads(target.read_text(encoding="utf-8")), document)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    return target


# Last markdown installed per note path, so a test that edits its fixture in
# place (``read_config_note(p).replace(...)``) still can.
_NOTES: dict[str, str] = {}


def write_config_note(path: Path | str, text: str, encoding: str | None = None) -> Path:
    """Stand-in for ``note_path.write_text(markdown)`` in tests written when the
    vault note was the config source: installs ``text`` as the repo config.
    ``path`` only keys :func:`read_config_note`; nothing is written there."""
    _NOTES[str(path)] = text
    return install_config_markdown(text)


def read_config_note(path: Path | str, encoding: str | None = None) -> str:
    """The markdown last passed to :func:`write_config_note` for ``path``."""
    return _NOTES[str(path)]
