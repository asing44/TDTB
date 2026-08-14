"""Metadata preserved for semantic sequencing hints."""
from __future__ import annotations

from datetime import date
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent / "gather"))
import tdtb_gather as gather


def test_run_data_preserves_parent_and_tags():
    note = {
        "name": "Career Ops Pipeline",
        "path": "P/Career.md",
        "fm": {
            "type": ["project"],
            "relates_to": "[[Professional Development]]",
            "tags": ["career", "systems"],
            "status": "open",
            "assigned": True,
        },
    }
    row = gather.build_run_data([], [note], date(2026, 7, 31))["assigned_items"][0]
    assert row["relates_to"] == "[[Professional Development]]"
    assert row["tags"] == ["career", "systems"]
