"""FEEDBACK-27 (2026-08-14): write-path and Mint reliability fixtures.

Deterministic, fake-only fixtures covering the reported write failure and
Mint defects. Each contract from FEEDBACK-23/24/25/26 is pinned here as ONE
named fixture story so the backend and frontend regression suites share it:

- Press due fixture (FEEDBACK-23): intent 19:00 versus Todoist 23:00Z /
  19:00 local. Reproduces the ORIGINAL pre-normalization mismatch (raw UTC
  wall clock 23:00 != intent 19:00), then proves the FEEDBACK-23 normalized
  reading (23:00Z through America/New_York == 19:00 local) verifies clean.
- Setup-gate fixture (FEEDBACK-24): a skeleton runstate materialised by
  /gather is NOT Day Setup confirmation; /commit?mode=live and
  /runtime-actions apply/undo fail closed (409) with zero vault bytes
  written; POST /day-setup is the only unlock.
- Mint-capacity fixture (FEEDBACK-25): a configured 300-minute Mint
  allotment yields 10 blocks / 300 minutes and the same capacity — never
  the hardcoded 2-block _SCHED_DEFAULTS fallback (that survives only for
  callers with NO allotment context).
- Mint-overlap fixture (FEEDBACK-25): an assigned task overlapping a
  selected Mint session is a HARD rejection (no overlap-grant escape);
  back-to-back touch is allowed; the route rejects the same shape.
- Mint-readback fixture (FEEDBACK-26): a shifted Mint interval readback
  fails with structured kind='calendar' mismatch details; an exact
  readback passes.
- Trinoor classification fixture (FEEDBACK-26): exact-match zone policy —
  '[🟡 ]Trinoor : <slot>' is Step D′; 'Trinoor sync' / 'Trinoorish' are not.

Safety: every fixture drives in-memory fakes; the read pipeline records only
read verbs and the writer fakes never expose a live handle. No live POST,
external write, restart, or commit.
"""
from __future__ import annotations

import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))
import commit  # noqa: E402
import external_sources as ext  # noqa: E402
import main as main_mod  # noqa: E402
import runstate as rs  # noqa: E402
import shadow  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent.parent / "gather"))
import tdtb_gather as gather  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent))
from test_commit import FakeStore, FakeTodoist, ShiftingStore  # noqa: E402
from test_main_api import (  # noqa: E402
    FakeLiveStore,
    FakeLiveTodoist,
    LIVE_DIGEST,
    LIVE_SEQUENCE,
    _fake_live_state,
)

from sequence import validate_sequence  # noqa: E402

GATE_DETAIL = "Day Setup not confirmed"
TODAY = date(2026, 7, 12)
MONDAY = date(2026, 7, 13)
CFG = {
    "Template Blocks": {"Trinoor Hours": [
        {"Slot": "Morning", "Start": "8:30 AM", "End": "12:30 PM"},
        {"Slot": "Afternoon", "Start": "1:30 PM", "End": "5:00 PM"},
    ]},
}


def _seq_row(id_, start, end, zone="any"):
    return {"id": id_, "start": start, "end": end, "zone": zone}


def _mint_item(id_="Mint Morning · 08:30", start="08:30", end="09:00"):
    return {
        "id": id_, "name": id_, "zone": "work_hours", "source": "schedulable",
        "mint_session": True, "mint_session_id": f"mint:morning:{start}",
        "placement_window": {"start": start, "end": end},
        "calendar_class": "mint",
    }


# ---------------------------------------------------------------------------
# Press due fixture — FEEDBACK-23 (Todoist time normalization)
# ---------------------------------------------------------------------------

class TestPressDueFixture:
    """The reported Press write failure compared intent 19:00 against the
    raw UTC wall clock 23:00 of a fixed Todoist due. This fixture reproduces
    that mismatch BEFORE normalization and proves the FEEDBACK-23 reading
    makes the same live task verify clean."""

    def _due_task(self, task_id, name, due):
        return {"id": task_id, "content": name, "due": due}

    def _noop_intent(self, name, hhmm, task_id="t42"):
        return commit.WriteIntent(
            step="A", surface="todoist", op="noop", name=name,
            task_id=task_id, due_time=hhmm, duration_min=60,
        )

    def test_original_press_mismatch_repro_before_normalization(self):
        # Pre-FEEDBACK-23 `_todoist_due_hhmm` read the fixed due's UTC wall
        # clock directly: '2026-07-12T23:00:00Z' -> '23:00', which does NOT
        # equal the 19:00 local intent — the exact reported failure.
        task = self._due_task(
            "t42", "Press",
            {"date": "2026-07-12T23:00:00Z", "timezone": "America/New_York"},
        )
        raw_wall = str(task["due"]["date"]).split("T")[1][:5]
        assert raw_wall == "23:00"
        assert raw_wall != "19:00"  # the original mismatch before normalization

        # FEEDBACK-23 normalized reading: 23:00Z through the due timezone is
        # 19:00 America/New_York — same instant, canonical local HH:MM.
        reading = commit._todoist_due_reading(task)
        assert reading.local_hhmm == "19:00"
        assert reading.error is None

        r = commit.write_todoist(
            [self._noop_intent("Press", "19:00")], FakeTodoist([task]))
        assert r.ok, r.error
        assert r.verify_failures == []

    def test_equivalent_2300z_1900_local_encoding_passes(self):
        client = FakeTodoist([self._due_task(
            "t42", "Press", {"date": "2026-07-12T23:00:00Z",
                             "timezone": "America/New_York"})])
        r = commit.write_todoist([self._noop_intent("Press", "19:00")], client)
        assert r.ok, r.error
        assert r.verify_failures == []
        assert commit._todoist_due_hhmm(client.get_task("t42")) == "19:00"

    def test_floating_local_due_verifies_locally(self):
        # A floating offset-less due wall time is the user's local time — a
        # timezone field must NOT shift it.
        client = FakeTodoist([self._due_task(
            "t42", "Walk", {"date": "2026-07-12T19:00:00",
                            "timezone": "America/New_York"})])
        r = commit.write_todoist([self._noop_intent("Walk", "19:00")], client)
        assert r.ok, r.error
        assert r.verify_failures == []

    def test_true_different_instant_fails_with_12h_message(self):
        # A genuinely different instant (floating 23:00 local vs intent
        # 19:00) remains a required failure — 12h text only, no raw 24h.
        client = FakeTodoist([self._due_task(
            "t42", "Press", {"date": "2026-07-12T23:00:00"})])
        r = commit.write_todoist([self._noop_intent("Press", "19:00")], client)
        assert not r.ok
        msg = r.verify_failures[0]
        assert "due mismatch" in msg
        assert "7 PM" in msg and "11 PM" in msg
        assert "19:00" not in msg and "23:00" not in msg

    def test_missing_timezone_on_utc_due_fails_closed(self):
        client = FakeTodoist([self._due_task(
            "t42", "Press", {"date": "2026-07-12T23:00:00Z"})])
        r = commit.write_todoist([self._noop_intent("Press", "19:00")], client)
        assert not r.ok
        assert any("missing timezone" in f for f in r.verify_failures)

    def test_structured_press_detail_keeps_canonical_values(self):
        client = FakeTodoist([self._due_task(
            "t42", "Press", {"date": "2026-07-12T23:00:00Z",
                             "timezone": "America/New_York"})])
        r = commit.write_todoist([self._noop_intent("Press", "18:00")], client)
        assert not r.ok
        [d] = r.verify_details
        assert d["kind"] == "due"
        assert d["name"] == "Press"
        assert d["intent"] == "18:00"
        assert d["live"] == "19:00"                      # normalized local 24h
        assert d["live_raw"] == "2026-07-12T23:00:00Z"   # canonical ISO
        assert d["live_timezone"] == "America/New_York"  # canonical tz
        assert d["reason"] == "mismatch"
        assert "6 PM" in d["message"] and "7 PM" in d["message"]


# ---------------------------------------------------------------------------
# Setup-gate fixture — FEEDBACK-24 (Day Setup confirmation is explicit)
# ---------------------------------------------------------------------------

@pytest.fixture
def vault(tmp_path: Path) -> Path:
    v = tmp_path / "vault-root"
    v.mkdir()
    return v


@pytest.fixture
def client(vault) -> TestClient:
    app = main_mod.create_app(vault_root=vault)
    c = TestClient(app)
    c.app_token = app.state.token
    return c


def _auth(client: TestClient) -> dict:
    return {"X-TDTB-Token": client.app_token}


def _today() -> date:
    return gather.effective_date(datetime.now())


class TestSetupGateBlocksExternalWrites:
    """Skeleton runstate (gather materialisation, Drop, ledger, sequence
    side-effects) never confirms Day Setup; write paths fail closed (409)
    until a real POST /day-setup for the current planning day."""

    def test_skeleton_runstate_from_gather_is_not_confirmed(self, client, vault):
        r = client.post("/gather", headers=_auth(client))
        assert r.status_code == 200
        assert rs.is_day_setup_confirmed(vault, _today()) is False
        inputs = client.get("/plan-inputs", headers=_auth(client)).json()
        assert inputs["day_setup_confirmed"] is False

    def test_live_commit_fails_closed_with_zero_vault_bytes(
        self, client, vault, monkeypatch
    ):
        (vault / "P").mkdir(parents=True, exist_ok=True)
        (vault / "P/Garage.md").write_text(
            "---\nassigned: false\n---\nbody\n", encoding="utf-8")
        (vault / "30 - Daily").mkdir(parents=True, exist_ok=True)
        (vault / "30 - Daily/2026-07-12.md").write_text(
            "# Journal\n", encoding="utf-8")
        monkeypatch.setattr(gather, "effective_date",
                            lambda now: date(2026, 7, 12))
        before = {p: p.read_bytes() for p in vault.rglob("*") if p.is_file()}
        r = client.post(
            "/commit?mode=live", headers=_auth(client),
            json={"digest": LIVE_DIGEST, "sequence": LIVE_SEQUENCE,
                  "config": {}},
        )
        assert r.status_code == 409
        assert GATE_DETAIL in r.json()["detail"]
        after = {p: p.read_bytes() for p in vault.rglob("*") if p.is_file()}
        assert before == after  # external write path wrote NOTHING

    def test_runtime_actions_fail_closed_without_setup(self, client, vault):
        apply = client.post("/runtime-actions", headers=_auth(client),
                            json={"verb": "complete", "target": "Press"})
        assert apply.status_code == 409
        assert GATE_DETAIL in apply.json()["detail"]
        undo = client.post("/runtime-actions/abc/undo", headers=_auth(client))
        assert undo.status_code == 409
        assert GATE_DETAIL in undo.json()["detail"]

    def test_confirmed_setup_unblocks_live_commit_with_fakes(
        self, client, vault, monkeypatch
    ):
        (vault / "P").mkdir(parents=True, exist_ok=True)
        (vault / "P/Garage.md").write_text(
            "---\nassigned: false\n---\nbody\n", encoding="utf-8")
        (vault / "30 - Daily").mkdir(parents=True, exist_ok=True)
        (vault / "30 - Daily/2026-07-12.md").write_text(
            "# Journal\n", encoding="utf-8")
        monkeypatch.setattr(gather, "effective_date",
                            lambda now: date(2026, 7, 12))
        monkeypatch.setattr(shadow, "gather_live_state", _fake_live_state)
        client.app.state.build_commit_clients = (
            lambda v, cfg: (FakeLiveTodoist(), FakeLiveStore())
        )
        setup = client.post("/day-setup", json={"anchor": "09:00"},
                            headers=_auth(client))
        assert setup.status_code == 200
        assert setup.json()["day_setup_confirmed"] is True
        r = client.post(
            "/commit?mode=live", headers=_auth(client),
            json={"digest": LIVE_DIGEST, "sequence": LIVE_SEQUENCE,
                  "config": {}},
        )
        assert r.status_code == 200
        assert r.json()["ok"] is True


# ---------------------------------------------------------------------------
# Mint-capacity fixture — FEEDBACK-25 (300 minutes, no 2-block fallback)
# ---------------------------------------------------------------------------

class TestMintCapacity300:
    """Configured 300-minute Mint allotment produces 10 blocks / 300 minutes
    in the schedulable row AND the capacity frame — never the hardcoded
    2-block _SCHED_DEFAULTS fallback (that survives only for callers with no
    allotment context)."""

    def test_300_minute_allotment_yields_ten_blocks_no_2_block_fallback(self):
        items, _, _ = ext.build_schedulable_blocks(
            CFG, {}, MONDAY, "09:00",
            resolved_day_semantics={"effective_allotment_minutes": 300},
        )
        [m] = [i for i in items if i["name"] == "Minting"]
        assert m["blocks"] == 10
        assert m["duration"] == 300

    def test_300_minute_dated_override_matches_capacity(self):
        items, _, _ = ext.build_schedulable_blocks(
            CFG, {"work_allotment_minutes": 300}, MONDAY, "09:00",
        )
        [m] = [i for i in items if i["name"] == "Minting"]
        assert m["blocks"] == 10
        assert m["duration"] == 300

    def test_hardcoded_2_block_default_only_without_allotment_context(self):
        # The historical fallback is pinned ONLY for no-allotment callers so
        # a 300-minute configuration can never silently fall back to it.
        items, _, _ = ext.build_schedulable_blocks(CFG, {}, MONDAY, "09:00")
        [m] = [i for i in items if i["name"] == "Minting"]
        assert m["blocks"] == 2

    def test_capacity_frame_reserves_ten_mint_blocks_for_300_minutes(self):
        _time, cap = main_mod._capacity_frame(
            {"Defaults": {"eod": "18:00", "anchor.round_to_minutes": 15,
                          "buffering.off_pct": 0}},
            {"anchor": "09:00", "eod": "18:00", "buffering": "off"},
            [],
            {"est_minutes": 0, "done": 0, "outstanding": 0},
            {"effective_allotment_minutes": 300},
            now=datetime(2026, 7, 13, 9, 0),
        )
        assert cap.mint == 10  # capacity reserves allotment/30 blocks

    def test_ten_selected_sessions_total_equals_300_minute_capacity(self):
        options = ext.mint_session_options(CFG)
        selected = [o["id"] for o in options[:10]]
        items, _, _ = ext.build_schedulable_blocks(
            CFG,
            {"schedulable": {"minting": {"on": True, "sessions": selected}}},
            MONDAY, "09:00",
            resolved_day_semantics={"effective_allotment_minutes": 300},
        )
        mint = [i for i in items if i.get("mint_session")]
        assert len(mint) == 10
        assert sum(i["blocks"] for i in mint) == 10  # 300 / 30
        assert all(i["name"] != "Minting" for i in items)  # sessions win


# ---------------------------------------------------------------------------
# Mint-overlap fixture — FEEDBACK-25 (selected Mint intervals are hard walls)
# ---------------------------------------------------------------------------

class TestMintOverlapRejected:
    def _res(self, rows, optional_items=None, assigned=None, grants=None):
        assigned = assigned if assigned is not None else [
            {"id": r["id"], "zone": r.get("zone") or "any"} for r in rows
        ]
        proposal = {"sequence": rows, "overlap_grants": grants or []}
        return validate_sequence(
            proposal, assigned, [], CFG,
            optional_items=optional_items or [],
        )

    def test_assigned_task_overlapping_selected_mint_is_hard_rejection(self):
        mint = _mint_item()
        result = self._res(
            [_seq_row("task-1", "08:45", "09:15")],
            optional_items=[mint],
        )
        assert result.ok is False
        assert any(
            "Mint" in e and "overlap" in e.lower() for e in result.hard_errors
        )

    def test_mint_wall_is_not_softened_by_an_overlap_grant(self):
        mint = _mint_item()
        grant = {
            "primary_id": "task-1",
            "companion_id": mint["id"],
            "primary_interval": {"start": "08:45", "end": "09:15"},
            "companion_interval": {"start": "08:30", "end": "09:00"},
            "reason": "explicit contract permission",
            "planning_config_fingerprint": "",
        }
        result = self._res(
            [_seq_row("task-1", "08:45", "09:15")],
            optional_items=[mint],
            grants=[grant],
        )
        assert result.ok is False
        assert any("Mint" in e for e in result.hard_errors)

    def test_back_to_back_boundary_is_allowed(self):
        mint = _mint_item()
        result = self._res(
            [_seq_row("task-1", "08:00", "08:30")],
            optional_items=[mint],
        )
        assert result.ok is True
        assert result.hard_errors == []

    def test_mint_row_itself_is_not_a_violation(self):
        mint = _mint_item()
        result = self._res(
            [
                {"id": mint["id"], "start": "08:30", "end": "09:00",
                 "zone": "work_hours"},
                _seq_row("task-1", "09:00", "09:30"),
            ],
            optional_items=[mint],
            assigned=[{"id": mint["id"], "zone": "work_hours"},
                      {"id": "task-1", "zone": "any"}],
        )
        assert result.ok is True
        assert result.hard_errors == []


def _write_cfg(vault: Path) -> None:
    cfg = vault / "00 - META/Skill-Configs/tdtb-bridger.md"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(
        "## Defaults\n"
        "| Key | Value |\n|---|---|\n"
        "| eod | 11:59 PM |\n"
        "| anchor.round_to_minutes | 15 |\n"
        "\n## Template Blocks\n"
        "### Trinoor Hours\n"
        "| Slot | Start | End |\n|---|---|---|\n"
        "| Morning | 8:30 AM | 12:30 PM |\n"
        "| Afternoon | 1:30 PM | 5:00 PM |\n",
        encoding="utf-8",
    )


def _seed_mint_session(vault) -> None:
    today = gather.effective_date(datetime.now())
    rs.write_runstate(vault, today, rs.build_runstate({
        "anchor": "00:00", "eod": "23:59",
        "schedulable": {"minting": {
            "on": True,
            "sessions": ["mint:morning:08:30"],
        }},
    }))


class TestMintOverlapRoute:
    def test_route_rejects_assigned_row_over_selected_mint(self, client, vault):
        _write_cfg(vault)
        _seed_mint_session(vault)
        r = client.post("/validate-sequence", headers=_auth(client), json={
            "sequence": [_seq_row("task-1", "08:45", "09:15")],
            "assigned": [{"id": "task-1"}],
            "anchored_blocks": [],
            "config": {"Template Blocks": {"Trinoor Hours": [
                {"Slot": "Morning", "Start": "8:30 AM", "End": "12:30 PM"},
                {"Slot": "Afternoon", "Start": "1:30 PM", "End": "5:00 PM"},
            ]}},
        })
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is False
        assert any("Mint" in e for e in body["hard_errors"])

    def test_route_allows_clean_row_beside_selected_mint(self, client, vault):
        _write_cfg(vault)
        _seed_mint_session(vault)
        r = client.post("/validate-sequence", headers=_auth(client), json={
            "sequence": [_seq_row("task-1", "09:00", "09:30")],
            "assigned": [{"id": "task-1"}],
            "anchored_blocks": [],
            "config": {"Template Blocks": {"Trinoor Hours": [
                {"Slot": "Morning", "Start": "8:30 AM", "End": "12:30 PM"},
                {"Slot": "Afternoon", "Start": "1:30 PM", "End": "5:00 PM"},
            ]}},
        })
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert body["hard_errors"] == []


# ---------------------------------------------------------------------------
# Mint-readback fixture — FEEDBACK-26 (full interval identity)
# ---------------------------------------------------------------------------

class TestMintReadback:
    """A Mint calendar write is verified by title + calendar_id + start +
    end + duration. The FEEDBACK-26 repro (intended 13:00-14:00 reads back
    13:30-14:30) FAILS with structured details; an exact readback PASSES."""

    def _mint_intent(self, name="🟡 Minting 1", start_h="13", start_m=0,
                     end_h="14", end_m=0, **kw):
        base = dict(step="D", surface="calendar", op="create", name=name,
                    calendar_id="cal-blocks-1", due_time=f"{start_h}:{start_m:02d}",
                    start=datetime(2026, 7, 12, int(start_h), start_m),
                    end=datetime(2026, 7, 12, int(end_h), end_m))
        base.update(kw)
        return commit.WriteIntent(**base)

    def test_shifted_mint_readback_fails_with_structured_details(self):
        store = ShiftingStore(start_delta=timedelta(minutes=30),
                              end_delta=timedelta(minutes=30))
        r = commit.write_calendar([self._mint_intent()], store, today=TODAY)
        assert not r.ok
        assert any("interval mismatch" in f for f in r.verify_failures)
        [d] = r.verify_details
        assert d["kind"] == "calendar"
        assert d["name"] == "🟡 Minting 1"
        by_field = {m["field"]: m for m in d["mismatches"]}
        assert set(by_field) == {"start", "end"}
        assert by_field["start"]["intent"] == "2026-07-12T13:00:00"
        assert by_field["start"]["live"] == "2026-07-12T13:30:00"
        assert by_field["end"]["intent"] == "2026-07-12T14:00:00"
        assert by_field["end"]["live"] == "2026-07-12T14:30:00"

    def test_duration_shift_fails_readback(self):
        store = ShiftingStore(end_delta=timedelta(minutes=-30))
        r = commit.write_calendar([self._mint_intent()], store, today=TODAY)
        assert not r.ok
        [d] = r.verify_details
        by_field = {m["field"]: m for m in d["mismatches"]}
        assert "duration_min" in by_field
        assert by_field["duration_min"]["intent"] == 60
        assert by_field["duration_min"]["live"] == 30

    def test_exact_mint_readback_passes(self):
        store = FakeStore()
        r = commit.write_calendar([self._mint_intent()], store, today=TODAY)
        assert r.ok, r.error
        assert r.verify_failures == []
        assert r.verify_details == []

    def test_source_event_same_title_not_treated_as_our_write(self):
        # A read-only SOURCE event with the same title+time on another
        # calendar must not collapse the create (idempotency is scoped to the
        # TDTB-owned output calendar) and never reads back as our write.
        source = {"id": "src1", "title": "🟡 Minting 1",
                  "start": datetime(2026, 7, 12, 13, 0),
                  "end": datetime(2026, 7, 12, 14, 0),
                  "calendar_id": "cal-SOURCE"}
        store = FakeStore(events=[source])
        r = commit.write_calendar([self._mint_intent()], store, today=TODAY)
        assert r.ok, r.error
        assert store.created_calls == 1
        assert r.created[0] != "src1"
        assert r.noops == []
        assert r.touched["🟡 Minting 1"] == r.created[0]


# ---------------------------------------------------------------------------
# Trinoor classification fixture — FEEDBACK-26 (exact-match policy)
# ---------------------------------------------------------------------------

class TestTrinoorExactClassification:
    """Step D′ is the canonical Trinoor work-zone shape
    '[🟡 ]Trinoor : <slot>' ONLY — source/anchor rows whose names merely
    contain 'Trinoor' keep their own Step D/E classification."""

    def test_canonical_trinoor_zone_is_d_prime(self):
        manifest = shadow.build_plan_manifest(
            {"assigned": []},
            {"sequence": [{"id": "🟡 Trinoor : Morning", "start": "08:30",
                           "end": "12:30", "backdrop": True}]},
            {})
        [row] = [m for m in manifest if "Trinoor" in m.name]
        assert row.step == "D′" and row.system == "calendar"

    def test_trinoor_substring_anchored_block_is_step_e_not_d_prime(self):
        # A configured anchored block named 'Trinoor sync' is NOT a work
        # zone — it keeps its Step E write intent (never hijacked to D′).
        config = {"anchored_blocks": [{"id": "Trinoor sync", "on": True}]}
        manifest = shadow.build_plan_manifest(
            {"assigned": []},
            {"sequence": [{"id": "Trinoor sync", "start": "09:00",
                           "end": "10:00", "zone": "any"}]},
            config)
        assert [m.step for m in manifest if m.step == "D′"] == []
        assert [m.name for m in manifest if m.step == "E"] == ["Trinoor sync"]

    def test_trinoor_like_name_is_plain_step_d(self):
        manifest = shadow.build_plan_manifest(
            {"assigned": []},
            {"sequence": [{"id": "Trinoorish", "start": "09:00",
                           "end": "10:00", "zone": "any"}]},
            {})
        assert [m.step for m in manifest if m.step == "D′"] == []
        assert [m.name for m in manifest if m.step == "D"] == ["Trinoorish"]


# ---------------------------------------------------------------------------
# Zero-writer safety — no real writer calls reachable through the fixtures
# ---------------------------------------------------------------------------

class _FakeCalendar:
    def __init__(self, title: str, identifier: str):
        self.title = title
        self.identifier = identifier


class _ReadOnlyFakeStore:
    """Read-only EventStore fake recording every method call; writer methods
    are explicit tripwires that raise — any production path that reaches for
    a real calendar write would fail loudly instead of mutating."""

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


class TestZeroRealWriterCalls:
    def test_calendar_read_pipeline_records_only_read_verbs(self):
        store = _ReadOnlyFakeStore(
            [{"title": "Cooking", "calendar_id": "CAL-COOK",
              "start": datetime(2026, 7, 14, 20, 30),
              "end": datetime(2026, 7, 14, 21, 0)}],
            [_FakeCalendar("Cooking", "CAL-COOK")])
        cfg = {"calendar_capacity_classes": [
            {"BusyCal title": "Cooking", "Class": "fixed"}]}
        blocks, warnings = ext.fetch_calendar_busy(store, cfg, date(2026, 7, 14))
        assert warnings == []
        assert {b["Block"]: b["capacity_class"] for b in blocks} == {
            "Cooking": "fixed",
        }
        assert store.calls == ["auth_status", "calendars", "query_events"]

    def test_fixture_fakes_never_expose_a_live_provider_handle(self):
        # The write-verification fakes are in-memory dict stores with no
        # network, EventKit, or HTTP client surface.
        for fake in (FakeTodoist(), FakeStore(), ShiftingStore()):
            for attr in ("http", "client", "session", "base_url", "token"):
                assert not hasattr(fake, attr), f"{type(fake).__name__}.{attr}"
