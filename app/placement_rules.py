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

# FEEDBACK-27: household person tokens. A shared person name alone is not
# activity semantics — "Cook dinner with Meegy" must not inherit the span of
# "Meegy vet appointment" (live 2026-08-17 false 90-minute override). A
# companion match needs at least one shared NON-person token.
_PERSON_TOKENS = frozenset({
    "adam", "meegan", "meegy", "megan",
})

_TIME_24_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
_TIME_12_RE = re.compile(
    r"^(?:0?[1-9]|1[0-2]):[0-5]\d\s*[AaPp][Mm]$"
)


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


def canonical_time(value: Any) -> str | None:
    """Return a valid time as canonical 24-hour ``HH:MM``.

    Validation accepts the public 24-hour form and equivalent 12-hour form,
    but deliberately does not accept unpadded 24-hour values or overnight
    notation.  Keeping parsing here gives sequence and semantic placement
    rules one comparison representation without weakening malformed-time
    rejection.
    """
    if not isinstance(value, str):
        return None
    text = value.strip()
    if _TIME_24_RE.fullmatch(text):
        return text
    if not _TIME_12_RE.fullmatch(text):
        return None
    hour_text, minute_text = text[:-2].strip().split(":")
    ampm = text[-2:].casefold()
    hour, minute = int(hour_text), int(minute_text[:2])
    if ampm == "am":
        hour = 0 if hour == 12 else hour
    else:
        hour = 12 if hour == 12 else hour + 12
    return f"{hour:02d}:{minute:02d}"


def time_to_minutes(value: Any) -> int | None:
    """Parse either accepted time encoding into minutes since midnight."""
    normalized = canonical_time(value)
    if normalized is None:
        return None
    hour, minute = (int(part) for part in normalized.split(":"))
    return hour * 60 + minute


def _hhmm(value: Any) -> str | None:
    """Compatibility alias for callers that need canonical display time."""
    return canonical_time(value)


def block_interval(item: dict[str, Any]) -> tuple[str, str] | None:
    start = _hhmm(item.get("Start") or item.get("start"))
    end = _hhmm(item.get("End") or item.get("end"))
    start_min = time_to_minutes(start)
    end_min = time_to_minutes(end)
    if (not start or not end or start_min is None or end_min is None
            or end_min <= start_min):
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
    assigned_items = sorted(
        (item for item in assigned if isinstance(item, dict)),
        key=lambda item: (item_id(item).casefold(), item_id(item)),
    )
    anchored_items = sorted(
        (item for item in anchored_blocks if isinstance(item, dict)),
        key=lambda item: (item_id(item).casefold(), item_id(item)),
    )
    named: dict[str, str] = {}
    for item in assigned_items:
        target = semantic_name(item_id(item))
        if target:
            # ``setdefault`` makes duplicate display names deterministic after
            # the source rows have been put in stable order.
            named.setdefault(target, item_id(item))
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

    systems = sorted({
        item_id(item)
        for item in assigned_items
        if item_id(item) and "systems" in item_labels(item)
    }, key=lambda value: (value.casefold(), value))
    if len(systems) >= 2:
        constraints.append({
            "kind": "systems_group",
            "item_ids": systems,
            "require_same_start": True,
            "reason": "systems-tagged work shares one systems period",
        })

    # ``systems`` has a dedicated legacy rule with its own error contract. It
    # is intentionally not also treated as a related tag. Person tokens are
    # likewise identity/context labels rather than a useful reason to force
    # unrelated rows into one period.
    related_by_tag: dict[str, set[str]] = {}
    for item in assigned_items:
        current_id = item_id(item)
        if not current_id:
            continue
        for tag in item_labels(item):
            if tag in _PERSON_TOKENS or tag in _ACTIVITY_STOPWORDS:
                continue
            if tag == "systems":
                continue
            related_by_tag.setdefault(tag, set()).add(current_id)
    for tag in sorted(related_by_tag):
        item_ids = sorted(
            related_by_tag[tag], key=lambda value: (value.casefold(), value)
        )
        if len(item_ids) < 2:
            continue
        constraints.append({
            "kind": "related_group",
            "tag": tag,
            "item_ids": item_ids,
            "require_same_start": True,
            "reason": f"rows sharing the {tag!r} tag share one start time",
        })

    calendar_items = [
        item for item in anchored_items
        if str(item.get("source", "")).strip().casefold() == "calendar"
    ]
    companions = assigned_items + [
        item for item in anchored_items
        if str(item.get("source", "")).strip().casefold() != "calendar"
    ]
    calendar_items.sort(key=lambda item: (item_id(item).casefold(), item_id(item)))
    companions.sort(key=lambda item: (item_id(item).casefold(), item_id(item)))
    for companion in companions:
        companion_id = item_id(companion)
        tokens = item_tokens(companion)
        if not companion_id or not tokens:
            continue
        # FEEDBACK-27: a unique shared person token is NOT activity semantics
        # (live 2026-08-17: "Cook dinner with Meegy" vs "Meegy vet
        # appointment" fabricated a 90-minute override). Require at least one
        # shared NON-person token: "walk" in Walk Meegy survives; "meegy"
        # alone does not. Valid activity nouns like "dinner" still match.
        matches = [
            event for event in calendar_items
            if item_id(event) != companion_id
            and (tokens - _PERSON_TOKENS).intersection(item_tokens(event))
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

    # Keep the existing kind ordering for prompt/error compatibility, while
    # making every relationship and group independent of source arrival order.
    kind_order = {
        "parent_child": 0,
        "systems_group": 1,
        "calendar_companion": 2,
        "related_group": 3,
    }

    def constraint_key(constraint: dict[str, Any]) -> tuple[Any, ...]:
        kind = str(constraint.get("kind") or "")
        if kind == "parent_child":
            identity = (
                str(constraint.get("child_id") or ""),
                str(constraint.get("parent_id") or ""),
            )
        elif kind == "systems_group":
            identity = tuple(constraint.get("item_ids") or [])
        elif kind == "calendar_companion":
            identity = (
                str(constraint.get("item_id") or ""),
                str(constraint.get("event_id") or ""),
            )
        elif kind == "related_group":
            identity = (
                str(constraint.get("tag") or ""),
                tuple(constraint.get("item_ids") or []),
            )
        else:
            identity = tuple(sorted((str(key), repr(value))
                                    for key, value in constraint.items()))
        return (kind_order.get(kind, 99), identity)

    return sorted(constraints, key=constraint_key)


def effective_duration_overrides(constraints: list[dict[str, Any]]) -> dict[str, int]:
    return {
        str(c["item_id"]): int(c["effective_duration_minutes"])
        for c in constraints
        if c.get("kind") == "calendar_companion"
    }


def _minutes(value: str) -> int:
    minutes = time_to_minutes(value)
    if minutes is None:
        raise ValueError(f"invalid time: {value!r}")
    return minutes


def _interval(row: dict[str, Any]) -> tuple[int, int]:
    return _minutes(str(row["start"])), _minutes(str(row["end"]))


def _canonical_grant_interval(value: Any) -> dict[str, str] | None:
    if not isinstance(value, dict):
        return None
    start = canonical_time(value.get("start"))
    end = canonical_time(value.get("end"))
    if start is None or end is None:
        return None
    return {"start": start, "end": end}


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
        primary_interval = _canonical_grant_interval(grant.get("primary_interval"))
        companion_interval = _canonical_grant_interval(
            grant.get("companion_interval")
        )
        if (
            grant.get("primary_id") == left_id
            and grant.get("companion_id") == right_id
            and primary_interval == fmt(left_interval)
            and companion_interval == fmt(right_interval)
        ) or (
            grant.get("primary_id") == right_id
            and grant.get("companion_id") == left_id
            and primary_interval == fmt(right_interval)
            and companion_interval == fmt(left_interval)
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
    reported_missing_grants: set[tuple[Any, ...]] = set()

    def grant_key(
        left_id: str,
        left_interval: tuple[int, int],
        right_id: str,
        right_interval: tuple[int, int],
    ) -> tuple[Any, ...]:
        pair = sorted((
            (left_id, left_interval),
            (right_id, right_interval),
        ))
        return (pair[0][0], pair[0][1], pair[1][0], pair[1][1])

    def require_grant(
        left_id: str,
        left_interval: tuple[int, int],
        right_id: str,
        right_interval: tuple[int, int],
        message: str,
    ) -> None:
        key = grant_key(left_id, left_interval, right_id, right_interval)
        if key in reported_missing_grants:
            return
        reported_missing_grants.add(key)
        if not _grant_matches(
            grants, left_id, left_interval, right_id, right_interval,
            planning_config_fingerprint,
        ):
            errors.append(message)

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
            if constraint.get("prefer_same_start") is True and (
                child_span[0] != parent_span[0]
            ):
                errors.append(
                    f"parent/child placement: {constraint['child_id']!r} must "
                    f"share the same start time as {constraint['parent_id']!r}"
                )
            require_grant(
                str(constraint["child_id"]), child_span,
                str(constraint["parent_id"]), parent_span,
                f"parent/child placement: missing exact overlap_grant for "
                f"{constraint['child_id']!r} and {constraint['parent_id']!r}",
            )
        elif kind == "systems_group":
            item_ids = sorted({
                str(value) for value in constraint.get("item_ids") or []
            }, key=lambda value: (value.casefold(), value))
            group = [rows.get(item_id) for item_id in item_ids]
            if any(row is None for row in group):
                continue
            typed_rows = [row for row in group if row is not None]
            starts = {
                canonical_time(row["start"]) for row in typed_rows
            }
            if len(starts) != 1:
                errors.append(
                    "systems block: all systems-tagged rows must share the same start time"
                )
            for index, left in enumerate(typed_rows):
                for right in typed_rows[index + 1:]:
                    require_grant(
                        str(left["id"]), _interval(left),
                        str(right["id"]), _interval(right),
                        f"systems block: missing exact overlap_grant for "
                        f"{left['id']!r} and {right['id']!r}",
                    )
        elif kind == "related_group":
            item_ids = sorted({
                str(value) for value in constraint.get("item_ids") or []
            }, key=lambda value: (value.casefold(), value))
            group = [rows.get(item_id) for item_id in item_ids]
            if any(row is None for row in group):
                continue
            typed_rows = [row for row in group if row is not None]
            if constraint.get("require_same_start") is True:
                starts = {
                    canonical_time(row.get("start")) for row in typed_rows
                }
                if len(starts) != 1:
                    errors.append(
                        f"related tag {constraint['tag']!r}: all related rows "
                        "must share the same start time"
                    )
            for index, left in enumerate(typed_rows):
                for right in typed_rows[index + 1:]:
                    require_grant(
                        str(left["id"]), _interval(left),
                        str(right["id"]), _interval(right),
                        f"related tag {constraint['tag']!r}: missing exact "
                        f"overlap_grant for {left['id']!r} and {right['id']!r}",
                    )
        elif kind == "calendar_companion":
            row = rows.get(str(constraint["item_id"]))
            if not row:
                continue
            required = constraint["event_interval"]
            required_start = canonical_time(required.get("start"))
            required_end = canonical_time(required.get("end"))
            if required_start is None or required_end is None:
                continue
            actual = {
                "start": canonical_time(row["start"]),
                "end": canonical_time(row["end"]),
            }
            required = {"start": required_start, "end": required_end}
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
