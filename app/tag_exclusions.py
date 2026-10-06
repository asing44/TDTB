"""Tag-exclusion policy evaluation — the any-match filter before selection.

The policy is a set of stable, source-qualified tag identities
(``source="capacities"``, ``space_id``, ``tag_id``). A Capacities row is
excluded when ANY configured identity matches one of the tag references the
source adapter projected onto it. Matching rules, exactly:

- Identity only: ``(source, space_id, tag_id)``. No title fallback, no
  case-folding, no substring or glob matching, and no ``#`` normalization —
  the ``#`` prefix is presentation, never identity.
- A renamed tag still matches (the id is stable) and the decision reports the
  row's current title.
- An unknown/deleted policy id matches any row still carrying it; a
  same-title tag with a different id never substitutes for it.
- A different space never matches.
- An empty policy or an untagged row retains the row.

Ordinary nonmatches fail open. Unusable metadata fails closed: while at least
one exclusion applies to a Capacities row's space, a malformed or title-only
tag payload cannot be proven non-matching, so planning is blocked with a
structured diagnostic (:class:`TagExclusionBlocked`) instead of returning a
deceptively empty brief. When no exclusion applies to the row's space (or the
row is not Capacities-sourced), unusable metadata is tolerated with a warning.

This module is pure: no I/O, no provider access, no mutation of its inputs.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from exclusion_settings import SCHEMA_VERSION, ExclusionSettingsRead, TagExclusion

CAPACITIES_SOURCE = "capacities"
EXCLUDE_ANY_MODE = "exclude_any"

#: Bound on the per-task diagnostics attached to a planning block. The UI
#: needs enough to act; an unbounded list is a log, not a diagnostic.
BLOCKED_TASK_LIMIT = 10


@dataclass(frozen=True)
class ExclusionPolicy:
    """The evaluated policy: server revision plus the stable tag identities."""

    version: int
    revision: int
    persisted: bool
    tags: tuple[TagExclusion, ...]
    mode: str = EXCLUDE_ANY_MODE

    @classmethod
    def from_read(cls, read: ExclusionSettingsRead) -> "ExclusionPolicy":
        return cls(
            version=SCHEMA_VERSION,
            revision=read.settings.revision,
            persisted=read.persisted,
            tags=read.settings.tags,
        )


class TagExclusionBlocked(Exception):
    """Planning cannot be evaluated against the active tag-exclusion policy.

    Carries the bounded, client-safe ``diagnostics`` payload the routes turn
    into a structured 503 — never a silently empty brief.
    """

    def __init__(self, diagnostics: dict[str, Any]) -> None:
        super().__init__(str(diagnostics.get("message") or "tag exclusion blocked planning"))
        self.diagnostics = diagnostics


def _stable_identity(row: dict[str, Any]) -> str:
    for key in ("identity", "path", "todoist_id"):
        value = row.get(key)
        if value:
            return str(value)
    return ""


def _row_tag_refs(row: dict[str, Any]) -> tuple[list[dict[str, str]] | None, str | None]:
    """Extract structured tag references from a row.

    Returns ``(refs, None)`` for a usable payload (possibly empty), or
    ``(None, reason)`` when the payload cannot be evaluated by identity.
    """
    if "capacities_tags" in row:
        raw = row.get("capacities_tags")
        if raw is None:
            return None, str(row.get("capacities_tags_error") or "tag metadata is unusable")
        if not isinstance(raw, list):
            return None, "tag metadata is malformed"
        refs: list[dict[str, str]] = []
        for entry in raw:
            if not isinstance(entry, dict):
                return None, "tag metadata contains a malformed reference"
            space_id = entry.get("space_id")
            tag_id = entry.get("tag_id")
            if (
                not isinstance(space_id, str)
                or not space_id
                or space_id != space_id.strip()
                or not isinstance(tag_id, str)
                or not tag_id
                or tag_id != tag_id.strip()
            ):
                return None, "tag metadata contains a reference without a stable identity"
            title = entry.get("title")
            refs.append({
                "space_id": space_id,
                "tag_id": tag_id,
                "title": title if isinstance(title, str) else "",
            })
        return refs, None
    # No structured key: a non-empty flat title list is title-only metadata —
    # the flat array can say "something is tagged" but never WHICH stable
    # identity, so it cannot be evaluated. Absent/empty is genuinely empty.
    flat = row.get("tags")
    if flat is None or (isinstance(flat, list) and not flat):
        return [], None
    return None, "tag metadata is title-only or malformed"


def _policy_applies_to_row(policy: ExclusionPolicy, row: dict[str, Any]) -> bool:
    """Whether any configured exclusion could apply to this row's space.

    An unknown row space fails closed (the policy is active and the row might
    belong to any configured space); a known space with no configured
    exclusion can never match, so unusable metadata there is tolerated.
    """
    if not policy.tags:
        return False
    row_space = row.get("capacities_space_id")
    row_space = row_space if isinstance(row_space, str) else ""
    if not row_space:
        return True
    return any(entry.space_id == row_space for entry in policy.tags)


def _matched_refs(
    refs: Iterable[dict[str, str]], policy: ExclusionPolicy
) -> list[dict[str, str]]:
    wanted = {(entry.space_id, entry.tag_id) for entry in policy.tags}
    matched: list[dict[str, str]] = []
    for ref in refs:
        if (ref["space_id"], ref["tag_id"]) in wanted:
            matched.append({
                "space_id": ref["space_id"],
                "tag_id": ref["tag_id"],
                "title": ref["title"],
            })
    return matched


def apply_tag_exclusions(
    assigned_items: list[dict[str, Any]],
    pool_items: list[dict[str, Any]],
    policy: ExclusionPolicy,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Filter both digest surfaces and return the observability envelope.

    Applies the policy to assigned rows AND the candidate pool — a source
    Assigned marker must never override the policy. Only rows actually removed
    at this stage are reported; earlier stages (the Ignore List) keep their own
    attribution. Counts are post-filter by construction.
    """
    decisions: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    tolerated_unusable = 0
    evaluated = {"assigned": len(assigned_items), "pool": len(pool_items)}
    excluded = {"assigned": 0, "pool": 0}

    def _evaluate(row: Any, surface: str) -> bool:
        nonlocal tolerated_unusable
        if not isinstance(row, dict):
            return True
        if str(row.get("source") or "").strip().casefold() != CAPACITIES_SOURCE:
            # Vault/Todoist rows carry their own tags/labels as planning
            # metadata; the policy never touches them.
            return True
        refs, unusable = _row_tag_refs(row)
        if unusable is not None:
            if _policy_applies_to_row(policy, row):
                if len(blocked) < BLOCKED_TASK_LIMIT:
                    blocked.append({
                        "identity": _stable_identity(row),
                        "name": str(row.get("name") or ""),
                        "surface": surface,
                        "reason": unusable,
                    })
            else:
                tolerated_unusable += 1
            return True
        assert refs is not None
        if not policy.tags:
            return True
        matched = _matched_refs(refs, policy)
        if not matched:
            return True
        decisions.append({
            "identity": _stable_identity(row),
            "name": str(row.get("name") or ""),
            "surface": surface,
            "matched_tags": matched,
            "reason": "excluded_tag",
        })
        return False

    kept_assigned: list[dict[str, Any]] = []
    for row in assigned_items:
        if _evaluate(row, "assigned"):
            kept_assigned.append(row)
        else:
            excluded["assigned"] += 1
    kept_pool: list[dict[str, Any]] = []
    for row in pool_items:
        if _evaluate(row, "pool"):
            kept_pool.append(row)
        else:
            excluded["pool"] += 1

    if blocked:
        raise TagExclusionBlocked({
            "code": "exclusion_policy_unusable_tags",
            "message": (
                "planning is blocked because Capacities tag metadata cannot be "
                "evaluated against the active tag exclusion policy; repair the "
                "source tag payload or clear the exclusions"
            ),
            "tasks": blocked,
        })

    warnings: list[str] = []
    if tolerated_unusable:
        warnings.append(
            f"{tolerated_unusable} Capacities row(s) carry unusable tag "
            "metadata but no exclusion applies to their space"
        )

    report = {
        "version": policy.version,
        "revision": policy.revision,
        "persisted": policy.persisted,
        "mode": policy.mode,
        "evaluated_counts": evaluated,
        "excluded_counts": {
            "assigned": excluded["assigned"],
            "pool": excluded["pool"],
            "total": excluded["assigned"] + excluded["pool"],
        },
        "decisions": decisions,
        "warnings": warnings,
    }
    return kept_assigned, kept_pool, report
