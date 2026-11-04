"""Schema version 2: the lazy version-1 migration, the tri-state ``statuses``
distinction, the mode/property rules, and the pre-save migration backup.

Everything here is local and fake: no provider, credential, network, or live
source is touched. The stored bytes are the only input.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import capacities_settings as cs  # noqa: E402


SPACE = "space-1"
EXCLUDED = f"capacities:{SPACE}:RootTask:task-excluded"

# The owner's live structures (version 1 revision 44).
ROOT_TASK = "RootTask"
PROJECT = "0d194525-c5a1-4af5-bb62-202b83006b5e"
EVENT = "80c91235-c892-4bff-a1ed-0ce19e48031e"
PURCHASE = "b0ef1817-d0c1-4982-ba48-6d5f9beb0a4d"
PRINT = "ced0f1b5-5f85-4537-81b0-f25566c32e8a"
CONTINUAL = "e16f8eeb-1d66-4f21-8d3a-adaeff0a75be"
PRESS = "6aa7b02a-4315-47d1-9cfb-0c0cdac0950c"

EVENT_PROPERTY = "p-event-assigned"
PURCHASE_PROPERTY = "p-purchase-assigned"
PRINT_PROPERTY = "p-print-assigned"
CONTINUAL_PROPERTY = "p-continual-assigned"
PRESS_PROPERTY = "p-press-assigned"


def _write_json(data) -> Path:
    path = cs.settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path


def _bytes() -> bytes:
    return cs.settings_path().read_bytes()


def _v1(**overrides) -> dict:
    """A minimal version-1 document (no additive keys)."""
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
    }
    payload.update(overrides)
    return payload


def _live_v1() -> dict:
    """The owner's live version-1 file: revision 44, six declarations."""
    return {
        "version": 1,
        "revision": 44,
        "native_task_auto": {
            "active_enabled": True,
            "due_enabled": True,
            "deadline_enabled": True,
            "deadline_horizon_days": 14,
        },
        "excluded": {EXCLUDED: True},
        "active_structures": {PROJECT: True},
        "native_task_structures": [ROOT_TASK, PRESS],
        "active_statuses": ["In Progress"],
        "assigned_structures": {
            EVENT: EVENT_PROPERTY,
            PURCHASE: PURCHASE_PROPERTY,
            PRINT: PRINT_PROPERTY,
            CONTINUAL: CONTINUAL_PROPERTY,
            PRESS: PRESS_PROPERTY,
        },
    }


def _backups() -> list[Path]:
    return sorted(
        cs.settings_path().parent.glob(f"capacities-settings.json{cs.BACKUP_INFIX}*")
    )


# ---------------------------------------------------------------------------
# Lazy version-1 migration (read)
# ---------------------------------------------------------------------------


class TestLiveVersionOneMigration:
    def test_live_document_translates_to_the_version_two_shape(self, tmp_path):
        _write_json(_live_v1())
        before = _bytes()

        result = cs.read_settings()

        assert result.persisted is True
        assert result.source_version == 1
        assert result.settings.revision == 44
        assert result.settings.as_dict()["version"] == 2
        # The read never writes and the version-1 bytes stay untouched.
        assert _bytes() == before
        assert _backups() == []

    def test_live_types_translate_faithfully(self, tmp_path):
        _write_json(_live_v1())

        settings = cs.read_settings().settings

        assert {
            structure_id: declaration.as_dict()
            for structure_id, declaration in settings.types.items()
        } == {
            # RootTask stays date-only: status admission is disabled.
            ROOT_TASK: {"mode": "native", "property": None, "statuses": []},
            PROJECT: {"mode": "active", "property": None, "statuses": None},
            EVENT: {"mode": "assigned", "property": EVENT_PROPERTY, "statuses": None},
            PURCHASE: {
                "mode": "assigned",
                "property": PURCHASE_PROPERTY,
                "statuses": None,
            },
            PRINT: {"mode": "assigned", "property": PRINT_PROPERTY, "statuses": None},
            CONTINUAL: {
                "mode": "assigned",
                "property": CONTINUAL_PROPERTY,
                "statuses": None,
            },
            # Press was native AND assigned in version 1: the explicit
            # boolean gate wins and the native membership/date rules go.
            PRESS: {"mode": "assigned", "property": PRESS_PROPERTY, "statuses": None},
        }
        assert settings.structure_ids("native") == (ROOT_TASK,)
        assert settings.structure_ids("assigned") == tuple(
            sorted([CONTINUAL, EVENT, PRESS, PRINT, PURCHASE])
        )

    def test_live_vocabularies_and_horizon_translate(self, tmp_path):
        _write_json(_live_v1())

        settings = cs.read_settings().settings

        assert settings.statuses.open == frozenset({"In Progress"})
        assert settings.statuses.completed == frozenset({"Completed", "Dropped"})
        assert settings.native.deadline_horizon_days == 14
        assert settings.excluded == frozenset({EXCLUDED})

    def test_root_task_has_no_status_admission(self, tmp_path):
        _write_json(_live_v1())

        settings = cs.read_settings().settings

        assert settings.status_admission_enabled(ROOT_TASK) is False
        assert settings.open_statuses_for(ROOT_TASK) == frozenset()
        assert settings.status_admission_enabled(PROJECT) is True
        assert settings.open_statuses_for(PROJECT) == frozenset({"In Progress"})

    def test_declared_properties_survive_the_migration(self, tmp_path):
        _write_json(_live_v1())

        settings = cs.read_settings().settings

        assert settings.types[EVENT].property_id == EVENT_PROPERTY
        assert settings.types[PRESS].property_id == PRESS_PROPERTY

    def test_save_after_a_migration_rewrites_version_two_and_backs_up(self, tmp_path):
        _write_json(_live_v1())
        before = _bytes()
        migrated = cs.read_settings().settings

        saved = cs.save_settings(
            expected_revision=migrated.revision,
            statuses=migrated.statuses,
            types=migrated.types,
            native=migrated.native,
            excluded=migrated.excluded,
        )

        assert saved.version == 2
        assert saved.revision == 45
        stored = json.loads(_bytes().decode("utf-8"))
        assert stored["version"] == 2
        assert stored["revision"] == 45
        assert stored["types"][ROOT_TASK] == {
            "mode": "native",
            "property": None,
            "statuses": [],
        }
        backups = _backups()
        assert len(backups) == 1
        assert backups[0].read_bytes() == before

    def test_migrated_document_round_trips_stably(self, tmp_path):
        _write_json(_live_v1())
        migrated = cs.read_settings().settings
        first = cs.save_settings(
            expected_revision=migrated.revision,
            statuses=migrated.statuses,
            types=migrated.types,
            native=migrated.native,
            excluded=migrated.excluded,
        )
        first_bytes = _bytes()

        second = cs.save_settings(
            expected_revision=first.revision,
            statuses=first.statuses,
            types=first.types,
            native=first.native,
            excluded=first.excluded,
        )

        assert second.as_dict() == dict(first.as_dict(), revision=second.revision)
        reread = cs.read_settings()
        assert reread.source_version == 2
        assert reread.settings == second
        # Only the revision moved between the two version-2 documents.
        stored = json.loads(_bytes())
        assert dict(stored, revision=0) == dict(json.loads(first_bytes), revision=0)
        assert stored["revision"] == second.revision


# ---------------------------------------------------------------------------
# Generic version-1 translation
# ---------------------------------------------------------------------------


class TestGenericVersionOneTranslation:
    def test_a_minimal_version_one_file_translates_to_the_defaults(self, tmp_path):
        _write_json(_v1())

        settings = cs.read_settings().settings

        assert settings.as_dict() == {
            "version": 2,
            "revision": 0,
            "statuses": {
                "open": ["active"],
                "completed": ["Completed", "Dropped"],
            },
            "types": {
                ROOT_TASK: {"mode": "native", "property": None, "statuses": []}
            },
            "native": {"deadline_horizon_days": 2},
            "excluded": {},
        }

    def test_each_version_one_input_maps_to_its_mode(self, tmp_path):
        _write_json(
            _v1(
                active_structures={"active-only": True},
                assigned_structures={
                    "assigned-only": "p-assigned",
                    "assigned-and-active": "p-both",
                },
                assigned_on_structures={
                    "assigned-on-only": "p-date",
                    "assigned-on-active": "p-date-2",
                },
                native_task_structures=["RootTask", "native-only"],
            )
        )

        settings = cs.read_settings().settings

        modes = {
            structure_id: declaration.mode
            for structure_id, declaration in settings.types.items()
        }
        assert modes == {
            "RootTask": "native",
            "native-only": "native",
            "active-only": "active",
            "assigned-only": "assigned",
            "assigned-and-active": "assigned",
            "assigned-on-only": "assignedOn",
            "assigned-on-active": "assignedOn",
        }
        # Only the declared-property modes carry a property id.
        assert settings.types["assigned-on-only"].property_id == "p-date"
        assert settings.types["native-only"].property_id is None

    def test_native_membership_wins_over_active(self, tmp_path):
        _write_json(
            _v1(
                active_structures={"both": True},
                native_task_structures=["RootTask", "both"],
            )
        )

        assert cs.read_settings().settings.mode_of("both") == "native"

    def test_empty_native_membership_leaves_no_native_types(self, tmp_path):
        _write_json(_v1(native_task_structures=[]))

        settings = cs.read_settings().settings

        assert settings.structure_ids("native") == ()
        assert settings.types == {}

    def test_active_statuses_become_the_shared_open_vocabulary(self, tmp_path):
        _write_json(_v1(active_statuses=["In Progress", "Next"]))

        settings = cs.read_settings().settings

        assert settings.statuses.open == frozenset({"In Progress", "Next"})
        assert settings.statuses.completed == frozenset({"Completed", "Dropped"})

    def test_version_one_strictness_is_unchanged(self, tmp_path):
        for broken in (
            _v1(native_task_auto={"active_enabled": True}),
            _v1(native_task_auto={"active_enabled": 1, "due_enabled": True,
                                 "deadline_enabled": True,
                                 "deadline_horizon_days": 2}),
            _v1(native_task_structures="RootTask"),
            _v1(active_statuses="active"),
            _v1(assigned_structures={"s": ""}),
            _v1(assigned_on_structures={"s": 1}),
            _v1(active_structures={" ": True}),
            _v1(unknown_key=True),
        ):
            _write_json(broken)
            before = _bytes()
            with pytest.raises(cs.SettingsFormatError):
                cs.read_settings()
            assert _bytes() == before

    def test_unsupported_version_is_still_rejected(self, tmp_path):
        _write_json(_v1(version=3))
        before = _bytes()

        with pytest.raises(cs.SettingsFormatError):
            cs.read_settings()

        assert _bytes() == before

    def test_a_save_over_a_corrupt_file_never_backs_up(self, tmp_path):
        path = cs.settings_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"garbage")

        with pytest.raises(cs.SettingsFormatError):
            cs.save_settings(expected_revision=0)

        assert _bytes() == b"garbage"
        assert _backups() == []


# ---------------------------------------------------------------------------
# The tri-state ``statuses`` distinction
# ---------------------------------------------------------------------------


class TestStatusTriState:
    def test_absent_and_null_both_inherit(self, tmp_path):
        for declaration in (
            {"mode": "native"},
            {"mode": "native", "statuses": None},
        ):
            saved = cs.save_settings(
                expected_revision=0 if not cs.settings_path().exists() else 1,
                types={"RootTask": declaration},
                statuses={"open": ["In Progress"], "completed": []},
            )
            parsed = saved.types["RootTask"]
            assert parsed.statuses is None
            assert parsed.status_admission_enabled is True
            assert saved.open_statuses_for("RootTask") == frozenset({"In Progress"})

    def test_empty_list_disables_status_admission(self, tmp_path):
        saved = cs.save_settings(
            expected_revision=0,
            types={"RootTask": {"mode": "native", "statuses": []}},
            statuses={"open": ["In Progress"], "completed": []},
        )

        parsed = saved.types["RootTask"]
        assert parsed.statuses == ()
        assert parsed.status_admission_enabled is False
        assert saved.open_statuses_for("RootTask") == frozenset()

    def test_non_empty_list_is_that_types_own_open_set(self, tmp_path):
        saved = cs.save_settings(
            expected_revision=0,
            types={
                "s": {"mode": "active", "statuses": ["Doing", "Ready"]},
                "inherits": {"mode": "native"},
            },
            statuses={"open": ["In Progress"], "completed": []},
        )

        parsed = saved.types["s"]
        assert parsed.status_admission_enabled is True
        assert parsed.statuses == ("Doing", "Ready")
        assert saved.open_statuses_for("s") == frozenset({"Doing", "Ready"})
        assert saved.open_statuses_for("inherits") == frozenset({"In Progress"})

    def test_the_three_states_persist_distinctly_and_decode_distinctly(self, tmp_path):
        _write_json(
            {
                "version": 2,
                "revision": 0,
                "statuses": {"open": ["In Progress"], "completed": []},
                "types": {
                    "inherits": {"mode": "native", "property": None,
                                 "statuses": None},
                    "disabled": {"mode": "native", "property": None,
                                 "statuses": []},
                    "own": {"mode": "native", "property": None,
                            "statuses": ["Mine"]},
                },
                "native": {"deadline_horizon_days": 2},
                "excluded": {},
            }
        )

        settings = cs.read_settings().settings

        assert settings.types["inherits"].statuses is None
        assert settings.types["disabled"].statuses == ()
        assert settings.types["own"].statuses == ("Mine",)
        assert settings.as_dict()["types"] == {
            "disabled": {"mode": "native", "property": None, "statuses": []},
            "inherits": {"mode": "native", "property": None, "statuses": None},
            "own": {"mode": "native", "property": None, "statuses": ["Mine"]},
        }

    def test_a_present_statuses_value_must_be_a_list(self, tmp_path):
        for statuses in ("Mine", {"Mine": True}, 1):
            _write_json(
                {
                    "version": 2,
                    "revision": 0,
                    "statuses": {"open": ["active"], "completed": []},
                    "types": {"s": {"mode": "active", "statuses": statuses}},
                    "native": {"deadline_horizon_days": 2},
                    "excluded": {},
                }
            )
            with pytest.raises(cs.SettingsFormatError):
                cs.read_settings()


# ---------------------------------------------------------------------------
# Mode/property rules
# ---------------------------------------------------------------------------


class TestPropertyRules:
    @pytest.mark.parametrize("mode", ["assigned", "assignedOn"])
    def test_property_is_required_for_the_declaring_modes(self, mode):
        with pytest.raises(ValueError):
            cs.TypeDeclaration(mode=mode)
        with pytest.raises(ValueError):
            cs.TypeDeclaration(mode=mode, property_id="")
        with pytest.raises(ValueError):
            cs.TypeDeclaration(mode=mode, property_id=1)

    @pytest.mark.parametrize("mode", ["off", "active", "native"])
    def test_property_is_forbidden_for_the_other_modes(self, mode):
        with pytest.raises(ValueError):
            cs.TypeDeclaration(mode=mode, property_id="p-1")

    def test_an_unknown_mode_is_rejected_by_the_model(self):
        with pytest.raises(ValueError):
            cs.TypeDeclaration(mode="sometimes")

    def test_save_rejects_a_missing_property_before_io(self, tmp_path):
        with pytest.raises(ValueError):
            cs.save_settings(
                expected_revision=0,
                types={"s": {"mode": "assigned", "property": None}},
            )
        assert not cs.settings_path().exists()

    def test_save_rejects_a_forbidden_property_before_io(self, tmp_path):
        with pytest.raises(ValueError):
            cs.save_settings(
                expected_revision=0,
                types={"s": {"mode": "native", "property": "p-1"}},
            )
        assert not cs.settings_path().exists()

    def test_read_rejects_a_missing_property(self, tmp_path):
        _write_json(
            {
                "version": 2,
                "revision": 0,
                "statuses": {"open": ["active"], "completed": []},
                "types": {"s": {"mode": "assigned", "property": None}},
                "native": {"deadline_horizon_days": 2},
                "excluded": {},
            }
        )
        before = _bytes()

        with pytest.raises(cs.SettingsFormatError) as excinfo:
            cs.read_settings()

        assert "requires a property id" in str(excinfo.value)
        assert _bytes() == before

    def test_read_rejects_a_forbidden_property(self, tmp_path):
        _write_json(
            {
                "version": 2,
                "revision": 0,
                "statuses": {"open": ["active"], "completed": []},
                "types": {"s": {"mode": "active", "property": "p-1"}},
                "native": {"deadline_horizon_days": 2},
                "excluded": {},
            }
        )

        with pytest.raises(cs.SettingsFormatError) as excinfo:
            cs.read_settings()

        assert "must not declare a property id" in str(excinfo.value)


class TestUnknownMode:
    def test_an_unknown_mode_is_rejected_with_a_clear_error(self, tmp_path):
        _write_json(
            {
                "version": 2,
                "revision": 0,
                "statuses": {"open": ["active"], "completed": []},
                "types": {"s": {"mode": "sometimes", "property": None}},
                "native": {"deadline_horizon_days": 2},
                "excluded": {},
            }
        )
        before = _bytes()

        with pytest.raises(cs.SettingsFormatError) as excinfo:
            cs.read_settings()

        message = str(excinfo.value)
        assert "unknown mode 'sometimes'" in message
        assert "assignedOn" in message
        assert _bytes() == before

    def test_a_non_string_mode_is_rejected(self, tmp_path):
        _write_json(
            {
                "version": 2,
                "revision": 0,
                "statuses": {"open": ["active"], "completed": []},
                "types": {"s": {"mode": 1}},
                "native": {"deadline_horizon_days": 2},
                "excluded": {},
            }
        )
        with pytest.raises(cs.SettingsFormatError):
            cs.read_settings()


# ---------------------------------------------------------------------------
# The pre-save migration backup
# ---------------------------------------------------------------------------


class TestMigrationBackup:
    def test_backup_is_written_before_the_replace_with_the_old_bytes(
        self, tmp_path, monkeypatch
    ):
        _write_json(_v1(revision=7))
        before = _bytes()
        seen: dict[str, object] = {}
        real = cs._atomic_write_json

        def spy(path, data):
            seen["backups"] = [backup.read_bytes() for backup in _backups()]
            seen["settings_before_replace"] = Path(path).read_bytes()
            return real(path, data)

        monkeypatch.setattr(cs, "_atomic_write_json", spy)

        saved = cs.save_settings(expected_revision=7)

        assert saved.revision == 8
        assert seen["settings_before_replace"] == before
        assert seen["backups"] == [before]

    def test_backup_failure_aborts_the_save_with_the_original_intact(
        self, tmp_path, monkeypatch
    ):
        _write_json(_v1(revision=3))
        before = _bytes()

        def boom(raw):
            raise OSError("backup unwritable")

        monkeypatch.setattr(cs, "_write_v1_backup", boom)

        with pytest.raises(OSError):
            cs.save_settings(expected_revision=3)

        assert _bytes() == before
        assert json.loads(_bytes())["version"] == 1
        assert _backups() == []

    def test_backup_names_carry_the_version_one_marker(self, tmp_path):
        _write_json(_v1(revision=1))

        cs.save_settings(expected_revision=1)

        backups = _backups()
        assert len(backups) == 1
        name = backups[0].name
        assert name.startswith("capacities-settings.json.bak-v1-")
        stamp = name.split(cs.BACKUP_INFIX, 1)[1]
        assert stamp.endswith("Z") and len(stamp) == 22

    def test_the_first_save_creates_no_backup(self, tmp_path):
        cs.save_settings(expected_revision=0)
        assert _backups() == []

    def test_a_version_two_save_creates_no_backup(self, tmp_path):
        cs.save_settings(expected_revision=0)
        cs.save_settings(expected_revision=1, native={"deadline_horizon_days": 5})
        assert _backups() == []

    def test_a_conflicting_save_creates_no_backup(self, tmp_path):
        _write_json(_v1(revision=2))
        before = _bytes()

        with pytest.raises(cs.SettingsConflictError):
            cs.save_settings(expected_revision=1)

        assert _bytes() == before
        assert _backups() == []

    def test_two_migrations_in_a_row_never_overwrite_a_backup(self, tmp_path):
        _write_json(_v1(revision=1))
        cs.save_settings(expected_revision=1)
        _write_json(_v1(revision=9))

        cs.save_settings(expected_revision=9)

        assert len(_backups()) == 2


# ---------------------------------------------------------------------------
# Version-2 round-trip stability
# ---------------------------------------------------------------------------


class TestRoundTripStability:
    def test_a_version_two_document_round_trips_byte_identically(self, tmp_path):
        saved = cs.save_settings(
            expected_revision=0,
            statuses={"open": ["In Progress"], "completed": ["Completed"]},
            types={
                "RootTask": {"mode": "native", "statuses": []},
                PROJECT: {"mode": "active"},
                EVENT: {"mode": "assignedOn", "property": "p-date"},
            },
            native={"deadline_horizon_days": 14},
            excluded=[EXCLUDED],
        )
        first_bytes = _bytes()
        assert json.loads(first_bytes) == saved.as_dict()

        reread = cs.read_settings()
        assert reread.source_version == 2
        resaved = cs.save_settings(
            expected_revision=reread.settings.revision,
            statuses=reread.settings.statuses,
            types=reread.settings.types,
            native=reread.settings.native,
            excluded=reread.settings.excluded,
        )

        assert json.loads(_bytes()) == dict(
            json.loads(first_bytes), revision=resaved.revision
        )
        assert resaved.as_dict() == dict(saved.as_dict(), revision=resaved.revision)

    def test_defaults_round_trip(self, tmp_path):
        cs.save_settings(expected_revision=0)
        first = json.loads(_bytes())

        settings = cs.read_settings().settings
        cs.save_settings(
            expected_revision=settings.revision,
            statuses=settings.statuses,
            types=settings.types,
            native=settings.native,
            excluded=settings.excluded,
        )

        assert json.loads(_bytes()) == dict(first, revision=2)
