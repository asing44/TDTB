"""Tests for restart-live.sh — the attended TDTB restart.

Each test runs the real script against a temporary ``HOME``/``TDTB_HOME`` with
stub ``launchctl`` and ``curl`` executables first on ``PATH``. Nothing here
touches the live seat: no real ``launchctl`` call is ever made, the health
probe goes to a stub, and every path the script writes (plist, logs,
snapshots) lives inside the test temp dir.
"""
from __future__ import annotations

import os
import stat
import subprocess
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
RESTART_SCRIPT = REPO_ROOT / "restart-live.sh"

# Paths the stubs and the script use to talk to each other.
CALLS_ENV = "LAUNCHCTL_CALLS"
BOOTSTRAP_COUNTER_ENV = "LAUNCHCTL_BOOTSTRAP_COUNTER"
BOOTSTRAP_FAIL_FIRST_ENV = "LAUNCHCTL_BOOTSTRAP_FAIL_FIRST"


def _write_executable(path: Path, body: str) -> None:
    path.write_text(body)
    path.chmod(stat.S_IRWXU | stat.S_IRGRP | stat.S_IROTH)


def _launchctl_stub(path: Path) -> None:
    """Records every invocation; ``print`` reports a loaded job with a pid."""
    body = """#!/bin/zsh
set -u
print -r -- "$*" >> "${@@CALLS@@}"
case "$1" in
  print)
    print -r -- $'\\tpid = 4242'
    exit 0
    ;;
  bootstrap)
    if [[ "${@@FAIL_FIRST@@:-0}" == "1" ]]; then
      n=0
      [[ -f "${@@COUNTER@@}" ]] && n=$(< "${@@COUNTER@@}")
      n=$(( n + 1 ))
      print -r -- "$n" > "${@@COUNTER@@}"
      if (( n == 1 )); then
        print -u2 -- "Bootstrap failed: 5: Input/output error"
        exit 5
      fi
    fi
    exit 0
    ;;
  bootout)
    exit 0
    ;;
esac
exit 0
"""
    for token, name in (
        ("@@CALLS@@", CALLS_ENV),
        ("@@FAIL_FIRST@@", BOOTSTRAP_FAIL_FIRST_ENV),
        ("@@COUNTER@@", BOOTSTRAP_COUNTER_ENV),
    ):
        body = body.replace(token, name)
    _write_executable(path, body)


def _curl_stub(path: Path, *, healthy: bool) -> None:
    if healthy:
        body = """#!/bin/zsh
print -r -- '{"status":"ok"}'
exit 0
"""
    else:
        body = """#!/bin/zsh
exit 22
"""
    _write_executable(path, body)


class Seat:
    """A throwaway seat: temp HOME, temp TDTB_HOME, stub PATH."""

    def __init__(self, root: Path, *, healthy: bool = True) -> None:
        self.root = root
        self.home = root / "home"
        self.home.mkdir(parents=True, exist_ok=True)
        self.state_home = self.home / ".config" / "tdtb"
        self.state_home.mkdir(parents=True, exist_ok=True)
        self.stubs = root / "stubs"
        self.stubs.mkdir(parents=True, exist_ok=True)
        self.calls = root / "launchctl-calls.log"
        self.counter = root / "bootstrap-counter"
        self.log = self.home / "Library" / "Logs" / "tdtb-server.log"
        self.log.parent.mkdir(parents=True, exist_ok=True)
        _launchctl_stub(self.stubs / "launchctl")
        _curl_stub(self.stubs / "curl", healthy=healthy)

    @property
    def backups(self) -> Path:
        return self.state_home / "backups"

    def env(self, **overrides: str) -> dict[str, str]:
        env = {
            "HOME": str(self.home),
            "TDTB_HOME": str(self.state_home),
            "TDTB_PORT": "8791",
            "TDTB_HEALTH_TIMEOUT": "5",
            "PATH": f"{self.stubs}:{os.environ.get('PATH', '/usr/bin:/bin')}",
            CALLS_ENV: str(self.calls),
            BOOTSTRAP_COUNTER_ENV: str(self.counter),
            BOOTSTRAP_FAIL_FIRST_ENV: "0",
        }
        env.update(overrides)
        return env

    def run(self, *args: str, **overrides: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["zsh", str(RESTART_SCRIPT), *args],
            env=self.env(**overrides),
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=120,
        )

    def launchctl_calls(self) -> list[str]:
        if not self.calls.exists():
            return []
        return [line for line in self.calls.read_text().splitlines() if line.strip()]

    def snapshots(self) -> list[Path]:
        if not self.backups.is_dir():
            return []
        return sorted(p for p in self.backups.glob("pre-restart-*") if p.is_dir())


def _populate_state(state_home: Path) -> None:
    """The real state shapes the snapshot must carry (and the ones it must not)."""
    (state_home / "config.json").write_text('{"anchored_blocks": []}')
    (state_home / "producer-rules.json").write_text('{"rules": []}')
    (state_home / ".env").write_text("SECRET=1\n")
    mcp = state_home / "capacities-mcp"
    mcp.mkdir()
    (mcp / "token.json").write_text("{}")

    state = state_home / "state"
    state.mkdir()
    (state / "planning-artifact.json").write_text("{}")
    (state / "producer-cache.json").write_text("{}")
    (state / "runstate.json").write_text("{}")
    (state / "tdtb-capacities-settings.lock").write_text("")
    (state / ".env").write_text("SECRET=2\n")
    nested = state / "sub"
    nested.mkdir()
    (nested / "nested.json").write_text("{}")
    (nested / "nested.lock").write_text("")

    (state_home / "tdtb-capacities-content-cache.json").write_text("{}")
    (state_home / "tdtb-capacities-source.json").write_text("{}")
    (state_home / "tdtb-capacities-settings.lock").write_text("")


@pytest.fixture
def seat(tmp_path):
    s = Seat(tmp_path / "seat")
    _populate_state(s.state_home)
    s.log.write_text("".join(f"log line {i:03d}\n" for i in range(1, 41)))
    return s


def test_snapshot_contains_state_and_excludes_locks_env_and_mcp(seat):
    result = seat.run("--no-build")

    assert result.returncode == 0, result.stderr
    assert "bundles     SKIPPED (--no-build)" in result.stdout

    snapshots = seat.snapshots()
    assert len(snapshots) == 1
    snapshot = snapshots[0]
    assert "snapshot    " + str(snapshot) in result.stdout

    copied = {str(p.relative_to(snapshot)) for p in snapshot.rglob("*") if p.is_file()}
    assert copied == {
        "config.json",
        "producer-rules.json",
        "state/planning-artifact.json",
        "state/producer-cache.json",
        "state/runstate.json",
        "state/sub/nested.json",
        "tdtb-capacities-content-cache.json",
        "tdtb-capacities-source.json",
    }
    assert not list(snapshot.rglob("*.lock"))
    assert not (snapshot / "capacities-mcp").exists()
    assert not (snapshot / ".env").exists()
    assert not (snapshot / "state" / ".env").exists()


def test_pruning_keeps_the_newest_five(seat):
    seat.backups.mkdir()
    now = time.time()
    old_dirs = []
    for age in range(7, 0, -1):
        path = seat.backups / f"pre-restart-2020010{age}-000000"
        path.mkdir()
        (path / "config.json").write_text("{}")
        stamp = now - age * 100
        os.utime(path, (stamp, stamp))
        old_dirs.append(path)

    result = seat.run("--no-build")

    assert result.returncode == 0, result.stderr
    remaining = seat.snapshots()
    assert len(remaining) == 5
    # The three oldest snapshots (ages 7, 6, 5) rotate out; the newest four
    # survive alongside the one just taken.
    for pruned in old_dirs[:3]:
        assert pruned not in remaining
    for kept in old_dirs[3:]:
        assert kept in remaining


def test_failed_snapshot_aborts_before_anything_is_stopped(seat):
    # A regular file where the backups directory must go makes the snapshot fail.
    seat.backups.write_text("not a directory")

    result = seat.run("--no-build")

    assert result.returncode != 0
    assert "ABORTED" in result.stderr
    assert "snapshot    FAILED" in result.stderr
    assert seat.launchctl_calls() == []


def test_health_timeout_prints_log_tail_snapshot_and_rollback(tmp_path):
    seat = Seat(tmp_path / "seat", healthy=False)
    _populate_state(seat.state_home)
    seat.log.write_text("".join(f"log line {i:03d}\n" for i in range(1, 41)))
    head_before = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
    ).stdout.strip()

    result = seat.run("--no-build", TDTB_HEALTH_TIMEOUT="2")

    assert result.returncode != 0
    # 30-line tail of the server log.
    assert "log line 040" in result.stderr
    assert "log line 011" in result.stderr
    assert "log line 010" not in result.stderr
    # Clear failure, snapshot path, and copy-pasteable rollback instructions.
    assert "NO RESPONSE after 2s" in result.stderr
    snapshot = seat.snapshots()[0]
    assert str(snapshot) in result.stderr
    assert (snapshot / "config.json").exists()
    assert "switch --detach" in result.stderr
    assert "launchctl bootout gui/" in result.stderr
    assert str(seat.state_home) in result.stderr
    # Instructions only: the script neither checked out code nor restored state.
    if head_before:
        assert head_before[:12] in result.stderr
        assert subprocess.run(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
        ).stdout.strip() == head_before
    assert (seat.state_home / "config.json").read_text() == '{"anchored_blocks": []}'


def test_bootstrap_retry_still_recovers_a_transient_failure(seat):
    result = seat.run(
        "--no-build",
        LAUNCHCTL_BOOTSTRAP_FAIL_FIRST="1",
    )

    # The seat is already "loaded" per the stub, so the reload path boots out
    # and then retries bootstrap once.
    assert result.returncode == 0, result.stderr
    calls = seat.launchctl_calls()
    assert sum(1 for call in calls if call.startswith("bootstrap")) == 2
    assert any(call.startswith("bootout") for call in calls)
    assert "retrying once" in result.stderr
