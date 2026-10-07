"""Public, pure effective-assignment evaluation for Capacities candidates.

TDTB decides whether a Capacities object is effective TDTB work by a fixed
precedence::

    source Assigned  >  TDTB Excluded  >  TDTB Auto

This module owns only that decision. It performs no I/O, reads no
configuration file, makes no provider call, and never mutates its inputs, so
the same candidate plus settings always produce the same decision. TDTB
Settings persistence, the settings API, and the cockpit UI are later slices;
this module accepts a plain settings object (or a bare exclusion set) so those
slices can pass one in without redesigning the decision.

Source truth is a precondition, not a competing assignment mode. A row that is
malformed, belongs to another space, carries no stable source identity, or is
closed in the source (completed/dropped) is never eligible, even when a stale
due or deadline date would otherwise satisfy the Auto rule. This preserves the
separate open-status/safety filter.

Native Capacities ``RootTask``/``Task`` objects expose immutable built-in
properties, so they carry no source Assigned marker and rely on the native
Auto rule. Custom objects may expose a source boolean Assigned marker:
``assigned=true`` is intentional explicit Assigned input and wins over any
TDTB exclusion; ``assigned=false``/absent encodes neither Auto nor Excluded and
falls through to exclusion and then to ``no-effective-assignment``.

Custom-object Auto has exactly one settled inference: when the object's
structure is explicitly Active-enabled by the caller's settings AND its typed
status label NAME is ``Active``, the object is Auto-eligible (reason
``auto-custom-status-active``). This is independent of the native
``active_enabled`` toggle and never applies to a native structure. No other
custom Auto inference exists: an open status is otherwise a safety
precondition, and custom dates are display signals, not implicit inclusion.

Nothing here matches on title. Exclusion and Auto both key on the stable
source-qualified identity the source adapter already emits
(``capacities:{space}:{structure}:{object}``).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Any, Iterable

CAPACITIES_SOURCE = "capacities"

#: Native Capacities task structures, keyed by structure ID. They expose
#: built-in, immutable properties and therefore no writable Assigned marker.
#: Only structure IDS can satisfy this set: a structure DISPLAY NAME can
#: never match the adapter's structure ids. ``Task`` is the display name of
#: the ``RootTask`` structure (an object-type request for ``Task`` returns
#: ``objectTypeId: RootTask``), so it is intentionally absent.
NATIVE_TASK_STRUCTURES = frozenset({"RootTask"})

#: Native task status that satisfies the native Auto status condition.
DEFAULT_ACTIVE_STATUSES = frozenset({"active"})

#: Custom-structure Auto pull: the typed status label NAME (not id) that, for a
#: structure explicitly Active-enabled by the caller, is an inclusion signal.
CUSTOM_ACTIVE_STATUS = "active"

#: ``deadline <= logical_day + N calendar days`` (inclusive), per contract.
DEFAULT_DEADLINE_HORIZON_DAYS = 2


class AssignmentMode(str, Enum):
    """Effective assignment state for one candidate."""

    ASSIGNED = "assigned"
    EXCLUDED = "excluded"
    AUTO = "auto"
    NONE = "none"


@dataclass(frozen=True)
class AssignmentReason:
    """One deterministic reason, suitable for a Settings explanation.

    ``origin`` is ``"source"`` for source truth, ``"tdtb"`` for a TDTB policy
    decision (exclusion or Auto), and ``"safety"`` for a precondition.
    """

    code: str
    detail: str
    origin: str


@dataclass(frozen=True)
class AssignmentCandidate:
    """Normalized source signals for one Capacities object.

    ``status_is_open`` is the mapped open/closed classification the source
    adapter derives from the structure's open-status filter: ``True`` open,
    ``False`` completed/dropped, ``None`` when no status filter is mapped. It
    is a safety signal only: ``False`` blocks every mode, and ``True`` never
    implies Auto for a custom object. ``status`` is the normalized source
    status token used by the native Auto status condition.
    """

    identity: str
    source_assigned: bool | None = None
    status: str | None = None
    status_is_open: bool | None = None
    due: date | None = None
    deadline: date | None = None
    well_formed: bool = True
    space_matches: bool = True


@dataclass(frozen=True)
class AssignmentSettings:
    """Minimal evaluator settings seam.

    The vault-scoped persistence and the settings API (``capacities_settings``)
    build this object; the cockpit UI is a later slice. This object deliberately
    carries only what the decision needs today, including the explicit
    ``active_structures`` custom Auto policy.
    """

    excluded_identities: frozenset[str] = frozenset()
    native_task_structures: frozenset[str] = NATIVE_TASK_STRUCTURES
    active_statuses: frozenset[str] = DEFAULT_ACTIVE_STATUSES
    deadline_horizon_days: int = DEFAULT_DEADLINE_HORIZON_DAYS
    #: Native Auto rule toggles. Each gates exactly one native Auto condition;
    #: all three default enabled. Disabling every rule never blocks explicit
    #: source assignment or a TDTB exclusion.
    active_enabled: bool = True
    due_enabled: bool = True
    deadline_enabled: bool = True
    #: Custom structures whose typed ``Active`` status label NAME is an
    #: inclusion signal. This is the ONLY custom Auto inference TDTB permits;
    #: it is independent of the native ``active_enabled`` toggle. Empty by
    #: default, so custom objects keep failing closed.
    active_structures: frozenset[str] = frozenset()


@dataclass(frozen=True)
class CapacitiesIdentity:
    """Parsed stable source-qualified identity."""

    space_id: str
    structure_id: str
    object_id: str

    @property
    def qualified(self) -> str:
        return f"{CAPACITIES_SOURCE}:{self.space_id}:{self.structure_id}:{self.object_id}"


@dataclass(frozen=True)
class AssignmentProvenance:
    """Source and policy evidence behind one decision."""

    source: str
    identity: str
    space_id: str
    structure_id: str
    object_id: str
    native_task: bool
    source_assigned: bool | None
    exclusion_matched: bool
    auto_conditions: tuple[str, ...]
    logical_day: str


@dataclass(frozen=True)
class AssignmentDecision:
    """Effective assignment result for one candidate."""

    identity: str
    mode: AssignmentMode
    eligible: bool
    reasons: tuple[AssignmentReason, ...]
    provenance: AssignmentProvenance

    @property
    def reason_codes(self) -> tuple[str, ...]:
        return tuple(reason.code for reason in self.reasons)


def parse_capacities_identity(value: Any) -> CapacitiesIdentity | None:
    """Parse ``capacities:{space}:{structure}:{object}`` or return ``None``.

    The identity must be source-qualified, non-empty in every component, and
    free of surrounding whitespace. A title, name, or bare object id is not a
    stable identity and is rejected.
    """
    text = str(value or "").strip()
    if not text:
        return None
    parts = text.split(":")
    if len(parts) < 4:
        return None
    if parts[0].casefold() != CAPACITIES_SOURCE:
        return None
    if any(part != part.strip() for part in parts):
        return None
    space_id, structure_id = parts[1], parts[2]
    object_id = ":".join(parts[3:])
    if not space_id or not structure_id or not object_id:
        return None
    return CapacitiesIdentity(space_id, structure_id, object_id)


def _normalized_status(value: Any) -> str:
    return " ".join(str(value or "").casefold().split())


def _resolve_settings(settings: AssignmentSettings | Iterable[str] | None) -> AssignmentSettings:
    if settings is None:
        return AssignmentSettings()
    if isinstance(settings, AssignmentSettings):
        return settings
    if isinstance(settings, (str, bytes)):
        raise TypeError(
            "assignment settings must be AssignmentSettings or an iterable of stable identities"
        )
    try:
        identities = frozenset(str(value) for value in settings)
    except TypeError:
        raise TypeError(
            "assignment settings must be AssignmentSettings or an iterable of stable identities"
        ) from None
    return AssignmentSettings(excluded_identities=identities)


def _reason(code: str, detail: str, origin: str) -> AssignmentReason:
    return AssignmentReason(code=code, detail=detail, origin=origin)


def _deadline_within_horizon(
    deadline: date, logical_day: date, horizon_days: int
) -> bool:
    """True when ``deadline <= logical_day + horizon_days`` (inclusive).

    Compares day offsets instead of adding a ``timedelta`` to the logical day,
    so an arbitrarily large horizon cannot overflow ``date`` and no arbitrary
    product cap has to be invented. A malformed horizon fails closed.
    """
    try:
        return (deadline - logical_day).days <= horizon_days
    except (OverflowError, TypeError):
        return False


def _auto_conditions(
    candidate: AssignmentCandidate,
    *,
    native_task: bool,
    structure_id: str | None,
    logical_day: date,
    settings: AssignmentSettings,
) -> tuple[str, ...]:
    """Return the Auto conditions that matched, in deterministic order.

    Native ``RootTask``/``Task`` objects have an OR Auto policy: status ==
    Active, due <= logical day, or deadline <= logical day + horizon. A missing
    date never matches. Each condition is additionally gated by its native Auto
    toggle.

    A non-native object has exactly ONE settled custom Auto inference: when its
    structure is explicitly in ``settings.active_structures`` and its typed
    status label NAME is ``Active``. That pull is independent of
    ``active_enabled`` and never applies to a native structure. Every other
    custom signal stays a precondition/display value and never implies
    inclusion.
    """
    if not native_task:
        if (
            structure_id is not None
            and structure_id in settings.active_structures
            and _normalized_status(candidate.status) == CUSTOM_ACTIVE_STATUS
        ):
            return ("custom-status-active",)
        return ()
    matched: list[str] = []
    active = {_normalized_status(value) for value in settings.active_statuses}
    if settings.active_enabled and _normalized_status(candidate.status) in active:
        matched.append("status-active")
    if (
        settings.due_enabled
        and candidate.due is not None
        and candidate.due <= logical_day
    ):
        matched.append("due-today-or-overdue")
    if (
        settings.deadline_enabled
        and candidate.deadline is not None
        and _deadline_within_horizon(
            candidate.deadline, logical_day, settings.deadline_horizon_days
        )
    ):
        matched.append("deadline-within-horizon")
    return tuple(matched)


def _auto_reason(condition: str, settings: AssignmentSettings) -> AssignmentReason:
    horizon = settings.deadline_horizon_days
    details = {
        "status-active": ("auto-status-active", "Source status is Active."),
        "due-today-or-overdue": (
            "auto-due-today-or-overdue",
            "Due date is today or overdue.",
        ),
        "deadline-within-horizon": (
            "auto-deadline-within-horizon",
            f"Deadline is within {horizon} calendar days of the logical day.",
        ),
        "custom-status-active": (
            "auto-custom-status-active",
            "Custom structure is Active-enabled and its status label name is Active.",
        ),
    }
    code, detail = details[condition]
    return _reason(code, detail, "tdtb")


def evaluate_assignment(
    candidate: AssignmentCandidate,
    *,
    logical_day: date,
    settings: AssignmentSettings | Iterable[str] | None = None,
) -> AssignmentDecision:
    """Evaluate the effective assignment of one candidate.

    ``settings`` accepts an :class:`AssignmentSettings` or a bare iterable of
    excluded stable identities. The function is pure: it reads nothing else and
    mutates neither argument.
    """
    resolved = _resolve_settings(settings)
    identity = str(candidate.identity or "").strip()
    parsed = parse_capacities_identity(candidate.identity)
    native_task = bool(parsed and parsed.structure_id in resolved.native_task_structures)

    def decide(
        mode: AssignmentMode,
        *,
        reasons: tuple[AssignmentReason, ...],
        auto_conditions: tuple[str, ...] = (),
        exclusion_matched: bool = False,
    ) -> AssignmentDecision:
        return AssignmentDecision(
            identity=identity,
            mode=mode,
            eligible=mode in (AssignmentMode.ASSIGNED, AssignmentMode.AUTO),
            reasons=reasons,
            provenance=AssignmentProvenance(
                source=CAPACITIES_SOURCE,
                identity=identity,
                space_id=parsed.space_id if parsed else "",
                structure_id=parsed.structure_id if parsed else "",
                object_id=parsed.object_id if parsed else "",
                native_task=native_task,
                source_assigned=candidate.source_assigned,
                exclusion_matched=exclusion_matched,
                auto_conditions=auto_conditions,
                logical_day=logical_day.isoformat(),
            ),
        )

    # 1. Source-truth preconditions. These gate every mode, including explicit
    #    assignment: a completed, dropped, malformed, or cross-space row never
    #    becomes eligible, even from a stale date.
    if not candidate.well_formed:
        return decide(
            AssignmentMode.NONE,
            reasons=(_reason("malformed-source-row", "Source row is malformed.", "safety"),),
        )
    if not candidate.space_matches:
        return decide(
            AssignmentMode.NONE,
            reasons=(_reason("cross-space-row", "Source row belongs to another space.", "safety"),),
        )
    if parsed is None:
        if not identity:
            reason = _reason(
                "missing-stable-identity",
                "Candidate has no stable source identity.",
                "safety",
            )
        else:
            reason = _reason(
                "unstable-identity",
                "Candidate identity is not a stable Capacities identity.",
                "safety",
            )
        return decide(AssignmentMode.NONE, reasons=(reason,))
    if candidate.status_is_open is False:
        return decide(
            AssignmentMode.NONE,
            reasons=(
                _reason(
                    "closed-source-status",
                    "Source status is completed or dropped.",
                    "safety",
                ),
            ),
        )

    # 2. Source Assigned wins over any TDTB exclusion.
    if candidate.source_assigned is True:
        return decide(
            AssignmentMode.ASSIGNED,
            reasons=(
                _reason(
                    "source-assigned",
                    "Capacities marks this object explicitly assigned.",
                    "source",
                ),
            ),
        )

    # 3. TDTB stable-identity exclusion wins over Auto.
    if identity in resolved.excluded_identities:
        return decide(
            AssignmentMode.EXCLUDED,
            reasons=(
                _reason(
                    "tdtb-excluded",
                    "Excluded in TDTB by stable source identity.",
                    "tdtb",
                ),
            ),
            exclusion_matched=True,
        )

    # 4. Native Auto policy. Custom Auto is not yet specified, so a non-native
    #    object falls through to no-effective-assignment below.
    conditions = _auto_conditions(
        candidate,
        native_task=native_task,
        structure_id=parsed.structure_id if parsed else None,
        logical_day=logical_day,
        settings=resolved,
    )
    if conditions:
        return decide(
            AssignmentMode.AUTO,
            reasons=tuple(_auto_reason(condition, resolved) for condition in conditions),
            auto_conditions=conditions,
        )

    return decide(
        AssignmentMode.NONE,
        reasons=(
            _reason(
                "no-effective-assignment",
                "No explicit assignment, exclusion, or native Auto match.",
                "tdtb",
            ),
        ),
    )
