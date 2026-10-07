"""Public-interface tests for the vault-scoped Capacities settings store.

Everything here is local and fake: no provider, credential, network, or live
source is touched. The tests drive only ``capacities_settings``' public surface
(``read_settings`` / ``save_settings`` / the strict model) plus its path
helpers.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import capacities_settings as cs  # noqa: E402


SPACE = "space-1"
NATIVE = f"capacities:{SPACE}:RootTask:task-1"
CUSTOM = f"capacities:{SPACE}:custom-project:project-1"

#: Documented defaults for the two additive version-1 admission inputs.
DEFAULT_NATIVE_STRUCTURES = ["RootTask", "Task"]
DEFAULT_ACTIVE_STATUSES = ["active"]


def _write_raw(vault_root, text: str) -> Path:
    path = cs.settings_path(vault_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _write_json(vault_root, data) -> Path:
    return _write_raw(vault_root, json.dumps(data))


def _bytes(vault_root) -> bytes:
    return cs.settings_path(vault_root).read_bytes()


def _valid_payload(**overrides) -> dict:
    payload = {
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
    payload.update(overrides)
    return payload


def _default_policy() -> cs.NativeTaskAutoPolicy:
    return cs.NativeTaskAutoPolicy()


# ---------------------------------------------------------------------------
# Paths and missing-file default
# ---------------------------------------------------------------------------


class TestPathsAndDefault:
    def test_paths_derive_only_from_vault_root(self, tmp_path):
        v1 = tmp_path / "vault-a"
        v2 = tmp_path / "vault-b"
        assert cs.settings_path(v1) == v1 / "00 - META/Cache/tdtb-capacities-settings.json"
        assert cs.lock_path(v1) == v1 / "00 - META/Cache/tdtb-capacities-settings.lock"
        assert cs.settings_path(v1) != cs.settings_path(v2)
        assert cs.settings_path(v1).is_relative_to(v1)
        assert cs.lock_path(v1).is_relative_to(v1)

    def test_missing_file_returns_default_without_creating_it(self, tmp_path):
        result = cs.read_settings(tmp_path)

        assert result.persisted is False
        assert result.settings.revision == 0
        policy = result.settings.native_task_auto
        assert policy.active_enabled is True
        assert policy.due_enabled is True
        assert policy.deadline_enabled is True
        assert policy.deadline_horizon_days == 2
        assert result.settings.excluded == frozenset()
        assert not cs.settings_path(tmp_path).exists()
        assert not cs.lock_path(tmp_path).exists()

    def test_default_settings_match_the_persisted_shape(self, tmp_path):
        result = cs.read_settings(tmp_path)
        expected = _valid_payload()
        expected["native_task_structures"] = DEFAULT_NATIVE_STRUCTURES
        expected["active_statuses"] = DEFAULT_ACTIVE_STATUSES
        assert result.settings.as_dict() == expected

    def test_vault_isolation(self, tmp_path):
        a = tmp_path / "vault-a"
        b = tmp_path / "vault-b"
        cs.save_settings(
            a,
            expected_revision=0,
            native_task_auto=_default_policy(),
            excluded=[NATIVE],
        )
        assert cs.read_settings(a).persisted is True
        assert cs.read_settings(a).settings.excluded == frozenset({NATIVE})
        assert cs.read_settings(b).persisted is False


# ---------------------------------------------------------------------------
# Round-trip
# ---------------------------------------------------------------------------


class TestRoundTrip:
    def test_save_round_trips_across_reads(self, tmp_path):
        saved = cs.save_settings(
            tmp_path,
            expected_revision=0,
            native_task_auto=cs.NativeTaskAutoPolicy(
                active_enabled=False,
                due_enabled=True,
                deadline_enabled=False,
                deadline_horizon_days=7,
            ),
            excluded=[NATIVE, CUSTOM],
        )

        assert saved.revision == 1
        assert saved.excluded == frozenset({NATIVE, CUSTOM})

        result = cs.read_settings(tmp_path)
        assert result.persisted is True
        assert result.settings == saved
        assert result.settings.native_task_auto.deadline_horizon_days == 7
        assert result.settings.native_task_auto.active_enabled is False
        assert result.settings.native_task_auto.deadline_enabled is False

    def test_persisted_file_has_exactly_the_versioned_shape(self, tmp_path):
        cs.save_settings(
            tmp_path,
            expected_revision=0,
            native_task_auto=_default_policy(),
            excluded=[NATIVE],
        )
        data = json.loads(_bytes(tmp_path).decode("utf-8"))

        assert data == {
            "version": 1,
            "revision": 1,
            "native_task_auto": {
                "active_enabled": True,
                "due_enabled": True,
                "deadline_enabled": True,
                "deadline_horizon_days": 2,
            },
            "excluded": {NATIVE: True},
            "active_structures": {},
            "native_task_structures": DEFAULT_NATIVE_STRUCTURES,
            "active_statuses": DEFAULT_ACTIVE_STATUSES,
        }

    def test_saved_settings_bridge_to_the_evaluator_seam(self, tmp_path):
        saved = cs.save_settings(
            tmp_path,
            expected_revision=0,
            native_task_auto=cs.NativeTaskAutoPolicy(
                active_enabled=False, due_enabled=False, deadline_enabled=False,
                deadline_horizon_days=5,
            ),
            excluded=[NATIVE],
        )
        seam = saved.to_assignment_settings()

        assert seam.excluded_identities == frozenset({NATIVE})
        assert seam.deadline_horizon_days == 5
        assert seam.active_enabled is False
        assert seam.due_enabled is False
        assert seam.deadline_enabled is False

    def test_large_horizon_round_trips_without_an_arbitrary_cap(self, tmp_path):
        saved = cs.save_settings(
            tmp_path,
            expected_revision=0,
            native_task_auto=cs.NativeTaskAutoPolicy(deadline_horizon_days=10**12),
            excluded=[],
        )
        assert cs.read_settings(tmp_path).settings == saved
        assert saved.native_task_auto.deadline_horizon_days == 10**12

    def test_exclusion_keys_are_written_sorted_and_deterministic(self, tmp_path):
        cs.save_settings(
            tmp_path,
            expected_revision=0,
            native_task_auto=_default_policy(),
            excluded=[CUSTOM, NATIVE],
        )
        data = json.loads(_bytes(tmp_path).decode("utf-8"))
        assert list(data["excluded"]) == sorted([CUSTOM, NATIVE])


# ---------------------------------------------------------------------------
# Canonical identity rejection
# ---------------------------------------------------------------------------


class TestCanonicalIdentity:
    def test_canonical_identity_round_trips(self):
        assert cs.canonical_exclusion_identity(NATIVE) == NATIVE
        assert cs.canonical_exclusion_identity(CUSTOM) == CUSTOM

    @pytest.mark.parametrize(
        "value",
        [
            "task-1",                                   # bare id
            "Write brief",                              # title
            f"  {NATIVE}  ",                            # whitespace alias
            f"capacities:{SPACE}:RootTask: task-1",     # inner whitespace
            f"Capacities:{SPACE}:RootTask:task-1",      # non-canonical casing
            "todoist:1:2:3",                            # wrong source
            f"capacities:{SPACE}:RootTask",             # missing component
            "",
        ],
    )
    def test_non_canonical_identities_are_rejected(self, value):
        with pytest.raises(ValueError):
            cs.canonical_exclusion_identity(value)

    def test_save_rejects_invalid_identity_before_any_file_access(self, tmp_path):
        with pytest.raises(ValueError):
            cs.save_settings(
                tmp_path,
                expected_revision=0,
                native_task_auto=_default_policy(),
                excluded=["task-1"],
            )
        assert not cs.settings_path(tmp_path).exists()
        assert not cs.lock_path(tmp_path).exists()

    def test_file_with_invalid_exclusion_identity_fails_closed(self, tmp_path):
        _write_json(tmp_path, _valid_payload(excluded={"task-1": True}))
        before = _bytes(tmp_path)
        with pytest.raises(cs.SettingsFormatError):
            cs.read_settings(tmp_path)
        with pytest.raises(cs.SettingsFormatError):
            cs.save_settings(
                tmp_path,
                expected_revision=0,
                native_task_auto=_default_policy(),
                excluded=[],
            )
        assert _bytes(tmp_path) == before


# ---------------------------------------------------------------------------
# Strict decoding
# ---------------------------------------------------------------------------


class TestStrictDecoding:
    @pytest.mark.parametrize(
        "raw",
        [
            "{not json!!",
            "",
            "[]",
            '"a string"',
            "42",
            "null",
        ],
    )
    def test_malformed_or_non_object_documents_fail_closed(self, tmp_path, raw):
        _write_raw(tmp_path, raw)
        with pytest.raises(cs.SettingsFormatError):
            cs.read_settings(tmp_path)
        assert _bytes(tmp_path) == raw.encode("utf-8")

    @pytest.mark.parametrize(
        "payload",
        [
            _valid_payload(extra_key="x"),                         # unknown top-level
            {k: v for k, v in _valid_payload().items() if k != "revision"},  # missing key
            _valid_payload(revision=True),                          # bool revision
            _valid_payload(revision=-1),                            # negative revision
            _valid_payload(revision=1.5),                           # float revision
            _valid_payload(version=True),                           # bool version
            _valid_payload(version=2),                              # unsupported version
            _valid_payload(version="1"),                            # string version
            _valid_payload(native_task_auto={"active_enabled": True}),  # missing native keys
            _valid_payload(native_task_auto={
                "active_enabled": True, "due_enabled": True,
                "deadline_enabled": True, "deadline_horizon_days": 2, "extra": 1,
            }),                                                     # unknown native key
            _valid_payload(native_task_auto={
                "active_enabled": 1, "due_enabled": True,
                "deadline_enabled": True, "deadline_horizon_days": 2,
            }),                                                     # int bool
            _valid_payload(native_task_auto={
                "active_enabled": True, "due_enabled": True,
                "deadline_enabled": True, "deadline_horizon_days": True,
            }),                                                     # bool horizon
            _valid_payload(native_task_auto={
                "active_enabled": True, "due_enabled": True,
                "deadline_enabled": True, "deadline_horizon_days": 2.5,
            }),                                                     # float horizon
            _valid_payload(native_task_auto={
                "active_enabled": True, "due_enabled": True,
                "deadline_enabled": True, "deadline_horizon_days": -1,
            }),                                                     # negative horizon
            _valid_payload(excluded=[]),                            # excluded not object
            _valid_payload(excluded={NATIVE: False}),               # flag false
            _valid_payload(excluded={NATIVE: 1}),                   # flag int
            _valid_payload(excluded={NATIVE: None}),                # flag null
        ],
    )
    def test_strict_shape_violations_fail_closed(self, tmp_path, payload):
        _write_json(tmp_path, payload)
        before = _bytes(tmp_path)
        with pytest.raises(cs.SettingsFormatError):
            cs.read_settings(tmp_path)
        assert _bytes(tmp_path) == before

    def test_duplicate_top_level_keys_are_rejected(self, tmp_path):
        _write_raw(
            tmp_path,
            '{"version": 1, "revision": 0, "revision": 1, '
            '"native_task_auto": {"active_enabled": true, "due_enabled": true, '
            '"deadline_enabled": true, "deadline_horizon_days": 2}, "excluded": {}}',
        )
        with pytest.raises(cs.SettingsFormatError):
            cs.read_settings(tmp_path)

    def test_duplicate_nested_keys_are_rejected(self, tmp_path):
        _write_raw(
            tmp_path,
            '{"version": 1, "revision": 0, '
            '"native_task_auto": {"active_enabled": true, "active_enabled": false, '
            '"due_enabled": true, "deadline_enabled": true, '
            '"deadline_horizon_days": 2}, "excluded": {}}',
        )
        with pytest.raises(cs.SettingsFormatError):
            cs.read_settings(tmp_path)

    def test_duplicate_exclusion_keys_are_rejected(self, tmp_path):
        _write_raw(
            tmp_path,
            '{"version": 1, "revision": 0, '
            '"native_task_auto": {"active_enabled": true, "due_enabled": true, '
            '"deadline_enabled": true, "deadline_horizon_days": 2}, '
            f'"excluded": {{"{NATIVE}": true, "{NATIVE}": true}}}}',
        )
        with pytest.raises(cs.SettingsFormatError):
            cs.read_settings(tmp_path)

    def test_valid_document_decodes_exactly(self, tmp_path):
        _write_json(
            tmp_path,
            _valid_payload(
                revision=4,
                native_task_auto={
                    "active_enabled": False, "due_enabled": True,
                    "deadline_enabled": False, "deadline_horizon_days": 9,
                },
                excluded={NATIVE: True},
            ),
        )
        result = cs.read_settings(tmp_path)
        assert result.persisted is True
        assert result.settings.revision == 4
        assert result.settings.native_task_auto.as_dict() == {
            "active_enabled": False, "due_enabled": True,
            "deadline_enabled": False, "deadline_horizon_days": 9,
        }
        assert result.settings.excluded == frozenset({NATIVE})

    def test_constructed_model_is_strict(self):
        with pytest.raises(ValueError):
            cs.NativeTaskAutoPolicy(active_enabled=1)
        with pytest.raises(ValueError):
            cs.NativeTaskAutoPolicy(deadline_horizon_days=True)
        with pytest.raises(ValueError):
            cs.NativeTaskAutoPolicy(deadline_horizon_days=-1)
        with pytest.raises(ValueError):
            cs.CapacitiesSettings(revision=-1)
        with pytest.raises(ValueError):
            cs.CapacitiesSettings(excluded=["task-1"])


# ---------------------------------------------------------------------------
# Revisions and conflicts
# ---------------------------------------------------------------------------


class TestRevisions:
    def test_first_save_creates_revision_one(self, tmp_path):
        saved = cs.save_settings(
            tmp_path, expected_revision=0,
            native_task_auto=_default_policy(), excluded=[],
        )
        assert saved.revision == 1
        assert cs.read_settings(tmp_path).settings.revision == 1

    def test_revision_increments_on_each_save(self, tmp_path):
        first = cs.save_settings(
            tmp_path, expected_revision=0,
            native_task_auto=_default_policy(), excluded=[],
        )
        second = cs.save_settings(
            tmp_path, expected_revision=first.revision,
            native_task_auto=_default_policy(), excluded=[NATIVE],
        )
        assert (first.revision, second.revision) == (1, 2)
        assert cs.read_settings(tmp_path).settings.revision == 2

    def test_stale_expected_revision_conflicts_and_preserves_bytes(self, tmp_path):
        cs.save_settings(
            tmp_path, expected_revision=0,
            native_task_auto=_default_policy(), excluded=[],
        )
        before = _bytes(tmp_path)
        with pytest.raises(cs.SettingsConflictError):
            cs.save_settings(
                tmp_path, expected_revision=0,
                native_task_auto=_default_policy(), excluded=[NATIVE],
            )
        assert _bytes(tmp_path) == before

    def test_missing_file_with_nonzero_expected_revision_conflicts(self, tmp_path):
        with pytest.raises(cs.SettingsConflictError):
            cs.save_settings(
                tmp_path, expected_revision=1,
                native_task_auto=_default_policy(), excluded=[],
            )
        assert not cs.settings_path(tmp_path).exists()

    def test_save_rejects_bad_expected_revision_before_file_access(self, tmp_path):
        for bad in (True, -1, 1.5, "0"):
            with pytest.raises(ValueError):
                cs.save_settings(
                    tmp_path, expected_revision=bad,
                    native_task_auto=_default_policy(), excluded=[],
                )
        assert not cs.settings_path(tmp_path).exists()


# ---------------------------------------------------------------------------
# Fail-closed writes and atomic replace
# ---------------------------------------------------------------------------


class TestFailClosedWrites:
    def test_save_fails_closed_on_malformed_file(self, tmp_path):
        _write_raw(tmp_path, "garbage")
        with pytest.raises(cs.SettingsFormatError):
            cs.save_settings(
                tmp_path, expected_revision=0,
                native_task_auto=_default_policy(), excluded=[],
            )
        assert _bytes(tmp_path) == b"garbage"

    def test_save_fails_closed_on_unsupported_version(self, tmp_path):
        _write_json(tmp_path, _valid_payload(version=99))
        before = _bytes(tmp_path)
        with pytest.raises(cs.SettingsFormatError):
            cs.save_settings(
                tmp_path, expected_revision=0,
                native_task_auto=_default_policy(), excluded=[],
            )
        assert _bytes(tmp_path) == before

    def test_save_fails_closed_on_lock_failure(self, tmp_path, monkeypatch):
        cs.save_settings(
            tmp_path, expected_revision=0,
            native_task_auto=_default_policy(), excluded=[],
        )
        before = _bytes(tmp_path)

        def boom(vault_root):
            raise OSError("lock unavailable")

        monkeypatch.setattr(cs, "_acquire_lock_file", boom)
        with pytest.raises(OSError):
            cs.save_settings(
                tmp_path, expected_revision=1,
                native_task_auto=_default_policy(), excluded=[NATIVE],
            )
        assert _bytes(tmp_path) == before

    def test_save_fails_closed_on_write_failure(self, tmp_path, monkeypatch):
        cs.save_settings(
            tmp_path, expected_revision=0,
            native_task_auto=_default_policy(), excluded=[],
        )
        before = _bytes(tmp_path)

        def boom(path, data):
            raise OSError("write failed")

        monkeypatch.setattr(cs, "_atomic_write_json", boom)
        with pytest.raises(OSError):
            cs.save_settings(
                tmp_path, expected_revision=1,
                native_task_auto=_default_policy(), excluded=[NATIVE],
            )
        assert _bytes(tmp_path) == before

    def test_read_fails_closed_on_directory_at_settings_path(self, tmp_path):
        path = cs.settings_path(tmp_path)
        path.mkdir(parents=True)
        with pytest.raises(cs.SettingsStoreError):
            cs.read_settings(tmp_path)

    def test_atomic_write_leaves_no_temp_files(self, tmp_path):
        cs.save_settings(
            tmp_path, expected_revision=0,
            native_task_auto=_default_policy(), excluded=[],
        )
        leftovers = [
            entry.name
            for entry in cs.settings_path(tmp_path).parent.iterdir()
            if entry.name.endswith(".tmp")
        ]
        assert leftovers == []

    def test_lock_file_created_under_vault(self, tmp_path):
        cs.save_settings(
            tmp_path, expected_revision=0,
            native_task_auto=_default_policy(), excluded=[],
        )
        assert cs.lock_path(tmp_path).is_file()


# ---------------------------------------------------------------------------
# Active-enabled custom structures (additive version-1 key)
# ---------------------------------------------------------------------------


PROJECT_STRUCTURE = "0d194525-c5a1-4af5-bb62-202b83006b5e"
PRESS_STRUCTURE = "6aa7b02a-4315-47d1-9cfb-0c0cdac0950c"


class TestActiveStructures:
    def test_default_has_no_active_structures(self, tmp_path):
        assert cs.read_settings(tmp_path).settings.active_structures == frozenset()

    def test_active_structures_round_trip(self, tmp_path):
        saved = cs.save_settings(
            tmp_path,
            expected_revision=0,
            native_task_auto=_default_policy(),
            excluded=[],
            active_structures=[PROJECT_STRUCTURE, "custom-project"],
        )

        assert saved.active_structures == frozenset({PROJECT_STRUCTURE, "custom-project"})
        result = cs.read_settings(tmp_path)
        assert result.settings.active_structures == frozenset(
            {PROJECT_STRUCTURE, "custom-project"}
        )

    def test_active_structures_are_written_sorted_and_deterministic(self, tmp_path):
        cs.save_settings(
            tmp_path, expected_revision=0,
            native_task_auto=_default_policy(), excluded=[],
            active_structures=["zeta", "alpha"],
        )
        data = json.loads(_bytes(tmp_path).decode("utf-8"))
        assert list(data["active_structures"]) == ["alpha", "zeta"]

    def test_version_1_file_without_the_key_reads_as_empty(self, tmp_path):
        # An existing on-disk version-1 file written before the additive key
        # existed must stay readable; its absence means "no custom structure
        # is Active-enabled".
        legacy = {
            "version": 1,
            "revision": 0,
            "native_task_auto": {
                "active_enabled": True,
                "due_enabled": True,
                "deadline_enabled": True,
                "deadline_horizon_days": 2,
            },
            "excluded": {},
        }
        _write_json(tmp_path, legacy)

        result = cs.read_settings(tmp_path)

        assert result.persisted is True
        assert result.settings.active_structures == frozenset()
        assert result.settings.as_dict()["active_structures"] == {}

    def test_saved_settings_bridge_active_structures_to_the_evaluator(self, tmp_path):
        saved = cs.save_settings(
            tmp_path, expected_revision=0,
            native_task_auto=_default_policy(), excluded=[],
            active_structures=[PROJECT_STRUCTURE],
        )
        assert saved.to_assignment_settings().active_structures == frozenset(
            {PROJECT_STRUCTURE}
        )

    @pytest.mark.parametrize(
        "active",
        [
            [],                                             # not an object
            "custom-project",                               # string
            1,                                              # number
            None,                                           # null
            {"custom-project": False},                      # flag false
            {"custom-project": 1},                          # flag int
            {"custom-project": None},                       # flag null
            {"": True},                                     # empty id
            {" custom-project ": True},                     # whitespace alias
            {"custom project": True},                       # inner whitespace
        ],
    )
    def test_malformed_active_structures_fail_closed(self, tmp_path, active):
        _write_json(tmp_path, _valid_payload(active_structures=active))
        before = _bytes(tmp_path)

        with pytest.raises(cs.SettingsFormatError):
            cs.read_settings(tmp_path)

        assert _bytes(tmp_path) == before

    def test_duplicate_active_structure_keys_are_rejected(self, tmp_path):
        _write_raw(
            tmp_path,
            '{"version": 1, "revision": 0, '
            '"native_task_auto": {"active_enabled": true, "due_enabled": true, '
            '"deadline_enabled": true, "deadline_horizon_days": 2}, '
            '"excluded": {}, "active_structures": {"alpha": true, "alpha": true}}',
        )
        with pytest.raises(cs.SettingsFormatError):
            cs.read_settings(tmp_path)

    def test_save_rejects_invalid_structure_before_any_file_access(self, tmp_path):
        with pytest.raises(ValueError):
            cs.save_settings(
                tmp_path, expected_revision=0,
                native_task_auto=_default_policy(), excluded=[],
                active_structures=["custom-project", "  "],
            )
        assert not cs.settings_path(tmp_path).exists()

    def test_constructed_model_is_strict_about_active_structures(self):
        with pytest.raises(ValueError):
            cs.CapacitiesSettings(active_structures=[""])
        with pytest.raises(ValueError):
            cs.CapacitiesSettings(active_structures=[None])
        with pytest.raises(ValueError):
            cs.CapacitiesSettings(active_structures=["a b"])


# ---------------------------------------------------------------------------
# Admission inputs (additive version-1 keys)
# ---------------------------------------------------------------------------
# ``native_task_structures`` and ``active_statuses`` were added additively to
# schema version 1 in the same way as ``active_structures``: absent means the
# documented default, every fresh write emits them, and a present value is
# validated strictly. ``SCHEMA_VERSION`` is deliberately unchanged so an
# existing version-1 file keeps reading instead of failing closed.


class TestAdmissionInputs:
    def test_schema_version_is_unchanged(self):
        assert cs.SCHEMA_VERSION == 1

    def test_defaults_when_no_file_exists(self, tmp_path):
        settings = cs.read_settings(tmp_path).settings

        assert settings.native_task_structures == frozenset({"RootTask", "Task"})
        assert settings.active_statuses == frozenset({"active"})

    def test_legacy_version_1_file_without_either_key_reads_with_the_documented_defaults(
        self, tmp_path
    ):
        _write_json(tmp_path, _valid_payload())

        result = cs.read_settings(tmp_path)

        assert result.persisted is True
        assert result.settings.native_task_structures == frozenset({"RootTask", "Task"})
        assert result.settings.active_statuses == frozenset({"active"})

    def test_both_keys_round_trip_through_write_and_read(self, tmp_path):
        saved = cs.save_settings(
            tmp_path,
            expected_revision=0,
            native_task_auto=_default_policy(),
            excluded=[],
            native_task_structures=["RootTask", "custom-project"],
            active_statuses=["Active", "In Progress"],
        )

        assert saved.native_task_structures == frozenset({"RootTask", "custom-project"})
        assert saved.active_statuses == frozenset({"Active", "In Progress"})
        result = cs.read_settings(tmp_path)
        assert result.settings == saved
        assert result.settings.native_task_structures == frozenset(
            {"RootTask", "custom-project"}
        )
        assert result.settings.active_statuses == frozenset({"Active", "In Progress"})

    def test_freshly_written_file_emits_both_keys(self, tmp_path):
        cs.save_settings(
            tmp_path,
            expected_revision=0,
            native_task_auto=_default_policy(),
            excluded=[],
        )
        data = json.loads(_bytes(tmp_path).decode("utf-8"))

        assert data["native_task_structures"] == DEFAULT_NATIVE_STRUCTURES
        assert data["active_statuses"] == DEFAULT_ACTIVE_STATUSES

    def test_written_lists_are_sorted_and_deterministic(self, tmp_path):
        cs.save_settings(
            tmp_path, expected_revision=0,
            native_task_auto=_default_policy(), excluded=[],
            native_task_structures=["zeta", "alpha"],
            active_statuses=["zeta", "Alpha"],
        )
        data = json.loads(_bytes(tmp_path).decode("utf-8"))

        assert data["native_task_structures"] == ["alpha", "zeta"]
        assert data["active_statuses"] == ["Alpha", "zeta"]

    def test_active_statuses_are_stored_as_given_not_pre_normalized(self, tmp_path):
        # The evaluator normalizes at comparison time; the persisted form keeps
        # the operator's exact text.
        saved = cs.save_settings(
            tmp_path, expected_revision=0,
            native_task_auto=_default_policy(), excluded=[],
            active_statuses=["In Progress"],
        )

        assert saved.active_statuses == frozenset({"In Progress"})
        assert cs.read_settings(tmp_path).settings.active_statuses == frozenset(
            {"In Progress"}
        )

    def test_empty_native_task_structures_is_a_legitimate_configuration(self, tmp_path):
        saved = cs.save_settings(
            tmp_path, expected_revision=0,
            native_task_auto=_default_policy(), excluded=[],
            native_task_structures=[],
        )

        assert saved.native_task_structures == frozenset()
        result = cs.read_settings(tmp_path)
        assert result.settings.native_task_structures == frozenset()
        assert result.settings.as_dict()["native_task_structures"] == []

    def test_empty_active_statuses_is_a_legitimate_configuration(self, tmp_path):
        saved = cs.save_settings(
            tmp_path, expected_revision=0,
            native_task_auto=_default_policy(), excluded=[],
            active_statuses=[],
        )

        assert saved.active_statuses == frozenset()
        assert cs.read_settings(tmp_path).settings.active_statuses == frozenset()

    @pytest.mark.parametrize(
        "key, bad",
        [
            ("native_task_structures", "RootTask"),            # not a list
            ("native_task_structures", {"RootTask": True}),    # object
            ("native_task_structures", 1),                     # number
            ("native_task_structures", None),                  # null
            ("native_task_structures", [""]),                  # empty entry
            ("native_task_structures", [None]),                # non-string entry
            ("native_task_structures", [1]),                   # non-string entry
            ("native_task_structures", ["RootTask", "RootTask"]),  # duplicate
            ("active_statuses", "active"),                     # not a list
            ("active_statuses", {"active": True}),             # object
            ("active_statuses", None),                         # null
            ("active_statuses", [""]),                         # empty entry
            ("active_statuses", [True]),                       # non-string entry
            ("active_statuses", ["active", "active"]),         # duplicate
        ],
    )
    def test_malformed_stored_values_fail_closed_and_preserve_the_bytes(
        self, tmp_path, key, bad
    ):
        _write_json(tmp_path, _valid_payload(**{key: bad}))
        before = _bytes(tmp_path)

        with pytest.raises(cs.SettingsFormatError):
            cs.read_settings(tmp_path)
        with pytest.raises(cs.SettingsFormatError):
            cs.save_settings(
                tmp_path, expected_revision=0,
                native_task_auto=_default_policy(), excluded=[],
            )
        assert _bytes(tmp_path) == before

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"native_task_structures": "RootTask"},
            {"native_task_structures": [""]},
            {"native_task_structures": [None]},
            {"native_task_structures": ["RootTask", "RootTask"]},
            {"native_task_structures": None},
            {"active_statuses": "active"},
            {"active_statuses": [""]},
            {"active_statuses": [True]},
            {"active_statuses": ["active", "active"]},
            {"active_statuses": None},
        ],
    )
    def test_malformed_save_input_is_rejected_before_any_file_access(
        self, tmp_path, kwargs
    ):
        with pytest.raises(ValueError):
            cs.save_settings(
                tmp_path, expected_revision=0,
                native_task_auto=_default_policy(), excluded=[], **kwargs,
            )
        assert not cs.settings_path(tmp_path).exists()
        assert not cs.lock_path(tmp_path).exists()

    def test_constructed_model_is_strict_about_the_admission_inputs(self):
        for kwargs in (
            {"native_task_structures": "RootTask"},
            {"native_task_structures": [""]},
            {"native_task_structures": [None]},
            {"native_task_structures": ["RootTask", "RootTask"]},
            {"native_task_structures": None},
            {"active_statuses": "active"},
            {"active_statuses": [""]},
            {"active_statuses": [True]},
            {"active_statuses": ["active", "active"]},
        ):
            with pytest.raises(ValueError):
                cs.CapacitiesSettings(**kwargs)
        assert cs.CapacitiesSettings(
            native_task_structures=[]
        ).native_task_structures == frozenset()

    def test_saved_settings_bridge_both_inputs_to_the_evaluator(self, tmp_path):
        saved = cs.save_settings(
            tmp_path, expected_revision=0,
            native_task_auto=_default_policy(), excluded=[],
            native_task_structures=["custom-project"],
            active_statuses=["In Progress"],
        )
        seam = saved.to_assignment_settings()

        assert seam.native_task_structures == frozenset({"custom-project"})
        assert seam.active_statuses == frozenset({"In Progress"})
