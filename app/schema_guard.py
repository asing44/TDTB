"""Predicate-hash drift guard.

The app's compiled predicates (inventory filters, digest ranking) are derived
from two Obsidian `.base` files in the vault. If those source files change,
the app's hardcoded logic can silently drift out of sync with the schema they
were compiled from. This module stores a sha256 of each guarded source at
compile time and compares it at startup / on demand — it warns loudly on
mismatch but never blocks (matches the plan's drift-check semantics: T8).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

# Vault-relative paths to the .base files the app's compiled predicates were
# derived from. Never hardcode a vault root alongside these — vault_root is
# always a parameter.
GUARDED_SOURCES: tuple[str, ...] = (
    "00 - META/Bases/assignment-pipeline.base",
    "00 - META/Bases/daily-assigned.base",
)

Status = str  # "ok" | "drifted" | "missing" | "unrecorded"


@dataclass(frozen=True)
class DriftFinding:
    path: str
    status: Status
    recorded_hash: str | None = None
    current_hash: str | None = None

    def __str__(self) -> str:  # human-readable line for CLI/log output
        if self.status == "ok":
            return f"ok        {self.path}"
        if self.status == "drifted":
            return (
                f"DRIFTED   {self.path}\n"
                f"          recorded: {self.recorded_hash}\n"
                f"          current:  {self.current_hash}"
            )
        if self.status == "missing":
            return f"MISSING   {self.path} (recorded hash {self.recorded_hash}, source file not found)"
        if self.status == "unrecorded":
            return f"UNRECORDED {self.path} (current hash {self.current_hash}, not in {SCHEMA_HASHES_FILENAME})"
        return f"UNKNOWN   {self.path} (status={self.status})"


SCHEMA_HASHES_FILENAME = "schema-hashes.json"
DEFAULT_HASHES_PATH = Path(__file__).parent / SCHEMA_HASHES_FILENAME


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_hashes(hashes_path: Path) -> dict:
    if not hashes_path.exists():
        return {}
    with hashes_path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _write_hashes(hashes_path: Path, data: dict) -> None:
    with hashes_path.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
        fh.write("\n")


def check_drift(
    vault_root: str | Path,
    hashes_path: str | Path = DEFAULT_HASHES_PATH,
) -> list[DriftFinding]:
    """Compare guarded .base sources against recorded hashes.

    Returns a list of DriftFinding — one per guarded source that is NOT
    clean (drifted/missing), plus one per recorded-but-unmatched entry that
    isn't a guarded source (unrecorded is for guarded sources present in the
    vault but absent from the hashes file). An empty list means everything
    checked out clean.
    """
    vault_root = Path(vault_root)
    hashes_path = Path(hashes_path)
    recorded = _load_hashes(hashes_path)

    findings: list[DriftFinding] = []
    for rel_path in GUARDED_SOURCES:
        source_path = vault_root / rel_path
        entry = recorded.get(rel_path)
        source_exists = source_path.exists()

        if entry is None:
            if source_exists:
                current = _sha256_file(source_path)
                findings.append(DriftFinding(rel_path, "unrecorded", current_hash=current))
            # Not recorded and not present: nothing to say — never guarded.
            continue

        recorded_hash = entry.get("sha256")

        if not source_exists:
            findings.append(DriftFinding(rel_path, "missing", recorded_hash=recorded_hash))
            continue

        current = _sha256_file(source_path)
        if current != recorded_hash:
            findings.append(
                DriftFinding(rel_path, "drifted", recorded_hash=recorded_hash, current_hash=current)
            )
        # else: ok — clean, not included in findings.

    return findings


def record(
    vault_root: str | Path,
    hashes_path: str | Path = DEFAULT_HASHES_PATH,
) -> dict:
    """Recompute hashes for every guarded source present in the vault and
    rewrite the hashes file. This is the "I reviewed the .base change,
    re-baseline" action — it does not warn, it just re-records.

    Sources that are missing from the vault are dropped from the recorded
    set (so a subsequent check_drift treats them as unrecorded rather than
    a stale missing entry, if they later reappear).
    """
    vault_root = Path(vault_root)
    hashes_path = Path(hashes_path)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    new_data: dict = {}
    for rel_path in GUARDED_SOURCES:
        source_path = vault_root / rel_path
        if not source_path.exists():
            continue
        new_data[rel_path] = {
            "sha256": _sha256_file(source_path),
            "recorded": now,
        }

    _write_hashes(hashes_path, new_data)
    return new_data


def warn_on_drift(
    vault_root: str | Path,
    hashes_path: str | Path = DEFAULT_HASHES_PATH,
) -> list[DriftFinding]:
    """Startup integration point — main.py calls this. Logs warnings for
    every finding, never raises. Returns the findings for callers that want
    to inspect them further.
    """
    try:
        findings = check_drift(vault_root, hashes_path)
    except Exception:  # noqa: BLE001 - a drift-check failure must never crash startup
        logger.exception("schema_guard: drift check failed to run")
        return []

    for finding in findings:
        logger.warning("schema_guard: %s", str(finding).replace("\n", " | "))

    return findings


def _cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Vault .base schema drift guard")
    parser.add_argument("--vault-root", required=True, help="Path to the vault root")
    parser.add_argument(
        "--hashes-path",
        default=str(DEFAULT_HASHES_PATH),
        help="Path to schema-hashes.json (default: next to schema_guard.py)",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check-drift", action="store_true", help="Check for drift, exit 1 if any found")
    group.add_argument("--record", action="store_true", help="Recompute + rewrite recorded hashes")

    args = parser.parse_args(argv)

    if args.record:
        data = record(args.vault_root, args.hashes_path)
        print(f"Recorded {len(data)} guarded source hash(es) to {args.hashes_path}")
        for rel_path, entry in sorted(data.items()):
            print(f"  {rel_path}: {entry['sha256']} ({entry['recorded']})")
        return 0

    findings = check_drift(args.vault_root, args.hashes_path)
    if not findings:
        print("schema_guard: clean — no drift detected")
        return 0

    print(f"schema_guard: {len(findings)} finding(s)")
    for finding in findings:
        print(str(finding))
    return 1


if __name__ == "__main__":
    sys.exit(_cli())
