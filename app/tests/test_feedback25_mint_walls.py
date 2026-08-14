"""FEEDBACK-25 tests — Mint blocks are authoritative hard scheduling walls.

Three contracts (task_definition FEEDBACK-25):
1. The effective Mint allotment plus selected session IDs is the SINGLE
   source for Mint capacity, schedulable rows, shadow intents, calendar
   writes, and readback expectations. A 300-minute allotment produces 10 Mint
   blocks — never the legacy hardcoded 2-block default (_SCHED_DEFAULTS).
2. Selected Mint session intervals are HARD walls: no assigned row may
   overlap one, in backend validation (sequence.validate_sequence) and at the
   route level (/validate-sequence). The Mint row itself is the wall.
3. Over-assignment is explicit: a row that cannot avoid Mint walls is
   rejected hard, never silently overlapped or softened.

Fixtures/fakes only — no live endpoints, no external writes.
"""
from __future__ import annotations

import sys
from datetime import date, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))
import main as main_mod  # noqa: E402
import external_sources as ext  # noqa: E402
import runstate as rs  # noqa: E402
import sequence  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent.parent / "gather"))
import tdtb_gather as gather  # noqa: E402

from sequence import validate_sequence  # noqa: E402


CFG = {
    "Template Blocks": {"Trinoor Hours": [
        {"Slot": "Morning", "Start": "8:30 AM", "End": "12:30 PM"},
        {"Slot": "Afternoon", "Start": "1:30 PM", "End": "5:00 PM"},
    ]},
}
MONDAY = date(2026, 7, 13)


def _seq_row(id_, start, end, zone="any"):
    return {"id": id_, "start": start, "end": end, "zone": zone}


def _mint_item(id_="Mint Morning · 08:30", start="08:30", end="09:00"):
    return {
        "id": id_, "name": id_, "zone": "work_hours", "source": "schedulable",
        "mint_session": True, "mint_session_id": f"mint:morning:{start}",
        "placement_window": {"start": start, "end": end},
        "calendar_class": "mint",
    }


# ---------------------------------------------------------------------------
# Contract 1 — authoritative Mint derivation (capacity == rows)
# ---------------------------------------------------------------------------

class TestMintAllotmentDerivation:
    def test_aggregate_minting_row_uses_effective_allotment(self):
        items, _, _ = ext.build_schedulable_blocks(
            CFG, {}, MONDAY, "09:00",
            resolved_day_semantics={"effective_allotment_minutes": 300},
        )
        [m] = [i for i in items if i["name"] == "Minting"]
        assert m["blocks"] == 10
        assert m["duration"] == 300

    def test_aggregate_minting_row_uses_dated_allotment_override(self):
        items, _, _ = ext.build_schedulable_blocks(
            CFG, {"work_allotment_minutes": 300}, MONDAY, "09:00",
        )
        [m] = [i for i in items if i["name"] == "Minting"]
        assert m["blocks"] == 10
        assert m["duration"] == 300

    def test_no_allotment_context_keeps_legacy_default(self):
        # No resolved semantics and no dated override: the historical
        # 2-block default is the contract for callers without allotment state.
        items, _, _ = ext.build_schedulable_blocks(CFG, {}, MONDAY, "09:00")
        [m] = [i for i in items if i["name"] == "Minting"]
        assert m["blocks"] == 2

    def test_zero_allotment_suppresses_legacy_aggregate_row(self):
        items, _, _ = ext.build_schedulable_blocks(
            CFG, {"work_allotment_minutes": 0}, MONDAY, "09:00",
        )
        assert all(i["name"] != "Minting" for i in items)

    def test_selected_sessions_still_win_over_allotment_aggregate(self):
        options = ext.mint_session_options(CFG)
        selected = [options[0]["id"], options[1]["id"]]
        items, _, _ = ext.build_schedulable_blocks(
            CFG,
            {"schedulable": {"minting": {"on": True, "sessions": selected}}},
            MONDAY, "09:00",
            resolved_day_semantics={"effective_allotment_minutes": 300},
        )
        assert all(i["name"] != "Minting" for i in items)
        mint = [i for i in items if i.get("mint_session")]
        assert len(mint) == 2
        assert all(i["blocks"] == 1 for i in mint)

    def test_session_row_total_matches_effective_allotment_capacity(self):
        # AC1: selected sessions derive the same Mint total as capacity —
        # each 30-minute session is 1 block, so the row total equals
        # effective_allotment_minutes / 30 when the two are in sync.
        options = ext.mint_session_options(CFG)
        selected = [options[0]["id"], options[1]["id"]]
        items, _, _ = ext.build_schedulable_blocks(
            CFG,
            {"schedulable": {"minting": {"on": True, "sessions": selected}}},
            MONDAY, "09:00",
            resolved_day_semantics={"effective_allotment_minutes": 60},
        )
        mint = [i for i in items if i.get("mint_session")]
        total = sum(i["blocks"] for i in mint)
        assert total == 60 / 30


# ---------------------------------------------------------------------------
# Contract 2 — selected Mint intervals are HARD walls (validation)
# ---------------------------------------------------------------------------

class TestMintWallsHardValidation:
    def _res(self, rows, optional_items=None, assigned=None, grants=None):
        assigned = assigned if assigned is not None else [
            {"id": r["id"], "zone": r.get("zone") or "any"} for r in rows
        ]
        proposal = {"sequence": rows,
                    "overlap_grants": grants or []}
        return validate_sequence(
            proposal, assigned, [], CFG,
            optional_items=optional_items or [],
        )

    def test_assigned_row_overlapping_selected_mint_session_is_hard_rejection(self):
        mint = _mint_item()
        result = self._res(
            [_seq_row("task-1", "08:45", "09:15")],
            optional_items=[mint],
        )
        assert result.ok is False
        assert any(
            "Mint" in e and "overlap" in e.lower() for e in result.hard_errors
        )

    def test_mint_row_itself_is_not_a_violation(self):
        mint = _mint_item()
        result = self._res(
            [
                {"id": mint["id"], "start": "08:30", "end": "09:00",
                 "zone": "work_hours"},
                _seq_row("task-1", "09:00", "09:30"),
            ],
            optional_items=[mint],
            assigned=[{"id": mint["id"], "zone": "work_hours"},
                      {"id": "task-1", "zone": "any"}],
        )
        assert result.ok is True
        assert result.hard_errors == []

    def test_back_to_back_boundary_is_allowed(self):
        mint = _mint_item()
        result = self._res(
            [_seq_row("task-1", "08:00", "08:30")],
            optional_items=[mint],
        )
        assert result.ok is True
        assert result.hard_errors == []

    def test_mint_wall_is_not_softened_by_an_overlap_grant(self):
        mint = _mint_item()
        grant = {
            "primary_id": "task-1",
            "companion_id": mint["id"],
            "primary_interval": {"start": "08:45", "end": "09:15"},
            "companion_interval": {"start": "08:30", "end": "09:00"},
            "reason": "explicit contract permission",
            "planning_config_fingerprint": "",
        }
        result = self._res(
            [_seq_row("task-1", "08:45", "09:15")],
            optional_items=[mint],
            grants=[grant],
        )
        assert result.ok is False
        assert any("Mint" in e for e in result.hard_errors)

    def test_legacy_window_upper_bound_check_still_fires(self):
        # A non-mint item with a placement window keeps the historical
        # upper-bound contract (regression guard for frozen contract 14).
        item = {
            "id": "Mint Morning",
            "zone": "work_hours",
            "placement_window": {"start": "08:30", "end": "12:30"},
        }
        result = validate_sequence(
            {"sequence": [_seq_row("Mint Morning", "13:00", "13:30")]},
            [], [], CFG, optional_ids={"Mint Morning"}, optional_items=[item],
        )
        assert result.ok is False
        assert "selected Mint session window" in result.hard_errors[0]


# ---------------------------------------------------------------------------
# Contract 1 + 2 — route-level wiring
# ---------------------------------------------------------------------------

def _write_cfg(vault: Path) -> None:
    cfg = vault / "00 - META/Skill-Configs/tdtb-bridger.md"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(
        "## Defaults\n"
        "| Key | Value |\n|---|---|\n"
        "| eod | 11:59 PM |\n"
        "| anchor.round_to_minutes | 15 |\n"
        "\n## Template Blocks\n"
        "### Trinoor Hours\n"
        "| Slot | Start | End |\n|---|---|---|\n"
        "| Morning | 8:30 AM | 12:30 PM |\n"
        "| Afternoon | 1:30 PM | 5:00 PM |\n",
        encoding="utf-8",
    )


@pytest.fixture
def vault(tmp_path) -> Path:
    return tmp_path / "vault-root"


@pytest.fixture
def client(vault) -> TestClient:
    vault.mkdir()
    app = main_mod.create_app(vault_root=vault)
    c = TestClient(app)
    c.app_token = app.state.token
    return c


def _auth(client: TestClient) -> dict:
    return {"X-TDTB-Token": client.app_token}


class TestSequenceRouteMintDerivation:
    def test_sequence_injects_allotment_derived_minting_row(self, client, vault, monkeypatch):
        _write_cfg(vault)
        frozen_date = date(2026, 7, 13)
        rs.write_runstate(vault, frozen_date, rs.build_runstate({
            "work_allotment_minutes": 300,
        }))
        captured = {}

        def fake_propose(assigned, config, anchored_blocks, ctx=None):
            captured["assigned"] = assigned
            return {"sequence": [], "overlap_grants": []}

        monkeypatch.setattr(main_mod.judgment, "propose_sequence", fake_propose)
        monkeypatch.setattr(main_mod.sequence, "validate_sequence",
                            lambda *a, **k: type("R", (), {
                                "ok": True, "hard_errors": [], "warnings": []})())

        class _FrozenDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return cls(2026, 7, 13, 9, 0)  # Monday 09:00

        monkeypatch.setattr(main_mod, "datetime", _FrozenDatetime)
        r = client.post("/sequence", json={
            "assigned": [],
            "config": {"Template Blocks": {"Trinoor Hours": [
                {"Slot": "Morning", "Start": "8:30 AM", "End": "12:30 PM"},
                {"Slot": "Afternoon", "Start": "1:30 PM", "End": "5:00 PM"}]}},
            "anchored_blocks": [],
        }, headers=_auth(client))
        assert r.status_code == 200, r.text
        minting = next(
            (a for a in captured["assigned"]
             if (a.get("id") or a.get("name")) == "Minting"),
            None,
        )
        assert minting is not None
        assert minting["blocks"] == 10
        assert minting["duration"] == 300


class TestValidateSequenceMintWalls:
    def _seed_mint_session(self, vault) -> None:
        today = gather.effective_date(datetime.now())
        rs.write_runstate(vault, today, rs.build_runstate({
            "anchor": "00:00", "eod": "23:59",
            "schedulable": {"minting": {
                "on": True,
                "sessions": ["mint:morning:08:30"],
            }},
        }))

    def test_route_rejects_row_over_selected_mint_session(self, client, vault):
        _write_cfg(vault)
        self._seed_mint_session(vault)
        r = client.post("/validate-sequence", headers=_auth(client), json={
            "sequence": [_seq_row("task-1", "08:45", "09:15")],
            "assigned": [{"id": "task-1"}],
            "anchored_blocks": [],
            "config": {"Template Blocks": {"Trinoor Hours": [
                {"Slot": "Morning", "Start": "8:30 AM", "End": "12:30 PM"},
                {"Slot": "Afternoon", "Start": "1:30 PM", "End": "5:00 PM"},
            ]}},
        })
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is False
        assert any("Mint" in e for e in body["hard_errors"])

    def test_route_allows_clean_rows_beside_mint_session(self, client, vault):
        _write_cfg(vault)
        self._seed_mint_session(vault)
        r = client.post("/validate-sequence", headers=_auth(client), json={
            "sequence": [_seq_row("task-1", "09:00", "09:30")],
            "assigned": [{"id": "task-1"}],
            "anchored_blocks": [],
            "config": {"Template Blocks": {"Trinoor Hours": [
                {"Slot": "Morning", "Start": "8:30 AM", "End": "12:30 PM"},
                {"Slot": "Afternoon", "Start": "1:30 PM", "End": "5:00 PM"},
            ]}},
        })
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert body["hard_errors"] == []
