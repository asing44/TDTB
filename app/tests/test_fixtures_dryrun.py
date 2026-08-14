"""Regression coverage for the isolated OpenRouter qualification harness."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import fixtures_dryrun as dryrun  # noqa: E402


def test_sequence_qualification_matrix_has_twenty_constraint_cases():
    cases = dryrun._sequence_cases(dryrun._load_fixtures())
    assert len(cases) >= 20
    assert {case["coverage"] for case in cases} >= {
        "late_anchor", "fixed_wall", "exact_duration", "overflow",
        "midnight", "no_morning_workout",
    }


def test_sequence_qualification_cases_carry_a_deterministic_time_frame():
    for case in dryrun._sequence_cases(dryrun._load_fixtures()):
        time = case["bundle"]["config"]["time"]
        assert time["anchor"] < time["effective_eod"]
