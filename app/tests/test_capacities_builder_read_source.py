"""U6b: the ``read_source`` gate in ``build_refresh_coordinator`` (R6, R9, R12).

Four branches, exactly as KTD10 states them:

- ``rest`` (the default and the kill switch) builds the bare REST client, no
  splitter, and no MCP module import during the build;
- ``auto`` with a usable custody file builds the splitter over that same REST
  client plus a real ``McpProvider``;
- ``auto`` with NO custody file builds the bare REST client: the operator has
  not consented yet, so a per-run warning would be noise;
- ``auto`` with a custody file that exists but is unusable (loose permissions)
  builds the splitter, and the first run is REST-only with exactly one bounded
  fallback warning.

Everything here is offline: the REST side is an ``httpx.MockTransport`` over
the real ``CapacitiesRestClient``, the MCP side is an injected fake session
factory over the real ``McpProvider``, the custody document is a ``tmp_path``
file, and the app home is the suite's per-test ``TDTB_HOME``.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
from pathlib import Path

import httpx
import pytest
from mcp import types
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

sys.path.insert(0, str(Path(__file__).parent.parent))

import capacities_builder as cb  # noqa: E402
import capacities_mcp as cpm  # noqa: E402
import capacities_mcp_auth as cpma  # noqa: E402
import capacities_read_source as rs  # noqa: E402
import capacities_refresh as rr  # noqa: E402
import capacities_settings as cs  # noqa: E402
import capacities_structure_titles as cst  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent))
from capacities_refresh_helpers import _content, _structures  # noqa: E402

SPACE = "space-1"
TYPE = "RootTask"
OBJECT_IDS = ("task-1", "task-2")

#: Fake credential text. No raised message, log record, job field, or warning
#: may ever carry either string (R6/KTD9).
FAKE_ACCESS = "fake-access-token-DO-NOT-LEAK"
FAKE_REFRESH = "fake-refresh-token-DO-NOT-LEAK"

#: Where the served content lives, shared by both fake read sources so a parity
#: comparison compares the read path and nothing else.
CONTENT = {object_id: _content(object_id, TYPE) for object_id in OBJECT_IDS}


@pytest.fixture(autouse=True)
def _isolate_machine_local_cache(tmp_path, monkeypatch):
    """No test may read or write the real ``~/.config/tdtb`` caches."""
    monkeypatch.setattr(
        cb, "DEFAULT_CONTENT_CACHE_PATH", tmp_path / "machine-content-cache.json"
    )
    monkeypatch.setattr(
        cst, "DEFAULT_TITLES_CACHE_PATH", tmp_path / "machine-titles.json"
    )
    monkeypatch.setattr(cb, "_CACHE_REGISTRY", {})


# ---------------------------------------------------------------------------
# Stores (source record, settings, credentials, custody)
# ---------------------------------------------------------------------------


def _write_token(tmp_path: Path) -> Path:
    path = tmp_path / "env"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("CAPACITIES_API_TOKEN=rest-token-value\n", encoding="utf-8")
    os.chmod(path, 0o600)
    return path


def _write_source_record() -> None:
    path = cb.source_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "revision": 0,
                "space_id": SPACE,
                "structures": [
                    {
                        "structure_id": TYPE,
                        "title_property": "title",
                        "status_property": "status",
                        "open_status_values": ["active"],
                        "assignment_property": "assigned",
                        "assignment_values": ["true"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def _write_settings(read_source: str) -> None:
    cs.save_settings(
        expected_revision=cs.read_settings().settings.revision,
        # A structure contributes only when the policy declares it: undeclared
        # is ``off``. The declaration is what makes the record's mapping live.
        types={TYPE: cs.TypeDeclaration(mode="active")},
        read_source=read_source,
    )


def _custody_path(tmp_path: Path) -> Path:
    return tmp_path / "custody" / "oauth.json"


def _configured_custody(tmp_path: Path) -> Path:
    """A custody document the operator's authorize step would have written."""
    storage = cpma.FileTokenStorage(_custody_path(tmp_path))
    asyncio.run(
        storage.set_client_info(
            OAuthClientInformationFull(
                redirect_uris=[cpm.REDIRECT_URI],
                token_endpoint_auth_method="none",
                client_id="client-1",
            )
        )
    )
    asyncio.run(
        storage.set_tokens(
            OAuthToken(
                access_token=FAKE_ACCESS,
                refresh_token=FAKE_REFRESH,
                expires_in=3600,
            )
        )
    )
    return storage.path


def _loose_custody(tmp_path: Path) -> Path:
    """A custody document that exists but is not owner-only."""
    path = _configured_custody(tmp_path)
    os.chmod(path, 0o644)
    return path


# ---------------------------------------------------------------------------
# Fakes for the two read sources
# ---------------------------------------------------------------------------


class _RestTransport(httpx.MockTransport):
    """The three REST reads a cold refresh makes, over the real client.

    ``gate`` lets a test hold the first REST content read until the MCP worker
    has served one, so "both sources are real" is a fact rather than a race.
    The wait is bounded, so a run that never reaches MCP still finishes.
    """

    def __init__(self, *, gate: threading.Event | None = None) -> None:
        self.gate = gate
        self.paths: list[str] = []
        self.content_reads: list[str] = []
        super().__init__(self._handler)

    def _handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.paths.append(path)
        if path == "/space/structures":
            return httpx.Response(200, json={"structures": _structures(TYPE)})
        if path == "/objects/structure":
            return httpx.Response(
                200,
                json={
                    "results": [
                        {"id": object_id, "structureId": TYPE}
                        for object_id in OBJECT_IDS
                    ],
                    "nextCursor": None,
                    "hasMore": False,
                },
            )
        if path == "/object":
            if self.gate is not None:
                self.gate.wait(2.0)
            object_id = request.url.params.get("id", "")
            self.content_reads.append(object_id)
            return httpx.Response(200, json=CONTENT[object_id])
        raise AssertionError(f"unexpected network call: {request.url}")


class FakeSession:
    """The two ``ClientSession`` calls ``McpProvider`` makes."""

    def __init__(self, served: threading.Event | None = None) -> None:
        self.calls: list[str] = []
        self._served = served

    async def list_tools(self):
        return types.ListToolsResult(
            tools=[types.Tool(name="capacities_getObjectContent", inputSchema={})]
        )

    async def call_tool(self, name, arguments=None, read_timeout_seconds=None):
        object_id = dict(arguments or {})["id"]
        self.calls.append(object_id)
        if self._served is not None:
            self._served.set()
        return types.CallToolResult(content=[], structuredContent=CONTENT[object_id])


class FakeSessionFactory:
    """An injected session factory: one local context per open, no network."""

    def __init__(self, served: threading.Event | None = None) -> None:
        self.served = served
        self.opens = 0
        self.sessions: list[FakeSession] = []

    def __call__(self, auth):
        self.opens += 1
        session = FakeSession(self.served)
        self.sessions.append(session)

        class _Context:
            async def __aenter__(self_inner):
                return session

            async def __aexit__(self_inner, *exc_info):
                return False

        return _Context()

    @property
    def reads(self) -> list[str]:
        return [object_id for session in self.sessions for object_id in session.calls]


def _config(
    tmp_path: Path,
    transport: httpx.MockTransport,
    factory: FakeSessionFactory | None = None,
) -> cb.CapacitiesBuilderConfig:
    return cb.CapacitiesBuilderConfig(
        token_path=_write_token(tmp_path),
        refresh_state_path=tmp_path / "state",
        transport=transport,
        mcp_token_path=_custody_path(tmp_path),
        mcp_session_factory=factory,
        mcp_metadata_loader=lambda: {
            "issuer": "https://api.capacities.io",
            "authorization_endpoint": "https://api.capacities.io/oauth/authorize",
            "token_endpoint": "https://api.capacities.io/oauth/token",
            "response_types_supported": ["code"],
        },
    )


def _build(
    tmp_path: Path,
    *,
    read_source: str,
    custody: str | None = None,
    transport: httpx.MockTransport | None = None,
    factory: FakeSessionFactory | None = None,
):
    _write_source_record()
    _write_settings(read_source)
    if custody == "valid":
        _configured_custody(tmp_path)
    elif custody == "loose":
        _loose_custody(tmp_path)
    coordinator = cb.build_refresh_coordinator(
        _config(tmp_path, transport or _RestTransport(), factory)
    )
    assert coordinator is not None
    return coordinator


def _run(coordinator) -> dict:
    coordinator.start()
    status = coordinator.wait(timeout=20)
    coordinator.close()
    return status


def _published(coordinator) -> list[tuple[str, str]]:
    snapshot = coordinator.store.load_snapshot(rr.DEFAULT_SCOPE_KEY)
    assert snapshot is not None
    return [(member.type_key, member.object_id) for member in snapshot.members]


def _no_secret(payload) -> None:
    text = json.dumps(payload, default=str)
    assert FAKE_ACCESS not in text
    assert FAKE_REFRESH not in text


# ---------------------------------------------------------------------------
# The four build branches
# ---------------------------------------------------------------------------


def test_rest_builds_the_bare_client_and_imports_no_mcp_module(tmp_path, monkeypatch):
    """``rest`` is the kill switch: today's provider, no splitter, no MCP."""
    monkeypatch.delitem(sys.modules, "capacities_mcp", raising=False)
    monkeypatch.delitem(sys.modules, "capacities_mcp_auth", raising=False)
    coordinator = _build(tmp_path, read_source="rest")

    assert type(coordinator.provider.raw) is cb.CapacitiesRestClient
    assert coordinator.provider.paces_content_reads is False
    assert not hasattr(coordinator.provider, "read_counts")
    assert "capacities_mcp" not in sys.modules
    assert "capacities_mcp_auth" not in sys.modules


def test_auto_with_a_usable_custody_file_builds_the_splitter(tmp_path):
    coordinator = _build(tmp_path, read_source="auto", custody="valid")

    splitter = coordinator.provider.raw
    assert type(splitter) is rs.ReadSourceSplitter
    assert coordinator.provider.paces_content_reads is True
    assert type(splitter.rest) is cb.CapacitiesRestClient
    assert type(splitter.mcp) is cpm.McpProvider
    # No session opened and no REST read made at build time (KTD10/R6).
    assert splitter.mcp._session is None


def test_auto_without_any_custody_file_builds_the_bare_client(tmp_path):
    """No file means no consent yet: REST-only, silently, with no warning."""
    coordinator = _build(tmp_path, read_source="auto", custody=None)

    assert type(coordinator.provider.raw) is cb.CapacitiesRestClient
    assert not hasattr(coordinator.provider, "read_counts")


def test_auto_with_a_loosely_permissioned_file_is_rest_only_with_one_warning(tmp_path):
    """The build never raises; the run says once that the fast read was lost."""
    transport = _RestTransport()
    coordinator = _build(
        tmp_path, read_source="auto", custody="loose", transport=transport
    )
    assert type(coordinator.provider.raw) is rs.ReadSourceSplitter

    status = _run(coordinator)

    assert status["phase"] == "complete"
    assert status["outcome"] == "published"
    metrics = status["progress"]["metrics"]
    assert metrics["read_source"] == "rest"
    assert metrics["mcp_reads"] == 0
    assert metrics["rest_reads"] == len(OBJECT_IDS)
    assert metrics["read_source_fell_back"] is True
    assert status["warnings"].count(rr.MCP_FALLBACK_WARNING) == 1
    assert sorted(transport.content_reads) == sorted(OBJECT_IDS)
    _no_secret(status)


# ---------------------------------------------------------------------------
# End to end: same publication through either read path
# ---------------------------------------------------------------------------


def test_auto_publishes_the_same_rows_as_rest_through_a_cold_refresh(tmp_path):
    """AE-level wiring check over the production builder, REST and MCP fakes."""
    rest_dir = tmp_path / "rest-mode"
    auto_dir = tmp_path / "auto-mode"
    rest_dir.mkdir()
    auto_dir.mkdir()

    rest_transport = _RestTransport()
    rest_coordinator = _build(
        rest_dir,
        read_source="rest",
        transport=rest_transport,
        factory=FakeSessionFactory(),
    )
    rest_status = _run(rest_coordinator)
    assert rest_status["outcome"] == "published", rest_status
    rest_metrics = rest_status["progress"]["metrics"]
    assert rest_metrics["read_source"] == "rest"
    assert rest_metrics["mcp_reads"] == 0

    # Hold the first REST content read until MCP has served one, so the MCP
    # worker is genuinely contributing instead of merely losing a race.
    mcp_served = threading.Event()
    auto_transport = _RestTransport(gate=mcp_served)
    factory = FakeSessionFactory(served=mcp_served)
    auto_coordinator = _build(
        auto_dir, read_source="auto", custody="valid", transport=auto_transport,
        factory=factory,
    )
    auto_status = _run(auto_coordinator)
    assert auto_status["outcome"] == "published", auto_status
    auto_metrics = auto_status["progress"]["metrics"]
    assert auto_metrics["read_source"] == "split"
    assert auto_metrics["mcp_reads"] >= 1
    assert auto_metrics["read_source_fell_back"] is False
    assert rr.MCP_FALLBACK_WARNING not in auto_status["warnings"]
    assert factory.reads, "the MCP worker served no read"

    assert _published(auto_coordinator) == _published(rest_coordinator)
    assert [member for member in _published(rest_coordinator)] == [
        (TYPE, object_id) for object_id in OBJECT_IDS
    ]
    _no_secret(rest_status)
    _no_secret(auto_status)


def test_a_missing_mcp_library_builds_rest_only(monkeypatch, tmp_path):
    """Review finding: an import failure never raises out of the build."""
    import builtins

    real_import = builtins.__import__

    def refuse(name, *args, **kwargs):
        if name == "capacities_mcp":
            raise ImportError("simulated missing mcp library")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", refuse)
    cfg = cb.CapacitiesBuilderConfig(mcp_token_path=tmp_path / "custody" / "oauth.json")
    assert cb._mcp_splitter_factory(object(), cfg, object()) is None
