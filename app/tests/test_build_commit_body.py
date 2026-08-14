"""Tests for build_commit_body.py — ISS-2 seam: today's run-state
``micro_adventure`` selection must be injected into the emitted ``config`` so the
SKILL.md Step E Live→Todoist reroute is reachable through the headless path.

Hermetic: fixed ``date`` (no clock), real run-state note round-trip via
runstate.write_runstate, real manifest via shadow.build_plan_manifest.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import build_commit_body as bcb  # noqa: E402
import runstate as runstate_mod  # noqa: E402
import shadow  # noqa: E402

TODAY = date(2026, 7, 13)
MICRO = {"id": "ma03", "idea": "Cook something new", "category": "food"}


def _write_runstate(vault: Path, micro):
    """Write today's run-state note with the given micro_adventure value."""
    state = runstate_mod.build_runstate({"micro_adventure": micro})
    return runstate_mod.write_runstate(vault, TODAY, state)


# ---------------------------------------------------------------------------
# _load_runstate_micro_adventure / inject_micro_adventure
# ---------------------------------------------------------------------------

class TestInjectMicroAdventure:
    def test_reads_todays_runstate_selection(self, tmp_path):
        _write_runstate(tmp_path, MICRO)
        out = bcb.inject_micro_adventure({}, tmp_path, TODAY)
        assert out["micro_adventure"] == MICRO

    def test_no_note_returns_config_unchanged(self, tmp_path):
        base = {"presets": []}
        out = bcb.inject_micro_adventure(base, tmp_path, TODAY)
        assert out is base  # untouched, same object
        assert "micro_adventure" not in out

    def test_note_without_selection_returns_unchanged(self, tmp_path):
        _write_runstate(tmp_path, None)  # default skeleton: micro_adventure = None
        base = {"presets": []}
        out = bcb.inject_micro_adventure(base, tmp_path, TODAY)
        assert out is base
        assert "micro_adventure" not in out

    def test_does_not_mutate_input(self, tmp_path):
        _write_runstate(tmp_path, MICRO)
        base = {"anchored_blocks": [{"id": "Live"}]}
        out = bcb.inject_micro_adventure(base, tmp_path, TODAY)
        assert "micro_adventure" not in base  # input untouched
        assert out is not base
        assert out["micro_adventure"] == MICRO

    def test_exact_date_read_ignores_prior_runstate(self, tmp_path):
        """A strictly-prior note (yesterday) must NOT satisfy today's read —
        guards against a regression to gather.load_runstate's prior-note lookup.
        """
        ystate = runstate_mod.build_runstate({"micro_adventure": MICRO})
        runstate_mod.write_runstate(tmp_path, date(2026, 7, 12), ystate)
        out = bcb.inject_micro_adventure({}, tmp_path, TODAY)
        assert "micro_adventure" not in out


# ---------------------------------------------------------------------------
# CLI override: --micro-adventure coercion + precedence over run-state
# ---------------------------------------------------------------------------

class TestMicroAdventureOverride:
    def test_coerce_json_object_used_verbatim(self):
        assert bcb._coerce_micro_adventure('{"id": "x", "idea": "Hike", "category": "outdoor"}') \
            == {"id": "x", "idea": "Hike", "category": "outdoor"}

    def test_coerce_bare_string_wraps_as_idea(self):
        assert bcb._coerce_micro_adventure("Cook something new") == {"idea": "Cook something new"}

    def test_coerce_none_is_none(self):
        assert bcb._coerce_micro_adventure(None) is None

    def test_coerce_non_object_json_wraps_as_idea(self):
        # a JSON scalar (not an object) is treated as a bare idea string
        assert bcb._coerce_micro_adventure("42") == {"idea": "42"}

    def test_override_wins_over_runstate(self, tmp_path):
        _write_runstate(tmp_path, MICRO)  # run-state has one selection
        override = {"idea": "Override wins"}
        out = bcb.inject_micro_adventure({}, tmp_path, TODAY, override=override)
        assert out["micro_adventure"] == override

    def test_override_injects_without_any_runstate(self, tmp_path):
        override = {"idea": "No note needed"}
        out = bcb.inject_micro_adventure({}, tmp_path, TODAY, override=override)
        assert out["micro_adventure"] == override


# ---------------------------------------------------------------------------
# ISS-2 regression seam: run-state -> injected config -> manifest reroute
# ---------------------------------------------------------------------------

class TestLiveRerouteSeam:
    DIGEST = {"assigned": []}
    SEQUENCE = {"sequence": [{"id": "Live", "start": "20:30", "end": "21:30", "zone": "any"}]}
    BASE_CONFIG = {"anchored_blocks": [{"id": "Live"}]}

    def test_injected_config_drives_live_todoist_reroute(self, tmp_path):
        _write_runstate(tmp_path, MICRO)
        injected = bcb.inject_micro_adventure(dict(self.BASE_CONFIG), tmp_path, TODAY)
        manifest = shadow.build_plan_manifest(self.DIGEST, self.SEQUENCE, injected)
        step_a = [m for m in manifest if m.step == "A"]
        assert len(step_a) == 1
        assert step_a[0].system == "todoist"
        assert step_a[0].name == "🌱 Cook something new"
        assert step_a[0].routing == "Inbox"

    def test_without_injection_live_stays_step_e_calendar(self, tmp_path):
        """Proves the fix is load-bearing: fed the raw config (no injection),
        the same manifest keeps Live as a Step E calendar event, never Step A.
        """
        manifest = shadow.build_plan_manifest(self.DIGEST, self.SEQUENCE, self.BASE_CONFIG)
        assert not [m for m in manifest if m.step == "A"]
        assert [m for m in manifest if m.step == "E" and m.system == "calendar"]
