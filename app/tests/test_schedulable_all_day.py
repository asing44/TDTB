"""Unit 1 — all-day schedulable calendars (backend INTAKE only).

A user-chosen LIST of calendars turns its all-day events into schedulable plan
rows (default 30 min / 1 block) instead of all-day strip entries. The original
source event is NEVER modified; every other calendar's all-day events keep the
frozen contract-18 behaviour. Commit/eligibility/shadow/runtime-actions and the
frontend are units 2-3 and are intentionally absent here.

TDD: these tests are written against the agreed public seams:
  - ``calendar_bridge.normalize_schedulable_all_day``
  - ``app_config.sections_from_document`` (``Schedulable All-Day Calendars``)
  - ``external_sources.fetch_calendar_decisions`` intake branch
  - ``external_sources.split_schedulable_all_day``
  - ``external_sources.exclude_already_committed``
  - the /plan-inputs and /capacity-preview routes.

Fakes only: no network, no EventKit, no source writes.
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))
import app_config  # noqa: E402
import calendar_bridge as cb  # noqa: E402
import external_sources as ext  # noqa: E402
import main as main_mod  # noqa: E402
import runstate as runstate_mod  # noqa: E402
# The app's gather module is path-shimmed as ``tdtb_gather`` (there is no
# top-level ``gather`` module; a bare ``import gather`` resolves to the
# ``app/gather`` namespace package and has no ``effective_date``). Every other
# suite test uses this same alias.
import tdtb_gather as gather  # noqa: E402

TODAY = date(2026, 7, 14)
OPTED_IN = "Evan & Nate"
GIFT_TITLE = "Buy birthday gifts for Evan and Nate"


# ---------------------------------------------------------------------------
# Fakes (no EventKit)
# ---------------------------------------------------------------------------

class _Cal:
    def __init__(self, title, identifier, **attrs):
        self.title = title
        self.identifier = identifier
        self.__dict__.update(attrs)


class _Store:
    def __init__(self, events, calendars=None):
        self._events = events
        self._calendars = calendars or [_Cal(OPTED_IN, "CAL-EVAN")]

    def auth_status(self):
        return "authorized"

    def calendars(self):
        return self._calendars

    def query_events(self, start, end, calendar_ids=None):
        return self._events


def _all_day(title=GIFT_TITLE, cal="CAL-EVAN", event_id="EV-1"):
    ev = {
        "title": title,
        "start": datetime(2026, 7, 14, 0, 0),
        "end": datetime(2026, 7, 15, 0, 0),
        "calendar_id": cal,
        "all_day": True,
    }
    if event_id is not None:
        ev["event_id"] = event_id
    return ev


def _timed(title, cal="CAL-X", start=(9, 0), end=(10, 0)):
    return {
        "title": title,
        "start": datetime(2026, 7, 14, *start),
        "end": datetime(2026, 7, 14, *end),
        "calendar_id": cal,
    }


# ===========================================================================
# 1. normalize_schedulable_all_day — config parsing fails safe
# ===========================================================================

class TestNormalizeSchedulableAllDay:
    def test_list_of_titles_casefolded(self):
        assert cb.normalize_schedulable_all_day([OPTED_IN]) == {OPTED_IN.casefold()}

    def test_exact_title_or_identifier_both_accepted(self):
        assert cb.normalize_schedulable_all_day(["Evan & Nate", "CAL-EVAN"]) == {
            "evan & nate", "cal-evan",
        }

    def test_empty_list_opts_nothing_in(self):
        assert cb.normalize_schedulable_all_day([]) == set()

    def test_missing_none_and_non_list_fail_safe(self):
        assert cb.normalize_schedulable_all_day(None) == set()
        assert cb.normalize_schedulable_all_day(42) == set()
        assert cb.normalize_schedulable_all_day(True) == set()
        # A bare string is not the list schema: fail safe, do not opt in.
        assert cb.normalize_schedulable_all_day(OPTED_IN) == set()
        # A dict is not the list schema either (unlike Disabled Calendars).
        assert cb.normalize_schedulable_all_day({OPTED_IN: True}) == set()

    def test_row_dicts_and_comma_strings_contribute_every_field(self):
        raw = [
            {"Title": "Evan & Nate", "Identifier": "CAL-EVAN"},
            "Family, Holidays",
        ]
        assert cb.normalize_schedulable_all_day(raw) == {
            "evan & nate", "cal-evan", "family", "holidays",
        }

    def test_duplicates_collapse(self):
        assert cb.normalize_schedulable_all_day([OPTED_IN, OPTED_IN.upper()]) == {
            OPTED_IN.casefold(),
        }

    def test_blank_entries_ignored(self):
        assert cb.normalize_schedulable_all_day(["", "   ", None]) == set()


# ===========================================================================
# 2. app_config.sections_from_document
# ===========================================================================

class TestConfigSections:
    def test_calendar_key_maps_to_section(self):
        out = app_config.sections_from_document({
            "version": 1,
            "calendar": {"schedulable_all_day": [OPTED_IN]},
        })
        assert out["Schedulable All-Day Calendars"] == [OPTED_IN]

    def test_missing_key_absent(self):
        out = app_config.sections_from_document({
            "version": 1,
            "calendar": {"titles": []},
        })
        assert "Schedulable All-Day Calendars" not in out

    def test_machine_local_layer_merges_into_repo_calendar(self):
        repo = {"version": 1, "calendar": {"titles": [{"Logical name": "blocks"}]}}
        local = {"version": 1, "calendar": {"schedulable_all_day": [OPTED_IN]}}
        merged = app_config._layer(repo, local)
        out = app_config.sections_from_document(merged)
        assert out["Calendar Titles"] == [{"Logical name": "blocks"}]
        assert out["Schedulable All-Day Calendars"] == [OPTED_IN]


# ===========================================================================
# 3. fetch_calendar_decisions intake branch
# ===========================================================================

def _opted_cfg(**extra):
    return {"schedulable_all_day": [OPTED_IN], **extra}


class TestIntake:
    def test_opted_in_all_day_becomes_schedulable_block(self):
        store = _Store([_all_day()])
        blocks, _w, decisions = ext.fetch_calendar_decisions(
            store, _opted_cfg(), TODAY
        )
        assert len(blocks) == 1
        block = blocks[0]
        assert block["Block"] == GIFT_TITLE
        assert block["schedulable_all_day"] is True
        assert block["all_day"] is True
        assert block["event_id"] == "EV-1"
        assert block["identity"] == f"calendar:EV-1:{TODAY.isoformat()}"
        assert block["path"] == f"calendar://EV-1/{TODAY.isoformat()}"
        assert [d for d in decisions if d["reason_code"] == "included_schedulable_all_day"]

    def test_unlisted_calendar_all_day_unchanged(self):
        store = _Store([_all_day()], [_Cal("Other", "CAL-OTHER")])
        blocks, _w, decisions = ext.fetch_calendar_decisions(
            store, _opted_cfg(), TODAY
        )
        assert len(blocks) == 1
        assert "schedulable_all_day" not in blocks[0]
        assert blocks[0]["all_day"] is True
        assert any(d["reason_code"] == "included_all_day" for d in decisions)

    def test_opted_in_without_stable_id_unchanged(self):
        store = _Store([_all_day(event_id=None)])
        blocks, _w, _d = ext.fetch_calendar_decisions(store, _opted_cfg(), TODAY)
        assert len(blocks) == 1
        assert "schedulable_all_day" not in blocks[0]
        assert blocks[0]["all_day"] is True

    def test_disabled_calendar_wins_first(self):
        store = _Store([_all_day()])
        cfg = _opted_cfg(calendar_disabled=[OPTED_IN])
        blocks, warnings, decisions = ext.fetch_calendar_decisions(store, cfg, TODAY)
        assert blocks == []
        assert any("disabled" in w.lower() for w in warnings)
        assert any(d["reason_code"] == "excluded_disabled_calendar" for d in decisions)

    def test_default_without_optin_is_unchanged(self):
        store = _Store([_all_day()])
        blocks, _w, _d = ext.fetch_calendar_decisions(store, {}, TODAY)
        assert len(blocks) == 1
        assert "schedulable_all_day" not in blocks[0]

    def test_ignored_class_wins_first(self):
        # The TDTB own-write calendar is always capacity_class "ignored" even
        # for all-day events: classification beats schedulability.
        store = _Store(
            [_all_day(cal="CAL-OWN")],
            [_Cal("Blocks", "CAL-OWN")],
        )
        cfg = _opted_cfg(calendar_ids={"Blocks": "CAL-OWN"})
        blocks, _w, _d = ext.fetch_calendar_decisions(store, cfg, TODAY)
        assert len(blocks) == 1
        assert blocks[0]["capacity_class"] == "ignored"
        assert "schedulable_all_day" not in blocks[0]

    def test_explicit_ignored_class_does_not_block_opt_in(self):
        # A family calendar the operator classed "ignored" (no capacity) may
        # still be opted in: the guard protects own-write/quarantined only.
        store = _Store([_all_day()])
        cfg = _opted_cfg(calendar_capacity_classes=[{"BusyCal title": OPTED_IN,
                                                     "Class": "ignored"}])
        blocks, _w, decisions = ext.fetch_calendar_decisions(store, cfg, TODAY)
        assert len(blocks) == 1
        assert blocks[0]["schedulable_all_day"] is True
        assert any(d["reason_code"] == "included_schedulable_all_day"
                   for d in decisions)

    def test_quarantined_class_wins_first(self):
        store = _Store([_all_day()])
        cfg = _opted_cfg(calendar_capacity_classes=[{"BusyCal title": OPTED_IN,
                                                     "Class": "quarantined"}])
        blocks, _w, _d = ext.fetch_calendar_decisions(store, cfg, TODAY)
        assert len(blocks) == 1
        assert "schedulable_all_day" not in blocks[0]


# ===========================================================================
# 4. split_schedulable_all_day
# ===========================================================================

class TestSplit:
    def _blocks(self):
        store = _Store([_all_day(), _timed("Dentist")])
        blocks, _w, _d = ext.fetch_calendar_decisions(store, _opted_cfg(), TODAY)
        return blocks

    def test_split_removes_schedulable_from_busy_and_returns_rows(self):
        busy, rows = ext.split_schedulable_all_day(self._blocks())
        assert [b["Block"] for b in busy] == ["Dentist"]
        assert len(rows) == 1
        row = rows[0]
        assert row["name"] == GIFT_TITLE
        assert row["source"] == "calendar"
        assert row["assigned"] is True
        assert row["blocks"] == 1
        assert row["identity"] == f"calendar:EV-1:{TODAY.isoformat()}"
        assert row["path"] == f"calendar://EV-1/{TODAY.isoformat()}"
        assert row["event_id"] == "EV-1"

    def test_split_without_optins_is_a_passthrough(self):
        blocks = [
            {"Block": "Holiday", "all_day": True},
            {"Block": "Dentist", "Start": "09:00", "End": "10:00"},
        ]
        busy, rows = ext.split_schedulable_all_day(blocks)
        assert busy == blocks
        assert rows == []


# ===========================================================================
# 5. exclude_already_committed
# ===========================================================================

class TestDedup:
    def _row(self, name=GIFT_TITLE):
        return {
            "name": name, "source": "calendar", "assigned": True, "blocks": 1,
            "identity": f"calendar:EV-1:{TODAY.isoformat()}",
            "path": f"calendar://EV-1/{TODAY.isoformat()}",
            "event_id": "EV-1", "calendar_id": "CAL-EVAN",
            "calendar_title": OPTED_IN, "capacity_class": "fixed",
        }

    def _own_block(self, title, **over):
        block = {
            "Block": title, "Start": "09:00", "End": "09:30",
            "source": "calendar", "calendar_id": "CAL-OWN",
            "calendar_title": "Blocks", "capacity_class": "ignored",
            "own_write": True,
        }
        block.update(over)
        return block

    def test_title_match_on_own_blocks_event_drops_row(self):
        kept, decisions = ext.exclude_already_committed(
            [self._row()], [self._own_block(GIFT_TITLE)]
        )
        assert kept == []
        assert len(decisions) == 1
        assert decisions[0]["reason_code"] == "excluded_already_committed"
        assert decisions[0]["decision"] == "excluded"

    def test_no_matching_block_keeps_row(self):
        kept, decisions = ext.exclude_already_committed(
            [self._row()], [self._own_block("Something else")]
        )
        assert len(kept) == 1 and decisions == []

    def test_non_own_calendar_does_not_dedup(self):
        # An explicitly "ignored"-class family calendar is not TDTB's own
        # write calendar: a same-title timed event there must not hide a row.
        block = self._own_block(GIFT_TITLE)
        block.pop("own_write")
        kept, decisions = ext.exclude_already_committed([self._row()], [block])
        assert len(kept) == 1 and decisions == []

    def test_all_day_own_block_does_not_dedup(self):
        block = self._own_block(GIFT_TITLE)
        block.pop("Start")
        block.pop("End")
        block["all_day"] = True
        kept, _decisions = ext.exclude_already_committed([self._row()], [block])
        assert len(kept) == 1


# ===========================================================================
# 6. /plan-inputs end to end
# ===========================================================================

def _write_local(doc: dict) -> None:
    path = app_config.config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")


MINIMAL_CFG = {
    "defaults": {
        "eod": "11:45 PM",
        "anchor": {"round_to_minutes": 15},
        "buffering": {"off_pct": 0},
    },
    "anchored_blocks": [],
    "presets": [],
}


def _client(todoist=None, store=None) -> TestClient:
    app = main_mod.create_app()
    app.state.build_read_clients = lambda cfg: (todoist, store)
    return TestClient(app)


@pytest.fixture
def schedulable_cfg():
    _write_local({
        "version": 1,
        "sources": {"mode": "live"},
        **MINIMAL_CFG,
        "calendar": {"schedulable_all_day": [OPTED_IN]},
    })


def test_plan_inputs_opts_in_all_day_as_assigned_row(schedulable_cfg):
    store = _Store([_all_day()])
    body = _client(store=store).get("/plan-inputs").json()
    names = [r["name"] for r in body["digest"]["assigned"]]
    assert GIFT_TITLE in names
    row = next(r for r in body["digest"]["assigned"] if r["name"] == GIFT_TITLE)
    assert row["source"] == "calendar"
    assert row["blocks"] == 1
    assert row["identity"] == f"calendar:EV-1:{today_iso()}"
    assert row["path"] == f"calendar://EV-1/{today_iso()}"
    # The all-day strip no longer carries the opted-in event.
    assert GIFT_TITLE not in [b["Block"] for b in body["anchored_blocks"]]


def test_plan_inputs_unlisted_calendar_stays_on_the_strip(schedulable_cfg):
    store = _Store([_all_day()], [_Cal("Other", "CAL-OTHER")])
    body = _client(store=store).get("/plan-inputs").json()
    assert GIFT_TITLE in [b["Block"] for b in body["anchored_blocks"]]
    assert GIFT_TITLE not in [r["name"] for r in body["digest"]["assigned"]]


def test_plan_inputs_digest_index_retains_identity_source_path(schedulable_cfg):
    store = _Store([_all_day()])
    _client(store=store).get("/plan-inputs")
    today = gather.effective_date(datetime.now())
    raw = json.loads(runstate_mod.digest_index_read_path(today).read_text(encoding="utf-8"))
    row = next(i for i in raw["items"] if i["name"] == GIFT_TITLE)
    assert row["source"] == "calendar"
    assert row["identity"] == f"calendar:EV-1:{today.isoformat()}"
    assert row["path"] == f"calendar://EV-1/{today.isoformat()}"


def test_plan_inputs_ignore_list_by_name_drops_row():
    _write_local({
        "version": 1, "sources": {"mode": "live"}, **MINIMAL_CFG,
        "calendar": {"schedulable_all_day": [OPTED_IN]},
        "ignore": {"names": [GIFT_TITLE]},
    })
    store = _Store([_all_day()])
    body = _client(store=store).get("/plan-inputs").json()
    assert GIFT_TITLE not in [r["name"] for r in body["digest"]["assigned"]]


def test_plan_inputs_ignore_list_by_path_drops_row():
    _write_local({
        "version": 1, "sources": {"mode": "live"}, **MINIMAL_CFG,
        "calendar": {"schedulable_all_day": [OPTED_IN]},
        "ignore": {"paths": [f"calendar://EV-1/{today_iso()}"]},
    })
    store = _Store([_all_day()])
    body = _client(store=store).get("/plan-inputs").json()
    assert GIFT_TITLE not in [r["name"] for r in body["digest"]["assigned"]]


def test_plan_inputs_dedupes_already_committed_timed_own_block(schedulable_cfg):
    config = {
        "version": 1,
        "sources": {"mode": "live"},
        **MINIMAL_CFG,
        "calendar": {
            "titles": [{"Logical name": "blocks", "BusyCal title": "Blocks"}],
            "schedulable_all_day": [OPTED_IN],
        },
    }
    _write_local(config)
    store = _Store(
        [_all_day(), _timed(GIFT_TITLE, cal="CAL-OWN")],
        [_Cal(OPTED_IN, "CAL-EVAN"), _Cal("Blocks", "CAL-OWN")],
    )
    body = _client(store=store).get("/plan-inputs").json()
    assert GIFT_TITLE not in [r["name"] for r in body["digest"]["assigned"]]
    assert any(
        d.get("reason_code") == "excluded_already_committed"
        for d in body["calendar_decisions"]
    )


def test_capacity_preview_consistent_with_intake(schedulable_cfg):
    store = _Store([_all_day()])
    resp = _client(store=store).get("/capacity-preview")
    assert resp.status_code == 200
    body = resp.json()
    # The opted-in all-day event leaves the busy frame; it adds no fixed cost
    # and does not crash the second consumer path.
    assert body["segments"]["fixed"] == 0


def today_iso() -> str:
    return gather.effective_date(datetime.now()).isoformat()
