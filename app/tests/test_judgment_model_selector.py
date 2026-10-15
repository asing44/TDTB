"""Tests for the repo-root judgment-model selector (``judgment-model.txt``).

The selector is a single-line, NON-SECRET file at the repository root naming
the OpenRouter model id judgment uses. It exists so an operator can change the
judgment model by editing one file and running ``tdtb-restart``, instead of
editing a launchd plist.

Precedence (``_configured_openrouter_model``):
  1. an explicit NONEMPTY ``TDTB_JUDGMENT_MODEL`` env value, returned verbatim
     (unchanged legacy handling; whitespace-only counts as nonempty and does
     not fall through),
  2. the repo-root selector file,
  3. the built-in ``DEFAULT_OPENROUTER_MODEL`` (DeepSeek) when the file is
     absent (legacy/synthetic copies).

The file is read once at import (startup); there is no per-call read and no hot
reload. Every test here stubs the file path and touches no provider, key, or
credential: ZERO real calls.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import judgment as j  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
SELECTOR_NAME = "judgment-model.txt"


def _selector(tmp_path: Path, content: str) -> Path:
    path = tmp_path / SELECTOR_NAME
    path.write_text(content, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Canonical selector file
# ---------------------------------------------------------------------------

class TestCanonicalSelector:
    def test_repo_root_selector_is_one_plain_model_id_line(self):
        path = REPO_ROOT / SELECTOR_NAME
        assert path.is_file(), "repo-root judgment-model.txt must exist"
        raw = path.read_text(encoding="utf-8")
        # Canonical formatting: exactly one plain model-id line — no comments,
        # quotes, keys, or extra blank lines. The value itself is deliberately
        # NOT asserted, so editing the selector (its whole purpose) keeps this
        # test green.
        model = j._parse_judgment_model_file(raw, path)
        assert raw == f"{model}\n"
        assert j._MODEL_ID_RE.fullmatch(model)

    def test_default_path_is_module_relative_not_home_literal(self):
        # Portable per worktree: derived from judgment.py's own location, so a
        # worktree or a relocated repo resolves its own root. No absolute home
        # path is baked in.
        expected = Path(j.__file__).resolve().parents[1] / SELECTOR_NAME
        assert j.JUDGMENT_MODEL_FILE == expected
        # parents[1] is the repo root that contains app/
        assert (expected.parent / "app").is_dir()

    def test_startup_resolution_matches_configured_source(self):
        # Startup resolves whichever source is active: env override, else the
        # repo-root selector file if present, else the built-in default. It is
        # deliberately NOT pinned to DeepSeek, so a legitimate alternate
        # selector file (or a fresh import in a copy) stays valid.
        assert j.OPENROUTER_MODEL == j._configured_openrouter_model()

    def test_alternate_selector_file_resolves_deterministically(
        self, tmp_path, monkeypatch
    ):
        # A deterministic proof that resolution follows a selector file other
        # than the canonical one, without mutating canonical judgment-model.txt.
        monkeypatch.setattr(j, "JUDGMENT_MODEL_FILE", tmp_path / SELECTOR_NAME)
        (tmp_path / SELECTOR_NAME).write_text(
            "google/gemini-2.5-pro\n", encoding="utf-8"
        )
        monkeypatch.delenv("TDTB_JUDGMENT_MODEL", raising=False)
        assert j._configured_openrouter_model() == "google/gemini-2.5-pro"


# ---------------------------------------------------------------------------
# File path is used when env is absent
# ---------------------------------------------------------------------------

class TestFilePathReplacement:
    def test_file_value_is_used_when_env_absent(self, tmp_path):
        path = _selector(tmp_path, "meta-llama/llama-3.1-70b-instruct\n")
        assert j._configured_openrouter_model({}, model_file=path) == (
            "meta-llama/llama-3.1-70b-instruct"
        )

    def test_file_value_is_trimmed_of_surrounding_whitespace(self, tmp_path):
        path = _selector(tmp_path, "  meta-llama/llama-3.1-70b-instruct  \n")
        assert j._configured_openrouter_model({}, model_file=path) == (
            "meta-llama/llama-3.1-70b-instruct"
        )

    def test_file_allows_routing_colon_suffix(self, tmp_path):
        path = _selector(tmp_path, "google/gemini-2.5-pro:free\n")
        assert j._configured_openrouter_model({}, model_file=path) == (
            "google/gemini-2.5-pro:free"
        )

    def test_file_allows_extra_path_segment(self, tmp_path):
        path = _selector(tmp_path, "openrouter/deepseek/deepseek-v4.1-flash\n")
        assert j._configured_openrouter_model({}, model_file=path) == (
            "openrouter/deepseek/deepseek-v4.1-flash"
        )


# ---------------------------------------------------------------------------
# Env precedence (legacy handling preserved)
# ---------------------------------------------------------------------------

class TestEnvPrecedence:
    def test_env_wins_over_file(self, tmp_path):
        path = _selector(tmp_path, "meta-llama/llama-3.1-70b-instruct\n")
        env = {"TDTB_JUDGMENT_MODEL": "openai/gpt-6-luna"}
        assert j._configured_openrouter_model(env, model_file=path) == (
            "openai/gpt-6-luna"
        )

    def test_env_prefix_passthrough_unchanged(self, tmp_path):
        # The existing env path does no normalisation; whatever the operator
        # writes is returned verbatim.
        path = _selector(tmp_path, "meta-llama/llama-3.1-70b-instruct\n")
        env = {"TDTB_JUDGMENT_MODEL": "openrouter/deepseek/deepseek-v4.1-flash"}
        assert j._configured_openrouter_model(env, model_file=path) == (
            "openrouter/deepseek/deepseek-v4.1-flash"
        )

    def test_blank_env_falls_back_to_file(self, tmp_path):
        path = _selector(tmp_path, "meta-llama/llama-3.1-70b-instruct\n")
        for blank in ("", None):
            env = {"TDTB_JUDGMENT_MODEL": blank} if blank is not None else {}
            assert j._configured_openrouter_model(env, model_file=path) == (
                "meta-llama/llama-3.1-70b-instruct"
            )

    def test_env_wins_even_when_file_is_invalid(self, tmp_path):
        # An invalid selector file only matters when it would actually be read;
        # a valid env override short-circuits before the file is touched.
        path = _selector(tmp_path, "not a model id\n")
        env = {"TDTB_JUDGMENT_MODEL": "openai/gpt-6-luna"}
        assert j._configured_openrouter_model(env, model_file=path) == (
            "openai/gpt-6-luna"
        )

    def test_whitespace_only_env_passes_through_not_fall_through(self, tmp_path):
        # Legacy behavior, deliberately unchanged: any NONEMPTY env string is
        # returned verbatim, including whitespace-only. The file is NOT read
        # and no strip/validation is applied to the env value.
        path = _selector(tmp_path, "meta-llama/llama-3.1-70b-instruct\n")
        assert j._configured_openrouter_model(
            {"TDTB_JUDGMENT_MODEL": "   "}, model_file=path
        ) == "   "


# ---------------------------------------------------------------------------
# Missing file -> built-in default (legacy / synthetic copies)
# ---------------------------------------------------------------------------

class TestMissingFileFallback:
    def test_missing_file_uses_existing_deepseek_default(self, tmp_path):
        absent = tmp_path / "absent" / SELECTOR_NAME
        assert j._configured_openrouter_model({}, model_file=absent) == (
            j.DEFAULT_OPENROUTER_MODEL
        )
        assert j._configured_openrouter_model({}, model_file=absent) == (
            "deepseek/deepseek-v4.1-flash"
        )

    def test_blank_env_and_missing_file_uses_builtin_default(self, tmp_path):
        absent = tmp_path / "absent" / SELECTOR_NAME
        assert j._configured_openrouter_model(
            {"TDTB_JUDGMENT_MODEL": ""}, model_file=absent
        ) == j.DEFAULT_OPENROUTER_MODEL


# ---------------------------------------------------------------------------
# Invalid / unreadable file fails clearly, never silently
# ---------------------------------------------------------------------------

class TestInvalidSelectorFailsClearly:
    @pytest.mark.parametrize(
        "content, echoed",
        [
            ("\n", None),                       # empty
            ("   \n", None),                    # whitespace only
            ("no-slash-model\n", "no-slash-model"),
            ("has spaces/here\n", "has spaces/here"),
            ("model/one\nmodel/two\n", "model/one"),
            ("sk-not-a-model-id\n", "sk-not-a-model-id"),
        ],
    )
    def test_invalid_content_raises_without_echoing(self, tmp_path, content, echoed):
        path = _selector(tmp_path, content)
        with pytest.raises(ValueError) as excinfo:
            j._configured_openrouter_model({}, model_file=path)
        message = str(excinfo.value)
        if echoed is not None:
            assert echoed not in message, "error must not echo invalid contents"
        assert SELECTOR_NAME in message

    def test_unreadable_file_raises_instead_of_silently_falling_back(self, tmp_path):
        # A directory is readable-openable as a path but not as a file: any
        # non-FileNotFoundError read failure must surface, never route to the
        # built-in default (that would silently point judgment somewhere else).
        directory = tmp_path / SELECTOR_NAME
        directory.mkdir()
        with pytest.raises(OSError) as excinfo:
            j._configured_openrouter_model({}, model_file=directory)
        assert not isinstance(excinfo.value, FileNotFoundError)

    def test_invalid_file_does_not_return_default(self, tmp_path):
        path = _selector(tmp_path, "\n")
        with pytest.raises(ValueError):
            j._configured_openrouter_model({}, model_file=path)


# ---------------------------------------------------------------------------
# Startup-only resolution (no per-call read / no hot reload)
# ---------------------------------------------------------------------------

class TestStartupOnlyResolution:
    def test_model_is_snapshot_not_per_call(self, tmp_path, monkeypatch):
        before = j.OPENROUTER_MODEL
        # Point the module constant at a different file AFTER import; the
        # already-resolved value must not change.
        monkeypatch.setattr(j, "JUDGMENT_MODEL_FILE", tmp_path / SELECTOR_NAME)
        (tmp_path / SELECTOR_NAME).write_text(
            "meta-llama/llama-3.1-70b-instruct\n", encoding="utf-8"
        )
        assert j.OPENROUTER_MODEL == before
