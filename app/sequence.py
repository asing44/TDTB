"""sequence.py — T12: server-side validation of a sequence proposal.

The belt to judgment.py's suspenders: judgment.py's ``_validate_sequence_proposal``
schema-gates the Agent SDK's raw response (retries once on failure); this module
re-validates the *accepted* proposal against the actual assigned/anchored/config
data with zero trust in what the SDK claimed. Two independent passes catch
different failure classes — the SDK could pass its own schema check yet still be
wrong about a zone, a latest_start, an overlap, or a dropped item.

Rule semantics mirror the tdtb-bridger-vault skill's Phase 4 placement passes:
  - Zone compatibility and latest_start are SOFT — flagged as warnings, never
    dropped (never-bump). Matches judgment.py's SEQUENCE_SYSTEM_PROMPT framing
    ("flag ... only when none exists" / "flagged ... if forced later").
  - The morning-workout ban is HARD — reject the whole proposal. Only a Press
    micro-adventure item with an explicit ``before_work`` zone preset in config
    is exempt, mirroring judgment.py's ``_validate_sequence_proposal``.
  - Structural invariants (HH:MM validity, end>start, chronological order,
    no overlap with a non-permeable anchored block, every assigned item present
    exactly once) are HARD.

``is_workout_item`` + the Press/before_work exception resolution are the single
canonical source for both layers — judgment.py imports them from here (the one
permitted edit to judgment.py per the T12 spec), never the other direction.
"""
from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

import placement_rules

_HHMM_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
_WORKOUT_TYPES = frozenset({"workout", "exercise", "fitness"})
_WORKOUT_KEYWORDS = ("workout", "exercise", "fitness")

# Calendar capacity classes excluded from planning (frozen contract 17):
# ``ignored`` costs zero and stays visible; ``quarantined`` is a KNOWN title
# the user has not reviewed yet. Neither may become a hard wall.
_CALENDAR_EXCLUDED_CLASSES = frozenset({"ignored", "quarantined"})

# Default zone windows (HH:MM, 24h), used when config carries no explicit
# Template Blocks / Defaults hours for a zone. Sensible fallbacks only —
# config values (Trinoor Hours, eod) take precedence when present.
_DEFAULT_ZONE_WINDOWS: dict[str, tuple[str, str]] = {
    "before_work": ("00:00", "08:30"),
    "work_hours": ("08:30", "17:00"),
    "after_work": ("17:00", "20:00"),
    "evening": ("18:00", "23:59"),
    "weekend": ("00:00", "23:59"),
    "any": ("00:00", "23:59"),
}


def validate_pinned_rows(
    pinned_rows: list[dict[str, Any]], assigned: list[dict[str, Any]]
) -> list[str]:
    """Validate immutable dated placements before any judgment charge."""
    errors: list[str] = []
    assigned_ids = {
        str(item.get("id") or item.get("name")) for item in assigned
        if item.get("id") or item.get("name")
    }
    seen: set[str] = set()
    spans: list[tuple[str, int, int]] = []
    for pin in pinned_rows:
        pin_id = str(pin.get("id") or "")
        start, end = pin.get("start"), pin.get("end")
        start_min = placement_rules.time_to_minutes(start)
        end_min = placement_rules.time_to_minutes(end)
        if (not pin_id or start_min is None or end_min is None
                or end_min <= start_min):
            errors.append(f"malformed pinned row {pin_id or '<missing id>'!r}")
            continue
        if pin_id in seen:
            errors.append(f"duplicate pinned row {pin_id!r}")
        seen.add(pin_id)
        if pin_id not in assigned_ids:
            errors.append(f"foreign pinned row {pin_id!r}")
        spans.append((pin_id, start_min, end_min))
    for index, (left_id, left_start, left_end) in enumerate(spans):
        for right_id, right_start, right_end in spans[index + 1:]:
            if left_start < right_end and right_start < left_end:
                errors.append(
                    f"pinned rows {left_id!r} and {right_id!r} overlap"
                )
    return errors


def _is_native_todoist_row(item: dict[str, Any]) -> bool:
    """Return whether an item is a Todoist row eligible for native protection.

    Real source rows carry ``source``.  Source-less legacy fixtures remain
    supported, while explicit non-Todoist sources and vault paths are never
    treated as native timed tasks.
    """
    source = str(item.get("source") or "").strip().casefold()
    if source:
        return source == "todoist"
    if item.get("todoist_id"):
        return True
    path = str(item.get("path") or "").strip().casefold()
    if path:
        return path.startswith("todoist://")
    types = item.get("types")
    if isinstance(types, (list, tuple, set)) and any(
        str(item_type).strip().casefold() == "todoist" for item_type in types
    ):
        return True
    # Backward compatibility for callers that predate source metadata.
    return True


def _protective_blocks(value: Any) -> int | float:
    """Resolve a block count without allowing bad data to remove protection."""
    if isinstance(value, bool):
        return 1
    if not isinstance(value, (int, float)):
        return 1
    try:
        numeric = float(value)
    except (OverflowError, ValueError):
        return 1
    if not math.isfinite(numeric):
        return 1
    if numeric == 0:
        return 0
    return value if numeric > 0 else 1


def _derive_timed_auto_pins(
    assigned: list[dict[str, Any]], *, exclude_ids: set[str] | None = None
) -> tuple[list[dict[str, Any]], list[str]]:
    """Derive valid native pins and hard errors for unrepresentable intervals."""
    exclude = exclude_ids or set()
    pins: list[dict[str, Any]] = []
    errors: list[str] = []
    for item in assigned:
        if not _is_native_todoist_row(item):
            continue
        if item.get("allow_time_adjustment") is True:
            continue
        start = placement_rules.canonical_time(item.get("scheduled_start"))
        if start is None:
            continue
        row_id = str(item.get("id") or item.get("name") or "")
        if not row_id or row_id in exclude:
            continue
        # ``native_blocks`` is server-only provenance for the Todoist due
        # duration. The ordinary ``blocks`` value remains the effective
        # planning duration and may legitimately be overridden to zero.
        duration_value = (
            item["native_blocks"] if "native_blocks" in item
            else item.get("blocks")
        )
        n = _protective_blocks(duration_value)
        if n == 0:
            continue
        try:
            duration_minutes = max(1, int(round(float(n) * 30)))
        except (OverflowError, ValueError):
            # A very large positive value is still protective; the same-day
            # cap below is the only representation that can be emitted.
            duration_minutes = 24 * 60
        start_min = _to_minutes(start)
        end_min = min(start_min + duration_minutes, 24 * 60 - 1)
        # HH:MM has no representation for the next day's midnight. For every
        # representable start before 23:59 keep the protective interval
        # positive even when rounding/capping a tiny duration near midnight.
        if end_min <= start_min and start_min < 24 * 60 - 1:
            end_min = 24 * 60 - 1
        if end_min <= start_min:
            # A 23:59 native start cannot be represented as a positive same-day
            # HH:MM interval. Do not silently make the row movable.
            errors.append(
                f"native timed row {row_id!r}: unrepresentable native interval "
                f"starting at {start!r}"
            )
            continue
        pins.append({
            "id": row_id, "start": start,
            "end": f"{end_min // 60:02d}:{end_min % 60:02d}",
            "zone": None,
        })
    return pins, errors


def timed_auto_pins(
    assigned: list[dict[str, Any]], *, exclude_ids: set[str] | None = None
) -> list[dict[str, Any]]:
    """Todoist rows with a native time are placement-immune by default.

    Server-authoritative — derives an immutable pin at each timed row's own
    ``scheduled_start`` regardless of what the client sent, so a client
    whose pin state was cleared (fingerprint drift, stale persistence) can
    never hand a timed row to judgment as movable. This applies to both
    recurring and one-off Todoist rows unless the item explicitly opts into
    retiming with ``allow_time_adjustment: true``.

    Duration is fail-closed: positive finite block counts use their duration,
    while missing, malformed, non-finite, and negative values receive one
    protective block. An explicit numeric ``blocks == 0`` is the all-day
    state and has no timeline pin. ``native_blocks``, when present, is used
    instead for the server-authoritative protection duration. ``exclude_ids``
    skips rows the client already pinned. Unrepresentable intervals are
    omitted here because this compatibility helper returns pins only;
    :func:`enforce_native_pins` and :func:`verify_native_pins_in_sequence`
    surface those cases as hard errors.
    """
    pins, _errors = _derive_timed_auto_pins(assigned, exclude_ids=exclude_ids)
    return pins


def recurring_auto_pins(
    assigned: list[dict[str, Any]], *, exclude_ids: set[str] | None = None
) -> list[dict[str, Any]]:
    """Compatibility wrapper for the former recurring-only public helper.

    New callers should use :func:`timed_auto_pins`, which protects both
    recurring and one-off native Todoist times. Keeping this wrapper scoped to
    recurring rows preserves the legacy helper's public behavior for existing
    consumers while the planning boundary uses the generalized contract.
    """
    recurring = [item for item in assigned if item.get("is_recurring")]
    return timed_auto_pins(recurring, exclude_ids=exclude_ids)


def _native_conflicts_for_pins(
    native: list[dict[str, Any]], pinned_rows: list[dict[str, Any]]
) -> list[str]:
    """Return caller pins that move a protected native timed row."""
    native_by_id = {pin["id"]: pin for pin in native}
    errors: list[str] = []
    for pin in pinned_rows:
        pin_id = str(pin.get("id") or "")
        native_pin = native_by_id.get(pin_id)
        if native_pin is None:
            continue
        pin_start = placement_rules.time_to_minutes(pin.get("start"))
        pin_end = placement_rules.time_to_minutes(pin.get("end"))
        native_start = placement_rules.time_to_minutes(native_pin["start"])
        native_end = placement_rules.time_to_minutes(native_pin["end"])
        if (pin_start, pin_end) != (native_start, native_end):
            errors.append(
                f"pinned row {pin_id!r} conflicts with its protected native "
                f"time {native_pin['start']}-{native_pin['end']}"
            )
    return errors


def canonicalize_pinned_row(pin: dict[str, Any]) -> dict[str, Any]:
    """Copy a pin while normalizing any valid endpoint to canonical HH:MM."""
    normalized = dict(pin)
    for key in ("start", "end"):
        value = placement_rules.canonical_time(pin.get(key))
        if value is not None:
            normalized[key] = value
    return normalized


def enforce_native_pins(
    assigned: list[dict[str, Any]], pinned_rows: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[str]]:
    """Canonicalize and complete server-authoritative native timed pins.

    Matching caller pins are replaced by their canonical server-derived row,
    conflicting caller pins fail closed, and omitted protected rows are added.
    Non-native manual pins pass through unchanged so the normal pinned-row
    validator can continue to report malformed or duplicate manual state.
    """
    native, native_errors = _derive_timed_auto_pins(assigned)
    errors = _native_conflicts_for_pins(native, pinned_rows)
    errors.extend(native_errors)
    native_by_id = {pin["id"]: pin for pin in native}
    effective: list[dict[str, Any]] = []
    native_seen: set[str] = set()
    for pin in pinned_rows:
        pin_id = str(pin.get("id") or "")
        native_pin = native_by_id.get(pin_id)
        if native_pin is not None:
            if pin_id not in native_seen:
                effective.append(native_pin)
                native_seen.add(pin_id)
            continue
        effective.append(canonicalize_pinned_row(pin))
    for pin in native:
        if pin["id"] not in native_seen:
            effective.append(pin)
            native_seen.add(pin["id"])
    return effective, errors


def verify_native_pins_in_sequence(
    assigned: list[dict[str, Any]],
    sequence_rows: list[dict[str, Any]],
    pinned_rows: list[dict[str, Any]],
) -> list[str]:
    """Verify that a sequence still carries every protected native pin.

    Native pins are recomputed from the server-side assigned rows rather than
    trusted from client ``pinned_rows`` or a snapshot. This makes omission of
    the client pin list unable to bypass protection. The supplied pin list is
    still checked for an unauthorized moved native pin when present.
    """
    native, native_errors = _derive_timed_auto_pins(assigned)
    errors = _native_conflicts_for_pins(native, pinned_rows)
    errors.extend(native_errors)
    by_id = {str(row.get("id")): row for row in sequence_rows}
    for pin in native:
        row = by_id.get(pin["id"])
        if row is None:
            errors.append(f"protected native row {pin['id']!r} missing from sequence")
            continue
        row_start = placement_rules.time_to_minutes(row.get("start"))
        row_end = placement_rules.time_to_minutes(row.get("end"))
        pin_start = placement_rules.time_to_minutes(pin["start"])
        pin_end = placement_rules.time_to_minutes(pin["end"])
        if (row_start, row_end) != (pin_start, pin_end):
            row_start_text = (
                placement_rules.canonical_time(row.get("start"))
                or str(row.get("start"))
            )
            row_end_text = (
                placement_rules.canonical_time(row.get("end"))
                or str(row.get("end"))
            )
            errors.append(
                f"protected native row {pin['id']!r} moved from "
                f"{pin['start']}-{pin['end']} to "
                f"{row_start_text}-{row_end_text}"
            )
    return errors


# ---------------------------------------------------------------------------
# Shared workout detection (canonical — judgment.py imports from here)
# ---------------------------------------------------------------------------

def is_workout_item(item: dict[str, Any]) -> bool:
    """True if an item's id/zone/type indicates a workout/exercise/fitness block.

    Matches on any of: the item id containing a workout keyword, its zone
    equal to a workout keyword, or an explicit "type" field naming one of
    the workout types. Case-insensitive.
    """
    item_id = str(item.get("id", "")).lower()
    if any(kw in item_id for kw in _WORKOUT_KEYWORDS):
        return True
    zone = str(item.get("zone", "")).lower()
    if zone in _WORKOUT_TYPES:
        return True
    item_type = str(item.get("type", "")).lower()
    if item_type in _WORKOUT_TYPES:
        return True
    return False


def _press_before_work_ids(config: dict[str, Any] | None) -> set[str]:
    """Names of config presets that are a Press item explicitly zoned
    before_work — the sole exception to the morning-workout ban."""
    ids: set[str] = set()
    if not config:
        return ids
    for preset in config.get("presets", []) or []:
        name = preset.get("Name") or preset.get("name")
        zone = str(preset.get("Zone") or preset.get("zone") or "").strip().lower()
        if name and "press" in str(name).lower() and zone == "before_work":
            ids.add(str(name))
    return ids


# ---------------------------------------------------------------------------
# Result shape
# ---------------------------------------------------------------------------

@dataclass
class ValidationResult:
    ok: bool
    hard_errors: list[str] = field(default_factory=list)
    warnings: list[dict[str, str]] = field(default_factory=list)
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    diagnostics: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        result = {
            "ok": self.ok,
            "hard_errors": self.hard_errors,
            "warnings": self.warnings,
        }
        if self.conflicts:
            result["conflicts"] = self.conflicts
        if self.diagnostics:
            result["diagnostics"] = self.diagnostics
        return result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_hhmm(value: Any) -> bool:
    return placement_rules.canonical_time(value) is not None


def _to_minutes(hhmm: Any) -> int:
    minutes = placement_rules.time_to_minutes(hhmm)
    if minutes is None:
        raise ValueError(f"invalid time: {hhmm!r}")
    return minutes


def _normalize_time(value: Any) -> str | None:
    """Best-effort normalize a config time ('7:45 AM', '—', '12:00 PM') to HH:MM
    24h, or None if unparseable/absent (e.g. the '—' placeholder for
    duration-only anchored blocks)."""
    if not isinstance(value, str) or value.strip() in ("", "—", "-", "--"):
        return None
    return placement_rules.canonical_time(value)


def canonicalize_pinned_rows(
    pinned_rows: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Return a copied pin list with equivalent times in canonical form."""
    return [canonicalize_pinned_row(pin) for pin in (pinned_rows or [])]


def canonicalize_overlap_grants(
    grants: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Return copied grants whose valid interval endpoints are canonical."""
    result: list[dict[str, Any]] = []
    for grant in grants or []:
        if not isinstance(grant, dict):
            result.append(grant)
            continue
        normalized = dict(grant)
        for key in ("primary_interval", "companion_interval"):
            interval = grant.get(key)
            if not isinstance(interval, dict):
                continue
            copied = dict(interval)
            for endpoint in ("start", "end"):
                value = placement_rules.canonical_time(interval.get(endpoint))
                if value is not None:
                    copied[endpoint] = value
            normalized[key] = copied
        result.append(normalized)
    return result


def canonicalize_time_frame(frame: dict[str, Any] | None) -> dict[str, Any]:
    """Project frame identity fields into canonical time values for comparison."""
    normalized = dict(frame or {})
    for key in ("now", "anchor", "effective_eod", "config_eod"):
        value = placement_rules.canonical_time(normalized.get(key))
        if value is not None:
            normalized[key] = value
    return normalized


def _zone_window(zone: str, config: dict[str, Any] | None) -> tuple[str, str] | None:
    """Resolve a zone's compatible [start, end) window. Reads config's
    Template Blocks / "Trinoor Hours" for work_hours when present; otherwise
    the sensible fallback windows. Unknown zone -> None (no check possible;
    treated as always-compatible, matching 'any' semantics)."""
    zone = (zone or "").strip().lower()
    if zone in ("", "any"):
        return None

    if zone == "work_hours" and config:
        sections = config.get("sections") or {}
        template = sections.get("Template Blocks") or {}
        hours = template.get("Trinoor Hours")
        if isinstance(hours, list) and hours:
            starts = [_normalize_time(r.get("Start")) for r in hours]
            ends = [_normalize_time(r.get("End")) for r in hours]
            starts = [s for s in starts if s]
            ends = [e for e in ends if e]
            if starts and ends:
                return (min(starts), max(ends))

    return _DEFAULT_ZONE_WINDOWS.get(zone)


def _in_window(start: str, window: tuple[str, str]) -> bool:
    lo, hi = window
    return _to_minutes(lo) <= _to_minutes(start) <= _to_minutes(hi)


def _anchored_span(block: dict[str, Any]) -> tuple[int, int] | None:
    """Resolve an anchored block's [start, end) span in minutes, deriving end
    from Duration when End is the '—' placeholder (hard, duration-only blocks)."""
    start = _normalize_time(block.get("Start"))
    if not start:
        return None
    start_m = _to_minutes(start)
    end = _normalize_time(block.get("End"))
    if end:
        return (start_m, _to_minutes(end))

    duration = block.get("Duration")
    minutes = 0
    if isinstance(duration, str):
        m = re.match(r"^(\d+)\s*m$", duration.strip())
        if m:
            minutes = int(m.group(1))
    elif isinstance(duration, (int, float)):
        minutes = int(duration)
    return (start_m, start_m + minutes)


def _placement_window(item: dict[str, Any] | None) -> tuple[int, int] | None:
    """Resolve an item's explicit placement window to minute bounds."""
    raw = item.get("placement_window") if isinstance(item, dict) else None
    if not isinstance(raw, dict):
        return None
    start = _normalize_time(raw.get("start"))
    end = _normalize_time(raw.get("end"))
    if not start or not end or _to_minutes(end) <= _to_minutes(start):
        return None
    return (_to_minutes(start), _to_minutes(end))


def _mint_source_identity(
    item: dict[str, Any], item_id: str, window: tuple[int, int]
) -> tuple[Any, ...]:
    """Return the stable identity used to collapse a selected Mint row."""
    source_id = str(item.get("mint_session_id") or "").strip()
    if source_id:
        return ("source", source_id)
    # Older callers did not carry a source ID. Keep their distinct windows
    # separate while still collapsing an exact duplicate representation.
    return ("row", item_id, window)


def _mint_candidate_sort_key(
    candidate: tuple[dict[str, Any], tuple[int, int], str, tuple[Any, ...]]
) -> tuple[Any, ...]:
    item, window, item_id, identity = candidate
    source_id = str(identity[1])
    # Mint options are emitted in time order by the source. The source ID is a
    # deterministic tie-breaker for same-time options with distinct labels.
    return (
        window[0], window[1], source_id.casefold(), source_id,
        item_id.casefold(), item_id,
        0 if item.get("id") == item.get("name") else 1,
    )


def _canonical_mint_candidates(
    items: list[dict[str, Any]] | None,
) -> list[tuple[dict[str, Any], tuple[int, int], str, tuple[Any, ...]]]:
    """Deduplicate selected Mint items and return them in stable option order."""
    grouped: dict[tuple[Any, ...], list[tuple[dict[str, Any], tuple[int, int], str, tuple[Any, ...]]]] = {}
    for item in items or []:
        if not isinstance(item, dict) or item.get("mint_session") is not True:
            continue
        window = _placement_window(item)
        item_id = str(item.get("id") or item.get("name") or "")
        if window is None or not item_id:
            continue
        identity = _mint_source_identity(item, item_id, window)
        candidate = (item, window, item_id, identity)
        grouped.setdefault(identity, []).append(candidate)

    canonical = [
        min(candidates, key=_mint_candidate_sort_key)
        for candidates in grouped.values()
    ]
    return sorted(canonical, key=_mint_candidate_sort_key)


def placement_window_rows(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build immutable sequence rows for items with an explicit window.

    Selected Mint sessions are user choices, not work for the judgment model
    to rearrange.  Returning their exact rows here keeps the model focused on
    movable work while preserving the normal assigned-item validation path.
    """
    rows: list[dict[str, Any]] = []
    for item in items:
        if isinstance(item, dict) and item.get("mint_session") is True:
            continue
        window = _placement_window(item)
        item_id = str(item.get("id") or item.get("name") or "")
        if window is None or not item_id:
            continue
        row: dict[str, Any] = {
            "id": item_id,
            "start": f"{window[0] // 60:02d}:{window[0] % 60:02d}",
            "end": f"{window[1] // 60:02d}:{window[1] % 60:02d}",
            "zone": item.get("zone"),
        }
        # Preserve the schedulable source metadata for the frontend and the
        # write-preview routing, without copying the whole prompt item into a
        # sequence row.
        for key in ("source", "mint_session", "mint_session_id", "calendar_class"):
            if key in item:
                row[key] = item[key]
        rows.append(row)

    # Selected Mint rows are source-backed choices. Do not let a stale UI
    # selection order or a repeated source row decide their final order.
    for item, window, item_id, _identity in _canonical_mint_candidates(items):
        row = {
            "id": item_id,
            "start": f"{window[0] // 60:02d}:{window[0] % 60:02d}",
            "end": f"{window[1] // 60:02d}:{window[1] % 60:02d}",
            "zone": item.get("zone"),
        }
        for key in ("source", "mint_session", "mint_session_id", "calendar_class"):
            if key in item:
                row[key] = item[key]
        rows.append(row)
    return rows


def selected_mint_walls(items: list[dict[str, Any]]) -> list[tuple[str, tuple[int, int]]]:
    """Selected Mint session intervals as hard placement walls.

    FEEDBACK-25: a row whose item carries ``mint_session`` plus a placement
    window is a user-selected Mint reservation. It is immutable (the exact
    row is merged after judgment) AND a HARD wall — no other row may overlap
    it, in any phase. Returns ``[(item_id, (start_min, end_min)), ...]``.

    CP-T29: the same Mint item can appear in BOTH ``optional_items`` and
    ``assigned`` (the schedulable row is injected into the assigned set while
    also flowing through optional_items). Identical (item id, interval)
    duplicates are collapsed, first occurrence wins, so one overlapping row
    yields exactly one hard error. The Mint row itself stays exempt.
    """
    return [
        (item_id, window)
        for _item, window, item_id, _identity in _canonical_mint_candidates(items)
    ]


def merge_immutable_rows(
    proposed_rows: list[dict[str, Any]], immutable_rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Replace model copies of immutable rows and return chronological output."""
    immutable_ids = {str(row.get("id")) for row in immutable_rows}
    merged = [
        row for row in proposed_rows
        if str(row.get("id")) not in immutable_ids
    ]
    merged.extend(immutable_rows)
    def sort_key(row: dict[str, Any]) -> tuple[int, str]:
        start = placement_rules.time_to_minutes(row.get("start"))
        return (start if start is not None else 24 * 60 + 1,
                str(row.get("id") or ""))

    return sorted(merged, key=sort_key)


def merge_pinned_rows(
    proposed_rows: list[dict[str, Any]], pinned_rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Replace any model copy with the caller's exact immutable pin objects."""
    return merge_immutable_rows(proposed_rows, pinned_rows)


def _identity_key(value: Any) -> str:
    """Normalize display names for a conservative model-id reconciliation."""
    text = unicodedata.normalize("NFKC", str(value)).casefold()
    return " ".join(
        "".join(char if char.isalnum() else " " for char in text).split()
    )


def canonicalize_sequence_ids(
    proposal: dict[str, Any], canonical_items: list[dict[str, Any]]
) -> dict[str, Any]:
    """Restore canonical item IDs when the model only changes decoration.

    Sequence identity is name-keyed, but models occasionally omit a leading
    emoji from a task title.  Exact IDs always win; a decorated fallback is
    applied only when the normalized display name maps to one canonical ID.
    Ambiguous names are left untouched so validation still fails loudly.
    """
    sequence = proposal.get("sequence")
    if not isinstance(sequence, list):
        return proposal

    exact: dict[str, str] = {}
    normalized: dict[str, set[str]] = {}
    for item in canonical_items:
        item_id = str(item.get("id") or item.get("name") or "")
        if not item_id:
            continue
        exact[item_id] = item_id
        normalized.setdefault(_identity_key(item_id), set()).add(item_id)

    rows: list[dict[str, Any]] = []
    for row in sequence:
        if not isinstance(row, dict):
            rows.append(row)
            continue
        row_id = str(row.get("id") or "")
        if row_id in exact:
            rows.append(row)
            continue
        matches = normalized.get(_identity_key(row_id), set())
        if len(matches) == 1:
            rows.append({**row, "id": next(iter(matches))})
        else:
            rows.append(row)
    return {**proposal, "sequence": rows}


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def validate_sequence(
    proposal: dict[str, Any],
    assigned: list[dict[str, Any]],
    anchored_blocks: list[dict[str, Any]],
    config: dict[str, Any],
    time_frame: dict[str, Any] | None = None,
    optional_ids: set[str] | None = None,
    optional_items: list[dict[str, Any]] | None = None,
    planning_config_fingerprint: str = "",
) -> ValidationResult:
    """Re-validate an already-schema-valid SequenceProposal against real data.

    Returns a ValidationResult: ``ok=False`` iff any HARD rule is violated
    (morning-workout ban, structural invariants). Zone/latest_start violations
    are always collected as warnings and never affect ``ok``.

    ui-parity T7 / revised 2026-07-21: when ``time_frame`` ({anchor,
    effective_eod}) is supplied, a row starting before the anchor is a soft
    ``placement_past`` warning (demoted from HARD per Adam's T14 directive —
    the plan always comes back; LD24 acceptance covers it); a row ending past
    the effective EOD is a soft ``⚠ past EOD`` warning (never-bump places it
    anyway). ``backdrop: true`` rows (🟡 Trinoor zone framing, Step
    D′) are exempt from validation entirely — permeable visuals, not placements.
    """
    hard_errors: list[str] = []
    warnings: list[dict[str, str]] = []
    diagnostics: list[dict[str, Any]] = []

    def interval_for(row_id: str, start: Any, end: Any) -> dict[str, str] | None:
        canonical_start = placement_rules.canonical_time(start)
        canonical_end = placement_rules.canonical_time(end)
        if canonical_start is None or canonical_end is None:
            return None
        return {"id": row_id, "start": canonical_start, "end": canonical_end}

    def span_interval(block_id: str, span: tuple[int, int]) -> dict[str, str]:
        return {
            "id": block_id,
            "start": f"{span[0] // 60:02d}:{span[0] % 60:02d}",
            "end": f"{span[1] // 60:02d}:{span[1] % 60:02d}",
        }

    def add_diagnostic(
        rule: str,
        severity: str,
        detail: str,
        affected_rows: list[str] | None = None,
        intervals: list[dict[str, str] | None] | None = None,
    ) -> None:
        diagnostics.append({
            "rule": rule,
            "severity": severity,
            "affected_rows": affected_rows or [],
            "intervals": [interval for interval in (intervals or [])
                          if interval is not None],
            "detail": detail,
        })

    def add_hard(
        detail: str,
        *,
        rule: str = "validation",
        affected_rows: list[str] | None = None,
        intervals: list[dict[str, str] | None] | None = None,
    ) -> None:
        hard_errors.append(detail)
        add_diagnostic(rule, "error", detail, affected_rows, intervals)

    def add_warning(
        warning: dict[str, str],
        *,
        rule: str | None = None,
        affected_rows: list[str] | None = None,
        intervals: list[dict[str, str] | None] | None = None,
    ) -> None:
        warnings.append(warning)
        add_diagnostic(
            rule or str(warning.get("rule") or warning.get("kind") or "warning"),
            "warning",
            str(warning.get("detail") or ""),
            affected_rows,
            intervals,
        )

    sequence = proposal.get("sequence")
    if not isinstance(sequence, list):
        detail = "SequenceProposal.sequence: expected a list"
        return ValidationResult(
            ok=False,
            hard_errors=[detail],
            diagnostics=[{
                "rule": "invalid_sequence",
                "severity": "error",
                "affected_rows": [],
                "intervals": [],
                "detail": detail,
            }],
        )
    normalized_sequence: list[dict[str, Any]] = []
    for raw_row in sequence:
        if isinstance(raw_row, dict) and raw_row.get("backdrop"):
            continue
        if not isinstance(raw_row, dict):
            normalized_sequence.append(raw_row)
            continue
        normalized_row = dict(raw_row)
        for endpoint in ("start", "end"):
            value = placement_rules.canonical_time(raw_row.get(endpoint))
            if value is not None:
                normalized_row[endpoint] = value
        normalized_sequence.append(normalized_row)
    sequence = normalized_sequence
    overlap_grants = canonicalize_overlap_grants(proposal.get("overlap_grants") or [])

    def _exact_grant(
        row: dict[str, Any], block_name: str, block_span: tuple[int, int]
    ) -> dict[str, Any] | None:
        companion_interval = {
            "start": f"{block_span[0] // 60:02d}:{block_span[0] % 60:02d}",
            "end": f"{block_span[1] // 60:02d}:{block_span[1] % 60:02d}",
        }
        primary_interval = {"start": row["start"], "end": row["end"]}
        for grant in overlap_grants:
            if not isinstance(grant, dict):
                continue
            if (
                grant.get("primary_id") == str(row.get("id"))
                and grant.get("companion_id") == block_name
                and grant.get("primary_interval") == primary_interval
                and grant.get("companion_interval") == companion_interval
                and grant.get("planning_config_fingerprint")
                    == planning_config_fingerprint
            ):
                return grant
        return None

    assigned_by_id = {str(a.get("id")): a for a in assigned}
    item_by_id = {
        **{str(item.get("id")): item for item in (optional_items or [])},
        **assigned_by_id,
    }
    press_exception_ids = _press_before_work_ids(config)

    # -- structural: HH:MM validity, end>start, chronological order ---------
    seen_ids: list[str] = []
    prev_start_min: int | None = None
    rows: list[dict[str, Any]] = []
    frame_anchor = _normalize_time(time_frame.get("anchor")) if time_frame else None
    frame_eod = _normalize_time(time_frame.get("effective_eod")) if time_frame else None
    frame_anchor_min = _to_minutes(frame_anchor) if frame_anchor else None
    frame_eod_min = _to_minutes(frame_eod) if frame_eod else None
    for row in sequence:
        if not isinstance(row, dict):
            detail = f"{row!r}: sequence row must be an object"
            add_hard(detail, rule="invalid_sequence_row")
            continue
        row_id = str(row.get("id"))
        raw_start, raw_end = row.get("start"), row.get("end")
        start = placement_rules.canonical_time(raw_start)
        end = placement_rules.canonical_time(raw_end)

        if start is None:
            detail = f"{row_id!r}: start {raw_start!r} is not valid HH:MM"
            add_hard(detail, rule="invalid_time", affected_rows=[row_id])
            continue
        if end is None:
            detail = f"{row_id!r}: end {raw_end!r} is not valid HH:MM"
            add_hard(detail, rule="invalid_time", affected_rows=[row_id])
            continue
        start_min = _to_minutes(start)
        end_min = _to_minutes(end)
        normalized_row = {**row, "start": start, "end": end}
        if end_min <= start_min:
            detail = f"{row_id!r}: end {end!r} not after start {start!r}"
            add_hard(
                detail,
                rule="non_positive_interval",
                affected_rows=[row_id],
                intervals=[interval_for(row_id, start, end)],
            )
            continue

        if prev_start_min is not None and start_min < prev_start_min:
            detail = (
                f"sequence not in chronological start order at {row_id!r} "
                f"({start!r} < preceding "
                f"{prev_start_min // 60:02d}:{prev_start_min % 60:02d})"
            )
            add_hard(
                detail,
                rule="chronological_order",
                affected_rows=[row_id],
                intervals=[interval_for(row_id, start, end)],
            )
        prev_start_min = start_min

        if frame_anchor_min is not None and start_min < frame_anchor_min:
            # Demoted HARD -> soft 2026-07-21 (Adam, T14 run): the plan
            # must always come back; past placement renders as an LD24
            # acceptable defect the user accepts or drags forward.
            warning = {
                "id": row_id, "rule": "placement_past",
                "detail": (
                    f"⚠ starts {start} before anchor {frame_anchor} (in the past)"
                ),
            }
            add_warning(
                warning,
                affected_rows=[row_id],
                intervals=[interval_for(row_id, start, end)],
            )
        if frame_eod_min is not None and end_min > frame_eod_min:
            warning = {
                "id": row_id, "rule": "past_eod",
                "detail": (
                    f"⚠ past EOD — ends {end}, effective EOD {frame_eod}"
                ),
            }
            add_warning(
                warning,
                affected_rows=[row_id],
                intervals=[interval_for(row_id, start, end)],
            )

        seen_ids.append(row_id)
        rows.append(normalized_row)

    # duplicates
    dupes = {i for i in seen_ids if seen_ids.count(i) > 1}
    for d in dupes:
        add_hard(
            f"{d!r}: appears more than once in sequence",
            rule="duplicate_sequence_row",
            affected_rows=[d],
        )

    # never-bump: every assigned item present exactly once
    missing = [aid for aid in assigned_by_id if aid not in seen_ids]
    for m in missing:
        add_hard(
            f"assigned item {m!r} missing from sequence (never-bump violated)",
            rule="never_bump",
            affected_rows=[m],
        )

    # extras not in assigned (and not an anchored block passthrough or an
    # optional id — e.g. QT-absorbed items a manual layout places directly)
    anchored_ids = {str(b.get("Block")) for b in anchored_blocks}
    # /sequence and /validate-sequence append synthetic pinned walls to this
    # list. They are immutable movable rows, not source anchored passthroughs,
    # so keep a separate identity set for exemptions below.
    source_anchored_ids = {
        str(b.get("Block"))
        for b in anchored_blocks
        if b.get("pinned") is not True
    }
    allowed = anchored_ids | (optional_ids or set())
    extras = [i for i in set(seen_ids) if i not in assigned_by_id and i not in allowed]
    for e in extras:
        add_hard(
            f"{e!r}: not present in assigned items or anchored blocks",
            rule="extra_sequence_row",
            affected_rows=[e],
        )

    # Explicit schedulable windows are user-selected placement bounds. They
    # are optional rows, so they do not participate in never-bump, but a row
    # that is emitted must not extend past its selected window's END. Frozen
    # contract 14 (capture): selected Mint sessions admit the associated
    # assignment BEFORE the session start — a row may lead into the window —
    # so only the upper bound is enforced, never the lower one.
    for row in rows:
        window = _placement_window(item_by_id.get(str(row.get("id"))))
        if window is None:
            continue
        row_end = _to_minutes(row["end"])
        if row_end > window[1]:
            detail = (
                f"{row['id']!r}: placed {row['start']}-{row['end']} outside "
                "its selected Mint session window "
                f"{window[0] // 60:02d}:{window[0] % 60:02d}-"
                f"{window[1] // 60:02d}:{window[1] % 60:02d}"
            )
            add_hard(
                detail,
                rule="selected_mint_window",
                affected_rows=[str(row["id"])],
                intervals=[interval_for(str(row["id"]), row["start"], row["end"])],
            )

    # -- overlap with non-permeable anchored blocks --------------------------
    # ISS-6: a ``Type: window`` block's [Start, End] is a placement WINDOW (its
    # Duration-sized footprint floats somewhere inside), NOT a solid wall — per
    # the skill SOT, "don't block windows; flag overlaps". So window blocks are
    # permeable to the sequence: a task inside the window is a SOFT flag, while
    # only hard/duration-only non-permeable blocks stay HARD walls.
    #
    # FEEDBACK-02 (2026-08-13, FF-CAL-03): imported calendar events are
    # hardened. A non-permeable calendar block (capacity fixed or work) is a
    # HARD wall — overlap rejects the proposal deterministically, never a soft
    # acceptable defect. Ignored/quarantined calendars are excluded from
    # planning (frozen contract 17) and are no wall at all. Config-backed
    # anchored blocks keep the LD26 acceptable-defect reading below.
    hard_spans: list[tuple[str, tuple[int, int]]] = []
    calendar_spans: list[tuple[str, tuple[int, int]]] = []
    window_spans: list[tuple[str, tuple[int, int]]] = []
    for b in anchored_blocks:
        if b.get("overlap_allowed"):
            continue
        span = _anchored_span(b)
        if not span:
            continue
        entry = (str(b.get("Block")), span)
        is_window = str(b.get("Type", "")).strip().lower() == "window"
        if is_window:
            window_spans.append(entry)
            continue
        if str(b.get("source", "")).casefold() == "calendar":
            klass = str(b.get("capacity_class") or "fixed").strip().casefold()
            if klass in _CALENDAR_EXCLUDED_CLASSES:
                continue  # contract 17: excluded from planning → no wall
            calendar_spans.append(entry)
        else:
            hard_spans.append(entry)

    # count window overlaps once per block (a full window would else flag most of
    # the day) — one concise soft advisory, not per-row amber noise.
    window_overlap_counts: dict[str, int] = {}

    # G16 overflow contract (2026-07-14): over-capacity must degrade, never 422.
    # A row starting at/after the effective EOD sits in the overflow tail — its
    # anchored-block overlap is a SOFT flag the user resolves on the timeline;
    # before the effective EOD, an overlap stays a HARD wall.
    eod_min = frame_eod_min

    for row in rows:
        row_id = str(row.get("id"))
        if row_id in anchored_ids:
            continue  # anchored blocks don't overlap-check against themselves
        r_start, r_end = _to_minutes(row["start"]), _to_minutes(row["end"])
        # FEEDBACK-02: non-permeable calendar walls reject overlaps hard. The
        # ONLY escape is an approved exact overlap grant for this exact pair
        # and interval; overflow-tail placement is NOT an escape for a
        # calendar wall (explicit infeasibility is the contract).
        for block_name, (b_start, b_end) in calendar_spans:
            if r_start < b_end and b_start < r_end:
                desc = (
                    f"{row_id!r} ({row['start']}-{row['end']}) overlaps non-permeable "
                    f"calendar block {block_name!r} ({b_start // 60:02d}:{b_start % 60:02d}-"
                    f"{b_end // 60:02d}:{b_end % 60:02d})"
                )
                grant = _exact_grant(row, block_name, (b_start, b_end))
                if grant is not None:
                    add_warning(
                        {
                            "id": row_id, "rule": "allowed_overlap",
                            "detail": f"Allowed overlap with {block_name!r}: "
                                      f"{grant.get('reason') or 'explicit grant'}",
                        },
                        affected_rows=[row_id, block_name],
                        intervals=[
                            interval_for(row_id, row["start"], row["end"]),
                            span_interval(block_name, (b_start, b_end)),
                        ],
                    )
                else:
                    add_hard(
                        desc,
                        rule="calendar_overlap",
                        affected_rows=[row_id, block_name],
                        intervals=[
                            interval_for(row_id, row["start"], row["end"]),
                            span_interval(block_name, (b_start, b_end)),
                        ],
                    )
        in_overflow_tail = eod_min is not None and r_start >= eod_min
        for block_name, (b_start, b_end) in hard_spans:
            if r_start < b_end and b_start < r_end:
                desc = (
                    f"{row_id!r} ({row['start']}-{row['end']}) overlaps non-permeable "
                    f"anchored block {block_name!r} ({b_start // 60:02d}:{b_start % 60:02d}-"
                    f"{b_end // 60:02d}:{b_end % 60:02d})"
                )
                grant = _exact_grant(row, block_name, (b_start, b_end))
                if grant is not None:
                    add_warning(
                        {
                            "id": row_id, "rule": "allowed_overlap",
                            "detail": f"Allowed overlap with {block_name!r}: "
                                      f"{grant.get('reason') or 'explicit grant'}",
                        },
                        affected_rows=[row_id, block_name],
                        intervals=[
                            interval_for(row_id, row["start"], row["end"]),
                            span_interval(block_name, (b_start, b_end)),
                        ],
                    )
                elif in_overflow_tail:
                    add_warning(
                        {
                            "id": row_id, "rule": "overflow_overlap",
                            "detail": f"⚠ overflow — {desc}",
                        },
                        affected_rows=[row_id, block_name],
                        intervals=[
                            interval_for(row_id, row["start"], row["end"]),
                            span_interval(block_name, (b_start, b_end)),
                        ],
                    )
                else:
                    # T18d/locked decision 26: unexpected wall overlaps are
                    # acceptable defects, never silent and never hard-safety
                    # overrides. The user may accept them explicitly later.
                    add_warning(
                        {"id": row_id, "rule": "unexpected_overlap", "detail": desc},
                        affected_rows=[row_id, block_name],
                        intervals=[
                            interval_for(row_id, row["start"], row["end"]),
                            span_interval(block_name, (b_start, b_end)),
                        ],
                    )
        for block_name, (b_start, b_end) in window_spans:
            if r_start < b_end and b_start < r_end:
                window_overlap_counts[block_name] = window_overlap_counts.get(block_name, 0) + 1

    # -- FEEDBACK-25: selected Mint sessions are HARD walls ------------------
    # Mint reservations are protected time: an assigned row that overlaps a
    # selected Mint interval is a HARD rejection, never a soft acceptable
    # defect, and no overlap grant can permit it (over-assignment is explicit
    # infeasibility, not a wall breach). The Mint row itself is the wall, so
    # its own id is exempt; a row ending exactly at the interval's start or
    # beginning exactly at its end touches, it does not overlap.
    mint_walls = selected_mint_walls(list(optional_items or []) + list(assigned))
    mint_wall_ids = {wall_id for wall_id, _ in mint_walls}
    for row in rows:
        row_id = str(row.get("id"))
        # Anchored passthrough rows are source walls/windows, not movable work.
        # Their own permeability/calendar rules are handled above (and the
        # /sequence route performs stale Mint-vs-fixed preflight), so do not
        # turn a permissible anchored window such as Live into a Mint breach.
        if row_id in mint_wall_ids or row_id in source_anchored_ids:
            continue
        r_start, r_end = _to_minutes(row["start"]), _to_minutes(row["end"])
        for wall_id, (w_start, w_end) in mint_walls:
            if r_start < w_end and w_start < r_end:
                detail = (
                    f"{row_id!r} ({row['start']}-{row['end']}) overlaps selected "
                    f"Mint session {wall_id!r} ({w_start // 60:02d}:{w_start % 60:02d}-"
                    f"{w_end // 60:02d}:{w_end % 60:02d})"
                )
                add_hard(
                    detail,
                    rule="mint_overlap",
                    affected_rows=[row_id, wall_id],
                    intervals=[
                        interval_for(row_id, row["start"], row["end"]),
                        span_interval(wall_id, (w_start, w_end)),
                    ],
                )

    for block_name, n in window_overlap_counts.items():
        add_warning({
            "id": block_name,
            "kind": "window-overlap",
            "detail": f"{n} task(s) scheduled within the {block_name!r} window "
                      f"— place its floating block in a free gap",
        }, rule="window-overlap", affected_rows=[block_name])

    # -- HARD: morning workout ban (before 12:00), Press before_work excepted --
    for row in rows:
        row_id = str(row.get("id"))
        item = item_by_id.get(row_id, {"id": row_id, "zone": row.get("zone", "")})
        # fold in the proposal row's own zone/id for detection robustness
        probe = {"id": row_id, "zone": row.get("zone") or item.get("zone", ""), "type": item.get("type", "")}
        if is_workout_item(probe) or is_workout_item(item):
            if _to_minutes(row["start"]) < _to_minutes("12:00") and row_id not in press_exception_ids:
                detail = (
                    f"{row_id!r}: workout block placed at {row['start']} — before noon is "
                    f"forbidden except a Press before_work exception"
                )
                add_hard(
                    detail,
                    rule="morning_workout",
                    affected_rows=[row_id],
                    intervals=[interval_for(row_id, row["start"], row["end"])],
                )

    # -- SOFT: zone compatibility --------------------------------------------
    for row in rows:
        row_id = str(row.get("id"))
        item = item_by_id.get(row_id, {})
        zone = item.get("zone") or row.get("zone") or "any"
        window = _zone_window(str(zone), config)
        if window is not None and not _in_window(row["start"], window):
            add_warning(
                {
                    "kind": "zone_violation",
                    "id": row_id,
                    "detail": f"placed at {row['start']}, outside {zone} window "
                    f"{window[0]}-{window[1]}",
                },
                affected_rows=[row_id],
                intervals=[interval_for(row_id, row["start"], row["end"])],
            )

    semantic_constraints = placement_rules.derive_constraints(
        assigned, anchored_blocks
    )
    for detail in placement_rules.validate_constraints(
        {"sequence": rows, "overlap_grants": overlap_grants},
        semantic_constraints,
        planning_config_fingerprint=planning_config_fingerprint,
    ):
        add_hard(detail, rule="semantic_constraint")

    # -- SOFT: latest_start ----------------------------------------------------
    for row in rows:
        row_id = str(row.get("id"))
        item = item_by_id.get(row_id, {})
        latest_start = item.get("latest_start")
        latest_start = _normalize_time(latest_start)
        if (latest_start is not None
                and _to_minutes(row["start"]) > _to_minutes(latest_start)):
            add_warning(
                {
                    "kind": "latest_start_violation",
                    "id": row_id,
                    "detail": f"started at {row['start']}, after latest_start {latest_start}",
                },
                affected_rows=[row_id],
                intervals=[interval_for(row_id, row["start"], row["end"])],
            )

    return ValidationResult(
        ok=not hard_errors,
        hard_errors=hard_errors,
        warnings=warnings,
        diagnostics=diagnostics,
    )
