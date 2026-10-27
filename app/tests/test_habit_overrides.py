"""test_habit_overrides.py — Set up day per-habit rows + per-day overrides.

The planning artifact's optional ``habits.items`` list carries one entry per
habit (``{id, content, minutes}``) — completion is not read, so no habit is
filtered out as done. The dated Day Setup note carries an additive ``habits``
override block (per-item ``{on, minutes}`` plus an aggregate ``total``
override). Capacity computes the habits segment from the items when present,
honouring the overrides; when no items exist it falls back to the aggregate
``est_minutes`` (still honouring the ``total`` override).

Contract pinned here: additive schema, old payloads/notes unchanged, invalid
overrides rejected 422, dated isolation, full-replace semantics, and that a
day-setup save with habit overrides never enters a Todoist or Calendar write
path.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import artifact_source as art
import main as main_mod
import producer_rules as pr
import runstate as rs
from tests.config_markdown import write_config_note  # noqa: E402


CONFIG = """\
---
description: habit override test config
last_updated: 2026-10-15
---

# TDTB Bridger Config

## Defaults

| Key | Value    |
| --- | -------- |
| eod | 11:45 PM |
"""

TASK_A = {"id": "a", "content": "A", "duration": "25m"}
TASK_B = {"id": "b", "content": "B", "duration": "10m"}
TASK_C = {"id": "c", "content": "C", "duration": "5m"}


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _install_config() -> None:
    write_config_note(
        "/tmp/tdtb-bridger.md", CONFIG, encoding="utf-8"
    )


def _write_artifact(habits, *, logical_day: str | None = None,
                    path: str | Path | None = None) -> Path:
    """Write a minimal, valid artifact carrying ``habits`` (or none)."""
    if logical_day is None:
        logical_day = main_mod.gather.effective_date(datetime.now()).isoformat()
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
def client() -> TestClient:
    app = main_mod.create_app()
    c = TestClient(app)
    c.app_token = app.state.token
    return c


def _auth(client: TestClient) -> dict:
    return {"X-TDTB-Token": client.app_token}


def _today_state() -> dict:
    today = main_mod.gather.effective_date(datetime.now())
    return main_mod.gather._extract_json_block(
        rs.runstate_read_path(today).read_text(encoding="utf-8")
    ) or {}


def _body(client: TestClient) -> dict:
    r = client.get("/plan-inputs")
    assert r.status_code == 200, r.text
    return r.json()


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
# Producer — the optional outstanding ``items`` list
# ---------------------------------------------------------------------------

class TestProducerItems:
    def test_emits_items_with_duration_or_fallback(self):
        entry = _habit_entry([
            {"id": "a", "content": "A", "duration": "25m"},
            {"id": "b", "content": "B"},          # fallback 4
            {"id": "c", "content": "C", "duration": "0m"},  # zero -> fallback
        ])
        result = pr.compute_habit_summary(entry)
        assert result["items"] == [
            {"id": "a", "content": "A", "minutes": 25},
            {"id": "b", "content": "B", "minutes": 4},
            {"id": "c", "content": "C", "minutes": 4},
        ]

    def test_completed_items_are_still_emitted_by_id(self):
        entry = _habit_entry(
            [{"id": "a", "content": "A", "duration": "25m"},
             {"id": "b", "content": "B", "duration": "25m"}],
            completed=[{"task_id": "a"}],
        )
        assert pr.compute_habit_summary(entry)["items"] == [
            {"id": "a", "content": "A", "minutes": 25},
            {"id": "b", "content": "B", "minutes": 25},
        ]

    def test_completed_items_are_still_emitted_by_name(self):
        entry = _habit_entry(
            [{"id": "a", "content": "A", "duration": "25m"},
             {"id": "b", "content": "B", "duration": "25m"}],
            completed=[{"content": "A"}],
        )
        assert [i["id"] for i in pr.compute_habit_summary(entry)["items"]] == [
            "a", "b",
        ]

    def test_every_habit_completed_still_yields_items_and_minutes(self):
        entry = _habit_entry(
            [{"id": "a", "content": "A", "duration": "30m"}],
            completed=[{"id": "a"}],
        )
        assert pr.compute_habit_summary(entry) == {
            "total": 1, "done": 0, "outstanding": 1, "est_minutes": 30,
            "items": [{"id": "a", "content": "A", "minutes": 30}],
        }

    def test_missing_entry_has_no_items(self):
        assert pr.compute_habit_summary(None) == art.HABITS_EMPTY


# ---------------------------------------------------------------------------
# Artifact seam — ``items`` accepted/validated, absent stays valid
# ---------------------------------------------------------------------------

class TestArtifactItems:
    def test_load_habits_returns_items_when_present(self, tmp_path):
        block = {
            "total": 2, "done": 0, "outstanding": 2, "est_minutes": 45,
            "items": [
                {"id": "a", "content": "A", "minutes": 25},
                {"id": "b", "content": "B", "minutes": 20},
            ],
        }
        target = _write_artifact(block, path=tmp_path / "a.json")
        loaded, warnings = art.load_habits(path=target)
        assert loaded["items"] == block["items"]
        assert warnings == []

    def test_artifact_without_items_keeps_the_four_field_block(self, tmp_path):
        block = {"total": 3, "done": 1, "outstanding": 2, "est_minutes": 30}
        target = _write_artifact(block, path=tmp_path / "a.json")
        loaded, warnings = art.load_habits(path=target)
        assert loaded == block
        assert warnings == []

    def test_invalid_items_are_dropped_and_warned(self, tmp_path):
        block = {
            "total": 2, "done": 0, "outstanding": 2, "est_minutes": 45,
            "items": [
                {"id": "a", "minutes": 25},
                {"id": "bad", "minutes": 900},       # out of range
                {"id": "nonint", "minutes": "5"},    # not an int
                "not-an-object",
                {"id": "c", "minutes": 20, "extra": True},  # unknown key ok
            ],
        }
        target = _write_artifact(block, path=tmp_path / "a.json")
        loaded, warnings = art.load_habits(path=target)
        assert [i["id"] for i in loaded["items"]] == ["a", "c"]
        assert loaded["total"] == 2  # aggregate untouched
        assert any("habit" in w.lower() and "item" in w.lower() for w in warnings)

    def test_non_list_items_are_dropped(self, tmp_path):
        block = {"total": 1, "done": 0, "outstanding": 1, "est_minutes": 15,
                 "items": {"a": 25}}
        target = _write_artifact(block, path=tmp_path / "a.json")
        loaded, warnings = art.load_habits(path=target)
        assert "items" not in loaded
        assert warnings

    def test_load_artifact_exposes_items(self, tmp_path):
        block = {
            "total": 1, "done": 0, "outstanding": 1, "est_minutes": 30,
            "items": [{"id": "a", "content": "A", "minutes": 30}],
        }
        target = _write_artifact(block, path=tmp_path / "a.json")
        result = art.load_artifact(
            datetime.now().astimezone(), path=target,
            overlay=tmp_path / "no-overlay.json",
        )
        assert result.habits["items"] == block["items"]


# ---------------------------------------------------------------------------
# /plan-inputs — the artifact items ride the habits block additively
# ---------------------------------------------------------------------------

class TestPlanInputsItems:
    def test_plan_inputs_exposes_items(self, client):
        _install_config()
        _write_artifact({
            "total": 2, "done": 0, "outstanding": 2, "est_minutes": 45,
            "items": [{"id": "a", "content": "A", "minutes": 25},
                      {"id": "b", "content": "B", "minutes": 20}],
        })
        body = _body(client)
        assert body["habits"]["items"] == [
            {"id": "a", "content": "A", "minutes": 25},
            {"id": "b", "content": "B", "minutes": 20},
        ]

    def test_plan_inputs_omits_items_when_absent(self, client):
        _install_config()
        _write_artifact({"total": 2, "done": 0, "outstanding": 2,
                         "est_minutes": 45})
        assert "items" not in _body(client)["habits"]


# ---------------------------------------------------------------------------
# Day Setup — override round trip, validation, dated isolation, clearing
# ---------------------------------------------------------------------------

OVERRIDE = {
    "items": {
        "a": {"on": False},
        "b": {"on": True, "minutes": 60},
    },
    "total": {"on": True, "minutes": 120},
}


class TestDaySetupOverrides:
    def test_round_trips_response_state_and_plan_inputs(self, client):
        _install_config()
        r = client.post("/day-setup", json={"habits": OVERRIDE},
                        headers=_auth(client))
        assert r.status_code == 200, r.text
        assert r.json()["day_setup"]["habits"] == OVERRIDE
        assert _today_state()["habits"] == OVERRIDE
        assert _body(client)["day_setup"]["habits"] == OVERRIDE

    def test_omitted_habits_preserves_the_dated_override(self, client):
        _install_config()
        client.post("/day-setup", json={"habits": OVERRIDE},
                    headers=_auth(client))
        client.post("/day-setup", json={"anchor": "09:00"},
                    headers=_auth(client))
        assert _today_state()["habits"] == OVERRIDE

    def test_full_replace_and_explicit_null_clears(self, client):
        _install_config()
        client.post("/day-setup", json={"habits": OVERRIDE},
                    headers=_auth(client))
        replacement = {"items": {"a": {"on": True}}, "total": None}
        client.post("/day-setup", json={"habits": replacement},
                    headers=_auth(client))
        assert _today_state()["habits"] == replacement
        client.post("/day-setup", json={"habits": None}, headers=_auth(client))
        assert _today_state().get("habits") is None
        assert "habits" not in _body(client)["day_setup"]

    def test_dated_isolation_other_date_unaffected(self, client):
        _install_config()
        today = main_mod.gather.effective_date(datetime.now())
        from datetime import timedelta
        other = today + timedelta(days=1)
        rs.write_runstate(other, rs.build_runstate(
            {"habits": {"items": {"zzz": {"on": False}}}}
        ))
        r = client.post("/day-setup", json={"habits": OVERRIDE},
                        headers=_auth(client))
        assert r.status_code == 200, r.text
        assert _today_state()["habits"] == OVERRIDE
        other_state = main_mod.gather._extract_json_block(
            rs.runstate_read_path(other).read_text(encoding="utf-8")
        ) or {}
        assert other_state["habits"] == {"items": {"zzz": {"on": False}}}

    def test_old_payload_without_habits_stays_valid(self, client):
        _install_config()
        r = client.post("/day-setup", json={"anchor": "09:00"},
                        headers=_auth(client))
        assert r.status_code == 200, r.text
        assert "habits" not in _today_state()

    @pytest.mark.parametrize("bad", [
        {"items": {"a": {"minutes": -1}}},
        {"items": {"a": {"minutes": 481}}},
        {"items": {"a": {"minutes": "30"}}},
        {"items": {"a": {"on": "yes"}}},
        {"items": {"a": "skip"}},
        {"items": {"": {"on": False}}},
        {"items": {"x" * 300: {"on": False}}},
        {"items": []},
        {"total": {"minutes": 481}},
        {"total": {"on": 1}},
        "nope",
    ])
    def test_invalid_override_is_422(self, client, bad):
        _install_config()
        r = client.post("/day-setup", json={"habits": bad},
                        headers=_auth(client))
        assert r.status_code == 422, r.text

    def test_habits_only_post_confirms_the_day(self, client):
        _install_config()
        today = main_mod.gather.effective_date(datetime.now())
        assert rs.is_day_setup_confirmed(today) is False
        r = client.post("/day-setup", json={"habits": OVERRIDE},
                        headers=_auth(client))
        assert r.status_code == 200, r.text
        assert r.json()["day_setup_confirmed"] is True
        assert rs.is_day_setup_confirmed(today) is True

    def test_save_never_enters_a_todoist_or_calendar_write_path(
        self, client, monkeypatch
    ):
        _install_config()
        calls: list[str] = []

        def _spy(name):
            def _inner(*args, **kwargs):
                calls.append(name)
                raise AssertionError(f"{name} entered by /day-setup")
            return _inner

        monkeypatch.setattr(main_mod.commit, "write_todoist",
                            _spy("write_todoist"))
        monkeypatch.setattr(main_mod.commit, "write_calendar",
                            _spy("write_calendar"))
        r = client.post("/day-setup", json={"habits": OVERRIDE},
                        headers=_auth(client))
        assert r.status_code == 200, r.text
        assert calls == []


# ---------------------------------------------------------------------------
# Capacity — items drive the habits segment; overrides adjust or skip
# ---------------------------------------------------------------------------

ITEMS = [
    {"id": "a", "content": "A", "minutes": 25},
    {"id": "b", "content": "B", "minutes": 10},
    {"id": "c", "content": "C", "minutes": 5},
]
ITEMS_BLOCK = {"total": 3, "done": 0, "outstanding": 3,
               "est_minutes": 45, "items": ITEMS}


class TestCapacityFromItems:
    def test_items_sum_drives_the_habits_segment(self, client):
        _install_config()
        _write_artifact(ITEMS_BLOCK)
        # 25 + 10 + 5 = 40 min -> two 30-min blocks.
        assert _body(client)["capacity"]["habits"] == 2

    def test_skipping_an_item_frees_its_time(self, client):
        _install_config()
        _write_artifact(ITEMS_BLOCK)
        client.post("/day-setup",
                    json={"habits": {"items": {"a": {"on": False}}}},
                    headers=_auth(client))
        # 10 + 5 = 15 min -> one block.
        assert _body(client)["capacity"]["habits"] == 1

    def test_item_minute_override_is_honoured(self, client):
        _install_config()
        _write_artifact(ITEMS_BLOCK)
        client.post("/day-setup",
                    json={"habits": {"items": {"b": {"minutes": 60}}}},
                    headers=_auth(client))
        # 25 + 60 + 5 = 90 min -> three blocks.
        assert _body(client)["capacity"]["habits"] == 3

    def test_total_override_is_ignored_when_items_exist(self, client):
        _install_config()
        _write_artifact(ITEMS_BLOCK)
        client.post("/day-setup",
                    json={"habits": {"total": {"on": False}}},
                    headers=_auth(client))
        assert _body(client)["capacity"]["habits"] == 2

    def test_stale_override_is_ignored_but_round_trips(self, client):
        _install_config()
        _write_artifact(ITEMS_BLOCK)
        stale = {"items": {"nope": {"on": False}}, "total": None}
        client.post("/day-setup", json={"habits": stale}, headers=_auth(client))
        assert _body(client)["capacity"]["habits"] == 2
        assert _body(client)["day_setup"]["habits"] == stale

    def test_note_mentions_skipped_and_adjusted(self, client):
        _install_config()
        _write_artifact({**ITEMS_BLOCK, "done": 1, "outstanding": 2})
        client.post("/day-setup", json={"habits": {"items": {
            "a": {"on": False}, "b": {"minutes": 45},
        }}}, headers=_auth(client))
        legend = _body(client)["capacity"]["legend"]
        assert "habits: 1 done · 2 left" in legend
        assert "1 skipped" in legend
        assert "1 adjusted" in legend


class TestCapacityTotalOverrideFallback:
    AGG = {"total": 3, "done": 0, "outstanding": 3, "est_minutes": 90}

    def test_aggregate_est_minutes_when_no_items(self, client):
        _install_config()
        _write_artifact(self.AGG)
        assert _body(client)["capacity"]["habits"] == 3

    def test_total_override_off_zeroes_the_segment(self, client):
        _install_config()
        _write_artifact(self.AGG)
        client.post("/day-setup",
                    json={"habits": {"total": {"on": False}}},
                    headers=_auth(client))
        assert _body(client)["capacity"]["habits"] == 0

    def test_total_override_minutes_replaces_the_estimate(self, client):
        _install_config()
        _write_artifact(self.AGG)
        client.post("/day-setup",
                    json={"habits": {"total": {"minutes": 30}}},
                    headers=_auth(client))
        assert _body(client)["capacity"]["habits"] == 1

    def test_no_override_keeps_the_existing_note(self, client):
        _install_config()
        _write_artifact({**self.AGG, "done": 1, "outstanding": 2})
        legend = _body(client)["capacity"]["legend"]
        assert "habits: 1 done · 2 left" in legend
        assert "skipped" not in legend
        assert "adjusted" not in legend
