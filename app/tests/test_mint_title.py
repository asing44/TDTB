"""Mint block titles carry no timestamp (owner-facing fix).

The cockpit already prints the block's start above it, so a Mint block reads
"Mint Afternoon" and the calendar event TDTB writes on the Mint calendar is
titled the same way. The timestamped form ("Mint Afternoon · 16:00") stays the
STABLE IDENTITY (``id``/``name``) so persisted Day Setup selections, the commit
ledger's ``touched`` map, and events TDTB already wrote keep matching; a
time-free ``title`` is what display and calendar writes use.

Each story is one named fixture:

- options: a generated session exposes a time-free ``title`` and keeps its
  timestamped ``id``/``name``.
- row/sequence: the schedulable Mint row and its sequence row carry the title
  through to the write manifest.
- shadow: both the legacy timestamped event and a time-free event are the SAME
  own block (no duplicate create).
- commit: a create lands the time-free title, and a pre-existing legacy-titled
  event stays a no-op with a clean reconciliation.
- persistence: an old saved selection resolves by either the old id or the old
  timestamped name.

Safety: pure fixtures and in-memory fakes only. No live server, no EventKit, no
write to a real calendar.
"""
from __future__ import annotations

import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import calendar_bridge  # noqa: E402
import commit  # noqa: E402
import external_sources as ext  # noqa: E402
import sequence as sequence_module  # noqa: E402
import shadow  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent))
from test_commit import FakeStore  # noqa: E402

MONDAY = date(2026, 7, 13)
CFG = {
    "Template Blocks": {"Trinoor Hours": [
        {"Slot": "Morning", "Start": "8:30 AM", "End": "12:00 PM"},
        {"Slot": "Afternoon", "Start": "1:30 PM", "End": "4:30 PM"},
    ]},
}
OWN_CAL = "cal-mint-1"
CALENDARS = [calendar_bridge.CalendarInfo("🟡 Mint", OWN_CAL, True, "iCloud")]
RESOLVED = {"🟡 Mint": OWN_CAL}

SESSION_ID = "mint:afternoon:16:00"
LEGACY_NAME = "Mint Afternoon · 16:00"
DISPLAY_TITLE = "Mint Afternoon"


def _selected_mint_items(session_id: str = SESSION_ID):
    """The schedulable Mint items for one explicitly selected session."""
    items, _zones, _notes = ext.build_schedulable_blocks(
        CFG,
        {"schedulable": {"minting": {"on": True, "sessions": [session_id]}}},
        MONDAY,
        "09:00",
        resolved_day_semantics={"effective_allotment_minutes": 30},
    )
    return [item for item in items if item.get("mint_session")]


def _sequence(items) -> dict:
    return {"sequence": sequence_module.placement_window_rows(items)}


def _manifest(items=None):
    return shadow.build_plan_manifest(
        {}, _sequence(items or _selected_mint_items()), CFG,
    )


def _live(title: str) -> dict:
    return {
        "todoist_tasks": [],
        "calendar_events": [{
            "id": "e1",
            "title": title,
            "start": datetime(2026, 7, 13, 16, 0),
            "end": datetime(2026, 7, 13, 16, 30),
            "calendar_id": OWN_CAL,
        }],
    }


# ---------------------------------------------------------------------------
# Fixture 1: the generated options
# ---------------------------------------------------------------------------

class TestMintSessionOptions:
    def test_session_exposes_time_free_title_and_keeps_its_identity(self):
        options = {o["id"]: o for o in ext.mint_session_options(CFG)}
        option = options[SESSION_ID]
        assert option["title"] == DISPLAY_TITLE
        assert ":" not in option["title"]
        # Identity is unchanged — persisted selections and the write ledger key
        # on these two values.
        assert option["name"] == LEGACY_NAME
        assert option["id"] == SESSION_ID

    def test_every_session_title_is_its_slot_label(self):
        for option in ext.mint_session_options(CFG):
            assert option["title"] == f"Mint {option['slot']}"
            assert option["start"] not in option["title"]


# ---------------------------------------------------------------------------
# Fixture 2: the schedulable row and its sequence row
# ---------------------------------------------------------------------------

class TestSchedulableAndSequenceRows:
    def test_schedulable_mint_row_carries_the_time_free_title(self):
        [mint] = _selected_mint_items()
        assert mint["title"] == DISPLAY_TITLE
        assert mint["id"] == LEGACY_NAME
        assert mint["name"] == LEGACY_NAME

    def test_sequence_row_preserves_the_title_and_the_identity(self):
        [row] = sequence_module.placement_window_rows(_selected_mint_items())
        assert row["title"] == DISPLAY_TITLE
        assert row["id"] == LEGACY_NAME
        assert row["mint_session_id"] == SESSION_ID


# ---------------------------------------------------------------------------
# Fixture 3: the write manifest and its diff against live calendar state
# ---------------------------------------------------------------------------

class TestManifestAndDiff:
    def test_manifest_keeps_identity_name_and_adds_display_title(self):
        [entry] = _manifest()
        assert entry.name == LEGACY_NAME
        assert entry.title == DISPLAY_TITLE
        assert entry.routing == "🟡 Mint"
        # The public manifest shape stays unchanged.
        assert "title" not in entry.as_dict()

    def test_legacy_timestamped_event_is_the_same_own_block(self):
        diff = shadow.diff_against_live(_manifest(), _live(LEGACY_NAME))
        [entry] = diff.entries
        assert entry.classification == shadow.NOOP
        assert entry.detail["event_id"] == "e1"

    def test_time_free_event_is_the_same_own_block(self):
        diff = shadow.diff_against_live(_manifest(), _live(DISPLAY_TITLE))
        [entry] = diff.entries
        assert entry.classification == shadow.NOOP
        assert entry.detail["event_id"] == "e1"

    def test_unrelated_title_is_still_a_create(self):
        diff = shadow.diff_against_live(_manifest(), _live("Mint Afternoon · 17:00"))
        [entry] = diff.entries
        assert entry.classification == shadow.CREATE


# ---------------------------------------------------------------------------
# Fixture 4: the calendar write path
# ---------------------------------------------------------------------------

class TestCalendarWritePath:
    def test_create_lands_the_time_free_title(self):
        intents = commit.plan_writes(
            shadow.diff_against_live(_manifest(), {"todoist_tasks": [],
                                                   "calendar_events": []}),
            RESOLVED, CFG, today=MONDAY,
        )
        store = FakeStore(calendars=CALENDARS)
        result = commit.write_calendar(intents, store, today=MONDAY)
        assert result.ok is True, result.error
        assert store.created_calls == 1
        [event] = store.query_events(None, None)
        assert event["title"] == DISPLAY_TITLE
        assert event["start"] == datetime(2026, 7, 13, 16, 0)

    def test_existing_legacy_event_is_never_duplicated(self):
        intents = commit.plan_writes(
            shadow.diff_against_live(_manifest(), _live(LEGACY_NAME)),
            RESOLVED, CFG, today=MONDAY,
        )
        assert [i.op for i in intents] == ["noop"]
        store = FakeStore(calendars=CALENDARS, events=[{
            "id": "e1", "title": LEGACY_NAME,
            "start": datetime(2026, 7, 13, 16, 0),
            "end": datetime(2026, 7, 13, 16, 30),
            "calendar_id": OWN_CAL,
        }])
        result = commit.write_calendar(intents, store, today=MONDAY)
        assert store.created_calls == 0
        assert result.noops == ["e1"]
        assert result.ok is True, result.error
        assert result.verify_failures == []


# ---------------------------------------------------------------------------
# Fixture 5: persisted selections
# ---------------------------------------------------------------------------

class TestPersistedSelection:
    def test_old_id_and_old_timestamped_name_still_resolve(self):
        out = ext.normalize_mint_session_override(
            CFG, {"on": True, "sessions": [SESSION_ID, "Mint Afternoon · 14:00"]},
            allotment_minutes=60,
        )
        assert out["sessions"] == ["mint:afternoon:14:00", SESSION_ID]
        assert out["n"] == 2


def test_sibling_sessions_sharing_a_title_stay_distinct():
    """Every session in one slot is titled "Mint Afternoon"; the 15:30 event
    must not absorb the 16:00 session (silent missing create)."""
    items, _z, _n = ext.build_schedulable_blocks(
        CFG,
        {"schedulable": {"minting": {"on": True, "sessions": [
            "mint:afternoon:15:30", "mint:afternoon:16:00"]}}},
        MONDAY, "09:00",
        resolved_day_semantics={"effective_allotment_minutes": 60},
    )
    mint = [i for i in items if i.get("mint_session")]
    assert len(mint) == 2
    manifest = shadow.build_plan_manifest(
        {}, {"sequence": sequence_module.placement_window_rows(mint)}, CFG,
    )
    event_1530 = {
        "id": "e-1530", "title": DISPLAY_TITLE,
        "start": datetime(2026, 7, 13, 15, 30), "end": datetime(2026, 7, 13, 16, 0),
        "calendar_id": OWN_CAL,
    }
    diff = shadow.diff_against_live(
        manifest, {"todoist_tasks": [], "calendar_events": [dict(event_1530)]},
    )
    by_name = {e.manifest.name: e for e in diff.entries if e.manifest.system == "calendar"}
    assert by_name["Mint Afternoon · 15:30"].classification == shadow.NOOP
    assert by_name["Mint Afternoon · 16:00"].classification == shadow.CREATE

    intents = [i for i in commit.plan_writes(diff, RESOLVED, CFG, today=MONDAY)
               if i.surface == "calendar"]
    store = FakeStore(calendars=CALENDARS, events=[dict(event_1530)])
    result = commit.write_calendar(intents, store, today=MONDAY)
    assert result.ok and store.created_calls == 1
    assert sorted(e["start"].strftime("%H:%M") for e in store.query_events(None, None)) == [
        "15:30", "16:00",
    ]


def test_a_client_title_never_reaches_the_calendar_write():
    import main

    rows = [
        {"id": SESSION_ID, "title": "CLIENT TITLE"},
        {"id": "Some Other Block", "title": "CLIENT TITLE"},
    ]
    sched = [{"id": SESSION_ID, "name": LEGACY_NAME, "title": DISPLAY_TITLE}]
    main._restamp_display_titles(rows, sched)
    assert rows[0]["title"] == DISPLAY_TITLE
    assert "title" not in rows[1]
