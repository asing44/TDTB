#!/usr/bin/env python3
"""validate_artifact.py — validate a planning artifact against the A1 contract.

Wraps ``app/artifact_source.py``'s ``validate_artifact`` so the ``tdtb-refresh``
skill (and the operator) can check a produced artifact before trusting it, or
after a hand edit. It never writes anything.

Usage::

    python tools/validate_artifact.py ~/.config/tdtb/state/planning-artifact.json
    python tools/produce_rows.py --rules rules.json < src.json | \\
        python tools/validate_artifact.py -

Exit codes: 0 valid, 1 malformed (the first violations are printed), 2 the
document could not be read or parsed as JSON.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_APP_DIR = Path(__file__).resolve().parent.parent / "app"
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))

import artifact_source  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate a TDTB planning artifact (tdtb.planning-artifact v1)."
    )
    parser.add_argument(
        "path", nargs="?", default="-",
        help="Artifact JSON file, or - for stdin (default)",
    )
    args = parser.parse_args(argv)

    try:
        if args.path == "-":
            raw = sys.stdin.read()
        else:
            raw = Path(args.path).read_text(encoding="utf-8")
    except OSError as exc:
        print(f"validate_artifact: cannot read {args.path}: {exc}", file=sys.stderr)
        return 2

    try:
        document = json.loads(raw)
    except ValueError as exc:
        print(f"validate_artifact: {args.path} is not valid JSON: {exc}", file=sys.stderr)
        return 1

    violations = artifact_source.validate_artifact(document)
    if violations:
        print(f"MALFORMED: {args.path}")
        for violation in violations:
            print(f"  - {violation}")
        return 1

    rows = document.get("rows") if isinstance(document, dict) else []
    print(
        f"OK: {args.path} (schema={document.get('schema')}, "
        f"version={document.get('version')}, rows={len(rows)})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
