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
import shadow  # noqa: E402


@pytest.fixture
def vault(tmp_path) -> Path:
    v = tmp_path / "vault-root"
    v.mkdir()
    proj_dir = v / "50 - Operations" / "Projects"
    proj_dir.mkdir(parents=True)
    (proj_dir / "Garage Buildout.md").write_text(
        "---\ntype: project\nassigned: false\n---\nbody\n", encoding="utf-8"
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
