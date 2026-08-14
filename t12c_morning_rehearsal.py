#!/usr/bin/env python3
"""t12c_morning_rehearsal.py — T12c: drive a whole morning on a scratch vault
with a MOCKED sequencer, so defects are found here instead of on the one paid
`/sequence` call of Adam's attended T12 run.

On 2026-07-26 the single billed judgment call of the T12 shakedown bought a
defect list instead of a plan. T12a proved one route (the frame filter) end to
end; this harness widens that shape to the whole spine:

    /plan-inputs → /day-setup → /plan-inputs → staging verbs
      → /sequence (MOCKED judgment) → /validate-sequence
      → /commit?mode=shadow  ── asserting the "Exact writes" manifest each time

Safety envelope, identical in kind to `t12a_frame_filter_proof.py`:

  - a REAL throwaway scratch vault on disk, never `WALL·E-THNK`;
  - a REAL app instance, `create_app(vault_root=<scratch>)`, under uvicorn on
    :8790 — never :8746, and it refuses to start if :8790 is already busy;
  - `judgment.propose_sequence` replaced by a deterministic local function, so
    the Agent SDK is NEVER invoked and nothing is billed. The harness counts
    the stub's invocations and asserts the real client was never constructed;
  - `shadow.gather_live_state` canned to empty live surfaces, so every manifest
    row classifies as would-create and the response entries ARE the "Exact
    writes" list the cockpit renders;
  - `mode=shadow` only. No live `/commit`, no external write, ever.

The day is deliberately shaped like a real one rather than a minimal fixture:
a 09:00 anchor with anchored lifestyle blocks BOTH BEHIND it (07:45 Morning
Routine — elapsed, must never publish) and ahead of it, two recurring Todoist
rows carrying native times (the T27 auto-pin path), and a mixed pool of vault
projects, a press item and plain tasks totalling a full working day.

Scenarios:

  S1 — Clean spine. Load, confirm the day, shape it, sequence it, validate,
       shadow. Asserts the elapsed anchored block is filtered, the ahead ones
       publish, and every included row reaches the write contract.
  S2 — Staging verbs pre-commit (T2/T3). complete / defer / delete_permanent
       against digest-level targets with NO plan manifest in existence, plus
       idempotency and undo.
  S3 — T27 auto-pins. A recurring row with a native 14:00 time must land at
       14:00 even though the mocked sequencer would pack it at 10:00, and the
       server must derive the pin itself when the client sends none.
  S4 — The foreign-pin path. A row is completed OUT OF BAND (Todoist-side)
       mid-run; the client re-sequences still holding its pin. This is the
       shape that produced the unrecoverable 422 on 2026-07-26.
  S5 — Staged-completed rows must not reach the write contract.

Run:  app/.venv/bin/python t12c_morning_rehearsal.py
      app/.venv/bin/python t12c_morning_rehearsal.py --keep   # keep the vault
Exit: 0 = every assertion held; 1 = at least one defect (printed as a list).

Bundled T12c artifact alongside `t12a_frame_filter_proof.py`, not part of the
pytest suite: its value is that it runs the ROUTES, in order, as a morning.
"""
from __future__ import annotations

import argparse
import socket
import sys
import tempfile
import threading
import time
from datetime import date, datetime
from pathlib import Path

_APP = Path(__file__).parent / "app"
for _p in (str(_APP), str(_APP / "gather")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import httpx  # noqa: E402
import uvicorn  # noqa: E402

import external_sources  # noqa: E402
import judgment  # noqa: E402
import main as main_mod  # noqa: E402
import shadow as shadow_mod  # noqa: E402
import tdtb_gather as gather  # noqa: E402

HOST, PORT = "127.0.0.1", 8790
BASE = f"http://{HOST}:{PORT}"

# The frozen morning. 09:00 puts Morning Routine (07:45) behind the frame and
# everything else ahead of it — the "anchored blocks both before and after now"
# the T12c brief asks for, without T12a's already-proven 21:45 extreme.
FROZEN_HOUR, FROZEN_MIN = 9, 0

from datetime import datetime as _real_datetime  # noqa: E402


class FrozenDateTime(_real_datetime):
    """Freeze every route clock at 09:00 today, so the same run reproduces at
    any hour — the validator floors placements at now(), so a live evening
    clock would 422 an ordinary morning as 'placement in the past'."""

    @classmethod
    def now(cls, tz=None):  # noqa: D102
        real = _real_datetime.now(tz)
        return cls(real.year, real.month, real.day, FROZEN_HOUR, FROZEN_MIN, 0, tzinfo=tz)


TODAY = gather.effective_date(FrozenDateTime.now())

# --------------------------------------------------------------------------
# A day that looks like a real one
# --------------------------------------------------------------------------

CONFIG_NOTE = """\
---
description: T12c morning-rehearsal config
last_updated: 2026-07-27
---

# TDTB Bridger Config

## Defaults

| Key | Value    |
| --- | -------- |
| eod | 11:45 PM |
| work_allotment_minutes | 240 |

## Day Presets

| Name | Days | Zones | Work Allotment (min) | Default |
|------|------|-------|----------------------|---------|
| Workday | workdays |  | 240 | |
| Weekend | weekends |  | 0 | |
| Default | daily |  | 240 | true |

## Overlap Permissions

Default for everything is no-overlap.

## Anchored Lifestyle Blocks

| Block           | Type   | Start    | End      | Duration | Days  | overlap_allowed |
| --------------- | ------ | -------- | -------- | -------- | ----- | --------------- |
| Morning Routine | hard   | 7:45 AM  | —        | 80m      | daily | no              |
| Lunch           | hard   | 12:00 PM | —        | 30m      | daily | no              |
| Live            | window | 12:00 PM | 8:00 PM  | 30m      | daily | yes             |
| Foods Dinner    | window | 6:00 PM  | 8:30 PM  | 60m      | daily | no              |
| Wind Down       | hard   | 9:30 PM  | —        | 60m      | daily | no              |

## Calendar Titles

| Logical name | BusyCal title | Role        |
| ------------ | ------------- | ----------- |
| blocks       | ⬜ Blocks      | schedulable |

## Presets

| Name | Type | Blocks | Priority |
|------|------|--------|----------|
| Make | interval | 2 | 2 |
"""

ELAPSED_ANCHORED = {"Morning Routine"}          # 07:45, behind a 09:00 frame
# Ahead of the frame and published as calendar events. "Live" is deliberately
# NOT here: the micro-adventure block publishes as a Todoist row carrying the
# chosen adventure's name (T19), not as a ⬜ Blocks event — asserting it as a
# calendar write is a harness error, and was one on the first T12c run.
AHEAD_ANCHORED = {"Lunch", "Foods Dinner", "Wind Down"}

# Vault projects — the effort/press side of the pool.
# Every note carries a real `status:` line: without one, `vault.complete` fails
# closed by design, which would mask the far more interesting question of what
# happens to a note it CAN complete.
VAULT_ITEMS = [
    ("Garage Buildout", "project", "---\ntype: project\nstatus: active\nassigned: true\n"
                                   "urgency: 3-high\ndeadline: 2026-07-31\n---\nbody\n"),
    ("Magic Mirror", "project", "---\ntype: project\nstatus: active\nassigned: true\n"
                                "urgency: 2-med\n---\nbody\n"),
    ("Entryway Design", "project", "---\ntype: project\nstatus: active\nassigned: true\n---\nbody\n"),
    ("Guest Space Buildout", "project", "---\ntype: project\nstatus: active\nassigned: true\n---\nbody\n"),
    ("Sample Press", "press", "---\ntype: press\nstatus: active\nassigned: true\n"
                              "duration_min: 75\n---\nbody\n"),
]

# Todoist side. Two recurring rows carry a native time — those are the T27
# auto-pin path. `Standup` is the row S4 completes out of band mid-run.
RECURRING_PIN_TIME = {"Standup": "10:30", "Evening Review": "14:00"}


def todoist_tasks(*, drop: set[str] | None = None) -> list[dict]:
    drop = drop or set()
    iso = TODAY.isoformat()
    rows = [
        {"id": "9001", "content": "Draft PHEP handover", "priority": 3,
         "due": {"date": iso}, "duration": {"unit": "minute", "amount": 60},
         "labels": []},
        {"id": "9002", "content": "Order garage brackets", "priority": 2,
         "due": {"date": iso}, "duration": {"unit": "minute", "amount": 30},
         "labels": []},
        {"id": "9003", "content": "Standup", "priority": 4,
         "due": {"date": f"{iso}T{RECURRING_PIN_TIME['Standup']}:00",
                 "is_recurring": True},
         "duration": {"unit": "minute", "amount": 30}, "labels": []},
        {"id": "9004", "content": "Evening Review", "priority": 4,
         "due": {"date": f"{iso}T{RECURRING_PIN_TIME['Evening Review']}:00",
                 "is_recurring": True},
         "duration": {"unit": "minute", "amount": 30}, "labels": []},
        {"id": "9005", "content": "Rowe's shirt proof", "priority": 1,
         "due": {"date": iso}, "duration": {"unit": "minute", "amount": 90},
         "labels": []},
    ]
    return [r for r in rows if r["content"] not in drop]


class MutableTodoist:
    """Read-fake whose task list can change BETWEEN calls — that mutability is
    the whole point of S4: a row completed in the Todoist app mid-run
    disappears from the next gather while the client still holds its pin."""

    def __init__(self):
        self.dropped: set[str] = set()

    def get_filter_tasks(self, query, limit=None):
        if query == external_sources.ASSIGNED_QUERY_FALLBACK:
            return todoist_tasks(drop=self.dropped)
        return []

    def close(self):  # the route closes clients it owns
        pass


class MemoryTodoistWriter:
    """In-memory Todoist WRITER for `/runtime-actions`.

    This is not a nicety. `main._runtime_clients()` falls back to a REAL
    `TodoistClient` (token from `~/.config/tdtb/env`) and a REAL EventKit store
    whenever `app.state.build_commit_clients` is unset — so a runtime-action
    harness that injects only READ clients silently drives verbs against the
    live account. It was caught here on 2026-07-27: the first T12c run reached
    `GET /tasks/9001` and `GET /tasks/9002` on the real API (both 404, and every
    write step reads before it writes, so nothing mutated) purely because this
    injection was missing. Never omit it."""

    def __init__(self, tasks: list[dict]):
        self.tasks = {str(t["id"]): dict(t) for t in tasks}
        self.closed: set[str] = set()
        self.deleted: set[str] = set()

    def get_task(self, task_id):
        tid = str(task_id)
        if tid not in self.tasks:
            raise KeyError(f"no such task {tid}")
        return dict(self.tasks[tid])

    def close_task(self, task_id):
        self.closed.add(str(task_id))

    def reopen_task(self, task_id):
        self.closed.discard(str(task_id))

    def delete_task(self, task_id):
        self.deleted.add(str(task_id))
        self.tasks.pop(str(task_id), None)

    def reschedule_task(self, task_id, due_string):
        self.tasks[str(task_id)]["due"] = {"date": TODAY.isoformat()}
        return self.tasks[str(task_id)]

    def update_task(self, task_id, **kwargs):
        self.tasks[str(task_id)].update(kwargs)
        return self.tasks[str(task_id)]

    def create_task(self, content, **kwargs):
        tid = f"m{len(self.tasks) + 1}"
        self.tasks[tid] = {"id": tid, "content": content}
        return self.tasks[tid]

    def get_filter_tasks(self, query, limit=None):
        return list(self.tasks.values())

    def close(self):
        pass


class MemoryCalendarWriter:
    """In-memory EventKit stand-in for the same reason."""

    def __init__(self):
        self.events: dict[str, dict] = {}

    def calendars(self):
        return []

    def query_events(self, start, end, calendar_ids=None):
        return list(self.events.values())

    def get_event(self, event_id):
        return self.events.get(event_id)

    def delete_event(self, event_id):
        self.events.pop(event_id, None)

    def update_event(self, event_id, **kwargs):
        self.events.setdefault(event_id, {}).update(kwargs)
        return self.events[event_id]


class CalendarFake:
    def query_events(self, start, end, calendar_ids=None):
        return [{
            "id": "EV-1", "title": "PHEP sync",
            "start": datetime(TODAY.year, TODAY.month, TODAY.day, 11, 0),
            "end": datetime(TODAY.year, TODAY.month, TODAY.day, 11, 30),
            "calendar_id": "CAL-X",
        }]

    def calendars(self):
        return [{"id": "CAL-X", "title": "Rehearsal"}]


def build_vault(root: Path) -> Path:
    v = root / "vault"
    (v / "00 - META/Skill-Configs").mkdir(parents=True)
    (v / "00 - META/Skill-Configs/tdtb-bridger.md").write_text(
        CONFIG_NOTE, encoding="utf-8")
    proj = v / "50 - Operations/Projects"
    proj.mkdir(parents=True)
    for name, _kind, body in VAULT_ITEMS:
        (proj / f"{name}.md").write_text(body, encoding="utf-8")
    hab = v / "00 - META/Habituals"
    hab.mkdir(parents=True)
    (hab / "Water.md").write_text(
        "---\ntitle: Water\ntype: habit\nentries:\n  - 2020-01-01\n---\n",
        encoding="utf-8")
    daily = v / "30 - Daily" / f"{TODAY.isoformat()}.md"
    daily.parent.mkdir(parents=True, exist_ok=True)
    daily.write_text("---\ntype: daily\n---\n\n# Notes\n", encoding="utf-8")
    return v


# --------------------------------------------------------------------------
# Mocked judgment — nothing billed, and provably so
# --------------------------------------------------------------------------

class MockSequencer:
    """Deterministic stand-in for the Agent SDK. Packs movable rows from 10:00
    in the order handed to it. Counts its calls: that count IS the proof no
    billed call was made, because the real `propose_sequence` is unreachable
    while this is installed.

    Note: it packs straight THROUGH anchored walls, which the real sequencer
    avoids. The `unexpected_overlap` warnings S1 prints are therefore the
    harness's own doing, not app defects — and they are useful: they prove
    `validate_sequence` surfaces wall collisions as SOFT warnings instead of
    hard-failing the proposal (the never-bump contract, LD24).
    """

    def __init__(self):
        self.calls = 0
        self.last_movable: list[str] = []

    def __call__(self, assigned_arg, config, anchored, ctx):
        self.calls += 1
        self.last_movable = [str(i.get("id") or i.get("name")) for i in assigned_arg]
        rows, cursor = [], 10 * 60
        for item in assigned_arg:
            blocks = item.get("blocks")
            n = blocks if isinstance(blocks, (int, float)) and blocks > 0 else 1
            end = cursor + int(n * 30)
            rows.append({
                "id": str(item.get("id") or item.get("name")),
                "start": f"{cursor // 60:02d}:{cursor % 60:02d}",
                "end": f"{end // 60:02d}:{end % 60:02d}",
                "zone": None,
            })
            cursor = end
        return {"sequence": rows, "warnings": []}


def canned_live_state(config, vault_root):
    """Empty live surfaces that AGREE with the scratch vault: every manifest
    row then classifies would-create, so the shadow entries ARE the exact
    writes."""
    return {
        "todoist": {"tasks": []},
        "calendar": {"events": [], "unavailable": False},
        "vault_frontmatter": {
            f"50 - Operations/Projects/{name}.md": {"type": kind, "assigned": True}
            for name, kind, _ in VAULT_ITEMS
        },
        "daily_note_text": "---\ntype: daily\n---\n\n# Notes\n",
        "unavailable_surfaces": [],
    }


# --------------------------------------------------------------------------
# Server + a client that speaks the cockpit's own request shapes
# --------------------------------------------------------------------------

class Morning:
    """One scratch server plus the request bodies the production ApiAdapter
    builds (`frontend/src/adapters/api.ts`) — mirrored, not reinvented, so a
    contract change here is a real contract change."""

    def __init__(self, vault: Path, todoist: MutableTodoist):
        self.vault = vault
        self.todoist = todoist
        self.app = main_mod.create_app(vault_root=vault)
        self.app.state.build_read_clients = lambda v, cfg: (todoist, CalendarFake())
        # MANDATORY — see MemoryTodoistWriter's docstring. Without this the
        # runtime-action route builds live clients from the real token.
        self.todoist_writer = MemoryTodoistWriter(todoist_tasks())
        self.calendar_writer = MemoryCalendarWriter()
        self.app.state.build_commit_clients = lambda v, cfg: (
            self.todoist_writer, self.calendar_writer)
        self.token = self.app.state.token
        cfg = uvicorn.Config(self.app, host=HOST, port=PORT, log_level="warning")
        self.server = uvicorn.Server(cfg)
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.raw: dict = {}

    def __enter__(self) -> "Morning":
        self.thread.start()
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if self.server.started:
                return self
            time.sleep(0.05)
        raise RuntimeError(f"scratch server never came up on :{PORT}")

    def __exit__(self, *exc) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=10)

    # -- transport ---------------------------------------------------------
    @property
    def auth(self) -> dict:
        return {"X-TDTB-Token": self.token}

    def get(self, path: str) -> httpx.Response:
        return httpx.get(f"{BASE}{path}", headers=self.auth, timeout=30)

    def post(self, path: str, body: dict) -> httpx.Response:
        return httpx.post(f"{BASE}{path}", headers=self.auth, json=body, timeout=60)

    # -- spine -------------------------------------------------------------
    def load(self) -> dict:
        """GET /plan-inputs — also the route that writes today's digest index,
        which is what makes staging-phase verb resolution possible."""
        r = self.get("/plan-inputs")
        r.raise_for_status()
        self.raw = r.json()
        return self.raw

    def confirm_day(self, **overrides) -> dict:
        body = {
            "anchor": f"{FROZEN_HOUR:02d}:{FROZEN_MIN:02d}",
            "eod": "23:45",
            "buffering": "standard",
            "day_preset": "Workday",
            "work_allotment_minutes": 240,
            "captures": {"intention": "Rehearsal intention",
                         "megan_nicety": "Rehearsal nicety",
                         "stoic_intention": "Rehearsal stoic"},
        }
        body.update(overrides)
        r = self.post("/day-setup", body)
        r.raise_for_status()
        return r.json()

    def assigned_names(self) -> list[str]:
        return [str(row.get("name")) for row in (self.raw.get("digest") or {}).get("assigned") or []]

    def included(self, blocks_by_name: dict[str, int] | None = None,
                 exclude: set[str] | None = None) -> list[dict]:
        """The allocator's output: which rows are in, and how many blocks each
        slider left them at. Mirrors `ctx.included` in api.ts."""
        blocks_by_name = blocks_by_name or {}
        exclude = exclude or set()
        out = []
        for row in (self.raw.get("digest") or {}).get("assigned") or []:
            name = str(row.get("name"))
            if name in exclude:
                continue
            n = row.get("blocks")
            n = int(n) if isinstance(n, (int, float)) and n > 0 else 1
            out.append({"id": name, "blocks": blocks_by_name.get(name, n)})
        return out

    def _shape(self, included: list[dict]) -> list[dict]:
        """`shapeAssignedWire` — id := name, blocks := the slider value."""
        by_id = {i["id"]: i["blocks"] for i in included}
        return [{**row, "id": str(row.get("name")), "blocks": by_id[str(row.get("name"))]}
                for row in (self.raw.get("digest") or {}).get("assigned") or []
                if str(row.get("name")) in by_id]

    def sequence(self, included: list[dict], pinned_rows: list[dict] | None = None
                 ) -> httpx.Response:
        return self.post("/sequence", {
            "assigned": self._shape(included),
            "config": self.raw.get("config") or {},
            "anchored_blocks": self.raw.get("anchored_blocks") or [],
            "day_semantics": self.raw.get("day_semantics") or {},
            "planning_config_fingerprint": self.raw.get("planning_config_fingerprint") or "",
            "pinned_rows": pinned_rows or [],
        })

    def validate(self, rows: list[dict], included: list[dict],
                 pinned_rows: list[dict] | None = None) -> httpx.Response:
        return self.post("/validate-sequence", {
            "sequence": rows,
            "assigned": self._shape(included),
            "anchored_blocks": self.raw.get("anchored_blocks") or [],
            "config": self.raw.get("config") or {},
            "planning_config_fingerprint": self.raw.get("planning_config_fingerprint") or "",
            "pinned_rows": pinned_rows or [],
        })

    def shadow(self, rows: list[dict], included: list[dict],
               pinned_rows: list[dict] | None = None) -> httpx.Response:
        digest = dict(self.raw.get("digest") or {})
        digest["assigned"] = self._shape(included)
        return self.post("/commit?mode=shadow", {
            "digest": digest,
            "sequence": {"sequence": rows},
            "config": self.raw.get("config") or {},
            "pinned_rows": pinned_rows or [],
            "planning_config_fingerprint": self.raw.get("planning_config_fingerprint") or "",
        })

    def verb(self, verb: str, target: str, args: dict | None = None) -> httpx.Response:
        return self.post("/runtime-actions",
                         {"verb": verb, "target": target, "args": args or {}})


# --------------------------------------------------------------------------
# Reporting helpers
# --------------------------------------------------------------------------

def exact_writes(diff: dict) -> list[dict]:
    return [e["manifest"] for e in diff.get("entries") or []]


def write_names(diff: dict, system: str | None = None) -> set[str]:
    return {m["name"] for m in exact_writes(diff)
            if system is None or m["system"] == system}


def report_writes(diff: dict, label: str) -> None:
    rows = exact_writes(diff)
    print(f"\n  {label} — exact writes ({len(rows)} rows):")
    for m in rows:
        print(f"    [{str(m.get('step')):>2}] {m.get('system',''):<8} "
              f"{m.get('action',''):<16} {str(m.get('time') or '--:--'):>5}  {m.get('name')}")


def row_at(rows: list[dict], row_id: str) -> dict | None:
    return next((r for r in rows if str(r.get("id")) == row_id), None)


# --------------------------------------------------------------------------
# Scenarios
# --------------------------------------------------------------------------

def scenario_1(vault: Path, mock: MockSequencer) -> list[str]:
    """The clean spine, end to end, with the allocator's shaping applied."""
    fails: list[str] = []
    with Morning(vault, MutableTodoist()) as m:
        m.load()
        m.confirm_day()
        m.load()

        names = m.assigned_names()
        print(f"\n  S1: pool loaded — {len(names)} assigned rows: {names}")
        if len(names) < 8:
            fails.append(f"S1: pool is thinner than a real day ({len(names)} rows)")

        # The allocator: everything in, two sliders moved off their default.
        included = m.included({"Garage Buildout": 3, "Magic Mirror": 2})
        r = m.sequence(included)
        if r.status_code != 200:
            fails.append(f"S1: /sequence refused the clean day — {r.status_code} {r.text[:400]}")
            return fails
        proposal = r.json()
        rows = proposal.get("sequence") or []
        print(f"    /sequence ok — {len(rows)} rows, "
              f"{len(proposal.get('pinned_rows') or [])} effective pins, "
              f"{len(proposal.get('warnings') or [])} warnings")
        for w in proposal.get("warnings") or []:
            print(f"      warn: {w}")

        v = m.validate(rows, included, proposal.get("pinned_rows"))
        if v.status_code != 200:
            fails.append(f"S1: /validate-sequence {v.status_code} {v.text[:300]}")
        elif not v.json().get("ok"):
            fails.append("S1: the proposal the server just accepted fails its own "
                         f"revalidation — {v.json().get('hard_errors')}")

        s = m.shadow(rows, included, proposal.get("pinned_rows"))
        if s.status_code != 200:
            fails.append(f"S1: /commit?mode=shadow {s.status_code} {s.text[:400]}")
            return fails
        diff = s.json()
        report_writes(diff, "S1: clean morning")

        cal = write_names(diff, "calendar")
        leaked = cal & ELAPSED_ANCHORED
        if leaked:
            fails.append(f"S1: elapsed anchored block(s) {sorted(leaked)} reached the "
                         "write contract — these would be back-dated events")
        missing = AHEAD_ANCHORED - cal
        if missing:
            fails.append(f"S1: anchored blocks ahead of the frame were dropped: {sorted(missing)}")
        # The Live block's own publish path (T19): a Todoist row, not an event.
        if not any(m["system"] == "todoist" and str(m.get("name", "")).startswith("🌱")
                   for m in exact_writes(diff)):
            fails.append("S1: the Live micro-adventure block published no Todoist row")
        if mock.calls == 0:
            fails.append("S1: the mocked sequencer was never called — the route may "
                         "have reached the real (billed) judgment path")
    return fails


def scenario_2(vault: Path, mock: MockSequencer) -> list[str]:
    """Staging verbs against digest-level targets, with no manifest in
    existence — the T2/T3 pre-commit path."""
    fails: list[str] = []
    with Morning(vault, MutableTodoist()) as m:
        m.load()
        m.confirm_day()
        m.load()

        r = m.verb("complete", "Order garage brackets")
        if r.status_code != 200:
            fails.append(f"S2: staging complete refused — {r.status_code} {r.text[:300]}")
        else:
            body = r.json()
            # The journal entry's key is `id` — `action_id` is only the undo
            # route's path parameter. Reading the wrong one made a healthy
            # action look like it minted nothing.
            first_id = body.get("id")
            print(f"\n  S2: staging complete → {body.get('status')} (id {first_id})")
            if body.get("status") != "applied":
                fails.append(f"S2: staging complete returned 200 but status "
                             f"{body.get('status')!r} — the verb did not apply "
                             f"({body.get('error') or body.get('steps')})")
            if not first_id:
                fails.append("S2: staging complete minted no journal id — nothing to "
                             "undo, and the idempotency/undo checks cannot run")
            again = m.verb("complete", "Order garage brackets")
            if again.status_code != 200:
                fails.append(f"S2: repeat complete errored instead of being "
                             f"idempotent — {again.status_code} {again.text[:200]}")
            elif again.json().get("id") != first_id:
                fails.append("S2: repeating a staging verb minted a SECOND journal "
                             f"action ({again.json().get('id')} vs {first_id}) "
                             "— the idempotency key is not holding pre-commit")
            if first_id:
                u = m.post(f"/runtime-actions/{first_id}/undo", {})
                if u.status_code != 200:
                    fails.append(f"S2: undo of a staging action failed — "
                                 f"{u.status_code} {u.text[:300]}")

        d = m.verb("defer", "Draft PHEP handover")
        if d.status_code != 200:
            fails.append(f"S2: staging defer refused — {d.status_code} {d.text[:300]}")
        else:
            print(f"    staging defer → {d.json().get('status')}")

        x = m.verb("delete_permanent", "Entryway Design")   # vault-sourced
        if x.status_code != 200:
            fails.append(f"S2: staging delete_permanent refused — "
                         f"{x.status_code} {x.text[:300]}")
        else:
            print(f"    staging delete_permanent (vault row) → {x.json().get('status')}")
            if x.json().get("status") != "applied":
                fails.append("S2: staging delete_permanent of a VAULT row did not "
                             f"apply — {x.json().get('status')}")

        # The same verb against a TODOIST-sourced row. Both surfaces reach the
        # same staging resolver, so if one conflates its source identity the
        # other does too.
        xt = m.verb("delete_permanent", "Rowe's shirt proof")
        if xt.status_code != 200:
            fails.append(f"S2: staging delete_permanent (todoist row) refused — "
                         f"{xt.status_code} {xt.text[:300]}")
        else:
            print(f"    staging delete_permanent (todoist row) → {xt.json().get('status')}")
            if xt.json().get("status") != "applied":
                fails.append("S2: staging delete_permanent of a TODOIST row did not "
                             f"apply — {xt.json().get('status')}: "
                             f"{xt.json().get('error')}")

        unknown = m.verb("complete", "No Such Item")
        if unknown.status_code == 200:
            fails.append("S2: a verb against an item that is in neither the manifest "
                         "nor the digest was ACCEPTED — unknown targets must refuse")

        j = m.get("/runtime-actions")
        if j.status_code == 200:
            actions = j.json().get("actions") or []
            print(f"    journal holds {len(actions)} action(s)")
    return fails


def scenario_3(vault: Path, mock: MockSequencer) -> list[str]:
    """T27 auto-pins: a recurring row with a native time is placement-immune,
    derived server-side even when the client sends no pins at all."""
    fails: list[str] = []
    with Morning(vault, MutableTodoist()) as m:
        m.load()
        m.confirm_day()
        m.load()

        included = m.included()
        r = m.sequence(included, pinned_rows=[])   # client sends NOTHING
        if r.status_code != 200:
            fails.append(f"S3: /sequence refused — {r.status_code} {r.text[:400]}")
            return fails
        proposal = r.json()
        pins = {str(p.get("id")): p for p in proposal.get("pinned_rows") or []}
        rows = proposal.get("sequence") or []
        print(f"\n  S3: server derived {len(pins)} auto-pin(s): "
              f"{ {k: v.get('start') for k, v in pins.items()} }")

        for name, want in RECURRING_PIN_TIME.items():
            if name not in pins:
                fails.append(f"S3: recurring row {name!r} produced NO auto-pin — it "
                             "would be handed to judgment as movable")
                continue
            if pins[name].get("start") != want:
                fails.append(f"S3: {name!r} auto-pinned at {pins[name].get('start')}, "
                             f"expected its native {want}")
            placed = row_at(rows, name)
            if placed is None:
                fails.append(f"S3: {name!r} is pinned but absent from the sequence")
            elif placed.get("start") != want:
                fails.append(f"S3: {name!r} was placed at {placed.get('start')} despite "
                             f"a {want} pin — merge_pinned_rows did not win")
        if not fails:
            print("    OK — both recurring rows land on their native times")

        s = m.shadow(rows, included, proposal.get("pinned_rows"))
        if s.status_code != 200:
            fails.append(f"S3: shadow after auto-pins — {s.status_code} {s.text[:300]}")
        else:
            diff = s.json()
            for name, want in RECURRING_PIN_TIME.items():
                row = next((w for w in exact_writes(diff) if w.get("name") == name), None)
                if row is not None and row.get("time") not in (want, None):
                    fails.append(f"S3: the write contract publishes {name!r} at "
                                 f"{row.get('time')}, not its pinned {want}")
    return fails


def scenario_4(vault: Path, mock: MockSequencer) -> list[str]:
    """The foreign-pin path. A row completed out of band mid-run vanishes from
    the next gather; the client re-sequences still holding its pin."""
    fails: list[str] = []
    todoist = MutableTodoist()
    with Morning(vault, todoist) as m:
        m.load()
        m.confirm_day()
        m.load()

        included = m.included()
        first = m.sequence(included)
        if first.status_code != 200:
            fails.append(f"S4: baseline /sequence refused — {first.status_code}")
            return fails
        client_pins = list(first.json().get("pinned_rows") or [])
        print(f"\n  S4: client is holding {len(client_pins)} pin(s) "
              f"{[p.get('id') for p in client_pins]}")

        # ---- out of band: Adam ticks Standup off in the Todoist app --------
        todoist.dropped.add("Standup")
        m.load()                       # the app re-reads its sources
        after = m.assigned_names()
        if "Standup" in after:
            fails.append("S4: the out-of-band completion never reached the pool — "
                         "the scenario did not arm")
            return fails
        print(f"    'Standup' completed out of band; pool is now {len(after)} rows")

        included = m.included()
        retry = m.sequence(included, pinned_rows=client_pins)
        if retry.status_code == 200:
            print("    /sequence ACCEPTED the stale pin set — no 422 to recover from")
        else:
            detail = retry.json().get("detail") if retry.headers.get(
                "content-type", "").startswith("application/json") else retry.text
            print(f"    /sequence → {retry.status_code}: {str(detail)[:200]}")
            if retry.status_code != 422:
                fails.append(f"S4: expected a 422 or a clean accept, got {retry.status_code}")
            else:
                # The defect is not the 422 — failing closed before a billed
                # call is correct. The defect is whether the client can RECOVER
                # without a reload: does the error name the offending pin in a
                # machine-readable way?
                errs = (detail or {}).get("hard_errors") if isinstance(detail, dict) else None
                if not errs:
                    fails.append("S4: the 422 carries no hard_errors list — the client "
                                 "cannot tell which pin to drop")
                elif not any("Standup" in str(e) for e in errs):
                    fails.append(f"S4: the 422 does not name the stale pin: {errs}")
                else:
                    print("    the 422 names the stale pin — recoverable IF the client "
                          "parses hard_errors and drops it")
                # Recovery must actually work once the stale pin is dropped.
                pruned = [p for p in client_pins if str(p.get("id")) != "Standup"]
                again = m.sequence(included, pinned_rows=pruned)
                if again.status_code != 200:
                    fails.append("S4: UNRECOVERABLE — dropping the stale pin did not "
                                 f"clear the refusal ({again.status_code} "
                                 f"{again.text[:300]})")
                else:
                    print("    recovery confirmed: dropping the stale pin re-opens /sequence")
    return fails


def scenario_5(vault: Path, mock: MockSequencer) -> list[str]:
    """A row completed from the staging queue must not reach the write
    contract — the verb has to be visible to the commit planner."""
    fails: list[str] = []
    with Morning(vault, MutableTodoist()) as m:
        m.load()
        m.confirm_day()
        m.load()

        victim = "Magic Mirror"          # vault-sourced, carries a status: line
        r = m.verb("complete", victim)
        if r.status_code != 200:
            fails.append(f"S5: staging complete of {victim!r} refused — "
                         f"{r.status_code} {r.text[:300]}")
            return fails
        status = r.json().get("status")
        print(f"\n  S5: staging complete of {victim!r} → {status}")
        if status != "applied":
            fails.append(f"S5: staging complete of a vault row did not apply — "
                         f"{status}: {r.json().get('error')}")
            return fails

        m.load()
        still_pooled = victim in m.assigned_names()
        if still_pooled:
            fails.append(
                f"S5: {victim!r} was completed from the staging queue and is STILL "
                "in the pool after a source refresh — the controller refreshes "
                "expecting the row to leave the queue, so it comes back and can "
                "be acted on twice")
        included = m.included(exclude={victim})   # the allocator drops it
        seq = m.sequence(included)
        if seq.status_code != 200:
            fails.append(f"S5: /sequence after a staging complete — "
                         f"{seq.status_code} {seq.text[:400]}")
            return fails
        proposal = seq.json()
        s = m.shadow(proposal.get("sequence") or [], included,
                     proposal.get("pinned_rows"))
        if s.status_code != 200:
            fails.append(f"S5: shadow — {s.status_code} {s.text[:400]}")
            return fails
        diff = s.json()
        report_writes(diff, f"S5: after completing {victim!r} from staging")
        if victim in write_names(diff):
            fails.append(f"S5: {victim!r} was completed from the staging queue and "
                         "STILL appears in the write contract")
        print(f"    (row still present in the re-read pool: {still_pooled})")
    return fails


# --------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true", help="keep the scratch vaults")
    args = ap.parse_args()

    with socket.socket() as probe:
        if probe.connect_ex((HOST, PORT)) == 0:
            print(f"port {PORT} already in use — refusing to reuse a foreign server",
                  file=sys.stderr)
            return 2

    mock = MockSequencer()
    judgment.propose_sequence = mock
    shadow_mod.gather_live_state = canned_live_state
    main_mod.datetime = FrozenDateTime

    # Tripwire, not decoration: `_runtime_clients()` reaches for a real
    # TodoistClient and a real EventKit store whenever the commit-client
    # injection is missing. Make that unreachable so a future edit that drops
    # the injection fails loudly here instead of quietly on Adam's account.
    def _no_live_client(*a, **k):
        raise AssertionError(
            "T12c tried to construct a LIVE client — the commit-client "
            "injection is missing on this Morning instance")

    shadow_mod.todoist_client.TodoistClient = _no_live_client
    main_mod.calendar_bridge.shared_store = _no_live_client

    print(f"T12c — mocked-spine morning rehearsal  ({datetime.now():%H:%M:%S})")
    print(f"  scratch port :{PORT} · frozen {FROZEN_HOUR:02d}:{FROZEN_MIN:02d} clock · "
          "mocked judgment · shadow only · no live surfaces")

    failures: list[str] = []
    for fn in (scenario_1, scenario_2, scenario_3, scenario_4, scenario_5):
        root = Path(tempfile.mkdtemp(prefix=f"t12c-{fn.__name__}-"))
        if args.keep:
            print(f"\n  scratch vault kept: {root}")
        try:
            failures += fn(build_vault(root), mock)
        except Exception as exc:  # a crash IS a finding — keep going
            failures.append(f"{fn.__name__} raised {type(exc).__name__}: {exc}")
        finally:
            if not args.keep:
                import shutil
                shutil.rmtree(root, ignore_errors=True)

    print(f"\n  mocked sequencer invocations: {mock.calls} "
          "(every one of them free — the Agent SDK was never reachable)")
    print()
    if failures:
        print("DEFECTS / FAILURES:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("PASS — the whole morning spine holds on a seeded scratch vault: "
          "elapsed anchored blocks stay out of the write contract, staging verbs "
          "resolve pre-commit, recurring auto-pins survive to the manifest, and "
          "an out-of-band completion is recoverable.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
