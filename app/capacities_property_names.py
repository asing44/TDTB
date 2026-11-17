"""capacities_property_names.py — last-known NAME of a declared property.

A version-3 ``capacities-settings.json`` pins each type's admission property by
raw provider ID (``types[sid] = {"mode": ..., "property": "<uuid>"}``). The
owner reworks assignment in Capacities per type, and Capacities lets a property
be renamed, deleted, or recreated — each of which leaves the settings document
naming an ID that no live property carries. The settings document deliberately
stores no name, so on its own a dead declaration can only be reported as
unknown, and the type then admits nothing until the owner re-picks it.

This store remembers the display NAME (and kind) of every declared property ID
that resolved against the live structure contract, so a later resolution can
follow the name once instead of silently emptying a type. It is machine-local
last-observed metadata, not settings: it lives beside the direct-refresh
contract cache under ``<app home>/state/capacities-refresh/`` and never in the
revision-checked settings document.

The on-disk document is::

    {
      "version": 1,
      "namespaces": {
        "<sha256 hex of space + base url>": {
          "properties": {
            "<structure id>": {
              "<declared property id>": {
                "name": "<display name>",
                "kind": "boolean|date|datetime",
                "since": "<ISO-8601 UTC>",
                "resolved": "<property id this last resolved to>"
              }
            }
          }
        }
      }
    }

``since`` marks when the remembered name/kind was last observed for that ID, so
a diagnostic can say how stale the record is. ``resolved`` records the outcome
of the last resolution (the declared ID itself when it was live, otherwise the
ID the name matched), which is what the Settings drawer projects.

The store is disposable and must never break a read or a settings write:
``read_property_names`` returns ``{}`` for a missing, unreadable, corrupt,
unsupported, or malformed file and never writes, repairs, or creates anything —
not even the lock file. ``remember_property_names`` returns ``False`` when it
cannot persist and never raises. Nothing here queries the provider, and no
resolution decision is made in this module: it only remembers what a resolution
already observed.
"""
from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from capacities_cache_io import (
    acquire_path_lock,
    atomic_write_json,
    cache_namespace,
    release_lock_file,
    store_lock,
)

PROPERTY_NAMES_SCHEMA_VERSION = 1
PROPERTY_NAMES_FILENAME = "property-names.json"

#: Property kinds a remembered name may carry. Anything else is dropped on
#: read and refused on write: an unknown kind can never authorize a fallback.
REMEMBERED_KINDS = frozenset({"boolean", "date", "datetime"})

_OWNER_ONLY_FILE_MODE = 0o600
_PRIVATE_DIR_MODE = 0o700


def default_path() -> Path:
    """``<app home>/state/capacities-refresh/property-names.json``.

    Imported lazily: the builder owns the refresh-state layout and imports
    this module's consumer, so a module-level import would be circular.
    """
    from capacities_builder import refresh_state_dir

    return refresh_state_dir() / PROPERTY_NAMES_FILENAME


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _resolve_path(path: str | Path | None) -> Path:
    return Path(path) if path is not None else default_path()


def _lock_file_for(path: Path) -> Path:
    return path.with_name(path.name + ".lock")


def _ensure_private_directory(directory: Path) -> None:
    """Create ``directory`` and any missing ancestors owner-only (0700).

    An existing directory keeps its own mode: only directories this call
    creates are hardened, so a caller-supplied parent such as ``~/.config`` is
    never rewritten.
    """
    missing: list[Path] = []
    current = directory
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


def _ensure_owner_only_file(path: Path) -> None:
    """Create ``path`` if absent and force owner-only (0600) file mode."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT, _OWNER_ONLY_FILE_MODE)
    os.close(fd)
    os.chmod(path, _OWNER_ONLY_FILE_MODE)


def _clean_name(value: Any) -> str:
    """A usable display name, or the empty string for anything else.

    A blank name must never be stored: the fallback matches on it, and a stored
    blank could only ever match another blank.
    """
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())


def clean_record(record: Any) -> dict[str, str]:
    """One property record with only its usable fields, or ``{}``.

    Every field is optional but a record with no name is useless, so it is
    dropped: an unusable entry must never be mistaken for a remembered name.
    """
    if not isinstance(record, Mapping):
        return {}
    name = _clean_name(record.get("name"))
    if not name:
        return {}
    cleaned: dict[str, str] = {"name": name}
    kind = record.get("kind")
    if isinstance(kind, str) and kind.strip() in REMEMBERED_KINDS:
        cleaned["kind"] = kind.strip()
    since = record.get("since")
    if isinstance(since, str) and since.strip():
        cleaned["since"] = since.strip()
    resolved = record.get("resolved")
    if isinstance(resolved, str) and resolved.strip():
        cleaned["resolved"] = resolved.strip()
    return cleaned


def _clean_structures(structures: Any) -> dict[str, dict[str, dict[str, str]]]:
    """Structure id -> property id -> cleaned record, dropping unusable rows."""
    cleaned: dict[str, dict[str, dict[str, str]]] = {}
    if not isinstance(structures, Mapping):
        return cleaned
    for structure_id, properties in structures.items():
        if not isinstance(structure_id, str) or not structure_id.strip():
            continue
        if not isinstance(properties, Mapping):
            continue
        entries: dict[str, dict[str, str]] = {}
        for property_id, record in properties.items():
            if not isinstance(property_id, str) or not property_id.strip():
                continue
            cleaned_record = clean_record(record)
            if cleaned_record:
                entries[property_id] = cleaned_record
        if entries:
            cleaned[structure_id] = entries
    return cleaned


def _load_document(path: Path) -> dict[str, Any]:
    """The usable stored document, or a fresh valid frame.

    Corrupt, unreadable, or unsupported-version storage is treated as a cold
    start: ``remember_property_names`` may replace it (only a read is forbidden
    to). Other namespaces are carried through as parsed values, so
    re-serialization leaves them untouched.
    """
    fresh: dict[str, Any] = {
        "version": PROPERTY_NAMES_SCHEMA_VERSION,
        "namespaces": {},
    }
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return fresh
    try:
        document = json.loads(raw)
    except ValueError:
        return fresh
    if not isinstance(document, dict):
        return fresh
    version = document.get("version")
    if type(version) is not int or version != PROPERTY_NAMES_SCHEMA_VERSION:
        return fresh
    namespaces = document.get("namespaces")
    if not isinstance(namespaces, dict):
        return fresh
    return {"version": PROPERTY_NAMES_SCHEMA_VERSION, "namespaces": namespaces}


def read_property_names(
    space_id: str,
    base_url: str,
    *,
    path: str | Path | None = None,
) -> dict[str, dict[str, dict[str, str]]]:
    """Last observed property records for this namespace.

    A missing file, an unreadable or corrupt file, an unsupported version,
    another namespace, or a malformed namespace entry all yield ``{}`` and
    never raise. This function never writes, repairs, or creates anything.
    """
    try:
        namespace = cache_namespace(space_id, base_url)
        raw = _resolve_path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError, TypeError, ValueError):
        return {}
    try:
        document = json.loads(raw)
    except ValueError:
        return {}
    if not isinstance(document, dict):
        return {}
    version = document.get("version")
    if type(version) is not int or version != PROPERTY_NAMES_SCHEMA_VERSION:
        return {}
    namespaces = document.get("namespaces")
    if not isinstance(namespaces, dict):
        return {}
    entry = namespaces.get(namespace)
    if not isinstance(entry, dict):
        return {}
    return _clean_structures(entry.get("properties"))


def remember_property_names(
    space_id: str,
    base_url: str,
    structures: Mapping[Any, Any],
    *,
    path: str | Path | None = None,
) -> bool:
    """Replace this namespace's property records in the machine-local store.

    Every other namespace is preserved. Returns ``True`` on success and
    ``False`` for any failure — an unusable path, an unwritable directory, a
    failing atomic write, or an invalid argument — and never raises: a store
    problem must never block planning or settings. No message here carries a
    path.
    """
    if not isinstance(structures, Mapping):
        return False
    snapshot = _clean_structures(structures)
    try:
        store_path = _resolve_path(path)
    except Exception:  # noqa: BLE001 — a store problem must never block callers
        return False
    lock_file = _lock_file_for(store_path)
    try:
        namespace = cache_namespace(space_id, base_url)
        _ensure_private_directory(store_path.parent)
        _ensure_owner_only_file(lock_file)
        with store_lock(store_path):
            handle = acquire_path_lock(lock_file)
            try:
                document = _load_document(store_path)
                document["namespaces"][namespace] = {"properties": snapshot}
                atomic_write_json(store_path, document)
            finally:
                release_lock_file(handle)
    except Exception:  # noqa: BLE001 — a store problem must never block callers
        return False
    return True


class PropertyNameMemory:
    """In-memory view of one namespace's records, flushed once per contract.

    The adapter is contract-built once per read and never opens a file itself,
    so it holds this object (``CapacitiesConfig.property_names``) and asks it
    two questions: what name was last known for a declared ID, and what this
    resolution observed. Persistence happens on ``flush()``, which is a no-op
    when nothing changed and never raises.
    """

    def __init__(
        self,
        space_id: str,
        base_url: str,
        *,
        path: str | Path | None = None,
        now: Callable[[], str] | None = None,
    ) -> None:
        self._space_id = space_id
        self._base_url = base_url
        self._path = path
        self._now = now or _utc_now
        self._records = read_property_names(space_id, base_url, path=path)
        self._changed = False

    def known(self, structure_id: str, property_id: str) -> tuple[str, str] | None:
        """``(name, kind)`` last observed for one declared ID, or ``None``.

        A record without a usable kind cannot authorize a fallback, because the
        mode decides which kind may be used; it is reported as unknown.
        """
        record = self._records.get(structure_id, {}).get(property_id) or {}
        name = record.get("name") or ""
        kind = record.get("kind") or ""
        if not name or not kind:
            return None
        return name, kind

    def record(
        self,
        structure_id: str,
        property_id: str,
        *,
        name: str,
        kind: str,
        resolved: str,
    ) -> None:
        """Remember one observed resolution; ``since`` moves only on a change.

        ``since`` marks the remembered name/kind, so an unchanged re-read keeps
        the original timestamp instead of churning the store on every refresh.
        """
        cleaned_name = _clean_name(name)
        cleaned_kind = str(kind or "").strip()
        resolved_id = str(resolved or "").strip()
        if not cleaned_name or cleaned_kind not in REMEMBERED_KINDS or not resolved_id:
            return
        structure = self._records.setdefault(structure_id, {})
        previous = structure.get(property_id) or {}
        if (
            previous.get("name") == cleaned_name
            and previous.get("kind") == cleaned_kind
        ):
            since = previous.get("since") or self._now()
        else:
            since = self._now()
        updated = {
            "name": cleaned_name,
            "kind": cleaned_kind,
            "since": since,
            "resolved": resolved_id,
        }
        if updated != previous:
            structure[property_id] = updated
            self._changed = True

    def snapshot(self) -> dict[str, dict[str, dict[str, str]]]:
        """A copy of every record this namespace holds."""
        return {
            structure_id: {
                property_id: dict(record)
                for property_id, record in properties.items()
            }
            for structure_id, properties in self._records.items()
        }

    def flush(self) -> bool:
        """Persist what changed since the last flush; never raises."""
        if not self._changed:
            return True
        try:
            persisted = remember_property_names(
                self._space_id,
                self._base_url,
                self._records,
                path=self._path,
            )
        except Exception:  # noqa: BLE001 — a store problem must never block callers
            return False
        if persisted:
            self._changed = False
        return persisted
