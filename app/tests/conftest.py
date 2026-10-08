"""Shared test hygiene for the app suite."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import calendar_bridge  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_app_home(tmp_path_factory, monkeypatch):
    """S1: the five app-owned stores (runstate, exclusions, capacities
    settings, capacities source, deferrals) live under ``$TDTB_HOME/state/``.
    Every test therefore gets its own app home — without this, a store write
    would land in the operator's real ``~/.config/tdtb/state/``.

    ``TDTB_HOME`` is read at call time (see ``app_config.app_home``), so
    setting it before the test body is enough."""
    monkeypatch.setenv("TDTB_HOME", str(tmp_path_factory.mktemp("tdtb-home")))


@pytest.fixture(autouse=True)
def _reset_shared_event_store():
    """The T14 shared EventStore singleton is process-lifetime by design —
    exactly wrong for tests: a store faked by one test (e.g. test_shadow's
    DeniedStore) would otherwise stay cached for every later test. Reset the
    cache on both sides of each test."""
    calendar_bridge._shared_store = None
    yield
    calendar_bridge._shared_store = None
