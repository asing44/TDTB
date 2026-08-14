"""TDD suite for external_sources — read-side aggregation (gather-parity plan T1–T3).

Fakes stand in for TodoistClient / EventStore; no network, no EventKit.
"""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import pytest

import external_sources as ext
from calendar_bridge import CalendarInfo


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class FakeTodoist:
    def __init__(self, by_query: dict[str, list[dict]] | None = None, raise_exc: Exception | None = None):
        self.by_query = by_query or {}
        self.raise_exc = raise_exc
        self.calls: list[str] = []

    def get_filter_tasks(self, query: str, limit: int | None = None) -> list[dict]:
        self.calls.append(query)
        if self.raise_exc:
            raise self.raise_exc
        return self.by_query.get(query, [])


class FakeStore:
    def __init__(
        self,
        events: list[dict] | None = None,
        raise_exc: Exception | None = None,
        calendars: list[CalendarInfo] | None = None,
    ):
        self.events = events or []
        self.raise_exc = raise_exc
        self.queries: list[tuple] = []
        if calendars is not None:
            self.calendars = lambda: calendars

    def query_events(self, start, end, calendar_ids=None) -> list[dict]:
        self.queries.append((start, end, calendar_ids))
        if self.raise_exc:
            raise self.raise_exc
        return self.events


def _task(tid="101", content="Call Vlad", priority=4, due="2026-07-14", labels=None, duration=None):
    t: dict = {"id": tid, "content": content, "priority": priority, "labels": labels or []}
    if due is not None:
        t["due"] = {"date": due}
    if duration is not None:
        t["duration"] = duration
    return t


TODAY = date(2026, 7, 14)


# ---------------------------------------------------------------------------
# T1 — Todoist read mapping
# ---------------------------------------------------------------------------

class TestFetchTodoistItems:
    def test_assigned_and_pool_split_by_query(self):
        client = FakeTodoist({
            ext.ASSIGNED_QUERY_FALLBACK: [_task("1", "Call Vlad")],
            ext.QUICK_QUERY_FALLBACK: [_task("2", "Water plants", priority=1, due=None)],
        })
        assigned, pool, warnings = ext.fetch_todoist_items(client, {})
        assert warnings == []
        assert [i["name"] for i in assigned] == ["Call Vlad"]
        assert [i["name"] for i in pool] == ["Water plants"]
        assert assigned[0]["assigned"] is True and pool[0]["assigned"] is False

    def test_item_shape(self):
        client = FakeTodoist({ext.ASSIGNED_QUERY_FALLBACK: [
            _task("42", "Ship memo", priority=3, due="2026-07-14",
                  duration={"amount": 25, "unit": "minute"}),
        ]})
        assigned, _, _ = ext.fetch_todoist_items(client, {})
        item = assigned[0]
        assert item["path"] == "todoist://42"
        assert item["todoist_id"] == "42"
        assert item["source"] == "todoist"
        assert item["types"] == ["todoist"]
        # API priority scale: 4 = highest — same direction as vault urgency.
        assert item["urgency"] == 3
        assert item["priority_score"] == 3.0
        assert item["deadline"] == "2026-07-14"
        assert item["duration"] == 25
        assert item["is_recurring"] is False

    def test_recurring_flag_carried(self):
        client = FakeTodoist({ext.ASSIGNED_QUERY_FALLBACK: [
            _task("9", "M1.0"), ]})
        client.by_query[ext.ASSIGNED_QUERY_FALLBACK][0]["due"] = {
            "datetime": "2026-07-14T12:00:00", "is_recurring": True}
        assigned, _, _ = ext.fetch_todoist_items(client, {})
        assert assigned[0]["is_recurring"] is True
        assert assigned[0]["scheduled_start"] == "12:00"

    def test_datetime_due_trimmed_to_date(self):
        client = FakeTodoist({ext.ASSIGNED_QUERY_FALLBACK: [
            _task("7", "Standup", due="2026-07-14"), ]})
        client.by_query[ext.ASSIGNED_QUERY_FALLBACK][0]["due"] = {"datetime": "2026-07-14T09:30:00"}
        assigned, _, _ = ext.fetch_todoist_items(client, {})
        assert assigned[0]["deadline"] == "2026-07-14"

    def test_reminders_excluded(self):
        client = FakeTodoist({ext.ASSIGNED_QUERY_FALLBACK: [
            _task("1", "Real task"),
            _task("2", "🔔 Nudge: drink water"),
            _task("3", "Labelled", labels=["🔔Reminder"]),
        ]})
        assigned, _, _ = ext.fetch_todoist_items(client, {})
        assert [i["name"] for i in assigned] == ["Real task"]

    def test_quick_task_also_due_today_dedupes_to_assigned(self):
        both = _task("9", "Quick + due")
        client = FakeTodoist({
            ext.ASSIGNED_QUERY_FALLBACK: [both],
            ext.QUICK_QUERY_FALLBACK: [both],
        })
        assigned, pool, _ = ext.fetch_todoist_items(client, {})
        assert [i["todoist_id"] for i in assigned] == ["9"]
        assert pool == []

    def test_config_overrides_queries(self):
        client = FakeTodoist({"p1 & today": [], "@zap": []})
        cfg = {"todoist.read_query.assigned": "p1 & today", "todoist.read_query.quick": "@zap"}
        ext.fetch_todoist_items(client, cfg)
        assert client.calls == ["p1 & today", "@zap"]

    def test_error_degrades_with_warning(self):
        client = FakeTodoist(raise_exc=RuntimeError("401"))
        assigned, pool, warnings = ext.fetch_todoist_items(client, {})
        assert assigned == [] and pool == []
        assert len(warnings) == 1 and "todoist" in warnings[0].lower()

    def test_none_client_degrades_with_warning(self):
        assigned, pool, warnings = ext.fetch_todoist_items(None, {})
        assert assigned == [] and pool == []
        assert len(warnings) == 1


# ---------------------------------------------------------------------------
# T2 — Calendar busy blocks
# ---------------------------------------------------------------------------

def _event(title="Dentist", start="09:00", end="10:00", cal="CAL-OTHER"):
    return {
        "title": title,
        "start": datetime(2026, 7, 14, *map(int, start.split(":"))),
        "end": datetime(2026, 7, 14, *map(int, end.split(":"))),
        "calendar_id": cal,
    }


class TestFetchCalendarBusy:
    def test_events_map_to_anchored_block_shape(self):
        store = FakeStore([_event()])
        blocks, warnings = ext.fetch_calendar_busy(store, {}, TODAY)
        assert warnings == []
        assert blocks == [{
            "Block": "Dentist", "Start": "09:00", "End": "10:00",
            "source": "calendar", "calendar_id": "CAL-OTHER",
            "calendar_title": None, "capacity_class": "fixed",
        }]

    def test_calendar_identity_and_capacity_classes_are_preserved(self):
        calendars = [
            CalendarInfo("Trinoor", "CAL-WORK", False, "Exchange"),
            CalendarInfo("Session: focus", "CAL-FOCUS", True, "Local"),
            CalendarInfo("🙋‍♂️ Personal", "CAL-FIXED", True, "iCloud"),
            CalendarInfo("⬜ Blocks", "CAL-OWN", True, "Google"),
        ]
        cfg = {
            "calendar_ids": {"⬜ Blocks": "CAL-OWN"},
            "calendar_capacity_classes": [
                {"BusyCal title": "Trinoor", "Class": "work"},
                {"BusyCal title": "Session: focus", "Class": "ignored"},
            ],
        }
        store = FakeStore([
            _event("Work meeting", cal="CAL-WORK"),
            _event("Pomodoro evidence", cal="CAL-FOCUS"),
            _event("Dentist", cal="CAL-FIXED"),
            _event("Generated block", cal="CAL-OWN"),
        ], calendars=calendars)
        blocks, _ = ext.fetch_calendar_busy(store, cfg, TODAY)
        # "🙋‍♂️ Personal" is a KNOWN title with no configured class — frozen
        # contract 17 quarantines it rather than silently defaulting to fixed.
        assert [
            (b["calendar_id"], b["calendar_title"], b["capacity_class"])
            for b in blocks
        ] == [
            ("CAL-WORK", "Trinoor", "work"),
            ("CAL-FOCUS", "Session: focus", "ignored"),
            ("CAL-FIXED", "🙋‍♂️ Personal", "quarantined"),
            ("CAL-OWN", "⬜ Blocks", "ignored"),
        ]

    def test_own_output_id_stays_ignored_even_if_config_says_fixed(self):
        calendars = [
            CalendarInfo("⬜ Blocks", "CAL-OWN", True, "Google"),
        ]
        cfg = {
            "calendar_ids": {"⬜ Blocks": "CAL-OWN"},
            "calendar_capacity_classes": [
                {"BusyCal title": "⬜ Blocks", "Class": "fixed"},
            ],
        }
        blocks, _ = ext.fetch_calendar_busy(
            FakeStore([_event("Generated", cal="CAL-OWN")], calendars=calendars),
            cfg,
            TODAY,
        )
        assert blocks[0]["capacity_class"] == "ignored"

    def test_trinoor_named_source_calendar_never_label_guessed(self):
        # FEEDBACK-26: classification follows explicit exact-title rules or
        # ownership — never a name overlay. A "Trinoor"-titled read-only
        # source calendar with no configured class stays quarantined (known
        # but unreviewed, frozen contract 17); a TDTB-owned output row on the
        # configured Mint calendar stays ignored.
        calendars = [
            CalendarInfo("Trinoor", "CAL-WORK", False, "Exchange"),
            CalendarInfo("🟡 Mint", "CAL-MINT", True, "Google"),
        ]
        cfg = {"calendar_ids": {"mint": "CAL-MINT"}}
        blocks, warnings = ext.fetch_calendar_busy(
            FakeStore([
                _event("Work meeting", cal="CAL-WORK"),
                _event("Mint block", cal="CAL-MINT"),
            ], calendars=calendars),
            cfg,
            TODAY,
        )
        assert warnings == []
        assert [
            (b["calendar_id"], b["calendar_title"], b["capacity_class"])
            for b in blocks
        ] == [
            ("CAL-WORK", "Trinoor", "quarantined"),
            ("CAL-MINT", "🟡 Mint", "ignored"),
        ]

    def test_query_spans_today(self):
        store = FakeStore([])
        ext.fetch_calendar_busy(store, {}, TODAY)
        (start, end, cal_ids), = store.queries
        assert start == datetime(2026, 7, 14, 0, 0)
        assert end == datetime(2026, 7, 15, 0, 0)
        assert cal_ids is None  # all calendars; own-write filtered post-hoc

    def test_error_degrades_with_warning(self):
        store = FakeStore(raise_exc=RuntimeError("no grant"))
        blocks, warnings = ext.fetch_calendar_busy(store, {}, TODAY)
        assert blocks == [] and len(warnings) == 1 and "calendar" in warnings[0].lower()

    def test_none_store_degrades_with_warning(self):
        blocks, warnings = ext.fetch_calendar_busy(None, {}, TODAY)
        assert blocks == [] and len(warnings) == 1

    def test_all_day_event_stays_all_day_and_non_timed(self):
        # Supersedes the pre-contract skip behavior: frozen contract 18 says
        # all-day source events remain all-day and non-timed on the wire —
        # emitted with no Start/End so no timed planning path can convert them.
        ev = _event("Holiday")
        ev["all_day"] = True
        store = FakeStore([ev])
        blocks, _ = ext.fetch_calendar_busy(store, {}, TODAY)
        assert len(blocks) == 1
        assert blocks[0]["all_day"] is True
        assert "Start" not in blocks[0] and "End" not in blocks[0]

    def test_duplicate_events_by_identity_canonicalize_to_one_row(self):
        # Frozen contract 16: same canonical event identity -> one logical
        # group, so attendance and capacity each count once.
        ev1 = _event("Standup", start="09:00", end="09:30")
        ev2 = _event("Standup", start="09:00", end="09:30")
        ev1["id"] = "EVT-1"
        ev2["id"] = "EVT-1"
        blocks, _ = ext.fetch_calendar_busy(FakeStore([ev1, ev2]), {}, TODAY)
        assert len(blocks) == 1
        assert blocks[0]["Block"] == "Standup"

    def test_identity_less_events_keep_individual_rows(self):
        ev1 = _event("Standup", start="09:00", end="09:30")
        ev2 = _event("Standup", start="09:00", end="09:30")
        blocks, _ = ext.fetch_calendar_busy(FakeStore([ev1, ev2]), {}, TODAY)
        assert len(blocks) == 2  # no identity -> no canonicalization

    def test_known_unclassified_calendar_stays_quarantined(self):
        # Frozen contract 17: a KNOWN calendar title the user has not
        # classified must not silently default to fixed capacity.
        store = FakeStore(
            [_event("Mystery", cal="CAL-UNKNOWN")],
            calendars=[CalendarInfo("Some Random Cal", "CAL-UNKNOWN", True, "Local")],
        )
        blocks, _ = ext.fetch_calendar_busy(
            store, {"calendar_capacity_classes": {}}, TODAY
        )
        assert blocks[0]["capacity_class"] == "quarantined"

    def test_configured_class_beats_quarantine_default(self):
        calendars = [CalendarInfo("Personal", "CAL-P", True, "iCloud")]
        cfg = {
            "calendar_capacity_classes": [
                {"BusyCal title": "Personal", "Class": "fixed"},
            ],
        }
        blocks, _ = ext.fetch_calendar_busy(
            FakeStore([_event("Dentist", cal="CAL-P")], calendars=calendars), cfg, TODAY
        )
        assert blocks[0]["capacity_class"] == "fixed"

    def test_authorized_but_zero_calendars_degrades_loud(self):
        # G29a: EventKit grant didn't carry to a restarted process — authorized
        # yet store.calendars() == [] produced "0 events, no warnings",
        # indistinguishable from a legitimately free day. Must be loud.
        store = FakeStore([])
        store.calendars = lambda: []
        blocks, warnings = ext.fetch_calendar_busy(store, {}, TODAY)
        assert blocks == []
        assert len(warnings) == 1
        assert "calendar" in warnings[0].lower() and "grant" in warnings[0].lower()

    def test_fake_without_calendars_attr_unchanged(self):
        # Fakes/stores without a calendars() method are treated as fine —
        # same defensive-getattr pattern as auth_status.
        store = FakeStore([_event()])
        assert not hasattr(store, "calendars")
        blocks, warnings = ext.fetch_calendar_busy(store, {}, TODAY)
        assert warnings == []
        assert len(blocks) == 1

    def test_calendars_raising_degrades_loud(self):
        store = FakeStore([])
        def _boom():
            raise RuntimeError("EventKit died")
        store.calendars = _boom
        blocks, warnings = ext.fetch_calendar_busy(store, {}, TODAY)
        assert blocks == [] and len(warnings) == 1
        assert "calendar" in warnings[0].lower()


# ---------------------------------------------------------------------------
# T3 — Habit status (capacity summary, NOT digest items — skill § habits)
# ---------------------------------------------------------------------------

def _habit_note(dirpath: Path, name: str, entries: list[str], duration: int | None = None):
    dur = f"duration: {duration}\n" if duration is not None else ""
    dirpath.joinpath(name).write_text(
        f"---\ntitle: {name[:-3]}\ntype: habit\n{dur}entries:\n"
        + "".join(f"  - {e}\n" for e in entries)
        + "---\n\n# x\n",
        encoding="utf-8",
    )


class TestFetchHabitStatus:
    def test_done_vs_outstanding_split(self, tmp_path):
        hab = tmp_path / "00 - META" / "Habituals"
        hab.mkdir(parents=True)
        _habit_note(hab, "Water.md", ["2026-07-13", "2026-07-14"])
        _habit_note(hab, "Stretch.md", ["2026-07-13"])
        _habit_note(hab, "Timestamped.md", ["2026-07-14T00:00:00.000Z"])
        status, warnings = ext.fetch_habit_status(tmp_path, {}, TODAY)
        assert warnings == []
        assert status["total"] == 3
        assert status["done"] == 2
        assert status["outstanding"] == 1

    def test_outstanding_minutes_use_duration_then_fallback_and_round_up(self, tmp_path):
        hab = tmp_path / "00 - META" / "Habituals"
        hab.mkdir(parents=True)
        _habit_note(hab, "Long.md", [], duration=20)     # outstanding, 20 min
        _habit_note(hab, "NoDur.md", [])                 # outstanding, fallback 4 min
        status, _ = ext.fetch_habit_status(tmp_path, {}, TODAY)
        # 24 min rounded up to 15-min grain = 30
        assert status["est_minutes"] == 30

    def test_zero_duration_falls_back(self, tmp_path):
        # Live vault has `duration: 0` notes (e.g. Water) — 0 means "unset".
        hab = tmp_path / "00 - META" / "Habituals"
        hab.mkdir(parents=True)
        _habit_note(hab, "Zero.md", [], duration=0)
        status, _ = ext.fetch_habit_status(tmp_path, {}, TODAY)
        assert status["est_minutes"] == 15  # fallback 4 → rounds to 15

    def test_archived_subdir_ignored(self, tmp_path):
        hab = tmp_path / "00 - META" / "Habituals"
        (hab / "Archived").mkdir(parents=True)
        _habit_note(hab, "Live.md", [])
        _habit_note(hab / "Archived", "Old.md", [])
        status, _ = ext.fetch_habit_status(tmp_path, {}, TODAY)
        assert status["total"] == 1

    def test_missing_dir_degrades_with_warning(self, tmp_path):
        status, warnings = ext.fetch_habit_status(tmp_path, {}, TODAY)
        assert status["total"] == 0 and len(warnings) == 1

    def test_config_dir_and_grain_override(self, tmp_path):
        custom = tmp_path / "Habits"
        custom.mkdir()
        _habit_note(custom, "A.md", [])
        cfg = {
            "habits.source_directory": "Habits/",
            "habits.fallback_minutes_per_habit": 10,
            "habits.round_to_minutes": 30,
        }
        status, _ = ext.fetch_habit_status(tmp_path, cfg, TODAY)
        assert status["total"] == 1 and status["est_minutes"] == 30

    def test_unauthorized_store_warns_instead_of_empty_success(self):
        class DeniedStore(FakeStore):
            def auth_status(self):
                return "notDetermined"

        store = DeniedStore([_event()])  # events exist but access is ungranted
        blocks, warnings = ext.fetch_calendar_busy(store, {}, TODAY)
        assert blocks == []
        assert len(warnings) == 1 and "notDetermined" in warnings[0]
        assert store.queries == []  # never queried while unauthorized

    def test_zero_duration_event_dropped(self):
        # Reminder-style markers (T11 live: "2.0M" 23:00-23:00) are not busy
        # time and make the judgment prompt unsatisfiable if echoed.
        store = FakeStore([_event(title="2.0M", start="23:00", end="23:00"),
                           _event()])
        blocks, warnings = ext.fetch_calendar_busy(store, {}, TODAY)
        assert [b["Block"] for b in blocks] == ["Dentist"]
        assert warnings == []

    def test_full_access_status_is_authorized(self):
        # macOS 14+ reports "fullAccess" instead of "authorized" (T11 live
        # grant surfaced this: real grant still warned + dropped busy blocks).
        class FullAccessStore(FakeStore):
            def auth_status(self):
                return "fullAccess"

        store = FullAccessStore([_event()])
        blocks, warnings = ext.fetch_calendar_busy(store, {}, TODAY)
        assert warnings == []
        assert len(blocks) == 1


# ---------------------------------------------------------------------------
# Name disambiguation (T8 live-verify finding): sequence identity is
# name-keyed (timeline sets id = name), so a Todoist task sharing a name with
# a vault item breaks never-bump/duplicate validation.
# ---------------------------------------------------------------------------

class TestDisambiguateNames:
    def test_collision_gets_todoist_suffix(self):
        vault = [{"name": "Stillness", "path": "50/Stillness.md"}]
        ext_items = [{"name": "Stillness", "source": "todoist", "todoist_id": "1"}]
        out = ext.disambiguate_names(vault, ext_items)
        assert out[0]["name"] == "Stillness (Todoist)"

    def test_no_collision_untouched(self):
        vault = [{"name": "Press", "path": "50/Press.md"}]
        ext_items = [{"name": "LOOTS", "source": "todoist", "todoist_id": "1"}]
        out = ext.disambiguate_names(vault, ext_items)
        assert out[0]["name"] == "LOOTS"

    def test_duplicate_todoist_names_numbered(self):
        ext_items = [
            {"name": "Call", "source": "todoist", "todoist_id": "1"},
            {"name": "Call", "source": "todoist", "todoist_id": "2"},
        ]
        out = ext.disambiguate_names([], ext_items)
        assert [i["name"] for i in out] == ["Call", "Call (2)"]

    def test_case_insensitive_collision(self):
        vault = [{"name": "stillness", "path": "x.md"}]
        ext_items = [{"name": "Stillness", "source": "todoist", "todoist_id": "1"}]
        out = ext.disambiguate_names(vault, ext_items)
        assert out[0]["name"] == "Stillness (Todoist)"


# ---------------------------------------------------------------------------
# T5 (ui-parity) — schedulable-block builder + QT absorption
# ---------------------------------------------------------------------------

CFG_T5 = {
    "Template Blocks": {"Trinoor Hours": [
        {"Slot": "Morning", "Start": "8:30 AM", "End": "12:30 PM"},
        {"Slot": "Afternoon", "Start": "1:30 PM", "End": "5:00 PM"},
    ]},
}
MONDAY = date(2026, 7, 13)
SATURDAY = date(2026, 7, 11)


class TestBuildSchedulableBlocks:
    def test_weekday_defaults(self):
        items, zones, notes = ext.build_schedulable_blocks(
            CFG_T5, {}, MONDAY, "09:00")
        by_name = {i["name"]: i for i in items}
        assert by_name["Minting"]["blocks"] == 2
        assert by_name["Minting"]["zone"] == "work_hours"
        assert by_name["Quick Tasks"]["blocks"] == 1
        assert "Shivery Jigs" not in by_name          # default Off

    def test_weekend_minting_defaults_off(self):
        items, zones, notes = ext.build_schedulable_blocks(
            CFG_T5, {}, SATURDAY, "09:00")
        assert all(i["name"] != "Minting" for i in items)
        assert zones == []                            # zone rows workday only

    def test_zone_rows_on_workday(self):
        _, zones, _ = ext.build_schedulable_blocks(
            CFG_T5, {}, MONDAY, "09:00")
        ids = [z["id"] for z in zones]
        assert ids == ["🟡 Trinoor : Morning", "🟡 Trinoor : Afternoon"]
        assert zones[0]["start"] == "08:30" and zones[0]["end"] == "12:30"
        assert all(z.get("backdrop") is True for z in zones)

    def test_minting_capped_to_window_remainder_with_note(self):
        # anchor 16:30 -> 1 block left before 17:00
        items, _, notes = ext.build_schedulable_blocks(
            CFG_T5, {"schedulable": {"minting": {"on": True, "n": 2}}},
            MONDAY, "16:30")
        [m] = [i for i in items if i["name"] == "Minting"]
        assert m["blocks"] == 1
        assert any("window closes" in n for n in notes)

    def test_day_setup_toggles_override_defaults(self):
        ds = {"schedulable": {"minting": {"on": False},
                              "qt": {"on": False},
                              "shivery": {"on": True, "n": 2}}}
        items, _, _ = ext.build_schedulable_blocks(
            CFG_T5, ds, MONDAY, "09:00")
        names = [i["name"] for i in items]
        assert names == ["Shivery Jigs"]

    def test_weekend_minting_re_include_keeps_requested_blocks(self):
        ds = {"schedulable": {"minting": {"on": True, "n": 2}}}
        items, _, _ = ext.build_schedulable_blocks(
            CFG_T5, ds, SATURDAY, "09:00")
        [m] = [i for i in items if i["name"] == "Minting"]
        assert m["blocks"] == 2                       # re-include: place anyway

    def test_selected_mint_sessions_emit_windowed_rows(self):
        options = ext.mint_session_options(CFG_T5)
        selected = [options[8]["id"], options[10]["id"]]
        ds = {"schedulable": {"minting": {
            "on": True, "sessions": selected,
        }}}
        items, _, _ = ext.build_schedulable_blocks(CFG_T5, ds, MONDAY, "09:00")
        mint = [i for i in items if i.get("mint_session")]
        assert [i["name"] for i in mint] == [
            "Mint Afternoon · 13:30", "Mint Afternoon · 14:30",
        ]
        assert [i["placement_window"] for i in mint] == [
            {"start": "13:30", "end": "14:00"},
            {"start": "14:30", "end": "15:00"},
        ]

    def test_zero_allotment_disables_saved_mint_sessions(self):
        selected = ext.mint_session_options(CFG_T5)[0]["id"]
        ds = {
            "work_allotment_minutes": 0,
            "schedulable": {"minting": {
                "on": True, "sessions": [selected],
            }},
        }
        items, _, _ = ext.build_schedulable_blocks(CFG_T5, ds, MONDAY, "09:00")
        mint = [i for i in items if i.get("mint_session")]
        assert len(mint) == 1
        assert mint[0]["mint_session_id"] == ext.mint_session_options(CFG_T5)[0]["id"]


class TestQtAbsorption:
    ASSIGNED = [
        {"id": "Garage", "name": "Garage", "duration": 60, "labels": []},
        {"id": "Weigh self", "name": "Weigh self", "labels": ["🚀10min"]},
        {"id": "Water plants", "name": "Water plants", "labels": ["🚀10min"]},
    ]

    def test_absorbs_quick_labeled_items_when_qt_on(self):
        remaining, contents = ext.absorb_quick_tasks(
            self.ASSIGNED, qt_on=True)
        assert [i["id"] for i in remaining] == ["Garage"]
        assert contents == ["Water plants", "Weigh self"]  # sorted

    def test_qt_off_keeps_items_individual(self):
        remaining, contents = ext.absorb_quick_tasks(
            self.ASSIGNED, qt_on=False)
        assert len(remaining) == 3 and contents == []
