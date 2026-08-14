"""T4 (cockpit-overhaul): durable vault duration resolution.

Locked decision 14 precedence for an assigned row's ``blocks``:
Todoist-native duration → name-matched ``## Presets`` row → contract-defined
type field (currently press ``duration_min``) → 1 block. Explicit zero from a
matched source stays 0 (background rows); only an absent/unparseable value
falls through. Pure resolver + /plan-inputs enrichment only — no vault writes,
no schema fields.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))
import main as main_mod  # noqa: E402

CONFIG_REL_PATH = "00 - META/Skill-Configs/tdtb-bridger.md"

PRESETS = [
    {"Name": "Press", "Type": "interval", "Blocks": "2.5", "Priority": "3"},
    {"Name": "Make", "Type": "interval", "Blocks": "2", "Priority": "2"},
    {"Name": "Zero Ritual", "Type": "interval", "Blocks": "0", "Priority": "1"},
    {"Name": "Broken", "Type": "interval", "Blocks": "—", "Priority": "1"},
]


def _resolve(item, presets=PRESETS, fm=None):
    return main_mod.resolve_assigned_blocks(item, presets, fm)


class TestTodoistNative:
    def test_native_minutes_round_up_to_blocks(self):
        assert _resolve({"name": "Call Vlad", "duration": 90}) == 3
        assert _resolve({"name": "Call Vlad", "duration": 75}) == 3
        assert _resolve({"name": "Call Vlad", "duration": 10}) == 1

    def test_explicit_zero_stays_zero(self):
        assert _resolve({"name": "Background ping", "duration": 0}) == 0

    def test_native_beats_preset_match(self):
        # A Todoist task named like a preset keeps its own duration.
        assert _resolve({"name": "Press", "duration": 30}) == 1

    def test_none_duration_falls_through(self):
        assert _resolve({"name": "Make", "duration": None}) == 2

    def test_unparseable_duration_falls_through_to_default(self):
        assert _resolve({"name": "Odd", "duration": "soon"}) == 1


class TestPresetMatch:
    def test_fractional_blocks_kept(self):
        assert _resolve({"name": "Press"}) == 2.5

    def test_integral_blocks_are_int(self):
        assert _resolve({"name": "Make"}) == 2

    def test_match_is_case_and_whitespace_insensitive(self):
        assert _resolve({"name": "  make "}) == 2

    def test_explicit_zero_blocks_stays_zero(self):
        assert _resolve({"name": "Zero Ritual"}) == 0

    def test_malformed_blocks_falls_through(self):
        assert _resolve({"name": "Broken"}) == 1

    def test_preset_beats_type_field(self):
        fm = {"type": ["press"], "duration_min": 240}
        assert _resolve({"name": "Press", "types": ["press"]}, fm=fm) == 2.5


class TestTypeContractField:
    def test_press_duration_min_rounds_up(self):
        fm = {"type": ["press"], "duration_min": 75}
        assert _resolve({"name": "Guitar", "types": ["press"]}, fm=fm) == 3

    def test_press_zero_stays_zero(self):
        fm = {"type": ["press"], "duration_min": 0}
        assert _resolve({"name": "Guitar", "types": ["press"]}, fm=fm) == 0

    def test_press_without_field_defaults(self):
        assert _resolve({"name": "Guitar", "types": ["press"]}, fm={"type": ["press"]}) == 1

    def test_press_unparseable_field_defaults(self):
        fm = {"type": ["press"], "duration_min": "a while"}
        assert _resolve({"name": "Guitar", "types": ["press"]}, fm=fm) == 1

    def test_non_press_type_ignores_field(self):
        fm = {"type": ["project"], "duration_min": 240}
        assert _resolve({"name": "Garage", "types": ["project"]}, fm=fm) == 1

    def test_missing_fm_defaults(self):
        assert _resolve({"name": "Guitar", "types": ["press"]}, fm=None) == 1


class TestDefault:
    def test_plain_vault_item_is_one_block(self):
        assert _resolve({"name": "Garage Buildout", "types": ["project"]}) == 1

    def test_no_presets_no_fm(self):
        assert _resolve({"name": "Anything"}, presets=[]) == 1


# ---------------------------------------------------------------------------
# /plan-inputs enrichment
# ---------------------------------------------------------------------------

CONFIG_WITH_PRESETS = """\
---
description: test config
last_updated: 2026-07-01
---

# TDTB Bridger Config

## Defaults

| Key | Value    |
| --- | -------- |
| eod | 11:45 PM |

## Presets

| Name | Type | Blocks | Priority |
|------|------|--------|----------|
| Make | interval | 2 | 2 |
"""


class FakeTodoist:
    def __init__(self, by_query):
        self.by_query = by_query

    def get_filter_tasks(self, query, limit=None):
        return self.by_query.get(query, [])


class FakeStore:
    def query_events(self, start, end, calendar_ids=None):
        return []

    def calendars(self):
        return [{"id": "CAL-X", "title": "Fixture"}]


@pytest.fixture
def vault(tmp_path) -> Path:
    v = tmp_path / "vault-root"
    v.mkdir()
    p = v / CONFIG_REL_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(CONFIG_WITH_PRESETS, encoding="utf-8")
    proj = v / "50 - Operations" / "Projects"
    proj.mkdir(parents=True)
    (proj / "Make.md").write_text(
        "---\ntype: project\nassigned: true\n---\nbody\n", encoding="utf-8"
    )
    (proj / "Garage Buildout.md").write_text(
        "---\ntype: project\nassigned: true\n---\nbody\n", encoding="utf-8"
    )
    (proj / "Guitar Reps.md").write_text(
        "---\ntype: press\nassigned: true\nduration_min: 75\n---\nbody\n",
        encoding="utf-8",
    )
    return v


def _client(vault, todoist=None) -> TestClient:
    app = main_mod.create_app(vault_root=vault)
    app.state.build_read_clients = lambda v, cfg: (todoist, FakeStore())
    return TestClient(app)


def _assigned_by_name(body):
    return {i["name"]: i for i in body["digest"]["assigned"]}

def test_plan_inputs_assigned_rows_carry_resolved_blocks(vault):
    import external_sources as ext

    todoist = FakeTodoist({
        ext.ASSIGNED_QUERY_FALLBACK: [
            {"id": "1", "content": "Call Vlad", "priority": 4,
             "duration": {"unit": "minute", "amount": 90}, "labels": []},
        ],
        ext.QUICK_QUERY_FALLBACK: [],
    })
    body = _client(vault, todoist=todoist).get("/plan-inputs").json()
    rows = _assigned_by_name(body)
    assert rows["Call Vlad"]["blocks"] == 3       # Todoist-native 90m
    assert rows["Make"]["blocks"] == 2            # preset name match
    assert rows["Guitar Reps"]["blocks"] == 3     # press duration_min 75
    assert rows["Garage Buildout"]["blocks"] == 1  # default


def test_plan_inputs_suggested_rows_untouched(vault):
    body = _client(vault, todoist=FakeTodoist({})).get("/plan-inputs").json()
    assert all("blocks" not in i for i in body["digest"]["suggested"])


def test_plan_inputs_no_config_still_enriches_default(tmp_path):
    v = tmp_path / "vault-root"
    v.mkdir()
    proj = v / "50 - Operations" / "Projects"
    proj.mkdir(parents=True)
    (proj / "Garage Buildout.md").write_text(
        "---\ntype: project\nassigned: true\n---\nbody\n", encoding="utf-8"
    )
    app = main_mod.create_app(vault_root=v)
    body = TestClient(app).get("/plan-inputs").json()
    rows = _assigned_by_name(body)
    assert rows["Garage Buildout"]["blocks"] == 1
