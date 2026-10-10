"""prompt_state.py — local logical-day prompt draft and persistent opt-in
stores (U5 P1).

The morning prompts (Intention, For Meegy, Stoic) are private runtime state.
This module owns the two machine-local stores that back them, and nothing
else — no vault path, no provider call, no credential, no manifest/route/UI
wiring (those are later U5/U6 slices).

1. **Logical-day draft store** — ``<app home>/state/prompt-drafts-<day>.json``,
   one file per logical planning day, keyed by prompt key. A same-day draft
   survives a restart; the next day reads a different (absent) file, so the
   text does not carry forward. A save is a PATCH: keys present in the patch
   are set, an explicit empty string clears that key, and omitted keys are
   preserved. Drafts start empty and nothing is migrated from any legacy
   runstate or vault prompt text.

2. **Persistent opt-in store** — ``<app home>/state/prompt-optins.json``, an
   undated per-prompt boolean (default ``false``) with an optimistic
   ``revision``. The opt-in survives restarts and logical-day rollover.

Both stores reuse the established machine-local storage primitives
(:mod:`capacities_cache_io`): a strict duplicate-key-rejecting JSON decode, an
atomic same-directory temp write, a per-key process lock paired with a POSIX
advisory lock file. Existing storage is read strictly: malformed, unsupported,
or day-mismatched bytes raise and are never repaired or overwritten. On a
successful draft write, older draft files are removed best-effort; a failed
write cleans nothing.

Prompt content is private: no prompt string is ever included in an error
message, exception cause, or diagnostic. Errors are typed and content-free.
"""
from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from types import MappingProxyType
from typing import Any

import app_config
import capacities_cache_io

#: The known morning-prompt keys. Any other key is rejected on save and on
#: decode; the set is fixed by the product contract, not caller-supplied.
PROMPT_KEYS: tuple[str, ...] = ("intention", "megan_nicety", "stoic_intention")
_KNOWN_PROMPT_KEYS = frozenset(PROMPT_KEYS)

DRAFTS_SCHEMA_VERSION = 1
OPTINS_SCHEMA_VERSION = 1

DRAFTS_FILENAME_PREFIX = "prompt-drafts-"
DRAFTS_FILENAME_SUFFIX = ".json"
OPTINS_FILENAME = "prompt-optins.json"
DRAFTS_LOCK_FILENAME = "prompt-drafts.lock"
OPTINS_LOCK_FILENAME = "prompt-optins.lock"

_TOP_DRAFT_KEYS = frozenset({"version", "day", "drafts"})
_TOP_OPTIN_KEYS = frozenset({"version", "revision", "optins"})

_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# ---------------------------------------------------------------------------
# Errors (typed, content-free)
# ---------------------------------------------------------------------------

class PromptStateError(Exception):
    """Prompt draft or opt-in storage could not be read or written.

    Callers fail closed and preserve the existing durable bytes."""


class PromptFormatError(PromptStateError):
    """Existing prompt storage is malformed or unsupported.

    Raised for an unparseable file, duplicate JSON keys, unknown/missing keys,
    strict-type violations, an unsupported version, or a stored day that
    disagrees with the requested day. Callers must never overwrite the
    offending bytes."""


class PromptValidationError(PromptStateError):
    """The caller's prompt input is invalid; nothing was written.

    Raised for an invalid day, an unknown prompt key, a non-string draft, a
    non-boolean opt-in, or an invalid revision. The message never contains a
    prompt string."""


class PromptOptinConflict(PromptStateError):
    """The caller's ``expected_revision`` is stale — a concurrent save won.

    Carries both revisions so the caller can answer with them. The message
    names neither a path nor any payload content."""

    def __init__(self, *, expected_revision: int, current_revision: int) -> None:
        self.expected_revision = expected_revision
        self.current_revision = current_revision
        super().__init__(
            "prompt opt-ins changed since they were read "
            f"(stored revision {current_revision}, expected {expected_revision})"
        )


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

def _parse_day(value: Any) -> str:
    """Validate and normalize an ISO logical-day string (``YYYY-MM-DD``).

    Raises :class:`ValueError` (no value echoed) so callers can wrap it into
    the typed error appropriate to their context."""
    if not isinstance(value, str) or not _DAY_RE.match(value):
        raise ValueError("day must be a string of the form YYYY-MM-DD")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("day must be a valid calendar date") from exc
    return parsed.isoformat()


def _normalize_drafts(value: Any) -> dict[str, str]:
    """Validate a stored/patch drafts mapping into a clean key -> text dict.

    Unknown keys and non-string values raise :class:`ValueError` (no value
    echoed). An explicit empty string is a clear and is dropped, so an empty
    draft is simply an absent key."""
    if not isinstance(value, Mapping):
        raise ValueError("drafts must be a JSON object")
    result: dict[str, str] = {}
    for key, text in value.items():
        if key not in _KNOWN_PROMPT_KEYS:
            raise ValueError("drafts contain an unknown prompt key")
        if not isinstance(text, str):
            raise ValueError("draft text must be a string")
        if text == "":
            continue
        result[key] = text
    return result


def _normalize_patch(value: Any) -> dict[str, str]:
    """Validate a save patch, KEEPING explicit empty strings as clear markers.

    Differs from :func:`_normalize_drafts` only in that ``""`` is retained so
    the merge step can distinguish "clear" from "omit"."""
    if not isinstance(value, Mapping):
        raise ValueError("draft patch must be a JSON object")
    result: dict[str, str] = {}
    for key, text in value.items():
        if key not in _KNOWN_PROMPT_KEYS:
            raise ValueError("draft patch contains an unknown prompt key")
        if not isinstance(text, str):
            raise ValueError("draft text must be a string")
        result[key] = text
    return result


def _normalize_optins(value: Any) -> dict[str, bool]:
    """Validate an opt-in mapping and fill missing known keys with ``False``."""
    if not isinstance(value, Mapping):
        raise ValueError("optins must be a JSON object")
    result: dict[str, bool] = {key: False for key in PROMPT_KEYS}
    for key, flag in value.items():
        if key not in _KNOWN_PROMPT_KEYS:
            raise ValueError("optins contain an unknown prompt key")
        if type(flag) is not bool:
            raise ValueError("opt-in value must be a boolean")
        result[key] = flag
    return result


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PromptDraftsRecord:
    """The persisted drafts for one logical day: day plus key -> text."""

    day: str
    drafts: Mapping[str, str] = field(default_factory=dict)
    version: int = DRAFTS_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if type(self.version) is not int or self.version != DRAFTS_SCHEMA_VERSION:
            raise ValueError("unsupported prompt drafts version")
        object.__setattr__(self, "day", _parse_day(self.day))
        object.__setattr__(
            self, "drafts", MappingProxyType(_normalize_drafts(self.drafts))
        )

    def as_dict(self) -> dict[str, Any]:
        return {"version": self.version, "day": self.day, "drafts": dict(self.drafts)}


@dataclass(frozen=True)
class PromptOptinsRecord:
    """The persisted opt-ins: every known key, plus the store ``revision``."""

    optins: Mapping[str, bool] = field(default_factory=dict)
    revision: int = 0
    version: int = OPTINS_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if type(self.version) is not int or self.version != OPTINS_SCHEMA_VERSION:
            raise ValueError("unsupported prompt opt-ins version")
        if type(self.revision) is not int or self.revision < 0:
            raise ValueError("revision must be a nonnegative integer")
        object.__setattr__(
            self, "optins", MappingProxyType(_normalize_optins(self.optins))
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "revision": self.revision,
            "optins": dict(self.optins),
        }


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

def drafts_path(day: str) -> Path:
    """``<app home>/state/prompt-drafts-<day>.json`` for a validated day.

    An invalid day is refused before any path is built, so a caller-supplied
    value can never escape the state directory."""
    try:
        valid = _parse_day(day)
    except ValueError as exc:
        raise PromptValidationError(str(exc)) from exc
    return app_config.state_dir() / f"{DRAFTS_FILENAME_PREFIX}{valid}{DRAFTS_FILENAME_SUFFIX}"


def optins_path() -> Path:
    """``<app home>/state/prompt-optins.json``."""
    return app_config.state_dir() / OPTINS_FILENAME


def drafts_lock_path() -> Path:
    """The drafts lock file, beside the draft files in the app home."""
    return app_config.state_dir() / DRAFTS_LOCK_FILENAME


def optins_lock_path() -> Path:
    """The opt-ins lock file, beside the opt-in record in the app home."""
    return app_config.state_dir() / OPTINS_LOCK_FILENAME


# ---------------------------------------------------------------------------
# Strict decoding
# ---------------------------------------------------------------------------

def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """``object_pairs_hook`` that rejects duplicate keys at every level."""
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _load_drafts_strict(path: Path) -> PromptDraftsRecord:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PromptStateError("prompt drafts record is unreadable") from exc
    try:
        data = json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
    except ValueError:
        # The parse error may carry the raw document; suppress it so no prompt
        # content reaches a log or a traceback.
        raise PromptFormatError("prompt drafts record file is malformed") from None
    if not isinstance(data, dict):
        raise PromptFormatError("prompt drafts record must be a JSON object")
    if set(data) != _TOP_DRAFT_KEYS:
        raise PromptFormatError("prompt drafts record has unknown or missing keys")
    version = data["version"]
    if type(version) is not int or version != DRAFTS_SCHEMA_VERSION:
        raise PromptFormatError("prompt drafts record version is unsupported")
    try:
        return PromptDraftsRecord(day=data["day"], drafts=data["drafts"], version=version)
    except ValueError as exc:
        raise PromptFormatError("prompt drafts record is malformed") from exc


def _load_optins_strict(path: Path) -> PromptOptinsRecord:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PromptStateError("prompt opt-ins record is unreadable") from exc
    try:
        data = json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
    except ValueError:
        raise PromptFormatError("prompt opt-ins record file is malformed") from None
    if not isinstance(data, dict):
        raise PromptFormatError("prompt opt-ins record must be a JSON object")
    if set(data) != _TOP_OPTIN_KEYS:
        raise PromptFormatError("prompt opt-ins record has unknown or missing keys")
    version = data["version"]
    if type(version) is not int or version != OPTINS_SCHEMA_VERSION:
        raise PromptFormatError("prompt opt-ins record version is unsupported")
    revision = data["revision"]
    if type(revision) is not int or revision < 0:
        raise PromptFormatError(
            "prompt opt-ins record revision must be a nonnegative integer"
        )
    try:
        return PromptOptinsRecord(
            optins=data["optins"], revision=revision, version=version
        )
    except ValueError as exc:
        raise PromptFormatError("prompt opt-ins record is malformed") from exc


# ---------------------------------------------------------------------------
# Drafts store
# ---------------------------------------------------------------------------

def _read_current_drafts(target: Path, day: str) -> PromptDraftsRecord:
    """The stored record for ``day`` (empty default when absent).

    A file whose stored day disagrees with the requested day is refused rather
    than returned, so an exact-day read can never surface another day's text."""
    try:
        exists = target.is_file()
    except OSError as exc:
        raise PromptStateError("prompt drafts record is unreadable") from exc
    if not exists:
        return PromptDraftsRecord(day=day)
    record = _load_drafts_strict(target)
    if record.day != day:
        raise PromptFormatError(
            "prompt drafts record day does not match the requested day"
        )
    return record


def load_drafts(day: str, *, path: str | Path | None = None) -> PromptDraftsRecord:
    """Read the persisted drafts for the logical ``day``.

    An absent file returns the empty default WITHOUT creating anything.
    Malformed/unsupported storage raises :class:`PromptFormatError`; an
    unreadable file raises :class:`PromptStateError`. Reads never write and
    never return another day's text."""
    try:
        valid_day = _parse_day(day)
    except ValueError as exc:
        raise PromptValidationError(str(exc)) from exc
    target = Path(path) if path is not None else drafts_path(valid_day)
    return _read_current_drafts(target, valid_day)


def save_drafts(
    *, day: str, patch: Mapping[str, str], path: str | Path | None = None
) -> PromptDraftsRecord:
    """Validate and persist a patch of ``day``'s drafts.

    A PATCH, not a replacement: a key present in ``patch`` is set to its
    string value, an explicit empty string clears that key, and an omitted key
    keeps its stored value. Unknown keys and non-string values raise
    :class:`PromptValidationError` before any file access. A malformed existing
    file raises :class:`PromptFormatError` and its bytes are preserved (the
    write is refused, not repaired). Concurrent saves are serialized by the
    path lock, so each patch is applied to a consistent document. On success,
    older draft files are removed best-effort."""
    try:
        valid_day = _parse_day(day)
    except ValueError as exc:
        raise PromptValidationError(str(exc)) from exc
    try:
        normalized_patch = _normalize_patch(patch)
    except ValueError as exc:
        raise PromptValidationError(str(exc)) from exc

    target = Path(path) if path is not None else drafts_path(valid_day)
    lock = drafts_lock_path()
    with capacities_cache_io.store_lock(lock):
        fh = capacities_cache_io.acquire_path_lock(lock)
        try:
            current = _read_current_drafts(target, valid_day)
            merged = dict(current.drafts)
            for key, text in normalized_patch.items():
                if text == "":
                    merged.pop(key, None)
                else:
                    merged[key] = text
            saved = PromptDraftsRecord(day=valid_day, drafts=merged)
            capacities_cache_io.atomic_write_json(target, saved.as_dict())
        finally:
            capacities_cache_io.release_lock_file(fh)

    _cleanup_older_drafts(valid_day)
    return saved


def _cleanup_older_drafts(day: str) -> None:
    """Best-effort removal of draft files for days strictly before ``day``.

    Runs only after a successful write. Every failure is swallowed: cleanup is
    opportunistic and must never fail a save. Unparseable filenames and
    non-draft files are left untouched."""
    try:
        directory = app_config.state_dir()
        current = _parse_day(day)
        candidates = list(
            directory.glob(f"{DRAFTS_FILENAME_PREFIX}*{DRAFTS_FILENAME_SUFFIX}")
        )
    except OSError:
        return
    for candidate in candidates:
        stem = candidate.name[
            len(DRAFTS_FILENAME_PREFIX) : -len(DRAFTS_FILENAME_SUFFIX)
        ]
        try:
            candidate_day = _parse_day(stem)
        except ValueError:
            continue
        if candidate_day < current:
            try:
                candidate.unlink()
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Opt-ins store
# ---------------------------------------------------------------------------

def load_optins(*, path: str | Path | None = None) -> PromptOptinsRecord:
    """Read the persisted opt-ins.

    An absent file returns the all-``False`` default (revision 0) WITHOUT
    creating anything. Malformed/unsupported storage raises
    :class:`PromptFormatError`; an unreadable file raises
    :class:`PromptStateError`."""
    target = Path(path) if path is not None else optins_path()
    try:
        exists = target.is_file()
    except OSError as exc:
        raise PromptStateError("prompt opt-ins record is unreadable") from exc
    if not exists:
        return PromptOptinsRecord()
    return _load_optins_strict(target)


def save_optins(
    *,
    expected_revision: int,
    optins: Mapping[str, bool],
    path: str | Path | None = None,
) -> PromptOptinsRecord:
    """Validate and persist the opt-ins as a full replacement.

    Every known key is stored (an omitted key defaults to ``False``). A stale
    ``expected_revision`` raises :class:`PromptOptinConflict` and preserves the
    original bytes; invalid input raises :class:`PromptValidationError` before
    any file access. The saved ``revision`` is the stored revision plus one
    (0 -> 1 on the first save)."""
    if type(expected_revision) is not int or expected_revision < 0:
        raise PromptValidationError("expected_revision must be a nonnegative integer")
    try:
        normalized = _normalize_optins(optins)
    except ValueError as exc:
        raise PromptValidationError(str(exc)) from exc

    target = Path(path) if path is not None else optins_path()
    lock = optins_lock_path()
    with capacities_cache_io.store_lock(lock):
        fh = capacities_cache_io.acquire_path_lock(lock)
        try:
            current_revision = _current_optins_revision(target)
            if current_revision != expected_revision:
                raise PromptOptinConflict(
                    expected_revision=expected_revision,
                    current_revision=current_revision,
                )
            saved = PromptOptinsRecord(
                optins=normalized, revision=current_revision + 1
            )
            capacities_cache_io.atomic_write_json(target, saved.as_dict())
        finally:
            capacities_cache_io.release_lock_file(fh)

    return saved


def _current_optins_revision(target: Path) -> int:
    try:
        exists = target.is_file()
    except OSError as exc:
        raise PromptStateError("prompt opt-ins record is unreadable") from exc
    if not exists:
        return 0
    return _load_optins_strict(target).revision
