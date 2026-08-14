"""Tests for config_reader.py — TDD gate for spec § 3.4 (T5).

Uses fixture config markdown built in tmp_path, modeled on the live shape at
`00 - META/Skill-Configs/tdtb-bridger.md` — never reads the live vault file.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from config_reader import (
    CONFIG_REL_PATH,
    FALLBACK_DEFAULTS,
    FALLBACK_TODOIST_FILTERS,
    FALLBACK_TODOIST_PROJECTS,
    read_config,
)


FULL_CONFIG = """\
---
schema_version: 2.1
description: Tunable configuration for the tdtb-bridger-vault skill.
last_updated: 2026-07-01
---

# TDTB Bridger Config

## Defaults

| Key                               | Value                |
| --------------------------------- | --------------------- |
| eod                               | 11:45 PM              |
| anchor.round_to_minutes           | 15                    |
| buffering.standard_pct            | 0.2                   |
| buffering.minimal_pct             | 0.11                  |
| buffering.off_pct                 | 0.00                  |
| caps.deep                         | 4                     |
| caps.mixed                        | 3                     |
| habits.source_directory           | 00 - META/Habituals/  |
| habits.fallback_minutes_per_habit | 4                     |
| habits.round_to_minutes           | 15                    |

## Schedulable Defaults

| Block        | State | Duration (blocks) | Notes                                 |
| ------------ | ----- | ------------------ | -------------------------------------- |
| buffering    | on    | —                   | mode: standard                         |
| minting      | on    | 4                   | individual blocks within Trinoor zone  |
| quick_tasks  | on    | 1                   | flexible / movable                     |
| shivery_jigs | off   | 0                   |                                         |

## Anchored Lifestyle Blocks

| Block           | Type   | Start    | End     | Duration | Days  | overlap_allowed |
| --------------- | ------ | -------- | ------- | -------- | ----- | ---------------- |
| Morning Routine | hard   | 7:45 AM  | —       | 80m      | daily | no                |
| Sudsing         | hard   | 5:45 PM  | —       | 30m      | daily | no                |
| Live            | window | 12:00 PM | 8:00 PM | 30m      | daily | yes               |

## Template Blocks
### Trinoor Hours

| Slot      | Start   | End      |
| --------- | ------- | -------- |
| Morning   | 8:30 AM | 12:30 PM |
| Afternoon | 1:30 AM | 5:00 PM  |
### Press (Gym)

| Days | Hours |
|------|-------|
| Mon – Thu | 5:00 AM – 10:00 PM |

## Overlap Permissions

### Todoist (by ID)

| ID               | Name (ref) | Notes                   |
| ---------------- | ---------- | ------------------------ |
| 6gQVxQXrgh4XQ48v | M1.5       | medication — every day  |

### Calendar Event Classes

| Calendar / prefix | Notes                                                |
| ------------------ | ----------------------------------------------------- |
| ◽ Blocks           | Minting work blocks accept overlay                    |
| 🟡 Trinoor          | Work zone accepts overlay (work happens IN the zone)  |

## Ignore List

### Todoist (by ID)

| ID               | Name (ref)   | Notes |
| ---------------- | ------------ | ----- |
| 6gJm2CCXM7hgXg9j | Post Move    |       |

### Obsidian (by path)

| Path | Notes |
|------|-------|
| — | populated on demand |

## Color Palette

| Token             | Color       | Hex    | Used for                              |
| ------------------ | ----------- | ------ | --------------------------------------- |
| event              | Teal        | 06B6D4 | Calendar fixed events (meetings)        |
| anchored           | Light red   | F87171 | Anchored blocks                         |

## Presets

| Name                   | Type     | Blocks | Priority | Zone                    | Latest Start |
| ---------------------- | -------- | ------ | -------- | ------------------------ | ------------ |
| Summits                | interval | 1      | 4        | any                       | —            |
| Press                  | interval | 2.5    | 3        | after_work, before_work   | 7:00 PM      |
| Make                   | interval | 2      | 2        | any                       | —            |

## Ranking Criteria

| Key | Value | Controls |
|-----|-------|----------|
| available_recency_days | 7 | Projects `updated` within N days → Available tier |
| interval_available_window_days | 3 | Intervals due within N days → Available tier |

## Calendar Titles

| Logical name | BusyCal title | Role |
|---|---|---|
| blocks | ⬜ Blocks | Work-window events |
| mint | 🟡 Mint | Minting |
| trinoor | 🟡 Trinoor | Trinoor work-zone framing events |

## Reference IDs

### Todoist Projects

| Project | ID               |
| ------- | ---------------- |
| PHEP    | `6fgXPMw28j7cRFMH` |
| Inbox   | `6M92PWG3HHJgQvfp` |

### Todoist Filters

| Filter        | ID           | Notes                             |
| -------------- | ------------ | ----------------------------------- |
| ⭐ Today       | `2368117560` | Dated + overdue tasks for today     |
| 🥇 First       | `2360031067` | No-date near-deadline               |

## Micro-Adventures

### Rotation

| Key | Value | Controls |
|-----|-------|----------|
| rotation.exclude_window_days | 14 | An idea isn't re-offered until N days |
| rotation.graduate_offer | yes | Offer to capture as adventure note |

### Pool

| ID   | Idea                                       | Category | Effort | Active |
| ---- | ------------------------------------------- | -------- | ------ | ------ |
| ma01 | Walk the greenway trail near the townhome   | nature   | low    | yes    |
| ma02 | Call a friend                               | social   | low    | yes    |
"""


def _write_config(vault_root: Path, text: str) -> Path:
    config_path = vault_root / CONFIG_REL_PATH
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(text, encoding="utf-8")
    return config_path


# ---------------------------------------------------------------------------
# Full parse
# ---------------------------------------------------------------------------

def test_full_parse_realistic_fixture(tmp_path: Path) -> None:
    _write_config(tmp_path, FULL_CONFIG)
    result = read_config(tmp_path)

    assert result.bootstrap_needed is False
    assert result.config is not None
    assert result.validation is not None
    assert result.validation.valid is True

    cfg = result.config
    assert set(REQUIRED_SECTIONS_FOR_TEST) <= set(cfg.sections.keys())


REQUIRED_SECTIONS_FOR_TEST = (
    "Defaults",
    "Schedulable Defaults",
    "Anchored Lifestyle Blocks",
    "Presets",
)


def test_full_parse_defaults_values(tmp_path: Path) -> None:
    _write_config(tmp_path, FULL_CONFIG)
    cfg = read_config(tmp_path).config

    eod = cfg.get_default("eod")
    assert eod.value == "11:45 PM"
    assert eod.source == "config"

    caps_deep = cfg.get_default("caps.deep")
    assert caps_deep.value == 4
    assert caps_deep.source == "config"


def test_full_parse_schedulable_defaults_is_record_list(tmp_path: Path) -> None:
    _write_config(tmp_path, FULL_CONFIG)
    cfg = read_config(tmp_path).config

    schedulable = cfg.sections["Schedulable Defaults"]
    assert isinstance(schedulable, list)
    names = {row["Block"] for row in schedulable}
    assert names == {"buffering", "minting", "quick_tasks", "shivery_jigs"}
    minting_row = next(r for r in schedulable if r["Block"] == "minting")
    assert minting_row["State"] == "on"
    assert minting_row["Duration (blocks)"] == 4


def test_full_parse_anchored_lifestyle_blocks(tmp_path: Path) -> None:
    _write_config(tmp_path, FULL_CONFIG)
    cfg = read_config(tmp_path).config

    blocks = cfg.sections["Anchored Lifestyle Blocks"]
    assert isinstance(blocks, list)
    live = next(r for r in blocks if r["Block"] == "Live")
    assert live["Type"] == "window"
    assert live["overlap_allowed"] is True  # "yes" -> True


def test_full_parse_template_blocks_nested_subsections(tmp_path: Path) -> None:
    _write_config(tmp_path, FULL_CONFIG)
    cfg = read_config(tmp_path).config

    template = cfg.sections["Template Blocks"]
    assert "Trinoor Hours" in template
    assert "Press (Gym)" in template
    trinoor_hours = template["Trinoor Hours"]
    assert isinstance(trinoor_hours, list)
    morning = next(r for r in trinoor_hours if r["Slot"] == "Morning")
    assert morning["Start"] == "8:30 AM"


# ---------------------------------------------------------------------------
# Dot-notation expansion
# ---------------------------------------------------------------------------

def test_dot_notation_flat_key_lookup(tmp_path: Path) -> None:
    _write_config(tmp_path, FULL_CONFIG)
    cfg = read_config(tmp_path).config

    # Flat dotted key present directly.
    assert cfg.sections["Defaults"]["buffering.standard_pct"] == 0.2


def test_dot_notation_nested_expansion(tmp_path: Path) -> None:
    _write_config(tmp_path, FULL_CONFIG)
    cfg = read_config(tmp_path).config

    defaults = cfg.sections["Defaults"]
    # Nested-dict mirror also present.
    assert defaults["buffering"]["standard_pct"] == 0.2
    assert defaults["caps"]["deep"] == 4
    assert defaults["habits"]["fallback_minutes_per_habit"] == 4


def test_dot_notation_in_micro_adventures_rotation(tmp_path: Path) -> None:
    _write_config(tmp_path, FULL_CONFIG)
    cfg = read_config(tmp_path).config

    rotation = cfg.sections["Micro-Adventures"]["Rotation"]
    assert rotation["rotation.exclude_window_days"] == 14
    assert rotation["rotation"]["exclude_window_days"] == 14
    assert rotation["rotation"]["graduate_offer"] is True


# ---------------------------------------------------------------------------
# Optional-section fallbacks
# ---------------------------------------------------------------------------

MINIMAL_REQUIRED_ONLY = """\
## Defaults

| Key | Value |
|-----|-------|
| eod | 11:59 PM |
| anchor.round_to_minutes | 15 |
| buffering.standard_pct | 0.19 |
| buffering.minimal_pct | 0.11 |
| buffering.off_pct | 0.00 |
| caps.deep | 4 |
| caps.mixed | 3 |
| habits.source_directory | 00 - META/Habituals/ |
| habits.fallback_minutes_per_habit | 4 |
| habits.round_to_minutes | 15 |

## Schedulable Defaults

| Block | State | Duration (blocks) | Notes |
|-------|-------|--------------------|-------|
| buffering | on | — | mode: standard |

## Anchored Lifestyle Blocks

| Block | Type | Start | End | Duration | Days | overlap_allowed |
|-------|------|-------|-----|----------|------|------------------|
| Sudsing | hard | 5:45 PM | — | 30m | daily | no |

## Presets

| Name | Type | Blocks | Priority |
|------|------|--------|----------|
| Summits | interval | 1 | 4 |
"""


def test_optional_ranking_criteria_absent_falls_back_per_key(tmp_path: Path) -> None:
    _write_config(tmp_path, MINIMAL_REQUIRED_ONLY)
    cfg = read_config(tmp_path).config

    assert "Ranking Criteria" not in cfg.sections
    result = cfg.get_ranking_criterion("available_recency_days")
    assert result.source == "fallback"
    assert result.value == 7

    result2 = cfg.get_ranking_criterion("sequence_rank")
    assert result2.source == "fallback"
    assert result2.value == ["priority", "overdue", "started", "alphabetical"]


def test_optional_micro_adventures_absent_falls_back(tmp_path: Path) -> None:
    _write_config(tmp_path, MINIMAL_REQUIRED_ONLY)
    cfg = read_config(tmp_path).config

    assert "Micro-Adventures" not in cfg.sections
    result = cfg.get_micro_adventure_setting("rotation.exclude_window_days")
    assert result.source == "fallback"
    assert result.value == 14


def test_optional_calendar_titles_absent_is_not_an_error(tmp_path: Path) -> None:
    _write_config(tmp_path, MINIMAL_REQUIRED_ONLY)
    result = read_config(tmp_path)

    assert result.validation.valid is True
    assert "Calendar Titles" not in result.config.sections


def test_optional_schema_reference_absent_falls_back_todoist_ids(tmp_path: Path) -> None:
    _write_config(tmp_path, MINIMAL_REQUIRED_ONLY)
    cfg = read_config(tmp_path).config

    today = cfg.get_todoist_filter_id("Today")
    assert today.source == "fallback"
    assert today.value == FALLBACK_TODOIST_FILTERS["Today"]
    assert today.value == "2368117560"

    phep = cfg.get_todoist_project_id("PHEP")
    assert phep.source == "fallback"
    assert phep.value == "6fgXPMw28j7cRFMH"


def test_ranking_criteria_partial_section_falls_back_per_missing_key(tmp_path: Path) -> None:
    partial = MINIMAL_REQUIRED_ONLY + """
## Ranking Criteria

| Key | Value | Controls |
|-----|-------|----------|
| available_recency_days | 10 | Custom override |
"""
    _write_config(tmp_path, partial)
    cfg = read_config(tmp_path).config

    overridden = cfg.get_ranking_criterion("available_recency_days")
    assert overridden.value == 10
    assert overridden.source == "config"

    still_falls_back = cfg.get_ranking_criterion("interval_available_window_days")
    assert still_falls_back.value == 3
    assert still_falls_back.source == "fallback"


# ---------------------------------------------------------------------------
# Missing-required-section reporting (never raises)
# ---------------------------------------------------------------------------

def test_missing_required_section_reported_not_raised(tmp_path: Path) -> None:
    text = """\
## Defaults

| Key | Value |
|-----|-------|
| eod | 11:59 PM |
| anchor.round_to_minutes | 15 |
| buffering.standard_pct | 0.19 |
| buffering.minimal_pct | 0.11 |
| buffering.off_pct | 0.00 |
| caps.deep | 4 |
| caps.mixed | 3 |
| habits.source_directory | 00 - META/Habituals/ |
| habits.fallback_minutes_per_habit | 4 |
| habits.round_to_minutes | 15 |
"""
    _write_config(tmp_path, text)
    result = read_config(tmp_path)  # must not raise

    assert result.bootstrap_needed is False
    assert result.validation.valid is False
    assert "Schedulable Defaults" in result.validation.missing_sections
    assert "Anchored Lifestyle Blocks" in result.validation.missing_sections
    assert "Presets" in result.validation.missing_sections


def test_missing_required_defaults_key_reported(tmp_path: Path) -> None:
    text = """\
## Defaults

| Key | Value |
|-----|-------|
| eod | 11:59 PM |

## Schedulable Defaults

| Block | State | Duration (blocks) | Notes |
|-------|-------|--------------------|-------|
| buffering | on | — | mode: standard |

## Anchored Lifestyle Blocks

| Block | Type | Start | End | Duration | Days | overlap_allowed |
|-------|------|-------|-----|----------|------|------------------|
| Sudsing | hard | 5:45 PM | — | 30m | daily | no |

## Presets

| Name | Type | Blocks | Priority |
|------|------|--------|----------|
| Summits | interval | 1 | 4 |
"""
    _write_config(tmp_path, text)
    result = read_config(tmp_path)

    assert result.validation.valid is False
    assert "Defaults" in result.validation.missing_keys
    assert "anchor.round_to_minutes" in result.validation.missing_keys["Defaults"]
    assert "caps.deep" in result.validation.missing_keys["Defaults"]


# ---------------------------------------------------------------------------
# Preset table with / without optional columns
# ---------------------------------------------------------------------------

def test_preset_row_with_optional_columns(tmp_path: Path) -> None:
    _write_config(tmp_path, FULL_CONFIG)
    cfg = read_config(tmp_path).config

    presets = cfg.get_presets()
    press = next(p for p in presets if p["Name"] == "Press")
    assert press["Type"] == "interval"
    assert press["Blocks"] == 2.5
    assert press["Priority"] == 3
    assert press["Zone"] == "after_work, before_work"
    assert press["Latest Start"] == "7:00 PM"


def test_preset_row_without_optional_columns(tmp_path: Path) -> None:
    _write_config(tmp_path, MINIMAL_REQUIRED_ONLY)
    result = read_config(tmp_path)

    assert result.validation.valid is True  # Zone/Latest Start not required
    presets = result.config.get_presets()
    summits = next(p for p in presets if p["Name"] == "Summits")
    assert summits["Type"] == "interval"
    assert summits["Blocks"] == 1
    assert summits["Priority"] == 4
    assert "Zone" not in summits


def test_preset_row_missing_required_column_flagged(tmp_path: Path) -> None:
    text = MINIMAL_REQUIRED_ONLY.replace(
        "| Summits | interval | 1 | 4 |",
        "| Summits | interval | 1 |  |",
    )
    _write_config(tmp_path, text)
    result = read_config(tmp_path)

    assert result.validation.valid is False
    assert "Presets" in result.validation.malformed_rows
    assert any("Priority" in msg for msg in result.validation.malformed_rows["Presets"])


def test_preset_priority_scale_not_inverted(tmp_path: Path) -> None:
    """4 = highest priority; this module must pass the raw value through
    unchanged, never remap or invert it."""
    _write_config(tmp_path, FULL_CONFIG)
    cfg = read_config(tmp_path).config

    presets = {p["Name"]: p for p in cfg.get_presets()}
    assert presets["Summits"]["Priority"] == 4
    assert presets["Make"]["Priority"] == 2
    assert presets["Summits"]["Priority"] > presets["Make"]["Priority"]


# ---------------------------------------------------------------------------
# Missing file -> bootstrap flag
# ---------------------------------------------------------------------------

def test_missing_config_file_sets_bootstrap_needed(tmp_path: Path) -> None:
    result = read_config(tmp_path)

    assert result.bootstrap_needed is True
    assert result.config is None
    assert result.validation is None


def test_missing_config_file_never_raises(tmp_path: Path) -> None:
    # tmp_path has no 00 - META/Skill-Configs/ at all — must not raise.
    empty_root = tmp_path / "nonexistent_nested" / "vault"
    result = read_config(empty_root)
    assert result.bootstrap_needed is True


# ---------------------------------------------------------------------------
# Fallback-source marking (general)
# ---------------------------------------------------------------------------

def test_fallback_source_marking_on_full_config_key_present(tmp_path: Path) -> None:
    _write_config(tmp_path, FULL_CONFIG)
    cfg = read_config(tmp_path).config

    result = cfg.get_default("eod")
    assert result.source == "config"


def test_fallback_source_marking_when_defaults_section_present_but_key_absent(
    tmp_path: Path,
) -> None:
    text = MINIMAL_REQUIRED_ONLY.replace("| habits.round_to_minutes | 15 |\n", "")
    _write_config(tmp_path, text)
    cfg = read_config(tmp_path).config

    result = cfg.get_default("habits.round_to_minutes")
    assert result.source == "fallback"
    assert result.value == FALLBACK_DEFAULTS["habits.round_to_minutes"]
    assert result.value == 15


def test_fallback_todoist_constants_match_contract() -> None:
    """Pin the exact fallback constants from the skill contract (never drift
    silently — a change here should be deliberate)."""
    assert FALLBACK_TODOIST_FILTERS["Today"] == "2368117560"
    assert FALLBACK_TODOIST_FILTERS["Quick Tasks"] == "2365541130"
    assert FALLBACK_TODOIST_FILTERS["First"] == "2360031067"
    assert FALLBACK_TODOIST_FILTERS["Next"] == "2360031248"
    assert FALLBACK_TODOIST_FILTERS["Then"] == "2360031650"
    assert FALLBACK_TODOIST_PROJECTS["PHEP"] == "6fgXPMw28j7cRFMH"


def test_vault_root_is_a_parameter_never_hardcoded(tmp_path: Path) -> None:
    """Two distinct tmp roots each resolve against their own file — proves
    vault_root is a real parameter, not a hardcoded path."""
    root_a = tmp_path / "vault_a"
    root_b = tmp_path / "vault_b"
    _write_config(root_a, FULL_CONFIG)
    _write_config(root_b, MINIMAL_REQUIRED_ONLY)

    result_a = read_config(root_a)
    result_b = read_config(root_b)

    assert result_a.config.get_default("eod").value == "11:45 PM"
    assert result_b.config.get_default("eod").value == "11:59 PM"


class TestEmojiInsensitiveRefLookup:
    """Regression: live config keys Reference-IDs rows as '⭐ Today' while
    callers use plain 'Today' — a rotated ID in config must never be shadowed
    by the stale fallback constant (locked decision 3)."""

    CONFIG = """\
## Defaults

| Key | Value |
|-----|-------|
| eod | 11:59 PM |

## Schedulable Defaults

| Block | State | Duration (blocks) |
|-------|-------|-------------------|
| minting | on | 2 |

## Anchored Lifestyle Blocks

| Block | Type | Start | Duration | Days |
|-------|------|-------|----------|------|
| Live | hard | 20:30 | 60m | daily |

## Presets

| Name | Type | Blocks | Priority |
|------|------|--------|----------|
| Summits | interval | 1 | 4 |

## Reference IDs

### Todoist Filters

| Filter | ID |
|--------|-----|
| ⭐ Today | 9999999999 |

### Todoist Projects

| Project | ID |
|---------|-----|
| 📂 PHEP | ROTATED_ID |
"""

    def _reader(self, tmp_path):
        cfg = tmp_path / "00 - META" / "Skill-Configs" / "tdtb-bridger.md"
        cfg.parent.mkdir(parents=True)
        cfg.write_text(self.CONFIG, encoding="utf-8")
        return read_config(tmp_path).config

    def test_plain_name_hits_emoji_config_row(self, tmp_path):
        cv = self._reader(tmp_path).get_todoist_filter_id("Today")
        assert cv.value == "9999999999"
        assert cv.source == "config"

    def test_rotated_project_id_not_shadowed_by_fallback(self, tmp_path):
        cv = self._reader(tmp_path).get_todoist_project_id("PHEP")
        assert cv.value == "ROTATED_ID"
        assert cv.source == "config"

    def test_exact_emoji_name_still_works(self, tmp_path):
        cv = self._reader(tmp_path).get_todoist_filter_id("⭐ Today")
        assert cv.value == "9999999999"
        assert cv.source == "config"


class TestIgnoreList:
    """`## Ignore List` — user-editable permanent hide list (T13e), honoring
    the live config's existing schema: Todoist rows by ID, Obsidian rows by
    vault-relative path, plus an optional Names subsection matched
    case-insensitively across sources."""

    CONFIG = """\
---
description: test config
---

# TDTB Bridger Config

## Ignore List

Items the app never surfaces.

### Todoist (by ID)

| ID               | Name (ref) | Notes |
| ---------------- | ---------- | ----- |
| 6gQVxQXrgh4XQ48v | M1.0       |       |

### Obsidian (by path)

| Path | Notes |
|------|-------|
| 50 - Operations/Tasks/Noisy.md | |
| — | populated on demand |

### Names

| Name  | Notes |
| ----- | ----- |
| Water |       |
"""

    def _reader(self, tmp_path):
        cfg = tmp_path / "00 - META" / "Skill-Configs" / "tdtb-bridger.md"
        cfg.parent.mkdir(parents=True)
        cfg.write_text(self.CONFIG, encoding="utf-8")
        return read_config(tmp_path).config

    def test_subsections_parsed_into_typed_sets(self, tmp_path):
        ig = self._reader(tmp_path).get_ignore_list()
        assert ig["todoist_ids"] == {"6gQVxQXrgh4XQ48v"}
        assert ig["paths"] == {"50 - Operations/Tasks/Noisy.md"}
        assert ig["names"] == {"water"}

    def test_placeholder_dash_rows_ignored(self, tmp_path):
        ig = self._reader(tmp_path).get_ignore_list()
        assert "—" not in ig["paths"]

    def test_absent_section_is_empty(self, tmp_path):
        cfg = tmp_path / "00 - META" / "Skill-Configs" / "tdtb-bridger.md"
        cfg.parent.mkdir(parents=True)
        cfg.write_text("---\ndescription: x\n---\n\n## Defaults\n\n| Key | Value |\n|---|---|\n| eod | 11:45 PM |\n", encoding="utf-8")
        ig = read_config(tmp_path).config.get_ignore_list()
        assert ig == {"todoist_ids": set(), "paths": set(), "names": set()}
