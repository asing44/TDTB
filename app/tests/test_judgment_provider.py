"""Tests for OpenRouter PROVIDER ROUTING keys in ``judgment-model.txt``.

The selector file gained a second family of ``key: value`` lines that map to
the OpenRouter ``provider`` object (order, only, ignore, quantizations, sort,
allow_fallbacks, data_collection, zdr, preferred latency/throughput). The hard
rules:

  * ``require_parameters`` stays ``True`` always and is NOT tunable — a
    ``provider_require_parameters`` key is rejected as unknown;
  * ``order``/``only``/``ignore`` may not contradict each other;
  * with NO ``provider_*`` keys set the payload's ``provider`` is exactly
    ``{"require_parameters": True}`` and the whole payload is byte-identical
    to the pre-provider behavior for every model.

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
GLM_MODEL = "z-ai/glm-5.3-flash"


def _selector(tmp_path: Path, content: str) -> Path:
    path = tmp_path / SELECTOR_NAME
    path.write_text(content, encoding="utf-8")
    return path


def _parse(content: str, tmp_path: Path):
    path = _selector(tmp_path, content)
    return j._parse_judgment_selector(path.read_text(), path)


# ---------------------------------------------------------------------------
# Parsing provider keys
# ---------------------------------------------------------------------------

class TestProviderKeysParse:
    def test_all_provider_keys_parse_with_expected_types(self, tmp_path):
        _model, params = _parse(
            f"{MODEL}\n"
            "provider_order: together,openai\n"
            "provider_only: together\n"
            "provider_ignore: deepinfra\n"
            "provider_quantizations: fp8,int8\n"
            "provider_sort: latency\n"
            "provider_allow_fallbacks: false\n"
            "provider_data_collection: deny\n"
            "provider_zdr: true\n"
            "provider_max_latency: 60\n"
            "provider_min_throughput: 20.5\n",
            tmp_path,
        )
        assert params == {
            "provider_order": ["together", "openai"],
            "provider_only": ["together"],
            "provider_ignore": ["deepinfra"],
            "provider_quantizations": ["fp8", "int8"],
            "provider_sort": "latency",
            "provider_allow_fallbacks": False,
            "provider_data_collection": "deny",
            "provider_zdr": True,
            "provider_max_latency": 60.0,
            "provider_min_throughput": 20.5,
        }

    def test_slug_lists_are_trimmed_and_lowercased(self, tmp_path):
        _model, params = _parse(
            f"{MODEL}\nprovider_order:  Together , Z-AI/FP16 \n", tmp_path
        )
        assert params["provider_order"] == ["together", "z-ai/fp16"]

    def test_every_provider_quantization_value_is_accepted(self, tmp_path):
        for quant in sorted(j._PROVIDER_QUANTIZATION_VALUES):
            _model, params = _parse(
                f"{MODEL}\nprovider_quantizations: {quant}\n", tmp_path
            )
            assert params["provider_quantizations"] == [quant]

    def test_boundary_numbers_accepted(self, tmp_path):
        _model, params = _parse(
            f"{MODEL}\nprovider_max_latency: 600\nprovider_min_throughput: 0.1\n",
            tmp_path,
        )
        assert params["provider_max_latency"] == 600.0
        assert params["provider_min_throughput"] == 0.1

    def test_provider_sort_and_data_collection_accept_documented_values(self, tmp_path):
        for sort in ("price", "throughput", "latency"):
            _model, params = _parse(f"{MODEL}\nprovider_sort: {sort}\n", tmp_path)
            assert params["provider_sort"] == sort
        for collection in ("allow", "deny"):
            _model, params = _parse(
                f"{MODEL}\nprovider_data_collection: {collection}\n", tmp_path
            )
            assert params["provider_data_collection"] == collection

    def test_reasoning_effort_max_is_accepted(self, tmp_path):
        _model, params = _parse(f"{MODEL}\nreasoning_effort: max\n", tmp_path)
        assert params["reasoning_effort"] == "max"

    def test_provider_keys_coexist_with_request_params(self, tmp_path):
        _model, params = _parse(
            f"{MODEL}\nreasoning_effort: max\nprovider_order: together\n", tmp_path
        )
        assert params == {
            "reasoning_effort": "max",
            "provider_order": ["together"],
        }


# ---------------------------------------------------------------------------
# Bad provider values fail loudly, naming the file and key, never the value
# ---------------------------------------------------------------------------

class TestProviderRejectsBadInput:
    @pytest.mark.parametrize(
        "key, value",
        [
            ("provider_order", "Together!"),
            ("provider_order", "together/two/many"),
            ("provider_order", "-leading-dash"),
            ("provider_only", "together, together"),
            ("provider_ignore", "together,"),
            ("provider_ignore", ","),
            ("provider_quantizations", "int3"),
            ("provider_quantizations", "int8, int8"),
            ("provider_sort", "cheapest"),
            ("provider_data_collection", "maybe"),
            ("provider_allow_fallbacks", "yes"),
            ("provider_zdr", "maybe"),
            ("provider_max_latency", "zero"),
            ("provider_max_latency", "-1.5"),
            ("provider_max_latency", "600.1"),
            ("provider_max_latency", "nan"),
            ("provider_min_throughput", "zero"),
            ("provider_min_throughput", "-5.5"),
            ("provider_min_throughput", "fast"),
        ],
    )
    def test_rejects_without_echoing_value(self, tmp_path, key, value):
        path = _selector(tmp_path, f"{MODEL}\n{key}: {value}\n")
        with pytest.raises(ValueError) as excinfo:
            j._parse_judgment_selector(path.read_text(), path)
        message = str(excinfo.value)
        assert SELECTOR_NAME in message
        assert key in message
        assert value not in message

    def test_bare_provider_key_names_the_key(self, tmp_path):
        path = _selector(tmp_path, f"{MODEL}\nprovider_order:\n")
        with pytest.raises(ValueError) as excinfo:
            j._parse_judgment_selector(path.read_text(), path)
        message = str(excinfo.value)
        assert "provider_order" in message

    def test_duplicate_provider_key_is_rejected(self, tmp_path):
        path = _selector(tmp_path, f"{MODEL}\nprovider_order: a\nprovider_order: b\n")
        with pytest.raises(ValueError) as excinfo:
            j._parse_judgment_selector(path.read_text(), path)
        assert "duplicate" in str(excinfo.value)
        assert "provider_order" in str(excinfo.value)


# ---------------------------------------------------------------------------
# require_parameters stays True and is not tunable
# ---------------------------------------------------------------------------

class TestRequireParametersNotTunable:
    def test_provider_require_parameters_is_rejected_as_unknown(self, tmp_path):
        path = _selector(
            tmp_path, f"{MODEL}\nprovider_require_parameters: false\n"
        )
        with pytest.raises(ValueError) as excinfo:
            j._parse_judgment_selector(path.read_text(), path)
        message = str(excinfo.value)
        assert "provider_require_parameters" in message
        assert "false" not in message

    def test_payload_always_pins_require_parameters(self):
        payload = j._build_openrouter_payload(
            j.SEQUENCE_SYSTEM_PROMPT,
            "u",
            model=MODEL,
            params={"provider_order": ["together"]},
        )
        assert payload["provider"]["require_parameters"] is True


# ---------------------------------------------------------------------------
# order / only / ignore must not contradict
# ---------------------------------------------------------------------------

class TestProviderContradictions:
    def test_only_and_ignore_overlap_names_both_keys(self, tmp_path):
        path = _selector(
            tmp_path,
            f"{MODEL}\nprovider_only: together\nprovider_ignore: together\n",
        )
        with pytest.raises(ValueError) as excinfo:
            j._parse_judgment_selector(path.read_text(), path)
        message = str(excinfo.value)
        assert "provider_only" in message
        assert "provider_ignore" in message

    def test_order_slug_in_ignore_names_both_keys(self, tmp_path):
        path = _selector(
            tmp_path,
            f"{MODEL}\nprovider_order: together,openai\n"
            "provider_ignore: openai\n",
        )
        with pytest.raises(ValueError) as excinfo:
            j._parse_judgment_selector(path.read_text(), path)
        message = str(excinfo.value)
        assert "provider_order" in message
        assert "provider_ignore" in message

    def test_order_subset_of_only_is_allowed(self, tmp_path):
        _model, params = _parse(
            f"{MODEL}\nprovider_only: together,openai\nprovider_order: openai\n",
            tmp_path,
        )
        assert params["provider_only"] == ["together", "openai"]
        assert params["provider_order"] == ["openai"]

    def test_disjoint_only_and_ignore_is_allowed(self, tmp_path):
        _model, params = _parse(
            f"{MODEL}\nprovider_only: together\nprovider_ignore: deepinfra\n",
            tmp_path,
        )
        assert params["provider_only"] == ["together"]
        assert params["provider_ignore"] == ["deepinfra"]


# ---------------------------------------------------------------------------
# Overlay: provider keys land in payload['provider'] with documented names
# ---------------------------------------------------------------------------

class TestProviderPayloadOverlay:
    def _payload(self, model, params):
        return j._build_openrouter_payload(
            j.SEQUENCE_SYSTEM_PROMPT, "u", model=model, params=params
        )

    def test_provider_fields_map_to_documented_names(self):
        payload = self._payload(
            MODEL,
            {
                "provider_order": ["together", "openai"],
                "provider_only": ["together"],
                "provider_ignore": ["deepinfra"],
                "provider_quantizations": ["fp8", "int8"],
                "provider_sort": "latency",
                "provider_allow_fallbacks": False,
                "provider_data_collection": "deny",
                "provider_zdr": True,
                "provider_max_latency": 60.0,
                "provider_min_throughput": 20.0,
            },
        )
        assert payload["provider"] == {
            "require_parameters": True,
            "order": ["together", "openai"],
            "only": ["together"],
            "ignore": ["deepinfra"],
            "quantizations": ["fp8", "int8"],
            "sort": "latency",
            "allow_fallbacks": False,
            "data_collection": "deny",
            "zdr": True,
            "preferred_max_latency": 60.0,
            "preferred_min_throughput": 20.0,
        }

    def test_max_effort_maps_to_reasoning_object(self):
        payload = self._payload(MODEL, {"reasoning_effort": "max"})
        assert payload["reasoning"] == {"effort": "max"}

    def test_provider_and_request_params_coexist(self):
        payload = self._payload(
            MODEL,
            {"reasoning_effort": "max", "provider_order": ["together"]},
        )
        assert payload["reasoning"] == {"effort": "max"}
        assert payload["provider"] == {
            "require_parameters": True,
            "order": ["together"],
        }


# ---------------------------------------------------------------------------
# No provider keys => provider pin unchanged (byte-identical payload)
# ---------------------------------------------------------------------------

def _legacy_payload(model: str, prompt: str) -> dict:
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": "u"},
        ],
        "response_format": j._response_format_for(prompt),
        "provider": {"require_parameters": True},
    }
    if not model.startswith("openai/"):
        payload["temperature"] = 0.2
    if model == "minimax/minimax-m3":
        payload["reasoning"] = {"effort": "none"}
    return payload


class TestNoProviderKeysLeavesProviderPinned:
    @pytest.mark.parametrize(
        "model",
        [
            "deepseek/deepseek-v4.1-flash",
            "openai/gpt-6-luna",
            "minimax/minimax-m3",
            "google/gemini-2.5-pro:free",
            GLM_MODEL,
        ],
    )
    def test_empty_params_provider_is_exactly_require_parameters(self, model):
        prompt = j.SEQUENCE_SYSTEM_PROMPT
        built = j._build_openrouter_payload(prompt, "u", model=model, params={})
        assert built["provider"] == {"require_parameters": True}
        assert built == _legacy_payload(model, prompt)
        assert json.dumps(built) == json.dumps(_legacy_payload(model, prompt))


# ---------------------------------------------------------------------------
# Call path: provider overrides apply to the active model, including env
# ---------------------------------------------------------------------------

class TestProviderCallPath:
    def _fake_client(self, monkeypatch, response_json):
        captured = {}

        class FakeResp:
            status_code = 200
            is_error = False
            text = ""

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
                captured["payload"] = json
                return FakeResp()

        monkeypatch.setattr(j.httpx, "AsyncClient", FakeClient)
        return captured

    def test_provider_overrides_are_sent(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
        monkeypatch.setattr(j, "OPENROUTER_MODEL", MODEL)
        monkeypatch.setattr(
            j,
            "JUDGMENT_PARAMS",
            {"reasoning_effort": "max", "provider_order": ["together"]},
        )
        cap = self._fake_client(
            monkeypatch, {"choices": [{"message": {"content": '{"ok": 1}'}}]}
        )
        out = asyncio.run(j._run_query_openrouter(j.SEQUENCE_SYSTEM_PROMPT, "u"))
        assert out == '{"ok": 1}'
        assert cap["payload"]["provider"] == {
            "require_parameters": True,
            "order": ["together"],
        }
        assert cap["payload"]["reasoning"] == {"effort": "max"}

    def test_provider_applies_to_env_overridden_model(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
        monkeypatch.setattr(j, "OPENROUTER_MODEL", GLM_MODEL)
        monkeypatch.setattr(
            j,
            "JUDGMENT_PARAMS",
            {
                "reasoning_effort": "low",
                "provider_order": ["together"],
                "provider_allow_fallbacks": False,
            },
        )
        cap = self._fake_client(
            monkeypatch, {"choices": [{"message": {"content": '{"ok": 1}'}}]}
        )
        asyncio.run(j._run_query_openrouter(j.SEQUENCE_SYSTEM_PROMPT, "u"))
        assert cap["payload"]["model"] == GLM_MODEL
        assert cap["payload"]["provider"] == {
            "require_parameters": True,
            "order": ["together"],
            "allow_fallbacks": False,
        }


# ---------------------------------------------------------------------------
# Selector file -> params -> payload end to end (no network)
# ---------------------------------------------------------------------------

class TestSelectorFileDrivesProviderPayload:
    def test_glm_selector_file_builds_expected_payload(self, tmp_path):
        path = _selector(
            tmp_path,
            f"{GLM_MODEL}\n"
            "reasoning_effort: low\n"
            "provider_order: together\n"
            "provider_allow_fallbacks: false\n",
        )
        model = j._configured_openrouter_model({}, model_file=path)
        params = j._configured_judgment_params({}, model_file=path)
        payload = j._build_openrouter_payload(
            j.SEQUENCE_SYSTEM_PROMPT, "u", model=model, params=params
        )
        assert payload["model"] == GLM_MODEL
        assert payload["reasoning"] == {"effort": "low"}
        assert payload["provider"] == {
            "require_parameters": True,
            "order": ["together"],
            "allow_fallbacks": False,
        }
        assert payload["response_format"]["json_schema"]["strict"] is True
