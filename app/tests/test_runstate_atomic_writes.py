"""Race-safety gate for the three runstate atomic writers.

Each writer used a FIXED ``<name>.tmp`` scratch path, so two concurrent
callers to the same target collided: the first ``os.replace`` consumed the
shared temp file and the second raised ``FileNotFoundError``. The live
failure was ``GET /plan-inputs`` (the cockpit fires several in parallel)
500-ing in ``write_digest_index``.

These tests hammer each writer from many threads with a barrier so all
threads enter the write window together, and pin:

  - no exception escapes any writer;
  - the final artifact is intact (the digest index parses as JSON);
  - no ``*.tmp`` scratch files survive in the state directory.

Everything is fixture-only: the autouse ``_isolated_app_home`` fixture in
``conftest.py`` points ``TDTB_HOME`` at a private tmp dir.
"""
from __future__ import annotations

import json
import sys
import threading
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import runstate  # noqa: E402


DAY = date(2026, 10, 12)
THREADS = 16
ITERATIONS = 25


def _hammer(writer, threads: int = THREADS, iterations: int = ITERATIONS):
    """Run ``writer`` from ``threads`` threads, barrier-locked per iteration.

    Returns the exceptions raised. Every thread stays in the loop so a
    failing writer keeps participating and the barrier is never broken by an
    early exit."""
    barrier = threading.Barrier(threads)
    errors: list[BaseException] = []
    errors_lock = threading.Lock()

    def worker() -> None:
        for _ in range(iterations):
            barrier.wait()
            try:
                writer()
            except BaseException as exc:  # noqa: BLE001 — collected for assertion
                with errors_lock:
                    errors.append(exc)

    workers = [threading.Thread(target=worker) for _ in range(threads)]
    for t in workers:
        t.start()
    for t in workers:
        t.join()
    return errors


def _tmp_leftovers(directory: Path) -> list[str]:
    return sorted(p.name for p in directory.glob("*.tmp"))


def test_concurrent_digest_index_writes_are_race_safe() -> None:
    out_dir = runstate.state_runstate_dir()
    errors = _hammer(
        lambda: runstate.write_digest_index(
            DAY, [{"name": "Row", "todoist_id": "1", "path": "row.md"}]
        )
    )

    assert errors == []
    final = runstate.state_digest_index_path(DAY)
    assert final.is_file()
    assert isinstance(json.loads(final.read_text(encoding="utf-8")), dict)
    assert _tmp_leftovers(out_dir) == []


def test_concurrent_runstate_writes_are_race_safe() -> None:
    out_dir = runstate.state_runstate_dir()
    state = runstate.build_runstate({"anchor": "08:15"})
    errors = _hammer(lambda: runstate.write_runstate(DAY, state))

    assert errors == []
    final = runstate.state_runstate_path(DAY)
    assert final.is_file()
    assert runstate.read_runstate(DAY) is not None
    assert _tmp_leftovers(out_dir) == []


def test_concurrent_recent_selection_writes_are_race_safe() -> None:
    out_dir = runstate.state_runstate_dir()
    selections = [{"id": "s-1", "path": "s.md", "blocks": 2}]
    errors = _hammer(lambda: runstate.append_recent_selection(DAY, selections))

    assert errors == []
    final = runstate.state_recent_selections_path()
    assert final.is_file()
    assert runstate.read_recent_selections()
    assert _tmp_leftovers(out_dir) == []
