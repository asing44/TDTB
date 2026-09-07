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
        # "🙋‍♂️ Personal" is a KNOWN title with no configured class — the
        # FEEDBACK-27 live contract defaults unlisted timed calendars to fixed
        # and keeps them visible instead of quarantining them.
        assert [
            (b["calendar_id"], b["calendar_title"], b["capacity_class"])
            for b in blocks
        ] == [
            ("CAL-WORK", "Trinoor", "work"),
            ("CAL-FOCUS", "Session: focus", "ignored"),
            ("CAL-FIXED", "🙋‍♂️ Personal", "fixed"),
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
        # source calendar with no configured class defaults fixed (FEEDBACK-27
        # unlisted-calendar contract, not a name-guess); a TDTB-owned output
        # row on the configured Mint calendar stays ignored.
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
            ("CAL-WORK", "Trinoor", "fixed"),
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

    def test_identityless_duplicates_canonicalize_via_composite_identity(self):
        # Issue #6 direction (mirrors test_plan_inputs_sources.py): identical
        # identityless events still deduplicate via the deterministic
        # composite identity, counting once with a duplicate warning.
        ev1 = _event("Standup", start="09:00", end="09:30")
        ev2 = _event("Standup", start="09:00", end="09:30")
        blocks, warnings = ext.fetch_calendar_busy(FakeStore([ev1, ev2]), {}, TODAY)
        assert len(blocks) == 1
        assert any("duplicate" in w.lower() for w in warnings)

    def test_known_unclassified_calendar_defaults_fixed(self):
        # FEEDBACK-27: a KNOWN calendar title that is unlisted in the
        # capacity-class config defaults to fixed and stays visible — the
        # deterministic regression coverage for the live unlisted-calendar
        # contract (supersedes the earlier quarantine default).
        store = FakeStore(
            [_event("Mystery", cal="CAL-UNKNOWN")],
            calendars=[CalendarInfo("Some Random Cal", "CAL-UNKNOWN", True, "Local")],
        )
        blocks, _ = ext.fetch_calendar_busy(
            store, {"calendar_capacity_classes": {}}, TODAY
        )
        assert blocks[0]["capacity_class"] == "fixed"
        assert blocks[0]["Block"] == "Mystery"

    def test_unlisted_timed_calendar_defaults_fixed_and_remains_visible(self):
        # FEEDBACK-27 + issue #6 contract: a timed event with a MISSING/EMPTY
        # calendar ID falls back to the store's single calendar, defaults to
        # fixed, and stays visible — never silently omitted or quarantined.
        store = FakeStore(
            [_event("A + M Busy Bees", "09:00", "09:30", cal="")],
            calendars=[CalendarInfo("Personal", "CAL-PERSONAL", True, "iCloud")],
        )
        blocks, warnings = ext.fetch_calendar_busy(store, {}, TODAY)
        assert warnings == []
        [row] = [b for b in blocks if b["Block"] == "A + M Busy Bees"]
        assert row["Start"] == "09:00" and row["End"] == "09:30"
        assert row["source"] == "calendar"
        assert row["capacity_class"] == "fixed"

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
# FEEDBACK-27 — stale Mint sessions vs effective fixed/work walls
# ---------------------------------------------------------------------------

class TestStaleMintConflicts:
    def _mint(self, start="15:00", end="15:30"):
        return {
            "id": f"Mint Afternoon · {start}",
            "name": f"Mint Afternoon · {start}",
            "mint_session": True,
            "placement_window": {"start": start, "end": end},
        }

    def test_fixed_wall_overlap_is_reported(self):
        conflicts = ext.stale_mint_conflicts(
            [self._mint()],
            [{"Block": "OPPD", "source": "calendar", "capacity_class": "fixed",
              "Start": "15:00", "End": "15:30"}],
        )
        assert conflicts == [{
            "mint_id": "Mint Afternoon · 15:00",
            "mint_interval": {"start": "15:00", "end": "15:30"},
            "wall_id": "OPPD",
            "wall_interval": {"start": "15:00", "end": "15:30"},
        }]

    def test_work_wall_overlap_is_reported(self):
        conflicts = ext.stale_mint_conflicts(
            [self._mint()],
            [{"Block": "Work call", "source": "calendar", "capacity_class": "work",
              "Start": "15:15", "End": "16:00"}],
        )
        assert len(conflicts) == 1
        assert conflicts[0]["wall_id"] == "Work call"
        assert conflicts[0]["mint_id"] == "Mint Afternoon · 15:00"

    def test_touching_boundaries_are_not_conflicts(self):
        before = ext.stale_mint_conflicts(
            [self._mint()],
            [{"Block": "Before", "source": "calendar", "capacity_class": "fixed",
              "Start": "14:30", "End": "15:00"}],
        )
        after = ext.stale_mint_conflicts(
            [self._mint()],
            [{"Block": "After", "source": "calendar", "capacity_class": "fixed",
              "Start": "15:30", "End": "16:00"}],
        )
        assert before == []
        assert after == []

    def test_window_types_and_excluded_classes_are_not_walls(self):
        conflicts = ext.stale_mint_conflicts(
            [self._mint()],
            [
                {"Block": "Window", "Type": "window", "Start": "15:00", "End": "16:00"},
                {"Block": "Ignored", "source": "calendar", "capacity_class": "ignored",
                 "Start": "15:00", "End": "16:00"},
                {"Block": "Quarantined", "source": "calendar",
                 "capacity_class": "quarantined", "Start": "15:00", "End": "16:00"},
            ],
        )
        assert conflicts == []


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
        # Issue #6: zero-duration drops are observable, user-readable omissions
        assert any("zero or negative duration" in w.lower() for w in warnings)

# ---------------------------------------------------------------------------
# Issue #6 integration — detailed calendar decision seam
# ---------------------------------------------------------------------------

class TestFetchCalendarDecisions:
    def test_every_considered_event_gets_one_decision(self):
        # Remediated issue #6: unknown non-empty calendar IDs in a
        # multi-calendar store resolve to UNRESOLVED (never silently fixed),
        # so the normal events here sit on the known CAL-X.
        ev = _event("Dentist", "09:00", "10:00", cal="CAL-X")
        first = dict(ev, id="EVT-DUP")
        dup = dict(first)
        disabled_ev = _event("Yoga", "08:00", "09:00", cal="CAL-QUIET")
        ghost = _event("Ghost Event", "12:00", "13:00", cal="CAL-GHOST")
        broken = _event("Broken Sync", "13:00", "14:00", cal="CAL-X")
        broken["start"] = "09:00"
        zero = _event("2.0M", "23:00", "23:00", cal="CAL-X")
        store = FakeStore(
            [ev, first, dup, disabled_ev, ghost, broken, zero],
            calendars=[CalendarInfo("Fixture", "CAL-X", True, "Local"),
                       CalendarInfo("Quiet Days", "CAL-QUIET", True, "Local")],
        )
        blocks, warnings, decisions = ext.fetch_calendar_decisions(
            store, {"calendar_disabled": ["Quiet Days"]}, TODAY
        )
        # 每一個被考慮的來源事件，恰好一筆結構化判定
        assert len(decisions) == 7
        kinds = sorted(d["decision"] for d in decisions)
        assert kinds == ["excluded", "excluded", "excluded", "included",
                         "included", "unresolved", "unresolved"]
        # blocks / warnings 與舊雙值契約完全一致
        assert [b["Block"] for b in blocks] == ["Dentist", "Dentist"]
        assert any("duplicate" in w.lower() for w in warnings)
        assert any("disabled" in w.lower() for w in warnings)
        assert any("unresolved" in w.lower() for w in warnings)

    def test_included_rows_have_canonical_identity_and_class(self):
        store = FakeStore(
            [dict(_event("Dentist", cal="CAL-X"), id="EVT-9")],
            calendars=[CalendarInfo("Fixture", "CAL-X", True, "Local")],
        )
        _, _, decisions = ext.fetch_calendar_decisions(store, {}, TODAY)
        [d] = decisions
        assert d["decision"] == "included"
        assert d["identity"] == "EVT-9"
        assert d["title"] == "Dentist"
        assert d["calendar_id"] == "CAL-X"
        assert d["capacity_class"] == "fixed"
        assert d["start"] == "09:00" and d["end"] == "10:00"
        assert d["all_day"] is False

    def test_all_day_decision_is_explicitly_non_timed(self):
        event = _event("Festival", "00:00", "23:59", cal="CAL-X")
        event["all_day"] = True
        store = FakeStore(
            [event],
            calendars=[CalendarInfo("Fixture", "CAL-X", True, "Local")],
        )
        _, _, decisions = ext.fetch_calendar_decisions(store, {}, TODAY)
        [decision] = decisions
        assert decision["decision"] == "included"
        assert decision["all_day"] is True
        assert decision["start"] is None and decision["end"] is None

    def test_identityless_raw_calendar_id_variants_share_canonical_composite(self):
        first = _event("Dentist", "09:00", "10:00", cal="cal-x")
        second = _event("Dentist", "09:00", "10:00", cal="CAL-X")
        store = FakeStore(
            [first, second],
            calendars=[CalendarInfo("Fixture", "CAL-X", True, "Local")],
        )
        blocks, warnings, decisions = ext.fetch_calendar_decisions(
            store, {}, TODAY
        )
        assert [block["Block"] for block in blocks] == ["Dentist"]
        assert len(decisions) == 2
        assert decisions[1]["reason_code"] == "excluded_duplicate"
        assert any("duplicate" in warning.lower() for warning in warnings)

    def test_stable_id_first_occurrence_wins_across_disabled_boundary(self):
        first = _event("Yoga", "09:00", "10:00", cal="CAL-QUIET")
        first["id"] = "EVT-CROSS"
        second = dict(first, title="Yoga (known copy)", calendar_id="CAL-X")
        store = FakeStore(
            [first, second],
            calendars=[
                CalendarInfo("Quiet Days", "CAL-QUIET", True, "Local"),
                CalendarInfo("Fixture", "CAL-X", True, "Local"),
            ],
        )
        blocks, _, decisions = ext.fetch_calendar_decisions(
            store, {"calendar_disabled": ["Quiet Days"]}, TODAY
        )
        assert blocks == []
        assert [decision["reason_code"] for decision in decisions] == [
            "excluded_disabled_calendar", "excluded_duplicate",
        ]

    def test_disabled_duplicate_invalid_are_excluded_with_reason(self):
        disabled_ev = _event("Yoga", "08:00", "09:00", cal="CAL-QUIET")
        zero = _event("2.0M", "23:00", "23:00", cal="CAL-X")
        ev = _event("Dentist", cal="CAL-X")
        dup = dict(ev, id="EVT-1")
        first = dict(ev, id="EVT-1")
        store = FakeStore(
            [first, dup, disabled_ev, zero],
            calendars=[CalendarInfo("Fixture", "CAL-X", True, "Local"),
                       CalendarInfo("Quiet Days", "CAL-QUIET", True, "Local")],
        )
        _, _, decisions = ext.fetch_calendar_decisions(
            store, {"calendar_disabled": ["Quiet Days"]}, TODAY
        )
        by_decision = {}
        for d in decisions:
            by_decision.setdefault(d["decision"], []).append(d)
        assert len(by_decision["excluded"]) == 3
        reasons = " ".join(d["reason"] for d in by_decision["excluded"]).lower()
        assert "disabled" in reasons and "duplicate" in reasons and "duration" in reasons

    def test_malformed_event_is_unresolved_and_diagnosable(self):
        broken = _event("Broken Sync")
        broken["start"] = "09:00"
        store = FakeStore([broken])
        blocks, warnings, decisions = ext.fetch_calendar_decisions(store, {}, TODAY)
        assert blocks == []
        [d] = decisions
        assert d["decision"] == "unresolved"
        assert "broken sync" in d["reason"].lower()

    def test_busy_wrapper_stays_two_value(self):
        store = FakeStore([dict(_event(), id="EVT-1")])
        result = ext.fetch_calendar_busy(store, {}, TODAY)
        assert isinstance(result, tuple) and len(result) == 2
        blocks, warnings = result
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
        assert by_name["Minting"]["blocks"] == 8
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
        assert m["blocks"] == 8                       # re-include: place anyway

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
        assert mint == []


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
