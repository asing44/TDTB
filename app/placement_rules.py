"""Deterministic placement rules derived from TDTB item metadata.

The sequencing model may choose the time, but it may not reinterpret these
relationships.  This module is deliberately independent of the model client
so prompt guidance and post-response validation use the same rules.
"""
from __future__ import annotations

import re
from typing import Any


_ACTIVITY_STOPWORDS = frozenset({
    "a", "an", "and", "at", "for", "in", "of", "on", "the", "to", "with",
})


def semantic_name(value: Any) -> str:
    text = str(value or "").strip()
    text = re.sub(r"^\[\[|\]\]$", "", text)
    text = text.split("|", 1)[0]
    text = text.rsplit("/", 1)[-1]
    return re.sub(r"\.md$", "", text, flags=re.IGNORECASE).strip().casefold()


def item_id(item: dict[str, Any]) -> str:
    return str(
        item.get("id")
        or item.get("name")
        or item.get("Block")
        or item.get("calendar_title")
        or ""
    ).strip()


def item_labels(item: dict[str, Any]) -> set[str]:
    labels: set[str] = set()
    for key in ("tags", "labels"):
        raw = item.get(key) or []
        values = raw if isinstance(raw, (list, tuple, set)) else [raw]
        for value in values:
            labels.update(
                label.strip().lstrip("#").casefold()
                for label in str(value).split(",")
                if label.strip()
            )
    return labels


def relation_targets(value: Any) -> set[str]:
    raw = value if isinstance(value, (list, tuple, set)) else [value]
    out: set[str] = set()
    for entry in raw:
        text = str(entry or "")
        candidates = re.findall(r"\[\[([^|\]]+)", text) or [text]
        out.update(filter(None, (semantic_name(candidate) for candidate in candidates)))
    return out


def item_tokens(item: dict[str, Any]) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z]+", item_id(item).casefold())
        if token not in _ACTIVITY_STOPWORDS and len(token) > 2
    }


def _hhmm(value: Any) -> str | None:
    text = str(value or "").strip()
    if re.fullmatch(r"\d{1,2}:\d{2}", text):
        hour, minute = (int(part) for part in text.split(":"))
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return f"{hour:02d}:{minute:02d}"
    return None


def block_interval(item: dict[str, Any]) -> tuple[str, str] | None:
    start = _hhmm(item.get("Start") or item.get("start"))
    end = _hhmm(item.get("End") or item.get("end"))
    if not start or not end or end <= start:
        return None
    return start, end


def duration_minutes(item: dict[str, Any]) -> int:
    value = item.get("duration_minutes")
    if isinstance(value, (int, float)) and value > 0:
        return int(value)
    blocks = item.get("blocks")
    if isinstance(blocks, (int, float)) and blocks > 0:
        return int(blocks * 30)
    duration = item.get("duration")
    if isinstance(duration, (int, float)) and duration > 0:
        return int(duration)
    return 30


def derive_constraints(
    assigned: list[dict[str, Any]], anchored_blocks: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Return permanent semantic placement constraints for this sequence."""
    assigned_items = [item for item in assigned if isinstance(item, dict)]
    anchored_items = [item for item in anchored_blocks if isinstance(item, dict)]
    named = {
        semantic_name(item_id(item)): item_id(item)
        for item in assigned_items
        if semantic_name(item_id(item))
    }
    constraints: list[dict[str, Any]] = []

    for child in assigned_items:
        child_id = item_id(child)
        for target in sorted(relation_targets(child.get("relates_to"))):
            parent_id = named.get(target)
            if not parent_id or parent_id == child_id:
                continue
            constraints.append({
                "kind": "parent_child",
                "child_id": child_id,
                "parent_id": parent_id,
                "require_child_within_parent": True,
                "prefer_same_start": True,
                "reason": f"{child_id} is a sub-item of {parent_id}",
            })
            break

    systems = [
        item_id(item)
        for item in assigned_items
        if "systems" in item_labels(item)
    ]
    if len(systems) >= 2:
        constraints.append({
            "kind": "systems_group",
            "item_ids": systems,
            "require_same_start": True,
            "reason": "systems-tagged work shares one systems period",
        })

    calendar_items = [
        item for item in anchored_items
        if str(item.get("source", "")).casefold() == "calendar"
    ]
    companions = assigned_items + [
        item for item in anchored_items
        if str(item.get("source", "")).casefold() != "calendar"
    ]
    for companion in companions:
        companion_id = item_id(companion)
        tokens = item_tokens(companion)
        if not companion_id or not tokens:
            continue
        matches = [
            event for event in calendar_items
            if item_id(event) != companion_id and tokens.intersection(item_tokens(event))
        ]
        if len(matches) != 1:
            continue
        event = matches[0]
        interval = block_interval(event)
        if not interval:
            continue
        constraints.append({
            "kind": "calendar_companion",
            "item_id": companion_id,
            "event_id": item_id(event),
            "event_interval": {"start": interval[0], "end": interval[1]},
            "effective_duration_minutes": (
                (int(interval[1][:2]) * 60 + int(interval[1][3:]))
                - (int(interval[0][:2]) * 60 + int(interval[0][3:]))
            ),
            "source_duration_minutes": duration_minutes(companion),
            "reason": f"{companion_id} shares activity semantics with {item_id(event)}",
        })
    return constraints


def effective_duration_overrides(constraints: list[dict[str, Any]]) -> dict[str, int]:
    return {
        str(c["item_id"]): int(c["effective_duration_minutes"])
        for c in constraints
        if c.get("kind") == "calendar_companion"
    }


def _minutes(value: str) -> int:
    return int(value[:2]) * 60 + int(value[3:])


def _interval(row: dict[str, Any]) -> tuple[int, int]:
    return _minutes(str(row["start"])), _minutes(str(row["end"]))


def _grant_matches(
    grants: list[dict[str, Any]],
    left_id: str,
    left_interval: tuple[int, int],
    right_id: str,
    right_interval: tuple[int, int],
    fingerprint: str | None = None,
) -> bool:
    def fmt(interval: tuple[int, int]) -> dict[str, str]:
        return {
            "start": f"{interval[0] // 60:02d}:{interval[0] % 60:02d}",
            "end": f"{interval[1] // 60:02d}:{interval[1] % 60:02d}",
        }

    for grant in grants:
        if not isinstance(grant, dict):
            continue
        if fingerprint is not None and grant.get("planning_config_fingerprint") != fingerprint:
            continue
        if (
            grant.get("primary_id") == left_id
            and grant.get("companion_id") == right_id
            and grant.get("primary_interval") == fmt(left_interval)
            and grant.get("companion_interval") == fmt(right_interval)
        ) or (
            grant.get("primary_id") == right_id
            and grant.get("companion_id") == left_id
            and grant.get("primary_interval") == fmt(right_interval)
            and grant.get("companion_interval") == fmt(left_interval)
        ):
            return True
    return False


def validate_constraints(
    proposal: dict[str, Any],
    constraints: list[dict[str, Any]],
    *,
    planning_config_fingerprint: str | None = None,
) -> list[str]:
    """Return hard errors when a proposal violates derived semantic rules."""
    rows = {
        str(row.get("id")): row
        for row in proposal.get("sequence", [])
        if isinstance(row, dict) and row.get("id") is not None
    }
    grants = proposal.get("overlap_grants") or []
    errors: list[str] = []
    for constraint in constraints:
        kind = constraint.get("kind")
        if kind == "parent_child":
            child = rows.get(str(constraint["child_id"]))
            parent = rows.get(str(constraint["parent_id"]))
            if not child or not parent:
                continue
            child_span, parent_span = _interval(child), _interval(parent)
            if child_span[0] < parent_span[0] or child_span[1] > parent_span[1]:
                errors.append(
                    f"parent/child placement: {constraint['child_id']!r} must stay within "
                    f"{constraint['parent_id']!r}"
                )
            if not _grant_matches(
                grants, str(constraint["child_id"]), child_span,
                str(constraint["parent_id"]), parent_span,
                planning_config_fingerprint,
            ):
                errors.append(
                    f"parent/child placement: missing exact overlap_grant for "
                    f"{constraint['child_id']!r} and {constraint['parent_id']!r}"
                )
        elif kind == "systems_group":
            group = [rows.get(str(item_id)) for item_id in constraint["item_ids"]]
            if any(row is None for row in group):
                continue
            typed_rows = [row for row in group if row is not None]
            starts = {str(row["start"]) for row in typed_rows}
            if len(starts) != 1:
                errors.append(
                    "systems block: all systems-tagged rows must share the same start time"
                )
            for index, left in enumerate(typed_rows):
                for right in typed_rows[index + 1:]:
                    if not _grant_matches(
                        grants, str(left["id"]), _interval(left),
                        str(right["id"]), _interval(right),
                        planning_config_fingerprint,
                    ):
                        errors.append(
                            f"systems block: missing exact overlap_grant for "
                            f"{left['id']!r} and {right['id']!r}"
                        )
        elif kind == "calendar_companion":
            row = rows.get(str(constraint["item_id"]))
            if not row:
                continue
            required = constraint["event_interval"]
            actual = {"start": row["start"], "end": row["end"]}
            if actual != required:
                errors.append(
                    f"calendar companion: {constraint['item_id']!r} must match "
                    f"{constraint['event_id']!r} at {required['start']}-{required['end']}"
                )
            row_span = _interval(row)
            event_span = (_minutes(required["start"]), _minutes(required["end"]))
            if not _grant_matches(
                grants, str(constraint["item_id"]), row_span,
                str(constraint["event_id"]), event_span,
                planning_config_fingerprint,
            ):
                errors.append(
                    f"calendar companion: missing exact overlap_grant for "
                    f"{constraint['item_id']!r} and {constraint['event_id']!r}"
                )
    return errors
