"""Todoist rows carry their project: ``project_id``/``project_name``/``project_color``.

The cockpit needs to show which Todoist project a row belongs to (bulk-apply a
duration to everything in "Home"), so every live Todoist row publishes its
project's id, display name, and Todoist colour NAME (``sky_blue``). Mapping the
colour name to hex is the frontend's job — the wire carries the name or null.

Contract pinned here:

- ``_to_item`` keeps ``project_id`` and resolves the name/colour through a
  project catalog;
- the catalog is read ONCE per run (one paginated ``GET /projects``), shared by
  the habit scope read and the row read — never one call per row;
- an unavailable, failed, or malformed project lookup degrades to null
  name/colour with NO new warning (planning is never blocked by it);
- a non-string colour (or an MCP-shaped nested one) normalises to a string or
  null;
- the fields reach the ``/plan-inputs`` digest rows.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))
import external_sources as ext  # noqa: E402
import main as main_mod  # noqa: E402

PROJECT_KEYS = ("project_id", "project_name", "project_color")

HOME_PROJECT = {"id": "p-home", "name": "Home", "color": "sky_blue"}
HABITS_PROJECT = {"id": "p-habits", "name": "\U0001F501 Habits", "color": "berry_red"}


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class _NoProjectLookup:
    """Todoist double with NO project lookup (an unconfigured runtime/double)."""

    def __init__(self, by_query=None):
        self.by_query = by_query or {}
        self.queries: list[str] = []

    def get_filter_tasks(self, query, limit=None):
        self.queries.append(query)
        return [dict(t) for t in self.by_query.get(query, [])]

    def get_project_tasks(self, project_id, limit=None):
        return []


class _CountingProjects(_NoProjectLookup):
    """Todoist double that COUNTS ``get_projects`` reads and can fail them."""

    def __init__(self, by_query=None, projects=(), raises=None):
        super().__init__(by_query)
        self._projects = list(projects)
        self._raises = raises
        self.project_reads = 0

    def get_projects(self):
        self.project_reads += 1
        if self._raises is not None:
            raise self._raises
        return [dict(p) for p in self._projects]


def _task(tid="1", content="Fix the sink", project_id="p-home", priority=2):
    task = {"id": tid, "content": content, "priority": priority, "labels": []}
    if project_id is not None:
        task["project_id"] = project_id
    return task


def _rows(tasks, projects=None):
    client = _CountingProjects(
        {ext.ASSIGNED_QUERY_FALLBACK: tasks}, projects=projects or ()
    )
    assigned, _pool, warnings = ext.fetch_todoist_items(client, {})
    return assigned, warnings


# ---------------------------------------------------------------------------
# The row itself
# ---------------------------------------------------------------------------

def test_to_item_keeps_the_project_id():
    row = ext._to_item(_task(), assigned=True)

    assert row["project_id"] == "p-home"
    # Unresolved without a catalog: present and null, never absent.
    assert row["project_name"] is None
    assert row["project_color"] is None


def test_row_carries_project_name_and_color():
    assigned, warnings = _rows([_task()], projects=[HOME_PROJECT])

    assert warnings == []
    row = assigned[0]
    assert row["project_id"] == "p-home"
    assert row["project_name"] == "Home"
    assert row["project_color"] == "sky_blue"


def test_task_without_a_project_keeps_null_fields():
    assigned, _ = _rows([_task(project_id=None)], projects=[HOME_PROJECT])

    row = assigned[0]
    assert row["project_id"] is None
    assert row["project_name"] is None
    assert row["project_color"] is None


def test_unknown_project_id_keeps_null_name_and_color():
    assigned, _ = _rows([_task(project_id="p-gone")], projects=[HOME_PROJECT])

    row = assigned[0]
    assert row["project_id"] == "p-gone"
    assert row["project_name"] is None
    assert row["project_color"] is None


# ---------------------------------------------------------------------------
# Colour normalisation (Todoist sends the NAME; hex is the frontend's job)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw_color,expected", [
    ("berry_red", "berry_red"),
    ("sky_blue", "sky_blue"),
    (42, None),
    (None, None),
    (["sky_blue"], None),
    ("", None),
    ("   ", None),
    # MCP-shaped projects may nest the colour; only a usable string survives.
    ({"name": "berry_red"}, "berry_red"),
    ({"hex": "#14aaf5"}, None),
    ({"name": 7}, None),
])
def test_project_color_normalises_to_a_string_or_null(raw_color, expected):
    project = {"id": "p-home", "name": "Home", "color": raw_color}
    assigned, _ = _rows([_task()], projects=[project])

    assert assigned[0]["project_color"] == expected


def test_non_string_project_name_becomes_null():
    project = {"id": "p-home", "name": 42, "color": "sky_blue"}
    assigned, _ = _rows([_task()], projects=[project])

    assert assigned[0]["project_name"] is None
    assert assigned[0]["project_color"] == "sky_blue"


# ---------------------------------------------------------------------------
# One project read per run
# ---------------------------------------------------------------------------

def test_projects_are_read_once_per_run_across_the_habit_and_row_reads():
    """The habit scope read and the row read share ONE ``GET /projects``."""
    habit_task = _task("2", "Messages", project_id=HABITS_PROJECT["id"])
    client = _CountingProjects(
        {ext.ASSIGNED_QUERY_FALLBACK: [_task(), habit_task]},
        projects=[HABITS_PROJECT, HOME_PROJECT],
    )
    catalog = ext.TodoistProjects(client)

    ext.fetch_habits_items(client, {}, None, projects=catalog)
    assert client.project_reads == 1

    assigned, _pool, warnings = ext.fetch_todoist_items(client, {}, projects=catalog)

    assert warnings == []
    assert client.project_reads == 1
    assert assigned[0]["project_name"] == "Home"
    # The cached project list also answered the habit scope: the habit
    # project's task is still filtered out of the plan.
    assert [row["name"] for row in assigned] == ["Fix the sink"]


def test_a_new_run_reads_the_projects_again():
    """Nothing is persisted: a fresh catalog is a fresh (single) read."""
    client = _CountingProjects({ext.ASSIGNED_QUERY_FALLBACK: []}, projects=[HOME_PROJECT])

    ext.fetch_todoist_items(client, {}, projects=ext.TodoistProjects(client))
    ext.fetch_todoist_items(client, {}, projects=ext.TodoistProjects(client))

    assert client.project_reads == 2


def test_a_catalog_less_fetch_reads_projects_once_not_once_per_row():
    client = _CountingProjects(
        {ext.ASSIGNED_QUERY_FALLBACK: [_task("1"), _task("2"), _task("3")]},
        projects=[HOME_PROJECT],
    )

    assigned, _pool, _warnings = ext.fetch_todoist_items(client, {})

    assert client.project_reads == 1
    assert [row["project_name"] for row in assigned] == ["Home"] * 3


# ---------------------------------------------------------------------------
# Degrade: no lookup, a failed lookup, a malformed response
# ---------------------------------------------------------------------------

def _without_project_fields(row: dict) -> dict:
    return {key: value for key, value in row.items() if key not in PROJECT_KEYS}


def test_missing_project_lookup_leaves_rows_unchanged_apart_from_nulls():
    control, _ = _rows([_task()], projects=[HOME_PROJECT])
    degraded_client = _NoProjectLookup({ext.ASSIGNED_QUERY_FALLBACK: [_task()]})

    assigned, _pool, warnings = ext.fetch_todoist_items(degraded_client, {})

    assert warnings == []
    row = assigned[0]
    # The id needs no lookup, so it survives; the name/colour cannot resolve.
    assert row["project_id"] == "p-home"
    assert row["project_name"] is None and row["project_color"] is None
    assert _without_project_fields(row) == _without_project_fields(control[0])


def test_failed_project_lookup_is_silent_and_null():
    client = _CountingProjects(
        {ext.ASSIGNED_QUERY_FALLBACK: [_task()]},
        raises=RuntimeError("projects unavailable"),
    )

    assigned, pool, warnings = ext.fetch_todoist_items(client, {})

    assert warnings == [] and pool == []
    assert [row["name"] for row in assigned] == ["Fix the sink"]
    assert assigned[0]["project_id"] == "p-home"
    assert assigned[0]["project_name"] is None
    assert assigned[0]["project_color"] is None


def test_non_list_project_response_is_silent_and_null():
    class _OddResponse(_NoProjectLookup):
        def get_projects(self):
            return {"id": "p-home", "name": "Home"}

    client = _OddResponse({ext.ASSIGNED_QUERY_FALLBACK: [_task()]})

    assigned, _pool, warnings = ext.fetch_todoist_items(client, {})

    assert warnings == []
    assert assigned[0]["project_name"] is None


# ---------------------------------------------------------------------------
# The wire: /plan-inputs digest rows
# ---------------------------------------------------------------------------

def test_plan_inputs_assigned_rows_carry_their_project(live_sources_mode):
    client = _CountingProjects(
        {
            ext.ASSIGNED_QUERY_FALLBACK: [
                {"id": "1", "content": "Fix the sink", "priority": 4,
                 "due": {"date": "2026-07-14"}, "labels": [],
                 "project_id": "p-home"},
            ],
            ext.QUICK_QUERY_FALLBACK: [
                {"id": "2", "content": "Water plants", "priority": 1,
                 "labels": [], "project_id": "p-home"},
            ],
        },
        projects=[HABITS_PROJECT, HOME_PROJECT],
    )
    app = main_mod.create_app()
    app.state.build_read_clients = lambda cfg: (client, None)
    body = TestClient(app).get("/plan-inputs").json()

    assigned = next(r for r in body["digest"]["assigned"] if r["name"] == "Fix the sink")
    suggested = next(
        r for r in body["digest"]["suggested"] if r["name"] == "Water plants"
    )
    for row in (assigned, suggested):
        assert row["project_id"] == "p-home"
        assert row["project_name"] == "Home"
        assert row["project_color"] == "sky_blue"
    # habit read + row read share ONE project read for the whole request.
    assert client.project_reads == 1


def test_plan_inputs_survives_an_unavailable_project_lookup(live_sources_mode):
    client = _NoProjectLookup({
        ext.ASSIGNED_QUERY_FALLBACK: [
            {"id": "1", "content": "Fix the sink", "priority": 4,
             "due": {"date": "2026-07-14"}, "labels": [], "project_id": "p-home"},
        ],
    })
    app = main_mod.create_app()
    app.state.build_read_clients = lambda cfg: (client, None)
    body = TestClient(app).get("/plan-inputs").json()

    row = next(r for r in body["digest"]["assigned"] if r["name"] == "Fix the sink")
    assert row["project_id"] == "p-home"
    assert row["project_name"] is None and row["project_color"] is None
