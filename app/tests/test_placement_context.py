"""test_placement_context.py — T5 (allocator rewrite), locked decision 10.

`## Placement Context` is config PROSE, not a subsystem: whatever Adam writes
rides verbatim into the one billed sequence call and nothing else. These tests
cover the sanitizer's bounds, the config accessor, and prompt inclusion —
the prompt assertions run against a mocked judgment call, never a live one.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import config_reader
import judgment


CONTEXT_PROSE = (
    "- No screen-heavy work after 21:00.\n"
    "- Put the hardest thinking before lunch.\n"
)

CONFIG_WITH_CONTEXT = f"""\
---
description: placement context test config
last_updated: 2026-07-26
---

# TDTB Bridger Config

## Defaults

| Key | Value    |
| --- | -------- |
| eod | 11:45 PM |

## Placement Context

{CONTEXT_PROSE}
"""

CONFIG_WITHOUT = """\
---
description: no placement context
last_updated: 2026-07-26
---

# TDTB Bridger Config

## Defaults

| Key | Value    |
| --- | -------- |
| eod | 11:45 PM |
"""


def _write_config(tmp_path: Path, text: str) -> Path:
    p = tmp_path / config_reader.CONFIG_REL_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return tmp_path


# ---------------------------------------------------------------------------
# Sanitizer
# ---------------------------------------------------------------------------

class TestSanitizer:
    def test_empty_input_is_empty_output(self):
        assert config_reader.sanitize_placement_context("") == ""
        assert config_reader.sanitize_placement_context("   \n\n  ") == ""

    def test_prose_survives_verbatim(self):
        out = config_reader.sanitize_placement_context(CONTEXT_PROSE)
        assert "No screen-heavy work after 21:00." in out
        assert "hardest thinking before lunch" in out

    def test_code_fences_are_stripped(self):
        out = config_reader.sanitize_placement_context(
            "before\n```\nfenced\n```\nafter")
        assert "```" not in out
        assert "before" in out and "after" in out and "fenced" in out

    def test_control_characters_are_dropped(self):
        out = config_reader.sanitize_placement_context("a\x00b\x07c")
        assert out == "abc"

    def test_blank_line_runs_collapse(self):
        out = config_reader.sanitize_placement_context("a\n\n\n\n\nb")
        assert out == "a\n\nb"

    def test_truncates_at_the_char_bound(self):
        raw = "\n".join(f"line {i} " + "x" * 80 for i in range(200))
        out = config_reader.sanitize_placement_context(raw)
        assert len(out) <= config_reader.PLACEMENT_CONTEXT_MAX_CHARS

    def test_truncation_lands_on_a_line_boundary(self):
        raw = "\n".join("y" * 100 for _ in range(200))
        out = config_reader.sanitize_placement_context(raw)
        assert all(len(line) == 100 for line in out.split("\n"))

    def test_leading_blank_lines_do_not_start_the_block(self):
        assert config_reader.sanitize_placement_context("\n\n\nrule").startswith("rule")


# ---------------------------------------------------------------------------
# Config accessor
# ---------------------------------------------------------------------------

class TestConfigAccessor:
    def test_reads_the_section_as_prose(self, tmp_path: Path):
        result = config_reader.read_config(_write_config(tmp_path, CONFIG_WITH_CONTEXT))
        assert "No screen-heavy work after 21:00." in result.config.get_placement_context()

    def test_absent_section_is_empty_string(self, tmp_path: Path):
        result = config_reader.read_config(_write_config(tmp_path, CONFIG_WITHOUT))
        assert result.config.get_placement_context() == ""

    def test_section_is_not_required_for_validation(self, tmp_path: Path):
        """An optional section must never turn a valid config invalid."""
        with_ctx = config_reader.read_config(
            _write_config(tmp_path / "a", CONFIG_WITH_CONTEXT))
        without = config_reader.read_config(
            _write_config(tmp_path / "b", CONFIG_WITHOUT))
        assert (with_ctx.validation.missing_sections
                == without.validation.missing_sections)


# ---------------------------------------------------------------------------
# Prompt inclusion — mocked, never a billed call
# ---------------------------------------------------------------------------

def _config_with(body: str) -> dict:
    return {config_reader.PLACEMENT_CONTEXT_SECTION: {"_body": body}}


class TestPromptInstruction:
    def test_absent_section_degrades_to_empty(self):
        assert judgment.placement_context_instruction({}) == ""
        assert judgment.placement_context_instruction(None) == ""

    def test_empty_body_degrades_to_empty(self):
        assert judgment.placement_context_instruction(_config_with("   ")) == ""

    def test_malformed_section_degrades_to_empty(self):
        assert judgment.placement_context_instruction(
            {config_reader.PLACEMENT_CONTEXT_SECTION: "just a string"}) == ""
        assert judgment.placement_context_instruction(
            {config_reader.PLACEMENT_CONTEXT_SECTION: [1, 2]}) == ""

    def test_present_body_rides_verbatim(self):
        out = judgment.placement_context_instruction(_config_with(CONTEXT_PROSE))
        assert "PLACEMENT CONTEXT" in out
        assert "No screen-heavy work after 21:00." in out

    def test_framed_as_preference_not_hard_constraint(self):
        out = judgment.placement_context_instruction(_config_with("rule"))
        assert "PREFERENCES" in out
        assert "never violate an anchor" in out

    def test_block_is_bounded(self):
        out = judgment.placement_context_instruction(_config_with("z" * 50_000))
        assert len(out) < config_reader.PLACEMENT_CONTEXT_MAX_CHARS + 500


class TestSequencePromptWiring:
    """The instruction reaches the actual /sequence user prompt — asserted by
    capturing the prompt at the _call_and_validate seam, with no model call."""

    @pytest.fixture
    def captured(self, monkeypatch: pytest.MonkeyPatch) -> dict:
        seen: dict = {}

        async def _fake(ctx, name, system_prompt, user_prompt, validate, **kw):
            seen["user_prompt"] = user_prompt
            return {"sequence": []}

        monkeypatch.setattr(judgment, "_call_and_validate", _fake)
        return seen

    def test_prompt_carries_the_context(self, captured: dict):
        judgment.propose_sequence([], _config_with(CONTEXT_PROSE), [])
        assert "PLACEMENT CONTEXT" in captured["user_prompt"]
        assert "No screen-heavy work after 21:00." in captured["user_prompt"]

    def test_prompt_without_the_section_is_unchanged(self, captured: dict):
        judgment.propose_sequence([], {}, [])
        assert "PLACEMENT CONTEXT" not in captured["user_prompt"]

    def test_context_precedes_the_json_payloads(self, captured: dict):
        judgment.propose_sequence([], _config_with(CONTEXT_PROSE), [])
        prompt = captured["user_prompt"]
        assert prompt.index("PLACEMENT CONTEXT") < prompt.index("assigned:")
