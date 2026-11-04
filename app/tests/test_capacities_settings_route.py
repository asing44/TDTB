"""Route tests for the local Capacities settings API (schema version 2).

GET /settings/capacities is tokenless local storage only; POST
/settings/capacities/save is token-guarded and full-replacement. No test here
builds an adapter, calls a provider, or touches a live source.

The persisted document is ONE schema-v2 policy: a status vocabulary, one
declaration per structure (``types[sid] = {mode, property?, statuses?}``), the
native deadline horizon, and the exclusion set. A structure therefore carries
exactly one admission mode and the old contradictions are unrepresentable.

Version-1 concepts that no longer exist, and what happened to the tests that
asserted them:

- the three global native toggles (``native_task_auto.active_enabled`` /
  ``due_enabled`` / ``deadline_enabled``): the ``native`` mode admits on every
  native condition, so the toggles were deleted, not rewritten; only
  ``native.deadline_horizon_days`` survives (`TestPostValidation`,
  `TestPostPersistence`).
- the ``active_structures`` flag map: rewritten as ``types[sid].mode ==
  "active"`` (`TestActiveModeStructures`).
- the ``assigned_structures`` / ``assigned_on_structures`` property maps:
  rewritten as ``types[sid] = {"mode": "assigned" | "assignedOn",
  "property": ...}`` (`TestAssignedTypeDeclarations`,
  `TestAssignedOnTypeDeclarations`).
- ``native_task_structures`` and ``active_statuses``: rewritten as per-type
  ``statuses`` plus the top-level ``statuses`` block (`TestStatusVocabulary`,
  `TestTypeStatusLists`).
- the ``capacities_settings_declaration_conflict`` refusal of a structure
  declared in BOTH property maps: deleted because schema v2 cannot represent
  it; the closest v2 equivalent (one declaration per structure, a later save
  REPLACING the mode, and the legacy dual-map payload now rejected as
  unknown keys) is asserted in `TestOneModePerStructure`. Deleted tests and
  their reasons: ``test_post_refuses_both_mappings_with_422_and_preserves_bytes``
  and ``test_post_refuses_on_a_first_save_without_creating_a_file`` (the
  error code and its 422 conflict body no longer exist),
  ``test_one_mapping_per_structure_still_saves`` (subsumed by the
  one-mode-per-structure round trip).
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
import capacities_adapter as ca  # noqa: E402
import capacities_builder as cb  # noqa: E402
import capacities_structure_titles as cst  # noqa: E402


SPACE = "space-1"
NATIVE = f"capacities:{SPACE}:RootTask:task-1"
CUSTOM = f"capacities:{SPACE}:custom-project:project-1"

PROJECT_STRUCTURE = "0d194525-c5a1-4af5-bb62-202b83006b5e"
PRESS_STRUCTURE = "6aa7b02a-4315-47d1-9cfb-0c0cdac0950c"
PROJECT_ASSIGNED_PROPERTY = "f779f78a-3b1e-4a2c-9f5d-6a0f0e1d2c3b"
ADVENTURE_ASSIGNED_PROPERTY = "c19f9b95-7c2d-4e18-8a51-9b0c1d2e3f40"
PROJECT_ASSIGNED_ON_PROPERTY = "1a2b3c4d-5e6f-4788-9a0b-1c2d3e4f5a6b"
LIVE_ASSIGNED_PROPERTY = "9f8e7d6c-5b4a-4392-8170-6f5e4d3c2b1a"


@pytest.fixture
def client() -> TestClient:
    app = main_mod.create_app()
    c = TestClient(app)
    c.app_token = app.state.token
    return c


@pytest.fixture(autouse=True)
def titles_cache(tmp_path, monkeypatch) -> Path:
    """Point every route under test at an isolated title cache.

    ``GET /settings/capacities`` reads the machine-local title cache through
    its module default, so each test must be kept off the operator's real
    file. The monkeypatched path is the same file ``remember_titles`` writes
    when called with no explicit ``path`` — exactly how the structure
    observer persists titles in production.
    """
    path = tmp_path / "machine-local" / "structure-titles.json"
    monkeypatch.setattr(cst, "DEFAULT_TITLES_CACHE_PATH", path)
    return path


def _auth(client: TestClient) -> dict:
    return {"X-TDTB-Token": client.app_token}


def _body(expected_revision: int = 0, **overrides) -> dict:
    """A minimal valid schema-v2 save body.

    ``statuses`` and ``types`` are optional and default to the documented
    vocabulary / no declarations; ``native`` and ``excluded`` must be stated.
    """
    body = {
        "expected_revision": expected_revision,
        "native": {"deadline_horizon_days": 2},
        "excluded": {},
    }
    body.update(overrides)
    return body


def _settings_bytes() -> bytes:
    return cs.settings_path().read_bytes()


def _stored_document() -> dict:
    return json.loads(_settings_bytes().decode("utf-8"))


def _assert_no_store_touched() -> None:
    """A rejected request is a 422 before ANY file access: no store, no lock."""
    assert not cs.settings_path().exists()
    assert not cs.lock_path().exists()


def _write_source_record(structure_ids: list[str]) -> Path:
    """Write a minimal valid Capacities source-mapping record (vault-local)."""
    path = cb.source_path()
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


def _v1_bytes(revision: int = 3) -> bytes:
    """A strict, valid schema-version-1 document (the pre-v2 shape)."""
    return (
        json.dumps(
            {
                "version": 1,
                "revision": revision,
                "native_task_auto": {
                    "active_enabled": True,
                    "due_enabled": False,
                    "deadline_enabled": False,
                    "deadline_horizon_days": 5,
                },
                "excluded": {NATIVE: True},
                "active_structures": {PROJECT_STRUCTURE: True},
                "assigned_structures": {
                    PRESS_STRUCTURE: PROJECT_ASSIGNED_PROPERTY
                },
                "assigned_on_structures": {},
                "native_task_structures": [],
                "active_statuses": ["In Progress"],
            },
            indent=2,
        )
        + "\n"
    ).encode("utf-8")


def _backup_siblings() -> list[Path]:
    directory = cs.settings_path().parent
    if not directory.exists():
        return []
    return sorted(
        directory.glob(f"{cs.STATE_FILENAME}{cs.BACKUP_INFIX}*")
    )


class TestGet:
    def test_get_is_tokenless_and_reports_the_default(self, client):
        response = client.get("/settings/capacities")

        assert response.status_code == 200
        body = response.json()
        assert body["persisted"] is False
        assert body["settings"] == {
            "version": 2,
            "revision": 0,
            "statuses": {
                "open": ["active"],
                "completed": ["Completed", "Dropped"],
            },
            "types": {},
            "native": {"deadline_horizon_days": 2},
            "excluded": {},
        }
        # A migration report only describes a file that EXISTS.
        assert "migrated_from" not in body
        assert not cs.settings_path().exists()

    def test_get_reports_persisted_settings_after_save(self, client):
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
        assert "migrated_from" not in body

    def test_get_malformed_storage_fails_closed_without_erasing(self, client):
        path = cs.settings_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json!!", encoding="utf-8")

        response = client.get("/settings/capacities")

        assert response.status_code == 500
        detail = response.json()["detail"]
        assert detail["code"] == "capacities_settings_storage_error"
        assert "preserved" in detail["message"]
        assert _settings_bytes() == b"{not json!!"

    def test_get_reports_no_available_structures_when_unconfigured(self, client):
        body = client.get("/settings/capacities").json()

        assert body["available_structures"] == []
        # Advisory only: reading the settings route never creates a record.
        assert not cb.source_path().exists()

    def test_get_reports_configured_structures_sorted(self, client):
        _write_source_record(["b-structure", "a-structure"])

        body = client.get("/settings/capacities").json()

        assert body["available_structures"] == ["a-structure", "b-structure"]
        # The source record and the settings policy are independent stores.
        assert body["persisted"] is False
        assert body["settings"]["revision"] == 0

    def test_get_malformed_source_record_degrades_to_empty(self, client):
        path = cb.source_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json!!", encoding="utf-8")

        response = client.get("/settings/capacities")

        # The advisory list fails soft; the settings route itself still works
        # and the malformed source bytes are left untouched for the builder to
        # report loudly when an adapter is next built.
        assert response.status_code == 200
        body = response.json()
        assert body["available_structures"] == []
        assert body["settings"]["version"] == 2
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
    def test_post_persists_and_returns_the_saved_settings(self, client):
        response = client.post(
            "/settings/capacities/save",
            headers=_auth(client),
            json=_body(
                native={"deadline_horizon_days": 4},
                excluded={NATIVE: True, CUSTOM: True},
            ),
        )

        assert response.status_code == 200
        body = response.json()
        assert body["persisted"] is True
        assert body["settings"]["version"] == 2
        assert body["settings"]["revision"] == 1
        assert body["settings"]["native"] == {"deadline_horizon_days": 4}
        assert body["settings"]["excluded"] == {NATIVE: True, CUSTOM: True}
        # The omitted editable inputs are written as the documented defaults
        # by the full-replacement save.
        assert body["settings"]["statuses"] == {
            "open": ["active"],
            "completed": ["Completed", "Dropped"],
        }
        assert body["settings"]["types"] == {}
        assert cs.settings_path().is_file()
        assert _stored_document()["version"] == 2

    def test_post_is_a_full_replacement(self, client):
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

    def test_post_stale_revision_conflicts_and_preserves_bytes(self, client):
        client.post(
            "/settings/capacities/save",
            headers=_auth(client),
            json=_body(),
        )
        before = _settings_bytes()

        response = client.post(
            "/settings/capacities/save",
            headers=_auth(client),
            json=_body(expected_revision=0, excluded={NATIVE: True}),
        )

        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "capacities_settings_conflict"
        assert _settings_bytes() == before
        # No convenience side effects on a rejected save.
        assert _backup_siblings() == []

    def test_post_malformed_existing_storage_conflicts_without_erasing(self, client):
        path = cs.settings_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("garbage", encoding="utf-8")

        response = client.post(
            "/settings/capacities/save",
            headers=_auth(client),
            json=_body(),
        )

        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "capacities_settings_storage_error"
        assert _settings_bytes() == b"garbage"


class TestPostValidation:
    @pytest.mark.parametrize(
        "payload",
        [
            _body(extra="x"),                                             # extra top-level
            {k: v for k, v in _body().items() if k != "expected_revision"},
            _body(expected_revision=True),                                # bool revision
            _body(expected_revision=-1),                                  # negative revision
            _body(expected_revision=1.5),                                 # float revision
            {k: v for k, v in _body().items() if k != "native"},          # missing native
            _body(native={"deadline_horizon_days": "2"}),                # string horizon
            _body(native={"deadline_horizon_days": 2, "extra": 1}),        # extra native key
            _body(native={"deadline_horizon_days": True}),                # bool horizon
            _body(native={"deadline_horizon_days": -1}),                  # negative horizon
            _body(native={"deadline_horizon_days": 2.5}),                 # float horizon
            _body(native="native"),                                       # non-object native
            _body(excluded={"task-1": True}),                             # bare id
            _body(excluded={"Write brief": True}),                        # title
            _body(excluded={f"  {NATIVE}  ": True}),                      # whitespace alias
            _body(excluded={NATIVE: False}),                              # flag false
            _body(excluded={NATIVE: 1}),                                  # flag int
            _body(statuses="open"),                                       # non-object statuses
            _body(statuses={"open": ["active"], "other": []}),            # unknown status key
        ],
    )
    def test_malformed_requests_are_rejected_422(self, client, payload):
        response = client.post(
            "/settings/capacities/save",
            headers=_auth(client),
            json=payload,
        )

        assert response.status_code == 422
        _assert_no_store_touched()

    def test_duplicate_request_keys_are_not_silently_accepted_as_two_identities(
        self, client
    ):
        # A duplicate JSON key in the request collapses to one key at parse
        # time; the server still validates the surviving value strictly. This
        # documents that the storage-level duplicate rejection is the strong
        # guarantee (see test_capacities_settings).
        raw = (
            '{"expected_revision": 0, '
            '"native": {"deadline_horizon_days": 2}, '
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
        self, client, monkeypatch
    ):
        self._poison_adapter(client)

        def boom(*_args, **_kwargs):
            raise AssertionError("settings routes must not call a provider")

        monkeypatch.setattr(main_mod.external_sources, "fetch_capacities_items", boom)

        assert client.get("/settings/capacities").status_code == 200

    def test_post_never_builds_an_adapter_or_provider(
        self, client, monkeypatch
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
        data = _stored_document()
        assert data["excluded"] == {NATIVE: True}


class TestActiveModeStructures:
    """``types[sid].mode == "active"`` replaces the v1 ``active_structures`` map.

    The declaration must survive the route in both directions, and omission
    stays a full replacement to no declarations at all."""

    def test_post_persists_and_get_reports_active_declarations(self, client):
        declarations = {
            "custom-project": {"mode": "active"},
            PROJECT_STRUCTURE: {"mode": "active"},
        }
        response = client.post(
            "/settings/capacities/save",
            headers=_auth(client),
            json=_body(types=declarations),
        )

        assert response.status_code == 200
        assert response.json()["settings"]["types"] == {
            "custom-project": {
                "mode": "active",
                "property": None,
                "statuses": None,
            },
            PROJECT_STRUCTURE: {
                "mode": "active",
                "property": None,
                "statuses": None,
            },
        }
        body = client.get("/settings/capacities").json()
        assert body["settings"]["types"] == response.json()["settings"]["types"]
        assert _stored_document()["types"] == response.json()["settings"]["types"]

    def test_omitting_types_is_a_full_replacement_to_empty(self, client):
        client.post(
            "/settings/capacities/save",
            headers=_auth(client),
            json=_body(types={"custom-project": {"mode": "active"}}),
        )
        second = client.post(
            "/settings/capacities/save",
            headers=_auth(client),
            json=_body(expected_revision=1),
        )

        assert second.status_code == 200
        assert second.json()["settings"]["types"] == {}

    def test_an_explicit_empty_types_object_is_a_legitimate_replacement(
        self, client
    ):
        # Explicit ``{}`` and omission mean the same thing (no declarations),
        # exactly like every other full-replacement input.
        response = client.post(
            "/settings/capacities/save",
            headers=_auth(client),
            json=_body(types={}),
        )

        assert response.status_code == 200
        assert response.json()["settings"]["types"] == {}

    @pytest.mark.parametrize(
        "bad",
        [
            [],                                                     # list
            "custom-project",                                       # string
            None,                                                   # null
            {"custom-project": True},                               # flag instead of mode
            {"custom-project": {"mode": "active", "extra": True}},  # extra declaration key
            {"custom-project": {"mode": "Active"}},                 # unknown mode casing
            {"custom-project": {}},                                 # missing mode
            {"": {"mode": "active"}},                               # empty structure id
            {" custom-project ": {"mode": "active"}},               # whitespace alias
            {"custom project": {"mode": "active"}},                 # interior whitespace
        ],
    )
    def test_post_rejects_malformed_type_declarations_422(self, client, bad):
        response = client.post(
            "/settings/capacities/save",
            headers=_auth(client),
            json=_body(types=bad),
        )

        assert response.status_code == 422
        assert any(
            "types" in entry["loc"] for entry in response.json()["detail"]
        )
        _assert_no_store_touched()


class TestModePropertyContradictions:
    """``property`` is required for the two property-reading modes and
    forbidden for every other mode; both are a 422 before any file access."""

    @pytest.mark.parametrize(
        "declaration",
        [
            {"mode": "bogus"},                                            # bad mode
            {"mode": "off", "property": PROJECT_ASSIGNED_PROPERTY},       # property on off
            {"mode": "active", "property": PROJECT_ASSIGNED_PROPERTY},    # property on active
            {"mode": "native", "property": PROJECT_ASSIGNED_PROPERTY},    # property on native
            {"mode": "assigned"},                                         # missing property
            {"mode": "assignedOn"},                                       # missing property
            {"mode": "assigned", "property": None},                       # null property
            {"mode": "assigned", "property": ""},                         # empty property
            {"mode": "assigned", "property": True},                       # non-string property
            {
                "mode": "active",
                "statuses": ["active", "active"],
            },                                                            # duplicate names
            {"mode": "active", "statuses": [""]},                         # blank name
            {"mode": "active", "statuses": "active"},                     # bare string list
        ],
    )
    def test_declaration_contradictions_are_rejected_422(self, client, declaration):
        response = client.post(
            "/settings/capacities/save",
            headers=_auth(client),
            json=_body(types={PROJECT_STRUCTURE: declaration}),
        )

        assert response.status_code == 422
        assert any(
            "types" in entry["loc"] for entry in response.json()["detail"]
        )
        _assert_no_store_touched()

    def test_each_rejected_payload_leaves_a_saved_file_untouched(self, client):
        first = client.post(
            "/settings/capacities/save",
            headers=_auth(client),
            json=_body(
                types={
                    PROJECT_STRUCTURE: {
                        "mode": "assigned",
                        "property": PROJECT_ASSIGNED_PROPERTY,
                    }
                }
            ),
        )
        assert first.status_code == 200
        before = _settings_bytes()

        for declaration in (
            {"mode": "bogus"},
            {"mode": "off", "property": PROJECT_ASSIGNED_PROPERTY},
            {"mode": "assigned"},
            {"mode": "active", "statuses": ["active", "active"]},
        ):
            response = client.post(
                "/settings/capacities/save",
                headers=_auth(client),
                json=_body(
                    expected_revision=1,
                    types={PRESS_STRUCTURE: declaration},
                ),
            )
            assert response.status_code == 422
            assert _settings_bytes() == before


class TestOneModePerStructure:
    """Schema v2 replaced the dual-declaration refusal with structure.

    One declaration per structure means an ``assigned`` + ``assignedOn``
    contradiction is unrepresentable: the second save REPLACES the mode rather
    than merging, and a legacy payload that still carries both v1 maps is
    rejected as unknown keys instead of by a dedicated conflict code."""

    def test_a_later_save_replaces_the_mode_of_the_same_structure(self, client):
        first = client.post(
            "/settings/capacities/save",
            headers=_auth(client),
            json=_body(
                types={
                    PROJECT_STRUCTURE: {
                        "mode": "assigned",
                        "property": PROJECT_ASSIGNED_PROPERTY,
                    }
                }
            ),
        )
        assert first.status_code == 200

        second = client.post(
            "/settings/capacities/save",
            headers=_auth(client),
            json=_body(
                expected_revision=1,
                types={
                    PROJECT_STRUCTURE: {
                        "mode": "assignedOn",
                        "property": PROJECT_ASSIGNED_ON_PROPERTY,
                    }
                },
            ),
        )

        assert second.status_code == 200
        types = second.json()["settings"]["types"]
        assert types == {
            PROJECT_STRUCTURE: {
                "mode": "assignedOn",
                "property": PROJECT_ASSIGNED_ON_PROPERTY,
                "statuses": None,
            }
        }
        # Exactly one declaration on disk: no residual boolean gate.
        assert _stored_document()["types"] == types
        assert PROJECT_ASSIGNED_PROPERTY not in _settings_bytes().decode("utf-8")

    def test_the_legacy_dual_map_payload_is_rejected_as_unknown_keys(self, client):
        response = client.post(
            "/settings/capacities/save",
            headers=_auth(client),
            json=_body(
                assigned_structures={
                    PROJECT_STRUCTURE: PROJECT_ASSIGNED_PROPERTY
                },
                assigned_on_structures={
                    PROJECT_STRUCTURE: PROJECT_ASSIGNED_ON_PROPERTY
                },
            ),
        )

        assert response.status_code == 422
        # The retired refusal code is gone; the schema itself now forbids the
        # shape, so the body is a plain validation error.
        assert "detail" in response.json()
        assert "capacities_settings_declaration_conflict" not in response.text
        _assert_no_store_touched()


class TestStatusVocabulary:
    """Round-trip for the top-level ``statuses`` block.

    ``statuses.open`` is what every declaration inherits; ``statuses.completed``
    is the closed vocabulary. Omission is a full replacement to the documented
    default vocabulary rather than to an empty set, and an explicit empty list
    is a legitimate replacement."""

    def test_post_persists_and_get_round_trips_both_lists(self, client):
        response = client.post(
            "/settings/capacities/save",
            headers=_auth(client),
            json=_body(
                statuses={
                    "open": ["active", "In Progress"],
                    "completed": ["Done"],
                }
            ),
        )

        assert response.status_code == 200
        expected = {"open": ["In Progress", "active"], "completed": ["Done"]}
        assert response.json()["settings"]["statuses"] == expected

        body = client.get("/settings/capacities").json()
        assert body["persisted"] is True
        assert body["settings"]["statuses"] == expected

        # The persisted version-2 bytes carry the same block.
        assert _stored_document()["statuses"] == expected

    def test_empty_lists_are_a_legitimate_full_replacement(self, client):
        response = client.post(
            "/settings/capacities/save",
            headers=_auth(client),
            json=_body(statuses={"open": [], "completed": []}),
        )

        assert response.status_code == 200
        assert response.json()["settings"]["statuses"] == {
            "open": [],
            "completed": [],
        }
        read_back = client.get("/settings/capacities").json()["settings"]
        assert read_back["statuses"] == {"open": [], "completed": []}

    def test_omitting_statuses_writes_the_documented_default(self, client):
        first = client.post(
            "/settings/capacities/save",
            headers=_auth(client),
            json=_body(statuses={"open": ["In Progress"], "completed": ["Done"]}),
        )
        assert first.status_code == 200

        second = client.post(
            "/settings/capacities/save",
            headers=_auth(client),
            json=_body(expected_revision=1),
        )

        assert second.status_code == 200
        assert second.json()["settings"]["statuses"] == {
            "open": ["active"],
            "completed": ["Completed", "Dropped"],
        }

    def test_stale_revision_with_a_new_vocabulary_still_conflicts(self, client):
        first = client.post(
            "/settings/capacities/save",
            headers=_auth(client),
            json=_body(statuses={"open": ["In Progress"], "completed": ["Done"]}),
        )
        assert first.status_code == 200
        before = _settings_bytes()

        response = client.post(
            "/settings/capacities/save",
            headers=_auth(client),
            json=_body(
                expected_revision=0,
                statuses={"open": ["active"], "completed": ["Done"]},
            ),
        )

        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "capacities_settings_conflict"
        assert _settings_bytes() == before

    @pytest.mark.parametrize(
        "field,bad",
        [
            ("open", "active"),                                   # bare string
            ("open", {"active": True}),                           # object
            ("open", None),                                       # null
            ("open", [None]),                                     # null entry
            ("open", [1]),                                        # number entry
            ("open", [True]),                                     # bool entry
            ("open", [""]),                                       # empty entry
            ("open", ["active", "active"]),                       # duplicate
            ("completed", "Completed"),                           # bare string
            ("completed", None),                                  # null
            ("completed", [""]),                                  # empty entry
            ("completed", ["Done", "Done"]),                      # duplicate
        ],
    )
    def test_post_rejects_malformed_vocabulary_422(self, client, field, bad):
        response = client.post(
            "/settings/capacities/save",
            headers=_auth(client),
            json=_body(statuses={field: bad}),
        )

        assert response.status_code == 422
        assert any(
            "statuses" in entry["loc"] and field in entry["loc"]
            for entry in response.json()["detail"]
        )
        _assert_no_store_touched()

    def test_malformed_vocabulary_preserves_existing_bytes(self, client):
        first = client.post(
            "/settings/capacities/save",
            headers=_auth(client),
            json=_body(statuses={"open": ["In Progress"], "completed": ["Done"]}),
        )
        assert first.status_code == 200
        before = _settings_bytes()

        response = client.post(
            "/settings/capacities/save",
            headers=_auth(client),
            json=_body(
                expected_revision=1,
                statuses={"open": ["In Progress", "In Progress"]},
            ),
        )

        assert response.status_code == 422
        assert _settings_bytes() == before


class TestTypeStatusLists:
    """``types[sid].statuses`` is tri-state and must survive the route.

    ``null`` (absent) inherits the shared open vocabulary; ``[]`` disables
    status admission for that structure (the date-only decision); a non-empty
    list is that structure's own open names."""

    def test_disabled_and_inherited_statuses_round_trip_distinctly(self, client):
        response = client.post(
            "/settings/capacities/save",
            headers=_auth(client),
            json=_body(
                statuses={"open": ["active"], "completed": ["Completed"]},
                types={
                    PROJECT_STRUCTURE: {"mode": "native", "statuses": []},
                    PRESS_STRUCTURE: {"mode": "native"},
                },
            ),
        )

        assert response.status_code == 200
        types = response.json()["settings"]["types"]
        assert types[PROJECT_STRUCTURE]["statuses"] == []
        assert types[PRESS_STRUCTURE]["statuses"] is None

        read_back = client.get("/settings/capacities").json()["settings"]["types"]
        assert read_back[PROJECT_STRUCTURE]["statuses"] == []
        assert read_back[PRESS_STRUCTURE]["statuses"] is None

        stored = _stored_document()["types"]
        assert stored[PROJECT_STRUCTURE]["statuses"] == []
        assert stored[PRESS_STRUCTURE]["statuses"] is None

    def test_a_structures_own_names_round_trip_sorted(self, client):
        response = client.post(
            "/settings/capacities/save",
            headers=_auth(client),
            json=_body(
                types={
                    PROJECT_STRUCTURE: {
                        "mode": "active",
                        "statuses": ["In Progress", "active"],
                    }
                }
            ),
        )

        assert response.status_code == 200
        assert response.json()["settings"]["types"][PROJECT_STRUCTURE][
            "statuses"
        ] == ["In Progress", "active"]
        # ``statuses.open`` was omitted, so the shared vocabulary keeps its
        # documented default and the per-structure names do not leak into it.
        assert response.json()["settings"]["statuses"]["open"] == ["active"]


class TestAssignedTypeDeclarations:
    """Round-trip for an ``assigned`` type declaration, replacing the v1
    additive ``assigned_structures`` property map."""

    def test_get_returns_the_declaration_and_post_round_trips_it(self, client):
        declarations = {
            PROJECT_STRUCTURE: {
                "mode": "assigned",
                "property": PROJECT_ASSIGNED_PROPERTY,
            },
            PRESS_STRUCTURE: {
                "mode": "assigned",
                "property": ADVENTURE_ASSIGNED_PROPERTY,
            },
        }
        response = client.post(
            "/settings/capacities/save",
            headers=_auth(client),
            json=_body(types=declarations),
        )

        assert response.status_code == 200
        assert response.json()["settings"]["types"] == {
            PROJECT_STRUCTURE: {
                "mode": "assigned",
                "property": PROJECT_ASSIGNED_PROPERTY,
                "statuses": None,
            },
            PRESS_STRUCTURE: {
                "mode": "assigned",
                "property": ADVENTURE_ASSIGNED_PROPERTY,
                "statuses": None,
            },
        }

        body = client.get("/settings/capacities").json()
        assert body["settings"]["types"] == response.json()["settings"]["types"]
        assert _stored_document()["types"] == response.json()["settings"]["types"]

    def test_the_v1_property_map_key_is_no_longer_accepted(self, client):
        response = client.post(
            "/settings/capacities/save",
            headers=_auth(client),
            json=_body(
                assigned_structures={
                    PROJECT_STRUCTURE: PROJECT_ASSIGNED_PROPERTY
                }
            ),
        )

        assert response.status_code == 422
        _assert_no_store_touched()


class TestAssignedOnTypeDeclarations:
    """Round-trip for an ``assignedOn`` type declaration (date-only gate)."""

    def test_get_returns_the_declaration_and_post_round_trips_it(self, client):
        declarations = {
            PROJECT_STRUCTURE: {
                "mode": "assignedOn",
                "property": PROJECT_ASSIGNED_ON_PROPERTY,
            }
        }
        response = client.post(
            "/settings/capacities/save",
            headers=_auth(client),
            json=_body(types=declarations),
        )

        assert response.status_code == 200
        assert response.json()["settings"]["types"] == {
            PROJECT_STRUCTURE: {
                "mode": "assignedOn",
                "property": PROJECT_ASSIGNED_ON_PROPERTY,
                "statuses": None,
            }
        }

        body = client.get("/settings/capacities").json()
        assert body["settings"]["types"] == response.json()["settings"]["types"]
        assert _stored_document()["types"] == response.json()["settings"]["types"]

    def test_stale_revision_conflicts_and_preserves_bytes(self, client):
        first = client.post(
            "/settings/capacities/save",
            headers=_auth(client),
            json=_body(
                types={
                    PROJECT_STRUCTURE: {
                        "mode": "assignedOn",
                        "property": PROJECT_ASSIGNED_ON_PROPERTY,
                    }
                }
            ),
        )
        assert first.status_code == 200
        before = _settings_bytes()

        response = client.post(
            "/settings/capacities/save",
            headers=_auth(client),
            json=_body(
                expected_revision=0,
                types={
                    PRESS_STRUCTURE: {
                        "mode": "assignedOn",
                        "property": ADVENTURE_ASSIGNED_PROPERTY,
                    }
                },
            ),
        )

        assert response.status_code == 409
        assert _settings_bytes() == before


class TestStructureTitles:
    """The additive ``structure_titles`` map on ``GET /settings/capacities``.

    The drawer shows mapped structures only, so titles are keyed exactly by
    ``available_structures`` and read under the same (resolved vault root,
    mapping-record space, builder base URL) namespace the structure observer
    writes to. The read is machine-local and fail-soft: a cache problem never
    changes the status code or any neighbouring field."""

    def test_mapped_titles_are_returned_and_unmapped_ones_dropped(
        self, client, titles_cache
    ):
        _write_source_record([PROJECT_STRUCTURE])
        assert cst.remember_titles(
            SPACE,
            cb.CAPACITIES_BASE_URL,
            {PROJECT_STRUCTURE: "Project", PRESS_STRUCTURE: "Press"},
        ) is True

        body = client.get("/settings/capacities").json()

        assert body["available_structures"] == [PROJECT_STRUCTURE]
        # Press is not mapped, so its cached title must not surface.
        assert body["structure_titles"] == {PROJECT_STRUCTURE: "Project"}

    def test_mapped_structure_without_a_cached_title_is_absent(
        self, client, titles_cache
    ):
        _write_source_record([PROJECT_STRUCTURE, PRESS_STRUCTURE])
        assert cst.remember_titles(
            SPACE, cb.CAPACITIES_BASE_URL, {PRESS_STRUCTURE: "Press"}
        ) is True

        body = client.get("/settings/capacities").json()

        # No empty string for the untitled structure: it is simply absent.
        assert body["structure_titles"] == {PRESS_STRUCTURE: "Press"}

    def test_unmapped_vault_shows_no_titles_even_with_a_cache(
        self, client, titles_cache
    ):
        assert cst.remember_titles(
            SPACE, cb.CAPACITIES_BASE_URL, {PROJECT_STRUCTURE: "Project"}
        ) is True

        body = client.get("/settings/capacities").json()

        assert body["available_structures"] == []
        assert body["structure_titles"] == {}

    @pytest.mark.parametrize("damage", ["missing", "corrupt", "version", "directory"])
    def test_broken_cache_is_empty_and_changes_nothing_else(
        self, client, titles_cache, damage
    ):
        _write_source_record([PROJECT_STRUCTURE])
        assert cst.remember_titles(
            SPACE, cb.CAPACITIES_BASE_URL, {PROJECT_STRUCTURE: "Project"}
        ) is True
        baseline = client.get("/settings/capacities").json()
        assert baseline["structure_titles"] == {PROJECT_STRUCTURE: "Project"}

        if damage == "missing":
            titles_cache.unlink()
        elif damage == "corrupt":
            titles_cache.write_text("{not json!!", encoding="utf-8")
        elif damage == "version":
            titles_cache.write_text(
                json.dumps({"version": 2, "namespaces": {}}), encoding="utf-8"
            )
        else:
            # An unreadable path that is not a regular file.
            titles_cache.unlink()
            titles_cache.mkdir()

        response = client.get("/settings/capacities")

        assert response.status_code == 200
        body = response.json()
        assert body["structure_titles"] == {}
        # Every other field is byte-identical to the healthy read.
        assert {
            key: value
            for key, value in body.items()
            if key != "structure_titles"
        } == {
            key: value
            for key, value in baseline.items()
            if key != "structure_titles"
        }

    def test_titles_from_another_space_or_base_url_are_invisible(
        self, client, titles_cache, tmp_path
    ):
        _write_source_record([PROJECT_STRUCTURE])
        # The route reads under the builder config's base URL; a different
        # constant here would silently hide every title.
        assert cb.CapacitiesBuilderConfig().base_url == cb.CAPACITIES_BASE_URL

        assert cst.remember_titles(
            "other-space",
            cb.CAPACITIES_BASE_URL,
            {PROJECT_STRUCTURE: "Other space"},
        ) is True
        assert cst.remember_titles(
            SPACE,
            "https://other.example",
            {PROJECT_STRUCTURE: "Other provider"},
        ) is True

        assert client.get("/settings/capacities").json()["structure_titles"] == {}

        assert cst.remember_titles(
            SPACE, cb.CAPACITIES_BASE_URL, {PROJECT_STRUCTURE: "Project"}
        ) is True
        assert client.get("/settings/capacities").json()["structure_titles"] == {
            PROJECT_STRUCTURE: "Project"
        }

    def test_the_read_stays_additive_and_a_declaration_round_trips(
        self, client, titles_cache
    ):
        _write_source_record([PROJECT_STRUCTURE, PRESS_STRUCTURE])
        assert cst.remember_titles(
            SPACE,
            cb.CAPACITIES_BASE_URL,
            {PROJECT_STRUCTURE: "Project", PRESS_STRUCTURE: "Press"},
        ) is True
        declaration = {
            PROJECT_STRUCTURE: {
                "mode": "assigned",
                "property": PROJECT_ASSIGNED_PROPERTY,
            }
        }
        saved = client.post(
            "/settings/capacities/save",
            headers=_auth(client),
            json=_body(types=declaration),
        )
        assert saved.status_code == 200

        body = client.get("/settings/capacities").json()

        assert set(body) == {
            "settings",
            "persisted",
            "available_structures",
            "structure_titles",
            "declaration_diagnostics",
        }
        assert set(body["settings"]) == {
            "version",
            "revision",
            "statuses",
            "types",
            "native",
            "excluded",
        }
        assert body["persisted"] is True
        assert body["settings"]["types"] == {
            PROJECT_STRUCTURE: {
                "mode": "assigned",
                "property": PROJECT_ASSIGNED_PROPERTY,
                "statuses": None,
            }
        }
        # Untouched settings blocks keep their documented values.
        assert body["settings"]["native"] == {"deadline_horizon_days": 2}
        assert body["settings"]["excluded"] == {}
        assert body["settings"]["statuses"] == {
            "open": ["active"],
            "completed": ["Completed", "Dropped"],
        }
        assert body["structure_titles"] == {
            PROJECT_STRUCTURE: "Project",
            PRESS_STRUCTURE: "Press",
        }


class TestStructureTitlesNoProviderContact:
    def test_the_title_read_touches_no_adapter_credential_or_provider(
        self, client, monkeypatch, titles_cache
    ):
        _write_source_record([PROJECT_STRUCTURE])
        assert cst.remember_titles(
            SPACE, cb.CAPACITIES_BASE_URL, {PROJECT_STRUCTURE: "Project"}
        ) is True
        calls: list[str] = []

        def forbidden(label: str):
            def _fail(*_args, **_kwargs):
                calls.append(label)
                raise AssertionError(f"{label} must not be called")
            return _fail

        client.app.state.build_capacities_adapter = forbidden("app adapter seam")
        monkeypatch.setattr(cb, "build_capacities_adapter", forbidden("builder"))
        monkeypatch.setattr(
            cb, "load_capacities_token", forbidden("credential read")
        )
        monkeypatch.setattr(
            cb, "discover_capacities_source", forbidden("provider discovery")
        )
        monkeypatch.setattr(
            ca.CapacitiesRestClient, "__init__", forbidden("provider client")
        )
        monkeypatch.setattr(
            main_mod.external_sources,
            "fetch_capacities_items",
            forbidden("provider fetch"),
        )

        response = client.get("/settings/capacities")

        assert response.status_code == 200
        assert response.json()["structure_titles"] == {
            PROJECT_STRUCTURE: "Project"
        }
        assert calls == []


class TestPostStructureMetadata:
    """POST /settings/capacities/save returns the same advisory structure
    metadata as GET.

    The Connections drawer adopts the save response as its whole settings
    state, so a save that drops ``available_structures``/``structure_titles``
    erases the cached structure names until the drawer is reopened. Both
    routes must therefore carry the same advisory metadata, computed from the
    same machine-local mapping record and title cache with no adapter,
    credential, or provider contact."""

    def test_save_response_metadata_matches_get(self, client, titles_cache):
        _write_source_record([PROJECT_STRUCTURE, PRESS_STRUCTURE])
        assert cst.remember_titles(
            SPACE,
            cb.CAPACITIES_BASE_URL,
            {PROJECT_STRUCTURE: "Project", PRESS_STRUCTURE: "Press"},
        ) is True

        saved = client.post(
            "/settings/capacities/save",
            headers=_auth(client),
            json=_body(),
        )

        assert saved.status_code == 200
        body = saved.json()
        assert body["persisted"] is True
        assert body["available_structures"] == [
            PROJECT_STRUCTURE,
            PRESS_STRUCTURE,
        ]
        assert body["structure_titles"] == {
            PROJECT_STRUCTURE: "Project",
            PRESS_STRUCTURE: "Press",
        }
        # The save response and the next read must agree, so a drawer that
        # reopens sees no metadata delta from the save it just made.
        after = client.get("/settings/capacities").json()
        assert body["available_structures"] == after["available_structures"]
        assert body["structure_titles"] == after["structure_titles"]


class TestDeclarationDiagnostics:
    """``declaration_diagnostics`` projects dead property declarations.

    A declaration naming a property the last published contract does not
    define can never read; the drawer needs to say so before a plan read. The
    projection is bounded: one entry per dead declaration, carrying the
    structure id, its cached title, and one sentence naming the property id —
    never a property value or any other source content."""

    def _save_assigned_declaration(self, client) -> None:
        response = client.post(
            "/settings/capacities/save",
            headers=_auth(client),
            json=_body(
                types={
                    PROJECT_STRUCTURE: {
                        "mode": "assigned",
                        "property": PROJECT_ASSIGNED_PROPERTY,
                    }
                }
            ),
        )
        assert response.status_code == 200

    def test_a_dead_declaration_yields_one_bounded_diagnostic(
        self, client, monkeypatch, titles_cache
    ):
        _write_source_record([PROJECT_STRUCTURE])
        assert cst.remember_titles(
            SPACE, cb.CAPACITIES_BASE_URL, {PROJECT_STRUCTURE: "Project"}
        ) is True
        self._save_assigned_declaration(client)

        # No published contract at all: absence of evidence is not evidence of
        # drift, so nothing is reported.
        assert client.get("/settings/capacities").json()[
            "declaration_diagnostics"
        ] == []

        monkeypatch.setattr(
            main_mod,
            "_capacities_published_contract_schema",
            lambda: {PROJECT_STRUCTURE: {LIVE_ASSIGNED_PROPERTY: "boolean"}},
        )

        body = client.get("/settings/capacities").json()

        diagnostics = body["declaration_diagnostics"]
        assert len(diagnostics) == 1
        diagnostic = diagnostics[0]
        assert set(diagnostic) == {
            "structure_id",
            "structure_label",
            "reason",
        }
        assert diagnostic["structure_id"] == PROJECT_STRUCTURE
        assert diagnostic["structure_label"] == "Project"
        # The property ID is the only provider detail carried; no property
        # value, contract kind, or other structure's data leaks.
        assert PROJECT_ASSIGNED_PROPERTY in diagnostic["reason"]
        assert LIVE_ASSIGNED_PROPERTY not in diagnostic["reason"]
        assert "boolean" not in diagnostic["reason"]
        assert PRESS_STRUCTURE not in json.dumps(diagnostic)

    def test_a_live_declaration_yields_no_diagnostic(
        self, client, monkeypatch, titles_cache
    ):
        _write_source_record([PROJECT_STRUCTURE])
        assert cst.remember_titles(
            SPACE, cb.CAPACITIES_BASE_URL, {PROJECT_STRUCTURE: "Project"}
        ) is True
        self._save_assigned_declaration(client)

        monkeypatch.setattr(
            main_mod,
            "_capacities_published_contract_schema",
            lambda: {PROJECT_STRUCTURE: {PROJECT_ASSIGNED_PROPERTY: "checkbox"}},
        )

        assert client.get("/settings/capacities").json()[
            "declaration_diagnostics"
        ] == []

    def test_only_the_two_property_modes_are_inspected(
        self, client, monkeypatch, titles_cache
    ):
        _write_source_record([PROJECT_STRUCTURE, PRESS_STRUCTURE])
        assert cst.remember_titles(
            SPACE, cb.CAPACITIES_BASE_URL, {PROJECT_STRUCTURE: "Project"}
        ) is True
        saved = client.post(
            "/settings/capacities/save",
            headers=_auth(client),
            json=_body(
                types={
                    PROJECT_STRUCTURE: {"mode": "active"},
                    PRESS_STRUCTURE: {"mode": "native", "statuses": []},
                }
            ),
        )
        assert saved.status_code == 200

        def forbidden():
            raise AssertionError(
                "no contract is needed when nothing declares a property"
            )

        monkeypatch.setattr(
            main_mod, "_capacities_published_contract_schema", forbidden
        )

        assert client.get("/settings/capacities").json()[
            "declaration_diagnostics"
        ] == []


class TestVersionOneMigration:
    """A version-1 file is translated on read and rewritten on first save.

    ``GET`` reports the migration additively (``migrated_from: 1``) and writes
    nothing. The first ``POST`` rewrites the document as version 2 and, before
    replacing it, drops a ``.bak-v1-<timestamp>`` sibling holding the previous
    bytes byte-for-byte so the pre-migration shape is recoverable."""

    def _write_v1(self, revision: int = 3) -> bytes:
        path = cs.settings_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = _v1_bytes(revision)
        path.write_bytes(raw)
        return raw

    def test_get_translates_v1_in_memory_and_reports_the_migration(self, client):
        raw = self._write_v1()

        response = client.get("/settings/capacities")

        assert response.status_code == 200
        body = response.json()
        assert body["persisted"] is True
        assert body["migrated_from"] == 1
        assert body["settings"]["version"] == 2
        # The revision is carried verbatim so the client can still save.
        assert body["settings"]["revision"] == 3
        assert body["settings"]["statuses"] == {
            "open": ["In Progress"],
            "completed": ["Completed", "Dropped"],
        }
        assert body["settings"]["native"] == {"deadline_horizon_days": 5}
        assert body["settings"]["excluded"] == {NATIVE: True}
        assert body["settings"]["types"] == {
            PROJECT_STRUCTURE: {
                "mode": "active",
                "property": None,
                "statuses": None,
            },
            PRESS_STRUCTURE: {
                "mode": "assigned",
                "property": PROJECT_ASSIGNED_PROPERTY,
                "statuses": None,
            },
        }
        # Reading never rewrites and never takes a backup.
        assert _settings_bytes() == raw
        assert _backup_siblings() == []

    def test_first_save_rewrites_as_v2_and_keeps_a_v1_backup(self, client):
        raw = self._write_v1()

        response = client.post(
            "/settings/capacities/save",
            headers=_auth(client),
            json=_body(expected_revision=3, excluded={}),
        )

        assert response.status_code == 200
        assert response.json()["settings"]["version"] == 2
        assert response.json()["settings"]["revision"] == 4
        # The rewrite is version 2 on disk, and the migration is no longer
        # reported because the file itself is current.
        stored = _stored_document()
        assert stored["version"] == 2
        assert stored["revision"] == 4
        assert "migrated_from" not in client.get("/settings/capacities").json()

        backups = _backup_siblings()
        assert len(backups) == 1
        assert backups[0].name.startswith(
            f"{cs.STATE_FILENAME}{cs.BACKUP_INFIX}"
        )
        assert backups[0].read_bytes() == raw

    def test_the_backup_is_the_pre_save_bytes_after_an_edited_get(self, client):
        raw = self._write_v1(revision=0)
        body = client.get("/settings/capacities").json()
        assert body["migrated_from"] == 1

        # Save from the migrated view, changing only the exclusion set.
        response = client.post(
            "/settings/capacities/save",
            headers=_auth(client),
            json=_body(expected_revision=0, excluded={CUSTOM: True}),
        )

        assert response.status_code == 200
        assert response.json()["settings"]["revision"] == 1
        assert response.json()["settings"]["excluded"] == {CUSTOM: True}
        backups = _backup_siblings()
        assert len(backups) == 1
        assert backups[0].read_bytes() == raw
        assert _stored_document()["excluded"] == {CUSTOM: True}

    def test_a_stale_v1_save_conflicts_and_keeps_the_bytes_and_no_backup(
        self, client
    ):
        raw = self._write_v1(revision=3)

        response = client.post(
            "/settings/capacities/save",
            headers=_auth(client),
            json=_body(expected_revision=0),
        )

        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "capacities_settings_conflict"
        assert _settings_bytes() == raw
        assert _backup_siblings() == []
