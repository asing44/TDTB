"""Tests for shadow.py — T13 shadow-mode commit (manifest build, live diff,
gather_live_state degradation). No live network/EventKit calls: gather_live_state
tests monkeypatch todoist_client / calendar_bridge."""
from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import shadow  # noqa: E402


FIXTURES_DIR = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# build_plan_manifest
# ---------------------------------------------------------------------------

class TestBuildPlanManifest:
    def test_zero_block_assigned_item_becomes_all_day_todoist_intent(self):
        digest = {"assigned": [{
            "name": "All Day Vault",
            "path": "50 - Operations/Projects/All Day Vault.md",
            "blocks": 0,
            "types": ["project"],
        }]}
        manifest = shadow.build_plan_manifest(digest, {"sequence": []}, {})

        [row] = [m for m in manifest if m.step == "A"]
        assert row.action == "schedule-all-day"
        assert row.name == "All Day Vault"
        assert row.time is None and row.duration_min == 0
        assert row.routing == "PHEP"

    def test_zero_block_todoist_item_preserves_task_identity(self):
        digest = {"assigned": [{
            "name": "All Day Task",
            "path": "todoist://t42",
            "blocks": 0,
            "source": "todoist",
        }]}
        manifest = shadow.build_plan_manifest(digest, {"sequence": []}, {})
        [row] = [m for m in manifest if m.step == "A"]
        assert row.action == "schedule-all-day"
        assert row.id_or_path == "todoist://t42"

    def test_all_day_diff_updates_timed_task_but_noops_date_only_task(self):
        manifest = [shadow.ManifestEntry(
            step="A", system="todoist", action="schedule-all-day",
            name="All Day Task", id_or_path="todoist://t42",
            time=None, duration_min=0, routing="Inbox",
        )]
        timed = shadow.diff_against_live(manifest, {"todoist_tasks": [{
            "id": "t42", "content": "All Day Task",
            "due": {"date": "2026-07-20T09:00:00"},
        }]})
        date_only = shadow.diff_against_live(manifest, {"todoist_tasks": [{
            "id": "t42", "content": "All Day Task",
            "due": {"date": "2026-07-20"},
        }]})
        assert timed.entries[0].classification == shadow.UPDATE
        assert date_only.entries[0].classification == shadow.NOOP

    def test_assigned_rows_become_step_a_todoist(self):
        fixture = load_fixture("day_a_overlap_conflict.json")
        digest = fixture["digest"]
        sequence = {
            "sequence": [
                {"id": "Garage Buildout", "start": "09:00", "end": "10:00", "zone": "any"},
                {"id": "Entryway Design", "start": "10:00", "end": "11:00", "zone": "any"},
            ]
        }
        manifest = shadow.build_plan_manifest(digest, sequence, fixture["config"])
        step_a = [m for m in manifest if m.step == "A"]
        assert {m.name for m in step_a} == {"Garage Buildout", "Entryway Design"}
        garage = next(m for m in step_a if m.name == "Garage Buildout")
        assert garage.system == "todoist"
        assert garage.time == "09:00"
        assert garage.duration_min == 60
        assert garage.routing == "PHEP"
        assert garage.id_or_path == "50 - Operations/Projects/Garage Buildout.md"

    def test_unmatched_rows_become_step_d_calendar(self):
        """A row that is neither assigned, Trinoor, nor a configured anchored
        block is a schedulable block (Minting/Shivery Jigs) -> Step D."""
        digest = {"assigned": [{"name": "Garage Buildout", "path": "P/Garage.md"}]}
        sequence = {
            "sequence": [
                {"id": "Garage Buildout", "start": "09:00", "end": "10:00", "zone": "any"},
                {"id": "🟡 Minting 1", "start": "13:00", "end": "13:30", "zone": "any"},
            ]
        }
        manifest = shadow.build_plan_manifest(digest, sequence, {})
        d_rows = [m for m in manifest if m.step == "D"]
        assert len(d_rows) == 1
        assert d_rows[0].name == "🟡 Minting 1"
        assert d_rows[0].system == "calendar"
        assert d_rows[0].duration_min == 30
        assert d_rows[0].routing == "🟡 Mint"

    def test_mint_session_row_routes_to_mint(self):
        manifest = shadow.build_plan_manifest(
            {"assigned": []},
            {"sequence": [{"id": "Mint Morning", "start": "09:00", "end": "09:30"}]},
            {})
        [row] = [m for m in manifest if m.name == "Mint Morning"]
        assert row.routing == "🟡 Mint"

    def test_anchored_block_rows_become_step_e(self):
        digest = {"assigned": []}
        sequence = {"sequence": [{"id": "Foods Dinner", "start": "18:00", "end": "18:30", "zone": "any"}]}
        config = {"anchored_blocks": [{"id": "Foods Dinner", "on": True}]}
        manifest = shadow.build_plan_manifest(digest, sequence, config)
        e_rows = [m for m in manifest if m.step == "E"]
        assert len(e_rows) == 1
        assert e_rows[0].name == "Foods Dinner"
        assert e_rows[0].system == "calendar"
        assert e_rows[0].action == "create-event"
        assert e_rows[0].routing == "⬜ Blocks"

    def test_anchored_block_skipped_today_emits_no_row(self):
        digest = {"assigned": []}
        sequence = {
            "sequence": [
                {"id": "Sudsing", "start": "16:00", "end": "16:30", "zone": "any"},
                {"id": "Foods Dinner", "start": "18:00", "end": "18:30", "zone": "any"},
            ]
        }
        config = {"anchored_blocks": [
            {"id": "Sudsing", "skip_today": True},
            {"id": "Foods Dinner"},
        ]}
        manifest = shadow.build_plan_manifest(digest, sequence, config)
        names = [m.name for m in manifest if m.step == "E"]
        assert names == ["Foods Dinner"]

    def test_anchored_block_toggled_off_emits_no_row(self):
        digest = {"assigned": []}
        sequence = {"sequence": [{"id": "Wind down", "start": "21:30", "end": "22:00", "zone": "any"}]}
        config = {"anchored_blocks": [{"id": "Wind down", "on": False}]}
        manifest = shadow.build_plan_manifest(digest, sequence, config)
        assert [m.step for m in manifest if m.step == "E"] == []

    def test_live_with_micro_adventure_routes_to_todoist_step_a(self):
        """SKILL.md Step E Live rule: micro-adventure set -> Todoist create
        in the Step A batch, NOT a BusyCal event."""
        digest = {"assigned": []}
        sequence = {"sequence": [{"id": "Live", "start": "20:30", "end": "21:30", "zone": "any"}]}
        config = {
            "anchored_blocks": [{"id": "Live"}],
            "micro_adventure": {"id": "ma03", "idea": "Cook something new", "category": "food"},
        }
        manifest = shadow.build_plan_manifest(digest, sequence, config)
        assert [m.step for m in manifest if m.step == "E"] == []
        live_rows = [m for m in manifest if m.step == "A"]
        assert len(live_rows) == 1
        assert live_rows[0].system == "todoist"
        assert live_rows[0].name == "🌱 Cook something new"
        assert live_rows[0].routing == "Inbox"
        assert live_rows[0].duration_min == 60

    def test_live_without_micro_adventure_stays_step_e_calendar(self):
        digest = {"assigned": []}
        sequence = {"sequence": [{"id": "Live", "start": "20:30", "end": "21:30", "zone": "any"}]}
        config = {"anchored_blocks": [{"id": "Live"}]}
        manifest = shadow.build_plan_manifest(digest, sequence, config)
        e_rows = [m for m in manifest if m.step == "E"]
        assert len(e_rows) == 1
        assert e_rows[0].system == "calendar"

    def test_trinoor_rows_become_step_d_prime(self):
        digest = {"assigned": []}
        sequence = {"sequence": [{"id": "Trinoor : Morning", "start": "08:30", "end": "12:30", "zone": "work_hours"}]}
        manifest = shadow.build_plan_manifest(digest, sequence, {})
        d_prime = [m for m in manifest if m.step == "D′"]
        assert len(d_prime) == 1
        assert d_prime[0].name == "Trinoor : Morning"

    def test_inbox_routing_for_non_phep_preset_type(self):
        digest = {"assigned": [{"name": "Quick Tasks", "path": None}]}
        sequence = {"sequence": [{"id": "Quick Tasks", "start": "13:00", "end": "13:30", "zone": "any"}]}
        config = {"presets": [{"name": "Quick Tasks", "type": "task"}]}
        manifest = shadow.build_plan_manifest(digest, sequence, config)
        row = next(m for m in manifest if m.step == "A")
        assert row.routing == "Inbox"

    def test_phep_routing_from_item_types_without_preset(self):
        # Shakedown 2026-07-14 (Magic Mirror -> Inbox): vault items carry their
        # types in the digest payload; PHEP routing must not require a Presets row.
        digest = {"assigned": [{"name": "Magic Mirror", "path": "P/Magic Mirror.md",
                                 "types": ["project"]}]}
        sequence = {"sequence": [{"id": "Magic Mirror", "start": "18:15", "end": "18:45", "zone": "any"}]}
        manifest = shadow.build_plan_manifest(digest, sequence, {})
        row = next(m for m in manifest if m.step == "A")
        assert row.routing == "PHEP"

    def test_inbox_routing_when_types_not_phep_and_no_preset(self):
        digest = {"assigned": [{"name": "Hotel finds", "path": None, "types": ["capture"]}]}
        sequence = {"sequence": [{"id": "Hotel finds", "start": "13:00", "end": "13:30", "zone": "any"}]}
        manifest = shadow.build_plan_manifest(digest, sequence, {})
        row = next(m for m in manifest if m.step == "A")
        assert row.routing == "Inbox"

    def test_always_emits_step_b(self):
        manifest = shadow.build_plan_manifest({"assigned": []}, {"sequence": []}, {})
        steps = [m.step for m in manifest]
        assert steps.count("B") == 1
        b_row = next(m for m in manifest if m.step == "B")
        assert b_row.system == "vault" and b_row.action == "patch"

    def test_recent_selections_is_not_a_manifest_step(self):
        """Recent-selections append is a separate post-commit action
        (runstate.append_recent_selection) — never a manifest row."""
        manifest = shadow.build_plan_manifest(
            {"assigned": [{"name": "X", "path": "P/X.md"}]},
            {"sequence": [{"id": "X", "start": "09:00", "end": "10:00", "zone": "any"}]},
            {},
        )
        assert all("recent-selections" not in m.name for m in manifest)
        assert all(m.action != "append" for m in manifest)

    def test_step_c_one_row_per_assigned_item(self):
        digest = {
            "assigned": [
                {"name": "Garage Buildout", "path": "P/Garage.md"},
                {"name": "Entryway Design", "path": "P/Entryway.md"},
            ]
        }
        manifest = shadow.build_plan_manifest(digest, {"sequence": []}, {})
        step_c = [m for m in manifest if m.step == "C"]
        assert {m.id_or_path for m in step_c} == {"P/Garage.md", "P/Entryway.md"}
        assert all(m.action == "set-flag" and m.system == "vault" for m in step_c)

    def test_empty_digest_and_sequence_still_yields_b_only(self):
        manifest = shadow.build_plan_manifest({}, {}, {})
        assert [m.step for m in manifest] == ["B"]


class TestPopulatedTitleCaseConfig:
    """ISS-1 regression: build_plan_manifest fed a REAL vault config.

    Every other test in this file passes ``config={}`` or a lowercase app
    fixture, so the section-key lookups (`Presets` / `Anchored Lifestyle
    Blocks`) were never exercised against the title-case dict that
    ``config_reader.parse_config_markdown`` actually produces. That gap let a
    title-case/lowercase mismatch silently disable PHEP routing and Step-E
    anchored events on real config (Townhome Pontification → Inbox instead of
    PHEP). These tests drive the real parse→manifest seam, not a hand-built
    dict, so they can't drift from the parser's true output.
    """

    # Minimal real-shaped config markdown — title-case `## ` headings, record
    # tables keyed by their original column names (Presets: Name/Type/...;
    # Anchored: Block/...), exactly as config_reader emits.
    _CONFIG_MD = """\
## Presets

| Name                  | Type    | Blocks | Priority |
|-----------------------|---------|--------|----------|
| Townhome Pontification | pursuit | 2      | 3        |
| Quick Tasks           | task    | 1      | 2        |

## Anchored Lifestyle Blocks

| Block       | Type     | Start | End   | Duration | Days | overlap_allowed |
|-------------|----------|-------|-------|----------|------|-----------------|
| Foods Dinner | anchored | 18:00 | 18:30 | 1        | all  | no              |
"""

    def _parsed_config(self):
        import config_reader  # sys.path already includes app dir (top of file)
        return config_reader.parse_config_markdown(self._CONFIG_MD)

    def test_assigned_pursuit_preset_routes_to_phep_from_real_config(self):
        """The Townhome repro: an assigned item that is a `pursuit`-typed Preset
        must route PHEP. Pre-fix, the title-case `Presets` key was missed →
        ptype=None → Inbox."""
        config = self._parsed_config()
        # Sanity: the parser really does key title-case, not lowercase.
        assert "Presets" in config and "presets" not in config
        digest = {"assigned": [{"name": "Townhome Pontification",
                                "path": "50 - Operations/Pursuits/Townhome Pontification.md"}]}
        sequence = {"sequence": [
            {"id": "Townhome Pontification", "start": "09:00", "end": "10:00", "zone": "any"},
        ]}
        manifest = shadow.build_plan_manifest(digest, sequence, config)
        row = next(m for m in manifest if m.step == "A")
        assert row.routing == "PHEP"

    def test_non_phep_preset_type_still_routes_inbox_from_real_config(self):
        """A `task`-typed Preset is not a PHEP type → Inbox. Guards against an
        over-broad fix that routes everything to PHEP."""
        config = self._parsed_config()
        digest = {"assigned": [{"name": "Quick Tasks", "path": None}]}
        sequence = {"sequence": [
            {"id": "Quick Tasks", "start": "13:00", "end": "13:30", "zone": "any"},
        ]}
        manifest = shadow.build_plan_manifest(digest, sequence, config)
        row = next(m for m in manifest if m.step == "A")
        assert row.routing == "Inbox"

    def test_anchored_block_step_e_fires_from_real_config(self):
        """A sequence row matching a title-case `Anchored Lifestyle Blocks` row
        must emit a Step-E calendar event. Pre-fix, the section key was missed →
        anchored={} → the row fell through to a generic Step-D block."""
        config = self._parsed_config()
        assert "Anchored Lifestyle Blocks" in config
        digest = {"assigned": []}
        sequence = {"sequence": [
            {"id": "Foods Dinner", "start": "18:00", "end": "18:30", "zone": "any"},
        ]}
        manifest = shadow.build_plan_manifest(digest, sequence, config)
        assert [m.step for m in manifest if m.step == "E"] == ["E"]
        assert [m.step for m in manifest if m.step == "D"] == []  # not misrouted to D

    def test_lowercase_app_fixture_config_still_works(self):
        """The fix dual-handles — it must not break the app-fixture lowercase
        shape the other tests rely on."""
        digest = {"assigned": [{"name": "Foo", "path": "P/Foo.md"}]}
        sequence = {"sequence": [{"id": "Foo", "start": "09:00", "end": "10:00", "zone": "any"}]}
        config = {"presets": [{"name": "Foo", "type": "project"}]}
        manifest = shadow.build_plan_manifest(digest, sequence, config)
        assert next(m for m in manifest if m.step == "A").routing == "PHEP"


# ---------------------------------------------------------------------------
# diff_against_live — classification
# ---------------------------------------------------------------------------

def _todoist_row(name="Garage Buildout", time="09:00", path="P/Garage.md"):
    return shadow.ManifestEntry(
        step="A", system="todoist", action="schedule",
        name=name, id_or_path=path, time=time, duration_min=60, routing="PHEP",
    )


def _flag_row(name="Garage Buildout", path="P/Garage.md"):
    return shadow.ManifestEntry(step="C", system="vault", action="set-flag", name=name, id_or_path=path)


def _calendar_row(name="Foods Dinner", time="18:00"):
    return shadow.ManifestEntry(
        step="D", system="calendar", action="create-event",
        name=name, id_or_path=name, time=time, duration_min=30, routing="⬜ Blocks",
    )


def _patch_row():
    return shadow.ManifestEntry(step="B", system="vault", action="patch", name="# TDTB Plan", id_or_path="daily")


def _anchored_event_row(name="Sudsing", time="16:00"):
    return shadow.ManifestEntry(
        step="E", system="calendar", action="create-event",
        name=name, id_or_path=name, time=time, duration_min=30, routing="⬜ Blocks",
    )


class TestDiffClassification:
    def test_todoist_no_live_match_is_would_create(self):
        diff = shadow.diff_against_live([_todoist_row()], {"todoist_tasks": []})
        assert diff.entries[0].classification == shadow.CREATE

    def test_todoist_matching_time_is_no_op(self):
        live = {"todoist_tasks": [{"id": "1", "content": "Garage Buildout",
                                    "due": {"datetime": "2026-07-12T09:00:00Z"}}]}
        diff = shadow.diff_against_live([_todoist_row()], live)
        assert diff.entries[0].classification == shadow.NOOP

    def test_todoist_v1_due_date_timed_is_no_op(self):
        # ISS-5: live Todoist unified-API v1 task carries the timed value under
        # `due.date` (no `due.datetime` key). shadow must read the time from it.
        live = {"todoist_tasks": [{"id": "1", "content": "Garage Buildout",
                                    "due": {"date": "2026-07-12T09:00:00"}}]}
        diff = shadow.diff_against_live([_todoist_row()], live)
        assert diff.entries[0].classification == shadow.NOOP

    def test_todoist_v1_due_date_timed_diff_is_would_update(self):
        # `due.date` timed but different from the manifest -> would-update, and the
        # delta must read the live time off `due.date` (11:00), not fall to None.
        live = {"todoist_tasks": [{"id": "1", "content": "Garage Buildout",
                                    "due": {"date": "2026-07-12T11:00:00"}}]}
        diff = shadow.diff_against_live([_todoist_row()], live)
        entry = diff.entries[0]
        assert entry.classification == shadow.UPDATE
        assert entry.detail["due_time"] == {"old": "11:00", "new": "09:00"}

    def test_todoist_due_date_only_has_no_time(self):
        # A date-only `due.date` (no "T") has no time-of-day -> None, so it differs
        # from the manifest's 09:00 -> would-update reading old=None. Guards against
        # mis-parsing "2026-07-12" as a timed value.
        live = {"todoist_tasks": [{"id": "1", "content": "Garage Buildout",
                                    "due": {"date": "2026-07-12"}}]}
        diff = shadow.diff_against_live([_todoist_row()], live)
        entry = diff.entries[0]
        assert entry.classification == shadow.UPDATE
        assert entry.detail["due_time"] == {"old": None, "new": "09:00"}

    def test_todoist_different_time_is_would_update_with_delta(self):
        live = {"todoist_tasks": [{"id": "1", "content": "Garage Buildout",
                                    "due": {"datetime": "2026-07-12T11:00:00Z"}}]}
        diff = shadow.diff_against_live([_todoist_row()], live)
        entry = diff.entries[0]
        assert entry.classification == shadow.UPDATE
        assert entry.detail["due_time"] == {"old": "11:00", "new": "09:00"}
        assert entry.detail["task_id"] == "1"

    def test_vault_flag_already_true_is_no_op(self):
        live = {"vault_frontmatter": {"P/Garage.md": {"assigned": True}}}
        diff = shadow.diff_against_live([_flag_row()], live)
        assert diff.entries[0].classification == shadow.NOOP

    def test_vault_flag_false_is_would_update(self):
        live = {"vault_frontmatter": {"P/Garage.md": {"assigned": False}}}
        diff = shadow.diff_against_live([_flag_row()], live)
        entry = diff.entries[0]
        assert entry.classification == shadow.UPDATE
        assert entry.detail["assigned"] == {"old": False, "new": True}

    def test_vault_flag_target_missing_is_conflict(self):
        diff = shadow.diff_against_live([_flag_row()], {"vault_frontmatter": {}})
        assert diff.entries[0].classification == shadow.CONFLICT

    def test_calendar_no_match_is_would_create(self):
        diff = shadow.diff_against_live([_calendar_row()], {"calendar_events": []})
        assert diff.entries[0].classification == shadow.CREATE

    def test_calendar_matching_event_is_no_op(self):
        import datetime as dt
        live = {"calendar_events": [{"id": "e1", "title": "Foods Dinner",
                                      "start": dt.datetime(2026, 7, 12, 18, 0)}]}
        diff = shadow.diff_against_live([_calendar_row()], live)
        assert diff.entries[0].classification == shadow.NOOP

    def test_calendar_time_shifted_title_match_is_no_op_not_create(self):
        # Shakedown 2026-07-14 dup vector: bridge cannot move events, so a
        # same-title live event at another time must diff as the counterpart
        # (no-op + time_mismatch detail), never as a duplicate would-create.
        import datetime as dt
        live = {"calendar_events": [{"id": "e1", "title": "Foods Dinner",
                                      "start": dt.datetime(2026, 7, 12, 19, 30)}]}
        diff = shadow.diff_against_live([_calendar_row()], live)
        entry = diff.entries[0]
        assert entry.classification == shadow.NOOP
        assert entry.detail["time_mismatch"] == {"live": "19:30", "planned": "18:00"}

    def test_calendar_exact_time_match_preferred_over_title_hit(self):
        import datetime as dt
        live = {"calendar_events": [
            {"id": "early", "title": "Foods Dinner", "start": dt.datetime(2026, 7, 12, 12, 0)},
            {"id": "exact", "title": "Foods Dinner", "start": dt.datetime(2026, 7, 12, 18, 0)},
        ]}
        diff = shadow.diff_against_live([_calendar_row()], live)
        entry = diff.entries[0]
        assert entry.classification == shadow.NOOP
        assert entry.detail["event_id"] == "exact"
        assert "time_mismatch" not in entry.detail

    def test_daily_note_missing_is_conflict(self):
        diff = shadow.diff_against_live([_patch_row()], {"daily_note_text": None})
        assert diff.entries[0].classification == shadow.CONFLICT

    def test_daily_note_without_section_is_would_create(self):
        diff = shadow.diff_against_live([_patch_row()], {"daily_note_text": "# Captures\n- thing"})
        assert diff.entries[0].classification == shadow.CREATE

    def test_daily_note_with_section_is_would_update(self):
        diff = shadow.diff_against_live([_patch_row()], {"daily_note_text": "# TDTB Plan\nold content"})
        assert diff.entries[0].classification == shadow.UPDATE

    def test_anchored_event_row_diffs_like_calendar(self):
        diff = shadow.diff_against_live([_anchored_event_row()], {"calendar_events": []})
        assert diff.entries[0].classification == shadow.CREATE

    def test_counts_tally_correctly(self):
        live = {
            "todoist_tasks": [{"id": "1", "content": "Garage Buildout",
                                "due": {"datetime": "2026-07-12T09:00:00Z"}}],
            "vault_frontmatter": {"P/Garage.md": {"assigned": False}},
            "calendar_events": [],
        }
        diff = shadow.diff_against_live([_todoist_row(), _flag_row(), _anchored_event_row()], live)
        counts = diff.counts()
        assert counts[shadow.NOOP] == 1
        assert counts[shadow.UPDATE] == 1
        assert counts[shadow.CREATE] == 1


class TestDiffDegradedSurfaces:
    def test_todoist_unavailable_marks_rows_unavailable_and_lists_surface(self):
        diff = shadow.diff_against_live([_todoist_row()], {"todoist_unavailable": True})
        assert diff.entries[0].classification == shadow.UNAVAILABLE
        assert diff.unavailable_surfaces == ["todoist"]

    def test_calendar_unavailable_does_not_block_todoist_rows(self):
        live = {
            "calendar_unavailable": True,
            "todoist_tasks": [],
        }
        diff = shadow.diff_against_live([_todoist_row(), _calendar_row()], live)
        todoist_entry, calendar_entry = diff.entries
        assert todoist_entry.classification == shadow.CREATE
        assert calendar_entry.classification == shadow.UNAVAILABLE
        assert diff.unavailable_surfaces == ["calendar"]

    def test_vault_unavailable_marks_flag_and_patch_rows(self):
        diff = shadow.diff_against_live([_flag_row(), _patch_row()], {"vault_unavailable": True})
        assert all(e.classification == shadow.UNAVAILABLE for e in diff.entries)
        assert diff.unavailable_surfaces == ["vault"]

    def test_multiple_unavailable_surfaces_all_listed(self):
        diff = shadow.diff_against_live([], {"todoist_unavailable": True, "calendar_unavailable": True})
        assert diff.unavailable_surfaces == ["todoist", "calendar"]


# ---------------------------------------------------------------------------
# gather_live_state — I/O layer, mocked/degraded
# ---------------------------------------------------------------------------

class TestGatherLiveState:
    def test_missing_token_file_raises_shadow_state_error(self, tmp_path, monkeypatch):
        monkeypatch.setattr(shadow, "TOKEN_ENV_PATH", tmp_path / "does-not-exist" / "env")
        vault = tmp_path / "vault"
        vault.mkdir()
        with pytest.raises(shadow.ShadowStateError, match="Todoist token file not found"):
            shadow.gather_live_state({}, vault)

    def test_todoist_failure_degrades_to_marker_not_crash(self, tmp_path, monkeypatch):
        token_path = tmp_path / "env"
        token_path.write_text("TODOIST_TOKEN=abc123\n", encoding="utf-8")
        os.chmod(token_path, stat.S_IRUSR | stat.S_IWUSR)
        monkeypatch.setattr(shadow, "TOKEN_ENV_PATH", token_path)

        class BoomClient:
            def __init__(self, token):
                raise RuntimeError("network unreachable")

        monkeypatch.setattr(shadow.todoist_client, "TodoistClient", BoomClient)

        # Force calendar + vault paths to also degrade cleanly so this test
        # isolates the Todoist failure (EventKit isn't importable in CI).
        monkeypatch.setattr(
            shadow.calendar_bridge, "EventStore",
            lambda: (_ for _ in ()).throw(RuntimeError("no EventKit")),
        )

        vault = tmp_path / "vault"
        vault.mkdir()
        state = shadow.gather_live_state({}, vault)
        assert state["todoist_unavailable"] is True
        assert "todoist_tasks" in state and state["todoist_tasks"] == []
        assert state["calendar_unavailable"] is True

    def test_calendar_consent_error_degrades_to_marker(self, tmp_path, monkeypatch):
        token_path = tmp_path / "env"
        token_path.write_text("TODOIST_TOKEN=abc123\n", encoding="utf-8")
        os.chmod(token_path, stat.S_IRUSR | stat.S_IWUSR)
        monkeypatch.setattr(shadow, "TOKEN_ENV_PATH", token_path)

        class FakeClient:
            def __init__(self, token):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                pass

            def get_filter_tasks(self, *a, **k):
                return [{"id": "1", "content": "x"}]

        monkeypatch.setattr(shadow.todoist_client, "TodoistClient", FakeClient)

        class DeniedStore:
            def auth_status(self):
                return "denied"

        monkeypatch.setattr(shadow.calendar_bridge, "EventStore", DeniedStore)

        vault = tmp_path / "vault"
        vault.mkdir()
        state = shadow.gather_live_state({}, vault)
        assert state["calendar_unavailable"] is True
        assert state["todoist_tasks"] == [{"id": "1", "content": "x"}]

    def test_todoist_snapshot_queries_today_and_overdue(self, tmp_path, monkeypatch):
        """G30: 'today' alone excludes OVERDUE tasks (due yesterday or
        earlier, still open) from the live snapshot diff_against_live
        matches against — including id matching — so a still-open overdue
        task can never be found and misclassifies as would-create. Pin the
        exact filter string gather_live_state queries."""
        token_path = tmp_path / "env"
        token_path.write_text("TODOIST_TOKEN=abc123\n", encoding="utf-8")
        os.chmod(token_path, stat.S_IRUSR | stat.S_IWUSR)
        monkeypatch.setattr(shadow, "TOKEN_ENV_PATH", token_path)

        calls = []

        class RecordingClient:
            def __init__(self, token):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                pass

            def get_filter_tasks(self, filter_id_or_query, *a, **k):
                calls.append(filter_id_or_query)
                return []

        monkeypatch.setattr(shadow.todoist_client, "TodoistClient", RecordingClient)
        monkeypatch.setattr(
            shadow.calendar_bridge, "EventStore",
            lambda: (_ for _ in ()).throw(RuntimeError("skip")),
        )

        vault = tmp_path / "vault"
        vault.mkdir()
        shadow.gather_live_state({}, vault)
        assert calls == ["today | overdue"]

    def test_vault_frontmatter_gathered_from_walk(self, tmp_path, monkeypatch):
        token_path = tmp_path / "env"
        token_path.write_text("TODOIST_TOKEN=abc123\n", encoding="utf-8")
        os.chmod(token_path, stat.S_IRUSR | stat.S_IWUSR)
        monkeypatch.setattr(shadow, "TOKEN_ENV_PATH", token_path)
        monkeypatch.setattr(
            shadow.todoist_client, "TodoistClient",
            lambda token: (_ for _ in ()).throw(RuntimeError("skip")),
        )
        monkeypatch.setattr(
            shadow.calendar_bridge, "EventStore",
            lambda: (_ for _ in ()).throw(RuntimeError("skip")),
        )

        vault = tmp_path / "vault"
        proj_dir = vault / "50 - Operations" / "Projects"
        proj_dir.mkdir(parents=True)
        (proj_dir / "Garage Buildout.md").write_text(
            "---\ntype: project\nassigned: true\n---\nbody\n", encoding="utf-8"
        )

        state = shadow.gather_live_state({}, vault)
        rel_path = "50 - Operations/Projects/Garage Buildout.md"
        assert rel_path in state["vault_frontmatter"]
        assert state["vault_frontmatter"][rel_path]["assigned"] is True

    def test_no_writes_performed(self, tmp_path, monkeypatch):
        """Shadow's I/O layer only reads — this asserts the vault tree is
        byte-for-byte untouched (mtimes + contents) after a gather call."""
        token_path = tmp_path / "env"
        token_path.write_text("TODOIST_TOKEN=abc123\n", encoding="utf-8")
        os.chmod(token_path, stat.S_IRUSR | stat.S_IWUSR)
        monkeypatch.setattr(shadow, "TOKEN_ENV_PATH", token_path)
        monkeypatch.setattr(
            shadow.todoist_client, "TodoistClient",
            lambda token: (_ for _ in ()).throw(RuntimeError("skip")),
        )
        monkeypatch.setattr(
            shadow.calendar_bridge, "EventStore",
            lambda: (_ for _ in ()).throw(RuntimeError("skip")),
        )

        vault = tmp_path / "vault"
        proj_dir = vault / "50 - Operations" / "Projects"
        proj_dir.mkdir(parents=True)
        note = proj_dir / "Garage Buildout.md"
        note.write_text("---\ntype: project\nassigned: true\n---\nbody\n", encoding="utf-8")
        before = {p: (p.read_bytes(), p.stat().st_mtime) for p in vault.rglob("*") if p.is_file()}

        shadow.gather_live_state({}, vault)

        after = {p: (p.read_bytes(), p.stat().st_mtime) for p in vault.rglob("*") if p.is_file()}
        assert before == after


class TestRecurrenceDetail:
    """Recurring tasks are PINNED (shakedown 2026-07-14, defect: M1.0
    would-update): the plan schedules around them, never retimes them.
    A time-diffed recurring match downgrades to no-op with a
    pinned_recurring detail; non-recurring keeps the update path."""

    def test_recurring_time_diff_is_pinned_no_op(self):
        live = {"todoist_tasks": [{"id": "1", "content": "Garage Buildout",
                                    "due": {"date": "2026-07-12T11:00:00",
                                            "is_recurring": True,
                                            "string": "every day at 11am"}}]}
        diff = shadow.diff_against_live([_todoist_row()], live)
        entry = diff.entries[0]
        assert entry.classification == shadow.NOOP
        assert entry.detail["pinned_recurring"] is True
        assert entry.detail["due_time"] == {"live": "11:00", "planned": "09:00"}
        assert entry.detail["task_id"] == "1"

    def test_recurring_matching_time_is_plain_no_op(self):
        live = {"todoist_tasks": [{"id": "1", "content": "Garage Buildout",
                                    "due": {"date": "2026-07-12T09:00:00",
                                            "is_recurring": True,
                                            "string": "every day at 9am"}}]}
        diff = shadow.diff_against_live([_todoist_row()], live)
        assert diff.entries[0].classification == shadow.NOOP

    def test_update_detail_is_recurring_false_when_absent(self):
        live = {"todoist_tasks": [{"id": "1", "content": "Garage Buildout",
                                    "due": {"date": "2026-07-12T11:00:00"}}]}
        diff = shadow.diff_against_live([_todoist_row()], live)
        entry = diff.entries[0]
        assert entry.classification == shadow.UPDATE
        assert entry.detail["is_recurring"] is False


class TestDaySetupOverrides:
    """T4 (ui-parity): Day Setup blob drives _anchored_block_off + re_included."""

    CFG = {"anchored_blocks": [
        {"id": "Sudsing", "Type": "hard", "Start": "5:45 PM", "Duration": "30m"},
        {"id": "Foods Dinner", "Type": "window", "Start": "6:00 PM",
         "End": "8:30 PM", "Duration": "60m"},
    ]}

    def test_override_merges_toggle_and_time(self):
        ds = {"anchored": [{"id": "Sudsing", "skip_today": True},
                           {"id": "Foods Dinner", "time": "19:00"}]}
        cfg = shadow.apply_day_setup(self.CFG, ds)
        specs = shadow._anchored_specs(cfg)
        assert shadow._anchored_block_off(specs["Sudsing"]) is True
        assert specs["Foods Dinner"]["time"] == "19:00"

    def test_re_included_forces_block_on(self):
        ds = {"anchored": [{"id": "Sudsing", "skip_today": True}],
              "re_included": ["Sudsing"]}
        cfg = shadow.apply_day_setup(self.CFG, ds)
        specs = shadow._anchored_specs(cfg)
        assert shadow._anchored_block_off(specs["Sudsing"]) is False
        assert specs["Sudsing"]["re_included"] is True

    def test_blocks_override_rewrites_duration_minutes(self):
        ds = {"anchored": [{"id": "Sudsing", "blocks": 3}]}
        cfg = shadow.apply_day_setup(self.CFG, ds)
        specs = shadow._anchored_specs(cfg)
        assert specs["Sudsing"]["Duration"] == 90
        # untouched block keeps its config duration
        assert specs["Foods Dinner"]["Duration"] == "60m"

    def test_blocks_override_bad_value_ignored(self):
        ds = {"anchored": [{"id": "Sudsing", "blocks": "junk"}]}
        cfg = shadow.apply_day_setup(self.CFG, ds)
        assert shadow._anchored_specs(cfg)["Sudsing"]["Duration"] == "30m"

    def test_empty_day_setup_is_identity(self):
        assert shadow.apply_day_setup(self.CFG, None) == self.CFG

    def test_manifest_tags_re_included_step_e(self):
        ds = {"re_included": ["Sudsing"]}
        cfg = shadow.apply_day_setup(self.CFG, ds)
        manifest = shadow.build_plan_manifest(
            {"assigned": []},
            {"sequence": [{"id": "Sudsing", "start": "18:00", "end": "18:30"}]},
            cfg)
        # T22: other ON anchored specs also emit Step E rows now — assert the
        # re-included tag on Sudsing's row specifically, not Step E exclusivity.
        [e] = [m for m in manifest if m.step == "E" and m.id_or_path == "Sudsing"]
        assert e.name == "Sudsing (re-included)"


class TestPastWindowDefaults:
    CFG = {
        "anchored_blocks": [
            {"id": "Morning Routine", "Type": "hard", "Start": "7:45 AM"},
            {"id": "Sudsing", "Type": "hard", "Start": "5:45 PM"},
            {"id": "Foods Dinner", "Type": "window", "Start": "6:00 PM", "End": "8:30 PM"},
        ],
        "Template Blocks": {"Trinoor Hours": [
            {"Slot": "Morning", "Start": "8:30 AM", "End": "12:30 PM"},
            {"Slot": "Afternoon", "Start": "1:30 PM", "End": "5:00 PM"},
        ]},
    }
    MONDAY = __import__("datetime").date(2026, 7, 13)
    SATURDAY = __import__("datetime").date(2026, 7, 11)

    def test_hard_block_past_start_defaults_off(self):
        off = shadow.past_window_defaults(self.CFG, "14:30", self.MONDAY)
        assert "Morning Routine" in off and "Sudsing" not in off

    def test_window_block_off_only_past_window_end(self):
        assert "Foods Dinner" not in shadow.past_window_defaults(self.CFG, "19:00", self.MONDAY)
        assert "Foods Dinner" in shadow.past_window_defaults(self.CFG, "20:31", self.MONDAY)

    def test_minting_defaults_off_on_weekend(self):
        assert "Minting" in shadow.past_window_defaults(self.CFG, "09:00", self.SATURDAY)

    def test_minting_defaults_off_past_work_end(self):
        assert "Minting" in shadow.past_window_defaults(self.CFG, "17:00", self.MONDAY)

    def test_minting_on_within_work_hours(self):
        assert "Minting" not in shadow.past_window_defaults(self.CFG, "09:00", self.MONDAY)


class TestScheduableClassification:
    """T5 (ui-parity): QT routes through Todoist (skill 1518); emoji-prefixed
    Trinoor zone rows are Step D′."""

    def test_quick_tasks_row_is_step_a_todoist(self):
        manifest = shadow.build_plan_manifest(
            {"assigned": []},
            {"sequence": [{"id": "Quick Tasks", "start": "18:00", "end": "18:30"}]},
            {})
        [row] = [m for m in manifest if m.name == "Quick Tasks"]
        assert row.step == "A" and row.system == "todoist"

    def test_emoji_prefixed_trinoor_zone_is_d_prime(self):
        manifest = shadow.build_plan_manifest(
            {"assigned": []},
            {"sequence": [{"id": "🟡 Trinoor : Morning", "start": "08:30",
                            "end": "12:30", "backdrop": True}]},
            {})
        [row] = [m for m in manifest if "Trinoor" in m.name]
        assert row.step == "D′" and row.system == "calendar"

    def test_trinoor_substring_anchored_block_not_highjacked_to_d_prime(self):
        # FEEDBACK-26: a block whose NAME merely contains "trinoor" (e.g. a
        # configured anchored block "Trinoor sync") is NOT a Trinoor work
        # zone. The broad substring rule classified it Step D′ and silently
        # dropped its Step E write intent.
        config = {"anchored_blocks": [{"id": "Trinoor sync", "on": True}]}
        manifest = shadow.build_plan_manifest(
            {"assigned": []},
            {"sequence": [{"id": "Trinoor sync", "start": "09:00",
                           "end": "10:00", "zone": "any"}]},
            config)
        assert [m.step for m in manifest if m.step == "D′"] == []
        assert [m.name for m in manifest if m.step == "E"] == ["Trinoor sync"]

    def test_trinoor_like_name_is_not_a_zone(self):
        # "Trinoorish" contains "trinoor" but is not the canonical zone shape
        # "[🟡 ]Trinoor : <slot>" — it is a plain schedulable block (Step D).
        manifest = shadow.build_plan_manifest(
            {"assigned": []},
            {"sequence": [{"id": "Trinoorish", "start": "09:00",
                           "end": "10:00", "zone": "any"}]},
            {})
        assert [m.step for m in manifest if m.step == "D′"] == []
        assert [m.name for m in manifest if m.step == "D"] == ["Trinoorish"]

    def test_minting_row_is_step_d(self):
        manifest = shadow.build_plan_manifest(
            {"assigned": []},
            {"sequence": [{"id": "Minting", "start": "09:00", "end": "10:00"}]},
            {})
        [row] = [m for m in manifest if m.name == "Minting"]
        assert row.step == "D" and row.system == "calendar"


class TestCapturesManifest:
    """T8 (ui-parity): Phase-1 captures — niceties to Todoist Inbox (Step A,
    bare text, all-day) + daily-note frontmatter patch (B6)."""

    CFG = {"captures": {"intention": "ship it", "megan_nicety": "Walk outside",
                         "stoic_intention": "Temperance"}}

    def _manifest(self, cfg=None):
        return shadow.build_plan_manifest({"assigned": []}, {"sequence": []},
                                          cfg if cfg is not None else self.CFG)

    def test_niceties_emit_bare_todoist_creates(self):
        rows = [m for m in self._manifest() if m.action == "capture-nicety"]
        names = sorted(r.name for r in rows)
        assert names == ["Temperance", "Walk outside"]     # verbatim, no prefix
        for r in rows:
            assert r.system == "todoist" and r.step == "A"
            assert r.time is None                          # all-day, intentional
            assert r.routing == "Inbox"

    def test_intention_never_becomes_a_todoist_task(self):
        rows = [m for m in self._manifest() if m.action == "capture-nicety"]
        assert all(r.name != "ship it" for r in rows)

    def test_b6_row_emitted_when_any_capture_present(self):
        rows = [m for m in self._manifest() if m.action == "frontmatter-captures"]
        assert len(rows) == 1
        assert rows[0].step == "B6" and rows[0].system == "vault"

    def test_no_captures_no_rows(self):
        m = self._manifest(cfg={})
        assert all(r.action not in ("capture-nicety", "frontmatter-captures")
                   for r in m)

    def test_empty_fields_skip_silently(self):
        m = self._manifest(cfg={"captures": {"megan_nicety": "", "intention": "x"}})
        assert not [r for r in m if r.action == "capture-nicety"]
        assert len([r for r in m if r.action == "frontmatter-captures"]) == 1


class TestCapturesDiff:
    def _b6(self):
        return shadow.ManifestEntry(step="B6", system="vault",
                                    action="frontmatter-captures",
                                    name="Phase-1 captures",
                                    id_or_path="<today's daily note>")

    def test_missing_daily_note_is_conflict(self):
        diff = shadow.diff_against_live([self._b6()], {"daily_note_text": None})
        assert diff.entries[0].classification == shadow.CONFLICT

    def test_missing_keys_is_update(self):
        live = {"daily_note_text": "---\ntype: daily\n---\nbody"}
        diff = shadow.diff_against_live([self._b6()], live)
        assert diff.entries[0].classification == shadow.UPDATE

    def test_all_keys_present_is_noop(self):
        live = {"daily_note_text": "---\nintention: a\nmegan_nicety: b\n"
                                    "stoic_intention: c\n---\nbody"}
        diff = shadow.diff_against_live([self._b6()], live)
        assert diff.entries[0].classification == shadow.NOOP


class TestDuplicateNameClaims:
    """T21 (2026-07-24 qualification FAIL): a vault-sourced row whose name
    content-matches a live task already claimed by an id-ref'd todoist row
    must NOT collapse onto that task — it classifies as would-create."""

    def _press_live(self, hhmm="11:00"):
        return {"todoist_tasks": [{
            "id": "t1", "content": "Press",
            "due": {"date": f"2026-07-12T{hhmm}:00"},
        }]}

    def test_vault_row_never_matches_task_claimed_by_id_ref(self):
        rows = [
            _todoist_row(name="Press (Todoist)", time="12:00",
                         path="todoist://t1"),
            _todoist_row(name="Press", time="13:00", path="P/Press.md"),
        ]
        diff = shadow.diff_against_live(rows, self._press_live())
        by_name = {e.manifest.name: e for e in diff.entries}
        assert by_name["Press (Todoist)"].classification == shadow.UPDATE
        assert by_name["Press (Todoist)"].detail["task_id"] == "t1"
        assert by_name["Press"].classification == shadow.CREATE

    def test_id_ref_claim_wins_regardless_of_manifest_order(self):
        rows = [
            _todoist_row(name="Press", time="13:00", path="P/Press.md"),
            _todoist_row(name="Press (Todoist)", time="12:00",
                         path="todoist://t1"),
        ]
        diff = shadow.diff_against_live(rows, self._press_live())
        by_name = {e.manifest.name: e for e in diff.entries}
        assert by_name["Press"].classification == shadow.CREATE
        assert by_name["Press (Todoist)"].classification == shadow.UPDATE

    def test_two_content_rows_one_task_first_claims_second_creates(self):
        rows = [
            _todoist_row(name="Press", time="12:00", path="P/PressA.md"),
            _todoist_row(name="Press", time="13:00", path="P/PressB.md"),
        ]
        diff = shadow.diff_against_live(rows, self._press_live("12:00"))
        assert diff.entries[0].classification == shadow.NOOP
        assert diff.entries[1].classification == shadow.CREATE

    def test_unclaimed_content_match_still_updates(self):
        rows = [_todoist_row(name="Press", time="13:00", path="P/Press.md")]
        diff = shadow.diff_against_live(rows, self._press_live())
        assert diff.entries[0].classification == shadow.UPDATE
        assert diff.entries[0].detail["task_id"] == "t1"


class TestAnchoredStepEParity:
    """T22 (2026-07-24 qualification FAIL): the cockpit's staged sequence
    carries movable work + zone rows only, so anchored blocks never reached
    the manifest and Sudsing landed on no calendar. Anchored specs now emit
    Step E rows even when absent from the sequence payload."""

    def _config(self, **extra):
        blocks = [
            {"Block": "Sudsing", "Type": "hard", "Start": "5:45 PM",
             "Duration": "30m", "Days": "daily"},
        ]
        blocks.extend(extra.pop("blocks", []))
        return {"anchored_blocks": blocks, **extra}

    def _seq(self, *ids):
        return {"sequence": [
            {"id": i, "start": "09:00", "end": "10:00"} for i in ids
        ]}

    def test_anchored_spec_absent_from_sequence_emits_step_e(self):
        entries = shadow.build_plan_manifest({}, self._seq(), self._config())
        e = [x for x in entries if x.step == "E"]
        assert len(e) == 1
        assert e[0].name == "Sudsing"
        assert e[0].system == "calendar"
        assert e[0].time == "17:45"
        assert e[0].duration_min == 30
        assert e[0].routing == "⬜ Blocks"

    def test_day_setup_time_override_positions_step_e(self):
        cfg = shadow.apply_day_setup(
            self._config(),
            {"anchored": [{"id": "Sudsing", "time": "18:30", "blocks": 2}]},
        )
        entries = shadow.build_plan_manifest({}, self._seq(), cfg)
        e = [x for x in entries if x.step == "E"]
        assert len(e) == 1
        assert e[0].time == "18:30"
        assert e[0].duration_min == 60

    def test_sequence_matched_anchored_row_not_duplicated(self):
        entries = shadow.build_plan_manifest({}, self._seq("Sudsing"), self._config())
        e = [x for x in entries if x.step == "E"]
        assert len(e) == 1
        assert e[0].time == "09:00"  # sequence placement wins

    def test_off_and_skipped_specs_emit_nothing(self):
        cfg = self._config(blocks=[
            {"Block": "Night Routine", "Type": "hard", "Start": "11:00 PM",
             "Duration": "45m", "skip_today": True},
            {"Block": "Morning Routine", "Type": "hard", "Start": "7:45 AM",
             "Duration": "80m", "on": False},
        ])
        entries = shadow.build_plan_manifest({}, self._seq(), cfg)
        names = [x.name for x in entries if x.step == "E"]
        assert names == ["Sudsing"]

    def test_calendar_sourced_spec_never_writes(self):
        cfg = self._config(blocks=[
            {"Block": "Working Session", "Start": "10:30", "End": "11:30",
             "source": "calendar"},
        ])
        entries = shadow.build_plan_manifest({}, self._seq(), cfg)
        names = [x.name for x in entries if x.step == "E"]
        assert names == ["Sudsing"]

    def test_live_absent_from_sequence_reroutes_to_todoist(self):
        cfg = self._config(
            blocks=[{"Block": "Live", "Type": "window", "Start": "12:00 PM",
                     "Duration": "30m"}],
            micro_adventure={"id": "ma07", "idea": "Watch sunset"},
        )
        entries = shadow.build_plan_manifest({}, self._seq(), cfg)
        live = [x for x in entries if x.name == "🌱 Watch sunset"]
        assert len(live) == 1
        assert live[0].system == "todoist"
        assert live[0].step == "A"
        assert live[0].duration_min == 30
        assert [x.name for x in entries if x.step == "E"] == ["Sudsing"]

    def test_zero_duration_spec_emits_no_event(self):
        cfg = shadow.apply_day_setup(
            self._config(),
            {"anchored": [{"id": "Sudsing", "blocks": 0}]},
        )
        entries = shadow.build_plan_manifest({}, self._seq(), cfg)
        assert [x.name for x in entries if x.step == "E"] == []


class TestApplyCalendarParticipation:
    """T28: skip_today (and ONLY skip_today) crosses onto calendar busy rows."""

    BUSY = [{"Block": "Farmers Market", "Start": "09:00", "End": "11:00",
             "Duration": 120, "source": "calendar"}]

    def test_skip_merges_by_block_name(self):
        out = shadow.apply_calendar_participation(
            self.BUSY,
            {"anchored": [{"id": "Farmers Market", "skip_today": True}]})
        assert out[0]["skip_today"] is True
        assert out[0]["Start"] == "09:00" and out[0]["Duration"] == 120

    def test_time_and_duration_overrides_never_cross(self):
        out = shadow.apply_calendar_participation(
            self.BUSY,
            {"anchored": [{"id": "Farmers Market", "skip_today": True,
                           "time": "14:00", "blocks": 1, "on": False}]})
        assert out[0]["Start"] == "09:00"
        assert out[0]["Duration"] == 120
        assert "on" not in out[0]

    def test_non_matching_and_empty_setup_are_noops(self):
        out = shadow.apply_calendar_participation(
            self.BUSY, {"anchored": [{"id": "Other", "skip_today": True}]})
        assert "skip_today" not in out[0]
        out = shadow.apply_calendar_participation(self.BUSY, None)
        assert "skip_today" not in out[0]

    def test_skip_false_does_not_mark(self):
        out = shadow.apply_calendar_participation(
            self.BUSY,
            {"anchored": [{"id": "Farmers Market", "skip_today": False}]})
        assert "skip_today" not in out[0]

    def test_input_not_mutated(self):
        busy = [dict(self.BUSY[0])]
        shadow.apply_calendar_participation(
            busy, {"anchored": [{"id": "Farmers Market", "skip_today": True}]})
        assert "skip_today" not in busy[0]


class TestOutOfFrameAnchoredBlocks:
    """T12 qualification (2026-07-26): the Step E parity loop published a
    create-event for EVERY on, non-calendar anchored spec absent from the
    sequence, at the spec's own configured start — with no frame check. On a
    late run (21:45 anchor) that back-dated five events into hours already
    elapsed, the same blocks Day Setup had already labelled "Outside the day
    frame". Capacity excluded them; the write contract did not.

    ``time_frame`` is optional so every pre-existing caller and test keeps the
    old behavior; when supplied, a spec starting before the frame anchor
    publishes nothing at all (calendar event AND Live->Todoist reroute), which
    is what the UI already tells the user is happening.
    """

    FRAME = {"anchor": "21:45", "effective_eod": "23:45"}

    def _config(self, blocks):
        return {"anchored_blocks": blocks}

    @staticmethod
    def _written(manifest):
        """Names of the calendar/todoist write rows — the always-present
        ``# TDTB Plan`` vault patch is not what these tests are about."""
        return [m.name for m in manifest if m.system != "vault"]

    def test_anchored_block_before_anchor_emits_nothing(self):
        digest = {"assigned": []}
        sequence = {"sequence": []}
        config = self._config([
            {"id": "Morning Routine", "Start": "07:45", "Duration": "1h30m"},
        ])
        manifest = shadow.build_plan_manifest(
            digest, sequence, config, time_frame=self.FRAME)
        assert self._written(manifest) == []

    def test_anchored_block_inside_frame_still_emits(self):
        digest = {"assigned": []}
        sequence = {"sequence": []}
        config = self._config([
            {"id": "Night Routine", "Start": "23:00", "Duration": "45m"},
        ])
        manifest = shadow.build_plan_manifest(
            digest, sequence, config, time_frame=self.FRAME)
        e_rows = [m for m in manifest if m.step == "E"]
        assert len(e_rows) == 1
        assert e_rows[0].name == "Night Routine"
        assert e_rows[0].action == "create-event"

    def test_mixed_set_keeps_only_in_frame_blocks(self):
        digest = {"assigned": []}
        sequence = {"sequence": []}
        config = self._config([
            {"id": "Morning Routine", "Start": "07:45", "Duration": "1h30m"},
            {"id": "Foods Breakfast", "Start": "08:30", "Duration": "1h"},
            {"id": "Sudsing", "Start": "17:45", "Duration": "30m"},
            {"id": "Foods Dinner", "Start": "18:00", "Duration": "1h"},
            {"id": "Night Routine", "Start": "23:00", "Duration": "45m"},
        ])
        manifest = shadow.build_plan_manifest(
            digest, sequence, config, time_frame=self.FRAME)
        assert self._written(manifest) == ["Night Routine"]

    def test_out_of_frame_live_does_not_reroute_to_todoist(self):
        """The Live reroute is gated by the same rule — an elapsed Live block
        must not publish a back-dated Todoist due either."""
        digest = {"assigned": []}
        sequence = {"sequence": []}
        config = {
            "anchored_blocks": [{"id": "Live", "Start": "12:00", "Duration": "30m"}],
            "micro_adventure": {"idea": "Write a handwritten note"},
        }
        manifest = shadow.build_plan_manifest(
            digest, sequence, config, time_frame=self.FRAME)
        assert self._written(manifest) == []

    def test_absent_time_frame_preserves_legacy_behavior(self):
        """Backward compatibility: no frame supplied -> every block publishes,
        exactly as before this fix."""
        digest = {"assigned": []}
        sequence = {"sequence": []}
        config = self._config([
            {"id": "Morning Routine", "Start": "07:45", "Duration": "1h30m"},
            {"id": "Night Routine", "Start": "23:00", "Duration": "45m"},
        ])
        manifest = shadow.build_plan_manifest(digest, sequence, config)
        assert sorted(self._written(manifest)) == ["Morning Routine", "Night Routine"]

    def test_sequenced_anchored_row_inside_frame_still_emits(self):
        """A model-placed anchored row inside the frame is untouched."""
        digest = {"assigned": []}
        sequence = {"sequence": [
            {"id": "Night Routine", "start": "23:00", "end": "23:45", "zone": "any"},
        ]}
        config = self._config([{"id": "Night Routine", "Start": "23:00", "Duration": "45m"}])
        manifest = shadow.build_plan_manifest(
            digest, sequence, config, time_frame=self.FRAME)
        assert self._written(manifest) == ["Night Routine"]

    def test_sequenced_anchored_row_before_anchor_emits_nothing(self):
        """T12a (2026-07-26): this path — NOT the parity loop above — is what
        an auto-sequenced day actually takes, and it was still unguarded after
        the first fix. judgment.py's prompt requires every anchored_block it
        is handed to appear in the proposal, ``_judged_anchored`` drops only
        off/skip_today blocks (never elapsed ones), and validate_sequence
        demoted a pre-anchor row to a soft ``placement_past`` warning — so an
        elapsed anchored block rides the commit payload as an ordinary
        sequence row and published a back-dated create-event. Proven live
        against a scratch shadow route by ``t12a_frame_filter_proof.py``."""
        digest = {"assigned": []}
        sequence = {"sequence": [
            {"id": "Sudsing", "start": "17:45", "end": "18:15", "zone": "any"},
            {"id": "Night Routine", "start": "23:00", "end": "23:45", "zone": "any"},
        ]}
        config = self._config([
            {"id": "Sudsing", "Start": "17:45", "Duration": "30m"},
            {"id": "Night Routine", "Start": "23:00", "Duration": "45m"},
        ])
        manifest = shadow.build_plan_manifest(
            digest, sequence, config, time_frame=self.FRAME)
        assert self._written(manifest) == ["Night Routine"]

    def test_sequenced_elapsed_block_moved_forward_still_emits(self):
        """The filter reads the PROPOSED start, not the spec's configured one:
        a block whose anchored time already elapsed but which the sequencer
        moved forward into the frame is a legitimate write."""
        digest = {"assigned": []}
        sequence = {"sequence": [
            {"id": "Sudsing", "start": "22:00", "end": "22:30", "zone": "any"},
        ]}
        config = self._config([{"id": "Sudsing", "Start": "17:45", "Duration": "30m"}])
        manifest = shadow.build_plan_manifest(
            digest, sequence, config, time_frame=self.FRAME)
        e_rows = [m for m in manifest if m.step == "E"]
        assert len(e_rows) == 1
        assert e_rows[0].name == "Sudsing"
        assert e_rows[0].time == "22:00"

    def test_sequenced_out_of_frame_live_does_not_reroute_to_todoist(self):
        """Same gate ahead of the Live branch on the in-sequence path — the
        shakedown's back-dated set included Live at 12:00."""
        digest = {"assigned": []}
        sequence = {"sequence": [
            {"id": "Live", "start": "12:00", "end": "12:30", "zone": "any"},
        ]}
        config = {
            "anchored_blocks": [{"id": "Live", "Start": "12:00", "Duration": "30m"}],
            "micro_adventure": {"idea": "Write a handwritten note"},
        }
        manifest = shadow.build_plan_manifest(
            digest, sequence, config, time_frame=self.FRAME)
        assert self._written(manifest) == []
