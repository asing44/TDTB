"""Tests for operator-editable judgment request PARAMETERS.

The repo-root ``judgment-model.txt`` selector gained optional ``key: value``
lines after the model id. They tune a SMALL, safe set of OpenRouter request
knobs (reasoning_effort, max_tokens, temperature, top_p, seed, stream) for
whichever model is active. With no keys set, the payload must be byte-identical
to the pre-parameter behavior.

Everything here is offline: the one client seam is stubbed, no provider is
contacted, and no credential or network value is read.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import judgment as j  # noqa: E402

SELECTOR_NAME = "judgment-model.txt"
MODEL = "deepseek/deepseek-v4.1-flash"


def _selector(tmp_path: Path, content: str) -> Path:
    path = tmp_path / SELECTOR_NAME
    path.write_text(content, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# File format
# ---------------------------------------------------------------------------

class TestSelectorFormat:
    def test_single_line_file_still_parses_with_no_params(self, tmp_path):
        path = _selector(tmp_path, f"{MODEL}\n")
        assert j._parse_judgment_selector(path.read_text(), path) == (MODEL, {})

    def test_comments_and_blank_lines_are_ignored(self, tmp_path):
        path = _selector(
            tmp_path,
            f"\n# a leading comment\n{MODEL}\n\n# reasoning_effort: high\n"
            "# max_tokens: 8192\n\n",
        )
        assert j._parse_judgment_selector(path.read_text(), path) == (MODEL, {})

    def test_indented_comment_and_key_are_accepted(self, tmp_path):
        path = _selector(tmp_path, f"{MODEL}\n   # note\n  seed: 7\n")
        assert j._parse_judgment_selector(path.read_text(), path) == (
            MODEL, {"seed": 7}
        )

    def test_colon_and_equals_separators_both_work(self, tmp_path):
        path = _selector(tmp_path, f"{MODEL}\nmax_tokens: 8192\nseed=7\n")
        assert j._parse_judgment_selector(path.read_text(), path) == (
            MODEL, {"max_tokens": 8192, "seed": 7}
        )

    def test_all_keys_parse_with_expected_types(self, tmp_path):
        path = _selector(
            tmp_path,
            f"{MODEL}\n"
            "reasoning_effort: xhigh\n"
            "max_tokens: 4096\n"
            "temperature: 0.7\n"
            "top_p: 0.9\n"
            "seed: 7\n"
            "stream: true\n",
        )
        assert j._parse_judgment_selector(path.read_text(), path) == (
            MODEL,
            {
                "reasoning_effort": "xhigh",
                "max_tokens": 4096,
                "temperature": 0.7,
                "top_p": 0.9,
                "seed": 7,
                "stream": True,
            },
        )

    def test_missing_file_yields_no_params(self, tmp_path):
        absent = tmp_path / "absent.txt"
        assert j._configured_judgment_params({}, model_file=absent) == {}

    def test_params_resolve_regardless_of_model_env_override(self, tmp_path):
        # TDTB_JUDGMENT_MODEL chooses the model, not the knobs: an env-overridden
        # model still follows the file's parameters.
        path = _selector(tmp_path, f"{MODEL}\nmax_tokens: 2048\n")
        env = {"TDTB_JUDGMENT_MODEL": "openai/gpt-6-luna"}
        assert j._configured_openrouter_model(env, model_file=path) == (
            "openai/gpt-6-luna"
        )
        assert j._configured_judgment_params(env, model_file=path) == {
            "max_tokens": 2048
        }

    def test_canonical_selector_ships_no_active_params(self):
        path = Path(j.__file__).resolve().parents[1] / SELECTOR_NAME
        model, params = j._parse_judgment_selector(path.read_text(), path)
        assert j._MODEL_ID_RE.fullmatch(model)
        assert params == {}


# ---------------------------------------------------------------------------
# Invalid input fails loudly and never echoes the value
# ---------------------------------------------------------------------------

class TestSelectorRejectsBadInput:
    @pytest.mark.parametrize(
        "content, expected_fragment",
        [
            (f"{MODEL}\nbogus_key: 1\n", "unknown judgment parameter"),
            (f"{MODEL}\nmax_tokens: 100\nmax_tokens: 200\n", "duplicate"),
            (f"{MODEL}\nmax_tokens: 0\n", "invalid value"),
            (f"{MODEL}\nmax_tokens: 131073\n", "invalid value"),
            (f"{MODEL}\nmax_tokens: 8.5\n", "invalid value"),
            (f"{MODEL}\nmax_tokens: -3\n", "invalid value"),
            (f"{MODEL}\nmax_tokens: 8_0\n", "invalid value"),
            (f"{MODEL}\ntemperature: -0.1\n", "invalid value"),
            (f"{MODEL}\ntemperature: 2.1\n", "invalid value"),
            (f"{MODEL}\ntemperature: nan\n", "invalid value"),
            (f"{MODEL}\ntop_p: 0.0\n", "invalid value"),
            (f"{MODEL}\ntop_p: 1.5\n", "invalid value"),
            (f"{MODEL}\nseed: abc\n", "invalid value"),
            (f"{MODEL}\nstream: yes\n", "invalid value"),
            (f"{MODEL}\nreasoning_effort: ultra\n", "invalid value"),
            (f"{MODEL}\nreasoning_effort: NONE\n", "invalid value"),
            # A second bare model-looking line (no separator).
            (f"{MODEL}\ngoogle/gemini-2.5-pro\n", "not a known judgment parameter"),
        ],
    )
    def test_rejects_without_echoing_value(self, tmp_path, content, expected_fragment):
        path = _selector(tmp_path, content)
        with pytest.raises(ValueError) as excinfo:
            j._parse_judgment_selector(path.read_text(), path)
        message = str(excinfo.value)
        assert SELECTOR_NAME in message
        assert expected_fragment in message

    @pytest.mark.parametrize(
        "content, sentinel",
        [
            (f"{MODEL}\nstream: yes\n", "yes"),
            (f"{MODEL}\nreasoning_effort: ultra\n", "ultra"),
            (f"{MODEL}\nmax_tokens: 8.5\n", "8.5"),
            (f"{MODEL}\nmax_tokens: 8_0\n", "8_0"),
            (f"{MODEL}\nseed: abc\n", "abc"),
            (f"{MODEL}\ngoogle/gemini-2.5-pro\n", "google"),
            (f"{MODEL}\nbogus_key: sentinelvalue\n", "sentinelvalue"),
        ],
    )
    def test_error_message_never_echoes_the_offending_value(
        self, tmp_path, content, sentinel
    ):
        path = _selector(tmp_path, content)
        with pytest.raises(ValueError) as excinfo:
            j._parse_judgment_selector(path.read_text(), path)
        assert sentinel not in str(excinfo.value)

    def test_duplicate_key_names_the_key_not_a_value(self, tmp_path):
        path = _selector(tmp_path, f"{MODEL}\nseed: 424242\nseed: 515151\n")
        with pytest.raises(ValueError) as excinfo:
            j._parse_judgment_selector(path.read_text(), path)
        message = str(excinfo.value)
        assert "seed" in message
        assert "515151" not in message

    def test_missing_model_id_still_errors(self, tmp_path):
        path = _selector(tmp_path, "# only a comment\n")
        with pytest.raises(ValueError, match="selector is empty"):
            j._parse_judgment_selector(path.read_text(), path)

    def test_invalid_model_line_reports_model_error_not_param_error(self, tmp_path):
        path = _selector(tmp_path, "not-a-model\nmax_tokens: 8\n")
        with pytest.raises(ValueError, match="not a valid OpenRouter model id"):
            j._parse_judgment_selector(path.read_text(), path)

    def test_boundary_values_are_accepted(self, tmp_path):
        floor = _selector(
            tmp_path,
            f"{MODEL}\nmax_tokens: 1\ntemperature: 0\ntop_p: 0.0001\n"
            "reasoning_effort: none\nstream: false\n",
        )
        _model, params = j._parse_judgment_selector(floor.read_text(), floor)
        assert params == {
            "max_tokens": 1,
            "temperature": 0,
            "top_p": 0.0001,
            "reasoning_effort": "none",
            "stream": False,
        }
        ceiling = _selector(
            tmp_path,
            f"{MODEL}\nmax_tokens: {j._MAX_TOKENS_CEILING}\ntemperature: 2\n"
            "top_p: 1\nreasoning_effort: xhigh\n",
        )
        _model, params = j._parse_judgment_selector(ceiling.read_text(), ceiling)
        assert params == {
            "max_tokens": j._MAX_TOKENS_CEILING,
            "temperature": 2,
            "top_p": 1,
            "reasoning_effort": "xhigh",
        }


# ---------------------------------------------------------------------------
# Unset keys => byte-identical legacy payload
# ---------------------------------------------------------------------------

def _legacy_payload(model: str, prompt: str) -> dict:
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": "user prompt"},
        ],
        "response_format": j._response_format_for(prompt),
        "provider": {"require_parameters": True},
    }
    if not model.startswith("openai/"):
        payload["temperature"] = 0.2
    if model == "minimax/minimax-m3":
        payload["reasoning"] = {"effort": "none"}
    return payload


class TestEmptyParamsAreByteIdentical:
    @pytest.mark.parametrize(
        "model",
        [
            "deepseek/deepseek-v4.1-flash",
            "openai/gpt-6-luna",
            "minimax/minimax-m3",
            "google/gemini-2.5-pro:free",
        ],
    )
    def test_no_params_matches_legacy_payload(self, model):
        prompt = j.SEQUENCE_SYSTEM_PROMPT
        built = j._build_openrouter_payload(prompt, "user prompt", model=model, params={})
        legacy = _legacy_payload(model, prompt)
        assert built == legacy
        # Same JSON bytes, including key order.
        assert json.dumps(built) == json.dumps(legacy)


# ---------------------------------------------------------------------------
# Params land in the payload (and explicit values win)
# ---------------------------------------------------------------------------

class TestParamsOverlay:
    def _payload(self, model, params):
        return j._build_openrouter_payload(
            j.SEQUENCE_SYSTEM_PROMPT, "user prompt", model=model, params=params
        )

    def test_each_key_maps_to_its_field(self):
        payload = self._payload(
            MODEL,
            {
                "reasoning_effort": "high",
                "max_tokens": 4096,
                "temperature": 0.7,
                "top_p": 0.9,
                "seed": 7,
            },
        )
        assert payload["reasoning"] == {"effort": "high"}
        assert payload["max_tokens"] == 4096
        assert payload["temperature"] == 0.7
        assert payload["top_p"] == 0.9
        assert payload["seed"] == 7
        # The strict-schema and provider pins are untouched.
        assert payload["response_format"]["json_schema"]["strict"] is True
        assert payload["provider"] == {"require_parameters": True}

    def test_explicit_temperature_wins_for_openai_models(self):
        payload = self._payload("openai/gpt-6-luna", {"temperature": 0.5})
        assert payload["temperature"] == 0.5

    def test_explicit_reasoning_effort_wins_over_m3_pin(self):
        payload = self._payload("minimax/minimax-m3", {"reasoning_effort": "low"})
        assert payload["reasoning"] == {"effort": "low"}

    def test_stream_false_adds_nothing(self):
        payload = self._payload(MODEL, {"stream": False})
        assert "stream" not in payload

    def test_stream_true_sets_flag(self):
        payload = self._payload(MODEL, {"stream": True})
        assert payload["stream"] is True


# ---------------------------------------------------------------------------
# Call path uses the startup snapshot
# ---------------------------------------------------------------------------

class TestCallPathUsesParams:
    def _fake_client(self, monkeypatch, response_json, status=200, text=""):
        captured = {}

        class FakeResp:
            status_code = status
            is_error = status >= 400
            text = property(lambda self: text)

            def json(self):
                return response_json

        class FakeClient:
            def __init__(self, *a, **kw):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def post(self, url, json=None, headers=None):
                captured["url"] = url
                captured["payload"] = json
                captured["headers"] = headers
                return FakeResp()

        monkeypatch.setattr(j.httpx, "AsyncClient", FakeClient)
        return captured

    def test_operator_params_are_sent(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
        monkeypatch.setattr(j, "OPENROUTER_MODEL", MODEL)
        monkeypatch.setattr(j, "JUDGMENT_PARAMS", {"max_tokens": 4096, "seed": 7})
        cap = self._fake_client(
            monkeypatch, {"choices": [{"message": {"content": '{"ok": 1}'}}]}
        )
        out = asyncio.run(j._run_query_openrouter(j.SEQUENCE_SYSTEM_PROMPT, "u"))
        assert out == '{"ok": 1}'
        assert cap["payload"]["max_tokens"] == 4096
        assert cap["payload"]["seed"] == 7

    def test_params_apply_to_env_overridden_model(self, monkeypatch):
        # The env override changes the model, not the knobs.
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
        monkeypatch.setattr(j, "OPENROUTER_MODEL", "openai/gpt-6-luna")
        monkeypatch.setattr(j, "JUDGMENT_PARAMS", {"max_tokens": 1024})
        cap = self._fake_client(
            monkeypatch, {"choices": [{"message": {"content": '{"ok": 1}'}}]}
        )
        asyncio.run(j._run_query_openrouter(j.SEQUENCE_SYSTEM_PROMPT, "u"))
        assert cap["payload"]["model"] == "openai/gpt-6-luna"
        assert cap["payload"]["max_tokens"] == 1024

    def test_stream_true_is_rejected_not_silently_ignored(self, monkeypatch):
        # Commit 1 boundary: until SSE reading ships, a stream request must fail
        # loudly rather than send stream:true and misparse a JSON response.
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
        monkeypatch.setattr(j, "OPENROUTER_MODEL", MODEL)
        monkeypatch.setattr(j, "JUDGMENT_PARAMS", {"stream": True})
        cap = self._fake_client(
            monkeypatch, {"choices": [{"message": {"content": '{"ok": 1}'}}]}
        )
        with pytest.raises(ValueError, match="stream is not supported yet"):
            asyncio.run(j._run_query_openrouter(j.SEQUENCE_SYSTEM_PROMPT, "u"))
        assert "payload" not in cap


# ---------------------------------------------------------------------------
# Startup snapshot, no hot reload
# ---------------------------------------------------------------------------

class TestStartupSnapshot:
    def test_params_are_a_module_snapshot(self, tmp_path, monkeypatch):
        before = j.JUDGMENT_PARAMS
        assert isinstance(before, dict)
        monkeypatch.setattr(j, "JUDGMENT_MODEL_FILE", tmp_path / SELECTOR_NAME)
        (tmp_path / SELECTOR_NAME).write_text(
            f"{MODEL}\nmax_tokens: 4096\n", encoding="utf-8"
        )
        assert j.JUDGMENT_PARAMS == before

    def test_params_are_not_rebuilt_from_the_selector_per_call(self, monkeypatch):
        monkeypatch.setattr(j, "JUDGMENT_PARAMS", {"seed": 7})
        assert j.JUDGMENT_PARAMS == {"seed": 7}
        assert j._configured_judgment_params({}, model_file=Path("/nonexistent")) == {}
