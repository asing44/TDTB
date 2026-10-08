"""artifact_source.py — consume the normalized planning artifact.

A1 of the artifact-contract pivot
(``docs/plan/2026-10-08-capacities-first-migration/plan.md``, section
"ARCHITECTURE PIVOT"). The app stops reading Capacities/Todoist over REST and
instead consumes ONE normalized file produced by a *swappable* producer: a
skill over MCP today, an n8n workflow later. This module owns the consumption
seam only. It never constructs a provider, never reaches the network, and
never raises.

The artifact contract is ``tdtb.planning-artifact`` v1::

    {
      "schema": "tdtb.planning-artifact",
      "version": 1,
      "generated_at": "2026-10-08T09:00:00-07:00",
      "logical_day": "2026-10-08",
      "producer": {"name": ..., "version": ..., "run_id": ...},
      "content_hash": "<sha256 over {sources, rows}>",
      "sources": {
        "todoist": {"status": "ok|partial|failed", "read_at": ..., "rows": N,
                     "dropped": N, "deferred": N, "warnings": [...]},
        "capacities": {...}
      },
      "rows": [ ...row objects... ],
      "admission": {"rule_set_hash": ..., "admitted": [...], "dropped": [...]}
    }

Consumption rules (settled by the operator, recorded in the plan):

- **Unknown row fields are preserved and ignored**, so a producer can add a
  field without an app migration.
- The artifact must **never carry ``duration_source``**: the app stamps that,
  and a producer value would fight the remembered-duration overlay. Any value
  present is dropped on load (it is not a validation error).
- **O2 — staleness is ``logical_day``.** When ``logical_day`` differs from the
  app's ``gather.effective_date(now)`` the status is ``stale`` and the rows are
  NOT used. Age alone (older than ``sources.artifact.max_age_minutes``, default
  240) does NOT disqualify the rows — it yields ``aged`` and a warning.
- **O4 — hand edits ride an overlay.** ``state_dir()/planning-overlay.json``
  (``{"rows": [...]}``, keyed by ``name``) is merged on load; overlay rows
  override an artifact row with the same name and add rows with new names. A
  ``content_hash`` mismatch on the artifact itself is a visible ``edited``
  flag, never a failure.
- **Never fall back to a live read.** A missing or malformed artifact yields
  empty rows plus a banner naming the artifact path and the producer command;
  a valid ``planning-artifact.prev.json`` is mentioned but never applied
  automatically.

Every helper here is total: a caller can rely on ``load_artifact`` never
raising, whatever is on disk.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import app_config

# ---------------------------------------------------------------------------
# Contract constants
# ---------------------------------------------------------------------------

ARTIFACT_SCHEMA = "tdtb.planning-artifact"
ARTIFACT_VERSION = 1

ARTIFACT_FILENAME = "planning-artifact.json"
PREV_ARTIFACT_FILENAME = "planning-artifact.prev.json"
OVERLAY_FILENAME = "planning-overlay.json"

#: The command that (re)generates the artifact. A1 ships no producer; this
#: names the intended one so the degrade banner tells the operator what to run.
PRODUCER_COMMAND = "tdtb planning-artifact build  (the planning-artifact skill over MCP)"

VALID_ROW_SOURCES = frozenset({"todoist", "capacities", "vault"})
VALID_SOURCE_STATUS = frozenset({"ok", "partial", "failed"})

REQUIRED_TOP_LEVEL = (
    "schema",
    "version",
    "generated_at",
    "logical_day",
    "producer",
    "content_hash",
    "sources",
    "rows",
    "admission",
)
REQUIRED_ROW_FIELDS = ("name", "source", "path", "identity", "assigned")

#: Reported in a malformed banner; the contract says "first 5 violations".
MAX_REPORTED_VIOLATIONS = 5

# Status vocabulary — exactly one rides an :class:`ArtifactResult`.
STATUS_FRESH = "fresh"
STATUS_AGED = "aged"
STATUS_STALE = "stale"
STATUS_MISSING = "missing"
STATUS_MALFORMED = "malformed"
STATUS_PARTIAL = "partial"
STATUS_EDITED = "edited"

#: Sentinel echoed in the digest's ``artifact`` block when the seam is off.
STATUS_LIVE = "live"

ALL_STATUSES = frozenset({
    STATUS_FRESH,
    STATUS_AGED,
    STATUS_STALE,
    STATUS_MISSING,
    STATUS_MALFORMED,
    STATUS_PARTIAL,
    STATUS_EDITED,
})

#: Statuses whose rows are usable. ``stale`` is deliberately absent (O2); so
#: are ``missing``/``malformed`` (no rows exist).
USABLE_STATUSES = frozenset({
    STATUS_FRESH,
    STATUS_AGED,
    STATUS_PARTIAL,
    STATUS_EDITED,
})


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

def artifact_path() -> Path:
    """The canonical artifact: ``<app home>/state/planning-artifact.json``."""
    return app_config.state_dir() / ARTIFACT_FILENAME


def prev_artifact_path(path: str | Path | None = None) -> Path:
    """The single retained prior copy, beside the artifact."""
    target = Path(path) if path is not None else artifact_path()
    return target.with_name(PREV_ARTIFACT_FILENAME)


def overlay_path() -> Path:
    """The hand-edit overlay: ``<app home>/state/planning-overlay.json``."""
    return app_config.state_dir() / OVERLAY_FILENAME


# ---------------------------------------------------------------------------
# Canonical hash
# ---------------------------------------------------------------------------

def compute_content_hash(sources: Any, rows: Any) -> str:
    """Return the contract's ``content_hash``.

    ``sha256`` over the canonical JSON of ``{"sources": sources, "rows":
    rows}`` — sorted keys, no insignificant whitespace, UTF-8, non-ASCII kept
    literal. Producers must use exactly this helper so a hand-written fixture
    and a skill agree byte for byte.
    """
    payload = {"sources": sources, "rows": rows}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                           ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------

@dataclass
class ArtifactResult:
    """Outcome of an artifact load. Never carries a raised exception."""

    rows: list[dict[str, Any]] = field(default_factory=list)
    status: str = STATUS_MISSING
    warnings: list[str] = field(default_factory=list)
    generated_at: str | None = None
    age_minutes: int | None = None
    logical_day: str | None = None
    producer: dict[str, Any] | None = None
    path: Path | None = None
    prev_available: bool = False
    overlay_rows: int = 0

    def as_digest_block(self) -> dict[str, Any]:
        """The ``artifact`` block the digest payload carries (point 6)."""
        return {
            "state": self.status,
            "generated_at": self.generated_at,
            "age_minutes": self.age_minutes,
            "logical_day": self.logical_day,
            "producer": self.producer,
            "warnings": list(self.warnings),
        }


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_artifact(document: Any) -> list[str]:
    """Return every contract violation, bounded to the first 5.

    Malformed is: a non-object document, a wrong ``schema``/``version``, a
    missing/ill-typed required field, a duplicate row ``name``, or a row
    missing a required field. An empty list means the artifact is well formed.
    Unknown row fields are NOT a violation — the contract preserves them.
    """
    violations: list[str] = []

    def add(message: str) -> None:
        if len(violations) < MAX_REPORTED_VIOLATIONS:
            violations.append(message)

    if not isinstance(document, dict):
        return ["artifact document is not a JSON object"]

    for key in REQUIRED_TOP_LEVEL:
        if key not in document:
            add(f"missing required top-level field {key!r}")

    if violations:
        return violations

    if document["schema"] != ARTIFACT_SCHEMA:
        add(f"schema must be {ARTIFACT_SCHEMA!r} (got {document['schema']!r})")
    version = document["version"]
    if type(version) is not int or version != ARTIFACT_VERSION:
        add(f"version must be the integer {ARTIFACT_VERSION} (got {version!r})")

    _validate_generated_at(document["generated_at"], add)
    _validate_logical_day(document["logical_day"], add)
    _validate_producer(document["producer"], add)
    _validate_content_hash(document["content_hash"], add)
    _validate_sources(document["sources"], add)
    _validate_rows(document["rows"], add)
    _validate_admission(document["admission"], add)

    return violations


def _validate_generated_at(value: Any, add) -> None:
    if not isinstance(value, str):
        add("generated_at must be an ISO-8601 string")
        return
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        add(f"generated_at {value!r} is not a valid ISO-8601 timestamp")
        return
    if parsed.tzinfo is None:
        add(f"generated_at {value!r} must carry a UTC offset")


def _validate_logical_day(value: Any, add) -> None:
    if not isinstance(value, str):
        add("logical_day must be a YYYY-MM-DD string")
        return
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        add(f"logical_day {value!r} is not a valid YYYY-MM-DD date")


def _validate_producer(value: Any, add) -> None:
    if not isinstance(value, dict):
        add("producer must be an object")
        return
    for key in ("name", "version", "run_id"):
        if not isinstance(value.get(key), str) or not value.get(key):
            add(f"producer.{key} must be a non-empty string")


def _validate_content_hash(value: Any, add) -> None:
    if not isinstance(value, str) or len(value) != 64:
        add("content_hash must be a 64-character sha256 hex string")
        return
    try:
        int(value, 16)
    except ValueError:
        add("content_hash is not valid hexadecimal")


def _validate_sources(value: Any, add) -> None:
    if not isinstance(value, dict):
        add("sources must be an object")
        return
    for name, entry in value.items():
        if not isinstance(entry, dict):
            add(f"sources.{name} must be an object")
            continue
        status = entry.get("status")
        if status not in VALID_SOURCE_STATUS:
            add(
                f"sources.{name}.status must be one of "
                f"{sorted(VALID_SOURCE_STATUS)} (got {status!r})"
            )


def _validate_rows(value: Any, add) -> None:
    if not isinstance(value, list):
        add("rows must be an array")
        return
    seen: set[str] = set()
    for index, row in enumerate(value):
        if not isinstance(row, dict):
            add(f"rows[{index}] must be an object")
            continue
        for field_name in REQUIRED_ROW_FIELDS:
            if field_name not in row:
                add(f"rows[{index}] is missing required field {field_name!r}")
        name = row.get("name")
        if not isinstance(name, str) or not name:
            add(f"rows[{index}].name must be a non-empty string")
        else:
            if name in seen:
                add(f"duplicate row name {name!r}")
            seen.add(name)
        source = row.get("source")
        if source not in VALID_ROW_SOURCES:
            add(
                f"rows[{index}].source must be one of "
                f"{sorted(VALID_ROW_SOURCES)} (got {source!r})"
            )
        if "assigned" in row and not isinstance(row["assigned"], bool):
            add(f"rows[{index}].assigned must be a boolean")


def _validate_admission(value: Any, add) -> None:
    if not isinstance(value, dict):
        add("admission must be an object")
        return
    if not isinstance(value.get("rule_set_hash"), str):
        add("admission.rule_set_hash must be a string")
    for key in ("admitted", "dropped"):
        if key not in value:
            add(f"admission.{key} is required")
        elif not isinstance(value[key], list):
            add(f"admission.{key} must be an array")


# ---------------------------------------------------------------------------
# Load
# ---------------------------------------------------------------------------

def _age_minutes(generated_at: Any, now: datetime) -> int | None:
    """Minutes between ``generated_at`` and ``now``, or None when unparseable."""
    if not isinstance(generated_at, str):
        return None
    try:
        dt = datetime.fromisoformat(generated_at)
    except ValueError:
        return None
    if now.tzinfo is None:
        now = now.astimezone()
    if dt.tzinfo is None:
        dt = dt.astimezone(now.tzinfo)
    return int((now - dt).total_seconds() // 60)


def _today_effective_date(now: datetime) -> str:
    """The app's own logical day, via the gather contract (O2)."""
    try:
        import tdtb_gather as gather  # local import: path-shimmed by main
        return str(gather.effective_date(now))
    except Exception:  # noqa: BLE001 — never let a shim failure raise
        return now.date().isoformat()


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_artifact(
    now: datetime,
    *,
    path: str | Path | None = None,
    max_age_minutes: int | None = None,
    overlay: str | Path | None = None,
) -> ArtifactResult:
    """Read the planning artifact and return an :class:`ArtifactResult`.

    Never raises. ``now`` supplies both the logical-day comparison (O2) and
    the age measurement. ``path``/``overlay`` are injectable for tests;
    ``max_age_minutes`` defaults to the config value (240).
    """
    target = Path(path) if path is not None else artifact_path()
    prev = prev_artifact_path(target)
    overlay_target = Path(overlay) if overlay is not None else overlay_path()
    if max_age_minutes is None:
        max_age_minutes = app_config.artifact_max_age_minutes()

    warnings: list[str] = []

    try:
        raw = target.read_text(encoding="utf-8")
    except FileNotFoundError:
        return _degrade(
            target, prev, warnings, STATUS_MISSING,
            f"Planning artifact not found at {target} — no Todoist or "
            f"Capacities rows are available. Regenerate with: {PRODUCER_COMMAND}",
        )
    except OSError as exc:
        return _degrade(
            target, prev, warnings, STATUS_MISSING,
            f"Planning artifact unreadable at {target} ({exc}) — regenerate "
            f"with: {PRODUCER_COMMAND}",
        )

    try:
        document = json.loads(raw)
    except ValueError as exc:
        return _degrade(
            target, prev, warnings, STATUS_MALFORMED,
            f"Planning artifact at {target} is not valid JSON ({exc}). "
            f"Regenerate with: {PRODUCER_COMMAND}",
        )

    violations = validate_artifact(document)
    if violations:
        joined = "; ".join(violations)
        return _degrade(
            target, prev, warnings, STATUS_MALFORMED,
            f"Planning artifact at {target} is malformed: {joined}. "
            f"Regenerate with: {PRODUCER_COMMAND}",
        )

    sources = document["sources"]
    raw_rows = document["rows"]
    generated_at = document["generated_at"]
    logical_day = document["logical_day"]
    producer = dict(document["producer"])
    age = _age_minutes(generated_at, now)

    # Hash over the file's own {sources, rows} — before the duration_source
    # strip, so a producer that (illegally) carries it and hashes it still
    # verifies; the field is then ignored.
    expected_hash = document["content_hash"]
    hash_ok = compute_content_hash(sources, raw_rows) == expected_hash
    if not hash_ok:
        warnings.append(
            "Planning artifact was hand-edited since it was produced "
            "(content_hash mismatch) — showing the file as written."
        )

    rows = [_strip_producer_fields(row) for row in raw_rows]

    status = _classify(
        logical_day=logical_day,
        today=_today_effective_date(now),
        sources=sources,
        hash_ok=hash_ok,
        age_minutes=age,
        max_age_minutes=max_age_minutes,
        warnings=warnings,
    )

    used: list[dict[str, Any]] = []
    if status in USABLE_STATUSES:
        used = rows
    else:  # stale — O2: rows are NOT used
        warnings.append(
            f"Planning artifact is for logical day {logical_day}, not today's "
            f"{_today_effective_date(now)} — its rows were not used. "
            f"Regenerate with: {PRODUCER_COMMAND}"
        )

    overlay_count = 0
    if used:
        used, overlay_count = _apply_overlay(used, overlay_target, warnings)

    return ArtifactResult(
        rows=used,
        status=status,
        warnings=warnings,
        generated_at=generated_at,
        age_minutes=age,
        logical_day=logical_day,
        producer=producer,
        path=target,
        prev_available=prev.is_file(),
        overlay_rows=overlay_count,
    )


def _classify(
    *,
    logical_day: str,
    today: str,
    sources: dict[str, Any],
    hash_ok: bool,
    age_minutes: int | None,
    max_age_minutes: int,
    warnings: list[str],
) -> str:
    """Map the load observations to exactly one status.

    Documented precedence (most severe/actionable first)::

        stale > partial > edited > aged > fresh

    ``missing``/``malformed`` are decided before this runs. Every condition
    that holds also contributes a warning, so a lower-precedence condition is
    never lost when a higher one wins the status.
    """
    degraded = sorted(
        name for name, entry in sources.items()
        if isinstance(entry, dict) and entry.get("status") in ("partial", "failed")
    )
    if degraded:
        warnings.append(
            "Planning artifact reports incomplete sources "
            f"({', '.join(degraded)}) — rows may be missing."
        )
    if logical_day != today:
        return STATUS_STALE
    if degraded:
        return STATUS_PARTIAL
    if not hash_ok:
        return STATUS_EDITED
    if age_minutes is not None and age_minutes > max_age_minutes:
        warnings.append(
            f"Planning artifact is {age_minutes} minutes old "
            f"(max {max_age_minutes}) — rows used, but they may be stale."
        )
        return STATUS_AGED
    return STATUS_FRESH


def _strip_producer_fields(row: dict[str, Any]) -> dict[str, Any]:
    """Preserve every unknown field; drop exactly the app-owned ones.

    ``duration_source`` is stamped by the app and must never ride the
    artifact — a producer value would fight the remembered-duration overlay.
    """
    clean = dict(row)
    clean.pop("duration_source", None)
    return clean


def _apply_overlay(
    rows: list[dict[str, Any]], overlay_target: Path, warnings: list[str]
) -> tuple[list[dict[str, Any]], int]:
    """Merge the hand-edit overlay (O4). Malformed overlay → warning, no-op."""
    try:
        overlay = _read_json(overlay_target)
    except FileNotFoundError:
        return rows, 0
    except (OSError, ValueError) as exc:
        warnings.append(f"Planning overlay at {overlay_target} is unusable ({exc})")
        return rows, 0
    if not isinstance(overlay, dict) or not isinstance(overlay.get("rows"), list):
        warnings.append(f"Planning overlay at {overlay_target} has no rows array")
        return rows, 0

    merged = [dict(row) for row in rows]
    index_by_name = {str(row.get("name")): i for i, row in enumerate(merged)}
    applied = 0
    for overlay_row in overlay["rows"]:
        if not isinstance(overlay_row, dict):
            warnings.append("Planning overlay carries a non-object row")
            continue
        name = overlay_row.get("name")
        if not isinstance(name, str) or not name:
            warnings.append("Planning overlay row has no name")
            continue
        replacement = _strip_producer_fields(overlay_row)
        position = index_by_name.get(name)
        if position is None:
            index_by_name[name] = len(merged)
            merged.append(replacement)
        else:
            merged[position] = replacement
        applied += 1

    if applied:
        warnings.append(f"Planning overlay applied to {applied} row(s).")
    return merged, applied


def _degrade(
    target: Path,
    prev: Path,
    warnings: list[str],
    status: str,
    message: str,
) -> ArtifactResult:
    """Build an empty-rows result for a missing/malformed artifact.

    The prior copy is mentioned when it is itself valid, but is NEVER applied
    automatically (point 7)."""
    warnings.append(message)
    if prev.is_file():
        try:
            prev_document = _read_json(prev)
        except (OSError, ValueError):
            prev_document = None
        if prev_document is not None and not validate_artifact(prev_document):
            warnings.append(
                f"A valid prior artifact exists at {prev} — it was NOT applied "
                "automatically; regenerate to refresh it deliberately."
            )
    return ArtifactResult(
        rows=[],
        status=status,
        warnings=warnings,
        path=target,
        prev_available=prev.is_file(),
    )


# ---------------------------------------------------------------------------
# Atomic write (the producer's write path; A1 does not call it)
# ---------------------------------------------------------------------------

def atomic_write_artifact(
    document: dict[str, Any], *, path: str | Path | None = None
) -> Path:
    """Write ``document`` atomically, retaining one prior copy.

    A unique same-directory temp file, flushed and fsynced, then
    ``os.replace``. When a copy already exists it is retained as
    ``planning-artifact.prev.json`` first. Never leaves a torn artifact and
    never uses a shared temp name. Raises ``OSError`` on failure — the caller
    (the producer) owns the error.
    """
    target = Path(path) if path is not None else artifact_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    prev = prev_artifact_path(target)
    if target.is_file():
        try:
            shutil.copyfile(target, prev)
        except OSError:  # a missing prior copy is not fatal to the write
            pass
    fd, tmp = tempfile.mkstemp(dir=str(target.parent), prefix=f".{target.name}.",
                               suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(document, handle, indent=2, sort_keys=True,
                      ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return target
