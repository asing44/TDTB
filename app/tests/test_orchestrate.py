"""Tests for orchestrate.py — T15 failure-safe commit orchestrator.

Covers the ledgered-dispatch invariants: fixed surface order, a failing
surface never aborting the run, per-surface crash-consistency (the ledger
lands on disk after each surface, not just at the end), resume skipping
known-ok surfaces without re-invoking their writer, preservation of other
runstate keys across a run, and recent-selections appended only on a clean
all-ok run. Fakes are copied from tests/test_commit.py (see its docstring)
rather than imported, so this file has no cross-test-module import wiring.
"""
from __future__ import annotations

import copy
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import calendar_bridge  # noqa: E402
import commit  # noqa: E402
import orchestrate  # noqa: E402
import runstate as runstate_mod  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent.parent / "gather"))
import tdtb_gather as gather  # noqa: E402

TODAY = date(2026, 7, 12)


# ---------------------------------------------------------------------------
# fakes (copied from tests/test_commit.py)
# ---------------------------------------------------------------------------

class FakeTodoist:
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
        due = {"date": f"{TODAY.isoformat()}T{hhmm}:00"} if hhmm else None
        self._tasks[tid] = {"id": tid, "content": content, "due": due,
                            "project_id": project_id}
        self.created_calls += 1
        return self._tasks[tid]

    def reschedule_task(self, task_id, due_string):
        hhmm = due_string.split("at ", 1)[1].strip() if "at " in due_string else None
        if hhmm:
            self._tasks[task_id]["due"] = {"date": f"{TODAY.isoformat()}T{hhmm}:00"}
        return self._tasks[task_id]


class Dropping(FakeTodoist):
    """A create whose readback vanishes — forces write_todoist to fail."""

    def create_task(self, *a, **k):
        self.created_calls += 1
        return {"id": "gone"}  # never stored -> get_task raises KeyError


class FakeStore:
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


# ---------------------------------------------------------------------------
# vault fixtures (V3: the vault is never read or written; the argument only
# anchors the state-dir ledger path, so an empty directory is enough)
# ---------------------------------------------------------------------------

SELECTION_PATH = "P/Garage.md"


def _vault(tmp_path: Path) -> Path:
    return tmp_path


def _both_surface_intents() -> list[commit.WriteIntent]:
    return [
        commit.WriteIntent("A", "todoist", "create", "Garage",
                           project_id=None, due_time="09:00", duration_min=30),
        commit.WriteIntent("D", "calendar", "create", "Minting",
                           calendar_id="cal-blocks-1", due_time="14:00",
                           start=datetime(2026, 7, 12, 14, 0), end=datetime(2026, 7, 12, 15, 0)),
    ]


def _ledger_from_disk(today: date = TODAY) -> dict:
    path = runstate_mod.runstate_read_path(today)
    data = gather._extract_json_block(path.read_text(encoding="utf-8"))
    assert data is not None
    return data


# ---------------------------------------------------------------------------
# 1. all-ok run over both surfaces (todoist, calendar)
# ---------------------------------------------------------------------------

def test_all_ok_both_surfaces_persists_ledger(tmp_path):
    vault = _vault(tmp_path)
    report = orchestrate.run_orchestrated(
        _both_surface_intents(), todoist=FakeTodoist(), store=FakeStore(), today=TODAY,
    )
    assert report["ok"] is True
    assert report["resumed"] is False
    assert set(report["surfaces"]) == set(orchestrate.SURFACES)
    for key in orchestrate.SURFACES:
        assert report["surfaces"][key]["status"] == "ok"

    data = _ledger_from_disk()
    assert data["commit_ledger"]["surfaces"]["todoist"]["status"] == "ok"
    # V3: the vault_flips / daily_note surfaces are gone from the ledger
    assert set(data["commit_ledger"]["surfaces"]) == {"todoist", "calendar"}
    assert data["commit_ledger"]["today"] == str(TODAY)


def test_ledger_estimation_section_records_planned_sizes(tmp_path):
    """G18a instrumentation: create/update intents with a duration land in the
    ledger's estimation list (planned side of actual-vs-estimated)."""
    vault = _vault(tmp_path)
    orchestrate.run_orchestrated(
        _both_surface_intents(), todoist=FakeTodoist(), store=FakeStore(), today=TODAY,
    )
    est = _ledger_from_disk()["commit_ledger"]["estimation"]
    garage = next(e for e in est if e["name"] == "Garage")
    assert garage["planned_blocks"] == 1
    assert garage["planned_duration_min"] == 30
    assert garage["time"] == "09:00"
    # zero-duration intents (the durationless calendar row) never enter the
    # estimation log
    assert all(e["planned_duration_min"] > 0 for e in est)


def test_ledger_preserves_fifteen_minute_half_block(tmp_path):
    vault = _vault(tmp_path)
    intents = _both_surface_intents()
    garage = next(i for i in intents if i.name == "Garage")
    garage.duration_min = 15
    orchestrate.run_orchestrated(
        intents, todoist=FakeTodoist(), store=FakeStore(), today=TODAY,
    )
    est = _ledger_from_disk()["commit_ledger"]["estimation"]
    assert next(e for e in est if e["name"] == "Garage")["planned_blocks"] == 0.5


# ---------------------------------------------------------------------------
# 2. per-surface failure — one test per surface, the other still runs
# ---------------------------------------------------------------------------

class TestPerSurfaceFailureDoesNotAbort:
    def _others_ok(self, surfaces: dict, failing: str) -> None:
        for key in orchestrate.SURFACES:
            if key == failing:
                assert surfaces[key]["status"] == "failed"
            else:
                assert surfaces[key]["status"] == "ok", f"{key} unexpectedly not ok"

    def test_todoist_failure(self, tmp_path):
        vault = _vault(tmp_path)
        report = orchestrate.run_orchestrated(
            _both_surface_intents(), todoist=Dropping(), store=FakeStore(), today=TODAY,
        )
        assert report["ok"] is False
        self._others_ok(report["surfaces"], "todoist")
        assert any("todoist" in f for f in report["failed"])

    def test_calendar_failure_wrong_surface_store(self, tmp_path):
        vault = _vault(tmp_path)
        report = orchestrate.run_orchestrated(
            _both_surface_intents(), todoist=FakeTodoist(), store=FakeStore(wrong_surface=True),
            today=TODAY,
        )
        assert report["ok"] is False
        self._others_ok(report["surfaces"], "calendar")


# ---------------------------------------------------------------------------
# 3. resume: known-ok surfaces are not re-dispatched
# ---------------------------------------------------------------------------

def _spy(monkeypatch, module, name):
    orig = getattr(module, name)
    calls: list[int] = []

    def wrapper(*a, **kw):
        calls.append(1)
        return orig(*a, **kw)

    monkeypatch.setattr(module, name, wrapper)
    return calls


def test_resume_skips_known_ok_surfaces(tmp_path, monkeypatch):
    vault = _vault(tmp_path)
    calls_a = _spy(monkeypatch, commit, "write_todoist")
    calls_d = _spy(monkeypatch, commit, "write_calendar")

    todoist = FakeTodoist()
    bad_store = FakeStore(wrong_surface=True)

    report1 = orchestrate.run_orchestrated(
        _both_surface_intents(), todoist=todoist, store=bad_store, today=TODAY,
    )
    assert report1["ok"] is False
    assert report1["surfaces"]["calendar"]["status"] == "failed"
    assert report1["surfaces"]["todoist"]["status"] == "ok"
    assert (len(calls_a), len(calls_d)) == (1, 1)

    good_store = FakeStore()
    report2 = orchestrate.run_orchestrated(
        _both_surface_intents(), todoist=todoist, store=good_store, today=TODAY, resume=True,
    )
    assert report2["ok"] is True
    assert report2["resumed"] is True
    assert report2["surfaces"]["calendar"]["status"] == "ok"
    assert report2["surfaces"]["todoist"]["note"] == "resumed: already ok"
    # the already-ok surface was never re-dispatched; only calendar ran again
    assert (len(calls_a), len(calls_d)) == (1, 2)


# ---------------------------------------------------------------------------
# 4. ledger preserves other runstate keys
# ---------------------------------------------------------------------------

def test_ledger_preserves_prior_runstate_selections(tmp_path):
    vault = _vault(tmp_path)
    prior_date = TODAY - timedelta(days=1)
    runstate_mod.write_runstate(
        prior_date,
        runstate_mod.build_runstate({"selections": [{"id": "x", "path": "p", "blocks": 1}]}),
    )

    intents = [commit.WriteIntent("A", "todoist", "create", "Garage", due_time="09:00")]
    report = orchestrate.run_orchestrated(
        intents, todoist=FakeTodoist(), today=TODAY,
    )
    assert report["ok"] is True

    data = _ledger_from_disk()
    assert "commit_ledger" in data
    assert data["selections"] == [{"id": "x", "path": "p", "blocks": 1}]


# ---------------------------------------------------------------------------
# 5. per-surface crash-consistency
# ---------------------------------------------------------------------------

def test_crash_consistency_earlier_surfaces_persisted_before_last_fails(tmp_path, monkeypatch):
    vault = _vault(tmp_path)
    snapshots: list[dict] = []
    orig_write = runstate_mod.write_runstate

    def spy_write(valid_date, state, now=None):
        snapshots.append(copy.deepcopy(state))
        return orig_write(valid_date, state, now=now)

    monkeypatch.setattr(runstate_mod, "write_runstate", spy_write)

    report = orchestrate.run_orchestrated(
        _both_surface_intents(), todoist=FakeTodoist(), store=FakeStore(wrong_surface=True),
        today=TODAY,
    )
    assert report["ok"] is False
    assert report["surfaces"]["calendar"]["status"] == "failed"

    # the write persisted right after todoist (the 1st surface, before
    # calendar was ever dispatched) already has the ok entry and no calendar key
    before_calendar = snapshots[0]
    surfaces_so_far = before_calendar["commit_ledger"]["surfaces"]
    assert set(surfaces_so_far) == {"todoist"}
    assert all(e["status"] == "ok" for e in surfaces_so_far.values())

    # the final on-disk state does include the failed calendar entry
    final = _ledger_from_disk()
    assert final["commit_ledger"]["surfaces"]["calendar"]["status"] == "failed"


# ---------------------------------------------------------------------------
# 6. empty-subset surface (no calendar intents, store=None) is ok, not failed
# ---------------------------------------------------------------------------

def test_empty_calendar_subset_with_no_store_is_ok(tmp_path):
    vault = _vault(tmp_path)
    intents = [
        commit.WriteIntent("A", "todoist", "create", "Garage", due_time="09:00"),
    ]
    report = orchestrate.run_orchestrated(
        intents, todoist=FakeTodoist(), store=None, today=TODAY,
    )
    assert report["ok"] is True
    cal_entry = report["surfaces"]["calendar"]
    assert cal_entry["status"] == "ok"
    assert cal_entry["note"] == "no intents"
    assert cal_entry["created"] == cal_entry["updated"] == cal_entry["noops"] == []


# ---------------------------------------------------------------------------
# 7. recent-selections appended only on all-ok
# ---------------------------------------------------------------------------

def test_recent_selections_appended_only_on_all_ok(tmp_path):
    vault = _vault(tmp_path)
    sel = [{"id": "t1", "path": SELECTION_PATH, "blocks": 2}]

    intents = [commit.WriteIntent("A", "todoist", "create", "Garage", due_time="09:00")]
    report = orchestrate.run_orchestrated(
        intents, todoist=FakeTodoist(), today=TODAY, selections=sel,
    )
    assert report["ok"] is True
    runs = runstate_mod.read_recent_selections()
    assert len(runs) == 1
    assert runs[0]["selections"][0]["path"] == SELECTION_PATH


def test_recent_selections_not_appended_on_failure(tmp_path):
    vault = _vault(tmp_path)
    sel = [{"id": "t2", "path": "x", "blocks": 1}]

    intents = [commit.WriteIntent("A", "todoist", "create", "Garage", due_time="09:00")]
    report = orchestrate.run_orchestrated(
        intents, todoist=Dropping(), today=TODAY, selections=sel,
    )
    assert report["ok"] is False
    assert runstate_mod.read_recent_selections() == []


class TestVerifyFailuresReport:
    """T9 (ui-parity): the commit report aggregates per-write verify failures;
    any entry blocks a bake-in PASS downstream."""

    def test_report_aggregates_verify_failures(self, tmp_path):
        import commit as commit_mod

        class BadTodoist:
            def get_filter_tasks(self, *a, **k): return []
            def get_task(self, tid): return {"id": tid, "due": {"date": "2026-07-12"}}
            def create_task(self, content, project_id=None, **f):
                return {"id": "t9x", "content": content, "due": {"date": "2026-07-12"}}
            def reschedule_task(self, *a, **k): return {}
            def reschedule_task_datetime(self, *a, **k): return {}

        intents = [commit_mod.WriteIntent("A", "todoist", "create", "Garage",
                                          project_id="P", due_time="09:00")]
        report = orchestrate.run_orchestrated(
            intents, todoist=BadTodoist(), persist_ledger=False)
        assert report["ok"] is False
        assert report["verify_failures"]
        assert any("Garage" in f for f in report["verify_failures"])

    def test_clean_report_has_empty_verify_failures(self, tmp_path):
        report = orchestrate.run_orchestrated([], persist_ledger=False)
        assert report["verify_failures"] == []
        assert report["verify_details"] == []

    def test_report_aggregates_verify_details_with_canonical_fields(self, tmp_path):
        """FEEDBACK-23: the report carries structured due-verification detail
        (machine-canonical values) alongside the 12h display strings."""
        import commit as commit_mod

        class BadTodoist:
            def get_filter_tasks(self, *a, **k): return []
            def get_task(self, tid): return {"id": tid, "due": {"date": "2026-07-12"}}
            def create_task(self, content, project_id=None, **f):
                return {"id": "t9x", "content": content,
                        "due": {"date": "2026-07-12"}}
            def reschedule_task(self, *a, **k): return {}
            def reschedule_task_datetime(self, *a, **k): return {}

        intents = [commit_mod.WriteIntent("A", "todoist", "create", "Garage",
                                          project_id="P", due_time="09:00")]
        report = orchestrate.run_orchestrated(
            intents, todoist=BadTodoist(), persist_ledger=False)
        assert report["verify_details"]
        d = report["verify_details"][0]
        assert d["kind"] == "due"
        assert d["name"] == "Garage"
        assert d["intent"] == "09:00" and d["live"] is None
        assert d["live_raw"] == "2026-07-12"  # canonical raw due preserved
        assert d["reason"] == "mismatch"
        assert "9 AM" in d["message"]         # 12h display in the string
        assert report["verify_failures"]
