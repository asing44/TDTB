#!/usr/bin/env python3
"""FEEDBACK-28 scratch requalification (2026-08-14).

Verification-only, fixture-only walkthrough that re-proves the write-path
and Mint reliability contracts from FEEDBACK-23..27 WITHOUT touching any
live runtime, provider, vault, Todoist, or Calendar source. Uses in-memory
fakes (FakeTodoist/FakeStore/ShiftingStore), pure functions, and an
in-memory TestClient against a temp vault.

Asserted scratch claims:
  S1 Press due normalization (FEEDBACK-23): raw UTC wall 23:00 != intent
     19:00 (the original failure); normalized 23:00Z through
     America/New_York == 19:00 local verifies clean; a true mismatch fails
     with a 12-hour user message (no raw 24h); missing timezone fails
     closed; structured detail keeps canonical machine values.
  S2 Day Setup gate (FEEDBACK-24): a /gather-materialised skeleton runstate
     is NOT confirmed; /commit?mode=live and /runtime-actions apply/undo
     fail closed 409 with zero vault bytes; only POST /day-setup unblocks a
     live commit (with fakes).
  S3 Mint capacity (FEEDBACK-25): a configured 300-minute allotment derives
     10 blocks / 300 minutes in rows AND capacity; the hardcoded 2-block
     default survives only without allotment context; 10 selected sessions
     total 10 blocks.
  S4 Mint walls (FEEDBACK-25): an assigned row overlapping a selected Mint
     session is a HARD rejection (no overlap-grant escape); back-to-back
     touch allowed; the /validate-sequence route rejects the same shape.
  S5 Mint readback (FEEDBACK-26): a shifted interval readback FAILS with
     structured kind='calendar' mismatch details; an exact readback PASSES;
     a same-title source event is never treated as our write.
  S6 Trinoor classification (FEEDBACK-26): '[🟡 ]Trinoor : <slot>' is Step
     D'; 'Trinoor sync'/'Trinoorish' keep Step E/D.
  S7 Zero external writers: the read pipeline records exactly
     auth_status/calendars/query_events; write fakes expose no live handle;
     no live POST, billed call, restart, or Git mutation.
"""
from __future__ import annotations

import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
APP_DIR = ROOT / "app"
sys.path.insert(0, str(APP_DIR))
sys.path.insert(0, str(APP_DIR / "gather"))
sys.path.insert(0, str(APP_DIR / "tests"))

import commit  # noqa: E402
import external_sources as ext  # noqa: E402
import main as main_mod  # noqa: E402
import runstate as rs  # noqa: E402
import shadow  # noqa: E402
import tdtb_gather as gather  # noqa: E402
from sequence import validate_sequence  # noqa: E402
from test_commit import FakeStore, FakeTodoist, ShiftingStore  # noqa: E402
from test_main_api import (  # noqa: E402
    FakeLiveStore,
    FakeLiveTodoist,
    LIVE_DIGEST,
    LIVE_SEQUENCE,
    _fake_live_state,
)
from fastapi.testclient import TestClient  # noqa: E402

TODAY = date(2026, 7, 12)
MONDAY = date(2026, 7, 13)
GATE_DETAIL = "Day Setup not confirmed"
CFG = {
    "Template Blocks": {"Trinoor Hours": [
        {"Slot": "Morning", "Start": "8:30 AM", "End": "12:30 PM"},
        {"Slot": "Afternoon", "Start": "1:30 PM", "End": "5:00 PM"},
    ]},
}

RESULTS: list[dict] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append({"assertion": name, "pass": bool(ok), "detail": detail})
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")


def _seq_row(id_, start, end, zone="any"):
    return {"id": id_, "start": start, "end": end, "zone": zone}


def _mint_item(id_="Mint Morning · 08:30", start="08:30", end="09:00"):
    return {
        "id": id_, "name": id_, "zone": "work_hours", "source": "schedulable",
        "mint_session": True, "mint_session_id": f"mint:morning:{start}",
        "placement_window": {"start": start, "end": end},
        "calendar_class": "mint",
    }


def _due_task(task_id, name, due):
    return {"id": task_id, "content": name, "due": due}


def _noop_intent(name, hhmm, task_id="t42"):
    return commit.WriteIntent(
        step="A", surface="todoist", op="noop", name=name,
        task_id=task_id, due_time=hhmm, duration_min=60,
    )


def _mint_intent(name="🟡 Minting 1", start_h="13", start_m=0,
                 end_h="14", end_m=0, **kw):
    base = dict(step="D", surface="calendar", op="create", name=name,
                calendar_id="cal-blocks-1", due_time=f"{start_h}:{start_m:02d}",
                start=datetime(2026, 7, 12, int(start_h), start_m),
                end=datetime(2026, 7, 12, int(end_h), end_m))
    base.update(kw)
    return commit.WriteIntent(**base)


def s1_press_due() -> None:
    """FEEDBACK-23: 23:00Z normalizes to 19:00 local; 12h user errors."""
    task = _due_task("t42", "Press",
                     {"date": "2026-07-12T23:00:00Z",
                      "timezone": "America/New_York"})
    raw_wall = str(task["due"]["date"]).split("T")[1][:5]
    check("S1 raw UTC wall clock is 23:00 (original mismatch)", raw_wall == "23:00")
    check("S1 raw wall != 19:00 local intent before normalization",
          raw_wall != "19:00")

    reading = commit._todoist_due_reading(task)
    check("S1 normalized 23:00Z through America/New_York == 19:00 local",
          reading.local_hhmm == "19:00" and reading.error is None,
          f"local_hhmm={reading.local_hhmm}")

    r = commit.write_todoist([_noop_intent("Press", "19:00")],
                             FakeTodoist([task]))
    check("S1 equivalent encoding verifies clean",
          r.ok and r.verify_failures == [], f"err={r.error}")

    float_client = FakeTodoist([_due_task(
        "t42", "Walk", {"date": "2026-07-12T19:00:00",
                        "timezone": "America/New_York"})])
    r2 = commit.write_todoist([_noop_intent("Walk", "19:00")], float_client)
    check("S1 floating local due verifies locally (timezone must not shift)",
          r2.ok and r2.verify_failures == [])

    mismatch = FakeTodoist([_due_task("t42", "Press",
                                      {"date": "2026-07-12T23:00:00"})])
    r3 = commit.write_todoist([_noop_intent("Press", "19:00")], mismatch)
    msg = r3.verify_failures[0] if r3.verify_failures else ""
    check("S1 true mismatch fails (12h text, no raw 24h)",
          (not r3.ok) and "due mismatch" in msg and "7 PM" in msg
          and "11 PM" in msg and "19:00" not in msg and "23:00" not in msg,
          msg)

    missing_tz = FakeTodoist([_due_task(
        "t42", "Press", {"date": "2026-07-12T23:00:00Z"})])
    r4 = commit.write_todoist([_noop_intent("Press", "19:00")], missing_tz)
    check("S1 missing timezone on UTC due fails closed",
          (not r4.ok) and any("missing timezone" in f
                              for f in r4.verify_failures))

    struct_client = FakeTodoist([task])
    r5 = commit.write_todoist([_noop_intent("Press", "18:00")], struct_client)
    d = r5.verify_details[0] if r5.verify_details else {}
    check("S1 structured detail keeps canonical machine values",
          d.get("kind") == "due" and d.get("name") == "Press"
          and d.get("intent") == "18:00" and d.get("live") == "19:00"
          and d.get("live_raw") == "2026-07-12T23:00:00Z"
          and d.get("live_timezone") == "America/New_York"
          and d.get("reason") == "mismatch"
          and "6 PM" in d.get("message", "") and "7 PM" in d.get("message", ""))


def _auth(client: TestClient) -> dict:
    return {"X-TDTB-Token": client.app_token}


def s2_setup_gate() -> None:
    """FEEDBACK-24: skeleton runstate never confirms; write paths 409."""
    vault = Path(tempfile.mkdtemp(prefix="feedback28-gate-"))
    app = main_mod.create_app(vault_root=vault)
    tc = TestClient(app)
    tc.app_token = app.state.token

    r_gather = tc.post("/gather", headers=_auth(tc))
    check("S2 /gather materialises a skeleton runstate (200)",
          r_gather.status_code == 200)
    check("S2 skeleton runstate is NOT Day Setup confirmed",
          rs.is_day_setup_confirmed(vault, gather.effective_date(datetime.now()))
          is False)
    inputs = tc.get("/plan-inputs", headers=_auth(tc)).json()
    check("S2 /plan-inputs day_setup_confirmed is False",
          inputs.get("day_setup_confirmed") is False)

    (vault / "P").mkdir(parents=True, exist_ok=True)
    (vault / "P/Garage.md").write_text("---\nassigned: false\n---\nbody\n",
                                       encoding="utf-8")
    (vault / "30 - Daily").mkdir(parents=True, exist_ok=True)
    (vault / "30 - Daily/2026-07-12.md").write_text("# Journal\n",
                                                    encoding="utf-8")
    orig_eff = gather.effective_date
    gather.effective_date = lambda now: TODAY
    try:
        before = {p: p.read_bytes() for p in vault.rglob("*")
                  if p.is_file()}
        r_live = tc.post("/commit?mode=live", headers=_auth(tc),
                         json={"digest": LIVE_DIGEST,
                               "sequence": LIVE_SEQUENCE, "config": {}})
        after = {p: p.read_bytes() for p in vault.rglob("*")
                 if p.is_file()}
        check("S2 live commit fails closed 409 without setup",
              r_live.status_code == 409 and GATE_DETAIL in r_live.json()["detail"],
              f"status={r_live.status_code}")
        check("S2 live commit refusal wrote ZERO vault bytes",
              before == after)
    finally:
        gather.effective_date = orig_eff

    r_apply = tc.post("/runtime-actions", headers=_auth(tc),
                      json={"verb": "complete", "target": "Press"})
    r_undo = tc.post("/runtime-actions/abc/undo", headers=_auth(tc))
    check("S2 runtime apply fails closed 409",
          r_apply.status_code == 409 and GATE_DETAIL in r_apply.json()["detail"])
    check("S2 runtime undo fails closed 409",
          r_undo.status_code == 409 and GATE_DETAIL in r_undo.json()["detail"])

    # Only a successful POST /day-setup unblocks a live commit (fakes).
    orig_eff2 = gather.effective_date
    gather.effective_date = lambda now: TODAY
    orig_shadow = shadow.gather_live_state
    shadow.gather_live_state = _fake_live_state
    try:
        app.state.build_commit_clients = (
            lambda v, cfg: (FakeLiveTodoist(), FakeLiveStore()))
        r_setup = tc.post("/day-setup", json={"anchor": "09:00"},
                          headers=_auth(tc))
        check("S2 POST /day-setup confirms (day_setup_confirmed True)",
              r_setup.status_code == 200
              and r_setup.json().get("day_setup_confirmed") is True)
        r_ok = tc.post("/commit?mode=live", headers=_auth(tc),
                       json={"digest": LIVE_DIGEST,
                             "sequence": LIVE_SEQUENCE, "config": {}})
        check("S2 confirmed setup unblocks live commit with fakes",
              r_ok.status_code == 200 and r_ok.json().get("ok") is True,
              f"status={r_ok.status_code}")
    finally:
        gather.effective_date = orig_eff2
        shadow.gather_live_state = orig_shadow
        app.state.build_commit_clients = None
    tc.close()


def s3_mint_capacity() -> None:
    """FEEDBACK-25: 300 minutes -> 10 blocks/300 min in rows and capacity."""
    items, _, _ = ext.build_schedulable_blocks(
        CFG, {}, MONDAY, "09:00",
        resolved_day_semantics={"effective_allotment_minutes": 300})
    [m] = [i for i in items if i["name"] == "Minting"]
    check("S3 300-min allotment -> aggregate Minting row 10 blocks/300 min",
          m["blocks"] == 10 and m["duration"] == 300,
          f"blocks={m['blocks']} duration={m['duration']}")

    items2, _, _ = ext.build_schedulable_blocks(
        CFG, {"work_allotment_minutes": 300}, MONDAY, "09:00")
    [m2] = [i for i in items2 if i["name"] == "Minting"]
    check("S3 dated 300-min override matches (10/300)",
          m2["blocks"] == 10 and m2["duration"] == 300)

    items3, _, _ = ext.build_schedulable_blocks(CFG, {}, MONDAY, "09:00")
    [m3] = [i for i in items3 if i["name"] == "Minting"]
    check("S3 hardcoded 2-block default survives ONLY without allotment",
          m3["blocks"] == 2 and m3["duration"] == 60,
          f"blocks={m3['blocks']}")

    _t, cap = main_mod._capacity_frame(
        {"Defaults": {"eod": "18:00", "anchor.round_to_minutes": 15,
                      "buffering.off_pct": 0}},
        {"anchor": "09:00", "eod": "18:00", "buffering": "off"},
        [],
        {"est_minutes": 0, "done": 0, "outstanding": 0},
        {"effective_allotment_minutes": 300},
        now=datetime(2026, 7, 13, 9, 0))
    check("S3 capacity frame reserves 10 mint blocks for 300 minutes",
          cap.mint == 10, f"cap.mint={cap.mint}")

    options = ext.mint_session_options(CFG)
    selected = [o["id"] for o in options[:10]]
    items4, _, _ = ext.build_schedulable_blocks(
        CFG,
        {"schedulable": {"minting": {"on": True, "sessions": selected}}},
        MONDAY, "09:00",
        resolved_day_semantics={"effective_allotment_minutes": 300})
    mint = [i for i in items4 if i.get("mint_session")]
    check("S3 10 selected sessions total 10 blocks (sessions win)",
          len(mint) == 10 and sum(i["blocks"] for i in mint) == 10
          and all(i["name"] != "Minting" for i in items4))


def s4_mint_walls() -> None:
    """FEEDBACK-25: selected Mint intervals are hard walls."""
    def _res(rows, optional_items=None, assigned=None, grants=None):
        assigned = assigned if assigned is not None else [
            {"id": r["id"], "zone": r.get("zone") or "any"} for r in rows]
        proposal = {"sequence": rows, "overlap_grants": grants or []}
        return validate_sequence(proposal, assigned, [], CFG,
                                 optional_items=optional_items or [])

    mint = _mint_item()
    r1 = _res([_seq_row("task-1", "08:45", "09:15")], optional_items=[mint])
    check("S4 assigned row overlapping selected Mint is HARD rejection",
          r1.ok is False and any("Mint" in e and "overlap" in e.lower()
                                 for e in r1.hard_errors),
          str(r1.hard_errors))

    grant = {
        "primary_id": "task-1", "companion_id": mint["id"],
        "primary_interval": {"start": "08:45", "end": "09:15"},
        "companion_interval": {"start": "08:30", "end": "09:00"},
        "reason": "explicit contract permission",
        "planning_config_fingerprint": "",
    }
    r2 = _res([_seq_row("task-1", "08:45", "09:15")],
              optional_items=[mint], grants=[grant])
    check("S4 Mint wall is NOT softened by an overlap grant",
          r2.ok is False and any("Mint" in e for e in r2.hard_errors))

    r3 = _res([_seq_row("task-1", "08:00", "08:30")], optional_items=[mint])
    check("S4 back-to-back boundary is allowed",
          r3.ok is True and r3.hard_errors == [])

    r4 = _res(
        [{"id": mint["id"], "start": "08:30", "end": "09:00",
          "zone": "work_hours"},
         _seq_row("task-1", "09:00", "09:30")],
        optional_items=[mint],
        assigned=[{"id": mint["id"], "zone": "work_hours"},
                  {"id": "task-1", "zone": "any"}])
    check("S4 Mint row itself is not a violation",
          r4.ok is True and r4.hard_errors == [])

    # Route-level: /validate-sequence rejects the same overlap shape.
    vault = Path(tempfile.mkdtemp(prefix="feedback28-mint-route-"))
    cfg_file = vault / "00 - META/Skill-Configs/tdtb-bridger.md"
    cfg_file.parent.mkdir(parents=True, exist_ok=True)
    cfg_file.write_text(
        "## Defaults\n"
        "| Key | Value |\n|---|---|\n"
        "| eod | 11:59 PM |\n"
        "| anchor.round_to_minutes | 15 |\n"
        "\n## Template Blocks\n"
        "### Trinoor Hours\n"
        "| Slot | Start | End |\n|---|---|---|\n"
        "| Morning | 8:30 AM | 12:30 PM |\n"
        "| Afternoon | 1:30 PM | 5:00 PM |\n", encoding="utf-8")
    today = gather.effective_date(datetime.now())
    rs.write_runstate(vault, today, rs.build_runstate({
        "anchor": "00:00", "eod": "23:59",
        "schedulable": {"minting": {"on": True,
                                    "sessions": ["mint:morning:08:30"]}},
    }))
    app = main_mod.create_app(vault_root=vault)
    tc = TestClient(app)
    tc.app_token = app.state.token
    r_route = tc.post("/validate-sequence", headers=_auth(tc), json={
        "sequence": [_seq_row("task-1", "08:45", "09:15")],
        "assigned": [{"id": "task-1"}],
        "anchored_blocks": [],
        "config": {"Template Blocks": {"Trinoor Hours": [
            {"Slot": "Morning", "Start": "8:30 AM", "End": "12:30 PM"},
            {"Slot": "Afternoon", "Start": "1:30 PM", "End": "5:00 PM"},
        ]}},
    })
    check("S4 route /validate-sequence rejects overlap",
          r_route.status_code == 200 and r_route.json()["ok"] is False
          and any("Mint" in e for e in r_route.json()["hard_errors"]))
    tc.close()


def s5_mint_readback() -> None:
    """FEEDBACK-26: shifted readback fails; exact readback passes."""
    store = ShiftingStore(start_delta=timedelta(minutes=30),
                          end_delta=timedelta(minutes=30))
    r = commit.write_calendar([_mint_intent()], store, today=TODAY)
    check("S5 shifted interval readback FAILS",
          not r.ok and any("interval mismatch" in f
                           for f in r.verify_failures),
          str(r.verify_failures))
    d = r.verify_details[0] if r.verify_details else {}
    by_field = {m["field"]: m for m in d.get("mismatches", [])}
    check("S5 structured kind=calendar details with canonical ISO mismatches",
          d.get("kind") == "calendar" and d.get("name") == "🟡 Minting 1"
          and set(by_field) == {"start", "end"}
          and by_field["start"]["intent"] == "2026-07-12T13:00:00"
          and by_field["start"]["live"] == "2026-07-12T13:30:00"
          and by_field["end"]["intent"] == "2026-07-12T14:00:00"
          and by_field["end"]["live"] == "2026-07-12T14:30:00")

    r2 = commit.write_calendar([_mint_intent()],
                               ShiftingStore(end_delta=timedelta(minutes=-30)),
                               today=TODAY)
    d2 = r2.verify_details[0] if r2.verify_details else {}
    by2 = {m["field"]: m for m in d2.get("mismatches", [])}
    check("S5 duration shift fails readback (60 vs 30)",
          (not r2.ok) and "duration_min" in by2
          and by2["duration_min"]["intent"] == 60
          and by2["duration_min"]["live"] == 30)

    r3 = commit.write_calendar([_mint_intent()], FakeStore(), today=TODAY)
    check("S5 exact readback PASSES (empty verify)",
          r3.ok and r3.verify_failures == [] and r3.verify_details == [])

    source = {"id": "src1", "title": "🟡 Minting 1",
              "start": datetime(2026, 7, 12, 13, 0),
              "end": datetime(2026, 7, 12, 14, 0),
              "calendar_id": "cal-SOURCE"}
    r4 = commit.write_calendar([_mint_intent()], FakeStore(events=[source]),
                               today=TODAY)
    check("S5 same-title source event never treated as our write",
          r4.ok and FakeStore(events=[source]).created_calls == 0
          and r4.created[0] != "src1" and r4.noops == []
          and r4.touched["🟡 Minting 1"] == r4.created[0],
          f"created={r4.created} noops={r4.noops}")


def s6_trinoor() -> None:
    """FEEDBACK-26: exact-match Step D' policy."""
    manifest = shadow.build_plan_manifest(
        {"assigned": []},
        {"sequence": [{"id": "🟡 Trinoor : Morning", "start": "08:30",
                       "end": "12:30", "backdrop": True}]},
        {})
    [row] = [m for m in manifest if "Trinoor" in m.name]
    check("S6 canonical '🟡 Trinoor : Morning' is Step D'",
          row.step == "D′" and row.system == "calendar")

    config = {"anchored_blocks": [{"id": "Trinoor sync", "on": True}]}
    m2 = shadow.build_plan_manifest(
        {"assigned": []},
        {"sequence": [{"id": "Trinoor sync", "start": "09:00",
                       "end": "10:00", "zone": "any"}]},
        config)
    check("S6 'Trinoor sync' anchored block is Step E, not D'",
          [m.step for m in m2 if m.step == "D′"] == []
          and [m.name for m in m2 if m.step == "E"] == ["Trinoor sync"])

    m3 = shadow.build_plan_manifest(
        {"assigned": []},
        {"sequence": [{"id": "Trinoorish", "start": "09:00",
                       "end": "10:00", "zone": "any"}]},
        {})
    check("S6 'Trinoorish' is plain Step D, not a zone",
          [m.step for m in m3 if m.step == "D′"] == []
          and [m.name for m in m3 if m.step == "D"] == ["Trinoorish"])


class _FakeCalendar:
    def __init__(self, title, identifier):
        self.title = title
        self.identifier = identifier


class _ReadOnlyFakeStore:
    def __init__(self, events, calendars):
        self._events = events
        self._calendars = calendars
        self.calls = []

    def auth_status(self):
        self.calls.append("auth_status")
        return "authorized"

    def calendars(self):
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


def s7_zero_writers() -> None:
    """Zero external writer calls through the read pipeline and fakes."""
    store = _ReadOnlyFakeStore(
        [{"title": "Cooking", "calendar_id": "CAL-COOK",
          "start": datetime(2026, 7, 14, 20, 30),
          "end": datetime(2026, 7, 14, 21, 0)}],
        [_FakeCalendar("Cooking", "CAL-COOK")])
    cfg = {"calendar_capacity_classes": [
        {"BusyCal title": "Cooking", "Class": "fixed"}]}
    blocks, warnings = ext.fetch_calendar_busy(store, cfg, date(2026, 7, 14))
    check("S7 read pipeline ledger is exactly read verbs",
          store.calls == ["auth_status", "calendars", "query_events"]
          and warnings == []
          and {b["Block"]: b["capacity_class"] for b in blocks} ==
          {"Cooking": "fixed"},
          f"ledger={store.calls}")

    for fake in (FakeTodoist(), FakeStore(), ShiftingStore()):
        for attr in ("http", "client", "session", "base_url", "token"):
            assert not hasattr(fake, attr), f"{type(fake).__name__}.{attr}"
    check("S7 write fakes never expose a live provider handle", True)


def main() -> int:
    s1_press_due()
    s2_setup_gate()
    s3_mint_capacity()
    s4_mint_walls()
    s5_mint_readback()
    s6_trinoor()
    s7_zero_writers()

    passed = sum(1 for r in RESULTS if r["pass"])
    total = len(RESULTS)
    print(f"\nSCRATCH RESULT: {passed}/{total} assertions passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
