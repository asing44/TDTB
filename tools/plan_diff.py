#!/usr/bin/env python3
"""plan_diff.py - repeatable before/after diff of TDTB's Capacities plan rows.

Two commits, one cache, one day: this tool materializes each git ref into a
throwaway detached worktree, runs THAT ref's own offline projection over a
private copy of the machine-local TDTB state, and reports how the plan rows
changed. It exists because a display-only change (hiding a Task embedded in an
assigned parent's page body) can only be proven against the owner's real cached
generation, and "looks right in a fixture" is not that proof.

Usage::

    python3 tools/plan_diff.py
    python3 tools/plan_diff.py --base d47b8c7 --head HEAD --day 2026-11-17
    python3 tools/plan_diff.py --md-out docs/reports/report.md --json-out /tmp/diff.json
    python3 tools/plan_diff.py --fail-on-unexplained

Design notes:

* **One generation, two evaluators.** The cache is copied ONCE and both refs
  read the identical copy, so a row difference can only come from code. The
  copy is made under ``--state-dir`` (default: a temp copy of ``$TDTB_HOME`` or
  ``~/.config/tdtb``) and is never written back; the real app home is only
  read.
* **Each ref evaluates itself.** The child process runs the ref's own
  ``capacities_builder.read_direct_intake`` + ``capacities_intake.load_direct_rows``
  - the same offline path the live ``/plan-inputs`` uses - so the base column
  is what the base code WOULD have shown, not a re-implementation. Row counts,
  warnings, drop tallies, and the parent-page read diagnostics therefore all
  come from the ref under test.
* **Capacities only.** Todoist has no comparable offline cache, so it is not
  evaluated; the report says so instead of showing half a plan.
* **Pure read.** No network, no credential, no provider, no source write: the
  only traffic is local file reads and ``git worktree``.

Exit codes: 0 report produced (differences included), 1 ``--fail-on-unexplained``
and at least one row vanished with no recorded reason, 2 setup failure (missing
ref, no cache, a child that could not read the generation).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Sequence

TOOL = "plan_diff"
DEFAULT_BASE = "d47b8c7"
DEFAULT_HEAD = "HEAD"

#: Reason code the head build records for a task hidden inside an assigned
#: parent's page body. Kept as a literal so this tool never imports app code.
HIDDEN_REASON = "embedded-in-assigned-parent"

#: Child-process harness. Executed by each ref's own interpreter path with that
#: ref's ``app/`` on ``sys.path``, so every import below resolves to the ref.
HARNESS = r'''
import json
import sys
from datetime import date, datetime, timedelta

worktree, home, day_arg = sys.argv[1], sys.argv[2], sys.argv[3]
sys.path.insert(0, worktree + "/app")
# The app's own path shim for the gather helper (same trick as capacities_intake).
sys.path.insert(0, worktree + "/app/gather")

import capacities_adapter
import capacities_builder
import capacities_intake
import tdtb_gather as gather

try:  # the child-hiding feature is absent on a pre-change ref
    import capacities_embedded
except ImportError:
    capacities_embedded = None


def fail(code, message):
    json.dump({"ok": False, "error": code, "message": message}, sys.stdout)
    raise SystemExit(0)


def effective(target):
    now = datetime.now()
    if target is None:
        return now, gather.effective_date(now)
    current = gather.effective_date(now)
    # The effective date is monotone in the timestamp, so shifting by whole days
    # lands on the target; hour nudges absorb a neighbouring day-boundary rule.
    now = now + timedelta(days=(target - current).days)
    for _ in range(48):
        if gather.effective_date(now) == target:
            return now, target
        step = timedelta(hours=1) if gather.effective_date(now) < target else timedelta(hours=-1)
        now = now + step
    return now, gather.effective_date(now)


captured = {}
_original = capacities_adapter.CapacitiesAdapter.items_for_day_from_objects


def _recording(self, logical_day, objects):
    result = _original(self, logical_day, objects)
    captured["result"] = result
    captured["adapter"] = self
    captured["object_count"] = len(list(objects))
    return result


capacities_adapter.CapacitiesAdapter.items_for_day_from_objects = _recording

target = None if day_arg in ("", "-") else date.fromisoformat(day_arg)
now, logical_day = effective(target)
if target is not None and logical_day != target:
    fail("day_unreachable", "no wall-clock time maps to %s" % target)

try:
    read = capacities_builder.read_direct_intake()
except capacities_builder.CapacitiesSourceStoreError as exc:
    fail("source_unreadable", type(exc).__name__)
if read is None:
    fail("unconfigured", "no Capacities source record in this app home")

assigned, pool, warnings, coverage = capacities_intake.load_direct_rows(
    now=now, read=read
)
result = captured.get("result")

titles = {}
for structure in read.structures or ():
    if isinstance(structure, dict) and isinstance(structure.get("id"), str):
        title = structure.get("title")
        if isinstance(title, str) and title.strip():
            titles[structure["id"]] = title.strip()

try:
    embedded = {
        child: [[parent_id, parent_title] for parent_id, parent_title in parents]
        for child, parents in capacities_embedded.embedded_child_map(
            read.objects
        ).items()
    }
except Exception:
    embedded = {}

hidden = []
for key, count in sorted(
    (getattr(captured.get("adapter"), "_embedded_hidden", None) or {}).items()
):
    structure_id, parent_id, parent_title = key
    hidden.append(
        {
            "structure_id": structure_id,
            "parent_id": parent_id,
            "parent_title": parent_title,
            "count": count,
        }
    )

snapshot = read.snapshot

def rows(source):
    out = []
    for row in source:
        out.append({key: row[key] for key in row})
    return out

json.dump(
    {
        "ok": True,
        "day": logical_day.isoformat(),
        "space_id": read.space_id,
        "generation": None if snapshot is None else snapshot.generation,
        "installed_at": None if snapshot is None else snapshot.installed_at,
        "partial": None if snapshot is None else snapshot.partial,
        "unchecked_types": [] if snapshot is None else list(snapshot.unchecked_types),
        "member_count": 0 if snapshot is None else len(snapshot.members),
        "object_count": captured.get("object_count", 0),
        "unreadable": read.unreadable,
        "assigned": rows(assigned),
        "pool": rows(pool),
        "warnings": list(warnings) + list(read.warnings),
        "coverage": {
            "members": coverage.members,
            "evaluated": coverage.evaluated,
            "malformed": coverage.malformed,
        },
        "drop_tally": (
            {} if result is None else {k: dict(v) for k, v in result.drop_tally.items()}
        ),
        "contract_warnings": [] if result is None else list(result.warnings),
        "structures": titles,
        "embedded": embedded,
        "hidden": hidden,
    },
    sys.stdout,
    default=str,
)
'''


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _run(cmd: list[str], *, cwd: Path | None = None, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, cwd=str(cwd) if cwd else None, env=env, capture_output=True, text=True
    )


def _git(repo: Path, *args: str) -> str:
    done = _run(["git", *args], cwd=repo)
    if done.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {done.stderr.strip()}")
    return done.stdout.strip()


def _resolve_ref(repo: Path, ref: str) -> str:
    return _git(repo, "rev-parse", "--verify", f"{ref}^{{commit}}")


def _app_home() -> Path:
    """The machine-local app home, read-only (``$TDTB_HOME`` or the default)."""
    override = os.environ.get("TDTB_HOME")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".config" / "tdtb"


def _copy_state(home: Path, root: Path) -> Path:
    """Copy the app home into ``root`` (mode 700) and return the copy.

    The copy is what both refs evaluate against. It is never written back, and
    it is removed with the temp root unless ``--keep``. No credential value is
    ever printed or evaluated: only the refresh cache inside it is read.
    """
    target = root / "tdtb-home"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(home, target, symlinks=True)
    os.chmod(target, 0o700)
    return target


# ---------------------------------------------------------------------------
# One ref's evaluation
# ---------------------------------------------------------------------------


def evaluate_ref(
    repo: Path,
    ref: str,
    *,
    work_root: Path,
    home: Path,
    day: str | None,
    python: str,
) -> dict[str, Any]:
    """Materialize ``ref`` detached and run the offline projection inside it."""
    sha = _resolve_ref(repo, ref)
    worktree = work_root / f"wt-{ref.replace('/', '-')}"
    _git(repo, "worktree", "add", "--detach", str(worktree), sha)
    try:
        harness = work_root / "harness.py"
        harness.write_text(HARNESS, encoding="utf-8")
        env = dict(os.environ)
        env["TDTB_HOME"] = str(home)
        env.pop("TDTB_REPO_CONFIG", None)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        done = _run(
            [python, str(harness), str(worktree), str(home), day or "-"],
            cwd=worktree,
            env=env,
        )
        try:
            payload = json.loads(done.stdout)
        except ValueError:
            raise RuntimeError(
                f"{ref}: evaluation produced no report "
                f"(exit {done.returncode}): {done.stderr.strip()[-400:]}"
            ) from None
        if not payload.get("ok"):
            raise RuntimeError(
                f"{ref}: evaluation refused "
                f"({payload.get('error')}: {payload.get('message')})"
            )
        payload["ref"] = ref
        payload["sha"] = sha
        return payload
    finally:
        _run(["git", "worktree", "remove", "--force", str(worktree)], cwd=repo)
        _run(["git", "worktree", "prune"], cwd=repo)


# ---------------------------------------------------------------------------
# Diff
# ---------------------------------------------------------------------------


def _identity(row: dict[str, Any]) -> str:
    for key in ("identity", "capacities_id", "path", "name"):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "<unidentified>"


def _index(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """``identity -> row`` over assigned AND pool rows (never the union minus)."""
    out: dict[str, dict[str, Any]] = {}
    for row in [*payload.get("assigned", []), *payload.get("pool", [])]:
        out.setdefault(_identity(row), row)
    return out


def _comparable(value: Any) -> str:
    """A stable, small rendering so a field diff never floods the report."""
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True, default=str)
    return "" if value is None else str(value)


def _changed_fields(
    base: dict[str, Any], head: dict[str, Any]
) -> list[dict[str, Any]]:
    changes = []
    for key in sorted(set(base) | set(head)):
        before, after = base.get(key), head.get(key)
        if _comparable(before) == _comparable(after):
            continue
        changes.append(
            {"field": key, "base": before, "head": after}
        )
    return changes


def _classify_vanished(
    identity: str,
    row: dict[str, Any],
    head: dict[str, Any],
    base_tally: dict[str, dict[str, int]],
) -> dict[str, Any]:
    """Why a base row is gone in head: a recorded drop reason, or nothing.

    Tier 1 (definitive) is page-body membership: the cached content maps this
    child to a parent that the head build recorded as hiding something.
    Tier 2 is a structure-level drop-tally delta for the SAME structure: some
    rows were dropped for a recorded reason, but the tally cannot name which,
    so the row is reported as tally-explained rather than silently acceptable.
    """
    child_id = row.get("capacities_id")
    structure_id = row.get("capacities_structure_id") or ""
    hidden_parents = {
        (entry["parent_id"], entry["parent_title"])
        for entry in head.get("hidden", [])
        if entry.get("structure_id") == structure_id
    }
    for parent_id, parent_title in (head.get("embedded", {}) or {}).get(
        str(child_id), []
    ):
        if (parent_id, parent_title) in hidden_parents:
            return {
                "identity": identity,
                "explained": True,
                "tier": "membership",
                "reason": HIDDEN_REASON,
                "parent_id": parent_id,
                "parent_title": parent_title,
            }
    base_counts = base_tally.get(structure_id, {})
    head_counts = head.get("drop_tally", {}).get(structure_id, {})
    deltas = {
        reason: head_counts.get(reason, 0) - base_counts.get(reason, 0)
        for reason in set(head_counts) | set(base_counts)
    }
    grown = {reason: delta for reason, delta in deltas.items() if delta > 0}
    if grown:
        return {
            "identity": identity,
            "explained": True,
            "tier": "drop-tally",
            "reason": ", ".join(f"{reason} +{count}" for reason, count in sorted(grown.items())),
            "parent_id": None,
            "parent_title": None,
        }
    return {
        "identity": identity,
        "explained": False,
        "tier": "none",
        "reason": None,
        "parent_id": None,
        "parent_title": None,
    }


def diff(base: dict[str, Any], head: dict[str, Any]) -> dict[str, Any]:
    base_rows, head_rows = _index(base), _index(head)
    only_base = [base_rows[key] for key in sorted(set(base_rows) - set(head_rows))]
    only_head = [head_rows[key] for key in sorted(set(head_rows) - set(base_rows))]
    common = sorted(set(base_rows) & set(head_rows))
    changed = []
    for key in common:
        fields = _changed_fields(base_rows[key], head_rows[key])
        if fields:
            changed.append(
                {"identity": key, "row": head_rows[key], "fields": fields}
            )

    base_tally = base.get("drop_tally", {})
    vanished = [
        _classify_vanished(_identity(row), row, head, base_tally)
        for row in only_base
    ]
    unexplained = [entry for entry in vanished if not entry["explained"]]

    return {
        "day": head.get("day"),
        "base": {
            "ref": base.get("ref"),
            "sha": base.get("sha"),
            "assigned": len(base.get("assigned", [])),
            "pool": len(base.get("pool", [])),
            "warnings": base.get("warnings", []),
            "drop_tally": base.get("drop_tally", {}),
            "hidden": base.get("hidden", []),
            "generation": base.get("generation"),
        },
        "head": {
            "ref": head.get("ref"),
            "sha": head.get("sha"),
            "assigned": len(head.get("assigned", [])),
            "pool": len(head.get("pool", [])),
            "warnings": head.get("warnings", []),
            "drop_tally": head.get("drop_tally", {}),
            "hidden": head.get("hidden", []),
            "generation": head.get("generation"),
        },
        "cache": {
            "space_id": head.get("space_id"),
            "generation": head.get("generation"),
            "installed_at": head.get("installed_at"),
            "member_count": head.get("member_count"),
            "unreadable": head.get("unreadable"),
            "partial": head.get("partial"),
        },
        "structures": head.get("structures", {}),
        "only_in_base": only_base,
        "only_in_head": only_head,
        "changed": changed,
        "vanished": vanished,
        "unexplained": unexplained,
        "hidden": head.get("hidden", []),
        "warnings": {"base": base.get("warnings", []), "head": head.get("warnings", [])},
        "totals": {
            "base_rows": len(base_rows),
            "head_rows": len(head_rows),
            "only_in_base": len(only_base),
            "only_in_head": len(only_head),
            "changed": len(changed),
            "hidden_children": sum(entry.get("count", 0) for entry in head.get("hidden", [])),
            "unexplained": len(unexplained),
        },
    }


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def _structure_label(report: dict[str, Any], row: dict[str, Any]) -> str:
    structure_id = str(row.get("capacities_structure_id") or "")
    return report.get("structures", {}).get(structure_id) or structure_id or "?"


def _row_line(report: dict[str, Any], row: dict[str, Any]) -> str:
    return (
        f"- `{row.get('capacities_id')}` **{row.get('name')}**"
        f" - {_structure_label(report, row)}"
        f" - assigned={'yes' if row.get('assigned') is True else 'no'}"
        f" - {row.get('duration_minutes')}m"
    )


def render_markdown(report: dict[str, Any], *, day: str | None) -> str:
    base, head = report["base"], report["head"]
    totals = report["totals"]
    cache = report["cache"]
    lines: list[str] = []
    lines.append("# Capacities plan-row diff: child tasks embedded in assigned parents")
    lines.append("")
    lines.append(
        f"Day **{day or report['day']}**, base `{base['ref']}` ({base['sha'][:7]}) "
        f"vs head `{head['ref']}` ({head['sha'][:7]}), one shared cached generation "
        f"(generation {cache['generation']}, {cache['member_count']} members, "
        f"space `{cache['space_id']}`)."
    )
    lines.append("")
    lines.append(
        f"Rows: base {totals['base_rows']} - head {totals['head_rows']}. "
        f"Only in base **{totals['only_in_base']}**, only in head "
        f"**{totals['only_in_head']}**, changed **{totals['changed']}**, "
        f"hidden children **{totals['hidden_children']}**, "
        f"unexplained disappearances **{totals['unexplained']}**."
    )
    lines.append("")
    if report["unexplained"]:
        lines.append("## Unexplained disappearances (regression)")
        lines.append("")
        lines.append(
            "These rows are present at the base ref and absent at head with NO "
            "recorded drop reason. That is a regression: the change must not remove "
            "a row it cannot account for."
        )
        lines.append("")
        for entry in report["unexplained"]:
            lines.append(f"- `{entry['identity']}`")
        lines.append("")
    else:
        lines.append(
            "No unexplained disappearances: every row absent at head carries a "
            "recorded drop reason."
        )
        lines.append("")

    lines.append("## Hidden child tasks")
    lines.append("")
    if not report["hidden"]:
        lines.append("None: no admitted parent's page body hid a child on this day.")
        lines.append("")
    else:
        lines.append("| Child | Title | Structure | Parent | Parent title | Reason |")
        lines.append("| --- | --- | --- | --- | --- | --- |")
        by_parent = {
            (entry["parent_id"], entry["parent_title"]): entry
            for entry in report["hidden"]
        }
        for entry in report["vanished"]:
            if entry.get("tier") != "membership":
                continue
            row = rows_by_child.get(entry["identity"]) or {}
            lines.append(
                f"| `{row.get('capacities_id')}` | {row.get('name')} | "
                f"{_structure_label(report, row)} | `{entry['parent_id']}` | "
                f"{entry['parent_title']} | `{entry['reason']}` |"
            )
        lines.append("")
        lines.append("Parents that hid at least one child:")
        lines.append("")
        for (parent_id, parent_title), entry in sorted(by_parent.items()):
            lines.append(
                f"- `{parent_id}` **{parent_title}** - {entry['count']} child(ren)"
                f" in {entry['structure_id']}"
            )
        lines.append("")

    lines.append("## Rows only in base")
    lines.append("")
    if not report["only_in_base"]:
        lines.append("None.")
    else:
        for row in report["only_in_base"]:
            entry = next(
                item for item in report["vanished"] if item["identity"] == _identity(row)
            )
            why = (
                f"`{entry['reason']}`"
                + (f" (parent `{entry['parent_id']}`)" if entry["parent_id"] else "")
                if entry["explained"]
                else "**UNEXPLAINED**"
            )
            lines.append(_row_line(report, row) + f" - {why}")
    lines.append("")

    lines.append("## Rows only in head")
    lines.append("")
    if not report["only_in_head"]:
        lines.append("None.")
    else:
        for row in report["only_in_head"]:
            lines.append(_row_line(report, row))
    lines.append("")

    lines.append("## Changed rows")
    lines.append("")
    if not report["changed"]:
        lines.append("None: every surviving row is field-for-field identical.")
    else:
        for entry in report["changed"]:
            lines.append(
                f"- `{entry['row'].get('capacities_id')}` **{entry['row'].get('name')}**"
            )
            for change in entry["fields"]:
                lines.append(
                    f"  - `{change['field']}`: "
                    f"`{_comparable(change['base'])}` -> `{_comparable(change['head'])}`"
                )
    lines.append("")

    lines.append("## Drop tallies")
    lines.append("")
    for side in ("base", "head"):
        tally = report[side]["drop_tally"]
        rendered = (
            "; ".join(
                f"{structure}: "
                + ", ".join(f"{reason} {count}" for reason, count in sorted(counts.items()))
                for structure, counts in sorted(tally.items())
            )
            or "none"
        )
        lines.append(f"- {side}: {rendered}")
    lines.append("")

    lines.append("## Plan warnings")
    lines.append("")
    for side in ("base", "head"):
        warnings = report["warnings"][side]
        lines.append(f"### {side}")
        lines.append("")
        if not warnings:
            lines.append("- none")
        else:
            for line in warnings:
                lines.append(f"- {line}")
        lines.append("")

    lines.append("## Scope and method")
    lines.append("")
    lines.append(
        "- Capacities only: Todoist has no comparable offline cache, so its rows "
        "are not evaluated here."
    )
    lines.append(
        "- Both columns come from each ref's own offline projection "
        "(`read_direct_intake` + `load_direct_rows`) over one copied cache; no "
        "network, no credential, no provider call, no source write."
    )
    lines.append(
        "- Rows are matched by identity across assigned AND pool, so an assignment "
        "change reads as a changed row, never a disappearance."
    )
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=TOOL,
        description=(
            "Diff TDTB Capacities plan rows between two git refs over one cached "
            "generation (read-only, offline, Capacities-only)."
        ),
    )
    parser.add_argument("--base", default=DEFAULT_BASE, help="base git ref")
    parser.add_argument("--head", default=DEFAULT_HEAD, help="head git ref")
    parser.add_argument(
        "--day",
        default=None,
        help="logical day (YYYY-MM-DD); default: the app's effective today",
    )
    parser.add_argument(
        "--state-dir",
        default=None,
        help="TDTB state dir to copy and evaluate (default: a temp copy of the app home)",
    )
    parser.add_argument(
        "--repo", default=None, help="repository root (default: this tool's repo)"
    )
    parser.add_argument(
        "--python",
        default=sys.executable,
        help="interpreter for the per-ref evaluation (default: this one)",
    )
    parser.add_argument("--md-out", default=None, help="write the markdown report here")
    parser.add_argument("--json-out", default=None, help="write the JSON result here")
    parser.add_argument(
        "--fail-on-unexplained",
        action="store_true",
        help="exit 1 when a row vanished with no recorded drop reason",
    )
    parser.add_argument(
        "--keep", action="store_true", help="keep the temp worktrees and state copy"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repo = Path(args.repo).expanduser().resolve() if args.repo else Path(__file__).resolve().parents[1]
    source_home = (
        Path(args.state_dir).expanduser().resolve()
        if args.state_dir
        else _app_home()
    )
    if not (source_home / "state").is_dir():
        print(f"{TOOL}: no TDTB state at {source_home}", file=sys.stderr)
        return 2
    if not (repo / ".git").exists():
        print(f"{TOOL}: not a git repository: {repo}", file=sys.stderr)
        return 2

    root = Path(tempfile.mkdtemp(prefix="tdtb-plan-diff-"))
    try:
        home = _copy_state(source_home, root)
        base = evaluate_ref(
            repo, args.base, work_root=root, home=home, day=args.day, python=args.python
        )
        head = evaluate_ref(
            repo, args.head, work_root=root, home=home, day=args.day, python=args.python
        )
        if base.get("day") != head.get("day"):
            print(
                f"{TOOL}: the two refs disagree on the logical day "
                f"({base.get('day')} vs {head.get('day')})",
                file=sys.stderr,
            )
            return 2
        report = diff(base, head)
        markdown = render_markdown(report, day=base.get("day"))
        if args.md_out:
            target = Path(args.md_out).expanduser()
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(markdown, encoding="utf-8")
        else:
            print(markdown)
        if args.json_out:
            target = Path(args.json_out).expanduser()
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                json.dumps(report, indent=2, sort_keys=True, default=str),
                encoding="utf-8",
            )
        if args.fail_on_unexplained and report["unexplained"]:
            print(
                f"{TOOL}: {len(report['unexplained'])} row(s) vanished with no "
                "recorded drop reason",
                file=sys.stderr,
            )
            return 1
        return 0
    except RuntimeError as exc:
        print(f"{TOOL}: {exc}", file=sys.stderr)
        return 2
    finally:
        if args.keep:
            print(f"{TOOL}: kept {root}", file=sys.stderr)
        else:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
