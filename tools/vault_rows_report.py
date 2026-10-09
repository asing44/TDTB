#!/usr/bin/env python3
"""vault_rows_report.py — what the S5 cutover would remove, listed for triage.

Slice S5 turns the vault gather off with ``sources.vault_enabled=false``. The
plan names the risk in one line: *"Silent data loss: ~96 rows vanish with
nothing flagging it."* This report is the mitigation — run it BEFORE flipping
the flag, read what would go, and decide which rows are worth re-homing into
Capacities or Todoist.

It is **read-only**. It writes nothing, anywhere: not the vault, not the app
home, not the artifact. It also works while the flag is still true, which is
the only state in which it is useful.

Row selection is delegated to ``gather.select_digest_notes`` — the same
function the live digest calls — so this report cannot drift from what the
digest would actually drop.

Usage::

    python tools/vault_rows_report.py --vault-root "/path/to/vault"
    python tools/vault_rows_report.py --vault-root "/path/to/vault" --format json
    python tools/vault_rows_report.py --vault-root "/path/to/vault" --today 2026-10-09
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path

_APP_DIR = Path(__file__).resolve().parent.parent / "app"
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))

_APP_DIR = Path(__file__).resolve().parent.parent / "app"
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))

# ``app/gather`` is a namespace package with no re-exports, so the app shims the
# directory onto sys.path and imports the module directly (see main.py). Match
# that exactly — a bare ``import gather`` resolves to an empty namespace here.
_GATHER_DIR = _APP_DIR / "gather"
if str(_GATHER_DIR) not in sys.path:
    sys.path.insert(0, str(_GATHER_DIR))

import tdtb_gather as gather  # noqa: E402  (path-shimmed import, as main.py does)


def _row_view(row: dict) -> dict:
    """The triage-relevant fields of one digest row.

    ``build_run_data`` rows carry no ``folder`` key — the vault-relative
    ``path`` is the only place the folder survives, so it is derived from
    there rather than left null (a report that cannot group is useless for
    triage)."""
    deadline = row.get("deadline")
    path = row.get("path") or ""
    folder = row.get("folder")
    if not folder and "/" in str(path):
        folder = str(path).rsplit("/", 1)[0]
    return {
        "name": row.get("name"),
        "path": path or None,
        "folder": folder,
        "types": row.get("types") or [],
        "deadline": deadline,
        "has_deadline": deadline is not None,
        "urgency": row.get("urgency"),
    }


def collect(vault_root: str | Path, today: date) -> dict:
    """The vault rows the digest would lose, grouped by top-level folder."""
    pool_notes, assigned_notes = gather.select_digest_notes(Path(vault_root), today)
    run_data = gather.build_run_data(pool_notes, assigned_notes, today)

    rows = [
        _row_view(row)
        for row in list(run_data.get("pool_items") or [])
        + list(run_data.get("assigned_items") or [])
    ]
    rows.sort(key=lambda r: (str(r.get("folder") or ""), str(r.get("name") or "")))

    by_folder: dict[str, int] = {}
    for row in rows:
        folder = str(row.get("folder") or "(no folder)")
        by_folder[folder] = by_folder.get(folder, 0) + 1

    top_level: dict[str, int] = {}
    for folder, count in by_folder.items():
        head = folder.split("/")[0].strip() or "(no folder)"
        top_level[head] = top_level.get(head, 0) + count

    return {
        "today": str(today),
        "vault_root": str(vault_root),
        "total": len(rows),
        "with_deadline": sum(1 for r in rows if r["has_deadline"]),
        "without_deadline": sum(1 for r in rows if not r["has_deadline"]),
        "by_top_level_folder": dict(
            sorted(top_level.items(), key=lambda kv: (-kv[1], kv[0]))
        ),
        "by_folder": dict(sorted(by_folder.items(), key=lambda kv: (-kv[1], kv[0]))),
        "rows": rows,
    }


def render_markdown(report: dict) -> str:
    out: list[str] = []
    out.append("# Vault rows the S5 cutover would remove")
    out.append("")
    out.append(f"- logical day: **{report['today']}**")
    out.append(f"- vault root: `{report['vault_root']}`")
    out.append(
        f"- **{report['total']} rows** "
        f"({report['with_deadline']} carry a deadline, "
        f"{report['without_deadline']} do not)"
    )
    out.append("")
    out.append("Flipping `sources.vault_enabled=false` stops the vault gather, so "
               "these rows leave the digest's candidate pool. Nothing is deleted — "
               "the notes stay in the vault and the flip is reversible by config.")
    out.append("")
    out.append("## By top-level folder")
    out.append("")
    out.append("| Folder | Rows |")
    out.append("| --- | ---: |")
    for folder, count in report["by_top_level_folder"].items():
        out.append(f"| `{folder}` | {count} |")
    out.append("")
    out.append("## Every row")
    out.append("")
    out.append("| Name | Folder | Types | Deadline | Urgency |")
    out.append("| --- | --- | --- | --- | --- |")
    for row in report["rows"]:
        types = ", ".join(str(t) for t in row["types"]) or "—"
        deadline = row["deadline"] or "—"
        urgency = row["urgency"] if row["urgency"] is not None else "—"
        out.append(
            f"| {row['name']} | `{row['folder'] or '—'}` | {types} | "
            f"{deadline} | {urgency} |"
        )
    out.append("")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="List the vault rows the S5 cutover would remove (read-only)."
    )
    parser.add_argument("--vault-root", required=True, help="Vault root to scan.")
    parser.add_argument(
        "--today",
        default=None,
        help="Logical day YYYY-MM-DD (default: the app's effective date).",
    )
    parser.add_argument("--format", choices=("md", "json"), default="md")
    args = parser.parse_args(argv)

    today = (
        date.fromisoformat(args.today)
        if args.today
        else gather.effective_date(datetime.now())
    )
    report = collect(args.vault_root, today)

    if args.format == "json":
        print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    else:
        print(render_markdown(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
