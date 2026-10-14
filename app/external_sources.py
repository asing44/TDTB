"""Read-side aggregation for the non-note sources (gather-parity plan, 2026-07-14).

Composes the app's existing read primitives — ``TodoistClient.get_filter_tasks``
and ``calendar_bridge.EventStore.query_events`` — into digest-ready shapes.
``main.py`` merges these outputs.

Degrade contract (locked decision 3): every fetch returns its data plus a
``warnings`` list and NEVER raises — a missing token / EventKit grant yields
empty data + one human-readable warning the UI must surface loudly.

Habits are a CAPACITY summary, not digest items — the skill SOT states habits
are never placed on the timeline and never become tasks/events; only the
outstanding-minutes total deducts from capacity (SKILL.md § Habits).
"""
from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta
from pathlib import Path
from dataclasses import dataclass
from typing import Any

from duration_tags import is_quick_task_tag

# v1 /tasks/filter takes filter QUERY strings, not saved-filter IDs (the app's
# existing live callers pass "today" — shadow.py). Saved-filter IDs from the
# config Schema Reference stay write-path-only.
ASSIGNED_QUERY_FALLBACK = "today | overdue"
# The live label is "🚀 10min" WITH A SPACE. Todoist /tasks/filter treats an
# unescaped space as a token boundary (the bare "@🚀10min" matches nothing) and
# does not accept a quoted form, so the space must be backslash-escaped.
QUICK_QUERY_FALLBACK = "@🚀\\ 10min"

_REMINDER_LABEL = "🔔Reminder"
_NUDGE_PREFIX = "🔔 Nudge:"


# ---------------------------------------------------------------------------
# Todoist
# ---------------------------------------------------------------------------

def _due_date(task: dict[str, Any]) -> str | None:
    due = task.get("due") or {}
    raw = due.get("date") or due.get("datetime") or ""
    return raw[:10] or None


def _due_time(task: dict[str, Any]) -> str | None:
    """Return the task's existing local wall-clock time, when Todoist sent one."""
    due = task.get("due") or {}
    raw = str(due.get("datetime") or due.get("date") or "")
    match = re.search(r"T(\d{2}):(\d{2})", raw)
    return f"{match.group(1)}:{match.group(2)}" if match else None


def _duration_minutes(task: dict[str, Any]) -> int | None:
    dur = task.get("duration") or {}
    if dur.get("unit") == "minute" and dur.get("amount"):
        return int(dur["amount"])
    if dur.get("unit") == "day" and dur.get("amount"):
        return int(dur["amount"]) * 24 * 60
    return None

#: Todoist projects whose tasks are habit nudges, not plannable work. Habits
#: reach the plan only as a capacity summary, so their tasks never become rows.
_HABIT_PROJECT_NAMES = frozenset({"habits"})


def _normalize_project_name(name: Any) -> str:
    """Lowercase letters/digits only, so "🔁 Habits" and "habits" compare equal."""
    return "".join(ch for ch in str(name or "").lower() if ch.isalnum())


def _habit_project_ids(client: Any) -> set[str]:
    """IDs of the habit projects; empty when the lookup is unavailable."""
    getter = getattr(client, "get_projects", None)
    if not callable(getter):
        return set()
    try:
        projects = getter()
    except Exception:  # noqa: BLE001 — best effort; rows stay visible on failure
        return set()
    return {
        str(p.get("id"))
        for p in projects
        if isinstance(p, dict)
        and _normalize_project_name(p.get("name")) in _HABIT_PROJECT_NAMES
    }


def _is_reminder(task: dict[str, Any]) -> bool:
    if _REMINDER_LABEL in (task.get("labels") or []):
        return True
    return str(task.get("content", "")).startswith(_NUDGE_PREFIX)


def _to_item(task: dict[str, Any], assigned: bool) -> dict[str, Any]:
    tid = str(task.get("id"))
    # Todoist API priority: 4 = highest — same direction as the urgency scale.
    priority = int(task.get("priority") or 1)
    return {
        "name": task.get("content") or f"(untitled {tid})",
        "path": f"todoist://{tid}",
        "types": ["todoist"],
        "urgency": priority,
        "deadline": _due_date(task),
        "priority_score": float(priority),
        "assigned": assigned,
        "source": "todoist",
        "todoist_id": tid,
        "duration": _duration_minutes(task),
        "labels": task.get("labels") or [],
        # recurring tasks are schedule-pinned (M1.0): surfaced so the UI can
        # suppress manual adjustment controls, mirroring shadow's no-op rule
        "is_recurring": bool((task.get("due") or {}).get("is_recurring")),
        "scheduled_start": _due_time(task),
    }


def fetch_todoist_items(
    client: Any, config: dict[str, Any], exclude_ids: set[str] | None = None
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    """Pull assigned (due today/overdue) + quick-pool tasks.

    ``exclude_ids`` drops app-created Capacities mirror tasks by Todoist id so
    a mirror never re-appears as a Todoist-sourced row on a later load
    (including later days while it is still open).

    Returns ``(assigned_items, pool_items, warnings)``; never raises.
    """
    if client is None:
        return [], [], ["Todoist unavailable (no client/token) — digest is missing Todoist items"]

    excluded = {str(value) for value in (exclude_ids or ())}
    assigned_q = str(config.get("todoist.read_query.assigned") or ASSIGNED_QUERY_FALLBACK)
    quick_q = str(config.get("todoist.read_query.quick") or QUICK_QUERY_FALLBACK)
    try:
        habit_projects = _habit_project_ids(client)

        def _plannable(t: dict[str, Any]) -> bool:
            return (not _is_reminder(t)
                    and str(t.get("project_id")) not in habit_projects
                    and str(t.get("id")) not in excluded)

        assigned_tasks = [t for t in client.get_filter_tasks(assigned_q) if _plannable(t)]
        quick_tasks = [t for t in client.get_filter_tasks(quick_q) if _plannable(t)]
    except Exception as exc:  # noqa: BLE001 — degrade contract
        return [], [], [f"Todoist read failed ({exc}) — digest is missing Todoist items"]

    assigned_items = [_to_item(t, assigned=True) for t in assigned_tasks]
    assigned_ids = {i["todoist_id"] for i in assigned_items}
    pool_items = [
        _to_item(t, assigned=False)
        for t in quick_tasks
        if str(t.get("id")) not in assigned_ids
    ]
    return assigned_items, pool_items, []


def fetch_capacities_items(
    adapter: Any, logical_day: date
) -> tuple[list[dict[str, Any]], list[str]]:
    """Read accepted Capacities objects through an injected adapter.

    The route never constructs a provider implicitly. This keeps tests and
    shadow/scratch runs offline while allowing an attended runtime to inject a
    configured application-owned REST adapter. Any provider or contract error
    degrades to a visible warning and an empty source, never to partial rows.
    """
    if adapter is None:
        return [], []
    try:
        result = adapter.items_for_day(logical_day)
        items = list(result.items)
        warnings = list(result.warnings)
        return items, warnings
    except Exception as exc:  # noqa: BLE001 — source boundary degrades
        return [], [f"Capacities read failed ({exc}) — source is unavailable"]
    finally:
        close = getattr(adapter, "close", None)
        if callable(close):
            try:
                close()
            except Exception:  # noqa: BLE001 — cleanup must not mask read result
                pass


def disambiguate_names(
    base_items: list[dict[str, Any]], ext_items: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Rename external items whose name collides with a base item (or an
    earlier external item). Sequence identity is name-keyed downstream
    (timeline sets id = name), so duplicate names break never-bump and
    duplicate-row validation. Base names stay canonical; collisions get a
    source-specific suffix such as ``(Todoist)`` or ``(Capacities)``. Mutates
    copies, not inputs.
    """
    base_taken = {str(i.get("name", "")).casefold() for i in base_items}
    taken = set(base_taken)
    out: list[dict[str, Any]] = []
    for item in ext_items:
        copy = dict(item)
        name = str(copy.get("name", ""))
        if name.casefold() in taken:
            # A base-item collision reads best as a source tag; a same-source
            # duplicate just numbers up.
            source_label = {
                "todoist": "Todoist",
                "capacities": "Capacities",
            }.get(str(copy.get("source") or "").strip().casefold(), "Source")
            candidates = (
                [f"{name} ({source_label})"]
                if name.casefold() in base_taken
                else []
            )
            candidates += [f"{name} ({n})" for n in range(2, len(ext_items) + 2)]
            copy["name"] = next(c for c in candidates if c.casefold() not in taken)
        taken.add(str(copy["name"]).casefold())
        out.append(copy)
    return out


# ---------------------------------------------------------------------------
# Calendar
# ---------------------------------------------------------------------------

def fetch_calendar_decisions(
    store: Any, config: dict[str, Any], today: date
) -> tuple[list[dict[str, Any]], list[str], list[dict[str, Any]]]:
    """Today's events as anchored-block-shaped busy blocks, plus one
    structured decision per considered event (issue #6 detailed contract).

    Returns ``(blocks, warnings, decisions)``; never raises. ``blocks`` and
    ``warnings`` are exactly the frozen two-value ``fetch_calendar_busy``
    contract; ``decisions`` adds one diagnosable row per queried event:
    ``included`` (emitted as a busy block), ``excluded`` (disabled calendar,
    duplicate representation, or zero/negative duration), or ``unresolved``
    (malformed start/end). Each decision carries a stable identity
    (``event_id``/``id`` or a deterministic field composite), title, calendar
    identity, a human-readable reason, and capacity class plus start/end
    when applicable.

    Underlying classification:

    T12j preserves source calendar identity and resolves each event to one of
    four durable capacity states: ``fixed`` (default for unlisted or
    unclassified timed calendars — FEEDBACK-27 live contract), ``work``,
    ``ignored``, or ``quarantined`` (only via an explicit Calendar Capacity
    Classes row; excluded from capacity/planning, contract 17 exclusion kept).
    TDTB's own zone-write IDs are always ``ignored`` even if a conflicting
    title rule exists. Ignored rows remain on the wire so the UI can explain
    why they cost zero instead of silently hiding them.

    Frozen contract 16: events sharing a canonical identity (``event_id`` /
    ``id``) canonicalize into ONE logical group — attendance and capacity
    each count once. First occurrence wins. Identity-less events deduplicate
    by the deterministic canonical composite (calendar identity, title, raw
    interval, all_day flag): identical composites merge into one row, while
    distinct identity-less events each emit their own row. Registration of
    BOTH identities happens BEFORE disabled/unknown filtering, so a later
    known copy of an already-seen event can never be included after its
    first occurrence was excluded — first-occurrence-wins holds across
    exclusion boundaries.

    Legacy test seam (do not rely on this in new code): stores that expose
    no calendar inventory at all — old test fakes without a ``calendars()``
    method — keep the historical known/fixed treatment for their events.
    Production stores always expose an inventory; this carve-out exists only
    so legacy fixtures keep passing and is explicitly NOT a production path.

    Frozen contract 18: all-day source events stay all-day and non-timed on
    the wire. They carry ``all_day: True`` and NO ``Start``/``End`` — every
    ordinary planning/commit path that converts timed rows sees nothing to
    convert, so no implicit timed inference can occur.
    """
    import calendar_bridge

    if store is None:
        return [], ["Calendar unavailable (no EventKit store/grant) — busy blocks missing"], []

    # An unauthorized EventStore returns [] from query_events without raising —
    # exactly the silent-degrade this module must never allow (bake-in day 1
    # live check caught auth 'notDetermined' masquerading as a free day).
    # Fakes without auth_status are treated as authorized.
    # macOS 14+ reports "fullAccess"; pre-14 reports "authorized". Both grant
    # event reads — anything else (notDetermined/denied/writeOnly/...) degrades.
    auth = getattr(store, "auth_status", lambda: "authorized")()
    if auth not in ("authorized", "fullAccess"):
        return [], [
            f"Calendar access {auth} — busy blocks missing "
            "(grant via EventStore().request_access() once)"
        ], []

    # Authorized but zero visible calendars is a distinct silent-degrade:
    # incident 2026-07-16 — an EventKit grant didn't carry to a restarted
    # process, store.calendars() returned [], and the day looked legitimately
    # free instead of degraded. Fakes without calendars() are treated as fine
    # (same defensive-getattr pattern as auth_status above).
    cals: list[Any] = []
    calendars_fn = getattr(store, "calendars", None)
    if calendars_fn is not None:
        try:
            cals = calendars_fn()
        except Exception as exc:  # noqa: BLE001 — degrade contract
            return [], [f"Calendar read failed ({exc}) — busy blocks missing"], []
        if not cals:
            return [], [
                "Calendar store has 0 visible calendars — grant likely "
                "missing for this process; busy blocks missing"
            ], []

    own_ids = set((config.get("calendar_ids") or {}).values())
    title_classes = calendar_bridge.normalize_capacity_class_map(
        config.get("calendar_capacity_classes")
        or config.get("Calendar Capacity Classes")
    )
    # Unit 1: the user-chosen list of calendars whose all-day events become
    # schedulable planning rows. Fail-safe: only a list opts anything in, and
    # an empty/absent key leaves every calendar on the frozen contract-18
    # all-day path.
    schedulable_all_day = calendar_bridge.normalize_schedulable_all_day(
        config.get("schedulable_all_day")
        or config.get("Schedulable All-Day Calendars")
    )

    def _cal_value(cal: Any, attr: str) -> Any:
        if isinstance(cal, dict):
            aliases = {
                "identifier": ("identifier", "id", "calendar_id", "calendarID"),
                "title": ("title", "calendar_title"),
            }
            return next(
                (cal.get(key) for key in aliases[attr] if cal.get(key) is not None),
                None,
            )
        return getattr(cal, attr, None)

    title_by_id: dict[str, str] = {}
    id_title_by_lower: dict[str, tuple[str, str]] = {}
    for cal in cals:
        identifier = _cal_value(cal, "identifier")
        title = _cal_value(cal, "title")
        if identifier and title:
            sid = str(identifier)
            title_by_id[sid] = str(title)
            id_title_by_lower[sid.casefold()] = (sid, str(title))
    # True only when the store actually exposed a calendar inventory; fakes
    # without a calendars() method (cals stays []) keep legacy behavior.
    have_inventory = bool(cals)

    # Issue #6: calendars disabled for planning contribute no rows, walls, or
    # capacity. A calendar is disabled either via the config list
    # (``calendar_disabled`` / ``Disabled Calendars``) or via an explicit
    # disable flag on the calendar object itself.
    disabled_names = calendar_bridge.normalize_disabled_calendars(
        config.get("calendar_disabled") or config.get("Disabled Calendars")
    )
    disabled_ids: set[str] = set()
    disabled_titles: set[str] = set()
    for cal in cals:
        if calendar_bridge.is_calendar_planning_disabled(cal, disabled_names):
            ident = _cal_value(cal, "identifier")
            title = _cal_value(cal, "title")
            if ident is not None:
                disabled_ids.add(str(ident).casefold())
            if title is not None:
                disabled_titles.add(str(title).strip().casefold())
    match_keys = disabled_names | disabled_ids | disabled_titles

    start = datetime.combine(today, time.min)
    end = start + timedelta(days=1)
    try:
        events = store.query_events(start, end, None)
    except Exception as exc:  # noqa: BLE001 — degrade contract
        return [], [f"Calendar read failed ({exc}) — busy blocks missing"], []

    blocks: list[dict[str, Any]] = []
    warnings: list[str] = []
    decisions: list[dict[str, Any]] = []
    omitted: list[str] = []
    seen_stable_ids: set[str] = set()
    seen_composites: set[str] = set()
    duplicates = 0

    def _build_decision(
        identity: str,
        title: str,
        source_calendar_id: str | None,
        calendar_id: str | None,
        calendar_title: str | None,
        decision: str,
        reason_code: str,
        reason: str,
        capacity_class: str | None = None,
        start: str | None = None,
        end: str | None = None,
        all_day: bool | None = None,
    ) -> dict[str, Any]:
        """Issue #6: single helper that builds every decision row with the
        SAME complete schema: stable identity, raw + canonical calendar IDs,
        title, decision, reason_code/reason, capacity_class, and interval/
        all_day fields. Nullable values are always explicit (None), never
        omitted — every branch emits the exact same shape."""
        row: dict[str, Any] = {
            "identity": identity,
            "title": title,
            "source_calendar_id": source_calendar_id,
            "calendar_id": calendar_id,
            "calendar_title": calendar_title,
            "decision": decision,
            "reason_code": reason_code,
            "reason": reason,
            "capacity_class": capacity_class,
            "start": start,
            "end": end,
            "all_day": all_day,
        }
        return row

    for ev in events:
        raw_calendar_id = ev.get("calendar_id")
        source_calendar_id = (
            str(raw_calendar_id).strip()
            if raw_calendar_id is not None and str(raw_calendar_id).strip() != ""
            else None
        )
        calendar_id: str | None = source_calendar_id
        calendar_title: str | None = None
        # Issue #6: canonicalize the calendar identity. A missing/empty ID
        # uses the single-calendar fallback; an unlisted ID in a sole visible
        # calendar keeps its source identity and the unlisted fixed default.
        # Only an unlisted ID in an ambiguous inventory is unresolved.
        if not calendar_id and cals and len(cals) == 1:
            canonical_id = _cal_value(cals[0], "identifier")
            canonical_title = _cal_value(cals[0], "title")
            if canonical_id is not None:
                calendar_id = str(canonical_id)
            if canonical_title is not None:
                calendar_title = str(canonical_title)
            calendar_known = True
        elif calendar_id:
            lowered = calendar_id.casefold()
            if lowered in id_title_by_lower:
                calendar_id, calendar_title = id_title_by_lower[lowered]
                calendar_known = True
            elif not have_inventory:
                # Store exposes no calendar inventory (fake/test seam): keep
                # the legacy fixed/visible treatment for its events.
                calendar_known = True
            elif len(cals) == 1:
                # A sole visible calendar is the default scope for an event
                # whose source ID is not present in the inventory. Preserve
                # the raw source ID/title identity while retaining the
                # FEEDBACK-27 fixed default; multi-calendar misses remain
                # unresolved so issue #6 never silently attributes them.
                calendar_known = True
            else:
                calendar_known = False
        else:
            calendar_known = False

        title_disp = ev.get("title") or "(untitled event)"
        cal_key = (calendar_id or calendar_title or source_calendar_id or "")
        composite_identity = "|".join(
            str(v)
            for v in (
                cal_key.casefold(),
                ev.get("title") or "",
                ev.get("start"),
                ev.get("end"),
                ev.get("all_day"),
            )
        )
        identity_raw = ev.get("event_id") or ev.get("id")
        has_stable_id = identity_raw is not None and str(identity_raw) != ""
        identity = str(identity_raw) if has_stable_id else composite_identity

        # Issue #6: capacity class is only assigned when the calendar identity
        # is known; unknown calendars never become fixed capacity.
        capacity_class: str | None = None
        if calendar_known:
            capacity_class = title_classes.get(calendar_title or "", "fixed")
            if calendar_id in own_ids:
                capacity_class = "ignored"

        # Issue #6: interval values are computed ONCE per event, before any
        # classification branch, so every decision row reports the same
        # complete picture. Datetimes render as HH:MM; malformed raw values
        # (strings, garbage) are preserved as-is when present. all_day is
        # always an explicit boolean — True for all-day sources, False for
        # every timed, malformed, or zero-duration event.
        if isinstance(ev.get("start"), datetime):
            interval_start = ev["start"].strftime("%H:%M")
        elif ev.get("start") is not None:
            interval_start = str(ev["start"])
        else:
            interval_start = None
        if isinstance(ev.get("end"), datetime):
            interval_end = ev["end"].strftime("%H:%M")
        elif ev.get("end") is not None:
            interval_end = str(ev["end"])
        else:
            interval_end = None
        all_day_flag = bool(ev.get("all_day"))

        # Contract 16: duplicate source events canonicalize into one logical
        # group; first occurrence wins. Stable-ID records dedup ONLY by stable
        # ID. Identity-less records dedup ONLY by the canonical composite
        # (canonical calendar identity/title/time) — the two namespaces never
        # cross, so an identity-less event is never collapsed against a
        # stable-ID record, while identity-less equivalents whose raw IDs
        # canonicalize to the same calendar still merge.
        # Registration happens FIRST, before disabled/unknown filtering, so a
        # later known copy of an identity whose first occurrence was excluded
        # can never slip through as an included block.
        if has_stable_id:
            if identity in seen_stable_ids:
                duplicates += 1
                reason = (
                    f"{title_disp}: duplicate event "
                    "representation merged by stable ID"
                )
                decisions.append(_build_decision(
                    identity, title_disp, source_calendar_id, calendar_id,
                    calendar_title, "excluded", "excluded_duplicate", reason,
                    capacity_class=capacity_class,
                    start=interval_start, end=interval_end,
                    all_day=all_day_flag,
                ))
                continue
            seen_stable_ids.add(identity)
        else:
            if composite_identity in seen_composites:
                duplicates += 1
                reason = (
                    f"{title_disp}: duplicate event "
                    "representation merged by canonical composite identity"
                )
                decisions.append(_build_decision(
                    identity, title_disp, source_calendar_id, calendar_id,
                    calendar_title, "excluded", "excluded_duplicate", reason,
                    capacity_class=capacity_class,
                    start=interval_start, end=interval_end,
                    all_day=all_day_flag,
                ))
                continue
            seen_composites.add(composite_identity)

        # Issue #6: disabled/inactive calendars contribute no rows, walls, or
        # capacity — omissions carry a user-readable reason.
        cal_keys = set()
        for value in (calendar_id, calendar_title, source_calendar_id):
            if value:
                cal_keys.add(str(value).strip().casefold())
        if cal_keys & match_keys:
            reason = (
                f"{title_disp}: calendar "
                f"{calendar_title or calendar_id or '(unknown)'} is disabled for planning"
            )
            omitted.append(reason)
            decisions.append(_build_decision(
                identity, title_disp, source_calendar_id, calendar_id,
                calendar_title, "excluded", "excluded_disabled_calendar",
                reason,
                capacity_class=capacity_class,
                start=interval_start, end=interval_end,
                all_day=all_day_flag,
            ))
            continue

        # Issue #6: a non-empty calendar ID that does not resolve to the
        # store's inventory is unknown — the event is unresolved, warned, and
        # never becomes fixed capacity.
        if not calendar_known:
            reason = (
                f"{title_disp}: unknown calendar ID '{source_calendar_id}' — "
                "event unresolved (not attributable to a known calendar)"
            )
            omitted.append(reason)
            decisions.append(_build_decision(
                identity, title_disp, source_calendar_id, None, None,
                "unresolved", "unresolved_unknown_calendar", reason,
                capacity_class=None,
                start=interval_start, end=interval_end,
                all_day=all_day_flag,
            ))
            continue
        # FEEDBACK-27: unlisted/unclassified timed calendars default FIXED and
        # stay visible — the live contract requires real timed commitments to
        # surface as capacity (2026-08-17 incident: an unclassified calendar
        # must not be silently quarantined away). Explicit title->class config
        # and own-write IDs still win; a configured "quarantined" class keeps
        # the contract-17 exclusion behavior.
        # Contract 18: all-day events remain all-day and non-timed — emitted
        # without Start/End so no timed planning path can convert them.
        if ev.get("all_day"):
            # Unit 1: an all-day event on an OPTED-IN calendar with a stable
            # event id becomes a schedulable planning row (one 30-minute
            # block) instead of a non-timed all-day strip entry. The disabled
            # (excluded_disabled_calendar) and unknown
            # (unresolved_unknown_calendar) branches above already ran FIRST,
            # and the ignored/quarantined capacity classes still win here: a
            # classified own-write or quarantined calendar keeps the frozen
            # contract-18 behaviour. An event without a stable id is
            # unchanged. The original source event is never modified.
            cal_keys = {
                str(value).strip().casefold()
                for value in (calendar_id, calendar_title, source_calendar_id)
                if value
            }
            if (
                has_stable_id
                and schedulable_all_day
                and (cal_keys & schedulable_all_day)
                and calendar_id not in own_ids
                and capacity_class != "quarantined"
            ):
                # Identity keys off the LOGICAL day the route is planning
                # (``today``), not the raw event start: the day is the
                # contract unit a schedulable row belongs to, and it matches
                # the Ignore List's path form.
                event_day = today.isoformat()
                blocks.append({
                    "Block": title_disp,
                    "source": "calendar",
                    "calendar_id": calendar_id or None,
                    "calendar_title": calendar_title,
                    "capacity_class": capacity_class,
                    "all_day": True,
                    "schedulable_all_day": True,
                    "event_id": identity,
                    "identity": f"calendar:{identity}:{event_day}",
                    "path": f"calendar://{identity}/{event_day}",
                })
                decisions.append(_build_decision(
                    identity, title_disp, source_calendar_id, calendar_id,
                    calendar_title, "included", "included_schedulable_all_day",
                    "all-day event on an opted-in calendar emitted as a "
                    "schedulable planning row",
                    capacity_class=capacity_class, all_day=True,
                ))
                continue
            blocks.append({
                "Block": title_disp,
                "source": "calendar",
                "calendar_id": calendar_id or None,
                "calendar_title": calendar_title,
                "capacity_class": capacity_class,
                "all_day": True,
            })
            decisions.append(_build_decision(
                identity, title_disp, source_calendar_id, calendar_id,
                calendar_title, "included", "included_all_day",
                "all-day event retained non-timed (contract 18)",
                capacity_class=capacity_class, all_day=True,
            ))
            continue
        ev_start, ev_end = ev.get("start"), ev.get("end")
        if not isinstance(ev_start, datetime) or not isinstance(ev_end, datetime):
            reason = (
                f"{title_disp}: malformed event "
                "(missing or non-datetime start/end)"
            )
            omitted.append(reason)
            decisions.append(_build_decision(
                identity, title_disp, source_calendar_id, calendar_id,
                calendar_title, "unresolved", "unresolved_malformed_interval",
                reason,
                capacity_class=capacity_class,
                start=interval_start, end=interval_end,
                all_day=all_day_flag,
            ))
            continue
        # Zero/negative-duration events are reminder-style markers, not busy
        # time — echoing one as an anchored block is unsatisfiable downstream
        # (judgment rejects end <= start; T11 live: "2.0M" 23:00-23:00).
        if ev_end <= ev_start:
            reason = (
                f"{title_disp}: zero or negative "
                "duration (reminder-style marker)"
            )
            omitted.append(reason)
            decisions.append(_build_decision(
                identity, title_disp, source_calendar_id, calendar_id,
                calendar_title, "excluded", "excluded_zero_duration", reason,
                capacity_class=capacity_class,
                start=interval_start, end=interval_end,
                all_day=all_day_flag,
            ))
            continue
        block = {
            "Block": title_disp,
            "Start": ev_start.strftime("%H:%M"),
            "End": ev_end.strftime("%H:%M"),
            "source": "calendar",
            "calendar_id": calendar_id or None,
            "calendar_title": calendar_title,
            "capacity_class": capacity_class,
        }
        if calendar_id and calendar_id in own_ids:
            block["own_write"] = True
        blocks.append(block)
        decisions.append(_build_decision(
            identity, title_disp, source_calendar_id, calendar_id,
            calendar_title, "included", "included_timed",
            "timed event included as busy block",
            capacity_class=capacity_class,
            start=block["Start"], end=block["End"],
            all_day=all_day_flag,
        ))
    if duplicates:
        warnings.append(
            f"Calendar: {duplicates} duplicate event representation(s) merged "
            "by canonical identity"
        )
    for reason in omitted:
        warnings.append(f"Calendar omission: {reason}")
    return blocks, warnings, decisions


def fetch_calendar_busy(
    store: Any, config: dict[str, Any], today: date
) -> tuple[list[dict[str, Any]], list[str]]:
    """Backward-compatible two-value wrapper around
    :func:`fetch_calendar_decisions` (issue #6): returns only
    ``(blocks, warnings)`` with identical content.
    """
    blocks, warnings, _decisions = fetch_calendar_decisions(store, config, today)
    return blocks, warnings


# ---------------------------------------------------------------------------
# Unit 1: schedulable all-day intake
# ---------------------------------------------------------------------------

def split_schedulable_all_day(
    blocks: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split opted-in all-day events out of the calendar busy frame.

    A block emitted by :func:`fetch_calendar_decisions` with
    ``schedulable_all_day`` is a schedulable planning row, not a busy wall.
    Returns ``(busy_blocks_without_them, rows)``. Without any opt-in this is
    a pass-through that preserves every input block unchanged. Each row
    carries the digest identity/source/path the plan-inputs assembly needs;
    the row shape matches an assigned digest row (``blocks: 1``).
    """
    busy: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    for block in blocks:
        if block.get("schedulable_all_day") is True:
            rows.append({
                "name": block.get("Block"),
                "source": "calendar",
                "assigned": True,
                "blocks": 1,
                "identity": block.get("identity"),
                "path": block.get("path"),
                "event_id": block.get("event_id"),
                "calendar_id": block.get("calendar_id"),
                "calendar_title": block.get("calendar_title"),
                "capacity_class": block.get("capacity_class"),
            })
        else:
            busy.append(block)
    return busy, rows


def exclude_already_committed(
    rows: list[dict[str, Any]],
    blocks: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Drop schedulable rows already committed as timed own-calendar events.

    A row is dropped when a TIMED event on TDTB's own zone-write calendar
    (``capacity_class == "ignored"``, i.e. an own_ids match) has an exactly
    equal title: that work is already on the timeline as a real commitment, so
    emitting a second schedulable copy would double-place it. Title-only
    match per the unit-1 contract — a non-ignored class or an all-day own
    block never dedupes. Returns ``(kept_rows, decision_rows)``.
    """
    committed = [
        block for block in blocks
        if str(block.get("source") or "").strip().casefold() == "calendar"
        and block.get("own_write") is True
        and not block.get("all_day")
        and block.get("Start") is not None
        and block.get("End") is not None
    ]
    kept: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    for row in rows:
        title = row.get("name")
        if not any(block.get("Block") == title for block in committed):
            kept.append(row)
            continue
        decisions.append({
            "identity": row.get("identity"),
            "title": title,
            "source_calendar_id": row.get("calendar_id"),
            "calendar_id": row.get("calendar_id"),
            "calendar_title": row.get("calendar_title"),
            "decision": "excluded",
            "reason_code": "excluded_already_committed",
            "reason": (
                f"{title}: already committed as a timed event on the "
                "TDTB Blocks calendar"
            ),
            "capacity_class": row.get("capacity_class"),
            "start": None,
            "end": None,
            "all_day": True,
        })
    return kept, decisions


# ---------------------------------------------------------------------------
# Schedulable-block builder (ui-parity T5)
# ---------------------------------------------------------------------------

QUICK_LABEL = "🚀10min"

_SCHED_DEFAULTS = {  # skill 792–800: Block / default-on / default blocks
    "minting": {"name": "Minting", "on": True, "n": 2},
    "qt": {"name": "Quick Tasks", "on": True, "n": 1},
    "shivery": {"name": "Shivery Jigs", "on": False, "n": 1},
}


def _hhmm_min(value: Any) -> int | None:
    import time_engine
    hhmm = time_engine.to_hhmm(value)
    return None if hhmm is None else int(hhmm[:2]) * 60 + int(hhmm[3:])


def _trinoor_slots(config: dict[str, Any]) -> list[tuple[int, int]]:
    slots = ((config.get("Template Blocks") or {}).get("Trinoor Hours")) or []
    out = []
    for s in slots:
        a, b = _hhmm_min(s.get("Start")), _hhmm_min(s.get("End"))
        if a is not None and b is not None and b > a:
            out.append((a, b))
    return out


def _fmt_hhmm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _session_slug(label: str, index: int) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", label.casefold()).strip("-")
    return slug or f"slot-{index + 1}"


def mint_session_options(config: dict[str, Any]) -> list[dict[str, str]]:
    """Return stable 30-minute Mint choices inside Trinoor windows."""
    raw = ((config.get("Template Blocks") or {}).get("Trinoor Hours")) or []
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, slot in enumerate(raw):
        if not isinstance(slot, dict):
            continue
        start, end = _hhmm_min(slot.get("Start")), _hhmm_min(slot.get("End"))
        if start is None or end is None or end <= start:
            continue
        label = str(slot.get("Slot") or ("Morning" if start < 12 * 60 else "Afternoon")).strip()
        slug = _session_slug(label, index)
        for session_start in range(start, end, 30):
            session_end = min(session_start + 30, end)
            if session_end - session_start != 30:
                continue
            session_id = f"mint:{slug}:{_fmt_hhmm(session_start)}"
            if session_id in seen:
                session_id = f"{session_id}:{index + 1}"
            seen.add(session_id)
            out.append({
                "id": session_id,
                "name": f"Mint {label} · {_fmt_hhmm(session_start)}",
                "slot": label,
                "start": _fmt_hhmm(session_start),
                "end": _fmt_hhmm(session_end),
            })
    return out


MINT_SESSION_MINUTES = 30


@dataclass(frozen=True)
class MintSchedule:
    """Canonical Mint state for one day and one anchor.

    ``active_blocks`` is the amount both schedulable rows and capacity reserve:
    the default fill from the anchor to the end of the Trinoor windows, reduced
    only by an explicit dated allotment. ``remaining_blocks`` is that fill before
    any explicit cap. ``effective_minutes`` remains the source allotment so
    callers can expose the configured/dated contract.
    """

    effective_minutes: int
    active: bool
    active_blocks: int | float
    remaining_blocks: int
    explicit_on: bool
    selected_session_ids: tuple[str, ...]


def _effective_mint_minutes(
    day_setup: dict[str, Any],
    resolved_day_semantics: dict[str, Any] | None,
    config: dict[str, Any] | None = None,
) -> int:
    """Return the canonical Mint allotment shared by route consumers."""
    import day_semantics

    return day_semantics.effective_allotment_minutes(
        config=config,
        day_setup=day_setup,
        resolved_day_semantics=resolved_day_semantics,
    )


def _canonical_mint_session_ids(
    config: dict[str, Any], raw_sessions: list[Any],
) -> tuple[list[str], list[dict[str, str]]]:
    """Normalize raw IDs/names before any wall filtering.

    Returns ``(selected_ids, options)`` in option order. Unknown and duplicate
    values are discarded.
    """
    options = mint_session_options(config)
    by_key = {
        key.casefold(): option["id"]
        for option in options
        for key in (option["id"], option["name"])
    }
    selected: list[str] = []
    seen: set[str] = set()
    for value in raw_sessions:
        canonical = by_key.get(str(value).strip().casefold())
        if canonical is None or canonical in seen:
            continue
        seen.add(canonical)
        selected.append(canonical)
    option_order = {option["id"]: index for index, option in enumerate(options)}
    selected.sort(key=lambda session_id: (option_order[session_id], session_id))
    return selected, options


def _wall_free_session_ids(
    ids: list[str], options: list[dict[str, str]],
    wall_intervals: list[tuple[int, int]] | None,
) -> list[str]:
    if not wall_intervals:
        return ids
    by_id = {option["id"]: option for option in options}
    out: list[str] = []
    for session_id in ids:
        option = by_id.get(session_id)
        if option is None:
            continue
        start, end = _hhmm_min(option["start"]), _hhmm_min(option["end"])
        if start is None or end is None:
            continue
        if not any(start < wall_end and wall_start < end
                   for wall_start, wall_end in wall_intervals):
            out.append(session_id)
    return out


def normalize_mint_session_override(
    config: dict[str, Any], raw: dict[str, Any],
    *, allotment_minutes: int | None = None,
    wall_intervals: list[tuple[int, int]] | None = None,
) -> dict[str, Any]:
    """Return one canonical, persistence-safe Mint override.

    Raw IDs and display names are canonicalized, then wall-blocked windows are
    dropped. A saved session list is an exact choice, so the allotment never
    clips it, including a list that selects every session. Invalid or stale IDs
    never survive. Explicit zero always wins and produces an empty disabled
    selection.
    """
    if not isinstance(raw.get("sessions"), list):
        return dict(raw)

    import day_semantics

    effective = (
        allotment_minutes
        if allotment_minutes is not None
        else day_semantics.effective_allotment_minutes(config=config)
    )
    selected, options = _canonical_mint_session_ids(config, raw["sessions"])
    if effective <= 0:
        selected = []
    selected = _wall_free_session_ids(selected, options, wall_intervals)
    enabled = bool(raw.get("on", bool(selected))) and bool(selected) and effective > 0
    if not enabled:
        selected = []
    return {
        **raw,
        "on": enabled,
        "n": len(selected),
        "sessions": selected,
    }


def normalize_mint_day_setup(
    config: dict[str, Any],
    day_setup: dict[str, Any],
    today: date,
    resolved_day_semantics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Normalize a setup snapshot before it reaches any route contract.

    Session selections are authoritative for their own total. The normalized
    setup therefore updates ``on``, ``n``, ``sessions``, and the dated total
    together, including legacy all-session lists and stale IDs. No wall filter
    is applied here; selected wall conflicts remain a hard route rejection.
    """
    setup = dict(day_setup or {})
    sched = dict(setup.get("schedulable") or {})
    minting = sched.get("minting")
    if not isinstance(minting, dict) or not isinstance(minting.get("sessions"), list):
        return setup
    effective = _effective_mint_minutes(setup, resolved_day_semantics, config)
    normalized = normalize_mint_session_override(
        config, minting, allotment_minutes=effective,
    )
    sched["minting"] = normalized
    setup["schedulable"] = sched
    # A concrete selection is the daily source-of-truth. Keep an explicit zero
    # as zero and turn a disabled/empty selection into the same zero contract.
    setup["work_allotment_minutes"] = (
        len(normalized.get("sessions") or []) * MINT_SESSION_MINUTES
        if normalized.get("on") else 0
    )
    return setup


def _fill_session_ids(config: dict[str, Any], anchor_min: int) -> list[str]:
    """Aligned 30-minute Mint sessions that still start at or after the anchor.

    Counting whole sessions, rather than flooring remaining minutes, keeps a
    window that starts off the half-hour grid from adding a phantom session.
    """
    return [
        option["id"]
        for option in mint_session_options(config)
        if (_hhmm_min(option["start"]) or 0) >= anchor_min
    ]


def _allotment_is_explicit(
    day_setup: dict[str, Any], resolved_day_semantics: dict[str, Any] | None,
) -> bool:
    """Only a dated or saved numeric allotment caps the default fill.

    Configured Defaults, presets, and the implicit default gate Mint on or off
    but never clip the fill from the anchor to the end of the windows.
    """
    raw = day_setup.get("work_allotment_minutes")
    if isinstance(raw, int) and not isinstance(raw, bool):
        return True
    return (
        isinstance(resolved_day_semantics, dict)
        and resolved_day_semantics.get("allotment_source") == "dated_override"
    )


def mint_schedule(
    config: dict[str, Any],
    day_setup: dict[str, Any],
    today: date,
    anchor: str,
    resolved_day_semantics: dict[str, Any] | None = None,
    *,
    wall_intervals: list[tuple[int, int]] | None = None,
) -> MintSchedule:
    """Compute the canonical Mint amount for a day and anchor.

    With Trinoor windows configured, the default is the fill: every aligned
    30-minute session that still starts at or after the anchor. Default Mint
    applies on workdays only. An explicit dated allotment caps that fill, while
    the configured Defaults, presets, and implicit default only gate Mint on or
    off. An explicit ``on: true`` re-includes Mint on weekends and after the
    windows close, placing whatever fill remains. Selected sessions are exact
    user choices; an explicit zero disables them before any wall logic. Without
    windows, the aggregate allotment remains the source of truth for legacy
    callers.
    """
    sched = dict(day_setup.get("schedulable") or {})
    user = sched.get("minting") or {}
    effective = _effective_mint_minutes(day_setup, resolved_day_semantics, config)
    anchor_min = _hhmm_min(anchor) or 0
    workday = today.weekday() < 5
    fill = len(_fill_session_ids(config, anchor_min))
    explicit_on = user.get("on") is True
    if isinstance(user.get("sessions"), list):
        normalized = normalize_mint_session_override(
            config, user, allotment_minutes=effective,
            wall_intervals=wall_intervals,
        )
        selected = list(normalized.get("sessions") or [])
        active = normalized.get("on") is True and bool(selected)
        return MintSchedule(
            effective_minutes=effective,
            active=active,
            active_blocks=len(selected) if active else 0,
            remaining_blocks=fill,
            explicit_on=explicit_on,
            selected_session_ids=tuple(selected),
        )

    if effective <= 0:
        return MintSchedule(effective, False, 0, fill, explicit_on, ())

    if not _trinoor_slots(config):
        # No windows to fill: default is off, and only an explicit re-include
        # reserves the requested blocks, as the aggregate legacy path always did.
        if not explicit_on:
            return MintSchedule(effective, False, 0, fill, explicit_on, ())
        return MintSchedule(
            effective_minutes=effective,
            active=True,
            active_blocks=effective / MINT_SESSION_MINUTES,
            remaining_blocks=fill,
            explicit_on=explicit_on,
            selected_session_ids=(),
        )

    default_on = workday and fill > 0
    active = explicit_on or (user.get("on") is not False and default_on)
    # Nothing left to fill (window closed or no eligible session): nothing to
    # place, so an explicit re-include has no session to reserve either.
    if not active or fill == 0:
        return MintSchedule(effective, False, 0, fill, explicit_on, ())
    blocks: int | float = fill
    if _allotment_is_explicit(day_setup, resolved_day_semantics):
        blocks = min(effective / MINT_SESSION_MINUTES, fill)
    return MintSchedule(
        effective_minutes=effective,
        active=True,
        active_blocks=blocks,
        remaining_blocks=fill,
        explicit_on=explicit_on,
        selected_session_ids=(),
    )


def build_schedulable_blocks(
    config: dict[str, Any],
    day_setup: dict[str, Any],
    today: date,
    anchor: str,
    resolved_day_semantics: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    """Build schedulable rows from the canonical Mint schedule.

    Mint rows and capacity consume ``MintSchedule.active_blocks``. Default-off
    weekends/no-window days remain off unless the user explicitly re-includes
    Mint with ``on: true``.
    """
    sched = dict(day_setup.get("schedulable") or {})
    schedule = mint_schedule(
        config, day_setup, today, anchor, resolved_day_semantics,
    )
    items: list[dict[str, Any]] = []
    notes: list[str] = []
    for key, default in _SCHED_DEFAULTS.items():
        user = sched.get(key) or {}
        if key == "minting":
            if not schedule.active:
                continue
            if isinstance(user.get("sessions"), list):
                options_by_id = {
                    option["id"]: option for option in mint_session_options(config)
                }
                for session_id in schedule.selected_session_ids:
                    option = options_by_id.get(session_id)
                    if option is None:
                        continue
                    items.append({
                        "id": option["name"],
                        "name": option["name"],
                        "blocks": 1,
                        "duration": 30,
                        "source": "schedulable",
                        "zone": "work_hours",
                        "mint_session": True,
                        "mint_session_id": option["id"],
                        "placement_window": {
                            "start": option["start"],
                            "end": option["end"],
                        },
                        "calendar_class": "mint",
                    })
                continue
            n = schedule.active_blocks
            item = {
                "id": default["name"], "name": default["name"],
                "blocks": n, "duration": n * MINT_SESSION_MINUTES,
                "source": "schedulable", "zone": "work_hours",
            }
            items.append(item)
            continue

        on = user["on"] if "on" in user else default["on"]
        if not on:
            continue
        n = int(user.get("n") or default["n"])
        item = {
            "id": default["name"], "name": default["name"],
            "blocks": n, "duration": n * 30, "source": "schedulable",
        }
        if key == "qt":
            item["qt"] = True
        items.append(item)

    zone_rows: list[dict[str, Any]] = []
    if today.weekday() < 5:
        raw = ((config.get("Template Blocks") or {}).get("Trinoor Hours")) or []
        for slot in raw:
            a, b = _hhmm_min(slot.get("Start")), _hhmm_min(slot.get("End"))
            if a is None or b is None:
                continue
            zone_rows.append({
                "id": f"🟡 Trinoor : {slot.get('Slot', '?')}",
                "start": f"{a // 60:02d}:{a % 60:02d}",
                "end": f"{b // 60:02d}:{b % 60:02d}",
                "zone": "work_hours", "backdrop": True,
            })
    return items, zone_rows, notes


def _mint_session_interval(item: dict[str, Any]) -> tuple[int, int] | None:
    """Selected Mint session window as [start, end) minutes, or None."""
    window = item.get("placement_window")
    if not isinstance(window, dict):
        return None
    start = _hhmm_min(window.get("start"))
    end = _hhmm_min(window.get("end"))
    if start is None or end is None or end <= start:
        return None
    return start, end


def _wall_interval(block: dict[str, Any]) -> tuple[int, int] | None:
    """[start, end) minutes for a non-window anchored wall, or None.

    Mirrors sequence.validate_sequence's wall reading: ``Type: window`` and
    ``overlap_allowed`` blocks are permeable (never walls), and imported
    calendar rows in the ignored/quarantined classes are excluded from
    planning (frozen contract 17). Duration-only blocks ("—" End) derive
    their end from Duration minutes."""
    if block.get("overlap_allowed"):
        return None
    if str(block.get("Type", "")).strip().lower() == "window":
        return None
    if str(block.get("source", "")).casefold() == "calendar":
        klass = str(block.get("capacity_class") or "fixed").strip().casefold()
        if klass in ("ignored", "quarantined"):
            return None
    start = _hhmm_min(block.get("Start"))
    if start is None:
        return None
    end = _hhmm_min(block.get("End"))
    if end is not None:
        return (start, end) if end > start else None
    duration = block.get("Duration")
    minutes = 0
    if isinstance(duration, (int, float)) and duration > 0:
        minutes = int(duration)
    elif isinstance(duration, str):
        m = re.match(r"^(\d+)\s*m$", duration.strip())
        if m:
            minutes = int(m.group(1))
    if minutes <= 0:
        return None
    return start, start + minutes


def stale_mint_conflicts(
    mint_items: list[dict[str, Any]], anchored_blocks: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Selected Mint sessions overlapping effective fixed or work walls.

    FEEDBACK-27: saved frontend Mint selection can be stale — a session whose
    window collides with a current fixed/work calendar wall or a hard
    anchored block must be surfaced BEFORE judgment or the immutable merge
    (live 2026-08-17: Mint 15:00-15:30 vs OPPD 15:00-15:30 became an
    immutable-over-immutable overlap and validation rejected a billed
    proposal). Returns deterministic conflict rows naming both sides; an
    empty list means the selection is wall-clean.
    """
    walls: list[tuple[str, tuple[int, int]]] = []
    for block in anchored_blocks or []:
        interval = _wall_interval(block)
        if interval is not None:
            walls.append((str(block.get("Block") or block.get("name") or "?"), interval))

    def _fmt(interval: tuple[int, int]) -> dict[str, str]:
        return {"start": _fmt_hhmm(interval[0]), "end": _fmt_hhmm(interval[1])}

    conflicts: list[dict[str, Any]] = []
    for item in mint_items or []:
        if item.get("mint_session") is not True:
            continue
        mint_interval = _mint_session_interval(item)
        if mint_interval is None:
            continue
        mint_id = str(item.get("id") or item.get("name") or "")
        for wall_id, (w_start, w_end) in walls:
            if mint_interval[0] < w_end and w_start < mint_interval[1]:
                conflicts.append({
                    "mint_id": mint_id,
                    "mint_interval": _fmt(mint_interval),
                    "wall_id": wall_id,
                    "wall_interval": _fmt((w_start, w_end)),
                })
    return conflicts


def absorb_quick_tasks(
    assigned: list[dict[str, Any]], qt_on: bool
) -> tuple[list[dict[str, Any]], list[str]]:
    """QT absorption (skill 777 / Pass 5): when QT is on, @🚀10min items fold
    into the single QT block as qt_contents (sorted) and leave the individual
    placement queue; when off, they place individually."""
    if not qt_on:
        return list(assigned), []
    remaining, contents = [], []
    for item in assigned:
        if any(is_quick_task_tag(label) for label in (item.get("labels") or [])):
            contents.append(str(item.get("name") or item.get("id") or ""))
        else:
            remaining.append(item)
    return remaining, sorted(contents)
