"""Contract tests for the machine-local Capacities MCP token custody.

The MCP reader authenticates with OAuth, not with the REST bearer token, and
Capacities rotates the refresh token on every refresh. Custody is therefore one
owner-only JSON document that only an attended consent may create, that the
server only ever refreshes, and that must survive a crash or a second writer
without stranding the credential. These tests pin the four properties that
carry that weight: owner-only modes, atomic locked writes, refresh-token
retention, and errors that leak neither a path nor a token.

Every test uses ``tmp_path``; nothing here reads or writes the operator's real
custody file.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

sys.path.insert(0, str(Path(__file__).parent.parent))

import capacities_mcp_auth as cma  # noqa: E402

APP_DIR = Path(__file__).resolve().parent.parent

REDIRECT_URI = "http://127.0.0.1:8765/callback"
FAKE_ACCESS = "fake-access-token-DO-NOT-LEAK"
FAKE_REFRESH = "fake-refresh-token-DO-NOT-LEAK"
FAKE_CLIENT_SECRET = "fake-client-secret-DO-NOT-LEAK"
FAKE_CODE = "fake-authorization-code-DO-NOT-LEAK"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(coro):
    return asyncio.run(coro)


def _tokens(
    *,
    access: str = FAKE_ACCESS,
    refresh: str | None = FAKE_REFRESH,
    expires_in: int | None = 3600,
) -> OAuthToken:
    return OAuthToken(
        access_token=access, expires_in=expires_in, refresh_token=refresh
    )


def _client_info(client_id: str = "client-1") -> OAuthClientInformationFull:
    return OAuthClientInformationFull(
        redirect_uris=[REDIRECT_URI],
        token_endpoint_auth_method="none",
        client_id=client_id,
        client_secret=FAKE_CLIENT_SECRET,
    )


def _document(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_raw(path: Path, document: dict) -> None:
    """Place a document without going through the module under test."""
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(json.dumps(document).encode("utf-8"))
    os.chmod(path, 0o600)


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


# ---------------------------------------------------------------------------
# Round trip
# ---------------------------------------------------------------------------

def test_round_trip_through_asyncio_run(tmp_path):
    path = tmp_path / "custody" / "capacities-mcp-oauth.json"
    storage = cma.FileTokenStorage(path)

    assert storage.is_configured() is False
    _run(storage.set_client_info(_client_info()))
    _run(storage.set_tokens(_tokens()))

    stored_info = _run(storage.get_client_info())
    stored_tokens = _run(storage.get_tokens())
    assert stored_info is not None and stored_tokens is not None
    assert stored_info.client_id == "client-1"
    assert stored_info.client_secret == FAKE_CLIENT_SECRET
    assert stored_tokens.access_token == FAKE_ACCESS
    assert stored_tokens.refresh_token == FAKE_REFRESH
    assert stored_tokens.token_type == "Bearer"
    assert storage.is_configured() is True
    storage.require_custody()

    document = _document(path)
    assert document["version"] == cma.CUSTODY_VERSION
    assert set(document) == {"version", "client_info", "tokens", "access_expires_at"}


def test_default_path_is_machine_local(tmp_path):
    # The default is a sibling of the REST credential slot and never a repo
    # path; it is only ever resolved, never read here.
    assert cma.DEFAULT_CUSTODY_PATH.name == "capacities-mcp-oauth.json"
    assert cma.DEFAULT_CUSTODY_PATH.parent.name == "tdtb"
    assert str(cma.DEFAULT_CUSTODY_PATH).startswith(str(Path.home()))
    assert repr(cma.FileTokenStorage()) == "<FileTokenStorage>"
    assert str(tmp_path) not in repr(cma.FileTokenStorage(tmp_path / "x.json"))


# ---------------------------------------------------------------------------
# Modes
# ---------------------------------------------------------------------------

def test_file_is_0600_and_directory_0700(tmp_path):
    path = tmp_path / "private" / "custody.json"
    storage = cma.FileTokenStorage(path)
    _run(storage.set_tokens(_tokens()))

    assert _mode(path) == 0o600
    assert _mode(path.parent) == 0o700
    # The advisory lock file is never looser than the document it guards.
    assert _mode(storage.lock_path) == 0o600


def test_loose_file_mode_is_rejected(tmp_path):
    path = tmp_path / "custody.json"
    _run(cma.FileTokenStorage(path).set_tokens(_tokens()))
    os.chmod(path, 0o644)

    with pytest.raises(cma.CapacitiesMcpAuthError) as reading:
        _run(cma.FileTokenStorage(path).get_tokens())
    assert "0600" in str(reading.value)

    with pytest.raises(cma.CapacitiesMcpAuthError):
        _run(cma.FileTokenStorage(path).set_tokens(_tokens(access="other")))
    # Rejected, not repaired: the operator's bytes and mode are untouched.
    assert _mode(path) == 0o644


def test_loose_directory_mode_is_rejected(tmp_path):
    directory = tmp_path / "loose"
    path = directory / "custody.json"
    _write_raw(path, {"version": 1, "tokens": {"access_token": FAKE_ACCESS}})
    os.chmod(directory, 0o755)

    with pytest.raises(cma.CapacitiesMcpAuthError) as reading:
        _run(cma.FileTokenStorage(path).get_tokens())
    assert "0700" in str(reading.value)

    # A write is refused for the same reason, and fixed nothing on the way out.
    with pytest.raises(cma.CapacitiesMcpAuthError):
        _run(cma.FileTokenStorage(path).set_tokens(_tokens()))
    assert _mode(directory) == 0o755


def test_symlink_is_rejected(tmp_path):
    real = tmp_path / "real.json"
    _run(cma.FileTokenStorage(real).set_tokens(_tokens()))
    link = tmp_path / "linked.json"
    link.symlink_to(real)

    with pytest.raises(cma.CapacitiesMcpAuthError) as reading:
        _run(cma.FileTokenStorage(link).get_tokens())
    assert "symbolic link" in str(reading.value)


def test_foreign_owned_file_is_rejected(tmp_path, monkeypatch):
    path = tmp_path / "custody.json"
    _run(cma.FileTokenStorage(path).set_tokens(_tokens()))
    monkeypatch.setattr(cma, "_current_uid", lambda: os.getuid() + 1)

    with pytest.raises(cma.CapacitiesMcpAuthError) as reading:
        _run(cma.FileTokenStorage(path).get_tokens())
    assert "owned" in str(reading.value)


# ---------------------------------------------------------------------------
# Rotation-safe writes
# ---------------------------------------------------------------------------

def test_refresh_without_refresh_token_keeps_the_stored_one(tmp_path):
    path = tmp_path / "custody.json"
    storage = cma.FileTokenStorage(path)
    _run(storage.set_client_info(_client_info()))
    _run(storage.set_tokens(_tokens(refresh="first-refresh")))

    # A refresh response that omits refresh_token must leave custody usable.
    _run(storage.set_tokens(_tokens(access="second-access", refresh=None)))

    stored = _run(storage.get_tokens())
    assert stored is not None
    assert stored.access_token == "second-access"
    assert stored.refresh_token == "first-refresh"
    assert storage.is_configured() is True

    # A rotated refresh token replaces the old one.
    _run(storage.set_tokens(_tokens(access="third", refresh="rotated")))
    stored = _run(storage.get_tokens())
    assert stored is not None and stored.refresh_token == "rotated"
    assert _document(path)["tokens"]["refresh_token"] == "rotated"


def test_access_expires_at_is_persisted(tmp_path):
    path = tmp_path / "custody.json"
    storage = cma.FileTokenStorage(path)
    before = time.time()
    _run(storage.set_tokens(_tokens(expires_in=3600)))
    after = time.time()

    expires_at = storage.access_expires_at()
    assert expires_at is not None
    assert before + 3600 <= expires_at <= after + 3600
    assert isinstance(_document(path)["access_expires_at"], (int, float))

    # A later read in a new instance sees the same absolute instant, which is
    # what the reader pre-sets context.token_expiry_time from.
    assert cma.FileTokenStorage(path).access_expires_at() == expires_at

    # No stated lifetime means no absolute expiry, never a guessed one.
    _run(storage.set_tokens(_tokens(access="short", expires_in=None)))
    assert storage.access_expires_at() is None


def test_concurrent_writers_leave_one_valid_document(tmp_path):
    path = tmp_path / "custody.json"
    storage = cma.FileTokenStorage(path)
    _run(storage.set_client_info(_client_info()))

    script = (
        "import asyncio, sys\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "from mcp.shared.auth import OAuthToken\n"
        "import capacities_mcp_auth as cma\n"
        "\n"
        "storage = cma.FileTokenStorage(sys.argv[2])\n"
        "prefix, count = sys.argv[3], int(sys.argv[4])\n"
        "\n"
        "async def main():\n"
        "    for index in range(count):\n"
        "        await storage.set_tokens(\n"
        "            OAuthToken(\n"
        "                access_token=f'{prefix}-access-{index}',\n"
        "                expires_in=3600,\n"
        "                refresh_token=f'{prefix}-refresh-{index}',\n"
        "            )\n"
        "        )\n"
        "\n"
        "asyncio.run(main())\n"
    )
    writers = [
        subprocess.Popen(
            [sys.executable, "-c", script, str(APP_DIR), str(path), prefix, "25"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        for prefix in ("alpha", "beta")
    ]
    for writer in writers:
        _, error = writer.communicate(timeout=180)
        assert writer.returncode == 0, error.decode("utf-8", "replace")

    document = _document(path)  # the file is never observed half-written
    assert document["version"] == cma.CUSTODY_VERSION
    stored = _run(storage.get_tokens())
    assert stored is not None
    # One writer's whole record survived: no merged or interleaved token pair.
    assert stored.access_token is not None and stored.refresh_token is not None
    access_prefix, refresh_prefix = (
        stored.access_token.split("-")[0],
        stored.refresh_token.split("-")[0],
    )
    assert access_prefix == refresh_prefix
    assert access_prefix in {"alpha", "beta"}
    assert stored.refresh_token.endswith("-24")
    # Client info written before the race is preserved by the read-modify-write.
    assert document["client_info"]["client_id"] == "client-1"
    assert _mode(path) == 0o600
    assert not [p for p in path.parent.iterdir() if p.name.endswith(".tmp")]


# ---------------------------------------------------------------------------
# Not configured
# ---------------------------------------------------------------------------

def test_missing_file_is_not_configured(tmp_path):
    path = tmp_path / "absent" / "custody.json"
    storage = cma.FileTokenStorage(path)

    assert _run(storage.get_tokens()) is None
    assert _run(storage.get_client_info()) is None
    assert storage.access_expires_at() is None
    assert storage.is_configured() is False
    with pytest.raises(cma.CapacitiesMcpAuthError) as reading:
        storage.require_custody()
    assert "no stored Capacities MCP client registration" in str(reading.value)
    # Reading a missing document creates nothing, not even the lock file.
    assert not path.parent.exists()


def test_client_registration_without_refresh_token_is_incomplete(tmp_path):
    path = tmp_path / "custody.json"
    _run(cma.FileTokenStorage(path).set_client_info(_client_info()))

    storage = cma.FileTokenStorage(path)
    assert storage.is_configured() is False
    _run(storage.set_tokens(_tokens(refresh=None)))
    assert storage.is_configured() is False
    with pytest.raises(cma.CapacitiesMcpAuthError) as reading:
        storage.require_custody()
    assert "no stored Capacities MCP refresh token" in str(reading.value)


# ---------------------------------------------------------------------------
# Leakage
# ---------------------------------------------------------------------------

def test_errors_and_reprs_leak_no_path_or_secret(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    directory = tmp_path / "secrets"
    path = directory / "custody.json"
    storage = cma.FileTokenStorage(path)
    _run(storage.set_client_info(_client_info()))
    _run(storage.set_tokens(_tokens()))

    messages: list[str] = []

    os.chmod(path, 0o644)
    for call in (
        lambda: _run(storage.get_tokens()),
        lambda: _run(storage.get_client_info()),
        storage.require_custody,
    ):
        with pytest.raises(cma.CapacitiesMcpAuthError) as caught:
            call()
        messages.append(str(caught.value))
    os.chmod(path, 0o600)

    # Corrupt but correctly permissioned: still a fixed, path-free message.
    path.write_text('{"tokens": {"access_token": "' + FAKE_ACCESS + '", ', encoding="utf-8")
    os.chmod(path, 0o600)
    with pytest.raises(cma.CapacitiesMcpAuthError) as caught:
        _run(storage.get_tokens())
    messages.append(str(caught.value))

    # An unreadable tokens record fails with a fixed message too.
    _write_raw(path, {"version": 1, "tokens": {"refresh_token": 5}})
    with pytest.raises(cma.CapacitiesMcpAuthError) as caught:
        _run(storage.get_tokens())
    messages.append(str(caught.value))

    seen = "\n".join(messages) + "\n" + repr(storage) + "\n" + caplog.text
    assert str(tmp_path) not in seen
    assert "custody.json" not in seen
    for secret in (FAKE_ACCESS, FAKE_REFRESH, FAKE_CLIENT_SECRET, FAKE_CODE):
        assert secret not in seen
