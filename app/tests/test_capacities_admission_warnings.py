"""Visible review-warning surface for under-evaluated stored-rule rows (U3b-3).

Test-first for the third U3b slice:

* an active-rule row carries a stable ``capacities_review_reasons`` list; the
  codes ``rule_unknown`` / ``status_unknown`` / ``completion_unknown`` are
  emitted ONLY for the corresponding UNKNOWN state, while ``unavailable``
  (no mapped property) and a manual unassigned candidate stay reason-free;
* ``_project_objects`` turns rows with reasons into a bounded, content-free
  warning that names the count, a few identities, and suggests a Rescan; the
  warning rows stay items and count as evaluated, so the run stays a complete
  publication rather than a partial success;
* the refresh coordinator passes the projection warnings into its successful
  ``complete``/``published`` finish instead of discarding them, while the
  installed snapshot remains a complete generation that retains the object.

The helpers under test are the existing local fakes: the stored-rule fixtures
in ``tests/test_capacities_rule_fallbacks.py`` and the coordinator fakes in
``tests/capacities_refresh_helpers.py``. No provider, credential, or vault
read is involved.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import capacities_adapter as ca  # noqa: E402
import capacities_refresh as rr  # noqa: E402
from tests.test_capacities_adapter import FakeProvider  # noqa: E402
from tests.test_capacities_rule_fallbacks import (  # noqa: E402
    DAY,
    REVISION,
    SPACE,
    _adapter,
    _custom_object,
    _custom_rule,
    _fixture_structures,
    _legacy_root_mapping,
    _rules,
    _status_mapping,
    _title_only_mapping,
)

sys.path.insert(0, str(Path(__file__).parent))
from capacities_refresh_helpers import Clock, Sleeper, _row, _store  # noqa: E402

REVIEW_PREFIX = "Capacities review"


def _review_warnings(warnings):
    return [warning for warning in warnings if warning.startswith(REVIEW_PREFIX)]


# ---------------------------------------------------------------------------
# Structured reasons are scoped to the corresponding UNKNOWN states
# ---------------------------------------------------------------------------

def test_unknown_status_keeps_rule_match_and_records_reasons():
    """A rule MATCH with an UNKNOWN status/completion stays truthful: the rule
    state is still ``match``, the row stays unassigned, and both unknowns are
    recorded as stable reason codes."""
    mapping = _status_mapping(
        completion_property="completion", completion_value="complete"
    )
    mappings = [_legacy_root_mapping(), mapping]
    record = _rules(_custom_rule())

    result = _adapter(mappings, record).items_for_day_from_objects(
        DAY, [_custom_object(state=None, completion=None)]
    )

    assert len(result.items) == 1
    row = result.items[0]
    assert row["capacities_rule"] == {"state": "match", "revision": REVISION}
    assert row["capacities_status_state"] == "unknown"
    assert row["capacities_completion_state"] == "unknown"
    assert row["capacities_review_reasons"] == [
        "status_unknown",
        "completion_unknown",
    ]
    assert row["assigned"] is False
    assert result.malformed == 0


def test_unknown_rule_is_a_review_reason():
    """An UNKNOWN rule evaluation is itself a review reason, independent of the
    status/completion classification."""
    mappings = [_legacy_root_mapping(), _status_mapping()]
    record = _rules(_custom_rule())

    result = _adapter(mappings, record).items_for_day_from_objects(
        DAY, [_custom_object(minutes=None)]
    )

    assert len(result.items) == 1
    row = result.items[0]
    assert row["capacities_rule"] == {"state": "unknown", "revision": REVISION}
    assert row["capacities_review_reasons"] == ["rule_unknown"]
    assert row["assigned"] is False


@pytest.mark.parametrize(
    "mapping",
    [
        pytest.param(_title_only_mapping("custom-project"), id="manual-unavailable"),
        pytest.param(_status_mapping(), id="open-status-completion-unavailable"),
    ],
)
def test_unavailable_and_manual_candidates_have_no_fake_reasons(mapping):
    """``unavailable`` is not UNKNOWN, and a manual unassigned candidate is a
    decided MATCH: neither fabricates a review reason or a review warning."""
    mappings = [_legacy_root_mapping(), mapping]
    record = _rules(_custom_rule())

    result = _adapter(mappings, record).items_for_day_from_objects(
        DAY, [_custom_object()]
    )

    assert len(result.items) == 1
    row = result.items[0]
    assert row["capacities_rule"]["state"] == "match"
    assert row["capacities_review_reasons"] == []
    assert _review_warnings(result.warnings) == []


def test_legacy_rows_carry_no_review_reasons():
    """Without an active rule the legacy row shape is unchanged."""
    mappings = [_legacy_root_mapping(), _status_mapping()]
    result = _adapter(mappings, None).items_for_day_from_objects(
        DAY, [_custom_object()]
    )

    assert len(result.items) == 1
    assert "capacities_review_reasons" not in result.items[0]
    assert _review_warnings(result.warnings) == []


# ---------------------------------------------------------------------------
# Bounded visible warnings; warning rows stay items and evaluated
# ---------------------------------------------------------------------------

def test_review_warning_is_bounded_and_names_count_identity_and_rescan():
    """Eight unknown rows produce ONE bounded warning: the exact count, at most
    three identities with their reason codes, a ``+N more`` remainder, and a
    Rescan suggestion — and no raw object content."""
    mapping = _status_mapping(
        completion_property="completion", completion_value="complete"
    )
    mappings = [_legacy_root_mapping(), mapping]
    record = _rules(_custom_rule())
    objects = [
        _custom_object(
            minutes=None, state=None, completion=None, object_id=f"obj-{index}"
        )
        for index in range(8)
    ]

    result = _adapter(mappings, record).items_for_day_from_objects(DAY, objects)

    # Every warning row is retained and counted as evaluated: the run is a
    # complete publication with a review surface, never a partial success.
    assert len(result.items) == 8
    assert result.evaluated == 8
    assert result.malformed == 0
    review = _review_warnings(result.warnings)
    assert len(review) == 1
    warning = review[0]
    assert "8 unassigned candidate" in warning
    assert "capacities:space-1:custom-project:obj-0" in warning
    assert "rule_unknown" in warning
    assert "status_unknown" in warning
    assert "completion_unknown" in warning
    assert "+5 more" in warning
    assert "Rescan Capacities" in warning
    # Bounded: at most three identities named, no raw content, short message.
    assert warning.count("capacities:space-1:custom-project:") == 3
    assert "Sample" not in warning
    assert len(warning) < 600


# ---------------------------------------------------------------------------
# The coordinator keeps the warnings on a successful complete publication
# ---------------------------------------------------------------------------

def test_coordinator_complete_publication_carries_review_warnings(tmp_path):
    """A successful refresh still publishes one complete generation, but the
    under-evaluated row's review warning rides the job instead of being
    discarded with the projection result."""
    mapping = _status_mapping(
        completion_property="completion", completion_value="complete"
    )
    mappings = (_legacy_root_mapping(), mapping)
    record = _rules(_custom_rule())
    pages = {
        ("custom-project", None): {
            "objects": [_row("obj-1", "custom-project")],
            "next_cursor": None,
        }
    }
    content = {
        "obj-1": _custom_object(minutes=None, state=None, completion=None)
    }
    provider = FakeProvider(_fixture_structures(), pages, content)
    store = _store(tmp_path)
    clock = Clock()
    coordinator = rr.RefreshCoordinator(
        root=tmp_path / "state",
        store=store,
        provider=provider,
        space_id=SPACE,
        mappings=mappings,
        revision_supplier=lambda: 1,
        scope_key="all",
        rules=record,
        logical_day=DAY,
        clock=clock,
        sleeper=Sleeper(clock),
    )

    coordinator.start()
    status = coordinator.wait(timeout=5)

    assert status["phase"] == "complete"
    assert status["outcome"] == "published"
    review = _review_warnings(status["warnings"])
    assert len(review) == 1
    assert "1 unassigned candidate" in review[0]
    assert "capacities:space-1:custom-project:obj-1" in review[0]
    assert "Rescan Capacities" in review[0]
    # The publication itself is complete and retains the warning row's object.
    snapshot = store.load_snapshot("all")
    assert snapshot is not None and snapshot.generation == 1
    assert {member.object_id for member in snapshot.members} == {"obj-1"}
