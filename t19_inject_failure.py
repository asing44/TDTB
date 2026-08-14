#!/usr/bin/env python3
"""t19_inject_failure.py — T19 acceptance harness: T15 ledger + resume proof.

The T19 gate (`spec.md` § 5) is a manual live end-to-end acceptance run. One
criterion it must exercise is the failure-safe commit orchestrator (T15): a
mid-commit surface failure must (a) never abort the run, (b) persist a
per-surface success ledger to `tdtb-runstate-<today>.md`, and (c) let a
`resume=true` re-run retry ONLY the unwritten surface and recover cleanly.

Proving that against LIVE Todoist/EventKit is fragile and destructive — it
needs a real half-failed commit, then a real recovery. So this script proves
the *same code path* (`orchestrate.run_orchestrated`) deterministically:

  - a REAL temp vault on disk (a throwaway `TemporaryDirectory`), so the
    persisted `tdtb-runstate-<today>.md` note is a real artifact you can open
    and read — the ledger you would otherwise inspect in WALL·E-THNK;
  - the SAME fakes the frozen orchestrate test suite uses (FakeTodoist /
    FakeStore / a tmp vault), so the writers run their real reconciliation
    logic — nothing here reimplements commit.py;
  - 0 model tokens, no live surfaces, no network — runnable anywhere,
    repeatable, side-effect-free.

It exercises both T15 guarantees:

  Scenario A — GRACEFUL one-surface failure (the "retries only the unwritten
    surface" proof). The calendar store lands events on the wrong calendar, so
    `write_calendar` returns ok=False. The three earlier surfaces
    (todoist -> vault_flips -> daily_note) all land ok and are persisted. A
    `resume=True` re-run with a healthy store re-dispatches ONLY calendar
    (proven by writer call-counts) and reaches all-ok.

  Scenario B — HARD crash mid-run (the crash-consistency proof).
    `commit.write_calendar` is monkeypatched to RAISE. `run_orchestrated` has
    no try/except around a writer, so the run aborts — but because the ledger
    is persisted after EACH surface, the on-disk note already carries the 3
    earlier ok surfaces. A `resume=True` re-run (patch removed) recovers.

Run:  python t19_inject_failure.py            # both scenarios
      python t19_inject_failure.py --keep     # keep the temp vault, print path
Exit: 0 = all guarantees held; 1 = a check failed (prints which).

This is a bundled T19 artifact, NOT part of the pytest suite — it drives the
frozen modules from outside and asserts nothing they don't already guarantee;
its value is a witnessable, live-shaped rehearsal of the resume mechanic Adam
must see pass before the T18 bake-in clock starts.
"""
from __future__ import annotations

import argparse
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path

_APP = Path(__file__).parent / "app"
for _p in (str(_APP), str(_APP / "gather")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import calendar_bridge  # noqa: E402
import commit  # noqa: E402
import orchestrate  # noqa: E402
import runstate as runstate_mod  # noqa: E402
import tdtb_gather as gather  # noqa: E402

TODAY = date.today()

# ---------------------------------------------------------------------------
# fakes — copied verbatim from tests/test_orchestrate.py (which copies them
# from tests/test_commit.py). Kept in sync by hand; if the frozen fakes ever
# change shape, this harness is the canary.
# ---------------------------------------------------------------------------


class FakeTodoist:
    def __init__(self, tasks=None):
        self._tasks = {t["id"]: t for t in (tasks or [])}
        self._seq = 1000
        self.created_calls = 0

    def get_filter_tasks(self, filter_id_or_query, limit=None):
        return list(self._tasks.values())

    def get_task(self, task_id):
        return self._tasks[task_id]

    def create_task(self, content, project_id=None, due_string=None,
                    duration=None, duration_unit=None, **_):
        self._seq += 1
        tid = f"t{self._seq}"
        hhmm = due_string.split("at ", 1)[1].strip() if due_string and "at " in due_string else None
        due = {"date": f"{TODAY.isoformat()}T{hhmm}:00"} if hhmm else None
        self._tasks[tid] = {"id": tid, "content": content, "due": due, "project_id": project_id}
        self.created_calls += 1
        return self._tasks[tid]

    def reschedule_task(self, task_id, due_string):
        hhmm = due_string.split("at ", 1)[1].strip() if "at " in due_string else None
        if hhmm:
            self._tasks[task_id]["due"] = {"date": f"{TODAY.isoformat()}T{hhmm}:00"}
        return self._tasks[task_id]


class FakeStore:
    def __init__(self, calendars=None, events=None, wrong_surface=False):
        self._cals = calendars or [
            calendar_bridge.CalendarInfo("Blocks", "cal-blocks-1", True, "iCloud"),
        ]
        self._events = {e["id"]: e for e in (events or [])}
        self._seq = 5000
        self._wrong_surface = wrong_surface
        self.created_calls = 0

    def calendars(self):
        return list(self._cals)

    def query_events(self, start, end, calendar_ids=None):
        return list(self._events.values())

    def create_event(self, spec):
        calendar_bridge.assert_write_target(spec.calendar_id, self._cals)
        self._seq += 1
        eid = f"e{self._seq}"
        landed_cal = "cal-OTHER" if self._wrong_surface else spec.calendar_id
        self._events[eid] = {"id": eid, "title": spec.title, "start": spec.start,
                             "end": spec.end, "calendar_id": landed_cal}
        self.created_calls += 1
        return eid

    def get_event(self, event_id):
        return self._events.get(event_id)


FLIP_REL = "P/Garage.md"
DAILY_REL = f"30 - Daily/{TODAY.isoformat()}.md"


def _build_vault(root: Path) -> Path:
    (root / "P").mkdir(parents=True, exist_ok=True)
    (root / FLIP_REL).write_text("---\nassigned: false\n---\nbody\n", encoding="utf-8")
    (root / "30 - Daily").mkdir(parents=True, exist_ok=True)
    (root / DAILY_REL).write_text("# Journal\n", encoding="utf-8")
    return root


def _all_four_intents() -> list[commit.WriteIntent]:
    """One intent per surface — the same minimal all-surface manifest the
    frozen orchestrate tests use, retargeted to today's date."""
    return [
        commit.WriteIntent("A", "todoist", "create", "Garage",
                           project_id=None, due_time="09:00", duration_min=30),
        commit.WriteIntent("C", "vault", "update", "Garage", path=FLIP_REL),
        commit.WriteIntent("B", "vault", "update", "# TDTB Plan"),
        commit.WriteIntent("D", "calendar", "create", "Minting",
                           calendar_id="cal-blocks-1", due_time="14:00",
                           start=datetime(TODAY.year, TODAY.month, TODAY.day, 14, 0),
                           end=datetime(TODAY.year, TODAY.month, TODAY.day, 15, 0)),
    ]


def _ledger_from_disk(vault: Path) -> dict:
    """Read back the persisted run-state note's JSON body — exactly what the
    orchestrator's own `_prior_state` reads for resume, and what you would
    `cat` in the live vault."""
    path = vault / runstate_mod.runstate_rel_path(TODAY)
    data = gather._extract_json_block(path.read_text(encoding="utf-8"))
    assert data is not None, "run-state note has no JSON block"
    return data


def _spy(module, name):
    """Wrap a writer with a call-counter (the test suite's `_spy`), returning
    the list so the caller can assert re-dispatch counts."""
    orig = getattr(module, name)
    calls: list[int] = []

    def wrapper(*a, **kw):
        calls.append(1)
        return orig(*a, **kw)

    setattr(module, name, wrapper)
    return calls, orig


# ---------------------------------------------------------------------------
# check harness
# ---------------------------------------------------------------------------

_FAILURES: list[str] = []


def check(cond: bool, label: str) -> None:
    mark = "PASS" if cond else "FAIL"
    print(f"    [{mark}] {label}")
    if not cond:
        _FAILURES.append(label)


def _surface_line(surfaces: dict) -> str:
    return "  ".join(f"{k}={surfaces[k]['status']}" for k in orchestrate.SURFACES)


# ---------------------------------------------------------------------------
# Scenario A — graceful one-surface failure, resume retries only that surface
# ---------------------------------------------------------------------------

def scenario_graceful(vault: Path) -> None:
    print("\n== Scenario A: graceful one-surface failure (calendar ok=False) ==")
    _build_vault(vault)

    calls_a, _ = _spy(commit, "write_todoist")
    calls_c, _ = _spy(commit, "write_frontmatter_flips")
    calls_b, _ = _spy(commit, "write_daily_note")
    calls_d, orig_d = _spy(commit, "write_calendar")
    try:
        todoist = FakeTodoist()

        # Run 1: calendar store mis-lands events -> write_calendar returns ok=False.
        r1 = orchestrate.run_orchestrated(
            _all_four_intents(), todoist=todoist, store=FakeStore(wrong_surface=True),
            vault_root=vault, plan_body="- 09:00 Garage", today=TODAY,
        )
        print(f"    run1 surfaces: {_surface_line(r1['surfaces'])}   ok={r1['ok']}")
        check(r1["ok"] is False, "run1 overall ok is False (one surface failed)")
        check(r1["surfaces"]["calendar"]["status"] == "failed", "run1 calendar surface failed")
        check(all(r1["surfaces"][k]["status"] == "ok"
                  for k in ("todoist", "vault_flips", "daily_note")),
              "run1 the three earlier surfaces all landed ok")

        disk1 = _ledger_from_disk(vault)["commit_ledger"]["surfaces"]
        check(disk1["todoist"]["status"] == "ok"
              and disk1["vault_flips"]["status"] == "ok"
              and disk1["daily_note"]["status"] == "ok"
              and disk1["calendar"]["status"] == "failed",
              "run1 ledger PERSISTED to disk with 3 ok + 1 failed")
        check((len(calls_a), len(calls_c), len(calls_b), len(calls_d)) == (1, 1, 1, 1),
              "run1 dispatched every writer exactly once")

        # Run 2: healthy store + resume=True -> only calendar re-dispatched.
        r2 = orchestrate.run_orchestrated(
            _all_four_intents(), todoist=todoist, store=FakeStore(),
            vault_root=vault, plan_body="- 09:00 Garage", today=TODAY, resume=True,
        )
        print(f"    run2 surfaces: {_surface_line(r2['surfaces'])}   ok={r2['ok']}  resumed={r2['resumed']}")
        check(r2["ok"] is True, "run2 recovers to overall ok")
        check(all(r2["surfaces"][k].get("note") == "resumed: already ok"
                  for k in ("todoist", "vault_flips", "daily_note")),
              "run2 the three ok surfaces carried forward (not re-run)")
        check((len(calls_a), len(calls_c), len(calls_b), len(calls_d)) == (1, 1, 1, 2),
              "run2 re-dispatched ONLY calendar (todoist/flips/daily untouched)")
        check(_ledger_from_disk(vault)["commit_ledger"]["surfaces"]["calendar"]["status"] == "ok",
              "run2 on-disk ledger now shows calendar ok")
    finally:
        _hard_restore()  # undo all four writer spies


# ---------------------------------------------------------------------------
# Scenario B — hard crash mid-run, ledger already persisted earlier surfaces
# ---------------------------------------------------------------------------

def scenario_crash(vault: Path) -> None:
    print("\n== Scenario B: hard crash mid-run (write_calendar raises) ==")
    _build_vault(vault)

    orig = commit.write_calendar

    def boom(*a, **kw):
        raise RuntimeError("injected calendar crash")

    commit.write_calendar = boom
    try:
        crashed = False
        try:
            orchestrate.run_orchestrated(
                _all_four_intents(), todoist=FakeTodoist(), store=FakeStore(),
                vault_root=vault, plan_body="- x", today=TODAY,
            )
        except RuntimeError as exc:
            crashed = "injected calendar crash" in str(exc)
        check(crashed, "run aborted with the injected crash (no writer try/except)")

        disk = _ledger_from_disk(vault)["commit_ledger"]["surfaces"]
        print(f"    on-disk surfaces after crash: {sorted(disk)}")
        check(set(disk) == {"todoist", "vault_flips", "daily_note"},
              "ledger persisted the 3 pre-crash surfaces; calendar absent")
        check(all(disk[k]["status"] == "ok" for k in disk),
              "all pre-crash surfaces recorded ok (crash-consistency)")
    finally:
        commit.write_calendar = orig

    # recovery: patch removed, resume=True finishes calendar.
    r = orchestrate.run_orchestrated(
        _all_four_intents(), todoist=FakeTodoist(), store=FakeStore(),
        vault_root=vault, plan_body="- x", today=TODAY, resume=True,
    )
    print(f"    recovery surfaces: {_surface_line(r['surfaces'])}   ok={r['ok']}")
    check(r["ok"] is True, "resume recovers the crashed run to all-ok")
    check(r["surfaces"]["calendar"]["status"] == "ok", "calendar completed on resume")


# ---------------------------------------------------------------------------
# spy restore helpers — keep the monkeypatched writers from leaking between
# scenarios (each scenario re-spies from the current module attr).
# ---------------------------------------------------------------------------

_PRISTINE = {name: getattr(commit, name) for name in
             ("write_todoist", "write_frontmatter_flips", "write_daily_note", "write_calendar")}


def _hard_restore() -> None:
    for name, fn in _PRISTINE.items():
        setattr(commit, name, fn)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--keep", action="store_true",
                    help="keep the temp vault and print its path for manual inspection")
    args = ap.parse_args()

    print(f"T19 ledger+resume injection proof — today={TODAY}")
    print("(deterministic: temp vault, frozen fakes, 0 tokens, no live surfaces)")

    tmp = tempfile.mkdtemp(prefix="t19-inject-")
    root = Path(tmp)
    try:
        scenario_graceful(root / "A")
        _hard_restore()
        scenario_crash(root / "B")
    finally:
        _hard_restore()
        if args.keep:
            print(f"\nTemp vault kept at: {root}")
            print(f"  Scenario A run-state note: {root / 'A' / runstate_mod.runstate_rel_path(TODAY)}")
        else:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)

    print()
    if _FAILURES:
        print(f"RESULT: FAIL — {len(_FAILURES)} check(s) failed:")
        for f in _FAILURES:
            print(f"  - {f}")
        return 1
    print("RESULT: PASS — T15 ledger persistence + resume-only-retry held on both scenarios.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
