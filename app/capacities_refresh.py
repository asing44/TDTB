"""capacities_refresh.py — paced, single-flight direct Capacities refresh.

This module owns the acquisition half of the direct Capacities refresh (U2):
it enumerates every configured type completely, reads only the content that is
new or uncached (or every scoped object for a Rescan), paces every provider
read, and installs exactly one complete generation through the U1 store
(``capacities_refresh_state``). It never rewrites the legacy live reader; it
reuses the adapter's transport contract, pagination, object normalization, and
projection through public seams.

Contract (KTD1–KTD3):

* **One machine-local single-flight job.** A process-local lock plus a POSIX
  advisory lock file serialize Refresh/Rescan across threads and processes.
  Status queries make no provider call and never spawn work.
* **Endpoint-aware pacing.** GET/content reads default to the documented 30
  requests per 60 seconds; listings carry their own policy and per-page cost.
  ``Retry-After``/rate-limit-reset headers are honored when present and a
  conservative clock pacing is used when they are absent. Retries are bounded,
  errors stay truthful, and cancellation interrupts backoff.
* **Complete scope or nothing.** Any incomplete listing, failed required read,
  or changed configuration revision prevents installation. The previous
  complete generation and every successful read are preserved.
* **No secret leakage.** Warnings are bounded fixed strings; exception text,
  tokens, and filesystem paths never reach a warning or a job document.

Machine-local only: the caller owns the state root (``app_config.state_dir()``)
so raw object content never enters a synced vault.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from collections import deque
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping

try:  # POSIX advisory locks
    import fcntl as _fcntl
except ImportError:  # pragma: no cover — non-POSIX fallback
    _fcntl = None

import capacities_adapter
import capacities_cache_io
import capacities_refresh_state as refresh_state
import tag_exclusions

#: Endpoint identities shared with the adapter's transport observer.
ENDPOINT_STRUCTURES = "structures"
ENDPOINT_LISTING = "listing"
ENDPOINT_CONTENT = "content"

JOB_DIRNAME = "jobs"
JOB_LOCK_FILENAME = ".refresh-job.lock"
JOB_SCHEMA_VERSION = 1

#: The published generation is keyed by one logical scope: the complete
#: configured Capacities scope. A scoped Rescan updates a member type but
#: still installs (or refuses) the one whole-scope generation.
DEFAULT_SCOPE_KEY = "all"

TERMINAL_PHASES = frozenset(
    {"complete", "failed", "cancelled", "interrupted"}
)

FAILURE_WARNING = (
    "Capacities could not complete a full refresh; the previous complete "
    "result is preserved and no partial result was published."
)
NO_RESULT_WARNING = (
    "No complete Capacities result is available; Capacities rows are omitted "
    "until a refresh completes."
)
STALE_WARNING = (
    "Capacities configuration changed during the refresh; the stale run was "
    "discarded and the previous complete result is preserved."
)
CANCEL_WARNING = (
    "Capacities refresh was cancelled; the previous complete result and every "
    "successful read were preserved."
)
INTERRUPT_WARNING = (
    "Capacities refresh was interrupted by a restart; the previous complete "
    "result is preserved and successful reads remain available."
)
TAG_EXCLUSION_WARNING = (
    "Capacities planning was blocked by the active tag-exclusion policy: tag "
    "metadata could not be evaluated by stable identity. Repair the source "
    "tag payload or clear the exclusions, then retry; the previous complete "
    "result is preserved."
)


class RefreshError(Exception):
    """Base class for every coordinator failure."""


class RefreshCancelled(RefreshError):
    """The job was cancelled; no publication happened."""


class RefreshBusyError(RefreshError):
    """Another Refresh/Rescan job is already running (single-flight)."""


class RefreshUnavailableError(RefreshError):
    """No usable configured source/credential/configuration exists."""


class RefreshIncomplete(RefreshError):
    """The scope could not be proven complete; nothing is published."""

    def __init__(self, warning: str) -> None:
        super().__init__(warning)
        self.warning = warning


class RefreshStale(RefreshError):
    """The configuration revision moved past the captured job revision."""

    def __init__(self, warning: str = STALE_WARNING) -> None:
        super().__init__(warning)
        self.warning = warning


# ---------------------------------------------------------------------------
# Pacing
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class EndpointPolicy:
    """A conservative rolling-window ceiling for one endpoint class."""

    limit: int
    window: float
    min_interval: float = 0.0


#: The live API allows 30 requests per minute and its listing carries no typed
#: properties, so every property decision costs one content read. Listings are
#: a separate endpoint with their own (equally conservative) ceiling.
DEFAULT_POLICIES: dict[str, EndpointPolicy] = {
    ENDPOINT_CONTENT: EndpointPolicy(limit=30, window=60.0),
    ENDPOINT_LISTING: EndpointPolicy(limit=30, window=60.0),
    ENDPOINT_STRUCTURES: EndpointPolicy(limit=30, window=60.0),
}


def _default_sleeper(seconds: float, cancel_event: threading.Event | None) -> None:
    """Block for ``seconds`` on a real clock, interruptible by cancellation."""
    deadline = time.monotonic() + max(0.0, seconds)
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        if cancel_event is not None and cancel_event.is_set():
            raise RefreshCancelled("cancelled during pacing backoff")
        time.sleep(min(0.05, remaining))


def _coerce_seconds(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    if number < 0:
        return None
    return number


class ProviderPacer:
    """Endpoint-aware, clock-injectable request pacing and 429 backoff."""

    def __init__(
        self,
        *,
        policies: Mapping[str, EndpointPolicy] | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float, threading.Event | None], None] | None = None,
        cancel_event: threading.Event | None = None,
        max_retries: int = 3,
        base_backoff: float = 1.0,
        max_backoff: float = 30.0,
    ) -> None:
        self.policies = dict(policies or DEFAULT_POLICIES)
        self._clock = clock
        self._sleeper = sleeper or _default_sleeper
        self._cancel_event = cancel_event
        self.max_retries = max(0, int(max_retries))
        self.base_backoff = max(0.0, float(base_backoff))
        self.max_backoff = max(0.0, float(max_backoff))
        self._times: dict[str, deque[float]] = {}
        self._cooldown_until: dict[str, float] = {}
        self._retry_after: dict[str, float] = {}
        self.sleep_calls: list[float] = []

    # -- policy / bookkeeping ------------------------------------------

    def _policy(self, endpoint: str) -> EndpointPolicy:
        return self.policies.get(endpoint) or EndpointPolicy(limit=30, window=60.0)

    def _times_for(self, endpoint: str) -> deque[float]:
        return self._times.setdefault(endpoint, deque())

    # -- cancellation ---------------------------------------------------

    def _check_cancel(self) -> None:
        if self._cancel_event is not None and self._cancel_event.is_set():
            raise RefreshCancelled("cancelled")

    def sleep(self, seconds: float) -> None:
        if seconds <= 0:
            self._check_cancel()
            return
        self._check_cancel()
        self.sleep_calls.append(float(seconds))
        self._sleeper(float(seconds), self._cancel_event)
        self._check_cancel()

    # -- request admission ---------------------------------------------

    def before_request(self, endpoint: str) -> None:
        """Block until one request for ``endpoint`` is within policy."""
        self._check_cancel()
        policy = self._policy(endpoint)
        times = self._times_for(endpoint)
        now = self._clock()
        while times and now - times[0] >= policy.window:
            times.popleft()
        if policy.limit > 0 and len(times) >= policy.limit:
            wait = policy.window - (now - times[0])
            if wait > 0:
                self.sleep(wait)
                now = self._clock()
                while times and now - times[0] >= policy.window:
                    times.popleft()
        if policy.min_interval > 0 and times:
            gap = now - times[-1]
            if gap < policy.min_interval:
                self.sleep(policy.min_interval - gap)
                now = self._clock()
        cooldown = self._cooldown_until.get(endpoint, 0.0)
        if cooldown > now:
            self.sleep(cooldown - now)
            now = self._clock()
        times.append(now)

    # -- response observation ------------------------------------------

    def observe(self, endpoint: str, headers: Any) -> None:
        """Record optional rate-limit headers conservatively."""
        if not isinstance(headers, Mapping):
            return
        lowered = {str(key).lower(): value for key, value in headers.items()}
        retry = _coerce_seconds(lowered.get("retry-after"))
        remaining = lowered.get("x-ratelimit-remaining")
        if remaining is None:
            remaining = lowered.get("ratelimit-remaining")
        reset = _coerce_seconds(
            lowered.get("x-ratelimit-reset")
            if lowered.get("x-ratelimit-reset") is not None
            else lowered.get("ratelimit-reset")
        )
        if retry is not None:
            self.note_rate_limited(endpoint, retry)
        elif str(remaining).strip() == "0" and reset is not None:
            self._cooldown_until[endpoint] = max(
                self._cooldown_until.get(endpoint, 0.0), self._clock() + reset
            )

    def note_rate_limited(self, endpoint: str, retry_after: float | None) -> None:
        delay = retry_after
        if delay is None:
            delay = self.base_backoff
        delay = min(max(0.0, delay), self.max_backoff) if self.max_backoff else delay
        self._retry_after[endpoint] = delay
        self._cooldown_until[endpoint] = max(
            self._cooldown_until.get(endpoint, 0.0), self._clock() + delay
        )

    def retry_delay(self, endpoint: str, attempt: int) -> float:
        retry = self._retry_after.get(endpoint)
        if retry is not None:
            return retry
        return min(self.max_backoff, self.base_backoff * (2 ** max(0, attempt)))

    def note_success(self, endpoint: str) -> None:
        self._retry_after.pop(endpoint, None)

    # -- call loop ------------------------------------------------------

    def call(self, endpoint: str, fn: Callable[[], Any]) -> Any:
        attempt = 0
        while True:
            self.before_request(endpoint)
            try:
                result = fn()
            except RefreshCancelled:
                raise
            except capacities_adapter.CapacitiesRateLimited as exc:
                self.note_rate_limited(endpoint, getattr(exc, "retry_after", None))
                if attempt >= self.max_retries:
                    raise
                self.sleep(self.retry_delay(endpoint, attempt))
                attempt += 1
                continue
            self.note_success(endpoint)
            return result


class PacedProvider:
    """The provider seam the adapter/coordinator see: paced and retried."""

    def __init__(self, raw: Any, pacer: ProviderPacer) -> None:
        self.raw = raw
        self.pacer = pacer
        setter = getattr(raw, "set_rate_observer", None)
        if callable(setter):
            setter(self.observe_headers)

    def observe_headers(self, endpoint: str, headers: Any) -> None:
        self.pacer.observe(endpoint, headers)

    def fetch_structures(self):
        return self.pacer.call(ENDPOINT_STRUCTURES, self.raw.fetch_structures)

    def list_objects(self, structure_id: str, cursor: str | None = None):
        return self.pacer.call(
            ENDPOINT_LISTING, lambda: self.raw.list_objects(structure_id, cursor)
        )

    def get_object(self, object_id: str):
        return self.pacer.call(
            ENDPOINT_CONTENT, lambda: self.raw.get_object(object_id)
        )

    def patch_object(self, object_id: str, properties: dict[str, Any]):
        return self.pacer.call(
            ENDPOINT_CONTENT,
            lambda: self.raw.patch_object(object_id, properties),
        )

    def close(self) -> None:
        close = getattr(self.raw, "close", None)
        if callable(close):
            close()


# ---------------------------------------------------------------------------
# Job document
# ---------------------------------------------------------------------------

def _digest(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _try_acquire_exclusive(path: Path) -> Any | None:
    """Non-blocking exclusive advisory lock; ``None`` when already held."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "a+", encoding="utf-8")
    if _fcntl is None:  # pragma: no cover — non-POSIX fallback
        return handle
    try:
        _fcntl.flock(handle.fileno(), _fcntl.LOCK_EX | _fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle


def _release_exclusive(handle: Any) -> None:
    if handle is None:
        return
    try:
        if _fcntl is not None:
            _fcntl.flock(handle.fileno(), _fcntl.LOCK_UN)
    except OSError:
        pass
    try:
        handle.close()
    except OSError:
        pass


class RefreshCoordinator:
    """The persisted single-flight Refresh/Rescan job coordinator."""

    def __init__(
        self,
        *,
        root: str | Path,
        store: refresh_state.RefreshStateStore,
        provider: Any,
        space_id: str,
        mappings: tuple[capacities_adapter.StructureMapping, ...],
        revision_supplier: Callable[[], int | None],
        scope_key: str = DEFAULT_SCOPE_KEY,
        assignment_settings: Any = None,
        rules: Any = None,
        exclusion_policy: Any = None,
        max_pages: int = 20,
        logical_day: date | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float, threading.Event | None], None] | None = None,
        max_retries: int = 3,
        policies: Mapping[str, EndpointPolicy] | None = None,
        namespace: str | None = None,
        config_guard: Callable[[], Any] | None = None,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        if not isinstance(space_id, str) or not space_id.strip():
            raise ValueError("space_id must be a nonblank string")
        if not mappings:
            raise ValueError("at least one Capacities mapping is required")
        if max_pages < 1:
            raise ValueError("max_pages must be positive")
        self.store = store
        self.root = Path(root)
        self.scope_key = scope_key
        self.max_pages = int(max_pages)
        self.space_id = space_id.strip()
        self.mappings = tuple(mappings)
        self.revision_supplier = revision_supplier
        self.logical_day = logical_day
        self.config_guard = config_guard
        self._wall_clock = wall_clock
        self._namespace = namespace or _digest(self.space_id)
        self.cancel_event = threading.Event()
        self.pacer = ProviderPacer(
            policies=policies,
            clock=clock,
            sleeper=sleeper,
            cancel_event=self.cancel_event,
            max_retries=max_retries,
        )
        self.provider = PacedProvider(provider, self.pacer)
        self.adapter = capacities_adapter.CapacitiesAdapter(
            self.provider,
            capacities_adapter.CapacitiesConfig(
                space_id=self.space_id,
                mappings=self.mappings,
                max_pages=self.max_pages,
                assignment_settings=(
                    assignment_settings
                    if assignment_settings is not None
                    else capacities_adapter.AssignmentSettings()
                ),
                rules=rules,
                exclusion_policy=exclusion_policy,
                content_cache=None,
            ),
        )
        self._job_dir = self.root / JOB_DIRNAME
        self.job_lock_path = self._job_dir / JOB_LOCK_FILENAME
        # The single-flight lock file and the durable job document live under
        # ``state/jobs/``. Create that directory at construction so a caller (or
        # a foreign process trying to hold the lock) can open the lock before
        # the first run starts.
        self._job_dir.mkdir(parents=True, exist_ok=True)
        self._process_lock = capacities_cache_io.store_lock(str(self.job_lock_path))
        self._file_lock: Any = None
        self._thread: threading.Thread | None = None
        self._job: dict[str, Any] | None = None

    # -- paths / persistence -------------------------------------------

    def _job_path(self) -> Path:
        return self._job_dir / f"{self._namespace}.json"

    def _persist_job(self) -> None:
        if self._job is None:
            return
        capacities_cache_io.atomic_write_json(
            self._job_path(),
            {
                "version": JOB_SCHEMA_VERSION,
                "namespace": self._namespace,
                "job": self._job,
            },
        )

    def _load_job(self) -> dict[str, Any] | None:
        try:
            text = self._job_path().read_text(encoding="utf-8")
        except (FileNotFoundError, OSError, UnicodeDecodeError):
            return None
        try:
            document = json.loads(text)
        except ValueError:
            return None
        if not isinstance(document, dict) or document.get("version") != JOB_SCHEMA_VERSION:
            return None
        job = document.get("job")
        if not isinstance(job, dict):
            return None
        return job

    # -- liveness / reconciliation -------------------------------------

    def _is_live(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def is_running(self) -> bool:
        """Whether a job thread is currently live.

        The route seam may replace a cached coordinator only while this is
        false: a live job keeps the instance that owns its thread, locks, and
        in-memory job, so cancel and status reach it and no second instance is
        built around the same lock files.
        """
        return self._is_live()

    def reconcile(self) -> None:
        """Report a run whose process died as interrupted; never resume it."""
        if self._is_live():
            return
        job = self._load_job()
        try:
            marker = self.store.read_unfinished_run(self.scope_key)
        except refresh_state.RefreshStateError:
            marker = None
        running = marker is not None or (
            job is not None and job.get("phase") not in TERMINAL_PHASES
        )
        if not running:
            return
        now = self._wall_clock()
        job = {
            **(job or {}),
            "phase": "interrupted",
            "outcome": "interrupted",
            "updated_at": now,
            "finished_at": now,
        }
        self._job = job
        self._persist_job()
        try:
            self.store.note_run_finished(self.scope_key)
        except refresh_state.RefreshStateError:
            pass

    # -- status ---------------------------------------------------------

    def status(self) -> dict[str, Any]:
        self.reconcile()
        job = self._job or self._load_job()
        snapshot = self.store.load_snapshot(self.scope_key)
        return {
            "configured": True,
            "phase": job.get("phase") if job else None,
            "outcome": job.get("outcome") if job else None,
            "mode": job.get("mode") if job else None,
            "scope": job.get("scope") if job else None,
            "job": job,
            "progress": job.get("progress") if job else {},
            "warnings": list(job.get("warnings", ())) if job else [],
            "coverage": _coverage(snapshot, job),
            "snapshot": _snapshot_summary(snapshot),
        }

    # -- start / cancel -------------------------------------------------

    def start(self, mode: str = "refresh", scope: str = DEFAULT_SCOPE_KEY) -> dict[str, Any]:
        if mode not in ("refresh", "rescan"):
            raise ValueError("mode must be 'refresh' or 'rescan'")
        if not isinstance(scope, str) or not scope.strip():
            raise ValueError("scope must be a nonblank string")
        scope = scope.strip()
        self.reconcile()
        if self._is_live():
            raise RefreshBusyError("a Capacities refresh job is already running")
        if not self._process_lock.acquire(blocking=False):
            raise RefreshBusyError("a Capacities refresh job is already running")
        try:
            self._file_lock = _try_acquire_exclusive(self.job_lock_path)
            if self._file_lock is None:
                raise RefreshBusyError(
                    "a Capacities refresh job is already running in another process"
                )
            revision = self.revision_supplier()
            if type(revision) is not int or revision < 0:
                raise RefreshUnavailableError("no usable Capacities configuration")
            prior = self.store.load_snapshot(self.scope_key)
            now = self._wall_clock()
            self.cancel_event.clear()
            self._job = {
                "job_id": _digest(self._namespace, str(now), mode, scope)[:16],
                "mode": mode,
                "scope": scope,
                "phase": "listing",
                "outcome": None,
                "revision": int(revision),
                "generation": prior.generation if prior is not None else 0,
                "started_at": now,
                "updated_at": now,
                "finished_at": None,
                "progress": {"listed": 0, "read": 0, "types": {}},
                "warnings": [],
            }
            self._persist_job()
            self.store.note_run_started(
                self.scope_key, int(revision), started_at=now
            )
            self._thread = threading.Thread(
                target=self._run, name="capacities-refresh", daemon=True
            )
            self._thread.start()
        except BaseException:
            self._release_locks()
            raise
        return self.status()

    def cancel(self) -> dict[str, Any]:
        if self._is_live():
            self.cancel_event.set()
            # Reflect the documented ``-> cancelled: cancel`` transition
            # promptly. The worker unwinds on the event at its next checkpoint,
            # so a status read between the signal and that unwind must not keep
            # reporting the pre-cancel phase. A job that already reached a
            # terminal phase is left alone: cancel never undoes a completed
            # generation.
            job = self._job or self._load_job()
            if job is None or job.get("phase") not in TERMINAL_PHASES:
                self._finish("cancelled", "cancelled", [CANCEL_WARNING])
        return self.status()

    def wait(self, timeout: float | None = None) -> dict[str, Any]:
        thread = self._thread
        if thread is not None:
            thread.join(timeout)
        return self.status()

    def close(self) -> None:
        try:
            self.adapter.close()
        except Exception:  # noqa: BLE001 — best-effort close
            pass

    def _release_locks(self) -> None:
        _release_exclusive(self._file_lock)
        self._file_lock = None
        try:
            self._process_lock.release()
        except RuntimeError:
            pass

    # -- run ------------------------------------------------------------

    def _run(self) -> None:
        try:
            self._acquire()
        except RefreshCancelled:
            self._finish("cancelled", "cancelled", [CANCEL_WARNING])
        except RefreshStale:
            self._finish("failed", "staleConfiguration", [STALE_WARNING])
        except RefreshUnavailableError:
            self._finish("failed", "noCapacities", [NO_RESULT_WARNING])
        except RefreshIncomplete as exc:
            self._finish("failed", "noCapacities", [exc.warning])
        except refresh_state.SnapshotConflictError:
            self._finish("failed", "staleConfiguration", [STALE_WARNING])
        except refresh_state.RefreshStateStorageError:
            self._finish(
                "failed",
                "noCapacities",
                ["Capacities refresh could not store content; the previous "
                 "complete result is preserved."],
            )
        except tag_exclusions.TagExclusionBlocked:
            # The matcher's structured diagnostics stay on the exception (the
            # route seam renders them); the job warning is the fixed bounded
            # string, never the payload. Nothing is installed, so the previous
            # complete generation is retained.
            self._finish("failed", "noCapacities", [TAG_EXCLUSION_WARNING])
        except (
            capacities_adapter.CapacitiesContractError,
            capacities_adapter.CapacitiesRateLimited,
            refresh_state.RefreshStateError,
        ):
            self._finish("failed", "noCapacities", [FAILURE_WARNING])
        except Exception:  # noqa: BLE001 — provider boundary, bounded warning
            self._finish("failed", "noCapacities", [FAILURE_WARNING])
        finally:
            try:
                self.store.note_run_finished(self.scope_key)
            except refresh_state.RefreshStateError:
                pass
            self._release_locks()

    def _finish(self, phase: str, outcome: str, warnings: list[str]) -> None:
        job = self._job or {}
        now = self._wall_clock()
        existing = list(job.get("warnings", ()))
        for warning in warnings:
            if warning not in existing:
                existing.append(warning)
        job = {
            **job,
            "phase": phase,
            "outcome": outcome,
            "warnings": existing,
            "updated_at": now,
            "finished_at": now,
        }
        self._job = job
        self._persist_job()

    def _set_phase(self, phase: str) -> None:
        if self.cancel_event.is_set():
            _raise_cancelled()
        if self._job is None:
            return
        self._job = {**self._job, "phase": phase, "updated_at": self._wall_clock()}
        self._persist_job()

    def _progress(self, *, type_key: str | None = None, listed: int = 0, read: int = 0) -> None:
        job = self._job
        if job is None:
            return
        progress = dict(job.get("progress") or {})
        types = dict(progress.get("types") or {})
        progress["listed"] = int(progress.get("listed", 0)) + listed
        progress["read"] = int(progress.get("read", 0)) + read
        if type_key is not None:
            entry = dict(types.get(type_key) or {"listed": 0, "read": 0})
            entry["listed"] = int(entry.get("listed", 0)) + listed
            entry["read"] = int(entry.get("read", 0)) + read
            types[type_key] = entry
        progress["types"] = types
        self._job = {**job, "progress": progress, "updated_at": self._wall_clock()}
        self._persist_job()

    # -- acquisition ----------------------------------------------------

    def _acquire(self) -> None:
        job = self._job or {}
        self.adapter.ensure_contract()
        contributing = [m for m in self.mappings if self.adapter.can_contribute(m)]
        if not contributing:
            raise RefreshIncomplete(
                "No configured Capacities type can contribute; no complete "
                "result was published."
            )
        configured_types = sorted(m.structure_id for m in contributing)
        prior = self.store.load_snapshot(self.scope_key)
        prior_types = sorted({m.type_key for m in prior.members}) if prior else []

        if job.get("mode") == "rescan" and job.get("scope") != DEFAULT_SCOPE_KEY:
            scope = job.get("scope")
            if scope not in configured_types:
                raise RefreshIncomplete(
                    "The Rescan scope is not a configured Capacities type; no "
                    "complete result was published."
                )
            listing_types = [scope]
            required_types = [scope]
            retained_types = [t for t in prior_types if t != scope]
        else:
            listing_types = configured_types
            required_types = configured_types
            retained_types = []

        mode = job.get("mode")
        logical_day = self.logical_day or date.today()
        listings: list[refresh_state.TypeListing] = []
        rows_by_type: dict[str, tuple[dict[str, Any], ...]] = {}
        for type_key in listing_types:
            if self.cancel_event.is_set():
                _raise_cancelled()
            enumeration = self.adapter.enumerate_structure(type_key)
            if not enumeration.complete:
                raise RefreshIncomplete(
                    "Capacities could not complete the listing for every "
                    "configured type; the previous complete result is "
                    "preserved."
                )
            object_ids: list[str] = []
            for row in enumeration.rows:
                object_id = _text(row.get("id")) if isinstance(row, dict) else ""
                if not object_id:
                    raise RefreshIncomplete(
                        "Capacities returned a listing row without an object "
                        "identity; no complete result was published."
                    )
                object_ids.append(object_id)
            rows_by_type[type_key] = enumeration.rows
            listings.append(
                refresh_state.TypeListing(
                    type_key=type_key,
                    object_ids=tuple(object_ids),
                    listing_checked_at=self._wall_clock(),
                    complete=True,
                )
            )
            self._progress(type_key=type_key, listed=len(object_ids))

        # Hydrate: read only new/uncached content (ordinary) or the whole
        # scoped type (Rescan). Successful reads persist immediately.
        merged: list[dict[str, Any]] = []
        unreadable = False
        for type_key in listing_types:
            for row in rows_by_type[type_key]:
                if self.cancel_event.is_set():
                    _raise_cancelled()
                object_id = _text(row.get("id"))
                try:
                    content, _fresh = self._read_or_reuse(
                        object_id, type_key, rescan=(mode == "rescan")
                    )
                except RefreshCancelled:
                    raise
                except refresh_state.RefreshStateStorageError:
                    raise
                except Exception:  # noqa: BLE001 — one unreadable object
                    unreadable = True
                    continue
                merged.append({**row, **content})
                self._progress(type_key=type_key, read=1)
        if unreadable:
            raise RefreshIncomplete(
                "One or more required Capacities objects could not be read; "
                "the previous complete result is preserved."
            )

        self._set_phase("evaluating")
        if self.cancel_event.is_set():
            _raise_cancelled()
        _result = self.adapter.items_for_day_from_objects(logical_day, merged)

        self._set_phase("publishing")
        if self.cancel_event.is_set():
            _raise_cancelled()
        evidence = refresh_state.SnapshotEvidence(
            scope_key=self.scope_key,
            revision=int(job.get("revision") or 0),
            required_types=tuple(required_types),
            listings=tuple(listings),
            retained_types=tuple(retained_types),
        )
        guard = self.config_guard() if callable(self.config_guard) else nullcontext()
        with guard:
            current = self.revision_supplier()
            if type(current) is not int or current != evidence.revision:
                raise RefreshStale()
            snapshot = self.store.install_generation(
                evidence, expected_generation=int(job.get("generation") or 0)
            )
        job = self._job or {}
        self._job = {**job, "generation": snapshot.generation}
        # The projection warnings — including the bounded review surface for
        # under-evaluated rows — ride the successful finish; the publication
        # itself stays one complete generation with every candidate retained.
        self._finish("complete", "published", list(_result.warnings))

    def _read_or_reuse(self, object_id: str, type_key: str, *, rescan: bool):
        if not rescan:
            cached = self.store.get(object_id, type_key)
            if cached is not None:
                return cached.content, False
        content = self.provider.get_object(object_id)
        if not isinstance(content, dict) or not isinstance(content.get("properties"), dict):
            raise RefreshIncomplete(
                "A required Capacities object read was malformed; no complete "
                "result was published."
            )
        self.store.put(object_id, type_key, content)
        return content, True


def _raise_cancelled() -> None:
    raise RefreshCancelled("cancelled")


def _snapshot_summary(snapshot: refresh_state.CompleteSnapshot | None) -> dict[str, Any]:
    if snapshot is None:
        return {
            "present": False,
            "generation": 0,
            "revision": None,
            "installed_at": None,
            "member_count": 0,
            "type_check_times": {},
        }
    return {
        "present": True,
        "generation": snapshot.generation,
        "revision": snapshot.revision,
        "installed_at": snapshot.installed_at,
        "member_count": len(snapshot.members),
        "type_check_times": dict(snapshot.type_check_times),
    }


def _coverage(snapshot: refresh_state.CompleteSnapshot | None, job: Any) -> dict[str, Any]:
    if snapshot is None:
        return {}
    started_at = job.get("started_at") if isinstance(job, dict) else None
    coverage: dict[str, dict[str, Any]] = {}
    for type_key, checked_at in snapshot.type_check_times:
        coverage[type_key] = {
            "listing_checked_at": checked_at,
            "members": 0,
            "freshly_read": 0,
        }
    for member in snapshot.members:
        entry = coverage.setdefault(
            member.type_key,
            {"listing_checked_at": None, "members": 0, "freshly_read": 0},
        )
        entry["members"] += 1
        if isinstance(started_at, (int, float)) and member.content_read_at >= started_at:
            entry["freshly_read"] += 1
    return coverage
