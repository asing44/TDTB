"""Micro-adventure selection — TDTB app pilot.

Implements the deterministic (no-LLM) daily micro-adventure selection for the
Live anchored block (20:30), per the tdtb-bridger-vault SKILL.md § 0.7 contract.

Layering mirrors `calendar_bridge`: every parse/selection path is a pure
function that degrades to a safe fallback and NEVER raises (contract: "Failure
of anything here degrades to no selection (None), never raises"). The only I/O
seam is `read_history` / `write_history`, which wrap `frontmatter` and swallow
all errors down to `[]` / a fresh log.

Contract surfaces:
  * Pool — the config's `Micro-Adventures` section (shape:
    ``{"Rotation": {...}, "Pool": [ {...}, ... ]}``), built by
    ``app_config.sections_from_document`` from ``micro_adventure_pool`` and
    ``micro_adventure_rotation`` in ``config/tdtb.json`` (plus the local
    override). Absent or malformed → the EXACT fallback seed pool
    (`FALLBACK_POOL`), which stays in code.
  * History — the log ``<app home>/state/micro-adventure-log.md`` (app-owned),
    YAML frontmatter, newest-first. Missing/unparseable → empty history.
  * Capacities — a DISABLED-by-default, PURE extension point
    (``normalize_capacities_ideas`` / ``capacities_pool`` / ``merge_pool``)
    over the folded ``Capacities`` settings plus a published artifact-style
    ideas block. No I/O, no clock, and never reached from a request path yet.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

import frontmatter

import app_config

DEFAULT_EXCLUDE_WINDOW_DAYS = 14
STATE_FILENAME = "micro-adventure-log.md"
_DEFAULT_LOG_DESCRIPTION = "TDTB micro-adventure selection history (newest-first)."


def history_path() -> Path:
    """The active log path: ``<app home>/state/micro-adventure-log.md``."""
    return app_config.state_dir() / STATE_FILENAME


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


# ------------------------------------------------- Capacities ideas pool core
#: Statuses :func:`capacities_pool` reports. Pure: no I/O, no clock, no MCP.
CAPACITIES_STATUS_DISABLED = "disabled"
CAPACITIES_STATUS_NOT_CONFIGURED = "not-configured"
CAPACITIES_STATUS_OK = "ok"
CAPACITIES_STATUS_STALE = "stale"
CAPACITIES_STATUS_UNAVAILABLE = "unavailable"

#: Stable id prefix for Capacities-derived rows (history/streak key by id).
CAPACITIES_ID_PREFIX = "cap-"
#: Bounds applied by :func:`normalize_capacities_ideas`.
CAPACITIES_MAX_IDEAS = 200
CAPACITIES_MAX_IDEA_CHARS = 200
#: Freshness default when the section omits ``refresh_cadence_hours``.
DEFAULT_REFRESH_CADENCE_HOURS = 168
#: The live pool size ``select_today`` exposes (``Selection.live_pool[:8]``).
DEFAULT_LIVE_POOL_CAP = 8


@dataclass(frozen=True)
class NormalizedIdeas:
    """Bounds-checked pool rows from one artifact-style ideas block.

    ``warnings`` carries one human-readable line per dropped item (or per
    truncation), so the caller can surface why the pool shrank."""

    ideas: tuple[PoolIdea, ...] = ()
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class CapacitiesPoolResult:
    """Typed result of :func:`capacities_pool`.

    ``ideas`` is only non-empty for ``ok`` and ``stale`` blocks; a stale block
    keeps its rows for diagnostics but callers fall back to the config pool
    (see :func:`merge_pool`)."""

    ideas: list[PoolIdea]
    status: str
    message: str
    warnings: tuple[str, ...] = ()


def _normalized_text_key(text: str) -> str:
    """Case-folded, whitespace-collapsed form used for text de-duplication."""
    return " ".join(text.split()).casefold()


def _coerce_timestamp(value: Any) -> datetime | None:
    """datetime / date / ISO-8601 str → aware UTC datetime; else None.
    Naive values are read as UTC. Never raises."""
    try:
        if isinstance(value, datetime):
            dt = value
        elif isinstance(value, date):
            dt = datetime(value.year, value.month, value.day)
        elif isinstance(value, str):
            raw = value.strip()
            if not raw:
                return None
            if raw.endswith(("Z", "z")):
                raw = raw[:-1] + "+00:00"
            try:
                dt = datetime.fromisoformat(raw)
            except ValueError:
                dt = datetime.strptime(raw, "%Y-%m-%d")
        else:
            return None
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _positive_int(value: Any, default: int) -> int:
    """A real positive int (bool rejected) or ``default``."""
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return default
    return value


def normalize_capacities_ideas(block: Any) -> NormalizedIdeas:
    """Normalize an artifact-style ideas block into pool rows.

    Input shape (any other shape degrades to no ideas, never raises)::

        {"status": "ok", "read_at": "...", "items": [{"id", "idea", ...}]}

    Each accepted item becomes ``PoolIdea("cap-<object id>", idea, category,
    effort, active)`` with the idea text trimmed. Items are dropped (each with
    a warning) when they are not objects, lack a usable ``id``, have a blank
    idea, repeat an id, or repeat a normalized idea text. Text is truncated to
    ``CAPACITIES_MAX_IDEA_CHARS`` and at most ``CAPACITIES_MAX_IDEAS`` rows are
    kept (one warning per dropped row)."""
    items = block.get("items") if isinstance(block, dict) else block
    if not isinstance(items, list):
        return NormalizedIdeas()

    ideas: list[PoolIdea] = []
    warnings: list[str] = []
    seen_ids: set[str] = set()
    seen_text: set[str] = set()

    for index, item in enumerate(items):
        if not isinstance(item, dict):
            warnings.append(
                f"micro_adventure_ideas item {index} is not an object; dropped."
            )
            continue

        raw_id = item.get("id")
        if isinstance(raw_id, bool) or not isinstance(raw_id, (str, int)):
            warnings.append(
                f"micro_adventure_ideas item {index} has no usable id; dropped."
            )
            continue
        object_id = str(raw_id).strip()
        if object_id.startswith(CAPACITIES_ID_PREFIX):
            object_id = object_id[len(CAPACITIES_ID_PREFIX):]
        if not object_id:
            warnings.append(
                f"micro_adventure_ideas item {index} has a blank id; dropped."
            )
            continue
        pool_id = f"{CAPACITIES_ID_PREFIX}{object_id}"
        if pool_id in seen_ids:
            warnings.append(
                f"micro_adventure_ideas item {pool_id} repeats an id; dropped."
            )
            continue

        raw_text = item.get("idea")
        if not isinstance(raw_text, str):
            warnings.append(
                f"micro_adventure_ideas item {pool_id} has no idea text; dropped."
            )
            continue
        text = raw_text.strip()
        if not text:
            warnings.append(
                f"micro_adventure_ideas item {pool_id} has a blank idea; dropped."
            )
            continue
        if len(text) > CAPACITIES_MAX_IDEA_CHARS:
            text = text[:CAPACITIES_MAX_IDEA_CHARS].rstrip()
            warnings.append(
                f"micro_adventure_ideas item {pool_id} idea truncated to "
                f"{CAPACITIES_MAX_IDEA_CHARS} characters."
            )

        text_key = _normalized_text_key(text)
        if text_key in seen_text:
            warnings.append(
                f"micro_adventure_ideas item {pool_id} repeats an existing idea; "
                "dropped."
            )
            continue

        category = item.get("category")
        category = category.strip() if isinstance(category, str) else ""
        effort = item.get("effort")
        effort = effort.strip() if isinstance(effort, str) else ""

        seen_ids.add(pool_id)
        seen_text.add(text_key)
        ideas.append(
            PoolIdea(pool_id, text, category, effort, _parse_active(item.get("active")))
        )

    if len(ideas) > CAPACITIES_MAX_IDEAS:
        overflow = ideas[CAPACITIES_MAX_IDEAS:]
        ideas = ideas[:CAPACITIES_MAX_IDEAS]
        for row in overflow:
            warnings.append(
                f"micro_adventure_ideas item {row.id} exceeds the "
                f"{CAPACITIES_MAX_IDEAS}-idea cap; dropped."
            )

    return NormalizedIdeas(tuple(ideas), tuple(warnings))


def _capacities_settings(section: Any) -> dict[str, Any]:
    """The folded ``Micro-Adventures`` -> ``Capacities`` mapping (or ``{}``)."""
    caps = section.get("Capacities") if isinstance(section, dict) else None
    return caps if isinstance(caps, dict) else {}


def _string_setting(caps: dict[str, Any], key: str) -> str:
    """Trimmed string setting; any other type reads as ``""``."""
    value = caps.get(key)
    return value.strip() if isinstance(value, str) else ""


def capacities_pool(
    section: Any, block: Any = None, now: Any = None
) -> CapacitiesPoolResult:
    """Resolve the Capacities-side pool from the folded config + artifact block.

    Pure: reads only the ``Micro-Adventures`` -> ``Capacities`` settings and the
    published artifact-style ``block`` (``{status, read_at, items, warnings}``).
    NO I/O, no clock: pass ``now`` to evaluate freshness, omit it to skip the
    staleness check. ``app/main.py`` calls it while building the Live block
    (``pool_source`` / ``pool_warnings``) and reads the artifact's published
    block only when the extension is enabled and configured, so the default
    (disabled) path does no extra I/O.

    Status is one of:

      * ``disabled`` — ``enabled`` is not the boolean ``True``; no ideas.
      * ``not-configured`` — enabled, but ``collection`` and ``tag`` are both
        blank; no ideas (the producer applies the membership filter).
      * ``unavailable`` — no block, or a malformed/failed block; no ideas.
      * ``stale`` — ``read_at`` older than ``refresh_cadence_hours`` (default
        168); ideas are still returned for diagnostics but callers fall back.
      * ``ok`` — otherwise.

    Never raises. The effort/collection/tag membership FILTER belongs to the
    producer; this function only carries the settings and the published block.
    Rows keep STABLE ids (``cap-<object id>``) so history/streak keyed by id
    survive. It is called only from that gather path, never from a live
    request path and never per row — it performs no I/O of its own.
    """
    caps = _capacities_settings(section)
    if caps.get("enabled") is not True:
        return CapacitiesPoolResult(
            [], CAPACITIES_STATUS_DISABLED,
            "Capacities micro-adventure ideas are off.",
        )

    collection = _string_setting(caps, "collection")
    tag = _string_setting(caps, "tag")
    if not collection and not tag:
        return CapacitiesPoolResult(
            [], CAPACITIES_STATUS_NOT_CONFIGURED,
            "Capacities micro-adventure ideas need a 'collection' or a 'tag'.",
        )

    if not isinstance(block, dict):
        return CapacitiesPoolResult(
            [], CAPACITIES_STATUS_UNAVAILABLE,
            "No micro_adventure_ideas block was published; using the config pool.",
        )

    block_status = block.get("status")
    if isinstance(block_status, str) and block_status.strip():
        if block_status.strip().lower() != CAPACITIES_STATUS_OK:
            return CapacitiesPoolResult(
                [], CAPACITIES_STATUS_UNAVAILABLE,
                f"micro_adventure_ideas block status is {block_status!r}; "
                "using the config pool.",
            )
    if not isinstance(block.get("items"), list):
        return CapacitiesPoolResult(
            [], CAPACITIES_STATUS_UNAVAILABLE,
            "micro_adventure_ideas block has no usable 'items' list; "
            "using the config pool.",
        )

    normalized = normalize_capacities_ideas(block)
    warnings = list(normalized.warnings)

    cadence = _positive_int(
        caps.get("refresh_cadence_hours"), DEFAULT_REFRESH_CADENCE_HOURS
    )
    read_at = _coerce_timestamp(block.get("read_at"))
    now_dt = _coerce_timestamp(now)
    raw_read_at = block.get("read_at")
    if now_dt is not None and raw_read_at not in (None, ""):
        if read_at is None:
            warnings.append(
                f"micro_adventure_ideas read_at {raw_read_at!r} is not a "
                "timestamp; freshness unknown, treated as current."
            )
        elif now_dt - read_at > timedelta(hours=cadence):
            return CapacitiesPoolResult(
                list(normalized.ideas), CAPACITIES_STATUS_STALE,
                f"micro_adventure_ideas is older than {cadence} hours; "
                "using the config pool.",
                tuple(warnings),
            )

    return CapacitiesPoolResult(
        list(normalized.ideas), CAPACITIES_STATUS_OK,
        f"{len(normalized.ideas)} Capacities micro-adventure idea(s).",
        tuple(warnings),
    )


def capacities_live_limit(live_cap: Any = DEFAULT_LIVE_POOL_CAP) -> int:
    """How many Capacities ideas may enter the live pool: at most half
    (floor) of ``live_cap``. Unusable input reads as the default cap."""
    if isinstance(live_cap, bool):
        return 0
    try:
        n = int(live_cap)
    except (TypeError, ValueError):
        n = DEFAULT_LIVE_POOL_CAP
    return n // 2 if n > 0 else 0


def _coerce_pool_rows(rows: Any) -> list[PoolIdea]:
    """Coerce a pool input to ``PoolIdea`` rows.

    A :class:`CapacitiesPoolResult` contributes rows only when it is ``ok``;
    a stale/unavailable/disabled result contributes nothing so the config pool
    is used alone. Any non-row entry is skipped. Never raises."""
    if isinstance(rows, CapacitiesPoolResult):
        rows = rows.ideas if rows.status == CAPACITIES_STATUS_OK else []
    if rows is None or isinstance(rows, (str, bytes, dict)):
        return []
    try:
        candidates = list(rows)
    except TypeError:
        return []
    return [row for row in candidates if isinstance(row, PoolIdea)]


def limit_capacities_ideas(
    ideas: Any, live_cap: Any = DEFAULT_LIVE_POOL_CAP
) -> list[PoolIdea]:
    """The Capacities rows eligible for the live pool (first ``live_cap // 2``)."""
    return _coerce_pool_rows(ideas)[: capacities_live_limit(live_cap)]


def merge_pool(
    config_pool: Any,
    capacities_ideas: Any = (),
    live_cap: Any = DEFAULT_LIVE_POOL_CAP,
) -> list[PoolIdea]:
    """Append Capacities ideas after the config pool, deduped and capped.

    Config rows come first and keep pool order. Capacities rows follow, deduped
    by id and by normalized idea text against everything already kept, and at
    most :func:`capacities_live_limit` of them enter the pool (half of the live
    pool). Empty, stale, failed, or garbage Capacities input yields EXACTLY the
    config pool, so ``FALLBACK_POOL`` behavior is unchanged. Never raises."""
    merged: list[PoolIdea] = []
    seen_ids: set[str] = set()
    seen_text: set[str] = set()

    for row in _coerce_pool_rows(config_pool):
        if row.id in seen_ids:
            continue
        text_key = _normalized_text_key(row.idea)
        if text_key and text_key in seen_text:
            continue
        seen_ids.add(row.id)
        if text_key:
            seen_text.add(text_key)
        merged.append(row)

    limit = capacities_live_limit(live_cap)
    added = 0
    for row in _coerce_pool_rows(capacities_ideas):
        if added >= limit:
            break
        if row.id in seen_ids:
            continue
        text_key = _normalized_text_key(row.idea)
        if text_key and text_key in seen_text:
            continue
        seen_ids.add(row.id)
        if text_key:
            seen_text.add(text_key)
        merged.append(row)
        added += 1

    return merged


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


def resolve_prior(
    history: Iterable[HistoryEntry],
    *,
    today: date,
    todoist_completed: Callable[[str], bool | None] | None = None,
) -> PriorResolution:
    """Resolve whether the most recent (history[0]) adventure was completed.

    The done state itself lives in the state-dir history log: an entry with
    ``done`` already set (the cockpit "Did it" / Dismiss answer, or an earlier
    flushed Todoist-completion signal) is final and skipped here.

    Applies ONLY when history[0].done is None and history[0].date < today.
      (a) todoist_completed(task_id) True → virtual done:true.
      (b) else pending_confirm = history[0], no done_update.
    A callable raising, or returning False/None, is inconclusive.

    The former daily-note ``### Live`` checkbox signal is gone; it has no
    state-dir equivalent, so an unconfirmed entry degrades to
    "not done" (pending confirm) until answered in the cockpit."""
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

    # (b) Inconclusive — flag for explicit confirmation.
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


def resolve_loose_end(
    history: Iterable[HistoryEntry], *, date_: date, entry_id: str, done: bool,
) -> tuple[HistoryEntry, ...] | None:
    """Record the Set up day banner's answer (Did it → True, Dismiss → False)
    for an unconfirmed prior entry. Only an entry still awaiting confirmation
    (done is None) matches; anything else returns None and the log is left
    alone, so an answered entry cannot be silently rewritten."""
    hist = tuple(history)
    target = next(
        (e for e in hist if e.date == date_ and e.id == entry_id and e.done is None),
        None,
    )
    if target is None:
        return None
    return apply_done_update(hist, replace(target, done=done))


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
