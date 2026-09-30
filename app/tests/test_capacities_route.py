"""TDTB route seam for an injected, read-only Capacities adapter."""
from __future__ import annotations

from datetime import date
from pathlib import Path
import sys

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))

import main  # noqa: E402
from capacities_adapter import CapacitiesReadResult  # noqa: E402
import runstate  # noqa: E402
import tdtb_gather as gather  # noqa: E402


class FakeReadAdapter:
    def __init__(self, row):
        self.row = row
        self.days = []
        self.closed = False

    def items_for_day(self, logical_day: date):
        self.days.append(logical_day)
        return CapacitiesReadResult(items=[self.row], warnings=[])

    def close(self):
        self.closed = True


def test_plan_inputs_projects_injected_capacities_items_and_indexes_identity(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    vault.mkdir()
    monkeypatch.setattr(main.gather, "effective_date", lambda _now: date(2026, 9, 29))
    adapter = FakeReadAdapter({
        "id": "Ship project",
        "name": "Ship project",
        "path": "capacities://space-1/object-1",
        "identity": "capacities:space-1:custom-project:object-1",
        "source": "capacities",
        "types": ["custom-project"],
        "urgency": None,
        "deadline": "2026-09-29",
        "priority_score": 0,
        "assigned": True,
        "duration": 60,
        "duration_minutes": 60,
        "blocks": 2,
        "capacities_id": "object-1",
        "capacities_space_id": "space-1",
        "capacities_structure_id": "custom-project",
        "capacities_completion_supported": False,
        "source_fingerprint": "fingerprint-1",
    })
    app = main.create_app(vault_root=vault)
    app.state.build_capacities_adapter = lambda _vault, _config: adapter

    body = TestClient(app).get("/plan-inputs").json()

    assert [row["name"] for row in body["digest"]["assigned"]] == ["Ship project"]
    assert body["source_counts"]["capacities"] == 1
    assert not any("Capacities" in warning for warning in body["source_warnings"])
    assert adapter.days == [date(2026, 9, 29)]
    assert adapter.closed is True
    index = runstate.read_digest_index(vault, date(2026, 9, 29))
    assert index[0]["identity"] == "capacities:space-1:custom-project:object-1"
    assert index[0]["capacities_id"] == "object-1"


def test_production_seam_honours_the_vault_and_config_call_convention(tmp_path):
    """The seam is called as ``(vault, config_dict)``.

    Regression: the production value used to be the builder itself, whose
    second parameter means an injected ``CapacitiesBuilderConfig``. The app
    passes the parsed vault config dict, so every live read failed with
    "'dict' object has no attribute 'token_path'" and Capacities reported
    itself unavailable instead of loading.
    """
    vault = tmp_path / "vault"
    vault.mkdir()

    # Unconfigured vault: the seam must answer None (no Capacities source)
    # rather than raising, and must accept the app's argument shape.
    assert main.build_real_capacities_adapter(vault, {}) is None
    assert main.create_app(vault_root=vault).state.build_capacities_adapter is (
        main.build_real_capacities_adapter
    )
