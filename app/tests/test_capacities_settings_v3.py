"""Schema version 3: the ``read_source`` enablement gate (R12/KTD10).

The document gains one strict top-level key, ``read_source`` (``"auto"`` or
``"rest"``, default ``"rest"``). ``"rest"`` is the kill switch: the sequential
REST-only read path. ``"auto"`` lets the builder construct the MCP splitter,
which U6b still gates on a valid custody file.

Everything here is local and fake: no provider, credential, custody file, or
network is touched. The stored bytes are the only input.
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
ROOT_TASK = "RootTask"
PROJECT = "0d194525-c5a1-4af5-bb62-202b83006b5e"
CUSTOM = f"capacities:{SPACE}:RootTask:task-custom"


def _write_json(data) -> Path:
    path = cs.settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path


def _bytes() -> bytes:
    return cs.settings_path().read_bytes()


def _stored() -> dict:
    return json.loads(_bytes().decode("utf-8"))


def _backups() -> list[Path]:
    directory = cs.settings_path().parent
    if not directory.exists():
        return []
    return sorted(directory.glob(f"{cs.STATE_FILENAME}.bak-v*"))


def _v1() -> dict:
    return {
        "version": 1,
        "revision": 7,
        "native_task_auto": {
            "active_enabled": True,
            "due_enabled": True,
            "deadline_enabled": False,
            "deadline_horizon_days": 5,
        },
        "excluded": {EXCLUDED: True},
        "active_structures": {PROJECT: True},
        "native_task_structures": [ROOT_TASK],
        "active_statuses": ["In Progress"],
    }


def _v2(revision: int = 12) -> dict:
    """A complete version-2 document: every editable field populated."""
    return {
        "version": 2,
        "revision": revision,
        "statuses": {"open": ["In Progress"], "completed": ["Completed"]},
        "types": {
            ROOT_TASK: {"mode": "native", "property": None, "statuses": []},
            PROJECT: {"mode": "active", "property": None, "statuses": None},
        },
        "native": {"deadline_horizon_days": 14},
        "excluded": {EXCLUDED: True},
    }


def _v3(revision: int = 12, read_source="rest") -> dict:
    payload = _v2(revision)
    payload["version"] = 3
    payload["read_source"] = read_source
    return payload


def _before_migration_bytes(version: int) -> bytes:
    return json.dumps(_v1() if version == 1 else _v2(), indent=2).encode("utf-8")


# ---------------------------------------------------------------------------
# Constants and the typed accessor
# ---------------------------------------------------------------------------


class TestSchemaAndAccessor:
    def test_schema_version_is_three(self):
        assert cs.SCHEMA_VERSION == 3

    def test_the_mode_vocabulary_is_closed(self):
        assert cs.READ_SOURCE_MODES == frozenset({"auto", "rest"})
        assert cs.DEFAULT_READ_SOURCE == "rest"

    def test_the_default_settings_object_is_rest(self):
        settings = cs.CapacitiesSettings()

        assert settings.read_source == "rest"
        assert settings.as_dict()["read_source"] == "rest"
        assert settings.as_dict()["version"] == cs.SCHEMA_VERSION

    def test_a_missing_document_reports_rest_and_creates_nothing(self):
        result = cs.read_settings()

        assert result.persisted is False
        assert result.source_version is None
        assert result.settings.read_source == "rest"
        assert not cs.settings_path().exists()

    def test_the_accessor_is_strict_at_construction(self):
        assert cs.CapacitiesSettings(read_source="auto").read_source == "auto"
        for bad in (True, False, None, 2, 0, 1.0, "", "mcp", "REST", ["rest"], {}):
            with pytest.raises(ValueError):
                cs.CapacitiesSettings(read_source=bad)


# ---------------------------------------------------------------------------
# Migration of the two superseded shapes
# ---------------------------------------------------------------------------


class TestVersionOneMigration:
    def test_a_v1_document_loads_as_v3_with_rest(self):
        _write_json(_v1())
        before = _bytes()

        result = cs.read_settings()

        assert result.persisted is True
        assert result.source_version == 1
        assert result.settings.version == 3
        assert result.settings.read_source == "rest"
        assert result.settings.revision == 7
        # A read never rewrites and never takes a backup.
        assert _bytes() == before
        assert _backups() == []

    def test_a_v1_save_rewrites_v3_and_keeps_the_v1_backup(self):
        _write_json(_v1())
        before = _bytes()

        saved = cs.save_settings(expected_revision=7)

        assert saved.version == 3
        assert saved.read_source == "rest"
        assert _stored()["version"] == 3
        assert _stored()["read_source"] == "rest"
        backups = _backups()
        assert len(backups) == 1
        assert backups[0].name.startswith(f"{cs.STATE_FILENAME}{cs.BACKUP_INFIX}")
        assert backups[0].read_bytes() == before


class TestVersionTwoMigration:
    def test_a_v2_document_loads_as_v3_with_rest_and_no_other_change(self):
        _write_json(_v2())
        before = _bytes()

        result = cs.read_settings()

        assert result.persisted is True
        assert result.source_version == 2
        assert result.settings.version == 3
        assert result.settings.read_source == "rest"
        # Every stored policy field survives the translation verbatim.
        assert result.settings.statuses.open == frozenset({"In Progress"})
        assert result.settings.statuses.completed == frozenset({"Completed"})
        assert result.settings.native.deadline_horizon_days == 14
        assert result.settings.excluded == frozenset({EXCLUDED})
        assert result.settings.structure_ids() == tuple(sorted([ROOT_TASK, PROJECT]))
        assert result.settings.mode_of(ROOT_TASK) == "native"
        assert result.settings.mode_of(PROJECT) == "active"
        assert result.settings.types[ROOT_TASK].statuses == ()
        # Translate-only: the stored bytes and the revision are untouched.
        assert _bytes() == before
        assert _backups() == []

    def test_the_translated_document_equals_the_stored_fields_plus_rest(self):
        _write_json(_v2())

        translated = cs.read_settings().settings

        assert translated == cs.CapacitiesSettings(
            revision=12,
            statuses=cs.StatusVocabulary(
                open=frozenset({"In Progress"}), completed=frozenset({"Completed"})
            ),
            types={
                ROOT_TASK: cs.TypeDeclaration(mode="native", statuses=()),
                PROJECT: cs.TypeDeclaration(mode="active"),
            },
            native=cs.NativePolicy(deadline_horizon_days=14),
            read_source="rest",
            excluded=frozenset({EXCLUDED}),
        )
        assert translated.as_dict() == _v3()

    def test_a_v2_save_rewrites_v3_and_keeps_a_v2_backup(self):
        _write_json(_v2())
        before = _bytes()

        saved = cs.save_settings(
            expected_revision=12,
            statuses={"open": ["In Progress"], "completed": ["Completed"]},
            types={
                ROOT_TASK: {"mode": "native", "statuses": []},
                PROJECT: {"mode": "active"},
            },
            native={"deadline_horizon_days": 14},
            excluded=[EXCLUDED],
        )

        assert saved.version == 3
        assert saved.read_source == "rest"
        assert saved.revision == 13
        stored = _stored()
        assert stored["version"] == 3
        assert stored["read_source"] == "rest"
        assert stored["revision"] == 13
        backups = _backups()
        assert len(backups) == 1
        assert backups[0].name.startswith(
            f"{cs.STATE_FILENAME}{cs.BACKUP_INFIX_V2}"
        )
        assert backups[0].read_bytes() == before
        assert cs.read_settings().source_version == 3


# ---------------------------------------------------------------------------
# Round-trip and idempotence
# ---------------------------------------------------------------------------


class TestRoundTrip:
    def test_auto_round_trips_through_the_document(self):
        saved = cs.save_settings(
            expected_revision=0,
            statuses={"open": ["In Progress"], "completed": ["Completed"]},
            types={PROJECT: {"mode": "active"}},
            native={"deadline_horizon_days": 14},
            excluded=[EXCLUDED],
            read_source="auto",
        )
        first = _bytes()
        assert saved.read_source == "auto"
        assert json.loads(first) == saved.as_dict()
        assert json.loads(first)["read_source"] == "auto"

        reread = cs.read_settings()
        assert reread.source_version == 3
        assert reread.settings.read_source == "auto"

        # An unrelated full-replacement save that does not express the gate
        # preserves it instead of silently falling back to the kill switch.
        resaved = cs.save_settings(
            expected_revision=reread.settings.revision,
            statuses=reread.settings.statuses,
            types=reread.settings.types,
            native=reread.settings.native,
            excluded=reread.settings.excluded,
        )
        assert resaved.read_source == "auto"
        assert json.loads(_bytes()) == dict(json.loads(first), revision=resaved.revision)

    def test_rest_is_settable_and_sticks(self):
        cs.save_settings(expected_revision=0, read_source="auto")
        stopped = cs.save_settings(expected_revision=1, read_source="rest")

        assert stopped.read_source == "rest"
        assert cs.read_settings().settings.read_source == "rest"
        assert _stored()["read_source"] == "rest"

    def test_a_v3_document_round_trips_without_a_rewrite(self):
        _write_json(_v3(read_source="auto"))
        before = _bytes()

        first = cs.read_settings()
        second = cs.read_settings()

        assert first.source_version == 3
        assert second.source_version == 3
        assert first.settings.read_source == "auto"
        assert second.settings == first.settings
        assert _bytes() == before
        assert _backups() == []

    def test_migrating_once_makes_the_second_load_a_no_op(self):
        _write_json(_v2())

        cs.save_settings(expected_revision=12, read_source="rest")
        after_migration = _bytes()
        backups_after_migration = len(_backups())

        again = cs.read_settings()
        assert again.source_version == 3
        assert again.settings.read_source == "rest"
        # A read of an already-current document neither rewrites nor backs up.
        assert _bytes() == after_migration
        assert len(_backups()) == backups_after_migration

        resaved = cs.save_settings(
            expected_revision=again.settings.revision, read_source="rest"
        )
        assert resaved.version == 3
        assert json.loads(_bytes()) == dict(
            json.loads(after_migration), revision=resaved.revision
        )
        assert len(_backups()) == backups_after_migration


# ---------------------------------------------------------------------------
# Strict rejection
# ---------------------------------------------------------------------------


BAD_READ_SOURCES = [
    True,
    False,
    None,
    1,
    0,
    2.5,
    [],
    ["rest"],
    {},
    "",
    "mcp",
    "REST",
    " auto",
    "auto ",
    "mixed",
]


class TestStrictRejection:
    @pytest.mark.parametrize("value", BAD_READ_SOURCES)
    def test_an_unsupported_read_source_fails_closed(self, value):
        _write_json(_v3(read_source=value))
        before = _bytes()

        with pytest.raises(cs.SettingsFormatError):
            cs.read_settings()

        assert _bytes() == before

    def test_a_missing_read_source_is_rejected(self):
        payload = _v3()
        del payload["read_source"]
        _write_json(payload)
        before = _bytes()

        with pytest.raises(cs.SettingsFormatError):
            cs.read_settings()

        assert _bytes() == before

    def test_unknown_keys_stay_rejected(self):
        payload = _v3()
        payload["read_sources"] = "rest"
        _write_json(payload)

        with pytest.raises(cs.SettingsFormatError):
            cs.read_settings()

    def test_a_v2_document_may_not_carry_the_key(self):
        payload = _v2()
        payload["read_source"] = "auto"
        _write_json(payload)

        with pytest.raises(cs.SettingsFormatError):
            cs.read_settings()

    def test_a_bad_mode_fails_before_any_file_access(self):
        cs.save_settings(expected_revision=0, read_source="rest")
        before = _bytes()

        with pytest.raises(ValueError):
            cs.save_settings(expected_revision=1, read_source="mcp")

        assert _bytes() == before
        assert _backups() == []

    def test_the_error_names_the_allowed_modes(self):
        _write_json(_v3(read_source="mcp"))

        with pytest.raises(cs.SettingsFormatError) as excinfo:
            cs.read_settings()

        message = str(excinfo.value)
        assert "read_source" in message
        assert "'auto'" in message and "'rest'" in message


# ---------------------------------------------------------------------------
# Nothing else changed
# ---------------------------------------------------------------------------


class TestUnaffectedContracts:
    def test_a_stale_expected_revision_still_wins_over_the_gate(self):
        _write_json(_v3(read_source="auto"))

        with pytest.raises(cs.SettingsConflictError):
            cs.save_settings(expected_revision=0, read_source="rest")

        assert _stored()["read_source"] == "auto"

    def test_an_unsupported_future_version_is_still_rejected(self):
        payload = _v3()
        payload["version"] = 4
        _write_json(payload)

        with pytest.raises(cs.SettingsFormatError):
            cs.read_settings()

    def test_a_migrated_v1_document_reports_the_stored_source_version(self):
        _write_json(_v1())

        assert cs.read_settings().source_version == cs.SCHEMA_VERSION_V1
        _write_json(_v2())
        assert cs.read_settings().source_version == cs.SCHEMA_VERSION_V2
