"""Contract tests for the operator-run Capacities MCP authorize CLI.

Consent is the one place in TDTB that may run an OAuth authorization flow, and
it is operator-attended: it prints a URL, waits for a ``127.0.0.1`` redirect,
and writes custody. The properties that carry that weight are pinned here —
the loopback listener is loopback-only and single-use, the returned ``state`` is
checked, custody lands owner-only, nothing secret reaches stdout/stderr/logs,
the CLI refuses to run while a Refresh job holds its lock, and no server module
imports it.

No test touches the network: the flow, the metadata document and the session
are all stubbed, and the only socket is the CLI's own loopback listener.
"""
from __future__ import annotations

import ast
import asyncio
import contextlib
import io
import logging
import os
import stat
import subprocess
import sys
import types
import urllib.request
from pathlib import Path
from urllib.parse import urlencode

import pytest
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

sys.path.insert(0, str(Path(__file__).parent.parent))

import capacities_mcp_authorize as cmz  # noqa: E402

APP_DIR = Path(__file__).resolve().parent.parent

REDIRECT_URI = "http://127.0.0.1:8765/callback"
FAKE_ACCESS = "fake-access-token-DO-NOT-LEAK"
FAKE_REFRESH = "fake-refresh-token-DO-NOT-LEAK"
FAKE_CLIENT_SECRET = "fake-client-secret-DO-NOT-LEAK"
FAKE_CODE = "fake-authorization-code-DO-NOT-LEAK"
EXPECTED_STATE = "expected-state-value"
MISMATCHED_STATE = "mismatched-state-DO-NOT-LEAK"
AUTHORIZATION_URL = (
    "https://api.capacities.io/authorize"
    f"?response_type=code&client_id=client-1&state={EXPECTED_STATE}"
    "&code_challenge=abc123&code_challenge_method=S256"
    f"&scope={cmz.REQUESTED_SCOPE.replace(' ', '%20')}"
)
METADATA_DOCUMENT = {
    "issuer": "https://api.capacities.io",
    "authorization_endpoint": "https://api.capacities.io/authorize",
    "token_endpoint": "https://api.capacities.io/oauth/token",
    "response_types_supported": ["code"],
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tokens() -> OAuthToken:
    return OAuthToken(
        access_token=FAKE_ACCESS, expires_in=3600, refresh_token=FAKE_REFRESH
    )


def _client_info() -> OAuthClientInformationFull:
    return OAuthClientInformationFull(
        redirect_uris=[REDIRECT_URI],
        token_endpoint_auth_method="none",
        client_id="client-1",
        client_secret=FAKE_CLIENT_SECRET,
    )


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def _fetch(url: str, *, code: str, state: str) -> None:
    """One real request to the CLI's own loopback listener."""
    query = urlencode({"code": code, "state": state})
    with urllib.request.urlopen(f"{url}?{query}", timeout=5.0) as response:
        response.read()


def _flow(*, delivered_state: str = EXPECTED_STATE, code: str = FAKE_CODE):
    """A fake OAuth flow: print the URL, deliver the redirect, write custody."""

    async def flow(storage, callback, *, metadata_loader=None, timeout=None):
        await callback.redirect_handler(
            f"https://api.capacities.io/authorize?response_type=code"
            f"&client_id=client-1&state={EXPECTED_STATE}"
        )
        await asyncio.to_thread(
            _fetch, callback.redirect_uri, code=code, state=delivered_state
        )
        returned_code, returned_state = await callback.callback_handler()
        assert returned_code == code
        assert returned_state == EXPECTED_STATE
        await storage.set_client_info(_client_info())
        await storage.set_tokens(_tokens())
        return 3

    return flow


def _main(flow, storage, *, out, lock_path=None, argv=()):
    return cmz.main(
        list(argv),
        storage=storage,
        flow=flow,
        lock_path_resolver=lambda: lock_path,
        out=out,
    )


# ---------------------------------------------------------------------------
# Consent, custody, and what reaches the output
# ---------------------------------------------------------------------------

def test_consent_writes_owner_only_custody_and_prints_no_secret(tmp_path, capsys):
    path = tmp_path / "custody" / "oauth.json"
    storage = cmz.capacities_mcp_auth.FileTokenStorage(path)
    out = io.StringIO()

    code = _main(
        _flow(),
        storage,
        out=out,
        lock_path=tmp_path / "jobs" / ".refresh-job.lock",
        argv=["--callback-timeout", "20", "--request-timeout", "20"],
    )

    assert code == cmz.EXIT_OK
    assert storage.is_configured() is True
    assert _mode(path) == 0o600
    assert _mode(path.parent) == 0o700

    captured = capsys.readouterr()
    assert captured.out == ""
    text = out.getvalue() + captured.out + captured.err
    for secret in (FAKE_CODE, FAKE_ACCESS, FAKE_REFRESH, FAKE_CLIENT_SECRET):
        assert secret not in text
    # The state must be in the printed URL (the operator opens that exact URL)
    # and nowhere else: no status line, no error line, no path.
    assert MISMATCHED_STATE not in text
    state_lines = [line for line in text.splitlines() if EXPECTED_STATE in line]
    assert len(state_lines) == 1
    assert state_lines[0].startswith("https://api.capacities.io/authorize?")
    assert "custody: written" in text
    assert "status: ok" in text
    assert str(path) not in text
    assert str(path.parent) not in text


def test_callback_returns_the_code_and_state_exactly_once(tmp_path):
    out = io.StringIO()
    consent = cmz.LoopbackConsentServer(timeout=2.0, out=out)
    consent.start()
    replies: list[tuple[int, str]] = []
    try:
        asyncio.run(consent.redirect_handler(AUTHORIZATION_URL))
        consent.handle_request(
            f"/callback?code={FAKE_CODE}&state={EXPECTED_STATE}",
            lambda status, message: replies.append((status, message)),
        )
        assert consent.wait_for_callback() == (FAKE_CODE, EXPECTED_STATE)
        # Single use: a second delivery cannot hand the code over again.
        consent.handle_request(
            f"/callback?code={FAKE_CODE}&state={EXPECTED_STATE}",
            lambda status, message: replies.append((status, message)),
        )
    finally:
        consent.close()

    assert replies[0][0] == 200
    assert replies[1] == (200, "This authorization redirect was already handled.")


def test_state_mismatch_is_rejected_without_echoing_it(tmp_path):
    consent = cmz.LoopbackConsentServer(timeout=2.0, out=io.StringIO())
    consent.start()
    try:
        asyncio.run(consent.redirect_handler(AUTHORIZATION_URL))
        consent.handle_request(
            f"/callback?code={FAKE_CODE}&state={MISMATCHED_STATE}",
            lambda status, message: None,
        )
        with pytest.raises(cmz.AuthorizationError) as rejected:
            consent.wait_for_callback()
    finally:
        consent.close()

    message = str(rejected.value)
    assert MISMATCHED_STATE not in message
    assert EXPECTED_STATE not in message
    assert FAKE_CODE not in message
    assert message == "the loopback redirect did not match this authorization request"


def test_mismatched_state_through_the_cli_fails_and_writes_nothing(tmp_path):
    path = tmp_path / "custody" / "oauth.json"
    storage = cmz.capacities_mcp_auth.FileTokenStorage(path)
    out = io.StringIO()

    code = _main(
        _flow(delivered_state=MISMATCHED_STATE),
        storage,
        out=out,
        lock_path=tmp_path / "jobs" / ".refresh-job.lock",
    )

    assert code == cmz.EXIT_FAILED
    assert path.exists() is False
    text = out.getvalue()
    assert "status: failed" in text
    assert MISMATCHED_STATE not in text
    assert FAKE_CODE not in text


def test_provider_error_redirect_is_refused(tmp_path):
    consent = cmz.LoopbackConsentServer(timeout=2.0, out=io.StringIO())
    consent.start()
    try:
        asyncio.run(consent.redirect_handler(AUTHORIZATION_URL))
        consent.handle_request(
            "/callback?error=access_denied&state=" + EXPECTED_STATE,
            lambda status, message: None,
        )
        with pytest.raises(cmz.AuthorizationError) as rejected:
            consent.wait_for_callback()
    finally:
        consent.close()

    assert str(rejected.value) == "authorization was not granted"


def test_callback_timeout_is_reported(tmp_path):
    consent = cmz.LoopbackConsentServer(timeout=0.01, out=io.StringIO())
    consent.start()
    try:
        asyncio.run(consent.redirect_handler(AUTHORIZATION_URL))
        with pytest.raises(cmz.AuthorizationError) as rejected:
            consent.wait_for_callback()
    finally:
        consent.close()

    assert str(rejected.value) == "the loopback redirect did not arrive before the timeout"


def test_unexpected_failure_prints_only_its_class_name(tmp_path):
    out = io.StringIO()
    storage = cmz.capacities_mcp_auth.FileTokenStorage(tmp_path / "c" / "oauth.json")

    async def flow(*_args, **_kwargs):
        raise RuntimeError(f"provider said: {FAKE_ACCESS}")

    code = _main(
        flow, storage, out=out, lock_path=tmp_path / "jobs" / ".refresh-job.lock"
    )

    assert code == cmz.EXIT_FAILED
    text = out.getvalue()
    assert "consent: failed (RuntimeError)" in text
    assert FAKE_ACCESS not in text
    assert "provider said" not in text


# ---------------------------------------------------------------------------
# Loopback listener
# ---------------------------------------------------------------------------

def test_loopback_listener_binds_127_0_0_1_only():
    consent = cmz.LoopbackConsentServer(out=io.StringIO())
    redirect_uri = consent.start()
    try:
        host, port = consent.address
        assert host == "127.0.0.1"
        assert port > 0
        assert redirect_uri == f"http://127.0.0.1:{port}/callback"
        assert "0.0.0.0" not in redirect_uri
        with pytest.raises(ValueError):
            cmz.LoopbackConsentServer(host="0.0.0.0")
        with pytest.raises(ValueError):
            cmz.LoopbackConsentServer(host="")
    finally:
        consent.close()


def test_redirect_handler_requires_a_state():
    consent = cmz.LoopbackConsentServer(out=io.StringIO())
    with pytest.raises(cmz.AuthorizationError) as rejected:
        asyncio.run(
            consent.redirect_handler("https://api.capacities.io/authorize?client_id=x")
        )
    assert str(rejected.value) == "the authorization URL carried no state"


def test_unrelated_paths_are_not_the_callback():
    consent = cmz.LoopbackConsentServer(out=io.StringIO())
    replies: list[tuple[int, str]] = []
    consent.handle_request("/favicon.ico", lambda s, m: replies.append((s, m)))
    assert replies == [(404, "Not the authorization redirect.")]


# ---------------------------------------------------------------------------
# Preflight: the refresh job lock
# ---------------------------------------------------------------------------

def test_refuses_while_the_refresh_job_lock_is_held(tmp_path):
    lock_path = tmp_path / "jobs" / ".refresh-job.lock"
    lock_path.parent.mkdir(parents=True)
    storage = cmz.capacities_mcp_auth.FileTokenStorage(tmp_path / "c" / "oauth.json")
    out = io.StringIO()
    called: list[int] = []

    def flow(*_args, **_kwargs):
        called.append(1)
        raise AssertionError("the flow must not run while a job holds the lock")

    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        import fcntl

        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert cmz.job_lock_is_held(lock_path) is True
        code = _main(flow, storage, out=out, lock_path=lock_path)
    finally:
        os.close(descriptor)

    assert code == cmz.EXIT_PREFLIGHT
    assert called == []
    assert (tmp_path / "c" / "oauth.json").exists() is False
    assert "preflight: a Capacities refresh job is running" in out.getvalue()
    assert "status: refused" in out.getvalue()
    # The lock is released again, so the same probe reports free.
    assert cmz.job_lock_is_held(lock_path) is False


def test_absent_lock_file_means_no_job_is_running(tmp_path):
    assert cmz.job_lock_is_held(tmp_path / "jobs" / ".refresh-job.lock") is False
    # The probe is read-only: it never creates the file or its directory.
    assert (tmp_path / "jobs").exists() is False


def test_unresolvable_lock_path_refuses(tmp_path):
    storage = cmz.capacities_mcp_auth.FileTokenStorage(tmp_path / "c" / "oauth.json")
    out = io.StringIO()

    code = _main(_flow(), storage, out=out, lock_path=None)

    assert code == cmz.EXIT_PREFLIGHT
    assert "preflight: the refresh job lock could not be located" in out.getvalue()
    assert (tmp_path / "c" / "oauth.json").exists() is False


def test_job_lock_path_matches_the_coordinator_contract(monkeypatch):
    path = cmz.refresh_job_lock_path()
    assert path is not None
    assert path.name == ".refresh-job.lock"
    assert path.parent.name == "jobs"


# ---------------------------------------------------------------------------
# Provider construction: metadata preset, scope, tools
# ---------------------------------------------------------------------------

def test_consent_presets_metadata_and_counts_tools_without_network(tmp_path):
    storage = cmz.capacities_mcp_auth.FileTokenStorage(tmp_path / "c" / "oauth.json")
    out = io.StringIO()
    consent = cmz.LoopbackConsentServer(timeout=5.0, out=out)
    consent.start()
    captured: dict[str, object] = {}

    @contextlib.asynccontextmanager
    async def opener(auth, *, timeout):
        captured["auth"] = auth
        captured["timeout"] = timeout

        class Session:
            async def list_tools(self):
                return types.SimpleNamespace(tools=[object(), object(), object()])

        yield Session()

    redirect_uri = consent.redirect_uri
    try:
        count = asyncio.run(
            cmz.run_consent(
                storage,
                consent,
                metadata_loader=lambda: dict(METADATA_DOCUMENT),
                session_opener=opener,
                timeout=11.0,
            )
        )
    finally:
        consent.close()

    assert count == 3
    assert captured["timeout"] == 11.0
    auth = captured["auth"]
    # The pitfall: without this the library refreshes against /token and 404s.
    assert str(auth.context.oauth_metadata.token_endpoint) == (
        "https://api.capacities.io/oauth/token"
    )
    assert auth.context.client_metadata.scope == cmz.REQUESTED_SCOPE
    assert str(auth.context.client_metadata.redirect_uris[0]) == redirect_uri
    assert auth.context.client_metadata.token_endpoint_auth_method == "none"


def test_metadata_failures_are_fixed_errors():
    def broken() -> dict:
        raise RuntimeError(f"cannot reach {FAKE_ACCESS}")

    with pytest.raises(cmz.AuthorizationError) as unavailable:
        cmz.load_authorization_server_metadata(broken)
    assert str(unavailable.value) == "the authorization server metadata is unavailable"
    assert FAKE_ACCESS not in str(unavailable.value)

    with pytest.raises(cmz.AuthorizationError) as unusable:
        cmz.load_authorization_server_metadata(lambda: {"issuer": "not-a-url"})
    assert str(unusable.value) == "the authorization server metadata is not usable"
    assert FAKE_ACCESS not in str(unusable.value)


def test_no_tools_listed_fails_verification(tmp_path):
    storage = cmz.capacities_mcp_auth.FileTokenStorage(tmp_path / "c" / "oauth.json")
    out = io.StringIO()
    consent = cmz.LoopbackConsentServer(timeout=5.0, out=out)
    consent.start()

    @contextlib.asynccontextmanager
    async def opener(auth, *, timeout):
        class Session:
            async def list_tools(self):
                return types.SimpleNamespace(tools=[])

        yield Session()

    try:
        count = asyncio.run(
            cmz.run_consent(
                storage,
                consent,
                metadata_loader=lambda: dict(METADATA_DOCUMENT),
                session_opener=opener,
            )
        )
    finally:
        consent.close()

    assert count == 0


# ---------------------------------------------------------------------------
# Log hygiene (KTD9)
# ---------------------------------------------------------------------------

@pytest.fixture
def sanitized_caplog(caplog):
    """caplog with the production filter on its handler, for this test only."""
    secret_free = cmz.SecretFreeLogFilter()
    caplog.handler.addFilter(secret_free)
    try:
        yield caplog
    finally:
        caplog.handler.removeFilter(secret_free)


def test_library_oauth_errors_never_reach_the_output(sanitized_caplog):
    caplog = sanitized_caplog
    cmz.install_secret_free_logging()
    logger = logging.getLogger("mcp.client.auth.oauth2")
    handler = next(
        item
        for item in logging.getLogger("mcp").handlers
        if isinstance(item, cmz._SecretFreeHandler)
    )
    buffer = io.StringIO()
    original = handler.stream
    handler.stream = buffer
    try:
        try:
            raise cmz.AuthorizationError(
                f"State parameter mismatch: {MISMATCHED_STATE}"
            )
        except cmz.AuthorizationError:
            logger.exception("OAuth flow error")
        logger.warning("Token exchange failed (400): access_token=%s", FAKE_ACCESS)
    finally:
        handler.stream = original

    text = buffer.getvalue()
    assert FAKE_ACCESS not in text
    assert MISMATCHED_STATE not in text
    assert "Traceback" not in text
    # The mechanism: nothing the library logs propagates to a root handler.
    assert logging.getLogger("mcp").propagate is False
    assert FAKE_ACCESS not in caplog.text
    assert MISMATCHED_STATE not in caplog.text


def test_filter_drops_tracebacks_and_redacts_token_shaped_messages():
    secret_free = cmz.SecretFreeLogFilter()

    with_traceback = logging.LogRecord(
        "mcp.client.auth.oauth2", logging.ERROR, __file__, 1, "boom", (), None
    )
    with_traceback.exc_info = (ValueError, ValueError(FAKE_CODE), None)
    assert secret_free.filter(with_traceback) is False

    message = logging.LogRecord(
        "mcp.client.auth.oauth2",
        logging.WARNING,
        __file__,
        1,
        "Token refresh failed: refresh_token=%s",
        (FAKE_REFRESH,),
        None,
    )
    assert secret_free.filter(message) is True
    assert FAKE_REFRESH not in message.getMessage()
    assert message.getMessage() == "<redacted>"


def test_logging_installation_is_quiet_and_filtered():
    cmz.install_secret_free_logging()
    for name in ("mcp", "mcp.client.auth"):
        logger = logging.getLogger(name)
        assert logger.level == logging.WARNING
        assert any(isinstance(item, cmz.SecretFreeLogFilter) for item in logger.filters)
    handlers = logging.getLogger("mcp").handlers
    ours = [item for item in handlers if isinstance(item, cmz._SecretFreeHandler)]
    assert len(ours) == 1
    assert any(isinstance(item, cmz.SecretFreeLogFilter) for item in ours[0].filters)
    # A foreign handler (a host process, a test harness) is never modified: a
    # filter on a handler it shares with other loggers would rewrite their output.
    foreign = [item for item in handlers if item not in ours]
    assert not any(
        isinstance(flt, cmz.SecretFreeLogFilter)
        for item in foreign
        for flt in item.filters
    )
    # Installing twice does not stack a second handler.
    cmz.install_secret_free_logging()
    assert logging.getLogger("mcp").handlers == handlers


# ---------------------------------------------------------------------------
# Import graph: the server never imports this module
# ---------------------------------------------------------------------------

SERVER_SOURCES = ("main.py", "capacities_refresh.py", "capacities_builder.py")


def _imported_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.add(node.module)
            for alias in node.names:
                names.add(alias.name)
    return names


@pytest.mark.parametrize("source", SERVER_SOURCES)
def test_server_sources_do_not_import_the_authorize_cli(source):
    names = _imported_names(APP_DIR / source)
    assert "capacities_mcp_authorize" not in names
    assert not any(name.startswith("capacities_mcp_authorize.") for name in names)


def test_importing_the_server_never_loads_the_authorize_cli(tmp_path):
    script = (
        "import os, sys\n"
        "os.environ['TDTB_HOME'] = sys.argv[2]\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "import capacities_builder, capacities_refresh, main\n"
        "print('capacities_mcp_authorize' in sys.modules)\n"
    )
    completed = subprocess.run(
        [sys.executable, "-I", "-c", script, str(APP_DIR), str(tmp_path)],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert completed.returncode == 0, completed.stderr[-2000:]
    assert completed.stdout.strip().splitlines()[-1] == "False"


def test_reconsent_discards_previous_custody(tmp_path):
    path = tmp_path / "custody" / "oauth.json"
    path.parent.mkdir(mode=0o700)
    path.write_text("{}")
    path.chmod(0o600)
    storage = cmz.capacities_mcp_auth.FileTokenStorage(path)
    cmz.discard_previous_custody(storage)
    assert not path.exists()
    cmz.discard_previous_custody(storage)  # already gone: no error
