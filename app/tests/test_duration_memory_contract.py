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
