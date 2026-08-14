"""Micro-adventure selection — TDTB app pilot.

Implements the deterministic (no-LLM) daily micro-adventure selection for the
Live anchored block (20:30), per the tdtb-bridger-vault SKILL.md § 0.7 contract.

Layering mirrors `calendar_bridge`: every parse/selection path is a pure
function that degrades to a safe fallback and NEVER raises (contract: "Failure
of anything here degrades to no selection (None), never raises"). The only I/O
seam is `read_history` / `write_history`, which wrap `frontmatter` and swallow
all errors down to `[]` / a fresh log.

Contract surfaces:
  * Pool — the vault config `## Micro-Adventures` section (parsed shape:
    ``{"_body": ..., "Rotation": {...}, "Pool": [ {...}, ... ]}``). Absent or
    malformed → the EXACT fallback seed pool (`FALLBACK_POOL`).
  * History — the vault log `00 - META/Cache/tdtb-micro-adventure-log.md`,
    YAML frontmatter, newest-first. Missing/unparseable → empty history.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable

import frontmatter

DEFAULT_EXCLUDE_WINDOW_DAYS = 14
HISTORY_REL_PATH = "00 - META/Cache/tdtb-micro-adventure-log.md"
_DEFAULT_LOG_DESCRIPTION = "TDTB micro-adventure selection history (newest-first)."


# ------------------------------------------------------------------ value types
@dataclass(frozen=True)
class PoolIdea:
    id: str
    idea: str
    category: str
    effort: str
    active: bool


@dataclass(frozen=True)
class HistoryEntry:
    date: date
    id: str
    idea: str
    todoist_task_id: str | None
    done: bool | None


# EXACT fallback seed pool (contract § Inputs 1). ma09/ma10 are retired — never
# present. All active; effort "low" except ma04 ("med").
FALLBACK_POOL: list[PoolIdea] = [
    PoolIdea("ma01", "Walk the greenway trail near the townhome", "nature", "low", True),
    PoolIdea("ma02", "Call a friend you haven't talked to in a while", "social", "low", True),
    PoolIdea("ma03", "Ride bike somewhere", "novelty", "low", True),
    PoolIdea("ma04", "Cook or eat something you've never tried", "novelty", "med", True),
    PoolIdea("ma05", "Strike up a conversation with a stranger", "courage", "low", True),
    PoolIdea("ma06", "Sit outside, no phone, for 15 minutes", "stillness", "low", True),
    PoolIdea("ma07", "Watch sunset", "nature", "low", True),
    PoolIdea("ma08", "Sketch or photograph something ordinary", "creative", "low", True),
    PoolIdea("ma11", "Write a handwritten note to someone and send it", "social", "low", True),
    PoolIdea("ma12", "Practice one new skill move for 15 minutes", "growth", "low", True),
    PoolIdea("ma13", "Take a short barefoot walk in the grass", "nature", "low", True),
    PoolIdea("ma14", "Do something unprompted and kind", "courage", "low", True),
]


# ---------------------------------------------------------------- parse helpers
_TRUTHY = {"yes", "true", "y", "1", "on"}
_FALSY = {"no", "false", "n", "0", "off"}


def _parse_active(value: Any) -> bool:
    """Truthy (True/"yes"/"true" …) → True; falsy (False/"no"/"false") → False;
    missing/unrecognized → True (default active — degrade toward inclusion)."""
    if value is None:
        return True
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        low = value.strip().lower()
        if low in _FALSY:
            return False
        return True
    return True


def _coerce_date(value: Any) -> date | None:
    """datetime.date / datetime.datetime / "YYYY-MM-DD" str → date; else None."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return datetime.strptime(value.strip(), "%Y-%m-%d").date()
        except (ValueError, TypeError):
            return None
    return None


def _coerce_done(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    if isinstance(value, str):
        low = value.strip().lower()
        if low in _TRUTHY:
            return True
        if low in _FALSY:
            return False
    return None


def parse_pool(section: Any) -> list[PoolIdea]:
    """Parse the `Micro-Adventures` section's Pool. Any degradation path
    (absent section, missing/empty/unparseable Pool, all rows skipped) →
    `FALLBACK_POOL`. Never raises."""
    if not isinstance(section, dict):
        return list(FALLBACK_POOL)
    rows = section.get("Pool")
    if not isinstance(rows, list) or not rows:
        return list(FALLBACK_POOL)

    out: list[PoolIdea] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        rid = row.get("ID")
        idea = row.get("Idea")
        if not isinstance(rid, str) or not rid.strip():
            continue
        if not isinstance(idea, str) or not idea.strip():
            continue
        category = row.get("Category")
        category = category.strip() if isinstance(category, str) else ""
        effort = row.get("Effort")
        effort = effort.strip() if isinstance(effort, str) and effort.strip() else "low"
        out.append(
            PoolIdea(rid.strip(), idea.strip(), category, effort, _parse_active(row.get("Active")))
        )
    if not out:
        return list(FALLBACK_POOL)
    return out


def exclude_window_days(section: Any) -> int:
    """Rotation."rotation.exclude_window_days" (flat) or nested
    Rotation.rotation.exclude_window_days. Absent/malformed/non-positive →
    `DEFAULT_EXCLUDE_WINDOW_DAYS`. Never raises."""
    if not isinstance(section, dict):
        return DEFAULT_EXCLUDE_WINDOW_DAYS
    rotation = section.get("Rotation")
    if not isinstance(rotation, dict):
        return DEFAULT_EXCLUDE_WINDOW_DAYS
    raw: Any = None
    if "rotation.exclude_window_days" in rotation:
        raw = rotation.get("rotation.exclude_window_days")
    else:
        nested = rotation.get("rotation")
        if isinstance(nested, dict):
            raw = nested.get("exclude_window_days")
    try:
        if isinstance(raw, bool):  # bool is an int subclass — reject it
            return DEFAULT_EXCLUDE_WINDOW_DAYS
        n = int(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return DEFAULT_EXCLUDE_WINDOW_DAYS
    return n if n > 0 else DEFAULT_EXCLUDE_WINDOW_DAYS


def parse_history(raw: Any) -> list[HistoryEntry]:
    """Parse the frontmatter `history` value (a list of dicts). Malformed
    entries (non-dict, missing/invalid date, missing id) are skipped. Result is
    sorted newest-first, stable for equal dates (input order preserved). Never
    raises."""
    if not isinstance(raw, list):
        return []
    parsed: list[HistoryEntry] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        d = _coerce_date(item.get("date"))
        if d is None:
            continue
        rid = item.get("id")
        if not isinstance(rid, str) or not rid.strip():
            continue
        idea = item.get("idea")
        idea = idea if isinstance(idea, str) else ""
        tid = item.get("todoist_task_id")
        tid = tid if isinstance(tid, str) and tid.strip() else None
        parsed.append(HistoryEntry(d, rid.strip(), idea, tid, _coerce_done(item.get("done"))))

    # newest-first, stable on ties (preserve input order for equal dates).
    indexed = list(enumerate(parsed))
    indexed.sort(key=lambda p: (-p[1].date.toordinal(), p[0]))
    return [e for _, e in indexed]


def read_history(path: Path) -> list[HistoryEntry]:
    """Thin I/O wrapper. Missing file / unparseable frontmatter → []."""
    try:
        if not path.exists():
            return []
        post = frontmatter.load(str(path))
        return parse_history(post.metadata.get("history"))
    except Exception:
        return []


# ----------------------------------------------------- prior-completion resolve
@dataclass(frozen=True)
class PriorResolution:
    history: tuple[HistoryEntry, ...]  # with any virtual done applied
    done_update: HistoryEntry | None  # entry now known done:true (write at COMMIT)
    pending_confirm: HistoryEntry | None


def daily_note_live_done(note_text: str | None) -> bool | None:
    """Inspect the '### Live' section of a daily note. A '- [x]' checkbox within
    it → True; only '- [ ]' (or no checkbox) → None (inconclusive); section or
    text missing → None. An unchecked box is NEVER treated as done:false."""
    if not isinstance(note_text, str) or not note_text:
        return None
    lines = note_text.splitlines()
    in_section = False
    for line in lines:
        stripped = line.strip()
        if not in_section:
            if stripped.lower() == "### live":
                in_section = True
            continue
        # Inside the Live section — a new heading ends it.
        if stripped.startswith("#"):
            break
        low = stripped.lower()
        if low.startswith("- [x]"):
            return True
    return None


def resolve_prior(
    history: Iterable[HistoryEntry],
    *,
    today: date,
    todoist_completed: Callable[[str], bool | None] | None = None,
    daily_note_live_checked: Callable[[date], bool | None] | None = None,
) -> PriorResolution:
    """Resolve whether the most recent (history[0]) adventure was completed.

    Applies ONLY when history[0].done is None and history[0].date < today.
      (a) todoist_completed(task_id) True → virtual done:true.
      (b) else daily_note_live_checked(date) True → virtual done:true.
      (c) else pending_confirm = history[0], no done_update.
    Callables raising, or returning False/None, are inconclusive. An (a) True
    short-circuits — (b) is never consulted."""
    hist = tuple(history)
    if not hist:
        return PriorResolution(hist, None, None)
    head = hist[0]
    if head.done is not None or head.date >= today:
        return PriorResolution(hist, None, None)

    done_entry = replace(head, done=True)

    # (a) Todoist completion signal.
    if head.todoist_task_id and todoist_completed is not None:
        try:
            result = todoist_completed(head.todoist_task_id)
        except Exception:
            result = None
        if result is True:
            return PriorResolution((done_entry,) + hist[1:], done_entry, None)

    # (b) Daily-note '### Live' checkbox.
    if daily_note_live_checked is not None:
        try:
            note_result = daily_note_live_checked(head.date)
        except Exception:
            note_result = None
        if note_result is True:
            return PriorResolution((done_entry,) + hist[1:], done_entry, None)

    # (c) Inconclusive — flag for explicit confirmation.
    return PriorResolution(hist, None, head)


# ----------------------------------------------------------------- selection
@dataclass(frozen=True)
class Selection:
    pick: PoolIdea | None
    live_pool: tuple[PoolIdea, ...]  # eligible list, LRU order, pick first, cap 8
    streak: int


def _last_used(history: Iterable[HistoryEntry]) -> dict[str, date]:
    last: dict[str, date] = {}
    for e in history:
        prev = last.get(e.id)
        if prev is None or e.date > prev:
            last[e.id] = e.date
    return last


def _streak(history: Iterable[HistoryEntry]) -> int:
    n = 0
    for e in history:
        if e.done is True:
            n += 1
        else:
            break
    return n


def _lru_order(cands: list[PoolIdea], last_used: dict[str, date]) -> list[PoolIdea]:
    """Never-used first (oldest), pool-order tie-break; then used by last-used
    date ascending, pool-order tie-break (stable sort preserves pool order)."""
    never = [p for p in cands if p.id not in last_used]
    used = [p for p in cands if p.id in last_used]
    used.sort(key=lambda p: last_used[p.id])
    return never + used


def select_today(
    pool: Iterable[PoolIdea],
    history: Iterable[HistoryEntry],
    *,
    today: date,
    window_days: int,
) -> Selection:
    """Deterministic LRU selection. Eligible = active ideas (id != "custom")
    not used within the window (entry.date > today - window_days excludes).
    All active in-window → relax to LRU across all active. No active → None."""
    hist = list(history)
    streak = _streak(hist)

    active = [p for p in pool if p.active and p.id != "custom"]
    if not active:
        return Selection(None, (), streak)

    threshold = today - timedelta(days=window_days)
    excluded_ids = {e.id for e in hist if e.date > threshold}
    eligible = [p for p in active if p.id not in excluded_ids]
    last_used = _last_used(hist)

    ordered = _lru_order(eligible if eligible else active, last_used)
    pick = ordered[0] if ordered else None
    return Selection(pick, tuple(ordered[:8]), streak)


# ------------------------------------------------------------------ commit path
def build_history_entry(
    pick_id: str, idea: str, *, today: date, todoist_task_id: str | None
) -> HistoryEntry:
    """Fresh commit entry — done is always None (undetermined at write time)."""
    return HistoryEntry(today, pick_id, idea, todoist_task_id, None)


def upsert_today_entry(
    history: Iterable[HistoryEntry], entry: HistoryEntry
) -> tuple[HistoryEntry, ...]:
    """Idempotent head commit: if history[0] shares entry.date, REPLACE it;
    else prepend. Never yields two head entries with the same date (re-commit
    safety)."""
    hist = tuple(history)
    if hist and hist[0].date == entry.date:
        return (entry,) + hist[1:]
    return (entry,) + hist


def apply_done_update(
    history: Iterable[HistoryEntry], done_update: HistoryEntry | None
) -> tuple[HistoryEntry, ...]:
    """Replace the (date, id)-matching entry with its done:true version.
    None → unchanged."""
    hist = tuple(history)
    if done_update is None:
        return hist
    return tuple(
        done_update if (e.date == done_update.date and e.id == done_update.id) else e
        for e in hist
    )


def write_history(path: Path, history: Iterable[HistoryEntry]) -> None:
    """Rewrite the log preserving the standard frontmatter shape. Round-trip:
    read_history(path) after write == the written (newest-first) input."""
    entries = list(history)

    description = _DEFAULT_LOG_DESCRIPTION
    try:
        if path.exists():
            existing = frontmatter.load(str(path))
            desc = existing.metadata.get("description")
            if isinstance(desc, str) and desc.strip():
                description = desc
    except Exception:
        pass

    hist_out: list[dict[str, Any]] = []
    for e in entries:
        row: dict[str, Any] = {"date": e.date.strftime("%Y-%m-%d"), "id": e.id, "idea": e.idea}
        if e.todoist_task_id is not None:
            row["todoist_task_id"] = e.todoist_task_id
        row["done"] = e.done
        hist_out.append(row)

    post = frontmatter.Post(
        "",
        description=description,
        schema_version=1,
        history=hist_out,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(frontmatter.dumps(post), encoding="utf-8")
