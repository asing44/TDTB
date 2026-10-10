"""Route tests for POST /commit?mode=shadow (T13) — shadow.gather_live_state
is monkeypatched so no live Todoist/EventKit calls happen under pytest.
Verifies shadow-default-off backward compat (bare /commit still 501s, per
tests/test_main_api.py's pre-existing contract), the token guard, mode
dispatch, and — the core no-write guarantee — that the vault tree is
untouched after a shadow commit call."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))
import main as main_mod  # noqa: E402
import runstate  # noqa: E402
import commit  # noqa: E402
import shadow  # noqa: E402
from datetime import date  # noqa: E402


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
    config_path.write_text(
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
        v, today, [{"name": "Garage Buildout",
                    "todoist_id": "",
                    "path": "50 - Operations/Projects/Garage Buildout.md",
                    "surface": "assigned"}]
    )
    runstate.write_runstate(
        v, today, runstate.build_runstate({"anchor": "00:00"}),
    )
    return v


@pytest.fixture
def client(vault) -> TestClient:
    app = main_mod.create_app(vault_root=vault)
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
        def fake_gather(config, vault_root):
            return {
                "todoist_tasks": [],
                "calendar_events": [],
                "vault_frontmatter": {
                    "50 - Operations/Projects/Garage Buildout.md": {"assigned": False},
                },
                "daily_note_text": None,
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

        # Step A (todoist create, no live task) + Step C (vault update, assigned False->True)
        # + Step B (patch, no daily note -> conflict). No anchored blocks in
        # this request's config, so no Step D/E rows; recent-selections is a
        # post-commit action, never a manifest row.
        classifications = {e["manifest"]["step"]: e["classification"] for e in body["entries"]}
        assert classifications["A"] == shadow.CREATE
        assert classifications["C"] == shadow.UPDATE
        assert classifications["B"] == shadow.CONFLICT
        assert set(classifications) == {"A", "B", "C"}

        after = {p: p.read_bytes() for p in vault.rglob("*") if p.is_file()}
        assert before == after

    def test_shadow_state_error_becomes_502(self, client, monkeypatch):
        def raise_state_error(config, vault_root):
            raise shadow.ShadowStateError("token file missing")

        monkeypatch.setattr(shadow, "gather_live_state", raise_state_error)

        r = client.post(
            "/commit?mode=shadow",
            headers=_auth(client),
            json={"digest": DIGEST, "sequence": SEQUENCE, "config": {}},
        )
        assert r.status_code == 502
        assert "token file missing" in r.json()["detail"]

    def test_shadow_503_when_vault_root_unconfigured(self, monkeypatch):
        monkeypatch.delenv(main_mod.VAULT_ROOT_ENV, raising=False)
        app = main_mod.create_app()
        c = TestClient(app)
        r = c.post(
            "/commit?mode=shadow",
            headers={"X-TDTB-Token": app.state.token},
            json={"digest": DIGEST, "sequence": SEQUENCE, "config": {}},
        )
        assert r.status_code == 503


class TestCommitEligibility:
    """P3-02: /commit must reject stale/dropped digest & sequence rows with
    422 before any manifest is built, while legitimate rows still pass."""

    def test_stale_assigned_item_is_422(self, client, vault, monkeypatch):
        def fake_gather(config, vault_root):
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

    def test_capacities_rows_are_not_misrouted_into_commit(self, client, vault):
        today = main_mod.gather.effective_date(main_mod.datetime.now())
        runstate.write_digest_index(vault, today, [{
            "name": "Ship project",
            "path": "capacities://space-1/object-1",
            "identity": "capacities:space-1:RootTask:object-1",
            "source": "capacities",
            "capacities_id": "object-1",
            "capacities_space_id": "space-1",
            "capacities_structure_id": "RootTask",
            "capacities_completion_supported": True,
            "source_fingerprint": "fingerprint-1",
            "surface": "assigned",
        }])
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
        assert "plan-only" in response.json()["detail"]
        assert "Ship project" in response.json()["detail"]
        after = {p: p.read_bytes() for p in vault.rglob("*") if p.is_file()}
        assert before == after

    def test_stale_sequence_row_is_422(self, client, vault, monkeypatch):
        def fake_gather(config, vault_root):
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
        def fake_gather(config, vault_root):
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
        def fake_gather(config, vault_root):
            return {
                "todoist_tasks": [], "calendar_events": [],
                "vault_frontmatter": {}, "daily_note_text": None,
            }

        monkeypatch.setattr(shadow, "gather_live_state", fake_gather)
        config_path = vault / "00 - META" / "Skill-Configs" / "tdtb-bridger.md"
        config_path.write_text(
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

        def _fake_state(config, vault_root):
            return {
                "todoist_tasks": [], "calendar_events": [],
                "vault_frontmatter": {}, "daily_note_text": None,
            }

        client.app.state.build_commit_clients = (
            lambda v, cfg: (_WritesRecorder(writes), _FakeStore())
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

    def _write_mixed_surface_index(self, vault: Path) -> None:
        """Index with the fixture's assigned row plus a suggested-only row, so
        a client cannot promote the suggested row by naming it as assigned."""
        today = main_mod.gather.effective_date(main_mod.datetime.now())
        runstate.write_digest_index(
            vault, today,
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
        self._write_mixed_surface_index(vault)
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
        runstate.update_runstate(vault, today, {
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
        runstate.write_digest_index(vault, today, [])
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
        self._write_mixed_surface_index(vault)

        writes = []

        class _WritesRecorder:
            def __init__(self, log):
                self._log = log

        class _FakeStore:
            def calendars(self):
                return []

        client.app.state.build_commit_clients = (
            lambda v, cfg: (_WritesRecorder(writes), _FakeStore())
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
            lambda v, cfg: (_WritesRecorder(writes), _FakeStore())
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


class TestStepCSkipsTodoistItems:
    def test_no_vault_flag_for_todoist_sourced_items(self):
        digest = {"assigned": [
            {"name": "LOOTS", "path": "todoist://T1", "source": "todoist"},
            {"name": "Press", "path": "50 - Operations/Intervals/Press.md"},
        ]}
        seq = {"sequence": [
            {"id": "LOOTS", "start": "09:00", "end": "09:30"},
            {"id": "Press", "start": "09:30", "end": "10:00"},
        ]}
        entries = shadow.build_plan_manifest(digest, seq, {})
        step_c = [e for e in entries if e.step == "C"]
        assert [e.name for e in step_c] == ["Press"]


class TestExactSequenceAuthorization:
    """P3 BLOCK findings: fixed-lane rows (anchored, Trinoor zones) must carry
    exact server timing; backdrop flag only honored on server zone rows;
    malformed timing and duplicates fail closed; assigned metadata must be
    sanitized before manifest construction; dropped items invalidate stale
    cached identities at commit time."""

    def _write_anchored_vault_config(self, vault: Path) -> None:
        config_path = vault / "00 - META" / "Skill-Configs" / "tdtb-bridger.md"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(
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
        monkeypatch.setattr(shadow, "gather_live_state", lambda c, v: {
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
        monkeypatch.setattr(shadow, "gather_live_state", lambda c, v: {
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
        monkeypatch.setattr(shadow, "gather_live_state", lambda c, v: {
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
        monkeypatch.setattr(shadow, "gather_live_state", lambda c, v: {
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
        monkeypatch.setattr(shadow, "gather_live_state", lambda c, v: {
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
        monkeypatch.setattr(shadow, "gather_live_state", lambda c, v: {
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
        monkeypatch.setattr(shadow, "gather_live_state", lambda c, v: {
            "todoist_tasks": [], "calendar_events": [],
            "vault_frontmatter": {}, "daily_note_text": None,
        })
        today = main_mod.gather.effective_date(main_mod.datetime.now())
        identity = "50 - Operations/Projects/Garage Buildout.md"
        runstate.update_runstate(
            vault, today,
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
        monkeypatch.setattr(shadow, "gather_live_state", lambda c, v: {
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


class TestCapacitiesRowsAreCalendarOnly:
    """U4 S6b: a Capacities assigned row is plan-only. Its manifest footprint is
    one calendar create-event on the existing ⬜ Blocks class, with no Todoist
    intent and no Step C vault flip. Classification reads the sanitized
    ``source`` field; a name shared with a Todoist or vault row fails closed."""

    CAP_ITEM = {
        "name": "Ship project",
        "path": "capacities://space-1/object-1",
        "source": "capacities",
        "identity": "capacities:space-1:RootTask:object-1",
    }
    SEQ = {"sequence": [{"id": "Ship project", "start": "09:00", "end": "10:00", "zone": "any"}]}

    def test_capacities_row_emits_only_calendar_entry(self):
        entries = shadow.build_plan_manifest({"assigned": [dict(self.CAP_ITEM)]}, self.SEQ, {})
        assert [e for e in entries if e.system == "todoist"] == []
        assert [e.step for e in entries if e.step == "C"] == []
        # Step B is the commit-level plan section, not a per-row action; its
        # suppression is an open decision (see the S6b report).
        assert [e.step for e in entries if e.system == "vault"] == ["B"]
        calendar = [e for e in entries if e.system == "calendar"]
        assert len(calendar) == 1
        cal = calendar[0]
        assert (cal.step, cal.action, cal.name, cal.id_or_path) == (
            "D", "create-event", "Ship project", "Ship project")
        assert (cal.time, cal.duration_min, cal.routing) == ("09:00", 60, "⬜ Blocks")
        assert cal.capacities is True

    def test_capacities_row_with_mint_like_name_keeps_blocks_class(self):
        item = dict(self.CAP_ITEM, name="Minting", path="capacities://space-1/object-2")
        seq = {"sequence": [{"id": "Minting", "start": "09:00", "end": "10:00"}]}
        cal = [e for e in shadow.build_plan_manifest({"assigned": [item]}, seq, {})
               if e.system == "calendar"]
        assert [e.routing for e in cal] == ["⬜ Blocks"]

    def test_capacities_row_named_like_live_todoist_task_does_not_match_it(self):
        item = dict(self.CAP_ITEM, name="Press")
        seq = {"sequence": [{"id": "Press", "start": "12:00", "end": "13:00"}]}
        manifest = shadow.build_plan_manifest({"assigned": [item]}, seq, {})
        live = {"todoist_tasks": [
            {"id": "t1", "content": "Press", "due": {"date": "2026-10-10T12:00:00"}},
        ]}
        diff = shadow.diff_against_live(manifest, live)
        assert [e for e in diff.entries if e.manifest.system == "todoist"] == []
        assert not any(e.detail.get("task_id") == "t1" for e in diff.entries)

    def test_name_shared_across_capacities_boundary_fails_closed(self):
        todoist = {"name": "Press", "path": "todoist://t1", "source": "todoist"}
        cap = dict(self.CAP_ITEM, name="Press")
        seq = {"sequence": [{"id": "Press", "start": "12:00", "end": "13:00"}]}
        for assigned in ([todoist, cap], [cap, todoist]):
            with pytest.raises(ValueError, match="Press"):
                shadow.build_plan_manifest({"assigned": assigned}, seq, {})

    def test_capacities_row_plans_calendar_create_and_no_todoist_intent(self):
        manifest = shadow.build_plan_manifest({"assigned": [dict(self.CAP_ITEM)]}, self.SEQ, {})
        live = {"todoist_tasks": [], "calendar_events": [], "vault_frontmatter": {},
                "daily_note_text": "# TDTB Plan\n"}
        diff = shadow.diff_against_live(manifest, live)
        intents = commit.plan_writes(diff, {"⬜ Blocks": "cal-blocks"}, {}, date(2026, 10, 10))
        assert [i for i in intents if i.surface == "todoist"] == []
        cal = [i for i in intents if i.surface == "calendar"]
        assert [(i.op, i.calendar_id, i.name) for i in cal] == [("create", "cal-blocks", "Ship project")]
        assert [i.step for i in intents if i.surface == "vault"] == ["B"]

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
    """Characterization for U4 S6b: Todoist and vault assigned rows keep their
    Step A schedule and Step C flip and emit no calendar entry."""

    def test_todoist_and_vault_assigned_rows_keep_steps_a_and_c(self):
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
        assert [e.name for e in entries if e.step == "C"] == ["Press"]
        assert [e.name for e in entries if e.system == "calendar"] == []
