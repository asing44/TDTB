#!/usr/bin/env python3
"""commit_run.py — T14 manual write-verify gate harness.

Runs the real commit writers (``commit.py``) against the live vault, Todoist,
and calendar from the CLI — the manual portion of the T14 gate that can't run
under pytest (calendar TCC binds to the launching process; run this from your
Terminal so the grant is Terminal's, spec § 3.3).

    .venv/bin/python commit_run.py                  # PLAN-ONLY preview (no writes)
    .venv/bin/python commit_run.py --sequence-file s.json
    .venv/bin/python commit_run.py --commit         # LIVE — actually writes

**Default is plan-only.** It builds the shadow diff, plans the concrete write
intents (``commit.plan_writes``), and prints them — writing NOTHING. Pass
``--commit`` to execute the writers and print the per-surface reconciliation
verdict (``WriterResult.ok`` + counts + any surfaced mismatch). This is the
first time real writes leave the app; run the plan-only preview first, eyeball
it, then re-run with ``--commit``.

Vault root: ``$TDTB_VAULT_ROOT`` (same env var main.py reads, never hardcoded).
Reuses shadow_run's gather→digest→sequence→manifest→diff pipeline verbatim, then
diverges at ``plan_writes`` / ``run_commit``.

Exit codes: 0 = plan-only preview OK, or a --commit run where every writer
reconciled (all ok); 1 = setup error (no vault / no token) OR a --commit run
where any writer returned ok=False (a surfaced reconciliation failure — the
kill-switch signal, spec T18).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent / "gather"))

import tdtb_gather as gather  # noqa: E402
import main as main_mod  # noqa: E402
import shadow  # noqa: E402
import commit  # noqa: E402
import calendar_bridge  # noqa: E402
import shadow_run  # noqa: E402  (reuse its gather/digest/sequence helpers)

VAULT_ROOT_ENV = "TDTB_VAULT_ROOT"


def _resolve_calendar_ids(config: dict, store: "calendar_bridge.EventStore | None") -> dict[str, str]:
    """display title -> live identifier, from the config's ``## Calendar Titles``
    map resolved against the live calendar set. Empty (with a warning) if
    the calendar surface is unavailable — plan_writes will then refuse any
    calendar row, which is the correct fail-closed behavior.

    T15 extraction: the logical->title normalization and the title-keyed
    resolution both moved into ``calendar_bridge`` (``normalize_title_map`` /
    ``resolve_titles_to_ids``) so this seam is unit-testable without a live
    EventStore. This function is now a thin delegator — net behavior
    (store-None guard, stderr warnings on unresolved names, exception guard
    returning ``{}``) is unchanged.
    """
    raw = config.get("Calendar Titles") or config.get("calendar_ids") or config.get("Calendar IDs")
    title_map = calendar_bridge.normalize_title_map(raw)
    if not title_map or store is None:
        return {}
    try:
        resolved, failures = calendar_bridge.resolve_titles_to_ids(title_map, store.calendars())
        if failures:
            print(f"⚠ unresolved calendar logical names: {failures}", file=sys.stderr)
        return resolved
    except Exception as exc:  # noqa: BLE001
        print(f"⚠ calendar resolution failed: {exc}", file=sys.stderr)
        return {}


def _render_plan_body(sequence: dict) -> str:
    """Trivial ``# TDTB Plan`` body from the sequence rows — a representative
    render for the write-verify gate (full rendering is a later concern)."""
    lines = []
    for row in sequence.get("sequence", []):
        lines.append(f"- {row.get('start', '??')}–{row.get('end', '??')} {row.get('id', '')}")
    return "\n".join(lines) or "- (no sequenced items)"


def _print_intents(intents: list["commit.WriteIntent"]) -> None:
    print("\n## Planned write intents (PLAN-ONLY — nothing written)\n")
    header = f"{'Step':<4} {'Surface':<9} {'Op':<7} {'Name':<32} {'Time':<6} Target"
    print(header)
    print("-" * len(header))
    for i in intents:
        target = i.task_id or i.calendar_id or i.path or (i.project_id or "—")
        print(f"{i.step:<4} {i.surface:<9} {i.op:<7} {(i.name or '')[:32]:<32} {i.due_time or '—':<6} {target}")
    print()


def _print_results(results: list["commit.WriterResult"]) -> None:
    print("\n## Commit results (LIVE)\n")
    for r in results:
        flag = "✅" if r.ok else "❌"
        print(f"{flag} Step {r.step} [{r.surface}]  "
              f"created={len(r.created)} updated={len(r.updated)} noop={len(r.noops)}  "
              f"reconciliation={r.reconciliation}")
        if r.error:
            print(f"     ⚠ {r.error}")
    print()


def run(vault: Path, sequence_file: str | None, do_commit: bool) -> int:
    today = gather.effective_date(datetime.now())

    # -- gather -> digest -> sequence -> manifest (shadow_run pipeline, reused) --
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
        print("(no --sequence-file given — synthesizing a trivial back-to-back sequence)")

    config = config_result.config.sections if config_result.config is not None else {}
    manifest = shadow.build_plan_manifest(digest, sequence, config)

    try:
        live_state = shadow.gather_live_state(config, vault)
    except shadow.ShadowStateError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    diff = shadow.diff_against_live(manifest, live_state)

    # -- calendar resolution (needed to plan calendar rows) --------------------
    store = None
    if not live_state.get("calendar_unavailable"):
        try:
            store = calendar_bridge.shared_store()
        except Exception as exc:  # noqa: BLE001
            print(f"⚠ EventStore init failed ({exc}); calendar rows will refuse to plan", file=sys.stderr)
    resolved_calendar_ids = _resolve_calendar_ids(config, store)

    # -- plan the concrete writes ---------------------------------------------
    try:
        intents = commit.plan_writes(diff, resolved_calendar_ids, config_result.config or config, today)
    except commit.CommitPlanError as exc:
        print(f"PLAN REFUSED: {exc}", file=sys.stderr)
        return 1

    if not do_commit:
        _print_intents(intents)
        print("Plan-only preview complete — re-run with --commit to write.")
        return 0

    # -- LIVE commit ----------------------------------------------------------
    token = shadow.todoist_client.load_token(shadow.TOKEN_ENV_PATH)
    plan_body = _render_plan_body(sequence)
    with shadow.todoist_client.TodoistClient(token) as todoist:
        results = commit.run_commit(
            intents, todoist=todoist, store=store, vault_root=vault,
            plan_body=plan_body, today=today,
        )
    _print_results(results)
    all_ok = all(r.ok for r in results)
    if all_ok:
        print("All writers reconciled ✅")
    else:
        print("One or more writers FAILED reconciliation ❌ — investigate before re-running (kill-switch, spec T18).")
    return 0 if all_ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sequence-file", default=None, help="Path to a /sequence-shaped JSON file")
    parser.add_argument("--commit", action="store_true",
                        help="Actually write (default is a plan-only preview)")
    args = parser.parse_args()

    root = os.environ.get(VAULT_ROOT_ENV)
    if not root:
        print(f"ERROR: {VAULT_ROOT_ENV} not set", file=sys.stderr)
        return 1
    vault = Path(root).expanduser()
    if not vault.is_dir():
        print(f"ERROR: vault root not found: {vault}", file=sys.stderr)
        return 1

    if args.commit:
        print("⚠ --commit: this WILL write to the live vault, Todoist, and calendar.\n")
    return run(vault, args.sequence_file, args.commit)


if __name__ == "__main__":
    raise SystemExit(main())
