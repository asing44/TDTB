"""Calendar-row local start overrides (backend half).

The Set up day strip's calendar events become editable LOCALLY ONLY: Count
today (``skip_today``), Duration, and a NEW Start time. The start edit and the
calendar's start at edit time persist on the dated runstate under the existing
``day_setup.anchored`` override record; nothing here writes to Google Calendar.

Contract: additive schema (old payloads and old stored notes stay valid),
invalid clock times rejected with the existing 422 style, the override
round-trips through ``/day-setup`` and ``/plan-inputs``, and the day-setup
save never enters the calendar write path.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))
import main as main_mod  # noqa: E402
import runstate as rs  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent.parent / "gather"))
import tdtb_gather as gather  # noqa: E402
from tests.config_markdown import write_config_note  # noqa: E402


def _write_min_config(vault: Path) -> None:
    cfg = vault / "00 - META/Skill-Configs/tdtb-bridger.md"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    write_config_note(cfg,
        "## Defaults\n"
        "| Key | Value |\n|---|---|\n"
        "| eod | 11:59 PM |\n"
        "| anchor.round_to_minutes | 15 |\n"
        "\n## Anchored Lifestyle Blocks\n"
        "| Block | Type | Start | End | Duration | Days |\n"
        "|---|---|---|---|---|---|\n"
        "| Sudsing | hard | 5:45 PM | — | 30m | daily |\n",
        encoding="utf-8",
    )


@pytest.fixture
def vault(tmp_path) -> Path:
    v = tmp_path / "vault-root"
    v.mkdir()
    return v


@pytest.fixture
def client(vault) -> TestClient:
    app = main_mod.create_app()
    c = TestClient(app)
    c.app_token = app.state.token
    return c


def _auth(client: TestClient) -> dict:
    return {"X-TDTB-Token": client.app_token}


def _today_state() -> dict:
    today = gather.effective_date(datetime.now())
    return gather._extract_json_block(
        rs.runstate_read_path(today).read_text(encoding="utf-8")
    ) or {}


#: A calendar-backed override row exactly as the strip already sends it today.
CAL_ROW = {"id": "Farmers Market", "on": True, "skip_today": False, "time": None}


class TestCalendarStartOverride:
    def test_round_trips_start_and_calendar_start_at_edit(self, client, vault):
        _write_min_config(vault)
        entry = {**CAL_ROW, "start": "20:00", "calendar_start_at_edit": "19:00"}
        r = client.post("/day-setup", json={"anchored": [entry]},
                        headers=_auth(client))
        assert r.status_code == 200, r.text
        echo = r.json()["day_setup"]["anchored"]
        assert echo[0]["start"] == "20:00"
        assert echo[0]["calendar_start_at_edit"] == "19:00"

        state = _today_state()
        assert state["anchored"][0]["start"] == "20:00"
        assert state["anchored"][0]["calendar_start_at_edit"] == "19:00"

        # The reader that hands the frontend the override round-trips it too.
        body = client.get("/plan-inputs").json()
        row = body["day_setup"]["anchored"][0]
        assert row["start"] == "20:00"
        assert row["calendar_start_at_edit"] == "19:00"

    @pytest.mark.parametrize(
        "value", ["25:00", "24:00", "12:60", "19:5", "abc", "7pm", ""]
    )
    def test_rejects_invalid_start(self, client, vault, value):
        _write_min_config(vault)
        r = client.post(
            "/day-setup",
            json={"anchored": [{**CAL_ROW, "start": value}]},
            headers=_auth(client),
        )
        assert r.status_code == 422, r.text

    @pytest.mark.parametrize("value", ["00:00", "23:59", "13:07"])
    def test_accepts_any_valid_clock_time_not_just_quarters(
        self, client, vault, value
    ):
        _write_min_config(vault)
        r = client.post(
            "/day-setup",
            json={"anchored": [{**CAL_ROW, "start": value,
                                "calendar_start_at_edit": value}]},
            headers=_auth(client),
        )
        assert r.status_code == 200, r.text
        assert _today_state()["anchored"][0]["start"] == value

    def test_old_payload_without_new_fields_reads_back_unchanged(self, client, vault):
        _write_min_config(vault)
        entry = dict(CAL_ROW)
        r = client.post("/day-setup", json={"anchored": [entry]},
                        headers=_auth(client))
        assert r.status_code == 200, r.text
        assert _today_state()["anchored"] == [entry]
        assert client.get("/plan-inputs").json()["day_setup"]["anchored"] == [entry]

    def test_old_stored_note_without_new_fields_still_reads(self, client, vault):
        _write_min_config(vault)
        today = gather.effective_date(datetime.now())
        seeded = {"anchored": [{"id": "Sudsing", "on": True, "time": None}]}
        rs.write_runstate(today, rs.build_runstate(seeded))
        body = client.get("/plan-inputs").json()
        assert body["day_setup"]["anchored"] == seeded["anchored"]

    def test_dated_isolation_other_date_unaffected(self, client, vault):
        _write_min_config(vault)
        today = gather.effective_date(datetime.now())
        # A later date: the runstate writer's trigger-1 cleanup only prunes
        # EARLIER-dated notes, so this one survives the today write.
        other = today + timedelta(days=1)
        rs.write_runstate(other, rs.build_runstate({"anchored": [{"id": "Other"}]}))
        r = client.post(
            "/day-setup",
            json={"anchored": [{**CAL_ROW, "start": "20:00",
                                "calendar_start_at_edit": "19:00"}]},
            headers=_auth(client),
        )
        assert r.status_code == 200, r.text
        assert _today_state()["anchored"][0]["start"] == "20:00"
        other_state = gather._extract_json_block(
            rs.runstate_read_path(other).read_text(encoding="utf-8")
        ) or {}
        assert other_state["anchored"] == [{"id": "Other"}]

    def test_clearing_the_override_removes_it(self, client, vault):
        _write_min_config(vault)
        client.post(
            "/day-setup",
            json={"anchored": [{**CAL_ROW, "start": "20:00",
                                "calendar_start_at_edit": "19:00"}]},
            headers=_auth(client),
        )
        assert _today_state()["anchored"][0]["start"] == "20:00"
        # The anchored list is a full replacement: re-saving the row without a
        # start edit removes both fields.
        client.post("/day-setup", json={"anchored": [dict(CAL_ROW)]},
                    headers=_auth(client))
        row = _today_state()["anchored"][0]
        assert row == CAL_ROW
        assert "start" not in row and "calendar_start_at_edit" not in row

    def test_day_setup_save_never_enters_calendar_write_path(
        self, client, vault, monkeypatch
    ):
        _write_min_config(vault)
        write_calls: list[str] = []

        def _spy(*args, **kwargs):
            write_calls.append("write_calendar")
            raise AssertionError("calendar write path entered by /day-setup")

        def _forbidden_store():
            raise AssertionError("calendar store opened by /day-setup")

        monkeypatch.setattr(main_mod.commit, "write_calendar", _spy)
        monkeypatch.setattr(main_mod.calendar_bridge, "shared_store", _forbidden_store)
        r = client.post(
            "/day-setup",
            json={"anchored": [{**CAL_ROW, "skip_today": True, "start": "20:00",
                                "calendar_start_at_edit": "19:00"}]},
            headers=_auth(client),
        )
        assert r.status_code == 200, r.text
        assert write_calls == []
