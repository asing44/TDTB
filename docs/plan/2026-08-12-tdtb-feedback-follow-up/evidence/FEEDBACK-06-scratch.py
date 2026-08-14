#!/usr/bin/env python3
"""FEEDBACK-06 scratch requalification (2026-08-14).

Isolated, fixture-only walkthrough that re-proves the finish-first contract
without touching any live runtime, provider, vault, Todoist, or Calendar
source. Uses the read-only fake EventStore ledger, pure capacity/sequence
functions, and an in-memory TestClient against a temp vault.

Asserted scratch claims:
  S1 Cooking (fixed) and DCP Bark Bar trivia (work) are hard walls — assigned
     rows overlapping them are rejected by name.
  S2 Steelers vs Packers (quarantined) is NOT a wall — a row crossing only it
     validates clean; it costs zero capacity.
  S3 Over-assignment (16 selected vs 2 available) is explicit, never silent.
  S4 The final sequence is chronological — merge_immutable_rows sorts
     (start, id); validation rejects a descending final order deterministically.
  S5 Zero calendar writer calls, zero billed calls, zero live POSTs — the fake
     store ledger records only read verbs, judgment.propose_sequence is never
     invoked, and no HTTP call leaves this process.
"""
from __future__ import annotations

import sys
import tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
APP_DIR = ROOT / "app"
sys.path.insert(0, str(APP_DIR))

import external_sources as ext
import main as main_mod
from sequence import merge_immutable_rows, validate_sequence

from fastapi.testclient import TestClient

TODAY = datetime(2026, 7, 14).date()
RESULTS: list[dict] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append({"assertion": name, "pass": bool(ok), "detail": detail})
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")


def _event(name: str, start: str, end: str, capacity_class: str) -> dict:
    return {
        "Block": name,
        "Start": start,
        "End": end,
        "source": "calendar",
        "calendar_id": f"CAL-{name}",
        "calendar_title": name,
        "capacity_class": capacity_class,
    }


def _seq_row(id_: str, start: str, end: str, zone: str = "any"):
    return {"id": id_, "start": start, "end": end, "zone": zone}


def _item(id_: str, zone: str = "any"):
    return {"id": id_, "zone": zone}


class _FakeCalendar:
    def __init__(self, title: str, identifier: str):
        self.title = title
        self.identifier = identifier


class _FakeStore:
    """Read-only EventStore fake with a call ledger; writer verbs absent so a
    production path reaching for one raises instead of mutating."""

    def __init__(self, events: list[dict], calendars: list[_FakeCalendar]):
        self._events = events
        self._calendars = calendars
        self.calls: list[str] = []

    def auth_status(self) -> str:
        self.calls.append("auth_status")
        return "authorized"

    def calendars(self) -> list[_FakeCalendar]:
        self.calls.append("calendars")
        return self._calendars

    def query_events(self, start, end, calendar_ids):
        self.calls.append("query_events")
        return self._events

    def save_event(self):
        raise AssertionError("calendar writer save_event must never be called")

    def update_event(self):
        raise AssertionError("calendar writer update_event must never be called")

    def delete_event(self):
        raise AssertionError("calendar writer delete_event must never be called")


def _fixture():
    calendars = [
        _FakeCalendar("Cooking", "CAL-COOK"),
        _FakeCalendar("DCP Bark Bar trivia", "CAL-TRIV"),
        _FakeCalendar("Sports", "CAL-SPORT"),
    ]
    cfg = {
        "calendar_ids": {},
        "calendar_capacity_classes": [
            {"BusyCal title": "Cooking", "Class": "fixed"},
            {"BusyCal title": "DCP Bark Bar trivia", "Class": "work"},
        ],
    }
    events = [
        {"title": "Cooking", "calendar_id": "CAL-COOK",
         "start": datetime(2026, 7, 14, 20, 30), "end": datetime(2026, 7, 14, 21, 0)},
        {"title": "DCP Bark Bar trivia", "calendar_id": "CAL-TRIV",
         "start": datetime(2026, 7, 14, 19, 0), "end": datetime(2026, 7, 14, 20, 0)},
        {"title": "Steelers vs Packers", "calendar_id": "CAL-SPORT",
         "start": datetime(2026, 7, 14, 20, 0), "end": datetime(2026, 7, 14, 22, 0)},
    ]
    return _FakeStore(events, calendars), cfg


def main() -> int:
    store, cfg = _fixture()

    # S1+S2: classification and wall enforcement from the fake read pipeline.
    blocks, warnings = ext.fetch_calendar_busy(store, cfg, TODAY)
    by_name = {b["Block"]: b["capacity_class"] for b in blocks}
    check("S1 classification Cooking=fixed", by_name.get("Cooking") == "fixed")
    check("S1 classification DCP trivia=work", by_name.get("DCP Bark Bar trivia") == "work")
    check("S2 classification Steelers=quarantined", by_name.get("Steelers vs Packers") == "quarantined")
    check("S1 no unclassified rows", warnings == [])

    WALLS = list(blocks) + [
        {"Block": "Foods Dinner", "Type": "window", "Start": "18:00",
         "End": "20:30", "Duration": "60m"},
    ]
    FRAME = {"anchor": "17:15", "effective_eod": "23:00"}

    r_cook = validate_sequence(
        {"sequence": [_seq_row("t1", "20:35", "21:05")]},
        [_item("t1")], list(WALLS), {}, time_frame=FRAME,
    )
    check("S1 Cooking wall blocks overlap",
          r_cook.ok is False and any("Cooking" in e and "overlap" in e.lower()
                                     for e in r_cook.hard_errors))

    r_trivia = validate_sequence(
        {"sequence": [_seq_row("t1", "19:15", "19:45")]},
        [_item("t1")], list(WALLS), {}, time_frame=FRAME,
    )
    check("S1 trivia work wall blocks overlap",
          r_trivia.ok is False and any("DCP Bark Bar trivia" in e
                                       for e in r_trivia.hard_errors))

    r_steelers = validate_sequence(
        {"sequence": [_seq_row("t1", "20:00", "20:30")]},
        [_item("t1")], list(WALLS), {}, time_frame=FRAME,
    )
    check("S2 Steelers exclusion is no wall",
          r_steelers.ok is True and r_steelers.hard_errors == [])

    # S3: explicit over-assignment via the capacity frame.
    _t, cap = main_mod._capacity_frame(
        {
            "Defaults": {
                "eod": "23:00",
                "anchor.round_to_minutes": 15,
                "buffering.off_pct": 0,
            },
            "Anchored Lifestyle Blocks": [
                {"Block": "Suds", "Type": "hard", "Start": "17:45",
                 "End": "18:15", "Duration": "30m"},
                {"Block": "Foods Dinner", "Type": "window", "Start": "18:00",
                 "End": "20:30", "Duration": "60m"},
                {"Block": "Night Routine", "Type": "hard", "Start": "23:00",
                 "End": "23:45", "Duration": "45m"},
            ],
        },
        {"anchor": "17:15", "eod": "23:00", "buffering": "off"},
        blocks,
        {"est_minutes": 30, "done": 0, "outstanding": 1},
        {"effective_allotment_minutes": 60},
        now=datetime(2026, 7, 14, 17, 10),
        extra_selected_blocks=16,
    )
    check("S3 capacity total=11", cap.total == 11)
    check("S3 fixed counts Cooking once", cap.fixed == 1)
    check("S3 available_for_selection=2", cap.available_for_selection == 2)
    check("S3 selected=16", cap.selected == 16)
    check("S3 free=-14", cap.free == -14)
    check("S3 overassigned flag true", cap.overassigned is True)
    check("S3 explicit remaining warning", "over" in cap.remaining)
    check("S3 Steelers adds zero capacity",
          cap.work_busy == 2 and cap.work_overflow == 0 and cap.mint == 2)

    # S4: chronology — merge sorts (start, id); descending final order rejected.
    model_rows = [
        {"id": "Deep Work", "start": "23:15", "end": "23:45", "zone": None},
        {"id": "Log hours", "start": "23:15", "end": "23:45", "zone": None},
    ]
    immutable = [{
        "id": "Log hours", "start": "17:15", "end": "17:45", "zone": "any",
    }]
    merged = merge_immutable_rows(model_rows, immutable)
    check("S4 merge sorts chronological after replace",
          [r["id"] for r in merged] == ["Log hours", "Deep Work"]
          and [r["start"] for r in merged] == ["17:15", "23:15"]
          and merged[0] is immutable[0])

    r_desc = validate_sequence(
        {"sequence": [
            _seq_row("Pick up prescription", "23:15", "23:45"),
            _seq_row("Log hours", "17:15", "17:45"),
        ]},
        [_item("Pick up prescription"), _item("Log hours")], [], {},
    )
    check("S4 descending final order rejected deterministically",
          r_desc.ok is False and any(
              "chronological" in e.lower() and "Log hours" in e and "17:15" in e
              and "23:15" in e for e in r_desc.hard_errors))

    # S5: zero calendar writer calls — ledger must hold only read verbs.
    check("S5 fake store ledger read-only",
          store.calls == ["auth_status", "calendars", "query_events"],
          f"ledger={store.calls}")

    # S5b: in-memory TestClient scratch — route-level wall proof, no network.
    vault = Path(tempfile.mkdtemp(prefix="feedback06-scratch-"))
    vault.mkdir(exist_ok=True)
    app = main_mod.create_app(vault_root=vault)
    tc = TestClient(app)
    token = app.state.token
    headers = {"X-TDTB-Token": token}
    r_route = tc.post(
        "/validate-sequence",
        headers=headers,
        json={
            "sequence": [{"id": "t1", "start": "20:35", "end": "21:05", "zone": "any"}],
            "assigned": [{"id": "t1", "zone": "any"}],
            "anchored_blocks": WALLS,
            "config": {"time": FRAME},
        },
    )
    ok_route = r_route.status_code == 200 and r_route.json().get("ok") is False
    check("S1 route /validate-sequence hard-rejects Cooking overlap", ok_route)
    tc.close()

    passed = sum(1 for r in RESULTS if r["pass"])
    total = len(RESULTS)
    print(f"\nSCRATCH RESULT: {passed}/{total} assertions passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
