#!/usr/bin/env python3
"""Tests for tdtb-gather.py — TDD gate for C1."""
import json
import sys
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

# Make the scripts directory importable
sys.path.insert(0, str(Path(__file__).parent.parent))
import tdtb_gather as g


TODAY = date(2026, 6, 6)


# ---------------------------------------------------------------------------
# parse_frontmatter
# ---------------------------------------------------------------------------

class TestParseFrontmatter(unittest.TestCase):
    def test_basic_kv(self):
        text = "---\nstatus: in-progress\nassigned: false\n---\n# Note"
        fm = g.parse_frontmatter(text)
        self.assertIsNotNone(fm)
        self.assertEqual(fm["status"], "in-progress")

    def test_returns_none_without_frontmatter(self):
        self.assertIsNone(g.parse_frontmatter("# Just a note\nno frontmatter"))

    def test_inline_list_type(self):
        text = "---\ntype: [project, task]\n---\n"
        fm = g.parse_frontmatter(text)
        self.assertIn("type", fm)
        types = g.get_types(fm)
        self.assertIn("project", types)
        self.assertIn("task", types)

    def test_boolean_assigned(self):
        text = "---\nassigned: true\n---\n"
        fm = g.parse_frontmatter(text)
        self.assertTrue(g.is_assigned_flag(fm))

    def test_empty_deadline_returns_none(self):
        text = "---\ndeadline:\n---\n"
        fm = g.parse_frontmatter(text)
        self.assertIsNone(g.get_deadline(fm))

    def test_date_deadline(self):
        text = "---\ndeadline: 2026-06-10\n---\n"
        fm = g.parse_frontmatter(text)
        self.assertEqual(g.get_deadline(fm), date(2026, 6, 10))


# ---------------------------------------------------------------------------
# get_types
# ---------------------------------------------------------------------------

class TestGetTypes(unittest.TestCase):
    def test_list_type(self):
        self.assertEqual(g.get_types({"type": ["project", "task"]}), {"project", "task"})

    def test_string_type(self):
        self.assertEqual(g.get_types({"type": "interval"}), {"interval"})

    def test_none_type(self):
        self.assertEqual(g.get_types({"type": None}), set())

    def test_missing_type(self):
        self.assertEqual(g.get_types({}), set())


# ---------------------------------------------------------------------------
# Folder exclusion
# ---------------------------------------------------------------------------

class TestIsExcludedFolder(unittest.TestCase):
    def test_excluded_zk(self):
        self.assertTrue(g.is_excluded_folder("20 - ZK/Concepts"))

    def test_excluded_atlas(self):
        self.assertTrue(g.is_excluded_folder("70 - Atlas"))

    def test_excluded_daily(self):
        self.assertTrue(g.is_excluded_folder("30 - Daily"))

    def test_excluded_archive(self):
        self.assertTrue(g.is_excluded_folder("90 - Archive/Projects"))

    def test_not_excluded_operations(self):
        self.assertFalse(g.is_excluded_folder("50 - Operations/Projects"))

    def test_not_excluded_meta(self):
        # 00 - META is not in the base filter exclusion list
        self.assertFalse(g.is_excluded_folder("00 - META/Bases"))


# ---------------------------------------------------------------------------
# is_open
# ---------------------------------------------------------------------------

class TestIsOpen(unittest.TestCase):
    def test_in_progress_is_open(self):
        self.assertTrue(g.is_open({"status": "in-progress"}))

    def test_todo_is_open(self):
        self.assertTrue(g.is_open({"status": "todo"}))

    def test_archived_closed(self):
        self.assertFalse(g.is_open({"status": "archived"}))

    def test_completed_closed(self):
        self.assertFalse(g.is_open({"status": "completed"}))

    def test_closed_closed(self):
        self.assertFalse(g.is_open({"status": "closed"}))

    def test_cancelled_closed(self):
        self.assertFalse(g.is_open({"status": "cancelled"}))

    def test_processed_closed(self):
        self.assertFalse(g.is_open({"status": "processed"}))


# ---------------------------------------------------------------------------
# compute_priority_score
# ---------------------------------------------------------------------------

class TestPriorityScore(unittest.TestCase):
    def _fm(self, *, urgency="", ret="", deadline=None):
        return {"urgency": urgency, "return": ret, "deadline": deadline}

    def test_no_deadline_no_urgency_no_return(self):
        # 0 + 1*4 + 0 = 4
        self.assertEqual(g.compute_priority_score(self._fm(), TODAY), 4)

    def test_overdue_with_4crit_urgency(self):
        # 100 + 4*4 + 0 = 116
        fm = self._fm(urgency="4-crit", deadline="2026-06-05")
        self.assertEqual(g.compute_priority_score(fm, TODAY), 116)

    def test_due_within_7_days(self):
        # 30 + 2*4 + 0 = 38
        fm = self._fm(urgency="2-med", deadline="2026-06-10")
        self.assertEqual(g.compute_priority_score(fm, TODAY), 38)

    def test_due_within_30_days(self):
        # 10 + 3*4 + 0 = 22
        fm = self._fm(urgency="3-high", deadline="2026-06-20")
        self.assertEqual(g.compute_priority_score(fm, TODAY), 22)

    def test_return_4_pivotal(self):
        # 0 + 1*4 + 4 = 8
        fm = self._fm(ret="4-pivotal")
        self.assertEqual(g.compute_priority_score(fm, TODAY), 8)

    def test_return_list_as_string(self):
        # return is a list; joined string should contain "3-solid"
        fm = {"urgency": "", "return": ["3-solid", "3-solid"], "deadline": None}
        # 0 + 1*4 + 3 = 7
        self.assertEqual(g.compute_priority_score(fm, TODAY), 7)

    def test_today_deadline_is_within_7(self):
        # deadline == today → 30 (not overdue; overdue is strictly < today)
        fm = self._fm(deadline="2026-06-06")
        score = g.compute_priority_score(fm, TODAY)
        self.assertEqual(score, 30 + 4)  # no urgency → 1*4=4; +30 deadline


# ---------------------------------------------------------------------------
# passes_base_filter
# ---------------------------------------------------------------------------

class TestPassesBaseFilter(unittest.TestCase):
    def _note(self, *, name="My Project", folder="50 - Operations/Projects",
              status="in-progress", assigned=False, types=None, urgency="", deadline=None, related=""):
        types = types or ["project"]
        fm = {
            "type": types, "status": status, "assigned": assigned,
            "urgency": urgency, "deadline": deadline, "relates_to": related,
        }
        return name, folder, fm

    def test_basic_open_project_passes(self):
        name, folder, fm = self._note()
        self.assertTrue(g.passes_base_filter(name, folder, fm))

    def test_template_name_fails(self):
        name, folder, fm = self._note(name="_template")
        self.assertFalse(g.passes_base_filter(name, folder, fm))

    def test_assigned_fails(self):
        name, folder, fm = self._note(assigned=True)
        self.assertFalse(g.passes_base_filter(name, folder, fm))

    def test_archived_fails(self):
        name, folder, fm = self._note(status="archived")
        self.assertFalse(g.passes_base_filter(name, folder, fm))

    def test_excluded_folder_fails(self):
        name, folder, fm = self._note(folder="20 - ZK/Notes")
        self.assertFalse(g.passes_base_filter(name, folder, fm))

    def test_capture_without_deadline_fails(self):
        name, folder, fm = self._note(types=["capture"])
        self.assertFalse(g.passes_base_filter(name, folder, fm))

    def test_capture_with_deadline_passes(self):
        name, folder, fm = self._note(types=["capture"], deadline="2026-07-01")
        self.assertTrue(g.passes_base_filter(name, folder, fm))

    def test_gift_without_deadline_fails(self):
        name, folder, fm = self._note(types=["gift"])
        self.assertFalse(g.passes_base_filter(name, folder, fm))


# ---------------------------------------------------------------------------
# passes_inclusion_filter
# ---------------------------------------------------------------------------

class TestPassesInclusionFilter(unittest.TestCase):
    def _note(self, *, types=None, urgency="", deadline=None, related=None):
        types = types or ["project"]
        fm = {
            "type": types, "urgency": urgency,
            "deadline": deadline, "relates_to": related or "",
        }
        return "Note", "50 - Operations/Projects", fm

    def test_4crit_passes(self):
        name, folder, fm = self._note(urgency="4-crit")
        self.assertTrue(g.passes_inclusion_filter(name, folder, fm, TODAY))

    def test_interval_due_today_passes(self):
        name, folder, fm = self._note(types=["interval"], deadline="2026-06-06")
        self.assertTrue(g.passes_inclusion_filter(name, folder, fm, TODAY))

    def test_interval_due_tomorrow_passes(self):
        name, folder, fm = self._note(types=["interval"], deadline="2026-06-07")
        self.assertTrue(g.passes_inclusion_filter(name, folder, fm, TODAY))

    def test_interval_not_due_soon_fails(self):
        name, folder, fm = self._note(types=["interval"], deadline="2026-06-20")
        self.assertFalse(g.passes_inclusion_filter(name, folder, fm, TODAY))

    def test_interval_no_deadline_fails(self):
        name, folder, fm = self._note(types=["interval"])
        self.assertFalse(g.passes_inclusion_filter(name, folder, fm, TODAY))

    def test_project_passes(self):
        name, folder, fm = self._note(types=["project"])
        self.assertTrue(g.passes_inclusion_filter(name, folder, fm, TODAY))

    def test_pursuit_passes(self):
        name, folder, fm = self._note(types=["pursuit"])
        self.assertTrue(g.passes_inclusion_filter(name, folder, fm, TODAY))

    def test_adventure_fails_standalone(self):
        # adventures have no relates_to but ARE excluded type → standalone fails
        name, folder, fm = self._note(types=["adventure"])
        self.assertFalse(g.passes_inclusion_filter(name, folder, fm, TODAY))

    def test_standalone_task_no_related_passes(self):
        name, folder, fm = self._note(types=["task"], related=None)
        self.assertTrue(g.passes_inclusion_filter(name, folder, fm, TODAY))

    def test_task_with_relates_to_fails(self):
        name, folder, fm = self._note(types=["task"], related="[[Some Project]]")
        self.assertFalse(g.passes_inclusion_filter(name, folder, fm, TODAY))

    def test_project_task_combo_passes(self):
        # [project, task] — project in types → heavy path
        name, folder, fm = self._note(types=["project", "task"])
        self.assertTrue(g.passes_inclusion_filter(name, folder, fm, TODAY))


# ---------------------------------------------------------------------------
# is_in_pool
# ---------------------------------------------------------------------------

class TestIsInPool(unittest.TestCase):
    def test_open_project_in_pool(self):
        fm = {"type": ["project"], "status": "in-progress", "assigned": False,
              "urgency": "3-high", "deadline": None, "relates_to": ""}
        self.assertTrue(g.is_in_pool("My Project", "50 - Operations/Projects", fm, TODAY))

    def test_assigned_project_not_in_pool(self):
        fm = {"type": ["project"], "status": "in-progress", "assigned": True,
              "urgency": "3-high", "deadline": None, "relates_to": ""}
        self.assertFalse(g.is_in_pool("My Project", "50 - Operations/Projects", fm, TODAY))

    def test_archived_project_not_in_pool(self):
        fm = {"type": ["project"], "status": "archived", "assigned": False,
              "urgency": "", "deadline": None, "relates_to": ""}
        self.assertFalse(g.is_in_pool("My Project", "50 - Operations/Projects", fm, TODAY))

    def test_interval_not_due_not_in_pool(self):
        fm = {"type": ["interval"], "status": "in-progress", "assigned": False,
              "urgency": "", "deadline": "2026-06-20", "relates_to": ""}
        self.assertFalse(g.is_in_pool("My Interval", "50 - Operations/Intervals", fm, TODAY))

    def test_interval_overdue_in_pool(self):
        fm = {"type": ["interval"], "status": "in-progress", "assigned": False,
              "urgency": "", "deadline": "2026-06-01", "relates_to": ""}
        self.assertTrue(g.is_in_pool("My Interval", "50 - Operations/Intervals", fm, TODAY))


# ---------------------------------------------------------------------------
# is_assigned (daily-assigned.base predicate)
# ---------------------------------------------------------------------------

class TestIsAssigned(unittest.TestCase):
    def test_assigned_active_project(self):
        fm = {"assigned": True, "status": "in-progress"}
        self.assertTrue(g.is_assigned("50 - Operations/Projects", fm))

    def test_assigned_but_archived(self):
        fm = {"assigned": True, "status": "archived"}
        self.assertFalse(g.is_assigned("50 - Operations/Projects", fm))

    def test_assigned_but_excluded_folder(self):
        fm = {"assigned": True, "status": "in-progress"}
        self.assertFalse(g.is_assigned("20 - ZK/Notes", fm))

    def test_not_assigned(self):
        fm = {"assigned": False, "status": "in-progress"}
        self.assertFalse(g.is_assigned("50 - Operations/Projects", fm))


# ---------------------------------------------------------------------------
# build_cache
# ---------------------------------------------------------------------------

class TestBuildCache(unittest.TestCase):
    def _make_note(self, name, path="50 - Operations/Projects/Foo.md", types=None,
                   urgency="", deadline=None, status="in-progress"):
        types = types or ["project"]
        fm = {"type": types, "urgency": urgency, "deadline": deadline,
              "return": "", "status": status, "assigned": False, "relates_to": ""}
        return {"name": name, "path": path, "folder": "50 - Operations/Projects", "fm": fm}

    def test_cache_structure(self):
        notes = [self._make_note("Foo"), self._make_note("Bar")]
        cache = g.build_cache(notes, TODAY)
        self.assertEqual(cache["schema_version"], 2)
        self.assertEqual(cache["parent_count"], 2)
        self.assertEqual(len(cache["parents"]), 2)
        self.assertEqual(cache["valid_date"], "2026-06-06")

    def test_cache_sorted_by_priority_desc(self):
        n1 = self._make_note("Low", urgency="1-low")
        n2 = self._make_note("High", urgency="4-crit")
        cache = g.build_cache([n1, n2], TODAY)
        self.assertEqual(cache["parents"][0]["name"], "High")
        self.assertEqual(cache["parents"][1]["name"], "Low")

    def test_empty_pool(self):
        cache = g.build_cache([], TODAY)
        self.assertEqual(cache["parent_count"], 0)
        self.assertEqual(cache["parents"], [])


# ---------------------------------------------------------------------------
# _cache_to_markdown round-trip
# ---------------------------------------------------------------------------

class TestCacheMarkdown(unittest.TestCase):
    def test_frontmatter_delimiters(self):
        cache = g.build_cache([], TODAY)
        md = g._cache_to_markdown(cache)
        self.assertTrue(md.startswith("---\n"))
        self.assertIn("\n---\n", md)

    def test_valid_date_in_output(self):
        cache = g.build_cache([], TODAY)
        md = g._cache_to_markdown(cache)
        self.assertIn("valid_date: '2026-06-06'", md)


# ---------------------------------------------------------------------------
# CLI dry-run (smoke test without actual vault)
# ---------------------------------------------------------------------------

class TestCLIDryRun(unittest.TestCase):
    def test_dry_run_with_no_scan_dirs(self, *_):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            ret = g.main(["--vault-root", tmp, "--dry-run"])
        self.assertEqual(ret, 0)

    def test_missing_vault_root_returns_1(self):
        ret = g.main(["--vault-root", "/nonexistent/path/that/does/not/exist"])
        self.assertEqual(ret, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)


# ---------------------------------------------------------------------------
# Eligibility escape hatch (resurfacing-wiring 2026-07-02, T5 Option A)
# ---------------------------------------------------------------------------

class TestEscapeHatch(unittest.TestCase):
    """Hatch: non-interval open item with urgency >= 3-high OR non-empty deadline
    is in the pool regardless of type exclusion or child-hidden status."""

    def test_hatch_admits_child_hidden_3high_task(self):
        fm = {"type": "task", "status": "todo", "urgency": "3-high",
              "relates_to": "[[Tune]]"}
        self.assertTrue(g.is_in_pool("n", "50 - Operations/Tasks", fm, TODAY))

    def test_hatch_admits_excluded_shop_with_deadline(self):
        fm = {"type": "shop", "status": "todo", "deadline": "2026-09-01"}
        self.assertTrue(g.is_in_pool("n", "50 - Operations/Shop", fm, TODAY))

    def test_hatch_admits_4crit_shop(self):
        fm = {"type": "shop", "status": "todo", "urgency": "4-crit"}
        self.assertTrue(g.is_in_pool("n", "50 - Operations/Shop", fm, TODAY))

    def test_hatch_does_not_admit_interval_early(self):
        fm = {"type": "interval", "status": "in-progress", "urgency": "3-high",
              "deadline": "2026-09-01", "recurrence": "3"}
        self.assertFalse(g.is_in_pool("n", "50 - Operations/Intervals", fm, TODAY))

    def test_low_urgency_no_deadline_shop_still_out(self):
        fm = {"type": "shop", "status": "todo", "urgency": "2-med"}
        self.assertFalse(g.is_in_pool("n", "50 - Operations/Shop", fm, TODAY))

    def test_terminal_status_never_hatched(self):
        fm = {"type": "shop", "status": "completed", "urgency": "3-high"}
        self.assertFalse(g.is_in_pool("n", "50 - Operations/Shop", fm, TODAY))


class TestHatchOnlyFolders(unittest.TestCase):
    """Newly-scanned folders (Tasks/Shop/Print/Gifts) admit ONLY via hatch or
    4-crit — the standalone unparented gate does not apply there (Option A:
    keeps 60+ unsignalled unparented tasks out of the pool)."""

    def test_unsignalled_unparented_task_stays_out(self):
        fm = {"type": "task", "status": "todo"}
        self.assertFalse(g.is_in_pool("n", "50 - Operations/Tasks", fm, TODAY))

    def test_unsignalled_unparented_capture_keeps_old_behavior(self):
        # core folder: base filter still requires capture+deadline; with a
        # deadline it passes as before
        fm = {"type": "capture", "status": "todo", "deadline": "2026-06-07"}
        self.assertTrue(g.is_in_pool("n", "05 - Capture", fm, TODAY))

    def test_scan_dirs_fixed(self):
        self.assertIn("50 - Operations/Tasks", g.SCAN_DIRS)
        self.assertIn("50 - Operations/Shop", g.SCAN_DIRS)
        self.assertIn("50 - Operations/Print", g.SCAN_DIRS)
        self.assertIn("50 - Operations/Gifts", g.SCAN_DIRS)
        for phantom in ("50 - Operations/Shops", "50 - Operations/Prints",
                        "50 - Operations/Captures"):
            self.assertNotIn(phantom, g.SCAN_DIRS)
