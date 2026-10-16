"""#18 — per-row operator "may overlap Mint" grant.

An operator-set, per-row opt-in (``may_overlap_mint: true``) lets exactly the
assigned rows that carry it overlap a selected Mint session, and only as a
SOFT named ``mint_overlap_granted`` warning. Every other row keeps the hard
``mint_overlap`` wall. The grant is read from the assigned/optional items
only — never from a proposal sequence row or an overlap grant — so neither the
model nor a client can self-authorize.

Fixtures/fakes only: zero billed calls, zero real source writes. The judgment
model is never called; the prompt test asserts text only.
"""
from __future__ import annotations

import asyncio
import sys
from datetime import date, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))
import main as main_mod  # noqa: E402
import judgment  # noqa: E402
import runstate as rs  # noqa: E402
import sequence  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent.parent / "gather"))
import tdtb_gather as gather  # noqa: E402

from sequence import validate_sequence  # noqa: E402
from tests.config_markdown import write_config_note  # noqa: E402


CFG = {
    "Template Blocks": {"Trinoor Hours": [
        {"Slot": "Morning", "Start": "8:30 AM", "End": "12:30 PM"},
        {"Slot": "Afternoon", "Start": "1:30 PM", "End": "5:00 PM"},
    ]},
}


def _seq_row(id_, start, end, zone="any", **extra):
    return {"id": id_, "start": start, "end": end, "zone": zone, **extra}


def _mint_item(id_="Mint Afternoon · 15:00", start="15:00", end="15:30"):
    return {
        "id": id_, "name": id_, "zone": "work_hours", "source": "schedulable",
        "mint_session": True, "mint_session_id": f"mint:afternoon:{start}",
        "placement_window": {"start": start, "end": end},
        "calendar_class": "mint",
    }


def _run(rows, *, assigned, optional_items=None, grants=None, time_frame=None):
    return validate_sequence(
        {"sequence": rows, "overlap_grants": grants or []},
        assigned, [], CFG,
        time_frame=time_frame,
        optional_items=optional_items or [],
    )


def _granted_warnings(result):
    return [w for w in result.warnings if w.get("rule") == "mint_overlap_granted"]


# ---------------------------------------------------------------------------
# Validator contract — soft grant vs hard wall
# ---------------------------------------------------------------------------

class TestValidatorMintOverlapGrant:
    def test_granted_row_inside_mint_is_ok_with_one_named_warning(self):
        mint = _mint_item()
        result = _run(
            [_seq_row("Backpack", "15:00", "15:30")],
            assigned=[{"id": "Backpack", "zone": "any", "may_overlap_mint": True}],
            optional_items=[mint],
        )
        assert result.ok is True
        assert result.hard_errors == []
        [warning] = _granted_warnings(result)
        assert "Backpack" in warning["detail"]
        assert "Mint Afternoon · 15:00" in warning["detail"]
        assert "May overlap Mint (operator)" in warning["detail"]

    def test_same_row_without_the_flag_stays_hard(self):
        mint = _mint_item()
        result = _run(
            [_seq_row("Backpack", "15:00", "15:30")],
            assigned=[{"id": "Backpack", "zone": "any"}],
            optional_items=[mint],
        )
        assert result.ok is False
        assert _granted_warnings(result) == []
        [hard] = [
            d for d in result.diagnostics
            if d["rule"] == "mint_overlap" and d["severity"] == "error"
        ]
        assert "Backpack" in hard["detail"]
        assert "Mint Afternoon · 15:00" in hard["detail"]

    def test_plain_overlap_grant_does_not_soften_the_wall(self):
        mint = _mint_item()
        grant = {
            "primary_id": "Backpack",
            "companion_id": mint["id"],
            "primary_interval": {"start": "15:00", "end": "15:30"},
            "companion_interval": {"start": "15:00", "end": "15:30"},
            "reason": "explicit contract permission",
            "planning_config_fingerprint": "",
        }
        result = _run(
            [_seq_row("Backpack", "15:00", "15:30")],
            assigned=[{"id": "Backpack", "zone": "any"}],
            optional_items=[mint],
            grants=[grant],
        )
        assert result.ok is False
        assert _granted_warnings(result) == []
        assert any("Mint" in e for e in result.hard_errors)

    def test_forged_proposal_row_flag_cannot_self_authorize(self):
        # The grant is read from assigned/optional items only. A proposal row
        # that carries may_overlap_mint while the assigned item lacks it is
        # still a hard wall.
        mint = _mint_item()
        result = _run(
            [_seq_row("Backpack", "15:00", "15:30", may_overlap_mint=True)],
            assigned=[{"id": "Backpack", "zone": "any"}],
            optional_items=[mint],
        )
        assert result.ok is False
        assert _granted_warnings(result) == []
        assert any("overlaps selected Mint session" in e for e in result.hard_errors)

    def test_granted_and_reseated_keeps_the_overflow_warning(self):
        # Precedence (#18): the re-seated soft case is checked first, so a row
        # that is both re-seated and granted keeps the overflow reading and
        # never emits the grant warning.
        mint = _mint_item(start="17:00", end="17:30")
        result = _run(
            [_seq_row("Backpack", "23:00", "23:30")],
            assigned=[{"id": "Backpack", "zone": "any", "blocks": 1,
                       "may_overlap_mint": True}],
            optional_items=[mint],
            time_frame={"effective_eod": "22:00"},
        )
        assert result.ok is True
        assert _granted_warnings(result) == []
        assert any(w.get("rule") == "overflow_overlap" for w in result.warnings)


# ---------------------------------------------------------------------------
# Prompt contract — text only, no model call
# ---------------------------------------------------------------------------

class TestSequencePromptGrant:
    def test_prompt_states_the_grant_and_carries_the_flagged_item_json(
        self, monkeypatch
    ):
        captured = {}

        async def fake_call(ctx, label, system_prompt, user_prompt, validate, **kw):
            captured["system"] = system_prompt
            captured["user"] = user_prompt
            return {"sequence": [], "overlap_grants": []}

        monkeypatch.setattr(judgment, "_call_and_validate", fake_call)
        asyncio.run(judgment.propose_sequence_async(
            [{"id": "Backpack", "name": "Backpack", "blocks": 1,
              "may_overlap_mint": True}],
            {}, [], judgment.RunContext(),
        ))
        assert (
            "may_overlap_mint: true is explicitly operator-granted"
            in captured["system"]
        )
        assert "the overlap is always reported" in captured["system"]
        # prompt_assigned spreads the whole item, so the flag reaches the model.
        assert '"may_overlap_mint": true' in captured["user"]


# ---------------------------------------------------------------------------
# Route-level wiring — both request paths
# ---------------------------------------------------------------------------

def _write_cfg(vault: Path) -> None:
    cfg = vault / "00 - META/Skill-Configs/tdtb-bridger.md"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    write_config_note(cfg,
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
    app = main_mod.create_app()
    c = TestClient(app)
    c.app_token = app.state.token
    return c


def _auth(client: TestClient) -> dict:
    return {"X-TDTB-Token": client.app_token}


def _today() -> date:
    return gather.effective_date(datetime.now())


def _seed_mint_and_index() -> None:
    today = _today()
    rs.write_runstate(today, rs.build_runstate({
        "anchor": "00:00", "eod": "23:59",
        "schedulable": {"minting": {
            "on": True,
            "sessions": ["mint:afternoon:15:00"],
        }},
    }))
    rs.write_digest_index(today, [{
        "name": "Backpack", "todoist_id": "t-backpack",
        "path": "todoist://t-backpack", "surface": "assigned",
        "source": "todoist", "blocks": 1,
    }])


def _cfg_body():
    return {"Template Blocks": {"Trinoor Hours": [
        {"Slot": "Morning", "Start": "8:30 AM", "End": "12:30 PM"},
        {"Slot": "Afternoon", "Start": "1:30 PM", "End": "5:00 PM"},
    ]}}


class TestValidateSequenceRouteGrant:
    def _post(self, client, assigned):
        return client.post("/validate-sequence", headers=_auth(client), json={
            "sequence": [_seq_row("Backpack", "15:00", "15:30")],
            "assigned": assigned,
            "anchored_blocks": [],
            "config": _cfg_body(),
        })

    def test_matched_flagged_row_is_ok_with_the_grant_warning(self, client, vault):
        _write_cfg(vault)
        _seed_mint_and_index()
        r = self._post(client, [{
            "name": "Backpack", "todoist_id": "t-backpack",
            "path": "todoist://t-backpack", "may_overlap_mint": True,
        }])
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is True
        assert body["hard_errors"] == []
        [warning] = [
            w for w in body["warnings"]
            if w.get("rule") == "mint_overlap_granted"
        ]
        assert "May overlap Mint (operator)" in warning["detail"]

    def test_matched_unflagged_row_is_hard(self, client, vault):
        _write_cfg(vault)
        _seed_mint_and_index()
        r = self._post(client, [{
            "name": "Backpack", "todoist_id": "t-backpack",
            "path": "todoist://t-backpack",
        }])
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is False
        assert any("overlaps selected Mint session" in e
                   for e in body["hard_errors"])

    def test_mismatched_identity_drops_the_grant_and_refuses_the_row(
        self, client, vault
    ):
        # A stable identity that does not match today's digest index is never
        # authorized: the row is refused before validation, so the grant can
        # never soften the wall.
        _write_cfg(vault)
        _seed_mint_and_index()
        r = self._post(client, [{
            "name": "Backpack", "todoist_id": "t-other",
            "path": "todoist://t-other", "may_overlap_mint": True,
        }])
        assert r.status_code == 422, r.text
        assert "Backpack" in str(r.json()["detail"])

    def test_identityless_legacy_row_drops_the_flag_and_stays_hard(
        self, client, vault
    ):
        # The flag is opt-in metadata, never carried forward on an unmatched
        # identity-less legacy row: it is dropped and the wall stays hard.
        _write_cfg(vault)
        _seed_mint_and_index()
        r = self._post(client, [{"id": "task-1", "may_overlap_mint": True}])
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is False
        assert [
            w for w in body["warnings"]
            if w.get("rule") == "mint_overlap_granted"
        ] == []
        assert any("overlaps selected Mint session" in e
                   for e in body["hard_errors"])


class TestSequenceRouteGrant:
    def test_proposal_path_carries_the_flag_to_judgment(self, client, vault, monkeypatch):
        _write_cfg(vault)
        _seed_mint_and_index()
        captured = {}

        def fake_propose(assigned, config, anchored_blocks, ctx=None):
            captured["assigned"] = assigned
            return {"sequence": [], "overlap_grants": []}

        monkeypatch.setattr(main_mod.judgment, "propose_sequence", fake_propose)
        monkeypatch.setattr(main_mod.planning.sequence, "validate_sequence",
                            lambda *a, **k: type("R", (), {
                                "ok": True, "hard_errors": [], "warnings": []})())
        r = client.post("/sequence", headers=_auth(client), json={
            "assigned": [{
                "name": "Backpack", "todoist_id": "t-backpack",
                "path": "todoist://t-backpack", "may_overlap_mint": True,
            }],
            "config": _cfg_body(),
            "anchored_blocks": [],
        })
        assert r.status_code == 200, r.text
        row = next(
            item for item in captured["assigned"] if item.get("id") == "Backpack"
        )
        assert row["may_overlap_mint"] is True
