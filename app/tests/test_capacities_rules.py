"""U3a — typed per-type Capacities rules, three-state evaluation, and the
active/draft store.

Test-first: this file was written before ``app/capacities_rules.py`` existed so
the red state is an import failure, then implemented to green.

Everything here is local and fake — no provider, credential, vault, network, or
real ``$HOME`` read. The autouse ``_isolated_app_home`` fixture (conftest) puts
``TDTB_HOME`` in a per-test tmp dir, so the store under test never touches the
operator's machine state.
"""
from __future__ import annotations

import json
import os
import socket
from pathlib import Path

import pytest

import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

import app_config  # noqa: E402
import capacities_rules as cr  # noqa: E402


SPACE = "space-1"
SPACE_OTHER = "space-2"
TODAY = "2026-10-09"

#: The discovered type shape: property id -> kind.
SCHEMA = {
    "status": "label",
    "title": "title",
    "tags": "entity",
    "points": "number",
    "due": "date",
    "billable": "boolean",
    "notes": "richText",
}

#: The record the predicates read. ``tags`` and ``billable`` are deliberately
#: absent so the three-state cases are available.
RECORD = {
    "status": "Active",
    "title": "Write the thing",
    "points": 5,
    "due": "2026-10-10",
}

# Leaves that always yield each of the three states against ``RECORD``.
LEAF_MATCH = {"prop": "status", "op": "eq", "values": ["Active"]}
LEAF_NO = {"prop": "status", "op": "eq", "values": ["Done"]}
LEAF_UNKNOWN = {"prop": "tags", "op": "eq", "values": ["archived"]}

VALID_RULE = {
    "all": [
        {"prop": "status", "op": "eq", "values": ["Active"]},
        {"not": {"prop": "tags", "op": "in", "values": ["archived"]}},
    ]
}
#: A rule referencing a property the discovery no longer carries (AE21).
REMOVED_PROPERTY_RULE = {"prop": "ghost", "op": "eq", "values": ["x"]}
#: An operator that is incompatible with the named property's kind.
INCOMPATIBLE_RULE = {"prop": "title", "op": "lt", "values": [3]}


def _eval(rule, record=None):
    return cr.evaluate(rule, RECORD if record is None else record, logical_day=TODAY)


# ---------------------------------------------------------------------------
# Three-state evaluation and Kleene combination
# ---------------------------------------------------------------------------

def test_leaf_states_are_the_three_way_signal():
    assert _eval(LEAF_MATCH) is cr.MATCH
    assert _eval(LEAF_NO) is cr.NO_MATCH
    assert _eval(LEAF_UNKNOWN) is cr.UNKNOWN
    assert not isinstance(_eval(LEAF_MATCH), bool)


@pytest.mark.parametrize(
    "children,expected",
    [
        ([LEAF_MATCH, LEAF_MATCH], cr.MATCH),
        ([LEAF_MATCH, LEAF_NO], cr.NO_MATCH),
        ([LEAF_NO, LEAF_UNKNOWN], cr.NO_MATCH),
        ([LEAF_MATCH, LEAF_UNKNOWN], cr.UNKNOWN),
        ([LEAF_UNKNOWN, LEAF_UNKNOWN], cr.UNKNOWN),
        ([], cr.MATCH),
    ],
)
def test_all_kleene_truth_table(children, expected):
    assert _eval({"all": children}) is expected


@pytest.mark.parametrize(
    "children,expected",
    [
        ([LEAF_MATCH, LEAF_NO], cr.MATCH),
        ([LEAF_MATCH, LEAF_MATCH], cr.MATCH),
        ([LEAF_NO, LEAF_NO], cr.NO_MATCH),
        ([LEAF_NO, LEAF_UNKNOWN], cr.UNKNOWN),
        ([LEAF_MATCH, LEAF_UNKNOWN], cr.MATCH),
        ([LEAF_UNKNOWN, LEAF_UNKNOWN], cr.UNKNOWN),
        ([], cr.NO_MATCH),
    ],
)
def test_any_kleene_truth_table(children, expected):
    assert _eval({"any": children}) is expected


@pytest.mark.parametrize(
    "child,expected",
    [
        (LEAF_MATCH, cr.NO_MATCH),
        (LEAF_NO, cr.MATCH),
        (LEAF_UNKNOWN, cr.UNKNOWN),
    ],
)
def test_not_swaps_match_and_no_match_and_preserves_unknown(child, expected):
    assert _eval({"not": child}) is expected


def test_nested_kleene_combination():
    rule = {"all": [{"any": [LEAF_MATCH, LEAF_UNKNOWN]}, {"not": LEAF_NO}]}
    assert _eval(rule) is cr.MATCH

    rule = {"any": [{"all": [LEAF_MATCH, LEAF_UNKNOWN]}, LEAF_NO]}
    assert _eval(rule) is cr.UNKNOWN

    rule = {"not": {"all": [LEAF_NO, LEAF_UNKNOWN]}}
    assert _eval(rule) is cr.MATCH


@pytest.mark.parametrize(
    "op",
    ["eq", "in", "lt", "gt", "before", "after"],
)
def test_value_comparison_on_uncarried_property_is_unknown(op):
    value = 3 if op in ("lt", "gt") else "2026-01-01"
    rule = {"prop": "tags", "op": op, "values": [value]}
    assert _eval(rule) is cr.UNKNOWN


def test_presence_predicates_stay_decidable_when_value_is_absent():
    assert _eval({"prop": "tags", "op": "exists"}) is cr.NO_MATCH
    assert _eval({"prop": "tags", "op": "truthy"}) is cr.NO_MATCH
    assert _eval({"prop": "status", "op": "exists"}) is cr.MATCH
    assert _eval({"prop": "status", "op": "truthy"}) is cr.MATCH


def test_presence_treats_a_carried_null_as_present_and_falsy():
    record = {"status": None, "points": 0}
    assert _eval({"prop": "status", "op": "exists"}, record) is cr.MATCH
    assert _eval({"prop": "status", "op": "truthy"}, record) is cr.NO_MATCH
    assert _eval({"prop": "points", "op": "truthy"}, record) is cr.NO_MATCH


def test_present_but_incomparable_value_is_no_match_not_unknown():
    # The property IS carried, so the comparison is decided (and fails); only
    # an absent property is UNKNOWN.
    assert _eval({"prop": "title", "op": "lt", "values": [3]}) is cr.NO_MATCH
    assert _eval({"prop": "title", "op": "before", "values": ["2026-01-01"]}) is cr.NO_MATCH


def test_ordering_and_date_operators_compare_carried_values():
    assert _eval({"prop": "points", "op": "gt", "values": [3]}) is cr.MATCH
    assert _eval({"prop": "points", "op": "lt", "values": [3]}) is cr.NO_MATCH
    assert _eval({"prop": "due", "op": "after", "values": ["2026-10-09"]}) is cr.MATCH
    assert _eval({"prop": "due", "op": "before", "values": ["2026-10-09"]}) is cr.NO_MATCH


def test_today_token_resolves_against_the_logical_day():
    assert _eval({"prop": "due", "op": "after", "values": ["$today"]}) is cr.MATCH
    assert _eval(
        {"prop": "due", "op": "before", "values": ["$today"]}
    ) is cr.NO_MATCH


def test_in_matches_a_list_valued_property():
    record = {"tags": ["archived", "other"]}
    assert _eval({"prop": "tags", "op": "in", "values": ["archived"]}, record) is cr.MATCH
    assert _eval({"prop": "tags", "op": "in", "values": ["nope"]}, record) is cr.NO_MATCH


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------

def test_valid_nested_rule_accepted():
    rule = {
        "all": [
            {"prop": "status", "op": "eq", "values": ["Active"]},
            {"not": {"prop": "tags", "op": "in", "values": ["archived"]}},
            {"any": [
                {"prop": "points", "op": "gt", "values": [3]},
                {"prop": "due", "op": "after", "values": ["$today"]},
            ]},
        ]
    }
    assert cr.validate_rule(rule, SCHEMA) is None
    assert cr.validate_rule(VALID_RULE, SCHEMA) is None


def test_matches_operator_is_always_rejected():
    reason = cr.validate_rule(
        {"prop": "status", "op": "matches", "values": ["a.*"]}, SCHEMA
    )
    assert reason is not None
    assert "matches" in reason.lower()


def test_unknown_property_is_rejected():
    reason = cr.validate_rule(REMOVED_PROPERTY_RULE, SCHEMA)
    assert reason is not None
    assert "ghost" in reason


def test_incompatible_operator_for_kind_is_rejected():
    assert cr.validate_rule(INCOMPATIBLE_RULE, SCHEMA) is not None
    assert cr.validate_rule(
        {"prop": "points", "op": "before", "values": ["2026-01-01"]}, SCHEMA
    ) is not None
    assert cr.validate_rule(
        {"prop": "pointless", "op": "eq", "values": ["x"]}, {"pointless": "geometry"}
    ) is not None


def test_presence_operators_need_only_the_property_to_exist():
    assert cr.validate_rule({"prop": "points", "op": "exists"}, SCHEMA) is None
    assert cr.validate_rule({"prop": "tags", "op": "truthy"}, SCHEMA) is None


def test_value_operator_requires_a_non_empty_values_list():
    assert cr.validate_rule({"prop": "status", "op": "eq"}, SCHEMA) is not None
    assert cr.validate_rule({"prop": "status", "op": "eq", "values": []}, SCHEMA) is not None
    assert cr.validate_rule({"prop": "status", "op": "eq", "values": "Active"}, SCHEMA) is not None


def test_malformed_shapes_are_rejected_without_raising():
    for rule in (None, [], {"all": "x"}, {"all": [1]}, {"not": []}, {"nope": 1}, {"prop": "", "op": "eq", "values": [1]}):
        assert cr.validate_rule(rule, SCHEMA) is not None


def test_empty_combinators_are_valid():
    assert cr.validate_rule({"all": []}, SCHEMA) is None
    assert cr.validate_rule({"any": []}, SCHEMA) is None


def test_shape_only_validation_allows_an_unknown_property():
    # No discovered schema was supplied, so only the predicate's shape is
    # checkable; the draft store relies on this to hold an AE21 draft.
    assert cr.validate_rule(REMOVED_PROPERTY_RULE, None) is None
    assert cr.validate_rule(
        {"prop": "ghost", "op": "matches", "values": ["x"]}, None
    ) is not None


def test_schema_from_adapter_definitions():
    definitions = {
        "status": {"id": "status", "type": "label"},
        "points": {"id": "points", "type": "number"},
    }
    assert cr.schema_from_definitions(definitions) == {
        "status": "label",
        "points": "number",
    }
    # A raw definition list (the adapter also accepts one) normalizes too.
    assert cr.schema_from_definitions(
        [{"id": "due", "type": "date"}]
    ) == {"due": "date"}


# ---------------------------------------------------------------------------
# Active / draft persistence
# ---------------------------------------------------------------------------

def test_absent_file_yields_empty_default_without_creating_anything():
    path = cr.rules_path()
    assert not path.exists()
    record = cr.load_rules(SPACE)
    assert record.space_id == SPACE
    assert record.structures == ()
    assert record.revision == 0
    assert cr.effective_rule(SPACE, "Project") is None
    assert not path.exists()


def test_valid_rule_activates_and_is_also_recorded_as_draft():
    result = cr.save_rule(
        SPACE, "Project", VALID_RULE, fallback_minutes=25,
        expected_revision=0, schema=SCHEMA,
    )
    assert result.valid is True
    assert result.reason is None
    assert result.active == VALID_RULE
    assert result.draft == VALID_RULE
    assert result.revision == 1

    entry = cr.load_rules(SPACE).structure("Project")
    assert entry.active == VALID_RULE
    assert entry.draft == VALID_RULE
    assert entry.fallback_minutes == 25
    assert cr.effective_rule(SPACE, "Project") == VALID_RULE


def test_invalid_rule_is_draft_only_and_leaves_active_untouched():
    cr.save_rule(SPACE, "Project", VALID_RULE, expected_revision=0, schema=SCHEMA)
    result = cr.save_rule(
        SPACE, "Project", REMOVED_PROPERTY_RULE, expected_revision=1, schema=SCHEMA
    )
    assert result.valid is False
    assert result.reason is not None
    assert result.draft == REMOVED_PROPERTY_RULE
    assert result.active == VALID_RULE

    entry = cr.load_rules(SPACE).structure("Project")
    assert entry.active == VALID_RULE
    assert entry.draft == REMOVED_PROPERTY_RULE
    assert cr.effective_rule(SPACE, "Project") == VALID_RULE


def test_incompatible_operator_rule_cannot_activate():
    result = cr.save_rule(
        SPACE, "Project", INCOMPATIBLE_RULE, expected_revision=0, schema=SCHEMA
    )
    assert result.valid is False
    assert result.active is None
    assert result.draft == INCOMPATIBLE_RULE
    assert cr.effective_rule(SPACE, "Project") is None


def test_valid_rule_saves_with_no_cached_content_available():
    home = Path(os.environ["TDTB_HOME"])
    assert not (app_config.state_dir() / "capacities-refresh").exists()
    result = cr.save_rule(
        SPACE, "Project", VALID_RULE, expected_revision=0, schema=SCHEMA
    )
    assert result.valid is True
    assert not (home / "state" / "producer-cache.json").exists()


def test_empty_all_rule_is_distinguishable_from_no_stored_rule():
    cr.save_rule(SPACE, "Project", {"all": []}, expected_revision=0, schema=SCHEMA)
    assert cr.effective_rule(SPACE, "Project") == {"all": []}
    assert cr.effective_rule(SPACE, "Missing") is None


def test_revision_increments_on_a_valid_save():
    first = cr.save_rule(SPACE, "A", VALID_RULE, expected_revision=0, schema=SCHEMA)
    assert first.revision == 1
    second = cr.save_rule(SPACE, "B", VALID_RULE, expected_revision=1, schema=SCHEMA)
    assert second.revision == 2
    assert cr.load_rules(SPACE).revision == 2


def test_stale_expected_revision_conflicts_and_preserves_bytes():
    cr.save_rule(SPACE, "Project", VALID_RULE, expected_revision=0, schema=SCHEMA)
    path = cr.rules_path()
    before = path.read_bytes()
    with pytest.raises(cr.RulesConflictError) as excinfo:
        cr.save_rule(
            SPACE, "Project", VALID_RULE, expected_revision=0, schema=SCHEMA
        )
    assert excinfo.value.expected_revision == 0
    assert excinfo.value.current_revision == 1
    assert path.read_bytes() == before
    assert cr.load_rules(SPACE).revision == 1


def test_malformed_storage_raises_and_never_overwrites():
    path = cr.rules_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(cr.RulesFormatError):
        cr.load_rules(SPACE)
    with pytest.raises(cr.RulesFormatError):
        cr.save_rule(SPACE, "Project", VALID_RULE, expected_revision=0, schema=SCHEMA)
    assert path.read_bytes() == before


def test_unsupported_version_raises():
    path = cr.rules_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"version": 99, "revision": 0, "space_id": SPACE, "structures": []}),
        encoding="utf-8",
    )
    with pytest.raises(cr.RulesFormatError):
        cr.load_rules(SPACE)


def test_duplicate_json_keys_are_rejected():
    path = cr.rules_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        '{"version":1,"version":1,"revision":0,"space_id":"space-1","structures":[]}',
        encoding="utf-8",
    )
    with pytest.raises(cr.RulesFormatError):
        cr.load_rules(SPACE)


def test_reads_are_space_scoped():
    cr.save_rule(SPACE, "Project", VALID_RULE, expected_revision=0, schema=SCHEMA)
    other = cr.load_rules(SPACE_OTHER)
    assert other.space_id == SPACE_OTHER
    assert other.structures == ()
    assert cr.effective_rule(SPACE_OTHER, "Project") is None
    assert cr.effective_rule(SPACE, "Project") == VALID_RULE


def test_store_stays_under_isolated_app_home_and_makes_no_network_call(monkeypatch):
    home = Path(os.environ["TDTB_HOME"])

    def _no_connect(*args, **kwargs):  # pragma: no cover - fails the test if hit
        raise AssertionError("a network connection was attempted")

    monkeypatch.setattr(socket.socket, "connect", _no_connect)
    cr.save_rule(SPACE, "Project", VALID_RULE, expected_revision=0, schema=SCHEMA)

    assert cr.rules_path() == app_config.state_dir() / cr.STATE_FILENAME
    assert str(cr.rules_path()).startswith(str(home))
    files = {
        p.relative_to(home).as_posix()
        for p in home.rglob("*")
        if p.is_file()
    }
    assert files <= {
        f"state/{cr.STATE_FILENAME}",
        f"state/{cr.STATE_LOCK_FILENAME}",
    }
