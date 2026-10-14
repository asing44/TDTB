"""Todoist task-exclusion policy evaluation — the any-match filter before
planning.

The policy is a set of persistent Todoist entries, each ``{id, name,
added_at}``. A row that belongs to Todoist is excluded when EITHER its
``todoist_id`` equals a listed id OR its normalized title equals a listed
normalized title. Matching rules, exactly:

- Id: exact string equality against the listed Todoist task id.
- Name: ``" ".join(value.split()).casefold()`` equality against the listed
  display title — case and whitespace variants match, but substrings and
  prefixes never do. The name rule is what keeps a recurring series excluded
  when a later occurrence carries a different id.
- Scope: only Todoist rows (``source`` is ``todoist`` or a ``todoist_id`` is
  present). A Capacities or vault row with the same title is never touched.
- Non-dict rows and rows without either a matching id or a matching name are
  retained. An empty policy retains everything.

This module is pure: no I/O, no provider access, no mutation of its inputs.
The persisted schema lives in :mod:`exclusion_settings`; the policy object is
the combined :class:`tag_exclusions.ExclusionPolicy` so one store read feeds
both dimensions.
"""
from __future__ import annotations

from typing import Any

from exclusion_settings import TodoistExclusion, normalize_todoist_name
from tag_exclusions import ExclusionPolicy

TODOIST_SOURCE = "todoist"


def _stable_identity(row: dict[str, Any]) -> str:
    for key in ("identity", "path", "todoist_id"):
        value = row.get(key)
        if value:
            return str(value)
    return ""


def _is_todoist_row(row: dict[str, Any]) -> bool:
    if str(row.get("source") or "").strip().casefold() == TODOIST_SOURCE:
        return True
    return bool(str(row.get("todoist_id") or ""))


def _match(
    row: Any, wanted_ids: set[str], wanted_names: set[str]
) -> dict[str, str | None] | None:
    """The matched id/name pair for a Todoist row, or ``None`` to retain it."""
    if not isinstance(row, dict) or not _is_todoist_row(row):
        return None
    row_id = str(row.get("todoist_id") or "")
    matched_id = row_id if row_id and row_id in wanted_ids else None
    normalized = normalize_todoist_name(row.get("name"))
    matched_name = (
        str(row.get("name"))
        if normalized and normalized in wanted_names
        else None
    )
    if matched_id is None and matched_name is None:
        return None
    return {"id": matched_id, "name": matched_name}


def apply_todoist_exclusions(
    assigned_items: list[dict[str, Any]],
    pool_items: list[dict[str, Any]],
    policy: ExclusionPolicy,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Filter both digest surfaces and return the diagnostics envelope.

    Mirrors :func:`tag_exclusions.apply_tag_exclusions`: both the assigned and
    pool surfaces are filtered (a source Assigned marker never overrides the
    policy), only rows actually removed here are reported, and the envelope
    carries the same version/revision/persisted/mode fields the tag stage
    surfaces so the digest stays uniformly diagnosable.
    """
    wanted_ids = {entry.id for entry in policy.todoist}
    wanted_names = {normalize_todoist_name(entry.name) for entry in policy.todoist}
    decisions: list[dict[str, Any]] = []
    evaluated = {"assigned": len(assigned_items), "pool": len(pool_items)}
    excluded = {"assigned": 0, "pool": 0}

    def _kept(row: Any, surface: str) -> bool:
        matched = _match(row, wanted_ids, wanted_names)
        if matched is None:
            return True
        decisions.append({
            "identity": _stable_identity(row),
            "name": str(row.get("name") or ""),
            "surface": surface,
            "matched": matched,
            "reason": "excluded_todoist",
        })
        return False

    kept_assigned: list[dict[str, Any]] = []
    for row in assigned_items:
        if _kept(row, "assigned"):
            kept_assigned.append(row)
        else:
            excluded["assigned"] += 1
    kept_pool: list[dict[str, Any]] = []
    for row in pool_items:
        if _kept(row, "pool"):
            kept_pool.append(row)
        else:
            excluded["pool"] += 1

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
        "warnings": [],
    }
    return kept_assigned, kept_pool, report
