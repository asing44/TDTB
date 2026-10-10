"""export_receipts.py — durable, content-free receipts for Commit-time
prompt exports (U5 B2).

The dedicated Commit-only Todoist lane (``prompt_export.py``) must never
blindly re-create a task whose earlier external write may already have
landed. This module owns the machine-local receipt store that makes that
possible:

* **One record per action.** A receipt is keyed by logical day + prompt key
  + action, so the same logical day's export is never repeated and a new day
  naturally starts clean.
* **Content-free by construction.** A receipt stores only the task id, a
  bounded status, an optional bounded reason, and a timestamp. No prompt
  text, request body, credential, or provider payload is ever written,
  echoed, or placed in an error.
* **Write-ahead pending.** ``begin_export`` persists a ``pending`` receipt
  BEFORE the caller performs any provider call. Only the caller that created
  that pending record proceeds; every other caller observes it and stops.
* **Fail-closed reads.** Existing bytes that are malformed, duplicate-keyed,
  or unsupported raise a typed error and are never repaired, overwritten, or
  truncated.
* **Cross-process serialization.** Every read-modify-write runs under the
  shared per-path process lock paired with a POSIX advisory lock file
  (``capacities_cache_io``), so two concurrent commits cannot both create the
  same task.

Statuses: ``pending`` (write-ahead, outcome unknown), ``done`` (provider
confirmed a task id), ``needs_review`` (provider errored or the outcome is
ambiguous — never auto-retried).
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
import prompt_state

RECEIPTS_SCHEMA_VERSION = 1
RECEIPTS_FILENAME = "prompt-export-receipts.json"
RECEIPTS_LOCK_FILENAME = "prompt-export-receipts.lock"

#: The only export action this module knows. Adding another is an explicit
#: contract change, not a caller-supplied value.
ACTION_TODOIST_TASK = "todoist-task"
KNOWN_ACTIONS = frozenset({ACTION_TODOIST_TASK})

STATUS_PENDING = "pending"
STATUS_DONE = "done"
STATUS_NEEDS_REVIEW = "needs_review"
KNOWN_STATUSES = frozenset({STATUS_PENDING, STATUS_DONE, STATUS_NEEDS_REVIEW})

BEGIN = "begin"
SKIP_DONE = "skip_done"
NEEDS_REVIEW = "needs_review"

#: Bounded, content-free reason text ceiling. The store only ever receives
#: fixed operator-facing strings, but the cap keeps a corrupt/hand-edited
#: document from smuggling an unbounded blob into a response.
MAX_REASON_LEN = 200

_TOP_KEYS = frozenset({"version", "receipts"})
_RECEIPT_KEYS = frozenset({
    "day", "prompt_key", "action", "status", "task_id", "reason", "updated_at",
})
_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


# ---------------------------------------------------------------------------
# Errors (typed, content-free)
# ---------------------------------------------------------------------------

class ExportReceiptError(Exception):
    """A receipt store could not be read or written.

    Callers fail closed: no provider call is made while the durable state is
    unknown, and the existing bytes are preserved."""


class ExportReceiptFormatError(ExportReceiptError):
    """Existing receipt storage is malformed or unsupported.

    Raised for an unparseable file, duplicate JSON keys, unknown/missing
    keys, strict-type violations, or an unsupported version. Callers must
    never overwrite the offending bytes."""


class ExportReceiptValidationError(ExportReceiptError):
    """The caller supplied an invalid receipt key or value; nothing was written."""


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


def _clean_reason(value: Any) -> str | None:
    """A bounded, content-free reason string (or ``None``)."""
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


def receipt_key(day: str, prompt_key: str, action: str) -> str:
    """The canonical receipt key: logical day + prompt key + action."""
    return f"{day}:{prompt_key}:{action}"


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ExportReceipt:
    """One durable export receipt (content-free)."""

    day: str
    prompt_key: str
    action: str
    status: str
    task_id: str | None = None
    reason: str | None = None
    updated_at: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "day", _parse_day(self.day))
        if self.prompt_key not in prompt_state.PROMPT_KEYS:
            raise ValueError("unknown prompt key")
        if self.action not in KNOWN_ACTIONS:
            raise ValueError("unknown export action")
        if self.status not in KNOWN_STATUSES:
            raise ValueError("unknown receipt status")
        object.__setattr__(self, "task_id", _clean_task_id(self.task_id))
        object.__setattr__(self, "reason", _clean_reason(self.reason))
        if self.updated_at is not None and not isinstance(self.updated_at, str):
            raise ValueError("updated_at must be a string")

    @property
    def key(self) -> str:
        return receipt_key(self.day, self.prompt_key, self.action)

    def as_dict(self) -> dict[str, Any]:
        return {
            "day": self.day,
            "prompt_key": self.prompt_key,
            "action": self.action,
            "status": self.status,
            "task_id": self.task_id,
            "reason": self.reason,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class ExportReceiptsRecord:
    """The whole store: receipt key -> :class:`ExportReceipt`."""

    receipts: Mapping[str, ExportReceipt] = field(default_factory=dict)
    version: int = RECEIPTS_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if type(self.version) is not int or self.version != RECEIPTS_SCHEMA_VERSION:
            raise ValueError("unsupported export receipts version")
        normalized: dict[str, ExportReceipt] = {}
        for key, receipt in self.receipts.items():
            if not isinstance(receipt, ExportReceipt):
                raise ValueError("receipts must map to ExportReceipt records")
            if key != receipt.key:
                raise ValueError("receipt key does not match its record")
            normalized[key] = receipt
        object.__setattr__(self, "receipts", MappingProxyType(normalized))

    def get(self, day: str, prompt_key: str, action: str) -> ExportReceipt | None:
        return self.receipts.get(receipt_key(day, prompt_key, action))

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "receipts": {k: r.as_dict() for k, r in self.receipts.items()},
        }


@dataclass(frozen=True)
class BeginOutcome:
    """The result of :func:`begin_export`.

    ``action`` is ``begin`` (this caller owns the provider call),
    ``skip_done`` (a confirmed receipt already exists), or ``needs_review``
    (an unconfirmed receipt exists — never re-create).
    """

    action: str
    receipt: ExportReceipt


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

def receipts_path() -> Path:
    """``<app home>/state/prompt-export-receipts.json``."""
    return app_config.state_dir() / RECEIPTS_FILENAME


def receipts_lock_path() -> Path:
    """The receipt lock file, beside the receipt record in the app home."""
    return app_config.state_dir() / RECEIPTS_LOCK_FILENAME


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


def _load_strict(path: Path) -> ExportReceiptsRecord:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        # The OS error carries the machine-local path; suppress it so no
        # filename or raw exception text reaches a log, response, or traceback.
        raise ExportReceiptError("export receipts record is unreadable") from None
    try:
        data = json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
    except ValueError:
        # The parse error can carry the raw document; suppress it so no
        # payload text reaches a log or traceback.
        raise ExportReceiptFormatError(
            "export receipts record file is malformed"
        ) from None
    if not isinstance(data, dict):
        raise ExportReceiptFormatError("export receipts record must be a JSON object")
    if set(data) != _TOP_KEYS:
        raise ExportReceiptFormatError(
            "export receipts record has unknown or missing keys"
        )
    version = data["version"]
    if type(version) is not int or version != RECEIPTS_SCHEMA_VERSION:
        raise ExportReceiptFormatError("export receipts record version is unsupported")
    stored = data["receipts"]
    if not isinstance(stored, dict):
        raise ExportReceiptFormatError("export receipts must be a JSON object")
    receipts: dict[str, ExportReceipt] = {}
    for key, value in stored.items():
        if not isinstance(value, dict) or set(value) != _RECEIPT_KEYS:
            raise ExportReceiptFormatError("export receipt entry has unknown or missing keys")
        try:
            receipt = ExportReceipt(
                day=value["day"],
                prompt_key=value["prompt_key"],
                action=value["action"],
                status=value["status"],
                task_id=value["task_id"],
                reason=value["reason"],
                updated_at=value["updated_at"],
            )
        except ValueError as exc:
            raise ExportReceiptFormatError("export receipt entry is malformed") from exc
        if key != receipt.key:
            raise ExportReceiptFormatError("export receipt key does not match its record")
        receipts[key] = receipt
    return ExportReceiptsRecord(receipts=receipts, version=version)


def load_receipts(*, path: str | Path | None = None) -> ExportReceiptsRecord:
    """Read the persisted receipts.

    An absent file returns the empty default WITHOUT creating anything.
    Malformed/unsupported storage raises :class:`ExportReceiptFormatError`; an
    unreadable file raises :class:`ExportReceiptError`."""
    target = Path(path) if path is not None else receipts_path()
    try:
        exists = target.is_file()
    except OSError:
        raise ExportReceiptError("export receipts record is unreadable") from None
    if not exists:
        return ExportReceiptsRecord()
    return _load_strict(target)


# ---------------------------------------------------------------------------
# Read-modify-write
# ---------------------------------------------------------------------------

def _write_record(target: Path, record: ExportReceiptsRecord) -> None:
    try:
        capacities_cache_io.atomic_write_json(target, record.as_dict())
    except OSError:
        # A full disk or a permission refusal must fail closed as a typed,
        # content-free error; the raw OS error (which carries the path) is
        # never chained. The existing bytes are left untouched by the atomic
        # write, so a retry never duplicates an already-landed task.
        raise ExportReceiptError(
            "export receipts record could not be written"
        ) from None


def _acquire_receipt_lock(lock: Path) -> Any:
    """Acquire the cross-process receipt lock, mapping storage OSError to a
    typed, content-free failure so it never escapes as a raw OSError."""
    try:
        return capacities_cache_io.acquire_path_lock(lock)
    except OSError:
        raise ExportReceiptError(
            "export receipts store lock is unavailable"
        ) from None


def _validate_inputs(day: Any, prompt_key: Any, action: Any) -> str:
    try:
        valid_day = _parse_day(day)
    except ValueError as exc:
        raise ExportReceiptValidationError(str(exc)) from exc
    if prompt_key not in prompt_state.PROMPT_KEYS:
        raise ExportReceiptValidationError("unknown prompt key")
    if action not in KNOWN_ACTIONS:
        raise ExportReceiptValidationError("unknown export action")
    return valid_day


def begin_export(
    *, day: str, prompt_key: str, action: str, path: str | Path | None = None
) -> BeginOutcome:
    """Claim (or observe) the receipt for one export action.

    Under the cross-process lock:

    * an existing ``done`` receipt yields ``skip_done`` — never re-create;
    * an existing ``pending``/``needs_review`` receipt yields
      ``needs_review`` — an unconfirmed outcome is never blind-retried;
    * an absent receipt is written ``pending`` and yields ``begin``, so the
      caller owns the only provider call for this key.

    The pending write is durable before the caller touches the provider."""
    valid_day = _validate_inputs(day, prompt_key, action)
    target = Path(path) if path is not None else receipts_path()
    lock = receipts_lock_path()
    with capacities_cache_io.store_lock(lock):
        fh = _acquire_receipt_lock(lock)
        try:
            record = load_receipts(path=target)
            existing = record.get(valid_day, prompt_key, action)
            if existing is not None:
                if existing.status == STATUS_DONE:
                    return BeginOutcome(action=SKIP_DONE, receipt=existing)
                return BeginOutcome(action=NEEDS_REVIEW, receipt=existing)
            pending = ExportReceipt(
                day=valid_day, prompt_key=prompt_key, action=action,
                status=STATUS_PENDING, updated_at=_utcnow(),
            )
            updated = dict(record.receipts)
            updated[pending.key] = pending
            _write_record(target, ExportReceiptsRecord(receipts=updated))
            return BeginOutcome(action=BEGIN, receipt=pending)
        finally:
            capacities_cache_io.release_lock_file(fh)


def complete_export(
    *, day: str, prompt_key: str, action: str, task_id: str,
    path: str | Path | None = None,
) -> ExportReceipt:
    """Persist the confirmed ``done`` receipt carrying the provider task id."""
    valid_day = _validate_inputs(day, prompt_key, action)
    try:
        clean_task_id = _clean_task_id(task_id)
    except ValueError as exc:
        raise ExportReceiptValidationError(str(exc)) from exc
    if clean_task_id is None:
        raise ExportReceiptValidationError("task_id is required to complete an export")
    target = Path(path) if path is not None else receipts_path()
    lock = receipts_lock_path()
    with capacities_cache_io.store_lock(lock):
        fh = _acquire_receipt_lock(lock)
        try:
            record = load_receipts(path=target)
            done = ExportReceipt(
                day=valid_day, prompt_key=prompt_key, action=action,
                status=STATUS_DONE, task_id=clean_task_id, updated_at=_utcnow(),
            )
            updated = dict(record.receipts)
            updated[done.key] = done
            _write_record(target, ExportReceiptsRecord(receipts=updated))
            return done
        finally:
            capacities_cache_io.release_lock_file(fh)


def fail_export(
    *, day: str, prompt_key: str, action: str, reason: str | None = None,
    path: str | Path | None = None,
) -> ExportReceipt:
    """Persist a ``needs_review`` receipt after an errored/ambiguous attempt.

    The reason is bounded and content-free; the caller must never pass prompt
    text, a provider body, or a credential."""
    valid_day = _validate_inputs(day, prompt_key, action)
    try:
        clean_reason = _clean_reason(reason)
    except ValueError as exc:
        raise ExportReceiptValidationError(str(exc)) from exc
    target = Path(path) if path is not None else receipts_path()
    lock = receipts_lock_path()
    with capacities_cache_io.store_lock(lock):
        fh = _acquire_receipt_lock(lock)
        try:
            record = load_receipts(path=target)
            review = ExportReceipt(
                day=valid_day, prompt_key=prompt_key, action=action,
                status=STATUS_NEEDS_REVIEW, reason=clean_reason, updated_at=_utcnow(),
            )
            updated = dict(record.receipts)
            updated[review.key] = review
            _write_record(target, ExportReceiptsRecord(receipts=updated))
            return review
        finally:
            capacities_cache_io.release_lock_file(fh)
