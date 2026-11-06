"""capacities_mcp_authorize.py — one-time, operator-run OAuth consent for MCP.

The Capacities hosted MCP endpoint authenticates with OAuth, not with the REST
bearer token, and TDTB's server must never run an authorization flow of its own
(R9): no browser, no loopback listener, no dynamic client registration. This
module is the attended half of that split. The operator runs it by hand, once::

    cd app && .venv/bin/python -m capacities_mcp_authorize

It prints one authorization URL and waits for the browser redirect on a
``127.0.0.1`` loopback listener. When consent completes, ``mcp``'s
``OAuthClientProvider`` exchanges the code and writes custody through
:class:`capacities_mcp_auth.FileTokenStorage` — tokens, client registration, and
the absolute access-token expiry KTD3 needs. One cheap ``list_tools`` call then
proves the credential works without reading any object content.

Four properties are load-bearing.

* **Nothing secret is printed.** The authorization URL is printed verbatim
  because the operator must open exactly it; it carries the client id, the
  redirect URI, the scope, and the ``state`` the flow itself generated — never
  an authorization code, a token, or a client secret. Everything else this
  module writes is a fixed status word, a count, or an exception class name.
* **The library's own logging never reaches the output.** ``mcp==1.28.1`` logs
  ``logger.exception("OAuth flow error")`` (``oauth2.py:597``) and builds a
  ``OAuthTokenError`` message from the token-endpoint response body
  (``oauth2.py:411-414``), and its state-mismatch error interpolates both state
  values. :func:`install_secret_free_logging` therefore drops any record
  carrying ``exc_info`` and redacts token-shaped message text (KTD9).
* **Metadata is pre-set before the first request.** A fresh process never runs
  discovery, so the library refreshes against ``<origin>/token`` and gets a 404
  (U0). Pre-setting ``auth.context.oauth_metadata`` from the well-known document
  makes both a re-consent and any later refresh target the real ``/oauth/token``.
* **It refuses to run while a Refresh job holds its lock.** Capacities rotates
  the refresh token on every refresh, so a consent run that overlapped a live
  job could strand the credential (KTD2). The check is a read-only, non-blocking
  ``flock`` probe of the coordinator's existing job lock file.

Nothing in the server imports this module. It is a leaf: it imports
:mod:`capacities_mcp_auth`, and reads the job-lock location from
:mod:`capacities_builder` / :mod:`capacities_refresh` inside a function.

Exit codes: ``0`` consent completed and custody is usable, ``2`` the flow
failed, ``3`` a preflight refusal (job running, or the lock could not be
located) in which nothing was written.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import fcntl
import logging
import os
import re
import secrets
import sys
import threading
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, TextIO
from urllib.parse import parse_qs, urlparse

import httpx
from mcp.client.auth import OAuthClientProvider
from mcp.shared.auth import OAuthClientMetadata, OAuthMetadata
from pydantic import AnyUrl

import capacities_mcp_auth

#: The hosted Capacities MCP endpoint (streamable HTTP).
SERVER_URL = "https://api.capacities.io/mcp"
#: The authorization server metadata document. Its ``token_endpoint`` is
#: ``/oauth/token``; without it the library guesses ``/token`` and 404s (U0).
AUTHORIZATION_SERVER_METADATA_URL = (
    "https://api.capacities.io/.well-known/oauth-authorization-server"
)
#: The scope U0 consented with, plus ``offline_access`` so the grant is
#: refreshable. ``mcp==1.28.1`` may replace this with the scopes the server's
#: own metadata advertises (``get_client_metadata_scopes``); that override is
#: the "unless the server dictates otherwise" half of the contract.
REQUESTED_SCOPE = "mcp:read mcp:write offline_access"
#: Loopback only. ``0.0.0.0`` would expose an authorization code to the network.
LOOPBACK_HOST = "127.0.0.1"
CALLBACK_PATH = "/callback"
#: How long to wait for the operator to finish in the browser.
DEFAULT_CALLBACK_TIMEOUT = 300.0
#: Per-request timeout for the MCP session (the verification call).
DEFAULT_REQUEST_TIMEOUT = 20.0
CLIENT_NAME = "TDTB Capacities MCP reader"

EXIT_OK = 0
EXIT_FAILED = 2
EXIT_PREFLIGHT = 3

_PREFLIGHT_LOCK_RUNNING = "a Capacities refresh job is running"
_PREFLIGHT_LOCK_UNKNOWN = "the refresh job lock could not be located"
_NOT_GRANTED = "authorization was not granted"
_TIMED_OUT = "the loopback redirect did not arrive before the timeout"
_NO_CODE = "the loopback redirect carried no authorization code"
_STATE_MISMATCH = "the loopback redirect did not match this authorization request"
_NO_STATE = "the authorization URL carried no state"
_NO_LISTENER = "the loopback listener is not running"
_METADATA_UNAVAILABLE = "the authorization server metadata is unavailable"
_METADATA_UNUSABLE = "the authorization server metadata is not usable"

_REDACTED = "<redacted>"
#: Shapes that must never survive into a log line: bearer values, JWTs, long
#: opaque runs, and ``key=value`` forms of the OAuth field names.
_SECRET_PATTERNS = (
    re.compile(r"(?i)\bbearer\s+\S+"),
    re.compile(r"\beyJ[A-Za-z0-9._\-]{6,}"),
    re.compile(r"\b[A-Za-z0-9_\-~+/=]{40,}\b"),
    re.compile(
        r"(?i)\b(access_token|refresh_token|client_secret|code_verifier|"
        r"authorization_code|id_token|code|state)\b(\s*[=:]\s*)\S+"
    ),
)
_MAX_LOG_MESSAGE = 200


class AuthorizationError(Exception):
    """The attended consent did not complete.

    Every message is a module-level fixed literal: no machine path, no
    authorization code, no state value, no token, no response body, and no
    ``repr`` carrying either."""


# ---------------------------------------------------------------------------
# Log hygiene (KTD9)
# ---------------------------------------------------------------------------


class SecretFreeLogFilter(logging.Filter):
    """Drop library records that could carry a credential.

    ``mcp.client.auth`` logs a full traceback on any OAuth failure and
    interpolates the state pair into its mismatch message, so a record with
    ``exc_info`` is dropped outright and any other message is redacted when it
    matches a token shape. Attach this to handlers, not only to the logger:
    filters on a logger never see records propagated from its children.
    """

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: D102
        if record.exc_info or record.exc_text:
            return False
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 — an unformattable record is still a record
            message = ""
        if any(pattern.search(message) for pattern in _SECRET_PATTERNS):
            record.msg = _REDACTED
            record.args = ()
        elif len(message) > _MAX_LOG_MESSAGE:
            record.msg = message[:_MAX_LOG_MESSAGE]
            record.args = ()
        return True


class _SecretFreeHandler(logging.StreamHandler):
    """A quiet stderr handler that carries the filter by construction."""

    def __init__(self) -> None:
        super().__init__(sys.stderr)
        self.addFilter(SecretFreeLogFilter())
        self.setFormatter(logging.Formatter("[%(name)s] %(levelname)s %(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        # A host process (or a test harness) may have replaced and closed the
        # stream since this handler was built. Follow the current stderr then,
        # rather than failing to log.
        if self.stream is None or getattr(self.stream, "closed", False):
            self.stream = sys.stderr
        super().emit(record)


def install_secret_free_logging() -> None:
    """Keep the library's OAuth logging out of the operator's terminal.

    The ``mcp`` tree gets a filtered handler of its own and stops propagating,
    so no record it creates can reach a root handler unsanitised, and no root
    handler is modified. The level is held at ``WARNING`` because
    ``INFO``/``DEBUG`` is where the library narrates the flow. Idempotent.
    """
    secret_free = SecretFreeLogFilter()
    for name in ("mcp", "mcp.client.auth"):
        logger = logging.getLogger(name)
        logger.setLevel(logging.WARNING)
        if not any(isinstance(item, SecretFreeLogFilter) for item in logger.filters):
            logger.addFilter(secret_free)
    mcp_logger = logging.getLogger("mcp")
    if not any(isinstance(item, _SecretFreeHandler) for item in mcp_logger.handlers):
        mcp_logger.addHandler(_SecretFreeHandler())
    mcp_logger.propagate = False
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)


# ---------------------------------------------------------------------------
# Loopback redirect
# ---------------------------------------------------------------------------


class _CallbackRequestHandler(BaseHTTPRequestHandler):
    """The one HTTP endpoint the browser talks to.

    Deliberately silent: ``log_message`` is a no-op, because the request line
    carries the authorization code and the state.
    """

    server_version = "tdtb-capacities-mcp-authorize"
    sys_version = ""

    def do_GET(self) -> None:  # noqa: N802 — http.server API
        consent: LoopbackConsentServer = getattr(self.server, "consent")
        consent.handle_request(self.path, self._reply)

    def do_POST(self) -> None:  # noqa: N802 — refuse anything but a GET redirect
        self._reply(405, "Only the authorization redirect is accepted here.")

    def _reply(self, status: int, message: str) -> None:
        body = message.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        with contextlib.suppress(Exception):
            self.wfile.write(body)

    def log_message(self, *_args: Any) -> None:  # keep the code and state out of stderr
        return


class LoopbackConsentServer:
    """A one-shot ``127.0.0.1`` redirect listener for the authorization code.

    The listener binds an ephemeral port on :data:`LOOPBACK_HOST` only. It
    accepts exactly one meaningful redirect, checks the returned ``state``
    against the value the authorization URL carried, and hands
    ``(code, state)`` to the library. After that it stops listening, so the
    code exists in memory only until the token exchange consumes it.
    """

    def __init__(
        self,
        *,
        host: str = LOOPBACK_HOST,
        path: str = CALLBACK_PATH,
        timeout: float = DEFAULT_CALLBACK_TIMEOUT,
        out: TextIO | None = None,
    ) -> None:
        if host != LOOPBACK_HOST:
            raise ValueError(f"the loopback listener binds {LOOPBACK_HOST} only")
        self._host = host
        self._path = path if path.startswith("/") else f"/{path}"
        self._timeout = float(timeout)
        self._out = out if out is not None else sys.stdout
        self._ready = threading.Event()
        self._result: tuple[str, str | None] | None = None
        self._problem: str | None = None
        #: Set once one redirect has been accepted. A later one is answered
        #: without touching the captured code, so the value is handed over once.
        self._settled = False
        self._expected_state: str | None = None
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def __repr__(self) -> str:
        # No port, no path: a repr reaches logs and tracebacks.
        return "<LoopbackConsentServer>"

    # -- lifecycle --------------------------------------------------------

    @property
    def timeout(self) -> float:
        return self._timeout

    @property
    def redirect_uri(self) -> str:
        """The redirect URI registered for this run."""
        if self._httpd is None:
            raise AuthorizationError(_NO_LISTENER)
        port = int(self._httpd.server_address[1])
        return f"http://{self._host}:{port}{self._path}"

    @property
    def address(self) -> tuple[str, int]:
        """The bound ``(host, port)``. Tests assert the host is loopback."""
        if self._httpd is None:
            raise AuthorizationError(_NO_LISTENER)
        return str(self._httpd.server_address[0]), int(self._httpd.server_address[1])

    def start(self) -> str:
        """Bind the loopback listener and return its redirect URI."""
        if self._httpd is not None:
            return self.redirect_uri
        httpd = ThreadingHTTPServer((self._host, 0), _CallbackRequestHandler)
        httpd.daemon_threads = True
        httpd.consent = self  # type: ignore[attr-defined]
        self._httpd = httpd
        thread = threading.Thread(
            target=httpd.serve_forever, name="capacities-mcp-consent", daemon=True
        )
        thread.start()
        self._thread = thread
        return self.redirect_uri

    def close(self) -> None:
        httpd, self._httpd = self._httpd, None
        if httpd is not None:
            with contextlib.suppress(Exception):
                httpd.shutdown()
            with contextlib.suppress(Exception):
                httpd.server_close()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=2.0)

    def __enter__(self) -> LoopbackConsentServer:
        self.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # -- handler surface --------------------------------------------------

    async def redirect_handler(self, url: str) -> None:
        """Record the expected ``state`` and print the URL. Opens no browser."""
        self._expected_state = _state_from_authorization_url(url)
        _emit(self._out, "")
        _emit(self._out, "Open this URL in a browser on this machine:")
        _emit(self._out, "")
        # Deliberate: the operator must open exactly this URL. It carries the
        # client id, redirect URI, scope and state — never a code or a token.
        _emit(self._out, url)
        _emit(self._out, "")

    async def callback_handler(self) -> tuple[str, str | None]:
        """Wait for the redirect off the event loop and return ``(code, state)``."""
        return await asyncio.to_thread(self.wait_for_callback)

    def wait_for_callback(self) -> tuple[str, str | None]:
        """Block until the redirect arrives, then return ``(code, state)``.

        Raises :class:`AuthorizationError` on a timeout, a provider error, a
        missing code, or a ``state`` that does not match the authorization URL.
        The captured code is cleared on the way out, so it is handed to the
        library exactly once."""
        if not self._ready.wait(self._timeout):
            raise AuthorizationError(_TIMED_OUT)
        if self._problem is not None:
            raise AuthorizationError(self._problem)
        result, self._result = self._result, None
        if result is None:
            raise AuthorizationError(_NO_CODE)
        code, state = result
        expected = self._expected_state
        if expected is None:
            raise AuthorizationError(_NO_STATE)
        if state is None or not secrets.compare_digest(state, expected):
            raise AuthorizationError(_STATE_MISMATCH)
        return code, state

    def handle_request(
        self, target: str, reply: Callable[[int, str], None]
    ) -> None:
        """Handle one redirect request path.

        Split out from the HTTP handler so the state check is testable without
        a socket. The first redirect that carries a code or an error wins;
        anything later is answered without touching the captured value.
        """
        parsed = urlparse(target)
        if parsed.path != self._path:
            reply(404, "Not the authorization redirect.")
            return
        if self._settled:
            reply(200, "This authorization redirect was already handled.")
            return
        params = parse_qs(parsed.query)
        if params.get("error"):
            # The provider's error code is not echoed: it is provider text.
            self._settled = True
            self._problem = _NOT_GRANTED
            reply(200, "Authorization was not granted. You can close this tab.")
            self._ready.set()
            return
        codes = params.get("code") or []
        if not codes:
            reply(400, "No authorization code in this redirect.")
            return
        states = params.get("state") or []
        self._settled = True
        self._result = (codes[0], states[0] if states else None)
        reply(200, "Consent captured. You can close this tab.")
        self._ready.set()


def _state_from_authorization_url(url: str) -> str:
    """The ``state`` parameter of the authorization URL the library built."""
    values = parse_qs(urlparse(url).query).get("state") or []
    state = values[0] if values else ""
    if not state:
        raise AuthorizationError(_NO_STATE)
    return state


# ---------------------------------------------------------------------------
# Authorization server metadata
# ---------------------------------------------------------------------------


def _fetch_authorization_server_metadata() -> Mapping[str, Any]:
    """GET the public well-known document. Contains no credential."""
    try:
        response = httpx.get(
            AUTHORIZATION_SERVER_METADATA_URL, timeout=15.0, follow_redirects=True
        )
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:  # noqa: BLE001 — the message is fixed, the cause is not shown
        raise AuthorizationError(_METADATA_UNAVAILABLE) from exc
    if not isinstance(payload, Mapping):
        raise AuthorizationError(_METADATA_UNUSABLE)
    return payload


def load_authorization_server_metadata(
    loader: Callable[[], Mapping[str, Any]] | None = None,
) -> OAuthMetadata:
    """The authorization server metadata, pre-set before the first request.

    Without it a fresh process refreshes against ``<origin>/token`` and 404s
    (U0). ``loader`` is the test seam; the default performs one public GET."""
    try:
        raw = (loader or _fetch_authorization_server_metadata)()
    except AuthorizationError:
        raise
    except Exception as exc:  # noqa: BLE001 — the cause is never rendered
        raise AuthorizationError(_METADATA_UNAVAILABLE) from exc
    try:
        return OAuthMetadata.model_validate(raw)
    except Exception as exc:  # noqa: BLE001
        raise AuthorizationError(_METADATA_UNUSABLE) from exc


# ---------------------------------------------------------------------------
# Consent flow
# ---------------------------------------------------------------------------


def client_metadata(redirect_uri: str) -> OAuthClientMetadata:
    """The client registration this run asks for.

    ``token_endpoint_auth_method="none"`` is the public-client convention for a
    loopback redirect; the registration response replaces it in ``client_info``
    when the server issues a secret."""
    return OAuthClientMetadata(
        client_name=CLIENT_NAME,
        redirect_uris=[AnyUrl(redirect_uri)],
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
        token_endpoint_auth_method="none",
        scope=REQUESTED_SCOPE,
    )


def build_auth_provider(
    storage: capacities_mcp_auth.FileTokenStorage,
    callback: LoopbackConsentServer,
    *,
    redirect_uri: str,
    timeout: float = DEFAULT_CALLBACK_TIMEOUT,
) -> OAuthClientProvider:
    """The attended provider: real redirect and callback handlers, no server use."""
    return OAuthClientProvider(
        SERVER_URL,
        client_metadata(redirect_uri),
        storage,
        redirect_handler=callback.redirect_handler,
        callback_handler=callback.callback_handler,
        timeout=timeout,
    )


@contextlib.asynccontextmanager
async def open_mcp_session(
    auth: OAuthClientProvider, *, timeout: float = DEFAULT_REQUEST_TIMEOUT
) -> AsyncIterator[Any]:
    """One streamable-HTTP MCP session carrying ``auth``. The only network path."""
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    effective = max(10.0, float(timeout))
    http_client = httpx.AsyncClient(
        auth=auth,
        timeout=httpx.Timeout(effective, read=effective),
        follow_redirects=True,
    )
    async with contextlib.AsyncExitStack() as stack:
        await stack.enter_async_context(http_client)
        streams = await stack.enter_async_context(
            streamable_http_client(
                SERVER_URL, http_client=http_client, terminate_on_close=True
            )
        )
        read_stream, write_stream, _session_id = streams
        session = await stack.enter_async_context(
            ClientSession(
                read_stream,
                write_stream,
                read_timeout_seconds=timedelta(seconds=effective),
            )
        )
        await session.initialize()
        yield session


def discard_previous_custody(storage: capacities_mcp_auth.FileTokenStorage) -> None:
    """Remove a previous custody document so consent starts from a clean slate."""
    with contextlib.suppress(AttributeError, FileNotFoundError):
        storage.path.unlink()


async def run_consent(
    storage: capacities_mcp_auth.FileTokenStorage,
    callback: LoopbackConsentServer,
    *,
    metadata_loader: Callable[[], Mapping[str, Any]] | None = None,
    session_opener: Callable[..., Any] | None = None,
    timeout: float = DEFAULT_REQUEST_TIMEOUT,
) -> int:
    """Complete consent, write custody, and return the tool count.

    ``session_opener`` and ``metadata_loader`` are the test seams; both default
    to the real network path. Nothing here prints: the caller reports counts.
    """
    auth = build_auth_provider(
        storage,
        callback,
        redirect_uri=callback.redirect_uri,
        timeout=callback.timeout,
    )
    auth.context.oauth_metadata = load_authorization_server_metadata(metadata_loader)
    opener = session_opener if session_opener is not None else open_mcp_session
    async with opener(auth, timeout=timeout) as session:
        result = await session.list_tools()
    tools = getattr(result, "tools", None) or ()
    return len(tools)


# ---------------------------------------------------------------------------
# Preflight: the refresh job lock
# ---------------------------------------------------------------------------


def refresh_job_lock_path() -> Path | None:
    """The coordinator's single-flight job lock, or ``None`` if unresolvable.

    Read-only: the path is derived from the same root and the same file names
    the coordinator uses (``capacities_refresh.JOB_DIRNAME`` and
    ``JOB_LOCK_FILENAME`` under ``capacities_builder.refresh_state_root()``),
    without constructing a coordinator or taking the lock."""
    try:
        import capacities_builder
        import capacities_refresh
    except Exception:  # noqa: BLE001 — an unimportable app is a refusal, not a crash
        return None
    try:
        root = capacities_builder.refresh_state_root()
        return (
            Path(root)
            / capacities_refresh.JOB_DIRNAME
            / capacities_refresh.JOB_LOCK_FILENAME
        )
    except Exception:  # noqa: BLE001
        return None


def job_lock_is_held(path: str | Path) -> bool:
    """Whether a Refresh job currently holds ``path``.

    A read-only, non-blocking ``flock`` probe on a read-only descriptor: it
    never creates the file, never truncates it, and releases immediately when
    free. A missing file means no job has ever taken the lock. A file that
    cannot be opened reports held, so consent never races a live job by
    accident."""
    target = Path(path)
    try:
        descriptor = os.open(target, os.O_RDONLY)
    except FileNotFoundError:
        return False
    except OSError:
        return True
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return True
        with contextlib.suppress(OSError):
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        return False
    finally:
        with contextlib.suppress(OSError):
            os.close(descriptor)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="capacities_mcp_authorize",
        description=(
            "One-time, operator-run OAuth consent for the Capacities MCP "
            "reader. Prints an authorization URL, waits for the loopback "
            "redirect, and writes machine-local custody. Run it by hand."
        ),
    )
    parser.add_argument(
        "--custody",
        default=None,
        help="custody JSON path (default: the machine-local TDTB custody file)",
    )
    parser.add_argument(
        "--callback-timeout",
        type=float,
        default=DEFAULT_CALLBACK_TIMEOUT,
        help=f"seconds to wait for the browser redirect ({DEFAULT_CALLBACK_TIMEOUT:.0f})",
    )
    parser.add_argument(
        "--request-timeout",
        type=float,
        default=DEFAULT_REQUEST_TIMEOUT,
        help=f"per-request timeout for the verification call ({DEFAULT_REQUEST_TIMEOUT:.0f})",
    )
    return parser.parse_args(argv)


def _emit(stream: TextIO, text: str) -> None:
    stream.write(text + "\n")
    with contextlib.suppress(Exception):
        stream.flush()


def main(
    argv: list[str] | None = None,
    *,
    storage: capacities_mcp_auth.FileTokenStorage | None = None,
    flow: Callable[..., Awaitable[int]] | None = None,
    metadata_loader: Callable[[], Mapping[str, Any]] | None = None,
    lock_path_resolver: Callable[[], Path | None] | None = None,
    out: TextIO | None = None,
) -> int:
    """Run one attended consent. Returns :data:`EXIT_OK`, ``2``, or ``3``.

    ``storage``, ``flow``, ``metadata_loader``, ``lock_path_resolver`` and
    ``out`` are the test seams; production passes none of them."""
    args = parse_args(argv)
    stream = out if out is not None else sys.stdout
    install_secret_free_logging()
    _emit(
        stream,
        "capacities-mcp-authorize: one-time operator consent for the "
        "Capacities MCP reader",
    )

    resolver = lock_path_resolver or refresh_job_lock_path
    try:
        lock_path = resolver()
    except Exception:  # noqa: BLE001 — an unresolvable lock is a refusal
        lock_path = None
    if lock_path is None:
        _emit(stream, f"preflight: {_PREFLIGHT_LOCK_UNKNOWN}")
        _emit(stream, "status: refused")
        return EXIT_PREFLIGHT
    if job_lock_is_held(lock_path):
        _emit(stream, f"preflight: {_PREFLIGHT_LOCK_RUNNING}")
        _emit(stream, "status: refused")
        return EXIT_PREFLIGHT
    _emit(stream, "preflight: ok")

    custody = storage if storage is not None else capacities_mcp_auth.FileTokenStorage(
        args.custody
    )
    # A re-consent registers a fresh client: stored client_info pins the old
    # ephemeral redirect URI, which the authorization server would reject.
    discard_previous_custody(custody)
    callback = LoopbackConsentServer(timeout=args.callback_timeout, out=stream)
    try:
        callback.start()
    except OSError:
        _emit(stream, f"loopback: could not bind {LOOPBACK_HOST}")
        _emit(stream, "status: failed")
        return EXIT_FAILED
    _emit(stream, f"loopback: listening on {LOOPBACK_HOST}")

    runner = flow if flow is not None else run_consent
    try:
        tool_count = asyncio.run(
            runner(
                custody,
                callback,
                metadata_loader=metadata_loader,
                timeout=args.request_timeout,
            )
        )
    except AuthorizationError as exc:
        # Every AuthorizationError message is a fixed literal: no code, no
        # state, no token, no path.
        _emit(stream, f"consent: {exc}")
        _emit(stream, "status: failed")
        return EXIT_FAILED
    except Exception as exc:  # noqa: BLE001 — class name only, never the message
        _emit(stream, f"consent: failed ({type(exc).__name__})")
        _emit(stream, "status: failed")
        return EXIT_FAILED
    finally:
        callback.close()

    if not custody.is_configured():
        _emit(stream, "custody: incomplete")
        _emit(stream, "status: failed")
        return EXIT_FAILED
    _emit(stream, "custody: written")
    if int(tool_count) <= 0:
        _emit(stream, "verification: no tools listed")
        _emit(stream, "status: failed")
        return EXIT_FAILED
    _emit(stream, f"tools: {int(tool_count)}")
    _emit(stream, "status: ok")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
