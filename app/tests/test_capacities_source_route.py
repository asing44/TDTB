"""Route tests for the local Capacities source-mapping API.

GET /settings/capacities/source is a tokenless local read; POST
/settings/capacities/source/save is token-guarded and full-replacement. No
test here builds an adapter, reads a credential, or contacts a provider —
the no-provider test proves that with poisoned spies.
"""
from __future__ import annotations

import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))

import main as main_mod  # noqa: E402
import capacities_adapter as ca  # noqa: E402
import capacities_builder as cb  # noqa: E402


SPACE = "space-1"

VALIDATION_ERROR = {
    "code": "capacities_source_validation_error",
    "message": "Capacities source mapping payload is invalid.",
}


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


def _structure(**overrides) -> dict:
    row = {
        "structure_id": "RootTask",
        "title_property": "title",
        "status_property": "status",
        "open_status_values": ["open", "On Hold"],
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


def _body(expected_revision: int = 0, **overrides) -> dict:
    body = {
        "expected_revision": expected_revision,
        "space_id": SPACE,
        "structures": [_structure()],
    }
    body.update(overrides)
    return body


def _source_bytes(vault: Path) -> bytes:
    return cb.source_path(vault).read_bytes()


class TestGet:
    def test_absent_record_is_null_and_creates_nothing(self, client, vault):
        before = sorted(
            str(p.relative_to(vault)) for p in vault.rglob("*")
        )

        response = client.get("/settings/capacities/source")

        assert response.status_code == 200
        assert response.json() == {"source": None, "persisted": False}
        assert not cb.source_path(vault).exists()
        # A read must not create the record, the lock, or any other path.
        assert not cb.lock_path(vault).exists()
        assert sorted(
            str(p.relative_to(vault)) for p in vault.rglob("*")
        ) == before

    def test_get_is_tokenless(self, client):
        response = client.get(
            "/settings/capacities/source",
            headers={"X-TDTB-Token": "wrong-token"},
        )

        assert response.status_code == 200

    def test_get_returns_the_persisted_record(self, client, vault):
        saved = client.post(
            "/settings/capacities/source/save",
            headers=_auth(client),
            json=_body(),
        )
        assert saved.status_code == 200

        body = client.get("/settings/capacities/source").json()

        assert body == {"source": saved.json()["source"], "persisted": True}
        assert body["source"]["revision"] == 1

    def test_corrupt_record_fails_closed_and_preserves_bytes(self, client, vault):
        path = cb.source_path(vault)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json", encoding="utf-8")

        response = client.get("/settings/capacities/source")

        assert response.status_code == 500
        detail = response.json()["detail"]
        assert detail["code"] == "capacities_source_storage_error"
        assert "preserved" in detail["message"]
        assert path.read_bytes() == b"{not json"

    def test_unreadable_record_fails_closed(self, client, vault):
        path = cb.source_path(vault)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.mkdir()

        response = client.get("/settings/capacities/source")

        assert response.status_code == 500
        assert (
            response.json()["detail"]["code"]
            == "capacities_source_storage_error"
        )

    def test_unresolved_vault_is_503(self, monkeypatch):
        monkeypatch.delenv("TDTB_VAULT_ROOT", raising=False)
        app = main_mod.create_app()

        response = TestClient(app).get("/settings/capacities/source")

        assert response.status_code == 503


class TestPostAuth:
    def test_post_requires_token(self, client, vault):
        response = client.post("/settings/capacities/source/save", json=_body())

        assert response.status_code == 403
        assert not cb.source_path(vault).exists()

    def test_post_wrong_token_is_rejected(self, client, vault):
        response = client.post(
            "/settings/capacities/source/save",
            headers={"X-TDTB-Token": "wrong-token"},
            json=_body(),
        )

        assert response.status_code == 403
        assert not cb.source_path(vault).exists()


class TestPostPersistence:
    def test_round_trip_increments_the_revision_once(self, client, vault):
        response = client.post(
            "/settings/capacities/source/save",
            headers=_auth(client),
            json=_body(),
        )

        assert response.status_code == 200
        body = response.json()
        assert body["persisted"] is True
        source = body["source"]
        assert source["version"] == 1
        assert source["revision"] == 1
        assert source["space_id"] == SPACE
        assert source["structures"] == [_structure()]

        read_back = client.get("/settings/capacities/source").json()
        assert read_back == {"source": source, "persisted": True}
        assert cb.read_source(vault).as_dict() == source

    def test_a_second_save_increments_the_revision_again(self, client, vault):
        first = client.post(
            "/settings/capacities/source/save",
            headers=_auth(client),
            json=_body(),
        )
        assert first.json()["source"]["revision"] == 1

        second = client.post(
            "/settings/capacities/source/save",
            headers=_auth(client),
            json=_body(
                expected_revision=1,
                structures=[_structure(structure_id="Task")],
            ),
        )

        assert second.status_code == 200
        assert second.json()["source"]["revision"] == 2
        # Full replacement: only the new structure survives.
        assert [
            row["structure_id"]
            for row in second.json()["source"]["structures"]
        ] == ["Task"]

    def test_absent_record_is_revision_zero(self, client, vault):
        response = client.post(
            "/settings/capacities/source/save",
            headers=_auth(client),
            json=_body(expected_revision=3),
        )

        assert response.status_code == 409
        assert response.json()["detail"] == {
            "code": "capacities_source_conflict",
            "message": "Mapping changed; reload and review.",
            "expected_revision": 3,
            "current_revision": 0,
        }
        assert not cb.source_path(vault).exists()

    def test_stale_revision_conflicts_with_both_revisions(self, client, vault):
        client.post(
            "/settings/capacities/source/save",
            headers=_auth(client),
            json=_body(),
        )
        before = _source_bytes(vault)

        response = client.post(
            "/settings/capacities/source/save",
            headers=_auth(client),
            json=_body(expected_revision=0),
        )

        assert response.status_code == 409
        assert response.json()["detail"] == {
            "code": "capacities_source_conflict",
            "message": "Mapping changed; reload and review.",
            "expected_revision": 0,
            "current_revision": 1,
        }
        assert _source_bytes(vault) == before

    def test_save_over_a_corrupt_record_is_409_without_erasing(
        self, client, vault
    ):
        path = cb.source_path(vault)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("garbage", encoding="utf-8")

        response = client.post(
            "/settings/capacities/source/save",
            headers=_auth(client),
            json=_body(),
        )

        assert response.status_code == 409
        assert (
            response.json()["detail"]["code"]
            == "capacities_source_storage_error"
        )
        assert path.read_bytes() == b"garbage"

    def test_save_write_failure_is_500_and_preserves_bytes(
        self, client, vault, monkeypatch
    ):
        client.post(
            "/settings/capacities/source/save",
            headers=_auth(client),
            json=_body(),
        )
        before = _source_bytes(vault)

        def fail_write(*_args, **_kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(cb, "_atomic_write_json", fail_write)

        response = client.post(
            "/settings/capacities/source/save",
            headers=_auth(client),
            json=_body(
                expected_revision=1,
                structures=[_structure(structure_id="Task")],
            ),
        )

        assert response.status_code == 500
        assert (
            response.json()["detail"]["code"]
            == "capacities_source_storage_error"
        )
        assert _source_bytes(vault) == before

    def test_unresolved_vault_is_503(self, client, monkeypatch):
        monkeypatch.delenv("TDTB_VAULT_ROOT", raising=False)
        app = main_mod.create_app()

        response = TestClient(app).post(
            "/settings/capacities/source/save",
            headers={"X-TDTB-Token": app.state.token},
            json=_body(),
        )

        assert response.status_code == 503


class TestPostValidation:
    @pytest.mark.parametrize(
        "payload",
        [
            _body(expected_revision=True),                     # bool revision
            _body(expected_revision=-1),                       # negative
            _body(expected_revision=1.5),                      # float
            _body(expected_revision="1"),                      # string
            _body(space_id=123),                               # int space id
            _body(space_id=" padded "),                        # whitespace id
            _body(space_id=""),                                # empty id
            _body(extra="nope"),                               # unknown top key
            {"expected_revision": 0, "space_id": SPACE},       # missing list
            {"space_id": SPACE, "structures": [_structure()]},  # missing revision
            {"expected_revision": 0, "structures": [_structure()]},
            _body(structures="not-a-list"),                    # string list
            _body(structures=[]),                              # empty list
            _body(structures=[{"structure_id": "a", "bogus": 1}]),
            _body(structures=[{"structure_id": "a"}] * 2),     # duplicate ids
            _body(structures=["not-an-object"]),
            _body(structures=[{"structure_id": "a", "title_property": None}]),
            _body(structures=[{"structure_id": " padded "}]),
            _body(
                structures=[
                    {"structure_id": "a", "open_status_values": ["open", " OPEN "]}
                ]
            ),
        ],
    )
    def test_malformed_bodies_are_422_and_leave_the_bytes_identical(
        self, client, vault, payload
    ):
        assert client.post(
            "/settings/capacities/source/save",
            headers=_auth(client),
            json=_body(),
        ).status_code == 200
        before = _source_bytes(vault)

        response = client.post(
            "/settings/capacities/source/save",
            headers=_auth(client),
            json=payload,
        )

        assert response.status_code == 422
        assert response.json()["detail"] == VALIDATION_ERROR
        assert _source_bytes(vault) == before

    @pytest.mark.parametrize(
        "raw",
        [
            "{not json",
            "",
            "[]",
            "null",
            "{}",
            # Duplicate JSON keys collapse under FastAPI's default parse;
            # this route's parse must reject them at every level.
            (
                '{"expected_revision": 0, "expected_revision": 0, '
                f'"space_id": "{SPACE}", '
                '"structures": [{"structure_id": "a"}]}'
            ),
            (
                '{"expected_revision": 0, '
                f'"space_id": "{SPACE}", '
                '"structures": [{"structure_id": "a", '
                '"structure_id": "b"}]}'
            ),
        ],
    )
    def test_raw_body_failures_are_422_and_leave_the_bytes_identical(
        self, client, vault, raw
    ):
        assert client.post(
            "/settings/capacities/source/save",
            headers=_auth(client),
            json=_body(),
        ).status_code == 200
        before = _source_bytes(vault)

        response = client.post(
            "/settings/capacities/source/save",
            headers={**_auth(client), "Content-Type": "application/json"},
            content=raw,
        )

        assert response.status_code == 422
        assert response.json()["detail"] == VALIDATION_ERROR
        assert _source_bytes(vault) == before


class TestConcurrentSave:
    def test_racing_saves_from_one_revision_produce_one_winner(
        self, app, vault
    ):
        token = app.state.token
        barrier = threading.Barrier(2)

        def attempt() -> tuple[int, dict]:
            client = TestClient(app)
            barrier.wait()
            response = client.post(
                "/settings/capacities/source/save",
                headers={"X-TDTB-Token": token},
                json=_body(
                    expected_revision=0,
                    structures=[_structure(structure_id="racing")],
                ),
            )
            return response.status_code, response.json()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _index: attempt(), range(2)))

        assert sorted(status for status, _ in results) == [200, 409]
        winner = next(body for status, body in results if status == 200)
        conflict = next(body for status, body in results if status == 409)
        assert winner["source"]["revision"] == 1
        assert conflict["detail"] == {
            "code": "capacities_source_conflict",
            "message": "Mapping changed; reload and review.",
            "expected_revision": 0,
            "current_revision": 1,
        }
        assert cb.read_source(vault).revision == 1


class TestNoProviderContact:
    def test_get_and_save_never_build_an_adapter_or_touch_a_provider(
        self, client, vault, monkeypatch
    ):
        calls: list[str] = []

        def forbidden(label: str):
            def _fail(*_args, **_kwargs):
                calls.append(label)
                raise AssertionError(f"{label} must not be called")
            return _fail

        client.app.state.build_capacities_adapter = forbidden("app adapter seam")
        monkeypatch.setattr(
            cb, "build_capacities_adapter", forbidden("builder")
        )
        monkeypatch.setattr(cb, "read_settings", forbidden("settings read"))
        monkeypatch.setattr(
            cb, "load_capacities_token", forbidden("credential read")
        )
        monkeypatch.setattr(
            ca.CapacitiesRestClient, "__init__", forbidden("provider client")
        )
        monkeypatch.setattr(
            main_mod.external_sources,
            "fetch_capacities_items",
            forbidden("provider fetch"),
        )

        assert client.get("/settings/capacities/source").status_code == 200
        saved = client.post(
            "/settings/capacities/source/save",
            headers=_auth(client),
            json=_body(),
        )
        assert saved.status_code == 200
        assert client.get("/settings/capacities/source").status_code == 200

        assert calls == []
