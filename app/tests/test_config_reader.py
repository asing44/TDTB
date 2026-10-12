"""Tests for config_reader.py — TDD gate for spec § 3.4 (T5).

V1 of the vault removal: ``read_config`` takes its sections from the layered
JSON config (``app_config``) and never reads the vault note. These tests
install small config documents at the per-test repo-config path (conftest
points ``TDTB_REPO_CONFIG`` at a tmp file) — never the operator's real repo
file, ``~/.config/tdtb``, or vault.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import app_config
import pytest
from config_reader import (
    FALLBACK_DEFAULTS,
    FALLBACK_RANKING_CRITERIA,
    FALLBACK_TODOIST_FILTERS,
    FALLBACK_TODOIST_PROJECTS,
    read_config,
)


FULL_DOC: dict[str, Any] = {
    "version": 1,
    "defaults": {
        "eod": "11:45 PM",
        "anchor": {"round_to_minutes": 15},
        "buffering": {"standard_pct": 0.2, "minimal_pct": 0.11, "off_pct": 0.0},
        "caps": {"deep": 4, "mixed": 3},
    },
    "habits": {"fallback_minutes_per_habit": 4, "round_to_minutes": 15},
    "schedulable_defaults": [
        {"Block": "buffering", "State": "on", "Duration (blocks)": None, "Notes": "mode: standard"},
        {"Block": "minting", "State": "on", "Duration (blocks)": 4, "Notes": "in Trinoor"},
        {"Block": "quick_tasks", "State": "on", "Duration (blocks)": 1, "Notes": None},
        {"Block": "shivery_jigs", "State": "off", "Duration (blocks)": 0, "Notes": None},
    ],
    "anchored_blocks": [
        {"Block": "Morning Routine", "Type": "hard", "Start": "7:45 AM", "Duration": "80m",
         "Days": "daily", "overlap_allowed": False},
        {"Block": "Live", "Type": "window", "Start": "12:00 PM", "End": "8:00 PM",
         "Duration": "30m", "Days": "daily", "overlap_allowed": True},
    ],
    "template_blocks": {
        "Trinoor Hours": [
            {"Slot": "Morning", "Start": "8:30 AM", "End": "12:30 PM"},
            {"Slot": "Afternoon", "Start": "1:30 PM", "End": "5:00 PM"},
        ],
    },
    "presets": [
        {"Name": "Summits", "Type": "interval", "Blocks": 1, "Priority": 4,
         "Zone": "any", "Latest Start": None},
        {"Name": "Press", "Type": "interval", "Blocks": 2.5, "Priority": 3,
         "Zone": "after_work, before_work", "Latest Start": "7:00 PM"},
    ],
}

#: The smallest document that passes validation.
MINIMAL_DOC: dict[str, Any] = {
    "version": 1,
    "defaults": {
        "eod": "11:59 PM",
        "anchor": {"round_to_minutes": 15},
        "buffering": {"standard_pct": 0.19, "minimal_pct": 0.11, "off_pct": 0.0},
        "caps": {"deep": 4, "mixed": 3},
    },
    "habits": {"fallback_minutes_per_habit": 4, "round_to_minutes": 15},
    "schedulable_defaults": [
        {"Block": "buffering", "State": "on", "Duration (blocks)": None, "Notes": "mode: standard"},
    ],
    "anchored_blocks": [
        {"Block": "Sudsing", "Type": "hard", "Start": "5:45 PM", "Duration": "30m",
         "Days": "daily", "overlap_allowed": False},
    ],
    "presets": [{"Name": "Summits", "Type": "interval", "Blocks": 1, "Priority": 4}],
}


def _install(doc: dict[str, Any]) -> None:
    path = app_config.repo_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")


def _doc(base: dict[str, Any], **changes: Any) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for key, value in changes.items():
        if value is None:
            out.pop(key, None)
        else:
            out[key] = value
    return out


# ---------------------------------------------------------------------------
# Full read
# ---------------------------------------------------------------------------

def test_full_read_is_valid() -> None:
    _install(FULL_DOC)
    result = read_config()

    assert result.bootstrap_needed is False
    assert result.config is not None
    assert result.validation is not None
    assert result.validation.valid is True
    assert {"Defaults", "Schedulable Defaults", "Anchored Lifestyle Blocks", "Presets"} <= set(
        result.config.sections
    )


def test_read_config_takes_no_argument_and_ignores_a_stray_note(tmp_path: Path) -> None:
    """``read_config`` has no vault parameter; a markdown note sitting anywhere
    changes nothing."""
    import inspect

    assert not inspect.signature(read_config).parameters
    _install(FULL_DOC)
    note = tmp_path / "00 - META" / "Skill-Configs" / "tdtb-bridger.md"
    note.parent.mkdir(parents=True)
    note.write_text("## Defaults\n| Key | Value |\n|---|---|\n| eod | 1:00 AM |\n", encoding="utf-8")

    assert read_config().config.get_default("eod").value == "11:45 PM"


def test_defaults_values_and_source() -> None:
    _install(FULL_DOC)
    cfg = read_config().config

    eod = cfg.get_default("eod")
    assert (eod.value, eod.source) == ("11:45 PM", "config")
    caps_deep = cfg.get_default("caps.deep")
    assert (caps_deep.value, caps_deep.source) == (4, "config")


def test_schedulable_defaults_is_record_list() -> None:
    _install(FULL_DOC)
    schedulable = read_config().config.sections["Schedulable Defaults"]
    assert {row["Block"] for row in schedulable} == {
        "buffering", "minting", "quick_tasks", "shivery_jigs",
    }
    minting = next(r for r in schedulable if r["Block"] == "minting")
    assert minting["State"] == "on"
    assert minting["Duration (blocks)"] == 4


def test_anchored_blocks_and_template_blocks() -> None:
    _install(FULL_DOC)
    sections = read_config().config.sections

    live = next(r for r in sections["Anchored Lifestyle Blocks"] if r["Block"] == "Live")
    assert live["Type"] == "window"
    assert live["overlap_allowed"] is True
    morning = next(r for r in sections["Template Blocks"]["Trinoor Hours"] if r["Slot"] == "Morning")
    assert morning["Start"] == "8:30 AM"


# ---------------------------------------------------------------------------
# Dot-notation
# ---------------------------------------------------------------------------

def test_dot_notation_flat_key_and_nested_mirror() -> None:
    _install(FULL_DOC)
    defaults = read_config().config.sections["Defaults"]

    assert defaults["buffering.standard_pct"] == 0.2
    assert defaults["buffering"]["standard_pct"] == 0.2
    assert defaults["caps"]["deep"] == 4
    assert defaults["habits"]["fallback_minutes_per_habit"] == 4


def test_micro_adventure_rotation_dot_notation() -> None:
    _install(_doc(FULL_DOC, micro_adventure_rotation={"exclude_window_days": 14, "graduate_offer": True}))
    rotation = read_config().config.sections["Micro-Adventures"]["Rotation"]

    assert rotation["rotation.exclude_window_days"] == 14
    assert rotation["rotation"]["exclude_window_days"] == 14
    assert rotation["rotation"]["graduate_offer"] is True


# ---------------------------------------------------------------------------
# Optional-section fallbacks
# ---------------------------------------------------------------------------

def test_optional_ranking_criteria_absent_falls_back_per_key() -> None:
    _install(MINIMAL_DOC)
    cfg = read_config().config

    assert "Ranking Criteria" not in cfg.sections
    recency = cfg.get_ranking_criterion("available_recency_days")
    assert (recency.source, recency.value) == ("fallback", 7)
    rank = cfg.get_ranking_criterion("sequence_rank")
    assert rank.source == "fallback"
    assert rank.value == FALLBACK_RANKING_CRITERIA["sequence_rank"]


def test_ranking_criteria_partial_section_falls_back_per_missing_key() -> None:
    _install(_doc(MINIMAL_DOC, ranking_criteria={"available_recency_days": 9}))
    cfg = read_config().config

    present = cfg.get_ranking_criterion("available_recency_days")
    assert (present.source, present.value) == ("config", 9)
    absent = cfg.get_ranking_criterion("interval_available_window_days")
    assert (absent.source, absent.value) == ("fallback", 3)


def test_ranking_order_list_is_read_from_config() -> None:
    _install(_doc(MINIMAL_DOC, ranking_criteria={"within_tier_sort": ["deadline", "urgency"]}))
    value = read_config().config.get_ranking_criterion("within_tier_sort")
    assert (value.source, value.value) == ("config", ["deadline", "urgency"])


def test_optional_micro_adventures_absent_falls_back() -> None:
    _install(MINIMAL_DOC)
    cfg = read_config().config

    assert "Micro-Adventures" not in cfg.sections
    setting = cfg.get_micro_adventure_setting("rotation.exclude_window_days")
    assert (setting.source, setting.value) == ("fallback", 14)


def test_optional_calendar_titles_absent_is_not_an_error() -> None:
    _install(MINIMAL_DOC)
    result = read_config()

    assert "Calendar Titles" not in result.config.sections
    assert result.validation.valid is True


def test_optional_schema_reference_absent_falls_back_todoist_ids() -> None:
    _install(MINIMAL_DOC)
    cfg = read_config().config

    today = cfg.get_todoist_filter_id("Today")
    assert (today.source, today.value) == ("fallback", FALLBACK_TODOIST_FILTERS["Today"])
    phep = cfg.get_todoist_project_id("PHEP")
    assert (phep.source, phep.value) == ("fallback", FALLBACK_TODOIST_PROJECTS["PHEP"])


# ---------------------------------------------------------------------------
# Validation — reported, never raised
# ---------------------------------------------------------------------------

def test_missing_required_section_reported_not_raised() -> None:
    _install({"version": 1, "defaults": MINIMAL_DOC["defaults"], "habits": MINIMAL_DOC["habits"]})
    result = read_config()  # must not raise

    assert result.bootstrap_needed is False
    assert result.validation.valid is False
    for section in ("Schedulable Defaults", "Anchored Lifestyle Blocks", "Presets"):
        assert section in result.validation.missing_sections


def test_missing_required_defaults_key_reported() -> None:
    _install(_doc(MINIMAL_DOC, defaults={"eod": "11:59 PM"}, habits=None))
    result = read_config()

    assert result.validation.valid is False
    assert "anchor.round_to_minutes" in result.validation.missing_keys["Defaults"]
    assert "caps.deep" in result.validation.missing_keys["Defaults"]


# ---------------------------------------------------------------------------
# Presets
# ---------------------------------------------------------------------------

def test_preset_row_with_optional_columns() -> None:
    _install(FULL_DOC)
    press = next(p for p in read_config().config.get_presets() if p["Name"] == "Press")

    assert press["Type"] == "interval"
    assert press["Blocks"] == 2.5
    assert press["Priority"] == 3
    assert press["Zone"] == "after_work, before_work"
    assert press["Latest Start"] == "7:00 PM"


def test_preset_row_without_optional_columns() -> None:
    _install(MINIMAL_DOC)
    result = read_config()

    assert result.validation.valid is True  # Zone / Latest Start not required
    summits = next(p for p in result.config.get_presets() if p["Name"] == "Summits")
    assert (summits["Type"], summits["Blocks"], summits["Priority"]) == ("interval", 1, 4)
    assert "Zone" not in summits


def test_preset_row_missing_required_column_flagged() -> None:
    _install(_doc(MINIMAL_DOC, presets=[{"Name": "Summits", "Type": "interval", "Blocks": 1}]))
    result = read_config()

    assert result.validation.valid is False
    assert any("Priority" in msg for msg in result.validation.malformed_rows["Presets"])


def test_preset_priority_scale_not_inverted() -> None:
    """4 = highest priority; passed through unchanged, never remapped."""
    _install(FULL_DOC)
    by_name = {p["Name"]: p for p in read_config().config.get_presets()}

    assert by_name["Summits"]["Priority"] == 4
    assert by_name["Press"]["Priority"] == 3


# ---------------------------------------------------------------------------
# Bootstrap flag
# ---------------------------------------------------------------------------

def test_no_config_sets_bootstrap_needed() -> None:
    result = read_config()

    assert result.bootstrap_needed is True
    assert result.config is None
    assert result.validation is None


def test_unusable_config_sets_bootstrap_needed_and_never_raises() -> None:
    path = app_config.repo_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ not json", encoding="utf-8")
    assert read_config().bootstrap_needed is True


# ---------------------------------------------------------------------------
# Fallback-source marking
# ---------------------------------------------------------------------------

def test_fallback_source_marking_on_key_present() -> None:
    _install(FULL_DOC)
    assert read_config().config.get_default("eod").source == "config"


def test_fallback_source_marking_when_key_absent() -> None:
    habits = {"fallback_minutes_per_habit": 4}
    _install(_doc(MINIMAL_DOC, habits=habits))

    result = read_config().config.get_default("habits.round_to_minutes")
    assert result.source == "fallback"
    assert result.value == FALLBACK_DEFAULTS["habits.round_to_minutes"] == 15


def test_fallback_todoist_constants_match_contract() -> None:
    """Pin the exact fallback constants from the skill contract (never drift
    silently — a change here should be deliberate)."""
    assert FALLBACK_TODOIST_FILTERS["Today"] == "2368117560"
    assert FALLBACK_TODOIST_FILTERS["Quick Tasks"] == "2365541130"
    assert FALLBACK_TODOIST_FILTERS["First"] == "2360031067"
    assert FALLBACK_TODOIST_FILTERS["Next"] == "2360031248"
    assert FALLBACK_TODOIST_FILTERS["Then"] == "2360031650"
    assert FALLBACK_TODOIST_PROJECTS["PHEP"] == "6fgXPMw28j7cRFMH"


# ---------------------------------------------------------------------------
# Todoist reference ids
# ---------------------------------------------------------------------------

class TestEmojiInsensitiveRefLookup:
    """Regression: the config keys filter rows as '⭐ Today' while callers use
    plain 'Today' — a rotated ID in config must never be shadowed by the stale
    fallback constant (locked decision 3)."""

    DOC = _doc(
        MINIMAL_DOC,
        schema_reference={
            "todoist_filters": [{"Filter": "⭐ Today", "ID": "9999999999"}],
            "todoist_projects": [{"Project": "📂 PHEP", "ID": "ROTATED_ID"}],
        },
    )

    def _reader(self):
        _install(self.DOC)
        return read_config().config

    def test_plain_name_hits_emoji_config_row(self):
        cv = self._reader().get_todoist_filter_id("Today")
        assert (cv.value, cv.source) == ("9999999999", "config")

    def test_rotated_project_id_not_shadowed_by_fallback(self):
        cv = self._reader().get_todoist_project_id("PHEP")
        assert (cv.value, cv.source) == ("ROTATED_ID", "config")

    def test_exact_emoji_name_still_works(self):
        cv = self._reader().get_todoist_filter_id("⭐ Today")
        assert (cv.value, cv.source) == ("9999999999", "config")


# ---------------------------------------------------------------------------
# Ignore list
# ---------------------------------------------------------------------------

class TestIgnoreList:
    """The permanent hide list (T13e): Todoist ids, vault-relative paths, and
    names matched case-insensitively across sources."""

    DOC = _doc(
        MINIMAL_DOC,
        ignore={
            "todoist_ids": ["6gQVxQXrgh4XQ48v"],
            "paths": ["50 - Operations/Tasks/Noisy.md", "—"],
            "names": ["Water"],
        },
    )

    def test_lists_become_typed_sets(self):
        _install(self.DOC)
        ig = read_config().config.get_ignore_list()
        assert ig["todoist_ids"] == {"6gQVxQXrgh4XQ48v"}
        assert ig["paths"] == {"50 - Operations/Tasks/Noisy.md"}
        assert ig["names"] == {"water"}

    def test_placeholder_dash_rows_ignored(self):
        _install(self.DOC)
        assert "—" not in read_config().config.get_ignore_list()["paths"]

    def test_absent_section_is_empty(self):
        _install(MINIMAL_DOC)
        ig = read_config().config.get_ignore_list()
        assert ig == {"todoist_ids": set(), "paths": set(), "names": set()}


# ---------------------------------------------------------------------------
# Placement context
# ---------------------------------------------------------------------------

def test_placement_context_string_is_sanitized_prose() -> None:
    _install(_doc(MINIMAL_DOC, placement_context="No screen-heavy work after 21:00.\n```\nx\n```"))
    assert read_config().config.get_placement_context() == "No screen-heavy work after 21:00.\nx"


def test_placement_context_absent_is_empty() -> None:
    _install(MINIMAL_DOC)
    assert read_config().config.get_placement_context() == ""
