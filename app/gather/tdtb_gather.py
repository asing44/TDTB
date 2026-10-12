"""
tdtb_gather.py -- run-date and run-state helpers shared by the TDTB app.

The note-scan gather (and its caches) is gone. What remains is the small
surface the rest of the app still needs:

* ``effective_date`` -- the logical-day date (midnight to 2am is still
  yesterday);
* ``empty_run_data`` -- the (always empty) note slice of the digest input, in
  the exact shape the ``/gather`` route has always returned;
* ``parse_frontmatter`` -- the frontmatter reader for the app-owned
  recent-selections cache;
* ``_extract_json_block`` / ``load_runstate`` -- the dated run-state note
  readers (state dir only).
"""
from __future__ import annotations

import json
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any


try:
    import yaml
    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False


# ---------------------------------------------------------------------------
# Frontmatter parsing
# ---------------------------------------------------------------------------

def parse_frontmatter(text: str) -> dict[str, Any] | None:
    """Extract and parse YAML frontmatter from a markdown file.

    Returns None if no valid frontmatter block is found.
    Uses PyYAML when available; falls back to a minimal line-by-line parser.
    """
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    if end == -1:
        return None
    yaml_block = text[3:end].strip()
    if _HAS_YAML:
        try:
            data = yaml.safe_load(yaml_block)
            return data if isinstance(data, dict) else None
        except yaml.YAMLError:
            return None
    # Minimal fallback: parse simple `key: value` and `key: [a, b]` patterns
    return _parse_frontmatter_fallback(yaml_block)


def _parse_frontmatter_fallback(yaml_block: str) -> dict[str, Any]:
    """Minimal YAML parser covering only the fields tdtb-gather needs."""
    result: dict[str, Any] = {}
    lines = yaml_block.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line or line.startswith("#"):
            i += 1
            continue
        m = re.match(r'^(\w[\w_-]*):\s*(.*)', line)
        if not m:
            i += 1
            continue
        key, raw = m.group(1), m.group(2).strip()
        if raw == "" or raw is None:
            # Could be a multi-line list starting on the next lines
            sub_items: list[str] = []
            j = i + 1
            while j < len(lines) and lines[j].startswith("  - "):
                sub_items.append(lines[j].strip()[2:].strip())
                j += 1
            result[key] = sub_items if sub_items else None
            i = j
        elif raw.startswith("["):
            # Inline list: [a, b, c]
            inner = raw.strip("[]")
            result[key] = [v.strip().strip("'\"") for v in inner.split(",") if v.strip()]
            i += 1
        elif raw.lower() in ("true", "false"):
            result[key] = raw.lower() == "true"
            i += 1
        else:
            result[key] = raw.strip("'\"")
            i += 1
    return result


# ---------------------------------------------------------------------------
# Run-data JSON
# ---------------------------------------------------------------------------

def empty_run_data(today: date) -> dict[str, Any]:
    """The run-data shape ``/gather`` returns, with no note rows.

    Todoist, Capacities, calendar and artifact rows are merged by the digest
    route itself; there is no longer a note-derived slice."""
    return {
        "generated": datetime.now(timezone.utc).isoformat(),
        "valid_date": str(today),
        "pool_count": 0,
        "assigned_count": 0,
        "pool_items": [],
        "assigned_items": [],
    }


# ---------------------------------------------------------------------------
# Logical day + run-state readers
# ---------------------------------------------------------------------------

def effective_date(now: datetime) -> date:
    """The TDTB logical-day date: midnight-2am still counts as yesterday."""
    d = now.date()
    if now.hour < 2:
        d = d - timedelta(days=1)
    return d


def _extract_json_block(text: str) -> dict[str, Any] | None:
    m = re.search(r"```json\s*\n(.*?)\n```", text, re.DOTALL)
    if not m:
        return None
    try:
        data = json.loads(m.group(1))
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        return None


def load_runstate(valid_date: date) -> tuple[date | None, dict[str, Any] | None]:
    """Load the most recent tdtb-runstate note strictly before valid_date.

    Returns (diff_base, runstate_dict) or (None, None) when no usable prior
    runstate exists. Searches the state-dir store only."""
    # ``runstate`` imports this module, so the import is deferred to call time
    # to avoid a module-load cycle.
    app_dir = Path(__file__).resolve().parent.parent
    if str(app_dir) not in sys.path:
        sys.path.insert(0, str(app_dir))
    import runstate
    directory = runstate.state_runstate_dir()
    candidates: list[Path] = (
        list(directory.glob("tdtb-runstate-*.md")) if directory.is_dir() else []
    )
    best: tuple[date, Path] | None = None
    for p in candidates:
        m = re.match(r"tdtb-runstate-(\d{4}-\d{2}-\d{2})\.md$", p.name)
        if not m:
            continue
        try:
            d = date.fromisoformat(m.group(1))
        except ValueError:
            continue
        if d >= valid_date:
            continue
        if best is None or d > best[0]:
            best = (d, p)
    if best is None:
        return None, None
    data = _extract_json_block(best[1].read_text(encoding="utf-8", errors="replace"))
    if data is None:
        return None, None
    return best[0], data
