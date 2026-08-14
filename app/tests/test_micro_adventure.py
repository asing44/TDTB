"""TDD suite for micro_adventure — deterministic daily micro-adventure selection
(tdtb-bridger-vault SKILL.md § 0.7). Pure-logic + a thin I/O round-trip.

Every parse path must degrade, never raise; selection is deterministic.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import micro_adventure as ma  # noqa: E402
from micro_adventure import (  # noqa: E402
    FALLBACK_POOL,
    HistoryEntry,
    PoolIdea,
)

TODAY = date(2026, 7, 15)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _pool_section(rows, *, rotation_window=None):
    section = {"_body": "prose", "Pool": rows}
    if rotation_window is not None:
        section["Rotation"] = {
            "rotation.exclude_window_days": rotation_window,
            "rotation": {"exclude_window_days": rotation_window, "graduate_offer": True},
        }
    return section


def _row(rid="ma01", idea="Do a thing", category="nature", effort="low", active=True):
    return {"ID": rid, "Idea": idea, "Category": category, "Effort": effort, "Active": active}


def _hist(rid, d, *, done=None, idea="x", tid=None):
    return HistoryEntry(d, rid, idea, tid, done)


# --------------------------------------------------------------------------- #
# parse_pool — fallback + degradation
# --------------------------------------------------------------------------- #
class TestParsePoolFallback:
    def test_none_section_falls_back(self):
        assert ma.parse_pool(None) == FALLBACK_POOL

    def test_malformed_section_falls_back(self):
        for bad in ["nope", 42, [], ["Pool"]]:
            assert ma.parse_pool(bad) == FALLBACK_POOL

    def test_missing_pool_key_falls_back(self):
        assert ma.parse_pool({"_body": "x", "Rotation": {}}) == FALLBACK_POOL

    def test_empty_pool_falls_back(self):
        assert ma.parse_pool(_pool_section([])) == FALLBACK_POOL

    def test_unparseable_pool_type_falls_back(self):
        assert ma.parse_pool(_pool_section("not-a-list")) == FALLBACK_POOL

    def test_fallback_pool_shape_is_exact(self):
        assert len(FALLBACK_POOL) == 12
        ids = [p.id for p in FALLBACK_POOL]
        assert "ma09" not in ids and "ma10" not in ids
        assert all(p.active for p in FALLBACK_POOL)
        ma04 = next(p for p in FALLBACK_POOL if p.id == "ma04")
        assert ma04.effort == "med"
        assert all(p.effort == "low" for p in FALLBACK_POOL if p.id != "ma04")

    def test_parse_never_raises_on_junk(self):
        for junk in [object(), {"Pool": [None, 5, "x"]}, {"Pool": [{}]}]:
            assert ma.parse_pool(junk) == FALLBACK_POOL  # all rows skipped -> fallback


class TestParsePoolRows:
    def test_valid_rows_parsed(self):
        pool = ma.parse_pool(_pool_section([_row("z1", "Walk"), _row("z2", "Ride", active=False)]))
        assert [p.id for p in pool] == ["z1", "z2"]
        assert pool[0] == PoolIdea("z1", "Walk", "nature", "low", True)
        assert pool[1].active is False

    def test_row_missing_id_skipped(self):
        pool = ma.parse_pool(_pool_section([{"Idea": "no id"}, _row("ok", "kept")]))
        assert [p.id for p in pool] == ["ok"]

    def test_row_missing_idea_skipped(self):
        pool = ma.parse_pool(_pool_section([{"ID": "x"}, _row("ok", "kept")]))
        assert [p.id for p in pool] == ["ok"]

    def test_all_rows_malformed_falls_back(self):
        assert ma.parse_pool(_pool_section([{"ID": "x"}, {"Idea": "y"}])) == FALLBACK_POOL

    def test_category_and_effort_defaults(self):
        pool = ma.parse_pool(_pool_section([{"ID": "x", "Idea": "y"}]))
        assert pool[0].category == ""
        assert pool[0].effort == "low"

    @pytest.mark.parametrize(
        "value,expected",
        [
            (True, True), (False, False),
            ("yes", True), ("YES", True), ("true", True), ("True", True),
            ("no", False), ("No", False), ("false", False),
        ],
    )
    def test_active_parsing(self, value, expected):
        pool = ma.parse_pool(_pool_section([{"ID": "x", "Idea": "y", "Active": value}]))
        assert pool[0].active is expected

    def test_active_missing_defaults_true(self):
        pool = ma.parse_pool(_pool_section([{"ID": "x", "Idea": "y"}]))
        assert pool[0].active is True


# --------------------------------------------------------------------------- #
# exclude_window_days
# --------------------------------------------------------------------------- #
class TestExcludeWindowDays:
    def test_flat_key(self):
        assert ma.exclude_window_days(_pool_section([], rotation_window=21)) == 21

    def test_nested_key_only(self):
        section = {"Rotation": {"rotation": {"exclude_window_days": 9}}}
        assert ma.exclude_window_days(section) == 9

    def test_absent_defaults(self):
        assert ma.exclude_window_days({"Rotation": {}}) == ma.DEFAULT_EXCLUDE_WINDOW_DAYS
        assert ma.exclude_window_days(None) == ma.DEFAULT_EXCLUDE_WINDOW_DAYS
        assert ma.exclude_window_days({}) == ma.DEFAULT_EXCLUDE_WINDOW_DAYS

    def test_non_positive_and_malformed_default(self):
        for bad in [0, -3, "abc", True, None, 2.5j if False else "x"]:
            section = {"Rotation": {"rotation.exclude_window_days": bad}}
            assert ma.exclude_window_days(section) == ma.DEFAULT_EXCLUDE_WINDOW_DAYS

    def test_numeric_string_coerced(self):
        section = {"Rotation": {"rotation.exclude_window_days": "30"}}
        assert ma.exclude_window_days(section) == 30


# --------------------------------------------------------------------------- #
# parse_history — degradation + sorting
# --------------------------------------------------------------------------- #
class TestParseHistory:
    def test_non_list_returns_empty(self):
        assert ma.parse_history(None) == []
        assert ma.parse_history("nope") == []
        assert ma.parse_history({}) == []

    def test_string_date_parsed(self):
        h = ma.parse_history([{"date": "2026-07-06", "id": "ma12", "idea": "z"}])
        assert h[0].date == date(2026, 7, 6)
        assert h[0].id == "ma12"

    def test_date_object_accepted(self):
        h = ma.parse_history([{"date": date(2026, 7, 6), "id": "ma12"}])
        assert h[0].date == date(2026, 7, 6)

    def test_malformed_entries_skipped(self):
        raw = [
            {"date": "2026-07-06", "id": "ma12"},   # good
            {"id": "no-date"},                       # missing date
            {"date": "not-a-date", "id": "bad"},     # invalid date
            {"date": "2026-07-05"},                  # missing id
            "junk",                                   # not a dict
            {"date": "2026-07-04", "id": "  "},      # blank id
        ]
        h = ma.parse_history(raw)
        assert [e.id for e in h] == ["ma12"]

    def test_todoist_id_and_done_carried(self):
        h = ma.parse_history(
            [{"date": "2026-07-06", "id": "ma12", "todoist_task_id": "abc", "done": True}]
        )
        assert h[0].todoist_task_id == "abc"
        assert h[0].done is True

    def test_done_null_and_false(self):
        h = ma.parse_history(
            [
                {"date": "2026-07-06", "id": "a", "done": None},
                {"date": "2026-07-05", "id": "b", "done": False},
            ]
        )
        assert h[0].done is None
        assert h[1].done is False

    def test_todoist_id_optional(self):
        h = ma.parse_history([{"date": "2026-07-06", "id": "a"}])
        assert h[0].todoist_task_id is None

    def test_sorted_newest_first(self):
        raw = [
            {"date": "2026-07-01", "id": "a"},
            {"date": "2026-07-09", "id": "b"},
            {"date": "2026-07-05", "id": "c"},
        ]
        assert [e.id for e in ma.parse_history(raw)] == ["b", "c", "a"]

    def test_equal_dates_stable_input_order(self):
        raw = [
            {"date": "2026-07-05", "id": "first"},
            {"date": "2026-07-05", "id": "second"},
            {"date": "2026-07-05", "id": "third"},
        ]
        assert [e.id for e in ma.parse_history(raw)] == ["first", "second", "third"]


# --------------------------------------------------------------------------- #
# read_history — I/O degradation
# --------------------------------------------------------------------------- #
class TestReadHistory:
    def test_missing_file_empty(self, tmp_path: Path):
        assert ma.read_history(tmp_path / "nope.md") == []

    def test_unparseable_file_empty(self, tmp_path: Path):
        p = tmp_path / "log.md"
        p.write_text("---\nnot: [valid: yaml: here\n---\n", encoding="utf-8")
        assert ma.read_history(p) == []

    def test_no_history_key_empty(self, tmp_path: Path):
        p = tmp_path / "log.md"
        p.write_text("---\ndescription: x\nschema_version: 1\n---\nbody\n", encoding="utf-8")
        assert ma.read_history(p) == []


# --------------------------------------------------------------------------- #
# select_today — eligibility window, LRU, relaxation, custom
# --------------------------------------------------------------------------- #
class TestSelectEligibility:
    def test_used_within_window_excluded(self):
        pool = [PoolIdea("a", "A", "", "low", True), PoolIdea("b", "B", "", "low", True)]
        history = [_hist("a", date(2026, 7, 14))]  # 1 day ago, inside 14d window
        sel = ma.select_today(pool, history, today=TODAY, window_days=14)
        assert sel.pick.id == "b"
        assert "a" not in [p.id for p in sel.live_pool]

    def test_boundary_exactly_window_days_ago_is_eligible(self):
        # today - window_days == 2026-07-01 ; entry.date > threshold excludes.
        pool = [PoolIdea("a", "A", "", "low", True)]
        history = [_hist("a", date(2026, 7, 1))]  # exactly 14 days ago -> NOT excluded
        sel = ma.select_today(pool, history, today=TODAY, window_days=14)
        assert sel.pick.id == "a"

    def test_boundary_window_days_minus_one_excluded(self):
        pool = [PoolIdea("a", "A", "", "low", True), PoolIdea("b", "B", "", "low", True)]
        history = [_hist("a", date(2026, 7, 2))]  # 13 days ago -> excluded
        sel = ma.select_today(pool, history, today=TODAY, window_days=14)
        assert sel.pick.id == "b"

    def test_all_in_window_relaxes_to_lru_all_active(self):
        pool = [PoolIdea("a", "A", "", "low", True), PoolIdea("b", "B", "", "low", True)]
        history = [_hist("b", date(2026, 7, 14)), _hist("a", date(2026, 7, 10))]
        sel = ma.select_today(pool, history, today=TODAY, window_days=30)
        # Both used within window -> relax; LRU among all active: a (older) first.
        assert sel.pick.id == "a"
        assert [p.id for p in sel.live_pool] == ["a", "b"]

    def test_no_active_ideas_none(self):
        pool = [PoolIdea("a", "A", "", "low", False)]
        sel = ma.select_today(pool, [], today=TODAY, window_days=14)
        assert sel.pick is None
        assert sel.live_pool == ()


class TestSelectLRUOrder:
    def test_never_used_before_used(self):
        pool = [PoolIdea("a", "A", "", "low", True), PoolIdea("b", "B", "", "low", True)]
        history = [_hist("a", date(2026, 6, 1))]  # a used long ago (outside window)
        sel = ma.select_today(pool, history, today=TODAY, window_days=14)
        # b never-used sorts before a (used); both eligible.
        assert [p.id for p in sel.live_pool] == ["b", "a"]
        assert sel.pick.id == "b"

    def test_never_used_tie_break_pool_order(self):
        pool = [
            PoolIdea("a", "A", "", "low", True),
            PoolIdea("b", "B", "", "low", True),
            PoolIdea("c", "C", "", "low", True),
        ]
        sel = ma.select_today(pool, [], today=TODAY, window_days=14)
        assert [p.id for p in sel.live_pool] == ["a", "b", "c"]

    def test_used_sorted_by_last_used_ascending(self):
        pool = [
            PoolIdea("a", "A", "", "low", True),
            PoolIdea("b", "B", "", "low", True),
            PoolIdea("c", "C", "", "low", True),
        ]
        history = [
            _hist("a", date(2026, 6, 10)),
            _hist("b", date(2026, 6, 1)),
            _hist("c", date(2026, 6, 5)),
        ]
        sel = ma.select_today(pool, history, today=TODAY, window_days=14)
        # all outside window -> eligible; ascending last-used: b, c, a
        assert [p.id for p in sel.live_pool] == ["b", "c", "a"]

    def test_used_equal_date_tie_break_pool_order(self):
        pool = [
            PoolIdea("a", "A", "", "low", True),
            PoolIdea("b", "B", "", "low", True),
        ]
        d = date(2026, 6, 1)
        history = [_hist("b", d), _hist("a", d)]  # equal last-used
        sel = ma.select_today(pool, history, today=TODAY, window_days=14)
        assert [p.id for p in sel.live_pool] == ["a", "b"]  # pool order wins tie

    def test_last_used_is_most_recent_of_multiple(self):
        pool = [
            PoolIdea("a", "A", "", "low", True),
            PoolIdea("b", "B", "", "low", True),
        ]
        history = [
            _hist("a", date(2026, 6, 2)),
            _hist("b", date(2026, 6, 5)),
            _hist("a", date(2026, 5, 1)),  # older a use ignored for LRU
        ]
        sel = ma.select_today(pool, history, today=TODAY, window_days=14)
        # a last-used 6-02, b last-used 6-05 -> a first
        assert [p.id for p in sel.live_pool] == ["a", "b"]

    def test_live_pool_capped_at_8(self):
        pool = [PoolIdea(f"x{i}", f"X{i}", "", "low", True) for i in range(12)]
        sel = ma.select_today(pool, [], today=TODAY, window_days=14)
        assert len(sel.live_pool) == 8
        assert sel.live_pool[0] is sel.pick

    def test_pick_is_first_of_live_pool(self):
        pool = [PoolIdea("a", "A", "", "low", True), PoolIdea("b", "B", "", "low", True)]
        sel = ma.select_today(pool, [], today=TODAY, window_days=14)
        assert sel.pick is sel.live_pool[0]


class TestSelectCustom:
    def test_custom_history_row_does_not_affect_pool_eligibility(self):
        pool = [PoolIdea("a", "A", "", "low", True)]
        history = [_hist("custom", date(2026, 7, 14))]  # custom used yesterday
        sel = ma.select_today(pool, history, today=TODAY, window_days=14)
        assert sel.pick.id == "a"  # 'custom' row excludes nothing else

    def test_custom_never_appears_as_pick(self):
        pool = [PoolIdea("custom", "Custom", "", "low", True), PoolIdea("a", "A", "", "low", True)]
        sel = ma.select_today(pool, [], today=TODAY, window_days=14)
        assert sel.pick.id == "a"
        assert "custom" not in [p.id for p in sel.live_pool]


# --------------------------------------------------------------------------- #
# daily_note_live_done
# --------------------------------------------------------------------------- #
class TestDailyNoteLiveDone:
    def test_checked_box_true(self):
        text = "## Day\n\n### Live\n- [x] Went on the adventure\n\n## Next\n"
        assert ma.daily_note_live_done(text) is True

    def test_unchecked_box_none(self):
        text = "### Live\n- [ ] Not yet\n"
        assert ma.daily_note_live_done(text) is None

    def test_missing_section_none(self):
        assert ma.daily_note_live_done("## Day\n- [x] unrelated\n") is None

    def test_missing_text_none(self):
        assert ma.daily_note_live_done(None) is None
        assert ma.daily_note_live_done("") is None

    def test_checkbox_after_section_end_ignored(self):
        text = "### Live\nno box here\n\n### Other\n- [x] not the live one\n"
        assert ma.daily_note_live_done(text) is None


# --------------------------------------------------------------------------- #
# resolve_prior
# --------------------------------------------------------------------------- #
class _Recorder:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def __call__(self, arg):
        self.calls.append(arg)
        return self.result


class TestResolvePrior:
    def _head(self, done=None, d=date(2026, 7, 14), tid="tk1"):
        return _hist("ma01", d, done=done, tid=tid)

    def test_todoist_true_short_circuits(self):
        note = _Recorder(True)  # would also say true, must NOT be called
        history = [self._head()]
        res = ma.resolve_prior(
            history, today=TODAY,
            todoist_completed=_Recorder(True),
            daily_note_live_checked=note,
        )
        assert res.done_update is not None
        assert res.done_update.done is True
        assert res.history[0].done is True
        assert res.pending_confirm is None
        assert note.calls == []  # short-circuited

    def test_todoist_inconclusive_falls_to_checkbox(self):
        note = _Recorder(True)
        history = [self._head()]
        res = ma.resolve_prior(
            history, today=TODAY,
            todoist_completed=_Recorder(None),  # inconclusive
            daily_note_live_checked=note,
        )
        assert res.done_update is not None
        assert res.history[0].done is True
        assert note.calls == [date(2026, 7, 14)]

    def test_todoist_false_falls_to_checkbox(self):
        note = _Recorder(True)
        res = ma.resolve_prior(
            [self._head()], today=TODAY,
            todoist_completed=_Recorder(False),
            daily_note_live_checked=note,
        )
        assert res.done_update is not None
        assert note.calls == [date(2026, 7, 14)]

    def test_checkbox_unchecked_never_done_false(self):
        # both inconclusive -> pending, done stays None (never becomes False)
        res = ma.resolve_prior(
            [self._head()], today=TODAY,
            todoist_completed=_Recorder(None),
            daily_note_live_checked=_Recorder(None),
        )
        assert res.done_update is None
        assert res.pending_confirm is not None
        assert res.pending_confirm.done is None
        assert res.history[0].done is None

    def test_both_inconclusive_pending(self):
        res = ma.resolve_prior(
            [self._head()], today=TODAY,
            todoist_completed=_Recorder(False),
            daily_note_live_checked=_Recorder(False),
        )
        assert res.pending_confirm is res.history[0]
        assert res.done_update is None

    def test_callables_raising_treated_as_inconclusive(self):
        def boom(_):
            raise RuntimeError("todoist down")

        def boom2(_):
            raise RuntimeError("note read failed")

        res = ma.resolve_prior(
            [self._head()], today=TODAY, todoist_completed=boom, daily_note_live_checked=boom2
        )
        assert res.done_update is None
        assert res.pending_confirm is not None

    def test_todoist_raises_but_checkbox_true(self):
        def boom(_):
            raise RuntimeError("down")

        res = ma.resolve_prior(
            [self._head()], today=TODAY,
            todoist_completed=boom,
            daily_note_live_checked=_Recorder(True),
        )
        assert res.done_update is not None
        assert res.history[0].done is True

    def test_no_task_id_skips_to_checkbox(self):
        note = _Recorder(True)
        tod = _Recorder(True)
        res = ma.resolve_prior(
            [self._head(tid=None)], today=TODAY,
            todoist_completed=tod,
            daily_note_live_checked=note,
        )
        assert tod.calls == []  # no task id -> todoist not consulted
        assert res.history[0].done is True  # checkbox resolved it

    def test_done_already_set_passthrough(self):
        history = [self._head(done=True)]
        res = ma.resolve_prior(
            history, today=TODAY, todoist_completed=_Recorder(True), daily_note_live_checked=_Recorder(True)
        )
        assert res.done_update is None
        assert res.pending_confirm is None
        assert res.history == tuple(history)

    def test_today_dated_head_passthrough(self):
        history = [self._head(d=TODAY)]  # date == today -> not applicable
        res = ma.resolve_prior(
            history, today=TODAY, todoist_completed=_Recorder(True)
        )
        assert res.done_update is None
        assert res.pending_confirm is None

    def test_empty_history_passthrough(self):
        res = ma.resolve_prior([], today=TODAY)
        assert res.history == ()
        assert res.done_update is None and res.pending_confirm is None

    def test_only_examines_head(self):
        # older entry is done:None and dated in the past, but only head matters
        history = [self._head(done=True, d=TODAY), self._head(done=None, d=date(2026, 7, 10))]
        res = ma.resolve_prior(history, today=TODAY, todoist_completed=_Recorder(True))
        assert res.done_update is None  # head already done -> passthrough


# --------------------------------------------------------------------------- #
# streak
# --------------------------------------------------------------------------- #
class TestStreak:
    def test_leading_true_run(self):
        history = [
            _hist("a", date(2026, 7, 14), done=True),
            _hist("b", date(2026, 7, 13), done=True),
            _hist("c", date(2026, 7, 12), done=True),
            _hist("d", date(2026, 7, 11), done=False),
        ]
        sel = ma.select_today([PoolIdea("z", "Z", "", "low", True)], history, today=TODAY, window_days=14)
        assert sel.streak == 3

    def test_broken_by_false(self):
        history = [_hist("a", date(2026, 7, 14), done=True), _hist("b", date(2026, 7, 13), done=False)]
        sel = ma.select_today([PoolIdea("z", "Z", "", "low", True)], history, today=TODAY, window_days=14)
        assert sel.streak == 1

    def test_broken_by_null(self):
        history = [_hist("a", date(2026, 7, 14), done=None)]
        sel = ma.select_today([PoolIdea("z", "Z", "", "low", True)], history, today=TODAY, window_days=14)
        assert sel.streak == 0

    def test_empty_history_zero(self):
        sel = ma.select_today([PoolIdea("z", "Z", "", "low", True)], [], today=TODAY, window_days=14)
        assert sel.streak == 0

    def test_virtual_done_from_resolve_prior_counts(self):
        # head done:None, todoist says complete -> resolve makes it virtual-true;
        # composed streak then counts it plus the trailing true run.
        history = [
            _hist("a", date(2026, 7, 14), done=None, tid="tk1"),
            _hist("b", date(2026, 7, 13), done=True),
        ]
        res = ma.resolve_prior(history, today=TODAY, todoist_completed=_Recorder(True))
        sel = ma.select_today(
            [PoolIdea("z", "Z", "", "low", True)], res.history, today=TODAY, window_days=14
        )
        assert sel.streak == 2


# --------------------------------------------------------------------------- #
# commit-path helpers
# --------------------------------------------------------------------------- #
class TestCommitHelpers:
    def test_build_history_entry(self):
        e = ma.build_history_entry("ma01", "Walk", today=TODAY, todoist_task_id="tk9")
        assert e == HistoryEntry(TODAY, "ma01", "Walk", "tk9", None)

    def test_build_history_entry_no_task_id(self):
        e = ma.build_history_entry("ma01", "Walk", today=TODAY, todoist_task_id=None)
        assert e.todoist_task_id is None and e.done is None

    def test_upsert_prepends_new_date(self):
        history = (_hist("b", date(2026, 7, 14)),)
        entry = ma.build_history_entry("a", "A", today=TODAY, todoist_task_id=None)
        out = ma.upsert_today_entry(history, entry)
        assert [e.id for e in out] == ["a", "b"]

    def test_upsert_replaces_same_date_head_idempotent(self):
        entry1 = ma.build_history_entry("a", "A", today=TODAY, todoist_task_id=None)
        history = ma.upsert_today_entry((), entry1)
        # re-commit same date, changed pick
        entry2 = ma.build_history_entry("b", "B", today=TODAY, todoist_task_id="tk")
        out = ma.upsert_today_entry(history, entry2)
        assert len(out) == 1
        assert out[0].id == "b"

    def test_upsert_double_commit_same_pick_one_entry(self):
        entry = ma.build_history_entry("a", "A", today=TODAY, todoist_task_id=None)
        once = ma.upsert_today_entry((), entry)
        twice = ma.upsert_today_entry(once, entry)
        assert len(twice) == 1

    def test_apply_done_update_none_unchanged(self):
        history = (_hist("a", TODAY),)
        assert ma.apply_done_update(history, None) == history

    def test_apply_done_update_replaces_match(self):
        history = (_hist("a", date(2026, 7, 14), done=None), _hist("b", date(2026, 7, 13)))
        done = _hist("a", date(2026, 7, 14), done=True)
        out = ma.apply_done_update(history, done)
        assert out[0].done is True
        assert out[1].done is None or out[1].done is None  # untouched


# --------------------------------------------------------------------------- #
# write_history / read_history round-trip
# --------------------------------------------------------------------------- #
class TestWriteRoundTrip:
    def test_round_trip_preserves_entries(self, tmp_path: Path):
        p = tmp_path / "log.md"
        history = [
            HistoryEntry(date(2026, 7, 6), "ma12", "Practice a skill", "6h3m73fQ", None),
            HistoryEntry(date(2026, 7, 5), "ma13", "Barefoot walk", None, False),
            HistoryEntry(date(2026, 7, 4), "ma01", "Greenway", "abc", True),
        ]
        ma.write_history(p, history)
        assert ma.read_history(p) == history

    def test_todoist_none_omitted_and_reads_back_none(self, tmp_path: Path):
        p = tmp_path / "log.md"
        ma.write_history(p, [HistoryEntry(date(2026, 7, 6), "ma12", "x", None, None)])
        assert "todoist_task_id" not in p.read_text(encoding="utf-8")
        assert ma.read_history(p)[0].todoist_task_id is None

    def test_write_creates_missing_parent_dirs(self, tmp_path: Path):
        p = tmp_path / "deep" / "nested" / "log.md"
        history = [HistoryEntry(date(2026, 7, 6), "ma12", "x", None, True)]
        ma.write_history(p, history)
        assert p.exists()
        assert ma.read_history(p) == history

    def test_write_preserves_existing_description(self, tmp_path: Path):
        p = tmp_path / "log.md"
        p.write_text(
            "---\ndescription: My custom log desc\nschema_version: 1\nhistory: []\n---\n",
            encoding="utf-8",
        )
        ma.write_history(p, [HistoryEntry(date(2026, 7, 6), "ma12", "x", None, None)])
        assert "My custom log desc" in p.read_text(encoding="utf-8")

    def test_write_default_description_when_absent(self, tmp_path: Path):
        p = tmp_path / "log.md"
        ma.write_history(p, [])
        text = p.read_text(encoding="utf-8")
        assert "description:" in text

    def test_round_trip_empty_history(self, tmp_path: Path):
        p = tmp_path / "log.md"
        ma.write_history(p, [])
        assert ma.read_history(p) == []


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
