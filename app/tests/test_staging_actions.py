"""test_staging_actions.py — T2 staging-phase runtime verbs (allocator rewrite).

complete / delete_permanent / defer resolve from today's ``digest_index``
before any commit exists; post-commit ``plan_manifest`` resolution is
unchanged and still wins; unknown items and placement-only verbs are refused.
V3: a staged vault-note row (``vault_path``) can no longer be completed,
assigned, unassigned or deleted; those verbs refuse it with a clear error.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import main
import runstate
import runtime_actions as ra


TODAY = date(2026, 7, 26)

DIGEST_INDEX = [
    {"name": "Roof", "todoist_id": "", "path": "50 - Operations/Roof.md"},
    {"name": "Call Vlad", "todoist_id": "7", "path": ""},
    {"name": "Both", "todoist_id": "9", "path": "50 - Operations/Both.md"},
]


class FakeTodoist:
    def __init__(self) -> None:
        self.tasks = {
            "7": {"id": "7", "content": "Call Vlad", "due": {"date": "2026-07-26"}},
            "9": {"id": "9", "content": "Both", "due": {"date": "2026-07-26"}},
        }
        self.calls: list[tuple] = []

    def get_task(self, task_id: str) -> dict:
        return dict(self.tasks[task_id])

    def close_task(self, task_id: str) -> None:
        self.calls.append(("close", task_id))

    def reopen_task(self, task_id: str) -> None:
        self.calls.append(("reopen", task_id))

    def delete_task(self, task_id: str) -> None:
        self.calls.append(("delete", task_id))

    def create_task(self, content: str, project_id: str | None = None, **fields) -> dict:
        self.calls.append(("create", content))
        return {"id": "new-1", "content": content}

    def reschedule_task(self, task_id: str, due_string: str) -> dict:
        self.calls.append(("reschedule", task_id, due_string))
        return self.tasks[task_id]

    def close(self) -> None:
        self.calls.append(("close_client",))


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    (tmp_path / "50 - Operations").mkdir(parents=True)
    for name in ("Roof", "Both"):
        (tmp_path / "50 - Operations" / f"{name}.md").write_text(
            f"---\ntype: [project]\nstatus: active\n---\n\n# {name}\n", encoding="utf-8")
    runstate.write_digest_index(tmp_path, TODAY, DIGEST_INDEX)
    # FEEDBACK-24: route verbs are gated on an explicit Day Setup confirm.
    runstate.write_runstate(tmp_path, TODAY,
                            runstate.build_runstate({"day_setup_confirmed": True}))
    return tmp_path


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------

class TestStagingResolution:
    def test_vault_row_resolves_to_a_staging_target(self, vault: Path):
        target = ra.resolve_target(vault, TODAY, "Roof")
        assert target["phase"] == "staging"
        assert target["vault_path"] == "50 - Operations/Roof.md"

    def test_todoist_row_resolves(self, vault: Path):
        assert ra.resolve_target(vault, TODAY, "Call Vlad")["todoist_id"] == "7"

    def test_both_surfaces_resolve_together(self, vault: Path):
        target = ra.resolve_target(vault, TODAY, "Both")
        assert target["todoist_id"] == "9"
        assert target["vault_path"] == "50 - Operations/Both.md"

    def test_unknown_item_is_refused(self, vault: Path):
        with pytest.raises(ra.RuntimeActionError, match="manifest or digest"):
            ra.resolve_target(vault, TODAY, "Never Heard Of It")

    def test_manifest_still_wins_when_present(self, vault: Path):
        runstate.update_runstate(vault, TODAY, {"plan_manifest": [
            {"name": "Roof", "system": "todoist", "id_or_path": "111"}]})
        target = ra.resolve_target(vault, TODAY, "Roof")
        assert target["phase"] == "committed"
        assert target["todoist_id"] == "111"
        assert "vault_path" not in target  # manifest row is authoritative

    def test_manifest_miss_falls_through_to_digest(self, vault: Path):
        runstate.update_runstate(vault, TODAY, {"plan_manifest": [
            {"name": "Something Else", "system": "todoist", "id_or_path": "111"}]})
        assert ra.resolve_target(vault, TODAY, "Roof")["phase"] == "staging"


# ---------------------------------------------------------------------------
# Verbs at the staging phase
# ---------------------------------------------------------------------------

class TestStagingVerbs:
    @pytest.mark.parametrize("verb", ["complete", "delete_permanent"])
    def test_a_staged_vault_note_is_refused_with_a_clear_error(self, vault: Path, verb: str):
        path = vault / "50 - Operations/Roof.md"
        before = path.read_text(encoding="utf-8")
        with pytest.raises(ra.RuntimeActionError, match="vault is retired"):
            ra.apply_action(vault, TODAY, verb, "Roof")
        assert path.read_text(encoding="utf-8") == before
        assert not (vault / ".trash").exists()
        assert ra.load_journal(vault, TODAY)["actions"] == []

    def test_defer_works_at_staging(self, vault: Path):
        import deferrals
        action = ra.apply_action(vault, TODAY, "defer", "Roof")
        assert action["status"] == "applied"
        assert deferrals.load_deferrals(vault)["path:50 - Operations/Roof.md"]["count"] == 1

    def test_staging_actions_are_journaled_like_any_other(self, vault: Path):
        ra.apply_action(vault, TODAY, "complete", "Call Vlad", todoist=FakeTodoist())
        journal = ra.load_journal(vault, TODAY)
        assert len(journal["actions"]) == 1
        assert journal["actions"][0]["target"]["phase"] == "staging"

    def test_staging_actions_stay_idempotent(self, vault: Path):
        todoist = FakeTodoist()
        ra.apply_action(vault, TODAY, "complete", "Call Vlad", todoist=todoist)
        dup = ra.apply_action(vault, TODAY, "complete", "Call Vlad", todoist=todoist)
        assert dup.get("duplicate") is True


class TestAssignVerb:
    """T9 forgot-strip's one-tap assign — the inverse of the signal that
    surfaced the row."""

    def test_assign_of_a_staged_vault_note_is_refused(self, vault: Path):
        path = vault / "50 - Operations/Roof.md"
        before = path.read_text(encoding="utf-8")
        with pytest.raises(ra.RuntimeActionError, match="vault is retired"):
            ra.apply_action(vault, TODAY, "assign", "Roof")
        assert path.read_text(encoding="utf-8") == before
        assert ra.load_journal(vault, TODAY)["actions"] == []

    def test_assign_pulls_a_todoist_row_to_today(self, vault: Path):
        todoist = FakeTodoist()
        ra.apply_action(vault, TODAY, "assign", "Call Vlad", todoist=todoist)
        assert ("reschedule", "7", "today") in todoist.calls

    def test_assign_honours_the_t27_recurring_guard(self, vault: Path):
        todoist = FakeTodoist()
        todoist.tasks["7"]["due"] = {"string": "every day", "is_recurring": True}
        ra.apply_action(vault, TODAY, "assign", "Call Vlad", todoist=todoist)
        assert todoist.calls == []

    def test_assign_of_a_todoist_row_is_undoable(self, vault: Path):
        todoist = FakeTodoist()
        action = ra.apply_action(vault, TODAY, "assign", "Call Vlad", todoist=todoist)
        undone = ra.undo_action(vault, TODAY, action["id"], todoist=todoist)
        assert undone["status"] == "undone"

    def test_assign_is_idempotent(self, vault: Path):
        todoist = FakeTodoist()
        ra.apply_action(vault, TODAY, "assign", "Call Vlad", todoist=todoist)
        dup = ra.apply_action(vault, TODAY, "assign", "Call Vlad", todoist=todoist)
        assert dup.get("duplicate") is True

    def test_assign_is_a_staging_verb(self):
        assert "assign" in ra.STAGING_VERBS
        assert "assign" in ra.VERBS


class TestPlacementOnlyVerbsRefuse:
    @pytest.mark.parametrize("verb,args", [
        ("skip_today", {}),
        ("remove_from_today", {}),
        ("duration_edit", {"blocks": 2}),
        ("move_resize", {"start": "09:00", "end": "10:00"}),
    ])
    def test_refused_before_commit(self, vault: Path, verb: str, args: dict):
        with pytest.raises(ra.RuntimeActionError, match="still staged"):
            ra.apply_action(vault, TODAY, verb, "Both", args=args,
                            todoist=FakeTodoist())

    def test_refusal_writes_no_journal_entry(self, vault: Path):
        with pytest.raises(ra.RuntimeActionError):
            ra.apply_action(vault, TODAY, "skip_today", "Both",
                            todoist=FakeTodoist())
        assert ra.load_journal(vault, TODAY)["actions"] == []

    def test_refusal_leaves_todoist_untouched(self, vault: Path):
        todoist = FakeTodoist()
        with pytest.raises(ra.RuntimeActionError):
            ra.apply_action(vault, TODAY, "remove_from_today", "Both",
                            todoist=todoist)
        assert todoist.calls == []


# ---------------------------------------------------------------------------
# Index builder + route
# ---------------------------------------------------------------------------

class TestDigestIndex:
    def test_indexes_both_surfaces(self):
        index = main.build_digest_index({
            "assigned": [{"name": "A", "path": "a.md"}],
            "suggested": [{"name": "B", "todoist_id": "2"}],
        })
        assert [r["name"] for r in index] == ["A", "B"]

    def test_drops_rows_with_no_identity(self):
        index = main.build_digest_index({"assigned": [{"name": "A"}], "suggested": []})
        assert index == []

    def test_drops_unnamed_rows(self):
        assert main.build_digest_index(
            {"assigned": [{"path": "a.md"}], "suggested": []}) == []

    def test_dedupes_identical_rows_across_surfaces(self):
        row = {"name": "A", "path": "a.md"}
        assert len(main.build_digest_index(
            {"assigned": [row], "suggested": [dict(row)]})) == 1

    def test_tolerates_a_malformed_row(self):
        assert main.build_digest_index(
            {"assigned": ["nope", {"name": "A", "path": "a.md"}],
             "suggested": []})[0]["name"] == "A"


class TestPlanInputsPersistsTheIndex:
    """The wiring T2 depends on: /plan-inputs must leave behind an index the
    later /runtime-actions call can resolve against."""

    def test_plan_inputs_writes_digest_index(self, tmp_path: Path,
                                             monkeypatch: pytest.MonkeyPatch):
        vault = tmp_path / "vault-root"
        (vault / "50 - Operations" / "Projects").mkdir(parents=True)
        (vault / "50 - Operations" / "Projects" / "Roof.md").write_text(
            "---\ntype: [project]\nstatus: active\nassigned: true\n---\n\n# Roof\n",
            encoding="utf-8")
        monkeypatch.setattr(main.gather, "effective_date", lambda _n: TODAY)
        client = TestClient(main.create_app(vault_root=vault))
        assert client.get("/plan-inputs").status_code == 200
        index = runstate.read_digest_index(vault, TODAY)
        assert any(r["path"].endswith("Roof.md") for r in index)

    def test_plan_inputs_does_not_materialise_the_runstate_note(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """The index has its own dated file on purpose: writing run-state from
        /plan-inputs would leave a skeleton note whose empty defaults later
        read back as user-confirmed day setup."""
        vault = tmp_path / "vault-root"
        vault.mkdir()
        monkeypatch.setattr(main.gather, "effective_date", lambda _n: TODAY)
        client = TestClient(main.create_app(vault_root=vault))
        assert client.get("/plan-inputs").json()["day_setup"] == {}
        assert not (vault / runstate.runstate_rel_path(TODAY)).exists()

    def test_index_survives_into_target_resolution(self, tmp_path: Path,
                                                   monkeypatch: pytest.MonkeyPatch):
        vault = tmp_path / "vault-root"
        (vault / "50 - Operations" / "Projects").mkdir(parents=True)
        (vault / "50 - Operations" / "Projects" / "Roof.md").write_text(
            "---\ntype: [project]\nstatus: active\nassigned: true\n---\n\n# Roof\n",
            encoding="utf-8")
        monkeypatch.setattr(main.gather, "effective_date", lambda _n: TODAY)
        client = TestClient(main.create_app(vault_root=vault))
        client.get("/plan-inputs")
        assert ra.resolve_target(vault, TODAY, "Roof")["phase"] == "staging"


class TestStagingRoute:
    @pytest.fixture
    def client(self, vault: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
        monkeypatch.setattr(main.gather, "effective_date", lambda _n: TODAY)
        app = main.create_app(vault_root=vault)
        app.state.build_commit_clients = lambda v, c: (FakeTodoist(), None)
        c = TestClient(app)
        c.headers.update({"X-TDTB-Token": app.state.token})
        return c

    def test_route_applies_a_staging_verb(self, client: TestClient):
        r = client.post("/runtime-actions",
                        json={"verb": "complete", "target": "Call Vlad"})
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "applied"

    def test_route_returns_422_for_a_removed_vault_verb(self, client: TestClient, vault: Path):
        r = client.post("/runtime-actions",
                        json={"verb": "complete", "target": "Roof"})
        assert r.status_code == 422
        assert "vault is retired" in r.json()["detail"]
        assert "status: active" in (vault / "50 - Operations/Roof.md").read_text(encoding="utf-8")

    def test_route_refuses_an_unknown_item_with_422(self, client: TestClient):
        r = client.post("/runtime-actions",
                        json={"verb": "complete", "target": "Ghost"})
        assert r.status_code == 422
        assert "manifest or digest" in r.json()["detail"]

    def test_route_refuses_a_placement_verb_pre_commit(self, client: TestClient):
        r = client.post("/runtime-actions",
                        json={"verb": "move_resize", "target": "Roof",
                              "args": {"start": "09:00", "end": "10:00"}})
        assert r.status_code == 422
        assert "still staged" in r.json()["detail"]


# ---------------------------------------------------------------------------
# T12d — the pseudo-path conflation (found by t12c_morning_rehearsal.py)
# ---------------------------------------------------------------------------

class TestTodoistPseudoPathIsNotAVaultPath:
    """`external_sources` gives every Todoist row a synthetic
    ``path: "todoist://<id>"``. `shadow.py` guards that string in two places;
    the T2 staging resolver did not, so it copied the pseudo-path into
    ``vault_path`` and every staging verb planned a vault write at
    ``<vault>/todoist://<id>``. On a real morning that closed the task, hit
    ENOENT, and rolled the close back — a visible failure on every Done or
    Delete against a Todoist row.

    The fixture above models Todoist rows with ``path: ""``, which production
    never emits; these build the index the way `main.build_digest_index` does,
    from a digest row.
    """

    @pytest.fixture
    def real_shaped_vault(self, tmp_path: Path) -> Path:
        index = main.build_digest_index({
            "assigned": [
                {"name": "Call Vlad", "todoist_id": "7", "path": "todoist://7",
                 "source": "todoist"},
            ],
        })
        runstate.write_digest_index(tmp_path, TODAY, index)
        return tmp_path

    def test_the_index_really_does_carry_the_pseudo_path(self, real_shaped_vault: Path):
        rows = runstate.read_digest_index(real_shaped_vault, TODAY)
        assert rows[0]["path"] == "todoist://7"

    def test_resolution_yields_no_vault_path(self, real_shaped_vault: Path):
        target = ra.resolve_target(real_shaped_vault, TODAY, "Call Vlad")
        assert target["todoist_id"] == "7"
        assert "vault_path" not in target, (
            f"the todoist:// pseudo-path leaked into vault_path: {target}")

    def test_complete_plans_only_a_todoist_step(self, real_shaped_vault: Path):
        target = ra.resolve_target(real_shaped_vault, TODAY, "Call Vlad")
        steps = ra.plan_steps("complete", target, {}, TODAY)
        assert [s["kind"] for s in steps] == ["todoist.close"]

    def test_assign_plans_only_a_todoist_step(self, real_shaped_vault: Path):
        """`assign` is the verb that actually failed on Adam's 2026-07-27
        morning — the forgot-strip's Assign button on a Todoist row. That day's
        runtime journal recorded it resolving to
        ``vault_path: 'todoist://6fgQFQmrfhVQ5c9X'`` and failing."""
        target = ra.resolve_target(real_shaped_vault, TODAY, "Call Vlad")
        steps = ra.plan_steps("assign", target, {}, TODAY)
        assert [s["kind"] for s in steps] == ["todoist.assign_today"]

    def test_delete_permanent_plans_only_a_todoist_step(self, real_shaped_vault: Path):
        target = ra.resolve_target(real_shaped_vault, TODAY, "Call Vlad")
        steps = ra.plan_steps("delete_permanent", target, {}, TODAY)
        assert all(s["surface"] == "todoist" for s in steps), steps

    def test_complete_of_a_todoist_row_applies(self, real_shaped_vault: Path):
        todoist = FakeTodoist()
        result = ra.apply_action(real_shaped_vault, TODAY, "complete", "Call Vlad",
                                 {}, todoist=todoist, store=None)
        assert result["status"] == "applied", result
        assert ("close", "7") in todoist.calls
