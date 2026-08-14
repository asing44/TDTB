"""T12 stress-audit fixes — regression tests for G15/G20/G21/G22 plus the
audit's post_gather runstate-wipe finding. Judgment SDK is mocked throughout
(no live calls, no billed spend)."""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))
import main as main_mod  # noqa: E402
import judgment  # noqa: E402
import runstate  # noqa: E402
import tdtb_gather as gather  # noqa: E402


@pytest.fixture
def vault(tmp_path) -> Path:
    v = tmp_path / "vault-root"
    v.mkdir()
    return v


@pytest.fixture
def client(vault) -> TestClient:
    app = main_mod.create_app(vault_root=vault)
    c = TestClient(app)
    c.app_token = app.state.token
    return c


def _auth(client: TestClient) -> dict:
    return {"X-TDTB-Token": client.app_token}


# ---------------------------------------------------------------------------
# G20 — runstate-suppressed anchored blocks stay out of the judgment payload
# ---------------------------------------------------------------------------

class TestSuppressedAnchoredFiltered:
    def _capture_proposer(self, monkeypatch):
        seen = {}

        def _proposer(assigned, config, anchored_blocks, ctx=None):
            seen["anchored"] = anchored_blocks
            rows = []
            t = 23 * 60 - 90
            for a in assigned:
                rows.append({"id": a.get("id") or a.get("name"),
                             "start": f"{t // 60:02d}:{t % 60:02d}",
                             "end": f"{(t + 30) // 60:02d}:{(t + 30) % 60:02d}",
                             "zone": "any"})
                t += 30
            return {"sequence": rows}

        monkeypatch.setattr(judgment, "propose_sequence", _proposer)
        return seen

    def test_block_level_skip_today_filtered(self, client, monkeypatch):
        seen = self._capture_proposer(monkeypatch)
        r = client.post("/sequence", headers=_auth(client), json={
            "assigned": [{"id": "A"}],
            "config": {},
            "anchored_blocks": [
                {"Block": "Morning Routine", "Start": "07:45",
                 "Duration": "30m", "skip_today": True},
                {"Block": "Wind-down", "Start": "21:00", "Duration": "30m"},
            ],
        })
        assert r.status_code == 200
        names = [b.get("Block") for b in seen["anchored"]]
        assert "Morning Routine" not in names
        assert "Wind-down" in names

    def test_runstate_day_setup_override_filtered(self, client, vault, monkeypatch):
        # The client copy of the block is clean — the skip lives only in
        # today's runstate note. The server must merge + filter regardless.
        seen = self._capture_proposer(monkeypatch)
        today = gather.effective_date(datetime.now())
        state = runstate.build_runstate(
            {"anchored": [{"id": "Morning Routine", "skip_today": True}]}
        )
        runstate.write_runstate(vault, today, state)
        r = client.post("/sequence", headers=_auth(client), json={
            "assigned": [{"id": "A"}],
            "config": {},
            "anchored_blocks": [
                {"Block": "Morning Routine", "Start": "07:45", "Duration": "30m"},
            ],
        })
        assert r.status_code == 200
        assert seen["anchored"] == []

    def test_quarantined_calendar_block_filtered(self, client, monkeypatch):
        # FEEDBACK-02 (frozen contract 17): a known-but-unreviewed calendar is
        # excluded from planning. Once calendar walls harden, a quarantined
        # row must not reach the judgment payload (it would silently become a
        # hard wall) — drop it alongside ignored rows.
        seen = self._capture_proposer(monkeypatch)
        r = client.post("/sequence", headers=_auth(client), json={
            "assigned": [{"id": "A"}],
            "config": {},
            "anchored_blocks": [
                {"Block": "Mystery cal", "Start": "09:00", "End": "10:00",
                 "source": "calendar", "capacity_class": "quarantined"},
            ],
        })
        assert r.status_code == 200, r.text
        assert all(b.get("Block") != "Mystery cal" for b in seen["anchored"])


# ---------------------------------------------------------------------------
# G21 — duration_minutes in the prompt payload + prescriptive validator error
# ---------------------------------------------------------------------------

class TestDurationInvariant:
    def test_prompt_carries_duration_minutes(self, monkeypatch):
        prompts = []

        async def fake_run_query(system_prompt, user_prompt):
            prompts.append(user_prompt)
            return ('{"sequence": [{"id": "Press (Todoist)", "start": "13:00", '
                    '"end": "13:30", "zone": "any"}]}')

        monkeypatch.setattr(judgment, "_run_query", fake_run_query)
        judgment.propose_sequence([{"name": "Press (Todoist)", "blocks": 1}], {}, [])
        assert '"duration_minutes": 30' in prompts[0]

    def test_lengthening_error_names_exact_fix(self):
        with pytest.raises(ValueError) as exc:
            judgment._validate_sequence_proposal(
                {"sequence": [{"id": "Press (Todoist)", "start": "13:00",
                               "end": "14:30", "zone": "any"}]},
                config={},
                assigned=[{"name": "Press (Todoist)", "blocks": 1}],
            )
        msg = str(exc.value)
        assert "set end to exactly 13:30" in msg
        assert "duration_minutes is 30" in msg


# ---------------------------------------------------------------------------
# G22 — transient SDK failures retry with the ORIGINAL prompt, after a delay
# ---------------------------------------------------------------------------

class TestTransientRetry:
    def test_is_transient_classification(self):
        assert judgment._is_transient(
            Exception("Claude Code returned an error result: success"))
        assert judgment._is_transient(asyncio.TimeoutError())
        assert not judgment._is_transient(ValueError("missing keys ['sequence']"))

    def test_sequence_transient_failure_does_not_auto_retry(self, monkeypatch):
        monkeypatch.setattr(judgment, "TRANSIENT_RETRY_DELAY_S", 0)
        prompts = []
        fail_first = {"done": False}

        async def fake_run_query(system_prompt, user_prompt):
            prompts.append(user_prompt)
            if not fail_first["done"]:
                fail_first["done"] = True
                raise Exception("Claude Code returned an error result: success")
            return ('{"sequence": [{"id": "A", "start": "23:00", "end": "23:30", '
                    '"zone": "any"}]}')

        monkeypatch.setattr(judgment, "_run_query", fake_run_query)
        with pytest.raises(judgment.JudgmentError, match="after 1 attempt"):
            judgment.propose_sequence([{"name": "A", "blocks": 1}], {}, [])
        assert len(prompts) == 1

    def test_sequence_validation_failure_does_not_auto_retry(self, monkeypatch):
        monkeypatch.setattr(judgment, "TRANSIENT_RETRY_DELAY_S", 0)
        prompts = []
        responses = ['{"nope": 1}',
                     '{"sequence": [{"id": "A", "start": "23:00", "end": "23:30", '
                     '"zone": "any"}]}']

        async def fake_run_query(system_prompt, user_prompt):
            prompts.append(user_prompt)
            return responses[len(prompts) - 1]

        monkeypatch.setattr(judgment, "_run_query", fake_run_query)
        with pytest.raises(judgment.JudgmentError, match="after 1 attempt"):
            judgment.propose_sequence([{"name": "A", "blocks": 1}], {}, [])
        assert len(prompts) == 1

    def test_contradictory_sdk_string_rewritten(self, monkeypatch):
        monkeypatch.setattr(judgment, "TRANSIENT_RETRY_DELAY_S", 0)

        async def always_crash(system_prompt, user_prompt):
            raise Exception("Claude Code returned an error result: success")

        monkeypatch.setattr(judgment, "_run_query", always_crash)
        with pytest.raises(judgment.JudgmentError) as exc:
            judgment.propose_sequence([{"name": "A"}], {}, [])
        assert "transient CLI crash" in str(exc.value)


# ---------------------------------------------------------------------------
# G15 — SDK query timeout ceiling
# ---------------------------------------------------------------------------

class TestQueryTimeout:
    def test_wedged_query_times_out(self, monkeypatch):
        monkeypatch.setattr(judgment, "QUERY_TIMEOUT_S", 0.05)

        async def wedged(prompt, options):
            await asyncio.sleep(30)
            yield  # pragma: no cover — never reached

        monkeypatch.setattr(judgment, "query", wedged)
        with pytest.raises(asyncio.TimeoutError):
            asyncio.run(judgment._run_query_sdk("sys", "user"))


# ---------------------------------------------------------------------------
# Audit: /gather must not wipe today's runstate (Day Setup, ledger, captures)
# ---------------------------------------------------------------------------

class TestGatherPreservesRunstate:
    def test_regather_keeps_day_setup(self, client, vault):
        today = gather.effective_date(datetime.now())
        state = runstate.build_runstate({
            "anchor": "10:15", "buffering": "standard",
            "intention": "ship T12",
            "commit_ledger": {"todoist": {"ok": True}},
        })
        runstate.write_runstate(vault, today, state)

        r = client.post("/gather", headers=_auth(client))
        assert r.status_code == 200

        text = (vault / runstate.runstate_rel_path(today)).read_text(encoding="utf-8")
        data = gather._extract_json_block(text)
        assert data["anchor"] == "10:15"
        assert data["buffering"] == "standard"
        assert data["intention"] == "ship T12"
        assert data["commit_ledger"] == {"todoist": {"ok": True}}
