"""Tests for calendar_bridge.normalize_title_map / resolve_titles_to_ids —
the T15 seam regression.

This is the exact seam that cost two live round-trips this session: an
earlier ``commit_run._resolve_calendar_ids`` re-keyed ``resolve_calendar_ids``'s
{logical: id} output by hand; ``resolve_titles_to_ids`` now owns that re-key
so it's covered here instead of only exercised live. Test 3 is the
integration guard — a title-keyed resolved map must actually satisfy
``commit.plan_writes``'s calendar-routing lookup (which keys by display
title, never logical name).
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import calendar_bridge  # noqa: E402
import commit  # noqa: E402
from shadow import CREATE, ManifestEntry, ShadowDiff, ShadowDiffEntry  # noqa: E402

TODAY = date(2026, 7, 12)


# ---------------------------------------------------------------------------
# 1. normalize_title_map
# ---------------------------------------------------------------------------

class TestNormalizeTitleMap:
    def test_list_of_rows_shape(self):
        raw = [
            {"Logical name": "blocks", "BusyCal title": "⬜ Blocks", "Role": "schedulable"},
            {"Logical name": "trinoor", "BusyCal title": "🔷 Trinoor", "Role": "work"},
            {"Logical name": "", "BusyCal title": "ignored — no logical name"},
            {"not a row": True},
        ]
        assert calendar_bridge.normalize_title_map(raw) == {
            "blocks": "⬜ Blocks", "trinoor": "🔷 Trinoor",
        }

    def test_dict_shape_unchanged(self):
        raw = {"blocks": "⬜ Blocks"}
        assert calendar_bridge.normalize_title_map(raw) == raw

    def test_none_or_garbage_returns_empty(self):
        assert calendar_bridge.normalize_title_map(None) == {}
        assert calendar_bridge.normalize_title_map("garbage") == {}
        assert calendar_bridge.normalize_title_map(123) == {}
        assert calendar_bridge.normalize_title_map([]) == {}


class TestNormalizeCapacityClassMap:
    def test_list_rows_normalize_by_exact_title(self):
        raw = [
            {"BusyCal title": "Trinoor", "Class": "WORK"},
            {"BusyCal title": "Session: focus", "Class": "ignored"},
        ]
        assert calendar_bridge.normalize_capacity_class_map(raw) == {
            "Trinoor": "work",
            "Session: focus": "ignored",
        }

    def test_invalid_class_is_dropped_to_allow_fixed_fallback(self):
        assert calendar_bridge.normalize_capacity_class_map(
            [{"BusyCal title": "Personal", "Class": "maybe"}]
        ) == {}


# ---------------------------------------------------------------------------
# 2. resolve_titles_to_ids — TITLE-keyed, not logical-keyed
# ---------------------------------------------------------------------------

class TestResolveTitlesToIds:
    def test_resolves_by_title_not_logical(self):
        calendars = [calendar_bridge.CalendarInfo("⬜ Blocks", "cal-1", True, "iCloud")]
        title_map = {"blocks": "⬜ Blocks"}
        resolved, failures = calendar_bridge.resolve_titles_to_ids(title_map, calendars)
        assert resolved == {"⬜ Blocks": "cal-1"}
        assert failures == []

    def test_defensive_drop_when_title_vanished(self):
        """resolve_calendar_ids resolved a logical name, but that logical is
        no longer present in title_map by the time we re-key — dropped, not
        raised (defensive per the deliverable spec)."""
        calendars = [calendar_bridge.CalendarInfo("⬜ Blocks", "cal-1", True, "iCloud")]
        resolved, failures = calendar_bridge.resolve_titles_to_ids(
            {"blocks": "⬜ Blocks", "trinoor": "🔷 Trinoor"}, calendars
        )
        # "trinoor" has no matching live calendar -> not in resolved at all;
        # only "blocks" resolves.
        assert resolved == {"⬜ Blocks": "cal-1"}
        assert failures == ["trinoor"]


# ---------------------------------------------------------------------------
# 3. INTEGRATION guard — title-keyed output satisfies commit.plan_writes
# ---------------------------------------------------------------------------

def _calendar_create_diff(routing: str) -> ShadowDiff:
    m = ManifestEntry(
        step="D", system="calendar", action="create-event", name="Minting",
        id_or_path="Minting", time="14:00", duration_min=60, routing=routing,
    )
    return ShadowDiff(entries=[ShadowDiffEntry(m, CREATE, {})])


class TestIntegrationSeam:
    def test_title_keyed_resolution_satisfies_plan_writes(self):
        diff = _calendar_create_diff(routing="⬜ Blocks")
        calendars = [calendar_bridge.CalendarInfo("⬜ Blocks", "cal-1", True, "iCloud")]
        resolved, failures = calendar_bridge.resolve_titles_to_ids({"blocks": "⬜ Blocks"}, calendars)
        assert failures == []

        [intent] = commit.plan_writes(diff, resolved, {}, TODAY)
        assert intent.calendar_id == "cal-1"

    def test_unresolved_title_fails_and_plan_refuses(self):
        diff = _calendar_create_diff(routing="⬜ Blocks")
        calendars = [calendar_bridge.CalendarInfo("⬜ Blocks", "cal-1", True, "iCloud")]
        # config names a title that doesn't exist live
        resolved, failures = calendar_bridge.resolve_titles_to_ids(
            {"blocks": "❌ Missing Calendar"}, calendars
        )
        assert failures == ["blocks"]
        assert resolved == {}

        with pytest.raises(commit.CommitPlanError, match="unresolved"):
            commit.plan_writes(diff, resolved, {}, TODAY)
