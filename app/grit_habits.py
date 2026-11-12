"""grit_habits.py — read the operator's Grit habits through a macOS Shortcut.

Habits reach TDTB only as a capacity summary (how many are still outstanding
today). They are moving off Todoist onto the macOS app **Grit**, which is read
through an operator-built, read-only Shortcut (no input) whose fixed name is
:data:`SHORTCUT_NAME`. Grit's App Intents expose a habit name, ``Get Habit
Progress`` (a number), and ``Get Habit History`` records shaped
``{numericAmount, recordedAt, recordKind, note}``; the Shortcut projects those
into the payload this module parses.

Output contract (``public.json`` from the Shortcut, strict)::

    {"schema": "tdtb.grit.v1",
     "generated_at": "<ISO8601>",
     "habits": [{"name": str,
                 "progress": number|null,
                 "records": [{"kind": str, "amount": number|null}]}]}

Decisions this module owns, and why:

- **The record-kind vocabulary is a placeholder.** The real ``recordKind``
  values are unknown until the operator captures sample Shortcut output
  (unit U0). The classifier is therefore ONE function
  (:func:`classify_habit`) driven by module-level kind constants
  (:data:`COMPLETION_KINDS`, :data:`FAILED_KINDS`, :data:`PROGRESS_KINDS`),
  each marked ``TODO(U0)``. Changing those constants is the whole change; the
  parser, the cache, and every caller stay untouched.
- **Status is derived, never persisted.** :attr:`HabitReading.status` is a
  property, so a post-U0 constant change also re-classifies an already cached
  read instead of leaving a stale verdict in the file.
- **Duplicate habit names are rejected, not deduped.** Two rows with the same
  name (compared casefolded, after trimming) would double-count one habit in
  the capacity summary; a typed failure keeps the number honest. The failure is
  ``duplicate-name`` so the operator can fix the Shortcut.
- **Only ``kind`` and ``amount`` are retained.** The Grit history record also
  carries a free-text ``note`` and a ``recordedAt``; neither is parsed or
  stored, so nothing habit-authored can leak into a cache file or a log. No
  habit name, note, or shortcut stdout is ever logged: the shortcut's stdout is
  captured and discarded.
- **Every record in the payload belongs to the read's logical day.** The
  contract has no per-record date, so the Shortcut (not this module) scopes the
  history to today; U0 confirms that scoping.
- **Unknown extra keys are ignored and never stored.** Required keys and types
  are strict; forward-compatible additions from the Shortcut do not break the
  read.
- **Failures are typed results, never exceptions.** A non-zero exit, a timeout,
  a missing or unparseable output file, or a contract violation all return a
  :class:`RunResult` / :class:`GritRead` carrying a ``reason`` constant. The
  caller decides the operator-facing warning.

Bounds are the artifact's own habit caps (``artifact_source``), imported rather
than copied so the two surfaces cannot drift.

Cache (``<app home>/state/grit_habits_cache.json``, ``app_config.state_dir()``):
the last good read plus the logical day it belongs to, following the
``calendar_settings`` / ``capacities_cache_io`` conventions — a process lock
plus a POSIX ``flock`` lock file, an atomic same-directory temp write
(flush/fsync/``os.replace``), and a fail-closed read (an unreadable or
malformed cache is "no cache", never a raise). The file reuses the payload
shape above with one extra ``logical_day`` key, so a cached read decodes
through the same strict parser as a live one.

The module is pure local-state Python: no route, no server, no credential, and
no network. It never mutates Grit.

A **plan read** (``/plan-inputs`` and the capacity segment) is cache-only and
never spawns the Shortcut: :func:`needs_refresh` tells it when the cached read
is missing, from an earlier logical day, or older than
:data:`CACHE_MAX_AGE_S`, and the caller kicks ONE detached :func:`refresh`
while serving what it has. :func:`failed_since_cache` is how a caller labels a
cached read that a later failed refresh attempt could not improve.
"""
from __future__ import annotations

import datetime
import json
import math
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, NamedTuple

import app_config
import artifact_source
import capacities_cache_io

_GATHER_DIR = str(Path(__file__).parent / "gather")
if _GATHER_DIR not in sys.path:
    sys.path.insert(0, _GATHER_DIR)

import tdtb_gather as gather  # noqa: E402  (path-shimmed import, see inventory.py)

# ---------------------------------------------------------------------------
# Contract constants
# ---------------------------------------------------------------------------

#: The operator-built, read-only Shortcut that projects Grit into JSON.
SHORTCUT_NAME = "TDTB Grit Habits v1"
#: The only payload schema this module accepts.
SCHEMA = "tdtb.grit.v1"
#: Same-directory state artifacts under ``app_config.state_dir()``.
CACHE_FILENAME = "grit_habits_cache.json"
CACHE_LOCK_FILENAME = "grit_habits_cache.lock"
#: A wedged Shortcut must never hang a route; ``shortcuts run`` is synchronous.
DEFAULT_TIMEOUT_S = 20

#: Bounds owned by the artifact's habit contract (imported, never copied).
MAX_HABITS = artifact_source.HABIT_ITEMS_MAX
MAX_RECORDS = artifact_source.HABIT_ITEMS_MAX
MAX_TEXT = artifact_source.HABIT_TEXT_MAX

# ---------------------------------------------------------------------------
# Classifier vocabulary (TODO(U0): replace with the real Grit recordKind values)
# ---------------------------------------------------------------------------
# Best-guess placeholders. A record whose normalized kind is in
# COMPLETION_KINDS makes the habit done; FAILED_KINDS makes it failed (unless
# it is also done); PROGRESS_KINDS makes it partial.
COMPLETION_KINDS: frozenset[str] = frozenset({"completed", "complete", "done"})
FAILED_KINDS: frozenset[str] = frozenset({"failed", "failure", "missed", "skipped"})
PROGRESS_KINDS: frozenset[str] = frozenset({"progress", "partial", "in progress"})

STATUS_DONE = "done"
STATUS_FAILED = "failed"
STATUS_PARTIAL = "partial"
STATUS_NONE = "none"
STATUSES: frozenset[str] = frozenset({STATUS_DONE, STATUS_FAILED, STATUS_PARTIAL, STATUS_NONE})

# ---------------------------------------------------------------------------
# Typed failure reasons
# ---------------------------------------------------------------------------

REASON_OK = "ok"
REASON_TIMEOUT = "timeout"
REASON_EXIT_NONZERO = "exit-nonzero"
REASON_RUNNER_ERROR = "runner-error"
REASON_MISSING_OUTPUT = "missing-output"
REASON_UNPARSEABLE = "unparseable"
REASON_SCHEMA = "schema-mismatch"
REASON_DUPLICATE = "duplicate-name"
REASON_NO_CACHE = "no-cache"
REASON_STALE_DAY = "stale-day"

# ---------------------------------------------------------------------------
# Content-free operator warnings (no habit names, no shortcut output)
# ---------------------------------------------------------------------------

GRIT_READ_STALE = "Grit habits read failed — showing today's last Grit read"
GRIT_READ_FAILED = (
    "Grit habits unavailable — counting every configured habit as not done"
)
GRIT_UNCONFIGURED = "Grit habits without minutes — using the default habit minutes"

#: What an unconfigured Grit habit costs: the habit exists in Grit, the owner
#: has saved no duration for it. Deliberately a module constant rather than the
#: legacy ``habits.fallback_minutes_per_habit`` config (4): that knob tunes the
#: Todoist path's per-habit fallback, and the Grit path owns its own default
#: instead of silently inheriting a number chosen for another source.
GRIT_UNCONFIGURED_MINUTES = 15

#: A concurrent caller that arrives within this window of a completed refresh
#: reuses that result instead of running the Shortcut a second time.
SINGLE_FLIGHT_WINDOW_S = 5.0

#: How old a stored cache may be before a plan read kicks a background refresh.
#: A plan read never runs the Shortcut itself: past this age it serves the
#: cached read it has and asks for a detach-refresh.
CACHE_MAX_AGE_S = 600.0


class GritFormatError(Exception):
    """The Shortcut payload violates the contract. ``reason`` is a constant."""

    def __init__(self, reason: str = REASON_SCHEMA) -> None:
        super().__init__(reason)
        self.reason = reason


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class HabitRecord:
    """One Grit history record, reduced to the two fields TDTB may keep.

    ``kind`` is stored normalized (trimmed, casefolded) so the classifier
    constants match whatever casing Grit uses. ``amount`` is the numeric amount
    when Grit supplied one.
    """

    kind: str
    amount: float | None = None


@dataclass(frozen=True)
class HabitReading:
    """One habit's progress observation for the read's logical day.

    ``status`` is derived on access (:func:`classify_habit`) rather than
    stored, so a cached read is re-classified under the current constants.
    """

    name: str
    progress: float | None = None
    records: tuple[HabitRecord, ...] = ()

    @property
    def status(self) -> str:
        return classify_habit(self.progress, self.records)

    @property
    def remaining(self) -> bool:
        """True when this habit still consumes a capacity slot."""
        return counts_toward_remaining(self.status)


@dataclass(frozen=True)
class RunResult:
    """Outcome of one ``shortcuts run`` invocation (never an exception)."""

    ok: bool
    reason: str = REASON_OK
    exit_code: int | None = None


@dataclass(frozen=True)
class GritRead:
    """One read attempt: the parsed habits on success, a reason otherwise."""

    ok: bool
    reason: str = REASON_OK
    habits: tuple[HabitReading, ...] = ()
    logical_day: str = ""
    generated_at: str = ""


@dataclass(frozen=True)
class GritCache:
    """A previously stored good read plus the logical day it belongs to."""

    logical_day: str
    generated_at: str = ""
    habits: tuple[HabitReading, ...] = ()


class TodayRead(NamedTuple):
    """``read_today`` result: usable habits, staleness, and a reason constant.

    Unpacks as ``(habits, stale, reason)`` for callers that prefer a tuple.
    """

    habits: tuple[HabitReading, ...] | None
    stale: bool
    reason: str


# ---------------------------------------------------------------------------
# Classifier
# ---------------------------------------------------------------------------

def classify_habit(
    progress: float | None, records: tuple[HabitRecord, ...] | list[HabitRecord]
) -> str:
    """Map one habit's progress and records onto a status constant.

    Precedence is ``done`` > ``failed`` > ``partial`` > ``none``:

    - ``done``: any record kind in :data:`COMPLETION_KINDS`, or
      ``progress >= 1``;
    - ``failed``: any record kind in :data:`FAILED_KINDS` (and not done);
    - ``partial``: ``0 < progress < 1``, or any kind in
      :data:`PROGRESS_KINDS` (and not done/failed);
    - ``none``: everything else, including a habit with nothing recorded.

    This is the ONE place the record-kind vocabulary is interpreted.
    """
    kinds = {str(record.kind) for record in records}
    if kinds & COMPLETION_KINDS:
        return STATUS_DONE
    if progress is not None and progress >= 1:
        return STATUS_DONE
    if kinds & FAILED_KINDS:
        return STATUS_FAILED
    if progress is not None and 0 < progress < 1:
        return STATUS_PARTIAL
    if kinds & PROGRESS_KINDS:
        return STATUS_PARTIAL
    return STATUS_NONE


def counts_toward_remaining(status: str) -> bool:
    """True when a status still consumes a capacity slot (binary treatment).

    A ``partial`` habit counts as fully remaining, and an unrecorded habit
    counts as remaining; only ``done`` and ``failed`` stop counting.
    """
    return status not in (STATUS_DONE, STATUS_FAILED)


# ---------------------------------------------------------------------------
# Runner (the only place a process is spawned)
# ---------------------------------------------------------------------------

def run_shortcut(
    name: str, output_path: str | Path, timeout_s: float = DEFAULT_TIMEOUT_S
) -> RunResult:
    """Run the Shortcut and write its JSON to ``output_path``; never raises.

    The shortcut's stdout is captured and discarded so nothing it prints (a
    habit name, a note) can reach a console or a log.
    """
    argv = [
        "shortcuts",
        "run",
        str(name),
        "--output-path",
        str(output_path),
        "--output-type",
        "public.json",
    ]
    try:
        completed = subprocess.run(
            argv,
            timeout=timeout_s,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return RunResult(ok=False, reason=REASON_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return RunResult(ok=False, reason=REASON_RUNNER_ERROR)
    code = getattr(completed, "returncode", None)
    if code != 0:
        return RunResult(ok=False, reason=REASON_EXIT_NONZERO, exit_code=code)
    return RunResult(ok=True, reason=REASON_OK, exit_code=0)


# ---------------------------------------------------------------------------
# Strict decoding
# ---------------------------------------------------------------------------

def _number(value: Any) -> float | None:
    """A finite int/float, or ``None``; anything else raises ``ValueError``."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("not a number")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("not finite")
    return number


def _record_of(entry: Any) -> HabitRecord:
    """One history record, keeping only ``kind`` and ``amount``."""
    if not isinstance(entry, dict):
        raise GritFormatError(REASON_SCHEMA)
    kind = entry.get("kind")
    if not isinstance(kind, str):
        raise GritFormatError(REASON_SCHEMA)
    kind = kind.strip().casefold()
    if not kind or len(kind) > MAX_TEXT:
        raise GritFormatError(REASON_SCHEMA)
    try:
        amount = _number(entry.get("amount"))
    except ValueError as exc:
        raise GritFormatError(REASON_SCHEMA) from exc
    return HabitRecord(kind=kind, amount=amount)


def _habit_of(entry: Any) -> HabitReading:
    """One habit row, validated against the bounded contract."""
    if not isinstance(entry, dict):
        raise GritFormatError(REASON_SCHEMA)
    name = entry.get("name")
    if not isinstance(name, str):
        raise GritFormatError(REASON_SCHEMA)
    name = name.strip()
    if not name or len(name) > MAX_TEXT:
        raise GritFormatError(REASON_SCHEMA)
    try:
        progress = _number(entry.get("progress"))
    except ValueError as exc:
        raise GritFormatError(REASON_SCHEMA) from exc
    raw_records = entry.get("records")
    if not isinstance(raw_records, list) or len(raw_records) > MAX_RECORDS:
        raise GritFormatError(REASON_SCHEMA)
    records = tuple(_record_of(row) for row in raw_records)
    return HabitReading(name=name, progress=progress, records=records)


def parse_payload(document: Any) -> tuple[HabitReading, ...]:
    """Strictly decode a ``tdtb.grit.v1`` payload; raises :class:`GritFormatError`.

    Rejects a wrong or missing ``schema``, a missing ``generated_at``, a
    non-list or oversized ``habits``, a blank or oversized name, an invalid
    progress/record shape, and duplicate names (casefolded). Unknown extra keys
    are ignored and never stored.
    """
    if not isinstance(document, dict):
        raise GritFormatError(REASON_SCHEMA)
    if document.get("schema") != SCHEMA:
        raise GritFormatError(REASON_SCHEMA)
    generated_at = document.get("generated_at")
    if (
        not isinstance(generated_at, str)
        or not generated_at.strip()
        or len(generated_at) > MAX_TEXT
    ):
        raise GritFormatError(REASON_SCHEMA)
    raw = document.get("habits")
    if not isinstance(raw, list) or len(raw) > MAX_HABITS:
        raise GritFormatError(REASON_SCHEMA)
    habits: list[HabitReading] = []
    seen: set[str] = set()
    for entry in raw:
        habit = _habit_of(entry)
        key = habit.name.casefold()
        if key in seen:
            raise GritFormatError(REASON_DUPLICATE)
        seen.add(key)
        habits.append(habit)
    return tuple(habits)


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

def cache_path() -> Path:
    """The active cache path: ``<app home>/state/grit_habits_cache.json``."""
    return app_config.state_dir() / CACHE_FILENAME


def lock_path() -> Path:
    """The active lock-file path, beside the cache in the app home.

    A 0-byte runtime artifact created on demand; never migrated."""
    return app_config.state_dir() / CACHE_LOCK_FILENAME


# ---------------------------------------------------------------------------
# Live read
# ---------------------------------------------------------------------------

Runner = Callable[..., RunResult]

#: Process-local single-flight record: ``(wall_ts, logical_day, read)``.
_RECENT_SUCCESS: tuple[float, str, GritRead] | None = None

#: Process-local record of the last FAILED refresh: ``(wall_ts, logical_day)``.
#: It is how a plan read tells "today's read is simply old" from "today's
#: refresh already failed after this cache was stored" (``GRIT_READ_STALE``).
_LAST_FAILURE: tuple[float, str] | None = None


def _now() -> datetime.datetime:
    return datetime.datetime.now()


def _logical_day(now: datetime.datetime) -> str:
    """The app's own logical day (the gather contract: midnight-2am is yesterday)."""
    return gather.effective_date(now).isoformat()


def read_habits(
    runner: Runner | None = None,
    *,
    now: datetime.datetime | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> GritRead:
    """Run the Shortcut once and return a typed result; never raises.

    ``runner`` is injectable so tests never spawn a process; it must accept
    ``(name, output_path, timeout_s=...)`` and return a :class:`RunResult`.
    """
    run = run_shortcut if runner is None else runner
    moment = _now() if now is None else now
    logical_day = _logical_day(moment)

    def _failed(reason: str) -> GritRead:
        return GritRead(ok=False, reason=reason, logical_day=logical_day)

    try:
        with tempfile.TemporaryDirectory(prefix="tdtb-grit-habits-") as tmp:
            output = Path(tmp) / "habits.json"
            try:
                result = run(SHORTCUT_NAME, output, timeout_s=timeout_s)
            except Exception:  # noqa: BLE001 — an injected runner must not raise out
                return _failed(REASON_RUNNER_ERROR)
            if not isinstance(result, RunResult):
                return _failed(REASON_RUNNER_ERROR)
            if not result.ok:
                return _failed(result.reason or REASON_RUNNER_ERROR)
            try:
                text = output.read_text(encoding="utf-8")
            except (OSError, ValueError):
                return _failed(REASON_MISSING_OUTPUT)
            try:
                document = json.loads(text)
            except ValueError:
                return _failed(REASON_UNPARSEABLE)
            try:
                habits = parse_payload(document)
            except GritFormatError as exc:
                return _failed(exc.reason)
            generated_at = document["generated_at"].strip()
    except OSError:  # pragma: no cover — tempdir creation failure
        return _failed(REASON_RUNNER_ERROR)
    return GritRead(
        ok=True,
        reason=REASON_OK,
        habits=habits,
        logical_day=logical_day,
        generated_at=generated_at,
    )


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

def _encode(habits: tuple[HabitReading, ...]) -> list[dict[str, Any]]:
    """The stored shape: the read's own payload, minus anything not retained."""
    return [
        {
            "name": habit.name,
            "progress": habit.progress,
            "records": [
                {"kind": record.kind, "amount": record.amount}
                for record in habit.records
            ],
        }
        for habit in habits
    ]


def _decode_cache(document: Any) -> GritCache:
    """Decode a stored cache document; raises :class:`GritFormatError`."""
    if not isinstance(document, dict):
        raise GritFormatError(REASON_SCHEMA)
    logical_day = document.get("logical_day")
    if (
        not isinstance(logical_day, str)
        or not logical_day.strip()
        or len(logical_day) > MAX_TEXT
    ):
        raise GritFormatError(REASON_SCHEMA)
    habits = parse_payload(document)
    return GritCache(
        logical_day=logical_day.strip(),
        generated_at=str(document["generated_at"]).strip(),
        habits=habits,
    )


def load_cache() -> GritCache | None:
    """The last good read, or ``None`` when there is no usable cache.

    Fail-closed: a missing, unreadable, or malformed cache is "no cache" — it
    never raises and it is never repaired here.
    """
    try:
        text = cache_path().read_text(encoding="utf-8")
    except (OSError, ValueError):
        return None
    try:
        document = json.loads(text)
    except ValueError:
        return None
    try:
        return _decode_cache(document)
    except GritFormatError:
        return None


def _store_cache_locked(result: GritRead, moment: datetime.datetime) -> bool:
    """Write the cache; the caller already holds the store + file locks."""
    payload = {
        "schema": SCHEMA,
        "generated_at": result.generated_at,
        "logical_day": _logical_day(moment),
        "habits": _encode(result.habits),
    }
    try:
        capacities_cache_io.atomic_write_json(cache_path(), payload)
    except (OSError, ValueError):
        return False
    return True


def store_cache(
    result: GritRead, *, now: datetime.datetime | None = None
) -> bool:
    """Persist one successful read; a failed read returns ``False`` untouched.

    The stored logical day is computed from ``now`` (``refresh`` passes the
    same moment it read with, so the two agree).
    """
    if not result.ok:
        return False
    moment = _now() if now is None else now
    with capacities_cache_io.store_lock(cache_path()):
        handle = capacities_cache_io.acquire_path_lock(lock_path())
        try:
            return _store_cache_locked(result, moment)
        finally:
            capacities_cache_io.release_lock_file(handle)


def read_today(now: datetime.datetime | None = None) -> TodayRead:
    """Today's cached habits, if the cache belongs to today's logical day.

    - same logical day: ``(habits, False, "ok")``;
    - a different day: ``(None, True, "stale-day")`` — never yesterday's rows;
    - no usable cache: ``(None, False, "no-cache")``.
    """
    moment = _now() if now is None else now
    today = _logical_day(moment)
    cache = load_cache()
    if cache is None:
        return TodayRead(None, False, REASON_NO_CACHE)
    if cache.logical_day != today:
        return TodayRead(None, True, REASON_STALE_DAY)
    return TodayRead(cache.habits, False, REASON_OK)


def cache_age_seconds(now: datetime.datetime | None = None) -> float | None:
    """Seconds since the stored cache was written, or ``None`` when absent.

    The cache file's own ``mtime`` is the local write time of the read, which
    is the only clock a plan read may use: ``generated_at`` is the Shortcut's
    own timestamp and is not guaranteed to describe this machine's now.
    """
    moment = _now() if now is None else now
    try:
        mtime = cache_path().stat().st_mtime
    except OSError:
        return None
    return max(0.0, moment.timestamp() - mtime)


def needs_refresh(now: datetime.datetime | None = None) -> bool:
    """Whether a plan read should ask for ONE detached refresh.

    True when no cache is usable for the current logical day (missing, or an
    earlier day's read) or when the stored cache is older than
    :data:`CACHE_MAX_AGE_S`. The caller serves the cache regardless and must
    never block on the refresh itself.
    """
    moment = _now() if now is None else now
    if read_today(moment).habits is None:
        return True
    age = cache_age_seconds(moment)
    return age is None or age > CACHE_MAX_AGE_S


def failed_since_cache(now: datetime.datetime | None = None) -> bool:
    """True when today's refresh attempt failed AFTER the cache was stored.

    That is the only case where serving the cached read needs the
    ``GRIT_READ_STALE`` label: an older failure the cache has since outlived is
    history, and a failure with no cache at all is the caller's harder step.
    """
    moment = _now() if now is None else now
    failure = _LAST_FAILURE
    if failure is None or failure[1] != _logical_day(moment):
        return False
    age = cache_age_seconds(moment)
    if age is None:
        return False
    return failure[0] > (moment.timestamp() - age)


# ---------------------------------------------------------------------------
# Single-flight refresh
# ---------------------------------------------------------------------------

def refresh(
    runner: Runner | None = None,
    *,
    now: datetime.datetime | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> GritRead:
    """Refresh under a single-flight lock and update the cache on success only.

    Concurrent callers (threads or processes) serialize on the lock file, and a
    caller that arrives within :data:`SINGLE_FLIGHT_WINDOW_S` of a completed
    same-day refresh reuses that result rather than running the Shortcut again.
    A failed read never touches the stored cache.
    """
    global _RECENT_SUCCESS, _LAST_FAILURE
    moment = _now() if now is None else now
    today = _logical_day(moment)
    with capacities_cache_io.store_lock(cache_path()):
        handle = capacities_cache_io.acquire_path_lock(lock_path())
        try:
            recent = _RECENT_SUCCESS
            if (
                recent is not None
                and recent[1] == today
                and moment.timestamp() - recent[0] <= SINGLE_FLIGHT_WINDOW_S
            ):
                return recent[2]
            result = read_habits(runner, now=moment, timeout_s=timeout_s)
            if result.ok:
                _store_cache_locked(result, moment)
                _RECENT_SUCCESS = (moment.timestamp(), today, result)
            else:
                _LAST_FAILURE = (moment.timestamp(), today)
            return result
        finally:
            capacities_cache_io.release_lock_file(handle)
