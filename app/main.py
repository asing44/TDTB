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

Every store lives in the app home; no environment variable names a notes
root, and the retired one is ignored if an older launchd plist still exports it.
"""
from __future__ import annotations

import hashlib
import json
import math
import secrets
import re
import subprocess
import sys
import threading
from datetime import date, datetime, timedelta
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
import prompt_state  # noqa: E402
import prompt_export  # noqa: E402
import judgment  # noqa: E402
import planning  # noqa: E402
sequence = planning.sequence  # compatibility alias for existing test seams
import shadow  # noqa: E402
import commit  # noqa: E402
import calendar_bridge  # noqa: E402
import calendar_drift  # noqa: E402
import runtime_actions  # noqa: E402
import runtime_batches  # noqa: E402
import external_sources  # noqa: E402
import micro_adventure  # noqa: E402
import orchestrate  # noqa: E402
import time_engine  # noqa: E402
import capacity as capacity_mod  # noqa: E402
import capacities_settings  # noqa: E402
import capacities_mirror  # noqa: E402
import capacities_builder  # noqa: E402
import capacities_intake  # noqa: E402
import capacities_rules  # noqa: E402
import capacities_selections  # noqa: E402
import capacities_adapter  # noqa: E402
import capacities_refresh  # noqa: E402
import capacities_structure_titles  # noqa: E402
import exclusion_settings  # noqa: E402
import tag_exclusions  # noqa: E402
import todoist_exclusions  # noqa: E402
import app_config  # noqa: E402
import config_write  # noqa: E402
import artifact_source  # noqa: E402

#: Sentinel distinguishing "no coordinator built yet" from a built-but-
#: unconfigured coordinator (``None``) in ``app.state.refresh_coordinator``.
_REFRESH_COORDINATOR_UNSET = object()

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
            parts.append(-(urgency + nudge))  # note-era urgency: 4 = highest
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


#: U3c-2 selection notices rendered onto the digest ``source_warnings``. The
#: canonical identity is the only row reference, so no title or payload leaks.
_SELECTION_NOTICE_TEXT = {
    capacities_selections.NOTICE_EXCLUDED:
        "was not promoted: excluded by the tag exclusions or the drop list",
    capacities_selections.NOTICE_TYPE_DISABLED:
        "was not promoted: its Capacities type is no longer mapped",
    capacities_selections.NOTICE_NOT_CACHED:
        "was not promoted: no cached Capacities row matches it",
    capacities_selections.NOTICE_RULE_CHANGED:
        "was promoted, but its selection predates the current rules revision",
}
_SELECTIONS_UNAPPLIED_WARNING = (
    "Capacities selections were not applied: a Capacities record is "
    "unreadable or malformed"
)
_SOURCE_UNAPPLIED_WARNING = (
    "Capacities selections were not applied: the source mapping record is "
    "unreadable"
)


def _capacities_hard_excluded(
    today: date,
    candidates: list[dict[str, Any]],
    exclusion_policy: tag_exclusions.ExclusionPolicy | None,
) -> set[str]:
    """The identities hard-excluded from Capacities candidates today.

    The one definition of a hard exclusion (U4 S4), shared by the selection
    promotion path and the select endpoint so the two cannot drift: today's
    drop list plus the tag-exclusion policy applied to ``candidates``. The tag
    dry run uses the shared matcher; ``build_digest`` re-applies it to the
    promoted rows, so the two stages cannot disagree. Raises
    ``tag_exclusions.TagExclusionBlocked`` when the policy cannot be evaluated.
    """
    hard_excluded = {
        str(entry.get("identity"))
        for entry in runstate.read_dropped(today)
        if entry.get("identity")
    }
    if exclusion_policy is not None and candidates:
        _assigned, kept_pool, _report = tag_exclusions.apply_tag_exclusions(
            [], candidates, exclusion_policy,
        )
        kept = {row.get("identity") for row in kept_pool}
        hard_excluded |= {
            row.get("identity") for row in candidates
            if row.get("identity") not in kept
        }
    return hard_excluded


def _capacities_selection_rejection(
    status_code: int, code: str, identity: str, message: str,
) -> HTTPException:
    """A bounded identity rejection: a stable code, the identity, and fixed text."""
    return HTTPException(
        status_code=status_code,
        detail={"code": code, "message": message, "identity": identity},
    )


def _capacities_selection_identity(value: str, space_id: str) -> str:
    """The canonical identity of one browser-supplied selection, or a 422.

    The canonicalizer is the store's own (``canonical_exclusion_identity``), so
    a title, path, bare id, or alias is refused the same way on every surface.
    An identity from another space is refused too.
    """
    message = "Capacities selection is not a canonical identity of this space."
    try:
        identity = capacities_settings.canonical_exclusion_identity(value)
    except ValueError as exc:
        raise _capacities_selection_rejection(
            422, "invalid_identity", value, message,
        ) from exc
    if identity.split(":", 3)[1] != space_id:
        raise _capacities_selection_rejection(
            422, "invalid_identity", value, message,
        )
    return identity


def _capacities_selections_conflict(
    expected_revision: int, current_revision: int,
) -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={
            "code": "capacities_selections_conflict",
            "message": (
                "Capacities selections changed since they were read; "
                "reload and retry."
            ),
            "expected_revision": expected_revision,
            "current_revision": current_revision,
        },
    )


def _capacities_selections_storage_error() -> HTTPException:
    """Bounded 503 for selection storage: fixed text, no path, no payload."""
    return HTTPException(
        status_code=503,
        detail={
            "code": "capacities_selections_storage_error",
            "message": (
                "Capacities selections storage is unavailable; "
                "the existing selections were preserved."
            ),
        },
    )


def _capacities_selections_payload(
    record: capacities_selections.SelectionsRecord, rules_revision: int,
) -> dict[str, Any]:
    """The one wire shape both selection routes answer with."""
    return {
        "space_id": record.space_id,
        "revision": record.revision,
        "rules_revision": rules_revision,
        "selections": [
            {
                "identity": selection.identity,
                "acknowledged": selection.acknowledged,
                "rules_revision": selection.rules_revision,
            }
            for selection in record.selections
        ],
    }


def _capacities_rules_storage_error() -> HTTPException:
    """Bounded 503 for rules storage: fixed text, no path, no payload."""
    return HTTPException(
        status_code=503,
        detail={
            "code": "capacities_rules_storage_error",
            "message": (
                "Capacities rules storage is unavailable; "
                "the existing rules were preserved."
            ),
        },
    )


def _capacities_rules_conflict(
    expected_revision: int, current_revision: int,
) -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={
            "code": "capacities_rules_conflict",
            "message": (
                "Capacities rules changed since they were read; "
                "reload and retry."
            ),
            "expected_revision": expected_revision,
            "current_revision": current_revision,
        },
    )


def _capacities_rule_schema_by_structure(
    structures_payload: Any,
) -> dict[str, dict[str, str]]:
    """Per-structure ``{property_id: kind}`` from the published structure
    contract, with no provider call.

    Accepts the raw structures payload (a list of rows, or a
    ``{"structures": [...]}`` object). A row's ``propertyDefinitions`` is
    normalized through :func:`capacities_rules.schema_from_definitions` — the
    same normalizer the store validates against. Unknown shapes yield no entry,
    so a missing or partial contract leaves the schema unavailable rather than
    fabricating one."""
    rows = structures_payload
    if isinstance(rows, dict):
        rows = rows.get("structures")
    if not isinstance(rows, (list, tuple)):
        return {}
    schema: dict[str, dict[str, str]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        structure_id = row.get("id") or row.get("structureId")
        if not isinstance(structure_id, str) or not structure_id:
            continue
        schema[structure_id] = capacities_rules.schema_from_definitions(
            row.get("propertyDefinitions")
        )
    return schema


def _capacities_rules_contract_schema(
    space_id: str,
) -> tuple[dict[str, dict[str, str]], bool]:
    """``(schema_by_structure, contract_available)`` from the installed
    generation's structure contract — an offline read, no provider call.

    A missing or unusable contract yields ``({}, False)``: a save is then
    stored as a draft because there is no discovered shape to validate the
    rule against, never because a shape was invented."""
    try:
        store = capacities_builder.build_refresh_state(space_id)
        contract = store.load_structure_contract(
            capacities_refresh.DEFAULT_SCOPE_KEY
        )
    except Exception as exc:  # noqa: BLE001 — bounded local boundary
        print(f"capacities rules contract read failed: {exc}", file=sys.stderr)
        return {}, False
    if contract is None:
        return {}, False
    return _capacities_rule_schema_by_structure(contract.structures), True


def _capacities_rules_payload(
    *,
    space_id: str | None,
    record: capacities_rules.RulesRecord | None,
    mapped_ids: tuple[str, ...],
    schema_by_structure: dict[str, dict[str, str]],
    contract_available: bool,
) -> dict[str, Any]:
    """The one wire shape both rules routes answer with.

    ``capabilities`` describes the grammar the editor may build from the
    store's own operator/kind sets (``matches`` is absent by contract). Each
    structure carries its persisted ``active``/``draft``/``fallback_minutes``
    plus the discovered ``schema`` when the published contract supplies one;
    ``schema_available`` is the honest flag that a save for this type can be
    validated and therefore activated."""
    stored = {
        entry.structure_id: entry
        for entry in (record.structures if record is not None else ())
    }
    ordered: list[str] = list(mapped_ids)
    for structure_id in stored:
        if structure_id not in ordered:
            ordered.append(structure_id)
    structures: list[dict[str, Any]] = []
    for structure_id in ordered:
        entry = stored.get(structure_id)
        base = (
            entry.as_dict()
            if entry is not None
            else {
                "structure_id": structure_id,
                "active": None,
                "draft": None,
                "fallback_minutes": None,
            }
        )
        schema = schema_by_structure.get(structure_id)
        structures.append({
            **base,
            "mapped": structure_id in mapped_ids,
            "schema": schema or {},
            "schema_available": schema is not None,
        })
    return {
        "space_id": space_id,
        "revision": 0 if record is None else record.revision,
        "configured": space_id is not None,
        "capabilities": {
            "ops": sorted(capacities_rules.OPS),
            "value_ops": sorted(capacities_rules.VALUE_OPS),
            "presence_ops": sorted(capacities_rules.PRESENCE_OPS),
            "number_ops": sorted(capacities_rules.NUMBER_OPS),
            "date_ops": sorted(capacities_rules.DATE_OPS),
            "equality_ops": sorted(capacities_rules.EQUALITY_OPS),
            "value_kinds": sorted(capacities_rules.VALUE_KINDS),
            "number_kinds": sorted(capacities_rules.NUMBER_KINDS),
            "date_kinds": sorted(capacities_rules.DATE_KINDS),
            "matches": False,
            "schema_source": "structure_contract",
            "contract_available": contract_available,
        },
        "structures": structures,
    }


def _promote_selected_capacities(
    today: date,
    pool_items: list[dict[str, Any]],
    assigned_items: list[dict[str, Any]],
    exclusion_policy: tag_exclusions.ExclusionPolicy | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    """Promote explicitly selected Capacities pool rows onto the assigned surface.

    Shared by both digest-build callers (U3c-2). It runs immediately BEFORE
    ``build_digest``, so a promoted row passes the same tag-exclusion and
    Ignore List stages as any pool row. The hard exclusions (the tag exclusion
    policy and today's drop list) are computed here, so each one yields a
    visible ``excluded`` notice rather than a silent removal. An identity that
    is already on the assigned surface keeps its own routing. Returns
    ``(pool_items, assigned_items, warnings)``; a no-op when nothing is selected.
    """
    try:
        source = capacities_builder.read_source()
    except (capacities_builder.CapacitiesSourceStoreError, OSError):
        return pool_items, assigned_items, [_SOURCE_UNAPPLIED_WARNING]
    if source is None:
        return pool_items, assigned_items, []
    try:
        selections = capacities_selections.load_selections(source.space_id)
        rules_revision = capacities_rules.load_rules(source.space_id).revision
    except (
        capacities_selections.SelectionStoreError,
        capacities_rules.RulesStoreError,
    ):
        return pool_items, assigned_items, [_SELECTIONS_UNAPPLIED_WARNING]
    if not selections.selections:
        return pool_items, assigned_items, []

    def _is_capacities(row: dict[str, Any]) -> bool:
        return str(row.get("source") or "").strip().casefold() == "capacities"

    assigned_identities = {
        row.get("identity") for row in assigned_items if _is_capacities(row)
    }
    pending = tuple(
        record for record in selections.selections
        if record.identity not in assigned_identities
    )
    pending_identities = {record.identity for record in pending}
    candidates = [
        row for row in pool_items
        if _is_capacities(row) and row.get("identity") in pending_identities
    ]
    hard_excluded = _capacities_hard_excluded(
        today, candidates, exclusion_policy,
    )
    promoted, notices = capacities_selections.resolve_selections(
        pending,
        candidates,
        rules_revision=rules_revision,
        mapped_structures={
            structure.structure_id for structure in source.structures
        },
        hard_excluded=frozenset(hard_excluded),
    )
    for row in promoted:
        # Server-owned basis marker (U4 rule-assigned admission): this row
        # reached the assigned surface through an explicit selection record,
        # not a rule. The commit gate requires a CURRENT selection record for
        # this basis. The copy is shallow, so the cached pool row is untouched.
        row["capacities_assignment_basis"] = "selection"
    promoted_identities = {row["identity"] for row in promoted}
    pool_items = [
        row for row in pool_items
        if not (_is_capacities(row) and row.get("identity") in promoted_identities)
    ]
    warnings = [
        f"Capacities selection {notice['identity']} "
        f"{_SELECTION_NOTICE_TEXT[notice['code']]}"
        for notice in notices
    ]
    return pool_items, [*assigned_items, *promoted], warnings


#: S5: the Ignore List notice names at most this many promoted identities, the
#: same bound the Capacities review warning uses; the remainder is counted.
_IGNORE_NOTICE_MAX_IDENTITIES = 3
_IGNORE_NOTICE_MARKER = "removed by the Ignore List"


def _is_capacities_source_row(row: dict[str, Any]) -> bool:
    """A Capacities row by its source field or its ``capacities://`` path."""
    return (
        str(row.get("source") or "").strip().casefold() == "capacities"
        or str(row.get("path") or "").strip().casefold().startswith("capacities://")
    )


def _is_calendar_source_row(row: dict[str, Any]) -> bool:
    """A schedulable all-day calendar row by source or ``calendar://`` path."""
    return (
        str(row.get("source") or "").strip().casefold() == "calendar"
        or str(row.get("path") or "").strip().casefold().startswith("calendar://")
    )


def _calendar_identity_matches(
    submitted: dict[str, Any], candidate: dict[str, Any],
) -> bool:
    """A calendar row matches only on its canonical source identity.

    Never a name or a path alone: the ``calendar://<event_id>/<date>`` path and
    the ``calendar:<event_id>:<date>`` identity must agree exactly on BOTH the
    server candidate and the submitted row, and a Todoist id contradicts a
    calendar identity. The original all-day source event is never resolved.
    """
    identity = _canonical_calendar_identity(
        str(candidate.get("identity") or ""), str(candidate.get("path") or ""))
    if not identity:
        return False
    submitted_identity = _canonical_calendar_identity(
        str(submitted.get("identity") or ""), str(submitted.get("path") or ""))
    if submitted_identity != identity:
        return False
    if str(submitted.get("todoist_id") or "").strip():
        return False
    submitted_path = str(submitted.get("path") or "").strip()
    return not submitted_path or submitted_path == str(candidate.get("path") or "").strip()


def _mirror_intake_exclusions() -> tuple[set[str], list[str]]:
    """The Capacities mirror Todoist ids to hide from intake, plus warnings.

    Reads the machine-local mirror store. A malformed/unreadable store yields
    an empty exclusion set (no row is dropped on a guess) plus one loud
    warning, matching the source-boundary degrade contract: intake never
    crashes, but the operator sees the degraded state."""
    try:
        return set(capacities_mirror.excluded_task_ids()), []
    except capacities_mirror.MirrorStoreError:
        return set(), [capacities_mirror.UNREADABLE_WARNING]


def _drop_excluded_todoist_rows(
    rows: list[dict[str, Any]], excluded: set[str],
) -> list[dict[str, Any]]:
    """Drop Todoist-sourced rows whose id is an app-created Capacities mirror.

    Applied to artifact-sourced rows too, so a mirror cannot re-enter planning
    through the artifact door (and therefore the digest index)."""
    if not excluded:
        return rows
    return [
        row for row in rows
        if str(row.get("todoist_id") or "").strip() not in excluded
    ]


def _capacities_identity_matches(
    submitted: dict[str, Any], candidate: dict[str, Any],
) -> bool:
    """A Capacities row matches only on its canonical identity.

    A name or a path alone never matches, and a Todoist id on the submitted
    row contradicts a Capacities identity.
    """
    identity = str(candidate.get("identity") or "").strip()
    if not identity or str(submitted.get("identity") or "").strip() != identity:
        return False
    if str(submitted.get("todoist_id") or "").strip():
        return False
    submitted_path = str(submitted.get("path") or "").strip()
    return not submitted_path or submitted_path == str(candidate.get("path") or "").strip()


def _capacities_index_complete(row: dict[str, Any]) -> bool:
    """An indexed Capacities row names itself fully: source, canonical identity,
    object id, and space id, with the identity inside that same space."""
    identity_parts = str(row.get("identity") or "").split(":", 3)
    space_id = str(row.get("capacities_space_id") or "").strip()
    return bool(
        str(row.get("source") or "").strip().casefold() == "capacities"
        and len(identity_parts) == 4
        and identity_parts[0] == "capacities"
        and space_id
        and identity_parts[1] == space_id
        and str(row.get("capacities_id") or "").strip()
    )


def _capacities_commit_refusals(
    today: date, matches: list[tuple[dict[str, Any], dict[str, Any]]],
) -> list[str]:
    """Commit-time admission of the authorized Capacities rows, one reason each.

    A row is admitted only when its index entry is complete, its review reasons
    (if any) were acknowledged, and no hard exclusion has caught it since
    indexing. The assignment basis is read ONLY from the server index entry,
    never from the browser submission: a ``capacities_assignment_basis`` of
    ``"rule"`` marks a row the server assigned from an active stored rule, and
    such a row needs no selection record. A ``"selection"`` basis, or a LEGACY
    index row with no marker at all, still requires the identity to be selected
    in the CURRENT selection store (fail closed). The selection store and
    tag-exclusion policy are re-read here, never trusted from the index. A
    failed read raises 503 and admits nothing. Todoist and note-path rows never
    reach this function.
    """
    candidates = [
        candidate for candidate, _submitted in matches
        if _is_capacities_source_row(candidate)
    ]
    if not candidates:
        return []
    refusals: list[str] = []
    complete: list[dict[str, Any]] = []
    for candidate in candidates:
        if _capacities_index_complete(candidate):
            complete.append(candidate)
        else:
            refusals.append(
                f"{str(candidate.get('name') or '<unnamed>')!r} has no complete "
                "stable identity in the index"
            )
    if not complete:
        return refusals

    selected: dict[str, Any] = {}
    try:
        for space_id in sorted({str(row["capacities_space_id"]).strip() for row in complete}):
            for record in capacities_selections.load_selections(space_id).selections:
                selected[record.identity] = record
    except (capacities_selections.SelectionStoreError, OSError) as exc:
        raise HTTPException(
            status_code=503,
            detail=(
                "commit refused: Capacities selections could not be read "
                "— nothing was written"
            ),
        ) from exc

    exclusion_policy = _exclusion_policy_or_block()
    try:
        excluded = _capacities_hard_excluded(today, complete, exclusion_policy)
    except tag_exclusions.TagExclusionBlocked as exc:
        raise HTTPException(status_code=503, detail=exc.diagnostics) from exc

    for candidate in complete:
        name = str(candidate.get("name") or "<unnamed>")
        record = selected.get(candidate.get("identity"))
        # Read the basis from the SERVER index row; a browser-supplied value on
        # the submission is never consulted (identity match is the only
        # browser input that reaches this gate).
        basis = str(candidate.get("capacities_assignment_basis") or "").strip()
        if candidate.get("identity") in excluded:
            refusals.append(f"{name!r} is excluded since it was indexed")
        elif basis == "rule":
            # Rule/source-assigned: no selection record is required. Review
            # reasons still need an acknowledging selection record.
            if candidate.get("capacities_review_reasons") and not (
                record is not None and record.acknowledged
            ):
                refusals.append(
                    f"{name!r} is not acknowledged (it carries review reasons)"
                )
        elif record is None:
            refusals.append(
                f"{name!r} is not selected (never selected, or withdrawn "
                "since it was indexed)"
            )
        elif candidate.get("capacities_review_reasons") and not record.acknowledged:
            refusals.append(
                f"{name!r} is not acknowledged (it carries review reasons)"
            )
    return refusals


def _build_manifest_or_refuse(
    digest: dict[str, Any], sequence: Any, config: dict[str, Any] | None, *,
    time_frame: dict[str, Any],
) -> Any:
    """``shadow.build_plan_manifest`` with its refusals as a 422, not a 500.

    The manifest is built before any write, so a ``ValueError`` (for example a
    Capacities row and a Todoist row sharing a name) is a fail-closed refusal:
    nothing was written, and the reason is named for the operator.
    """
    try:
        return shadow.build_plan_manifest(
            digest, sequence, config, time_frame=time_frame)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=f"commit refused: {exc} — nothing was written",
        ) from exc


def _capacities_identities(rows: list[dict[str, Any]]) -> list[str]:
    """The Capacities identities on ``rows``, in order, each named once."""
    return list(dict.fromkeys(
        str(row["identity"])
        for row in rows
        if str(row.get("source") or "").strip().casefold() == "capacities"
        and row.get("identity")
    ))


def _ignore_list_selection_warnings(
    assigned_input: list[dict[str, Any]],
    assigned_rows: list[dict[str, Any]],
    digest_assigned: list[dict[str, Any]],
    existing: list[str],
) -> list[str]:
    """One bounded notice for promoted selections the Ignore List removed.

    ``build_digest`` applies the Ignore List after promotion and drops matching
    rows without a notice. Tag-excluded and drop-listed selections are never
    promoted, so they keep their own notice; a promoted identity missing from
    the digest's assigned surface was removed by the Ignore List. At most one
    such notice is emitted per digest build, so no identity is named twice.
    """
    if any(_IGNORE_NOTICE_MARKER in warning for warning in existing):
        return []
    before = set(_capacities_identities(assigned_input))
    promoted = [
        identity for identity in _capacities_identities(assigned_rows)
        if identity not in before
    ]
    surfaced = set(_capacities_identities(digest_assigned))
    removed = [identity for identity in promoted if identity not in surfaced]
    if not removed:
        return []
    shown = removed[:_IGNORE_NOTICE_MAX_IDENTITIES]
    remaining = len(removed) - len(shown)
    named = "; ".join(shown)
    if remaining > 0:
        named = f"{named}; +{remaining} more"
    if len(removed) == 1:
        return [f"Capacities selection {named} was promoted, then {_IGNORE_NOTICE_MARKER}"]
    return [f"Capacities selections {named} were promoted, then {_IGNORE_NOTICE_MARKER}"]


def _refresh_is_running(app_state: Any) -> bool:
    """Whether a Capacities Refresh job thread is live, without building one.

    Reads the cached coordinator off ``app.state`` directly. Deliberately not
    ``_refresh_coordinator()``: that seam can refuse with a 503 for an
    unconfigured or broken source, and a plan-inputs read must stay local.
    """
    coordinator = getattr(app_state, "refresh_coordinator", None)
    if coordinator is None or coordinator is _REFRESH_COORDINATOR_UNSET:
        return False
    is_running = getattr(coordinator, "is_running", None)
    return bool(is_running()) if callable(is_running) else False


def _last_refresh_job(read: Any) -> dict[str, Any] | None:
    """The persisted last Refresh job document for the read generation."""
    if read is None:
        return None
    return capacities_refresh.read_last_job(
        capacities_builder.refresh_state_root(), read.space_id
    )


def _capacities_intake_block(
    digest: dict[str, Any],
    coverage: capacities_intake.DirectCoverage,
    direct_warnings: list[str],
    read: capacities_builder.DirectIntakeRead | None,
) -> dict[str, Any]:
    """The additive ``capacities_intake`` block the ``direct`` intake reports.

    Token-free and read-only: the caller's single generation read (so rows and
    metadata always describe the same generation) and the selection store.
    ``state`` is ``not_configured`` (no source record), ``refresh_required``
    (no complete generation, no contract, or a damaged member),
    ``unavailable`` (warnings and no Capacities rows served), ``degraded``
    (rows served with warnings), or ``ok``. Unassigned candidates are the
    Capacities rows left on the suggested surface.
    """
    snapshot = read.snapshot if read is not None else None
    served = [
        row for row in [*digest["assigned"], *digest["suggested"]]
        if row.get("source") == "capacities"
    ]
    if read is None:
        state = "unavailable" if direct_warnings else "not_configured"
    elif snapshot is None or read.structures is None or read.unreadable:
        state = "refresh_required"
    elif direct_warnings:
        state = "degraded" if served else "unavailable"
    else:
        state = "ok"
    selected: set[str] = set()
    if read is not None:
        try:
            selections = capacities_selections.load_selections(read.space_id)
        except capacities_selections.SelectionStoreError:
            selections = None
        if selections is not None:
            selected = {record.identity for record in selections.selections}
    return {
        "mode": app_config.CAPACITIES_INTAKE_DIRECT,
        "state": state,
        "generation": snapshot.generation if snapshot is not None else None,
        "installed_at": snapshot.installed_at if snapshot is not None else None,
        "type_check_times": (
            dict(snapshot.type_check_times) if snapshot is not None else {}
        ),
        "coverage": {
            "members": coverage.members,
            "evaluated": coverage.evaluated,
            "malformed": coverage.malformed,
            "unreadable": coverage.unreadable,
        },
        "skipped": [
            {
                "identity": entry.identity,
                "title": entry.title,
                "reason": entry.reason,
                "detail": entry.detail,
                "structure_id": entry.structure_id,
                "structure_name": entry.structure_name,
                "labels": list(entry.labels),
                "updated_at": entry.updated_at,
                "hint": entry.hint,
            }
            for entry in sorted(coverage.skipped, key=lambda item: item.identity)[:50]
        ],
        "unassigned_candidates": [
            {
                "identity": row.get("identity"),
                "name": row.get("name"),
                "review_reasons": list(row.get("capacities_review_reasons") or []),
                "selected": row.get("identity") in selected,
            }
            for row in digest["suggested"]
            if row.get("source") == "capacities"
        ],
    }


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
    every surface: Todoist rows by ``todoist_id``, path rows by
    ``path``, any row by case-insensitive name. The user-editable permanent
    hide list; counts reflect the post-filter sets.

    ``exclusion_policy`` (the app-managed tag exclusion list) runs at ONE
    stage, immediately AFTER the Ignore List and BEFORE assigned sorting, pool
    dedup, ranking, and forgot-list derivation, and filters BOTH surfaces —
    an excluded task must never reach the brief, and a source Assigned marker
    must not override the policy. The stage's observability envelope rides the
    digest as ``exclusion_policy``. Ignore List matches keep their earlier
    attribution; only rows removed at this stage are reported.

    The same policy's ``todoist`` dimension runs immediately after the tag
    stage on the same surfaces: a Todoist row is dropped when its task id is
    listed OR its normalized title equals a listed title, so a recurring
    series stays excluded even when Todoist re-issues a new id. Its mirrored
    envelope rides the digest as ``todoist_exclusion_policy``.

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
    todoist_report: dict[str, Any] | None = None
    if exclusion_policy is not None:
        assigned_items, pool_items, exclusion_report = (
            tag_exclusions.apply_tag_exclusions(
                assigned_items, pool_items, exclusion_policy
            )
        )
        assigned_items, pool_items, todoist_report = (
            todoist_exclusions.apply_todoist_exclusions(
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
    if todoist_report is not None:
        digest["todoist_exclusion_policy"] = todoist_report
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
                # U4 S6: the commit boundary refuses an unacknowledged row by
                # its indexed review reasons; the rule state rides with them.
                "capacities_review_reasons", "capacities_rule",
            ):
                if key in row and row.get(key) is not None:
                    entry[key] = row[key]
            # U4 rule-assigned admission: stamp the server-owned basis so the
            # commit gate can tell a rule/source-assigned row (no selection
            # record required) from a selection-promoted row (a CURRENT
            # selection record is required). Promotion sets
            # ``capacities_assignment_basis = "selection"`` on the shallow copy
            # in ``_promote_selected_capacities``; an assigned Capacities row
            # with no marker is a server rule/source assignment. An unmarked
            # pool row earns no basis (it is not commit-eligible), and a LEGACY
            # index row written before this field existed stays unmarked and
            # fails closed at commit.
            if str(row.get("source") or "").strip().casefold() == "capacities":
                basis = str(row.get("capacities_assignment_basis") or "").strip()
                if basis not in ("rule", "selection"):
                    basis = "rule" if row.get("assigned") is True else ""
                elif basis == "rule" and row.get("assigned") is not True:
                    # "rule" is derived strictly from the server assigned flag.
                    basis = ""
                if basis:
                    entry["capacities_assignment_basis"] = basis
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
    "capacities_completion_supported", "may_overlap_mint",
})

_ROUTE_NATIVE_CLAIM_FIELDS = frozenset({
    "identity", "source", "scheduled_start", "is_recurring", "native_blocks",
    "allow_time_adjustment", "capacities_id", "capacities_space_id",
    "capacities_structure_id", "capacities_completion_supported",
    "source_fingerprint", "may_overlap_mint",
})


def _route_name(value: Any) -> str:
    """Canonical comparison form for a submitted/displayed item name."""
    return " ".join(str(value or "").strip().split()).casefold()


def _canonical_calendar_identity(identity: str, path: str) -> str | None:
    """Canonical ``calendar:<event_id>:<YYYY-MM-DD>`` for a calendar row.

    Requires the ``calendar://<event_id>/<YYYY-MM-DD>`` path AND an identity
    that agrees with it exactly. A missing, forged, or mismatched path/identity
    yields ``None`` so the row can never be authorized (a mismatch between the
    two forms is rejected rather than reconciled).
    """
    text = str(path or "").strip()
    if not text.casefold().startswith("calendar://"):
        return None
    parts = text[len("calendar://"):].split("/")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        return None
    canonical = f"calendar:{parts[0]}:{parts[1]}"
    if str(identity or "").strip() != canonical:
        return None
    return canonical


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

    calendar_path = path.casefold().startswith("calendar://")
    if source == "calendar" or calendar_path:
        if todoist_id or not calendar_path:
            return None
        canonical = _canonical_calendar_identity(identity, path)
        if canonical is None:
            return None
        return "calendar", canonical

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
    # Any other path (a retired note path) carries no source identity.
    return None


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
    index = runstate.read_digest_index(today)
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
            # ``may_overlap_mint`` is opt-in UI metadata (see #18), not a
            # source/timing identity claim: it is stripped from every
            # unmatched row rather than making an identity-less legacy row
            # fail closed by its mere presence.
            or any(
                key in row for key in _ROUTE_NATIVE_CLAIM_FIELDS
                if key != "may_overlap_mint"
            )
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
            for key in _ROUTE_TIMING_FIELDS - {
                "id", "name", "allow_time_adjustment", "may_overlap_mint",
            }:
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
            # #18: the per-row Mint overlap grant is admitted onto the
            # server-projected row ONLY for a server-matched assigned item.
            # Any matched row (not just Todoist) may carry the operator
            # grant; the exact literal True is the only authorization.
            if row.get("may_overlap_mint") is True:
                projected["may_overlap_mint"] = True
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


def _available_capacities_structures() -> list[str]:
    """Advisory list of configured Capacities structure ids from the app-owned
    source mapping record (``capacities_builder``).

    This is UI metadata for the settings drawer, so it is deliberately
    fail-soft: a missing, malformed, or unreadable record yields ``[]`` and
    never changes the settings route's own error behaviour. The builder still
    reports a malformed record loudly when an adapter is built. No adapter is
    constructed, no credential is read, and no provider is contacted here.
    """
    try:
        record = capacities_builder.read_source()
    except (capacities_builder.CapacitiesSourceStoreError, OSError) as exc:
        print(f"capacities source read failed: {exc}", file=sys.stderr)
        return []
    if record is None:
        return []
    return sorted({structure.structure_id for structure in record.structures})


def _capacities_structure_titles(
    available_structures: list[str]
) -> dict[str, str]:
    """Last observed display titles for the mapped Capacities structures.

    The settings drawer shows mapped structures only, so the returned map is
    keyed exactly by ``available_structures``: a cached title for an unmapped
    structure is dropped rather than offered as a phantom row, and a mapped
    structure with no cached title is simply absent (the drawer falls back to
    the structure id).

    The read uses the same (mapping-record ``space_id``, builder ``base_url``)
    namespace pair the structure observer writes
    under, so another space or provider base URL can never leak a
    title. ``read_titles`` is machine-local and fail-soft: a missing,
    unreadable, corrupt, or unsupported cache yields ``{}`` and never raises,
    so a cache problem can never change the settings route's status code or
    its other fields. No adapter is constructed, no credential is read, and
    no provider is contacted.
    """
    try:
        record = capacities_builder.read_source()
    except (capacities_builder.CapacitiesSourceStoreError, OSError) as exc:
        print(f"capacities source read failed: {exc}", file=sys.stderr)
        return {}
    if record is None:
        return {}
    titles = capacities_structure_titles.read_titles(
        record.space_id,
        capacities_builder.CapacitiesBuilderConfig().base_url,
    )
    return {
        structure_id: titles[structure_id]
        for structure_id in available_structures
        if structure_id in titles
    }


def _exclusion_policy_or_block() -> tag_exclusions.ExclusionPolicy:
    """Read the tag-exclusion policy for a planning surface, failing closed.

    Malformed or unreadable storage blocks planning with a structured 503
    rather than silently evaluating an empty policy — an empty brief would be
    indistinguishable from "nothing is excluded". The settings routes keep
    their own storage error semantics; this is the planning boundary only.
    """
    try:
        read = exclusion_settings.read_settings()
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
        # Bounded client-safe message — no absolute cache path.
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


class LooseEndRequest(BaseModel):
    date: str  # YYYY-MM-DD of the unconfirmed Live block
    id: str
    done: StrictBool  # True = Did it, False = Dismiss


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


class AnchoredStartSaveRequest(BaseModel):
    """/config/anchored-blocks/start body: one config anchored block by name
    plus its new start (``HH:MM`` 24h or ``H:MM AM/PM``). Time validity is
    decided by ``config_write.normalize_start`` so the route can return a
    bounded 422 with the writer's own message."""

    block: str
    start: str


class RuntimeActionRequest(BaseModel):
    """/runtime-actions body (T20): one verb against one committed plan item.

    ``target`` is the plan-item NAME — resolution to Todoist ids / owned
    event ids / source paths happens server-side from today's runstate
    ``plan_manifest``, so the client can never address an artifact the app
    didn't commit."""

    verb: str
    target: str
    args: dict[str, Any] = Field(default_factory=dict)


class RuntimeBatchItem(BaseModel):
    """One item of a /runtime-batches body (B5): the same ``verb``/``target``/
    ``args`` tuple /runtime-actions accepts, so a batch queues registered verbs
    without inventing a second vocabulary."""

    verb: str
    target: str
    args: dict[str, Any] = Field(default_factory=dict)


class RuntimeBatchRequest(BaseModel):
    """/runtime-batches body (B5): an ordered list of runtime actions applied
    as one journaled batch. ``items`` is required — a missing list is bad input
    (422), never a silent empty batch."""

    items: list[RuntimeBatchItem]


class DaySetupRequest(BaseModel):
    """/day-setup body: the Phase-1 confirm payload. Session/day-scoped —
    persists to the dated run-state note, NEVER to the config files (locked
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
    anchored: list[dict[str, Any]] | None = None  # [{id, on, skip_today, time[, start, calendar_start_at_edit]}]
    captures: dict[str, Any] | None = None  # {intention, megan_nicety, stoic_intention}
    # U5 additive prompt opt-ins: an undated bool map plus the expected store
    # revision for optimistic concurrency. Omitted -> no opt-in write.
    optins: dict[str, StrictBool] | None = None
    optins_revision: StrictInt | None = None
    day_preset: str | None = None        # dated preset override (T18b.2)
    work_allotment_minutes: StrictInt | None = None  # dated Mint allotment (T18b.2)
    micro_adventure: dict[str, Any] | None = None  # T19 dated Live override; null clears to auto
    # Additive per-day habit overrides: per-item {on, minutes} keyed by the
    # habit's task id or name, plus an aggregate ``total`` override. Full
    # replace per POST; explicit null clears; omitted preserves. Local-only —
    # this never reaches a Todoist or Calendar write path.
    habits: dict[str, Any] | None = None

    @field_validator("habits")
    @classmethod
    def validate_habits(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        """Bound the additive habit-override block (T19 follow-on).

        ``items`` maps a bounded id/name key to ``{on?: bool,
        minutes?: int|null}``; ``total`` is ``null`` or the same entry shape.
        An entry's ``minutes`` must be an integer 0..480. Unknown extra keys
        are ignored; an omitted/None value keeps old payloads valid."""
        if value is None:
            return value
        if not isinstance(value, dict):
            raise ValueError("habits must be an object")
        items = value.get("items")
        if items is not None:
            if not isinstance(items, dict):
                raise ValueError(
                    "habits.items must be an object keyed by habit id or name"
                )
            for key, entry in items.items():
                if (not isinstance(key, str) or not key.strip()
                        or len(key) > artifact_source.HABIT_TEXT_MAX):
                    raise ValueError(
                        "habits.items keys must be non-empty bounded strings"
                    )
                cls._validate_habit_override(entry, "habits.items")
        total = value.get("total")
        if total is not None:
            cls._validate_habit_override(total, "habits.total")
        return value

    @staticmethod
    def _validate_habit_override(entry: Any, label: str) -> None:
        if not isinstance(entry, dict):
            raise ValueError(f"{label} must be an object")
        on = entry.get("on")
        if on is not None and not isinstance(on, bool):
            raise ValueError(f"{label}.on must be a boolean")
        minutes = entry.get("minutes")
        if minutes is None:
            return
        if isinstance(minutes, bool) or not isinstance(minutes, int):
            raise ValueError(f"{label}.minutes must be an integer or null")
        if minutes < 0 or minutes > artifact_source.HABIT_MINUTES_MAX:
            raise ValueError(
                f"{label}.minutes must be between 0 and "
                f"{artifact_source.HABIT_MINUTES_MAX}"
            )

    @field_validator("work_allotment_minutes")
    @classmethod
    def validate_work_allotment_minutes(cls, value: int | None) -> int | None:
        if value is not None and (value < 0 or value % 15 != 0):
            raise ValueError(
                "work_allotment_minutes must be a nonnegative integer "
                "divisible by 15"
            )
        return value

    @field_validator("anchored")
    @classmethod
    def validate_anchored_start(
        cls, value: list[dict[str, Any]] | None
    ) -> list[dict[str, Any]] | None:
        """An optional calendar-row ``start`` edit must be a real clock time.

        The calendar lanes on the Set up day strip persist a local-only start
        override plus the calendar's start at edit time. ``start`` is
        validated like the anchor time (any ``HH:MM``/``H:MM AM|PM`` within
        00:00-23:59, not just 15-minute quarters); ``calendar_start_at_edit``
        is stored verbatim. Omitted/None keeps old payloads valid."""
        if value is None:
            return value
        for entry in value:
            if not isinstance(entry, dict):
                continue
            raw = entry.get("start")
            if raw is None:
                continue
            if time_engine.to_hhmm(raw) is None:
                raise ValueError(
                    "anchored start must be a valid HH:MM clock time"
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
    #: Settings-declared ``Assigned On`` marker: ``{structure_id:
    #: property_id}`` naming a raw Capacities DATE/DATETIME property. When a
    #: structure is declared here, only that property decides
    #: ``source_assigned`` (the boolean declaration is ignored for it), and
    #: the value's civil date in the app's local timezone must fall on or
    #: before the read's logical day; a future date is not assigned yet.
    #: Omission is a full replacement to the empty map.
    #: The marker is READ-ONLY: TDTB never writes the Capacities property.
    assigned_on_structures: dict[str, StrictStr] = Field(default_factory=dict)

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

    @field_validator("assigned_on_structures")
    @classmethod
    def _assigned_on_structures_valid(
        cls, value: dict[str, str]
    ) -> dict[str, str]:
        # Same 422-before-any-file-access contract as ``assigned_structures``.
        # Property-type validation is deliberately absent here: the save route
        # has no discovery data, so a non-date property is reported at read
        # time as one bounded, content-free warning instead.
        try:
            return capacities_settings.canonical_assignment_declarations(
                value, key="assigned_on_structures"
            )
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


class TodoistExclusionEntryRequest(BaseModel):
    """One persistent Todoist task exclusion in the save body.

    ``id`` is the Todoist task id, ``name`` the bounded non-blank display
    title, and ``added_at`` a bounded ISO-8601 timestamp — the same strictness
    the store re-checks before any file access. A numeric id is rejected
    rather than coerced.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    added_at: str

    @field_validator("id")
    @classmethod
    def _id_valid(cls, value: str) -> str:
        try:
            return exclusion_settings.canonical_todoist_id(value)
        except ValueError as exc:
            raise ValueError(f"invalid Todoist task id: {value!r}") from exc

    @field_validator("name")
    @classmethod
    def _name_valid(cls, value: str) -> str:
        try:
            return exclusion_settings.canonical_todoist_name(value)
        except ValueError as exc:
            raise ValueError(f"invalid Todoist task name: {value!r}") from exc

    @field_validator("added_at")
    @classmethod
    def _added_at_valid(cls, value: str) -> str:
        try:
            return exclusion_settings.canonical_todoist_added_at(value)
        except ValueError as exc:
            raise ValueError(f"invalid added_at timestamp: {value!r}") from exc


class ExclusionDimensionsRequest(BaseModel):
    """The exclusion dimension object for the save body.

    Closed on purpose: only ``tags`` and ``todoist`` are supported. An
    unknown dimension (``labels``, ...) is rejected here rather than silently
    accepted — adding one later must be an explicit schema change. ``todoist``
    defaults to empty so a legacy tags-only payload stays valid; the save is a
    full replacement, so an omitted dimension clears it.
    """

    model_config = ConfigDict(extra="forbid")

    tags: list[TagExclusionEntryRequest]
    todoist: list[TodoistExclusionEntryRequest] = Field(
        default_factory=list,
        max_length=exclusion_settings.MAX_TODOIST_EXCLUSIONS,
    )

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

    @field_validator("todoist")
    @classmethod
    def _todoist_unique(
        cls, value: list[TodoistExclusionEntryRequest]
    ) -> list[TodoistExclusionEntryRequest]:
        seen_ids: set[str] = set()
        seen_names: set[str] = set()
        for entry in value:
            if entry.id in seen_ids:
                raise ValueError("duplicate Todoist exclusion id")
            normalized = exclusion_settings.normalize_todoist_name(entry.name)
            if normalized in seen_names:
                raise ValueError("duplicate Todoist exclusion name")
            seen_ids.add(entry.id)
            seen_names.add(normalized)
        return value


class ExclusionSettingsSaveRequest(BaseModel):
    """Full-replacement body for POST /settings/exclusions/save.

    ``expected_revision`` drives the server-side stale-write check; ``version``
    and the new ``revision`` are server-owned and deliberately absent."""

    model_config = ConfigDict(extra="forbid")

    expected_revision: StrictInt
    exclusions: ExclusionDimensionsRequest

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


class CapacitiesSourceDiscoverRequest(BaseModel):
    """Request body for POST /settings/capacities/source/discover.

    Closed and strictly typed like the save body — unknown keys are rejected
    and ``space_id`` must be a literal JSON string — but the structure list is
    deliberately absent: discovery reads the provider's structures, it never
    accepts them. ``space_id`` follows the mapping schema's identifier rule
    (non-empty and whitespace-free)."""

    model_config = ConfigDict(extra="forbid")

    space_id: StrictStr

    @field_validator("space_id")
    @classmethod
    def _usable_space_id(cls, value: str) -> str:
        if not value or any(char.isspace() for char in value):
            raise ValueError("space_id must be a non-empty whitespace-free id")
        return value


class CapacitiesRefreshStartRequest(BaseModel):
    """Request body for POST /capacities/refresh/start.

    Closed and strictly typed: unknown keys are rejected, ``mode`` is one of
    the two job kinds, and a supplied ``scope`` must be a non-blank literal
    string (the whole-scope default is ``all``). A malformed body is a 422.
    """

    model_config = ConfigDict(extra="forbid")

    mode: Literal["refresh", "rescan"]
    scope: StrictStr = "all"

    @field_validator("scope")
    @classmethod
    def _usable_scope(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("scope must be a non-blank string")
        return value


class CapacitiesSelectionEntry(BaseModel):
    """One identity to select. ``acknowledge`` is the operator's R32 flag."""

    model_config = ConfigDict(extra="forbid")

    identity: StrictStr
    acknowledge: StrictBool = False


class CapacitiesSelectionsSaveRequest(BaseModel):
    """Request body for POST /capacities/selections.

    Closed and strictly typed. ``rules_revision`` is server-owned and is
    rejected at every depth, so it is never read from the body. Identity
    validity and candidacy are decided by the route, not by this model.
    """

    model_config = ConfigDict(extra="forbid")

    expected_revision: StrictInt
    select: list[CapacitiesSelectionEntry] = Field(default_factory=list)
    deselect: list[StrictStr] = Field(default_factory=list)

    @field_validator("expected_revision")
    @classmethod
    def _revision_nonnegative(cls, value: int) -> int:
        if value < 0:
            raise ValueError("expected_revision must be a nonnegative integer")
        return value


class CapacitiesRuleSaveRequest(BaseModel):
    """Request body for POST /capacities/rules.

    Closed and strictly typed at the boundary: ``version`` and the new
    ``revision`` are server-owned and never read from the body. The predicate
    itself is free-form JSON — ``capacities_rules`` validates its grammar and
    property references, so a syntactically invalid predicate is stored as a
    draft, not rejected as a request error."""

    model_config = ConfigDict(extra="forbid")

    structure_id: StrictStr
    rule: dict[str, Any]
    fallback_minutes: StrictInt | None = None
    expected_revision: StrictInt

    @field_validator("structure_id")
    @classmethod
    def _usable_structure_id(cls, value: str) -> str:
        if not value or value != value.strip() or any(c.isspace() for c in value):
            raise ValueError("structure_id must be a non-empty whitespace-free id")
        return value

    @field_validator("fallback_minutes")
    @classmethod
    def _fallback_nonnegative(cls, value: int | None) -> int | None:
        if value is not None and value < 0:
            raise ValueError("fallback_minutes must be a nonnegative integer or null")
        return value

    @field_validator("expected_revision")
    @classmethod
    def _revision_nonnegative(cls, value: int) -> int:
        if value < 0:
            raise ValueError("expected_revision must be a nonnegative integer")
        return value


def _capacities_catalog_payload(structures: Any) -> list[dict[str, Any]]:
    """Serialize a discovery catalog into the pinned editor wire shape.

    Only ids, display strings, types, the writability flag, and label options
    cross the boundary; the provider payload never does. The wire contract
    restates the structure-id order the builder's catalog already applies, so
    the endpoint answers sorted regardless of the seam that supplied it."""

    def _property(prop: Any) -> dict[str, Any]:
        return {
            "property_id": prop.property_id,
            "title": prop.title,
            "type": prop.type,
            "writable": prop.writable,
            "label_options": [
                {"id": option.id, "title": option.title}
                for option in prop.label_options
            ],
        }

    return [
        {
            "structure_id": structure.structure_id,
            "title": structure.title,
            "properties": [_property(prop) for prop in structure.properties],
        }
        for structure in sorted(structures, key=lambda item: item.structure_id)
    ]


async def _capacities_source_discover_request(
    request: Request,
) -> CapacitiesSourceDiscoverRequest:
    """Strictly parse and validate the discovery body, or answer 422.

    Same HTTP boundary as the save body: duplicate JSON keys are rejected
    before validation and every invalid body answers the fixed
    ``{detail: {code, message}}`` shape instead of FastAPI's error list."""
    try:
        payload = capacities_builder.parse_source_json(await request.body())
        return CapacitiesSourceDiscoverRequest.model_validate(payload)
    except (ValueError, ValidationError) as exc:
        raise _capacities_source_invalid_body() from exc


# Day Setup keys /plan-inputs echoes back from run state (the UI's read side).
# G24: per-day billed-SDK-call cap, enforced against the persistent runstate
# ledger (billed_calls) — the same 4-call bound RunContext asserts per run.
BILLED_CAP = judgment.MAX_CALLS_PER_RUN

_DAY_SETUP_KEYS = ("anchor", "eod", "buffering", "schedulable", "anchored",
                   "re_included", "intention", "megan_nicety", "stoic_intention",
                   "day_preset", "work_allotment_minutes", "habits")


def _read_today_runstate(today: date) -> dict[str, Any]:
    """Today's exact-date run-state note as a dict; missing/unparseable → {}."""
    rs_path = runstate.runstate_read_path(today)
    if not rs_path.is_file():
        return {}
    return gather._extract_json_block(
        rs_path.read_text(encoding="utf-8", errors="replace")
    ) or {}


# U5: the morning prompts are private local runtime state (prompt_state.py).
# The runstate skeleton still carries these key names for compatibility, but
# they are INERT — reads drop them and overlay today's exact-logical-day local
# drafts, and the /day-setup writer never writes them.
_PROMPT_KEYS = prompt_state.PROMPT_KEYS
_PROMPT_READ_WARNING = "Prompt drafts could not be read; showing empty prompts."
# U5 read-side: a failed opt-in read is NOT the same as an all-false store. The
# /plan-inputs metadata is omitted (the UI then shows preferences unavailable)
# and this bounded, content-free warning is surfaced instead of a fabricated
# all-false store that would overwrite saved preferences.
_PROMPT_OPTINS_READ_WARNING = (
    "Prompt opt-ins could not be read; preferences are shown as unavailable."
)
# Fields that make a /day-setup POST an explicit Day Setup confirmation. A
# payload carrying ONLY prompt content (captures / opt-ins) updates the local
# prompt stores without confirming the day.
_NON_PROMPT_SETUP_FIELDS = frozenset({
    "anchor", "eod", "buffering", "schedulable", "anchored",
    "day_preset", "work_allotment_minutes", "micro_adventure", "habits",
})
_PROMPT_ONLY_FIELDS = frozenset({"captures", "optins", "optins_revision"})


def _read_prompt_drafts(today: date) -> tuple[dict[str, str], list[str]]:
    """Today's local prompt drafts + bounded warnings.

    A failed read never raises into a route: malformed/unreadable local storage
    degrades to empty prompts, content-free (no prompt text reaches a response
    or log), exactly as an absent file does."""
    try:
        return dict(prompt_state.load_drafts(today.isoformat()).drafts), []
    except prompt_state.PromptStateError:
        return {}, [_PROMPT_READ_WARNING]


def _day_setup_with_prompts(
    state: dict[str, Any], today: date
) -> tuple[dict[str, Any], list[str]]:
    """Day Setup dict with the legacy prompt keys made INERT.

    Legacy runstate ``intention``/``megan_nicety``/``stoic_intention`` values
    are dropped (never echoed) and replaced by today's exact-logical-day local
    prompt_state drafts. Non-prompt keys are unchanged."""
    setup = {k: v for k, v in state.items()
             if k in _DAY_SETUP_KEYS and v not in ("", None)}
    for key in _PROMPT_KEYS:
        setup.pop(key, None)
    drafts, warnings = _read_prompt_drafts(today)
    for key, text in drafts.items():
        if text:
            setup[key] = text
    return setup, warnings


def _read_day_setup(today: date) -> tuple[dict[str, Any], list[str]]:
    """Today's Day Setup (runstate + local prompt drafts) + read warnings."""
    return _day_setup_with_prompts(_read_today_runstate(today), today)


def _read_prompt_optins_metadata() -> tuple[dict[str, Any] | None, list[str]]:
    """Top-level ``/plan-inputs`` opt-in metadata, or ``None`` when unreadable.

    The frontend's ``projectPromptOptins`` treats an absent block as
    ``available: false`` and never assumes false, so a malformed/unreadable
    local store must omit the block and add one bounded, content-free warning
    instead of reporting an all-false store (which would overwrite saved
    preferences). A clean read — including an absent file, the all-false
    default — is exposed truthfully."""
    try:
        record = prompt_state.load_optins()
    except prompt_state.PromptStateError:
        return None, [_PROMPT_OPTINS_READ_WARNING]
    return {"optins": dict(record.optins), "revision": record.revision}, []


def _save_prompt_drafts(today: date, captures: dict[str, Any] | None) -> None:
    """Persist a captures patch into today's local draft store.

    A PATCH: an omitted key preserves, an explicit empty string clears. Typed,
    content-free failures: 422 invalid input, 409 malformed existing bytes
    (preserved), 500 unreadable/unwritable (preserved)."""
    if captures is None:
        return
    try:
        prompt_state.save_drafts(day=today.isoformat(), patch=captures)
    except prompt_state.PromptValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "prompt_drafts_invalid", "message": str(exc)},
        ) from exc
    except prompt_state.PromptFormatError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "prompt_drafts_storage_error",
                "message": (
                    "Prompt draft storage is malformed or unsupported; "
                    "the existing file was preserved."
                ),
            },
        ) from exc
    except prompt_state.PromptStateError as exc:
        print(f"prompt drafts save failed: {exc}", file=sys.stderr)
        raise HTTPException(
            status_code=500,
            detail={
                "code": "prompt_drafts_storage_error",
                "message": (
                    "Prompt drafts could not be saved; "
                    "the existing file was preserved."
                ),
            },
        ) from exc


def _save_prompt_optins(
    body: DaySetupRequest,
) -> tuple[dict[str, bool] | None, int | None, list[str]]:
    """Persist the additive opt-in map with optimistic concurrency.

    Returns the persisted ``(map, revision, warnings)`` to echo. With no
    opt-ins in the body, echoes the current local store through the metadata
    reader: an unreadable store yields ``(None, None, [warning])`` so the echo
    omits the opt-in block (prefs stay unavailable) instead of fabricating an
    all-false store that would overwrite saved preferences. A missing revision
    is a 422 and a stale revision is a 409 carrying both revisions. Messages
    are content-free."""
    if body.optins is None:
        metadata, warnings = _read_prompt_optins_metadata()
        if metadata is None:
            return None, None, warnings
        return dict(metadata["optins"]), int(metadata["revision"]), warnings
    if body.optins_revision is None:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "prompt_optins_invalid",
                "message": "optins_revision is required when optins is set",
            },
        )
    try:
        saved = prompt_state.save_optins(
            expected_revision=body.optins_revision, optins=body.optins,
        )
    except prompt_state.PromptOptinConflict as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "prompt_optins_conflict",
                "message": (
                    "Prompt opt-ins changed since they were read; "
                    "reload and retry."
                ),
                "expected_revision": exc.expected_revision,
                "current_revision": exc.current_revision,
            },
        ) from exc
    except prompt_state.PromptValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "prompt_optins_invalid", "message": str(exc)},
        ) from exc
    except prompt_state.PromptFormatError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "prompt_optins_storage_error",
                "message": (
                    "Prompt opt-in storage is malformed or unsupported; "
                    "the existing file was preserved."
                ),
            },
        ) from exc
    except prompt_state.PromptStateError as exc:
        print(f"prompt optins save failed: {exc}", file=sys.stderr)
        raise HTTPException(
            status_code=500,
            detail={
                "code": "prompt_optins_storage_error",
                "message": (
                    "Prompt opt-ins could not be saved; "
                    "the existing file was preserved."
                ),
            },
        ) from exc
    return dict(saved.optins), saved.revision, []


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


def _micro_adventure_state(
    sections: dict[str, Any], today: date, todoist_c: Any = None,
) -> tuple[dict[str, Any], Any]:
    """Read-only micro-adventure contract (T19 / locked decision 25): pool +
    rotation from config, history from the state-dir log, prior-entry resolution,
    deterministic LRU selection. Returns (JSON-safe state, done_update) —
    done_update is a HistoryEntry flushed to the log only in the commit path.
    Degrades to a no-pick state on any failure; never raises."""
    try:
        section = sections.get("Micro-Adventures") if isinstance(sections, dict) else None
        pool = micro_adventure.parse_pool(section)
        window = micro_adventure.exclude_window_days(section)
        # S3: the published Capacities ideas block extends the config pool. The
        # artifact is read ONLY when the extension is enabled AND configured, so
        # the default (disabled) path does no extra I/O and keeps today's pool.
        # Any non-ok status keeps the static pool plus one fixed warning; the
        # selection contract (failure => no pick, never an exception) is intact.
        caps = micro_adventure.capacities_pool(section, None, datetime.now())
        if caps.status == micro_adventure.CAPACITIES_STATUS_UNAVAILABLE:
            ideas_block, _ideas_warnings = artifact_source.load_micro_ideas()
            if ideas_block is not None:
                caps = micro_adventure.capacities_pool(
                    section, ideas_block, datetime.now()
                )
        if caps.status == micro_adventure.CAPACITIES_STATUS_OK:
            pool = micro_adventure.merge_pool(pool, caps)
            pool_source = "config+capacities"
            pool_warnings = list(caps.warnings)
        else:
            pool_source = "config"
            pool_warnings = (
                []
                if caps.status == micro_adventure.CAPACITIES_STATUS_DISABLED
                else [
                    "Capacities micro-adventure ideas were not used; the config "
                    "micro-adventure pool is in effect."
                ]
            )
        history = micro_adventure.read_history(micro_adventure.history_path())
        resolution = micro_adventure.resolve_prior(
            history,
            today=today,
            todoist_completed=_todoist_completed_probe(todoist_c),
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
            "pool_source": pool_source,
            "pool_warnings": pool_warnings,
        }
        return state, resolution.done_update
    except Exception:  # noqa: BLE001 — LD25: any failure degrades to a plain Live block
        return {"auto_pick": None, "live_pool": [], "streak": 0,
                "pending_confirm": None, "pool_source": "config",
                "pool_warnings": []}, None


def _ensure_micro_adventure(
    config: dict[str, Any], today: date,
) -> dict[str, Any]:
    """Server-authoritative micro_adventure merge for the commit paths: the
    dated runstate override wins; otherwise the deterministic auto-pick. Never
    trusts the client-echoed config alone (LD25: the app, not the client,
    owns selection)."""
    if config.get("micro_adventure"):
        return config
    micro = _read_today_runstate(today).get("micro_adventure")
    if not micro:
        result = config_reader.read_config()
        sections: dict[str, Any] = (
            dict(result.config.sections) if result.config is not None else {}
        )
        state, _ = _micro_adventure_state(sections, today, None)
        micro = state["auto_pick"]
    if micro:
        return {**config, "micro_adventure": micro}
    return config


def _append_micro_adventure_history(
    report: Any, config: dict[str, Any], intents: list[Any], today: date,
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
        log_path = micro_adventure.history_path()
        history = micro_adventure.read_history(log_path)
        resolution = micro_adventure.resolve_prior(
            history, today=today,
            todoist_completed=None,  # commit-time flush uses checkbox only
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


def _override_minutes(entry: Any) -> int | None:
    """A dated override's ``minutes`` as a nonnegative int, else ``None``."""
    if not isinstance(entry, dict):
        return None
    minutes = entry.get("minutes")
    if isinstance(minutes, bool) or not isinstance(minutes, int):
        return None
    return max(0, minutes)


def _habit_override_for(overrides: dict[str, Any], item: Any) -> Any:
    """The dated override entry for an item, matched by task id then name."""
    if not isinstance(item, dict):
        return None
    for key in (item.get("id"), item.get("content")):
        if isinstance(key, str) and key and key in overrides:
            return overrides[key]
    return None


def _habits_segment(
    habits: dict[str, Any], override: Any,
) -> tuple[int, str]:
    """Habits capacity blocks + legend note, honouring per-day overrides.

    When the artifact carries ``items``, the segment sums each OUTSTANDING
    habit's minutes with its dated override applied (``on: false`` skips it;
    a set ``minutes`` replaces its duration). When no items exist the aggregate
    ``est_minutes`` is used, still honouring the dated ``total`` override.
    Overrides keyed to ids/names not in ``items`` are ignored (they still
    round-trip in the dated note). The base note text is unchanged; a
    ``· N skipped`` / ``· M adjusted`` suffix is appended only when an override
    is actually in play."""
    done = habits.get("done", 0)
    outstanding = habits.get("outstanding", 0)
    items = habits.get(artifact_source.HABITS_ITEMS_KEY)
    ov = override if isinstance(override, dict) else {}
    ov_items = ov.get("items") if isinstance(ov.get("items"), dict) else {}
    ov_total = ov.get("total") if isinstance(ov.get("total"), dict) else None
    skipped = adjusted = 0
    if isinstance(items, list) and items:
        total_minutes = 0
        for item in items:
            entry = _habit_override_for(ov_items, item)
            if isinstance(entry, dict) and entry.get("on") is False:
                skipped += 1
                continue
            minutes = _override_minutes(entry)
            if minutes is not None:
                total_minutes += minutes
                adjusted += 1
            else:
                total_minutes += int(
                    (item.get("minutes") if isinstance(item, dict) else 0) or 0
                )
    else:
        total_minutes = int(habits.get("est_minutes") or 0)
        if ov_total is not None:
            if ov_total.get("on") is False:
                total_minutes = 0
                skipped += 1
            elif _override_minutes(ov_total) is not None:
                total_minutes = _override_minutes(ov_total)
                adjusted += 1
    note = f"habits: {done} done · {outstanding} left"
    flags = []
    if skipped:
        flags.append(f"{skipped} skipped")
    if adjusted:
        flags.append(f"{adjusted} adjusted")
    if flags:
        note += " · " + " · ".join(flags)
    return _blocks_of_minutes(total_minutes), note


def _duration_minutes(dur: Any) -> int | None:
    """Delegates to time_engine.duration_minutes (T22 promotion — one parser
    shared with shadow's Step E anchored parity)."""
    return time_engine.duration_minutes(dur)


# T4 (cockpit-overhaul): contract-defined per-type duration fields — press
# rows carry duration_min (minutes).
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


def _assigned_duration_tag(
    item: dict[str, Any], *, warn: bool = True
) -> tuple[str | None, int | None]:
    """Fail-open duration-label step for the assigned-row blocks ladder.

    Reuses ``duration_memory.duration_tag_resolution`` — the same recognizer
    and collision rule ``resolve_duration`` uses — instead of re-deriving
    label semantics here. Returns ``(label, minutes)`` when a label resolves,
    and ``(None, None)`` when no label resolves or the recognized label
    encodes no minutes (``🐢 Multi-hour`` is metadata, not a source).

    Deliberate divergence from :func:`duration_memory.resolve_duration`,
    which raises ``ValueError`` on a same-precedence collision with distinct
    minutes (``dur30`` + ``dur45``): there the raise stays, because the
    resolver feeds a user-facing save. This ladder runs inside
    ``build_digest`` via :func:`resolve_assigned_blocks`, where raising would
    fail the whole ``/plan-inputs`` response over one mislabelled task, so it
    fails OPEN — the row gets no label resolution and falls through to
    native/preset/type/1 block — and emits one short stderr line naming only
    the conflicting labels. ``warn=False`` is for the digest site's
    provenance-only call, which must not duplicate that diagnostic.
    """
    try:
        return duration_memory.duration_tag_resolution(item)
    except ValueError as exc:
        if warn:
            print(
                f"duration-label step ignored conflicting labels: {exc}",
                file=sys.stderr,
            )
        return None, None


def resolve_assigned_blocks(
    item: dict[str, Any],
    presets: list[dict[str, Any]],
    fm: dict[str, Any] | None = None,
) -> float | int:
    """Assigned-row duration precedence: locked decision 14 as amended by
    the round-2 duration-label decision — deterministic duration label
    (``🏃‍♂️ Hour``, ``🍅 Half-hour``, ``🚀 10min``, ``dur<N>``) →
    Todoist-native duration → name-matched Presets row → contract-defined
    type field (press duration_min) → 1 block. Explicit zero from a matched
    source stays 0 (background rows); only absent/unparseable falls through.
    Pure — no source writes, no schema fields, no session-override handling
    (today-only edits are client state, locked decision 14).

    The label step sits before native per frozen item 11 (remembered → tag →
    native → preset → type → default). Remembered is applied by the caller
    after this ladder (:func:`duration_memory.apply_remembered_overlay`), so
    it still wins. See :func:`_assigned_duration_tag` for the fail-open
    collision divergence from :func:`duration_memory.resolve_duration`.
    """
    _label, tag_minutes = _assigned_duration_tag(item)
    if tag_minutes is not None:
        return _blocks_of_minutes(tag_minutes)
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
    habits_blk, habits_note = _habits_segment(habits, day_setup.get("habits"))
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
        habits_note=habits_note,
        work_busy=work_busy_blk,
        work_overflow=work_overflow_blk,
    )
    return frame, cap


def create_app() -> FastAPI:
    """Build the TDTB FastAPI app. Every store lives in
    the app home (``app_config.app_home()``)."""

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
    app.state.token = secrets.token_urlsafe(32)
    # T02: /version fingerprint paths — the running process's own repo root
    # and the committed cockpit dir. Tests override these to isolated
    # fixtures; the route never touches any state-bearing path.
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
    # A1 fix: the calendar store has its OWN seam because it must survive in
    # artifact mode — calendar, habits and anchored blocks stay on their
    # current readers whether or not the Todoist/Capacities live clients are
    # constructed. Callable [[Path, dict], store_like|None]; None (offline) →
    # no store → degrade warnings, exactly like build_read_clients. The
    # real-server module bottom swaps in build_real_calendar_store.
    app.state.build_calendar_store = None
    # Capacities stays opt-in by configuration, not by code: the production
    # builder returns None when no app-owned mapping record exists, so an
    # unconfigured machine sees no Capacities source and no warning, while a
    # configured-but-broken one (malformed record, or a mapping record with no
    # usable credential) raises and surfaces as a source warning instead of
    # silently ingesting nothing. Tests still inject fakes here.
    app.state.build_capacities_adapter = build_real_capacities_adapter
    # U2: the direct-refresh coordinator is a SEPARATE opt-in seam because it
    # needs its own machine-local store, credential, and single-flight job —
    # the same lazy, cache-once contract as the adapter seam above. Tests
    # inject a Callable[[Path, dict], coordinator|None] here; None from the
    # builder means "no Capacities source configured".
    app.state.build_refresh_coordinator = build_real_refresh_coordinator
    app.state.refresh_coordinator = _REFRESH_COORDINATOR_UNSET
    app.state.refresh_coordinator_revision = _REFRESH_COORDINATOR_UNSET
    app.state.refresh_coordinator_space_id = _REFRESH_COORDINATOR_UNSET
    #: Serializes the seam's check-build-assign so two racing starts cannot
    #: each build an instance (the loser cached last would not own the live
    #: job and could report it as interrupted).
    app.state.refresh_coordinator_lock = threading.Lock()
    # G25: in-flight guard on POST /commit?mode=live — two racing live commits
    # both pass check-before-write against the same snapshot and double-write.
    app.state.live_commit_lock = threading.Lock()

    def require_token(x_tdtb_token: str | None = Header(default=None)) -> None:
        if not x_tdtb_token or not secrets.compare_digest(x_tdtb_token, app.state.token):
            raise HTTPException(status_code=403, detail="missing or invalid X-TDTB-Token")

    def _ranking_order() -> list[str]:
        result = config_reader.read_config()
        if result.config is not None:
            value = result.config.get_ranking_criterion("within_tier_sort").value
            if isinstance(value, str):
                return [p.strip() for p in value.split(",") if p.strip()]
            if isinstance(value, list):
                return value
        return list(config_reader.FALLBACK_RANKING_CRITERIA["within_tier_sort"])

    # -- G24: persistent billed-call ledger ----------------------------------

    def _billed_spent(today: date) -> int:
        state = runstate.read_runstate(today) or {}
        try:
            return int(state.get("billed_calls") or 0)
        except (TypeError, ValueError):
            return 0

    def _require_billed_budget(today: date) -> None:
        spent = _billed_spent(today)
        if spent >= BILLED_CAP:
            raise HTTPException(
                status_code=429,
                detail=f"billed budget spent ({spent}/{BILLED_CAP}) for {today}",
            )

    def _billed_ctx(today: date) -> judgment.RunContext:
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
            runstate.update_runstate(today, spend)
        return judgment.RunContext(charge=charge)

    # -- FEEDBACK-24: Day Setup confirmation gate ---------------------------

    def _require_day_setup(today: date, action: str) -> None:
        """Fail closed (409, actionable) when today's runstate holds no
        explicit Day Setup confirmation. Only a successful POST /day-setup
        for this date writes the confirmation key — skeleton keys, Drop,
        ledger, and other unrelated runstate writes never satisfy it."""
        if not runstate.is_day_setup_confirmed(today):
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
            # Operator request knobs from judgment-model.txt (loaded once at
            # startup). Only explicitly-set keys appear; every value is a
            # non-secret request parameter, so none are redacted here.
            "judgment_params": dict(judgment.JUDGMENT_PARAMS),
        }

    @app.get("/version")
    def get_version() -> dict:
        """T02: tokenless, pure read-only source/runtime fingerprint. Reads
        only Git identity (rev-parse on the running repo) and the committed
        cockpit index + referenced production asset bytes — no judgment,
        ledger, provider, runstate, billing, Todoist, Calendar, or
        external-source call. Missing Git
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
        result = config_reader.read_config()
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

    @app.get("/calendar-drift")
    def get_calendar_drift() -> dict:
        """Issue #19 (part A): read-only drift report between the calendar
        blocks today's commit ledger recorded and the current Calendar state.

        Tokenless read (like /config): it writes nothing, adopts nothing, and
        never re-commits. The ledger is read by exact date; the Calendar
        surface is one ``query_events`` read plus the inventory read the
        ownership scope already needs. A missing/unreadable Calendar degrades
        to a 200 ``calendar-unavailable`` body, never a 500."""
        day = gather.effective_date(datetime.now())
        checked_at = datetime.now().isoformat(timespec="seconds")
        ledger = calendar_drift.read_ledger(day)
        if not calendar_drift.ledger_touched(ledger):
            return calendar_drift.no_commit_report(day, checked_at)
        try:
            store = calendar_bridge.shared_store()
            status = store.auth_status()
            if status not in shadow._CALENDAR_AUTH_OK:
                raise calendar_bridge.CalendarWriteError(
                    f"calendar consent not granted: {status}")
            start = datetime.combine(day, datetime.min.time())
            live_events = store.query_events(start, start + timedelta(days=1))
            result = config_reader.read_config()
            config = (
                dict(result.config.sections)
                if result.config is not None else {}
            )
            own_ids = shadow._own_calendar_ids(config, store)
        except Exception as exc:  # noqa: BLE001 — degrade to a 200 body
            return calendar_drift.unavailable_report(
                day, checked_at, f"calendar unavailable: {exc}")
        return calendar_drift.compare(day, ledger, live_events, own_ids, checked_at)

    @app.get("/settings/capacities")
    def get_capacities_settings() -> dict:
        """Tokenless local read of the persisted Capacities assignment policy.

        Reads the app-owned settings policy (or reports the default-enabled
        policy when it is absent), the advisory app-owned source mapping
        record, and the machine-local structure-title cache; it never builds a
        Capacities adapter, reads a credential, calls a provider, or touches
        runstate. Malformed/unsupported settings storage fails closed with a
        bounded error and leaves the bytes untouched."""
        try:
            result = capacities_settings.read_settings()
        except (capacities_settings.SettingsStoreError, OSError) as exc:
            # Bounded client-safe message — no absolute cache path.
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
        available_structures = _available_capacities_structures()
        return {
            "settings": result.settings.as_dict(),
            "persisted": result.persisted,
            "available_structures": available_structures,
            "structure_titles": _capacities_structure_titles(
                available_structures
            ),
        }

    @app.post("/settings/capacities/save", dependencies=[Depends(require_token)])
    def post_capacities_settings_save(body: CapacitiesSettingsSaveRequest) -> dict:
        """Explicit, full-replacement save of the Capacities assignment policy.

        The complete editable policy is supplied; ``version`` and the new
        ``revision`` are server-owned. The current revision is compared under
        the store lock: a stale ``expected_revision`` is a 409, malformed
        existing storage is a 409, and a lock/read/write failure is a 500 —
        every failure path preserves the original bytes. The success response
        also carries the same advisory structure metadata as the GET route
        (computed once, from the same machine-local sources) so a client that
        adopts the response as its whole settings state keeps its cached
        structure names."""
        policy = capacities_settings.NativeTaskAutoPolicy(
            active_enabled=body.native_task_auto.active_enabled,
            due_enabled=body.native_task_auto.due_enabled,
            deadline_enabled=body.native_task_auto.deadline_enabled,
            deadline_horizon_days=body.native_task_auto.deadline_horizon_days,
        )
        try:
            saved = capacities_settings.save_settings(
                expected_revision=body.expected_revision,
                native_task_auto=policy,
                excluded=body.excluded.keys(),
                active_structures=body.active_structures.keys(),
                native_task_structures=body.native_task_structures,
                active_statuses=body.active_statuses,
                assigned_structures=body.assigned_structures,
                assigned_on_structures=body.assigned_on_structures,
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
            # Bounded client-safe message — no absolute cache path.
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
        available_structures = _available_capacities_structures()
        return {
            "settings": saved.as_dict(),
            "persisted": True,
            "available_structures": available_structures,
            "structure_titles": _capacities_structure_titles(
                available_structures
            ),
        }

    @app.get("/settings/capacities/source")
    def get_capacities_source() -> dict:
        """Tokenless read of the app-owned Capacities source mapping record.

        Reads exactly one state file and never constructs an adapter,
        reads a credential, or contacts a provider. An absent record answers
        ``{source: null, persisted: false}`` and creates no file; malformed or
        unreadable storage fails closed with a bounded 500 and leaves the
        existing bytes untouched."""
        try:
            record = capacities_builder.read_source()
        except (capacities_builder.CapacitiesSourceStoreError, OSError) as exc:
            # Bounded client-safe message — no absolute cache path.
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
        try:
            saved = capacities_builder.save_source(
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
            # Bounded client-safe message — no absolute cache path.
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
            # Bounded client-safe message — no absolute cache path.
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

    @app.post(
        "/settings/capacities/source/discover",
        dependencies=[Depends(require_token)],
    )
    def post_capacities_source_discover(
        body: CapacitiesSourceDiscoverRequest = Depends(
            _capacities_source_discover_request
        ),
    ) -> dict:
        """Token-guarded discovery of the space's structures for the editor.

        Delegates wholly to ``capacities_builder.discover_capacities_source``:
        one provider structures request, the client closed either way, and the
        catalog serialized into the pinned wire shape. Discovery is strictly
        read-only — it never reads, creates, or modifies the app-owned
        mapping record, so it cannot change what TDTB ingests.

        Space scoping is token-implied: the provider's structures endpoint
        carries no space parameter, so the request has no explicit scope and
        the payload cannot verify one (``CapacitiesRestClient.fetch_structures``
        records the same fact). The requested ``space_id`` is echoed back, not
        re-derived from the payload.

        ``warnings`` is a bounded list of sanitized operator-facing notes;
        discovery has no recoverable conditions to report, so it stays empty.
        Every failure is bounded too: a missing/unreadable credential is a
        503, a provider rate limit a 429, and any other provider, contract,
        or transport failure a 502 — no message carries exception text, the
        token, or a filesystem path."""
        try:
            catalog = capacities_builder.discover_capacities_source(
                body.space_id
            )
        except capacities_builder.CapacitiesTokenError as exc:
            # Bounded client-safe message — no credential slot or path.
            print(
                f"capacities discovery credential failed: {exc}",
                file=sys.stderr,
            )
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "capacities_discovery_credentials_unavailable",
                    "message": (
                        "Capacities credential is unavailable; "
                        "discovery cannot run."
                    ),
                },
            ) from exc
        except capacities_adapter.CapacitiesRateLimited as exc:
            print(f"capacities discovery rate limited: {exc}", file=sys.stderr)
            raise HTTPException(
                status_code=429,
                detail={
                    "code": "capacities_discovery_rate_limited",
                    "message": (
                        "Capacities rate limit exceeded; "
                        "retry discovery shortly."
                    ),
                },
            ) from exc
        except Exception as exc:  # noqa: BLE001 — provider boundary, fixed message
            # CapacitiesContractError and every other provider, contract, or
            # transport failure share one bounded 502. The fixed message never
            # carries exception text, the token, or a path.
            print(f"capacities discovery failed: {exc}", file=sys.stderr)
            raise HTTPException(
                status_code=502,
                detail={
                    "code": "capacities_discovery_failed",
                    "message": (
                        "Capacities discovery failed before a catalog "
                        "was read."
                    ),
                },
            ) from exc
        return {
            "space_id": body.space_id,
            "structures": _capacities_catalog_payload(catalog),
            "warnings": [],
        }

    # -- U2: paced direct Capacities refresh (status/start/cancel) ----------

    def _refresh_revision() -> Any:
        """Current combined configuration revision, or ``None`` when unconfigured.

        A malformed or unreadable mapping/settings store is a bounded 503: the
        seam must never treat broken storage as "unchanged" and run a job
        against scope it could not read.
        """
        try:
            return capacities_builder.refresh_config_revision()
        except Exception as exc:  # noqa: BLE001 — bounded source boundary
            print(f"capacities refresh config read failed: {exc}", file=sys.stderr)
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "capacities_refresh_unavailable",
                    "message": (
                        "The Capacities configuration could not be read; "
                        "the previous complete result is preserved."
                    ),
                },
            ) from exc

    def _refresh_source_space() -> Any:
        """Live source space id, or ``None`` when unconfigured.

        The combined revision is a sum, so a source space switch can leave it
        equal; this read is the space half of the reuse check. A malformed or
        unreadable mapping store is a bounded 503 for the same reason as
        ``_refresh_revision``: the seam must never treat broken storage as
        "unchanged" and reuse a coordinator whose scope it could not verify.
        """
        try:
            return capacities_builder.refresh_source_space_id()
        except Exception as exc:  # noqa: BLE001 — bounded source boundary
            print(f"capacities refresh source read failed: {exc}", file=sys.stderr)
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "capacities_refresh_unavailable",
                    "message": (
                        "The Capacities source could not be read; "
                        "the previous complete result is preserved."
                    ),
                },
            ) from exc

    def _refresh_coordinator(*, ensure_current: bool = False) -> Any | None:
        """Build-once, cache the single-flight coordinator; ``None`` = unconfigured.

        Caching the instance is required for correctness: cancel and status must
        observe the same in-memory job/thread that start launched, so the seam
        cannot be rebuilt per request. ``ensure_current`` (start only) replaces
        an *idle* instance whose configuration revision or source space moved,
        so a mapping or settings save, or a source space switch, reaches the
        next run instead of running the new configuration with the previous
        scope. A live job is never replaced — it keeps the
        instance that owns its thread and locks, and its own publication guard
        rejects the stale revision. A build failure is a bounded 503 — a
        configured-but-broken source is never silently treated as unconfigured.
        """
        cached = getattr(app.state, "refresh_coordinator", _REFRESH_COORDINATOR_UNSET)
        if cached is not _REFRESH_COORDINATOR_UNSET and not ensure_current:
            return cached
        with app.state.refresh_coordinator_lock:
            # Re-read under the lock: a racing start may have built while this
            # one waited, and its instance must win over a second build.
            cached = getattr(app.state, "refresh_coordinator", _REFRESH_COORDINATOR_UNSET)
            if cached is not _REFRESH_COORDINATOR_UNSET:
                if cached is not None and cached.is_running():
                    return cached
            revision = _refresh_revision()
            source_space = _refresh_source_space()
            if (
                cached is not _REFRESH_COORDINATOR_UNSET
                and revision
                == getattr(
                    app.state,
                    "refresh_coordinator_revision",
                    _REFRESH_COORDINATOR_UNSET,
                )
                and source_space
                == getattr(
                    app.state,
                    "refresh_coordinator_space_id",
                    _REFRESH_COORDINATOR_UNSET,
                )
            ):
                return cached
            build = app.state.build_refresh_coordinator
            coordinator: Any | None = None
            if build is not None:
                try:
                    coordinator = build({})
                except Exception as exc:  # noqa: BLE001 — bounded source boundary
                    print(
                        f"capacities refresh coordinator build failed: {exc}",
                        file=sys.stderr,
                    )
                    raise HTTPException(
                        status_code=503,
                        detail={
                            "code": "capacities_refresh_unavailable",
                            "message": (
                                "The Capacities source could not be prepared for a "
                                "refresh; the previous complete result is preserved."
                            ),
                        },
                    ) from exc
            app.state.refresh_coordinator = coordinator
            app.state.refresh_coordinator_revision = revision
            app.state.refresh_coordinator_space_id = source_space
            return coordinator

    def _refresh_configured(*, ensure_current: bool = False) -> Any:
        coordinator = _refresh_coordinator(ensure_current=ensure_current)
        if coordinator is None:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "capacities_refresh_unconfigured",
                    "message": "No Capacities source is configured; nothing to refresh.",
                },
            )
        return coordinator

    @app.get("/capacities/refresh/status")
    def get_capacities_refresh_status() -> dict:
        """Tokenless local read of the one refresh job + last complete snapshot.

        Never calls a provider and never spawns work; an unconfigured source
        answers ``configured: false`` with no job rather than erroring."""
        coordinator = _refresh_coordinator()
        if coordinator is None:
            return {
                "configured": False,
                "job": None,
                "phase": None,
                "outcome": None,
                "progress": {},
                "warnings": [],
                "coverage": {},
                "snapshot": {
                    "present": False,
                    "generation": 0,
                    "revision": None,
                    "installed_at": None,
                    "member_count": 0,
                    "type_check_times": {},
                },
            }
        return coordinator.status()

    @app.post(
        "/capacities/refresh/start",
        dependencies=[Depends(require_token)],
    )
    def post_capacities_refresh_start(body: CapacitiesRefreshStartRequest) -> dict:
        """Token-guarded start of one paced Refresh/Rescan job.

        The job runs on a background thread; this returns the job's initial
        truthful status without blocking on completion. A running job is a 409
        (single-flight), an unconfigured source a 503, and a malformed body a
        422."""
        coordinator = _refresh_configured(ensure_current=True)
        try:
            return coordinator.start(mode=body.mode, scope=body.scope)
        except capacities_refresh.RefreshBusyError as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "capacities_refresh_busy",
                    "message": "A Capacities refresh job is already running.",
                },
            ) from exc
        except capacities_refresh.RefreshUnavailableError as exc:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "capacities_refresh_unconfigured",
                    "message": "No Capacities source is configured; nothing to refresh.",
                },
            ) from exc

    @app.post(
        "/capacities/refresh/cancel",
        dependencies=[Depends(require_token)],
    )
    def post_capacities_refresh_cancel() -> dict:
        """Token-guarded cancellation of the running job.

        Signals the in-flight job, keeps every successful content read, and
        installs no new generation; returns the current truthful status."""
        coordinator = _refresh_configured()
        return coordinator.cancel()

    def _tag_catalog() -> dict[str, Any]:
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
            adapter = build({})
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

    # -- U4 S4: per-identity Capacities selections (GET/POST) ---------------

    @app.get("/capacities/selections")
    def get_capacities_selections() -> dict:
        """Tokenless read of the durable Capacities selections for the source space.

        Reads the source record and the selection store only: no provider call,
        no credential, no write. No source record answers the documented empty
        shape with ``space_id: null``. A store for another space answers the
        empty shape for the source space. Storage failures are a bounded 503.
        """
        try:
            source = capacities_builder.read_source()
        except (capacities_builder.CapacitiesSourceStoreError, OSError) as exc:
            raise _capacities_selections_storage_error() from exc
        if source is None:
            return {
                "space_id": None,
                "revision": 0,
                "rules_revision": None,
                "selections": [],
            }
        try:
            record = capacities_selections.load_selections(source.space_id)
            rules_revision = capacities_rules.load_rules(source.space_id).revision
        except (
            capacities_selections.SelectionStoreError,
            capacities_rules.RulesStoreError,
        ) as exc:
            raise _capacities_selections_storage_error() from exc
        return _capacities_selections_payload(record, rules_revision)

    @app.post(
        "/capacities/selections",
        dependencies=[Depends(require_token)],
    )
    def post_capacities_selections(body: CapacitiesSelectionsSaveRequest) -> dict:
        """Token-guarded merge of Capacities selections for the source space.

        The browser supplies identities and flags only. Each selected identity
        must be a current direct candidate, not hard-excluded, of a mapped
        structure, and acknowledged when it carries review reasons. The rules
        revision is stamped from the server's current rules. The write goes
        through the store's optimistic revision, so a stale ``expected_revision``
        is a 409 carrying both revisions. Direct intake only; no provider call
        and no Capacities write.
        """
        if app_config.capacities_intake() != app_config.CAPACITIES_INTAKE_DIRECT:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "capacities_intake_not_direct",
                    "message": "Capacities selections apply only under direct intake.",
                },
            )
        try:
            source = capacities_builder.read_source()
        except (capacities_builder.CapacitiesSourceStoreError, OSError) as exc:
            raise _capacities_selections_storage_error() from exc
        if source is None:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "capacities_selections_unconfigured",
                    "message": "No Capacities source is configured; nothing to select.",
                },
            )
        space_id = source.space_id
        try:
            current = capacities_selections.load_selections(space_id)
            rules_revision = capacities_rules.load_rules(space_id).revision
        except (
            capacities_selections.SelectionStoreError,
            capacities_rules.RulesStoreError,
        ) as exc:
            raise _capacities_selections_storage_error() from exc
        if current.revision != body.expected_revision:
            raise _capacities_selections_conflict(
                body.expected_revision, current.revision,
            )

        # Parse every identity before any candidate or exclusion state, so a
        # malformed or repeated identity is a request error on its own.
        seen: set[str] = set()
        selected: list[tuple[str, bool]] = []
        for entry in body.select:
            identity = _capacities_selection_identity(entry.identity, space_id)
            if identity in seen:
                raise _capacities_selection_rejection(
                    422, "duplicate_identity", identity,
                    "Capacities selection names the same identity more than once.",
                )
            seen.add(identity)
            selected.append((identity, entry.acknowledge))
        removed = {
            _capacities_selection_identity(identity, space_id)
            for identity in body.deselect
        }

        try:
            read = capacities_builder.read_direct_intake()
        except capacities_builder.CapacitiesSourceStoreError as exc:
            raise _capacities_selections_storage_error() from exc
        assigned, pool, _warnings, _coverage = capacities_intake.load_direct_rows(
            read=read,
        )
        candidates = {row.get("identity"): row for row in [*assigned, *pool]}
        exclusion_policy = _exclusion_policy_or_block()
        try:
            hard_excluded = _capacities_hard_excluded(
                gather.effective_date(datetime.now()),
                list(candidates.values()),
                exclusion_policy,
            )
        except tag_exclusions.TagExclusionBlocked as exc:
            raise HTTPException(status_code=503, detail=exc.diagnostics) from exc
        mapped_structures = {
            structure.structure_id for structure in source.structures
        }

        # Same order as the resolver: excluded, type_disabled, not_cached, then
        # the acknowledgement that a review-reason candidate requires.
        for identity, acknowledge in selected:
            if identity in hard_excluded:
                raise _capacities_selection_rejection(
                    409, "excluded", identity,
                    "Capacities selection is excluded by the tag exclusions or the drop list.",
                )
            # A canonical identity is four-part; its structure is the third part.
            if identity.split(":", 3)[2] not in mapped_structures:
                raise _capacities_selection_rejection(
                    409, "type_disabled", identity,
                    "Capacities selection's structure is no longer mapped.",
                )
            row = candidates.get(identity)
            if row is None:
                raise _capacities_selection_rejection(
                    409, "not_cached", identity,
                    "Capacities selection is not a current cached candidate; "
                    "Refresh may be required.",
                )
            if row.get("capacities_review_reasons") and not acknowledge:
                raise _capacities_selection_rejection(
                    409, "acknowledgement_required", identity,
                    "Capacities selection carries review reasons; "
                    "acknowledge it to select it.",
                )

        # Merge over the current record. Removals run first, then selections, so
        # a deselect and a re-select of one identity in one call ends selected
        # exactly once, under the current rules revision.
        merged = {record.identity: record for record in current.selections}
        for identity in removed:
            merged.pop(identity, None)
        for identity, acknowledge in selected:
            merged[identity] = capacities_selections.SelectionRecord(
                identity=identity,
                rules_revision=rules_revision,
                acknowledged=acknowledge,
            )
        try:
            saved = capacities_selections.save_selections(
                space_id=space_id,
                expected_revision=body.expected_revision,
                selections=list(merged.values()),
            )
        except capacities_selections.SelectionConflict as exc:
            raise _capacities_selections_conflict(
                exc.expected_revision, exc.current_revision,
            ) from exc
        except capacities_selections.SelectionValidationError as exc:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "capacities_selections_invalid",
                    "message": "Capacities selections are invalid; nothing was saved.",
                },
            ) from exc
        except (capacities_selections.SelectionStoreError, OSError) as exc:
            # Bounded client-safe message: the store's text stays server-side.
            print(f"capacities selections save failed: {exc}", file=sys.stderr)
            raise _capacities_selections_storage_error() from exc
        return _capacities_selections_payload(saved, rules_revision)

    # -- U3a routes: per-type Capacities inclusion rules (GET/POST) ---------

    def _capacities_rules_source() -> Any:
        """The configured source record, or a bounded fail-closed 503.

        A malformed/unreadable mapping store raises rather than being read as
        unconfigured; ``None`` means genuinely no source (a documented empty
        shape, not an error)."""
        try:
            return capacities_builder.read_source()
        except (capacities_builder.CapacitiesSourceStoreError, OSError) as exc:
            print(f"capacities rules source read failed: {exc}", file=sys.stderr)
            raise _capacities_rules_storage_error() from exc

    @app.get("/capacities/rules")
    def get_capacities_rules() -> dict:
        """Tokenless local read of the per-type Capacities inclusion rules.

        Reads the source mapping, the rules store, and the installed
        generation's structure contract only: no provider call, no credential,
        no write, and no external Capacities mutation. No source record answers
        the documented empty shape with ``space_id: null``. A malformed or
        unreadable source/rules store fails closed with a bounded 503; a
        missing or unusable structure contract is reported as
        ``contract_available: false`` rather than a fabricated schema."""
        source = _capacities_rules_source()
        if source is None:
            return _capacities_rules_payload(
                space_id=None,
                record=None,
                mapped_ids=(),
                schema_by_structure={},
                contract_available=False,
            )
        try:
            record = capacities_rules.load_rules(source.space_id)
        except (capacities_rules.RulesStoreError, OSError) as exc:
            print(f"capacities rules read failed: {exc}", file=sys.stderr)
            raise _capacities_rules_storage_error() from exc
        schema_by_structure, contract_available = _capacities_rules_contract_schema(
            source.space_id
        )
        return _capacities_rules_payload(
            space_id=source.space_id,
            record=record,
            mapped_ids=tuple(
                structure.structure_id for structure in source.structures
            ),
            schema_by_structure=schema_by_structure,
            contract_available=contract_available,
        )

    @app.post("/capacities/rules", dependencies=[Depends(require_token)])
    def post_capacities_rules(body: CapacitiesRuleSaveRequest) -> dict:
        """Token-guarded save of ONE per-type Capacities inclusion rule.

        Reuses the rules store's own active/draft + optimistic-revision API: a
        rule valid for the published type shape becomes ``active`` and replaces
        the previous active rule; a rule that is invalid — bad syntax, a
        removed property, or no published schema to validate against — is
        stored as ``draft`` and the previous active rule is preserved.
        ``fallback_minutes`` is written as supplied (omitted/null clears it). A
        stale ``expected_revision`` is a 409 carrying both revisions; malformed
        existing storage is a 409; a storage failure is a 503 — every failure
        path preserves the original bytes.

        The rule's structure must be a mapped structure of the current source
        space (a foreign/unknown type is a 422). Offline and local only: no
        provider call, no credential, and no write to external Capacities. The
        rule store's own lock is the only lock taken here — the route
        deliberately does not nest it inside the publication guard."""
        source = _capacities_rules_source()
        if source is None:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "capacities_rules_unconfigured",
                    "message": "No Capacities source is configured; nothing to save.",
                },
            )
        mapped_ids = tuple(
            structure.structure_id for structure in source.structures
        )
        if body.structure_id not in mapped_ids:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "capacities_rules_unknown_structure",
                    "message": (
                        "The rule's structure is not a mapped structure of the "
                        "configured Capacities source."
                    ),
                },
            )
        schema_by_structure, contract_available = _capacities_rules_contract_schema(
            source.space_id
        )
        schema = schema_by_structure.get(body.structure_id)
        try:
            result = capacities_rules.save_rule(
                source.space_id,
                body.structure_id,
                body.rule,
                fallback_minutes=body.fallback_minutes,
                expected_revision=body.expected_revision,
                schema=schema,
            )
        except capacities_rules.RulesConflictError as exc:
            raise _capacities_rules_conflict(
                exc.expected_revision, exc.current_revision,
            ) from exc
        except capacities_rules.RulesFormatError as exc:
            print(f"capacities rules save failed: {exc}", file=sys.stderr)
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "capacities_rules_storage_error",
                    "message": (
                        "Capacities rules storage is malformed or unsupported; "
                        "the existing rules were preserved."
                    ),
                },
            ) from exc
        except (capacities_rules.RulesStoreError, OSError) as exc:
            print(f"capacities rules save failed: {exc}", file=sys.stderr)
            raise _capacities_rules_storage_error() from exc
        try:
            record = capacities_rules.load_rules(source.space_id)
        except (capacities_rules.RulesStoreError, OSError) as exc:
            print(f"capacities rules read failed: {exc}", file=sys.stderr)
            raise _capacities_rules_storage_error() from exc
        payload = _capacities_rules_payload(
            space_id=source.space_id,
            record=record,
            mapped_ids=mapped_ids,
            schema_by_structure=schema_by_structure,
            contract_available=contract_available,
        )
        payload["save"] = {
            "structure_id": result.structure_id,
            "valid": result.valid,
            "reason": result.reason,
            "active": result.active,
            "draft": result.draft,
            "fallback_minutes": result.fallback_minutes,
            "revision": result.revision,
        }
        return payload

    @app.get("/settings/exclusions")
    def get_exclusion_settings() -> dict:
        """Tokenless local read of the persisted tag-exclusion policy.

        Reads exactly one state file (or reports the empty default when
        it is absent); the advisory tag catalog may build a provider adapter,
        but its failure degrades to a status + warning and never hides the
        saved settings. Malformed/unsupported storage fails closed with a
        bounded 500 and leaves the bytes untouched."""
        try:
            result = exclusion_settings.read_settings()
        except (exclusion_settings.ExclusionSettingsStoreError, OSError) as exc:
            # Bounded client-safe message — no absolute cache path.
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
            "tag_catalog": _tag_catalog(),
        }

    @app.post("/settings/exclusions/save", dependencies=[Depends(require_token)])
    def post_exclusion_settings_save(body: ExclusionSettingsSaveRequest) -> dict:
        """Explicit, full-replacement save of the tag-exclusion policy.

        The complete editable policy is supplied; ``version`` and the new
        ``revision`` are server-owned. The current revision is compared under
        the store lock: a stale ``expected_revision`` is a 409, malformed
        existing storage is a 409, and a lock/read/write failure is a 500 —
        every failure path preserves the original bytes."""
        try:
            saved = exclusion_settings.save_settings(
                expected_revision=body.expected_revision,
                exclusions=[
                    exclusion_settings.TagExclusion(
                        source=entry.source,
                        space_id=entry.space_id,
                        tag_id=entry.tag_id,
                    )
                    for entry in body.exclusions.tags
                ],
                todoist=[
                    exclusion_settings.TodoistExclusion(
                        id=entry.id,
                        name=entry.name,
                        added_at=entry.added_at,
                    )
                    for entry in body.exclusions.todoist
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
            # Bounded client-safe message — no absolute cache path.
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
        config files and /config exposes only section *keys*; this mirrors
        build_commit_body.build_body's input assembly (minus the sequence).

        Tokenless GET, like /config. T16 justified that with "writes nothing";
        allocator-rewrite T2 narrows the claim rather than dropping it: the
        route writes exactly ONE run-state key, ``digest_index``, and nothing
        else. That write is derived solely from data this same GET returns, is
        confined to a non-authoritative cache key, touches no external system,
        and is idempotent for identical state — so the token boundary
        (which gates external writes and billed calls) is unchanged. The write
        is deliberately LAST, after ``day_setup`` is read back."""
        today = gather.effective_date(datetime.now())
        run_data = gather.empty_run_data(today)
        order = _ranking_order()
        w_config: list[str] = app_config.sources_config_warnings()

        result = config_reader.read_config()
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
            # Issue #6: canonical config section for disabled calendars,
            # so route-level exclusions become diagnosable via calendar_decisions.
            "calendar_disabled": config.get("Disabled Calendars"),
            # Unit 1: the user-chosen list of calendars whose all-day events
            # become schedulable plan rows instead of all-day strip entries.
            "schedulable_all_day": config.get("Schedulable All-Day Calendars"),
        }
        build_clients = app.state.build_read_clients or (lambda c: (None, None))
        build_store = app.state.build_calendar_store or (lambda c: None)
        capacities_items: list[dict[str, Any]] = []
        w_capacities: list[str] = []
        # A1 artifact seam: when the operator sets sources.mode=artifact, the
        # Todoist and Capacities rows come ONLY from the normalized artifact
        # and NEITHER live client is constructed. A live read is never a
        # fallback (point 7) — the digest degrades with a loud banner instead.
        source_mode = app_config.sources_mode()
        # U4 S3: under ``direct`` intake the published Capacities generation is
        # the only Capacities source, in either source mode. It replaces the
        # artifact's Capacities rows and the live builder; Todoist and habits
        # keep their source-mode readers. Refusals never fall back to either.
        direct_intake = app_config.capacities_intake() == app_config.CAPACITIES_INTAKE_DIRECT
        direct_coverage = capacities_intake.DirectCoverage()
        direct_warnings: list[str] = []
        artifact_block: dict[str, Any] = {
            "state": artifact_source.STATUS_LIVE,
            "generated_at": None,
            "age_minutes": None,
            "logical_day": None,
            "producer": None,
            "warnings": [],
        }
        artifact_warnings: list[str] = []
        todoist_c = None
        store = None
        t_assigned: list[dict[str, Any]] = []
        t_pool: list[dict[str, Any]] = []
        # O1: a Capacities rule marked pool:true emits assigned:false, so those
        # rows must reach the pool rather than being treated as assigned. Empty
        # in live mode, where every Capacities row keeps its existing routing.
        c_pool: list[dict[str, Any]] = []
        w_todo: list[str] = []
        # PHep mirror exclusion (contract change 2026-10-13): tasks this app
        # created as Capacities mirrors are hidden from Todoist intake so a
        # mirror never re-appears as a Todoist row (any day, while open) and
        # never collides with its own Capacities row. A store read failure
        # degrades visibly here; it never crashes plan-inputs.
        mirror_excluded, mirror_warnings = _mirror_intake_exclusions()
        # Habits are agent-fetched data either way (the REST Todoist client
        # exposes no completed-tasks read), so the summary rides the artifact
        # in BOTH source modes. Never a note fallback; no data -> zeroed block
        # plus a loud source_warnings entry.
        habits: dict[str, Any] = dict(artifact_source.HABITS_EMPTY)
        w_hab: list[str] = []
        if source_mode == app_config.SOURCES_MODE_ARTIFACT:
            # The calendar store is NOT a live plan source the artifact
            # replaces: calendar, habits and anchored blocks keep their
            # current readers under the artifact seam (A1 fix). Obtain it
            # through its own seam so the digest's calendar rows survive
            # artifact mode; Todoist and Capacities stay artifact-only and
            # construct no live client.
            store = build_store(config)
            artifact_result = artifact_source.load_artifact(datetime.now())
            artifact_block = artifact_result.as_digest_block()
            if artifact_result.status != artifact_source.STATUS_FRESH:
                artifact_warnings = list(artifact_result.warnings)
            habits = dict(artifact_result.habits)
            w_hab = list(artifact_result.habit_warnings)
            t_assigned = [
                r for r in artifact_result.rows
                if r.get("source") == "todoist" and r.get("assigned") is True
            ]
            t_pool = [
                r for r in artifact_result.rows
                if r.get("source") == "todoist" and r.get("assigned") is not True
            ]
            capacities_rows = [
                r for r in artifact_result.rows if r.get("source") == "capacities"
            ]
            # Defense-in-depth only: under direct intake the override below
            # discards these rows, so this guard is not the enforcement point
            # (R-B F2). Removing it survives the discard tests by design.
            if not direct_intake:
                capacities_items = [r for r in capacities_rows if r.get("assigned") is True]
                c_pool = [r for r in capacities_rows if r.get("assigned") is not True]
        else:
            todoist_c, store = build_clients(config)
            build_capacities = app.state.build_capacities_adapter
            # Under direct intake the live builder is never called: no client is
            # constructed and no provider read happens on this path.
            if build_capacities is not None and not direct_intake:
                try:
                    capacities_client = build_capacities(config)
                except Exception as exc:  # noqa: BLE001 — source boundary degrades
                    capacities_client = None
                    w_capacities = [
                        f"Capacities adapter setup failed ({exc}) — source is unavailable"
                    ]
                if capacities_client is not None:
                    live_capacities, w_capacities = external_sources.fetch_capacities_items(
                        capacities_client, today
                    )
                    # F2: only rows the rules assign reach the assigned surface.
                    # A pool:true or UNKNOWN row stays on the pool.
                    capacities_items = [r for r in live_capacities if r.get("assigned") is True]
                    c_pool = [r for r in live_capacities if r.get("assigned") is not True]
            # Live mode still sources the habit summary from the artifact.
            habits, w_hab = artifact_source.load_habits()
        if direct_intake:
            # ONE generation read per request: the rows and the intake block
            # must describe the same generation (R-B F3, adjacent-generation
            # pairing). On a store error the warning surfaces here and the
            # rows refuse; load_direct_rows reuses this read as-is.
            try:
                intake_read: capacities_builder.DirectIntakeRead | None = (
                    capacities_builder.read_direct_intake()
                )
            except capacities_builder.CapacitiesSourceStoreError:
                intake_read = None
                w_capacities = [
                    *w_capacities,
                    capacities_intake.SOURCE_UNREADABLE,
                ]
            (
                capacities_items,
                c_pool,
                direct_warnings,
                direct_coverage,
            ) = capacities_intake.load_direct_rows(read=intake_read)
            # R5/KTD5: the last Refresh's unfinished outcome becomes one bounded
            # warning, joined into direct_warnings so the intake block's state
            # turns degraded/unavailable truthfully. A live run adds nothing.
            direct_warnings = [
                *direct_warnings,
                *capacities_intake.refresh_outcome_warnings(
                    _last_refresh_job(intake_read), _refresh_is_running(app.state)
                ),
            ]
            w_capacities = [*w_capacities, *direct_warnings]
        try:
            if source_mode != app_config.SOURCES_MODE_ARTIFACT:
                t_assigned, t_pool, w_todo = external_sources.fetch_todoist_items(
                    todoist_c, ext_cfg, exclude_ids=mirror_excluded
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
            # Unit 1: opted-in all-day events are schedulable plan rows, not
            # busy walls. Split them out of the busy frame, then drop any row
            # already committed as a timed event on TDTB's own Blocks
            # calendar so it is never double-placed. Both steps are inert
            # unless a calendar is opted in (the split is a pass-through and
            # the dedup finds nothing).
            busy_blocks, schedulable_all_day_rows = (
                external_sources.split_schedulable_all_day(busy_blocks)
            )
            schedulable_all_day_rows, already_committed_decisions = (
                external_sources.exclude_already_committed(
                    schedulable_all_day_rows, busy_blocks
                )
            )
            calendar_decisions = (
                calendar_decisions + already_committed_decisions
            )
            # T19: deterministic micro-adventure state — pure reads (config
            # section, state-dir log, Todoist completion probe
            # on the already-open read client). Never writes, never consumes.
            ma_state, _ma_done = _micro_adventure_state(config, today, todoist_c)
        finally:
            if todoist_c is not None and hasattr(todoist_c, "close"):
                try:
                    todoist_c.close()
                except Exception:  # noqa: BLE001
                    pass

        # PHep mirror exclusion: the live fetch already drops excluded ids;
        # this also covers artifact-sourced Todoist rows, so the digest (and
        # therefore the persisted digest index, commit eligibility, sequencing
        # and prompts) can never see a mirror task. The unreadable-store
        # warning rides the Todoist warnings the UI renders loudly.
        t_assigned = _drop_excluded_todoist_rows(t_assigned, mirror_excluded)
        t_pool = _drop_excluded_todoist_rows(t_pool, mirror_excluded)
        w_todo = [*w_todo, *mirror_warnings]

        # Sequence identity downstream is name-keyed (timeline id = name) —
        # rename Todoist items that collide with existing row names before merging.
        existing_rows = run_data["pool_items"] + run_data["assigned_items"]
        t_assigned = external_sources.disambiguate_names(existing_rows, t_assigned)
        t_pool = external_sources.disambiguate_names(existing_rows + t_assigned, t_pool)
        capacities_items = external_sources.disambiguate_names(
            existing_rows + t_assigned + t_pool,
            capacities_items,
        )
        c_pool = external_sources.disambiguate_names(
            existing_rows + t_assigned + t_pool + capacities_items,
            c_pool,
        )
        # Unit 1: calendar schedulable rows join the same disambiguation chain
        # so a name collision never reaches the name-keyed sequence identity.
        schedulable_all_day_rows = external_sources.disambiguate_names(
            existing_rows + t_assigned + t_pool + capacities_items + c_pool,
            schedulable_all_day_rows,
        )
        exclusion_policy = _exclusion_policy_or_block()
        try:
            # U3c-2: selected Capacities rows are promoted BEFORE build_digest,
            # so they pass the same exclusion stages as every other row.
            assigned_input = (
                run_data["assigned_items"] + t_assigned + capacities_items
                + schedulable_all_day_rows
            )
            pool_rows, assigned_rows, selection_warnings = (
                _promote_selected_capacities(
                    today,
                    run_data["pool_items"] + t_pool + c_pool,
                    assigned_input,
                    exclusion_policy,
                )
            )
            w_capacities = [*w_capacities, *selection_warnings]
            digest = build_digest(
                pool_rows,
                assigned_rows,
                today,
                order,
                ignore=(
                    result.config.get_ignore_list() if result.config is not None else None
                ),
                bias=deferrals.bias_map(today),  # T1 defer-with-memory
                exclusion_policy=exclusion_policy,
            )
            # S5: a promoted selection that the Ignore List then removed is named
            # here, since build_digest drops it silently.
            w_capacities = [
                *w_capacities,
                *_ignore_list_selection_warnings(
                    assigned_input, assigned_rows, digest["assigned"], w_capacities,
                ),
            ]
        except tag_exclusions.TagExclusionBlocked as exc:
            raise HTTPException(status_code=503, detail=exc.diagnostics) from exc

        # T4 (cockpit-overhaul): assigned rows gain resolved `blocks` per the
        # locked precedence (duration label → Todoist-native → Preset →
        # press duration_min → 1). Suggested rows stay untouched — the
        # cockpit never consumes them.
        presets = result.config.get_presets() if result.config is not None else []
        for row in digest["assigned"]:
            row["blocks"] = resolve_assigned_blocks(row, presets, None)
            # Exact minutes when the duration-label step won: a 10-minute label
            # is 1/3 block, not the 30-minute-quantised 1, so the cockpit shows
            # and edits what the label says. Same exact-blocks shape as the
            # remembered overlay below (FT-05 F1), which runs afterwards and
            # still overwrites these fields when a remembered value exists.
            # warn=False: the collision diagnostic already came from
            # resolve_assigned_blocks above.
            tag_label, tag_minutes = _assigned_duration_tag(row, warn=False)
            if tag_minutes is not None:
                exact_blocks = tag_minutes / 30
                row["blocks"] = (
                    int(exact_blocks) if exact_blocks.is_integer() else exact_blocks
                )
                row["duration_minutes"] = tag_minutes
                row["duration_source"] = f"tag:{tag_label}"

        # FT-01: remembered-duration overlay — a valid remembered
        # value wins over the source-derived blocks and is labelled
        # ``remembered``. Pure read: never mutates the duration cache, never
        # calls billed endpoints, never writes upstream sources. Missing or
        # corrupt cache data simply leaves source-resolved blocks in place.
        remembered = duration_memory.read_all_memory()
        for row in digest["assigned"]:
            duration_memory.apply_remembered_overlay(row, remembered)

        # micro_adventure side-load (Locked #7 / build_commit_body parity):
        # today's exact-date run-state selection merges into config so a
        # selected Live micro-adventure reaches the /commit Live→Todoist
        # reroute. Missing note or absent key → no-op. Reads the dated note
        # directly (not load_runstate, which returns the strictly-prior note).
        rs_path = runstate.runstate_read_path(today)
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
            "pool_source": ma_state["pool_source"],
            "pool_warnings": ma_state["pool_warnings"],
        }

        # -- Day Setup + time/capacity (ui-parity T4) ------------------------
        day_setup, prompt_warnings = _read_day_setup(today)
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
        dropped_rows = runstate.read_dropped(today)
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
        try:
            runstate.write_digest_index(
                today,
                build_digest_index(digest),
                # Server-owned policy stamp: the revision this digest was
                # built under. Recorded for a later staleness guard; this
                # slice adds no refresh rejection.
                exclusion_settings_revision=exclusion_policy.revision,
            )
        except OSError as exc:
            # Best-effort cache: the index is derived solely from data this
            # same GET returns, so a failed write degrades target resolution
            # to "unknown" for this date rather than failing the read.
            print(f"digest index write failed: {exc}", file=sys.stderr)

        # U5 read-side: expose the persisted opt-in metadata top-level, exactly
        # as the frontend's projectPromptOptins expects it. A corrupt store
        # omits the block (prefs unavailable) and adds one bounded warning.
        optins_metadata, optins_warnings = _read_prompt_optins_metadata()
        response = {
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
            "day_setup_confirmed": runstate.is_day_setup_confirmed(today),
            "day_semantics": resolved_day_semantics,
            "planning_config_fingerprint": planning_config_fingerprint,
            "micro_adventure": micro_payload,
            "dropped_today": dropped_rows,
            "calendar_decisions": calendar_decisions,
            "artifact": artifact_block,
            "source_warnings": (
                w_todo + w_cal + w_hab + w_capacities + artifact_warnings
                + w_config + prompt_warnings + optins_warnings
            ),
            "source_counts": {
                "todoist": len(t_assigned) + len(t_pool),
                "capacities": len(capacities_items),
                "calendar": len(busy_blocks),
            },
        }
        if optins_metadata is not None:
            response["prompt_optins"] = optins_metadata
        if direct_intake:
            response["capacities_intake"] = _capacities_intake_block(
                digest, direct_coverage, direct_warnings, intake_read,
            )
        return response

    @app.get("/billed-ledger")
    def get_billed_ledger() -> dict:
        """G24: tokenless read of the persistent per-day billed-call ledger,
        so UI budget counters render the server's number, not a client-side
        guess (same contract stance as /capacity-preview)."""
        today = gather.effective_date(datetime.now())
        spent = _billed_spent(today)
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

        today = gather.effective_date(datetime.now())
        persisted, _prompt_warnings = _read_day_setup(today)
        merged = {**persisted,
                  **{k: v for k, v in overrides.items() if k in _DAY_SETUP_KEYS}}

        result = config_reader.read_config()
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
            # Issue #6: canonical config section for disabled calendars,
            # so route-level exclusions become diagnosable via calendar_decisions.
            "calendar_disabled": config.get("Disabled Calendars"),
            # Unit 1: keep the frame consistent with /plan-inputs — an opted-in
            # all-day event leaves the busy frame instead of riding the strip
            # (its all-day cost was already zero capacity).
            "schedulable_all_day": config.get("Schedulable All-Day Calendars"),
        }
        build_clients = app.state.build_read_clients or (lambda c: (None, None))
        todoist_c, store = build_clients(config)
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
            # Unit 1: same split as /plan-inputs so the two consumers agree on
            # the busy frame; the schedulable rows themselves are not part of
            # the capacity math (they are assigned plan rows).
            busy_blocks, _schedulable_all_day_rows = (
                external_sources.split_schedulable_all_day(busy_blocks)
            )
            # Habits ride the artifact here too — no note read.
            habits, _w_hab = artifact_source.load_habits()
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

    def _source_resolved_fallback(today: date, identity: str):
        """Current source-resolved ``(minutes, label)`` for a canonical
        identity, resolved WITHOUT memory — the reset route's fallback.
        Mirrors /plan-inputs' row assembly (external Todoist
        via the injected read clients); read-only and deterministic. None
        when the identity is not present today."""
        result = config_reader.read_config()
        config: dict[str, Any] = (
            dict(result.config.sections) if result.config is not None else {}
        )
        presets = result.config.get_presets() if result.config is not None else []
        # PHep mirror exclusion: this read resolves a specific identity for the
        # duration-memory reset route; exclude app-created mirrors so they can
        # never be resolved as a source row here either.
        mirror_excluded, _mirror_warnings = _mirror_intake_exclusions()
        rows: list[dict[str, Any]] = []
        build_clients = app.state.build_read_clients or (lambda c: (None, None))
        todoist_c, _store = build_clients(config)
        try:
            if todoist_c is not None:
                ext_cfg: dict[str, Any] = {
                    **dict(config.get("Defaults") or {}),
                    "calendar_capacity_classes": config.get("Calendar Capacity Classes"),
                }
                t_assigned, t_pool, _w = external_sources.fetch_todoist_items(
                    todoist_c, ext_cfg, exclude_ids=mirror_excluded
                )
                rows = rows + _drop_excluded_todoist_rows(
                    list(t_assigned) + list(t_pool), mirror_excluded)
        finally:
            if todoist_c is not None and hasattr(todoist_c, "close"):
                try:
                    todoist_c.close()
                except Exception:  # noqa: BLE001
                    pass
        for row in rows:
            if duration_memory.item_identity(row) == identity:
                return duration_memory.resolve_duration(
                    row, presets, None, memory={}
                )
        return None

    @app.post("/config/anchored-blocks/start", dependencies=[Depends(require_token)])
    def post_anchored_block_start(body: AnchoredStartSaveRequest) -> dict:
        """Persist a changed Start for ONE anchored block to the tracked repo
        config (``config/tdtb.json``), touching nothing else in the file.

        This is the only route that edits the repo config, and only this one
        value. Unknown names (including calendar-sourced rows, which are not
        config rows) are 404, an unparseable time is 422, and a machine-local
        override that defines ``anchored_blocks`` is 409 because it would
        shadow the saved value. The config is re-read from disk on every use,
        so the running app sees the change without a restart."""
        try:
            return config_write.save_anchored_start(body.block, body.start)
        except config_write.InvalidStartError as exc:
            raise HTTPException(
                status_code=422,
                detail={"code": "anchored_start_invalid", "message": str(exc)},
            ) from exc
        except config_write.UnknownBlockError as exc:
            raise HTTPException(
                status_code=404,
                detail={"code": "anchored_block_not_found", "message": str(exc)},
            ) from exc
        except config_write.ShadowedByLocalConfigError as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": "anchored_blocks_shadowed", "message": str(exc)},
            ) from exc
        except config_write.NotEditableError as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": "anchored_block_not_editable", "message": str(exc)},
            ) from exc
        except OSError as exc:
            # Never return raw internals (they carry absolute paths); the real
            # cause stays server-side and the original file is untouched.
            print(f"anchored start save failed: {exc}", file=sys.stderr)
            raise HTTPException(
                status_code=500,
                detail={
                    "code": "config_write_error",
                    "message": (
                        "The config could not be written; "
                        "the existing file was preserved."
                    ),
                },
            ) from exc

    @app.post("/duration-memory/save", dependencies=[Depends(require_token)])
    def post_duration_save(body: DurationSaveRequest) -> dict:
        """FT-01: persist a remembered duration for one canonical identity.

        Strict validation (integer >= 0 divisible by 5) happens before any
        cache access; lock/read/write failures fail closed (500) and never
        replace existing durable bytes."""
        try:
            identity = duration_memory.normalize_identity(body.identity)
            minutes = duration_memory.save_memory(identity, body.minutes)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except (duration_memory.MemoryStoreError, OSError) as exc:
            # FT-05 F3: never return raw internal diagnostics (they can carry
            # absolute cache paths). The real cause stays server-side.
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
        no upstream writes — the fallback resolves from the
        same injected read clients /plan-inputs uses.

        FT-06 F2-R1 ordering: the source fallback is resolved FIRST. When no
        fallback exists (identity absent from today's plan) or resolution
        fails, reset returns a bounded error and leaves the durable cache
        bytes unchanged — the remembered value is never deleted without a
        proven fallback. Only a valid fallback proceeds to the (fail-closed)
        deletion and is returned to the client."""
        today = gather.effective_date(datetime.now())
        try:
            identity = duration_memory.normalize_identity(body.identity)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        try:
            fallback = _source_resolved_fallback(today, identity)
        except Exception as exc:  # noqa: BLE001 — bounded client-safe boundary
            # FT-06 F2-R1: resolution failure must not delete durable memory.
            # The real cause (which may name source paths) stays
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
            removed = duration_memory.reset_memory(identity)
        except (duration_memory.MemoryStoreError, OSError) as exc:
            # FT-05 F3: bounded client-safe detail; the real cause (which may
            # name the cache path) stays server-side only.
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
        """Seed today's trigger-1 run-state note (the micro-adventure keys) and
        return the run-data envelope. There is no note gather any more, so the
        envelope carries no rows; the write is token-guarded."""
        today = gather.effective_date(datetime.now())
        run_data = gather.empty_run_data(today)
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
        result = config_reader.read_config()
        sections: dict[str, Any] = (
            dict(result.config.sections) if result.config is not None else {}
        )
        ma_state, _ = _micro_adventure_state(sections, today, None)
        ma_updates: dict[str, Any] = {
            "live_pool": ma_state["live_pool"],
            "live_streak": ma_state["streak"],
            "pending_confirm": ma_state["pending_confirm"],
        }
        if not _read_today_runstate(today).get("micro_adventure"):
            ma_updates["micro_adventure"] = ma_state["auto_pick"]
        runstate.update_runstate(today, ma_updates)
        return run_data

    @app.post("/day-setup", dependencies=[Depends(require_token)])
    def post_day_setup(body: DaySetupRequest) -> dict:
        """Persist the Phase-1 Day Setup confirm into today's run-state note
        (session/day-scoped — never the config files). Derives ``re_included``
        server-side, once, per skill 819: a block whose window-passed DEFAULT
        is off/skipped but which the payload turns on."""
        today = gather.effective_date(datetime.now())

        result = config_reader.read_config()
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
        # U5: a payload carrying ONLY prompt content updates the local prompt
        # stores without confirming the day. Any non-prompt setup field (or a
        # bare {} save) keeps the existing explicit-confirmation behavior.
        prompt_only = bool(present & _PROMPT_ONLY_FIELDS) and not bool(
            present & _NON_PROMPT_SETUP_FIELDS
        )
        existing_setup, prompt_warnings = _read_day_setup(today)
        # Local prompt stores are the only prompt target; the legacy runstate
        # keys stay INERT and are never written here.
        _save_prompt_drafts(today, body.captures)
        optins_map, optins_revision, optins_warnings = _save_prompt_optins(body)
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
        # Additive per-day habit overrides: full replace like ``anchored``,
        # with the tri-state null that clears. Purely local — no provider write.
        if "habits" in present:
            updates["habits"] = body.habits
        # U5: a prompt-only save never confirms Day Setup. Any non-prompt field
        # (or a bare {} save) preserves the existing explicit confirmation.
        confirmed = True
        if prompt_only:
            # No runstate write at all: the local prompt stores are the only
            # target, so a draft/opt-in edit cannot materialize or alter the
            # dated runstate note.
            state = _read_today_runstate(today)
            confirmed = runstate.is_day_setup_confirmed(today)
        else:
            # G26: locked RMW — concurrent /day-setup POSTs previously lost
            # updates. FEEDBACK-24: this successful POST is the ONLY writer of
            # the explicit confirmation, scoped to today's dated note.
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

            state = runstate.update_runstate(today, _save_day_setup)
        day_setup_echo, echo_warnings = _day_setup_with_prompts(state, today)
        prompt_warnings = [*prompt_warnings, *echo_warnings, *optins_warnings]
        response: dict[str, Any] = {
            "ok": True,
            "re_included": sorted(re_included),
            "day_setup_confirmed": confirmed,
            "day_setup": {k: day_setup_echo.get(k) for k in _DAY_SETUP_KEYS},
        }
        # U5: an unreadable opt-in store omits the echo block (the frontend then
        # keeps prefs unavailable) rather than echoing a fabricated all-false
        # store that would overwrite saved preferences.
        if optins_map is not None:
            response["optins"] = optins_map
            response["optins_revision"] = optins_revision
        if prompt_warnings:
            response["prompt_warnings"] = prompt_warnings
        return response

    @app.post("/digest", dependencies=[Depends(require_token)])
    def post_digest(body: DigestRequest | None = None) -> dict:
        """Deterministic tiered digest. Accepts pre-gathered run-data in the
        body; otherwise the note-row slice is empty. Same input → identical
        output (integration-tested)."""
        order = _ranking_order()
        if body and body.pool_items is not None:
            today = date.fromisoformat(body.today) if body.today else gather.effective_date(datetime.now())
            pool_items = body.pool_items
            assigned_items = body.assigned_items or []
        else:
            today = gather.effective_date(datetime.now())
            pool_items = []
            assigned_items = []
        # P3-02: the same-day Drop-from-plan exclusions must also gate /digest
        # — a dropped item may neither be returned nor re-indexed for today,
        # on either surface. Date-scoped: tomorrow the item is eligible again.
        dropped_rows = runstate.read_dropped(today)
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
        cfg_result = config_reader.read_config()
        exclusion_policy = _exclusion_policy_or_block()
        try:
            # U3c-2: the same promotion as the plan-inputs caller, BEFORE
            # build_digest.
            pool_items, assigned_items, selection_warnings = (
                _promote_selected_capacities(
                    today, pool_items, assigned_items, exclusion_policy,
                )
            )
            digest = build_digest(
                pool_items,
                assigned_items,
                today,
                order,
                ignore=(
                    cfg_result.config.get_ignore_list() if cfg_result.config is not None else None
                ),
                bias=deferrals.bias_map(today),  # T1 defer-with-memory
                exclusion_policy=exclusion_policy,
            )
        except tag_exclusions.TagExclusionBlocked as exc:
            raise HTTPException(status_code=503, detail=exc.diagnostics) from exc
        # /digest has no warnings envelope of its own: selection notices ride
        # the same ``source_warnings`` key plan-inputs uses, and only when set,
        # so the response shape is otherwise unchanged.
        if selection_warnings:
            digest["source_warnings"] = selection_warnings
        return digest

    @app.post("/adjust", dependencies=[Depends(require_token)])
    def post_adjust(body: AdjustRequest) -> dict:
        """Translate a free-text adjustment ask into structured ops via the
        judgment layer (call #3). SDK/schema failure → 502-style JSON error,
        never a bare 500. G24: gated on + charged against the persistent
        per-day billed ledger (429 when spent)."""
        today = gather.effective_date(datetime.now())
        _require_billed_budget(today)
        try:
            return judgment.adjust_freetext(
                body.instruction, body.digest, ctx=_billed_ctx(today))
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
        today = gather.effective_date(datetime.now())
        canonical_assigned = _canonicalize_route_assigned(
            today, body.assigned,
        )
        day_setup, _prompt_warnings = _read_day_setup(today)
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
        _require_billed_budget(today)
        try:
            outcome = planning.run_proposal(
                prepared, judgment.propose_sequence,
                ctx=_billed_ctx(today),
            )
        except judgment.BudgetExceededError as exc:
            raise HTTPException(status_code=429, detail=str(exc)) from exc
        except judgment.JudgmentError as exc:
            raise HTTPException(status_code=502, detail=f"judgment error: {exc}") from exc
        if not outcome.validation.ok:
            detail: dict[str, Any] = {
                "message": "sequence validation failed",
                "hard_errors": outcome.validation.hard_errors,
                "rejected_proposal": outcome.rejected_proposal,
            }
            if outcome.validation.warnings:
                # Includes any accepted model-ID repairs, so a repaired-but-
                # still-rejected proposal never hides what was changed.
                detail["warnings"] = outcome.validation.warnings
            raise HTTPException(
                status_code=422,
                detail=detail,
            )
        runstate.update_runstate(today, outcome.snapshot)
        assert outcome.result is not None
        return outcome.result

    @app.post("/validate-sequence", dependencies=[Depends(require_token)])
    def post_validate_sequence(body: ValidateSequenceRequest) -> dict:
        """Revalidate the client layout without provider calls or merging."""
        today = gather.effective_date(datetime.now())
        canonical_assigned = _canonicalize_route_assigned(
            today, body.assigned,
        )
        snapshot = _read_today_runstate(today)
        day_setup, _prompt_warnings = _day_setup_with_prompts(snapshot, today)
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
        today = gather.effective_date(datetime.now())
        snapshot = _read_today_runstate(today)
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

    @app.post("/micro-adventure/loose-end", dependencies=[Depends(require_token)])
    def post_loose_end(body: LooseEndRequest) -> dict:
        """Record the Set up day banner's Did it / Dismiss for an unconfirmed
        prior Live block. Writes only the micro-adventure log: never today's
        plan, the day frame, or Todoist."""
        try:
            entry_date = date.fromisoformat(body.date)
        except ValueError:
            raise HTTPException(status_code=422, detail="date must be YYYY-MM-DD")
        log_path = micro_adventure.history_path()
        updated = micro_adventure.resolve_loose_end(
            micro_adventure.read_history(log_path),
            date_=entry_date, entry_id=body.id, done=body.done,
        )
        if updated is None:
            raise HTTPException(
                status_code=404, detail="no unconfirmed Live block for that date and id",
            )
        micro_adventure.write_history(log_path, updated)
        return {"ok": True, "date": body.date, "id": body.id, "done": body.done}

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
                live_today = gather.effective_date(datetime.now())
                _require_day_setup(live_today, "committing")
                commit_config, safe_digest = _validate_commit_eligibility(
                    body, live_today,
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

        shadow_today = gather.effective_date(datetime.now())
        shadow_day_setup, _prompt_warnings = _read_day_setup(shadow_today)
        # P3-02: eligibility boundary immediately before manifest construction.
        # It returns the same server-authoritative effective config that the
        # validator used, plus the sanitized digest, so the client cannot
        # inject or rewrite Step E/D source rows or assigned metadata after
        # the boundary.
        shadow_config, safe_digest = _validate_commit_eligibility(body, shadow_today)
        manifest = _build_manifest_or_refuse(
            safe_digest, body.sequence, shadow_config,
            time_frame=_frame_for_writes(shadow_config, shadow_day_setup))

        config_for_state: Any = shadow_config
        try:
            live_state = shadow.gather_live_state(config_for_state)
        except shadow.ShadowStateError as exc:
            raise HTTPException(status_code=502, detail=f"shadow state error: {exc}") from exc

        diff = shadow.diff_against_live(manifest, live_state)
        result = diff.as_dict()
        # B2: a truthful, read-only preview of the planned prompt exports —
        # server opt-ins + local logical-day drafts only. A shadow build writes
        # no receipt and performs no provider call; the preview is content-free.
        preview_drafts, _ = _read_prompt_drafts(shadow_today)
        result["prompt_exports"] = prompt_export.preview_prompt_exports(
            day=shadow_today.isoformat(),
            civil_date=prompt_export.civil_date_for(datetime.now()).isoformat(),
            drafts=preview_drafts,
        )
        return result

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
        today: date, day_setup: dict[str, Any],
    ) -> dict[str, Any]:
        """Build the effective config used by the commit boundary.

        The browser echoes config in the commit body, but source rows must be
        authorized from the current config and dated setup instead of
        from that echo.  The returned object is also passed to the manifest
        builder so a client cannot alter an authorized row after validation.
        """
        result = config_reader.read_config()
        config: dict[str, Any] = (
            dict(result.config.sections) if result.config is not None else {}
        )
        config = shadow.apply_day_setup(config, day_setup)
        return _ensure_micro_adventure(config, today)

    def _validate_commit_eligibility(
        body: CommitRequest, today: date,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """P3-02 server authorization immediately before manifest building.

        Names alone are not identities: assigned rows must match the server's
        assigned digest surface and every source identity supplied by the
        client.  Sequence rows are then limited to current server-derived
        assigned, anchored, schedulable, and Trinoor identities; fixed-lane
        rows (anchored blocks, Trinoor work zones) must also carry the exact
        server timing, and a ``backdrop`` flag is honored only on server-emitted
        Trinoor zone rows and overlap_allowed anchored rows.  The client ``_excluded`` flag and client-only config
        are never authorization. Native timed protection is recomputed from
        the dated server digest index immediately before the manifest boundary.

        Capacities rows (U4 S6) are admitted only by canonical identity, and only
        while still selected (selection-basis rows; rule-assigned rows need no
        selection record), acknowledged if they carry review reasons, and not
        hard-excluded since indexing; see ``_capacities_commit_refusals``.

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

        index = runstate.read_digest_index(today)
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
            str(d.get("identity")) for d in runstate.read_dropped(today)
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
            the same source identity. Plain paths remain path-only. In either
            case a name-only submission is never sufficient. A Capacities row
            matches only on its canonical identity (see
            ``_capacities_identity_matches``).
            """
            if _is_calendar_source_row(candidate):
                return _calendar_identity_matches(submitted, candidate)
            if _is_capacities_source_row(candidate):
                return _capacities_identity_matches(submitted, candidate)
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
        capacities_refusals: list[str] = []
        for row in submitted_assigned:
            match = _server_match(row)
            name = str(row.get("name") or "<unnamed>")
            if match is not None:
                authorized_rows.append(match)
                authorized_matches.append((match, row))
            elif _is_capacities_source_row(row) or any(
                _is_capacities_source_row(candidate)
                and str(candidate.get("name") or "").strip() == str(row.get("name") or "").strip()
                for candidate in server_assigned
            ):
                capacities_refusals.append(f"{name!r} has no server-indexed identity match")
            else:
                unknown_assigned.append(name)
        capacities_refusals.extend(
            _capacities_commit_refusals(today, authorized_matches),
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

        day_setup, _prompt_warnings = _read_day_setup(today)
        config = _server_commit_config(today, day_setup)
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
        anchored_specs = shadow._anchored_specs(config)
        anchored_names = set(anchored_specs)
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
        # Sequence population injects overlap_allowed anchored blocks as
        # ``backdrop: true`` rows (see sequence.populate_remaining_anchors).
        # The client echoes that flag verbatim, so honor it on those anchored
        # names too; exact timing still binds through the anchored branch.
        backdrop_names = zone_names | {
            name for name, spec in anchored_specs.items()
            if spec.get("overlap_allowed")
        }
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
            if bool(row.get("backdrop")) and row_id not in backdrop_names:
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
                spec = anchored_specs[row_id]
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
                or malformed_blocks or capacities_refusals):
            parts: list[str] = []
            if unknown_assigned:
                parts.append("stale/dropped assigned items: "
                             + ", ".join(repr(name) for name in unknown_assigned))
            if capacities_refusals:
                parts.append("Capacities rows not admitted: "
                             + "; ".join(capacities_refusals))
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
        today = gather.effective_date(datetime.now())
        # FEEDBACK-24: the external write path fails closed until Day Setup is
        # explicitly confirmed for today (409, actionable). The client-facing
        # wizard already lands on setup, but a direct or stale client must not
        # be able to write a plan over an unconfirmed day.
        _require_day_setup(today, "committing")
        # T8: Day Setup state (anchored overrides, re_included, captures)
        # flows into the manifest via config
        live_day_setup, _prompt_warnings = _read_day_setup(today)
        if config_override is None:
            config = _server_commit_config(today, live_day_setup)
        else:
            config = config_override
        manifest = _build_manifest_or_refuse(
            digest_override if digest_override is not None else body.digest,
            body.sequence, config,
            time_frame=_frame_for_writes(config, live_day_setup))
        try:
            live_state = shadow.gather_live_state(config)
        except shadow.ShadowStateError as exc:
            raise HTTPException(status_code=502, detail=f"shadow state error: {exc}") from exc
        diff = shadow.diff_against_live(manifest, live_state)

        injected_todoist: Any = None
        store: Any = None
        token: str | None = None
        has_calendar_rows = any(e.manifest.system == "calendar" for e in diff.entries)
        if app.state.build_commit_clients:
            injected_todoist, store = app.state.build_commit_clients(config)
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

        # B2: the due is the CIVIL calendar date, not the pre-02:00 logical
        # day that keys the draft store.
        civil_date = prompt_export.civil_date_for(datetime.now()).isoformat()
        if app.state.build_commit_clients:
            report = orchestrate.run_orchestrated(
                intents, todoist=injected_todoist, store=store, today=today, resume=resume,
            )
            export_outcomes = prompt_export.run_prompt_exports_for_report(
                report, day=today.isoformat(), civil_date=civil_date,
                todoist=injected_todoist,
            )
        else:
            with shadow.todoist_client.TodoistClient(token) as todoist:
                report = orchestrate.run_orchestrated(
                    intents, todoist=todoist, store=store, today=today, resume=resume,
                )
                export_outcomes = prompt_export.run_prompt_exports_for_report(
                    report, day=today.isoformat(), civil_date=civil_date,
                    todoist=todoist,
                )
        prompt_export.attach_to_report(report, export_outcomes)
        # T19: the authorized commit is the ONLY history-consuming surface —
        # exactly one idempotent log upsert, after every surface reports ok.
        _append_micro_adventure_history(report, config, intents, today)
        return report

    # -- runtime item actions (T20) -------------------------------------------

    def _runtime_clients() -> tuple[Any, Any, Any, bool]:
        """(todoist, store, capacities, owns_todoist) for runtime actions.

        Injected builders are used first in tests. Each source surface degrades
        to None and the verb planner fails closed before any write. The
        Capacities builder remains opt-in; no live credential route is created
        here by default.
        """
        owns_todoist = False
        if app.state.build_commit_clients:
            todoist_c, store = app.state.build_commit_clients(None)
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
                result = config_reader.read_config()
                config = (
                    dict(result.config.sections)
                    if result.config is not None
                    else {}
                )
                capacities_c = build_capacities(config)
            except Exception:  # noqa: BLE001 — action boundary fails closed
                capacities_c = None
        return todoist_c, store, capacities_c, owns_todoist

    def _raise_runtime_error(exc: runtime_actions.RuntimeActionError) -> None:
        code = 503 if "surface unavailable" in str(exc) else 422
        raise HTTPException(status_code=code, detail=str(exc)) from exc

    @app.post("/runtime-actions", dependencies=[Depends(require_token)])
    def post_runtime_action(body: RuntimeActionRequest) -> Any:
        today = gather.effective_date(datetime.now())
        # FEEDBACK-24: runtime verbs write external state (Todoist/Calendar) —
        # fail closed until Day Setup is explicitly confirmed for today.
        _require_day_setup(today, "applying runtime actions")
        todoist_c, store, capacities_c, owns = _runtime_clients()
        try:
            return runtime_actions.apply_action(
                today, body.verb, body.target, body.args,
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
        today = gather.effective_date(datetime.now())
        # FEEDBACK-24: undo writes external state too — same closed gate.
        _require_day_setup(today, "undoing runtime actions")
        todoist_c, store, capacities_c, owns = _runtime_clients()
        try:
            return runtime_actions.undo_action(
                today, action_id,
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
        today = gather.effective_date(datetime.now())
        return runtime_actions.load_journal(today)

    # -- runtime batches (B5) ------------------------------------------------

    @app.post("/runtime-batches", dependencies=[Depends(require_token)])
    def post_runtime_batch(body: RuntimeBatchRequest) -> Any:
        today = gather.effective_date(datetime.now())
        # FEEDBACK-24: batch items run the same write verbs as /runtime-actions
        # — same closed Day Setup gate.
        _require_day_setup(today, "applying runtime batches")
        todoist_c, store, capacities_c, owns = _runtime_clients()
        try:
            return runtime_batches.apply_batch(
                today, [item.model_dump() for item in body.items],
                todoist=todoist_c, store=store, capacities=capacities_c,
            )
        except runtime_actions.RuntimeActionError as exc:
            _raise_runtime_error(exc)
        finally:
            if owns:
                todoist_c.close()
            if capacities_c is not None and hasattr(capacities_c, "close"):
                capacities_c.close()

    @app.post("/runtime-batches/{batch_id}/retry",
              dependencies=[Depends(require_token)])
    def post_runtime_batch_retry(batch_id: str) -> Any:
        today = gather.effective_date(datetime.now())
        _require_day_setup(today, "retrying runtime batches")
        todoist_c, store, capacities_c, owns = _runtime_clients()
        try:
            return runtime_batches.retry_batch(
                today, batch_id,
                todoist=todoist_c, store=store, capacities=capacities_c,
            )
        except runtime_actions.RuntimeActionError as exc:
            _raise_runtime_error(exc)
        finally:
            if owns:
                todoist_c.close()
            if capacities_c is not None and hasattr(capacities_c, "close"):
                capacities_c.close()

    @app.post("/runtime-batches/{batch_id}/undo",
              dependencies=[Depends(require_token)])
    def post_runtime_batch_undo(batch_id: str) -> Any:
        today = gather.effective_date(datetime.now())
        _require_day_setup(today, "undoing runtime batches")
        todoist_c, store, capacities_c, owns = _runtime_clients()
        try:
            return runtime_batches.undo_batch(
                today, batch_id,
                todoist=todoist_c, store=store, capacities=capacities_c,
            )
        except runtime_actions.RuntimeActionError as exc:
            _raise_runtime_error(exc)
        finally:
            if owns:
                todoist_c.close()
            if capacities_c is not None and hasattr(capacities_c, "close"):
                capacities_c.close()

    # -- static UI (T10: thin, unstyled config + digest views) ----------------
    if _STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=str(_STATIC_DIR), html=True), name="static")

    return app


def build_real_read_clients(config: dict[str, Any]) -> tuple[Any, Any]:
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


def build_real_calendar_store(config: dict[str, Any]) -> Any:
    """Live calendar store for the artifact-mode seam; degrades to None.

    Calendar is not a live *plan source* the artifact replaces — it is a
    read the digest keeps, so it gets its own seam (A1 fix). Mirrors the
    ``shared_store()`` guard in ``build_real_read_clients`` exactly.
    """
    try:
        return calendar_bridge.shared_store()
    except Exception:  # noqa: BLE001 — absence degrades, never blocks
        return None


def build_real_capacities_adapter(config: dict[str, Any]) -> Any:
    """Live Capacities adapter for the source seam.

    The seam's shape is ``(config) -> adapter|None``, matching
    ``build_real_read_clients``; the builder underneath instead takes an
    optional ``CapacitiesBuilderConfig`` for injection, so this adapter is the
    boundary between the two conventions.

    ``config`` (the parsed config) is unused: Capacities reads its own
    app-owned mapping record and assignment settings, and the builder
    supplies the credential slot, page bound, and content-read budget.
    """
    return capacities_builder.build_capacities_adapter()


def build_real_refresh_coordinator(config: dict[str, Any]) -> Any:
    """Live paced-refresh coordinator for the direct-refresh seam (U2).

    Mirrors ``build_real_capacities_adapter``: the seam's shape is
    ``(config) -> coordinator|None`` while the builder owns the
    credential slot, store root, and page bound. ``config`` (the parsed
    config) is unused — Capacities reads its own app-owned mapping record and
    settings policy. ``None`` means "no Capacities source configured".
    """
    return capacities_builder.build_refresh_coordinator()


app = create_app()
app.state.build_read_clients = build_real_read_clients
app.state.build_calendar_store = build_real_calendar_store


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
