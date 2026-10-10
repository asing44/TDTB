"""Offline direct-intake projector and config switch (U4 slice S1).

The projector turns the published direct-refresh cache into Capacities plan
rows with NO provider access. These tests pin the ``sources.capacities_intake``
switch, the offline assigned/pool split, the strict no-fallback refusals, the
contract-shape mapping, dedupe, and the app effective date.

Everything is local. ``TDTB_HOME`` is the autouse per-test app home from
``tests/conftest.py``, the vault is ``tmp_path``, and the credential and REST
client constructors are replaced with tripwires so a token read or a provider
call fails the test instead of passing silently.
"""
from __future__ import annotations

import dataclasses
import json
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import app_config  # noqa: E402
import capacities_builder as cb  # noqa: E402
import capacities_intake as ci  # noqa: E402
import capacities_refresh_state as crs  # noqa: E402
import capacities_rules  # noqa: E402
import tag_exclusions  # noqa: E402

SPACE = "space-1"
#: The adapter requires the canonical native structure to be mapped explicitly.
ROOT = "RootTask"
CUSTOM = "T2"
SCOPE = "all"

NOON = datetime(2026, 10, 10, 12, 0)
NOON_TS = NOON.timestamp()
OLD_TS = datetime(2026, 10, 1, 12, 0).timestamp()
NEW_TS = datetime(2026, 10, 8, 12, 0).timestamp()


# ---------------------------------------------------------------------------
# Builders: the published record, the S0 contract, and cached objects
# ---------------------------------------------------------------------------

def _definition(
    prop_id: str, kind: str, labels=None, *, writable: bool = False
) -> dict[str, Any]:
    row: dict[str, Any] = {"id": prop_id, "type": kind}
    if labels is not None:
        row["labelSet"] = [{"id": i, "name": n} for i, n in labels]
    if writable:
        row["writable"] = True
    return row


def _contract(*, drop: frozenset[str] = frozenset()) -> list[dict[str, Any]]:
    """The S0 structures payload. ``drop`` removes properties upstream."""
    properties = [
        _definition("title", "title"),
        _definition("assigned", "boolean"),
        _definition("status", "label", [("active", "Active"), ("done", "Done")]),
        _definition("due", "date"),
        _definition("completion", "label", [("done", "Done")], writable=True),
    ]
    return [
        {
            "id": type_id,
            "title": type_id,
            "propertyDefinitions": [p for p in properties if p["id"] not in drop],
        }
        for type_id in (ROOT, CUSTOM)
    ]


def _save_record(vault: Path) -> None:
    structures = []
    for type_id in (ROOT, CUSTOM):
        # A distinct completion field knows only ``done``; every other value is
        # UNKNOWN, so a custom row is an unassigned pool candidate. The root type
        # has no completion mapping, so its eligible rows are assigned.
        completion = (
            {"completion_property": "completion", "completion_value": "done"}
            if type_id == CUSTOM
            else {}
        )
        structures.append(
            cb.SourceStructureRecord(
                structure_id=type_id,
                title_property="title",
                status_property="status",
                open_status_values=("active",),
                date_property="due",
                assignment_property="assigned",
                assignment_values=("true",),
                **completion,
            )
        )
    cb.save_source(
        vault,
        expected_revision=0,
        space_id=SPACE,
        structures=structures,
    )


def _object(
    object_id: str,
    type_id: str,
    *,
    assigned: bool = True,
    due: str | None = None,
) -> dict[str, Any]:
    properties: dict[str, Any] = {
        "title": {"type": "title", "title": {"value": object_id}},
        "assigned": {"type": "boolean", "boolean": assigned},
        "status": {"type": "label", "label": [{"id": "active", "name": "Active"}]},
    }
    if due is not None:
        properties["due"] = {"type": "date", "date": {"start": due}}
    return {"id": object_id, "structureId": type_id, "properties": properties}


def _publish(
    vault: Path,
    objects: list[dict[str, Any]],
    *,
    structures: Any,
    checked: dict[str, float] | None = None,
    expected_generation: int = 0,
) -> crs.CompleteSnapshot:
    """Install one complete generation through the production store seam."""
    store = cb.build_refresh_state(vault, SPACE)
    listed: dict[str, list[str]] = {}
    for obj in objects:
        store.put(obj["id"], obj["structureId"], obj)
        listed.setdefault(obj["structureId"], []).append(obj["id"])
    checked = checked or {}
    evidence = crs.SnapshotEvidence(
        scope_key=SCOPE,
        revision=1,
        required_types=tuple(listed),
        listings=tuple(
            crs.TypeListing(
                type_key=type_id,
                object_ids=tuple(ids),
                listing_checked_at=checked.get(type_id, NOON_TS),
            )
            for type_id, ids in listed.items()
        ),
    )
    return store.install_generation(
        evidence,
        expected_generation=expected_generation,
        structures=structures,
    )


def _corrupt_cached_object(object_id: str) -> None:
    objects_dir = cb.refresh_state_dir() / crs.OBJECTS_DIRNAME
    for path in objects_dir.glob("*.json"):
        if json.loads(path.read_text(encoding="utf-8")).get("object_id") == object_id:
            path.write_text("{not json", encoding="utf-8")
            return
    raise AssertionError(f"no cached object stored for {object_id!r}")


def _load(vault: Path, now: datetime = NOON):
    return ci.load_direct_rows(vault, now=now)


def _names(rows: list[dict[str, Any]]) -> set[str]:
    return {row["name"] for row in rows}


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    root.mkdir()
    return root


@pytest.fixture
def no_token_no_provider(monkeypatch):
    """Any credential read or REST client construction fails the test."""

    def _tripwire(*_args, **_kwargs):
        raise AssertionError("the offline intake must not read a credential or build a client")

    monkeypatch.setattr(cb, "load_capacities_token", _tripwire)
    monkeypatch.setattr(cb, "CapacitiesRestClient", _tripwire)


# ---------------------------------------------------------------------------
# Part 1: sources.capacities_intake switch
# ---------------------------------------------------------------------------

def _write_config(text: str) -> None:
    path = app_config.config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_sources(sources: dict[str, Any]) -> None:
    _write_config(json.dumps({"version": 1, "sources": sources}))


def test_intake_switch_defaults_to_legacy_without_a_config_file():
    assert app_config.capacities_intake() == "legacy"
    assert app_config.sources_config_warnings() == []


def test_intake_switch_selects_direct_only_when_explicitly_set():
    _write_sources({"mode": "artifact", "capacities_intake": "direct"})

    assert app_config.capacities_intake() == "direct"
    assert app_config.sources_config_warnings() == []


def test_intake_switch_legacy_is_accepted_explicitly():
    _write_sources({"capacities_intake": "legacy"})

    assert app_config.capacities_intake() == "legacy"
    assert app_config.sources_config_warnings() == []


@pytest.mark.parametrize(
    "value", ["Direct", " direct", "both", "", None, True, 1, ["direct"]]
)
def test_invalid_intake_value_resolves_to_legacy_with_a_warning(value):
    _write_sources({"capacities_intake": value})

    assert app_config.capacities_intake() == "legacy"
    warnings = app_config.sources_config_warnings()
    assert len(warnings) == 1
    assert "capacities_intake" in warnings[0]
    assert "legacy" in warnings[0]


@pytest.mark.parametrize(
    "text",
    ["{not json", json.dumps({"version": 2, "sources": {"capacities_intake": "direct"}})],
)
def test_unusable_config_never_selects_direct(text):
    _write_config(text)

    assert app_config.capacities_intake() == "legacy"
    assert app_config.sources_config_warnings() == []


# ---------------------------------------------------------------------------
# Part 2: offline projection
# ---------------------------------------------------------------------------

def test_offline_projection_splits_assigned_rows_from_pool_rows(
    vault, no_token_no_provider
):
    _save_record(vault)
    _publish(
        vault,
        [
            _object("root-assigned", ROOT, assigned=True),
            _object("custom-pool", CUSTOM, assigned=True),
            _object("custom-unassigned", CUSTOM, assigned=False),
        ],
        structures=_contract(),
    )

    assigned, pool, warnings, coverage = _load(vault)

    assert _names(assigned) == {"root-assigned"}
    assert _names(pool) == {"custom-pool"}
    assert all(row["assigned"] is True for row in assigned)
    assert all(row["assigned"] is not True for row in pool)
    # The UNKNOWN-completion pool row is surfaced as the adapter's own review
    # warning; the offline path adds no refusal text.
    assert len(warnings) == 1
    assert warnings[0].startswith("Capacities review")
    assert coverage.members == 3
    assert coverage.unreadable == 0
    assert coverage.evaluated == 3
    assert coverage.malformed == 0


def test_offline_provider_refuses_every_provider_call():
    provider = ci._OfflineProvider(_contract())

    with pytest.raises(ci.ProviderCallDefect):
        provider.list_objects(ROOT)
    with pytest.raises(ci.ProviderCallDefect):
        provider.get_object("root-assigned")
    with pytest.raises(ci.ProviderCallDefect):
        provider.patch_object("root-assigned", {})


def test_absent_source_record_is_silently_not_configured(vault):
    assert _load(vault) == ([], [], [], ci.DirectCoverage())


# ---------------------------------------------------------------------------
# Part 2: strict no-fallback refusals
# ---------------------------------------------------------------------------

def test_missing_snapshot_serves_no_rows_and_requires_refresh(
    vault, no_token_no_provider
):
    _save_record(vault)

    assigned, pool, warnings, coverage = _load(vault)

    assert (assigned, pool) == ([], [])
    assert len(warnings) == 1
    assert "Refresh required" in warnings[0]
    assert coverage.members == 0


def test_missing_structure_contract_serves_no_rows(vault, no_token_no_provider):
    _save_record(vault)
    _publish(vault, [_object("root-assigned", ROOT)], structures=None)

    assigned, pool, warnings, _ = _load(vault)

    assert (assigned, pool) == ([], [])
    assert len(warnings) == 1
    assert "Refresh required" in warnings[0]
    assert "contract" in warnings[0]


def test_contract_from_a_prior_generation_is_never_served(vault, no_token_no_provider):
    _save_record(vault)
    _publish(vault, [_object("root-assigned", ROOT)], structures=_contract())
    _publish(
        vault,
        [_object("root-assigned", ROOT)],
        structures=None,
        expected_generation=1,
    )

    assigned, pool, warnings, _ = _load(vault)

    assert (assigned, pool) == ([], [])
    assert "contract" in warnings[0]


def test_damaged_cached_member_serves_no_rows_and_counts_it(
    vault, no_token_no_provider
):
    _save_record(vault)
    _publish(
        vault,
        [_object("root-assigned", ROOT), _object("custom-assigned", CUSTOM)],
        structures=_contract(),
    )
    _corrupt_cached_object("custom-assigned")

    assigned, pool, warnings, coverage = _load(vault)

    assert (assigned, pool) == ([], [])
    assert coverage.unreadable == 1
    assert any("1 cached object" in w and "Refresh required" in w for w in warnings)


def test_contract_shape_error_maps_to_refresh_required_without_rows(
    vault, no_token_no_provider
):
    """A property removed upstream is a contract error, not a crash."""
    _save_record(vault)
    _publish(
        vault,
        [_object("root-assigned", ROOT)],
        structures=_contract(drop=frozenset({"assigned"})),
    )

    assigned, pool, warnings, _ = _load(vault)

    assert (assigned, pool) == ([], [])
    assert len(warnings) == 1
    assert "Refresh required" in warnings[0]


@pytest.mark.parametrize("failure", ["rules", "tag-exclusion"])
def test_unusable_rules_or_exclusion_metadata_fails_closed(
    vault, no_token_no_provider, monkeypatch, failure
):
    _save_record(vault)
    _publish(vault, [_object("root-assigned", ROOT)], structures=_contract())

    if failure == "rules":
        def broken_rules(space_id, **_kwargs):
            raise capacities_rules.RulesFormatError("malformed rules")

        monkeypatch.setattr(capacities_rules, "load_rules", broken_rules)
    else:
        def blocked(*_args, **_kwargs):
            raise tag_exclusions.TagExclusionBlocked({"message": "blocked metadata"})

        monkeypatch.setattr(tag_exclusions, "apply_tag_exclusions", blocked)

    assigned, pool, warnings, _ = _load(vault)

    assert (assigned, pool) == ([], [])
    assert len(warnings) == 1


# ---------------------------------------------------------------------------
# Part 2: type-level warnings
# ---------------------------------------------------------------------------

def test_configured_type_absent_from_snapshot_needs_refresh(
    vault, no_token_no_provider
):
    """The custom type is mapped but never listed in the published generation."""
    _save_record(vault)
    _publish(vault, [_object("root-assigned", ROOT)], structures=_contract())

    assigned, _pool, warnings, _ = _load(vault)

    assert _names(assigned) == {"root-assigned"}
    assert [w for w in warnings if "needs Refresh" in w and CUSTOM in w]


def test_type_checked_before_the_newest_check_is_marked_unchanged_since(
    vault, no_token_no_provider
):
    """The single-type Rescan leaves the other type's check time behind."""
    _save_record(vault)
    _publish(
        vault,
        [_object("root-assigned", ROOT), _object("custom-assigned", CUSTOM)],
        structures=_contract(),
        checked={ROOT: OLD_TS, CUSTOM: NEW_TS},
    )

    assigned, pool, warnings, _ = _load(vault)

    assert _names(assigned) | _names(pool) == {"root-assigned", "custom-assigned"}
    stale = [w for w in warnings if "unchanged since" in w]
    assert len(stale) == 1
    assert ROOT in stale[0]
    assert "2026-10-01" in stale[0]
    assert not any(CUSTOM in w and "unchanged since" in w for w in warnings)


# ---------------------------------------------------------------------------
# Part 2: dedupe and effective date
# ---------------------------------------------------------------------------

def test_duplicate_projected_rows_are_deduped_by_identity(
    vault, no_token_no_provider, monkeypatch
):
    _save_record(vault)
    _publish(vault, [_object("root-assigned", ROOT)], structures=_contract())
    real_read = cb.read_direct_intake

    def doubled(root):
        read = real_read(root)
        return dataclasses.replace(read, objects=read.objects + read.objects)

    monkeypatch.setattr(cb, "read_direct_intake", doubled)

    assigned, pool, _warnings, coverage = _load(vault)

    assert [row["name"] for row in assigned] == ["root-assigned"]
    assert pool == []
    assert coverage.members == 1


class _NoToday(date):
    """Any ``date.today()`` call in the projector fails the test."""

    @classmethod
    def today(cls):
        raise AssertionError("the intake must use the app effective date, not date.today()")


class _FrozenDatetime(datetime):
    """01:30 local: still the previous logical day under the 02:00 rollover."""

    @classmethod
    def now(cls, tz=None):
        return cls(2026, 10, 10, 1, 30)


def test_projection_uses_the_app_effective_date_not_date_today(
    vault, no_token_no_provider, monkeypatch
):
    """A due-tomorrow object is future at 01:30 (logical day 10-09) and is
    served only once the rollover moves the logical day to 10-10."""
    _save_record(vault)
    _publish(
        vault,
        [_object("root-due", ROOT, assigned=True, due="2026-10-10")],
        structures=_contract(),
    )
    monkeypatch.setattr(ci, "date", _NoToday)

    before_rollover, _, _, _ = _load(vault, now=datetime(2026, 10, 10, 1, 30))
    after_rollover, _, _, _ = _load(vault, now=datetime(2026, 10, 10, 2, 30))

    assert before_rollover == []
    assert _names(after_rollover) == {"root-due"}


def test_default_clock_path_uses_the_effective_date(
    vault, no_token_no_provider, monkeypatch
):
    _save_record(vault)
    _publish(
        vault,
        [_object("root-due", ROOT, assigned=True, due="2026-10-10")],
        structures=_contract(),
    )
    monkeypatch.setattr(ci, "date", _NoToday)
    monkeypatch.setattr(ci, "datetime", _FrozenDatetime)

    assigned, _pool, _warnings, _ = ci.load_direct_rows(vault)

    assert assigned == []
