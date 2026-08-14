"""runstate.py — run-state + recent-selections persistence for the TDTB app.

Writes the two Phase-0.8 / Phase-5 cache notes in the EXACT shapes the
tdtb-bridger-vault skill uses (SKILL.md § 0.8 Run-state persistence and
§ Recent-selections cache layer):

  - ``00 - META/Cache/tdtb-runstate-<YYYY-MM-DD>.md`` — frontmatter
    ``valid_date`` + ``written_at`` (ISO), then a fenced ```json body block
    (the shape ``tdtb_gather.load_runstate`` / ``_extract_json_block`` reads
    back — round-trip compatibility with gather is the format contract).
  - ``00 - META/Cache/tdtb-recent-selections.md`` — frontmatter ``runs:``
    list, max 5 entries, newest first; each entry
    ``{date, selections: [{id, path, blocks}]}``.

``vault_root`` is always a caller-supplied parameter — never hardcoded
(spec locked decision 2). Gate: T9 integration tests.
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable

_GATHER_DIR = str(Path(__file__).parent / "gather")
if _GATHER_DIR not in sys.path:
    sys.path.insert(0, _GATHER_DIR)

import tdtb_gather as gather  # noqa: E402  (path-shimmed import, see inventory.py)

CACHE_DIR_REL = "00 - META/Cache"
RECENT_SELECTIONS_REL_PATH = f"{CACHE_DIR_REL}/tdtb-recent-selections.md"
RECENT_SELECTIONS_MAX_RUNS = 5

# FEEDBACK-24: the only runstate key that records a confirmed Day Setup.
DAY_SETUP_CONFIRMED_KEY = "day_setup_confirmed"

_RUNSTATE_NAME_RE = re.compile(r"tdtb-runstate-(\d{4}-\d{2}-\d{2})\.md$")

# Full run-state key skeleton (SKILL.md § 0.8) — trigger-1 writes carry every
# key so later triggers only overwrite values, never invent shape.
RUNSTATE_DEFAULTS: dict[str, Any] = {
    "anchor": "",
    "eod": "",
    "buffering": "standard",
    "daily_note_path": "",
    "dedup_map": {},
    "re_included": [],
    "summit_child": None,
    "anchored": [],
    "schedulable": {},
    "calendar": [],
    "intention": "",
    "megan_nicety": "",
    "stoic_intention": "",
    "micro_adventure": None,
    "live_pool": [],
    "pending_confirm": None,
    "live_streak": 0,
    "ctx_weekend_items": [],
    "selections": [],
    "overlap_grants": [],
    "pinned_rows": [],
    "planning_config_fingerprint": "",
    "plan_manifest": [],
    "commit_ledger": {},
    "billed_calls": 0,
    # IMP-05: date-scoped Drop-from-plan exclusions. The dated note scopes
    # the list to one day — an item dropped today is eligible again tomorrow.
    # Entries are {identity, name, dropped_at}; identity is the canonical
    # source identity (todoist:<id> / vault path).
    "dropped": [],
    # T18b.2: tri-state dated overrides — None = no override (fall back to
    # config resolution); 0 work_allotment_minutes = explicit Mint disable.
    "day_preset": None,
    "work_allotment_minutes": None,
    # FEEDBACK-24: explicit Day Setup confirmation. Set to True ONLY by a
    # successful POST /day-setup for the current planning day. A skeleton
    # note (gather), Drop entries, ledger persists, or any other unrelated
    # runstate write never sets it — so an unconfirmed day reads False even
    # when the note is otherwise full of keys.
    "day_setup_confirmed": False,
}

# Per-day RMW locks (G26). The app is a single process (routes run in the
# threadpool), so a process-local lock closes the read-modify-write race;
# cross-process writers are excluded by the bake-in single-writer rule.
_DAY_LOCKS: dict[str, threading.Lock] = {}
_DAY_LOCKS_GUARD = threading.Lock()


def day_lock(valid_date: date | str) -> threading.Lock:
    """The shared per-valid_date lock every runstate RMW must hold."""
    key = str(valid_date)
    with _DAY_LOCKS_GUARD:
        return _DAY_LOCKS.setdefault(key, threading.Lock())


def runstate_rel_path(valid_date: date) -> str:
    """Vault-relative path of the dated run-state note."""
    return f"{CACHE_DIR_REL}/tdtb-runstate-{valid_date}.md"


def build_runstate(overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    """Full-shape run-state dict: skeleton defaults + caller overrides."""
    state = json.loads(json.dumps(RUNSTATE_DEFAULTS))  # deep copy, JSON-safe
    if overrides:
        state.update(overrides)
    return state


def write_runstate(
    vault_root: Path | str,
    valid_date: date,
    state: dict[str, Any],
    now: datetime | None = None,
) -> Path:
    """Write ``tdtb-runstate-<valid_date>.md`` in the skill's § 0.8 shape.

    Frontmatter carries ``valid_date`` + ``written_at``; the body is a fenced
    ```json block (what ``tdtb_gather._extract_json_block`` parses). Per the
    skill's trigger-1 rule, stale ``tdtb-runstate-*.md`` notes from earlier
    dates are best-effort deleted so they don't accumulate; same-day writes
    overwrite.
    """
    vault_root = Path(vault_root)
    now = now or datetime.now(timezone.utc)
    written_at = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    cache_dir = vault_root / CACHE_DIR_REL
    cache_dir.mkdir(parents=True, exist_ok=True)

    # Best-effort cleanup of earlier-dated run-state notes (trigger 1).
    for p in cache_dir.glob("tdtb-runstate-*.md"):
        m = _RUNSTATE_NAME_RE.match(p.name)
        if not m:
            continue
        try:
            d = date.fromisoformat(m.group(1))
        except ValueError:
            continue
        if d < valid_date:
            try:
                p.unlink()
            except OSError:
                pass

    body = json.dumps(state, indent=2, ensure_ascii=False, default=str)
    text = "\n".join([
        "---",
        f"valid_date: '{valid_date}'",
        f"written_at: '{written_at}'",
        "---",
        "",
        "```json",
        body,
        "```",
        "",
    ])
    out_path = vault_root / runstate_rel_path(valid_date)
    # Atomic replace (T12 audit): routes run in parallel threadpool workers,
    # and a bare write_text lets a concurrent reader see a truncated note.
    tmp_path = out_path.with_suffix(".md.tmp")
    tmp_path.write_text(text, encoding="utf-8")
    os.replace(tmp_path, out_path)
    return out_path


def read_runstate(vault_root: Path | str, valid_date: date) -> dict[str, Any] | None:
    """Read the dated run-state note's JSON body. Missing/unparseable → None."""
    path = Path(vault_root) / runstate_rel_path(valid_date)
    if not path.is_file():
        return None
    return gather._extract_json_block(path.read_text(encoding="utf-8", errors="replace"))


def is_day_setup_confirmed(vault_root: Path | str, valid_date: date) -> bool:
    """True ONLY when the dated note carries the explicit Day Setup
    confirmation written by a successful POST /day-setup for that date.

    The check is strict-boolean on ``day_setup_confirmed`` — a skeleton note
    (gather), Drop entries, commit/billed ledgers, sequence side-effect keys,
    or any other unrelated runstate write never satisfies it, and a
    pre-change note without the key fails closed.
    """
    state = read_runstate(vault_root, valid_date)
    if state is None:
        return False
    return state.get(DAY_SETUP_CONFIRMED_KEY) is True


def update_runstate(
    vault_root: Path | str,
    valid_date: date,
    updates: dict[str, Any] | Callable[[dict[str, Any]], None],
    now: datetime | None = None,
) -> dict[str, Any]:
    """Atomic read-modify-write of the dated run-state note (G26).

    Holds the per-day lock across a FRESH disk read, the mutation, and the
    write — callers must never RMW from a state dict read outside this
    function (a stale base silently overwrites concurrent writers).
    ``updates`` is either a dict merged over the state or a callable that
    mutates the state in place. Returns the state as written.
    """
    with day_lock(valid_date):
        state = build_runstate(read_runstate(vault_root, valid_date) or None)
        if callable(updates):
            updates(state)
        else:
            state.update(updates)
        write_runstate(vault_root, valid_date, state, now=now)
        return state


# ---------------------------------------------------------------------------
# Digest identity index (allocator-rewrite T2)
# ---------------------------------------------------------------------------
#
# Deliberately its OWN dated file rather than a run-state key. /plan-inputs
# writes this on every load, and an ``update_runstate`` call would materialise
# the dated run-state note as a side effect — after which every later
# ``day_setup`` read sees the skeleton's empty defaults (anchored, re_included,
# schedulable) as if the user had confirmed them. Same cache dir, same atomic
# discipline, no shared-shape coupling.

def digest_index_rel_path(valid_date: date) -> str:
    return f"{CACHE_DIR_REL}/tdtb-digest-index-{valid_date}.json"


def write_digest_index(
    vault_root: Path | str,
    valid_date: date,
    index: list[dict[str, str]],
) -> Path:
    """Atomically write today's ``[{name, todoist_id, path}]`` identity index."""
    out_path = Path(vault_root) / digest_index_rel_path(valid_date)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps({"valid_date": str(valid_date), "items": index},
                   indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    os.replace(tmp, out_path)
    return out_path


def read_digest_index(vault_root: Path | str, valid_date: date) -> list[dict[str, Any]]:
    """Today's index; missing or unreadable degrades to ``[]`` (the caller
    then refuses the target, exactly as an unknown name is refused)."""
    path = Path(vault_root) / digest_index_rel_path(valid_date)
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    items = data.get("items") if isinstance(data, dict) else None
    return [i for i in items if isinstance(i, dict)] if isinstance(items, list) else []


def read_dropped(vault_root: Path | str, valid_date: date) -> list[dict[str, Any]]:
    """Today's Drop-from-plan exclusions, or ``[]`` when none.

    The dated run-state note scopes the list: a different ``valid_date`` is a
    different day, so dropped items are eligible again. Malformed entries are
    ignored, never raised on.
    """
    state = read_runstate(vault_root, valid_date) or {}
    dropped = state.get("dropped")
    if not isinstance(dropped, list):
        return []
    return [d for d in dropped if isinstance(d, dict)]


def _selections_to_yaml_lines(runs: list[dict[str, Any]]) -> list[str]:
    """Serialise the ``runs:`` list as YAML frontmatter lines."""
    lines = ["runs:"]
    for run in runs:
        lines.append(f"  - date: '{run['date']}'")
        selections = run.get("selections") or []
        if not selections:
            lines.append("    selections: []")
            continue
        lines.append("    selections:")
        for sel in selections:
            lines.append(f"      - id: {json.dumps(str(sel.get('id', '')))}")
            lines.append(f"        path: {json.dumps(str(sel.get('path', '')))}")
            blocks = sel.get("blocks", 0)
            if not isinstance(blocks, (int, float)) or isinstance(blocks, bool):
                blocks = 0
            elif isinstance(blocks, float) and blocks.is_integer():
                blocks = int(blocks)
            lines.append(f"        blocks: {blocks}")
    return lines


def read_recent_selections(vault_root: Path | str) -> list[dict[str, Any]]:
    """Read the ``runs:`` list from the recent-selections cache.

    Missing or unparseable file → empty list (the skill treats this cache as
    an enhancement, never a dependency — skip silently).
    """
    path = Path(vault_root) / RECENT_SELECTIONS_REL_PATH
    if not path.is_file():
        return []
    fm = gather.parse_frontmatter(path.read_text(encoding="utf-8", errors="replace"))
    if not fm:
        return []
    runs = fm.get("runs")
    return runs if isinstance(runs, list) else []


def append_recent_selection(
    vault_root: Path | str,
    run_date: date,
    selections: list[dict[str, Any]],
) -> Path:
    """Prepend a run entry to the recent-selections cache (max 5, newest first).

    Each selection row is ``{id, path, blocks}`` per the skill's cache-layer
    spec. A same-date entry is replaced rather than duplicated (same-day
    re-commit overwrites, matching the run-state overwrite semantics).
    """
    vault_root = Path(vault_root)
    runs = [r for r in read_recent_selections(vault_root)
            if str(r.get("date")) != str(run_date)]
    entry = {
        "date": str(run_date),
        "selections": [
            {"id": s.get("id", ""), "path": s.get("path", ""), "blocks": s.get("blocks", 0)}
            for s in selections
        ],
    }
    runs = [entry] + runs
    runs = runs[:RECENT_SELECTIONS_MAX_RUNS]

    lines = ["---"] + _selections_to_yaml_lines(runs) + [
        "---", "", "_Auto-generated by runstate.py. Do not edit manually._", "",
    ]
    out_path = vault_root / RECENT_SELECTIONS_REL_PATH
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = out_path.with_suffix(".md.tmp")
    tmp_path.write_text("\n".join(lines), encoding="utf-8")
    os.replace(tmp_path, out_path)
    return out_path
