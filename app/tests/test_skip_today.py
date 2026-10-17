"""test_skip_today.py — durable Skip today / Restore today (slice B1).

"Skip today" reuses the runstate ``drop_from_plan`` verb (date-scoped, honored
by plan-inputs / digest / commit). "Restore today" is the new ``restore_today``
verb addressed by identity through the ``runstate.undrop`` step: dropped rows
are absent from the digest index (it is built from the filtered digest), so the
verb resolves from ``runstate.read_dropped`` instead.

Both verbs are runstate-only — no Todoist / Calendar / Capacities client is
needed, so these tests inject none.

Proofs: undo of a drop removes ONLY its own identity (a later Skip survives);
restore removes only its identity and its undo re-adds the saved entry;
snapshot fields persist while legacy entries keep working; an unknown identity
is a clean refusal; tomorrow sees an empty dropped list.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

import runstate
import runtime_actions as ra

TODAY = date(2026, 8, 13)
TOMORROW = date(2026, 8, 14)


def two_item_manifest() -> list[dict]:
    """Two committed Todoist rows with distinct identities and durations."""
    return [
        {"step": "B", "system": "todoist", "action": "schedule", "name": "Alpha",
         "id_or_path": "task-a", "time": "10:00", "duration_min": 30,
         "routing": "\u2014"},
        {"step": "B", "system": "todoist", "action": "schedule", "name": "Beta",
         "id_or_path": "task-b", "time": "11:00", "duration_min": 45,
         "routing": "\u2014"},
    ]


@pytest.fixture()
def plan(tmp_path: Path) -> Path:
    runstate.write_runstate(
        TODAY, runstate.build_runstate({"plan_manifest": two_item_manifest()}))
    return tmp_path


def _identities(day: date = TODAY) -> list[str]:
    return [str(d.get("identity")) for d in runstate.read_dropped(day)]


# ---------------------------------------------------------------------------
# Verb catalogue
# ---------------------------------------------------------------------------

def test_restore_today_is_a_known_verb():
    assert "restore_today" in ra.VERBS
    assert "restore_today" in ra.STAGING_VERBS
    # the post-commit event-delete verb is untouched
    assert "skip_today" in ra.VERBS


# ---------------------------------------------------------------------------
# Snapshot fields on the dropped entry
# ---------------------------------------------------------------------------

class TestDropSnapshot:
    def test_drop_persists_optional_snapshot_fields(self, plan: Path):
        action = ra.apply_action(TODAY, "drop_from_plan", "Alpha")
        assert action["status"] == "applied"
        entry = runstate.read_dropped(TODAY)[0]
        assert entry["identity"] == "todoist:task-a"
        assert entry["source"] == "todoist"
        assert entry["minutes"] == 30

    def test_legacy_entry_without_snapshot_still_reads_and_restores(self, plan: Path):
        runstate.update_runstate(TODAY, {"dropped": [
            {"identity": "todoist:legacy", "name": "Legacy",
             "dropped_at": "2026-08-13T00:00:00Z"},
        ]})
        assert _identities() == ["todoist:legacy"]
        action = ra.apply_action(TODAY, "restore_today", "todoist:legacy")
        assert action["status"] == "applied"
        assert runstate.read_dropped(TODAY) == []

    def test_snapshot_key_names_are_contained(self, plan: Path):
        # A cap: the snapshot never invents extra entry keys.
        ra.apply_action(TODAY, "drop_from_plan", "Alpha")
        assert set(runstate.read_dropped(TODAY)[0]) <= {
            "identity", "name", "dropped_at", "source", "kind", "minutes"}


# ---------------------------------------------------------------------------
# Reverse of drop: identity-scoped, never a whole-list restore
# ---------------------------------------------------------------------------

class TestReverseDropIsIdentityScoped:
    def test_undo_of_first_drop_leaves_a_later_drop(self, plan: Path):
        first = ra.apply_action(TODAY, "drop_from_plan", "Alpha")
        ra.apply_action(TODAY, "drop_from_plan", "Beta")
        ra.undo_action(TODAY, first["id"])
        assert _identities() == ["todoist:task-b"]

    def test_existing_drop_journal_still_undoes_to_empty(self, plan: Path):
        action = ra.apply_action(TODAY, "drop_from_plan", "Alpha")
        assert action["verb"] == "drop_from_plan"
        ra.undo_action(TODAY, action["id"])
        assert runstate.read_dropped(TODAY) == []

    def test_drop_is_idempotent_per_date(self, plan: Path):
        ra.apply_action(TODAY, "drop_from_plan", "Alpha")
        dup = ra.apply_action(TODAY, "drop_from_plan", "Alpha")
        assert dup.get("duplicate") is True
        assert len(runstate.read_dropped(TODAY)) == 1


# ---------------------------------------------------------------------------
# Restore today: identity-addressed, identity-scoped, journaled, reversible
# ---------------------------------------------------------------------------

class TestRestoreToday:
    def test_restore_removes_only_its_identity(self, plan: Path):
        ra.apply_action(TODAY, "drop_from_plan", "Alpha")
        ra.apply_action(TODAY, "drop_from_plan", "Beta")
        action = ra.apply_action(TODAY, "restore_today", "todoist:task-a")
        assert action["status"] == "applied"
        assert _identities() == ["todoist:task-b"]

    def test_undo_restore_re_adds_the_saved_entry_with_snapshot(self, plan: Path):
        ra.apply_action(TODAY, "drop_from_plan", "Alpha")
        action = ra.apply_action(TODAY, "restore_today", "todoist:task-a")
        ra.undo_action(TODAY, action["id"])
        entry = runstate.read_dropped(TODAY)[0]
        assert entry["identity"] == "todoist:task-a"
        assert entry["source"] == "todoist"
        assert entry["minutes"] == 30

    def test_restore_unknown_identity_is_a_clean_refusal(self, plan: Path):
        with pytest.raises(ra.RuntimeActionError):
            ra.apply_action(TODAY, "restore_today", "todoist:missing")
        assert ra.load_journal(TODAY)["actions"] == []

    def test_restore_is_journaled_and_idempotent(self, plan: Path):
        ra.apply_action(TODAY, "drop_from_plan", "Alpha")
        first = ra.apply_action(TODAY, "restore_today", "todoist:task-a")
        again = ra.apply_action(TODAY, "restore_today", "todoist:task-a")
        assert again.get("duplicate") is True
        assert again["id"] == first["id"]
        assert runstate.read_dropped(TODAY) == []

    def test_restore_plans_a_runstate_only_step(self):
        steps = ra.plan_steps(
            "restore_today",
            {"phase": "dropped", "identity": "todoist:task-a", "name": "Alpha"},
            {}, TODAY)
        assert [s["kind"] for s in steps] == ["runstate.undrop"]
        assert all(s.get("system") == "runstate" for s in steps)


# ---------------------------------------------------------------------------
# Date scoping
# ---------------------------------------------------------------------------

class TestDateScoping:
    def test_tomorrow_sees_an_empty_dropped_list(self, plan: Path):
        ra.apply_action(TODAY, "drop_from_plan", "Alpha")
        assert runstate.read_dropped(TOMORROW) == []

    def test_restore_tomorrow_refuses(self, plan: Path):
        ra.apply_action(TODAY, "drop_from_plan", "Alpha")
        with pytest.raises(ra.RuntimeActionError):
            ra.apply_action(TOMORROW, "restore_today", "todoist:task-a")
