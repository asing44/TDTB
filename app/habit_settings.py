"""Versioned local persistence for the per-habit duration / weekday policy.

U2 of the Grit-habits work. This module owns the ONE app-owned store the habit
settings API reads and writes: ``<app home>/state/habit_settings.json``
(``app_config.state_dir()``).

It mirrors :mod:`calendar_settings` — same file conventions (state-dir file,
beside-it ``flock`` lock file, atomic same-directory temp + ``os.replace``),
same server-owned ``version``/``revision`` pair, same optimistic-concurrency
rule (``expected_revision``), and the same fail-closed discipline for malformed
storage.

The store persists only the owner-edited habit plan: the Grit habit NAME
(identity), the planned ``minutes``, and the optional weekday selection. It
never persists Grit items, completion state, or any planning result.

Contract:

- Missing file is safe: :func:`load` returns the empty default (revision ``0``,
  no habits) with ``persisted=False`` WITHOUT creating a file.
- ``habits`` is sorted by ``name`` (plain code-point order, deterministic).
  The list is capped at ``MAX_HABITS`` (``artifact_source.HABIT_ITEMS_MAX``).
- **Identity is the trimmed Grit habit name.** Comparison is case-sensitive
  and exact, so ``"Walk"`` and ``"walk"`` are two habits and a rename is a new
  habit (a saved row that no Grit item matches is reported as ``missing``; it
  is never auto-deleted).
- ``name`` is stored trimmed; a blank name is rejected, and the length cap is
  ``artifact_source.HABIT_TEXT_MAX`` (200) — the same bound the planning
  artifact applies to habit text.
- ``minutes`` is a strict ``int`` (bools rejected) in ``0..480``
  (``artifact_source.HABIT_MINUTES_MAX``).
- **Weekdays use Python's ``datetime.date.weekday()`` numbering: Monday = 0
  … Sunday = 6.** ``null`` means every day. A list must be non-empty, unique,
  and in range; it is normalized to a sorted tuple. An EMPTY list is a
  validation error, not a synonym for ``null``: the owner's explicit "no day"
  selection is a mistake the UI must surface, and silently widening it to
  "every day" would over-plan.
- Values are strict on read: ``version``/``revision``/``minutes`` are
  type-checked (bools, floats, strings rejected), unknown or missing keys are
  rejected, and duplicate JSON keys are rejected at every level.
- Malformed or unsupported storage fails closed: reads raise
  :class:`HabitSettingsFormatError`, and writes refuse while preserving the
  original bytes. Nothing is silently defaulted or erased.
- Writes validate every row BEFORE any file access, serialize read-modify-write
  under a process lock plus an advisory ``flock``, then replace atomically.
  A save whose ``expected_revision`` no longer matches the stored revision
  raises :class:`HabitSettingsConflictError` and leaves the bytes untouched.

Error → HTTP mapping the later route unit relies on:

- :class:`HabitSettingsConflictError` → 409 ``habit_settings_conflict``
- :class:`HabitSettingsFormatError`   → 409 (malformed existing storage)
- :class:`HabitSettingsInvalidRowError` → 422 (which row / which field)
- :class:`HabitSettingsStoreError`    → 500 (unreadable / unwritable storage)

Note the ordering trap inherited from :mod:`calendar_settings`: conflict and
format errors both SUBCLASS :class:`HabitSettingsStoreError`, so catch them
first. :class:`HabitSettingsInvalidRowError` is a ``ValueError`` and is not a
storage error at all.

The store is pure Python with no provider, credential, or network access.
"""
from __future__ import annotations

import json
import os
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
import artifact_source

# ---------------------------------------------------------------------------
# Paths and schema constants
# ---------------------------------------------------------------------------

STATE_FILENAME = "habit_settings.json"
STATE_LOCK_FILENAME = "habit_settings.lock"
SCHEMA_VERSION = 1

#: Bounds reuse the planning-artifact habit contract so a saved row can always
#: be expressed as a Grit item and vice versa.
MAX_HABITS = artifact_source.HABIT_ITEMS_MAX          # 200
MAX_NAME_LENGTH = artifact_source.HABIT_TEXT_MAX      # 200
MINUTES_MAX = artifact_source.HABIT_MINUTES_MAX       # 480
WEEKDAY_MIN = 0
WEEKDAY_MAX = 6
WEEKDAY_COUNT = 7

_TOP_KEYS = frozenset({"version", "revision", "habits"})
_ENTRY_KEYS = frozenset({"name", "minutes", "weekdays"})
#: Save inputs may omit ``weekdays`` (meaning every day). The persisted shape
#: always carries all three keys, so a read stays byte-shape strict.
_ENTRY_KEYS_REQUIRED = frozenset({"name", "minutes"})


class HabitSettingsStoreError(Exception):
    """Habit settings storage failure (HTTP 500) — callers fail closed and
    preserve the existing durable bytes."""


class HabitSettingsFormatError(HabitSettingsStoreError):
    """Existing habit settings storage is malformed or unsupported (HTTP 409).

    Raised for an unparseable file, duplicate JSON keys, unknown/missing keys,
    strict type violations, unsupported versions, or invalid rows. Callers
    must never overwrite the offending bytes."""


class HabitSettingsConflictError(HabitSettingsStoreError):
    """The caller's ``expected_revision`` is stale (HTTP 409
    ``habit_settings_conflict``) — a concurrent save won."""


class HabitSettingsInvalidRowError(ValueError):
    """One requested habit row is invalid (HTTP 422).

    Carries ``index`` (position in the submitted list, ``None`` when the error
    comes from constructing a :class:`HabitSetting` directly) and ``field``
    (``name`` / ``minutes`` / ``weekdays`` / ``None``) so the route can name
    exactly which row and field failed without echoing the other content.
    """

    def __init__(
        self,
        message: str,
        *,
        index: int | None = None,
        field: str | None = None,
    ) -> None:
        super().__init__(message)
        self.index = index
        self.field = field


def _invalid(message: str, *, field: str | None = None) -> HabitSettingsInvalidRowError:
    return HabitSettingsInvalidRowError(message, field=field)


def canonical_habit_name(value: Any) -> str:
    """Validate and return the trimmed habit name that is the row identity."""
    if not isinstance(value, str):
        raise _invalid(f"{value!r} is not a valid habit name", field="name")
    name = value.strip()
    if not name:
        raise _invalid("habit name must not be blank", field="name")
    if len(name) > MAX_NAME_LENGTH:
        raise _invalid(
            f"habit name is longer than {MAX_NAME_LENGTH} characters",
            field="name",
        )
    return name


def canonical_minutes(value: Any) -> int:
    """Validate the planned duration: a strict ``int`` in ``0..480``."""
    if type(value) is not int:
        raise _invalid(
            f"{value!r} is not a valid habit duration in minutes",
            field="minutes",
        )
    if not 0 <= value <= MINUTES_MAX:
        raise _invalid(
            f"habit minutes must be between 0 and {MINUTES_MAX}",
            field="minutes",
        )
    return value


def canonical_weekdays(value: Any) -> tuple[int, ...] | None:
    """Validate a weekday selection (``None`` = every day).

    Weekday numbers are ``datetime.date.weekday()`` numbers (Monday = 0 …
    Sunday = 6). A list must be non-empty, in range, and unique; the normalized
    result is a sorted tuple. An empty list is REJECTED rather than widened to
    ``None`` — see the module docstring.
    """
    if value is None:
        return None
    if isinstance(value, (str, bytes)) or not isinstance(value, Iterable):
        raise _invalid(
            f"{value!r} is not a valid weekday selection", field="weekdays"
        )
    days: list[int] = []
    for raw in value:
        if type(raw) is not int:
            raise _invalid(
                f"{raw!r} is not a valid weekday", field="weekdays"
            )
        if not WEEKDAY_MIN <= raw <= WEEKDAY_MAX:
            raise _invalid(
                f"weekday must be between {WEEKDAY_MIN} and {WEEKDAY_MAX}",
                field="weekdays",
            )
        if raw in days:
            raise _invalid(
                f"weekday {raw} is listed more than once", field="weekdays"
            )
        days.append(raw)
    if not days:
        raise _invalid(
            "weekdays must list at least one day (null means every day)",
            field="weekdays",
        )
    return tuple(sorted(days))


@dataclass(frozen=True)
class HabitSetting:
    """One saved habit row.

    ``name`` is the trimmed Grit habit name and the row identity;
    ``weekdays=None`` means every day. Construction is strict and happens
    BEFORE any file access.
    """

    name: str
    minutes: int
    weekdays: tuple[int, ...] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", canonical_habit_name(self.name))
        object.__setattr__(self, "minutes", canonical_minutes(self.minutes))
        object.__setattr__(self, "weekdays", canonical_weekdays(self.weekdays))

    def weekday_list(self) -> list[int] | None:
        """The persisted/JSON form of the weekday selection."""
        return None if self.weekdays is None else list(self.weekdays)

    def due_on(self, weekday: int) -> bool:
        """Whether this habit is planned for ``weekday`` (Monday = 0)."""
        return self.weekdays is None or weekday in self.weekdays

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "minutes": self.minutes,
            "weekdays": self.weekday_list(),
        }


@dataclass(frozen=True)
class HabitSettings:
    """The full persisted habit plan.

    ``revision`` is server-owned and monotonic. ``habits`` is sorted by
    ``name``; duplicates raise rather than collapsing or merging.
    """

    revision: int = 0
    habits: tuple[HabitSetting, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if type(self.revision) is not int or self.revision < 0:
            raise ValueError("revision must be a nonnegative integer")
        rows: list[HabitSetting] = []
        seen: set[str] = set()
        for entry in self.habits:
            if not isinstance(entry, HabitSetting):
                raise _invalid(f"{entry!r} is not a habit setting entry")
            if entry.name in seen:
                raise _invalid(
                    f"duplicate habit name {entry.name!r}", field="name"
                )
            seen.add(entry.name)
            rows.append(entry)
        if len(rows) > MAX_HABITS:
            raise ValueError("too many habits")
        rows.sort(key=lambda entry: entry.name)
        object.__setattr__(self, "habits", tuple(rows))

    def as_dict(self) -> dict[str, Any]:
        """The exact persisted JSON shape (sorted, deterministic)."""
        return {
            "version": SCHEMA_VERSION,
            "revision": self.revision,
            "habits": [entry.as_dict() for entry in self.habits],
        }

    def find(self, name: str) -> HabitSetting | None:
        """The saved row for a trimmed habit name, or ``None``."""
        for entry in self.habits:
            if entry.name == name:
                return entry
        return None


@dataclass(frozen=True)
class HabitSettingsRead:
    """Result of a settings read: the plan plus whether a file existed."""

    settings: HabitSettings
    persisted: bool


def settings_path() -> Path:
    """The active store path: ``<app home>/state/habit_settings.json``."""
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


def _entry_from_mapping(
    value: Any, *, index: int | None = None, strict_keys: bool = True
) -> HabitSetting:
    """Build a strict :class:`HabitSetting` from a mapping or entry.

    ``strict_keys`` requires all three persisted keys; otherwise the mapping
    may omit ``weekdays`` (a save input meaning every day).
    """
    if isinstance(value, HabitSetting):
        return value
    if not isinstance(value, Mapping):
        raise HabitSettingsInvalidRowError(
            f"{value!r} is not a habit setting entry", index=index
        )
    keys = set(value)
    allowed = _ENTRY_KEYS
    required = _ENTRY_KEYS if strict_keys else _ENTRY_KEYS_REQUIRED
    if keys - allowed or not required <= keys:
        raise HabitSettingsInvalidRowError(
            "habit setting entries have unknown or missing keys", index=index
        )
    try:
        return HabitSetting(
            name=value["name"],
            minutes=value["minutes"],
            weekdays=value.get("weekdays"),
        )
    except HabitSettingsInvalidRowError as exc:
        exc.index = index
        raise


def _decode(data: Any) -> HabitSettings:
    """Decode a parsed JSON document into strict settings or raise."""
    if not isinstance(data, dict):
        raise HabitSettingsFormatError("habit settings must be a JSON object")
    if set(data) != _TOP_KEYS:
        raise HabitSettingsFormatError(
            "habit settings has unknown or missing top-level keys"
        )
    version = data["version"]
    if type(version) is not int or version != SCHEMA_VERSION:
        raise HabitSettingsFormatError(
            f"habit settings version {version!r} is unsupported"
        )
    revision = data["revision"]
    if type(revision) is not int or revision < 0:
        raise HabitSettingsFormatError(
            "habit settings revision must be a nonnegative integer"
        )
    raw = data["habits"]
    if not isinstance(raw, list):
        raise HabitSettingsFormatError("habit settings habits must be a list")
    if len(raw) > MAX_HABITS:
        raise HabitSettingsFormatError("habit settings has too many habits")
    entries: list[HabitSetting] = []
    for index, row in enumerate(raw):
        try:
            entries.append(_entry_from_mapping(row, index=index))
        except ValueError as exc:
            raise HabitSettingsFormatError(
                f"habit settings has an invalid habit entry at index {index}"
            ) from exc
    try:
        return HabitSettings(revision=revision, habits=tuple(entries))
    except ValueError as exc:
        raise HabitSettingsFormatError("habit settings is malformed") from exc


def _load_strict(path: Path) -> HabitSettings:
    """Strict read of an existing file. Raises on malformed/unsupported data
    or I/O failure — never repairs, defaults, or erases."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise HabitSettingsStoreError(
            "habit settings storage is unreadable"
        ) from exc
    try:
        data = json.loads(text, object_pairs_hook=_reject_duplicate_keys)
    except ValueError as exc:
        raise HabitSettingsFormatError("habit settings file is malformed") from exc
    return _decode(data)


def load() -> HabitSettingsRead:
    """Read the app-owned habit settings. Never writes, never migrates.

    A missing file returns the empty default plan with ``persisted=False`` and
    creates nothing. Malformed/unsupported storage raises
    :class:`HabitSettingsFormatError`; an unreadable file raises
    :class:`HabitSettingsStoreError`.
    """
    try:
        path = settings_path()
        exists = os.path.lexists(path)
    except (OSError, TypeError, ValueError) as exc:  # pragma: no cover — defensive
        raise HabitSettingsStoreError(
            "habit settings storage is unreadable"
        ) from exc
    if not exists:
        return HabitSettingsRead(settings=HabitSettings(), persisted=False)
    return HabitSettingsRead(settings=_load_strict(path), persisted=True)


# ---------------------------------------------------------------------------
# Locking + atomic write
# ---------------------------------------------------------------------------
# Process-local RMW locks (the same single-process convention as
# ``calendar_settings``); the flock on the app-home lock file adds
# cross-process serialization for the same bytes.

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


def validate_habits(habits: Any) -> list[HabitSetting]:
    """Validate a submitted habit collection BEFORE any file access.

    Rows may be :class:`HabitSetting` instances or mappings carrying ``name``,
    ``minutes``, and an optional ``weekdays``. The first offending row raises
    :class:`HabitSettingsInvalidRowError` naming its index and field.
    """
    if isinstance(habits, (str, bytes)) or isinstance(habits, Mapping):
        raise HabitSettingsInvalidRowError(
            "habits must be a collection of habit entries"
        )
    if not isinstance(habits, Iterable):
        raise HabitSettingsInvalidRowError(
            "habits must be a collection of habit entries"
        )
    entries: list[HabitSetting] = []
    seen: set[str] = set()
    for index, value in enumerate(habits):
        entry = _entry_from_mapping(value, index=index, strict_keys=False)
        if entry.name in seen:
            raise HabitSettingsInvalidRowError(
                f"duplicate habit name {entry.name!r}",
                index=index,
                field="name",
            )
        seen.add(entry.name)
        entries.append(entry)
    if len(entries) > MAX_HABITS:
        raise HabitSettingsInvalidRowError(f"at most {MAX_HABITS} habits")
    return entries


def save_full_replace(
    new_habits: Any, expected_revision: int
) -> HabitSettings:
    """Explicitly replace the whole habit plan (full replacement).

    ``version`` and ``revision`` stay server-owned: the saved revision is the
    stored revision plus one. Rows are validated before any file access.

    Under the store lock the current file is read, its revision compared with
    ``expected_revision``, and only then is the file replaced atomically. A
    stale revision raises :class:`HabitSettingsConflictError`; malformed
    storage raises :class:`HabitSettingsFormatError`; an unwritable store
    raises :class:`HabitSettingsStoreError`. All three preserve the original
    bytes.
    """
    if type(expected_revision) is not int or expected_revision < 0:
        raise ValueError("expected_revision must be a nonnegative integer")
    entries = validate_habits(new_habits)

    with _store_lock():
        try:
            fh = _acquire_lock_file()
        except OSError as exc:
            raise HabitSettingsStoreError(
                "habit settings storage is unwritable"
            ) from exc
        try:
            current = load().settings
            if current.revision != expected_revision:
                raise HabitSettingsConflictError(
                    "habit settings changed since they were read "
                    f"(stored revision {current.revision}, expected "
                    f"{expected_revision})"
                )
            saved = HabitSettings(
                revision=current.revision + 1, habits=tuple(entries)
            )
            try:
                _atomic_write_json(settings_path(), saved.as_dict())
            except OSError as exc:
                raise HabitSettingsStoreError(
                    "habit settings storage is unwritable"
                ) from exc
        finally:
            _release_lock_file(fh)
    return saved


# ---------------------------------------------------------------------------
# Read-only helpers for the later route / planning units
# ---------------------------------------------------------------------------

def due_today(settings: HabitSettings, weekday: int) -> list[HabitSetting]:
    """Habits planned for ``weekday`` (``datetime.date.weekday()``, Monday = 0).

    ``weekdays=None`` (every day) is always due; a saved list is due only when
    it contains ``weekday``. The result keeps the store's name order.
    """
    if not isinstance(settings, HabitSettings):
        raise TypeError("settings must be a HabitSettings")
    if type(weekday) is not int or not WEEKDAY_MIN <= weekday <= WEEKDAY_MAX:
        raise ValueError(
            f"weekday must be an integer between {WEEKDAY_MIN} and {WEEKDAY_MAX}"
        )
    return [entry for entry in settings.habits if entry.due_on(weekday)]


def inventory(settings: HabitSettings, grit_names: Iterable[str]) -> list[dict[str, Any]]:
    """Reconcile the saved plan against the current Grit habit names.

    Returns one row per name, sorted by ``name``:

    ``{"name", "status", "minutes", "weekdays"}`` where ``status`` is

    - ``configured`` — saved AND present in Grit (with its saved values);
    - ``unconfigured`` — present in Grit with no saved row (``minutes`` and
      ``weekdays`` are ``None``);
    - ``missing`` — saved but absent from Grit (``rename`` shape: the old name
      is ``missing`` and the new one ``unconfigured``). Missing rows are
      reported, never auto-deleted.

    ``weekdays`` is ``None`` (every day) or a sorted list. Grit names are
    trimmed for comparison; blank and repeated names are ignored.
    """
    if not isinstance(settings, HabitSettings):
        raise TypeError("settings must be a HabitSettings")
    if isinstance(grit_names, (str, bytes)):
        raise ValueError("grit_names must be a collection of names")

    def _row(entry: HabitSetting, status: str) -> dict[str, Any]:
        return {
            "name": entry.name,
            "status": status,
            "minutes": entry.minutes,
            "weekdays": entry.weekday_list(),
        }

    seen: set[str] = set()
    rows: list[dict[str, Any]] = []
    for raw in grit_names or ():
        if not isinstance(raw, str):
            raise ValueError(f"{raw!r} is not a valid Grit habit name")
        name = raw.strip()
        if not name or name in seen:
            continue
        seen.add(name)
        saved = settings.find(name)
        if saved is None:
            rows.append(
                {
                    "name": name,
                    "status": "unconfigured",
                    "minutes": None,
                    "weekdays": None,
                }
            )
        else:
            rows.append(_row(saved, "configured"))
    for entry in settings.habits:
        if entry.name not in seen:
            rows.append(_row(entry, "missing"))
    rows.sort(key=lambda row: row["name"])
    return rows
