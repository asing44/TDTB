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

TODAY = date(2026, 7, 12)
LOG_REL = micro_adventure.HISTORY_REL_PATH


@pytest.fixture
def vault(tmp_path) -> Path:
    return tmp_path / "vault-root"


@pytest.fixture
def client(vault) -> TestClient:
    vault.mkdir()
    app = main_mod.create_app(vault_root=vault)
    c = TestClient(app)
    c.app_token = app.state.token
    return c


def _auth(client: TestClient) -> dict:
    return {"X-TDTB-Token": client.app_token}


def _write_log(vault: Path, history_yaml: str) -> None:
    p = vault / LOG_REL
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

    def test_recent_use_excluded_lru_advances(self, client, vault, monkeypatch):
        monkeypatch.setattr(gather, "effective_date", lambda now: TODAY)
        _write_log(vault, "  - date: 2026-07-11\n    id: ma01\n    idea: x\n    done: true\n")
        ma = client.get("/plan-inputs").json()["micro_adventure"]
        assert ma["pick"]["id"] == "ma02"  # ma01 inside the 14-day window
        assert ma["streak"] == 1

    def test_pending_confirm_surfaces_when_inconclusive(self, client, vault, monkeypatch):
        monkeypatch.setattr(gather, "effective_date", lambda now: TODAY)
        _write_log(vault, "  - date: 2026-07-11\n    id: ma03\n    idea: Ride bike somewhere\n    done:\n")
        ma = client.get("/plan-inputs").json()["micro_adventure"]
        assert ma["pending_confirm"] == {
            "date": "2026-07-11", "id": "ma03", "idea": "Ride bike somewhere",
        }

    def test_todoist_completion_probe_resolves_prior(self, client, vault, monkeypatch):
        monkeypatch.setattr(gather, "effective_date", lambda now: TODAY)
        _write_log(
            vault,
            "  - date: 2026-07-11\n    id: ma03\n    idea: x\n"
            "    todoist_task_id: t99\n    done:\n",
        )

        class ReadClient:
            def get_task(self, task_id):
                assert task_id == "t99"
                return {"checked": True}

        client.app.state.build_read_clients = lambda v, c: (ReadClient(), None)
        ma = client.get("/plan-inputs").json()["micro_adventure"]
        assert ma["pending_confirm"] is None
        assert ma["streak"] == 1  # virtually resolved done:true
        # read path never writes the log — done stays null on disk
        head = micro_adventure.read_history(vault / LOG_REL)[0]
        assert head.done is None

    def test_runstate_override_wins(self, client, vault, monkeypatch):
        monkeypatch.setattr(gather, "effective_date", lambda now: TODAY)
        rs.update_runstate(
            vault, TODAY,
            {"micro_adventure": {"id": "custom", "idea": "Night swim", "category": "custom"}},
        )
        body = client.get("/plan-inputs").json()
        ma = body["micro_adventure"]
        assert ma["source"] == "override"
        assert ma["pick"]["idea"] == "Night swim"
        assert body["config"]["micro_adventure"]["idea"] == "Night swim"


class TestGatherPopulatesRunstate:
    def test_gather_writes_micro_keys(self, client, vault, monkeypatch):
        monkeypatch.setattr(gather, "effective_date", lambda now: TODAY)
        r = client.post("/gather", headers=_auth(client))
        assert r.status_code == 200
        state = main_mod._read_today_runstate(vault, TODAY)
        assert state["micro_adventure"]["id"] == "ma01"
        assert state["live_pool"][0]["id"] == "ma01"
        assert state["live_streak"] == 0
        assert state["pending_confirm"] is None

    def test_gather_preserves_existing_override(self, client, vault, monkeypatch):
        monkeypatch.setattr(gather, "effective_date", lambda now: TODAY)
        override = {"id": "custom", "idea": "Night swim", "category": "custom"}
        rs.update_runstate(vault, TODAY, {"micro_adventure": override})
        client.post("/gather", headers=_auth(client))
        state = main_mod._read_today_runstate(vault, TODAY)
        assert state["micro_adventure"] == override  # not clobbered by auto


class TestDaySetupOverride:
    def test_set_persist_and_clear(self, client, vault, monkeypatch):
        monkeypatch.setattr(gather, "effective_date", lambda now: TODAY)
        r = client.post(
            "/day-setup", headers=_auth(client),
            json={"micro_adventure": {"id": "ma07", "idea": "Watch sunset", "category": "nature"}},
        )
        assert r.status_code == 200
        assert main_mod._read_today_runstate(vault, TODAY)["micro_adventure"]["id"] == "ma07"

        # omitted field preserves
        client.post("/day-setup", headers=_auth(client), json={"anchor": "09:00"})
        assert main_mod._read_today_runstate(vault, TODAY)["micro_adventure"]["id"] == "ma07"

        # explicit null clears back to auto
        client.post("/day-setup", headers=_auth(client), json={"micro_adventure": None})
        assert main_mod._read_today_runstate(vault, TODAY)["micro_adventure"] is None

    def test_custom_category_defaulted(self, client, vault, monkeypatch):
        monkeypatch.setattr(gather, "effective_date", lambda now: TODAY)
        client.post(
            "/day-setup", headers=_auth(client),
            json={"micro_adventure": {"id": "custom", "idea": "Night swim"}},
        )
        stored = main_mod._read_today_runstate(vault, TODAY)["micro_adventure"]
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


def _fake_live_state(config, vault_root):
    return {
        "todoist_tasks": [],
        "calendar_events": [],
        "vault_frontmatter": {},
        "daily_note_text": "# Journal\n",
    }


LIVE_SEQUENCE = {"sequence": [{"id": "Live", "start": "20:30", "end": "21:30", "zone": "any"}]}
LIVE_CONFIG = {"anchored_blocks": [{"id": "Live", "time": "20:30"}]}


class TestCommitHistoryAppend:
    def _seed(self, vault: Path, monkeypatch) -> None:
        (vault / "30 - Daily").mkdir(parents=True, exist_ok=True)
        (vault / "30 - Daily/2026-07-12.md").write_text("# Journal\n", encoding="utf-8")
        monkeypatch.setattr(gather, "effective_date", lambda now: TODAY)
        monkeypatch.setattr(shadow, "gather_live_state", _fake_live_state)
        rs.update_runstate(
            vault, TODAY,
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
        client.app.state.build_commit_clients = lambda v, cfg: (fake, None)
        r = self._post_live(client)
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert body["micro_adventure_logged"] is True
        history = micro_adventure.read_history(vault / LOG_REL)
        assert len(history) == 1
        head = history[0]
        assert (head.date, head.id, head.idea) == (TODAY, "ma03", "Ride bike somewhere")
        assert head.todoist_task_id in fake._tasks
        assert fake._tasks[head.todoist_task_id]["content"] == "🌱 Ride bike somewhere"
        assert head.done is None

    def test_recommit_is_idempotent_one_entry(self, client, vault, monkeypatch):
        self._seed(vault, monkeypatch)
        fake = FakeLiveTodoist()
        client.app.state.build_commit_clients = lambda v, cfg: (fake, None)
        assert self._post_live(client).json()["ok"] is True
        assert self._post_live(client, resume=True).json()["ok"] is True
        history = micro_adventure.read_history(vault / LOG_REL)
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
        assert not (vault / LOG_REL).exists()

    def test_failed_commit_does_not_append(self, client, vault, monkeypatch):
        self._seed(vault, monkeypatch)
        # no todoist client -> the todoist surface fails -> report.ok False
        client.app.state.build_commit_clients = lambda v, cfg: (None, None)
        body = self._post_live(client).json()
        assert body["ok"] is False
        assert body["micro_adventure_logged"] is False
        assert not (vault / LOG_REL).exists()

    def test_commit_flushes_checkbox_resolved_done_update(self, client, vault, monkeypatch):
        self._seed(vault, monkeypatch)
        _write_log(vault, "  - date: 2026-07-11\n    id: ma07\n    idea: Watch sunset\n    done:\n")
        (vault / "30 - Daily/2026-07-11.md").write_text(
            "# J\n### Live\n- [x] 🌱 Watch sunset\n", encoding="utf-8"
        )
        fake = FakeLiveTodoist()
        client.app.state.build_commit_clients = lambda v, cfg: (fake, None)
        assert self._post_live(client).json()["micro_adventure_logged"] is True
        history = micro_adventure.read_history(vault / LOG_REL)
        assert [ (e.id, e.done) for e in history ] == [("ma03", None), ("ma07", True)]
