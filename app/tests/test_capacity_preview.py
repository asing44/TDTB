"""Route tests for GET /capacity-preview (ui-revamp T2, G19/G27).

Single number source for the Day Setup budget bar: tokenless read-only GET
that accepts the UI's *proposed* (unsaved) Day Setup state plus the included
assigned-row durations, and returns the canonical capacity numbers computed
by the same capacity.py/time_engine.py path /plan-inputs uses. The frontend
renders these verbatim — its own block arithmetic is deleted (locked
decision 2, 2026-07-16-tdtb-ui-revamp.md).

Pinned G27 divergences (each was a JS-vs-Python disagreement):
- 0-duration spec → 0 blocks, no min-1 clamp (JS clamped to 1).
- buffering default 'minimal' per SKILL.md 397/797 (JS defaulted 'standard').
- h-format Durations ("1h20m") parse as 80 min (old server regex read 1 min).
- midnight: EOD ≤ anchor is "no schedulable time" per the skill, never a
  +24h wrap (JS wrapped).
- Selected = included assigned rows + schedulables (SKILL.md 763) — assigned
  durations arrive via the `selected` param and are parsed server-side.
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))
import main as main_mod  # noqa: E402
import runstate as runstate_mod  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent.parent / "gather"))
import tdtb_gather as gather  # noqa: E402

CONFIG_REL_PATH = "00 - META/Skill-Configs/tdtb-bridger.md"

# Two anchored blocks: 80m → 3 blk, and an h-format 1h20m → 3 blk (G27 pin —
# the old bare-prefix regex parsed "1h20m" as 1 minute → 1 block).
CONFIG = """\
---
description: test config
last_updated: 2026-07-01
---

# TDTB Bridger Config

## Defaults

| Key                    | Value    |
| ---------------------- | -------- |
| eod                    | 11:45 PM |
| buffering.standard_pct | 0.19     |
| buffering.minimal_pct  | 0.11     |
| buffering.off_pct      | 0        |

## Anchored Lifestyle Blocks

| Block           | Type | Start   | End | Duration | Days  | overlap_allowed |
| --------------- | ---- | ------- | --- | -------- | ----- | --------------- |
| Morning Routine | hard | 7:45 AM | —   | 80m      | daily | no              |
| Stretch         | hard | 6:00 PM | —   | 1h20m    | daily | no              |
"""

FRAME = {"anchor": "08:00", "eod": "20:00"}  # total = 24 blocks, deterministic

# Deterministic logical day for weekday-Mint expectations: 2026-07-13 is a
# Monday. The client fixture pins gather.effective_date to it so the route's
# logical today (and thus the weekday Mint default) never depends on the host
# weekday — same convention as test_mint_defaults.MONDAY.
MONDAY = date(2026, 7, 13)


@pytest.fixture
def vault(tmp_path) -> Path:
    v = tmp_path / "vault-root"
    v.mkdir()
    p = v / CONFIG_REL_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(CONFIG, encoding="utf-8")
    return v


@pytest.fixture
def client(vault, monkeypatch) -> TestClient:
    # Route parity: the route's logical today comes from
    # gather.effective_date; pin it to MONDAY so the weekday Mint default
    # (8 blocks with the implicit 240-minute allotment) is host-date stable.
    monkeypatch.setattr(gather, "effective_date", lambda _now: MONDAY)
    return TestClient(main_mod.create_app(vault_root=vault))


def _get(client, day_setup=None, selected=None):
    params = {}
    if day_setup is not None:
        params["day_setup"] = json.dumps({**FRAME, **day_setup})
    else:
        params["day_setup"] = json.dumps(FRAME)
    if selected is not None:
        params["selected"] = json.dumps(selected)
    return client.get("/capacity-preview", params=params)


class TestShape:
    def test_tokenless_returns_plan_shape(self, client):
        r = _get(client)
        assert r.status_code == 200
        body = r.json()
        assert set(body) >= {"segments", "total", "free", "over", "day_setup_echo"}
        assert set(body["segments"]) == {
            "fixed", "anchored", "habits", "mint", "selected", "buffer"
        }
        # canonical readout strings come from the server too (skill: every
        # surface emits THESE strings) so T3 can delete label-building
        assert "blk" in body["remaining"]
        assert body["legend"].startswith("Fixed ")
        assert body["work_busy"] == 0
        assert body["work_overflow"] == 0

    def test_day_setup_echo_is_effective_merged_state(self, client):
        body = _get(client, {"buffering": "off"}).json()
        assert body["day_setup_echo"]["buffering"] == "off"
        assert body["day_setup_echo"]["anchor"] == "08:00"


class TestG27Pins:
    def test_buffering_default_is_minimal(self, client):
        # No buffering key anywhere → 'minimal' (SKILL.md 397/797). JS
        # defaulting to 'standard' was the G27 bug. raw_remaining =
        # 24 − 0 − 6 − 0 = 18 → ceil(18 × 0.11) = 2, not ceil(18 × 0.19) = 4.
        body = _get(client).json()
        assert body["total"] == 24
        assert body["segments"]["buffer"] == 2

    def test_h_format_anchored_duration(self, client):
        # 80m → 3 blk plus 1h20m → 3 blk. Old parse gave 3 + 1 = 4.
        body = _get(client).json()
        assert body["segments"]["anchored"] == 6

    def test_selected_durations_parsed_server_side(self, client):
        # "30m"→1, "1h30m"→3, 90 (bare minutes)→3, "0m"→0 (no min-1 clamp),
        # null (row without a duration) → default 1 block like the old bar.
        body = _get(
            client,
            {"schedulable": {"minting": {"on": True, "n": 2}}},
            selected=["30m", "1h30m", 90, "0m", None],
        ).json()
        # Legacy Minting n is no longer Selected; T18c reserves Mint from the
        # resolved allotment instead.
        assert body["segments"]["selected"] == 1 + 3 + 3 + 0 + 1

    def test_mint_allotment_is_separate_and_canonical_minting_not_selected(self, client, vault):
        from tests.test_main_api import _write_day_presets_config
        _write_day_presets_config(vault)
        body = _get(
            client,
            {"work_allotment_minutes": 120,
             "schedulable": {"minting": {"on": True, "n": 9}}},
            selected=[30],
        ).json()
        assert body["segments"]["mint"] == 4
        assert body["segments"]["selected"] == 1

    def test_zero_allotment_suppresses_mint_segment_and_zones(self, client, vault):
        from tests.test_main_api import _write_day_presets_config
        _write_day_presets_config(vault)
        body = _get(client, {"work_allotment_minutes": 0}).json()
        assert body["segments"]["mint"] == 0
        assert body["day_semantics"]["enabled_zones"] == []

    def test_fifteen_minute_override_costs_half_a_block(self, client):
        body = _get(client, {"buffering": "off"}, selected=[15, 0]).json()
        assert body["segments"]["selected"] == 0.5
        assert body["free"] == 9.5  # 24 − 6 anchored − 8 Mint − 0.5 selected

    def test_midnight_eod_before_anchor_never_wraps(self, client):
        # Skill: EOD ≤ anchor → "no schedulable time", NOT a +24h wrap
        # (JS wrapped 22:00→00:30 into 5 blocks).
        body = _get(client, {"anchor": "22:00", "eod": "00:30"}).json()
        assert body["total"] <= 0
        assert body["time"]["no_time_left"] is True


class TestDaySetupInputs:
    def test_buffering_off_zeroes_buffer(self, client):
        body = _get(client, {"buffering": "off"}).json()
        assert body["segments"]["buffer"] == 0

    def test_anchored_toggle_off_and_skip_today(self, client):
        body = _get(
            client,
            {"anchored": [{"id": "Morning Routine", "on": False}]},
        ).json()
        assert body["segments"]["anchored"] == 3  # Stretch only
        body = _get(
            client,
            {"anchored": [{"id": "Stretch", "skip_today": True}]},
        ).json()
        assert body["segments"]["anchored"] == 3  # Morning Routine only

    def test_anchored_blocks_override_rewrites_duration(self, client):
        # apply_day_setup: {"blocks": n} rewrites Duration to n×30 minutes.
        body = _get(
            client,
            {"anchored": [{"id": "Morning Routine", "blocks": 5}]},
        ).json()
        assert body["segments"]["anchored"] == 5 + 3

    def test_persisted_runstate_merged_query_wins(self, client, vault):
        # Same pinned logical day the route resolves (client fixture pins
        # gather.effective_date → MONDAY); the persisted runstate must land
        # exactly where the route reads it back.
        today = MONDAY
        state = runstate_mod.build_runstate({"buffering": "off"})
        runstate_mod.write_runstate(vault, today, state)
        # no query override → persisted 'off' applies
        r = client.get(
            "/capacity-preview", params={"day_setup": json.dumps(FRAME)}
        )
        assert r.json()["segments"]["buffer"] == 0
        # query override beats persisted; the default Mint allotment consumes
        # 8 of the 18 raw remaining blocks, leaving 10 for buffering.
        body = _get(client, {"buffering": "standard"}).json()
        assert body["segments"]["buffer"] == 2  # ceil(10 × 0.19)

    def test_route_forwards_logical_today_into_capacity_frame(
        self, client, monkeypatch
    ):
        # The client fixture pins gather.effective_date → MONDAY (2026-07-13,
        # a workday), so the route's logical today is MONDAY. Pin the wall
        # clock to a Saturday so _capacity_frame's datetime.now() fallback
        # would disagree: without forwarding the route's logical today the
        # frame would treat Minting as inactive and report zero Mint blocks.
        class _FixedClock(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime(2026, 7, 11, 9, 0)  # Saturday

        monkeypatch.setattr(main_mod, "datetime", _FixedClock)
        body = _get(client, {"anchor": "08:00", "eod": "18:00"}).json()
        # 240-min implicit allotment (CONFIG has no work_allotment_minutes)
        # survives only on a workday; without the forwarded logical today the
        # Saturday fallback would suppress it entirely.
        assert body["segments"]["mint"] == 8


class TestOverAndErrors:
    def test_over_is_blocks_over_and_free_signed(self, client):
        # total 4 (08:00–10:00), anchored 6 and default Mint 8 →
        # free = 4 − 6 − 8 = −10 (signed).
        body = _get(client, {"anchor": "08:00", "eod": "10:00"}).json()
        assert body["free"] == -10
        assert body["over"] == 10
        assert body["overassigned"] is True

    def test_over_zero_when_free_positive(self, client):
        body = _get(client).json()
        assert body["free"] > 0
        assert body["over"] == 0

    def test_invalid_json_is_400(self, client):
        r = client.get("/capacity-preview", params={"day_setup": "{nope"})
        assert r.status_code == 400
        r = client.get("/capacity-preview", params={"selected": "nope"})
        assert r.status_code == 400

    def test_wrong_type_is_400(self, client):
        r = client.get("/capacity-preview", params={"day_setup": "[1,2]"})
        assert r.status_code == 400
        r = client.get("/capacity-preview", params={"selected": "{}"})
        assert r.status_code == 400

    def test_unparseable_selected_entry_is_400(self, client):
        r = _get(client, selected=["30m", "banana"])
        assert r.status_code == 400


# ---------------------------------------------------------------------------
# Issue #6 — route-level capacity contract: a timed event on a calendar
# disabled via the vault config's '## Disabled Calendars' section contributes
# zero to segments.fixed, with the response shape unchanged. Fakes only; the
# fake store ignores query dates so the test is deterministic for the route's
# effective today. No diagnostics (e.g. calendar_decisions) are added to this
# response.
# ---------------------------------------------------------------------------

from calendar_bridge import CalendarInfo  # noqa: E402


CONFIG_DISABLED_CALENDARS = CONFIG + """
## Disabled Calendars

| Title    |
| -------- |
| Personal |
"""


class _PreviewStore:
    """Fake EventStore exposing an inventory; ignores queried dates."""

    def __init__(self, events, calendars):
        self._events = events
        self._calendars = calendars

    def auth_status(self):
        return "authorized"

    def calendars(self):
        return self._calendars

    def query_events(self, start, end, calendar_ids=None):
        return self._events


def _client_with_store(vault: Path, store) -> TestClient:
    p = vault / CONFIG_REL_PATH
    p.write_text(CONFIG_DISABLED_CALENDARS, encoding="utf-8")
    app = main_mod.create_app(vault_root=vault)
    app.state.build_read_clients = lambda v, cfg: (None, store)
    return TestClient(app)


class TestDisabledCalendarCapacity:
    def test_disabled_calendar_timed_event_contributes_zero_fixed(self, vault):
        event = lambda cal: [{  # noqa: E731 — local fixture helper
            "title": "Yoga", "start": datetime(2026, 7, 14, 9, 0),
            "end": datetime(2026, 7, 14, 13, 0), "calendar_id": cal,
        }]
        calendars = [
            CalendarInfo("Personal", "CAL-PERS", False, "Local"),
            CalendarInfo("School", "CAL-SCHOOL", False, "Local"),
        ]
        # Baseline: the same 4h timed event on an enabled calendar would
        # contribute 8 fixed blocks (240 min / 30).
        enabled_client = _client_with_store(
            vault, _PreviewStore(event("CAL-SCHOOL"), calendars)
        )
        body = _get(enabled_client).json()
        assert body["segments"]["fixed"] == 8

        # The identical event on a disabled calendar contributes zero.
        disabled_client = _client_with_store(
            vault, _PreviewStore(event("CAL-PERS"), calendars)
        )
        body = _get(disabled_client).json()
        assert body["segments"]["fixed"] == 0
        # Existing response shape preserved; no diagnostics added.
        assert set(body) >= {"segments", "total", "free", "over", "day_setup_echo"}
        assert set(body["segments"]) == {
            "fixed", "anchored", "habits", "mint", "selected", "buffer",
        }
        assert "calendar_decisions" not in body
