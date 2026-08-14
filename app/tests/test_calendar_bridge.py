"""Pure-logic tests for calendar_bridge (no pyobjc import — EventStore is
manually verified per the T4 gate)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from calendar_bridge import (  # noqa: E402
    CalendarInfo,
    CalendarWriteError,
    assert_write_target,
    resolve_calendar_ids,
)

LIVE = [
    CalendarInfo("⬜ Blocks", "F5EEE5A4", True, "Google"),
    CalendarInfo("🟡 Mint", "7FA0FDE1", True, "Google"),
    CalendarInfo("Birthdays", "EA332AE7", True, "Exchange"),
    CalendarInfo("Birthdays", "78D424EE", False, "Other"),
    CalendarInfo("US Holidays", "012BB73E", False, "Subscribed Calendars"),
]


class TestResolve(unittest.TestCase):
    def test_resolves_exact_titles(self):
        resolved, failures = resolve_calendar_ids(
            {"blocks": "⬜ Blocks", "mint": "🟡 Mint"}, LIVE
        )
        self.assertEqual(resolved, {"blocks": "F5EEE5A4", "mint": "7FA0FDE1"})
        self.assertEqual(failures, [])

    def test_missing_title_reported_not_guessed(self):
        resolved, failures = resolve_calendar_ids(
            {"trinoor": "🟡 Trinoor", "mint": "🟡 Mint"}, LIVE
        )
        self.assertEqual(resolved, {"mint": "7FA0FDE1"})
        self.assertEqual(failures, ["trinoor"])

    def test_duplicate_title_prefers_writable(self):
        resolved, failures = resolve_calendar_ids({"bday": "Birthdays"}, LIVE)
        self.assertEqual(resolved["bday"], "EA332AE7")
        self.assertEqual(failures, [])

    def test_empty_map(self):
        self.assertEqual(resolve_calendar_ids({}, LIVE), ({}, []))


class TestAssertWriteTarget(unittest.TestCase):
    def test_valid_writable_target(self):
        cal = assert_write_target("F5EEE5A4", LIVE)
        self.assertEqual(cal.title, "⬜ Blocks")

    def test_unknown_id_raises(self):
        with self.assertRaises(CalendarWriteError):
            assert_write_target("DEADBEEF", LIVE)

    def test_readonly_target_raises(self):
        with self.assertRaises(CalendarWriteError):
            assert_write_target("012BB73E", LIVE)

    def test_error_names_readonly_calendar(self):
        try:
            assert_write_target("012BB73E", LIVE)
        except CalendarWriteError as e:
            self.assertIn("US Holidays", str(e))


if __name__ == "__main__":
    unittest.main()
