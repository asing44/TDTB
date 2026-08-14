"""Tests for judgment.py (T11) — schema validation, retry, call-counter bound.

The live Agent SDK is never invoked here (no network in CI) — every test
monkeypatches ``judgment._run_query`` (the one seam that calls the SDK) with
a canned coroutine.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import judgment as j  # noqa: E402


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _queue(monkeypatch, responses: list[str]):
    """Patch _run_query to return successive canned responses, one per call."""
    calls = {"n": 0}

    async def fake_run_query(system_prompt, user_prompt):
        idx = calls["n"]
        calls["n"] += 1
        return responses[idx]

    monkeypatch.setattr(j, "_run_query", fake_run_query)
    return calls


# ---------------------------------------------------------------------------
# Call-budget / RunContext
# ---------------------------------------------------------------------------

class TestRunContext:
    def test_consume_under_budget(self):
        ctx = j.RunContext()
        for _ in range(4):
            ctx.consume("x")
        assert ctx.calls_made == 4

    def test_consume_over_budget_raises(self):
        ctx = j.RunContext()
        for _ in range(4):
            ctx.consume("x")
        with pytest.raises(j.JudgmentError, match="call budget exceeded"):
            ctx.consume("x")

    def test_all_four_calls_share_one_context(self, monkeypatch):
        ctx = j.RunContext()
        _queue(monkeypatch, [
            '{"anomalies": [], "unassigned_candidates": [], "stale_assigned": []}',
            '{"suggestions": []}',
            '{"ops": []}',
            '{"sequence": [{"id": "A", "start": "13:00", "end": "13:30", "zone": "any"}]}',
        ])
        j.audit_pipeline({"pool_items": []}, ctx)
        j.suggest_digest({"suggested": []}, {}, ctx)
        j.adjust_freetext("drop nothing", {"assigned": []}, ctx)
        j.propose_sequence([{"id": "A"}], {}, [], ctx)
        assert ctx.calls_made == 4
        with pytest.raises(j.JudgmentError):
            j.audit_pipeline({"pool_items": []}, ctx)


# ---------------------------------------------------------------------------
# JSON extraction
# ---------------------------------------------------------------------------

class TestExtractJson:
    def test_bare_json(self):
        assert j._extract_json('{"a": 1}') == {"a": 1}

    def test_fenced_json(self):
        raw = "here you go:\n```json\n{\"a\": 1}\n```\n"
        assert j._extract_json(raw) == {"a": 1}

    def test_embedded_json_in_prose(self):
        raw = "Sure! {\"a\": 1} — hope that helps."
        assert j._extract_json(raw) == {"a": 1}

    def test_no_json_raises(self):
        with pytest.raises(ValueError):
            j._extract_json("no json here")


# ---------------------------------------------------------------------------
# Call #1 — audit_pipeline
# ---------------------------------------------------------------------------

class TestAuditPipeline:
    def test_accept_valid(self, monkeypatch):
        _queue(monkeypatch, [
            '{"anomalies": [], "unassigned_candidates": '
            '[{"name": "A", "path": "p/A.md", "reason": "crit, no deadline"}], '
            '"stale_assigned": []}'
        ])
        report = j.audit_pipeline({"pool_items": []})
        assert report["unassigned_candidates"][0]["name"] == "A"

    def test_reject_missing_key_then_raise(self, monkeypatch):
        calls = _queue(monkeypatch, [
            '{"anomalies": []}',  # missing required keys, both attempts
            '{"anomalies": []}',
        ])
        with pytest.raises(j.JudgmentError):
            j.audit_pipeline({"pool_items": []})
        assert calls["n"] == 2  # exactly one retry

    def test_retry_recovers(self, monkeypatch):
        _queue(monkeypatch, [
            'not json at all',
            '{"anomalies": [], "unassigned_candidates": [], "stale_assigned": []}',
        ])
        report = j.audit_pipeline({"pool_items": []})
        assert report["anomalies"] == []

    def test_reject_over_5_unassigned_candidates(self, monkeypatch):
        many = [{"name": f"n{i}", "path": f"p{i}", "reason": "x"} for i in range(6)]
        import json as _json
        bad = _json.dumps({"anomalies": [], "unassigned_candidates": many, "stale_assigned": []})
        _queue(monkeypatch, [bad, bad])
        with pytest.raises(j.JudgmentError):
            j.audit_pipeline({"pool_items": []})


class TestAuditPromptContract:
    def test_consumes_structural_pool_and_preserves_assigned_only_planning(self):
        prompt = j.AUDIT_SYSTEM_PROMPT
        for marker in (
            "structurally admitted",
            "already-supplied pool",
            "exclude anything already assigned",
            "never add unassigned work directly to today's plan",
            "non-blocking upstream assignment nudge",
        ):
            assert marker in prompt

    def test_renders_independent_reasons_in_deterministic_non_composite_order(self):
        prompt = j.AUDIT_SYSTEM_PROMPT
        for marker in (
            "deadline, urgency, return, stable name",
            "deadline, urgency, and return as separate reasons",
            "not a composite winner",
            "up to five",
        ):
            assert marker in prompt

    def test_does_not_restore_stale_threshold_or_score_authority(self):
        prompt = j.AUDIT_SYSTEM_PROMPT
        assert "urgency 4 (\"crit\"), OR a deadline within 7 days, OR overdue" not in prompt
        assert "Rank by urgency desc" not in prompt
        assert "priority_score" not in prompt


# ---------------------------------------------------------------------------
# Call #2 — suggest_digest
# ---------------------------------------------------------------------------

class TestSuggestDigest:
    def test_accept_valid(self, monkeypatch):
        _queue(monkeypatch, [
            '{"suggestions": [{"id": "Beta", "reason": "summit due soon", "tier": "summit"}]}'
        ])
        out = j.suggest_digest({"suggested": [{"name": "Beta"}]}, {})
        assert len(out["suggestions"]) == 1

    def test_reject_over_5_suggestions(self, monkeypatch):
        import json as _json
        six = [{"id": f"i{i}", "reason": "x", "tier": "other"} for i in range(6)]
        _queue(monkeypatch, [
            _json.dumps({"suggestions": six}),
            _json.dumps({"suggestions": six}),
        ])
        with pytest.raises(j.JudgmentError):
            j.suggest_digest({"suggested": []}, {})

    def test_reject_bad_tier(self, monkeypatch):
        _queue(monkeypatch, [
            '{"suggestions": [{"id": "A", "reason": "x", "tier": "bogus"}]}',
            '{"suggestions": [{"id": "A", "reason": "x", "tier": "other"}]}',
        ])
        out = j.suggest_digest({"suggested": []}, {})
        assert out["suggestions"][0]["tier"] == "other"

    def test_reject_reason_too_long(self, monkeypatch):
        import json as _json
        long_reason = "x" * 141
        _queue(monkeypatch, [
            _json.dumps({"suggestions": [{"id": "A", "reason": long_reason, "tier": "other"}]}),
            _json.dumps({"suggestions": [{"id": "A", "reason": "short", "tier": "other"}]}),
        ])
        out = j.suggest_digest({"suggested": []}, {})
        assert out["suggestions"][0]["reason"] == "short"


# ---------------------------------------------------------------------------
# Call #3 — adjust_freetext
# ---------------------------------------------------------------------------

class TestAdjustFreetext:
    def test_accept_valid(self, monkeypatch):
        _queue(monkeypatch, [
            '{"ops": [{"op": "complete", "id": "Beta", "args": {}}]}'
        ])
        plan = j.adjust_freetext("mark Beta done", {"assigned": [{"name": "Beta"}]})
        assert plan["ops"][0]["op"] == "complete"

    def test_reject_bad_op(self, monkeypatch):
        _queue(monkeypatch, [
            '{"ops": [{"op": "explode", "id": "Beta", "args": {}}]}',
            '{"ops": []}',
        ])
        plan = j.adjust_freetext("do something weird", {"assigned": []})
        assert plan["ops"] == []

    def test_retime_requires_blocks_arg(self, monkeypatch):
        _queue(monkeypatch, [
            '{"ops": [{"op": "retime", "id": "Beta", "args": {}}]}',
            '{"ops": [{"op": "retime", "id": "Beta", "args": {"blocks": 2}}]}',
        ])
        plan = j.adjust_freetext("2 blocks for Beta", {"assigned": [{"name": "Beta"}]})
        assert plan["ops"][0]["args"]["blocks"] == 2

    def test_empty_ops_is_valid(self, monkeypatch):
        _queue(monkeypatch, ['{"ops": []}'])
        plan = j.adjust_freetext("do nothing sensible", {"assigned": []})
        assert plan["ops"] == []


# ---------------------------------------------------------------------------
# Call #4 — propose_sequence
# ---------------------------------------------------------------------------

def test_metadata_placement_instruction_carries_feedback_rules():
    instruction = j.placement_semantic_instruction(
        [
            {"name": "Professional Development"},
            {
                "name": "Career Ops Pipeline",
                "relates_to": "[[Professional Development]]",
            },
            {"name": "Systems A", "tags": ["systems"]},
            {"name": "Systems B", "tags": ["#systems"]},
        ],
        [
            {"Block": "Foods Dinner", "Type": "window"},
            {"Block": "Dinner at Tribeca Tavern", "source": "calendar",
             "Start": "18:30", "End": "20:00"},
        ],
    )
    assert "Career Ops Pipeline" in instruction
    assert "Professional Development" in instruction
    assert "systems" in instruction
    assert "Foods Dinner" in instruction
    assert "Dinner at Tribeca Tavern" in instruction


def test_calendar_companion_span_is_the_only_duration_override():
    proposal = {
        "sequence": [{"id": "Dinner prep", "start": "18:30", "end": "20:00", "zone": "any"}],
        "overlap_grants": [{
            "primary_id": "Dinner prep", "companion_id": "Dinner reservation",
            "primary_interval": {"start": "18:30", "end": "20:00"},
            "companion_interval": {"start": "18:30", "end": "20:00"},
            "reason": "same dinner activity", "planning_config_fingerprint": "fp",
        }],
    }
    j._validate_sequence_proposal(
        proposal,
        config={"planning_config_fingerprint": "fp"},
        assigned=[{"name": "Dinner prep", "blocks": 1}],
        anchored_blocks=[{"Block": "Dinner reservation", "source": "calendar",
                          "Start": "18:30", "End": "20:00"}],
    )

class TestProposeSequence:
    def test_accept_valid(self, monkeypatch):
        _queue(monkeypatch, [
            '{"sequence": [{"id": "Minting", "start": "13:00", "end": "14:00", "zone": "work_hours"}]}'
        ])
        out = j.propose_sequence([{"id": "Minting"}], {}, [])
        assert out["sequence"][0]["start"] == "13:00"

    def test_out_of_order_rows_normalized_not_rejected(self, monkeypatch):
        # T11 live 502 (2026-07-14): mis-ordered but otherwise valid proposal
        # burned the retry. Order is derivable — sort, don't reject.
        _queue(monkeypatch, [
            '{"sequence": ['
            '{"id": "B", "start": "15:00", "end": "16:00", "zone": "any"},'
            '{"id": "A", "start": "09:30", "end": "10:00", "zone": "any"}]}'
        ])
        out = j.propose_sequence([{"id": "A"}, {"id": "B"}], {}, [])
        assert [r["id"] for r in out["sequence"]] == ["A", "B"]

    def test_reject_inflated_duration_without_retry(self, monkeypatch):
        # Shakedown 2026-07-14 (Press 75 min): assigned items are sized by their
        # blocks field (default 1 block = 30 min); a longer span is rejected.
        _queue(monkeypatch, [
            '{"sequence": [{"id": "Press", "start": "16:00", "end": "17:15", "zone": "any"}]}',
            '{"sequence": [{"id": "Press", "start": "16:00", "end": "16:30", "zone": "any"}]}',
        ])
        with pytest.raises(j.JudgmentError, match="after 1 attempt"):
            j.propose_sequence([{"name": "Press", "path": "P/Press.md"}], {}, [])

    def test_blocks_field_sets_duration_budget(self, monkeypatch):
        _queue(monkeypatch, [
            '{"sequence": [{"id": "Deep Work", "start": "13:00", "end": "14:30", "zone": "any"}]}'
        ])
        out = j.propose_sequence(
            [{"name": "Deep Work", "path": "P/DW.md", "blocks": 3}], {}, [])
        assert out["sequence"][0]["end"] == "14:30"

    def test_shortened_duration_accepted(self, monkeypatch):
        # Cap-only: midnight-rule shortening of tail items is legal.
        _queue(monkeypatch, [
            '{"sequence": [{"id": "Deep Work", "start": "23:15", "end": "23:45", "zone": "any"}]}'
        ])
        out = j.propose_sequence(
            [{"name": "Deep Work", "path": "P/DW.md", "blocks": 3}], {}, [])
        assert out["sequence"][0]["end"] == "23:45"

    def test_midnight_wrap_is_clamped_same_day_without_retry(self, monkeypatch):
        # T11 live 2026-07-17: Night Routine 23:00-00:00 is a valid late
        # placement intent, but the UI/commit contract is same-day HH:MM.
        # Normalize it to the last same-day minute instead of burning retry 2.
        _queue(monkeypatch, [
            '{"sequence": [{"id": "Night Routine", "start": "23:00", '
            '"end": "00:00", "zone": "evening"}]}'
        ])
        out = j.propose_sequence([], {}, [{"Block": "Night Routine"}])
        assert out["sequence"][0]["end"] == "23:59"

    def test_prompt_grants_task_task_overlap_and_bundling(self):
        # T29: the prompt must invite reasoned task-task overlap (errand
        # ride-alongs) and bundling into named context blocks, not only
        # primary × anchored-companion grants.
        p = j.SEQUENCE_SYSTEM_PROMPT
        assert "TASK BUNDLING" in p
        assert "context block" in p
        assert "errand" in p

    def test_task_task_grant_round_trips(self, monkeypatch):
        # T29 contract pin: overlap_grants accepts two movable-task ids —
        # the grant schema is id-agnostic and must stay that way.
        _queue(monkeypatch, [
            '{"sequence": ['
            '{"id": "Haircut", "start": "14:00", "end": "14:30", "zone": "any"},'
            '{"id": "Return burr", "start": "14:00", "end": "14:30", "zone": "any"}],'
            '"overlap_grants": [{'
            '"primary_id": "Return burr", "companion_id": "Haircut",'
            '"primary_interval": {"start": "14:00", "end": "14:30"},'
            '"companion_interval": {"start": "14:00", "end": "14:30"},'
            '"reason": "driving errand rides the Haircut trip",'
            '"planning_config_fingerprint": "fp"}]}'
        ])
        out = j.propose_sequence([{"id": "Haircut"}, {"id": "Return burr"}], {}, [])
        assert out["overlap_grants"][0]["companion_id"] == "Haircut"
        assert out["overlap_grants"][0]["primary_id"] == "Return burr"

    def test_reject_bad_hhmm(self, monkeypatch):
        _queue(monkeypatch, [
            '{"sequence": [{"id": "A", "start": "1pm", "end": "2pm", "zone": "any"}]}',
            '{"sequence": [{"id": "A", "start": "13:00", "end": "14:00", "zone": "any"}]}',
        ])
        with pytest.raises(j.JudgmentError, match="after 1 attempt"):
            j.propose_sequence([{"id": "A"}], {}, [])

    def test_reject_end_before_start(self, monkeypatch):
        _queue(monkeypatch, [
            '{"sequence": [{"id": "A", "start": "14:00", "end": "13:00", "zone": "any"}]}',
            '{"sequence": [{"id": "A", "start": "13:00", "end": "14:00", "zone": "any"}]}',
        ])
        with pytest.raises(j.JudgmentError, match="after 1 attempt"):
            j.propose_sequence([{"id": "A"}], {}, [])

    def test_reject_empty_sequence(self, monkeypatch):
        _queue(monkeypatch, [
            '{"sequence": []}',
            '{"sequence": [{"id": "A", "start": "13:00", "end": "13:30", "zone": "any"}]}',
        ])
        with pytest.raises(j.JudgmentError, match="after 1 attempt"):
            j.propose_sequence([{"id": "A"}], {}, [])

    def test_reject_morning_workout(self, monkeypatch):
        """The standing rule: a workout block before 12:00 fails validation
        even though it's syntactically well-formed — this is a VALIDATION
        failure, not just a prompt instruction."""
        _queue(monkeypatch, [
            '{"sequence": [{"id": "Morning Workout", "start": "07:00", "end": "07:30", "zone": "any"}]}',
            '{"sequence": [{"id": "Morning Workout", "start": "17:00", "end": "17:30", "zone": "evening"}]}',
        ])
        with pytest.raises(j.JudgmentError, match="after 1 attempt"):
            j.propose_sequence([{"id": "Morning Workout"}], {}, [])

    def test_reject_morning_workout_no_retry_recovery_raises(self, monkeypatch):
        import json as _json
        bad = _json.dumps({
            "sequence": [{"id": "Workout", "start": "08:00", "end": "08:30", "zone": "any"}]
        })
        _queue(monkeypatch, [bad, bad])
        with pytest.raises(j.JudgmentError, match="before noon is forbidden"):
            j.propose_sequence([{"id": "Workout"}], {}, [])

    def test_start_before_live_anchor_accepted_no_retry(self, monkeypatch):
        """2026-07-21 (Adam, T14 run): a past-anchor row no longer fails schema
        validation — each schema retry is a BILLED call (the 07-21 run burned
        2 on 'Minting' at 00:30). The proposal passes through; downstream
        validate_sequence surfaces the soft placement warning instead."""
        _queue(monkeypatch, [
            '{"sequence": [{"id": "A", "start": "07:45", "end": "08:15", "zone": "any"}]}',
        ])
        out = j.propose_sequence([{"id": "A"}], {"time": {"anchor": "16:00"}}, [])
        assert out["sequence"][0]["start"] == "07:45"

    def test_prompt_gives_every_item_explicit_earliest_start(self, monkeypatch):
        """G32: make the live anchor local to every item so the first proposal
        does not treat a before_work/noon preference as permission to use past time."""
        captured = {}

        async def fake_run_query(system_prompt, user_prompt):
            captured["system"] = system_prompt
            captured["user"] = user_prompt
            return (
                '{"sequence": [{"id": "Press", "start": "14:45", '
                '"end": "15:15", "zone": "before_work"}]}'
            )

        monkeypatch.setattr(j, "_run_query", fake_run_query)
        j.propose_sequence(
            [{"id": "Press", "zone": "before_work"}],
            {"time": {"anchor": "14:45"}},
            [],
        )

        assert '"earliest_start": "14:45"' in captured["user"]
        assert "12:00 is invalid" in captured["user"]
        assert "start >= earliest_start" in captured["user"]

    def test_anchor_absent_no_past_check(self, monkeypatch):
        _queue(monkeypatch, [
            '{"sequence": [{"id": "A", "start": "07:45", "end": "08:15", "zone": "any"}]}'
        ])
        out = j.propose_sequence([{"id": "A"}], {}, [])
        assert out["sequence"][0]["start"] == "07:45"

    def test_press_before_work_exception_allowed(self, monkeypatch):
        config = {"presets": [{"name": "Press", "zone": "before_work"}]}
        _queue(monkeypatch, [
            '{"sequence": [{"id": "Press", "start": "06:30", "end": "07:00", "zone": "before_work"}]}'
        ])
        out = j.propose_sequence([{"id": "Press"}], config, [])
        assert out["sequence"][0]["start"] == "06:30"

    def test_other_workout_id_not_press_still_rejected(self, monkeypatch):
        config = {"presets": [{"name": "Press", "zone": "before_work"}]}
        import json as _json
        bad = _json.dumps({
            "sequence": [{"id": "Workout: Squats", "start": "06:30", "end": "07:00", "zone": "any"}]
        })
        _queue(monkeypatch, [bad, bad])
        with pytest.raises(j.JudgmentError, match="before noon is forbidden"):
            j.propose_sequence([{"id": "Workout: Squats"}], config, [])


# ---------------------------------------------------------------------------
# Launchd-hang forensics: stderr tail capture (2026-07-17)
# ---------------------------------------------------------------------------

class TestStderrTailCapture:
    """_run_query must surface the CLI child's stderr when a query fails —
    the 2026-07-17 launchd timeouts were undiagnosable because stderr was
    silently discarded by the SDK default."""

    def test_timeout_logs_stderr_tail(self, monkeypatch, caplog):
        import asyncio

        def fake_options(system_prompt, stderr_cb=None):
            # simulate the CLI child emitting stderr before wedging
            if stderr_cb is not None:
                stderr_cb("child booting")
                stderr_cb("child wedged on X")
            return object()

        async def fake_query(prompt, options):
            await asyncio.sleep(30)
            yield  # pragma: no cover

        monkeypatch.setattr(j, "_sdk_options", fake_options)
        monkeypatch.setattr(j, "query", fake_query)
        monkeypatch.setattr(j, "QUERY_TIMEOUT_S", 0.05)

        with caplog.at_level("WARNING"):
            with pytest.raises(asyncio.TimeoutError):
                asyncio.run(j._run_query_sdk("sys", "user"))
        joined = "\n".join(r.getMessage() for r in caplog.records)
        assert "stderr tail" in joined
        assert "child wedged on X" in joined

    def test_failure_with_no_stderr_says_so(self, monkeypatch, caplog):
        import asyncio

        monkeypatch.setattr(j, "_sdk_options", lambda sp, stderr_cb=None: object())

        async def fake_query(prompt, options):
            await asyncio.sleep(30)
            yield  # pragma: no cover

        monkeypatch.setattr(j, "query", fake_query)
        monkeypatch.setattr(j, "QUERY_TIMEOUT_S", 0.05)

        with caplog.at_level("WARNING"):
            with pytest.raises(asyncio.TimeoutError):
                asyncio.run(j._run_query_sdk("sys", "user"))
        joined = "\n".join(r.getMessage() for r in caplog.records)
        assert "no stderr" in joined

    def test_success_logs_nothing(self, monkeypatch, caplog):
        import asyncio

        monkeypatch.setattr(j, "_sdk_options", lambda sp, stderr_cb=None: object())

        async def fake_query(prompt, options):
            from claude_agent_sdk import AssistantMessage, TextBlock
            yield AssistantMessage(content=[TextBlock(text="ok")], model="m")

        monkeypatch.setattr(j, "query", fake_query)

        with caplog.at_level("WARNING"):
            out = asyncio.run(j._run_query_sdk("sys", "user"))
        assert out == "ok"
        assert "stderr" not in "\n".join(r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------------------
# OpenRouter provider (2026-07-17 — judgment off the Claude Agent SDK)
# ---------------------------------------------------------------------------

class TestOpenRouterProvider:
    def test_default_provider_is_openrouter(self):
        assert j.PROVIDER == "openrouter"

    def test_key_env_wins(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-env")
        assert j._openrouter_key() == "sk-or-env"

    def test_key_falls_back_to_opencode_auth(self, monkeypatch, tmp_path):
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        f = tmp_path / "auth.json"
        f.write_text('{"openrouter": {"key": "sk-or-file"}}')
        monkeypatch.setattr(j, "_OPENCODE_AUTH", f)
        assert j._openrouter_key() == "sk-or-file"

    def test_key_missing_raises_judgment_error(self, monkeypatch, tmp_path):
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        monkeypatch.setattr(j, "_OPENCODE_AUTH", tmp_path / "absent.json")
        with pytest.raises(j.JudgmentError):
            j._openrouter_key()

    def _fake_client(self, monkeypatch, response_json, status=200, text=""):
        import httpx
        captured = {}

        class FakeResp:
            status_code = status
            # Mirrors the httpx surface the caller actually uses. `is_error`
            # and `text` matter because an HTTP failure is now reported with
            # OpenRouter's own reason string rather than httpx's status line
            # (2026-07-27: a bare "403 Forbidden" hid a key spend cap).
            is_error = status >= 400
            @property
            def text(self):
                return text
            def raise_for_status(self):
                if status >= 400:
                    raise httpx.HTTPStatusError(
                        f"Server error '{status}'", request=None, response=None
                    )
            def json(self):
                return response_json

        class FakeClient:
            def __init__(self, **kw): captured["timeout"] = kw.get("timeout")
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def post(self, url, json=None, headers=None):
                captured["url"] = url
                captured["payload"] = json
                captured["headers"] = headers
                return FakeResp()

        monkeypatch.setattr(j.httpx, "AsyncClient", FakeClient)
        return captured

    def test_openrouter_call_extracts_content(self, monkeypatch):
        import asyncio
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
        cap = self._fake_client(
            monkeypatch,
            {"choices": [{"message": {"content": '{"ok": 1}'}}]},
        )
        out = asyncio.run(j._run_query_openrouter(j.SEQUENCE_SYSTEM_PROMPT, "user prompt"))
        assert out == '{"ok": 1}'
        assert cap["payload"]["model"] == j.OPENROUTER_MODEL
        assert cap["payload"]["messages"][0] == {"role": "system", "content": j.SEQUENCE_SYSTEM_PROMPT}
        assert cap["payload"]["messages"][1] == {"role": "user", "content": "user prompt"}
        assert cap["headers"]["Authorization"] == "Bearer sk-or-test"
        assert cap["timeout"] == j.QUERY_TIMEOUT_S

    def test_missing_model_env_uses_qualified_luna_default(self):
        assert j._configured_openrouter_model({}) == "openai/gpt-5.6-luna"
        assert j._configured_openrouter_model({
            "TDTB_JUDGMENT_MODEL": "openrouter/deepseek/deepseek-v4-pro",
        }) == "openrouter/deepseek/deepseek-v4-pro"

    def test_openrouter_payload_requires_strict_schema_and_provider_support(self, monkeypatch):
        import asyncio
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
        monkeypatch.setattr(j, "OPENROUTER_MODEL", "deepseek/deepseek-v4-pro")
        cap = self._fake_client(
            monkeypatch,
            {"choices": [{"message": {"content": '{"sequence": []}'}}]},
        )

        asyncio.run(j._run_query_openrouter(j.SEQUENCE_SYSTEM_PROMPT, "user prompt"))

        response_format = cap["payload"]["response_format"]
        assert response_format["type"] == "json_schema"
        assert response_format["json_schema"]["name"] == "sequence_proposal"
        assert response_format["json_schema"]["strict"] is True
        assert response_format["json_schema"]["schema"]["additionalProperties"] is False
        # Non-OpenAI models keep the near-deterministic temperature.
        assert cap["payload"]["temperature"] == 0.2

    def test_openai_models_omit_temperature(self, monkeypatch):
        """GPT-5-family endpoints reject temperature, and require_parameters
        turns that into a 404 "no endpoints found" for the WHOLE request —
        verified live 2026-07-27 (openai/gpt-5.6-luna succeeds the moment
        temperature is dropped, fails with it present)."""
        import asyncio
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
        monkeypatch.setattr(j, "OPENROUTER_MODEL", "openai/gpt-5.6-luna")
        cap = self._fake_client(
            monkeypatch,
            {"choices": [{"message": {"content": '{"sequence": []}'}}]},
        )
        asyncio.run(j._run_query_openrouter(j.SEQUENCE_SYSTEM_PROMPT, "user prompt"))
        assert "temperature" not in cap["payload"]
        # The strict-schema pin survives — that is what carries determinism
        # for these models instead.
        assert cap["payload"]["response_format"]["json_schema"]["strict"] is True
        assert cap["payload"]["provider"] == {"require_parameters": True}
        assert cap["payload"]["provider"] == {"require_parameters": True}

    def test_m3_payload_explicitly_disables_reasoning(self, monkeypatch):
        import asyncio
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
        monkeypatch.setattr(j, "OPENROUTER_MODEL", "minimax/minimax-m3")
        cap = self._fake_client(
            monkeypatch,
            {"choices": [{"message": {"content": '{"sequence": []}'}}]},
        )

        asyncio.run(j._run_query_openrouter(j.SEQUENCE_SYSTEM_PROMPT, "user prompt"))

        assert cap["payload"]["reasoning"] == {"effort": "none"}

    def test_non_m3_payload_does_not_request_reasoning_controls(self, monkeypatch):
        import asyncio
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
        monkeypatch.setattr(j, "OPENROUTER_MODEL", "deepseek/deepseek-v4-flash")
        cap = self._fake_client(
            monkeypatch,
            {"choices": [{"message": {"content": '{"sequence": []}'}}]},
        )

        asyncio.run(j._run_query_openrouter(j.SEQUENCE_SYSTEM_PROMPT, "user prompt"))

        assert "reasoning" not in cap["payload"]

    def test_openrouter_error_body_raises(self, monkeypatch):
        import asyncio
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
        self._fake_client(monkeypatch, {"error": {"message": "rate limited", "code": 429}})
        with pytest.raises(Exception) as ei:
            asyncio.run(j._run_query_openrouter(j.SEQUENCE_SYSTEM_PROMPT, "u"))
        assert "rate limited" in str(ei.value)

    def test_http_error_surfaces_openrouter_reason_not_just_the_status(self, monkeypatch):
        """2026-07-27: a live 403 reported only "403 Forbidden" plus a link to
        MDN, and the actual cause — the key's monthly spend cap — was only
        found by querying OpenRouter's /key endpoint by hand. The body says
        why; it must reach the operator."""
        import asyncio
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
        self._fake_client(
            monkeypatch,
            {},
            status=403,
            text='{"error":{"message":"Key limit exceeded","code":403}}',
        )
        with pytest.raises(Exception) as ei:
            asyncio.run(j._run_query_openrouter(j.SEQUENCE_SYSTEM_PROMPT, "u"))
        msg = str(ei.value)
        assert "403" in msg
        assert "Key limit exceeded" in msg

    def test_http_error_without_a_body_still_names_the_status(self, monkeypatch):
        import asyncio
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
        self._fake_client(monkeypatch, {}, status=502, text="")
        with pytest.raises(Exception) as ei:
            asyncio.run(j._run_query_openrouter(j.SEQUENCE_SYSTEM_PROMPT, "u"))
        assert "502" in str(ei.value)

    def test_http_5xx_and_429_classify_transient(self):
        assert j._is_transient(Exception("Server error '502 Bad Gateway'"))
        assert j._is_transient(Exception("429 Too Many Requests"))

    def test_run_query_dispatches_by_provider(self, monkeypatch):
        import asyncio
        seen = {}

        async def fake_or(sp, up):
            seen["provider"] = "openrouter"
            return "x"

        monkeypatch.setattr(j, "_run_query_openrouter", fake_or)
        monkeypatch.setattr(j, "PROVIDER", "openrouter")
        assert asyncio.run(j._run_query("s", "u")) == "x"
        assert seen["provider"] == "openrouter"


class TestOpenRouterWallClock:
    def test_slow_trickling_request_hits_hard_ceiling(self, monkeypatch):
        import asyncio

        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
        monkeypatch.setattr(j, "QUERY_TIMEOUT_S", 0.05)

        class FakeClient:
            def __init__(self, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def post(self, *args, **kwargs):
                await asyncio.sleep(30)

        monkeypatch.setattr(j.httpx, "AsyncClient", FakeClient)

        with pytest.raises(asyncio.TimeoutError):
            asyncio.run(j._run_query_openrouter(j.SEQUENCE_SYSTEM_PROMPT, "user"))
