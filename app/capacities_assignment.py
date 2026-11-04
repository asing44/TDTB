"""Public, pure effective-assignment evaluation for Capacities candidates.

TDTB decides whether a Capacities object is effective TDTB work by a fixed
precedence::

    source Assigned  >  TDTB Excluded  >  TDTB Auto

This module owns only that decision. It performs no I/O, reads no
configuration file, makes no provider call, and never mutates its inputs, so
the same candidate plus settings always produce the same decision. The
version-2 settings document (:mod:`capacities_settings`) is the policy source;
:meth:`AssignmentSettings.from_v2` derives the evaluator seam from it, so an
operator declares one admission mode per structure and this module is the only
place that interprets what a mode means.

One mode per structure
----------------------
Every structure carries exactly one ``mode``:

- ``off`` never admits anything. It is the default for an undeclared structure
  and is never enumerated.
- ``active`` admits only when the source status label NAME resolves into that
  structure's open status names.
- ``assigned`` admits only when the declared boolean property reads ``true``.
- ``assignedOn`` admits only when the declared date property is on or before
  the logical day.
- ``native`` admits on status-open OR ``due <= logical day`` OR
  ``deadline <= logical day + N`` (``native.deadline_horizon_days``).

Status admission is tri-state per structure, mirroring the document: inherited
(no names of its own), DISABLED (dates/assignment only — the owner's
``RootTask`` decision), or its own names. A status mode with no open names to
compare against reports ``no-status-vocabulary`` rather than silently admitting
or dropping.

Source truth is a precondition, not a competing admission mode. A row that is
malformed, belongs to another space, carries no stable source identity, or is
closed in the source (completed/dropped) is never eligible, even when a stale
due or deadline date would otherwise satisfy a mode. This preserves the
separate open-status/safety filter. An UNKNOWN status never drops: it stays an
unassigned candidate, because the evaluator distinguishes "not admitted" from
"forbidden".

Nothing here matches on title. Exclusion and admission both key on the stable
source-qualified identity the source adapter already emits
(``capacities:{space}:{structure}:{object}``). A stored per-type rule remains
its own eligibility authority on the adapter's rule path; this module never
interprets a rule and never re-decides one.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from types import MappingProxyType
from typing import Any, Iterable, Mapping

CAPACITIES_SOURCE = "capacities"

#: Native Capacities task structures, keyed by structure ID. Retained as the
#: version-1 migration vocabulary (``capacities_settings`` translates a
#: version-1 ``native_task_structures`` list into ``native`` declarations with
#: it); admission itself is mode-driven, so nothing here is a policy input.
#: ``Task`` is the display name of the ``RootTask`` structure, so it is
#: intentionally absent.
NATIVE_TASK_STRUCTURES = frozenset({"RootTask"})

#: The default open-status vocabulary (``statuses.open``). Retained as the
#: version-1/version-2 default vocabulary so a structure that inherits statuses
#: and an unmigrated adapter reader agree on one value.
DEFAULT_ACTIVE_STATUSES = frozenset({"active"})

#: ``deadline <= logical_day + N calendar days`` (inclusive), per contract.
DEFAULT_DEADLINE_HORIZON_DAYS = 2

#: The five admission modes, exactly as the settings document spells them.
MODE_OFF = "off"
MODE_ACTIVE = "active"
MODE_ASSIGNED = "assigned"
MODE_ASSIGNED_ON = "assignedOn"
MODE_NATIVE = "native"
ADMISSION_MODES = frozenset(
    {MODE_OFF, MODE_ACTIVE, MODE_ASSIGNED, MODE_ASSIGNED_ON, MODE_NATIVE}
)
#: Modes whose decision reads a declared provider property.
PROPERTY_MODES = frozenset({MODE_ASSIGNED, MODE_ASSIGNED_ON})
#: Modes whose decision consults the status condition at all.
STATUS_MODES = frozenset({MODE_ACTIVE, MODE_NATIVE})


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
    decision (exclusion or admission), and ``"safety"`` for a precondition.
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
    implies admission by itself. ``status`` is the source status label NAME
    the status condition compares (never the label id).

    ``assigned_property`` and ``assigned_on`` carry the values of the provider
    properties a ``assigned``/``assignedOn`` declaration names. The adapter
    resolves the declared raw property id to these values; the evaluator stays
    property-id agnostic, so a declaration change never touches the decision.
    ``None`` means unreadable or absent and never admits.
    """

    identity: str
    source_assigned: bool | None = None
    status: str | None = None
    status_is_open: bool | None = None
    due: date | None = None
    deadline: date | None = None
    well_formed: bool = True
    space_matches: bool = True
    assigned_property: bool | None = None
    assigned_on: date | None = None


@dataclass(frozen=True)
class TypeMode:
    """One structure's resolved declaration: exactly one admission mode.

    ``open_statuses`` is the per-structure status admission: ``None`` inherits
    the shared vocabulary (``AssignmentSettings.open_statuses``), an empty set
    DISABLES status admission for this structure (dates/assignment only — the
    owner's ``RootTask`` decision), and a non-empty set is this structure's own
    open names, kept exactly as configured and normalized at comparison time.

    ``property_id`` is the RAW provider property id the adapter must read for
    ``assigned``/``assignedOn``; it is required iff the mode reads a property
    and forbidden otherwise, mirroring the persisted document.
    """

    mode: str = MODE_OFF
    property_id: str | None = None
    open_statuses: frozenset[str] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.mode, str) or self.mode not in ADMISSION_MODES:
            raise ValueError(
                f"{self.mode!r} is not a supported admission mode "
                f"(expected one of {sorted(ADMISSION_MODES)})"
            )
        if self.mode in PROPERTY_MODES:
            if not isinstance(self.property_id, str) or not self.property_id:
                raise ValueError(
                    f"mode {self.mode!r} requires a non-empty raw property id, "
                    f"got {self.property_id!r}"
                )
        elif self.property_id is not None:
            raise ValueError(f"mode {self.mode!r} must not declare a property")
        if self.open_statuses is not None:
            if isinstance(self.open_statuses, (str, bytes)):
                raise ValueError("open_statuses must be a collection of names")
            object.__setattr__(
                self, "open_statuses", frozenset(self.open_statuses)
            )

    @property
    def uses_status(self) -> bool:
        """Whether this mode consults the status condition at all."""
        return self.mode in STATUS_MODES

    def resolved_open_statuses(self, inherited: Iterable[str]) -> frozenset[str]:
        """The open names this declaration admits on, resolved.

        A mode that never admits on status contributes the empty set, so a
        caller can compare unconditionally without knowing the mode.
        """
        if not self.uses_status:
            return frozenset()
        if self.open_statuses is None:
            return frozenset(inherited)
        return self.open_statuses


@dataclass(frozen=True)
class AssignmentSettings:
    """The evaluator seam derived from the version-2 settings document.

    ``types`` maps a structure id to its :class:`TypeMode`; a plain mode string
    is accepted as shorthand for ``TypeMode(mode=...)``. A structure that is
    absent is ``off`` and admits nothing. Build this from the persisted policy
    with :meth:`from_v2`, or state a mode map directly for tests and fixtures.

    The three read-only projections at the bottom exist because the source
    adapter still reads the version-1 policy names while it is migrated to
    modes. They are DERIVED from ``types`` and can never be set; they are not
    inputs, and a mode is the only authority.
    """

    excluded_identities: frozenset[str] = frozenset()
    deadline_horizon_days: int = DEFAULT_DEADLINE_HORIZON_DAYS
    #: The shared open-status vocabulary a declaration inherits when it
    #: declares no names of its own (``statuses.open`` in the document).
    open_statuses: frozenset[str] = DEFAULT_ACTIVE_STATUSES
    types: Mapping[str, TypeMode] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if type(self.deadline_horizon_days) is not int or self.deadline_horizon_days < 0:
            raise ValueError("deadline_horizon_days must be a nonnegative integer")
        object.__setattr__(self, "excluded_identities", frozenset(self.excluded_identities))
        object.__setattr__(self, "open_statuses", frozenset(self.open_statuses))
        resolved: dict[str, TypeMode] = {}
        for structure_id, declaration in dict(self.types).items():
            if not isinstance(structure_id, str) or not structure_id:
                raise ValueError(f"{structure_id!r} is not a valid structure id")
            resolved[structure_id] = (
                declaration
                if isinstance(declaration, TypeMode)
                else TypeMode(mode=declaration)
            )
        object.__setattr__(self, "types", MappingProxyType(resolved))

    # -- construction ------------------------------------------------------

    @classmethod
    def from_v2(cls, settings: Any) -> "AssignmentSettings":
        """Derive the evaluator seam from a version-2 settings document.

        ``settings`` is duck-typed on the shape :class:`capacities_settings.
        CapacitiesSettings` publishes (``types``, ``statuses.open``,
        ``native.deadline_horizon_days``, ``excluded``) so this module keeps no
        import of the persistence layer and the dependency stays one-way.
        An ``off`` declaration is dropped: it is the default for an absent
        structure and is never enumerated.
        """
        declarations = getattr(settings, "types", None) or {}
        types: dict[str, TypeMode] = {}
        for structure_id, declaration in dict(declarations).items():
            mode = getattr(declaration, "mode", MODE_OFF)
            if mode == MODE_OFF:
                continue
            statuses = getattr(declaration, "statuses", None)
            types[structure_id] = TypeMode(
                mode=mode,
                property_id=getattr(declaration, "property_id", None),
                open_statuses=None if statuses is None else frozenset(statuses),
            )
        native = getattr(settings, "native", None)
        horizon = (
            getattr(native, "deadline_horizon_days", DEFAULT_DEADLINE_HORIZON_DAYS)
            if native is not None
            else DEFAULT_DEADLINE_HORIZON_DAYS
        )
        vocabulary = getattr(getattr(settings, "statuses", None), "open", None)
        return cls(
            excluded_identities=frozenset(getattr(settings, "excluded", ()) or ()),
            deadline_horizon_days=horizon,
            open_statuses=(
                DEFAULT_ACTIVE_STATUSES if vocabulary is None else frozenset(vocabulary)
            ),
            types=types,
        )

    # -- accessors ---------------------------------------------------------

    def type_mode(self, structure_id: str) -> TypeMode:
        """The declaration for ``structure_id``; ``off`` when undeclared."""
        return self.types.get(structure_id, TypeMode())

    def mode_of(self, structure_id: str) -> str:
        """The admission mode for ``structure_id`` (``"off"`` when absent)."""
        return self.type_mode(structure_id).mode

    def open_statuses_for(self, structure_id: str) -> frozenset[str]:
        """The RESOLVED open status names for one structure.

        Own names when declared, the shared vocabulary when inherited, and the
        empty set when status admission is disabled or the mode never admits on
        status.
        """
        return self.type_mode(structure_id).resolved_open_statuses(self.open_statuses)

    def status_admission_enabled(self, structure_id: str) -> bool:
        """Whether a status label can admit an object of this structure."""
        return bool(self.open_statuses_for(structure_id))

    def structure_ids(self, mode: str | None = None) -> tuple[str, ...]:
        """Sorted declared structure ids, optionally filtered to one mode."""
        return tuple(
            sorted(
                structure_id
                for structure_id, declaration in self.types.items()
                if mode is None or declaration.mode == mode
            )
        )

    # -- adapter projections (derived, read-only, not inputs) --------------

    @property
    def native_task_structures(self) -> frozenset[str]:
        """Adapter projection: structures declared ``native``."""
        return frozenset(self.structure_ids(MODE_NATIVE))

    @property
    def active_structures(self) -> frozenset[str]:
        """Adapter projection: structures declared ``active``."""
        return frozenset(self.structure_ids(MODE_ACTIVE))

    @property
    def active_statuses(self) -> frozenset[str]:
        """Adapter projection: the shared open-status vocabulary."""
        return self.open_statuses


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
    type_mode: str
    source_assigned: bool | None
    exclusion_matched: bool
    auto_conditions: tuple[str, ...]
    logical_day: str

    @property
    def native_task(self) -> bool:
        """Whether the structure's declared mode is ``native``."""
        return self.type_mode == MODE_NATIVE


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


def _admits_on_status(candidate: AssignmentCandidate, open_names: frozenset[str]) -> bool:
    """True when the candidate's status label NAME is one of ``open_names``.

    A missing status signal never matches, so an absent payload can never be
    read as open. Names are compared normalized (case and inside-whitespace
    insensitive) on both sides; the configured text itself is preserved.
    """
    if not open_names or candidate.status is None:
        return False
    return _normalized_status(candidate.status) in {
        _normalized_status(value) for value in open_names
    }


def _auto_conditions(
    candidate: AssignmentCandidate,
    *,
    type_mode: TypeMode,
    structure_id: str | None,
    logical_day: date,
    settings: AssignmentSettings,
) -> tuple[str, ...]:
    """Return the admission conditions that matched, in deterministic order.

    Each mode reads exactly the signals its declaration names, and no other
    mode's signal is consulted: ``assigned`` reads the declared boolean,
    ``assignedOn`` the declared date, ``active`` the status label NAME, and
    ``native`` the OR of status-open, due, and deadline-within-horizon. An
    absent signal never matches.
    """
    mode = type_mode.mode
    if mode == MODE_ASSIGNED:
        return ("assigned-property",) if candidate.assigned_property is True else ()
    if mode == MODE_ASSIGNED_ON:
        if candidate.assigned_on is None:
            return ()
        try:
            on_or_before = candidate.assigned_on <= logical_day
        except TypeError:
            return ()
        return ("assigned-on-or-before-day",) if on_or_before else ()
    if mode == MODE_ACTIVE:
        if structure_id is None:
            return ()
        if _admits_on_status(candidate, settings.open_statuses_for(structure_id)):
            return ("status-active",)
        return ()
    if mode != MODE_NATIVE:
        return ()
    matched: list[str] = []
    if structure_id is not None and _admits_on_status(
        candidate, settings.open_statuses_for(structure_id)
    ):
        matched.append("status-active")
    if candidate.due is not None and candidate.due <= logical_day:
        matched.append("due-today-or-overdue")
    if candidate.deadline is not None and _deadline_within_horizon(
        candidate.deadline, logical_day, settings.deadline_horizon_days
    ):
        matched.append("deadline-within-horizon")
    return tuple(matched)


def _auto_reason(condition: str, settings: AssignmentSettings) -> AssignmentReason:
    horizon = settings.deadline_horizon_days
    details = {
        "status-active": (
            "auto-status-active",
            "Status label name is in the structure's open status names.",
        ),
        "due-today-or-overdue": (
            "auto-due-today-or-overdue",
            "Due date is today or overdue.",
        ),
        "deadline-within-horizon": (
            "auto-deadline-within-horizon",
            f"Deadline is within {horizon} calendar days of the logical day.",
        ),
        "assigned-property": (
            "auto-assigned-property",
            "The declared assignment property reads true.",
        ),
        "assigned-on-or-before-day": (
            "auto-assigned-on",
            "The declared date property is on or before the logical day.",
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

    ``settings`` accepts an :class:`AssignmentSettings` (normally derived from
    the version-2 document with :meth:`AssignmentSettings.from_v2`) or a bare
    iterable of excluded stable identities. The function is pure: it reads
    nothing else and mutates neither argument.
    """
    resolved = _resolve_settings(settings)
    identity = str(candidate.identity or "").strip()
    parsed = parse_capacities_identity(candidate.identity)
    structure_id = parsed.structure_id if parsed else None
    type_mode = resolved.type_mode(structure_id) if structure_id is not None else TypeMode()

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
                structure_id=structure_id or "",
                object_id=parsed.object_id if parsed else "",
                type_mode=type_mode.mode,
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

    # 3. TDTB stable-identity exclusion wins over any admission mode.
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

    # 4. The structure's single declared mode decides. ``off`` is never
    #    enumerated and admits nothing, so it reports its own cause instead of
    #    the generic one.
    if type_mode.mode == MODE_OFF:
        return decide(
            AssignmentMode.NONE,
            reasons=(
                _reason(
                    "mode-off",
                    "The structure's admission mode is off.",
                    "tdtb",
                ),
            ),
        )

    conditions = _auto_conditions(
        candidate,
        type_mode=type_mode,
        structure_id=structure_id,
        logical_day=logical_day,
        settings=resolved,
    )
    if conditions:
        return decide(
            AssignmentMode.AUTO,
            reasons=tuple(_auto_reason(condition, resolved) for condition in conditions),
            auto_conditions=conditions,
        )

    if type_mode.mode == MODE_ACTIVE and not resolved.status_admission_enabled(
        structure_id
    ):
        # ``active`` is a status-only mode, so an empty resolved name set leaves
        # it with nothing to admit on. ``native`` is not reported this way: its
        # statuses may be disabled BY DESIGN (date-only), and it simply missed
        # every date condition instead.
        return decide(
            AssignmentMode.NONE,
            reasons=(
                _reason(
                    "no-status-vocabulary",
                    "The status mode has no open status names to match.",
                    "tdtb",
                ),
            ),
        )

    return decide(
        AssignmentMode.NONE,
        reasons=(
            _reason(
                "no-effective-assignment",
                "No explicit assignment, exclusion, or declared-mode match.",
                "tdtb",
            ),
        ),
    )
