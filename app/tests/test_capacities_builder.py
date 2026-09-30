"""Public-interface tests for the Capacities adapter builder.

Everything here is local and fake: no provider, real credential, or network is
touched. ``tmp_path`` is both the vault root and the token-file home, and the
transport is an ``httpx.MockTransport`` that records (and asserts zero) calls.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import capacities_builder as cb  # noqa: E402
import capacities_settings as cs  # noqa: E402
from capacities_adapter import (  # noqa: E402
    CapacitiesAdapter,
    CapacitiesContractError,
    StructureMapping,
)


SPACE = "space-1"
TODAY = date(2026, 9, 29)
EXCLUDED = f"capacities:{SPACE}:RootTask:task-1"


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