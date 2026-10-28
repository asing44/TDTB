"""T19 route wiring — micro-adventure selection across /plan-inputs, /gather,
/day-setup, and both /commit modes.

Pins the LD25 boundary: reads compute + expose (never write the history log),
/gather populates dated runstate, /day-setup persists free overrides, and the
authorized live commit is the ONLY history-consuming surface (idempotent).
"""
from __future__ import annotations

import sys
from datetime import date, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))
import main as main_mod  # noqa: E402
import micro_adventure  # noqa: E402
import runstate as rs  # noqa: E402
import shadow  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent.parent / "gather"))
import tdtb_gather as gather  # noqa: E402
from tests.config_markdown import write_config_note  # noqa: E402

TODAY = date(2026, 7, 12)
LOG_PATH = micro_adventure.history_path


@pytest.fixture
def vault(tmp_path) -> Path:
    return tmp_path / "vault-root"


@pytest.fixture
def client(vault) -> TestClient:
    vault.mkdir()
    app = main_mod.create_app()
    c = TestClient(app)
    c.app_token = app.state.token
    return c


def _auth(client: TestClient) -> dict:
    return {"X-TDTB-Token": client.app_token}


def _write_log(history_yaml: str) -> None:
    p = LOG_PATH()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        "---\ndescription: test log\nschema_version: 1\nhistory:\n"
        + history_yaml + "---\n",
        encoding="utf-8",
    )


class TestPlanInputsMicro:
    def test_bootstrap_auto_pick_from_fallback_pool(self, client):
        body = client.get("/plan-inputs").json()
        ma = body["micro_adventure"]
        assert ma["source"] == "auto"
        assert ma["pick"]["id"] == "ma01"  # fallback pool order, empty history
        assert ma["streak"] == 0
        assert ma["pending_confirm"] is None
        assert 0 < len(ma["live_pool"]) <= 8
        assert ma["live_pool"][0] == ma["pick"]
        # the auto-pick rides config for downstream sequence/commit bodies
        assert body["config"]["micro_adventure"] == ma["pick"]

    def test_recent_use_excluded_lru_advances(self, client, monkeypatch):
        monkeypatch.setattr(gather, "effective_date", lambda now: TODAY)
        _write_log("  - date: 2026-07-11\n    id: ma01\n    idea: x\n    done: true\n")
        ma = client.get("/plan-inputs").json()["micro_adventure"]
        assert ma["pick"]["id"] == "ma02"  # ma01 inside the 14-day window
        assert ma["streak"] == 1

    def test_pending_confirm_surfaces_when_inconclusive(self, client, monkeypatch):
        monkeypatch.setattr(gather, "effective_date", lambda now: TODAY)
        _write_log("  - date: 2026-07-11\n    id: ma03\n    idea: Ride bike somewhere\n    done:\n")
        ma = client.get("/plan-inputs").json()["micro_adventure"]
        assert ma["pending_confirm"] == {
            "date": "2026-07-11", "id": "ma03", "idea": "Ride bike somewhere",
        }

    def test_todoist_completion_probe_resolves_prior(self, client, monkeypatch, live_sources_mode):
        monkeypatch.setattr(gather, "effective_date", lambda now: TODAY)
        _write_log(
            "  - date: 2026-07-11\n    id: ma03\n    idea: x\n"
            "    todoist_task_id: t99\n    done:\n",
        )

        class ReadClient:
            def get_task(self, task_id):
                assert task_id == "t99"
                return {"checked": True}

        client.app.state.build_read_clients = lambda c: (ReadClient(), None)
        ma = client.get("/plan-inputs").json()["micro_adventure"]
        assert ma["pending_confirm"] is None
        assert ma["streak"] == 1  # virtually resolved done:true
        # read path never writes the log — done stays null on disk
        head = micro_adventure.read_history(LOG_PATH())[0]
        assert head.done is None

    def test_runstate_override_wins(self, client, monkeypatch):
        monkeypatch.setattr(gather, "effective_date", lambda now: TODAY)
        rs.update_runstate(
            TODAY,
            {"micro_adventure": {"id": "custom", "idea": "Night swim", "category": "custom"}},
        )
        body = client.get("/plan-inputs").json()
        ma = body["micro_adventure"]
        assert ma["source"] == "override"
        assert ma["pick"]["idea"] == "Night swim"
        assert body["config"]["micro_adventure"]["idea"] == "Night swim"


class TestGatherPopulatesRunstate:
    def test_gather_writes_micro_keys(self, client, monkeypatch):
        monkeypatch.setattr(gather, "effective_date", lambda now: TODAY)
        r = client.post("/gather", headers=_auth(client))
        assert r.status_code == 200
        state = main_mod._read_today_runstate(TODAY)
        assert state["micro_adventure"]["id"] == "ma01"
        assert state["live_pool"][0]["id"] == "ma01"
        assert state["live_streak"] == 0
        assert state["pending_confirm"] is None

    def test_gather_preserves_existing_override(self, client, monkeypatch):
        monkeypatch.setattr(gather, "effective_date", lambda now: TODAY)
        override = {"id": "custom", "idea": "Night swim", "category": "custom"}
        rs.update_runstate(TODAY, {"micro_adventure": override})
        client.post("/gather", headers=_auth(client))
        state = main_mod._read_today_runstate(TODAY)
        assert state["micro_adventure"] == override  # not clobbered by auto


class TestDaySetupOverride:
    def test_set_persist_and_clear(self, client, monkeypatch):
        monkeypatch.setattr(gather, "effective_date", lambda now: TODAY)
        r = client.post(
            "/day-setup", headers=_auth(client),
            json={"micro_adventure": {"id": "ma07", "idea": "Watch sunset", "category": "nature"}},
        )
        assert r.status_code == 200
        assert main_mod._read_today_runstate(TODAY)["micro_adventure"]["id"] == "ma07"

        # omitted field preserves
        client.post("/day-setup", headers=_auth(client), json={"anchor": "09:00"})
        assert main_mod._read_today_runstate(TODAY)["micro_adventure"]["id"] == "ma07"

        # explicit null clears back to auto
        client.post("/day-setup", headers=_auth(client), json={"micro_adventure": None})
        assert main_mod._read_today_runstate(TODAY)["micro_adventure"] is None

    def test_custom_category_defaulted(self, client, monkeypatch):
        monkeypatch.setattr(gather, "effective_date", lambda now: TODAY)
        client.post(
            "/day-setup", headers=_auth(client),
            json={"micro_adventure": {"id": "custom", "idea": "Night swim"}},
        )
        stored = main_mod._read_today_runstate(TODAY)["micro_adventure"]
        assert stored == {"id": "custom", "idea": "Night swim", "category": "custom"}

    def test_invalid_shape_422(self, client):
        r = client.post(
            "/day-setup", headers=_auth(client),
            json={"micro_adventure": {"idea": "no id"}},
        )
        assert r.status_code == 422


# ------------------------------------------------------------- commit paths

class FakeLiveTodoist:
    def __init__(self):
        self._tasks = {}
        self._seq = 0

    def close(self):
        pass

    def get_filter_tasks(self, filter_id_or_query, limit=None):
        return list(self._tasks.values())

    def get_task(self, task_id):
        return self._tasks[task_id]

    def create_task(self, content, project_id=None, due_string=None,
                    duration=None, duration_unit=None, **_):
        self._seq += 1
        tid = f"t{self._seq}"
        hhmm = due_string.split("at ", 1)[1].strip() if due_string and "at " in due_string else None
        due = {"date": f"2026-07-12T{hhmm}:00"} if hhmm else None
        self._tasks[tid] = {"id": tid, "content": content, "due": due, "project_id": project_id}
        return self._tasks[tid]

    def reschedule_task(self, task_id, due_string):
        return self._tasks[task_id]


def _fake_live_state(config):
    return {
        "todoist_tasks": [],
        "calendar_events": [],
    }


LIVE_SEQUENCE = {"sequence": [{"id": "Live", "start": "20:30", "end": "21:30", "zone": "any"}]}
LIVE_CONFIG = {"anchored_blocks": [{"id": "Live", "time": "20:30"}]}


class TestCommitHistoryAppend:
    def _seed(self, vault: Path, monkeypatch) -> None:
        config_path = vault / "00 - META" / "Skill-Configs" / "tdtb-bridger.md"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        write_config_note(config_path,
            "## Defaults\n"
            "| Key | Value |\n|---|---|\n| eod | 11:59 PM |\n\n"
            "## Anchored Lifestyle Blocks\n"
            "| Block | Type | Start | End | Duration | Days |\n"
            "|---|---|---|---|---|---|\n"
            "| Live | hard | 8:30 PM | 9:30 PM | 60m | daily |\n\n"
            "## Template Blocks\n"
            "### Trinoor Hours\n"
            "| Slot | Start | End |\n|---|---|---|\n"
            "| Morning | 12:00 AM | 11:59 PM |\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(gather, "effective_date", lambda now: TODAY)
        monkeypatch.setattr(shadow, "gather_live_state", _fake_live_state)
        rs.update_runstate(
            TODAY,
            {"micro_adventure": {"id": "ma03", "idea": "Ride bike somewhere",
                                 "category": "novelty"},
             # T12a: the commit routes derive a day frame from the runstate
             # anchor (falling back to wall-clock now), and out-of-frame
             # anchored blocks no longer publish. Without an explicit early
             # anchor these tests pass before 20:30 and fail after it —
             # LIVE_SEQUENCE's Live block would be filtered as elapsed. The
             # reroute is what's under test here, not the frame.
             "anchor": "06:00",
             # FEEDBACK-24: live commit is gated on an explicit Day Setup
             # confirm — seed it so the history-append path is exercisable.
             "day_setup_confirmed": True},
        )

    def _post_live(self, client, resume: bool = False):
        qs = "?mode=live" + ("&resume=true" if resume else "")
        return client.post(
            "/commit" + qs, headers=_auth(client),
            json={"digest": {"assigned": [], "suggested": []},
                  "sequence": LIVE_SEQUENCE, "config": LIVE_CONFIG},
        )

    def test_live_commit_appends_history_with_task_id(self, client, vault, monkeypatch):
        self._seed(vault, monkeypatch)
        fake = FakeLiveTodoist()
        client.app.state.build_commit_clients = lambda cfg: (fake, None)
        r = self._post_live(client)
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert body["micro_adventure_logged"] is True
        history = micro_adventure.read_history(LOG_PATH())
        assert len(history) == 1
        head = history[0]
        assert (head.date, head.id, head.idea) == (TODAY, "ma03", "Ride bike somewhere")
        assert head.todoist_task_id in fake._tasks
        assert fake._tasks[head.todoist_task_id]["content"] == "🌱 Ride bike somewhere"
        assert head.done is None

    def test_recommit_is_idempotent_one_entry(self, client, vault, monkeypatch):
        self._seed(vault, monkeypatch)
        fake = FakeLiveTodoist()
        client.app.state.build_commit_clients = lambda cfg: (fake, None)
        assert self._post_live(client).json()["ok"] is True
        assert self._post_live(client, resume=True).json()["ok"] is True
        history = micro_adventure.read_history(LOG_PATH())
        assert len(history) == 1  # upsert replaced, never duplicated

    def test_shadow_commit_never_writes_history(self, client, vault, monkeypatch):
        self._seed(vault, monkeypatch)
        r = client.post(
            "/commit?mode=shadow", headers=_auth(client),
            json={"digest": {"assigned": [], "suggested": []},
                  "sequence": LIVE_SEQUENCE, "config": LIVE_CONFIG},
        )
        assert r.status_code == 200
        # shadow reroutes Live -> todoist in the preview (server-side merge)…
        entries = r.json()["entries"]
        live_rows = [e for e in entries if e["manifest"]["system"] == "todoist"]
        assert any("🌱" in e["manifest"]["name"] for e in live_rows)
        # …but the history log stays untouched
        assert not (LOG_PATH()).exists()

    def test_failed_commit_does_not_append(self, client, vault, monkeypatch):
        self._seed(vault, monkeypatch)
        # no todoist client -> the todoist surface fails -> report.ok False
        client.app.state.build_commit_clients = lambda cfg: (None, None)
        body = self._post_live(client).json()
        assert body["ok"] is False
        assert body["micro_adventure_logged"] is False
        assert not (LOG_PATH()).exists()

    def test_commit_ignores_a_daily_note_live_checkbox(self, client, vault, monkeypatch):
        """V3: a ticked ``### Live`` box in the old daily note no longer counts;
        the prior entry stays unconfirmed (done null) until answered in the
        cockpit, and the vault note is not even read."""
        self._seed(vault, monkeypatch)
        _write_log("  - date: 2026-07-11\n    id: ma07\n    idea: Watch sunset\n    done:\n")
        note = vault / "30 - Daily" / "2026-07-11.md"
        note.parent.mkdir(parents=True, exist_ok=True)
        note.write_text("# J\n### Live\n- [x] 🌱 Watch sunset\n", encoding="utf-8")
        fake = FakeLiveTodoist()
        client.app.state.build_commit_clients = lambda cfg: (fake, None)
        assert self._post_live(client).json()["micro_adventure_logged"] is True
        history = micro_adventure.read_history(LOG_PATH())
        assert [(e.id, e.done) for e in history] == [("ma03", None), ("ma07", None)]

    def test_commit_flushes_state_history_done_from_the_loose_end_answer(
            self, client, vault, monkeypatch):
        """The Live "done" state comes from the state-dir history: a "Did it"
        answer recorded through the loose-end route survives the next commit's
        history rewrite."""
        self._seed(vault, monkeypatch)
        _write_log("  - date: 2026-07-11\n    id: ma07\n    idea: Watch sunset\n    done:\n")
        r = client.post("/micro-adventure/loose-end", headers=_auth(client),
                        json={"date": "2026-07-11", "id": "ma07", "done": True})
        assert r.status_code == 200, r.text
        fake = FakeLiveTodoist()
        client.app.state.build_commit_clients = lambda cfg: (fake, None)
        assert self._post_live(client).json()["micro_adventure_logged"] is True
        history = micro_adventure.read_history(LOG_PATH())
        assert [(e.id, e.done) for e in history] == [("ma03", None), ("ma07", True)]


class TestPoolComesFromConfig:
    """V1: the pool and rotation window are read from the layered JSON config
    (``micro_adventure_pool`` / ``micro_adventure_rotation``), never the vault
    note; with neither present the in-code ``FALLBACK_POOL`` is used."""

    @staticmethod
    def _state(tmp_path: Path):
        import config_reader
        result = config_reader.read_config()
        sections = dict(result.config.sections) if result.config else {}
        return main_mod._micro_adventure_state(sections, TODAY)[0]

    def test_config_pool_is_offered(self, tmp_path: Path) -> None:
        import json
        import app_config
        path = app_config.repo_config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "version": 1,
            "micro_adventure_pool": [
                {"ID": "cfg1", "Idea": "Config idea", "Category": "x", "Effort": "low", "Active": True},
            ],
        }), encoding="utf-8")
        state = self._state(tmp_path)
        assert [p["id"] for p in state["live_pool"]] == ["cfg1"]
        assert state["auto_pick"]["id"] == "cfg1"

    def test_no_config_uses_the_code_fallback_pool(self, tmp_path: Path) -> None:
        state = self._state(tmp_path)
        assert state["auto_pick"] is not None
        assert state["auto_pick"]["id"] in {p.id for p in micro_adventure.FALLBACK_POOL}


class TestConfigPoolApi:
    """The config-pool editor API: the TRACKED repo config's pool only.

    GET is the editor's source of truth, so it must return every config entry
    — uncapped (no 8-item live window), unfiltered by ``Active``/rotation, and
    never merged with Capacities ideas. POST is a full replacement, written into
    ``config/tdtb.json`` as a surgical array-span edit."""

    @staticmethod
    def _item(index: int, *, active: bool = True) -> dict:
        return {
            "id": f"ma{index:02d}",
            "idea": f"Config idea {index}",
            "category": "nature",
            "effort": "low",
            "active": active,
        }

    @pytest.fixture
    def pool_cfg(self, tmp_path) -> Path:
        import json
        import app_config
        path = app_config.repo_config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "micro_adventure_pool": [
                        {"ID": f"ma{i:02d}", "Idea": f"Config idea {i}",
                         "Category": "nature", "Effort": "low",
                         "Active": i <= 10}
                        for i in range(1, 13)
                    ],
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        return path

    def _auth(self, client) -> dict:
        return {"X-TDTB-Token": client.app_token}

    def test_requires_token(self, client, pool_cfg):
        assert client.get("/config/micro-adventure/pool").status_code == 403
        assert client.post(
            "/config/micro-adventure/pool", json={"pool": [self._item(1)]}
        ).status_code == 403

    def test_get_returns_every_config_entry_uncapped_and_unfiltered(self, client, pool_cfg):
        r = client.get("/config/micro-adventure/pool", headers=self._auth(client))
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        # 12 entries: a live-pool implementation would cap at 8 …
        assert len(body["pool"]) == 12
        # … and would hide the two Active=false entries.
        assert [item["id"] for item in body["pool"]] == [f"ma{i:02d}" for i in range(1, 13)]
        assert [item["active"] for item in body["pool"]][-2:] == [False, False]
        assert set(body["pool"][0]) == {"id", "idea", "category", "effort", "active"}

    def test_get_never_uses_the_merged_live_state(self, client, pool_cfg, monkeypatch):
        def boom(*a, **k):
            raise AssertionError("the config-pool route must not compute live state")

        monkeypatch.setattr(main_mod, "_micro_adventure_state", boom)
        assert client.get(
            "/config/micro-adventure/pool", headers=self._auth(client)
        ).status_code == 200

    def test_post_replaces_the_pool_and_get_reflects_it(self, client, pool_cfg):
        new_pool = [self._item(1), self._item(99, active=False)]
        r = client.post(
            "/config/micro-adventure/pool", headers=self._auth(client), json={"pool": new_pool}
        )
        assert r.status_code == 200
        assert r.json() == {"ok": True, "pool": new_pool}
        assert client.get(
            "/config/micro-adventure/pool", headers=self._auth(client)
        ).json()["pool"] == new_pool
        # the app reads the saved pool straight from the layered config
        import app_config
        pool = app_config.load_sections()["Micro-Adventures"]["Pool"]
        assert [row["ID"] for row in pool] == ["ma01", "ma99"]

    def test_missing_pool_key_404(self, client):
        r = client.get("/config/micro-adventure/pool", headers=self._auth(client))
        assert r.status_code == 404
        assert r.json()["detail"]["code"] == "micro_adventure_pool_not_found"
        r = client.post(
            "/config/micro-adventure/pool",
            headers=self._auth(client),
            json={"pool": [self._item(1)]},
        )
        assert r.status_code == 404
        assert r.json()["detail"]["code"] == "micro_adventure_pool_not_found"

    def test_invalid_pool_422(self, client, pool_cfg):
        before = pool_cfg.read_bytes()
        for bad in ([self._item(1), self._item(1)], [self._item(1) | {"idea": "  "}]):
            r = client.post(
                "/config/micro-adventure/pool", headers=self._auth(client), json={"pool": bad}
            )
            assert r.status_code == 422
            detail = r.json()["detail"]
            assert detail["code"] == "micro_adventure_pool_invalid"
            assert "Config idea" not in detail["message"]
        assert pool_cfg.read_bytes() == before

    def test_non_boolean_active_422_at_the_boundary(self, client, pool_cfg):
        before = pool_cfg.read_bytes()
        r = client.post(
            "/config/micro-adventure/pool",
            headers=self._auth(client),
            json={"pool": [self._item(1) | {"active": "true"}]},
        )
        assert r.status_code == 422
        assert pool_cfg.read_bytes() == before

    def test_shadowed_409(self, client, pool_cfg):
        import json
        import app_config
        local = app_config.config_path()
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_text(
            json.dumps({"version": 1, "micro_adventure_pool": []}), encoding="utf-8"
        )
        before = pool_cfg.read_bytes()
        r = client.post(
            "/config/micro-adventure/pool",
            headers=self._auth(client),
            json={"pool": [self._item(1)]},
        )
        assert r.status_code == 409
        detail = r.json()["detail"]
        assert detail["code"] == "micro_adventure_pool_shadowed"
        assert "override" in detail["message"].lower()
        assert pool_cfg.read_bytes() == before

    def test_write_failure_500_bounded(self, client, pool_cfg, monkeypatch):
        import config_write
        before = pool_cfg.read_bytes()

        def boom(src, dst):
            raise OSError("/secret/path exploded")

        monkeypatch.setattr(config_write.os, "replace", boom)
        r = client.post(
            "/config/micro-adventure/pool",
            headers=self._auth(client),
            json={"pool": [self._item(1)]},
        )
        assert r.status_code == 500
        assert r.json()["detail"]["code"] == "config_write_error"
        assert "/secret/path" not in r.text
        assert pool_cfg.read_bytes() == before
