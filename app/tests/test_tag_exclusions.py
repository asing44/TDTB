"""Matcher truth-table tests for the tag-exclusion policy.

The matcher is pure: it decides from stable ``(source, space_id, tag_id)``
identities only. Titles are display metadata, never identity. Ordinary
nonmatches fail open; unusable tag payloads under an applicable policy fail
closed by blocking planning with a structured diagnostic.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import exclusion_settings as es  # noqa: E402
import tag_exclusions as tx  # noqa: E402


SPACE = "space-1"
OTHER_SPACE = "space-2"
TAG_A = "5a25370b-f9a0-40cf-bc3a-0cab4744913c"
TAG_B = "0d194525-c5a1-4af5-bb62-202b83006b5e"


def _policy(tags=(), *, revision: int = 1, persisted: bool = True) -> tx.ExclusionPolicy:
    read = es.ExclusionSettingsRead(
        settings=es.ExclusionSettings(
            revision=revision,
            tags=tuple(
                es.TagExclusion("capacities", space, tag_id)
                for space, tag_id in tags
            ),
        ),
        persisted=persisted,
    )
    return tx.ExclusionPolicy.from_read(read)


_UNSET = object()


def _cap_row(
    name: str,
    *,
    tags: object = _UNSET,
    error: str | None = None,
    space: str = SPACE,
    include_key: bool = True,
    identity: str | None = None,
) -> dict:
    row = {
        "name": name,
        "path": f"capacities://{space}/{name}",
        "identity": identity or f"capacities:{space}:RootTask:{name}",
        "source": "capacities",
        "capacities_space_id": space,
    }
    if include_key:
        row["capacities_tags"] = [] if tags is _UNSET else tags
    if error is not None:
        row["capacities_tags_error"] = error
    return row


def _ref(tag_id: str, title: str, space: str = SPACE) -> dict:
    return {"space_id": space, "tag_id": tag_id, "title": title}


def _row(name: str, **kwargs) -> dict:
    return {
        "name": name,
        "path": f"50 - Ops/{name}.md",
        "identity": f"vault:50 - Ops/{name}.md",
        "source": "vault",
        "types": ["project"],
        "tags": ["legacy-vault-tag"],
        **kwargs,
    }


class TestRetention:
    def test_empty_policy_retains_everything_and_reports_zero_exclusions(self):
        assigned = [_cap_row("A"), _row("V")]
        pool = [_cap_row("P")]

        kept_assigned, kept_pool, report = tx.apply_tag_exclusions(
            assigned, pool, _policy()
        )

        assert kept_assigned == assigned
        assert kept_pool == pool
        assert report == {
            "version": 1,
            "revision": 1,
            "persisted": True,
            "mode": "exclude_any",
            "evaluated_counts": {"assigned": 2, "pool": 1},
            "excluded_counts": {"assigned": 0, "pool": 0, "total": 0},
            "decisions": [],
            "warnings": [],
        }

    def test_untagged_capacities_row_is_retained(self):
        kept_assigned, kept_pool, report = tx.apply_tag_exclusions(
            [_cap_row("Untagged", tags=[])],
            [],
            _policy([(SPACE, TAG_A)]),
        )

        assert [r["name"] for r in kept_assigned] == ["Untagged"]
        assert report["excluded_counts"]["total"] == 0

    def test_row_without_any_tag_metadata_is_retained(self):
        # A legacy/hand-built Capacities row with no tag metadata at all is
        # genuinely empty, not a title-only payload.
        kept_assigned, _, report = tx.apply_tag_exclusions(
            [_cap_row("Legacy", include_key=False)],
            [],
            _policy([(SPACE, TAG_A)]),
        )

        assert [r["name"] for r in kept_assigned] == ["Legacy"]
        assert report["decisions"] == []

    def test_other_source_rows_tolerate_tags_and_labels(self):
        row = _row("Todoist", source="todoist", todoist_id="42", labels=["habituals"])
        kept_assigned, _, report = tx.apply_tag_exclusions(
            [row],
            [],
            _policy([(SPACE, TAG_A)]),
        )

        assert kept_assigned == [row]
        assert kept_assigned[0]["labels"] == ["habituals"]
        assert report["decisions"] == []


class TestMatching:
    def test_matching_identity_excludes_on_both_surfaces_with_a_decision(self):
        assigned = [
            _cap_row("Keep", tags=[_ref(TAG_B, "other")]),
            _cap_row("Drop", tags=[_ref(TAG_A, "habituals")]),
        ]
        pool = [
            _cap_row("Pool drop", tags=[_ref(TAG_A, "habituals")]),
            _cap_row("Pool keep"),
        ]

        kept_assigned, kept_pool, report = tx.apply_tag_exclusions(
            assigned, pool, _policy([(SPACE, TAG_A)])
        )

        assert [r["name"] for r in kept_assigned] == ["Keep"]
        assert [r["name"] for r in kept_pool] == ["Pool keep"]
        assert report["excluded_counts"] == {"assigned": 1, "pool": 1, "total": 2}
        assert report["decisions"] == [
            {
                "identity": "capacities:space-1:RootTask:Drop",
                "name": "Drop",
                "surface": "assigned",
                "matched_tags": [
                    {"space_id": SPACE, "tag_id": TAG_A, "title": "habituals"},
                ],
                "reason": "excluded_tag",
            },
            {
                "identity": "capacities:space-1:RootTask:Pool drop",
                "name": "Pool drop",
                "surface": "pool",
                "matched_tags": [
                    {"space_id": SPACE, "tag_id": TAG_A, "title": "habituals"},
                ],
                "reason": "excluded_tag",
            },
        ]

    def test_any_match_wins_and_all_matching_identities_are_reported(self):
        row = _cap_row(
            "Multi",
            tags=[_ref(TAG_B, "beta"), _ref(TAG_A, "habituals")],
        )

        _, _, report = tx.apply_tag_exclusions(
            [row], [], _policy([(SPACE, TAG_A), (SPACE, TAG_B)])
        )

        assert len(report["decisions"]) == 1
        assert [t["tag_id"] for t in report["decisions"][0]["matched_tags"]] == [
            TAG_B,
            TAG_A,
        ]

    def test_title_is_not_identity(self):
        # Same title, different stable id → no match.
        _, kept_pool, report = tx.apply_tag_exclusions(
            [],
            [_cap_row("Imposter", tags=[_ref(TAG_B, "habituals")])],
            _policy([(SPACE, TAG_A)]),
        )

        assert [r["name"] for r in kept_pool] == ["Imposter"]
        assert report["decisions"] == []

    def test_no_case_folding_on_tag_id(self):
        _, kept_pool, report = tx.apply_tag_exclusions(
            [],
            [_cap_row("Cased", tags=[_ref(TAG_A.upper(), "habituals")])],
            _policy([(SPACE, TAG_A)]),
        )

        assert [r["name"] for r in kept_pool] == ["Cased"]
        assert report["decisions"] == []

    def test_different_space_never_matches(self):
        _, kept_pool, report = tx.apply_tag_exclusions(
            [],
            [_cap_row("Other space", tags=[_ref(TAG_A, "habituals", space=OTHER_SPACE)],
                      space=OTHER_SPACE)],
            _policy([(SPACE, TAG_A)]),
        )

        assert [r["name"] for r in kept_pool] == ["Other space"]
        assert report["decisions"] == []

    def test_renamed_tag_still_matches_and_reports_the_current_title(self):
        _, _, report = tx.apply_tag_exclusions(
            [_cap_row("Renamed", tags=[_ref(TAG_A, "habituals renamed")])],
            [],
            _policy([(SPACE, TAG_A)]),
        )

        assert report["decisions"][0]["matched_tags"] == [
            {"space_id": SPACE, "tag_id": TAG_A, "title": "habituals renamed"},
        ]

    def test_unknown_policy_identity_matches_only_a_row_still_carrying_it(self):
        _, kept_pool, report = tx.apply_tag_exclusions(
            [],
            [
                _cap_row("Same title", tags=[_ref(TAG_B, "deleted-tag")]),
                _cap_row("Still there", tags=[_ref(TAG_A, "deleted-tag")]),
            ],
            _policy([(SPACE, TAG_A)]),
        )

        assert [r["name"] for r in kept_pool] == ["Same title"]
        assert report["excluded_counts"]["pool"] == 1


class TestUnusablePayloads:
    def test_title_only_flat_tags_block_when_an_exclusion_applies(self):
        row = _cap_row("Title only", include_key=False)
        row["tags"] = ["habituals"]

        with pytest.raises(tx.TagExclusionBlocked) as caught:
            tx.apply_tag_exclusions([row], [], _policy([(SPACE, TAG_A)]))

        diagnostics = caught.value.diagnostics
        assert diagnostics["code"] == "exclusion_policy_unusable_tags"
        assert diagnostics["tasks"] == [
            {
                "identity": "capacities:space-1:RootTask:Title only",
                "name": "Title only",
                "surface": "assigned",
                "reason": "tag metadata is title-only or malformed",
            }
        ]

    def test_malformed_reference_blocks_when_an_exclusion_applies(self):
        row = _cap_row("Broken", tags=[{"space_id": SPACE}])

        with pytest.raises(tx.TagExclusionBlocked):
            tx.apply_tag_exclusions([row], [], _policy([(SPACE, TAG_A)]))

    def test_projected_unusable_payload_blocks_with_its_recorded_reason(self):
        row = _cap_row(
            "Unusable", tags=None, error="tags property is not an entity property"
        )

        with pytest.raises(tx.TagExclusionBlocked) as caught:
            tx.apply_tag_exclusions([row], [], _policy([(SPACE, TAG_A)]))

        assert caught.value.diagnostics["tasks"][0]["reason"] == (
            "tags property is not an entity property"
        )

    def test_unusable_payload_is_tolerated_when_no_policy_applies_to_the_space(self):
        row = _cap_row("Elsewhere", tags=None, error="title-only", space=OTHER_SPACE)

        kept, _, report = tx.apply_tag_exclusions([row], [], _policy([(SPACE, TAG_A)]))

        assert [r["name"] for r in kept] == ["Elsewhere"]
        assert report["decisions"] == []
        assert report["warnings"] == [
            "1 Capacities row(s) carry unusable tag metadata but no exclusion "
            "applies to their space"
        ]

    def test_unusable_payload_with_no_policy_at_all_is_tolerated_with_a_warning(self):
        row = _cap_row("Unusable", tags=None, error="title-only")

        kept, _, report = tx.apply_tag_exclusions([row], [], _policy())

        assert [r["name"] for r in kept] == ["Unusable"]
        assert len(report["warnings"]) == 1

    def test_unknown_row_space_fails_closed_under_an_active_policy(self):
        row = _cap_row("No space", tags=None, error="title-only")
        del row["capacities_space_id"]

        with pytest.raises(tx.TagExclusionBlocked):
            tx.apply_tag_exclusions([row], [], _policy([(SPACE, TAG_A)]))

    def test_block_diagnostics_are_bounded(self):
        rows = [_cap_row(f"Broken {n}", tags=[{"space_id": SPACE}]) for n in range(25)]

        with pytest.raises(tx.TagExclusionBlocked) as caught:
            tx.apply_tag_exclusions(rows, [], _policy([(SPACE, TAG_A)]))

        assert len(caught.value.diagnostics["tasks"]) == 10


class TestPolicyBridge:
    def test_from_read_carries_version_revision_and_persistence(self):
        read = es.ExclusionSettingsRead(
            settings=es.ExclusionSettings(
                revision=4,
                tags=(es.TagExclusion("capacities", SPACE, TAG_A),),
            ),
            persisted=True,
        )

        policy = tx.ExclusionPolicy.from_read(read)

        assert policy.version == 1
        assert policy.revision == 4
        assert policy.persisted is True
        assert policy.mode == "exclude_any"
        assert policy.tags[0].tag_id == TAG_A
