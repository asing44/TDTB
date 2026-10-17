"""Micro-adventure Capacities pool core — pure normalizer, status, merge.

Slice S1 of the micro-adventure Capacities pool: everything here is pure (no
I/O, no clock, no MCP). ``app/main.py`` is still NOT wired to it (that is S3),
so the stub suite's non-reference assertion stays valid.

Pinned contract:
  * block shape ``{status, read_at, items: [{id, idea, ...}], warnings}``;
  * stable ids ``cap-<object id>`` so history/streak keyed by id survive;
  * bounded (200 items, 200 chars), trimmed, deduped by id and normalized text;
  * statuses ``disabled | not-configured | ok | stale | unavailable``;
  * merge appends after the config pool with Capacities capped at half the
    live pool; empty/failed results yield exactly the config pool.
"""
from __future__ import annotations

import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import micro_adventure as ma  # noqa: E402

TODAY = date(2026, 7, 15)
NOW = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)


def _section(**caps):
    base = {
        "enabled": True,
        "collection": "Personal Adventures",
        "tag": "_TDTB-live",
        "refresh_cadence_hours": 168,
    }
    base.update(caps)
    return {"Capacities": base}


def _block(items, *, status="ok", read_at="2026-10-17T09:00:00+00:00", warnings=None):
    return {
        "status": status,
        "read_at": read_at,
        "items": items,
        "warnings": list(warnings or []),
    }


CONFIG_POOL = [
    ma.PoolIdea("ma01", "Walk the greenway trail", "nature", "low", True),
    ma.PoolIdea("ma02", "Call a friend", "social", "low", True),
]


# --------------------------------------------------------------------------- #
# normalize_capacities_ideas
# --------------------------------------------------------------------------- #
class TestNormalizeCapacitiesIdeas:
    def test_stable_prefixed_ids_and_trimmed_text(self):
        result = ma.normalize_capacities_ideas(_block([
            {"id": "obj-1", "idea": "  Kayak the river  "},
            {"id": "obj-2", "idea": "Fly fishing"},
        ]))
        assert [(p.id, p.idea) for p in result.ideas] == [
            ("cap-obj-1", "Kayak the river"),
            ("cap-obj-2", "Fly fishing"),
        ]
        assert result.warnings == ()

    def test_id_prefix_is_not_doubled(self):
        result = ma.normalize_capacities_ideas(_block([
            {"id": "cap-obj-1", "idea": "Kayak"},
        ]))
        assert [p.id for p in result.ideas] == ["cap-obj-1"]

    def test_entries_are_active_pool_rows(self):
        result = ma.normalize_capacities_ideas(_block([{"id": "o1", "idea": "Coffee crawl"}]))
        assert result.ideas[0] == ma.PoolIdea(
            "cap-o1", "Coffee crawl", "", "", True
        )

    def test_empty_and_malformed_items_dropped_with_warnings(self):
        block = _block([
            {"id": "o1", "idea": "   "},
            {"id": "", "idea": "No id"},
            {"idea": "No id either"},
            "not a dict",
            {"id": "o2", "idea": "Gardens Sake"},
        ])
        result = ma.normalize_capacities_ideas(block)
        assert [p.id for p in result.ideas] == ["cap-o2"]
        assert len(result.warnings) == 4

    def test_duplicate_id_dropped_with_warning(self):
        result = ma.normalize_capacities_ideas(_block([
            {"id": "o1", "idea": "Kayak"},
            {"id": "o1", "idea": "Kayak again"},
        ]))
        assert [p.idea for p in result.ideas] == ["Kayak"]
        assert len(result.warnings) == 1

    def test_duplicate_text_dropped_by_normalized_form(self):
        result = ma.normalize_capacities_ideas(_block([
            {"id": "o1", "idea": "Coffee  Crawl"},
            {"id": "o2", "idea": " coffee crawl "},
        ]))
        assert [p.id for p in result.ideas] == ["cap-o1"]
        assert len(result.warnings) == 1

    def test_text_is_bounded_to_200_chars(self):
        result = ma.normalize_capacities_ideas(_block([
            {"id": "o1", "idea": "x" * 300},
        ]))
        assert len(result.ideas[0].idea) == ma.CAPACITIES_MAX_IDEA_CHARS == 200
        assert result.warnings

    def test_items_are_bounded_to_200(self):
        items = [{"id": f"o{i}", "idea": f"Idea {i}"} for i in range(250)]
        result = ma.normalize_capacities_ideas(_block(items))
        assert len(result.ideas) == ma.CAPACITIES_MAX_IDEAS == 200
        assert result.ideas[-1].id == "cap-o199"
        assert len(result.warnings) == 50

    def test_garbage_never_raises(self):
        for block in (
            None, 42, "nope", [], {}, {"items": None}, {"items": "nope"},
            {"items": [None, 1, {"id": None}, {"id": "o1", "idea": 7}]},
        ):
            result = ma.normalize_capacities_ideas(block)
            assert isinstance(result.ideas, tuple)
            assert all(isinstance(p, ma.PoolIdea) for p in result.ideas)

    def test_bare_item_list_is_accepted(self):
        result = ma.normalize_capacities_ideas([{"id": "o1", "idea": "Kayak"}])
        assert [p.id for p in result.ideas] == ["cap-o1"]


# --------------------------------------------------------------------------- #
# capacities_pool statuses
# --------------------------------------------------------------------------- #
class TestCapacitiesPoolStatuses:
    def test_disabled_when_enabled_is_not_true(self):
        for section in (
            None, {}, {"Capacities": {"enabled": False}}, {"Capacities": {"enabled": "true"}},
        ):
            result = ma.capacities_pool(section, _block([{"id": "o1", "idea": "Kayak"}]), NOW)
            assert result.status == ma.CAPACITIES_STATUS_DISABLED == "disabled"
            assert result.ideas == []

    def test_not_configured_when_collection_and_tag_both_unset(self):
        for caps in (
            {"enabled": True},
            {"enabled": True, "collection": "", "tag": ""},
            {"enabled": True, "collection": "   ", "tag": "  "},
        ):
            result = ma.capacities_pool(caps and {"Capacities": caps}, _block([]), NOW)
            assert result.status == ma.CAPACITIES_STATUS_NOT_CONFIGURED == "not-configured"
            assert result.ideas == []

    def test_collection_alone_is_configured(self):
        result = ma.capacities_pool(
            _section(tag=""), _block([{"id": "o1", "idea": "Kayak"}]), NOW
        )
        assert result.status == ma.CAPACITIES_STATUS_OK == "ok"
        assert [p.id for p in result.ideas] == ["cap-o1"]

    def test_tag_alone_is_configured(self):
        result = ma.capacities_pool(
            _section(collection=""), _block([{"id": "o1", "idea": "Kayak"}]), NOW
        )
        assert result.status == ma.CAPACITIES_STATUS_OK

    def test_unavailable_when_block_missing_or_malformed(self):
        for block in (None, 42, "nope", [], {"items": "nope"}, {"status": "failed"}):
            result = ma.capacities_pool(_section(), block, NOW)
            assert result.status == ma.CAPACITIES_STATUS_UNAVAILABLE == "unavailable"
            assert result.ideas == []

    def test_ok_with_fresh_block(self):
        result = ma.capacities_pool(
            _section(), _block([{"id": "o1", "idea": "Kayak"}]), NOW
        )
        assert result.status == ma.CAPACITIES_STATUS_OK
        assert [p.id for p in result.ideas] == ["cap-o1"]
        assert isinstance(result.message, str) and result.message

    def test_stale_when_read_at_outside_cadence(self):
        old = (NOW - timedelta(hours=169)).isoformat()
        result = ma.capacities_pool(
            _section(), _block([{"id": "o1", "idea": "Kayak"}], read_at=old), NOW
        )
        assert result.status == ma.CAPACITIES_STATUS_STALE == "stale"
        assert [p.id for p in result.ideas] == ["cap-o1"]
        assert result.message

    def test_fresh_within_cadence_including_naive_and_z_timestamps(self):
        inside = (NOW - timedelta(hours=167)).isoformat()
        assert ma.capacities_pool(
            _section(), _block([{"id": "o1", "idea": "K"}], read_at=inside), NOW
        ).status == ma.CAPACITIES_STATUS_OK
        z = (NOW - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        assert ma.capacities_pool(
            _section(), _block([{"id": "o1", "idea": "K"}], read_at=z), NOW
        ).status == ma.CAPACITIES_STATUS_OK
        naive = (NOW - timedelta(hours=1)).replace(tzinfo=None).isoformat()
        assert ma.capacities_pool(
            _section(), _block([{"id": "o1", "idea": "K"}], read_at=naive), NOW
        ).status == ma.CAPACITIES_STATUS_OK

    def test_custom_cadence_is_honored(self):
        two_hours_old = (NOW - timedelta(hours=2)).isoformat()
        result = ma.capacities_pool(
            _section(refresh_cadence_hours=1),
            _block([{"id": "o1", "idea": "K"}], read_at=two_hours_old),
            NOW,
        )
        assert result.status == ma.CAPACITIES_STATUS_STALE

    def test_missing_read_at_is_not_stale(self):
        result = ma.capacities_pool(_section(), {"items": [{"id": "o1", "idea": "K"}]}, NOW)
        assert result.status == ma.CAPACITIES_STATUS_OK

    def test_unparseable_read_at_is_not_stale_but_warns(self):
        result = ma.capacities_pool(
            _section(), _block([{"id": "o1", "idea": "K"}], read_at="not-a-time"), NOW
        )
        assert result.status == ma.CAPACITIES_STATUS_OK
        assert result.warnings

    def test_never_raises_on_garbage(self):
        for section in (None, 1, "x", [], {}, {"Capacities": "nope"}, {"Capacities": None}):
            for block in (None, 1, "x", [], {}, {"items": [None]}):
                for now in (None, 1, "x", NOW):
                    result = ma.capacities_pool(section, block, now)
                    assert result.status in {
                        ma.CAPACITIES_STATUS_DISABLED,
                        ma.CAPACITIES_STATUS_NOT_CONFIGURED,
                        ma.CAPACITIES_STATUS_OK,
                        ma.CAPACITIES_STATUS_STALE,
                        ma.CAPACITIES_STATUS_UNAVAILABLE,
                    }

    def test_result_carries_normalizer_warnings(self):
        result = ma.capacities_pool(
            _section(),
            _block([{"id": "o1", "idea": "Kayak"}, {"id": "o1", "idea": "Dup"}]),
            NOW,
        )
        assert result.status == ma.CAPACITIES_STATUS_OK
        assert result.warnings


# --------------------------------------------------------------------------- #
# merge_pool + live-pool cap
# --------------------------------------------------------------------------- #
class TestMergePool:
    def test_config_pool_first_then_capacities(self):
        caps = [ma.PoolIdea("cap-o1", "Kayak", "", "", True)]
        merged = ma.merge_pool(CONFIG_POOL, caps)
        assert [p.id for p in merged] == ["ma01", "ma02", "cap-o1"]

    def test_empty_capacities_yields_exactly_the_config_pool(self):
        assert ma.merge_pool(CONFIG_POOL, []) == CONFIG_POOL
        assert ma.merge_pool(CONFIG_POOL, ()) == CONFIG_POOL
        assert ma.merge_pool(CONFIG_POOL) == CONFIG_POOL

    def test_empty_both_is_empty(self):
        assert ma.merge_pool([], []) == []

    def test_dedupe_by_id_and_normalized_text(self):
        caps = [
            ma.PoolIdea("ma01", "Duplicated id", "", "", True),
            ma.PoolIdea("cap-o1", "  walk the greenway trail ", "", "", True),
            ma.PoolIdea("cap-o2", "Kayak", "", "", True),
            ma.PoolIdea("cap-o2", "Kayak again", "", "", True),
        ]
        merged = ma.merge_pool(CONFIG_POOL, caps)
        assert [p.id for p in merged] == ["ma01", "ma02", "cap-o2"]

    def test_capacities_capped_at_half_the_live_pool(self):
        caps = [ma.PoolIdea(f"cap-o{i}", f"Idea {i}", "", "", True) for i in range(10)]
        merged = ma.merge_pool(CONFIG_POOL, caps)
        assert len(merged) == 2 + ma.capacities_live_limit(8) == 2 + 4
        assert ma.capacities_live_limit() == 4

    def test_limit_helper_is_tolerant(self):
        assert ma.capacities_live_limit(0) == 0
        assert ma.capacities_live_limit(1) == 0
        assert ma.capacities_live_limit(7) == 3
        for bad in (None, "x", True, -4):
            assert ma.capacities_live_limit(bad) >= 0

    def test_limit_capacities_ideas_helper(self):
        caps = [ma.PoolIdea(f"cap-o{i}", f"Idea {i}", "", "", True) for i in range(10)]
        assert len(ma.limit_capacities_ideas(caps, 8)) == 4
        assert ma.limit_capacities_ideas([], 8) == []
        assert ma.limit_capacities_ideas("garbage", 8) == []

    def test_garbage_capacities_do_not_raise(self):
        for caps in (None, "x", 3, [None, "str", {"id": "o1"}]):
            assert ma.merge_pool(CONFIG_POOL, caps) == CONFIG_POOL

    def test_accepts_a_capacities_pool_result(self):
        good = ma.CapacitiesPoolResult(
            [ma.PoolIdea("cap-o1", "Kayak", "", "", True)], ma.CAPACITIES_STATUS_OK, ""
        )
        assert [p.id for p in ma.merge_pool(CONFIG_POOL, good)] == ["ma01", "ma02", "cap-o1"]
        bad = ma.CapacitiesPoolResult([], ma.CAPACITIES_STATUS_UNAVAILABLE, "")
        assert ma.merge_pool(CONFIG_POOL, bad) == CONFIG_POOL
        stale = ma.CapacitiesPoolResult(
            [ma.PoolIdea("cap-o1", "Kayak", "", "", True)], ma.CAPACITIES_STATUS_STALE, ""
        )
        assert ma.merge_pool(CONFIG_POOL, stale) == CONFIG_POOL

    def test_input_pool_is_not_mutated(self):
        caps = [ma.PoolIdea("cap-o1", "Kayak", "", "", True)]
        before = list(CONFIG_POOL)
        ma.merge_pool(CONFIG_POOL, caps)
        assert CONFIG_POOL == before


# --------------------------------------------------------------------------- #
# enabled:false keeps today's behavior byte-for-byte
# --------------------------------------------------------------------------- #
class TestDisabledKeepsTodaysPool:
    def test_disabled_capacities_do_not_change_parse_or_select(self):
        document_section = {
            "_body": "prose",
            "Pool": [
                {"ID": "z1", "Idea": "A", "Active": True},
                {"ID": "z2", "Idea": "B", "Active": True},
            ],
            "Rotation": {"rotation.exclude_window_days": 5},
        }
        with_caps = {
            **document_section,
            "Capacities": {
                "enabled": False,
                "collection": "Personal Adventures",
                "tag": "_TDTB-live",
            },
        }
        assert ma.parse_pool(document_section) == ma.parse_pool(with_caps)
        assert ma.exclude_window_days(document_section) == 5
        base = ma.select_today(ma.parse_pool(document_section), [], today=TODAY, window_days=5)
        caps = ma.select_today(ma.parse_pool(with_caps), [], today=TODAY, window_days=5)
        assert base == caps
        assert ma.capacities_pool(with_caps, _block([{"id": "o1", "idea": "K"}]), NOW).ideas == []

    def test_fallback_pool_still_used_for_empty_section(self):
        assert ma.parse_pool({}) == list(ma.FALLBACK_POOL)
        assert ma.parse_pool({"Capacities": {"enabled": True}}) == list(ma.FALLBACK_POOL)
