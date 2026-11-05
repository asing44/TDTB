"""capacities_refresh.py — paced, single-flight direct Capacities refresh.

This module owns the acquisition half of the direct Capacities refresh (U2):
it enumerates every configured type completely, re-reads only the content that
is new, uncached, or older than the refresh-mode read-through TTL (and every
scoped object for a Rescan), paces every provider read, and publishes through
the U1 store (``capacities_refresh_state``) as each type finishes and once more
when the whole read completes.
It never rewrites the legacy live reader; it reuses the adapter's transport
contract, pagination, object normalization, and projection through public
seams.

Contract (KTD1–KTD3):

* **One machine-local single-flight job.** A process-local lock plus a POSIX
  advisory lock file serialize Refresh/Rescan across threads and processes.
  Status queries make no provider call and never spawn work.
* **Endpoint-aware pacing.** Every response's rate-limit headers are parsed
  into one budget per endpoint (``RateLimit``/``ratelimit-*`` forms, lowest
  remaining wins), so pacing follows the API's own numbers while that budget
  is current: a header above the documented 30 requests per 60 seconds raises
  throughput, one below lowers it. When no usable header is present — or the
  budget's reset passes unrefreshed — the documented 30 per 60 seconds governs
  again. ``Retry-After`` keeps its 429 meaning (up to ``max_backoff``). A
  rate-limited run waits and resumes by itself within a per-run wait budget;
  an explicit ``max_retries`` keeps the older count bound. Errors stay truthful
  and cancellation interrupts every backoff.
* **Measured runs.** A job document carries a ``progress.metrics`` block with
  the run's own read count, read timings, read rate per minute, elapsed time,
  cold flag, 429 count, and the last observed content limit — additive
  numbers, booleans, and nulls only, never content or exception text.
* **Read-through refresh TTL.** An explicit Refresh reuses cached object
  content younger than :data:`DEFAULT_CONTENT_TTL_SECONDS` (10 minutes) with
  zero provider reads, so a back-to-back Refresh stays cheap, and re-reads
  older content so an Assigned On/status/title edit made in Capacities reaches
  TDTB on the next Refresh. A Rescan ignores the TTL and re-reads every scoped
  object. The U1 store itself never expires an entry.
* **Per-type publication in a priority order.** Types are listed completely
  up front, then read one type at a time in a deterministic priority order
  (smallest read count first, operator-overridable through the coordinator's
  ``type_order``): as a type finishes reading, an *interim* generation is
  installed that publishes the finished types fresh and keeps every unread type
  at its last published state, naming those types on the generation and in the
  job (``progress.publication``/``progress.unchecked_types``). The final install
  publishes the complete generation, which clears the interim flag and drops
  every member the complete read did not confirm. A type whose content was
  entirely reused from the cache publishes nothing early: it read no provider
  content, so it has nothing newer to show.
* **Complete scope or nothing, for the complete install.** Any incomplete
  listing, failed required read, changed configuration revision, or changed
  source space prevents the *complete* installation. A run that stops after an
  interim stays interim: it is marked partial, names its unchecked types, and
  never claims a complete read. The previous complete generation and every
  successful read are preserved.
* **No secret leakage.** Warnings are bounded fixed strings; exception text,
  tokens, and filesystem paths never reach a warning or a job document.

Machine-local only: the caller owns the state root (``app_config.state_dir()``)
so raw object content never enters a synced folder.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import threading
import time
from collections import deque
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Mapping

try:  # POSIX advisory locks
    import fcntl as _fcntl
except ImportError:  # pragma: no cover — non-POSIX fallback
    _fcntl = None

import capacities_adapter
import capacities_cache_io
import capacities_refresh_state as refresh_state
import capacities_schema
import capacities_tag_titles
import tag_exclusions

logger = logging.getLogger("tdtb.capacities_refresh")

#: Endpoint identities shared with the adapter's transport observer.
ENDPOINT_STRUCTURES = "structures"
ENDPOINT_LISTING = "listing"
ENDPOINT_CONTENT = "content"

#: The fixed ``progress.waiting.reason`` value for a rate-limit wait.
RATE_LIMIT_WAIT_REASON = "rateLimited"

JOB_DIRNAME = "jobs"
JOB_LOCK_FILENAME = ".refresh-job.lock"
JOB_SCHEMA_VERSION = 1

#: The published generation is keyed by one logical scope: the complete
#: configured Capacities scope. A scoped Rescan updates a member type but
#: still installs (or refuses) the one whole-scope generation.
DEFAULT_SCOPE_KEY = "all"

#: Refresh-mode read-through TTL (seconds). During an explicit Refresh a
#: cached object whose ``content_read_at`` is older than this is re-read and
#: renewed; younger content is reused with zero provider reads. A Rescan
#: ignores the TTL and re-reads every scoped object. Ten minutes keeps a
#: back-to-back Refresh cheap while still letting a Capacities edit reach TDTB
#: on the next Refresh.
DEFAULT_CONTENT_TTL_SECONDS = 600.0

TERMINAL_PHASES = frozenset(
    {"complete", "failed", "cancelled", "interrupted"}
)

FAILURE_WARNING = (
    "Capacities could not complete a full refresh; the previous complete "
    "result is preserved and no partial result was published."
)
#: Prefixes for the bounded, content-free failure diagnostics appended to the
#: job after FAILURE_WARNING: one ``Cause:`` line (exception class name plus
#: retry-after or a bounded exception code/kind when present) and one
#: ``Where:`` line for the acquisition stage. The exception message is never
#: included.
CAUSE_PREFIX = "Cause: "
WHERE_PREFIX = "Where: "
#: The fixed acquisition-stage vocabulary a failure can be attributed to.
#: Bounded and closed so neither the job document nor the server log can
#: carry source content.
FAILURE_STAGES = ("list", "read", "assemble", "install")
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
#: The one bounded notice a run adds when it stops after publishing an interim
#: generation: the plan on screen is that run's partial result, not a complete
#: read, and the named types keep their last published state.
INTERIM_RETAINED_WARNING = (
    "The published Capacities result is interim: the refresh stopped before "
    "every object type was read, so the unchecked types keep their last "
    "published state until a complete refresh finishes."
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


def _coerce_count(value: Any) -> int | None:
    """An integer read out of a header value, or ``None``.

    A quantity is only usable when it is written as an integer: ``"5"`` is,
    ``"4.5"`` and ``"four"`` are not, so a malformed number can never widen
    the pacing budget it was meant to describe.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


#: The rolling window (seconds) of the conservative fallback policy. A header
#: ``reset`` longer than twice this is not a rate-limit window this module can
#: trust, so such an observation counts as unusable and the fallback applies.
FALLBACK_RATE_WINDOW_SECONDS: float = DEFAULT_POLICIES[ENDPOINT_CONTENT].window

#: Response header names that carry a whole rate-limit policy (IETF draft-8).
RATE_LIMIT_HEADER_NAMES = ("ratelimit", "rate-limit")

#: Per-item policy keys: the structured-field short names (``q``/``r``/``t``)
#: and their spelled-out equivalents.
RATE_LIMIT_FIELD_ALIASES = {
    "q": "limit",
    "limit": "limit",
    "r": "remaining",
    "remaining": "remaining",
    "t": "reset",
    "reset": "reset",
}

#: The split per-field headers read today, most specific prefix first.
RATE_LIMIT_SPLIT_PREFIXES = ("x-ratelimit-", "ratelimit-", "rate-limit-")


@dataclass(frozen=True)
class RateLimitObservation:
    """One usable rate-limit budget taken from a response's headers.

    ``reset_seconds`` is the response's own delta-seconds until the window
    resets, and ``limit`` is the policy's stated ceiling when the header
    carries one. Anything that is not usable yields ``None`` from
    :func:`parse_rate_limit`, so the caller keeps its conservative fallback.
    """

    limit: int | None
    remaining: int
    reset_seconds: float


def _split_rate_limit_policies(value: str) -> list[dict[str, str]]:
    """Break one rate-limit header value into per-policy field maps.

    Accepts both published shapes: the parameter list Capacities sends today
    (``limit=5, remaining=4, reset=58``) and the structured-field form
    (``"default";r=4;t=58``, optionally several comma-separated policies).
    Comma-separated items are separate policies only when at least one item
    names a policy; otherwise they are the parameters of a single policy.
    """
    parsed: list[tuple[str | None, dict[str, str]]] = []
    for item in value.split(","):
        if not item.strip():
            continue
        name: str | None = None
        fields: dict[str, str] = {}
        for segment in item.split(";"):
            segment = segment.strip()
            if not segment:
                continue
            raw_key, separator, raw_value = segment.partition("=")
            if separator:
                field = RATE_LIMIT_FIELD_ALIASES.get(
                    raw_key.strip().strip('"').lower()
                )
                if field is not None:
                    fields[field] = raw_value.strip().strip('"')
            elif name is None:
                name = segment.strip('"')
        parsed.append((name, fields))
    if any(name is not None for name, _ in parsed):
        return [fields for _, fields in parsed]
    merged: dict[str, str] = {}
    for _, fields in parsed:
        merged.update(fields)
    return [merged] if merged else []


def _observation_from_fields(
    fields: Mapping[str, str],
) -> RateLimitObservation | None:
    """Coerce one policy's fields into an observation, or ``None``."""
    remaining = _coerce_count(fields.get("remaining"))
    if remaining is None or remaining < 0:
        return None
    reset = _coerce_seconds(fields.get("reset"))
    if reset is None or reset > 2 * FALLBACK_RATE_WINDOW_SECONDS:
        return None
    limit: int | None = None
    if "limit" in fields:
        limit = _coerce_count(fields.get("limit"))
        if limit is None or limit < 0:
            return None
    return RateLimitObservation(
        limit=limit, remaining=remaining, reset_seconds=float(reset)
    )


def parse_rate_limit(headers: Any) -> RateLimitObservation | None:
    """The tightest usable rate-limit budget in ``headers``, or ``None``.

    Tolerant by design: the combined ``RateLimit`` header (either published
    shape) is read first, then the split ``x-ratelimit-*``/``ratelimit-*``
    fields this module has always honored. Several policies in one header mean
    the lowest ``remaining`` wins, because the tightest stated budget is the
    one that cannot be exceeded. A missing or non-integer ``remaining``, a
    negative number, an unusable ``limit``, or a reset longer than twice the
    fallback window is unusable, so the caller keeps its conservative window.
    ``Retry-After`` is not a budget and is never read here.
    """
    if not isinstance(headers, Mapping):
        return None
    lowered = {str(key).strip().lower(): value for key, value in headers.items()}
    observations: list[RateLimitObservation] = []
    for name in RATE_LIMIT_HEADER_NAMES:
        value = lowered.get(name)
        if value is None:
            continue
        for fields in _split_rate_limit_policies(str(value)):
            observation = _observation_from_fields(fields)
            if observation is not None:
                observations.append(observation)
    if not observations:
        split_fields: dict[str, str] = {}
        for prefix in RATE_LIMIT_SPLIT_PREFIXES:
            for field in ("limit", "remaining", "reset"):
                key = f"{prefix}{field}"
                if key in lowered and field not in split_fields:
                    split_fields[field] = str(lowered[key])
        if split_fields:
            observation = _observation_from_fields(split_fields)
            if observation is not None:
                observations.append(observation)
    if not observations:
        return None
    return min(observations, key=lambda item: item.remaining)


def _bounded_identifier(value: Any) -> str | None:
    """A short, content-free token from an exception's own code/kind field.

    Anything that is not a plain identifier-shaped string (or is longer than
    64 characters) is dropped rather than surfaced, so a diagnostic field
    can never smuggle source content into a warning or a log line.
    """
    if not isinstance(value, str):
        return None
    token = value.strip()
    if not token or len(token) > 64:
        return None
    if not all(char.isalnum() or char in "_.-" for char in token):
        return None
    return token


def _bounded_detail(value: Any) -> str | None:
    """A <=200-character printable diagnostic string.

    The adapter sets ``detail`` only from configured identifiers and fixed
    phrases, but the renderer bounds it anyway so no exception field can
    widen a warning or a log line.
    """
    if not isinstance(value, str):
        return None
    cleaned = "".join(char for char in value if char.isprintable())
    cleaned = " ".join(cleaned.split())
    if not cleaned:
        return None
    return cleaned[:200]


def _failure_cause_line(exc: BaseException) -> str:
    """One bounded cause line: the exception CLASS NAME, plus only the
    structured facts the exception itself already exposes — the retry-after
    seconds for a rate limit, or a bounded code/kind/detail field for a
    contract error. The exception message is deliberately never included."""
    name = type(exc).__name__
    detail: str | None = None
    if isinstance(exc, capacities_adapter.CapacitiesRateLimited):
        seconds = _coerce_seconds(getattr(exc, "retry_after", None))
        if seconds is not None:
            detail = f"retry after {seconds:g}s"
    elif isinstance(exc, capacities_adapter.CapacitiesContractError):
        detail = (
            _bounded_detail(getattr(exc, "detail", None))
            or _bounded_identifier(getattr(exc, "code", None))
            or _bounded_identifier(getattr(exc, "kind", None))
        )
    return f"{CAUSE_PREFIX}{name} ({detail})" if detail else f"{CAUSE_PREFIX}{name}"


def _failure_where_line(stage: Any) -> str | None:
    """The fixed acquisition-stage line, or ``None`` for an unknown stage."""
    if stage in FAILURE_STAGES:
        return f"{WHERE_PREFIX}{stage}"
    return None


@dataclass
class _HeaderBudget:
    """A live per-endpoint budget taken from one response's headers.

    ``remaining`` counts down locally, ``reset_at`` is the monotonic instant
    the API said the window resets, and ``limit`` is the stated ceiling when
    the header carried one. A budget governs pacing only while it is current;
    once ``reset_at`` passes without a newer observation the conservative
    fallback window governs again.
    """

    remaining: int
    reset_at: float
    limit: int | None


class ProviderPacer:
    """Endpoint-aware, clock-injectable request pacing and 429 backoff."""

    def __init__(
        self,
        *,
        policies: Mapping[str, EndpointPolicy] | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float, threading.Event | None], None] | None = None,
        cancel_event: threading.Event | None = None,
        max_retries: int | None = 3,
        base_backoff: float = 1.0,
        max_backoff: float = 30.0,
        rate_limit_budget_seconds: float | None = None,
        on_wait: Callable[[str, float, str | None], None] | None = None,
    ) -> None:
        self.policies = dict(policies or DEFAULT_POLICIES)
        self._clock = clock
        self._sleeper = sleeper or _default_sleeper
        self._cancel_event = cancel_event
        #: ``None`` means unbounded retries, stopped only by the wait budget.
        self.max_retries = None if max_retries is None else max(0, int(max_retries))
        self.base_backoff = max(0.0, float(base_backoff))
        self.max_backoff = max(0.0, float(max_backoff))
        #: The per-run ceiling on cumulative rate-limit sleep; only consulted
        #: when ``max_retries`` is ``None``.
        self.rate_limit_budget_seconds = (
            None
            if rate_limit_budget_seconds is None
            else max(0.0, float(rate_limit_budget_seconds))
        )
        self.on_wait = on_wait
        #: Cumulative 429 / reset-header cooldown seconds slept in this run.
        self.rate_limit_slept = 0.0
        #: 429 responses handled in this run; reset by :meth:`begin_run`.
        self.rate_limited = 0
        #: The last usable header limit per endpoint (``{limit, window}``),
        #: kept for the job's run metrics and reset by :meth:`begin_run`.
        self.observed_limits: dict[str, dict[str, Any]] = {}
        self._times: dict[str, deque[float]] = {}
        self._cooldown_until: dict[str, float] = {}
        self._retry_after: dict[str, float] = {}
        self._budgets: dict[str, _HeaderBudget] = {}
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

    def begin_run(self) -> None:
        """Reset the per-run rate-limit accounting and header budgets."""
        self.rate_limit_slept = 0.0
        self.rate_limited = 0
        self.observed_limits.clear()
        self._budgets.clear()

    def _notify_wait(self, endpoint: str, seconds: float, reason: str | None) -> None:
        """Report one rate-limit wait to the hook, then its end (``None``).

        Ordinary window pacing never reaches this hook; only a 429 retry or a
        rate-limit-reset cooldown does, so a caller can surface the wait
        without reporting routine throttling as a rate limit.
        """
        if self.on_wait is None:
            return
        self.on_wait(endpoint, float(seconds), reason)

    def _wait_after_rate_limit(self, endpoint: str, seconds: float) -> None:
        """Sleep out one rate-limit delay, interruptible by cancellation."""
        self._notify_wait(endpoint, seconds, RATE_LIMIT_WAIT_REASON)
        try:
            self.sleep(seconds)
        finally:
            self._notify_wait(endpoint, 0.0, None)

    def _current_budget(self, endpoint: str) -> _HeaderBudget | None:
        """This endpoint's live header budget, expiring it when stale."""
        budget = self._budgets.get(endpoint)
        if budget is not None and self._clock() >= budget.reset_at:
            self._budgets.pop(endpoint, None)
            return None
        return budget

    # -- request admission ---------------------------------------------

    def before_request(self, endpoint: str) -> None:
        """Block until one request for ``endpoint`` is within policy.

        While a header budget is current it alone governs: the local
        ``remaining`` counts down and reaching zero waits out ``reset_at``
        through the rate-limit cooldown path, so no request ever exceeds what
        the API said was left. Once that budget is absent or expired, the
        conservative rolling window applies as before.
        """
        self._check_cancel()
        policy = self._policy(endpoint)
        times = self._times_for(endpoint)
        now = self._clock()
        budget = self._current_budget(endpoint)
        if budget is not None:
            if budget.remaining > 0:
                budget.remaining -= 1
            else:
                wait = max(0.0, budget.reset_at - now)
                if wait > 0:
                    self._wait_after_rate_limit(endpoint, wait)
                self._budgets.pop(endpoint, None)
                now = self._clock()
                budget = None
        if budget is None:
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
            self._wait_after_rate_limit(endpoint, cooldown - now)
            now = self._clock()
        times.append(now)

    # -- response observation ------------------------------------------

    def observe(self, endpoint: str, headers: Any) -> None:
        """Record optional rate-limit headers conservatively.

        A usable rate-limit header replaces this endpoint's budget, so pacing
        follows the API's own per-endpoint numbers until ``reset_at`` passes;
        an absent or unusable header leaves the fallback window in charge.
        ``Retry-After`` keeps its existing 429 meaning. Every response's
        headers — including a 429's — reach this seam, so the budget stays
        current without an extra provider call.
        """
        if not isinstance(headers, Mapping):
            return
        lowered = {str(key).lower(): value for key, value in headers.items()}
        retry = _coerce_seconds(lowered.get("retry-after"))
        observation = parse_rate_limit(lowered)
        if observation is not None:
            self._budgets[endpoint] = _HeaderBudget(
                remaining=observation.remaining,
                reset_at=self._clock() + observation.reset_seconds,
                limit=observation.limit,
            )
            if observation.limit is not None:
                self.observed_limits[endpoint] = {
                    "limit": int(observation.limit),
                    "window": float(observation.reset_seconds),
                }
        if retry is not None:
            self.note_rate_limited(endpoint, retry)

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
                # Counted per run from the response that actually arrived, so
                # a job document can state whether a run met its 429 limit.
                self.rate_limited += 1
                self.note_rate_limited(endpoint, getattr(exc, "retry_after", None))
                if self.max_retries is not None and attempt >= self.max_retries:
                    raise
                delay = self.retry_delay(endpoint, attempt)
                budget = self.rate_limit_budget_seconds
                if (
                    self.max_retries is None
                    and budget is not None
                    and self.rate_limit_slept + delay > budget
                ):
                    # The run cannot wait this one out within its budget, so
                    # it fails truthfully instead of sleeping past it.
                    raise
                self._wait_after_rate_limit(endpoint, delay)
                self.rate_limit_slept += delay
                attempt += 1
                continue
            self.note_success(endpoint)
            return result


class PacedProvider:
    """The provider seam the adapter/coordinator see: paced and retried."""

    def __init__(self, raw: Any, pacer: ProviderPacer) -> None:
        self.raw = raw
        self.pacer = pacer
        #: The raw structures payload most recently fetched through this seam.
        self.structures_payload: Any = None
        setter = getattr(raw, "set_rate_observer", None)
        if callable(setter):
            setter(self.observe_headers)

    def observe_headers(self, endpoint: str, headers: Any) -> None:
        self.pacer.observe(endpoint, headers)

    @property
    def base_url(self) -> str | None:
        """The wrapped transport's provider base URL, when it exposes one."""
        return getattr(self.raw, "base_url", None)

    @property
    def supports_object_listing(self) -> bool:
        """Whether the wrapped transport can enumerate objects.

        Delegated, never assumed: the paced seam is only as capable as the raw
        transport behind it, so a replay transport wrapped in pacing still
        refuses the adapter's RootTag catalog read.
        """
        return getattr(self.raw, "supports_object_listing", False) is True

    def fetch_structures(self):
        payload = self.pacer.call(ENDPOINT_STRUCTURES, self.raw.fetch_structures)
        # The adapter resolves its contract from this exact payload. Keeping it
        # lets the install persist the contract without another provider read.
        self.structures_payload = payload
        return payload

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


def _job_path_for(root: str | Path, namespace: str) -> Path:
    """The durable job document path for one namespace under ``root``."""
    return Path(root) / JOB_DIRNAME / f"{namespace}.json"


def _load_job_document(path: Path) -> dict[str, Any] | None:
    """The validated job mapping in one document, or ``None``.

    A missing, unreadable, unparsable, or wrong-schema document is ``None``
    rather than an error: the document is a progress record, not authority.
    """
    try:
        text = path.read_text(encoding="utf-8")
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


def read_last_job(root: str | Path, space_id: str) -> dict[str, Any] | None:
    """The last persisted job document for ``space_id``, or ``None``.

    A public pure local read: no provider call, and no coordinator is built, so
    a caller that must not construct one (``/plan-inputs``) can still tell how
    the last Refresh ended. The path uses the coordinator's default namespace
    (``_digest(space_id)``), which is what the builder constructs, and the
    document is validated exactly as :meth:`RefreshCoordinator._load_job`.
    """
    if not isinstance(space_id, str) or not space_id.strip():
        return None
    return _load_job_document(_job_path_for(root, _digest(space_id.strip())))


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _iso_utc(epoch: float) -> str:
    """ISO-8601 UTC timestamp for one wall-clock epoch seconds value."""
    return datetime.fromtimestamp(float(epoch), tz=timezone.utc).isoformat()


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


def _retained_member_counts(
    prior: refresh_state.CompleteSnapshot | None, retained_types: Sequence[str]
) -> dict[str, int]:
    """Objects kept from the previous generation, grouped by type key.

    A type that lost eligibility, or left the mapping record, keeps its
    published membership with the original timestamps. The count comes from the
    prior generation itself, so naming it costs no provider read.
    """
    if prior is None or not retained_types:
        return {}
    retained = set(retained_types)
    counts: dict[str, int] = {}
    for member in prior.members:
        if member.type_key in retained:
            counts[member.type_key] = counts.get(member.type_key, 0) + 1
    return counts


def retained_scope_warnings(
    counts: Mapping[str, int],
    *,
    structures_payload: Any,
    mapped_structure_ids: set[str],
) -> list[str]:
    """One bounded line per type retained instead of enumerated.

    ``structure-not-eligible`` means the type is still mapped but no assignment
    path, Active enable, or stored rule can make its objects eligible, so the
    enumeration gate skipped it; ``structure-unmapped`` means the type is no
    longer configured at all. Either way the objects are NOT deleted — they stay
    in the published generation — so the line says "retained", names the type,
    the count, and the reason, instead of leaving one anonymous aggregate.
    """
    warnings: list[str] = []
    for type_key in sorted(counts):
        reason = (
            capacities_adapter.DROP_REASON_STRUCTURE_NOT_ELIGIBLE
            if type_key in mapped_structure_ids
            else capacities_adapter.DROP_REASON_STRUCTURE_UNMAPPED
        )
        warnings.append(
            capacities_adapter.retained_scope_warning(
                capacities_adapter.structure_label(structures_payload, type_key),
                {reason: counts[type_key]},
            )
        )
    return [warning for warning in warnings if warning]


def generation_coverage_warnings(
    *,
    type_keys: Iterable[str],
    member_type_keys: Iterable[str],
    cached_objects: int,
    generation_objects: int,
    retargeted: int = 0,
    structures_payload: Any = None,
) -> list[str]:
    """Diagnostics for a published generation the owner's edits left gaps in.

    Three owner-caused states are invisible in the projection itself, so they
    are stated once each instead of being silently absorbed:

    * a configured type with ZERO members in the published generation — every
      object of that type stops being plannable, and nothing in the rows says
      why;
    * cached objects the generation does not account for (``cached_objects``
      minus ``generation_objects``) — stale content can outnumber the members;
    * ``retargeted`` cached objects whose stored ``structureId`` disagrees with
      the generation's type key, which would otherwise be projected through
      another type's mapping without a word.

    Pure and local: the caller supplies counts it already holds, so a warning
    costs no provider read. Lines carry a structure label, a count, and the
    Refresh hint only — never an object title or a property value. A clean
    generation returns ``[]``.
    """
    present = {key for key in member_type_keys if isinstance(key, str) and key}
    warnings: list[str] = []
    for type_key in sorted({
        key for key in type_keys if isinstance(key, str) and key
    }):
        if type_key in present:
            continue
        label = capacities_adapter.structure_label(structures_payload, type_key)
        warnings.append(
            f"Capacities type {label!r}: no objects in the published "
            "generation; Refresh to re-list it."
        )
    orphans = int(cached_objects) - int(generation_objects)
    if orphans > 0:
        warnings.append(
            f"{orphans} cached Capacities objects are not in the published "
            "generation; Refresh to reconcile."
        )
    if int(retargeted) > 0:
        warnings.append(
            f"{int(retargeted)} cached Capacities objects changed type since "
            "the last Refresh; Refresh to reconcile."
        )
    return warnings


def _generation_object_count(
    store: Any,
    snapshot: refresh_state.CompleteSnapshot,
    listed_types: set[str],
) -> int:
    """How many of a published generation's members the cache backs.

    Every member of a type this run listed was read successfully (an unreadable
    object fails the run before installation), so those members are cached. A
    retained member keeps its dated membership from the prior generation and is
    counted only while its cached object is still present, so an evicted file
    reads as an orphan instead of disappearing into the difference.
    """
    count = 0
    for member in snapshot.members:
        if member.type_key in listed_types or store.contains(
            member.object_id, member.type_key
        ):
            count += 1
    return count


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
        settings: Any = None,
        rules: Any = None,
        exclusion_policy: Any = None,
        max_pages: int = 20,
        logical_day: date | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float, threading.Event | None], None] | None = None,
        max_retries: int | None = None,
        rate_limit_budget_seconds: float = 900.0,
        max_backoff: float = 120.0,
        policies: Mapping[str, EndpointPolicy] | None = None,
        namespace: str | None = None,
        config_guard: Callable[[], Any] | None = None,
        wall_clock: Callable[[], float] = time.time,
        content_ttl: float = DEFAULT_CONTENT_TTL_SECONDS,
        configuration_revision: int | None = None,
        space_id_supplier: Callable[[], str | None] | None = None,
        type_order: Iterable[str] | None = None,
    ) -> None:
        if not isinstance(space_id, str) or not space_id.strip():
            raise ValueError("space_id must be a nonblank string")
        if not mappings:
            raise ValueError("at least one Capacities mapping is required")
        if max_pages < 1:
            raise ValueError("max_pages must be positive")
        try:
            ttl_seconds = float(content_ttl)
        except (TypeError, ValueError):
            raise ValueError(
                "content_ttl must be a nonnegative finite number"
            ) from None
        if not math.isfinite(ttl_seconds) or ttl_seconds < 0:
            raise ValueError("content_ttl must be a nonnegative finite number")
        self.store = store
        self.root = Path(root)
        self.scope_key = scope_key
        self.max_pages = int(max_pages)
        self.space_id = space_id.strip()
        self.mappings = tuple(mappings)
        self.revision_supplier = revision_supplier
        # The revision read before the adapter configuration was frozen; start
        # and publication compare against that same snapshot.
        self.configuration_revision = configuration_revision
        # The optional live source-space read for the publication guard: the
        # combined revision is a sum, so a source space switch can leave it
        # equal while the adapter was frozen for another space.
        self.space_id_supplier = space_id_supplier
        #: The operator's own type priority (KTD7), configured through the
        #: caller's settings: the named types are read and published first, in
        #: this order, and every other type follows smallest read count first.
        #: Empty means the deterministic smallest-first order alone.
        self.type_order = tuple(
            key
            for key in (
                _text(value) for value in (type_order or ())
            )
            if key
        )
        self.logical_day = logical_day
        self.config_guard = config_guard
        self._wall_clock = wall_clock
        self._content_ttl = ttl_seconds
        self._namespace = namespace or _digest(self.space_id)
        self.cancel_event = threading.Event()
        self.pacer = ProviderPacer(
            policies=policies,
            clock=clock,
            sleeper=sleeper,
            cancel_event=self.cancel_event,
            max_retries=max_retries,
            max_backoff=max_backoff,
            rate_limit_budget_seconds=rate_limit_budget_seconds,
            on_wait=self._note_rate_limit_wait,
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
                settings=settings,
                rules=rules,
                exclusion_policy=exclusion_policy,
                content_cache=None,
            ),
            # Tag titles the catalog read resolves here are display metadata for
            # the next OFFLINE replay: the digest route projects the cached
            # generation and must never call the provider, so the map has to
            # outlive this run. Advisory: a cache failure never fails a refresh.
            tag_titles_sink=self._remember_tag_titles,
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
        #: The bounded acquisition stage the job is currently in; used only to
        #: attribute a catch-all failure (``Where: list|read|assemble|install``).
        self._stage: str | None = "list"
        #: Wall-clock times of this run's first and last fresh content read;
        #: used only by the job's ``progress.metrics`` block, never admission.
        self._read_started_at: float | None = None
        self._read_finished_at: float | None = None
        #: The types this run has finished reading, in the order it finished
        #: them; only ever used to describe the interim publication truthfully.
        self._completed_types: list[str] = []
        #: The types not yet fully read, sorted. Mirrored into the job's
        #: ``progress.unchecked_types`` so the panel can name them (R7/R9).
        self._unchecked_types: tuple[str, ...] = ()
        #: The interim publication this run last installed, or ``None``. Every
        #: value is a type key, a count, an epoch, or a flag — never content.
        self._publication: dict[str, Any] | None = None
        self._interim_count = 0
        self._installed_at: float | None = None

    # -- paths / persistence -------------------------------------------

    def _job_path(self) -> Path:
        return _job_path_for(self.root, self._namespace)

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
        return _load_job_document(self._job_path())

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
            revision = (
                self.configuration_revision
                if self.configuration_revision is not None
                else self.revision_supplier()
            )
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
                "progress": {
                    "listed": 0,
                    "read": 0,
                    "to_read": 0,
                    "waiting": None,
                    "types": {},
                    "unchecked_types": [],
                    "publication": {
                        "partial": False,
                        "interim_count": 0,
                        "generation": (
                            prior.generation if prior is not None else 0
                        ),
                        "installed_at": None,
                        "unchecked_types": [],
                        "completed_types": [],
                    },
                },
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
        self.pacer.begin_run()
        self._read_started_at = None
        self._read_finished_at = None
        self._completed_types = []
        self._unchecked_types = ()
        self._publication = None
        self._interim_count = 0
        self._installed_at = None
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
        ) as exc:
            self._finish(
                "failed",
                "noCapacities",
                [FAILURE_WARNING, *self._failure_detail(exc)],
            )
        except Exception as exc:  # noqa: BLE001 — provider boundary, bounded warning
            self._finish(
                "failed",
                "noCapacities",
                [FAILURE_WARNING, *self._failure_detail(exc)],
            )
        finally:
            try:
                self.store.note_run_finished(self.scope_key)
            except refresh_state.RefreshStateError:
                pass
            self._release_locks()

    def _failure_detail(self, exc: BaseException) -> list[str]:
        """Bounded, content-free failure detail for the job and the log.

        The job gains a ``Cause:`` line (exception class name plus the
        retry-after seconds or a bounded exception code/kind when the
        exception itself carries one — never its message) and, when the
        acquisition stage is known, a ``Where:`` line. The server log carries
        the same facts at WARNING under this module's logger; neither surface
        can leak source content.
        """
        cause = _failure_cause_line(exc)
        detail = [cause]
        where = _failure_where_line(self._stage)
        if where is not None:
            detail.append(where)
        logger.warning(
            "Capacities refresh failed: %s stage=%s",
            cause,
            self._stage if self._stage in FAILURE_STAGES else "unknown",
        )
        return detail

    def _build_metrics(self, *, finished_at: float) -> dict[str, Any]:
        """This run's own numbers for the job's ``progress.metrics`` block.

        Every value is a number, boolean, or null: nothing derived from object
        content or exception text is stored. ``read_rate_per_minute`` covers
        only the read phase (it excludes listing time) and is null when the run
        performed no fresh read or both reads landed on one instant, so it
        never divides by zero.
        """
        job = self._job or {}
        progress = job.get("progress") or {}
        reads = _progress_count(progress.get("read"))
        listed = _progress_count(progress.get("listed"))
        to_read = _progress_count(progress.get("to_read"))
        started_at = job.get("started_at")
        started = (
            float(started_at)
            if isinstance(started_at, (int, float)) and not isinstance(started_at, bool)
            else None
        )
        first = self._read_started_at
        last = self._read_finished_at
        rate: float | None = None
        if reads > 0 and first is not None and last is not None:
            minutes = (last - first) / 60.0
            if minutes > 0:
                rate = reads / minutes
        observed = self.pacer.observed_limits.get(ENDPOINT_CONTENT)
        return {
            "reads": reads,
            "read_started_at": first,
            "read_finished_at": last,
            "read_rate_per_minute": rate,
            "elapsed_seconds": (
                None if started is None else float(finished_at) - started
            ),
            "cold": bool(job.get("mode") == "rescan" or to_read == listed),
            "rate_limited": int(self.pacer.rate_limited),
            "observed_limit": dict(observed) if observed is not None else None,
        }

    def _finish(self, phase: str, outcome: str, warnings: list[str]) -> None:
        job = self._job or {}
        now = self._wall_clock()
        # R8: a run that stops after an interim publication says so. The plan on
        # screen is that run's partial result, and the notice names the shared
        # fact (unchecked types keep their last published state) in one bounded
        # string that can never carry content or exception text.
        publication = self._publication_record(phase=phase)
        if publication["partial"] and INTERIM_RETAINED_WARNING not in warnings:
            warnings = [*warnings, INTERIM_RETAINED_WARNING]
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
        # Finalize the run metrics against the real finish instant; a run that
        # never reached its read phase still gets the elapsed time it consumed.
        self._progress_field("metrics", self._build_metrics(finished_at=now))
        # ... and the publication state, so a final read never has to infer it.
        self._progress_field(
            "unchecked_types", list(publication["unchecked_types"])
        )
        self._progress_field("publication", publication)

    def _publication_record(self, *, phase: str) -> dict[str, Any]:
        """The job's ``progress.publication`` block for one terminal phase.

        ``partial`` is True exactly when the generation installed last is an
        interim one: the run published while reading and never finished, so the
        unchecked types are named and keep their last published state. A run
        that reached ``complete`` replaced the interim whole and clears the
        flag. Only type keys, counts, one epoch, and flags are stored.
        """
        job = self._job or {}
        partial = bool(self._interim_count) and phase != "complete"
        return {
            "partial": partial,
            "interim_count": int(self._interim_count),
            "generation": _progress_count(job.get("generation")),
            "installed_at": self._installed_at,
            "unchecked_types": list(self._unchecked_types) if partial else [],
            "completed_types": sorted(set(self._completed_types)),
        }

    def _note_unchecked(self, type_keys: Iterable[str]) -> None:
        """Persist the types this run has not finished reading yet, sorted."""
        unchecked = tuple(sorted({_text(key) for key in type_keys if _text(key)}))
        if unchecked == self._unchecked_types:
            return
        self._unchecked_types = unchecked
        self._progress_field("unchecked_types", list(unchecked))

    def _note_publication(self, *, unchecked: Iterable[str]) -> None:
        """Persist one installed interim generation and its unchecked types."""
        job = self._job
        if job is None:
            return
        self._publication = {
            "partial": True,
            "interim_count": int(self._interim_count),
            "generation": _progress_count(job.get("generation")),
            "installed_at": self._installed_at,
            "unchecked_types": sorted(
                {_text(key) for key in unchecked if _text(key)}
            ),
            "completed_types": sorted(set(self._completed_types)),
        }
        progress = dict(job.get("progress") or {})
        progress["publication"] = self._publication
        progress["unchecked_types"] = list(self._unchecked_types)
        self._job = {
            **job,
            "progress": progress,
            "updated_at": self._installed_at or self._wall_clock(),
        }
        self._persist_job()

    def _publish_order(
        self, type_keys: Iterable[str], read_counts: Mapping[str, int]
    ) -> list[str]:
        """KTD7's read-and-publish order: smallest read count first.

        Every mapped contributing type is read; the order only decides which
        type finishes (and so publishes) soonest, which gets the most types
        fresh in the least time. It is deterministic — equal read counts fall
        back to the type key — and the operator's configured ``type_order``
        goes first, in that order, before the smallest-first remainder.
        """
        keys = list(dict.fromkeys(type_keys))
        priority = [key for key in self.type_order if key in keys]
        rest = [key for key in keys if key not in set(priority)]
        rest.sort(key=lambda key: (int(read_counts.get(key, 0)), key))
        return priority + rest

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

    def _progress_field(self, key: str, value: Any) -> None:
        """Set one additive ``progress`` field and persist the job once."""
        job = self._job
        if job is None:
            return
        progress = dict(job.get("progress") or {})
        progress[key] = value
        self._job = {**job, "progress": progress, "updated_at": self._wall_clock()}
        self._persist_job()

    def _note_rate_limit_wait(
        self, endpoint: str, seconds: float, reason: str | None
    ) -> None:
        """Persist (or clear) ``progress.waiting`` around one rate-limit wait.

        The pacer calls this for a 429 retry or a reset-header cooldown only,
        so ``waiting`` never appears for ordinary window pacing. ``resume_at``
        is a wall-clock epoch so the panel can count down without the job's
        monotonic clock.
        """
        job = self._job
        if job is None:
            return
        progress = dict(job.get("progress") or {})
        if reason is not None and seconds > 0:
            progress["waiting"] = {
                "reason": reason,
                "endpoint": endpoint,
                "retry_after": float(seconds),
                "resume_at": self._wall_clock() + float(seconds),
            }
        else:
            progress["waiting"] = None
        self._job = {**job, "progress": progress, "updated_at": self._wall_clock()}
        self._persist_job()

    # -- acquisition ----------------------------------------------------

    def _acquire(self) -> None:
        job = self._job or {}
        self._stage = "list"
        self.adapter.ensure_contract()
        structures_payload = self.provider.structures_payload
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
            # Losing eligibility is not evidence that previously listed objects
            # were deleted. Preserve their dated membership, as scoped Rescan
            # does, without claiming a fresh listing for inactive types.
            retained_types = [t for t in prior_types if t not in configured_types]

        # Retention accounting, per type: the objects kept from the previous
        # generation are exactly the scope this run did not enumerate, and a
        # status-only or unmapped type would otherwise vanish into one
        # anonymous aggregate line. Counts come from the prior generation's own
        # members, so no provider call is added.
        retained_member_counts = _retained_member_counts(prior, retained_types)

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

        # Hydrate: reuse cached content younger than the refresh TTL, re-read
        # stale or uncached content (ordinary) or the whole scoped type
        # (Rescan). Successful reads persist immediately. Each listing is read
        # in its own order, and the types are visited in the KTD7 priority
        # order (smallest read count first), so a Refresh always re-reads stale
        # objects in the same stable sequence.
        self._stage = "read"
        rescan = mode == "rescan"
        # The read target behind "176/227": the same predicate hydration uses,
        # so the count cannot drift from the reads the run actually performs.
        # The per-type counts also order the run, so one pass serves both.
        to_read_by_type = {
            type_key: sum(
                1
                for row in rows_by_type[type_key]
                if self._needs_read(_text(row.get("id")), type_key, rescan=rescan)
            )
            for type_key in listing_types
        }
        self._progress_field("to_read", int(sum(to_read_by_type.values())))
        publish_order = self._publish_order(listing_types, to_read_by_type)
        self._note_unchecked(publish_order)
        rows_merged_by_type: dict[str, list[dict[str, Any]]] = {}
        unreadable = False
        #: Cached reads whose stored ``structureId`` disagrees with the type key
        #: this run listed them under: a re-typed object, which the projection
        #: would otherwise map with another type's property mapping.
        retargeted = 0
        for type_key in publish_order:
            type_rows: list[dict[str, Any]] = []
            fresh_reads = 0
            for row in rows_by_type[type_key]:
                if self.cancel_event.is_set():
                    _raise_cancelled()
                object_id = _text(row.get("id"))
                try:
                    content, fresh = self._read_or_reuse(
                        object_id, type_key, rescan=rescan
                    )
                except RefreshCancelled:
                    raise
                except refresh_state.RefreshStateStorageError:
                    raise
                except capacities_adapter.CapacitiesRateLimited:
                    # A rate limit that outlived the per-run wait budget fails
                    # the run truthfully (Cause/Where), not one object.
                    raise
                except Exception:  # noqa: BLE001 — one unreadable object
                    unreadable = True
                    continue
                cached_type = _text(content.get("structureId"))
                if cached_type and cached_type != type_key:
                    retargeted += 1
                type_rows.append({**row, **content})
                if fresh:
                    # Only an actual provider read advances the read counter, so
                    # the job's progress stays truthful when content is reused.
                    fresh_reads += 1
                    read_at = self._wall_clock()
                    if self._read_started_at is None:
                        self._read_started_at = read_at
                    self._read_finished_at = read_at
                    self._progress(type_key=type_key, read=1)
            rows_merged_by_type[type_key] = type_rows
            self._completed_types = [*self._completed_types, type_key]
            finished = set(self._completed_types)
            remaining = [key for key in publish_order if key not in finished]
            self._note_unchecked(remaining)
            # R6/KTD6: publish this type's fresh rows now, keeping every type
            # still being read at its last published state. A type whose
            # content was entirely reused has nothing newer to show, and a type
            # whose read failed cannot be published as checked, so neither
            # installs an interim generation.
            if fresh_reads and remaining and not unreadable:
                self._publish_interim(
                    completed=self._completed_types,
                    unchecked=remaining,
                    listings=listings,
                    retained_types=retained_types,
                )
        merged: list[dict[str, Any]] = [
            row
            for type_key in listing_types
            for row in rows_merged_by_type.get(type_key, ())
        ]        # The read phase is over: publish its own numbers before the run moves
        # on to assembly, so a run that fails later still reports what it read.
        self._progress_field(
            "metrics", self._build_metrics(finished_at=self._wall_clock())
        )
        if unreadable:
            raise RefreshIncomplete(
                "One or more required Capacities objects could not be read; "
                "the previous complete result is preserved."
            )

        self._stage = "assemble"
        self._set_phase("evaluating")
        if self.cancel_event.is_set():
            _raise_cancelled()
        _result = self.adapter.items_for_day_from_objects(logical_day, merged)

        self._stage = "install"
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
        # The baseline for the schema-change notice is the contract stamped with
        # the currently installed generation, and it must be read before this
        # run replaces it. An unreadable baseline is simply no baseline: the
        # notice is advisory and must never influence the publication.
        try:
            previous_contract = self.store.load_structure_contract(self.scope_key)
        except Exception:  # noqa: BLE001 — advisory baseline read
            previous_contract = None
        previous_structures = (
            previous_contract.structures if previous_contract is not None else None
        )
        with guard:
            current = self.revision_supplier()
            if type(current) is not int or current != evidence.revision:
                raise RefreshStale()
            if self.space_id_supplier is not None:
                current_space = self.space_id_supplier()
                if (
                    not isinstance(current_space, str)
                    or current_space != self.space_id
                ):
                    raise RefreshStale()
            snapshot = self.store.install_generation(
                evidence,
                expected_generation=int((self._job or {}).get("generation") or 0),
                structures=structures_payload,
            )
        self._installed_at = self._wall_clock()
        job = self._job or {}
        self._job = {**job, "generation": snapshot.generation}
        # The projection warnings — including the bounded review surface for
        # under-evaluated rows — ride the successful finish; the publication
        # itself stays one complete generation with every candidate retained.
        warnings = list(_result.warnings)
        warnings.extend(
            retained_scope_warnings(
                retained_member_counts,
                structures_payload=structures_payload,
                mapped_structure_ids={m.structure_id for m in self.mappings},
            )
        )
        # The generation's own coverage, in the same vocabulary: a configured
        # type with no listed member, cached objects the generation does not
        # account for, and cached objects whose type moved since the last
        # Refresh. Counts come from the generation just installed and the run's
        # own reads, so no provider call is added and no object content is
        # printed.
        warnings.extend(
            generation_coverage_warnings(
                type_keys=configured_types,
                member_type_keys=[m.type_key for m in snapshot.members],
                cached_objects=self.store.object_count(),
                generation_objects=_generation_object_count(
                    self.store, snapshot, set(listing_types)
                ),
                retargeted=retargeted,
                structures_payload=structures_payload,
            )
        )
        self._finish("complete", "published", warnings)
        self._record_schema_change(
            previous=previous_structures,
            current=structures_payload,
            generation=snapshot.generation,
        )

    def _publish_interim(
        self,
        *,
        completed: Iterable[str],
        unchecked: Iterable[str],
        listings: Iterable[refresh_state.TypeListing],
        retained_types: Iterable[str],
    ) -> None:
        """Install an interim generation for the types finished so far (R6/R8).

        The finished types are published from this run's own complete listings;
        every type still being read keeps its last published members (the store
        refuses an install that would silently drop one), and the generation is
        marked partial with those types named as unchecked, so no surface can
        mistake it for a complete read. The final complete install supersedes it
        whole. A failure, cancellation, or staleness after this install leaves
        it in place; the job's warnings and publication block describe that.

        The freshness guard is the same one the complete install uses: an
        interim publication must never be stamped with a revision or source
        space the run was not built against.
        """
        if self.cancel_event.is_set():
            _raise_cancelled()
        job = self._job or {}
        finished = set(completed)
        unchecked = list(unchecked)
        try:
            prior = self.store.load_snapshot(self.scope_key)
        except refresh_state.RefreshStateError:
            raise
        prior_types = (
            {member.type_key for member in prior.members}
            if prior is not None
            else set()
        )
        # Every type that keeps published members but was not read this run must
        # be named as retained: the types still queued, plus the types this run
        # never intended to read (a scope that lost eligibility).
        retained = sorted(
            {
                key
                for key in (*retained_types, *unchecked)
                if key in prior_types and key not in finished
            }
        )
        evidence = refresh_state.SnapshotEvidence(
            scope_key=self.scope_key,
            revision=int(job.get("revision") or 0),
            required_types=tuple(sorted(finished)),
            listings=tuple(
                listing for listing in listings if listing.type_key in finished
            ),
            retained_types=tuple(retained),
            partial=True,
            unchecked_types=tuple(unchecked),
        )
        stage = self._stage
        self._stage = "install"
        guard = self.config_guard() if callable(self.config_guard) else nullcontext()
        with guard:
            current = self.revision_supplier()
            if type(current) is not int or current != evidence.revision:
                raise RefreshStale()
            if self.space_id_supplier is not None:
                current_space = self.space_id_supplier()
                if (
                    not isinstance(current_space, str)
                    or current_space != self.space_id
                ):
                    raise RefreshStale()
            snapshot = self.store.install_generation(
                evidence,
                expected_generation=int(job.get("generation") or 0),
            )
        self._stage = stage
        self._interim_count += 1
        self._installed_at = self._wall_clock()
        job = self._job or {}
        self._job = {
            **job,
            "generation": snapshot.generation,
            "updated_at": self._installed_at,
        }
        self._note_publication(unchecked=unchecked)
        logger.info(
            "Capacities refresh published an interim generation: "
            "types=%d unchecked=%d",
            len(finished),
            len(unchecked),
        )

    def _record_schema_change(
        self, *, previous: Any, current: Any, generation: int
    ) -> None:
        """Persist one content-free notice when the owner's schema changed.

        Called only after a generation published successfully. A ``None``
        ``previous`` is the first published contract (a silent baseline) and an
        unchanged payload records nothing, so an existing notice survives
        either case. A change that is already recorded (same target
        fingerprint) keeps its acknowledgement instead of reopening it.

        Failure isolation is the whole point of this seam: the notice is
        advisory, so any failure here — a raising diff, an unwritable sidecar —
        is swallowed and can never alter the outcome, warnings, published
        generation, or stored state of the Refresh that produced it.
        """
        if previous is None:
            return
        try:
            # Mapped structures and their recorded open status values are the
            # coordinator's own mapping records, so the diff can distinguish a
            # broken mapped structure from an unmapped one without a new read.
            mapped_structure_ids = [m.structure_id for m in self.mappings]
            open_status_values = {
                m.structure_id: tuple(sorted(m.open_status_values))
                for m in self.mappings
                if m.open_status_values
            }
            result = capacities_schema.diff(
                previous,
                current,
                mapped_structure_ids=mapped_structure_ids,
                open_status_values=open_status_values or None,
            )
            if result.severity == capacities_schema.SEVERITY_NONE:
                return
            stored = self.store.load_schema_change()
            if (
                isinstance(stored, Mapping)
                and stored.get("to_hash") == result.fingerprint_after
            ):
                return
            self.store.write_schema_change(
                {
                    "schema_version": capacities_schema.SCHEMA_VERSION,
                    "scope_key": self.scope_key,
                    "from_hash": result.fingerprint_before,
                    "to_hash": result.fingerprint_after,
                    "generation": int(generation),
                    "detected_at": _iso_utc(self._wall_clock()),
                    "severity": result.severity,
                    "diff": capacities_schema.to_dict(result),
                    "summary": capacities_schema.summary_text(result),
                    "acknowledged": False,
                    "acknowledged_at": None,
                }
            )
        except Exception:  # noqa: BLE001 — advisory notice, never the outcome
            logger.debug(
                "Capacities refresh: the schema-change notice was not recorded"
            )

    def _needs_read(self, object_id: str, type_key: str, *, rescan: bool = False) -> bool:
        """Whether one listed object must be read from the provider.

        ``True`` when a Rescan forces a full re-read, when the object is
        uncached, or when its cached content is at least the refresh TTL old.
        This single predicate backs both ``progress.to_read`` and
        :meth:`_read_or_reuse`, so the visible read target matches the reads.
        """
        if rescan:
            return True
        cached = self.store.get(object_id, type_key)
        if cached is None:
            return True
        return self._wall_clock() - cached.content_read_at >= self._content_ttl

    def _read_or_reuse(self, object_id: str, type_key: str, *, rescan: bool):
        """Return ``(content, fresh)`` for one required object.

        ``fresh`` is True only when this call actually read the provider. A
        Rescan always reads. An ordinary Refresh reuses cached content while it
        is younger than the refresh-mode TTL and re-reads older content through
        the paced provider, renewing ``content_read_at`` with the coordinator's
        wall clock (the same clock :func:`_coverage` compares against).
        """
        if not self._needs_read(object_id, type_key, rescan=rescan):
            cached = self.store.get(object_id, type_key)
            return cached.content, False
        content = self.provider.get_object(object_id)
        if not isinstance(content, dict) or not isinstance(content.get("properties"), dict):
            raise RefreshIncomplete(
                "A required Capacities object read was malformed; no complete "
                "result was published."
            )
        self.store.put(
            object_id, type_key, content, content_read_at=self._wall_clock()
        )
        return content, True

    def _remember_tag_titles(
        self, titles: dict[str, str], complete: bool
    ) -> None:
        """Persist the resolved tag-title map for the next offline replay.

        A complete catalog replaces this namespace's map, which is how a tag
        renamed in Capacities reaches the next replay; a partial catalog merges
        additively so a partial read never deletes a known id. ``remember_titles``
        never raises and returns ``False`` on any failure, so a cache problem
        cannot fail the run or add a second warning.
        """
        capacities_tag_titles.remember_titles(
            self.space_id,
            capacities_tag_titles.provider_base_url(self.provider),
            titles,
            merge=not complete,
        )


def _raise_cancelled() -> None:
    raise RefreshCancelled("cancelled")


def _progress_count(value: Any) -> int:
    """A non-negative count read back out of the job's ``progress`` block."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return max(0, int(value))


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
