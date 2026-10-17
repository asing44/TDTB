#!/usr/bin/env python3
"""micro_ideas_spec.py — print the Capacities micro-adventure fetch spec.

The ``tdtb-refresh`` skill (``skills/tdtb-refresh/SKILL.md``) must list and read
Capacities objects from ONE place: the operator's own config. This CLI prints
that spec as JSON so the agent never guesses a collection name, a tag, or an
effort value, and so the producer and the agent agree on one shape:

    {"enabled": true,
     "collection": "Personal Adventures",
     "tag": "_TDTB-live",
     "effort_property": "effort",
     "effort_allowed": ["small"]}

It only READS the folded ``Micro-Adventures`` -> ``Capacities`` settings (the
same loader the app and the producer use, honouring ``TDTB_HOME``); it never
writes, never calls Capacities, and needs no MCP.

Usage::

    python tools/micro_ideas_spec.py
    python tools/micro_ideas_spec.py --config ~/.config/tdtb/config.json

Exit codes: 0 the spec printed, 6 Capacities micro-adventure ideas are disabled,
7 enabled but neither a collection nor a tag is configured, 2 the config could
not be read. Every non-zero exit explains the fix on stderr.
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
import producer_rules  # noqa: E402

EXIT_DISABLED = 6
EXIT_NOT_CONFIGURED = 7
EXIT_UNREADABLE = 2


def _section(path: str | None):
    """The folded config sections, or the ones from an explicit file."""
    if path is None:
        return app_config.load_sections()
    document = app_config.read_document_file(path)
    if document is None:
        return None
    return app_config.sections_from_document(document)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Print the Capacities micro-adventure fetch spec (collection, tag, "
            "effort property and allowed effort values) from the app config."
        )
    )
    parser.add_argument(
        "--config", default=None,
        help="Config file to read instead of the app home's layered config",
    )
    args = parser.parse_args(argv)

    sections = _section(args.config)
    if sections is None:
        print(
            f"micro_ideas_spec: cannot read config at {args.config}", file=sys.stderr
        )
        return EXIT_UNREADABLE

    settings = producer_rules.micro_ideas_settings(sections)
    if settings["enabled"] is not True:
        print(
            "micro_ideas_spec: Capacities micro-adventure ideas are disabled "
            "(micro_adventure_capacities.enabled is not true in config.json); "
            "nothing to fetch.",
            file=sys.stderr,
        )
        return EXIT_DISABLED
    if not settings["collection"] and not settings["tag"]:
        print(
            "micro_ideas_spec: Capacities micro-adventure ideas are not "
            "configured (set micro_adventure_capacities.collection or .tag); "
            "nothing to fetch.",
            file=sys.stderr,
        )
        return EXIT_NOT_CONFIGURED

    json.dump(settings, sys.stdout, indent=2, sort_keys=True, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
