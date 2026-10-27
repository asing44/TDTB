"""Deterministic placement rules derived from TDTB item metadata.

The sequencing model may choose the time, but it may not reinterpret these
relationships.  This module is deliberately independent of the model client
so prompt guidance and post-response validation use the same rules.
"""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from typing import Any

from duration_tags import is_duration_tag


_ACTIVITY_STOPWORDS = frozenset({
    "a", "an", "and", "at", "for", "in", "of", "on", "the", "to", "with",
})

_AUTO_ALIGN_RULE = "calendar_companion_aligned"
_AUTO_GRANT_RULE = "overlap_grant_synthesized"
# The reason recorded on a grant the rules derived themselves. It states the
# provenance plainly: this consent came from item metadata, not from a user.
_DETERMINISTIC_GRANT_REASON = "deterministic metadata-derived activity match"

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
            if (
                is_duration_tag(tag)
                or tag in _PERSON_TOKENS
                or tag in _ACTIVITY_STOPWORDS
            ):
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


def _span_interval(interval: tuple[int, int]) -> dict[str, str]:
    """Format a minutes span as the canonical grant interval shape."""
    return {
        "start": f"{interval[0] // 60:02d}:{interval[0] % 60:02d}",
        "end": f"{interval[1] // 60:02d}:{interval[1] % 60:02d}",
    }


def _span_text(interval: tuple[int, int]) -> str:
    formatted = _span_interval(interval)
    return f"{formatted['start']}\u2013{formatted['end']}"


def _row_span(row: dict[str, Any]) -> tuple[int, int] | None:
    """The row's minutes span, or None when it is not a usable interval.

    ``_interval`` raises on malformed times because the validator already owns
    rejecting them; deterministic repair must stay total and simply decline.
    """
    start = time_to_minutes(row.get("start"))
    end = time_to_minutes(row.get("end"))
    if start is None or end is None or end <= start:
        return None
    return start, end


def _grant_matches(
    grants: list[dict[str, Any]],
    left_id: str,
    left_interval: tuple[int, int],
    right_id: str,
    right_interval: tuple[int, int],
    fingerprint: str | None = None,
) -> bool:
    fmt = _span_interval

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


@dataclass
class DeterministicRepair:
    """The outcome of :func:`repair_deterministic`.

    ``sequence`` and ``overlap_grants`` are always safe to use as the layout
    that was validated; ``notes`` are ``{id, rule, detail}`` records in the
    same shape as the existing model-ID repairs, so they ride the validation
    warnings the UI already renders.
    """

    sequence: list[dict[str, Any]] = field(default_factory=list)
    overlap_grants: list[dict[str, Any]] = field(default_factory=list)
    notes: list[dict[str, Any]] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.notes)


def repair_deterministic(
    proposal: dict[str, Any],
    constraints: list[dict[str, Any]],
    *,
    planning_config_fingerprint: str | None = None,
    protected_ids: set[str] | None = None,
) -> DeterministicRepair:
    """Repair the deterministic, metadata-derived placement blockers.

    These relationships are already consented by the rule that derived them:
    the model only copies server numbers, and a grant for a metadata-derived
    pair records a decision nobody made at the keyboard. So both are repaired
    here instead of being reported as hard blockers:

    * a ``calendar_companion`` row is snapped to its matched event's exact
      interval, and
    * a missing exact overlap grant is synthesized at the intervals that are
      current *after* the snaps above.

    Pure and total: the input proposal is never mutated, malformed input is
    declined rather than raised on, and an input needing no repair comes back
    equal with no notes. Running it twice is a no-op the second time.

    Deliberately NOT repaired, and still hard blockers exactly as before:
    structural rules (invalid time, non-positive interval, duplicate or extra
    rows, chronological order), same-start and containment violations for
    parent/child, systems, and related groups, mint selection/window rules,
    the morning-workout ban, and snapshot/pin staleness. A grant is only ever
    synthesized for a pair that genuinely overlaps: a grant documents an
    intentional overlap, so a non-overlapping pair stays with its same-start
    blocker instead of being handed a meaningless consent record.

    ``protected_ids`` names rows whose placement is not a rule's to move —
    server pins, anchored source blocks, and injected window rows. Their
    blockers stay (now actionable, see :func:`validate_constraints`); nothing
    is snapped and no grant is synthesized on their behalf, because a
    synthesized grant must never vouch for a row that was not repaired.
    """
    source = proposal if isinstance(proposal, dict) else {}
    raw_rows = source.get("sequence")
    rows: list[dict[str, Any]] = (
        copy.deepcopy(raw_rows) if isinstance(raw_rows, list) else []
    )
    raw_grants = source.get("overlap_grants")
    grants: list[dict[str, Any]] = (
        copy.deepcopy(raw_grants) if isinstance(raw_grants, list) else []
    )
    notes: list[dict[str, Any]] = []
    fingerprint = (
        "" if planning_config_fingerprint is None
        else str(planning_config_fingerprint)
    )
    protected = {str(value) for value in (protected_ids or ())}
    if not rows or not constraints:
        return DeterministicRepair(rows, grants, notes)

    rows_by_id: dict[str, dict[str, Any]] = {}
    position_by_id: dict[str, int] = {}
    for position, row in enumerate(rows):
        if not isinstance(row, dict) or row.get("id") is None:
            continue
        key = str(row["id"])
        if key in rows_by_id:
            continue
        rows_by_id[key] = row
        position_by_id[key] = position

    def neighbour_starts(position: int) -> tuple[int | None, int | None]:
        """The nearest valid starts before and after ``position``."""
        previous: int | None = None
        following: int | None = None
        for other in range(position - 1, -1, -1):
            candidate = rows[other]
            if not isinstance(candidate, dict):
                continue
            value = time_to_minutes(candidate.get("start"))
            if value is not None:
                previous = value
                break
        for other in range(position + 1, len(rows)):
            candidate = rows[other]
            if not isinstance(candidate, dict):
                continue
            value = time_to_minutes(candidate.get("start"))
            if value is not None:
                following = value
                break
        return previous, following

    # -- calendar companion: snap to the matched event's exact interval -----
    aligned_pairs: set[tuple[str, str]] = set()
    for constraint in constraints:
        if not isinstance(constraint, dict):
            continue
        if constraint.get("kind") != "calendar_companion":
            continue
        item_id = str(constraint.get("item_id") or "")
        event_id = str(constraint.get("event_id") or "")
        row = rows_by_id.get(item_id)
        if row is None or item_id in protected:
            continue
        required = constraint.get("event_interval")
        if not isinstance(required, dict):
            continue
        required_start = canonical_time(required.get("start"))
        required_end = canonical_time(required.get("end"))
        if required_start is None or required_end is None:
            continue
        current = (canonical_time(row.get("start")),
                   canonical_time(row.get("end")))
        if current[0] is None or current[1] is None:
            # Invalid times are a structural blocker, never a repair target.
            continue
        if current == (required_start, required_end):
            continue
        new_start = time_to_minutes(required_start)
        previous, following = neighbour_starts(position_by_id[item_id])
        # Chronology is a separate hard invariant: an earlier start that would
        # reorder the sequence is left as an actionable blocker instead of
        # trading one blocker for another.
        if new_start is not None and previous is not None and new_start < previous:
            continue
        if new_start is not None and following is not None and new_start > following:
            continue
        row["start"] = required_start
        row["end"] = required_end
        aligned_pairs.add(tuple(sorted((item_id, event_id))))
        notes.append({
            "id": item_id,
            "rule": _AUTO_ALIGN_RULE,
            "detail": (
                f'Auto-aligned "{item_id}" to "{event_id}" '
                f'{required_start}\u2013{required_end}'
            ),
        })

    # -- missing exact grants: synthesize at the current intervals ----------
    pairs: list[tuple[str, str]] = []
    seen_pairs: set[tuple[str, str]] = set()
    # A calendar companion's consent is against the matched *event*, which is
    # usually an anchored block rather than a sequence row, so its interval
    # comes from the constraint instead of a row lookup. Only the movable row
    # of such a pair has to be unprotected: the event is a source fact.
    event_spans: dict[tuple[str, str], tuple[int, int]] = {}
    pair_blocked: set[tuple[str, str]] = set()

    def add_pair(left: Any, right: Any,
                 event_span: tuple[int, int] | None = None,
                 subjects: tuple[str, ...] | None = None) -> None:
        left_id, right_id = str(left or ""), str(right or "")
        if not left_id or not right_id or left_id == right_id:
            return
        key = tuple(sorted((left_id, right_id)))
        if any(subject in protected for subject in (subjects or key)):
            pair_blocked.add(key)
        if key in seen_pairs:
            return
        seen_pairs.add(key)
        pairs.append((left_id, right_id))
        if event_span is not None:
            event_spans[key] = event_span

    for constraint in constraints:
        if not isinstance(constraint, dict):
            continue
        kind = constraint.get("kind")
        if kind == "parent_child":
            add_pair(constraint.get("child_id"), constraint.get("parent_id"))
        elif kind in ("systems_group", "related_group"):
            item_ids = sorted({
                str(value) for value in (constraint.get("item_ids") or [])
            })
            for index, left in enumerate(item_ids):
                for right in item_ids[index + 1:]:
                    add_pair(left, right)
        elif kind == "calendar_companion":
            required = constraint.get("event_interval")
            span: tuple[int, int] | None = None
            if isinstance(required, dict):
                start = time_to_minutes(required.get("start"))
                end = time_to_minutes(required.get("end"))
                if start is not None and end is not None and end > start:
                    span = (start, end)
            item_id = str(constraint.get("item_id") or "")
            add_pair(item_id, constraint.get("event_id"), span,
                     subjects=(item_id,))

    def realign_grant(pair_key: tuple[str, str], left_id: str,
                      left_span: tuple[int, int], right_id: str,
                      right_span: tuple[int, int]) -> None:
        """Follow an aligned row with the grant that already consented to it.

        The row moved, so a grant written at the pre-snap interval is
        interval-stale. Re-anchoring it (instead of appending a second one)
        keeps exactly one consent record per pair across repeated
        revalidations. A grant whose fingerprint does not match is the model's
        own stale consent and is never rewritten.
        """
        for grant in grants:
            if not isinstance(grant, dict):
                continue
            if tuple(sorted((
                str(grant.get("primary_id")), str(grant.get("companion_id")),
            ))) != pair_key:
                continue
            if (
                planning_config_fingerprint is not None
                and grant.get("planning_config_fingerprint")
                    != planning_config_fingerprint
            ):
                continue
            intervals = (
                (_span_interval(left_span), _span_interval(right_span))
                if grant.get("primary_id") == left_id
                else (_span_interval(right_span), _span_interval(left_span))
            )
            current = (
                _canonical_grant_interval(grant.get("primary_interval")),
                _canonical_grant_interval(grant.get("companion_interval")),
            )
            if current == intervals:
                continue
            grant["primary_interval"], grant["companion_interval"] = intervals
            notes.append({
                "id": left_id,
                "rule": "overlap_grant_realigned",
                "detail": (
                    f'Re-anchored the overlap grant for "{left_id}" and '
                    f'"{right_id}" to {_span_text(left_span)}'
                ),
            })

    for left_id, right_id in pairs:
        if tuple(sorted((left_id, right_id))) in pair_blocked:
            continue
        left, right = rows_by_id.get(left_id), rows_by_id.get(right_id)
        if left is None:
            continue
        left_span = _row_span(left)
        if left_span is None:
            continue
        right_span = event_spans.get(tuple(sorted((left_id, right_id))))
        if right_span is None and right is not None:
            right_span = _row_span(right)
        if right_span is None:
            continue
        pair_key = tuple(sorted((left_id, right_id)))
        if pair_key in aligned_pairs:
            realign_grant(pair_key, left_id, left_span, right_id, right_span)
        if max(left_span[0], right_span[0]) >= min(left_span[1], right_span[1]):
            continue
        if _grant_matches(
            grants, left_id, left_span, right_id, right_span,
            planning_config_fingerprint,
        ):
            continue
        grants.append({
            "primary_id": left_id,
            "companion_id": right_id,
            "primary_interval": _span_interval(left_span),
            "companion_interval": _span_interval(right_span),
            "reason": _DETERMINISTIC_GRANT_REASON,
            "planning_config_fingerprint": fingerprint,
        })
        notes.append({
            "id": left_id,
            "rule": _AUTO_GRANT_RULE,
            "detail": (
                f'Recorded the exact overlap for "{left_id}" and '
                f'"{right_id}" {_span_text(left_span)}'
            ),
        })

    return DeterministicRepair(rows, grants, notes)


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
                    f" \u2014 move it to start {required['start']} and end "
                    f"{required['end']} (or unpin it)"
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
                    f" \u2014 set it to exactly {required['start']}-{required['end']}"
                    f" and record an overlap grant (or unpin it)"
                )
    return errors
