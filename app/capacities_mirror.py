"""capacities_mirror.py — machine-local receipts + intake exclusion for the
Capacities -> PHEP Todoist mirror lane (contract change 2026-10-13).

A Capacities assigned row that reaches the calendar write ALSO creates one
Todoist task under the PHEP project, so the work is actionable in Todoist
without becoming a second plan item. This module is the machine-local
authority that makes that write safe:

* **Write-ahead receipt keyed by (logical day, Capacities identity).** A
  ``pending`` receipt is persisted BEFORE the provider call, so a
  resume/re-commit never duplicates the task and a changed title cannot
  duplicate it (the key is the identity, not the title). An existing
  ``pending``/``needs_review`` receipt is surfaced as ``needs_review`` and is
  never blindly retried as success.
* **Created ids are hidden from intake.** ``complete_mirror`` records the
  landed Todoist id in ``excluded_task_ids`` so a mirror never re-appears as a
  Todoist-sourced row on a later load (including later days while still open)
  and never collides with its own Capacities row.
* **Fail-closed reads.** Existing bytes that are malformed, duplicate-keyed,
  or unsupported raise a typed error and are never repaired, overwritten, or
  truncated. Callers that create must refuse; intake degrades with a visible
  warning (``UNREADABLE_WARNING``).
* **Cross-process serialization.** Every read-modify-write runs under the
  shared per-path process lock paired with a POSIX advisory lock file
  (``capacities_cache_io``) and an atomic same-directory write.

The store holds no credentials and no provider payload: only a logical day, a
Capacities identity, a bounded status, an optional task id, a bounded reason,
and a timestamp.
"""
from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Any

import app_config
import capacities_cache_io

SCHEMA_VERSION = 1
STORE_FILENAME = "capacities-mirror.json"
LOCK_FILENAME = "capacities-mirror.lock"

STATUS_PENDING = "pending"
STATUS_DONE = "done"
STATUS_NEEDS_REVIEW = "needs_review"
KNOWN_STATUSES = frozenset({STATUS_PENDING, STATUS_DONE, STATUS_NEEDS_REVIEW})

BEGIN = "begin"
SKIP_DONE = "skip_done"
NEEDS_REVIEW = "needs_review"

MAX_IDENTITY_LEN = 500
MAX_REASON_LEN = 200

#: Operator-facing, content-free warning for an unreadable exclusion store at
#: intake. Intake degrades to no exclusions and surfaces this loudly.
UNREADABLE_WARNING = (
    "Capacities mirror exclusions unavailable — Todoist rows may include "
    "previously created mirror tasks"
)

_TOP_KEYS = frozenset({"version", "receipts", "excluded_task_ids"})
_RECEIPT_KEYS = frozenset({
    "day", "identity", "status", "task_id", "reason", "updated_at",
})
_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_KEY_SEP = "::"


# ---------------------------------------------------------------------------
# Errors (typed, content-free)
# ---------------------------------------------------------------------------

class MirrorStoreError(Exception):
    """The mirror store could not be read or written.

    Callers fail closed: no provider call is made while the durable state is
    unknown, and the existing bytes are preserved."""


class MirrorStoreFormatError(MirrorStoreError):
    """Existing storage is malformed or unsupported. Never overwritten."""


class MirrorStoreValidationError(MirrorStoreError):
    """The caller supplied an invalid day/identity/task id; nothing was written."""


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

def _parse_day(value: Any) -> str:
    if not isinstance(value, str) or not _DAY_RE.match(value):
        raise ValueError("day must be a string of the form YYYY-MM-DD")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("day must be a valid calendar date") from exc
    return parsed.isoformat()


def _clean_identity(value: Any) -> str:
    """A non-empty, control-free, bounded Capacities identity."""
    if not isinstance(value, str):
        raise ValueError("identity must be a string")
    text = _CONTROL_RE.sub(" ", value).strip()
    if not text:
        raise ValueError("identity must be non-empty")
    return text[:MAX_IDENTITY_LEN]


def _clean_reason(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("reason must be a string")
    text = _CONTROL_RE.sub(" ", value).strip()
    return text[:MAX_REASON_LEN] or None


def _clean_task_id(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("task_id must be a string")
    text = value.strip()
    return text or None


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def receipt_key(day: str, identity: str) -> str:
    """The canonical receipt key: logical day + Capacities identity."""
    return f"{day}{_KEY_SEP}{identity}"


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class MirrorReceipt:
    """One durable mirror receipt (content-free)."""

    day: str
    identity: str
    status: str
    task_id: str | None = None
    reason: str | None = None
    updated_at: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "day", _parse_day(self.day))
        object.__setattr__(self, "identity", _clean_identity(self.identity))
        if self.status not in KNOWN_STATUSES:
            raise ValueError("unknown mirror receipt status")
        object.__setattr__(self, "task_id", _clean_task_id(self.task_id))
        object.__setattr__(self, "reason", _clean_reason(self.reason))
        if self.updated_at is not None and not isinstance(self.updated_at, str):
            raise ValueError("updated_at must be a string")

    @property
    def key(self) -> str:
        return receipt_key(self.day, self.identity)

    def as_dict(self) -> dict[str, Any]:
        return {
            "day": self.day,
            "identity": self.identity,
            "status": self.status,
            "task_id": self.task_id,
            "reason": self.reason,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class MirrorRecord:
    """The whole store: receipt key -> :class:`MirrorReceipt`, plus the ids
    hidden from Todoist intake."""

    receipts: Mapping[str, MirrorReceipt] = field(default_factory=dict)
    excluded_task_ids: tuple[str, ...] = ()
    version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if type(self.version) is not int or self.version != SCHEMA_VERSION:
            raise ValueError("unsupported capacities mirror version")
        normalized: dict[str, MirrorReceipt] = {}
        for key, receipt in self.receipts.items():
            if not isinstance(receipt, MirrorReceipt):
                raise ValueError("receipts must map to MirrorReceipt records")
            if key != receipt.key:
                raise ValueError("receipt key does not match its record")
            normalized[key] = receipt
        object.__setattr__(self, "receipts", MappingProxyType(normalized))
        excluded: list[str] = []
        for value in self.excluded_task_ids:
            clean = _clean_task_id(value)
            if clean is None:
                raise ValueError("excluded task ids must be non-empty strings")
            if clean not in excluded:
                excluded.append(clean)
        object.__setattr__(self, "excluded_task_ids", tuple(excluded))

    def get(self, day: str, identity: str) -> MirrorReceipt | None:
        return self.receipts.get(receipt_key(day, identity))

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "receipts": {k: r.as_dict() for k, r in self.receipts.items()},
            "excluded_task_ids": list(self.excluded_task_ids),
        }


@dataclass(frozen=True)
class MirrorBeginOutcome:
    """The result of :func:`begin_mirror`.

    ``action`` is ``begin`` (this caller owns the provider call),
    ``skip_done`` (a confirmed receipt already exists), or ``needs_review``
    (an unconfirmed receipt exists — never re-create)."""

    action: str
    receipt: MirrorReceipt


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

def store_path() -> Path:
    """``<app home>/state/capacities-mirror.json``."""
    return app_config.state_dir() / STORE_FILENAME


def lock_path() -> Path:
    """The store lock file, beside the record in the app home."""
    return app_config.state_dir() / LOCK_FILENAME


# ---------------------------------------------------------------------------
# Strict decoding
# ---------------------------------------------------------------------------

def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _load_strict(path: Path) -> MirrorRecord:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        raise MirrorStoreError("capacities mirror record is unreadable") from None
    try:
        data = json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
    except ValueError:
        raise MirrorStoreFormatError(
            "capacities mirror record file is malformed"
        ) from None
    if not isinstance(data, dict):
        raise MirrorStoreFormatError("capacities mirror record must be a JSON object")
    if set(data) != _TOP_KEYS:
        raise MirrorStoreFormatError(
            "capacities mirror record has unknown or missing keys"
        )
    version = data["version"]
    if type(version) is not int or version != SCHEMA_VERSION:
        raise MirrorStoreFormatError("capacities mirror version is unsupported")
    stored = data["receipts"]
    if not isinstance(stored, dict):
        raise MirrorStoreFormatError("capacities mirror receipts must be a JSON object")
    excluded_stored = data["excluded_task_ids"]
    if not isinstance(excluded_stored, list):
        raise MirrorStoreFormatError(
            "capacities mirror excluded task ids must be a JSON list"
        )
    receipts: dict[str, MirrorReceipt] = {}
    for key, value in stored.items():
        if not isinstance(value, dict) or set(value) != _RECEIPT_KEYS:
            raise MirrorStoreFormatError(
                "capacities mirror receipt has unknown or missing keys"
            )
        try:
            receipt = MirrorReceipt(
                day=value["day"],
                identity=value["identity"],
                status=value["status"],
                task_id=value["task_id"],
                reason=value["reason"],
                updated_at=value["updated_at"],
            )
        except ValueError as exc:
            raise MirrorStoreFormatError(
                "capacities mirror receipt is malformed"
            ) from exc
        if key != receipt.key:
            raise MirrorStoreFormatError(
                "capacities mirror key does not match its record"
            )
        receipts[key] = receipt
    try:
        return MirrorRecord(
            receipts=receipts,
            excluded_task_ids=tuple(excluded_stored),
            version=version,
        )
    except ValueError as exc:
        raise MirrorStoreFormatError(
            "capacities mirror excluded task ids are malformed"
        ) from exc


def load_mirror(*, path: str | Path | None = None) -> MirrorRecord:
    """Read the persisted store.

    An absent file returns the empty default WITHOUT creating anything.
    Malformed/unsupported storage raises :class:`MirrorStoreFormatError`; an
    unreadable file raises :class:`MirrorStoreError`."""
    target = Path(path) if path is not None else store_path()
    try:
        exists = target.is_file()
    except OSError:
        raise MirrorStoreError("capacities mirror record is unreadable") from None
    if not exists:
        return MirrorRecord()
    return _load_strict(target)


def excluded_task_ids(*, path: str | Path | None = None) -> tuple[str, ...]:
    """The persisted mirror Todoist ids to hide from intake (fail-closed read)."""
    return load_mirror(path=path).excluded_task_ids


# ---------------------------------------------------------------------------
# Read-modify-write
# ---------------------------------------------------------------------------

def _write_record(target: Path, record: MirrorRecord) -> None:
    try:
        capacities_cache_io.atomic_write_json(target, record.as_dict())
    except OSError:
        raise MirrorStoreError(
            "capacities mirror record could not be written"
        ) from None


def _acquire_lock(lock: Path) -> Any:
    try:
        return capacities_cache_io.acquire_path_lock(lock)
    except OSError:
        raise MirrorStoreError("capacities mirror store lock is unavailable") from None


def _validate_inputs(day: Any, identity: Any) -> tuple[str, str]:
    try:
        valid_day = _parse_day(day)
    except ValueError as exc:
        raise MirrorStoreValidationError(str(exc)) from exc
    try:
        valid_identity = _clean_identity(identity)
    except ValueError as exc:
        raise MirrorStoreValidationError(str(exc)) from exc
    return valid_day, valid_identity


def begin_mirror(
    *, day: str, identity: str, path: str | Path | None = None
) -> MirrorBeginOutcome:
    """Claim (or observe) the receipt for one Capacities mirror.

    Under the cross-process lock:

    * an existing ``done`` receipt yields ``skip_done`` — never re-create;
    * an existing ``pending``/``needs_review`` receipt yields
      ``needs_review`` — an unconfirmed outcome is never blind-retried;
    * an absent receipt is written ``pending`` and yields ``begin``.

    The pending write is durable before the caller touches the provider."""
    valid_day, valid_identity = _validate_inputs(day, identity)
    target = Path(path) if path is not None else store_path()
    lock = lock_path()
    with capacities_cache_io.store_lock(lock):
        fh = _acquire_lock(lock)
        try:
            record = load_mirror(path=target)
            existing = record.get(valid_day, valid_identity)
            if existing is not None:
                if existing.status == STATUS_DONE:
                    return MirrorBeginOutcome(action=SKIP_DONE, receipt=existing)
                return MirrorBeginOutcome(action=NEEDS_REVIEW, receipt=existing)
            pending = MirrorReceipt(
                day=valid_day, identity=valid_identity,
                status=STATUS_PENDING, updated_at=_utcnow(),
            )
            updated = dict(record.receipts)
            updated[pending.key] = pending
            _write_record(target, MirrorRecord(
                receipts=updated, excluded_task_ids=record.excluded_task_ids))
            return MirrorBeginOutcome(action=BEGIN, receipt=pending)
        finally:
            capacities_cache_io.release_lock_file(fh)


def complete_mirror(
    *, day: str, identity: str, task_id: str,
    path: str | Path | None = None,
) -> MirrorReceipt:
    """Persist the confirmed ``done`` receipt and hide the landed task id."""
    valid_day, valid_identity = _validate_inputs(day, identity)
    try:
        clean_task_id = _clean_task_id(task_id)
    except ValueError as exc:
        raise MirrorStoreValidationError(str(exc)) from exc
    if clean_task_id is None:
        raise MirrorStoreValidationError("task_id is required to complete a mirror")
    target = Path(path) if path is not None else store_path()
    lock = lock_path()
    with capacities_cache_io.store_lock(lock):
        fh = _acquire_lock(lock)
        try:
            record = load_mirror(path=target)
            done = MirrorReceipt(
                day=valid_day, identity=valid_identity, status=STATUS_DONE,
                task_id=clean_task_id, updated_at=_utcnow(),
            )
            updated = dict(record.receipts)
            updated[done.key] = done
            excluded = list(record.excluded_task_ids)
            if clean_task_id not in excluded:
                excluded.append(clean_task_id)
            _write_record(target, MirrorRecord(
                receipts=updated, excluded_task_ids=tuple(excluded)))
            return done
        finally:
            capacities_cache_io.release_lock_file(fh)


def fail_mirror(
    *, day: str, identity: str, reason: str | None = None,
    path: str | Path | None = None,
) -> MirrorReceipt:
    """Persist a ``needs_review`` receipt after an errored/ambiguous attempt."""
    valid_day, valid_identity = _validate_inputs(day, identity)
    try:
        clean_reason = _clean_reason(reason)
    except ValueError as exc:
        raise MirrorStoreValidationError(str(exc)) from exc
    target = Path(path) if path is not None else store_path()
    lock = lock_path()
    with capacities_cache_io.store_lock(lock):
        fh = _acquire_lock(lock)
        try:
            record = load_mirror(path=target)
            review = MirrorReceipt(
                day=valid_day, identity=valid_identity,
                status=STATUS_NEEDS_REVIEW, reason=clean_reason, updated_at=_utcnow(),
            )
            updated = dict(record.receipts)
            updated[review.key] = review
            _write_record(target, MirrorRecord(
                receipts=updated, excluded_task_ids=record.excluded_task_ids))
            return review
        finally:
            capacities_cache_io.release_lock_file(fh)
