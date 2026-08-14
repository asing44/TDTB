"""Tests for day_semantics.py — T18a TDD gate.

Pure config projection for `## Day Presets`, Template zones, allotment
defaults, and the complete raw `## Overlap Permissions` section. No I/O,
no live calls, no billed endpoints.

Invariants:
- Day Presets parse exact `Days` tokens (daily/workdays/weekends/explicit).
- Resolution order: explicit dated override -> unique matching row -> configured default with warning.
- Work allotments are canonical integer minutes divisible by 15.
- Reject ambiguous/malformed definitions deterministically.
- Overlap Permissions prose preserved verbatim.
- Existing task `## Presets` contract unchanged.
"""
from __future__ import annotations

import datetime
import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import day_semantics  # noqa: E402
import config_reader  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FULL_SECTIONS = {
    "Defaults": {
        "eod": "11:59 PM",
        "work_allotment_minutes": 240,
    },
    "Day Presets": [
        {"Name": "Workday", "Days": "workdays", "Zones": "Trinoor Hours", "Work Allotment (min)": 240},
        {"Name": "Weekend", "Days": "weekends", "Zones": "", "Work Allotment (min)": 0},
        {"Name": "Default", "Days": "daily", "Zones": "Trinoor Hours", "Work Allotment (min)": None, "Default": "true"},
    ],
    "Template Blocks": {
        "Trinoor Hours": [
            {"Slot": "Morning", "Start": "8:30 AM", "End": "12:30 PM"},
            {"Slot": "Afternoon", "Start": "1:30 PM", "End": "5:00 PM"},
        ],
        "Press (Gym)": [
            {"Days": "Mon – Thu", "Hours": "5:00 AM – 10:00 PM"},
        ],
    },
    "Overlap Permissions": {
        "_body": "Default for everything is no-overlap.\n\n### Todoist (by ID)\n\n| ID | Name (ref) |\n|----|-------|\n| 6gQVxQXrgh4XQ48v | M1.5 |",
    },
    "Presets": [
        {"Name": "Summits", "Type": "interval", "Blocks": 1, "Priority": 4},
    ],
}

FULL_RAW = """\
## Overlap Permissions

Default for everything is no-overlap.

### Todoist (by ID)

| ID | Name (ref) |
|----|-------|
| 6gQVxQXrgh4XQ48v | M1.5 |
"""


# ---------------------------------------------------------------------------
# Day Presets parsing
# ---------------------------------------------------------------------------

class TestDayPresetsParsing:
    def test_parses_named_rows_with_days_zones_allotment(self):
        proj = day_semantics.project_day_semantics(FULL_SECTIONS, FULL_RAW)
        names = [p.name for p in proj.presets]
        assert names == ["Workday", "Weekend", "Default"]

    def test_days_token_workdays_expands_to_weekday_set(self):
        proj = day_semantics.project_day_semantics(FULL_SECTIONS, FULL_RAW)
        workday = next(p for p in proj.presets if p.name == "Workday")
        assert workday.days == frozenset({"mon", "tue", "wed", "thu", "fri"})

    def test_days_token_weekends_expands_to_weekend_set(self):
        proj = day_semantics.project_day_semantics(FULL_SECTIONS, FULL_RAW)
        weekend = next(p for p in proj.presets if p.name == "Weekend")
        assert weekend.days == frozenset({"sat", "sun"})

    def test_days_token_daily_expands_to_all_seven(self):
        proj = day_semantics.project_day_semantics(FULL_SECTIONS, FULL_RAW)
        default = next(p for p in proj.presets if p.name == "Default")
        assert default.days == frozenset({"mon", "tue", "wed", "thu", "fri", "sat", "sun"})

    def test_days_explicit_list_parses_individual_tokens(self):
        sections = {
            "Day Presets": [
                {"Name": "MWF", "Days": "Mon, Wed, Fri", "Zones": "", "Work Allotment (min)": 60},
            ],
        }
        proj = day_semantics.project_day_semantics(sections, "")
        mwf = proj.presets[0]
        assert mwf.days == frozenset({"mon", "wed", "fri"})

    def test_allotment_integer_minutes_from_row(self):
        proj = day_semantics.project_day_semantics(FULL_SECTIONS, FULL_RAW)
        workday = next(p for p in proj.presets if p.name == "Workday")
        assert workday.work_allotment_minutes == 240

    def test_allotment_zero_is_explicit_disable(self):
        proj = day_semantics.project_day_semantics(FULL_SECTIONS, FULL_RAW)
        weekend = next(p for p in proj.presets if p.name == "Weekend")
        assert weekend.work_allotment_minutes == 0

    def test_allotment_none_inherits_default(self):
        proj = day_semantics.project_day_semantics(FULL_SECTIONS, FULL_RAW)
        default = next(p for p in proj.presets if p.name == "Default")
        assert default.work_allotment_minutes is None

    def test_enabled_zones_parsed_from_comma_separated(self):
        sections = {
            "Day Presets": [
                {"Name": "Full", "Days": "daily", "Zones": "Trinoor Hours, Press (Gym)", "Work Allotment (min)": 120},
            ],
            "Template Blocks": {
                "Trinoor Hours": [{"Slot": "Morning", "Start": "8:30 AM", "End": "12:30 PM"}],
                "Press (Gym)": [{"Days": "Mon – Thu", "Hours": "5:00 AM – 10:00 PM"}],
            },
        }
        proj = day_semantics.project_day_semantics(sections, "")
        full = proj.presets[0]
        assert full.enabled_zones == ["Trinoor Hours", "Press (Gym)"]

    def test_empty_zones_string_is_empty_list(self):
        proj = day_semantics.project_day_semantics(FULL_SECTIONS, FULL_RAW)
        weekend = next(p for p in proj.presets if p.name == "Weekend")
        assert weekend.enabled_zones == []


# ---------------------------------------------------------------------------
# Allotment validation
# ---------------------------------------------------------------------------

class TestAllotmentValidation:
    def test_allotment_not_divisible_by_15_rejected(self):
        sections = {
            "Day Presets": [
                {"Name": "Bad", "Days": "daily", "Zones": "", "Work Allotment (min)": 25},
            ],
        }
        proj = day_semantics.project_day_semantics(sections, "")
        assert proj.errors
        assert any("25" in e and "15" in e for e in proj.errors)

    def test_negative_allotment_rejected(self):
        sections = {
            "Day Presets": [
                {"Name": "Neg", "Days": "daily", "Zones": "", "Work Allotment (min)": -30},
            ],
        }
        proj = day_semantics.project_day_semantics(sections, "")
        assert proj.errors

    def test_default_allotment_from_defaults_section(self):
        proj = day_semantics.project_day_semantics(FULL_SECTIONS, FULL_RAW)
        assert proj.default_allotment_minutes == 240

    def test_default_allotment_fallback_when_defaults_absent(self):
        sections = {"Day Presets": []}
        proj = day_semantics.project_day_semantics(sections, "")
        assert proj.default_allotment_minutes == 0

    def test_default_allotment_fallback_when_key_absent(self):
        sections = {"Defaults": {"eod": "11:59 PM"}}
        proj = day_semantics.project_day_semantics(sections, "")
        assert proj.default_allotment_minutes == 0


# ---------------------------------------------------------------------------
# Template zones
# ---------------------------------------------------------------------------

class TestTemplateZones:
    def test_zones_parsed_from_template_blocks_subsections(self):
        proj = day_semantics.project_day_semantics(FULL_SECTIONS, FULL_RAW)
        assert "Trinoor Hours" in proj.zones
        assert "Press (Gym)" in proj.zones

    def test_zone_intervals_from_start_end_columns(self):
        proj = day_semantics.project_day_semantics(FULL_SECTIONS, FULL_RAW)
        trinoor = proj.zones["Trinoor Hours"]
        assert trinoor.intervals == [("8:30 AM", "12:30 PM"), ("1:30 PM", "5:00 PM")]

    def test_no_template_blocks_section_yields_empty_zones(self):
        sections = {"Day Presets": []}
        proj = day_semantics.project_day_semantics(sections, "")
        assert proj.zones == {}


# ---------------------------------------------------------------------------
# Overlap Permissions prose
# ---------------------------------------------------------------------------

class TestOverlapPermissionsProse:
    def test_raw_prose_preserved_verbatim(self):
        proj = day_semantics.project_day_semantics(FULL_SECTIONS, FULL_RAW)
        assert proj.overlap_permissions_raw == FULL_RAW.strip()

    def test_absent_section_yields_empty_string(self):
        sections = {"Day Presets": []}
        proj = day_semantics.project_day_semantics(sections, "")
        assert proj.overlap_permissions_raw == ""


# ---------------------------------------------------------------------------
# Day resolution
# ---------------------------------------------------------------------------

class TestDayResolution:
    def test_explicit_dated_override_wins(self):
        proj = day_semantics.project_day_semantics(FULL_SECTIONS, FULL_RAW)
        monday = datetime.date(2026, 7, 20)  # Monday
        resolved = day_semantics.resolve_day(proj, monday, dated_override="Weekend")
        assert resolved.preset.name == "Weekend"
        assert resolved.resolution_source == "dated_override"

    def test_matched_row_for_workday(self):
        proj = day_semantics.project_day_semantics(FULL_SECTIONS, FULL_RAW)
        monday = datetime.date(2026, 7, 20)  # Monday
        resolved = day_semantics.resolve_day(proj, monday)
        assert resolved.preset.name == "Workday"
        assert resolved.resolution_source == "matched_row"

    def test_matched_row_for_weekend(self):
        proj = day_semantics.project_day_semantics(FULL_SECTIONS, FULL_RAW)
        saturday = datetime.date(2026, 7, 25)  # Saturday
        resolved = day_semantics.resolve_day(proj, saturday)
        assert resolved.preset.name == "Weekend"
        assert resolved.resolution_source == "matched_row"

    def test_daily_row_matches_any_day(self):
        sections = {
            "Day Presets": [
                {"Name": "Everyday", "Days": "daily", "Zones": "", "Work Allotment (min)": 60},
            ],
        }
        proj = day_semantics.project_day_semantics(sections, "")
        for d in [datetime.date(2026, 7, 20), datetime.date(2026, 7, 25)]:
            resolved = day_semantics.resolve_day(proj, d)
            assert resolved.preset.name == "Everyday"

    def test_no_match_falls_back_to_configured_default_with_warning(self):
        sections = {
            "Day Presets": [
                {"Name": "Workday", "Days": "workdays", "Zones": "", "Work Allotment (min)": 240},
            ],
        }
        proj = day_semantics.project_day_semantics(sections, "")
        proj.configured_default = "Workday"
        saturday = datetime.date(2026, 7, 25)
        resolved = day_semantics.resolve_day(proj, saturday)
        assert resolved.preset.name == "Workday"
        assert resolved.resolution_source == "configured_default"
        assert resolved.warnings

    def test_no_match_no_default_returns_none_with_warning(self):
        sections = {
            "Day Presets": [
                {"Name": "Workday", "Days": "workdays", "Zones": "", "Work Allotment (min)": 240},
            ],
        }
        proj = day_semantics.project_day_semantics(sections, "")
        proj.configured_default = None
        saturday = datetime.date(2026, 7, 25)
        resolved = day_semantics.resolve_day(proj, saturday)
        assert resolved.preset is None
        assert resolved.resolution_source == "fallback"
        assert resolved.warnings

    def test_allotment_from_matched_row(self):
        proj = day_semantics.project_day_semantics(FULL_SECTIONS, FULL_RAW)
        monday = datetime.date(2026, 7, 20)
        resolved = day_semantics.resolve_day(proj, monday)
        assert resolved.work_allotment_minutes == 240

    def test_allotment_none_inherits_default(self):
        proj = day_semantics.project_day_semantics(FULL_SECTIONS, FULL_RAW)
        wednesday = datetime.date(2026, 7, 22)  # Wednesday -> matches Workday
        # Make Default the only row with None allotment to isolate the inherit path
        sections = {
            "Defaults": {"work_allotment_minutes": 180},
            "Day Presets": [
                {"Name": "Inherit", "Days": "daily", "Zones": "", "Work Allotment (min)": None},
            ],
        }
        proj2 = day_semantics.project_day_semantics(sections, "")
        resolved = day_semantics.resolve_day(proj2, wednesday)
        assert resolved.work_allotment_minutes == 180

    def test_zero_allotment_disables_mint(self):
        proj = day_semantics.project_day_semantics(FULL_SECTIONS, FULL_RAW)
        saturday = datetime.date(2026, 7, 25)
        resolved = day_semantics.resolve_day(proj, saturday)
        assert resolved.work_allotment_minutes == 0
        assert resolved.mint_enabled is False

    def test_positive_allotment_enables_mint(self):
        proj = day_semantics.project_day_semantics(FULL_SECTIONS, FULL_RAW)
        monday = datetime.date(2026, 7, 20)
        resolved = day_semantics.resolve_day(proj, monday)
        assert resolved.mint_enabled is True

    def test_enabled_zones_resolved_to_zone_specs(self):
        proj = day_semantics.project_day_semantics(FULL_SECTIONS, FULL_RAW)
        monday = datetime.date(2026, 7, 20)
        resolved = day_semantics.resolve_day(proj, monday)
        zone_names = [z.name for z in resolved.enabled_zones]
        assert "Trinoor Hours" in zone_names

    def test_dated_override_unknown_name_warns(self):
        proj = day_semantics.project_day_semantics(FULL_SECTIONS, FULL_RAW)
        monday = datetime.date(2026, 7, 20)
        resolved = day_semantics.resolve_day(proj, monday, dated_override="Nonexistent")
        assert resolved.warnings
        assert any("Nonexistent" in w for w in resolved.warnings)


# ---------------------------------------------------------------------------
# Ambiguous / malformed rejection
# ---------------------------------------------------------------------------

class TestAmbiguousMalformed:
    def test_duplicate_preset_names_rejected(self):
        sections = {
            "Day Presets": [
                {"Name": "Workday", "Days": "workdays", "Zones": "", "Work Allotment (min)": 240},
                {"Name": "Workday", "Days": "workdays", "Zones": "", "Work Allotment (min)": 120},
            ],
        }
        proj = day_semantics.project_day_semantics(sections, "")
        assert proj.errors
        assert any("duplicate" in e.lower() for e in proj.errors)

    def test_unknown_days_token_rejected(self):
        sections = {
            "Day Presets": [
                {"Name": "Bad", "Days": "funday", "Zones": "", "Work Allotment (min)": 60},
            ],
        }
        proj = day_semantics.project_day_semantics(sections, "")
        assert proj.errors
        assert any("funday" in e for e in proj.errors)

    def test_missing_name_rejected(self):
        sections = {
            "Day Presets": [
                {"Days": "daily", "Zones": "", "Work Allotment (min)": 60},
            ],
        }
        proj = day_semantics.project_day_semantics(sections, "")
        assert proj.errors

    def test_missing_days_rejected(self):
        sections = {
            "Day Presets": [
                {"Name": "NoDays", "Zones": "", "Work Allotment (min)": 60},
            ],
        }
        proj = day_semantics.project_day_semantics(sections, "")
        assert proj.errors

    def test_multiple_specific_rows_match_same_day_rejected_as_ambiguous(self):
        sections = {
            "Day Presets": [
                {"Name": "A", "Days": "workdays", "Zones": "", "Work Allotment (min)": 60},
                {"Name": "B", "Days": "Mon, Tue, Wed, Thu, Fri", "Zones": "", "Work Allotment (min)": 120},
            ],
        }
        proj = day_semantics.project_day_semantics(sections, "")
        monday = datetime.date(2026, 7, 20)  # Monday — matches both specific rows
        resolved = day_semantics.resolve_day(proj, monday)
        # Ambiguous match falls back with a warning
        assert resolved.warnings
        assert any("ambig" in w.lower() for w in resolved.warnings)


# ---------------------------------------------------------------------------
# Existing Presets contract unchanged
# ---------------------------------------------------------------------------

class TestPresetsContractUnchanged:
    def test_task_presets_section_not_consumed(self):
        sections = {
            "Presets": [
                {"Name": "Summits", "Type": "interval", "Blocks": 1, "Priority": 4},
            ],
            "Day Presets": [],
        }
        proj = day_semantics.project_day_semantics(sections, "")
        # Day Presets projection does not touch the task Presets section
        assert not hasattr(proj, "task_presets") or proj.task_presets is None


# ---------------------------------------------------------------------------
# Empty / absent sections
# ---------------------------------------------------------------------------

class TestEmptyAbsent:
    def test_no_day_presets_section_yields_empty_list(self):
        proj = day_semantics.project_day_semantics({}, "")
        assert proj.presets == []
        assert proj.errors == []

    def test_resolve_day_with_no_presets_returns_fallback(self):
        proj = day_semantics.project_day_semantics({}, "")
        resolved = day_semantics.resolve_day(proj, datetime.date(2026, 7, 20))
        assert resolved.preset is None
        assert resolved.resolution_source == "fallback"
        assert resolved.work_allotment_minutes == 0


# ---------------------------------------------------------------------------
# T18b.1 — projection seam closure
# ---------------------------------------------------------------------------

class TestT18b1ProjectionSeams:
    """T18b.1: derive configured default from the `Default` column, reject
    malformed defaults and invalid Defaults.work_allotment_minutes, keep a
    missing section backward-compatible, and remove caller-injected default
    selection from normal production use."""

    def test_default_column_truthy_marks_configured_default(self):
        sections = {
            "Day Presets": [
                {"Name": "Workday", "Days": "workdays", "Zones": "", "Work Allotment (min)": 240, "Default": "true"},
                {"Name": "Weekend", "Days": "weekends", "Zones": "", "Work Allotment (min)": 0},
            ],
        }
        proj = day_semantics.project_day_semantics(sections, "")
        assert proj.configured_default == "Workday"
        assert not proj.errors

    def test_zero_defaults_when_section_present_rejected(self):
        sections = {
            "Day Presets": [
                {"Name": "Workday", "Days": "workdays", "Zones": "", "Work Allotment (min)": 240},
                {"Name": "Weekend", "Days": "weekends", "Zones": "", "Work Allotment (min)": 0},
            ],
        }
        proj = day_semantics.project_day_semantics(sections, "")
        assert proj.errors
        assert any("default" in e.lower() for e in proj.errors)

    def test_multiple_defaults_rejected(self):
        sections = {
            "Day Presets": [
                {"Name": "A", "Days": "workdays", "Zones": "", "Work Allotment (min)": 240, "Default": "true"},
                {"Name": "B", "Days": "weekends", "Zones": "", "Work Allotment (min)": 0, "Default": "yes"},
            ],
        }
        proj = day_semantics.project_day_semantics(sections, "")
        assert proj.errors
        assert any("default" in e.lower() for e in proj.errors)

    def test_unknown_default_value_rejected(self):
        sections = {
            "Day Presets": [
                {"Name": "A", "Days": "workdays", "Zones": "", "Work Allotment (min)": 240, "Default": "maybe"},
            ],
        }
        proj = day_semantics.project_day_semantics(sections, "")
        assert proj.errors
        assert any("default" in e.lower() for e in proj.errors)

    def test_invalid_defaults_work_allotment_minutes_rejected(self):
        sections = {
            "Defaults": {"work_allotment_minutes": 25},
            "Day Presets": [
                {"Name": "A", "Days": "workdays", "Zones": "", "Work Allotment (min)": 240, "Default": "true"},
            ],
        }
        proj = day_semantics.project_day_semantics(sections, "")
        assert proj.errors
        assert any("work_allotment_minutes" in e or "allotment" in e.lower() for e in proj.errors)

    def test_negative_defaults_work_allotment_minutes_rejected(self):
        sections = {
            "Defaults": {"work_allotment_minutes": -60},
            "Day Presets": [
                {"Name": "A", "Days": "workdays", "Zones": "", "Work Allotment (min)": 240, "Default": "true"},
            ],
        }
        proj = day_semantics.project_day_semantics(sections, "")
        assert proj.errors
        assert any("allotment" in e.lower() for e in proj.errors)

    def test_missing_day_presets_section_backward_compatible(self):
        # No ## Day Presets section at all: no errors, no presets, default allotment
        # still read from Defaults. Preserves pre-T18k live config (no section yet).
        sections = {"Defaults": {"work_allotment_minutes": 240}}
        proj = day_semantics.project_day_semantics(sections, "")
        assert proj.presets == []
        assert proj.errors == []
        assert proj.configured_default is None
        assert proj.default_allotment_minutes == 240

    def test_missing_section_with_invalid_defaults_allotment_still_rejects(self):
        sections = {"Defaults": {"work_allotment_minutes": 25}}
        proj = day_semantics.project_day_semantics(sections, "")
        # Even without Day Presets, an invalid Defaults allotment is a config error.
        assert proj.errors
        assert any("allotment" in e.lower() for e in proj.errors)

    def test_caller_injected_default_parameter_removed(self):
        # The configured_default kwarg is gone: normal production use derives the
        # default from the Default column only. Test that passing it raises.
        sections = {
            "Day Presets": [
                {"Name": "A", "Days": "workdays", "Zones": "", "Work Allotment (min)": 240, "Default": "true"},
            ],
        }
        import pytest
        with pytest.raises(TypeError):
            day_semantics.project_day_semantics(sections, "", configured_default="A")

    def test_default_column_falsey_does_not_mark(self):
        sections = {
            "Day Presets": [
                {"Name": "A", "Days": "workdays", "Zones": "", "Work Allotment (min)": 240, "Default": "no"},
                {"Name": "B", "Days": "weekends", "Zones": "", "Work Allotment (min)": 0, "Default": "true"},
            ],
        }
        proj = day_semantics.project_day_semantics(sections, "")
        assert proj.configured_default == "B"
        assert not proj.errors

    def test_default_column_empty_string_is_falsey(self):
        sections = {
            "Day Presets": [
                {"Name": "A", "Days": "workdays", "Zones": "", "Work Allotment (min)": 240, "Default": ""},
                {"Name": "B", "Days": "weekends", "Zones": "", "Work Allotment (min)": 0, "Default": "true"},
            ],
        }
        proj = day_semantics.project_day_semantics(sections, "")
        assert proj.configured_default == "B"
        assert not proj.errors

    def test_default_column_dash_is_falsey(self):
        sections = {
            "Day Presets": [
                {"Name": "A", "Days": "workdays", "Zones": "", "Work Allotment (min)": 240, "Default": "—"},
                {"Name": "B", "Days": "weekends", "Zones": "", "Work Allotment (min)": 0, "Default": "true"},
            ],
        }
        proj = day_semantics.project_day_semantics(sections, "")
        assert proj.configured_default == "B"
        assert not proj.errors


# ---------------------------------------------------------------------------
# T18b.3 — resolved-contract helper (pure, JSON-safe)
# ---------------------------------------------------------------------------

class TestT18b3ResolvedContract:
    """T18b.3: one pure helper that takes ConfigReadResult (or sections + raw
    text), a date, and dated overrides, and returns a JSON-safe resolved
    contract. Dated allotment override applies AFTER preset resolution:
    absent/None uses the preset/config result; 0 disables Mint; positive int
    overrides."""

    def _sections(self):
        return {
            "Defaults": {"work_allotment_minutes": 180},
            "Day Presets": [
                {"Name": "Workday", "Days": "workdays", "Zones": "Trinoor Hours",
                 "Work Allotment (min)": 240},
                {"Name": "Weekend", "Days": "weekends", "Zones": "",
                 "Work Allotment (min)": 0},
                {"Name": "Default", "Days": "daily", "Zones": "Trinoor Hours",
                 "Work Allotment (min)": None, "Default": "true"},
            ],
            "Template Blocks": {
                "Trinoor Hours": [
                    {"Slot": "Morning", "Start": "8:30 AM", "End": "12:30 PM"},
                ],
            },
        }

    def _raw(self):
        return (
            "## Overlap Permissions\n\n"
            "Default is no-overlap.\n"
        )

    def test_accepts_one_config_read_result(self):
        """The helper boundary is the reader result, not its parsed internals."""
        raw = self._raw()
        result = config_reader.ConfigReadResult(
            config=config_reader.TdtbConfig(self._sections(), raw),
            bootstrap_needed=False,
            validation=None,
        )

        contract = day_semantics.resolve_day_contract(
            result, datetime.date(2026, 7, 20),
        )

        assert contract["selected_preset"]["name"] == "Workday"

    def test_contract_shape_json_safe(self):
        contract = day_semantics.resolve_day_contract(
            self._sections(), self._raw(),
            datetime.date(2026, 7, 20),  # Monday
        )
        # JSON-safe: every value is a primitive, list, or dict
        json.dumps(contract)
        for key in ("available_presets", "selected_preset", "resolution_source",
                    "enabled_zones", "effective_allotment_minutes",
                    "default_allotment_minutes", "mint_enabled", "warnings",
                    "errors", "overlap_permissions_raw"):
            assert key in contract, key

    def test_available_presets_listed_with_fields(self):
        contract = day_semantics.resolve_day_contract(
            self._sections(), self._raw(), datetime.date(2026, 7, 20))
        names = [p["name"] for p in contract["available_presets"]]
        assert names == ["Workday", "Weekend", "Default"]
        workday = contract["available_presets"][0]
        assert workday["days"] == ["mon", "tue", "wed", "thu", "fri"]
        assert workday["enabled_zones"] == ["Trinoor Hours"]
        assert workday["work_allotment_minutes"] == 240

    def test_selected_preset_for_monday_is_workday(self):
        contract = day_semantics.resolve_day_contract(
            self._sections(), self._raw(), datetime.date(2026, 7, 20))
        assert contract["selected_preset"]["name"] == "Workday"
        assert contract["resolution_source"] == "matched_row"

    def test_selected_preset_for_saturday_is_weekend(self):
        contract = day_semantics.resolve_day_contract(
            self._sections(), self._raw(), datetime.date(2026, 7, 25))
        assert contract["selected_preset"]["name"] == "Weekend"
        assert contract["resolution_source"] == "matched_row"

    def test_enabled_zones_resolved_to_intervals(self):
        contract = day_semantics.resolve_day_contract(
            self._sections(), self._raw(), datetime.date(2026, 7, 20))
        zones = contract["enabled_zones"]
        assert len(zones) == 1
        assert zones[0]["name"] == "Trinoor Hours"
        assert zones[0]["intervals"] == [["8:30 AM", "12:30 PM"]]

    def test_effective_allotment_uses_preset_value(self):
        # Workday has its own allotment = 240; no dated override
        contract = day_semantics.resolve_day_contract(
            self._sections(), self._raw(), datetime.date(2026, 7, 20))
        assert contract["effective_allotment_minutes"] == 240
        assert contract["mint_enabled"] is True

    def test_effective_allotment_inherits_config_default_when_preset_none(self):
        # Default preset has work_allotment_minutes = None → inherits config 180
        # Force a Sunday (matches Default via fallback since no Weekend match on Sunday)
        # Actually Weekend matches Sun. Use a date that only matches Default.
        # Make a sections where only Default exists:
        sections = {
            "Defaults": {"work_allotment_minutes": 180},
            "Day Presets": [
                {"Name": "Everyday", "Days": "daily", "Zones": "",
                 "Work Allotment (min)": None, "Default": "true"},
            ],
        }
        contract = day_semantics.resolve_day_contract(
            sections, "", datetime.date(2026, 7, 20))
        assert contract["selected_preset"]["name"] == "Everyday"
        assert contract["effective_allotment_minutes"] == 180
        assert contract["default_allotment_minutes"] == 180

    def test_dated_allotment_override_positive_wins(self):
        contract = day_semantics.resolve_day_contract(
            self._sections(), self._raw(), datetime.date(2026, 7, 20),
            dated_overrides={"work_allotment_minutes": 300},
        )
        assert contract["effective_allotment_minutes"] == 300
        assert contract["mint_enabled"] is True

    def test_dated_allotment_override_zero_disables_mint(self):
        contract = day_semantics.resolve_day_contract(
            self._sections(), self._raw(), datetime.date(2026, 7, 20),
            dated_overrides={"work_allotment_minutes": 0},
        )
        assert contract["effective_allotment_minutes"] == 0
        assert contract["mint_enabled"] is False
        assert contract["enabled_zones"] == []
        # Even though the Workday preset would enable Mint, the dated 0 wins.

    def test_equivalent_enabled_zone_intervals_are_deduplicated(self):
        sections = self._sections()
        sections["Template Blocks"]["Trinoor Hours"].append(
            {"Slot": "Duplicate", "Start": "8:30 AM", "End": "12:30 PM"}
        )
        contract = day_semantics.resolve_day_contract(
            sections, self._raw(), datetime.date(2026, 7, 20),
        )
        assert contract["enabled_zones"][0]["intervals"] == [
            ["8:30 AM", "12:30 PM"]
        ]

    def test_dated_allotment_override_absent_uses_preset_resolution(self):
        # No dated override → preset resolution result (240 from Workday)
        contract = day_semantics.resolve_day_contract(
            self._sections(), self._raw(), datetime.date(2026, 7, 20),
            dated_overrides=None,
        )
        assert contract["effective_allotment_minutes"] == 240

    def test_dated_allotment_override_none_uses_preset_resolution(self):
        # dated_overrides has work_allotment_minutes = None → same as absent
        contract = day_semantics.resolve_day_contract(
            self._sections(), self._raw(), datetime.date(2026, 7, 20),
            dated_overrides={"work_allotment_minutes": None},
        )
        assert contract["effective_allotment_minutes"] == 240

    def test_dated_preset_override_wins(self):
        # Saturday normally matches Weekend; override to Workday
        contract = day_semantics.resolve_day_contract(
            self._sections(), self._raw(), datetime.date(2026, 7, 25),
            dated_overrides={"day_preset": "Workday"},
        )
        assert contract["selected_preset"]["name"] == "Workday"
        assert contract["resolution_source"] == "dated_override"
        assert contract["effective_allotment_minutes"] == 240

    def test_dated_preset_override_with_allotment_override(self):
        contract = day_semantics.resolve_day_contract(
            self._sections(), self._raw(), datetime.date(2026, 7, 25),
            dated_overrides={"day_preset": "Workday", "work_allotment_minutes": 60},
        )
        assert contract["selected_preset"]["name"] == "Workday"
        assert contract["effective_allotment_minutes"] == 60

    def test_errors_propagated_from_projection(self):
        sections = {
            "Day Presets": [
                {"Name": "A", "Days": "workdays", "Zones": "", "Work Allotment (min)": 240},
                {"Name": "B", "Days": "workdays", "Zones": "", "Work Allotment (min)": 120},
            ],
        }
        contract = day_semantics.resolve_day_contract(
            sections, "", datetime.date(2026, 7, 20))
        assert contract["errors"]
        assert any("default" in e.lower() for e in contract["errors"])

    def test_warnings_propagated(self):
        # Dated override to a nonexistent preset warns
        contract = day_semantics.resolve_day_contract(
            self._sections(), self._raw(), datetime.date(2026, 7, 20),
            dated_overrides={"day_preset": "Nonexistent"},
        )
        assert contract["warnings"]
        assert any("Nonexistent" in w for w in contract["warnings"])

    def test_overlap_permissions_raw_preserved(self):
        contract = day_semantics.resolve_day_contract(
            self._sections(), self._raw(), datetime.date(2026, 7, 20))
        assert contract["overlap_permissions_raw"] == self._raw().strip()

    def test_missing_day_presets_section_backward_compatible(self):
        # No ## Day Presets → no presets, no errors, no selected_preset
        sections = {"Defaults": {"work_allotment_minutes": 240}}
        contract = day_semantics.resolve_day_contract(
            sections, "", datetime.date(2026, 7, 20))
        assert contract["available_presets"] == []
        assert contract["selected_preset"] is None
        assert contract["errors"] == []
        assert contract["effective_allotment_minutes"] == 240  # from Defaults

    def test_mint_enabled_false_when_allotment_zero(self):
        # Weekend preset has allotment = 0
        contract = day_semantics.resolve_day_contract(
            self._sections(), self._raw(), datetime.date(2026, 7, 25))
        assert contract["selected_preset"]["name"] == "Weekend"
        assert contract["effective_allotment_minutes"] == 0
        assert contract["mint_enabled"] is False

    def test_contract_stable_across_calls(self):
        """Same inputs → same output (deterministic, no hidden state)."""
        c1 = day_semantics.resolve_day_contract(
            self._sections(), self._raw(), datetime.date(2026, 7, 20))
        c2 = day_semantics.resolve_day_contract(
            self._sections(), self._raw(), datetime.date(2026, 7, 20))
        assert c1 == c2


# ---------------------------------------------------------------------------
# T18b.4 — planning-config fingerprint
# ---------------------------------------------------------------------------

class TestT18b4PlanningConfigFingerprint:
    """The digest pins planning semantics, not parser or source noise."""

    def _sections(self):
        return {
            "Defaults": {"work_allotment_minutes": 180, "eod": "11:59 PM"},
            "Day Presets": [
                {"Name": "Workday", "Days": "workdays", "Zones": "Trinoor Hours",
                 "Work Allotment (min)": 240, "Default": "true"},
                {"Name": "Weekend", "Days": "weekends", "Zones": "Press (Gym)",
                 "Work Allotment (min)": 0, "Default": ""},
            ],
            "Template Blocks": {
                "Trinoor Hours": [
                    {"Start": "1:30 PM", "End": "5:00 PM"},
                    {"Start": "8:30 AM", "End": "12:30 PM"},
                ],
                "Press (Gym)": [
                    {"Hours": "5:00 AM – 10:00 PM"},
                ],
            },
            "Presets": [{"Name": "Unrelated task preset", "Blocks": 1}],
        }

    def _result(self, sections=None, raw=None):
        return config_reader.ConfigReadResult(
            config=config_reader.TdtbConfig(
                sections or self._sections(),
                raw or "## Overlap Permissions\n\nNo overlap by default.\n",
            ),
            bootstrap_needed=False,
            validation=None,
        )

    def _fingerprint(self, sections=None, raw=None, *, overrides=None):
        return day_semantics.planning_config_fingerprint(
            self._result(sections, raw),
            datetime.date(2026, 7, 20),
            dated_overrides=overrides,
        )

    def test_row_dict_and_interval_order_noise_is_stable(self):
        reordered = copy.deepcopy(self._sections())
        reordered["Day Presets"].reverse()
        reordered["Template Blocks"]["Trinoor Hours"].reverse()
        reordered["Template Blocks"] = dict(reversed(reordered["Template Blocks"].items()))
        reordered["Day Presets"] = [dict(reversed(row.items())) for row in reordered["Day Presets"]]

        assert self._fingerprint() == self._fingerprint(reordered)

    def test_deduplicated_equivalent_zone_intervals_are_stable(self):
        duplicate = copy.deepcopy(self._sections())
        duplicate["Template Blocks"]["Trinoor Hours"].append(
            {"Start": "8:30 AM", "End": "12:30 PM"},
        )

        assert self._fingerprint() == self._fingerprint(duplicate)

    def test_semantic_preset_zone_allotment_policy_and_choice_changes_digest(self):
        baseline = self._fingerprint()

        changed_preset = copy.deepcopy(self._sections())
        changed_preset["Day Presets"][0]["Work Allotment (min)"] = 300
        changed_zone = copy.deepcopy(self._sections())
        changed_zone["Template Blocks"]["Trinoor Hours"][0]["End"] = "5:30 PM"
        changed_default = copy.deepcopy(self._sections())
        changed_default["Defaults"]["work_allotment_minutes"] = 210
        changed_configured_default = copy.deepcopy(self._sections())
        changed_configured_default["Day Presets"][0]["Default"] = ""
        changed_configured_default["Day Presets"][1]["Default"] = "true"
        changed_policy = "## Overlap Permissions\n\nAllow companions only.\n"

        for changed in (
            self._fingerprint(changed_preset),
            self._fingerprint(changed_zone),
            self._fingerprint(changed_default),
            self._fingerprint(changed_configured_default),
            self._fingerprint(raw=changed_policy),
            self._fingerprint(overrides={"work_allotment_minutes": 300}),
            self._fingerprint(overrides={"day_preset": "Weekend"}),
        ):
            assert changed != baseline

    def test_unrelated_source_data_does_not_change_digest(self):
        unrelated = copy.deepcopy(self._sections())
        unrelated["Presets"] = [{"Name": "Changed task preset", "Blocks": 99}]
        unrelated["Defaults"]["eod"] = "10:00 PM"

        assert self._fingerprint() == self._fingerprint(unrelated)
