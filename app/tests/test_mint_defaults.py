"""Focused regressions for the Mint allotment default and route parity."""
from __future__ import annotations

import json
import sys
from datetime import date, datetime
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))
import day_semantics  # noqa: E402
import external_sources as ext  # noqa: E402
import main as main_mod  # noqa: E402
import runstate  # noqa: E402


MONDAY = date(2026, 7, 13)
CONFIG = {
    "Defaults": {"work_allotment_minutes": 300},
    "Template Blocks": {
        "Trinoor Hours": [
            {"Slot": "Morning", "Start": "8:30 AM", "End": "12:30 PM"},
            {"Slot": "Afternoon", "Start": "1:30 PM", "End": "5:00 PM"},
        ],
    },
}


def _contract(allotment: int | None = None) -> dict[str, object]:
    sections = {
        "Defaults": {},
        "Day Presets": [
            {
                "Name": "Workday",
                "Days": "workdays",
                "Zones": "",
                "Work Allotment (min)": allotment,
                "Default": "true",
            },
        ],
    }
    return day_semantics.resolve_day_contract(
        sections, "", MONDAY,
        dated_overrides=(
            {"work_allotment_minutes": allotment}
            if allotment is not None else None
        ),
    )


def test_implicit_allotment_is_eight_blocks():
    contract = day_semantics.resolve_day_contract(
        {"Day Presets": []}, "", MONDAY,
    )
    assert contract["effective_allotment_minutes"] == 240
    assert contract["mint_enabled"] is True
    items, _, _ = ext.build_schedulable_blocks(
        CONFIG, {}, MONDAY, "09:00", resolved_day_semantics=contract,
    )
    assert next(item for item in items if item["name"] == "Minting")["blocks"] == 8


def test_allotment_sources_keep_implicit_configured_preset_and_dated_distinct():
    implicit = day_semantics.resolve_day_contract(
        {"Day Presets": []}, "", MONDAY,
    )
    assert implicit["allotment_source"] == "implicit_default"

    configured = day_semantics.resolve_day_contract(
        {"Defaults": {"work_allotment_minutes": 180}}, "", MONDAY,
    )
    assert configured["effective_allotment_minutes"] == 180
    assert configured["allotment_source"] == "configured_default"
    assert configured["mint_below_default"] is True

    preset = day_semantics.resolve_day_contract(
        {"Day Presets": [{"Name": "Workday", "Days": "workdays",
                          "Zones": "", "Work Allotment (min)": 180,
                          "Default": "true"}]},
        "", MONDAY,
    )
    assert preset["allotment_source"] == "preset"
    assert preset["effective_allotment_minutes"] == 180

    dated = day_semantics.resolve_day_contract(
        {"Defaults": {"work_allotment_minutes": 300}}, "", MONDAY,
        dated_overrides={"work_allotment_minutes": 0},
    )
    assert dated["allotment_source"] == "dated_override"
    assert dated["effective_allotment_minutes"] == 0
    assert dated["mint_enabled"] is False


def test_configured_300_minutes_is_ten_blocks():
    items, _, _ = ext.build_schedulable_blocks(CONFIG, {}, MONDAY, "09:00")
    assert next(item for item in items if item["name"] == "Minting")["blocks"] == 10


def test_explicit_210_minutes_is_seven_blocks_and_warns():
    contract = _contract(210)
    assert contract["effective_allotment_minutes"] == 210
    assert contract["mint_enabled"] is True
    assert contract["mint_below_default"] is True
    assert any("240-minute daily default" in warning for warning in contract["warnings"])
    items, _, _ = ext.build_schedulable_blocks(
        CONFIG, {}, MONDAY, "09:00", resolved_day_semantics=contract,
    )
    assert next(item for item in items if item["name"] == "Minting")["blocks"] == 7


def test_explicit_zero_disables_mint_and_stale_sessions():
    session = ext.mint_session_options(CONFIG)[0]["id"]
    items, _, _ = ext.build_schedulable_blocks(
        CONFIG,
        {
            "work_allotment_minutes": 0,
            "schedulable": {"minting": {"on": True, "sessions": [session]}},
        },
        MONDAY,
        "09:00",
        resolved_day_semantics={"effective_allotment_minutes": 300},
    )
    assert not any(item["name"] == "Minting" for item in items)
    assert not any(item.get("mint_session") for item in items)


def test_capacity_preview_and_schedulable_row_use_same_effective_allotment(
    tmp_path, monkeypatch
):
    vault = tmp_path / "vault"
    config_path = vault / "00 - META/Skill-Configs/tdtb-bridger.md"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        "---\n"
        "description: test config\n"
        "last_updated: 2026-07-01\n"
        "---\n\n"
        "# TDTB Bridger Config\n\n"
        "## Defaults\n"
        "| Key | Value |\n|---|---|\n"
        "| work_allotment_minutes | 300 |\n"
        "| eod | 18:00 |\n"
        "| buffering.off_pct | 0 |\n\n"
        "## Anchored Lifestyle Blocks\n"
        "| Block | Type | Start | End | Duration | Days | overlap_allowed |\n"
        "|---|---|---|---|---|---|---|\n\n"
        "## Template Blocks\n"
        "### Trinoor Hours\n"
        "| Slot | Start | End |\n|---|---|---|\n"
        "| Morning | 8:30 AM | 5:00 PM |\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(main_mod.gather, "effective_date", lambda _now: MONDAY)
    client = TestClient(main_mod.create_app(vault_root=vault))
    preview = client.get(
        "/capacity-preview",
        params={"day_setup": json.dumps({"anchor": "08:00", "eod": "18:00"})},
    )
    assert preview.status_code == 200
    body = preview.json()
    items, _, _ = ext.build_schedulable_blocks(
        CONFIG, body["day_setup_echo"], MONDAY, body["time"]["anchor"],
        resolved_day_semantics=body["day_semantics"],
    )
    mint_row = next(item for item in items if item["name"] == "Minting")
    assert body["segments"]["mint"] == 10
    assert mint_row["blocks"] == body["segments"]["mint"]


def test_day_setup_zero_clears_persisted_stale_sessions(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    vault.mkdir()
    monkeypatch.setattr(main_mod.gather, "effective_date", lambda _now: MONDAY)
    app = main_mod.create_app(vault_root=vault)
    client = TestClient(app)
    headers = {"X-TDTB-Token": app.state.token}
    session = ext.mint_session_options(CONFIG)[0]["id"]

    selected = client.post(
        "/day-setup",
        headers=headers,
        json={"schedulable": {"minting": {"on": True, "sessions": [session]}}},
    )
    assert selected.status_code == 200
    disabled = client.post(
        "/day-setup",
        headers=headers,
        json={"work_allotment_minutes": 0},
    )
    assert disabled.status_code == 200
    state = runstate.read_runstate(vault, MONDAY)
    assert state["work_allotment_minutes"] == 0
    assert state["schedulable"]["minting"] == {"on": False, "n": 0, "sessions": []}


def test_legacy_all_session_detection_happens_before_wall_filtering():
    config = {"Template Blocks": {"Trinoor Hours": [
        {"Slot": "Morning", "Start": "08:00", "End": "12:00"},
    ]}}
    options = ext.mint_session_options(config)
    normalized = ext.normalize_mint_session_override(
        config,
        {"on": True, "sessions": [option["id"] for option in options]},
        allotment_minutes=240,
        wall_intervals=[(8 * 60, 8 * 60 + 30)],
    )
    # There are eight raw options and the first one is wall-blocked. The
    # legacy all-session list is rebuilt to the requested eight before the
    # wall filter, rather than preserving the remaining seven as an intent.
    assert normalized["n"] == 7
    assert normalized["sessions"] == [option["id"] for option in options[1:8]]


def test_invalid_and_stale_session_ids_are_discarded():
    config = {"Template Blocks": {"Trinoor Hours": [
        {"Slot": "Morning", "Start": "08:00", "End": "10:00"},
    ]}}
    options = ext.mint_session_options(config)
    normalized = ext.normalize_mint_session_override(
        config,
        {"on": True, "sessions": [options[0]["name"], "stale", options[0]["id"]]},
        allotment_minutes=240,
    )
    assert normalized["sessions"] == [options[0]["id"]]
    assert normalized["n"] == 1


def test_default_off_and_explicit_reinclusion_have_same_row_capacity_amount():
    config = {"Template Blocks": {"Trinoor Hours": [
        {"Slot": "Morning", "Start": "08:00", "End": "10:00"},
    ]}}
    weekend = ext.mint_schedule(
        config, {}, date(2026, 7, 11), "08:00",
        {"effective_allotment_minutes": 240},
    )
    reincluded = ext.mint_schedule(
        config, {"schedulable": {"minting": {"on": True}}},
        date(2026, 7, 11), "08:00",
        {"effective_allotment_minutes": 240},
    )
    late = ext.mint_schedule(
        config, {}, MONDAY, "09:30",
        {"effective_allotment_minutes": 240},
    )
    assert (weekend.active, weekend.active_blocks) == (False, 0)
    assert (reincluded.active, reincluded.active_blocks) == (True, 8)
    assert (late.active, late.active_blocks) == (True, 1)


def test_route_capacity_and_rows_share_weekend_and_late_anchor_amounts():
    scenarios = [
        (MONDAY, {}, datetime(2026, 7, 13, 8, 0), 10),
        (date(2026, 7, 11), {}, datetime(2026, 7, 11, 8, 0), 0),
        (
            date(2026, 7, 11),
            {"schedulable": {"minting": {"on": True}}},
            datetime(2026, 7, 11, 8, 0),
            10,
        ),
        (MONDAY, {"anchor": "16:00"}, datetime(2026, 7, 13, 16, 0), 2),
    ]
    for valid_date, setup, now, expected in scenarios:
        frame, capacity = main_mod._capacity_frame(
            CONFIG, setup, [], {}, now=now, today=valid_date,
        )
        items, _, _ = ext.build_schedulable_blocks(
            CONFIG, setup, valid_date, frame.anchor,
        )
        row_blocks = sum(
            item["blocks"] for item in items if item.get("name") == "Minting"
        )
        assert row_blocks == expected
        assert capacity.mint == expected


def test_route_normalization_discards_stale_ids_and_caps_legacy_all_selection():
    options = ext.mint_session_options(CONFIG)
    setup = ext.normalize_mint_day_setup(
        CONFIG,
        {"schedulable": {"minting": {
            "on": True,
            "sessions": [option["id"] for option in options] + ["stale-id"],
        }}},
        MONDAY,
    )
    minting = setup["schedulable"]["minting"]
    assert minting["on"] is True
    assert minting["n"] == 10
    assert len(minting["sessions"]) == 10
    assert "stale-id" not in minting["sessions"]
    assert setup["work_allotment_minutes"] == 300


def test_mint_wall_preflight_uses_half_open_boundaries_and_exclusions():
    mint = {
        "id": "Mint 09:00",
        "mint_session": True,
        "placement_window": {"start": "09:00", "end": "09:30"},
    }
    touching = {"Block": "Touching", "Start": "08:30", "End": "09:00"}
    overlapping = {"Block": "Overlapping", "Start": "09:29", "End": "10:00"}
    ignored = {
        "Block": "Ignored calendar", "source": "calendar",
        "capacity_class": "ignored", "Start": "09:00", "End": "10:00",
    }
    assert ext.stale_mint_conflicts([mint], [touching, ignored]) == []
    conflicts = ext.stale_mint_conflicts([mint], [overlapping])
    assert len(conflicts) == 1
    assert conflicts[0]["wall_id"] == "Overlapping"


def test_sequence_recomputes_omitted_day_semantics_from_request_config(
    tmp_path, monkeypatch,
):
    vault = tmp_path / "vault"
    vault.mkdir()
    app = main_mod.create_app(vault_root=vault)
    client = TestClient(app)
    captured = {}

    def fake_propose(assigned, config, anchored_blocks, ctx=None):
        captured["config"] = config
        return {"sequence": [], "rationale": "test"}

    monkeypatch.setattr(main_mod.judgment, "propose_sequence", fake_propose)
    monkeypatch.setattr(
        main_mod.sequence,
        "validate_sequence",
        lambda *args, **kwargs: type(
            "Result", (), {"ok": True, "hard_errors": [], "warnings": []}
        )(),
    )
    monkeypatch.setattr(main_mod.gather, "effective_date", lambda _now: MONDAY)

    response = client.post(
        "/sequence",
        headers={"X-TDTB-Token": app.state.token},
        json={
            "assigned": [],
            "config": {"Defaults": {"work_allotment_minutes": 180}},
            "anchored_blocks": [],
            # day_semantics intentionally omitted
        },
    )
    assert response.status_code == 200
    assert (
        captured["config"]["resolved_day_semantics"]
        ["effective_allotment_minutes"]
        == 180
    )
