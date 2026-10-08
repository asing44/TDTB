"""A3 — the paced read cursor (TDD gate).

The cursor is a COVERAGE cursor: it records what has been read so a partial
run resumes, and it cannot detect that a cached object changed (Capacities
exposes no guaranteed ``updatedAt``). These tests pin skip-already-read,
budget enforcement at 24 content reads / 60 seconds, partial reporting with a
deferred count, window rollover, and the CLI the skill invokes.
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import app_config  # noqa: E402
import producer_rules as pr  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
TOOLS = REPO_ROOT / "tools"

BASE = datetime.fromisoformat("2026-10-08T09:00:00-07:00")


def _listed(count: int, *, start: int = 0) -> list[dict]:
    return [{"id": f"obj-{i}", "structureId": "Project", "spaceId": "space-1"}
            for i in range(start, start + count)]


# ---------------------------------------------------------------------------
# Paths and IO
# ---------------------------------------------------------------------------

def test_producer_cache_path_lives_in_the_state_dir():
    assert pr.producer_cache_path() == app_config.state_dir() / "producer-cache.json"
    assert pr.PRODUCER_CACHE_FILENAME == "producer-cache.json"


def test_load_cache_is_total_for_missing_and_corrupt(tmp_path):
    missing = tmp_path / "nope.json"
    assert pr.load_cache(missing)["objects"] == {}

    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert pr.load_cache(bad)["objects"] == {}

    not_object = tmp_path / "list.json"
    not_object.write_text("[1, 2]", encoding="utf-8")
    assert pr.load_cache(not_object)["objects"] == {}


def test_record_reads_persists_and_round_trips(tmp_path):
    path = tmp_path / "producer-cache.json"
    recorded = pr.record_reads(_listed(3), path=path, now=BASE)
    assert recorded == 3
    assert path.is_file()

    cache = pr.load_cache(path)
    assert set(cache["objects"]) == {"obj-0", "obj-1", "obj-2"}
    assert cache["objects"]["obj-1"]["structure_id"] == "Project"
    assert cache["objects"]["obj-1"]["space_id"] == "space-1"


# ---------------------------------------------------------------------------
# Coverage semantics
# ---------------------------------------------------------------------------

def test_plan_skips_objects_the_cursor_already_covers(tmp_path):
    path = tmp_path / "producer-cache.json"
    pr.record_reads(_listed(2), path=path, now=BASE)

    plan = pr.plan_reads(_listed(5), path=path, now=BASE)
    assert plan.already_read == 2
    assert plan.need_read == ["obj-2", "obj-3", "obj-4"]
    assert plan.deferred == 0
    assert plan.status == "ok"
    assert plan.cache_entries == 2


def test_plan_preserves_listing_order_for_unread_objects(tmp_path):
    plan = pr.plan_reads(_listed(3, start=10), path=tmp_path / "c.json", now=BASE)
    assert plan.need_read == ["obj-10", "obj-11", "obj-12"]


# ---------------------------------------------------------------------------
# Budget enforcement
# ---------------------------------------------------------------------------

def test_budget_is_enforced_at_24_per_window(tmp_path):
    assert pr.DEFAULT_CONTENT_READ_BUDGET == 24
    assert pr.DEFAULT_CONTENT_READ_WINDOW_SECONDS == 60

    plan = pr.plan_reads(_listed(30), path=tmp_path / "c.json", now=BASE)
    assert len(plan.need_read) == 24
    assert plan.deferred == 6
    assert plan.status == "partial"
    assert plan.window_remaining == 24


def test_a_partial_run_reports_partial_with_a_deferred_count(tmp_path):
    path = tmp_path / "producer-cache.json"
    first = pr.plan_reads(_listed(30), path=path, now=BASE)
    assert first.status == "partial"
    assert first.deferred == 6

    pr.record_reads(
        [{"id": object_id, "structureId": "Project", "spaceId": "space-1"}
         for object_id in first.need_read],
        path=path, now=BASE,
    )
    second = pr.plan_reads(_listed(30), path=path, now=BASE)
    assert second.already_read == 24
    assert second.need_read == []
    assert second.status == "partial"
    assert second.deferred == 6


def test_partial_run_resumes_from_the_cursor(tmp_path):
    path = tmp_path / "producer-cache.json"
    first = pr.plan_reads(_listed(30), path=path, now=BASE)
    pr.record_reads(first.need_read, path=path, now=BASE)

    # A fresh window lets the deferred tail through.
    later = BASE + timedelta(seconds=61)
    second = pr.plan_reads(_listed(30), path=path, now=later)
    assert second.already_read == 24
    assert second.need_read == [f"obj-{i}" for i in range(24, 30)]
    assert second.deferred == 0
    assert second.status == "ok"

    pr.record_reads(second.need_read, path=path, now=later)
    third = pr.plan_reads(_listed(30), path=path, now=later + timedelta(seconds=61))
    assert third.need_read == []
    assert third.deferred == 0
    assert third.already_read == 30
    assert third.status == "ok"


def test_window_resets_after_60_seconds(tmp_path):
    path = tmp_path / "producer-cache.json"
    pr.record_reads(_listed(24), path=path, now=BASE)

    same_window = pr.plan_reads(_listed(30), path=path, now=BASE + timedelta(seconds=59))
    assert same_window.window_remaining == 0
    assert same_window.need_read == []

    next_window = pr.plan_reads(_listed(30), path=path, now=BASE + timedelta(seconds=60))
    assert next_window.window_remaining == 24
    assert next_window.need_read == [f"obj-{i}" for i in range(24, 30)]


def test_budget_is_configurable(tmp_path):
    plan = pr.plan_reads(_listed(10), path=tmp_path / "c.json", now=BASE, budget=4)
    assert len(plan.need_read) == 4
    assert plan.deferred == 6
    assert plan.status == "partial"


def test_plan_rejects_a_bad_budget(tmp_path):
    with pytest.raises(pr.CursorError):
        pr.plan_reads([], path=tmp_path / "c.json", now=BASE, budget=-1)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _run(args: list[str], payload, cache: Path):
    full = [sys.executable, str(TOOLS / "read_cursor.py"), *args, "--cache", str(cache)]
    return subprocess.run(
        full, input=json.dumps(payload), capture_output=True, text=True,
        cwd=str(REPO_ROOT),
    )


def test_cli_plan_and_record_round_trip(tmp_path):
    cache = tmp_path / "producer-cache.json"
    listed = {"objects": _listed(30)}

    planned = _run(["plan", "--input", "-"], listed, cache)
    assert planned.returncode == 0, planned.stderr
    plan = json.loads(planned.stdout)
    assert plan["status"] == "partial"
    assert plan["deferred"] == 6
    assert len(plan["need_read"]) == 24

    read = [{"id": object_id, "structureId": "Project", "spaceId": "space-1"}
            for object_id in plan["need_read"]]
    recorded = _run(["record", "--input", "-"], read, cache)
    assert recorded.returncode == 0, recorded.stderr
    assert json.loads(recorded.stdout)["recorded"] == 24

    status = subprocess.run(
        [sys.executable, str(TOOLS / "read_cursor.py"), "status", "--cache", str(cache)],
        capture_output=True, text=True, cwd=str(REPO_ROOT),
    )
    assert status.returncode == 0, status.stderr
    assert json.loads(status.stdout)["objects"] == 24

    again = _run(["plan", "--input", "-"], listed, cache)
    assert json.loads(again.stdout)["already_read"] == 24


def test_cli_rejects_non_json_input(tmp_path):
    cache = tmp_path / "producer-cache.json"
    result = subprocess.run(
        [sys.executable, str(TOOLS / "read_cursor.py"), "plan", "--input", "-",
         "--cache", str(cache)],
        input="not json", capture_output=True, text=True, cwd=str(REPO_ROOT),
    )
    assert result.returncode == 2
    assert "cannot read input" in result.stderr
