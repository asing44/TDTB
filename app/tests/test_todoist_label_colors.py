"""Todoist rows carry their labels' colours: the ``label_colors`` row key.

The cockpit tints a row's label chips, and Todoist already knows each label's
colour. Every live Todoist row therefore publishes one mapping from the label
names the row ALREADY carries to that label's Todoist colour NAME
(``berry_red``) — mapping the name to hex is the frontend's job, so the wire
carries the name or null.

Contract pinned here:

- ``label_colors`` keys are exactly the row's own ``labels`` entries (case
  preserved, exact match only);
- a known label maps to its colour name; an unknown one maps to null;
- a row with no labels, or a run with no label catalog, publishes ``{}``;
- the catalog is read ONCE per run (one paginated ``GET /labels``), shared by
  the habit read and the row read — never one call per row;
- an unavailable, failed, or malformed label lookup degrades to ``{}`` with NO
  new warning (planning is never blocked by it);
- a non-string colour normalises to null;
- the key reaches the ``/plan-inputs`` digest rows.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))
import external_sources as ext  # noqa: E402
import main as main_mod  # noqa: E402

TEN_MIN = {"id": "l-10", "name": "10min", "color": "red"}
HALF_HOUR = {"id": "l-30", "name": "Half-hour", "color": "yellow"}
HABITS_PROJECT = {"id": "p-habits", "name": "\U0001F501 Habits"}


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class _NoLabelLookup:
    """Todoist double with NO label lookup (an unconfigured runtime/double)."""

    def __init__(self, by_query=None):
        self.by_query = by_query or {}
        self.queries: list[str] = []

    def get_filter_tasks(self, query, limit=None):
        self.queries.append(query)
        return [dict(t) for t in self.by_query.get(query, [])]

    def get_project_tasks(self, project_id, limit=None):
        return []


class _CountingLabels(_NoLabelLookup):
    """Todoist double that COUNTS ``get_labels`` reads and can fail them."""

    def __init__(self, by_query=None, labels=(), raises=None):
        super().__init__(by_query)
        self._labels = list(labels)
        self._raises = raises
        self.label_reads = 0

    def get_labels(self):
        self.label_reads += 1
        if self._raises is not None:
            raise self._raises
        return [dict(label) for label in self._labels]


def _task(tid="1", content="Fix the sink", labels=("10min",)):
    return {
        "id": tid, "content": content, "priority": 2,
        "labels": list(labels), "project_id": "p-home",
    }


def _rows(tasks, labels=None):
    client = _CountingLabels(
        {ext.ASSIGNED_QUERY_FALLBACK: tasks}, labels=labels or ()
    )
    assigned, _pool, warnings = ext.fetch_todoist_items(client, {})
    return assigned, warnings


# ---------------------------------------------------------------------------
# The row itself
# ---------------------------------------------------------------------------

def test_to_item_without_a_catalog_gets_an_empty_mapping():
    row = ext._to_item(_task(), assigned=True)

    # The key is always present and honest; without a catalog nothing resolves.
    assert row["label_colors"] == {}


def test_row_carries_each_label_colour():
    assigned, warnings = _rows(
        [_task(labels=("10min", "Half-hour"))], labels=[TEN_MIN, HALF_HOUR]
    )

    assert warnings == []
    assert assigned[0]["labels"] == ["10min", "Half-hour"]
    assert assigned[0]["label_colors"] == {"10min": "red", "Half-hour": "yellow"}


def test_a_row_with_no_labels_gets_an_empty_mapping():
    assigned, _ = _rows([_task(labels=())], labels=[TEN_MIN])

    assert assigned[0]["labels"] == []
    assert assigned[0]["label_colors"] == {}


def test_label_names_match_exactly_and_keep_their_case():
    assigned, _ = _rows(
        [_task(labels=("10min", "half-hour", "HALF-HOUR"))], labels=[HALF_HOUR]
    )

    # Only the exact name resolves; the others are unknown, not guessed.
    assert assigned[0]["label_colors"] == {
        "10min": None, "half-hour": None, "HALF-HOUR": None,
    }


def test_unknown_label_maps_to_null():
    assigned, _ = _rows([_task(labels=("10min", "Someday"))], labels=[TEN_MIN])

    assert assigned[0]["label_colors"] == {"10min": "red", "Someday": None}


# ---------------------------------------------------------------------------
# Colour normalisation (Todoist sends the NAME; hex is the frontend's job)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw_color,expected", [
    ("berry_red", "berry_red"),
    ("blue", "blue"),
    (42, None),
    (None, None),
    (["red"], None),
    ("", None),
    ("   ", None),
    ({"name": "red"}, None),
])
def test_label_colour_normalises_to_a_string_or_null(raw_color, expected):
    label = {"id": "l-10", "name": "10min", "color": raw_color}
    assigned, _ = _rows([_task()], labels=[label])

    assert assigned[0]["label_colors"] == {"10min": expected}


def test_a_label_with_no_name_is_never_indexed():
    labels = [{"id": "l-x", "color": "red"}, {"id": "l-y", "name": 7, "color": "blue"}]
    assigned, _ = _rows([_task()], labels=labels)

    assert assigned[0]["label_colors"] == {"10min": None}


# ---------------------------------------------------------------------------
# One label read per run
# ---------------------------------------------------------------------------

def test_labels_are_read_once_per_run_across_the_habit_and_row_reads():
    """The habit read issues no label read; the row read issues exactly one."""
    habit_task = _task("2", "Messages")
    client = _CountingLabels(
        {ext.ASSIGNED_QUERY_FALLBACK: [_task()]}, labels=[TEN_MIN]
    )
    catalog = ext.TodoistLabels(client)

    ext.fetch_habits_items(client, {}, None)
    # Habits carry no label chips, so they never trigger the label catalog.
    assert client.label_reads == 0

    assigned, _pool, warnings = ext.fetch_todoist_items(client, {}, labels=catalog)

    assert warnings == []
    assert client.label_reads == 1
    assert [row["label_colors"] for row in assigned] == [{"10min": "red"}]


def test_a_new_run_reads_the_labels_again():
    """Nothing is persisted: a fresh catalog is a fresh (single) read."""
    client = _CountingLabels(
        {ext.ASSIGNED_QUERY_FALLBACK: [_task()]}, labels=[TEN_MIN]
    )

    ext.fetch_todoist_items(client, {}, labels=ext.TodoistLabels(client))
    ext.fetch_todoist_items(client, {}, labels=ext.TodoistLabels(client))

    assert client.label_reads == 2


def test_no_labelled_row_means_no_label_read():
    """The catalog is lazy: an unlabelled plan never pays for a label read."""
    client = _CountingLabels({ext.ASSIGNED_QUERY_FALLBACK: [_task(labels=())]})

    assigned, _pool, warnings = ext.fetch_todoist_items(client, {})

    assert warnings == []
    assert assigned[0]["label_colors"] == {}
    assert client.label_reads == 0


def test_a_catalog_less_fetch_reads_labels_once_not_once_per_row():
    client = _CountingLabels(
        {ext.ASSIGNED_QUERY_FALLBACK: [_task("1"), _task("2"), _task("3")]},
        labels=[TEN_MIN],
    )

    assigned, _pool, _warnings = ext.fetch_todoist_items(client, {})

    assert client.label_reads == 1
    assert [row["label_colors"] for row in assigned] == [{"10min": "red"}] * 3


# ---------------------------------------------------------------------------
# Degrade: no lookup, a failed lookup, a malformed response
# ---------------------------------------------------------------------------

def _without_label_colors(row: dict) -> dict:
    return {key: value for key, value in row.items() if key != "label_colors"}


def test_missing_label_lookup_leaves_rows_unchanged_apart_from_the_empty_map():
    control, _ = _rows([_task()], labels=[TEN_MIN])
    degraded_client = _NoLabelLookup({ext.ASSIGNED_QUERY_FALLBACK: [_task()]})

    assigned, _pool, warnings = ext.fetch_todoist_items(degraded_client, {})

    assert warnings == []
    row = assigned[0]
    assert row["label_colors"] == {}
    assert _without_label_colors(row) == _without_label_colors(control[0])


def test_failed_label_lookup_is_silent_and_empty():
    client = _CountingLabels(
        {ext.ASSIGNED_QUERY_FALLBACK: [_task()]},
        raises=RuntimeError("labels unavailable"),
    )

    assigned, pool, warnings = ext.fetch_todoist_items(client, {})

    assert warnings == [] and pool == []
    assert [row["name"] for row in assigned] == ["Fix the sink"]
    # The row's own labels survive untouched; only the colours are unknown.
    assert assigned[0]["labels"] == ["10min"]
    assert assigned[0]["label_colors"] == {}


def test_non_list_label_response_is_silent_and_empty():
    class _OddResponse(_NoLabelLookup):
        def get_labels(self):
            return {"id": "l-10", "name": "10min", "color": "red"}

    client = _OddResponse({ext.ASSIGNED_QUERY_FALLBACK: [_task()]})

    assigned, _pool, warnings = ext.fetch_todoist_items(client, {})

    assert warnings == []
    assert assigned[0]["label_colors"] == {}


# ---------------------------------------------------------------------------
# The wire: /plan-inputs digest rows
# ---------------------------------------------------------------------------

def test_plan_inputs_assigned_rows_carry_their_label_colours(live_sources_mode):
    client = _CountingLabels(
        {
            ext.ASSIGNED_QUERY_FALLBACK: [
                {"id": "1", "content": "Fix the sink", "priority": 4,
                 "due": {"date": "2026-07-14"}, "labels": ["10min", "Half-hour"],
                 "project_id": "p-home"},
            ],
            ext.QUICK_QUERY_FALLBACK: [
                {"id": "2", "content": "Water plants", "priority": 1,
                 "labels": ["Half-hour"], "project_id": "p-home"},
            ],
        },
        labels=[TEN_MIN, HALF_HOUR],
    )
    app = main_mod.create_app()
    app.state.build_read_clients = lambda cfg: (client, None)
    body = TestClient(app).get("/plan-inputs").json()

    assigned = next(r for r in body["digest"]["assigned"] if r["name"] == "Fix the sink")
    suggested = next(
        r for r in body["digest"]["suggested"] if r["name"] == "Water plants"
    )
    assert assigned["label_colors"] == {"10min": "red", "Half-hour": "yellow"}
    assert suggested["label_colors"] == {"Half-hour": "yellow"}
    # One label read for the whole request — never one per row.
    assert client.label_reads == 1


def test_plan_inputs_survives_an_unavailable_label_lookup(live_sources_mode):
    client = _NoLabelLookup({
        ext.ASSIGNED_QUERY_FALLBACK: [
            {"id": "1", "content": "Fix the sink", "priority": 4,
             "due": {"date": "2026-07-14"}, "labels": ["10min"],
             "project_id": "p-home"},
        ],
    })
    app = main_mod.create_app()
    app.state.build_read_clients = lambda cfg: (client, None)
    body = TestClient(app).get("/plan-inputs").json()

    row = next(r for r in body["digest"]["assigned"] if r["name"] == "Fix the sink")
    assert row["labels"] == ["10min"]
    assert row["label_colors"] == {}
