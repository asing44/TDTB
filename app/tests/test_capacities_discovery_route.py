"""Route tests for POST /settings/capacities/source/discover.

Discovery is token-guarded and strictly read-only: it answers the pinned
catalog shape sorted by structure id, maps each failure class onto its pinned
status and code, and never touches the vault-local mapping record. The
provider is always faked — no test reads a real credential or opens a network
connection.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))

import main as main_mod  # noqa: E402
import capacities_adapter as ca  # noqa: E402
import capacities_builder as cb  # noqa: E402


SPACE = "space-1"
DISCOVER = "/settings/capacities/source/discover"
SAVE = "/settings/capacities/source/save"

VALIDATION_ERROR = {
    "code": "capacities_source_validation_error",
    "message": "Capacities source mapping payload is invalid.",
}
CREDENTIAL_ERROR = {
    "code": "capacities_discovery_credentials_unavailable",
    "message": "Capacities credential is unavailable; discovery cannot run.",
}
RATE_LIMIT_ERROR = {
    "code": "capacities_discovery_rate_limited",
    "message": "Capacities rate limit exceeded; retry discovery shortly.",
}
PROVIDER_ERROR = {
    "code": "capacities_discovery_failed",
    "message": "Capacities discovery failed before a catalog was read.",
}

#: One structures payload, deliberately out of id order, exercising the
#: title fallbacks: ``alpha`` has no title and ``done`` has no display name.
STRUCTURES_PAYLOAD = [
    {
        "id": "zeta",
        "title": "Zeta",
        "propertyDefinitions": [
            {
                "id": "zeta-status",
                "name": "Status",
                "type": "label",
                "writable": True,
                "labelSet": [
                    {"id": "open", "name": "Open"},
                    {"id": "done"},
                ],
            },
            {"id": "zeta-due", "name": "Due", "type": "date"},
        ],
    },
    {"id": "alpha", "propertyDefinitions": []},
]

EXPECTED_CATALOG = [
    {"structure_id": "alpha", "title": "alpha", "properties": []},
    {
        "structure_id": "zeta",
        "title": "Zeta",
        "properties": [
            {
                "property_id": "zeta-status",
                "title": "Status",
                "type": "label",
                "writable": True,
                "label_options": [
                    {"id": "open", "title": "Open"},
                    {"id": "done", "title": "done"},
                ],
            },
            {
                "property_id": "zeta-due",
                "title": "Due",
                "type": "date",
                "writable": False,
                "label_options": [],
            },
        ],
    },
]


class _StructuresClient:
    """Fake REST client serving one structures payload to the real builder."""

    last: "_StructuresClient | None" = None

    def __init__(
        self,
        token,
        *,
        space_id,
        base_url,
        timeout,
        transport,
        structures_observer=None,
    ):
        self.token = token
        self.space_id = space_id
        self.structures_observer = structures_observer
        self.closed = False
        self.fetch_calls = 0
        _StructuresClient.last = self

    def fetch_structures(self, observer=None):
        self.fetch_calls += 1
        return {"structures": STRUCTURES_PAYLOAD}

    def close(self):
        self.closed = True


@pytest.fixture
def vault(tmp_path) -> Path:
    root = tmp_path / "vault-root"
    root.mkdir()
    return root


@pytest.fixture
def app(vault):
    return main_mod.create_app(vault_root=vault)


@pytest.fixture
def client(app) -> TestClient:
    c = TestClient(app)
    c.app_token = app.state.token
    return c


def _auth(client: TestClient) -> dict:
    return {"X-TDTB-Token": client.app_token}


def _install_real_discovery(monkeypatch):
    """Bind the route to the real builder over a fake provider + credential."""
    monkeypatch.setattr(cb, "load_capacities_token", lambda path=None: "fake")
    monkeypatch.setattr(cb, "CapacitiesRestClient", _StructuresClient)


def _install_discovery(monkeypatch, catalog=(), error=None):
    """Replace the discovery seam; returns the recorded (vault, space) calls."""
    calls: list[tuple[Path, str]] = []

    def fake_discover(vault_root, space_id, config=None):
        calls.append((Path(vault_root), space_id))
        if error is not None:
            raise error
        return catalog

    monkeypatch.setattr(cb, "discover_capacities_source", fake_discover)
    return calls


def _mapping_structure(**overrides) -> dict:
    row = {
        "structure_id": "RootTask",
        "title_property": "title",
        "status_property": "status",
        "open_status_values": ["open"],
        "date_property": "date",
        "deadline_property": None,
        "duration_property": None,
        "assignment_property": None,
        "assignment_values": [],
        "completion_property": None,
        "completion_value": None,
    }
    row.update(overrides)
    return row


class TestAuth:
    def test_missing_token_is_403_and_contacts_no_provider(
        self, client, vault, monkeypatch
    ):
        calls = _install_discovery(monkeypatch)

        response = client.post(DISCOVER, json={"space_id": SPACE})

        assert response.status_code == 403
        assert response.json() == {
            "detail": "missing or invalid X-TDTB-Token"
        }
        assert calls == []
        assert not cb.source_path(vault).exists()

    def test_wrong_token_is_403_and_contacts_no_provider(
        self, client, vault, monkeypatch
    ):
        calls = _install_discovery(monkeypatch)

        response = client.post(
            DISCOVER,
            headers={"X-TDTB-Token": "wrong-token"},
            json={"space_id": SPACE},
        )

        assert response.status_code == 403
        assert calls == []
        assert not cb.source_path(vault).exists()

    def test_token_is_checked_before_the_body_like_the_save_route(
        self, client, monkeypatch
    ):
        calls = _install_discovery(monkeypatch)

        response = client.post(DISCOVER, json={"bogus": 1})

        assert response.status_code == 403
        assert calls == []


class TestSuccess:
    def test_discovery_returns_the_pinned_sorted_shape_and_echoes_space(
        self, client, vault, monkeypatch
    ):
        _install_real_discovery(monkeypatch)

        response = client.post(
            DISCOVER, headers=_auth(client), json={"space_id": SPACE}
        )

        assert response.status_code == 200
        assert response.json() == {
            "space_id": SPACE,
            "structures": EXPECTED_CATALOG,
            "warnings": [],
        }
        assert cb.source_path(vault).exists() is False

    def test_structure_without_a_display_title_returns_its_structure_id(
        self, client, monkeypatch
    ):
        _install_real_discovery(monkeypatch)

        response = client.post(
            DISCOVER, headers=_auth(client), json={"space_id": SPACE}
        )

        alpha = response.json()["structures"][0]
        assert alpha == {
            "structure_id": "alpha", "title": "alpha", "properties": [],
        }

    def test_requested_space_reaches_discovery_and_is_echoed(
        self, client, vault, monkeypatch
    ):
        calls = _install_discovery(
            monkeypatch,
            catalog=(
                cb.CatalogStructure(
                    structure_id="RootTask",
                    title="RootTask",
                    properties=(
                        cb.CatalogProperty(
                            property_id="status",
                            title="status",
                            type="label",
                            writable=True,
                            label_options=(
                                cb.CatalogLabelOption(id="open", title="Open"),
                            ),
                        ),
                    ),
                ),
            ),
        )

        response = client.post(
            DISCOVER,
            headers=_auth(client),
            json={"space_id": "space-elsewhere"},
        )

        assert response.status_code == 200
        assert calls == [(vault, "space-elsewhere")]
        assert response.json() == {
            "space_id": "space-elsewhere",
            "structures": [
                {
                    "structure_id": "RootTask",
                    "title": "RootTask",
                    "properties": [
                        {
                            "property_id": "status",
                            "title": "status",
                            "type": "label",
                            "writable": True,
                            "label_options": [{"id": "open", "title": "Open"}],
                        }
                    ],
                }
            ],
            "warnings": [],
        }


class TestErrorContract:
    @pytest.mark.parametrize(
        "error, status, detail",
        [
            (
                cb.CapacitiesTokenError("capacities token file not found"),
                503,
                CREDENTIAL_ERROR,
            ),
            (
                ca.CapacitiesRateLimited(
                    "the Capacities API rate limit (30 requests per minute) "
                    "was exceeded"
                ),
                429,
                RATE_LIMIT_ERROR,
            ),
            (
                ca.CapacitiesContractError(
                    "Capacities structures response is malformed"
                ),
                502,
                PROVIDER_ERROR,
            ),
            (RuntimeError("provider exploded"), 502, PROVIDER_ERROR),
            (OSError("connection refused"), 502, PROVIDER_ERROR),
        ],
    )
    def test_each_failure_class_answers_its_pinned_status_and_code(
        self, client, monkeypatch, error, status, detail
    ):
        _install_discovery(monkeypatch, error=error)

        response = client.post(
            DISCOVER, headers=_auth(client), json={"space_id": SPACE}
        )

        assert response.status_code == status
        assert response.json() == {"detail": detail}

    def test_error_bodies_never_leak_token_text_or_paths(
        self, client, vault, monkeypatch
    ):
        leak = (
            f"token={client.app_token} "
            f"path={vault}/00 - META/Cache/tdtb-capacities-source.json "
            "raw provider payload: SUPER-SECRET"
        )
        for error in (
            cb.CapacitiesTokenError(leak),
            ca.CapacitiesRateLimited(leak),
            ca.CapacitiesContractError(leak),
            RuntimeError(leak),
            OSError(leak),
        ):
            _install_discovery(monkeypatch, error=error)

            response = client.post(
                DISCOVER, headers=_auth(client), json={"space_id": SPACE}
            )

            assert response.status_code in {429, 502, 503}
            text = response.text
            assert client.app_token not in text
            assert str(vault) not in text
            assert "SUPER-SECRET" not in text
            assert "token=" not in text
            assert "path=" not in text
            assert "raw provider payload" not in text


class TestValidation:
    @pytest.mark.parametrize(
        "payload",
        [
            {},                                          # missing space_id
            {"space_id": 123},                           # int coercion
            {"space_id": True},                          # bool coercion
            {"space_id": 1.5},                           # float coercion
            {"space_id": None},                          # null
            {"space_id": ""},                            # empty id
            {"space_id": "   "},                         # blank id
            {"space_id": " padded "},                    # whitespace id
            {"space_id": [SPACE]},                       # list
            {"space_id": {"id": SPACE}},                 # object
            {"space_id": SPACE, "expected_revision": 0},  # unknown key
            {"space_id": SPACE, "structures": []},       # unknown key
        ],
    )
    def test_invalid_bodies_are_422_with_the_shared_error(
        self, client, vault, monkeypatch, payload
    ):
        calls = _install_discovery(monkeypatch)

        response = client.post(DISCOVER, headers=_auth(client), json=payload)

        assert response.status_code == 422
        assert response.json() == {"detail": VALIDATION_ERROR}
        assert calls == []
        assert not cb.source_path(vault).exists()

    @pytest.mark.parametrize(
        "raw",
        [
            "",
            "{not json",
            "[]",
            "null",
            "{}",
            f'{{"space_id": "{SPACE}", "space_id": "{SPACE}"}}',
            f'{{"space_id": "{SPACE}", "extra": 1, "extra": 2}}',
        ],
    )
    def test_raw_body_failures_are_422(
        self, client, monkeypatch, raw
    ):
        calls = _install_discovery(monkeypatch)

        response = client.post(
            DISCOVER,
            headers={**_auth(client), "Content-Type": "application/json"},
            content=raw,
        )

        assert response.status_code == 422
        assert response.json() == {"detail": VALIDATION_ERROR}
        assert calls == []

    def test_invalid_body_never_echoes_its_values(self, client, monkeypatch):
        calls = _install_discovery(monkeypatch)

        response = client.post(
            DISCOVER,
            headers=_auth(client),
            json={"space_id": "leaky-value", "secret-key": "leaky-value"},
        )

        assert response.status_code == 422
        assert "leaky-value" not in response.text
        assert "secret-key" not in response.text
        assert calls == []


class TestNoAutoSave:
    def test_discovery_never_reads_or_writes_the_mapping_record(
        self, client, vault, monkeypatch
    ):
        def forbidden(label):
            def _fail(*_args, **_kwargs):
                raise AssertionError(f"{label} must not be called")
            return _fail

        monkeypatch.setattr(cb, "save_source", forbidden("save_source"))
        monkeypatch.setattr(cb, "read_source", forbidden("read_source"))
        monkeypatch.setattr(cb, "read_settings", forbidden("read_settings"))
        _install_discovery(
            monkeypatch,
            catalog=(
                cb.CatalogStructure(
                    structure_id="RootTask", title="RootTask", properties=()
                ),
            ),
        )

        response = client.post(
            DISCOVER, headers=_auth(client), json={"space_id": SPACE}
        )

        assert response.status_code == 200
        assert not cb.source_path(vault).exists()
        assert not cb.lock_path(vault).exists()

    def test_real_discovery_path_creates_no_mapping_file(
        self, client, vault, monkeypatch
    ):
        _install_real_discovery(monkeypatch)

        response = client.post(
            DISCOVER, headers=_auth(client), json={"space_id": SPACE}
        )

        assert response.status_code == 200
        assert not cb.source_path(vault).exists()
        assert not cb.lock_path(vault).exists()

    def test_discovery_leaves_an_existing_mapping_byte_identical(
        self, client, vault, monkeypatch
    ):
        saved = client.post(
            SAVE,
            headers=_auth(client),
            json={
                "expected_revision": 0,
                "space_id": SPACE,
                "structures": [_mapping_structure()],
            },
        )
        assert saved.status_code == 200
        before = cb.source_path(vault).read_bytes()
        revision = saved.json()["source"]["revision"]

        _install_real_discovery(monkeypatch)
        response = client.post(
            DISCOVER, headers=_auth(client), json={"space_id": SPACE}
        )

        assert response.status_code == 200
        assert cb.source_path(vault).read_bytes() == before
        read_back = client.get("/settings/capacities/source").json()
        assert read_back["source"]["revision"] == revision
        assert read_back["source"]["space_id"] == SPACE
