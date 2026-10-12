#!/usr/bin/env python3
"""shadow_run.py — T13 gate prep: run a full shadow diff from the CLI (no
app process, no writes).

    .venv/bin/python shadow_run.py
    .venv/bin/python shadow_run.py --sequence-file path/to/sequence.json

The Obsidian note gather is gone (vault removal V4): the digest this tool
builds carries no note rows, so the useful input is ``--sequence-file``.

Pipeline: gather -> digest (main.py's rank_pool/build_digest, same
deterministic ranking the /digest route uses) -> (skip judgment — this is a
diagnostic tool, not a live LLM call) -> a sequence, either read from
``--sequence-file`` (the exact shape a real /sequence response has:
``{"sequence": [{id,start,end,zone}, ...]}``) or synthesized trivially from
the digest's Assigned items -> shadow.build_plan_manifest -> gather_live_state
-> shadow.diff_against_live -> a readable table + unavailable-surface
warnings.

Always exits 0 — this is a diagnostic preview, not a CI gate. A missing
Todoist token file (ShadowStateError) prints a
clear error and exit 1; everything else about a shadow run degrades to a
warning per shadow.gather_live_state's per-surface contract.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent / "gather"))

import tdtb_gather as gather  # noqa: E402
import main as main_mod  # noqa: E402
import shadow  # noqa: E402


def _synthesize_trivial_sequence(assigned_items: list[dict]) -> dict:
    """No --sequence-file given: place each Assigned digest item back-to-back
    starting at 09:00, 30 minutes each, in digest order. This is NOT a real
    plan (no zone/capacity/anchored-block awareness) — it exists only to
    exercise build_plan_manifest / gather_live_state / diff_against_live
    end-to-end against live Todoist/calendar state ahead of a real sequence."""
    rows = []
    minute = 9 * 60
    for item in assigned_items:
        sh, sm = divmod(minute, 60)
        minute += 30
        eh, em = divmod(minute, 60)
        rows.append({
            "id": item.get("name"),
            "start": f"{sh:02d}:{sm:02d}",
            "end": f"{eh:02d}:{em:02d}",
            "zone": "any",
        })
    return {"sequence": rows}


def _print_table(diff: "shadow.ShadowDiff") -> None:
    print("\n## Shadow diff\n")
    header = f"{'Step':<4} {'System':<9} {'Class':<14} {'Name':<32} {'Time':<6} Detail"
    print(header)
    print("-" * len(header))
    for entry in diff.entries:
        m = entry.manifest
        detail = json.dumps(entry.detail, default=str) if entry.detail else ""
        name = (m.name or "")[:32]
        print(f"{m.step:<4} {m.system:<9} {entry.classification:<14} {name:<32} {m.time or '—':<6} {detail}")

    print("\n## Counts")
    for cls, n in diff.counts().items():
        print(f"  {cls:<14} {n}")

    if diff.unavailable_surfaces:
        print("\n## Unavailable surfaces")
        for surface in diff.unavailable_surfaces:
            print(f"  ⚠ {surface} — see the '{surface}_error' key in live_state for detail")
    print()


def run(sequence_file: str | None) -> int:
    today = gather.effective_date(datetime.now())
    run_data = gather.empty_run_data(today)

    order = list(main_mod.config_reader.FALLBACK_RANKING_CRITERIA["within_tier_sort"])
    config_result = main_mod.config_reader.read_config()
    if config_result.config is not None:
        value = config_result.config.get_ranking_criterion("within_tier_sort").value
        if isinstance(value, str):
            order = [p.strip() for p in value.split(",") if p.strip()]
        elif isinstance(value, list):
            order = value

    digest = main_mod.build_digest(run_data["pool_items"], run_data["assigned_items"], today, order)

    if sequence_file:
        sequence = json.loads(Path(sequence_file).read_text(encoding="utf-8"))
    else:
        sequence = _synthesize_trivial_sequence(digest["assigned"])
        print("(no --sequence-file given — synthesizing a trivial back-to-back sequence)")

    config = config_result.config.sections if config_result.config is not None else {}
    manifest = shadow.build_plan_manifest(digest, sequence, config)

    try:
        live_state = shadow.gather_live_state(config)
    except shadow.ShadowStateError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    diff = shadow.diff_against_live(manifest, live_state)
    _print_table(diff)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sequence-file", default=None, help="Path to a /sequence-shaped JSON file")
    args = parser.parse_args()

    return run(args.sequence_file)


if __name__ == "__main__":
    raise SystemExit(main())
