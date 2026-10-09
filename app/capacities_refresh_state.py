"""capacities_refresh_state.py — durable direct-refresh cache and snapshot store.

This module owns the persistence half of the direct Capacities refresh (U1):
a per-object content store and a separate complete-generation pointer. It
deliberately does no acquisition, pacing, or job coordination — U2 supplies
the enumeration and read evidence, and this store is the only place a
complete generation can be installed.

Contract (KTD2/KTD3):

* **No TTL on cached properties.** A valid cached object keeps its original
  ``content_read_at``; only an explicit re-read (a Rescan) replaces it. A
  read never renews a timestamp.
* **Per-object atomic writes.** Each object is its own versioned JSON file,
  written through the shared atomic-write primitive, so an interruption
  keeps every successful read.
* **A separate atomic complete-generation pointer.** The pointer is a single
  small document replaced atomically; a torn pointer is impossible.
* **Nothing is silently evicted.** Reaching the storage bound raises
  :class:`RefreshStateStorageError` and preserves both the existing objects
  and the prior generation.
* **Complete evidence only.** A generation installs only from a complete
  listing of every required type, a successful cached read for every member,
  and a current configuration revision; complete listing absence is the only
  removal evidence.
* **Namespace isolation.** Objects are keyed by provider origin + space +
  type + object; pointers are keyed by vault root + origin + space. Stored
  bytes for another scope are never served, and they are never overwritten by
  a blind install.

Machine-local only: the caller owns the root (``app_config.state_dir()`` via
``capacities_builder.refresh_state_dir``), so raw content never enters a
synced vault. Diagnostics never embed content, credentials, or absolute paths.
"""
from __future__ import annotations

import hashlib
import json
import math
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping
from urllib.parse import urlsplit

import capacities_cache_io

#: Schema versions are explicit so unusable bytes are detected, never guessed.
REFRESH_STATE_SCHEMA_VERSION = 1
OBJECT_SCHEMA_VERSION = 1
RUN_MARKER_SCHEMA_VERSION = 1
#: The legacy ``_ContentCache`` document version this module can read for an
#: optional, read-only import. It mirrors ``capacities_builder``'s constant
#: without importing the builder back (that would be an import cycle).
LEGACY_CONTENT_CACHE_SCHEMA_VERSION = 1

OBJECTS_DIRNAME = "objects"
SNAPSHOTS_DIRNAME = "snapshots"
RUNS_DIRNAME = "runs"
LOCK_FILENAME = "refresh-state.lock"

#: Storage bounds are ceilings, not eviction policies: exceeding one is a
#: visible failure that preserves the previous complete result.
DEFAULT_MAX_OBJECTS = 20000
DEFAULT_MAX_BYTES = 64 * 1024 * 1024

_SEPARATOR = "\x1f"


class RefreshStateError(Exception):
    """Base class for every durable refresh-state failure."""


class RefreshStateFormatError(RefreshStateError):
    """Stored bytes are present but cannot be trusted."""


class RefreshStateStorageError(RefreshStateError):
    """The storage bound is reached; nothing was evicted."""


class RefreshStateIncompleteError(RefreshStateError):
    """A complete generation cannot be proven from the supplied evidence."""


class SnapshotConflictError(RefreshStateError):
    """A newer generation or configuration revision already won."""


# ---------------------------------------------------------------------------
# Namespacing
# ---------------------------------------------------------------------------

def _norm(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _digest(*parts: str) -> str:
    material = _SEPARATOR.join(parts)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def provider_origin(base_url: str) -> str:
    """The provider's identity — scheme + host — without base paths.

    Falls back to the trimmed literal when the value has no scheme so a test
    or an alternate deployment can still name its provider scope.
    """
    text = _norm(base_url)
    if not text:
        return ""
    parts = urlsplit(text)
    if parts.scheme and parts.netloc:
        return f"{parts.scheme}://{parts.netloc}"
    return text.rstrip("/")


def object_namespace(origin: str, space_id: str, type_key: str, object_id: str) -> str:
    """Stable digest for one provider origin + space + type + object."""
    return _digest(_norm(origin), _norm(space_id), _norm(type_key), _norm(object_id))


def snapshot_namespace(vault_root: Any, origin: str, space_id: str) -> str:
    """Stable digest for one vault + provider origin + space."""
    root = str(Path(vault_root).resolve()) if vault_root is not None else ""
    return _digest(root, _norm(origin), _norm(space_id))


# ---------------------------------------------------------------------------
# Optional, read-only legacy import
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class LegacyImport:
    """Result of a read-only legacy content-cache import."""

    imported: int = 0
    skipped_unknown: int = 0
    skipped_invalid: int = 0
    warnings: tuple[str, ...] = ()


def import_legacy_entries(
    store: "RefreshStateStore",
    legacy_path: str | Path,
    *,
    expected_namespace: str,
    object_types: Mapping[str, str],
) -> LegacyImport:
    """Copy compatible entries out of a legacy ``_ContentCache`` document.

    Read-only in both directions that matter: the legacy file is never
    written, and only entries whose object id the caller can name a type for
    are imported (the new namespace is provider origin + space + type +
    object, and the legacy document carries no type). ``fetched_at`` is
    carried over as ``content_read_at`` — the original freshness is recorded,
    never fabricated.

    Incompatible bytes (absent, malformed, another namespace, unsupported
    version) are skipped with a sanitized warning rather than raising: import
    is an optional migration, not a required read.
    """
    if not isinstance(object_types, Mapping):
        raise ValueError("object_types must be a mapping of object id to type key")
    path = Path(legacy_path)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return LegacyImport()
    except (OSError, UnicodeDecodeError):
        return LegacyImport(
            warnings=(
                "Capacities refresh state: skipped an unreadable legacy content "
                "cache; objects will be read fresh.",
            )
        )
    try:
        document = json.loads(text)
    except ValueError:
        document = None
    if not isinstance(document, dict):
        return LegacyImport(
            warnings=(
                "Capacities refresh state: skipped an unreadable legacy content "
                "cache; objects will be read fresh.",
            )
        )
    if document.get("version") != LEGACY_CONTENT_CACHE_SCHEMA_VERSION:
        return LegacyImport(
            warnings=(
                "Capacities refresh state: skipped a legacy content cache with an "
                "unsupported format version; objects will be read fresh.",
            )
        )
    if document.get("namespace") != expected_namespace:
        return LegacyImport(
            warnings=(
                "Capacities refresh state: skipped a legacy content cache stored "
                "for a different provider scope; objects will be read fresh.",
            )
        )
    entries = document.get("entries")
    if not isinstance(entries, list):
        return LegacyImport(
            warnings=(
                "Capacities refresh state: skipped an unreadable legacy content "
                "cache; objects will be read fresh.",
            )
        )

    imported = skipped_unknown = skipped_invalid = 0
    for raw in entries:
        parsed = _legacy_entry(raw)
        if parsed is None:
            skipped_invalid += 1
            continue
        object_id, fetched_at, content = parsed
        type_key = object_types.get(object_id)
        if not isinstance(type_key, str) or not type_key.strip():
            skipped_unknown += 1
            continue
        store.put(
            object_id,
            type_key,
            content,
            content_read_at=fetched_at,
        )
        imported += 1
    return LegacyImport(
        imported=imported,
        skipped_unknown=skipped_unknown,
        skipped_invalid=skipped_invalid,
    )


def _legacy_entry(raw: Any) -> tuple[str, float, dict[str, Any]] | None:
    if not isinstance(raw, dict):
        return None
    object_id = raw.get("object_id")
    if not isinstance(object_id, str) or not object_id.strip():
        return None
    fetched_at = raw.get("fetched_at")
    if isinstance(fetched_at, bool) or not isinstance(fetched_at, (int, float)):
        return None
    fetched_at = float(fetched_at)
    if not math.isfinite(fetched_at):
        return None
    content = raw.get("content")
    if not isinstance(content, dict):
        return None
    return object_id.strip(), fetched_at, content


# ---------------------------------------------------------------------------
# Values
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CachedObject:
    """One persisted object read, with the read time it actually had."""

    object_id: str
    type_key: str
    content: dict[str, Any]
    content_read_at: float
    listing_checked_at: float | None = None


@dataclass(frozen=True)
class SnapshotMember:
    """One published member of a complete generation."""

    object_id: str
    type_key: str
    content_read_at: float


@dataclass(frozen=True)
class CompleteSnapshot:
    """The atomically installed, complete generation pointer."""

    scope_key: str
    revision: int
    generation: int
    installed_at: float
    listing_checked_at: float
    members: tuple[SnapshotMember, ...]
    type_check_times: tuple[tuple[str, float], ...] = ()

    def has(self, object_id: str, type_key: str) -> bool:
        return any(
            member.object_id == object_id and member.type_key == type_key
            for member in self.members
        )

    def members_of_type(self, type_key: str) -> tuple[SnapshotMember, ...]:
        return tuple(
            member for member in self.members if member.type_key == type_key
        )

    def check_time(self, type_key: str) -> float | None:
        for key, checked_at in self.type_check_times:
            if key == type_key:
                return checked_at
        return None


@dataclass(frozen=True)
class TypeListing:
    """One type's enumeration result for this run.

    ``complete=False`` means pagination did not finish: the listing can never
    be used as removal evidence.
    """

    type_key: str
    object_ids: tuple[str, ...]
    listing_checked_at: float
    complete: bool = True


@dataclass(frozen=True)
class SnapshotEvidence:
    """The acquisition evidence the coordinator hands to the store.

    ``required_types`` must all appear in a complete ``listings`` entry.
    ``retained_types`` are types this run did not touch; their published
    members are carried forward from the prior generation (or the install is
    refused, never silently dropped). ``unreadable_objects`` names required
    reads that failed, which also prevents installation.
    """

    scope_key: str
    revision: int
    required_types: tuple[str, ...]
    listings: tuple[TypeListing, ...] = ()
    retained_types: tuple[str, ...] = ()
    unreadable_objects: tuple[str, ...] = ()


@dataclass(frozen=True)
class RunMarker:
    """An unfinished refresh run, reconciled on restart."""

    scope_key: str
    revision: int
    started_at: float


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------

def _require_text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonblank string")
    return value.strip()


def _require_int(value: Any, name: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{name} must be a nonnegative integer")
    return value


def _require_finite(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{name} must be a finite number")
    return number


def _is_finite(value: Any) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
    )


class RefreshStateStore:
    """Durable per-object content plus one atomic complete-generation pointer.

    All writes are serialized by a process-local lock paired with a POSIX
    advisory lock file, so two writers in one process or across processes
    cannot race the storage bound or the generation guard. Reads take the
    same lock; the store is small and correctness dominates throughput here.
    """

    def __init__(
        self,
        root: str | Path,
        *,
        origin: str,
        space_id: str,
        vault_root: Any = None,
        max_objects: int = DEFAULT_MAX_OBJECTS,
        max_bytes: int = DEFAULT_MAX_BYTES,
        epoch_clock: Callable[[], float] = time.time,
    ) -> None:
        self._root = Path(root)
        self._origin = _norm(origin)
        self._space_id = _norm(space_id)
        self._snapshot_namespace = snapshot_namespace(vault_root, self._origin, self._space_id)
        self._max_objects = int(max_objects)
        self._max_bytes = int(max_bytes)
        self._epoch_clock = epoch_clock
        self._lockfile = self._root / LOCK_FILENAME
        self._lock = capacities_cache_io.store_lock(self._lockfile)
        self._warnings: list[str] = []
        self._warning_keys: set[str] = set()

    # -- locking --------------------------------------------------------

    @contextmanager
    def _locked(self) -> Iterator[None]:
        with self._lock:
            handle = capacities_cache_io.acquire_path_lock(self._lockfile)
            try:
                yield
            finally:
                capacities_cache_io.release_lock_file(handle)

    # -- paths ----------------------------------------------------------

    def _object_path(self, type_key: str, object_id: str) -> Path:
        digest = object_namespace(self._origin, self._space_id, type_key, object_id)
        return self._root / OBJECTS_DIRNAME / f"{digest}.json"

    def _snapshot_path(self, scope_key: str) -> Path:
        digest = _digest(self._snapshot_namespace, _norm(scope_key))
        return self._root / SNAPSHOTS_DIRNAME / f"{digest}.json"

    def _run_path(self, scope_key: str) -> Path:
        digest = _digest(self._snapshot_namespace, "run", _norm(scope_key))
        return self._root / RUNS_DIRNAME / f"{digest}.json"

    # -- diagnostics ----------------------------------------------------

    def _warn(self, key: str, message: str) -> None:
        if key in self._warning_keys:
            return
        self._warning_keys.add(key)
        self._warnings.append(message)

    def drain_warnings(self) -> list[str]:
        """Return (and clear) sanitized diagnostics recorded since the drain."""
        drained = list(self._warnings)
        self._warnings.clear()
        self._warning_keys.clear()
        return drained

    # -- object store ---------------------------------------------------

    def put(
        self,
        object_id: str,
        type_key: str,
        content: dict[str, Any],
        *,
        content_read_at: float | None = None,
        listing_checked_at: float | None = None,
    ) -> CachedObject:
        """Atomically persist one object read.

        ``content_read_at`` defaults to now; a caller performing a selective
        import passes the original read time so freshness is preserved.
        Exceeding ``max_objects``/``max_bytes`` raises
        :class:`RefreshStateStorageError` and writes nothing.
        """
        oid = _require_text(object_id, "object_id")
        tkey = _require_text(type_key, "type_key")
        if not isinstance(content, dict):
            raise ValueError("content must be a dict")
        read_at = (
            float(self._epoch_clock())
            if content_read_at is None
            else _require_finite(content_read_at, "content_read_at")
        )
        checked = (
            None
            if listing_checked_at is None
            else _require_finite(listing_checked_at, "listing_checked_at")
        )
        namespace = object_namespace(self._origin, self._space_id, tkey, oid)
        document = {
            "version": OBJECT_SCHEMA_VERSION,
            "namespace": namespace,
            "object_id": oid,
            "type_key": tkey,
            "content_read_at": read_at,
            "listing_checked_at": checked,
            "content": content,
        }
        payload = len(json.dumps(document, indent=2, sort_keys=True).encode("utf-8"))
        with self._locked():
            path = self._object_path(tkey, oid)
            try:
                existing = path.stat().st_size
            except FileNotFoundError:
                existing = 0
            except OSError as exc:
                raise RefreshStateStorageError(
                    "the stored object could not be inspected"
                ) from exc
            count, total = self._usage_unlocked()
            if existing == 0 and count >= self._max_objects:
                self._warn(
                    "objects-bound",
                    "Capacities refresh state: could not store object content "
                    "because the entry limit is reached; the previous complete "
                    "result is preserved and no cached object was evicted.",
                )
                raise RefreshStateStorageError(
                    "the object store is at its entry limit; nothing was evicted"
                )
            if payload > self._max_bytes or total - existing + payload > self._max_bytes:
                self._warn(
                    "bytes-bound",
                    "Capacities refresh state: could not store object content "
                    "because the size limit is reached; the previous complete "
                    "result is preserved and no cached object was evicted.",
                )
                raise RefreshStateStorageError(
                    "the object store is at its size limit; nothing was evicted"
                )
            capacities_cache_io.atomic_write_json(path, document)
        return CachedObject(
            object_id=oid,
            type_key=tkey,
            content=content,
            content_read_at=read_at,
            listing_checked_at=checked,
        )

    def get(self, object_id: str, type_key: str) -> CachedObject | None:
        """The cached read for one object, or ``None`` when absent/unusable.

        There is deliberately no TTL: a valid entry keeps its original
        ``content_read_at`` until an explicit re-read replaces it.
        """
        oid = _require_text(object_id, "object_id")
        tkey = _require_text(type_key, "type_key")
        with self._locked():
            return self._read_object_unlocked(tkey, oid)

    def contains(self, object_id: str, type_key: str) -> bool:
        return self.get(object_id, type_key) is not None

    def object_count(self) -> int:
        with self._locked():
            return self._usage_unlocked()[0]

    def _read_object_unlocked(self, type_key: str, object_id: str) -> CachedObject | None:
        """Unlocked variant for callers already holding the store lock."""
        raw = self._read_document_unlocked(
            self._object_path(type_key, object_id), kind="object"
        )
        if raw is None:
            return None
        if raw.get("version") != OBJECT_SCHEMA_VERSION:
            self._warn(
                "object-version",
                "Capacities refresh state: ignored a cached object with an "
                "unsupported format version; it will be re-read.",
            )
            return None
        if raw.get("namespace") != object_namespace(
            self._origin, self._space_id, type_key, object_id
        ):
            self._warn(
                "object-namespace",
                "Capacities refresh state: ignored a cached object stored for a "
                "different provider scope; it will be re-read.",
            )
            return None
        if raw.get("object_id") != object_id or raw.get("type_key") != type_key:
            self._warn(
                "object-identity",
                "Capacities refresh state: ignored a cached object whose stored "
                "identity does not match its namespace; it will be re-read.",
            )
            return None
        content = raw.get("content")
        read_at = raw.get("content_read_at")
        if not isinstance(content, dict) or not _is_finite(read_at):
            self._warn(
                "object-invalid",
                "Capacities refresh state: ignored an unreadable cached object; "
                "it will be re-read.",
            )
            return None
        checked = raw.get("listing_checked_at")
        return CachedObject(
            object_id=object_id,
            type_key=type_key,
            content=content,
            content_read_at=float(read_at),
            listing_checked_at=float(checked) if _is_finite(checked) else None,
        )

    def _usage_unlocked(self) -> tuple[int, int]:
        directory = self._root / OBJECTS_DIRNAME
        try:
            entries = list(directory.iterdir())
        except FileNotFoundError:
            return 0, 0
        count = 0
        total = 0
        for entry in entries:
            if entry.name.endswith(".tmp") or not entry.name.endswith(".json"):
                continue
            try:
                total += entry.stat().st_size
            except OSError:
                continue
            count += 1
        return count, total

    # -- complete-generation pointer -----------------------------------

    def load_snapshot(self, scope_key: str) -> CompleteSnapshot | None:
        """The installed complete generation, or ``None`` when unusable.

        Unusable stored bytes are reported through :meth:`drain_warnings` and
        are never served. Installation is stricter: it refuses to overwrite
        present-but-unusable bytes (see :meth:`install_generation`).
        """
        key = _require_text(scope_key, "scope_key")
        with self._locked():
            try:
                return self._load_pointer_unlocked(key)
            except RefreshStateFormatError:
                return None

    def install_generation(
        self, evidence: SnapshotEvidence, *, expected_generation: int
    ) -> CompleteSnapshot:
        """Atomically install one complete generation, or refuse.

        Refuses (preserving the prior generation and every cached read) when
        the caller's expected generation or configuration revision is stale,
        when a required listing is incomplete, when a required read failed, or
        when any member has no cached read. Removals happen only here, only as
        the difference a complete listing proves.
        """
        if not isinstance(evidence, SnapshotEvidence):
            raise ValueError("evidence must be a SnapshotEvidence")
        expected = _require_int(expected_generation, "expected_generation")
        scope_key = _require_text(evidence.scope_key, "scope_key")
        with self._locked():
            prior = self._load_pointer_unlocked(scope_key)
            prior_generation = prior.generation if prior is not None else 0
            if expected != prior_generation:
                raise SnapshotConflictError(
                    "another generation was installed since this run started "
                    f"(stored generation {prior_generation}, expected {expected})"
                )
            if prior is not None and evidence.revision < prior.revision:
                raise SnapshotConflictError(
                    "the configuration revision moved past this run "
                    f"(stored revision {prior.revision}, run revision "
                    f"{evidence.revision})"
                )
            members, type_check_times = self._resolve_members_unlocked(prior, evidence)
            snapshot = CompleteSnapshot(
                scope_key=scope_key,
                revision=evidence.revision,
                generation=prior_generation + 1,
                installed_at=float(self._epoch_clock()),
                listing_checked_at=max(
                    (value for _, value in type_check_times), default=0.0
                ),
                members=members,
                type_check_times=type_check_times,
            )
            capacities_cache_io.atomic_write_json(
                self._snapshot_path(scope_key), self._snapshot_document(snapshot)
            )
        return snapshot

    def clear_unusable_snapshot(self, scope_key: str) -> bool:
        """Drop a present-but-unusable pointer; never touch a readable one.

        This is the explicit, bounded repair seam: an unreadable pointer
        otherwise blocks every future installation by design.
        """
        key = _require_text(scope_key, "scope_key")
        with self._locked():
            path = self._snapshot_path(key)
            if not path.exists():
                return False
            try:
                self._load_pointer_unlocked(key)
            except RefreshStateFormatError:
                path.unlink()
                return True
            return False

    def _resolve_members_unlocked(
        self, prior: CompleteSnapshot | None, evidence: SnapshotEvidence
    ) -> tuple[tuple[SnapshotMember, ...], tuple[tuple[str, float], ...]]:
        if evidence.unreadable_objects:
            raise RefreshStateIncompleteError(
                "required object reads did not complete; no generation installed"
            )
        fresh: dict[str, TypeListing] = {}
        for listing in evidence.listings:
            if not isinstance(listing, TypeListing):
                raise ValueError("listings must contain TypeListing values")
            if listing.complete:
                fresh[listing.type_key] = listing
        missing = sorted({t for t in evidence.required_types if t not in fresh})
        if missing:
            raise RefreshStateIncompleteError(
                "listing did not complete for: " + ", ".join(missing)
            )
        prior_by_type: dict[str, list[SnapshotMember]] = {}
        if prior is not None:
            for member in prior.members:
                prior_by_type.setdefault(member.type_key, []).append(member)

        planned: dict[str, tuple[str, ...]] = {}
        times: dict[str, float] = {}
        for type_key, listing in fresh.items():
            planned[type_key] = tuple(sorted(set(listing.object_ids)))
            times[type_key] = listing.listing_checked_at
        for type_key in evidence.retained_types:
            if type_key in planned:
                continue
            retained = prior_by_type.get(type_key)
            if not retained:
                raise RefreshStateIncompleteError(
                    f"type {type_key!r} is retained but has no prior generation"
                )
            planned[type_key] = tuple(sorted(m.object_id for m in retained))
            prior_time = prior.check_time(type_key) if prior is not None else None
            times[type_key] = (
                prior_time
                if prior_time is not None
                else (prior.listing_checked_at if prior is not None else 0.0)
            )
        for type_key, members in prior_by_type.items():
            if type_key not in planned:
                raise RefreshStateIncompleteError(
                    f"type {type_key!r} is neither freshly listed nor retained"
                )

        members: list[SnapshotMember] = []
        for type_key in sorted(planned):
            for object_id in planned[type_key]:
                cached = self._read_object_unlocked(type_key, object_id)
                if cached is None:
                    raise RefreshStateIncompleteError(
                        "a required content read is missing for the published "
                        "scope; no generation installed"
                    )
                members.append(
                    SnapshotMember(
                        object_id=object_id,
                        type_key=type_key,
                        content_read_at=cached.content_read_at,
                    )
                )
        return tuple(members), tuple(sorted(times.items()))

    def _snapshot_document(self, snapshot: CompleteSnapshot) -> dict[str, Any]:
        return {
            "version": REFRESH_STATE_SCHEMA_VERSION,
            "namespace": self._snapshot_namespace,
            "scope_key": snapshot.scope_key,
            "revision": snapshot.revision,
            "generation": snapshot.generation,
            "installed_at": snapshot.installed_at,
            "listing_checked_at": snapshot.listing_checked_at,
            "type_check_times": dict(snapshot.type_check_times),
            "members": [
                {
                    "object_id": member.object_id,
                    "type_key": member.type_key,
                    "content_read_at": member.content_read_at,
                }
                for member in snapshot.members
            ],
        }

    def _load_pointer_unlocked(self, scope_key: str) -> CompleteSnapshot | None:
        """Absent -> ``None``; present but unusable -> format error."""
        path = self._snapshot_path(scope_key)
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except (OSError, UnicodeDecodeError) as exc:
            self._warn(
                "snapshot-read-failed",
                "Capacities refresh state: could not read the stored complete "
                "pointer; installation is blocked until it is repaired.",
            )
            raise RefreshStateFormatError(
                "the stored complete pointer is unreadable"
            ) from exc
        try:
            raw = json.loads(text)
        except ValueError as exc:
            self._warn(
                "snapshot-corrupt",
                "Capacities refresh state: the stored complete pointer is not "
                "valid; installation is blocked until it is repaired.",
            )
            raise RefreshStateFormatError(
                "the stored complete pointer is not valid JSON"
            ) from exc
        if not isinstance(raw, dict):
            self._warn(
                "snapshot-corrupt",
                "Capacities refresh state: the stored complete pointer is not "
                "valid; installation is blocked until it is repaired.",
            )
            raise RefreshStateFormatError("the stored complete pointer is not an object")
        if raw.get("version") != REFRESH_STATE_SCHEMA_VERSION:
            self._warn(
                "snapshot-version",
                "Capacities refresh state: the stored complete pointer has an "
                "unsupported format version; installation is blocked until it "
                "is repaired.",
            )
            raise RefreshStateFormatError("unsupported complete pointer version")
        if raw.get("namespace") != self._snapshot_namespace:
            self._warn(
                "snapshot-namespace",
                "Capacities refresh state: the stored complete pointer belongs "
                "to a different vault, provider, or space; it is never served "
                "here.",
            )
            raise RefreshStateFormatError("complete pointer scope mismatch")
        if raw.get("scope_key") != scope_key:
            self._warn(
                "snapshot-scope",
                "Capacities refresh state: the stored complete pointer belongs "
                "to a different scope; it is never served here.",
            )
            raise RefreshStateFormatError("complete pointer scope mismatch")

        generation = raw.get("generation")
        revision = raw.get("revision")
        installed_at = raw.get("installed_at")
        listing_checked_at = raw.get("listing_checked_at")
        if (
            type(generation) is not int
            or generation < 1
            or type(revision) is not int
            or revision < 0
            or not _is_finite(installed_at)
            or not _is_finite(listing_checked_at)
        ):
            self._warn(
                "snapshot-invalid",
                "Capacities refresh state: the stored complete pointer is "
                "unreadable; installation is blocked until it is repaired.",
            )
            raise RefreshStateFormatError("complete pointer fields are invalid")

        raw_members = raw.get("members")
        if not isinstance(raw_members, list):
            self._warn(
                "snapshot-invalid",
                "Capacities refresh state: the stored complete pointer is "
                "unreadable; installation is blocked until it is repaired.",
            )
            raise RefreshStateFormatError("complete pointer members are invalid")
        members: list[SnapshotMember] = []
        for raw_member in raw_members:
            if not isinstance(raw_member, dict):
                self._warn(
                    "snapshot-invalid",
                    "Capacities refresh state: the stored complete pointer is "
                    "unreadable; installation is blocked until it is repaired.",
                )
                raise RefreshStateFormatError("complete pointer members are invalid")
            object_id = raw_member.get("object_id")
            type_key = raw_member.get("type_key")
            read_at = raw_member.get("content_read_at")
            if (
                not isinstance(object_id, str)
                or not object_id.strip()
                or not isinstance(type_key, str)
                or not type_key.strip()
                or not _is_finite(read_at)
            ):
                self._warn(
                    "snapshot-invalid",
                    "Capacities refresh state: the stored complete pointer is "
                    "unreadable; installation is blocked until it is repaired.",
                )
                raise RefreshStateFormatError("complete pointer members are invalid")
            members.append(
                SnapshotMember(
                    object_id=object_id,
                    type_key=type_key,
                    content_read_at=float(read_at),
                )
            )

        raw_times = raw.get("type_check_times")
        times: list[tuple[str, float]] = []
        if isinstance(raw_times, dict):
            for type_key, checked_at in raw_times.items():
                if not isinstance(type_key, str) or not _is_finite(checked_at):
                    self._warn(
                        "snapshot-invalid",
                        "Capacities refresh state: the stored complete pointer "
                        "is unreadable; installation is blocked until it is "
                        "repaired.",
                    )
                    raise RefreshStateFormatError(
                        "complete pointer coverage times are invalid"
                    )
                times.append((type_key, float(checked_at)))

        return CompleteSnapshot(
            scope_key=scope_key,
            revision=int(revision),
            generation=int(generation),
            installed_at=float(installed_at),
            listing_checked_at=float(listing_checked_at),
            members=tuple(members),
            type_check_times=tuple(sorted(times)),
        )

    # -- interrupted-run markers ---------------------------------------

    def note_run_started(
        self, scope_key: str, revision: int, *, started_at: float | None = None
    ) -> RunMarker:
        """Record that a run started; a restart can then report it interrupted."""
        key = _require_text(scope_key, "scope_key")
        rev = _require_int(revision, "revision")
        marker = RunMarker(
            scope_key=key,
            revision=rev,
            started_at=(
                float(self._epoch_clock())
                if started_at is None
                else _require_finite(started_at, "started_at")
            ),
        )
        document = {
            "version": RUN_MARKER_SCHEMA_VERSION,
            "namespace": self._snapshot_namespace,
            "scope_key": key,
            "revision": rev,
            "started_at": marker.started_at,
        }
        with self._locked():
            capacities_cache_io.atomic_write_json(self._run_path(key), document)
        return marker

    def note_run_finished(self, scope_key: str) -> None:
        """Clear the run marker for any terminal outcome."""
        key = _require_text(scope_key, "scope_key")
        with self._locked():
            try:
                self._run_path(key).unlink()
            except FileNotFoundError:
                return

    def read_unfinished_run(self, scope_key: str) -> RunMarker | None:
        """The still-running marker for one scope, if a process died mid-run."""
        key = _require_text(scope_key, "scope_key")
        with self._locked():
            raw = self._read_document_unlocked(self._run_path(key), kind="run")
            return self._decode_run_marker(raw, key)

    def unfinished_runs(self) -> tuple[RunMarker, ...]:
        """Every unfinished run marker under this store's namespace scope.

        Markers for another vault/provider/space are never reported, so a
        restart reconciles only its own interrupted work.
        """
        directory = self._root / RUNS_DIRNAME
        with self._locked():
            try:
                paths = sorted(directory.glob("*.json"))
            except OSError:
                return ()
            markers: list[RunMarker] = []
            for path in paths:
                raw = self._read_document_unlocked(path, kind="run")
                if raw is None:
                    continue
                if raw.get("namespace") != self._snapshot_namespace:
                    continue
                scope_key = raw.get("scope_key")
                marker = self._decode_run_marker(raw, scope_key)
                if marker is not None:
                    markers.append(marker)
            return tuple(markers)

    def _decode_run_marker(self, raw: Any, scope_key: Any) -> RunMarker | None:
        if raw is None:
            return None
        if (
            not isinstance(raw, dict)
            or raw.get("version") != RUN_MARKER_SCHEMA_VERSION
            or raw.get("namespace") != self._snapshot_namespace
            or not isinstance(scope_key, str)
            or raw.get("scope_key") != scope_key
            or type(raw.get("revision")) is not int
            or not _is_finite(raw.get("started_at"))
        ):
            self._warn(
                "run-marker-invalid",
                "Capacities refresh state: ignored an unreadable interrupted-run "
                "marker.",
            )
            return None
        return RunMarker(
            scope_key=scope_key,
            revision=int(raw["revision"]),
            started_at=float(raw["started_at"]),
        )

    # -- generic document read -----------------------------------------

    def _read_document_unlocked(self, path: Path, *, kind: str) -> Any:
        """Read one JSON document: absent -> ``None``, unusable -> warning."""
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except (OSError, UnicodeDecodeError):
            self._warn(
                f"{kind}-read-failed",
                f"Capacities refresh state: could not read a stored {kind}; it "
                "will be rebuilt from fresh reads.",
            )
            return None
        try:
            document = json.loads(text)
        except ValueError:
            document = None
        if not isinstance(document, dict):
            self._warn(
                f"{kind}-corrupt",
                f"Capacities refresh state: ignored an unreadable stored {kind}; "
                "it will be rebuilt from fresh reads.",
            )
            return None
        return document
