"""Read-side aggregation for non-vault sources (gather-parity plan, 2026-07-14).

Composes the app's existing read primitives — ``TodoistClient.get_filter_tasks``
and ``calendar_bridge.EventStore.query_events`` — into digest-ready shapes.
The frozen vault gather module is untouched; ``main.py`` merges these outputs.

Degrade contract (locked decision 3): every fetch returns its data plus a
``warnings`` list and NEVER raises — a missing token / EventKit grant yields
empty data + one human-readable warning the UI must surface loudly.

Habits are a CAPACITY summary, not digest items — the skill SOT states habits
are never placed on the timeline and never become tasks/events; only the
outstanding-minutes total deducts from capacity (SKILL.md § Habits).
"""
from __future__ import annotations

import math
import re
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

# v1 /tasks/filter takes filter QUERY strings, not saved-filter IDs (the app's
# existing live callers pass "today" — shadow.py). Saved-filter IDs from the
# config Schema Reference stay write-path-only.
ASSIGNED_QUERY_FALLBACK = "today | overdue"
QUICK_QUERY_FALLBACK = "@🚀10min"

_REMINDER_LABEL = "🔔Reminder"
_NUDGE_PREFIX = "🔔 Nudge:"

_ENTRY_DATE_RE = re.compile(r"^\s*-\s*[\"']?(\d{4}-\d{2}-\d{2})")


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

def _is_reminder(task: dict[str, Any]) -> bool:
    if _REMINDER_LABEL in (task.get("labels") or []):
        return True
    return str(task.get("content", "")).startswith(_NUDGE_PREFIX)


def _to_item(task: dict[str, Any], assigned: bool) -> dict[str, Any]:
    tid = str(task.get("id"))
    # Todoist API priority: 4 = highest — same direction as vault urgency.
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
    client: Any, config: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    """Pull assigned (due today/overdue) + quick-pool tasks.

    Returns ``(assigned_items, pool_items, warnings)``; never raises.
    """
    if client is None:
        return [], [], ["Todoist unavailable (no client/token) — digest is missing Todoist items"]

    assigned_q = str(config.get("todoist.read_query.assigned") or ASSIGNED_QUERY_FALLBACK)
    quick_q = str(config.get("todoist.read_query.quick") or QUICK_QUERY_FALLBACK)
    try:
        assigned_tasks = [t for t in client.get_filter_tasks(assigned_q) if not _is_reminder(t)]
        quick_tasks = [t for t in client.get_filter_tasks(quick_q) if not _is_reminder(t)]
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


def disambiguate_names(
    vault_items: list[dict[str, Any]], ext_items: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Rename external items whose name collides with a vault item (or an
    earlier external item). Sequence identity is name-keyed downstream
    (timeline sets id = name), so duplicate names break never-bump and
    duplicate-row validation. Vault names stay canonical; collisions get
    " (Todoist)" then " (2)", " (3)"… Mutates copies, not inputs.
    """
    vault_taken = {str(i.get("name", "")).casefold() for i in vault_items}
    taken = set(vault_taken)
    out: list[dict[str, Any]] = []
    for item in ext_items:
        copy = dict(item)
        name = str(copy.get("name", ""))
        if name.casefold() in taken:
            # vault collision reads best as a source tag; a same-source
            # duplicate just numbers up.
            candidates = [f"{name} (Todoist)"] if name.casefold() in vault_taken else []
            candidates += [f"{name} ({n})" for n in range(2, len(ext_items) + 2)]
            copy["name"] = next(c for c in candidates if c.casefold() not in taken)
        taken.add(str(copy["name"]).casefold())
        out.append(copy)
    return out


# ---------------------------------------------------------------------------
# Calendar
# ---------------------------------------------------------------------------

def fetch_calendar_busy(
    store: Any, config: dict[str, Any], today: date
) -> tuple[list[dict[str, Any]], list[str]]:
    """Today's events as anchored-block-shaped busy blocks.

    T12j preserves source calendar identity and resolves each event to one of
    four durable capacity states: ``fixed`` (default for unidentified
    calendars), ``work``, ``ignored``, or ``quarantined`` (a KNOWN title the
    user has not classified yet — excluded from capacity/planning until
    reviewed, frozen contract 17). TDTB's own zone-write IDs are always
    ``ignored`` even if a conflicting title rule exists. Ignored rows remain
    on the wire so the UI can explain why they cost zero instead of silently
    hiding them.

    Frozen contract 16: events sharing a canonical identity (``event_id`` /
    ``id``) canonicalize into ONE logical group — attendance and capacity
    each count once. First occurrence wins; identity-less events emit
    one row each (deterministic, stable under refresh).

    Frozen contract 18: all-day source events stay all-day and non-timed on
    the wire. They carry ``all_day: True`` and NO ``Start``/``End`` — every
    ordinary planning/commit path that converts timed rows sees nothing to
    convert, so no implicit timed inference can occur.
    """
    import calendar_bridge

    if store is None:
        return [], ["Calendar unavailable (no EventKit store/grant) — busy blocks missing"]

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
        ]

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
            return [], [f"Calendar read failed ({exc}) — busy blocks missing"]
        if not cals:
            return [], [
                "Calendar store has 0 visible calendars — grant likely "
                "missing for this process; busy blocks missing"
            ]

    own_ids = set((config.get("calendar_ids") or {}).values())
    title_classes = calendar_bridge.normalize_capacity_class_map(
        config.get("calendar_capacity_classes")
        or config.get("Calendar Capacity Classes")
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

    title_by_id = {
        str(identifier): str(title)
        for cal in cals
        if (identifier := _cal_value(cal, "identifier"))
        and (title := _cal_value(cal, "title"))
    }
    start = datetime.combine(today, time.min)
    end = start + timedelta(days=1)
    try:
        events = store.query_events(start, end, None)
    except Exception as exc:  # noqa: BLE001 — degrade contract
        return [], [f"Calendar read failed ({exc}) — busy blocks missing"]

    blocks: list[dict[str, Any]] = []
    seen_identities: set[str] = set()
    for ev in events:
        # Contract 16: canonicalize duplicate source events into one logical
        # group by stable identity; first occurrence wins.
        identity = ev.get("event_id") or ev.get("id")
        if identity is not None:
            identity = str(identity)
            if identity in seen_identities:
                continue
            seen_identities.add(identity)
        calendar_id = str(ev.get("calendar_id") or "")
        calendar_title = title_by_id.get(calendar_id)
        # Contract 17: a KNOWN title the user has not classified stays
        # quarantined — never silently counted as fixed. An unidentified
        # calendar (no inventory) keeps the historical fixed default.
        if calendar_title is not None:
            capacity_class = title_classes.get(
                calendar_title, calendar_bridge.CAPACITY_CLASS_QUARANTINED
            )
        else:
            capacity_class = title_classes.get("", "fixed")
        if calendar_id in own_ids:
            capacity_class = "ignored"
        # Contract 18: all-day events remain all-day and non-timed — emitted
        # without Start/End so no timed planning path can convert them.
        if ev.get("all_day"):
            blocks.append({
                "Block": ev.get("title") or "(untitled event)",
                "source": "calendar",
                "calendar_id": calendar_id or None,
                "calendar_title": calendar_title,
                "capacity_class": capacity_class,
                "all_day": True,
            })
            continue
        ev_start, ev_end = ev.get("start"), ev.get("end")
        if not isinstance(ev_start, datetime) or not isinstance(ev_end, datetime):
            continue
        # Zero/negative-duration events are reminder-style markers, not busy
        # time — echoing one as an anchored block is unsatisfiable downstream
        # (judgment rejects end <= start; T11 live: "2.0M" 23:00-23:00).
        if ev_end <= ev_start:
            continue
        blocks.append({
            "Block": ev.get("title") or "(untitled event)",
            "Start": ev_start.strftime("%H:%M"),
            "End": ev_end.strftime("%H:%M"),
            "source": "calendar",
            "calendar_id": calendar_id or None,
            "calendar_title": calendar_title,
            "capacity_class": capacity_class,
        })
    return blocks, []


# ---------------------------------------------------------------------------
# Habits (capacity summary)
# ---------------------------------------------------------------------------

def _habit_fields(text: str) -> tuple[list[str], int | None]:
    """Extract (entry dates, duration) from a habit note's frontmatter.

    Line-based scan — habit frontmatter is machine-written (habit-tracker
    plugin); full YAML parsing is unnecessary and adds a dependency.
    """
    entries: list[str] = []
    duration: int | None = None
    in_fm = False
    in_entries = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped == "---":
            if in_fm:
                break
            in_fm = True
            continue
        if not in_fm:
            continue
        if in_entries:
            m = _ENTRY_DATE_RE.match(line)
            if m:
                entries.append(m.group(1))
                continue
            if not line.startswith((" ", "\t")):
                in_entries = False
        if stripped.startswith("entries:"):
            in_entries = True
        elif stripped.startswith("duration:"):
            raw = stripped.split(":", 1)[1].strip()
            if raw.isdigit():
                duration = int(raw)
    return entries, duration


def fetch_habit_status(
    vault_root: str | Path, config: dict[str, Any], today: date
) -> tuple[dict[str, Any], list[str]]:
    """Done/outstanding split + outstanding-minutes estimate (skill § Habits).

    ``duration: 0`` counts as unset (live vault notes carry it); estimate =
    sum of outstanding durations (fallback per-habit minutes where unset),
    rounded UP to the ``round_to_minutes`` grain.
    """
    rel = str(config.get("habits.source_directory") or "00 - META/Habituals/")
    fallback_min = int(config.get("habits.fallback_minutes_per_habit") or 4)
    grain = int(config.get("habits.round_to_minutes") or 15)
    habits_dir = Path(vault_root) / rel
    empty = {"total": 0, "done": 0, "outstanding": 0, "est_minutes": 0}
    if not habits_dir.is_dir():
        return dict(empty), [f"Habits directory missing ({rel}) — habit capacity unknown"]

    today_str = str(today)
    total = done = 0
    outstanding_minutes = 0
    for note in sorted(habits_dir.glob("*.md")):
        try:
            entries, duration = _habit_fields(note.read_text(encoding="utf-8"))
        except OSError:
            continue
        total += 1
        if today_str in entries:
            done += 1
        else:
            outstanding_minutes += duration if duration else fallback_min
    outstanding = total - done
    est = math.ceil(outstanding_minutes / grain) * grain if outstanding_minutes else 0
    return (
        {"total": total, "done": done, "outstanding": outstanding, "est_minutes": est},
        [],
    )


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


def _effective_mint_minutes(
    day_setup: dict[str, Any], resolved_day_semantics: dict[str, Any] | None
) -> int | None:
    """Effective Mint allotment in minutes, or None when no allotment context
    exists. The resolved day-semantics contract is the authoritative source
    (preset + dated override resolution); the dated runstate override is the
    server-side fallback. None preserves the legacy no-allotment default."""
    if resolved_day_semantics:
        value = resolved_day_semantics.get("effective_allotment_minutes")
        if value is not None:
            return int(value)
    value = day_setup.get("work_allotment_minutes")
    if value is not None:
        return int(value)
    return None


def normalize_mint_session_override(
    config: dict[str, Any], raw: dict[str, Any]
) -> dict[str, Any]:
    """Canonicalize selected Mint rows and derive their daily total.

    A session list is the authoritative placement choice.  The corresponding
    allotment is therefore always ``30 minutes * selected sessions``; keeping
    both fields independently editable is what allowed the old UI to save
    contradictory state.
    """
    if not isinstance(raw.get("sessions"), list):
        return dict(raw)

    options = mint_session_options(config)
    by_key = {
        key.casefold(): option["id"]
        for option in options
        for key in (option["id"], option["name"])
    }
    selected: list[str] = []
    seen: set[str] = set()
    for value in raw["sessions"]:
        key = str(value).strip().casefold()
        canonical = by_key.get(key)
        if canonical is None and options:
            # With a live source config, an unknown row cannot be placed and
            # must not inflate the saved Mint total.
            continue
        if canonical is None:
            # Keep a stable legacy value when the source configuration is
            # unavailable; a later read can still show the saved intent.
            canonical = str(value).strip()
        if canonical and canonical not in seen:
            seen.add(canonical)
            selected.append(canonical)

    enabled = bool(raw.get("on", bool(selected))) and bool(selected)
    if not enabled:
        selected = []
    return {
        **raw,
        "on": enabled,
        "n": len(selected),
        "sessions": selected,
    }


def build_schedulable_blocks(
    config: dict[str, Any],
    day_setup: dict[str, Any],
    today: date,
    anchor: str,
    resolved_day_semantics: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    """Schedulable items + Trinoor zone backdrop rows + capacity notes.

    Skill 792–800 / 1241–1252: Minting defaults On weekdays (Off on weekends
    or when the work window has no blocks left after anchor — the toggle stays
    live, so an explicit ``on: true`` re-includes and places anyway). When a
    Day Setup ``sessions`` list exists, Mint emits one 30-minute row per
    selected session; otherwise the legacy aggregate row remains available.
    QT defaults On (1 block), Shivery defaults Off. The 🟡 Trinoor zone rows
    are a permeable visual backdrop (Step D′) — NEVER subtractive, workday only.

    FEEDBACK-25: the effective Mint allotment (``resolved_day_semantics`` /
    dated ``work_allotment_minutes``) is the authoritative Mint source for the
    legacy aggregate row — capacity reserves allotment/30 blocks, so the row
    must agree or the plan promises more Mint than it places. The hardcoded
    2-block ``_SCHED_DEFAULTS`` default survives only when NO allotment
    context exists (legacy callers without day-preset state).
    """
    sched = dict(day_setup.get("schedulable") or {})
    anchor_min = _hhmm_min(anchor) or 0
    slots = _trinoor_slots(config)
    workday = today.weekday() < 5
    remaining = sum(max(0, e - max(s, anchor_min)) for s, e in slots) // 30 if workday else 0
    mint_minutes = _effective_mint_minutes(day_setup, resolved_day_semantics)

    items: list[dict[str, Any]] = []
    notes: list[str] = []
    for key, d in _SCHED_DEFAULTS.items():
        user = sched.get(key) or {}
        if (
            key == "minting"
            and day_setup.get("work_allotment_minutes") == 0
            and not isinstance(user.get("sessions"), list)
        ):
            continue
        default_on = d["on"] if key != "minting" else (d["on"] and remaining > 0)

        # A saved ``sessions`` list is the newer Day Setup contract. It keeps
        # Mint as separate rows tied to the exact Trinoor windows the user
        # selected. With no list, retain the legacy aggregate Minting row.
        if key == "minting" and isinstance(user.get("sessions"), list):
            selected = {str(value).casefold() for value in user["sessions"]}
            on = user["on"] if "on" in user else bool(selected)
            if on:
                options = mint_session_options(config)
                for option in options:
                    if (
                        option["id"].casefold() not in selected
                        and option["name"].casefold() not in selected
                    ):
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

        on = user["on"] if "on" in user else default_on
        if not on:
            continue
        if key == "minting":
            # FEEDBACK-25: the effective allotment derives the aggregate row's
            # size (allotment/30 blocks — the same number capacity reserves).
            # Only with no allotment context does the legacy default remain.
            if mint_minutes is not None and mint_minutes > 0:
                n = mint_minutes / 30
            else:
                n = int(user.get("n") or d["n"])
        else:
            n = int(user.get("n") or d["n"])
        # Cap to the window remainder (skill 1249) — but a re-include
        # (explicit on with remaining == 0) places anyway per never-bump.
        if key == "minting" and 0 < remaining < n:
            end = max((e for _, e in slots), default=0)
            notes.append(
                f"Minting: {remaining} block{'s' if remaining != 1 else ''} "
                f"(work window closes at {end // 60:02d}:{end % 60:02d})")
            n = remaining
        item = {"id": d["name"], "name": d["name"], "blocks": n,
                "duration": n * 30, "source": "schedulable"}
        if key == "minting":
            item["zone"] = "work_hours"
        if key == "qt":
            item["qt"] = True
        items.append(item)

    zone_rows: list[dict[str, Any]] = []
    if workday:
        raw = ((config.get("Template Blocks") or {}).get("Trinoor Hours")) or []
        for s in raw:
            a, b = _hhmm_min(s.get("Start")), _hhmm_min(s.get("End"))
            if a is None or b is None:
                continue
            zone_rows.append({
                "id": f"🟡 Trinoor : {s.get('Slot', '?')}",
                "start": f"{a // 60:02d}:{a % 60:02d}",
                "end": f"{b // 60:02d}:{b % 60:02d}",
                "zone": "work_hours", "backdrop": True,
            })
    return items, zone_rows, notes


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
        if QUICK_LABEL in (item.get("labels") or []):
            contents.append(str(item.get("name") or item.get("id") or ""))
        else:
            remaining.append(item)
    return remaining, sorted(contents)
