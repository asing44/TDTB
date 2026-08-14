"""
inventory.py — active-inventory.md (v2) consumer + rewriter for the TDTB app.

Reads/writes the shared cache at ``00 - META/Cache/active-inventory.md``
(vault-relative; vault_root is always a caller-supplied argument — never
hardcoded, per spec locked decision 2).

Hard schema guard (spec locked decision 6): a missing file, unparseable
frontmatter, ``schema_version != 2``, or an absent/empty ``parents`` list is
a REFUSAL, never a guess and never a partial pool. ``InventoryResult.ok``
distinguishes the good path; ``InventoryResult.reason`` names the miss.

Frontmatter parsing/emission deliberately reuses gather's patterns
(``tdtb_gather.parse_frontmatter``, its frontmatter-only markdown shape, and
its 2am-boundary ``effective_date``) rather than inventing a second YAML
style for the same cache file. ``gather/tdtb_gather.py`` is not imported as a
package (it has no ``__init__.py`` — mirrors its own test suite's
``sys.path.insert`` + flat-module-import convention) and is never modified.
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

_GATHER_DIR = str(Path(__file__).parent / "gather")
if _GATHER_DIR not in sys.path:
    sys.path.insert(0, _GATHER_DIR)

import tdtb_gather as gather  # noqa: E402  (path-shimmed import, see above)

CACHE_REL_PATH = gather.CACHE_REL_PATH
CACHE_SCHEMA_VERSION = gather.CACHE_SCHEMA_VERSION
effective_date = gather.effective_date


@dataclass
class InventoryResult:
    """Outcome of an inventory read.

    ``ok=False`` is a refusal, not a partial result — callers must check
    ``ok`` before touching ``parents``, and branch on ``reason`` if they need
    to distinguish miss kinds (cache-miss retry vs. hard schema error).
    """
    ok: bool
    reason: str | None = None
    parents: list[dict[str, Any]] = field(default_factory=list)
    valid_date: date | None = None
    generated: str | None = None
    inventory_hash: str | None = None
    parent_count: int = 0


def read_inventory(vault_root: Path | str, now: datetime | None = None) -> InventoryResult:
    """Read + validate the active-inventory cache.

    Cache-hit requires BOTH ``schema_version == 2`` and
    ``valid_date == effective_date(now)`` (the 2am logical-day rule, borrowed
    verbatim from ``tdtb_gather.effective_date``). Any other condition —
    missing file, unparseable frontmatter, wrong/absent schema_version,
    absent/empty ``parents`` — returns an explicit refusal result naming the
    miss reason. Never guesses, never returns a partial pool.
    """
    vault_root = Path(vault_root)
    cache_path = vault_root / CACHE_REL_PATH

    if not cache_path.is_file():
        return InventoryResult(ok=False, reason="missing_file")

    text = cache_path.read_text(encoding="utf-8", errors="replace")
    fm = gather.parse_frontmatter(text)
    if fm is None:
        return InventoryResult(ok=False, reason="unparseable")

    schema_version = fm.get("schema_version")
    if schema_version != CACHE_SCHEMA_VERSION:
        return InventoryResult(
            ok=False, reason=f"schema_version_mismatch:{schema_version!r}"
        )

    parents = fm.get("parents")
    if not parents:
        return InventoryResult(ok=False, reason="parents_absent_or_empty")

    valid_date_raw = fm.get("valid_date")
    try:
        valid_date = date.fromisoformat(str(valid_date_raw))
    except (TypeError, ValueError):
        return InventoryResult(ok=False, reason=f"invalid_valid_date:{valid_date_raw!r}")

    now = now or datetime.now()
    today = effective_date(now)
    if valid_date != today:
        return InventoryResult(ok=False, reason=f"stale_valid_date:{valid_date}!={today}")

    return InventoryResult(
        ok=True,
        parents=parents,
        valid_date=valid_date,
        generated=fm.get("generated"),
        inventory_hash=fm.get("inventory_hash"),
        parent_count=fm.get("parent_count", len(parents)),
    )


def _format_parents_block(parents: list[dict[str, Any]]) -> list[str]:
    """Mirror tdtb_gather._cache_to_markdown's per-parent line shape exactly."""
    lines = ["parents:"]
    for p in parents:
        lines.append(f"  - type: {p['type']}")
        lines.append(f"    name: {json.dumps(p['name'])}")
        lines.append(f"    path: {json.dumps(p['path'])}")
        if p.get("urgency"):
            lines.append(f"    urgency: {p['urgency']}")
        if p.get("deadline"):
            lines.append(f"    deadline: '{p['deadline']}'")
        if p.get("priority_score") is not None:
            lines.append(f"    priority_score: {p['priority_score']}")
    return lines


def write_inventory(
    vault_root: Path | str,
    parents: list[dict[str, Any]],
    valid_date: date,
    now: datetime | None = None,
    prior: InventoryResult | None = None,
) -> None:
    """Rewrite the active-inventory cache in gather's frontmatter-only shape.

    Never emits a JSON body block — same YAML-frontmatter-only markdown
    style as ``tdtb_gather._cache_to_markdown``. ``inventory_hash`` is
    carried over from ``prior`` when present (any InventoryResult — ok or
    not, as long as it has a hash to carry — e.g. a stale-but-parseable
    prior cache); omitted entirely otherwise, matching gather's own
    omit-if-absent style for optional per-parent fields.
    """
    vault_root = Path(vault_root)
    now = now or datetime.now(timezone.utc)
    generated = now.strftime("%Y-%m-%dT%H:%M:%S.000Z")

    lines = [
        "---",
        f"schema_version: {CACHE_SCHEMA_VERSION}",
        f"valid_date: '{valid_date}'",
        f"generated: '{generated}'",
    ]

    inventory_hash = prior.inventory_hash if prior and prior.inventory_hash else None
    if inventory_hash:
        lines.append(f"inventory_hash: {inventory_hash}")

    lines.append(f"parent_count: {len(parents)}")
    lines.extend(_format_parents_block(parents))
    lines += ["---", "", "_Auto-generated by inventory.py. Do not edit manually._", ""]

    out_path = vault_root / CACHE_REL_PATH
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")
