#!/usr/bin/env python3
"""migrate_vault_state.py — one-shot vault state -> app-home state (S1).

Run EXPLICITLY by a human, once, when moving the five app-owned stores out of
the vault and into ``<app home>/state/``. This script is NEVER imported or
called at app startup; nothing in ``app/`` depends on it.

It copies the pre-S1 vault copies of the five stores into the app home:

  - ``tdtb-runstate-<date>.md``         -> ``state/runstate/``
  - ``tdtb-recent-selections.md``       -> ``state/runstate/``
  - ``tdtb-digest-index-<date>.json``   -> ``state/runstate/``
  - ``tdtb-exclusion-settings.json``    -> ``state/exclusions.json``
  - ``tdtb-capacities-settings.json``   -> ``state/capacities-settings.json``
  - ``tdtb-capacities-source.json``     -> ``state/capacities-source.json``
  - ``tdtb-deferrals.json``             -> ``state/deferrals.json``

Contract (see ``app/tests/test_migrate_vault_state.py``):

  - every vault file is left byte-identical — the vault stays the rollback
    point;
  - a destination that already exists is never overwritten (``kept``);
  - a store with no vault source is reported ``missing`` and not created;
  - the 0-byte runtime ``.lock`` artifacts are never copied;
  - ``--dry-run`` reports what it would do and writes nothing.

Usage::

    python tools/migrate_vault_state.py --vault-root "/path/to/vault"
    python tools/migrate_vault_state.py --vault-root "/path/to/vault" \
        --state-dir /tmp/state --dry-run

The app home honours ``TDTB_HOME`` (see ``app/app_config.py``).
"""
from __future__ import annotations

import argparse
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

_APP_DIR = Path(__file__).resolve().parent.parent / "app"
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))

import app_config  # noqa: E402
import capacities_builder  # noqa: E402
import capacities_settings  # noqa: E402
import deferrals  # noqa: E402
import exclusion_settings  # noqa: E402
import runstate  # noqa: E402

#: Pre-S1 vault cache directory (the source of every migrated store).
CACHE_REL = runstate.CACHE_DIR_REL

#: Dated runstate-owned stores discovered by glob: file-name glob -> the
#: ``state/`` subdirectory they migrate into.
_DATED_STORES = (
    ("tdtb-runstate-*.md", "runstate"),
    ("tdtb-digest-index-*.json", "runstate"),
)

#: Fixed-name stores: (source vault file name, destination path under
#: ``state/``). Referencing the module constants keeps the tool honest if a
#: legacy path or state filename ever changes.
_FIXED_STORES = (
    (
        Path(exclusion_settings.SETTINGS_REL_PATH).name,
        exclusion_settings.STATE_FILENAME,
    ),
    (
        Path(capacities_settings.SETTINGS_REL_PATH).name,
        capacities_settings.STATE_FILENAME,
    ),
    (
        Path(capacities_builder.SOURCE_REL_PATH).name,
        capacities_builder.STATE_FILENAME,
    ),
    (Path(deferrals.DEFERRALS_REL_PATH).name, deferrals.STATE_FILENAME),
    (
        Path(runstate.RECENT_SELECTIONS_REL_PATH).name,
        f"{runstate.STATE_RUNSTATE_DIRNAME}/{Path(runstate.RECENT_SELECTIONS_REL_PATH).name}",
    ),
)


@dataclass(frozen=True)
class MigrationResult:
    """Outcome of migrating one store.

    ``name`` is the pre-S1 vault cache file name (the stable identifier the
    report and tests key on); ``status`` is one of ``copied``, ``kept``,
    ``missing`` or ``would-copy``.
    """

    name: str
    status: str


def _plan(vault_root: Path) -> list[tuple[str, Path, Path]]:
    """Every store to consider: ``(name, source, destination-relative)``.

    Dated stores are discovered by glob; fixed-name stores are always listed,
    even when absent, so a missing store is reported rather than silently
    skipped. Lock artifacts are deliberately not part of the plan."""
    cache = vault_root / CACHE_REL
    plan: list[tuple[str, Path, Path]] = []
    if cache.is_dir():
        for pattern, subdir in _DATED_STORES:
            for source in sorted(cache.glob(pattern)):
                plan.append((source.name, source, Path(subdir) / source.name))
    for name, dest_rel in _FIXED_STORES:
        plan.append((name, cache / name, Path(dest_rel)))
    return plan


def migrate(
    vault_root: str | Path,
    state_dir: str | Path | None = None,
    *,
    dry_run: bool = False,
) -> list[MigrationResult]:
    """Copy the pre-S1 vault stores into ``state_dir``.

    ``state_dir`` defaults to ``app_config.state_dir()`` (honours
    ``TDTB_HOME``). Returns one :class:`MigrationResult` per store. The vault
    is only ever read."""
    vault = Path(vault_root)
    state = Path(state_dir) if state_dir is not None else app_config.state_dir()

    results: list[MigrationResult] = []
    for name, source, dest_rel in _plan(vault):
        if not source.is_file():
            results.append(MigrationResult(name, "missing"))
            continue
        dest = state / dest_rel
        if dest.exists():
            results.append(MigrationResult(name, "kept"))
            continue
        if dry_run:
            results.append(MigrationResult(name, "would-copy"))
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        # Byte-for-byte copy; no metadata is migrated.
        shutil.copyfile(source, dest)
        results.append(MigrationResult(name, "copied"))
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "One-shot migration of the pre-S1 vault stores into "
            "<app home>/state/ (honours TDTB_HOME)."
        )
    )
    parser.add_argument(
        "--vault-root",
        required=True,
        help="Vault root containing 00 - META/Cache",
    )
    parser.add_argument(
        "--state-dir",
        default=None,
        help="Destination state directory (default: <app home>/state, honouring TDTB_HOME)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be copied and write nothing",
    )
    args = parser.parse_args(argv)

    state = Path(args.state_dir) if args.state_dir else app_config.state_dir()
    results = migrate(args.vault_root, state_dir=state, dry_run=args.dry_run)

    counts: dict[str, int] = {}
    for result in results:
        counts[result.status] = counts.get(result.status, 0) + 1
        print(f"{result.status:>9}  {result.name}")

    print(f"state dir: {state}")
    print(
        f"copied {counts.get('copied', 0)}, "
        f"kept {counts.get('kept', 0)}, "
        f"missing {counts.get('missing', 0)}, "
        f"would-copy {counts.get('would-copy', 0)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
