"""U3c-1 — durable per-identity Capacities selections plus the pure resolver.

Test-first: this file was written before ``app/capacities_selections.py``
existed so the red state is an import failure, then each test failed for its
stated reason against a no-op skeleton, then implemented to green.

Everything here is fixture-only — no provider, credential, vault, network, or
real ``$HOME`` read. The autouse ``_isolated_app_home`` fixture (conftest) puts
``TDTB_HOME`` in a per-test tmp dir, so the store under test never touches the
operator's machine state.
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import capacities_selections as cs  # noqa: E402

SPACE = "space-1"
OTHER_SPACE = "space-2"
IDENTITY = f"capacities:{SPACE}:Project:obj-1"
IDENTITY_2 = f"capacities:{SPACE}:Project:obj-2"


def _row(
    identity: str,
    *,
    rule_state: str = "match",
    rule_revision: int = 0,
    completion_state: str = "open",
    reasons: tuple[str, ...] = (),
) -> dict:
    """A cached server row shaped like the adapter's output.

    Only keys the adapter actually emits are used; ``source_fingerprint`` is
    present on real rows but deliberately unused (the selection is
    identity-only)."""
    return {
        "id": "Write the thing",
        "name": "Write the thing",
        "path": "/tasks/obj-1",
        "identity": identity,
        "source": "capacities",
        "types": ["Project"],
        "assigned": True,
        "source_fingerprint": "fingerprint-obj-1",
        "capacities_rule": {"state": rule_state, "revision": rule_revision},
        "capacities_completion_state": completion_state,
        "capacities_review_reasons": list(reasons),
    }


def _entry(identity: str, *, rules_revision: int = 0, acknowledged: bool = False) -> dict:
    return {
        "identity": identity,
        "rules_revision": rules_revision,
        "acknowledged": acknowledged,
    }


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------

def test_save_requires_canonical_identity_and_bumps_revision_with_conflict():
    with pytest.raises(cs.SelectionValidationError):
        cs.save_selections(
            space_id=SPACE,
            expected_revision=0,
            selections=[_entry("not-an-identity")],
        )
    assert not cs.selections_path().exists()

    saved = cs.save_selections(
        space_id=SPACE,
        expected_revision=0,
        selections=[_entry(IDENTITY, rules_revision=1, acknowledged=True)],
    )
    assert saved.revision == 1
    assert saved.selections == (cs.SelectionRecord(IDENTITY, 1, True),)
    assert cs.load_selections(SPACE) == saved

    target = cs.selections_path()
    before = target.read_bytes()
    with pytest.raises(cs.SelectionConflict) as caught:
        cs.save_selections(
            space_id=SPACE,
            expected_revision=0,
            selections=[_entry(IDENTITY)],
        )
    assert caught.value.expected_revision == 0
    assert caught.value.current_revision == 1
    assert target.read_bytes() == before


def test_forged_identity_is_rejected_on_save():
    forged = [
        "Write the thing",                       # a title
        "/vault/notes/obj-1.md",                 # a path
        "obj-1",                                 # a bare object id
        "capacities_id",                         # a capacities_* field name
        " Capacities:space-1:Project:obj-1",     # non-canonical casing/space
        f"capacities:{OTHER_SPACE}:Project:obj-1",  # a different space
    ]
    for value in forged:
        with pytest.raises(cs.SelectionValidationError):
            cs.save_selections(
                space_id=SPACE,
                expected_revision=0,
                selections=[_entry(value)],
            )
    assert not cs.selections_path().exists()


def test_save_rejects_non_bool_acknowledged_and_non_int_revision():
    with pytest.raises(cs.SelectionValidationError):
        cs.save_selections(
            space_id=SPACE,
            expected_revision=0,
            selections=[_entry(IDENTITY, acknowledged=1)],
        )
    with pytest.raises(cs.SelectionValidationError):
        cs.save_selections(
            space_id=SPACE,
            expected_revision=0,
            selections=[_entry(IDENTITY, rules_revision=True)],
        )
    with pytest.raises(cs.SelectionValidationError):
        cs.save_selections(
            space_id=SPACE,
            expected_revision=0,
            selections=[_entry(IDENTITY, rules_revision=-1)],
        )
    with pytest.raises(cs.SelectionValidationError):
        cs.save_selections(
            space_id=SPACE,
            expected_revision=0,
            selections=[_entry(IDENTITY), _entry(IDENTITY)],
        )
    assert not cs.selections_path().exists()


def test_corrupt_store_raises_instead_of_resetting():
    target = cs.selections_path()
    target.parent.mkdir(parents=True, exist_ok=True)

    duplicate = (
        '{"version": 1, "revision": 1, "space_id": "space-1", '
        '"selections": [], "selections": []}'
    )
    target.write_text(duplicate, encoding="utf-8")
    with pytest.raises(cs.SelectionFormatError):
        cs.load_selections(SPACE)
    assert target.read_text(encoding="utf-8") == duplicate

    unknown = json.dumps(
        {
            "version": 1,
            "revision": 1,
            "space_id": SPACE,
            "selections": [_entry(IDENTITY)],
            "extra": True,
        }
    )
    target.write_text(unknown, encoding="utf-8")
    with pytest.raises(cs.SelectionFormatError):
        cs.load_selections(SPACE)
    assert target.read_text(encoding="utf-8") == unknown

    bad_bool = json.dumps(
        {
            "version": 1,
            "revision": 1,
            "space_id": SPACE,
            "selections": [_entry(IDENTITY, acknowledged=1)],
        }
    )
    target.write_text(bad_bool, encoding="utf-8")
    with pytest.raises(cs.SelectionFormatError):
        cs.load_selections(SPACE)
    assert target.read_text(encoding="utf-8") == bad_bool


def test_other_space_document_is_treated_as_absent():
    saved = cs.save_selections(
        space_id=SPACE,
        expected_revision=0,
        selections=[_entry(IDENTITY)],
    )
    assert saved.revision == 1
    target = cs.selections_path()
    before = target.read_bytes()

    foreign = cs.load_selections(OTHER_SPACE)
    assert foreign.space_id == OTHER_SPACE
    assert foreign.revision == 0
    assert foreign.selections == ()
    assert target.read_bytes() == before

    # The foreign-space document is absent for conflict purposes too: the
    # caller's first save into the new space starts from revision 0.
    replaced = cs.save_selections(
        space_id=OTHER_SPACE,
        expected_revision=0,
        selections=[_entry(f"capacities:{OTHER_SPACE}:Project:obj-9")],
    )
    assert replaced.revision == 1
    assert cs.load_selections(OTHER_SPACE).selections == replaced.selections


# ---------------------------------------------------------------------------
# Resolver
# ---------------------------------------------------------------------------

def test_forged_identity_absent_from_cached_rows_is_never_promoted():
    saved = cs.save_selections(
        space_id=SPACE,
        expected_revision=0,
        selections=[_entry(IDENTITY)],
    )
    promoted, notices = cs.resolve_selections(
        saved.selections,
        [],
        rules_revision=0,
        mapped_structures={"Project"},
        hard_excluded=frozenset(),
    )
    assert promoted == []
    assert notices == [{"code": "not_cached", "identity": IDENTITY}]
    # The record stays stored; the resolver never removes it.
    assert cs.load_selections(SPACE).selections == saved.selections


def test_hard_exclusion_removes_selection_with_notice():
    row = _row(IDENTITY)
    saved = cs.save_selections(
        space_id=SPACE,
        expected_revision=0,
        selections=[_entry(IDENTITY)],
    )
    promoted, notices = cs.resolve_selections(
        saved.selections,
        [row],
        rules_revision=0,
        mapped_structures={"Project"},
        hard_excluded=frozenset({IDENTITY}),
    )
    assert promoted == []
    assert notices == [{"code": "excluded", "identity": IDENTITY}]


def test_rule_change_retains_selection_with_warning():
    row = _row(IDENTITY, rule_revision=1)
    saved = cs.save_selections(
        space_id=SPACE,
        expected_revision=0,
        selections=[_entry(IDENTITY, rules_revision=1)],
    )
    promoted, notices = cs.resolve_selections(
        saved.selections,
        [row],
        rules_revision=2,
        mapped_structures={"Project"},
        hard_excluded=frozenset(),
    )
    assert promoted == [row]
    assert promoted[0] is not row
    assert notices == [{"code": "rule_changed", "identity": IDENTITY}]


def test_type_removal_retains_selection_as_disabled_record():
    row = _row(IDENTITY)
    saved = cs.save_selections(
        space_id=SPACE,
        expected_revision=0,
        selections=[_entry(IDENTITY)],
    )
    promoted, notices = cs.resolve_selections(
        saved.selections,
        [row],
        rules_revision=0,
        mapped_structures={"RootTask"},
        hard_excluded=frozenset(),
    )
    assert promoted == []
    assert notices == [{"code": "type_disabled", "identity": IDENTITY}]

    reloaded = cs.load_selections(SPACE)
    assert reloaded.revision == 1
    assert reloaded.selections == saved.selections


def test_unknown_row_selection_keeps_its_unknown_state_and_reasons():
    row = _row(
        IDENTITY,
        rule_state="unknown",
        completion_state="unknown",
        reasons=("rule_unknown", "completion_unknown"),
    )
    snapshot = copy.deepcopy(row)
    saved = cs.save_selections(
        space_id=SPACE,
        expected_revision=0,
        selections=[_entry(IDENTITY)],
    )
    promoted, notices = cs.resolve_selections(
        saved.selections,
        [row],
        rules_revision=0,
        mapped_structures={"Project"},
        hard_excluded=frozenset(),
    )
    assert notices == []
    assert len(promoted) == 1
    assert promoted[0] is not row
    assert promoted[0] == row
    assert promoted[0]["capacities_rule"] == {"state": "unknown", "revision": 0}
    assert promoted[0]["capacities_completion_state"] == "unknown"
    assert promoted[0]["capacities_review_reasons"] == [
        "rule_unknown",
        "completion_unknown",
    ]
    assert row == snapshot


def test_resolver_promotes_in_selection_order_and_stays_silent_when_current():
    rows = [_row(IDENTITY), _row(IDENTITY_2)]
    saved = cs.save_selections(
        space_id=SPACE,
        expected_revision=0,
        selections=[
            _entry(IDENTITY_2, rules_revision=3),
            _entry(IDENTITY, rules_revision=3),
        ],
    )
    promoted, notices = cs.resolve_selections(
        saved.selections,
        rows,
        rules_revision=3,
        mapped_structures={"Project"},
        hard_excluded=frozenset(),
    )
    assert notices == []
    assert promoted == [rows[1], rows[0]]
    assert promoted[0] is not rows[1]
    assert promoted[1] is not rows[0]
