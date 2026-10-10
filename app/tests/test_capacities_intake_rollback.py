"""Offline rollback drill for the direct Capacities intake (U4 slice S8).

Characterization, not new behavior: every assertion below pins behavior that
already ships in S1/S3. The drill flips ``sources.capacities_intake`` between
``direct`` and ``legacy`` on one running app and proves:

* ``direct`` serves ONLY the published cache — the live Capacities builder is
  never constructed and artifact Capacities rows never leak onto a surface;
* ``legacy`` restores the original legacy rows and response shape (no
  ``capacities_intake`` block), so the rollback path is intact;
* the published generation, the cache, and the selection store survive the
  flip unchanged, and a stored selection is still promoted afterwards;
* a missing direct snapshot stays ``refresh_required`` with NO legacy
  fallback, even when an artifact Capacities row is present and would serve
  under ``legacy``.

Everything is local: ``TDTB_HOME`` is the per-test app home, the vault is
``tmp_path``, and the live seams are counters/fakes — never a provider, a
credential read, or a real config edit. The shared builders and the ``vault``
fixture come from ``tests/test_plan_inputs_sources.py`` (S3).
"""
from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))

import capacities_builder as cap_builder  # noqa: E402
import capacities_selections as sel  # noqa: E402
import main as main_mod  # noqa: E402
from tests.test_plan_inputs_sources import (  # noqa: E402
    DIRECT_ROOT,
    DIRECT_SPACE,
    FakeCapacitiesAdapter,
    _CallRecorder,
    _artifact_row,
    _capacities_row,
    _direct_client,
    _direct_object,
    _names,
    _publish_direct,
    _save_direct_source,
    _seed_selections,
    _set_sources,
    _write_fresh_artifact,
    vault,
)

ARTIFACT_CAP_IDENTITY = f"capacities:{DIRECT_SPACE}:{DIRECT_ROOT}:artifact-only"


def _artifact_rows() -> list[dict]:
    """A fresh artifact carrying a stale Capacities row and a Todoist row.

    The Capacities row is the leakage vector: it must never reach a surface
    under ``direct``, yet must serve under ``legacy`` (artifact mode)."""
    return [
        _artifact_row("Artifact cap", "capacities", ARTIFACT_CAP_IDENTITY, assigned=True),
        _artifact_row("Call Vlad", "todoist", "todoist:9001", assigned=True),
    ]


class _LiveSeams:
    """Counting live seams: a real construction under ``direct`` is a failure."""

    def __init__(self, legacy_rows: list[dict]) -> None:
        self.legacy_rows = legacy_rows
        self.clients = 0
        self.builder = 0

    def build_clients(self, _vault, _config):
        self.clients += 1
        return (None, None)

    def build_capacities(self, _vault, _config):
        self.builder += 1
        return FakeCapacitiesAdapter(self.legacy_rows)


def _drill_client(vault: Path, seams: _LiveSeams) -> TestClient:
    app = main_mod.create_app(vault_root=vault)
    app.state.build_read_clients = seams.build_clients
    app.state.build_capacities_adapter = seams.build_capacities
    return TestClient(app)


def test_direct_legacy_direct_flip_uses_cache_not_artifact_and_keeps_state(vault):
    """The rollback drill in artifact mode: direct -> legacy -> direct.

    ``direct`` must serve the published generation only (no artifact Capacities
    leakage); ``legacy`` must restore the artifact Capacities rows; the second
    ``direct`` must show the same generation and a surviving selection."""
    _set_sources(mode="artifact", capacities_intake="direct")
    _save_direct_source(vault)
    _publish_direct(vault, [
        _direct_object("obj-a", DIRECT_ROOT),
        _direct_object("obj-pool", DIRECT_ROOT, assigned=False),
    ])
    _write_fresh_artifact(_artifact_rows())
    _seed_selections("obj-pool")

    seams = _LiveSeams([_capacities_row("Cap A", []), _capacities_row("Cap B", [])])
    client = _drill_client(vault, seams)

    # Phase 1 — direct: cache only, artifact Capacities discarded, selection
    # promoted, and neither live seam touched (artifact mode never needs them).
    first = client.get("/plan-inputs").json()
    block = first["capacities_intake"]
    assert block["mode"] == "direct"
    assert block["state"] == "ok"
    snapshot = cap_builder.read_direct_intake(vault).snapshot
    assert block["generation"] == snapshot.generation
    assert block["installed_at"] == snapshot.installed_at
    assigned = _names(first, "assigned")
    assert "obj-a" in assigned
    assert "obj-pool" in assigned  # the stored selection is honored
    assert "Artifact cap" not in assigned + _names(first, "suggested")
    assert "Call Vlad" in assigned  # artifact Todoist is preserved
    assert seams.builder == 0 and seams.clients == 0

    # Phase 2 — legacy: original legacy rows, no intake block, cache unused.
    _set_sources(mode="artifact", capacities_intake="legacy")
    second = client.get("/plan-inputs").json()
    assert "capacities_intake" not in second
    assert "Artifact cap" in _names(second, "assigned")
    assert "obj-a" not in _names(second, "assigned")
    assert second["source_counts"]["capacities"] == 1
    assert seams.builder == 0 and seams.clients == 0

    # Phase 3 — direct again: the generation, cache, and selection survived.
    _set_sources(mode="artifact", capacities_intake="direct")
    third = client.get("/plan-inputs").json()
    assert third["capacities_intake"]["state"] == "ok"
    assert third["capacities_intake"]["generation"] == block["generation"]
    assert third["capacities_intake"]["installed_at"] == block["installed_at"]
    assert "obj-a" in _names(third, "assigned")
    assert "obj-pool" in _names(third, "assigned")
    assert "Artifact cap" not in _names(third, "assigned")
    stored = sel.load_selections(DIRECT_SPACE)
    assert [record.identity for record in stored.selections] == [
        f"capacities:{DIRECT_SPACE}:{DIRECT_ROOT}:obj-pool"
    ]
    assert cap_builder.read_direct_intake(vault).snapshot.generation == snapshot.generation


def test_direct_legacy_direct_flip_never_builds_live_capacities_under_direct(vault):
    """In live mode the direct phase skips the builder; legacy still builds it.

    This is the live-builder half of the same flip, so the ``direct`` guard is
    pinned independently of the artifact leakage path."""
    _set_sources(mode="live", capacities_intake="direct")
    _save_direct_source(vault)
    _publish_direct(vault, [_direct_object("obj-a", DIRECT_ROOT)])

    seams = _LiveSeams([_capacities_row("Cap A", []), _capacities_row("Cap B", [])])
    client = _drill_client(vault, seams)

    first = client.get("/plan-inputs").json()
    assert seams.builder == 0  # direct never constructs the live builder
    assert "obj-a" in _names(first, "assigned")
    assert not any("adapter setup failed" in w for w in first["source_warnings"])

    _set_sources(mode="live", capacities_intake="legacy")
    second = client.get("/plan-inputs").json()
    assert seams.builder == 1  # legacy uses its own live path
    assert _names(second, "assigned") == ["Cap A", "Cap B"]
    assert "capacities_intake" not in second

    _set_sources(mode="live", capacities_intake="direct")
    third = client.get("/plan-inputs").json()
    assert seams.builder == 1  # back on direct: no new builder
    assert "obj-a" in _names(third, "assigned")


def test_missing_direct_snapshot_is_refresh_required_without_legacy_fallback(vault):
    """No published generation is a hard refusal: ``refresh_required`` and no
    fallback — even though the artifact Capacities row would serve under
    ``legacy``."""
    _set_sources(mode="artifact", capacities_intake="direct")
    _save_direct_source(vault)  # the source record exists; no generation does
    _write_fresh_artifact(_artifact_rows())

    client = _direct_client(
        vault, build_clients=_CallRecorder(), build_capacities=_CallRecorder(),
    )

    body = client.get("/plan-inputs").json()

    assert body["capacities_intake"]["state"] == "refresh_required"
    assert body["capacities_intake"]["generation"] is None
    served = [
        row for row in body["digest"]["assigned"] + body["digest"]["suggested"]
        if row.get("source") == "capacities"
    ]
    assert served == []  # no cache row, and no artifact fallback
    assert any("Refresh required" in w for w in body["source_warnings"])
    assert "Call Vlad" in _names(body, "assigned")

    # The same artifact row DOES serve under legacy: the refusal above is a
    # deliberate no-fallback, not a missing row.
    _set_sources(mode="artifact", capacities_intake="legacy")
    legacy = client.get("/plan-inputs").json()
    assert "Artifact cap" in _names(legacy, "assigned")
