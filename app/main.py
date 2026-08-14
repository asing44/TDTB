"""TDTB app pilot — FastAPI entry point.

Localhost-only by contract (spec § 1): run via
  .venv/bin/python main.py            # binds 127.0.0.1 only
  (or .venv/bin/uvicorn main:app --host 127.0.0.1 --port 8746)

T9 wiring: routes /gather /digest /adjust /sequence /commit /config (+ the
T1 /health stub, preserved). Security per the council mandate: a per-session
random token is generated at startup and EVERY mutating route (any POST)
requires it via the ``X-TDTB-Token`` header — missing/wrong token → 403.
GET /config and GET /health are tokenless reads.

T11 wiring: /adjust and /sequence now call the judgment layer (judgment.py)
for real — free-text adjustment translation and sequencing proposals,
respectively. Both return a 502-style JSON error body on ``JudgmentError``
(SDK/schema failure) rather than a raw 500, so the client can distinguish
"the model failed" from a server bug. /commit is still a wired stub (real
route shape + real token guard) returning 501 until the commit writers
(T14/T15) land.

``vault_root`` is resolved at request time — per-app override (tests /
create_app arg) first, else the ``TDTB_VAULT_ROOT`` env var. Never hardcoded
(spec locked decision 2).
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import secrets
import re
import sys
import threading
from datetime import date, datetime
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, StrictInt, field_validator

_STATIC_DIR = Path(__file__).parent / "static"

_GATHER_DIR = str(Path(__file__).parent / "gather")
if _GATHER_DIR not in sys.path:
    sys.path.insert(0, _GATHER_DIR)

import tdtb_gather as gather  # noqa: E402  (path-shimmed import, see inventory.py)

import config_reader  # noqa: E402
import day_semantics  # noqa: E402
import deferrals  # noqa: E402
import runstate  # noqa: E402
import judgment  # noqa: E402
import sequence  # noqa: E402
import shadow  # noqa: E402
import commit  # noqa: E402
import calendar_bridge  # noqa: E402
import runtime_actions  # noqa: E402
import external_sources  # noqa: E402
import micro_adventure  # noqa: E402
import orchestrate  # noqa: E402
import time_engine  # noqa: E402
import capacity as capacity_mod  # noqa: E402

VAULT_ROOT_ENV = "TDTB_VAULT_ROOT"

# Distant-future sentinel for deadline sorting: items without a deadline sort
# after every dated item, deterministically.
_NO_DEADLINE = date.max


# ---------------------------------------------------------------------------
# Deterministic digest ranking
# ---------------------------------------------------------------------------

def _rank_key(item: dict[str, Any], today: date, order: list[str],
              bias: dict[str, int] | None = None):
    """Stable sort key for one pool item per the config ``within_tier_sort``
    order (default: urgency, overdue, deadline, staleness, summit).

    Every criterion maps to a deterministic scalar; the final (name, path)
    tie-break guarantees identical output for identical input — no wall-clock
    or insertion-order dependence.

    ``bias`` is T1's defer-with-memory map (``deferrals.bias_map``): a bounded
    0..MAX_BIAS nudge applied twice — folded into the urgency criterion (so a
    deferred item climbs at most MAX_BIAS tiers) AND as the last tie-break
    before (name, path), so the locked effect "deferred yesterday ⇒ ranks
    higher today" holds even when ``urgency`` isn't in the configured order.
    """
    try:
        deadline = date.fromisoformat(item["deadline"]) if item.get("deadline") else None
    except (TypeError, ValueError):
        deadline = None
    try:
        urgency = int(item.get("urgency") or 0)
    except (TypeError, ValueError):
        urgency = 0

    nudge = 0
    if bias:
        try:
            nudge = int(bias.get(deferrals.key_for_item(item)) or 0)
        except ValueError:  # blank identity — unbiasable, never fatal
            nudge = 0

    parts: list[Any] = []
    for criterion in order:
        if criterion == "urgency":
            parts.append(-(urgency + nudge))  # vault urgency: 4 = highest
        elif criterion == "overdue":
            parts.append(0 if (deadline and deadline < today) else 1)
        elif criterion == "deadline":
            parts.append(deadline or _NO_DEADLINE)
        elif criterion == "staleness":
            # Staleness (interval last_completed) isn't in gather's summary
            # shape; substitute priority_score DESC — gather's own composite,
            # already deterministic — so the slot still discriminates.
            parts.append(-(item.get("priority_score") or 0))
        elif criterion == "summit":
            parts.append(0 if "summit" in (item.get("types") or []) else 1)
        # Unknown criteria are skipped, never raise — config is user-edited.
    parts.append(-nudge)
    parts.append(item.get("name") or "")
    parts.append(item.get("path") or "")
    return tuple(parts)


def _render_plan_body(sequence_body: dict[str, Any]) -> str:
    """Trivial ``# TDTB Plan`` body from the sequence rows — mirrors
    ``commit_run.py``'s ``_render_plan_body`` exactly (duplicated rather than
    imported: commit_run.py imports main.py, so importing back would be
    circular)."""
    lines = []
    for row in sequence_body.get("sequence", []):
        lines.append(f"- {row.get('start', '??')}–{row.get('end', '??')} {row.get('id', '')}")
    return "\n".join(lines) or "- (no sequenced items)"


def rank_pool(
    pool_items: list[dict[str, Any]],
    today: date,
    order: list[str],
    bias: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    """Deterministically rank pool items (gather run-data summary shape).

    ``bias`` — T1 defer-with-memory map from ``deferrals.bias_map``; absent or
    empty leaves ranking byte-identical to the pre-T1 behaviour.
    """
    return sorted(pool_items, key=lambda i: _rank_key(i, today, order, bias))


def build_digest(
    pool_items: list[dict[str, Any]],
    assigned_items: list[dict[str, Any]],
    today: date,
    order: list[str],
    ignore: dict[str, set[str]] | None = None,
    bias: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Two-surface digest per SKILL.md Phase 2/3: Assigned + ranked Suggested.

    ``ignore`` (config `## Ignore List`, T13e) drops matching items from
    every surface: Todoist rows by ``todoist_id``, vault rows by relative
    ``path``, any row by case-insensitive name. The user-editable permanent
    hide list; counts reflect the post-filter sets.

    Assigned rows sort alphabetically (stable identity list, not a ranking);
    Suggested rows are the pool minus already-assigned paths, ranked per
    ``within_tier_sort``.
    """
    if ignore and any(ignore.values()):
        def _kept(i: dict[str, Any]) -> bool:
            if str(i.get("todoist_id") or "") in ignore["todoist_ids"]:
                return False
            if str(i.get("path") or "") in ignore["paths"]:
                return False
            return str(i.get("name") or "").casefold() not in ignore["names"]

        assigned_items = [i for i in assigned_items if _kept(i)]
        pool_items = [i for i in pool_items if _kept(i)]
    assigned = sorted(assigned_items, key=lambda i: (i.get("name") or "", i.get("path") or ""))
    assigned_paths = {i.get("path") for i in assigned}
    suggestable = [i for i in pool_items if i.get("path") not in assigned_paths]
    suggested = rank_pool(suggestable, today, order, bias)
    unassigned_candidates, stale_assigned = build_forgot_lists(
        assigned, suggested, today, bias)
    return {
        "valid_date": str(today),
        "ranking_order": order,
        "assigned_count": len(assigned),
        "pool_count": len(pool_items),
        "assigned": assigned,
        "suggested": suggested,
        # T6 forgot-strip inputs — derived here, deterministically, so the
        # strip renders at load without the billed audit pipeline.
        "unassigned_candidates": unassigned_candidates,
        "stale_assigned": stale_assigned,
    }


FORGOT_LIST_CAP = 5


def _forgot_reason(item: dict[str, Any], today: date, nudge: int,
                   assigned: bool) -> str | None:
    """The single strongest "you may have forgotten this" signal, or None.

    Deterministic and gather-local by construction — locked decision 8 wants
    this at LOAD, and locked decision 4 forbids a new billed call, so the
    signals are exactly the ones already on a digest row (deadline, urgency)
    plus T1's deferral memory. No model, no wider net.
    """
    try:
        deadline = date.fromisoformat(item["deadline"]) if item.get("deadline") else None
    except (TypeError, ValueError):
        deadline = None
    try:
        urgency = int(item.get("urgency") or 0)
    except (TypeError, ValueError):
        urgency = 0

    prefix = "assigned but " if assigned else ""
    if deadline and deadline < today:
        return f"{prefix}deadline {deadline} has passed"
    if assigned:
        # A still-assigned row is only "stale" on evidence it isn't moving:
        # a passed deadline above, or a deferral it survived. Being merely
        # assigned and undated is the normal case, not a finding.
        return "assigned but deferred recently" if nudge else None
    if deadline == today:
        return "due today and unassigned"
    if nudge:
        return "deferred recently and still not scheduled"
    if urgency >= 4:
        return "4-crit and unassigned"
    return None


def build_forgot_lists(
    assigned: list[dict[str, Any]],
    suggested: list[dict[str, Any]],
    today: date,
    bias: dict[str, int] | None = None,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """``(unassigned_candidates, stale_assigned)`` for the T9 forgot-strip.

    Shape is deliberately the AuditReport's ``{name, path, reason}`` with the
    same 5-entry cap: if the billed audit pipeline ever runs alongside this,
    the two surfaces stay interchangeable rather than competing.

    ``suggested`` arrives already ranked, so candidate order is the digest's
    own ranking — including T1's deferral bias — not a second opinion.
    """
    def _rows(items: list[dict[str, Any]], is_assigned: bool) -> list[dict[str, str]]:
        out: list[dict[str, str]] = []
        for item in items:
            try:
                nudge = int((bias or {}).get(deferrals.key_for_item(item)) or 0)
            except ValueError:
                nudge = 0
            reason = _forgot_reason(item, today, nudge, is_assigned)
            if reason is None:
                continue
            out.append({
                "name": str(item.get("name") or ""),
                "path": str(item.get("path") or ""),
                "reason": reason,
            })
            if len(out) >= FORGOT_LIST_CAP:
                break
        return out

    return _rows(suggested, False), _rows(assigned, True)


def build_digest_index(digest: dict[str, Any]) -> list[dict[str, str]]:
    """Identity index of a built digest — ``[{name, todoist_id, path}]``.

    Persisted to runstate ``digest_index`` by /plan-inputs so T2's
    staging-phase ``resolve_target`` can map a name the client sends back to
    the source artifacts the SERVER derived. The client never names an id, so
    the T20 property "the app only touches artifacts it derived itself"
    survives the move to pre-commit; only the derivation source changes
    (plan_manifest → digest). Rows with no usable identity are dropped.
    """
    index: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for row in list(digest.get("assigned") or []) + list(digest.get("suggested") or []):
        if not isinstance(row, dict):
            continue
        entry = {
            "name": str(row.get("name") or ""),
            "todoist_id": str(row.get("todoist_id") or ""),
            "path": str(row.get("path") or ""),
        }
        if not entry["name"] or not (entry["todoist_id"] or entry["path"]):
            continue
        key = (entry["name"], entry["todoist_id"], entry["path"])
        if key in seen:
            continue
        seen.add(key)
        index.append(entry)
    return index


# ---------------------------------------------------------------------------
# App factory + security
# ---------------------------------------------------------------------------

class DigestRequest(BaseModel):
    """Optional /digest body: pre-gathered run-data. Absent → gather live."""

    pool_items: list[dict[str, Any]] | None = None
    assigned_items: list[dict[str, Any]] | None = None
    today: str | None = None


class AdjustRequest(BaseModel):
    """/adjust body: a free-text instruction + the digest it applies against."""

    instruction: str
    digest: dict[str, Any]


class SequenceRequest(BaseModel):
    """/sequence body: assigned items + config + anchored blocks to place."""

    assigned: list[dict[str, Any]]
    config: dict[str, Any]
    anchored_blocks: list[dict[str, Any]]
    day_semantics: dict[str, Any] = Field(default_factory=dict)
    planning_config_fingerprint: str = ""
    pinned_rows: list[dict[str, Any]] = Field(default_factory=list)


class ValidateSequenceRequest(BaseModel):
    """/validate-sequence body (T16): a proposal's sequence rows + the same
    assigned/anchored/config inputs, re-checked against the FROZEN
    sequence.validate_sequence. Deterministic — no Agent SDK call, no writes.
    The timeline view POSTs this on drag-end to refresh {ok, hard_errors,
    warnings} without re-proposing via /sequence.

    ``sequence`` is the row list ([{id,start,end,zone}]); the route wraps it as
    ``{"sequence": [...]}`` for the validator, matching a SequenceProposal.
    Note: assigned items must carry ``id`` (= the digest item's name) so the
    validator's by-id matching lines up with the row ids — the view layer
    normalizes id=name before POSTing (T1 contract)."""

    sequence: list[dict[str, Any]]
    assigned: list[dict[str, Any]]
    anchored_blocks: list[dict[str, Any]]
    config: dict[str, Any]
    overlap_grants: list[dict[str, Any]] = Field(default_factory=list)
    planning_config_fingerprint: str = ""
    pinned_rows: list[dict[str, Any]] = Field(default_factory=list)


class CommitRequest(BaseModel):
    """/commit?mode=shadow body: the confirmed digest + sequence to preview.

    Optional so a bare ``POST /commit`` (no query param, no body) keeps
    returning the legacy 501 stub untouched — see the mode dispatch below."""

    digest: dict[str, Any] | None = None
    sequence: dict[str, Any] | None = None
    config: dict[str, Any] | None = None
    overlap_grants: list[dict[str, Any]] = Field(default_factory=list)
    pinned_rows: list[dict[str, Any]] = Field(default_factory=list)
    planning_config_fingerprint: str = ""


class RuntimeActionRequest(BaseModel):
    """/runtime-actions body (T20): one verb against one committed plan item.

    ``target`` is the plan-item NAME — resolution to Todoist ids / owned
    event ids / vault paths happens server-side from today's runstate
    ``plan_manifest``, so the client can never address an artifact the app
    didn't commit."""

    verb: str
    target: str
    args: dict[str, Any] = Field(default_factory=dict)


class DaySetupRequest(BaseModel):
    """/day-setup body: the Phase-1 confirm payload. Session/day-scoped —
    persists to the dated run-state note, NEVER to vault config (locked
    decision 2; skill 811 skip_today is session-only).

    T18b.2 tri-state semantics for ``day_preset`` and
    ``work_allotment_minutes``: omitted preserves the dated override (the
    request body lacks the field); explicit ``null`` removes the override and
    restores config resolution; ``0`` work_allotment_minutes persists as the
    explicit Mint disable. Field presence is detected via
    ``model_fields_set`` — a default value does NOT count as present."""

    anchor: str | None = None            # HH:MM override (Start Time edit)
    eod: str | None = None               # HH:MM override
    buffering: str | None = None         # standard | minimal | off
    schedulable: dict[str, Any] | None = None   # {minting:{on,n}, qt:{...}, shivery:{...}}
    anchored: list[dict[str, Any]] | None = None  # [{id, on, skip_today, time}]
    captures: dict[str, Any] | None = None  # {intention, megan_nicety, stoic_intention}
    day_preset: str | None = None        # dated preset override (T18b.2)
    work_allotment_minutes: StrictInt | None = None  # dated Mint allotment (T18b.2)
    micro_adventure: dict[str, Any] | None = None  # T19 dated Live override; null clears to auto

    @field_validator("work_allotment_minutes")
    @classmethod
    def validate_work_allotment_minutes(cls, value: int | None) -> int | None:
        if value is not None and (value < 0 or value % 15 != 0):
            raise ValueError(
                "work_allotment_minutes must be a nonnegative integer "
                "divisible by 15"
            )
        return value


# Day Setup keys /plan-inputs echoes back from run state (the UI's read side).
# G24: per-day billed-SDK-call cap, enforced against the persistent runstate
# ledger (billed_calls) — the same 4-call bound RunContext asserts per run.
BILLED_CAP = judgment.MAX_CALLS_PER_RUN

_DAY_SETUP_KEYS = ("anchor", "eod", "buffering", "schedulable", "anchored",
                   "re_included", "intention", "megan_nicety", "stoic_intention",
                   "day_preset", "work_allotment_minutes")


def _read_today_runstate(vault: Path, today: date) -> dict[str, Any]:
    """Today's exact-date run-state note as a dict; missing/unparseable → {}."""
    rs_path = vault / runstate.runstate_rel_path(today)
    if not rs_path.is_file():
        return {}
    return gather._extract_json_block(
        rs_path.read_text(encoding="utf-8", errors="replace")
    ) or {}


def _todoist_completed_probe(client: Any):
    """T19 prior-resolution signal (a): a callable answering "is this Todoist
    task completed?" — unified-API v1 ``checked`` (v2 compat ``is_completed``).
    None client → no probe (micro_adventure treats it as inconclusive)."""
    if client is None:
        return None

    def probe(task_id: str) -> bool | None:
        task = client.get_task(task_id)  # raising → inconclusive (module catches)
        if not isinstance(task, dict):
            return None
        flag = task.get("checked")
        if flag is None:
            flag = task.get("is_completed")
        return bool(flag) if flag is not None else False

    return probe


def _daily_note_live_probe(vault: Path):
    """T19 prior-resolution signal (b): read a date's daily note and inspect
    its '### Live' checkbox. Missing note → inconclusive."""

    def probe(d: date) -> bool | None:
        note = vault / "30 - Daily" / f"{d.isoformat()}.md"
        if not note.is_file():
            return None
        return micro_adventure.daily_note_live_done(
            note.read_text(encoding="utf-8", errors="replace")
        )

    return probe


def _micro_adventure_state(
    vault: Path, sections: dict[str, Any], today: date, todoist_c: Any = None,
) -> tuple[dict[str, Any], Any]:
    """Read-only micro-adventure contract (T19 / locked decision 25): pool +
    rotation from config, history from the vault log, prior-entry resolution,
    deterministic LRU selection. Returns (JSON-safe state, done_update) —
    done_update is a HistoryEntry flushed to the log only in the commit path.
    Degrades to a no-pick state on any failure; never raises."""
    try:
        section = sections.get("Micro-Adventures") if isinstance(sections, dict) else None
        pool = micro_adventure.parse_pool(section)
        window = micro_adventure.exclude_window_days(section)
        history = micro_adventure.read_history(vault / micro_adventure.HISTORY_REL_PATH)
        resolution = micro_adventure.resolve_prior(
            history,
            today=today,
            todoist_completed=_todoist_completed_probe(todoist_c),
            daily_note_live_checked=_daily_note_live_probe(vault),
        )
        sel = micro_adventure.select_today(
            pool, resolution.history, today=today, window_days=window
        )

        def _idea(p: Any) -> dict[str, Any]:
            return {"id": p.id, "idea": p.idea, "category": p.category}

        state = {
            "auto_pick": _idea(sel.pick) if sel.pick else None,
            "live_pool": [_idea(p) for p in sel.live_pool],
            "streak": sel.streak,
            "pending_confirm": (
                {
                    "date": resolution.pending_confirm.date.isoformat(),
                    "id": resolution.pending_confirm.id,
                    "idea": resolution.pending_confirm.idea,
                }
                if resolution.pending_confirm
                else None
            ),
        }
        return state, resolution.done_update
    except Exception:  # noqa: BLE001 — LD25: any failure degrades to a plain Live block
        return {"auto_pick": None, "live_pool": [], "streak": 0,
                "pending_confirm": None}, None


def _ensure_micro_adventure(
    config: dict[str, Any], vault: Path, today: date,
) -> dict[str, Any]:
    """Server-authoritative micro_adventure merge for the commit paths: the
    dated runstate override wins; otherwise the deterministic auto-pick. Never
    trusts the client-echoed config alone (LD25: the app, not the client,
    owns selection)."""
    if config.get("micro_adventure"):
        return config
    micro = _read_today_runstate(vault, today).get("micro_adventure")
    if not micro:
        result = config_reader.read_config(vault)
        sections: dict[str, Any] = (
            dict(result.config.sections) if result.config is not None else {}
        )
        state, _ = _micro_adventure_state(vault, sections, today, None)
        micro = state["auto_pick"]
    if micro:
        return {**config, "micro_adventure": micro}
    return config


def _append_micro_adventure_history(
    report: Any, config: dict[str, Any], intents: list[Any], vault: Path, today: date,
) -> None:
    """T19 commit-path history append — the ONLY surface that writes the log
    (LD25: previews/refreshes/reloads never consume a pick). Idempotent via
    upsert (resume/re-commit replaces today's head entry). Also flushes any
    checkbox-resolved prior done_update. Any failure degrades to
    ``micro_adventure_logged: False`` and never blocks the day."""
    if not isinstance(report, dict):
        return
    report["micro_adventure_logged"] = False
    micro = config.get("micro_adventure")
    if not micro or not report.get("ok"):
        return
    idea = micro.get("idea") if isinstance(micro, dict) else str(micro)
    if not idea:
        return
    live_name = f"🌱 {idea}"
    if not any(
        getattr(i, "surface", None) == "todoist" and getattr(i, "name", None) == live_name
        for i in intents
    ):
        return  # Live block off/skipped today — no selection was committed
    try:
        log_path = vault / micro_adventure.HISTORY_REL_PATH
        history = micro_adventure.read_history(log_path)
        resolution = micro_adventure.resolve_prior(
            history, today=today,
            todoist_completed=None,  # commit-time flush uses checkbox only
            daily_note_live_checked=_daily_note_live_probe(vault),
        )
        hist = micro_adventure.apply_done_update(history, resolution.done_update)
        touched = ((report.get("surfaces") or {}).get("todoist") or {}).get("touched") or {}
        entry = micro_adventure.build_history_entry(
            str((micro.get("id") if isinstance(micro, dict) else None) or "custom"),
            str(idea), today=today, todoist_task_id=touched.get(live_name),
        )
        micro_adventure.write_history(
            log_path, micro_adventure.upsert_today_entry(hist, entry)
        )
        report["micro_adventure_logged"] = True
    except Exception:  # noqa: BLE001 — log failure never blocks the committed day
        report["micro_adventure_logged"] = False


def _anchored_source_fingerprint(config: dict[str, Any]) -> str:
    """Deterministic fingerprint of raw anchored config specs, before dated
    Day Setup overrides. This is deliberately separate from the client's
    effective fixed-input fingerprint: a retained override must not hide an
    upstream config edit (Cockpit locked decision 21)."""
    rows: list[dict[str, Any]] = []
    for name, spec in shadow._anchored_specs(config).items():
        normalized = {
            str(key).strip().lower(): (
                value.strip() if isinstance(value, str) else value
            )
            for key, value in spec.items()
        }
        normalized["id"] = name.strip()
        rows.append(normalized)
    rows.sort(key=lambda row: str(row.get("id") or ""))
    canonical = json.dumps(rows, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _blocks_of_minutes(minutes: int) -> int:
    return (max(0, minutes) + 29) // 30


def _duration_minutes(dur: Any) -> int | None:
    """Delegates to time_engine.duration_minutes (T22 promotion — one parser
    shared with shadow's Step E anchored parity)."""
    return time_engine.duration_minutes(dur)


# T4 (cockpit-overhaul): contract-defined per-type duration fields. The vault
# FileClass owns the contract — press notes carry duration_min (minutes).
_TYPE_DURATION_FIELDS: dict[str, str] = {"press": "duration_min"}


def _preset_blocks(name: str, presets: list[dict[str, Any]]) -> float | int | None:
    """Blocks from the ``## Presets`` row whose Name matches ``name``
    (case/whitespace-insensitive). None on no match or unparseable Blocks."""
    key = name.strip().casefold()
    for row in presets or []:
        if not isinstance(row, dict):
            continue
        row_name = str(row.get("Name") or row.get("name") or "").strip().casefold()
        if row_name != key or not key:
            continue
        try:
            blocks = float(str(row.get("Blocks") or row.get("blocks")).strip())
        except (TypeError, ValueError):
            return None
        return int(blocks) if blocks.is_integer() else blocks
    return None


def resolve_assigned_blocks(
    item: dict[str, Any],
    presets: list[dict[str, Any]],
    fm: dict[str, Any] | None = None,
) -> float | int:
    """Locked decision 14 duration precedence for an assigned row:
    Todoist-native duration → name-matched Presets row → contract-defined
    type field (press duration_min) → 1 block. Explicit zero from a matched
    source stays 0 (background rows); only absent/unparseable falls through.
    Pure — no vault writes, no schema fields, no session-override handling
    (today-only edits are client state, locked decision 14)."""
    native = item.get("duration")
    if isinstance(native, (int, float)) and not isinstance(native, bool):
        return _blocks_of_minutes(int(native))
    preset = _preset_blocks(str(item.get("name") or ""), presets)
    if preset is not None:
        return preset
    if fm is not None:
        for t in item.get("types") or []:
            field = _TYPE_DURATION_FIELDS.get(str(t))
            if field is None:
                continue
            mins = _duration_minutes(fm.get(field))
            if mins is not None:
                return _blocks_of_minutes(mins)
    return 1


def _spec_blocks(spec: dict[str, Any]) -> int:
    """An anchored/busy spec's capacity cost in blocks: the Duration field
    ("30m" / "1h20m" / int minutes) when present — a window block consumes
    its duration, not its whole window — else End−Start (calendar busy
    blocks; midnight-wrapping, so a 23:00–00:30 event costs 90 min, G27)."""
    mins = _duration_minutes(spec.get("Duration") or spec.get("duration"))
    if mins is not None:
        return _blocks_of_minutes(mins)
    start = time_engine.to_hhmm(spec.get("Start") or spec.get("start"))
    end = time_engine.to_hhmm(spec.get("End") or spec.get("end"))
    if start and end:
        s = int(start[:2]) * 60 + int(start[3:])
        e = int(end[:2]) * 60 + int(end[3:])
        d = e - s if e >= s else e + 24 * 60 - s
        if d > 0:
            return _blocks_of_minutes(d)
    return 0


def _calendar_union_blocks(
    busy_blocks: list[dict[str, Any]],
    frame_start: str,
    frame_end: str,
    capacity_class: str,
) -> int:
    """Ceiling block cost of one calendar class inside the active frame.

    Overlapping meetings are unioned before rounding, so two work events that
    share clock time consume that time once. Events wholly before/after the
    frame and per-day ``skip_today`` rows cost zero.
    """
    start_hhmm = time_engine.to_hhmm(frame_start)
    end_hhmm = time_engine.to_hhmm(frame_end)
    if not start_hhmm or not end_hhmm:
        return 0

    def minute(hhmm: str) -> int:
        return int(hhmm[:2]) * 60 + int(hhmm[3:])

    frame_a, frame_b = minute(start_hhmm), minute(end_hhmm)
    if frame_b <= frame_a:
        return 0

    intervals: list[tuple[int, int]] = []
    for block in busy_blocks:
        if block.get("skip_today"):
            continue
        if block.get("capacity_class", "fixed") != capacity_class:
            continue
        a_hhmm = time_engine.to_hhmm(block.get("Start") or block.get("start"))
        b_hhmm = time_engine.to_hhmm(block.get("End") or block.get("end"))
        if not a_hhmm or not b_hhmm:
            continue
        a, b = minute(a_hhmm), minute(b_hhmm)
        if b <= a:
            b += 24 * 60
        clipped_a, clipped_b = max(a, frame_a), min(b, frame_b)
        if clipped_b > clipped_a:
            intervals.append((clipped_a, clipped_b))

    if not intervals:
        return 0
    intervals.sort()
    union_minutes = 0
    cur_a, cur_b = intervals[0]
    for a, b in intervals[1:]:
        if a <= cur_b:
            cur_b = max(cur_b, b)
        else:
            union_minutes += cur_b - cur_a
            cur_a, cur_b = a, b
    union_minutes += cur_b - cur_a
    return _blocks_of_minutes(union_minutes)


def _capacity_frame(
    config: dict[str, Any],
    day_setup: dict[str, Any],
    busy_blocks: list[dict[str, Any]],
    habits: dict[str, Any],
    resolved_day_semantics: dict[str, Any] | None = None,
    *,
    extra_selected_blocks: int | float = 0,
    now: datetime | None = None,
) -> tuple[time_engine.TimeFrame, capacity_mod.Capacity]:
    """Shared time-frame + 6-segment capacity assembly (ui-revamp T2).

    The single computation path behind /plan-inputs and /capacity-preview —
    ``config`` must already have Day Setup applied (shadow.apply_day_setup).
    Buffering default is 'minimal' (SKILL.md 397/797); the JS 'standard'
    default was the G27 divergence.
    """
    defaults: dict[str, Any] = dict(config.get("Defaults") or {})
    frame = time_engine.compute_time_frame(
        now=now or datetime.now(),
        config_eod=time_engine.to_hhmm(defaults.get("eod")) or "23:59",
        round_to_minutes=int(defaults.get("anchor.round_to_minutes") or 15),
        # T28: a dismissed (not-attending) calendar row frees its interval —
        # it must not truncate the frame or count as fixed capacity.
        # Contract 17: quarantined (unknown, unreviewed) calendars are
        # excluded from planning exactly like ignored rows.
        busy_events=[{"start": b.get("Start"), "title": b.get("Block")}
                     for b in busy_blocks
                     if b.get("Start") and not b.get("skip_today")
                     and b.get("capacity_class", "fixed")
                     not in ("ignored", calendar_bridge.CAPACITY_CLASS_QUARANTINED)],
        anchor_override=time_engine.to_hhmm(day_setup.get("anchor")),
        eod_override=time_engine.to_hhmm(day_setup.get("eod")),
    )
    anchored_specs = shadow._anchored_specs(config)
    fixed_blk = sum(
        _spec_blocks(b)
        for b in busy_blocks
        if not b.get("skip_today")
        and b.get("capacity_class", "fixed") == "fixed"
    )
    anch_blk = sum(_spec_blocks(s) for s in anchored_specs.values()
                   if not shadow._anchored_block_off(s))
    habits_blk = _blocks_of_minutes(int(habits.get("est_minutes") or 0))
    sched = dict(day_setup.get("schedulable") or {})
    # Mint is reserved by the resolved integer-minute allotment, independent
    # of the legacy schedulable row. Never count canonical Minting twice.
    semantics = resolved_day_semantics or {}
    mint_minutes = int(semantics.get("effective_allotment_minutes") or 0)
    allotted_work_blk = mint_minutes / 30
    work_busy_blk = _calendar_union_blocks(
        busy_blocks, frame.anchor, frame.effective_eod, "work"
    )
    mint_blk = max(allotted_work_blk, work_busy_blk)
    work_overflow_blk = max(0, work_busy_blk - allotted_work_blk)
    if float(mint_blk).is_integer():
        mint_blk = int(mint_blk)
    sched_blk = sum(
        int((v or {}).get("n") or 0)
        for key, v in sched.items()
        if str(key).strip().casefold() != "minting" and (v or {}).get("on")
    )
    buf_choice = str(day_setup.get("buffering") or "minimal")
    buf_pct = float(defaults.get(f"buffering.{buf_choice}_pct") or 0.0)
    cap = capacity_mod.compute_capacity(
        total=frame.total_blocks,
        fixed=fixed_blk, anchored=anch_blk, habits=habits_blk, mint=mint_blk,
        selected=sched_blk + extra_selected_blocks, buffering_pct=buf_pct,
        caps={"deep": int(defaults.get("caps.deep") or 0),
              "mixed": int(defaults.get("caps.mixed") or 0)},
        habits_note=(f"habits: {habits.get('done', 0)} done "
                     f"· {habits.get('outstanding', 0)} left"),
        work_busy=work_busy_blk,
        work_overflow=work_overflow_blk,
    )
    return frame, cap


def create_app(vault_root: str | Path | None = None) -> FastAPI:
    """Build the TDTB FastAPI app.

    ``vault_root`` (tests inject a tmp dir here) overrides the
    ``TDTB_VAULT_ROOT`` env var; neither present → vault-dependent routes
    return 503 rather than guessing a path.
    """
    app = FastAPI(title="TDTB", docs_url=None, redoc_url=None)
    app.state.vault_root = str(vault_root) if vault_root else None
    app.state.token = secrets.token_urlsafe(32)
    # Tests inject a Callable[[Path, dict], tuple[todoist_like, store_like|None]]
    # here so mode=live can be exercised without a real Todoist token or
    # EventKit grant. None (the default) means "build the real clients".
    app.state.build_commit_clients = None
    # Read-side twin for /plan-inputs source aggregation: a Callable
    # [[Path, dict], tuple[todoist_like|None, store_like|None]]. Default is
    # OFFLINE ((None, None) → degrade warnings) so unit tests never touch the
    # live token/EventKit; the real-server module bottom swaps in
    # build_real_read_clients. Tests inject fakes here.
    app.state.build_read_clients = None
    # G25: in-flight guard on POST /commit?mode=live — two racing live commits
    # both pass check-before-write against the same snapshot and double-write.
    app.state.live_commit_lock = threading.Lock()

    def resolve_vault_root() -> Path:
        root = app.state.vault_root or os.environ.get(VAULT_ROOT_ENV)
        if not root:
            raise HTTPException(
                status_code=503,
                detail=f"vault root not configured — set {VAULT_ROOT_ENV}",
            )
        path = Path(root).expanduser()
        if not path.is_dir():
            raise HTTPException(status_code=503, detail=f"vault root not found: {path}")
        return path

    def require_token(x_tdtb_token: str | None = Header(default=None)) -> None:
        if not x_tdtb_token or not secrets.compare_digest(x_tdtb_token, app.state.token):
            raise HTTPException(status_code=403, detail="missing or invalid X-TDTB-Token")

    def _run_gather(vault: Path, today: date) -> tuple[list[dict], list[dict]]:
        pool_notes: list[dict[str, Any]] = []
        assigned_notes: list[dict[str, Any]] = []
        for note in gather.walk_vault(vault):
            name, folder, fm = note["name"], note["folder"], note["fm"]
            if gather.is_assigned(folder, fm):
                # Frozen contract 5: future-dated vault work does not appear
                # as today's work or consume today's capacity. Assigned notes
                # are never pool-eligible (the base filter rejects the
                # assigned flag), so excluding them here removes them from
                # today's digest entirely. Past-due and undated stay.
                deadline = gather.get_deadline(fm)
                if deadline is not None and deadline > today:
                    continue
                assigned_notes.append(note)
            if gather.is_in_pool(name, folder, fm, today):
                pool_notes.append(note)
        return pool_notes, assigned_notes

    def _ranking_order(vault: Path) -> list[str]:
        result = config_reader.read_config(vault)
        if result.config is not None:
            value = result.config.get_ranking_criterion("within_tier_sort").value
            if isinstance(value, str):
                return [p.strip() for p in value.split(",") if p.strip()]
            if isinstance(value, list):
                return value
        return list(config_reader.FALLBACK_RANKING_CRITERIA["within_tier_sort"])

    # -- G24: persistent billed-call ledger ----------------------------------

    def _billed_spent(vault: Path, today: date) -> int:
        state = runstate.read_runstate(vault, today) or {}
        try:
            return int(state.get("billed_calls") or 0)
        except (TypeError, ValueError):
            return 0

    def _require_billed_budget(vault: Path, today: date) -> None:
        spent = _billed_spent(vault, today)
        if spent >= BILLED_CAP:
            raise HTTPException(
                status_code=429,
                detail=f"billed budget spent ({spent}/{BILLED_CAP}) for {today}",
            )

    def _billed_ctx(vault: Path, today: date) -> judgment.RunContext:
        """RunContext whose charge hook spends the persistent per-day ledger
        once per REAL SDK attempt (retries included), atomically (G26 lock)."""
        def charge(label: str) -> None:
            def spend(state: dict) -> None:
                spent = int(state.get("billed_calls") or 0)
                if spent >= BILLED_CAP:
                    raise judgment.BudgetExceededError(
                        f"billed budget spent ({spent}/{BILLED_CAP}) for {today}: {label}"
                    )
                state["billed_calls"] = spent + 1
            runstate.update_runstate(vault, today, spend)
        return judgment.RunContext(charge=charge)

    # -- FEEDBACK-24: Day Setup confirmation gate ---------------------------

    def _require_day_setup(vault: Path, today: date, action: str) -> None:
        """Fail closed (409, actionable) when today's runstate holds no
        explicit Day Setup confirmation. Only a successful POST /day-setup
        for this date writes the confirmation key — skeleton keys, Drop,
        ledger, and other unrelated runstate writes never satisfy it."""
        if not runstate.is_day_setup_confirmed(vault, today):
            raise HTTPException(
                status_code=409,
                detail=f"Day Setup not confirmed for {today} — confirm Day "
                       f"Setup before {action}",
            )

    # -- tokenless reads ----------------------------------------------------

    @app.get("/health")
    def health() -> dict:
        return {
            "status": "ok",
            "judgment_model": judgment.OPENROUTER_MODEL,
        }

    @app.get("/session-token")
    def get_session_token(request: Request) -> dict:
        """Tokenless localhost-only read exposing the per-session X-TDTB-Token
        so the thin static UI (T10) can call the token-guarded /digest POST.

        Localhost-restricted (not just tokenless) as the simplest safe option:
        the app already binds 127.0.0.1-only by contract (module docstring),
        so this is defense-in-depth rather than the primary boundary. No
        existing route's guard is weakened — /gather, /digest, /adjust,
        /sequence, /commit still require X-TDTB-Token via require_token.
        """
        client_host = request.client.host if request.client else None
        # "testclient" is Starlette TestClient's simulated host (no real socket) —
        # allowed so the route is exercisable under pytest without weakening the
        # real-world boundary (an actual network client never presents that host).
        if client_host not in ("127.0.0.1", "::1", "localhost", "testclient"):
            raise HTTPException(status_code=403, detail="session-token is localhost-only")
        return {"token": app.state.token}

    @app.get("/config")
    def get_config() -> dict:
        vault = resolve_vault_root()
        result = config_reader.read_config(vault)
        if result.bootstrap_needed:
            return {"bootstrap_needed": True, "sections": [], "validation": None}
        assert result.config is not None and result.validation is not None
        return {
            "bootstrap_needed": False,
            "sections": sorted(result.config.sections.keys()),
            "validation": {
                "valid": result.validation.valid,
                "missing_sections": result.validation.missing_sections,
                "missing_keys": result.validation.missing_keys,
                "malformed_rows": result.validation.malformed_rows,
            },
        }

    @app.get("/plan-inputs")
    def get_plan_inputs() -> dict:
        """T16: read-only assembly of the {digest, config, anchored_blocks}
        inputs the timeline view needs to build its /sequence,
        /validate-sequence, and /commit bodies. The browser can't read the
        vault and /config exposes only section *keys*; this mirrors
        build_commit_body.build_body's input assembly (minus the sequence).

        Tokenless GET, like /config. T16 justified that with "writes nothing";
        allocator-rewrite T2 narrows the claim rather than dropping it: the
        route writes exactly ONE run-state key, ``digest_index``, and nothing
        else. That write is derived solely from data this same GET returns, is
        confined to a non-authoritative cache key, touches no external system,
        and is idempotent for identical vault state — so the token boundary
        (which gates external writes and billed calls) is unchanged. The write
        is deliberately LAST, after ``day_setup`` is read back."""
        vault = resolve_vault_root()
        today = gather.effective_date(datetime.now())
        pool_notes, assigned_notes = _run_gather(vault, today)
        run_data = gather.build_run_data(pool_notes, assigned_notes, today)
        order = _ranking_order(vault)

        result = config_reader.read_config(vault)
        config: dict[str, Any] = (
            dict(result.config.sections) if result.config is not None else {}
        )

        # -- external sources (gather-parity 2026-07-14) ---------------------
        # Todoist items join the digest; calendar events become busy blocks;
        # habits ride as a capacity summary. Every degrade path lands in
        # source_warnings — the UI renders them loudly (locked decision 3).
        ext_cfg: dict[str, Any] = {
            **dict(config.get("Defaults") or {}),
            "calendar_capacity_classes": config.get("Calendar Capacity Classes"),
        }
        build_clients = app.state.build_read_clients or (lambda v, c: (None, None))
        todoist_c, store = build_clients(vault, config)
        try:
            t_assigned, t_pool, w_todo = external_sources.fetch_todoist_items(
                todoist_c, ext_cfg
            )
            if store is not None:
                try:
                    resolved, _missing = calendar_bridge.resolve_titles_to_ids(
                        calendar_bridge.normalize_title_map(
                            config.get("Calendar Titles")
                        ),
                        store.calendars(),
                    )
                    ext_cfg = {**ext_cfg, "calendar_ids": resolved}
                except Exception:  # noqa: BLE001 — no titles → no own-write exclusion
                    pass
            busy_blocks, w_cal = external_sources.fetch_calendar_busy(
                store, ext_cfg, today
            )
            habits, w_hab = external_sources.fetch_habit_status(vault, ext_cfg, today)
            # T19: deterministic micro-adventure state — pure reads (config
            # section, vault log, prior daily note, Todoist completion probe
            # on the already-open read client). Never writes, never consumes.
            ma_state, _ma_done = _micro_adventure_state(vault, config, today, todoist_c)
        finally:
            if todoist_c is not None and hasattr(todoist_c, "close"):
                try:
                    todoist_c.close()
                except Exception:  # noqa: BLE001
                    pass

        # Sequence identity downstream is name-keyed (timeline id = name) —
        # rename Todoist items that collide with vault names before merging.
        vault_all = run_data["pool_items"] + run_data["assigned_items"]
        t_assigned = external_sources.disambiguate_names(vault_all, t_assigned)
        t_pool = external_sources.disambiguate_names(vault_all + t_assigned, t_pool)
        digest = build_digest(
            run_data["pool_items"] + t_pool,
            run_data["assigned_items"] + t_assigned,
            today,
            order,
            ignore=(
                result.config.get_ignore_list() if result.config is not None else None
            ),
            bias=deferrals.bias_map(vault, today),  # T1 defer-with-memory
        )

        # T4 (cockpit-overhaul): assigned rows gain resolved `blocks` per the
        # locked precedence (Todoist-native → Preset → press duration_min → 1).
        # Suggested rows stay untouched — the cockpit never consumes them.
        presets = result.config.get_presets() if result.config is not None else []
        fm_by_path = {n["path"]: n["fm"] for n in assigned_notes}
        for row in digest["assigned"]:
            row["blocks"] = resolve_assigned_blocks(
                row, presets, fm_by_path.get(row.get("path"))
            )

        # micro_adventure side-load (Locked #7 / build_commit_body parity):
        # today's exact-date run-state selection merges into config so a
        # selected Live micro-adventure reaches the /commit Live→Todoist
        # reroute. Missing note or absent key → no-op. Reads the dated note
        # directly (not load_runstate, which returns the strictly-prior note).
        rs_path = vault / runstate.runstate_rel_path(today)
        if rs_path.is_file():
            rs_data = gather._extract_json_block(
                rs_path.read_text(encoding="utf-8", errors="replace")
            )
            micro = (rs_data or {}).get("micro_adventure")
            if micro:
                config = {**config, "micro_adventure": micro}
        # T19: no dated override → the deterministic auto-pick rides config so
        # sequence/shadow/commit bodies built from this payload reroute Live →
        # Todoist exactly like a skill run (SKILL.md Step E).
        micro_override = config.get("micro_adventure")
        if not micro_override and ma_state["auto_pick"]:
            config = {**config, "micro_adventure": ma_state["auto_pick"]}
        micro_payload = {
            "pick": micro_override or ma_state["auto_pick"],
            "source": "override" if micro_override else "auto",
            "live_pool": ma_state["live_pool"],
            "streak": ma_state["streak"],
            "pending_confirm": ma_state["pending_confirm"],
        }

        # -- Day Setup + time/capacity (ui-parity T4) ------------------------
        day_setup = {k: v for k, v in _read_today_runstate(vault, today).items()
                     if k in _DAY_SETUP_KEYS and v not in ("", None)}
        resolved_day_semantics = day_semantics.resolve_day_contract(
            result, today, dated_overrides=day_setup,
        )
        # Expose configured Trinoor windows as concrete Mint-session choices
        # even when the current allotment is zero. The user can enable the
        # allotment and choose sessions in one Day Setup save.
        resolved_day_semantics = {
            **resolved_day_semantics,
            "mint_sessions": external_sources.mint_session_options(config),
        }
        planning_config_fingerprint = day_semantics.planning_config_fingerprint(
            result, today, dated_overrides=day_setup,
        )
        anchored_source_fingerprint = _anchored_source_fingerprint(config)
        config = shadow.apply_day_setup(config, day_setup)

        # T28: per-day calendar dismissal (plan participation only) rides the
        # emitted rows and frees capacity; the source calendar is never touched.
        busy_effective = shadow.apply_calendar_participation(busy_blocks, day_setup)
        # Quarantined (unknown) calendars must not affect planning either —
        # excluded from the frame scan alongside ignored rows (contract 17).
        frame, cap = _capacity_frame(
            config, day_setup, busy_effective, habits, resolved_day_semantics,
        )

        anchored = (
            config.get("Anchored Lifestyle Blocks")
            or config.get("anchored_blocks")
            or []
        )

        # IMP-05 Drop from plan: date-scoped exclusions remove rows from
        # today's planning digest and surface under Dropped today. The
        # identity index is written from the FILTERED digest so a dropped
        # item is also unresolvable to staging verbs this date.
        dropped_rows = runstate.read_dropped(vault, today)
        dropped_ids = {str(d.get("identity"))
                       for d in dropped_rows if d.get("identity")}
        if dropped_ids:
            digest["assigned"] = [r for r in digest["assigned"]
                                  if runtime_actions.drop_identity_of(r)
                                  not in dropped_ids]
            digest["suggested"] = [r for r in digest["suggested"]
                                   if runtime_actions.drop_identity_of(r)
                                   not in dropped_ids]

        # T2 (allocator rewrite): persist the digest's identity index so the
        # staging-phase runtime verbs can resolve a target before a commit
        # exists. Both surfaces are indexed — the forgot-strip promotes
        # suggested rows, and a row can be completed/deleted from either.
        # Its own dated file, NOT a run-state key: writing run-state here would
        # materialise the dated note, after which every later day_setup read
        # treats the skeleton's empty defaults as user-confirmed.
        runstate.write_digest_index(vault, today, build_digest_index(digest))

        return {
            "digest": digest,
            "config": config,
            "anchored_blocks": list(anchored) + busy_effective,
            "anchored_source_fingerprint": anchored_source_fingerprint,
            "habits": habits,
            "time": frame.as_dict(),
            "capacity": cap.as_dict(),
            "day_setup": day_setup,
            # FEEDBACK-24: the ONLY signal the UI may treat as "Day Setup
            # confirmed" — a skeleton echo (any non-empty day_setup keys)
            # must never imply confirmation.
            "day_setup_confirmed": runstate.is_day_setup_confirmed(vault, today),
            "day_semantics": resolved_day_semantics,
            "planning_config_fingerprint": planning_config_fingerprint,
            "micro_adventure": micro_payload,
            "dropped_today": dropped_rows,
            "source_warnings": w_todo + w_cal + w_hab,
            "source_counts": {
                "vault": len(run_data["pool_items"]) + len(run_data["assigned_items"]),
                "todoist": len(t_assigned) + len(t_pool),
                "calendar": len(busy_blocks),
            },
        }

    @app.get("/billed-ledger")
    def get_billed_ledger() -> dict:
        """G24: tokenless read of the persistent per-day billed-call ledger,
        so UI budget counters render the server's number, not a client-side
        guess (same contract stance as /capacity-preview)."""
        vault = resolve_vault_root()
        today = gather.effective_date(datetime.now())
        spent = _billed_spent(vault, today)
        return {
            "today": str(today),
            "spent": spent,
            "cap": BILLED_CAP,
            "remaining": max(0, BILLED_CAP - spent),
        }

    @app.get("/capacity-preview")
    def get_capacity_preview(
        day_setup: str | None = None, selected: str | None = None
    ) -> dict:
        """ui-revamp T2 (G19/G27): the budget bar's single number source.

        Tokenless read-only GET like /plan-inputs. Accepts the UI's
        *proposed* (unsaved) Day Setup state so the bar renders live edits
        without a runstate write:

        - ``day_setup``: JSON object of Day Setup overrides, merged OVER
          today's persisted runstate blob (same key set /plan-inputs echoes).
        - ``selected``: JSON array of included assigned-row durations
          ("1h30m" | "90m" | bare minutes | null). Selected = included
          assigned rows + schedulables (SKILL.md 763); durations parse
          server-side so the frontend never does block math. Explicit zero
          costs 0 blocks (no min-1 clamp); a null/missing duration defaults
          to 1 block (the old bar's row default).

        Frontends render the returned numbers and readout strings verbatim
        (locked decision 2, 2026-07-16-tdtb-ui-revamp.md) — the G27
        divergence class dies by construction.
        """
        def _parse(name: str, raw: str | None, expect: type) -> Any:
            if raw is None:
                return None
            try:
                val = json.loads(raw)
            except ValueError:
                raise HTTPException(
                    status_code=400, detail=f"{name}: invalid JSON"
                )
            if not isinstance(val, expect):
                raise HTTPException(
                    status_code=400,
                    detail=f"{name}: expected a JSON {expect.__name__}",
                )
            return val

        overrides = _parse("day_setup", day_setup, dict) or {}
        sel_items = _parse("selected", selected, list) or []
        extra_blk: int | float = 0
        for i, dur in enumerate(sel_items):
            if dur is None:
                extra_blk += 1
                continue
            mins = _duration_minutes(dur)
            if mins is None:
                raise HTTPException(
                    status_code=400,
                    detail=f"selected[{i}]: unparseable duration {dur!r}",
                )
            # Today-only shaping supports 15-minute work items. Preserve the
            # exact fractional block cost instead of rounding 15m up to 30m.
            extra_blk += max(0, mins) / 30

        vault = resolve_vault_root()
        today = gather.effective_date(datetime.now())
        persisted = {k: v for k, v in _read_today_runstate(vault, today).items()
                     if k in _DAY_SETUP_KEYS and v not in ("", None)}
        merged = {**persisted,
                  **{k: v for k, v in overrides.items() if k in _DAY_SETUP_KEYS}}

        result = config_reader.read_config(vault)
        config: dict[str, Any] = (
            dict(result.config.sections) if result.config is not None else {}
        )
        resolved_day_semantics = day_semantics.resolve_day_contract(
            result, today, dated_overrides=merged,
        )
        planning_config_fingerprint = day_semantics.planning_config_fingerprint(
            result, today, dated_overrides=merged,
        )
        ext_cfg: dict[str, Any] = {
            **dict(config.get("Defaults") or {}),
            "calendar_capacity_classes": config.get("Calendar Capacity Classes"),
        }
        build_clients = app.state.build_read_clients or (lambda v, c: (None, None))
        todoist_c, store = build_clients(vault, config)
        try:
            if store is not None:
                try:
                    resolved, _missing = calendar_bridge.resolve_titles_to_ids(
                        calendar_bridge.normalize_title_map(
                            config.get("Calendar Titles")
                        ),
                        store.calendars(),
                    )
                    ext_cfg = {**ext_cfg, "calendar_ids": resolved}
                except Exception:  # noqa: BLE001 — no titles → no own-write exclusion
                    pass
            busy_blocks, _w_cal = external_sources.fetch_calendar_busy(
                store, ext_cfg, today
            )
            habits, _w_hab = external_sources.fetch_habit_status(
                vault, ext_cfg, today
            )
        finally:
            if todoist_c is not None and hasattr(todoist_c, "close"):
                try:
                    todoist_c.close()
                except Exception:  # noqa: BLE001
                    pass

        config = shadow.apply_day_setup(config, merged)
        # T28: proposed/persisted calendar dismissals free fixed capacity.
        busy_effective = shadow.apply_calendar_participation(busy_blocks, merged)
        frame, cap = _capacity_frame(
            config, merged, busy_effective, habits, resolved_day_semantics,
            extra_selected_blocks=extra_blk,
        )
        return {
            "segments": {
                "fixed": cap.fixed, "anchored": cap.anchored,
                "habits": cap.habits, "mint": cap.mint,
                "selected": cap.selected,
                "buffer": cap.buffer,
            },
            "total": cap.total,
            "free": cap.free,                    # signed, never clamped
            "over": max(0, -cap.free),           # blocks over; 0 when fits
            "overassigned": cap.overassigned,
            "available_for_selection": cap.available_for_selection,
            "remaining": cap.remaining,
            "ratio": cap.ratio,
            "legend": cap.legend,
            "counters": cap.counters,
            "work_busy": cap.work_busy,
            "work_overflow": cap.work_overflow,
            "time": frame.as_dict(),
            "day_setup_echo": merged,
            "day_semantics": resolved_day_semantics,
            "planning_config_fingerprint": planning_config_fingerprint,
        }

    # -- mutating routes (token-guarded) --------------------------------------

    @app.post("/gather", dependencies=[Depends(require_token)])
    def post_gather() -> dict:
        """Run the deterministic vault gather; writes the active-inventory
        cache and the trigger-1 run-state note (both vault-side writes —
        hence token-guarded)."""
        vault = resolve_vault_root()
        today = gather.effective_date(datetime.now())
        pool_notes, assigned_notes = _run_gather(vault, today)

        cache = gather.build_cache(pool_notes, today)
        gather.write_cache(cache, vault)

        run_data = gather.build_run_data(pool_notes, assigned_notes, today)
        # Merge-preserving (T12 audit): a re-gather must not reset today's
        # note to the skeleton — that wiped confirmed Day Setup (anchor/eod/
        # buffering/anchored/captures) and the commit ledger on any second
        # /gather. Seed defaults only for keys the existing note lacks.
        # G26: atomic RMW under the per-day lock — a bare read+write here
        # raced /day-setup and lost its update.
        # T19 / LD25: gather populates the dated micro-adventure runstate keys
        # (pool/streak/pending, plus the auto-pick only when no dated override
        # exists) but never consumes an idea — history appends live solely in
        # the commit path.
        result = config_reader.read_config(vault)
        sections: dict[str, Any] = (
            dict(result.config.sections) if result.config is not None else {}
        )
        ma_state, _ = _micro_adventure_state(vault, sections, today, None)
        ma_updates: dict[str, Any] = {
            "live_pool": ma_state["live_pool"],
            "live_streak": ma_state["streak"],
            "pending_confirm": ma_state["pending_confirm"],
        }
        if not _read_today_runstate(vault, today).get("micro_adventure"):
            ma_updates["micro_adventure"] = ma_state["auto_pick"]
        runstate.update_runstate(vault, today, ma_updates)
        return run_data

    @app.post("/day-setup", dependencies=[Depends(require_token)])
    def post_day_setup(body: DaySetupRequest) -> dict:
        """Persist the Phase-1 Day Setup confirm into today's run-state note
        (session/day-scoped — never vault config). Derives ``re_included``
        server-side, once, per skill 819: a block whose window-passed DEFAULT
        is off/skipped but which the payload turns on."""
        vault = resolve_vault_root()
        today = gather.effective_date(datetime.now())

        result = config_reader.read_config(vault)
        config: dict[str, Any] = (
            dict(result.config.sections) if result.config is not None else {}
        )
        defaults: dict[str, Any] = dict(config.get("Defaults") or {})

        anchor = time_engine.to_hhmm(body.anchor) or time_engine.compute_time_frame(
            now=datetime.now(),
            config_eod=time_engine.to_hhmm(defaults.get("eod")) or "23:59",
            round_to_minutes=int(defaults.get("anchor.round_to_minutes") or 15),
        ).anchor

        defaults_off = shadow.past_window_defaults(config, anchor, today)
        re_included: set[str] = set()
        for o in body.anchored or []:
            name = str(o.get("id") or "")
            on = o.get("on") is True or (o.get("skip_today") is False)
            if name in defaults_off and on and not o.get("skip_today"):
                re_included.add(name)
        schedulable = body.schedulable
        minting = (schedulable or {}).get("minting") or {}
        if isinstance(minting, dict) and isinstance(minting.get("sessions"), list):
            minting = external_sources.normalize_mint_session_override(
                config, minting
            )
            schedulable = {
                **(schedulable or {}),
                "minting": minting,
            }
        if "Minting" in defaults_off and minting.get("on"):
            re_included.add("Minting")

        updates: dict[str, Any] = {"re_included": sorted(re_included)}
        if body.anchor:
            updates["anchor"] = time_engine.to_hhmm(body.anchor) or body.anchor
        if body.eod:
            updates["eod"] = time_engine.to_hhmm(body.eod) or body.eod
        if body.buffering:
            updates["buffering"] = body.buffering
        if schedulable is not None:
            updates["schedulable"] = schedulable
        if body.anchored is not None:
            updates["anchored"] = body.anchored
        for key in ("intention", "megan_nicety", "stoic_intention"):
            val = (body.captures or {}).get(key)
            if val is not None:
                updates[key] = val
        # T18b.2 tri-state: omitted preserves (do not write); explicit null
        # clears (write None); explicit value persists. Field presence is
        # detected via model_fields_set so a default value never counts as
        # present. work_allotment_minutes is validated as a nonnegative 15-
        # divisible integer when not None.
        present = body.model_fields_set
        if "day_preset" in present:
            updates["day_preset"] = body.day_preset
        if "work_allotment_minutes" in present:
            allot = body.work_allotment_minutes
            if allot is not None:
                if not isinstance(allot, int) or isinstance(allot, bool):
                    raise HTTPException(
                        status_code=422,
                        detail=f"work_allotment_minutes must be an integer, got {type(allot).__name__}",
                    )
                if allot < 0 or allot % 15 != 0:
                    raise HTTPException(
                        status_code=422,
                        detail=f"work_allotment_minutes must be a nonnegative integer divisible by 15, got {allot}",
                    )
            updates["work_allotment_minutes"] = allot
        if isinstance(minting, dict) and isinstance(minting.get("sessions"), list):
            # Concrete Mint session choices and the total are one persisted
            # value.  This intentionally wins over an omitted, null, or
            # mismatched work_allotment_minutes field from older clients.
            updates["work_allotment_minutes"] = (
                len(minting["sessions"]) * external_sources.MINT_SESSION_MINUTES
                if minting.get("on")
                else 0
            )
        # T19 tri-state Live override: omitted preserves; explicit null clears
        # (auto-pick resumes on next read); a value must be {id, idea[, category]}
        # — shuffle/pick/custom are all free local writes, never billed.
        if "micro_adventure" in present:
            ma = body.micro_adventure
            if ma is not None:
                ma_id = str(ma.get("id") or "").strip() if isinstance(ma, dict) else ""
                ma_idea = str(ma.get("idea") or "").strip() if isinstance(ma, dict) else ""
                if not ma_id or not ma_idea:
                    raise HTTPException(
                        status_code=422,
                        detail="micro_adventure must be null or {id, idea[, category]}",
                    )
                ma = {
                    "id": ma_id,
                    "idea": ma_idea,
                    "category": (
                        str(ma.get("category") or "").strip()
                        or ("custom" if ma_id == "custom" else "")
                    ),
                }
            updates["micro_adventure"] = ma
        # G26: locked RMW — concurrent /day-setup POSTs previously lost updates.
        # FEEDBACK-24: this successful POST is the ONLY writer of the explicit
        # confirmation, scoped to today's dated note.
        updates[runstate.DAY_SETUP_CONFIRMED_KEY] = True
        state = runstate.update_runstate(vault, today, updates)
        return {"ok": True, "re_included": sorted(re_included),
                "day_setup_confirmed": True,
                "day_setup": {k: state.get(k) for k in _DAY_SETUP_KEYS}}

    @app.post("/digest", dependencies=[Depends(require_token)])
    def post_digest(body: DigestRequest | None = None) -> dict:
        """Deterministic tiered digest. Accepts pre-gathered run-data in the
        body; otherwise gathers live from the vault. Same input → identical
        output (integration-tested)."""
        vault = resolve_vault_root()
        order = _ranking_order(vault)
        if body and body.pool_items is not None:
            today = date.fromisoformat(body.today) if body.today else gather.effective_date(datetime.now())
            pool_items = body.pool_items
            assigned_items = body.assigned_items or []
        else:
            today = gather.effective_date(datetime.now())
            pool_notes, assigned_notes = _run_gather(vault, today)
            run_data = gather.build_run_data(pool_notes, assigned_notes, today)
            pool_items = run_data["pool_items"]
            assigned_items = run_data["assigned_items"]
        cfg_result = config_reader.read_config(vault)
        return build_digest(
            pool_items,
            assigned_items,
            today,
            order,
            ignore=(
                cfg_result.config.get_ignore_list() if cfg_result.config is not None else None
            ),
            bias=deferrals.bias_map(vault, today),  # T1 defer-with-memory
        )

    @app.post("/adjust", dependencies=[Depends(require_token)])
    def post_adjust(body: AdjustRequest) -> dict:
        """Translate a free-text adjustment ask into structured ops via the
        judgment layer (call #3). SDK/schema failure → 502-style JSON error,
        never a bare 500. G24: gated on + charged against the persistent
        per-day billed ledger (429 when spent)."""
        vault = resolve_vault_root()
        today = gather.effective_date(datetime.now())
        _require_billed_budget(vault, today)
        try:
            return judgment.adjust_freetext(
                body.instruction, body.digest, ctx=_billed_ctx(vault, today))
        except judgment.BudgetExceededError as exc:
            raise HTTPException(status_code=429, detail=str(exc)) from exc
        except judgment.JudgmentError as exc:
            raise HTTPException(status_code=502, detail=f"judgment error: {exc}") from exc

    def _judged_anchored(
        anchored_blocks: list[dict[str, Any]], day_setup: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """G20: the anchored set the judgment/validation layers should see —
        Day Setup overrides merged server-side (the client copy may be stale),
        then runstate-suppressed blocks (off / skip_today) dropped entirely.
        A suppressed block leaking into the prompt gets placed by the model
        (live 2026-07-16: skipped 'Morning Routine' placed pre-anchor, retry
        burned); dropping it also removes its overlap wall from validation.

        FEEDBACK-02 (frozen contract 17): quarantined (known-but-unreviewed)
        calendar titles are excluded from planning exactly like ignored rows —
        once calendar walls harden, a leaked quarantined row would silently
        become a hard wall."""
        merged = shadow.apply_day_setup(
            {"anchored_blocks": [dict(b) for b in anchored_blocks]}, day_setup
        ).get("anchored_blocks") or []
        return [
            b for b in merged
            if not shadow._anchored_block_off(b)
            and b.get("capacity_class", "fixed")
            not in ("ignored", calendar_bridge.CAPACITY_CLASS_QUARANTINED)
        ]

    @app.post("/sequence", dependencies=[Depends(require_token)])
    def post_sequence(body: SequenceRequest) -> dict:
        """Propose a timeline sequence via the judgment layer (call #4), then
        re-validate server-side (T12: sequence.validate_sequence) — the belt
        to judgment.py's suspenders. SDK/schema failure → 502-style JSON
        error. A HARD validation failure (structural, or the standing
        no-morning-workout rule) → 422 with details. Soft warnings
        (zone/latest_start) are attached to the 200 response, never gate it.

        ui-parity T5: schedulable blocks (Minting/QT/Shivery) are injected
        server-side from config + Day Setup state before the judgment call —
        @🚀10min items fold into the QT block (qt_contents), and the 🟡
        Trinoor zone backdrop rows are appended to the returned proposal
        (permeable, Step D′ — never validated as placements)."""
        vault = resolve_vault_root()
        today = gather.effective_date(datetime.now())
        pin_errors = sequence.validate_pinned_rows(body.pinned_rows, body.assigned)
        if pin_errors:
            raise HTTPException(
                status_code=422,
                detail={"message": "pinned-row validation failed", "hard_errors": pin_errors},
            )
        # T27: recurring todoist rows with a native time are placement-immune —
        # server-authoritative auto-pins for any the client didn't already pin,
        # validated with the client pins BEFORE the billed boundary so a
        # conflict fails closed without spending a call.
        auto_pins = sequence.recurring_auto_pins(
            body.assigned,
            exclude_ids={str(pin.get("id")) for pin in body.pinned_rows},
        )
        effective_pins = list(body.pinned_rows) + auto_pins
        if auto_pins:
            pin_errors = sequence.validate_pinned_rows(effective_pins, body.assigned)
            if pin_errors:
                raise HTTPException(
                    status_code=422,
                    detail={"message": "pinned-row validation failed",
                            "hard_errors": pin_errors},
                )
        _require_billed_budget(vault, today)  # G24
        day_setup = {k: v for k, v in _read_today_runstate(vault, today).items()
                     if k in _DAY_SETUP_KEYS and v not in ("", None)}
        defaults: dict[str, Any] = dict((body.config or {}).get("Defaults") or {})
        # T28: dismissed calendar rows (server-authoritative runstate merge)
        # free their interval — no frame truncation, no wall (dropped later
        # by _judged_anchored's skip filter).
        anchored_effective = shadow.apply_calendar_participation(
            body.anchored_blocks, day_setup)
        frame = time_engine.compute_time_frame(
            now=datetime.now(),
            config_eod=time_engine.to_hhmm(defaults.get("eod")) or "23:59",
            round_to_minutes=int(defaults.get("anchor.round_to_minutes") or 15),
            busy_events=[{"start": time_engine.to_hhmm(b.get("Start")),
                          "title": b.get("Block")}
                         for b in anchored_effective
                         if b.get("source") == "calendar" and b.get("Start")
                         and not b.get("skip_today")],
            anchor_override=time_engine.to_hhmm(day_setup.get("anchor")),
            eod_override=time_engine.to_hhmm(day_setup.get("eod")),
        )
        anchor = frame.anchor
        blocks, zone_rows, block_notes = external_sources.build_schedulable_blocks(
            body.config or {}, day_setup, today, anchor,
            resolved_day_semantics=body.day_semantics,
        )
        # G18a: planning-fallacy correction — inflate each assigned item's
        # block estimate by the configured factor BEFORE injection/judgment,
        # so the prompt, the duration validator, and the manifest all see the
        # corrected size. 1.0 (default) is a no-op. Injected schedulable
        # blocks and anchored blocks are config-sized, never corrected.
        try:
            factor = float(defaults.get("estimation.correction_factor") or 1.0)
        except (TypeError, ValueError):
            factor = 1.0
        corrected = body.assigned
        if factor > 1.0:
            corrected = []
            for item in body.assigned:
                blocks_est = item.get("blocks")
                n = blocks_est if isinstance(blocks_est, (int, float)) and blocks_est > 0 else 1
                corrected.append({**item, "blocks": math.ceil(n * factor)})
        qt_on = any(b.get("qt") for b in blocks)
        assigned, qt_contents = external_sources.absorb_quick_tasks(corrected, qt_on)
        # Injected block names must survive the name-keyed sequence identity —
        # disambiguate against the assigned set like any external source.
        blocks = external_sources.disambiguate_names(assigned, blocks)
        assigned = assigned + blocks
        # Selected Mint sessions are exact user-selected windows, not movable
        # work for the judgment model. Keep them in the assigned set for
        # never-bump validation, but synthesize their rows deterministically
        # after the model returns.
        fixed_schedulable_rows = sequence.placement_window_rows(blocks)
        fixed_schedulable_ids = {
            str(row.get("id")) for row in fixed_schedulable_rows
        }
        # An auto-pinned row absorbed out of the assigned set (QT fold) would
        # validate as a foreign sequence row — drop its pin with it.
        present_ids = {str(item.get("id") or item.get("name")) for item in assigned}
        effective_pins = list(body.pinned_rows) + [
            pin for pin in auto_pins if str(pin.get("id")) in present_ids
        ]
        pinned_ids = {str(pin.get("id")) for pin in effective_pins}
        movable_assigned = [
            item for item in assigned
            if str(item.get("id") or item.get("name")) not in pinned_ids
            and str(item.get("id") or item.get("name")) not in fixed_schedulable_ids
        ]

        # T7: the judgment prompt sees the live day frame (now/anchor/eod).
        seq_config = {
            **(body.config or {}),
            "time": frame.as_dict(),
            "resolved_zones": body.day_semantics.get("enabled_zones") or [],
            "overlap_permissions_raw": (
                body.day_semantics.get("overlap_permissions_raw") or ""
            ),
            "planning_config_fingerprint": body.planning_config_fingerprint,
        }

        seq_anchored = _judged_anchored(body.anchored_blocks, day_setup)
        pinned_walls = [
            {"Block": pin["id"], "Type": "hard", "Start": pin["start"],
             "End": pin["end"], "pinned": True}
            for pin in effective_pins
        ]
        try:
            proposal = judgment.propose_sequence(
                movable_assigned, seq_config, seq_anchored + pinned_walls,
                ctx=_billed_ctx(vault, today))
        except judgment.BudgetExceededError as exc:
            raise HTTPException(status_code=429, detail=str(exc)) from exc
        except judgment.JudgmentError as exc:
            raise HTTPException(status_code=502, detail=f"judgment error: {exc}") from exc

        proposal = sequence.canonicalize_sequence_ids(
            proposal, assigned + [
                {"id": block.get("Block"), "name": block.get("Block")}
                for block in seq_anchored
                if block.get("Block")
            ],
        )
        proposal["sequence"] = sequence.merge_immutable_rows(
            list(proposal.get("sequence") or []),
            effective_pins + fixed_schedulable_rows,
        )
        result = sequence.validate_sequence(
            proposal, assigned, seq_anchored + pinned_walls, body.config,
            time_frame=frame.as_dict(),
            optional_items=blocks,
            planning_config_fingerprint=body.planning_config_fingerprint,
        )
        if not result.ok:
            # T12 qualification (2026-07-26): this discarded the proposal
            # outright. By here the SDK call has ALREADY been made and the
            # billed ledger ALREADY charged (judgment.py spends per real
            # attempt), so throwing the body away means the user pays for a
            # plan they never get to see. The rejection still stands — a hard
            # failure must never be presentable as a committable plan — but
            # the proposal rides along so the client can show it read-only,
            # marked rejected, and the user can at least read what their call
            # bought and re-enter it by hand.
            raise HTTPException(
                status_code=422,
                detail={"message": "sequence validation failed",
                        "hard_errors": result.hard_errors,
                        "rejected_proposal": proposal},
            )
        proposal["warnings"] = result.warnings + block_notes
        # Backdrop zone rows append AFTER validation — permeable framing only.
        proposal["sequence"] = list(proposal.get("sequence") or []) + zone_rows
        if qt_contents:
            proposal["qt_contents"] = qt_contents
        # T27: expose + persist the EFFECTIVE pin set (client + auto) so the
        # client adopts server-derived recurring pins and later
        # /validate-sequence /commit snapshot checks line up.
        proposal["pinned_rows"] = effective_pins
        runstate.update_runstate(
            vault, today,
            {"overlap_grants": list(proposal.get("overlap_grants") or []),
             "pinned_rows": effective_pins,
             "planning_config_fingerprint": body.planning_config_fingerprint},
        )
        return proposal

    @app.post("/validate-sequence", dependencies=[Depends(require_token)])
    def post_validate_sequence(body: ValidateSequenceRequest) -> dict:
        """T16: deterministic re-validation of a (possibly drag-adjusted)
        sequence against the FROZEN sequence.validate_sequence — no judgment
        call, no writes. Returns {ok, hard_errors, warnings} verbatim so the
        timeline view can render soft warnings (amber, non-blocking) vs hard
        errors (red, gate the commit) without re-proposing. The belt's belt:
        same validator the /commit path trusts, exposed for per-drag feedback."""
        vault = resolve_vault_root()
        today = gather.effective_date(datetime.now())
        if body.pinned_rows or body.overlap_grants or body.planning_config_fingerprint:
            snapshot = _read_today_runstate(vault, today)
            if (
                snapshot.get("pinned_rows", []) != body.pinned_rows
                or snapshot.get("overlap_grants", []) != body.overlap_grants
                or snapshot.get("planning_config_fingerprint", "")
                    != body.planning_config_fingerprint
            ):
                return {"ok": False,
                        "hard_errors": ["planning snapshot is stale"],
                        "warnings": []}
        day_setup = {k: v for k, v in _read_today_runstate(vault, today).items()
                     if k in _DAY_SETUP_KEYS and v not in ("", None)}
        defaults = dict((body.config or {}).get("Defaults") or {})
        # T28: mirror /sequence — dismissed calendar rows free their interval.
        anchored_effective = shadow.apply_calendar_participation(
            body.anchored_blocks, day_setup)
        frame = time_engine.compute_time_frame(
            now=datetime.now(),
            config_eod=time_engine.to_hhmm(defaults.get("eod")) or "23:59",
            round_to_minutes=int(defaults.get("anchor.round_to_minutes") or 15),
            busy_events=[{"start": time_engine.to_hhmm(b.get("Start")),
                          "title": b.get("Block")}
                         for b in anchored_effective
                         if b.get("source") == "calendar" and b.get("Start")
                         and not b.get("skip_today")],
            anchor_override=time_engine.to_hhmm(day_setup.get("anchor")),
            eod_override=time_engine.to_hhmm(day_setup.get("eod")),
        )
        # Mirror /sequence's schedulable-block injection (T5) so Minting/QT/
        # Shivery rows in a proposal don't validate as foreign extras here.
        blocks, _zone_rows, _notes = external_sources.build_schedulable_blocks(
            body.config or {}, day_setup, today, frame.anchor,
            resolved_day_semantics=getattr(body, "day_semantics", None),
        )
        qt_on = any(b.get("qt") for b in blocks)
        assigned, _qt = external_sources.absorb_quick_tasks(list(body.assigned), qt_on)
        # QT-absorbed items are optional placements: an LLM proposal folds
        # them into the QT block (unplaced), a manual layout places them
        # directly — both must validate.
        absorbed = {str(i.get("name")) for i in body.assigned} - {
            str(i.get("name")) for i in assigned}
        blocks = external_sources.disambiguate_names(assigned, blocks)
        # Injected blocks are OPTIONAL here (unlike /sequence, which requires
        # its own injections): the user may have dragged them off the plan.
        optional = absorbed | {str(b.get("name")) for b in blocks}
        pin_errors = sequence.validate_pinned_rows(body.pinned_rows, body.assigned)
        if pin_errors:
            return {"ok": False, "hard_errors": pin_errors, "warnings": []}
        expected_pins = {str(pin.get("id")): pin for pin in body.pinned_rows}
        actual_pins = {
            str(row.get("id")): row for row in body.sequence
            if str(row.get("id")) in expected_pins
        }
        if actual_pins != expected_pins:
            return {"ok": False,
                    "hard_errors": ["pinned rows changed from immutable snapshot"],
                    "warnings": []}
        pinned_walls = [
            {"Block": pin["id"], "Type": "hard", "Start": pin["start"],
             "End": pin["end"], "pinned": True}
            for pin in body.pinned_rows
        ]
        result = sequence.validate_sequence(
            {"sequence": body.sequence, "overlap_grants": body.overlap_grants}, assigned,
            _judged_anchored(body.anchored_blocks, day_setup) + pinned_walls,
            body.config, time_frame=frame.as_dict(), optional_ids=optional,
            optional_items=blocks,
            planning_config_fingerprint=body.planning_config_fingerprint,
        )
        return result.as_dict()

    @app.post("/commit", dependencies=[Depends(require_token)])
    def post_commit(
        mode: str | None = None, resume: bool = False, body: CommitRequest | None = None
    ) -> Any:
        """T13: shadow-mode preview. T15: ``mode=live`` real commit.

        ``mode=shadow`` computes the full plan_manifest + live diff and
        WRITES NOTHING. A bare call with no ``mode`` keeps 501ing — preserved
        for backward compatibility with the pre-T13 stub contract (some
        caller may still probe the old bare-POST shape). ``mode=live`` now
        actually writes, via ``commit.plan_writes`` + ``orchestrate.run_orchestrated``.
        """
        if mode is None:
            raise HTTPException(status_code=501, detail="not implemented until T14/T15 (commit writers)")

        if body is not None and (
            body.pinned_rows or body.overlap_grants
            or body.planning_config_fingerprint
        ):
            vault = resolve_vault_root()
            today = gather.effective_date(datetime.now())
            snapshot = _read_today_runstate(vault, today)
            if (
                snapshot.get("pinned_rows", []) != body.pinned_rows
                or snapshot.get("overlap_grants", []) != body.overlap_grants
                or snapshot.get("planning_config_fingerprint", "")
                    != body.planning_config_fingerprint
            ):
                raise HTTPException(
                    status_code=409,
                    detail="planning snapshot is stale; regenerate before commit",
                )
            sequence_rows = (
                body.sequence.get("sequence", [])
                if isinstance(body.sequence, dict) else []
            )
            expected_pins = {str(pin.get("id")): pin for pin in body.pinned_rows}
            actual_pins = {
                str(row.get("id")): row for row in sequence_rows
                if str(row.get("id")) in expected_pins
            }
            if actual_pins != expected_pins:
                raise HTTPException(
                    status_code=409,
                    detail="pinned rows changed from immutable snapshot",
                )

        if mode == "live":
            if body is None or body.digest is None or body.sequence is None:
                raise HTTPException(
                    status_code=400, detail="live commit requires a body with 'digest' and 'sequence'"
                )
            # G25: single-flight guard. Check-before-write idempotency runs
            # against a once-per-run live snapshot, so two RACING live commits
            # both classify as create and double-write Todoist/calendar. Held
            # for the whole write path; second caller gets an immediate 409.
            if not app.state.live_commit_lock.acquire(blocking=False):
                raise HTTPException(
                    status_code=409,
                    detail="live commit already in flight — retry after it returns",
                )
            try:
                return _run_live_commit(body, resume)
            finally:
                app.state.live_commit_lock.release()

        if mode != "shadow":
            raise HTTPException(status_code=400, detail=f"unknown commit mode: {mode!r}")

        if body is None or body.digest is None or body.sequence is None:
            raise HTTPException(
                status_code=400, detail="shadow commit requires a body with 'digest' and 'sequence'"
            )

        vault = resolve_vault_root()
        shadow_today = gather.effective_date(datetime.now())
        shadow_day_setup = {
            k: v for k, v in _read_today_runstate(vault, shadow_today).items()
            if k in _DAY_SETUP_KEYS and v not in ("", None)
        }
        shadow_config = shadow.apply_day_setup(body.config or {}, shadow_day_setup)
        # T19: server-authoritative Live selection (runstate override → auto),
        # so the shadow preview reroutes Live → Todoist without trusting the
        # client echo. Shadow never writes the history log.
        shadow_config = _ensure_micro_adventure(shadow_config, vault, shadow_today)
        manifest = shadow.build_plan_manifest(
            body.digest, body.sequence, shadow_config,
            time_frame=_frame_for_writes(body.config, shadow_day_setup))

        config_for_state: Any = shadow_config
        try:
            live_state = shadow.gather_live_state(config_for_state, vault)
        except shadow.ShadowStateError as exc:
            raise HTTPException(status_code=502, detail=f"shadow state error: {exc}") from exc

        diff = shadow.diff_against_live(manifest, live_state)
        return diff.as_dict()

    def _frame_for_writes(config: dict[str, Any] | None,
                          day_setup: dict[str, Any]) -> dict[str, Any]:
        """Day frame handed to ``shadow.build_plan_manifest`` so anchored
        blocks that already elapsed stay out of the write contract (T12
        qualification, 2026-07-26 — a 21:45 run back-dated five create-events).

        Deliberately omits calendar busy events: those only ever push the
        anchor LATER, so leaving them out can only make the filter more
        permissive. It will never drop a block that is still ahead.
        """
        defaults: dict[str, Any] = dict((config or {}).get("Defaults") or {})
        frame = time_engine.compute_time_frame(
            now=datetime.now(),
            config_eod=time_engine.to_hhmm(defaults.get("eod")) or "23:59",
            round_to_minutes=int(defaults.get("anchor.round_to_minutes") or 15),
            anchor_override=time_engine.to_hhmm(day_setup.get("anchor")),
            eod_override=time_engine.to_hhmm(day_setup.get("eod")),
        )
        return frame.as_dict()

    def _run_live_commit(body: CommitRequest, resume: bool) -> Any:
        """T15 live-commit write path — always runs under the G25 lock."""
        vault = resolve_vault_root()
        today = gather.effective_date(datetime.now())
        # FEEDBACK-24: the external write path fails closed until Day Setup is
        # explicitly confirmed for today (409, actionable). The client-facing
        # wizard already lands on setup, but a direct or stale client must not
        # be able to write a plan over an unconfirmed day.
        _require_day_setup(vault, today, "committing")
        # T8: Day Setup state (anchored overrides, re_included, captures)
        # flows into the manifest via config
        live_day_setup = {
            k: v for k, v in _read_today_runstate(vault, today).items()
            if k in _DAY_SETUP_KEYS and v not in ("", None)
        }
        config: dict[str, Any] = shadow.apply_day_setup(body.config or {}, live_day_setup)
        # T19: server-authoritative Live selection (runstate override → auto).
        config = _ensure_micro_adventure(config, vault, today)
        manifest = shadow.build_plan_manifest(
            body.digest, body.sequence, config,
            time_frame=_frame_for_writes(body.config, live_day_setup))
        try:
            live_state = shadow.gather_live_state(config, vault)
        except shadow.ShadowStateError as exc:
            raise HTTPException(status_code=502, detail=f"shadow state error: {exc}") from exc
        diff = shadow.diff_against_live(manifest, live_state)

        injected_todoist: Any = None
        store: Any = None
        token: str | None = None
        has_calendar_rows = any(e.manifest.system == "calendar" for e in diff.entries)
        if app.state.build_commit_clients:
            injected_todoist, store = app.state.build_commit_clients(vault, config)
        else:
            token = shadow.todoist_client.load_token(shadow.TOKEN_ENV_PATH)
            if has_calendar_rows:
                try:
                    store = calendar_bridge.shared_store()
                except Exception:  # noqa: BLE001 — EventKit init degrades to store=None
                    store = None

        # T14 Option A: calendar rows must see a writable calendar before any
        # planning — a degraded store (2026-07-23: a second instance saw zero
        # calendars while the GET store was healthy) fails closed here, with
        # the reason, before any write client is touched.
        if has_calendar_rows and not calendar_bridge.has_writable_calendar(store):
            raise HTTPException(
                status_code=422,
                detail="plan refused: calendar rows present but the EventKit "
                       "store sees no writable calendar (grant missing or store "
                       "degraded) — nothing was written",
            )

        resolved = (
            calendar_bridge.resolve_titles_to_ids(
                calendar_bridge.normalize_title_map(
                    config.get("Calendar Titles")
                    or config.get("calendar_ids")
                    or config.get("Calendar IDs")
                ),
                store.calendars(),
            )[0]
            if store
            else {}
        )
        try:
            intents = commit.plan_writes(diff, resolved, config, today)
        except commit.CommitPlanError as exc:
            raise HTTPException(status_code=422, detail=f"plan refused: {exc}") from exc

        plan_body = _render_plan_body(body.sequence)
        if app.state.build_commit_clients:
            report = orchestrate.run_orchestrated(
                intents, todoist=injected_todoist, store=store, vault_root=vault,
                plan_body=plan_body, today=today, resume=resume,
            )
        else:
            with shadow.todoist_client.TodoistClient(token) as todoist:
                report = orchestrate.run_orchestrated(
                    intents, todoist=todoist, store=store, vault_root=vault,
                    plan_body=plan_body, today=today, resume=resume,
                )
        # T19: the authorized commit is the ONLY history-consuming surface —
        # exactly one idempotent log upsert, after every surface reports ok.
        _append_micro_adventure_history(report, config, intents, vault, today)
        return report

    # -- runtime item actions (T20) -------------------------------------------

    def _runtime_clients() -> tuple[Any, Any, bool]:
        """(todoist, store, owns_todoist) — injected builder first (tests),
        else live clients; each surface degrades to None and the verb
        planner fails closed per surface before any write."""
        vault = resolve_vault_root()
        if app.state.build_commit_clients:
            todoist_c, store = app.state.build_commit_clients(vault, None)
            return todoist_c, store, False
        todoist_c = None
        try:
            token = shadow.todoist_client.load_token(shadow.TOKEN_ENV_PATH)
            todoist_c = shadow.todoist_client.TodoistClient(token)
        except Exception:  # noqa: BLE001 — absence degrades, fail-closed later
            pass
        store = None
        try:
            store = calendar_bridge.shared_store()
        except Exception:  # noqa: BLE001
            pass
        return todoist_c, store, todoist_c is not None

    def _raise_runtime_error(exc: runtime_actions.RuntimeActionError) -> None:
        code = 503 if "surface unavailable" in str(exc) else 422
        raise HTTPException(status_code=code, detail=str(exc)) from exc

    @app.post("/runtime-actions", dependencies=[Depends(require_token)])
    def post_runtime_action(body: RuntimeActionRequest) -> Any:
        vault = resolve_vault_root()
        today = gather.effective_date(datetime.now())
        # FEEDBACK-24: runtime verbs write external state (Todoist/vault) —
        # fail closed until Day Setup is explicitly confirmed for today.
        _require_day_setup(vault, today, "applying runtime actions")
        todoist_c, store, owns = _runtime_clients()
        try:
            return runtime_actions.apply_action(
                vault, today, body.verb, body.target, body.args,
                todoist=todoist_c, store=store,
            )
        except runtime_actions.RuntimeActionError as exc:
            _raise_runtime_error(exc)
        finally:
            if owns:
                todoist_c.close()

    @app.post("/runtime-actions/{action_id}/undo",
              dependencies=[Depends(require_token)])
    def post_runtime_action_undo(action_id: str) -> Any:
        vault = resolve_vault_root()
        today = gather.effective_date(datetime.now())
        # FEEDBACK-24: undo writes external state too — same closed gate.
        _require_day_setup(vault, today, "undoing runtime actions")
        todoist_c, store, owns = _runtime_clients()
        try:
            return runtime_actions.undo_action(
                vault, today, action_id, todoist=todoist_c, store=store)
        except runtime_actions.RuntimeActionError as exc:
            _raise_runtime_error(exc)
        finally:
            if owns:
                todoist_c.close()

    @app.get("/runtime-actions", dependencies=[Depends(require_token)])
    def get_runtime_actions() -> Any:
        vault = resolve_vault_root()
        today = gather.effective_date(datetime.now())
        return runtime_actions.load_journal(vault, today)

    # -- static UI (T10: thin, unstyled config + digest views) ----------------
    if _STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=str(_STATIC_DIR), html=True), name="static")

    return app


def build_real_read_clients(vault: Path, config: dict[str, Any]) -> tuple[Any, Any]:
    """Live read clients for /plan-inputs; each degrades to None (→ a
    source_warnings entry) when the token/EventKit grant is absent."""
    todoist_c = None
    try:
        token = shadow.todoist_client.load_token(shadow.TOKEN_ENV_PATH)
        todoist_c = shadow.todoist_client.TodoistClient(token)
    except Exception:  # noqa: BLE001 — absence degrades, never blocks
        pass
    store = None
    try:
        store = calendar_bridge.shared_store()
    except Exception:  # noqa: BLE001
        pass
    return todoist_c, store


app = create_app()
app.state.build_read_clients = build_real_read_clients


if __name__ == "__main__":
    import uvicorn

    # EventKit grant is per-responsible-process (TCC): a Terminal-run grant
    # attributes to Terminal.app and does NOT carry to a launchd-spawned
    # server (G29 recurrence, 2026-07-16 launchd adoption). Requesting at
    # boot pops the system dialog ONCE for this python binary; thereafter
    # the grant persists across respawns. Best-effort — a denied/headless
    # environment just keeps the loud-degrade warnings.
    try:
        store = calendar_bridge.shared_store()
        if store.auth_status() == "notDetermined":
            granted = store.request_access()
            print(f"EventKit grant requested at boot: granted={granted}")
    except Exception as exc:  # noqa: BLE001 — grant is best-effort at boot
        print(f"EventKit grant request skipped: {exc}")

    print(f"X-TDTB-Token: {app.state.token}")
    uvicorn.run(app, host="127.0.0.1", port=8746)  # localhost-only by contract
