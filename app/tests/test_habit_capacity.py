"""test_habit_capacity.py — habit time from the artifact, never the vault.

Obsidian is retired: the habit done/total split and the outstanding-minutes
estimate ride the planning artifact's top-level ``habits`` block, computed by
the producer (``producer_rules.compute_habit_summary``). This file pins the
producer arithmetic (grain rounding, per-habit fallback) and the end-to-end
capacity effect through /plan-inputs.

Habit completion is NOT read: Adam tracks habits in Grit, so the Todoist
Habits list is the active set and toggling happens in the app's per-day
overrides. Every habit is therefore outstanding, every habit's minutes are
reserved, and any completion data that reaches the producer (a legacy fetch,
an old artifact) is ignored rather than honoured.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import artifact_source as art
import main
import producer_rules as pr
from tests.config_markdown import write_config_note  # noqa: E402


TODAY = date(2026, 7, 26)
LOGICAL_DAY = TODAY.isoformat()
#: The day strictly after the logical day: a habit due then is not today's.
FUTURE = (TODAY + timedelta(days=1)).isoformat()

CONFIG = """\
---
description: habit capacity test config
last_updated: 2026-07-26
---

# TDTB Bridger Config

## Defaults

| Key | Value    |
| --- | -------- |
| eod | 11:45 PM |
"""


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _write_artifact(habits, *, logical_day=LOGICAL_DAY, path=None) -> Path:
    """Write a minimal, valid, fresh artifact carrying ``habits`` (or none)."""
    rows: list[dict] = []
    read_at = _now()
    sources = {
        "todoist": {"status": "ok", "read_at": read_at, "rows": 0,
                    "dropped": 0, "deferred": 0, "warnings": []}
    }
    document = {
        "schema": art.ARTIFACT_SCHEMA,
        "version": art.ARTIFACT_VERSION,
        "generated_at": read_at,
        "logical_day": logical_day,
        "producer": {"name": "test", "version": "0.1.0", "run_id": "r1"},
        "content_hash": art.compute_content_hash(sources, rows),
        "sources": sources,
        "rows": rows,
        "admission": {"rule_set_hash": "rs", "admitted": [], "dropped": []},
    }
    if habits is not None:
        document["habits"] = habits
    target = Path(path) if path is not None else art.artifact_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(document), encoding="utf-8")
    return target


def _write_document(document: dict, path: Path | None = None) -> Path:
    """Persist a producer-built artifact document where the app reads it."""
    target = Path(path) if path is not None else art.artifact_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(document), encoding="utf-8")
    return target


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault-root"
    (root / "00 - META" / "Skill-Configs").mkdir(parents=True)
    write_config_note(root / "00 - META" / "Skill-Configs" / "tdtb-bridger.md",
        CONFIG, encoding="utf-8")
    return root


def _client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(main.gather, "effective_date", lambda _n: TODAY)
    return TestClient(main.create_app())


def _body(monkeypatch: pytest.MonkeyPatch) -> dict:
    response = _client(monkeypatch).get("/plan-inputs")
    assert response.status_code == 200, response.text
    return response.json()


def _habit_entry(tasks, completed=(), **knobs) -> dict:
    """A habits source entry. ``completed`` keeps the legacy fetch shape
    reachable so tests can prove that completion data is IGNORED."""
    entry = {
        "status": "ok",
        "read_at": _now(),
        "tasks": list(tasks),
        "completed": list(completed),
    }
    entry.update(knobs)
    return entry


# ---------------------------------------------------------------------------
# Producer — the deterministic estimate
# ---------------------------------------------------------------------------

def _local_stamp(day: str, hour: int = 16, minute: int = 25) -> str:
    """A UTC ``completedAt`` whose LOCAL date is ``day``, whatever the test TZ.

    Built from a local wall-clock time rather than a literal UTC instant, so
    the assertion does not depend on the machine's timezone."""
    y, m, d = (int(part) for part in day.split("-"))
    local = datetime(y, m, d, hour, minute).astimezone()
    return local.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class TestComputeHabitSummary:
    def test_counts_and_sums_outstanding_durations(self):
        entry = _habit_entry([
            {"id": "a", "content": "A", "duration": "25m"},
            {"id": "b", "content": "B", "duration": "10m"},
            {"id": "c", "content": "C", "duration": "5m"},
        ])
        # 25 + 10 + 5 = 40 min -> round up to the 15-min grain = 45
        result = pr.compute_habit_summary(entry)
        assert {k: result[k] for k in (
            "total", "done", "outstanding", "est_minutes",
        )} == {"total": 3, "done": 0, "outstanding": 3, "est_minutes": 45}
        assert result["items"] == [
            {"id": "a", "content": "A", "minutes": 25},
            {"id": "b", "content": "B", "minutes": 10},
            {"id": "c", "content": "C", "minutes": 5},
        ]

    def test_unset_duration_uses_the_per_habit_fallback(self):
        entry = _habit_entry([{"id": "a", "content": "A"}])  # fallback 4 min
        assert pr.compute_habit_summary(entry)["est_minutes"] == 15

    def test_zero_duration_falls_back(self):
        entry = _habit_entry([{"id": "a", "content": "A", "duration": "0m"}])
        assert pr.compute_habit_summary(entry)["est_minutes"] == 15

    def test_grain_rounds_up_and_is_configurable(self):
        entry = _habit_entry([
            {"id": "a", "content": "A", "duration": "20m"},
            {"id": "b", "content": "B"},  # + 4 fallback = 24
        ])
        assert pr.compute_habit_summary(entry)["est_minutes"] == 30  # 24 -> 30
        entry["fallback_minutes_per_habit"] = 10
        entry["round_to_minutes"] = 30
        assert pr.compute_habit_summary(entry)["est_minutes"] == 30  # 30 -> 30

    def test_five_habits_with_two_completions_are_all_outstanding(self):
        """The contract example: completions never subtract. Five habits, two
        referenced by today's completion tokens, still total five and reserve
        all five habits' minutes."""
        entry = _habit_entry(
            [{"id": "a", "content": "A", "duration": "25m"},
             {"id": "b", "content": "B", "duration": "25m"},
             {"id": "c", "content": "C", "duration": "10m"},
             {"id": "d", "content": "D", "duration": "10m"},
             {"id": "e", "content": "E", "duration": "10m"}],
            completed=[{"task_id": "a"}, {"content": "B"}],
        )
        # 25 + 25 + 10 + 10 + 10 = 80 min -> 90 at the 15-min grain.
        result = pr.compute_habit_summary(entry, logical_day="2026-10-08")
        assert {k: result[k] for k in (
            "total", "done", "outstanding", "est_minutes",
        )} == {"total": 5, "done": 0, "outstanding": 5, "est_minutes": 90}
        assert [item["id"] for item in result["items"]] == [
            "a", "b", "c", "d", "e",
        ]

    @pytest.mark.parametrize("completed", [
        pytest.param([{"task_id": "a"}], id="id-today-undated"),
        pytest.param([{"id": "a"}], id="bare-object-id"),
        pytest.param(["a"], id="bare-string-id"),
        pytest.param([{"content": "A"}], id="content-name"),
        pytest.param([{"task_id": "a",
                       "completedAt": _local_stamp("2026-10-08")}],
                     id="id-completed-on-the-logical-day"),
        pytest.param([{"task_id": "a",
                       "completedAt": _local_stamp("2026-10-06")}],
                     id="id-completed-earlier-in-the-week"),
        pytest.param([{"task_id": "a", "completedAt": "not-a-timestamp"}],
                     id="malformed-completed-at"),
        pytest.param([{"task_id": "a"}, {"content": "B"}, "b"],
                     id="redundant-matches"),
    ])
    def test_completion_data_never_changes_the_block(self, completed):
        """Every legacy completion shape is ignored, so the block equals the
        completion-free baseline exactly."""
        tasks = [{"id": "a", "content": "A", "duration": "25m"},
                 {"id": "b", "content": "B", "duration": "25m"}]
        baseline = pr.compute_habit_summary(
            _habit_entry(tasks), logical_day="2026-10-08"
        )
        result = pr.compute_habit_summary(
            _habit_entry(tasks, completed=completed), logical_day="2026-10-08"
        )
        assert result == baseline
        # 25 + 25 = 50 min -> 60 at the 15-min grain; nothing is subtracted.
        assert {k: result[k] for k in (
            "total", "done", "outstanding", "est_minutes",
        )} == {"total": 2, "done": 0, "outstanding": 2, "est_minutes": 60}

    def test_a_completion_shaped_like_a_working_record_is_ignored(self):
        """A completion carried in the older, richer shapes still never counts."""
        entry = _habit_entry(
            [{"id": "a", "content": "A", "duration": "30m"}],
            completed=[{"task_id": "a", "taskId": "a", "id": "c1",
                        "content": "A", "name": "A", "completed_at":
                        _local_stamp("2026-10-08")}],
        )
        assert pr.compute_habit_summary(entry, logical_day="2026-10-08") == {
            "total": 1, "done": 0, "outstanding": 1, "est_minutes": 30,
            "items": [{"id": "a", "content": "A", "minutes": 30}],
        }

    def test_every_habit_completed_still_reserves_every_minute(self):
        entry = _habit_entry(
            [{"id": "a", "content": "A", "duration": "30m"}],
            completed=[{"id": "a"}],
        )
        assert pr.compute_habit_summary(entry) == {
            "total": 1, "done": 0, "outstanding": 1, "est_minutes": 30,
            "items": [{"id": "a", "content": "A", "minutes": 30}],
        }

    def test_missing_entry_is_the_zeroed_block(self):
        assert pr.compute_habit_summary(None) == art.HABITS_EMPTY


# ---------------------------------------------------------------------------
# Producer — due-date eligibility against the caller's logical day
# ---------------------------------------------------------------------------

class TestHabitDueDateScope:
    """Habit tasks are scheduled only for a day on or before the logical day.

    The habit source carries RAW Todoist records (it bypasses ``normalize_task``),
    so the producer reads the MCP flat ``dueDate`` and the REST nested ``due``
    directly. A task due strictly AFTER the caller's logical day is not part of
    that day's plan; counting it inflates both the estimate and the item list.
    The scope compares CIVIL due dates, never the wall clock, and never remaps a
    due date through the 02:00 cutoff a second time."""

    def test_mcp_due_dates_keep_today_overdue_and_undated(self):
        entry = _habit_entry([
            {"id": "today", "content": "Habit today", "duration": "10m",
             "dueDate": LOGICAL_DAY},
            {"id": "overdue", "content": "Habit overdue", "duration": "10m",
             "dueDate": "2026-07-25"},
            {"id": "future", "content": "Habit future", "duration": "10m",
             "dueDate": FUTURE},
            {"id": "undated", "content": "Habit undated", "duration": "10m"},
        ])
        result = pr.compute_habit_summary(entry, logical_day=LOGICAL_DAY)
        assert result == {
            "total": 3, "done": 0, "outstanding": 3, "est_minutes": 30,
            "items": [
                {"id": "today", "content": "Habit today", "minutes": 10},
                {"id": "overdue", "content": "Habit overdue", "minutes": 10},
                {"id": "undated", "content": "Habit undated", "minutes": 10},
            ],
        }

    def test_mcp_timed_due_date_uses_its_civil_day(self):
        entry = _habit_entry([
            {"id": "today", "content": "Habit today", "duration": "10m",
             "dueDate": "2026-07-26T22:00:00"},
            {"id": "future", "content": "Habit future", "duration": "10m",
             "dueDate": "2026-07-27T22:00:00"},
        ])
        result = pr.compute_habit_summary(entry, logical_day=LOGICAL_DAY)
        assert [i["id"] for i in result["items"]] == ["today"]
        assert result["total"] == 1

    def test_rest_due_date_and_datetime_are_read(self):
        entry = _habit_entry([
            {"id": "date", "content": "Habit date", "duration": "10m",
             "due": {"date": "2026-07-26"}},
            {"id": "timed", "content": "Habit timed", "duration": "10m",
             "due": {"date": "2026-07-26T09:00:00"}},
            {"id": "dt", "content": "Habit datetime", "duration": "10m",
             "due": {"datetime": "2026-07-26T09:00:00"}},
            {"id": "future", "content": "Habit future", "duration": "10m",
             "due": {"date": "2026-07-27"}},
        ])
        result = pr.compute_habit_summary(entry, logical_day=LOGICAL_DAY)
        assert [i["id"] for i in result["items"]] == ["date", "timed", "dt"]
        assert result["total"] == 3

    @pytest.mark.parametrize("task", [
        {"id": "a", "duration": "10m", "due": "tomorrow"},
        {"id": "b", "duration": "10m", "due": ["2026-07-27"]},
        {"id": "c", "duration": "10m", "due": {"date": "not-a-date"}},
        {"id": "d", "duration": "10m", "due": {"datetime": 20260727}},
        {"id": "e", "duration": "10m", "dueDate": "2026-13-40"},
        {"id": "f", "duration": "10m", "dueDate": 20260727},
        {"id": "g", "duration": "10m", "dueDate": None, "due": None},
    ])
    def test_malformed_due_values_keep_old_inclusion(self, task):
        # A malformed due value is not evidence of a future task; the task stays
        # counted exactly as it was before the scope existed.
        result = pr.compute_habit_summary(
            _habit_entry([task]), logical_day=LOGICAL_DAY
        )
        assert result["total"] == 1
        assert result["est_minutes"] == 15  # 10 -> 15 at the grain

    def test_deadline_never_stands_in_for_a_due_date(self):
        task = {"id": "d", "content": "Habit deadline", "duration": "10m",
                "deadlineDate": "2029-01-01"}
        result = pr.compute_habit_summary(
            _habit_entry([task]), logical_day=LOGICAL_DAY
        )
        assert result["total"] == 1

    def test_missing_or_invalid_logical_day_stays_unscoped(self):
        entry = _habit_entry([
            {"id": "today", "content": "Habit today", "duration": "10m",
             "dueDate": LOGICAL_DAY},
            {"id": "future", "content": "Habit future", "duration": "10m",
             "dueDate": "2027-01-01"},
        ])
        assert pr.compute_habit_summary(entry)["total"] == 2
        assert pr.compute_habit_summary(entry, logical_day="garbage")["total"] == 2

    def test_a_completion_on_a_future_habit_changes_nothing(self):
        entry = _habit_entry(
            [{"id": "today", "content": "Habit today", "duration": "10m",
              "dueDate": LOGICAL_DAY},
             {"id": "future", "content": "Habit future", "duration": "10m",
              "dueDate": FUTURE}],
            completed=[{"task_id": "future"}],
        )
        result = pr.compute_habit_summary(entry, logical_day=LOGICAL_DAY)
        assert result == {
            "total": 1, "done": 0, "outstanding": 1, "est_minutes": 15,
            "items": [{"id": "today", "content": "Habit today", "minutes": 10}],
        }

    def test_eligible_completed_today_still_reserves_minutes(self):
        entry = _habit_entry(
            [{"id": "a", "content": "Habit A", "duration": "25m",
              "dueDate": LOGICAL_DAY},
             {"id": "b", "content": "Habit B", "duration": "25m",
              "dueDate": LOGICAL_DAY}],
            completed=[{"task_id": "a"}],
        )
        result = pr.compute_habit_summary(entry, logical_day=LOGICAL_DAY)
        assert {k: result[k] for k in (
            "total", "done", "outstanding", "est_minutes",
        )} == {"total": 2, "done": 0, "outstanding": 2, "est_minutes": 60}
        assert result["items"] == [
            {"id": "a", "content": "Habit A", "minutes": 25},
            {"id": "b", "content": "Habit B", "minutes": 25},
        ]

    def test_caller_logical_day_governs_not_the_wall_clock(self):
        # At 01:00 the 02:00 cutoff still puts the caller on the previous civil
        # day. The scope must follow THAT logical day, so a habit due the actual
        # wall-clock day is future and stays out of today's plan.
        logical = date.today() - timedelta(days=1)
        entry = _habit_entry([
            {"id": "logical", "content": "Habit logical", "duration": "10m",
             "dueDate": logical.isoformat()},
            {"id": "wall", "content": "Habit wall", "duration": "10m",
             "dueDate": date.today().isoformat()},
        ])
        result = pr.compute_habit_summary(entry, logical_day=logical.isoformat())
        assert [i["id"] for i in result["items"]] == ["logical"]
        assert result["total"] == 1

    def test_regression_nine_today_and_five_future(self):
        tasks = [
            {"id": f"t{i}", "content": f"Habit T{i}", "duration": "10m",
             "dueDate": LOGICAL_DAY}
            for i in range(9)
        ] + [
            {"id": f"f{i}", "content": f"Habit F{i}", "duration": "6m",
             "dueDate": FUTURE}
            for i in range(5)
        ]
        result = pr.compute_habit_summary(
            _habit_entry(tasks), logical_day=LOGICAL_DAY
        )
        assert (result["total"], result["outstanding"]) == (9, 9)
        assert result["est_minutes"] == 90
        assert len(result["items"]) == 9

    def test_all_future_is_the_empty_sentinel(self):
        tasks = [
            {"id": f"f{i}", "content": f"Habit F{i}", "duration": "10m",
             "dueDate": FUTURE}
            for i in range(3)
        ]
        result = pr.compute_habit_summary(
            _habit_entry(tasks), logical_day=LOGICAL_DAY
        )
        assert result == art.HABITS_EMPTY
        assert "items" not in result


# ---------------------------------------------------------------------------
# Artifact seam — the block is exposed, absent means zeroed plus a warning
# ---------------------------------------------------------------------------

class TestArtifactHabits:
    def test_load_artifact_exposes_the_habit_block(self, tmp_path):
        target = _write_artifact(
            {"total": 3, "done": 1, "outstanding": 2, "est_minutes": 30},
            path=tmp_path / "a.json",
        )
        result = art.load_artifact(
            datetime.now().astimezone(), path=target,
            overlay=tmp_path / "no-overlay.json",
        )
        assert result.habits == {
            "total": 3, "done": 1, "outstanding": 2, "est_minutes": 30,
        }
        assert result.habit_warnings == []

    def test_artifact_without_a_habits_block_loads_zeroed_plus_warning(self, tmp_path):
        target = _write_artifact(None, path=tmp_path / "a.json")
        result = art.load_artifact(
            datetime.now().astimezone(), path=target,
            overlay=tmp_path / "no-overlay.json",
        )
        assert result.habits == art.HABITS_EMPTY
        assert result.habit_warnings
        assert "habits block" in result.habit_warnings[0]

    def test_load_habits_missing_artifact_degrades_loudly(self, tmp_path):
        block, warnings = art.load_habits(path=tmp_path / "nope.json")
        assert block == art.HABITS_EMPTY
        assert warnings

    def test_legacy_block_with_completion_data_stays_valid(self, tmp_path):
        """An artifact written before completion was dropped may still carry a
        ``done`` value, a completion list, and done-filtered ``items``. That is
        not a new contract violation and nothing of it is re-read: the block
        loads exactly as written, with no added warnings."""
        legacy = {
            "total": 5, "done": 2, "outstanding": 3, "est_minutes": 45,
            "completed": [
                {"task_id": "a", "content": "A",
                 "completedAt": "2026-07-26T10:00:00Z"},
                {"task_id": "b"},
            ],
            "items": [{"id": "c", "content": "C", "minutes": 15}],
        }
        target = _write_artifact(
            legacy, logical_day=art._today_effective_date(datetime.now()),
            path=tmp_path / "a.json",
        )
        document = json.loads(Path(target).read_text(encoding="utf-8"))
        assert art.validate_artifact(document) == []
        result = art.load_artifact(
            datetime.now().astimezone(), path=target,
            overlay=tmp_path / "no-overlay.json",
        )
        assert result.status == art.STATUS_FRESH
        assert result.habits == {
            "total": 5, "done": 2, "outstanding": 3, "est_minutes": 45,
            "items": [{"id": "c", "content": "C", "minutes": 15}],
        }
        assert result.habit_warnings == []

    def test_legacy_completion_rows_in_the_habits_source_stay_valid(self, tmp_path):
        """The habits SOURCE entry could carry ``tasks`` plus fetched
        ``completed`` rows. Those extra keys stay contract-legal and are never
        read back as habit state."""
        read_at = _now()
        sources = {
            "habits": {
                "status": "ok", "read_at": read_at, "rows": 0,
                "dropped": 0, "deferred": 0, "warnings": [],
                "tasks": [{"id": "a", "content": "A", "duration": "25m"}],
                "completed": [{"task_id": "a"}],
            }
        }
        document = {
            "schema": art.ARTIFACT_SCHEMA,
            "version": art.ARTIFACT_VERSION,
            "generated_at": read_at,
            "logical_day": art._today_effective_date(datetime.now()),
            "producer": {"name": "test", "version": "0.1.0", "run_id": "r1"},
            "content_hash": art.compute_content_hash(sources, []),
            "sources": sources,
            "rows": [],
            "admission": {"rule_set_hash": "rs", "admitted": [], "dropped": []},
            "habits": {"total": 1, "done": 1, "outstanding": 0,
                       "est_minutes": 0},
        }
        target = _write_document(document, path=tmp_path / "b.json")
        assert art.validate_artifact(
            json.loads(Path(target).read_text(encoding="utf-8"))
        ) == []
        result = art.load_artifact(
            datetime.now().astimezone(), path=target,
            overlay=tmp_path / "no-overlay.json",
        )
        assert result.status == art.STATUS_FRESH
        assert result.habits == {
            "total": 1, "done": 1, "outstanding": 0, "est_minutes": 0,
        }
        assert result.habit_warnings == []


# ---------------------------------------------------------------------------
# End to end — the allocator's habits segment tracks the artifact estimate
# ---------------------------------------------------------------------------

class TestCapacitySegment:
    def test_habits_segment_reflects_the_artifact_estimate(self, vault, monkeypatch):
        _write_artifact({"total": 3, "done": 0, "outstanding": 3, "est_minutes": 90})
        assert _body(monkeypatch)["capacity"]["habits"] == 3

    def test_ticking_one_off_frees_a_block(self, vault, monkeypatch):
        _write_artifact({"total": 3, "done": 0, "outstanding": 3, "est_minutes": 90})
        before = _body(monkeypatch)["capacity"]
        _write_artifact({"total": 3, "done": 1, "outstanding": 2, "est_minutes": 60})
        after = _body(monkeypatch)["capacity"]
        assert after["habits"] == before["habits"] - 1
        assert after["free"] > before["free"]

    def test_all_done_leaves_no_habit_reservation(self, vault, monkeypatch):
        _write_artifact({"total": 2, "done": 2, "outstanding": 0, "est_minutes": 0})
        assert _body(monkeypatch)["capacity"]["habits"] == 0

    def test_legend_reports_the_done_left_split(self, vault, monkeypatch):
        _write_artifact({"total": 2, "done": 1, "outstanding": 1, "est_minutes": 30})
        legend = _body(monkeypatch)["capacity"]["legend"]
        assert "habits: 1 done · 1 left" in legend

    def test_no_artifact_zeroes_habits_and_warns_loudly(self, vault, monkeypatch):
        body = _body(monkeypatch)
        assert body["habits"] == {
            "total": 0, "done": 0, "outstanding": 0, "est_minutes": 0,
        }
        assert "habit" in " ".join(body["source_warnings"]).lower()

    def test_digest_publishes_the_same_block_shape(self, vault, monkeypatch):
        _write_artifact({"total": 4, "done": 2, "outstanding": 2, "est_minutes": 30})
        body = _body(monkeypatch)
        assert set(body["habits"]) == {
            "total", "done", "outstanding", "est_minutes",
        }
        assert body["habits"]["est_minutes"] == 30

    def _built(self, tasks, **knobs) -> dict:
        """A producer-built artifact document whose only content is habits."""
        source = {
            "todoist": {"status": "ok", "read_at": _now(), "tasks": []},
            "habits": _habit_entry(tasks, **knobs),
        }
        return pr.build_artifact(
            source, {"version": 1, "rules": []},
            logical_day=LOGICAL_DAY, generated_at=_now(), run_id="habit-scope",
        )

    def test_due_dates_scope_the_built_block_and_capacity(self, vault, monkeypatch):
        tasks = [
            {"id": "t1", "content": "Habit T1", "duration": "25m",
             "dueDate": LOGICAL_DAY},
            {"id": "t2", "content": "Habit T2", "duration": "25m",
             "dueDate": LOGICAL_DAY},
            {"id": "t3", "content": "Habit T3", "duration": "25m",
             "dueDate": LOGICAL_DAY},
            {"id": "f1", "content": "Habit F1", "duration": "25m",
             "dueDate": FUTURE},
        ]
        document = self._built(tasks)
        assert document["habits"] == {
            "total": 3, "done": 0, "outstanding": 3, "est_minutes": 75,
            "items": [
                {"id": "t1", "content": "Habit T1", "minutes": 25},
                {"id": "t2", "content": "Habit T2", "minutes": 25},
                {"id": "t3", "content": "Habit T3", "minutes": 25},
            ],
        }
        _write_document(document)
        # 75 min = three 30-min blocks; the future task must not make it four.
        assert _body(monkeypatch)["capacity"]["habits"] == 3

    def test_all_future_built_block_zeroes_capacity(self, vault, monkeypatch):
        tasks = [
            {"id": "f1", "content": "Habit F1", "duration": "25m",
             "dueDate": FUTURE},
            {"id": "f2", "content": "Habit F2", "duration": "25m",
             "dueDate": FUTURE},
        ]
        document = self._built(tasks)
        assert document["habits"] == art.HABITS_EMPTY
        _write_document(document)
        assert _body(monkeypatch)["capacity"]["habits"] == 0
