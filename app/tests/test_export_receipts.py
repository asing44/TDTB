"""U5 B2 — durable content-free export receipts (``export_receipts.py``).

These pin the crash/concurrency contract the Commit-only prompt export lane
depends on:

- an absent store reads empty and creates nothing;
- ``begin_export`` writes a durable ``pending`` record BEFORE any provider
  call, and only the caller that created it may proceed;
- an already-``done`` key is skipped (never re-created);
- an unconfirmed ``pending``/``needs_review`` key strands as ``needs_review``;
- malformed/duplicate-keyed storage fails closed and its bytes are preserved;
- concurrent begins elect exactly one creator;
- a receipt is content-free (fixed key set, bounded reason).

Fixture-only: the autouse ``_isolated_app_home`` fixture (conftest) puts
``TDTB_HOME`` in a per-test tmp dir, so no real machine state is touched.
"""
from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import export_receipts as er  # noqa: E402

DAY = "2026-10-09"
KEY = "megan_nicety"
ACTION = er.ACTION_TODOIST_TASK


def _begin(**kw):
    return er.begin_export(day=kw.get("day", DAY), prompt_key=kw.get("key", KEY),
                           action=ACTION)


# ---------------------------------------------------------------------------
# Absent store / write-ahead pending
# ---------------------------------------------------------------------------

def test_absent_store_is_empty_and_creates_nothing():
    assert er.load_receipts() == er.ExportReceiptsRecord()
    assert not er.receipts_path().exists()


def test_begin_persists_pending_before_any_provider_call():
    outcome = _begin()
    assert outcome.action == er.BEGIN
    assert outcome.receipt.status == er.STATUS_PENDING
    assert outcome.receipt.task_id is None
    # Durable on disk before the caller could have touched a provider.
    stored = er.load_receipts().get(DAY, KEY, ACTION)
    assert stored is not None and stored.status == er.STATUS_PENDING


def test_second_begin_on_pending_strands_as_needs_review():
    _begin()
    second = _begin()
    assert second.action == er.NEEDS_REVIEW
    assert second.receipt.status == er.STATUS_PENDING


def test_done_receipt_is_skipped_not_recreated():
    _begin()
    er.complete_export(day=DAY, prompt_key=KEY, action=ACTION, task_id="t-77")
    again = _begin()
    assert again.action == er.SKIP_DONE
    assert again.receipt.status == er.STATUS_DONE
    assert again.receipt.task_id == "t-77"


def test_failed_receipt_strands_as_needs_review_with_reason():
    _begin()
    er.fail_export(day=DAY, prompt_key=KEY, action=ACTION,
                   reason="provider rejected the export")
    again = _begin()
    assert again.action == er.NEEDS_REVIEW
    assert again.receipt.status == er.STATUS_NEEDS_REVIEW
    assert again.receipt.reason == "provider rejected the export"


def test_complete_requires_a_task_id():
    with pytest.raises(er.ExportReceiptValidationError):
        er.complete_export(day=DAY, prompt_key=KEY, action=ACTION, task_id="")


def test_unknown_action_or_key_is_rejected_without_writing():
    with pytest.raises(er.ExportReceiptValidationError):
        er.begin_export(day=DAY, prompt_key=KEY, action="bogus")
    with pytest.raises(er.ExportReceiptValidationError):
        er.begin_export(day=DAY, prompt_key="unknown_prompt", action=ACTION)
    assert not er.receipts_path().exists()


# ---------------------------------------------------------------------------
# Content-free + bounded
# ---------------------------------------------------------------------------

def test_receipt_is_content_free_and_bounded():
    _begin()
    er.fail_export(day=DAY, prompt_key=KEY, action=ACTION,
                   reason="x" * 500 + "\x00control")
    stored = er.load_receipts().get(DAY, KEY, ACTION)
    assert set(stored.as_dict()) == {
        "day", "prompt_key", "action", "status", "task_id", "reason", "updated_at",
    }
    assert len(stored.reason) <= er.MAX_REASON_LEN
    assert "\x00" not in stored.reason
    raw = er.receipts_path().read_text(encoding="utf-8")
    assert "\x00" not in raw


# ---------------------------------------------------------------------------
# Strict decoding / corrupt preservation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw", [
    "not json",
    '{"version": 1, "version": 1, "receipts": {}}',
    json.dumps({"version": 2, "receipts": {}}),
    json.dumps({"version": 1, "receipts": {}, "extra": True}),
    json.dumps({"version": 1}),
    json.dumps({"version": 1, "receipts": []}),
    json.dumps({"version": 1, "receipts": {
        f"{DAY}:{KEY}:{ACTION}": {"day": DAY, "prompt_key": KEY,
                                  "action": ACTION, "status": "nope",
                                  "task_id": None, "reason": None,
                                  "updated_at": None}}}),
    json.dumps({"version": 1, "receipts": {
        "wrong-key": {"day": DAY, "prompt_key": KEY, "action": ACTION,
                      "status": "pending", "task_id": None, "reason": None,
                      "updated_at": None}}}),
])
def test_malformed_storage_is_rejected_without_repair(raw):
    target = er.receipts_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(raw, encoding="utf-8")

    with pytest.raises(er.ExportReceiptFormatError):
        er.load_receipts()
    with pytest.raises(er.ExportReceiptFormatError):
        _begin()
    assert target.read_text(encoding="utf-8") == raw


def test_corrupt_store_preserves_bytes_on_write_refusal():
    target = er.receipts_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    raw = '{"version": 1, "receipts": {"dup": {}, "dup": {}}}'
    target.write_text(raw, encoding="utf-8")
    with pytest.raises(er.ExportReceiptFormatError):
        er.complete_export(day=DAY, prompt_key=KEY, action=ACTION, task_id="t1")
    assert target.read_text(encoding="utf-8") == raw


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------

def test_concurrent_begin_elects_one_creator():
    barrier = threading.Barrier(2)
    outcomes: list[object] = []

    def worker() -> None:
        barrier.wait()
        try:
            outcomes.append(_begin())
        except er.ExportReceiptError as exc:  # pragma: no cover — must not happen
            outcomes.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    begins = [o for o in outcomes if isinstance(o, er.BeginOutcome) and o.action == er.BEGIN]
    reviews = [o for o in outcomes if isinstance(o, er.BeginOutcome) and o.action == er.NEEDS_REVIEW]
    assert len(begins) == 1 and len(reviews) == 1
    # Exactly one durable record survives.
    record = er.load_receipts()
    assert len(record.receipts) == 1
