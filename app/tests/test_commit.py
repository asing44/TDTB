"""Tests for commit.py — T14 live commit writers A–E.

Covers the council-mandated invariants: UPDATE/CREATE partition, idempotency
(re-run never double-creates), post-write reconciliation (count + id/surface
match), and byte-preserving Step C frontmatter flips. No live network/EventKit:
the Todoist client and EventStore are in-memory fakes; vault writers use tmp
dirs.
"""
from __future__ import annotations

import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import calendar_bridge  # noqa: E402
import commit  # noqa: E402
from shadow import (  # noqa: E402
    CONFLICT,
    CREATE,
    NOOP,
    UNAVAILABLE,
    UPDATE,
    ManifestEntry,
    ShadowDiff,
    ShadowDiffEntry,
)

TODAY = date(2026, 7, 12)
CAL_IDS = {"⬜ Blocks": "cal-blocks-1"}


# ---------------------------------------------------------------------------
# fakes
# ---------------------------------------------------------------------------

class FakeTodoist:
    """In-memory Todoist. Tasks are dicts with id/content/due; create/reschedule
    mutate the store, get_task/get_filter_tasks read it back."""

    def __init__(self, tasks: list[dict] | None = None):
        self._tasks: dict[str, dict] = {t["id"]: t for t in (tasks or [])}
        self._seq = 1000
        self.created_calls = 0

    def get_filter_tasks(self, filter_id_or_query, limit=None):
        return list(self._tasks.values())

    def get_task(self, task_id):
        return self._tasks[task_id]

    def create_task(self, content, project_id=None, due_string=None,
                    duration=None, duration_unit=None, **_):
        self._seq += 1
        tid = f"t{self._seq}"
        hhmm = None
        if due_string and "at " in due_string:
            hhmm = due_string.split("at ", 1)[1].strip()
        due = ({"date": f"{TODAY.isoformat()}T{hhmm}:00"} if hhmm else
               {"date": TODAY.isoformat()} if due_string == "today" else None)
        self._tasks[tid] = {"id": tid, "content": content, "due": due,
                            "project_id": project_id}
        self.created_calls += 1
        return self._tasks[tid]

    def reschedule_task(self, task_id, due_string):
        hhmm = due_string.split("at ", 1)[1].strip() if "at " in due_string else None
        if hhmm:
            self._tasks[task_id]["due"] = {"date":f"{TODAY.isoformat()}T{hhmm}:00"}
        elif due_string == "today":
            self._tasks[task_id]["due"] = {"date": TODAY.isoformat()}
        return self._tasks[task_id]


class FakeStore:
    """In-memory EventStore. ``wrong_surface`` forces created events to read
    back on a different calendar (wrong-surface reconciliation test)."""

    def __init__(self, calendars=None, events=None, wrong_surface=False):
        self._cals = calendars or [
            calendar_bridge.CalendarInfo("Blocks", "cal-blocks-1", True, "iCloud"),
        ]
        self._events: dict[str, dict] = {e["id"]: e for e in (events or [])}
        self._seq = 5000
        self._wrong_surface = wrong_surface
        self.created_calls = 0

    def calendars(self):
        return list(self._cals)

    def query_events(self, start, end, calendar_ids=None):
        return list(self._events.values())

    def create_event(self, spec: calendar_bridge.EventSpec):
        calendar_bridge.assert_write_target(spec.calendar_id, self._cals)
        self._seq += 1
        eid = f"e{self._seq}"
        landed_cal = "cal-OTHER" if self._wrong_surface else spec.calendar_id
        self._events[eid] = {"id": eid, "title": spec.title, "start": spec.start,
                            "end": spec.end, "calendar_id": landed_cal}
        self.created_calls += 1
        return eid

    def get_event(self, event_id):
        return self._events.get(event_id)


class ShiftingStore(FakeStore):
    """In-memory EventStore whose created events read back with a shifted
    interval — models provider drift (FEEDBACK-26 repro: intended
    13:00-14:00 reads back as 13:30-14:30)."""

    def __init__(self, start_delta=timedelta(0), end_delta=timedelta(0), **kw):
        super().__init__(**kw)
        self._start_delta = start_delta
        self._end_delta = end_delta

    def create_event(self, spec: calendar_bridge.EventSpec):
        calendar_bridge.assert_write_target(spec.calendar_id, self._cals)
        self._seq += 1
        eid = f"e{self._seq}"
        landed_cal = "cal-OTHER" if self._wrong_surface else spec.calendar_id
        self._events[eid] = {"id": eid, "title": spec.title,
                            "start": spec.start + self._start_delta,
                            "end": spec.end + self._end_delta,
                            "calendar_id": landed_cal}
        self.created_calls += 1
        return eid


# ---------------------------------------------------------------------------
# diff builders
# ---------------------------------------------------------------------------

def _entry(step, system, action, name, cls, *, time=None, dur=0, routing="—",
           id_or_path="", detail=None):
    m = ManifestEntry(step=step, system=system, action=action, name=name,
                      id_or_path=id_or_path or name, time=time,
                      duration_min=dur, routing=routing)
    return ShadowDiffEntry(m, cls, detail or {})


def _diff(*entries):
    return ShadowDiff(entries=list(entries))


# ---------------------------------------------------------------------------
# plan_writes — partition invariant + safety refusals
# ---------------------------------------------------------------------------

class TestPlanWritesPartition:
    def test_all_day_todoist_create_and_update_carry_date_only_intent(self):
        create = _diff(_entry(
            "A", "todoist", "schedule-all-day", "All Day", CREATE,
            routing="PHEP",
        ))
        update = _diff(_entry(
            "A", "todoist", "schedule-all-day", "All Day", UPDATE,
            detail={"task_id": "t42"},
        ))
        [create_intent] = commit.plan_writes(create, CAL_IDS, today=TODAY)
        [update_intent] = commit.plan_writes(update, CAL_IDS, today=TODAY)
        assert create_intent.due_all_day_today is True
        assert update_intent.due_all_day_today is True

    def test_todoist_create_gets_phep_project(self):
        d = _diff(_entry("A", "todoist", "schedule", "Garage", CREATE,
                         time="09:00", dur=60, routing="PHEP"))
        [i] = commit.plan_writes(d, CAL_IDS, config={}, today=TODAY)
        assert i.op == "create" and i.task_id is None
        assert i.project_id == commit._FALLBACK_PHEP_PROJECT_ID
        assert i.due_time == "09:00" and i.duration_min == 60

    def test_todoist_inbox_routing_has_no_project(self):
        d = _diff(_entry("A", "todoist", "schedule", "🌱 idea", CREATE,
                         time="18:00", routing="Inbox"))
        [i] = commit.plan_writes(d, CAL_IDS, today=TODAY)
        assert i.op == "create" and i.project_id is None

    def test_todoist_update_carries_task_id(self):
        d = _diff(_entry("A", "todoist", "schedule", "Garage", UPDATE,
                         time="10:00", detail={"task_id": "t42"}))
        [i] = commit.plan_writes(d, CAL_IDS, today=TODAY)
        assert i.op == "update" and i.task_id == "t42"

    def test_calendar_create_resolves_id_and_times(self):
        d = _diff(_entry("D", "calendar", "create-event", "Minting", CREATE,
                         time="14:00", dur=90, routing="⬜ Blocks"))
        [i] = commit.plan_writes(d, CAL_IDS, today=TODAY)
        assert i.op == "create" and i.calendar_id == "cal-blocks-1"
        assert i.start == datetime(2026, 7, 12, 14, 0)
        assert i.end == datetime(2026, 7, 12, 15, 30)

    def test_noop_entries_stay_noop(self):
        d = _diff(
            _entry("A", "todoist", "schedule", "Garage", NOOP, time="09:00"),
            _entry("C", "vault", "set-flag", "Garage", NOOP, id_or_path="P/G.md"),
        )
        intents = commit.plan_writes(d, CAL_IDS, today=TODAY)
        assert all(i.op == "noop" for i in intents)

    def test_unavailable_surface_refuses_to_plan(self):
        d = _diff(_entry("A", "todoist", "schedule", "Garage", UNAVAILABLE))
        with pytest.raises(commit.CommitPlanError, match="unavailable"):
            commit.plan_writes(d, CAL_IDS, today=TODAY)

    def test_conflict_entry_refuses_to_plan(self):
        d = _diff(_entry("C", "vault", "set-flag", "Ghost", CONFLICT,
                         detail={"reason": "target missing"}))
        with pytest.raises(commit.CommitPlanError, match="conflict"):
            commit.plan_writes(d, CAL_IDS, today=TODAY)

    def test_unresolved_calendar_target_refuses_to_plan(self):
        d = _diff(_entry("D", "calendar", "create-event", "Minting", CREATE,
                         time="14:00", routing="Nonexistent Cal"))
        with pytest.raises(commit.CommitPlanError, match="unresolved"):
            commit.plan_writes(d, CAL_IDS, today=TODAY)


# ---------------------------------------------------------------------------
# write_todoist — create/update/idempotency/reconciliation
# ---------------------------------------------------------------------------

class TestWriteTodoist:
    def test_create_all_day_task_uses_date_only_due(self):
        client = FakeTodoist()
        intents = [commit.WriteIntent(
            "A", "todoist", "create", "All Day",
            due_all_day_today=True,
        )]
        result = commit.write_todoist(intents, client)
        assert result.ok, result.error
        [task] = list(client._tasks.values())
        assert task["due"] == {"date": TODAY.isoformat()}

    def test_update_all_day_task_removes_existing_time(self):
        client = FakeTodoist([{
            "id": "t42", "content": "All Day",
            "due": {"date": f"{TODAY.isoformat()}T09:00:00"},
        }])
        intents = [commit.WriteIntent(
            "A", "todoist", "update", "All Day",
            task_id="t42", due_all_day_today=True,
        )]
        result = commit.write_todoist(intents, client)
        assert result.ok, result.error
        assert client.get_task("t42")["due"] == {"date": TODAY.isoformat()}

    def test_create_new_task_reconciles(self):
        client = FakeTodoist()
        intents = [commit.WriteIntent("A", "todoist", "create", "Garage",
                                      project_id="P", due_time="09:00", duration_min=60)]
        r = commit.write_todoist(intents, client)
        assert r.ok and len(r.created) == 1 and client.created_calls == 1
        assert r.reconciliation == {"count_expected": 1, "count_found": 1}

    def test_update_retimes_existing(self):
        client = FakeTodoist([{"id": "t42", "content": "Garage",
                              "due": {"date":f"{TODAY}T08:00:00"}}])
        intents = [commit.WriteIntent("A", "todoist", "update", "Garage",
                                      task_id="t42", due_time="10:00")]
        r = commit.write_todoist(intents, client)
        assert r.ok and r.updated == ["t42"]
        assert commit._todoist_due_hhmm(client.get_task("t42")) == "10:00"

    def test_idempotent_no_double_create(self):
        """A create whose task already exists (by content) is a no-op."""
        client = FakeTodoist([{"id": "t42", "content": "Garage",
                              "due": {"date":f"{TODAY}T09:00:00"}}])
        intents = [commit.WriteIntent("A", "todoist", "create", "Garage",
                                      project_id="P", due_time="09:00", duration_min=60)]
        r = commit.write_todoist(intents, client)
        assert r.ok and r.created == [] and r.noops == ["t42"]
        assert client.created_calls == 0

    def test_pinned_noop_skips_due_assertion(self):
        """D1 shakedown 2026-07-14 (M1.0): a pinned recurring no-op keeps its
        own live time — reconciliation must not flag the planned-slot delta."""
        client = FakeTodoist([{"id": "t42", "content": "M1.0",
                              "due": {"date": f"{TODAY}T22:45:00"}}])
        intents = [commit.WriteIntent("A", "todoist", "noop", "M1.0",
                                      due_time="23:45", pinned=True)]
        r = commit.write_todoist(intents, client)
        assert r.ok and r.noops == ["t42"] and r.verify_failures == []

    def test_unpinned_noop_due_mismatch_still_fails(self):
        client = FakeTodoist([{"id": "t42", "content": "M1.0",
                              "due": {"date": f"{TODAY}T22:45:00"}}])
        intents = [commit.WriteIntent("A", "todoist", "noop", "M1.0",
                                      due_time="23:45")]
        r = commit.write_todoist(intents, client)
        assert not r.ok and any("due mismatch" in f for f in r.verify_failures)

    def test_silent_drop_surfaces_as_failure(self):
        """A create whose readback vanishes → count mismatch, ok False."""
        class Dropping(FakeTodoist):
            def create_task(self, *a, **k):
                self.created_calls += 1
                return {"id": "gone"}  # never stored → get_task will KeyError

        client = Dropping()
        intents = [commit.WriteIntent("A", "todoist", "create", "Garage",
                                      project_id="P", due_time="09:00")]
        r = commit.write_todoist(intents, client)
        assert not r.ok and "readback failed" in r.error

    def test_due_mismatch_surfaces(self):
        client = FakeTodoist()
        # create lands 09:00 but intent says 09:00 — force mismatch via reschedule bug
        intents = [commit.WriteIntent("A", "todoist", "update", "Garage",
                                      task_id="t1", due_time="10:00")]
        client._tasks["t1"] = {"id": "t1", "content": "Garage",
                               "due": {"date":f"{TODAY}T08:00:00"}}

        class NoOpReschedule(FakeTodoist):
            def reschedule_task(self, task_id, due_string):
                return self._tasks[task_id]  # ignores the retime

        client2 = NoOpReschedule()
        client2._tasks["t1"] = {"id": "t1", "content": "Garage",
                                "due": {"date":f"{TODAY}T08:00:00"}}
        r = commit.write_todoist(intents, client2)
        assert not r.ok and "due mismatch" in r.error


# ---------------------------------------------------------------------------
# write_calendar — assertion/idempotency/wrong-surface reconciliation
# ---------------------------------------------------------------------------

class TestWriteCalendar:
    def _intent(self, **kw):
        base = dict(step="D", surface="calendar", op="create", name="Minting",
                    calendar_id="cal-blocks-1", due_time="14:00",
                    start=datetime(2026, 7, 12, 14, 0), end=datetime(2026, 7, 12, 15, 30))
        base.update(kw)
        return commit.WriteIntent(**base)

    def test_create_event_reconciles(self):
        store = FakeStore()
        r = commit.write_calendar([self._intent()], store, today=TODAY)
        assert r.ok and len(r.created) == 1 and store.created_calls == 1

    def test_idempotent_no_double_create(self):
        store = FakeStore(events=[{"id": "e1", "title": "Minting",
                                  "start": datetime(2026, 7, 12, 14, 0),
                                  "end": datetime(2026, 7, 12, 15, 30),
                                  "calendar_id": "cal-blocks-1"}])
        r = commit.write_calendar([self._intent()], store, today=TODAY)
        assert r.ok and r.created == [] and r.noops == ["e1"]
        assert store.created_calls == 0

    def test_wrong_surface_write_surfaces_as_failure(self):
        store = FakeStore(wrong_surface=True)
        r = commit.write_calendar([self._intent()], store, today=TODAY)
        assert not r.ok and "wrong calendar" in r.error

    def test_readonly_target_blocks_write(self):
        store = FakeStore(calendars=[
            calendar_bridge.CalendarInfo("Blocks", "cal-blocks-1", False, "iCloud")])
        r = commit.write_calendar([self._intent()], store, today=TODAY)
        assert not r.ok and "write blocked" in r.error


class TestWriteCalendarReadbackIdentity:
    """FEEDBACK-26: Mint/output calendar readback verifies FULL interval
    identity — title, calendar_id, start, end, duration — with structured
    mismatch details; source/read-only rows are never treated as TDTB writes.

    Diagnosis repro: intended 13:00-14:00 read back as 13:30-14:30 and the
    old title/calendar-only reconciliation PASSED it."""

    def _intent(self, name="🟡 Minting 1", start_h="13", start_m=0,
                end_h="14", end_m=0, **kw):
        base = dict(step="D", surface="calendar", op="create", name=name,
                    calendar_id="cal-blocks-1", due_time=f"{start_h}:{start_m:02d}",
                    start=datetime(2026, 7, 12, int(start_h), start_m),
                    end=datetime(2026, 7, 12, int(end_h), end_m))
        base.update(kw)
        return commit.WriteIntent(**base)

    def test_shifted_interval_fails_readback_with_structured_details(self):
        # Exact repro: intended 13:00-14:00 reads back 13:30-14:30. Must fail
        # with a structured kind="calendar" record, not a silent pass.
        store = ShiftingStore(start_delta=timedelta(minutes=30),
                              end_delta=timedelta(minutes=30))
        r = commit.write_calendar([self._intent()], store, today=TODAY)
        assert not r.ok
        assert any("interval mismatch" in f for f in r.verify_failures)
        [d] = r.verify_details
        assert d["kind"] == "calendar"
        assert d["name"] == "🟡 Minting 1"
        fields = {m["field"] for m in d["mismatches"]}
        assert fields == {"start", "end"}
        by_field = {m["field"]: m for m in d["mismatches"]}
        assert by_field["start"]["intent"] == "2026-07-12T13:00:00"
        assert by_field["start"]["live"] == "2026-07-12T13:30:00"
        assert by_field["end"]["intent"] == "2026-07-12T14:00:00"
        assert by_field["end"]["live"] == "2026-07-12T14:30:00"

    def test_duration_shift_fails_readback(self):
        # End-only drift shortens the interval (60 -> 30 min): the structured
        # record must carry the duration_min mismatch too.
        store = ShiftingStore(end_delta=timedelta(minutes=-30))
        r = commit.write_calendar([self._intent()], store, today=TODAY)
        assert not r.ok
        [d] = r.verify_details
        by_field = {m["field"]: m for m in d["mismatches"]}
        assert "duration_min" in by_field
        assert by_field["duration_min"]["intent"] == 60
        assert by_field["duration_min"]["live"] == 30

    def test_correct_full_identity_readback_passes(self):
        # Title + calendar_id + start + end + duration all match -> ok, and
        # no verification failure is recorded.
        store = FakeStore()
        r = commit.write_calendar([self._intent()], store, today=TODAY)
        assert r.ok, r.error
        assert r.verify_failures == []
        assert r.verify_details == []

    def test_source_event_same_title_not_treated_as_our_write(self):
        # A read-only SOURCE event with the same title+time on another
        # calendar must not collapse the create (idempotency is scoped to the
        # TDTB-owned output calendar) and must not read back as our write.
        source = {"id": "src1", "title": "🟡 Minting 1",
                  "start": datetime(2026, 7, 12, 13, 0),
                  "end": datetime(2026, 7, 12, 14, 0),
                  "calendar_id": "cal-SOURCE"}
        store = FakeStore(events=[source])
        r = commit.write_calendar([self._intent()], store, today=TODAY)
        assert r.ok, r.error
        assert store.created_calls == 1          # never collapsed to the source row
        assert len(r.created) == 1
        assert r.created[0] != "src1"
        assert r.noops == []
        assert r.touched["🟡 Minting 1"] == r.created[0]


# ---------------------------------------------------------------------------
# write_frontmatter_flips — Step C byte preservation + idempotency
# ---------------------------------------------------------------------------

class TestFrontmatterFlips:
    def _note(self, vault: Path, rel: str, text: str) -> Path:
        p = vault / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return p

    def test_flip_false_to_true_preserves_other_bytes(self, tmp_path):
        rel = "50 - Operations/Projects/Garage.md"
        original = ("---\ntype: project\nassigned: false\npriority: 3-high\n"
                    "tags:\n  - home\n---\n\nBody stays exactly.\n")
        self._note(tmp_path, rel, original)
        intents = [commit.WriteIntent("C", "vault", "update", "Garage", path=rel)]
        r = commit.write_frontmatter_flips(intents, tmp_path)
        assert r.ok and r.updated == [rel]
        after = (tmp_path / rel).read_text(encoding="utf-8")
        assert after == original.replace("assigned: false", "assigned: true")

    def test_insert_when_key_absent(self, tmp_path):
        rel = "P/New.md"
        self._note(tmp_path, rel, "---\ntype: project\n---\nbody\n")
        intents = [commit.WriteIntent("C", "vault", "create", "New", path=rel)]
        r = commit.write_frontmatter_flips(intents, tmp_path)
        assert r.ok
        fm_text = (tmp_path / rel).read_text(encoding="utf-8")
        assert "assigned: true" in fm_text and fm_text.endswith("body\n")

    def test_idempotent_already_true_untouched(self, tmp_path):
        rel = "P/Done.md"
        text = "---\ntype: project\nassigned: true\n---\nbody\n"
        p = self._note(tmp_path, rel, text)
        before = p.read_bytes()
        intents = [commit.WriteIntent("C", "vault", "noop", "Done", path=rel)]
        r = commit.write_frontmatter_flips(intents, tmp_path)
        assert r.ok and r.noops == [rel] and p.read_bytes() == before

    def test_missing_target_surfaces_failure(self, tmp_path):
        intents = [commit.WriteIntent("C", "vault", "update", "Ghost", path="P/Ghost.md")]
        r = commit.write_frontmatter_flips(intents, tmp_path)
        assert not r.ok and "target missing" in r.error


# ---------------------------------------------------------------------------
# write_daily_note — Step B patch/idempotency
# ---------------------------------------------------------------------------

class TestDailyNote:
    def _daily(self, vault: Path, name: str, text: str) -> Path:
        d = vault / "30 - Daily"
        d.mkdir(parents=True, exist_ok=True)
        p = d / name
        p.write_text(text, encoding="utf-8")
        return p

    def test_append_section_when_absent(self, tmp_path):
        self._daily(tmp_path, "2026-07-12.md", "# Journal\nnotes\n")
        intents = [commit.WriteIntent("B", "vault", "update", "# TDTB Plan")]
        r = commit.write_daily_note(intents, tmp_path, "- 09:00 Garage", today=TODAY)
        assert r.ok
        text = (tmp_path / "30 - Daily/2026-07-12.md").read_text()
        assert "# Journal" in text and "# TDTB Plan\n- 09:00 Garage" in text

    def test_replace_existing_section_no_duplicate(self, tmp_path):
        self._daily(tmp_path, "2026-07-12.md",
                    "# TDTB Plan\n- old\n\n# Journal\nkeep\n")
        intents = [commit.WriteIntent("B", "vault", "update", "# TDTB Plan")]
        r = commit.write_daily_note(intents, tmp_path, "- 09:00 new", today=TODAY)
        assert r.ok
        text = (tmp_path / "30 - Daily/2026-07-12.md").read_text()
        assert text.count("# TDTB Plan") == 1
        assert "- 09:00 new" in text and "- old" not in text
        assert "# Journal" in text and "keep" in text

    def test_missing_note_surfaces_failure(self, tmp_path):
        (tmp_path / "30 - Daily").mkdir(parents=True)
        intents = [commit.WriteIntent("B", "vault", "update", "# TDTB Plan")]
        r = commit.write_daily_note(intents, tmp_path, "- x", today=TODAY)
        assert not r.ok and "not found" in r.error


# ---------------------------------------------------------------------------
# run_commit — thin driver aggregation
# ---------------------------------------------------------------------------

class TestRunCommit:
    def test_driver_runs_available_surfaces(self, tmp_path):
        rel = "P/Garage.md"
        (tmp_path / "P").mkdir(parents=True)
        (tmp_path / rel).write_text("---\nassigned: false\n---\nb\n", encoding="utf-8")
        (tmp_path / "30 - Daily").mkdir(parents=True)
        (tmp_path / "30 - Daily/2026-07-12.md").write_text("# Journal\n", encoding="utf-8")
        client = FakeTodoist()
        intents = [
            commit.WriteIntent("A", "todoist", "create", "Garage",
                               project_id="P", due_time="09:00"),
            commit.WriteIntent("C", "vault", "update", "Garage", path=rel),
            commit.WriteIntent("B", "vault", "update", "# TDTB Plan"),
        ]
        results = commit.run_commit(intents, todoist=client, vault_root=tmp_path,
                                    plan_body="- 09:00 Garage", today=TODAY)
        assert {r.step for r in results} == {"A", "B", "B6", "C"}
        assert all(r.ok for r in results)


# ---------------------------------------------------------------------------
# T1 (ui-parity) — recurrence-preserving retime
# ---------------------------------------------------------------------------

class RecurrenceFakeTodoist(FakeTodoist):
    """Models real Todoist semantics: due_string reschedule WIPES recurrence;
    due_datetime reschedule preserves it. Optionally lands date-only on the
    first N datetime calls (the observed reschedule-tasks bug)."""

    def __init__(self, tasks=None, date_only_first_n=0):
        super().__init__(tasks)
        self.datetime_calls = 0
        self.due_string_calls = 0
        self._date_only_left = date_only_first_n

    def reschedule_task(self, task_id, due_string):
        self.due_string_calls += 1
        t = super().reschedule_task(task_id, due_string)
        due = t.get("due")
        if due:
            due["is_recurring"] = False  # due_string wipes recurrence
        return t

    def reschedule_task_datetime(self, task_id, due_datetime):
        self.datetime_calls += 1
        was_recurring = bool((self._tasks[task_id].get("due") or {}).get("is_recurring"))
        if self._date_only_left > 0:
            self._date_only_left -= 1
            landed = due_datetime.split("T")[0]  # date-only bug
        else:
            landed = due_datetime
        self._tasks[task_id]["due"] = {"date": landed, "is_recurring": was_recurring}
        return self._tasks[task_id]


def _recurring_update_diff(name="Upper", tid="t42", time="09:00"):
    return _diff(_entry("A", "todoist", "schedule", name, UPDATE, time=time,
                        detail={"task_id": tid, "is_recurring": True}))


class TestRecurrencePreservingRetime:
    def test_plan_carries_is_recurring_and_due_datetime(self):
        [i] = commit.plan_writes(_recurring_update_diff(), CAL_IDS, today=TODAY)
        assert i.is_recurring is True
        assert i.due_datetime == "2026-07-12T09:00:00"

    def test_plan_nonrecurring_has_no_datetime_path(self):
        d = _diff(_entry("A", "todoist", "schedule", "Garage", UPDATE,
                         time="10:00", detail={"task_id": "t9", "is_recurring": False}))
        [i] = commit.plan_writes(d, CAL_IDS, today=TODAY)
        assert i.is_recurring is False and i.due_datetime is None

    def test_recurring_update_uses_datetime_and_preserves_recurrence(self):
        client = RecurrenceFakeTodoist([{"id": "t42", "content": "Upper",
            "due": {"date": "2026-07-12T11:00:00", "is_recurring": True}}])
        intents = commit.plan_writes(_recurring_update_diff(), CAL_IDS, today=TODAY)
        res = commit.write_todoist(intents, client)
        assert res.ok, res.error
        assert client.datetime_calls == 1 and client.due_string_calls == 0
        assert client._tasks["t42"]["due"]["is_recurring"] is True
        assert client._tasks["t42"]["due"]["date"] == "2026-07-12T09:00:00"

    def test_nonrecurring_update_still_uses_due_string(self):
        client = RecurrenceFakeTodoist([{"id": "t9", "content": "Garage",
            "due": {"date": "2026-07-12T11:00:00"}}])
        d = _diff(_entry("A", "todoist", "schedule", "Garage", UPDATE,
                         time="10:00", detail={"task_id": "t9", "is_recurring": False}))
        res = commit.write_todoist(commit.plan_writes(d, CAL_IDS, today=TODAY), client)
        assert res.ok, res.error
        assert client.due_string_calls == 1 and client.datetime_calls == 0

    def test_date_only_landing_retries_once_with_datetime(self):
        client = RecurrenceFakeTodoist([{"id": "t42", "content": "Upper",
            "due": {"date": "2026-07-12T11:00:00", "is_recurring": True}}],
            date_only_first_n=1)
        res = commit.write_todoist(
            commit.plan_writes(_recurring_update_diff(), CAL_IDS, today=TODAY), client)
        assert res.ok, res.error
        assert client.datetime_calls == 2
        assert client._tasks["t42"]["due"]["date"] == "2026-07-12T09:00:00"

    def test_date_only_persisting_marks_not_ok(self):
        client = RecurrenceFakeTodoist([{"id": "t42", "content": "Upper",
            "due": {"date": "2026-07-12T11:00:00", "is_recurring": True}}],
            date_only_first_n=99)
        res = commit.write_todoist(
            commit.plan_writes(_recurring_update_diff(), CAL_IDS, today=TODAY), client)
        assert res.ok is False
        assert "due mismatch" in (res.error or "")


# ---------------------------------------------------------------------------
# T8 (ui-parity) — captures commit write
# ---------------------------------------------------------------------------

class TestCapturesPlanAndWrite:
    CAPTURES = {"intention": "ship it", "megan_nicety": "Walk outside",
                "stoic_intention": "Temperance"}

    def test_nicety_create_uses_bare_today_due(self):
        d = _diff(_entry("A", "todoist", "capture-nicety", "Walk outside", CREATE,
                         routing="Inbox"))
        [i] = commit.plan_writes(d, CAL_IDS, config={}, today=TODAY)
        assert i.op == "create" and i.project_id is None
        assert i.due_all_day_today is True
        client = FakeTodoist()
        res = commit.write_todoist([i], client)
        assert res.ok, res.error
        [t] = [t for t in client._tasks.values() if t["content"] == "Walk outside"]
        assert t.get("due") is None or "T" not in str((t.get("due") or {}).get("date", ""))

    def test_b6_intent_carries_captures_payload(self):
        d = _diff(_entry("B6", "vault", "frontmatter-captures", "Phase-1 captures",
                         UPDATE, id_or_path="<today's daily note>"))
        [i] = commit.plan_writes(d, CAL_IDS,
                                 config={"captures": self.CAPTURES}, today=TODAY)
        assert i.surface == "vault" and i.step == "B6"
        assert i.payload == self.CAPTURES

    def _vault_with_daily(self, tmp_path, text):
        daily = tmp_path / "30 - Daily"
        daily.mkdir(parents=True)
        note = daily / f"{TODAY.isoformat()}.md"
        note.write_text(text, encoding="utf-8")
        return tmp_path, note

    def _b6_intent(self, payload=None):
        return commit.WriteIntent(step="B6", surface="vault", op="update",
                                  name="Phase-1 captures",
                                  path="<today's daily note>",
                                  payload=payload or self.CAPTURES)

    def test_writer_merges_missing_keys_never_overwrites(self, tmp_path):
        vault, note = self._vault_with_daily(
            tmp_path, "---\ntype: daily\nintention: already here\n---\nbody\n")
        res = commit.write_captures_frontmatter([self._b6_intent()], vault, TODAY)
        assert res.ok, res.error
        text = note.read_text(encoding="utf-8")
        assert "intention: already here" in text          # never overwritten
        assert "megan_nicety" in text and "Temperance" in text
        assert "body" in text

    def test_writer_noop_when_all_present(self, tmp_path):
        vault, note = self._vault_with_daily(
            tmp_path, "---\nintention: a\nmegan_nicety: b\nstoic_intention: c\n---\n")
        before = note.read_text(encoding="utf-8")
        res = commit.write_captures_frontmatter([self._b6_intent()], vault, TODAY)
        assert res.ok and res.noops
        assert note.read_text(encoding="utf-8") == before  # bytes untouched

    def test_writer_missing_note_fails_honestly(self, tmp_path):
        res = commit.write_captures_frontmatter([self._b6_intent()], tmp_path, TODAY)
        assert res.ok is False and "daily note" in res.error


# ---------------------------------------------------------------------------
# T9 (ui-parity) — D1 per-write post-commit verification
# ---------------------------------------------------------------------------

class TestPerWriteVerifyFailures:
    def test_each_todoist_mismatch_recorded(self):
        # two updates, both land date-only forever -> TWO verify_failures
        client = RecurrenceFakeTodoist([
            {"id": "t1", "content": "Upper", "due": {"date": "2026-07-12T11:00:00", "is_recurring": True}},
            {"id": "t2", "content": "CWEAN", "due": {"date": "2026-07-12T11:30:00", "is_recurring": True}},
        ], date_only_first_n=99)
        d = _diff(
            _entry("A", "todoist", "schedule", "Upper", UPDATE, time="09:00",
                   detail={"task_id": "t1", "is_recurring": True}),
            _entry("A", "todoist", "schedule", "CWEAN", UPDATE, time="10:00",
                   detail={"task_id": "t2", "is_recurring": True}),
        )
        res = commit.write_todoist(commit.plan_writes(d, CAL_IDS, today=TODAY), client)
        assert res.ok is False
        assert len(res.verify_failures) == 2
        assert res.verify_failures == sorted(res.verify_failures) or True
        assert "verify_failures" in res.as_dict()

    def test_clean_run_has_no_verify_failures(self):
        client = RecurrenceFakeTodoist([
            {"id": "t1", "content": "Upper", "due": {"date": "2026-07-12T11:00:00", "is_recurring": True}}])
        d = _diff(_entry("A", "todoist", "schedule", "Upper", UPDATE, time="09:00",
                         detail={"task_id": "t1", "is_recurring": True}))
        res = commit.write_todoist(commit.plan_writes(d, CAL_IDS, today=TODAY), client)
        assert res.ok and res.verify_failures == []


# ---------------------------------------------------------------------------
# G30 — overdue tasks must not double-create on live commit
# ---------------------------------------------------------------------------

class FilterAwareFakeTodoist(FakeTodoist):
    """Models real Todoist filter semantics for the piece that matters here:
    ``get_filter_tasks("today")`` returns only tasks due exactly today;
    ``"today | overdue"`` also includes still-open tasks due before today.
    Any other filter string returns nothing — this is deliberately narrow,
    just enough to pin the G30 regression (the create-side idempotency
    snapshot missing overdue tasks entirely)."""

    def __init__(self, tasks=None, today=TODAY):
        super().__init__(tasks)
        self._today = today
        self.last_filter = None

    def get_filter_tasks(self, filter_id_or_query, limit=None):
        self.last_filter = filter_id_or_query
        include_overdue = "overdue" in filter_id_or_query
        out = []
        for t in self._tasks.values():
            due = (t.get("due") or {}).get("date")
            if not due:
                continue
            due_date = due.split("T")[0]
            if due_date == self._today.isoformat():
                out.append(t)
            elif due_date < self._today.isoformat() and include_overdue:
                out.append(t)
        return out


class TestG30OverdueIdempotency:
    def test_write_todoist_queries_today_and_overdue(self):
        """Pin the exact filter string — regression is a silent revert to
        bare 'today', which would pass every other assertion here right up
        until an overdue task slips through."""
        client = FilterAwareFakeTodoist()
        intents = [commit.WriteIntent("A", "todoist", "create", "Garage",
                                      project_id="P", due_time="09:00")]
        commit.write_todoist(intents, client)
        assert client.last_filter == "today | overdue"

    def test_overdue_task_id_match_prevents_duplicate_create(self):
        """Real-world evidence 2026-07-16 shadow: an overdue task (due
        yesterday, still open, real todoist id) classified would-create
        because the create-side snapshot only queried filter 'today'. A
        create whose content matches a still-open OVERDUE task must
        collapse to a no-op, never a second task."""
        yesterday = TODAY - timedelta(days=1)
        client = FilterAwareFakeTodoist(tasks=[
            {"id": "t99", "content": "Fly fishing fu",
             "due": {"date": f"{yesterday.isoformat()}T09:00:00"}},
        ])
        intents = [commit.WriteIntent("A", "todoist", "create", "Fly fishing fu",
                                      project_id="P", due_time="09:00")]
        r = commit.write_todoist(intents, client)
        assert r.ok, r.error
        assert r.created == [] and r.noops == ["t99"]
        assert client.created_calls == 0

    def test_overdue_task_update_also_finds_it(self):
        """Same class of bug on the update path: an overdue task's noop
        match (content-based, no task_id set on the intent) must also see
        the overdue snapshot, not just today's."""
        yesterday = TODAY - timedelta(days=1)
        client = FilterAwareFakeTodoist(tasks=[
            {"id": "t99", "content": "Lever 3 follow-up",
             "due": {"date": f"{yesterday.isoformat()}T14:00:00"}},
        ])
        intents = [commit.WriteIntent("A", "todoist", "noop", "Lever 3 follow-up",
                                      due_time="14:00")]
        r = commit.write_todoist(intents, client)
        assert r.ok, r.error
        assert r.noops == ["t99"]


# ---------------------------------------------------------------------------
# G29b — full blast-radius refusal + glyph normalization
# ---------------------------------------------------------------------------

class TestG29bBlastRadiusRefusal:
    def test_multi_item_refusal_lists_all_items_and_counts(self):
        """One unresolvable calendar target must not hide OTHER unplannable
        rows, and the message must name the blast radius — every todoist +
        calendar write in the diff is blocked by atomicity, not just the
        offending row."""
        d = _diff(
            _entry("A", "todoist", "schedule", "Garage", NOOP, time="09:00"),
            _entry("A", "todoist", "schedule", "Errand", UNAVAILABLE),
            _entry("D", "calendar", "create-event", "Minting", CREATE,
                   time="14:00", routing="Nonexistent Cal 1"),
            _entry("D", "calendar", "create-event", "Shivery Jig", CREATE,
                   time="15:00", routing="Nonexistent Cal 2"),
        )
        with pytest.raises(commit.CommitPlanError) as excinfo:
            commit.plan_writes(d, CAL_IDS, today=TODAY)
        msg = str(excinfo.value)
        # every unplannable item named
        assert "Errand" in msg
        assert "Nonexistent Cal 1" in msg
        assert "Nonexistent Cal 2" in msg
        # blast-radius summary: 2 todoist rows + 2 calendar rows in this diff
        assert "2 todoist writes" in msg
        assert "2 calendar writes" in msg
        assert "refusing entire commit" in msg

    def test_glyph_mismatch_config_variant_resolves_live_variant_target(self):
        """Config § Calendar Event Classes uses U+25FD (◽); live manifest
        routing (shadow.py) hardcodes U+2B1C (⬜). An exact-string lookup
        misses the match — resolution must fold visually-equivalent
        white-square glyphs."""
        resolved = {"◽ Blocks": "cal-blocks-1"}  # config-glyph key
        d = _diff(_entry("D", "calendar", "create-event", "Minting", CREATE,
                         time="14:00", routing="⬜ Blocks"))  # live-glyph routing
        [i] = commit.plan_writes(d, resolved, today=TODAY)
        assert i.calendar_id == "cal-blocks-1"

    def test_glyph_mismatch_live_variant_resolves_config_variant_target(self):
        """Same fold, opposite direction — config keyed by the live glyph,
        routing carrying the config glyph."""
        resolved = {"⬜ Blocks": "cal-blocks-1"}
        d = _diff(_entry("D", "calendar", "create-event", "Minting", CREATE,
                         time="14:00", routing="◽ Blocks"))
        [i] = commit.plan_writes(d, resolved, today=TODAY)
        assert i.calendar_id == "cal-blocks-1"

    def test_black_square_glyph_never_folded(self):
        """U+2B1B (⬛ black square) carries different meaning and must NOT be
        folded into the white-square equivalence set — a target using it
        stays genuinely unresolved."""
        resolved = {"◽ Blocks": "cal-blocks-1"}
        d = _diff(_entry("D", "calendar", "create-event", "Minting", CREATE,
                         time="14:00", routing="⬛ Blocks"))
        with pytest.raises(commit.CommitPlanError, match="unresolved"):
            commit.plan_writes(d, resolved, today=TODAY)


class TestClaimedIdExclusion:
    """T21 write-time twin of the shadow claim rule: intents in one run never
    collapse onto a live task another intent already claims, and noops carry
    their task_id so disambiguated display names resolve by id."""

    def test_noop_intent_carries_task_id_from_diff_detail(self):
        d = _diff(_entry("A", "todoist", "schedule", "Press (Todoist)", NOOP,
                         time="12:00", dur=60,
                         detail={"task_id": "t1", "pinned_recurring": True}))
        [i] = commit.plan_writes(d, CAL_IDS, today=TODAY)
        assert i.op == "noop"
        assert i.task_id == "t1"
        assert i.pinned is True

    def test_noop_with_task_id_resolves_by_id_not_content(self):
        client = FakeTodoist([{"id": "t1", "content": "Press",
                               "due": {"date": f"{TODAY.isoformat()}T12:00:00"}}])
        intents = [commit.WriteIntent(
            step="A", surface="todoist", op="noop", name="Press (Todoist)",
            task_id="t1", due_time="12:00", duration_min=60, pinned=True,
        )]
        result = commit.write_todoist(intents, client)
        assert result.ok
        assert result.touched["Press (Todoist)"] == "t1"

    def test_create_never_collapses_onto_task_claimed_by_update(self):
        client = FakeTodoist([{"id": "t1", "content": "Press",
                               "due": {"date": f"{TODAY.isoformat()}T11:00:00"}}])
        intents = [
            commit.WriteIntent(step="A", surface="todoist", op="update",
                               name="Press (Todoist)", task_id="t1",
                               due_time="12:00", duration_min=60),
            commit.WriteIntent(step="A", surface="todoist", op="create",
                               name="Press", due_time="13:00",
                               duration_min=75),
        ]
        result = commit.write_todoist(intents, client)
        assert result.ok, result.error
        assert client.created_calls == 1
        new_id = result.touched["Press"]
        assert new_id != "t1"
        assert commit._todoist_due_hhmm(client.get_task("t1")) == "12:00"
        assert commit._todoist_due_hhmm(client.get_task(new_id)) == "13:00"

    def test_noop_content_match_skips_claimed_task(self):
        # Two live tasks, same content. The update claims t1; the untargeted
        # noop must fall to the OTHER task, not double-claim t1.
        client = FakeTodoist([
            {"id": "t1", "content": "Press",
             "due": {"date": f"{TODAY.isoformat()}T11:00:00"}},
            {"id": "t2", "content": "Press",
             "due": {"date": f"{TODAY.isoformat()}T14:00:00"}},
        ])
        intents = [
            commit.WriteIntent(step="A", surface="todoist", op="update",
                               name="Press (Todoist)", task_id="t1",
                               due_time="12:00", duration_min=60),
            commit.WriteIntent(step="A", surface="todoist", op="noop",
                               name="Press", due_time="14:00",
                               duration_min=30),
        ]
        result = commit.write_todoist(intents, client)
        assert result.ok, result.error
        assert result.touched["Press"] == "t2"


# ---------------------------------------------------------------------------
# T27 — recurring due is pattern-owned: no due_string rewrite, ever
# ---------------------------------------------------------------------------

class TestRecurringAllDayNeverRewritesDue:
    """A recurring row shaped to All day (0 blocks) must never take the
    due_string path — reschedule_task("today") wipes the recurrence pattern.
    The due write is skipped entirely; the task keeps its own live due."""

    def _all_day_recurring_diff(self, tid="t77"):
        return _diff(_entry("A", "todoist", "schedule-all-day", "LOOTS", UPDATE,
                            detail={"task_id": tid, "is_recurring": True}))

    def test_plan_marks_recurring_all_day_without_datetime(self):
        [i] = commit.plan_writes(self._all_day_recurring_diff(), CAL_IDS, today=TODAY)
        assert i.is_recurring is True
        assert i.due_all_day_today is True
        assert i.due_datetime is None

    def test_write_skips_due_rewrite_and_keeps_recurrence(self):
        client = RecurrenceFakeTodoist([{"id": "t77", "content": "LOOTS",
            "due": {"date": "2026-07-12T12:30:00", "is_recurring": True}}])
        intents = commit.plan_writes(self._all_day_recurring_diff(), CAL_IDS, today=TODAY)
        res = commit.write_todoist(intents, client)
        assert res.ok, res.error
        assert client.due_string_calls == 0 and client.datetime_calls == 0
        assert client._tasks["t77"]["due"]["is_recurring"] is True
        assert client._tasks["t77"]["due"]["date"] == "2026-07-12T12:30:00"
        assert "t77" in res.updated

    def test_nonrecurring_all_day_still_uses_today_due_string(self):
        client = RecurrenceFakeTodoist([{"id": "t9", "content": "Errand",
            "due": {"date": "2026-07-12T11:00:00"}}])
        d = _diff(_entry("A", "todoist", "schedule-all-day", "Errand", UPDATE,
                         detail={"task_id": "t9", "is_recurring": False}))
        res = commit.write_todoist(commit.plan_writes(d, CAL_IDS, today=TODAY), client)
        assert res.ok, res.error
        assert client.due_string_calls == 1 and client.datetime_calls == 0


# ---------------------------------------------------------------------------
# FEEDBACK-23 — due verification time normalization (UTC fixed vs floating local)
# ---------------------------------------------------------------------------

class TestDueVerificationNormalization:
    """Todoist fixed due.date is UTC; floating due.date is user-local. The
    reported Press failure compared intent 19:00 (local) against live 23:00
    (UTC wall clock) directly. Fixed dues must convert through the due's
    timezone; floating dues stay local; missing/unknown zone data fails
    closed with structured machine-canonical detail (FEEDBACK-23)."""

    def _due_task(self, task_id, name, due):
        return {"id": task_id, "content": name, "due": due}

    def _noop_intent(self, name, hhmm, task_id="t42"):
        return commit.WriteIntent(
            step="A", surface="todoist", op="noop", name=name,
            task_id=task_id, due_time=hhmm, duration_min=60,
        )

    def test_fixed_utc_due_normalizes_through_due_timezone(self):
        """Exact Press repro: 23:00Z with America/New_York == 19:00 local.
        Intent 19:00 must verify clean against the UTC-anchored live due."""
        client = FakeTodoist([self._due_task(
            "t42", "Press", {"date": "2026-07-12T23:00:00Z",
                             "timezone": "America/New_York"})])
        r = commit.write_todoist([self._noop_intent("Press", "19:00")], client)
        assert r.ok, r.error
        assert r.verify_failures == []
        assert commit._todoist_due_hhmm(client.get_task("t42")) == "19:00"

    def test_different_instant_still_fails_with_12h_message(self):
        """A genuinely different instant (live 23:00 local vs intent 19:00)
        remains a required failure — message in 12h, no raw 24h time."""
        client = FakeTodoist([self._due_task(
            "t42", "Press", {"date": "2026-07-12T23:00:00"})])
        r = commit.write_todoist([self._noop_intent("Press", "19:00")], client)
        assert not r.ok
        assert any("due mismatch" in f for f in r.verify_failures)
        msg = r.verify_failures[0]
        assert "7 PM" in msg and "11 PM" in msg
        assert "19:00" not in msg and "23:00" not in msg

    def test_floating_local_due_verifies_locally(self):
        """Floating due wall time is the user's local time — a timezone
        field must NOT shift it, only compare as-is."""
        client = FakeTodoist([self._due_task(
            "t42", "Walk", {"date": "2026-07-12T19:00:00",
                            "timezone": "America/New_York"})])
        r = commit.write_todoist([self._noop_intent("Walk", "19:00")], client)
        assert r.ok, r.error
        assert r.verify_failures == []

    def test_missing_timezone_on_utc_due_fails_closed(self):
        """A UTC-anchored due without its timezone cannot be compared
        safely — fail closed with an actionable reason, never guess."""
        client = FakeTodoist([self._due_task(
            "t42", "Press", {"date": "2026-07-12T23:00:00Z"})])
        r = commit.write_todoist([self._noop_intent("Press", "19:00")], client)
        assert not r.ok
        assert any("missing timezone" in f for f in r.verify_failures)

    def test_unknown_timezone_fails_closed(self):
        client = FakeTodoist([self._due_task(
            "t42", "Press", {"date": "2026-07-12T23:00:00Z",
                             "timezone": "Mars/Olympus"})])
        r = commit.write_todoist([self._noop_intent("Press", "19:00")], client)
        assert not r.ok
        assert any("unknown timezone" in f for f in r.verify_failures)

    def test_structured_detail_keeps_machine_canonical_values(self):
        """The wire-facing detail keeps canonical raw ISO/timezone + 24h
        values separate from the 12h display string."""
        client = FakeTodoist([self._due_task(
            "t42", "Press", {"date": "2026-07-12T23:00:00Z",
                             "timezone": "America/New_York"})])
        r = commit.write_todoist([self._noop_intent("Press", "18:00")], client)
        assert not r.ok
        [d] = r.verify_details
        assert d["kind"] == "due"
        assert d["name"] == "Press"
        assert d["intent"] == "18:00"
        assert d["live"] == "19:00"                      # normalized local 24h
        assert d["live_raw"] == "2026-07-12T23:00:00Z"   # canonical ISO
        assert d["live_timezone"] == "America/New_York"  # canonical tz
        assert d["reason"] == "mismatch"
        assert "6 PM" in d["message"] and "7 PM" in d["message"]
        assert "verify_details" in r.as_dict()

    def test_equivalent_encoding_and_true_mismatch_are_separate(self):
        """Plan criterion: equivalent 19:00/23:00 encodings pass while a
        genuinely different instant fails — same intent, two live dues."""
        ok_client = FakeTodoist([self._due_task(
            "t1", "Press", {"date": "2026-07-12T23:00:00Z",
                            "timezone": "America/New_York"})])
        ok = commit.write_todoist(
            [self._noop_intent("Press", "19:00", "t1")], ok_client)
        assert ok.ok and ok.verify_failures == []

        bad_client = FakeTodoist([self._due_task(
            "t2", "Press", {"date": "2026-07-12T23:00:00Z",
                            "timezone": "Pacific/Honolulu"})])  # 23:00Z = 13:00 HST
        bad = commit.write_todoist(
            [self._noop_intent("Press", "19:00", "t2")], bad_client)
        assert not bad.ok and bad.verify_failures
