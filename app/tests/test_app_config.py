"""Tests for app_config.py — the repo config + machine-local override (V1).

The hand-editable config is the tracked ``config/tdtb.json`` with
``$TDTB_HOME/config.json`` layered on top. Every test isolates both paths
(conftest points ``TDTB_HOME`` and ``TDTB_REPO_CONFIG`` at tmp dirs): the
operator's real repo file, vault and ``~/.config/tdtb`` are never read.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import app_config  # noqa: E402
from config_reader import read_config  # noqa: E402


REPO_DOC = {
    "version": 1,
    "defaults": {
        "eod": "11:45 PM",
        "caps": {"deep": 4, "mixed": 3},
        "work_allotment_minutes": 240,
    },
    "habits": {"fallback_minutes_per_habit": 4, "round_to_minutes": 15},
    "presets": [
        {"Name": "Summits", "Type": "interval", "Blocks": 1, "Priority": 4},
    ],
    "micro_adventure_pool": [{"ID": "ma01", "Idea": "Walk", "Active": True}],
    "micro_adventure_rotation": {"exclude_window_days": 10, "graduate_offer": True},
}


def _write_json(path: Path, doc: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def _repo(doc: dict) -> Path:
    return _write_json(app_config.repo_config_path(), doc)


def _local(doc: dict) -> Path:
    return _write_json(app_config.config_path(), doc)


# ---------------------------------------------------------------------------
# 1. Home resolver
# ---------------------------------------------------------------------------

def test_app_home_defaults_to_config_tdtb(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TDTB_HOME", raising=False)
    assert app_config.app_home() == Path.home() / ".config" / "tdtb"


def test_tdtb_home_override_resolves_paths(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / "custom-home"
    monkeypatch.setenv("TDTB_HOME", str(home))
    assert app_config.app_home() == home
    assert app_config.config_path() == home / "config.json"
    assert app_config.state_dir() == home / "state"


def test_empty_tdtb_home_falls_back_to_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TDTB_HOME", "")
    assert app_config.app_home() == Path.home() / ".config" / "tdtb"


def test_state_dir_helper_is_beside_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("TDTB_HOME", str(home))
    assert app_config.state_dir() == app_config.app_home() / "state"
    # The helper only computes a path — it must not create or move anything.
    assert not app_config.state_dir().exists()


# ---------------------------------------------------------------------------
# 2. Repo path resolver
# ---------------------------------------------------------------------------

def test_repo_config_defaults_to_tracked_file(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TDTB_REPO_CONFIG", raising=False)
    path = app_config.repo_config_path()
    assert path.name == "tdtb.json"
    assert path.parent.name == "config"
    assert path == Path(app_config.__file__).resolve().parent.parent / "config" / "tdtb.json"


def test_repo_config_env_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TDTB_REPO_CONFIG", str(tmp_path / "elsewhere.json"))
    assert app_config.repo_config_path() == tmp_path / "elsewhere.json"


# ---------------------------------------------------------------------------
# 3. Layering: repo file first, local override on top
# ---------------------------------------------------------------------------

def test_no_config_anywhere_bootstraps() -> None:
    assert app_config.load_document() is None
    assert app_config.load_sections() == {}
    result = read_config()
    assert result.bootstrap_needed is True
    assert result.config is None


def test_repo_file_alone_is_the_config() -> None:
    _repo(REPO_DOC)
    result = read_config()
    assert result.bootstrap_needed is False
    assert result.config is not None
    assert result.config.get_presets()[0]["Name"] == "Summits"
    assert result.config.get_default("eod").value == "11:45 PM"
    assert result.config.get_default("caps.deep").value == 4
    assert result.config.get_default("habits.fallback_minutes_per_habit").value == 4


def test_local_file_alone_still_loads() -> None:
    _local({"version": 1, "habits": {"fallback_minutes_per_habit": 9}})
    cfg = read_config().config
    assert cfg is not None
    assert cfg.get_default("habits.fallback_minutes_per_habit").value == 9


def test_local_override_wins_and_repo_fills_the_rest() -> None:
    _repo(REPO_DOC)
    _local({
        "version": 1,
        "habits": {"fallback_minutes_per_habit": 7},
        "presets": [{"Name": "LocalPreset", "Type": "interval", "Blocks": 3, "Priority": 4}],
    })
    cfg = read_config().config
    assert cfg is not None
    # Overridden key wins, the repo's other habits key survives (objects merge)...
    assert cfg.get_default("habits.fallback_minutes_per_habit").value == 7
    assert cfg.get_default("habits.round_to_minutes").value == 15
    # ...a list is replaced wholesale, not appended to...
    assert [p["Name"] for p in cfg.get_presets()] == ["LocalPreset"]
    # ...and sections the local file does not carry come from the repo.
    assert cfg.get_default("eod").value == "11:45 PM"


def test_local_sources_join_repo_without_repo_sources() -> None:
    _repo(REPO_DOC)
    _local({"version": 1, "sources": {"mode": "live"}})
    assert app_config.sources_mode() == "live"


def test_unsupported_version_local_is_ignored_repo_still_loads() -> None:
    _repo(REPO_DOC)
    _local({"version": 99, "presets": [{"Name": "ShouldNotWin"}]})
    cfg = read_config().config
    assert cfg is not None
    assert cfg.get_presets()[0]["Name"] == "Summits"


def test_corrupt_repo_file_leaves_local_in_play() -> None:
    app_config.repo_config_path().parent.mkdir(parents=True, exist_ok=True)
    app_config.repo_config_path().write_text("{not json", encoding="utf-8")
    _local({"version": 1, "habits": {"fallback_minutes_per_habit": 5}})
    cfg = read_config().config
    assert cfg is not None
    assert cfg.get_default("habits.fallback_minutes_per_habit").value == 5


def test_both_mode_in_local_rejects_only_the_local_file() -> None:
    _repo(REPO_DOC)
    _local({"version": 1, "sources": {"mode": "both"}, "presets": [{"Name": "No"}]})
    assert app_config.load_document()["presets"][0]["Name"] == "Summits"
    assert app_config.sources_mode() == app_config.DEFAULT_SOURCES_MODE


def test_explicit_path_reads_that_file_only(tmp_path: Path) -> None:
    _repo(REPO_DOC)
    only = _write_json(tmp_path / "only.json", {"version": 1, "presets": []})
    assert app_config.load_document(only) == {"version": 1, "presets": []}


def test_sources_warnings_cover_both_layers() -> None:
    _repo({"version": 1, "sources": {"bogus_repo_key": 1}})
    _local({"version": 1, "sources": {"bogus_local_key": 1}})
    joined = " ".join(app_config.sources_config_warnings())
    assert "bogus_repo_key" in joined
    assert "bogus_local_key" in joined


# ---------------------------------------------------------------------------
# 4. Document -> sections translation (the sections the app consumes)
# ---------------------------------------------------------------------------

def test_defaults_habits_and_read_query_share_one_defaults_section() -> None:
    sections = app_config.sections_from_document({
        "version": 1,
        "defaults": {"eod": "11:45 PM", "buffering": {"standard_pct": 0.2}},
        "habits": {"round_to_minutes": 15},
        "todoist": {"read_query": {"assigned": "today"}},
    })
    defaults = sections["Defaults"]
    assert defaults["eod"] == "11:45 PM"
    assert defaults["buffering.standard_pct"] == 0.2
    assert defaults["buffering"] == {"standard_pct": 0.2}
    assert defaults["habits.round_to_minutes"] == 15
    assert defaults["todoist.read_query.assigned"] == "today"


def test_vault_note_sections_with_a_home_in_the_document() -> None:
    sections = app_config.sections_from_document({
        "version": 1,
        "schedulable_defaults": [{"Block": "minting", "State": "on"}],
        "ranking_criteria": {"within_tier_sort": ["urgency", "overdue"]},
        "placement_context": "No screen-heavy work after 21:00.",
        "schema_reference": {
            "todoist_projects": [{"Project": "PHEP", "ID": "P1"}],
            "todoist_filters": [{"Filter": "⭐ Today", "ID": "F1"}],
        },
        "day_presets": [{"Name": "Workday"}],
        "template_blocks": {"Trinoor Hours": [{"Slot": "Morning"}]},
        "overlap_permissions": "## Overlap Permissions\n\nNo overlap.",
        "colors": [{"Token": "event", "Hex": "7DD3FC"}],
        "micro_adventure_rotation": {"exclude_window_days": 10},
    })
    assert sections["Schedulable Defaults"][0]["Block"] == "minting"
    assert sections["Ranking Criteria"]["within_tier_sort"] == ["urgency", "overdue"]
    assert sections["Placement Context"] == {"_body": "No screen-heavy work after 21:00."}
    assert sections["Schema Reference"]["Todoist Projects"][0]["ID"] == "P1"
    assert sections["Schema Reference"]["Todoist Filters"][0]["ID"] == "F1"
    assert sections["Day Presets"][0]["Name"] == "Workday"
    assert sections["Template Blocks"]["Trinoor Hours"][0]["Slot"] == "Morning"
    assert sections["Overlap Permissions"]["_body"].startswith("## Overlap Permissions")
    assert sections["Color Palette"][0]["Token"] == "event"
    assert sections["Micro-Adventures"]["Rotation"]["rotation.exclude_window_days"] == 10
    assert sections["Micro-Adventures"]["Rotation"]["rotation"] == {"exclude_window_days": 10}


def test_read_config_exposes_schema_reference_and_placement_context() -> None:
    _repo({
        "version": 1,
        "placement_context": "Keep evenings light.",
        "schema_reference": {
            "todoist_projects": [{"Project": "PHEP", "ID": "ROTATED"}],
            "todoist_filters": [{"Filter": "⭐ Today", "ID": "9999"}],
        },
    })
    cfg = read_config().config
    assert cfg.get_placement_context() == "Keep evenings light."
    assert cfg.get_todoist_project_id("PHEP").value == "ROTATED"
    assert cfg.get_todoist_filter_id("Today").value == "9999"
    assert cfg.get_todoist_filter_id("Today").source == "config"


def test_micro_adventure_section_feeds_pool_and_window() -> None:
    import micro_adventure

    _repo(REPO_DOC)
    section = read_config().config.sections["Micro-Adventures"]
    pool = micro_adventure.parse_pool(section)
    assert [p.id for p in pool] == ["ma01"]
    assert micro_adventure.exclude_window_days(section) == 10


# ---------------------------------------------------------------------------
# 5. The tracked repo file itself
# ---------------------------------------------------------------------------

REAL_REPO_CONFIG = Path(app_config.__file__).resolve().parent.parent / "config" / "tdtb.json"


def test_tracked_repo_config_is_valid_and_complete() -> None:
    document = app_config.read_document_file(REAL_REPO_CONFIG)
    assert document is not None
    for key in ("defaults", "schedulable_defaults", "presets", "anchored_blocks",
                "ignore", "calendar", "colors", "micro_adventure_pool", "habits",
                "ranking_criteria", "schema_reference", "day_presets", "template_blocks",
                "overlap_permissions"):
        assert key in document, key


def test_tracked_repo_config_carries_no_machine_settings_or_secrets() -> None:
    text = REAL_REPO_CONFIG.read_text(encoding="utf-8")
    document = json.loads(text)
    assert "sources" not in document
    for forbidden in ("vault_enabled", "TDTB_TOKEN", "api_key", "password", "secret"):
        assert forbidden not in text, forbidden


def test_tracked_repo_config_reads_cleanly_through_read_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TDTB_REPO_CONFIG", str(REAL_REPO_CONFIG))
    result = read_config()
    assert result.bootstrap_needed is False
    assert result.validation is not None
    assert result.validation.valid is True, result.validation
    cfg = result.config
    assert cfg.get_todoist_filter_id("Today").source == "config"
    assert cfg.get_todoist_project_id("PHEP").source == "config"
    assert cfg.get_ranking_criterion("within_tier_sort").source == "config"
    assert cfg.get_micro_adventure_setting("rotation.exclude_window_days").source in ("config", "fallback")
    assert cfg.sections["Overlap Permissions"]["_body"].startswith("## Overlap Permissions")
    assert "habits.source_directory" not in cfg.sections["Defaults"]


def test_vault_note_is_not_a_config_source(tmp_path: Path) -> None:
    note = tmp_path / "00 - META" / "Skill-Configs" / "tdtb-bridger.md"
    note.parent.mkdir(parents=True)
    note.write_text("## Defaults\n| Key | Value |\n|---|---|\n| eod | 1:00 AM |\n", encoding="utf-8")
    result = read_config()
    assert result.bootstrap_needed is True
    assert result.config is None
