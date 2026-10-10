"""capacities_intake.py — offline Capacities plan rows from the published cache.

S1 of the direct-intake slice. The planning digest can read Capacities two
ways, selected by ``sources.capacities_intake`` (see ``app_config``):

  - ``legacy`` (the default): the existing read path, unchanged here.
  - ``direct``: :func:`load_direct_rows` below, which projects the published
    direct-refresh generation into plan rows with NO provider access.

The projector reads three things the refresh coordinator already publishes:
the complete-generation snapshot, the structure contract stamped with it, and
the per-object cached content. It then runs the same adapter projection the
live path uses, with the CURRENT rules, the record's mappings, the tag
exclusion policy, and the app effective date (the 02:00 rollover).

It is strict: a missing snapshot, a missing contract, any unreadable cached
member, or a contract the configured mappings no longer match yields NO rows
and an explicit warning. It never serves a truncated result, and it never falls
back to an artifact or a live read. It never reads the credential and never
calls the provider; any provider call from this path is a defect.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

_GATHER_DIR = str(Path(__file__).parent / "gather")
if _GATHER_DIR not in sys.path:
    sys.path.insert(0, _GATHER_DIR)

import tdtb_gather as gather  # noqa: E402  (path-shimmed import, see inventory.py)

import capacities_builder  # noqa: E402
import capacities_rules  # noqa: E402
import exclusion_settings  # noqa: E402
import tag_exclusions  # noqa: E402
from capacities_adapter import (  # noqa: E402
    CapacitiesAdapter,
    CapacitiesConfig,
    CapacitiesContractError,
)

SOURCE_UNREADABLE = (
    "The Capacities source record is unreadable; no Capacities rows are shown."
)
NO_GENERATION = (
    "No published Capacities generation; Refresh required. "
    "No Capacities rows are shown."
)
NO_CONTRACT = (
    "The published Capacities structure contract is missing; Refresh required. "
    "No Capacities rows are shown."
)
CONTRACT_MISMATCH = (
    "The published Capacities structure contract no longer matches the "
    "configured mappings; Refresh required. No Capacities rows are shown."
)
POLICY_UNREADABLE = (
    "Capacities rules or tag exclusions are unreadable; no Capacities rows are shown."
)


class ProviderCallDefect(RuntimeError):
    """The offline projection reached the provider. A defect, never a fallback."""


class _OfflineProvider:
    """Serves the published structure contract and refuses every other call.

    ``fetch_structures`` is the one seam the adapter reads its contract through
    (``CapacitiesAdapter._ensure_contract``). Serving the stored contract there
    validates the record's mappings exactly as the live path does, with no
    network. Object reads and writes raise :class:`ProviderCallDefect`.
    """

    def __init__(self, structures: Any) -> None:
        self._structures = structures

    def fetch_structures(self) -> Any:
        return self._structures

    def list_objects(self, *_args: Any, **_kwargs: Any) -> Any:
        raise ProviderCallDefect("offline intake must not list objects")

    def get_object(self, *_args: Any, **_kwargs: Any) -> Any:
        raise ProviderCallDefect("offline intake must not read objects")

    def patch_object(self, *_args: Any, **_kwargs: Any) -> Any:
        raise ProviderCallDefect("offline intake must not write objects")


@dataclass(frozen=True)
class DirectCoverage:
    """Bounded counts for one offline read. Identities are never included."""

    members: int = 0
    unreadable: int = 0
    evaluated: int = 0
    malformed: int = 0


def load_direct_rows(
    vault_root: str | Path,
    *,
    now: datetime | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str], DirectCoverage]:
    """Project the published cache into Capacities plan rows, offline.

    Returns ``(assigned_rows, pool_rows, warnings, coverage)``. Rows are split
    by ``assigned is True`` after an identity dedupe. ``now`` defaults to the
    wall clock and is folded into the app effective date, never ``date.today()``.
    """
    try:
        read = capacities_builder.read_direct_intake(vault_root)
    except capacities_builder.CapacitiesSourceStoreError:
        return _refused([SOURCE_UNREADABLE])
    if read is None:
        return [], [], [], DirectCoverage()

    members = len(read.snapshot.members) if read.snapshot is not None else 0
    coverage = DirectCoverage(members=members, unreadable=read.unreadable)
    if read.snapshot is None:
        return _refused([NO_GENERATION], coverage)
    if read.structures is None:
        return _refused([NO_CONTRACT], coverage)
    if read.unreadable:
        return _refused(
            [
                f"Capacities refresh cache: {read.unreadable} cached object(s) are "
                "missing or unreadable; Refresh required. No Capacities rows are shown."
            ],
            coverage,
        )

    try:
        rules = capacities_rules.load_rules(read.space_id)
        policy = tag_exclusions.ExclusionPolicy.from_read(
            exclusion_settings.read_settings(vault_root)
        )
    except (capacities_rules.RulesStoreError, exclusion_settings.ExclusionSettingsStoreError):
        return _refused([POLICY_UNREADABLE], coverage)

    logical_day: date = gather.effective_date(now if now is not None else datetime.now())
    try:
        adapter = CapacitiesAdapter(
            _OfflineProvider(read.structures),
            CapacitiesConfig(
                space_id=read.space_id,
                mappings=read.mappings,
                assignment_settings=read.settings.to_assignment_settings(),
                rules=rules,
                exclusion_policy=policy,
            ),
        )
        result = adapter.items_for_day_from_objects(logical_day, list(read.objects))
    except CapacitiesContractError:
        return _refused([CONTRACT_MISMATCH], coverage)
    except tag_exclusions.TagExclusionBlocked as exc:
        return _refused(
            [f"Capacities tag exclusions could not be evaluated: {exc}. "
             "No Capacities rows are shown."],
            coverage,
        )

    rows = _dedupe_by_identity(result.items)
    assigned = [row for row in rows if row.get("assigned") is True]
    pool = [row for row in rows if row.get("assigned") is not True]
    warnings = [*_type_warnings(read), *result.warnings]
    coverage = DirectCoverage(
        members=members,
        evaluated=result.evaluated,
        malformed=result.malformed,
    )
    return assigned, pool, warnings, coverage


def _refused(
    warnings: list[str], coverage: DirectCoverage | None = None
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str], DirectCoverage]:
    """No rows, only the explicit reason. Never a partial serve."""
    return [], [], warnings, coverage if coverage is not None else DirectCoverage()


def _type_warnings(read: capacities_builder.DirectIntakeRead) -> list[str]:
    """Per-type freshness: a configured type the generation never checked, and
    any type checked before the newest check (the single-type Rescan case)."""
    if read.snapshot is None:
        return []
    checked = dict(read.snapshot.type_check_times)
    warnings: list[str] = []
    for mapping in read.mappings:
        if mapping.structure_id not in checked:
            warnings.append(
                f"Capacities structure {mapping.structure_id!r} is not in the "
                "published generation; needs Refresh."
            )
    if checked:
        newest = max(checked.values())
        for type_key in sorted(checked):
            if checked[type_key] < newest:
                warnings.append(
                    f"Capacities structure {type_key!r} unchanged since "
                    f"{_local_day(checked[type_key])}; Refresh to check it again."
                )
    return warnings


def _local_day(checked_at: float) -> str:
    return datetime.fromtimestamp(checked_at).date().isoformat()


def _dedupe_by_identity(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep the first row for each identity. Defensive: the read is already
    keyed per object, so a repeat can only come from a corrupted input."""
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for row in rows:
        identity = row.get("identity")
        if identity:
            if identity in seen:
                continue
            seen.add(identity)
        unique.append(row)
    return unique
