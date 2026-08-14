"""day_semantics.py — T18a: pure config projection for preset-driven zone semantics.

Parses `## Day Presets`, Template zones, allotment defaults, and the complete
raw `## Overlap Permissions` section from the parsed config sections dict
(produced by `config_reader.parse_config_markdown`). Pure — no I/O, no live
calls, no billed endpoints.

Contract (locked decisions 30–40, T18a):
- `## Day Presets` rows define named presets with exact `Days` tokens
  (daily/workdays/weekends/explicit day list), enabled Template zone names,
  and optional integer-minute work allotment divisible by 15.
- Resolution: explicit dated override -> unique matching row -> configured
  default with a visible warning. Ambiguous matches (two rows fit the same
  date) fall back with a warning.
- `Defaults.work_allotment_minutes` is the fallback when a matched/default
  row omits its own allotment (None).
- `0` explicitly disables Mint capacity and Mint zones.
- Overlap Permissions prose is preserved verbatim from the raw config text.
- Existing task `## Presets` contract is NOT consumed or modified.
- Malformed definitions (duplicate names, unknown days, missing required
  fields, non-15-min allotments) are rejected deterministically into
  `errors`; projection never raises.

Gate: TDD — tests/test_day_semantics.py.
"""
from __future__ import annotations

import datetime
import hashlib
import json
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Canonical constants
# ---------------------------------------------------------------------------

_DAYS_TOKENS: dict[str, frozenset[str]] = {
    "daily": frozenset({"mon", "tue", "wed", "thu", "fri", "sat", "sun"}),
    "workdays": frozenset({"mon", "tue", "wed", "thu", "fri"}),
    "weekends": frozenset({"sat", "sun"}),
}

_DAY_ABBREVS: dict[str, str] = {
    "mon": "mon", "tue": "tue", "wed": "wed", "thu": "thu",
    "fri": "fri", "sat": "sat", "sun": "sun",
    "monday": "mon", "tuesday": "tue", "wednesday": "wed",
    "thursday": "thu", "friday": "fri", "saturday": "sat", "sunday": "sun",
}

_WEEKDAY_TO_ABBREV = {
    0: "mon", 1: "tue", 2: "wed", 3: "thu",
    4: "fri", 5: "sat", 6: "sun",
}

_DEFAULT_ALLOTMENT = 0

_TRUTHY_DEFAULT_TOKENS: frozenset[str] = frozenset({"true", "yes", "1", "✓", "x"})
_FALSEY_DEFAULT_TOKENS: frozenset[str] = frozenset({"", "false", "no", "0", "—", "-", "–", "none", "n/a"})


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DayPreset:
    name: str
    days: frozenset[str]
    enabled_zones: list[str]
    work_allotment_minutes: int | None  # None = inherit default


@dataclass(frozen=True)
class ZoneSpec:
    name: str
    intervals: list[tuple[str, str]]  # [(start, end), ...]


@dataclass
class ResolvedDay:
    preset: DayPreset | None
    resolution_source: str  # "dated_override" | "matched_row" | "configured_default" | "fallback"
    warnings: list[str]
    enabled_zones: list[ZoneSpec]
    work_allotment_minutes: int
    mint_enabled: bool
    overlap_permissions_raw: str


@dataclass
class DaySemanticsProjection:
    presets: list[DayPreset]
    configured_default: str | None
    zones: dict[str, ZoneSpec]
    default_allotment_minutes: int
    overlap_permissions_raw: str
    errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def _parse_days_token(raw: str) -> frozenset[str] | None:
    """Parse a Days cell into a frozenset of weekday abbreviations.
    Returns None for unknown/malformed tokens."""
    s = (raw or "").strip().lower()
    if not s:
        return None
    if s in _DAYS_TOKENS:
        return _DAYS_TOKENS[s]
    # Explicit day list: "Mon, Wed, Fri" or "Mon – Fri"
    # Handle en-dash ranges like "Mon – Thu"
    s = s.replace("–", "-").replace("—", "-")
    if "-" in s and "," not in s:
        # Range like "Mon - Thu"
        parts = [p.strip() for p in s.split("-") if p.strip()]
        if len(parts) == 2:
            start = _DAY_ABBREVS.get(parts[0])
            end = _DAY_ABBREVS.get(parts[1])
            if start and end:
                order = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
                si = order.index(start)
                ei = order.index(end)
                if si <= ei:
                    return frozenset(order[si:ei + 1])
        return None
    # Comma-separated list
    tokens = [t.strip() for t in s.split(",") if t.strip()]
    out: set[str] = set()
    for t in tokens:
        abbrev = _DAY_ABBREVS.get(t)
        if not abbrev:
            return None
        out.add(abbrev)
    return frozenset(out) if out else None


def _parse_zones_list(raw) -> list[str]:
    """Parse a Zones cell into a list of zone names."""
    if raw is None:
        return []
    s = str(raw).strip()
    if not s or s in ("—", "-", "–"):
        return []
    return [z.strip() for z in s.split(",") if z.strip()]


def _parse_allotment(raw) -> int | None:
    """Parse a work-allotment cell into integer minutes or None.
    None means "inherit default". Returns -1 for invalid (caller rejects)."""
    if raw is None:
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float):
        if raw != int(raw):
            return -1
        return int(raw)
    s = str(raw).strip()
    if not s or s in ("—", "-", "–"):
        return None
    try:
        return int(s)
    except ValueError:
        return -1


def _validate_allotment(minutes: int) -> bool:
    """True if the allotment is a valid canonical integer (>=0, divisible by 15)."""
    return minutes >= 0 and minutes % 15 == 0


def _parse_default_flag(raw) -> bool | None:
    """Parse a `Default` column cell into True/False/None.

    Returns True for truthy tokens, False for falsey tokens, None for unknown
    (caller rejects as malformed)."""
    if raw is None:
        return False
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)):
        return bool(raw)
    s = str(raw).strip().lower()
    if s in _TRUTHY_DEFAULT_TOKENS:
        return True
    if s in _FALSEY_DEFAULT_TOKENS:
        return False
    return None


# ---------------------------------------------------------------------------
# Zone extraction
# ---------------------------------------------------------------------------

def _extract_zones(template_blocks: dict) -> dict[str, ZoneSpec]:
    """Extract ZoneSpecs from the parsed `## Template Blocks` section.

    Each subsection becomes a ZoneSpec. Intervals are extracted from rows
    with either `Start`/`End` columns (Trinoor Hours shape) or `Hours`
    columns containing a time range (Press (Gym) shape).
    """
    zones: dict[str, ZoneSpec] = {}
    if not isinstance(template_blocks, dict):
        return zones
    for name, body in template_blocks.items():
        if name == "_body":
            continue
        intervals: list[tuple[str, str]] = []
        if isinstance(body, list):
            for row in body:
                if not isinstance(row, dict):
                    continue
                start = row.get("Start")
                end = row.get("End")
                if start and end:
                    intervals.append((str(start).strip(), str(end).strip()))
                    continue
                hours = row.get("Hours")
                if hours and isinstance(hours, str):
                    parsed = _parse_hours_range(hours)
                    if parsed:
                        intervals.append(parsed)
        if intervals or name in template_blocks:
            zones[name] = ZoneSpec(name=name, intervals=intervals)
    return zones


def _parse_hours_range(raw: str) -> tuple[str, str] | None:
    """Parse a `Hours` cell like '5:00 AM – 10:00 PM' into (start, end)."""
    s = raw.strip()
    for sep in (" – ", " - ", " — ", "–", "-", "—"):
        if sep in s:
            parts = [p.strip() for p in s.split(sep, 1) if p.strip()]
            if len(parts) == 2:
                return (parts[0], parts[1])
    return None


# ---------------------------------------------------------------------------
# Overlap Permissions raw text extraction
# ---------------------------------------------------------------------------

def _extract_overlap_raw(raw_config_text: str) -> str:
    """Extract the `## Overlap Permissions` section verbatim from raw markdown.
    Returns the section heading + body, stripped. Empty string if absent."""
    if not raw_config_text:
        return ""
    lines = raw_config_text.splitlines()
    start_idx = None
    end_idx = len(lines)
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped == "## Overlap Permissions":
            start_idx = i
            continue
        if start_idx is not None and stripped.startswith("## ") and i > start_idx:
            end_idx = i
            break
    if start_idx is None:
        return ""
    return "\n".join(lines[start_idx:end_idx]).strip()


# ---------------------------------------------------------------------------
# Projection
# ---------------------------------------------------------------------------

def project_day_semantics(
    sections: dict[str, object],
    raw_config_text: str = "",
) -> DaySemanticsProjection:
    """Project the day-semantics contract from parsed config sections.

    ``sections`` is the dict from ``config_reader.parse_config_markdown``.
    ``raw_config_text`` is the full raw markdown text (for verbatim Overlap
    Permissions extraction). The configured-default preset is derived from
    exactly one truthy ``Default`` column on a Day Presets row; callers must
    not inject a default.

    Never raises — malformed input goes into ``errors``.
    """
    errors: list[str] = []
    presets: list[DayPreset] = []
    seen_names: set[str] = set()
    default_rows: list[str] = []  # names of rows marked Default truthy

    # Allotment default from Defaults.work_allotment_minutes (validated)
    defaults = sections.get("Defaults")
    default_allotment = _DEFAULT_ALLOTMENT
    if isinstance(defaults, dict):
        raw_allot = defaults.get("work_allotment_minutes")
        if raw_allot is not None:
            parsed_allot = _parse_allotment(raw_allot)
            if parsed_allot == -1 or not _validate_allotment(parsed_allot):
                errors.append(
                    f"Defaults.work_allotment_minutes invalid: {raw_allot!r} "
                    f"(must be a nonnegative integer divisible by 15)"
                )
            else:
                default_allotment = parsed_allot

    # Day Presets
    day_presets_raw = sections.get("Day Presets")
    section_present = isinstance(day_presets_raw, list)
    if section_present:
        for i, row in enumerate(day_presets_raw):
            if not isinstance(row, dict):
                errors.append(f"Day Presets row {i}: not a dict")
                continue
            name = row.get("Name")
            if not name or not str(name).strip():
                errors.append(f"Day Presets row {i}: missing Name")
                continue
            name_str = str(name).strip()
            if name_str in seen_names:
                errors.append(f"Day Presets: duplicate name '{name_str}'")
                continue
            seen_names.add(name_str)

            days_raw = row.get("Days")
            if not days_raw:
                errors.append(f"Day Presets '{name_str}': missing Days")
                continue
            days = _parse_days_token(str(days_raw))
            if days is None:
                errors.append(f"Day Presets '{name_str}': unknown Days token '{days_raw}'")
                continue

            zones_list = _parse_zones_list(row.get("Zones"))
            allotment = _parse_allotment(row.get("Work Allotment (min)"))
            if allotment == -1:
                errors.append(f"Day Presets '{name_str}': invalid work allotment '{row.get('Work Allotment (min)')}'")
                continue
            if allotment is not None and not _validate_allotment(allotment):
                errors.append(
                    f"Day Presets '{name_str}': work allotment {allotment} not divisible by 15"
                )
                continue

            default_flag = _parse_default_flag(row.get("Default"))
            if default_flag is None:
                errors.append(
                    f"Day Presets '{name_str}': unknown Default value '{row.get('Default')}'"
                )
                continue
            if default_flag:
                default_rows.append(name_str)

            presets.append(DayPreset(
                name=name_str,
                days=days,
                enabled_zones=zones_list,
                work_allotment_minutes=allotment,
            ))

        # Exactly one truthy Default row when section is present
        if len(default_rows) == 0:
            errors.append(
                "Day Presets: no row marked Default; exactly one row must have "
                "a truthy Default column (true/yes/1/✓/x)"
            )
        elif len(default_rows) > 1:
            errors.append(
                f"Day Presets: multiple rows marked Default: {default_rows}; "
                f"exactly one row may have a truthy Default column"
            )
        configured_default = default_rows[0] if len(default_rows) == 1 else None

    # Template zones
    template_blocks = sections.get("Template Blocks")
    zones = _extract_zones(template_blocks if isinstance(template_blocks, dict) else {})

    # Overlap Permissions raw
    overlap_raw = _extract_overlap_raw(raw_config_text)

    return DaySemanticsProjection(
        presets=presets,
        configured_default=configured_default if section_present else None,
        zones=zones,
        default_allotment_minutes=default_allotment,
        overlap_permissions_raw=overlap_raw,
        errors=errors,
    )


# ---------------------------------------------------------------------------
# Day resolution
# ---------------------------------------------------------------------------

def resolve_day(
    projection: DaySemanticsProjection,
    date: datetime.date,
    *,
    dated_override: str | None = None,
) -> ResolvedDay:
    """Resolve the effective day preset for a given date.

    Resolution order: explicit dated override -> unique matching row ->
    configured default with warning -> fallback (no preset).

    Ambiguous matches (multiple rows fit) fall back with a warning.
    """
    warnings: list[str] = []
    preset: DayPreset | None = None
    source = "fallback"

    # 1. Explicit dated override
    if dated_override:
        matched = next((p for p in projection.presets if p.name == dated_override), None)
        if matched:
            preset = matched
            source = "dated_override"
        else:
            warnings.append(f"Dated override '{dated_override}' not found in Day Presets")

    # 2. Unique matching row (specificity-ordered: specific-day rows
    #    outrank generic `daily` rows so a Workday preset wins over a
    #    `daily` Default for Monday without an ambiguous-match warning)
    if preset is None:
        date_abbrev = _WEEKDAY_TO_ABBREV[date.weekday()]
        all_days = frozenset({"mon", "tue", "wed", "thu", "fri", "sat", "sun"})
        specific_matches = [
            p for p in projection.presets
            if date_abbrev in p.days and p.days != all_days
        ]
        generic_matches = [
            p for p in projection.presets
            if date_abbrev in p.days and p.days == all_days
        ]
        if len(specific_matches) == 1:
            preset = specific_matches[0]
            source = "matched_row"
        elif len(specific_matches) > 1:
            warnings.append(
                f"Ambiguous day preset match for {date.isoformat()} ({date_abbrev}): "
                f"{', '.join(p.name for p in specific_matches)}"
            )
        elif len(generic_matches) == 1:
            preset = generic_matches[0]
            source = "matched_row"
        elif len(generic_matches) > 1:
            warnings.append(
                f"Ambiguous day preset match for {date.isoformat()} ({date_abbrev}): "
                f"{', '.join(p.name for p in generic_matches)}"
            )

    # 3. Configured default with warning
    if preset is None and projection.configured_default:
        default = next((p for p in projection.presets if p.name == projection.configured_default), None)
        if default:
            preset = default
            source = "configured_default"
            warnings.append(
                f"No unique Day Preset match for {date.isoformat()}; "
                f"using configured default '{preset.name}'"
            )

    # 4. Fallback — no preset at all
    if preset is None:
        source = "fallback"
        if not warnings:
            warnings.append(f"No Day Preset match or default for {date.isoformat()}")

    # Resolve allotment
    if preset is not None and preset.work_allotment_minutes is not None:
        allotment = preset.work_allotment_minutes
    else:
        allotment = projection.default_allotment_minutes

    # Resolve enabled zones to ZoneSpecs
    enabled_zones: list[ZoneSpec] = []
    if preset is not None:
        for zname in preset.enabled_zones:
            zspec = projection.zones.get(zname)
            if zspec:
                enabled_zones.append(zspec)
            else:
                warnings.append(f"Zone '{zname}' referenced by preset '{preset.name}' not found in Template Blocks")

    mint_enabled = allotment > 0

    return ResolvedDay(
        preset=preset,
        resolution_source=source,
        warnings=warnings,
        enabled_zones=enabled_zones,
        work_allotment_minutes=allotment,
        mint_enabled=mint_enabled,
        overlap_permissions_raw=projection.overlap_permissions_raw,
    )


# ---------------------------------------------------------------------------
# T18b.3 — Pure resolved-contract helper (JSON-safe)
# ---------------------------------------------------------------------------

_WEEKDAY_ORDER = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def _preset_to_json_safe(p: DayPreset) -> dict[str, object]:
    """Project a DayPreset into a JSON-safe dict (weekday-ordered days for
    deterministic, human-readable output)."""
    return {
        "name": p.name,
        "days": [d for d in _WEEKDAY_ORDER if d in p.days],
        "enabled_zones": list(p.enabled_zones),
        "work_allotment_minutes": p.work_allotment_minutes,
    }


def _zone_to_json_safe(z: ZoneSpec) -> dict[str, object]:
    return {
        "name": z.name,
        "intervals": [list(iv) for iv in sorted(set(z.intervals))],
    }


def resolve_day_contract(
    config_read_result: object,
    valid_date_or_raw_config_text: datetime.date | str,
    valid_date: datetime.date | None = None,
    *,
    dated_overrides: dict[str, object] | None = None,
) -> dict[str, object]:
    """Build the resolved day contract from one ``ConfigReadResult``.

    The normal public boundary is ``(config_read_result, valid_date, *)``.
    Its ``TdtbConfig`` carries both parsed sections and the original raw config
    text, keeping route callers out of parser internals. The temporary
    ``(sections, raw_config_text, valid_date, *)`` form remains only for
    focused pure-projection tests while the contract is introduced.

    Pure — no I/O, no live calls, never raises.

    The contract is JSON-safe (every value is a primitive, list, or dict).

    Dated overrides carry tri-state semantics for ``work_allotment_minutes``:
      * absent / None  → use the preset resolution result (preset's own
        allotment when set, else the config ``Defaults.work_allotment_minutes``).
      * 0              → explicitly disable Mint (allotment = 0, mint_enabled = False).
      * positive int   → use that value.

    For ``day_preset``:
      * absent / None  → full resolution (matched row → configured default → fallback).
      * str            → use that preset name as the dated override.
    """
    if isinstance(config_read_result, dict):
        # Test-only compatibility for direct projection fixtures. T18b.5 route
        # callers must use ConfigReadResult so bootstrap handling stays uniform.
        sections = config_read_result
        raw_config_text = str(valid_date_or_raw_config_text)
        if valid_date is None:
            raise TypeError("valid_date is required with direct sections")
    else:
        config = getattr(config_read_result, "config", None)
        if config is None:
            return {
                "available_presets": [],
                "selected_preset": None,
                "resolution_source": "fallback",
                "enabled_zones": [],
                "effective_allotment_minutes": _DEFAULT_ALLOTMENT,
                "default_allotment_minutes": _DEFAULT_ALLOTMENT,
                "mint_enabled": False,
                "warnings": ["Planning config unavailable; using deterministic fallback"],
                "errors": [],
                "overlap_permissions_raw": "",
            }
        sections = config.sections
        raw_config_text = config.raw_text or ""
        valid_date = valid_date_or_raw_config_text

    if not isinstance(valid_date, datetime.date):
        raise TypeError("valid_date must be a datetime.date")

    overrides = dated_overrides or {}
    projection = project_day_semantics(sections, raw_config_text)

    preset_override = overrides.get("day_preset")
    if preset_override is None:
        preset_override = None  # absent or explicit None both mean "no override"

    resolved = resolve_day(projection, valid_date, dated_override=preset_override)

    # Apply the dated allotment override AFTER preset resolution.
    allotment_override = overrides.get("work_allotment_minutes")
    errors = list(projection.errors)
    if allotment_override is None:
        # absent or None → use the resolved preset's allotment (which already
        # inherits the config default when the preset has no own value).
        effective_allotment = resolved.work_allotment_minutes
    elif isinstance(allotment_override, int) and not isinstance(allotment_override, bool) and _validate_allotment(allotment_override):
        # 0 → disable; positive int → use that value.
        effective_allotment = allotment_override
    else:
        effective_allotment = resolved.work_allotment_minutes
        errors.append(
            "Dated work_allotment_minutes invalid; using resolved preset/config allotment"
        )

    mint_enabled = effective_allotment > 0

    return {
        "available_presets": [_preset_to_json_safe(p) for p in projection.presets],
        "selected_preset": (
            _preset_to_json_safe(resolved.preset) if resolved.preset is not None else None
        ),
        "resolution_source": resolved.resolution_source,
        # Work allotment is the opt-in for Mint zones. A zero allotment keeps
        # Template definitions in available preset metadata but projects no
        # active zone backdrops for the date (locked decisions 30/34).
        "enabled_zones": (
            [_zone_to_json_safe(z) for z in resolved.enabled_zones]
            if mint_enabled else []
        ),
        "effective_allotment_minutes": effective_allotment,
        "default_allotment_minutes": projection.default_allotment_minutes,
        "mint_enabled": mint_enabled,
        "warnings": list(resolved.warnings),
        "errors": errors,
        "overlap_permissions_raw": projection.overlap_permissions_raw,
    }


# ---------------------------------------------------------------------------
# T18b.4 — canonical planning-semantics fingerprint
# ---------------------------------------------------------------------------

def _canonical_preset(preset: DayPreset) -> dict[str, object]:
    """Return the order-insensitive semantic form of one preset."""
    return {
        "name": preset.name,
        "days": sorted(preset.days),
        "enabled_zones": sorted(set(preset.enabled_zones)),
        "work_allotment_minutes": preset.work_allotment_minutes,
    }


def _canonical_zone(zone: ZoneSpec) -> dict[str, object]:
    """Return a zone with equivalent intervals deduplicated and sorted."""
    return {
        "name": zone.name,
        "intervals": [list(interval) for interval in sorted(set(zone.intervals))],
    }


def planning_config_fingerprint(
    config_read_result: object,
    valid_date: datetime.date,
    *,
    dated_overrides: dict[str, object] | None = None,
) -> str:
    """SHA-256 fingerprint of resolved planning semantics.

    The digest deliberately includes only day-preset definitions, the selected
    default, normalized Template zones, default/effective allotments, the
    resolved dated choice, and verbatim overlap-policy prose. Parser ordering,
    unrelated config, source data, warnings, and errors cannot perturb it.
    """
    contract = resolve_day_contract(
        config_read_result, valid_date, dated_overrides=dated_overrides,
    )
    config = getattr(config_read_result, "config", None)
    if config is None:
        projection = DaySemanticsProjection(
            presets=[],
            configured_default=None,
            zones={},
            default_allotment_minutes=_DEFAULT_ALLOTMENT,
            overlap_permissions_raw="",
        )
    else:
        projection = project_day_semantics(config.sections, config.raw_text or "")

    selected = contract["selected_preset"]
    canonical = {
        "presets": sorted(
            (_canonical_preset(preset) for preset in projection.presets),
            key=lambda preset: str(preset["name"]),
        ),
        "configured_default": projection.configured_default,
        "zones": sorted(
            (_canonical_zone(zone) for zone in projection.zones.values()),
            key=lambda zone: str(zone["name"]),
        ),
        "default_allotment_minutes": projection.default_allotment_minutes,
        "effective_allotment_minutes": contract["effective_allotment_minutes"],
        "resolved_preset": selected["name"] if isinstance(selected, dict) else None,
        "overlap_permissions_raw": projection.overlap_permissions_raw,
    }
    canonical_bytes = json.dumps(
        canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical_bytes).hexdigest()
