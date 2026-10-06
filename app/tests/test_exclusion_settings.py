"""Store tests for the local tag-exclusion policy.

The store is one vault-scoped JSON file beneath ``00 - META/Cache``. These
tests pin the contract: defaults never create a file, storage is strict and
fail-closed, revisions are monotonic under a lock, and a failed write never
damages the durable bytes.
"""
from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import exclusion_settings as es  # noqa: E402


SPACE = "space-1"
OTHER_SPACE = "space-2"
TAG_A = "5a25370b-f9a0-40cf-bc3a-0cab4744913c"
TAG_B = "0d194525-c5a1-4af5-bb62-202b83006b5e"


def _entry(tag_id: str = TAG_A, space: str = SPACE, source: str = "capacities") -> dict:
    return {"source": source, "space_id": space, "tag_id": tag_id}


def _write_raw(vault: Path, raw: str) -> Path:
    path = es.settings_path(vault)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(raw, encoding="utf-8")
    return path


def _valid_doc(revision: int = 0, tags: list | None = None) -> dict:
    return {
        "version": 1,
        "revision": revision,
        "exclusions": {"tags": tags if tags is not None else []},
    }


class TestDefaults:
    def test_missing_file_returns_defaults_and_creates_nothing(self, tmp_path):
        vault = tmp_path / "vault-root"
        vault.mkdir()

        result = es.read_settings(vault)

        assert result.persisted is False
        assert result.settings.revision == 0
        assert result.settings.tags == ()
        assert not es.settings_path(vault).exists()
        # Reading must not even materialize the cache directory.
        assert not (vault / "00 - META" / "Cache").exists()

    def test_default_document_shape(self):
        assert es.ExclusionSettings().as_dict() == {
            "version": 1,
            "revision": 0,
            "exclusions": {"tags": []},
        }

    def test_tag_exclusion_rejects_unknown_source(self):
        with pytest.raises(ValueError):
            es.TagExclusion(source="todoist", space_id=SPACE, tag_id=TAG_A)

    def test_tag_exclusion_rejects_non_canonical_uuid(self):
        for bad in (
            "not-a-uuid",
            TAG_A.upper(),
            f"{{{TAG_A}}}",
            f"urn:uuid:{TAG_A}",
            TAG_A.replace("-", ""),
            f" {TAG_A} ",
            "",
        ):
            with pytest.raises(ValueError):
                es.TagExclusion(source="capacities", space_id=SPACE, tag_id=bad)

    def test_tag_exclusion_rejects_empty_or_whitespace_space(self):
        for bad in ("", "  ", "space 1", f" {SPACE}"):
            with pytest.raises(ValueError):
                es.TagExclusion(source="capacities", space_id=bad, tag_id=TAG_A)

    def test_revision_must_be_strict_nonnegative_integer(self):
        for bad in (True, -1, 1.5, "1"):
            with pytest.raises(ValueError):
                es.ExclusionSettings(revision=bad)

    def test_duplicate_identities_rejected(self):
        with pytest.raises(ValueError):
            es.ExclusionSettings(
                tags=(
                    es.TagExclusion("capacities", SPACE, TAG_A),
                    es.TagExclusion("capacities", SPACE, TAG_A),
                )
            )


class TestSave:
    def test_save_writes_sorted_replacement_and_increments_revision(self, tmp_path):
        vault = tmp_path / "vault-root"
        vault.mkdir()

        saved = es.save_settings(
            vault,
            expected_revision=0,
            exclusions=[
                _entry(TAG_B, OTHER_SPACE),
                _entry(TAG_A, SPACE),
            ],
        )

        assert saved.revision == 1
        assert [t.tag_id for t in saved.tags] == [TAG_A, TAG_B]
        on_disk = json.loads(es.settings_path(vault).read_text(encoding="utf-8"))
        assert on_disk == {
            "version": 1,
            "revision": 1,
            "exclusions": {
                "tags": [
                    {"source": "capacities", "space_id": SPACE, "tag_id": TAG_A},
                    {"source": "capacities", "space_id": OTHER_SPACE, "tag_id": TAG_B},
                ]
            },
        }
        # Round-trip read agrees with the saved settings.
        result = es.read_settings(vault)
        assert result.persisted is True
        assert result.settings == saved

    def test_second_save_replaces_the_whole_list(self, tmp_path):
        vault = tmp_path / "vault-root"
        vault.mkdir()
        es.save_settings(vault, expected_revision=0, exclusions=[_entry(TAG_A)])

        saved = es.save_settings(vault, expected_revision=1, exclusions=[_entry(TAG_B)])

        assert saved.revision == 2
        assert [t.tag_id for t in saved.tags] == [TAG_B]

    def test_stale_revision_conflicts_and_preserves_bytes(self, tmp_path):
        vault = tmp_path / "vault-root"
        vault.mkdir()
        es.save_settings(vault, expected_revision=0, exclusions=[_entry(TAG_A)])
        before = es.settings_path(vault).read_bytes()

        with pytest.raises(es.ExclusionSettingsConflictError):
            es.save_settings(vault, expected_revision=0, exclusions=[_entry(TAG_B)])

        assert es.settings_path(vault).read_bytes() == before

    def test_invalid_inputs_rejected_before_any_file_access(self, tmp_path):
        vault = tmp_path / "vault-root"
        vault.mkdir()
        _write_raw(vault, "{not json")

        with pytest.raises(ValueError):
            es.save_settings(vault, expected_revision=0, exclusions=[_entry(source="todoist")])
        with pytest.raises(ValueError):
            es.save_settings(vault, expected_revision=-1, exclusions=[])
        with pytest.raises(ValueError):
            es.save_settings(
                vault,
                expected_revision=0,
                exclusions=[_entry(TAG_A), _entry(TAG_A)],
            )

        assert es.settings_path(vault).read_bytes() == b"{not json"

    def test_malformed_storage_blocks_save_without_erasing(self, tmp_path):
        vault = tmp_path / "vault-root"
        vault.mkdir()
        _write_raw(vault, json.dumps({"version": 1}))

        with pytest.raises(es.ExclusionSettingsFormatError):
            es.save_settings(vault, expected_revision=0, exclusions=[_entry(TAG_A)])

        assert es.settings_path(vault).read_bytes() == json.dumps({"version": 1}).encode()

    def test_concurrent_saves_serialize_on_the_lock(self, tmp_path):
        vault = tmp_path / "vault-root"
        vault.mkdir()
        outcomes: list[str] = []
        barrier = threading.Barrier(2)

        def worker() -> None:
            barrier.wait()
            try:
                es.save_settings(vault, expected_revision=0, exclusions=[_entry(TAG_A)])
                outcomes.append("saved")
            except es.ExclusionSettingsConflictError:
                outcomes.append("conflict")

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert sorted(outcomes) == ["conflict", "saved"]
        assert es.read_settings(vault).settings.revision == 1

    def test_atomic_write_failure_preserves_the_original_bytes(self, tmp_path, monkeypatch):
        vault = tmp_path / "vault-root"
        vault.mkdir()
        es.save_settings(vault, expected_revision=0, exclusions=[_entry(TAG_A)])
        before = es.settings_path(vault).read_bytes()

        def boom(*args, **kwargs):
            raise OSError("simulated write failure")

        monkeypatch.setattr(es, "_atomic_write_json", boom)
        with pytest.raises(OSError):
            es.save_settings(vault, expected_revision=1, exclusions=[_entry(TAG_B)])

        assert es.settings_path(vault).read_bytes() == before
        assert es.read_settings(vault).settings.revision == 1

    def test_no_temp_files_left_behind(self, tmp_path):
        vault = tmp_path / "vault-root"
        vault.mkdir()
        es.save_settings(vault, expected_revision=0, exclusions=[_entry(TAG_A)])

        leftovers = [
            p.name for p in es.settings_path(vault).parent.iterdir()
            if p.name.endswith(".tmp")
        ]
        assert leftovers == []


class TestMalformedStorage:
    @pytest.mark.parametrize(
        "raw",
        [
            "{not json!!",
            "[]",
            json.dumps({"version": 2, "revision": 0, "exclusions": {"tags": []}}),
            json.dumps({"version": 1, "revision": -1, "exclusions": {"tags": []}}),
            json.dumps({"version": 1, "revision": True, "exclusions": {"tags": []}}),
            json.dumps({"version": 1, "revision": 0}),
            json.dumps({"version": 1, "revision": 0, "exclusions": {"tags": [], "labels": []}}),
            json.dumps({"version": 1, "revision": 0, "exclusions": {"tags": {}}}),
            json.dumps({
                "version": 1, "revision": 0,
                "exclusions": {"tags": [{"source": "capacities", "space_id": SPACE}]},
            }),
            json.dumps({
                "version": 1, "revision": 0,
                "exclusions": {"tags": [{
                    "source": "todoist", "space_id": SPACE, "tag_id": TAG_A,
                }]},
            }),
            json.dumps({
                "version": 1, "revision": 0,
                "exclusions": {"tags": [{
                    "source": "capacities", "space_id": SPACE, "tag_id": "nope",
                }]},
            }),
            json.dumps({
                "version": 1, "revision": 0,
                "exclusions": {"tags": [{
                    "source": "capacities", "space_id": SPACE, "tag_id": TAG_A,
                    "title": "habituals",
                }]},
            }),
            json.dumps({
                "version": 1, "revision": 0,
                "exclusions": {"tags": [
                    {"source": "capacities", "space_id": SPACE, "tag_id": TAG_A},
                    {"source": "capacities", "space_id": SPACE, "tag_id": TAG_A},
                ]},
            }),
            # Duplicate JSON keys are rejected at every level.
            '{"version": 1, "version": 1, "revision": 0, "exclusions": {"tags": []}}',
            '{"version": 1, "revision": 0, "exclusions": {"tags": [], "tags": []}}',
        ],
    )
    def test_malformed_storage_raises_and_preserves_bytes(self, tmp_path, raw):
        vault = tmp_path / "vault-root"
        vault.mkdir()
        path = _write_raw(vault, raw)

        with pytest.raises(es.ExclusionSettingsFormatError):
            es.read_settings(vault)

        assert path.read_bytes() == raw.encode()

    def test_valid_document_reads_back(self, tmp_path):
        vault = tmp_path / "vault-root"
        vault.mkdir()
        _write_raw(
            vault,
            json.dumps(_valid_doc(3, [
                {"source": "capacities", "space_id": SPACE, "tag_id": TAG_A},
            ])),
        )

        result = es.read_settings(vault)

        assert result.persisted is True
        assert result.settings.revision == 3
        assert [t.tag_id for t in result.settings.tags] == [TAG_A]

    def test_unreadable_directory_raises_store_error(self, tmp_path):
        vault = tmp_path / "vault-root"
        vault.mkdir()
        # A directory where the file should be is unreadable storage, not a
        # missing file — reads fail closed without touching it.
        es.settings_path(vault).mkdir(parents=True)

        with pytest.raises(es.ExclusionSettingsStoreError):
            es.read_settings(vault)
