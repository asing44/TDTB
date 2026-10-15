"""Offline tests for the ``openrouter-timing`` diagnostic lines.

An unexplained 240s SEQUENCE no-response (the request reached OpenRouter within
1.9s and was marked FAILED with no stored generation) is only diagnosable if a
single call's wall time can be attributed to one stage: local pre-send
(DNS/TCP/TLS, or a proxy), in-transit, or response read. These tests pin the
free, non-secret timing lines that make that attribution possible.

Everything here is offline: the one client seam is stubbed with event hooks and
synthetic httpcore trace names, no provider is contacted, and no credential,
prompt, or response body may appear in the captured output.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import judgment as j  # noqa: E402

MODEL = "deepseek/deepseek-v4.1-flash"
PREFIX = "openrouter-timing"
# The six phases the design requires, in httpcore's emitted order.
TRACE_EVENTS = [
    ("connection.connect_tcp.started", {}),
    ("connection.connect_tcp.complete", {"return_value": object()}),
    ("connection.start_tls.started", {}),
    ("connection.start_tls.complete", {"return_value": object()}),
    ("http11.send_request_headers.started", {}),
    ("http11.send_request_headers.complete", {}),
    ("http11.receive_response_headers.started", {}),
    (
        "http11.receive_response_headers.complete",
        {"return_value": ("HTTP/1.1", 200, "OK", [("x-generation-id", "gen-1")])},
    ),
]


def _prep(monkeypatch, caplog, *, params=None):
    caplog.set_level(logging.INFO, logger="tdtb.judgment")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.setattr(j, "OPENROUTER_MODEL", MODEL)
    monkeypatch.setattr(j, "JUDGMENT_PARAMS", params if params is not None else {})


def _lines(caplog):
    return [r.getMessage() for r in caplog.records if r.getMessage().startswith(PREFIX)]


def _phases(caplog):
    return [line.split()[1] for line in _lines(caplog)]


class _FakeRequest:
    def __init__(self):
        self.extensions = {}


class _FakeResp:
    def __init__(self, *, status=200, content=b"", payload=None, headers=None):
        self.status_code = status
        self.is_error = status >= 400
        self.content = content
        self.headers = headers or {}
        self._payload = payload

    @property
    def text(self):
        return self.content.decode("utf-8", "replace")

    def json(self):
        return self._payload


async def _fire_hooks(hooks, req):
    for hook in hooks["request"]:
        await hook(req)


async def _fire_trace(req):
    trace = req.extensions.get("trace")
    if trace is None:
        return
    for name, info in TRACE_EVENTS:
        await trace(name, info)


def _install_nonstream(monkeypatch, *, payload, content=b"", sleep=0.0, headers=None):
    cap = {}
    resp_headers = headers or {
        "x-generation-id": "gen-abc",
        "x-request-id": "req-xyz",
    }

    class FakeClient:
        def __init__(self, *a, **kw):
            cap["event_hooks"] = kw.get("event_hooks")
            cap["timeout"] = kw.get("timeout")

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, headers=None):
            cap["url"] = url
            cap["payload"] = json
            cap["headers"] = headers
            hooks = cap["event_hooks"]
            req = _FakeRequest()
            await _fire_hooks(hooks, req)
            await _fire_trace(req)
            if sleep:
                await asyncio.sleep(sleep)
            resp = _FakeResp(
                content=content,
                payload=payload,
                headers=resp_headers,
            )
            for hook in hooks["response"]:
                await hook(resp)
            return resp

    monkeypatch.setattr(j.httpx, "AsyncClient", FakeClient)
    return cap


def _install_stream(monkeypatch, lines, *, status=200, body=b"", sleep=0.0, headers=None):
    cap = {}

    class FakeStreamResp:
        def __init__(self):
            self.status_code = status
            self.is_error = status >= 400
            self.headers = headers or {}

        async def aread(self):
            return body

        async def aiter_lines(self):
            if sleep:
                await asyncio.sleep(sleep)
            for line in lines:
                yield line

    class FakeStreamCM:
        async def __aenter__(self):
            hooks = cap["event_hooks"]
            req = _FakeRequest()
            await _fire_hooks(hooks, req)
            await _fire_trace(req)
            resp = FakeStreamResp()
            for hook in hooks["response"]:
                await hook(resp)
            return resp

        async def __aexit__(self, *a):
            return False

    class FakeClient:
        def __init__(self, *a, **kw):
            cap["event_hooks"] = kw.get("event_hooks")

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def stream(self, method, url, json=None, headers=None):
            cap["payload"] = json
            cap["headers"] = headers
            return FakeStreamCM()

    monkeypatch.setattr(j.httpx, "AsyncClient", FakeClient)
    return cap


def _success_payload():
    return {"choices": [{"message": {"content": '{"ok": 1}'}}]}


# ---------------------------------------------------------------------------
# Successful call: ordered lines and required fields
# ---------------------------------------------------------------------------

def test_success_lines_are_emitted_in_order(monkeypatch, caplog):
    _prep(monkeypatch, caplog)
    payload = _success_payload()
    body = json.dumps(payload).encode()
    _install_nonstream(monkeypatch, payload=payload, content=body)

    out = asyncio.run(j._run_query_openrouter(j.SEQUENCE_SYSTEM_PROMPT, "u"))

    assert out == '{"ok": 1}'
    assert _phases(caplog) == [
        "call_start",
        "request_sent",
        "connect_tcp_started",
        "connect_tcp_complete",
        "start_tls_started",
        "start_tls_complete",
        "send_request_headers_complete",
        "receive_response_headers_complete",
        "response_headers",
        "response_complete",
    ]


def test_every_line_carries_timestamp_model_and_ceiling(monkeypatch, caplog):
    _prep(monkeypatch, caplog)
    payload = _success_payload()
    _install_nonstream(monkeypatch, payload=payload, content=json.dumps(payload).encode())

    asyncio.run(j._run_query_openrouter(j.SEQUENCE_SYSTEM_PROMPT, "u"))

    lines = _lines(caplog)
    assert lines
    for line in lines:
        assert "ts=" in line and "T" in line and "Z" in line
        assert "elapsed_ms=" in line
        assert f"model={MODEL}" in line
        assert f"ceiling={j.SEQUENCE_QUERY_TIMEOUT_S:g}" in line


def test_header_ids_status_and_body_bytes_are_recorded(monkeypatch, caplog):
    _prep(monkeypatch, caplog)
    payload = _success_payload()
    body = json.dumps(payload).encode()
    _install_nonstream(monkeypatch, payload=payload, content=body)

    asyncio.run(j._run_query_openrouter(j.SEQUENCE_SYSTEM_PROMPT, "u"))

    blob = "\n".join(_lines(caplog))
    assert "receive_response_headers_complete" in blob and "status=200" in blob
    assert "generation_id=gen-abc" in blob
    assert "request_id=req-xyz" in blob
    assert "response_complete" in blob and f"bytes={len(body)}" in blob


def test_call_start_reports_proxy_env_as_boolean_only(monkeypatch, caplog):
    _prep(monkeypatch, caplog)
    monkeypatch.delenv("HTTPS_PROXY", raising=False)
    monkeypatch.delenv("ALL_PROXY", raising=False)
    monkeypatch.delenv("https_proxy", raising=False)
    monkeypatch.delenv("all_proxy", raising=False)
    payload = _success_payload()
    _install_nonstream(monkeypatch, payload=payload, content=json.dumps(payload).encode())

    asyncio.run(j._run_query_openrouter(j.SEQUENCE_SYSTEM_PROMPT, "u"))

    call_start = _lines(caplog)[0]
    assert call_start.startswith(f"{PREFIX} call_start")
    assert "proxy_env=false" in call_start


# ---------------------------------------------------------------------------
# Stream mode
# ---------------------------------------------------------------------------

def test_stream_logs_first_body_byte_before_complete(monkeypatch, caplog):
    _prep(monkeypatch, caplog, params={"stream": True})
    stream_lines = [
        'data: {"choices": [{"delta": {"content": "ok"}}]}',
        "data: [DONE]",
    ]
    _install_stream(monkeypatch, stream_lines, headers={"x-generation-id": "gen-s"})

    out = asyncio.run(j._run_query_openrouter(j.SEQUENCE_SYSTEM_PROMPT, "u"))

    assert out == "ok"
    phases = _phases(caplog)
    assert "first_body_byte" in phases
    assert phases.index("first_body_byte") < phases.index("response_complete")
    assert phases[-1] == "response_complete"


# ---------------------------------------------------------------------------
# Delivery: the lines must survive uvicorn's default logging configuration
# ---------------------------------------------------------------------------
# Under uvicorn/launchd the app config leaves the root logger at WARNING with
# no handlers, so an INFO record is dropped before ``logging.lastResort`` ever
# sees it. caplog injects a level-0 handler and would hide that, so this test
# reproduces the real configuration and reads the real stderr stream.

@contextlib.contextmanager
def _uvicorn_default_logging():
    root = logging.getLogger()
    saved_handlers = root.handlers[:]
    saved_level = root.level
    root.handlers = []
    root.setLevel(logging.WARNING)
    try:
        yield
    finally:
        root.handlers = saved_handlers
        root.setLevel(saved_level)


def test_timing_lines_reach_stderr_under_root_warning_no_handlers(capsys):
    with _uvicorn_default_logging():
        j._OpenRouterTiming(MODEL, 240.0).log("delivery_probe")

    err = capsys.readouterr().err
    assert f"{PREFIX} delivery_probe" in err


# ---------------------------------------------------------------------------
# Timeout: last_phase must be the last COMPLETED progress phase
# ---------------------------------------------------------------------------
# A synthetic trace cannot reproduce the teardown that polluted the field:
# when the wall-clock waiter cancels, httpcore's http11 handler runs
# ``except BaseException`` and emits ``response_closed`` (and a ``.failed`` for
# the stage in flight) while the waiter unwinds. Real loopback listeners
# exercise that path with real httpcore traces.

_STALL_TIMEOUT_S = 0.6


class _LoopbackServer:
    """A real 127.0.0.1 HTTP/1.1 listener that stalls by scenario.

    ``scenario="silent"`` accepts the connection and answers nothing.
    ``scenario="headers_then_stall"`` reads the request and writes response
    headers with a Content-Length it never fills.
    """

    def __init__(self, scenario: str) -> None:
        self.scenario = scenario
        self.release = asyncio.Event()
        self._server = None
        self._tasks: set[asyncio.Task] = set()

    @property
    def url(self) -> str:
        port = self._server.sockets[0].getsockname()[1]
        return f"http://127.0.0.1:{port}/v1/chat/completions"

    async def _serve(self, reader, writer) -> None:
        try:
            if self.scenario == "silent":
                await self.release.wait()
            else:
                await asyncio.wait_for(reader.read(65536), timeout=2.0)
                writer.write(
                    b"HTTP/1.1 200 OK\r\n"
                    b"Content-Type: application/json\r\n"
                    b"Content-Length: 100\r\n\r\n"
                )
                await writer.drain()
                await self.release.wait()
        finally:
            writer.close()

    async def __aenter__(self):
        async def _on_connect(reader, writer):
            task = asyncio.current_task()
            self._tasks.add(task)
            try:
                await self._serve(reader, writer)
            finally:
                self._tasks.discard(task)

        self._server = await asyncio.start_server(_on_connect, "127.0.0.1", 0)
        return self

    async def __aexit__(self, *exc):
        self.release.set()
        self._server.close()
        await self._server.wait_closed()
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)


def _probe_timeout(monkeypatch, caplog, scenario, *, stream):
    _prep(monkeypatch, caplog, params={"stream": stream})
    monkeypatch.setattr(j, "QUERY_TIMEOUT_S", _STALL_TIMEOUT_S)

    async def _run():
        async with _LoopbackServer(scenario) as server:
            monkeypatch.setattr(j, "OPENROUTER_URL", server.url)
            with pytest.raises(asyncio.TimeoutError):
                await j._run_query_openrouter(j.AUDIT_SYSTEM_PROMPT, "u")

    asyncio.run(_run())


def _timeout_line(caplog):
    last = _lines(caplog)[-1]
    assert last.startswith(f"{PREFIX} timeout")
    return last


def test_silent_server_last_phase_is_last_completed_send_stage(monkeypatch, caplog):
    _probe_timeout(monkeypatch, caplog, "silent", stream=False)

    last = _timeout_line(caplog)
    assert (
        "last_phase=send_request_headers_complete" in last
        or "last_phase=send_request_body_complete" in last
    )
    assert "response_closed" not in last


def test_headers_then_stalled_body_last_phase_is_response_headers(monkeypatch, caplog):
    _probe_timeout(monkeypatch, caplog, "headers_then_stall", stream=False)

    last = _timeout_line(caplog)
    assert "last_phase=receive_response_headers_complete" in last
    assert "response_closed" not in last


def test_headers_then_stalled_stream_last_phase_is_response_headers(monkeypatch, caplog):
    _probe_timeout(monkeypatch, caplog, "headers_then_stall", stream=True)

    last = _timeout_line(caplog)
    assert "last_phase=receive_response_headers_complete" in last
    assert "response_closed" not in last


# ---------------------------------------------------------------------------
# Safety: no secret, prompt, or body content
# ---------------------------------------------------------------------------

def test_no_secret_prompt_or_body_content_is_logged(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="tdtb.judgment")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-SECRETSENTINEL")
    monkeypatch.setattr(j, "OPENROUTER_MODEL", MODEL)
    monkeypatch.setattr(j, "JUDGMENT_PARAMS", {})
    payload = {"choices": [{"message": {"content": "BODYSENTINEL"}}]}
    _install_nonstream(
        monkeypatch, payload=payload, content=json.dumps(payload).encode()
    )

    asyncio.run(j._run_query_openrouter(j.SEQUENCE_SYSTEM_PROMPT, "PROMPTSENTINEL"))

    blob = "\n".join(_lines(caplog))
    assert blob
    for secret in ("SECRETSENTINEL", "PROMPTSENTINEL", "BODYSENTINEL"):
        assert secret not in blob
    assert "Authorization" not in blob
    assert "Bearer" not in blob


# ---------------------------------------------------------------------------
# Logging must never break the call; payload must be unchanged
# ---------------------------------------------------------------------------

def test_logging_failure_is_swallowed_and_call_still_succeeds(monkeypatch, caplog):
    _prep(monkeypatch, caplog)
    payload = _success_payload()
    _install_nonstream(monkeypatch, payload=payload, content=json.dumps(payload).encode())

    def _boom(*a, **kw):
        raise RuntimeError("log boom")

    monkeypatch.setattr(j.logger, "warning", _boom)

    out = asyncio.run(j._run_query_openrouter(j.SEQUENCE_SYSTEM_PROMPT, "u"))
    assert out == '{"ok": 1}'


def test_payload_and_timeout_are_unchanged_by_timing(monkeypatch, caplog):
    _prep(monkeypatch, caplog)
    payload = _success_payload()
    cap = _install_nonstream(
        monkeypatch, payload=payload, content=json.dumps(payload).encode()
    )

    asyncio.run(j._run_query_openrouter(j.SEQUENCE_SYSTEM_PROMPT, "u"))

    assert cap["payload"] == j._build_openrouter_payload(j.SEQUENCE_SYSTEM_PROMPT, "u")
    assert cap["timeout"] == j.SEQUENCE_QUERY_TIMEOUT_S
