"""Bounded static/dry-run tests for the RESTART-01 restart rewire.

These tests never execute restart-live.sh, tdtb-restart, launchctl, or mutate
the live :8746 service. They verify canonical path resolution, plist template
selection, placeholder rendering, and the tdtb-restart symlink target.
"""
from __future__ import annotations

import os
import re
import subprocess
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
RESTART_SCRIPT = REPO_ROOT / "restart-live.sh"
PLIST_TEMPLATE = REPO_ROOT / "launchd" / "com.walle.tdtb.plist"
TDTB_RESTART = Path.home() / ".local" / "bin" / "tdtb-restart"
OLD_LAYOUT_MARKERS = ("Tasks/tdtb-app-pilot", "Development/Claudius")


def test_canonical_script_is_executable():
    assert os.access(RESTART_SCRIPT, os.X_OK)


def test_canonical_script_passes_zsh_syntax_check():
    result = subprocess.run(
        ["zsh", "-n", str(RESTART_SCRIPT)], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr


def test_canonical_script_has_no_old_layout_references():
    text = RESTART_SCRIPT.read_text()
    for marker in OLD_LAYOUT_MARKERS:
        assert marker not in text, f"old layout marker {marker!r} still present"


def test_canonical_script_plist_template_points_at_canonical_launchd():
    text = RESTART_SCRIPT.read_text()
    assert re.search(
        r'PLIST_TEMPLATE="\$TASK_DIR/launchd/\$LABEL\.plist"', text
    ), "PLIST_TEMPLATE must resolve from the script's own dir into launchd/"
    assert "REPO_DIR" not in text, "old two-levels-up REPO_DIR resolution is gone"


def test_canonical_script_resolves_canonical_runtime_paths():
    text = RESTART_SCRIPT.read_text()
    assert 'TASK_DIR="${0:A:h}"' in text
    assert "$TASK_DIR/frontend" in text
    assert "$TASK_DIR/app/static/cockpit/index.html" in text
    assert PLIST_TEMPLATE.exists()


def test_canonical_plist_template_uses_canonical_layout():
    text = PLIST_TEMPLATE.read_text()
    assert "__WALLE_HOME__/Repos/Projects/TDTB/app/.venv/bin/python" in text
    assert "<string>__WALLE_HOME__/Repos/Projects/TDTB/app</string>" in text
    for marker in OLD_LAYOUT_MARKERS:
        assert marker not in text, f"old layout marker {marker!r} in plist"


def test_plist_template_renders_cleanly():
    """Dry-run: replicate the script's sed substitution into a temp file and
    lint the result. No launchctl, no ~/Library/LaunchAgents write."""
    home = "/Users/walle-mini"
    vault = "/tmp/tdtb-fake-vault"
    template = PLIST_TEMPLATE.read_text()
    result = subprocess.run(
        [
            "sed",
            "-e",
            f"s|__WALLE_HOME__|{home}|g",
            "-e",
            f"s|__TDTB_VAULT_ROOT__|{vault}|g",
        ],
        input=template,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    rendered = result.stdout
    assert "__WALLE_HOME__" not in rendered
    assert "__TDTB_VAULT_ROOT__" not in rendered
    assert "Repos/Projects/TDTB/app/.venv/bin/python" in rendered
    assert "Repos/Projects/TDTB/app</string>" in rendered
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "com.walle.tdtb.plist"
        out.write_text(rendered)
        lint = subprocess.run(
            ["plutil", "-lint", str(out)], capture_output=True, text=True
        )
        assert lint.returncode == 0, lint.stderr


def test_tdtb_restart_symlink_points_at_canonical_script():
    if not TDTB_RESTART.is_symlink():
        pytest.skip("tdtb-restart not installed on this seat")
    target = os.path.realpath(TDTB_RESTART)
    assert target == str(RESTART_SCRIPT)
    for marker in OLD_LAYOUT_MARKERS:
        assert marker not in target
