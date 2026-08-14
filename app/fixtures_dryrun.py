#!/usr/bin/env python3
"""Isolated, billed OpenRouter qualification gate for T11 judgment models.

This script never calls the live :8746 app: it builds in-memory fixture cases
and talks directly to ``judgment.propose_sequence``. It therefore cannot spend
the live run ledger or mutate runstate. Provider calls remain billed.
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any

import judgment as j
import sequence

FIXTURES_DIR = Path(__file__).parent / "tests" / "fixtures"
DEFAULT_MODELS = ("minimax/minimax-m3", "deepseek/deepseek-v4-flash")


def _load_fixtures() -> list[tuple[str, dict[str, Any]]]:
    return [
        (path.stem, json.loads(path.read_text(encoding="utf-8")))
        for path in sorted(FIXTURES_DIR.glob("*.json"))
    ]


def _case(name: str, coverage: str, base: dict[str, Any], *, anchor: str,
          eod: str, assigned: list[dict[str, Any]] | None = None,
          anchored: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    bundle = copy.deepcopy(base)
    bundle.setdefault("config", {})["time"] = {"anchor": anchor, "effective_eod": eod}
    if assigned is not None:
        bundle["assigned"] = assigned
    if anchored is not None:
        bundle["anchored_blocks"] = anchored
    return {"name": name, "coverage": coverage, "bundle": bundle}


def _sequence_cases(fixtures: list[tuple[str, dict[str, Any]]]) -> list[dict[str, Any]]:
    """Build >=20 deterministic sequence prompts across the six T11 risks."""
    bases = {name: bundle for name, bundle in fixtures}
    a = bases["day_a_overlap_conflict"]
    b = bases["day_b_anchored_heavy"]
    c = bases["day_c_preset_trigger"]
    d = bases["day_d_latest_start_edge"]
    workout = [{"id": "Evening Workout", "blocks": 1, "type": "workout", "zone": "evening"}]
    deep = [
        {"id": "Deep Work", "blocks": 3, "zone": "any"},
        {"id": "Admin", "blocks": 1, "zone": "any"},
    ]
    return [
        _case("fixed-wall-a", "fixed_wall", a, anchor="12:00", eod="22:00"),
        _case("fixed-wall-b", "fixed_wall", b, anchor="12:00", eod="22:00"),
        _case("fixed-wall-c", "fixed_wall", a, anchor="15:00", eod="22:00"),
        _case("fixed-wall-d", "fixed_wall", b, anchor="15:30", eod="22:00"),
        _case("late-anchor-1230", "late_anchor", a, anchor="12:30", eod="22:00"),
        _case("late-anchor-1445", "late_anchor", c, anchor="14:45", eod="22:00"),
        _case("late-anchor-1730", "late_anchor", d, anchor="17:30", eod="22:30"),
        _case("late-anchor-1930", "late_anchor", b, anchor="19:30", eod="23:30"),
        _case("exact-duration-1", "exact_duration", a, anchor="12:00", eod="22:00", assigned=deep),
        _case("exact-duration-2", "exact_duration", b, anchor="12:00", eod="22:00", assigned=deep),
        _case("exact-duration-3", "exact_duration", c, anchor="12:00", eod="22:00", assigned=deep),
        _case("exact-duration-4", "exact_duration", d, anchor="12:00", eod="22:00", assigned=deep),
        _case("overflow-1", "overflow", a, anchor="20:00", eod="20:30", assigned=deep),
        _case("overflow-2", "overflow", b, anchor="21:00", eod="21:30", assigned=deep),
        _case("overflow-3", "overflow", d, anchor="19:00", eod="19:30", assigned=deep),
        _case("midnight-1", "midnight", a, anchor="22:00", eod="23:30", assigned=deep),
        _case("midnight-2", "midnight", b, anchor="22:30", eod="23:45", assigned=deep),
        _case("midnight-3", "midnight", c, anchor="23:00", eod="23:59", assigned=deep),
        _case("workout-1", "no_morning_workout", b, anchor="08:00", eod="20:00", assigned=workout),
        _case("workout-2", "no_morning_workout", c, anchor="09:00", eod="20:00", assigned=workout),
        _case("workout-3", "no_morning_workout", a, anchor="10:30", eod="20:00", assigned=workout),
    ]


def _validator_anchored(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize fixture shorthand to the frozen validator's config shape."""
    return [{
        "Block": block.get("Block") or block.get("id"),
        "Type": block.get("Type") or block.get("type"),
        "Start": block.get("Start") or block.get("start"),
        "End": block.get("End") or block.get("end"),
        "Duration": block.get("Duration") or block.get("duration"),
        "overlap_allowed": block.get("overlap_allowed", False),
    } for block in blocks]


def _run_case(model: str, case: dict[str, Any]) -> tuple[bool, int, str]:
    bundle = case["bundle"]
    ctx = j.RunContext()
    j.OPENROUTER_MODEL = model
    try:
        proposal = j.propose_sequence(bundle["assigned"], bundle["config"], bundle["anchored_blocks"], ctx)
        verdict = sequence.validate_sequence(
            proposal, bundle["assigned"], _validator_anchored(bundle["anchored_blocks"]),
            bundle["config"], time_frame=bundle["config"]["time"],
        )
        if not verdict.ok:
            raise ValueError("; ".join(verdict.hard_errors))
        return True, ctx.attempts_made, "ok"
    except Exception as exc:  # noqa: BLE001 - qualification must report all cases
        return False, ctx.attempts_made, f"{type(exc).__name__}: {exc}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--models", nargs="+", default=list(DEFAULT_MODELS))
    args = parser.parse_args(argv)
    cases = _sequence_cases(_load_fixtures())
    rows: list[tuple[str, str, bool, int, str]] = []
    for model in args.models:
        for case in cases:
            ok, attempts, detail = _run_case(model, case)
            rows.append((model, case["name"], ok, attempts, detail))
            print(f"{model:<30} {case['name']:<22} {'PASS' if ok else 'FAIL':<4} {attempts} attempt(s)  {detail}")

    by_model = {model: [row for row in rows if row[0] == model] for model in args.models}
    m3_rows = by_model.get("minimax/minimax-m3", [])
    deepseek_rows = by_model.get("deepseek/deepseek-v4-flash", [])
    m3_pass = all(row[2] and row[3] == 1 for row in m3_rows)
    m3_first = sum(row[2] and row[3] == 1 for row in m3_rows)
    deepseek_first = sum(row[2] and row[3] == 1 for row in deepseek_rows)
    qualified = bool(m3_rows) and m3_pass and m3_first >= deepseek_first
    print(
        f"\nM3 qualification: {'PASS' if qualified else 'FAIL'} — "
        f"M3 first-attempt {m3_first}/{len(m3_rows)}, "
        f"DeepSeek {deepseek_first}/{len(deepseek_rows)}"
    )
    return 0 if qualified else 1


if __name__ == "__main__":
    sys.exit(main())
