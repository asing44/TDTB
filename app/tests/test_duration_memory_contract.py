"""IMP-03 Red tests for the locked duration contract (frozen plan items 10-12).

Locked precedence: saved user memory -> deterministic tag mapping -> Todoist
native or exact named preset -> contract-defined type field -> default. The
payload returns BOTH value and source (``remembered`` / ``tag:<name>`` /
``native`` / ``preset`` / ``type`` / ``default``). Memory is keyed by stable
source identity (Todoist task id, normalized vault path), survives recurring
occurrences, uses atomic writes, and never rewrites source duration fields.
Same-precedence tag collisions fail visibly.

The canonical seam ``app/duration_memory.py`` (PATH-016 in the frozen plan)
does not exist yet: these tests are the Red for IMP-05 and fail with
ModuleNotFoundError until the seam lands. Do not edit them into passing.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


def test_duration_memory_module_exists():
    import duration_memory  # noqa: F401


def test_memory_keyed_by_stable_identity_not_name():
    from duration_memory import read_memory, store_memory

    store_memory("todoist:12345", 90)
    assert read_memory("todoist:12345") == 90
    with pytest.raises(ValueError):
        store_memory("Some Name", 90)  # name alone is not an identity


def test_duration_precedence_memory_first_with_source_label():
    from duration_memory import resolve_duration

    value, source = resolve_duration(
        {"name": "Press", "todoist_id": "12345", "duration": 30},
        presets=[],
        fm={},
        memory={"todoist:12345": 120},
    )
    assert (value, source) == (120, "remembered")


def test_tag_collision_fails_visibly():
    from duration_memory import resolve_duration

    with pytest.raises(ValueError):
        resolve_duration(
            {"name": "Press", "labels": ["dur30", "dur45"]},
            presets=[],
            fm={},
            memory={},
        )


# ---------------------------------------------------------------------------
# FT-01: vault-scoped versioned duration-memory cache (MVP)
# ---------------------------------------------------------------------------

import json  # noqa: E402
import os  # noqa: E402

import duration_memory as dm  # noqa: E402


def _write_cache(vault_root, data: dict, raw: str | None = None) -> None:
    """Seed the cache file with ``data`` (JSON-serialized) or raw text."""
    path = dm.cache_path(vault_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(raw if raw is not None else json.dumps(data), encoding="utf-8")


def _cache_bytes(vault_root) -> bytes:
    return dm.cache_path(vault_root).read_bytes()


class TestVaultCachePaths:
    def test_paths_derive_only_from_vault_root(self, tmp_path):
        v1 = tmp_path / "vault-a"
        v2 = tmp_path / "vault-b"
        assert dm.cache_path(v1) == v1 / "00 - META/Cache/tdtb-duration-memory.json"
        assert dm.lock_path(v1) == v1 / "00 - META/Cache/tdtb-duration-memory.lock"
        assert dm.cache_path(v1) != dm.cache_path(v2)
        # No absolute-path or env leakage: both paths sit strictly beneath the
        # resolved vault root.
        assert dm.cache_path(v1).is_relative_to(v1)
        assert dm.lock_path(v1).is_relative_to(v1)

    def test_vault_isolation(self, tmp_path):
        a = tmp_path / "vault-a"
        b = tmp_path / "vault-b"
        dm.save_memory(a, "todoist:1", 90)
        assert dm.read_vault_memory(a) == {"todoist:1": 90}
        assert dm.read_vault_memory(b) == {}


class TestStrictValidation:
    @pytest.mark.parametrize("value", [True, False])
    def test_rejects_bool(self, tmp_path, value):
        with pytest.raises(ValueError):
            dm.validate_duration_minutes(value)
        with pytest.raises(ValueError):
            dm.save_memory(tmp_path, "todoist:1", value)
        assert not dm.cache_path(tmp_path).exists()

    @pytest.mark.parametrize("value", [30.5, 12.0, -5, 7, 23, 0.5])
    def test_rejects_fraction_negative_off_grid(self, tmp_path, value):
        with pytest.raises(ValueError):
            dm.validate_duration_minutes(value)
        with pytest.raises(ValueError):
            dm.save_memory(tmp_path, "todoist:1", value)
        assert not dm.cache_path(tmp_path).exists()

    def test_zero_is_valid(self, tmp_path):
        assert dm.validate_duration_minutes(0) == 0
        assert dm.save_memory(tmp_path, "todoist:1", 0) == 0
        assert dm.read_vault_memory(tmp_path) == {"todoist:1": 0}

    @pytest.mark.parametrize("value", [0, 5, 30, 90, 120])
    def test_grid_values_round_trip(self, tmp_path, value):
        assert dm.save_memory(tmp_path, "todoist:1", value) == value
        assert dm.read_vault_memory(tmp_path) == {"todoist:1": value}

    def test_validation_does_not_mutate_existing_cache(self, tmp_path):
        dm.save_memory(tmp_path, "todoist:1", 90)
        before = _cache_bytes(tmp_path)
        with pytest.raises(ValueError):
            dm.save_memory(tmp_path, "todoist:1", 7)
        with pytest.raises(ValueError):
            dm.save_memory(tmp_path, "todoist:1", True)
        assert _cache_bytes(tmp_path) == before


class TestSaveReset:
    def test_save_writes_versioned_json(self, tmp_path):
        dm.save_memory(tmp_path, "todoist:123", 90)
        data = json.loads(_cache_bytes(tmp_path).decode("utf-8"))
        assert data["version"] == 1
        assert data["items"] == {"todoist:123": 90}

    def test_save_vault_path_identity(self, tmp_path):
        identity = "50 - Operations/Projects/Make.md"
        dm.save_memory(tmp_path, identity, 30)
        assert dm.read_vault_memory(tmp_path) == {identity: 30}

    def test_save_rejects_name_only_without_mutation(self, tmp_path):
        with pytest.raises(ValueError):
            dm.save_memory(tmp_path, "Some Display Name", 90)
        assert not dm.cache_path(tmp_path).exists()

    def test_save_replaces_existing_value_for_identity(self, tmp_path):
        dm.save_memory(tmp_path, "todoist:1", 90)
        dm.save_memory(tmp_path, "todoist:1", 60)
        assert dm.read_vault_memory(tmp_path) == {"todoist:1": 60}

    def test_one_canonical_identity_per_key(self, tmp_path):
        dm.save_memory(tmp_path, "todoist:1", 90)
        dm.save_memory(tmp_path, "todoist:2", 120)
        assert dm.read_vault_memory(tmp_path) == {"todoist:1": 90, "todoist:2": 120}

    def test_reset_removes_entry(self, tmp_path):
        dm.save_memory(tmp_path, "todoist:1", 90)
        assert dm.reset_memory(tmp_path, "todoist:1") is True
        assert dm.read_vault_memory(tmp_path) == {}
        assert json.loads(_cache_bytes(tmp_path).decode("utf-8"))["items"] == {}

    def test_reset_missing_identity_returns_false(self, tmp_path):
        dm.save_memory(tmp_path, "todoist:1", 90)
        assert dm.reset_memory(tmp_path, "todoist:2") is False
        assert dm.read_vault_memory(tmp_path) == {"todoist:1": 90}

    def test_reset_missing_file_returns_false_no_creation(self, tmp_path):
        assert dm.reset_memory(tmp_path, "todoist:1") is False
        assert not dm.cache_path(tmp_path).exists()

    def test_reset_rejects_name_only(self, tmp_path):
        dm.save_memory(tmp_path, "todoist:1", 90)
        with pytest.raises(ValueError):
            dm.reset_memory(tmp_path, "Display Name")
        assert dm.read_vault_memory(tmp_path) == {"todoist:1": 90}


class TestFallbackReadsNoRepair:
    def test_missing_file_reads_empty(self, tmp_path):
        assert dm.read_vault_memory(tmp_path) == {}

    def test_corrupt_file_reads_empty_no_repair(self, tmp_path):
        _write_cache(tmp_path, {}, raw="{not json!!")
        assert dm.read_vault_memory(tmp_path) == {}
        assert _cache_bytes(tmp_path) == b"{not json!!"

    def test_unsupported_version_reads_empty_no_repair(self, tmp_path):
        _write_cache(tmp_path, {"version": 99, "items": {"todoist:1": 90}})
        assert dm.read_vault_memory(tmp_path) == {}
        data = json.loads(_cache_bytes(tmp_path).decode("utf-8"))
        assert data["version"] == 99  # untouched

    def test_wrong_shape_reads_empty_no_repair(self, tmp_path):
        _write_cache(tmp_path, [1, 2, 3])
        assert dm.read_vault_memory(tmp_path) == {}
        assert _cache_bytes(tmp_path) == b"[1, 2, 3]"

    def test_invalid_entries_skipped_no_repair(self, tmp_path):
        _write_cache(tmp_path, {
            "version": 1,
            "items": {
                "todoist:1": 90,
                "todoist:2": 7,            # off-grid — invalid
                "todoist:3": True,         # bool — invalid
                "Display Name": 30,        # name-only identity — invalid
                "todoist:4": -5,           # negative — invalid
            },
        })
        assert dm.read_vault_memory(tmp_path) == {"todoist:1": 90}
        data = json.loads(_cache_bytes(tmp_path).decode("utf-8"))
        assert "todoist:2" in data["items"]  # no repair


class TestFailClosedWrites:
    def test_save_fails_closed_on_corrupt_read(self, tmp_path):
        _write_cache(tmp_path, {}, raw="garbage")
        with pytest.raises(dm.MemoryStoreError):
            dm.save_memory(tmp_path, "todoist:1", 90)
        assert _cache_bytes(tmp_path) == b"garbage"

    def test_save_fails_closed_on_unsupported_version(self, tmp_path):
        _write_cache(tmp_path, {"version": 99, "items": {}})
        with pytest.raises(dm.MemoryStoreError):
            dm.save_memory(tmp_path, "todoist:1", 90)
        data = json.loads(_cache_bytes(tmp_path).decode("utf-8"))
        assert data["version"] == 99

    def test_save_fails_closed_on_invalid_entry(self, tmp_path):
        _write_cache(tmp_path, {"version": 1, "items": {"todoist:2": 7}})
        with pytest.raises(dm.MemoryStoreError):
            dm.save_memory(tmp_path, "todoist:1", 90)
        data = json.loads(_cache_bytes(tmp_path).decode("utf-8"))
        assert data["items"] == {"todoist:2": 7}  # bytes preserved

    def test_save_fails_closed_on_lock_failure(self, tmp_path, monkeypatch):
        dm.save_memory(tmp_path, "todoist:1", 90)
        before = _cache_bytes(tmp_path)

        def boom(vault_root):
            raise OSError("lock unavailable")

        monkeypatch.setattr(dm, "_acquire_lock_file", boom)
        with pytest.raises(OSError):
            dm.save_memory(tmp_path, "todoist:1", 120)
        assert _cache_bytes(tmp_path) == before

    def test_save_fails_closed_on_write_failure(self, tmp_path, monkeypatch):
        dm.save_memory(tmp_path, "todoist:1", 90)
        before = _cache_bytes(tmp_path)

        def boom(path, data):
            raise OSError("write failed")

        monkeypatch.setattr(dm, "_atomic_write_json", boom)
        with pytest.raises(OSError):
            dm.save_memory(tmp_path, "todoist:1", 120)
        assert _cache_bytes(tmp_path) == before

    def test_reset_fails_closed_on_corrupt_read(self, tmp_path):
        _write_cache(tmp_path, {}, raw="garbage")
        with pytest.raises(dm.MemoryStoreError):
            dm.reset_memory(tmp_path, "todoist:1")
        assert _cache_bytes(tmp_path) == b"garbage"

    def test_lock_file_created_under_vault(self, tmp_path):
        dm.save_memory(tmp_path, "todoist:1", 90)
        assert dm.lock_path(tmp_path).is_file()


class TestRememberedOverlay:
    def test_overlay_sets_blocks_and_label(self):
        row = {"name": "Press", "todoist_id": "123", "path": None}
        dm.apply_remembered_overlay(row, {"todoist:123": 90})
        assert row["blocks"] == 3
        assert row["duration_source"] == "remembered"
        assert row["duration_minutes"] == 90

    def test_overlay_noop_without_identity(self):
        row = {"name": "Press", "path": None}
        dm.apply_remembered_overlay(row, {"todoist:123": 90})
        assert "blocks" not in row and "duration_source" not in row

    def test_overlay_noop_without_memory(self):
        row = {"name": "Press", "todoist_id": "123", "path": None}
        dm.apply_remembered_overlay(row, {})
        assert "blocks" not in row and "duration_source" not in row

    # FT-05 F1: exact remembered minutes must survive the GET projection —
    # 45 minutes is 1.5 blocks, never a 30-minute-grid ceiling of 2.
    def test_overlay_preserves_exact_45_minutes(self):
        row = {"name": "Press", "todoist_id": "123", "path": None}
        dm.apply_remembered_overlay(row, {"todoist:123": 45})
        assert row["blocks"] == 1.5
        assert row["duration_source"] == "remembered"
        assert row["duration_minutes"] == 45

    def test_overlay_75_minutes_stays_2_5_blocks(self):
        row = {"name": "Press", "todoist_id": "123", "path": None}
        dm.apply_remembered_overlay(row, {"todoist:123": 75})
        assert row["blocks"] == 2.5
        assert row["duration_minutes"] == 75

    def test_overlay_integral_minutes_stay_integral_blocks(self):
        row = {"name": "Press", "todoist_id": "123", "path": None}
        dm.apply_remembered_overlay(row, {"todoist:123": 90})
        assert row["blocks"] == 3
        assert isinstance(row["blocks"], int)

    def test_overlay_skips_invalid_remembered_value(self):
        row = {"name": "Press", "todoist_id": "123", "path": None}
        dm.apply_remembered_overlay(row, {"todoist:123": 7})  # off-grid
        assert "blocks" not in row and "duration_source" not in row

    def test_overlay_does_not_mutate_memory(self):
        row = {"name": "Press", "todoist_id": "123", "path": None}
        memory = {"todoist:123": 90}
        dm.apply_remembered_overlay(row, memory)
        assert memory == {"todoist:123": 90}
