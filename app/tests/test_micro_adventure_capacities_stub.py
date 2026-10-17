"""Micro-adventure Capacities extension point — config folding + stub.

The optional ``micro_adventure_capacities`` block is a DISABLED-by-default
extension point for eventually pulling micro-adventure ideas from Capacities.
This suite pins the zero-behavior-change contract: config folds and validates
with defaults on any bad type, the stub reports a typed status and performs no
I/O, and nothing in the request path (``_micro_adventure_state``,
``select_today``, ``app/main.py``) references it.
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import app_config  # noqa: E402
import micro_adventure as ma  # noqa: E402

TODAY = date(2026, 7, 15)

CAPACITIES_DEFAULTS = {
    "enabled": False,
    "structure": "",
    "idea_property": "",
    "category_property": "",
    "effort_property": "effort",
    "refresh_cadence_hours": 168,
    "collection": "Personal Adventures",
    "tag": "_TDTB-live",
    "effort_allowed": ["small"],
    "merge": "append",
}

MAIN_PY = Path(app_config.__file__).resolve().parent / "main.py"


def _repo(doc: dict) -> Path:
    path = app_config.repo_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# config folding + defaults
# --------------------------------------------------------------------------- #
class TestCapacitiesConfigFolding:
    def test_defaults_are_pinned(self):
        assert app_config.MICRO_ADVENTURE_CAPACITIES_DEFAULTS == CAPACITIES_DEFAULTS
        assert app_config.MICRO_ADVENTURE_CAPACITIES_KNOWN_KEYS == frozenset(CAPACITIES_DEFAULTS)

    def test_partial_block_folds_with_defaults_filled(self):
        sections = app_config.sections_from_document({
            "micro_adventure_pool": [{"ID": "ma01", "Idea": "Walk", "Active": True}],
            "micro_adventure_rotation": {"exclude_window_days": 10},
            "micro_adventure_capacities": {
                "enabled": True, "structure": "Adventure", "idea_property": "Idea",
            },
        })
        micro = sections["Micro-Adventures"]
        caps = micro["Capacities"]
        assert caps == {
            "enabled": True,
            "structure": "Adventure",
            "idea_property": "Idea",
            "category_property": "",
            "effort_property": "effort",
            "refresh_cadence_hours": 168,
            "collection": "Personal Adventures",
            "tag": "_TDTB-live",
            "effort_allowed": ["small"],
            "merge": "append",
        }
        # Pool and Rotation are untouched by the new key.
        assert micro["Pool"] == [{"ID": "ma01", "Idea": "Walk", "Active": True}]
        assert micro["Rotation"]["rotation.exclude_window_days"] == 10

    def test_wrong_types_degrade_to_defaults(self):
        sections = app_config.sections_from_document({
            "micro_adventure_capacities": {
                "enabled": "true",
                "structure": 5,
                "idea_property": [],
                "category_property": {},
                "effort_property": 7,
                "refresh_cadence_hours": "168",
            },
        })
        assert sections["Micro-Adventures"]["Capacities"] == CAPACITIES_DEFAULTS

    def test_non_object_block_is_ignored(self):
        sections = app_config.sections_from_document({
            "micro_adventure_capacities": "nope",
        })
        assert "Micro-Adventures" not in sections

    def test_absent_key_leaves_section_shape_alone(self):
        sections = app_config.sections_from_document({
            "micro_adventure_pool": [{"ID": "ma01", "Idea": "Walk", "Active": True}],
        })
        assert sections["Micro-Adventures"] == {
            "Pool": [{"ID": "ma01", "Idea": "Walk", "Active": True}],
        }


# --------------------------------------------------------------------------- #
# config warnings
# --------------------------------------------------------------------------- #
class TestCapacitiesConfigWarnings:
    def test_unknown_key_reported(self):
        path = _repo({"version": 1, "micro_adventure_capacities": {"enabled": False, "bogus_key": 1}})
        warnings = app_config.micro_adventure_capacities_config_warnings(path)
        assert len(warnings) == 1
        assert "bogus_key" in warnings[0]
        assert "enabled" in warnings[0]  # known keys are listed for the operator

    def test_wrong_types_warn(self):
        path = _repo({
            "version": 1,
            "micro_adventure_capacities": {
                "enabled": "true", "structure": 5, "refresh_cadence_hours": "168",
            },
        })
        joined = " ".join(app_config.micro_adventure_capacities_config_warnings(path))
        assert "enabled" in joined
        assert "structure" in joined
        assert "refresh_cadence_hours" in joined

    def test_enabled_must_be_a_real_bool(self):
        clean = _repo({"version": 1, "micro_adventure_capacities": {"enabled": True}})
        assert app_config.micro_adventure_capacities_config_warnings(clean) == []
        for bad in (1, 0, "false", None):
            path = _repo({"version": 1, "micro_adventure_capacities": {"enabled": bad}})
            assert app_config.micro_adventure_capacities_config_warnings(path)

    def test_non_object_block_warns(self):
        path = _repo({"version": 1, "micro_adventure_capacities": "nope"})
        warnings = app_config.micro_adventure_capacities_config_warnings(path)
        assert len(warnings) == 1
        assert "micro_adventure_capacities" in warnings[0]

    def test_absent_and_clean_blocks_make_no_warning(self):
        assert app_config.micro_adventure_capacities_config_warnings(_repo({"version": 1})) == []
        clean = _repo({"version": 1, "micro_adventure_capacities": dict(CAPACITIES_DEFAULTS)})
        assert app_config.micro_adventure_capacities_config_warnings(clean) == []


# --------------------------------------------------------------------------- #
# capacities_pool stub statuses
# --------------------------------------------------------------------------- #
class TestCapacitiesPoolStub:
    def test_disabled_by_default(self):
        for section in (None, {}, {"Capacities": {"enabled": False}}):
            result = ma.capacities_pool(section)
            assert result.status == ma.CAPACITIES_STATUS_DISABLED
            assert result.ideas == []

    def test_not_configured_without_collection_or_tag(self):
        for caps in (
            {"enabled": True, "collection": "", "tag": ""},
            {"enabled": True, "collection": "   ", "tag": "  "},
        ):
            result = ma.capacities_pool({"Capacities": caps})
            assert result.status == ma.CAPACITIES_STATUS_NOT_CONFIGURED
            assert result.ideas == []

    def test_collection_or_tag_alone_is_configured_but_unavailable_without_block(self):
        for caps in (
            {"enabled": True, "collection": "Personal Adventures"},
            {"enabled": True, "tag": "_TDTB-live"},
        ):
            result = ma.capacities_pool({"Capacities": caps})
            assert result.status == ma.CAPACITIES_STATUS_UNAVAILABLE
            assert result.ideas == []

    def test_ok_when_fully_configured_with_a_published_block(self):
        result = ma.capacities_pool(
            {
                "Capacities": {
                    "enabled": True,
                    "structure": "Adventure",
                    "idea_property": "Idea",
                    "collection": "Personal Adventures",
                    "tag": "_TDTB-live",
                },
            },
            {"status": "ok", "read_at": "2026-07-15T06:00:00Z",
             "items": [{"id": "obj-1", "idea": "Kayaking"}]},
            TODAY,
        )
        assert result.status == ma.CAPACITIES_STATUS_OK
        assert [p.id for p in result.ideas] == ["cap-obj-1"]
        assert isinstance(result.message, str) and result.message

    def test_result_type_is_stable(self):
        result = ma.capacities_pool({})
        assert result.ideas == []
        assert result.status in {
            ma.CAPACITIES_STATUS_DISABLED,
            ma.CAPACITIES_STATUS_NOT_CONFIGURED,
            ma.CAPACITIES_STATUS_OK,
            ma.CAPACITIES_STATUS_STALE,
            ma.CAPACITIES_STATUS_UNAVAILABLE,
        }

    def test_docstring_records_stable_id_and_off_request_path_rule(self):
        doc = ma.capacities_pool.__doc__ or ""
        assert "cap-" in doc
        assert "request path" in doc


# --------------------------------------------------------------------------- #
# unchanged behavior
# --------------------------------------------------------------------------- #
class TestExistingBehaviorUnchanged:
    def test_fallback_pool_ids_pinned(self):
        assert [p.id for p in ma.FALLBACK_POOL] == [
            "ma01", "ma02", "ma03", "ma04", "ma05", "ma06",
            "ma07", "ma08", "ma11", "ma12", "ma13", "ma14",
        ]

    def test_parse_select_and_window_unchanged_by_capacities(self):
        base = {
            "_body": "prose",
            "Pool": [{"ID": "z1", "Idea": "A", "Active": True},
                     {"ID": "z2", "Idea": "B", "Active": True}],
            "Rotation": {"rotation.exclude_window_days": 5},
        }
        with_caps = {
            **base,
            "Capacities": {"enabled": True, "structure": "S", "idea_property": "I"},
        }
        assert ma.parse_pool(base) == ma.parse_pool(with_caps)
        assert ma.exclude_window_days(base) == ma.exclude_window_days(with_caps) == 5
        sel_base = ma.select_today(ma.parse_pool(base), [], today=TODAY, window_days=5)
        sel_caps = ma.select_today(ma.parse_pool(with_caps), [], today=TODAY, window_days=5)
        assert sel_base == sel_caps

    def test_state_payload_unchanged_by_capacities(self):
        import main as main_mod

        base = {"Micro-Adventures": {
            "_body": "prose",
            "Pool": [{"ID": "ma01", "Idea": "Walk", "Active": True}],
            "Rotation": {"rotation.exclude_window_days": 10},
        }}
        with_caps = {"Micro-Adventures": {
            **base["Micro-Adventures"],
            "Capacities": {"enabled": True, "structure": "S", "idea_property": "I"},
        }}
        state_base, _ = main_mod._micro_adventure_state(base, TODAY, None)
        state_caps, _ = main_mod._micro_adventure_state(with_caps, TODAY, None)
        assert state_base == state_caps
        assert set(state_base) == {"auto_pick", "live_pool", "streak", "pending_confirm"}

    def test_stub_is_not_referenced_from_main(self):
        text = MAIN_PY.read_text(encoding="utf-8")
        assert "capacities_pool" not in text
        assert "CAPACITIES_STATUS_" not in text
