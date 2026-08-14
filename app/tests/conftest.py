"""Shared test hygiene for the app suite."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import calendar_bridge  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_shared_event_store():
    """The T14 shared EventStore singleton is process-lifetime by design —
    exactly wrong for tests: a store faked by one test (e.g. test_shadow's
    DeniedStore) would otherwise stay cached for every later test. Reset the
    cache on both sides of each test."""
    calendar_bridge._shared_store = None
    yield
    calendar_bridge._shared_store = None
