"""The operator CLI that flips ``read_source`` without disturbing the policy."""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import capacities_read_source_set as cli  # noqa: E402
import capacities_settings as cs  # noqa: E402

PROJECT = "0d194525-c5a1-4af5-bb62-202b83006b5e"


def _seed() -> cs.CapacitiesSettings:
    return cs.save_settings(
        expected_revision=0,
        statuses={"open": ["In Progress", "active"], "completed": ["Done"]},
        types={PROJECT: {"mode": "active", "statuses": ["In Progress"]}},
        native={"deadline_horizon_days": 5},
        excluded=["capacities:space-1:RootTask:task-excluded"],
    )


def _run(mode: str) -> tuple[int, str]:
    buffer = io.StringIO()
    code = cli.main([mode], stream=buffer)
    return code, buffer.getvalue()


def test_auto_changes_only_read_source():
    before = _seed()
    code, text = _run("auto")
    after = cs.read_settings().settings

    assert code == cli.EXIT_OK
    assert "status: changed" in text
    assert after.read_source == "auto"
    assert after.revision == before.revision + 1
    assert after.statuses == before.statuses
    assert after.types == before.types
    assert after.native == before.native
    assert after.excluded == before.excluded


def test_rest_is_the_kill_switch_and_round_trips():
    _seed()
    assert _run("auto")[0] == cli.EXIT_OK
    code, text = _run("rest")
    assert code == cli.EXIT_OK
    assert "status: changed" in text
    assert cs.read_settings().settings.read_source == "rest"


def test_setting_the_current_value_writes_nothing():
    _seed()
    revision = cs.read_settings().settings.revision
    code, text = _run("rest")
    assert code == cli.EXIT_OK
    assert "status: unchanged" in text
    assert cs.read_settings().settings.revision == revision


def test_a_missing_document_is_refused_and_nothing_is_created():
    assert not cs.read_settings().persisted
    code, text = _run("auto")
    assert code == cli.EXIT_REFUSED
    assert "refused" in text
    assert not cs.settings_path().exists()


def test_a_malformed_document_fails_without_printing_it():
    path = cs.settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": 3, "token": "fake-secret-DO-NOT-PRINT"}))
    code, text = _run("auto")
    assert code == cli.EXIT_FAILED
    assert "fake-secret" not in text
    assert "failed (" in text


def test_the_server_never_imports_the_cli():
    root = Path(__file__).parent.parent
    for name in ("main.py", "capacities_refresh.py", "capacities_builder.py"):
        assert "capacities_read_source_set" not in (root / name).read_text(encoding="utf-8")
