"""Route tests for the local tag-exclusion settings API.

GET /settings/exclusions is tokenless local storage plus an advisory tag
catalog; POST /settings/exclusions/save is token-guarded and full-replacement.
No test here builds a real provider or touches a live source: catalog fakes
are injected through the same application seam /plan-inputs uses.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))

import main as main_mod  # noqa: E402
import exclusion_settings as es  # noqa: E402


SPACE = "space-1"
OTHER_SPACE = "space-2"
TAG_A = "5a25370b-f9a0-40cf-bc3a-0cab4744913c"
TAG_B = "0d194525-c5a1-4af5-bb62-202b83006b5e"


@pytest.fixture
def vault(tmp_path) -> Path:
    root = tmp_path / "vault-root"
    root.mkdir()
    return root


@pytest.fixture
def client(vault) -> TestClient:
    app = main_mod.create_app(vault_root=vault)
    c = TestClient(app)
    c.app_token = app.state.token
    return c


def _auth(client: TestClient) -> dict:
    return {"X-TDTB-Token": client.app_token}


def _entry(tag_id: str = TAG_A, space: str = SPACE, source: str = "capacities") -> dict:
    return {"source": source, "space_id": space, "tag_id": tag_id}


def _body(expected_revision: int = 0, tags: list | None = None, **overrides) -> dict:
    body = {
        "expected_revision": expected_revision,
        "exclusions": {"tags": tags if tags is not None else []},
    }
    body.update(overrides)
    return body


def _settings_bytes(vault: Path) -> bytes:
    return es.settings_path(vault).read_bytes()


class FakeCatalogAdapter:
    def __init__(self, result=None, error=None):
        self._result = result
        self._error = error
        self.closed = False

    def list_tags(self):
        if self._error is not None:
            raise self._error
        return self._result

    def close(self):
        self.closed = True


def _default_catalog() -> dict:
    return {"status": "unconfigured", "space_id": None, "tags": [], "warnings": []}


class TestGet:
    def test_get_is_tokenless_and_reports_the_default(self, client, vault):
        response = client.get("/settings/exclusions")

        assert response.status_code == 200
        body = response.json()
        assert body["persisted"] is False
        assert body["settings"] == {
            "version": 1,
            "revision": 0,
            "exclusions": {"tags": []},
        }
        # No source record → the advisory catalog reports unconfigured and
        # reading never creates the settings file.
        assert body["tag_catalog"] == _default_catalog()
        assert not es.settings_path(vault).exists()

    def test_get_reports_persisted_settings_after_save(self, client, vault):
        saved = client.post(
            "/settings/exclusions/save",
            headers=_auth(client),
            json=_body(tags=[_entry()]),
        )
        assert saved.status_code == 200

        body = client.get("/settings/exclusions").json()
        assert body["persisted"] is True
        assert body["settings"]["revision"] == 1
        assert body["settings"]["exclusions"]["tags"] == [_entry()]

    def test_get_malformed_storage_fails_closed_without_erasing(self, client, vault):
        path = es.settings_path(vault)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json!!", encoding="utf-8")

        response = client.get("/settings/exclusions")

        assert response.status_code == 500
        detail = response.json()["detail"]
        assert detail["code"] == "exclusion_settings_storage_error"
        assert "preserved" in detail["message"]
        assert _settings_bytes(vault) == b"{not json!!"

    def test_get_catalog_lists_root_tags_when_configured(self, client):
        adapter = FakeCatalogAdapter(result={
            "status": "complete",
            "space_id": SPACE,
            "tags": [
                {"id": TAG_A, "title": "habituals"},
                {"id": TAG_B, "title": "chores"},
            ],
            "warnings": [],
        })
        client.app.state.build_capacities_adapter = lambda v, cfg: adapter

        body = client.get("/settings/exclusions").json()

        assert body["tag_catalog"] == {
            "status": "complete",
            "space_id": SPACE,
            "tags": [
                {"id": TAG_A, "title": "habituals"},
                {"id": TAG_B, "title": "chores"},
            ],
            "warnings": [],
        }
        assert adapter.closed is True

    def test_get_catalog_failure_does_not_hide_saved_settings(self, client, vault):
        client.post(
            "/settings/exclusions/save",
            headers=_auth(client),
            json=_body(tags=[_entry()]),
        )

        def boom(v, cfg):
            raise RuntimeError("capacities token file not found")

        client.app.state.build_capacities_adapter = boom

        response = client.get("/settings/exclusions")

        assert response.status_code == 200
        body = response.json()
        assert body["persisted"] is True
        assert body["settings"]["exclusions"]["tags"] == [_entry()]
        assert body["tag_catalog"]["status"] == "unavailable"
        assert any("unavailable" in w for w in body["tag_catalog"]["warnings"])

    def test_get_catalog_adapter_raise_degrades_with_saved_settings_intact(self, client, vault):
        client.post(
            "/settings/exclusions/save",
            headers=_auth(client),
            json=_body(tags=[_entry()]),
        )
        client.app.state.build_capacities_adapter = lambda v, cfg: FakeCatalogAdapter(
            error=RuntimeError("provider down")
        )

        body = client.get("/settings/exclusions").json()

        assert body["tag_catalog"]["status"] == "unavailable"
        assert body["settings"]["exclusions"]["tags"] == [_entry()]

    def test_get_catalog_partial_status_passes_through_with_warnings(self, client):
        adapter = FakeCatalogAdapter(result={
            "status": "partial",
            "space_id": SPACE,
            "tags": [{"id": TAG_A, "title": "habituals"}],
            "warnings": ["tag catalog stopped after 1 page(s) — the listing is incomplete"],
        })
        client.app.state.build_capacities_adapter = lambda v, cfg: adapter

        body = client.get("/settings/exclusions").json()

        assert body["tag_catalog"]["status"] == "partial"
        assert body["tag_catalog"]["warnings"]

    def test_get_unresolved_vault_returns_503(self, monkeypatch):
        monkeypatch.delenv("TDTB_VAULT_ROOT", raising=False)
        app = main_mod.create_app()
        assert TestClient(app).get("/settings/exclusions").status_code == 503


class TestPostAuth:
    def test_post_requires_token(self, client):
        assert client.post("/settings/exclusions/save", json=_body()).status_code == 403

    def test_post_wrong_token_is_rejected(self, client):
        response = client.post(
            "/settings/exclusions/save",
            headers={"X-TDTB-Token": "wrong"},
            json=_body(),
        )
        assert response.status_code == 403


class TestPostPersistence:
    def test_post_persists_and_returns_the_saved_settings(self, client, vault):
        response = client.post(
            "/settings/exclusions/save",
            headers=_auth(client),
            json=_body(tags=[_entry(TAG_B, OTHER_SPACE), _entry(TAG_A)]),
        )

        assert response.status_code == 200
        body = response.json()
        assert body["persisted"] is True
        assert body["settings"] == {
            "version": 1,
            "revision": 1,
            "exclusions": {
                "tags": [
                    _entry(TAG_A, SPACE),
                    _entry(TAG_B, OTHER_SPACE),
                ]
            },
        }
        assert es.settings_path(vault).is_file()

    def test_post_is_a_full_replacement(self, client):
        client.post(
            "/settings/exclusions/save",
            headers=_auth(client),
            json=_body(tags=[_entry(TAG_A)]),
        )
        second = client.post(
            "/settings/exclusions/save",
            headers=_auth(client),
            json=_body(expected_revision=1, tags=[]),
        )

        assert second.status_code == 200
        assert second.json()["settings"]["exclusions"]["tags"] == []
        assert second.json()["settings"]["revision"] == 2

    def test_post_stale_revision_conflicts_and_preserves_bytes(self, client, vault):
        client.post(
            "/settings/exclusions/save",
            headers=_auth(client),
            json=_body(),
        )
        before = _settings_bytes(vault)

        response = client.post(
            "/settings/exclusions/save",
            headers=_auth(client),
            json=_body(expected_revision=0, tags=[_entry()]),
        )

        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "exclusion_settings_conflict"
        assert _settings_bytes(vault) == before

    def test_post_malformed_existing_storage_conflicts_without_erasing(self, client, vault):
        path = es.settings_path(vault)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("garbage", encoding="utf-8")

        response = client.post(
            "/settings/exclusions/save",
            headers=_auth(client),
            json=_body(),
        )

        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "exclusion_settings_storage_error"
        assert _settings_bytes(vault) == b"garbage"

    def test_post_unresolved_vault_returns_503(self, monkeypatch):
        monkeypatch.delenv("TDTB_VAULT_ROOT", raising=False)
        app = main_mod.create_app()
        c = TestClient(app)

        response = c.post(
            "/settings/exclusions/save",
            headers={"X-TDTB-Token": app.state.token},
            json=_body(),
        )

        assert response.status_code == 503


class TestPostValidation:
    @pytest.mark.parametrize(
        "payload",
        [
            _body(extra="x"),                                              # extra top-level
            {k: v for k, v in _body().items() if k != "expected_revision"},
            {k: v for k, v in _body().items() if k != "exclusions"},
            _body(expected_revision=True),                                 # bool revision
            _body(expected_revision=-1),                                   # negative revision
            _body(expected_revision=1.5),                                  # float revision
            {"expected_revision": 0, "exclusions": {"tags": [], "labels": []}},  # unknown dimension
            {"expected_revision": 0, "exclusions": {"tags": {}}},          # tags not a list
            {"expected_revision": 0, "exclusions": {"tags": ["habituals"]}},  # title, not identity
            _body(tags=[{"space_id": SPACE, "tag_id": TAG_A}]),            # missing source
            _body(tags=[_entry(source="todoist")]),                        # unsupported source
            _body(tags=[_entry(tag_id="not-a-uuid")]),                     # non-UUID
            _body(tags=[_entry(tag_id=TAG_A.upper())]),                    # non-canonical UUID
            _body(tags=[_entry(space="")]),                                # empty space
            _body(tags=[_entry(space=f" {SPACE}")]),                       # whitespace alias
            _body(tags=[_entry(), _entry()]),                              # duplicate identity
            _body(tags=[{**_entry(), "title": "habituals"}]),              # extra entry key
        ],
    )
    def test_malformed_requests_are_rejected_422(self, client, vault, payload):
        response = client.post(
            "/settings/exclusions/save",
            headers=_auth(client),
            json=payload,
        )

        assert response.status_code == 422
        assert not es.settings_path(vault).exists()

    def test_duplicate_request_keys_collapse_to_one_validated_identity(self, client, vault):
        # A duplicate JSON key in the request collapses to one key at parse
        # time; the server still validates the surviving value strictly. This
        # documents that the storage-level duplicate rejection is the strong
        # guarantee (see test_exclusion_settings).
        raw = (
            '{"expected_revision": 0, "exclusions": {"tags": ['
            '{"source": "capacities", "space_id": "space-1", '
            f'"tag_id": "{TAG_A}", "tag_id": "{TAG_A}"'
            "}]}}"
        )
        response = client.post(
            "/settings/exclusions/save",
            headers={**_auth(client), "Content-Type": "application/json"},
            content=raw,
        )

        assert response.status_code == 200
        assert response.json()["settings"]["exclusions"]["tags"] == [_entry(TAG_A)]
