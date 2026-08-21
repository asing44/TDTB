"""Static-UI structural markers.

cockpit-overhaul T15: the five legacy views are redirect stubs into the
cockpit SPA. The wizard source is archived under app/legacy-static/ and is
no longer served at runtime. Endpoint-shape tests (session token, digest
fetch flow) are view-independent and survive unchanged.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))
import main as main_mod  # noqa: E402

POOL_ITEMS = [
    {"name": "Alpha", "path": "50 - Operations/Projects/Alpha.md", "types": ["project"],
     "urgency": "4", "deadline": "2026-07-20", "priority_score": 30, "assigned": False},
]

STATIC_DIR = Path(__file__).parent.parent / "static"
LEGACY_ARCHIVE_DIR = Path(__file__).parent.parent / "legacy-static"
LEGACY_TO_STEP = {
    "index.html": "setup",
    "digest.html": "digest",
    "adjust.html": "adjust",
    "timeline.html": "timeline",
    "commit.html": "commit",
}
STEP_FILES = ["setup.js", "digest.js", "adjust.js", "timeline.js", "commit.js"]
CHIP_STEPS = ["digest.js", "adjust.js", "timeline.js", "commit.js"]  # setup has no chip rows


def _read_static(name: str) -> str:
    return (STATIC_DIR / name).read_text()


def _read_step(name: str) -> str:
    return (LEGACY_ARCHIVE_DIR / "steps" / name).read_text()


def _read_archive(name: str) -> str:
    return (LEGACY_ARCHIVE_DIR / name).read_text()


@pytest.fixture
def vault(tmp_path) -> Path:
    v = tmp_path / "vault-root"
    v.mkdir()
    return v


@pytest.fixture
def client(vault) -> TestClient:
    app = main_mod.create_app(vault_root=vault)
    c = TestClient(app)
    c.app_token = app.state.token
    return c


class TestLegacyRedirectStubs:
    """cockpit-overhaul T15 (locked decision 15): legacy bookmarks land on the
    cockpit. Served 200 so bookmarks work, one-commit revertable, zero leftover
    view logic. No wizard fallback — the wizard is archived, not served."""

    @pytest.mark.parametrize("view,step", sorted(LEGACY_TO_STEP.items()))
    def test_stub_served_and_redirects(self, client, view, step):
        r = client.get(f"/static/{view}")
        assert r.status_code == 200
        assert "text/html" in r.headers["content-type"]
        assert "/static/cockpit/" in r.text
        assert "http-equiv=\"refresh\"" in r.text      # no-JS fallback
        assert "location.replace(" in r.text            # instant path

    @pytest.mark.parametrize("view", sorted(LEGACY_TO_STEP))
    def test_stub_carries_no_view_logic(self, view):
        html = _read_static(view)
        assert len(html) < 2048, f"{view} is not a thin stub"
        for remnant in ("kitFetch", "chipEl(", "fetch('/plan-inputs", "tdtbKit"):
            assert remnant not in html, f"{view} still carries view logic: {remnant}"


class TestLegacyWizardArchived:
    """cockpit-overhaul T15: the wizard SPA is archived under app/legacy-static/
    for rollback/history. It is no longer served at runtime — /static/legacy.html
    returns 404. Archive structural integrity is preserved."""

    def test_legacy_html_not_served(self, client):
        r = client.get("/static/legacy.html")
        assert r.status_code == 404

    def test_archive_legacy_html_exists(self):
        assert (LEGACY_ARCHIVE_DIR / "legacy.html").is_file()

    def test_archive_includes_kit_and_wizard(self):
        html = _read_archive("legacy.html")
        assert "ui_kit.css" in html
        assert "ui_kit.js" in html
        assert "wizard_logic.js" in html
        assert "timeline_logic.js" in html

    def test_archive_loads_all_step_modules(self):
        html = _read_archive("legacy.html")
        for step in STEP_FILES:
            assert f"steps/{step}" in html, f"legacy.html missing steps/{step}"

    def test_archive_step_modules_exist(self):
        for step in STEP_FILES:
            assert (LEGACY_ARCHIVE_DIR / "steps" / step).is_file(), f"steps/{step} missing"

    def test_archive_budget_header_renders_server_ledger(self):
        html = _read_archive("legacy.html")
        assert "/billed-ledger" in html


class TestSessionTokenRoute:
    def test_session_token_tokenless_returns_app_token(self, client):
        r = client.get("/session-token")
        assert r.status_code == 200
        assert r.json() == {"token": client.app_token}

    def test_session_token_not_blocked_by_token_guard(self, client):
        # No X-TDTB-Token header sent — must still succeed (tokenless read).
        r = client.get("/session-token", headers={})
        assert r.status_code == 200


class TestDigestFetchShapeMatchesUI:
    """Confirms the JSON shape the digest step's JS expects from POST /digest
    when authenticated with the token obtained via GET /session-token."""

    def test_ui_flow_token_then_digest(self, client):
        token_resp = client.get("/session-token")
        assert token_resp.status_code == 200
        token = token_resp.json()["token"]

        r = client.post(
            "/digest",
            headers={"X-TDTB-Token": token},
            json={"pool_items": POOL_ITEMS, "assigned_items": [], "today": "2026-07-12"},
        )
        assert r.status_code == 200
        body = r.json()

        assert "valid_date" in body
        assert "assigned_count" in body
        assert isinstance(body["assigned"], list)
        assert isinstance(body["suggested"], list)
        for item in body["suggested"]:
            assert "name" in item
            assert "path" in item

    def test_digest_still_rejects_missing_token(self, client):
        r = client.post("/digest", json={"pool_items": POOL_ITEMS})
        assert r.status_code == 403


class TestStepModuleMarkers:
    """The legacy views' structural pins, retargeted at the step modules."""

    def test_setup_step_markers(self):
        js = _read_step("setup.js")
        # read side + confirm POST + backend-verbatim bar (ui-parity T6,
        # ui-revamp T3 — capacity math stays server-side).
        assert "plan-inputs" in js
        assert "day-setup" in js
        assert "capacity-preview" in js
        assert "/config" in js                     # folded config viewer

    def test_digest_step_markers(self):
        js = _read_step("digest.js")
        # /plan-inputs is the single assembly endpoint (source counts +
        # warnings + capacity + habits) — the loud-degrade surface.
        assert "plan-inputs" in js
        assert "OVERASSIGNED" in js

    @pytest.mark.parametrize("step", CHIP_STEPS)
    def test_step_renders_source_chip(self, step):
        js = _read_step(step)
        assert "chipEl(" in js or "chipHtml(" in js, f"{step} has no chip render call"

    def test_no_durationtoblocks_remnant_anywhere(self):
        # ui-revamp locked decision 2: capacity math lives server-side
        # (/capacity-preview); the old client-side helper must not survive in
        # ANY static file, step modules included.
        for path in STATIC_DIR.rglob("*"):
            if path.suffix not in (".html", ".js"):
                continue
            assert "durationToBlocks" not in path.read_text(), (
                f"durationToBlocks remnant in {path.name}")

    def test_timeline_has_no_billed_auto_fire_on_mount(self):
        # G23 (d8a7013): a fresh timeline mount with no stash must NOT
        # auto-fire the billed /sequence call — it idles until an explicit
        # Propose/Manual click. Pin the idle-mount copy.
        js = _read_step("timeline.js")
        assert "G23" in js
        assert "No plan yet — Propose sequence (1 billed call) or Manual layout." in js

    def test_timeline_totals_label_is_selected_not_placed(self):
        # ui-revamp T7 fix: /capacity-preview's segments.selected counts
        # selections, not canvas placements — label must stay "Selected".
        js = _read_step("timeline.js")
        assert "'Selected: '" in js
        assert "'Placed: '" not in js

    def test_error_fetches_use_kit_error_surface(self):
        # Every step that hand-rolls a fetch must still resolve failures to a
        # visible red status render, never a swallowed catch.
        for step in STEP_FILES:
            js = _read_step(step)
            assert ("status error" in js) or ("renderError" in js) or ("kitFetch" in js), (
                f"steps/{step} has no visible error-status render path")


class TestCockpitBuildBeside:
    """cockpit-overhaul T15 (locked decision 15): production cockpit build is
    served at /static/cockpit/ as the sole runtime UI. Build must be the
    api-adapter-only bundle — no fixture code."""

    COCKPIT_DIR = STATIC_DIR / "cockpit"

    def _bundle_js(self) -> str:
        assets = sorted((self.COCKPIT_DIR / "assets").glob("index-*.js"))
        assert assets, "cockpit build missing — run frontend `npm run build:prod`"
        return assets[-1].read_text()

    def test_cockpit_index_served(self, client):
        r = client.get("/static/cockpit/")
        assert r.status_code == 200
        assert "text/html" in r.headers["content-type"]

    def test_index_references_committed_assets(self, client):
        html = (self.COCKPIT_DIR / "index.html").read_text()
        import re
        refs = re.findall(r'\./(assets/[^"]+)', html)
        assert refs, "cockpit index.html references no built assets"
        for ref in refs:
            assert (self.COCKPIT_DIR / ref).exists(), f"missing built asset {ref}"
            assert client.get(f"/static/cockpit/{ref}").status_code == 200

    def test_bundle_has_no_fixture_adapter(self):
        # vite --mode production sets __FIXTURE__ false; fixture scenarios and
        # the fixture adapter must be tree-shaken out of the committed bundle.
        js = self._bundle_js()
        assert "fixture" not in js.lower()
        assert "__FIXTURE__" not in js

    def test_bundle_no_autofired_billed_calls(self):
        # Same invariant the wizard carries (G23): booting the cockpit must not
        # auto-fire billed endpoints. Bundle may reference the paths (explicit
        # user actions), but boot-path fetches are load/validate/read-only —
        # pinned indirectly: the string markers for the explicit-action gate
        # must survive minification via the API path constants.
        js = self._bundle_js()
        assert "/sequence" in js          # explicit action exists
        assert "/plan-inputs" in js       # read model boots from reads
        assert "/adjust" not in js, "cockpit must carry no /adjust affordance"
