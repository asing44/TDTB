"""Contract tests for the Capacities MCP content-read provider.

``McpProvider`` is the only MCP surface the server has: a synchronous
``get_object`` that must return payloads admission cannot distinguish from REST
``GET /object``, must be headless-safe on a rotating OAuth credential, and must
treat every authorization, session, transport, and throttling failure as source
loss rather than as a read outcome.

Everything here is offline: the custody file is a ``tmp_path`` document, the
session factory is a fake, and the authorization-server metadata is injected.
The tests pin the three normalization rules individually against a captured
REST/MCP payload pair, the two pre-sets KTD3 exists for, the fact that a 429 is
source loss carrying ``Retry-After``, and that no fake credential reaches a log.
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from mcp import types
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

sys.path.insert(0, str(Path(__file__).parent.parent))

import capacities_adapter as adapter  # noqa: E402
import capacities_mcp as cpm  # noqa: E402
from capacities_mcp_auth import FileTokenStorage  # noqa: E402

SPACE_ID = "space-11111111"
SERVER_URL = "https://api.capacities.io/mcp"
TOKEN_ENDPOINT = "https://api.capacities.io/oauth/token"
PROPERTY_ENDPOINT = "https://api.capacities.io/oauth/authorize"

FAKE_ACCESS = "eyJhbGciOiJIUzI1NiJ9.fake-access-DO-NOT-LEAK"
FAKE_REFRESH = "fake-refresh-token-DO-NOT-LEAK-" + "x" * 40
FAKE_CLIENT_SECRET = "fake-client-secret-DO-NOT-LEAK"

METADATA = {
    "issuer": "https://api.capacities.io",
    "authorization_endpoint": PROPERTY_ENDPOINT,
    "token_endpoint": TOKEN_ENDPOINT,
    "response_types_supported": ["code"],
}


# ---------------------------------------------------------------------------
# Fakes and fixtures
# ---------------------------------------------------------------------------


class FakeSession:
    """The two ``ClientSession`` calls the provider makes, with a scripted answer."""

    def __init__(
        self,
        *,
        result=None,
        error: BaseException | None = None,
        list_error: BaseException | None = None,
        tool_names=("capacities_getObjectContent",),
    ) -> None:
        self.result = result
        self.error = error
        self.list_error = list_error
        self.tool_names = tuple(tool_names)
        self.calls: list[tuple[str, dict, object]] = []
        self.list_calls = 0

    async def list_tools(self):
        self.list_calls += 1
        if self.list_error is not None:
            raise self.list_error
        return SimpleNamespace(tools=[SimpleNamespace(name=name) for name in self.tool_names])

    async def call_tool(self, name, arguments=None, read_timeout_seconds=None):
        self.calls.append((name, dict(arguments or {}), read_timeout_seconds))
        if self.error is not None:
            raise self.error
        return self.result


class FakeSessionContext:
    """A session context manager whose exit can fail, as a dead transport does."""

    def __init__(
        self,
        session: FakeSession,
        *,
        exit_error: BaseException | None = None,
        enter_error: BaseException | None = None,
    ) -> None:
        self.session = session
        self.exit_error = exit_error
        self.enter_error = enter_error
        self.entered = 0
        self.exited = 0

    async def __aenter__(self) -> FakeSession:
        if self.enter_error is not None:
            raise self.enter_error
        self.entered += 1
        return self.session

    async def __aexit__(self, *exc_info) -> object:
        self.exited += 1
        if self.exit_error is not None:
            raise self.exit_error
        return False


class FakeFactory:
    """An injected session factory: hands out one context per open, never the network."""

    def __init__(self, *contexts: FakeSessionContext) -> None:
        self._pending = list(contexts)
        self.auths: list = []
        self.contexts: list[FakeSessionContext] = []

    def __call__(self, auth):
        self.auths.append(auth)
        context = (
            self._pending.pop(0)
            if self._pending
            else FakeSessionContext(FakeSession(result=structured({"id": "obj-1", "properties": {}})))
        )
        self.contexts.append(context)
        return context

    @property
    def opens(self) -> int:
        return len(self.auths)


def structured(payload: dict) -> types.CallToolResult:
    """A ``structuredContent`` delivery, as the live server sends."""
    return types.CallToolResult(content=[], structuredContent=payload)


def text_delivery(payload: dict) -> types.CallToolResult:
    """A single ``TextContent`` JSON delivery."""
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=json.dumps(payload))]
    )


def http_error(status: int, *, retry_after: str | None = None) -> httpx.HTTPStatusError:
    headers = {} if retry_after is None else {"Retry-After": retry_after}
    request = httpx.Request("POST", SERVER_URL)
    response = httpx.Response(status, headers=headers, request=request)
    return httpx.HTTPStatusError(
        f"transport {status}", request=request, response=response
    )


def _run(coro):
    return asyncio.run(coro)


def _storage(tmp_path: Path, *, expires_in: int = 3600, client_info: bool = True) -> FileTokenStorage:
    storage = FileTokenStorage(tmp_path / "custody" / "oauth.json")
    if client_info:
        _run(
            storage.set_client_info(
                OAuthClientInformationFull(
                    redirect_uris=[cpm.REDIRECT_URI],
                    token_endpoint_auth_method="none",
                    client_id="client-1",
                )
            )
        )
    _run(
        storage.set_tokens(
            OAuthToken(
                access_token=FAKE_ACCESS,
                refresh_token=FAKE_REFRESH,
                expires_in=expires_in,
            )
        )
    )
    return storage


def _provider(storage: FileTokenStorage, factory: FakeFactory, **kwargs) -> cpm.McpProvider:
    return cpm.McpProvider(
        SPACE_ID,
        storage,
        session_factory=factory,
        metadata_loader=lambda: METADATA,
        **kwargs,
    )


def _portal_threads() -> list[threading.Thread]:
    return [t for t in threading.enumerate() if t.name == cpm.PORTAL_THREAD_NAME]


CLEAN_PROPERTIES = {
    "title": {"type": "title", "title": {"value": "Draft the plan"}},
}


def mcp_payload(**overrides) -> dict:
    """A representative MCP content payload: REST's fields plus MCP's extras."""
    payload = {
        "id": "obj-1",
        "structureId": "leaked-should-not-be-trusted",
        "spaceId": SPACE_ID,
        "objectType": "Task",
        "objectTypeId": "RootTask",
        "title": "Draft the plan",
        "content": "<p>body</p>",
        "createdAt": "2026-11-01T10:00:00Z",
        "lastUpdated": "2026-11-02T10:00:00Z",
        "tags": ["triage"],
        "collections": ["inbox"],
        "blocks": {"block-1": [{"tokens": [{"text": "hint"}]}]},
        "properties": {
            **CLEAN_PROPERTIES,
            "status": {"type": "label", "label": []},
        },
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# Normalization: the three rules, one at a time
# ---------------------------------------------------------------------------


def test_rule_one_stamps_the_listing_structure_id():
    payload = mcp_payload()
    normalized = cpm.normalize_mcp_content(
        payload, structure_id="Project", space_id=SPACE_ID
    )
    # The MCP value is discarded, not merged.
    assert normalized["structureId"] == "Project"
    assert "objectTypeId" not in normalized and "objectType" not in normalized


def test_rule_one_keeps_the_literal_root_task_structure_id():
    payload = mcp_payload(objectType="Task", objectTypeId="RootTask")
    normalized = cpm.normalize_mcp_content(
        payload, structure_id="RootTask", space_id=SPACE_ID
    )
    assert normalized["structureId"] == "RootTask"


def test_rule_two_drops_a_matching_top_level_space_id():
    normalized = cpm.normalize_mcp_content(
        mcp_payload(), structure_id="Project", space_id=SPACE_ID
    )
    assert "spaceId" not in normalized


def test_rule_three_strips_entity_reference_titles_and_keeps_ids_in_order():
    payload = mcp_payload(
        properties={
            **CLEAN_PROPERTIES,
            "people": {
                "type": "entity",
                "entity": [
                    {"id": "p-1", "title": "Ada"},
                    {"id": "p-2", "title": "Grace"},
                ],
            },
            "mixed": {"type": "entity", "entities": [{"id": "e-1", "title": "One"}, "loose"]},
        }
    )
    properties = cpm.normalize_mcp_content(
        payload, structure_id="Project", space_id=SPACE_ID
    )["properties"]
    assert properties["people"]["entity"] == [{"id": "p-1"}, {"id": "p-2"}]
    assert properties["mixed"]["entities"] == [{"id": "e-1"}, "loose"]


def test_top_level_title_survives_because_the_adapter_falls_back_to_it():
    normalized = cpm.normalize_mcp_content(
        mcp_payload(properties={"title": {"type": "title", "title": {"value": ""}}}),
        structure_id="Project",
        space_id=SPACE_ID,
    )
    assert normalized["title"] == "Draft the plan"


def test_only_the_adapter_read_keys_are_kept():
    normalized = cpm.normalize_mcp_content(
        mcp_payload(), structure_id="Project", space_id=SPACE_ID
    )
    # REST carries no top-level tags/collections/blocks, so neither may MCP:
    # admission and the rules predicate record must see one shape only.
    assert set(normalized) == {"id", "structureId", "properties", "title"}


def test_an_empty_status_label_payload_passes_through_unchanged():
    payload = mcp_payload(properties={"status": {"type": "label", "label": []}})
    normalized = cpm.normalize_mcp_content(
        payload, structure_id="Project", space_id=SPACE_ID
    )
    assert normalized["properties"]["status"] == {"type": "label", "label": []}


def test_the_normalized_fingerprint_equals_the_rest_fingerprint():
    rest = {
        "id": "obj-1",
        "structureId": "Project",
        "properties": {
            "title": {"type": "title", "title": {"value": "Draft the plan"}},
            "status": {"type": "label", "label": []},
            "people": {"type": "entity", "entity": [{"id": "p-1"}, {"id": "p-2"}]},
            "due": {"type": "date", "date": {"start": "2026-11-10", "end": None}},
        },
        "blocks": {"block-1": [{"tokens": [{"text": "hint"}]}]},
    }
    mcp = mcp_payload(
        properties={
            "title": {"type": "title", "title": {"value": "Draft the plan"}},
            "status": {"type": "label", "label": []},
            "people": {
                "type": "entity",
                "entity": [{"id": "p-1", "title": "Ada"}, {"id": "p-2", "title": "Grace"}],
            },
            "due": {"type": "date", "date": {"start": "2026-11-10", "end": None}},
        }
    )
    normalized = cpm.normalize_mcp_content(
        mcp, structure_id="Project", space_id=SPACE_ID
    )
    assert adapter._fingerprint(normalized) == adapter._fingerprint(rest)


# ---------------------------------------------------------------------------
# Result delivery and contract failures
# ---------------------------------------------------------------------------


def test_structured_content_and_text_content_normalize_identically(tmp_path):
    factory = FakeFactory(
        FakeSessionContext(FakeSession(result=structured(mcp_payload()))),
        FakeSessionContext(FakeSession(result=text_delivery(mcp_payload()))),
    )
    provider = _provider(_storage(tmp_path), factory)
    try:
        first = provider.get_object("obj-1", structure_id="Project")
        second = provider.get_object("obj-1", structure_id="Project")
    finally:
        provider.close()
    assert first == second
    assert first["structureId"] == "Project"
    assert "spaceId" not in first


def test_the_tool_is_resolved_by_suffix(tmp_path):
    session = FakeSession(
        result=structured(mcp_payload()),
        tool_names=("capacities_search", "capacities_getObjectContent"),
    )
    factory = FakeFactory(FakeSessionContext(session))
    provider = _provider(_storage(tmp_path), factory)
    try:
        provider.get_object("obj-1", structure_id="Project")
    finally:
        provider.close()
    name, arguments, timeout = session.calls[0]
    assert name == "capacities_getObjectContent"
    assert arguments == {"id": "obj-1"}
    assert timeout.total_seconds() == pytest.approx(15.0)


def test_an_is_error_result_is_a_contract_error(tmp_path):
    result = types.CallToolResult(
        content=[types.TextContent(type="text", text="boom")], isError=True
    )
    factory = FakeFactory(FakeSessionContext(FakeSession(result=result)))
    provider = _provider(_storage(tmp_path), factory)
    try:
        with pytest.raises(adapter.CapacitiesContractError):
            provider.get_object("obj-1", structure_id="Project")
    finally:
        provider.close()


@pytest.mark.parametrize(
    "result",
    [
        types.CallToolResult(content=[types.TextContent(type="text", text="not json")]),
        types.CallToolResult(content=[types.TextContent(type="text", text="[1, 2]")]),
        types.CallToolResult(content=[]),
        types.CallToolResult(
            content=[
                types.TextContent(type="text", text=json.dumps({"id": "obj-1"})),
                types.TextContent(type="text", text=json.dumps({"id": "obj-1"})),
            ]
        ),
    ],
    ids=["non-json", "json-list", "empty", "multiple-blocks"],
)
def test_garbage_results_are_contract_errors(tmp_path, result):
    factory = FakeFactory(FakeSessionContext(FakeSession(result=result)))
    provider = _provider(_storage(tmp_path), factory)
    try:
        with pytest.raises(adapter.CapacitiesContractError):
            provider.get_object("obj-1", structure_id="Project")
    finally:
        provider.close()


def test_missing_properties_is_a_contract_error(tmp_path):
    factory = FakeFactory(
        FakeSessionContext(FakeSession(result=structured(mcp_payload(properties=None))))
    )
    provider = _provider(_storage(tmp_path), factory)
    try:
        with pytest.raises(adapter.CapacitiesContractError):
            provider.get_object("obj-1", structure_id="Project")
    finally:
        provider.close()


def test_a_foreign_space_id_is_a_contract_error_and_stores_nothing(tmp_path):
    factory = FakeFactory(
        FakeSessionContext(
            FakeSession(result=structured(mcp_payload(spaceId="space-99999999")))
        )
    )
    provider = _provider(_storage(tmp_path), factory)
    try:
        with pytest.raises(adapter.CapacitiesContractError):
            provider.get_object("obj-1", structure_id="Project")
        # Deterministic, not a source failure: no fallback signal is produced.
        assert factory.opens == 1
    finally:
        provider.close()


def test_a_read_without_a_structure_id_is_a_contract_error(tmp_path):
    factory = FakeFactory()
    provider = _provider(_storage(tmp_path), factory)
    try:
        with pytest.raises(adapter.CapacitiesContractError):
            provider.get_object("obj-1", structure_id="")
        assert factory.opens == 0
    finally:
        provider.close()


# ---------------------------------------------------------------------------
# Headless auth (KTD3)
# ---------------------------------------------------------------------------


def test_the_absolute_expiry_is_preset_before_the_first_request(tmp_path):
    storage = _storage(tmp_path, expires_in=-3600)
    expired_at = storage.access_expires_at()
    assert expired_at is not None and expired_at < time.time()

    factory = FakeFactory(FakeSessionContext(FakeSession(result=structured(mcp_payload()))))
    provider = _provider(storage, factory)
    try:
        provider.get_object("obj-1", structure_id="Project")
    finally:
        provider.close()
    # Without this the library would see a valid-looking token, get a 401, and
    # start a full authorization flow instead of refreshing (KTD3).
    assert factory.auths[0].context.token_expiry_time == pytest.approx(expired_at)


def test_oauth_metadata_is_preset_once_before_the_first_request(tmp_path):
    loads = {"count": 0}

    def loader():
        loads["count"] += 1
        return METADATA

    factory = FakeFactory(
        FakeSessionContext(FakeSession(result=structured(mcp_payload()))),
    )
    provider = cpm.McpProvider(
        SPACE_ID, _storage(tmp_path), session_factory=factory, metadata_loader=loader
    )
    try:
        provider.get_object("obj-1", structure_id="Project")
        provider.get_object("obj-1", structure_id="Project")
    finally:
        provider.close()
    context = factory.auths[0].context
    # Without this the library refreshes against <origin>/token and 404s (U0).
    assert str(context.oauth_metadata.token_endpoint) == TOKEN_ENDPOINT
    assert loads["count"] == 1


def test_metadata_unavailable_is_source_loss_and_is_not_refetched(tmp_path):
    calls = {"count": 0}

    def loader():
        calls["count"] += 1
        raise httpx.ConnectError("unreachable")

    factory = FakeFactory()
    provider = cpm.McpProvider(
        SPACE_ID, _storage(tmp_path), session_factory=factory, metadata_loader=loader
    )
    try:
        with pytest.raises(adapter.CapacitiesReadSourceUnavailable) as first:
            provider.get_object("obj-1", structure_id="Project")
        with pytest.raises(adapter.CapacitiesReadSourceUnavailable):
            provider.get_object("obj-1", structure_id="Project")
    finally:
        provider.close()
    assert str(first.value) == ""
    assert calls["count"] == 1
    assert factory.opens == 0


def test_a_metadata_failure_costs_one_run_not_the_whole_process(tmp_path):
    """Review finding: the next run's begin_run fetches the metadata afresh."""
    calls = {"count": 0}

    def loader():
        calls["count"] += 1
        if calls["count"] == 1:
            raise httpx.ConnectError("unreachable")
        return METADATA

    factory = FakeFactory(
        FakeSessionContext(FakeSession(result=structured(mcp_payload()))),
    )
    provider = cpm.McpProvider(
        SPACE_ID, _storage(tmp_path), session_factory=factory, metadata_loader=loader
    )
    try:
        provider.begin_run()
        with pytest.raises(adapter.CapacitiesReadSourceUnavailable):
            provider.get_object("obj-1", structure_id="Project")
        provider.end_run()

        provider.begin_run()
        value = provider.get_object("obj-1", structure_id="Project")
    finally:
        provider.close()
    assert value["id"] == "obj-1"
    assert calls["count"] == 2


def test_missing_client_info_refuses_before_any_request(tmp_path):
    storage = _storage(tmp_path, client_info=False)
    factory = FakeFactory()
    provider = _provider(storage, factory)
    try:
        with pytest.raises(adapter.CapacitiesReadSourceUnavailable) as refusal:
            provider.begin_run()
        assert str(refusal.value) == ""
        # No session was opened, so nothing could have attempted registration.
        assert factory.opens == 0
        assert _portal_threads() == []
        with pytest.raises(adapter.CapacitiesReadSourceUnavailable):
            provider.get_object("obj-1", structure_id="Project")
        assert factory.opens == 0
    finally:
        provider.close()


def test_an_oauth_token_error_is_source_loss_with_an_empty_message(tmp_path):
    from mcp.client.auth import OAuthTokenError

    session = FakeSession(error=OAuthTokenError(f"Token exchange failed: {FAKE_ACCESS}"))
    factory = FakeFactory(FakeSessionContext(session))
    provider = _provider(_storage(tmp_path), factory)
    try:
        with pytest.raises(adapter.CapacitiesReadSourceUnavailable) as failure:
            provider.get_object("obj-1", structure_id="Project")
    finally:
        provider.close()
    assert str(failure.value) == ""
    assert failure.value.retry_after is None
    assert FAKE_ACCESS not in str(failure.value)


def test_a_401_is_source_loss(tmp_path):
    session = FakeSession(error=http_error(401))
    factory = FakeFactory(FakeSessionContext(session, exit_error=http_error(401)))
    provider = _provider(_storage(tmp_path), factory)
    try:
        with pytest.raises(adapter.CapacitiesReadSourceUnavailable) as failure:
            provider.get_object("obj-1", structure_id="Project")
    finally:
        provider.close()
    assert str(failure.value) == ""
    assert failure.value.retry_after is None


def test_a_missing_tool_is_source_loss_not_a_contract_error(tmp_path):
    session = FakeSession(tool_names=("capacities_search", "capacities_readObjectBlocks"))
    factory = FakeFactory(FakeSessionContext(session))
    provider = _provider(_storage(tmp_path), factory)
    try:
        with pytest.raises(adapter.CapacitiesReadSourceUnavailable):
            provider.get_object("obj-1", structure_id="Project")
        assert session.calls == []
    finally:
        provider.close()


# ---------------------------------------------------------------------------
# Throttling is source loss, never a read retry (KTD4/KTD7)
# ---------------------------------------------------------------------------


def test_a_429_is_source_loss_with_retry_after_and_a_fresh_session_next_time(tmp_path):
    # The measured shape: raise_for_status inside the transport's task group
    # cancels the in-flight call, and the real error surfaces on context exit.
    dead = FakeSessionContext(
        FakeSession(error=asyncio.CancelledError()),
        exit_error=http_error(429, retry_after="39"),
    )
    healthy = FakeSessionContext(FakeSession(result=structured(mcp_payload(id="obj-2"))))
    factory = FakeFactory(dead, healthy)
    provider = _provider(_storage(tmp_path), factory)
    try:
        with pytest.raises(adapter.CapacitiesReadSourceUnavailable) as failure:
            provider.get_object("obj-1", structure_id="Project")
        assert failure.value.retry_after == pytest.approx(39.0)
        assert str(failure.value) == ""
        assert dead.exited == 1
        assert _portal_threads() == []
        rebuilt = provider.get_object("obj-2", structure_id="Project")
    finally:
        provider.close()
    assert rebuilt["id"] == "obj-2"
    assert factory.opens == 2
    assert factory.contexts[1].entered == 1


def test_a_429_without_a_retry_after_header_still_reports_source_loss(tmp_path):
    dead = FakeSessionContext(
        FakeSession(error=http_error(429)), exit_error=http_error(429)
    )
    factory = FakeFactory(dead)
    provider = _provider(_storage(tmp_path), factory)
    try:
        with pytest.raises(adapter.CapacitiesReadSourceUnavailable) as failure:
            provider.get_object("obj-1", structure_id="Project")
    finally:
        provider.close()
    assert failure.value.retry_after is None


def test_a_non_numeric_retry_after_is_not_guessed(tmp_path):
    dead = FakeSessionContext(
        FakeSession(error=http_error(429)),
        exit_error=http_error(429, retry_after="Wed, 21 Oct 2026 07:28:00 GMT"),
    )
    factory = FakeFactory(dead)
    provider = _provider(_storage(tmp_path), factory)
    try:
        with pytest.raises(adapter.CapacitiesReadSourceUnavailable) as failure:
            provider.get_object("obj-1", structure_id="Project")
    finally:
        provider.close()
    assert failure.value.retry_after is None


# ---------------------------------------------------------------------------
# Run lifecycle
# ---------------------------------------------------------------------------


def test_two_reads_share_one_session_and_one_tool_resolution(tmp_path):
    session = FakeSession(result=structured(mcp_payload()))
    factory = FakeFactory(FakeSessionContext(session))
    provider = _provider(_storage(tmp_path), factory)
    try:
        provider.begin_run()
        provider.get_object("obj-1", structure_id="Project")
        provider.get_object("obj-1", structure_id="Project")
    finally:
        provider.end_run()
    assert factory.opens == 1
    assert session.list_calls == 1
    assert len(session.calls) == 2


def test_close_joins_the_portal_thread(tmp_path):
    session = FakeSession(result=structured(mcp_payload()))
    provider = _provider(_storage(tmp_path), FakeFactory(FakeSessionContext(session)))
    provider.begin_run()
    provider.get_object("obj-1", structure_id="Project")
    assert len(_portal_threads()) == 1

    provider.close()
    assert _portal_threads() == []
    # Idempotent, and an unused provider never starts a thread at all.
    provider.close()
    cpm.McpProvider(SPACE_ID, _storage(tmp_path), session_factory=FakeFactory()).close()
    assert _portal_threads() == []


def test_end_run_discards_the_session_and_a_new_run_reopens_it(tmp_path):
    factory = FakeFactory(
        FakeSessionContext(FakeSession(result=structured(mcp_payload(id="obj-1")))),
        FakeSessionContext(FakeSession(result=structured(mcp_payload(id="obj-2")))),
    )
    provider = _provider(_storage(tmp_path), factory)
    try:
        provider.begin_run()
        provider.get_object("obj-1", structure_id="Project")
        provider.end_run()
        assert factory.contexts[0].exited == 1
        provider.begin_run()
        second = provider.get_object("obj-2", structure_id="Project")
    finally:
        provider.close()
    assert second["id"] == "obj-2"
    assert factory.opens == 2


# ---------------------------------------------------------------------------
# Log hygiene (KTD9 / R8)
# ---------------------------------------------------------------------------


@pytest.fixture
def sanitized_caplog(caplog):
    """caplog with the production filter on its handler, for this test only.

    Production never touches a foreign handler, so a harness handler that sees
    the ``mcp`` tree would otherwise render what the library logged. Attaching
    and detaching it here keeps the filter from leaking into other tests.
    """
    secret_free = cpm.SecretFreeLogFilter()
    caplog.handler.addFilter(secret_free)
    try:
        yield caplog
    finally:
        caplog.handler.removeFilter(secret_free)


def test_no_fake_secret_reaches_the_logs(tmp_path, sanitized_caplog):
    caplog = sanitized_caplog
    caplog.set_level(logging.DEBUG)
    factory = FakeFactory(FakeSessionContext(FakeSession(result=structured(mcp_payload()))))
    provider = _provider(_storage(tmp_path), factory)
    try:
        provider.begin_run()
        oauth_log = logging.getLogger("mcp.client.auth.oauth2")
        oauth_log.warning("Token refresh failed: 401 body=%s", FAKE_ACCESS)
        try:
            raise RuntimeError(f"client_secret={FAKE_CLIENT_SECRET}")
        except RuntimeError:
            oauth_log.error("OAuth flow error", exc_info=sys.exc_info())
        provider.get_object("obj-1", structure_id="Project")
    finally:
        provider.close()

    assert FAKE_ACCESS not in caplog.text
    assert FAKE_REFRESH not in caplog.text
    assert FAKE_CLIENT_SECRET not in caplog.text
    # The guarantee is the installed filter, not a quiet logger level.
    installed = logging.getLogger("mcp.client.auth").filters
    assert any(isinstance(item, cpm.SecretFreeLogFilter) for item in installed)


def test_the_filter_redacts_a_token_and_drops_a_traceback():
    secret_free = cpm.SecretFreeLogFilter()

    redacted = logging.LogRecord(
        "mcp.client.auth.oauth2",
        logging.WARNING,
        __file__,
        1,
        "access_token=%s",
        (FAKE_ACCESS,),
        None,
    )
    assert secret_free.filter(redacted) is True
    assert FAKE_ACCESS not in redacted.getMessage()

    try:
        raise RuntimeError(f"refresh_token={FAKE_REFRESH}")
    except RuntimeError:
        dropped = logging.LogRecord(
            "mcp.client.auth.oauth2",
            logging.ERROR,
            __file__,
            1,
            "OAuth flow error",
            None,
            sys.exc_info(),
        )
    assert secret_free.filter(dropped) is False


def test_the_provider_never_prints_a_credential_in_its_own_repr(tmp_path):
    provider = cpm.McpProvider(SPACE_ID, _storage(tmp_path), session_factory=FakeFactory())
    try:
        assert repr(provider) == "<McpProvider>"
        assert FAKE_ACCESS not in repr(provider)
        assert str(tmp_path) not in repr(provider)
    finally:
        provider.close()
