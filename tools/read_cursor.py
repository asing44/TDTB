#!/usr/bin/env python3
"""read_cursor.py — the paced content-read cursor for the tdtb-refresh skill (A3).

The Capacities REST API charges one content read per object whenever a
property beyond the free tag/collection membership is needed, and allows only
30 requests / 60 seconds. This CLI lets the skill read a large corpus in
budgeted windows and RESUME where it stopped:

  - ``plan``   — given the free listing (``listObjectsByTag`` /
                 ``listObjectsByCollection`` output), report which object ids
                 still need a content read this window, what is already
                 covered, and how many are deferred. A non-zero deferred count
                 means the source is ``partial``.
  - ``record`` — record the objects whose content was actually read, so the
                 next run skips them and the window's pacing advances.
  - ``status`` — print the cursor's object count and window.

The cursor is ``state_dir()/producer-cache.json`` (i.e.
``~/.config/tdtb/state/producer-cache.json``, honouring ``TDTB_HOME``). It is a
COVERAGE cursor, NOT change detection: Capacities exposes no guaranteed
``updatedAt``, so the cursor knows only what was read, never whether a read
object changed.

Usage::

    python tools/read_cursor.py plan --input listed.json
    python tools/read_cursor.py record --input read.json
    python tools/read_cursor.py status

Input is a JSON array of objects, or an object carrying one under
``objects``/``records``/``items``. Each entry needs an ``id`` and may carry
``structureId``/``spaceId``.

Exit codes: 0 ok, 2 the input could not be read or is not JSON.
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

import producer_rules  # noqa: E402


def _read_input(source: str) -> Any:
    raw = sys.stdin.read() if source == "-" else Path(source).read_text(encoding="utf-8")
    return json.loads(raw)


def _listed(payload: Any) -> list[Any]:
    if isinstance(payload, dict):
        for key in ("objects", "records", "items"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
        return []
    if isinstance(payload, list):
        return payload
    return []


def _parse_now(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed


def _dump(payload: Any) -> None:
    json.dump(payload, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Plan and record paced Capacities content reads (coverage cursor)."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    plan = sub.add_parser("plan", help="which listed objects still need a content read")
    plan.add_argument("--input", default="-", help="Listed objects JSON (default: stdin)")
    plan.add_argument("--cache", default=None, help="Cursor path (default: state dir)")
    plan.add_argument("--budget", type=int, default=producer_rules.DEFAULT_CONTENT_READ_BUDGET)
    plan.add_argument("--window", type=int, default=producer_rules.DEFAULT_CONTENT_READ_WINDOW_SECONDS)
    plan.add_argument("--now", default=None, help="ISO-8601 clock override (tests)")

    record = sub.add_parser("record", help="record the objects whose content was read")
    record.add_argument("--input", default="-", help="Read objects JSON (default: stdin)")
    record.add_argument("--cache", default=None, help="Cursor path (default: state dir)")
    record.add_argument("--window", type=int, default=producer_rules.DEFAULT_CONTENT_READ_WINDOW_SECONDS)
    record.add_argument("--now", default=None, help="ISO-8601 clock override (tests)")

    status = sub.add_parser("status", help="print the cursor summary")
    status.add_argument("--cache", default=None, help="Cursor path (default: state dir)")

    args = parser.parse_args(argv)

    path = Path(args.cache) if getattr(args, "cache", None) else None

    if args.command == "status":
        cache = producer_rules.load_cache(path)
        objects = cache.get("objects") or {}
        _dump(
            {
                "cache": str(path or producer_rules.producer_cache_path()),
                "objects": len(objects),
                "window": cache.get("window"),
            }
        )
        return 0

    try:
        payload = _read_input(args.input)
    except (OSError, ValueError) as exc:
        print(f"read_cursor: cannot read input: {exc}", file=sys.stderr)
        return 2

    try:
        now = _parse_now(getattr(args, "now", None))
    except ValueError as exc:
        print(f"read_cursor: invalid --now timestamp: {exc}", file=sys.stderr)
        return 2

    try:
        if args.command == "plan":
            result = producer_rules.plan_reads(
                _listed(payload),
                path=path,
                now=now,
                budget=args.budget,
                window_seconds=args.window,
            )
            _dump(result.as_dict())
            return 0
        recorded = producer_rules.record_reads(
            _listed(payload), path=path, now=now, window_seconds=args.window
        )
        _dump({"recorded": recorded})
        return 0
    except producer_rules.CursorError as exc:
        print(f"read_cursor: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
