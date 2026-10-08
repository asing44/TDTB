"""A1 — the planning-artifact consumption seam (TDD gate).

The app gains ONE normalized artifact it consumes instead of reading
Todoist/Capacities live. These tests pin the contract, every status path, the
hand-edit overlay (O4), the ``sources.mode`` config knob (with ``both``
rejected), and the route wiring that constructs no live client in artifact
mode.
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "gather"))

import app_config  # noqa: E402
import artifact_source as art  # noqa: E402
import main as main_mod  # noqa: E402
import tdtb_gather as gather  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "planning-artifact.json"

NOW = datetime.fromisoformat("2026-10-08T09:05:00-07:00")
LOGICAL_DAY = "2026-10-08"
GENERATED_AT = "2026-10-08T09:00:00-07:00"


def _rows() -> list[dict]:
    return [
        {
            "name": "Call Vlad", "source": "todoist", "path": "todoist://9001",
            "identity": "todoist:9001", "assigned": True,
            "todoist_id": "9001", "duration_minutes": 30, "blocks": 1,
        },
        {
            "name": "Water plants", "source": "todoist", "path": "todoist://9002",
            "identity": "todoist:9002", "assigned": False, "todoist_id": "9002",
        },
    ]


def _sources(status="ok"):
    return {
        "todoist": {
            "status": status, "read_at": GENERATED_AT,
            "rows": 2, "dropped": 0, "deferred": 0, "warnings": [],
        },
        "capacities": {
            "status": "ok", "read_at": GENERATED_AT,
            "rows": 0, "dropped": 0, "deferred": 0, "warnings": [],
        },
    }


def _doc(
    *,
    rows=None,
    sources=None,
    logical_day=LOGICAL_DAY,
    generated_at=GENERATED_AT,
    schema=art.ARTIFACT_SCHEMA,
    version=art.ARTIFACT_VERSION,
    content_hash=None,
) -> dict:
    rows = _rows() if rows is None else rows
    sources = _sources() if sources is None else sources
    return {
        "schema": schema,
        "version": version,
        "generated_at": generated_at,
        "logical_day": logical_day,
        "producer": {"name": "fixture", "version": "0.1.0", "run_id": "r1"},
        "content_hash": content_hash or art.compute_content_hash(sources, rows),
        "sources": sources,
        "rows": rows,
        "admission": {"rule_set_hash": "rs-1", "admitted": [], "dropped": []},
    }


def _write(path: Path, document) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        document if isinstance(document, str)
        else json.dumps(document, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def _load(path: Path, now=NOW, overlay=None, **kwargs):
    return art.load_artifact(
        now, path=path, overlay=overlay or path.with_name("no-overlay.json"),
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Status paths
# ---------------------------------------------------------------------------

def test_missing_artifact_is_empty_rows_with_a_path_and_producer_banner(tmp_path):
    result = _load(tmp_path / "planning-artifact.json")

    assert result.status == art.STATUS_MISSING
    assert result.rows == []
    joined = " ".join(result.warnings)
    assert "planning-artifact.json" in joined
    assert art.PRODUCER_COMMAND.split()[0] in joined


def test_missing_artifact_mentions_a_valid_prev_but_does_not_apply_it(tmp_path):
    target = _write(tmp_path / "planning-artifact.json", _doc())
    prev = tmp_path / art.PREV_ARTIFACT_FILENAME
    prev.write_text(target.read_text(encoding="utf-8"), encoding="utf-8")
    target.unlink()

    result = _load(tmp_path / "planning-artifact.json")

    assert result.status == art.STATUS_MISSING
    assert result.rows == []
    assert result.prev_available is True
    assert art.PREV_ARTIFACT_FILENAME in " ".join(result.warnings)


def test_invalid_json_is_malformed(tmp_path):
    path = _write(tmp_path / "planning-artifact.json", "{not json")
    result = _load(path)
    assert result.status == art.STATUS_MALFORMED
    assert result.rows == []


@pytest.mark.parametrize("bad", [
    {"schema": "wrong.schema"},
    {"version": 2},
    {"generated_at": "yesterday"},
    {"logical_day": "08/10/2026"},
])
def test_wrong_contract_fields_are_malformed(tmp_path, bad):
    doc = _doc()
    doc.update(bad)
    path = _write(tmp_path / "planning-artifact.json", doc)
    result = _load(path)
    assert result.status == art.STATUS_MALFORMED
    assert result.warnings


def test_duplicate_row_names_are_malformed(tmp_path):
    rows = _rows()
    rows[1]["name"] = rows[0]["name"]
    doc = _doc(rows=rows)
    path = _write(tmp_path / "planning-artifact.json", doc)

    result = _load(path)

    assert result.status == art.STATUS_MALFORMED
    assert "duplicate" in " ".join(result.warnings).lower()


def test_missing_required_row_field_is_malformed(tmp_path):
    rows = _rows()
    del rows[0]["identity"]
    path = _write(tmp_path / "planning-artifact.json", _doc(rows=rows))

    result = _load(path)

    assert result.status == art.STATUS_MALFORMED
    assert "identity" in " ".join(result.warnings)


def test_fresh_artifact_uses_rows(tmp_path):
    path = _write(tmp_path / "planning-artifact.json", _doc())
    result = _load(path)
    assert result.status == art.STATUS_FRESH
    assert [r["name"] for r in result.rows] == ["Call Vlad", "Water plants"]
    assert result.logical_day == LOGICAL_DAY
    assert result.age_minutes == 5


def test_aged_artifact_still_uses_rows_with_a_warning(tmp_path):
    path = _write(
        tmp_path / "planning-artifact.json",
        _doc(generated_at="2026-10-08T04:00:00-07:00"),
    )
    result = _load(path, max_age_minutes=240)

    assert result.status == art.STATUS_AGED
    assert len(result.rows) == 2
    assert "old" in " ".join(result.warnings).lower()


def test_stale_by_logical_day_does_not_use_rows(tmp_path):
    path = _write(
        tmp_path / "planning-artifact.json", _doc(logical_day="2026-10-07")
    )
    result = _load(path)

    assert result.status == art.STATUS_STALE
    assert result.rows == []
    assert "2026-10-07" in " ".join(result.warnings)


def test_partial_sources_use_rows_with_a_warning(tmp_path):
    path = _write(
        tmp_path / "planning-artifact.json", _doc(sources=_sources("partial"))
    )
    result = _load(path)

    assert result.status == art.STATUS_PARTIAL
    assert len(result.rows) == 2
    assert "partial" in " ".join(result.warnings).lower() or \
        "incomplete" in " ".join(result.warnings).lower()


def test_content_hash_mismatch_is_edited_not_a_failure(tmp_path):
    path = _write(
        tmp_path / "planning-artifact.json", _doc(content_hash="0" * 64)
    )
    result = _load(path)

    assert result.status == art.STATUS_EDITED
    assert len(result.rows) == 2
    assert "edited" in " ".join(result.warnings).lower()


def test_load_never_raises_on_a_directory(tmp_path):
    # A directory where a file is expected is an OSError, not a crash.
    bogus = tmp_path / "planning-artifact.json"
    bogus.mkdir()
    result = _load(bogus)
    assert result.status in (art.STATUS_MISSING, art.STATUS_MALFORMED)
    assert result.rows == []


# ---------------------------------------------------------------------------
# Field preservation
# ---------------------------------------------------------------------------

def test_unknown_row_fields_are_preserved(tmp_path):
    rows = _rows()
    rows[0]["producer_extra"] = {"nested": [1, 2, 3]}
    path = _write(tmp_path / "planning-artifact.json", _doc(rows=rows))

    result = _load(path)

    assert result.status == art.STATUS_FRESH
    assert result.rows[0]["producer_extra"] == {"nested": [1, 2, 3]}


def test_producer_duration_source_is_ignored_not_validated(tmp_path):
    rows = _rows()
    rows[0]["duration_source"] = "native"
    # Hash the file exactly as written (with the illegal field) so this proves
    # the field is dropped rather than the artifact being flagged edited.
    path = _write(tmp_path / "planning-artifact.json", _doc(rows=rows))

    result = _load(path)

    assert result.status == art.STATUS_FRESH
    assert "duration_source" not in result.rows[0]
    assert "duration_source" not in result.rows[1]


# ---------------------------------------------------------------------------
# Overlay (O4)
# ---------------------------------------------------------------------------

def test_overlay_overrides_and_adds_rows(tmp_path):
    path = _write(tmp_path / "planning-artifact.json", _doc())
    overlay = _write(tmp_path / "planning-overlay.json", {
        "rows": [
            {"name": "Call Vlad", "source": "todoist", "path": "todoist://9001",
             "identity": "todoist:9001", "assigned": True, "blocks": 9},
            {"name": "Hand added", "source": "vault", "path": "Notes/Hand.md",
             "identity": "Notes/Hand.md", "assigned": False},
        ],
    })

    result = _load(path, overlay=overlay)

    by_name = {r["name"]: r for r in result.rows}
    assert by_name["Call Vlad"]["blocks"] == 9          # override wins
    assert by_name["Hand added"]["path"] == "Notes/Hand.md"  # added
    assert len(result.rows) == 3
    assert result.overlay_rows == 2


def test_overlay_is_dropped_entirely_for_a_stale_artifact(tmp_path):
    path = _write(
        tmp_path / "planning-artifact.json", _doc(logical_day="2026-10-07")
    )
    overlay = _write(tmp_path / "planning-overlay.json", {
        "rows": [{"name": "Hand added", "source": "vault",
                  "path": "Notes/Hand.md", "identity": "Notes/Hand.md",
                  "assigned": False}],
    })

    result = _load(path, overlay=overlay)

    assert result.status == art.STATUS_STALE
    assert result.rows == []


def test_malformed_overlay_is_a_warning_not_a_failure(tmp_path):
    path = _write(tmp_path / "planning-artifact.json", _doc())
    overlay = _write(tmp_path / "planning-overlay.json", "{bad json")

    result = _load(path, overlay=overlay)

    assert result.status == art.STATUS_FRESH
    assert len(result.rows) == 2
    assert "overlay" in " ".join(result.warnings).lower()


# ---------------------------------------------------------------------------
# Atomic write helper (shipped for the producer)
# ---------------------------------------------------------------------------

def test_atomic_write_retains_one_prior_copy(tmp_path):
    target = tmp_path / "planning-artifact.json"
    art.atomic_write_artifact(_doc(), path=target)
    first = target.read_text(encoding="utf-8")
    assert not (tmp_path / art.PREV_ARTIFACT_FILENAME).exists()

    doc2 = _doc(logical_day="2026-10-09")
    art.atomic_write_artifact(doc2, path=target)

    assert (tmp_path / art.PREV_ARTIFACT_FILENAME).read_text(encoding="utf-8") == first
    assert json.loads(target.read_text(encoding="utf-8"))["logical_day"] == "2026-10-09"
    # No stray temp files remain.
    assert not [p for p in tmp_path.iterdir() if p.suffix == ".tmp"]


# ---------------------------------------------------------------------------
# sources.mode config knob
# ---------------------------------------------------------------------------

def _write_config(tmp_path: Path, sources) -> Path:
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"version": 1, "sources": sources}), encoding="utf-8")
    return path


def test_mode_artifact_is_read_from_config(tmp_path):
    path = _write_config(tmp_path, {"mode": "artifact"})
    assert app_config.sources_mode(path) == "artifact"


def test_mode_absent_defaults_to_live(tmp_path):
    path = _write_config(tmp_path, {})
    assert app_config.sources_mode(path) == "live"


def test_mode_both_is_rejected_at_config_load(tmp_path):
    path = _write_config(tmp_path, {"mode": "both"})

    assert app_config.load_document(path) is None
    assert app_config.sources_mode(path) == "live"


def test_unknown_mode_is_rejected_at_config_load(tmp_path):
    path = _write_config(tmp_path, {"mode": "hybrid"})
    assert app_config.load_document(path) is None


def test_max_age_default_and_override(tmp_path):
    default = _write_config(tmp_path, {"mode": "artifact"})
    assert app_config.artifact_max_age_minutes(default) == 240
    overridden = _write_config(
        tmp_path, {"mode": "artifact", "artifact": {"max_age_minutes": 30}}
    )
    assert app_config.artifact_max_age_minutes(overridden) == 30


# ---------------------------------------------------------------------------
# Route wiring — artifact mode constructs no live client
# ---------------------------------------------------------------------------

MINIMAL_CONFIG = """\
---
description: test config
---

# TDTB Bridger Config

## Defaults

| Key | Value    |
| --- | -------- |
| eod | 11:45 PM |
"""


@pytest.fixture
def vault(tmp_path) -> Path:
    v = tmp_path / "vault-root"
    v.mkdir()
    p = v / "00 - META/Skill-Configs/tdtb-bridger.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(MINIMAL_CONFIG, encoding="utf-8")
    return v


def _artifact_for_today() -> dict:
    today = gather.effective_date(datetime.now())
    generated = datetime.now().astimezone().isoformat()
    doc = _doc(logical_day=str(today), generated_at=generated)
    return doc, today


def _enable_artifact_mode() -> None:
    app_config.config_path().parent.mkdir(parents=True, exist_ok=True)
    app_config.config_path().write_text(
        json.dumps({"version": 1, "sources": {"mode": "artifact"}}),
        encoding="utf-8",
    )


def _boom(*_args, **_kwargs):
    raise AssertionError("a live client seam was constructed in artifact mode")


def test_artifact_mode_constructs_no_live_client(vault):
    doc, today = _artifact_for_today()
    _enable_artifact_mode()
    art.atomic_write_artifact(doc)

    app = main_mod.create_app(vault_root=vault)
    app.state.build_read_clients = _boom
    app.state.build_capacities_adapter = _boom
    client = TestClient(app)

    body = client.get("/plan-inputs").json()

    names = [r["name"] for r in body["digest"]["assigned"]]
    assert "Call Vlad" in names                     # artifact, not live
    assert body["artifact"]["state"] == "fresh"
    assert body["artifact"]["logical_day"] == str(today)
    assert body["source_counts"]["capacities"] == 0


def test_artifact_mode_missing_file_degrades_with_a_banner(vault):
    _enable_artifact_mode()
    app = main_mod.create_app(vault_root=vault)
    app.state.build_read_clients = _boom
    app.state.build_capacities_adapter = _boom
    client = TestClient(app)

    body = client.get("/plan-inputs").json()

    assert body["artifact"]["state"] == "missing"
    assert body["digest"]["assigned"] == []
    joined = " ".join(body["source_warnings"])
    assert "planning-artifact.json" in joined


def test_artifact_mode_stale_rows_are_not_used_and_ride_warnings(vault):
    doc, _today = _artifact_for_today()
    doc["logical_day"] = "1999-01-01"
    doc["content_hash"] = art.compute_content_hash(doc["sources"], doc["rows"])
    _enable_artifact_mode()
    art.atomic_write_artifact(doc)

    app = main_mod.create_app(vault_root=vault)
    app.state.build_read_clients = _boom
    app.state.build_capacities_adapter = _boom
    client = TestClient(app)

    body = client.get("/plan-inputs").json()

    assert body["artifact"]["state"] == "stale"
    assert body["digest"]["assigned"] == []
    assert body["digest"]["suggested"] == []
    assert any("logical day" in w.lower() for w in body["source_warnings"])


def test_live_mode_still_constructs_the_live_seam(vault):
    # The default is live and unchanged.
    called = {"n": 0}

    def _clients(_v, _c):
        called["n"] += 1
        return None, None

    app = main_mod.create_app(vault_root=vault)
    app.state.build_read_clients = _clients
    app.state.build_capacities_adapter = None
    client = TestClient(app)

    body = client.get("/plan-inputs").json()

    assert called["n"] == 1
    assert body["artifact"]["state"] == "live"


# ---------------------------------------------------------------------------
# The fixture, run through build_digest
# ---------------------------------------------------------------------------

def test_fixture_flows_through_build_digest():
    result = art.load_artifact(
        datetime.fromisoformat("2026-10-08T09:05:00-07:00"),
        path=FIXTURE, overlay=FIXTURE.with_name("none.json"),
        max_age_minutes=240,
    )
    assert result.status == art.STATUS_FRESH
    assert [r["name"] for r in result.rows] == ["Call Vlad", "Water plants", "Review PR"]

    assigned = [r for r in result.rows if r.get("assigned") is True]
    pool = [r for r in result.rows if r.get("assigned") is not True]
    digest = main_mod.build_digest(
        pool, assigned, date(2026, 10, 8),
        ["urgency", "overdue", "deadline", "staleness", "summit"],
    )

    assert [r["name"] for r in digest["assigned"]] == ["Call Vlad"]
    assert {r["name"] for r in digest["suggested"]} == {"Water plants", "Review PR"}
