"""Pure sequence planning boundary.

This module is the seam between the HTTP/source layer and the judgment
adapter.  It accepts effective inputs only: resolving runstate, calendar
participation, and the live frame belongs to ``main.py``.  The proposal and
revalidation modes intentionally have different invariants.
"""
from __future__ import annotations

import copy
import math
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable

import external_sources
import sequence


class PlanningError(Exception):
    """A deterministic preflight failure, before a judgment call."""

    def __init__(self, message: str, *, hard_errors: list[str] | None = None,
                 conflicts: list[dict[str, Any]] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.hard_errors = hard_errors or []
        self.conflicts = conflicts or []


@dataclass
class PreparedSequence:
    mode: str
    assigned: list[dict[str, Any]]
    movable_assigned: list[dict[str, Any]]
    config: dict[str, Any]
    anchored_blocks: list[dict[str, Any]]
    day_setup: dict[str, Any]
    today: date
    time_frame: dict[str, Any]
    day_semantics: dict[str, Any]
    planning_config_fingerprint: str
    pinned_rows: list[dict[str, Any]] = field(default_factory=list)
    overlap_grants: list[dict[str, Any]] = field(default_factory=list)
    effective_pins: list[dict[str, Any]] = field(default_factory=list)
    blocks: list[dict[str, Any]] = field(default_factory=list)
    zone_rows: list[dict[str, Any]] = field(default_factory=list)
    block_notes: list[str] = field(default_factory=list)
    optional_ids: set[str] = field(default_factory=set)
    qt_contents: list[str] = field(default_factory=list)
    fixed_schedulable_rows: list[dict[str, Any]] = field(default_factory=list)
    snapshot: dict[str, Any] = field(default_factory=dict)
    sequence: list[dict[str, Any]] = field(default_factory=list)
    preflight_conflicts: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class PlanningOutcome:
    result: dict[str, Any] | None
    validation: sequence.ValidationResult
    rejected_proposal: dict[str, Any] | None = None
    snapshot: dict[str, Any] = field(default_factory=dict)


def _id(item: dict[str, Any]) -> str:
    return str(item.get("id") or item.get("name") or "")


def _effective_blocks(
    inputs: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    """Build schedulable rows from already-resolved day setup values."""
    frame = inputs["time_frame"]
    return external_sources.build_schedulable_blocks(
        inputs["config"],
        inputs.get("day_setup") or {},
        inputs["today"],
        str(frame.get("anchor") or "00:00"),
        resolved_day_semantics=inputs.get("day_semantics") or {},
    )


def _recurring_pin_errors(
    assigned: list[dict[str, Any]], pinned_rows: list[dict[str, Any]]
) -> list[str]:
    """Reject client pins that move a timed recurring row off its native slot."""
    expected = {
        _id(pin): pin
        for item in assigned
        for pin in sequence.recurring_auto_pins([item])
    }
    errors: list[str] = []
    for pin in pinned_rows:
        expected_pin = expected.get(_id(pin))
        if expected_pin is None:
            continue
        if (
            pin.get("start") != expected_pin["start"]
            or pin.get("end") != expected_pin["end"]
        ):
            errors.append(
                f"recurring pinned row {_id(pin)!r} must remain at "
                f"{expected_pin['start']}-{expected_pin['end']}"
            )
    return errors


def prepare_sequence(inputs: dict[str, Any], *, mode: str) -> PreparedSequence:
    """Prepare a proposal or revalidation without provider/source access.

    ``inputs`` is copied before any downstream helper is called.  In
    particular, this function never applies persisted setup or calendar
    participation: those are responsibilities of the caller that assembled
    the effective anchored set.
    """
    if mode not in {"proposal", "revalidation"}:
        raise ValueError(f"unknown sequence planning mode: {mode!r}")
    data = copy.deepcopy(inputs)
    assigned_input = list(data.get("assigned") or [])
    config = dict(data.get("config") or {})
    anchored = list(data.get("anchored_blocks") or [])
    pins = list(data.get("pinned_rows") or [])

    if mode == "proposal":
        pin_errors = sequence.validate_pinned_rows(pins, assigned_input)
        if pin_errors:
            raise PlanningError(
                "pinned-row validation failed", hard_errors=pin_errors
            )
        recurring_pin_errors = _recurring_pin_errors(assigned_input, pins)
        if recurring_pin_errors:
            raise PlanningError(
                "recurring pinned row validation failed",
                hard_errors=recurring_pin_errors,
            )

    blocks, zone_rows, block_notes = _effective_blocks(data)
    conflicts = external_sources.stale_mint_conflicts(blocks, anchored)
    if mode == "proposal" and conflicts:
        raise PlanningError(
            "selected Mint sessions conflict with fixed or work walls",
            conflicts=conflicts,
        )

    if mode == "proposal":
        auto_pins = sequence.recurring_auto_pins(
            assigned_input, exclude_ids={_id(pin) for pin in pins}
        )
        effective_pins = pins + auto_pins
        if auto_pins:
            pin_errors = sequence.validate_pinned_rows(effective_pins, assigned_input)
            if pin_errors:
                raise PlanningError(
                    "pinned-row validation failed", hard_errors=pin_errors
                )
    else:
        # Revalidation must not manufacture or restore pins.  Client changes
        # are observable through snapshot and immutable-row checks below.
        effective_pins = pins

    defaults = dict(config.get("Defaults") or {})
    try:
        factor = float(defaults.get("estimation.correction_factor") or 1.0)
    except (TypeError, ValueError):
        factor = 1.0
    corrected = assigned_input
    if mode == "proposal" and factor > 1.0:
        corrected = []
        for item in assigned_input:
            blocks_est = item.get("blocks")
            n = blocks_est if isinstance(blocks_est, (int, float)) and blocks_est > 0 else 1
            corrected.append({**item, "blocks": math.ceil(n * factor)})

    qt_on = any(block.get("qt") for block in blocks)
    unabsorbed, qt_contents = external_sources.absorb_quick_tasks(corrected, qt_on)
    blocks = external_sources.disambiguate_names(unabsorbed, blocks)

    if mode == "proposal":
        all_assigned = unabsorbed + blocks
        fixed_rows = sequence.placement_window_rows(blocks)
        fixed_ids = {str(row.get("id")) for row in fixed_rows}
        present_ids = {_id(item) for item in all_assigned}
        effective_pins = [pin for pin in effective_pins if _id(pin) in present_ids]
        pinned_ids = {_id(pin) for pin in effective_pins}
        movable = [
            item for item in all_assigned
            if _id(item) not in pinned_ids and _id(item) not in fixed_ids
        ]
        optional_ids: set[str] = set()
    else:
        all_assigned = unabsorbed
        fixed_rows = []
        absorbed = {_id(item) for item in assigned_input} - {_id(item) for item in unabsorbed}
        optional_ids = absorbed | {_id(block) for block in blocks}
        # No pin filtering: all client rows remain visible to the validator.
        movable = list(unabsorbed)

    snapshot = copy.deepcopy(data.get("snapshot") or {})
    return PreparedSequence(
        mode=mode,
        assigned=all_assigned,
        movable_assigned=movable,
        config=config,
        anchored_blocks=anchored,
        day_setup=data.get("day_setup") or {},
        today=data["today"],
        time_frame=copy.deepcopy(data["time_frame"]),
        day_semantics=data.get("day_semantics") or {},
        planning_config_fingerprint=str(data.get("planning_config_fingerprint") or ""),
        pinned_rows=pins,
        overlap_grants=list(data.get("overlap_grants") or []),
        effective_pins=effective_pins,
        blocks=blocks,
        zone_rows=zone_rows,
        block_notes=block_notes,
        optional_ids=optional_ids,
        qt_contents=qt_contents,
        fixed_schedulable_rows=fixed_rows,
        snapshot=snapshot,
        sequence=copy.deepcopy(data.get("sequence") or []),
        preflight_conflicts=conflicts,
    )


def _pinned_walls(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {"Block": pin["id"], "Type": "hard", "Start": pin["start"],
         "End": pin["end"], "pinned": True}
        for pin in rows
    ]


def _proposal_config(prepared: PreparedSequence) -> dict[str, Any]:
    return {
        **prepared.config,
        "time": prepared.time_frame,
        "resolved_zones": prepared.day_semantics.get("enabled_zones") or [],
        "overlap_permissions_raw": (
            prepared.day_semantics.get("overlap_permissions_raw") or ""
        ),
        "resolved_day_semantics": copy.deepcopy(prepared.day_semantics),
        "planning_config_fingerprint": prepared.planning_config_fingerprint,
    }


def run_proposal(
    prepared: PreparedSequence,
    judgment_adapter: Callable[..., dict[str, Any]],
    *,
    ctx: Any = None,
) -> PlanningOutcome:
    """Call the injected judgment adapter once, then validate deterministically."""
    if prepared.mode != "proposal":
        raise ValueError("run_proposal requires proposal preparation")
    prompt_walls = _pinned_walls(prepared.effective_pins)
    prompt_walls.extend(
        {"Block": row["id"], "Type": "hard", "Start": row["start"],
         "End": row["end"], "pinned": True, "mint_session": True}
        for row in prepared.fixed_schedulable_rows
    )
    proposal = judgment_adapter(
        copy.deepcopy(prepared.movable_assigned),
        _proposal_config(prepared),
        copy.deepcopy(prepared.anchored_blocks) + prompt_walls,
        ctx=ctx,
    )
    proposal = copy.deepcopy(proposal)
    proposal = sequence.canonicalize_sequence_ids(
        proposal,
        prepared.assigned + [
            {"id": block.get("Block"), "name": block.get("Block")}
            for block in prepared.anchored_blocks if block.get("Block")
        ],
    )
    proposal["sequence"] = sequence.merge_immutable_rows(
        list(proposal.get("sequence") or []),
        prepared.effective_pins + prepared.fixed_schedulable_rows,
    )
    validation = sequence.validate_sequence(
        proposal,
        prepared.assigned,
        prepared.anchored_blocks + _pinned_walls(prepared.effective_pins),
        prepared.config,
        time_frame=prepared.time_frame,
        optional_items=prepared.blocks,
        planning_config_fingerprint=prepared.planning_config_fingerprint,
    )
    snapshot = {
        "overlap_grants": list(proposal.get("overlap_grants") or []),
        "pinned_rows": copy.deepcopy(prepared.effective_pins),
        "planning_config_fingerprint": prepared.planning_config_fingerprint,
    }
    if not validation.ok:
        return PlanningOutcome(
            result=None, validation=validation,
            rejected_proposal=proposal, snapshot=snapshot,
        )

    proposal["warnings"] = validation.warnings + prepared.block_notes
    proposal["sequence"] = list(proposal.get("sequence") or []) + copy.deepcopy(prepared.zone_rows)
    if prepared.qt_contents:
        proposal["qt_contents"] = prepared.qt_contents
    proposal["pinned_rows"] = copy.deepcopy(prepared.effective_pins)
    return PlanningOutcome(result=proposal, validation=validation, snapshot=snapshot)


def validate_revalidation(prepared: PreparedSequence) -> sequence.ValidationResult:
    """Validate the client layout without sorting, merging, or deriving pins."""
    if prepared.mode != "revalidation":
        raise ValueError("validate_revalidation requires revalidation preparation")
    expected_snapshot = {
        "overlap_grants": prepared.snapshot.get("overlap_grants", []),
        "pinned_rows": prepared.snapshot.get("pinned_rows", []),
        "planning_config_fingerprint": prepared.snapshot.get(
            "planning_config_fingerprint", ""
        ),
    }
    actual_snapshot = {
        "overlap_grants": prepared.overlap_grants,
        "pinned_rows": prepared.pinned_rows,
        "planning_config_fingerprint": prepared.planning_config_fingerprint,
    }
    if actual_snapshot != expected_snapshot and prepared.snapshot:
        return sequence.ValidationResult(
            ok=False, hard_errors=["planning snapshot is stale"]
        )

    pin_errors = sequence.validate_pinned_rows(prepared.pinned_rows, prepared.assigned)
    if pin_errors:
        return sequence.ValidationResult(ok=False, hard_errors=pin_errors)
    expected = {_id(pin): pin for pin in prepared.pinned_rows}
    actual = {
        _id(row): row for row in prepared.sequence if _id(row) in expected
    }
    if actual != expected:
        return sequence.ValidationResult(
            ok=False, hard_errors=["pinned rows changed from immutable snapshot"]
        )

    if prepared.preflight_conflicts:
        return sequence.ValidationResult(
            ok=False,
            hard_errors=["selected Mint sessions conflict with fixed or work walls"],
            conflicts=copy.deepcopy(prepared.preflight_conflicts),
        )

    return sequence.validate_sequence(
        {"sequence": copy.deepcopy(prepared.sequence),
         "overlap_grants": copy.deepcopy(prepared.overlap_grants)},
        prepared.assigned,
        prepared.anchored_blocks + _pinned_walls(prepared.pinned_rows),
        prepared.config,
        time_frame=prepared.time_frame,
        optional_ids=prepared.optional_ids,
        optional_items=prepared.blocks,
        planning_config_fingerprint=prepared.planning_config_fingerprint,
    )
