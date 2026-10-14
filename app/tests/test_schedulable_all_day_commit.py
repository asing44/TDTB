"""Unit 2 — all-day schedulable calendars: commit eligibility, shadow, runtime.

A server-indexed calendar row (source ``calendar``, identity
``calendar:<event_id>:<event_date>``, path ``calendar://<event_id>/<event_date>``)
previews and commits as exactly ONE timed ``⬜ Blocks`` calendar create-event:
no Todoist task, no PHEP mirror, and never a write to the source calendar.

TDD against the agreed public seams. Fakes only: no network, no EventKit, no
source writes.
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))
import app_config  # noqa: E402
import calendar_bridge as cb  # noqa: E402
import main as main_mod  # noqa: E402
import runstate  # noqa: E402
import runtime_actions as ra  # noqa: E402
import shadow  # noqa: E402
import tdtb_gather as gather  # noqa: E402

GIFT_TITLE = "Buy birthday gifts for Evan and Nate"
OPTED_IN = "Evan & Nate"
EVENT_ID = "EV-1"


def _today() -> date:
    return gather.effective_date(datetime.now())


def _cal_row(
    name: str = GIFT_TITLE, *, event_id: str = EVENT_ID, day: date | None = None,
    blocks: int = 1,
) -> dict:
    """The Unit-1 assigned-row shape for a schedulable all-day event."""
    d = (day or _today()).isoformat()
    return {
        "name": name, "source": "calendar", "assigned": True, "blocks": blocks,
        "identity": f"calendar:{event_id}:{d}",
        "path": f"calendar://{event_id}/{d}",
        "event_id": event_id, "calendar_id": "CAL-EVAN",
        "calendar_title": OPTED_IN, "capacity_class": "fixed",
    }


def _submitted(row: dict, **over: object) -> dict:
    out = {
        "name": row["name"], "identity": row["identity"], "path": row["path"],
        "source": row.get("source") or "calendar",
    }
    out.update(over)
    return out


def _write_index(day: date, assigned=(), suggested=()) -> None:
    runstate.write_digest_index(day, main_mod.build_digest_index(
        {"assigned": list(assigned), "suggested": list(suggested)}))


def _sequence(name: str = GIFT_TITLE, start: str = "09:00", end: str = "09:30") -> dict:
    return {"sequence": [{"id": name, "start": start, "end": end, "zone": "any"}]}


# ---------------------------------------------------------------------------
# Config / clients
# ---------------------------------------------------------------------------

MINIMAL_CFG = {
    "defaults": {
        "eod": "11:45 PM",
        "anchor": {"round_to_minutes": 15},
        "buffering": {"off_pct": 0},
    },
    "anchored_blocks": [],
    "presets": [],
    "calendar": {
        "titles": [{"Logical name": "blocks", "BusyCal title": "⬜ Blocks"}],
    },
}


def _write_local(doc: dict) -> None:
    path = app_config.config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")


@pytest.fixture(autouse=True)
def _no_live_state(monkeypatch):
    """No test here may touch a real Todoist/EventKit read path.

    Models the live condition that exposed the Unit-2 scoping defect: the
    opted-in source calendar still holds the original all-day event whose
    title names the plan row (``src-1`` on ``CAL-EVAN``). The diff must ignore
    it because it lives on the source calendar, not on TDTB's own
    ``cal-blocks``.
    """
    monkeypatch.setattr(shadow, "gather_live_state", lambda config: {
        "todoist_tasks": [],
        "calendar_events": [_source_event()],
        "calendar_own_ids": {"cal-blocks"},
        "vault_frontmatter": {}, "daily_note_text": None,
    })


@pytest.fixture
def client():
    app = main_mod.create_app()
    c = TestClient(app)
    c.app_token = app.state.token
    return c


def _auth(client: TestClient) -> dict:
    return {"X-TDTB-Token": client.app_token}


def _confirm_day_setup(client: TestClient) -> None:
    assert client.post("/day-setup", json={"anchor": "09:00"},
                      headers=_auth(client)).status_code == 200


def _source_event() -> dict:
    day = _today()
    start = datetime(day.year, day.month, day.day)
    return {
        "id": "src-1", "title": GIFT_TITLE, "all_day": True,
        "start": start, "end": start + timedelta(days=1),
        "calendar_id": "CAL-EVAN",
    }


def _blocks_event(event_id: str = "blk-1", *, start: str = "09:00",
                  end: str = "09:30") -> dict:
    """The timed ⬜ Blocks event a successful Unit-2 commit lands."""
    day = _today()
    h, m = (int(x) for x in start.split(":"))
    e_h, e_m = (int(x) for x in end.split(":"))
    begin = datetime(day.year, day.month, day.day, h, m)
    finish = datetime(day.year, day.month, day.day, e_h, e_m)
    return {
        "id": event_id, "title": GIFT_TITLE, "all_day": False,
        "start": begin, "end": finish, "calendar_id": "cal-blocks",
    }


class FakeTodoist:
    """Records calls; a calendar-only plan must never reach it."""

    def __init__(self):
        self.created_calls = 0
        self.calls: list[tuple] = []

    def get_filter_tasks(self, query, limit=None):
        self.calls.append(("get_filter_tasks", query))
        return []

    def get_task(self, task_id):
        self.calls.append(("get_task", task_id))
        return {"id": task_id}

    def create_task(self, content, **kwargs):
        self.created_calls += 1
        self.calls.append(("create_task", content))
        return {"id": "t-new", "content": content}

    def update_task(self, task_id, **fields):
        self.calls.append(("update_task", task_id))
        return {"id": task_id}

    def reschedule_task(self, task_id, due_string):
        self.calls.append(("reschedule_task", task_id))
        return {"id": task_id}


class FakeStore:
    """In-memory EventStore with the TDTB output calendar + the source calendar."""

    def __init__(self, events=None):
        self.cals = [
            cb.CalendarInfo("⬜ Blocks", "cal-blocks", True, "iCloud"),
            cb.CalendarInfo(OPTED_IN, "CAL-EVAN", True, "iCloud"),
        ]
        self.events = {e["id"]: e for e in (events or [])}
        self._seq = 0
        self.created: list[str] = []

    def calendars(self):
        return list(self.cals)

    def query_events(self, start, end, calendar_ids=None):
        return list(self.events.values())

    def create_event(self, spec: cb.EventSpec):
        cb.assert_write_target(spec.calendar_id, self.cals)
        self._seq += 1
        eid = f"e{self._seq}"
        self.events[eid] = {
            "id": eid, "title": spec.title, "start": spec.start,
            "end": spec.end, "calendar_id": spec.calendar_id,
        }
        self.created.append(eid)
        return eid

    def get_event(self, event_id):
        return self.events.get(event_id)


def _commit(client: TestClient, row: dict, *, mode: str, sequence=None):
    return client.post(
        f"/commit?mode={mode}",
        headers=_auth(client),
        json={
            "digest": {"assigned": [row]},
            "sequence": sequence or _sequence(row["name"]),
            "config": {},
        },
    )


# ===========================================================================
# 1. _route_source_identity — canonical calendar identity only
# ===========================================================================

class TestRouteSourceIdentity:
    def _row(self, **over):
        base = {
            "source": "calendar", "assigned": True, "blocks": 1,
            "name": GIFT_TITLE,
            "identity": f"calendar:{EVENT_ID}:{_today().isoformat()}",
            "path": f"calendar://{EVENT_ID}/{_today().isoformat()}",
        }
        base.update(over)
        return base

    def test_canonical_calendar_row_resolves_to_its_identity(self):
        assert main_mod._route_source_identity(self._row()) == (
            "calendar", f"calendar:{EVENT_ID}:{_today().isoformat()}"
        )

    def test_path_mismatch_is_rejected(self):
        assert main_mod._route_source_identity(
            self._row(path=f"calendar://OTHER/{_today().isoformat()}")
        ) is None

    def test_non_calendar_path_is_rejected(self):
        assert main_mod._route_source_identity(
            self._row(source="calendar", path="todoist://T1")
        ) is None

    def test_todoist_id_contradicts_a_calendar_identity(self):
        assert main_mod._route_source_identity(self._row(todoist_id="T1")) is None

    def test_identity_without_calendar_date_is_rejected(self):
        assert main_mod._route_source_identity(self._row(identity="calendar:EV-1")) is None


# ===========================================================================
# 2. Shadow preview
# ===========================================================================

class TestShadowPreview:
    def test_calendar_row_previews_exactly_one_blocks_create(self, client):
        row = _cal_row()
        _write_index(_today(), assigned=[row])

        resp = _commit(client, _submitted(row), mode="shadow")

        assert resp.status_code == 200, resp.text
        entries = [e for e in resp.json()["entries"]
                   if e["manifest"]["name"] == GIFT_TITLE]
        assert len(entries) == 1
        assert entries[0]["classification"] == "would-create"
        m = entries[0]["manifest"]
        assert (m["step"], m["system"], m["action"]) == ("D", "calendar", "create-event")
        assert m["routing"] == "⬜ Blocks"
        assert (m["time"], m["duration_min"]) == ("09:00", 30)
        # Never a Todoist task or PHEP mirror for a calendar row.
        assert [e for e in resp.json()["entries"] if e["manifest"]["system"] == "todoist"] == []

    def test_zero_block_calendar_row_writes_nothing(self, client):
        row = _cal_row(blocks=0)
        _write_index(_today(), assigned=[row])
        resp = _commit(client, _submitted(row), mode="shadow")
        assert resp.status_code == 200, resp.text
        assert [e for e in resp.json()["entries"]
                if e["manifest"]["name"] == GIFT_TITLE] == []

    def test_missing_blocks_calendar_row_writes_nothing(self):
        row = _cal_row()
        row.pop("blocks")
        entries = shadow.build_plan_manifest(
            {"assigned": [row]}, _sequence(), {})
        assert [e for e in entries if e.system == "calendar"] == []


# ===========================================================================
# 2b. Calendar-source diff scoping (FEEDBACK-26)
# ===========================================================================

class TestCalendarSourceDiffScoping:
    """A calendar-source create must be diffed only against TDTB's own write
    calendar. An imported source event sharing the row title is never our
    write, so it must not collapse the create into a no-op."""

    def _cal_entries(self, events, own_ids):
        row = _cal_row()
        manifest = shadow.build_plan_manifest(
            {"assigned": [row]}, _sequence(), {})
        state = {
            "todoist_tasks": [], "calendar_events": list(events),
            "calendar_own_ids": own_ids,
        }
        return [e for e in shadow.diff_against_live(manifest, state).entries
                if e.manifest.system == "calendar"]

    def test_source_all_day_event_on_another_calendar_is_a_create(self):
        [entry] = self._cal_entries([_source_event()], {"cal-blocks"})
        assert entry.classification == shadow.CREATE

    def test_timed_blocks_event_with_same_title_and_start_is_a_noop(self):
        [entry] = self._cal_entries(
            [_source_event(), _blocks_event()], {"cal-blocks"})
        assert entry.classification == shadow.NOOP
        assert entry.detail.get("event_id") == "blk-1"

    def test_gather_live_state_resolves_configured_write_calendar_ids(
        self, monkeypatch, tmp_path
    ):
        """The scoping inventory comes from the real gather, not a fixture."""
        token_path = tmp_path / "env"
        token_path.write_text("TODOIST_TOKEN=abc123\n", encoding="utf-8")
        monkeypatch.setattr(shadow, "TOKEN_ENV_PATH", token_path)
        monkeypatch.setattr(shadow.todoist_client, "load_token", lambda p: "abc123")

        class _NoTodoist:
            def __init__(self, token):
                raise RuntimeError("no live Todoist")

        monkeypatch.setattr(shadow.todoist_client, "TodoistClient", _NoTodoist)
        monkeypatch.setattr(cb, "_shared_store", None)

        class _Store:
            def auth_status(self):
                return "fullAccess"

            def calendars(self):
                return [
                    cb.CalendarInfo("⬜ Blocks", "cal-blocks", True, "iCloud"),
                    cb.CalendarInfo(OPTED_IN, "CAL-EVAN", True, "iCloud"),
                ]

            def query_events(self, start, end, calendar_ids=None):
                return [_source_event()]

        monkeypatch.setattr(cb, "EventStore", _Store)

        state = shadow.gather_live_state({
            "Calendar Titles": [{"Logical name": "blocks",
                                 "BusyCal title": "⬜ Blocks"}],
        })

        assert state["calendar_own_ids"] == {"cal-blocks"}
        assert [e["calendar_id"] for e in state["calendar_events"]] == ["CAL-EVAN"]


# ===========================================================================
# 3. Live commit
# ===========================================================================

class TestLiveCommit:
    def _prepare(self, client, store, todoist):
        _write_local({
            "version": 1, "sources": {"mode": "live"}, **MINIMAL_CFG,
        })
        _write_index(_today(), assigned=[_cal_row()])
        _confirm_day_setup(client)
        client.app.state.build_commit_clients = lambda cfg: (todoist, store)

    def test_calendar_row_commits_one_blocks_event_and_no_todoist(self, client):
        store = FakeStore(events=[_source_event()])
        todoist = FakeTodoist()
        self._prepare(client, store, todoist)
        before_source = dict(store.events["src-1"])

        resp = _commit(client, _submitted(_cal_row()), mode="live")

        assert resp.status_code == 200, resp.text
        assert len(store.created) == 1
        created = store.events[store.created[0]]
        assert created["title"] == GIFT_TITLE
        assert created["calendar_id"] == "cal-blocks"
        assert created["start"].strftime("%H:%M") == "09:00"
        assert created["end"].strftime("%H:%M") == "09:30"
        # No Todoist task, no PHEP mirror, and the source event is untouched.
        assert todoist.created_calls == 0
        assert store.events["src-1"] == before_source
        assert not any(store.events[i]["calendar_id"] == "CAL-EVAN" for i in store.created)

    def test_recommit_does_not_create_a_second_blocks_event(self, client):
        store = FakeStore()
        todoist = FakeTodoist()
        self._prepare(client, store, todoist)

        assert _commit(client, _submitted(_cal_row()), mode="live").status_code == 200
        assert _commit(client, _submitted(_cal_row()), mode="live").status_code == 200

        assert len(store.created) == 1
        assert todoist.created_calls == 0

    def test_timed_blocks_event_makes_the_row_a_noop_without_a_second_event(
        self, client, monkeypatch
    ):
        store = FakeStore(events=[_source_event(), _blocks_event()])
        todoist = FakeTodoist()
        self._prepare(client, store, todoist)
        monkeypatch.setattr(shadow, "gather_live_state", lambda config: {
            "todoist_tasks": [],
            "calendar_events": list(store.events.values()),
            "calendar_own_ids": {"cal-blocks"},
            "vault_frontmatter": {}, "daily_note_text": None,
        })
        before_source = dict(store.events["src-1"])

        resp = _commit(client, _submitted(_cal_row()), mode="live")

        assert resp.status_code == 200, resp.text
        assert store.created == []
        assert store.events["src-1"] == before_source


# ===========================================================================
# 4. Forged rows are refused (422), nothing written
# ===========================================================================

class TestForgedRowsRefused:
    def _assert_refused(self, client, row, *, assigned=(), suggested=()):
        if assigned or suggested:
            _write_index(_today(), assigned=assigned, suggested=suggested)
        resp = _commit(client, row, mode="shadow")
        assert resp.status_code == 422, resp.text
        return resp

    def test_row_not_in_index_is_refused(self, client):
        row = _cal_row()
        self._assert_refused(client, _submitted(row), assigned=[])

    def test_indexed_suggested_row_is_not_assignable(self, client):
        row = _cal_row()
        self._assert_refused(
            client, _submitted(row), assigned=[], suggested=[row],
        )

    def test_identity_mismatch_is_refused(self, client):
        row = _cal_row()
        forged = _submitted(row, identity=f"calendar:{EVENT_ID}:1999-01-01")
        self._assert_refused(client, forged, assigned=[row])

    def test_path_mismatch_is_refused(self, client):
        row = _cal_row()
        forged = _submitted(row, path=f"calendar://OTHER/{_today().isoformat()}")
        self._assert_refused(client, forged, assigned=[row])

    def test_todoist_id_on_a_calendar_row_is_refused(self, client):
        row = _cal_row()
        forged = _submitted(row, todoist_id="T1")
        self._assert_refused(client, forged, assigned=[row])

    def test_wrong_source_row_is_refused(self, client):
        row = _cal_row()
        forged = {"name": GIFT_TITLE, "path": "todoist://T1", "todoist_id": "T1",
                  "source": "todoist"}
        self._assert_refused(client, forged, assigned=[row])

    def test_dropped_identity_is_refused(self, client):
        row = _cal_row()
        _write_index(_today(), assigned=[row])

        def _drop(state):
            state["dropped"] = [{"identity": row["identity"],
                                 "dropped_at": "2026-07-14T09:00:00-07:00"}]

        runstate.update_runstate(_today(), _drop)
        resp = _commit(client, _submitted(row), mode="shadow")
        assert resp.status_code == 422, resp.text


# ===========================================================================
# 5. Name collision (generalized) — never retime the same-name Todoist task
# ===========================================================================

class TestNameCollision:
    def test_calendar_and_todoist_rows_sharing_a_name_fail_closed(self):
        todoist = {"name": GIFT_TITLE, "source": "todoist",
                   "path": "todoist://T1", "todoist_id": "T1"}
        cal = _cal_row()
        with pytest.raises(ValueError, match=GIFT_TITLE):
            shadow.build_plan_manifest(
                {"assigned": [todoist, cal]}, _sequence(), {})

    def test_route_refuses_the_collision_without_writing(self, client):
        todoist = {"name": GIFT_TITLE, "source": "todoist",
                   "path": "todoist://T1", "todoist_id": "T1"}
        _write_index(_today(), assigned=[todoist, _cal_row()])
        resp = client.post(
            "/commit?mode=shadow",
            headers=_auth(client),
            json={
                "digest": {"assigned": [todoist, _submitted(_cal_row())]},
                "sequence": _sequence(),
                "config": {},
            },
        )
        assert resp.status_code == 422, resp.text


# ===========================================================================
# 6. Runtime verbs (decision b)
# ===========================================================================

class TestRuntimeVerbs:
    def _staged_target(self):
        row = _cal_row()
        _write_index(_today(), assigned=[row])
        target = ra.resolve_target(_today(), GIFT_TITLE)
        return row, target

    def test_resolve_never_exposes_the_source_event_id(self):
        _row, target = self._staged_target()
        assert target.get("source") == "calendar"
        assert target.get("identity") == _row["identity"]
        assert "event_id" not in target

    def test_drop_from_plan_keys_the_calendar_identity(self):
        row, target = self._staged_target()
        steps = ra.plan_steps("drop_from_plan", target, {}, _today())
        assert [s["kind"] for s in steps] == ["runstate.drop"]
        assert ra.drop_identity_of(steps[0]["item"]) == row["identity"]

    def test_unassign_keys_the_calendar_identity_via_runstate(self):
        row, target = self._staged_target()
        steps = ra.plan_steps("unassign", target, {}, _today())
        assert [s["kind"] for s in steps] == ["runstate.drop"]
        assert ra.drop_identity_of(steps[0]["item"]) == row["identity"]

    def test_apply_drop_records_the_calendar_identity(self):
        row, _target = self._staged_target()
        action = ra.apply_action(
            _today(), "drop_from_plan", GIFT_TITLE, {}, todoist=None, store=None)
        assert action["status"] == "applied"
        assert row["identity"] in {d["identity"] for d in runstate.read_dropped(_today())}

    @pytest.mark.parametrize("verb", ["complete", "done", "delete",
                                      "delete_permanent", "assign"])
    def test_source_writing_verbs_refuse_a_calendar_row(self, verb):
        _row, target = self._staged_target()
        with pytest.raises(ra.RuntimeActionError):
            ra.plan_steps(verb, target, {}, _today())

    def test_capacities_collision_test_still_holds(self):
        """Guard against over-generalizing the collision rule off Capacities."""
        todoist = {"name": "Press", "path": "todoist://t1", "source": "todoist"}
        cap = {"name": "Press", "source": "capacities",
               "path": "capacities://space-1/object-1",
               "identity": "capacities:space-1:RootTask:object-1"}
        with pytest.raises(ValueError, match="Press"):
            shadow.build_plan_manifest({"assigned": [todoist, cap]}, _sequence("Press"), {})
