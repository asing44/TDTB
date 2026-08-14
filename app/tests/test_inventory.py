"""Tests for inventory.py — TDD gate for T3."""
from __future__ import annotations

import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import inventory as inv  # noqa: E402


CACHE_REL_PATH = inv.CACHE_REL_PATH


def _write_cache(vault_root: Path, text: str) -> Path:
    out = vault_root / CACHE_REL_PATH
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    return out


VALID_CACHE = """---
schema_version: 2
valid_date: '2026-05-29'
generated: '2026-05-29T06:18:59.000Z'
inventory_hash: da3b2bedba0b
parent_count: 1
parents:
  - type: project
    name: "Car Purchasing"
    path: "50 - Operations/Projects/Car Purchasing.md"
---
"""


# ---------------------------------------------------------------------------
# read_inventory — cache hit
# ---------------------------------------------------------------------------

class TestCacheHit:
    def test_hit_when_valid_date_matches_effective_date(self, tmp_path):
        _write_cache(tmp_path, VALID_CACHE)
        now = datetime(2026, 5, 29, 9, 0, 0)  # afternoon-ish, same logical day
        result = inv.read_inventory(tmp_path, now=now)
        assert result.ok is True
        assert result.reason is None
        assert result.parent_count == 1
        assert len(result.parents) == 1
        assert result.parents[0]["name"] == "Car Purchasing"
        assert result.parents[0]["path"] == "50 - Operations/Projects/Car Purchasing.md"
        assert result.parents[0]["type"] == "project"
        assert result.inventory_hash == "da3b2bedba0b"
        assert result.valid_date == date(2026, 5, 29)


# ---------------------------------------------------------------------------
# read_inventory — refusal paths
# ---------------------------------------------------------------------------

class TestRefusals:
    def test_miss_on_missing_file(self, tmp_path):
        now = datetime(2026, 5, 29, 9, 0, 0)
        result = inv.read_inventory(tmp_path, now=now)
        assert result.ok is False
        assert result.reason == "missing_file"
        assert result.parents == []

    def test_miss_on_unparseable_file(self, tmp_path):
        _write_cache(tmp_path, "not frontmatter at all, no leading dashes")
        now = datetime(2026, 5, 29, 9, 0, 0)
        result = inv.read_inventory(tmp_path, now=now)
        assert result.ok is False
        assert result.reason == "unparseable"

    def test_miss_on_schema_version_1(self, tmp_path):
        text = VALID_CACHE.replace("schema_version: 2", "schema_version: 1")
        _write_cache(tmp_path, text)
        now = datetime(2026, 5, 29, 9, 0, 0)
        result = inv.read_inventory(tmp_path, now=now)
        assert result.ok is False
        assert result.reason.startswith("schema_version_mismatch")

    def test_miss_on_empty_parents(self, tmp_path):
        text = """---
schema_version: 2
valid_date: '2026-05-29'
generated: '2026-05-29T06:18:59.000Z'
inventory_hash: da3b2bedba0b
parent_count: 0
parents: []
---
"""
        _write_cache(tmp_path, text)
        now = datetime(2026, 5, 29, 9, 0, 0)
        result = inv.read_inventory(tmp_path, now=now)
        assert result.ok is False
        assert result.reason == "parents_absent_or_empty"

    def test_miss_on_absent_parents_key(self, tmp_path):
        text = """---
schema_version: 2
valid_date: '2026-05-29'
generated: '2026-05-29T06:18:59.000Z'
inventory_hash: da3b2bedba0b
parent_count: 0
---
"""
        _write_cache(tmp_path, text)
        now = datetime(2026, 5, 29, 9, 0, 0)
        result = inv.read_inventory(tmp_path, now=now)
        assert result.ok is False
        assert result.reason == "parents_absent_or_empty"

    def test_miss_on_stale_valid_date(self, tmp_path):
        _write_cache(tmp_path, VALID_CACHE)
        now = datetime(2026, 5, 30, 9, 0, 0)  # next day
        result = inv.read_inventory(tmp_path, now=now)
        assert result.ok is False
        assert result.reason.startswith("stale_valid_date")


# ---------------------------------------------------------------------------
# 2am effective-date boundary
# ---------------------------------------------------------------------------

class TestEffectiveDateBoundary:
    def test_0130_hits_yesterdays_cache(self, tmp_path):
        # Cache valid for 2026-05-29; "now" is 01:30 on 2026-05-30 — the
        # 2am logical-day rule means this still counts as 2026-05-29.
        _write_cache(tmp_path, VALID_CACHE)
        now = datetime(2026, 5, 30, 1, 30, 0)
        result = inv.read_inventory(tmp_path, now=now)
        assert result.ok is True
        assert result.valid_date == date(2026, 5, 29)

    def test_0200_no_longer_hits_yesterdays_cache(self, tmp_path):
        # 02:00 flips the logical day back to today — the prior-day cache
        # is now stale.
        _write_cache(tmp_path, VALID_CACHE)
        now = datetime(2026, 5, 30, 2, 0, 0)
        result = inv.read_inventory(tmp_path, now=now)
        assert result.ok is False
        assert result.reason.startswith("stale_valid_date")


# ---------------------------------------------------------------------------
# write_inventory — round-trip + hash preservation
# ---------------------------------------------------------------------------

class TestWriteInventory:
    def test_write_then_read_round_trip(self, tmp_path):
        parents = [
            {
                "type": "project",
                "name": "Car Purchasing",
                "path": "50 - Operations/Projects/Car Purchasing.md",
            },
            {
                "type": "pursuit",
                "name": "Guitar",
                "path": "50 - Operations/Pursuits/Guitar.md",
                "urgency": "2-med",
                "deadline": "2026-06-01",
                "priority_score": 12,
            },
        ]
        now = datetime(2026, 5, 29, 9, 0, 0)
        inv.write_inventory(tmp_path, parents, date(2026, 5, 29), now=now)

        result = inv.read_inventory(tmp_path, now=now)
        assert result.ok is True
        assert result.parent_count == 2
        assert [p["name"] for p in result.parents] == ["Car Purchasing", "Guitar"]
        assert result.parents[1]["urgency"] == "2-med"
        assert result.parents[1]["deadline"] == "2026-06-01"
        assert result.parents[1]["priority_score"] == 12

        cache_path = tmp_path / CACHE_REL_PATH
        raw = cache_path.read_text(encoding="utf-8")
        assert "schema_version: 2" in raw
        assert "```json" not in raw  # never a JSON body block — frontmatter only

    def test_write_never_emits_json_body_block(self, tmp_path):
        now = datetime(2026, 5, 29, 9, 0, 0)
        inv.write_inventory(tmp_path, [], date(2026, 5, 29), now=now)
        cache_path = tmp_path / CACHE_REL_PATH
        raw = cache_path.read_text(encoding="utf-8")
        assert raw.startswith("---\n")
        assert "```" not in raw

    def test_inventory_hash_preserved_from_prior(self, tmp_path):
        _write_cache(tmp_path, VALID_CACHE)
        now = datetime(2026, 5, 29, 9, 0, 0)
        prior = inv.read_inventory(tmp_path, now=now)
        assert prior.ok is True
        assert prior.inventory_hash == "da3b2bedba0b"

        new_parents = [
            {
                "type": "project",
                "name": "Car Purchasing",
                "path": "50 - Operations/Projects/Car Purchasing.md",
            }
        ]
        inv.write_inventory(tmp_path, new_parents, date(2026, 5, 29), now=now, prior=prior)

        result = inv.read_inventory(tmp_path, now=now)
        assert result.ok is True
        assert result.inventory_hash == "da3b2bedba0b"

    def test_inventory_hash_omitted_when_no_prior(self, tmp_path):
        now = datetime(2026, 5, 29, 9, 0, 0)
        inv.write_inventory(tmp_path, [], date(2026, 5, 29), now=now)
        cache_path = tmp_path / CACHE_REL_PATH
        raw = cache_path.read_text(encoding="utf-8")
        assert "inventory_hash" not in raw
