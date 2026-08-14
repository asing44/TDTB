"""T14 Option A regression — one process-wide EventKit store (2026-07-23).

The 2026-07-23 qualification attempt failed closed (HTTP 422) because the
live-commit path constructed a second EKEventStore instance that saw zero
calendars while the GET-proven /plan-inputs store in the same process was
healthy. These tests pin the approved fix: preflight (read), shadow, and
live commit all resolve to the same shared store construction, and a plan
with calendar rows refuses before any write unless that store sees at least
one writable calendar.
"""
from __future__ import annotations

import re
import sys
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

APP_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(APP_DIR))
import calendar_bridge  # noqa: E402
import main as main_mod  # noqa: E402
import shadow  # noqa: E402

sys.path.insert(0, str(APP_DIR / "gather"))
import tdtb_gather as gather  # noqa: E402


class FakeCountingStore:
    """Stands in for EventStore; class-level counter catches extra constructions."""

    constructed = 0

    def __init__(self) -> None:
        type(self).constructed += 1
        self._cals: list[calendar_bridge.CalendarInfo] = []

    def auth_status(self) -> str:
        return "fullAccess"

    def calendars(self):
        return list(self._cals)

    def query_events(self, start, end, calendar_ids=None):
        return []


@pytest.fixture
def counting_store(monkeypatch):
    FakeCountingStore.constructed = 0
    monkeypatch.setattr(calendar_bridge, "EventStore", FakeCountingStore)
    monkeypatch.setattr(calendar_bridge, "_shared_store", None)
    return FakeCountingStore


# ---------------------------------------------------------------- singleton

class TestSharedStoreSingleton:
    def test_shared_store_constructs_exactly_once(self, counting_store):
        first = calendar_bridge.shared_store()
        second = calendar_bridge.shared_store()
        assert first is second
        assert counting_store.constructed == 1

    def test_failed_construction_is_not_cached(self, counting_store, monkeypatch):
        def boom():
            raise RuntimeError("no EventKit")

        monkeypatch.setattr(calendar_bridge, "EventStore", boom)
        with pytest.raises(RuntimeError):
            calendar_bridge.shared_store()
        # a later successful construction still works
        monkeypatch.setattr(calendar_bridge, "EventStore", FakeCountingStore)
        assert calendar_bridge.shared_store() is calendar_bridge.shared_store()


# ------------------------------------------------- read + shadow reuse

class TestSharedAcrossPhases:
    def test_read_clients_return_the_shared_store(self, counting_store, monkeypatch, tmp_path):
        # keep the Todoist side inert regardless of this machine's token state
        monkeypatch.setattr(
            shadow.todoist_client, "load_token",
            lambda p: (_ for _ in ()).throw(RuntimeError("no token")),
        )
        _todoist, store = main_mod.build_real_read_clients(tmp_path, {})
        assert store is calendar_bridge.shared_store()
        assert counting_store.constructed == 1

    def test_gather_live_state_reuses_the_preflight_store(
        self, counting_store, monkeypatch, tmp_path
    ):
        monkeypatch.setattr(
            shadow.todoist_client, "load_token",
            lambda p: (_ for _ in ()).throw(RuntimeError("no token")),
        )
        preflight = calendar_bridge.shared_store()
        state = shadow.gather_live_state({}, tmp_path)
        assert counting_store.constructed == 1  # no second construction
        assert state.get("calendar_unavailable") is not True
        assert state["calendar_events"] == []
        assert calendar_bridge.shared_store() is preflight


# ------------------------------------------- no stray constructions (static)

def test_no_module_constructs_its_own_eventstore():
    """The divergence class is any call site building a private store.

    Only calendar_bridge.shared_store() may construct EventStore; every app
    module must go through it. (String mentions like the external_sources
    degrade message are not calls and use the bare class name.)
    """
    pattern = re.compile(r"calendar_bridge\.EventStore\(\)")
    offenders: list[str] = []
    for py in APP_DIR.glob("*.py"):
        for i, line in enumerate(py.read_text(encoding="utf-8").splitlines(), 1):
            if pattern.search(line):
                offenders.append(f"{py.name}:{i}: {line.strip()}")
    assert offenders == [], (
        "private EventStore() constructions found — use calendar_bridge.shared_store():\n"
        + "\n".join(offenders)
    )


# ------------------------------------- writable-calendar precondition (422)

class FakeStoreWithCals:
    def __init__(self, cals):
        self._cals = cals

    def calendars(self):
        return list(self._cals)


def _fake_live_state(config, vault_root):
    return {
        "todoist_tasks": [],
        "calendar_events": [],
        "vault_frontmatter": {},
        "daily_note_text": "# Journal\n",
    }


CAL_SEQUENCE = {"sequence": [{"id": "🌊 Minting", "start": "14:00", "end": "15:00", "zone": "any"}]}


class TestWritableCalendarPrecondition:
    @pytest.fixture
    def client(self, tmp_path) -> TestClient:
        vault = tmp_path / "vault-root"
        vault.mkdir()
        app = main_mod.create_app(vault_root=vault)
        c = TestClient(app)
        c.app_token = app.state.token
        return c

    def _post_live(self, client, store, monkeypatch):
        monkeypatch.setattr(shadow, "gather_live_state", _fake_live_state)
        # FEEDBACK-24: live commit is gated on an explicit Day Setup confirm —
        # seed it so the calendar-writability precondition is what's tested.
        token = {"X-TDTB-Token": client.app_token}
        assert client.post("/day-setup", json={}, headers=token).status_code == 200
        touched = {"todoist": False}

        class TripwireTodoist:
            def __getattr__(self, name):
                touched["todoist"] = True
                raise AssertionError("no write client may be touched before the gate")

        client.app.state.build_commit_clients = lambda v, cfg: (TripwireTodoist(), store)
        r = client.post(
            "/commit?mode=live",
            headers={"X-TDTB-Token": client.app_token},
            json={"digest": {"assigned": [], "suggested": []},
                  "sequence": CAL_SEQUENCE, "config": {}},
        )
        return r, touched

    def test_calendar_rows_with_no_writable_calendar_422(self, client, monkeypatch):
        store = FakeStoreWithCals([
            calendar_bridge.CalendarInfo(
                title="⬜ Blocks", identifier="X", writable=False, source="fake"
            ),
        ])
        r, touched = self._post_live(client, store, monkeypatch)
        assert r.status_code == 422
        assert "plan refused" in r.json()["detail"]
        assert "writable" in r.json()["detail"]
        assert touched["todoist"] is False

    def test_calendar_rows_with_absent_store_422(self, client, monkeypatch):
        r, touched = self._post_live(client, None, monkeypatch)
        assert r.status_code == 422
        assert "plan refused" in r.json()["detail"]
        assert touched["todoist"] is False


class TestHasWritableCalendar:
    def test_none_store_is_false(self):
        assert calendar_bridge.has_writable_calendar(None) is False

    def test_erroring_store_is_false(self):
        class Boom:
            def calendars(self):
                raise RuntimeError("degraded")

        assert calendar_bridge.has_writable_calendar(Boom()) is False

    def test_read_only_calendars_are_false(self):
        store = FakeStoreWithCals([
            calendar_bridge.CalendarInfo("A", "1", False, "s"),
        ])
        assert calendar_bridge.has_writable_calendar(store) is False

    def test_one_writable_calendar_is_true(self):
        store = FakeStoreWithCals([
            calendar_bridge.CalendarInfo("A", "1", False, "s"),
            calendar_bridge.CalendarInfo("⬜ Blocks", "2", True, "s"),
        ])
        assert calendar_bridge.has_writable_calendar(store) is True
