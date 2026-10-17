"""test_artifact_micro_ideas.py — the optional ``micro_adventure_ideas`` block.

The block is published by the tdtb-refresh producer (slice S2b) and consumed
here (slice S2a): ``artifact_source.load_micro_ideas`` reads it in BOTH source
modes, ignores ``logical_day``, validates it field by field, and never raises.
An artifact WITHOUT the block stays valid and returns the "absent" result the
caller maps to ``unavailable``.
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import app_config  # noqa: E402
import artifact_source as art  # noqa: E402

TOOLS = Path(__file__).resolve().parent.parent.parent / "tools"
FIXTURE = Path(__file__).parent / "fixtures" / "planning-artifact.json"
MAX_IDEAS = 200
MAX_IDEA_CHARS = 200


def _read_at() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _document(*, micro=None, logical_day: str = "2026-10-17") -> dict:
    """A minimal, valid artifact document; ``micro`` adds the ideas block."""
    read_at = _read_at()
    sources = {
        "todoist": {"status": "ok", "read_at": read_at, "rows": 0,
                    "dropped": 0, "deferred": 0, "warnings": []}
    }
    document = {
        "schema": art.ARTIFACT_SCHEMA,
        "version": art.ARTIFACT_VERSION,
        "generated_at": read_at,
        "logical_day": logical_day,
        "producer": {"name": "test", "version": "0.1.0", "run_id": "r1"},
        "content_hash": art.compute_content_hash(sources, []),
        "sources": sources,
        "rows": [],
        "admission": {"rule_set_hash": "rs", "admitted": [], "dropped": []},
    }
    if micro is not None:
        document[art.MICRO_IDEAS_KEY] = micro
    return document


def _write(document, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def _block(items=None, *, status: str = "ok", **extra) -> dict:
    read_at = _read_at()
    block = {
        "status": status,
        "read_at": read_at,
        "warnings": [],
        "items": [] if items is None else items,
    }
    block.update(extra)
    return block


@pytest.fixture
def artifact(tmp_path: Path) -> Path:
    return tmp_path / "planning-artifact.json"


# --------------------------------------------------------------------- absent
class TestAbsent:
    def test_absent_block_returns_none_without_warnings(self, artifact):
        path = _write(_document(), artifact)
        block, warnings = art.load_micro_ideas(path=path)
        assert block is None
        assert warnings == []

    def test_null_block_returns_none(self, artifact):
        document = _document()
        document[art.MICRO_IDEAS_KEY] = None
        path = _write(document, artifact)
        assert art.load_micro_ideas(path=path) == (None, [])

    def test_missing_artifact_never_raises(self, tmp_path):
        block, warnings = art.load_micro_ideas(path=tmp_path / "nope.json")
        assert block is None
        assert isinstance(warnings, list)

    def test_artifact_that_is_not_json_never_raises(self, artifact):
        artifact.write_text("{ not json", encoding="utf-8")
        block, warnings = art.load_micro_ideas(path=artifact)
        assert block is None
        assert warnings

    def test_artifact_document_that_is_not_an_object(self, artifact):
        artifact.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
        block, warnings = art.load_micro_ideas(path=artifact)
        assert block is None
        assert warnings


# ---------------------------------------------------------------------- valid
class TestValid:
    def test_block_round_trips_items(self, artifact):
        items = [
            {"id": "cap-abc", "idea": "FlyFishing"},
            {"id": "def", "idea": "  Coffee crawl  "},
        ]
        path = _write(_document(micro=_block(items)), artifact)
        block, warnings = art.load_micro_ideas(path=path)
        assert warnings == []
        assert block["status"] == "ok"
        assert block["items"] == [
            {"id": "cap-abc", "idea": "FlyFishing"},
            {"id": "cap-def", "idea": "Coffee crawl"},
        ]
        assert block["read_at"]

    def test_logical_day_is_ignored(self, artifact):
        items = [{"id": "cap-abc", "idea": "Kayaking"}]
        path = _write(
            _document(micro=_block(items), logical_day="1999-01-01"), artifact
        )
        block, _warnings = art.load_micro_ideas(path=path)
        assert block is not None
        assert block["items"] == items

    @pytest.mark.parametrize("mode", ["live", "artifact"])
    def test_read_in_both_source_modes(self, artifact, monkeypatch, mode):
        monkeypatch.setattr(app_config, "sources_mode", lambda *_a, **_k: mode)
        items = [{"id": "cap-abc", "idea": "Gardens Sake"}]
        path = _write(_document(micro=_block(items)), artifact)
        block, _warnings = art.load_micro_ideas(path=path)
        assert block is not None
        assert block["items"] == items

    def test_block_warnings_ride_through(self, artifact):
        block_in = _block([{"id": "cap-abc", "idea": "A"}],
                          warnings=["collection listed 0 objects"])
        path = _write(_document(micro=block_in), artifact)
        block, _warnings = art.load_micro_ideas(path=path)
        assert block["warnings"] == ["collection listed 0 objects"]

    def test_failed_status_is_preserved_for_the_caller(self, artifact):
        path = _write(_document(micro=_block([], status="failed")), artifact)
        block, _warnings = art.load_micro_ideas(path=path)
        assert block["status"] == "failed"
        assert block["items"] == []

    def test_unknown_item_keys_are_dropped(self, artifact):
        items = [{"id": "cap-abc", "idea": "A", "effort": "Small", "x": 1}]
        path = _write(_document(micro=_block(items)), artifact)
        block, _warnings = art.load_micro_ideas(path=path)
        assert block["items"] == [{"id": "cap-abc", "idea": "A"}]


# -------------------------------------------------------------------- bounded
class TestBounded:
    def test_bad_items_are_dropped_with_warnings(self, artifact):
        items = [
            "not an object",
            {"idea": "no id"},
            {"id": "cap-blank", "idea": "   "},
            {"id": "cap-abc", "idea": "Kayaking"},
            {"id": "cap-abc", "idea": "Kayaking again"},
        ]
        path = _write(_document(micro=_block(items)), artifact)
        block, warnings = art.load_micro_ideas(path=path)
        assert block["items"] == [{"id": "cap-abc", "idea": "Kayaking"}]
        assert len(warnings) == 4

    def test_long_idea_is_truncated_to_the_cap(self, artifact):
        items = [{"id": "cap-long", "idea": "x" * (MAX_IDEA_CHARS + 50)}]
        path = _write(_document(micro=_block(items)), artifact)
        block, warnings = art.load_micro_ideas(path=path)
        assert block["items"][0]["idea"] == "x" * MAX_IDEA_CHARS
        assert warnings

    def test_more_than_the_item_cap_is_bounded(self, artifact):
        items = [
            {"id": f"cap-{index}", "idea": f"idea {index}"}
            for index in range(MAX_IDEAS + 5)
        ]
        path = _write(_document(micro=_block(items)), artifact)
        block, warnings = art.load_micro_ideas(path=path)
        assert len(block["items"]) == MAX_IDEAS
        assert warnings

    def test_non_string_item_warnings_are_dropped(self, artifact):
        path = _write(
            _document(micro=_block([], warnings=["fine", 7, None])), artifact
        )
        block, _warnings = art.load_micro_ideas(path=path)
        assert block["warnings"] == ["fine"]


# -------------------------------------------------------------------- invalid
class TestInvalidBlock:
    def test_block_that_is_not_an_object(self, artifact):
        path = _write(_document(micro=["nope"]), artifact)
        block, warnings = art.load_micro_ideas(path=path)
        assert block is None
        assert warnings

    def test_items_that_are_not_an_array(self, artifact):
        block_in = _block()
        block_in["items"] = {"id": "cap-abc"}
        path = _write(_document(micro=block_in), artifact)
        block, warnings = art.load_micro_ideas(path=path)
        assert block is None
        assert warnings

    def test_unknown_status_is_unusable(self, artifact):
        path = _write(_document(micro=_block([], status="weird")), artifact)
        block, warnings = art.load_micro_ideas(path=path)
        assert block is None
        assert warnings

    def test_unparseable_read_at_warns_but_keeps_the_block(self, artifact):
        block_in = _block([{"id": "cap-abc", "idea": "A"}])
        block_in["read_at"] = "not a timestamp"
        path = _write(_document(micro=block_in), artifact)
        block, warnings = art.load_micro_ideas(path=path)
        assert block is not None
        assert block["read_at"] == "not a timestamp"
        assert warnings


# ------------------------------------------------------------------ validator
class TestValidator:
    def test_existing_fixture_without_the_block_stays_valid(self):
        document = json.loads(FIXTURE.read_text(encoding="utf-8"))
        assert art.validate_artifact(document) == []
        assert art.MICRO_IDEAS_KEY not in document

    def test_document_with_a_valid_block_is_valid(self, artifact):
        path = _write(
            _document(micro=_block([{"id": "cap-abc", "idea": "A"}])), artifact
        )
        document = json.loads(path.read_text(encoding="utf-8"))
        assert art.validate_artifact(document) == []

    def test_block_that_is_not_an_object_is_rejected(self):
        document = _document()
        document[art.MICRO_IDEAS_KEY] = "nope"
        assert art.validate_artifact(document)

    def test_items_that_are_not_an_array_are_rejected(self):
        document = _document()
        document[art.MICRO_IDEAS_KEY] = {"status": "ok", "items": {"a": 1}}
        assert art.validate_artifact(document)

    def test_status_that_is_not_a_string_is_rejected(self):
        document = _document()
        document[art.MICRO_IDEAS_KEY] = {"status": 7, "items": []}
        assert art.validate_artifact(document)

    def test_warnings_that_are_not_an_array_are_rejected(self):
        document = _document()
        document[art.MICRO_IDEAS_KEY] = {"status": "ok", "items": [],
                                        "warnings": "nope"}
        assert art.validate_artifact(document)

    def test_junk_items_do_not_invalidate_the_artifact(self):
        document = _document()
        document[art.MICRO_IDEAS_KEY] = {
            "status": "ok", "read_at": _read_at(), "warnings": [],
            "items": ["junk", {"idea": "no id"}],
        }
        assert art.validate_artifact(document) == []


# ---------------------------------------------------------------- tools CLI
class TestValidateArtifactCli:
    def _run(self, path: Path) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(TOOLS / "validate_artifact.py"), str(path)],
            capture_output=True, text=True,
        )

    def test_cli_accepts_an_artifact_with_the_block(self, artifact):
        path = _write(
            _document(micro=_block([{"id": "cap-abc", "idea": "A"}])), artifact
        )
        result = self._run(path)
        assert result.returncode == 0, result.stdout + result.stderr
        assert result.stdout.startswith("OK:")

    def test_cli_rejects_a_structurally_invalid_block(self, artifact):
        document = _document()
        document[art.MICRO_IDEAS_KEY] = {"status": "ok", "items": "nope"}
        path = _write(document, artifact)
        result = self._run(path)
        assert result.returncode == 1, result.stdout + result.stderr
        assert "micro_adventure_ideas" in result.stdout
