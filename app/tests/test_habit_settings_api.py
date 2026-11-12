"""Route tests for the habit settings API and the Grit refresh route (U3).

``GET /settings/habits`` is a tokenless local read that writes nothing and
NEVER runs the operator's Shortcut — its inventory is reconciled against the
stored Grit cache only. ``POST /settings/habits/save`` is token-guarded,
revision-checked, and full-replacement. ``POST /habits/grit/refresh`` runs the
read through an injected runner and answers the same payload plus a
``refresh`` status.

Absolute rules pinned here: no test spawns a process (the autouse guard fails
the run if ``subprocess.run`` is reached), no test touches the real Grit
Shortcut, the live service, or the operator's app home.
"""
from __future__ import annotations

import datetime
import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))

import grit_habits as gh  # noqa: E402
import habit_settings as hs  # noqa: E402
import main as main_mod  # noqa: E402
import tdtb_gather as gather  # noqa: E402

GENERATED_AT = "2026-10-14T07:30:00-07:00"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _today() -> str:
    """The app's own logical day (midnight-2am is still yesterday)."""
    return gather.effective_date(datetime.datetime.now()).isoformat()


def _yesterday() -> str:
    return (
        gather.effective_date(datetime.datetime.now())
        - datetime.timedelta(days=1)
    ).isoformat()


def _payload(names=("Read",)) -> dict:
    return {
        "schema": gh.SCHEMA,
        "generated_at": GENERATED_AT,
        "habits": [
            {"name": name, "progress": None, "records": []} for name in names
        ],
    }


def _write_cache(logical_day: str, names=("Read",)) -> Path:
    """Store a Grit cache exactly as ``grit_habits`` would."""
    path = gh.cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    document = dict(_payload(names))
    document["logical_day"] = logical_day
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def _save_settings(habits) -> None:
    hs.save_full_replace(habits, expected_revision=0)


class FakeRunner:
    """Injected Grit runner: writes a payload (or not) and records its calls."""

    def __init__(self, *, payload=None, result=None, write=True):
        self.payload = payload
        self.result = result
        self.write = write
        self.calls: list[dict] = []

    def __call__(self, name, output_path, timeout_s=None):
        self.calls.append(
            {"name": name, "output_path": str(output_path), "timeout_s": timeout_s}
        )
        if self.result is not None:
            return self.result
        if self.write:
            text = json.dumps(
                self.payload if self.payload is not None else _payload()
            )
            Path(output_path).write_text(text, encoding="utf-8")
        return gh.RunResult(ok=True)


@pytest.fixture(autouse=True)
def no_process(monkeypatch):
    """No test may spawn a process; the single-flight window is reset too."""
    attempts: list[tuple] = []

    def _guard(*args, **kwargs):
        attempts.append((args, kwargs))
        raise AssertionError("tests must never invoke subprocess.run")

    monkeypatch.setattr(gh.subprocess, "run", _guard)
    monkeypatch.setattr(gh, "_RECENT_SUCCESS", None)
    yield attempts
    assert attempts == [], "a test invoked subprocess.run for real"


def _client(runner=None) -> TestClient:
    app = main_mod.create_app()
    app.state.grit_runner = runner
    client = TestClient(app)
    client.app_token = app.state.token
    return client


def _auth(client: TestClient) -> dict:
    return {"X-TDTB-Token": client.app_token}


def _save_body(expected_revision: int = 0, habits: list | None = None) -> dict:
    return {
        "expected_revision": expected_revision,
        "habits": habits if habits is not None else [],
    }


# ---------------------------------------------------------------------------
# GET /settings/habits
# ---------------------------------------------------------------------------

class TestGetHabitSettings:
    def test_empty_default_reads_nothing_and_writes_nothing(self):
        client = _client()
        response = client.get("/settings/habits")
        assert response.status_code == 200
        body = response.json()
        assert body["settings"] == {"version": 1, "revision": 0, "habits": []}
        assert body["persisted"] is False
        assert body["inventory"] == []
        assert body["inventory_status"] == "unavailable"
        assert body["grit_read_at"] is None
        assert body["warnings"] == []
        assert not hs.settings_path().exists()
        assert not gh.cache_path().exists()

    def test_same_day_cache_classifies_configured_unconfigured_missing(self):
        _save_settings(
            [
                {"name": "Read", "minutes": 20, "weekdays": None},
                {"name": "Walk", "minutes": 30, "weekdays": [0, 2]},
            ]
        )
        _write_cache(_today(), names=("Read", "Meditate"))
        body = _client().get("/settings/habits").json()
        assert body["persisted"] is True
        assert body["inventory_status"] == "ok"
        assert body["grit_read_at"] == GENERATED_AT
        assert body["warnings"] == []
        rows = {row["name"]: row for row in body["inventory"]}
        assert rows["Read"]["status"] == "configured"
        assert rows["Read"]["minutes"] == 20
        assert rows["Read"]["weekdays"] is None
        assert rows["Meditate"]["status"] == "unconfigured"
        assert rows["Meditate"]["minutes"] is None
        assert rows["Meditate"]["weekdays"] is None
        assert rows["Walk"]["status"] == "missing"
        assert rows["Walk"]["minutes"] == 30
        assert rows["Walk"]["weekdays"] == [0, 2]
        assert [row["name"] for row in body["inventory"]] == [
            "Meditate",
            "Read",
            "Walk",
        ]

    def test_no_cache_marks_saved_rows_unverified(self):
        _save_settings([{"name": "Read", "minutes": 20}])
        body = _client().get("/settings/habits").json()
        assert body["inventory_status"] == "unavailable"
        assert body["grit_read_at"] is None
        assert body["inventory"] == [
            {
                "name": "Read",
                "status": "unverified",
                "minutes": 20,
                "weekdays": None,
            }
        ]

    def test_earlier_day_cache_is_stale_but_names_still_reconcile(self):
        _save_settings([{"name": "Read", "minutes": 20}])
        _write_cache(_yesterday(), names=("Read", "Meditate"))
        body = _client().get("/settings/habits").json()
        assert body["inventory_status"] == "stale"
        assert body["grit_read_at"] == GENERATED_AT
        assert body["warnings"] == [main_mod.HABIT_INVENTORY_STALE]
        rows = {row["name"]: row["status"] for row in body["inventory"]}
        assert rows == {"Meditate": "unconfigured", "Read": "configured"}

    def test_get_never_runs_the_shortcut(self, no_process):
        """A GET reconciles from the cache; it must not invoke the runner."""
        runner = FakeRunner()
        _client(runner).get("/settings/habits")
        assert runner.calls == []

    def test_malformed_storage_is_a_bounded_500_and_preserves_bytes(self):
        path = hs.settings_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json", encoding="utf-8")
        response = _client().get("/settings/habits")
        assert response.status_code == 500
        assert response.json()["detail"]["code"] == "habit_settings_storage_error"
        assert path.read_text(encoding="utf-8") == "{not json"


# ---------------------------------------------------------------------------
# POST /settings/habits/save
# ---------------------------------------------------------------------------

class TestSaveHabitSettings:
    def test_requires_token(self):
        client = _client()
        response = client.post("/settings/habits/save", json=_save_body())
        assert response.status_code == 403
        assert not hs.settings_path().exists()

    def test_round_trip_increments_revision_and_answers_the_get_payload(self):
        _write_cache(_today(), names=("Read",))
        client = _client()
        response = client.post(
            "/settings/habits/save",
            headers=_auth(client),
            json=_save_body(
                habits=[
                    {"name": "Walk", "minutes": 30},
                    {"name": "Read", "minutes": 20, "weekdays": [0, 1]},
                ]
            ),
        )
        assert response.status_code == 200
        body = response.json()
        assert body["persisted"] is True
        assert body["settings"]["version"] == 1
        assert body["settings"]["revision"] == 1
        assert body["settings"]["habits"] == [
            {"name": "Read", "minutes": 20, "weekdays": [0, 1]},
            {"name": "Walk", "minutes": 30, "weekdays": None},
        ]
        assert body["inventory_status"] == "ok"
        rows = {row["name"]: row["status"] for row in body["inventory"]}
        assert rows == {"Read": "configured", "Walk": "missing"}
        assert hs.load().settings.revision == 1

    def test_stale_revision_is_409_and_leaves_the_file_unchanged(self):
        client = _client()
        first = client.post(
            "/settings/habits/save",
            headers=_auth(client),
            json=_save_body(habits=[{"name": "Read", "minutes": 20}]),
        )
        assert first.status_code == 200
        before = hs.settings_path().read_bytes()
        stale = client.post(
            "/settings/habits/save",
            headers=_auth(client),
            json=_save_body(habits=[{"name": "Walk", "minutes": 30}]),
        )
        assert stale.status_code == 409
        assert stale.json()["detail"]["code"] == "habit_settings_conflict"
        assert hs.settings_path().read_bytes() == before

    @pytest.mark.parametrize(
        "row, field",
        [
            ({"name": "Read", "minutes": 900}, "minutes"),
            ({"name": "Read", "minutes": -5}, "minutes"),
            ({"name": "   ", "minutes": 20}, "name"),
            ({"name": "Read", "minutes": 20, "weekdays": []}, "weekdays"),
            ({"name": "Read", "minutes": 20, "weekdays": [7]}, "weekdays"),
        ],
    )
    def test_invalid_row_is_422_with_index_and_field(self, row, field):
        client = _client()
        response = client.post(
            "/settings/habits/save",
            headers=_auth(client),
            json=_save_body(habits=[{"name": "Walk", "minutes": 30}, row]),
        )
        assert response.status_code == 422
        detail = response.json()["detail"]
        assert detail["code"] == "habit_settings_invalid"
        assert detail["index"] == 1
        assert detail["field"] == field
        assert not hs.settings_path().exists()

    def test_duplicate_name_is_422_on_the_second_row(self):
        client = _client()
        response = client.post(
            "/settings/habits/save",
            headers=_auth(client),
            json=_save_body(
                habits=[
                    {"name": "Read", "minutes": 20},
                    {"name": "Read", "minutes": 30},
                ]
            ),
        )
        assert response.status_code == 422
        detail = response.json()["detail"]
        assert (detail["index"], detail["field"]) == (1, "name")
        assert not hs.settings_path().exists()

    def test_server_owned_version_in_the_body_is_rejected(self):
        client = _client()
        response = client.post(
            "/settings/habits/save",
            headers=_auth(client),
            json={"expected_revision": 0, "habits": [], "version": 1},
        )
        assert response.status_code == 422
        assert not hs.settings_path().exists()

    def test_malformed_storage_is_409_and_preserves_bytes(self):
        path = hs.settings_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"version": 9}', encoding="utf-8")
        client = _client()
        response = client.post(
            "/settings/habits/save", headers=_auth(client), json=_save_body()
        )
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "habit_settings_storage_error"
        assert path.read_text(encoding="utf-8") == '{"version": 9}'


# ---------------------------------------------------------------------------
# POST /habits/grit/refresh
# ---------------------------------------------------------------------------

class TestGritRefresh:
    def test_requires_token_and_runs_nothing(self):
        runner = FakeRunner()
        client = _client(runner)
        response = client.post("/habits/grit/refresh")
        assert response.status_code == 403
        assert runner.calls == []
        assert not gh.cache_path().exists()

    def test_success_stores_the_cache_and_reports_ok(self):
        _save_settings([{"name": "Read", "minutes": 20}])
        runner = FakeRunner(payload=_payload(names=("Read", "Meditate")))
        client = _client(runner)
        response = client.post("/habits/grit/refresh", headers=_auth(client))
        assert response.status_code == 200
        body = response.json()
        assert body["refresh"] == {"ok": True}
        assert body["inventory_status"] == "ok"
        assert body["grit_read_at"] == GENERATED_AT
        assert gh.cache_path().exists()
        cached = gh.load_cache()
        assert cached is not None
        assert cached.logical_day == _today()
        assert [habit.name for habit in cached.habits] == ["Read", "Meditate"]
        rows = {row["name"]: row["status"] for row in body["inventory"]}
        assert rows == {"Meditate": "unconfigured", "Read": "configured"}
        # One read, through the injected runner and the fixed Shortcut name.
        assert [call["name"] for call in runner.calls] == [gh.SHORTCUT_NAME]

    def test_failure_is_200_with_ok_false_and_leaves_the_cache_untouched(self):
        _write_cache(_today(), names=("Read",))
        before = gh.cache_path().read_bytes()
        runner = FakeRunner(result=gh.RunResult(ok=False, reason=gh.REASON_TIMEOUT))
        client = _client(runner)
        response = client.post("/habits/grit/refresh", headers=_auth(client))
        assert response.status_code == 200
        body = response.json()
        assert body["refresh"] == {"ok": False, "reason": "timeout"}
        assert gh.cache_path().read_bytes() == before
        # The prior same-day read still answers the inventory.
        assert body["inventory_status"] == "ok"
        assert [row["name"] for row in body["inventory"]] == ["Read"]

    def test_failure_with_no_cache_reports_unavailable(self):
        runner = FakeRunner(write=False)
        client = _client(runner)
        response = client.post("/habits/grit/refresh", headers=_auth(client))
        assert response.status_code == 200
        body = response.json()
        assert body["refresh"] == {
            "ok": False,
            "reason": gh.REASON_MISSING_OUTPUT,
        }
        assert body["inventory_status"] == "unavailable"
        assert body["grit_read_at"] is None
        assert not gh.cache_path().exists()

    def test_refresh_answers_the_same_shape_as_get(self):
        client = _client(FakeRunner())
        get_body = client.get("/settings/habits").json()
        refresh_body = client.post(
            "/habits/grit/refresh", headers=_auth(client)
        ).json()
        assert set(get_body) | {"refresh"} == set(refresh_body)
        assert set(get_body) == {
            "settings",
            "persisted",
            "inventory",
            "inventory_status",
            "grit_read_at",
            "warnings",
        }
