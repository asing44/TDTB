"""Tests for the app-owned per-habit settings store (``habit_settings``).

Contract pins (U2 of the Grit-habits work): missing file = empty plan and no
file created, strict decode, revision-guarded atomic full replace, malformed
storage preserved byte for byte, per-row validation with row/field identity,
name identity (trimmed, case-sensitive, sorted), weekday numbering matching
``datetime.date.weekday()`` (Monday = 0), and the two read-only helpers the
later units consume (``due_today`` / ``inventory``).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import habit_settings as hs  # noqa: E402


def _write(payload) -> Path:
    path = hs.settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _write_raw(text: str) -> Path:
    path = hs.settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _stored() -> dict:
    return json.loads(hs.settings_path().read_text(encoding="utf-8"))


class TestDefaults:
    def test_missing_file_is_empty_and_creates_nothing(self):
        result = hs.load()
        assert result.persisted is False
        assert result.settings.revision == 0
        assert result.settings.habits == ()
        assert not hs.settings_path().exists()

    def test_state_file_lives_beside_the_lock_in_the_state_dir(self):
        assert hs.settings_path().name == "habit_settings.json"
        assert hs.settings_path().parent.name == "state"
        assert hs.lock_path().parent == hs.settings_path().parent


class TestSave:
    def test_round_trip_sorted_and_revisioned(self):
        saved = hs.save_full_replace(
            [
                {"name": "Stretch", "minutes": 10, "weekdays": [2, 0]},
                {"name": "Walk", "minutes": 30},
                {"name": "Read", "minutes": 20, "weekdays": None},
            ],
            expected_revision=0,
        )
        assert saved.revision == 1
        assert [row.name for row in saved.habits] == ["Read", "Stretch", "Walk"]
        stored = hs.load()
        assert stored.persisted is True
        assert stored.settings.as_dict() == {
            "version": 1,
            "revision": 1,
            "habits": [
                {"name": "Read", "minutes": 20, "weekdays": None},
                {"name": "Stretch", "minutes": 10, "weekdays": [0, 2]},
                {"name": "Walk", "minutes": 30, "weekdays": None},
            ],
        }
        assert _stored() == stored.settings.as_dict()

    def test_revision_increments_monotonically(self):
        first = hs.save_full_replace([{"name": "Walk", "minutes": 30}], 0)
        second = hs.save_full_replace([{"name": "Walk", "minutes": 45}], 1)
        third = hs.save_full_replace([], 2)
        assert (first.revision, second.revision, third.revision) == (1, 2, 3)
        assert hs.load().settings.revision == 3
        assert hs.load().settings.habits == ()

    def test_stale_revision_conflicts_and_preserves_bytes(self):
        hs.save_full_replace([{"name": "Walk", "minutes": 30}], 0)
        before = hs.settings_path().read_bytes()
        with pytest.raises(hs.HabitSettingsConflictError):
            hs.save_full_replace([{"name": "Run", "minutes": 15}], 0)
        assert hs.settings_path().read_bytes() == before

    def test_name_identity_is_trimmed_sorted_and_case_sensitive(self):
        saved = hs.save_full_replace(
            [
                {"name": "  Walk  ", "minutes": 30},
                {"name": "walk", "minutes": 5},
            ],
            0,
        )
        assert [row.name for row in saved.habits] == ["Walk", "walk"]

    def test_weekdays_normalized_and_all_null_preserved(self):
        saved = hs.save_full_replace(
            [
                {"name": "Weekday", "minutes": 10, "weekdays": [4, 1, 3]},
                {"name": "Anytime", "minutes": 10},
            ],
            0,
        )
        assert [row.name for row in saved.habits] == ["Anytime", "Weekday"]
        assert saved.habits[0].weekdays is None
        assert saved.habits[1].weekdays == (1, 3, 4)
        assert _stored()["habits"][0]["weekdays"] is None
        assert _stored()["habits"][1]["weekdays"] == [1, 3, 4]

    def test_sunday_is_six(self):
        saved = hs.save_full_replace(
            [{"name": "Walk", "minutes": 30, "weekdays": [6]}], 0
        )
        assert saved.habits[0].weekdays == (6,)


class TestInvalidRows:
    def _assert_rejected(self, row, *, index, field):
        with pytest.raises(hs.HabitSettingsInvalidRowError) as excinfo:
            hs.save_full_replace(
                [{"name": "Walk", "minutes": 30}, row], expected_revision=0
            )
        assert excinfo.value.index == index
        assert excinfo.value.field == field
        assert not hs.settings_path().exists()

    @pytest.mark.parametrize("minutes", [-1, 481, "30", 30.0, True, None])
    def test_minutes_rejected(self, minutes):
        self._assert_rejected(
            {"name": "Bad", "minutes": minutes}, index=1, field="minutes"
        )

    @pytest.mark.parametrize(
        "weekdays", [[], [7], [-1], ["0"], [0, 0], [1.0], "Mon", 0]
    )
    def test_weekdays_rejected(self, weekdays):
        self._assert_rejected(
            {"name": "Bad", "minutes": 10, "weekdays": weekdays},
            index=1,
            field="weekdays",
        )

    @pytest.mark.parametrize("name", ["", "   ", 5, None])
    def test_name_rejected(self, name):
        self._assert_rejected({"name": name, "minutes": 10}, index=1, field="name")

    def test_overlong_name_rejected(self):
        self._assert_rejected(
            {"name": "x" * (hs.MAX_NAME_LENGTH + 1), "minutes": 10},
            index=1,
            field="name",
        )

    def test_name_at_cap_accepted(self):
        saved = hs.save_full_replace(
            [{"name": "x" * hs.MAX_NAME_LENGTH, "minutes": 10}], 0
        )
        assert len(saved.habits[0].name) == hs.MAX_NAME_LENGTH

    def test_duplicate_names_rejected(self):
        with pytest.raises(hs.HabitSettingsInvalidRowError) as excinfo:
            hs.save_full_replace(
                [
                    {"name": "Walk", "minutes": 30},
                    {"name": " Walk ", "minutes": 15},
                ],
                expected_revision=0,
            )
        assert excinfo.value.index == 1
        assert excinfo.value.field == "name"
        assert not hs.settings_path().exists()

    def test_unknown_row_key_rejected(self):
        with pytest.raises(hs.HabitSettingsInvalidRowError):
            hs.save_full_replace(
                [{"name": "Walk", "minutes": 30, "color": "red"}],
                expected_revision=0,
            )

    def test_expected_revision_is_strict(self):
        for bad in (True, -1, "0", 1.0, None):
            with pytest.raises(ValueError):
                hs.save_full_replace([], bad)
        assert not hs.settings_path().exists()

    def test_habits_argument_must_be_a_collection(self):
        for bad in ("Walk", {"name": "Walk", "minutes": 30}, 5, None):
            with pytest.raises(hs.HabitSettingsInvalidRowError):
                hs.save_full_replace(bad, 0)
        assert not hs.settings_path().exists()


class TestFailClosed:
    @pytest.mark.parametrize(
        "payload",
        [
            {"version": 2, "revision": 0, "habits": []},
            {"version": 1, "revision": 0},
            {"version": 1, "revision": 0, "habits": [], "extra": True},
            {"version": 1, "revision": -1, "habits": []},
            {"version": 1, "revision": 0, "habits": {}},
            {"version": 1, "revision": 0, "habits": [{"name": "Walk"}]},
            {
                "version": 1,
                "revision": 0,
                "habits": [{"name": "Walk", "minutes": 30}],
            },
            {
                "version": 1,
                "revision": 0,
                "habits": [
                    {"name": "Walk", "minutes": 30, "weekdays": None, "color": "r"}
                ],
            },
            {
                "version": 1,
                "revision": 0,
                "habits": [{"name": "Walk", "minutes": 30, "weekdays": []}],
            },
            {
                "version": 1,
                "revision": 0,
                "habits": [{"name": "Walk", "minutes": "30", "weekdays": None}],
            },
            {
                "version": 1,
                "revision": 0,
                "habits": [{"name": "Walk", "minutes": 30, "weekdays": [7]}],
            },
            {
                "version": 1,
                "revision": 0,
                "habits": [
                    {"name": "Walk", "minutes": 30, "weekdays": None},
                    {"name": "Walk", "minutes": 15, "weekdays": None},
                ],
            },
        ],
    )
    def test_malformed_storage_raises_and_is_preserved(self, payload):
        path = _write(payload)
        before = path.read_bytes()
        with pytest.raises(hs.HabitSettingsFormatError):
            hs.load()
        with pytest.raises(hs.HabitSettingsFormatError):
            hs.save_full_replace([{"name": "Run", "minutes": 5}], 0)
        assert path.read_bytes() == before

    def test_duplicate_json_keys_rejected(self):
        _write_raw(
            '{"version": 1, "revision": 0, "revision": 0, "habits": []}'
        )
        with pytest.raises(hs.HabitSettingsFormatError):
            hs.load()

    def test_unparseable_bytes_preserved(self):
        _write_raw("not json at all")
        with pytest.raises(hs.HabitSettingsFormatError):
            hs.load()
        with pytest.raises(hs.HabitSettingsFormatError):
            hs.save_full_replace([], 0)
        assert hs.settings_path().read_text(encoding="utf-8") == "not json at all"

    def test_error_hierarchy_separates_storage_from_row_errors(self):
        assert issubclass(hs.HabitSettingsFormatError, hs.HabitSettingsStoreError)
        assert issubclass(hs.HabitSettingsConflictError, hs.HabitSettingsStoreError)
        assert not issubclass(
            hs.HabitSettingsInvalidRowError, hs.HabitSettingsStoreError
        )


def _settings(rows, revision=1) -> hs.HabitSettings:
    return hs.HabitSettings(
        revision=revision,
        habits=tuple(
            hs.HabitSetting(name=name, minutes=minutes, weekdays=weekdays)
            for name, minutes, weekdays in rows
        ),
    )


class TestDueToday:
    def test_null_weekdays_is_every_day(self):
        settings = _settings([("Walk", 30, None)])
        for weekday in range(7):
            assert [row.name for row in hs.due_today(settings, weekday)] == ["Walk"]

    def test_list_weekdays_selects_only_those_days(self):
        settings = _settings([("Stretch", 10, (0, 2, 4)), ("Walk", 30, None)])
        assert [row.name for row in hs.due_today(settings, 0)] == ["Stretch", "Walk"]
        assert [row.name for row in hs.due_today(settings, 1)] == ["Walk"]
        assert [row.name for row in hs.due_today(settings, 6)] == ["Walk"]

    def test_weekday_bounds_enforced(self):
        settings = _settings([("Walk", 30, None)])
        for bad in (-1, 7, True, "0", 1.0):
            with pytest.raises(ValueError):
                hs.due_today(settings, bad)

    def test_requires_settings_object(self):
        with pytest.raises(TypeError):
            hs.due_today({"habits": []}, 0)


class TestInventory:
    def test_configured_unconfigured_missing_and_rename(self):
        settings = _settings(
            [
                ("Walk", 30, None),
                ("Old name", 10, (1,)),
                ("Read", 20, (4,)),
            ]
        )
        rows = hs.inventory(settings, ["Read", "Walk", "New name"])
        assert rows == [
            {
                "name": "New name",
                "status": "unconfigured",
                "minutes": None,
                "weekdays": None,
            },
            {
                "name": "Old name",
                "status": "missing",
                "minutes": 10,
                "weekdays": [1],
            },
            {"name": "Read", "status": "configured", "minutes": 20, "weekdays": [4]},
            {
                "name": "Walk",
                "status": "configured",
                "minutes": 30,
                "weekdays": None,
            },
        ]

    def test_missing_rows_are_never_dropped_from_storage(self):
        hs.save_full_replace([{"name": "Old name", "minutes": 10}], 0)
        rows = hs.inventory(hs.load().settings, ["New name"])
        assert [(row["name"], row["status"]) for row in rows] == [
            ("New name", "unconfigured"),
            ("Old name", "missing"),
        ]
        assert hs.load().settings.habits[0].name == "Old name"

    def test_grit_names_are_trimmed_deduped_and_blanks_skipped(self):
        settings = _settings([("Walk", 30, None)])
        rows = hs.inventory(settings, ["  Walk  ", "Walk", "", "   ", "Run"])
        assert [(row["name"], row["status"]) for row in rows] == [
            ("Run", "unconfigured"),
            ("Walk", "configured"),
        ]

    def test_empty_inputs(self):
        assert hs.inventory(hs.HabitSettings(), []) == []
        assert [
            (row["name"], row["status"])
            for row in hs.inventory(_settings([("Walk", 30, None)]), [])
        ] == [("Walk", "missing")]

    def test_non_string_grit_name_rejected(self):
        with pytest.raises(ValueError):
            hs.inventory(hs.HabitSettings(), [5])
