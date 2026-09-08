"""Integration tests for main.py — T9 gate (routes, token guard, run-state)."""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))
import main as main_mod  # noqa: E402
import runstate as rs  # noqa: E402
import shadow  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent.parent / "gather"))
import tdtb_gather as gather  # noqa: E402


MUTATING_ROUTES = ("/gather", "/digest", "/adjust", "/sequence", "/commit")

POOL_ITEMS = [
    {"name": "Beta", "path": "50 - Operations/Projects/Beta.md", "types": ["project"],
     "urgency": "2", "deadline": None, "priority_score": 10, "assigned": False},
    {"name": "Alpha", "path": "50 - Operations/Projects/Alpha.md", "types": ["project"],
     "urgency": "4", "deadline": "2026-07-20", "priority_score": 30, "assigned": False},
    {"name": "Gamma", "path": "50 - Operations/Projects/Gamma.md", "types": ["project"],
     "urgency": "4", "deadline": "2026-07-01", "priority_score": 20, "assigned": False},
]


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


class TestRouteShape:
    def test_health_tokenless(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json() == {
            "status": "ok",
            "judgment_model": "openai/gpt-5.6-luna",
        }

    def test_config_tokenless_bootstrap(self, client):
        r = client.get("/config")
        assert r.status_code == 200
        assert r.json()["bootstrap_needed"] is True

    def test_config_reads_config_file(self, client, vault):
        cfg = vault / "00 - META/Skill-Configs/tdtb-bridger.md"
        cfg.parent.mkdir(parents=True)
        cfg.write_text(
            "## Defaults\n| Key | Value |\n|---|---|\n| eod | 11:59 PM |\n",
            encoding="utf-8",
        )
        r = client.get("/config")
        body = r.json()
        assert body["bootstrap_needed"] is False
        assert "Defaults" in body["sections"]
        assert body["validation"]["valid"] is False  # required sections missing

    def test_stub_routes_return_501_with_task_pointer(self, client):
        # /adjust and /sequence were wired to real judgment-layer handlers in
        # T11 (see tests/test_judgment_routes.py for their coverage) — only
        # /commit remains a stub pending T14/T15.
        r = client.post("/commit", headers=_auth(client))
        assert r.status_code == 501
        assert "T14" in r.json()["detail"]

    def test_503_when_vault_root_unconfigured(self, monkeypatch):
        monkeypatch.delenv(main_mod.VAULT_ROOT_ENV, raising=False)
        app = main_mod.create_app()
        c = TestClient(app)
        r = c.get("/config")
        assert r.status_code == 503


class TestTokenGuard:
    @pytest.mark.parametrize("route", MUTATING_ROUTES)
    def test_missing_token_403(self, client, route):
        assert client.post(route).status_code == 403

    @pytest.mark.parametrize("route", MUTATING_ROUTES)
    def test_wrong_token_403(self, client, route):
        r = client.post(route, headers={"X-TDTB-Token": "wrong-token"})
        assert r.status_code == 403

    @pytest.mark.parametrize("route", MUTATING_ROUTES)
    def test_correct_token_passes_guard(self, client, route):
        r = client.post(route, headers=_auth(client))
        assert r.status_code != 403


class TestGatherAndRunstate:
    def test_gather_writes_runstate_and_cache(self, client, vault):
        r = client.post("/gather", headers=_auth(client))
        assert r.status_code == 200
        body = r.json()
        assert body["pool_count"] == 0 and body["assigned_count"] == 0

        today = gather.effective_date(datetime.now())
        rs_path = vault / f"00 - META/Cache/tdtb-runstate-{today}.md"
        assert rs_path.is_file()
        text = rs_path.read_text(encoding="utf-8")
        # Frontmatter shape per SKILL.md § 0.8
        assert text.startswith("---\n")
        assert f"valid_date: '{today}'" in text
        assert "written_at: '" in text
        # JSON body block parseable by gather's own reader
        data = gather._extract_json_block(text)
        assert data is not None
        assert data["plan_manifest"] == []
        assert data["selections"] == []
        assert "dedup_map" in data and "re_included" in data

        # active-inventory cache also written
        assert (vault / gather.CACHE_REL_PATH).is_file()

    def test_gather_deletes_stale_runstates(self, client, vault):
        cache_dir = vault / "00 - META/Cache"
        cache_dir.mkdir(parents=True)
        today = gather.effective_date(datetime.now())
        stale = cache_dir / f"tdtb-runstate-{today - timedelta(days=3)}.md"
        stale.write_text("---\nvalid_date: 'x'\n---\n", encoding="utf-8")
        client.post("/gather", headers=_auth(client))
        assert not stale.exists()
        assert (cache_dir / f"tdtb-runstate-{today}.md").is_file()

    def test_runstate_loadable_by_gather_load_runstate(self, vault):
        vault.mkdir()
        d = date(2026, 7, 10)
        rs.write_runstate(vault, d, rs.build_runstate({"selections": [{"id": "x"}]}))
        diff_base, state = gather.load_runstate(vault, d + timedelta(days=1))
        assert diff_base == d
        assert state["selections"] == [{"id": "x"}]


class TestDigestDeterminism:
    def _digest(self, client, payload):
        r = client.post("/digest", json=payload, headers=_auth(client))
        assert r.status_code == 200
        return r.json()

    def test_same_input_twice_identical_output(self, client):
        payload = {"pool_items": POOL_ITEMS, "assigned_items": [], "today": "2026-07-12"}
        assert self._digest(client, payload) == self._digest(client, payload)

    def test_input_order_does_not_change_output(self, client):
        a = {"pool_items": POOL_ITEMS, "assigned_items": [], "today": "2026-07-12"}
        b = {"pool_items": list(reversed(POOL_ITEMS)), "assigned_items": [], "today": "2026-07-12"}
        assert self._digest(client, a)["suggested"] == self._digest(client, b)["suggested"]

    def test_digest_honors_same_day_drop_on_both_surfaces(self, client, vault):
        """A dropped source stays out of POST /digest even when the caller
        supplies fresh pool and assigned inputs without refreshing /plan-inputs."""
        today = gather.effective_date(datetime.now())
        assigned = {
            "name": "Dropped Assigned",
            "path": "Projects/Dropped Assigned.md",
            "assigned": True,
        }
        suggested = {
            "name": "Dropped Suggested",
            "path": "Projects/Dropped Suggested.md",
            "assigned": False,
        }
        rs.update_runstate(vault, today, {
            "dropped": [
                {"identity": assigned["path"], "name": assigned["name"]},
                {"identity": suggested["path"], "name": suggested["name"]},
            ],
        })
        response = client.post(
            "/digest",
            headers=_auth(client),
            json={
                "pool_items": [suggested],
                "assigned_items": [assigned],
                "today": str(today),
            },
        )
        assert response.status_code == 200
        body = response.json()
        names = {
            row["name"]
            for surface in ("assigned", "suggested")
            for row in body[surface]
        }
        assert "Dropped Assigned" not in names
        assert "Dropped Suggested" not in names

    def test_ranking_order(self, client):
        payload = {"pool_items": POOL_ITEMS, "assigned_items": [], "today": "2026-07-12"}
        names = [i["name"] for i in self._digest(client, payload)["suggested"]]
        # Gamma: urgency 4 + overdue; Alpha: urgency 4, future deadline; Beta: urgency 2
        assert names == ["Gamma", "Alpha", "Beta"]

    def test_assigned_excluded_from_suggested(self, client):
        assigned = [dict(POOL_ITEMS[1], assigned=True)]
        payload = {"pool_items": POOL_ITEMS, "assigned_items": assigned, "today": "2026-07-12"}
        body = self._digest(client, payload)
        assert [i["name"] for i in body["assigned"]] == ["Alpha"]
        assert "Alpha" not in [i["name"] for i in body["suggested"]]

    def test_live_gather_digest_deterministic(self, client):
        r1 = client.post("/digest", headers=_auth(client))
        r2 = client.post("/digest", headers=_auth(client))
        assert r1.status_code == r2.status_code == 200
        assert r1.json() == r2.json()


class TestRecentSelections:
    SEL = [{"id": "t1", "path": "50 - Operations/Projects/Alpha.md", "blocks": 2}]

    def test_append_creates_file_in_skill_shape(self, tmp_path):
        path = rs.append_recent_selection(tmp_path, date(2026, 7, 12), self.SEL)
        assert path == tmp_path / "00 - META/Cache/tdtb-recent-selections.md"
        fm = gather.parse_frontmatter(path.read_text(encoding="utf-8"))
        assert fm is not None
        runs = fm["runs"]
        assert len(runs) == 1
        assert str(runs[0]["date"]) == "2026-07-12"
        assert runs[0]["selections"][0]["path"] == "50 - Operations/Projects/Alpha.md"
        assert runs[0]["selections"][0]["blocks"] == 2

    def test_newest_first_max_five(self, tmp_path):
        for i in range(7):
            rs.append_recent_selection(tmp_path, date(2026, 7, 1 + i), self.SEL)
        runs = rs.read_recent_selections(tmp_path)
        assert len(runs) == 5
        assert [str(r["date"]) for r in runs] == [
            "2026-07-07", "2026-07-06", "2026-07-05", "2026-07-04", "2026-07-03"]

    def test_same_date_replaces_not_duplicates(self, tmp_path):
        rs.append_recent_selection(tmp_path, date(2026, 7, 12), self.SEL)
        rs.append_recent_selection(tmp_path, date(2026, 7, 12),
                                   [{"id": "t2", "path": "p", "blocks": 1}])
        runs = rs.read_recent_selections(tmp_path)
        assert len(runs) == 1
        assert runs[0]["selections"][0]["id"] == "t2"

    def test_fractional_block_duration_round_trips(self, tmp_path):
        rs.append_recent_selection(
            tmp_path,
            date(2026, 7, 12),
            [{"id": "quick", "path": "p", "blocks": 0.5}],
        )
        runs = rs.read_recent_selections(tmp_path)
        assert runs[0]["selections"][0]["blocks"] == 0.5

    def test_missing_file_reads_empty(self, tmp_path):
        assert rs.read_recent_selections(tmp_path) == []


# ---------------------------------------------------------------------------
# T15: POST /commit?mode=live — real writes via injected clients
# ---------------------------------------------------------------------------

class FakeLiveTodoist:
    """Minimal in-memory Todoist for the live route test (see
    tests/test_commit.py's FakeTodoist for the fuller-featured original)."""

    def __init__(self):
        self._tasks: dict[str, dict] = {}
        self._seq = 3000

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


class FakeLiveStore:
    def __init__(self, calendars=None):
        self._cals = calendars or []

    def calendars(self):
        return list(self._cals)


LIVE_DIGEST = {"assigned": [{"name": "Garage", "path": "P/Garage.md"}], "suggested": []}
LIVE_SEQUENCE = {"sequence": [{"id": "Garage", "start": "09:00", "end": "10:00", "zone": "any"}]}


def _fake_live_state(config, vault_root):
    return {
        "todoist_tasks": [],
        "calendar_events": [],
        "vault_frontmatter": {"P/Garage.md": {"assigned": False}},
        "daily_note_text": "# Journal\n",
    }


class TestLiveCommit:
    def _seed_vault(self, vault: Path) -> None:
        (vault / "P").mkdir(parents=True, exist_ok=True)
        (vault / "P/Garage.md").write_text("---\nassigned: false\n---\nbody\n", encoding="utf-8")
        (vault / "30 - Daily").mkdir(parents=True, exist_ok=True)
        (vault / "30 - Daily/2026-07-12.md").write_text("# Journal\n", encoding="utf-8")
        config_path = vault / "00 - META" / "Skill-Configs" / "tdtb-bridger.md"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(
            "## Defaults\n"
            "| Key | Value |\n|---|---|\n| eod | 11:59 PM |\n\n"
            "## Template Blocks\n"
            "### Trinoor Hours\n"
            "| Slot | Start | End |\n|---|---|---|\n"
            "| Morning | 12:00 AM | 11:59 PM |\n",
            encoding="utf-8",
        )
        # P3-02: seed today's server digest identity index so the commit
        # eligibility boundary recognizes "Garage" as a server-known assignment.
        rs.write_digest_index(
            vault, date(2026, 7, 12),
            [{"name": "Garage", "todoist_id": "", "path": "P/Garage.md",
              "surface": "assigned"}],
        )

    def test_mode_live_injected_clients_succeeds(self, client, vault, monkeypatch):
        self._seed_vault(vault)
        # Freeze the route's date resolution to the seeded day so the daily-note
        # surface targets the file _seed_vault created — otherwise the live path
        # (main.py: gather.effective_date(datetime.now())) uses the real system
        # date and patches a note that does not exist. Same module singleton main
        # imports, so this reaches the route's own call.
        monkeypatch.setattr(gather, "effective_date", lambda now: date(2026, 7, 12))
        monkeypatch.setattr(shadow, "gather_live_state", _fake_live_state)
        client.app.state.build_commit_clients = lambda v, cfg: (FakeLiveTodoist(), FakeLiveStore())
        # FEEDBACK-24: live commit is gated on an explicit Day Setup confirm —
        # seed it through the real route so the write path is exercisable.
        assert client.post("/day-setup", json={"anchor": "09:00"},
                           headers=_auth(client)).status_code == 200

        r = client.post(
            "/commit?mode=live",
            headers=_auth(client),
            json={"digest": LIVE_DIGEST, "sequence": LIVE_SEQUENCE, "config": {}},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert body["resumed"] is False
        assert set(body["surfaces"]) == {"todoist", "vault_flips", "daily_note",
                                          "captures", "calendar"}
        # the daily note actually got the plan section patched in
        text = (vault / "30 - Daily/2026-07-12.md").read_text(encoding="utf-8")
        assert "# TDTB Plan" in text

    def test_mode_live_resume_true_returns_200(self, client, vault, monkeypatch):
        self._seed_vault(vault)
        monkeypatch.setattr(gather, "effective_date", lambda now: date(2026, 7, 12))
        monkeypatch.setattr(shadow, "gather_live_state", _fake_live_state)
        client.app.state.build_commit_clients = lambda v, cfg: (FakeLiveTodoist(), FakeLiveStore())
        assert client.post("/day-setup", json={"anchor": "09:00"},
                           headers=_auth(client)).status_code == 200

        r = client.post(
            "/commit?mode=live&resume=true",
            headers=_auth(client),
            json={"digest": LIVE_DIGEST, "sequence": LIVE_SEQUENCE, "config": {}},
        )
        assert r.status_code == 200
        assert r.json()["resumed"] is True

    def test_bare_commit_still_501(self, client):
        r = client.post("/commit", headers=_auth(client))
        assert r.status_code == 501
        assert "T14" in r.json()["detail"]

    def test_mode_shadow_unchanged(self, client, vault, monkeypatch):
        monkeypatch.setattr(shadow, "gather_live_state", _fake_live_state)
        # P3-02: seed today's digest index so the eligibility boundary
        # recognizes "Garage" as a server-known assignment.
        rs.write_digest_index(
            vault, gather.effective_date(datetime.now()),
            [{"name": "Garage", "todoist_id": "", "path": "P/Garage.md",
              "surface": "assigned"}],
        )
        r = client.post(
            "/commit?mode=shadow",
            headers=_auth(client),
            json={"digest": LIVE_DIGEST, "sequence": LIVE_SEQUENCE, "config": {}},
        )
        assert r.status_code == 200
        assert "entries" in r.json()

    def test_mode_live_missing_token_403(self, client):
        r = client.post(
            "/commit?mode=live",
            json={"digest": LIVE_DIGEST, "sequence": LIVE_SEQUENCE, "config": {}},
        )
        assert r.status_code == 403

    def test_mode_live_plan_error_returns_422(self, client, vault, monkeypatch):
        self._seed_vault(vault)
        monkeypatch.setattr(gather, "effective_date", lambda now: date(2026, 7, 12))

        def fake_live_state_no_calendar(config, vault_root):
            return {
                "todoist_tasks": [], "calendar_events": [],
                "vault_frontmatter": {}, "daily_note_text": "# Journal\n",
            }

        monkeypatch.setattr(shadow, "gather_live_state", fake_live_state_no_calendar)
        # store=None -> no calendar titles resolve -> the calendar CREATE row
        # below can never plan -> CommitPlanError -> 422.
        client.app.state.build_commit_clients = lambda v, cfg: (FakeLiveTodoist(), None)
        assert client.post("/day-setup", json={"anchor": "09:00"},
                           headers=_auth(client)).status_code == 200
        rs.update_runstate(vault, date(2026, 7, 12), {
            "work_allotment_minutes": 30,
            "schedulable": {"minting": {"on": True}},
        })

        digest = {"assigned": [], "suggested": []}
        sequence = {"sequence": [{"id": "🌊 Minting", "start": "14:00", "end": "15:00", "zone": "any"}]}
        r = client.post(
            "/commit?mode=live",
            headers=_auth(client),
            json={"digest": digest, "sequence": sequence, "config": {}},
        )
        assert r.status_code == 422
        assert "plan refused" in r.json()["detail"]


# ---------------------------------------------------------------------------
# T4 (ui-parity) — Day Setup state + /plan-inputs time/capacity blocks
# ---------------------------------------------------------------------------

def _write_min_config(vault: Path) -> None:
    cfg = vault / "00 - META/Skill-Configs/tdtb-bridger.md"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(
        "## Defaults\n"
        "| Key | Value |\n|---|---|\n"
        "| eod | 11:59 PM |\n"
        "| anchor.round_to_minutes | 15 |\n"
        "| buffering.standard_pct | 0.19 |\n"
        "| buffering.minimal_pct | 0.11 |\n"
        "| buffering.off_pct | 0.00 |\n"
        "| caps.deep | 2 |\n"
        "| caps.mixed | 4 |\n"
        "\n## Anchored Lifestyle Blocks\n"
        "| Block | Type | Start | End | Duration | Days |\n"
        "|---|---|---|---|---|---|\n"
        "| Sudsing | hard | 5:45 PM | — | 30m | daily |\n"
        "| Foods Dinner | window | 6:00 PM | 8:30 PM | 60m | daily |\n",
        encoding="utf-8",
    )


class TestDaySetup:
    def test_day_setup_requires_token(self, client):
        r = client.post("/day-setup", json={})
        assert r.status_code in (401, 403)

    def test_day_setup_persists_blob_and_derives_re_included(self, client, vault):
        _write_min_config(vault)
        payload = {
            "anchor": "18:00", "eod": "22:00", "buffering": "standard",
            "schedulable": {"minting": {"on": False, "n": 2},
                            "qt": {"on": True, "n": 1}},
            "anchored": [{"id": "Sudsing", "on": True},          # past 17:45 -> re-include
                          {"id": "Foods Dinner", "skip_today": True}],
            "captures": {"intention": "ship T4", "megan_nicety": "hi Meegy",
                          "stoic_intention": "temperance"},
        }
        r = client.post("/day-setup", json=payload, headers=_auth(client))
        assert r.status_code == 200
        body = r.json()
        assert "Sudsing" in body["re_included"]

        today = gather.effective_date(datetime.now())
        note = vault / rs.runstate_rel_path(today)
        assert note.is_file()
        state = gather._extract_json_block(note.read_text(encoding="utf-8"))
        assert state["anchor"] == "18:00" and state["eod"] == "22:00"
        assert state["buffering"] == "standard"
        assert state["intention"] == "ship T4"
        assert state["megan_nicety"] == "hi Meegy"
        assert "Sudsing" in state["re_included"]
        assert state["schedulable"]["qt"]["on"] is True

    def test_day_setup_merge_preserves_existing_keys(self, client, vault):
        _write_min_config(vault)
        today = gather.effective_date(datetime.now())
        rs.write_runstate(vault, today, rs.build_runstate(
            {"micro_adventure": {"idea": "stargaze"}}))
        client.post("/day-setup", json={"anchor": "18:00"}, headers=_auth(client))
        state = gather._extract_json_block(
            (vault / rs.runstate_rel_path(today)).read_text(encoding="utf-8"))
        assert state["micro_adventure"] == {"idea": "stargaze"}
        assert state["anchor"] == "18:00"

    def test_day_setup_syncs_mint_sessions_and_total(self, client, vault):
        cfg = vault / "00 - META/Skill-Configs/tdtb-bridger.md"
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text(
            "## Defaults\n"
            "| Key | Value |\n|---|---|\n"
            "| eod | 11:59 PM |\n\n"
            "## Template Blocks\n"
            "### Trinoor Hours\n"
            "| Slot | Start | End |\n|---|---|---|\n"
            "| Morning | 8:30 AM | 10:00 AM |\n",
            encoding="utf-8",
        )
        r = client.post("/day-setup", json={
            "schedulable": {"minting": {
                "on": True,
                "n": 99,
                "sessions": [
                    "mint:morning:08:30",
                    "Mint Morning · 09:00",
                    "not-a-session",
                ],
            }},
            "work_allotment_minutes": 240,
        }, headers=_auth(client))
        assert r.status_code == 200, r.text

        today = gather.effective_date(datetime.now())
        state = gather._extract_json_block(
            (vault / rs.runstate_rel_path(today)).read_text(encoding="utf-8"))
        assert state["schedulable"]["minting"] == {
            "on": True,
            "n": 2,
            "sessions": ["mint:morning:08:30", "mint:morning:09:00"],
        }
        assert state["work_allotment_minutes"] == 60


def _write_day_presets_config(vault: Path) -> None:
    """Like _write_min_config, but adds a valid `## Day Presets` section +
    `Defaults.work_allotment_minutes` so tri-state persistence can be tested
    against real preset resolution (T18b.2+)."""
    cfg = vault / "00 - META/Skill-Configs/tdtb-bridger.md"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(
        "## Defaults\n"
        "| Key | Value |\n|---|---|\n"
        "| eod | 11:59 PM |\n"
        "| anchor.round_to_minutes | 15 |\n"
        "| buffering.standard_pct | 0.19 |\n"
        "| buffering.minimal_pct | 0.11 |\n"
        "| buffering.off_pct | 0.00 |\n"
        "| caps.deep | 2 |\n"
        "| caps.mixed | 4 |\n"
        "| work_allotment_minutes | 240 |\n"
        "\n## Day Presets\n"
        "| Name | Days | Zones | Work Allotment (min) | Default |\n"
        "|---|---|---|---|---|\n"
        "| Workday | workdays | Trinoor Hours | 240 |  |\n"
        "| Weekend | weekends |  | 0 |  |\n"
        "| Default | daily | Trinoor Hours |  | true |\n"
        "\n## Template Blocks\n"
        "### Trinoor Hours\n"
        "| Slot | Start | End |\n"
        "|---|---|---|\n"
        "| Morning | 8:30 AM | 12:30 PM |\n"
        "| Afternoon | 1:30 PM | 5:00 PM |\n"
        "\n## Anchored Lifestyle Blocks\n"
        "| Block | Type | Start | End | Duration | Days |\n"
        "|---|---|---|---|---|---|\n"
        "| Sudsing | hard | 5:45 PM | — | 30m | daily |\n"
        "| Foods Dinner | window | 6:00 PM | 8:30 PM | 60m | daily |\n",
        encoding="utf-8",
    )


class TestT18b2TriStatePersistence:
    """T18b.2: day_preset + work_allotment_minutes use Pydantic field-presence
    semantics. Omitted preserves the dated override; explicit null removes it
    (restores config default); 0 persists as the explicit Mint disable."""

    def test_runstate_defaults_include_tri_state_keys(self):
        assert "day_preset" in rs.RUNSTATE_DEFAULTS
        assert "work_allotment_minutes" in rs.RUNSTATE_DEFAULTS
        assert rs.RUNSTATE_DEFAULTS["day_preset"] is None
        assert rs.RUNSTATE_DEFAULTS["work_allotment_minutes"] is None

    def test_day_setup_keys_include_tri_state(self):
        assert "day_preset" in main_mod._DAY_SETUP_KEYS
        assert "work_allotment_minutes" in main_mod._DAY_SETUP_KEYS

    def test_preset_string_persists(self, client, vault):
        _write_day_presets_config(vault)
        r = client.post("/day-setup", json={"day_preset": "Workday"},
                        headers=_auth(client))
        assert r.status_code == 200, r.text
        today = gather.effective_date(datetime.now())
        state = gather._extract_json_block(
            (vault / rs.runstate_rel_path(today)).read_text(encoding="utf-8"))
        assert state["day_preset"] == "Workday"

    def test_preset_omitted_preserves_existing(self, client, vault):
        _write_day_presets_config(vault)
        today = gather.effective_date(datetime.now())
        rs.write_runstate(vault, today, rs.build_runstate({"day_preset": "Weekend"}))
        # Body OMITS day_preset — must not clear the existing override
        r = client.post("/day-setup", json={"anchor": "09:00"},
                        headers=_auth(client))
        assert r.status_code == 200, r.text
        state = gather._extract_json_block(
            (vault / rs.runstate_rel_path(today)).read_text(encoding="utf-8"))
        assert state["day_preset"] == "Weekend"
        assert state["anchor"] == "09:00"

    def test_preset_null_clears_override(self, client, vault):
        _write_day_presets_config(vault)
        today = gather.effective_date(datetime.now())
        rs.write_runstate(vault, today, rs.build_runstate({"day_preset": "Workday"}))
        # Body sends explicit null — must clear the override
        r = client.post("/day-setup", json={"day_preset": None},
                        headers=_auth(client))
        assert r.status_code == 200, r.text
        state = gather._extract_json_block(
            (vault / rs.runstate_rel_path(today)).read_text(encoding="utf-8"))
        assert state["day_preset"] is None

    def test_allotment_positive_persists(self, client, vault):
        _write_day_presets_config(vault)
        r = client.post("/day-setup", json={"work_allotment_minutes": 240},
                        headers=_auth(client))
        assert r.status_code == 200, r.text
        today = gather.effective_date(datetime.now())
        state = gather._extract_json_block(
            (vault / rs.runstate_rel_path(today)).read_text(encoding="utf-8"))
        assert state["work_allotment_minutes"] == 240

    def test_allotment_omitted_preserves_existing(self, client, vault):
        _write_day_presets_config(vault)
        today = gather.effective_date(datetime.now())
        rs.write_runstate(vault, today, rs.build_runstate({"work_allotment_minutes": 180}))
        r = client.post("/day-setup", json={"anchor": "09:00"},
                        headers=_auth(client))
        assert r.status_code == 200, r.text
        state = gather._extract_json_block(
            (vault / rs.runstate_rel_path(today)).read_text(encoding="utf-8"))
        assert state["work_allotment_minutes"] == 180
        assert state["anchor"] == "09:00"

    def test_allotment_null_clears_override(self, client, vault):
        _write_day_presets_config(vault)
        today = gather.effective_date(datetime.now())
        rs.write_runstate(vault, today, rs.build_runstate({"work_allotment_minutes": 240}))
        r = client.post("/day-setup", json={"work_allotment_minutes": None},
                        headers=_auth(client))
        assert r.status_code == 200, r.text
        state = gather._extract_json_block(
            (vault / rs.runstate_rel_path(today)).read_text(encoding="utf-8"))
        assert state["work_allotment_minutes"] is None

    def test_allotment_zero_persists_as_disable(self, client, vault):
        _write_day_presets_config(vault)
        r = client.post("/day-setup", json={"work_allotment_minutes": 0},
                        headers=_auth(client))
        assert r.status_code == 200, r.text
        today = gather.effective_date(datetime.now())
        state = gather._extract_json_block(
            (vault / rs.runstate_rel_path(today)).read_text(encoding="utf-8"))
        assert state["work_allotment_minutes"] == 0

    def test_allotment_not_divisible_by_15_rejected(self, client, vault):
        _write_day_presets_config(vault)
        r = client.post("/day-setup", json={"work_allotment_minutes": 25},
                        headers=_auth(client))
        assert r.status_code == 422, r.text
        assert "15" in r.text or "allotment" in r.text.lower()

    def test_allotment_negative_rejected(self, client, vault):
        _write_day_presets_config(vault)
        r = client.post("/day-setup", json={"work_allotment_minutes": -30},
                        headers=_auth(client))
        assert r.status_code == 422, r.text

    @pytest.mark.parametrize("value", [30.0, "30", True])
    def test_allotment_rejects_non_integer_json_types(self, client, vault, value):
        """Canonical minutes are a JSON integer, never a coerced float,
        string, or boolean."""
        _write_day_presets_config(vault)
        r = client.post("/day-setup", json={"work_allotment_minutes": value},
                        headers=_auth(client))
        assert r.status_code == 422, r.text

    def test_round_trip_through_plan_inputs(self, client, vault):
        _write_day_presets_config(vault)
        client.post("/day-setup", json={
            "day_preset": "Weekend",
            "work_allotment_minutes": 60,
        }, headers=_auth(client))
        body = client.get("/plan-inputs").json()
        ds = body["day_setup"]
        assert ds.get("day_preset") == "Weekend"
        assert ds.get("work_allotment_minutes") == 60

    def test_omitted_null_zero_round_trip_distinctly(self, client, vault):
        """The three semantics must round-trip distinctly through a single
        runstate note: omitted preserves, null clears, 0 disables."""
        _write_day_presets_config(vault)
        today = gather.effective_date(datetime.now())
        # 1) Seed a positive override
        client.post("/day-setup", json={"work_allotment_minutes": 240},
                    headers=_auth(client))
        state = gather._extract_json_block(
            (vault / rs.runstate_rel_path(today)).read_text(encoding="utf-8"))
        assert state["work_allotment_minutes"] == 240
        # 2) Omitted — must preserve 240
        client.post("/day-setup", json={"anchor": "09:00"},
                    headers=_auth(client))
        state = gather._extract_json_block(
            (vault / rs.runstate_rel_path(today)).read_text(encoding="utf-8"))
        assert state["work_allotment_minutes"] == 240
        # 3) Explicit null — must clear to None
        client.post("/day-setup", json={"work_allotment_minutes": None},
                    headers=_auth(client))
        state = gather._extract_json_block(
            (vault / rs.runstate_rel_path(today)).read_text(encoding="utf-8"))
        assert state["work_allotment_minutes"] is None
        # 4) Explicit 0 — must persist as 0 (distinct from None)
        client.post("/day-setup", json={"work_allotment_minutes": 0},
                    headers=_auth(client))
        state = gather._extract_json_block(
            (vault / rs.runstate_rel_path(today)).read_text(encoding="utf-8"))
        assert state["work_allotment_minutes"] == 0


class TestPlanInputsTimeCapacity:
    def test_plan_inputs_carries_time_capacity_day_setup(self, client, vault):
        _write_min_config(vault)
        r = client.get("/plan-inputs")
        assert r.status_code == 200
        body = r.json()
        for key in ("time", "capacity", "day_setup"):
            assert key in body, key
        t = body["time"]
        assert set(t) >= {"now", "anchor", "effective_eod", "total_blocks",
                          "no_time_left"}
        c = body["capacity"]
        assert set(c) >= {"total", "free", "overassigned", "remaining",
                          "legend", "counters"}

    def test_plan_inputs_time_honors_day_setup_overrides(self, client, vault):
        _write_min_config(vault)
        client.post("/day-setup", json={"anchor": "18:00", "eod": "21:00"},
                    headers=_auth(client))
        body = client.get("/plan-inputs").json()
        assert body["time"]["anchor"] == "18:00"
        assert body["time"]["effective_eod"] == "21:00"
        assert body["time"]["total_blocks"] == 6
        assert body["day_setup"]["anchor"] == "18:00"


class TestT18b5ReadContracts:
    @pytest.fixture(autouse=True)
    def _frozen_workday(self, monkeypatch):
        # Preset resolution follows the real calendar — freeze to a Monday so
        # the Workday-row assertions hold on weekend test runs too.
        monkeypatch.setattr(main_mod.gather, "effective_date",
                            lambda _now: date(2026, 7, 13))

    def test_default_contract_is_resolved_and_fingerprint_is_stable(self, client, vault):
        _write_day_presets_config(vault)

        first = client.get("/plan-inputs").json()
        second = client.get("/plan-inputs").json()
        semantics = first["day_semantics"]

        assert semantics["selected_preset"]["name"] == "Workday"
        assert semantics["resolution_source"] == "matched_row"
        assert semantics["effective_allotment_minutes"] == 240
        assert semantics["mint_enabled"] is True
        assert semantics["enabled_zones"]
        assert first["planning_config_fingerprint"] == second["planning_config_fingerprint"]

    def test_dated_preset_override_reaches_both_read_contracts(self, client, vault):
        _write_day_presets_config(vault)
        client.post("/day-setup", json={"day_preset": "Weekend"}, headers=_auth(client))

        plan = client.get("/plan-inputs").json()
        preview = client.get("/capacity-preview").json()

        for body in (plan, preview):
            semantics = body["day_semantics"]
            assert semantics["selected_preset"]["name"] == "Weekend"
            assert semantics["resolution_source"] == "dated_override"
            assert semantics["effective_allotment_minutes"] == 0
            assert semantics["mint_enabled"] is False
            assert len(body["planning_config_fingerprint"]) == 64

    def test_omitted_null_and_zero_allotment_resolve_distinctly(self, client, vault):
        _write_day_presets_config(vault)

        client.post("/day-setup", json={"work_allotment_minutes": 300}, headers=_auth(client))
        assert client.get("/plan-inputs").json()["day_semantics"]["effective_allotment_minutes"] == 300

        # Omitted preserves the dated override.
        client.post("/day-setup", json={"anchor": "09:00"}, headers=_auth(client))
        assert client.get("/plan-inputs").json()["day_semantics"]["effective_allotment_minutes"] == 300

        # Explicit null clears it back to the resolved Workday value.
        client.post("/day-setup", json={"work_allotment_minutes": None}, headers=_auth(client))
        cleared = client.get("/plan-inputs").json()
        assert cleared["day_semantics"]["effective_allotment_minutes"] == 240
        assert cleared["day_semantics"]["mint_enabled"] is True

        # Explicit zero is a dated disable, not a missing value.
        client.post("/day-setup", json={"work_allotment_minutes": 0}, headers=_auth(client))
        disabled = client.get("/plan-inputs").json()
        assert disabled["day_setup"]["work_allotment_minutes"] == 0
        assert disabled["day_semantics"]["effective_allotment_minutes"] == 0
        assert disabled["day_semantics"]["mint_enabled"] is False

    def test_planning_fingerprint_changes_when_day_semantics_change(self, client, vault):
        _write_day_presets_config(vault)
        before = client.get("/plan-inputs").json()["planning_config_fingerprint"]

        cfg = vault / "00 - META/Skill-Configs/tdtb-bridger.md"
        text = cfg.read_text(encoding="utf-8").replace(
            "| Workday | workdays | Trinoor Hours | 240 |  |",
            "| Workday | workdays | Trinoor Hours | 300 |  |",
        )
        cfg.write_text(text, encoding="utf-8")

        after = client.get("/plan-inputs").json()["planning_config_fingerprint"]
        assert after != before

    def test_plan_inputs_exposes_day_semantics_and_fingerprint(self, client, vault):
        _write_day_presets_config(vault)

        body = client.get("/plan-inputs").json()

        assert set(body) >= {"day_semantics", "planning_config_fingerprint"}
        assert body["day_semantics"]["available_presets"]
        assert len(body["planning_config_fingerprint"]) == 64

    def test_capacity_preview_resolves_merged_proposed_day_setup(self, client, vault):
        _write_day_presets_config(vault)

        body = client.get("/capacity-preview", params={
            "day_setup": json.dumps({
                "day_preset": "Weekend", "work_allotment_minutes": 0,
            }),
        }).json()

        assert body["day_semantics"]["selected_preset"]["name"] == "Weekend"
        assert body["day_semantics"]["effective_allotment_minutes"] == 0
        assert body["day_semantics"]["mint_enabled"] is False
        assert len(body["planning_config_fingerprint"]) == 64

    def test_bootstrap_plan_inputs_returns_deterministic_contract_warnings(self, client):
        body = client.get("/plan-inputs").json()

        assert body["day_semantics"]["selected_preset"] is None
        assert body["day_semantics"]["warnings"]
        assert len(body["planning_config_fingerprint"]) == 64

    def test_malformed_day_presets_are_reported_not_500(self, client, vault):
        cfg = vault / "00 - META/Skill-Configs/tdtb-bridger.md"
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text(
            "## Defaults\n| Key | Value |\n|---|---|\n"
            "| work_allotment_minutes | 240 |\n\n"
            "## Day Presets\n"
            "| Name | Days | Zones | Work Allotment (min) | Default |\n"
            "|---|---|---|---|---|\n"
            "| Workday | workdays |  | 240 | |\n",
            encoding="utf-8",
        )

        response = client.get("/plan-inputs")

        assert response.status_code == 200
        assert response.json()["day_semantics"]["errors"]

        preview = client.get("/capacity-preview")
        assert preview.status_code == 200
        assert preview.json()["day_semantics"]["errors"]
        assert len(preview.json()["planning_config_fingerprint"]) == 64


class TestSequenceInjection:
    """T5 (ui-parity): /sequence injects schedulable blocks server-side and
    appends Trinoor zone backdrop rows to the proposal."""

    def _echo_proposal(self, assigned, config, anchored_blocks):
        rows, t = [], 9 * 60
        for a in assigned:
            dur = int(a.get("duration") or 30)
            rows.append({"id": a.get("id") or a.get("name"),
                         "start": f"{t//60:02d}:{t%60:02d}",
                         "end": f"{(t+dur)//60:02d}:{(t+dur)%60:02d}",
                         "zone": "any"})
            t += dur
        return {"sequence": rows, "rationale": "echo"}

    def test_sequence_injects_blocks_and_zone_rows(self, client, vault, monkeypatch):
        _write_min_config(vault)
        captured = {}

        def fake_propose(assigned, config, anchored_blocks, ctx=None):
            captured["assigned"] = assigned
            return self._echo_proposal(assigned, config, anchored_blocks)

        monkeypatch.setattr(main_mod.judgment, "propose_sequence", fake_propose)
        monkeypatch.setattr(main_mod.planning.sequence, "validate_sequence",
                            lambda *a, **k: type("R", (), {
                                "ok": True, "hard_errors": [], "warnings": []})())

        # Freeze the anchor: injection depends on live time (Minting needs
        # remaining work-window blocks after the anchor), so pin a Monday
        # morning to make the test wall-clock-independent.
        class _FrozenDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return cls(2026, 7, 13, 9, 0)  # Monday 09:00

        monkeypatch.setattr(main_mod, "datetime", _FrozenDatetime)
        rs.write_digest_index(vault, date(2026, 7, 13), [{
            "name": "Garage", "todoist_id": "", "path": "P/Garage.md",
            "surface": "assigned", "source": "vault", "duration": 60,
            "blocks": 2,
        }])
        r = client.post("/sequence", json={
            "assigned": [{"id": "Garage", "name": "Garage",
                           "path": "P/Garage.md", "duration": 60,
                           "labels": []}],
            "config": {"Template Blocks": {"Trinoor Hours": [
                {"Slot": "Morning", "Start": "8:30 AM", "End": "12:30 PM"},
                {"Slot": "Afternoon", "Start": "1:30 PM", "End": "5:00 PM"}]}},
            "anchored_blocks": [],
        }, headers=_auth(client))
        assert r.status_code == 200
        body = r.json()
        names = [a.get("id") or a.get("name") for a in captured["assigned"]]
        assert "Quick Tasks" in names            # QT default On every day
        row_ids = [row["id"] for row in body["sequence"]]
        assert "Minting" in names
        assert "🟡 Trinoor : Morning" in row_ids

    def test_invalid_pin_rejected_before_judgment(self, client, monkeypatch):
        called = False
        def fake_propose(*args, **kwargs):
            nonlocal called
            called = True
            return {"sequence": []}
        monkeypatch.setattr(main_mod.judgment, "propose_sequence", fake_propose)
        r = client.post("/sequence", json={
            "assigned": [{"id": "A", "name": "A"}],
            "config": {}, "anchored_blocks": [],
            "pinned_rows": [{"id": "foreign", "start": "09:00", "end": "09:30"}],
        }, headers=_auth(client))
        assert r.status_code == 422
        assert called is False

    def test_pin_excluded_from_movable_and_merged_exactly(self, client, monkeypatch):
        pin = {"id": "A", "start": "09:00", "end": "09:30",
               "zone": "any", "metadata": {"source": "manual"}}
        captured = {}
        def fake_propose(assigned, config, anchored_blocks, ctx=None):
            captured["assigned"] = assigned
            captured["anchored"] = anchored_blocks
            return {"sequence": [{"id": "B", "start": "10:00", "end": "10:30",
                                  "zone": "any"}], "overlap_grants": []}
        monkeypatch.setattr(main_mod.judgment, "propose_sequence", fake_propose)
        monkeypatch.setattr(main_mod.planning.sequence, "validate_sequence",
                            lambda *a, **k: type("R", (), {
                                "ok": True, "hard_errors": [], "warnings": []})())
        r = client.post("/sequence", json={
            "assigned": [{"id": "A", "name": "A"}, {"id": "B", "name": "B"}],
            "config": {}, "anchored_blocks": [], "pinned_rows": [pin],
        }, headers=_auth(client))
        assert r.status_code == 200, r.text
        assert all((item.get("id") or item.get("name")) != "A"
                   for item in captured["assigned"])
        assert any(block.get("pinned") for block in captured["anchored"])
        assert r.json()["sequence"][0] == pin

    def test_selected_mint_sessions_are_excluded_and_merged_at_exact_windows(
        self, client, vault, monkeypatch
    ):
        _write_min_config(vault)
        frozen_date = date(2026, 7, 13)
        rs.write_runstate(vault, frozen_date, rs.build_runstate({
            "schedulable": {"minting": {
                "on": True,
                "sessions": ["mint:morning:08:30"],
            }},
        }))
        captured = {}

        def fake_propose(assigned, config, anchored_blocks, ctx=None):
            captured["assigned"] = assigned
            return self._echo_proposal(assigned, config, anchored_blocks)

        monkeypatch.setattr(main_mod.judgment, "propose_sequence", fake_propose)
        monkeypatch.setattr(main_mod.planning.sequence, "validate_sequence",
                            lambda *a, **k: type("R", (), {
                                "ok": True, "hard_errors": [], "warnings": []})())

        class _FrozenDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return cls(2026, 7, 13, 9, 0)

        monkeypatch.setattr(main_mod, "datetime", _FrozenDatetime)
        rs.write_digest_index(vault, frozen_date, [{
            "name": "Garage", "todoist_id": "", "path": "P/Garage.md",
            "surface": "assigned", "source": "vault", "duration": 60,
            "blocks": 2,
        }])
        r = client.post("/sequence", json={
            "assigned": [{"id": "Garage", "name": "Garage",
                           "path": "P/Garage.md", "duration": 60}],
            "config": {"Template Blocks": {"Trinoor Hours": [
                {"Slot": "Morning", "Start": "8:30 AM", "End": "10:00 AM"},
            ]}},
            "anchored_blocks": [],
        }, headers=_auth(client))
        assert r.status_code == 200, r.text
        assert all(
            (item.get("id") or item.get("name")) != "Mint Morning · 08:30"
            for item in captured["assigned"]
        )
        mint_rows = [
            row for row in r.json()["sequence"]
            if row["id"] == "Mint Morning · 08:30"
        ]
        assert mint_rows == [{
            "id": "Mint Morning · 08:30",
            "start": "08:30",
            "end": "09:00",
            "zone": "work_hours",
            "source": "schedulable",
            "mint_session": True,
            "mint_session_id": "mint:morning:08:30",
            "calendar_class": "mint",
        }]

    def test_sequence_reconciles_a_dropped_leading_icon(self, client, monkeypatch):
        def fake_propose(assigned, config, anchored_blocks, ctx=None):
            return {"sequence": [{
                "id": "Water the Creeping Pilea",
                "start": "13:00",
                "end": "13:30",
                "zone": "any",
            }]}

        monkeypatch.setattr(main_mod.judgment, "propose_sequence", fake_propose)
        monkeypatch.setattr(main_mod.planning.sequence, "validate_sequence",
                            lambda *a, **k: type("R", (), {
                                "ok": True, "hard_errors": [], "warnings": []})())
        r = client.post("/sequence", json={
            "assigned": [{
                "id": "💧 Water the Creeping Pilea",
                "name": "💧 Water the Creeping Pilea",
            }],
            "config": {}, "anchored_blocks": [],
        }, headers=_auth(client))
        assert r.status_code == 200, r.text
        assert r.json()["sequence"][0]["id"] == "💧 Water the Creeping Pilea"


class TestEstimationCorrection:
    """G18a: Defaults estimation.correction_factor inflates assigned block
    estimates before the judgment call; 1.0/absent is a no-op."""

    def _run(self, client, vault, monkeypatch, defaults):
        _write_min_config(vault)
        today = gather.effective_date(datetime.now())
        rs.write_digest_index(vault, today, [{
            "name": "Garage", "todoist_id": "", "path": "P/Garage.md",
            "surface": "assigned", "source": "vault", "blocks": 2,
        }])
        captured = {}

        def fake_propose(assigned, config, anchored_blocks, ctx=None):
            captured["assigned"] = assigned
            return {"sequence": [{"id": "Garage", "start": "13:00",
                                   "end": "13:30", "zone": "any"}]}

        monkeypatch.setattr(main_mod.judgment, "propose_sequence", fake_propose)
        monkeypatch.setattr(main_mod.planning.sequence, "validate_sequence",
                            lambda *a, **k: type("R", (), {
                                "ok": True, "hard_errors": [], "warnings": []})())
        r = client.post("/sequence", json={
            "assigned": [{"name": "Garage", "path": "P/Garage.md", "blocks": 2},
                          {"name": "Hotel finds"}],
            "config": {"Defaults": defaults},
            "anchored_blocks": [],
        }, headers=_auth(client))
        assert r.status_code == 200
        return {i["name"]: i for i in captured["assigned"] if i.get("name")}

    def test_factor_inflates_blocks(self, client, vault, monkeypatch):
        items = self._run(client, vault, monkeypatch,
                          {"estimation.correction_factor": 1.5})
        assert items["Garage"]["blocks"] == 3       # ceil(2 * 1.5)
        assert items["Hotel finds"]["blocks"] == 2  # ceil(1 * 1.5), default 1

    def test_default_factor_is_no_op(self, client, vault, monkeypatch):
        items = self._run(client, vault, monkeypatch, {})
        assert items["Garage"]["blocks"] == 2
        assert "blocks" not in items["Hotel finds"]


class TestCommitCapturesFlow:
    """T8 (ui-parity): Day Setup captures reach the /commit shadow diff."""

    def test_shadow_commit_shows_capture_rows(self, client, vault):
        _write_min_config(vault)
        client.post("/day-setup", json={
            "captures": {"intention": "ship it", "megan_nicety": "Walk outside"},
        }, headers=_auth(client))
        # token file for gather_live_state todoist read isn't present in the
        # tmp vault — shadow degrades that surface, vault rows still classify
        r = client.post("/commit?mode=shadow", headers=_auth(client),
                        json={"digest": {"assigned": []},
                              "sequence": {"sequence": []}, "config": {}})
        if r.status_code == 502:
            import pytest as _pytest
            _pytest.skip("shadow state unavailable in this env")
        entries = r.json()["entries"]
        actions = [e["manifest"]["action"] for e in entries]
        assert "capture-nicety" in actions
        assert "frontmatter-captures" in actions


class TestIgnoreListDigestFilter:
    """build_digest(ignore=…) — Todoist rows drop by todoist_id, vault rows by
    path, any row by case-insensitive name (T13e)."""

    def _items(self):
        vault = {"name": "Deep Work", "path": "50 - Operations/Tasks/Deep Work.md"}
        todoist = {"name": "M1.0", "path": None, "todoist_id": "6gQVxQXrgh4XQ48v"}
        return [vault, todoist]

    def test_todoist_id_ignored(self):
        ignore = {"todoist_ids": {"6gQVxQXrgh4XQ48v"}, "paths": set(), "names": set()}
        d = main_mod.build_digest([], self._items(), date(2026, 7, 21), [], ignore=ignore)
        assert [i["name"] for i in d["assigned"]] == ["Deep Work"]
        assert d["assigned_count"] == 1

    def test_path_ignored_from_pool_too(self):
        ignore = {
            "todoist_ids": set(),
            "paths": {"50 - Operations/Tasks/Deep Work.md"},
            "names": set(),
        }
        d = main_mod.build_digest(self._items(), [], date(2026, 7, 21), [], ignore=ignore)
        assert d["pool_count"] == 1
        assert all(i["name"] != "Deep Work" for i in d["suggested"])

    def test_none_ignore_is_noop(self):
        d = main_mod.build_digest([], self._items(), date(2026, 7, 21), [], ignore=None)
        assert d["assigned_count"] == 2


class TestRecurringPlacementImmunity:
    """T27: recurring todoist rows with a native time are placement-immune
    server-side — auto-pinned walls regardless of what the client sent."""

    RECURRING = {
        "id": "LOOTS", "name": "LOOTS", "source": "todoist",
        "todoist_id": "t-loots", "path": "todoist://t-loots",
        "blocks": 0.5, "is_recurring": True, "scheduled_start": "12:30",
    }

    def _post(self, client, vault, monkeypatch, *, pinned_rows=None, captured=None):
        captured = captured if captured is not None else {}
        today = gather.effective_date(datetime.now())
        rs.write_digest_index(vault, today, [{
            "name": "LOOTS", "todoist_id": "t-loots",
            "path": "todoist://t-loots", "surface": "assigned",
            "source": "todoist", "blocks": 0.5,
            "is_recurring": True, "scheduled_start": "12:30",
        }])
        def fake_propose(assigned, config, anchored_blocks, ctx=None):
            captured["assigned"] = assigned
            captured["anchored"] = anchored_blocks
            return {"sequence": [{"id": "B", "start": "10:00", "end": "10:30",
                                  "zone": "any"}], "overlap_grants": []}
        monkeypatch.setattr(main_mod.judgment, "propose_sequence", fake_propose)
        monkeypatch.setattr(main_mod.planning.sequence, "validate_sequence",
                            lambda *a, **k: type("R", (), {
                                "ok": True, "hard_errors": [], "warnings": []})())
        return client.post("/sequence", json={
            "assigned": [dict(self.RECURRING), {"id": "B", "name": "B"}],
            "config": {}, "anchored_blocks": [],
            "pinned_rows": pinned_rows or [],
        }, headers=_auth(client)), captured

    def test_recurring_row_excluded_from_movable_and_walled(self, client, vault, monkeypatch):
        r, captured = self._post(client, vault, monkeypatch)
        assert r.status_code == 200, r.text
        assert all((i.get("id") or i.get("name")) != "LOOTS"
                   for i in captured["assigned"])
        walls = [b for b in captured["anchored"] if b.get("pinned")]
        assert [(w["Start"], w["End"]) for w in walls] == [("12:30", "12:45")]

    def test_recurring_row_lands_in_sequence_and_response_pins(self, client, vault, monkeypatch):
        r, _ = self._post(client, vault, monkeypatch)
        rows = {row["id"]: row for row in r.json()["sequence"]}
        assert rows["LOOTS"]["start"] == "12:30"
        pins = r.json()["pinned_rows"]
        assert [p["id"] for p in pins] == ["LOOTS"]

    def test_client_pin_for_recurring_id_wins_no_duplicate(self, client, vault, monkeypatch):
        pin = {"id": "LOOTS", "start": "12:30", "end": "12:45", "zone": None}
        r, captured = self._post(client, vault, monkeypatch, pinned_rows=[pin])
        assert r.status_code == 200, r.text
        walls = [b for b in captured["anchored"] if b.get("pinned")]
        assert len(walls) == 1
        assert r.json()["pinned_rows"] == [pin]

    def test_conflicting_client_pin_rejected_before_judgment(self, client, vault, monkeypatch):
        # a client pin of ANOTHER row overlapping the recurring wall must
        # fail closed pre-charge, same as overlapping client pins do
        pin = {"id": "B", "start": "12:30", "end": "13:00", "zone": None}
        called = {}
        r, captured = self._post(client, vault, monkeypatch, pinned_rows=[pin],
                                 captured=called)
        assert r.status_code == 422
        assert "assigned" not in called  # judgment never invoked


class TestNativeTimedRouteEnforcement:
    """P4-02: /sequence and /validate-sequence rederive native protection."""

    NON_RECURRING = {
        "id": "ERRAND", "name": "ERRAND", "source": "todoist",
        "todoist_id": "t-errand", "path": "todoist://t-errand",
        "blocks": 0.5, "is_recurring": False, "scheduled_start": "14:00",
    }

    def _seed_index(self, vault, rows=None):
        today = gather.effective_date(datetime.now())
        rs.write_digest_index(vault, today, rows or [{
            "name": "ERRAND", "todoist_id": "t-errand",
            "path": "todoist://t-errand", "surface": "assigned",
            "source": "todoist", "blocks": 0.5,
            "is_recurring": False, "scheduled_start": "14:00",
        }])

    def _post_sequence(self, client, vault, monkeypatch, *, pinned_rows=None,
                       captured=None, submitted_rows=None,
                       server_rows=None):
        captured = captured if captured is not None else {}
        self._seed_index(vault, server_rows)

        def fake_propose(assigned, config, anchored_blocks, ctx=None):
            captured["assigned"] = assigned
            captured["anchored"] = anchored_blocks
            return {"sequence": [], "overlap_grants": []}

        monkeypatch.setattr(main_mod.judgment, "propose_sequence", fake_propose)
        monkeypatch.setattr(
            main_mod.planning.sequence,
            "validate_sequence",
            lambda *a, **k: type("R", (), {
                "ok": True, "hard_errors": [], "warnings": []
            })(),
        )
        payload = {
            "assigned": submitted_rows or [dict(self.NON_RECURRING)],
            "config": {},
            "anchored_blocks": [],
        }
        if pinned_rows is not None:
            payload["pinned_rows"] = pinned_rows
        return client.post("/sequence", json=payload, headers=_auth(client)), captured

    def test_omitted_pin_is_rederived_for_non_recurring_native_time(
        self, client, vault, monkeypatch
    ):
        # Contract: omitting pinned_rows cannot turn a native timed row loose.
        r, captured = self._post_sequence(client, vault, monkeypatch)
        assert r.status_code == 200, r.text
        assert all(
            (item.get("id") or item.get("name")) != "ERRAND"
            for item in captured["assigned"]
        )
        walls = [block for block in captured["anchored"] if block.get("pinned")]
        assert [(wall["Start"], wall["End"]) for wall in walls] == [("14:00", "14:15")]
        assert r.json()["sequence"] == [{
            "id": "ERRAND", "start": "14:00", "end": "14:15", "zone": None,
        }]

    def test_submitted_blocks_override_is_effective_but_native_provenance_stays_server_owned(
        self, client, vault, monkeypatch
    ):
        # Client timing/source/recurrence are replaced by the dated server
        # index, and native provenance (native_blocks) is server-owned — but
        # the submitted blocks=0 is a legitimate effective override.
        submitted = dict(self.NON_RECURRING)
        submitted.update({
            "source": "vault", "scheduled_start": "09:00",
            "is_recurring": True, "blocks": 0, "duration": 1,
            "allow_time_adjustment": True,
        })
        server = [{
            "name": "ERRAND", "todoist_id": "t-errand",
            "path": "todoist://t-errand", "surface": "assigned",
            "source": "todoist", "blocks": 0.5, "duration": 30,
            "is_recurring": False, "scheduled_start": "14:00",
        }]
        r, captured = self._post_sequence(
            client, vault, monkeypatch, submitted_rows=[submitted],
            server_rows=server,
        )
        assert r.status_code == 200, r.text
        row = next(item for item in captured["assigned"] if item["id"] == "ERRAND")
        assert row["source"] == "todoist"
        assert row["scheduled_start"] == "14:00"
        assert row["is_recurring"] is False
        assert row["blocks"] == 0            # effective override accepted
        assert row["native_blocks"] == 0.5   # provenance never from the client
        assert row["duration"] == 30
        assert row["allow_time_adjustment"] is True

    def test_client_blocks_zero_cannot_disable_native_protection(
        self, client, vault, monkeypatch
    ):
        submitted = dict(self.NON_RECURRING)
        submitted.update({"blocks": 0, "scheduled_start": "09:00", "source": "vault"})
        r, captured = self._post_sequence(
            client, vault, monkeypatch, submitted_rows=[submitted]
        )
        assert r.status_code == 200, r.text
        assert all(item.get("id") != "ERRAND" for item in captured["assigned"])
        walls = [block for block in captured["anchored"] if block.get("pinned")]
        assert [(wall["Start"], wall["End"]) for wall in walls] == [("14:00", "14:15")]

    @pytest.mark.parametrize("allow", [False, "true", 1])
    def test_only_exact_boolean_opt_in_survives(
        self, client, vault, monkeypatch, allow
    ):
        submitted = dict(self.NON_RECURRING)
        submitted["allow_time_adjustment"] = allow
        r, captured = self._post_sequence(
            client, vault, monkeypatch, submitted_rows=[submitted]
        )
        assert r.status_code == 200, r.text
        assert all(item.get("id") != "ERRAND" for item in captured["assigned"])
        walls = [block for block in captured["anchored"] if block.get("pinned")]
        assert [(wall["Start"], wall["End"]) for wall in walls] == [("14:00", "14:15")]

    def test_opt_in_on_matching_vault_row_does_not_survive(
        self, client, vault, monkeypatch
    ):
        vault_row = {
            "id": "LOCAL", "name": "LOCAL", "source": "vault",
            "path": "Projects/Local.md", "blocks": 1,
        }
        server = [{
            "name": "ERRAND", "todoist_id": "t-errand",
            "path": "todoist://t-errand", "surface": "assigned",
            "source": "todoist", "blocks": 0.5,
            "is_recurring": False, "scheduled_start": "14:00",
        }, {
            "name": "LOCAL", "todoist_id": "", "path": "Projects/Local.md",
            "surface": "assigned", "source": "vault", "blocks": 1,
        }]
        submitted = [dict(self.NON_RECURRING, allow_time_adjustment=True),
                     dict(vault_row, allow_time_adjustment=True)]
        r, captured = self._post_sequence(
            client, vault, monkeypatch, submitted_rows=submitted,
            server_rows=server,
        )
        assert r.status_code == 200, r.text
        local = next(item for item in captured["assigned"] if item["id"] == "LOCAL")
        assert "allow_time_adjustment" not in local

    def test_native_claim_without_today_index_fails_closed(
        self, client, monkeypatch
    ):
        called = {}
        monkeypatch.setattr(
            main_mod.judgment, "propose_sequence",
            lambda *args, **kwargs: called.setdefault("called", True),
        )
        r = client.post("/sequence", json={
            "assigned": [dict(self.NON_RECURRING)],
            "config": {}, "anchored_blocks": [],
        }, headers=_auth(client))
        assert r.status_code == 422
        assert "ERRAND" in str(r.json()["detail"])
        assert called == {}

    def test_suggested_identity_cannot_be_submitted_as_assigned(
        self, client, vault, monkeypatch
    ):
        suggested = dict(self.NON_RECURRING)
        server = [{
            "name": "ERRAND", "todoist_id": "t-errand",
            "path": "todoist://t-errand", "surface": "suggested",
            "source": "todoist", "blocks": 0.5,
            "is_recurring": False, "scheduled_start": "14:00",
        }]
        called = {}
        monkeypatch.setattr(
            main_mod.judgment, "propose_sequence",
            lambda *args, **kwargs: called.setdefault("called", True),
        )
        r, _ = self._post_sequence(
            client, vault, monkeypatch, submitted_rows=[suggested],
            server_rows=server,
        )
        assert r.status_code == 422
        assert "ERRAND" in str(r.json()["detail"])
        assert called == {}

    def test_mismatched_identity_cannot_authorize_by_name(
        self, client, vault, monkeypatch
    ):
        server = [{
            "name": "ERRAND", "todoist_id": "t-other",
            "path": "todoist://t-other", "surface": "assigned",
            "source": "todoist", "blocks": 0.5,
            "is_recurring": False, "scheduled_start": "14:00",
        }]
        called = {}
        monkeypatch.setattr(
            main_mod.judgment, "propose_sequence",
            lambda *args, **kwargs: called.setdefault("called", True),
        )
        r, _ = self._post_sequence(
            client, vault, monkeypatch,
            submitted_rows=[dict(self.NON_RECURRING, allow_time_adjustment=True)],
            server_rows=server,
        )
        assert r.status_code == 422
        assert "ERRAND" in str(r.json()["detail"])
        assert called == {}

    def test_identityless_native_metadata_is_not_a_legacy_row(
        self, client, monkeypatch
    ):
        called = {}
        monkeypatch.setattr(
            main_mod.judgment, "propose_sequence",
            lambda *args, **kwargs: called.setdefault("called", True),
        )
        r = client.post("/sequence", json={
            "assigned": [{"name": "Legacy", "blocks": 0}],
            "config": {}, "anchored_blocks": [],
        }, headers=_auth(client))
        assert r.status_code == 422
        assert "Legacy" in str(r.json()["detail"])
        assert called == {}

    def test_matching_pin_is_canonicalized_at_route_boundary(self, client, vault, monkeypatch):
        # Contract: a matching client pin is accepted only as server canonical.
        matching = {
            "id": "ERRAND", "start": "14:00", "end": "14:15",
            "zone": "client-zone", "metadata": {"stale": True},
        }
        r, captured = self._post_sequence(
            client, vault, monkeypatch, pinned_rows=[matching]
        )
        assert r.status_code == 200, r.text
        walls = [block for block in captured["anchored"] if block.get("pinned")]
        assert [(wall["Start"], wall["End"]) for wall in walls] == [("14:00", "14:15")]
        assert r.json()["pinned_rows"] == [{
            "id": "ERRAND", "start": "14:00", "end": "14:15", "zone": None,
        }]

    def test_conflicting_native_pin_fails_before_billed_boundary(self, client, vault, monkeypatch):
        # Contract: an altered native pin is rejected before judgment/billing.
        captured = {}
        conflicting = {
            "id": "ERRAND", "start": "13:00", "end": "13:15", "zone": None,
        }
        r, captured = self._post_sequence(
            client, vault, monkeypatch, pinned_rows=[conflicting],
            captured=captured,
        )
        assert r.status_code == 422
        assert "assigned" not in captured

    @pytest.mark.parametrize(
        ("pinned_rows", "message"),
        [
            (
                [
                    {"id": "ERRAND", "start": "14:00", "end": "14:15"},
                    {"id": "ERRAND", "start": "14:00", "end": "14:15"},
                ],
                "duplicate",
            ),
            ([{"id": "foreign", "start": "10:00", "end": "10:30"}], "foreign"),
            ([{"id": "ERRAND", "start": "bad", "end": "10:30"}], "malformed"),
        ],
    )
    def test_duplicate_foreign_and_malformed_pins_remain_rejected(
        self, client, vault, monkeypatch, pinned_rows, message
    ):
        # Contract: generic pin validation remains a hard preflight boundary.
        r, captured = self._post_sequence(
            client, vault, monkeypatch, pinned_rows=pinned_rows,
        )
        assert r.status_code == 422
        assert message in str(r.json()["detail"])
        assert "assigned" not in captured

    def test_validate_sequence_recomputes_protection_without_pinned_rows(
        self, client, vault
    ):
        # Contract: /validate-sequence must reject a moved native row even when
        # client pinned_rows and snapshot metadata are both omitted.
        self._seed_index(vault)
        r = client.post("/validate-sequence", json={
            "sequence": [{
                "id": "ERRAND", "start": "15:00", "end": "15:15", "zone": "any",
            }],
            "assigned": [{
                **self.NON_RECURRING,
                "source": "vault", "scheduled_start": "09:00",
                "is_recurring": True, "blocks": 0,
            }],
            "anchored_blocks": [],
            "config": {},
        }, headers=_auth(client))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is False
        assert any(
            "protected native" in str(error)
            or "moved" in str(error)
            or "pinned rows changed" in str(error)
            for error in body["hard_errors"]
        )


class TestCommitNativeProtection:
    def _seed_recurring_todoist(self, vault, today):
        rs.write_digest_index(vault, today, [{
            "name": "Native", "todoist_id": "t-native",
            "path": "todoist://t-native", "surface": "assigned",
            "source": "todoist", "duration": 30, "blocks": 1,
            "is_recurring": True, "scheduled_start": "12:30",
        }])

    def _capture_shadow_manifest(self, monkeypatch):
        manifest_calls = []
        monkeypatch.setattr(
            main_mod.shadow,
            "build_plan_manifest",
            lambda *args, **kwargs: manifest_calls.append((args, kwargs)) or [],
        )
        monkeypatch.setattr(main_mod.shadow, "gather_live_state", lambda *a, **k: {})
        monkeypatch.setattr(
            main_mod.shadow,
            "diff_against_live",
            lambda *a, **k: main_mod.shadow.ShadowDiff(entries=[]),
        )
        return manifest_calls

    def _commit_body(self, *, allow=None, path="todoist://t-native", blocks=3):
        row = {
            "name": "Native", "todoist_id": "t-native", "path": path,
            "blocks": blocks, "source": "vault", "types": ["adventure"],
        }
        if allow is not None:
            row["allow_time_adjustment"] = allow
        return {
            "digest": {"assigned": [row], "suggested": []},
            "sequence": {"sequence": [{
                "id": "Native", "start": "12:30", "end": "13:00", "zone": "any",
            }]},
            "config": {},
        }

    def _seed_included_and_omitted_native_rows(self, vault, today):
        rs.write_digest_index(vault, today, [{
            "name": "Included Native", "todoist_id": "t-included",
            "path": "todoist://t-included", "surface": "assigned",
            "source": "todoist", "duration": 30, "blocks": 0,
            "native_blocks": 1, "is_recurring": False,
            "scheduled_start": "12:30",
        }, {
            "name": "Omitted Native", "todoist_id": "t-omitted",
            "path": "todoist://t-omitted", "surface": "assigned",
            "source": "todoist", "duration": 30, "blocks": 1,
            "native_blocks": 1, "is_recurring": False,
            "scheduled_start": "15:00",
        }])

    def _included_native_body(self, *, start="12:30", end="13:00"):
        return {
            "digest": {"assigned": [{
                "name": "Included Native", "todoist_id": "t-included",
                "path": "todoist://t-included", "source": "vault",
                "scheduled_start": "09:00", "blocks": 0,
                "native_blocks": 0,
            }], "suggested": []},
            "sequence": {"sequence": [{
                "id": "Included Native", "start": start, "end": end,
                "zone": "any",
            }]},
            "config": {},
        }

    def test_shadow_commit_rejects_moved_native_row_without_pin_metadata(
        self, client, vault, monkeypatch
    ):
        # Contract: /commit recomputes native protection unconditionally before
        # manifest construction; omitted client metadata cannot bypass it.
        today = gather.effective_date(datetime.now())
        rs.write_digest_index(vault, today, [{
            "name": "Native", "todoist_id": "t-native",
            "path": "todoist://t-native", "surface": "assigned",
            "source": "todoist", "blocks": 1,
            "is_recurring": False, "scheduled_start": "12:30",
        }])
        manifest_calls = []
        monkeypatch.setattr(
            main_mod.shadow,
            "build_plan_manifest",
            lambda *args, **kwargs: manifest_calls.append((args, kwargs)) or [],
        )
        monkeypatch.setattr(main_mod.shadow, "gather_live_state", lambda *a, **k: {})
        monkeypatch.setattr(
            main_mod.shadow,
            "diff_against_live",
            lambda *a, **k: main_mod.shadow.ShadowDiff(entries=[]),
        )
        r = client.post("/commit?mode=shadow", headers=_auth(client), json={
            "digest": {
                "assigned": [{
                    "name": "Native", "todoist_id": "t-native",
                    "path": "todoist://t-native",
                }],
                "suggested": [],
            },
            "sequence": {"sequence": [{
                "id": "Native", "start": "13:00", "end": "13:30", "zone": "any",
            }]},
            "config": {},
        })
        assert r.status_code == 422
        assert "Native" in str(r.json()["detail"])
        assert manifest_calls == []

    def test_exact_opt_in_reaches_sanitized_recurring_digest(
        self, client, vault, monkeypatch
    ):
        today = gather.effective_date(datetime.now())
        self._seed_recurring_todoist(vault, today)
        manifest_calls = self._capture_shadow_manifest(monkeypatch)

        body = self._commit_body(allow=True)
        body["sequence"]["sequence"][0].update({
            "start": "13:00", "end": "13:30",
        })
        r = client.post(
            "/commit?mode=shadow", headers=_auth(client), json=body,
        )

        assert r.status_code == 200, r.text
        assert len(manifest_calls) == 1
        safe_row = manifest_calls[0][0][0]["assigned"][0]
        assert safe_row["allow_time_adjustment"] is True
        assert safe_row["source"] == "todoist"
        assert safe_row["todoist_id"] == "t-native"
        assert safe_row["path"] == "todoist://t-native"
        assert safe_row["blocks"] == 3        # client overlay is the effective duration
        assert safe_row["native_blocks"] == 1  # server blocks retained as provenance
        assert safe_row["duration"] == 30
        assert safe_row["scheduled_start"] == "12:30"
        assert safe_row["is_recurring"] is True
        assert "types" not in safe_row

    @pytest.mark.parametrize("allow", [False, "true"])
    def test_non_boolean_opt_in_is_not_retained(
        self, client, vault, monkeypatch, allow
    ):
        today = gather.effective_date(datetime.now())
        self._seed_recurring_todoist(vault, today)
        manifest_calls = self._capture_shadow_manifest(monkeypatch)

        r = client.post(
            "/commit?mode=shadow", headers=_auth(client),
            json=self._commit_body(allow=allow),
        )

        assert r.status_code == 200, r.text
        safe_row = manifest_calls[0][0][0]["assigned"][0]
        assert "allow_time_adjustment" not in safe_row

    def test_nonmatching_opt_in_is_rejected_before_manifest(
        self, client, vault, monkeypatch
    ):
        today = gather.effective_date(datetime.now())
        self._seed_recurring_todoist(vault, today)
        manifest_calls = self._capture_shadow_manifest(monkeypatch)

        r = client.post(
            "/commit?mode=shadow", headers=_auth(client),
            json=self._commit_body(allow=True, path="todoist://other"),
        )

        assert r.status_code == 422
        assert "Native" in str(r.json()["detail"])
        assert manifest_calls == []

    def test_omitted_native_row_is_not_required_in_commit_sequence(
        self, client, vault, monkeypatch
    ):
        today = gather.effective_date(datetime.now())
        self._seed_included_and_omitted_native_rows(vault, today)
        manifest_calls = self._capture_shadow_manifest(monkeypatch)

        r = client.post(
            "/commit?mode=shadow", headers=_auth(client),
            json=self._included_native_body(),
        )

        assert r.status_code == 200, r.text
        safe_rows = manifest_calls[0][0][0]["assigned"]
        assert [row["name"] for row in safe_rows] == ["Included Native"]
        safe_row = safe_rows[0]
        assert safe_row["native_blocks"] == 1
        assert safe_row["blocks"] == 0
        assert safe_row["duration"] == 30

    def test_altered_included_native_row_still_rejects_with_omitted_row(
        self, client, vault, monkeypatch
    ):
        today = gather.effective_date(datetime.now())
        self._seed_included_and_omitted_native_rows(vault, today)
        manifest_calls = self._capture_shadow_manifest(monkeypatch)

        r = client.post(
            "/commit?mode=shadow", headers=_auth(client),
            json=self._included_native_body(start="13:00", end="13:30"),
        )

        assert r.status_code == 422
        detail = str(r.json()["detail"])
        assert "Included Native" in detail
        assert "Omitted Native" not in detail
        assert manifest_calls == []


class TestEffectiveBlocksOverlay:
    """Duration-override boundary (P4-xx): a matched assigned row may carry a
    validated today-only effective-blocks override — including zero — for
    planning and commit-manifest duration, while identity/source/timing/
    recurrence and Todoist native provenance stay server-owned. Malformed or
    extreme overlays fail closed at the route boundary."""

    VAULT = {"id": "LOCAL", "name": "LOCAL", "source": "vault",
             "path": "Projects/Local.md", "blocks": 1}
    TODO = {"id": "ERRAND", "name": "ERRAND", "source": "todoist",
            "todoist_id": "t-errand", "path": "todoist://t-errand",
            "blocks": 0.5, "is_recurring": False, "scheduled_start": "14:00"}

    def _seed(self, vault, rows):
        today = gather.effective_date(datetime.now())
        seeded = [
            {**dict(row), "surface": "assigned"}
            for row in rows
        ]
        rs.write_digest_index(vault, today, seeded)

    def _post_sequence(self, client, vault, monkeypatch, *, submitted_rows,
                       server_rows, captured=None, called=None):
        self._seed(vault, server_rows)
        captured = captured if captured is not None else {}
        state = {"called": False}

        def fake_propose(assigned, config, anchored_blocks, ctx=None):
            state["called"] = True
            captured["assigned"] = assigned
            captured["anchored"] = anchored_blocks
            return {"sequence": [], "overlap_grants": []}

        monkeypatch.setattr(main_mod.judgment, "propose_sequence", fake_propose)
        monkeypatch.setattr(
            main_mod.planning.sequence, "validate_sequence",
            lambda *a, **k: type("R", (), {
                "ok": True, "hard_errors": [], "warnings": []})(),
        )
        r = client.post("/sequence", json={
            "assigned": submitted_rows, "config": {}, "anchored_blocks": [],
        }, headers=_auth(client))
        return r, captured, state

    def test_vault_blocks_override_is_effective_for_planning(
        self, client, vault, monkeypatch
    ):
        # Red repro: a 3-block today-only override must reach the planning
        # duration (90 min), not be replaced by the server's 1 block.
        r, captured, _ = self._post_sequence(
            client, vault, monkeypatch,
            submitted_rows=[{**self.VAULT, "blocks": 3}],
            server_rows=[self.VAULT],
        )
        assert r.status_code == 200, r.text
        row = next(item for item in captured["assigned"] if item["id"] == "LOCAL")
        assert row["blocks"] == 3
        assert row["source"] == "vault"
        assert row["path"] == "Projects/Local.md"

    def test_todoist_blocks_override_cannot_unpin_native_row(
        self, client, vault, monkeypatch
    ):
        # blocks=0 is a legitimate effective override, but native protection
        # is server-derived (native_blocks) and must still pin the row.
        r, captured, _ = self._post_sequence(
            client, vault, monkeypatch,
            submitted_rows=[{**self.TODO, "blocks": 0}],
            server_rows=[self.TODO],
        )
        assert r.status_code == 200, r.text
        assert all(item.get("id") != "ERRAND" for item in captured["assigned"])
        walls = [block for block in captured["anchored"] if block.get("pinned")]
        assert [(wall["Start"], wall["End"]) for wall in walls] == [("14:00", "14:15")]

    def test_todoist_override_with_opt_in_is_movable_and_retains_provenance(
        self, client, vault, monkeypatch
    ):
        # With the exact opt-in the row is movable; the effective override
        # rides ``blocks`` while ``native_blocks`` keeps the server value.
        r, captured, _ = self._post_sequence(
            client, vault, monkeypatch,
            submitted_rows=[{**self.TODO, "blocks": 3,
                             "allow_time_adjustment": True,
                             "native_blocks": 0}],
            server_rows=[self.TODO],
        )
        assert r.status_code == 200, r.text
        row = next(item for item in captured["assigned"] if item["id"] == "ERRAND")
        assert row["blocks"] == 3          # overlay accepted
        assert row["native_blocks"] == 0.5  # provenance never from the client
        assert row["allow_time_adjustment"] is True

    def test_zero_blocks_override_is_legitimate_for_vault_row(
        self, client, vault, monkeypatch
    ):
        r, captured, _ = self._post_sequence(
            client, vault, monkeypatch,
            submitted_rows=[{**self.VAULT, "blocks": 0}],
            server_rows=[self.VAULT],
        )
        assert r.status_code == 200, r.text
        row = next(item for item in captured["assigned"] if item["id"] == "LOCAL")
        assert row["blocks"] == 0

    def test_absent_blocks_falls_back_to_server_blocks(
        self, client, vault, monkeypatch
    ):
        submitted = {key: value for key, value in self.VAULT.items()
                     if key != "blocks"}
        r, captured, _ = self._post_sequence(
            client, vault, monkeypatch,
            submitted_rows=[submitted],
            server_rows=[self.VAULT],
        )
        assert r.status_code == 200, r.text
        row = next(item for item in captured["assigned"] if item["id"] == "LOCAL")
        assert row["blocks"] == 1

    @pytest.mark.parametrize("bad", [True, "3", -1, 999, 10**400])
    def test_malformed_or_extreme_blocks_overlay_fails_closed(
        self, client, vault, monkeypatch, bad
    ):
        # Bounded 30-minute-block policy: bools, non-numbers, non-finite
        # (10**400 overflows to non-finite), negatives, and >48 blocks are
        # never silently accepted.
        r, captured, state = self._post_sequence(
            client, vault, monkeypatch,
            submitted_rows=[{**self.VAULT, "blocks": bad}],
            server_rows=[self.VAULT],
        )
        assert r.status_code == 422
        assert "LOCAL" in str(r.json()["detail"])
        assert "blocks" in str(r.json()["detail"])
        assert state["called"] is False

    def test_validate_sequence_accepts_valid_blocks_overlay(
        self, client, vault
    ):
        self._seed(vault, [self.VAULT])
        r = client.post("/validate-sequence", json={
            "sequence": [{"id": "LOCAL", "start": "10:00", "end": "11:30",
                          "zone": "any"}],
            "assigned": [{**self.VAULT, "blocks": 3}],
            "anchored_blocks": [], "config": {},
        }, headers=_auth(client))
        assert r.status_code == 200, r.text
        assert r.json()["ok"] is True

    def test_validate_sequence_rejects_malformed_blocks_overlay(
        self, client, vault
    ):
        self._seed(vault, [self.VAULT])
        r = client.post("/validate-sequence", json={
            "sequence": [{"id": "LOCAL", "start": "10:00", "end": "10:30",
                          "zone": "any"}],
            "assigned": [{**self.VAULT, "blocks": -3}],
            "anchored_blocks": [], "config": {},
        }, headers=_auth(client))
        assert r.status_code == 422

    def _capture_shadow_manifest(self, monkeypatch):
        manifest_calls = []
        monkeypatch.setattr(
            main_mod.shadow, "build_plan_manifest",
            lambda *args, **kwargs: manifest_calls.append((args, kwargs)) or [],
        )
        monkeypatch.setattr(main_mod.shadow, "gather_live_state", lambda *a, **k: {})
        monkeypatch.setattr(
            main_mod.shadow, "diff_against_live",
            lambda *a, **k: main_mod.shadow.ShadowDiff(entries=[]),
        )
        return manifest_calls

    def test_shadow_commit_carries_effective_blocks_overlay(
        self, client, vault, monkeypatch
    ):
        self._seed(vault, [self.VAULT])
        manifest_calls = self._capture_shadow_manifest(monkeypatch)
        r = client.post("/commit?mode=shadow", headers=_auth(client), json={
            "digest": {"assigned": [{**self.VAULT, "blocks": 3}],
                       "suggested": []},
            "sequence": {"sequence": [{
                "id": "LOCAL", "start": "10:00", "end": "10:30", "zone": "any",
            }]},
            "config": {},
        })
        assert r.status_code == 200, r.text
        safe_row = manifest_calls[0][0][0]["assigned"][0]
        assert safe_row["blocks"] == 3
        assert safe_row["path"] == "Projects/Local.md"
        assert safe_row["source"] == "vault"

    def test_shadow_commit_todoist_zero_override_keeps_native_blocks(
        self, client, vault, monkeypatch
    ):
        self._seed(vault, [self.TODO])
        manifest_calls = self._capture_shadow_manifest(monkeypatch)
        r = client.post("/commit?mode=shadow", headers=_auth(client), json={
            "digest": {"assigned": [{
                **self.TODO, "blocks": 0, "native_blocks": 0,
            }], "suggested": []},
            "sequence": {"sequence": [{
                "id": "ERRAND", "start": "14:00", "end": "14:15", "zone": "any",
            }]},
            "config": {},
        })
        assert r.status_code == 200, r.text
        safe_row = manifest_calls[0][0][0]["assigned"][0]
        assert safe_row["blocks"] == 0
        assert safe_row["native_blocks"] == 0.5  # server provenance retained

    def test_shadow_commit_todoist_zero_override_cannot_unpin(
        self, client, vault, monkeypatch
    ):
        self._seed(vault, [self.TODO])
        manifest_calls = self._capture_shadow_manifest(monkeypatch)
        r = client.post("/commit?mode=shadow", headers=_auth(client), json={
            "digest": {"assigned": [{
                **self.TODO, "blocks": 0, "native_blocks": 0,
            }], "suggested": []},
            "sequence": {"sequence": [{
                "id": "ERRAND", "start": "15:00", "end": "15:15", "zone": "any",
            }]},
            "config": {},
        })
        assert r.status_code == 422
        assert "ERRAND" in str(r.json()["detail"])
        assert manifest_calls == []

    def test_shadow_commit_rejects_malformed_blocks_overlay(
        self, client, vault, monkeypatch
    ):
        self._seed(vault, [self.VAULT])
        manifest_calls = self._capture_shadow_manifest(monkeypatch)
        r = client.post("/commit?mode=shadow", headers=_auth(client), json={
            "digest": {"assigned": [{**self.VAULT, "blocks": -1}],
                       "suggested": []},
            "sequence": {"sequence": [{
                "id": "LOCAL", "start": "10:00", "end": "10:30", "zone": "any",
            }]},
            "config": {},
        })
        assert r.status_code == 422
        assert "blocks" in str(r.json()["detail"])
        assert manifest_calls == []


class TestCalendarDismissalSequenceSide:
    """T28: a dismissed calendar row must vanish from judgment walls and the
    time-frame busy_events on /sequence (freed interval available), while the
    row itself stays source-immutable."""

    CAL_ROW = {"Block": "Farmers Market", "Start": "09:00", "End": "11:00",
               "Duration": 120, "source": "calendar"}

    def test_dismissed_calendar_row_dropped_from_walls_and_frame(
            self, client, vault, monkeypatch):
        r = client.post("/day-setup", json={
            "anchored": [{"id": "Farmers Market", "on": True,
                          "skip_today": True, "time": None}],
        }, headers=_auth(client))
        assert r.status_code == 200, r.text
        captured = {}
        def fake_propose(assigned, config, anchored_blocks, ctx=None):
            captured["anchored"] = anchored_blocks
            captured["time"] = config.get("time")
            return {"sequence": [], "overlap_grants": []}
        monkeypatch.setattr(main_mod.judgment, "propose_sequence", fake_propose)
        monkeypatch.setattr(main_mod.planning.sequence, "validate_sequence",
                            lambda *a, **k: type("R", (), {
                                "ok": True, "hard_errors": [], "warnings": []})())
        r = client.post("/sequence", json={
            "assigned": [{"id": "A", "name": "A"}],
            "config": {}, "anchored_blocks": [dict(self.CAL_ROW)],
            "pinned_rows": [],
        }, headers=_auth(client))
        assert r.status_code == 200, r.text
        assert all(b.get("Block") != "Farmers Market"
                   for b in captured["anchored"])

    def test_attending_calendar_row_still_walls(self, client, monkeypatch):
        captured = {}
        def fake_propose(assigned, config, anchored_blocks, ctx=None):
            captured["anchored"] = anchored_blocks
            return {"sequence": [], "overlap_grants": []}
        monkeypatch.setattr(main_mod.judgment, "propose_sequence", fake_propose)
        monkeypatch.setattr(main_mod.planning.sequence, "validate_sequence",
                            lambda *a, **k: type("R", (), {
                                "ok": True, "hard_errors": [], "warnings": []})())
        r = client.post("/sequence", json={
            "assigned": [{"id": "A", "name": "A"}],
            "config": {}, "anchored_blocks": [dict(self.CAL_ROW)],
            "pinned_rows": [],
        }, headers=_auth(client))
        assert r.status_code == 200, r.text
        assert any(b.get("Block") == "Farmers Market"
                   for b in captured["anchored"])


# ---------------------------------------------------------------------------
# FT-01: duration-memory routes — token guard, strict validation, save, reset
# ---------------------------------------------------------------------------

import duration_memory as dm  # noqa: E402

_DM_CONFIG = """\
---
description: test config
last_updated: 2026-07-01
---

# TDTB Bridger Config

## Defaults

| Key | Value    |
| --- | -------- |
| eod | 11:45 PM |

## Presets

| Name | Type | Blocks | Priority |
|------|------|--------|----------|
| Make | interval | 2 | 2 |
"""

DM_IDENTITY = "50 - Operations/Projects/Make.md"


def _seed_dm_vault(vault: Path) -> None:
    """Vault with one assigned preset-named note (Make, 2 blocks) + config."""
    cfg = vault / "00 - META/Skill-Configs/tdtb-bridger.md"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(_DM_CONFIG, encoding="utf-8")
    proj = vault / "50 - Operations" / "Projects"
    proj.mkdir(parents=True, exist_ok=True)
    (proj / "Make.md").write_text(
        "---\ntype: project\nassigned: true\n---\nbody\n", encoding="utf-8"
    )


class TestDurationMemorySave:
    def test_save_requires_token(self, client):
        assert client.post("/duration-memory/save", json={
            "identity": DM_IDENTITY, "minutes": 90,
        }).status_code == 403

    def test_save_wrong_token_403(self, client):
        r = client.post("/duration-memory/save", json={
            "identity": DM_IDENTITY, "minutes": 90,
        }, headers={"X-TDTB-Token": "nope"})
        assert r.status_code == 403

    def test_save_valid_persists_vault_scoped(self, client, vault):
        r = client.post("/duration-memory/save", json={
            "identity": DM_IDENTITY, "minutes": 90,
        }, headers=_auth(client))
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert body["identity"] == DM_IDENTITY
        assert body["minutes"] == 90
        assert body["duration_source"] == "remembered"
        cache = dm.cache_path(vault)
        assert cache.is_file()
        assert cache.is_relative_to(vault)

    @pytest.mark.parametrize("minutes", [7, 23, 8])
    def test_save_rejects_off_grid(self, client, vault, minutes):
        r = client.post("/duration-memory/save", json={
            "identity": DM_IDENTITY, "minutes": minutes,
        }, headers=_auth(client))
        assert r.status_code == 422
        assert not dm.cache_path(vault).exists()

    @pytest.mark.parametrize("minutes", [30.5, "30", True, None])
    def test_save_rejects_fraction_string_bool(self, client, vault, minutes):
        r = client.post("/duration-memory/save", json={
            "identity": DM_IDENTITY, "minutes": minutes,
        }, headers=_auth(client))
        assert r.status_code == 422
        assert not dm.cache_path(vault).exists()

    def test_save_rejects_negative(self, client, vault):
        r = client.post("/duration-memory/save", json={
            "identity": DM_IDENTITY, "minutes": -5,
        }, headers=_auth(client))
        assert r.status_code == 422
        assert not dm.cache_path(vault).exists()

    def test_save_rejects_name_only_identity(self, client, vault):
        r = client.post("/duration-memory/save", json={
            "identity": "Some Display Name", "minutes": 90,
        }, headers=_auth(client))
        assert r.status_code == 422
        assert not dm.cache_path(vault).exists()

    def test_save_zero_is_valid(self, client, vault):
        r = client.post("/duration-memory/save", json={
            "identity": DM_IDENTITY, "minutes": 0,
        }, headers=_auth(client))
        assert r.status_code == 200, r.text
        assert r.json()["minutes"] == 0

    def test_invalid_save_preserves_existing_cache(self, client, vault):
        ok = client.post("/duration-memory/save", json={
            "identity": DM_IDENTITY, "minutes": 90,
        }, headers=_auth(client))
        assert ok.status_code == 200
        before = dm.cache_path(vault).read_bytes()
        for minutes in (7, 30.5, -5):
            r = client.post("/duration-memory/save", json={
                "identity": DM_IDENTITY, "minutes": minutes,
            }, headers=_auth(client))
            assert r.status_code == 422
        assert dm.cache_path(vault).read_bytes() == before


class TestDurationMemoryReset:
    def test_reset_requires_token(self, client):
        assert client.post("/duration-memory/reset", json={
            "identity": DM_IDENTITY,
        }).status_code == 403

    def test_reset_removes_and_returns_source_fallback(self, client, vault):
        _seed_dm_vault(vault)
        save = client.post("/duration-memory/save", json={
            "identity": DM_IDENTITY, "minutes": 90,
        }, headers=_auth(client))
        assert save.status_code == 200

        r = client.post("/duration-memory/reset", json={
            "identity": DM_IDENTITY,
        }, headers=_auth(client))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is True
        assert body["removed"] is True
        assert body["identity"] == DM_IDENTITY
        # Source fallback: Make preset = 2 blocks = 60 minutes.
        assert body["duration_minutes"] == 60
        assert body["duration_source"] == "preset"
        assert body["found"] is True
        assert dm.read_vault_memory(vault) == {}

    def test_reset_no_source_fallback_returns_bounded_error(self, client, vault):
        # FT-06 F2-R1: resolve-first — an identity absent from today's plan has
        # NO source fallback, so reset must return a bounded error (not a
        # successful found:false response) and leave durable memory unchanged.
        r = client.post("/duration-memory/reset", json={
            "identity": "todoist:never-saved",
        }, headers=_auth(client))
        assert r.status_code == 409, r.text
        detail = r.json()["detail"]
        assert isinstance(detail, dict)
        assert detail["code"] == "duration_memory_no_fallback"
        assert "message" in detail
        assert not dm.cache_path(vault).exists()

    def test_reset_no_source_fallback_preserves_durable_bytes(self, client, vault):
        # FT-06 F2-R1: when reset is rejected for a missing fallback, the
        # remembered value stays byte-for-byte identical on disk.
        save = client.post("/duration-memory/save", json={
            "identity": "todoist:ghost", "minutes": 90,
        }, headers=_auth(client))
        assert save.status_code == 200
        cache = dm.cache_path(vault)
        before = cache.read_bytes()

        r = client.post("/duration-memory/reset", json={
            "identity": "todoist:ghost",
        }, headers=_auth(client))
        assert r.status_code == 409, r.text
        assert cache.read_bytes() == before
        assert dm.read_vault_memory(vault) == {"todoist:ghost": 90}

    def test_reset_fallback_resolution_failure_preserves_durable_bytes(
        self, client, vault, monkeypatch
    ):
        # FT-06 F2-R1: when fallback resolution fails (gather error), reset
        # returns a bounded error and the remembered value stays unchanged.
        _seed_dm_vault(vault)
        save = client.post("/duration-memory/save", json={
            "identity": DM_IDENTITY, "minutes": 90,
        }, headers=_auth(client))
        assert save.status_code == 200
        cache = dm.cache_path(vault)
        before = cache.read_bytes()

        def boom(pool_notes, assigned_notes, today):
            raise RuntimeError("gather exploded mid-resolution")

        monkeypatch.setattr(gather, "build_run_data", boom)
        r = client.post("/duration-memory/reset", json={
            "identity": DM_IDENTITY,
        }, headers=_auth(client))
        assert r.status_code == 500, r.text
        detail = r.json()["detail"]
        assert isinstance(detail, dict)
        assert detail["code"] == "duration_memory_fallback_error"
        assert str(vault) not in r.text
        assert cache.read_bytes() == before
        assert dm.read_vault_memory(vault) == {DM_IDENTITY: 90}

    def test_reset_unremembered_with_valid_fallback_reports_removed_false(
        self, client, vault
    ):
        # No remembered value but a valid source fallback: reset is a no-op
        # deletion and returns the source-resolved fallback.
        _seed_dm_vault(vault)
        r = client.post("/duration-memory/reset", json={
            "identity": DM_IDENTITY,
        }, headers=_auth(client))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is True
        assert body["removed"] is False
        assert body["duration_minutes"] == 60
        assert body["duration_source"] == "preset"
        assert body["found"] is True
        assert not dm.cache_path(vault).exists()

    def test_reset_rejects_name_only_identity(self, client, vault):
        r = client.post("/duration-memory/reset", json={
            "identity": "Some Display Name",
        }, headers=_auth(client))
        assert r.status_code == 422

    def test_reset_fallback_source_resolves_for_vault_row(self, client, vault):
        _seed_dm_vault(vault)
        client.post("/duration-memory/save", json={
            "identity": DM_IDENTITY, "minutes": 90,
        }, headers=_auth(client))
        client.post("/duration-memory/reset", json={
            "identity": DM_IDENTITY,
        }, headers=_auth(client))
        body = client.get("/plan-inputs").json()
        rows = {i["name"]: i for i in body["digest"]["assigned"]}
        assert rows["Make"]["blocks"] == 2
        assert "duration_source" not in rows["Make"]


class TestDurationMemoryErrorRedaction:
    """FT-05 F3: client-visible cache error details must never expose absolute
    vault/cache paths or raw internal diagnostics. Errors keep bounded,
    safe message codes; the real cause stays in the server-side exception
    chain only."""

    def test_save_cache_error_detail_has_no_absolute_path(self, client, vault):
        p = dm.cache_path(vault)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{not json", encoding="utf-8")
        r = client.post("/duration-memory/save", json={
            "identity": DM_IDENTITY, "minutes": 90,
        }, headers=_auth(client))
        assert r.status_code == 500
        assert str(vault) not in r.text
        assert str(dm.cache_path(vault)) not in r.text
        detail = r.json()["detail"]
        assert isinstance(detail, dict)
        assert "message" in detail

    def test_reset_cache_error_detail_has_no_absolute_path(self, client, vault):
        # FT-06 resolve-first: a valid source fallback must exist before the
        # route touches the cache, so seed the vault to reach the store-error
        # path. A corrupt cache then fails closed with a bounded 500.
        _seed_dm_vault(vault)
        p = dm.cache_path(vault)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{not json", encoding="utf-8")
        r = client.post("/duration-memory/reset", json={
            "identity": DM_IDENTITY,
        }, headers=_auth(client))
        assert r.status_code == 500
        assert str(vault) not in r.text
        assert str(dm.cache_path(vault)) not in r.text
        detail = r.json()["detail"]
        assert isinstance(detail, dict)
        assert "message" in detail

    def test_save_oserror_detail_has_no_absolute_path(self, client, vault, monkeypatch):
        def boom(path, data):
            raise OSError(
                "write failed at /private/tmp/secret-cache/tdtb-duration-memory.json"
            )

        monkeypatch.setattr(dm, "_atomic_write_json", boom)
        r = client.post("/duration-memory/save", json={
            "identity": DM_IDENTITY, "minutes": 90,
        }, headers=_auth(client))
        assert r.status_code == 500
        assert "/private/tmp/secret-cache" not in r.text
        detail = r.json()["detail"]
        assert isinstance(detail, dict)
        assert "message" in detail


class TestDurationMemoryIsolation:
    def test_app_vault_isolation(self, tmp_path):
        a = tmp_path / "vault-a"
        b = tmp_path / "vault-b"
        a.mkdir()
        b.mkdir()
        app_a = main_mod.create_app(vault_root=a)
        app_b = main_mod.create_app(vault_root=b)
        c_a = TestClient(app_a)
        c_b = TestClient(app_b)
        h_a = {"X-TDTB-Token": app_a.state.token}
        r = c_a.post("/duration-memory/save", json={
            "identity": "todoist:1", "minutes": 90,
        }, headers=h_a)
        assert r.status_code == 200
        assert dm.read_vault_memory(a) == {"todoist:1": 90}
        assert dm.read_vault_memory(b) == {}
# T02: GET /version — tokenless, pure read-only source/runtime fingerprint
# ---------------------------------------------------------------------------

VERSION_FIELDS = {
    "status", "source_commit", "source_tree", "cockpit_index_sha256",
    "cockpit_asset", "cockpit_asset_sha256",
}
_GIT_ID_RE = re.compile(r"^[0-9a-f]{40}$")
_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")
_ASSET_RE = re.compile(r"^assets/index-[A-Za-z0-9_-]+\.js$")


def _make_git_repo(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email",
                    "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name",
                    "TDTB Test"], check=True)
    (root / "seed.txt").write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "init"], check=True)


def _git_ids(root: Path) -> tuple[str, str]:
    commit = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True).stdout.strip()
    tree = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD^{tree}"],
        capture_output=True, text=True, check=True).stdout.strip()
    return commit, tree


def _seed_cockpit_repo(tmp_path: Path, index_text: str, asset_name: str,
                       asset_bytes: bytes | None) -> tuple[Path, Path]:
    """(repo_root, cockpit_dir) fixture: a real git repo whose committed
    static/cockpit holds index.html plus (when asset_bytes is not None) the
    referenced production asset."""
    repo = tmp_path / "repo"
    _make_git_repo(repo)
    cockpit = repo / "static" / "cockpit"
    cockpit.mkdir(parents=True, exist_ok=True)
    (cockpit / "index.html").write_text(index_text, encoding="utf-8")
    if asset_bytes is not None:
        (cockpit / asset_name).parent.mkdir(parents=True, exist_ok=True)
        (cockpit / asset_name).write_bytes(asset_bytes)
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "cockpit"],
                   check=True)
    return repo, cockpit


def _point_version_paths(client, repo_root: Path, cockpit_dir: Path) -> None:
    client.app.state.repo_root = str(repo_root)
    client.app.state.cockpit_dir = str(cockpit_dir)


class TestVersionEndpoint:
    def test_version_tokenless_exact_fields_and_formats(self, client):
        r = client.get("/version")  # no X-TDTB-Token header
        assert r.status_code == 200
        body = r.json()
        assert set(body) == VERSION_FIELDS  # exactly six fields, no extras
        assert body["status"] == "ok"
        assert _GIT_ID_RE.fullmatch(body["source_commit"])
        assert _GIT_ID_RE.fullmatch(body["source_tree"])
        assert _SHA256_HEX_RE.fullmatch(body["cockpit_index_sha256"])
        assert _SHA256_HEX_RE.fullmatch(body["cockpit_asset_sha256"])
        assert _ASSET_RE.fullmatch(body["cockpit_asset"])

    def test_version_does_not_require_vault_or_config(self, monkeypatch):
        monkeypatch.delenv(main_mod.VAULT_ROOT_ENV, raising=False)
        c = TestClient(main_mod.create_app())
        assert c.get("/version").status_code == 200
        assert c.get("/config").status_code == 503  # contrast: config needs vault

    def test_version_deterministic_and_pins_fixture_values(self, client, tmp_path):
        asset_name = "assets/index-abc12345.js"
        asset_bytes = b"export const x = 1;\n"
        repo, cockpit = _seed_cockpit_repo(
            tmp_path,
            '<script type="module" src="./assets/index-abc12345.js"></script>',
            asset_name, asset_bytes,
        )
        _point_version_paths(client, repo, cockpit)

        first = client.get("/version").json()
        second = client.get("/version").json()
        assert first == second  # deterministic across calls

        commit, tree = _git_ids(repo)
        assert first["source_commit"] == commit
        assert first["source_tree"] == tree
        assert first["cockpit_index_sha256"] == hashlib.sha256(
            (cockpit / "index.html").read_bytes()).hexdigest()
        assert first["cockpit_asset"] == asset_name
        assert first["cockpit_asset_sha256"] == hashlib.sha256(asset_bytes).hexdigest()

    def test_version_no_state_bearing_calls(self, client, monkeypatch):
        def _forbidden(*_a, **_k):
            raise AssertionError(
                "state-bearing surface must never be called by GET /version")

        monkeypatch.setattr(main_mod.judgment, "propose_sequence", _forbidden)
        monkeypatch.setattr(main_mod.judgment, "adjust_freetext", _forbidden)
        monkeypatch.setattr(main_mod.runstate, "update_runstate", _forbidden)
        monkeypatch.setattr(main_mod.runstate, "write_digest_index", _forbidden)
        monkeypatch.setattr(main_mod.external_sources, "fetch_todoist_items",
                            _forbidden)
        monkeypatch.setattr(main_mod.external_sources, "fetch_calendar_busy",
                            _forbidden)
        monkeypatch.setattr(main_mod.calendar_bridge, "shared_store", _forbidden)
        monkeypatch.setattr(main_mod.config_reader, "read_config", _forbidden)

        r = client.get("/version")
        assert r.status_code == 200

    @pytest.mark.parametrize(
        "kind,index_text,asset_name,asset_bytes",
        [
            ("no_index", "", "assets/index-x.js", b"x"),
            ("ambiguous",
             '<script src="./assets/index-a.js"></script>'
             '<script src="./assets/index-b.js"></script>',
             "assets/index-a.js", b"x"),
            ("missing_asset",
             '<script src="./assets/index-missing.js"></script>',
             "assets/index-missing.js", None),
        ],
    )
    def test_version_fails_closed(self, client, tmp_path, kind, index_text,
                                  asset_name, asset_bytes):
        repo, cockpit = _seed_cockpit_repo(
            tmp_path, index_text, asset_name, asset_bytes)
        _point_version_paths(client, repo, cockpit)
        before = sorted(p.name for p in cockpit.iterdir())
        r = client.get("/version")
        assert r.status_code == 503
        assert sorted(p.name for p in cockpit.iterdir()) == before  # no mutation

    def test_version_fails_closed_no_git_identity(self, client, tmp_path):
        plain = tmp_path / "plain"
        plain.mkdir(parents=True, exist_ok=True)
        cockpit = plain / "static" / "cockpit"
        cockpit.mkdir(parents=True)
        (cockpit / "index.html").write_text(
            '<script src="./assets/index-x.js"></script>', encoding="utf-8")
        (cockpit / "assets").mkdir()
        (cockpit / "assets/index-x.js").write_bytes(b"x")
        _point_version_paths(client, plain, cockpit)
        r = client.get("/version")
        assert r.status_code == 503
