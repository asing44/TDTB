#!/usr/bin/env python3
"""plan_diff.py - repeatable before/after diff of TDTB's Capacities plan rows.

Two commits, one cache, one day: this tool materializes each git ref into a
throwaway detached worktree, runs THAT ref's own offline projection over a
private copy of the machine-local TDTB state, and reports how the plan rows
changed. It exists because a display-only change (hiding a Task embedded in an
assigned parent's page body) can only be proven against the owner's real cached
generation, and "looks right in a fixture" is not that proof.

The same diff also runs the other way round: one code ref, two saved DATA
snapshots. That is the Capacities-property-rework workflow - save a baseline
snapshot, rework and refresh Capacities, then diff baseline vs current with
identical code, so a row difference can only come from the data.

Usage::

    python3 tools/plan_diff.py
    python3 tools/plan_diff.py --base d47b8c7 --head HEAD --day 2026-11-17
    python3 tools/plan_diff.py --md-out docs/reports/report.md --json-out /tmp/diff.json
    python3 tools/plan_diff.py --fail-on-unexplained

    # Baseline -> rework -> diff, one code ref, two snapshots
    python3 tools/plan_diff.py snapshot --out /tmp/tdtb-baseline
    #   ... rework Capacities, then refresh the live generation ...
    python3 tools/plan_diff.py snapshot --out /tmp/tdtb-current
    python3 tools/plan_diff.py --base-state /tmp/tdtb-baseline \
        --head-state /tmp/tdtb-current --ref worktree --day 2026-11-17

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
* **Snapshot mode reads, never writes.** ``snapshot`` copies only the parts
  this diff needs (``state/`` including the refresh caches, contracts, and
  ``property-names.json``; ``config.json``; ``producer-rules.json``;
  ``tdtb-capacities-*.json``) and excludes locks, environment/credential files,
  ``capacities-mcp/``, ``backups/``, and older snapshots. The source home is
  opened read-only.
* **Data mode answers the same question about content.** With
  ``--base-state``/``--head-state`` both sides are projected by ONE code ref,
  each over its own private temp copy of its state dir, and the logical day is
  pinned identically for both sides so date drift cannot read as change.
* **Capacities only.** Todoist has no comparable offline cache, so it is not
  evaluated; the report says so instead of showing half a plan.
* **Pure read.** No network, no credential, no provider, no source write: the
  only traffic is local file reads, ``git worktree``, and (for ``snapshot``)
  the new snapshot directory itself.

Exit codes: 0 report produced (differences included), 1 ``--fail-on-unexplained``
and at least one row vanished with no recorded reason, 2 setup failure (missing
ref, no cache, a child that could not read the generation, a snapshot target
that would clobber its source or an existing snapshot).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

TOOL = "plan_diff"
DEFAULT_BASE = "d47b8c7"
DEFAULT_HEAD = "HEAD"

MODE_REF = "ref"
MODE_DATA = "data"

#: The only files taken from a TDTB home ROOT by ``snapshot``. Everything else
#: at that level is a backup, a lock, a credential, or an older snapshot.
SNAPSHOT_ROOT_FILES = ("config.json", "producer-rules.json")
SNAPSHOT_ROOT_GLOBS = ("tdtb-capacities-*.json",)
#: Home-root directories that never belong in a snapshot.
SNAPSHOT_SKIP_DIRS = ("backups", "capacities-mcp", "snapshots")

#: Reason code the head build records for a task hidden inside an assigned
#: parent's page body. Kept as a literal so this tool never imports app code.
HIDDEN_REASON = "embedded-in-assigned-parent"

#: Logical-day probe: the app's own effective-date rule, asked of the repo's
#: code rather than re-derived here (a local mirror would drift).
LOGICAL_DAY_PROBE = r'''
import sys
from datetime import datetime

sys.path.insert(0, sys.argv[1] + "/app")
sys.path.insert(0, sys.argv[1] + "/app/gather")

import tdtb_gather as gather

print(gather.effective_date(datetime.now()).isoformat())
'''

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
# Snapshot: the plan-diff parts of a TDTB home, copied out read-only
# ---------------------------------------------------------------------------


def _is_excluded_name(name: str) -> bool:
    """Lock files and environment/credential files are never copied."""
    if name.endswith(".lock"):
        return True
    return name == "env" or name.startswith(".env") or name.endswith(".env")


def _ignore_excluded(directory: str, names: list[str]) -> set[str]:
    """``copytree`` hook: drop locks and environment files at every depth."""
    return {name for name in names if _is_excluded_name(name)}


def copy_home_parts(source: Path, target: Path) -> list[str]:
    """Copy the plan-diff parts of ``source`` into ``target``.

    Only ``state/`` (which carries the refresh caches, structure contracts, and
    ``property-names.json``), ``config.json``, ``producer-rules.json``, and
    ``tdtb-capacities-*.json`` are taken; locks, ``env``/``.env*``,
    ``capacities-mcp/``, ``backups/``, and old snapshots never are. The source
    tree is only read, never created in or written to.
    """
    target.mkdir(parents=True, exist_ok=True)
    os.chmod(target, 0o700)
    copied: list[str] = []
    for name in SNAPSHOT_ROOT_FILES:
        path = source / name
        if path.is_file() and not _is_excluded_name(name):
            shutil.copy2(path, target / name)
            copied.append(name)
    for pattern in SNAPSHOT_ROOT_GLOBS:
        for path in sorted(source.glob(pattern)):
            if not path.is_file() or path.name in SNAPSHOT_ROOT_FILES:
                continue
            if _is_excluded_name(path.name):
                continue
            shutil.copy2(path, target / path.name)
            copied.append(path.name)
    state = source / "state"
    if state.is_dir():
        shutil.copytree(
            state, target / "state", ignore=_ignore_excluded, symlinks=True
        )
        copied.append("state/")
    return copied


def _logical_day(repo: Path | None, python: str) -> str | None:
    """Ask the repo's own code for the effective logical day (``None`` if it
    cannot be asked). Called only by ``snapshot``: the diff itself always gets
    the day from the evaluation."""
    if repo is None or not (repo / "app" / "gather").is_dir():
        return None
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    done = _run([python, "-c", LOGICAL_DAY_PROBE, str(repo)], cwd=repo, env=env)
    if done.returncode != 0:
        return None
    text = done.stdout.strip().splitlines()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text[-1]).date().isoformat()
    except ValueError:
        return None


def snapshot(
    source_home: Path,
    out: Path | None,
    *,
    day: str | None = None,
    repo: Path | None = None,
    python: str = sys.executable,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Save the plan-diff parts of ``source_home`` as a named snapshot.

    Returns the manifest that was written next to the copied files. Raises
    ``FileNotFoundError`` when the source has no ``state/``, ``ValueError`` when
    ``out`` would clobber the source itself, and ``FileExistsError`` when
    ``out`` is already a non-empty directory: a snapshot is evidence, so it is
    never overwritten in place.
    """
    source_home = Path(source_home).expanduser().resolve()
    if not (source_home / "state").is_dir():
        raise FileNotFoundError(f"no TDTB state at {source_home}")
    stamp = now or datetime.now()
    if out is None:
        out = source_home / "snapshots" / f"plan-diff-{stamp:%Y%m%d-%H%M%S}"
    else:
        out = Path(out).expanduser().resolve()
    if out == source_home or out in source_home.parents:
        raise ValueError(
            f"refusing to snapshot into {out}: that is the source home or above it"
        )
    if out.exists():
        if not out.is_dir():
            raise FileExistsError(f"snapshot target is not a directory: {out}")
        if any(out.iterdir()):
            raise FileExistsError(f"snapshot target is not empty: {out}")
    copied = copy_home_parts(source_home, out)
    git_head = None
    if repo is not None and (repo / ".git").exists():
        try:
            git_head = _resolve_ref(repo, "HEAD")
        except RuntimeError:
            git_head = None
    manifest = {
        "tool": TOOL,
        "kind": "plan-diff-snapshot",
        "created_at": stamp.astimezone().isoformat(timespec="seconds"),
        "source_home": str(source_home),
        "git_head": git_head,
        "logical_day": day or _logical_day(repo, python) or stamp.date().isoformat(),
        "day_pinned": bool(day),
        "path": str(out),
        "file_count": sum(1 for path in out.rglob("*") if path.is_file()),
        "entries": copied,
        "excluded": [
            "*.lock",
            "env/.env*",
            *SNAPSHOT_SKIP_DIRS,
        ],
    }
    (out / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------


def _harness(
    code_dir: Path,
    home: Path,
    *,
    work_root: Path,
    day: str | None,
    python: str,
    label: str,
) -> dict[str, Any]:
    """Run one offline projection of ``home`` with the code at ``code_dir``."""
    harness = work_root / "harness.py"
    harness.write_text(HARNESS, encoding="utf-8")
    env = dict(os.environ)
    env["TDTB_HOME"] = str(home)
    env.pop("TDTB_REPO_CONFIG", None)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    done = _run(
        [python, str(harness), str(code_dir), str(home), day or "-"],
        cwd=code_dir,
        env=env,
    )
    try:
        payload = json.loads(done.stdout)
    except ValueError:
        raise RuntimeError(
            f"{label}: evaluation produced no report "
            f"(exit {done.returncode}): {done.stderr.strip()[-400:]}"
        ) from None
    if not payload.get("ok"):
        raise RuntimeError(
            f"{label}: evaluation refused "
            f"({payload.get('error')}: {payload.get('message')})"
        )
    return payload


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
        payload = _harness(
            worktree, home, work_root=work_root, day=day, python=python, label=ref
        )
        payload["ref"] = ref
        payload["sha"] = sha
        return payload
    finally:
        _run(["git", "worktree", "remove", "--force", str(worktree)], cwd=repo)
        _run(["git", "worktree", "prune"], cwd=repo)


def _materialize_code(
    repo: Path, ref: str, work_root: Path
) -> tuple[Path, str, Path | None]:
    """Return ``(code_dir, sha, worktree_to_remove)`` for a single code ref.

    ``ref`` may be any git ref, or ``worktree`` for this repository exactly as
    it stands on disk (uncommitted edits included, evaluated at ``HEAD``'s sha
    for provenance).
    """
    if ref.strip().lower() in ("worktree", "-", "."):
        return repo, _resolve_ref(repo, "HEAD"), None
    sha = _resolve_ref(repo, ref)
    worktree = work_root / f"wt-{ref.replace('/', '-')}"
    _git(repo, "worktree", "add", "--detach", str(worktree), sha)
    return worktree, sha, worktree


def evaluate_state(
    code_dir: Path,
    home: Path,
    *,
    work_root: Path,
    day: str | None,
    python: str,
    label: str,
    sha: str | None,
    state_path: Path,
) -> dict[str, Any]:
    """Project one state snapshot through ``code_dir`` (data mode)."""
    payload = _harness(
        code_dir, home, work_root=work_root, day=day, python=python, label=label
    )
    payload["ref"] = f"state:{state_path}"
    payload["sha"] = sha or ""
    return payload


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


def diff(
    base: dict[str, Any],
    head: dict[str, Any],
    *,
    mode: str = MODE_REF,
    sources: dict[str, Any] | None = None,
    code: dict[str, Any] | None = None,
) -> dict[str, Any]:
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
        "mode": mode,
        "code": code or {"ref": head.get("ref"), "sha": head.get("sha")},
        "sources": sources or {},
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
    mode = report.get("mode", MODE_REF)
    sources = report.get("sources", {}) or {}
    code = report.get("code", {}) or {}
    code_sha = (code.get("sha") or "")[:7]
    lines: list[str] = []
    lines.append(
        "# Capacities plan-row diff: child tasks embedded in assigned parents"
        if mode == MODE_REF
        else "# Capacities plan-row diff: base snapshot vs head snapshot"
    )
    lines.append("")
    if mode == MODE_DATA:
        lines.append(
            f"Mode **data** (two state snapshots, one code ref) - code `{code.get('ref')}`"
            f" ({code_sha})."
        )
        lines.append("")
        lines.append(f"- Base state: `{(sources.get('base') or {}).get('path')}`")
        lines.append(f"- Head state: `{(sources.get('head') or {}).get('path')}`")
    else:
        lines.append(
            "Mode **ref** (two git refs, one shared state copy) - code "
            f"`{base.get('ref')}`..`{head.get('ref')}`."
        )
        lines.append("")
        lines.append(f"- State source (shared by both sides): `{(sources.get('base') or {}).get('path')}`")
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
        rows_by_identity = {
            _identity(row): row
            for row in [*report["only_in_base"], *report["only_in_head"]]
        }
        by_parent = {
            (entry["parent_id"], entry["parent_title"]): entry
            for entry in report["hidden"]
        }
        for entry in report["vanished"]:
            if entry.get("tier") != "membership":
                continue
            row = rows_by_identity.get(entry["identity"]) or {}
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
    if mode == MODE_DATA:
        lines.append(
            "- Both columns come from the SAME code ref's offline projection "
            "(`read_direct_intake` + `load_direct_rows`), each over its own private "
            "copy of its state dir; the logical day is pinned identically on both "
            "sides, so a row difference is a data difference. No network, no "
            "credential, no provider call, no source write."
        )
    else:
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
            "generation, or between two saved state snapshots under one code ref "
            "(read-only, offline, Capacities-only)."
        ),
    )
    parser.add_argument(
        "command",
        nargs="?",
        choices=("diff", "snapshot"),
        default="diff",
        help=(
            "diff (default): compare two sides; snapshot: save the plan-diff parts "
            "of a TDTB home into a named directory"
        ),
    )
    parser.add_argument("--base", default=DEFAULT_BASE, help="base git ref")
    parser.add_argument("--head", default=DEFAULT_HEAD, help="head git ref")
    parser.add_argument(
        "--day",
        default=None,
        help=(
            "logical day (YYYY-MM-DD); default: the app's effective today, pinned "
            "identically on both sides"
        ),
    )
    parser.add_argument(
        "--state-dir",
        default=None,
        help="TDTB state dir to copy and evaluate (default: a temp copy of the app home)",
    )
    parser.add_argument(
        "--base-state",
        default=None,
        help=(
            "data mode: base state snapshot (or TDTB home) to project; requires "
            "--head-state"
        ),
    )
    parser.add_argument(
        "--head-state",
        default=None,
        help="data mode: head state snapshot (or TDTB home) to project",
    )
    parser.add_argument(
        "--ref",
        default=DEFAULT_HEAD,
        help=(
            "data mode: the ONE code ref that projects both sides (default HEAD; "
            "`worktree` uses this repository as it stands, uncommitted edits included)"
        ),
    )
    parser.add_argument(
        "--home",
        default=None,
        help="snapshot: TDTB home to snapshot (default: $TDTB_HOME or ~/.config/tdtb)",
    )
    parser.add_argument(
        "--out",
        default=None,
        help=(
            "snapshot: destination directory (default: "
            "$TDTB_HOME/snapshots/plan-diff-YYYYMMDD-HHMMSS)"
        ),
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


def _run_snapshot(args: argparse.Namespace, repo: Path) -> int:
    source_home = Path(args.home).expanduser() if args.home else _app_home()
    try:
        manifest = snapshot(
            source_home,
            Path(args.out) if args.out else None,
            day=args.day,
            repo=repo,
            python=args.python,
        )
    except (FileNotFoundError, FileExistsError, ValueError) as exc:
        print(f"{TOOL}: {exc}", file=sys.stderr)
        return 2
    print(
        f"{TOOL}: snapshot {manifest['path']} "
        f"({manifest['file_count']} files, logical day {manifest['logical_day']}, "
        f"git {str(manifest['git_head'])[:7]})",
        file=sys.stderr,
    )
    return 0


def _data_mode_requested(args: argparse.Namespace) -> bool:
    return bool(args.base_state or args.head_state)


def main(argv: Sequence[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    args = build_parser().parse_args(raw)
    repo = Path(args.repo).expanduser().resolve() if args.repo else Path(__file__).resolve().parents[1]
    if args.command == "snapshot":
        return _run_snapshot(args, repo)

    data_mode = _data_mode_requested(args)
    if data_mode:
        if not args.base_state or not args.head_state:
            print(
                f"{TOOL}: data mode needs BOTH --base-state and --head-state",
                file=sys.stderr,
            )
            return 2
        if "--base" in raw or "--head" in raw or "--state-dir" in raw:
            print(
                f"{TOOL}: --base/--head/--state-dir are ref mode; data mode uses "
                "--base-state/--head-state/--ref",
                file=sys.stderr,
            )
            return 2
        base_state = Path(args.base_state).expanduser().resolve()
        head_state = Path(args.head_state).expanduser().resolve()
        for side in (base_state, head_state):
            if not (side / "state").is_dir():
                print(f"{TOOL}: no TDTB state at {side}", file=sys.stderr)
                return 2
    else:
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

    prefix = "tdtb-plan-diff-data-" if data_mode else "tdtb-plan-diff-"
    root = Path(tempfile.mkdtemp(prefix=prefix))
    worktree_to_remove: Path | None = None
    try:
        if data_mode:
            code_dir, sha, worktree_to_remove = _materialize_code(
                repo, args.ref, root
            )
            base_home = root / "base" / "tdtb-home"
            head_home = root / "head" / "tdtb-home"
            copy_home_parts(base_state, base_home)
            copy_home_parts(head_state, head_home)
            base = evaluate_state(
                code_dir,
                base_home,
                work_root=root,
                day=args.day,
                python=args.python,
                label="base",
                sha=sha,
                state_path=base_state,
            )
            head = evaluate_state(
                code_dir,
                head_home,
                work_root=root,
                day=args.day,
                python=args.python,
                label="head",
                sha=sha,
                state_path=head_state,
            )
            mode = MODE_DATA
            sources = {
                "base": {"kind": "state", "path": str(base_state)},
                "head": {"kind": "state", "path": str(head_state)},
            }
            code = {"ref": args.ref, "sha": sha}
        else:
            home = _copy_state(source_home, root)
            base = evaluate_ref(
                repo, args.base, work_root=root, home=home, day=args.day, python=args.python
            )
            head = evaluate_ref(
                repo, args.head, work_root=root, home=home, day=args.day, python=args.python
            )
            mode = MODE_REF
            sources = {
                "base": {"kind": "state", "path": str(source_home), "shared": True},
                "head": {"kind": "state", "path": str(source_home), "shared": True},
            }
            code = {"ref": f"{args.base}..{args.head}", "sha": head.get("sha")}
        if base.get("day") != head.get("day"):
            print(
                f"{TOOL}: the two sides disagree on the logical day "
                f"({base.get('day')} vs {head.get('day')})",
                file=sys.stderr,
            )
            return 2
        report = diff(base, head, mode=mode, sources=sources, code=code)
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
        if worktree_to_remove is not None:
            _run(
                ["git", "worktree", "remove", "--force", str(worktree_to_remove)],
                cwd=repo,
            )
            _run(["git", "worktree", "prune"], cwd=repo)
        if args.keep:
            print(f"{TOOL}: kept {root}", file=sys.stderr)
        else:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
