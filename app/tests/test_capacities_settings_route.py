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
import capacities_builder as cb  # noqa: E402


SPACE = "space-1"
NATIVE = f"capacities:{SPACE}:RootTask:task-1"
CUSTOM = f"capacities:{SPACE}:custom-project:project-1"

PROJECT_STRUCTURE = "0d194525-c5a1-4af5-bb62-202b83006b5e"
PRESS_STRUCTURE = "6aa7b02a-4315-47d1-9cfb-0c0cdac0950c"
PROJECT_ASSIGNED_PROPERTY = "f779f78a-3b1e-4a2c-9f5d-6a0f0e1d2c3b"
ADVENTURE_ASSIGNED_PROPERTY = "c19f9b95-7c2d-4e18-8a51-9b0c1d2e3f40"


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


def _write_source_record(vault: Path, structure_ids: list[str]) -> Path:
    """Write a minimal valid Capacities source-mapping record (vault-local)."""
    path = cb.source_path(vault)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({
            "version": 1,
            "revision": 0,
            "space_id": SPACE,
            "structures": [{"structure_id": sid} for sid in structure_ids],
        }),
        encoding="utf-8",
    )
    return path


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
            "assigned_structures": {},
            "native_task_structures": ["RootTask", "Task"],
            "active_statuses": ["active"],
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

    def test_get_reports_no_available_structures_when_unconfigured(self, client, vault):
        body = client.get("/settings/capacities").json()

        assert body["available_structures"] == []
        # Advisory only: reading the settings route never creates a record.
        assert not cb.source_path(vault).exists()

    def test_get_reports_configured_structures_sorted(self, client, vault):
        _write_source_record(vault, ["b-structure", "a-structure"])

        body = client.get("/settings/capacities").json()

        assert body["available_structures"] == ["a-structure", "b-structure"]
        # The source record and the settings policy are independent stores.
        assert body["persisted"] is False
        assert body["settings"]["revision"] == 0

    def test_get_malformed_source_record_degrades_to_empty(self, client, vault):
        path = cb.source_path(vault)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json!!", encoding="utf-8")

        response = client.get("/settings/capacities")

        # The advisory list fails soft; the settings route itself still works
        # and the malformed source bytes are left untouched for the builder to
        # report loudly when an adapter is next built.
        assert response.status_code == 200
        body = response.json()
        assert body["available_structures"] == []
        assert body["settings"]["version"] == 1
        assert path.read_bytes() == b"{not json!!"


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
        # The omitted additive admission inputs are written as the documented
        # defaults by the full-replacement save.
        assert body["settings"]["native_task_structures"] == ["RootTask", "Task"]
        assert body["settings"]["active_statuses"] == ["active"]
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


class TestAdmissionInputs:
    """POST/GET round-trip for the two additive admission inputs.

    The save stays a full replacement: sending both lists replaces both, and
    omitting one falls back to its documented default (the built-in native
    task structures / the single ``active`` status) rather than to an empty
    set, exactly as ``capacities_settings.save_settings`` documents. An
    explicit empty list is a legitimate replacement (nothing native / no
    status satisfies the condition)."""

    def test_post_persists_and_get_round_trips_both_inputs(self, client, vault):
        response = client.post(
            "/settings/capacities/save",
            headers=_auth(client),
            json=_body(
                native_task_structures=["Task", "custom-project", "RootTask"],
                active_statuses=["active", "In Progress"],
            ),
        )

        assert response.status_code == 200
        expected_structures = ["RootTask", "Task", "custom-project"]
        expected_statuses = ["In Progress", "active"]
        settings = response.json()["settings"]
        assert settings["native_task_structures"] == expected_structures
        assert settings["active_statuses"] == expected_statuses

        body = client.get("/settings/capacities").json()
        assert body["persisted"] is True
        assert body["settings"]["native_task_structures"] == expected_structures
        assert body["settings"]["active_statuses"] == expected_statuses

        # The persisted version-1 bytes carry the same additive keys.
        stored = json.loads(_settings_bytes(vault).decode("utf-8"))
        assert stored["native_task_structures"] == expected_structures
        assert stored["active_statuses"] == expected_statuses

    def test_empty_lists_are_a_legitimate_full_replacement(self, client, vault):
        response = client.post(
            "/settings/capacities/save",
            headers=_auth(client),
            json=_body(native_task_structures=[], active_statuses=[]),
        )

        assert response.status_code == 200
        settings = response.json()["settings"]
        assert settings["native_task_structures"] == []
        assert settings["active_statuses"] == []
        read_back = client.get("/settings/capacities").json()["settings"]
        assert read_back["native_task_structures"] == []
        assert read_back["active_statuses"] == []

    def test_omitting_each_input_writes_its_documented_default(self, client, vault):
        first = client.post(
            "/settings/capacities/save",
            headers=_auth(client),
            json=_body(native_task_structures=["custom-project"]),
        )

        assert first.status_code == 200
        # ``active_statuses`` was omitted -> the documented default, not []
        # (the full-replacement contract, same as an omitted
        # ``active_structures``).
        assert first.json()["settings"]["active_statuses"] == ["active"]

        second = client.post(
            "/settings/capacities/save",
            headers=_auth(client),
            json=_body(expected_revision=1, active_statuses=["In Progress"]),
        )

        assert second.status_code == 200
        # ``native_task_structures`` was omitted -> the documented default.
        assert second.json()["settings"]["native_task_structures"] == [
            "RootTask",
            "Task",
        ]
        assert second.json()["settings"]["active_statuses"] == ["In Progress"]

    def test_stale_revision_with_admission_inputs_still_conflicts(self, client, vault):
        first = client.post(
            "/settings/capacities/save",
            headers=_auth(client),
            json=_body(
                native_task_structures=["custom-project"],
                active_statuses=["In Progress"],
            ),
        )
        assert first.status_code == 200
        before = _settings_bytes(vault)

        response = client.post(
            "/settings/capacities/save",
            headers=_auth(client),
            json=_body(
                expected_revision=0,
                native_task_structures=["other-project"],
                active_statuses=["active"],
            ),
        )

        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "capacities_settings_conflict"
        assert _settings_bytes(vault) == before

    def test_malformed_admission_input_preserves_existing_bytes(self, client, vault):
        first = client.post(
            "/settings/capacities/save",
            headers=_auth(client),
            json=_body(native_task_structures=["custom-project"]),
        )
        assert first.status_code == 200
        before = _settings_bytes(vault)

        response = client.post(
            "/settings/capacities/save",
            headers=_auth(client),
            json=_body(
                expected_revision=1,
                active_statuses=["In Progress", "In Progress"],
            ),
        )

        assert response.status_code == 422
        assert any(
            "active_statuses" in entry["loc"]
            for entry in response.json()["detail"]
        )
        assert _settings_bytes(vault) == before

    @pytest.mark.parametrize(
        "field,bad",
        [
            ("native_task_structures", "RootTask"),               # bare string
            ("native_task_structures", {"RootTask": True}),       # object
            ("native_task_structures", None),                     # null
            ("native_task_structures", [None]),                   # null entry
            ("native_task_structures", [1]),                      # number entry
            ("native_task_structures", [True]),                   # bool entry
            ("native_task_structures", [""]),                     # empty entry
            ("native_task_structures", ["RootTask", "RootTask"]),  # duplicate
            ("active_statuses", "active"),                        # bare string
            ("active_statuses", {"active": True}),                # object
            ("active_statuses", None),                            # null
            ("active_statuses", [None]),                          # null entry
            ("active_statuses", [1]),                             # number entry
            ("active_statuses", [True]),                          # bool entry
            ("active_statuses", [""]),                            # empty entry
            ("active_statuses", ["active", "active"]),            # duplicate
        ],
    )
    def test_post_rejects_malformed_admission_inputs_422(
        self, client, vault, field, bad
    ):
        response = client.post(
            "/settings/capacities/save",
            headers=_auth(client),
            json=_body(**{field: bad}),
        )

        assert response.status_code == 422
        assert any(field in entry["loc"] for entry in response.json()["detail"])
        assert not cs.settings_path(vault).exists()


class TestAssignedStructuresRoute:
    """POST/GET round-trip for the additive ``assigned_structures`` key.

    The declaration must survive the route in BOTH directions — a store change
    the route cannot carry is not shipped — and omission stays a full
    replacement to the empty default (no override anywhere, so the mapping's
    own ``assignment_property`` applies)."""

    def test_get_returns_the_key_and_post_round_trips_it(self, client, vault):
        declarations = {
            PROJECT_STRUCTURE: PROJECT_ASSIGNED_PROPERTY,
            PRESS_STRUCTURE: ADVENTURE_ASSIGNED_PROPERTY,
        }
        response = client.post(
            "/settings/capacities/save",
            headers=_auth(client),
            json=_body(assigned_structures=declarations),
        )

        assert response.status_code == 200
        assert response.json()["settings"]["assigned_structures"] == declarations

        body = client.get("/settings/capacities").json()
        assert body["settings"]["assigned_structures"] == declarations

        # The persisted version-1 bytes carry the same additive key.
        stored = json.loads(_settings_bytes(vault).decode("utf-8"))
        assert stored["assigned_structures"] == declarations

    def test_omitting_the_key_is_a_full_replacement_to_empty(self, client, vault):
        first = client.post(
            "/settings/capacities/save",
            headers=_auth(client),
            json=_body(
                assigned_structures={
                    PROJECT_STRUCTURE: PROJECT_ASSIGNED_PROPERTY
                }
            ),
        )
        assert first.status_code == 200

        second = client.post(
            "/settings/capacities/save",
            headers=_auth(client),
            json=_body(expected_revision=1),
        )

        assert second.status_code == 200
        assert second.json()["settings"]["assigned_structures"] == {}
        assert (
            client.get("/settings/capacities").json()["settings"][
                "assigned_structures"
            ]
            == {}
        )

    def test_empty_object_is_a_legitimate_replacement(self, client, vault):
        response = client.post(
            "/settings/capacities/save",
            headers=_auth(client),
            json=_body(assigned_structures={}),
        )

        assert response.status_code == 200
        assert response.json()["settings"]["assigned_structures"] == {}

    @pytest.mark.parametrize(
        "bad",
        [
            [],                                                     # list
            "custom-project",                                       # string
            None,                                                   # null
            {PROJECT_STRUCTURE: False},                             # flag
            {PROJECT_STRUCTURE: 1},                                 # int value
            {PROJECT_STRUCTURE: None},                              # null value
            {PROJECT_STRUCTURE: ""},                                # empty property id
            {"": PROJECT_ASSIGNED_PROPERTY},                        # empty structure id
            {" custom-project ": PROJECT_ASSIGNED_PROPERTY},        # whitespace alias
        ],
    )
    def test_post_rejects_malformed_declarations_422(self, client, vault, bad):
        response = client.post(
            "/settings/capacities/save",
            headers=_auth(client),
            json=_body(assigned_structures=bad),
        )

        assert response.status_code == 422
        assert any(
            "assigned_structures" in entry["loc"]
            for entry in response.json()["detail"]
        )
        assert not cs.settings_path(vault).exists()

    def test_malformed_declaration_preserves_existing_bytes(self, client, vault):
        first = client.post(
            "/settings/capacities/save",
            headers=_auth(client),
            json=_body(
                assigned_structures={
                    PROJECT_STRUCTURE: PROJECT_ASSIGNED_PROPERTY
                }
            ),
        )
        assert first.status_code == 200
        before = _settings_bytes(vault)

        response = client.post(
            "/settings/capacities/save",
            headers=_auth(client),
            json=_body(
                expected_revision=1,
                assigned_structures={PROJECT_STRUCTURE: ""},
            ),
        )

        assert response.status_code == 422
        assert _settings_bytes(vault) == before
