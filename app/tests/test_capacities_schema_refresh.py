"""Behavior tests for the schema-change notice recorded at a Capacities Refresh.

Everything here is local and fake: a deterministic provider, an injected
monotonic clock and sleeper (no real ``time.sleep``), a ``tmp_path`` state
root, and no credential, provider, or ``$HOME`` read. The subject is the
content-free sidecar a later surface reads to show a banner: a Refresh
compares the newly stamped structures payload with the previously stamped one
and persists one small record only when the owner changed their Capacities
schema.

Red-first expectations (the feature does not exist yet) are stated in each
test docstring: the sidecar helpers and the install-time hook are the change
under test.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import capacities_adapter as ca  # noqa: E402
import capacities_cache_io  # noqa: E402
import capacities_refresh as rr  # noqa: E402
import capacities_refresh_state as refresh_state  # noqa: E402
import capacities_schema as schema  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent))
from capacities_refresh_helpers import (  # noqa: E402
    PRIMARY,
    SPACE,
    TYPES,
    Clock,
    Sleeper,
    _content,
    _mapping,
    _paged_provider,
    _store,
    _structures,
)

#: Sentinel display names: none of these may ever appear in the sidecar.
PROP_SENTINEL = "SENTINEL_PROP_NAME"
OPTION_SENTINEL = "SENTINEL_OPTION_NAME"
TITLE_SENTINEL = "SENTINEL_STRUCTURE_TITLE"
VALUE_SENTINEL = "SENTINEL_OPEN_VALUE"


class Revision:
    def __init__(self, value: int = 1) -> None:
        self.value = value

    def __call__(self):
        return self.value


def _coordinator(tmp_path, provider, *, mappings=TYPES, wall_clock=None):
    """A coordinator over one ``tmp_path`` state root (mirrors U2's harness)."""
    clock = Clock()
    sleeper = Sleeper(clock)
    store = _store(tmp_path)
    overrides = {}
    if wall_clock is not None:
        overrides["wall_clock"] = wall_clock
    return (
        rr.RefreshCoordinator(
            root=tmp_path / "state",
            store=store,
            provider=provider,
            space_id=SPACE,
            mappings=tuple(
                m if isinstance(m, ca.StructureMapping) else _mapping(m)
                for m in mappings
            ),
            revision_supplier=Revision(),
            scope_key="all",
            clock=clock,
            sleeper=sleeper,
            **overrides,
        ),
        store,
    )


def _run(tmp_path, provider, *, wall_clock=None):
    """Start one Refresh to completion and return ``(status, store)``."""
    coordinator, store = _coordinator(tmp_path, provider, wall_clock=wall_clock)
    coordinator.start()
    try:
        return coordinator.wait(timeout=5), store
    finally:
        coordinator.close()


def _state_root(tmp_path) -> Path:
    return tmp_path / "state"


def _sidecar_path(tmp_path) -> Path:
    return _state_root(tmp_path) / refresh_state.SCHEMA_CHANGE_FILENAME


def _read_sidecar(tmp_path) -> dict:
    return json.loads(_sidecar_path(tmp_path).read_text(encoding="utf-8"))


def _structures_with_blocked(*, on: str = PRIMARY) -> list[dict]:
    """``_structures`` plus one extra status option on one structure only."""
    structures = _structures(*TYPES)
    for row in structures:
        if row["id"] != on:
            continue
        for definition in row["propertyDefinitions"]:
            if definition["id"] == "status":
                definition["labelSet"].append({"id": "blocked", "name": "Blocked"})
    return structures


def _structures_without_done(*, on: str = "T2") -> list[dict]:
    """``_structures`` with one status option removed from one structure."""
    structures = _structures(*TYPES)
    for row in structures:
        if row["id"] != on:
            continue
        for definition in row["propertyDefinitions"]:
            if definition["id"] == "status":
                definition["labelSet"] = [
                    entry for entry in definition["labelSet"] if entry["id"] != "done"
                ]
    return structures


def _sentinel_structures() -> list[dict]:
    """Structures whose every display name is a leak sentinel."""
    return [
        {
            "id": structure_id,
            "title": f"{TITLE_SENTINEL}_{structure_id}",
            "propertyDefinitions": [
                {
                    "id": "title",
                    "name": f"{PROP_SENTINEL}_title",
                    "type": "title",
                },
                {
                    "id": "assigned",
                    "name": f"{PROP_SENTINEL}_assigned",
                    "type": "boolean",
                },
                {
                    "id": "status",
                    "name": f"{PROP_SENTINEL}_status",
                    "type": "label",
                    "labelSet": [
                        {"id": "active", "name": f"{OPTION_SENTINEL}_active"},
                        {"id": "done", "name": f"{OPTION_SENTINEL}_done"},
                    ],
                },
            ],
        }
        for structure_id in TYPES
    ]


# ---------------------------------------------------------------------------
# (1) The first published contract is a silent baseline
# ---------------------------------------------------------------------------


def test_the_first_refresh_with_no_prior_contract_writes_no_record(tmp_path):
    provider = _paged_provider(objects={PRIMARY: ["a"], "T2": ["c"]})
    status, store = _run(tmp_path, provider)

    assert status["phase"] == "complete", status
    assert store.load_schema_change() is None
    assert _sidecar_path(tmp_path).exists() is False


# ---------------------------------------------------------------------------
# (2) A changed payload on a mapped structure records a 'warn' change
# ---------------------------------------------------------------------------


def test_a_removed_mapped_status_option_records_a_warn_change(tmp_path):
    baseline = _structures_with_blocked()
    first = _paged_provider(objects={PRIMARY: ["a"], "T2": ["c"]}, structures=baseline)
    status, store = _run(tmp_path, first)
    assert status["phase"] == "complete", status
    assert store.load_schema_change() is None

    current = _structures(*TYPES)
    second = _paged_provider(objects={PRIMARY: ["a"], "T2": ["c"]}, structures=current)
    status, store = _run(tmp_path, second, wall_clock=Clock(now=1_700_000_000.0))

    assert status["phase"] == "complete", status
    record = store.load_schema_change()
    assert record is not None
    assert record["version"] == 1
    assert record["schema_version"] == schema.SCHEMA_VERSION
    assert record["severity"] == schema.SEVERITY_WARN
    assert record["diff"]["counts"]["options_removed"] == 1
    assert record["diff"]["options_removed"] == [[PRIMARY, "status", "blocked"]]
    assert record["from_hash"] == schema.fingerprint(baseline)
    assert record["to_hash"] == schema.fingerprint(current)
    assert record["generation"] == 2
    assert record["acknowledged"] is False
    assert record["acknowledged_at"] is None
    assert record["detected_at"].startswith("2023-11-14T")
    assert record["detected_at"].endswith("+00:00")
    assert record["summary"] == schema.summary_text(
        schema.diff(baseline, current, mapped_structure_ids=list(TYPES))
    )


# ---------------------------------------------------------------------------
# (3) An unchanged payload neither writes nor clears a record
# ---------------------------------------------------------------------------


def test_an_unchanged_payload_leaves_an_existing_record_byte_identical(tmp_path):
    baseline = _structures_with_blocked()
    first = _paged_provider(objects={PRIMARY: ["a"], "T2": ["c"]}, structures=baseline)
    assert _run(tmp_path, first)[0]["phase"] == "complete"

    current = _structures(*TYPES)
    second = _paged_provider(objects={PRIMARY: ["a"], "T2": ["c"]}, structures=current)
    assert _run(tmp_path, second)[0]["phase"] == "complete"
    before = _sidecar_path(tmp_path).read_bytes()
    assert json.loads(before.decode("utf-8"))["severity"] == schema.SEVERITY_WARN

    # A third Refresh re-stamps the identical payload: nothing changed, so the
    # recorded notice must survive untouched.
    third = _paged_provider(objects={PRIMARY: ["a"], "T2": ["c"]}, structures=current)
    status, store = _run(tmp_path, third)

    assert status["phase"] == "complete", status
    assert _sidecar_path(tmp_path).read_bytes() == before
    assert store.load_schema_change()["severity"] == schema.SEVERITY_WARN


def test_an_unchanged_payload_writes_no_record_at_all(tmp_path):
    structures = _structures(*TYPES)
    first = _paged_provider(objects={PRIMARY: ["a"], "T2": ["c"]}, structures=structures)
    assert _run(tmp_path, first)[0]["phase"] == "complete"

    again = _paged_provider(objects={PRIMARY: ["a"], "T2": ["c"]}, structures=structures)
    status, store = _run(tmp_path, again)

    assert status["phase"] == "complete", status
    assert store.load_schema_change() is None
    assert _sidecar_path(tmp_path).exists() is False


# ---------------------------------------------------------------------------
# (4) A rename-only change follows the pure module's severity
# ---------------------------------------------------------------------------


def test_a_rename_only_change_records_the_modules_severity(tmp_path):
    baseline = _structures(*TYPES)
    first = _paged_provider(objects={PRIMARY: ["a"], "T2": ["c"]}, structures=baseline)
    assert _run(tmp_path, first)[0]["phase"] == "complete"

    renamed = _structures(*TYPES)
    renamed[0]["title"] = f"{TITLE_SENTINEL}_renamed"
    for definition in renamed[0]["propertyDefinitions"]:
        definition["name"] = f"{PROP_SENTINEL}_{definition['id']}"
    expected = schema.diff(baseline, renamed, mapped_structure_ids=list(TYPES))
    # A rename is presentation-only: the identity fingerprint is unchanged.
    assert expected.severity == schema.SEVERITY_INFO
    assert expected.fingerprint_before == expected.fingerprint_after

    second = _paged_provider(objects={PRIMARY: ["a"], "T2": ["c"]}, structures=renamed)
    status, store = _run(tmp_path, second)

    assert status["phase"] == "complete", status
    record = store.load_schema_change()
    assert record is not None
    assert record["severity"] == expected.severity
    assert record["from_hash"] == expected.fingerprint_before
    assert record["to_hash"] == expected.fingerprint_after
    assert record["acknowledged"] is False


# ---------------------------------------------------------------------------
# (5) The same change keeps its acknowledgement; a new one reopens it
# ---------------------------------------------------------------------------


def test_the_same_change_keeps_acknowledgement_and_a_new_change_resets_it(tmp_path):
    first = _paged_provider(
        objects={PRIMARY: ["a"], "T2": ["c"]}, structures=_structures_with_blocked()
    )
    assert _run(tmp_path, first)[0]["phase"] == "complete"

    removed = _structures(*TYPES)
    second = _paged_provider(objects={PRIMARY: ["a"], "T2": ["c"]}, structures=removed)
    assert _run(tmp_path, second)[0]["phase"] == "complete"
    _, store = _coordinator(
        tmp_path,
        _paged_provider(objects={PRIMARY: ["a"], "T2": ["c"]}),
    )
    to_hash = store.load_schema_change()["to_hash"]
    assert store.acknowledge_schema_change(to_hash) is True

    # The identical change is observed again: the acknowledgement survives.
    third = _paged_provider(objects={PRIMARY: ["a"], "T2": ["c"]}, structures=removed)
    status, store = _run(tmp_path, third)
    assert status["phase"] == "complete", status
    assert store.load_schema_change()["acknowledged"] is True
    assert store.load_schema_change()["to_hash"] == to_hash

    # A different change replaces the record and reopens the notice.
    further = _structures_without_done()
    fourth = _paged_provider(objects={PRIMARY: ["a"], "T2": ["c"]}, structures=further)
    status, store = _run(tmp_path, fourth)

    assert status["phase"] == "complete", status
    record = store.load_schema_change()
    assert record is not None
    assert record["to_hash"] != to_hash
    assert record["acknowledged"] is False
    assert record["acknowledged_at"] is None


# ---------------------------------------------------------------------------
# (6) acknowledge_schema_change matches only the current to_hash
# ---------------------------------------------------------------------------


def _record(
    *,
    to_hash: str = "a" * 64,
    from_hash: str = "b" * 64,
    acknowledged: bool = False,
) -> dict:
    return {
        "version": 1,
        "schema_version": schema.SCHEMA_VERSION,
        "from_hash": from_hash,
        "to_hash": to_hash,
        "generation": 2,
        "detected_at": "2026-10-29T00:00:00+00:00",
        "severity": schema.SEVERITY_WARN,
        "diff": schema.to_dict(schema.SchemaDiff(severity=schema.SEVERITY_WARN)),
        "summary": "Capacities changed since the last refresh.",
        "acknowledged": acknowledged,
        "acknowledged_at": None,
    }


def test_acknowledge_matches_only_the_current_to_hash(tmp_path):
    store = _store(tmp_path)

    assert store.acknowledge_schema_change("a" * 64) is False  # no record yet

    store.write_schema_change(_record())
    assert store.acknowledge_schema_change("c" * 64) is False
    assert store.load_schema_change()["acknowledged"] is False

    assert store.acknowledge_schema_change("a" * 64) is True
    stored = store.load_schema_change()
    assert stored["acknowledged"] is True
    assert isinstance(stored["acknowledged_at"], str)
    assert stored["acknowledged_at"].endswith("+00:00")

    acknowledged_at = stored["acknowledged_at"]
    assert store.acknowledge_schema_change("a" * 64) is True  # idempotent
    assert store.load_schema_change()["acknowledged_at"] == acknowledged_at


def test_acknowledge_never_raises_on_a_broken_sidecar(tmp_path):
    store = _store(tmp_path)
    path = _sidecar_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")

    assert store.acknowledge_schema_change("a" * 64) is False
    assert store.acknowledge_schema_change(None) is False


# ---------------------------------------------------------------------------
# (7) Corrupt or absent sidecar reads as None and the next change writes clean
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("payload", ["{not json", "[]", '{"version": 99}', ""])
def test_a_corrupt_sidecar_reads_as_none_and_the_next_change_writes(tmp_path, payload):
    first = _paged_provider(
        objects={PRIMARY: ["a"], "T2": ["c"]}, structures=_structures_with_blocked()
    )
    assert _run(tmp_path, first)[0]["phase"] == "complete"

    path = _sidecar_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8")
    store = _store(tmp_path)
    assert store.load_schema_change() is None

    second = _paged_provider(
        objects={PRIMARY: ["a"], "T2": ["c"]}, structures=_structures(*TYPES)
    )
    status, store = _run(tmp_path, second)

    assert status["phase"] == "complete", status
    record = store.load_schema_change()
    assert record is not None
    assert record["severity"] == schema.SEVERITY_WARN
    assert record["to_hash"] == schema.fingerprint(_structures(*TYPES))


def test_an_absent_sidecar_reads_as_none(tmp_path):
    store = _store(tmp_path)
    assert store.load_schema_change() is None


# ---------------------------------------------------------------------------
# (8) A failing diff or sidecar write never changes the Refresh
# ---------------------------------------------------------------------------


def _control_run(tmp_path):
    """A clean baseline+change pair on a private root, for comparison."""
    first = _paged_provider(
        objects={PRIMARY: ["a"], "T2": ["c"]}, structures=_structures_with_blocked()
    )
    assert _run(tmp_path, first)[0]["phase"] == "complete"
    second = _paged_provider(
        objects={PRIMARY: ["a"], "T2": ["c"]}, structures=_structures(*TYPES)
    )
    return _run(tmp_path, second)


def test_a_raising_diff_never_changes_the_refresh(tmp_path, monkeypatch):
    control_root = tmp_path / "control"
    control, control_store = _control_run(control_root)

    def boom(*args, **kwargs):
        raise RuntimeError("diff exploded")

    monkeypatch.setattr(schema, "diff", boom)

    first = _paged_provider(
        objects={PRIMARY: ["a"], "T2": ["c"]}, structures=_structures_with_blocked()
    )
    assert _run(tmp_path, first)[0]["phase"] == "complete"
    second = _paged_provider(
        objects={PRIMARY: ["a"], "T2": ["c"]}, structures=_structures(*TYPES)
    )
    status, store = _run(tmp_path, second)

    assert status["phase"] == "complete", status
    assert status["outcome"] == "published", status
    assert store.load_snapshot("all").generation == control_store.load_snapshot("all").generation
    assert status["warnings"] == control["warnings"]
    assert store.drain_warnings() == []
    assert store.load_schema_change() is None


def test_a_raising_sidecar_write_never_changes_the_refresh(tmp_path, monkeypatch):
    control_root = tmp_path / "control"
    control, control_store = _control_run(control_root)

    def boom(*args, **kwargs):
        raise OSError("disk gone")

    monkeypatch.setattr(refresh_state.RefreshStateStore, "write_schema_change", boom)

    first = _paged_provider(
        objects={PRIMARY: ["a"], "T2": ["c"]}, structures=_structures_with_blocked()
    )
    assert _run(tmp_path, first)[0]["phase"] == "complete"
    second = _paged_provider(
        objects={PRIMARY: ["a"], "T2": ["c"]}, structures=_structures(*TYPES)
    )
    status, store = _run(tmp_path, second)

    assert status["phase"] == "complete", status
    assert status["outcome"] == "published", status
    snapshot = store.load_snapshot("all")
    assert snapshot.generation == control_store.load_snapshot("all").generation
    assert {m.object_id for m in snapshot.members} == {
        m.object_id for m in control_store.load_snapshot("all").members
    }
    assert status["warnings"] == control["warnings"]
    assert store.drain_warnings() == []


def test_a_failed_refresh_writes_no_record(tmp_path):
    first = _paged_provider(
        objects={PRIMARY: ["a"], "T2": ["c"]}, structures=_structures_with_blocked()
    )
    assert _run(tmp_path, first)[0]["phase"] == "complete"

    changed = _paged_provider(
        objects={PRIMARY: ["a"], "T2": ["c"]}, structures=_structures(*TYPES)
    )
    changed.list_error = RuntimeError("provider unavailable")
    status, store = _run(tmp_path, changed)

    assert status["phase"] == "failed", status
    assert status["outcome"] == "noCapacities", status
    assert store.load_schema_change() is None
    assert _sidecar_path(tmp_path).exists() is False


def test_a_cancelled_refresh_writes_no_record(tmp_path):
    first = _paged_provider(
        objects={PRIMARY: ["a"], "T2": ["c"]}, structures=_structures_with_blocked()
    )
    assert _run(tmp_path, first)[0]["phase"] == "complete"

    changed = _paged_provider(
        objects={PRIMARY: ["a"], "T2": ["c"]}, structures=_structures(*TYPES)
    )
    coordinator, store = _coordinator(tmp_path, changed)
    changed.on_get = lambda object_id: coordinator.cancel()
    coordinator.start(mode="rescan", scope=PRIMARY)
    status = coordinator.wait(timeout=5)
    coordinator.close()

    assert status["phase"] == "cancelled", status
    assert store.load_schema_change() is None
    assert _sidecar_path(tmp_path).exists() is False


# ---------------------------------------------------------------------------
# (9) The record is content-free
# ---------------------------------------------------------------------------


def test_the_record_carries_no_names_titles_or_values(tmp_path):
    baseline = _sentinel_structures()
    first = _paged_provider(objects={PRIMARY: ["a"], "T2": ["c"]}, structures=baseline)
    assert _run(tmp_path, first)[0]["phase"] == "complete"

    current = _sentinel_structures()
    # One option removed, one renamed: both a warn and a name-only difference.
    for row in current:
        for definition in row["propertyDefinitions"]:
            if definition["id"] == "status":
                definition["labelSet"] = [
                    entry for entry in definition["labelSet"] if entry["id"] != "done"
                ]
    second = _paged_provider(objects={PRIMARY: ["a"], "T2": ["c"]}, structures=current)
    status, store = _run(tmp_path, second)

    assert status["phase"] == "complete", status
    raw = _sidecar_path(tmp_path).read_text(encoding="utf-8")
    record = json.loads(raw)
    assert record["severity"] == schema.SEVERITY_WARN
    for sentinel in (
        PROP_SENTINEL,
        OPTION_SENTINEL,
        TITLE_SENTINEL,
        VALUE_SENTINEL,
        "Blocked",
        "Active",
        "Done",
    ):
        assert sentinel not in raw
    # Ids, counts, hashes and the generated sentence only.
    assert set(record) == {
        "version",
        "schema_version",
        "from_hash",
        "to_hash",
        "generation",
        "detected_at",
        "severity",
        "diff",
        "summary",
        "acknowledged",
        "acknowledged_at",
        "namespace",
        "scope_key",
    }


# ---------------------------------------------------------------------------
# (10) The file is private and replaced atomically
# ---------------------------------------------------------------------------


def test_the_record_file_is_private(tmp_path):
    first = _paged_provider(
        objects={PRIMARY: ["a"], "T2": ["c"]}, structures=_structures_with_blocked()
    )
    assert _run(tmp_path, first)[0]["phase"] == "complete"
    second = _paged_provider(
        objects={PRIMARY: ["a"], "T2": ["c"]}, structures=_structures(*TYPES)
    )
    assert _run(tmp_path, second)[0]["phase"] == "complete"

    path = _sidecar_path(tmp_path)
    assert path.stat().st_mode & 0o777 == 0o600


def test_a_failed_sidecar_write_leaves_the_previous_record_and_no_partial_file(
    tmp_path, monkeypatch
):
    store = _store(tmp_path)
    store.write_schema_change(_record())
    path = _sidecar_path(tmp_path)
    before = path.read_bytes()

    def boom(*args, **kwargs):
        raise OSError("replace failed")

    monkeypatch.setattr(capacities_cache_io.os, "replace", boom)
    with pytest.raises(OSError):
        store.write_schema_change(_record(to_hash="d" * 64))

    assert path.read_bytes() == before
    leftovers = [p.name for p in path.parent.iterdir() if p.name.endswith(".tmp")]
    assert leftovers == []


# ---------------------------------------------------------------------------
# (11) The notice adds no warning, job field, or intake state
# ---------------------------------------------------------------------------


def test_the_notice_adds_no_warning_or_job_field(tmp_path):
    first = _paged_provider(
        objects={PRIMARY: ["a"], "T2": ["c"]}, structures=_structures_with_blocked()
    )
    baseline_status, store = _run(tmp_path, first)
    baseline_keys = set(baseline_status["job"])

    changed = _paged_provider(
        objects={PRIMARY: ["a"], "T2": ["c"]}, structures=_structures(*TYPES)
    )
    status, store = _run(tmp_path, changed)

    assert status["phase"] == "complete", status
    assert store.load_schema_change() is not None
    assert set(status["job"]) == baseline_keys
    assert status["warnings"] == baseline_status["warnings"]
    assert status["warnings"] == []
    assert store.drain_warnings() == []
    # The published generation and its projection are the ordinary ones.
    snapshot = store.load_snapshot("all")
    assert snapshot is not None and snapshot.generation == 2
    assert {m.object_id for m in snapshot.members} == {"a", "c"}
    assert store.load_structure_contract("all").structures == _structures(*TYPES)
