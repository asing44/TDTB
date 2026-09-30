"""Route tests for the local Capacities settings API.

GET /settings/capacities is tokenless local storage only; POST
/settings/capacities/save is token-guarded and full-replacement. No test here
builds an adapter, calls a provider, or touches a live source.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))

import main as main_mod  # noqa: E402
import capacities_settings as cs  # noqa: E402


SPACE = "space-1"
NATIVE = f"capacities:{SPACE}:RootTask:task-1"
CUSTOM = f"capacities:{SPACE}:custom-project:project-1"


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


def _body(expected_revision: int = 0, **overrides) -> dict:
    body = {
        "expected_revision": expected_revision,
        "native_task_auto": {
            "active_enabled": True,
            "due_enabled": True,
            "deadline_enabled": True,
            "deadline_horizon_days": 2,
        },
        "excluded": {},
    }
    body.update(overrides)
    return body


def _settings_bytes(vault: Path) -> bytes:
    return cs.settings_path(vault).read_bytes()


class TestGet:
    def test_get_is_tokenless_and_reports_the_default(self, client, vault):
        response = client.get("/settings/capacities")

        assert response.status_code == 200
        body = response.json()
        assert body["persisted"] is False
        assert body["settings"] == {
            "version": 1,
            "revision": 0,
            "native_task_auto": {
                "active_enabled": True,
                "due_enabled": True,
                "deadline_enabled": True,
                "deadline_horizon_days": 2,
            },
            "excluded": {},
            "active_structures": {},
        }
        assert not cs.settings_path(vault).exists()

    def test_get_reports_persisted_settings_after_save(self, client, vault):
        saved = client.post(
            "/settings/capacities/save",
            headers=_auth(client),
            json=_body(excluded={NATIVE: True}),
        )
        assert saved.status_code == 200

        body = client.get("/settings/capacities").json()
        assert body["persisted"] is True
        assert body["settings"]["revision"] == 1
        assert body["settings"]["excluded"] == {NATIVE: True}

    def test_get_malformed_storage_fails_closed_without_erasing(self, client, vault):
        path = cs.settings_path(vault)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json!!", encoding="utf-8")

        response = client.get("/settings/capacities")

        assert response.status_code == 500
        detail = response.json()["detail"]
        assert detail["code"] == "capacities_settings_storage_error"
        assert "preserved" in detail["message"]
        assert _settings_bytes(vault) == b"{not json!!"


class TestPostAuth:
    def test_post_requires_token(self, client):
        assert client.post("/settings/capacities/save", json=_body()).status_code == 403

    def test_post_wrong_token_is_rejected(self, client):
        response = client.post(
            "/settings/capacities/save",
            headers={"X-TDTB-Token": "wrong"},
            json=_body(),
        )
        assert response.status_code == 403


class TestPostPersistence:
    def test_post_persists_and_returns_the_saved_settings(self, client, vault):
        response = client.post(
            "/settings/capacities/save",
            headers=_auth(client),
            json=_body(
                native_task_auto={
                    "active_enabled": False,
                    "due_enabled": True,
                    "deadline_enabled": False,
                    "deadline_horizon_days": 4,
                },
                excluded={NATIVE: True, CUSTOM: True},
            ),
        )

        assert response.status_code == 200
        body = response.json()
        assert body["persisted"] is True
        assert body["settings"]["version"] == 1
        assert body["settings"]["revision"] == 1
        assert body["settings"]["native_task_auto"]["active_enabled"] is False
        assert body["settings"]["native_task_auto"]["deadline_horizon_days"] == 4
        assert body["settings"]["excluded"] == {NATIVE: True, CUSTOM: True}
        assert cs.settings_path(vault).is_file()

    def test_post_is_a_full_replacement(self, client, vault):
        client.post(
            "/settings/capacities/save",
            headers=_auth(client),
            json=_body(excluded={NATIVE: True}),
        )
        second = client.post(
            "/settings/capacities/save",
            headers=_auth(client),
            json=_body(expected_revision=1, excluded={}),
        )

        assert second.status_code == 200
        assert second.json()["settings"]["excluded"] == {}
        assert second.json()["settings"]["revision"] == 2

    def test_post_stale_revision_conflicts_and_preserves_bytes(self, client, vault):
        client.post(
            "/settings/capacities/save",
            headers=_auth(client),
            json=_body(),
        )
        before = _settings_bytes(vault)

        response = client.post(
            "/settings/capacities/save",
            headers=_auth(client),
            json=_body(expected_revision=0, excluded={NATIVE: True}),
        )

        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "capacities_settings_conflict"
        assert _settings_bytes(vault) == before

    def test_post_malformed_existing_storage_conflicts_without_erasing(self, client, vault):
        path = cs.settings_path(vault)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("garbage", encoding="utf-8")

        response = client.post(
            "/settings/capacities/save",
            headers=_auth(client),
            json=_body(),
        )

        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "capacities_settings_storage_error"
        assert _settings_bytes(vault) == b"garbage"

    def test_post_unresolved_vault_returns_503(self, monkeypatch):
        monkeypatch.delenv("TDTB_VAULT_ROOT", raising=False)
        app = main_mod.create_app()
        c = TestClient(app)

        response = c.post(
            "/settings/capacities/save",
            headers={"X-TDTB-Token": app.state.token},
            json=_body(),
        )

        assert response.status_code == 503

    def test_get_unresolved_vault_returns_503(self, monkeypatch):
        monkeypatch.delenv("TDTB_VAULT_ROOT", raising=False)
        app = main_mod.create_app()
        assert TestClient(app).get("/settings/capacities").status_code == 503


class TestPostValidation:
    @pytest.mark.parametrize(
        "payload",
        [
            _body(extra="x"),                                             # extra top-level
            {k: v for k, v in _body().items() if k != "expected_revision"},
            _body(expected_revision=True),                                # bool revision
            _body(expected_revision=-1),                                  # negative revision
            _body(expected_revision=1.5),                                 # float revision
            _body(native_task_auto={"active_enabled": True}),             # missing native keys
            _body(native_task_auto={
                "active_enabled": True, "due_enabled": True,
                "deadline_enabled": True, "deadline_horizon_days": 2, "extra": 1,
            }),                                                           # extra native key
            _body(native_task_auto={
                "active_enabled": 1, "due_enabled": True,
                "deadline_enabled": True, "deadline_horizon_days": 2,
            }),                                                           # int bool
            _body(native_task_auto={
                "active_enabled": True, "due_enabled": True,
                "deadline_enabled": True, "deadline_horizon_days": True,
            }),                                                           # bool horizon
            _body(native_task_auto={
                "active_enabled": True, "due_enabled": True,
                "deadline_enabled": True, "deadline_horizon_days": -1,
            }),                                                           # negative horizon
            _body(native_task_auto={
                "active_enabled": True, "due_enabled": True,
                "deadline_enabled": True, "deadline_horizon_days": 2.5,
            }),                                                           # float horizon
            _body(excluded={"task-1": True}),                             # bare id
            _body(excluded={"Write brief": True}),                        # title
            _body(excluded={f"  {NATIVE}  ": True}),                      # whitespace alias
            _body(excluded={NATIVE: False}),                              # flag false
            _body(excluded={NATIVE: 1}),                                  # flag int
        ],
    )
    def test_malformed_requests_are_rejected_422(self, client, vault, payload):
        response = client.post(
            "/settings/capacities/save",
            headers=_auth(client),
            json=payload,
        )

        assert response.status_code == 422
        assert not cs.settings_path(vault).exists()

    def test_duplicate_request_keys_are_not_silently_accepted_as_two_identities(
        self, client, vault
    ):
        # A duplicate JSON key in the request collapses to one key at parse
        # time; the server still validates the surviving value strictly. This
        # documents that the storage-level duplicate rejection is the strong
        # guarantee (see test_capacities_settings).
        raw = (
            '{"expected_revision": 0, '
            '"native_task_auto": {"active_enabled": true, "due_enabled": true, '
            '"deadline_enabled": true, "deadline_horizon_days": 2}, '
            f'"excluded": {{"{NATIVE}": true, "{NATIVE}": true}}}}'
        )
        response = client.post(
            "/settings/capacities/save",
            headers={**_auth(client), "Content-Type": "application/json"},
            content=raw,
        )

        assert response.status_code == 200
        assert response.json()["settings"]["excluded"] == {NATIVE: True}


class TestNoProviderCalls:
    def _poison_adapter(self, client):
        def boom(*_args, **_kwargs):
            raise AssertionError("settings routes must not build an adapter")

        client.app.state.build_capacities_adapter = boom

    def test_get_never_builds_an_adapter_or_provider(
        self, client, vault, monkeypatch
    ):
        self._poison_adapter(client)

        def boom(*_args, **_kwargs):
            raise AssertionError("settings routes must not call a provider")

        monkeypatch.setattr(main_mod.external_sources, "fetch_capacities_items", boom)

        assert client.get("/settings/capacities").status_code == 200

    def test_post_never_builds_an_adapter_or_provider(
        self, client, vault, monkeypatch
    ):
        self._poison_adapter(client)

        def boom(*_args, **_kwargs):
            raise AssertionError("settings routes must not call a provider")

        monkeypatch.setattr(main_mod.external_sources, "fetch_capacities_items", boom)

        response = client.post(
            "/settings/capacities/save",
            headers=_auth(client),
            json=_body(excluded={NATIVE: True}),
        )

        assert response.status_code == 200
        data = json.loads(_settings_bytes(vault).decode("utf-8"))
        assert data["excluded"] == {NATIVE: True}


PROJECT_STRUCTURE = "0d194525-c5a1-4af5-bb62-202b83006b5e"


class TestActiveStructures:
    def test_post_persists_and_get_reports_active_structures(self, client, vault):
        response = client.post(
            "/settings/capacities/save",
            headers=_auth(client),
            json=_body(active_structures={"custom-project": True, PROJECT_STRUCTURE: True}),
        )

        assert response.status_code == 200
        assert response.json()["settings"]["active_structures"] == {
            "custom-project": True,
            PROJECT_STRUCTURE: True,
        }
        body = client.get("/settings/capacities").json()
        assert body["settings"]["active_structures"] == {
            "custom-project": True,
            PROJECT_STRUCTURE: True,
        }

    def test_omitting_active_structures_is_a_full_replacement_to_empty(
        self, client, vault
    ):
        client.post(
            "/settings/capacities/save",
            headers=_auth(client),
            json=_body(active_structures={"custom-project": True}),
        )
        second = client.post(
            "/settings/capacities/save",
            headers=_auth(client),
            json=_body(expected_revision=1),
        )

        assert second.status_code == 200
        assert second.json()["settings"]["active_structures"] == {}

    @pytest.mark.parametrize(
        "bad",
        [
            {"custom-project": False},
            {"custom-project": 1},
            {"custom-project": None},
            {"": True},
            {" custom-project ": True},
            {"custom project": True},
            [],
            "custom-project",
        ],
    )
    def test_post_rejects_malformed_active_structures_422(self, client, vault, bad):
        response = client.post(
            "/settings/capacities/save",
            headers=_auth(client),
            json=_body(active_structures=bad),
        )

        assert response.status_code == 422
        assert not cs.settings_path(vault).exists()
