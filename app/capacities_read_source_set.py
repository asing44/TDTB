"""Operator CLI: switch the Capacities content read source (``auto`` | ``rest``).

``python -m capacities_read_source_set auto`` lets the refresh use the MCP
splitter, which still runs only when a valid custody file exists (U6b).
``python -m capacities_read_source_set rest`` is the kill switch: the sequential
REST-only read path, as before.

``save_settings`` replaces the whole policy, so this reads the stored policy and
saves it back unchanged with only ``read_source`` different. It refuses when no
settings document exists (there is nothing to preserve and nothing configured),
and never prints the policy itself. Operator-run only: the server never imports
it. Exit codes: 0 changed or already set, 2 failed, 3 refused.
"""

from __future__ import annotations

import argparse
import sys
from typing import Sequence, TextIO

import capacities_settings

EXIT_OK = 0
EXIT_FAILED = 2
EXIT_REFUSED = 3


def set_read_source(mode: str) -> str:
    """Persist ``mode`` keeping every other policy field; return a status word."""
    if mode not in capacities_settings.READ_SOURCE_MODES:
        raise ValueError("read_source must be 'auto' or 'rest'")
    read = capacities_settings.read_settings()
    if not read.persisted:
        return "refused"
    current = read.settings
    if current.read_source == mode and read.source_version == capacities_settings.SCHEMA_VERSION:
        return "unchanged"
    capacities_settings.save_settings(
        expected_revision=current.revision,
        statuses=current.statuses,
        types=current.types,
        native=current.native,
        excluded=current.excluded,
        read_source=mode,
    )
    return "changed"


def main(argv: Sequence[str] | None = None, stream: TextIO | None = None) -> int:
    out = stream if stream is not None else sys.stdout
    parser = argparse.ArgumentParser(
        prog="capacities_read_source_set",
        description="Set the Capacities content read source.",
    )
    parser.add_argument("mode", choices=sorted(capacities_settings.READ_SOURCE_MODES))
    args = parser.parse_args(argv)
    try:
        status = set_read_source(args.mode)
    except Exception as exc:  # noqa: BLE001 - report the class only, never content
        print(f"status: failed ({type(exc).__name__})", file=out)
        return EXIT_FAILED
    if status == "refused":
        print("status: refused (no Capacities settings document exists yet)", file=out)
        return EXIT_REFUSED
    print(f"read_source: {args.mode}", file=out)
    print(f"status: {status}", file=out)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
