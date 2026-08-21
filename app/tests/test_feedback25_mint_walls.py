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
    def _res(
        self, rows, optional_items=None, assigned=None, grants=None,
        anchored_blocks=None,
    ):
        assigned = assigned if assigned is not None else [
            {"id": r["id"], "zone": r.get("zone") or "any"} for r in rows
        ]
        proposal = {"sequence": rows,
                    "overlap_grants": grants or []}
        return validate_sequence(
            proposal, assigned, anchored_blocks or [], CFG,
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

    def test_duplicate_mint_item_in_assigned_and_optional_yields_one_wall_error(self):
        # CP-T29: the same Mint item is present in BOTH assigned and
        # optional_items (the schedulable row is injected into the assigned
        # set while also flowing through optional_items). The assigned copy
        # carries the SAME mint_session/placement_window metadata, so
        # selected_mint_walls sees two identical (item id, interval) entries
        # that must collapse to ONE hard wall — a single overlapping row then
        # produces exactly one hard error, never two.
        mint = _mint_item()
        result = self._res(
            [
                {"id": mint["id"], "start": "08:30", "end": "09:00",
                 "zone": "work_hours"},
                _seq_row("task-1", "08:45", "09:15"),
            ],
            optional_items=[mint],
            assigned=[dict(mint),  # same metadata → identical (id, interval) wall
                      {"id": "task-1", "zone": "any"}],
        )
        assert result.ok is False
        mint_errors = [e for e in result.hard_errors if "Mint" in e]
        assert len(mint_errors) == 1
        assert "overlaps selected Mint session" in mint_errors[0]

    def test_selected_mint_walls_deduplicates_identical_entries(self):
        # CP-T29: identical (item id, interval) entries collapse, first
        # occurrence wins, order preserved.
        mint = _mint_item()
        walls = sequence.selected_mint_walls([mint, mint])
        assert walls == [("Mint Morning · 08:30", (510, 540))]

    def test_anchored_window_is_not_rejected_by_selected_mint_wall(self):
        # A permeable anchored window may share the selected Mint interval;
        # the Mint wall protects movable work, not the source row itself.
        mint = _mint_item()
        result = self._res(
            [
                {"id": "Live", "start": "08:30", "end": "09:00",
                 "zone": "anchored"},
                {"id": mint["id"], "start": "08:30", "end": "09:00",
                 "zone": "work_hours"},
            ],
            optional_items=[mint],
            assigned=[
                {"id": "Live", "zone": "anchored"},
                {"id": mint["id"], "zone": "work_hours"},
            ],
            anchored_blocks=[
                {"Block": "Live", "Type": "window", "Start": "08:30",
                 "End": "12:30", "overlap_allowed": True},
            ],
        )
        assert result.ok is True
        assert result.hard_errors == []

    def test_pinned_movable_row_is_still_rejected_by_selected_mint_wall(self):
        # Synthetic pinned walls travel through anchored_blocks, but remain
        # movable-work identities for the Mint hard-wall contract.
        mint = _mint_item()
        result = self._res(
            [
                {"id": mint["id"], "start": "08:30", "end": "09:00",
                 "zone": "work_hours"},
                _seq_row("task-1", "08:45", "09:15"),
            ],
            optional_items=[mint],
            assigned=[
                dict(mint),
                {"id": "task-1", "zone": "any"},
            ],
            anchored_blocks=[
                {"Block": "task-1", "Type": "hard", "Start": "08:45",
                 "End": "09:15", "pinned": True},
            ],
        )
        assert result.ok is False
        mint_errors = [e for e in result.hard_errors if "Mint" in e]
        assert len(mint_errors) == 1
        assert "overlaps selected Mint session" in mint_errors[0]


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


# ---------------------------------------------------------------------------
# FEEDBACK-27 — stale Mint sessions vs effective fixed/work walls (route)
# ---------------------------------------------------------------------------

AUG17 = date(2026, 8, 17)  # Monday — workday so Mint sessions emit


def _seed_aug17_mint(vault, sessions=("mint:afternoon:15:00",)) -> None:
    # QT is disabled so the AUG17 fixture's selected set is exactly the
    # chosen Mint session plus the tasks the test posts — never-bump then
    # proves each selected item appears exactly once.
    rs.write_runstate(vault, AUG17, rs.build_runstate({
        "schedulable": {"minting": {
            "on": True,
            "sessions": list(sessions),
        }, "qt": {"on": False}},
    }))


def _freeze_aug17(monkeypatch) -> None:
    class _FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 8, 17, 9, 0)  # Monday 09:00

    monkeypatch.setattr(main_mod, "datetime", _FrozenDatetime)


def _oppd_wall(capacity_class="fixed"):
    return {"Block": "OPPD", "source": "calendar",
            "capacity_class": capacity_class,
            "Start": "15:00", "End": "15:30"}


def _post_aug17(client, anchored_blocks, assigned=None):
    return client.post("/sequence", json={
        "assigned": assigned if assigned is not None else [],
        "config": {"Template Blocks": {"Trinoor Hours": [
            {"Slot": "Morning", "Start": "8:30 AM", "End": "12:30 PM"},
            {"Slot": "Afternoon", "Start": "1:30 PM", "End": "5:00 PM"},
        ]}},
        "anchored_blocks": anchored_blocks,
    }, headers=_auth(client))


def test_august_17_stale_mint_filtered_before_judgment(client, vault, monkeypatch):
    """FEEDBACK-27: a saved Mint selection overlapping a current fixed or work
    calendar wall is a stale preflight failure. It must stop BEFORE judgment,
    the immutable merge, or any billed ledger change — the exact August 17
    incident shape (Mint 15:00-15:30 vs OPPD 15:00-15:30)."""
    _write_cfg(vault)
    _seed_aug17_mint(vault)
    _freeze_aug17(monkeypatch)
    judgment_calls = []

    def fake_propose(*args, **kwargs):
        judgment_calls.append(args)
        return {"sequence": [], "overlap_grants": []}

    monkeypatch.setattr(main_mod.judgment, "propose_sequence", fake_propose)

    ledger_before = client.get("/billed-ledger", headers=_auth(client)).json()
    r = _post_aug17(client, [_oppd_wall()])
    assert r.status_code == 422
    body = r.json()
    assert body["detail"]["message"] == (
        "selected Mint sessions conflict with fixed or work walls"
    )
    assert body["detail"]["conflicts"] == [{
        "mint_id": "Mint Afternoon · 15:00",
        "mint_interval": {"start": "15:00", "end": "15:30"},
        "wall_id": "OPPD",
        "wall_interval": {"start": "15:00", "end": "15:30"},
    }]
    assert judgment_calls == []
    state = rs.read_runstate(vault, AUG17)
    assert state["billed_calls"] == 0
    # Public ledger seam: the stale preflight never spends the per-day budget.
    ledger_after = client.get("/billed-ledger", headers=_auth(client)).json()
    assert ledger_after == ledger_before
    assert ledger_after["spent"] == 0


def test_august_17_work_wall_also_stops_before_judgment(client, vault, monkeypatch):
    # The server-side recheck covers work walls too, not only fixed.
    _write_cfg(vault)
    _seed_aug17_mint(vault)
    _freeze_aug17(monkeypatch)
    judgment_calls = []
    monkeypatch.setattr(
        main_mod.judgment, "propose_sequence",
        lambda *a, **k: judgment_calls.append(a) or {"sequence": []},
    )
    r = _post_aug17(client, [_oppd_wall("work")])
    assert r.status_code == 422
    assert r.json()["detail"]["conflicts"][0]["wall_id"] == "OPPD"
    assert judgment_calls == []
    assert rs.read_runstate(vault, AUG17)["billed_calls"] == 0


def test_clean_mint_selection_still_sequences(client, vault, monkeypatch):
    # A saved Mint session that does NOT touch a wall keeps the normal path:
    # judgment runs, the exact immutable row is merged, no conflict.
    _write_cfg(vault)
    _seed_aug17_mint(vault, sessions=("mint:afternoon:14:00",))
    _freeze_aug17(monkeypatch)
    captured = {}

    def fake_propose(assigned, config, anchored_blocks, ctx=None):
        captured["assigned"] = assigned
        return {"sequence": [], "overlap_grants": []}

    monkeypatch.setattr(main_mod.judgment, "propose_sequence", fake_propose)
    monkeypatch.setattr(main_mod.sequence, "validate_sequence",
                        lambda *a, **k: type("R", (), {
                            "ok": True, "hard_errors": [], "warnings": []})())
    r = _post_aug17(client, [_oppd_wall()])
    assert r.status_code == 200, r.text
    mint_rows = [
        row for row in r.json()["sequence"]
        if row.get("mint_session_id") == "mint:afternoon:14:00"
    ]
    assert mint_rows == [{
        "id": "Mint Afternoon · 14:00",
        "start": "14:00",
        "end": "14:30",
        "zone": "work_hours",
        "source": "schedulable",
        "mint_session": True,
        "mint_session_id": "mint:afternoon:14:00",
        "calendar_class": "mint",
    }]


def test_august_17_clean_sequence_places_every_task_once_at_exact_duration(
    client, vault, monkeypatch
):
    """FEEDBACK-27 August 17 AC: with the OPPD 15:00-15:30 fixed wall in place
    and a collision-free saved Mint session (14:00-14:30), the route sequences
    EVERY selected task exactly once at its exact 15/30-minute duration
    through the REAL validator — no dropped, duplicated, or shortened rows,
    and the selected Mint session stays a single immutable row."""
    _write_cfg(vault)
    _seed_aug17_mint(vault, sessions=("mint:afternoon:14:00",))
    _freeze_aug17(monkeypatch)

    def fake_propose(assigned, config, anchored_blocks, ctx=None):
        return {"sequence": [
            {"id": "Write brief", "start": "09:00", "end": "09:15"},
            {"id": "Deep work", "start": "09:15", "end": "09:45"},
        ], "overlap_grants": []}

    monkeypatch.setattr(main_mod.judgment, "propose_sequence", fake_propose)
    r = _post_aug17(client, [_oppd_wall()], assigned=[
        {"id": "Write brief", "name": "Write brief", "duration": 15,
         "blocks": 1, "labels": []},
        {"id": "Deep work", "name": "Deep work", "duration": 30,
         "blocks": 1, "labels": []},
    ])
    assert r.status_code == 200, r.text
    body = r.json()

    def _minutes(hhmm: str) -> int:
        hour, minute = (int(p) for p in hhmm.split(":"))
        return hour * 60 + minute

    placed = [
        row for row in body["sequence"]
        if not row.get("mint_session") and not row.get("backdrop")
    ]
    assert [row["id"] for row in placed] == ["Write brief", "Deep work"]
    by_id = {row["id"]: row for row in placed}
    for task_id, start, end, minutes in (
        ("Write brief", "09:00", "09:15", 15),
        ("Deep work", "09:15", "09:45", 30),
    ):
        assert by_id[task_id]["start"] == start
        assert by_id[task_id]["end"] == end
        assert _minutes(end) - _minutes(start) == minutes
    # The selected Mint session remains exactly one immutable row.
    mint_rows = [
        row for row in body["sequence"]
        if row.get("mint_session_id") == "mint:afternoon:14:00"
    ]
    assert len(mint_rows) == 1
    assert mint_rows[0]["start"] == "14:00" and mint_rows[0]["end"] == "14:30"


# ---------------------------------------------------------------------------
# CP-T29 — selected Mint windows are prompt-visible hard walls
# ---------------------------------------------------------------------------

def test_selected_mint_windows_reach_judgment_as_prompt_only_walls(
    client, vault, monkeypatch
):
    """CP-T29: the exact selected Mint interval is visible to judgment as a
    hard wall in the anchored_blocks it renders into the prompt, so the model
    can place movable work around it. The wall is prompt-only: it must NOT be
    passed to validate_sequence's anchored_blocks (validation derives Mint
    walls itself from optional_items/assigned), and the Mint row is never
    made movable."""
    _write_cfg(vault)
    _seed_aug17_mint(vault, sessions=("mint:afternoon:14:00",))
    _freeze_aug17(monkeypatch)
    captured = {}

    def fake_propose(assigned, config, anchored_blocks, ctx=None):
        captured["judgment_anchored"] = anchored_blocks
        return {"sequence": [], "overlap_grants": []}

    def fake_validate(proposal, assigned, anchored_blocks, config, **kwargs):
        captured["validation_anchored"] = anchored_blocks
        return type("R", (), {"ok": True, "hard_errors": [], "warnings": []})()

    monkeypatch.setattr(main_mod.judgment, "propose_sequence", fake_propose)
    monkeypatch.setattr(main_mod.sequence, "validate_sequence", fake_validate)
    r = _post_aug17(client, [])
    assert r.status_code == 200, r.text

    # The exact Mint interval reaches judgment as a hard wall.
    assert {
        "Block": "Mint Afternoon · 14:00", "Type": "hard",
        "Start": "14:00", "End": "14:30", "pinned": True, "mint_session": True,
    } in captured["judgment_anchored"]
    # Prompt-only: validation's anchored_blocks are untouched — the Mint wall
    # is derived inside validate_sequence from optional_items/assigned.
    assert captured["validation_anchored"] == []
    # The Mint row is not movable work for the model.
    assert all(
        str(a.get("id") or a.get("name")) != "Mint Afternoon · 14:00"
        for a in captured["judgment_anchored"]
        if isinstance(a, dict) and a.get("mint_session") is not True
    )


def test_sequence_rejects_post_judgment_mint_overlap_once(
    client, vault, monkeypatch
):
    """The real /sequence seam keeps Mint walls hard after judgment returns.

    This deliberately supplies an invalid canned proposal so the route-level
    validator proves the billed proposal cannot silently cross the selected
    Mint interval, while the dedupe fix keeps the user-facing error singular.
    """
    _write_cfg(vault)
    _seed_aug17_mint(vault, sessions=("mint:afternoon:14:00",))
    _freeze_aug17(monkeypatch)

    def fake_propose(assigned, config, anchored_blocks, ctx=None):
        return {
            "sequence": [
                {"id": "overlap", "start": "14:15", "end": "14:45"}
            ],
            "overlap_grants": [],
        }

    monkeypatch.setattr(main_mod.judgment, "propose_sequence", fake_propose)
    r = _post_aug17(
        client,
        [],
        assigned=[
            {"id": "overlap", "name": "overlap", "blocks": 1, "labels": []}
        ],
    )

    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    mint_errors = [
        error for error in detail["hard_errors"]
        if "overlaps selected Mint session" in error
    ]
    assert len(mint_errors) == 1
    assert "Mint Afternoon · 14:00" in mint_errors[0]
    assert rs.read_runstate(vault, AUG17)["billed_calls"] == 0
