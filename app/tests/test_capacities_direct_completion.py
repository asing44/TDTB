"""Direct-refresh completion override for rule-less rows (U3b-4).

Test-first for the fourth U3b slice:

* a direct refresh always supplies a ``RulesRecord`` (possibly empty or
  draft-only), while the legacy builder supplies ``None``; a rule-less
  direct row keeps the legacy ``evaluate_assignment`` admission decision but
  is additionally classified by the strict completion vocabulary;
* a mapped known completed value hard-excludes even a source-assigned row;
* a mapped completion that is absent, empty, unreadable, or a distinct
  non-completed value is UNKNOWN: the row stays an unassigned warning
  candidate with ``capacities_completion_state`` and a
  ``completion_unknown`` review reason, and is counted as evaluated, never
  malformed;
* an unmapped completion is ``unavailable`` with no fabricated reason;
* a same-field status+completion checks the completed token first, so a
  mixed ``[active, done]`` payload hard-excludes while an open payload
  admits; readable completed evidence survives a partly malformed payload;
* a row the legacy evaluator rejects is never resurrected as an UNKNOWN
  candidate, and ``rules=None`` keeps the old row shape untouched.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import capacities_rules as cr  # noqa: E402
from tests.test_capacities_rule_fallbacks import (  # noqa: E402
    DAY,
    _adapter,
    _custom_object,
    _legacy_root_mapping,
    _prop,
    _rules,
    _status_mapping,
)

RULE = {"prop": "minutes", "op": "gt", "values": [10]}
REVIEW_PREFIX = "Capacities review"


def _review_warnings(warnings):
    return [warning for warning in warnings if warning.startswith(REVIEW_PREFIX)]


def _distinct_mapping():
    return _status_mapping(
        completion_property="completion", completion_value="complete"
    )


def _same_field_mapping():
    return _status_mapping(completion_property="state", completion_value="done")


def _direct(mapping, record):
    return _adapter([_legacy_root_mapping(), mapping], record)


# ---------------------------------------------------------------------------
# A mapped completed value hard-excludes even a source-assigned direct row
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "record",
    [
        pytest.param(_rules(), id="empty"),
        pytest.param(
            _rules(cr.RuleStructureRecord("custom-project", draft=RULE)),
            id="draft-only",
        ),
    ],
)
def test_direct_mapped_completed_value_hard_excludes_source_assigned_row(record):
    """The direct path's completion override excludes a completed row even
    though the legacy decision admits it as source-assigned."""
    result = _direct(_distinct_mapping(), record).items_for_day_from_objects(
        DAY, [_custom_object(completion="complete")]
    )

    assert result.items == []
    assert result.malformed == 0
    assert result.evaluated == 1


# ---------------------------------------------------------------------------
# UNKNOWN completion stays visible, unassigned, and warned
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "completion",
    [
        pytest.param("started", id="other-readable-value"),
        pytest.param(None, id="missing"),
        pytest.param({"type": "label", "label": []}, id="empty"),
        pytest.param({"type": "label", "label": "started"}, id="unreadable"),
    ],
)
def test_direct_unknown_completion_is_retained_unassigned_with_reason(completion):
    """Every non-completed distinct completion value is UNKNOWN: the row
    stays an unassigned warning candidate instead of being guessed closed or
    failed, and the legacy decision metadata is left intact."""
    result = _direct(_distinct_mapping(), _rules()).items_for_day_from_objects(
        DAY, [_custom_object(completion=completion)]
    )

    assert len(result.items) == 1
    row = result.items[0]
    assert row["assigned"] is False
    assert row["capacities_completion_state"] == "unknown"
    assert row["capacities_review_reasons"] == ["completion_unknown"]
    assert "capacities_rule" not in row
    assert row["capacities_assignment"]["source_assigned"] is True
    assert result.malformed == 0
    warnings = _review_warnings(result.warnings)
    assert len(warnings) == 1
    assert "completion_unknown" in warnings[0]
    assert "Rescan Capacities" in warnings[0]


def test_direct_unmapped_completion_is_unavailable_without_reason():
    """No completion mapping is a decided gap: ``unavailable``, no reason,
    no warning, and the legacy admission decision unchanged."""
    result = _direct(_status_mapping(), _rules()).items_for_day_from_objects(
        DAY, [_custom_object()]
    )

    assert len(result.items) == 1
    row = result.items[0]
    assert row["assigned"] is True
    assert row["capacities_completion_state"] == "unavailable"
    assert row["capacities_review_reasons"] == []
    assert row["capacities_completion_supported"] is False
    assert _review_warnings(result.warnings) == []


# ---------------------------------------------------------------------------
# Same-field status+completion: the completed token is checked first
# ---------------------------------------------------------------------------

def test_direct_same_field_completed_token_excludes_alongside_open():
    """A mixed ``[active, done]`` payload is a known completion even though
    ``active`` intersects the open values; an open payload still admits."""
    result = _direct(_same_field_mapping(), _rules()).items_for_day_from_objects(
        DAY,
        [
            _custom_object(object_id="obj-open"),
            _custom_object(
                state=_prop("label", "label", [{"id": "active"}, {"id": "done"}]),
                object_id="obj-mixed",
            ),
        ],
    )

    assert [row["capacities_id"] for row in result.items] == ["obj-open"]
    row = result.items[0]
    assert row["assigned"] is True
    assert row["capacities_completion_state"] == "open"
    assert row["capacities_review_reasons"] == []
    assert result.malformed == 0


def test_direct_partial_malformed_completed_token_still_hard_excludes():
    """Readable completed evidence wins over a partly malformed payload, so
    the strict hard exclusion is preserved on the direct path."""
    result = _direct(_same_field_mapping(), _rules()).items_for_day_from_objects(
        DAY,
        [
            _custom_object(
                state=_prop("label", "label", [{"id": {"x": 1}}, {"id": "done"}])
            )
        ],
    )

    assert result.items == []
    assert result.malformed == 0


# ---------------------------------------------------------------------------
# The override never resurrects a row the legacy evaluator rejected
# ---------------------------------------------------------------------------

def test_direct_unknown_completion_does_not_resurrect_legacy_rejected_row():
    """A row the legacy decision rejects stays rejected: the completion
    override classifies only rows ``evaluate_assignment`` already admitted."""
    result = _direct(_distinct_mapping(), _rules()).items_for_day_from_objects(
        DAY, [_custom_object(tdtb="no", completion=None)]
    )

    assert result.items == []
    assert _review_warnings(result.warnings) == []
    assert result.malformed == 0


# ---------------------------------------------------------------------------
# rules=None keeps the legacy row shape byte-for-byte
# ---------------------------------------------------------------------------

def test_legacy_none_record_keeps_old_shape_for_completed_and_missing_rows():
    """The legacy builder supplies no record: completed and missing
    completion values are admitted as before, with no new metadata and no
    review warning."""
    result = _adapter(
        [_legacy_root_mapping(), _distinct_mapping()], None
    ).items_for_day_from_objects(
        DAY,
        [
            _custom_object(completion="complete", object_id="obj-complete"),
            _custom_object(completion=None, object_id="obj-missing"),
        ],
    )

    assert len(result.items) == 2
    for row in result.items:
        assert row["assigned"] is True
        assert "capacities_completion_state" not in row
        assert "capacities_review_reasons" not in row
        assert "capacities_rule" not in row
        assert row["capacities_assignment"]["source_assigned"] is True
    assert _review_warnings(result.warnings) == []
    assert result.malformed == 0
