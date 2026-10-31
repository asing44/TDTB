#!/usr/bin/env python3
"""capacities_csv_diff.py — diff a Capacities query CSV export against TDTB.

The owner's Capacities "Assigned" query is the human definition of what TDTB
should load, but Capacities exposes no API for a query's *results*: the only
supported escape hatch is the in-app export
(query -> ... -> Export -> CSV). This CLI proves TDTB mirrors that query by
comparing the exported titles against the rows the running app currently
loads.

It is strictly READ-ONLY: no writes anywhere, no credentials, and no
Capacities calls. The only network traffic is two tokenless local GETs against
the app itself.

Usage::

    python3 tools/capacities_csv_diff.py PATH.csv
    python3 tools/capacities_csv_diff.py PATH.csv --url http://127.0.0.1:8746
    python3 tools/capacities_csv_diff.py PATH.csv --json

The CSV is read with ``csv.DictReader`` (``utf-8-sig``, so a BOM is
tolerated; quoting, extra columns, and blank lines are handled). Column
detection is tolerant because no sample export exists:

* the TITLE column is the first header whose casefolded name is one of
  ``title``, ``name``, ``object``, ``object name``;
* the optional TYPE column is the first header whose casefolded name is one
  of ``type``, ``object type``, ``structure``, ``object type name``.

Subject titles are trimmed; empty titles are ignored and duplicate titles are
collapsed (the count is reported). Matching is by normalized title (casefold,
whitespace collapsed, stripped). When a TYPE column exists and both sides
carry a type, a title match with a differing type is reported as a MISMATCH
rather than a match.

TDTB rows come from ``GET {url}/plan-inputs``: rows under ``digest.assigned``
whose ``source`` casefolds to ``capacities``, each carrying ``name``,
``capacities_structure_id``, and ``capacities_assignment`` {mode, reasons}.
The structure id is mapped to a type title via ``GET {url}/settings/capacities``
-> ``structure_titles``; when that call fails or its shape differs, the id is
shown shortened to 8 characters.

Output is plain text sized for a narrow terminal — a summary line
``CSV n · TDTB n · matched n``, then the sections ``In the query but not
loaded``, ``Loaded but not in the query``, and ``Type mismatches`` (only when
non-empty). ``--json`` prints the same content as one object with the keys
``summary``, ``missing``, ``extra``, and ``mismatched``.

Exit codes: 0 no differences, 1 differences found, 2 the CSV could not be
read or has no title column, 3 the app could not be reached.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

DEFAULT_URL = "http://127.0.0.1:8746"
REQUEST_TIMEOUT_SECONDS = 10.0

TITLE_HEADERS = ("title", "name", "object", "object name")
TYPE_HEADERS = ("type", "object type", "structure", "object type name")

EXIT_OK = 0
EXIT_DIFFERENCES = 1
EXIT_BAD_CSV = 2
EXIT_UNREACHABLE = 3

SHORT_ID_LENGTH = 8


class CsvError(Exception):
    """The export could not be read, or carries no recognizable title column."""


class ServerError(Exception):
    """The local TDTB app could not be reached or answered with junk."""


def normalize_title(value: Any) -> str:
    """Casefold, collapse internal whitespace, strip — the matching key."""
    if value is None:
        return ""
    return " ".join(str(value).split()).casefold()


# ---------------------------------------------------------------------------
# CSV side
# ---------------------------------------------------------------------------

def _pick_column(fieldnames: list[str] | None, candidates: tuple[str, ...]) -> str | None:
    """First header whose casefolded name is in ``candidates``."""
    for name in fieldnames or []:
        if name is None:
            continue
        if name.strip().casefold() in candidates:
            return name
    return None


def read_query_csv(path: str) -> tuple[list[dict[str, str | None]], dict[str, int]]:
    """Parse the export into ``[{"title", "type"}]`` plus counters.

    Duplicates are collapsed by normalized title (first occurrence wins, so a
    later duplicate type never rewrites an earlier one).
    """
    try:
        raw = Path(path).read_bytes()
    except OSError as exc:
        raise CsvError(f"capacities_csv_diff: cannot read {path}: {exc}") from exc

    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise CsvError(f"capacities_csv_diff: {path} is not UTF-8: {exc}") from exc

    try:
        reader = csv.DictReader(io.StringIO(text, newline=""))
        fieldnames = list(reader.fieldnames or [])
        title_column = _pick_column(fieldnames, TITLE_HEADERS)
        if title_column is None:
            seen = ", ".join(repr(name) for name in fieldnames) or "(none)"
            raise CsvError(
                "capacities_csv_diff: no title column (looking for "
                f"{', '.join(TITLE_HEADERS)}); headers seen: {seen}"
            )
        type_column = _pick_column(fieldnames, TYPE_HEADERS)
        if type_column == title_column:
            type_column = None

        rows: dict[str, dict[str, str | None]] = {}
        blank = 0
        duplicates = 0
        for record in reader:
            title = (record.get(title_column) or "").strip()
            if not title:
                blank += 1
                continue
            type_value = (record.get(type_column) or "").strip() if type_column else ""
            key = normalize_title(title)
            if key in rows:
                duplicates += 1
                continue
            rows[key] = {"title": title, "type": type_value or None}
    except csv.Error as exc:
        raise CsvError(f"capacities_csv_diff: {path} is not parseable CSV: {exc}") from exc

    counters = {
        "rows": len(rows),
        "blank": blank,
        "duplicates": duplicates,
        "has_type": 1 if type_column else 0,
    }
    return list(rows.values()), counters


# ---------------------------------------------------------------------------
# TDTB side
# ---------------------------------------------------------------------------

def _get_json(url: str) -> Any:
    request = urllib.request.Request(url, method="GET")
    # Deliberately no Authorization header: /plan-inputs and
    # /settings/capacities are tokenless local reads.
    with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
        payload = response.read()
    return json.loads(payload.decode("utf-8"))


def fetch_plan_inputs(url: str) -> dict[str, Any]:
    """GET ``{url}/plan-inputs`` — the app's read-only plan input assembly."""
    try:
        payload = _get_json(f"{url.rstrip('/')}/plan-inputs")
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise ServerError(
            f"capacities_csv_diff: cannot read {url.rstrip('/')}/plan-inputs: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise ServerError(
            f"capacities_csv_diff: {url.rstrip('/')}/plan-inputs did not return an object"
        )
    return payload


def fetch_structure_titles(url: str) -> dict[str, str]:
    """GET ``{url}/settings/capacities`` -> ``structure_titles``, fail-soft.

    Any failure or unexpected shape yields ``{}`` so the caller falls back to
    a shortened structure id rather than failing the whole diff.
    """
    try:
        payload = _get_json(f"{url.rstrip('/')}/settings/capacities")
    except (urllib.error.URLError, OSError, ValueError):
        return {}
    if not isinstance(payload, dict):
        return {}
    titles = payload.get("structure_titles")
    if not isinstance(titles, dict):
        return {}
    return {
        str(key): str(value)
        for key, value in titles.items()
        if isinstance(key, str) and isinstance(value, str) and value.strip()
    }


def _short_structure_id(structure_id: str) -> str:
    text = str(structure_id or "").strip()
    return text[:SHORT_ID_LENGTH] if text else ""


def loaded_rows(
    payload: dict[str, Any], structure_titles: dict[str, str]
) -> list[dict[str, str | None]]:
    """Capacities rows from ``digest.assigned``, with their type and reason."""
    digest = payload.get("digest")
    assigned = digest.get("assigned") if isinstance(digest, dict) else None
    if not isinstance(assigned, list):
        return []

    rows: list[dict[str, str | None]] = []
    for row in assigned:
        if not isinstance(row, dict):
            continue
        if str(row.get("source") or "").casefold() != "capacities":
            continue
        name = str(row.get("name") or "").strip()
        if not name:
            continue
        structure_id = str(row.get("capacities_structure_id") or "").strip()
        type_value = structure_titles.get(structure_id) or _short_structure_id(structure_id)
        assignment = row.get("capacities_assignment")
        reasons = assignment.get("reasons") if isinstance(assignment, dict) else None
        reason = ""
        if isinstance(reasons, list):
            for item in reasons:
                text = str(item or "").strip()
                if text:
                    reason = text
                    break
        rows.append({"title": name, "type": type_value or None, "reason": reason or None})
    return rows


# ---------------------------------------------------------------------------
# Diff
# ---------------------------------------------------------------------------

def build_diff(
    csv_rows: list[dict[str, str | None]],
    tdtb_rows: list[dict[str, str | None]],
    counters: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Classify the two sides into matched / missing / extra / mismatched."""
    counters = counters or {}
    loaded: dict[str, dict[str, str | None]] = {}
    for row in tdtb_rows:
        loaded.setdefault(normalize_title(row.get("title")), row)

    query: dict[str, dict[str, str | None]] = {}
    for row in csv_rows:
        query.setdefault(normalize_title(row.get("title")), row)

    matched = 0
    missing: list[dict[str, str | None]] = []
    mismatched: list[dict[str, str | None]] = []

    for key, csv_row in query.items():
        tdtb_row = loaded.get(key)
        if tdtb_row is None:
            missing.append({"title": csv_row["title"], "type": csv_row["type"]})
            continue
        csv_type = (csv_row.get("type") or "").strip()
        tdtb_type = (tdtb_row.get("type") or "").strip()
        if (
            counters.get("has_type")
            and csv_type
            and tdtb_type
            and csv_type.casefold() != tdtb_type.casefold()
        ):
            mismatched.append(
                {
                    "title": csv_row["title"],
                    "csv_type": csv_type,
                    "tdtb_type": tdtb_type,
                }
            )
            continue
        matched += 1

    extra: list[dict[str, str | None]] = [
        {
            "title": row["title"],
            "type": row.get("type"),
            "reason": row.get("reason"),
        }
        for key, row in loaded.items()
        if key not in query
    ]

    return {
        "summary": {
            "csv": len(query),
            "tdtb": len(loaded),
            "matched": matched,
            "duplicates": int(counters.get("duplicates") or 0),
        },
        "missing": sorted(missing, key=lambda item: normalize_title(item["title"])),
        "extra": sorted(extra, key=lambda item: normalize_title(item["title"])),
        "mismatched": sorted(mismatched, key=lambda item: normalize_title(item["title"])),
    }


def has_differences(result: dict[str, Any]) -> bool:
    return bool(result["missing"] or result["extra"] or result["mismatched"])


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def _with_type(title: Any, type_value: Any) -> str:
    text = str(title or "")
    type_text = str(type_value or "").strip()
    return f"{text}  [{type_text}]" if type_text else text


def render_text(result: dict[str, Any]) -> str:
    summary = result["summary"]
    lines = [
        "CSV {csv} · TDTB {tdtb} · matched {matched}".format(**summary),
    ]
    if summary.get("duplicates"):
        lines.append(f"Duplicates collapsed  {summary['duplicates']}")

    if result["missing"]:
        lines.append("")
        lines.append("In the query but not loaded")
        lines.extend(f"  {_with_type(row['title'], row['type'])}" for row in result["missing"])

    if result["extra"]:
        lines.append("")
        lines.append("Loaded but not in the query")
        for row in result["extra"]:
            line = f"  {_with_type(row['title'], row['type'])}"
            reason = str(row.get("reason") or "").strip()
            lines.append(f"{line}  {reason}" if reason else line)

    if result["mismatched"]:
        lines.append("")
        lines.append("Type mismatches")
        lines.extend(
            "  {title}  CSV: {csv_type} · TDTB: {tdtb_type}".format(**row)
            for row in result["mismatched"]
        )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(
    argv: list[str] | None = None,
    *,
    load_plan_inputs: Callable[[str], dict[str, Any]] | None = None,
    load_structure_titles: Callable[[str], dict[str, str]] | None = None,
) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Diff a Capacities query CSV export against the rows TDTB currently "
            "loads (read-only)."
        )
    )
    parser.add_argument("path", help="Capacities query CSV export to compare")
    parser.add_argument(
        "--url", default=DEFAULT_URL, help=f"TDTB base URL (default: {DEFAULT_URL})"
    )
    parser.add_argument("--json", action="store_true", help="print one JSON object")
    args = parser.parse_args(argv)

    try:
        csv_rows, counters = read_query_csv(args.path)
    except CsvError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_BAD_CSV

    fetch_inputs = load_plan_inputs or fetch_plan_inputs
    fetch_titles = load_structure_titles or fetch_structure_titles

    try:
        payload = fetch_inputs(args.url)
    except ServerError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_UNREACHABLE
    except Exception as exc:  # noqa: BLE001 — never leak a traceback
        print(f"capacities_csv_diff: cannot read {args.url}: {exc}", file=sys.stderr)
        return EXIT_UNREACHABLE

    try:
        titles = fetch_titles(args.url) or {}
    except Exception:  # noqa: BLE001 — structure titles are cosmetic
        titles = {}
    if not isinstance(titles, dict):
        titles = {}

    result = build_diff(csv_rows, loaded_rows(payload, titles), counters)

    if args.json:
        json.dump(result, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
    else:
        print(render_text(result))

    return EXIT_DIFFERENCES if has_differences(result) else EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
