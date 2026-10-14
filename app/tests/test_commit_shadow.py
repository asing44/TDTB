"""Route tests for POST /commit?mode=shadow (T13) — shadow.gather_live_state
is monkeypatched so no live Todoist/EventKit calls happen under pytest.
Verifies shadow-default-off backward compat (bare /commit still 501s, per
tests/test_main_api.py's pre-existing contract), the token guard, mode
dispatch, and — the core no-write guarantee — that the vault tree is
untouched after a shadow commit call."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))
import main as main_mod  # noqa: E402
import runstate  # noqa: E402
import commit  # noqa: E402
import commit_run  # noqa: E402
import capacities_selections  # noqa: E402
import shadow  # noqa: E402
from datetime import date  # noqa: E402
from tests.config_markdown import write_config_note  # noqa: E402


@pytest.fixture
def vault(tmp_path) -> Path:
    v = tmp_path / "vault-root"
    v.mkdir()
    proj_dir = v / "50 - Operations" / "Projects"
    proj_dir.mkdir(parents=True)
    (proj_dir / "Garage Buildout.md").write_text(
        "---\ntype: project\nassigned: false\n---\nbody\n", encoding="utf-8"
    )
    config_path = v / "00 - META" / "Skill-Configs" / "tdtb-bridger.md"
    config_path.parent.mkdir(parents=True)
    write_config_note(config_path,
        "## Defaults\n"
        "| Key | Value |\n|---|---|\n| eod | 11:59 PM |\n\n"
        "## Template Blocks\n"
        "### Trinoor Hours\n"
        "| Slot | Start | End |\n|---|---|---|\n"
        "| Morning | 12:00 AM | 11:59 PM |\n",
        encoding="utf-8",
    )
    # P3-02: persist today's server digest identity index so the commit
    # eligibility boundary can verify submitted assigned names.
    today = main_mod.gather.effective_date(main_mod.datetime.now())
    runstate.write_digest_index(
        today, [{"name": "Garage Buildout",
                    "todoist_id": "",
                    "path": "50 - Operations/Projects/Garage Buildout.md",
                    "surface": "assigned"}]
    )
    runstate.write_runstate(
        today, runstate.build_runstate({"anchor": "00:00"}),
    )
    return v


@pytest.fixture
def client(vault) -> TestClient:
    app = main_mod.create_app()
    c = TestClient(app)
    c.app_token = app.state.token
    return c


def _auth(client: TestClient) -> dict:
    return {"X-TDTB-Token": client.app_token}


DIGEST = {"assigned": [{"name": "Garage Buildout", "path": "50 - Operations/Projects/Garage Buildout.md"}]}
SEQUENCE = {"sequence": [{"id": "Garage Buildout", "start": "09:00", "end": "10:00", "zone": "any"}]}


class TestModeDispatch:
    def test_bare_commit_still_501s_legacy_stub(self, client):
        """Backward compat: the pre-T13 stub contract (bare POST /commit ->
        501 'T14/T15') is preserved — this is tests/test_main_api.py's
        test_stub_routes_return_501_with_task_pointer, kept green here as a
        second witness against regressions in the mode-dispatch rewrite."""
        r = client.post("/commit", headers=_auth(client))
        assert r.status_code == 501
        assert "T14" in r.json()["detail"]

    def test_mode_live_without_body_is_400(self, client):
        """T15: mode=live now dispatches for real (see tests/test_main_api.py's
        TestLiveCommit) — only a bare call with NO mode at all still 501s
        (test_bare_commit_still_501s_legacy_stub above). A live call missing
        its required digest/sequence body 400s, same shape as the shadow path."""
        r = client.post("/commit?mode=live", headers=_auth(client))
        assert r.status_code == 400

    def test_unknown_mode_is_400(self, client):
        r = client.post("/commit?mode=bogus", headers=_auth(client))
        assert r.status_code == 400

    def test_shadow_without_body_is_400(self, client):
        r = client.post("/commit?mode=shadow", headers=_auth(client))
        assert r.status_code == 400

    def test_shadow_requires_token(self, client):
        r = client.post("/commit?mode=shadow", json={"digest": DIGEST, "sequence": SEQUENCE})
        assert r.status_code == 403


class TestShadowFlow:
    def test_shadow_returns_diff_and_writes_nothing(self, client, vault, monkeypatch):
        def fake_gather(config):
            return {
                "todoist_tasks": [],
                "calendar_events": [],
            }

        monkeypatch.setattr(shadow, "gather_live_state", fake_gather)

        before = {p: p.read_bytes() for p in vault.rglob("*") if p.is_file()}

        r = client.post(
            "/commit?mode=shadow",
            headers=_auth(client),
            json={"digest": DIGEST, "sequence": SEQUENCE, "config": {}},
        )
        assert r.status_code == 200
        body = r.json()
        assert "entries" in body and "counts" in body and "unavailable_surfaces" in body

        # Step A only (todoist create, no live task). V3: no Step B daily-note
        # patch and no Step C vault flip rows. No anchored blocks in this
        # request's config, so no Step D/E rows; recent-selections is a
        # post-commit action, never a manifest row.
        classifications = {e["manifest"]["step"]: e["classification"] for e in body["entries"]}
        assert classifications == {"A": shadow.CREATE}
        assert all(e["manifest"]["system"] != "vault" for e in body["entries"])

        after = {p: p.read_bytes() for p in vault.rglob("*") if p.is_file()}
        assert before == after

    def test_shadow_state_error_becomes_502(self, client, monkeypatch):
        def raise_state_error(config):
            raise shadow.ShadowStateError("token file missing")

        monkeypatch.setattr(shadow, "gather_live_state", raise_state_error)

        r = client.post(
            "/commit?mode=shadow",
            headers=_auth(client),
            json={"digest": DIGEST, "sequence": SEQUENCE, "config": {}},
        )
        assert r.status_code == 502
        assert "token file missing" in r.json()["detail"]

class TestCommitEligibility:
    """P3-02: /commit must reject stale/dropped digest & sequence rows with
    422 before any manifest is built, while legitimate rows still pass."""

    def test_stale_assigned_item_is_422(self, client, vault, monkeypatch):
        def fake_gather(config):
            return {
                "todoist_tasks": [], "calendar_events": [],
                "vault_frontmatter": {}, "daily_note_text": None,
            }

        monkeypatch.setattr(shadow, "gather_live_state", fake_gather)
        stale_digest = {"assigned": [
            {"name": "Garage Buildout",
             "path": "50 - Operations/Projects/Garage Buildout.md"},
            {"name": "Dropped Item",
             "path": "50 - Operations/Projects/Dropped Item.md"},
        ]}
        before = {p: p.read_bytes() for p in vault.rglob("*") if p.is_file()}

        r = client.post(
            "/commit?mode=shadow", headers=_auth(client),
            json={"digest": stale_digest, "sequence": SEQUENCE, "config": {}},
        )
        assert r.status_code == 422
        assert "Dropped Item" in r.json()["detail"]
        assert "Nothing was written" in r.json()["detail"]
        after = {p: p.read_bytes() for p in vault.rglob("*") if p.is_file()}
        assert before == after

    SHIP_IDENTITY = "capacities:space-1:RootTask:object-1"

    def _seed_ship_project_index(self):
        today = main_mod.gather.effective_date(main_mod.datetime.now())
        runstate.write_digest_index(today, [{
            "name": "Ship project",
            "path": "capacities://space-1/object-1",
            "identity": self.SHIP_IDENTITY,
            "source": "capacities",
            "capacities_id": "object-1",
            "capacities_space_id": "space-1",
            "capacities_structure_id": "RootTask",
            "capacities_completion_supported": True,
            "source_fingerprint": "fingerprint-1",
            "surface": "assigned",
        }])

    def test_selected_capacities_row_is_admitted_as_phep_mirror(
        self, client, vault, monkeypatch
    ):
        self._seed_ship_project_index()
        capacities_selections.save_selections(
            space_id="space-1", expected_revision=0,
            selections=[{
                "identity": self.SHIP_IDENTITY,
                "rules_revision": 0, "acknowledged": True,
            }],
        )
        monkeypatch.setattr(shadow, "gather_live_state", lambda config: {
            "todoist_tasks": [], "calendar_events": [], "vault_frontmatter": {},
            "daily_note_text": "# TDTB Plan\n",
        })
        before = {p: p.read_bytes() for p in vault.rglob("*") if p.is_file()}

        response = client.post(
            "/commit?mode=shadow",
            headers=_auth(client),
            json={
                "digest": {"assigned": [{
                    "name": "Ship project",
                    "path": "capacities://space-1/object-1",
                    "identity": self.SHIP_IDENTITY,
                }]},
                "sequence": {"sequence": [{
                    "id": "Ship project", "start": "09:00", "end": "10:00",
                }]},
                "config": {},
            },
        )

        assert response.status_code == 200, response.text
        manifest = [e["manifest"] for e in response.json()["entries"]]
        ship = [m for m in manifest if m["name"] == "Ship project"]
        # PHep mirror (contract change 2026-10-13, revised): only a PHEP
        # Todoist create, no ⬜ Blocks calendar event. The Capacities-only
        # plan still writes no vault intent and nothing live (shadow mode).
        assert [(m["system"], m["action"], m["routing"]) for m in ship] == [
            ("todoist", "schedule", "PHEP"),
        ]
        assert [m for m in manifest if m["system"] == "todoist"] == [
            m for m in ship if m["system"] == "todoist"]
        after = {p: p.read_bytes() for p in vault.rglob("*") if p.is_file()}
        assert before == after

    def test_capacities_row_matched_by_name_and_path_only_is_refused(self, client, vault):
        self._seed_ship_project_index()
        before = {p: p.read_bytes() for p in vault.rglob("*") if p.is_file()}

        response = client.post(
            "/commit?mode=shadow",
            headers=_auth(client),
            json={
                "digest": {"assigned": [{
                    "name": "Ship project",
                    "path": "capacities://space-1/object-1",
                }]},
                "sequence": {"sequence": [{
                    "id": "Ship project", "start": "09:00", "end": "10:00",
                }]},
                "config": {},
            },
        )

        assert response.status_code == 422
        assert "server-indexed identity" in response.json()["detail"]
        assert "Ship project" in response.json()["detail"]
        assert "plan-only" not in response.json()["detail"]
        after = {p: p.read_bytes() for p in vault.rglob("*") if p.is_file()}
        assert before == after

    def test_stale_sequence_row_is_422(self, client, vault, monkeypatch):
        def fake_gather(config):
            return {
                "todoist_tasks": [], "calendar_events": [],
                "vault_frontmatter": {}, "daily_note_text": None,
            }

        monkeypatch.setattr(shadow, "gather_live_state", fake_gather)
        stale_seq = {"sequence": [
            {"id": "Garage Buildout", "start": "09:00", "end": "10:00", "zone": "any"},
            {"id": "Ghost Row", "start": "10:00", "end": "11:00", "zone": "any"},
        ]}
        before = {p: p.read_bytes() for p in vault.rglob("*") if p.is_file()}

        r = client.post(
            "/commit?mode=shadow", headers=_auth(client),
            json={"digest": DIGEST, "sequence": stale_seq, "config": {}},
        )
        assert r.status_code == 422
        assert "Ghost Row" in r.json()["detail"]
        after = {p: p.read_bytes() for p in vault.rglob("*") if p.is_file()}
        assert before == after

    def test_valid_schedulable_row_accepted(self, client, vault, monkeypatch):
        """Minting (and its legacy display variant) is a legitimate Step D row
        and must reach the manifest/diff, not the eligibility rejection."""
        def fake_gather(config):
            return {
                "todoist_tasks": [], "calendar_events": [],
                "vault_frontmatter": {}, "daily_note_text": None,
            }

        monkeypatch.setattr(shadow, "gather_live_state", fake_gather)
        sched_seq = {"sequence": [
            {"id": "Garage Buildout", "start": "09:00", "end": "10:00", "zone": "any"},
            {"id": "Minting", "start": "10:00", "end": "11:00", "zone": "work_hours"},
            {"id": "🌊 Minting", "start": "11:00", "end": "11:30", "zone": "work_hours"},
        ]}
        r = client.post(
            "/commit?mode=shadow", headers=_auth(client),
            json={"digest": DIGEST, "sequence": sched_seq, "config": {}},
        )
        assert r.status_code == 200
        body = r.json()
        steps = {e["manifest"]["step"] for e in body["entries"]}
        assert "A" in steps and "D" in steps

    def test_valid_anchored_and_trinoor_rows_accepted(self, client, vault, monkeypatch):
        """Anchored (Step E) and Trinoor zone (Step D′) rows pass the guard."""
        def fake_gather(config):
            return {
                "todoist_tasks": [], "calendar_events": [],
                "vault_frontmatter": {}, "daily_note_text": None,
            }

        monkeypatch.setattr(shadow, "gather_live_state", fake_gather)
        config_path = vault / "00 - META" / "Skill-Configs" / "tdtb-bridger.md"
        write_config_note(config_path,
            "## Defaults\n"
            "| Key | Value |\n|---|---|\n| eod | 11:59 PM |\n\n"
            "## Anchored Lifestyle Blocks\n"
            "| Block | Type | Start | End | Duration | Days |\n"
            "|---|---|---|---|---|---|\n"
            "| Press | hard | 7:00 AM | — | 60m | daily |\n\n"
            "## Template Blocks\n"
            "### Trinoor Hours\n"
            "| Slot | Start | End |\n|---|---|---|\n"
            "| Morning | 8:30 AM | 12:30 PM |\n",
            encoding="utf-8",
        )
        config = {
            "anchored_blocks": [{"id": "Press", "Start": "07:00", "Duration": 60}],
            "Template Blocks": {"Trinoor Hours": [
                {"Slot": "Morning", "Start": "8:30 AM", "End": "12:30 PM"},
            ]},
        }
        seq = {"sequence": [
            {"id": "Garage Buildout", "start": "09:00", "end": "10:00", "zone": "any"},
            {"id": "Press", "start": "07:00", "end": "08:00", "zone": "any"},
            {"id": "🟡 Trinoor : Morning", "start": "08:30", "end": "12:30",
             "zone": "work_hours", "backdrop": True},
        ]}
        r = client.post(
            "/commit?mode=shadow", headers=_auth(client),
            json={"digest": DIGEST, "sequence": seq, "config": config},
        )
        assert r.status_code == 200
        steps = {e["manifest"]["step"] for e in r.json()["entries"]}
        assert "A" in steps and "E" in steps and "D′" in steps

    def test_live_stale_row_422_before_write(self, client, vault, monkeypatch):
        """The same guard gates mode=live before the write path (shadow-only
        test: the injected client records nothing)."""
        class _WritesRecorder:
            def __init__(self, log):
                self._log = log

        writes = []

        class _FakeStore:
            def calendars(self):
                return []

        def _fake_state(config):
            return {
                "todoist_tasks": [], "calendar_events": [],
                "vault_frontmatter": {}, "daily_note_text": None,
            }

        client.app.state.build_commit_clients = (
            lambda cfg: (_WritesRecorder(writes), _FakeStore())
        )
        monkeypatch.setattr(shadow, "gather_live_state", _fake_state)
        # FEEDBACK-24 gate: confirm Day Setup first so the eligibility guard
        # (422), not the Day Setup gate (409), is what fires.
        assert client.post("/day-setup", json={"anchor": "09:00"},
                           headers=_auth(client)).status_code == 200
        stale_digest = {"assigned": [{"name": "Gone Item", "path": "P/Gone.md"}]}
        before = {p: p.read_bytes() for p in vault.rglob("*") if p.is_file()}

        r = client.post(
            "/commit?mode=live", headers=_auth(client),
            json={"digest": stale_digest, "sequence": SEQUENCE, "config": {}},
        )
        assert r.status_code == 422
        assert "Gone Item" in r.json()["detail"]
        assert writes == []
        after = {p: p.read_bytes() for p in vault.rglob("*") if p.is_file()}
        assert before == after


    # ---- P3-02 hardening: server authorization boundary --------------------

    def _write_mixed_surface_index(self) -> None:
        """Index with the fixture's assigned row plus a suggested-only row, so
        a client cannot promote the suggested row by naming it as assigned."""
        today = main_mod.gather.effective_date(main_mod.datetime.now())
        runstate.write_digest_index(
            today,
            [{"name": "Garage Buildout", "todoist_id": "",
              "path": "50 - Operations/Projects/Garage Buildout.md",
              "surface": "assigned"},
             {"name": "Pool Item", "todoist_id": "",
              "path": "50 - Operations/Pool/Pool Item.md",
              "surface": "suggested"}],
        )

    def _confirm_day_setup(self, client) -> None:
        assert client.post("/day-setup", json={"anchor": "09:00"},
                           headers=_auth(client)).status_code == 200

    def test_suggested_to_assigned_promotion_is_422(self, client, vault):
        """A suggested-only server row must not be promotable to the assigned
        surface just because the client labels it assigned."""
        self._write_mixed_surface_index()
        before = {p: p.read_bytes() for p in vault.rglob("*") if p.is_file()}
        promo_digest = {"assigned": [
            {"name": "Pool Item", "path": "50 - Operations/Pool/Pool Item.md"},
        ]}
        r = client.post(
            "/commit?mode=shadow", headers=_auth(client),
            json={"digest": promo_digest, "sequence": SEQUENCE, "config": {}},
        )
        assert r.status_code == 422
        assert "Pool Item" in r.json()["detail"]
        assert "Nothing was written" in r.json()["detail"]
        after = {p: p.read_bytes() for p in vault.rglob("*") if p.is_file()}
        assert before == after

    def test_path_spoof_is_422(self, client, vault):
        """Matching name but a tampered path must not authorize the row."""
        before = {p: p.read_bytes() for p in vault.rglob("*") if p.is_file()}
        spoof_digest = {"assigned": [
            {"name": "Garage Buildout",
             "path": "50 - Operations/Projects/Imposter.md"},
        ]}
        r = client.post(
            "/commit?mode=shadow", headers=_auth(client),
            json={"digest": spoof_digest, "sequence": SEQUENCE, "config": {}},
        )
        assert r.status_code == 422
        assert "Garage Buildout" in r.json()["detail"]
        after = {p: p.read_bytes() for p in vault.rglob("*") if p.is_file()}
        assert before == after

    def test_todoist_id_spoof_is_422(self, client, vault):
        """A forged todoist_id on an otherwise valid row must not authorize it
        (the server index row has no todoist_id)."""
        before = {p: p.read_bytes() for p in vault.rglob("*") if p.is_file()}
        spoof_digest = {"assigned": [
            {"name": "Garage Buildout",
             "path": "50 - Operations/Projects/Garage Buildout.md",
             "todoist_id": "T999"},
        ]}
        r = client.post(
            "/commit?mode=shadow", headers=_auth(client),
            json={"digest": spoof_digest, "sequence": SEQUENCE, "config": {}},
        )
        assert r.status_code == 422
        assert "Garage Buildout" in r.json()["detail"]
        after = {p: p.read_bytes() for p in vault.rglob("*") if p.is_file()}
        assert before == after

    def test_nonexistent_schedulable_id_is_422(self, client, vault):
        """A sequence row naming no current server-derived schedulable item is
        refused even though its digest row is fully authorized."""
        before = {p: p.read_bytes() for p in vault.rglob("*") if p.is_file()}
        ghost_seq = {"sequence": [
            {"id": "Garage Buildout", "start": "09:00", "end": "10:00", "zone": "any"},
            {"id": "Shivery Session", "start": "10:00", "end": "11:00", "zone": "any"},
        ]}
        r = client.post(
            "/commit?mode=shadow", headers=_auth(client),
            json={"digest": DIGEST, "sequence": ghost_seq, "config": {}},
        )
        assert r.status_code == 422
        assert "Shivery Session" in r.json()["detail"]
        after = {p: p.read_bytes() for p in vault.rglob("*") if p.is_file()}
        assert before == after

    def test_inactive_minting_row_is_422(self, client, vault):
        """A currently disabled Minting projection is not an authorization
        source merely because Minting is a known legacy schedulable name."""
        today = main_mod.gather.effective_date(main_mod.datetime.now())
        runstate.update_runstate(today, {
            "work_allotment_minutes": 0,
        })
        before = {p: p.read_bytes() for p in vault.rglob("*") if p.is_file()}
        inactive_seq = {"sequence": [
            {"id": "Garage Buildout", "start": "09:00", "end": "10:00", "zone": "any"},
            {"id": "Minting", "start": "10:00", "end": "11:00", "zone": "work_hours"},
        ]}
        r = client.post(
            "/commit?mode=shadow", headers=_auth(client),
            json={"digest": DIGEST, "sequence": inactive_seq, "config": {}},
        )
        assert r.status_code == 422
        assert "Minting" in r.json()["detail"]
        after = {p: p.read_bytes() for p in vault.rglob("*") if p.is_file()}
        assert before == after

    def test_fabricated_trinoor_id_is_422_and_server_zone_rows_authoritative(
            self, client, vault):
        """A Trinoor slot the server projection does not emit is refused, even
        when the client fabricates its own Trinoor config defining it."""
        before = {p: p.read_bytes() for p in vault.rglob("*") if p.is_file()}
        forged_config = {
            "Template Blocks": {"Trinoor Hours": [
                {"Slot": "Morning", "Start": "12:00 AM", "End": "11:59 PM"},
                {"Slot": "Afternoon", "Start": "12:30 PM", "End": "5:30 PM"},
            ]},
        }
        seq = {"sequence": [
            {"id": "Garage Buildout", "start": "09:00", "end": "10:00", "zone": "any"},
            {"id": "🟡 Trinoor : Afternoon", "start": "13:00", "end": "14:00",
             "zone": "work_hours", "backdrop": True},
        ]}
        r = client.post(
            "/commit?mode=shadow", headers=_auth(client),
            json={"digest": DIGEST, "sequence": seq, "config": forged_config},
        )
        assert r.status_code == 422
        assert "Trinoor : Afternoon" in r.json()["detail"]
        after = {p: p.read_bytes() for p in vault.rglob("*") if p.is_file()}
        assert before == after

    def test_client_only_anchored_row_is_422(self, client, vault):
        """An anchored block present only in the client config is never
        authorization for a Step E sequence row."""
        before = {p: p.read_bytes() for p in vault.rglob("*") if p.is_file()}
        client_config = {
            "anchored_blocks": [{"id": "Press", "Start": "07:00", "Duration": 60}],
        }
        seq = {"sequence": [
            {"id": "Garage Buildout", "start": "09:00", "end": "10:00", "zone": "any"},
            {"id": "Press", "start": "07:00", "end": "08:00", "zone": "any"},
        ]}
        r = client.post(
            "/commit?mode=shadow", headers=_auth(client),
            json={"digest": DIGEST, "sequence": seq, "config": client_config},
        )
        assert r.status_code == 422
        assert "Press" in r.json()["detail"]
        assert "client-only anchored" in r.json()["detail"]
        after = {p: p.read_bytes() for p in vault.rglob("*") if p.is_file()}
        assert before == after

    def test_client_only_anchored_row_is_422_with_empty_assigned_digest(
            self, client, vault):
        """Same rejection when the client submits an empty assigned digest."""
        before = {p: p.read_bytes() for p in vault.rglob("*") if p.is_file()}
        client_config = {
            "anchored_blocks": [{"id": "Press", "Start": "07:00", "Duration": 60}],
        }
        seq = {"sequence": [
            {"id": "Press", "start": "07:00", "end": "08:00", "zone": "any"},
        ]}
        r = client.post(
            "/commit?mode=shadow", headers=_auth(client),
            json={"digest": {"assigned": []}, "sequence": seq,
                  "config": client_config},
        )
        assert r.status_code == 422
        assert "Press" in r.json()["detail"]
        after = {p: p.read_bytes() for p in vault.rglob("*") if p.is_file()}
        assert before == after

    def test_malformed_assigned_rows_are_422(self, client, vault):
        """Non-dict rows and rows lacking any identity field are refused."""
        before = {p: p.read_bytes() for p in vault.rglob("*") if p.is_file()}
        bad_digest = {"assigned": [
            "Garage Buildout",
            {"name": "No Identity"},
        ]}
        r = client.post(
            "/commit?mode=shadow", headers=_auth(client),
            json={"digest": bad_digest, "sequence": SEQUENCE, "config": {}},
        )
        assert r.status_code == 422
        assert "malformed assigned rows" in r.json()["detail"]
        after = {p: p.read_bytes() for p in vault.rglob("*") if p.is_file()}
        assert before == after

    def test_malformed_sequence_rows_are_422(self, client, vault):
        """Non-dict rows and rows with an empty id are refused."""
        before = {p: p.read_bytes() for p in vault.rglob("*") if p.is_file()}
        bad_seq = {"sequence": [
            {"id": "Garage Buildout", "start": "09:00", "end": "10:00", "zone": "any"},
            "not-a-row",
            {"start": "11:00", "end": "12:00", "zone": "any"},
        ]}
        r = client.post(
            "/commit?mode=shadow", headers=_auth(client),
            json={"digest": DIGEST, "sequence": bad_seq, "config": {}},
        )
        assert r.status_code == 422
        assert "malformed sequence rows" in r.json()["detail"]
        after = {p: p.read_bytes() for p in vault.rglob("*") if p.is_file()}
        assert before == after

    def test_empty_server_index_cannot_be_bypassed_by_forged_identity_or_config(
            self, client, vault):
        """With no server-derived assigned identity for today, submitting the
        otherwise-legitimate digest — plus forged todoist ids and config —
        still fails closed."""
        today = main_mod.gather.effective_date(main_mod.datetime.now())
        runstate.write_digest_index(today, [])
        before = {p: p.read_bytes() for p in vault.rglob("*") if p.is_file()}
        forged_digest = {"assigned": [
            {"name": "Garage Buildout",
             "path": "50 - Operations/Projects/Garage Buildout.md",
             "todoist_id": "T1"},
        ]}
        forged_config = {
            "anchored_blocks": [{"id": "Press", "Start": "07:00", "Duration": 60}],
        }
        r = client.post(
            "/commit?mode=shadow", headers=_auth(client),
            json={"digest": forged_digest, "sequence": SEQUENCE,
                  "config": forged_config},
        )
        assert r.status_code == 422
        assert "no server-derived assigned identity" in r.json()["detail"]
        after = {p: p.read_bytes() for p in vault.rglob("*") if p.is_file()}
        assert before == after

    def test_live_suggested_promotion_rejected_before_any_write(
            self, client, vault):
        """Live mode: the promotion attempt is refused at the eligibility
        boundary; the injected write client is never touched."""
        self._write_mixed_surface_index()

        writes = []

        class _WritesRecorder:
            def __init__(self, log):
                self._log = log

        class _FakeStore:
            def calendars(self):
                return []

        client.app.state.build_commit_clients = (
            lambda cfg: (_WritesRecorder(writes), _FakeStore())
        )
        self._confirm_day_setup(client)
        before = {p: p.read_bytes() for p in vault.rglob("*") if p.is_file()}
        promo_digest = {"assigned": [
            {"name": "Pool Item", "path": "50 - Operations/Pool/Pool Item.md"},
        ]}
        r = client.post(
            "/commit?mode=live", headers=_auth(client),
            json={"digest": promo_digest, "sequence": SEQUENCE, "config": {}},
        )
        assert r.status_code == 422
        assert "Pool Item" in r.json()["detail"]
        assert writes == []
        after = {p: p.read_bytes() for p in vault.rglob("*") if p.is_file()}
        assert before == after

    def test_live_forged_anchored_row_rejected_before_any_write(
            self, client, vault):
        """Live mode: a client-only anchored block cannot smuggle a Step E row
        past the boundary; the injected write client is never touched."""
        writes = []

        class _WritesRecorder:
            def __init__(self, log):
                self._log = log

        class _FakeStore:
            def calendars(self):
                return []

        client.app.state.build_commit_clients = (
            lambda cfg: (_WritesRecorder(writes), _FakeStore())
        )
        self._confirm_day_setup(client)
        before = {p: p.read_bytes() for p in vault.rglob("*") if p.is_file()}
        client_config = {
            "anchored_blocks": [{"id": "Press", "Start": "07:00", "Duration": 60}],
        }
        seq = {"sequence": [
            {"id": "Garage Buildout", "start": "09:00", "end": "10:00", "zone": "any"},
            {"id": "Press", "start": "07:00", "end": "08:00", "zone": "any"},
        ]}
        r = client.post(
            "/commit?mode=live", headers=_auth(client),
            json={"digest": DIGEST, "sequence": seq, "config": client_config},
        )
        assert r.status_code == 422
        assert "Press" in r.json()["detail"]
        assert writes == []
        after = {p: p.read_bytes() for p in vault.rglob("*") if p.is_file()}
        assert before == after


class TestTodoistIdMatching:
    """Gather-parity T8 finding: disambiguated names ("Stillness (Todoist)")
    broke content matching — a live commit would duplicate-create. Sourced
    manifest rows carry todoist://<id> in id_or_path; matching must prefer
    the id and fall back to content."""

    LIVE = {"todoist_tasks": [
        {"id": "T1", "content": "Stillness", "due": {"datetime": "2026-07-14T09:00:00"}},
    ]}

    def _entry(self, name, ref):
        m = shadow.ManifestEntry(
            step="A", system="todoist", action="schedule", name=name,
            id_or_path=ref, time="12:45", duration_min=15, routing="Inbox",
        )
        diff = shadow.diff_against_live([m], dict(self.LIVE))
        return diff.entries[0]

    def test_renamed_item_matches_by_id(self):
        e = self._entry("Stillness (Todoist)", "todoist://T1")
        assert e.classification == shadow.UPDATE
        assert e.detail["task_id"] == "T1"

    def test_unknown_id_still_creates(self):
        e = self._entry("Brand new", "todoist://T999")
        assert e.classification == shadow.CREATE

    def test_vault_item_still_matches_by_content(self):
        e = self._entry("Stillness", "50 - Operations/Intervals/Stillness.md")
        assert e.classification == shadow.UPDATE


class TestExactSequenceAuthorization:
    """P3 BLOCK findings: fixed-lane rows (anchored, Trinoor zones) must carry
    exact server timing; backdrop flag only honored on server zone rows;
    malformed timing and duplicates fail closed; assigned metadata must be
    sanitized before manifest construction; dropped items invalidate stale
    cached identities at commit time."""

    def _write_anchored_vault_config(self, vault: Path) -> None:
        config_path = vault / "00 - META" / "Skill-Configs" / "tdtb-bridger.md"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        write_config_note(config_path,
            "## Defaults\n"
            "| Key | Value |\n|---|---|\n| eod | 11:59 PM |\n\n"
            "## Anchored Lifestyle Blocks\n"
            "| Block | Type | Start | End | Duration | Days |\n"
            "|---|---|---|---|---|---|\n"
            "| Press | hard | 7:00 AM | — | 60m | daily |\n\n"
            "## Template Blocks\n"
            "### Trinoor Hours\n"
            "| Slot | Start | End |\n|---|---|---|\n"
            "| Morning | 8:30 AM | 12:30 PM |\n",
            encoding="utf-8",
        )

    def test_forged_anchored_timing_is_422(self, client, vault, monkeypatch):
        """A valid anchored ID with a forged start must not be authorized."""
        monkeypatch.setattr(shadow, "gather_live_state", lambda c: {
            "todoist_tasks": [], "calendar_events": [],
            "vault_frontmatter": {}, "daily_note_text": None,
        })
        self._write_anchored_vault_config(vault)
        seq = {"sequence": [
            {"id": "Press", "start": "06:00", "end": "07:00", "zone": "any"},
        ]}
        r = client.post(
            "/commit?mode=shadow", headers=_auth(client),
            json={"digest": DIGEST, "sequence": seq, "config": {}},
        )
        assert r.status_code == 422
        assert "does not match" in r.json()["detail"]
        assert "Nothing was written" in r.json()["detail"]

    def test_forged_zone_timing_is_422(self, client, vault, monkeypatch):
        """A valid Trinoor zone ID with forged timing must not be authorized."""
        monkeypatch.setattr(shadow, "gather_live_state", lambda c: {
            "todoist_tasks": [], "calendar_events": [],
            "vault_frontmatter": {}, "daily_note_text": None,
        })
        self._write_anchored_vault_config(vault)
        seq = {"sequence": [
            {"id": "🟡 Trinoor : Morning", "start": "10:00", "end": "11:00",
             "zone": "work_hours", "backdrop": True},
        ]}
        r = client.post(
            "/commit?mode=shadow", headers=_auth(client),
            json={"digest": {"assigned": []}, "sequence": seq, "config": {}},
        )
        assert r.status_code == 422
        assert "Trinoor : Morning" in r.json()["detail"]

    def test_backdrop_on_non_zone_row_is_422(self, client, vault, monkeypatch):
        """A ``backdrop: true`` flag is honored only on server zone rows."""
        monkeypatch.setattr(shadow, "gather_live_state", lambda c: {
            "todoist_tasks": [], "calendar_events": [],
            "vault_frontmatter": {}, "daily_note_text": None,
        })
        self._write_anchored_vault_config(vault)
        seq = {"sequence": [
            {"id": "Garage Buildout", "start": "09:00", "end": "10:00",
             "zone": "any", "backdrop": True},
        ]}
        r = client.post(
            "/commit?mode=shadow", headers=_auth(client),
            json={"digest": DIGEST, "sequence": seq, "config": {}},
        )
        assert r.status_code == 422
        assert "backdrop" in r.json()["detail"]

    def test_non_positive_interval_is_422(self, client, vault, monkeypatch):
        """A sequence row whose end does not follow its start fails closed."""
        monkeypatch.setattr(shadow, "gather_live_state", lambda c: {
            "todoist_tasks": [], "calendar_events": [],
            "vault_frontmatter": {}, "daily_note_text": None,
        })
        bad_seq = {"sequence": [
            {"id": "Garage Buildout", "start": "09:00", "end": "09:00", "zone": "any"},
        ]}
        r = client.post(
            "/commit?mode=shadow", headers=_auth(client),
            json={"digest": DIGEST, "sequence": bad_seq, "config": {}},
        )
        assert r.status_code == 422
        assert "non-positive interval" in r.json()["detail"]

    def test_invalid_hhmm_timing_is_422(self, client, vault, monkeypatch):
        """Missing or unparseable start/end fail closed before manifest build."""
        monkeypatch.setattr(shadow, "gather_live_state", lambda c: {
            "todoist_tasks": [], "calendar_events": [],
            "vault_frontmatter": {}, "daily_note_text": None,
        })
        bad_seq = {"sequence": [
            {"id": "Garage Buildout", "start": "whenever", "end": "10:00", "zone": "any"},
        ]}
        r = client.post(
            "/commit?mode=shadow", headers=_auth(client),
            json={"digest": DIGEST, "sequence": bad_seq, "config": {}},
        )
        assert r.status_code == 422
        assert "not valid HH:MM" in r.json()["detail"]

    def test_duplicate_sequence_id_is_422(self, client, vault, monkeypatch):
        """The same id appearing twice in the sequence fails closed."""
        monkeypatch.setattr(shadow, "gather_live_state", lambda c: {
            "todoist_tasks": [], "calendar_events": [],
            "vault_frontmatter": {}, "daily_note_text": None,
        })
        dup_seq = {"sequence": [
            {"id": "Garage Buildout", "start": "09:00", "end": "10:00", "zone": "any"},
            {"id": "Garage Buildout", "start": "10:00", "end": "11:00", "zone": "any"},
        ]}
        r = client.post(
            "/commit?mode=shadow", headers=_auth(client),
            json={"digest": DIGEST, "sequence": dup_seq, "config": {}},
        )
        assert r.status_code == 422
        assert "more than once" in r.json()["detail"]

    def test_dropped_after_index_write_then_commit_is_422(self, client, vault, monkeypatch):
        """Drop-after-index-write: a Drop recorded after the last /plan-inputs
        refresh must invalidate the cached assigned identity at commit time,
        without any /plan-inputs refresh in between."""
        monkeypatch.setattr(shadow, "gather_live_state", lambda c: {
            "todoist_tasks": [], "calendar_events": [],
            "vault_frontmatter": {}, "daily_note_text": None,
        })
        today = main_mod.gather.effective_date(main_mod.datetime.now())
        identity = "Garage Buildout"  # a row with no source id falls back to its name
        runstate.update_runstate(
            today,
            lambda s: s.setdefault("dropped", []).append(
                {"identity": identity, "name": "Garage Buildout",
                 "dropped_at": "2026-09-05T00:00:00Z"}
            ),
        )
        before = {p: p.read_bytes() for p in vault.rglob("*") if p.is_file()}
        r = client.post(
            "/commit?mode=shadow", headers=_auth(client),
            json={"digest": DIGEST, "sequence": SEQUENCE, "config": {}},
        )
        assert r.status_code == 422
        assert "Garage Buildout" in r.json()["detail"]
        after = {p: p.read_bytes() for p in vault.rglob("*") if p.is_file()}
        assert before == after

    def test_forged_assigned_metadata_sanitized_before_manifest(
            self, client, vault, monkeypatch):
        """Client-supplied non-identity metadata (types, source) must not reach
        build_plan_manifest: routing must fall back to server config, so the
        forged 'adventure' type cannot reroute the item to PHEP."""
        monkeypatch.setattr(shadow, "gather_live_state", lambda c: {
            "todoist_tasks": [], "calendar_events": [],
            "vault_frontmatter": {}, "daily_note_text": None,
        })
        forged_digest = {"assigned": [
            {"name": "Garage Buildout",
             "path": "50 - Operations/Projects/Garage Buildout.md",
             "types": ["adventure"], "blocks": 3, "source": "todoist"},
        ]}
        r = client.post(
            "/commit?mode=shadow", headers=_auth(client),
            json={"digest": forged_digest, "sequence": SEQUENCE, "config": {}},
        )
        assert r.status_code == 200
        rows = [e["manifest"] for e in r.json()["entries"]
                if e["manifest"]["step"] == "A"]
        # Forged types=["adventure"] would route PHEP; sanitized digest must
        # fall back to server config (no presets) -> Inbox.
        assert rows and all(m["routing"] == "Inbox" for m in rows)


class TestPlacementPastSurvivesInManifest:
    """P3-03: an accepted ``placement_past`` anchored row (soft warning the
    user accepted) must survive into the manifest, not be silently filtered
    by the frame rule in build_plan_manifest."""

    def test_in_sequence_pre_anchor_anchored_row_survives(self):
        config = {"anchored_blocks": [{"id": "Press", "Start": "07:00", "Duration": 60}]}
        seq = {"sequence": [{"id": "Press", "start": "06:00", "end": "07:00"}]}
        frame = {"anchor": "09:00", "effective_eod": "17:00"}
        entries = shadow.build_plan_manifest({}, seq, config, time_frame=frame)
        step_e = [e for e in entries if e.step == "E" and e.name == "Press"]
        assert step_e and step_e[0].time == "06:00"

    def test_fallback_loop_still_filters_elapsed_blocks(self):
        """The not-in-sequence fallback loop still obeys the frame rule."""
        config = {"anchored_blocks": [{"id": "Press", "Start": "07:00", "Duration": 60}]}
        frame = {"anchor": "09:00", "effective_eod": "17:00"}
        entries = shadow.build_plan_manifest({}, {"sequence": []}, config,
                                             time_frame=frame)
        assert not [e for e in entries if e.name == "Press"]


class _StubTodoistClient:
    """Context-managed stand-in for TodoistClient; the commit path never calls it."""

    def __init__(self, token):
        self.token = token

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _run_commit_run_live(monkeypatch, tmp_path, digest, sequence) -> dict:
    """Drive ``commit_run.run``'s live commit with every I/O seam stubbed and
    return the intents and run_commit kwargs that the commit writer received."""
    seq_file = tmp_path / "sequence.json"
    seq_file.write_text(json.dumps(sequence), encoding="utf-8")
    captured: dict = {}

    def _run_commit(intents, **kwargs):
        captured["intents"] = intents
        captured["kwargs"] = kwargs
        return []

    monkeypatch.setattr(commit_run.main_mod, "build_digest", lambda *a, **k: digest)
    monkeypatch.setattr(commit_run.shadow, "gather_live_state", lambda config: {
        "todoist_tasks": [], "calendar_events": [],
    })
    monkeypatch.setattr(commit_run.calendar_bridge, "shared_store", lambda: None)
    monkeypatch.setattr(commit_run, "_resolve_calendar_ids",
                        lambda config, store: {"⬜ Blocks": "cal-blocks"})
    monkeypatch.setattr(commit_run.shadow.todoist_client, "load_token", lambda path: "token")
    monkeypatch.setattr(commit_run.shadow.todoist_client, "TodoistClient", _StubTodoistClient)
    monkeypatch.setattr(commit_run.commit, "run_commit", _run_commit)

    assert commit_run.run(str(seq_file), do_commit=True) == 0
    return captured


class TestCapacitiesRowsArePhepMirrorOnly:
    """U4 S6b + PHep mirror (contract change 2026-10-13, revised): a Capacities
    assigned row is exactly ONE Todoist create under the PHEP project (timed
    ``due_datetime`` + duration), and never a ⬜ Blocks calendar event — the
    operator's daily lookahead already renders the mirror, so a calendar block
    duplicated the row. No Step C vault flip either. Classification reads the
    sanitized ``source`` field; a name shared with a Todoist or vault row fails
    closed. The mirror is keyed off the server-built manifest, never a client
    type or project."""

    CAP_ITEM = {
        "name": "Ship project",
        "path": "capacities://space-1/object-1",
        "source": "capacities",
        "identity": "capacities:space-1:RootTask:object-1",
    }
    SEQ = {"sequence": [{"id": "Ship project", "start": "09:00", "end": "10:00", "zone": "any"}]}

    def test_capacities_row_emits_only_phep_mirror(self, monkeypatch, tmp_path):
        entries = shadow.build_plan_manifest({"assigned": [dict(self.CAP_ITEM)]}, self.SEQ, {})
        assert [e.step for e in entries if e.step == "C"] == []
        # V3: no vault rows at all (the Step B daily-note patch is gone).
        assert [e.step for e in entries if e.system == "vault"] == []
        # No ⬜ Blocks calendar event for a Capacities row: the PHEP mirror is
        # the row's single representation.
        assert [e for e in entries if e.system == "calendar"] == []
        todoist = [e for e in entries if e.system == "todoist"]
        assert len(todoist) == 1
        m = todoist[0]
        assert (m.step, m.action, m.name, m.routing) == ("A", "schedule", "Ship project", "PHEP")
        assert m.capacities_mirror is True
        assert m.capacities_identity == "capacities:space-1:RootTask:object-1"
        assert m.capacities_link == "capacities://space-1/object-1"
        assert (m.time, m.duration_min) == ("09:00", 60)
        # Capacities-only plan: one mirror todoist intent, no calendar intent,
        # no vault intent, and no plan body handed to the writers.
        captured = _run_commit_run_live(
            monkeypatch, tmp_path, {"assigned": [dict(self.CAP_ITEM)]}, self.SEQ)
        assert "plan_body" not in captured["kwargs"]
        assert [i.surface for i in captured["intents"]] == ["todoist"]

    def test_capacities_row_yields_only_phep_mirror_while_others_keep_entries(self):
        """Manifest contract: the Capacities row is exactly one PHEP mirror
        with no calendar event, while non-Capacities rows are untouched — a
        Todoist-source row keeps its Step A create and a plain schedulable row
        still gets its ⬜ Blocks calendar event."""
        digest = {"assigned": [
            dict(self.CAP_ITEM),
            {"name": "LOOTS", "path": "todoist://T9", "source": "todoist"},
        ]}
        sequence = {"sequence": [
            {"id": "Ship project", "start": "09:00", "end": "10:00"},
            {"id": "LOOTS", "start": "10:00", "end": "10:30"},
            {"id": "Schedulable", "start": "11:00", "end": "12:00"},
        ]}
        entries = shadow.build_plan_manifest(digest, sequence, {})
        ship = [e for e in entries if e.name == "Ship project"]
        assert len(ship) == 1
        assert (ship[0].system, ship[0].action, ship[0].routing) == (
            "todoist", "schedule", "PHEP")
        assert ship[0].capacities_mirror is True
        assert (ship[0].time, ship[0].duration_min) == ("09:00", 60)
        assert not any(
            e.system == "calendar" and e.name == "Ship project" for e in entries)
        others = [
            (e.system, e.name, e.routing) for e in entries if e.name != "Ship project"
        ]
        assert others == [
            ("todoist", "LOOTS", "Inbox"),
            ("calendar", "Schedulable", "⬜ Blocks"),
        ]

    def test_mixed_plan_keeps_todoist_row_and_writes_no_vault_intent(
            self, monkeypatch, tmp_path):
        """S6b: a mixed plan still plans its Todoist row; the Capacities row
        contributes only its PHEP mirror (no calendar event); nothing targets
        the vault."""
        digest = {"assigned": [
            dict(self.CAP_ITEM),
            {"name": "LOOTS", "path": "todoist://T1", "source": "todoist", "todoist_id": "T1"},
        ]}
        sequence = {"sequence": [
            {"id": "Ship project", "start": "09:00", "end": "10:00", "zone": "any"},
            {"id": "LOOTS", "start": "10:00", "end": "10:30", "zone": "any"},
        ]}
        captured = _run_commit_run_live(monkeypatch, tmp_path, digest, sequence)
        # The Capacities row contributes only its PHEP mirror alongside LOOTS.
        assert [i.name for i in captured["intents"] if i.surface == "todoist"] == [
            "Ship project", "LOOTS"]
        assert [i for i in captured["intents"] if i.surface == "calendar"] == []
        assert [i for i in captured["intents"] if i.surface == "vault"] == []

    def test_capacities_row_with_mint_like_name_stays_phep_mirror_only(self):
        # Name-based calendar routing is irrelevant to a Capacities row: it
        # never gets a calendar event, mint-like name or not, and its mirror
        # stays hard-routed PHEP.
        item = dict(self.CAP_ITEM, name="Minting", path="capacities://space-1/object-2")
        seq = {"sequence": [{"id": "Minting", "start": "09:00", "end": "10:00"}]}
        entries = shadow.build_plan_manifest({"assigned": [item]}, seq, {})
        assert [e for e in entries if e.system == "calendar"] == []
        assert [e.routing for e in entries if e.system == "todoist"] == ["PHEP"]

    def test_capacities_mirror_same_name_live_task_is_a_noop(self):
        # The mirror classifies against live like any other Todoist create:
        # an existing same-content task is a no-op, not a second create.
        item = dict(self.CAP_ITEM, name="Press")
        seq = {"sequence": [{"id": "Press", "start": "12:00", "end": "13:00"}]}
        manifest = shadow.build_plan_manifest({"assigned": [item]}, seq, {})
        live = {"todoist_tasks": [
            {"id": "t1", "content": "Press", "due": {"date": "2026-10-10T12:00:00"}},
        ]}
        diff = shadow.diff_against_live(manifest, live)
        todoist = [e for e in diff.entries if e.manifest.system == "todoist"]
        assert len(todoist) == 1 and todoist[0].classification == shadow.NOOP

    def test_capacities_mirror_same_name_different_time_is_a_noop(self):
        # BLOCKING review finding: a same-name live task at a DIFFERENT time
        # (e.g. an Inbox task due 15:00) must never classify would-update for
        # the mirror lane — the mirror may only CREATE. A live same-content
        # task is a no-op at ANY time or project.
        item = dict(self.CAP_ITEM, name="Cleanups")
        seq = {"sequence": [{"id": "Cleanups", "start": "09:00", "end": "10:00"}]}
        manifest = shadow.build_plan_manifest({"assigned": [item]}, seq, {})
        live = {"todoist_tasks": [
            {"id": "t-user", "content": "Cleanups", "project_id": "inbox",
             "due": {"date": "2026-10-10T15:00:00"}},
        ]}
        diff = shadow.diff_against_live(manifest, live)
        todoist = [e for e in diff.entries if e.manifest.system == "todoist"]
        assert len(todoist) == 1
        assert todoist[0].classification == shadow.NOOP

    def test_capacities_row_without_usable_time_writes_nothing(self):
        # A Capacities row with a missing or zero-length start/end has no place
        # on the timeline: it produces no calendar event and no
        # undated/durationless PHEP mirror — nothing is written for the row.
        for seq in (
            {"sequence": [{"id": "Ship project"}]},
            {"sequence": [{"id": "Ship project", "start": "09:00", "end": "09:00"}]},
        ):
            manifest = shadow.build_plan_manifest(
                {"assigned": [dict(self.CAP_ITEM)]}, seq, {})
            assert manifest == []

    def test_capacities_zero_block_row_gets_no_calendar_and_no_mirror(self):
        item = dict(self.CAP_ITEM, blocks=0)
        entries = shadow.build_plan_manifest({"assigned": [item]}, {"sequence": []}, {})
        assert [e for e in entries if e.system == "calendar"] == []
        assert [e for e in entries if e.system == "todoist"] == []

    def test_forged_client_types_cannot_reroute_the_mirror(self):
        # A client-supplied type/project must never move the mirror off PHEP.
        item = dict(self.CAP_ITEM, types=["adventure"], type="adventure",
                    routing="Inbox", project_id="forged")
        entries = shadow.build_plan_manifest({"assigned": [item]}, self.SEQ, {})
        todoist = [e for e in entries if e.system == "todoist"]
        assert [e.routing for e in todoist] == ["PHEP"]

    def test_name_shared_across_capacities_boundary_fails_closed(self):
        todoist = {"name": "Press", "path": "todoist://t1", "source": "todoist"}
        cap = dict(self.CAP_ITEM, name="Press")
        seq = {"sequence": [{"id": "Press", "start": "12:00", "end": "13:00"}]}
        for assigned in ([todoist, cap], [cap, todoist]):
            with pytest.raises(ValueError, match="Press"):
                shadow.build_plan_manifest({"assigned": assigned}, seq, {})

    def test_capacities_row_plans_only_phep_todoist_create(self):
        manifest = shadow.build_plan_manifest({"assigned": [dict(self.CAP_ITEM)]}, self.SEQ, {})
        live = {"todoist_tasks": [], "calendar_events": []}
        diff = shadow.diff_against_live(manifest, live)
        intents = commit.plan_writes(diff, {"⬜ Blocks": "cal-blocks"}, {}, date(2026, 10, 10))
        todoist = [i for i in intents if i.surface == "todoist"]
        assert len(todoist) == 1
        mirror = todoist[0]
        assert mirror.op == "create" and mirror.task_id is None
        assert mirror.project_id == commit._FALLBACK_PHEP_PROJECT_ID
        assert mirror.capacities_mirror is True
        assert mirror.capacities_identity == "capacities:space-1:RootTask:object-1"
        assert mirror.due_datetime == "2026-10-10T09:00:00"
        assert "capacities://space-1/object-1" in (mirror.description or "")
        assert [i for i in intents if i.surface == "calendar"] == []
        assert [i for i in intents if i.surface == "vault"] == []

    def test_todoist_writer_refuses_capacities_provenance(self):
        m = shadow.ManifestEntry(
            step="A", system="todoist", action="schedule", name="Ship project",
            id_or_path="capacities://space-1/object-1", time="09:00",
            duration_min=60, routing="Inbox", capacities=True,
        )
        diff = shadow.ShadowDiff(entries=[shadow.ShadowDiffEntry(m, shadow.CREATE, {})])
        with pytest.raises(commit.CommitPlanError, match="Capacities"):
            commit.plan_writes(diff, {}, {}, date(2026, 10, 10))


class TestNonCapacitiesManifestUnchanged:
    """Characterization for U4 S6b: Todoist and vault-path assigned rows keep
    their Step A schedule and emit no calendar entry (V3: no Step C flip)."""

    def test_todoist_and_vault_assigned_rows_keep_step_a_only(self):
        digest = {"assigned": [
            {"name": "LOOTS", "path": "todoist://T1", "source": "todoist", "todoist_id": "T1"},
            {"name": "Press", "path": "50 - Operations/Intervals/Press.md"},
        ]}
        seq = {"sequence": [
            {"id": "LOOTS", "start": "09:00", "end": "09:30"},
            {"id": "Press", "start": "09:30", "end": "10:00"},
        ]}
        entries = shadow.build_plan_manifest(digest, seq, {})
        todoist = [(e.step, e.name, e.routing) for e in entries if e.system == "todoist"]
        assert todoist == [("A", "LOOTS", "Inbox"), ("A", "Press", "Inbox")]
        assert all(not e.capacities_mirror for e in entries)
        assert [e.name for e in entries if e.step == "C"] == []
        assert [e.name for e in entries if e.system == "calendar"] == []
