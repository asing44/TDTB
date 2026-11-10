"""untimed_lane.py — the "quick check-in" untimed-lane classifier (A1).

Owner decision 2026-11-09 (hand corrections): a date-only Todoist row that is
either very short (<= 10 minutes) or named like a check-in ("fu ...", "...
texts", "check ...", "follow up") belongs in the existing all-day state
instead of a 30-minute timed slot.  Evidence and the reversal record live in
``docs/queue/2026-10-16-placement-edits/05a-untimed-lane-reversal.md``.

This reverses the 2026-10-16 queue decision "no automatic date-only rule"
(``docs/queue/2026-10-16-placement-edits/05-keep-all-day-action.md``, see the
comment at the ``main._canonicalize_route_assigned`` hook).  The per-row
"Keep all-day" action from that decision stays; this adds an automatic rule
for quick check-ins only.

The lane is NOT a new lane type.  ``blocks == 0`` is the existing all-day
state, which already runs end to end: ``sequence.timed_auto_pins`` emits no pin
for a row with no ``scheduled_start``, ``sequence._assigned_duration_minutes``
gives a zero-block row no placement duration (so it is never re-seated), and
``shadow.build_plan_manifest`` emits ``schedule-all-day`` with a date-only
Todoist due.

``classify`` is pure: no I/O, no config-file read, no clock.  The caller
passes the server-canonicalized row plus the config dict it already holds.

Config (all optional; the code defaults below apply when a key is absent):

    {"Defaults": {"untimed_lane": {
        "enabled": true,          # false turns the automatic rule off
        "max_blocks": 0.3333,     # 1/3 block = 10 minutes at 30 min/block
        "tokens": ["^fu\\b", ...],  # regexes, case-insensitive, replace defaults
        "overrides": {"<row name>": false}   # per-item opt-out (or opt-in)
    }}}

The thresholds live here rather than in ``config_reader.FALLBACK_DEFAULTS`` so
a single source of truth owns the rule; the config file overrides them.
"""
from __future__ import annotations

import math
import re
from typing import Any

#: The day runs on 30-minute blocks (``sequence._assigned_duration_minutes``).
MINUTES_PER_BLOCK = 30

#: A third of a block — 10 minutes.  Anything at or under this is a check-in.
DEFAULT_MAX_BLOCKS = 1 / 3

#: Check-in name tokens, tried in order, case-insensitive.  ``^fu\b`` is the
#: "fu <person>" follow-up shorthand; ``texts?`` covers "... inspo texts";
#: ``check`` covers "check if feeling shit"; ``follow[- ]?up`` covers the
#: spelled-out form.
DEFAULT_TOKENS = (r"^fu\b", r"\btexts?\b", r"\bcheck\b", r"follow[- ]?up")

#: The lane is on unless the operator turns it off.
DEFAULT_ENABLED = True

#: Zones that are not an explicit placement request.
_NON_EXPLICIT_ZONES = frozenset({"", "any", "none"})


def _normalize_name(value: Any) -> str:
    """Comparison form for a row name: collapsed whitespace, casefolded."""
    return " ".join(str(value or "").split()).casefold()


def _number(value: Any) -> float | None:
    """A finite, non-bool number, else ``None``."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        numeric = float(value)
    except (OverflowError, ValueError):
        return None
    return numeric if math.isfinite(numeric) else None


def _lane_config(config: Any) -> dict[str, Any]:
    """Resolve ``Defaults`` → ``untimed_lane`` from a config dict.

    Accepts the shape the app already passes around: the sections dict whose
    ``Defaults`` member is the flat-dotted keys plus the nested mirror
    (``app_config.sections_from_document``).  Nested keys win over dotted ones,
    a top-level ``untimed_lane`` is honoured for a bare caller, and anything
    malformed resolves to ``{}`` so the code defaults apply.
    """
    if not isinstance(config, dict):
        return {}
    lane: dict[str, Any] = {}
    defaults = config.get("Defaults")
    if isinstance(defaults, dict):
        dotted = defaults.get("untimed_lane")
        if isinstance(dotted, dict):
            lane.update(dotted)
        prefix = "untimed_lane."
        for key, value in defaults.items():
            if isinstance(key, str) and key.startswith(prefix):
                lane.setdefault(key[len(prefix):], value)
    top = config.get("untimed_lane")
    if isinstance(top, dict):
        for key, value in top.items():
            lane.setdefault(key, value)
    return lane


def _max_blocks(lane: dict[str, Any]) -> float:
    """The configured block threshold, else the code default (fail-soft)."""
    value = _number(lane.get("max_blocks"))
    return value if value is not None and value >= 0 else DEFAULT_MAX_BLOCKS


def _enabled(lane: dict[str, Any]) -> bool:
    """Explicit false (or 0) turns the rule off; anything else leaves it on."""
    if "enabled" not in lane:
        return DEFAULT_ENABLED
    value = lane.get("enabled")
    if value is False or value == 0:
        return False
    return True


def _compiled_tokens(lane: dict[str, Any]) -> list[re.Pattern[str]]:
    """The configured token regexes; a malformed one never raises.

    The configured list REPLACES the default list, so an operator can narrow
    the rule.  An invalid regex is dropped (it can never match) rather than
    failing a planning call.
    """
    raw = lane.get("tokens")
    patterns = list(DEFAULT_TOKENS) if raw is None else raw
    if isinstance(patterns, str):
        patterns = [patterns]
    if not isinstance(patterns, (list, tuple)):
        patterns = list(DEFAULT_TOKENS)
    compiled: list[re.Pattern[str]] = []
    for pattern in patterns:
        if not isinstance(pattern, str) or not pattern.strip():
            continue
        try:
            compiled.append(re.compile(pattern, re.IGNORECASE))
        except re.error:
            continue
    return compiled


def _name_override(name: str, lane: dict[str, Any]) -> bool | None:
    """The per-item config override for ``name``, or ``None`` when there is none."""
    overrides = lane.get("overrides")
    if not isinstance(overrides, dict):
        return None
    wanted = _normalize_name(name)
    for key, value in overrides.items():
        if _normalize_name(key) != wanted:
            continue
        if isinstance(value, dict):
            value = value.get("untimed")
        if isinstance(value, bool):
            return value
        return None
    return None


def _row_is_native_todoist(item: dict[str, Any]) -> bool:
    """Whether the row is a native Todoist row.

    Follows ``sequence._is_native_todoist_row`` but is deliberately STRICTER:
    it requires a positive Todoist identity (an explicit ``source``, a
    ``todoist_id``, a ``todoist://`` path, or a ``todoist`` type).  Sequence's
    legacy branch treats a source-less, identity-less fixture as native so it
    keeps protecting it; here the same row must stay TIMED, because "never
    assume untimed" is the fail-safe direction for this lane.  Duplicated
    rather than imported so this module stays pure and dependency free.
    """
    if not isinstance(item, dict):
        return False
    source = str(item.get("source") or "").strip().casefold()
    if source:
        return source == "todoist"
    if str(item.get("todoist_id") or "").strip():
        return True
    path = str(item.get("path") or "").strip().casefold()
    if path:
        return path.startswith("todoist://")
    types = item.get("types")
    if isinstance(types, (list, tuple, set)) and any(
        str(item_type).strip().casefold() == "todoist" for item_type in types
    ):
        return True
    return False


def _row_minutes(item: dict[str, Any]) -> float | None:
    """The row's native duration in minutes, or ``None`` when it has none.

    Exact minutes are read FIRST (``duration_minutes`` — a duration label or
    remembered value — then ``duration``, the Todoist-native minutes) because
    the digest's ``blocks`` is quantised UP to whole 30-minute blocks:
    ``main.resolve_assigned_blocks`` turns a 10-minute Todoist duration into
    ``blocks = 1``.  Reading ``blocks`` first would make the 10-minute rule
    unreachable for exactly the rows it targets.

    ``native_blocks`` is the server-only provenance for the row's effective
    block count (its today-only override may be the all-day zero), so it
    outranks ``blocks`` for the last-resort minutes reading.
    """
    for key in ("duration_minutes", "duration"):
        minutes = _number(item.get(key))
        if minutes is not None:
            return minutes
    for key in ("native_blocks", "blocks"):
        blocks = _number(item.get(key))
        if blocks is not None:
            return blocks * MINUTES_PER_BLOCK
    return None


def classify(item: Any, config: Any = None) -> dict[str, Any]:
    """Classify one row as an untimed quick check-in or a timed item.

    Returns ``{"untimed": bool, "reason": str}``.  Both keys are always
    present; the reason is stable, human-readable, and safe to show on a row
    (it never carries provider text beyond the row's own name fields).

    Untimed only when ALL hold:

    a. a native Todoist row with no due time (``scheduled_start`` absent/None,
       i.e. a date-only due).  A due time is "specified" and stays timed;
    b. no explicit placement request: no zone other than ``any``/none, no
       ``latest_start``/``latest_end``, no ``allow_time_adjustment``, and no
       per-item config override saying no;
    c. a name matching a check-in token, or a native duration of at most
       ``max_blocks`` (default 1/3 block = 10 minutes).  Exact minutes are
       read from ``duration_minutes``/``duration`` before ``blocks``, because
       the digest quantises ``blocks`` up to whole 30-minute blocks.

    A client-supplied ``blocks > 0`` escape hatch is the caller's business
    (``main._canonicalize_route_assigned``), not this function's: the row here
    is already the server-canonicalized view.
    """
    if not isinstance(item, dict):
        return {"untimed": False, "reason": "not a row"}
    lane = _lane_config(config)
    if not _enabled(lane):
        return {"untimed": False, "reason": "untimed lane disabled in config"}

    # (a) native Todoist row with a date-only due.
    if not _row_is_native_todoist(item):
        return {"untimed": False, "reason": "not a native Todoist row"}
    due_time = str(item.get("scheduled_start") or "").strip()
    if due_time:
        return {"untimed": False, "reason": f"due time present: {due_time}"}

    # (b) no explicit placement request.
    zone = str(item.get("zone") or "").strip().casefold()
    if zone not in _NON_EXPLICIT_ZONES:
        return {"untimed": False, "reason": f"explicit zone: {zone}"}
    for key in ("latest_start", "latest_end"):
        if str(item.get(key) or "").strip():
            return {"untimed": False, "reason": f"{key} set"}
    if item.get("allow_time_adjustment") is True:
        return {"untimed": False, "reason": "time adjustment allowed"}
    name = str(item.get("name") or item.get("id") or "").strip()
    override = _name_override(name, lane)
    if override is False:
        return {"untimed": False, "reason": "per-item config override"}

    # (c) a check-in name, or a short enough native duration.
    minutes = _row_minutes(item)
    if override is True:
        return {"untimed": True, "reason": "per-item config override"}
    for pattern in _compiled_tokens(lane):
        if pattern.search(name):
            return {
                "untimed": True,
                "reason": f"check-in name: {pattern.pattern}",
            }
    max_minutes = _max_blocks(lane) * MINUTES_PER_BLOCK
    if minutes is not None and minutes <= max_minutes + 1e-9:
        return {
            "untimed": True,
            "reason": f"short duration: {int(round(minutes))} min",
        }
    if minutes is None:
        return {"untimed": False, "reason": "no native duration: assume timed"}
    return {
        "untimed": False,
        "reason": f"not a quick check-in: {int(round(minutes))} min",
    }
