"""test_habits_live.py — live mode reads the habit list from Todoist itself.

The operator's Todoist habit project (``🔁 Habits``) IS the active habit set:
completion is tracked elsewhere, so every OPEN task in that project is one
habit and ``done`` is always 0. In ``sources.mode = live`` the app reads those
tasks BY PROJECT ID (``GET /tasks?project_id=…``), never through a filter
query: Todoist's filter grammar cannot express a project name carrying a space
or an emoji, and a query that matches nothing is indistinguishable from a day
without habits. ``sources.mode = artifact`` keeps the published artifact's
cached ``habits`` block.

Contract pinned here: the live block has the artifact's shape
(``{total, done, outstanding, est_minutes, items}``), minutes come from the
Todoist duration with the per-habit fallback, ``est_minutes`` rounds UP to the
producer grain, a failed live read falls back to the cached artifact list with
one loud content-free warning, a live zero falls back to the cached list when
that list still carries habits (never a silent zero), a zero from both is a
real zero (no warning), and the Day Setup per-habit overrides work on habits
that only the live read can see.
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))
import artifact_source as art  # noqa: E402
import external_sources as ext  # noqa: E402
import main as main_mod  # noqa: E402
from tests.config_markdown import write_config_note  # noqa: E402

TODAY = date(2026, 10, 28)

CONFIG = """\
---
description: live habits test config
last_updated: 2026-10-28
---

# TDTB Bridger Config

## Defaults

| Key | Value    |
| --- | ------- |
| eod | 11:45 PM |
"""

HABITS_PROJECT = {"id": "6hc482XrFwF3RJ6Q", "name": "\U0001F501 Habits"}
WORK_PROJECT = {"id": "p-work", "name": "Work"}

#: ``(id, content, minutes)`` — the 14 habits the operator keeps, mixed
#: durations plus one without a duration (the per-habit fallback applies).
#: ``6hhg5PRhH34WFhwQ`` is the stable Todoist id the artifact already carried
#: for TDTB, so per-habit Day Setup overrides keep matching.
HABITS: list[tuple[str, str, int | None]] = [
    ("6hhg5PRhH34WFhwQ", "TDTB", 5),
    ("h-litter", "LitterZz", 5),
    ("h-playtime", "Playtime", 10),
    ("h-kitty", "Pet a Stray Kitty", 5),
    ("h-cards", "Cards", 5),
    ("h-stretch", "Stretch and breathing", 25),
    ("h-journal", "Journal", 5),
    ("h-actual", "ActualBudget", 10),
    ("h-negviz", "Negative Visualization", 5),
    ("h-inbox", "Inbox Zero", 10),
    ("h-quick", "Quick Tasks", 5),
    ("h-messages", "Messages", 5),
    ("h-news", "News", 10),
    ("h-water", "Water", None),
]
#: 13 explicit durations (5+5+10+5+5+25+5+10+5+10+5+5+10 = 105) plus the 4-min
#: fallback for Water = 109 raw minutes, rounded UP to the 15-min grain = 120.
EXPECTED_RAW_MINUTES = 109
EXPECTED_EST_MINUTES = 120


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _habit_tasks() -> list[dict]:
    tasks: list[dict] = []
    for task_id, content, minutes in HABITS:
        task: dict = {"id": task_id, "content": content,
                      "project_id": HABITS_PROJECT["id"], "labels": []}
        if minutes is not None:
            task["duration"] = {"amount": minutes, "unit": "minute"}
        tasks.append(task)
    return tasks


class FakeHabitsTodoist:
    """Live Todoist double: project lookup plus ID-scoped task reads.

    ``get_project_tasks`` is the ONLY read the live habit adapter may use, so
    ``get_filter_tasks`` records every ROW query and fails loudly on a habit
    query — a filter string can never silently match nothing again.
    ``fail`` makes every provider read raise, which is the unreachable-Todoist
    case. ``tasks`` overrides the habit list (``[]`` is a valid live zero)."""

    def __init__(self, *, tasks=None, projects=(HABITS_PROJECT, WORK_PROJECT),
                 fail=False, projects_fail=False):
        self._tasks = _habit_tasks() if tasks is None else list(tasks)
        self._projects = list(projects)
        self._fail = fail
        self._projects_fail = projects_fail
        self.queries: list[str] = []
        self.project_reads: list[str] = []

    def get_projects(self):
        if self._projects_fail:
            raise RuntimeError("projects unavailable")
        return [dict(p) for p in self._projects]

    def get_project_tasks(self, project_id, limit=None):
        if self._fail:
            raise RuntimeError("todoist unreachable")
        self.project_reads.append(str(project_id))
        if str(project_id) != HABITS_PROJECT["id"]:
            return []
        return [dict(t) for t in self._tasks]

    def get_filter_tasks(self, query, limit=None):
        if "Habits" in query:
            raise AssertionError(f"habits read used a filter query: {query!r}")
        self.queries.append(query)
        return []


def _write_artifact(habits) -> None:
    """Write a minimal fresh artifact carrying ``habits`` (or none)."""
    rows: list[dict] = []
    read_at = _now()
    sources = {
        "todoist": {"status": "ok", "read_at": read_at, "rows": 0,
                    "dropped": 0, "deferred": 0, "warnings": []}
    }
    document: dict = {
        "schema": art.ARTIFACT_SCHEMA,
        "version": art.ARTIFACT_VERSION,
        "generated_at": read_at,
        "logical_day": TODAY.isoformat(),
        "producer": {"name": "test", "version": "0.1.0", "run_id": "r1"},
        "content_hash": art.compute_content_hash(sources, rows),
        "sources": sources,
        "rows": rows,
        "admission": {"rule_set_hash": "rs", "admitted": [], "dropped": []},
    }
    if habits is not None:
        document["habits"] = habits
    target = art.artifact_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(document), encoding="utf-8")


STALE_BLOCK = {
    "total": 5, "done": 0, "outstanding": 5, "est_minutes": 30,
    "items": [{"id": "6hhg5PRhH34WFhwQ", "content": "TDTB", "minutes": 5}],
}


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    write_config_note("/tmp/tdtb-bridger-live-habits.md", CONFIG, encoding="utf-8")
    monkeypatch.setattr(main_mod.gather, "effective_date", lambda _n: TODAY)
    app = main_mod.create_app()
    c = TestClient(app)
    c.app_token = app.state.token
    return c


def _inject(client: TestClient, todoist) -> None:
    client.app.state.build_read_clients = lambda cfg: (todoist, None)


def _body(client: TestClient) -> dict:
    response = client.get("/plan-inputs")
    assert response.status_code == 200, response.text
    return response.json()


def _names_minutes(block: dict) -> list[tuple[str, int]]:
    return [(i["content"], i["minutes"]) for i in block.get("items", [])]


# ---------------------------------------------------------------------------
# The external-source read itself
# ---------------------------------------------------------------------------

class TestFetchHabitsItems:
    def test_builds_the_artifact_shaped_block_from_open_tasks(self):
        block, warnings = ext.fetch_habits_items(FakeHabitsTodoist(), {}, TODAY)

        assert warnings == []
        assert block is not None
        assert block["total"] == 14
        assert block["done"] == 0
        assert block["outstanding"] == 14
        assert block["est_minutes"] == EXPECTED_EST_MINUTES
        assert len(block["items"]) == 14
        minutes = dict(_names_minutes(block))
        assert minutes["TDTB"] == 5
        assert minutes["Stretch and breathing"] == 25
        # A habit without a duration costs the per-habit fallback (4 min).
        assert minutes["Water"] == 4
        assert sum(minutes.values()) == EXPECTED_RAW_MINUTES
        # Stable Todoist task ids: the Day Setup override key.
        assert [i["id"] for i in block["items"]][0] == "6hhg5PRhH34WFhwQ"
        # The item shape is the artifact's contract, nothing more.
        assert set(block["items"][0]) == {"id", "content", "minutes"}

    def test_read_is_scoped_to_the_habits_project(self):
        foreign = {"id": "foreign", "content": "Ship it", "project_id": "p-work"}
        todoist = FakeHabitsTodoist(tasks=_habit_tasks() + [foreign])
        block, warnings = ext.fetch_habits_items(todoist, {}, TODAY)

        # Project ID is the only scope: no filter query is issued for habits.
        assert todoist.project_reads == [HABITS_PROJECT["id"]]
        assert todoist.queries == []
        assert warnings == []
        assert "Ship it" not in dict(_names_minutes(block))
        assert block["total"] == 14

    def test_a_read_that_matches_only_foreign_tasks_degrades(self):
        foreign = [{"id": "f1", "content": "Ship it", "project_id": "p-work"}]
        block, warnings = ext.fetch_habits_items(
            FakeHabitsTodoist(tasks=foreign), {}, TODAY
        )

        # Never a false zero: the scoped read is not doing its job.
        assert block is None
        assert warnings == [ext.HABITS_READ_FAILED_WARNING]

    def test_no_habits_project_is_a_read_failure(self):
        block, warnings = ext.fetch_habits_items(
            FakeHabitsTodoist(projects=(WORK_PROJECT,)), {}, TODAY
        )

        # Nothing to scope the read to: fall back to the labelled stale list.
        assert block is None
        assert warnings == [ext.HABITS_READ_FAILED_WARNING]

    def test_config_knobs_drive_the_estimate(self):
        block, _warnings = ext.fetch_habits_items(
            FakeHabitsTodoist(),
            {"habits.fallback_minutes_per_habit": 20,
             "habits.round_to_minutes": 60},
            TODAY,
        )

        # Water now costs 20 instead of 4: 125 raw minutes, one 60-min grain
        # step above = 180.
        assert dict(_names_minutes(block))["Water"] == 20
        assert block["est_minutes"] == 180

    def test_read_failure_degrades_to_none_plus_one_warning(self):
        block, warnings = ext.fetch_habits_items(
            FakeHabitsTodoist(fail=True), {}, TODAY
        )

        assert block is None
        assert warnings == [ext.HABITS_READ_FAILED_WARNING]
        # Content-free: no habit name, id, or provider text rides the warning.
        assert not any(name in warnings[0] for _i, name, _m in HABITS)

    def test_zero_live_habits_is_a_valid_zero_block(self):
        _write_artifact({"total": 0, "done": 0, "outstanding": 0, "est_minutes": 0})
        block, warnings = ext.fetch_habits_items(
            FakeHabitsTodoist(tasks=[]), {}, TODAY
        )

        # Nothing live AND nothing cached: a real zero, with no warning.
        assert warnings == []
        assert block == {"total": 0, "done": 0, "outstanding": 0,
                         "est_minutes": 0}

    def test_zero_live_habits_with_a_cached_artifact_falls_back(self):
        _write_artifact(STALE_BLOCK)
        block, warnings = ext.fetch_habits_items(
            FakeHabitsTodoist(tasks=[]), {}, TODAY
        )

        # A zero where the cached list still carries habits is a silent
        # failure, not an answer: fall back and label it distinctly.
        assert block is None
        assert warnings == [ext.HABITS_EMPTY_LIVE_WARNING]
        assert ext.HABITS_EMPTY_LIVE_WARNING != ext.HABITS_READ_FAILED_WARNING

    def test_a_day_unit_duration_uses_the_fallback_minutes(self):
        tasks = [{"id": "h-day", "content": "Read",
                  "project_id": HABITS_PROJECT["id"],
                  "duration": {"amount": 1, "unit": "day"}}]
        block, _warnings = ext.fetch_habits_items(
            FakeHabitsTodoist(tasks=tasks), {}, TODAY
        )

        # Never amount * 1440: a non-minute unit costs the fallback instead.
        assert dict(_names_minutes(block))["Read"] == 4
        assert block["est_minutes"] == 15

    def test_live_items_are_bounded_to_the_artifact_contract(self):
        tasks = [{"id": f"h{i}", "content": "x" * 300,
                  "project_id": HABITS_PROJECT["id"],
                  "duration": {"amount": 1440, "unit": "minute"}}
                 for i in range(250)]
        block, _warnings = ext.fetch_habits_items(
            FakeHabitsTodoist(tasks=tasks), {}, TODAY
        )

        assert block["total"] == art.HABIT_ITEMS_MAX
        assert len(block["items"]) == art.HABIT_ITEMS_MAX
        assert all(i["minutes"] == art.HABIT_MINUTES_MAX for i in block["items"])
        assert all(len(i["content"]) == art.HABIT_TEXT_MAX for i in block["items"])
        # The published block already satisfies the artifact's own validator.
        items, item_warnings = art._habit_items_of(block["items"])
        assert item_warnings == []
        assert len(items) == art.HABIT_ITEMS_MAX

    def test_real_habit_durations_produce_the_documented_totals(self):
        # The operator's live durations. NOTE: this list sums to 120, not the
        # 130 the review reported (130 rounds to 135 at the 15-min grain); the
        # arithmetic below is asserted as it actually is.
        durations = [5, 5, 5, 10, 5, 10, 10, 10, 5, 10, 25, 10, 5, 5]
        tasks = [{"id": f"h{i}", "content": f"Habit {i}",
                  "project_id": HABITS_PROJECT["id"],
                  "duration": {"amount": minutes, "unit": "minute"}}
                 for i, minutes in enumerate(durations)]
        block, warnings = ext.fetch_habits_items(
            FakeHabitsTodoist(tasks=tasks), {}, TODAY
        )

        assert warnings == []
        assert block["total"] == 14
        assert sum(i["minutes"] for i in block["items"]) == 120
        assert block["est_minutes"] == 120

    def test_no_client_is_not_an_error(self):
        assert ext.fetch_habits_items(None, {}, TODAY) == (None, [])


# ---------------------------------------------------------------------------
# /plan-inputs — which source wins in which mode
# ---------------------------------------------------------------------------

class TestPlanInputsHabitsSource:
    def test_live_mode_shows_all_fourteen_habits(self, client, live_sources_mode):
        _write_artifact(STALE_BLOCK)
        _inject(client, FakeHabitsTodoist())

        body = _body(client)

        assert body["habits"]["total"] == 14
        assert body["habits"]["est_minutes"] == EXPECTED_EST_MINUTES
        assert len(body["habits"]["items"]) == 14
        assert ext.HABITS_READ_FAILED_WARNING not in body["source_warnings"]

    def test_live_read_failure_falls_back_to_the_artifact_list(
        self, client, live_sources_mode
    ):
        _write_artifact(STALE_BLOCK)
        _inject(client, FakeHabitsTodoist(fail=True))

        body = _body(client)

        assert body["habits"]["total"] == 5
        assert ext.HABITS_READ_FAILED_WARNING in body["source_warnings"]

    def test_zero_live_habits_zeroes_the_block_without_a_warning(
        self, client, live_sources_mode
    ):
        _write_artifact({"total": 0, "done": 0, "outstanding": 0, "est_minutes": 0})
        _inject(client, FakeHabitsTodoist(tasks=[]))

        body = _body(client)

        assert body["habits"] == {"total": 0, "done": 0, "outstanding": 0,
                                  "est_minutes": 0}
        assert ext.HABITS_READ_FAILED_WARNING not in body["source_warnings"]
        assert ext.HABITS_EMPTY_LIVE_WARNING not in body["source_warnings"]

    def test_zero_live_habits_keeps_the_cached_list_and_labels_it(
        self, client, live_sources_mode
    ):
        _write_artifact(STALE_BLOCK)
        _inject(client, FakeHabitsTodoist(tasks=[]))

        body = _body(client)

        # A live zero against a non-empty cached list is reported, not shown
        # as a real zero.
        assert body["habits"]["total"] == 5
        assert ext.HABITS_EMPTY_LIVE_WARNING in body["source_warnings"]

    def test_no_todoist_client_labels_the_cached_list(
        self, client, live_sources_mode
    ):
        _write_artifact(STALE_BLOCK)
        client.app.state.build_read_clients = lambda cfg: (None, None)

        body = _body(client)

        # No live habit read at all: the stale list is still labelled.
        assert body["habits"]["total"] == 5
        assert ext.HABITS_READ_FAILED_WARNING in body["source_warnings"]

    def test_artifact_mode_keeps_the_artifact_list(self, client):
        _write_artifact(STALE_BLOCK)
        _inject(client, FakeHabitsTodoist())

        body = _body(client)

        assert body["habits"]["total"] == 5
        assert body["habits"]["est_minutes"] == 30

    def test_override_skips_a_habit_only_the_live_read_can_see(
        self, client, live_sources_mode
    ):
        _write_artifact(STALE_BLOCK)
        _inject(client, FakeHabitsTodoist())
        headers = {"X-TDTB-Token": client.app_token}
        assert _body(client)["capacity"]["habits"] == 4  # 109 min -> 4 blocks

        saved = client.post("/day-setup",
                            json={"habits": {"items": {"h-stretch": {"on": False}}}},
                            headers=headers)
        assert saved.status_code == 200, saved.text

        # 109 - 25 = 84 min -> three 30-min blocks. The habit stays in the
        # published list; the override only removes its capacity cost.
        body = _body(client)
        assert body["habits"]["total"] == 14
        assert body["capacity"]["habits"] == 3

    def test_minutes_override_replaces_a_live_habits_duration(
        self, client, live_sources_mode
    ):
        _write_artifact(STALE_BLOCK)
        _inject(client, FakeHabitsTodoist())
        headers = {"X-TDTB-Token": client.app_token}

        saved = client.post("/day-setup",
                            json={"habits": {"items": {"h-water": {"minutes": 60}}}},
                            headers=headers)
        assert saved.status_code == 200, saved.text

        # 105 + 60 = 165 min -> six 30-min blocks.
        assert _body(client)["capacity"]["habits"] == 6
