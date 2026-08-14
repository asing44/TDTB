#!/usr/bin/env python3
"""Capture sanitized API contract fixtures for the cockpit frontend (T5).

Runs the REAL FastAPI routes (TestClient) against a synthetic vault + fake
external clients, and dumps each endpoint's actual JSON response to
``frontend/src/adapters/contract-fixtures/``. Every shape in those files came
out of the production route code — nothing hand-authored — with fixture data
sanitized by construction (fake names, no real vault content).

Billed safety: /sequence's judgment layer is monkeypatched to a canned
proposal — the route's validation/injection/response code still runs, the
Agent SDK is never invoked, nothing is billed.

Run with the app venv:  ../app/.venv/bin/python scripts/capture_contract_fixtures.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path

FRONTEND = Path(__file__).resolve().parent.parent
APP = FRONTEND.parent / "app"
sys.path.insert(0, str(APP))
sys.path.insert(0, str(APP / "gather"))

from fastapi.testclient import TestClient  # noqa: E402

import external_sources  # noqa: E402
import judgment  # noqa: E402
import main as main_mod  # noqa: E402
import orchestrate  # noqa: E402
import runstate  # noqa: E402
import tdtb_gather as gather  # noqa: E402

OUT = FRONTEND / "src" / "adapters" / "contract-fixtures"

CONFIG = """\
---
description: contract-fixture config
last_updated: 2026-07-01
---

# TDTB Bridger Config

## Defaults

| Key | Value    |
| --- | -------- |
| eod | 11:45 PM |
| work_allotment_minutes | 180 |

## Day Presets

| Name | Days | Zones | Work Allotment (min) | Default |
|------|------|-------|----------------------|---------|
| Workday | workdays |  | 240 | |
| Weekend | weekends |  | 0 | |
| Default | daily |  |  | true |

## Overlap Permissions

Default for everything is no-overlap.

## Anchored Lifestyle Blocks

| Block           | Type   | Start    | End      | Duration | Days  | overlap_allowed |
| --------------- | ------ | -------- | -------- | -------- | ----- | --------------- |
| Morning Routine | hard   | 7:45 AM  | —        | 80m      | daily | no              |
| Foods Dinner    | window | 6:00 PM  | 8:30 PM  | 60m      | daily | no              |
| Live            | window | 12:00 PM | 8:00 PM  | 30m      | daily | yes             |

## Calendar Titles

| Logical name | BusyCal title | Role        |
| ------------ | ------------- | ----------- |
| blocks       | ⬜ Blocks      | schedulable |

## Presets

| Name | Type | Blocks | Priority |
|------|------|--------|----------|
| Make | interval | 2 | 2 |
"""


class FakeTodoist:
    def __init__(self, by_query):
        self.by_query = by_query

    def get_filter_tasks(self, query, limit=None):
        return self.by_query.get(query, [])


class FakeStore:
    def __init__(self, events):
        self.events = events

    def query_events(self, start, end, calendar_ids=None):
        return self.events

    def calendars(self):
        return [{"id": "CAL-X", "title": "Fixture"}]


class DeadStore:
    """Unauthorized store → real calendar degrade warning path."""

    def auth_status(self):
        return "denied"

    def query_events(self, start, end, calendar_ids=None):
        return []

    def calendars(self):
        return []


def build_vault(root: Path) -> Path:
    v = root / "vault"
    v.mkdir()
    cfg = v / "00 - META/Skill-Configs/tdtb-bridger.md"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(CONFIG, encoding="utf-8")
    proj = v / "50 - Operations/Projects"
    proj.mkdir(parents=True)
    (proj / "Sample Project.md").write_text(
        "---\ntype: project\nassigned: true\nurgency: 3-high\ndeadline: 2026-07-19\n---\nbody\n",
        encoding="utf-8",
    )
    (proj / "Make.md").write_text(
        "---\ntype: project\nassigned: true\n---\nbody\n", encoding="utf-8"
    )
    (proj / "Sample Press.md").write_text(
        "---\ntype: press\nassigned: true\nduration_min: 75\n---\nbody\n",
        encoding="utf-8",
    )
    hab = v / "00 - META/Habituals"
    hab.mkdir(parents=True)
    (hab / "Water.md").write_text(
        "---\ntitle: Water\ntype: habit\nentries:\n  - 2020-01-01\n---\n",
        encoding="utf-8",
    )
    return v


def todoist_fake() -> FakeTodoist:
    return FakeTodoist({
        external_sources.ASSIGNED_QUERY_FALLBACK: [
            {"id": "9001", "content": "Sample Todoist Task", "priority": 3,
             "due": {"date": str(TODAY)},
             "duration": {"unit": "minute", "amount": 90}, "labels": []},
        ],
        external_sources.QUICK_QUERY_FALLBACK: [],
    })


def store_fake() -> FakeStore:
    return FakeStore([{
        "id": "EV-1",
        "title": "Sample Meeting",
        "start": datetime(TODAY.year, TODAY.month, TODAY.day, 9, 15),
        "end": datetime(TODAY.year, TODAY.month, TODAY.day, 9, 45),
        "calendar_id": "CAL-X",
    }])


# Obs 286: fixtures must be reproducible — wall-clock drift is not contract
# drift. TDTB_FIXTURE_DATE=YYYY-MM-DD freezes the logical day (routes see a
# frozen 08:00 clock on that date); unset keeps live-clock behavior for ad-hoc
# runs, but committed regenerations should pass the previous fixtures' date so
# only intended contract changes diff.
import os as _os

_FIXTURE_DATE = _os.environ.get("TDTB_FIXTURE_DATE")
if _FIXTURE_DATE:
    _fy, _fm, _fd = (int(p) for p in _FIXTURE_DATE.split("-"))

    class _FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):  # noqa: D102
            return cls(_fy, _fm, _fd, 8, 0, 0, tzinfo=tz)

    main_mod.datetime = _FrozenDateTime
    TODAY = gather.effective_date(_FrozenDateTime.now())
else:
    TODAY = gather.effective_date(datetime.now())


def dump(name: str, payload) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{name}.json").write_text(
        json.dumps(payload, indent=2, sort_keys=False, default=str) + "\n",
        encoding="utf-8",
    )
    print(f"  {name}.json")


def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="tdtb-contract-"))
    vault = build_vault(tmp)
    app = main_mod.create_app(vault_root=vault)
    app.state.build_read_clients = lambda v, cfg: (todoist_fake(), store_fake())
    client = TestClient(app)
    token = client.get("/session-token").json()["token"]
    auth = {"X-TDTB-Token": token}

    print("capturing:")

    # -- reads ---------------------------------------------------------------
    dump("plan-inputs", client.get("/plan-inputs").json())
    dump("plan-inputs-allotment-omitted", client.get("/plan-inputs").json())
    dump("billed-ledger", client.get("/billed-ledger").json())
    dump("capacity-preview", client.get(
        "/capacity-preview",
        params={"day_setup": json.dumps({"buffering": "standard"}),
                "selected": json.dumps(["1h30m", "1h", None, 0])},
    ).json())
    dump("capacity-preview-allotment-omitted", client.get("/capacity-preview").json())

    # -- day-setup (persists to the synthetic vault's runstate note) ---------
    r = client.post("/day-setup", headers=auth, json={
        "anchor": "07:30", "eod": "23:00", "buffering": "standard",
        "day_preset": "Workday", "work_allotment_minutes": 240,
        "anchored": [{"id": "Live", "on": True, "skip_today": False, "time": None}],
        "captures": {"intention": "Sample intention",
                     "megan_nicety": "Sample nicety",
                     "stoic_intention": "Sample stoic"},
    })
    dump("day-setup-response", r.json())
    # Re-read: plan-inputs with a persisted day_setup echo (confirmed shape).
    dump("plan-inputs-with-setup", client.get("/plan-inputs").json())

    # -- T18b read-contract variants ----------------------------------------
    # These are all real route responses from the same sanitized vault. The
    # frontend intentionally does not project day_semantics yet; T18f owns
    # that consumer work.
    client.post("/day-setup", headers=auth, json={
        "day_preset": "Weekend", "work_allotment_minutes": None,
    })
    dump("plan-inputs-day-preset", client.get("/plan-inputs").json())
    dump("capacity-preview-day-preset", client.get("/capacity-preview").json())

    client.post("/day-setup", headers=auth, json={
        "day_preset": None, "work_allotment_minutes": None,
    })
    dump("plan-inputs-allotment-null", client.get("/plan-inputs").json())
    dump("capacity-preview-allotment-null", client.get("/capacity-preview").json())

    client.post("/day-setup", headers=auth, json={
        "day_preset": "Workday", "work_allotment_minutes": 0,
    })
    dump("plan-inputs-allotment-zero", client.get("/plan-inputs").json())
    dump("capacity-preview-allotment-zero", client.get("/capacity-preview").json())

    cfg = vault / "00 - META/Skill-Configs/tdtb-bridger.md"
    cfg.write_text(
        cfg.read_text(encoding="utf-8").replace(
            "| Workday | workdays |  | 240 | |",
            "| Workday | workdays |  | 300 | |",
        ),
        encoding="utf-8",
    )
    dump("plan-inputs-fingerprint-changed", client.get("/plan-inputs").json())
    dump("capacity-preview-fingerprint-changed", client.get("/capacity-preview").json())

    cfg.write_text(
        "## Defaults\n| Key | Value |\n|---|---|\n| work_allotment_minutes | 180 |\n\n"
        "## Day Presets\n| Name | Days | Zones | Work Allotment (min) | Default |\n"
        "|---|---|---|---|---|\n| Workday | workdays |  | 240 | |\n",
        encoding="utf-8",
    )
    dump("plan-inputs-malformed", client.get("/plan-inputs").json())
    dump("capacity-preview-malformed", client.get("/capacity-preview").json())

    # Restore the valid sanitized config before the remaining route fixtures.
    cfg.write_text(CONFIG, encoding="utf-8")

    # Degraded sources: real calendar/todoist warning strings.
    app.state.build_read_clients = lambda v, cfg: (None, DeadStore())
    dump("plan-inputs-degraded", client.get("/plan-inputs").json())
    app.state.build_read_clients = lambda v, cfg: (todoist_fake(), store_fake())

    # -- sequence (judgment monkeypatched — route code real, nothing billed) --
    inputs = client.get("/plan-inputs").json()
    assigned = inputs["digest"]["assigned"]
    seq_body = {
        "assigned": [{**i, "id": i["name"]} for i in assigned],
        "config": inputs["config"],
        "anchored_blocks": inputs["anchored_blocks"],
    }

    real_propose = judgment.propose_sequence

    def canned_propose(assigned_arg, config, anchored, ctx):
        rows = []
        start_h, start_m = 10, 0
        for item in assigned_arg:
            blocks = item.get("blocks")
            n = blocks if isinstance(blocks, (int, float)) and blocks > 0 else 1
            mins = int(n * 30)
            end_m = start_h * 60 + start_m + mins
            rows.append({
                "id": item.get("name"),
                "start": f"{start_h:02d}:{start_m:02d}",
                "end": f"{end_m // 60:02d}:{end_m % 60:02d}",
                "zone": None,
            })
            start_h, start_m = end_m // 60, end_m % 60
        return {"sequence": rows, "warnings": []}

    judgment.propose_sequence = canned_propose
    try:
        r = client.post("/sequence", headers=auth, json=seq_body)
        assert r.status_code == 200, r.text
        dump("sequence-ok", r.json())

        # 422: canned proposal that overlaps Morning Routine (hard wall).
        def bad_propose(assigned_arg, config, anchored, ctx):
            return {"sequence": [
                {"id": a.get("name"), "start": "07:50", "end": "08:20", "zone": None}
                for a in assigned_arg[:1]
            ], "warnings": []}

        judgment.propose_sequence = bad_propose
        r = client.post("/sequence", headers=auth, json=seq_body)
        assert r.status_code == 422, r.text
        dump("sequence-422", r.json())
    finally:
        judgment.propose_sequence = real_propose

    # 429: exhaust the persistent ledger, then hit the real gate.
    runstate.update_runstate(vault, TODAY, {"billed_calls": main_mod.BILLED_CAP})
    r = client.post("/sequence", headers=auth, json=seq_body)
    assert r.status_code == 429, r.text
    dump("sequence-429", r.json())
    dump("billed-ledger-spent", client.get("/billed-ledger").json())
    runstate.update_runstate(vault, TODAY, {"billed_calls": 0})

    # -- validate-sequence ---------------------------------------------------
    ok_rows = [r_ for r_ in json.loads((OUT / "sequence-ok.json").read_text())["sequence"]
               if r_.get("zone") is None]
    val_body = {"sequence": ok_rows, **seq_body}
    r = client.post("/validate-sequence", headers=auth, json=val_body)
    assert r.status_code == 200, r.text
    dump("validate-ok", r.json())

    # A malformed reverse interval is a hard structural error. Ordinary wall
    # overlap and before-anchor placement are acceptable defects (T18d).
    bad_rows = [{**ok_rows[0], "start": "06:00", "end": "05:30"}] + ok_rows[1:]
    r = client.post("/validate-sequence", headers=auth, json={**val_body, "sequence": bad_rows})
    dump("validate-fail", r.json())

    # -- shadow commit (writes nothing; live state from the synthetic vault) --
    import shadow as shadow_mod

    real_gather_live = shadow_mod.gather_live_state
    shadow_mod.gather_live_state = lambda config, vault_root: {
        "todoist": {"tasks": []},
        "calendar": {"events": [], "unavailable": True},
        "vault_frontmatter": {
            "50 - Operations/Projects/Sample Project.md": {"type": "project", "assigned": True},
        },
        "unavailable_surfaces": ["calendar"],
    }
    try:
        r = client.post("/commit", headers=auth, params={"mode": "shadow"}, json={
            "digest": inputs["digest"],
            "sequence": {"sequence": ok_rows},
            "config": inputs["config"],
        })
        assert r.status_code == 200, r.text
        dump("shadow-diff", r.json())
    finally:
        shadow_mod.gather_live_state = real_gather_live

    # -- live commit report shapes (real orchestrate code, fake surfaces) -----
    report_ok = orchestrate.run_orchestrated(
        [], todoist=None, store=None, vault_root=vault,
        plan_body="- 10:00–11:30 Sample Project", today=TODAY,
        persist_ledger=False,
    )
    dump("commit-live-ok", report_ok)

    import commit as commit_mod
    intents = [commit_mod.WriteIntent(
        "B", "todoist", "create", "Sample Todoist Task",
        due_time="10:00", duration_min=60,
    )]
    report_partial = orchestrate.run_orchestrated(
        intents, todoist=None, store=None, vault_root=vault,
        plan_body="", today=TODAY, persist_ledger=False,
    )
    dump("commit-live-partial", report_partial)

    # -- 409 single-flight (real lock, real error shape) ----------------------
    app.state.live_commit_lock.acquire()
    try:
        r = client.post("/commit", headers=auth, params={"mode": "live"}, json={
            "digest": inputs["digest"],
            "sequence": {"sequence": ok_rows},
            "config": inputs["config"],
        })
        assert r.status_code == 409, r.text
        dump("commit-409", r.json())
    finally:
        app.state.live_commit_lock.release()

    # -- auth error shape ------------------------------------------------------
    r = client.post("/sequence", json=seq_body)
    assert r.status_code == 403
    dump("error-403", r.json())

    print(f"done → {OUT}")


if __name__ == "__main__":
    main()
