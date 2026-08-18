"""IMP-03 Red tests for the frozen reliability contract.

Authority: ``Plans Link/2026-08-09-tdtb-planning-ui-reliability.md`` (locked
product contract items 1-21 and the final source-action semantics table).

These tests express locked behavior the canonical app does NOT implement yet.
They MUST FAIL against current source; that is the expected pre-implementation
(Red) state recorded in ``contract-test-gaps.json``. IMP-04/05/07 implement the
behavior - do not edit these tests into passing.

T04 corrective (2026-08-17): the contract-17 case was reconciled to post-merge
FEEDBACK-27 semantics — an EXPLICIT ``quarantined`` class keeps the exclusion,
while unlisted/unclassified timed calendars default ``fixed`` and stay visible.
The contract-17 test below passes against current source.

Fake/synthetic inputs only: no EventKit, no Todoist, no vault writes.
"""
from __future__ import annotations

import sys
import tempfile
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))
import external_sources as ext  # noqa: E402
import runtime_actions  # noqa: E402
import sequence  # noqa: E402

TODAY = date(2026, 7, 14)


class FakeStore:
    """Minimal EventKit-shaped fake (auth_status + calendars + query_events)."""

    def __init__(self, events: list[dict], calendars: list[dict] | None = None):
        self.events = events
        if calendars is not None:
            self._calendars = calendars

    def auth_status(self) -> str:
        return "authorized"

    def calendars(self) -> list[dict]:
        return getattr(self, "_calendars", [{"identifier": "CAL-X", "title": "X"}])

    def query_events(self, start: Any, end: Any, calendar_ids: Any = None) -> list[dict]:
        return self.events


def _event(
    title: str = "Dentist",
    start: str = "09:00",
    end: str = "10:00",
    cal: str = "CAL-OTHER",
    event_id: str | None = None,
    all_day: bool = False,
) -> dict:
    ev = {
        "title": title,
        "start": datetime(2026, 7, 14, *map(int, start.split(":"))),
        "end": datetime(2026, 7, 14, *map(int, end.split(":"))),
        "calendar_id": cal,
        "all_day": all_day,
    }
    if event_id is not None:
        ev["event_id"] = event_id
    return ev


# ---------------------------------------------------------------------------
# Contract 16 - duplicate source events canonicalize into one logical group
# ---------------------------------------------------------------------------

def test_duplicate_calendar_events_canonicalize_to_one_logical_group():
    """Two events sharing canonical identity collapse so attendance and
    capacity each count once. Current behavior emits one row per event."""
    store = FakeStore([
        _event("Standup", cal="CAL-WORK", event_id="EVT-1"),
        _event("Standup", cal="CAL-WORK", event_id="EVT-1"),
    ])
    blocks, _ = ext.fetch_calendar_busy(store, {}, TODAY)
    assert len(blocks) == 1


# ---------------------------------------------------------------------------
# Contract 17 - quarantined calendars stay excluded; unlisted defaults fixed
# ---------------------------------------------------------------------------

def test_explicitly_quarantined_calendar_stays_excluded():
    """Contract 17 exclusion is KEPT for an explicitly configured
    ``quarantined`` class: the row stays on the wire, excluded from
    fixed/work capacity and planning until reviewed."""
    store = FakeStore(
        [_event("Mystery", cal="CAL-UNKNOWN")],
        calendars=[{"identifier": "CAL-UNKNOWN", "title": "Some Random Cal"}],
    )
    blocks, _ = ext.fetch_calendar_busy(
        store,
        {
            "calendar_ids": {},
            "calendar_capacity_classes": [
                {"BusyCal title": "Some Random Cal", "Class": "quarantined"},
            ],
        },
        TODAY,
    )
    assert blocks[0]["capacity_class"] == "quarantined"


def test_unlisted_timed_calendar_defaults_fixed_and_stays_visible():
    """FEEDBACK-27 (2026-08-17): an unlisted/unclassified timed calendar
    defaults ``fixed`` and remains visible — implicit unlisted quarantine is
    superseded so real timed commitments surface as capacity."""
    store = FakeStore(
        [_event("Mystery", cal="CAL-UNKNOWN")],
        calendars=[{"identifier": "CAL-UNKNOWN", "title": "Some Random Cal"}],
    )
    blocks, _ = ext.fetch_calendar_busy(
        store,
        {"calendar_ids": {}, "calendar_capacity_classes": {}},
        TODAY,
    )
    assert blocks[0]["capacity_class"] == "fixed"
    assert blocks[0]["calendar_title"] == "Some Random Cal"


# ---------------------------------------------------------------------------
# Contract 18 - all-day source events stay all-day and non-timed
# ---------------------------------------------------------------------------

def test_all_day_source_event_stays_all_day_and_non_timed():
    """All-day source events remain all-day and non-timed on the wire; no
    ordinary planning path converts them to timed blocks. Current behavior
    silently drops the event (``if ev.get("all_day"): continue``)."""
    store = FakeStore([_event("Vacation", all_day=True)])
    blocks, _ = ext.fetch_calendar_busy(store, {}, TODAY)
    assert len(blocks) == 1
    assert blocks[0].get("all_day") is True


# ---------------------------------------------------------------------------
# Contract 5 - future-dated work does not appear as today's work
# ---------------------------------------------------------------------------

def test_future_dated_vault_assigned_item_absent_from_today_plan_inputs():
    """A vault note with ``assigned: true`` and a future deadline must not
    appear in today's assigned digest (no implicit capacity consumption).
    Current gather includes it whenever the assigned flag is set."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        note = root / "50 - Operations" / "Projects" / "Future.md"
        note.parent.mkdir(parents=True)
        note.write_text(
            "---\ntype: project\nassigned: true\ndeadline: 2099-01-01\n---\nbody\n",
            encoding="utf-8",
        )
        import main as main_mod

        app = main_mod.create_app(vault_root=root)
        body = TestClient(app).get("/plan-inputs").json()
        names = [i["name"] for i in body["digest"]["assigned"]]
        assert "Future" not in names


# ---------------------------------------------------------------------------
# Contract 14 - selected Mint sessions admit assignment before session start
# ---------------------------------------------------------------------------

CONFIG: dict[str, Any] = {
    "Defaults": {"eod": "20:00", "buffering.off_pct": 0},
    "Anchored Lifestyle Blocks": [],
}


def _seq_row(rid: str, start: str, end: str) -> dict:
    return {"id": rid, "start": start, "end": end}


def _mint_item() -> dict:
    return {
        "id": "Mint Morning",
        "zone": "work_hours",
        "placement_window": {"start": "08:30", "end": "09:00"},
    }


def test_selected_mint_session_permits_assignment_before_session_start():
    """Mint placement direction (capture): selected Mint sessions admit
    associated assignment BEFORE the session start, not only inside/after the
    window. Current validation hard-rejects any row starting before the
    session window."""
    result = sequence.validate_sequence(
        {"sequence": [_seq_row("Mint Morning", "08:00", "08:30")]},
        [],
        [],
        CONFIG,
        optional_ids={"Mint Morning"},
        optional_items=[_mint_item()],
    )
    assert result.ok is True


# ---------------------------------------------------------------------------
# Final source-action semantics - Done / Drop from plan / Unassign / Delete
# ---------------------------------------------------------------------------

def test_final_action_verbs_exist():
    """The four final intents replace the current verb catalogue."""
    assert {"done", "drop_from_plan", "unassign", "delete"} <= set(runtime_actions.VERBS)


def test_drop_from_plan_is_date_scoped_with_no_source_write():
    """Drop is a date-scoped TDTB exclusion: it removes from current planning
    only, touches no Todoist/vault/calendar, and is undoable. Current code has
    no such verb."""
    steps = runtime_actions.plan_steps(
        "drop_from_plan",
        {"phase": "committed", "name": "Press", "id": "x"},
        {},
        TODAY,
    )
    assert steps
    assert all(step.get("system") == "runstate" for step in steps)


def test_unassign_advances_recurring_without_completing():
    """Recurring Unassign advances the due occurrence without completing it
    and preserves the recurrence contract. Current code has no such verb."""
    steps = runtime_actions.plan_steps(
        "unassign",
        {"phase": "committed", "name": "Press", "id": "x", "is_recurring": True},
        {},
        TODAY,
    )
    assert any(step.get("system") == "todoist" and not step.get("close") for step in steps)


def test_drop_and_unassign_are_legal_before_commit():
    """All four final actions are available before AND after commit."""
    staging = {"phase": "staging", "name": "Press", "id": "x"}
    for verb in ("drop_from_plan", "unassign"):
        runtime_actions.plan_steps(verb, staging, {}, TODAY)  # must not refuse
