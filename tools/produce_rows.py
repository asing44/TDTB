#!/usr/bin/env python3
"""produce_rows.py — the deterministic half of the tdtb-refresh producer (A2).

The ``tdtb-refresh`` skill (``skills/tdtb-refresh/SKILL.md``) does the part
only an agent can do: it speaks Todoist over MCP and writes the fetched source
JSON. This CLI does the part that must never be judgement: it applies the
declarative rule set and emits the normalized planning artifact.

It reads fetched source JSON on stdin, evaluates the rules deterministically,
and writes the complete ``tdtb.planning-artifact`` v1 document to stdout — the
artifact's ``rows`` are the normalized rows the app consumes. With ``--write``
it also writes that document through ``artifact_source.atomic_write_artifact``
(never a bare file write), retaining one prior copy.

Source JSON shape (keys are source names; Todoist is the only one evaluated in
A2)::

    {
      "todoist": {
        "status": "ok",
        "read_at": "2026-10-08T09:00:00-07:00",
        "warnings": [],
        "tasks": [ { ...raw Todoist task objects... } ]
      },
      "habits": {
        "status": "ok",
        "read_at": "2026-10-08T09:00:00-07:00",
        "tasks": [ { "id": ..., "content": ..., "duration": "5m" } ],
        "completed": [ { "task_id": ... } ]
      }
    }

``habits`` is not a row source: the producer folds it into the artifact's
top-level ``habits`` block (``total/done/outstanding/est_minutes``), the only
source of habit time now that the vault read is retired.

Usage::

    python tools/produce_rows.py --rules ~/.config/tdtb/producer-rules.json \\
        --logical-day 2026-10-08 --write < source.json
    python tools/produce_rows.py --rules rules.json < source.json > artifact.json

The rules default to ``<app home>/producer-rules.json`` (honours ``TDTB_HOME``).
Exit codes: 0 ok, 2 input not JSON, 3 rules invalid, 4 the built artifact failed
its own A1 validation (nothing is written in that case).
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

_APP_DIR = Path(__file__).resolve().parent.parent / "app"
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))
_GATHER_DIR = str(_APP_DIR / "gather")
if _GATHER_DIR not in sys.path:
    sys.path.insert(0, _GATHER_DIR)

import artifact_source  # noqa: E402
import producer_rules  # noqa: E402


def _read_input(source: str) -> str:
    if source == "-":
        return sys.stdin.read()
    return Path(source).read_text(encoding="utf-8")


def _today_logical_day() -> str:
    """The app's own logical day, via the gather contract (O2)."""
    return str(artifact_source._today_effective_date(datetime.now().astimezone()))


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _print_summary(document: dict, wrote: Path | None) -> None:
    admission = document.get("admission") or {}
    per_rule = admission.get("per_rule") or []
    print("tdtb-refresh produce_rows:", file=sys.stderr)
    for stat in per_rule:
        print(
            f"  rule {stat.get('id')!r}: matched {stat.get('matched', 0)}, "
            f"admitted {stat.get('admitted', 0)}, dropped {stat.get('dropped', 0)}",
            file=sys.stderr,
        )
    recent = admission.get("recent_drops") or []
    for drop in recent[-5:]:
        print(
            f"  dropped {drop.get('name')!r} by {drop.get('rule') or '(no rule)'}",
            file=sys.stderr,
        )
    print(
        f"  rows: {len(document.get('rows') or [])}, "
        f"logical_day: {document.get('logical_day')}",
        file=sys.stderr,
    )
    habits = document.get("habits")
    if isinstance(habits, dict):
        print(
            f"  habits: {habits.get('done', 0)} done / "
            f"{habits.get('total', 0)} total, "
            f"{habits.get('est_minutes', 0)} min outstanding",
            file=sys.stderr,
        )
    if wrote is not None:
        print(f"  wrote {wrote}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Apply the declarative producer rules to fetched source JSON on "
            "stdin and emit the normalized planning artifact on stdout."
        )
    )
    parser.add_argument(
        "--rules", default=None,
        help="Rules file (default: <app home>/producer-rules.json, honouring TDTB_HOME)",
    )
    parser.add_argument(
        "--input", default="-", help="Source JSON file, or - for stdin (default)",
    )
    parser.add_argument(
        "--logical-day", default=None,
        help="The artifact's logical day YYYY-MM-DD (default: the app's effective date)",
    )
    parser.add_argument(
        "--generated-at", default=None,
        help="ISO-8601 timestamp with offset (default: now)",
    )
    parser.add_argument(
        "--write", action="store_true",
        help="Write the artifact atomically (and retain one prior copy)",
    )
    parser.add_argument(
        "--artifact", default=None,
        help="Destination artifact path (default: <app home>/state/planning-artifact.json)",
    )
    parser.add_argument("--run-id", default=None, help="Producer run id")
    parser.add_argument("--producer-name", default="tdtb-refresh")
    parser.add_argument("--producer-version", default="0.1.0")
    args = parser.parse_args(argv)

    try:
        source_json = json.loads(_read_input(args.input))
    except OSError as exc:
        print(f"produce_rows: cannot read input: {exc}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"produce_rows: input is not valid JSON: {exc}", file=sys.stderr)
        return 2
    if not isinstance(source_json, dict):
        print("produce_rows: input must be a JSON object keyed by source", file=sys.stderr)
        return 2

    rules_target = Path(args.rules) if args.rules else producer_rules.rules_path()
    try:
        rules_document = producer_rules.load_rules(rules_target)
    except producer_rules.RulesError as exc:
        print(f"produce_rows: invalid producer rules at {rules_target}", file=sys.stderr)
        for violation in exc.violations:
            print(f"  - {violation}", file=sys.stderr)
        return 3

    logical_day = args.logical_day or _today_logical_day()
    generated_at = args.generated_at or _now_iso()
    run_id = args.run_id or f"tdtb-refresh-{logical_day}"

    try:
        document = producer_rules.build_artifact(
            source_json,
            rules_document,
            logical_day=logical_day,
            generated_at=generated_at,
            producer_name=args.producer_name,
            producer_version=args.producer_version,
            run_id=run_id,
        )
    except producer_rules.RulesError as exc:
        print("produce_rows: rule evaluation failed", file=sys.stderr)
        for violation in exc.violations:
            print(f"  - {violation}", file=sys.stderr)
        return 3

    violations = artifact_source.validate_artifact(document)
    if violations:
        print("produce_rows: built artifact failed A1 validation; not writing", file=sys.stderr)
        for violation in violations:
            print(f"  - {violation}", file=sys.stderr)
        return 4

    wrote: Path | None = None
    if args.write:
        target = Path(args.artifact) if args.artifact else artifact_source.artifact_path()
        try:
            artifact_source.atomic_write_artifact(document, path=target)
        except OSError as exc:
            print(f"produce_rows: cannot write artifact at {target}: {exc}", file=sys.stderr)
            return 5
        wrote = target

    json.dump(document, sys.stdout, indent=2, sort_keys=True, ensure_ascii=False)
    sys.stdout.write("\n")
    _print_summary(document, wrote)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
