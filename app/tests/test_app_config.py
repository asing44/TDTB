"""Tests for app_config.py + the S0 vault→config.json migration (TDD gate).

S0 is plumbing only: a machine-local app home, a `config.json` loader, a
dual-read in `config_reader.read_config` (config.json wins, vault fallback
when absent), and a one-shot migration tool. Zero behaviour change.

Every test isolates `TDTB_HOME` to a tmp dir — the operator's real vault and
real `~/.config/tdtb` are never read or written.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "tools"))

import app_config  # noqa: E402
import migrate_vault_config  # noqa: E402
from config_reader import CONFIG_REL_PATH, parse_config_markdown, read_config  # noqa: E402


VAULT_FIXTURE = """\
---
description: fixture config — never the operator's real vault
last_updated: 2026-07-01
---

# TDTB Bridger Config

## Defaults

| Key | Value |
|-----|-------|
| eod | 11:45 PM |
| caps.deep | 4 |
| habits.source_directory | 00 - META/Habituals/ |
| habits.fallback_minutes_per_habit | 4 |
| habits.round_to_minutes | 15 |

## Schedulable Defaults

| Block | State | Duration (blocks) | Notes |
|-------|-------|-------------------|-------|
| buffering | on | — | mode: standard |

## Anchored Lifestyle Blocks

| Block | Type | Start | End | Duration | Days | overlap_allowed |
|-------|------|-------|-----|----------|------|-----------------|
| Sudsing | hard | 5:45 PM | — | 30m | daily | no |
| Live | window | 12:00 PM | 8:00 PM | 30m | daily | yes |

## Presets

| Name | Type | Blocks | Priority | Zone | Latest Start |
|------|------|--------|----------|------|--------------|
| Summits | interval | 1 | 4 | any | — |
| Make | interval | 2 | 2 | any | — |

## Color Palette

| Token | Color | Hex | Used for |
|-------|-------|-----|----------|
| event | Soft blue | 7DD3FC | Calendar fixed events |

## Calendar Titles

| Logical name | BusyCal title | Role |
|---|---|---|
| blocks | ⬜ Blocks | Work-window events |
| mint | 🟡 Mint | Minting |

## Calendar Capacity Classes

| BusyCal title | Class | Reason |
|---|---|---|
| Trinoor | work | Work meetings |

## Disabled Calendars

| Title |
|-------|
| Personal |

## Ignore List

### Todoist (by ID)

| ID | Name (ref) | Notes |
|----|------------|-------|
| 6gJm2CCXM7hgXg9j | Post Move | |

### Obsidian (by path)

| Path | Notes |
|------|-------|
| — | populated on demand |

### Names

| Name | Notes |
|------|-------|
| — | matches any source |

## Ranking Criteria

| Key | Value | Controls |
|-----|-------|----------|
| available_recency_days | 7 | x |

## Micro-Adventures

### Rotation

| Key | Value | Controls |
|-----|-------|----------|
| rotation.exclude_window_days | 14 | x |
| rotation.graduate_offer | yes | x |

### Pool

| ID | Idea | Category | Effort | Active |
|----|------|----------|--------|--------|
| ma01 | Walk | nature | low | yes |
| ma02 | Call a friend | social | low | yes |
"""


def _write_vault(vault_root: Path, text: str) -> Path:
    config_path = vault_root / CONFIG_REL_PATH
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(text, encoding="utf-8")
    return config_path


def _write_json(path: Path, doc: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


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
# 2. Dual-read: absent config.json falls back to the vault, identically
# ---------------------------------------------------------------------------

def test_vault_fallback_is_identical_to_today(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("TDTB_HOME", str(tmp_path / "home"))  # no config.json
    vault = tmp_path / "vault"
    _write_vault(vault, VAULT_FIXTURE)

    result = read_config(vault)

    assert result.bootstrap_needed is False
    assert result.config is not None
    assert result.config.sections == parse_config_markdown(VAULT_FIXTURE)
    assert result.config.raw_text == VAULT_FIXTURE
    assert result.config.get_default("habits.source_directory").source == "config"
    assert result.config.get_default("habits.source_directory").value == "00 - META/Habituals/"


def test_missing_vault_and_no_config_still_bootstraps(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("TDTB_HOME", str(tmp_path / "home"))
    result = read_config(tmp_path / "no-such-vault")
    assert result.bootstrap_needed is True
    assert result.config is None


# ---------------------------------------------------------------------------
# 3. Dual-read: config.json wins over the vault when both exist
# ---------------------------------------------------------------------------

def test_config_json_wins_over_vault(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("TDTB_HOME", str(home))
    vault = tmp_path / "vault"
    _write_vault(vault, VAULT_FIXTURE)
    _write_json(
        home / "config.json",
        {
            "version": 1,
            "presets": [
                {
                    "Name": "ConfigPreset",
                    "Type": "interval",
                    "Blocks": 3,
                    "Priority": 4,
                    "Zone": "any",
                    "Latest Start": None,
                }
            ],
        },
    )

    result = read_config(vault)
    assert result.config is not None
    assert result.config.get_presets()[0]["Name"] == "ConfigPreset"
    # A section config.json does not carry still comes from the vault.
    assert "Schedulable Defaults" in result.config.sections


def test_config_json_merges_habits_into_defaults(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("TDTB_HOME", str(home))
    vault = tmp_path / "vault"
    _write_vault(vault, VAULT_FIXTURE)
    _write_json(
        home / "config.json",
        {"version": 1, "habits": {"source_directory": "99 - Elsewhere/"}},
    )

    cfg = read_config(vault).config
    assert cfg is not None
    # Overridden key wins...
    assert cfg.get_default("habits.source_directory").value == "99 - Elsewhere/"
    # ...without dropping the vault's other Defaults keys.
    assert cfg.get_default("eod").value == "11:45 PM"


def test_config_json_only_without_vault(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("TDTB_HOME", str(home))
    _write_json(home / "config.json", {"version": 1, "habits": {"source_directory": "X/"}})

    result = read_config(tmp_path / "empty-vault")
    assert result.bootstrap_needed is False
    assert result.config is not None
    assert result.config.get_default("habits.source_directory").value == "X/"


def test_unsupported_version_falls_back_to_vault(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("TDTB_HOME", str(home))
    vault = tmp_path / "vault"
    _write_vault(vault, VAULT_FIXTURE)
    _write_json(
        home / "config.json",
        {"version": 99, "presets": [{"Name": "ShouldNotWin"}]},
    )

    cfg = read_config(vault).config
    assert cfg is not None
    assert cfg.get_presets()[0]["Name"] == "Summits"


# ---------------------------------------------------------------------------
# 4. Migration tool: fixture round-trip, vault untouched
# ---------------------------------------------------------------------------

def test_migration_round_trips_fixture(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("TDTB_HOME", str(home))
    vault = tmp_path / "vault"
    cfg_path = _write_vault(vault, VAULT_FIXTURE)
    before = cfg_path.read_bytes()

    out = migrate_vault_config.migrate(vault)

    assert out == home / "config.json"
    doc = json.loads(out.read_text(encoding="utf-8"))
    sections = parse_config_markdown(VAULT_FIXTURE)

    assert doc["version"] == 1
    assert doc["presets"] == sections["Presets"]
    assert doc["anchored_blocks"] == sections["Anchored Lifestyle Blocks"]
    assert doc["colors"] == sections["Color Palette"]
    assert doc["calendar"]["titles"] == sections["Calendar Titles"]
    assert doc["calendar"]["capacity_classes"] == sections["Calendar Capacity Classes"]
    assert doc["calendar"]["disabled"] == ["Personal"]
    assert doc["ignore"] == {
        "todoist_ids": ["6gJm2CCXM7hgXg9j"],
        "paths": [],
        "names": [],
    }
    assert doc["habits"]["source_directory"] == "00 - META/Habituals/"
    assert doc["habits"]["fallback_minutes_per_habit"] == 4
    assert doc["micro_adventure_pool"] == sections["Micro-Adventures"]["Pool"]

    # The migration is read-only with respect to the vault file.
    assert cfg_path.read_bytes() == before


def test_migration_then_read_matches_vault_parse(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("TDTB_HOME", str(home))
    vault = tmp_path / "vault"
    _write_vault(vault, VAULT_FIXTURE)

    migrate_vault_config.migrate(vault)
    cfg = read_config(vault).config
    assert cfg is not None

    sections = parse_config_markdown(VAULT_FIXTURE)
    assert cfg.get_presets() == sections["Presets"]
    assert cfg.sections["Anchored Lifestyle Blocks"] == sections["Anchored Lifestyle Blocks"]
    assert cfg.sections["Color Palette"] == sections["Color Palette"]
    assert cfg.sections["Calendar Titles"] == sections["Calendar Titles"]
    assert cfg.sections["Calendar Capacity Classes"] == sections["Calendar Capacity Classes"]
    assert cfg.sections["Micro-Adventures"]["Pool"] == sections["Micro-Adventures"]["Pool"]
    # Rotation survives the overlay (config.json carries only the Pool).
    assert cfg.sections["Micro-Adventures"]["Rotation"] == sections["Micro-Adventures"]["Rotation"]
    assert cfg.get_ignore_list()["todoist_ids"] == {"6gJm2CCXM7hgXg9j"}
    # Non-migrated sections still read from the vault.
    assert cfg.sections["Ranking Criteria"] == sections["Ranking Criteria"]


def test_migration_writes_only_the_config_json_target(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("TDTB_HOME", str(home))
    vault = tmp_path / "vault"
    _write_vault(vault, VAULT_FIXTURE)

    out = migrate_vault_config.migrate(vault, out_path=tmp_path / "explicit.json")

    assert out == tmp_path / "explicit.json"
    assert out.exists()
    assert not (home / "config.json").exists()
    # Only the vault config existed before; the tool adds exactly one file.
    assert sorted(p.name for p in vault.rglob("*") if p.is_file()) == ["tdtb-bridger.md"]
