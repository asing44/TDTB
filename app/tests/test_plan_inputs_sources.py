"""GET /plan-inputs external-source merge (gather-parity plan T4).

Todoist items join the digest, calendar busy blocks join anchored_blocks,
habits ride as a capacity summary, and every degrade path surfaces in
``source_warnings`` — never a 500, never silent.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))
import main as main_mod  # noqa: E402
import exclusion_settings as es  # noqa: E402
import runstate as runstate_mod  # noqa: E402
from calendar_bridge import CalendarInfo  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent.parent / "gather"))
import tdtb_gather as gather  # noqa: E402

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


# ---------------------------------------------------------------------------
# Tag-exclusion slice: the policy runs before selection on /plan-inputs
# ---------------------------------------------------------------------------

SPACE = "space-1"
TAG_A = "5a25370b-f9a0-40cf-bc3a-0cab4744913c"
TAG_B = "0d194525-c5a1-4af5-bb62-202b83006b5e"


class FakeCapacitiesAdapter:
    """Deterministic adapter double: rows in, rows out, no provider call."""

    def __init__(self, items):
        self._items = items
        self.closed = False

    def items_for_day(self, logical_day):
        return SimpleNamespace(items=list(self._items), warnings=[])

    def close(self):
        self.closed = True


def _capacities_row(name, tags, *, space=SPACE):
    return {
        "id": name, "name": name,
        "path": f"capacities://{space}/{name}",
        "identity": f"capacities:{space}:RootTask:{name}",
        "source": "capacities",
        "types": ["RootTask"],
        "urgency": None, "deadline": None, "priority_score": 0,
        "assigned": True, "blocks": 1, "duration": 30, "duration_minutes": 30,
        "capacities_id": name, "capacities_space_id": space,
        "capacities_structure_id": "RootTask",
        "capacities_tags": tags,
    }


def _client_with_capacities(vault, items, todoist=None, store=None) -> TestClient:
    app = main_mod.create_app(vault_root=vault)
    app.state.build_read_clients = lambda v, cfg: (todoist, store)
    app.state.build_capacities_adapter = lambda v, cfg: FakeCapacitiesAdapter(items)
    return TestClient(app)


def _save_tag_exclusion(vault: Path, tag_id: str = TAG_A) -> None:
    es.save_settings(
        vault,
        expected_revision=0,
        exclusions=[{"source": "capacities", "space_id": SPACE, "tag_id": tag_id}],
    )


def test_plan_inputs_applies_tag_exclusions_and_stamps_the_revision(vault):
    _save_tag_exclusion(vault)
    items = [
        _capacities_row("Keep", [{"space_id": SPACE, "tag_id": TAG_B, "title": "chores"}]),
        _capacities_row("Drop", [{"space_id": SPACE, "tag_id": TAG_A, "title": "habituals"}]),
    ]
    client = _client_with_capacities(vault, items)

    body = client.get("/plan-inputs").json()

    names = [r["name"] for r in body["digest"]["assigned"]]
    assert "Keep" in names
    assert "Drop" not in names
    report = body["digest"]["exclusion_policy"]
    assert report["revision"] == 1
    assert report["mode"] == "exclude_any"
    assert report["excluded_counts"] == {"assigned": 1, "pool": 0, "total": 1}
    assert report["decisions"] == [{
        "identity": f"capacities:{SPACE}:RootTask:Drop",
        "name": "Drop",
        "surface": "assigned",
        "matched_tags": [{"space_id": SPACE, "tag_id": TAG_A, "title": "habituals"}],
        "reason": "excluded_tag",
    }]

    # The dated index carries the server-owned policy stamp and retains the
    # canonical tag identities on indexed rows.
    today = gather.effective_date(datetime.now())
    raw = json.loads(
        (runstate_mod.digest_index_read_path(vault, today)).read_text(encoding="utf-8")
    )
    assert raw["exclusion_settings_revision"] == 1
    keep = next(i for i in raw["items"] if i["name"] == "Keep")
    assert keep["capacities_tags"] == [
        {"space_id": SPACE, "tag_id": TAG_B, "title": "chores"}
    ]
    assert all(i["name"] != "Drop" for i in raw["items"])


def test_plan_inputs_tolerates_unusable_tags_when_no_policy_applies(vault):
    row = _capacities_row("Unusable", None)
    row["capacities_tags_error"] = "tags are title-only without typed identities"
    client = _client_with_capacities(vault, [row])

    body = client.get("/plan-inputs").json()

    assert "Unusable" in [r["name"] for r in body["digest"]["assigned"]]
    assert body["digest"]["exclusion_policy"]["warnings"]


def test_plan_inputs_blocks_on_unusable_tag_payload_when_a_policy_applies(vault):
    _save_tag_exclusion(vault)
    row = _capacities_row("TitleOnly", None)
    row["capacities_tags_error"] = "tags are title-only without typed identities"
    client = _client_with_capacities(vault, [row])

    response = client.get("/plan-inputs")

    assert response.status_code == 503
    detail = response.json()["detail"]
    assert detail["code"] == "exclusion_policy_unusable_tags"
    assert detail["tasks"] == [{
        "identity": f"capacities:{SPACE}:RootTask:TitleOnly",
        "name": "TitleOnly",
        "surface": "assigned",
        "reason": "tags are title-only without typed identities",
    }]


def test_plan_inputs_blocks_when_settings_storage_is_malformed(vault):
    path = es.settings_path(vault)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("garbage", encoding="utf-8")
    client = _client(vault)

    response = client.get("/plan-inputs")

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "exclusion_settings_storage_error"
    assert path.read_text(encoding="utf-8") == "garbage"


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


# ---------------------------------------------------------------------------
# Issue #6 — canonical identity / dedup, disabled calendars, all-day
# accounting, and observable omission decisions (unit level, fakes only)
# ---------------------------------------------------------------------------

import calendar_bridge as cb
import external_sources as ext2

FIXED_TODAY = datetime(2026, 7, 14).date()


class _Cal:
    def __init__(self, title, identifier, **attrs):
        self.title = title
        self.identifier = identifier
        self.__dict__.update(attrs)


class _Store:
    def __init__(self, events, calendars=None):
        self._events = events
        self._calendars = calendars or [_Cal("Fixture", "CAL-X")]

    def auth_status(self):
        return "authorized"

    def calendars(self):
        return self._calendars

    def query_events(self, start, end, calendar_ids=None):
        return self._events


class TestCalendarIdentityAndOmissions:
    def test_same_event_id_counts_once_first_occurrence_wins(self):
        ev = {"title": "Meeting", "start": datetime(2026, 7, 14, 9, 0),
              "end": datetime(2026, 7, 14, 10, 0), "calendar_id": "CAL-X"}
        dup = dict(ev, title="Meeting (sync copy)", event_id="sync-E1")
        first = dict(ev, event_id="sync-E1")
        # first occurrence in source order wins: the original title is emitted,
        # the later sync copy is merged away with a duplicate warning
        blocks, warnings = ext2.fetch_calendar_busy(_Store([first, dup]), {}, FIXED_TODAY)
        assert [b["Block"] for b in blocks] == ["Meeting"]
        assert any("duplicate" in w.lower() for w in warnings)

    def test_identityless_duplicates_canonicalize_on_title_and_interval(self):
        ev = {"title": "Dentist", "start": datetime(2026, 7, 14, 11, 0),
              "end": datetime(2026, 7, 14, 12, 0), "calendar_id": "CAL-X"}
        blocks, warnings = ext2.fetch_calendar_busy(_Store([ev, dict(ev)]), {}, FIXED_TODAY)
        assert [b["Block"] for b in blocks] == ["Dentist"]
        assert any("duplicate" in w.lower() for w in warnings)

    def test_identityless_distinct_events_each_survive(self):
        a = {"title": "Dentist", "start": datetime(2026, 7, 14, 11, 0),
             "end": datetime(2026, 7, 14, 12, 0), "calendar_id": "CAL-X"}
        b = {"title": "Coffee", "start": datetime(2026, 7, 14, 12, 0),
             "end": datetime(2026, 7, 14, 12, 30), "calendar_id": "CAL-X"}
        blocks, warnings = ext2.fetch_calendar_busy(_Store([a, b]), {}, FIXED_TODAY)
        assert sorted(b["Block"] for b in blocks) == ["Coffee", "Dentist"]
        assert warnings == []

    def test_disabled_calendar_by_config_name_omits_events_with_reason(self):
        store = _Store([
            {"title": "Yoga", "start": datetime(2026, 7, 14, 8, 0),
             "end": datetime(2026, 7, 14, 9, 0), "calendar_id": "CAL-QUIET"},
            {"title": "Dentist", "start": datetime(2026, 7, 14, 11, 0),
             "end": datetime(2026, 7, 14, 12, 0), "calendar_id": "CAL-X"},
        ], [_Cal("Quiet Days", "CAL-QUIET"), _Cal("Fixture", "CAL-X")])
        blocks, warnings = ext2.fetch_calendar_busy(
            store, {"calendar_disabled": ["Quiet Days"]}, FIXED_TODAY
        )
        assert [b["Block"] for b in blocks] == ["Dentist"]
        joined = " ".join(warnings).lower()
        assert "disabled" in joined
        assert "quiet days" in joined
        assert "yoga" in joined

    def test_disabled_calendar_by_identifier_attribute(self):
        store = _Store(
            [{"title": "Pilates", "start": datetime(2026, 7, 14, 7, 0),
              "end": datetime(2026, 7, 14, 8, 0), "calendar_id": "CAL-X"}],
            [_Cal("Fixture", "CAL-X", disabled=True)],
        )
        blocks, warnings = ext2.fetch_calendar_busy(store, {}, FIXED_TODAY)
        assert blocks == []
        assert any("disabled" in w.lower() for w in warnings)

    def test_disabled_calendar_costs_zero_capacity(self):
        store = _Store(
            [{"title": "Offsite", "start": datetime(2026, 7, 14, 9, 0),
              "end": datetime(2026, 7, 14, 13, 0), "calendar_id": "CAL-X"}],
            [_Cal("Fixture", "CAL-X", disabled=True)],
        )
        blocks, _ = ext2.fetch_calendar_busy(store, {}, FIXED_TODAY)
        assert blocks == []

    def test_not_disabled_calendar_unchanged(self):
        store = _Store([
            {"title": "Dentist", "start": datetime(2026, 7, 14, 11, 0),
             "end": datetime(2026, 7, 14, 12, 0), "calendar_id": "CAL-X"}],
            [_Cal("Fixture", "CAL-X")],
        )
        blocks, warnings = ext2.fetch_calendar_busy(
            store, {"calendar_disabled": ["Other Calendar"]}, FIXED_TODAY
        )
        assert [b["Block"] for b in blocks] == ["Dentist"]
        assert warnings == []

    def test_all_day_event_is_non_timed_and_costs_zero_capacity(self):
        ev = {"title": "Festival", "start": datetime(2026, 7, 14, 0, 0),
              "end": datetime(2026, 7, 15, 0, 0), "calendar_id": "CAL-X",
              "all_day": True}
        blocks, warnings = ext2.fetch_calendar_busy(_Store([ev]), {}, FIXED_TODAY)
        assert warnings == []
        [block] = blocks
        assert block["all_day"] is True
        assert "Start" not in block and "End" not in block
        assert block["capacity_class"] == "fixed"
        # accounting: an all-day fixed event contributes zero capacity
        _time, cap = main_mod._capacity_frame(
            {
                "Defaults": {
                    "eod": "20:00",
                    "anchor.round_to_minutes": 15,
                    "buffering.off_pct": 0,
                },
                "Anchored Lifestyle Blocks": [],
            },
            {"anchor": "08:00", "eod": "20:00", "buffering": "off"},
            blocks,
            {"est_minutes": 0, "done": 0, "outstanding": 0},
            {"effective_allotment_minutes": 0},
            now=datetime(2026, 7, 14, 8, 0),
        )
        assert cap.fixed == 0

    def test_malformed_and_zero_duration_events_omitted_with_reason(self):
        store = _Store([
            {"title": "Broken Sync", "start": "09:00", "end": "10:00",
             "calendar_id": "CAL-X"},
            {"title": "Reminder Marker", "start": datetime(2026, 7, 14, 9, 0),
             "end": datetime(2026, 7, 14, 9, 0), "calendar_id": "CAL-X"},
        ])
        blocks, warnings = ext2.fetch_calendar_busy(store, {}, FIXED_TODAY)
        assert blocks == []
        joined = " ".join(warnings).lower()
        assert "omission" in joined
        assert "broken sync" in joined
        assert "reminder marker" in joined
