"""Resilience diagnostics for "the owner changed something in Capacities".

Two states are invisible in the projected rows, so each gets one bounded,
content-free line instead of silence:

* a configured type with ZERO members in the published generation, which makes
  every object of that type unplannable with no explanation;
* cached objects the generation does not account for (stale content outnumbers
  members), and cached objects whose stored type moved since the last Refresh,
  which the projection would otherwise map through another type's properties.

Everything here is local: ``TDTB_HOME`` is the per-test app home (conftest),
rows are fixtures or the shared fakes in ``tests/capacities_refresh_helpers``,
and no provider, credential, or real store is read. The warning text is
asserted to carry a structure label and a count only — never an object title or
a property value.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import capacities_builder as cap_builder  # noqa: E402
import capacities_refresh as rr  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent))
from capacities_refresh_helpers import (  # noqa: E402
    PRIMARY,
    _content,
    _paged_provider,
    _store,
    _structures,
)
from tests.test_plan_inputs_sources import (  # noqa: E402
    DIRECT_CUSTOM,
    DIRECT_ROOT,
    DIRECT_SPACE,
    _direct_object,
    _publish_direct,
    _save_direct_source,
)
from tests.test_capacities_refresh import Revision, _coordinator  # noqa: E402

NO_MEMBERS = (
    "Capacities type {label!r}: no objects in the published generation; "
    "Refresh to re-list it."
)
ORPHANS = (
    "{count} cached Capacities objects are not in the published generation; "
    "Refresh to reconcile."
)
RETARGETED = (
    "{count} cached Capacities objects changed type since the last Refresh; "
    "Refresh to reconcile."
)


def _store_for_source() -> object:
    return cap_builder.build_refresh_state(DIRECT_SPACE)


def _publish(objects: list[dict]) -> None:
    _save_direct_source()
    _publish_direct(objects)


# ---------------------------------------------------------------------------
# The pure counter: one line per state, nothing when the generation is clean
# ---------------------------------------------------------------------------

_PURE_CASES = [
    pytest.param(
        dict(
            type_keys=[PRIMARY],
            member_type_keys=[PRIMARY],
            cached_objects=2,
            generation_objects=2,
        ),
        [],
        id="clean-generation-is-silent",
    ),
    pytest.param(
        dict(
            type_keys=[],
            member_type_keys=[],
            cached_objects=0,
            generation_objects=0,
            structures_payload=None,
        ),
        [],
        id="nothing-configured-or-cached-is-silent",
    ),
    pytest.param(
        dict(
            type_keys=[PRIMARY, "Project"],
            member_type_keys=[PRIMARY],
            cached_objects=1,
            generation_objects=1,
        ),
        [NO_MEMBERS.format(label="Project")],
        id="zero-member-type-falls-back-to-its-id",
    ),
    pytest.param(
        dict(
            type_keys=["Project"],
            member_type_keys=[PRIMARY],
            cached_objects=1,
            generation_objects=1,
            structures_payload={
                "structures": [{"id": "Project", "title": "Project"}]
            },
        ),
        [NO_MEMBERS.format(label="Project")],
        id="zero-member-type-names-the-contract-title",
    ),
    pytest.param(
        dict(
            type_keys=[PRIMARY],
            member_type_keys=[PRIMARY],
            cached_objects=5,
            generation_objects=2,
        ),
        [ORPHANS.format(count=3)],
        id="orphans-are-one-aggregate-line",
    ),
    pytest.param(
        dict(
            type_keys=[PRIMARY],
            member_type_keys=[PRIMARY],
            cached_objects=2,
            generation_objects=2,
            retargeted=1,
        ),
        [RETARGETED.format(count=1)],
        id="retargeted-objects-are-one-aggregate-line",
    ),
    pytest.param(
        dict(
            type_keys=[PRIMARY],
            member_type_keys=[PRIMARY],
            cached_objects=1,
            generation_objects=4,
        ),
        [],
        id="more-generation-members-than-cached-objects-is-not-an-orphan",
    ),
    pytest.param(
        dict(
            type_keys=["Project", "Area", PRIMARY],
            member_type_keys=[PRIMARY],
            cached_objects=4,
            generation_objects=1,
            retargeted=2,
            structures_payload=None,
        ),
        [
            NO_MEMBERS.format(label="Area"),
            NO_MEMBERS.format(label="Project"),
            ORPHANS.format(count=3),
            RETARGETED.format(count=2),
        ],
        id="every-state-at-once-stays-ordered-and-bounded",
    ),
]


@pytest.mark.parametrize("kwargs, expected", _PURE_CASES)
def test_generation_coverage_warnings(kwargs, expected):
    warnings = rr.generation_coverage_warnings(**kwargs)

    assert warnings == expected
    for warning in warnings:
        assert "\n" not in warning
        assert len(warning) <= 200


def test_generation_coverage_warnings_never_mentions_content():
    """The counter is fed counts only, so nothing content-bearing can leak."""
    warnings = rr.generation_coverage_warnings(
        type_keys=["Project"],
        member_type_keys=[PRIMARY],
        cached_objects=3,
        generation_objects=1,
        retargeted=1,
        structures_payload={"structures": [{"id": "Project", "title": "Project"}]},
    )

    assert warnings
    for warning in warnings:
        assert "TOP-SECRET-TITLE" not in warning
        assert "PROPRIETARY-VALUE" not in warning


# ---------------------------------------------------------------------------
# The builder read: the diagnostics ride DirectIntakeRead for the intake
# ---------------------------------------------------------------------------


def test_clean_published_generation_emits_no_warning():
    _publish([
        _direct_object("obj-a", DIRECT_ROOT),
        _direct_object("obj-b", DIRECT_CUSTOM),
    ])

    read = cap_builder.read_direct_intake()

    assert read is not None
    assert read.warnings == ()


def test_zero_member_type_is_reported_once_by_name():
    _publish([_direct_object("obj-a", DIRECT_ROOT)])

    read = cap_builder.read_direct_intake()

    assert read is not None
    assert read.warnings == (NO_MEMBERS.format(label=DIRECT_CUSTOM),)


def test_cached_objects_outside_the_generation_are_one_line():
    _publish([
        _direct_object("obj-a", DIRECT_ROOT),
        _direct_object("obj-b", DIRECT_CUSTOM),
    ])
    store = _store_for_source()
    store.put(
        "obj-orphan", DIRECT_ROOT,
        _direct_object("obj-orphan", DIRECT_ROOT),
    )

    read = cap_builder.read_direct_intake()

    assert read is not None
    assert read.warnings == (ORPHANS.format(count=1),)


def test_retargeted_object_is_reported_as_a_count():
    """A cached read whose type moved upstream is projected through the
    generation's type key, so the mismatch is named instead of hidden."""
    _publish([
        _direct_object("obj-a", DIRECT_ROOT),
        _direct_object("obj-b", DIRECT_CUSTOM),
    ])
    store = _store_for_source()
    # The member stays published under RootTask while its content now says T2.
    store.put("obj-a", DIRECT_ROOT, _direct_object("obj-a", DIRECT_CUSTOM))

    read = cap_builder.read_direct_intake()

    assert read is not None
    assert read.warnings == (RETARGETED.format(count=1),)


def test_builder_warnings_never_carry_a_title_or_property_value():
    secret = _direct_object("obj-secret", DIRECT_ROOT)
    secret["properties"]["title"]["title"]["value"] = "TOP-SECRET-TITLE"
    secret["properties"]["assigned"]["boolean"] = True
    secret["properties"]["note"] = {
        "type": "text", "text": {"value": "PROPRIETARY-VALUE"},
    }
    _publish([secret, _direct_object("obj-b", DIRECT_CUSTOM)])
    store = _store_for_source()
    orphan = _direct_object("obj-orphan", DIRECT_ROOT)
    orphan["properties"]["title"]["title"]["value"] = "TOP-SECRET-TITLE"
    store.put("obj-orphan", DIRECT_ROOT, orphan)
    store.put("obj-secret", DIRECT_ROOT, {**secret, "structureId": DIRECT_CUSTOM})

    read = cap_builder.read_direct_intake()

    assert read is not None
    assert read.warnings  # the diagnostics did fire
    for warning in read.warnings:
        assert "TOP-SECRET-TITLE" not in warning
        assert "PROPRIETARY-VALUE" not in warning
        assert "obj-" not in warning


# ---------------------------------------------------------------------------
# The Refresh publish: the same lines reach the job warnings the panel renders
# ---------------------------------------------------------------------------


def _run(tmp_path, *, objects, seed=None):
    provider = _paged_provider(objects=objects, structures=_structures(PRIMARY, "T2"))
    coordinator, store, _, _ = _coordinator(tmp_path, provider, revision=Revision())
    for object_id, type_id, content in seed or ():
        store.put(object_id, type_id, content)
    coordinator.start()
    return coordinator.wait(timeout=5)


def test_refresh_publish_reports_an_empty_type_and_orphans(tmp_path):
    status = _run(
        tmp_path,
        objects={PRIMARY: ["a"], "T2": []},
        seed=[("ghost", PRIMARY, _content("ghost", PRIMARY))],
    )

    assert status["phase"] == "complete", status
    assert NO_MEMBERS.format(label="T2") in status["warnings"]
    assert ORPHANS.format(count=1) in status["warnings"]
    assert not any(RETARGETED.format(count=1) == w for w in status["warnings"])


def test_refresh_publish_reports_a_retargeted_cached_object(tmp_path):
    # Cached under T2 while its stored content declares RootTask: the reuse
    # path sees the type move without a second provider read.
    status = _run(
        tmp_path,
        objects={PRIMARY: ["a"], "T2": ["c"]},
        seed=[("c", "T2", _content("c", PRIMARY))],
    )

    assert status["phase"] == "complete", status
    assert RETARGETED.format(count=1) in status["warnings"]


def test_refresh_publish_is_silent_when_the_generation_is_clean(tmp_path):
    status = _run(tmp_path, objects={PRIMARY: ["a"], "T2": ["c"]})

    assert status["phase"] == "complete", status
    assert status["warnings"] == []
