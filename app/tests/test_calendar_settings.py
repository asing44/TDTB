"""Tests for the app-owned per-calendar on/off store (``calendar_settings``).

The store replaces the retired config ``calendar.disabled`` list. Contract
pins: missing file = every calendar on (and no file created), strict decode,
sorted ids, duplicate/oversize rejection, revision-guarded atomic save, and a
one-time idempotent migration off the legacy config list.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import calendar_settings as cs  # noqa: E402


def _write(payload) -> Path:
    path = cs.settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _read_raw() -> dict:
    return json.loads(cs.settings_path().read_text(encoding="utf-8"))


class TestDefaults:
    def test_missing_file_is_all_on_and_creates_nothing(self):
        result = cs.read_settings()
        assert result.persisted is False
        assert result.settings.revision == 0
        assert result.settings.calendars == ()
        assert not cs.settings_path().exists()
        assert result.settings.is_enabled("any-calendar") is True

    def test_unseen_calendar_defaults_on(self):
        settings = cs.CalendarSettings(
            calendars=(cs.CalendarSetting(id="b", enabled=False),)
        )
        assert settings.is_enabled("a") is True
        assert settings.is_enabled("b") is False
        assert settings.off_ids() == ["b"]


class TestSave:
    def test_round_trip_sorted_and_revisioned(self):
        saved = cs.save_settings(
            expected_revision=0,
            calendars=[
                cs.CalendarSetting(id="z", title="Zed", enabled=False),
                cs.CalendarSetting(id="a", title="Ann", enabled=True),
            ],
        )
        assert saved.revision == 1
        assert [row.id for row in saved.calendars] == ["a", "z"]
        stored = cs.read_settings()
        assert stored.persisted is True
        assert stored.settings.as_dict() == {
            "version": 1,
            "revision": 1,
            "calendars": [
                {"id": "a", "title": "Ann", "enabled": True},
                {"id": "z", "title": "Zed", "enabled": False},
            ],
        }

    def test_stale_revision_conflicts_and_preserves_bytes(self):
        cs.save_settings(expected_revision=0, calendars=[])
        before = cs.settings_path().read_bytes()
        with pytest.raises(cs.CalendarSettingsConflictError):
            cs.save_settings(expected_revision=0, calendars=[])
        assert cs.settings_path().read_bytes() == before

    def test_duplicate_ids_rejected(self):
        with pytest.raises(ValueError):
            cs.save_settings(
                expected_revision=0,
                calendars=[
                    cs.CalendarSetting(id="a"),
                    cs.CalendarSetting(id="a"),
                ],
            )
        assert not cs.settings_path().exists()

    def test_too_many_calendars_rejected(self):
        with pytest.raises(ValueError):
            cs.save_settings(
                expected_revision=0,
                calendars=[
                    cs.CalendarSetting(id=f"c{index}")
                    for index in range(cs.MAX_CALENDARS + 1)
                ],
            )
        assert not cs.settings_path().exists()

    def test_strict_types(self):
        with pytest.raises(ValueError):
            cs.CalendarSetting(id="a", enabled=1)  # int is not a bool
        with pytest.raises(ValueError):
            cs.CalendarSetting(id="")
        with pytest.raises(ValueError):
            cs.CalendarSetting(id="a", title=None)
        with pytest.raises(ValueError):
            cs.CalendarSettings(revision=-1)

    def test_locked_calendars_cannot_be_saved_off(self):
        saved = cs.save_settings(
            expected_revision=0,
            calendars=[cs.CalendarSetting(id="own", enabled=False)],
            locked_on_ids={"own"},
        )
        assert saved.calendars[0].enabled is True


class TestFailClosed:
    @pytest.mark.parametrize(
        "payload",
        [
            {"version": 2, "revision": 0, "calendars": []},
            {"version": 1, "revision": 0},
            {"version": 1, "revision": 0, "calendars": [], "extra": True},
            {"version": 1, "revision": -1, "calendars": []},
            {"version": 1, "revision": 0, "calendars": [{"id": "a"}]},
            {"version": 1, "revision": 0, "calendars": [{"id": "a", "title": "", "enabled": "yes"}]},
            {"version": 1, "revision": 0, "calendars": [{"id": "a", "title": "", "enabled": True}, {"id": "a", "title": "", "enabled": True}]},
        ],
    )
    def test_malformed_storage_raises_and_is_preserved(self, payload):
        path = _write(payload)
        before = path.read_bytes()
        with pytest.raises(cs.CalendarSettingsFormatError):
            cs.read_settings()
        with pytest.raises(cs.CalendarSettingsFormatError):
            cs.save_settings(expected_revision=0, calendars=[])
        assert path.read_bytes() == before

    def test_duplicate_json_keys_rejected(self):
        path = cs.settings_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            '{"version": 1, "revision": 0, "revision": 0, "calendars": []}',
            encoding="utf-8",
        )
        with pytest.raises(cs.CalendarSettingsFormatError):
            cs.read_settings()

    def test_unparseable_bytes_preserved(self):
        path = cs.settings_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("not json at all", encoding="utf-8")
        with pytest.raises(cs.CalendarSettingsFormatError):
            cs.read_settings()
        assert path.read_text(encoding="utf-8") == "not json at all"


INVENTORY = [
    {"id": "CAL-P", "title": "Personal"},
    {"id": "CAL-W", "title": "Work"},
]


class TestLegacyMigration:
    def test_migrates_titles_and_ids_to_revision_one(self):
        read, unresolved = cs.migrate_legacy_disabled(
            entries=["personal", "CAL-W"], inventory=INVENTORY
        )
        assert unresolved == []
        assert read.persisted is True
        assert read.settings.revision == 1
        assert [
            (row.id, row.title, row.enabled) for row in read.settings.calendars
        ] == [("CAL-P", "Personal", False), ("CAL-W", "Work", False)]

    def test_idempotent_and_never_rewrites(self):
        first, _ = cs.migrate_legacy_disabled(
            entries=["Personal"], inventory=INVENTORY
        )
        before = cs.settings_path().read_bytes()
        # A later read with different legacy entries must not rewrite.
        second, _ = cs.migrate_legacy_disabled(
            entries=["Work"], inventory=INVENTORY
        )
        assert second.persisted is True
        assert second.settings.as_dict() == first.settings.as_dict()
        assert cs.settings_path().read_bytes() == before

    def test_unresolved_entries_write_nothing(self):
        read, unresolved = cs.migrate_legacy_disabled(
            entries=["Nope"], inventory=INVENTORY
        )
        assert read.persisted is False
        assert unresolved == ["Nope"]
        assert not cs.settings_path().exists()

    def test_unreadable_inventory_writes_nothing(self):
        read, _ = cs.migrate_legacy_disabled(entries=["Personal"], inventory=[])
        assert read.persisted is False
        assert not cs.settings_path().exists()
