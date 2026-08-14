"""test_habit_capacity.py — T4 (allocator rewrite): dynamic habit accounting.

Locked decision 9: remaining habit time excludes habits already done today.
``external_sources.fetch_habit_status`` already splits done/outstanding and
sums only outstanding durations, and ``_capacity_frame`` reserves capacity
from that estimate — so this task is a PINNING test, not a change.

test_external_sources covers the source-level split. What was uncovered, and
what these tests pin, is the end-to-end effect: ticking a habit off today
must shrink the ``habits`` segment the allocator budgets against, all the way
through /plan-inputs.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import external_sources as ext
import main


TODAY = date(2026, 7, 26)
HABITS_REL = "00 - META/Habituals"

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


def _habit(vault: Path, name: str, entries: list[str], duration: int) -> None:
    lines = [f"  - {e}" for e in entries] or []
    body = "\n".join(
        ["---", f"title: {name}", "type: habit", f"duration: {duration}", "entries:"]
        + (lines if lines else ["  []"])
        + ["---", "", f"# {name}", ""]
    )
    (vault / HABITS_REL / f"{name}.md").write_text(body, encoding="utf-8")


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault-root"
    (root / HABITS_REL).mkdir(parents=True)
    (root / "00 - META" / "Skill-Configs").mkdir(parents=True, exist_ok=True)
    (root / "00 - META" / "Skill-Configs" / "tdtb-bridger.md").write_text(
        CONFIG, encoding="utf-8")
    return root


def _client(vault: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(main.gather, "effective_date", lambda _n: TODAY)
    return TestClient(main.create_app(vault_root=vault))


def _capacity(vault: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    r = _client(vault, monkeypatch).get("/plan-inputs")
    assert r.status_code == 200, r.text
    return r.json()["capacity"]


# ---------------------------------------------------------------------------
# Source level — the estimate excludes today's completions
# ---------------------------------------------------------------------------

class TestOutstandingEstimate:
    def test_none_done_reserves_every_habit(self, vault: Path):
        for name in ("Water", "Stretch", "Read"):
            _habit(vault, name, [], 30)
        status, _ = ext.fetch_habit_status(vault, {}, TODAY)
        assert status == {"total": 3, "done": 0, "outstanding": 3,
                          "est_minutes": 90}

    def test_one_done_today_shrinks_the_estimate(self, vault: Path):
        _habit(vault, "Water", [str(TODAY)], 30)
        _habit(vault, "Stretch", [], 30)
        _habit(vault, "Read", [], 30)
        status, _ = ext.fetch_habit_status(vault, {}, TODAY)
        assert status["done"] == 1
        assert status["est_minutes"] == 60

    def test_all_done_today_reserves_nothing(self, vault: Path):
        for name in ("Water", "Stretch"):
            _habit(vault, name, [str(TODAY)], 30)
        status, _ = ext.fetch_habit_status(vault, {}, TODAY)
        assert status["outstanding"] == 0
        assert status["est_minutes"] == 0

    def test_yesterdays_completion_does_not_count(self, vault: Path):
        _habit(vault, "Water", ["2026-07-25"], 30)
        status, _ = ext.fetch_habit_status(vault, {}, TODAY)
        assert status["done"] == 0
        assert status["est_minutes"] == 30


# ---------------------------------------------------------------------------
# End to end — the allocator's habits segment tracks the estimate
# ---------------------------------------------------------------------------

class TestCapacitySegment:
    def test_habits_segment_reflects_outstanding_only(
        self, vault: Path, monkeypatch: pytest.MonkeyPatch
    ):
        for name in ("Water", "Stretch", "Read"):
            _habit(vault, name, [], 30)
        assert _capacity(vault, monkeypatch)["habits"] == 3

    def test_ticking_one_off_frees_a_block(
        self, vault: Path, monkeypatch: pytest.MonkeyPatch
    ):
        for name in ("Water", "Stretch", "Read"):
            _habit(vault, name, [], 30)
        before = _capacity(vault, monkeypatch)
        _habit(vault, "Water", [str(TODAY)], 30)
        after = _capacity(vault, monkeypatch)
        assert after["habits"] == before["habits"] - 1
        assert after["free"] > before["free"]

    def test_all_done_leaves_no_habit_reservation(
        self, vault: Path, monkeypatch: pytest.MonkeyPatch
    ):
        for name in ("Water", "Stretch"):
            _habit(vault, name, [str(TODAY)], 30)
        assert _capacity(vault, monkeypatch)["habits"] == 0

    def test_legend_reports_the_live_done_left_split(
        self, vault: Path, monkeypatch: pytest.MonkeyPatch
    ):
        _habit(vault, "Water", [str(TODAY)], 30)
        _habit(vault, "Stretch", [], 30)
        assert "habits: 1 done · 1 left" in _capacity(vault, monkeypatch)["legend"]

    def test_no_habits_directory_reserves_nothing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        root = tmp_path / "bare"
        root.mkdir()
        assert _capacity(root, monkeypatch)["habits"] == 0
