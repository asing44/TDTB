"""Tests for ``grit_habits`` — the Shortcut-backed Grit habits reader.

Pins: the ``shortcuts run`` argv/timeout contract, the strict ``tdtb.grit.v1``
payload parser (wrong schema, oversized list/name, duplicate names, record
shape), the isolated record-kind classifier, the logical-day cache (round trip,
other-day ignored, failed refresh leaves bytes untouched), the single-flight
refresh, and one absolute rule — no test ever spawns a process.
"""
from __future__ import annotations

import datetime
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import artifact_source as art  # noqa: E402
import grit_habits as gh  # noqa: E402
import tdtb_gather as gather  # noqa: E402

NOW = datetime.datetime(2026, 10, 14, 9, 30)


def _day(moment: datetime.datetime = NOW) -> str:
    return gather.effective_date(moment).isoformat()


def _payload(**overrides) -> dict:
    payload = {
        "schema": gh.SCHEMA,
        "generated_at": "2026-10-14T07:30:00-07:00",
        "habits": [
            {
                "name": "Read",
                "progress": None,
                "records": [{"kind": "completed", "amount": 1}],
            }
        ],
    }
    payload.update(overrides)
    return payload


def _habit(name: str = "Read", progress=None, records=()) -> gh.HabitReading:
    return gh.HabitReading(
        name=name,
        progress=progress,
        records=tuple(gh.HabitRecord(kind=kind, amount=amount) for kind, amount in records),
    )


class FakeRunner:
    """An injected runner: writes a payload (or not) and records its calls."""

    def __init__(self, *, payload=None, raw=None, result=None, write=True, delay=0.0):
        self.payload = payload
        self.raw = raw
        self.result = result
        self.write = write
        self.delay = delay
        self.calls: list[dict] = []

    def __call__(self, name, output_path, timeout_s=None):
        self.calls.append(
            {"name": name, "output_path": str(output_path), "timeout_s": timeout_s}
        )
        if self.delay:
            time.sleep(self.delay)
        if self.result is not None:
            return self.result
        if self.write:
            text = (
                self.raw
                if self.raw is not None
                else json.dumps(self.payload if self.payload is not None else _payload())
            )
            Path(output_path).write_text(text, encoding="utf-8")
        return gh.RunResult(ok=True)


@pytest.fixture(autouse=True)
def no_process(monkeypatch):
    """Every test runs with a subprocess guard installed.

    ``grit_habits`` imports the shared ``subprocess`` module, so patching its
    ``run`` here covers the real runner too: any accidental spawn fails loudly
    and the fixture exposes the attempted calls for a final assertion.
    """
    attempts: list[tuple] = []

    def _guard(*args, **kwargs):
        attempts.append((args, kwargs))
        raise AssertionError("tests must never invoke subprocess.run")

    monkeypatch.setattr(gh.subprocess, "run", _guard)
    monkeypatch.setattr(gh, "_RECENT_SUCCESS", None)
    yield attempts
    assert attempts == [], "a test invoked subprocess.run for real"


# ---------------------------------------------------------------------------
# Runner (argv + timeout contract, typed failure mapping)
# ---------------------------------------------------------------------------

class TestRunShortcut:
    def test_argv_and_timeout(self, monkeypatch):
        seen = {}

        def fake_run(argv, **kwargs):
            seen["argv"] = argv
            seen["kwargs"] = kwargs
            return subprocess.CompletedProcess(argv, 0)

        monkeypatch.setattr(gh.subprocess, "run", fake_run)
        result = gh.run_shortcut(gh.SHORTCUT_NAME, Path("/tmp/out.json"), 7)

        assert result.ok is True
        assert result.exit_code == 0
        assert seen["argv"] == [
            "shortcuts",
            "run",
            gh.SHORTCUT_NAME,
            "--output-path",
            "/tmp/out.json",
            "--output-type",
            "public.json",
        ]
        assert seen["kwargs"]["timeout"] == 7
        assert seen["kwargs"]["check"] is False

    def test_nonzero_exit_is_typed(self, monkeypatch):
        monkeypatch.setattr(
            gh.subprocess, "run",
            lambda argv, **kwargs: subprocess.CompletedProcess(argv, 2),
        )
        result = gh.run_shortcut(gh.SHORTCUT_NAME, "/tmp/out.json")
        assert result.ok is False
        assert result.reason == gh.REASON_EXIT_NONZERO
        assert result.exit_code == 2

    def test_timeout_is_typed(self, monkeypatch):
        def _timeout(argv, **kwargs):
            raise subprocess.TimeoutExpired(argv, 20)

        monkeypatch.setattr(gh.subprocess, "run", _timeout)
        result = gh.run_shortcut(gh.SHORTCUT_NAME, "/tmp/out.json")
        assert (result.ok, result.reason) == (False, gh.REASON_TIMEOUT)

    def test_missing_binary_is_typed(self, monkeypatch):
        def _missing(argv, **kwargs):
            raise FileNotFoundError("shortcuts")

        monkeypatch.setattr(gh.subprocess, "run", _missing)
        result = gh.run_shortcut(gh.SHORTCUT_NAME, "/tmp/out.json")
        assert (result.ok, result.reason) == (False, gh.REASON_RUNNER_ERROR)


# ---------------------------------------------------------------------------
# Strict parser
# ---------------------------------------------------------------------------

class TestParsePayload:
    def test_success(self):
        habits = gh.parse_payload(_payload())
        assert [habit.name for habit in habits] == ["Read"]
        assert habits[0].records == (gh.HabitRecord(kind="completed", amount=1.0),)
        assert habits[0].status == gh.STATUS_DONE

    def test_bounds_come_from_the_artifact_contract(self):
        assert gh.MAX_HABITS == art.HABIT_ITEMS_MAX
        assert gh.MAX_TEXT == art.HABIT_TEXT_MAX

    def test_wrong_schema_rejected(self):
        with pytest.raises(gh.GritFormatError) as excinfo:
            gh.parse_payload(_payload(schema="tdtb.grit.v2"))
        assert excinfo.value.reason == gh.REASON_SCHEMA

    def test_missing_schema_rejected(self):
        payload = _payload()
        payload.pop("schema")
        with pytest.raises(gh.GritFormatError):
            gh.parse_payload(payload)

    def test_non_list_habits_rejected(self):
        with pytest.raises(gh.GritFormatError):
            gh.parse_payload(_payload(habits={"name": "Read"}))

    def test_oversized_habits_list_rejected(self):
        habits = [
            {"name": f"habit-{index}", "progress": None, "records": []}
            for index in range(art.HABIT_ITEMS_MAX + 1)
        ]
        with pytest.raises(gh.GritFormatError) as excinfo:
            gh.parse_payload(_payload(habits=habits))
        assert excinfo.value.reason == gh.REASON_SCHEMA

    @pytest.mark.parametrize("name", ["", "   ", "x" * (art.HABIT_TEXT_MAX + 1)])
    def test_invalid_name_rejected(self, name):
        habits = [{"name": name, "progress": None, "records": []}]
        with pytest.raises(gh.GritFormatError) as excinfo:
            gh.parse_payload(_payload(habits=habits))
        assert excinfo.value.reason == gh.REASON_SCHEMA

    def test_duplicate_names_rejected_not_deduped(self):
        habits = [
            {"name": "Read", "progress": None, "records": []},
            {"name": " read ", "progress": None, "records": []},
        ]
        with pytest.raises(gh.GritFormatError) as excinfo:
            gh.parse_payload(_payload(habits=habits))
        assert excinfo.value.reason == gh.REASON_DUPLICATE

    @pytest.mark.parametrize(
        "record",
        [
            "completed",
            {"amount": 1},
            {"kind": "", "amount": 1},
            {"kind": "completed", "amount": "1"},
            {"kind": "completed", "amount": True},
        ],
    )
    def test_invalid_record_rejected(self, record):
        habits = [{"name": "Read", "progress": None, "records": [record]}]
        with pytest.raises(gh.GritFormatError):
            gh.parse_payload(_payload(habits=habits))

    def test_records_must_be_a_list(self):
        habits = [{"name": "Read", "progress": None, "records": "none"}]
        with pytest.raises(gh.GritFormatError):
            gh.parse_payload(_payload(habits=habits))

    def test_notes_are_never_parsed_or_stored(self):
        habits = [
            {
                "name": "Read",
                "progress": 1,
                "records": [
                    {
                        "kind": " Completed ",
                        "amount": 1,
                        "recordedAt": "2026-10-14T07:00:00-07:00",
                        "note": "private journal line",
                    }
                ],
            }
        ]
        habit = gh.parse_payload(_payload(habits=habits, extra_key="ignored"))[0]
        assert habit.records == (gh.HabitRecord(kind="completed", amount=1.0),)
        assert "private journal line" not in repr(habit)

    def test_unknown_keys_ignored_and_not_persisted(self):
        payload = _payload(read_at="whatever")
        payload["habits"][0]["extra"] = "dropped"
        stored = gh.store_cache(
            gh.GritRead(ok=True, habits=gh.parse_payload(payload), generated_at="g"),
            now=NOW,
        )
        assert stored is True
        text = gh.cache_path().read_text(encoding="utf-8")
        assert "whatever" not in text
        assert "dropped" not in text


# ---------------------------------------------------------------------------
# Classifier
# ---------------------------------------------------------------------------

class TestClassifier:
    def test_done_via_completion_kind(self):
        assert gh.classify_habit(None, [gh.HabitRecord("completed")]) == gh.STATUS_DONE

    def test_done_via_progress_at_or_above_one(self):
        assert gh.classify_habit(1.0, []) == gh.STATUS_DONE
        assert gh.classify_habit(2.5, []) == gh.STATUS_DONE

    def test_failed_via_kind(self):
        assert gh.classify_habit(None, [gh.HabitRecord("missed")]) == gh.STATUS_FAILED

    def test_done_beats_failed(self):
        records = [gh.HabitRecord("failed"), gh.HabitRecord("completed")]
        assert gh.classify_habit(0, records) == gh.STATUS_DONE

    def test_partial_via_progress(self):
        assert gh.classify_habit(0.4, []) == gh.STATUS_PARTIAL

    def test_partial_via_progress_kind(self):
        assert gh.classify_habit(None, [gh.HabitRecord("partial")]) == gh.STATUS_PARTIAL

    def test_none_without_signal(self):
        assert gh.classify_habit(None, []) == gh.STATUS_NONE
        assert gh.classify_habit(0, []) == gh.STATUS_NONE

    def test_remaining_is_binary(self):
        assert gh.counts_toward_remaining(gh.STATUS_DONE) is False
        assert gh.counts_toward_remaining(gh.STATUS_FAILED) is False
        assert gh.counts_toward_remaining(gh.STATUS_PARTIAL) is True
        assert gh.counts_toward_remaining(gh.STATUS_NONE) is True

    def test_habit_reading_exposes_status_and_remaining(self):
        assert _habit(progress=0.5).status == gh.STATUS_PARTIAL
        assert _habit(progress=0.5).remaining is True
        assert _habit(records=[("completed", 1)]).remaining is False


# ---------------------------------------------------------------------------
# Live read through an injected runner
# ---------------------------------------------------------------------------

class TestReadHabits:
    def test_success(self, no_process):
        runner = FakeRunner()
        result = gh.read_habits(runner, now=NOW)

        assert result.ok is True
        assert result.reason == gh.REASON_OK
        assert result.logical_day == _day()
        assert result.generated_at == "2026-10-14T07:30:00-07:00"
        assert [habit.name for habit in result.habits] == ["Read"]
        assert runner.calls[0]["name"] == gh.SHORTCUT_NAME
        assert runner.calls[0]["timeout_s"] == gh.DEFAULT_TIMEOUT_S
        assert no_process == []

    def test_nonzero_exit(self):
        runner = FakeRunner(result=gh.RunResult(ok=False, reason=gh.REASON_EXIT_NONZERO))
        result = gh.read_habits(runner, now=NOW)
        assert (result.ok, result.reason) == (False, gh.REASON_EXIT_NONZERO)
        assert result.habits == ()

    def test_timeout(self):
        runner = FakeRunner(result=gh.RunResult(ok=False, reason=gh.REASON_TIMEOUT))
        assert gh.read_habits(runner, now=NOW).reason == gh.REASON_TIMEOUT

    def test_missing_output_file(self):
        runner = FakeRunner(write=False)
        result = gh.read_habits(runner, now=NOW)
        assert (result.ok, result.reason) == (False, gh.REASON_MISSING_OUTPUT)

    def test_invalid_json(self):
        runner = FakeRunner(raw="{not json")
        result = gh.read_habits(runner, now=NOW)
        assert (result.ok, result.reason) == (False, gh.REASON_UNPARSEABLE)

    def test_wrong_schema(self):
        runner = FakeRunner(payload=_payload(schema="something.else"))
        result = gh.read_habits(runner, now=NOW)
        assert (result.ok, result.reason) == (False, gh.REASON_SCHEMA)

    def test_duplicate_names(self):
        runner = FakeRunner(
            payload=_payload(
                habits=[
                    {"name": "Read", "progress": None, "records": []},
                    {"name": "read", "progress": None, "records": []},
                ]
            )
        )
        result = gh.read_habits(runner, now=NOW)
        assert (result.ok, result.reason) == (False, gh.REASON_DUPLICATE)

    def test_raising_runner_is_a_typed_failure(self):
        def _boom(name, output_path, timeout_s=None):
            raise RuntimeError("runner exploded")

        result = gh.read_habits(_boom, now=NOW)
        assert (result.ok, result.reason) == (False, gh.REASON_RUNNER_ERROR)


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

class TestCache:
    def test_round_trip(self):
        assert gh.load_cache() is None
        read = gh.GritRead(
            ok=True,
            habits=(_habit(progress=0.5, records=[("partial", 1)]),),
            logical_day=_day(),
            generated_at="2026-10-14T07:30:00-07:00",
        )
        assert gh.store_cache(read, now=NOW) is True

        cached = gh.load_cache()
        assert cached is not None
        assert cached.logical_day == _day()
        assert cached.generated_at == "2026-10-14T07:30:00-07:00"
        assert cached.habits[0].name == "Read"
        assert cached.habits[0].progress == 0.5
        assert cached.habits[0].records == (gh.HabitRecord(kind="partial", amount=1.0),)
        assert cached.habits[0].status == gh.STATUS_PARTIAL

    def test_read_today_uses_same_day_cache(self):
        runner = FakeRunner()
        assert gh.refresh(runner, now=NOW).ok is True
        habits, stale, reason = gh.read_today(NOW)
        assert stale is False
        assert reason == gh.REASON_OK
        assert [habit.name for habit in habits] == ["Read"]

    def test_other_day_cache_is_ignored(self):
        assert gh.refresh(FakeRunner(), now=NOW).ok is True
        tomorrow = NOW + datetime.timedelta(days=1)
        habits, stale, reason = gh.read_today(tomorrow)
        assert habits is None
        assert stale is True
        assert reason == gh.REASON_STALE_DAY

    def test_no_cache(self):
        habits, stale, reason = gh.read_today(NOW)
        assert (habits, stale, reason) == (None, False, gh.REASON_NO_CACHE)

    def test_corrupt_cache_reads_as_no_cache(self):
        gh.cache_path().parent.mkdir(parents=True, exist_ok=True)
        gh.cache_path().write_text("{not json", encoding="utf-8")
        assert gh.load_cache() is None
        assert gh.read_today(NOW) == (None, False, gh.REASON_NO_CACHE)

    def test_failed_store_writes_nothing(self):
        assert gh.store_cache(gh.GritRead(ok=False, reason=gh.REASON_TIMEOUT), now=NOW) is False
        assert not gh.cache_path().exists()

    def test_failed_refresh_leaves_prior_cache_untouched(self):
        assert gh.refresh(FakeRunner(), now=NOW).ok is True
        before = gh.cache_path().read_bytes()

        # A later same-day moment: outside the single-flight window, so this
        # really is a second attempt that fails.
        failed = gh.refresh(
            FakeRunner(result=gh.RunResult(ok=False, reason=gh.REASON_TIMEOUT)),
            now=NOW + datetime.timedelta(minutes=5),
        )
        assert (failed.ok, failed.reason) == (False, gh.REASON_TIMEOUT)
        assert gh.cache_path().read_bytes() == before
        habits, stale, reason = gh.read_today(NOW)
        assert [habit.name for habit in habits] == ["Read"]
        assert stale is False


# ---------------------------------------------------------------------------
# Single-flight
# ---------------------------------------------------------------------------

class TestSingleFlight:
    def test_concurrent_callers_run_the_shortcut_once(self):
        runner = FakeRunner(delay=0.05)
        barrier = threading.Barrier(2)
        results: list[gh.GritRead] = []

        def worker():
            barrier.wait()
            results.append(gh.refresh(runner, now=NOW))

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)

        assert len(results) == 2
        assert all(result.ok for result in results)
        assert len(runner.calls) == 1
        assert gh.load_cache() is not None

    def test_a_later_refresh_reads_again(self):
        runner = FakeRunner()
        assert gh.refresh(runner, now=NOW).ok is True
        later = NOW + datetime.timedelta(minutes=30)
        assert gh.refresh(runner, now=later).ok is True
        assert len(runner.calls) == 2


# ---------------------------------------------------------------------------
# Warning texts (content-free, exact)
# ---------------------------------------------------------------------------

class TestWarnings:
    def test_exact_text_and_no_content_leak(self):
        assert gh.GRIT_READ_STALE == (
            "Grit habits read failed — showing today's last Grit read"
        )
        assert gh.GRIT_READ_FAILED == (
            "Grit habits unavailable — counting every configured habit as not done"
        )
        assert gh.GRIT_UNCONFIGURED == (
            "Grit habits without minutes — using the default habit minutes"
        )
        assert "Read" not in gh.GRIT_READ_STALE
