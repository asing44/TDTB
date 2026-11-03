"""Versioned local persistence for the per-calendar on/off policy.

This module owns the ONE app-owned store the calendar settings API reads and
writes: ``<app home>/state/calendar_settings.json`` (``app_config.state_dir()``).

It replaces the hand-edited config ``calendar.disabled`` list (``Disabled
Calendars`` section) with an owner-managed per-calendar default. It persists
only the policy — a stable EventKit ``calendarIdentifier`` plus the title
snapshot at save time and the on/off flag — plus the server-owned schema
``version`` and a monotonic ``revision``. It never persists source
properties, event data, or effective-planning results.

Contract:

- Missing file is safe: :func:`read_settings` returns the default policy
  (revision ``0``, no entries) with ``persisted=False`` WITHOUT creating a
  file. An unseen calendar defaults to ON, so an empty policy and an absent
  file mean the same thing: nothing is switched off.
- ``calendars`` is sorted by ``id``; duplicate ids are rejected, never
  collapsed. The list is capped at ``MAX_CALENDARS`` (200).
- Reads use the state-dir file only. Writes always land there.
- Malformed or unsupported storage fails closed: reads raise
  :class:`CalendarSettingsFormatError`, and writes refuse while preserving the
  original bytes. Nothing is silently defaulted or erased.
- Values are strict: ``version``/``revision``/``enabled`` are type-checked
  (bools, floats, strings are rejected where an int or a bool is expected) and
  duplicate JSON keys are rejected at every level.
- Writes serialize read-modify-write under a process lock plus an advisory
  ``flock`` on the app-home lock file, then replace atomically (unique
  same-directory temp + flush/fsync + ``os.replace``). A save whose
  ``expected_revision`` no longer matches the stored revision raises
  :class:`CalendarSettingsConflictError` and leaves the bytes untouched.
- :func:`migrate_legacy_disabled` is the ONE-TIME, idempotent migration off the
  retired config list: it only writes when no store file exists yet, resolves
  the legacy entries (title or identifier) against the supplied calendar
  inventory, and writes them as ``enabled: false`` at revision ``1``. Nothing
  resolved means nothing is written, so an unresolvable legacy list keeps
  being honored by the caller's fallback instead of being silently dropped.

The store is pure Python with no provider, credential, or network access.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

try:
    import fcntl as _fcntl  # POSIX advisory file locks (macOS/Linux)
except ImportError:  # pragma: no cover — non-POSIX fallback
    _fcntl = None

import app_config

# ---------------------------------------------------------------------------
# Paths and schema constants
# ---------------------------------------------------------------------------

STATE_FILENAME = "calendar_settings.json"
STATE_LOCK_FILENAME = "calendar_settings.lock"
SCHEMA_VERSION = 1

#: Bounds. The calendar inventory on a normal machine is a few dozen entries;
#: 200 leaves generous headroom while keeping the file bounded.
MAX_CALENDARS = 200
MAX_ID_LENGTH = 256
MAX_TITLE_LENGTH = 300

_TOP_KEYS = frozenset({"version", "revision", "calendars"})
_ENTRY_KEYS = frozenset({"id", "title", "enabled"})


class CalendarSettingsStoreError(Exception):
    """Calendar settings storage failure — callers fail closed and preserve
    the existing durable bytes."""


class CalendarSettingsFormatError(CalendarSettingsStoreError):
    """Existing calendar settings storage is malformed or unsupported.

    Raised for an unparseable file, duplicate JSON keys, unknown/missing keys,
    strict type violations, unsupported versions, or invalid calendar
    identities. Callers must never overwrite the offending bytes."""


class CalendarSettingsConflictError(CalendarSettingsStoreError):
    """The caller's ``expected_revision`` is stale — a concurrent save won."""


def canonical_calendar_id(value: Any) -> str:
    """Validate and return a bounded, non-blank calendar identifier.

    EventKit identifiers are opaque strings; identity is the exact string (no
    case folding), so two spellings are two calendars."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{value!r} is not a valid calendar id")
    if len(value) > MAX_ID_LENGTH:
        raise ValueError("calendar id is too long")
    return value


def canonical_calendar_title(value: Any) -> str:
    """Validate and return a bounded display title snapshot (may be empty: a
    calendar can be renamed or unnamed at read time)."""
    if not isinstance(value, str):
        raise ValueError(f"{value!r} is not a valid calendar title")
    if len(value) > MAX_TITLE_LENGTH:
        raise ValueError("calendar title is too long")
    return value


@dataclass(frozen=True)
class CalendarSetting:
    """One per-calendar policy row.

    ``id`` is the stable EventKit ``calendarIdentifier`` and ``title`` the
    display snapshot taken at save time (presentation only, never identity).
    Construction is strict and happens BEFORE any file access.
    """

    id: str
    title: str = ""
    enabled: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", canonical_calendar_id(self.id))
        object.__setattr__(self, "title", canonical_calendar_title(self.title))
        if type(self.enabled) is not bool:
            raise ValueError("enabled must be a boolean")
        object.__setattr__(self, "enabled", self.enabled)

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "title": self.title, "enabled": self.enabled}


@dataclass(frozen=True)
class CalendarSettings:
    """The full persisted per-calendar policy.

    ``revision`` is server-owned and monotonic. ``calendars`` is sorted by
    ``id``; duplicates raise rather than collapsing. An id absent from
    ``calendars`` is ON by default (the store only records what was switched
    off plus any row the owner saved explicitly).
    """

    revision: int = 0
    calendars: tuple[CalendarSetting, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if type(self.revision) is not int or self.revision < 0:
            raise ValueError("revision must be a nonnegative integer")
        rows: list[CalendarSetting] = []
        seen: set[str] = set()
        for entry in self.calendars:
            if not isinstance(entry, CalendarSetting):
                raise ValueError("calendars must contain CalendarSetting entries")
            if entry.id in seen:
                raise ValueError("duplicate calendar id")
            seen.add(entry.id)
            rows.append(entry)
        if len(rows) > MAX_CALENDARS:
            raise ValueError("too many calendars")
        rows.sort(key=lambda e: e.id)
        object.__setattr__(self, "calendars", tuple(rows))

    def as_dict(self) -> dict[str, Any]:
        """The exact persisted JSON shape (sorted, deterministic)."""
        return {
            "version": SCHEMA_VERSION,
            "revision": self.revision,
            "calendars": [entry.as_dict() for entry in self.calendars],
        }

    def is_enabled(self, calendar_id: str) -> bool:
        """Effective state for one calendar: unseen calendars default ON."""
        for entry in self.calendars:
            if entry.id == calendar_id:
                return entry.enabled
        return True

    def off_ids(self) -> list[str]:
        """Identifiers the owner switched off (sorted)."""
        return [entry.id for entry in self.calendars if not entry.enabled]


@dataclass(frozen=True)
class CalendarSettingsRead:
    """Result of a settings read: the policy plus whether a file existed."""

    settings: CalendarSettings
    persisted: bool


def settings_path() -> Path:
    """The active store path: ``<app home>/state/calendar_settings.json``."""
    return app_config.state_dir() / STATE_FILENAME


def lock_path() -> Path:
    """The active lock-file path, beside the store in the app home.

    A 0-byte runtime artifact created on demand; never migrated."""
    return app_config.state_dir() / STATE_LOCK_FILENAME


# ---------------------------------------------------------------------------
# Strict decoding
# ---------------------------------------------------------------------------

def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """``object_pairs_hook`` that rejects duplicate keys at every level."""
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def _entry_from_mapping(value: Any) -> CalendarSetting:
    """Build a strict :class:`CalendarSetting` from a mapping or entry."""
    if isinstance(value, CalendarSetting):
        return value
    if not isinstance(value, dict):
        raise ValueError(f"{value!r} is not a calendar setting entry")
    if set(value) != _ENTRY_KEYS:
        raise ValueError("calendar setting entries have unknown or missing keys")
    return CalendarSetting(
        id=value["id"], title=value["title"], enabled=value["enabled"]
    )


def _decode(data: Any) -> CalendarSettings:
    """Decode a parsed JSON document into strict settings or raise."""
    if not isinstance(data, dict):
        raise CalendarSettingsFormatError(
            "calendar settings must be a JSON object"
        )
    if set(data) != _TOP_KEYS:
        raise CalendarSettingsFormatError(
            "calendar settings has unknown or missing top-level keys"
        )
    version = data["version"]
    if type(version) is not int or version != SCHEMA_VERSION:
        raise CalendarSettingsFormatError(
            f"calendar settings version {version!r} is unsupported"
        )
    revision = data["revision"]
    if type(revision) is not int or revision < 0:
        raise CalendarSettingsFormatError(
            "calendar settings revision must be a nonnegative integer"
        )
    raw = data["calendars"]
    if not isinstance(raw, list):
        raise CalendarSettingsFormatError("calendar settings calendars must be a list")
    if len(raw) > MAX_CALENDARS:
        raise CalendarSettingsFormatError("calendar settings has too many calendars")
    entries: list[CalendarSetting] = []
    for row in raw:
        try:
            entries.append(_entry_from_mapping(row))
        except ValueError as exc:
            raise CalendarSettingsFormatError(
                "calendar settings has an invalid calendar entry"
            ) from exc
    try:
        return CalendarSettings(revision=revision, calendars=tuple(entries))
    except ValueError as exc:
        raise CalendarSettingsFormatError("calendar settings is malformed") from exc


def _load_strict(path: Path) -> CalendarSettings:
    """Strict read of an existing file. Raises on malformed/unsupported data
    or I/O failure — never repairs, defaults, or erases."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CalendarSettingsStoreError(
            "calendar settings storage is unreadable"
        ) from exc
    try:
        data = json.loads(text, object_pairs_hook=_reject_duplicate_keys)
    except ValueError as exc:
        raise CalendarSettingsFormatError(
            "calendar settings file is malformed"
        ) from exc
    return _decode(data)


def read_settings() -> CalendarSettingsRead:
    """Read the app-owned calendar settings. Never writes, never migrates.

    A missing file returns the default (empty) policy with ``persisted=False``
    and creates nothing. Malformed/unsupported storage raises
    :class:`CalendarSettingsFormatError`; an unreadable file raises
    :class:`CalendarSettingsStoreError`.
    """
    try:
        path = settings_path()
        exists = os.path.lexists(path)
    except (OSError, TypeError, ValueError) as exc:  # pragma: no cover — defensive
        raise CalendarSettingsStoreError(
            "calendar settings storage is unreadable"
        ) from exc
    if not exists:
        return CalendarSettingsRead(settings=CalendarSettings(), persisted=False)
    return CalendarSettingsRead(settings=_load_strict(path), persisted=True)


# ---------------------------------------------------------------------------
# Locking + atomic write
# ---------------------------------------------------------------------------
# Process-local RMW locks (the same single-process convention as
# ``exclusion_settings`` / ``capacities_settings``); the flock on the app-home
# lock file adds cross-process serialization for the same bytes.

_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _store_lock() -> threading.Lock:
    key = str(lock_path().resolve())
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.Lock())


def _acquire_lock_file() -> Any:
    """Advisory exclusive flock on the app-home lock file."""
    path = lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(path, "a+", encoding="utf-8")
    try:
        if _fcntl is not None:
            _fcntl.flock(fh.fileno(), _fcntl.LOCK_EX)
    except BaseException:
        fh.close()
        raise
    return fh


def _release_lock_file(fh: Any) -> None:
    try:
        if _fcntl is not None:
            _fcntl.flock(fh.fileno(), _fcntl.LOCK_UN)
    except OSError:
        pass
    try:
        fh.close()
    except OSError:
        pass


def _atomic_write_json(path: str | Path, data: dict[str, Any]) -> None:
    """Write ``data`` atomically: a unique temp file in the same directory,
    flushed and fsynced, then ``os.replace``."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=f".{p.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, p)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _validate_entries(calendars: Any, locked_on_ids: Iterable[str] = ()) -> list[CalendarSetting]:
    """Validate a calendar collection and coerce locked calendars back ON.

    Locked ids (TDTB's own writable zone calendars) can never be saved off:
    a request that asks for ``enabled: false`` on one is stored as ``true``.
    """
    if isinstance(calendars, (str, bytes)):
        raise ValueError("calendars must be a collection of calendar entries")
    locked = {str(value) for value in locked_on_ids}
    entries: list[CalendarSetting] = []
    seen: set[str] = set()
    for value in calendars:
        entry = _entry_from_mapping(value)
        if entry.id in seen:
            raise ValueError("duplicate calendar id")
        seen.add(entry.id)
        if entry.id in locked and not entry.enabled:
            entry = CalendarSetting(id=entry.id, title=entry.title, enabled=True)
        entries.append(entry)
    if len(entries) > MAX_CALENDARS:
        raise ValueError("too many calendars")
    return entries


def save_settings(
    *,
    expected_revision: int,
    calendars: Any = (),
    locked_on_ids: Iterable[str] = (),
) -> CalendarSettings:
    """Explicitly replace the whole calendar policy (full replacement).

    ``version`` and ``revision`` stay server-owned: the saved revision is the
    stored revision plus one. Inputs are validated BEFORE any file access.

    Under the store lock the current file is read, its revision compared with
    ``expected_revision``, and only then is the file replaced atomically. A
    stale revision raises :class:`CalendarSettingsConflictError`; malformed
    storage raises :class:`CalendarSettingsFormatError`; both preserve the
    original bytes.
    """
    if type(expected_revision) is not int or expected_revision < 0:
        raise ValueError("expected_revision must be a nonnegative integer")
    entries = _validate_entries(calendars, locked_on_ids)

    with _store_lock():
        fh = _acquire_lock_file()
        try:
            current = read_settings().settings
            if current.revision != expected_revision:
                raise CalendarSettingsConflictError(
                    "calendar settings changed since they were read "
                    f"(stored revision {current.revision}, expected "
                    f"{expected_revision})"
                )
            saved = CalendarSettings(
                revision=current.revision + 1, calendars=tuple(entries)
            )
            _atomic_write_json(settings_path(), saved.as_dict())
        finally:
            _release_lock_file(fh)
    return saved


def _casefold(value: Any) -> str:
    return str(value).strip().casefold() if value is not None else ""


def resolve_legacy_entries(
    entries: Iterable[str], inventory: Iterable[Mapping[str, Any]]
) -> tuple[list[str], list[str]]:
    """Resolve retired config ``Disabled Calendars`` entries against the live
    inventory.

    Each entry matches an inventory row by exact ``id`` (casefolded) or by
    exact ``title`` (casefolded); the identifier wins when both match. Returns
    ``(resolved_ids, unresolved_entries)``; ambiguous duplicates are simply
    resolved to every matching row.
    """
    rows = [row for row in inventory if row.get("id")]
    resolved: list[str] = []
    unresolved: list[str] = []
    for raw in entries:
        key = _casefold(raw)
        if not key:
            continue
        matched = [str(row["id"]) for row in rows if _casefold(row["id"]) == key]
        if not matched:
            matched = [
                str(row["id"])
                for row in rows
                if _casefold(row.get("title")) == key
            ]
        if not matched:
            unresolved.append(str(raw))
            continue
        for identifier in matched:
            if identifier not in resolved:
                resolved.append(identifier)
    return resolved, unresolved


def migrate_legacy_disabled(
    *,
    entries: Iterable[str],
    inventory: Iterable[Mapping[str, Any]],
    titles: Mapping[str, str] | None = None,
    locked_on_ids: Iterable[str] = (),
) -> tuple[CalendarSettingsRead, list[str]]:
    """One-time idempotent migration off the retired config list.

    Writes at most once: if a store file already exists the current read is
    returned untouched (the migration never re-applies, never rewrites, and
    never resurrects an entry the owner later switched back on).

    When the file is missing, the legacy ``entries`` (titles or identifiers)
    are resolved against ``inventory`` and written as ``enabled: false`` rows
    at revision ``1``. Nothing resolved means nothing is written — the caller's
    fallback keeps honoring the config list. Returns
    ``(read, unresolved_entries)``.
    """
    resolved, unresolved = resolve_legacy_entries(entries, inventory)
    # Own-write calendars are locked ON: a legacy entry never switches one off.
    locked = {str(i) for i in locked_on_ids}
    resolved = [identifier for identifier in resolved if identifier not in locked]
    read = read_settings()
    if read.persisted or not resolved:
        return read, unresolved
    rows: list[CalendarSetting] = []
    for identifier in resolved:
        title = ""
        for row in inventory:
            if str(row.get("id")) == identifier:
                title = str(row.get("title") or "")
                break
        if titles and identifier in titles:
            title = str(titles[identifier] or "")
        try:
            rows.append(CalendarSetting(id=identifier, title=title, enabled=False))
        except ValueError:
            continue
    if not rows:
        return read, unresolved
    with _store_lock():
        fh = _acquire_lock_file()
        try:
            current = read_settings()
            if current.persisted:
                return current, unresolved
            migrated = CalendarSettings(revision=1, calendars=tuple(rows))
            _atomic_write_json(settings_path(), migrated.as_dict())
        finally:
            _release_lock_file(fh)
    print(
        "calendar settings migrated: "
        f"{len(migrated.calendars)} calendar(s) carried over from the retired "
        "config 'Disabled Calendars' list; that config key is now ignored",
        file=sys.stderr,
    )
    return (
        CalendarSettingsRead(settings=migrated, persisted=True),
        unresolved,
    )
