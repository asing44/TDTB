"""A1 — the "quick check-in" untimed-lane classifier.

Owner decision 2026-11-09 (hand corrections): a date-only Todoist row that is
either very short (<= 10 minutes) or named like a check-in ("fu ...", "...
texts", "check ...", "follow up") rides the existing all-day state
(``blocks == 0``) instead of a timed slot.  The rule is a table here: one case
per documented condition so a change to any single condition fails loudly.
"""
from __future__ import annotations

import untimed_lane


def _item(**overrides):
    """A date-only native Todoist row (10 minutes, no due time)."""
    row = {
        "name": "Meegy couch inspo texts",
        "source": "todoist",
        "todoist_id": "t-1",
        "path": "todoist://t-1",
        "duration": 10,
    }
    row.update(overrides)
    return row


def _lane(**overrides):
    lane = {"enabled": True, "max_blocks": 1 / 3,
            "tokens": list(untimed_lane.DEFAULT_TOKENS)}
    lane.update(overrides)
    return {"Defaults": {"untimed_lane": lane}}


def test_classify_returns_the_documented_shape():
    verdict = untimed_lane.classify(_item())
    assert set(verdict) == {"untimed", "reason"}
    assert verdict["untimed"] is True
    assert isinstance(verdict["reason"], str) and verdict["reason"]


def test_the_four_owner_examples_route_as_specified():
    # 2026-11-09 hand corrections, verbatim from the queue evidence.
    cases = [
        ("Meegy couch inspo texts", 10, True),
        ("Tirzepatide check if feeling shit", 15, True),
        ("fu Indiria", 30, True),
        ("palce down more sticky traps", 15, False),
    ]
    for name, minutes, expected in cases:
        verdict = untimed_lane.classify(_item(name=name, duration=minutes))
        assert verdict["untimed"] is expected, (name, minutes, verdict)


def test_short_duration_is_untimed_without_a_token_match():
    verdict = untimed_lane.classify(_item(name="water the plants", duration=10))
    assert verdict["untimed"] is True
    assert "10 min" in verdict["reason"]


def test_quantised_digest_blocks_do_not_hide_the_native_minutes():
    # The real digest shape for a 10-minute Todoist task: resolve_assigned_blocks
    # rounds UP to whole blocks, so the row arrives as duration=10, blocks=1.
    # The exact minutes must win, or the 10-minute rule could never fire.
    verdict = untimed_lane.classify(
        _item(name="water the plants", duration=10, blocks=1)
    )
    assert verdict["untimed"] is True
    assert "10 min" in verdict["reason"]


def test_blocks_provenance_is_read_when_duration_is_absent():
    assert untimed_lane.classify(
        _item(name="water the plants", duration=None, native_blocks=0.3333)
    )["untimed"] is True
    # A 30-minute row with no token stays timed.
    assert untimed_lane.classify(
        _item(name="water the plants", duration=None, native_blocks=1)
    )["untimed"] is False


def test_native_blocks_provenance_outranks_the_effective_blocks():
    # The effective all-day override never turns a 15-minute row untimed.
    verdict = untimed_lane.classify(
        _item(name="palce down more sticky traps", duration=None,
              native_blocks=0.5, blocks=0)
    )
    assert verdict["untimed"] is False


def test_due_time_means_specified_and_stays_timed():
    verdict = untimed_lane.classify(
        _item(name="fu Indiria", duration=10, scheduled_start="09:00")
    )
    assert verdict["untimed"] is False
    assert "due time" in verdict["reason"]


def test_explicit_zone_stays_timed():
    for zone in ("evening", "work_hours", "morning"):
        verdict = untimed_lane.classify(_item(name="fu Indiria", zone=zone))
        assert verdict["untimed"] is False, zone
        assert zone in verdict["reason"]
    # "any"/none are not an explicit zone.
    assert untimed_lane.classify(_item(name="fu Indiria", zone="any"))["untimed"] is True
    assert untimed_lane.classify(_item(name="fu Indiria", zone=""))["untimed"] is True


def test_latest_start_and_time_adjustment_stay_timed():
    assert untimed_lane.classify(
        _item(name="fu Indiria", latest_start="17:00")
    )["untimed"] is False
    assert untimed_lane.classify(
        _item(name="fu Indiria", allow_time_adjustment=True)
    )["untimed"] is False


def test_non_todoist_and_identityless_rows_are_never_untimed():
    assert untimed_lane.classify(
        _item(source="capacities", todoist_id="", path="capacities://s/1")
    )["untimed"] is False
    assert untimed_lane.classify(
        {"name": "fu Indiria", "duration": 10}
    )["untimed"] is False


def test_config_can_override_the_threshold():
    # 15 minutes is over the code default (0.333 blocks) and under 0.5 blocks.
    assert untimed_lane.classify(
        _item(name="palce down more sticky traps", duration=15)
    )["untimed"] is False
    assert untimed_lane.classify(
        _item(name="palce down more sticky traps", duration=15),
        _lane(max_blocks=0.5),
    )["untimed"] is True
    # A malformed threshold falls back to the code default.
    assert untimed_lane.classify(
        _item(name="palce down more sticky traps", duration=15),
        _lane(max_blocks="lots"),
    )["untimed"] is False


def test_config_can_override_the_tokens():
    renamed = _item(name="ping Indiria", duration=30)
    assert untimed_lane.classify(renamed)["untimed"] is False
    assert untimed_lane.classify(renamed, _lane(tokens=[r"\bping\b"]))["untimed"] is True
    # The default token set is replaced, not extended.
    assert untimed_lane.classify(
        _item(name="fu Indiria", duration=30), _lane(tokens=[r"\bping\b"])
    )["untimed"] is False
    # A malformed token never raises and never matches.
    assert untimed_lane.classify(
        _item(name="fu Indiria", duration=30), _lane(tokens=["[unclosed"])
    )["untimed"] is False


def test_disabled_flag_turns_the_lane_off():
    for row in (_item(name="fu Indiria", duration=10),
                _item(name="palce down more sticky traps", duration=10)):
        verdict = untimed_lane.classify(row, _lane(enabled=False))
        assert verdict["untimed"] is False
        assert "disabled" in verdict["reason"]


def test_per_item_config_override_wins_both_ways():
    forced_timed = _lane(overrides={"fu Indiria": False})
    assert untimed_lane.classify(
        _item(name="fu Indiria", duration=30), forced_timed
    )["untimed"] is False
    forced_untimed = _lane(overrides={"palce down more sticky traps": True})
    assert untimed_lane.classify(
        _item(name="palce down more sticky traps", duration=30), forced_untimed
    )["untimed"] is True


def test_config_may_be_absent_or_malformed():
    for config in (None, {}, {"Defaults": None}, {"Defaults": {"untimed_lane": "on"}},
                   {"untimed_lane": {"enabled": True}}):
        verdict = untimed_lane.classify(_item(name="fu Indiria", duration=30), config)
        assert verdict["untimed"] is True, config


def test_flat_dotted_defaults_keys_are_read():
    flat = {"Defaults": {"untimed_lane.enabled": False,
                         "untimed_lane.tokens": [r"\bping\b"]}}
    assert untimed_lane.classify(_item(name="fu Indiria", duration=10), flat)["untimed"] is False
