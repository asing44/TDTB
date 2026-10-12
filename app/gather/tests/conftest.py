"""Gather test hygiene: V2 moved runstate to ``$TDTB_HOME/state/runstate/``,
so every test gets its own app home (never the operator's real one)."""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolated_app_home(tmp_path_factory, monkeypatch):
    monkeypatch.setenv("TDTB_HOME", str(tmp_path_factory.mktemp("tdtb-home")))
