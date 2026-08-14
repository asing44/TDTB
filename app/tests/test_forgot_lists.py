"""test_forgot_lists.py — T6 (allocator rewrite): forgot-strip inputs.

Locked decision 8 wants ``unassigned_candidates`` + ``stale_assigned`` on the
digest at LOAD; locked decision 4 forbids a new billed call. Those two
together mean the lists are DERIVED, deterministically, from signals already
on a digest row plus T1's deferral memory — the identically-shaped lists on
the billed AuditReport are a separate, unused path here.
"""

from __future__ import annotations

from datetime import date

import main


TODAY = date(2026, 7, 26)
ORDER = ["urgency", "overdue", "deadline", "staleness", "summit"]


def item(name: str, **kw) -> dict:
    base = {"name": name, "path": f"50 - Operations/{name}.md", "urgency": 2}
    base.update(kw)
    return base


def candidates(pool, assigned=(), bias=None):
    d = main.build_digest(list(pool), list(assigned), TODAY, ORDER, bias=bias)
    return d["unassigned_candidates"]


def stale(assigned, bias=None):
    d = main.build_digest([], list(assigned), TODAY, ORDER, bias=bias)
    return d["stale_assigned"]


# ---------------------------------------------------------------------------
# unassigned_candidates
# ---------------------------------------------------------------------------

class TestUnassignedCandidates:
    def test_quiet_pool_yields_nothing(self):
        assert candidates([item("A"), item("B")]) == []

    def test_overdue_is_a_candidate(self):
        rows = candidates([item("Roof", deadline="2026-07-20")])
        assert rows[0]["name"] == "Roof"
        assert rows[0]["reason"] == "deadline 2026-07-20 has passed"

    def test_due_today_is_a_candidate(self):
        rows = candidates([item("Roof", deadline="2026-07-26")])
        assert rows[0]["reason"] == "due today and unassigned"

    def test_future_deadline_is_not(self):
        assert candidates([item("Roof", deadline="2026-08-30")]) == []

    def test_four_crit_is_a_candidate(self):
        rows = candidates([item("Roof", urgency=4)])
        assert rows[0]["reason"] == "4-crit and unassigned"

    def test_urgency_three_is_not(self):
        assert candidates([item("Roof", urgency=3)]) == []

    def test_deferred_is_a_candidate(self):
        rows = candidates([item("Roof")],
                          bias={"path:50 - Operations/Roof.md": 1})
        assert rows[0]["reason"] == "deferred recently and still not scheduled"

    def test_overdue_reason_wins_over_deferral(self):
        rows = candidates([item("Roof", deadline="2026-07-01", urgency=4)],
                          bias={"path:50 - Operations/Roof.md": 2})
        assert "has passed" in rows[0]["reason"]

    def test_capped_at_five(self):
        pool = [item(f"Item{i}", urgency=4) for i in range(9)]
        assert len(candidates(pool)) == main.FORGOT_LIST_CAP

    def test_assigned_rows_never_appear_as_candidates(self):
        rows = candidates([item("Roof", urgency=4)], assigned=[item("Roof", urgency=4)])
        assert rows == []

    def test_order_follows_the_digest_ranking(self):
        pool = [item("Low", urgency=4), item("Urgent", deadline="2026-07-01", urgency=4)]
        assert [r["name"] for r in candidates(pool)] == ["Urgent", "Low"]

    def test_rows_carry_the_audit_report_shape(self):
        row = candidates([item("Roof", urgency=4)])[0]
        assert set(row) == {"name", "path", "reason"}

    def test_reasons_stay_inside_the_140_char_contract(self):
        pool = [item("R" * 200, urgency=4, deadline="2026-07-01")]
        assert all(len(r["reason"]) <= 140 for r in candidates(pool))

    def test_malformed_deadline_is_not_a_signal(self):
        assert candidates([item("Roof", deadline="not-a-date")]) == []

    def test_malformed_urgency_is_not_a_signal(self):
        assert candidates([item("Roof", urgency="high")]) == []


# ---------------------------------------------------------------------------
# stale_assigned
# ---------------------------------------------------------------------------

class TestStaleAssigned:
    def test_plain_assigned_row_is_not_stale(self):
        assert stale([item("Roof")]) == []

    def test_four_crit_alone_is_not_stale(self):
        """Being urgent is why it IS assigned — not evidence it's stuck."""
        assert stale([item("Roof", urgency=4)]) == []

    def test_passed_deadline_is_stale(self):
        rows = stale([item("Roof", deadline="2026-07-20")])
        assert rows[0]["reason"] == "assigned but deadline 2026-07-20 has passed"

    def test_deferred_yet_still_assigned_is_stale(self):
        rows = stale([item("Roof")], bias={"path:50 - Operations/Roof.md": 1})
        assert rows[0]["reason"] == "assigned but deferred recently"

    def test_due_today_and_assigned_is_not_stale(self):
        assert stale([item("Roof", deadline="2026-07-26")]) == []

    def test_capped_at_five(self):
        rows = stale([item(f"I{i}", deadline="2026-07-01") for i in range(9)])
        assert len(rows) == main.FORGOT_LIST_CAP


class TestDigestContract:
    def test_both_keys_always_present(self):
        d = main.build_digest([], [], TODAY, ORDER)
        assert d["unassigned_candidates"] == []
        assert d["stale_assigned"] == []

    def test_ignore_list_filtering_applies_first(self):
        d = main.build_digest(
            [item("Roof", urgency=4)], [], TODAY, ORDER,
            ignore={"todoist_ids": set(), "paths": {"50 - Operations/Roof.md"},
                    "names": set()})
        assert d["unassigned_candidates"] == []
