"""Stored-rule admission behavior for the direct Capacities path (U3b-1).

Test-first for the stored-rule eligibility slice: the active per-type stored
rule is the SINGLE eligibility authority for its structure; an under-evaluated
object stays visible as an unassigned row with structured rule state and the
rules revision it was decided under; and every compatibility case (no record,
draft-only entry, wrong space) keeps the legacy assignment decision unchanged.
"""
from __future__ import annotations

from datetime import date
import threading
from types import SimpleNamespace

import pytest

import capacities_adapter as ca
import capacities_builder as cb
import capacities_rules as cr
from tests.test_capacities_adapter import (
    FakeProvider,
    _mapping,
    _object,
    _prop,
    _structures,
)

DAY = date(2026, 9, 29)
SPACE = "space-1"
RULE = {"prop": "minutes", "op": "gt", "values": [10]}
DRAFT_RULE = {"prop": "minutes", "op": "gt", "values": [100]}


def rules(active=RULE, draft=None, space=SPACE, revision=2):
    return cr.RulesRecord(
        space,
        (cr.RuleStructureRecord("custom-project", active, draft),),
        revision=revision,
    )


def adapter(record):
    return ca.CapacitiesAdapter(
        FakeProvider(_structures(), {}),
        ca.CapacitiesConfig(SPACE, _mapping(), rules=record),
    )


def obj(minutes=20, assigned="no", state="active", object_id="obj-1"):
    props = {
        "title": _prop("title", "title", "Sample"),
        "tdtb": _prop("label", "label", [{"id": assigned, "name": assigned}]),
        "state": _prop("label", "label", [{"id": state, "name": state}]),
    }
    if minutes is not None:
        props["minutes"] = _prop("number", "number", minutes)
    return _object(object_id, "custom-project", props)


# ---------------------------------------------------------------------------
# The stored active rule is the single eligibility authority
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("minutes,assigned,count", [(20, "no", 1), (5, "yes", 0)])
def test_stored_rule_is_the_single_eligibility_authority(minutes, assigned, count):
    """The rule decides admission; the legacy assignment signal is not ANDed.

    A matching rule admits an object the source does not mark assigned, and a
    non-matching rule excludes an object the source does mark assigned.
    """
    result = adapter(rules()).items_for_day_from_objects(DAY, [obj(minutes, assigned)])
    assert len(result.items) == count
    if count:
        row = result.items[0]
        assert row["assigned"] is True
        assert row["identity"] == "capacities:space-1:custom-project:obj-1"
        assert row["path"] == "capacities://space-1/obj-1"
        assert row["capacities_rule"] == {"state": "match", "revision": 2}


def test_active_rule_decides_even_when_the_draft_would_decide_otherwise():
    record = rules(active=RULE, draft=DRAFT_RULE)
    result = adapter(record).items_for_day_from_objects(DAY, [obj(20)])
    assert len(result.items) == 1
    assert result.items[0]["capacities_rule"]["state"] == "match"


def test_empty_all_rule_is_a_real_rule_not_the_legacy_default():
    """``{"all": []}`` matches everything; it is distinct from no stored rule."""
    result = adapter(rules(active={"all": []})).items_for_day_from_objects(
        DAY, [obj(20, "no")]
    )
    assert len(result.items) == 1
    assert result.items[0]["capacities_rule"]["state"] == "match"


def test_unknown_rule_is_retained_as_unassigned_not_silently_dropped():
    """An under-evaluated object stays visible with structured rule state."""
    result = adapter(rules()).items_for_day_from_objects(DAY, [obj(None)])
    assert len(result.items) == 1
    row = result.items[0]
    assert row["assigned"] is False
    assert row["capacities_rule"] == {"state": "unknown", "revision": 2}
    assert row["identity"] == "capacities:space-1:custom-project:obj-1"
    assert row["path"] == "capacities://space-1/obj-1"


# ---------------------------------------------------------------------------
# Legacy compatibility: no active rule keeps evaluate_assignment unchanged
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "record",
    [rules(active=None, draft=RULE), rules(space="other-space")],
    ids=["draft-only", "other-space"],
)
def test_draft_only_or_other_space_rules_do_not_override_legacy(record):
    result = adapter(record).items_for_day_from_objects(
        DAY, [obj(20, "no", object_id="obj-a"), obj(5, "yes", object_id="obj-b")]
    )
    assert len(result.items) == 1
    row = result.items[0]
    assert row["capacities_id"] == "obj-b"
    assert "capacities_rule" not in row
    assert row["capacities_assignment"]["source_assigned"] is True


def test_rule_cannot_override_known_closed_status():
    assert adapter(rules()).items_for_day_from_objects(DAY, [obj(state="done")]).items == []


# ---------------------------------------------------------------------------
# Enumeration gate honours an active rule-only structure
# ---------------------------------------------------------------------------

def test_rule_only_structure_is_enumerated():
    """A structure whose only eligibility path is a stored rule is listed.

    Without the rule the same mapping has no legacy way to contribute, so it
    must not be enumerated at all (no wasted provider requests).
    """
    rule_mapping = ca.StructureMapping(
        "custom-project",
        open_status_property="state",
        open_status_values=frozenset({"active"}),
    )
    root_mapping = ca.StructureMapping(
        "RootTask",
        open_status_property="status",
        open_status_values=frozenset({"open"}),
    )
    pages = {("custom-project", None): {"objects": [obj()], "next_cursor": None}}

    provider = FakeProvider(_structures(), pages)
    source = ca.CapacitiesAdapter(
        provider,
        ca.CapacitiesConfig(SPACE, (rule_mapping, root_mapping), rules=rules()),
    )
    assert len(source.items_for_day(DAY).items) == 1
    assert ("custom-project", None) in provider.list_calls

    legacy_provider = FakeProvider(_structures(), pages)
    legacy_source = ca.CapacitiesAdapter(
        legacy_provider,
        ca.CapacitiesConfig(SPACE, (rule_mapping, root_mapping)),
    )
    assert legacy_source.items_for_day(DAY).items == []
    assert ("custom-project", None) not in legacy_provider.list_calls


# ---------------------------------------------------------------------------
# Builder seams: direct refresh loads rules; the legacy adapter does not
# ---------------------------------------------------------------------------

def _source_record(revision=3):
    return cb.SourceRecord(
        space_id=SPACE,
        structures=(
            cb.SourceStructureRecord(
                structure_id="custom-project",
                assignment_property="tdtb",
                assignment_values=("yes",),
            ),
        ),
        revision=revision,
    )


def test_direct_refresh_wires_rules_and_revision_but_legacy_does_not(tmp_path, monkeypatch):
    source = _source_record()
    monkeypatch.setattr(cb, "read_source", lambda _: source)
    monkeypatch.setattr(cb, "load_capacities_token", lambda _: "synthetic-token")
    monkeypatch.setattr(cr, "load_rules", lambda space_id: rules())
    monkeypatch.setattr(cb, "build_refresh_state", lambda *args: object())
    captured = {}

    def coordinator(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(**kwargs)

    monkeypatch.setattr(cb.capacities_refresh, "RefreshCoordinator", coordinator)
    config = cb.CapacitiesBuilderConfig(content_cache=None, refresh_state_path=tmp_path)

    built = cb.build_refresh_coordinator(tmp_path, config)
    assert built.rules == rules()
    # Source revision 3 + settings revision 0 + rules revision 2.
    assert cb.refresh_config_revision(tmp_path) == 5

    legacy = cb.build_capacities_adapter(tmp_path, config)
    assert legacy.config.rules is None


def test_refresh_config_revision_tracks_a_real_rules_save(tmp_path, monkeypatch):
    monkeypatch.setattr(cb, "read_source", lambda _: _source_record())
    before = cb.refresh_config_revision(tmp_path)
    cr.save_rule(SPACE, "custom-project", RULE, schema={"minutes": "number"})
    after = cb.refresh_config_revision(tmp_path)
    assert (before, after) == (3, 4)


def test_coordinator_passes_rules_to_its_internal_adapter_config(tmp_path):
    record = rules()
    store = cb.build_refresh_state(
        tmp_path, SPACE, cb.CapacitiesBuilderConfig(refresh_state_path=tmp_path / "state")
    )
    coordinator = cb.capacities_refresh.RefreshCoordinator(
        root=tmp_path / "state",
        store=store,
        provider=FakeProvider(_structures(), {}),
        space_id=SPACE,
        mappings=_mapping(),
        revision_supplier=lambda: 0,
        rules=record,
    )
    assert coordinator.adapter.config.rules is record


# ---------------------------------------------------------------------------
# Publication guard excludes a rules save
# ---------------------------------------------------------------------------

def test_publication_guard_takes_rules_store_lock(tmp_path, monkeypatch):
    handles = []
    acquire = cb.capacities_cache_io.acquire_path_lock

    def tracked(path):
        handles.append(path)
        return acquire(path)

    monkeypatch.setattr(cb.capacities_cache_io, "acquire_path_lock", tracked)
    with cb._config_save_guard(tmp_path):
        assert cr.lock_path() in handles


def test_publication_guard_excludes_a_real_rules_save(tmp_path):
    """A rules save cannot land while the publication guard is held.

    The revision the coordinator guards on now includes the rules revision, so
    a save landing mid-window would let stale rules publish. Drive the real
    ``save_rule`` and prove it cannot complete until the guard releases.
    """
    entered = threading.Event()
    release = threading.Event()

    def hold_guard():
        with cb._config_save_guard(tmp_path):
            entered.set()
            release.wait(timeout=5)

    holder = threading.Thread(target=hold_guard)
    holder.start()
    assert entered.wait(timeout=5)

    done = threading.Event()

    def save_like():
        cr.save_rule(SPACE, "custom-project", RULE, schema={"minutes": "number"})
        done.set()

    saver = threading.Thread(target=save_like)
    saver.start()
    assert not done.wait(timeout=0.3)

    release.set()
    holder.join(timeout=5)
    saver.join(timeout=5)
    assert done.is_set()
    assert cr.load_rules(SPACE).structure("custom-project").active == RULE
