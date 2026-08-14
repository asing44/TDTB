"""Tests for schema_guard.py — predicate-hash drift guard (T8)."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import schema_guard  # noqa: E402


BASE_A = "00 - META/Bases/assignment-pipeline.base"
BASE_B = "00 - META/Bases/daily-assigned.base"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _make_vault(tmp_path: Path, content_a: bytes = b"filters: a", content_b: bytes = b"filters: b") -> Path:
    vault = tmp_path / "vault"
    bases = vault / "00 - META" / "Bases"
    bases.mkdir(parents=True)
    if content_a is not None:
        (bases / "assignment-pipeline.base").write_bytes(content_a)
    if content_b is not None:
        (bases / "daily-assigned.base").write_bytes(content_b)
    return vault


def _write_hashes(hashes_path: Path, entries: dict) -> None:
    hashes_path.write_text(json.dumps(entries, indent=2, sort_keys=True) + "\n", encoding="utf-8")


# --- check_drift ---


def test_check_drift_clean(tmp_path):
    vault = _make_vault(tmp_path)
    hashes_path = tmp_path / "schema-hashes.json"
    entries = {
        BASE_A: {"sha256": _sha(b"filters: a"), "recorded": "2026-01-01"},
        BASE_B: {"sha256": _sha(b"filters: b"), "recorded": "2026-01-01"},
    }
    _write_hashes(hashes_path, entries)

    findings = schema_guard.check_drift(vault, hashes_path)
    assert findings == []


def test_check_drift_drifted_file(tmp_path):
    vault = _make_vault(tmp_path, content_a=b"filters: CHANGED")
    hashes_path = tmp_path / "schema-hashes.json"
    old_hash = _sha(b"filters: a")
    entries = {
        BASE_A: {"sha256": old_hash, "recorded": "2026-01-01"},
        BASE_B: {"sha256": _sha(b"filters: b"), "recorded": "2026-01-01"},
    }
    _write_hashes(hashes_path, entries)

    findings = schema_guard.check_drift(vault, hashes_path)
    assert len(findings) == 1
    finding = findings[0]
    assert finding.path == BASE_A
    assert finding.status == "drifted"
    assert finding.recorded_hash == old_hash
    assert finding.current_hash == _sha(b"filters: CHANGED")
    assert finding.current_hash != finding.recorded_hash


def test_check_drift_missing_file(tmp_path):
    vault = _make_vault(tmp_path, content_a=None)
    hashes_path = tmp_path / "schema-hashes.json"
    recorded_hash = _sha(b"filters: a")
    entries = {
        BASE_A: {"sha256": recorded_hash, "recorded": "2026-01-01"},
        BASE_B: {"sha256": _sha(b"filters: b"), "recorded": "2026-01-01"},
    }
    _write_hashes(hashes_path, entries)

    findings = schema_guard.check_drift(vault, hashes_path)
    assert len(findings) == 1
    finding = findings[0]
    assert finding.path == BASE_A
    assert finding.status == "missing"
    assert finding.recorded_hash == recorded_hash


def test_check_drift_unrecorded_file(tmp_path):
    vault = _make_vault(tmp_path)
    hashes_path = tmp_path / "schema-hashes.json"
    # Only record one of the two guarded sources.
    entries = {
        BASE_A: {"sha256": _sha(b"filters: a"), "recorded": "2026-01-01"},
    }
    _write_hashes(hashes_path, entries)

    findings = schema_guard.check_drift(vault, hashes_path)
    assert len(findings) == 1
    finding = findings[0]
    assert finding.path == BASE_B
    assert finding.status == "unrecorded"
    assert finding.current_hash == _sha(b"filters: b")


def test_check_drift_missing_hashes_file(tmp_path):
    # No schema-hashes.json at all -> every present source is unrecorded.
    vault = _make_vault(tmp_path)
    hashes_path = tmp_path / "does-not-exist.json"

    findings = schema_guard.check_drift(vault, hashes_path)
    statuses = {f.path: f.status for f in findings}
    assert statuses == {BASE_A: "unrecorded", BASE_B: "unrecorded"}


# --- record + round trip ---


def test_record_then_check_clean_round_trip(tmp_path):
    vault = _make_vault(tmp_path)
    hashes_path = tmp_path / "schema-hashes.json"
    assert not hashes_path.exists()

    written = schema_guard.record(vault, hashes_path)
    assert set(written.keys()) == {BASE_A, BASE_B}
    assert hashes_path.exists()

    findings = schema_guard.check_drift(vault, hashes_path)
    assert findings == []


def test_record_after_drift_reconciles(tmp_path):
    vault = _make_vault(tmp_path)
    hashes_path = tmp_path / "schema-hashes.json"
    stale_entries = {
        BASE_A: {"sha256": "0" * 64, "recorded": "2020-01-01"},
        BASE_B: {"sha256": "0" * 64, "recorded": "2020-01-01"},
    }
    _write_hashes(hashes_path, stale_entries)

    findings_before = schema_guard.check_drift(vault, hashes_path)
    assert len(findings_before) == 2
    assert all(f.status == "drifted" for f in findings_before)

    schema_guard.record(vault, hashes_path)
    findings_after = schema_guard.check_drift(vault, hashes_path)
    assert findings_after == []


# --- warn_on_drift ---


def test_warn_on_drift_logs_and_never_raises(tmp_path, caplog):
    vault = _make_vault(tmp_path, content_a=b"filters: CHANGED")
    hashes_path = tmp_path / "schema-hashes.json"
    entries = {
        BASE_A: {"sha256": _sha(b"filters: a"), "recorded": "2026-01-01"},
        BASE_B: {"sha256": _sha(b"filters: b"), "recorded": "2026-01-01"},
    }
    _write_hashes(hashes_path, entries)

    with caplog.at_level("WARNING"):
        findings = schema_guard.warn_on_drift(vault, hashes_path)

    assert len(findings) == 1
    assert any("drifted" in rec.message.lower() or "DRIFTED" in rec.message for rec in caplog.records)


def test_warn_on_drift_swallows_errors(tmp_path, caplog, monkeypatch):
    # Point vault_root at something that will blow up inside check_drift by
    # making hashes_path a directory (json.load on a dir raises).
    vault = _make_vault(tmp_path)
    bad_hashes_path = tmp_path  # a directory, not a file

    with caplog.at_level("WARNING"):
        findings = schema_guard.warn_on_drift(vault, bad_hashes_path)

    # Must never raise; on internal failure it returns an empty list.
    assert findings == []


def test_warn_on_drift_clean_produces_no_warnings(tmp_path, caplog):
    vault = _make_vault(tmp_path)
    hashes_path = tmp_path / "schema-hashes.json"
    schema_guard.record(vault, hashes_path)

    with caplog.at_level("WARNING"):
        findings = schema_guard.warn_on_drift(vault, hashes_path)

    assert findings == []
    assert not any(rec.levelname == "WARNING" for rec in caplog.records)


# --- CLI ---


def _run_cli(*args: str) -> subprocess.CompletedProcess:
    script = Path(__file__).resolve().parents[1] / "schema_guard.py"
    python = sys.executable
    return subprocess.run(
        [python, str(script), *args],
        capture_output=True,
        text=True,
    )


def test_cli_check_drift_exit_0_clean(tmp_path):
    vault = _make_vault(tmp_path)
    hashes_path = tmp_path / "schema-hashes.json"
    schema_guard.record(vault, hashes_path)

    result = _run_cli("--vault-root", str(vault), "--hashes-path", str(hashes_path), "--check-drift")
    assert result.returncode == 0
    assert "clean" in result.stdout.lower()


def test_cli_check_drift_exit_1_on_drift(tmp_path):
    vault = _make_vault(tmp_path, content_a=b"filters: CHANGED")
    hashes_path = tmp_path / "schema-hashes.json"
    entries = {
        BASE_A: {"sha256": _sha(b"filters: a"), "recorded": "2026-01-01"},
        BASE_B: {"sha256": _sha(b"filters: b"), "recorded": "2026-01-01"},
    }
    _write_hashes(hashes_path, entries)

    result = _run_cli("--vault-root", str(vault), "--hashes-path", str(hashes_path), "--check-drift")
    assert result.returncode == 1
    assert "DRIFTED" in result.stdout
    assert BASE_A in result.stdout


def test_cli_record_exit_0_and_writes_file(tmp_path):
    vault = _make_vault(tmp_path)
    hashes_path = tmp_path / "schema-hashes.json"
    assert not hashes_path.exists()

    result = _run_cli("--vault-root", str(vault), "--hashes-path", str(hashes_path), "--record")
    assert result.returncode == 0
    assert hashes_path.exists()

    data = json.loads(hashes_path.read_text())
    assert set(data.keys()) == {BASE_A, BASE_B}


def test_cli_requires_one_of_check_drift_or_record(tmp_path):
    vault = _make_vault(tmp_path)
    result = _run_cli("--vault-root", str(vault))
    assert result.returncode != 0


def test_cli_default_hashes_path_is_next_to_module():
    assert schema_guard.DEFAULT_HASHES_PATH.parent == Path(__file__).resolve().parents[1]
    assert schema_guard.DEFAULT_HASHES_PATH.name == "schema-hashes.json"
