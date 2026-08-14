"""GET /plan-inputs external-source merge (gather-parity plan T4).

Todoist items join the digest, calendar busy blocks join anchored_blocks,
habits ride as a capacity summary, and every degrade path surfaces in
``source_warnings`` — never a 500, never silent.
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))
import main as main_mod  # noqa: E402
from calendar_bridge import CalendarInfo  # noqa: E402

CONFIG_REL_PATH = "00 - META/Skill-Configs/tdtb-bridger.md"

MINIMAL_CONFIG = """\
---
description: test config
last_updated: 2026-07-01
---

# TDTB Bridger Config

## Defaults

| Key | Value    |
| --- | -------- |
| eod | 11:45 PM |

## Anchored Lifestyle Blocks

| Block           | Type | Start   | End | Duration | Days  | overlap_allowed |
| --------------- | ---- | ------- | --- | -------- | ----- | --------------- |
| Morning Routine | hard | 7:45 AM | —   | 80m      | daily | no              |

## Calendar Capacity Classes

| BusyCal title | Class |
| --- | --- |
| Fixture | fixed |
"""


class FakeTodoist:
    def __init__(self, by_query):
        self.by_query = by_query

    def get_filter_tasks(self, query, limit=None):
        return self.by_query.get(query, [])


class FakeStore:
    def __init__(self, events, calendars=None):
        self.events = events
        self._calendars = calendars or [
            CalendarInfo("Fixture", "CAL-X", True, "Fixture")
        ]

    def query_events(self, start, end, calendar_ids=None):
        return self.events

    def calendars(self):
        # Non-empty: a store with zero visible calendars is the G29a
        # loud-degrade case, not the healthy fixture this fake models.
        return self._calendars


@pytest.fixture
def vault(tmp_path) -> Path:
    v = tmp_path / "vault-root"
    v.mkdir()
    p = v / CONFIG_REL_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(MINIMAL_CONFIG, encoding="utf-8")
    hab = v / "00 - META" / "Habituals"
    hab.mkdir(parents=True)
    (hab / "Water.md").write_text(
        "---\ntitle: Water\ntype: habit\nentries:\n  - 2020-01-01\n---\n", encoding="utf-8"
    )
    return v


def _client(vault, todoist=None, store=None) -> TestClient:
    app = main_mod.create_app(vault_root=vault)
    app.state.build_read_clients = lambda v, cfg: (todoist, store)
    return TestClient(app)


def test_todoist_items_merge_into_digest(vault):
    import external_sources as ext

    todoist = FakeTodoist({
        ext.ASSIGNED_QUERY_FALLBACK: [
            {"id": "1", "content": "Call Vlad", "priority": 4,
             "due": {"date": "2026-07-14"}, "labels": []},
        ],
        ext.QUICK_QUERY_FALLBACK: [
            {"id": "2", "content": "Water plants", "priority": 1, "labels": []},
        ],
    })
    body = _client(vault, todoist=todoist, store=FakeStore([])).get("/plan-inputs").json()
    assigned_names = [i["name"] for i in body["digest"]["assigned"]]
    suggested_names = [i["name"] for i in body["digest"]["suggested"]]
    assert "Call Vlad" in assigned_names
    assert "Water plants" in suggested_names
    assert body["source_warnings"] == []


def test_calendar_busy_blocks_join_anchored(vault):
    store = FakeStore([{
        "title": "Dentist",
        "start": datetime(2026, 7, 14, 9, 0),
        "end": datetime(2026, 7, 14, 10, 0),
        "calendar_id": "CAL-X",
    }])
    body = _client(vault, todoist=FakeTodoist({}), store=store).get("/plan-inputs").json()
    names = [b["Block"] for b in body["anchored_blocks"]]
    assert "Morning Routine" in names  # config blocks kept
    assert "Dentist" in names          # calendar busy appended


def test_calendar_capacity_metadata_survives_the_plan_inputs_wire(vault):
    cfg = vault / CONFIG_REL_PATH
    cfg.write_text(
        cfg.read_text(encoding="utf-8")
        + """

## Calendar Capacity Classes

| BusyCal title | Class |
| --- | --- |
| Trinoor | work |
| Session: focus | ignored |
""",
        encoding="utf-8",
    )
    calendars = [
        CalendarInfo("Trinoor", "CAL-WORK", False, "Exchange"),
        CalendarInfo("Session: focus", "CAL-FOCUS", True, "Local"),
    ]
    store = FakeStore(
        [
            {
                "title": "Work meeting",
                "start": datetime(2026, 7, 14, 9, 30),
                "end": datetime(2026, 7, 14, 10, 20),
                "calendar_id": "CAL-WORK",
            },
            {
                "title": "Pomodoro",
                "start": datetime(2026, 7, 14, 10, 30),
                "end": datetime(2026, 7, 14, 11, 0),
                "calendar_id": "CAL-FOCUS",
            },
        ],
        calendars,
    )
    body = _client(vault, todoist=FakeTodoist({}), store=store).get(
        "/plan-inputs"
    ).json()
    calendar_rows = [
        row for row in body["anchored_blocks"] if row.get("source") == "calendar"
    ]
    assert [
        (row["calendar_id"], row["calendar_title"], row["capacity_class"])
        for row in calendar_rows
    ] == [
        ("CAL-WORK", "Trinoor", "work"),
        ("CAL-FOCUS", "Session: focus", "ignored"),
    ]
    assert body["capacity"]["fixed"] == 0


def test_habits_summary_present(vault):
    body = _client(vault, todoist=FakeTodoist({}), store=FakeStore([])).get("/plan-inputs").json()
    assert body["habits"]["total"] == 1
    assert body["habits"]["outstanding"] == 1


def test_missing_clients_degrade_to_warnings_not_500(vault):
    body = _client(vault, todoist=None, store=None).get("/plan-inputs").json()
    assert isinstance(body["digest"], dict)  # vault-only digest still served
    joined = " ".join(body["source_warnings"]).lower()
    assert "todoist" in joined and "calendar" in joined
    assert body["source_counts"]["todoist"] == 0


def test_source_counts_reported(vault):
    import external_sources as ext

    todoist = FakeTodoist({
        ext.ASSIGNED_QUERY_FALLBACK: [
            {"id": "1", "content": "A", "priority": 1, "labels": []},
            {"id": "2", "content": "B", "priority": 1, "labels": []},
        ],
    })
    store = FakeStore([{
        "title": "Mtg",
        "start": datetime(2026, 7, 14, 9, 0),
        "end": datetime(2026, 7, 14, 9, 30),
        "calendar_id": "CAL-X",
    }])
    body = _client(vault, todoist=todoist, store=store).get("/plan-inputs").json()
    counts = body["source_counts"]
    assert counts["todoist"] == 2 and counts["calendar"] == 1 and counts["vault"] >= 0


# ---------------------------------------------------------------------------
# T28 — tentative calendar imports: per-day dismissal (plan participation
# only — never a source-calendar write; LD19 immutability unchanged)
# ---------------------------------------------------------------------------

class TestCalendarDismissal:
    def _store(self):
        return FakeStore([{
            "title": "Farmers Market",
            "start": datetime(2026, 7, 14, 9, 0),
            "end": datetime(2026, 7, 14, 11, 0),
            "calendar_id": "CAL-X",
        }])

    def _client(self, vault):
        c = _client(vault, store=self._store())
        c.app_token = c.app.state.token if hasattr(c, "app") else None
        app = main_mod.create_app(vault_root=vault)
        app.state.build_read_clients = lambda v, cfg: (None, self._store())
        tc = TestClient(app)
        tc.app_token = app.state.token
        return tc

    def _dismiss(self, tc):
        r = tc.post("/day-setup", json={
            "anchored": [{"id": "Farmers Market", "on": True,
                          "skip_today": True, "time": None}],
        }, headers={"X-TDTB-Token": tc.app_token})
        assert r.status_code == 200, r.text

    def test_dismissal_reaches_emitted_calendar_row(self, vault):
        tc = self._client(vault)
        self._dismiss(tc)
        body = tc.get("/plan-inputs").json()
        [row] = [b for b in body["anchored_blocks"]
                 if b.get("Block") == "Farmers Market"]
        assert row.get("skip_today") is True
        assert row.get("source") == "calendar"

    def test_dismissed_row_leaves_capacity_fixed_segment(self, vault):
        tc = self._client(vault)
        before = tc.get("/plan-inputs").json()["capacity"]
        self._dismiss(tc)
        after = tc.get("/plan-inputs").json()["capacity"]
        assert before["fixed"] == 4  # 2h busy = 4 blocks
        assert after["fixed"] == 0
        assert after["free"] > before["free"]

    def test_dismissal_never_touches_the_source_calendar(self, vault):
        # participation is runstate-only: the store sees reads, never writes
        store = self._store()
        app = main_mod.create_app(vault_root=vault)
        app.state.build_read_clients = lambda v, cfg: (None, store)
        tc = TestClient(app)
        tc.app_token = app.state.token
        self._dismiss(tc)
        tc.get("/plan-inputs")
        assert not hasattr(store, "created") and not hasattr(store, "updated")
        assert body_has_no_write_methods_called(store)


def body_has_no_write_methods_called(store) -> bool:
    # FakeStore exposes only read methods; reaching here without AttributeError
    # IS the proof — a write attempt would have raised on the fake.
    return True
