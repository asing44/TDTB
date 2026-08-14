#!/usr/bin/env python3
"""
tdtb-gather.py — Deterministic TDTB vault gather for the CLI environment.

Reads WALL⋅E-THNK off disk, evaluates the assignment-pipeline.base#assignables
predicate (pool) and the daily-assigned.base predicate (assigned items), builds
and writes the active-inventory cache, and emits run-data JSON.

Gate: TDD — tests/test_tdtb_gather.py must pass.

Usage:
  python3 tdtb-gather.py --vault-root PATH [options]

Options:
  --vault-root PATH   Vault root directory (required)
  --cache-out PATH    Write active-inventory cache to PATH (default: auto)
  --run-data PATH     Write run-data JSON to PATH (default: stdout)
  --today DATE        Override today's date as YYYY-MM-DD (for testing)
  --dry-run           Print what would be written, write nothing
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterator


try:
    import yaml
    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False


# ---------------------------------------------------------------------------
# Predicate constants (mirrored verbatim from the .base files)
# ---------------------------------------------------------------------------

EXCLUDED_FOLDER_PREFIXES = (
    "20 - ZK",
    "70 - Atlas",
    "30 - Daily",
    "90 - Archive",
)

EXCLUDED_TYPES_STANDALONE = frozenset(("shop", "print", "adventure", "movement", "press"))

OPEN_STATUSES = frozenset()  # "open" is defined by NOT being in the closed set
CLOSED_STATUSES = frozenset(("archived", "completed", "closed", "cancelled", "processed"))

# Scan roots (all operational note folders relative to vault root)
# Core pool folders — full inclusion gates apply (heavy / interval-due / standalone).
CORE_SCAN_DIRS = (
    "50 - Operations/Projects",
    "50 - Operations/Intervals",
    "50 - Operations/Pursuits",
    "50 - Operations/Adventures",
    "05 - Capture",
)
# Hatch-only folders (resurfacing-wiring 2026-07-02, T5 Option A): newly scanned,
# admitted ONLY via the escape hatch or 4-crit — the standalone unparented gate
# does NOT apply here, so unsignalled backlog items don't flood the pool.
# (Replaces the phantom "Prints"/"Shops"/"Captures" entries that never matched a
# real folder — Shop/Print/Tasks were silently unscanned before this change.)
HATCH_ONLY_SCAN_DIRS = (
    "50 - Operations/Tasks",
    "50 - Operations/Shop",
    "50 - Operations/Print",
    "50 - Operations/Gifts",
)
SCAN_DIRS = CORE_SCAN_DIRS + HATCH_ONLY_SCAN_DIRS

CACHE_REL_PATH = "00 - META/Cache/active-inventory.md"
CACHE_SCHEMA_VERSION = 2


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
        else:
            result[key] = raw.strip("'\"")
            i += 1
    return result


# ---------------------------------------------------------------------------
# Note structure helpers
# ---------------------------------------------------------------------------

def get_types(fm: dict[str, Any]) -> set[str]:
    """Return the set of type strings from a note's frontmatter."""
    raw = fm.get("type")
    if raw is None:
        return set()
    if isinstance(raw, list):
        return {str(v).lower().strip() for v in raw if v}
    return {str(raw).lower().strip()}


def get_status(fm: dict[str, Any]) -> str:
    """Return the note's status string (lowercased), or '' if absent."""
    v = fm.get("status")
    return str(v).lower().strip() if v else ""


def is_open(fm: dict[str, Any]) -> bool:
    """True when status is not in the closed-status set."""
    return get_status(fm) not in CLOSED_STATUSES


def get_relates_to(fm: dict[str, Any]) -> str | None:
    """Return relates_to string, or None if absent/empty."""
    v = fm.get("relates_to")
    if v is None:
        return None
    s = str(v).strip()
    return s if s else None


def get_tags(fm: dict[str, Any]) -> list[str]:
    """Return normalized frontmatter tags in stable order.

    Tags are planning metadata, not assignment criteria. Preserve them on
    the run-data rows so the sequencing call can make grouping decisions such
    as keeping all ``systems`` work together without rereading vault files.
    """
    raw = fm.get("tags")
    if raw is None:
        return []
    values = raw if isinstance(raw, (list, tuple, set)) else str(raw).split(",")
    return sorted(
        {str(value).strip() for value in values if str(value).strip()},
        key=str.casefold,
    )


def get_urgency(fm: dict[str, Any]) -> str:
    v = fm.get("urgency")
    return str(v).strip() if v else ""


def get_return(fm: dict[str, Any]) -> str:
    """Return the return field as a comma-joined string (handles list or str)."""
    v = fm.get("return")
    if v is None:
        return ""
    if isinstance(v, list):
        return ",".join(str(x) for x in v if x)
    return str(v).strip()


def get_deadline(fm: dict[str, Any]) -> date | None:
    """Parse the deadline field into a date, or None if absent/unparseable."""
    v = fm.get("deadline")
    if not v:
        return None
    try:
        return date.fromisoformat(str(v)[:10])
    except (ValueError, TypeError):
        return None


def is_assigned_flag(fm: dict[str, Any]) -> bool:
    v = fm.get("assigned")
    if isinstance(v, bool):
        return v
    return str(v).lower().strip() == "true" if v is not None else False


# ---------------------------------------------------------------------------
# Folder / file filters
# ---------------------------------------------------------------------------

def is_excluded_folder(folder: str) -> bool:
    """True when the folder (relative to vault root) matches any exclusion prefix."""
    for prefix in EXCLUDED_FOLDER_PREFIXES:
        if folder.startswith(prefix):
            return True
    return False


def is_template_name(name: str) -> bool:
    """True when the note name starts with underscore (template guard)."""
    return name.startswith("_")


# ---------------------------------------------------------------------------
# Priority score formula (verbatim from assignment-pipeline.base formulas)
# ---------------------------------------------------------------------------

def compute_priority_score(fm: dict[str, Any], today: date) -> int:
    """
    Deadline component + urgency component (×4) + return component.

    Mirrors:
      if(deadline.isEmpty(), 0,
        if(deadline < today(), 100,
          if((deadline-today()).days<=7, 30,
            if((deadline-today()).days<=30, 10, 0))))
      + (if(urgency.isEmpty(), 1,
          if(urgency.contains("4-crit"), 4,
            if(urgency.contains("3-high"), 3,
              if(urgency.contains("2-med"), 2, 1)))) * 4)
      + if(return.isEmpty(), 0,
          if(return.contains("4-pivotal"), 4,
            if(return.contains("3-solid"), 3,
              if(return.contains("2-nice"), 2, 1))))
    """
    deadline = get_deadline(fm)
    if deadline is None:
        deadline_score = 0
    elif deadline < today:
        deadline_score = 100
    elif (deadline - today).days <= 7:
        deadline_score = 30
    elif (deadline - today).days <= 30:
        deadline_score = 10
    else:
        deadline_score = 0

    urgency = get_urgency(fm)
    if not urgency:
        urg_score = 1
    elif "4-crit" in urgency:
        urg_score = 4
    elif "3-high" in urgency:
        urg_score = 3
    elif "2-med" in urgency:
        urg_score = 2
    else:
        urg_score = 1

    ret = get_return(fm)
    if not ret:
        ret_score = 0
    elif "4-pivotal" in ret:
        ret_score = 4
    elif "3-solid" in ret:
        ret_score = 3
    elif "2-nice" in ret:
        ret_score = 2
    else:
        ret_score = 1

    return deadline_score + (urg_score * 4) + ret_score


# ---------------------------------------------------------------------------
# assignment-pipeline.base#assignables predicate
# ---------------------------------------------------------------------------

def passes_base_filter(name: str, folder: str, fm: dict[str, Any]) -> bool:
    """Apply the base-level assignment-pipeline filters (all must hold)."""
    if is_template_name(name):
        return False
    if not is_open(fm):
        return False
    if is_assigned_flag(fm):
        return False
    if is_excluded_folder(folder):
        return False
    types = get_types(fm)
    deadline = get_deadline(fm)
    # capture/gift types require a deadline
    if "capture" in types and deadline is None:
        return False
    if "gift" in types and deadline is None:
        return False
    return True


HATCH_URGENCIES = ("3-high", "4-crit")


def passes_escape_hatch(fm: dict[str, Any]) -> bool:
    """Eligibility escape hatch (resurfacing-wiring 2026-07-02, locked decision 4).

    A non-interval open item with urgency >= 3-high OR a non-empty deadline is
    pool-eligible regardless of type exclusion or child-hidden status. Intervals
    are carved out — the cadence machinery (due-window + weekplan) owns them.
    Mirrors the first-class OR clause in assignment-pipeline.base#assignables.
    """
    if "interval" in get_types(fm):
        return False
    urgency = get_urgency(fm)
    if any(u in urgency for u in HATCH_URGENCIES):
        return True
    return get_deadline(fm) is not None


def _is_hatch_only_folder(folder: str) -> bool:
    return any(folder == d or folder.startswith(d + "/") for d in HATCH_ONLY_SCAN_DIRS)


def passes_inclusion_filter(name: str, folder: str, fm: dict[str, Any], today: date) -> bool:
    """Apply the view-level assignables inclusion filter (at least one path)."""
    types = get_types(fm)
    urgency = get_urgency(fm)
    deadline = get_deadline(fm)

    # Escape hatch — first-class OR path, all folders.
    if passes_escape_hatch(fm):
        return True

    # Hatch-only folders: 4-crit still admits; nothing else does (Option A —
    # the standalone unparented gate below must not flood the pool with the
    # unsignalled backlog these folders hold).
    if _is_hatch_only_folder(folder):
        return "4-crit" in urgency

    # ASAP: urgency contains 4-crit
    if "4-crit" in urgency:
        return True

    # Due interval: type interval AND deadline <= today + 1 day
    if "interval" in types and deadline is not None:
        delta = (deadline - today).days
        if delta <= 1:
            return True

    # Heavy / standalone: not interval
    if "interval" not in types:
        # Heavy: type is project or pursuit
        if types & {"project", "pursuit"}:
            return True
        # Standalone: no relates_to AND not excluded type
        related = get_relates_to(fm)
        if related is None and not (types & EXCLUDED_TYPES_STANDALONE):
            return True

    return False


def is_in_pool(name: str, folder: str, fm: dict[str, Any], today: date) -> bool:
    """True when the note belongs in the assignment pipeline pool."""
    return (
        passes_base_filter(name, folder, fm) and
        passes_inclusion_filter(name, folder, fm, today)
    )


# ---------------------------------------------------------------------------
# daily-assigned.base predicate
# ---------------------------------------------------------------------------

def is_assigned(folder: str, fm: dict[str, Any]) -> bool:
    """True when the note is in the assigned set per daily-assigned.base."""
    if not is_assigned_flag(fm):
        return False
    if not is_open(fm):
        return False
    if is_excluded_folder(folder):
        return False
    return True


# ---------------------------------------------------------------------------
# Vault walking
# ---------------------------------------------------------------------------

def walk_vault(vault_root: Path) -> Iterator[dict[str, Any]]:
    """Yield note dicts for every .md file under the scan directories."""
    for scan_rel in SCAN_DIRS:
        scan_abs = vault_root / scan_rel
        if not scan_abs.is_dir():
            continue
        for md_path in sorted(scan_abs.rglob("*.md")):
            rel = md_path.relative_to(vault_root)
            folder = str(rel.parent)
            name = md_path.stem
            try:
                text = md_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            fm = parse_frontmatter(text)
            if fm is None:
                continue
            yield {
                "path": str(rel),
                "abs_path": str(md_path),
                "folder": folder,
                "name": name,
                "fm": fm,
            }


# ---------------------------------------------------------------------------
# Cache builder
# ---------------------------------------------------------------------------

def _inventory_hash(pool: list[dict[str, Any]]) -> str:
    paths = "".join(n["path"] for n in pool)
    return hashlib.md5(paths.encode()).hexdigest()[:12]


def build_cache(pool_notes: list[dict[str, Any]], today: date) -> dict[str, Any]:
    """Build the active-inventory cache structure from pool notes."""
    parents = []
    for note in pool_notes:
        types = get_types(note["fm"])
        # Use the first type for the cache 'type' field (primary type)
        primary_type = sorted(types)[0] if types else "unknown"
        parents.append({
            "type": primary_type,
            "name": note["name"],
            "path": note["path"],
            "urgency": get_urgency(note["fm"]) or None,
            "deadline": str(get_deadline(note["fm"])) if get_deadline(note["fm"]) else None,
            "priority_score": compute_priority_score(note["fm"], today),
        })
    # Internal sequencing/wire-compatibility order; assignment membership is
    # structural and the user-facing Base no longer treats this as authority.
    parents.sort(key=lambda p: p["priority_score"], reverse=True)

    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    return {
        "schema_version": CACHE_SCHEMA_VERSION,
        "valid_date": str(today),
        "generated": now_utc,
        "inventory_hash": _inventory_hash(pool_notes),
        "parent_count": len(parents),
        "parents": parents,
    }


def _cache_to_markdown(cache: dict[str, Any]) -> str:
    """Serialise the cache dict as the YAML-frontmatter-only markdown format."""
    lines = [
        "---",
        f"schema_version: {cache['schema_version']}",
        f"valid_date: '{cache['valid_date']}'",
        f"generated: '{cache['generated']}'",
        f"inventory_hash: {cache['inventory_hash']}",
        f"parent_count: {cache['parent_count']}",
        "parents:",
    ]
    for p in cache["parents"]:
        lines.append(f"  - type: {p['type']}")
        lines.append(f"    name: {json.dumps(p['name'])}")
        lines.append(f"    path: {json.dumps(p['path'])}")
        if p.get("urgency"):
            lines.append(f"    urgency: {p['urgency']}")
        if p.get("deadline"):
            lines.append(f"    deadline: '{p['deadline']}'")
        lines.append(f"    priority_score: {p['priority_score']}")
    lines += ["---", "", "_Auto-generated by tdtb-gather.py. Do not edit manually._", ""]
    return "\n".join(lines)


def write_cache(cache: dict[str, Any], vault_root: Path, cache_rel: str = CACHE_REL_PATH) -> None:
    """Write the active-inventory cache to the vault."""
    out_path = vault_root / cache_rel
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(_cache_to_markdown(cache), encoding="utf-8")


# ---------------------------------------------------------------------------
# Run-data JSON
# ---------------------------------------------------------------------------

def build_run_data(
    pool_notes: list[dict[str, Any]],
    assigned_notes: list[dict[str, Any]],
    today: date,
) -> dict[str, Any]:
    """Build the run-data JSON emitted for the skill to consume."""

    def _summary(note: dict[str, Any]) -> dict[str, Any]:
        fm = note["fm"]
        return {
            "name": note["name"],
            "path": note["path"],
            "types": sorted(get_types(fm)),
            "relates_to": get_relates_to(fm),
            "tags": get_tags(fm),
            "urgency": get_urgency(fm) or None,
            "deadline": str(get_deadline(fm)) if get_deadline(fm) else None,
            "priority_score": compute_priority_score(fm, today),
            "assigned": is_assigned_flag(fm),
        }

    return {
        "generated": datetime.now(timezone.utc).isoformat(),
        "valid_date": str(today),
        "pool_count": len(pool_notes),
        "assigned_count": len(assigned_notes),
        "pool_items": [_summary(n) for n in pool_notes],
        "assigned_items": [_summary(n) for n in assigned_notes],
    }


# ---------------------------------------------------------------------------
# Precompute mode (nightly day-shape delta — see SKILL.md § Precompute cache layer)
# ---------------------------------------------------------------------------

PRECOMPUTE_CACHE_REL_PATH = "00 - META/Cache/tdtb-precompute-cache.md"
PRECOMPUTE_SCHEMA_VERSION = 1
DELTA_KINDS = frozenset(("new-due", "dropped", "shifted-anchor", "capacity", "newly-assigned"))
VALID_SOURCES = ("vault", "todoist", "calendar")


def effective_date(now: datetime) -> date:
    """The TDTB logical-day date: midnight–2am still counts as yesterday."""
    d = now.date()
    if now.hour < 2:
        from datetime import timedelta
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


def load_runstate(vault_root: Path, valid_date: date) -> tuple[date | None, dict[str, Any] | None]:
    """Load the most recent tdtb-runstate note strictly before valid_date.

    Returns (diff_base, runstate_dict) or (None, None) when no usable prior
    runstate exists — the precompute then degrades to a fresh-pool proposal.
    """
    cache_dir = vault_root / "00 - META/Cache"
    if not cache_dir.is_dir():
        return None, None
    best: tuple[date, Path] | None = None
    for p in cache_dir.glob("tdtb-runstate-*.md"):
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


def build_precompute_request(
    pool_notes: list[dict[str, Any]],
    assigned_notes: list[dict[str, Any]],
    runstate: dict[str, Any] | None,
    diff_base: date | None,
    valid_date: date,
) -> dict[str, Any]:
    """Build the JSON classification request for the nightly precompute LLM step.

    The script owns only the MECHANICAL delta candidates (vault-derived facts);
    the LLM step reasons over them, verifies Todoist/calendar via MCP, and
    produces the final proposed/delta payload for --precompute-commit.
    """
    selections = (runstate or {}).get("selections") or []
    selected_paths = {s.get("path") for s in selections if s.get("path")}
    notes_by_path = {n["path"]: n for n in pool_notes + assigned_notes}

    def _summary(n: dict[str, Any]) -> dict[str, Any]:
        fm = n["fm"]
        return {
            "name": n["name"], "path": n["path"], "types": sorted(get_types(fm)),
            "relates_to": get_relates_to(fm), "tags": get_tags(fm),
            "urgency": get_urgency(fm) or None,
            "deadline": str(get_deadline(fm)) if get_deadline(fm) else None,
            "priority_score": compute_priority_score(fm, valid_date),
            "assigned": is_assigned_flag(fm),
        }

    carried: list[dict[str, Any]] = []
    delta: list[dict[str, Any]] = []

    for s in selections:
        entry = dict(s)
        path = s.get("path")
        if s.get("source") == "todoist":
            # Completion state is invisible to the script — LLM must verify via MCP.
            entry["verify"] = "todoist"
            carried.append(entry)
            continue
        if path and path not in notes_by_path:
            # Note closed, archived, or moved out of the pool/assigned sets.
            delta.append({
                "kind": "dropped", "item": s.get("name") or path, "ref": path,
                "detail": "yesterday's selection is no longer open/eligible",
            })
            continue
        carried.append(entry)

    for n in pool_notes:
        if n["path"] in selected_paths:
            continue
        dl = get_deadline(n["fm"])
        if dl is not None and (dl - valid_date).days <= 1:
            delta.append({
                "kind": "new-due", "item": n["name"], "ref": n["path"],
                "detail": f"deadline {dl}", **{"summary": _summary(n)},
            })

    for n in assigned_notes:
        if n["path"] in selected_paths:
            continue
        delta.append({
            "kind": "newly-assigned", "item": n["name"], "ref": n["path"],
            "detail": "assigned: true and not in yesterday's selections",
            "summary": _summary(n),
        })

    proposed_base = {}
    if runstate:
        proposed_base = {k: runstate[k] for k in ("anchor", "eod", "buffering") if k in runstate}

    return {
        "mode": "precompute",
        "valid_date": str(valid_date),
        "diff_base": str(diff_base) if diff_base else None,
        "proposed_base": proposed_base,
        "carried_candidates": carried,
        "delta_candidates": delta,
        "pool_items": [_summary(n) for n in pool_notes],
        "assigned_items": [_summary(n) for n in assigned_notes],
        "counts": {"pool": len(pool_notes), "assigned": len(assigned_notes),
                   "carried": len(carried), "delta": len(delta)},
    }


def write_precompute_cache(
    payload: dict[str, Any],
    vault_root: Path,
    valid_date: date,
    diff_base: date | None,
) -> None:
    """Validate + serialize the LLM's precompute payload to the cache note.

    Schema home: tdtb-bridger-vault SKILL.md § Precompute cache layer. Invalid
    delta kinds and unknown sources are dropped (never guessed).
    """
    delta = [d for d in payload.get("delta") or []
             if isinstance(d, dict) and d.get("kind") in DELTA_KINDS]
    sources = [s for s in VALID_SOURCES if s in (payload.get("sources") or [])]
    body = {
        "proposed": payload.get("proposed") or {},
        "delta": delta,
        "pool": payload.get("pool") or [],
    }
    now_local = datetime.now().astimezone().isoformat(timespec="seconds")
    diff_line = f"'{diff_base}'" if diff_base else "null"
    content = "\n".join([
        "---",
        f"schema_version: {PRECOMPUTE_SCHEMA_VERSION}",
        f"valid_date: '{valid_date}'",
        f"generated: '{now_local}'",
        f"diff_base: {diff_line}",
        f"sources: [{', '.join(sources)}]",
        "---",
        "",
        "```json",
        json.dumps(body, indent=2, default=str),
        "```",
        "",
        "_Auto-generated by tdtb_gather.py --precompute-commit. Do not edit manually._",
        "",
    ])
    out_path = vault_root / PRECOMPUTE_CACHE_REL_PATH
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--vault-root", required=True, help="Vault root directory")
    parser.add_argument("--cache-out", default=None, help="Write cache to this path (default: <vault>/" + CACHE_REL_PATH + ")")
    parser.add_argument("--run-data", default=None, help="Write run-data JSON to this path (default: stdout)")
    parser.add_argument("--today", default=None, help="Override today as YYYY-MM-DD")
    parser.add_argument("--dry-run", action="store_true", help="Print stats, write nothing")
    parser.add_argument("--precompute", action="store_true",
                        help="Emit the nightly precompute request JSON (pool + carried shape + mechanical delta candidates)")
    parser.add_argument("--precompute-commit", action="store_true",
                        help="Read the LLM's precompute payload from stdin and write the precompute cache note")
    args = parser.parse_args(argv)

    vault_root = Path(args.vault_root).expanduser().resolve()
    if not vault_root.is_dir():
        print(f"ERROR: vault root not found: {vault_root}", file=sys.stderr)
        return 1

    today = date.fromisoformat(args.today) if args.today else effective_date(datetime.now())

    if args.precompute_commit:
        try:
            payload = json.load(sys.stdin)
        except json.JSONDecodeError as e:
            print(f"ERROR: invalid precompute payload JSON on stdin: {e}", file=sys.stderr)
            return 1
        diff_base_raw = payload.get("diff_base")
        diff_base = date.fromisoformat(diff_base_raw) if diff_base_raw else None
        write_precompute_cache(payload, vault_root, today, diff_base)
        n_delta = len([d for d in payload.get("delta") or [] if isinstance(d, dict) and d.get("kind") in DELTA_KINDS])
        print(f"TDTB precompute cache written for {today}: {n_delta} delta items, "
              f"sources [{', '.join(s for s in VALID_SOURCES if s in (payload.get('sources') or []))}].")
        return 0

    pool_notes: list[dict[str, Any]] = []
    assigned_notes: list[dict[str, Any]] = []

    for note in walk_vault(vault_root):
        name, folder, fm = note["name"], note["folder"], note["fm"]
        if is_assigned(folder, fm):
            assigned_notes.append(note)
        if is_in_pool(name, folder, fm, today):
            pool_notes.append(note)

    if args.precompute:
        diff_base, runstate = load_runstate(vault_root, today)
        req = build_precompute_request(pool_notes, assigned_notes, runstate, diff_base, today)
        out_json = json.dumps(req, indent=2, default=str)
        if args.run_data:
            Path(args.run_data).expanduser().write_text(out_json, encoding="utf-8")
        else:
            print(out_json)
        return 0

    cache = build_cache(pool_notes, today)
    run_data = build_run_data(pool_notes, assigned_notes, today)

    if args.dry_run:
        print(f"Pool items:     {len(pool_notes)}")
        print(f"Assigned items: {len(assigned_notes)}")
        print("Dry run — no files written.")
        return 0

    # Write cache
    cache_rel = args.cache_out or CACHE_REL_PATH
    if args.cache_out:
        out = Path(args.cache_out).expanduser()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(_cache_to_markdown(cache), encoding="utf-8")
    else:
        write_cache(cache, vault_root)

    # Write or print run-data
    run_data_json = json.dumps(run_data, indent=2, default=str)
    if args.run_data:
        Path(args.run_data).expanduser().write_text(run_data_json, encoding="utf-8")
    else:
        print(run_data_json)

    return 0


if __name__ == "__main__":
    sys.exit(main())
