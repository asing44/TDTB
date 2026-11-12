"""Mint grant PLACEMENT — the app places a granted row inside Mint.

Owner decision (2026-11-15): the per-row ``may_overlap_mint`` grant is not a
tolerance, it is an instruction. The deterministic post-pass
(``sequence.place_granted_mint_rows``) fills the day's SELECTED Mint sessions:

- one granted row per 30-minute session, a longer row spanning consecutive
  free sessions, earliest free session first;
- a native Todoist due time is not an exception — the grant authorizes the
  retime, and the two downstream gates must agree: the commit lane waives
  that row's native pin ONLY while it sits inside a server-selected session,
  and the shadow lane treats the grant as retime authorization.

Fixtures/fakes only: zero billed calls, zero real source writes.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))
import main as main_mod  # noqa: E402
import runstate as rs  # noqa: E402
import sequence as sequence_module  # noqa: E402
import shadow  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent.parent / "gather"))
import tdtb_gather as gather  # noqa: E402

from datetime import datetime  # noqa: E402
from sequence import validate_sequence  # noqa: E402
from tests.config_markdown import write_config_note  # noqa: E402


CFG = {
    "Template Blocks": {"Trinoor Hours": [
        {"Slot": "Morning", "Start": "9:00 AM", "End": "11:00 AM"},
        {"Slot": "Afternoon", "Start": "1:00 PM", "End": "3:00 PM"},
    ]},
}


def _seq_row(id_, start, end, zone="any", **extra):
    return {"id": id_, "start": start, "end": end, "zone": zone, **extra}


def _mint_item(id_, start, end):
    return {
        "id": id_, "name": id_, "zone": "work_hours", "source": "schedulable",
        "mint_session": True, "mint_session_id": f"mint:session:{start}",
        "placement_window": {"start": start, "end": end},
        "calendar_class": "mint",
    }


def _sessions(*windows):
    """Selected Mint sessions as optional_items, 30 minutes each."""
    return [
        _mint_item(f"Mint Session · {start}", start, end)
        for start, end in windows
    ]


def _assigned(id_, blocks, **extra):
    return {"id": id_, "zone": "any", "blocks": blocks, **extra}


def _run(proposal, *, assigned, anchored=None, optional_items=None, grants=None):
    return validate_sequence(
        {"sequence": proposal, "overlap_grants": grants or []},
        assigned, anchored or [], CFG,
        optional_items=optional_items or [],
    )


def _row_by_id(proposal, row_id):
    [row] = [row for row in proposal if row.get("id") == row_id]
    return row


def _unplaced_warnings(result):
    return [w for w in result.warnings
            if w.get("rule") == sequence_module.MINT_GRANT_UNPLACED]


def _granted_warnings(result):
    return [w for w in result.warnings if w.get("rule") == "mint_overlap_granted"]


# Four back-to-back 13:00-15:00 sessions.
FOUR = _sessions(
    ("13:00", "13:30"), ("13:30", "14:00"),
    ("14:00", "14:30"), ("14:30", "15:00"),
)


# ---------------------------------------------------------------------------
# The post-pass: placement geometry
# ---------------------------------------------------------------------------

class TestGrantedPlacementInsideMint:
    def test_longer_granted_row_starts_at_the_first_session_and_spans_three(self):
        # A 90-minute grant needs three consecutive 30-minute sessions: it
        # starts at the earliest free one and spans them, never shrinks.
        proposal = [_seq_row("Deep Work", "09:00", "10:30")]
        result = _run(
            proposal,
            assigned=[_assigned("Deep Work", 3, may_overlap_mint=True)],
            optional_items=FOUR,
        )
        assert result.hard_errors == []
        row = _row_by_id(proposal, "Deep Work")
        assert (row["start"], row["end"]) == ("13:00", "14:30")
        # The grant is applied, not merely tolerated.
        assert _granted_warnings(result) == []
        assert sequence_module.MINT_GRANT_UNPLACED not in {
            w.get("rule") for w in result.warnings
        }

    def test_two_granted_rows_never_share_a_session(self):
        proposal = [
            _seq_row("First", "09:00", "09:30"),
            _seq_row("Second", "10:00", "10:30"),
        ]
        result = _run(
            proposal,
            assigned=[
                _assigned("First", 1, may_overlap_mint=True),
                _assigned("Second", 1, may_overlap_mint=True),
            ],
            optional_items=_sessions(("13:00", "13:30"), ("13:30", "14:00")),
        )
        assert result.hard_errors == []
        first = _row_by_id(proposal, "First")
        second = _row_by_id(proposal, "Second")
        assert (first["start"], first["end"]) == ("13:00", "13:30")
        assert (second["start"], second["end"]) == ("13:30", "14:00")

    def test_wall_blocked_session_is_skipped_for_the_next_free_run(self):
        # A fixed calendar event owns 13:00-13:30, so the only free session is
        # the next one; the granted row lands there instead.
        busy = {
            "Block": "Standup", "Type": "hard", "Start": "13:00",
            "End": "13:30", "source": "calendar", "capacity_class": "fixed",
        }
        proposal = [_seq_row("Backpack", "09:00", "09:30")]
        result = _run(
            proposal,
            assigned=[_assigned("Backpack", 1, may_overlap_mint=True)],
            anchored=[busy],
            optional_items=_sessions(("13:00", "13:30"), ("13:30", "14:00")),
        )
        assert result.hard_errors == []
        row = _row_by_id(proposal, "Backpack")
        assert (row["start"], row["end"]) == ("13:30", "14:00")

    def test_too_long_granted_row_stays_put_and_names_the_unplaced_rule(self):
        # Two free sessions hold 60 minutes; a 120-minute grant cannot be
        # placed, so it keeps its own slot and says so by rule name.
        proposal = [_seq_row("Deep Work", "09:00", "11:00")]
        result = _run(
            proposal,
            assigned=[_assigned("Deep Work", 4, may_overlap_mint=True)],
            optional_items=_sessions(("13:00", "13:30"), ("13:30", "14:00")),
        )
        assert result.hard_errors == []
        row = _row_by_id(proposal, "Deep Work")
        assert (row["start"], row["end"]) == ("09:00", "11:00")
        [warning] = _unplaced_warnings(result)
        assert "Deep Work" in warning["detail"]
        assert "no free consecutive Mint session holds 120 min" in warning["detail"]

    def test_same_row_without_the_grant_is_still_a_hard_mint_overlap(self):
        # The wall itself is unchanged: only a granted row is movable into it.
        proposal = [_seq_row("Backpack", "13:00", "13:30")]
        result = _run(
            proposal,
            assigned=[_assigned("Backpack", 1)],
            optional_items=_sessions(("13:00", "13:30")),
        )
        assert result.ok is False
        assert any("Backpack" in error for error in result.hard_errors)

    def test_no_grants_leaves_the_sequence_untouched(self):
        proposal = [
            _seq_row("Alpha", "09:00", "09:30"),
            _seq_row("Beta", "10:00", "10:30"),
        ]
        result = _run(
            proposal,
            assigned=[_assigned("Alpha", 1), _assigned("Beta", 1)],
            optional_items=FOUR,
        )
        assert result.ok is True
        assert result.warnings == []
        assert [(row["id"], row["start"], row["end"]) for row in proposal] == [
            ("Alpha", "09:00", "09:30"),
            ("Beta", "10:00", "10:30"),
        ]


# ---------------------------------------------------------------------------
# Native due time: the grant authorizes the move
# ---------------------------------------------------------------------------

NATIVE_ITEM = {
    "id": "Errand", "name": "Errand", "zone": "any", "blocks": 1,
    "source": "todoist", "todoist_id": "t-errand",
    "path": "todoist://t-errand", "scheduled_start": "20:30",
    "is_recurring": False,
}


class TestGrantedNativeRow:
    def test_granted_native_row_is_moved_into_the_selected_session(self):
        # Operator decision: Todoist due 20:30 → Mint 13:00. The grant, not
        # the source time, decides; the pass moves the row and reports no
        # defect because the grant is what it applied.
        proposal = [_seq_row("Errand", "20:30", "21:00")]
        result = _run(
            proposal,
            assigned=[{**NATIVE_ITEM, "may_overlap_mint": True}],
            optional_items=_sessions(("13:00", "13:30")),
        )
        assert result.ok is True
        assert result.hard_errors == []
        row = _row_by_id(proposal, "Errand")
        assert (row["start"], row["end"]) == ("13:00", "13:30")
        assert _unplaced_warnings(result) == []

    def test_ungranted_native_row_is_not_moved_by_the_pass(self):
        proposal = [_seq_row("Errand", "20:30", "21:00")]
        result = _run(
            proposal,
            assigned=[dict(NATIVE_ITEM)],
            optional_items=_sessions(("13:00", "13:30")),
        )
        assert result.ok is True
        row = _row_by_id(proposal, "Errand")
        assert (row["start"], row["end"]) == ("20:30", "21:00")


# ---------------------------------------------------------------------------
# Commit lane: the native pin is waived only inside a selected session
# ---------------------------------------------------------------------------

class TestCommitNativePinWaiver:
    def _native_assigned(self):
        return [{
            "id": "Errand", "name": "Errand", "source": "todoist",
            "todoist_id": "t-errand", "path": "todoist://t-errand",
            "scheduled_start": "20:30", "blocks": 1, "is_recurring": False,
        }]

    def test_waiver_requires_a_server_selected_session_and_the_grant(self):
        # Placement inside a server-selected session: waived.
        placed = main_mod._mint_grant_placed_row_ids(
            [({"name": "Errand"}, {"may_overlap_mint": True})],
            [{"id": "Errand", "start": "13:00", "end": "13:30"}],
            _sessions(("13:00", "13:30")),
        )
        assert placed == {"Errand"}
        # Moved anywhere else: no waiver, so the pin still binds.
        assert main_mod._mint_grant_placed_row_ids(
            [({"name": "Errand"}, {"may_overlap_mint": True})],
            [{"id": "Errand", "start": "16:00", "end": "16:30"}],
            _sessions(("13:00", "13:30")),
        ) == set()
        # No admitted grant (no literal True on the submitted row): no waiver.
        assert main_mod._mint_grant_placed_row_ids(
            [({"name": "Errand"}, {"may_overlap_mint": "true"})],
            [{"id": "Errand", "start": "13:00", "end": "13:30"}],
            _sessions(("13:00", "13:30")),
        ) == set()

    def test_verify_native_pins_waives_the_granted_row_inside_mint(self):
        rows = [{"id": "Errand", "start": "13:00", "end": "13:30", "zone": "any"}]
        assert sequence_module.verify_native_pins_in_sequence(
            self._native_assigned(), rows, []
        )
        assert sequence_module.verify_native_pins_in_sequence(
            self._native_assigned(), rows, [], mint_granted_ids={"Errand"}
        ) == []

    def test_other_native_rows_keep_their_pin_when_one_row_is_waived(self):
        native = self._native_assigned() + [{
            "id": "Call", "name": "Call", "source": "todoist",
            "todoist_id": "t-call", "path": "todoist://t-call",
            "scheduled_start": "09:00", "blocks": 1, "is_recurring": False,
        }]
        rows = [
            {"id": "Errand", "start": "13:00", "end": "13:30", "zone": "any"},
            {"id": "Call", "start": "10:00", "end": "10:30", "zone": "any"},
        ]
        errors = sequence_module.verify_native_pins_in_sequence(
            native, rows, [], mint_granted_ids={"Errand"}
        )
        assert any("Call" in error and "moved" in error for error in errors)
        assert not any("Errand" in error for error in errors)


# ---------------------------------------------------------------------------
# Shadow lane: an admitted grant is retime authorization
# ---------------------------------------------------------------------------

def _granted_digest(granted: bool):
    item = dict(NATIVE_ITEM)
    if granted:
        item["may_overlap_mint"] = True
    return {"assigned": [item], "suggested": []}


def _live_errand():
    return {"todoist_tasks": [{
        "id": "t-errand", "content": "Errand",
        "due": {"date": "2026-07-12T20:30:00"},
    }]}


SHADOW_SEQUENCE = {"sequence": [
    {"id": "Errand", "start": "13:00", "end": "13:30", "zone": "any"},
]}


class TestShadowGrantedRetime:
    def test_granted_native_row_is_retimed_to_its_mint_slot(self):
        manifest = shadow.build_plan_manifest(
            _granted_digest(True), SHADOW_SEQUENCE, {},
        )
        [entry] = shadow.diff_against_live(manifest, _live_errand()).entries
        assert entry.classification == shadow.UPDATE
        assert entry.detail["retiming_authorized"] is True
        assert entry.detail["due_time"] == {"old": "20:30", "new": "13:00"}

    def test_same_row_without_the_grant_is_still_a_pinned_noop(self):
        # Regression: no grant, no retime — the protected native time holds.
        manifest = shadow.build_plan_manifest(
            _granted_digest(False), SHADOW_SEQUENCE, {},
        )
        [entry] = shadow.diff_against_live(manifest, _live_errand()).entries
        assert entry.classification == shadow.NOOP
        assert entry.detail["pinned_native"] is True
        assert entry.detail["retiming_authorized"] is False


# ---------------------------------------------------------------------------
# Commit route: the admitted grant reaches the manifest, not the pin refusal
# ---------------------------------------------------------------------------

CFG_NOTE = (
    "## Defaults\n"
    "| Key | Value |\n|---|---|\n"
    "| eod | 11:59 PM |\n"
    "| anchor.round_to_minutes | 15 |\n"
    "\n## Template Blocks\n"
    "### Trinoor Hours\n"
    "| Slot | Start | End |\n|---|---|---|\n"
    "| Afternoon | 1:00 PM | 2:00 PM |\n"
)


@pytest.fixture
def vault(tmp_path) -> Path:
    v = tmp_path / "vault-root"
    v.mkdir()
    cfg = v / "00 - META/Skill-Configs/tdtb-bridger.md"
    cfg.parent.mkdir(parents=True)
    write_config_note(cfg, CFG_NOTE, encoding="utf-8")
    return v


@pytest.fixture
def client(vault) -> TestClient:
    app = main_mod.create_app()
    c = TestClient(app)
    c.app_token = app.state.token
    return c


def _auth(client: TestClient) -> dict:
    return {"X-TDTB-Token": client.app_token}


class TestCommitRouteGrantedNativeRow:
    def _seed(self, client):
        today = gather.effective_date(datetime.now())
        rs.write_digest_index(today, [{
            "name": "Errand", "todoist_id": "t-errand",
            "path": "todoist://t-errand", "surface": "assigned",
            "source": "todoist", "blocks": 1, "duration": 30,
            "is_recurring": False, "scheduled_start": "20:30",
        }])
        r = client.post("/day-setup", headers=_auth(client), json={
            "schedulable": {"minting": {
                "on": True,
                "sessions": ["mint:afternoon:13:00", "mint:afternoon:13:30"],
            }},
            "work_allotment_minutes": 60,
        })
        assert r.status_code == 200, r.text

    def _body(self, *, granted):
        assigned = {
            "name": "Errand", "todoist_id": "t-errand",
            "path": "todoist://t-errand", "source": "todoist",
        }
        if granted:
            assigned["may_overlap_mint"] = True
        return {
            "digest": {"assigned": [assigned], "suggested": []},
            "sequence": {"sequence": [
                {"id": "Errand", "start": "13:00", "end": "13:30", "zone": "any"},
            ]},
            "config": {},
        }

    def _capture_manifest(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            main_mod.shadow, "build_plan_manifest",
            lambda *a, **k: calls.append(a) or [],
        )
        monkeypatch.setattr(main_mod.shadow, "gather_live_state", lambda *a, **k: {})
        monkeypatch.setattr(
            main_mod.shadow, "diff_against_live",
            lambda *a, **k: main_mod.shadow.ShadowDiff(entries=[]),
        )
        return calls

    def test_granted_row_moved_into_a_selected_session_commits(self, client, monkeypatch):
        self._seed(client)
        calls = self._capture_manifest(monkeypatch)
        r = client.post(
            "/commit?mode=shadow", headers=_auth(client),
            json=self._body(granted=True),
        )
        assert r.status_code == 200, r.text
        assert len(calls) == 1
        # The admitted grant rides the sanitized digest so the write lane can
        # retime the row instead of reporting a pinned native mismatch.
        assert calls[0][0]["assigned"][0]["may_overlap_mint"] is True

    def test_same_row_without_the_grant_is_still_refused(self, client, monkeypatch):
        self._seed(client)
        calls = self._capture_manifest(monkeypatch)
        r = client.post(
            "/commit?mode=shadow", headers=_auth(client),
            json=self._body(granted=False),
        )
        assert r.status_code == 422
        assert "native timed protection" in str(r.json()["detail"])
        assert calls == []


# ---------------------------------------------------------------------------
# The two pin gates: the waiver is exactly "admitted grant + inside Mint"
# ---------------------------------------------------------------------------

#: The native pin the server derives for the 20:30 Todoist row (30 minutes):
#: ``timed_auto_pins`` emits ``zone: None`` for a server-derived native pin.
PIN = {"id": "Errand", "start": "20:30", "end": "21:00", "zone": None}

PIN_MOVED = "pinned rows changed from immutable snapshot"

MINTING = {
    "on": True,
    "sessions": ["mint:afternoon:13:00", "mint:afternoon:13:30"],
}


def _seed_native_mint_day() -> None:
    """Today's dated state: two selected Mint sessions and one native pin."""
    today = gather.effective_date(datetime.now())
    rs.write_runstate(today, rs.build_runstate({
        "anchor": "00:00",
        "eod": "23:59",
        "schedulable": {"minting": dict(MINTING)},
        "pinned_rows": [dict(PIN)],
        "overlap_grants": [],
        "planning_config_fingerprint": "",
    }))
    rs.write_digest_index(today, [{
        "name": "Errand", "todoist_id": "t-errand",
        "path": "todoist://t-errand", "surface": "assigned",
        "source": "todoist", "blocks": 1, "duration": 30,
        "is_recurring": False, "scheduled_start": "20:30",
    }])


def _submitted_assigned(*, granted: bool) -> dict:
    row = {
        "name": "Errand", "todoist_id": "t-errand",
        "path": "todoist://t-errand",
    }
    if granted:
        row["may_overlap_mint"] = True
    return row


def _post_validate(client, *, start, end, granted):
    return client.post("/validate-sequence", headers=_auth(client), json={
        "sequence": [{"id": "Errand", "start": start, "end": end, "zone": "any"}],
        "assigned": [_submitted_assigned(granted=granted)],
        "anchored_blocks": [],
        "config": CFG,
        "pinned_rows": [dict(PIN)],
        "overlap_grants": [],
        "planning_config_fingerprint": "",
    })


class TestValidateSequencePinGate:
    def test_granted_native_row_placed_in_mint_passes_the_pin_gate(self, client):
        # Owner decision: an ADMITTED grant placing the row fully inside a
        # selected Mint session is the authorized retime, so the immutable-pin
        # snapshot comparison must not refuse it.
        _seed_native_mint_day()
        r = _post_validate(client, start="13:00", end="13:30", granted=True)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is True, body
        assert body["hard_errors"] == []

    def test_granted_native_row_moved_outside_mint_still_fails_the_pin_gate(
        self, client
    ):
        # Same grant, different slot: the waiver needs the server's own
        # selected-session coverage, so 16:00 keeps the native pin intact.
        _seed_native_mint_day()
        r = _post_validate(client, start="16:00", end="16:30", granted=True)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is False
        assert body["hard_errors"] == [PIN_MOVED]

    def test_ungranted_native_row_moved_into_mint_still_fails_the_pin_gate(
        self, client
    ):
        # Without the admitted grant the row is exactly as protected as before:
        # the pin comparison refuses it (and the Mint wall does too).
        _seed_native_mint_day()
        r = _post_validate(client, start="13:00", end="13:30", granted=False)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is False
        assert PIN_MOVED in body["hard_errors"]


class TestCommitPinGate:
    """The commit lane's immutable-pin 409 applies the same narrow waiver."""

    def _seed(self, client) -> None:
        today = gather.effective_date(datetime.now())
        rs.write_digest_index(today, [{
            "name": "Errand", "todoist_id": "t-errand",
            "path": "todoist://t-errand", "surface": "assigned",
            "source": "todoist", "blocks": 1, "duration": 30,
            "is_recurring": False, "scheduled_start": "20:30",
        }])
        r = client.post("/day-setup", headers=_auth(client), json={
            "schedulable": {"minting": dict(MINTING)},
            "work_allotment_minutes": 60,
        })
        assert r.status_code == 200, r.text
        # The client echoes the snapshot's pins, so both gates see the same
        # server-derived pin list and only the row's slot differs.
        rs.update_runstate(today, {
            "pinned_rows": [dict(PIN)],
            "overlap_grants": [],
            "planning_config_fingerprint": "",
        })

    def _post(self, client, monkeypatch, *, start, end, granted):
        calls = []
        monkeypatch.setattr(
            main_mod.shadow, "build_plan_manifest",
            lambda *a, **k: calls.append(a) or [],
        )
        monkeypatch.setattr(main_mod.shadow, "gather_live_state", lambda *a, **k: {})
        monkeypatch.setattr(
            main_mod.shadow, "diff_against_live",
            lambda *a, **k: main_mod.shadow.ShadowDiff(entries=[]),
        )
        r = client.post(
            "/commit?mode=shadow", headers=_auth(client),
            json={
                "digest": {
                    "assigned": [_submitted_assigned(granted=granted)],
                    "suggested": [],
                },
                "sequence": {"sequence": [
                    {"id": "Errand", "start": start, "end": end, "zone": "any"},
                ]},
                "config": {},
                "pinned_rows": [dict(PIN)],
                "overlap_grants": [],
                "planning_config_fingerprint": "",
            },
        )
        return r, calls

    def test_granted_row_inside_mint_is_not_a_pinned_row_409(self, client, monkeypatch):
        self._seed(client)
        r, calls = self._post(
            client, monkeypatch, start="13:00", end="13:30", granted=True,
        )
        assert r.status_code == 200, r.text
        assert len(calls) == 1

    def test_granted_row_outside_mint_is_still_a_pinned_row_409(
        self, client, monkeypatch
    ):
        self._seed(client)
        r, calls = self._post(
            client, monkeypatch, start="16:00", end="16:30", granted=True,
        )
        assert r.status_code == 409, r.text
        assert PIN_MOVED in str(r.json()["detail"])
        assert calls == []

    def test_ungranted_row_inside_mint_is_still_a_pinned_row_409(
        self, client, monkeypatch
    ):
        self._seed(client)
        r, calls = self._post(
            client, monkeypatch, start="13:00", end="13:30", granted=False,
        )
        assert r.status_code == 409, r.text
        assert PIN_MOVED in str(r.json()["detail"])
        assert calls == []

    def _post_live(self, client, monkeypatch, *, start, end, granted):
        """Live mode: no writers are reached, so the pin gate is observable.

        A row that clears the gate falls through to the live-state read, which
        is stubbed to fail closed with 502 — that is the proof it was admitted.
        """
        monkeypatch.setattr(
            main_mod.shadow, "gather_live_state",
            lambda *a, **k: (_ for _ in ()).throw(
                main_mod.shadow.ShadowStateError("stub")
            ),
        )
        return client.post(
            "/commit?mode=live", headers=_auth(client),
            json={
                "digest": {
                    "assigned": [_submitted_assigned(granted=granted)],
                    "suggested": [],
                },
                "sequence": {"sequence": [
                    {"id": "Errand", "start": start, "end": end, "zone": "any"},
                ]},
                "config": {},
                "pinned_rows": [dict(PIN)],
                "overlap_grants": [],
                "planning_config_fingerprint": "",
            },
        )

    def test_live_granted_row_inside_mint_clears_the_pin_gate(self, client, monkeypatch):
        self._seed(client)
        r = self._post_live(
            client, monkeypatch, start="13:00", end="13:30", granted=True,
        )
        assert PIN_MOVED not in str(r.json().get("detail", "")), r.text
        assert r.status_code == 502, r.text

    def test_live_granted_row_outside_mint_is_still_a_pinned_row_409(
        self, client, monkeypatch
    ):
        self._seed(client)
        r = self._post_live(
            client, monkeypatch, start="16:00", end="16:30", granted=True,
        )
        assert r.status_code == 409, r.text
        assert PIN_MOVED in str(r.json()["detail"])

    def test_live_ungranted_row_inside_mint_is_still_a_pinned_row_409(
        self, client, monkeypatch
    ):
        self._seed(client)
        r = self._post_live(
            client, monkeypatch, start="13:00", end="13:30", granted=False,
        )
        assert r.status_code == 409, r.text
        assert PIN_MOVED in str(r.json()["detail"])


class TestSharedCoverageHelper:
    """One geometry rule, used by both gates."""

    def test_consecutive_sessions_cover_a_longer_row_but_a_gap_does_not(self):
        items = _sessions(("13:00", "13:30"), ("13:30", "14:00"))
        covered = sequence_module.mint_grant_covered_row_ids(
            {"Deep Work"}, [{"id": "Deep Work", "start": "13:00", "end": "14:00"}],
            items,
        )
        assert covered == {"Deep Work"}
        # A session missing in the middle leaves the run too short.
        gapped = _sessions(("13:00", "13:30"), ("14:00", "14:30"))
        assert sequence_module.mint_grant_covered_row_ids(
            {"Deep Work"}, [{"id": "Deep Work", "start": "13:00", "end": "14:00"}],
            gapped,
        ) == set()

    def test_ungranted_ids_and_non_mint_items_are_never_covered(self):
        items = _sessions(("13:00", "13:30"))
        rows = [{"id": "Errand", "start": "13:00", "end": "13:30"}]
        assert sequence_module.mint_grant_covered_row_ids(set(), rows, items) == set()
        assert sequence_module.mint_grant_covered_row_ids(
            {"Errand"}, rows, [],
        ) == set()
