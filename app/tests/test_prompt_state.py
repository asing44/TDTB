"""U5 P1 — local logical-day prompt draft and persistent opt-in stores.

Test-first: this file was written before ``app/prompt_state.py`` existed, so
the red state is an import failure; each behavior below then failed for its
stated reason against a skeleton and was implemented to green.

Everything here is fixture-only. The autouse ``_isolated_app_home`` fixture
(conftest) puts ``TDTB_HOME`` in a per-test tmp dir, so the store under test
never reads the operator's real machine state, a vault, a provider, or a
credential. Every prompt string is synthetic.
"""
from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import app_config  # noqa: E402
import capacities_cache_io  # noqa: E402
import prompt_state as ps  # noqa: E402

DAY = "2026-10-09"
NEXT_DAY = "2026-10-10"
PRIOR_DAY = "2026-10-08"
OLDER_DAY = "2026-10-07"
NEWER_DAY = "2026-10-11"

ALL_FALSE = {
    "intention": False,
    "megan_nicety": False,
    "stoic_intention": False,
}


def _seed_drafts(day: str, drafts: dict[str, str]) -> Path:
    """Write a well-formed drafts file directly (bypassing save/cleanup)."""
    path = ps.drafts_path(day)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"version": 1, "day": day, "drafts": drafts}),
        encoding="utf-8",
    )
    return path


def _state_files() -> set[str]:
    directory = app_config.state_dir()
    if not directory.exists():
        return set()
    return {p.name for p in directory.iterdir()}


# ---------------------------------------------------------------------------
# Defaults, shape, and no side effects
# ---------------------------------------------------------------------------

def test_missing_files_default_empty_without_creating_them():
    drafts = ps.load_drafts(DAY)
    assert drafts.day == DAY
    assert drafts.drafts == {}

    optins = ps.load_optins()
    assert optins.revision == 0
    assert set(optins.optins) == set(ps.PROMPT_KEYS)
    assert all(value is False for value in optins.optins.values())

    assert not ps.drafts_path(DAY).exists()
    assert not ps.optins_path().exists()


def test_paths_are_state_dir_and_day_scoped():
    assert ps.drafts_path(DAY) == app_config.state_dir() / f"prompt-drafts-{DAY}.json"
    assert ps.optins_path() == app_config.state_dir() / "prompt-optins.json"


def test_saving_touches_only_the_local_state_store():
    ps.save_drafts(day=DAY, patch={"intention": "synthetic intention"})
    ps.save_optins(expected_revision=0, optins={"intention": True})

    # No vault-style output: nothing but the app-home state store was touched.
    assert list(app_config.app_home().rglob("*.md")) == []
    assert _state_files() <= {
        f"prompt-drafts-{DAY}.json",
        "prompt-drafts.lock",
        "prompt-optins.json",
        "prompt-optins.lock",
    }


# ---------------------------------------------------------------------------
# Same-day stability, rollover, exact-day reads
# ---------------------------------------------------------------------------

def test_same_day_draft_survives_restart_reread():
    ps.save_drafts(day=DAY, patch={"intention": "synthetic day-one text"})

    # A fresh read models a restart within the same logical day.
    reread = ps.load_drafts(DAY)
    assert reread.day == DAY
    assert reread.drafts == {"intention": "synthetic day-one text"}


def test_rollover_clears_text_and_keeps_optins():
    ps.save_drafts(day=DAY, patch={"intention": "synthetic day-one text"})
    ps.save_optins(expected_revision=0, optins={"intention": True})

    # The next logical day starts empty...
    assert ps.load_drafts(NEXT_DAY).drafts == {}
    # ...but the per-prompt opt-in carries forward.
    assert ps.load_optins().optins == {
        "intention": True,
        "megan_nicety": False,
        "stoic_intention": False,
    }
    # The prior day's file is still readable until a later write cleans it.
    assert ps.load_drafts(DAY).drafts == {"intention": "synthetic day-one text"}


def test_exact_day_read_never_returns_another_days_text():
    ps.save_drafts(day=DAY, patch={"intention": "synthetic day-one text"})

    # A different day reads a different (absent) file — never the other text.
    assert ps.load_drafts(NEXT_DAY).drafts == {}

    # A file whose stored day disagrees with its requested day is refused.
    mismatched = ps.drafts_path(NEXT_DAY)
    mismatched.parent.mkdir(parents=True, exist_ok=True)
    mismatched.write_text(
        json.dumps(
            {"version": 1, "day": DAY, "drafts": {"intention": "synthetic day-one text"}}
        ),
        encoding="utf-8",
    )
    with pytest.raises(ps.PromptFormatError):
        ps.load_drafts(NEXT_DAY)


# ---------------------------------------------------------------------------
# Patch semantics
# ---------------------------------------------------------------------------

def test_patch_preserves_omitted_keys_and_empty_string_clears():
    ps.save_drafts(day=DAY, patch={"intention": "A", "megan_nicety": "B"})

    saved = ps.save_drafts(day=DAY, patch={"stoic_intention": "C"})
    assert saved.drafts == {"intention": "A", "megan_nicety": "B", "stoic_intention": "C"}

    cleared = ps.save_drafts(day=DAY, patch={"intention": ""})
    assert cleared.drafts == {"megan_nicety": "B", "stoic_intention": "C"}

    updated = ps.save_drafts(day=DAY, patch={"megan_nicety": "B2"})
    assert updated.drafts == {"megan_nicety": "B2", "stoic_intention": "C"}


def test_unknown_prompt_key_is_rejected_and_writes_nothing():
    with pytest.raises(ps.PromptValidationError):
        ps.save_drafts(day=DAY, patch={"unknown_prompt": "x"})
    assert not ps.drafts_path(DAY).exists()


def test_draft_text_must_be_a_string():
    for bad in (5, None, True, ["x"], {"k": "v"}):
        with pytest.raises(ps.PromptValidationError):
            ps.save_drafts(day=DAY, patch={"intention": bad})
    assert not ps.drafts_path(DAY).exists()


def test_non_mapping_patch_is_rejected():
    with pytest.raises(ps.PromptValidationError):
        ps.save_drafts(day=DAY, patch=["intention"])
    assert not ps.drafts_path(DAY).exists()


# ---------------------------------------------------------------------------
# Day validation / no path escape
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "bad_day",
    [
        "../../etc/passwd",
        "2026-10-09/../escape",
        "../prompt-drafts-2026-10-09",
        "2026-10-9",
        "2026-13-01",
        "2026-02-30",
        "",
        "not-a-date",
        "20261009",
        "2026-10-09 ",
    ],
)
def test_invalid_day_is_refused_without_writing(bad_day):
    with pytest.raises(ps.PromptValidationError):
        ps.save_drafts(day=bad_day, patch={"intention": "x"})
    with pytest.raises(ps.PromptValidationError):
        ps.load_drafts(bad_day)
    with pytest.raises(ps.PromptValidationError):
        ps.drafts_path(bad_day)
    assert not (app_config.state_dir()).exists() or list(
        app_config.state_dir().glob("prompt-drafts-*")
    ) == []


# ---------------------------------------------------------------------------
# Strict decoding of existing storage
# ---------------------------------------------------------------------------

def test_malformed_and_duplicate_drafts_json_is_rejected_without_repair():
    target = ps.drafts_path(DAY)
    target.parent.mkdir(parents=True, exist_ok=True)

    cases = [
        # duplicate key at the top level
        '{"version": 1, "version": 1, "day": "2026-10-09", "drafts": {}}',
        # duplicate key inside drafts
        '{"version": 1, "day": "2026-10-09", "drafts": {"intention": "a", "intention": "b"}}',
        # unsupported version
        json.dumps({"version": 2, "day": DAY, "drafts": {}}),
        # unknown top-level key
        json.dumps({"version": 1, "day": DAY, "drafts": {}, "extra": True}),
        # missing top-level key
        json.dumps({"version": 1, "day": DAY}),
        # unknown prompt key
        json.dumps({"version": 1, "day": DAY, "drafts": {"nope": "x"}}),
        # wrong draft value type
        json.dumps({"version": 1, "day": DAY, "drafts": {"intention": 5}}),
        # drafts is not an object
        json.dumps({"version": 1, "day": DAY, "drafts": []}),
        # not JSON at all
        "{not json",
    ]
    for raw in cases:
        target.write_text(raw, encoding="utf-8")
        with pytest.raises(ps.PromptFormatError):
            ps.load_drafts(DAY)
        assert target.read_text(encoding="utf-8") == raw


def test_corrupt_current_draft_bytes_are_preserved_on_write_refusal():
    target = ps.drafts_path(DAY)
    target.parent.mkdir(parents=True, exist_ok=True)
    raw = '{"version": 1, "day": "2026-10-09", "drafts": {"intention": "a", "intention": "b"}}'
    target.write_text(raw, encoding="utf-8")

    with pytest.raises(ps.PromptFormatError):
        ps.save_drafts(day=DAY, patch={"megan_nicety": "x"})

    assert target.read_text(encoding="utf-8") == raw


def test_optins_malformed_storage_is_rejected_without_repair():
    target = ps.optins_path()
    target.parent.mkdir(parents=True, exist_ok=True)

    cases = [
        '{"version": 1, "revision": 0, "optins": {"intention": true}, "optins": {}}',
        json.dumps({"version": 3, "revision": 0, "optins": {}}),
        json.dumps({"version": 1, "revision": -1, "optins": {}}),
        json.dumps({"version": 1, "revision": 0, "optins": {"nope": True}}),
        json.dumps({"version": 1, "revision": 0, "optins": {"intention": "yes"}}),
        json.dumps({"version": 1, "revision": 0, "optins": [], "extra": 1}),
        "not json",
    ]
    for raw in cases:
        target.write_text(raw, encoding="utf-8")
        with pytest.raises(ps.PromptFormatError):
            ps.load_optins()
        assert target.read_text(encoding="utf-8") == raw


# ---------------------------------------------------------------------------
# Opt-ins: defaults, revision, conflict
# ---------------------------------------------------------------------------

def test_optins_default_false_and_revision_bumps_with_conflict():
    saved = ps.save_optins(expected_revision=0, optins={"intention": True})
    assert saved.revision == 1
    assert saved.optins == {
        "intention": True,
        "megan_nicety": False,
        "stoic_intention": False,
    }
    assert ps.load_optins() == saved

    target = ps.optins_path()
    before = target.read_bytes()
    with pytest.raises(ps.PromptOptinConflict) as caught:
        ps.save_optins(expected_revision=0, optins={"megan_nicety": True})
    assert caught.value.expected_revision == 0
    assert caught.value.current_revision == 1
    assert target.read_bytes() == before


def test_optins_reject_unknown_keys_and_non_bool():
    with pytest.raises(ps.PromptValidationError):
        ps.save_optins(expected_revision=0, optins={"unknown_prompt": True})
    for bad in (1, 0, "yes", None):
        with pytest.raises(ps.PromptValidationError):
            ps.save_optins(expected_revision=0, optins={"intention": bad})
    with pytest.raises(ps.PromptValidationError):
        ps.save_optins(expected_revision=-1, optins={"intention": True})
    with pytest.raises(ps.PromptValidationError):
        ps.save_optins(expected_revision=True, optins={"intention": True})
    assert not ps.optins_path().exists()


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------

def test_concurrent_draft_saves_are_consistent():
    barrier = threading.Barrier(2)

    def worker(key: str, value: str) -> None:
        barrier.wait()
        ps.save_drafts(day=DAY, patch={key: value})

    threads = [
        threading.Thread(target=worker, args=("intention", "A")),
        threading.Thread(target=worker, args=("stoic_intention", "C")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert ps.load_drafts(DAY).drafts == {"intention": "A", "stoic_intention": "C"}


def test_concurrent_optin_saves_serialize_to_one_winner():
    barrier = threading.Barrier(2)
    results: list[object] = []

    def worker(key: str) -> None:
        barrier.wait()
        try:
            results.append(ps.save_optins(expected_revision=0, optins={key: True}))
        except ps.PromptOptinConflict as exc:
            results.append(exc)

    threads = [
        threading.Thread(target=worker, args=("intention",)),
        threading.Thread(target=worker, args=("megan_nicety",)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sum(isinstance(r, ps.PromptOptinsRecord) for r in results) == 1
    assert sum(isinstance(r, ps.PromptOptinConflict) for r in results) == 1
    # The stored document is intact and reads back.
    assert ps.load_optins().revision == 1


# ---------------------------------------------------------------------------
# Best-effort cleanup of older draft files
# ---------------------------------------------------------------------------

def test_older_draft_files_cleaned_up_on_successful_write_only():
    older = _seed_drafts(OLDER_DAY, {"intention": "older"})
    prior = _seed_drafts(PRIOR_DAY, {"intention": "prior"})
    newer = _seed_drafts(NEWER_DAY, {"intention": "newer"})
    unrelated = app_config.state_dir() / "prompt-optins.json"
    unrelated.write_text("{}", encoding="utf-8")
    stray = app_config.state_dir() / "prompt-drafts-not-a-day.json"
    stray.write_text("{}", encoding="utf-8")

    ps.save_drafts(day=DAY, patch={"intention": "today"})

    assert not older.exists()
    assert not prior.exists()
    assert newer.exists()
    assert ps.drafts_path(DAY).exists()
    assert unrelated.exists()
    assert stray.exists()


def test_failed_write_does_not_clean_older_files():
    older = _seed_drafts(OLDER_DAY, {"intention": "older"})
    current = ps.drafts_path(DAY)
    current.parent.mkdir(parents=True, exist_ok=True)
    current.write_text("{bad json", encoding="utf-8")

    with pytest.raises(ps.PromptFormatError):
        ps.save_drafts(day=DAY, patch={"intention": "today"})

    assert older.exists()
    assert current.read_text(encoding="utf-8") == "{bad json"


# ---------------------------------------------------------------------------
# Content redaction
# ---------------------------------------------------------------------------

def test_prompt_content_never_appears_in_errors():
    secret = "SYNTHETIC-SECRET-PROMPT-TEXT-9f3a"

    with pytest.raises(ps.PromptValidationError) as caught:
        ps.save_drafts(day=DAY, patch={"intention": [secret]})
    assert secret not in str(caught.value)
    assert secret not in repr(caught.value)

    target = ps.drafts_path(DAY)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(
            {
                "version": 1,
                "day": DAY,
                "drafts": {"intention": secret},
                "extra": True,
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ps.PromptFormatError) as caught_format:
        ps.load_drafts(DAY)
    assert secret not in str(caught_format.value)
    assert secret not in repr(caught_format.value)
    assert caught_format.value.__cause__ is None

    target.write_text(f"{{not json {secret}", encoding="utf-8")
    with pytest.raises(ps.PromptFormatError) as caught_json:
        ps.load_drafts(DAY)
    assert secret not in str(caught_json.value)
    assert secret not in repr(caught_json.value)
    assert secret not in repr(caught_json.value.__cause__)


# ---------------------------------------------------------------------------
# Storage I/O failure — typed, content-free, bytes preserved
# ---------------------------------------------------------------------------

def _deny_write(path, data):
    raise OSError(28, "No space left on device")


def _deny_lock(path):
    raise PermissionError(13, "Permission denied")


def test_save_drafts_write_ioerror_is_typed_content_free_and_preserves_bytes(monkeypatch):
    ps.save_drafts(day=DAY, patch={"intention": "A"})
    target = ps.drafts_path(DAY)
    before = target.read_bytes()
    monkeypatch.setattr(capacities_cache_io, "atomic_write_json", _deny_write)
    with pytest.raises(ps.PromptStateError) as caught:
        ps.save_drafts(day=DAY, patch={"intention": "B"})
    # A write I/O failure maps to the 500 route branch, not the 409 format one.
    assert not isinstance(caught.value, ps.PromptFormatError)
    assert caught.value.__cause__ is None
    assert "No space" not in str(caught.value)
    assert str(target) not in str(caught.value)
    assert target.read_bytes() == before


def test_save_optins_write_ioerror_is_typed_content_free_and_preserves_bytes(monkeypatch):
    ps.save_optins(expected_revision=0, optins={"intention": True})
    target = ps.optins_path()
    before = target.read_bytes()

    def _deny(path, data):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(capacities_cache_io, "atomic_write_json", _deny)
    with pytest.raises(ps.PromptStateError) as caught:
        ps.save_optins(expected_revision=1, optins={"megan_nicety": True})
    assert not isinstance(caught.value, ps.PromptFormatError)
    assert caught.value.__cause__ is None
    assert "Permission" not in str(caught.value)
    assert str(target) not in str(caught.value)
    assert target.read_bytes() == before


def test_save_lock_acquisition_ioerror_is_typed_content_free(monkeypatch):
    monkeypatch.setattr(capacities_cache_io, "acquire_path_lock", _deny_lock)
    with pytest.raises(ps.PromptStateError) as caught:
        ps.save_drafts(day=DAY, patch={"intention": "A"})
    assert caught.value.__cause__ is None
    assert "Permission" not in str(caught.value)
    assert not ps.drafts_path(DAY).exists()


def test_load_ioerror_is_typed_content_free_and_suppresses_cause(monkeypatch):
    target = ps.drafts_path(DAY)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps({"version": 1, "day": DAY, "drafts": {}}), encoding="utf-8"
    )
    real_read_text = Path.read_text

    def _deny(self, *args, **kwargs):
        if self == target:
            raise PermissionError(13, "Permission denied")
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", _deny)
    with pytest.raises(ps.PromptStateError) as caught:
        ps.load_drafts(DAY)
    assert caught.value.__cause__ is None
    assert "Permission" not in str(caught.value)
    assert str(target) not in str(caught.value)
