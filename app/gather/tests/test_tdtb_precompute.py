#!/usr/bin/env python3
"""Tests for tdtb_gather.py --precompute / --precompute-commit — TDD gate for
the universal-precompute plan (2026-07-01), sub-lever A."""
import io
import json
import sys
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import tdtb_gather as g

VALID = date(2026, 7, 2)


def make_vault(root: Path) -> Path:
    (root / "00 - META/Cache").mkdir(parents=True)
    (root / "50 - Operations/Intervals").mkdir(parents=True)
    (root / "50 - Operations/Pursuits").mkdir(parents=True)
    return root


def write_runstate(root: Path, d: str, selections: list, extra: str = "") -> None:
    body = json.dumps({
        "anchor": "10:15", "eod": "23:45", "buffering": "standard",
        "selections": selections})
    (root / f"00 - META/Cache/tdtb-runstate-{d}.md").write_text(
        f"---\nvalid_date: {d}\nwritten_at: {d}T10:00:00-04:00\n---\n\n```json\n{body}\n```\n")


def note(root: Path, rel: str, fm: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f"---\n{fm}\n---\nbody\n")


class TestEffectiveDate(unittest.TestCase):
    def test_after_2am_is_today(self):
        self.assertEqual(g.effective_date(datetime(2026, 7, 2, 3, 4)), date(2026, 7, 2))

    def test_before_2am_is_yesterday(self):
        self.assertEqual(g.effective_date(datetime(2026, 7, 2, 1, 59)), date(2026, 7, 1))


class TestLoadRunstate(unittest.TestCase):
    def test_picks_latest_before_valid_date(self):
        with tempfile.TemporaryDirectory() as td:
            root = make_vault(Path(td))
            write_runstate(root, "2026-06-30", [])
            write_runstate(root, "2026-07-01", [{"id": "x", "source": "vault",
                           "path": "50 - Operations/Intervals/Press.md", "name": "Press", "blocks": 0}])
            diff_base, rs = g.load_runstate(root, VALID)
            self.assertEqual(diff_base, date(2026, 7, 1))
            self.assertEqual(rs["selections"][0]["id"], "x")

    def test_missing_returns_none(self):
        with tempfile.TemporaryDirectory() as td:
            root = make_vault(Path(td))
            diff_base, rs = g.load_runstate(root, VALID)
            self.assertIsNone(diff_base)
            self.assertIsNone(rs)

    def test_ignores_runstate_on_or_after_valid_date(self):
        with tempfile.TemporaryDirectory() as td:
            root = make_vault(Path(td))
            write_runstate(root, "2026-07-02", [])
            diff_base, rs = g.load_runstate(root, VALID)
            self.assertIsNone(diff_base)


class TestPrecomputeRequest(unittest.TestCase):
    def _run(self, root):
        pool, assigned = [], []
        for n in g.walk_vault(root):
            if g.is_assigned(n["folder"], n["fm"]):
                assigned.append(n)
            if g.is_in_pool(n["name"], n["folder"], n["fm"], VALID):
                pool.append(n)
        diff_base, rs = g.load_runstate(root, VALID)
        return g.build_precompute_request(pool, assigned, rs, diff_base, VALID)

    def test_new_due_candidate(self):
        with tempfile.TemporaryDirectory() as td:
            root = make_vault(Path(td))
            write_runstate(root, "2026-07-01", [])
            note(root, "50 - Operations/Intervals/Rowing.md",
                 f"type: interval\nstatus: in-progress\ndeadline: {VALID}")
            req = self._run(root)
            kinds = {(c["kind"], c["item"]) for c in req["delta_candidates"]}
            self.assertIn(("new-due", "Rowing"), kinds)

    def test_carried_and_not_new_due_when_selected_yesterday(self):
        with tempfile.TemporaryDirectory() as td:
            root = make_vault(Path(td))
            note(root, "50 - Operations/Intervals/Rowing.md",
                 f"type: interval\nstatus: in-progress\ndeadline: {VALID}")
            write_runstate(root, "2026-07-01", [{"id": "rowing", "source": "vault",
                           "path": "50 - Operations/Intervals/Rowing.md", "name": "Rowing", "blocks": 1}])
            req = self._run(root)
            self.assertEqual([c["path"] for c in req["carried_candidates"]],
                             ["50 - Operations/Intervals/Rowing.md"])
            self.assertNotIn("new-due", {c["kind"] for c in req["delta_candidates"]})

    def test_dropped_candidate_when_selected_note_closed(self):
        with tempfile.TemporaryDirectory() as td:
            root = make_vault(Path(td))
            note(root, "50 - Operations/Intervals/Gone.md",
                 "type: interval\nstatus: archived")
            write_runstate(root, "2026-07-01", [{"id": "gone", "source": "vault",
                           "path": "50 - Operations/Intervals/Gone.md", "name": "Gone", "blocks": 1}])
            req = self._run(root)
            kinds = {(c["kind"], c["item"]) for c in req["delta_candidates"]}
            self.assertIn(("dropped", "Gone"), kinds)

    def test_newly_assigned_candidate(self):
        with tempfile.TemporaryDirectory() as td:
            root = make_vault(Path(td))
            write_runstate(root, "2026-07-01", [])
            note(root, "50 - Operations/Pursuits/Guitar.md",
                 "type: pursuit\nstatus: todo\nassigned: true")
            req = self._run(root)
            kinds = {(c["kind"], c["item"]) for c in req["delta_candidates"]}
            self.assertIn(("newly-assigned", "Guitar"), kinds)

    def test_no_runstate_degrades_to_fresh(self):
        with tempfile.TemporaryDirectory() as td:
            root = make_vault(Path(td))
            note(root, "50 - Operations/Intervals/Rowing.md",
                 f"type: interval\nstatus: in-progress\ndeadline: {VALID}")
            req = self._run(root)
            self.assertIsNone(req["diff_base"])
            self.assertEqual(req["carried_candidates"], [])
            self.assertEqual(req["proposed_base"], {})

    def test_proposed_base_from_runstate(self):
        with tempfile.TemporaryDirectory() as td:
            root = make_vault(Path(td))
            write_runstate(root, "2026-07-01", [])
            req = self._run(root)
            self.assertEqual(req["proposed_base"]["anchor"], "10:15")
            self.assertEqual(req["proposed_base"]["eod"], "23:45")

    def test_todoist_selection_flagged_for_verification(self):
        with tempfile.TemporaryDirectory() as td:
            root = make_vault(Path(td))
            write_runstate(root, "2026-07-01", [{"id": "screw", "source": "todoist",
                           "task_id": "abc123", "name": "Screw legs", "blocks": 1}])
            req = self._run(root)
            self.assertEqual(req["carried_candidates"][0]["verify"], "todoist")


class TestWritePrecomputeCache(unittest.TestCase):
    def test_writes_frontmatter_and_json_body(self):
        with tempfile.TemporaryDirectory() as td:
            root = make_vault(Path(td))
            payload = {
                "proposed": {"anchor": "10:15", "eod": "23:45", "buffering": "standard",
                             "carried_selections": []},
                "delta": [{"kind": "new-due", "item": "Rowing", "ref": "50 - Operations/Intervals/Rowing.md",
                           "detail": "due today", "proposal": "add 1 blk", "rationale": "deadline"}],
                "pool": [],
                "sources": ["vault", "todoist"],
            }
            g.write_precompute_cache(payload, root, VALID, date(2026, 7, 1))
            out = (root / "00 - META/Cache/tdtb-precompute-cache.md").read_text()
            self.assertIn("schema_version: 1", out)
            self.assertIn("valid_date: '2026-07-02'", out)
            self.assertIn("diff_base: '2026-07-01'", out)
            self.assertIn("sources: [vault, todoist]", out)
            body = json.loads(out.split("```json\n")[1].split("\n```")[0])
            self.assertEqual(body["delta"][0]["kind"], "new-due")

    def test_invalid_delta_kind_dropped_and_sources_filtered(self):
        with tempfile.TemporaryDirectory() as td:
            root = make_vault(Path(td))
            payload = {
                "proposed": {}, "pool": [],
                "delta": [{"kind": "bogus", "item": "X"},
                          {"kind": "capacity", "item": "Y", "detail": "d", "proposal": "p", "rationale": "r"}],
                "sources": ["vault", "sorcery"],
            }
            g.write_precompute_cache(payload, root, VALID, None)
            out = (root / "00 - META/Cache/tdtb-precompute-cache.md").read_text()
            self.assertIn("diff_base: null", out)
            self.assertIn("sources: [vault]", out)
            body = json.loads(out.split("```json\n")[1].split("\n```")[0])
            self.assertEqual([d["kind"] for d in body["delta"]], ["capacity"])


if __name__ == "__main__":
    unittest.main()
