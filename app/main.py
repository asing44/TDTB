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
import subprocess
import sys
import threading
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    ValidationError,
    ValidationInfo,
    field_validator,
)

_STATIC_DIR = Path(__file__).parent / "static"

_GATHER_DIR = str(Path(__file__).parent / "gather")
if _GATHER_DIR not in sys.path:
    sys.path.insert(0, _GATHER_DIR)

import tdtb_gather as gather  # noqa: E402  (path-shimmed import, see inventory.py)

import config_reader  # noqa: E402
import day_semantics  # noqa: E402
import deferrals  # noqa: E402
import duration_memory  # noqa: E402
import runstate  # noqa: E402
import judgment  # noqa: E402
import planning  # noqa: E402
sequence = planning.sequence  # compatibility alias for existing test seams
import shadow  # noqa: E402
import commit  # noqa: E402
import calendar_bridge  # noqa: E402
import runtime_actions  # noqa: E402
import external_sources  # noqa: E402
import micro_adventure  # noqa: E402
import orchestrate  # noqa: E402
import time_engine  # noqa: E402
import capacity as capacity_mod  # noqa: E402
import capacities_settings  # noqa: E402
import capacities_builder  # noqa: E402
import exclusion_settings  # noqa: E402
import tag_exclusions  # noqa: E402

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
    exclusion_policy: tag_exclusions.ExclusionPolicy | None = None,
) -> dict[str, Any]:
    """Two-surface digest per SKILL.md Phase 2/3: Assigned + ranked Suggested.

    ``ignore`` (config `## Ignore List`, T13e) drops matching items from
    every surface: Todoist rows by ``todoist_id``, vault rows by relative
    ``path``, any row by case-insensitive name. The user-editable permanent
    hide list; counts reflect the post-filter sets.

    ``exclusion_policy`` (the app-managed tag exclusion list) runs at ONE
    stage, immediately AFTER the Ignore List and BEFORE assigned sorting, pool
    dedup, ranking, and forgot-list derivation, and filters BOTH surfaces —
    an excluded task must never reach the brief, and a source Assigned marker
    must not override the policy. The stage's observability envelope rides the
    digest as ``exclusion_policy``. Ignore List matches keep their earlier
    attribution; only rows removed at this stage are reported.

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
    exclusion_report: dict[str, Any] | None = None
    if exclusion_policy is not None:
        assigned_items, pool_items, exclusion_report = (
            tag_exclusions.apply_tag_exclusions(
                assigned_items, pool_items, exclusion_policy
            )
        )
    assigned = sorted(assigned_items, key=lambda i: (i.get("name") or "", i.get("path") or ""))
    assigned_paths = {i.get("path") for i in assigned}
    suggestable = [i for i in pool_items if i.get("path") not in assigned_paths]
    suggested = rank_pool(suggestable, today, order, bias)
    unassigned_candidates, stale_assigned = build_forgot_lists(
        assigned, suggested, today, bias)
    digest = {
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
    if exclusion_report is not None:
        digest["exclusion_policy"] = exclusion_report
    return digest


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


def build_digest_index(digest: dict[str, Any]) -> list[dict[str, Any]]:
    """Build the server-owned identity/timing index for a digest.

    Persisted to runstate ``digest_index`` by /plan-inputs so T2's
    staging-phase ``resolve_target`` can map a name the client sends back to
    the source artifacts the SERVER derived. The client never names an id, so
    the T20 property "the app only touches artifacts it derived itself"
    survives the move to pre-commit; only the derivation source changes
    (plan_manifest → digest). Rows with no usable identity are dropped.

    FEEDBACK-25: each row carries ``surface`` ("assigned" or "suggested") so
    the commit eligibility boundary can authorize submitted assigned items
    only from server rows that were actually assigned. Assigned rows are
    indexed first; an identical row appearing on both surfaces keeps the
    assigned role (assigned-first dedupe).

    P4: Todoist timing metadata is retained only from the server-derived
    digest.  The commit boundary uses these fields to recompute native timed
    protection; client pin metadata is never persisted as authorization.
    """
    index: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for surface in ("assigned", "suggested"):
        for row in list(digest.get(surface) or []):
            if not isinstance(row, dict):
                continue
            entry = {
                "name": str(row.get("name") or ""),
                "todoist_id": str(row.get("todoist_id") or ""),
                "path": str(row.get("path") or ""),
                "surface": surface,
            }
            for key in (
                "identity", "source", "blocks", "native_blocks", "duration",
                "duration_minutes", "scheduled_start", "is_recurring",
                "capacities_id", "capacities_space_id", "capacities_structure_id",
                "capacities_completion_supported", "source_fingerprint",
                # Tag-exclusion slice: canonical tag identities are retained
                # on the indexed row so a later staleness guard can re-check
                # the policy that produced this index without a provider read.
                "capacities_tags",
            ):
                if key in row and row.get(key) is not None:
                    entry[key] = row[key]
            # Todoist rows retain their server-native duration provenance in
            # the index so the route/commit boundaries never need the client
            # to supply it. Existing ``native_blocks`` wins; otherwise the
            # trusted server ``blocks`` is recorded (never a client overlay).
            if ("native_blocks" not in entry and "blocks" in entry
                    and (str(row.get("source") or "").strip().casefold() == "todoist"
                         or str(row.get("todoist_id") or "").strip())):
                entry["native_blocks"] = entry["blocks"]
            if not entry["name"] or not (
                entry["todoist_id"] or entry["path"] or entry.get("identity")
            ):
                continue
            key = (entry["name"], entry["todoist_id"], entry["path"])
            if key in seen:
                continue
            seen.add(key)
            index.append(entry)
    return index


_ROUTE_TIMING_FIELDS = frozenset({
    "id", "name", "identity", "todoist_id", "path", "source", "scheduled_start",
    "is_recurring", "duration", "blocks", "duration_minutes",
    "duration_source", "placement_window", "latest_start", "latest_end",
    "zone", "native_blocks", "allow_time_adjustment", "source_fingerprint",
    "capacities_id", "capacities_space_id", "capacities_structure_id",
    "capacities_completion_supported",
})

_ROUTE_NATIVE_CLAIM_FIELDS = frozenset({
    "identity", "source", "scheduled_start", "is_recurring", "native_blocks",
    "allow_time_adjustment", "capacities_id", "capacities_space_id",
    "capacities_structure_id", "capacities_completion_supported",
    "source_fingerprint",
})


def _route_name(value: Any) -> str:
    """Canonical comparison form for a submitted/displayed item name."""
    return " ".join(str(value or "").strip().split()).casefold()


def _route_source_identity(
    row: dict[str, Any],
) -> tuple[str, str] | None:
    """Return a stable source identity, rejecting contradictions."""
    source = str(row.get("source") or "").strip().casefold()
    todoist_id = str(row.get("todoist_id") or "").strip()
    path = str(row.get("path") or "").strip()
    identity = str(row.get("identity") or "").strip()
    todoist_path = path.casefold().startswith("todoist://")
    capacities_path = path.casefold().startswith("capacities://")

    if source == "capacities" or capacities_path:
        if not capacities_path or (identity and not identity.casefold().startswith("capacities:")):
            return None
        return "capacities", identity or path

    if todoist_id:
        if path:
            if not todoist_path:
                return None
            path_id = path[len("todoist://"):].strip()
            if path_id != todoist_id:
                return None
        return "todoist", todoist_id
    if not path:
        return None
    if todoist_path:
        path_id = path[len("todoist://"):].strip()
        return ("todoist", path_id) if path_id else None
    return "vault", path


# Bounded effective-blocks policy: the day runs on 30-minute blocks, so an
# all-day override tops out at 48 blocks (24h). Anything larger (or
# malformed) is refused at the route boundary, never silently accepted.
MAX_EFFECTIVE_BLOCKS = 48


def _validated_blocks_overlay(value: Any) -> float | int | None:
    """Validate a submitted today-only effective-blocks overlay.

    Returns the numeric value (int when integral) for a valid overlay, or
    None when the overlay is absent (no ``blocks`` key / explicit null) so
    callers fall back to the trusted server value. Raises ``ValueError`` for
    malformed or extreme input — bools, non-numbers, non-finite values,
    negatives, and anything above ``MAX_EFFECTIVE_BLOCKS`` (48 blocks = 24h
    under the 30-minute-block policy) are never silently accepted.
    """
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"must be a number, got {type(value).__name__}")
    try:
        numeric = float(value)
    except (OverflowError, ValueError):
        raise ValueError("must be a finite number within range") from None
    if not math.isfinite(numeric):
        raise ValueError("must be a finite number")
    if numeric < 0:
        raise ValueError(f"must be >= 0, got {value!r}")
    if numeric > MAX_EFFECTIVE_BLOCKS:
        raise ValueError(
            f"must be <= {MAX_EFFECTIVE_BLOCKS} blocks (24h), got {value!r}"
        )
    return int(value) if float(value).is_integer() else value


def _canonicalize_route_assigned(
    vault: Path,
    today: date,
    submitted_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Canonicalize assigned rows before proposal or revalidation planning.

    The dated digest index is the only source of source identity and native
    timing for rows that carry a stable identity. The submitted list still
    controls the included subset, and identity-less legacy rows without native
    metadata retain their old today-only shaping. Request-provided native
    timing, source, identity, and opt-in metadata is never carried forward.

    A matched assigned row may carry a validated today-only ``blocks``
    override (including zero) for planning duration; that effective value is
    applied on top of the server-derived fields. Todoist native provenance is
    server-only: ``native_blocks`` (the index value, else the trusted server
    blocks) is retained on the row — never the client overlay — so an
    override cannot unpin a native timed row. Malformed or extreme ``blocks``
    overlays fail closed (the row is refused). Client pins remain untouched
    and are passed to planning for its canonical conflict diagnostics.
    """
    index = runstate.read_digest_index(vault, today)
    server_rows = [
        row for row in index
        if isinstance(row, dict)
        and str(row.get("surface") or "").casefold() == "assigned"
        and _route_name(row.get("name"))
        and _route_source_identity(row) is not None
    ]
    named_server_rows = {
        _route_name(row.get("name"))
        for row in index
        if isinstance(row, dict)
        and _route_name(row.get("name"))
        and _route_source_identity(row) is not None
    }

    def _submitted_name(row: dict[str, Any]) -> str:
        return str(row.get("name") or row.get("id") or "").strip()

    def _has_stable_claim(row: dict[str, Any]) -> bool:
        return bool(
            str(row.get("todoist_id") or "").strip()
            or str(row.get("path") or "").strip()
            or str(row.get("identity") or "").strip()
        )

    def _has_native_claim(row: dict[str, Any]) -> bool:
        return bool(
            _has_stable_claim(row)
            or any(key in row for key in _ROUTE_NATIVE_CLAIM_FIELDS)
            # An identity-less explicit all-day claim is still timing
            # metadata.  Do not let blocks==0 erase a native timed pin;
            # positive blocks remain valid legacy local planning shape.
            or row.get("blocks") == 0
        )

    def _match(row: dict[str, Any]) -> dict[str, Any] | None:
        name = _route_name(_submitted_name(row))
        identity = _route_source_identity(row)
        if not name or identity is None:
            return None
        matches = [
            candidate for candidate in server_rows
            if _route_name(candidate.get("name")) == name
            and _route_source_identity(candidate) == identity
        ]
        return matches[0] if len(matches) == 1 else None

    refused: list[str] = []
    canonical: list[dict[str, Any]] = []
    for row in submitted_rows:
        if not isinstance(row, dict):
            refused.append(repr(row))
            continue
        name = _submitted_name(row)
        match = _match(row)
        if match is not None:
            # Keep non-timing planning metadata (labels, relationships, and
            # similar today-only shape), but make every timing/source field
            # server-owned. Missing server fields are removed rather than
            # filled from the request.
            projected = {
                key: value for key, value in row.items()
                if key not in _ROUTE_TIMING_FIELDS
            }
            projected["id"] = str(match.get("name") or "").strip()
            projected["name"] = str(match.get("name") or "").strip()
            for key in _ROUTE_TIMING_FIELDS - {"id", "name", "allow_time_adjustment"}:
                if key in match and match.get(key) is not None:
                    projected[key] = match[key]
            # Todoist native provenance is server-owned: ``native_blocks``
            # (the index value, else the trusted server blocks) — never the
            # submitted overlay — so a today-only override cannot unpin a
            # native timed row.
            match_identity = _route_source_identity(match)
            if (
                match_identity is not None
                and match_identity[0] == "todoist"
                and "native_blocks" not in projected
                and match.get("blocks") is not None
            ):
                projected["native_blocks"] = match.get("blocks")
            # A validated effective-blocks overlay is the planning duration;
            # absent blocks falls back to the server value just copied above.
            try:
                overlay = _validated_blocks_overlay(row.get("blocks"))
            except ValueError as exc:
                refused.append(f"{name or '<unnamed>'} (blocks: {exc})")
                continue
            if overlay is not None:
                projected["blocks"] = overlay
            # Only the exact literal True from this matched Todoist row is an
            # authorization. False, strings, and pins from other rows vanish.
            if (
                match_identity is not None
                and match_identity[0] == "todoist"
                and row.get("allow_time_adjustment") is True
            ):
                projected["allow_time_adjustment"] = True
            canonical.append(projected)
            continue

        # A name-only row cannot fall back to the legacy lane when the server
        # knows a stable item with that name: doing so would authorize by
        # display name and could omit native protection. Stable/native claims
        # also fail closed when today's index is missing or only suggested.
        if _has_native_claim(row) or _route_name(name) in named_server_rows:
            refused.append(name or "<unnamed>")
            continue

        # Legacy route callers use identity-less rows for today-only shaping.
        # Keep that compatibility, while discarding any opt-in or timing keys
        # that could be smuggled through an identity-less row — and never
        # accept a malformed/extreme blocks value through that lane either.
        try:
            _validated_blocks_overlay(row.get("blocks"))
        except ValueError as exc:
            refused.append(f"{name or '<unnamed>'} (blocks: {exc})")
            continue
        projected = {
            key: value for key, value in row.items()
            if key not in _ROUTE_NATIVE_CLAIM_FIELDS
            and key not in {"todoist_id", "path"}
        }
        canonical.append(projected)

    if refused:
        names = ", ".join(repr(name) for name in refused)
        raise HTTPException(
            status_code=422,
            detail=(
                "assigned rows are not server-authorized for today: "
                f"{names}"
            ),
        )
    return canonical


def _available_capacities_structures(vault: Path) -> list[str]:
    """Advisory list of configured Capacities structure ids from the vault-local
    source mapping record (``capacities_builder``).

    This is UI metadata for the settings drawer, so it is deliberately
    fail-soft: a missing, malformed, or unreadable record yields ``[]`` and
    never changes the settings route's own error behaviour. The builder still
    reports a malformed record loudly when an adapter is built. No adapter is
    constructed, no credential is read, and no provider is contacted here.
    """
    try:
        record = capacities_builder.read_source(vault)
    except (capacities_builder.CapacitiesSourceStoreError, OSError) as exc:
        print(f"capacities source read failed: {exc}", file=sys.stderr)
        return []
    if record is None:
        return []
    return sorted({structure.structure_id for structure in record.structures})


def _exclusion_policy_or_block(vault: Path) -> tag_exclusions.ExclusionPolicy:
    """Read the tag-exclusion policy for a planning surface, failing closed.

    Malformed or unreadable storage blocks planning with a structured 503
    rather than silently evaluating an empty policy — an empty brief would be
    indistinguishable from "nothing is excluded". The settings routes keep
    their own storage error semantics; this is the planning boundary only.
    """
    try:
        read = exclusion_settings.read_settings(vault)
    except exclusion_settings.ExclusionSettingsFormatError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "exclusion_settings_storage_error",
                "message": (
                    "Tag exclusion settings storage is malformed or "
                    "unsupported; planning is blocked until it is repaired."
                ),
            },
        ) from exc
    except (exclusion_settings.ExclusionSettingsStoreError, OSError) as exc:
        # Bounded client-safe message — no absolute vault/cache path.
        print(f"exclusion settings read failed: {exc}", file=sys.stderr)
        raise HTTPException(
            status_code=503,
            detail={
                "code": "exclusion_settings_storage_error",
                "message": (
                    "Tag exclusion settings storage could not be read; "
                    "planning is blocked until it is repaired."
                ),
            },
        ) from exc
    return tag_exclusions.ExclusionPolicy.from_read(read)


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
    day_semantics: dict[str, Any] = Field(default_factory=dict)
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


class DurationMemoryRequest(BaseModel):
    """/duration-memory/reset body: one canonical source identity."""

    identity: str


class DurationSaveRequest(BaseModel):
    """/duration-memory/save body (FT-01): one canonical identity + strict
    minutes.

    ``minutes`` is a ``StrictInt`` so bools, floats/fractions, and strings
    are rejected at the JSON boundary; the grid rule (integer >= 0 divisible
    by 5) is validated here and re-checked by ``duration_memory.save_memory``
    before any cache access — a rejected value never mutates the cache."""

    identity: str
    minutes: StrictInt

    @field_validator("minutes")
    @classmethod
    def _minutes_grid(cls, value: int) -> int:
        if isinstance(value, bool):
            raise ValueError("minutes must be an integer")
        if value < 0 or value % duration_memory.DURATION_STEP_MINUTES != 0:
            raise ValueError(
                "minutes must be a nonnegative integer "
                f"divisible by {duration_memory.DURATION_STEP_MINUTES}"
            )
        return value


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


class CapacitiesNativeTaskAutoRequest(BaseModel):
    """Editable native ``RootTask``/``Task`` Auto policy for the save body.

    ``StrictBool`` rejects 0/1 and strings; the horizon is a ``StrictInt`` so
    bools and floats are rejected at the JSON boundary, with the nonnegative
    rule checked here and re-checked by ``capacities_settings`` before any file
    access."""

    model_config = ConfigDict(extra="forbid")

    active_enabled: StrictBool
    due_enabled: StrictBool
    deadline_enabled: StrictBool
    deadline_horizon_days: StrictInt

    @field_validator("deadline_horizon_days")
    @classmethod
    def _horizon_nonnegative(cls, value: int) -> int:
        if value < 0:
            raise ValueError("deadline_horizon_days must be a nonnegative integer")
        return value


class CapacitiesSettingsSaveRequest(BaseModel):
    """Full-replacement body for POST /settings/capacities/save.

    ``expected_revision`` drives the server-side stale-write check; ``version``
    and the new ``revision`` are server-owned and deliberately absent. The
    ``excluded`` object mirrors the persisted shape — canonical stable
    Capacities identities mapped to ``true`` — and each key is validated by the
    same parser the evaluator uses. The two additive admission inputs are
    lists of unique non-empty strings on the wire; each is validated by the
    same helper the store uses on load."""

    model_config = ConfigDict(extra="forbid")

    expected_revision: StrictInt
    native_task_auto: CapacitiesNativeTaskAutoRequest
    excluded: dict[str, StrictBool]
    #: Optional Active-enabled custom structure ids. Omission means full
    #: replacement to the empty set, consistent with the full-replacement
    #: contract. Keys are validated as non-empty, whitespace-free ids.
    active_structures: dict[str, StrictBool] = Field(default_factory=dict)
    #: Configured native task structures. A plain list on the wire (unlike
    #: ``active_structures``); omission means full replacement to the
    #: documented store default (the built-in native task structures) and an
    #: explicit empty list is a legitimate replacement.
    native_task_structures: list[StrictStr] = Field(
        default_factory=lambda: sorted(capacities_settings.NATIVE_TASK_STRUCTURES)
    )
    #: Status values satisfying the native status condition, stored exactly as
    #: given. Omission means full replacement to the documented store default
    #: (the single ``active`` status); an explicit empty list is legitimate.
    active_statuses: list[StrictStr] = Field(
        default_factory=lambda: sorted(capacities_settings.DEFAULT_ACTIVE_STATUSES)
    )
    #: Settings-declared source assignment: ``{structure_id: property_id}``.
    #: The property id is a raw, opaque Capacities property id whose boolean
    #: ``true`` means "assigned at TDTB level". Omission means full
    #: replacement to the empty map — no override anywhere, so each structure
    #: keeps the source mapping's own assignment declaration.
    assigned_structures: dict[str, StrictStr] = Field(default_factory=dict)

    @field_validator("expected_revision")
    @classmethod
    def _revision_nonnegative(cls, value: int) -> int:
        if value < 0:
            raise ValueError("expected_revision must be a nonnegative integer")
        return value

    @field_validator("excluded")
    @classmethod
    def _excluded_canonical(cls, value: dict[str, bool]) -> dict[str, bool]:
        for identity, flag in value.items():
            try:
                capacities_settings.canonical_exclusion_identity(identity)
            except ValueError as exc:
                raise ValueError(
                    f"invalid Capacities exclusion identity: {identity!r}"
                ) from exc
            if flag is not True:
                raise ValueError("exclusion flags must be true")
        return value

    @field_validator("active_structures")
    @classmethod
    def _active_structures_valid(cls, value: dict[str, bool]) -> dict[str, bool]:
        for structure_id, flag in value.items():
            try:
                capacities_settings.canonical_active_structure_id(structure_id)
            except ValueError as exc:
                raise ValueError(
                    f"invalid Capacities active structure id: {structure_id!r}"
                ) from exc
            if flag is not True:
                raise ValueError("active structure flags must be true")
        return value

    @field_validator("assigned_structures")
    @classmethod
    def _assigned_structures_valid(
        cls, value: dict[str, str]
    ) -> dict[str, str]:
        # Apply the same strict parser the store uses before any file access,
        # so a malformed declaration is a 422 here instead of a 500 raised
        # deep inside ``save_settings``.
        try:
            return capacities_settings.canonical_assignment_declarations(value)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc

    @field_validator("native_task_structures", "active_statuses")
    @classmethod
    def _admission_values_valid(
        cls, value: list[str], info: ValidationInfo
    ) -> list[str]:
        # Mirror the store's strictness exactly (unique non-empty strings) so a
        # malformed payload fails here — before any file access — instead of
        # raising deep inside ``save_settings``.
        try:
            return list(
                capacities_settings.canonical_unique_admission_values(
                    value, info.field_name
                )
            )
        except ValueError as exc:
            raise ValueError(str(exc)) from exc


class TagExclusionEntryRequest(BaseModel):
    """One stable tag identity in the tag-exclusion save body.

    ``source`` is a closed literal, the space id must be a non-empty
    whitespace-free string, and ``tag_id`` must be a canonical UUID — the
    same strictness the store re-checks before any file access. Titles and
    ``#``-prefixed display names are never identities and never appear here.
    """

    model_config = ConfigDict(extra="forbid")

    source: Literal["capacities"]
    space_id: str
    tag_id: str

    @field_validator("space_id")
    @classmethod
    def _space_valid(cls, value: str) -> str:
        try:
            return exclusion_settings.canonical_space_id(value)
        except ValueError as exc:
            raise ValueError(f"invalid exclusion space id: {value!r}") from exc

    @field_validator("tag_id")
    @classmethod
    def _tag_valid(cls, value: str) -> str:
        try:
            return exclusion_settings.canonical_tag_id(value)
        except ValueError as exc:
            raise ValueError(f"invalid exclusion tag id: {value!r}") from exc


class TagExclusionDimensionsRequest(BaseModel):
    """The exclusion dimension object for the save body.

    Closed on purpose: the only supported dimension today is ``tags``. An
    unknown dimension (``labels``, ...) is rejected here rather than silently
    accepted — adding one later must be an explicit schema change.
    """

    model_config = ConfigDict(extra="forbid")

    tags: list[TagExclusionEntryRequest]

    @field_validator("tags")
    @classmethod
    def _unique(
        cls, value: list[TagExclusionEntryRequest]
    ) -> list[TagExclusionEntryRequest]:
        seen: set[tuple[str, str, str]] = set()
        for entry in value:
            key = (entry.source, entry.space_id, entry.tag_id)
            if key in seen:
                raise ValueError("duplicate tag exclusion identity")
            seen.add(key)
        return value


class ExclusionSettingsSaveRequest(BaseModel):
    """Full-replacement body for POST /settings/exclusions/save.

    ``expected_revision`` drives the server-side stale-write check; ``version``
    and the new ``revision`` are server-owned and deliberately absent."""

    model_config = ConfigDict(extra="forbid")

    expected_revision: StrictInt
    exclusions: TagExclusionDimensionsRequest

    @field_validator("expected_revision")
    @classmethod
    def _revision_nonnegative(cls, value: int) -> int:
        if value < 0:
            raise ValueError("expected_revision must be a nonnegative integer")
        return value


class CapacitiesSourceSaveRequest(BaseModel):
    """Full-replacement body for POST /settings/capacities/source/save.

    ``version`` and ``revision`` are server-owned and deliberately absent;
    ``expected_revision`` drives the locked stale-write check. The top level
    is closed and strictly typed; each structure row is decoded by
    ``capacities_builder``'s strict row decoder — the same one the file store
    uses — so the accepted request shape cannot drift from the stored
    shape."""

    model_config = ConfigDict(extra="forbid")

    expected_revision: StrictInt
    space_id: StrictStr
    #: Raw structure rows; decoded by the store's own strict row decoder
    #: (``capacities_builder.decode_source_payload``), never re-modeled here.
    structures: list[Any]

    @field_validator("expected_revision")
    @classmethod
    def _revision_nonnegative(cls, value: int) -> int:
        if value < 0:
            raise ValueError("expected_revision must be a nonnegative integer")
        return value


def _capacities_source_invalid_body() -> HTTPException:
    """Bounded 422 for an invalid Capacities source save body.

    The fixed message never echoes parser text, a validation detail, a key
    name, a payload value, or a path."""
    return HTTPException(
        status_code=422,
        detail={
            "code": "capacities_source_validation_error",
            "message": "Capacities source mapping payload is invalid.",
        },
    )


async def _capacities_source_save_request(
    request: Request,
) -> CapacitiesSourceSaveRequest:
    """Strictly parse and validate the source-save body, or answer 422.

    The body is parsed here rather than as an endpoint parameter because the
    request must reject duplicate JSON keys — FastAPI's default JSON parse
    silently keeps the last occurrence — and because every invalid body must
    answer with the fixed ``{detail: {code, message}}`` shape instead of
    FastAPI's validation-error list."""
    try:
        payload = capacities_builder.parse_source_json(await request.body())
        return CapacitiesSourceSaveRequest.model_validate(payload)
    except (ValueError, ValidationError) as exc:
        raise _capacities_source_invalid_body() from exc


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


def _authoritative_day_semantics(
    config: dict[str, Any] | None,
    day_setup: dict[str, Any],
    today: date,
) -> dict[str, Any]:
    """Recompute day semantics from request config and server state.

    Sequence clients may echo stale or partial ``day_semantics``. The request
    config plus today's persisted setup are the authoritative inputs; direct-
    section compatibility keeps this route independent of reader internals.
    """
    return day_semantics.resolve_day_contract(
        config or {}, "", today, dated_overrides=day_setup,
    )


def _normalize_route_day_setup(
    config: dict[str, Any],
    day_setup: dict[str, Any],
    today: date,
    resolved_day_semantics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Canonicalize dated Mint state before any route emits or validates it."""
    return external_sources.normalize_mint_day_setup(
        config, day_setup, today, resolved_day_semantics,
    )


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
    today: date | None = None,
) -> tuple[time_engine.TimeFrame, capacity_mod.Capacity]:
    """Shared time-frame + 6-segment capacity assembly (ui-revamp T2).

    The single computation path behind /plan-inputs and /capacity-preview —
    ``config`` must already have Day Setup applied (shadow.apply_day_setup).
    Buffering default is 'minimal' (SKILL.md 397/797); the JS 'standard'
    default was the G27 divergence.
    """
    defaults: dict[str, Any] = dict(config.get("Defaults") or {})
    effective_now = now or datetime.now()
    frame = time_engine.compute_time_frame(
        now=effective_now,
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
    effective_today = today or effective_now.date()
    # Normalize persisted/legacy session selections at the capacity boundary
    # too. This keeps the capacity amount identical to the schedulable rows
    # even when the caller supplied stale IDs or a legacy all-session list.
    day_setup = _normalize_route_day_setup(
        config, day_setup, effective_today, resolved_day_semantics,
    )
    sched = dict(day_setup.get("schedulable") or {})
    # Mint is reserved by the resolved integer-minute allotment, independent
    # of the legacy schedulable row. Never count canonical Minting twice.
    # Mint rows and capacity use the same active/capped schedule. Do not
    # independently rebuild this from ``or 0``: absent semantics mean the
    # healthy default, while zero and default-off days remain real disables.
    schedule = external_sources.mint_schedule(
        config, day_setup, effective_today, frame.anchor,
        resolved_day_semantics,
    )
    allotted_work_blk = schedule.active_blocks
    # Capacity callers may have an explicit semantic allotment without any
    # configured Trinoor placement windows. Preserve that abstract work
    # reservation, but leave row generation off because there is no concrete
    # window in which to place Mint.
    minting_setup = (sched.get("minting") or {})
    if (
        allotted_work_blk == 0
        and schedule.effective_minutes > 0
        and not external_sources._trinoor_slots(config)
        and (
            effective_today.weekday() < 5
            or schedule.explicit_on
        )
        and minting_setup.get("on") is not False
    ):
        allotted_work_blk = schedule.effective_minutes / external_sources.MINT_SESSION_MINUTES
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

    # -----------------------------------------------------------------------
    # GET /version — tokenless, pure read-only source/runtime fingerprint
    # -----------------------------------------------------------------------

    _GIT_ID_RE = re.compile(r"^[0-9a-f]{40}$")
    _COCKPIT_INDEX_ASSET_RE = re.compile(r"assets/index-[A-Za-z0-9_-]+\.js")

    def _git_head_identity(repo_root: Path) -> tuple[str, str] | None:
        """(commit, tree) object IDs at the repo HEAD, or None when the
        running process has no resolvable Git identity (non-repo checkout,
        unborn HEAD, missing git binary). ``git rev-parse`` handles worktree
        gitdir indirection. Pure read of local repo state — no writes."""
        try:
            commit = subprocess.run(
                ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
                capture_output=True, text=True, check=True, timeout=10,
            ).stdout.strip()
            tree = subprocess.run(
                ["git", "-C", str(repo_root), "rev-parse", "HEAD^{tree}"],
                capture_output=True, text=True, check=True, timeout=10,
            ).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return None
        if not _GIT_ID_RE.fullmatch(commit) or not _GIT_ID_RE.fullmatch(tree):
            return None
        return commit, tree

    def _cockpit_index_asset(cockpit_dir: Path) -> tuple[Path, str] | None:
        """(index_path, single assets/index-*.js reference) from the cockpit
        index.html — or None when the index is missing or the reference is
        not exactly one (fail closed on ambiguity)."""
        index = cockpit_dir / "index.html"
        if not index.is_file():
            return None
        try:
            text = index.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        refs = _COCKPIT_INDEX_ASSET_RE.findall(text)
        if len(refs) != 1:
            return None
        return index, refs[0]

    def _sha256_hex(path: Path) -> str | None:
        try:
            return hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            return None

    def build_version_fingerprint(repo_root: Path,
                                  cockpit_dir: Path) -> dict[str, str] | None:
        """GET /version payload — deterministic read-only fingerprint of the
        repo's committed identity plus the committed cockpit index and its
        single production asset. None on ANY missing/invalid input (fail
        closed without state mutation): no Git identity, no index, a
        non-unique asset reference, or a missing asset file."""
        identity = _git_head_identity(repo_root)
        if identity is None:
            return None
        commit, tree = identity
        indexed = _cockpit_index_asset(cockpit_dir)
        if indexed is None:
            return None
        index_path, asset_ref = indexed
        index_sha = _sha256_hex(index_path)
        asset_sha = _sha256_hex(cockpit_dir / asset_ref)
        if index_sha is None or asset_sha is None:
            return None
        return {
            "status": "ok",
            "source_commit": commit,
            "source_tree": tree,
            "cockpit_index_sha256": index_sha,
            "cockpit_asset": asset_ref,
            "cockpit_asset_sha256": asset_sha,
        }

    app = FastAPI(title="TDTB", docs_url=None, redoc_url=None)
    app.state.vault_root = str(vault_root) if vault_root else None
    app.state.token = secrets.token_urlsafe(32)
    # T02: /version fingerprint paths — the running process's own repo root
    # and the committed cockpit dir. Tests override these to isolated
    # fixtures; the route never touches the vault or any state-bearing path.
    app.state.repo_root = str(Path(__file__).resolve().parent.parent)
    app.state.cockpit_dir = str(_STATIC_DIR / "cockpit")
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
    # Capacities stays opt-in by configuration, not by code: the production
    # builder returns None when no vault-local mapping record exists, so an
    # unconfigured machine sees no Capacities source and no warning, while a
    # configured-but-broken one (malformed record, or a mapping record with no
    # usable credential) raises and surfaces as a source warning instead of
    # silently ingesting nothing. Tests still inject fakes here.
    app.state.build_capacities_adapter = build_real_capacities_adapter
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

    @app.get("/version")
    def get_version() -> dict:
        """T02: tokenless, pure read-only source/runtime fingerprint. Reads
        only Git identity (rev-parse on the running repo) and the committed
        cockpit index + referenced production asset bytes — no judgment,
        ledger, provider, runstate, billing, Todoist, Calendar, Vault, or
        external-source call, and never resolve_vault_root. Missing Git
        identity, index, unique asset reference, or asset → 503 (fail
        closed, no state mutation)."""
        payload = build_version_fingerprint(
            Path(app.state.repo_root), Path(app.state.cockpit_dir)
        )
        if payload is None:
            raise HTTPException(
                status_code=503,
                detail="version fingerprint unavailable (missing Git "
                       "identity, index, or asset)",
            )
        return payload

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

    @app.get("/settings/capacities")
    def get_capacities_settings() -> dict:
        """Tokenless local read of the persisted Capacities assignment policy.

        Reads exactly one vault cache file (or reports the default-enabled
        policy when it is absent) and never builds a Capacities adapter, calls
        a provider, or touches runstate. Malformed/unsupported storage fails
        closed with a bounded error and leaves the bytes untouched."""
        vault = resolve_vault_root()
        try:
            result = capacities_settings.read_settings(vault)
        except (capacities_settings.SettingsStoreError, OSError) as exc:
            # Bounded client-safe message — no absolute vault/cache path.
            print(f"capacities settings read failed: {exc}", file=sys.stderr)
            raise HTTPException(
                status_code=500,
                detail={
                    "code": "capacities_settings_storage_error",
                    "message": (
                        "Capacities settings storage could not be read; "
                        "the existing file was preserved."
                    ),
                },
            ) from exc
        return {
            "settings": result.settings.as_dict(),
            "persisted": result.persisted,
            "available_structures": _available_capacities_structures(vault),
        }

    @app.post("/settings/capacities/save", dependencies=[Depends(require_token)])
    def post_capacities_settings_save(body: CapacitiesSettingsSaveRequest) -> dict:
        """Explicit, full-replacement save of the Capacities assignment policy.

        The complete editable policy is supplied; ``version`` and the new
        ``revision`` are server-owned. The current revision is compared under
        the vault lock: a stale ``expected_revision`` is a 409, malformed
        existing storage is a 409, and a lock/read/write failure is a 500 —
        every failure path preserves the original bytes."""
        vault = resolve_vault_root()
        policy = capacities_settings.NativeTaskAutoPolicy(
            active_enabled=body.native_task_auto.active_enabled,
            due_enabled=body.native_task_auto.due_enabled,
            deadline_enabled=body.native_task_auto.deadline_enabled,
            deadline_horizon_days=body.native_task_auto.deadline_horizon_days,
        )
        try:
            saved = capacities_settings.save_settings(
                vault,
                expected_revision=body.expected_revision,
                native_task_auto=policy,
                excluded=body.excluded.keys(),
                active_structures=body.active_structures.keys(),
                native_task_structures=body.native_task_structures,
                active_statuses=body.active_statuses,
                assigned_structures=body.assigned_structures,
            )
        except capacities_settings.SettingsConflictError as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "capacities_settings_conflict",
                    "message": (
                        "Capacities settings changed since they were read; "
                        "reload and retry."
                    ),
                },
            ) from exc
        except capacities_settings.SettingsFormatError as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "capacities_settings_storage_error",
                    "message": (
                        "Capacities settings storage is malformed or "
                        "unsupported; the existing file was preserved."
                    ),
                },
            ) from exc
        except (capacities_settings.SettingsStoreError, OSError) as exc:
            # Bounded client-safe message — no absolute vault/cache path.
            print(f"capacities settings save failed: {exc}", file=sys.stderr)
            raise HTTPException(
                status_code=500,
                detail={
                    "code": "capacities_settings_storage_error",
                    "message": (
                        "Capacities settings could not be saved; "
                        "the existing file was preserved."
                    ),
                },
            ) from exc
        return {"settings": saved.as_dict(), "persisted": True}

    @app.get("/settings/capacities/source")
    def get_capacities_source() -> dict:
        """Tokenless read of the vault-local Capacities source mapping record.

        Reads exactly one vault cache file and never constructs an adapter,
        reads a credential, or contacts a provider. An absent record answers
        ``{source: null, persisted: false}`` and creates no file; malformed or
        unreadable storage fails closed with a bounded 500 and leaves the
        existing bytes untouched."""
        vault = resolve_vault_root()
        try:
            record = capacities_builder.read_source(vault)
        except (capacities_builder.CapacitiesSourceStoreError, OSError) as exc:
            # Bounded client-safe message — no absolute vault/cache path.
            print(f"capacities source read failed: {exc}", file=sys.stderr)
            raise HTTPException(
                status_code=500,
                detail={
                    "code": "capacities_source_storage_error",
                    "message": (
                        "Capacities source mapping storage could not be read; "
                        "the existing file was preserved."
                    ),
                },
            ) from exc
        return {
            "source": record.as_dict() if record is not None else None,
            "persisted": record is not None,
        }

    @app.post(
        "/settings/capacities/source/save",
        dependencies=[Depends(require_token)],
    )
    def post_capacities_source_save(
        body: CapacitiesSourceSaveRequest = Depends(
            _capacities_source_save_request
        ),
    ) -> dict:
        """Explicit, full-replacement save of the Capacities source mapping.

        The complete editable record is supplied; ``version`` and the new
        ``revision`` are server-owned. The request goes through the store's
        strict decoder and its locked compare-and-replace write: a stale
        ``expected_revision`` is a 409 carrying both revisions, malformed
        existing storage is a 409, and a lock/read/write failure is a 500 —
        every failure path preserves the original bytes."""
        try:
            candidate = capacities_builder.decode_source_payload(
                space_id=body.space_id, structures=body.structures
            )
        except capacities_builder.CapacitiesSourceFormatError as exc:
            raise _capacities_source_invalid_body() from exc
        vault = resolve_vault_root()
        try:
            saved = capacities_builder.save_source(
                vault,
                expected_revision=body.expected_revision,
                space_id=candidate.space_id,
                structures=candidate.structures,
            )
        except capacities_builder.CapacitiesSourceConflictError as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "capacities_source_conflict",
                    "message": "Mapping changed; reload and review.",
                    "expected_revision": exc.expected_revision,
                    "current_revision": exc.current_revision,
                },
            ) from exc
        except capacities_builder.CapacitiesSourceFormatError as exc:
            # Bounded client-safe message — no absolute vault/cache path.
            print(f"capacities source save failed: {exc}", file=sys.stderr)
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "capacities_source_storage_error",
                    "message": (
                        "Capacities source mapping storage is malformed or "
                        "unsupported; the existing file was preserved."
                    ),
                },
            ) from exc
        except (capacities_builder.CapacitiesSourceStoreError, OSError) as exc:
            # Bounded client-safe message — no absolute vault/cache path.
            print(f"capacities source save failed: {exc}", file=sys.stderr)
            raise HTTPException(
                status_code=500,
                detail={
                    "code": "capacities_source_storage_error",
                    "message": (
                        "Capacities source mapping could not be saved; "
                        "the existing file was preserved."
                    ),
                },
            ) from exc
        return {"source": saved.as_dict(), "persisted": True}

    def _tag_catalog(vault: Path) -> dict[str, Any]:
        """Advisory RootTag catalog for the exclusion drawer.

        Degrades instead of hiding saved settings: an unconfigured source, a
        broken mapping/credential, or a provider failure yields a bounded
        status + warning while the settings themselves still answer. The
        catalog is advisory UI metadata — never persisted as policy and never
        derived from filtered digest rows.
        """
        unconfigured: dict[str, Any] = {
            "status": "unconfigured", "space_id": None, "tags": [], "warnings": [],
        }
        build = app.state.build_capacities_adapter
        if build is None:
            return unconfigured
        try:
            adapter = build(vault, {})
        except Exception as exc:  # noqa: BLE001 — advisory boundary degrades
            return {
                "status": "unavailable", "space_id": None, "tags": [],
                "warnings": [f"Capacities tag catalog unavailable ({exc})"],
            }
        if adapter is None:
            return unconfigured
        try:
            list_tags = getattr(adapter, "list_tags", None)
            if not callable(list_tags):
                return {
                    "status": "unavailable", "space_id": None, "tags": [],
                    "warnings": ["Capacities adapter exposes no tag catalog"],
                }
            result = list_tags()
        except Exception as exc:  # noqa: BLE001 — advisory boundary degrades
            return {
                "status": "unavailable", "space_id": None, "tags": [],
                "warnings": [f"Capacities tag catalog unavailable ({exc})"],
            }
        finally:
            close = getattr(adapter, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:  # noqa: BLE001 — cleanup must not mask the read
                    pass
        if not isinstance(result, dict):
            return {
                "status": "unavailable", "space_id": None, "tags": [],
                "warnings": ["Capacities tag catalog returned a malformed result"],
            }
        status = str(result.get("status") or "")
        if status not in {"complete", "partial", "unavailable", "unconfigured"}:
            status = "unavailable"
        tags: list[dict[str, str]] = []
        for row in result.get("tags") or []:
            if (
                isinstance(row, dict)
                and isinstance(row.get("id"), str) and row["id"]
                and isinstance(row.get("title"), str) and row["title"]
            ):
                tags.append({"id": row["id"], "title": row["title"]})
        space_id = result.get("space_id")
        return {
            "status": status,
            "space_id": space_id if isinstance(space_id, str) and space_id else None,
            "tags": tags,
            "warnings": [str(w) for w in (result.get("warnings") or []) if str(w)],
        }

    @app.get("/settings/exclusions")
    def get_exclusion_settings() -> dict:
        """Tokenless local read of the persisted tag-exclusion policy.

        Reads exactly one vault cache file (or reports the empty default when
        it is absent); the advisory tag catalog may build a provider adapter,
        but its failure degrades to a status + warning and never hides the
        saved settings. Malformed/unsupported storage fails closed with a
        bounded 500 and leaves the bytes untouched."""
        vault = resolve_vault_root()
        try:
            result = exclusion_settings.read_settings(vault)
        except (exclusion_settings.ExclusionSettingsStoreError, OSError) as exc:
            # Bounded client-safe message — no absolute vault/cache path.
            print(f"exclusion settings read failed: {exc}", file=sys.stderr)
            raise HTTPException(
                status_code=500,
                detail={
                    "code": "exclusion_settings_storage_error",
                    "message": (
                        "Tag exclusion settings storage could not be read; "
                        "the existing file was preserved."
                    ),
                },
            ) from exc
        return {
            "settings": result.settings.as_dict(),
            "persisted": result.persisted,
            "tag_catalog": _tag_catalog(vault),
        }

    @app.post("/settings/exclusions/save", dependencies=[Depends(require_token)])
    def post_exclusion_settings_save(body: ExclusionSettingsSaveRequest) -> dict:
        """Explicit, full-replacement save of the tag-exclusion policy.

        The complete editable policy is supplied; ``version`` and the new
        ``revision`` are server-owned. The current revision is compared under
        the vault lock: a stale ``expected_revision`` is a 409, malformed
        existing storage is a 409, and a lock/read/write failure is a 500 —
        every failure path preserves the original bytes."""
        vault = resolve_vault_root()
        try:
            saved = exclusion_settings.save_settings(
                vault,
                expected_revision=body.expected_revision,
                exclusions=[
                    exclusion_settings.TagExclusion(
                        source=entry.source,
                        space_id=entry.space_id,
                        tag_id=entry.tag_id,
                    )
                    for entry in body.exclusions.tags
                ],
            )
        except exclusion_settings.ExclusionSettingsConflictError as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "exclusion_settings_conflict",
                    "message": (
                        "Tag exclusion settings changed since they were read; "
                        "reload and retry."
                    ),
                },
            ) from exc
        except exclusion_settings.ExclusionSettingsFormatError as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "exclusion_settings_storage_error",
                    "message": (
                        "Tag exclusion settings storage is malformed or "
                        "unsupported; the existing file was preserved."
                    ),
                },
            ) from exc
        except (exclusion_settings.ExclusionSettingsStoreError, OSError) as exc:
            # Bounded client-safe message — no absolute vault/cache path.
            print(f"exclusion settings save failed: {exc}", file=sys.stderr)
            raise HTTPException(
                status_code=500,
                detail={
                    "code": "exclusion_settings_storage_error",
                    "message": (
                        "Tag exclusion settings could not be saved; "
                        "the existing file was preserved."
                    ),
                },
            ) from exc
        return {"settings": saved.as_dict(), "persisted": True}

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
            # Issue #6: canonical vault config section for disabled calendars,
            # so route-level exclusions become diagnosable via calendar_decisions.
            "calendar_disabled": config.get("Disabled Calendars"),
        }
        build_clients = app.state.build_read_clients or (lambda v, c: (None, None))
        todoist_c, store = build_clients(vault, config)
        capacities_items: list[dict[str, Any]] = []
        w_capacities: list[str] = []
        build_capacities = app.state.build_capacities_adapter
        if build_capacities is not None:
            try:
                capacities_client = build_capacities(vault, config)
            except Exception as exc:  # noqa: BLE001 — source boundary degrades
                capacities_client = None
                w_capacities = [
                    f"Capacities adapter setup failed ({exc}) — source is unavailable"
                ]
            if capacities_client is not None:
                capacities_items, w_capacities = external_sources.fetch_capacities_items(
                    capacities_client, today
                )
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
            busy_blocks, w_cal, calendar_decisions = (
                external_sources.fetch_calendar_decisions(store, ext_cfg, today)
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
        capacities_items = external_sources.disambiguate_names(
            vault_all + t_assigned + t_pool,
            capacities_items,
        )
        exclusion_policy = _exclusion_policy_or_block(vault)
        try:
            digest = build_digest(
                run_data["pool_items"] + t_pool,
                run_data["assigned_items"] + t_assigned + capacities_items,
                today,
                order,
                ignore=(
                    result.config.get_ignore_list() if result.config is not None else None
                ),
                bias=deferrals.bias_map(vault, today),  # T1 defer-with-memory
                exclusion_policy=exclusion_policy,
            )
        except tag_exclusions.TagExclusionBlocked as exc:
            raise HTTPException(status_code=503, detail=exc.diagnostics) from exc

        # T4 (cockpit-overhaul): assigned rows gain resolved `blocks` per the
        # locked precedence (Todoist-native → Preset → press duration_min → 1).
        # Suggested rows stay untouched — the cockpit never consumes them.
        presets = result.config.get_presets() if result.config is not None else []
        fm_by_path = {n["path"]: n["fm"] for n in assigned_notes}
        for row in digest["assigned"]:
            row["blocks"] = resolve_assigned_blocks(
                row, presets, fm_by_path.get(row.get("path"))
            )

        # FT-01: remembered-duration overlay — a valid vault-scoped remembered
        # value wins over the source-derived blocks and is labelled
        # ``remembered``. Pure read: never mutates the duration cache, never
        # calls billed endpoints, never writes upstream sources. Missing or
        # corrupt cache data simply leaves source-resolved blocks in place.
        remembered = duration_memory.read_vault_memory(vault)
        for row in digest["assigned"]:
            duration_memory.apply_remembered_overlay(row, remembered)

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
        normalized_day_setup = _normalize_route_day_setup(
            config, day_setup, today, resolved_day_semantics,
        )
        if normalized_day_setup != day_setup:
            day_setup = normalized_day_setup
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
            today=today,
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
        runstate.write_digest_index(
            vault,
            today,
            build_digest_index(digest),
            # Server-owned policy stamp: the revision this digest was built
            # under. Recorded for a later staleness guard; this slice adds no
            # refresh rejection.
            exclusion_settings_revision=exclusion_policy.revision,
        )

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
            "calendar_decisions": calendar_decisions,
            "source_warnings": w_todo + w_cal + w_hab + w_capacities,
            "source_counts": {
                "vault": len(run_data["pool_items"]) + len(run_data["assigned_items"]),
                "todoist": len(t_assigned) + len(t_pool),
                "capacities": len(capacities_items),
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
        normalized_merged = _normalize_route_day_setup(
            config, merged, today, resolved_day_semantics,
        )
        if normalized_merged != merged:
            merged = normalized_merged
            resolved_day_semantics = day_semantics.resolve_day_contract(
                result, today, dated_overrides=merged,
            )
        planning_config_fingerprint = day_semantics.planning_config_fingerprint(
            result, today, dated_overrides=merged,
        )
        ext_cfg: dict[str, Any] = {
            **dict(config.get("Defaults") or {}),
            "calendar_capacity_classes": config.get("Calendar Capacity Classes"),
            # Issue #6: canonical vault config section for disabled calendars,
            # so route-level exclusions become diagnosable via calendar_decisions.
            "calendar_disabled": config.get("Disabled Calendars"),
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
            today=today,
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

    # -- duration-memory (FT-01) ----------------------------------------------

    def _source_resolved_fallback(vault: Path, today: date, identity: str):
        """Current source-resolved ``(minutes, label)`` for a canonical
        identity, resolved WITHOUT memory — the reset route's fallback.
        Mirrors /plan-inputs' row assembly (vault gather + external Todoist
        via the injected read clients); read-only and deterministic. None
        when the identity is not present today."""
        pool_notes, assigned_notes = _run_gather(vault, today)
        run_data = gather.build_run_data(pool_notes, assigned_notes, today)
        result = config_reader.read_config(vault)
        config: dict[str, Any] = (
            dict(result.config.sections) if result.config is not None else {}
        )
        presets = result.config.get_presets() if result.config is not None else []
        fm_by_path = {n["path"]: n["fm"] for n in assigned_notes}
        rows = list(run_data["assigned_items"]) + list(run_data["pool_items"])
        build_clients = app.state.build_read_clients or (lambda v, c: (None, None))
        todoist_c, _store = build_clients(vault, config)
        try:
            if todoist_c is not None:
                ext_cfg: dict[str, Any] = {
                    **dict(config.get("Defaults") or {}),
                    "calendar_capacity_classes": config.get("Calendar Capacity Classes"),
                }
                t_assigned, t_pool, _w = external_sources.fetch_todoist_items(
                    todoist_c, ext_cfg
                )
                rows = rows + list(t_assigned) + list(t_pool)
        finally:
            if todoist_c is not None and hasattr(todoist_c, "close"):
                try:
                    todoist_c.close()
                except Exception:  # noqa: BLE001
                    pass
        for row in rows:
            if duration_memory.item_identity(row) == identity:
                return duration_memory.resolve_duration(
                    row, presets, fm_by_path.get(row.get("path")), memory={}
                )
        return None

    @app.post("/duration-memory/save", dependencies=[Depends(require_token)])
    def post_duration_save(body: DurationSaveRequest) -> dict:
        """FT-01: persist a remembered duration for one canonical identity.

        Strict validation (integer >= 0 divisible by 5) happens before any
        cache access; lock/read/write failures fail closed (500) and never
        replace existing durable bytes."""
        vault = resolve_vault_root()
        try:
            identity = duration_memory.normalize_identity(body.identity)
            minutes = duration_memory.save_memory(vault, identity, body.minutes)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except (duration_memory.MemoryStoreError, OSError) as exc:
            # FT-05 F3: never return raw internal diagnostics (they can carry
            # absolute vault/cache paths). The real cause stays server-side.
            print(f"duration-memory save failed: {exc}", file=sys.stderr)
            raise HTTPException(
                status_code=500,
                detail={
                    "code": "duration_memory_store_error",
                    "message": (
                        "Duration memory could not be saved; "
                        "the existing value was preserved."
                    ),
                },
            ) from exc
        return {"ok": True, "identity": identity, "minutes": minutes,
                "duration_source": "remembered"}

    @app.post("/duration-memory/reset", dependencies=[Depends(require_token)])
    def post_duration_reset(body: DurationMemoryRequest) -> dict:
        """FT-01: remove the remembered duration for one canonical identity
        and return the current source-resolved fallback. No billed calls and
        no upstream writes — the fallback resolves from vault gather plus the
        same injected read clients /plan-inputs uses.

        FT-06 F2-R1 ordering: the source fallback is resolved FIRST. When no
        fallback exists (identity absent from today's plan) or resolution
        fails, reset returns a bounded error and leaves the durable cache
        bytes unchanged — the remembered value is never deleted without a
        proven fallback. Only a valid fallback proceeds to the (fail-closed)
        deletion and is returned to the client."""
        vault = resolve_vault_root()
        today = gather.effective_date(datetime.now())
        try:
            identity = duration_memory.normalize_identity(body.identity)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        try:
            fallback = _source_resolved_fallback(vault, today, identity)
        except Exception as exc:  # noqa: BLE001 — bounded client-safe boundary
            # FT-06 F2-R1: resolution failure must not delete durable memory.
            # The real cause (which may name vault/source paths) stays
            # server-side only.
            print(
                f"duration-memory reset fallback resolution failed: {exc}",
                file=sys.stderr,
            )
            raise HTTPException(
                status_code=500,
                detail={
                    "code": "duration_memory_fallback_error",
                    "message": (
                        "Duration memory could not be reset; the source "
                        "duration could not be resolved and the existing "
                        "value was preserved."
                    ),
                },
            ) from exc
        if fallback is None:
            # FT-06 F2-R1: no source fallback — bounded error, memory preserved.
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "duration_memory_no_fallback",
                    "message": (
                        "No source duration was found to restore; the "
                        "remembered value was preserved."
                    ),
                },
            )
        try:
            removed = duration_memory.reset_memory(vault, identity)
        except (duration_memory.MemoryStoreError, OSError) as exc:
            # FT-05 F3: bounded client-safe detail; the real cause (which may
            # name the vault/cache path) stays server-side only.
            print(f"duration-memory reset failed: {exc}", file=sys.stderr)
            raise HTTPException(
                status_code=500,
                detail={
                    "code": "duration_memory_store_error",
                    "message": (
                        "Duration memory could not be reset; "
                        "the existing value was preserved."
                    ),
                },
            ) from exc
        return {
            "ok": True,
            "identity": identity,
            "removed": removed,
            "duration_minutes": fallback[0],
            "duration_source": fallback[1],
            "found": True,
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
        present = body.model_fields_set
        existing_setup = {
            k: v for k, v in _read_today_runstate(vault, today).items()
            if k in _DAY_SETUP_KEYS and v not in ("", None)
        }
        normalization_overrides = dict(existing_setup)
        if "day_preset" in present:
            normalization_overrides["day_preset"] = body.day_preset
        if "work_allotment_minutes" in present:
            normalization_overrides["work_allotment_minutes"] = body.work_allotment_minutes
        normalization_semantics = day_semantics.resolve_day_contract(
            result, today, dated_overrides=normalization_overrides,
        )
        re_included: set[str] = set()
        for o in body.anchored or []:
            name = str(o.get("id") or "")
            on = o.get("on") is True or (o.get("skip_today") is False)
            if name in defaults_off and on and not o.get("skip_today"):
                re_included.add(name)
        # Merge the request over today's state before normalization. This
        # repairs stale persisted IDs even when the user only edits another
        # Day Setup field, while preserving omitted dated overrides.
        candidate_setup = dict(existing_setup)
        if body.schedulable is not None:
            candidate_setup["schedulable"] = body.schedulable
        if "day_preset" in present:
            candidate_setup["day_preset"] = body.day_preset
        if "work_allotment_minutes" in present:
            candidate_setup["work_allotment_minutes"] = body.work_allotment_minutes
        candidate_setup = _normalize_route_day_setup(
            config, candidate_setup, today, normalization_semantics,
        )
        schedulable = candidate_setup.get("schedulable")
        minting = (schedulable or {}).get("minting") or {}
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
        explicit_mint_disable = (
            "work_allotment_minutes" in present
            and body.work_allotment_minutes == 0
        )
        if explicit_mint_disable:
            # A dated zero is authoritative even when the client sends stale
            # selected sessions, or omits schedulable entirely. The RMW below
            # also clears any previously persisted selection.
            minting = {**minting, "on": False, "n": 0, "sessions": []}
            schedulable = {
                **(schedulable or {}),
                "minting": minting,
            }
            updates["schedulable"] = schedulable
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
        def _save_day_setup(state: dict[str, Any]) -> None:
            state.update(updates)
            if not explicit_mint_disable:
                return
            current_sched = dict(state.get("schedulable") or {})
            current_mint = dict(current_sched.get("minting") or {})
            current_mint.update({"on": False, "n": 0, "sessions": []})
            current_sched["minting"] = current_mint
            state["schedulable"] = current_sched

        state = runstate.update_runstate(vault, today, _save_day_setup)
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
        # P3-02: the same-day Drop-from-plan exclusions must also gate /digest
        # — a dropped item may neither be returned nor re-indexed for today,
        # on either surface. Date-scoped: tomorrow the item is eligible again.
        dropped_rows = runstate.read_dropped(vault, today)
        dropped_ids = {
            str(d.get("identity")) for d in dropped_rows if d.get("identity")
        }
        if dropped_ids:
            assigned_items = [
                i for i in assigned_items
                if runtime_actions.drop_identity_of(i) not in dropped_ids
            ]
            pool_items = [
                i for i in pool_items
                if runtime_actions.drop_identity_of(i) not in dropped_ids
            ]
        cfg_result = config_reader.read_config(vault)
        exclusion_policy = _exclusion_policy_or_block(vault)
        try:
            return build_digest(
                pool_items,
                assigned_items,
                today,
                order,
                ignore=(
                    cfg_result.config.get_ignore_list() if cfg_result.config is not None else None
                ),
                bias=deferrals.bias_map(vault, today),  # T1 defer-with-memory
                exclusion_policy=exclusion_policy,
            )
        except tag_exclusions.TagExclusionBlocked as exc:
            raise HTTPException(status_code=503, detail=exc.diagnostics) from exc

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
        """Propose a timeline through the pure planning boundary."""
        vault = resolve_vault_root()
        today = gather.effective_date(datetime.now())
        canonical_assigned = _canonicalize_route_assigned(
            vault, today, body.assigned,
        )
        day_setup = {k: v for k, v in _read_today_runstate(vault, today).items()
                     if k in _DAY_SETUP_KEYS and v not in ("", None)}
        defaults: dict[str, Any] = dict((body.config or {}).get("Defaults") or {})
        resolved_day_semantics = _authoritative_day_semantics(
            body.config, day_setup, today,
        )
        day_setup = _normalize_route_day_setup(
            body.config or {}, day_setup, today, resolved_day_semantics,
        )
        resolved_day_semantics = _authoritative_day_semantics(
            body.config, day_setup, today,
        )
        effective_anchored = _judged_anchored(body.anchored_blocks, day_setup)
        frame = time_engine.compute_time_frame(
            now=datetime.now(),
            config_eod=time_engine.to_hhmm(defaults.get("eod")) or "23:59",
            round_to_minutes=int(defaults.get("anchor.round_to_minutes") or 15),
            busy_events=[{"start": time_engine.to_hhmm(b.get("Start")),
                          "title": b.get("Block")}
                         for b in effective_anchored
                         if b.get("source") == "calendar" and b.get("Start")
                         and not b.get("skip_today")],
            anchor_override=time_engine.to_hhmm(day_setup.get("anchor")),
            eod_override=time_engine.to_hhmm(day_setup.get("eod")),
        )
        prepared_inputs = {
            "assigned": canonical_assigned,
            "config": body.config or {},
            "anchored_blocks": effective_anchored,
            "day_setup": day_setup,
            "today": today,
            "time_frame": frame.as_dict(),
            "day_semantics": resolved_day_semantics,
            "planning_config_fingerprint": body.planning_config_fingerprint,
            "pinned_rows": body.pinned_rows,
        }
        try:
            prepared = planning.prepare_sequence(prepared_inputs, mode="proposal")
        except planning.PlanningError as exc:
            if exc.conflicts:
                raise HTTPException(
                    status_code=422,
                    detail={"message": str(exc), "conflicts": exc.conflicts},
                ) from exc
            raise HTTPException(
                status_code=422,
                detail={"message": str(exc), "hard_errors": exc.hard_errors},
            ) from exc
        _require_billed_budget(vault, today)
        try:
            outcome = planning.run_proposal(
                prepared, judgment.propose_sequence,
                ctx=_billed_ctx(vault, today),
            )
        except judgment.BudgetExceededError as exc:
            raise HTTPException(status_code=429, detail=str(exc)) from exc
        except judgment.JudgmentError as exc:
            raise HTTPException(status_code=502, detail=f"judgment error: {exc}") from exc
        if not outcome.validation.ok:
            raise HTTPException(
                status_code=422,
                detail={"message": "sequence validation failed",
                        "hard_errors": outcome.validation.hard_errors,
                        "rejected_proposal": outcome.rejected_proposal},
            )
        runstate.update_runstate(vault, today, outcome.snapshot)
        assert outcome.result is not None
        return outcome.result

    @app.post("/validate-sequence", dependencies=[Depends(require_token)])
    def post_validate_sequence(body: ValidateSequenceRequest) -> dict:
        """Revalidate the client layout without provider calls or merging."""
        vault = resolve_vault_root()
        today = gather.effective_date(datetime.now())
        canonical_assigned = _canonicalize_route_assigned(
            vault, today, body.assigned,
        )
        snapshot = _read_today_runstate(vault, today)
        day_setup = {k: v for k, v in snapshot.items()
                     if k in _DAY_SETUP_KEYS and v not in ("", None)}
        defaults = dict((body.config or {}).get("Defaults") or {})
        resolved_day_semantics = _authoritative_day_semantics(
            body.config, day_setup, today,
        )
        day_setup = _normalize_route_day_setup(
            body.config or {}, day_setup, today, resolved_day_semantics,
        )
        resolved_day_semantics = _authoritative_day_semantics(
            body.config, day_setup, today,
        )
        effective_anchored = _judged_anchored(body.anchored_blocks, day_setup)
        frame = time_engine.compute_time_frame(
            now=datetime.now(),
            config_eod=time_engine.to_hhmm(defaults.get("eod")) or "23:59",
            round_to_minutes=int(defaults.get("anchor.round_to_minutes") or 15),
            busy_events=[{"start": time_engine.to_hhmm(b.get("Start")),
                          "title": b.get("Block")}
                         for b in effective_anchored
                         if b.get("source") == "calendar" and b.get("Start")
                         and not b.get("skip_today")],
            anchor_override=time_engine.to_hhmm(day_setup.get("anchor")),
            eod_override=time_engine.to_hhmm(day_setup.get("eod")),
        )
        try:
            prepared = planning.prepare_sequence({
                "assigned": canonical_assigned,
                "config": body.config or {},
                "anchored_blocks": effective_anchored,
                "day_setup": day_setup,
                "today": today,
                "time_frame": frame.as_dict(),
                "day_semantics": resolved_day_semantics,
                "planning_config_fingerprint": body.planning_config_fingerprint,
                "pinned_rows": body.pinned_rows,
                "overlap_grants": body.overlap_grants,
                "snapshot": snapshot,
                "sequence": body.sequence,
            }, mode="revalidation")
        except planning.PlanningError as exc:
            return {"ok": False, "hard_errors": exc.hard_errors or [str(exc)],
                    "warnings": []}
        return planning.validate_revalidation(prepared).as_dict()

    def _check_optional_commit_snapshot(body: CommitRequest) -> None:
        """Keep the legacy snapshot check optional, separate from native pins.

        Older clients omit the snapshot metadata entirely.  That omission must
        not disable the server-derived native protection, so this compatibility
        check is deliberately limited to the metadata a client actually sent.
        """
        if not (
            body.pinned_rows
            or body.overlap_grants
            or body.planning_config_fingerprint
        ):
            return
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

        if body is not None:
            _check_optional_commit_snapshot(body)

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
                # FEEDBACK-24: the Day Setup gate (409) takes precedence over
                # the P3-02 eligibility boundary; apply the P3-02 eligibility
                # boundary before any live write path runs.
                live_vault = resolve_vault_root()
                live_today = gather.effective_date(datetime.now())
                _require_day_setup(live_vault, live_today, "committing")
                commit_config, safe_digest = _validate_commit_eligibility(
                    body, live_vault, live_today,
                )
                return _run_live_commit(
                    body, resume, config_override=commit_config,
                    digest_override=safe_digest,
                )
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
        # P3-02: eligibility boundary immediately before manifest construction.
        # It returns the same server-authoritative effective config that the
        # validator used, plus the sanitized digest, so the client cannot
        # inject or rewrite Step E/D source rows or assigned metadata after
        # the boundary.
        shadow_config, safe_digest = _validate_commit_eligibility(body, vault, shadow_today)
        manifest = shadow.build_plan_manifest(
            safe_digest, body.sequence, shadow_config,
            time_frame=_frame_for_writes(shadow_config, shadow_day_setup))

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

    def _server_commit_config(
        vault: Path, today: date, day_setup: dict[str, Any],
    ) -> dict[str, Any]:
        """Build the effective config used by the commit boundary.

        The browser echoes config in the commit body, but source rows must be
        authorized from the current vault/config and dated setup instead of
        from that echo.  The returned object is also passed to the manifest
        builder so a client cannot alter an authorized row after validation.
        """
        result = config_reader.read_config(vault)
        config: dict[str, Any] = (
            dict(result.config.sections) if result.config is not None else {}
        )
        config = shadow.apply_day_setup(config, day_setup)
        return _ensure_micro_adventure(config, vault, today)

    def _validate_commit_eligibility(
        body: CommitRequest, vault: Path, today: date,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """P3-02 server authorization immediately before manifest building.

        Names alone are not identities: assigned rows must match the server's
        assigned digest surface and every source identity supplied by the
        client.  Sequence rows are then limited to current server-derived
        assigned, anchored, schedulable, and Trinoor identities; fixed-lane
        rows (anchored blocks, Trinoor work zones) must also carry the exact
        server timing, and a ``backdrop`` flag is honored only on server-emitted
        Trinoor zone rows.  The client ``_excluded`` flag and client-only config
        are never authorization. Native timed protection is recomputed from
        the dated server digest index immediately before the manifest boundary.

        Returns the server-authoritative effective config plus a SANITIZED
        digest whose assigned rows carry only trusted server identity, source,
        timing, recurrence, and duration fields. The only request-derived
        assigned metadata that can survive is an exact boolean
        ``allow_time_adjustment`` for a matching Todoist row and a validated
        effective-blocks overlay (including zero) for planning/manifest
        duration; client ``types``/routing metadata never reaches the manifest
        builder, and Todoist ``native_blocks`` is always server-derived so an
        overlay cannot unpin a native timed row.
        """
        def _hhmm(value: Any) -> str | None:
            text = time_engine.to_hhmm(value)
            if not text:
                return None
            try:
                hours, minutes = text.split(":")
                return f"{int(hours):02d}:{int(minutes):02d}"
            except (ValueError, AttributeError):
                return None

        digest = body.digest if isinstance(body.digest, dict) else {}
        submitted_assigned = digest.get("assigned") or []
        if not isinstance(submitted_assigned, list):
            raise HTTPException(
                status_code=422,
                detail="commit refused: malformed assigned digest — nothing was written",
            )

        malformed_assigned: list[str] = []
        for row in submitted_assigned:
            if not isinstance(row, dict):
                malformed_assigned.append(repr(row))
                continue
            name = str(row.get("name") or "").strip()
            path = str(row.get("path") or "").strip()
            todoist_id = str(row.get("todoist_id") or "").strip()
            if not name or not (path or todoist_id):
                malformed_assigned.append(name or repr(row))
        if malformed_assigned:
            raise HTTPException(
                status_code=422,
                detail="commit refused: malformed assigned rows: "
                       + ", ".join(malformed_assigned)
                       + " — nothing was written",
            )

        index = runstate.read_digest_index(vault, today)
        server_assigned = [
            row for row in index
            if isinstance(row, dict)
            and str(row.get("surface") or "").casefold() == "assigned"
        ]

        # P3-02 stale/drop revalidation: the cached index may predate a Drop
        # made after the last /plan-inputs refresh.  Re-read today's dropped
        # identities and remove matching cached assigned rows so a dropped
        # item cannot be committed merely because the index was not refreshed.
        dropped_ids = {
            str(d.get("identity")) for d in runstate.read_dropped(vault, today)
            if d.get("identity")
        }
        dropped_assigned_names: list[str] = []
        if dropped_ids:
            dropped_assigned_names = [
                str(row.get("name") or "<unnamed>")
                for row in server_assigned
                if runtime_actions.drop_identity_of(row) in dropped_ids
            ]
            server_assigned = [
                row for row in server_assigned
                if runtime_actions.drop_identity_of(row) not in dropped_ids
            ]

        def _server_identity_matches(
            submitted: dict[str, Any], candidate: dict[str, Any],
        ) -> bool:
            """Match a submitted name plus a stable server source identity.

            A Todoist id and its ``todoist://`` path are equivalent forms of
            the same source identity. Vault paths remain path-only. In either
            case a name-only submission is never sufficient.
            """
            submitted_path = str(submitted.get("path") or "").strip()
            submitted_id = str(submitted.get("todoist_id") or "").strip()
            candidate_path = str(candidate.get("path") or "").strip()
            candidate_id = str(candidate.get("todoist_id") or "").strip()

            if candidate_id:
                if submitted_id and submitted_id != candidate_id:
                    return False
                if submitted_path and candidate_path and submitted_path != candidate_path:
                    return False
                return bool(
                    submitted_id == candidate_id
                    or submitted_path == candidate_path
                    or submitted_path == f"todoist://{candidate_id}"
                )
            if candidate_path.startswith("todoist://"):
                return bool(
                    submitted_path == candidate_path
                    or submitted_id == candidate_path[len("todoist://"):]
                ) and not (
                    submitted_path and submitted_path != candidate_path
                )
            return bool(candidate_path) and submitted_path == candidate_path and not submitted_id

        def _server_match(row: dict[str, Any]) -> dict[str, Any] | None:
            name = str(row.get("name") or "").strip()
            for candidate in server_assigned:
                if str(candidate.get("name") or "").strip() != name:
                    continue
                if not _server_identity_matches(row, candidate):
                    continue
                return candidate
            return None

        authorized_rows: list[dict[str, Any]] = []
        authorized_matches: list[tuple[dict[str, Any], dict[str, Any]]] = []
        unknown_assigned: list[str] = []
        for row in submitted_assigned:
            match = _server_match(row)
            if match is None:
                unknown_assigned.append(str(row.get("name") or "<unnamed>"))
            else:
                authorized_rows.append(match)
                authorized_matches.append((match, row))

        capacities_names = sorted({
            str(candidate.get("name") or "<unnamed>")
            for candidate, _submitted in authorized_matches
            if (
                str(candidate.get("source") or "").strip().casefold() == "capacities"
                or str(candidate.get("path") or "").strip().casefold().startswith("capacities://")
            )
        })
        if capacities_names:
            raise HTTPException(
                status_code=422,
                detail=(
                    "commit refused: Capacities rows are plan-only in this "
                    "integration slice; completion uses /runtime-actions: "
                    + ", ".join(capacities_names)
                    + " — nothing was written"
                ),
            )

        # Effective-blocks overlays are validated BEFORE any write decision:
        # a malformed or extreme value is collected here and fails the commit
        # closed, never silently accepted as the manifest duration.
        malformed_blocks: list[str] = []
        for candidate, submitted in authorized_matches:
            try:
                _validated_blocks_overlay(submitted.get("blocks"))
            except ValueError as exc:
                malformed_blocks.append(
                    f"{str(candidate.get('name') or '<unnamed>')!r} (blocks: {exc})"
                )

        if submitted_assigned and not server_assigned:
            detail = (
                "commit refused: no server-derived assigned identity "
                "available for today"
            )
            if dropped_assigned_names:
                detail += "; dropped today: " + ", ".join(dropped_assigned_names)
            raise HTTPException(
                status_code=422,
                detail=detail + " — nothing was written",
            )

        day_setup = {
            key: value
            for key, value in _read_today_runstate(vault, today).items()
            if key in _DAY_SETUP_KEYS and value not in ("", None)
        }
        config = _server_commit_config(vault, today, day_setup)
        presets = config.get("Presets") or config.get("presets") or []

        def _server_digest_row(
            candidate: dict[str, Any],
        ) -> dict[str, Any]:
            """Project only server-owned digest fields into the write lane."""
            projected: dict[str, Any] = {
                "name": str(candidate.get("name") or "").strip(),
                "todoist_id": str(candidate.get("todoist_id") or "").strip(),
                "path": str(candidate.get("path") or "").strip(),
            }
            for key in (
                "source", "duration", "blocks", "native_blocks",
                "scheduled_start", "is_recurring",
            ):
                if key in candidate and candidate.get(key) is not None:
                    projected[key] = candidate[key]
            # A legacy index may have duration but not blocks. Resolve that
            # equivalent from the server row/config, never from the request.
            if "blocks" not in projected:
                projected["blocks"] = resolve_assigned_blocks(projected, presets)
            # Todoist native provenance is server-owned: ``native_blocks``
            # (existing index value, else the trusted server blocks) is never
            # the client overlay, so an effective-blocks override (incl. zero)
            # cannot unpin a native timed row at the commit boundary.
            if ("native_blocks" not in projected
                    and _is_todoist_row(projected)
                    and projected.get("blocks") is not None):
                projected["native_blocks"] = projected["blocks"]
            return projected

        def _is_todoist_row(row: dict[str, Any]) -> bool:
            source = str(row.get("source") or "").strip().casefold()
            todoist_id = str(row.get("todoist_id") or "").strip()
            path = str(row.get("path") or "").strip().casefold()
            return source == "todoist" or bool(todoist_id) or path.startswith("todoist://")

        def _effective_blocks_with_overlay(
            candidate: dict[str, Any], submitted: dict[str, Any],
        ) -> float | int | None:
            """The effective planning blocks for an authorized row: a validated
            submitted overlay, else the trusted server blocks. Overlays were
            already validated before any write decision, so this never raises
            and never re-records a malformed overlay."""
            overlay = _validated_blocks_overlay(submitted.get("blocks"))
            if overlay is None:
                return candidate.get("blocks")
            return overlay

        client_anchored = set(shadow._anchored_specs(body.config or {}))
        server_anchored = set(shadow._anchored_specs(config))
        injected_anchored = sorted(client_anchored - server_anchored)

        sequence = body.sequence if isinstance(body.sequence, dict) else {}
        rows = sequence.get("sequence") or []
        if not isinstance(rows, list):
            raise HTTPException(
                status_code=422,
                detail="commit refused: malformed sequence — nothing was written",
            )
        malformed_sequence: list[str] = []
        valid_rows: list[dict[str, Any]] = []
        seen_seq_ids: set[str] = set()
        for row in rows:
            if not isinstance(row, dict) or not str(row.get("id") or "").strip():
                malformed_sequence.append(repr(row))
                continue
            # P3-02: fail closed on malformed or non-positive timing — never
            # silently normalize a malformed row at the commit boundary.
            start = time_engine.to_hhmm(row.get("start"))
            end = time_engine.to_hhmm(row.get("end"))
            if start is None or end is None:
                malformed_sequence.append(
                    f"{str(row.get('id')).strip()!r}: start/end is not valid HH:MM")
                continue
            try:
                start_min = int(start[:2]) * 60 + int(start[3:])
                end_min = int(end[:2]) * 60 + int(end[3:])
            except ValueError:
                malformed_sequence.append(
                    f"{str(row.get('id')).strip()!r}: unparseable interval")
                continue
            if end_min <= start_min:
                malformed_sequence.append(
                    f"{str(row.get('id')).strip()!r}: non-positive interval "
                    f"({start}-{end})")
                continue
            row_id = str(row.get("id")).strip()
            if row_id in seen_seq_ids:
                malformed_sequence.append(f"{row_id!r}: appears more than once in sequence")
                continue
            seen_seq_ids.add(row_id)
            normalized_row = dict(row)
            normalized_row.update({"id": row_id, "start": start, "end": end})
            valid_rows.append(normalized_row)

        # The caller's pin list is only diagnostic input. Native protection is
        # server-derived for the assigned rows actually included in this
        # commit; intentionally omitted assigned rows are not in this lane.
        native_assigned: list[dict[str, Any]] = []
        for candidate, submitted in authorized_matches:
            if not _is_todoist_row(candidate):
                continue
            native_row = _server_digest_row(candidate)
            native_row["id"] = native_row["name"]
            if submitted.get("allow_time_adjustment") is True:
                native_row["allow_time_adjustment"] = True
            native_assigned.append(native_row)
        native_pin_errors = planning.sequence.verify_native_pins_in_sequence(
            native_assigned, valid_rows, body.pinned_rows,
        )

        frame = _frame_for_writes(config, day_setup)
        resolved_day_semantics = _authoritative_day_semantics(
            config, day_setup, today,
        )
        try:
            sched_items, zone_rows, _notes = external_sources.build_schedulable_blocks(
                config, day_setup, today, str(frame.get("anchor") or "00:00"),
                resolved_day_semantics=resolved_day_semantics,
            )
        except Exception as exc:  # noqa: BLE001 — fail closed on source ambiguity
            raise HTTPException(
                status_code=422,
                detail=f"commit refused: server schedulable eligibility unavailable ({exc})"
                       " — nothing was written",
            ) from exc

        def _hhmm2(value: Any) -> str | None:
            text = time_engine.to_hhmm(value)
            if not text:
                return None
            try:
                hours, minutes = text.split(":")
                return f"{int(hours):02d}:{int(minutes):02d}"
            except (ValueError, AttributeError):
                return None

        # Legacy configs predate day-preset projections. Their configured
        # Trinoor slots remain server-owned fixed rows even when the
        # schedulable builder suppresses weekday-only backdrop emission; use
        # the same server slot values and retain exact timing validation.
        server_zone_rows = list(zone_rows)
        if not server_zone_rows and "Day Presets" not in config:
            for slot in ((config.get("Template Blocks") or {}).get("Trinoor Hours") or []):
                if not isinstance(slot, dict):
                    continue
                start = _hhmm2(slot.get("Start"))
                end = _hhmm2(slot.get("End"))
                if start is None or end is None or start >= end:
                    continue
                server_zone_rows.append({
                    "id": f"🟡 Trinoor : {slot.get('Slot', '?')}",
                    "start": start,
                    "end": end,
                    "zone": "work_hours",
                    "backdrop": True,
                })

        assigned_names = {
            str(row.get("name") or "").strip() for row in authorized_rows
        }
        anchored_names = set(shadow._anchored_specs(config))
        sched_names = {
            str(value).strip()
            for item in sched_items if isinstance(item, dict)
            for value in (item.get("id"), item.get("name"))
            if value
        }
        sched_names.discard("")
        # Older clients used emoji-prefixed Minting labels.  These aliases are
        # eligible only when the current server projection emitted Minting.
        if any(
            name.casefold() in {"minting", "🌊 minting", "🟡 minting"}
            for name in sched_names
        ):
            sched_names.update({"Minting", "🌊 Minting", "🟡 Minting"})
        # The legacy aggregate aliases are a compatibility lane, not a
        # permanent allowlist. Prefer an actually emitted Minting row; for a
        # legacy config with no day-preset projection, preserve the old
        # default row only when today's server setup has not explicitly
        # disabled Minting or selected an empty session set.
        minting_setup = (day_setup.get("schedulable") or {}).get("minting")
        has_mint_session_selection = (
            isinstance(minting_setup, dict)
            and isinstance(minting_setup.get("sessions"), list)
        )
        explicit_mint_disable = (
            day_setup.get("work_allotment_minutes") == 0
            or isinstance(minting_setup, dict)
            and minting_setup.get("on") is False
            or isinstance(minting_setup, dict)
            and isinstance(minting_setup.get("sessions"), list)
            and not minting_setup.get("sessions")
        )
        if (
            not any(_route_name(name) == "minting" for name in sched_names)
            and "Day Presets" not in config
            and not explicit_mint_disable
            and not has_mint_session_selection
            and resolved_day_semantics.get("mint_enabled", True) is not False
        ):
            sched_names.update({"Minting", "🌊 Minting", "🟡 Minting"})

        zone_names = {
            str(row.get("id") or "").strip()
            for row in server_zone_rows if isinstance(row, dict)
        }
        zone_names.discard("")
        allowed = assigned_names | anchored_names | sched_names | zone_names
        unknown_sequence = [
            str(row.get("id") or "<empty>")
            for row in valid_rows
            if str(row.get("id") or "").strip() not in allowed
        ]

        # P3-02: exact authorization for server-derived anchored and Trinoor
        # rows — a valid ID must not authorize forged timing or backdrop.
        # Assigned/schedulable rows are movable: only well-formedness applies.
        fixed_timing: list[str] = []
        for row in valid_rows:
            row_id = str(row.get("id") or "").strip()
            submitted_start = _hhmm2(row.get("start"))
            submitted_end = _hhmm2(row.get("end"))
            if bool(row.get("backdrop")) and row_id not in zone_names:
                fixed_timing.append(f"{row_id!r}: client backdrop on non-zone row")
                continue
            if row_id in zone_names:
                server_span = next(
                    (r for r in server_zone_rows
                     if str(r.get("id") or "").strip() == row_id),
                    None,
                )
                if (server_span is None
                        or submitted_start != _hhmm2(server_span.get("start"))
                        or submitted_end != _hhmm2(server_span.get("end"))):
                    fixed_timing.append(
                        f"{row_id!r}: timing {submitted_start}-{submitted_end} "
                        f"does not match server zone projection")
                continue
            if row_id in anchored_names:
                spec = shadow._anchored_specs(config)[row_id]
                server_start = _hhmm2(spec.get("time")) or _hhmm2(spec.get("Start")) or _hhmm2(spec.get("start"))
                if server_start is not None and submitted_start != server_start:
                    fixed_timing.append(
                        f"{row_id!r}: start {submitted_start} does not match "
                        f"server anchored projection {server_start}")
                    continue
                server_end = (_hhmm2(spec.get("End")) or _hhmm2(spec.get("end")))
                if server_end is None:
                    mins = time_engine.duration_minutes(spec.get("Duration"))
                    if mins and server_start is not None:
                        total = (int(server_start[:2]) * 60 + int(server_start[3:])) + mins
                        server_end = f"{total // 60:02d}:{total % 60:02d}"
                if server_end is not None and submitted_end != server_end:
                    fixed_timing.append(
                        f"{row_id!r}: end {submitted_end} does not match "
                        f"server anchored projection {server_end}")

        if (unknown_assigned or injected_anchored or malformed_sequence
                or unknown_sequence or fixed_timing or native_pin_errors
                or malformed_blocks):
            parts: list[str] = []
            if unknown_assigned:
                parts.append("stale/dropped assigned items: "
                             + ", ".join(repr(name) for name in unknown_assigned))
            if injected_anchored:
                parts.append("client-only anchored items: "
                             + ", ".join(repr(name) for name in injected_anchored))
            if malformed_sequence:
                parts.append("malformed sequence rows: "
                             + ", ".join(malformed_sequence))
            if unknown_sequence:
                parts.append("stale/dropped sequence rows: "
                             + ", ".join(repr(row) for row in unknown_sequence))
            if fixed_timing:
                parts.append("forged fixed-lane timing/backdrop: "
                             + ", ".join(fixed_timing))
            if native_pin_errors:
                parts.append("native timed protection: "
                             + "; ".join(native_pin_errors))
            if malformed_blocks:
                parts.append("malformed blocks overlay: "
                             + ", ".join(malformed_blocks))
            raise HTTPException(
                status_code=422,
                detail="commit refused: " + "; ".join(parts)
                       + " — regenerate before committing. Nothing was written",
            )

        # P3-02/P4 canonical digest: hand the manifest builder a SANITIZED
        # digest whose assigned rows carry only trusted server fields. The
        # exact per-item opt-in is added only from its matching request row;
        # a validated effective-blocks overlay (including zero) rides as the
        # manifest duration, while ``native_blocks`` stays server-derived and
        # client ``types``/routing metadata never reaches build_plan_manifest.
        sanitized_assigned: list[dict[str, Any]] = []
        for candidate, submitted in authorized_matches:
            sanitized = _server_digest_row(candidate)
            overlay = _effective_blocks_with_overlay(candidate, submitted)
            if overlay is not None:
                sanitized["blocks"] = overlay
            if _is_todoist_row(candidate) and submitted.get("allow_time_adjustment") is True:
                sanitized["allow_time_adjustment"] = True
            sanitized_assigned.append(sanitized)
        safe_digest = {
            "valid_date": str(today),
            "assigned": sanitized_assigned,
            "suggested": [],
        }
        return config, safe_digest

    def _run_live_commit(
        body: CommitRequest, resume: bool,
        config_override: dict[str, Any] | None = None,
        digest_override: dict[str, Any] | None = None,
    ) -> Any:
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
        if config_override is None:
            config = _server_commit_config(vault, today, live_day_setup)
        else:
            config = config_override
        manifest = shadow.build_plan_manifest(
            digest_override if digest_override is not None else body.digest,
            body.sequence, config,
            time_frame=_frame_for_writes(config, live_day_setup))
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

    def _runtime_clients() -> tuple[Any, Any, Any, bool]:
        """(todoist, store, capacities, owns_todoist) for runtime actions.

        Injected builders are used first in tests. Each source surface degrades
        to None and the verb planner fails closed before any write. The
        Capacities builder remains opt-in; no live credential route is created
        here by default.
        """
        vault = resolve_vault_root()
        owns_todoist = False
        if app.state.build_commit_clients:
            todoist_c, store = app.state.build_commit_clients(vault, None)
            capacities_c = None
        else:
            todoist_c = None
            try:
                token = shadow.todoist_client.load_token(shadow.TOKEN_ENV_PATH)
                todoist_c = shadow.todoist_client.TodoistClient(token)
            except Exception:  # noqa: BLE001 — absence degrades, fail-closed later
                pass
            owns_todoist = todoist_c is not None
            store = None
            try:
                store = calendar_bridge.shared_store()
            except Exception:  # noqa: BLE001
                pass
            capacities_c = None

        build_capacities = app.state.build_capacities_adapter
        if build_capacities is not None:
            try:
                result = config_reader.read_config(vault)
                config = (
                    dict(result.config.sections)
                    if result.config is not None
                    else {}
                )
                capacities_c = build_capacities(vault, config)
            except Exception:  # noqa: BLE001 — action boundary fails closed
                capacities_c = None
        return todoist_c, store, capacities_c, owns_todoist

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
        todoist_c, store, capacities_c, owns = _runtime_clients()
        try:
            return runtime_actions.apply_action(
                vault, today, body.verb, body.target, body.args,
                todoist=todoist_c, store=store, capacities=capacities_c,
            )
        except runtime_actions.RuntimeActionError as exc:
            _raise_runtime_error(exc)
        finally:
            if owns:
                todoist_c.close()
            if capacities_c is not None and hasattr(capacities_c, "close"):
                capacities_c.close()

    @app.post("/runtime-actions/{action_id}/undo",
              dependencies=[Depends(require_token)])
    def post_runtime_action_undo(action_id: str) -> Any:
        vault = resolve_vault_root()
        today = gather.effective_date(datetime.now())
        # FEEDBACK-24: undo writes external state too — same closed gate.
        _require_day_setup(vault, today, "undoing runtime actions")
        todoist_c, store, capacities_c, owns = _runtime_clients()
        try:
            return runtime_actions.undo_action(
                vault, today, action_id,
                todoist=todoist_c, store=store, capacities=capacities_c,
            )
        except runtime_actions.RuntimeActionError as exc:
            _raise_runtime_error(exc)
        finally:
            if owns:
                todoist_c.close()
            if capacities_c is not None and hasattr(capacities_c, "close"):
                capacities_c.close()

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


def build_real_capacities_adapter(vault: Path, config: dict[str, Any]) -> Any:
    """Live Capacities adapter for the source seam.

    The seam's shape is ``(vault, config) -> adapter|None``, matching
    ``build_real_read_clients``; the builder underneath instead takes an
    optional ``CapacitiesBuilderConfig`` for injection, so this adapter is the
    boundary between the two conventions.

    ``config`` (the parsed vault config) is unused: Capacities reads its own
    vault-local mapping record and assignment settings, and the builder
    supplies the credential slot, page bound, and content-read budget.
    """
    return capacities_builder.build_capacities_adapter(vault)


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
