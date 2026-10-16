"""Tests for the read-only calendar drift report (GitHub issue #19, part A).

Pure-compare tests feed plain data (no EventKit). Route tests inject a
read-only fake store through ``calendar_bridge.shared_store`` and assert the
report schema. Fixtures only — zero network, billed calls, Todoist/Calendar
writes, or real source reads.
"""
from __future__ import annotations

import sys
from datetime import date, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))
import calendar_bridge  # noqa: E402
import calendar_drift  # noqa: E402
import main as main_mod  # noqa: E402
import runstate  # noqa: E402

DAY = date(2026, 10, 16)


def _dt(hhmm: str) -> datetime:
    h, m = (int(p) for p in hhmm.split(":"))
    return datetime(DAY.year, DAY.month, DAY.day, h, m)


def _ledger(touched, estimation=()):
    return {
        "surfaces": {"calendar": {"status": "ok", "touched": dict(touched)}},
        "estimation": list(estimation),
    }


def _row(name, *, op="create", minutes=30, time="15:00", surface="calendar"):
    return {
        "name": name,
        "surface": surface,
        "op": op,
        "planned_duration_min": minutes,
        "planned_blocks": minutes / 30,
        "time": time,
    }


def _event(event_id, title, start, end, *, calendar_id="cal-blocks", all_day=False):
    return {
        "id": event_id,
        "title": title,
        "start": start,
        "end": end,
        "calendar_id": calendar_id,
        "all_day": all_day,
    }


# ---------------------------------------------------------------------------
# Pure comparison — no I/O
# ---------------------------------------------------------------------------


class TestCompare:
    def test_moved_flags_both_intervals(self):
        report = calendar_drift.compare(
            DAY,
            _ledger({"Mint Afternoon": "E1"}, [_row("Mint Afternoon")]),
            [_event("E1", "Mint Afternoon", _dt("16:00"), _dt("16:30"))],
            {"cal-blocks"},
        )
        assert report["hasCommit"] is True
        assert report["missing"] == [] and report["added"] == []
        assert report["moved"] == [{
            "name": "Mint Afternoon",
            "id": "E1",
            "committed": {"start": "2026-10-16T15:00:00",
                          "end": "2026-10-16T15:30:00"},
            "live": {"start": "2026-10-16T16:00:00",
                     "end": "2026-10-16T16:30:00"},
        }]

    def test_missing_committed_id_flagged(self):
        report = calendar_drift.compare(
            DAY,
            _ledger({"Mint Afternoon": "E1"}, [_row("Mint Afternoon")]),
            [],
            {"cal-blocks"},
        )
        assert report["missing"] == [{
            "name": "Mint Afternoon",
            "id": "E1",
            "committed": {"start": "2026-10-16T15:00:00",
                          "end": "2026-10-16T15:30:00"},
        }]
        assert report["moved"] == [] and report["added"] == []

    def test_added_owned_event_not_in_ledger(self):
        report = calendar_drift.compare(
            DAY,
            _ledger({"Mint Afternoon": "E1"}, [_row("Mint Afternoon")]),
            [
                _event("E1", "Mint Afternoon", _dt("15:00"), _dt("15:30")),
                _event("E9", "Swift", _dt("17:00"), _dt("17:30")),
            ],
            {"cal-blocks"},
        )
        assert report["added"] == [{
            "name": "Swift",
            "id": "E9",
            "live": {"start": "2026-10-16T17:00:00",
                     "end": "2026-10-16T17:30:00"},
        }]
        assert report["moved"] == [] and report["missing"] == []

    def test_unchanged_yields_empty_lists(self):
        report = calendar_drift.compare(
            DAY,
            _ledger({"Mint Afternoon": "E1"}, [_row("Mint Afternoon")]),
            [_event("E1", "Mint Afternoon", _dt("15:00"), _dt("15:30"))],
            {"cal-blocks"},
        )
        assert report["status"] == "ok" and report["reason"] is None
        assert report["moved"] == [] and report["missing"] == []
        assert report["added"] == []

    def test_foreign_event_never_added(self):
        report = calendar_drift.compare(
            DAY,
            _ledger({"Mint Afternoon": "E1"}, [_row("Mint Afternoon")]),
            [
                _event("E1", "Mint Afternoon", _dt("15:00"), _dt("15:30")),
                _event("F1", "Standup", _dt("09:00"), _dt("09:30"),
                       calendar_id="cal-foreign"),
            ],
            {"cal-blocks"},
        )
        assert report["added"] == []

    def test_all_day_owned_event_never_added(self):
        report = calendar_drift.compare(
            DAY,
            _ledger({"Mint Afternoon": "E1"}, [_row("Mint Afternoon")]),
            [
                _event("A1", "Holiday", _dt("00:00"), _dt("00:00"), all_day=True),
            ],
            {"cal-blocks"},
        )
        assert report["added"] == []

    def test_id_only_noop_moved_is_not_claimed_moved(self):
        # No estimation row -> no recorded interval -> EXIST/ABSENT only.
        report = calendar_drift.compare(
            DAY,
            _ledger({"Mint Afternoon": "E1"}),
            [_event("E1", "Mint Afternoon", _dt("16:00"), _dt("16:30"))],
            {"cal-blocks"},
        )
        assert report["moved"] == [] and report["missing"] == []

    def test_id_only_noop_absent_is_missing_with_null_interval(self):
        report = calendar_drift.compare(
            DAY, _ledger({"Mint Afternoon": "E1"}), [], {"cal-blocks"}
        )
        assert report["missing"] == [
            {"name": "Mint Afternoon", "id": "E1", "committed": None}
        ]

    def test_no_commit_today(self):
        report = calendar_drift.compare(DAY, None, [], set())
        assert report["hasCommit"] is False
        assert report["reason"] == "no commit today"
        assert report["moved"] == [] and report["missing"] == []
        assert report["added"] == []

    def test_ledger_without_calendar_surface_is_no_commit(self):
        report = calendar_drift.compare(
            DAY, {"surfaces": {"todoist": {"touched": {"Row": "T1"}}}}, [], set()
        )
        assert report["hasCommit"] is False
        assert report["reason"] == "no commit today"

    def test_compare_reaches_no_eventkit_write_path(self, monkeypatch):
        def _boom(*args, **kwargs):
            raise AssertionError("EventKit write path reached")

        monkeypatch.setattr(calendar_bridge.EventStore, "create_event", _boom)
        monkeypatch.setattr(calendar_bridge.EventStore, "update_event", _boom)
        monkeypatch.setattr(calendar_bridge.EventStore, "delete_event", _boom)
        report = calendar_drift.compare(
            DAY,
            _ledger({"Mint Afternoon": "E1"}, [_row("Mint Afternoon")]),
            [_event("E1", "Mint Afternoon", _dt("16:00"), _dt("16:30"))],
            {"cal-blocks"},
        )
        assert report["moved"]


# ---------------------------------------------------------------------------
# Route — ledger + fake Calendar read wired around the pure comparison
# ---------------------------------------------------------------------------


class FakeStore:
    """Read-only EventKit-shaped fake; every write method raises so a stray
    write path fails the test loudly."""

    def __init__(self, events=(), *, auth="fullAccess"):
        self.events = list(events)
        self.auth = auth
        self.calls: list[str] = []

    def auth_status(self) -> str:
        self.calls.append("auth_status")
        return self.auth

    def calendars(self):
        self.calls.append("calendars")
        return [calendar_bridge.CalendarInfo(
            title="Blocks", identifier="cal-blocks", writable=True, source="test")]

    def query_events(self, start, end):
        self.calls.append("query_events")
        return list(self.events)

    def create_event(self, *args, **kwargs):
        raise AssertionError("create_event must never be called")

    def update_event(self, *args, **kwargs):
        raise AssertionError("update_event must never be called")

    def delete_event(self, *args, **kwargs):
        raise AssertionError("delete_event must never be called")


def _write_ledger(touched, estimation=()):
    runstate.write_runstate(DAY, runstate.build_runstate({
        "commit_ledger": {
            "today": DAY.isoformat(),
            "surfaces": {"calendar": {
                "status": "ok", "step": "D/E", "touched": dict(touched)}},
            "estimation": list(estimation),
        }
    }))


@pytest.fixture
def logical_day(monkeypatch):
    """Pin the logical day (the route must use gather.effective_date, not the
    wall-clock date)."""
    monkeypatch.setattr(main_mod.gather, "effective_date", lambda now: DAY)
    return DAY


def _client(monkeypatch, store) -> TestClient:
    monkeypatch.setattr(calendar_bridge, "shared_store", lambda: store)
    return TestClient(main_mod.create_app())


class TestRoute:
    def test_reports_moved_with_both_intervals(self, logical_day, monkeypatch):
        _write_ledger({"Mint Afternoon": "E1"}, [_row("Mint Afternoon")])
        monkeypatch.setattr(
            main_mod.shadow, "_own_calendar_ids", lambda config, store: {"cal-blocks"})
        store = FakeStore([_event("E1", "Mint Afternoon", _dt("16:00"), _dt("16:30"))])
        response = _client(monkeypatch, store).get("/calendar-drift")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok" and body["day"] == str(DAY)
        assert body["hasCommit"] is True and body["reason"] is None
        assert body["moved"][0]["committed"]["start"] == "2026-10-16T15:00:00"
        assert body["moved"][0]["live"]["start"] == "2026-10-16T16:00:00"
        assert body["checkedAt"]
        # No EventKit write method was reached (they would have raised).
        assert set(store.calls) <= {"auth_status", "query_events"}

    def test_added_owned_event_flagged(self, logical_day, monkeypatch):
        _write_ledger({"Mint Afternoon": "E1"}, [_row("Mint Afternoon")])
        monkeypatch.setattr(
            main_mod.shadow, "_own_calendar_ids", lambda config, store: {"cal-blocks"})
        store = FakeStore([
            _event("E1", "Mint Afternoon", _dt("15:00"), _dt("15:30")),
            _event("E9", "Swift", _dt("17:00"), _dt("17:30")),
        ])
        body = _client(monkeypatch, store).get("/calendar-drift").json()
        assert [row["name"] for row in body["added"]] == ["Swift"]

    def test_no_commit_today_skips_the_calendar_read(self, logical_day, monkeypatch):
        store = FakeStore([])
        body = _client(monkeypatch, store).get("/calendar-drift").json()
        assert body["hasCommit"] is False
        assert body["reason"] == "no commit today"
        assert body["moved"] == [] and body["missing"] == [] and body["added"] == []
        assert store.calls == []

    def test_unauthorized_calendar_degrades_to_200(self, logical_day, monkeypatch):
        _write_ledger({"Mint Afternoon": "E1"}, [_row("Mint Afternoon")])
        store = FakeStore([], auth="denied")
        response = _client(monkeypatch, store).get("/calendar-drift")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "calendar-unavailable"
        assert body["reason"]
        assert body["moved"] == [] and body["added"] == []

    def test_shared_store_failure_degrades_to_200(self, logical_day, monkeypatch):
        _write_ledger({"Mint Afternoon": "E1"}, [_row("Mint Afternoon")])

        def _boom():
            raise RuntimeError("no EventKit in this process")

        monkeypatch.setattr(calendar_bridge, "shared_store", _boom)
        response = TestClient(main_mod.create_app()).get("/calendar-drift")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "calendar-unavailable"
        assert body["reason"]
