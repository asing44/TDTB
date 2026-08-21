#!/usr/bin/env python3
"""T7 scratch integration gate — real routes, real ApiAdapter, nothing billed.

Boots the REAL FastAPI app against a synthetic scratch vault on :8790
(uvicorn, in-process thread), with the same fake externals as the T5 contract
capture:

- read clients faked (synthetic Todoist tasks + calendar events),
- ``judgment.propose_sequence`` monkeypatched to a canned deterministic
  proposal — route validation/injection code runs, the Agent SDK never does,
  nothing is billed,
- ``shadow.gather_live_state`` canned (empty live surfaces),
- commit clients injected as IN-MEMORY writers (T22 — previously ``(None,
  None)`` dead surfaces, which never exercised the calendar plan/verify path
  and let the Step E publish gap ship): live-commit orchestration runs the
  real writer + reconciliation code against in-memory stores; no external
  system is written.

Then runs the Vitest scratch suite (``src/adapters/scratch.integration.test.ts``)
with ``TDTB_SCRATCH_URL`` pointing at the server: the production ``ApiAdapter``
+ ``Controller`` drive load → day setup → shaping → sequence → drag-to-error →
deterministic revalidation → fix → shadow → arm → live commit over real HTTP.

NEVER points at :8746. Run with the app venv on PATH-free invocation:

    ../app/.venv/bin/python scripts/scratch_integration.py
"""
from __future__ import annotations

import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

FRONTEND = Path(__file__).resolve().parent.parent
APP = FRONTEND.parent / "app"
sys.path.insert(0, str(APP))
sys.path.insert(0, str(APP / "gather"))
sys.path.insert(0, str(FRONTEND / "scripts"))

HOST, PORT = "127.0.0.1", 8790
BASE = f"http://{HOST}:{PORT}"

import calendar_bridge  # noqa: E402
import capture_contract_fixtures as cf  # noqa: E402 — reuse vault + fakes
import judgment  # noqa: E402
import main as main_mod  # noqa: E402
import shadow as shadow_mod  # noqa: E402

import uvicorn  # noqa: E402


class WriterTodoist:
    """In-memory Todoist WRITER for the live-commit path (T22). The read
    fakes stay cf.todoist_fake(); this one only backs write_todoist's
    create/reschedule/readback loop."""

    def __init__(self):
        self._tasks: dict[str, dict] = {}
        self._seq = 9000

    def get_filter_tasks(self, q, limit=None):
        return list(self._tasks.values())

    def get_task(self, task_id):
        return self._tasks[task_id]

    def create_task(self, content, project_id=None, due_string=None,
                    duration=None, duration_unit=None, **_):
        from datetime import date
        self._seq += 1
        tid = f"t{self._seq}"
        hhmm = (due_string.split("at ", 1)[1].strip()
                if due_string and "at " in due_string else None)
        due = ({"date": f"{date.today().isoformat()}T{hhmm}:00"} if hhmm
               else {"date": date.today().isoformat()} if due_string == "today"
               else None)
        self._tasks[tid] = {"id": tid, "content": content, "due": due,
                            "project_id": project_id}
        return self._tasks[tid]

    def reschedule_task(self, task_id, due_string):
        from datetime import date
        hhmm = (due_string.split("at ", 1)[1].strip()
                if "at " in due_string else None)
        if hhmm:
            self._tasks[task_id]["due"] = {
                "date": f"{date.today().isoformat()}T{hhmm}:00"}
        elif due_string == "today":
            self._tasks[task_id]["due"] = {"date": date.today().isoformat()}
        return self._tasks[task_id]

    def reschedule_task_datetime(self, task_id, due_datetime):
        self._tasks[task_id]["due"] = {"date": due_datetime}
        return self._tasks[task_id]


class WriterStore:
    """In-memory EventKit WRITER with one writable ⬜ Blocks calendar, so the
    T22 anchored Step E publish path runs plan → resolve → write → verify."""

    def __init__(self):
        self._cals = [calendar_bridge.CalendarInfo(
            "⬜ Blocks", "cal-blocks-1", True, "iCloud")]
        self._events: dict[str, dict] = {}
        self._seq = 7000

    def calendars(self):
        return list(self._cals)

    def query_events(self, start, end, calendar_ids=None):
        return list(self._events.values())

    def create_event(self, spec):
        self._seq += 1
        eid = f"e{self._seq}"
        self._events[eid] = {"id": eid, "title": spec.title,
                             "start": spec.start, "end": spec.end,
                             "calendar_id": spec.calendar_id}
        return eid

    def get_event(self, event_id):
        return self._events.get(event_id)


from datetime import datetime as _real_datetime  # noqa: E402


class FrozenDateTime(_real_datetime):
    """The route validator floors the schedulable anchor at now(), so a real
    evening clock would 422 every fixed proposal as 'placement in the past'
    or spill it past midnight. Freeze the scratch server at 08:00 today —
    every run of this harness sees the same morning, any hour you run it."""

    @classmethod
    def now(cls, tz=None):  # noqa: D102
        real = _real_datetime.now(tz)
        return cls(real.year, real.month, real.day, 8, 0, 0, tzinfo=tz)


def canned_propose(assigned_arg, config, anchored, ctx):
    """Deterministic sequencer stand-in: pack sequentially from 10:00 (valid
    against the frozen 08:00 clock), honoring the shaped per-item blocks the
    frontend sent (T6/T7 payload contract)."""
    rows = []
    cursor = 10 * 60
    for item in assigned_arg:
        blocks = item.get("blocks")
        n = blocks if isinstance(blocks, (int, float)) and blocks > 0 else 1
        end = cursor + int(n * 30)
        rows.append({
            "id": item.get("name"),
            "start": f"{cursor // 60:02d}:{cursor % 60:02d}",
            "end": f"{end // 60:02d}:{end % 60:02d}",
            "zone": None,
        })
        cursor = end
    return {"sequence": rows, "warnings": []}


def build_vault(root):
    """Synthetic scratch vault, renamed off the generic ``Sample`` token so the
    scratch gate can't false-positive on a missing ``Sample`` collision."""
    vault = cf.build_vault(root)
    proj = vault / "50 - Operations/Projects"
    (proj / "Sample Project.md").rename(proj / "Client Project.md")
    (proj / "Sample Press.md").rename(proj / "Press Brief.md")
    return vault


def todoist_fake():
    """cf.todoist_fake() with only the synthetic assigned task renamed."""
    fake = cf.todoist_fake()
    for tasks in fake.by_query.values():
        for task in tasks:
            if task.get("content") == "Sample Todoist Task":
                task["content"] = "Inbox Task"
    return fake


def store_fake():
    """cf.store_fake() with only the synthetic calendar title renamed."""
    fake = cf.store_fake()
    for event in fake.events:
        if event.get("title") == "Sample Meeting":
            event["title"] = "Team Meeting"
    return fake


def canned_live_state(config, vault_root):
    """Canned live surfaces that AGREE with the scratch vault, so the commit
    planner sees clean would-creates instead of refusing on conflicts. All
    surfaces available: an unavailable surface is a shadow blocker that
    (correctly) refuses ARM_LIVE at the state level (T8)."""
    return {
        "todoist": {"tasks": []},
        "calendar": {"events": [], "unavailable": False},
        "vault_frontmatter": {
            "50 - Operations/Projects/Client Project.md": {"type": "project", "assigned": True},
            "50 - Operations/Projects/Make.md": {"type": "project", "assigned": True},
            "50 - Operations/Projects/Press Brief.md": {"type": "press", "assigned": True},
        },
        "daily_note_text": "---\ntype: daily\n---\n\n# Notes\n",
        "unavailable_surfaces": [],
    }


def main() -> int:
    with socket.socket() as probe:
        if probe.connect_ex((HOST, PORT)) == 0:
            print(f"port {PORT} already in use — refusing to reuse a foreign server", file=sys.stderr)
            return 2

    tmp = Path(tempfile.mkdtemp(prefix="tdtb-scratch-t7-"))
    vault = build_vault(tmp)
    # Daily note: the vault write path (plan section + captures) targets it.
    from datetime import date

    daily = vault / "30 - Daily" / f"{date.today().isoformat()}.md"
    daily.parent.mkdir(parents=True, exist_ok=True)
    daily.write_text("---\ntype: daily\n---\n\n# Notes\n", encoding="utf-8")
    app = main_mod.create_app(vault_root=vault)
    app.state.build_read_clients = lambda v, cfg: (todoist_fake(), store_fake())
    app.state.build_commit_clients = lambda v, cfg: (WriterTodoist(), WriterStore())
    judgment.propose_sequence = canned_propose
    shadow_mod.gather_live_state = canned_live_state
    main_mod.datetime = FrozenDateTime  # freeze route clocks (anchor, dates)

    server = uvicorn.Server(uvicorn.Config(app, host=HOST, port=PORT, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    deadline = time.time() + 15
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{BASE}/session-token", timeout=1) as r:
                if r.status == 200:
                    break
        except Exception:
            time.sleep(0.2)
    else:
        print("scratch server never came up", file=sys.stderr)
        return 2
    print(f"scratch server up on {BASE} (vault: {vault})")

    proc = subprocess.run(
        ["npx", "vitest", "run", "src/adapters/scratch.integration.test.ts"],
        cwd=FRONTEND,
        env={**__import__("os").environ, "TDTB_SCRATCH_URL": BASE},
    )
    server.should_exit = True
    thread.join(timeout=5)
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main())
