"""V2 gate: runtime state is written to ``<TDTB_HOME>/state/`` and NEVER to the vault.

The vault root in every test below is a read-only, empty directory. Any write
under it would raise (or, if the process bypasses permissions, be caught by the
"still empty" assertion), so these tests pin both halves of the V2 contract:

  - the micro-adventure log, duration memory and runtime journal live in the
    state dir (``micro-adventure-log.md``, ``duration-memory.json``,
    ``runtime-journal/<date>.json``);
  - the stores S1 moved read/write only the state dir as well.
"""
from __future__ import annotations

import os
import stat
import sys
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "gather"))

import app_config  # noqa: E402
import capacities_builder as cb  # noqa: E402
import capacities_settings as cs  # noqa: E402
import deferrals as df  # noqa: E402
import duration_memory as dm  # noqa: E402
import exclusion_settings as es  # noqa: E402
import main as main_mod  # noqa: E402
import micro_adventure as ma  # noqa: E402
import runstate  # noqa: E402
import runtime_actions as ra  # noqa: E402
import tdtb_gather as gather  # noqa: E402

TODAY = date(2026, 7, 12)
SPACE = "space-1"


@pytest.fixture
def ro_vault(tmp_path: Path):
    root = tmp_path / "ro-vault"
    root.mkdir()
    root.chmod(stat.S_IRUSR | stat.S_IXUSR)
    yield root
    root.chmod(stat.S_IRWXU)


def _assert_vault_untouched(root: Path) -> None:
    assert list(root.rglob("*")) == [], "something was written under the vault root"


def test_state_paths_for_the_three_v2_stores(tmp_path, monkeypatch) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("TDTB_HOME", str(home))
    state = home / "state"
    assert ma.history_path() == state / "micro-adventure-log.md"
    assert dm.cache_path() == state / "duration-memory.json"
    assert dm.lock_path() == state / "duration-memory.lock"
    assert ra.journal_path(TODAY) == state / "runtime-journal" / "2026-07-12.json"


def test_absent_files_read_as_empty_and_create_nothing(ro_vault: Path) -> None:
    assert ma.read_history(ma.history_path()) == []
    assert dm.read_all_memory() == {}
    assert ra.load_journal(TODAY) == {"valid_date": str(TODAY), "actions": []}
    assert runstate.read_runstate(TODAY) is None
    assert df.load_deferrals() == {}
    assert es.read_settings().persisted is False
    assert cs.read_settings().persisted is False
    assert cb.read_source() is None
    assert not app_config.state_dir().exists()
    _assert_vault_untouched(ro_vault)


def test_module_level_writers_only_touch_the_state_dir(ro_vault: Path) -> None:
    entry = ma.build_history_entry("ma03", "Ride bike somewhere", today=TODAY, todoist_task_id="9")
    ma.write_history(ma.history_path(), ma.upsert_today_entry([], entry))
    assert dm.save_memory("todoist:1", 90) == 90
    assert dm.reset_memory("todoist:1") is True
    runstate.write_runstate(TODAY, runstate.build_runstate())
    runstate.write_digest_index(TODAY, [{"name": "x", "todoist_id": "1", "path": ""}])
    runstate.append_recent_selection(TODAY, [{"id": "a", "path": "p", "blocks": 1}])
    df.record_deferral({"name": "Fresh", "todoist_id": "99"}, TODAY)
    es.save_settings(expected_revision=0, exclusions=[])
    cs.save_settings(expected_revision=0, native_task_auto=cs.NativeTaskAutoPolicy())

    state = app_config.state_dir()
    assert (state / "micro-adventure-log.md").is_file()
    assert (state / "duration-memory.json").is_file()
    assert (state / "duration-memory.lock").is_file()
    _assert_vault_untouched(ro_vault)


def test_runtime_journal_apply_and_undo_use_the_state_dir(ro_vault: Path) -> None:
    from tests.test_runtime_actions import FakeStore, FakeTodoist  # noqa: PLC0415

    runstate.write_runstate(TODAY, runstate.build_runstate({"plan_manifest": [
        {"step": "B", "system": "todoist", "action": "schedule", "name": "Press",
         "id_or_path": "task-1", "time": "16:00", "duration_min": 30, "routing": "—"},
    ]}))
    todoist = FakeTodoist({
        "task-1": {"id": "task-1", "content": "Press", "project_id": "p1",
                   "is_completed": False, "due": {"datetime": "2026-07-12T16:00:00"}},
    })
    action = ra.apply_action(TODAY, "complete", "Press", {},
                             todoist=todoist, store=FakeStore())
    assert action["status"] == "applied"
    assert ra.journal_path(TODAY).is_file()
    ra.undo_action(TODAY, action["id"], todoist=todoist, store=FakeStore())
    assert ra.find_action(ra.load_journal(TODAY), action["id"])["status"] == "undone"
    _assert_vault_untouched(ro_vault)


def test_routes_write_the_log_and_duration_memory_to_the_state_dir(
    ro_vault: Path, monkeypatch
) -> None:
    monkeypatch.setattr(gather, "effective_date", lambda now: TODAY)
    app = main_mod.create_app()
    client = TestClient(app)
    headers = {"X-TDTB-Token": app.state.token}

    log = ma.history_path()
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(
        "---\ndescription: t\nschema_version: 1\nhistory:\n"
        "  - date: 2026-07-11\n    id: ma03\n    idea: Ride bike somewhere\n    done:\n---\n",
        encoding="utf-8",
    )
    r = client.post("/micro-adventure/loose-end",
                    json={"date": "2026-07-11", "id": "ma03", "done": True}, headers=headers)
    assert r.status_code == 200
    assert [(e.id, e.done) for e in ma.read_history(log)] == [("ma03", True)]

    r = client.post("/duration-memory/save",
                    json={"identity": "todoist:5", "minutes": 45}, headers=headers)
    assert r.status_code == 200
    assert dm.read_all_memory() == {"todoist:5": 45}
    assert dm.cache_path().parent == app_config.state_dir()
    _assert_vault_untouched(ro_vault)
