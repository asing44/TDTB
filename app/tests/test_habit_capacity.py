"""test_habit_capacity.py — habit time from the artifact, never the vault.

Obsidian is retired: the habit done/total split and the outstanding-minutes
estimate ride the planning artifact's top-level ``habits`` block, computed by
the producer (``producer_rules.compute_habit_summary``). This file pins the
producer arithmetic (grain rounding, per-habit fallback, completed-today) and
the end-to-end capacity effect through /plan-inputs.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import artifact_source as art
import main
import producer_rules as pr


TODAY = date(2026, 7, 26)
LOGICAL_DAY = TODAY.isoformat()

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


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault-root"
    (root / "00 - META" / "Skill-Configs").mkdir(parents=True)
    (root / "00 - META" / "Skill-Configs" / "tdtb-bridger.md").write_text(
        CONFIG, encoding="utf-8")
    return root


def _client(vault: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(main.gather, "effective_date", lambda _n: TODAY)
    return TestClient(main.create_app(vault_root=vault))


def _body(vault: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    response = _client(vault, monkeypatch).get("/plan-inputs")
    assert response.status_code == 200, response.text
    return response.json()


def _habit_entry(tasks, completed=(), **knobs) -> dict:
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
        assert pr.compute_habit_summary(entry) == {
            "total": 3, "done": 0, "outstanding": 3, "est_minutes": 45,
        }

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

    def test_completed_today_is_done_and_excluded_from_minutes(self):
        entry = _habit_entry(
            [{"id": "a", "content": "A", "duration": "25m"},
             {"id": "b", "content": "B", "duration": "25m"}],
            completed=[{"task_id": "a"}],
        )
        assert pr.compute_habit_summary(entry) == {
            "total": 2, "done": 1, "outstanding": 1, "est_minutes": 30,
        }

    def test_a_completion_from_another_day_is_not_done_today(self):
        """``find-completed-tasks`` defaults to a WEEK-long window, so the fetch
        cannot scope "done today" — the producer must. A habit finished earlier
        in the week must not read as done."""
        entry = _habit_entry(
            [{"id": "a", "content": "A", "duration": "25m"},
             {"id": "b", "content": "B", "duration": "25m"}],
            completed=[{"task_id": "a",
                        "completedAt": _local_stamp("2026-10-06")}],
        )
        # 25 + 25 = 50 min -> 60 at the 15-min grain; nothing counts as done.
        assert pr.compute_habit_summary(entry, logical_day="2026-10-08") == {
            "total": 2, "done": 0, "outstanding": 2, "est_minutes": 60,
        }

    def test_a_completion_on_the_logical_day_is_done(self):
        entry = _habit_entry(
            [{"id": "a", "content": "A", "duration": "25m"},
             {"id": "b", "content": "B", "duration": "25m"}],
            completed=[{"task_id": "a",
                        "completedAt": _local_stamp("2026-10-08")}],
        )
        assert pr.compute_habit_summary(entry, logical_day="2026-10-08") == {
            "total": 2, "done": 1, "outstanding": 1, "est_minutes": 30,
        }

    def test_an_undated_completion_still_counts(self):
        """The fetch window is the only guard for an undated completion."""
        entry = _habit_entry(
            [{"id": "a", "content": "A", "duration": "25m"}],
            completed=[{"task_id": "a"}],
        )
        assert pr.compute_habit_summary(entry, logical_day="2026-10-08")["done"] == 1

    def test_all_done_reserves_nothing(self):
        entry = _habit_entry(
            [{"id": "a", "content": "A", "duration": "30m"}],
            completed=[{"id": "a"}],
        )
        assert pr.compute_habit_summary(entry) == {
            "total": 1, "done": 1, "outstanding": 0, "est_minutes": 0,
        }

    def test_missing_entry_is_the_zeroed_block(self):
        assert pr.compute_habit_summary(None) == art.HABITS_EMPTY


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


# ---------------------------------------------------------------------------
# End to end — the allocator's habits segment tracks the artifact estimate
# ---------------------------------------------------------------------------

class TestCapacitySegment:
    def test_habits_segment_reflects_the_artifact_estimate(self, vault, monkeypatch):
        _write_artifact({"total": 3, "done": 0, "outstanding": 3, "est_minutes": 90})
        assert _body(vault, monkeypatch)["capacity"]["habits"] == 3

    def test_ticking_one_off_frees_a_block(self, vault, monkeypatch):
        _write_artifact({"total": 3, "done": 0, "outstanding": 3, "est_minutes": 90})
        before = _body(vault, monkeypatch)["capacity"]
        _write_artifact({"total": 3, "done": 1, "outstanding": 2, "est_minutes": 60})
        after = _body(vault, monkeypatch)["capacity"]
        assert after["habits"] == before["habits"] - 1
        assert after["free"] > before["free"]

    def test_all_done_leaves_no_habit_reservation(self, vault, monkeypatch):
        _write_artifact({"total": 2, "done": 2, "outstanding": 0, "est_minutes": 0})
        assert _body(vault, monkeypatch)["capacity"]["habits"] == 0

    def test_legend_reports_the_done_left_split(self, vault, monkeypatch):
        _write_artifact({"total": 2, "done": 1, "outstanding": 1, "est_minutes": 30})
        legend = _body(vault, monkeypatch)["capacity"]["legend"]
        assert "habits: 1 done · 1 left" in legend

    def test_no_artifact_zeroes_habits_and_warns_loudly(self, vault, monkeypatch):
        body = _body(vault, monkeypatch)
        assert body["habits"] == {
            "total": 0, "done": 0, "outstanding": 0, "est_minutes": 0,
        }
        assert "habit" in " ".join(body["source_warnings"]).lower()

    def test_digest_publishes_the_same_block_shape(self, vault, monkeypatch):
        _write_artifact({"total": 4, "done": 2, "outstanding": 2, "est_minutes": 30})
        body = _body(vault, monkeypatch)
        assert set(body["habits"]) == {
            "total", "done", "outstanding", "est_minutes",
        }
        assert body["habits"]["est_minutes"] == 30
