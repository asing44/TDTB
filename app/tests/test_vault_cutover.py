"""test_vault_cutover.py — slice S5: the vault cutover flag and its report.

Obsidian is retired, but the vault gather is still the source of the digest's
candidate pool. S5 makes turning it off a single reversible config flag
(``sources.vault_enabled``) rather than a code deletion — S6 deletes. The
plan names the risk this file exists to pin:

    "Silent data loss: ~96 rows vanish with nothing flagging it."

So two properties matter and are pinned here: the flag must **fail toward
"vault on"** (a malformed config can never silently drop the rows), and turning
it off must be **loud** — a warning, plus a report that lists exactly what
would go before anyone flips it.
"""
from __future__ import annotations

import importlib.util
import json
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app_config
import main


TODAY = date(2026, 10, 9)

MINIMAL_CONFIG = """\
---
description: test config
last_updated: 2026-07-01
---

# TDTB Bridger Config

## Defaults

| Key | Value    |
| --- | -------- |
| eod | 11:45 PM |

## Anchored Lifestyle Blocks

| Block           | Type | Start   | End | Duration | Days  | overlap_allowed |
| --------------- | ---- | ------- | --- | -------- | ----- | --------------- |
| Morning Routine | hard | 7:45 AM | —   | 80m      | daily | no              |

## Calendar Capacity Classes

| BusyCal title | Class |
| --- | --- |
| Fixture | fixed |
"""


def _write_note(vault: Path, rel: str, name: str, *, deadline=None) -> Path:
    """One pool-eligible vault note with the frontmatter the gather reads."""
    path = vault / rel / f"{name}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "---",
        "type: project",
        "status: in-progress",
        "assigned: false",
    ]
    if deadline is not None:
        lines.append(f"deadline: {deadline}")
    lines += ["---", "", f"# {name}", ""]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


@pytest.fixture
def vault(tmp_path) -> Path:
    v = tmp_path / "vault-root"
    (v / "00 - META/Skill-Configs").mkdir(parents=True)
    (v / "00 - META/Skill-Configs/tdtb-bridger.md").write_text(
        MINIMAL_CONFIG, encoding="utf-8"
    )
    _write_note(v, "50 - Operations/Projects", "Alpha Project")
    _write_note(v, "50 - Operations/Projects", "Beta Project", deadline="2026-10-20")
    _write_note(v, "05 - Capture", "A Captured Idea")
    return v


def _set_flag(value) -> None:
    """Write the operator's config.json, optionally carrying the flag."""
    path = app_config.config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    document = {"version": 1, "sources": {"mode": "artifact"}}
    if value is not None:
        document["sources"]["vault_enabled"] = value
    path.write_text(json.dumps(document), encoding="utf-8")


def _client(vault: Path) -> TestClient:
    app = main.create_app(vault_root=vault)
    app.state.build_read_clients = lambda v, c: (None, None)
    app.state.build_calendar_store = lambda v, c: None
    app.state.build_capacities_adapter = lambda v, c: None
    return TestClient(app)


def _vault_rows(body: dict) -> list[dict]:
    """Digest rows that came from the vault (not Todoist, not Capacities)."""
    digest = body["digest"]
    rows = list(digest["assigned"]) + list(digest["suggested"])
    return [r for r in rows if r.get("source") not in ("todoist", "capacities")]


# ---------------------------------------------------------------------------
# The flag's resolution — the safety property
# ---------------------------------------------------------------------------

class TestVaultEnabledResolution:
    def test_defaults_true_when_config_is_absent(self):
        assert app_config.vault_enabled() is True

    def test_defaults_true_when_config_is_unusable(self, tmp_path):
        path = tmp_path / "config.json"
        path.write_text("{ this is not json", encoding="utf-8")
        assert app_config.vault_enabled(path) is True

    def test_defaults_true_when_the_document_is_rejected(self, tmp_path):
        path = tmp_path / "config.json"
        path.write_text(
            json.dumps({"version": 999, "sources": {"vault_enabled": False}}),
            encoding="utf-8",
        )
        # An unsupported version is rejected outright, so the flag it carries
        # must NOT be honoured — failing toward "vault on" is deliberate.
        assert app_config.vault_enabled(path) is True

    def test_defaults_true_when_the_key_is_absent(self, tmp_path):
        path = tmp_path / "config.json"
        path.write_text(json.dumps({"version": 1, "sources": {}}), encoding="utf-8")
        assert app_config.vault_enabled(path) is True

    def test_only_an_explicit_false_disables_it(self, tmp_path):
        path = tmp_path / "config.json"
        path.write_text(
            json.dumps({"version": 1, "sources": {"vault_enabled": False}}),
            encoding="utf-8",
        )
        assert app_config.vault_enabled(path) is False

    def test_accepts_the_top_level_alias(self, tmp_path):
        path = tmp_path / "config.json"
        path.write_text(json.dumps({"version": 1, "vault_enabled": False}),
                        encoding="utf-8")
        assert app_config.vault_enabled(path) is False

    def test_an_unparseable_value_does_not_disable_it(self, tmp_path):
        path = tmp_path / "config.json"
        path.write_text(
            json.dumps({"version": 1, "sources": {"vault_enabled": "maybe"}}),
            encoding="utf-8",
        )
        assert app_config.vault_enabled(path) is True


# ---------------------------------------------------------------------------
# The digest, both ways
# ---------------------------------------------------------------------------

class TestDigestWithVaultRows:
    def test_default_keeps_the_vault_rows(self, vault):
        """Shipping S5 must change nothing."""
        _set_flag(None)
        body = _client(vault).get("/plan-inputs").json()
        assert len(_vault_rows(body)) == 3
        assert body["source_counts"]["vault"] == 3
        assert not [
            w for w in body.get("source_warnings") or []
            if "Vault rows are disabled" in w
        ]

    def test_flag_true_keeps_the_vault_rows(self, vault):
        _set_flag(True)
        body = _client(vault).get("/plan-inputs").json()
        assert body["source_counts"]["vault"] == 3

    def test_flag_false_drops_the_rows_and_says_so(self, vault):
        _set_flag(False)
        body = _client(vault).get("/plan-inputs").json()

        assert _vault_rows(body) == []
        assert body["source_counts"]["vault"] == 0
        warnings = [w for w in body.get("source_warnings") or []
                    if "Vault rows are disabled" in w]
        assert warnings, "disabling the vault must be loud, not silent"

    def test_flag_false_serves_without_any_vault_root(self):
        """The cutover must not 503 on a missing vault root."""
        _set_flag(False)
        app = main.create_app()          # no vault_root, no env var
        app.state.build_read_clients = lambda v, c: (None, None)
        app.state.build_calendar_store = lambda v, c: None
        app.state.build_capacities_adapter = lambda v, c: None
        response = TestClient(app).get("/plan-inputs")
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# The report — what the flip would remove
# ---------------------------------------------------------------------------

def _load_report_module():
    path = Path(__file__).resolve().parent.parent.parent / "tools" / "vault_rows_report.py"
    spec = importlib.util.spec_from_file_location("vault_rows_report", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestVaultRowsReport:
    def test_lists_exactly_the_rows_the_digest_would_drop(self, vault):
        report = _load_report_module().collect(vault, TODAY)
        assert report["total"] == 3
        assert sorted(r["name"] for r in report["rows"]) == [
            "A Captured Idea", "Alpha Project", "Beta Project",
        ]

    def test_groups_by_top_level_folder(self, vault):
        report = _load_report_module().collect(vault, TODAY)
        assert report["by_top_level_folder"] == {
            "50 - Operations": 2,
            "05 - Capture": 1,
        }

    def test_reports_which_rows_carry_a_deadline(self, vault):
        report = _load_report_module().collect(vault, TODAY)
        assert report["with_deadline"] == 1
        assert report["without_deadline"] == 2

    def test_writes_nothing(self, vault, tmp_path):
        """It is a triage aid, not a mutation — the vault must be untouched."""
        before = {
            p: p.read_text(encoding="utf-8")
            for p in sorted(vault.rglob("*")) if p.is_file()
        }
        module = _load_report_module()
        module.collect(vault, TODAY)
        module.render_markdown(module.collect(vault, TODAY))
        after = {
            p: p.read_text(encoding="utf-8")
            for p in sorted(vault.rglob("*")) if p.is_file()
        }
        assert before == after
