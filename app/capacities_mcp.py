"""capacities_mcp.py — Capacities content reads over the hosted MCP endpoint.

The hosted Capacities MCP endpoint (``https://api.capacities.io/mcp``) serves
the same object content as REST ``GET /object`` on an OAuth credential with its
own request budget, so a refresh can read content from two independent sources
at once. This module is the MCP half of that: a **synchronous, content-read
only** provider that returns REST-shaped payloads, so nothing downstream has to
know which source answered a read.

Four properties are load-bearing.

* **Synchronous outside, asynchronous inside.** ``OAuthClientProvider`` is an
  ``httpx.AsyncAuth`` and the transport is an async context manager whose task
  group must be entered and exited in one task, while the refresh coordinator is
  synchronous and threaded. Each run therefore gets one private blocking event
  loop (:func:`anyio.from_thread.start_blocking_portal`) and the session is
  driven through :meth:`anyio.from_thread.BlockingPortal.
  wrap_async_context_manager`. :meth:`McpProvider.close` stops that loop and
  joins its thread, so no worker outlives a run.
* **Headless-safe construction.** ``mcp==1.28.1`` restores stored tokens after a
  restart but never restores ``context.token_expiry_time``, so an expired access
  token looks valid, the server answers 401, and the library starts a *full*
  authorization flow instead of refreshing. Two pre-sets prevent that: the
  absolute expiry from :class:`capacities_mcp_auth.FileTokenStorage`, and the
  authorization-server metadata from the public well-known document (without it
  the library refreshes against ``<origin>/token`` and gets a 404 — the real
  endpoint is ``/oauth/token``). Both are applied before the first request, and
  the provider refuses to start without stored client info, so no request can
  trigger dynamic client registration or reach for a browser (R9).
* **Normalization is exactly three rules** (:func:`normalize_mcp_content`).
  MCP's payload is REST ``GET /object`` plus extras; ``structureId`` is stamped
  from the REST listing row that queued the read (MCP only carries
  ``objectTypeId``, whose display name is not identity — ``RootTask`` reports
  ``Task``), top-level ``spaceId`` is dropped (REST omits it and
  ``capacities_adapter._fingerprint`` hashes the *absence*), and per-element
  ``title`` is stripped from entity-reference lists inside ``properties`` (REST
  sends ``{id}``). Top-level ``title`` is kept on purpose: the adapter's title
  projection falls back to it when the typed title property is empty
  (``capacities_adapter.py`` ``_project_object``). Everything else MCP adds
  (``content``, ``createdAt``, ``lastUpdated``, ``objectType``, ``objectTypeId``,
  ``tags``, ``collections``, ``blocks``) is dropped, because REST does not carry
  it either and admission must not see a field the REST path never had.
* **Throttling is source loss, never a read retry.** A transport 429 destroys
  the ``mcp==1.28.1`` session: ``raise_for_status`` inside the transport's task
  group cancels the in-flight call and the real error surfaces when the session
  context is exited. Every failure is therefore reported as
  :class:`capacities_adapter.CapacitiesReadSourceUnavailable`, the session is
  discarded, and the next attempt rebuilds both the loop and the session. The
  exception carries an optional ``retry_after`` read from ``Retry-After`` and no
  message, body, token, or provider text (R8, KTD9). Library logging is filtered
  by :func:`install_secret_free_logging` for the same reason.

Nothing here lists objects or writes one: listing stays on REST (R2) and
``patch_object`` is never routed here. ``capacities_mcp_authorize`` — the
operator-run consent CLI — is never imported by this module or by the server,
and this provider installs ``redirect_handler=None``/``callback_handler=None``,
so no request it builds could complete a browser consent.
"""
from __future__ import annotations

import contextlib
import functools
import json
import logging
import re
import sys
from collections.abc import Callable, Iterator, Mapping
from contextlib import AbstractAsyncContextManager
from datetime import timedelta
from typing import Any, Protocol

import httpx
from anyio.from_thread import start_blocking_portal
from mcp.client.auth import OAuthClientProvider
from mcp.shared.auth import OAuthClientMetadata, OAuthMetadata
from mcp.types import TextContent
from pydantic import AnyUrl

import capacities_adapter
from capacities_mcp_auth import CapacitiesMcpAuthError, FileTokenStorage

#: The hosted Capacities MCP endpoint (streamable HTTP).
DEFAULT_SERVER_URL = "https://api.capacities.io/mcp"
#: The authorization server metadata document. Its ``token_endpoint`` is
#: ``/oauth/token``; without it the library guesses ``/token`` and 404s (U0).
AUTHORIZATION_SERVER_METADATA_URL = (
    "https://api.capacities.io/.well-known/oauth-authorization-server"
)
#: The content tool is resolved by suffix, not by literal name: the harness
#: prefixes tools with ``capacities_`` while the server's own names are bare.
CONTENT_TOOL_SUFFIX = "getObjectContent"
#: The one argument the content tool takes.
CONTENT_TOOL_ARGUMENT = "id"
#: Placeholder redirect URI. This provider never runs a consent flow; the value
#: exists only so ``OAuthClientMetadata`` validates (a refresh sends no
#: ``redirect_uri``). It is the same loopback origin the authorize CLI uses.
REDIRECT_URI = "http://127.0.0.1:8765/callback"
#: The scope the stored grant was consented with. A refresh does not re-send it;
#: it is present so the client metadata matches the recorded registration.
REQUESTED_SCOPE = "mcp:read mcp:write offline_access"
CLIENT_NAME = "TDTB Capacities MCP reader"
#: Name of the private event-loop thread, so a test (or an operator) can prove
#: :meth:`McpProvider.close` joined it.
PORTAL_THREAD_NAME = "tdtb-capacities-mcp"

DEFAULT_TIMEOUT = 15.0
METADATA_TIMEOUT = 15.0

#: Property sub-keys whose value is a list of entity references. REST sends
#: ``{id}``; MCP sends ``{id, title}``.
ENTITY_REFERENCE_KEYS = ("entity", "entities")

#: Top-level MCP keys that survive normalization because the adapter reads
#: them. ``title`` is the title-property fallback; ``tags``/``blocks`` are
#: REST-only in the measured payloads, so they are never present here and stay
#: unlisted rather than fabricated.
PASSTHROUGH_KEYS = ("title",)

_REDACTED = "<redacted>"
#: Shapes that must never survive into a log line: bearer values, JWTs, long
#: opaque runs, and ``key=value`` forms of the OAuth field names. A deliberate
#: minimal duplicate of the authorize CLI's set, because the server must never
#: import that module.
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

#: Fixed, code-authored diagnostics. None of them interpolates provider text,
#: a token, a header, or a response body.
_UNUSABLE_PAYLOAD = "capacities MCP content payload is not usable"
_TOOL_ERROR = "capacities MCP content tool reported an error"
_MISSING_IDENTITY = "capacities MCP content read needs an object id and a structure id"


# ---------------------------------------------------------------------------
# Log hygiene (KTD9)
# ---------------------------------------------------------------------------


class SecretFreeLogFilter(logging.Filter):
    """Drop library records that could carry a credential.

    ``mcp.client.auth`` logs ``Token refresh failed: <status>``, logs
    ``logger.exception("OAuth flow error")``, and builds an ``OAuthTokenError``
    message from the token-endpoint response body. A record carrying
    ``exc_info`` is therefore dropped outright, and any other message is
    redacted when it matches a credential shape.
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
        # stream since this handler was built. Follow the current stderr then.
        if self.stream is None or getattr(self.stream, "closed", False):
            self.stream = sys.stderr
        super().emit(record)


def install_secret_free_logging() -> None:
    """Keep the library's OAuth logging out of any credential-bearing form.

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
# Result parsing and normalization (KTD5)
# ---------------------------------------------------------------------------


def _text(value: Any) -> str | None:
    """A trimmed string, or ``None`` for anything else."""
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def without_entity_titles(properties: Mapping[str, Any]) -> dict[str, Any]:
    """Rule (iii): strip per-element ``title`` from entity-reference lists.

    REST sends ``{"id": ...}``; MCP sends ``{"id": ..., "title": ...}``. Only
    the ``entity``/``entities`` sub-lists are touched, element order and every
    other key are preserved, and no other property sub-key is rewritten.
    """
    out: dict[str, Any] = {}
    for key, prop in properties.items():
        if not isinstance(prop, dict):
            out[key] = prop
            continue
        clone = dict(prop)
        for sub in ENTITY_REFERENCE_KEYS:
            value = prop.get(sub)
            if not isinstance(value, list):
                continue
            clone[sub] = [
                {k: v for k, v in item.items() if k != "title"}
                if isinstance(item, dict)
                else item
                for item in value
            ]
        out[key] = clone
    return out


def payload_from_result(result: Any) -> dict[str, Any]:
    """The content dict of one ``call_tool`` result, or a contract error.

    ``structuredContent`` is the live delivery; a single ``TextContent`` block
    carrying a JSON object is accepted as the fallback. ``isError``, a
    non-object payload, a non-JSON text block, and a multi-part result are all
    contract errors: deterministic, and never retried.
    """
    if getattr(result, "isError", False):
        raise capacities_adapter.CapacitiesContractError(_TOOL_ERROR)
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict):
        return structured
    blocks = list(getattr(result, "content", None) or [])
    text_blocks = [block for block in blocks if isinstance(block, TextContent)]
    if len(blocks) != 1 or len(text_blocks) != 1:
        raise capacities_adapter.CapacitiesContractError(_UNUSABLE_PAYLOAD)
    try:
        parsed = json.loads(text_blocks[0].text)
    except (TypeError, ValueError):
        raise capacities_adapter.CapacitiesContractError(_UNUSABLE_PAYLOAD) from None
    if not isinstance(parsed, dict):
        raise capacities_adapter.CapacitiesContractError(_UNUSABLE_PAYLOAD)
    return parsed


def normalize_mcp_content(
    payload: Any, *, structure_id: str, space_id: str
) -> dict[str, Any]:
    """Reshape one MCP content payload into the REST ``GET /object`` shape.

    Exactly three rules, each justified by TDTB code rather than by taste:

    (i) ``structureId`` is stamped from the REST listing row that requested the
    read. MCP carries ``objectTypeId`` instead, and its ``objectType`` display
    name is not identity (``RootTask`` reports ``Task``), so nothing here keys
    off a type name.
    (ii) a present top-level ``spaceId`` must equal the configured space and is
    then dropped. REST omits it, and ``capacities_adapter._fingerprint`` hashes
    the absence, so keeping it would change the completion-write guard. A
    different space is a contract error, not a row (AE4).
    (iii) entity-reference lists inside ``properties`` lose their per-element
    ``title`` (see :func:`without_entity_titles`).

    Top-level ``title`` is preserved because the adapter's title projection
    falls back to it when the typed title property is empty. Every other MCP-only
    key is dropped: REST does not carry it, so admission, the exclusion matcher,
    and the rules predicate record must not see it.
    """
    if not isinstance(payload, dict):
        raise capacities_adapter.CapacitiesContractError(_UNUSABLE_PAYLOAD)
    structure = _text(structure_id)
    if structure is None:
        # Structural, not provider, so a fixed message and no payload detail.
        raise capacities_adapter.CapacitiesContractError(_MISSING_IDENTITY)
    sent_space = payload.get("spaceId")
    if sent_space is not None and _text(sent_space) != space_id:
        raise capacities_adapter.CapacitiesContractError(
            "capacities MCP content belongs to another space"
        )
    properties = payload.get("properties")
    if not isinstance(properties, dict):
        raise capacities_adapter.CapacitiesContractError(_UNUSABLE_PAYLOAD)
    normalized: dict[str, Any] = {
        "id": payload.get("id"),
        "structureId": structure,
        "properties": without_entity_titles(properties),
    }
    for key in PASSTHROUGH_KEYS:
        if key in payload:
            normalized[key] = payload[key]
    return normalized


# ---------------------------------------------------------------------------
# Failure classification
# ---------------------------------------------------------------------------


def _walk_exceptions(exc: BaseException | None) -> Iterator[BaseException]:
    """Every exception reachable from ``exc``: groups, causes, and contexts.

    An ``anyio`` task group reports through a ``BaseExceptionGroup``, while an
    ``httpx`` auth flow chains through ``__cause__``; both must be inspectable
    without ever rendering their text.
    """
    if exc is None:
        return
    seen: set[int] = set()
    stack: list[BaseException] = [exc]
    while stack:
        current = stack.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        yield current
        nested = getattr(current, "exceptions", None)
        if isinstance(nested, (list, tuple)):
            stack.extend(item for item in nested if isinstance(item, BaseException))
        for linked in (current.__cause__, current.__context__):
            if isinstance(linked, BaseException):
                stack.append(linked)


def http_status_of(exc: BaseException | None) -> int | None:
    """The first HTTP status reachable from ``exc``, or ``None``."""
    for item in _walk_exceptions(exc):
        if isinstance(item, httpx.HTTPStatusError):
            return item.response.status_code
        status = getattr(item, "status_code", None)
        if isinstance(status, int) and not isinstance(status, bool):
            return status
    return None


def retry_after_of(exc: BaseException | None) -> float | None:
    """A numeric ``Retry-After`` from the first response reachable from ``exc``.

    Pacing metadata only. An HTTP-date form is not parsed: returning ``None``
    leaves the cooldown decision to the caller's own policy.
    """
    for item in _walk_exceptions(exc):
        response = getattr(item, "response", None)
        headers = getattr(response, "headers", None)
        if headers is None:
            continue
        raw = headers.get("Retry-After") or headers.get("retry-after")
        if not isinstance(raw, str):
            continue
        try:
            value = float(raw.strip())
        except ValueError:
            continue
        if value >= 0:
            return value
    return None


def unavailable_from(
    *causes: BaseException | None,
) -> "capacities_adapter.CapacitiesReadSourceUnavailable":
    """The source-loss exception for these failures, with no message body.

    ``retry_after`` is set only when a reachable response reports HTTP 429, and
    the message stays empty: the caller's fallback warning is fixed text, and no
    library message (which may quote a response body) is ever forwarded.
    """
    retry_after: float | None = None
    for cause in causes:
        if http_status_of(cause) == 429:
            retry_after = retry_after_of(cause)
            break
    return capacities_adapter.CapacitiesReadSourceUnavailable(
        retry_after=retry_after
    )


# ---------------------------------------------------------------------------
# Session plumbing
# ---------------------------------------------------------------------------


class McpSession(Protocol):
    """The two ``ClientSession`` calls this provider makes, for the test seam."""

    async def list_tools(self) -> Any: ...

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        read_timeout_seconds: timedelta | None = None,
    ) -> Any: ...


#: Opens one MCP session over the given auth. Tests inject a fake so no network
#: is touched.
SessionFactory = Callable[[OAuthClientProvider], AbstractAsyncContextManager[McpSession]]


@contextlib.asynccontextmanager
async def open_streamable_http_session(
    auth: OAuthClientProvider,
    *,
    server_url: str = DEFAULT_SERVER_URL,
    timeout: float = DEFAULT_TIMEOUT,
) -> Any:
    """Open, initialize, and yield one streamable-HTTP MCP session.

    A transport failure raises out of the task group and therefore surfaces when
    the caller exits this context — which is why the provider always exits the
    context, even on a failed call, and inspects that error for a 429.
    """
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    limit = max(10.0, float(timeout))
    http_client = httpx.AsyncClient(
        auth=auth,
        timeout=httpx.Timeout(limit, read=limit),
        follow_redirects=True,
    )
    async with http_client:
        streams = streamable_http_client(
            server_url, http_client=http_client, terminate_on_close=True
        )
        async with streams as (read_stream, write_stream, _session_id):
            session = ClientSession(
                read_stream,
                write_stream,
                read_timeout_seconds=timedelta(seconds=limit),
            )
            async with session:
                await session.initialize()
                yield session


class _RecordingSessionContext:
    """Records the session context's exit error, then re-raises it.

    The real cause of a destroyed session (``raise_for_status`` inside the
    transport's task group) surfaces when the session context is exited. An
    ``anyio`` portal whose task group was just cancelled may fail to deliver
    that error through the synchronous wrapper, so it is captured here first —
    which is what makes a 429's ``Retry-After`` observable at all.
    """

    def __init__(self, context_manager: AbstractAsyncContextManager[McpSession]) -> None:
        self._context_manager = context_manager
        self.exit_error: BaseException | None = None

    async def __aenter__(self) -> McpSession:
        return await self._context_manager.__aenter__()

    async def __aexit__(self, *exc_info: Any) -> Any:
        try:
            return await self._context_manager.__aexit__(*exc_info)
        except (KeyboardInterrupt, SystemExit):  # pragma: no cover
            raise
        except BaseException as exc:  # noqa: BLE001 — recorded, then re-raised
            self.exit_error = exc
            raise


def _load_authorization_server_metadata() -> Mapping[str, Any]:
    """One public GET of the authorization-server metadata document."""
    response = httpx.get(
        AUTHORIZATION_SERVER_METADATA_URL,
        timeout=METADATA_TIMEOUT,
        follow_redirects=True,
    )
    response.raise_for_status()
    return response.json()


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------


class McpProvider:
    """Synchronous ``get_object`` over MCP, returning REST-shaped content.

    One instance serves one refresh job: :meth:`begin_run` validates custody and
    :meth:`end_run` (or :meth:`close`) discards the session and joins the private
    event-loop thread, so nothing survives a run, a cancel, or a failure.

    ``session_factory`` and ``metadata_loader`` are the test seams; the defaults
    are the streamable-HTTP session and one public metadata GET.
    """

    def __init__(
        self,
        space_id: str,
        storage: FileTokenStorage,
        *,
        server_url: str = DEFAULT_SERVER_URL,
        session_factory: SessionFactory | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        metadata_loader: Callable[[], Mapping[str, Any]] | None = None,
    ) -> None:
        space = _text(space_id)
        if space is None:
            raise ValueError("a Capacities space id is required")
        self._space_id = space
        self._storage = storage
        self._server_url = server_url
        self._timeout = max(5.0, float(timeout))
        self._session_factory: SessionFactory = session_factory or (
            functools.partial(
                open_streamable_http_session,
                server_url=server_url,
                timeout=self._timeout,
            )
        )
        self._metadata_loader = metadata_loader or _load_authorization_server_metadata
        self._metadata: OAuthMetadata | None = None
        #: The recorded metadata failure. The document is fetched once per
        #: provider: a transient 5xx must not be re-fetched on every read.
        self._metadata_error: BaseException | None = None
        self._portal_cm: Any = None
        self._portal: Any = None
        self._sync_cm: Any = None
        self._session_context: _RecordingSessionContext | None = None
        self._session: McpSession | None = None
        self._auth: OAuthClientProvider | None = None
        self._tool_name: str | None = None

    def __repr__(self) -> str:
        # Deliberately carries no path and no credential.
        return "<McpProvider>"

    # ------------------------------------------------------------------
    # Run lifecycle
    # ------------------------------------------------------------------

    def begin_run(self) -> None:
        """Start one run: drop any leftover state, then prove custody is usable.

        Raises :class:`capacities_adapter.CapacitiesReadSourceUnavailable` when
        the stored client registration or refresh token is missing, which is the
        only way a headless process could be pushed into dynamic client
        registration or a browser flow (R9). The caller treats that as "MCP is
        off for this run" and falls back to REST (R6). Nothing is left running:
        the portal is started lazily, after this check.
        """
        self._discard_everything()
        install_secret_free_logging()
        self._require_custody()

    def end_run(self) -> None:
        """Discard the session and join the event-loop thread. Never raises."""
        self._discard_everything()

    def close(self) -> None:
        """Alias for :meth:`end_run`; idempotent."""
        self.end_run()

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def get_object(self, object_id: str, *, structure_id: str) -> dict[str, Any]:
        """One content read, normalized to the REST ``GET /object`` shape.

        ``structure_id`` is required: it is the REST listing row's structure id
        and normalization rule (i) stamps it, because MCP's own type fields are
        not identity.

        Raises :class:`capacities_adapter.CapacitiesContractError` for a payload
        admission cannot trust (deterministic, never retried) and
        :class:`capacities_adapter.CapacitiesReadSourceUnavailable` for any
        authorization, session, transport, or throttling failure (source loss,
        never a read retry).
        """
        identity = _text(object_id)
        structure = _text(structure_id)
        if identity is None or structure is None:
            raise capacities_adapter.CapacitiesContractError(_MISSING_IDENTITY)
        session = self._ensure_session()
        tool_name = self._content_tool()
        arguments = {CONTENT_TOOL_ARGUMENT: identity}
        try:
            result = self._portal.call(
                functools.partial(
                    session.call_tool,
                    tool_name,
                    arguments,
                    read_timeout_seconds=timedelta(seconds=self._timeout),
                )
            )
        except (KeyboardInterrupt, SystemExit):  # pragma: no cover - operator signals
            raise
        except capacities_adapter.CapacitiesContractError:
            raise
        except BaseException as exc:  # noqa: BLE001 — every cause becomes source loss
            raise self._abandon(exc) from None
        payload = payload_from_result(result)
        return normalize_mcp_content(
            payload, structure_id=structure, space_id=self._space_id
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _require_custody(self) -> None:
        """Refuse to run without a stored registration and refresh token."""
        try:
            self._storage.require_custody()
        except CapacitiesMcpAuthError:
            raise unavailable_from(None) from None
        except OSError:  # pragma: no cover - custody raises its own error type
            raise unavailable_from(None) from None

    def _ensure_portal(self) -> Any:
        if self._portal is not None:
            return self._portal
        self._portal_cm = start_blocking_portal(
            backend="asyncio", name=PORTAL_THREAD_NAME
        )
        self._portal = self._portal_cm.__enter__()
        return self._portal

    def _ensure_session(self) -> McpSession:
        if self._session is not None:
            return self._session
        install_secret_free_logging()
        self._require_custody()
        self._ensure_portal()
        try:
            auth = self._build_auth()
            context_manager = self._session_factory(auth)
        except CapacitiesMcpAuthError:
            raise self._abandon(None) from None
        except capacities_adapter.CapacitiesReadSourceUnavailable:
            raise
        except (KeyboardInterrupt, SystemExit):  # pragma: no cover
            raise
        except BaseException as exc:  # noqa: BLE001 — metadata or auth failure
            raise self._abandon(exc) from None
        recorder = _RecordingSessionContext(context_manager)
        self._session_context = recorder
        try:
            sync_cm = self._portal.wrap_async_context_manager(recorder)
            session = sync_cm.__enter__()
        except (KeyboardInterrupt, SystemExit):  # pragma: no cover
            raise
        except BaseException as exc:  # noqa: BLE001 — the session cannot open
            raise self._abandon(exc) from None
        self._sync_cm = sync_cm
        self._session = session
        self._auth = auth
        return session

    def _build_auth(self) -> OAuthClientProvider:
        """A headless auth provider with the two KTD3 pre-sets already applied."""
        metadata = self._authorization_metadata()
        client_metadata = OAuthClientMetadata(
            client_name=CLIENT_NAME,
            redirect_uris=[AnyUrl(REDIRECT_URI)],
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
            token_endpoint_auth_method="none",
            scope=REQUESTED_SCOPE,
        )
        auth = OAuthClientProvider(
            self._server_url,
            client_metadata,
            self._storage,
            redirect_handler=None,
            callback_handler=None,
            timeout=self._timeout,
        )
        # Restart bug: the library loads tokens but never restores the expiry, so
        # an expired access token would look valid, 401, and start a full flow.
        expires_at = self._storage.access_expires_at()
        if expires_at is not None:
            auth.context.token_expiry_time = float(expires_at)
        # Without the public metadata the library refreshes against
        # <origin>/token (404); the real token endpoint is /oauth/token.
        auth.context.oauth_metadata = metadata
        return auth

    def _authorization_metadata(self) -> OAuthMetadata:
        """The authorization-server metadata, fetched at most once per provider."""
        if self._metadata is not None:
            return self._metadata
        if self._metadata_error is not None:
            raise unavailable_from(self._metadata_error) from None
        try:
            raw = self._metadata_loader()
            metadata = OAuthMetadata.model_validate(raw)
        except (KeyboardInterrupt, SystemExit):  # pragma: no cover
            raise
        except BaseException as exc:  # noqa: BLE001 — class name only, never text
            self._metadata_error = exc
            raise unavailable_from(exc) from None
        self._metadata = metadata
        return metadata

    def _content_tool(self) -> str:
        """The resolved content tool name, cached for this session."""
        if self._tool_name is not None:
            return self._tool_name
        try:
            listed = self._portal.call(self._session.list_tools)
        except (KeyboardInterrupt, SystemExit):  # pragma: no cover
            raise
        except BaseException as exc:  # noqa: BLE001
            raise self._abandon(exc) from None
        for tool in getattr(listed, "tools", None) or []:
            name = getattr(tool, "name", None)
            if isinstance(name, str) and name.endswith(CONTENT_TOOL_SUFFIX):
                self._tool_name = name
                return name
        # "The tool is unavailable" is a documented R6 fallback condition.
        raise unavailable_from(None)

    def _abandon(self, cause: BaseException | None) -> Any:
        """Discard the session and return the source-loss exception to raise.

        The session dies on a transport error, and the *real* error (a 429, for
        instance) surfaces only when the session context is exited, so the exit
        error is inspected for the status and the ``Retry-After`` value.
        """
        exit_error = self._discard_session()
        return unavailable_from(cause, exit_error)

    def _discard_session(self) -> BaseException | None:
        """Close the session, then stop the loop. Returns the exit error, if any."""
        context_manager = self._sync_cm
        recorder = self._session_context
        self._sync_cm = None
        self._session_context = None
        self._session = None
        self._auth = None
        self._tool_name = None
        error: BaseException | None = None
        if context_manager is not None:
            try:
                context_manager.__exit__(None, None, None)
            except (KeyboardInterrupt, SystemExit):  # pragma: no cover
                raise
            except BaseException as exc:  # noqa: BLE001 — reported, not raised
                error = exc
        if recorder is not None and recorder.exit_error is not None:
            error = recorder.exit_error
        self._stop_portal()
        return error

    def _stop_portal(self) -> None:
        """Stop the private loop and join its thread. Never raises."""
        context_manager = self._portal_cm
        self._portal = None
        self._portal_cm = None
        if context_manager is None:
            return
        try:
            context_manager.__exit__(None, None, None)
        except (KeyboardInterrupt, SystemExit):  # pragma: no cover
            raise
        except BaseException:  # noqa: BLE001 — the loop is already gone
            pass

    def _discard_everything(self) -> None:
        """Best-effort teardown that never raises out of a run boundary."""
        with contextlib.suppress(Exception):
            self._discard_session()
