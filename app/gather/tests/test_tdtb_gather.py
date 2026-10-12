#!/usr/bin/env python3
"""Tests for tdtb_gather.py -- the non-vault helpers that remain after the
vault gather was deleted (vault removal V4)."""
import json
import sys
import unittest
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
import tdtb_gather as g
import runstate as rs


class TestParseFrontmatter(unittest.TestCase):
    def test_basic_kv(self):
        fm = g.parse_frontmatter("---\nstatus: in-progress\nassigned: false\n---\n# Note")
        self.assertIsNotNone(fm)
        self.assertEqual(fm["status"], "in-progress")

    def test_returns_none_without_frontmatter(self):
        self.assertIsNone(g.parse_frontmatter("# Just a note\nno frontmatter"))

    def test_unterminated_frontmatter_is_none(self):
        self.assertIsNone(g.parse_frontmatter("---\nstatus: open\n"))

    def test_fallback_parser_reads_lists_and_bools(self):
        fm = g._parse_frontmatter_fallback("a: [x, y]\nb: true\nc: 'q'\nd:\n  - 1\n  - 2")
        self.assertEqual(fm, {"a": ["x", "y"], "b": True, "c": "q", "d": ["1", "2"]})


class TestEffectiveDate(unittest.TestCase):
    def test_after_two_am_is_today(self):
        self.assertEqual(g.effective_date(datetime(2026, 10, 12, 2, 0)), date(2026, 10, 12))

    def test_midnight_to_two_am_is_yesterday(self):
        self.assertEqual(g.effective_date(datetime(2026, 10, 12, 1, 59)), date(2026, 10, 11))


class TestEmptyRunData(unittest.TestCase):
    def test_keeps_the_gather_envelope_shape_with_no_rows(self):
        data = g.empty_run_data(date(2026, 10, 12))
        self.assertEqual(
            set(data),
            {"generated", "valid_date", "pool_count", "assigned_count",
             "pool_items", "assigned_items"},
        )
        self.assertEqual(data["valid_date"], "2026-10-12")
        self.assertEqual((data["pool_count"], data["assigned_count"]), (0, 0))
        self.assertEqual((data["pool_items"], data["assigned_items"]), ([], []))


class TestExtractJsonBlock(unittest.TestCase):
    def test_reads_the_fenced_block(self):
        self.assertEqual(g._extract_json_block('x\n```json\n{"a": 1}\n```\n'), {"a": 1})

    def test_non_object_and_garbage_are_none(self):
        self.assertIsNone(g._extract_json_block("```json\n[1]\n```"))
        self.assertIsNone(g._extract_json_block("```json\n{bad\n```"))
        self.assertIsNone(g._extract_json_block("no block"))


class TestLoadRunstate(unittest.TestCase):
    """``load_runstate`` reads the state-dir store only (TDTB_HOME is isolated
    per test by conftest)."""

    def _write(self, day: date, state: dict) -> None:
        directory = rs.state_runstate_dir()
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"tdtb-runstate-{day}.md").write_text(
            "```json\n" + json.dumps(state) + "\n```\n", encoding="utf-8"
        )

    def test_none_when_the_store_is_absent(self):
        self.assertEqual(g.load_runstate(date(2026, 10, 12)), (None, None))

    def test_returns_the_latest_strictly_prior_note(self):
        self._write(date(2026, 10, 9), {"marker": "old"})
        self._write(date(2026, 10, 10), {"marker": "prior"})
        self._write(date(2026, 10, 12), {"marker": "today"})
        base, state = g.load_runstate(date(2026, 10, 12))
        self.assertEqual(base, date(2026, 10, 10))
        self.assertEqual(state, {"marker": "prior"})

    def test_unparseable_prior_note_is_none(self):
        directory = rs.state_runstate_dir()
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "tdtb-runstate-2026-10-10.md").write_text("no json", encoding="utf-8")
        self.assertEqual(g.load_runstate(date(2026, 10, 12)), (None, None))


class TestNoVaultSurface(unittest.TestCase):
    def test_the_note_scan_is_gone(self):
        for name in ("walk_vault", "select_digest_notes", "build_cache", "write_cache",
                     "write_precompute_cache", "passes_base_filter", "SCAN_DIRS",
                     "CACHE_REL_PATH", "PRECOMPUTE_CACHE_REL_PATH", "main"):
            self.assertFalse(hasattr(g, name), name)


if __name__ == "__main__":
    unittest.main()
