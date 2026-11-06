"""capacities_mcp_auth.py — machine-local OAuth custody for Capacities MCP.

The hosted Capacities MCP endpoint authenticates with OAuth (scope
``mcp:read mcp:write``), not with the REST bearer token, and ``mcp``'s
``OAuthClientProvider`` persists through a :class:`mcp.client.auth.TokenStorage`
it is handed. This module is that storage: one owner-only JSON document, so a
headless server can refresh without a browser and without ever registering
itself a client.

The document is::

    {
      "version": 1,
      "client_info": {...},         # OAuthClientInformationFull, from consent
      "tokens": {...},              # OAuthToken: access + rotating refresh
      "access_expires_at": <epoch>  # absolute, or null when unknown
    }

``access_expires_at`` is stored because ``mcp==1.28.1`` restores tokens after a
restart but never restores ``context.token_expiry_time``: without an absolute
expiry an expired access token looks valid, the server answers 401, and the
library starts a full authorization flow instead of refreshing. The reader
pre-sets that context field from here.

Three properties are load-bearing:

* **Rotation is indivisible.** Capacities rotates the refresh token on every
  refresh, so a read-modify-write that a second writer interleaves, or that a
  crash interrupts between rotation and rename, strands the credential
  permanently. Writes take a process-local lock plus a POSIX advisory lock on
  a sibling ``.lock`` file, and land through an ``O_EXCL`` 0600 temp file that
  is fsynced and then renamed over the target.
* **Custody is owner-only.** The directory is 0700, the file 0600, and any
  looser mode — or a symlink, or a file owned by someone else — is rejected
  rather than read or repaired. This mirrors
  :func:`capacities_builder.load_capacities_token`.
* **Nothing leaks.** Every failure is a
  :class:`CapacitiesMcpAuthError` with a fixed message: no machine path, no
  token, no authorization code, no response body, and no ``repr`` carrying
  either. This module logs nothing at all.

An absent file is not an error: it means "MCP is not configured", so
``get_tokens`` and ``get_client_info`` return ``None`` and :meth:`is_configured`
is ``False``. A present but unusable file raises, because a configured
credential that has silently stopped working is indistinguishable from a
compromised one.

The only writer of ``client_info`` and of the initial tokens is the
operator-attended authorize step; this server-side module refreshes what is
already there and refuses to start otherwise (:meth:`require_custody`).
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import os
import stat
import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

#: Machine-local custody for the hosted Capacities MCP session: a sibling of
#: the REST credential slot, outside every repository, synced folder, and
#: backup tree, because it holds a long-lived refresh token.
DEFAULT_CUSTODY_PATH = (
    Path.home() / ".config" / "tdtb" / "capacities-mcp" / "oauth.json"
)

CUSTODY_VERSION = 1

_OWNER_ONLY_FILE_MODE = 0o600
_PRIVATE_DIR_MODE = 0o700
_LOCK_SUFFIX = ".lock"
#: Group and other permission bits. Any of them present is too loose.
_GROUP_OTHER_BITS = 0o077


class CapacitiesMcpAuthError(Exception):
    """Capacities MCP custody is absent where it is required, unusable, or
    loosely permissioned. The message never carries a machine path, a token,
    an authorization code, or a response body."""


def _current_uid() -> int:
    """The uid that must own the custody file. A seam, so tests can prove the
    rejection without ever creating a foreign-owned file."""
    return os.getuid()


def _resolve_path(path: str | Path | None) -> Path:
    return Path(path).expanduser() if path is not None else DEFAULT_CUSTODY_PATH


def _stored_client_info(document: dict[str, Any]) -> dict[str, Any] | None:
    """The stored client registration, when it names a client."""
    info = document.get("client_info")
    if not isinstance(info, dict):
        return None
    client_id = info.get("client_id")
    if not isinstance(client_id, str) or not client_id.strip():
        return None
    return info


def _stored_refresh_token(document: dict[str, Any]) -> str | None:
    """The stored refresh token, when it is a usable string."""
    tokens = document.get("tokens")
    value = tokens.get("refresh_token") if isinstance(tokens, dict) else None
    if not isinstance(value, str) or not value.strip():
        return None
    return value


def _fsync_directory(directory: Path) -> None:
    """Persist the rename itself.

    Without this a crash can resurrect the previous document, which after a
    rotation is the one holding the refresh token the server already replaced.
    Best-effort: a platform that cannot fsync a directory still gets the
    atomic rename."""
    with contextlib.suppress(OSError):
        handle = os.open(str(directory), os.O_RDONLY)
        try:
            os.fsync(handle)
        finally:
            os.close(handle)


def _atomic_write(path: Path, document: dict[str, Any]) -> None:
    """Replace ``path`` with ``document``.

    The temp file is created ``O_EXCL`` (via ``mkstemp``) in the same directory
    and forced to 0600, flushed and fsynced, then renamed over the target — the
    same pattern as the app-owned stores and :mod:`capacities_tag_titles`. The
    target is therefore never observed half-written and never carries the
    temp file's or the umask's mode."""
    payload = json.dumps(
        document, indent=2, sort_keys=True, ensure_ascii=True
    ).encode("utf-8")
    descriptor, temp = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        os.fchmod(descriptor, _OWNER_ONLY_FILE_MODE)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temp)
        raise
    _fsync_directory(path.parent)


class FileTokenStorage:
    """``mcp.client.auth.TokenStorage`` backed by one owner-only JSON document.

    ``path`` defaults to :data:`DEFAULT_CUSTODY_PATH`; an explicit path is what
    every test uses. Instances are cheap and stateless apart from the path, so
    the reader may build one per run.
    """

    def __init__(self, path: str | Path | None = None) -> None:
        self._path = _resolve_path(path)
        #: The process-local half of the write lock. The advisory lock on the
        #: ``.lock`` file is the cross-process half.
        self._thread_lock = threading.Lock()

    def __repr__(self) -> str:
        # Deliberately carries no path: a repr reaches logs and tracebacks.
        return "<FileTokenStorage>"

    @property
    def path(self) -> Path:
        return self._path

    @property
    def lock_path(self) -> Path:
        return self._path.with_name(self._path.name + _LOCK_SUFFIX)

    # ------------------------------------------------------------------
    # TokenStorage protocol
    # ------------------------------------------------------------------

    async def get_tokens(self) -> OAuthToken | None:
        """The stored tokens, or ``None`` when custody is not configured."""
        stored = self._read().get("tokens")
        if not isinstance(stored, dict):
            return None
        try:
            return OAuthToken.model_validate(stored)
        except ValueError as exc:
            raise CapacitiesMcpAuthError(
                "stored Capacities MCP tokens are not a readable OAuth token"
            ) from exc

    async def set_tokens(self, tokens: OAuthToken) -> None:
        """Store ``tokens`` and the absolute access-token expiry they imply.

        A refresh response may omit ``refresh_token``. The stored one is then
        still the only way back in, so it survives the write: the two are
        merged rather than replaced."""
        payload = tokens.model_dump(mode="json", exclude_none=True)
        expires_in = tokens.expires_in
        issued_at = time.time()

        def apply(document: dict[str, Any]) -> None:
            if not payload.get("refresh_token"):
                previous = _stored_refresh_token(document)
                if previous is not None:
                    payload["refresh_token"] = previous
            document["tokens"] = payload
            document["access_expires_at"] = (
                None if expires_in is None else issued_at + float(expires_in)
            )

        self._mutate(apply)

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        """The stored client registration, or ``None`` when not configured."""
        stored = self._read().get("client_info")
        if not isinstance(stored, dict):
            return None
        try:
            return OAuthClientInformationFull.model_validate(stored)
        except ValueError as exc:
            raise CapacitiesMcpAuthError(
                "stored Capacities MCP client information is not readable"
            ) from exc

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        """Store the client registration an attended consent produced."""
        payload = client_info.model_dump(mode="json", exclude_none=True)

        def apply(document: dict[str, Any]) -> None:
            document["client_info"] = payload

        self._mutate(apply)

    # ------------------------------------------------------------------
    # Custody reads
    # ------------------------------------------------------------------

    def access_expires_at(self) -> float | None:
        """The absolute epoch second the stored access token expires.

        ``None`` when custody is not configured or the provider never stated
        an ``expires_in``. The reader uses this to pre-set
        ``context.token_expiry_time`` before the first request."""
        value = self._read().get("access_expires_at")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return float(value)

    def is_configured(self) -> bool:
        """True when a usable document holds a client registration and a
        refresh token — the two things a headless refresh needs.

        An unreadable or loosely permissioned file reports ``False`` here: this
        is the gate a caller checks to decide between MCP and the REST
        fallback, and it must never raise into that decision.
        :meth:`require_custody` is the raising form."""
        try:
            document = self._read()
        except CapacitiesMcpAuthError:
            return False
        return (
            _stored_client_info(document) is not None
            and _stored_refresh_token(document) is not None
        )

    def require_custody(self) -> None:
        """Refuse to start without a stored registration and refresh token.

        Without client info ``mcp==1.28.1`` performs dynamic client
        registration when the server answers 401, and without a redirect
        handler it then raises out of the authorization flow. Both are
        operator actions, never server-side ones."""
        document = self._read()
        if _stored_client_info(document) is None:
            raise CapacitiesMcpAuthError(
                "no stored Capacities MCP client registration; "
                "run the one-time authorize step"
            )
        if _stored_refresh_token(document) is None:
            raise CapacitiesMcpAuthError(
                "no stored Capacities MCP refresh token; "
                "run the one-time authorize step"
            )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _read(self) -> dict[str, Any]:
        """The document, or ``{}`` when the file is absent.

        Never creates, repairs, or chmods anything: an unusable file raises
        instead, so the operator sees it."""
        try:
            link_info = os.lstat(self._path)
        except FileNotFoundError:
            return {}
        except OSError as exc:
            raise CapacitiesMcpAuthError(
                "Capacities MCP custody file is unreadable"
            ) from exc
        if stat.S_ISLNK(link_info.st_mode):
            raise CapacitiesMcpAuthError(
                "Capacities MCP custody file must not be a symbolic link"
            )
        self._check_directory()

        try:
            descriptor = os.open(
                self._path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            )
        except OSError as exc:
            raise CapacitiesMcpAuthError(
                "Capacities MCP custody file is unreadable"
            ) from exc
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode):
                raise CapacitiesMcpAuthError(
                    "Capacities MCP custody path is not a regular file"
                )
            if info.st_uid != _current_uid():
                raise CapacitiesMcpAuthError(
                    "Capacities MCP custody file is not owned by the current user"
                )
            mode = stat.S_IMODE(info.st_mode)
            if mode & _GROUP_OTHER_BITS:
                raise CapacitiesMcpAuthError(
                    f"Capacities MCP custody file has permissions {oct(mode)}; "
                    "expected 0600 or stricter"
                )
            with os.fdopen(descriptor, "rb") as handle:
                descriptor = -1
                raw = handle.read()
        finally:
            if descriptor >= 0:
                with contextlib.suppress(OSError):
                    os.close(descriptor)

        try:
            document = json.loads(raw)
        except ValueError as exc:
            raise CapacitiesMcpAuthError(
                "Capacities MCP custody document is not readable JSON"
            ) from exc
        if not isinstance(document, dict):
            raise CapacitiesMcpAuthError(
                "Capacities MCP custody document is not a JSON object"
            )
        if document.get("version") != CUSTODY_VERSION:
            raise CapacitiesMcpAuthError(
                "Capacities MCP custody document has an unsupported version"
            )
        return document

    def _mutate(self, apply: Callable[[dict[str, Any]], None]) -> None:
        """Read, apply, and atomically write the document under both locks."""
        with self._thread_lock:
            self._ensure_directory()
            with self._exclusive_lock():
                document = self._read()
                apply(document)
                document["version"] = CUSTODY_VERSION
                _atomic_write(self._path, document)

    @contextlib.contextmanager
    def _exclusive_lock(self):
        """Serialize the whole read-modify-write, across threads and processes.

        Advisory ``flock`` on a sibling ``.lock`` file: released by the kernel
        if the writer dies, so a crash never leaves custody unwritable."""
        descriptor = os.open(
            self.lock_path, os.O_RDWR | os.O_CREAT, _OWNER_ONLY_FILE_MODE
        )
        try:
            os.fchmod(descriptor, _OWNER_ONLY_FILE_MODE)
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            with contextlib.suppress(OSError):
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            with contextlib.suppress(OSError):
                os.close(descriptor)

    def _ensure_directory(self) -> None:
        """Create any missing ancestor owner-only, then validate the mode.

        Only directories this call creates are hardened, so a caller-supplied
        parent such as ``~/.config`` is never rewritten; an existing directory
        that is readable by group or others is rejected, not fixed."""
        missing: list[Path] = []
        current = self._path.parent
        while not current.exists():
            missing.append(current)
            parent = current.parent
            if parent == current:
                break
            current = parent
        for target in reversed(missing):
            try:
                os.mkdir(target, _PRIVATE_DIR_MODE)
            except FileExistsError:
                continue  # a concurrent creator owns that directory's mode
            os.chmod(target, _PRIVATE_DIR_MODE)
        self._check_directory()

    def _check_directory(self) -> None:
        """Reject a custody directory that group or others can reach."""
        try:
            info = self._path.parent.stat()
        except OSError as exc:
            raise CapacitiesMcpAuthError(
                "Capacities MCP custody directory is unusable"
            ) from exc
        if not stat.S_ISDIR(info.st_mode):
            raise CapacitiesMcpAuthError(
                "Capacities MCP custody directory is not a directory"
            )
        mode = stat.S_IMODE(info.st_mode)
        if mode & _GROUP_OTHER_BITS:
            raise CapacitiesMcpAuthError(
                f"Capacities MCP custody directory has permissions {oct(mode)}; "
                "expected 0700 or stricter"
            )
