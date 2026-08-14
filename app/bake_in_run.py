#!/usr/bin/env python3
"""bake_in_run.py — T18: wires the bake-in differ (``bake_in_diff.py``) to a
real manifest/live-state and appends one row to ``bake-in-log.md``.

Thin by design (protocol § 2, "the differ owns the write"; deliverable
checklist item 2) — all classification logic lives in ``bake_in_diff.py``;
this module's only job is:

    shadow.diff_against_live(manifest, live_state)
        -> bake_in_diff.classify_bakein(...)
        -> bake_in_diff.day_verdict(...)
        -> one appended row in ``bake-in-log.md`` (protocol § 2.3 schema).

``run()`` is the tested surface — ``manifest`` and ``live_state`` are either
plain data (what a test hands it, no I/O) or a ``(vault_root, today) ->
data`` callable the caller supplies (what ``main()`` hands it, wrapping the
real gather/shadow calls). This mirrors ``shadow_run.py``'s
gather -> digest -> manifest -> diff pipeline but never performs the I/O
itself — ``main()`` builds the callables, ``run()`` only ever calls them.

``recon_fail`` / ``wrong_surface`` are **not** derived here — protocol § 2.2
says they come from "the driver's T14 commit report on app-days" (T15's
``orchestrate.py`` / ``commit.py`` ``WriterResult``s), and are 0 / False on
skill-days and shadow-only app runs. Wiring an app-day's real commit report
into those two arguments is the caller's job, not this module's.

Log semantics (protocol § 2.3): append-only. The header + separator are
written once, the first time the log file doesn't exist; every subsequent
call appends exactly one data row and never rewrites what's already there.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent / "gather"))

import tdtb_gather as gather  # noqa: E402
import shadow  # noqa: E402
import runstate  # noqa: E402
import todoist_client  # noqa: E402
from bake_in_diff import BakeInVerdict, classify_bakein, day_verdict  # noqa: E402

_VALID_DRIVERS = ("skill", "app")

_LOG_HEADER = "| date | driver | agree | unexplained | inconclusive | recon_fail | verdict | notes |"
_LOG_SEP = "|------|--------|-------|-------------|--------------|------------|---------|-------|"

# Default log path: the TASK dir's bake-in-log.md, i.e. one level up from
# app/ (this module's parent). Callers may override via the ``log_path`` arg.
DEFAULT_LOG_PATH = Path(__file__).resolve().parent.parent / "bake-in-log.md"


def _resolve(source: Any, vault_root: Path, today: date) -> Any:
    """Call ``source(vault_root, today)`` if it's callable, else return it
    as-is. Lets tests hand plain manifest/live-state data (no I/O) while
    ``main()`` hands closures over the real gather/shadow calls."""
    return source(vault_root, today) if callable(source) else source


def _escape_pipes(text: str) -> str:
    return text.replace("|", "\\|")


def build_log_row(
    today: date,
    driver: str,
    verdict_obj: BakeInVerdict,
    verdict_str: str,
    recon_fail: int,
) -> dict[str, Any]:
    """The § 2.3 row as a dict — one entry per log column. ``notes`` is the
    joined ``unexplained_notes()`` (or ``"—"`` when empty), stored raw here;
    pipe-escaping happens at render time (``_format_row``), the single place
    that turns any row dict into a literal markdown-table line — so a row
    handed to ``append_log_row`` from anywhere (not just this builder) still
    comes out escaped."""
    notes_list = verdict_obj.unexplained_notes()
    notes = "; ".join(notes_list) if notes_list else "—"
    return {
        "date": today.isoformat(),
        "driver": driver,
        "agree": verdict_obj.agree(),
        "unexplained": verdict_obj.unexplained(),
        "inconclusive": verdict_obj.inconclusive(),
        "recon_fail": recon_fail,
        "verdict": verdict_str,
        "notes": notes,
    }


def _format_row(row: dict[str, Any]) -> str:
    notes = _escape_pipes(str(row["notes"]))
    return (
        f"| {row['date']} | {row['driver']} | {row['agree']} | {row['unexplained']} | "
        f"{row['inconclusive']} | {row['recon_fail']} | {row['verdict']} | {notes} |"
    )


def append_log_row(log_path: Path | str, row: dict[str, Any]) -> Path:
    """Append-only write (protocol § 2.3): create the file with the header +
    separator when it doesn't exist yet, then always append exactly one data
    row. Never rewrites or reorders prior rows."""
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    is_new = not log_path.is_file()
    with log_path.open("a", encoding="utf-8") as f:
        if is_new:
            f.write(_LOG_HEADER + "\n")
            f.write(_LOG_SEP + "\n")
        f.write(_format_row(row) + "\n")
    return log_path


def run(
    vault_root: Path | str,
    today: date,
    driver: str,
    manifest: list[shadow.ManifestEntry] | Callable[[Path, date], list[shadow.ManifestEntry]],
    live_state: dict[str, Any] | Callable[[Path, date], dict[str, Any]],
    *,
    recon_fail: int = 0,
    wrong_surface: bool = False,
    log_path: Path | str | None = None,
) -> tuple[dict[str, Any], str]:
    """Run one bake-in day end-to-end and append its log row.

    ``manifest`` / ``live_state`` accept either plain data or a
    ``(vault_root, today) -> data`` callable (kept injectable so tests never
    hit disk/network — see module docstring). ``recon_fail`` /
    ``wrong_surface`` come from the driver's T14 commit report on app-days;
    leave them at their 0/False defaults on skill-days and shadow-only app
    runs (protocol § 2.2).

    Returns ``(row, verdict)`` — the appended row dict (which itself carries
    the ``verdict`` column) and the verdict string again for convenience.
    """
    if driver not in _VALID_DRIVERS:
        raise ValueError(f"driver must be one of {_VALID_DRIVERS!r}, got {driver!r}")

    vault_root = Path(vault_root)
    manifest_rows = _resolve(manifest, vault_root, today)
    state = _resolve(live_state, vault_root, today)

    diff = shadow.diff_against_live(manifest_rows, state)
    verdict_obj = classify_bakein(diff)
    verdict_str = day_verdict(verdict_obj, recon_fail=recon_fail, wrong_surface=wrong_surface)

    row = build_log_row(today, driver, verdict_obj, verdict_str, recon_fail)
    append_log_row(log_path if log_path is not None else DEFAULT_LOG_PATH, row)

    return row, verdict_str


# ---------------------------------------------------------------------------
# CLI entry point — mirrors shadow_run.py's style. Not the tested surface
# (run() is); this just wires the real gather/shadow calls for a manual/cron
# invocation once the T19 bake-in clock is running.
# ---------------------------------------------------------------------------

VAULT_ROOT_ENV = "TDTB_VAULT_ROOT"


def _gather_manifest(vault: Path, today: date, sequence_file: str | None) -> list[shadow.ManifestEntry]:
    import main as main_mod  # local import: avoids a hard dependency for tests that only use run()
    import shadow_run

    pool_notes, assigned_notes = shadow_run._gather_pool_and_assigned(vault, today)
    run_data = gather.build_run_data(pool_notes, assigned_notes, today)

    order = list(main_mod.config_reader.FALLBACK_RANKING_CRITERIA["within_tier_sort"])
    config_result = main_mod.config_reader.read_config(vault)
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
        sequence = shadow_run._synthesize_trivial_sequence(digest["assigned"])

    config = config_result.config.sections if config_result.config is not None else {}
    return shadow.build_plan_manifest(digest, sequence, config)


def _ledger_todoist_ids(vault_root: Path, today: date) -> list[str]:
    """Todoist task IDs the day's commit ledger recorded as created / updated /
    noop (ISS-4).

    These are the driver's own writes. Reading them back **by ID** is strongly
    consistent, unlike a filter/search query whose index lags task creation —
    so the bake-in differ must reconcile the Todoist surface against these IDs
    rather than re-discovering tasks by filter, mirroring T14's post-write
    read-back. Reads the **exact-date** run-state note directly (like
    ``orchestrate.py``); never ``gather.load_runstate`` (which reads the
    strictly-prior note for the diff base — the ISS-2 lesson). Returns ``[]``
    when no ledger exists yet (skill-days, pre-commit), so the caller falls
    back to the filter read unchanged.
    """
    path = Path(vault_root) / runstate.runstate_rel_path(today)
    if not path.exists():
        return []
    data = gather._extract_json_block(path.read_text(encoding="utf-8", errors="replace"))
    if not data:
        return []
    todoist = (((data.get("commit_ledger") or {}).get("surfaces") or {}).get("todoist")) or {}
    ids: list[str] = []
    for key in ("created", "updated", "noops"):
        for tid in todoist.get(key) or []:
            if tid and str(tid) not in ids:
                ids.append(str(tid))
    return ids


def gather_live_state_by_id(
    vault_root: Path,
    today: date,
    config: Any,
    fetch_task: Callable[[str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Like ``shadow.gather_live_state`` but reconcile the **Todoist** surface
    by-ID from the day's commit ledger (ISS-4) instead of a lag-prone filter
    read. Vault + calendar surfaces come from ``shadow.gather_live_state``
    unchanged (file reads / EventKit are not subject to the query-index lag).

    ``fetch_task`` is injectable ``(task_id) -> task-dict`` (tests hand a fake;
    ``None`` opens the real ``TodoistClient``). If the ledger names no Todoist
    IDs (skill-day / pre-commit), the base filter read is kept as-is. If the
    by-ID fetch fails wholesale (auth/network), the surface is marked
    ``todoist_unavailable`` so the differ reports UNAVAILABLE — never a false
    ``would-create``. A single missing ID (404 — task deleted post-commit) is
    skipped, not fatal.
    """
    vault_root = Path(vault_root)
    state = shadow.gather_live_state(config, vault_root)

    ids = _ledger_todoist_ids(vault_root, today)
    if not ids:
        return state  # skill-day / no ledger -> keep the filter read

    if fetch_task is None:
        try:
            token = todoist_client.load_token(shadow.TOKEN_ENV_PATH)
            client = todoist_client.TodoistClient(token)
        except Exception as exc:  # noqa: BLE001 — degrade the surface, never raise
            state["todoist_unavailable"] = True
            state["todoist_error"] = f"by-id reconcile: {exc}"
            return state
        try:
            tasks = _fetch_tasks_by_id(ids, client.get_task)
        except Exception as exc:  # noqa: BLE001 — a fetch failure degrades, never raises
            state["todoist_unavailable"] = True
            state["todoist_error"] = f"by-id reconcile: {exc}"
            return state
        finally:
            client.close()
    else:
        tasks = _fetch_tasks_by_id(ids, fetch_task)

    state["todoist_tasks"] = tasks
    state.pop("todoist_unavailable", None)
    state.pop("todoist_error", None)
    return state


def _fetch_tasks_by_id(
    ids: list[str], fetch_task: Callable[[str], dict[str, Any]]
) -> list[dict[str, Any]]:
    """Fetch each ledger task by ID (strongly consistent). A 404 (task
    hard-deleted post-commit) is skipped as genuinely absent; any other error
    propagates for the caller to degrade on.

    No due-shape normalization is applied: as of the ISS-5 ``shadow.py``
    unfreeze, ``shadow._todoist_due_time`` reads the timed v1 ``due.date``
    natively, so the differ reconciles the v1 ``/tasks/{id}`` shape without a
    bridge (was ``_normalize_task_due``, removed as redundant 2026-07-13).
    ``is_deleted`` is intentionally NOT gated on — the v1 endpoint reports it
    unreliably, so gating would manufacture false ``would-create`` DIFFs;
    presence + due time is the reconciliation key."""
    tasks: list[dict[str, Any]] = []
    for tid in ids:
        try:
            task = fetch_task(tid)
        except todoist_client.TodoistError as exc:
            if exc.status_code == 404:
                continue  # hard-deleted after commit — genuinely absent
            raise
        if task:
            tasks.append(task)
    return tasks


def _gather_live_state(vault: Path, today: date) -> dict[str, Any]:
    import main as main_mod

    config_result = main_mod.config_reader.read_config(vault)
    config = config_result.config.sections if config_result.config is not None else {}
    return gather_live_state_by_id(vault, today, config)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--driver", choices=_VALID_DRIVERS, required=True,
                         help="Who committed today's plan: 'skill' or 'app'")
    parser.add_argument("--sequence-file", default=None,
                         help="Path to a /sequence-shaped JSON file (else a trivial sequence is synthesized)")
    parser.add_argument("--recon-fail", type=int, default=0,
                         help="Reconciliation-failure count from the driver's T14 commit report")
    parser.add_argument("--wrong-surface", action="store_true",
                         help="Set if the driver's commit report tripped the calendar-ID assertion")
    parser.add_argument("--log-path", default=None, help="Override the bake-in-log.md path")
    args = parser.parse_args()

    root = os.environ.get(VAULT_ROOT_ENV)
    if not root:
        print(f"ERROR: {VAULT_ROOT_ENV} not set", file=sys.stderr)
        return 1
    vault = Path(root).expanduser()
    if not vault.is_dir():
        print(f"ERROR: vault root not found: {vault}", file=sys.stderr)
        return 1

    today = gather.effective_date(datetime.now())

    try:
        manifest = _gather_manifest(vault, today, args.sequence_file)
        live_state = _gather_live_state(vault, today)
    except shadow.ShadowStateError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    row, verdict = run(
        vault, today, args.driver, manifest, live_state,
        recon_fail=args.recon_fail, wrong_surface=args.wrong_surface,
        log_path=args.log_path,
    )
    print(json.dumps(row, indent=2))
    print(f"verdict: {verdict}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
