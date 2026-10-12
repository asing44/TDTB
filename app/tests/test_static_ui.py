"""Static-UI structural markers.

cockpit-overhaul T15: the five legacy views are redirect stubs into the
cockpit SPA. The wizard source was deleted (vault removal V5b) and is not
served at runtime. Endpoint-shape tests (session token, digest
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
LEGACY_TO_STEP = {
    "index.html": "setup",
    "digest.html": "digest",
    "adjust.html": "adjust",
    "timeline.html": "timeline",
    "commit.html": "commit",
}


def _read_static(name: str) -> str:
    return (STATIC_DIR / name).read_text()


@pytest.fixture
def client() -> TestClient:
    app = main_mod.create_app()
    c = TestClient(app)
    c.app_token = app.state.token
    return c


class TestLegacyRedirectStubs:
    """cockpit-overhaul T15 (locked decision 15): legacy bookmarks land on the
    cockpit. Served 200 so bookmarks work, one-commit revertable, zero leftover
    view logic. No wizard fallback — the wizard is deleted."""

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


class TestLegacyWizardRemoved:
    """The wizard SPA (formerly archived under app/legacy-static/) is deleted;
    /static/legacy.html returns 404."""

    def test_legacy_html_not_served(self, client):
        r = client.get("/static/legacy.html")
        assert r.status_code == 404

    def test_legacy_archive_dir_is_gone(self):
        assert not (Path(__file__).parent.parent / "legacy-static").exists()


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


class TestStaticRemnants:
    """Structural pins over the files that remain under static/."""

    def test_no_durationtoblocks_remnant_anywhere(self):
        # ui-revamp locked decision 2: capacity math lives server-side
        # (/capacity-preview); the old client-side helper must not survive in
        # ANY static file, step modules included.
        for path in STATIC_DIR.rglob("*"):
            if path.suffix not in (".html", ".js"):
                continue
            assert "durationToBlocks" not in path.read_text(), (
                f"durationToBlocks remnant in {path.name}")


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
