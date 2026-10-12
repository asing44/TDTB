"""config_reader.py — Runtime view of the TDTB config (repo file + local override).

The config no longer comes from the Obsidian vault note (vault removal V1,
docs/plan/2026-10-12-vault-removal). ``read_config`` takes its sections from
``app_config`` — the tracked ``config/tdtb.json`` with the optional
machine-local ``~/.config/tdtb/config.json`` layered on top — and every key
that is absent falls back to the in-code constants below (the same inline
fallback contract the tdtb-bridger-vault skill carries, SKILL.md § 0.1 Step 2,
"Skill-inline fallback values", lines ~399-410).

Gate: TDD — tests/test_config_reader.py must pass.

Contract summary (spec § 3.4):
  - ``sections`` is the markdown-shaped dict ``app_config.sections_from_document``
    emits: key/value sections as dicts (dot-notation keys such as
    ``buffering.standard_pct`` are kept and also expanded to nested values),
    record sections as lists of row dicts.
  - Every key has an inline fallback; `get()` returns config value if present,
    else fallback, with an inspectable `source` marker ("config" / "fallback").
  - Required sections (Defaults, Schedulable Defaults, Anchored Lifestyle
    Blocks, Presets) are validated; specific missing sections/keys are
    reported, never raised as an exception.
  - Optional sections (Ranking Criteria, Micro-Adventures, Calendar Titles,
    Schema Reference) fall back individually per-key, never error.
  - `bootstrap_needed` is set when neither config layer is usable. This
    module does NOT write a default config.
  - Preset rows require Name/Type/Blocks/Priority; Zone + Latest Start are
    optional. Priority scale is 4=highest — never inverted, never touched here
    (this module passes the raw value through).
  - Todoist IDs are read from a Schema Reference section when present;
    otherwise fall back to the skill's stable constants, marked
    source="fallback".
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import app_config


REQUIRED_SECTIONS = (
    "Defaults",
    "Schedulable Defaults",
    "Anchored Lifestyle Blocks",
    "Presets",
)

REQUIRED_DEFAULTS_KEYS = (
    "eod",
    "anchor.round_to_minutes",
    "buffering.standard_pct",
    "buffering.minimal_pct",
    "buffering.off_pct",
    "caps.deep",
    "caps.mixed",
    "habits.fallback_minutes_per_habit",
    "habits.round_to_minutes",
)

REQUIRED_PRESET_COLUMNS = ("Name", "Type", "Blocks", "Priority")
OPTIONAL_PRESET_COLUMNS = ("Zone", "Latest Start")

# ---------------------------------------------------------------------------
# Skill-inline fallback values (SKILL.md § 0.1 Step 2, lines ~399-410)
# ---------------------------------------------------------------------------

FALLBACK_DEFAULTS: dict[str, Any] = {
    "eod": "11:59 PM",
    "anchor.round_to_minutes": 15,
    "buffering.standard_pct": 0.19,
    "buffering.minimal_pct": 0.11,
    "buffering.off_pct": 0.00,
    "caps.deep": 4,
    "caps.mixed": 3,
    "habits.fallback_minutes_per_habit": 4,
    "habits.round_to_minutes": 15,
    "habits.completion_field": "entries",
    # G18a (time-blocking research, 2026-07-14): planning-fallacy correction
    # applied at label→blocks translation in /sequence. 1.0 = off; raise to
    # ~1.5 once actual-vs-estimated data exists (ledger "estimation" section).
    "estimation.correction_factor": 1.0,
}

FALLBACK_TODOIST_FILTERS: dict[str, str] = {
    "Today": "2368117560",
    "Quick Tasks": "2365541130",
    "First": "2360031067",
    "Next": "2360031248",
    "Then": "2360031650",
}

# Emoji-prefixed aliases, per spec § 3.2 / SKILL.md naming ("⭐Today" etc.)
FALLBACK_TODOIST_FILTER_ALIASES: dict[str, str] = {
    "⭐Today": FALLBACK_TODOIST_FILTERS["Today"],
    "⚡Quick": FALLBACK_TODOIST_FILTERS["Quick Tasks"],
    "🥇First": FALLBACK_TODOIST_FILTERS["First"],
    "🥈Next": FALLBACK_TODOIST_FILTERS["Next"],
    "🥉Then": FALLBACK_TODOIST_FILTERS["Then"],
}

FALLBACK_TODOIST_PROJECTS: dict[str, str] = {
    "PHEP": "6fgXPMw28j7cRFMH",
}

FALLBACK_RANKING_CRITERIA: dict[str, Any] = {
    "available_recency_days": 7,
    "interval_available_window_days": 3,
    "within_tier_sort": ["urgency", "overdue", "deadline", "staleness", "summit"],
    "sequence_rank": ["priority", "overdue", "started", "alphabetical"],
    "today_sort": ["priority", "due_date", "alphabetical"],
}

FALLBACK_MICRO_ADVENTURES: dict[str, Any] = {
    "rotation.exclude_window_days": 14,
    "rotation.graduate_offer": True,
}


@dataclass
class ConfigValue:
    """A resolved config value with provenance."""

    value: Any
    source: str  # "config" or "fallback"


@dataclass
class ValidationResult:
    """Outcome of required-section/key validation. Never raises — only reports."""

    valid: bool
    missing_sections: list[str] = field(default_factory=list)
    missing_keys: dict[str, list[str]] = field(default_factory=dict)
    malformed_rows: dict[str, list[str]] = field(default_factory=dict)


class TdtbConfig:
    """TDTB config with fallback-aware lookups.

    Construct via `read_config()`, not directly — the reader handles
    the missing-file / bootstrap_needed case before a TdtbConfig exists.
    """

    def __init__(self, sections: dict[str, Any], raw_text: str | None) -> None:
        self.sections = sections
        self.raw_text = raw_text

    # -- generic key/value lookup with dot-notation + fallback -------------

    def get(self, section: str, key: str, fallback: Any = None) -> ConfigValue:
        """Look up `key` (dot-notation supported) within `section`.

        Returns a ConfigValue with `source` set to "config" when found in the
        parsed file, else "fallback" (using the caller-supplied fallback, or
        None if omitted).
        """
        section_data = self.sections.get(section)
        if isinstance(section_data, dict) and key in section_data:
            return ConfigValue(value=section_data[key], source="config")
        return ConfigValue(value=fallback, source="fallback")

    def get_default(self, key: str) -> ConfigValue:
        """Look up a `## Defaults` key with the skill-inline fallback applied."""
        fallback = FALLBACK_DEFAULTS.get(key)
        return self.get("Defaults", key, fallback=fallback)

    def get_ranking_criterion(self, key: str) -> ConfigValue:
        """Look up a `## Ranking Criteria` key; optional section, per-key fallback."""
        fallback = FALLBACK_RANKING_CRITERIA.get(key)
        return self.get("Ranking Criteria", key, fallback=fallback)

    def get_micro_adventure_setting(self, key: str) -> ConfigValue:
        """Look up a `## Micro-Adventures` rotation key; optional, per-key fallback."""
        fallback = FALLBACK_MICRO_ADVENTURES.get(key)
        return self.get("Micro-Adventures", key, fallback=fallback)

    # -- presets -------------------------------------------------------------

    def get_presets(self) -> list[dict[str, Any]]:
        """Return `## Presets` rows as dicts. Empty list if section is absent."""
        rows = self.sections.get("Presets")
        if not isinstance(rows, list):
            return []
        return rows

    def get_placement_context(self) -> str:
        """Return `## Placement Context` as sanitized prose, "" when absent.

        Allocator-rewrite T5 / locked decision 10: wellbeing and placement
        preferences are config PROSE, not a subsystem. Whatever Adam writes
        here ("no screen-heavy work after 21:00") rides verbatim into the
        sequence prompt. The config's ``placement_context`` string arrives as
        ``{"_body": ...}``.
        """
        section = self.sections.get(PLACEMENT_CONTEXT_SECTION)
        if not isinstance(section, dict):
            return ""
        return sanitize_placement_context(str(section.get("_body") or ""))

    def get_ignore_list(self) -> dict[str, set[str]]:
        """Return `## Ignore List` as {"todoist_ids", "paths", "names"} sets —
        the user-editable permanent hide list (T13e). The section's existing
        schema is honored: `### Todoist (by ID)` rows match on todoist_id,
        `### Obsidian (by path)` rows on vault-relative path. A `### Names`
        subsection (or bare `Name` rows) matches any source case-insensitively.
        Optional section; empty sets if absent."""
        section = self.sections.get("Ignore List")
        out: dict[str, set[str]] = {"todoist_ids": set(), "paths": set(), "names": set()}
        if not isinstance(section, dict):
            return out
        for sub_name, rows in section.items():
            if not isinstance(rows, list):
                continue
            key = sub_name.casefold()
            for row in rows:
                if not isinstance(row, dict):
                    continue
                if "todoist" in key:
                    v = str(row.get("ID") or "").strip()
                    if v and v != "—":
                        out["todoist_ids"].add(v)
                elif "obsidian" in key or "path" in key:
                    v = str(row.get("Path") or "").strip()
                    if v and v != "—":
                        out["paths"].add(v)
                else:
                    v = str(row.get("Name") or "").strip()
                    if v and v != "—":
                        out["names"].add(v.casefold())
        return out

    # -- todoist ids -----------------------------------------------------------

    def get_todoist_filter_id(self, name: str) -> ConfigValue:
        """Look up a Todoist filter ID by logical name (e.g. 'Today', '⭐Today').

        Checks a config `## Schema Reference` / `### Todoist Filters` table
        first; falls back to the skill's stable constants (source="fallback").
        Matching is emoji-insensitive: the live config keys rows as '⭐ Today'
        while callers use 'Today' — a plain-name lookup MUST still hit the
        config row, or a rotated ID in config would be silently shadowed by
        the stale fallback constant (locked decision 3).
        """
        schema_filters = self._schema_reference_filters()
        hit = schema_filters.get(name) or schema_filters.get(_normalize_ref_name(name))
        if hit:
            return ConfigValue(value=hit, source="config")
        fallback = FALLBACK_TODOIST_FILTERS.get(name) or FALLBACK_TODOIST_FILTER_ALIASES.get(name)
        return ConfigValue(value=fallback, source="fallback")

    def get_todoist_project_id(self, name: str) -> ConfigValue:
        """Look up a Todoist project ID by name (e.g. 'PHEP'). Emoji-insensitive
        like get_todoist_filter_id."""
        schema_projects = self._schema_reference_projects()
        hit = schema_projects.get(name) or schema_projects.get(_normalize_ref_name(name))
        if hit:
            return ConfigValue(value=hit, source="config")
        fallback = FALLBACK_TODOIST_PROJECTS.get(name)
        return ConfigValue(value=fallback, source="fallback")

    def _schema_reference_filters(self) -> dict[str, str]:
        ref = self.sections.get("Schema Reference") or self.sections.get("Reference IDs")
        if not isinstance(ref, dict):
            return {}
        filters = ref.get("Todoist Filters")
        if not isinstance(filters, list):
            return {}
        out: dict[str, str] = {}
        for row in filters:
            filt_name = row.get("Filter")
            filt_id = row.get("ID")
            if filt_name and filt_id:
                key = str(filt_name).strip()
                out[key] = str(filt_id).strip()
                out.setdefault(_normalize_ref_name(key), str(filt_id).strip())
        return out

    def _schema_reference_projects(self) -> dict[str, str]:
        ref = self.sections.get("Schema Reference") or self.sections.get("Reference IDs")
        if not isinstance(ref, dict):
            return {}
        projects = ref.get("Todoist Projects")
        if not isinstance(projects, list):
            return {}
        out: dict[str, str] = {}
        for row in projects:
            proj_name = row.get("Project")
            proj_id = row.get("ID")
            if proj_name and proj_id:
                key = str(proj_name).strip()
                out[key] = str(proj_id).strip()
                out.setdefault(_normalize_ref_name(key), str(proj_id).strip())
        return out

    # -- validation ------------------------------------------------------------

    def validate(self) -> ValidationResult:
        """Validate required sections/keys per SKILL.md § 0.1 Step 4b.

        Never raises. Optional sections (Ranking Criteria, Micro-Adventures,
        Calendar Titles, Schema Reference) are excluded from this check —
        their gaps fall back individually and are never reported as errors.
        """
        missing_sections: list[str] = []
        missing_keys: dict[str, list[str]] = {}
        malformed_rows: dict[str, list[str]] = {}

        for section in REQUIRED_SECTIONS:
            if section not in self.sections:
                missing_sections.append(section)

        if "Defaults" in self.sections:
            defaults = self.sections["Defaults"]
            missing = [k for k in REQUIRED_DEFAULTS_KEYS if k not in defaults]
            if missing:
                missing_keys["Defaults"] = missing

        if "Presets" in self.sections:
            preset_rows = self.sections["Presets"]
            row_problems: list[str] = []
            if isinstance(preset_rows, list):
                for i, row in enumerate(preset_rows):
                    missing_cols = [c for c in REQUIRED_PRESET_COLUMNS if not row.get(c)]
                    if missing_cols:
                        row_name = row.get("Name", f"row {i}")
                        row_problems.append(f"{row_name}: missing {', '.join(missing_cols)}")
            if row_problems:
                malformed_rows["Presets"] = row_problems

        valid = not missing_sections and not missing_keys and not malformed_rows
        return ValidationResult(
            valid=valid,
            missing_sections=missing_sections,
            missing_keys=missing_keys,
            malformed_rows=malformed_rows,
        )


@dataclass
class ConfigReadResult:
    """Top-level result of `read_config`."""

    config: TdtbConfig | None
    bootstrap_needed: bool
    validation: ValidationResult | None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalize_ref_name(name: str) -> str:
    """Emoji-insensitive key for Reference-IDs row matching: drop every
    non-alphanumeric rune (emoji, medals, spaces, punctuation) and casefold,
    so '⭐ Today' == 'Today' == '⭐today'."""
    return "".join(ch for ch in name if ch.isalnum()).casefold()


# Allocator-rewrite T5: the config-owned prose block that rides verbatim into
# the sequence prompt. Bounded so a runaway section can't crowd out the real
# inputs (assigned rows, anchored blocks) in the one billed call.
PLACEMENT_CONTEXT_SECTION = "Placement Context"
PLACEMENT_CONTEXT_MAX_CHARS = 2000


def sanitize_placement_context(raw: str) -> str:
    """Make config prose safe to embed verbatim in a prompt.

    Three jobs, deliberately minimal — this is Adam's own config note, not
    untrusted input, so the goal is structural safety, not content policing:

    - strip code fences, which would break out of the prompt's own block;
    - drop control characters and collapse runs of blank lines;
    - truncate to ``PLACEMENT_CONTEXT_MAX_CHARS`` on a LINE boundary, so the
      model never sees a rule cut off mid-sentence and half-applies it.
    """
    if not raw:
        return ""
    kept: list[str] = []
    blanks = 0
    for line in raw.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = "".join(ch for ch in line if ch >= " " or ch == "\t")
        if line.lstrip().startswith("```"):
            continue
        if not line.strip():
            blanks += 1
            if blanks > 1 or not kept:
                continue
            kept.append("")
            continue
        blanks = 0
        kept.append(line.rstrip())
    out: list[str] = []
    used = 0
    for line in kept:
        cost = len(line) + (1 if out else 0)
        if used + cost > PLACEMENT_CONTEXT_MAX_CHARS:
            break
        out.append(line)
        used += cost
    return "\n".join(out).strip()


# ---------------------------------------------------------------------------
# Top-level entry point
# ---------------------------------------------------------------------------

def read_config(vault_root: str | Path | None = None) -> ConfigReadResult:
    """Read the TDTB config: ``config/tdtb.json`` with the machine-local
    ``~/.config/tdtb/config.json`` layered on top (see ``app_config``).

    ``vault_root`` is accepted and ignored: the vault note is no longer a
    config source. The parameter stays only so the existing callers (which
    still thread the vault root for their other, later-slice reads) need not
    change; it goes away with them.

    Returns a `ConfigReadResult`:
      - `bootstrap_needed=True`, `config=None`, `validation=None` when neither
        layer is usable (caller decides whether to offer writing defaults —
        this module does not write one).
      - Otherwise `config` is a populated `TdtbConfig` and `validation` is
        the required-section/key report (never raises on gaps).
    """
    sections = app_config.load_sections()
    if not sections:
        return ConfigReadResult(config=None, bootstrap_needed=True, validation=None)
    config = TdtbConfig(sections=sections, raw_text=None)
    return ConfigReadResult(
        config=config, bootstrap_needed=False, validation=config.validate()
    )
