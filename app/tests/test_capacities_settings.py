"""Public-interface tests for the Capacities settings store (schema version 2).

Everything here is local and fake: no provider, credential, network, or live
source is touched. The tests drive only ``capacities_settings``' public
surface (``read_settings`` / ``save_settings`` / the strict model) plus its
path helpers. Version-1 reading and the migration backup are covered by
``test_capacities_settings_v2.py``.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import app_config  # noqa: E402
import capacities_settings as cs  # noqa: E402


SPACE = "space-1"
NATIVE = f"capacities:{SPACE}:RootTask:task-1"
CUSTOM = f"capacities:{SPACE}:custom-project:project-1"

PROJECT_STRUCTURE = "0d194525-c5a1-4af5-bb62-202b83006b5e"
PROJECT_PROPERTY = "f779f78a-3b1e-4a2c-9f5d-6a0f0e1d2c3b"


def _write_raw(text: str) -> Path:
    path = cs.settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _write_json(data) -> Path:
    return _write_raw(json.dumps(data))


def _bytes() -> bytes:
    return cs.settings_path().read_bytes()


def _valid_payload(**overrides) -> dict:
    payload = {
        "version": 3,
        "revision": 0,
        "statuses": {"open": ["active"], "completed": ["Completed", "Dropped"]},
        "types": {},
        "native": {"deadline_horizon_days": 2},
        "read_source": "rest",
        "excluded": {},
    }
    payload.update(overrides)
    return payload


def _saved(**kwargs):
    kwargs.setdefault("expected_revision", 0)
    return cs.save_settings(**kwargs)


def _validated(**kwargs):
    """Validate save inputs without touching a file (validation is pre-I/O)."""
    return cs.save_settings(expected_revision=0, **kwargs)


# ---------------------------------------------------------------------------
# Paths and missing-file default
# ---------------------------------------------------------------------------


class TestPathsAndDefault:
    def test_schema_version_is_three(self):
        assert cs.SCHEMA_VERSION == 3

    def test_paths_derive_only_from_app_home(self, tmp_path):
        assert cs.settings_path() == app_config.state_dir() / "capacities-settings.json"
        assert cs.lock_path() == app_config.state_dir() / "capacities-settings.lock"
        assert cs.settings_path().is_relative_to(app_config.state_dir())
        assert cs.lock_path().is_relative_to(app_config.state_dir())

    def test_missing_file_returns_default_without_creating_it(self, tmp_path):
        result = cs.read_settings()

        assert result.persisted is False
        assert result.source_version is None
        assert result.settings.revision == 0
        assert result.settings.statuses.open == frozenset({"active"})
        assert result.settings.statuses.completed == frozenset(
            {"Completed", "Dropped"}
        )
        assert result.settings.native.deadline_horizon_days == 2
        assert result.settings.types == {}
        assert result.settings.excluded == frozenset()
        assert not cs.settings_path().exists()
        assert not cs.lock_path().exists()

    def test_default_settings_match_the_persisted_shape(self, tmp_path):
        result = cs.read_settings()

        assert result.settings.as_dict() == _valid_payload()

    def test_vault_isolation(self, tmp_path, monkeypatch):
        """S1: the store is machine-local, so isolation is by app home
        (``TDTB_HOME``), not by vault root."""
        home_a = tmp_path / "home-a"
        home_b = tmp_path / "home-b"
        monkeypatch.setenv("TDTB_HOME", str(home_a))
        _saved(excluded=[NATIVE])
        assert cs.read_settings().persisted is True
        assert cs.read_settings().settings.excluded == frozenset({NATIVE})

        monkeypatch.setenv("TDTB_HOME", str(home_b))
        assert cs.read_settings().persisted is False


# ---------------------------------------------------------------------------
# Round-trip
# ---------------------------------------------------------------------------


class TestRoundTrip:
    def test_save_round_trips_across_reads(self, tmp_path):
        saved = _saved(
            statuses={"open": ["In Progress"], "completed": ["Completed"]},
            types={
                "RootTask": {"mode": "native", "statuses": []},
                PROJECT_STRUCTURE: {
                    "mode": "assigned",
                    "property": PROJECT_PROPERTY,
                },
            },
            native={"deadline_horizon_days": 14},
            excluded=[NATIVE],
        )

        result = cs.read_settings()

        assert result.persisted is True
        assert result.source_version == 3
        assert result.settings == saved
        assert result.settings.statuses.open == frozenset({"In Progress"})
        assert result.settings.native.deadline_horizon_days == 14
        assert result.settings.excluded == frozenset({NATIVE})
        assert result.settings.types["RootTask"].statuses == ()
        assert (
            result.settings.types[PROJECT_STRUCTURE].property_id == PROJECT_PROPERTY
        )

    def test_written_bytes_are_sorted_and_deterministic(self, tmp_path):
        _saved(types={"zeta": {"mode": "active"}, "alpha": {"mode": "off"}})

        data = json.loads(_bytes().decode("utf-8"))

        assert list(data["types"]) == ["alpha", "zeta"]
        assert data["types"]["alpha"] == {
            "mode": "off",
            "property": None,
            "statuses": None,
        }
        assert data["statuses"]["completed"] == ["Completed", "Dropped"]

    def test_constructed_model_is_strict(self):
        with pytest.raises(ValueError):
            cs.CapacitiesSettings(revision=-1)
        with pytest.raises(ValueError):
            cs.CapacitiesSettings(revision=True)
        with pytest.raises(ValueError):
            cs.CapacitiesSettings(excluded=["task-1"])
        with pytest.raises(ValueError):
            cs.CapacitiesSettings(native={"deadline_horizon_days": 2})
        with pytest.raises(ValueError):
            cs.NativePolicy(deadline_horizon_days=True)
        with pytest.raises(ValueError):
            cs.NativePolicy(deadline_horizon_days=-1)
        with pytest.raises(ValueError):
            cs.StatusVocabulary(open=[""])
        with pytest.raises(ValueError):
            cs.StatusVocabulary(open=["active", "active"])


# ---------------------------------------------------------------------------
# Canonical identity
# ---------------------------------------------------------------------------


class TestCanonicalIdentity:
    @pytest.mark.parametrize(
        "bad",
        ["", "task-1", "capacities:space-1:RootTask", NATIVE + " ", " " + NATIVE],
    )
    def test_non_canonical_identities_are_rejected(self, bad):
        with pytest.raises(ValueError):
            cs.canonical_exclusion_identity(bad)

    def test_canonical_identity_is_returned_unchanged(self):
        assert cs.canonical_exclusion_identity(NATIVE) == NATIVE

    def test_non_canonical_exclusion_fails_the_save_before_io(self, tmp_path):
        with pytest.raises(ValueError):
            _saved(excluded=["task-1"])
        assert not cs.settings_path().exists()

    def test_bad_exclusion_identity_fails_the_read(self, tmp_path):
        _write_json(_valid_payload(excluded={"task-1": True}))
        with pytest.raises(cs.SettingsFormatError):
            cs.read_settings()


# ---------------------------------------------------------------------------
# Strict decoding (version 2)
# ---------------------------------------------------------------------------


class TestStrictDecoding:
    @pytest.mark.parametrize(
        "raw",
        ["{not json!!", "", "[]", '"a string"', "42", "null", b"\xff\xfe"],
    )
    def test_malformed_or_non_object_documents_fail_closed(self, tmp_path, raw):
        path = cs.settings_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(raw, bytes):
            path.write_bytes(raw)
        else:
            path.write_text(raw, encoding="utf-8")
        with pytest.raises(cs.SettingsStoreError):
            cs.read_settings()
        assert path.read_bytes() == (raw if isinstance(raw, bytes) else raw.encode())

    @pytest.mark.parametrize(
        "payload",
        [
            _valid_payload(extra_key="x"),                              # unknown top-level
            {k: v for k, v in _valid_payload().items() if k != "native"},  # missing key
            _valid_payload(version=True),
            _valid_payload(version=4),
            _valid_payload(version="2"),
            _valid_payload(revision=True),
            _valid_payload(revision=-1),
            _valid_payload(revision=1.5),
            _valid_payload(statuses={"open": ["active"]}),               # missing completed
            _valid_payload(statuses={"open": ["active"], "completed": ["x"], "y": []}),
            _valid_payload(statuses={"open": "active", "completed": []}),  # not a list
            _valid_payload(statuses={"open": [""], "completed": []}),      # empty name
            _valid_payload(statuses={"open": ["a", "a"], "completed": []}),  # duplicate
            _valid_payload(native={}),                                     # missing horizon
            _valid_payload(native={"deadline_horizon_days": 2, "x": 1}),
            _valid_payload(native={"deadline_horizon_days": True}),
            _valid_payload(native={"deadline_horizon_days": -1}),
            _valid_payload(native={"deadline_horizon_days": 2.5}),
            _valid_payload(types=[]),                                      # not an object
            _valid_payload(types={"s": {"mode": "active", "extra": 1}}),
            _valid_payload(types={"s": {"property": "p"}}),                # missing mode
            _valid_payload(types={"s": {"mode": "active", "statuses": "x"}}),
            _valid_payload(types={"s": "active"}),                         # not an object
            _valid_payload(types={" ": {"mode": "active"}}),               # bad structure id
            _valid_payload(excluded=[]),
            _valid_payload(excluded={NATIVE: False}),
            _valid_payload(excluded={NATIVE: 1}),
            _valid_payload(excluded={NATIVE: None}),
        ],
    )
    def test_strict_shape_violations_fail_closed(self, tmp_path, payload):
        _write_json(payload)
        before = _bytes()
        with pytest.raises(cs.SettingsFormatError):
            cs.read_settings()
        assert _bytes() == before

    def test_duplicate_top_level_keys_are_rejected(self, tmp_path):
        _write_raw(
            '{"version": 2, "revision": 0, "revision": 1, '
            '"statuses": {"open": ["active"], "completed": []}, '
            '"types": {}, "native": {"deadline_horizon_days": 2}, "excluded": {}}'
        )
        with pytest.raises(cs.SettingsFormatError):
            cs.read_settings()

    def test_duplicate_type_keys_are_rejected(self, tmp_path):
        _write_raw(
            '{"version": 2, "revision": 0, '
            '"statuses": {"open": ["active"], "completed": []}, '
            '"types": {"s": {"mode": "off"}, "s": {"mode": "active"}}, '
            '"native": {"deadline_horizon_days": 2}, "excluded": {}}'
        )
        with pytest.raises(cs.SettingsFormatError):
            cs.read_settings()

    def test_valid_document_decodes_exactly(self, tmp_path):
        _write_json(
            _valid_payload(
                revision=4,
                statuses={"open": ["In Progress"], "completed": ["Done"]},
                native={"deadline_horizon_days": 9},
                excluded={NATIVE: True},
            )
        )

        result = cs.read_settings()

        assert result.persisted is True
        assert result.settings.revision == 4
        assert result.settings.as_dict() == _valid_payload(
            revision=4,
            statuses={"open": ["In Progress"], "completed": ["Done"]},
            native={"deadline_horizon_days": 9},
            excluded={NATIVE: True},
        )


# ---------------------------------------------------------------------------
# Revisions and conflicts
# ---------------------------------------------------------------------------


class TestRevisions:
    def test_first_save_creates_revision_one(self, tmp_path):
        saved = _saved()
        assert saved.revision == 1
        assert cs.read_settings().settings.revision == 1

    def test_revision_increments_on_each_save(self, tmp_path):
        first = _saved()
        second = _saved(expected_revision=first.revision, excluded=[NATIVE])
        assert (first.revision, second.revision) == (1, 2)
        assert cs.read_settings().settings.revision == 2

    def test_stale_expected_revision_conflicts_and_preserves_bytes(self, tmp_path):
        _saved()
        before = _bytes()
        with pytest.raises(cs.SettingsConflictError):
            _saved(expected_revision=0, excluded=[NATIVE])
        assert _bytes() == before

    def test_conflict_is_a_store_error_naming_both_revisions(self, tmp_path):
        _saved()
        with pytest.raises(cs.SettingsStoreError) as excinfo:
            _saved(expected_revision=0)
        message = str(excinfo.value)
        assert "stored revision 1" in message
        assert "expected 0" in message

    def test_missing_file_with_nonzero_expected_revision_conflicts(self, tmp_path):
        with pytest.raises(cs.SettingsConflictError):
            _saved(expected_revision=1)
        assert not cs.settings_path().exists()

    def test_save_rejects_bad_expected_revision_before_file_access(self, tmp_path):
        for bad in (True, -1, 1.5, "0", None):
            with pytest.raises(ValueError):
                cs.save_settings(expected_revision=bad)
        assert not cs.settings_path().exists()


# ---------------------------------------------------------------------------
# Fail-closed writes and atomic replace
# ---------------------------------------------------------------------------


class TestFailClosedWrites:
    def test_save_fails_closed_on_malformed_file(self, tmp_path):
        _write_raw("garbage")
        with pytest.raises(cs.SettingsFormatError):
            _saved()
        assert _bytes() == b"garbage"

    def test_save_fails_closed_on_unsupported_version(self, tmp_path):
        _write_json(_valid_payload(version=99))
        before = _bytes()
        with pytest.raises(cs.SettingsFormatError):
            _saved()
        assert _bytes() == before

    def test_save_fails_closed_on_lock_failure(self, tmp_path, monkeypatch):
        _saved()
        before = _bytes()

        def boom():
            raise OSError("lock unavailable")

        monkeypatch.setattr(cs, "_acquire_lock_file", boom)
        with pytest.raises(OSError):
            _saved(expected_revision=1, excluded=[NATIVE])
        assert _bytes() == before

    def test_save_fails_closed_on_write_failure(self, tmp_path, monkeypatch):
        _saved()
        before = _bytes()

        def boom(path, data):
            raise OSError("write failed")

        monkeypatch.setattr(cs, "_atomic_write_json", boom)
        with pytest.raises(OSError):
            _saved(expected_revision=1, excluded=[NATIVE])
        assert _bytes() == before

    def test_read_fails_closed_on_directory_at_settings_path(self, tmp_path):
        cs.settings_path().mkdir(parents=True)
        with pytest.raises(cs.SettingsStoreError):
            cs.read_settings()

    def test_atomic_write_leaves_no_temp_files(self, tmp_path):
        _saved()
        leftovers = [
            entry.name
            for entry in cs.settings_path().parent.iterdir()
            if entry.name.endswith(".tmp")
        ]
        assert leftovers == []

    def test_lock_file_created_under_app_home(self, tmp_path):
        _saved()
        assert cs.lock_path().is_file()


# ---------------------------------------------------------------------------
# The types document
# ---------------------------------------------------------------------------


class TestTypeDeclarations:
    def test_undeclared_structure_is_off_and_never_admits_on_status(self, tmp_path):
        settings = cs.read_settings().settings

        assert settings.mode_of("unknown") == "off"
        assert settings.type_declaration("unknown").mode == "off"
        assert settings.status_admission_enabled("unknown") is False
        assert settings.open_statuses_for("unknown") == frozenset()

    def test_declarations_round_trip_for_every_mode(self, tmp_path):
        saved = _saved(
            types={
                "off-structure": {"mode": "off"},
                "active-structure": {"mode": "active"},
                "assigned-structure": {"mode": "assigned", "property": "p-1"},
                "assigned-on-structure": {"mode": "assignedOn", "property": "p-2"},
                "native-structure": {"mode": "native"},
            }
        )

        assert saved.as_dict()["types"] == {
            "active-structure": {"mode": "active", "property": None, "statuses": None},
            "assigned-on-structure": {
                "mode": "assignedOn",
                "property": "p-2",
                "statuses": None,
            },
            "assigned-structure": {
                "mode": "assigned",
                "property": "p-1",
                "statuses": None,
            },
            "native-structure": {"mode": "native", "property": None, "statuses": None},
            "off-structure": {"mode": "off", "property": None, "statuses": None},
        }
        assert cs.read_settings().settings == saved

    def test_property_ids_are_preserved_exactly_as_declared(self, tmp_path):
        saved = _saved(
            types={"s": {"mode": "assigned", "property": "Assigned-ID_CaseSensitive"}}
        )
        assert (
            cs.read_settings().settings.types["s"].property_id
            == "Assigned-ID_CaseSensitive"
        )
        assert saved.types["s"].property_id == "Assigned-ID_CaseSensitive"

    def test_omitted_types_replace_to_no_declarations(self, tmp_path):
        _saved(types={"s": {"mode": "active"}})
        saved = _saved(expected_revision=1)
        assert saved.types == {}
        assert json.loads(_bytes().decode("utf-8"))["types"] == {}

    def test_invalid_structure_ids_are_rejected_before_io(self):
        for bad in (" ", "", 1):
            with pytest.raises(ValueError):
                _validated(types={bad: {"mode": "active"}})

    def test_structure_ids_accessor_filters_by_mode(self, tmp_path):
        saved = _saved(
            types={
                "b": {"mode": "native"},
                "a": {"mode": "native"},
                "c": {"mode": "active"},
            }
        )
        assert saved.structure_ids("native") == ("a", "b")
        assert saved.structure_ids() == ("a", "b", "c")


# ---------------------------------------------------------------------------
# The statuses document
# ---------------------------------------------------------------------------


class TestStatusVocabulary:
    def test_defaults(self):
        vocabulary = cs.StatusVocabulary()
        assert vocabulary.open == frozenset({"active"})
        assert vocabulary.completed == frozenset({"Completed", "Dropped"})
        assert vocabulary.as_dict() == {
            "open": ["active"],
            "completed": ["Completed", "Dropped"],
        }

    def test_statuses_round_trip_verbatim(self, tmp_path):
        saved = _saved(
            statuses={"open": ["In Progress", "Next"], "completed": ["Closed"]}
        )
        assert saved.statuses.open == frozenset({"In Progress", "Next"})
        assert cs.read_settings().settings.statuses.completed == frozenset(
            {"Closed"}
        )

    def test_status_names_are_never_pre_normalized(self, tmp_path):
        saved = _saved(statuses={"open": ["In Progress"], "completed": []})
        assert saved.statuses.open == frozenset({"In Progress"})

    def test_invalid_status_inputs_are_rejected_before_io(self):
        for bad in (
            {"open": "active", "completed": []},
            {"open": [""], "completed": []},
            {"open": ["a", "a"], "completed": []},
            {"open": [1], "completed": []},
            {"open": ["a"], "completed": [], "extra": []},
            ["active"],
        ):
            with pytest.raises(ValueError):
                _validated(statuses=bad)

    def test_native_policy_inputs_are_rejected_before_io(self):
        for bad in (
            {"deadline_horizon_days": -1},
            {"deadline_horizon_days": True},
            {"deadline_horizon_days": 1.5},
            {},
            {"deadline_horizon_days": 1, "extra": 1},
            [1],
        ):
            with pytest.raises(ValueError):
                _validated(native=bad)
