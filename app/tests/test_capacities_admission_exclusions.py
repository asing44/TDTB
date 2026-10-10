"""Direct-refresh tag-exclusion admission for the Capacities adapter (U3b-4).

Test-first for the direct-refresh half of the tag-exclusion slice:

* the adapter's ``CapacitiesConfig`` carries an OPTIONAL
  ``tag_exclusions.ExclusionPolicy``; when supplied, projected candidates are
  split into source-assigned and unassigned surfaces and filtered through the
  shared ``tag_exclusions.apply_tag_exclusions`` matcher — never a second
  matcher;
* a matched stable tag identity removes a source-assigned rule MATCH and an
  UNKNOWN unassigned candidate alike, while a same-title tag with a different
  id, a tag identity in another space, and an absent/empty policy retain the
  row;
* unusable applicable tag metadata raises ``TagExclusionBlocked`` from the
  adapter and fails the coordinator without installing a generation; the
  previous complete snapshot is retained and the fixed job warning carries no
  payload;
* the bounded removal notice reports counts and stable identities only, and
  the review warning is recomputed from SURVIVING items so an excluded
  UNKNOWN row is never counted as needing review;
* the direct-refresh builder reads the exclusions store and wires the policy
  plus its revision into the combined revision and the publication guard,
  while the legacy ``build_capacities_adapter`` path is unchanged.
"""
from __future__ import annotations

from datetime import date
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import capacities_adapter as ca  # noqa: E402
import capacities_builder as cb  # noqa: E402
import capacities_refresh as rr  # noqa: E402
import capacities_rules as cr  # noqa: E402
import exclusion_settings as es  # noqa: E402
import tag_exclusions as tx  # noqa: E402
from tests.test_capacities_adapter import (  # noqa: E402
    FakeProvider,
    _definition,
    _object,
    _prop,
)
from tests.test_capacities_rule_fallbacks import (  # noqa: E402
    DAY,
    SPACE,
    _custom_rule,
    _fixture_structures,
    _legacy_root_mapping,
    _rules,
    _status_mapping,
)

sys.path.insert(0, str(Path(__file__).parent))
from capacities_refresh_helpers import (  # noqa: E402
    PRIMARY,
    Clock,
    Sleeper,
    _paged_provider,
    _row,
    _store,
)

TAG_A = "5a25370b-f9a0-40cf-bc3a-0cab4744913c"
TAG_B = "0d194525-c5a1-4af5-bb62-202b83006b5e"
OTHER_SPACE = "space-2"
REMOVAL_PREFIX = "Capacities tag exclusions"
REVIEW_PREFIX = "Capacities review"


def _policy(tags=(), *, revision: int = 1) -> tx.ExclusionPolicy:
    read = es.ExclusionSettingsRead(
        settings=es.ExclusionSettings(
            revision=revision,
            tags=tuple(
                es.TagExclusion("capacities", space, tag_id)
                for space, tag_id in tags
            ),
        ),
        persisted=True,
    )
    return tx.ExclusionPolicy.from_read(read)


def _structures_with_tags():
    structures = _fixture_structures()
    for structure in structures:
        if structure["id"] == "custom-project":
            structure["propertyDefinitions"].append(_definition("tags", "entity"))
    return structures


def _tagged_object(
    *,
    minutes=20,
    tdtb="yes",
    state="active",
    tag_id=None,
    tag_title="habituals",
    flat_tags=None,
    object_id="obj-1",
):
    properties = {"title": _prop("title", "title", "Sample")}
    if minutes is not None:
        properties["minutes"] = _prop("number", "number", minutes)
    if tdtb is not None:
        properties["tdtb"] = _prop("label", "label", [{"id": tdtb, "name": tdtb}])
    if state is not None:
        properties["state"] = _prop("label", "label", [{"id": state, "name": state}])
    if tag_id is not None:
        properties["tags"] = _prop(
            "entity", "entity", [{"id": tag_id, "title": tag_title}]
        )
    obj = _object(object_id, "custom-project", properties)
    if flat_tags is not None:
        obj["tags"] = list(flat_tags)
    return obj


def _adapter(policy, record=None, *, mappings=None):
    return ca.CapacitiesAdapter(
        FakeProvider(_structures_with_tags(), {}),
        ca.CapacitiesConfig(
            SPACE,
            tuple(
                mappings
                if mappings is not None
                else [_legacy_root_mapping(), _status_mapping()]
            ),
            rules=record if record is not None else _rules(_custom_rule()),
            exclusion_policy=policy,
        ),
    )


def _removals(warnings):
    return [warning for warning in warnings if warning.startswith(REMOVAL_PREFIX)]


def _reviews(warnings):
    return [warning for warning in warnings if warning.startswith(REVIEW_PREFIX)]


# ---------------------------------------------------------------------------
# Matcher admission through the adapter projection
# ---------------------------------------------------------------------------

def test_matched_tag_excludes_a_source_assigned_rule_match():
    """A stable tag match removes a rule MATCH the source marks assigned: the
    stored rule is the eligibility authority, never an exclusion bypass."""
    result = _adapter(_policy([(SPACE, TAG_A)])).items_for_day_from_objects(
        DAY, [_tagged_object(minutes=20, tdtb="yes", tag_id=TAG_A)]
    )

    assert result.items == []
    assert result.evaluated == 1
    removal = _removals(result.warnings)
    assert len(removal) == 1
    assert "1 candidate(s)" in removal[0]
    assert "capacities:space-1:custom-project:obj-1" in removal[0]
    assert "Sample" not in removal[0]


def test_matched_tag_excludes_an_unknown_unassigned_candidate():
    result = _adapter(_policy([(SPACE, TAG_A)])).items_for_day_from_objects(
        DAY, [_tagged_object(minutes=None, tag_id=TAG_A)]
    )

    assert result.items == []
    assert result.evaluated == 1
    removal = _removals(result.warnings)
    assert len(removal) == 1
    assert "1 candidate(s)" in removal[0]


def test_same_tag_title_with_a_different_id_is_not_excluded():
    result = _adapter(_policy([(SPACE, TAG_A)])).items_for_day_from_objects(
        DAY, [_tagged_object(tag_id=TAG_B, tag_title="habituals")]
    )

    assert len(result.items) == 1
    assert result.items[0]["identity"] == "capacities:space-1:custom-project:obj-1"
    assert _removals(result.warnings) == []


def test_tag_identity_in_another_space_is_not_excluded():
    result = _adapter(_policy([(OTHER_SPACE, TAG_A)])).items_for_day_from_objects(
        DAY, [_tagged_object(tag_id=TAG_A)]
    )

    assert len(result.items) == 1
    assert _removals(result.warnings) == []


def test_removal_notice_is_bounded_and_carries_no_title():
    objects = [
        _tagged_object(tag_id=TAG_A, object_id=f"obj-{index}")
        for index in range(5)
    ]

    result = _adapter(_policy([(SPACE, TAG_A)])).items_for_day_from_objects(
        DAY, objects
    )

    assert result.items == []
    removal = _removals(result.warnings)
    assert len(removal) == 1
    assert "5 candidate(s)" in removal[0]
    assert removal[0].count("capacities:space-1:custom-project:") == 3
    assert "+2 more" in removal[0]
    assert "Sample" not in removal[0]
    assert len(removal[0]) < 400


# ---------------------------------------------------------------------------
# Absent/empty policy tolerates; unusable applicable metadata blocks
# ---------------------------------------------------------------------------

def test_no_policy_tolerates_unusable_tag_metadata():
    result = _adapter(None).items_for_day_from_objects(
        DAY, [_tagged_object(flat_tags=["habituals"])]
    )

    assert len(result.items) == 1
    assert _removals(result.warnings) == []
    assert not any("tag metadata" in warning for warning in result.warnings)


def test_empty_policy_tolerates_unusable_tag_metadata_without_excluding():
    result = _adapter(_policy()).items_for_day_from_objects(
        DAY, [_tagged_object(flat_tags=["habituals"])]
    )

    assert len(result.items) == 1
    assert _removals(result.warnings) == []
    assert any("unusable tag metadata" in warning for warning in result.warnings)


def test_unusable_applicable_tag_metadata_raises_and_propagates():
    with pytest.raises(tx.TagExclusionBlocked) as caught:
        _adapter(_policy([(SPACE, TAG_A)])).items_for_day_from_objects(
            DAY, [_tagged_object(flat_tags=["habituals"])]
        )

    diagnostics = caught.value.diagnostics
    assert diagnostics["code"] == "exclusion_policy_unusable_tags"
    assert diagnostics["tasks"][0]["identity"] == (
        "capacities:space-1:custom-project:obj-1"
    )


# ---------------------------------------------------------------------------
# The review surface is recomputed from SURVIVING items
# ---------------------------------------------------------------------------

def test_excluded_unknown_row_does_not_repeat_a_review_warning():
    result = _adapter(_policy([(SPACE, TAG_A)])).items_for_day_from_objects(
        DAY,
        [
            _tagged_object(minutes=None, tag_id=TAG_A, object_id="obj-1"),
            _tagged_object(minutes=None, object_id="obj-2"),
        ],
    )

    # The excluded row still counts as evaluated coverage; only the surviving
    # UNKNOWN row produces the review warning.
    assert result.evaluated == 2
    assert [row["identity"] for row in result.items] == [
        "capacities:space-1:custom-project:obj-2"
    ]
    review = _reviews(result.warnings)
    assert len(review) == 1
    assert "1 unassigned candidate" in review[0]
    assert "obj-2" in review[0]
    assert "obj-1" not in review[0]


# ---------------------------------------------------------------------------
# Coordinator: exclusions publish, a block fails complete-only
# ---------------------------------------------------------------------------

def _coordinator(tmp_path, provider, policy, *, store=None, record=None):
    clock = Clock()
    return rr.RefreshCoordinator(
        root=tmp_path / "state",
        store=store if store is not None else _store(tmp_path),
        provider=provider,
        space_id=SPACE,
        mappings=(_legacy_root_mapping(), _status_mapping()),
        revision_supplier=lambda: 1,
        scope_key="all",
        rules=record if record is not None else _rules(_custom_rule()),
        logical_day=DAY,
        exclusion_policy=policy,
        clock=clock,
        sleeper=Sleeper(clock),
    )


def test_coordinator_publishes_with_a_bounded_removal_warning(tmp_path):
    pages = {
        ("custom-project", None): {
            "objects": [
                _row("obj-1", "custom-project"),
                _row("obj-2", "custom-project"),
            ],
            "next_cursor": None,
        }
    }
    content = {
        "obj-1": _tagged_object(tag_id=TAG_A, object_id="obj-1"),
        "obj-2": _tagged_object(object_id="obj-2"),
    }
    provider = FakeProvider(_structures_with_tags(), pages, content)
    store = _store(tmp_path)
    coordinator = _coordinator(
        tmp_path, provider, _policy([(SPACE, TAG_A)]), store=store
    )

    coordinator.start()
    status = coordinator.wait(timeout=5)

    assert status["phase"] == "complete"
    assert status["outcome"] == "published"
    removal = _removals(status["warnings"])
    assert len(removal) == 1
    assert "capacities:space-1:custom-project:obj-1" in removal[0]
    assert "Sample" not in " ".join(status["warnings"])
    # The content snapshot is a complete generation and retains every fetched
    # object; the tag exclusion is a planning filter, not a fetch filter.
    snapshot = store.load_snapshot("all")
    assert snapshot is not None
    assert {member.object_id for member in snapshot.members} == {"obj-1", "obj-2"}


def test_coordinator_tag_exclusion_block_retains_the_previous_generation(tmp_path):
    pages = {
        ("custom-project", None): {
            "objects": [_row("obj-1", "custom-project")],
            "next_cursor": None,
        }
    }
    content = {"obj-1": _tagged_object(flat_tags=["habituals"], object_id="obj-1")}
    provider = FakeProvider(_structures_with_tags(), pages, content)
    store = _store(tmp_path)

    first = _coordinator(tmp_path, provider, None, store=store)
    first.start()
    published = first.wait(timeout=5)
    assert published["phase"] == "complete"
    assert published["outcome"] == "published"
    prior = store.load_snapshot("all")
    assert prior is not None and prior.generation == 1

    second = _coordinator(
        tmp_path, provider, _policy([(SPACE, TAG_A)]), store=store
    )
    second.start()
    blocked = second.wait(timeout=5)

    assert blocked["phase"] == "failed"
    assert blocked["outcome"] == "noCapacities"
    assert blocked["warnings"] == [rr.TAG_EXCLUSION_WARNING]
    assert "obj-1" not in rr.TAG_EXCLUSION_WARNING
    assert "Sample" not in rr.TAG_EXCLUSION_WARNING
    retained = store.load_snapshot("all")
    assert retained is not None
    assert retained.generation == prior.generation
    assert {member.object_id for member in retained.members} == {"obj-1"}


# ---------------------------------------------------------------------------
# Builder seams: direct refresh wires exclusions; legacy does not
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


def _read(revision=7):
    return es.ExclusionSettingsRead(
        settings=es.ExclusionSettings(
            revision=revision,
            tags=(es.TagExclusion("capacities", SPACE, TAG_A),),
        ),
        persisted=True,
    )


def test_direct_refresh_wires_the_exclusion_policy_but_legacy_does_not(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(cb, "read_source", lambda _: _source_record())
    monkeypatch.setattr(cb, "load_capacities_token", lambda _: "synthetic-token")
    monkeypatch.setattr(cr, "load_rules", lambda space_id: _rules(_custom_rule()))
    monkeypatch.setattr(cb.exclusion_settings, "read_settings", lambda _: _read(7))
    monkeypatch.setattr(cb, "build_refresh_state", lambda *args: object())
    captured = {}

    def coordinator(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(**kwargs)

    monkeypatch.setattr(cb.capacities_refresh, "RefreshCoordinator", coordinator)
    config = cb.CapacitiesBuilderConfig(content_cache=None, refresh_state_path=tmp_path)

    built = cb.build_refresh_coordinator(tmp_path, config)
    assert built.exclusion_policy.revision == 7
    assert built.exclusion_policy.tags[0].tag_id == TAG_A
    # Source revision 3 + settings revision 0 + rules revision 2 + exclusions 7.
    assert cb.refresh_config_revision(tmp_path) == 12

    legacy = cb.build_capacities_adapter(tmp_path, config)
    assert legacy.config.exclusion_policy is None


def test_legacy_builder_never_reads_the_exclusions_store(tmp_path, monkeypatch):
    monkeypatch.setattr(cb, "read_source", lambda _: _source_record())
    monkeypatch.setattr(cb, "load_capacities_token", lambda _: "synthetic-token")

    def boom(_):
        raise AssertionError("the legacy path must not read exclusion settings")

    monkeypatch.setattr(cb.exclusion_settings, "read_settings", boom)

    legacy = cb.build_capacities_adapter(
        tmp_path, cb.CapacitiesBuilderConfig(content_cache=None)
    )
    assert legacy.config.exclusion_policy is None


def test_source_absent_returns_none_before_reading_exclusions(tmp_path, monkeypatch):
    monkeypatch.setattr(cb, "read_source", lambda _: None)

    def boom(_):
        raise AssertionError("exclusions must not be read when the source is absent")

    monkeypatch.setattr(cb.exclusion_settings, "read_settings", boom)

    assert cb.refresh_config_revision(tmp_path) is None
    config = cb.CapacitiesBuilderConfig(refresh_state_path=tmp_path / "state")
    assert cb.build_refresh_coordinator(tmp_path, config) is None


def test_refresh_config_revision_tracks_a_real_exclusion_save(tmp_path, monkeypatch):
    monkeypatch.setattr(cb, "read_source", lambda _: _source_record())
    before = cb.refresh_config_revision(tmp_path)
    es.save_settings(
        tmp_path,
        expected_revision=0,
        exclusions=[es.TagExclusion("capacities", SPACE, TAG_A)],
    )
    after = cb.refresh_config_revision(tmp_path)
    assert (before, after) == (3, 4)


def test_build_freezes_the_configuration_revision_before_config_reads(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(cb, "read_source", lambda _: _source_record())
    monkeypatch.setattr(cb, "load_capacities_token", lambda _: "synthetic-token")

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

    monkeypatch.setattr(cb, "CapacitiesRestClient", FakeClient)
    before = cb.refresh_config_revision(tmp_path)
    coordinator = cb.build_refresh_coordinator(
        tmp_path, cb.CapacitiesBuilderConfig(refresh_state_path=tmp_path / "state")
    )

    assert coordinator is not None
    assert coordinator.configuration_revision == before

    es.save_settings(
        tmp_path,
        expected_revision=0,
        exclusions=[es.TagExclusion("capacities", SPACE, TAG_A)],
    )
    assert coordinator.configuration_revision == before
    assert cb.refresh_config_revision(tmp_path) != before


def _primary_source_record(revision=3):
    return cb.SourceRecord(
        space_id=SPACE,
        structures=(
            cb.SourceStructureRecord(
                structure_id=PRIMARY,
                status_property="status",
                open_status_values=("active",),
                assignment_property="assigned",
                assignment_values=("true",),
            ),
        ),
        revision=revision,
    )


def test_build_then_exclusion_save_fails_the_first_start_stale(tmp_path, monkeypatch):
    monkeypatch.setattr(cb, "read_source", lambda _: _primary_source_record())
    monkeypatch.setattr(cb, "load_capacities_token", lambda _: "synthetic-token")
    provider = _paged_provider(objects={PRIMARY: ["a"]})
    monkeypatch.setattr(cb, "CapacitiesRestClient", lambda *a, **k: provider)
    coordinator = cb.build_refresh_coordinator(
        tmp_path, cb.CapacitiesBuilderConfig(refresh_state_path=tmp_path / "state")
    )
    assert coordinator is not None
    frozen = coordinator.configuration_revision

    es.save_settings(
        tmp_path,
        expected_revision=0,
        exclusions=[es.TagExclusion("capacities", SPACE, TAG_A)],
    )
    assert cb.refresh_config_revision(tmp_path) == frozen + 1

    coordinator.start()
    status = coordinator.wait(timeout=5)

    assert status["phase"] == "failed", status
    assert status["outcome"] == "staleConfiguration", status
    assert coordinator.store.load_snapshot("all") is None


# ---------------------------------------------------------------------------
# Publication guard excludes an exclusions save
# ---------------------------------------------------------------------------

def test_publication_guard_takes_the_exclusions_lock_innermost(tmp_path, monkeypatch):
    handles = []
    acquire = cb.capacities_cache_io.acquire_path_lock

    def tracked(path):
        handles.append(path)
        return acquire(path)

    monkeypatch.setattr(cb.capacities_cache_io, "acquire_path_lock", tracked)

    with cb._config_save_guard(tmp_path):
        assert handles[-1] == es.lock_path()

    assert handles == [
        cb.lock_path(),
        cb.capacities_settings.lock_path(),
        cr.lock_path(),
        es.lock_path(),
    ]


def test_publication_guard_excludes_a_real_exclusion_save(tmp_path):
    """An exclusions save cannot land while the publication guard is held.

    The exclusions revision is part of the combined revision, so a save
    landing mid-window would let stale scope publish. Drive the real
    ``save_settings`` and prove it cannot complete until the guard releases.
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
        es.save_settings(
            tmp_path,
            expected_revision=0,
            exclusions=[es.TagExclusion("capacities", SPACE, TAG_A)],
        )
        done.set()

    saver = threading.Thread(target=save_like)
    saver.start()
    assert not done.wait(timeout=0.3)

    release.set()
    holder.join(timeout=5)
    saver.join(timeout=5)
    assert done.is_set()
    stored = es.read_settings(tmp_path).settings
    assert stored.revision == 1
    assert stored.tags[0].tag_id == TAG_A


# ---------------------------------------------------------------------------
# Frozen revision: build read ordering and source-space switches
# ---------------------------------------------------------------------------

def _primary_record_for(space_id, revision, *, open_values=("active",)):
    return cb.SourceRecord(
        space_id=space_id,
        structures=(
            cb.SourceStructureRecord(
                structure_id=PRIMARY,
                status_property="status",
                open_status_values=open_values,
                assignment_property="assigned",
                assignment_values=("true",),
            ),
        ),
        revision=revision,
    )


def test_build_reads_the_revision_before_the_source_record(tmp_path, monkeypatch):
    """A source save between the two build reads must fail stale, not publish.

    ``build_refresh_coordinator`` reads the space, then the combined
    revision, then the source record it derives mappings from. The first two
    reads here see the pre-save record (frozen revision 3) and the record
    read sees the post-save one, so publication compares live 4 against
    frozen 3 and fails stale. Reading the record before the revision instead
    lets a save land between those reads and freeze the NEW revision with
    the OLD mappings, so the publication guard sees the live revision
    unchanged and publishes scope the configuration no longer describes.
    """
    reads = {"count": 0}

    def flipping_read_source(_vault):
        reads["count"] += 1
        if reads["count"] <= 2:
            return _primary_record_for(SPACE, 3, open_values=("active",))
        return _primary_record_for(SPACE, 4, open_values=("done",))

    monkeypatch.setattr(cb, "read_source", flipping_read_source)
    monkeypatch.setattr(cb, "load_capacities_token", lambda _: "synthetic-token")
    provider = _paged_provider(objects={PRIMARY: ["a"]})
    monkeypatch.setattr(cb, "CapacitiesRestClient", lambda *a, **k: provider)

    coordinator = cb.build_refresh_coordinator(
        tmp_path, cb.CapacitiesBuilderConfig(refresh_state_path=tmp_path / "state")
    )

    assert coordinator is not None
    # The space pre-read and the revision read came before the record read,
    # so the frozen revision (3) trails the post-save record. Pre-fix the
    # record read came before the revision read, so the save landed between
    # them and froze the post-save 4 with the pre-save mappings, and the run
    # published.
    assert coordinator.configuration_revision == 3

    coordinator.start()
    status = coordinator.wait(timeout=5)

    assert status["phase"] == "failed", status
    assert status["outcome"] == "staleConfiguration", status
    assert coordinator.store.load_snapshot("all") is None


def test_source_space_switch_with_an_equal_revision_sum_fails_stale(
    tmp_path, monkeypatch
):
    """A space switch that keeps the combined revision sum equal must not
    publish against the previous space.

    The combined revision is a SUM of per-store revisions: moving the source
    record to another space raises the source revision by one while the
    previous space's rules document stops applying (revision 0), so the sum can
    stay equal while the live source is a different space. The publication
    guard must compare the live source space, not only the revision sum.
    """
    vault = tmp_path / "vault"
    vault.mkdir()
    structures = _primary_record_for(SPACE, 0).structures
    for expected in range(3):
        cb.save_source(
            vault,
            expected_revision=expected,
            space_id=SPACE,
            structures=structures,
        )
    cr.save_rule(
        SPACE,
        PRIMARY,
        {"prop": "minutes", "op": "gt", "values": [10]},
        expected_revision=0,
    )
    # Source 3 + settings 0 + rules 1 + exclusions 0.
    assert cb.refresh_config_revision(vault) == 4

    monkeypatch.setattr(cb, "load_capacities_token", lambda _: "synthetic-token")
    provider = _paged_provider(objects={PRIMARY: ["a"]})
    monkeypatch.setattr(cb, "CapacitiesRestClient", lambda *a, **k: provider)
    coordinator = cb.build_refresh_coordinator(
        vault, cb.CapacitiesBuilderConfig(refresh_state_path=tmp_path / "state")
    )
    assert coordinator is not None
    assert coordinator.configuration_revision == 4
    assert coordinator.space_id == SPACE

    # The switch: source 3 -> 4 in another space; the space-1 rules no longer
    # apply, so the live sum is unchanged at 4 while the live space moved.
    cb.save_source(
        vault,
        expected_revision=3,
        space_id="space-B",
        structures=structures,
    )
    assert cb.refresh_config_revision(vault) == 4
    assert cb.read_source(vault).space_id == "space-B"

    coordinator.start()
    status = coordinator.wait(timeout=5)

    assert status["phase"] == "failed", status
    assert status["outcome"] == "staleConfiguration", status
    assert coordinator.store.load_snapshot("all") is None


def test_space_switch_between_the_build_reads_fails_closed(tmp_path, monkeypatch):
    """A source space switch landing between the build's reads fails closed.

    The coordinator's space and the publication guard's comparison target
    both come from the record read. If the source switches between the
    revision read and the record read, the guard would compare the live
    space with the post-switch space and match it, and only the lossy
    revision sum could still catch the switch; a settings save that restores
    the sum (the switch adds the new space's source revision while dropping
    the previous space's rules revision) would then mask it and the job
    would publish against the wrong space. The build must refuse instead,
    leaving the next start to build cleanly against the new space.
    """
    vault = tmp_path / "vault"
    vault.mkdir()
    structures = _primary_record_for(SPACE, 0).structures
    for expected in range(4):
        cb.save_source(
            vault,
            expected_revision=expected,
            space_id=SPACE,
            structures=structures,
        )
    cr.save_rule(
        SPACE,
        PRIMARY,
        {"prop": "minutes", "op": "gt", "values": [10]},
        expected_revision=0,
    )
    # Source 4 + settings 0 + rules 1 + exclusions 0.
    assert cb.refresh_config_revision(vault) == 5

    reads = {"count": 0}

    def switching_read_source(_vault):
        reads["count"] += 1
        if reads["count"] >= 3:
            return _primary_record_for("space-B", 4, open_values=("done",))
        return _primary_record_for(SPACE, 4, open_values=("active",))

    monkeypatch.setattr(cb, "read_source", switching_read_source)

    with pytest.raises(RuntimeError, match="space changed"):
        cb.build_refresh_coordinator(
            vault, cb.CapacitiesBuilderConfig(refresh_state_path=tmp_path / "state")
        )


def test_space_switch_between_the_space_and_revision_reads_fails_closed(
    tmp_path, monkeypatch
):
    """A source space switch landing before the revision read fails closed.

    The mismatch check is only sound if the space is read before the
    revision: the pre-read space must trail the post-switch record. If the
    revision read moved above the space pre-read, the pre-read space would
    already be the post-switch space and the check would pass vacuously, so
    a later settings save restoring the lossy revision sum would mask the
    switch and the job would publish against the wrong space.
    """
    reads = {"count": 0}

    def switching_read_source(_vault):
        reads["count"] += 1
        if reads["count"] == 1:
            return _primary_record_for(SPACE, 3, open_values=("active",))
        return _primary_record_for("space-B", 3, open_values=("done",))

    monkeypatch.setattr(cb, "read_source", switching_read_source)

    with pytest.raises(RuntimeError, match="space changed"):
        cb.build_refresh_coordinator(
            tmp_path, cb.CapacitiesBuilderConfig(refresh_state_path=tmp_path / "state")
        )


def test_publication_guard_fails_stale_when_the_space_read_is_unreadable(
    tmp_path, monkeypatch
):
    """A None space read at publication must fail stale, not publish.

    Broken source storage at publish time makes the space read return None;
    a non-str is never equal to the built space, so the guard must fail
    closed instead of treating the unreadable space as unchanged.
    """
    vault = tmp_path / "vault"
    vault.mkdir()
    structures = _primary_record_for(SPACE, 0).structures
    for expected in range(3):
        cb.save_source(
            vault,
            expected_revision=expected,
            space_id=SPACE,
            structures=structures,
        )
    monkeypatch.setattr(cb, "load_capacities_token", lambda _: "synthetic-token")
    provider = _paged_provider(objects={PRIMARY: ["a"]})
    monkeypatch.setattr(cb, "CapacitiesRestClient", lambda *a, **k: provider)
    coordinator = cb.build_refresh_coordinator(
        vault, cb.CapacitiesBuilderConfig(refresh_state_path=tmp_path / "state")
    )
    assert coordinator is not None

    coordinator.space_id_supplier = lambda: None
    coordinator.start()
    status = coordinator.wait(timeout=5)

    assert status["phase"] == "failed", status
    assert status["outcome"] == "staleConfiguration", status
    assert coordinator.store.load_snapshot("all") is None
