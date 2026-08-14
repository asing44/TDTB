"""test_deferrals.py — T1 defer-with-memory (allocator rewrite).

Covers the rolling deferral store (identity keys, atomic write, corrupt-file
tolerance), the decay/expiry policy, and the `rank_pool` bias it feeds. No
network, no live vault — tmp_path only.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

import deferrals
import main
import runstate
import runtime_actions as ra


TODAY = date(2026, 7, 26)
ORDER = ["urgency", "overdue", "deadline", "staleness", "summit"]


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    (tmp_path / "00 - META" / "Cache").mkdir(parents=True)
    return tmp_path


def item(name: str, **kw) -> dict:
    base = {"name": name, "path": f"50 - Operations/{name}.md", "urgency": 2}
    base.update(kw)
    return base


# ---------------------------------------------------------------------------
# Identity keys
# ---------------------------------------------------------------------------

class TestKeys:
    def test_path_wins_over_todoist_and_name(self):
        assert deferrals.deferral_key(
            name="A", path="p/a.md", todoist_id="99") == "path:p/a.md"

    def test_todoist_when_no_path(self):
        assert deferrals.deferral_key(name="A", todoist_id="99") == "todoist:99"

    def test_name_casefolded_last_resort(self):
        assert deferrals.deferral_key(name="Mow The Lawn") == "name:mow the lawn"

    def test_blank_identity_raises(self):
        with pytest.raises(ValueError):
            deferrals.deferral_key(name="", path="", todoist_id="")

    def test_key_for_item_reads_the_pool_item_shape(self):
        assert deferrals.key_for_item(item("Roof")) == "path:50 - Operations/Roof.md"
        assert deferrals.key_for_item(
            {"name": "Call", "todoist_id": "7"}) == "todoist:7"


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------

class TestStore:
    def test_missing_file_loads_empty(self, vault: Path):
        assert deferrals.load_deferrals(vault) == {}

    def test_record_creates_entry_with_count_one(self, vault: Path):
        deferrals.record_deferral(vault, item("Roof"), TODAY)
        store = deferrals.load_deferrals(vault)
        entry = store["path:50 - Operations/Roof.md"]
        assert entry["count"] == 1
        assert entry["last_deferred"] == "2026-07-26"
        assert entry["name"] == "Roof"

    def test_repeat_same_day_still_increments(self, vault: Path):
        deferrals.record_deferral(vault, item("Roof"), TODAY)
        deferrals.record_deferral(vault, item("Roof"), TODAY)
        entry = deferrals.load_deferrals(vault)["path:50 - Operations/Roof.md"]
        assert entry["count"] == 2

    def test_later_day_updates_last_deferred(self, vault: Path):
        deferrals.record_deferral(vault, item("Roof"), date(2026, 7, 20))
        deferrals.record_deferral(vault, item("Roof"), TODAY)
        entry = deferrals.load_deferrals(vault)["path:50 - Operations/Roof.md"]
        assert entry["count"] == 2
        assert entry["last_deferred"] == "2026-07-26"

    def test_file_lands_at_the_cache_dir_path(self, vault: Path):
        deferrals.record_deferral(vault, item("Roof"), TODAY)
        assert (vault / deferrals.DEFERRALS_REL_PATH).is_file()

    def test_no_tmp_file_left_behind(self, vault: Path):
        deferrals.record_deferral(vault, item("Roof"), TODAY)
        assert not list((vault / "00 - META" / "Cache").glob("*.tmp"))

    def test_corrupt_file_degrades_to_empty(self, vault: Path):
        path = vault / deferrals.DEFERRALS_REL_PATH
        path.write_text("{not json", encoding="utf-8")
        assert deferrals.load_deferrals(vault) == {}

    def test_wrong_shape_degrades_to_empty(self, vault: Path):
        (vault / deferrals.DEFERRALS_REL_PATH).write_text(
            json.dumps([1, 2, 3]), encoding="utf-8")
        assert deferrals.load_deferrals(vault) == {}

    def test_expired_entries_pruned_on_load(self, vault: Path):
        deferrals.record_deferral(vault, item("Old"), date(2026, 6, 1))
        deferrals.record_deferral(vault, item("Fresh"), TODAY)
        store = deferrals.load_deferrals(vault, today=TODAY)
        assert "path:50 - Operations/Fresh.md" in store
        assert "path:50 - Operations/Old.md" not in store

    def test_load_without_today_does_not_prune(self, vault: Path):
        deferrals.record_deferral(vault, item("Old"), date(2026, 6, 1))
        assert len(deferrals.load_deferrals(vault)) == 1

    def test_set_entry_restores_exact_prior_state(self, vault: Path):
        deferrals.record_deferral(vault, item("Roof"), TODAY)
        key = "path:50 - Operations/Roof.md"
        before = deferrals.load_deferrals(vault)[key]
        deferrals.record_deferral(vault, item("Roof"), TODAY)
        deferrals.set_entry(vault, key, before)
        assert deferrals.load_deferrals(vault)[key] == before

    def test_set_entry_none_removes(self, vault: Path):
        deferrals.record_deferral(vault, item("Roof"), TODAY)
        deferrals.set_entry(vault, "path:50 - Operations/Roof.md", None)
        assert deferrals.load_deferrals(vault) == {}


# ---------------------------------------------------------------------------
# Decay / expiry policy
# ---------------------------------------------------------------------------

class TestBias:
    def test_deferred_yesterday_biases_up(self):
        e = {"count": 1, "last_deferred": "2026-07-25"}
        assert deferrals.bias_for(e, TODAY) == 1

    def test_bias_caps_at_max(self):
        e = {"count": 99, "last_deferred": "2026-07-25"}
        assert deferrals.bias_for(e, TODAY) == deferrals.MAX_BIAS

    def test_bias_decays_one_step_per_week(self):
        e = {"count": 2, "last_deferred": "2026-07-18"}  # 8 days
        assert deferrals.bias_for(e, TODAY) == 1

    def test_bias_zero_past_ttl(self):
        e = {"count": 5, "last_deferred": "2026-07-01"}  # 25 days > TTL
        assert deferrals.bias_for(e, TODAY) == 0

    def test_future_date_treated_as_today(self):
        e = {"count": 1, "last_deferred": "2026-08-01"}
        assert deferrals.bias_for(e, TODAY) == 1

    def test_unparseable_date_is_zero(self):
        assert deferrals.bias_for({"count": 3, "last_deferred": "nope"}, TODAY) == 0

    def test_missing_count_is_zero(self):
        assert deferrals.bias_for({"last_deferred": "2026-07-25"}, TODAY) == 0

    def test_bias_map_keys_by_identity(self, vault: Path):
        deferrals.record_deferral(vault, item("Roof"), date(2026, 7, 25))
        assert deferrals.bias_map(vault, TODAY) == {
            "path:50 - Operations/Roof.md": 1}


# ---------------------------------------------------------------------------
# rank_pool bias — the locked effect: deferred yesterday ⇒ ranks higher today
# ---------------------------------------------------------------------------

class TestRankPoolBias:
    def test_no_bias_map_is_unchanged_ordering(self):
        pool = [item("B", urgency=3), item("A", urgency=4)]
        assert [i["name"] for i in main.rank_pool(pool, TODAY, ORDER)] == ["A", "B"]

    def test_deferred_item_outranks_identical_peer(self):
        pool = [item("A"), item("B")]
        biased = main.rank_pool(
            pool, TODAY, ORDER, bias={"path:50 - Operations/B.md": 1})
        assert [i["name"] for i in biased] == ["B", "A"]

    def test_bias_lifts_across_one_urgency_tier(self):
        pool = [item("High", urgency=3), item("Low", urgency=2)]
        biased = main.rank_pool(
            pool, TODAY, ORDER, bias={"path:50 - Operations/Low.md": 2})
        assert [i["name"] for i in biased] == ["Low", "High"]

    def test_bias_cannot_invert_a_two_tier_gap_beyond_max(self):
        pool = [item("Crit", urgency=4), item("Meh", urgency=1)]
        biased = main.rank_pool(
            pool, TODAY, ORDER, bias={"path:50 - Operations/Meh.md": deferrals.MAX_BIAS})
        assert [i["name"] for i in biased] == ["Crit", "Meh"]

    def test_bias_still_applies_when_urgency_not_in_sort_order(self):
        pool = [item("A"), item("B")]
        biased = main.rank_pool(
            pool, TODAY, ["deadline"], bias={"path:50 - Operations/B.md": 1})
        assert [i["name"] for i in biased] == ["B", "A"]

    def test_ranking_is_deterministic_under_input_shuffle(self):
        bias = {"path:50 - Operations/B.md": 1}
        first = main.rank_pool([item("A"), item("B"), item("C")], TODAY, ORDER, bias=bias)
        second = main.rank_pool([item("C"), item("B"), item("A")], TODAY, ORDER, bias=bias)
        assert [i["name"] for i in first] == [i["name"] for i in second]

    def test_build_digest_accepts_and_applies_bias(self):
        digest = main.build_digest(
            [item("A"), item("B")], [], TODAY, ORDER,
            bias={"path:50 - Operations/B.md": 1})
        assert [i["name"] for i in digest["suggested"]] == ["B", "A"]


# ---------------------------------------------------------------------------
# The `defer` runtime verb — record + best-effort un-schedule, journaled
# ---------------------------------------------------------------------------

class FakeTodoist:
    def __init__(self, tasks: dict[str, dict]) -> None:
        self.tasks = tasks
        self.calls: list[tuple] = []

    def get_task(self, task_id: str) -> dict:
        return dict(self.tasks[task_id])

    def reschedule_task(self, task_id: str, due_string: str) -> dict:
        self.calls.append(("reschedule", task_id, due_string))
        return self.tasks[task_id]

    def reschedule_task_datetime(self, task_id: str, due_datetime: str) -> dict:
        self.calls.append(("reschedule_dt", task_id, due_datetime))
        return self.tasks[task_id]


def _vault_with_manifest(vault: Path, rows: list[dict]) -> Path:
    runstate.write_runstate(vault, TODAY, runstate.build_runstate(
        {"plan_manifest": rows}))
    return vault


class TestDeferVerb:
    def test_defer_is_a_registered_verb(self):
        assert "defer" in ra.VERBS

    def test_defer_records_and_journals(self, vault: Path):
        _vault_with_manifest(vault, [
            {"name": "Roof", "system": "vault", "id_or_path": "50 - Operations/Roof.md"}])
        action = ra.apply_action(vault, TODAY, "defer", "Roof")
        assert action["status"] == "applied"
        store = deferrals.load_deferrals(vault)
        assert store["path:50 - Operations/Roof.md"]["count"] == 1

    def test_defer_needs_no_scheduled_artifacts(self, vault: Path):
        """Unlike remove_from_today, defer never refuses an unplaced item —
        this is what makes it legal from the staging queue."""
        _vault_with_manifest(vault, [
            {"name": "Roof", "system": "vault", "id_or_path": "50 - Operations/Roof.md"}])
        steps = ra.plan_steps("defer", {"name": "Roof"}, {}, TODAY)
        assert [s["kind"] for s in steps] == ["deferrals.record"]

    def test_defer_also_clears_todoist_time_when_scheduled(self, vault: Path):
        _vault_with_manifest(vault, [
            {"name": "Call", "system": "todoist", "id_or_path": "7"}])
        todoist = FakeTodoist({"7": {"id": "7", "content": "Call",
                                     "due": {"date": "2026-07-26", "string": "today"}}})
        action = ra.apply_action(vault, TODAY, "defer", "Call", todoist=todoist)
        assert action["status"] == "applied"
        assert ("reschedule", "7", "today") in todoist.calls
        assert deferrals.load_deferrals(vault)["todoist:7"]["count"] == 1

    def test_defer_honours_the_t27_recurring_guard(self, vault: Path):
        _vault_with_manifest(vault, [
            {"name": "Call", "system": "todoist", "id_or_path": "7"}])
        todoist = FakeTodoist({"7": {"id": "7", "content": "Call",
                                     "due": {"string": "every day",
                                             "is_recurring": True}}})
        ra.apply_action(vault, TODAY, "defer", "Call", todoist=todoist)
        assert todoist.calls == []  # no due write — recurrence preserved

    def test_undo_defer_restores_prior_entry(self, vault: Path):
        _vault_with_manifest(vault, [
            {"name": "Roof", "system": "vault", "id_or_path": "50 - Operations/Roof.md"}])
        ra.apply_action(vault, TODAY, "defer", "Roof")
        second = ra.apply_action(vault, TODAY, "defer", "Roof",
                                 args={"nonce": 1})
        assert deferrals.load_deferrals(vault)["path:50 - Operations/Roof.md"]["count"] == 2
        ra.undo_action(vault, TODAY, second["id"])
        assert deferrals.load_deferrals(vault)["path:50 - Operations/Roof.md"]["count"] == 1

    def test_undo_first_defer_removes_the_entry(self, vault: Path):
        _vault_with_manifest(vault, [
            {"name": "Roof", "system": "vault", "id_or_path": "50 - Operations/Roof.md"}])
        action = ra.apply_action(vault, TODAY, "defer", "Roof")
        ra.undo_action(vault, TODAY, action["id"])
        assert deferrals.load_deferrals(vault) == {}

    def test_defer_is_idempotent_per_key(self, vault: Path):
        _vault_with_manifest(vault, [
            {"name": "Roof", "system": "vault", "id_or_path": "50 - Operations/Roof.md"}])
        ra.apply_action(vault, TODAY, "defer", "Roof")
        dup = ra.apply_action(vault, TODAY, "defer", "Roof")
        assert dup.get("duplicate") is True
        assert deferrals.load_deferrals(vault)["path:50 - Operations/Roof.md"]["count"] == 1

    def test_defer_feeds_the_next_days_ranking(self, vault: Path):
        _vault_with_manifest(vault, [
            {"name": "Roof", "system": "vault", "id_or_path": "50 - Operations/Roof.md"}])
        ra.apply_action(vault, TODAY, "defer", "Roof")
        tomorrow = date(2026, 7, 27)
        pool = [item("Roof"), item("Other")]
        ranked = main.rank_pool(pool, tomorrow, ORDER,
                                bias=deferrals.bias_map(vault, tomorrow))
        assert [i["name"] for i in ranked] == ["Roof", "Other"]
