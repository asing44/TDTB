"""test_skip_cycle_safety.py — Drop/Restore cycle identity and idempotency.

Skip today (``drop_from_plan``) and Restore today (``restore_today``) are two
transitions of ONE identity's in-day membership. Two failure classes this file
pins down:

1. Idempotency must follow the EFFECT of the earlier action, not a constant
   ``date|verb|target|args`` key. ``drop -> restore -> drop`` is a new request
   and must journal a new, correct drop; only a genuine retry of a request that
   is still in force may answer ``duplicate``.
2. A later cycle owns the identity. Undo of an OLDER drop (single action or
   batch) must not delete the entry a LATER drop created, and undo of an OLDER
   restore must not resurrect a stale entry after later cycles. Ownership is a
   persisted per-identity witness (monotonic counter + owner token), so a
   presence-only check can never pass for another generation.

Proofs: repeated cycles leave the latest live membership correct and the
journal with one action per request; genuine retries stay idempotent; stale
single and batch undos report a conflict/refusal and preserve the live entry;
a different identity is untouched; a legacy journal step either still reverses
its own single transition or fails closed instead of clobbering.

No network, no real writes — the app-home fixture (conftest) isolates every
store.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

import runstate
import runtime_actions as ra
import runtime_batches as rb

TODAY = date(2026, 8, 13)
ALPHA = "todoist:task-a"
BETA = "todoist:task-b"


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


def _dropped(day: date = TODAY) -> list[dict]:
    return runstate.read_dropped(day)


def _identities(day: date = TODAY) -> list[str]:
    return [str(d.get("identity")) for d in _dropped(day)]


def _live_owner(identity: str = ALPHA) -> str:
    cycles = runstate.read_runstate(TODAY)["drop_cycles"]
    return str(cycles[identity]["owner"])


def _drop_steps(action: dict) -> list[dict]:
    return [s for s in action.get("steps") or []
            if s.get("kind") == "runstate.drop"]


def _actions(verb: str | None = None) -> list[dict]:
    actions = ra.load_journal(TODAY)["actions"]
    return [a for a in actions if verb is None or a.get("verb") == verb]


def _append_legacy_action(action: dict) -> None:
    journal = ra.load_journal(TODAY)
    journal["actions"].append(action)
    ra._write_journal(TODAY, journal)


def _legacy_action(action_id: str, verb: str, step: dict) -> dict:
    return {
        "id": action_id, "date": str(TODAY), "verb": verb,
        "target": {"name": "Alpha"}, "args": {},
        "idempotency_key": f"legacy-{action_id}",
        "status": "applied", "steps": [dict(step, applied=True)],
        "created_at": "2026-08-13T00:00:00Z",
        "finished_at": "2026-08-13T00:00:00Z",
        "undone_at": None, "error": None,
    }


# ---------------------------------------------------------------------------
# Repeated cycles: a new request journals a new, correct action
# ---------------------------------------------------------------------------

class TestRepeatedCycles:
    def test_drop_restore_drop_creates_a_new_action_and_one_live_entry(
        self, plan: Path,
    ):
        first = ra.apply_action(TODAY, "drop_from_plan", "Alpha")
        ra.apply_action(TODAY, "restore_today", ALPHA)
        second = ra.apply_action(TODAY, "drop_from_plan", "Alpha")

        assert second.get("duplicate") is not True
        assert second["id"] != first["id"]
        assert second["status"] == "applied"
        assert _identities() == [ALPHA]
        assert len(_actions("drop_from_plan")) == 2

    def test_restore_drop_restore_creates_a_new_restore_action(self, plan: Path):
        ra.apply_action(TODAY, "drop_from_plan", "Alpha")
        first = ra.apply_action(TODAY, "restore_today", ALPHA)
        ra.apply_action(TODAY, "drop_from_plan", "Alpha")
        second = ra.apply_action(TODAY, "restore_today", ALPHA)

        assert second.get("duplicate") is not True
        assert second["id"] != first["id"]
        assert second["status"] == "applied"
        assert _identities() == []
        assert len(_actions("restore_today")) == 2

    def test_genuine_retries_stay_idempotent(self, plan: Path):
        first = ra.apply_action(TODAY, "drop_from_plan", "Alpha")
        again = ra.apply_action(TODAY, "drop_from_plan", "Alpha")
        assert again.get("duplicate") is True
        assert again["id"] == first["id"]
        assert _identities() == [ALPHA]

        restored = ra.apply_action(TODAY, "restore_today", ALPHA)
        restore_again = ra.apply_action(TODAY, "restore_today", ALPHA)
        assert restore_again.get("duplicate") is True
        assert restore_again["id"] == restored["id"]
        assert _identities() == []


# ---------------------------------------------------------------------------
# Stale undo: an older cycle must never reverse a newer generation
# ---------------------------------------------------------------------------

class TestStaleUndoIsRefused:
    def test_undo_of_older_single_skip_after_reskip_preserves_the_later_entry(
        self, plan: Path,
    ):
        first = ra.apply_action(TODAY, "drop_from_plan", "Alpha")
        ra.apply_action(TODAY, "restore_today", ALPHA)
        later = ra.apply_action(TODAY, "drop_from_plan", "Alpha")

        with pytest.raises(ra.RuntimeActionError):
            ra.undo_action(TODAY, first["id"])

        assert _identities() == [ALPHA]
        assert _live_owner() == _drop_steps(later)[0]["after"]["owner"]
        # the live generation still reverses normally
        ra.undo_action(TODAY, later["id"])
        assert _identities() == []

    def test_step_conflict_is_generation_bound(self, plan: Path):
        ra.apply_action(TODAY, "drop_from_plan", "Alpha")
        older_step = _drop_steps(_actions("drop_from_plan")[0])[0]
        assert ra.step_conflict(older_step, TODAY) is None

        ra.apply_action(TODAY, "restore_today", ALPHA)
        ra.apply_action(TODAY, "drop_from_plan", "Alpha")

        assert ra.step_conflict(older_step, TODAY)

    def test_undo_of_older_batch_skip_after_reskip_reports_a_conflict(
        self, plan: Path,
    ):
        applied = rb.apply_batch(
            TODAY, [{"verb": "drop_from_plan", "target": "Alpha", "args": {}}])
        assert applied["results"][0]["status"] == "applied"

        ra.apply_action(TODAY, "restore_today", ALPHA)
        later = ra.apply_action(TODAY, "drop_from_plan", "Alpha")

        report = rb.undo_batch(TODAY, applied["batch_id"])

        assert report["reverted"] == []
        assert [c["target"] for c in report["conflicts"]] == ["Alpha"]
        assert _identities() == [ALPHA]
        assert _live_owner() == _drop_steps(later)[0]["after"]["owner"]

    def test_undo_of_older_restore_does_not_resurrect_a_stale_drop(
        self, plan: Path,
    ):
        ra.apply_action(TODAY, "drop_from_plan", "Alpha")
        older_restore = ra.apply_action(TODAY, "restore_today", ALPHA)
        later = ra.apply_action(TODAY, "drop_from_plan", "Alpha")

        with pytest.raises(ra.RuntimeActionError):
            ra.undo_action(TODAY, older_restore["id"])

        assert _identities() == [ALPHA]
        assert len(_dropped()) == 1
        assert _live_owner() == _drop_steps(later)[0]["after"]["owner"]

    def test_older_restore_undo_after_a_later_restore_cycle_is_refused(
        self, plan: Path,
    ):
        ra.apply_action(TODAY, "drop_from_plan", "Alpha")
        older_restore = ra.apply_action(TODAY, "restore_today", ALPHA)
        ra.apply_action(TODAY, "drop_from_plan", "Alpha")
        ra.apply_action(TODAY, "restore_today", ALPHA)

        with pytest.raises(ra.RuntimeActionError):
            ra.undo_action(TODAY, older_restore["id"])

        assert _identities() == []

    def test_later_different_identity_skip_is_untouched(self, plan: Path):
        first = ra.apply_action(TODAY, "drop_from_plan", "Alpha")
        ra.apply_action(TODAY, "restore_today", ALPHA)
        later = ra.apply_action(TODAY, "drop_from_plan", "Alpha")
        ra.apply_action(TODAY, "drop_from_plan", "Beta")

        with pytest.raises(ra.RuntimeActionError):
            ra.undo_action(TODAY, first["id"])

        assert set(_identities()) == {ALPHA, BETA}
        ra.undo_action(TODAY, later["id"])
        assert _identities() == [BETA]
        assert _live_owner(ALPHA) == ""


# ---------------------------------------------------------------------------
# Legacy journals: reverse the provable single transition, else fail closed
# ---------------------------------------------------------------------------

class TestLegacyJournals:
    def test_legacy_drop_step_is_refused_once_a_later_cycle_ran(self, plan: Path):
        ra.apply_action(TODAY, "drop_from_plan", "Alpha")
        ra.apply_action(TODAY, "restore_today", ALPHA)
        ra.apply_action(TODAY, "drop_from_plan", "Alpha")

        legacy_step = {"kind": "runstate.drop", "surface": "runstate",
                       "identity": ALPHA}
        assert ra.step_conflict(legacy_step, TODAY)

    def test_legacy_drop_journal_still_undoes_its_single_transition(
        self, plan: Path,
    ):
        runstate.update_runstate(TODAY, {"dropped": [
            {"identity": ALPHA, "name": "Alpha",
             "dropped_at": "2026-08-13T00:00:00Z"}]})
        _append_legacy_action(_legacy_action(
            "ra-legacy-drop", "drop_from_plan",
            {"kind": "runstate.drop", "surface": "runstate",
             "identity": ALPHA}))

        action = ra.undo_action(TODAY, "ra-legacy-drop")

        assert action["status"] == "undone"
        assert _identities() == []

    def test_legacy_restore_undo_does_not_resurrect_after_a_newer_cycle(
        self, plan: Path,
    ):
        ra.apply_action(TODAY, "drop_from_plan", "Alpha")
        ra.apply_action(TODAY, "restore_today", ALPHA)
        later = ra.apply_action(TODAY, "drop_from_plan", "Alpha")
        _append_legacy_action(_legacy_action(
            "ra-legacy-restore", "restore_today",
            {"kind": "runstate.undrop", "surface": "runstate",
             "identity": ALPHA,
             "before": {"entry": {"identity": ALPHA, "name": "Alpha",
                                  "dropped_at": "2026-08-13T00:00:00Z"}}}))

        with pytest.raises(ra.RuntimeActionError):
            ra.undo_action(TODAY, "ra-legacy-restore")

        assert _identities() == [ALPHA]
        assert _live_owner() == _drop_steps(later)[0]["after"]["owner"]
