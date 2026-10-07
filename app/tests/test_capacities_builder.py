"""Public-interface tests for the Capacities adapter builder.

Everything here is local and fake: no provider, real credential, or network is
touched. ``tmp_path`` is both the vault root and the token-file home, and the
transport is an ``httpx.MockTransport`` that records (and asserts zero) calls.
"""
from __future__ import annotations

import json
import os
import stat
import sys
import threading
from datetime import date
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import capacities_builder as cb  # noqa: E402
import capacities_settings as cs  # noqa: E402
import capacities_structure_titles as cst  # noqa: E402
from capacities_adapter import (  # noqa: E402
    CapacitiesAdapter,
    CapacitiesContractError,
    StructureMapping,
)


SPACE = "space-1"
TODAY = date(2026, 9, 29)
EXCLUDED = f"capacities:{SPACE}:RootTask:task-1"

#: Captured before the autouse isolation fixture runs, so the production
#: default stays pinned to the operator-chosen machine-local root.
_MACHINE_LOCAL_DEFAULT = getattr(cb, "DEFAULT_CONTENT_CACHE_PATH", None)


@pytest.fixture(autouse=True)
def _isolate_machine_local_cache(tmp_path, monkeypatch):
    """No test may read or write the real ``~/.config/tdtb`` cache."""
    monkeypatch.setattr(
        cb, "DEFAULT_CONTENT_CACHE_PATH", tmp_path / "machine-content-cache.json"
    )
    monkeypatch.setattr(
        cst, "DEFAULT_TITLES_CACHE_PATH", tmp_path / "machine-titles.json"
    )
    monkeypatch.setattr(cb, "_CACHE_REGISTRY", {})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_token(path: Path, text: str, mode: int = 0o600) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    os.chmod(path, mode)
    return path


def _valid_token_file(tmp_path: Path) -> Path:
    return _write_token(tmp_path / "env", "CAPACITIES_API_TOKEN=secret-token-value\n")


def _write_source(vault_root: Path, payload: dict) -> Path:
    path = cb.source_path(vault_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _write_source_raw(vault_root: Path, text: str) -> Path:
    path = cb.source_path(vault_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _write_settings(vault_root: Path) -> None:
    cs.save_settings(
        vault_root,
        expected_revision=0,
        native_task_auto=cs.NativeTaskAutoPolicy(),
        excluded=(EXCLUDED,),
        active_structures=("custom-project",),
    )


def _structures_payload() -> list[dict]:
    return [
        {
            "structure_id": "RootTask",
            "title_property": "title",
            "status_property": "status",
            "open_status_values": ["open"],
        },
        {
            "structure_id": "custom-project",
            "title_property": "title",
            "status_property": "state",
            "open_status_values": ["active"],
            "assignment_property": "tdtb",
            "assignment_values": ["yes"],
            "date_property": "date",
            "duration_property": "minutes",
        },
    ]


def _valid_payload(**overrides) -> dict:
    payload = {
        "version": 1,
        "revision": 0,
        "space_id": SPACE,
        "structures": _structures_payload(),
    }
    payload.update(overrides)
    return payload


def _expected_mappings() -> tuple[StructureMapping, ...]:
    return (
        StructureMapping(
            structure_id="RootTask",
            title_property="title",
            open_status_property="status",
            open_status_values=frozenset({"open"}),
        ),
        StructureMapping(
            structure_id="custom-project",
            title_property="title",
            assignment_property="tdtb",
            assignment_values=frozenset({"yes"}),
            date_property="date",
            open_status_property="state",
            open_status_values=frozenset({"active"}),
            duration_property="minutes",
        ),
    )


class _RecordingTransport(httpx.MockTransport):
    """MockTransport that records request paths and fails loudly if called."""

    def __init__(self):
        self.calls: list[str] = []
        super().__init__(self._handler)

    def _handler(self, request: httpx.Request) -> httpx.Response:  # pragma: no cover
        self.calls.append(request.url.path)
        raise AssertionError(f"unexpected network call during construction: {request.url}")


class _FakeProvider:
    """Minimal provider seam used only to drive the adapter's contract check."""

    def __init__(self, structures):
        self._structures = structures

    def fetch_structures(self):
        return self._structures

    def list_objects(self, structure_id, cursor=None):
        return {"objects": [], "next_cursor": None}

    def get_object(self, object_id):
        return {}

    def patch_object(self, object_id, properties):
        return {}


# ---------------------------------------------------------------------------
# Silent opt-out: absent record
# ---------------------------------------------------------------------------

def test_absent_record_returns_none_silently(tmp_path):
    token = _valid_token_file(tmp_path)
    result = cb.build_capacities_adapter(
        tmp_path, cb.CapacitiesBuilderConfig(token_path=token)
    )
    assert result is None
    # Nothing was created as a side effect of the opt-out read.
    assert not cb.source_path(tmp_path).exists()


def test_read_source_returns_none_when_absent(tmp_path):
    assert cb.read_source(tmp_path) is None


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

def test_well_formed_record_returns_configured_adapter(tmp_path):
    _write_source(tmp_path, _valid_payload())
    _write_settings(tmp_path)
    token = _valid_token_file(tmp_path)
    transport = _RecordingTransport()

    adapter = cb.build_capacities_adapter(
        tmp_path,
        cb.CapacitiesBuilderConfig(token_path=token),
        transport=transport,
    )

    assert isinstance(adapter, CapacitiesAdapter)
    assert adapter.config.space_id == SPACE
    assert adapter.config.mappings == _expected_mappings()
    assert adapter.config.max_pages == cb.DEFAULT_MAX_PAGES
    assert adapter.config.assignment_settings.active_structures == frozenset(
        {"custom-project"}
    )
    assert EXCLUDED in adapter.config.assignment_settings.excluded_identities
    assert transport.calls == []


def test_settings_admission_inputs_reach_the_adapter_config(tmp_path):
    """The production path carries both persisted admission inputs from the
    vault file through ``to_assignment_settings`` into the built adapter."""
    _write_source(tmp_path, _valid_payload())
    cs.save_settings(
        tmp_path,
        expected_revision=0,
        native_task_auto=cs.NativeTaskAutoPolicy(),
        excluded=(),
        active_structures=(),
        native_task_structures=("RootTask", "custom-project"),
        active_statuses=("active", "In Progress"),
    )
    token = _valid_token_file(tmp_path)
    transport = _RecordingTransport()

    adapter = cb.build_capacities_adapter(
        tmp_path,
        cb.CapacitiesBuilderConfig(token_path=token),
        transport=transport,
    )

    assert adapter.config.assignment_settings.native_task_structures == frozenset(
        {"RootTask", "custom-project"}
    )
    assert adapter.config.assignment_settings.active_statuses == frozenset(
        {"active", "In Progress"}
    )
    assert transport.calls == []


def test_transport_is_injected_and_no_network_calls_made(tmp_path):
    _write_source(tmp_path, _valid_payload())
    token = _valid_token_file(tmp_path)
    transport = _RecordingTransport()

    adapter = cb.build_capacities_adapter(
        tmp_path, cb.CapacitiesBuilderConfig(token_path=token, transport=transport)
    )

    assert adapter is not None
    assert adapter.provider._client is not None  # a real client wrapping the fake
    assert transport.calls == []


# ---------------------------------------------------------------------------
# Settings-declared source assignment (``assigned_structures``)
# ---------------------------------------------------------------------------
# The settings store and the vault-local source mapping are two separate
# persisted surfaces. The builder is where they meet, so the per-structure
# precedence is resolved HERE: a structure declared in ``assigned_structures``
# reads the declared property ID with ``true`` implied, and every other
# structure keeps the mapping's own ``assignment_property`` /
# ``assignment_values`` untouched. The resolution sets a distinct mapping field
# (``assigned_property``) precisely so the declaration cannot reach
# ``assignment_property`` and therefore cannot change which structures the
# adapter enumerates.

SETTINGS_ASSIGNED_PROPERTY = "f779f78a-settings-assigned"
ADVENTURE_ASSIGNED_PROPERTY = "c19f9b95-settings-assigned"


def _write_settings_with_declarations(vault_root: Path, declarations: dict) -> None:
    cs.save_settings(
        vault_root,
        expected_revision=0,
        native_task_auto=cs.NativeTaskAutoPolicy(),
        excluded=(EXCLUDED,),
        active_structures=(),
        assigned_structures=declarations,
    )


def _write_legacy_settings(vault_root: Path) -> None:
    """A pre-existing version-1 file written before the additive key existed."""
    path = cs.settings_path(vault_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({
            "version": 1,
            "revision": 0,
            "native_task_auto": {
                "active_enabled": True,
                "due_enabled": True,
                "deadline_enabled": True,
                "deadline_horizon_days": 2,
            },
            "excluded": {},
            "active_structures": {"custom-project": True},
        }),
        encoding="utf-8",
    )


def _assignment_payload() -> dict:
    """A source record where BOTH structures carry a mapping declaration."""
    return _valid_payload(
        structures=[
            {
                "structure_id": "RootTask",
                "title_property": "title",
                "status_property": "status",
                "open_status_values": ["open"],
                "assignment_property": "assigned",
                "assignment_values": ["true"],
            },
            {
                "structure_id": "custom-project",
                "title_property": "title",
                "status_property": "state",
                "open_status_values": ["active"],
                "assignment_property": "tdtb",
                "assignment_values": ["yes"],
                "date_property": "date",
                "duration_property": "minutes",
            },
        ]
    )


def _built_mappings(tmp_path: Path, payload: dict) -> tuple[StructureMapping, ...]:
    _write_source(tmp_path, payload)
    token = _valid_token_file(tmp_path)
    adapter = cb.build_capacities_adapter(
        tmp_path,
        cb.CapacitiesBuilderConfig(token_path=token, transport=_RecordingTransport()),
    )
    assert adapter is not None
    return adapter.config.mappings


class _StructuresTransport(httpx.MockTransport):
    """Serves the structure contract the adapter validates; fails loudly on
    any other request so a real read can never escape the fixture."""

    def __init__(self):
        self.calls: list[str] = []
        super().__init__(self._handler)

    def _handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request.url.path)
        if request.url.path != "/space/structures":
            raise AssertionError(f"unexpected network call: {request.url}")
        return httpx.Response(200, json={"structures": _declared_contract_rows()})


def _declared_contract_rows() -> list[dict]:
    return [
        {
            "id": "RootTask",
            "title": "RootTask",
            "propertyDefinitions": [
                {"id": pid, "name": pid, "type": "text"}
                for pid in ("title", "status", "assigned")
            ],
        },
        {
            "id": "custom-project",
            "title": "custom-project",
            "propertyDefinitions": [
                {"id": pid, "name": pid, "type": "text"}
                for pid in ("title", "tdtb", "date", "state", "minutes")
            ]
            + [
                {
                    "id": SETTINGS_ASSIGNED_PROPERTY,
                    "name": "Assigned",
                    "type": "boolean",
                }
            ],
        },
    ]


def _declared_object(object_id: str, *, boolean: bool) -> dict:
    return {
        "id": object_id,
        "structureId": "custom-project",
        "properties": {
            "title": {"type": "title", "title": {"value": "Declared row"}},
            "state": {"type": "label", "label": [{"id": "active", "name": "Active"}]},
            "tdtb": {"type": "label", "label": [{"id": "no", "name": "No"}]},
            SETTINGS_ASSIGNED_PROPERTY: {
                "type": "boolean",
                "boolean": {"value": boolean},
            },
        },
    }


def test_declaration_overrides_only_the_declared_structure(tmp_path):
    _write_settings_with_declarations(tmp_path, {"custom-project": SETTINGS_ASSIGNED_PROPERTY})

    mappings = {m.structure_id: m for m in _built_mappings(tmp_path, _assignment_payload())}

    # Declared: the settings property ID wins, with ``true`` implied and no
    # stored value list.
    assert mappings["custom-project"].assigned_property == SETTINGS_ASSIGNED_PROPERTY
    # Undeclared: the mapping's own declaration is untouched.
    assert mappings["RootTask"].assigned_property is None
    assert mappings["RootTask"].assignment_property == "assigned"
    assert mappings["RootTask"].assignment_values == frozenset({"true"})
    # The mapping fields the enumeration gate reads stay exactly as stored,
    # even for the declared structure.
    assert mappings["custom-project"].assignment_property == "tdtb"
    assert mappings["custom-project"].assignment_values == frozenset({"yes"})


def test_no_declaration_leaves_every_mapping_at_its_own_default(tmp_path):
    _write_settings_with_declarations(tmp_path, {})

    mappings = {m.structure_id: m for m in _built_mappings(tmp_path, _assignment_payload())}

    assert all(m.assigned_property is None for m in mappings.values())
    assert mappings["custom-project"].assignment_property == "tdtb"


def test_legacy_settings_file_without_the_key_falls_back_to_the_mapping(tmp_path):
    _write_legacy_settings(tmp_path)

    mappings = {m.structure_id: m for m in _built_mappings(tmp_path, _assignment_payload())}

    assert all(m.assigned_property is None for m in mappings.values())
    assert mappings["custom-project"].assignment_property == "tdtb"
    assert mappings["custom-project"].assignment_values == frozenset({"yes"})


def test_declaration_for_an_unmapped_structure_is_ignored(tmp_path):
    _write_settings_with_declarations(tmp_path, {"Adventure": ADVENTURE_ASSIGNED_PROPERTY})

    mappings = _built_mappings(tmp_path, _valid_payload())

    assert mappings == _expected_mappings()


def test_declaration_does_not_supply_a_mapping_assignment_property(tmp_path):
    # A declared structure that has no mapping declaration must NOT acquire
    # one: ``assignment_property`` drives ``_structure_can_contribute``, and
    # the operator chose "assignment check only" — the declaration must not
    # gate enumeration.
    _write_settings_with_declarations(tmp_path, {"RootTask": SETTINGS_ASSIGNED_PROPERTY})

    mappings = {m.structure_id: m for m in _built_mappings(tmp_path, _valid_payload())}

    assert mappings["RootTask"].assigned_property == SETTINGS_ASSIGNED_PROPERTY
    assert mappings["RootTask"].assignment_property is None
    assert mappings["RootTask"].assignment_values == frozenset()


def test_declared_property_reaches_a_real_read_through_the_production_path(tmp_path):
    """The declaration must survive settings file -> builder -> adapter read."""
    _write_source(tmp_path, _valid_payload())
    _write_settings_with_declarations(tmp_path, {"custom-project": SETTINGS_ASSIGNED_PROPERTY})
    token = _valid_token_file(tmp_path)
    transport = _StructuresTransport()

    adapter = cb.build_capacities_adapter(
        tmp_path, cb.CapacitiesBuilderConfig(token_path=token, transport=transport)
    )
    result = adapter.items_for_day_from_objects(
        TODAY,
        [_declared_object("project-1", boolean=True)],
    )

    assert [row["capacities_id"] for row in result.items] == ["project-1"]
    assert result.items[0]["capacities_assignment"]["mode"] == "assigned"
    assert result.items[0]["capacities_assignment"]["source_assigned"] is True


def test_declared_property_false_is_not_assigned_through_the_production_path(tmp_path):
    _write_source(tmp_path, _valid_payload())
    _write_settings_with_declarations(tmp_path, {"custom-project": SETTINGS_ASSIGNED_PROPERTY})
    token = _valid_token_file(tmp_path)

    adapter = cb.build_capacities_adapter(
        tmp_path,
        cb.CapacitiesBuilderConfig(token_path=token, transport=_StructuresTransport()),
    )
    result = adapter.items_for_day_from_objects(
        TODAY,
        [_declared_object("project-1", boolean=False)],
    )

    assert result.items == []


# ---------------------------------------------------------------------------
# Credential errors — present config must fail visibly
# ---------------------------------------------------------------------------

def test_missing_token_file_raises_without_leaking_path_or_token(tmp_path):
    _write_source(tmp_path, _valid_payload())
    missing = tmp_path / "nested" / "env"

    with pytest.raises(cb.CapacitiesTokenError) as excinfo:
        cb.build_capacities_adapter(
            tmp_path, cb.CapacitiesBuilderConfig(token_path=missing)
        )

    message = str(excinfo.value)
    assert str(tmp_path) not in message
    assert "secret-token-value" not in message


def test_loose_token_permissions_raise(tmp_path):
    _write_source(tmp_path, _valid_payload())
    token = _write_token(
        tmp_path / "env", "CAPACITIES_API_TOKEN=secret-token-value\n", mode=0o644
    )

    with pytest.raises(cb.CapacitiesTokenError) as excinfo:
        cb.build_capacities_adapter(
            tmp_path, cb.CapacitiesBuilderConfig(token_path=token)
        )
    assert "secret-token-value" not in str(excinfo.value)


def test_token_file_without_key_raises(tmp_path):
    _write_source(tmp_path, _valid_payload())
    token = _write_token(tmp_path / "env", "SOMETHING_ELSE=value\n")

    with pytest.raises(cb.CapacitiesTokenError) as excinfo:
        cb.build_capacities_adapter(
            tmp_path, cb.CapacitiesBuilderConfig(token_path=token)
        )
    assert "value" not in str(excinfo.value)


def test_token_prefers_primary_key_and_accepts_alias(tmp_path):
    both = _write_token(
        tmp_path / "env",
        "CAPACITIES_REST_TOKEN=alt\nCAPACITIES_API_TOKEN=primary\n",
    )
    assert cb.load_capacities_token(both) == "primary"

    alias = _write_token(tmp_path / "env2", "CAPACITIES_REST_TOKEN=only-alt\n")
    assert cb.load_capacities_token(alias) == "only-alt"


def test_token_ignores_blank_and_comment_lines(tmp_path):
    token = _write_token(
        tmp_path / "env",
        "# a comment\n\nCAPACITIES_API_TOKEN=parsed\n",
    )
    assert cb.load_capacities_token(token) == "parsed"


# ---------------------------------------------------------------------------
# Malformed record — raises and preserves original bytes
# ---------------------------------------------------------------------------

def _dup_structure_payload() -> list[dict]:
    return [
        {"structure_id": "RootTask", "title_property": "title"},
        {"structure_id": "RootTask", "title_property": "title"},
    ]


@pytest.mark.parametrize(
    "payload",
    [
        _valid_payload(unknown="nope"),
        _valid_payload(structures=_dup_structure_payload()),
        _valid_payload(structures="not-a-list"),
        _valid_payload(structures=[]),
        _valid_payload(version=2),
        _valid_payload(revision="1"),
        _valid_payload(
            structures=[{"structure_id": "RootTask", "bogus_key": 1}]
        ),
    ],
)
def test_malformed_record_raises_and_preserves_bytes(tmp_path, payload):
    path = _write_source(tmp_path, payload)
    before = path.read_bytes()
    token = _valid_token_file(tmp_path)

    with pytest.raises(cb.CapacitiesSourceFormatError):
        cb.build_capacities_adapter(
            tmp_path, cb.CapacitiesBuilderConfig(token_path=token)
        )

    assert path.read_bytes() == before


def test_duplicate_json_keys_raise_and_preserve_bytes(tmp_path):
    raw = (
        '{"version": 1, "revision": 0, "revision": 0, '
        f'"space_id": "{SPACE}", "structures": []}}'
    )
    path = _write_source_raw(tmp_path, raw)
    before = path.read_bytes()

    with pytest.raises(cb.CapacitiesSourceFormatError):
        cb.read_source(tmp_path)

    assert path.read_bytes() == before


def test_unparseable_record_raises(tmp_path):
    _write_source_raw(tmp_path, "{not json")
    with pytest.raises(cb.CapacitiesSourceFormatError):
        cb.read_source(tmp_path)


def test_parse_source_json_rejects_duplicate_keys_at_every_level():
    with pytest.raises(ValueError):
        cb.parse_source_json('{"a": 1, "a": 2}')
    with pytest.raises(ValueError):
        cb.parse_source_json('{"outer": {"a": 1, "a": 2}}')
    with pytest.raises(ValueError):
        cb.parse_source_json(b'{"a": [{"b": 1, "b": 2}]}')


def test_parse_source_json_parses_a_clean_document():
    assert cb.parse_source_json('{"a": [1, 2]}') == {"a": [1, 2]}


# ---------------------------------------------------------------------------
# Bare structure: builder round-trips; adapter enforces its contract
# ---------------------------------------------------------------------------

def test_structure_without_assignment_or_status_round_trips_and_contract_is_deferred(
    tmp_path,
):
    payload = _valid_payload(
        structures=[{"structure_id": "custom-bare", "title_property": "title"}]
    )
    _write_source(tmp_path, payload)
    token = _valid_token_file(tmp_path)

    adapter = cb.build_capacities_adapter(
        tmp_path, cb.CapacitiesBuilderConfig(token_path=token)
    )

    # The builder is structural-only; it round-trips the mapping verbatim.
    assert adapter is not None
    assert adapter.config.mappings == (
        StructureMapping(structure_id="custom-bare", title_property="title"),
    )

    # The adapter still owns the provider contract and fails closed when it is
    # first exercised — the builder deliberately does not duplicate that rule
    # (contract validation needs live structure definitions, and the builder
    # makes no network calls).
    provider = _FakeProvider(
        [
            {
                "id": "custom-bare",
                "title": "Bare",
                "propertyDefinitions": [
                    {"id": "title", "name": "Title", "type": "title", "writable": True}
                ],
            }
        ]
    )
    with pytest.raises(CapacitiesContractError):
        CapacitiesAdapter(provider, adapter.config).items_for_day(TODAY)


# ---------------------------------------------------------------------------
# Store write round-trip (mirrors capacities_settings semantics)
# ---------------------------------------------------------------------------

def test_save_source_round_trips_and_conflicts_preserve_bytes(tmp_path):
    record = cb.SourceRecord(
        space_id=SPACE,
        structures=(
            cb.SourceStructureRecord(
                structure_id="RootTask",
                status_property="status",
                open_status_values=("open",),
            ),
        ),
    )
    saved = cb.save_source(
        tmp_path, expected_revision=0, space_id=record.space_id,
        structures=record.structures,
    )
    assert saved.revision == 1
    assert cb.read_source(tmp_path) == saved

    before = cb.source_path(tmp_path).read_bytes()
    with pytest.raises(cb.CapacitiesSourceStoreError):
        cb.save_source(
            tmp_path, expected_revision=0, space_id=record.space_id,
            structures=record.structures,
        )
    assert cb.source_path(tmp_path).read_bytes() == before


def test_stale_save_raises_the_typed_conflict_with_both_revisions(tmp_path):
    record = cb.SourceRecord(
        space_id=SPACE,
        structures=(cb.SourceStructureRecord(structure_id="RootTask"),),
    )
    cb.save_source(
        tmp_path, expected_revision=0, space_id=record.space_id,
        structures=record.structures,
    )
    before = cb.source_path(tmp_path).read_bytes()

    with pytest.raises(cb.CapacitiesSourceConflictError) as excinfo:
        cb.save_source(
            tmp_path, expected_revision=0, space_id=record.space_id,
            structures=record.structures,
        )

    assert excinfo.value.expected_revision == 0
    assert excinfo.value.current_revision == 1
    # The typed conflict is still the store error every existing caller
    # already catches.
    assert isinstance(excinfo.value, cb.CapacitiesSourceStoreError)
    assert cb.source_path(tmp_path).read_bytes() == before


def test_racing_saves_from_the_same_revision_produce_one_winner(tmp_path):
    record = cb.SourceRecord(
        space_id=SPACE,
        structures=(cb.SourceStructureRecord(structure_id="RootTask"),),
    )
    barrier = threading.Barrier(2)
    results: list[object] = []

    def attempt() -> None:
        barrier.wait()
        try:
            results.append(
                cb.save_source(
                    tmp_path, expected_revision=0,
                    space_id=record.space_id, structures=record.structures,
                )
            )
        except cb.CapacitiesSourceConflictError as exc:
            results.append(exc)

    threads = [threading.Thread(target=attempt) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    winners = [r for r in results if isinstance(r, cb.SourceRecord)]
    conflicts = [
        r for r in results if isinstance(r, cb.CapacitiesSourceConflictError)
    ]
    assert len(winners) == 1
    assert len(conflicts) == 1
    assert winners[0].revision == 1
    assert conflicts[0].expected_revision == 0
    assert conflicts[0].current_revision == 1
    assert cb.read_source(tmp_path) == winners[0]


def test_decode_source_payload_reuses_the_store_decoders():
    candidate = cb.decode_source_payload(
        space_id=SPACE,
        structures=[
            {
                "structure_id": "RootTask",
                "status_property": "status",
                "open_status_values": ["open", "On Hold"],
            }
        ],
    )

    assert candidate.version == cb.SCHEMA_VERSION
    assert candidate.revision == 0
    assert candidate.space_id == SPACE
    assert candidate.structures == (
        cb.SourceStructureRecord(
            structure_id="RootTask",
            status_property="status",
            open_status_values=("open", "On Hold"),
        ),
    )


@pytest.mark.parametrize(
    "space_id, structures",
    [
        (SPACE, "not-a-list"),
        (SPACE, None),
        (SPACE, []),
        (SPACE, ["not-an-object"]),
        (SPACE, [{"structure_id": "a", "bogus_key": 1}]),
        (SPACE, [{"structure_id": "a"}, {"structure_id": "a"}]),
        (SPACE, [{"structure_id": "a", "title_property": None}]),
        (SPACE, [{"structure_id": "a", "open_status_values": "open"}]),
        (SPACE, [{"structure_id": "a", "open_status_values": ["open", "OPEN"]}]),
        (SPACE, [{"structure_id": " padded "}]),
        (" padded ", [{"structure_id": "a"}]),
        (123, [{"structure_id": "a"}]),
    ],
)
def test_decode_source_payload_rejects_anything_the_store_would_reject(
    space_id, structures
):
    with pytest.raises(cb.CapacitiesSourceFormatError):
        cb.decode_source_payload(space_id=space_id, structures=structures)


class TestStatusValueVocabulary:
    """``open_status_values`` must accept real multi-word status names.

    Values are compared after casefold plus collapsed internal whitespace, so a
    vocabulary containing ``On Hold`` has to be storable. Rejecting it would
    silently classify every ``On Hold`` object as closed and drop it before any
    inclusion signal was even considered.
    """

    def test_multi_word_status_value_round_trips(self, tmp_path):
        record = cb.save_source(
            tmp_path,
            expected_revision=0,
            space_id="space-1",
            structures=(
                cb.SourceStructureRecord(
                    structure_id="RootTask",
                    status_property="status",
                    open_status_values=("Todo", "Started", "Active", "On Hold"),
                ),
            ),
        )

        assert record.structures[0].open_status_values == (
            "Todo", "Started", "Active", "On Hold",
        )
        assert cb.read_source(tmp_path) == record

    @pytest.mark.parametrize(
        "bad", ["", " ", " Active", "Active ", "On\tHold", "On\nHold"]
    )
    def test_invalid_status_values_are_rejected(self, bad):
        with pytest.raises(ValueError):
            cb.SourceStructureRecord(
                structure_id="RootTask", open_status_values=(bad,)
            )

    def test_duplicate_after_normalization_is_rejected(self):
        with pytest.raises(ValueError):
            cb.SourceStructureRecord(
                structure_id="RootTask",
                open_status_values=("On Hold", "on  hold"),
            )


# ---------------------------------------------------------------------------
# Content cache — the provider allows 30 requests per minute
# ---------------------------------------------------------------------------

class _ObjectTransport(httpx.MockTransport):
    """Answers per-object content reads and counts them."""

    def __init__(self):
        self.reads: list[str] = []
        super().__init__(self._handler)

    def _handler(self, request: httpx.Request) -> httpx.Response:
        self.reads.append(request.url.params.get("id", ""))
        return httpx.Response(200, json={"id": "task-1", "properties": {}})


def _cached_adapter(tmp_path, transport, cache):
    _write_source(tmp_path, _valid_payload())
    token = _valid_token_file(tmp_path)
    return cb.build_capacities_adapter(
        tmp_path,
        cb.CapacitiesBuilderConfig(
            token_path=token, transport=transport, content_cache=cache
        ),
    )


def test_builder_default_resolves_a_namespace_scoped_durable_cache(tmp_path):
    """Consecutive refreshes must not re-spend the request budget.

    The adapter is rebuilt per read, so the factory resolves the default
    sentinel to one namespace-scoped cache per vault + space + provider and
    keeps it for every rebuild. That cache is durable, so the progress also
    survives a process restart. The adapter is rebuilt per read, so the cache
    has to outlive it; otherwise two refreshes inside the provider's
    one-minute window each pay for every object and the second degrades.
    """
    transport = _ObjectTransport()
    _write_source(tmp_path, _valid_payload())
    token = _valid_token_file(tmp_path)
    cache_path = tmp_path / "content-cache.json"

    assert cb.CapacitiesBuilderConfig().content_cache is cb._DEFAULT_CONTENT_CACHE

    config = cb.CapacitiesBuilderConfig(
        token_path=token, transport=transport, cache_path=cache_path
    )
    first = cb.build_capacities_adapter(tmp_path, config)
    first.provider.get_object("task-1")
    second = cb.build_capacities_adapter(tmp_path, config)
    second.provider.get_object("task-1")

    assert first.config.content_cache is second.config.content_cache
    assert transport.reads == ["task-1"], transport.reads
    assert cache_path.exists()
    document = json.loads(cache_path.read_text(encoding="utf-8"))
    assert document["namespace"] == cb._content_cache_namespace(
        tmp_path, SPACE, cb.CAPACITIES_BASE_URL
    )


def test_content_cache_is_optional(tmp_path):
    transport = _ObjectTransport()
    adapter = _cached_adapter(tmp_path, transport, None)
    adapter.provider.get_object("task-1")
    adapter.provider.get_object("task-1")

    assert transport.reads == ["task-1", "task-1"], transport.reads


def test_content_cache_expires_and_stays_bounded():
    cache = cb._ContentCache(ttl_seconds=0.0, max_entries=2)
    cache.put("a", {"id": "a"})
    assert cache.get("a") is None  # expired immediately

    fresh = cb._ContentCache(ttl_seconds=300.0, max_entries=2)
    for key in ("a", "b", "c"):
        fresh.put(key, {"id": key})
    # The bound holds: older entries are dropped rather than growing forever.
    assert sum(1 for key in ("a", "b", "c") if fresh.get(key) is not None) <= 2


def test_default_content_cache_path_is_machine_local():
    assert _MACHINE_LOCAL_DEFAULT == (
        Path.home() / ".config" / "tdtb" / "tdtb-capacities-content-cache.json"
    )


# ---------------------------------------------------------------------------
# Content cache — durable, machine-local backing
# ---------------------------------------------------------------------------

class _Clock:
    """Deterministic stand-in for the module's wall / monotonic clock seams."""

    def __init__(self, value: float = 0.0) -> None:
        self.value = float(value)

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += float(seconds)


def _durable_cache(
    tmp_path,
    *,
    namespace="ns-a",
    ttl=cb.CONTENT_CACHE_TTL_SECONDS,
    max_entries=cb.CONTENT_CACHE_MAX_ENTRIES,
    max_bytes=cb.CONTENT_CACHE_MAX_BYTES,
    wall=None,
    monotonic=None,
    name="content-cache.json",
):
    return cb._ContentCache(
        ttl_seconds=ttl,
        max_entries=max_entries,
        persist_path=tmp_path / name,
        namespace=namespace,
        max_bytes=max_bytes,
        epoch_clock=wall if wall is not None else _Clock(),
        monotonic_clock=monotonic if monotonic is not None else _Clock(),
    )


def _cache_document(tmp_path, name="content-cache.json"):
    return json.loads((tmp_path / name).read_text(encoding="utf-8"))


def test_content_cache_survives_a_reconstructed_cache(tmp_path):
    wall, mono = _Clock(1000.0), _Clock(10.0)
    first = _durable_cache(tmp_path, wall=wall, monotonic=mono)
    first.put("task-1", {"id": "task-1", "properties": {"status": "open"}})
    assert first.drain_warnings() == []

    wall.advance(10.0)
    mono.advance(10.0)
    rebuilt = _durable_cache(tmp_path, wall=wall, monotonic=mono)

    assert rebuilt.get("task-1") == {
        "id": "task-1",
        "properties": {"status": "open"},
    }
    document = _cache_document(tmp_path)
    assert document["version"] == cb.CONTENT_CACHE_SCHEMA_VERSION
    assert document["entries"][0]["object_id"] == "task-1"
    assert document["entries"][0]["fetched_at"] == 1000.0


def test_content_cache_honours_remaining_ttl_after_a_restart(tmp_path):
    wall, mono = _Clock(1000.0), _Clock(10.0)
    first = _durable_cache(tmp_path, wall=wall, monotonic=mono)
    first.put("task-1", {"id": "task-1"})

    wall.advance(250.0)
    mono.advance(250.0)
    rebuilt = _durable_cache(tmp_path, wall=wall, monotonic=mono)
    assert rebuilt.get("task-1") == {"id": "task-1"}  # 50s left

    wall.advance(49.0)
    assert rebuilt.get("task-1") == {"id": "task-1"}  # 1s left
    wall.advance(1.0)
    assert rebuilt.get("task-1") is None  # exactly 300s: expired


def test_content_cache_monotonic_deadline_bounds_in_use_freshness(tmp_path):
    wall, mono = _Clock(1000.0), _Clock(10.0)
    first = _durable_cache(tmp_path, wall=wall, monotonic=mono)
    first.put("task-1", {"id": "task-1"})

    wall.advance(100.0)  # age 100 -> 200s of lifetime left
    mono.advance(100.0)
    rebuilt = _durable_cache(tmp_path, wall=wall, monotonic=mono)

    mono.advance(199.0)
    assert rebuilt.get("task-1") == {"id": "task-1"}
    mono.advance(1.0)
    # The wall clock never moved; only the derived monotonic deadline expired.
    assert rebuilt.get("task-1") is None


def test_content_cache_rewrite_does_not_renew_an_entry(tmp_path):
    wall, mono = _Clock(1000.0), _Clock(10.0)
    first = _durable_cache(tmp_path, wall=wall, monotonic=mono)
    first.put("old", {"id": "old"})

    wall.advance(200.0)
    mono.advance(200.0)
    rebuilt = _durable_cache(tmp_path, wall=wall, monotonic=mono)
    assert rebuilt.get("old") == {"id": "old"}  # 100s left

    rebuilt.put("new", {"id": "new"})  # rewrite while both are fresh
    entries = {
        entry["object_id"]: entry for entry in _cache_document(tmp_path)["entries"]
    }
    assert entries["old"]["fetched_at"] == 1000.0  # anchored to the real fetch
    assert entries["new"]["fetched_at"] == 1200.0

    wall.advance(100.0)
    mono.advance(100.0)
    third = _durable_cache(tmp_path, wall=wall, monotonic=mono)
    assert third.get("old") is None  # expired despite the rewrite
    assert third.get("new") == {"id": "new"}


def test_content_cache_rejects_future_and_invalid_fetch_times(tmp_path):
    path = tmp_path / "content-cache.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "namespace": "ns-a",
                "entries": [
                    {
                        "object_id": "future",
                        "fetched_at": 1010.0,
                        "content": {"id": "future"},
                    },
                    {
                        "object_id": "text",
                        "fetched_at": "soon",
                        "content": {"id": "text"},
                    },
                    {
                        "object_id": "nan",
                        "fetched_at": float("nan"),
                        "content": {"id": "nan"},
                    },
                    {
                        "object_id": "bool",
                        "fetched_at": True,
                        "content": {"id": "bool"},
                    },
                    {
                        "object_id": "good",
                        "fetched_at": 1000.0,
                        "content": {"id": "good"},
                    },
                ],
            },
            allow_nan=True,
        ),
        encoding="utf-8",
    )
    cache = _durable_cache(tmp_path, wall=_Clock(1005.0), monotonic=_Clock(5.0))
    for object_id in ("future", "text", "nan", "bool"):
        assert cache.get(object_id) is None
    assert cache.get("good") == {"id": "good"}
    warnings = cache.drain_warnings()
    assert warnings and all(str(tmp_path) not in warning for warning in warnings)
    assert any("invalid" in warning for warning in warnings)


def test_expired_entries_are_dropped_silently_on_load(tmp_path):
    wall, mono = _Clock(1000.0), _Clock(10.0)
    first = _durable_cache(tmp_path, wall=wall, monotonic=mono)
    first.put("task-1", {"id": "task-1"})

    wall.advance(400.0)
    mono.advance(400.0)
    rebuilt = _durable_cache(tmp_path, wall=wall, monotonic=mono)
    assert rebuilt.get("task-1") is None
    # Aging is routine, not a diagnostic: a restart after the TTL is silence.
    assert rebuilt.drain_warnings() == []


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param("{not json", id="unreadable"),
        pytest.param(
            json.dumps({"version": 99, "namespace": "ns-a", "entries": []}),
            id="unsupported-version",
        ),
        pytest.param(
            json.dumps({"version": 1, "namespace": "other", "entries": []}),
            id="wrong-namespace",
        ),
        pytest.param(
            json.dumps({"version": 1, "namespace": "ns-a", "entries": "nope"}),
            id="bad-entries",
        ),
    ],
)
def test_content_cache_never_serves_unusable_bytes(tmp_path, payload):
    path = tmp_path / "content-cache.json"
    path.write_text(payload, encoding="utf-8")
    cache = _durable_cache(tmp_path, wall=_Clock(1000.0), monotonic=_Clock(10.0))

    assert cache.get("task-1") is None
    warnings = cache.drain_warnings()
    joined = " ".join(warnings)
    assert warnings
    assert "content cache" in joined.lower()
    assert str(tmp_path) not in joined

    # Fresh validated data atomically replaces the unusable bytes.
    cache.put("task-1", {"id": "task-1"})
    document = _cache_document(tmp_path)
    assert document["namespace"] == "ns-a"
    assert [entry["object_id"] for entry in document["entries"]] == ["task-1"]


def test_content_cache_ignores_an_oversized_file_before_loading(tmp_path):
    path = tmp_path / "content-cache.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "namespace": "ns-a",
                "entries": [
                    {
                        "object_id": "task-1",
                        "fetched_at": 1000.0,
                        "content": {"id": "task-1"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    cache = _durable_cache(
        tmp_path, max_bytes=10, wall=_Clock(1005.0), monotonic=_Clock(5.0)
    )
    assert cache.get("task-1") is None
    assert any("size limit" in warning for warning in cache.drain_warnings())


def test_content_cache_skips_entries_that_exceed_the_file_cap(tmp_path):
    cache = _durable_cache(tmp_path, max_bytes=2048)
    cache.put("huge", {"id": "huge", "blob": "x" * 8192})
    cache.put("small", {"id": "small"})

    raw = (tmp_path / "content-cache.json").read_bytes()
    assert len(raw) <= 2048
    ids = [entry["object_id"] for entry in _cache_document(tmp_path)["entries"]]
    assert "huge" not in ids
    assert "small" in ids

    rebuilt = _durable_cache(tmp_path, max_bytes=2048)
    assert rebuilt.get("small") == {"id": "small"}
    assert rebuilt.get("huge") is None  # skipped, never resurrected from disk


def test_content_cache_never_writes_a_document_over_the_cap(tmp_path):
    cache = _durable_cache(tmp_path, max_bytes=1)
    cache.put("task-1", {"id": "task-1"})

    assert not (tmp_path / "content-cache.json").exists()
    assert cache.get("task-1") == {"id": "task-1"}  # memory still works
    assert "not durable" in " ".join(cache.drain_warnings())


def test_content_cache_prunes_expired_entries_before_fresh_ones(tmp_path):
    wall, mono = _Clock(1000.0), _Clock(10.0)
    cache = _durable_cache(tmp_path, max_entries=2, wall=wall, monotonic=mono)
    cache.put("stale", {"id": "stale"})

    wall.advance(301.0)
    mono.advance(301.0)
    cache.put("a", {"id": "a"})
    cache.put("b", {"id": "b"})
    cache.put("c", {"id": "c"})  # bound reached: "stale" is expired and goes first

    ids = {entry["object_id"] for entry in _cache_document(tmp_path)["entries"]}
    assert "stale" not in ids
    assert "b" in ids and "c" in ids


def test_content_cache_prunes_oldest_entries_at_the_entry_bound(tmp_path):
    wall, mono = _Clock(1000.0), _Clock(10.0)
    cache = _durable_cache(tmp_path, max_entries=2, wall=wall, monotonic=mono)
    cache.put("old-1", {"id": "old-1"})
    cache.put("old-2", {"id": "old-2"})

    wall.advance(100.0)
    mono.advance(100.0)
    cache.put("fresh", {"id": "fresh"})

    ids = {entry["object_id"] for entry in _cache_document(tmp_path)["entries"]}
    assert len(ids) <= 2
    assert "fresh" in ids
    assert "old-1" not in ids  # oldest first


def test_content_cache_write_failure_preserves_file_and_memory(
    tmp_path, monkeypatch
):
    wall, mono = _Clock(1000.0), _Clock(10.0)
    cache = _durable_cache(tmp_path, wall=wall, monotonic=mono)
    cache.put("task-1", {"id": "task-1"})
    path = tmp_path / "content-cache.json"
    before = path.read_bytes()

    def _boom(_path, _data):
        raise OSError(
            "/Users/walle-mini/.config/tdtb/tdtb-capacities-content-cache.json: "
            "permission denied"
        )

    monkeypatch.setattr(cb, "_atomic_write_json", _boom)
    cache.put("task-2", {"id": "task-2"})

    assert path.read_bytes() == before
    assert cache.get("task-1") == {"id": "task-1"}
    assert cache.get("task-2") == {"id": "task-2"}
    joined = " ".join(cache.drain_warnings())
    assert "not durable" in joined
    assert "OSError" in joined
    assert "/Users/" not in joined
    assert str(tmp_path) not in joined


def test_content_cache_concurrent_puts_do_not_lose_progress(tmp_path):
    cache = _durable_cache(tmp_path)
    errors = []

    def worker(index):
        try:
            cache.put(f"task-{index}", {"id": f"task-{index}"})
        except Exception as exc:  # noqa: BLE001 — surfaced through the assertion
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(index,)) for index in range(16)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    rebuilt = _durable_cache(tmp_path)
    assert all(rebuilt.get(f"task-{index}") is not None for index in range(16))


def test_content_cache_merges_newer_progress_from_another_writer(tmp_path):
    wall, mono = _Clock(1000.0), _Clock(10.0)
    first = _durable_cache(tmp_path, wall=wall, monotonic=mono)  # stale snapshot
    second = _durable_cache(tmp_path, wall=wall, monotonic=mono)  # stale snapshot

    first.put("task-1", {"id": "task-1"})
    # The second writer must read/merge what is already on disk, not clobber it.
    second.put("task-2", {"id": "task-2"})

    rebuilt = _durable_cache(tmp_path, wall=wall, monotonic=mono)
    assert rebuilt.get("task-1") is not None
    assert rebuilt.get("task-2") is not None


def test_content_cache_file_is_versioned_and_owner_only(tmp_path):
    cache = _durable_cache(tmp_path)
    cache.put("task-1", {"id": "task-1", "properties": {"status": "open"}})

    path = tmp_path / "content-cache.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["version"] == cb.CONTENT_CACHE_SCHEMA_VERSION
    assert set(document) == {"version", "namespace", "entries"}
    assert set(document["entries"][0]) == {"object_id", "fetched_at", "content"}
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_cache_namespace_separates_vault_space_and_base_url(tmp_path):
    base = cb._content_cache_namespace(tmp_path, "space-1", "https://api.capacities.io")
    assert base != cb._content_cache_namespace(
        tmp_path, "space-2", "https://api.capacities.io"
    )
    assert base != cb._content_cache_namespace(
        tmp_path, "space-1", "https://other.example"
    )
    assert base != cb._content_cache_namespace(
        tmp_path / "elsewhere", "space-1", "https://api.capacities.io"
    )


def test_content_cache_namespace_isolation_between_writers(tmp_path):
    wall, mono = _Clock(1000.0), _Clock(10.0)
    first = _durable_cache(
        tmp_path, namespace="vault-a|space-1|https://api", wall=wall, monotonic=mono
    )
    first.put("task-1", {"id": "task-1"})

    other = _durable_cache(
        tmp_path, namespace="vault-b|space-1|https://api", wall=wall, monotonic=mono
    )
    assert other.get("task-1") is None
    warnings = other.drain_warnings()
    assert warnings
    assert any("different vault or space" in warning for warning in warnings)
    assert all(str(tmp_path) not in warning for warning in warnings)

    other.put("task-2", {"id": "task-2"})
    assert _cache_document(tmp_path)["namespace"] == "vault-b|space-1|https://api"


def test_builder_namespace_separates_vaults_spaces_and_base_urls(tmp_path):
    vault_a = tmp_path / "a"
    vault_b = tmp_path / "b"
    vault_c = tmp_path / "c"
    for root, payload in (
        (vault_a, _valid_payload()),
        (vault_b, _valid_payload()),
        (vault_c, _valid_payload(space_id="space-2")),
    ):
        root.mkdir()
        _write_source(root, payload)
    token = _valid_token_file(tmp_path)
    cache_path = tmp_path / "content-cache.json"

    def build(root, **overrides):
        config = cb.CapacitiesBuilderConfig(
            token_path=token,
            transport=_ObjectTransport(),
            cache_path=cache_path,
            **overrides,
        )
        return cb.build_capacities_adapter(root, config)

    first = build(vault_a)
    first.config.content_cache.put("task-1", {"id": "task-1"})
    assert build(vault_a).config.content_cache.get("task-1") is not None

    # A different vault, a different space, and a different provider base URL
    # each resolve to their own namespace, so object-id-only entries never leak.
    assert build(vault_b).config.content_cache.get("task-1") is None
    assert build(vault_c).config.content_cache.get("task-1") is None
    assert (
        build(vault_a, base_url="https://other.example").config.content_cache.get(
            "task-1"
        )
        is None
    )


def _contract_structure_rows():
    def definitions(*ids):
        return [{"id": prop_id, "name": prop_id, "type": "text"} for prop_id in ids]

    return [
        {
            "id": "RootTask",
            "title": "RootTask",
            "propertyDefinitions": definitions("title", "status"),
        },
        {
            "id": "custom-project",
            "title": "custom-project",
            "propertyDefinitions": definitions(
                "title", "tdtb", "date", "state", "minutes"
            ),
        },
    ]


def test_cache_diagnostics_reach_adapter_warnings_without_leaks(tmp_path):
    cache_path = tmp_path / "content-cache.json"
    cache_path.write_text("{not json", encoding="utf-8")
    _write_source(tmp_path, _valid_payload())
    _write_settings(tmp_path)
    token = _valid_token_file(tmp_path)

    adapter = cb.build_capacities_adapter(
        tmp_path,
        cb.CapacitiesBuilderConfig(
            token_path=token, transport=_RecordingTransport(), cache_path=cache_path
        ),
    )
    result = CapacitiesAdapter(
        _FakeProvider(_contract_structure_rows()), adapter.config
    ).items_for_day(TODAY)

    joined = " ".join(result.warnings)
    assert "content cache" in joined.lower()
    assert "rebuilt from fresh reads" in joined
    assert str(tmp_path) not in joined
    assert "/Users/" not in joined


def test_default_builder_writes_only_to_the_isolated_cache_path(tmp_path):
    _write_source(tmp_path, _valid_payload())
    token = _valid_token_file(tmp_path)
    adapter = cb.build_capacities_adapter(
        tmp_path, cb.CapacitiesBuilderConfig(token_path=token, transport=_ObjectTransport())
    )
    adapter.provider.get_object("task-1")

    assert cb.DEFAULT_CONTENT_CACHE_PATH.exists()
    assert cb.DEFAULT_CONTENT_CACHE_PATH.parent == tmp_path


# ---------------------------------------------------------------------------
# Structure discovery for the mapping editor
# ---------------------------------------------------------------------------

PROJECT_STRUCTURE = "0d194525-c5a1-4af5-bb62-202b83006b5e"
PRESS_STRUCTURE = "6aa7b02a-4315-47d1-9cfb-0c0cdac0950c"
UNTITLED_STRUCTURE = "7a7b7ef0-8eec-4349-8ccd-f2e25e424047"


def _probe_structures() -> list[dict]:
    """The structures shape observed against the operator's real space.

    Deliberately out of order: the catalog is sorted by structure ID, not
    by provider order.
    """
    return [
        {
            "id": PRESS_STRUCTURE,
            "title": "Press",
            "propertyDefinitions": [
                {
                    "id": "press-done",
                    "name": "done",
                    "type": "boolean",
                    "writable": True,
                },
                {
                    "id": "press-status",
                    "name": "status",
                    "type": "label",
                    "writable": True,
                    "labelSet": [
                        {"id": "press-active", "name": "Active"},
                        {"id": "press-dropped", "name": "Dropped"},
                    ],
                },
                {
                    "id": "press-duration",
                    "name": "durationMin",
                    "type": "number",
                    "writable": True,
                },
            ],
        },
        {
            "id": UNTITLED_STRUCTURE,
            "propertyDefinitions": [
                {"id": "no-name", "type": "text"},
                {"id": "blank-name", "name": "   ", "type": "number"},
            ],
        },
        {
            "id": PROJECT_STRUCTURE,
            "title": "Project",
            "propertyDefinitions": [
                {
                    "id": "project-assigned",
                    "name": "assigned",
                    "type": "boolean",
                    "writable": True,
                },
                {
                    "id": "project-status",
                    "name": "status",
                    "type": "label",
                    "writable": True,
                    "labelSet": [
                        {"id": "idle", "name": "Idle"},
                        {"id": "active", "name": "Active"},
                        {"id": "on-hold", "name": "On Hold"},
                        {"id": "completed", "name": "Completed"},
                        {"id": "dropped", "name": "Dropped"},
                    ],
                },
                {
                    "id": "project-timeframe",
                    "name": "timeFrame",
                    "type": "date",
                },
            ],
        },
    ]


class _StructuresPayloadTransport(httpx.MockTransport):
    """Serves one structures payload; any other request fails the test."""

    def __init__(self, structures=None):
        self.calls: list[str] = []
        self._structures = (
            _probe_structures() if structures is None else structures
        )
        super().__init__(self._handler)

    def _handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request.url.path)
        if request.url.path != "/space/structures":
            raise AssertionError(f"unexpected network call: {request.url}")
        return httpx.Response(200, json={"structures": self._structures})


def _discovery_config(tmp_path: Path, transport) -> cb.CapacitiesBuilderConfig:
    return cb.CapacitiesBuilderConfig(
        token_path=_valid_token_file(tmp_path), transport=transport
    )


def _catalog_ids(catalog) -> list[str]:
    return [row.structure_id for row in catalog]


def test_discovery_succeeds_when_the_mapping_is_absent(tmp_path):
    transport = _StructuresPayloadTransport()

    catalog = cb.discover_capacities_source(
        tmp_path, SPACE, _discovery_config(tmp_path, transport)
    )

    assert _catalog_ids(catalog) == [
        PROJECT_STRUCTURE,
        PRESS_STRUCTURE,
        UNTITLED_STRUCTURE,
    ]
    assert transport.calls == ["/space/structures"]
    # Discovery is a read of the provider, not of the vault: nothing created.
    assert not cb.source_path(tmp_path).exists()


def test_discovery_succeeds_when_the_mapping_is_malformed(tmp_path, monkeypatch):
    raw = "{ not json"
    source = _write_source_raw(tmp_path, raw)
    token = _valid_token_file(tmp_path)

    # The normal factory cannot serve this vault: the stored mapping is
    # unreadable. Discovery exists precisely to work before a mapping does.
    with pytest.raises(cb.CapacitiesSourceFormatError):
        cb.build_capacities_adapter(
            tmp_path,
            cb.CapacitiesBuilderConfig(
                token_path=token, transport=_RecordingTransport()
            ),
        )

    def forbidden(*args, **kwargs):
        raise AssertionError("discovery touched the mapping or settings")

    monkeypatch.setattr(cb, "read_source", forbidden)
    monkeypatch.setattr(cb, "save_source", forbidden)
    monkeypatch.setattr(cb, "read_settings", forbidden)
    transport = _StructuresPayloadTransport()

    catalog = cb.discover_capacities_source(
        tmp_path, SPACE, _discovery_config(tmp_path, transport)
    )

    assert _catalog_ids(catalog) == [
        PROJECT_STRUCTURE,
        PRESS_STRUCTURE,
        UNTITLED_STRUCTURE,
    ]
    assert source.read_text(encoding="utf-8") == raw
    assert transport.calls == ["/space/structures"]


class _FakeDiscoveryClient:
    """Fake REST client proving discovery's request and lifecycle shape."""

    last: "_FakeDiscoveryClient | None" = None

    def __init__(self, token, **kwargs):
        self.token = token
        self.kwargs = kwargs
        self.structures_calls = 0
        self.object_calls: list[str] = []
        self.closed = False
        _FakeDiscoveryClient.last = self

    def fetch_structures(self, observer=None):
        self.structures_calls += 1
        return {"structures": _probe_structures()}

    def list_objects(self, *args, **kwargs):
        self.object_calls.append("list_objects")
        raise AssertionError("discovery must not list objects")

    def get_object(self, *args, **kwargs):
        self.object_calls.append("get_object")
        raise AssertionError("discovery must not read object content")

    def close(self):
        self.closed = True


def test_discovery_fetches_structures_once_without_other_calls(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(cb, "CapacitiesRestClient", _FakeDiscoveryClient)
    config = _discovery_config(tmp_path, None)

    catalog = cb.discover_capacities_source(tmp_path, SPACE, config)

    client = _FakeDiscoveryClient.last
    assert client is not None
    assert client.structures_calls == 1
    assert client.object_calls == []
    assert client.closed is True
    assert client.kwargs["space_id"] == SPACE
    assert client.kwargs["base_url"] == config.base_url
    assert callable(client.kwargs["structures_observer"])
    assert _catalog_ids(catalog)[0] == PROJECT_STRUCTURE


class _FailingDiscoveryClient(_FakeDiscoveryClient):
    def fetch_structures(self, observer=None):
        self.structures_calls += 1
        raise RuntimeError("provider unavailable")


def test_discovery_closes_the_client_when_the_fetch_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(cb, "CapacitiesRestClient", _FailingDiscoveryClient)

    with pytest.raises(RuntimeError, match="provider unavailable"):
        cb.discover_capacities_source(
            tmp_path, SPACE, _discovery_config(tmp_path, None)
        )

    assert _FailingDiscoveryClient.last is not None
    assert _FailingDiscoveryClient.last.closed is True


def test_catalog_shape_matches_the_probed_space(tmp_path):
    transport = _StructuresPayloadTransport()

    catalog = cb.discover_capacities_source(
        tmp_path, SPACE, _discovery_config(tmp_path, transport)
    )

    assert _catalog_ids(catalog) == [
        PROJECT_STRUCTURE,
        PRESS_STRUCTURE,
        UNTITLED_STRUCTURE,
    ]
    project, press, untitled = catalog

    assert project.title == "Project"
    assert project.properties == (
        cb.CatalogProperty(
            property_id="project-assigned",
            title="assigned",
            type="boolean",
            writable=True,
            label_options=(),
        ),
        cb.CatalogProperty(
            property_id="project-status",
            title="status",
            type="label",
            writable=True,
            label_options=(
                cb.CatalogLabelOption(id="idle", title="Idle"),
                cb.CatalogLabelOption(id="active", title="Active"),
                cb.CatalogLabelOption(id="on-hold", title="On Hold"),
                cb.CatalogLabelOption(id="completed", title="Completed"),
                cb.CatalogLabelOption(id="dropped", title="Dropped"),
            ),
        ),
        cb.CatalogProperty(
            property_id="project-timeframe",
            title="timeFrame",
            type="date",
            writable=False,  # absent in the provider payload
            label_options=(),
        ),
    )

    assert press.title == "Press"
    assert [prop.type for prop in press.properties] == [
        "boolean",
        "label",
        "number",
    ]

    # No display name: the structure ID is the title, not a blank.
    assert untitled.title == UNTITLED_STRUCTURE
    assert [prop.title for prop in untitled.properties] == [
        "no-name",
        "blank-name",
    ]


def test_blank_structure_title_falls_back_to_the_structure_id(tmp_path):
    rows = [
        {
            "id": "structured-blank",
            "title": "   ",
            "propertyDefinitions": [
                {"id": "title", "name": "Title", "type": "title"}
            ],
        }
    ]
    transport = _StructuresPayloadTransport(rows)

    catalog = cb.discover_capacities_source(
        tmp_path, SPACE, _discovery_config(tmp_path, transport)
    )

    assert _catalog_ids(catalog) == ["structured-blank"]
    assert catalog[0].title == "structured-blank"


@pytest.mark.parametrize(
    "rows",
    [
        [{"id": "no-definitions"}],
        [{"id": "bad-rows", "propertyDefinitions": [{"name": "x"}]}],
        [{"id": "untyped", "propertyDefinitions": [{"id": "p", "name": "p"}]}],
        [
            {"id": "dup", "propertyDefinitions": []},
            {"id": "dup", "propertyDefinitions": []},
        ],
    ],
)
def test_discovery_raises_on_a_malformed_catalog_payload(tmp_path, rows):
    transport = _StructuresPayloadTransport(rows)

    with pytest.raises(CapacitiesContractError):
        cb.discover_capacities_source(
            tmp_path, SPACE, _discovery_config(tmp_path, transport)
        )


# ---------------------------------------------------------------------------
# Title capture on the real read path
# ---------------------------------------------------------------------------

def _builder_adapter(tmp_path: Path, transport) -> CapacitiesAdapter:
    _write_source(tmp_path, _valid_payload())
    _write_settings_with_declarations(
        tmp_path, {"custom-project": SETTINGS_ASSIGNED_PROPERTY}
    )
    token = _valid_token_file(tmp_path)
    adapter = cb.build_capacities_adapter(
        tmp_path, cb.CapacitiesBuilderConfig(token_path=token, transport=transport)
    )
    assert adapter is not None
    return adapter


def test_a_raising_title_sink_cannot_change_a_read(tmp_path, monkeypatch):
    adapter = _builder_adapter(tmp_path, _StructuresTransport())

    def _boom(*args, **kwargs):
        raise RuntimeError("title cache exploded")

    monkeypatch.setattr(cb, "remember_titles", _boom)

    result = adapter.items_for_day_from_objects(
        TODAY, [_declared_object("project-1", boolean=True)]
    )

    assert [row["capacities_id"] for row in result.items] == ["project-1"]


def test_a_failing_title_sink_cannot_change_a_read(tmp_path, monkeypatch):
    adapter = _builder_adapter(tmp_path, _StructuresTransport())

    # A path whose parent is a file: remember_titles must return False, not
    # raise, and the read must be unaffected.
    blocked = tmp_path / "blocked"
    blocked.write_text("not a directory", encoding="utf-8")
    monkeypatch.setattr(cst, "DEFAULT_TITLES_CACHE_PATH", blocked / "titles.json")

    result = adapter.items_for_day_from_objects(
        TODAY, [_declared_object("project-1", boolean=True)]
    )

    assert [row["capacities_id"] for row in result.items] == ["project-1"]
    assert cst.read_titles(tmp_path, SPACE, cb.CAPACITIES_BASE_URL) == {}


def test_a_normal_read_captures_titles_for_its_own_namespace(tmp_path):
    adapter = _builder_adapter(tmp_path, _StructuresTransport())

    result = adapter.items_for_day_from_objects(
        TODAY, [_declared_object("project-1", boolean=True)]
    )
    assert result.items

    assert cst.read_titles(tmp_path, SPACE, cb.CAPACITIES_BASE_URL) == {
        "RootTask": "RootTask",
        "custom-project": "custom-project",
    }
    # Another space or provider URL is another namespace: no bleed-through.
    assert cst.read_titles(tmp_path, "other-space", cb.CAPACITIES_BASE_URL) == {}
    assert cst.read_titles(tmp_path, SPACE, "https://other.example") == {}


class _TagsTransport(httpx.MockTransport):
    """Serves the tag listing; every other request fails the test."""

    def __init__(self):
        self.calls: list[str] = []
        super().__init__(self._handler)

    def _handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request.url.path)
        if request.url.path != "/objects/structure":
            raise AssertionError(f"unexpected network call: {request.url}")
        return httpx.Response(
            200,
            json={
                "results": [{"id": "tag-1", "title": "habituals"}],
                "nextCursor": None,
            },
        )


def test_a_read_that_never_fetches_structures_leaves_titles_untouched(tmp_path):
    _write_source(tmp_path, _valid_payload())
    token = _valid_token_file(tmp_path)
    transport = _TagsTransport()
    adapter = cb.build_capacities_adapter(
        tmp_path, cb.CapacitiesBuilderConfig(token_path=token, transport=transport)
    )

    titles_path = cst.DEFAULT_TITLES_CACHE_PATH
    assert cst.remember_titles(
        tmp_path, SPACE, cb.CAPACITIES_BASE_URL, {"RootTask": "Task"}
    )
    before = titles_path.read_bytes()

    result = adapter.list_tags()

    assert result["status"] == "complete"
    assert [tag["id"] for tag in result["tags"]] == ["tag-1"]
    # Exactly the tag listing: the structures observer never fires, so no
    # extra request is made and the stored titles are byte-identical.
    assert transport.calls == ["/objects/structure"]
    assert titles_path.read_bytes() == before
    assert cst.read_titles(tmp_path, SPACE, cb.CAPACITIES_BASE_URL) == {
        "RootTask": "Task"
    }
