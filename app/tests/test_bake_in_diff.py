"""Tests for bake_in_diff.py (T18b) and bake_in_run.py (T18) — the bake-in
verdict lens over shadow.py's diff engine (protocol § 2.1–2.3). Mirrors
test_shadow.py's conventions: direct ManifestEntry/ShadowDiff/ShadowDiffEntry
construction, no fixtures needed for the pure-classification tests. No
disk/network I/O except the log-append tests, which use pytest's tmp_path."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import bake_in_diff  # noqa: E402
import bake_in_run  # noqa: E402
import shadow  # noqa: E402


# ---------------------------------------------------------------------------
# Manifest-row builders (mirrors test_shadow.py's _todoist_row/_flag_row/etc.)
# ---------------------------------------------------------------------------

def _todoist_row(name="Garage Buildout", time="09:00"):
    return shadow.ManifestEntry(
        step="A", system="todoist", action="schedule",
        name=name, id_or_path="P/Garage.md", time=time, duration_min=60, routing="PHEP",
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


def _make_diff(entries: list[shadow.ShadowDiffEntry]) -> shadow.ShadowDiff:
    return shadow.ShadowDiff(entries=entries)


# ---------------------------------------------------------------------------
# classify_bakein — every § 2.1 mapping row
# ---------------------------------------------------------------------------

class TestClassifyBakeinMapping:
    def test_noop_is_agree(self):
        diff = _make_diff([shadow.ShadowDiffEntry(_todoist_row(), shadow.NOOP, {})])
        verdict = bake_in_diff.classify_bakein(diff)
        assert verdict.agree() == 1
        assert verdict.unexplained() == 0
        assert verdict.inconclusive() == 0
        assert verdict.rows[0].verdict == bake_in_diff.AGREE

    def test_step_b_would_update_is_agree_the_carve_out(self):
        """The critical carve-out: Step B (action == 'patch') would-update ->
        AGREE, because diff_against_live always marks it non-no-op once the
        section exists (shadow.py:394). Without this every day would carry
        >=1 diff and the bake-in could never pass."""
        entry = shadow.ShadowDiffEntry(_patch_row(), shadow.UPDATE, {"reason": "section exists, will be replaced"})
        verdict = bake_in_diff.classify_bakein(_make_diff([entry]))
        assert verdict.agree() == 1
        assert verdict.unexplained() == 0
        assert verdict.rows[0].verdict == bake_in_diff.AGREE

    def test_step_b_conflict_is_unexplained_not_carved_out(self):
        """A Step B conflict ('daily note not found') is a real problem —
        the carve-out only applies to would-update, never conflict."""
        entry = shadow.ShadowDiffEntry(_patch_row(), shadow.CONFLICT, {"reason": "daily note not found"})
        verdict = bake_in_diff.classify_bakein(_make_diff([entry]))
        assert verdict.unexplained() == 1
        assert verdict.rows[0].verdict == bake_in_diff.UNEXPLAINED

    def test_would_create_is_unexplained(self):
        entry = shadow.ShadowDiffEntry(_todoist_row(), shadow.CREATE, {"content": "Garage Buildout"})
        verdict = bake_in_diff.classify_bakein(_make_diff([entry]))
        assert verdict.unexplained() == 1
        assert verdict.rows[0].verdict == bake_in_diff.UNEXPLAINED

    def test_todoist_would_update_is_unexplained(self):
        entry = shadow.ShadowDiffEntry(
            _todoist_row(), shadow.UPDATE, {"task_id": "1", "due_time": {"old": "11:00", "new": "09:00"}}
        )
        verdict = bake_in_diff.classify_bakein(_make_diff([entry]))
        assert verdict.unexplained() == 1
        assert verdict.rows[0].verdict == bake_in_diff.UNEXPLAINED

    def test_step_c_would_update_is_unexplained(self):
        entry = shadow.ShadowDiffEntry(_flag_row(), shadow.UPDATE, {"assigned": {"old": False, "new": True}})
        verdict = bake_in_diff.classify_bakein(_make_diff([entry]))
        assert verdict.unexplained() == 1
        assert verdict.rows[0].verdict == bake_in_diff.UNEXPLAINED

    def test_unavailable_is_inconclusive(self):
        entry = shadow.ShadowDiffEntry(_todoist_row(), shadow.UNAVAILABLE, {"reason": "todoist surface unavailable"})
        verdict = bake_in_diff.classify_bakein(_make_diff([entry]))
        assert verdict.inconclusive() == 1
        assert verdict.unexplained() == 0
        assert verdict.rows[0].verdict == bake_in_diff.INCONCLUSIVE


# ---------------------------------------------------------------------------
# The all-agree day — proves the Step-B carve-out actually lets a clean day
# pass (a realistic mixed manifest across steps/surfaces).
# ---------------------------------------------------------------------------

class TestAllAgreeDay:
    def test_mixed_clean_day_is_all_agree_and_passes(self):
        entries = [
            shadow.ShadowDiffEntry(_todoist_row(), shadow.NOOP, {}),          # Step A no-op
            shadow.ShadowDiffEntry(_patch_row(), shadow.UPDATE,               # Step B carve-out
                                    {"reason": "section exists, will be replaced"}),
            shadow.ShadowDiffEntry(_flag_row(), shadow.NOOP, {}),             # Step C no-op
            shadow.ShadowDiffEntry(_calendar_row(), shadow.NOOP, {"event_id": "e1"}),  # calendar no-op
        ]
        verdict = bake_in_diff.classify_bakein(_make_diff(entries))
        assert verdict.agree() == 4
        assert verdict.unexplained() == 0
        assert verdict.inconclusive() == 0
        assert verdict.unexplained_notes() == []

        result = bake_in_diff.day_verdict(verdict, recon_fail=0, wrong_surface=False)
        assert result == bake_in_diff.PASS


# ---------------------------------------------------------------------------
# day_verdict precedence: KILL > DIFF > INCONCLUSIVE > PASS
# ---------------------------------------------------------------------------

class TestDayVerdictPrecedence:
    def _verdict_with(self, *, unexplained=0, inconclusive=0, agree=0):
        rows = []
        rows += [bake_in_diff.BakeInRow(_todoist_row(), shadow.NOOP, bake_in_diff.AGREE, "ok")] * agree
        rows += [bake_in_diff.BakeInRow(_todoist_row(), shadow.CREATE, bake_in_diff.UNEXPLAINED, "diff")] * unexplained
        rows += [bake_in_diff.BakeInRow(_todoist_row(), shadow.UNAVAILABLE, bake_in_diff.INCONCLUSIVE, "inc")] * inconclusive
        return bake_in_diff.BakeInVerdict(rows=rows)

    def test_kill_beats_diff(self):
        v = self._verdict_with(unexplained=1)
        assert bake_in_diff.day_verdict(v, recon_fail=1, wrong_surface=False) == bake_in_diff.KILL

    def test_wrong_surface_alone_kills(self):
        v = self._verdict_with(agree=3)
        assert bake_in_diff.day_verdict(v, recon_fail=0, wrong_surface=True) == bake_in_diff.KILL

    def test_diff_beats_inconclusive(self):
        v = self._verdict_with(unexplained=1, inconclusive=1)
        assert bake_in_diff.day_verdict(v, recon_fail=0, wrong_surface=False) == bake_in_diff.DIFF

    def test_inconclusive_only_when_unexplained_zero(self):
        v = self._verdict_with(inconclusive=1)
        assert bake_in_diff.day_verdict(v, recon_fail=0, wrong_surface=False) == bake_in_diff.INCONCLUSIVE

    def test_pass_when_all_clear(self):
        v = self._verdict_with(agree=5)
        assert bake_in_diff.day_verdict(v, recon_fail=0, wrong_surface=False) == bake_in_diff.PASS


# ---------------------------------------------------------------------------
# unexplained_notes() content shape
# ---------------------------------------------------------------------------

class TestUnexplainedNotes:
    def test_notes_name_step_and_item(self):
        entry = shadow.ShadowDiffEntry(
            shadow.ManifestEntry(step="A", system="todoist", action="schedule",
                                  name="Guitar", id_or_path="P/Guitar.md", time="20:00",
                                  duration_min=30, routing="Inbox"),
            shadow.CREATE, {"content": "Guitar", "due_time": "20:00"},
        )
        verdict = bake_in_diff.classify_bakein(_make_diff([entry]))
        notes = verdict.unexplained_notes()
        assert len(notes) == 1
        assert "Step A" in notes[0]
        assert "Guitar" in notes[0]

    def test_agree_rows_never_appear_in_unexplained_notes(self):
        entries = [
            shadow.ShadowDiffEntry(_todoist_row(), shadow.NOOP, {}),
            shadow.ShadowDiffEntry(_todoist_row(name="Other"), shadow.CREATE, {}),
        ]
        verdict = bake_in_diff.classify_bakein(_make_diff(entries))
        assert len(verdict.unexplained_notes()) == 1
        assert "Other" in verdict.unexplained_notes()[0]


# ---------------------------------------------------------------------------
# bake_in_run.py — log append semantics
# ---------------------------------------------------------------------------

class TestLogAppend:
    def test_creates_header_when_absent(self, tmp_path):
        log_path = tmp_path / "bake-in-log.md"
        row = {
            "date": "2026-07-20", "driver": "skill", "agree": 11, "unexplained": 0,
            "inconclusive": 0, "recon_fail": 0, "verdict": "PASS", "notes": "—",
        }
        bake_in_run.append_log_row(log_path, row)
        lines = log_path.read_text(encoding="utf-8").splitlines()
        assert lines[0] == bake_in_run._LOG_HEADER
        assert lines[1] == bake_in_run._LOG_SEP
        assert len(lines) == 3
        assert "2026-07-20" in lines[2] and "PASS" in lines[2]

    def test_appends_without_rewriting_header_when_present(self, tmp_path):
        log_path = tmp_path / "bake-in-log.md"
        row1 = {
            "date": "2026-07-20", "driver": "skill", "agree": 11, "unexplained": 0,
            "inconclusive": 0, "recon_fail": 0, "verdict": "PASS", "notes": "—",
        }
        row2 = {
            "date": "2026-07-21", "driver": "app", "agree": 10, "unexplained": 1,
            "inconclusive": 0, "recon_fail": 0, "verdict": "DIFF",
            "notes": "Step A 'Guitar': created, not in committed plan",
        }
        bake_in_run.append_log_row(log_path, row1)
        bake_in_run.append_log_row(log_path, row2)
        lines = log_path.read_text(encoding="utf-8").splitlines()
        # header + separator appear exactly once, followed by exactly 2 data rows
        assert lines.count(bake_in_run._LOG_HEADER) == 1
        assert lines.count(bake_in_run._LOG_SEP) == 1
        data_rows = [l for l in lines if l not in (bake_in_run._LOG_HEADER, bake_in_run._LOG_SEP)]
        assert len(data_rows) == 2
        assert "2026-07-20" in data_rows[0]
        assert "2026-07-21" in data_rows[1]

    def test_pipe_in_notes_is_escaped(self, tmp_path):
        log_path = tmp_path / "bake-in-log.md"
        row = {
            "date": "2026-07-22", "driver": "app", "agree": 9, "unexplained": 1,
            "inconclusive": 0, "recon_fail": 0, "verdict": "DIFF",
            "notes": "Step D 'A | B': created, not in committed plan",
        }
        bake_in_run.append_log_row(log_path, row)
        text = log_path.read_text(encoding="utf-8")
        assert "A \\| B" in text

    def test_build_log_row_notes_reach_the_file_escaped(self, tmp_path):
        """build_log_row's notes carry the raw pipe (it's a data model, not a
        rendered line); append_log_row/_format_row is the single place that
        escapes it before writing — this test proves the two compose to a
        valid table row on disk."""
        entry = shadow.ShadowDiffEntry(
            shadow.ManifestEntry(step="D", system="calendar", action="create-event",
                                  name="A | B", id_or_path="A | B", time="16:00",
                                  duration_min=30, routing="⬜ Blocks"),
            shadow.CREATE, {"title": "A | B"},
        )
        verdict = bake_in_diff.classify_bakein(_make_diff([entry]))
        row = bake_in_run.build_log_row(__import__("datetime").date(2026, 7, 22), "app", verdict, "DIFF", 0)
        assert "A | B" in row["notes"]  # raw, unescaped in the data model

        log_path = tmp_path / "bake-in-log.md"
        bake_in_run.append_log_row(log_path, row)
        assert "A \\| B" in log_path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# bake_in_run.run() — injected manifest+live_state, no I/O beyond the tmp log
# ---------------------------------------------------------------------------

class TestRun:
    def test_run_clean_day_passes_and_writes_row(self, tmp_path):
        import datetime as dt

        manifest = [_todoist_row(), _patch_row(), _flag_row()]
        live_state = {
            "todoist_tasks": [{"id": "1", "content": "Garage Buildout",
                                "due": {"datetime": "2026-07-20T09:00:00Z"}}],
            "vault_frontmatter": {"P/Garage.md": {"assigned": True}},
            "daily_note_text": "# TDTB Plan\nold content",
        }
        log_path = tmp_path / "bake-in-log.md"
        row, verdict = bake_in_run.run(
            tmp_path, dt.date(2026, 7, 20), "skill", manifest, live_state,
            log_path=log_path,
        )
        assert verdict == bake_in_diff.PASS
        assert row["verdict"] == bake_in_diff.PASS
        assert row["driver"] == "skill"
        assert row["date"] == "2026-07-20"
        assert row["unexplained"] == 0
        assert log_path.is_file()
        assert "PASS" in log_path.read_text(encoding="utf-8")

    def test_run_with_diff_writes_unexplained_notes(self, tmp_path):
        import datetime as dt

        manifest = [_todoist_row(name="Guitar")]
        live_state = {"todoist_tasks": []}  # no live match -> would-create -> UNEXPLAINED
        log_path = tmp_path / "bake-in-log.md"
        row, verdict = bake_in_run.run(
            tmp_path, dt.date(2026, 7, 21), "app", manifest, live_state,
            log_path=log_path,
        )
        assert verdict == bake_in_diff.DIFF
        assert "Guitar" in row["notes"]

    def test_run_kill_on_recon_fail_even_if_diff_clean(self, tmp_path):
        import datetime as dt

        manifest = [_todoist_row()]
        live_state = {
            "todoist_tasks": [{"id": "1", "content": "Garage Buildout",
                                "due": {"datetime": "2026-07-22T09:00:00Z"}}],
        }
        row, verdict = bake_in_run.run(
            tmp_path, dt.date(2026, 7, 22), "app", manifest, live_state,
            recon_fail=1, log_path=tmp_path / "bake-in-log.md",
        )
        assert verdict == bake_in_diff.KILL
        assert row["recon_fail"] == 1

    def test_run_accepts_callables_for_injectable_gather(self, tmp_path):
        """manifest/live_state may be (vault_root, today) -> data callables —
        never touching disk/network in this test, just proving the shape is
        respected."""
        import datetime as dt

        calls = []

        def manifest_source(vault_root, today):
            calls.append(("manifest", vault_root, today))
            return [_todoist_row()]

        def live_state_source(vault_root, today):
            calls.append(("live_state", vault_root, today))
            return {"todoist_tasks": [{"id": "1", "content": "Garage Buildout",
                                        "due": {"datetime": "2026-07-23T09:00:00Z"}}]}

        row, verdict = bake_in_run.run(
            tmp_path, dt.date(2026, 7, 23), "skill", manifest_source, live_state_source,
            log_path=tmp_path / "bake-in-log.md",
        )
        assert verdict == bake_in_diff.PASS
        assert len(calls) == 2
        assert calls[0][1] == tmp_path
        assert calls[0][2] == dt.date(2026, 7, 23)

    def test_run_rejects_unknown_driver(self, tmp_path):
        import datetime as dt

        with pytest.raises(ValueError, match="driver"):
            bake_in_run.run(
                tmp_path, dt.date(2026, 7, 24), "bogus", [], {},
                log_path=tmp_path / "bake-in-log.md",
            )

    def test_run_never_writes_to_default_log_path_when_overridden(self, tmp_path, monkeypatch):
        """Guard against a test accidentally polluting the real
        ../bake-in-log.md — the default path must never be touched when a
        caller supplies log_path explicitly."""
        import datetime as dt

        fake_default = tmp_path / "should-not-be-used.md"
        monkeypatch.setattr(bake_in_run, "DEFAULT_LOG_PATH", fake_default)
        override = tmp_path / "override.md"
        bake_in_run.run(
            tmp_path, dt.date(2026, 7, 25), "skill", [], {},
            log_path=override,
        )
        assert override.is_file()
        assert not fake_default.exists()


# ---------------------------------------------------------------------------
# ISS-4: bake_in_run reconciles the Todoist surface BY-ID from the commit
# ledger, not a lag-prone filter read. A filter/search query's index lags
# task creation, so it misses a commit's own same-day creates -> false
# would-create DIFFs. By-ID reads (get_task) are strongly consistent.
# ---------------------------------------------------------------------------

class TestByIdReconcile:
    def _seed_ledger(self, vault, today, created=(), updated=(), noops=()):
        import runstate
        state = runstate.build_runstate({
            "commit_ledger": {
                "today": today.isoformat(),
                "surfaces": {
                    "todoist": {
                        "status": "ok", "step": "A",
                        "created": list(created),
                        "updated": list(updated),
                        "noops": list(noops),
                    }
                },
            }
        })
        runstate.write_runstate(vault, today, state)

    def test_ledger_ids_dedup_across_created_updated_noops(self, tmp_path):
        import datetime as dt
        today = dt.date(2026, 7, 13)
        self._seed_ledger(tmp_path, today, created=["T1", "T2"], updated=["T2"], noops=["T3", "T1"])
        assert bake_in_run._ledger_todoist_ids(tmp_path, today) == ["T1", "T2", "T3"]

    def test_no_ledger_returns_empty_ids(self, tmp_path):
        import datetime as dt
        assert bake_in_run._ledger_todoist_ids(tmp_path, dt.date(2026, 7, 13)) == []

    def test_by_id_flips_false_would_create_to_agree(self, tmp_path, monkeypatch):
        """The core ISS-4 regression: a lagged filter read would false-DIFF the
        6 same-day creates; by-ID reconciliation makes the differ AGREE."""
        import datetime as dt
        today = dt.date(2026, 7, 13)
        self._seed_ledger(tmp_path, today, created=["T1", "T2"])
        # Simulate the stale filter index: base read sees NO tasks.
        monkeypatch.setattr(
            shadow, "gather_live_state",
            lambda config, vault: {"todoist_tasks": [], "vault_frontmatter": {}, "daily_note_text": None},
        )
        live = {
            "T1": {"id": "T1", "content": "Garage Buildout", "due": {"datetime": "2026-07-13T09:00:00Z"}},
            "T2": {"id": "T2", "content": "Volunteering", "due": {"datetime": "2026-07-13T11:30:00Z"}},
        }
        state = bake_in_run.gather_live_state_by_id(tmp_path, today, {}, fetch_task=lambda tid: live[tid])
        assert not state.get("todoist_unavailable")
        assert {t["content"] for t in state["todoist_tasks"]} == {"Garage Buildout", "Volunteering"}
        manifest = [_todoist_row("Garage Buildout", "09:00"), _todoist_row("Volunteering", "11:30")]
        diff = shadow.diff_against_live(manifest, state)
        assert [e.classification for e in diff.entries] == [shadow.NOOP, shadow.NOOP]

    def test_no_ledger_keeps_filter_read(self, tmp_path, monkeypatch):
        import datetime as dt
        base_tasks = [{"id": "F", "content": "FromFilter", "due": {"datetime": "2026-07-13T08:00:00Z"}}]
        monkeypatch.setattr(shadow, "gather_live_state", lambda config, vault: {"todoist_tasks": list(base_tasks)})
        state = bake_in_run.gather_live_state_by_id(
            tmp_path, dt.date(2026, 7, 13), {}, fetch_task=lambda tid: {},
        )
        assert state["todoist_tasks"] == base_tasks

    def test_by_id_skips_404_deleted_task(self, tmp_path, monkeypatch):
        import datetime as dt
        import todoist_client
        today = dt.date(2026, 7, 13)
        self._seed_ledger(tmp_path, today, created=["T1", "GONE"])
        monkeypatch.setattr(shadow, "gather_live_state", lambda config, vault: {"todoist_tasks": []})

        def fetch(tid):
            if tid == "GONE":
                raise todoist_client.TodoistError(404, "not found")
            return {"id": "T1", "content": "Garage Buildout", "due": {"datetime": "2026-07-13T09:00:00Z"}}

        state = bake_in_run.gather_live_state_by_id(tmp_path, today, {}, fetch_task=fetch)
        assert [t["id"] for t in state["todoist_tasks"]] == ["T1"]

    def test_by_id_non_404_error_propagates_from_injected_fetch(self, tmp_path, monkeypatch):
        import datetime as dt
        import todoist_client
        today = dt.date(2026, 7, 13)
        self._seed_ledger(tmp_path, today, created=["T1"])
        monkeypatch.setattr(shadow, "gather_live_state", lambda config, vault: {"todoist_tasks": []})

        def fetch(tid):
            raise todoist_client.TodoistError(500, "server error")

        with pytest.raises(todoist_client.TodoistError):
            bake_in_run.gather_live_state_by_id(tmp_path, today, {}, fetch_task=fetch)

    def test_by_id_reconciles_v1_due_date_via_native_reader(self, tmp_path, monkeypatch):
        """v1 /tasks/{id} carries the time under due.date; post-ISS-5,
        shadow._todoist_due_time reads timed due.date natively, so the by-id
        path reconciles the raw v1 shape as NOOP without any date->datetime
        bridge (the removed _normalize_task_due). Guards against reintroducing
        the false time-None DIFF the bridge originally fixed."""
        import datetime as dt
        today = dt.date(2026, 7, 13)
        self._seed_ledger(tmp_path, today, created=["T1"])
        monkeypatch.setattr(shadow, "gather_live_state", lambda config, vault: {"todoist_tasks": []})
        # v1 shape: due.date populated, due.datetime absent; plus an unreliable is_deleted.
        v1_task = {"id": "T1", "content": "Garage Buildout", "is_deleted": True,
                   "due": {"date": "2026-07-13T09:00:00", "datetime": None, "string": "today at 09:00"}}
        state = bake_in_run.gather_live_state_by_id(tmp_path, today, {}, fetch_task=lambda tid: v1_task)
        # no bridge: the raw v1 due shape is passed through untouched...
        assert state["todoist_tasks"][0]["due"]["datetime"] is None
        assert state["todoist_tasks"][0]["due"]["date"] == "2026-07-13T09:00:00"
        # ...yet shadow's native reader matches the time -> AGREE.
        diff = shadow.diff_against_live([_todoist_row("Garage Buildout", "09:00")], state)
        assert diff.entries[0].classification == shadow.NOOP
