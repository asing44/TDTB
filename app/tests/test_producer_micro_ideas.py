"""S2b — the producer side of the Capacities micro-adventure ideas pool.

The producer is where the SELECTION RULE lives (deterministic, no judgement):
include a Capacities object when it carries the configured tag (any effort) OR
it is in the configured collection AND its flattened ``effort`` is empty or an
allowed (case-insensitive) value. The idea text is the object title, the id is
``cap-<object id>``, and bounds/dedupe ride ``micro_adventure``'s S1 normalizer.

Absent source entry -> no ``micro_adventure_ideas`` block at all (the app then
falls back to the config pool). A renamed collection/tag is the silent trap, so
an entry that lists 0 objects warns, and so does a selection that keeps
nothing.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "gather"))
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "tools"))

import app_config  # noqa: E402
import artifact_source as art  # noqa: E402
import micro_adventure as ma  # noqa: E402
import producer_rules as pr  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
TOOLS = REPO_ROOT / "tools"

LOGICAL_DAY = "2026-10-08"
GENERATED_AT = "2026-10-08T09:00:00-07:00"

SETTINGS = {
    "enabled": True,
    "collection": "Personal Adventures",
    "tag": "_TDTB-live",
    "effort_property": "effort",
    "effort_allowed": ["small"],
}


# ---------------------------------------------------------------------------
# Helpers — the exact source-entry shape the tdtb-refresh skill emits
# ---------------------------------------------------------------------------

def _label(name: str) -> dict:
    return {"type": "label", "label": [{"name": name}]}


def _obj(oid: str, title: str, *, collections=(), tags=(), effort=None) -> dict:
    properties = {}
    if effort is not None:
        properties["effort"] = _label(effort)
    return {
        "id": oid,
        "title": title,
        "collections": list(collections),
        "tags": list(tags),
        "properties": properties,
    }


def _entry(*objects: dict, status: str = "ok", warnings=None) -> dict:
    return {
        "status": status,
        "read_at": GENERATED_AT,
        "warnings": list(warnings or []),
        "objects": list(objects),
    }


def _block(entry: dict, settings=None) -> dict | None:
    return pr.build_micro_adventure_ideas(
        entry, settings if settings is not None else SETTINGS,
        generated_at=GENERATED_AT,
    )


def _ids(block: dict) -> list[str]:
    return [item["id"] for item in block["items"]]


# ---------------------------------------------------------------------------
# The selection rule
# ---------------------------------------------------------------------------

SELECTION_CASES = [
    # (label, object kwargs, expected included)
    ("tag-only-any-effort", dict(tags=["_TDTB-live"], effort="Medium"), True),
    ("tag-only-high-effort", dict(tags=["_TDTB-live"], effort="High"), True),
    ("tag-with-hash-prefix", dict(tags=["#_TDTB-live"]), True),
    ("collection-small", dict(collections=["Personal Adventures"], effort="Small"), True),
    ("collection-empty-effort", dict(collections=["Personal Adventures"]), True),
    ("collection-medium", dict(collections=["Personal Adventures"], effort="Medium"), False),
    ("collection-high", dict(collections=["Personal Adventures"], effort="High"), False),
    ("collection-other-effort-not-allowed",
     dict(collections=["Personal Adventures"], effort="Large"), False),
    ("neither", dict(effort="Small"), False),
    ("other-collection-small", dict(collections=["Work"], effort="Small"), False),
]


def test_selection_rule_includes_and_excludes():
    for label, kwargs, expected in SELECTION_CASES:
        block = _block(_entry(_obj("1", "Kayaking", **kwargs)))
        assert (block["items"] != []) is expected, label


def test_effort_allowed_is_case_insensitive():
    for effort in ("small", "SMALL", "Small", " small "):
        block = _block(
            _entry(_obj("1", "Kayaking", collections=["Personal Adventures"],
                        effort=effort))
        )
        assert _ids(block) == ["cap-1"], effort


def test_effort_allowed_accepts_a_plain_string_payload():
    entry = _entry({
        "id": "1", "title": "Kayaking",
        "collections": ["Personal Adventures"],
        "tags": [], "properties": {"effort": "Small"},
    })
    assert _ids(_block(entry)) == ["cap-1"]


def test_tag_wins_even_when_effort_is_disallowed():
    block = _block(_entry(_obj(
        "7", "Gardens Sake",
        collections=["Personal Adventures"], tags=["_TDTB-live"], effort="High",
    )))
    assert _ids(block) == ["cap-7"]
    assert block["items"] == [{"id": "cap-7", "idea": "Gardens Sake"}]


# ---------------------------------------------------------------------------
# Block shape, status, warnings
# ---------------------------------------------------------------------------

def test_block_shape_is_the_agreed_contract():
    block = _block(_entry(
        _obj("1", "Kayaking", tags=["_TDTB-live"]),
        _obj("2", "Coffee crawl", collections=["Personal Adventures"], effort="Small"),
        _obj("3", "South Korea", collections=["Personal Adventures"], effort="Medium"),
    ))
    assert set(block) == {"status", "read_at", "warnings", "items"}
    assert block["status"] == "ok"
    assert block["read_at"] == GENERATED_AT
    assert block["warnings"] == []
    assert block["items"] == [
        {"id": "cap-1", "idea": "Kayaking"},
        {"id": "cap-2", "idea": "Coffee crawl"},
    ]


def test_absent_entry_emits_no_block():
    document = pr.build_artifact(
        {"todoist": {"status": "ok", "tasks": []}},
        {"version": 1, "rules": []},
        logical_day=LOGICAL_DAY, generated_at=GENERATED_AT,
        micro_ideas_settings=SETTINGS,
    )
    assert "micro_adventure_ideas" not in document
    assert art.validate_artifact(document) == []


def test_disabled_settings_emit_no_block():
    block = _block(
        _entry(_obj("1", "Kayaking", tags=["_TDTB-live"])),
        {**SETTINGS, "enabled": False},
    )
    assert block is None


def test_zero_objects_warns_about_the_renamed_filter_trap():
    block = _block(_entry())
    assert block["items"] == []
    assert any("0 objects" in warning for warning in block["warnings"])


def test_selection_keeping_nothing_warns():
    block = _block(_entry(
        _obj("1", "South Korea", collections=["Personal Adventures"], effort="Medium"),
        _obj("2", "Frontier Flight", collections=["Work"], effort="Small"),
    ))
    assert block["items"] == []
    assert any("nothing" in warning or "0 item" in warning
               for warning in block["warnings"])


def test_unrecognized_status_degrades_to_ok_with_a_warning():
    block = _block(_entry(_obj("1", "Kayaking", tags=["_TDTB-live"]),
                          status="weird"))
    assert block["status"] == "ok"
    assert any("weird" in warning for warning in block["warnings"])


def test_partial_status_and_source_warnings_are_carried():
    block = _block(_entry(
        _obj("1", "Kayaking", tags=["_TDTB-live"]),
        status="partial", warnings=["deferred 2 of 7 content reads"],
    ))
    assert block["status"] == "partial"
    assert "deferred 2 of 7 content reads" in block["warnings"]


def test_malformed_entry_emits_a_failed_block():
    block = pr.build_micro_adventure_ideas("nope", SETTINGS, generated_at=GENERATED_AT)
    assert block["status"] == "failed"
    assert block["items"] == []
    assert block["warnings"]


def test_extra_block_warnings_never_hide_the_items():
    block = _block(_entry(
        _obj("1", "Kayaking", tags=["_TDTB-live"]),
        _obj("2", "kayaking ", tags=["_TDTB-live"]),
    ))
    assert _ids(block) == ["cap-1"]
    assert any("repeats an existing idea" in warning for warning in block["warnings"])


# ---------------------------------------------------------------------------
# Bounds (reuses micro_adventure.normalize_capacities_ideas)
# ---------------------------------------------------------------------------

def test_items_are_capped_at_the_normalizer_bound():
    entry = _entry(*[
        _obj(str(i), f"Idea {i}", tags=["_TDTB-live"])
        for i in range(ma.CAPACITIES_MAX_IDEAS + 3)
    ])
    block = _block(entry)
    assert len(block["items"]) == ma.CAPACITIES_MAX_IDEAS
    assert any(str(ma.CAPACITIES_MAX_IDEAS) in warning for warning in block["warnings"])


def test_idea_text_is_truncated_at_the_normalizer_bound():
    long_title = "K" * (ma.CAPACITIES_MAX_IDEA_CHARS + 40)
    block = _block(_entry(_obj("1", long_title, tags=["_TDTB-live"])))
    assert len(block["items"][0]["idea"]) == ma.CAPACITIES_MAX_IDEA_CHARS
    assert any("truncated" in warning for warning in block["warnings"])


def test_ids_are_stable_and_prefixed_once():
    entry = _entry(
        _obj("abc-123", "Kayaking", tags=["_TDTB-live"]),
        _obj("cap-def-456", "Guitar Lessons", tags=["_TDTB-live"]),
    )
    assert _ids(_block(entry)) == ["cap-abc-123", "cap-def-456"]


# ---------------------------------------------------------------------------
# Artifact integration
# ---------------------------------------------------------------------------

def _produced(entry: dict | None, settings=None) -> dict:
    source = {"todoist": {"status": "ok", "read_at": GENERATED_AT, "tasks": []}}
    if entry is not None:
        source["micro_adventure_ideas"] = entry
    document = pr.build_artifact(
        source, {"version": 1, "rules": []},
        logical_day=LOGICAL_DAY, generated_at=GENERATED_AT,
        micro_ideas_settings=settings if settings is not None else SETTINGS,
    )
    return document


def test_artifact_carries_the_block_and_still_validates():
    document = _produced(_entry(_obj("1", "Kayaking", tags=["_TDTB-live"])))
    assert document["micro_adventure_ideas"] == {
        "status": "ok",
        "read_at": GENERATED_AT,
        "warnings": [],
        "items": [{"id": "cap-1", "idea": "Kayaking"}],
    }
    assert art.validate_artifact(document) == []

    validated = subprocess.run(
        [sys.executable, str(TOOLS / "validate_artifact.py"), "-"],
        input=json.dumps(document), capture_output=True, text=True,
        cwd=str(REPO_ROOT),
    )
    assert validated.returncode == 0, validated.stdout + validated.stderr


def test_source_entry_does_not_warn_as_an_unevaluated_source():
    document = _produced(_entry(_obj("1", "Kayaking", tags=["_TDTB-live"])))
    warnings = document["admission"]["warnings"]
    assert not [w for w in warnings if "not evaluated" in w and "micro_adventure" in w]
    assert document["sources"]["micro_adventure_ideas"]["status"] == "ok"


# ---------------------------------------------------------------------------
# Settings: config loader + the fetch-spec CLI
# ---------------------------------------------------------------------------

def _write_config(payload: dict) -> Path:
    path = app_config.config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": 1, **payload}), encoding="utf-8")
    return path


def _run_tool(name: str, *argv: str, from_stdin: str | None = None):
    return subprocess.run(
        [sys.executable, str(TOOLS / name), *argv],
        input=from_stdin, capture_output=True, text=True, cwd=str(REPO_ROOT),
    )


def test_settings_come_from_the_app_config_loader():
    _write_config({"micro_adventure_capacities": {
        "enabled": True, "collection": "Adventures 2027", "tag": "",
        "effort_allowed": ["Small", "Tiny"],
    }})
    settings = pr.micro_ideas_settings()
    assert settings["enabled"] is True
    assert settings["collection"] == "Adventures 2027"
    assert settings["tag"] == ""
    assert settings["effort_property"] == "effort"
    assert settings["effort_allowed"] == ["Small", "Tiny"]


def test_settings_default_to_disabled_without_a_config_block():
    settings = pr.micro_ideas_settings()
    assert settings["enabled"] is False
    assert settings["collection"] == "Personal Adventures"
    assert settings["effort_allowed"] == ["small"]


def test_spec_cli_prints_the_fetch_spec():
    _write_config({"micro_adventure_capacities": {
        "enabled": True, "collection": "Personal Adventures",
        "tag": "_TDTB-live", "effort_property": "effort",
        "effort_allowed": ["small"],
    }})
    spec = _run_tool("micro_ideas_spec.py")
    assert spec.returncode == 0, spec.stderr
    assert json.loads(spec.stdout) == {
        "enabled": True,
        "collection": "Personal Adventures",
        "tag": "_TDTB-live",
        "effort_property": "effort",
        "effort_allowed": ["small"],
    }


def test_spec_cli_fails_clearly_when_disabled():
    spec = _run_tool("micro_ideas_spec.py")
    assert spec.returncode != 0
    assert "disabled" in spec.stderr.lower()


def test_spec_cli_fails_clearly_when_not_configured():
    _write_config({"micro_adventure_capacities": {
        "enabled": True, "collection": "", "tag": "",
    }})
    spec = _run_tool("micro_ideas_spec.py")
    assert spec.returncode != 0
    assert "configured" in spec.stderr.lower()


def test_spec_cli_honours_an_explicit_config_path(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"version": 1, "micro_adventure_capacities": {
        "enabled": True, "collection": "Elsewhere", "tag": "",
    }}), encoding="utf-8")
    spec = _run_tool("micro_ideas_spec.py", "--config", str(path))
    assert spec.returncode == 0, spec.stderr
    assert json.loads(spec.stdout)["collection"] == "Elsewhere"


# ---------------------------------------------------------------------------
# produce_rows CLI
# ---------------------------------------------------------------------------

def _produce(argv_extra: list[str], source: dict):
    return subprocess.run(
        [sys.executable, str(TOOLS / "produce_rows.py"),
         "--logical-day", LOGICAL_DAY,
         "--generated-at", GENERATED_AT,
         "--run-id", "micro-test", *argv_extra],
        input=json.dumps(source), capture_output=True, text=True,
        cwd=str(REPO_ROOT),
    )


def test_produce_rows_reads_settings_from_the_config_loader(tmp_path):
    rules = tmp_path / "rules.json"
    rules.write_text(json.dumps({"version": 1, "rules": []}), encoding="utf-8")
    _write_config({"micro_adventure_capacities": {
        "enabled": True, "collection": "Personal Adventures", "tag": "_TDTB-live",
    }})
    produced = _produce(["--rules", str(rules)], {
        "todoist": {"status": "ok", "read_at": GENERATED_AT, "tasks": []},
        "micro_adventure_ideas": _entry(
            _obj("1", "Kayaking", tags=["_TDTB-live"]),
            _obj("2", "South Korea", collections=["Personal Adventures"], effort="Medium"),
        ),
    })
    assert produced.returncode == 0, produced.stderr
    document = json.loads(produced.stdout)
    assert [item["id"] for item in document["micro_adventure_ideas"]["items"]] == ["cap-1"]
    assert art.validate_artifact(document) == []


def test_produce_rows_accepts_a_fetch_spec_file(tmp_path):
    rules = tmp_path / "rules.json"
    rules.write_text(json.dumps({"version": 1, "rules": []}), encoding="utf-8")
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({**SETTINGS, "collection": "Adventures 2027",
                                "tag": "_TDTB-live"}), encoding="utf-8")
    produced = _produce(
        ["--rules", str(rules), "--micro-ideas-settings", str(spec)],
        {
            "todoist": {"status": "ok", "read_at": GENERATED_AT, "tasks": []},
            "micro_adventure_ideas": _entry(
                _obj("1", "Kayaking", collections=["Adventures 2027"], effort="Small"),
                _obj("2", "Skip me", collections=["Personal Adventures"], effort="Small"),
            ),
        },
    )
    assert produced.returncode == 0, produced.stderr
    document = json.loads(produced.stdout)
    assert [item["id"] for item in document["micro_adventure_ideas"]["items"]] == ["cap-1"]
