"""shadow.py — T13: shadow-mode commit.

Computes the full Phase-5 write contract (``plan_manifest``, per SKILL.md
§ 0.8) from a confirmed digest + sequence, diffs it against the live state of
every write target (Todoist, calendar, vault), and renders a preview —
WITHOUT writing anything. This is the first true end-to-end pass against real
vault data before any live writer exists (T14/T15), and stays a permanent
feature afterward (bake-in comparison uses it).

Three layers, kept strictly separate so the diff logic is unit-testable
without touching disk or the network:

1. ``build_plan_manifest`` — pure. digest + sequence + config -> manifest rows.
2. ``diff_against_live`` — pure. manifest + live_state -> ShadowDiff.
3. ``gather_live_state`` — the only I/O in this module. Reads Todoist,
   calendar, and vault state; each surface degrades independently (a marker
   in the returned dict), never crashing the other surfaces.

Step semantics (SKILL.md Phase 5, the skill contract):
  A  — timed Todoist tasks (retime existing / create new). The manifest
       itself does not resolve retime-vs-create (that needs live Todoist
       state, which this module doesn't have until ``gather_live_state``
       runs) — every Step A row is emitted generically; ``diff_against_live``
       resolves the split via its would-update (retime) / would-create
       (create) classification.
  B  — daily-note "# TDTB Plan" section patch.
  C  — ``assigned: true`` frontmatter flips (Assigned digest items).
  D  — schedulable block calendar events (Minting / Shivery Jigs — sequence
       rows matched to neither an Assigned digest item, an anchored block,
       nor a Trinoor work zone).
  D′ — Trinoor work-zone calendar events (0–2 rows whose id names a
       Trinoor work zone; workday only — the sequence proposal only carries
       them on workdays, so no weekday check is re-derived here).
  E  — Anchored Lifestyle Block calendar events (SKILL.md § Step E,
       ~line 1688): create_event intents for sequence rows matching a
       configured anchored block, minus blocks toggled off / skipped today,
       and minus Live when ``config["micro_adventure"]`` is set — that one
       routes through Todoist as a Step A create instead.

Recent-selections is NOT a manifest step — it's a separate post-commit
action (``runstate.append_recent_selection``, built in T9) outside the
write contract this module previews.
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

_GATHER_DIR = str(Path(__file__).parent / "gather")
if _GATHER_DIR not in sys.path:
    sys.path.insert(0, _GATHER_DIR)

import tdtb_gather as gather  # noqa: E402  (path-shimmed import, see inventory.py)

import todoist_client  # noqa: E402
import calendar_bridge  # noqa: E402
import time_engine  # noqa: E402

TOKEN_ENV_PATH = Path.home() / ".config" / "tdtb" / "env"

_PHEP_TYPES = {"project", "interval", "pursuit", "adventure"}
_CALENDAR_AUTH_OK = {"fullAccess", "writeOnly"}


class ShadowStateError(Exception):
    """A required live-state surface couldn't even be attempted — e.g. the
    Todoist token file is missing entirely. Distinct from a surface that's
    reachable but fails (auth/consent/network), which degrades to a partial
    state marker instead of raising (§ gather_live_state)."""


# ---------------------------------------------------------------------------
# Data shapes
# ---------------------------------------------------------------------------

@dataclass
class ManifestEntry:
    step: str            # "A" | "B" | "C" | "D" | "D′" | "E"
    system: str           # "todoist" | "vault" | "calendar"
    action: str            # "schedule" | "patch" | "set-flag" | "create-event"
    name: str
    id_or_path: str
    time: str | None = None
    duration_min: int = 0
    routing: str = "—"

    def as_dict(self) -> dict[str, Any]:
        return {
            "step": self.step,
            "system": self.system,
            "action": self.action,
            "name": self.name,
            "id_or_path": self.id_or_path,
            "time": self.time,
            "duration_min": self.duration_min,
            "routing": self.routing,
        }


# Classification vocabulary shared by diff_against_live.
CREATE = "would-create"
UPDATE = "would-update"
NOOP = "no-op"
CONFLICT = "conflict"
UNAVAILABLE = "unavailable"


@dataclass
class ShadowDiffEntry:
    manifest: ManifestEntry
    classification: str
    detail: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "manifest": self.manifest.as_dict(),
            "classification": self.classification,
            "detail": self.detail,
        }


@dataclass
class ShadowDiff:
    entries: list[ShadowDiffEntry]
    unavailable_surfaces: list[str] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        out = {CREATE: 0, UPDATE: 0, NOOP: 0, CONFLICT: 0, UNAVAILABLE: 0}
        for e in self.entries:
            out[e.classification] = out.get(e.classification, 0) + 1
        return out

    def as_dict(self) -> dict[str, Any]:
        return {
            "entries": [e.as_dict() for e in self.entries],
            "unavailable_surfaces": self.unavailable_surfaces,
            "counts": self.counts(),
        }


# ---------------------------------------------------------------------------
# 1. build_plan_manifest — pure
# ---------------------------------------------------------------------------

def _minutes_between(start: str | None, end: str | None) -> int:
    if not start or not end:
        return 0
    try:
        sh, sm = (int(p) for p in start.split(":"))
        eh, em = (int(p) for p in end.split(":"))
    except (ValueError, AttributeError):
        return 0
    return (eh * 60 + em) - (sh * 60 + sm)


def _preset_type(name: str, config: dict[str, Any]) -> str | None:
    # Section key dual-handled (ISS-1): real vault config keys this section by
    # its title-case heading "Presets" (config_reader.parse_config_markdown),
    # the app fixtures use lowercase "presets". Title-case wins; lowercase is the
    # fixture fallback — mirrors the row-level Name/name dual-handling below.
    for preset in config.get("Presets") or config.get("presets") or []:
        pname = preset.get("name") or preset.get("Name")
        if pname == name:
            ptype = preset.get("type") or preset.get("Type")
            return str(ptype).strip().lower() if ptype else None
    return None


def _anchored_specs(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Anchored-block specs keyed by block id/name. Accepts both the app
    fixture shape (``id``) and the skill's config-table shape (``Block``)."""
    specs: dict[str, dict[str, Any]] = {}
    # Section key dual-handled (ISS-1): real config keys this "Anchored Lifestyle
    # Blocks" (title-case heading); app fixtures use "anchored_blocks". Title-case
    # wins, lowercase is the fixture fallback.
    for block in config.get("Anchored Lifestyle Blocks") or config.get("anchored_blocks") or []:
        name = block.get("id") or block.get("Block") or block.get("name")
        if name:
            specs[str(name)] = block
    return specs


def _anchored_block_off(spec: dict[str, Any]) -> bool:
    """True when the block is toggled off or skipped today (Phase 1 edits)."""
    if spec.get("on") is False:
        return True
    if spec.get("skip_today") is True:
        return True
    return False


def apply_calendar_participation(
    busy_blocks: list[dict[str, Any]], day_setup: dict[str, Any] | None
) -> list[dict[str, Any]]:
    """T28: merge per-day plan participation onto calendar busy rows.

    Only ``skip_today`` crosses — an imported calendar event's time and
    duration stay immutable (locked decision 19), and nothing here ever
    writes to the source calendar. Name-keyed like ``apply_day_setup``
    (busy rows carry their event title as ``Block``)."""
    if not day_setup:
        return [dict(b) for b in busy_blocks]
    overrides = {str(o.get("id")): o for o in (day_setup.get("anchored") or [])
                 if o.get("id")}
    out: list[dict[str, Any]] = []
    for b in busy_blocks:
        nb = dict(b)
        o = overrides.get(str(b.get("Block") or b.get("name") or ""))
        if o and o.get("skip_today") is True:
            nb["skip_today"] = True
        out.append(nb)
    return out


def apply_day_setup(
    config: dict[str, Any], day_setup: dict[str, Any] | None
) -> dict[str, Any]:
    """Merge a Day Setup blob (runstate, ui-parity T4) into config so the
    manifest builder sees Phase-1 edits: anchored overrides ({id, on,
    skip_today, time}) land on the matching spec; ``re_included`` blocks are
    forced on (they beat any skip) and tagged for the Step E title suffix."""
    if not day_setup:
        return config
    cfg = dict(config)
    overrides = {str(o.get("id")): o for o in (day_setup.get("anchored") or [])
                 if o.get("id")}
    re_inc = {str(n) for n in (day_setup.get("re_included") or [])}

    key = ("Anchored Lifestyle Blocks" if "Anchored Lifestyle Blocks" in config
           else "anchored_blocks")
    blocks = []
    for b in (config.get(key) or []):
        name = str(b.get("id") or b.get("Block") or b.get("name") or "")
        nb = dict(b)
        o = overrides.get(name)
        if o:
            for k in ("on", "skip_today", "time"):
                if k in o:
                    nb[k] = o[k]
            # block-count override rewrites Duration (minutes) so capacity
            # (_spec_blocks), shadow, and the sequence payload all inherit it
            if o.get("blocks") is not None:
                try:
                    nb["Duration"] = max(0, int(o["blocks"])) * 30
                except (TypeError, ValueError):
                    pass
        if name in re_inc:
            nb["on"] = True
            nb["skip_today"] = False
            nb["re_included"] = True
        blocks.append(nb)
    cfg[key] = blocks
    cfg["re_included"] = sorted(re_inc)
    captures = {k: day_setup[k]
                for k in ("intention", "megan_nicety", "stoic_intention")
                if day_setup.get(k)}
    if captures:
        cfg["captures"] = captures
    return cfg


def _cfg_time_min(value: Any) -> int | None:
    hhmm = time_engine.to_hhmm(value)
    if hhmm is None:
        return None
    h, m = (int(p) for p in hhmm.split(":"))
    return h * 60 + m


def past_window_defaults(
    config: dict[str, Any], anchor: str, today: date
) -> set[str]:
    """Block names that DEFAULT off/skipped at this anchor (skill 813 + 797):
    hard anchored blocks whose start < anchor; window blocks whose window end
    < anchor; "Minting" on weekends or when anchor ≥ Trinoor work-end. These
    are defaults only — Phase 1 toggles stay operable, and a block in this set
    that the confirmed payload turns on enters ``re_included``."""
    anchor_min = _cfg_time_min(anchor)
    off: set[str] = set()
    if anchor_min is None:
        return off

    for name, spec in _anchored_specs(config).items():
        btype = str(spec.get("Type") or spec.get("type") or "hard").lower()
        start = _cfg_time_min(spec.get("Start") or spec.get("start"))
        end = _cfg_time_min(spec.get("End") or spec.get("end"))
        if btype == "window":
            if end is not None and end < anchor_min:
                off.add(name)
        elif start is not None and start < anchor_min:
            off.add(name)

    if today.weekday() >= 5:
        off.add("Minting")
    else:
        slots = ((config.get("Template Blocks") or {}).get("Trinoor Hours")) or []
        ends = [m for m in (_cfg_time_min(s.get("End")) for s in slots) if m is not None]
        if ends and anchor_min >= max(ends):
            off.add("Minting")
    return off


def _is_live_block(name: str) -> bool:
    return str(name).strip().lower() in ("live", "⬜ live")


# Canonical Trinoor work-zone id shape: "[🟡 ]Trinoor : <slot>" — exactly what
# external_sources.build_schedulable_blocks emits for the zone backdrop rows
# ("🟡 Trinoor : Morning"). FEEDBACK-26: an explicit exact-match policy — the
# old broad substring test (`"trinoor" in id`) classified ANY name containing
# the word (e.g. a configured anchored block "Trinoor sync") as Step D′ and
# silently dropped its Step E write intent.
_TRINOOR_ZONE_RE = re.compile(r"^(?:🟡 )?Trinoor : .+")


def _is_trinoor_zone_id(row_id: Any) -> bool:
    """True only for the canonical Trinoor work-zone id shape (exact-match).

    Never a substring hit: a row is a Step D′ zone backdrop only when its id
    is ``[🟡 ]Trinoor : <slot>``. Source/anchor rows whose names merely
    contain "Trinoor" keep their own classification."""
    return isinstance(row_id, str) and bool(_TRINOOR_ZONE_RE.match(row_id))


def _assigned_routing(name: str, item: dict[str, Any], config: dict[str, Any]) -> str:
    """Resolve an assigned item to Todoist Inbox or the PHEP project."""
    item_types = {str(t).strip().lower() for t in (item.get("types") or [])}
    ptype = _preset_type(name, config)
    return "PHEP" if (item_types & _PHEP_TYPES or ptype in _PHEP_TYPES) else "Inbox"


def _schedulable_routing(name: Any) -> str:
    """Route Mint rows to the Mint calendar class in the write preview."""
    text = str(name or "").strip().casefold()
    text = text.removeprefix("🟡 ").strip()
    return (
        "🟡 Mint"
        if text == "minting" or text.startswith("mint ") or text.startswith("minting ")
        else "⬜ Blocks"
    )


def _hhmm_minutes(value: Any) -> int | None:
    """Minutes-since-midnight for an hh:mm-ish value, or None if unparseable."""
    text = time_engine.to_hhmm(value)
    if not text:
        return None
    try:
        hours, minutes = text.split(":")
        return int(hours) * 60 + int(minutes)
    except (ValueError, AttributeError):
        return None


def _starts_before_frame(spec_start: Any, time_frame: dict[str, Any] | None) -> bool:
    """True when an anchored spec's own start falls before the day frame's
    anchor — i.e. the block already elapsed (T12 qualification, 2026-07-26).

    Day Setup already labels these "Outside the day frame" and capacity already
    excludes them; without this the write contract still published a
    create-event at the elapsed time, back-dating events into the real
    calendar. No frame (``None``) means no filtering, preserving every
    pre-existing caller.
    """
    if not time_frame:
        return False
    anchor = _hhmm_minutes(time_frame.get("anchor"))
    start = _hhmm_minutes(spec_start)
    if anchor is None or start is None:
        return False
    return start < anchor


def build_plan_manifest(
    digest: dict[str, Any],
    sequence: dict[str, Any],
    config: dict[str, Any] | None = None,
    time_frame: dict[str, Any] | None = None,
) -> list[ManifestEntry]:
    """Build the Phase-5 write contract from a confirmed digest + sequence
    proposal (the /sequence response body: ``{"sequence": [{id,start,end,zone}, ...]}``).

    Row partition (SKILL.md Phase 5 semantics):
      - id matches an Assigned digest item's name -> Step A (Todoist).
      - id names a Trinoor work zone -> Step D′ (calendar).
      - id matches a ``config["anchored_blocks"]`` entry -> Step E
        (calendar), skipping blocks toggled off / skipped today, and
        rerouting Live to a Step A Todoist create when
        ``config["micro_adventure"]`` is set (SKILL.md Step E Live rule).
      - anything else -> Step D (schedulable block: Minting / Shivery Jigs).
    """
    config = config or {}
    assigned = {item.get("name"): item for item in (digest.get("assigned") or [])}
    anchored = _anchored_specs(config)
    micro_adventure = config.get("micro_adventure")
    rows = sequence.get("sequence") or []

    entries: list[ManifestEntry] = []
    sequenced_assigned: set[str] = set()
    for row in rows:
        row_id = row.get("id")
        start = row.get("start")
        end = row.get("end")
        duration = _minutes_between(start, end)

        if row_id in assigned:
            item = assigned[row_id]
            sequenced_assigned.add(row_id)
            # Routing reads the digest item's own vault types first (shakedown
            # 2026-07-14, defect: Magic Mirror -> Inbox): _preset_type only
            # knows config Presets rows, but assigned items are vault notes
            # whose types ride in the digest payload. Presets stay as fallback.
            entries.append(ManifestEntry(
                step="A", system="todoist", action="schedule",
                name=row_id, id_or_path=item.get("path") or row_id,
                time=start, duration_min=duration,
                routing=_assigned_routing(row_id, item, config),
            ))
        elif isinstance(row_id, str) and row_id.strip().lower() == "quick tasks":
            # QT routes through Todoist (skill 1518) — never a BusyCal event,
            # so its contents surface via Todoist sync and can be checked off.
            entries.append(ManifestEntry(
                step="A", system="todoist", action="schedule",
                name=row_id, id_or_path=str(row_id),
                time=start, duration_min=duration, routing="Inbox",
            ))
        elif _is_trinoor_zone_id(row_id):
            entries.append(ManifestEntry(
                step="D′", system="calendar", action="create-event",
                name=row_id, id_or_path=str(row_id),
                time=start, duration_min=duration, routing="⬜ Blocks",
            ))
        elif str(row_id) in anchored:
            spec = anchored[str(row_id)]
            if _anchored_block_off(spec):
                continue  # toggled off / skipped today — no write intent
            # T12a (2026-07-26): the SAME frame rule as the not-in-sequence
            # loop below. An auto-sequenced day reaches here, not there —
            # judgment.py's prompt REQUIRES every anchored_block passed to it
            # to appear in the proposal, _judged_anchored does not drop
            # elapsed blocks, and validate_sequence demoted pre-anchor rows to
            # a soft warning, so an elapsed block rides the commit payload as
            # an ordinary row. Filtering only the fallback loop left the
            # shakedown's five back-dated create-events fully reachable.
            # Filters on the PROPOSED start, so a block the sequencer moved
            # forward into the frame still publishes.
            if _starts_before_frame(start, time_frame):
                continue
            if _is_live_block(row_id) and micro_adventure:
                # SKILL.md Step E Live rule: micro-adventure set -> Todoist
                # create in the Step A batch, NOT a BusyCal event.
                idea = (micro_adventure.get("idea") if isinstance(micro_adventure, dict)
                        else str(micro_adventure))
                entries.append(ManifestEntry(
                    step="A", system="todoist", action="schedule",
                    name=f"🌱 {idea}", id_or_path=str(row_id),
                    time=start, duration_min=duration or 60, routing="Inbox",
                ))
                continue
            # Skill 1702: a re-included block's event title carries the tag so
            # the retroactively-placed block is visually distinct.
            title = f"{row_id} (re-included)" if spec.get("re_included") else str(row_id)
            entries.append(ManifestEntry(
                step="E", system="calendar", action="create-event",
                name=title, id_or_path=str(row_id),
                time=start, duration_min=duration, routing="⬜ Blocks",
            ))
        else:
            entries.append(ManifestEntry(
                step="D", system="calendar", action="create-event",
                name=str(row_id), id_or_path=str(row_id),
                time=start, duration_min=duration, routing=_schedulable_routing(row_id),
            ))

    # T22 Step E parity: the cockpit's staged sequence carries movable work +
    # zone rows only — anchored blocks are fixed-lane context and never ride
    # the payload (2026-07-24 qualification: Sudsing landed on no calendar).
    # Every ON, non-calendar anchored spec absent from the sequence publishes
    # one ⬜ Blocks event at its effective start (Day Setup time/blocks
    # overrides already merged by apply_day_setup); Live still reroutes to a
    # Todoist create when a micro-adventure is set.
    covered = {str(row.get("id")) for row in rows}
    for name, spec in anchored.items():
        if name in covered or spec.get("source") == "calendar":
            continue
        if _anchored_block_off(spec):
            continue
        start = time_engine.to_hhmm(spec.get("time")) or time_engine.to_hhmm(spec.get("Start"))
        duration = time_engine.duration_minutes(spec.get("Duration")) or 0
        # T12 qualification: an elapsed block publishes nothing at all — not a
        # calendar event, not a Live->Todoist reroute. Gated ahead of the Live
        # branch so both paths obey the frame.
        if _starts_before_frame(start, time_frame):
            continue
        if _is_live_block(name) and micro_adventure:
            idea = (micro_adventure.get("idea") if isinstance(micro_adventure, dict)
                    else str(micro_adventure))
            entries.append(ManifestEntry(
                step="A", system="todoist", action="schedule",
                name=f"🌱 {idea}", id_or_path=name,
                time=start, duration_min=duration or 60, routing="Inbox",
            ))
            continue
        if not start or duration <= 0:
            continue  # unplaceable or explicit zero — background only, no event
        title = f"{name} (re-included)" if spec.get("re_included") else name
        entries.append(ManifestEntry(
            step="E", system="calendar", action="create-event",
            name=title, id_or_path=name,
            time=start, duration_min=duration, routing="⬜ Blocks",
        ))

    # Zero-block assigned work remains part of today's commitment, but has no
    # timeline row or capacity cost. Give it a date-only Todoist due instead
    # of silently dropping its Step A write intent.
    for name, item in assigned.items():
        if name in sequenced_assigned or item.get("blocks") != 0:
            continue
        entries.append(ManifestEntry(
            step="A", system="todoist", action="schedule-all-day",
            name=name, id_or_path=item.get("path") or name,
            time=None, duration_min=0,
            routing=_assigned_routing(name, item, config),
        ))

    # Step A captures — niceties to Todoist Inbox (skill 1550–1560): bare
    # verbatim text, ALL-DAY by design (no time, no duration, no block).
    # ``intention`` never becomes a task — it lives only in B6 frontmatter.
    captures = config.get("captures") or {}
    for cap_key in ("megan_nicety", "stoic_intention"):
        text = str(captures.get(cap_key) or "").strip()
        if not text:
            continue  # skip silently — empty fields never write
        entries.append(ManifestEntry(
            step="A", system="todoist", action="capture-nicety",
            name=text, id_or_path=f"capture://{cap_key}", routing="Inbox",
        ))

    # B6 — Phase-1 captures into the daily note's frontmatter (skill 1607):
    # one row when any capture is present; the writer merges only MISSING keys.
    if any(str(captures.get(k) or "").strip()
           for k in ("intention", "megan_nicety", "stoic_intention")):
        entries.append(ManifestEntry(
            step="B6", system="vault", action="frontmatter-captures",
            name="Phase-1 captures", id_or_path="<today's daily note>",
        ))

    # Step B — daily-note "# TDTB Plan" section patch. Always exactly one row
    # (a commit always writes/refreshes the plan section).
    entries.append(ManifestEntry(
        step="B", system="vault", action="patch",
        name="# TDTB Plan", id_or_path="<today's daily note>",
    ))

    # Step C — assigned:true frontmatter flips, one per Assigned digest item.
    # Todoist-sourced items (todoist:// paths) have no vault note to flip —
    # their live surface is the task itself (Step A); skip them or every
    # sourced item shadows as a phantom "target missing" conflict.
    for name, item in assigned.items():
        path = item.get("path") or name
        if str(path).startswith("todoist://"):
            continue
        entries.append(ManifestEntry(
            step="C", system="vault", action="set-flag",
            name=name, id_or_path=path,
        ))

    return entries


# ---------------------------------------------------------------------------
# 2. diff_against_live — pure
# ---------------------------------------------------------------------------

def _todoist_due_time(task: dict[str, Any]) -> str | None:
    due = task.get("due") or {}
    dt = due.get("datetime")
    if not dt:
        # Todoist unified-API v1 (/tasks/filter AND /tasks/{id}) carries the timed
        # due value under `due.date` (e.g. "2026-07-13T09:00:00"), with `datetime`
        # absent — read it. A date-only due ("2026-07-13", no "T") has no
        # time-of-day, so it stays None, as before. (ISS-5.)
        date_val = due.get("date")
        if date_val and "T" in str(date_val):
            dt = date_val
        else:
            return None
    try:
        return datetime.fromisoformat(str(dt).replace("Z", "+00:00")).strftime("%H:%M")
    except ValueError:
        return None


def _match_todoist(
    name: str, tasks: list[dict[str, Any]], ref: str | None = None,
    claimed: set[str] | None = None,
) -> dict[str, Any] | None:
    """Prefer the task id when ``ref`` is a ``todoist://<id>`` path (items
    sourced from Todoist reads carry it; their display names may be
    disambiguated — e.g. "Stillness (Todoist)" — so content matching would
    misclassify them as creates). Content matching remains the fallback for
    vault-sourced rows, but never matches a task in ``claimed`` — a task
    another row of the same diff already owns (T21: vault "Press" collapsing
    onto the id-ref'd todoist "Press" double-updated one task live)."""
    if ref and ref.startswith("todoist://"):
        tid = ref[len("todoist://"):]
        for t in tasks:
            if str(t.get("id")) == tid:
                return t
        return None
    target = (name or "").strip().lower()
    for t in tasks:
        if claimed and str(t.get("id")) in claimed:
            continue
        if (t.get("content") or "").strip().lower() == target:
            return t
    return None


def _event_start_hhmm(event: dict[str, Any]) -> str | None:
    start = event.get("start")
    if isinstance(start, datetime):
        return start.strftime("%H:%M")
    if isinstance(start, str) and len(start) >= 5:
        return start[11:16] if "T" in start else start[:5]
    return None


def _match_calendar_event(
    name: str, time: str | None, events: list[dict[str, Any]]
) -> dict[str, Any] | None:
    # Prefer an exact-time hit, but fall back to a same-day title match at ANY
    # time: the calendar bridge cannot move events, so a time-shifted live
    # event must diff as the counterpart (no-op), never as a duplicate create.
    title_hit: dict[str, Any] | None = None
    for e in events:
        if (e.get("title") or "") != name:
            continue
        if time is None or _event_start_hhmm(e) == time:
            return e
        if title_hit is None:
            title_hit = e
    return title_hit


def diff_against_live(manifest: list[ManifestEntry], live_state: dict[str, Any]) -> ShadowDiff:
    """Classify every manifest row against live_state. Pure — no I/O.

    Classes: would-create (no live counterpart), would-update (live
    counterpart differs), no-op (already matches), conflict (live state
    contradicts the manifest's assumptions), unavailable (the relevant
    surface degraded in ``gather_live_state`` and can't be diffed).
    """
    unavailable: list[str] = []
    if live_state.get("todoist_unavailable"):
        unavailable.append("todoist")
    if live_state.get("calendar_unavailable"):
        unavailable.append("calendar")
    if live_state.get("vault_unavailable"):
        unavailable.append("vault")

    todoist_tasks = live_state.get("todoist_tasks") or []
    calendar_events = live_state.get("calendar_events") or []
    vault_frontmatter = live_state.get("vault_frontmatter") or {}
    daily_note_text = live_state.get("daily_note_text")

    # T21 claim pre-pass: id-ref'd todoist rows own their task outright, in
    # any manifest order — content-fallback rows may only match what's left.
    claimed: set[str] = set()
    for m in manifest:
        if m.system == "todoist" and (m.id_or_path or "").startswith("todoist://"):
            claimed.add(m.id_or_path[len("todoist://"):])

    entries: list[ShadowDiffEntry] = []
    for m in manifest:
        if m.system == "todoist":
            if live_state.get("todoist_unavailable"):
                entries.append(ShadowDiffEntry(m, UNAVAILABLE, {"reason": "todoist surface unavailable"}))
                continue
            match = _match_todoist(m.name, todoist_tasks, m.id_or_path, claimed)
            if match is not None:
                claimed.add(str(match.get("id")))
            if match is None:
                entries.append(ShadowDiffEntry(m, CREATE, {"content": m.name, "due_time": m.time}))
                continue
            live_time = _todoist_due_time(match)
            is_recurring = bool((match.get("due") or {}).get("is_recurring"))
            if live_time == m.time:
                entries.append(ShadowDiffEntry(m, NOOP, {}))
            elif is_recurring:
                # Recurring tasks are pinned: the plan schedules AROUND them,
                # it never retimes them (their pattern owns the time).
                entries.append(ShadowDiffEntry(
                    m, NOOP, {
                        "task_id": match.get("id"),
                        "pinned_recurring": True,
                        "due_time": {"live": live_time, "planned": m.time},
                    }
                ))
            else:
                entries.append(ShadowDiffEntry(
                    m, UPDATE, {
                        "task_id": match.get("id"),
                        "due_time": {"old": live_time, "new": m.time},
                        "is_recurring": False,
                    }
                ))

        elif m.system == "calendar":
            if live_state.get("calendar_unavailable"):
                entries.append(ShadowDiffEntry(m, UNAVAILABLE, {"reason": "calendar surface unavailable"}))
                continue
            match = _match_calendar_event(m.name, m.time, calendar_events)
            if match is None:
                entries.append(ShadowDiffEntry(m, CREATE, {"title": m.name, "start": m.time}))
            else:
                detail: dict[str, Any] = {"event_id": match.get("id")}
                live_start = _event_start_hhmm(match)
                if m.time is not None and live_start != m.time:
                    detail["time_mismatch"] = {"live": live_start, "planned": m.time}
                entries.append(ShadowDiffEntry(m, NOOP, detail))

        elif m.action == "set-flag":  # Step C
            if live_state.get("vault_unavailable"):
                entries.append(ShadowDiffEntry(m, UNAVAILABLE, {"reason": "vault surface unavailable"}))
                continue
            if m.id_or_path not in vault_frontmatter:
                entries.append(ShadowDiffEntry(m, CONFLICT, {"reason": f"target missing: {m.id_or_path}"}))
                continue
            fm = vault_frontmatter[m.id_or_path] or {}
            if fm.get("assigned") is True:
                entries.append(ShadowDiffEntry(m, NOOP, {}))
            else:
                entries.append(ShadowDiffEntry(
                    m, UPDATE, {"assigned": {"old": fm.get("assigned"), "new": True}}
                ))

        elif m.action == "frontmatter-captures":  # B6
            if live_state.get("vault_unavailable"):
                entries.append(ShadowDiffEntry(m, UNAVAILABLE, {"reason": "vault surface unavailable"}))
                continue
            if daily_note_text is None:
                entries.append(ShadowDiffEntry(m, CONFLICT, {"reason": "daily note not found"}))
                continue
            fm = gather.parse_frontmatter(daily_note_text) or {}
            missing = [k for k in ("intention", "megan_nicety", "stoic_intention")
                       if k not in fm]
            if missing:
                entries.append(ShadowDiffEntry(m, UPDATE, {"missing_keys": missing}))
            else:
                entries.append(ShadowDiffEntry(m, NOOP, {}))

        elif m.action == "patch":  # Step B
            if live_state.get("vault_unavailable"):
                entries.append(ShadowDiffEntry(m, UNAVAILABLE, {"reason": "vault surface unavailable"}))
                continue
            if daily_note_text is None:
                entries.append(ShadowDiffEntry(m, CONFLICT, {"reason": "daily note not found"}))
            elif "# TDTB Plan" in daily_note_text:
                entries.append(ShadowDiffEntry(m, UPDATE, {"reason": "section exists, will be replaced"}))
            else:
                entries.append(ShadowDiffEntry(m, CREATE, {"reason": "section absent, will be appended"}))

        else:
            entries.append(ShadowDiffEntry(m, CONFLICT, {"reason": f"unrecognized manifest row: {m.step}/{m.action}"}))

    return ShadowDiff(entries=entries, unavailable_surfaces=unavailable)


# ---------------------------------------------------------------------------
# 3. gather_live_state — the I/O layer
# ---------------------------------------------------------------------------

def gather_live_state(config: Any, vault_root: str | Path) -> dict[str, Any]:
    """Read the current live state of every Phase-5 write target.

    Each surface degrades independently — a reachable-but-failing surface
    (auth, consent, network) yields an ``<surface>_unavailable`` marker in
    the returned dict rather than raising, so the shadow diff can still
    render what it can. The one hard failure is the Todoist token file being
    entirely absent: there's no way to even attempt that surface, so this
    raises ``ShadowStateError`` naming the missing file (distinct from a
    surface that's reachable but errors).

    ``config`` is accepted for parity with the live commit-writer's future
    signature (calendar-ID / project-ID resolution) but this shadow pass
    keeps its live reads deliberately broad (today's filter, today's
    calendar window, all tracked vault frontmatter) rather than
    config-scoped — precision narrows once real writers land in T14/T15.
    """
    vault_root = Path(vault_root)
    state: dict[str, Any] = {
        "todoist_tasks": [],
        "calendar_events": [],
        "vault_frontmatter": {},
        "daily_note_text": None,
    }

    if not TOKEN_ENV_PATH.is_file():
        raise ShadowStateError(
            f"Todoist token file not found: {TOKEN_ENV_PATH} — create it "
            "(TODOIST_TOKEN=... , mode 0600) before running a shadow commit"
        )

    # -- Todoist --------------------------------------------------------------
    try:
        token = todoist_client.load_token(TOKEN_ENV_PATH)
        with todoist_client.TodoistClient(token) as client:
            # G30: "today" alone excludes OVERDUE tasks (due yesterday or
            # earlier, still open) — they'd be entirely absent from this
            # snapshot, so diff_against_live's id/content match against them
            # can never fire and a still-open overdue task misclassifies as
            # would-create. "today | overdue" is the Todoist filter syntax
            # for "due today OR before today, not yet completed".
            state["todoist_tasks"] = client.get_filter_tasks("today | overdue")
    except Exception as exc:  # noqa: BLE001 — any Todoist failure degrades, never crashes the diff
        state["todoist_unavailable"] = True
        state["todoist_error"] = str(exc)

    # -- Calendar ---------------------------------------------------------------
    try:
        store = calendar_bridge.shared_store()
        status = store.auth_status()
        if status not in _CALENDAR_AUTH_OK:
            raise calendar_bridge.CalendarWriteError(f"calendar consent not granted: {status}")
        today = date.today()
        start = datetime.combine(today, datetime.min.time())
        end = datetime.combine(today, datetime.max.time())
        state["calendar_events"] = store.query_events(start, end)
    except Exception as exc:  # noqa: BLE001 — consent/import errors degrade, never crash
        state["calendar_unavailable"] = True
        state["calendar_error"] = str(exc)

    # -- Vault frontmatter --------------------------------------------------------
    try:
        fm_map: dict[str, Any] = {}
        for note in gather.walk_vault(vault_root):
            fm_map[note["path"]] = note["fm"]
        state["vault_frontmatter"] = fm_map
    except Exception as exc:  # noqa: BLE001
        state["vault_unavailable"] = True
        state["vault_error"] = str(exc)

    # -- Daily note text ----------------------------------------------------------
    try:
        today = gather.effective_date(datetime.now())
        daily_dir = vault_root / "30 - Daily"
        match: Path | None = None
        if daily_dir.is_dir():
            for candidate in (f"{today.isoformat()}.md", today.strftime("%b %d, %Y") + ".md"):
                p = daily_dir / candidate
                if p.is_file():
                    match = p
                    break
        if match is not None:
            state["daily_note_text"] = match.read_text(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001 — stays None; diff treats a missing note as a conflict, not a crash
        pass

    return state
