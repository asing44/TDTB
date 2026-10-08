#!/usr/bin/env python3
"""migrate_vault_config.py — one-shot vault config → `~/.config/tdtb/config.json`.

Run EXPLICITLY by a human, once, when moving to the Capacities-first config
home. This script is NEVER imported or called at app startup; nothing in `app/`
depends on it.

It reads the vault config markdown (`00 - META/Skill-Configs/tdtb-bridger.md`
under `--vault-root`) once, translates the sections config.json v1 carries,
and writes the result to `<app home>/config.json` (or `--out`). The vault file
is read-only here: it is never written, truncated, or deleted.

Usage::

    python tools/migrate_vault_config.py --vault-root "/path/to/vault"
    python tools/migrate_vault_config.py --vault-root "/path/to/vault" --out ./config.json

The app home honours `TDTB_HOME` (see `app/app_config.py`).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_APP_DIR = Path(__file__).resolve().parent.parent / "app"
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))

import app_config  # noqa: E402
from config_reader import CONFIG_REL_PATH, parse_config_markdown  # noqa: E402


def migrate(vault_root: str | Path, out_path: str | Path | None = None) -> Path:
    """Parse the vault config under `vault_root` and write `config.json`.

    Returns the written path. The vault file is only read."""
    source = Path(vault_root) / CONFIG_REL_PATH
    text = source.read_text(encoding="utf-8")
    sections = parse_config_markdown(text)
    document = app_config.document_from_sections(sections)

    target = Path(out_path) if out_path is not None else app_config.config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(document, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="One-shot migration of the vault config into ~/.config/tdtb/config.json."
    )
    parser.add_argument(
        "--vault-root",
        required=True,
        help="Vault root containing 00 - META/Skill-Configs/tdtb-bridger.md",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Output path (default: <app home>/config.json, honouring TDTB_HOME)",
    )
    args = parser.parse_args(argv)

    target = migrate(args.vault_root, args.out)
    print(f"wrote {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
