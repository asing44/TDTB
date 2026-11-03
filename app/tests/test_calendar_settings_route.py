"""Route tests for the per-calendar on/off settings API.

``GET /settings/calendars`` is a tokenless local read that writes nothing;
``POST /settings/calendars/save`` is token-guarded, revision-checked, and
full-replacement. Nothing here builds a real provider or touches EventKit:
the calendar store and read-client seams are injected, and the legacy
``Disabled Calendars`` config list is migrated on the first PLANNING read.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))

import app_config  # noqa: E402
import calendar_settings as cs  # noqa: E402
import main as main_mod  # noqa: E402
from calendar_bridge import CalendarInfo  # noqa: E402

PERSONAL = CalendarInfo("Personal", "CAL-P", True, "iCloud")
OWN = CalendarInfo("TDTB Blocks", "CAL-OWN", True, "Google")
CALENDARS = [PERSONAL, OWN]


class FakeCalendarStore:
    """Minimal EventStore fake: authorized, one inventory, one event."""

    def __init__(self, events=None, calendars=None, raise_exc=None):
        self._events = events or []
        self._calendars = CALENDARS if calendars is None else calendars
        self._raise_exc = raise_exc

    def auth_status(self) -> str:
        return "authorized"

    def calendars(self):
        if self._raise_exc is not None:
            raise self._raise_exc
        return self._calendars

    def query_events(self, start, end, calendar_ids=None):
        return self._events


def _event(title: str, cal: str) -> dict:
    return {
        "title": title,
        "calendar_id": cal,
        "start": datetime(2026, 7, 14, 9, 0),
        "end": datetime(2026, 7, 14, 9, 30),
    }


def _write_config(calendar: dict | None = None) -> Path:
    path = app_config.config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    document: dict = {"version": 1, "sources": {"mode": "artifact"}}
    if calendar is not None:
        document["calendar"] = calendar
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def _client(store=None, monkeypatch=None) -> TestClient:
    app = main_mod.create_app()
    app.state.build_read_clients = lambda _cfg: (None, store)
    app.state.build_calendar_store = lambda _cfg: store
    app.state.build_capacities_adapter = None
    client = TestClient(app)
    client.app_token = app.state.token
    return client


def _auth(client: TestClient) -> dict:
    return {"X-TDTB-Token": client.app_token}


def _save_body(expected_revision: int = 0, calendars: list | None = None) -> dict:
    return {
        "expected_revision": expected_revision,
        "calendars": calendars if calendars is not None else [],
    }


class TestGetSettings:
    def test_missing_file_reports_all_on_and_writes_nothing(self):
        client = _client(FakeCalendarStore())
        response = client.get("/settings/calendars")
        assert response.status_code == 200
        body = response.json()
        assert body["persisted"] is False
        assert body["settings"] == {"version": 1, "revision": 0, "calendars": []}
        assert not cs.settings_path().exists()

    def test_unreadable_inventory_degrades_to_status_and_warning(self):
        client = _client(FakeCalendarStore(raise_exc=RuntimeError("no grant")))
        body = client.get("/settings/calendars").json()
        assert body["inventory"] == []
        assert body["inventory_status"] == "unavailable"
        assert body["warnings"]
        assert body["settings"]["revision"] == 0

    def test_no_store_seam_degrades(self):
        app = main_mod.create_app()
        app.state.build_calendar_store = None
        body = TestClient(app).get("/settings/calendars").json()
        assert body["inventory_status"] == "unavailable"
        assert body["warnings"]

    def test_inventory_effective_state_and_own_write_lock(self):
        _write_config(
            {"titles": [{"Logical name": "blocks", "BusyCal title": "TDTB Blocks"}]}
        )
        cs.save_settings(
            expected_revision=0,
            calendars=[
                cs.CalendarSetting(id="CAL-P", title="Personal", enabled=False),
                cs.CalendarSetting(id="CAL-OWN", title="TDTB Blocks", enabled=False),
            ],
        )
        client = _client(FakeCalendarStore())
        body = client.get("/settings/calendars").json()
        assert body["persisted"] is True
        assert body["inventory_status"] == "ok"
        rows = {row["id"]: row for row in body["inventory"]}
        assert rows["CAL-P"]["effective_enabled"] is False
        assert rows["CAL-P"]["locked"] is None
        assert rows["CAL-OWN"]["locked"] == "own_write"
        assert rows["CAL-OWN"]["effective_enabled"] is True

    def test_unseen_calendar_defaults_on(self):
        client = _client(FakeCalendarStore())
        rows = {
            row["id"]: row
            for row in client.get("/settings/calendars").json()["inventory"]
        }
        assert rows["CAL-P"]["effective_enabled"] is True
        assert rows["CAL-P"]["title"] == "Personal"
        assert rows["CAL-P"]["source"] == "iCloud"
        assert rows["CAL-P"]["writable"] is True

    def test_malformed_storage_answers_500_and_preserves_bytes(self):
        path = cs.settings_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json", encoding="utf-8")
        client = _client(FakeCalendarStore())
        response = client.get("/settings/calendars")
        assert response.status_code == 500
        assert response.json()["detail"]["code"] == "calendar_settings_storage_error"
        assert path.read_text(encoding="utf-8") == "{not json"


class TestSaveSettings:
    def test_requires_token(self):
        client = _client(FakeCalendarStore())
        response = client.post("/settings/calendars/save", json=_save_body())
        assert response.status_code == 403
        assert not cs.settings_path().exists()

    def test_round_trip(self):
        client = _client(FakeCalendarStore())
        response = client.post(
            "/settings/calendars/save",
            headers=_auth(client),
            json=_save_body(
                calendars=[
                    {"id": "CAL-P", "title": "Personal", "enabled": False},
                    {"id": "CAL-OWN", "title": "TDTB Blocks", "enabled": True},
                ]
            ),
        )
        assert response.status_code == 200
        body = response.json()
        assert body["persisted"] is True
        assert body["settings"]["revision"] == 1
        assert [
            (row["id"], row["enabled"]) for row in body["settings"]["calendars"]
        ] == [("CAL-OWN", True), ("CAL-P", False)]

    def test_stale_revision_409(self):
        client = _client(FakeCalendarStore())
        first = client.post(
            "/settings/calendars/save", headers=_auth(client), json=_save_body()
        )
        assert first.status_code == 200
        before = cs.settings_path().read_bytes()
        stale = client.post(
            "/settings/calendars/save", headers=_auth(client), json=_save_body()
        )
        assert stale.status_code == 409
        assert stale.json()["detail"]["code"] == "calendar_settings_conflict"
        assert cs.settings_path().read_bytes() == before

    def test_own_write_calendar_cannot_be_saved_off(self):
        _write_config(
            {"titles": [{"Logical name": "blocks", "BusyCal title": "TDTB Blocks"}]}
        )
        client = _client(FakeCalendarStore())
        response = client.post(
            "/settings/calendars/save",
            headers=_auth(client),
            json=_save_body(
                calendars=[
                    {"id": "CAL-OWN", "title": "TDTB Blocks", "enabled": False},
                ]
            ),
        )
        assert response.status_code == 200
        assert response.json()["settings"]["calendars"][0]["enabled"] is True

    def test_unknown_body_key_is_rejected(self):
        client = _client(FakeCalendarStore())
        response = client.post(
            "/settings/calendars/save",
            headers=_auth(client),
            json={"expected_revision": 0, "calendars": [], "version": 1},
        )
        assert response.status_code == 422
        assert not cs.settings_path().exists()

    def test_malformed_storage_409_and_preserves_bytes(self):
        path = cs.settings_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"version": 9}', encoding="utf-8")
        client = _client(FakeCalendarStore())
        response = client.post(
            "/settings/calendars/save", headers=_auth(client), json=_save_body()
        )
        assert response.status_code == 409
        assert path.read_text(encoding="utf-8") == '{"version": 9}'


class TestLegacyMigration:
    def test_migrates_once_on_first_planning_read_and_get_never_writes(self):
        _write_config({"disabled": ["Personal"]})
        client = _client(FakeCalendarStore([_event("Dentist", "CAL-P")]))

        # GET is read-only: it must never create the store.
        assert client.get("/settings/calendars").json()["persisted"] is False
        assert not cs.settings_path().exists()

        first = client.get("/plan-inputs").json()
        assert cs.settings_path().exists()
        stored = cs.read_settings()
        assert stored.settings.revision == 1
        assert [
            (row.id, row.title, row.enabled) for row in stored.settings.calendars
        ] == [("CAL-P", "Personal", False)]
        codes = [row["reason_code"] for row in first["calendar_decisions"]]
        assert codes == ["excluded_calendar_off"]

        # Idempotent: a second planning read neither rewrites nor re-applies
        # the (now retired) config key.
        before = cs.settings_path().read_bytes()
        client.get("/plan-inputs")
        assert cs.settings_path().read_bytes() == before

    def test_config_key_is_ignored_after_migration(self):
        _write_config({"disabled": ["Personal"]})
        client = _client(FakeCalendarStore())
        client.get("/plan-inputs")
        # The owner re-adds a legacy entry: the store is authoritative now.
        _write_config({"disabled": ["Personal", "TDTB Blocks"]})
        body = client.get("/plan-inputs").json()
        codes = {row["reason_code"] for row in body["calendar_decisions"]}
        assert codes <= {"excluded_calendar_off"}
        assert [row.id for row in cs.read_settings().settings.calendars] == ["CAL-P"]


class TestRouteAgreement:
    def test_plan_inputs_and_capacity_preview_pass_the_same_off_ids(
        self, monkeypatch
    ):
        """Both ext_cfg builders must hand the SAME off-calendar ids to the
        calendar reader, or the busy frame and capacity math would disagree."""
        captured: dict[str, dict] = {}
        real_decisions = main_mod.external_sources.fetch_calendar_decisions
        real_busy = main_mod.external_sources.fetch_calendar_busy

        def _decisions(store, cfg, today):
            captured["plan_inputs"] = dict(cfg)
            return real_decisions(store, cfg, today)

        def _busy(store, cfg, today):
            captured["capacity_preview"] = dict(cfg)
            return real_busy(store, cfg, today)

        monkeypatch.setattr(
            main_mod.external_sources, "fetch_calendar_decisions", _decisions
        )
        monkeypatch.setattr(main_mod.external_sources, "fetch_calendar_busy", _busy)

        cs.save_settings(
            expected_revision=0,
            calendars=[cs.CalendarSetting(id="CAL-P", title="Personal", enabled=False)],
        )
        client = _client(FakeCalendarStore([_event("Dentist", "CAL-P")]))

        plan = client.get("/plan-inputs")
        assert plan.status_code == 200
        preview = client.get(
            "/capacity-preview",
            params={"day_setup": json.dumps({"anchor": "08:00", "eod": "20:00"})},
        )
        assert preview.status_code == 200

        assert captured["plan_inputs"]["calendar_disabled_ids"] == ["CAL-P"]
        assert captured["capacity_preview"]["calendar_disabled_ids"] == ["CAL-P"]
        assert (
            captured["plan_inputs"]["calendar_disabled_ids"]
            == captured["capacity_preview"]["calendar_disabled_ids"]
        )
        assert plan.json()["source_counts"]["calendar"] == 0
