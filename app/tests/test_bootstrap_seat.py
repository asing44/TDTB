"""Tests for bootstrap-seat.sh — the portable TDTB seat bootstrap runner.

These tests fake uv, npm, and node with minimal stub executables, use a
temporary HOME, repo root, and vault, and never touch the real filesystem
outside the test temp dirs.
"""
from __future__ import annotations

import hashlib
import os
import stat
import subprocess
from pathlib import Path

import pytest

# Path to the real bootstrap script (always at the canonical repo root)
BOOTSTRAP_SCRIPT = Path(__file__).resolve().parent.parent.parent / "bootstrap-seat.sh"


def _compute_hash(file_path: Path) -> str:
    """SHA-256 hex digest of *file_path*, matching ``shasum -a 256``."""
    return hashlib.sha256(file_path.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_stub_exe(path: Path, exit_code: int = 0, *, stdout: str = "", stderr: str = ""):
    """Create a stub executable at *path* that prints *stdout*/*stderr* and
    exits with *exit_code*."""
    body = f"""#!/bin/zsh
{stdout:+echo {stdout!r}}
{stderr:+echo {stderr!r} >&2}
exit {exit_code}
"""
    path.write_text(body)
    path.chmod(stat.S_IRWXU | stat.S_IRGRP | stat.S_IROTH)


def _make_fake_uv(path: Path, *, python_ok: bool = True, pip_ok: bool = True, python_version: str = "Python 3.12.99"):
    """Create a stub ``uv`` executable at *path*.

    The stub supports two subcommands::

        uv venv --python 3.12 <dest>
        VIRTUAL_ENV=... uv pip install -r <reqfile>

    *python_ok* controls whether ``uv venv`` succeeds.
    *pip_ok*     controls whether ``uv pip install`` succeeds.
    *python_version* is the string the stub's ``python --version`` emits.
    """
    body = f"""#!/bin/zsh
set -u
case "$1" in
  venv)
    if [[ "$2" == "--python" && "$3" == "3.12" ]]; then
      mkdir -p "$4/bin"
      echo '#!/bin/zsh' > "$4/bin/python"
      echo 'if [[ "$1" == "--version" ]]; then echo "{python_version}"; else exit 0; fi' >> "$4/bin/python"
      chmod +x "$4/bin/python"
      exit {0 if python_ok else 1}
    else
      exit 1
    fi
    ;;
  pip)
    if [[ "$2" == "install" && "$3" == "-r" ]]; then
      exit {0 if pip_ok else 1}
    else
      exit 1
    fi
    ;;
  *)
    exit 1
    ;;
esac
"""
    path.write_text(body)
    path.chmod(stat.S_IRWXU | stat.S_IRGRP | stat.S_IROTH)


def _make_fake_npm(path: Path, *, ok: bool = True):
    """Create a stub ``npm`` executable at *path*.

    The stub supports::

        npm ci --prefix <dir>

    *ok* controls whether it succeeds.
    """
    body = f"""#!/bin/zsh
set -u
if [[ "$1" == "ci" && "$2" == "--prefix" ]]; then
  mkdir -p "$3/node_modules"
  exit {0 if ok else 1}
else
  exit 1
fi
"""
    path.write_text(body)
    path.chmod(stat.S_IRWXU | stat.S_IRGRP | stat.S_IROTH)


def _make_fake_node(path: Path):
    """Create a minimal stub ``node`` that just exits 0."""
    body = """#!/bin/zsh
exit 0
"""
    path.write_text(body)
    path.chmod(stat.S_IRWXU | stat.S_IRGRP | stat.S_IROTH)


@pytest.fixture
def temp_home(tmp_path: Path) -> Path:
    """A temporary $HOME with ``~/.local/bin`` created."""
    home = tmp_path / "home"
    home.mkdir(parents=True)
    (home / ".local" / "bin").mkdir(parents=True)
    return home


@pytest.fixture
def temp_repo(tmp_path: Path, temp_home: Path) -> Path:
    """A temporary repo root with the required sub-structure.

    Creates::

        <repo>/
          app/
            requirements.txt    (empty marker)
          frontend/
            package.json        (minimal)
            package-lock.json   (minimal)
          launchd/
            com.walle.tdtb.plist (template)
          restart-live.sh       (stub)
    """
    repo = tmp_path / "repo"
    (repo / "app").mkdir(parents=True)
    (repo / "app" / "requirements.txt").write_text("# test\n")
    (repo / "frontend").mkdir(parents=True)
    (repo / "frontend" / "package.json").write_text('{"name":"test"}\n')
    (repo / "frontend" / "package-lock.json").write_text('{"lockfileVersion":3}\n')
    (repo / "launchd").mkdir(parents=True)
    # The plist template uses __WALLE_HOME__, __TDTB_REPO__, __TDTB_VAULT_ROOT__
    (repo / "launchd" / "com.walle.tdtb.plist").write_text("""\
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.walle.tdtb</string>
  <key>ProgramArguments</key>
  <array>
    <string>__TDTB_REPO__/app/.venv/bin/python</string>
    <string>main.py</string>
  </array>
  <key>WorkingDirectory</key>
  <string>__TDTB_REPO__/app</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>TDTB_VAULT_ROOT</key>
    <string>__TDTB_VAULT_ROOT__</string>
  </dict>
  <key>RunAtLoad</key>
  <true/>
  <key>StandardOutPath</key>
  <string>__WALLE_HOME__/Library/Logs/tdtb-server.log</string>
  <key>StandardErrorPath</key>
  <string>__WALLE_HOME__/Library/Logs/tdtb-server.log</string>
</dict>
</plist>
""")
    # restart-live.sh and bootstrap-seat.sh stubs
    (repo / "restart-live.sh").write_text("#!/bin/zsh\n# stub\n")
    (repo / "restart-live.sh").chmod(0o755)
    (repo / "bootstrap-seat.sh").write_text("#!/bin/zsh\n# stub\n")
    (repo / "bootstrap-seat.sh").chmod(0o755)
    return repo


@pytest.fixture
def temp_vault(tmp_path: Path) -> Path:
    """A temporary vault at one of the two supported paths relative to HOME.

    Creates ``<tmp>/vault`` (caller sets HOME accordingly or passes the path
    explicitly via TDTB_VAULT_ROOT).
    """
    vault = tmp_path / "vault"
    vault.mkdir(parents=True)
    (vault / ".obsidian").mkdir(parents=True)  # minimal vault marker
    return vault


@pytest.fixture
def fake_bindir(tmp_path: Path) -> Path:
    """A temporary bin directory with fake uv, npm, and node."""
    bindir = tmp_path / "bin"
    bindir.mkdir(parents=True)
    _make_fake_uv(bindir / "uv")
    _make_fake_npm(bindir / "npm")
    _make_fake_node(bindir / "node")
    return bindir


def _run_bootstrap(
    repo: Path,
    home: Path,
    fake_bin: Path,
    *,
    extra_args: list[str] | None = None,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    """Run bootstrap-seat.sh with the given environment.

    Returns the completed process.  Does *not* raise on non-zero exit.
    """
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["TDTB_REPO"] = str(repo)
    env["PATH"] = f"{fake_bin}:/usr/bin:/bin"
    if extra_env:
        env.update(extra_env)

    args = [str(BOOTSTRAP_SCRIPT)]
    if extra_args:
        args.extend(extra_args)

    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        env=env,
    )


# ===================================================================
# Tests
# ===================================================================

class TestDefaultBootstrap:
    """Default mode: venv, frontend deps, restart symlink."""

    def test_creates_venv(self, temp_repo: Path, temp_home: Path, fake_bindir: Path):
        """Default run creates .venv, installs deps, and writes hash marker."""
        result = _run_bootstrap(temp_repo, temp_home, fake_bindir)
        assert result.returncode == 0, f"stderr: {result.stderr}"
        venv_dir = temp_repo / "app" / ".venv"
        assert venv_dir.is_dir(), ".venv should have been created"
        marker = venv_dir / ".tdtb-req-hash"
        assert marker.is_file(), "requirements hash marker should exist"

    def test_creates_node_modules(self, temp_repo: Path, temp_home: Path, fake_bindir: Path):
        """Default run installs frontend deps."""
        result = _run_bootstrap(temp_repo, temp_home, fake_bindir)
        assert result.returncode == 0, f"stderr: {result.stderr}"
        nm = temp_repo / "frontend" / "node_modules"
        assert nm.is_dir(), "node_modules should have been created"
        marker = temp_repo / "frontend" / ".tdtb-lock-hash"
        assert marker.is_file(), "lock hash marker should exist"

    def test_creates_restart_symlink(self, temp_repo: Path, temp_home: Path, fake_bindir: Path):
        """Default run symlinks tdtb-restart to restart-live.sh."""
        result = _run_bootstrap(temp_repo, temp_home, fake_bindir)
        assert result.returncode == 0, f"stderr: {result.stderr}"
        link = temp_home / ".local" / "bin" / "tdtb-restart"
        assert link.is_symlink(), "tdtb-restart symlink should exist"
        expected_target = (temp_repo / "restart-live.sh").resolve()
        assert link.resolve() == expected_target, (
            f"symlink target mismatch: {link.resolve()} != {expected_target}"
        )

    def test_idempotent(self, temp_repo: Path, temp_home: Path, fake_bindir: Path):
        """Repeated runs succeed and don't rebuild unnecessarily."""
        # First run
        r1 = _run_bootstrap(temp_repo, temp_home, fake_bindir)
        assert r1.returncode == 0, f"first run failed: {r1.stderr}"
        # Second run — markers now exist and match, so no rebuild
        r2 = _run_bootstrap(temp_repo, temp_home, fake_bindir)
        assert r2.returncode == 0, f"second run failed: {r2.stderr}"
        # Should skip both venv and frontend steps
        assert "already up to date" in r2.stdout, (
            "idempotent run should skip both venv and frontend: " + r2.stdout
        )

    def test_repairs_incomplete_venv(self, temp_repo: Path, temp_home: Path, fake_bindir: Path):
        """A partial venv from an interrupted run is repaired on retry."""
        (temp_repo / "app" / ".venv").mkdir()
        result = _run_bootstrap(temp_repo, temp_home, fake_bindir)
        assert result.returncode == 0, f"repair run failed: {result.stderr}"
        assert (temp_repo / "app" / ".venv" / "bin" / "python").is_file()
        assert "incomplete venv detected" in result.stderr

    def test_skips_venv_when_current(self, temp_repo: Path, temp_home: Path, fake_bindir: Path):
        """When venv and marker match requirements, skip reinstall."""
        venv_dir = temp_repo / "app" / ".venv"
        (venv_dir / "bin").mkdir(parents=True)
        # Create a python stub that reports the expected version
        (venv_dir / "bin" / "python").write_text(
            '#!/bin/zsh\nif [[ "$1" == "--version" ]]; then echo "Python 3.12.99"; else exit 0; fi\n'
        )
        (venv_dir / "bin" / "python").chmod(0o755)
        # Write a matching marker
        expected_hash = _compute_hash(temp_repo / "app" / "requirements.txt")
        (venv_dir / ".tdtb-req-hash").write_text(expected_hash)

        result = _run_bootstrap(temp_repo, temp_home, fake_bindir)
        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert "already up to date" in result.stdout

    def test_recreates_venv_on_wrong_python_version(self, temp_repo: Path, temp_home: Path, fake_bindir: Path):
        """When venv python version is not 3.12, recreate even if marker matches."""
        venv_dir = temp_repo / "app" / ".venv"
        (venv_dir / "bin").mkdir(parents=True)
        # Create a python stub that reports wrong version
        (venv_dir / "bin" / "python").write_text(
            '#!/bin/zsh\nif [[ "$1" == "--version" ]]; then echo "Python 3.11.10"; else exit 0; fi\n'
        )
        (venv_dir / "bin" / "python").chmod(0o755)
        # Write a seemingly matching marker
        expected_hash = _compute_hash(temp_repo / "app" / "requirements.txt")
        (venv_dir / ".tdtb-req-hash").write_text(expected_hash)

        result = _run_bootstrap(temp_repo, temp_home, fake_bindir)
        assert result.returncode == 0, f"stderr: {result.stderr}"
        # Should warn about version mismatch and recreate
        assert "version mismatch" in result.stderr
        # venv should be replaced with new bin/python from fake_uv (version 3.12)
        new_python = venv_dir / "bin" / "python"
        assert new_python.is_file()
        new_version = subprocess.run(
            [str(new_python), "--version"], capture_output=True, text=True
        )
        assert "3.12" in new_version.stdout

    def test_reinstalls_on_venv_hash_change(self, temp_repo: Path, temp_home: Path, fake_bindir: Path):
        """When requirements.txt changes, venv is reinstalled."""
        venv_dir = temp_repo / "app" / ".venv"
        (venv_dir / "bin").mkdir(parents=True)
        (venv_dir / "bin" / "python").write_text(
            '#!/bin/zsh\nif [[ "$1" == "--version" ]]; then echo "Python 3.12.99"; else exit 0; fi\n'
        )
        (venv_dir / "bin" / "python").chmod(0o755)
        # Write a stale marker with a deliberately wrong hash
        (venv_dir / ".tdtb-req-hash").write_text("0" * 64)

        result = _run_bootstrap(temp_repo, temp_home, fake_bindir)
        assert result.returncode == 0, f"stderr: {result.stderr}"
        # Should warn about hash mismatch and reinstall
        assert "hash mismatch" in result.stderr
        # Marker should now have the correct hash
        expected_hash = _compute_hash(temp_repo / "app" / "requirements.txt")
        actual_hash = (venv_dir / ".tdtb-req-hash").read_text().strip()
        assert actual_hash == expected_hash

    def test_failed_pip_does_not_write_marker(self, temp_repo: Path, temp_home: Path, fake_bindir: Path, tmp_path: Path):
        """If uv pip install fails, the marker is NOT updated."""
        venv_dir = temp_repo / "app" / ".venv"
        (venv_dir / "bin").mkdir(parents=True)
        (venv_dir / "bin" / "python").write_text(
            '#!/bin/zsh\nif [[ "$1" == "--version" ]]; then echo "Python 3.12.99"; else exit 0; fi\n'
        )
        (venv_dir / "bin" / "python").chmod(0o755)
        # Write a stale marker
        (venv_dir / ".tdtb-req-hash").write_text("STALE_MARKER")

        # Create a bindir where uv pip fails
        bad_bin = tmp_path / "badbin"
        bad_bin.mkdir()
        _make_fake_uv(bad_bin / "uv", python_ok=True, pip_ok=False)
        _make_fake_npm(bad_bin / "npm")
        _make_fake_node(bad_bin / "node")

        result = _run_bootstrap(temp_repo, temp_home, bad_bin)
        assert result.returncode != 0, "bootstrap should fail when pip fails"
        # Marker should still contain the original stale value
        assert (venv_dir / ".tdtb-req-hash").read_text().strip() == "STALE_MARKER"

    def test_skips_frontend_when_current(self, temp_repo: Path, temp_home: Path, fake_bindir: Path):
        """When node_modules and marker match lock, skip reinstall."""
        (temp_repo / "frontend" / "node_modules").mkdir()
        expected_hash = _compute_hash(temp_repo / "frontend" / "package-lock.json")
        (temp_repo / "frontend" / ".tdtb-lock-hash").write_text(expected_hash)

        result = _run_bootstrap(temp_repo, temp_home, fake_bindir)
        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert "already up to date" in result.stdout

    def test_reinstalls_frontend_on_stale_marker(self, temp_repo: Path, temp_home: Path, fake_bindir: Path):
        """When the lock marker is stale, npm ci is re-run."""
        (temp_repo / "frontend" / "node_modules").mkdir()
        (temp_repo / "frontend" / ".tdtb-lock-hash").write_text("0" * 64)

        result = _run_bootstrap(temp_repo, temp_home, fake_bindir)
        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert "hash mismatch" in result.stderr
        # Marker should now have the correct hash
        expected_hash = _compute_hash(temp_repo / "frontend" / "package-lock.json")
        actual_hash = (temp_repo / "frontend" / ".tdtb-lock-hash").read_text().strip()
        assert actual_hash == expected_hash

    def test_failed_npm_does_not_write_marker(self, temp_repo: Path, temp_home: Path, fake_bindir: Path, tmp_path: Path):
        """If npm ci fails, the marker is NOT updated."""
        (temp_repo / "frontend" / "node_modules").mkdir()
        (temp_repo / "frontend" / ".tdtb-lock-hash").write_text("STALE_LOCK")

        bad_bin = tmp_path / "badbin"
        bad_bin.mkdir()
        _make_fake_uv(bad_bin / "uv")
        _make_fake_npm(bad_bin / "npm", ok=False)
        _make_fake_node(bad_bin / "node")

        result = _run_bootstrap(temp_repo, temp_home, bad_bin)
        assert result.returncode != 0, "bootstrap should fail when npm fails"
        assert (temp_repo / "frontend" / ".tdtb-lock-hash").read_text().strip() == "STALE_LOCK"

    def test_retry_after_failed_pip(self, temp_repo: Path, temp_home: Path, tmp_path: Path):
        """After a failed pip install, a retry with a working tool recovers."""
        venv_dir = temp_repo / "app" / ".venv"
        (venv_dir / "bin").mkdir(parents=True)
        (venv_dir / "bin" / "python").write_text(
            '#!/bin/zsh\nif [[ "$1" == "--version" ]]; then echo "Python 3.12.99"; else exit 0; fi\n'
        )
        (venv_dir / "bin" / "python").chmod(0o755)
        (venv_dir / ".tdtb-req-hash").write_text("0" * 64)

        # Create a bindir where uv pip fails
        bad_bin = tmp_path / "badbin"
        bad_bin.mkdir()
        _make_fake_uv(bad_bin / "uv", python_ok=True, pip_ok=False)
        _make_fake_npm(bad_bin / "npm")
        _make_fake_node(bad_bin / "node")

        # First run — pip fails, marker unchanged
        r1 = _run_bootstrap(temp_repo, temp_home, bad_bin)
        assert r1.returncode != 0, "first run should fail"
        assert (venv_dir / ".tdtb-req-hash").read_text().strip() == "0" * 64

        # Second run — pip succeeds, marker updated, bootstrap passes
        good_bin = tmp_path / "goodbin"
        good_bin.mkdir()
        _make_fake_uv(good_bin / "uv", python_ok=True, pip_ok=True)
        _make_fake_npm(good_bin / "npm")
        _make_fake_node(good_bin / "node")

        r2 = _run_bootstrap(temp_repo, temp_home, good_bin)
        assert r2.returncode == 0, f"retry should succeed: {r2.stderr}"
        expected_hash = _compute_hash(temp_repo / "app" / "requirements.txt")
        assert (venv_dir / ".tdtb-req-hash").read_text().strip() == expected_hash

    def test_retry_after_failed_npm(self, temp_repo: Path, temp_home: Path, tmp_path: Path):
        """After a failed npm ci, a retry with a working tool recovers."""
        (temp_repo / "frontend" / "node_modules").mkdir()
        (temp_repo / "frontend" / ".tdtb-lock-hash").write_text("0" * 64)

        # First run — npm fails, marker unchanged
        bad_bin = tmp_path / "badbin"
        bad_bin.mkdir()
        _make_fake_uv(bad_bin / "uv")
        _make_fake_npm(bad_bin / "npm", ok=False)
        _make_fake_node(bad_bin / "node")

        r1 = _run_bootstrap(temp_repo, temp_home, bad_bin)
        assert r1.returncode != 0, "first run should fail"
        assert (temp_repo / "frontend" / ".tdtb-lock-hash").read_text().strip() == "0" * 64

        # Second run — npm succeeds, marker updated
        good_bin = tmp_path / "goodbin"
        good_bin.mkdir()
        _make_fake_uv(good_bin / "uv")
        _make_fake_npm(good_bin / "npm", ok=True)
        _make_fake_node(good_bin / "node")

        r2 = _run_bootstrap(temp_repo, temp_home, good_bin)
        assert r2.returncode == 0, f"retry should succeed: {r2.stderr}"
        expected_hash = _compute_hash(temp_repo / "frontend" / "package-lock.json")
        assert (temp_repo / "frontend" / ".tdtb-lock-hash").read_text().strip() == expected_hash


class TestBootstrapLink:
    """Default mode also creates ~/.local/bin/tdtb-bootstrap symlink."""

    def test_creates_bootstrap_symlink(self, temp_repo: Path, temp_home: Path, fake_bindir: Path):
        """Default run creates tdtb-bootstrap symlink."""
        result = _run_bootstrap(temp_repo, temp_home, fake_bindir)
        assert result.returncode == 0, f"stderr: {result.stderr}"
        link = temp_home / ".local" / "bin" / "tdtb-bootstrap"
        assert link.is_symlink(), "tdtb-bootstrap symlink should exist"

    def test_both_symlinks_created(self, temp_repo: Path, temp_home: Path, fake_bindir: Path):
        """Default run creates both tdtb-restart and tdtb-bootstrap."""
        result = _run_bootstrap(temp_repo, temp_home, fake_bindir)
        assert result.returncode == 0
        restart_link = temp_home / ".local" / "bin" / "tdtb-restart"
        bootstrap_link = temp_home / ".local" / "bin" / "tdtb-bootstrap"
        assert restart_link.is_symlink(), "tdtb-restart symlink should exist"
        assert bootstrap_link.is_symlink(), "tdtb-bootstrap symlink should exist"

    def test_bootstrap_symlink_target_correct(self, temp_repo: Path, temp_home: Path, fake_bindir: Path):
        """tdtb-bootstrap points to bootstrap-seat.sh in the repo."""
        result = _run_bootstrap(temp_repo, temp_home, fake_bindir)
        assert result.returncode == 0
        link = temp_home / ".local" / "bin" / "tdtb-bootstrap"
        expected_target = (temp_repo / "bootstrap-seat.sh").resolve()
        assert link.resolve() == expected_target, (
            f"symlink target mismatch: {link.resolve()} != {expected_target}"
        )

    def test_bootstrap_symlink_idempotent(self, temp_repo: Path, temp_home: Path, fake_bindir: Path):
        """Second run detects both links already point correctly."""
        r1 = _run_bootstrap(temp_repo, temp_home, fake_bindir)
        assert r1.returncode == 0, f"first run failed: {r1.stderr}"
        r2 = _run_bootstrap(temp_repo, temp_home, fake_bindir)
        assert r2.returncode == 0, f"second run failed: {r2.stderr}"
        # Both links should show "already points to" in output
        assert "symlink already points to" in r2.stdout

    def test_bootstrap_symlink_custom_repo(self, temp_repo: Path, temp_home: Path, fake_bindir: Path, tmp_path: Path):
        """When TDTB_REPO points to a worktree, the symlink targets its bootstrap-seat.sh."""
        worktree = tmp_path / "worktree"
        (worktree / "app").mkdir(parents=True)
        (worktree / "app" / "requirements.txt").write_text("# wt\n")
        (worktree / "frontend").mkdir(parents=True)
        (worktree / "frontend" / "package.json").write_text('{"name":"wt"}\n')
        (worktree / "frontend" / "package-lock.json").write_text('{"lockfileVersion":3}\n')
        (worktree / "launchd").mkdir(parents=True)
        (worktree / "launchd" / "com.walle.tdtb.plist").write_text(
            (temp_repo / "launchd" / "com.walle.tdtb.plist").read_text()
        )
        (worktree / "restart-live.sh").write_text("#!/bin/zsh\n# stub\n")
        (worktree / "restart-live.sh").chmod(0o755)
        (worktree / "bootstrap-seat.sh").write_text("#!/bin/zsh\n# stub\n")
        (worktree / "bootstrap-seat.sh").chmod(0o755)

        result = _run_bootstrap(worktree, temp_home, fake_bindir)
        assert result.returncode == 0, f"stderr: {result.stderr}"

        link = temp_home / ".local" / "bin" / "tdtb-bootstrap"
        expected_target = (worktree / "bootstrap-seat.sh").resolve()
        assert link.resolve() == expected_target

    def test_bootstrap_symlink_fails_on_non_symlink_collision(self, temp_repo: Path, temp_home: Path, fake_bindir: Path):
        """A regular file at the link path causes a clear failure."""
        link = temp_home / ".local" / "bin" / "tdtb-bootstrap"
        link.write_text("not a symlink")

        result = _run_bootstrap(temp_repo, temp_home, fake_bindir)
        assert result.returncode != 0
        assert "non-symlink" in result.stderr
        assert "tdtb-bootstrap" in result.stderr


class TestLaunchdMode:
    """--launchd renders and stages the plist."""

    def test_stages_plist(self, temp_repo: Path, temp_home: Path, fake_bindir: Path, temp_vault: Path):
        """--launchd stages the plist with all substitutions."""
        result = _run_bootstrap(
            temp_repo, temp_home, fake_bindir,
            extra_args=["--launchd"],
            extra_env={"TDTB_VAULT_ROOT": str(temp_vault)},
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"

        plist = temp_home / "Library" / "LaunchAgents" / "com.walle.tdtb.plist"
        assert plist.is_file(), "plist should have been staged"

        content = plist.read_text()
        # Verify all three substitutions
        assert str(temp_home) in content, "plist should contain resolved HOME"
        assert str(temp_repo) in content, "plist should contain resolved TDTB_REPO"
        assert str(temp_vault) in content, "plist should contain resolved vault"
        # Verify template markers are gone
        assert "__WALLE_HOME__" not in content, "HOME marker should be substituted"
        assert "__TDTB_REPO__" not in content, "REPO marker should be substituted"
        assert "__TDTB_VAULT_ROOT__" not in content, "vault marker should be substituted"

    def test_launchd_custom_repo(self, temp_repo: Path, temp_home: Path, fake_bindir: Path, temp_vault: Path, tmp_path: Path):
        """A custom TDTB_REPO (e.g. a worktree) is reflected in the staged plist."""
        # Create a second repo structure (simulates a worktree)
        worktree = tmp_path / "worktree"
        (worktree / "app").mkdir(parents=True)
        (worktree / "app" / "requirements.txt").write_text("# worktree\n")
        (worktree / "frontend").mkdir(parents=True)
        (worktree / "frontend" / "package.json").write_text('{"name":"wt"}\n')
        (worktree / "frontend" / "package-lock.json").write_text('{"lockfileVersion":3}\n')
        (worktree / "launchd").mkdir(parents=True)
        (worktree / "launchd" / "com.walle.tdtb.plist").write_text(
            (temp_repo / "launchd" / "com.walle.tdtb.plist").read_text()
        )
        (worktree / "restart-live.sh").write_text("#!/bin/zsh\n# worktree stub\n")
        (worktree / "restart-live.sh").chmod(0o755)
        (worktree / "bootstrap-seat.sh").write_text("#!/bin/zsh\n# worktree stub\n")
        (worktree / "bootstrap-seat.sh").chmod(0o755)

        result = _run_bootstrap(
            worktree, temp_home, fake_bindir,
            extra_args=["--launchd"],
            extra_env={"TDTB_VAULT_ROOT": str(temp_vault)},
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"

        plist = temp_home / "Library" / "LaunchAgents" / "com.walle.tdtb.plist"
        content = plist.read_text()
        # The plist should reference the worktree path, not the original repo
        assert str(worktree) in content, (
            f"plist should contain worktree path {worktree}: {content}"
        )
        assert str(temp_repo) not in content, (
            "plist should NOT contain the default repo path"
        )

    def test_detects_current_plist(self, temp_repo: Path, temp_home: Path, fake_bindir: Path, temp_vault: Path):
        """Re-run with same vault skips re-rendering."""
        r1 = _run_bootstrap(
            temp_repo, temp_home, fake_bindir,
            extra_args=["--launchd"],
            extra_env={"TDTB_VAULT_ROOT": str(temp_vault)},
        )
        assert r1.returncode == 0, f"first run: {r1.stderr}"

        r2 = _run_bootstrap(
            temp_repo, temp_home, fake_bindir,
            extra_args=["--launchd"],
            extra_env={"TDTB_VAULT_ROOT": str(temp_vault)},
        )
        assert r2.returncode == 0, f"second run: {r2.stderr}"
        assert "already current" in r2.stdout or "already exists" in r2.stdout, (
            "second run should detect plist is current"
        )

    def test_fails_on_missing_vault(self, temp_repo: Path, temp_home: Path, fake_bindir: Path):
        """--launchd fails closed when no vault is found."""
        result = _run_bootstrap(
            temp_repo, temp_home, fake_bindir,
            extra_args=["--launchd"],
        )
        assert result.returncode != 0
        assert "vault" in result.stderr.lower(), (
            "error should mention vault: " + result.stderr
        )

    def test_fails_on_invalid_plist(self, temp_repo: Path, temp_home: Path, fake_bindir: Path, temp_vault: Path, tmp_path: Path):
        """--launchd fails closed when the rendered plist is invalid."""
        # Replace plist template with something that will render to invalid XML
        (temp_repo / "launchd" / "com.walle.tdtb.plist").write_text("not xml at all <<<")
        result = _run_bootstrap(
            temp_repo, temp_home, fake_bindir,
            extra_args=["--launchd"],
            extra_env={"TDTB_VAULT_ROOT": str(temp_vault)},
        )
        assert result.returncode != 0
        assert "invalid" in result.stderr.lower() or "plutil" in result.stderr.lower(), (
            "error should mention invalid plist: " + result.stderr
        )

    def test_preserves_existing_plist_on_failure(
        self, temp_repo: Path, temp_home: Path, fake_bindir: Path, temp_vault: Path,
    ):
        """If the new plist can't be validated, the old one is preserved."""
        plist_dest = temp_home / "Library" / "LaunchAgents" / "com.walle.tdtb.plist"
        plist_dest.parent.mkdir(parents=True)
        plist_dest.write_text("<?xml version=\"1.0\"?>\n<plist><dict/></plist>\n")

        # Now corrupt the template so new rendering will fail
        (temp_repo / "launchd" / "com.walle.tdtb.plist").write_text("broken <<<")
        result = _run_bootstrap(
            temp_repo, temp_home, fake_bindir,
            extra_args=["--launchd"],
            extra_env={"TDTB_VAULT_ROOT": str(temp_vault)},
        )
        assert result.returncode != 0
        # The old plist should still be there
        assert plist_dest.exists(), "existing plist should be preserved"
        assert plist_dest.read_text().startswith("<?xml"), (
            "old plist content should be unchanged"
        )


class TestDryRun:
    """--dry-run reports changes without making any."""

    def test_no_venv_created(self, temp_repo: Path, temp_home: Path, fake_bindir: Path):
        """--dry-run does not create .venv."""
        result = _run_bootstrap(temp_repo, temp_home, fake_bindir, extra_args=["--dry-run"])
        assert result.returncode == 0, f"stderr: {result.stderr}"
        venv_dir = temp_repo / "app" / ".venv"
        assert not venv_dir.exists(), ".venv should NOT have been created during dry run"
        assert "dry-run" in result.stdout.lower()

    def test_no_node_modules(self, temp_repo: Path, temp_home: Path, fake_bindir: Path):
        """--dry-run does not run npm ci."""
        result = _run_bootstrap(temp_repo, temp_home, fake_bindir, extra_args=["--dry-run"])
        assert result.returncode == 0
        nm = temp_repo / "frontend" / "node_modules"
        assert not nm.exists(), "node_modules should NOT have been created during dry run"

    def test_no_symlink(self, temp_repo: Path, temp_home: Path, fake_bindir: Path):
        """--dry-run does not create either symlink."""
        result = _run_bootstrap(temp_repo, temp_home, fake_bindir, extra_args=["--dry-run"])
        assert result.returncode == 0
        restart_link = temp_home / ".local" / "bin" / "tdtb-restart"
        bootstrap_link = temp_home / ".local" / "bin" / "tdtb-bootstrap"
        assert not restart_link.exists(), "restart symlink should NOT have been created during dry run"
        assert not bootstrap_link.exists(), "bootstrap symlink should NOT have been created during dry run"

    def test_no_plist(self, temp_repo: Path, temp_home: Path, fake_bindir: Path, temp_vault: Path):
        """--dry-run --launchd does not stage the plist."""
        result = _run_bootstrap(
            temp_repo, temp_home, fake_bindir,
            extra_args=["--dry-run", "--launchd"],
            extra_env={"TDTB_VAULT_ROOT": str(temp_vault)},
        )
        assert result.returncode == 0
        plist = temp_home / "Library" / "LaunchAgents" / "com.walle.tdtb.plist"
        assert not plist.exists(), "plist should NOT have been staged during dry run"

    def test_dry_run_no_marker_writes(self, temp_repo: Path, temp_home: Path, fake_bindir: Path):
        """--dry-run does not write dependency hash markers."""
        result = _run_bootstrap(temp_repo, temp_home, fake_bindir, extra_args=["--dry-run"])
        assert result.returncode == 0
        req_marker = temp_repo / "app" / ".venv" / ".tdtb-req-hash"
        lock_marker = temp_repo / "frontend" / ".tdtb-lock-hash"
        assert not req_marker.exists(), "req hash marker should NOT be written during dry run"
        assert not lock_marker.exists(), "lock hash marker should NOT be written during dry run"


class TestFailureModes:
    """Fail-closed behavior."""

    def test_fails_on_missing_uv(self, temp_repo: Path, temp_home: Path, tmp_path: Path):
        """Missing uv causes a clear failure."""
        bindir = tmp_path / "bin"
        bindir.mkdir(parents=True)
        _make_fake_npm(bindir / "npm")
        _make_fake_node(bindir / "node")
        # No uv
        result = _run_bootstrap(temp_repo, temp_home, bindir)
        assert result.returncode != 0
        assert "uv" in result.stderr, f"should mention uv: {result.stderr}"

    def test_fails_on_missing_npm(self, temp_repo: Path, temp_home: Path, tmp_path: Path):
        """Missing npm causes a clear failure."""
        bindir = tmp_path / "bin"
        bindir.mkdir(parents=True)
        _make_fake_uv(bindir / "uv")
        _make_fake_node(bindir / "node")
        # No npm
        result = _run_bootstrap(temp_repo, temp_home, bindir)
        assert result.returncode != 0
        assert "npm" in result.stderr, f"should mention npm: {result.stderr}"

    def test_fails_on_missing_repo_path(self, temp_repo: Path, temp_home: Path, fake_bindir: Path):
        """Missing required repo files cause a clear failure."""
        (temp_repo / "app" / "requirements.txt").unlink()
        result = _run_bootstrap(temp_repo, temp_home, fake_bindir)
        assert result.returncode != 0
        assert "requirements.txt" in result.stderr, (
            f"should mention missing file: {result.stderr}"
        )
