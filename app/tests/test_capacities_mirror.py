"""Tests for capacities_mirror.py — the machine-local receipts + exclusion
store for the Capacities -> PHEP Todoist mirror lane (contract change
2026-10-13).

The store is the durable authority that makes the mirror write idempotent
(one task per logical day + Capacities identity), records an ambiguous
attempt as ``needs_review`` (never blind-retried), and hides created mirror
ids from Todoist intake. Reads are fail-closed: malformed or unreadable
storage raises a typed error rather than silently creating a duplicate.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import capacities_mirror as mirror  # noqa: E402

DAY = "2026-10-13"
IDENTITY = "capacities:space-1:RootTask:object-1"


class TestRoundTrip:
    def test_absent_store_is_empty(self):
        record = mirror.load_mirror()
        assert dict(record.receipts) == {}
        assert record.excluded_task_ids == ()

    def test_begin_complete_persists_and_excludes(self):
        out = mirror.begin_mirror(day=DAY, identity=IDENTITY)
        assert out.action == mirror.BEGIN
        assert out.receipt.status == mirror.STATUS_PENDING
        done = mirror.complete_mirror(day=DAY, identity=IDENTITY, task_id="t1")
        assert done.status == mirror.STATUS_DONE and done.task_id == "t1"
        assert mirror.excluded_task_ids() == ("t1",)

    def test_second_begin_after_done_skips(self):
        mirror.complete_mirror(day=DAY, identity=IDENTITY, task_id="t1")
        out = mirror.begin_mirror(day=DAY, identity=IDENTITY)
        assert out.action == mirror.SKIP_DONE
        assert out.receipt.task_id == "t1"

    def test_second_begin_after_pending_needs_review(self):
        mirror.begin_mirror(day=DAY, identity=IDENTITY)
        out = mirror.begin_mirror(day=DAY, identity=IDENTITY)
        assert out.action == mirror.NEEDS_REVIEW

    def test_fail_records_needs_review_never_retried(self):
        mirror.begin_mirror(day=DAY, identity=IDENTITY)
        failed = mirror.fail_mirror(day=DAY, identity=IDENTITY, reason="provider error")
        assert failed.status == mirror.STATUS_NEEDS_REVIEW
        out = mirror.begin_mirror(day=DAY, identity=IDENTITY)
        assert out.action == mirror.NEEDS_REVIEW
        assert mirror.excluded_task_ids() == ()

    def test_different_day_starts_clean(self):
        mirror.complete_mirror(day=DAY, identity=IDENTITY, task_id="t1")
        out = mirror.begin_mirror(day="2026-10-14", identity=IDENTITY)
        assert out.action == mirror.BEGIN

    def test_distinct_identities_do_not_collide(self):
        mirror.complete_mirror(day=DAY, identity=IDENTITY, task_id="t1")
        other = "capacities:space-1:RootTask:object-2"
        out = mirror.begin_mirror(day=DAY, identity=other)
        assert out.action == mirror.BEGIN
        assert set(mirror.excluded_task_ids()) == {"t1"}


class TestValidation:
    def test_bad_day_rejected(self):
        with pytest.raises(mirror.MirrorStoreValidationError):
            mirror.begin_mirror(day="not-a-day", identity=IDENTITY)

    def test_empty_identity_rejected(self):
        with pytest.raises(mirror.MirrorStoreValidationError):
            mirror.begin_mirror(day=DAY, identity="   ")

    def test_malformed_store_read_fails_closed(self):
        path = mirror.store_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("garbage", encoding="utf-8")
        with pytest.raises(mirror.MirrorStoreFormatError):
            mirror.load_mirror()

    def test_unknown_version_fails_closed(self):
        path = mirror.store_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"version": 99, "receipts": {}, "excluded_task_ids": []}),
            encoding="utf-8",
        )
        with pytest.raises(mirror.MirrorStoreFormatError):
            mirror.excluded_task_ids()

    def test_unknown_status_fails_closed(self):
        key = f"{DAY}::{IDENTITY}"
        path = mirror.store_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({
                "version": 1,
                "receipts": {key: {
                    "day": DAY, "identity": IDENTITY, "status": "weird",
                    "task_id": None, "reason": None, "updated_at": None,
                }},
                "excluded_task_ids": [],
            }),
            encoding="utf-8",
        )
        with pytest.raises(mirror.MirrorStoreFormatError):
            mirror.load_mirror()

    def test_failed_write_does_not_corrupt_bytes(self):
        path = mirror.store_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("garbage", encoding="utf-8")
        with pytest.raises(mirror.MirrorStoreError):
            mirror.begin_mirror(day=DAY, identity=IDENTITY)
        assert path.read_text(encoding="utf-8") == "garbage"
