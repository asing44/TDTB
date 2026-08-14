"""G23: run the node --test suite over static/timeline_logic.js from pytest,
so `pytest` alone exercises the JS timeline math (the manual-seed defects
shipped because that logic sat outside the Python suite). Skips when node
isn't installed rather than failing the whole suite."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

APP_DIR = Path(__file__).parent.parent


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_timeline_logic_js_suite():
    test_files = sorted(
        str(p.relative_to(APP_DIR)) for p in (APP_DIR / "tests" / "js").glob("*.test.mjs")
    )
    assert test_files, "no JS test files found under tests/js/"
    proc = subprocess.run(
        ["node", "--test", *test_files],
        cwd=APP_DIR,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, (
        f"node --test failed:\n{proc.stdout}\n{proc.stderr}"
    )
