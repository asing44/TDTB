"""orchestrate.py — T15: failure-safe commit orchestrator.

``commit.py`` (T14) gives us five idempotent, self-reconciling writers and a
thin ``run_commit`` driver that runs them all in a fixed order with no ledger,
no resume, and no partial-failure honesty — that driver exists only for the
manual write-verify gate. This module is the real orchestration layer the
live ``/commit?mode=live`` route (T15) calls:

  - **Fixed surface order + stable ledger keys** (``SURFACES``) — every run
    dispatches todoist -> vault_flips -> daily_note -> calendar, in that
    order, and records a per-surface entry under that same key every time.
  - **A failing surface never aborts the run.** Writers are already
    idempotent and self-reconciling (commit.py's guarantee); a writer
    returning ``ok=False`` only marks its own ledger entry failed — later
    surfaces still run. Nothing here re-raises a writer's failure.
  - **Per-surface crash-consistency.** The ledger is persisted to the vault's
    ``tdtb-runstate-<today>.md`` note immediately after each surface is
    processed, not just at the end — a crash mid-run still leaves the ok
    surfaces recorded on disk.
  - **Resume skips known-ok surfaces.** With ``resume=True``, a surface whose
    *prior* ledger entry (loaded from the vault) already reads ``ok`` is
    carried forward verbatim and its writer is never re-invoked. The writers
    are idempotent so re-running them would also be safe — resuming instead
    of re-running is a deliberate proof of that safety, and it avoids
    redundant live API calls (Todoist, EventKit) on a retry.
  - **``recent-selections`` stays a post-commit action** (per commit.py's
    docstring) — appended only when the *whole* run lands ok.

Base-state loading note: ``gather.load_runstate(vault_root, valid_date)``
finds the most recent runstate *strictly before* ``valid_date`` (its
contract, built for the precompute's diff-base lookup) — it can never see a
same-day file. Resume fundamentally needs exactly that same-day file (a prior
*partial* run's own ledger), so ``_prior_state`` below reads today's own note
directly first and only falls back to ``gather.load_runstate`` when no
same-day note exists yet (the ordinary case: nothing has written today's note
before this run).
"""
from __future__ import annotations

import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

_GATHER_DIR = str(Path(__file__).parent / "gather")
if _GATHER_DIR not in sys.path:
    sys.path.insert(0, _GATHER_DIR)

import tdtb_gather as gather  # noqa: E402  (path-shimmed import, see inventory.py)

import commit  # noqa: E402
import runstate  # noqa: E402

# Fixed dispatch order + the ledger's stable key set. Order matters for
# crash-consistency reasoning (todoist lands before anything vault-side, so a
# crash after Step A never leaves an orphaned vault flip with no matching
# task) and is never reordered by intent content.
SURFACES: list[str] = ["todoist", "vault_flips", "daily_note", "captures", "calendar"]

# surface key -> (WriterResult.step label, the client this surface needs)
_STEP_FOR: dict[str, str] = {
    "todoist": "A",
    "vault_flips": "C",
    "daily_note": "B",
    "captures": "B6",
    "calendar": "D/E",
}
_CLIENT_NAME_FOR: dict[str, str] = {
    "todoist": "todoist",
    "vault_flips": "vault_root",
    "daily_note": "vault_root",
    "captures": "vault_root",
    "calendar": "store",
}


def _subset_for(key: str, intents: list[commit.WriteIntent]) -> list[commit.WriteIntent]:
    """The intent subset each surface owns — mirrors commit.py's own writer
    filters (todoist/vault-step-C/vault-step-B/calendar) so a surface never
    sees an intent another surface is responsible for."""
    if key == "todoist":
        return [i for i in intents if i.surface == "todoist"]
    if key == "vault_flips":
        return [i for i in intents if i.surface == "vault" and i.step == "C"]
    if key == "daily_note":
        return [i for i in intents if i.surface == "vault" and i.step == "B"]
    if key == "captures":
        return [i for i in intents if i.surface == "vault" and i.step == "B6"]
    if key == "calendar":
        return [i for i in intents if i.surface == "calendar"]
    raise ValueError(f"unknown surface key {key!r}")  # pragma: no cover — SURFACES is closed


def _dispatch(
    key: str,
    subset: list[commit.WriteIntent],
    *,
    todoist: commit.TodoistLike | None,
    store: commit.EventStoreLike | None,
    vault_root: Path | None,
    plan_body: str,
    today: date,
) -> commit.WriterResult:
    if key == "todoist":
        return commit.write_todoist(subset, todoist)
    if key == "vault_flips":
        return commit.write_frontmatter_flips(subset, vault_root)
    if key == "daily_note":
        return commit.write_daily_note(subset, vault_root, plan_body, today)
    if key == "captures":
        return commit.write_captures_frontmatter(subset, vault_root, today)
    if key == "calendar":
        return commit.write_calendar(subset, store, today)
    raise ValueError(f"unknown surface key {key!r}")  # pragma: no cover — SURFACES is closed


def _result_entry(result: commit.WriterResult) -> dict[str, Any]:
    return {
        "status": "ok" if result.ok else "failed",
        "step": result.step,
        "verify_failures": list(result.verify_failures),
        "verify_details": [dict(d) for d in result.verify_details],
        "created": list(result.created),
        "updated": list(result.updated),
        "noops": list(result.noops),
        "reconciliation": dict(result.reconciliation),
        "touched": dict(result.touched),
        "error": result.error,
        "note": None,
    }


def _empty_entry(key: str, *, note: str, status: str, error: str | None = None) -> dict[str, Any]:
    return {
        "status": status,
        "step": _STEP_FOR[key],
        "created": [],
        "updated": [],
        "noops": [],
        "reconciliation": {},
        "error": error,
        "note": note,
    }


def _prior_state(vault_root: Path, today: date) -> dict[str, Any] | None:
    """State to carry forward into this run.

    Prefers today's own runstate note (an earlier partial run this same day —
    exactly what resume needs to see) over ``gather.load_runstate``'s
    strictly-earlier-day lookup, which cannot return a same-day note by
    contract. Falls back to that earlier-day lookup only when today's own
    note doesn't exist yet (still preserves whatever ``/gather`` or a prior
    day left behind, per commit_ledger's "preserves other runstate keys"
    guarantee)."""
    own_path = vault_root / runstate.runstate_rel_path(today)
    if own_path.is_file():
        data = gather._extract_json_block(own_path.read_text(encoding="utf-8", errors="replace"))
        if data is not None:
            return data
    _, state = gather.load_runstate(vault_root, today)
    return state


def run_orchestrated(
    intents: list[commit.WriteIntent],
    *,
    todoist: commit.TodoistLike | None = None,
    store: commit.EventStoreLike | None = None,
    vault_root: Path | str | None = None,
    plan_body: str = "",
    today: date | None = None,
    resume: bool = False,
    selections: list[dict[str, Any]] | None = None,
    persist_ledger: bool = True,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Dispatch every surface's writer over its ``intents`` subset, ledgering
    each outcome as it lands. See module docstring for the failure-safety and
    resume guarantees.

    Returns a JSON-safe report:
    ``{"ok", "resumed", "today", "surfaces", "landed", "failed"}`` — ``ok`` is
    true only when every surface's ledger entry is ``"ok"`` (an empty/no-op
    surface counts as ok; a missing required client or a writer failure does
    not).
    """
    today = today or date.today()
    now = now or datetime.now(timezone.utc)
    vault_path = Path(vault_root) if vault_root is not None else None

    prior: dict[str, Any] | None = _prior_state(vault_path, today) if vault_path is not None else None
    if persist_ledger and vault_path is not None and prior:
        # Carry a prior-day state forward into today's note ONCE, under the
        # day lock — only when today's own note doesn't exist yet. Later
        # persists are locked commit_ledger-only merges (G26), so they can't
        # do this carry-forward themselves.
        with runstate.day_lock(today):
            if runstate.read_runstate(vault_path, today) is None:
                runstate.write_runstate(
                    vault_path, today, runstate.build_runstate(prior), now=now)
    prior_ledger = (prior or {}).get("commit_ledger") or {}
    prior_surfaces = prior_ledger.get("surfaces") or {}

    ledger: dict[str, Any] = {
        "updated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "today": str(today),
        # G18a instrumentation: the day's planned sizes, joinable against
        # completion state later to compute a personal estimation-correction
        # factor (planning fallacy). Planned side only — actuals are a
        # follow-up gather concern.
        "estimation": [
            {
                "name": i.name, "surface": i.surface, "op": i.op,
                "planned_duration_min": i.duration_min,
                "planned_blocks": (i.duration_min or 0) / 30,
                "time": i.due_time or (i.start.strftime("%H:%M") if i.start else None),
            }
            for i in intents
            if i.op in ("create", "update") and (i.duration_min or 0) > 0
        ],
        "surfaces": {},
    }
    surfaces = ledger["surfaces"]

    client_values: dict[str, Any] = {
        "todoist": todoist,
        "vault_root": vault_path,
        "store": store,
    }

    for key in SURFACES:
        subset = _subset_for(key, intents)
        prior_entry = prior_surfaces.get(key) if isinstance(prior_surfaces, dict) else None

        if resume and isinstance(prior_entry, dict) and prior_entry.get("status") == "ok":
            entry = dict(prior_entry)
            entry["note"] = "resumed: already ok"
        elif not subset:
            entry = _empty_entry(key, note="no intents", status="ok")
        elif client_values[_CLIENT_NAME_FOR[key]] is None:
            entry = _empty_entry(
                key, note=None, status="failed",
                error=f"{key}: {_CLIENT_NAME_FOR[key]} unavailable",
            )
        else:
            result = _dispatch(
                key, subset, todoist=todoist, store=store, vault_root=vault_path,
                plan_body=plan_body, today=today,
            )
            entry = _result_entry(result)

        surfaces[key] = entry

        if persist_ledger and vault_path is not None:
            # G26: locked RMW touching ONLY commit_ledger — persisting the
            # run-entry base_state clobbered concurrent /day-setup writes.
            runstate.update_runstate(
                vault_path, today, {"commit_ledger": ledger}, now=now)

    overall_ok = all(e["status"] != "failed" for e in surfaces.values())

    if overall_ok and selections and vault_path is not None:
        runstate.append_recent_selection(vault_path, today, selections)

    if persist_ledger and vault_path is not None:
        runstate.update_runstate(vault_path, today, {"commit_ledger": ledger}, now=now)

    landed: list[str] = []
    failed: list[str] = []
    for key, entry in surfaces.items():
        if entry["status"] == "ok":
            activity = len(entry["created"]) + len(entry["updated"]) + len(entry["noops"])
            if activity > 0:
                landed.append(
                    f"{key}:{entry['step']} created={len(entry['created'])} "
                    f"updated={len(entry['updated'])} noops={len(entry['noops'])}"
                )
        else:
            failed.append(f"{key}: {entry['error']}")

    # D1 (ui-parity T9): per-write verification evidence, aggregated. ANY
    # entry here blocks a bake-in PASS (bake-in-protocol gating rule).
    verify_failures: list[str] = []
    verify_details: list[dict[str, Any]] = []
    for entry in surfaces.values():
        verify_failures.extend(entry.get("verify_failures") or [])
        verify_details.extend(entry.get("verify_details") or [])

    return {
        "ok": overall_ok,
        "resumed": resume,
        "today": str(today),
        "surfaces": surfaces,
        "landed": landed,
        "failed": failed,
        "verify_failures": verify_failures,
        "verify_details": verify_details,
    }
