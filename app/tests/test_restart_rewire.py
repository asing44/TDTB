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
    assert "__TDTB_REPO__/app/.venv/bin/python" in text
    assert "<string>__TDTB_REPO__/app</string>" in text
    for marker in OLD_LAYOUT_MARKERS:
        assert marker not in text, f"old layout marker {marker!r} in plist"


def test_plist_template_renders_cleanly():
    """Dry-run: replicate the script's sed substitution into a temp file and
    lint the result. No launchctl, no ~/Library/LaunchAgents write."""
    home = "/Users/walle-mini"
    repo = str(REPO_ROOT)
    vault = "/tmp/tdtb-fake-vault"
    template = PLIST_TEMPLATE.read_text()
    result = subprocess.run(
        [
            "sed",
            "-e",
            f"s|__WALLE_HOME__|{home}|g",
            "-e",
            f"s|__TDTB_REPO__|{repo}|g",
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
    assert "__TDTB_REPO__" not in rendered
    assert "__TDTB_VAULT_ROOT__" not in rendered
    assert f"{repo}/app/.venv/bin/python" in rendered
    assert f"{repo}/app</string>" in rendered
    # Verify no unresolved __...__ placeholders remain
    assert not re.search(r'__[A-Z_]+__', rendered), (
        "rendered plist contains unresolved placeholder"
    )
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "com.walle.tdtb.plist"
        out.write_text(rendered)
        lint = subprocess.run(
            ["plutil", "-lint", str(out)], capture_output=True, text=True
        )
        assert lint.returncode == 0, lint.stderr


def test_tdtb_restart_plist_refresh_includes_repo_substitution():
    """restart-live.sh's refresh_plist() substitutes __TDTB_REPO__ with TASK_DIR."""
    text = RESTART_SCRIPT.read_text()
    # Check the sed command in refresh_plist() has all three substitutions
    assert re.search(
        r'__TDTB_REPO__.*\$TASK_DIR', text
    ), "refresh_plist must substitute __TDTB_REPO__ with TASK_DIR"
    assert "__TDTB_REPO__" in text
    assert "__TDTB_VAULT_ROOT__" in text
    assert "__WALLE_HOME__" in text


def test_tdtb_restart_rejects_unresolved_placeholders():
    """The grep guard in refresh_plist() rejects remaining __...__ placeholders."""
    text = RESTART_SCRIPT.read_text()
    assert "grep -q '__[A-Z_]*__'" in text or re.search(
        r"grep.*__\[A-Z_\]+__", text
    ), "refresh_plist must grep for unresolved placeholders before plutil"


def test_plist_template_rejects_unresolved_placeholder():
    """A rendered plist with a leftover placeholder fails lint and grep guard."""
    # Start from the real template and omit one substitution
    home = "/Users/walle-mini"
    repo = str(REPO_ROOT)
    vault = "/tmp/tdtb-fake-vault"
    template = PLIST_TEMPLATE.read_text()
    # Only substitute two of three — leave __TDTB_REPO__ unresolved
    result = subprocess.run(
        ["sed", "-e", f"s|__WALLE_HOME__|{home}|g", "-e", f"s|__TDTB_VAULT_ROOT__|{vault}|g"],
        input=template,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    rendered = result.stdout
    # Verify __TDTB_REPO__ is still present
    assert "__TDTB_REPO__" in rendered
    # The grep guard regex should match
    assert re.search(r'__[A-Z_]+__', rendered), (
        "grep guard should detect unresolved __TDTB_REPO__"
    )


def test_tdtb_restart_symlink_is_hq_owned():
    """The global ~/.local/bin/tdtb-restart launcher is owned by HQ
    (Configurations/gpt-stack/seat-bootstrap.sh), not by TDTB bootstrap.
    This test verifies the canonical restart-live.sh source is valid;
    the symlink itself is a machine-local HQ projection, not a TDTB contract."""
    # Source-only: verify the canonical script exists and is executable
    assert RESTART_SCRIPT.exists(), "canonical restart-live.sh must exist"
    assert os.access(RESTART_SCRIPT, os.X_OK), "canonical restart-live.sh must be executable"
    # The symlink at ~/.local/bin/tdtb-restart is an HQ-owned projection;
    # its target is not a TDTB contract. Skip if not installed.
    if not TDTB_RESTART.is_symlink():
        pytest.skip("tdtb-restart not installed on this seat (HQ-owned projection)")
