"""S3 wiring: the published Capacities ideas pool reaches the request paths.

Pins the slice contract:

* disabled (the default) leaves the config pool and the pick exactly as today;
* ``ok`` merges the Capacities rows after the config pool, capped at half of
  the live pool, and the pick may be a ``cap-`` row;
* ``stale`` / ``unavailable`` / ``not-configured`` keep the static pool and add
  one fixed-text warning; a garbage block never raises;
* ``/plan-inputs`` reports ``pool_source`` / ``pool_warnings`` and agrees with
  the commit fallback (``_ensure_micro_adventure``);
* a selected ``cap-`` idea round-trips through ``/day-setup`` and the log.

Everything else (history, streak, pending_confirm, loose end, commit gate) is
owned by the existing suites and is not touched here.
"""
from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))
import app_config  # noqa: E402
import artifact_source  # noqa: E402
import micro_adventure as ma  # noqa: E402
import main as main_mod  # noqa: E402

TODAY = date(2026, 7, 15)
FALLBACK_WARNING = (
    "Capacities micro-adventure ideas were not used; the config "
    "micro-adventure pool is in effect."
)
CAP_IDS = ["cap-o1", "cap-o2", "cap-o3", "cap-o4", "cap-o5", "cap-o6"]


# --------------------------------------------------------------------------- #
# fixtures / helpers
# --------------------------------------------------------------------------- #
def _caps(**over: object) -> dict:
    caps: dict = {
        "enabled": True,
        "collection": "Personal Adventures",
        "tag": "_TDTB-live",
        "refresh_cadence_hours": 168,
    }
    caps.update(over)
    return caps


def _sections(*, caps: dict | None = None, pool: list[dict] | None = None) -> dict:
    micro: dict = {
        "_body": "prose",
        "Pool": list(pool if pool is not None else [
            {"ID": "cfg1", "Idea": "Walk the ridge", "Active": True},
            {"ID": "cfg2", "Idea": "Read outside", "Active": True},
        ]),
        "Rotation": {"rotation.exclude_window_days": 10},
    }
    if caps is not None:
        micro["Capacities"] = caps
    return {"Micro-Adventures": micro}


def _block(items: list[dict] | None = None, **over: object) -> dict:
    doc: dict = {"status": "ok", "items": list(items or [])}
    doc.update(over)
    return doc


def _items(count: int = 6) -> list[dict]:
    return [{"id": f"o{i}", "idea": f"Capacities idea {i}"} for i in range(1, count + 1)]


@pytest.fixture
def published(monkeypatch):
    """Install a fake artifact reader and count how often it is consulted."""
    calls: list[int] = []

    def _install(block, warnings: list[str] | None = None):
        def _fake(*, path=None):
            calls.append(1)
            return block, list(warnings or [])

        monkeypatch.setattr(artifact_source, "load_micro_ideas", _fake)
        return calls

    return _install


@pytest.fixture
def config_file():
    """Install the micro-adventure pool/capacities keys in the repo config."""
    def _install(caps: dict | None, pool: list[dict] | None = None) -> Path:
        doc: dict = {
            "version": 1,
            "micro_adventure_pool": list(pool if pool is not None else [
                {"ID": "cfg1", "Idea": "Walk the ridge", "Active": True},
            ]),
            "micro_adventure_rotation": {"exclude_window_days": 10},
        }
        if caps is not None:
            doc["micro_adventure_capacities"] = caps
        path = app_config.repo_config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(doc), encoding="utf-8")
        return path

    return _install


@pytest.fixture
def client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setattr(main_mod.gather, "effective_date", lambda now: TODAY)
    app = main_mod.create_app()
    c = TestClient(app)
    c.app_token = app.state.token
    return c


def _auth(client: TestClient) -> dict:
    return {"X-TDTB-Token": client.app_token}


def _state(sections: dict) -> dict:
    return main_mod._micro_adventure_state(sections, TODAY, None)[0]


def _history(*entries: ma.HistoryEntry) -> None:
    ma.write_history(ma.history_path(), entries)


# --------------------------------------------------------------------------- #
# disabled (default) => today's behavior
# --------------------------------------------------------------------------- #
class TestDisabledIsToday:
    def test_disabled_pool_and_pick_match_the_no_capacities_baseline(self, published):
        calls = published(_block(_items()))
        baseline = _state(_sections())
        state = _state(_sections(caps=_caps(enabled=False)))

        for key in ("auto_pick", "live_pool", "streak", "pending_confirm"):
            assert state[key] == baseline[key]
        assert state["auto_pick"]["id"] == "cfg1"
        assert state["pool_source"] == "config"
        assert state["pool_warnings"] == []
        assert calls == []  # the disabled path never reads the artifact

    def test_absent_capacities_key_is_the_same_payload(self, published):
        published(_block(_items()))
        assert _state(_sections()) == _state(_sections(caps=_caps(enabled=False)))


# --------------------------------------------------------------------------- #
# ok => merged pool
# --------------------------------------------------------------------------- #
class TestOkMerges:
    def test_ok_appends_capacities_rows_capped_at_half_the_live_pool(self, published):
        published(_block(_items(6)))
        state = _state(_sections(caps=_caps()))

        ids = [row["id"] for row in state["live_pool"]]
        cap_ids = [i for i in ids if i.startswith("cap-")]
        assert ma.capacities_live_limit(ma.DEFAULT_LIVE_POOL_CAP) == 4
        assert ids[:2] == ["cfg1", "cfg2"]  # config rows keep pool order
        assert len(cap_ids) == 4
        assert cap_ids == CAP_IDS[:4]
        assert state["pool_source"] == "config+capacities"
        assert state["pool_warnings"] == []

    def test_pick_can_be_a_capacities_idea(self, published):
        published(_block(_items(3)))
        _history(ma.HistoryEntry(TODAY - timedelta(days=1), "cfg1", "Walk the ridge", None, True))
        state = _state(_sections(caps=_caps(), pool=[
            {"ID": "cfg1", "Idea": "Walk the ridge", "Active": True},
        ]))

        assert state["auto_pick"]["id"] == "cap-o1"
        assert state["auto_pick"]["idea"] == "Capacities idea 1"
        assert state["live_pool"][0] == state["auto_pick"]
        assert state["streak"] == 1

    def test_normalization_warnings_ride_pool_warnings(self, published):
        published(_block([{"id": "o1", "idea": "  "}]))
        state = _state(_sections(caps=_caps()))
        assert state["pool_source"] == "config+capacities"
        assert state["live_pool"][-1]["id"] == "cfg2"
        assert any("cap-o1" in w for w in state["pool_warnings"])


# --------------------------------------------------------------------------- #
# degraded statuses => static pool + one fixed warning
# --------------------------------------------------------------------------- #
class TestDegradedFallsBack:
    def test_stale_block_keeps_the_static_pool(self, published):
        published(_block(_items(3), read_at="2020-01-01T00:00:00Z"))
        baseline = _state(_sections())
        state = _state(_sections(caps=_caps()))

        assert state["live_pool"] == baseline["live_pool"]
        assert state["auto_pick"] == baseline["auto_pick"]
        assert state["pool_source"] == "config"
        assert state["pool_warnings"] == [FALLBACK_WARNING]

    def test_unavailable_block_keeps_the_static_pool(self, published):
        published(None)
        state = _state(_sections(caps=_caps()))
        assert state["auto_pick"]["id"] == "cfg1"
        assert state["pool_source"] == "config"
        assert state["pool_warnings"] == [FALLBACK_WARNING]

    def test_not_configured_never_reads_the_artifact(self, published):
        calls = published(_block(_items(3)))
        state = _state(_sections(caps=_caps(collection="", tag="")))
        assert state["auto_pick"]["id"] == "cfg1"
        assert state["pool_source"] == "config"
        assert state["pool_warnings"] == [FALLBACK_WARNING]
        assert calls == []

    def test_garbage_block_never_raises(self, published):
        for garbage in ("nope", ["nope"], {"status": "ok", "items": "nope"}, 42):
            published(garbage)
            state = _state(_sections(caps=_caps()))
            assert state["auto_pick"]["id"] == "cfg1"
            assert state["pool_source"] == "config"
            assert state["pool_warnings"] == [FALLBACK_WARNING]

    def test_failed_block_status_falls_back(self, published):
        published(_block(_items(3), status="failed"))
        state = _state(_sections(caps=_caps()))
        assert state["pool_source"] == "config"
        assert state["pool_warnings"] == [FALLBACK_WARNING]


# --------------------------------------------------------------------------- #
# route surfaces: /plan-inputs, /day-setup, commit fallback
# --------------------------------------------------------------------------- #
class TestRouteWiring:
    def test_plan_inputs_payload_carries_the_pool_fields(self, client, published, config_file):
        published(_block(_items(3)))
        config_file(_caps())

        payload = client.get("/plan-inputs").json()["micro_adventure"]
        assert payload["pool_source"] == "config+capacities"
        assert payload["pool_warnings"] == []
        assert [r["id"] for r in payload["live_pool"]] == [
            "cfg1", "cap-o1", "cap-o2", "cap-o3",
        ]
        assert payload["pick"]["id"] == "cfg1"
        assert payload["source"] == "auto"

    def test_plan_inputs_payload_defaults_are_config_and_no_warnings(
            self, client, published, config_file):
        published(_block(_items(3)))
        config_file(None)
        payload = client.get("/plan-inputs").json()["micro_adventure"]
        assert payload["pool_source"] == "config"
        assert payload["pool_warnings"] == []

    def test_plan_inputs_pick_equals_the_commit_fallback_pick(
            self, client, published, config_file):
        published(_block(_items(3)))
        config_file(_caps())
        _history(ma.HistoryEntry(TODAY - timedelta(days=1), "cfg1", "Walk the ridge", None, True))

        payload = client.get("/plan-inputs").json()["micro_adventure"]
        fallback = main_mod._ensure_micro_adventure({}, TODAY)["micro_adventure"]
        assert payload["pick"]["id"] == "cap-o1"
        assert fallback == payload["pick"]

    def test_selected_capacities_idea_round_trips_through_day_setup(
            self, client, published, config_file):
        published(_block(_items(3)))
        config_file(_caps())

        pick = client.get("/plan-inputs").json()["micro_adventure"]["live_pool"][1]
        assert pick["id"] == "cap-o1"

        r = client.post("/day-setup", headers=_auth(client),
                        json={"micro_adventure": pick})
        assert r.status_code == 200, r.text
        assert main_mod._read_today_runstate(TODAY)["micro_adventure"]["id"] == "cap-o1"

        override = client.get("/plan-inputs").json()["micro_adventure"]
        assert override["source"] == "override"
        assert override["pick"]["id"] == "cap-o1"
        assert override["pool_source"] == "config+capacities"

        client.post("/day-setup", headers=_auth(client), json={"micro_adventure": None})
        auto = client.get("/plan-inputs").json()["micro_adventure"]
        assert auto["source"] == "auto"
        assert auto["pick"]["id"] == "cfg1"

    def test_capacities_id_survives_the_history_log_round_trip(self):
        entry = ma.build_history_entry(
            "cap-o1", "Capacities idea 1", today=TODAY, todoist_task_id=None,
        )
        ma.write_history(ma.history_path(), ma.upsert_today_entry((), entry))
        history = ma.read_history(ma.history_path())
        assert [(e.id, e.idea, e.done) for e in history] == [
            ("cap-o1", "Capacities idea 1", None),
        ]
