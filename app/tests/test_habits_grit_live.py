"""test_habits_grit_live.py — /plan-inputs reads habits from the GRIT CACHE.

U4 of the Grit-habits work. Grit owns the habit source: today's habit block is
built from the cached Grit read joined with the owner's saved per-habit minutes
and weekdays, and the completion verdict comes from the ONE classifier in
``grit_habits`` (never re-derived here). The capacity segment then charges only
the habits that still count (``done`` and ``failed`` stop counting; ``partial``
counts fully).

The failure ladder pinned here, in order:

1. a usable same-logical-day cache is the source; ``GRIT_READ_STALE`` is added
   only when a refresh attempt for today failed after that cache was stored, and
   ``GRIT_UNCONFIGURED`` when a due habit has no saved minutes;
2. no usable cache but a saved plan -> every configured due habit counts as not
   done, with ``GRIT_READ_FAILED``;
3. neither -> an EMPTY habit block with no warning. Unit U7 retired the
   Todoist habit read: no Todoist client method is called for habits and the
   archived Habits project is never consulted, so an unconfigured app plans the
   day with no habit load.

Absolute rules: a plan read NEVER runs the Shortcut. It serves the cache and
kicks at most ONE detached background refresh, so ``/plan-inputs`` returns
while an injected runner is still blocked. The autouse guard fails the run if
``subprocess.run`` is ever reached.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))

import grit_habits as gh  # noqa: E402
import habit_settings as hs  # noqa: E402
import main as main_mod  # noqa: E402

from tests.config_markdown import write_config_note  # noqa: E402

TODAY = date(2026, 10, 28)
#: Today's ``datetime.date.weekday()`` (Monday = 0), the numbering the saved
#: weekday lists use.
WEEKDAY = TODAY.weekday()
#: A weekday that is NOT today, for the filtering tests.
OTHER_WEEKDAY = (WEEKDAY + 1) % 7
LOGICAL_DAY = TODAY.isoformat()
GENERATED_AT = "2026-10-28T07:30:00-07:00"

GRIT_WARNINGS = (
    gh.GRIT_READ_STALE,
    gh.GRIT_READ_FAILED,
    gh.GRIT_UNCONFIGURED,
)

CONFIG = """\
---
description: grit habits live test config
last_updated: 2026-10-28
---

# TDTB Bridger Config

## Defaults

| Key | Value    |
| --- | ------- |
| eod | 11:45 PM |
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _payload(habits) -> dict:
    """A strict ``tdtb.grit.v1`` payload from ``[(name, progress, records)]``."""
    return {
        "schema": gh.SCHEMA,
        "generated_at": GENERATED_AT,
        "habits": [
            {
                "name": name,
                "progress": progress,
                "records": [
                    {"kind": kind, "amount": amount} for kind, amount in records
                ],
            }
            for name, progress, records in habits
        ],
    }


def _write_cache(habits, *, logical_day: str | None = None, age_s: float = 0.0) -> Path:
    """Store a Grit cache exactly as ``grit_habits`` would, optionally aged."""
    path = gh.cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    document = dict(_payload(habits))
    document["logical_day"] = LOGICAL_DAY if logical_day is None else logical_day
    path.write_text(json.dumps(document), encoding="utf-8")
    if age_s:
        past = time.time() - age_s
        os.utime(path, (past, past))
    return path


def _save(habits) -> None:
    """Save the plan the same way the settings route does."""
    hs.save_full_replace(habits, expected_revision=0)


class FakeRunner:
    """Injected Grit runner: never spawns a process, counts its calls.

    ``gate`` blocks the read until the test sets it, which is how a test proves
    a request did not wait on the runner and how two concurrent requests are
    held inside the single-flight window.
    """

    def __init__(self, *, payload=None, ok: bool = True,
                 gate: threading.Event | None = None):
        self.payload = payload
        self.ok = ok
        self.gate = gate
        self.started = threading.Event()
        self._lock = threading.Lock()
        self.calls: list[str] = []

    def __call__(self, name, output_path, timeout_s=None):
        with self._lock:
            self.calls.append(name)
        self.started.set()
        if self.gate is not None:
            self.gate.wait(timeout=10)
        if not self.ok:
            return gh.RunResult(ok=False, reason=gh.REASON_TIMEOUT)
        if self.payload is not None:
            Path(output_path).write_text(
                json.dumps(self.payload), encoding="utf-8"
            )
        return gh.RunResult(ok=True)


class SpyTodoist:
    """A Todoist double for ladder step 3 that FLAGS any habit read.

    U7 retired the Todoist habit read, so ``/plan-inputs`` must never call the
    one method that read implemented (``get_project_tasks``). Row reads still
    happen and answer empty.
    """

    PROJECT = {"id": "p-habits", "name": "\U0001F501 Habits"}

    def __init__(self):
        self.project_task_reads: list[str] = []

    def get_projects(self):
        return [dict(self.PROJECT)]

    def get_project_tasks(self, project_id, limit=None):
        self.project_task_reads.append(project_id)
        return [
            {"id": "h1", "content": "TDTB", "project_id": self.PROJECT["id"],
             "duration": {"amount": 5, "unit": "minute"}},
        ]

    def get_filter_tasks(self, query, limit=None):
        return []


@pytest.fixture(autouse=True)
def no_process(monkeypatch):
    """No test may spawn a process; the module's process-local records reset too."""
    attempts: list[tuple] = []

    def _guard(*args, **kwargs):
        attempts.append((args, kwargs))
        raise AssertionError("tests must never invoke subprocess.run")

    monkeypatch.setattr(gh.subprocess, "run", _guard)
    monkeypatch.setattr(gh, "_RECENT_SUCCESS", None)
    monkeypatch.setattr(gh, "_LAST_FAILURE", None)
    yield attempts
    assert attempts == [], "a test invoked subprocess.run for real"


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    write_config_note("/tmp/tdtb-bridger-grit-habits.md", CONFIG, encoding="utf-8")
    monkeypatch.setattr(main_mod.gather, "effective_date", lambda _n: TODAY)
    app = main_mod.create_app()
    c = TestClient(app)
    c.app_token = app.state.token
    return c


def _body(client: TestClient) -> dict:
    response = client.get("/plan-inputs")
    assert response.status_code == 200, response.text
    return response.json()


def _grit_warnings(body: dict) -> list[str]:
    return [w for w in body["source_warnings"] if w in GRIT_WARNINGS]


def _minutes(body: dict) -> dict[str, int]:
    return {i["content"]: i["minutes"] for i in body["habits"].get("items", [])}


def _wait_refresh_idle(client: TestClient, timeout: float = 10.0) -> None:
    """Wait for the detached refresh to clear its in-flight flag."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not getattr(client.app.state, "grit_refresh_inflight", False):
            return
        time.sleep(0.01)
    raise AssertionError("the background Grit refresh did not finish")


# ---------------------------------------------------------------------------
# Ladder step 1 — the cached Grit read is the source
# ---------------------------------------------------------------------------

class TestCachedGritReadIsTheSource:
    def test_done_and_failed_stop_counting_and_partial_counts_fully(self, client):
        _save([
            {"name": "Read", "minutes": 10},
            {"name": "Walk", "minutes": 20},
            {"name": "Run", "minutes": 30},
        ])
        _write_cache([
            ("Read", 1.0, []),                    # done by progress
            ("Walk", None, [("failed", None)]),   # failed also stops counting
            ("Run", 0.5, []),                     # partial counts fully
        ])
        runner = FakeRunner()
        client.app.state.grit_runner = runner

        body = _body(client)

        assert body["habits"]["total"] == 3
        assert body["habits"]["done"] == 2
        assert body["habits"]["outstanding"] == 1
        # Binary treatment: only Run still costs its full 30 minutes.
        assert body["habits"]["est_minutes"] == 30
        statuses = {i["content"]: i["status"] for i in body["habits"]["items"]}
        assert statuses == {
            "Read": gh.STATUS_DONE,
            "Walk": gh.STATUS_FAILED,
            "Run": gh.STATUS_PARTIAL,
        }
        assert body["capacity"]["habits"] == 1
        assert "habits: 3 due · 2 done" in body["capacity"]["legend"]
        assert _grit_warnings(body) == []
        # The cache was fresh: no refresh was needed, so none ran.
        assert runner.calls == []

    def test_every_item_carries_the_name_as_its_id(self, client):
        _save([{"name": "Read", "minutes": 10}])
        _write_cache([("Read", None, [])])

        item = _body(client)["habits"]["items"][0]

        # Grit has no TDTB id, so the name is the only identity an override
        # can match on.
        assert item == {
            "id": "Read",
            "content": "Read",
            "minutes": 10,
            "done": False,
            "status": gh.STATUS_NONE,
        }

    def test_a_cache_with_no_habits_is_a_real_zero(self, client):
        _save([{"name": "Read", "minutes": 10}])
        _write_cache([])

        body = _body(client)

        assert body["habits"] == {
            "total": 0, "done": 0, "outstanding": 0, "est_minutes": 0,
        }
        assert body["capacity"]["habits"] == 0
        # An empty read is an answer, not a failure to label.
        assert _grit_warnings(body) == []

    def test_capacity_preview_reads_the_same_grit_block(self, client):
        _save([
            {"name": "Read", "minutes": 10},
            {"name": "Run", "minutes": 30},
        ])
        _write_cache([("Read", 1.0, []), ("Run", 0.5, [])])
        client.app.state.build_read_clients = lambda cfg: (None, None)

        body = client.get("/capacity-preview").json()

        assert body["segments"]["habits"] == 1
        assert "habits: 2 due · 1 done" in body["legend"]


# ---------------------------------------------------------------------------
# Weekday filtering and unconfigured durations
# ---------------------------------------------------------------------------

class TestDueTodayAndMinutes:
    def test_a_habit_not_due_today_costs_nothing(self, client):
        _save([
            {"name": "Read", "minutes": 10},
            {"name": "Gym", "minutes": 40, "weekdays": [OTHER_WEEKDAY]},
        ])
        _write_cache([("Read", None, []), ("Gym", None, [])])

        body = _body(client)

        assert body["habits"]["total"] == 1
        assert _minutes(body) == {"Read": 10}
        # 10 min -> 1 block; the excluded 40 would have made it 2.
        assert body["capacity"]["habits"] == 1

    def test_a_habit_due_today_keeps_its_saved_weekdays(self, client):
        _save([{"name": "Gym", "minutes": 40, "weekdays": [WEEKDAY]}])
        _write_cache([("Gym", None, [])])

        body = _body(client)

        assert _minutes(body) == {"Gym": 40}
        assert body["capacity"]["habits"] == 1

    def test_an_unconfigured_habit_costs_the_grit_default(self, client):
        _save([{"name": "Read", "minutes": 10}])
        _write_cache([("Read", None, []), ("Meditate", None, [])])

        body = _body(client)

        # Present in Grit with no saved minutes: the Grit default, not the
        # legacy Todoist per-habit fallback (4).
        assert _minutes(body) == {"Read": 10, "Meditate": 15}
        assert gh.GRIT_UNCONFIGURED_MINUTES == 15
        assert body["habits"]["est_minutes"] == 25
        assert gh.GRIT_UNCONFIGURED in body["source_warnings"]

    def test_a_fully_configured_day_carries_no_unconfigured_warning(self, client):
        _save([{"name": "Read", "minutes": 10}])
        _write_cache([("Read", None, [])])

        assert gh.GRIT_UNCONFIGURED not in _body(client)["source_warnings"]


# ---------------------------------------------------------------------------
# Ladder step 1 with a failed refresh, and step 2
# ---------------------------------------------------------------------------

class TestStaleAndFailedLadder:
    def test_a_failed_refresh_labels_the_served_cache(self, client):
        _save([{"name": "Read", "minutes": 10}])
        _write_cache(
            [("Read", None, [])], age_s=gh.CACHE_MAX_AGE_S + 60
        )
        gate = threading.Event()
        runner = FakeRunner(ok=False, gate=gate)
        client.app.state.grit_runner = runner

        first = _body(client)

        # The read that kicked the refresh cannot know its verdict yet.
        assert gh.GRIT_READ_STALE not in first["source_warnings"]
        assert runner.started.wait(timeout=5), "an aged cache must ask for a refresh"
        gate.set()
        _wait_refresh_idle(client)

        second = _body(client)
        _wait_refresh_idle(client)

        # The cache is still served, now labelled with the failed attempt.
        assert gh.GRIT_READ_STALE in second["source_warnings"]
        assert gh.GRIT_READ_FAILED not in second["source_warnings"]
        assert second["habits"]["total"] == 1

    def test_no_cache_counts_every_saved_habit_as_not_done(self, client):
        _save([
            {"name": "Skip", "minutes": 5},
            {"name": "Jump", "minutes": 10},
        ])
        client.app.state.grit_runner = FakeRunner(ok=False)

        body = _body(client)
        _wait_refresh_idle(client)

        assert body["habits"]["total"] == 2
        assert body["habits"]["done"] == 0
        assert body["habits"]["outstanding"] == 2
        assert body["habits"]["est_minutes"] == 15
        assert all(i["done"] is False for i in body["habits"]["items"])
        assert all(
            i["status"] == gh.STATUS_NONE for i in body["habits"]["items"]
        )
        assert gh.GRIT_READ_FAILED in body["source_warnings"]

    def test_yesterdays_cache_is_never_served(self, client):
        _save([{"name": "Read", "minutes": 10}])
        _write_cache([("Read", 1.0, [])], logical_day="2026-10-27")
        client.app.state.grit_runner = FakeRunner(ok=False)

        body = _body(client)
        _wait_refresh_idle(client)

        # Yesterday's completion must not mark today's habit done.
        assert body["habits"]["outstanding"] == 1
        assert body["habits"]["est_minutes"] == 10
        assert gh.GRIT_READ_FAILED in body["source_warnings"]


# ---------------------------------------------------------------------------
# Ladder step 3 — Grit has nothing to say, and Todoist is never asked
# ---------------------------------------------------------------------------

class TestUnconfiguredHasNoHabitSource:
    def test_no_settings_and_no_cache_publish_an_empty_habit_block(
        self, client, live_sources_mode
    ):
        spy = SpyTodoist()
        client.app.state.build_read_clients = lambda cfg: (spy, None)

        body = _body(client)

        # Step 3 is the zeroed block: Grit has no settings and no cache, so the
        # day carries no habit load and no habit warning — never the archived
        # project's habits.
        assert body["habits"] == {
            "total": 0, "done": 0, "outstanding": 0, "est_minutes": 0,
        }
        assert "items" not in body["habits"]
        assert _grit_warnings(body) == []
        # Nothing is configured, so nothing asked the Shortcut to run.
        assert client.app.state.grit_refresh_inflight is False

    def test_no_todoist_method_reads_habits(
        self, client, live_sources_mode
    ):
        """U7: the row read still runs, the habit read cannot."""
        spy = SpyTodoist()
        client.app.state.build_read_clients = lambda cfg: (spy, None)

        body = _body(client)

        assert body["habits"]["total"] == 0
        assert spy.project_task_reads == []

    def test_an_empty_saved_plan_still_reads_grit_with_defaults(self, client):
        _save([])
        _write_cache([("Read", 1.0, [])])
        runner = FakeRunner()
        client.app.state.grit_runner = runner

        body = _body(client)

        # A cache exists, so Grit is the source even though the saved plan
        # names nothing: every Grit habit is simply unconfigured.
        assert body["habits"]["total"] == 1
        assert _minutes(body) == {"Read": gh.GRIT_UNCONFIGURED_MINUTES}
        assert body["habits"]["done"] == 1
        assert gh.GRIT_UNCONFIGURED in body["source_warnings"]
        # An unconfigured plan never asks the Shortcut to run.
        assert runner.calls == []
        assert client.app.state.grit_refresh_inflight is False


# ---------------------------------------------------------------------------
# Today's Day Setup override still applies to a Grit habit
# ---------------------------------------------------------------------------

class TestOverrides:
    def test_an_override_matches_a_grit_habit_by_name(self, client):
        _save([
            {"name": "Stretch", "minutes": 60},
            {"name": "Read", "minutes": 5},
        ])
        _write_cache([("Stretch", None, []), ("Read", None, [])])
        headers = {"X-TDTB-Token": client.app_token}
        assert _body(client)["capacity"]["habits"] == 2  # 65 min -> 2 blocks

        saved = client.post(
            "/day-setup",
            json={"habits": {"items": {"Stretch": {"on": False}}}},
            headers=headers,
        )
        assert saved.status_code == 200, saved.text

        body = _body(client)

        # The habit stays in the published list; only its cost is removed.
        assert body["habits"]["total"] == 2
        assert body["capacity"]["habits"] == 1


# ---------------------------------------------------------------------------
# The plan read never runs the Shortcut
# ---------------------------------------------------------------------------

class TestNeverBlocks:
    def test_plan_inputs_returns_while_the_runner_is_blocked(self, client):
        _save([{"name": "Read", "minutes": 10}])
        gate = threading.Event()
        runner = FakeRunner(gate=gate)
        client.app.state.grit_runner = runner

        started = time.monotonic()
        body = _body(client)
        elapsed = time.monotonic() - started

        try:
            assert elapsed < 5.0
            assert runner.started.wait(timeout=5), "a refresh should have started"
            # The read is still inside the runner, so the response cannot
            # have come from it: step 2 planned the day pessimistically.
            assert client.app.state.grit_refresh_inflight is True
            assert body["habits"]["total"] == 1
            assert gh.GRIT_READ_FAILED in body["source_warnings"]
        finally:
            gate.set()
            _wait_refresh_idle(client)

    def test_concurrent_requests_share_one_background_refresh(self, client):
        _save([{"name": "Read", "minutes": 10}])
        gate = threading.Event()
        runner = FakeRunner(gate=gate)
        client.app.state.grit_runner = runner

        statuses: list[int] = []
        lock = threading.Lock()

        def attempt() -> None:
            response = client.get("/plan-inputs")
            with lock:
                statuses.append(response.status_code)

        threads = [threading.Thread(target=attempt) for _ in range(3)]
        try:
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=10)
            assert runner.started.wait(timeout=5)
            assert len(runner.calls) == 1, runner.calls
        finally:
            gate.set()
            _wait_refresh_idle(client)

        assert statuses == [200, 200, 200]
