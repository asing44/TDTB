"""Route tests for GET /plan-inputs (T16-T2b) — the read-only server-side
assembly of {digest, config, anchored_blocks} the timeline view needs to build
its /sequence, /validate-sequence, and /commit bodies.

The browser can't read the vault, and /config exposes only section *keys* (not
bodies), so this endpoint mirrors build_commit_body.build_body's input
assembly minus the sequence. Tokenless like /config; its only run-state write
is the allocator-rewrite T2 ``digest_index`` cache key — see the route
docstring for why that leaves the token boundary intact. Parity with build_commit_body includes the micro_adventure
run-state side-load (Locked #7) so a selected Live micro-adventure reaches the
eventual /commit reroute.
"""
from __future__ import annotations

import sys
from pathlib import Path
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))
import main as main_mod  # noqa: E402
import runstate as runstate_mod  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent.parent / "gather"))
import tdtb_gather as gather  # noqa: E402

CONFIG_REL_PATH = "00 - META/Skill-Configs/tdtb-bridger.md"

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

| Block           | Type   | Start    | End     | Duration | Days  | overlap_allowed |
| --------------- | ------ | -------- | ------- | -------- | ----- | --------------- |
| Morning Routine | hard   | 7:45 AM  | —       | 80m      | daily | no              |
| Live            | window | 12:00 PM | 8:00 PM | 30m      | daily | yes             |
"""


@pytest.fixture
def vault(tmp_path) -> Path:
    return tmp_path / "vault-root"


@pytest.fixture
def client(vault) -> TestClient:
    vault.mkdir()
    app = main_mod.create_app(vault_root=vault)
    return TestClient(app)


def _write_config(vault: Path) -> None:
    p = vault / CONFIG_REL_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(MINIMAL_CONFIG, encoding="utf-8")


class TestPlanInputsRoute:
    def test_tokenless_returns_shape(self, client):
        r = client.get("/plan-inputs")
        assert r.status_code == 200
        body = r.json()
        assert set(body.keys()) >= {"digest", "config", "anchored_blocks"}
        assert "assigned" in body["digest"]

    def test_bootstrap_vault_empty(self, client):
        # No config file -> config carries only the T19 fallback-seed Live
        # auto-pick (SKILL § 0.7: absent section → inline seed pool);
        # anchored_blocks [], digest still built.
        r = client.get("/plan-inputs")
        body = r.json()
        config = dict(body["config"])
        micro = config.pop("micro_adventure", None)
        assert config == {}
        assert micro is not None and micro["id"].startswith("ma")
        assert body["anchored_blocks"] == []
        assert body["digest"]["assigned_count"] == 0

    def test_config_sections_exposed(self, client, vault):
        _write_config(vault)
        r = client.get("/plan-inputs")
        body = r.json()
        assert "Anchored Lifestyle Blocks" in body["config"]
        assert "Defaults" in body["config"]

    def test_anchored_blocks_from_titlecase_section(self, client, vault):
        _write_config(vault)
        r = client.get("/plan-inputs")
        blocks = r.json()["anchored_blocks"]
        names = {b.get("Block") for b in blocks}
        assert names == {"Morning Routine", "Live"}
        live = next(b for b in blocks if b.get("Block") == "Live")
        assert str(live.get("overlap_allowed")).lower() in ("yes", "true")

    def test_anchored_source_fingerprint_ignores_day_setup_overrides(self, client, vault):
        """Raw config drift stays detectable beneath a same-day override."""
        _write_config(vault)
        before = client.get("/plan-inputs").json()
        token = client.get("/session-token").json()["token"]
        saved = client.post(
            "/day-setup",
            json={"anchored": [{
                "id": "Morning Routine", "on": True, "skip_today": False,
                "time": "08:15", "blocks": 2,
            }]},
            headers={"X-TDTB-Token": token},
        )
        assert saved.status_code == 200

        after = client.get("/plan-inputs").json()
        assert after["anchored_source_fingerprint"] == before["anchored_source_fingerprint"]
        morning = next(
            b for b in after["anchored_blocks"] if b.get("Block") == "Morning Routine"
        )
        assert morning["time"] == "08:15"
        assert morning["Duration"] == 60

    def test_anchored_source_fingerprint_changes_on_raw_config_edit(self, client, vault):
        _write_config(vault)
        before = client.get("/plan-inputs").json()["anchored_source_fingerprint"]
        path = vault / CONFIG_REL_PATH
        path.write_text(
            path.read_text(encoding="utf-8").replace("| 80m      |", "| 90m      |"),
            encoding="utf-8",
        )
        after = client.get("/plan-inputs").json()["anchored_source_fingerprint"]
        assert after != before

    def test_micro_adventure_side_load(self, client, vault):
        # Parity with build_commit_body Locked #7: today's run-state selection
        # is merged into config so the /commit Live->Todoist reroute is reachable.
        today = gather.effective_date(datetime.now())
        micro = {"id": "ma03", "idea": "Cook something new", "category": "food"}
        state = runstate_mod.build_runstate({"micro_adventure": micro})
        runstate_mod.write_runstate(vault, today, state)
        r = client.get("/plan-inputs")
        assert r.json()["config"].get("micro_adventure") == micro


class TestIgnoreList:
    """`## Ignore List` config section drops matching items from the digest —
    vault rows by relative path, name rows case-insensitively, both surfaces
    (T13e). Todoist-ID matching is pinned at the build_digest level in
    test_main_api since this route runs without a Todoist client."""

    CONFIG_WITH_IGNORES = MINIMAL_CONFIG + """
## Ignore List

### Obsidian (by path)

| Path | Notes |
|------|-------|
| 50 - Operations/Tasks/By Path.md | |

### Names

| Name | Notes |
|------|-------|
| M1.0 | |
"""

    def _write(self, vault: Path, name: str) -> None:
        note = vault / "50 - Operations" / "Tasks" / f"{name}.md"
        note.parent.mkdir(parents=True, exist_ok=True)
        note.write_text(
            f"---\nassigned: true\ntype: [task]\nstatus: in-progress\n---\n{name}\n",
            encoding="utf-8",
        )

    def _write_cfg(self, vault: Path) -> None:
        p = vault / CONFIG_REL_PATH
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(self.CONFIG_WITH_IGNORES, encoding="utf-8")

    def test_ignored_name_and_path_dropped_from_digest(self, client, vault):
        self._write_cfg(vault)
        self._write(vault, "M1.0")
        self._write(vault, "By Path")
        self._write(vault, "Keep Me")
        body = client.get("/plan-inputs").json()
        names = [i["name"] for i in body["digest"]["assigned"]]
        assert names == ["Keep Me"]

    def test_name_ignore_is_case_insensitive(self, client, vault):
        self._write_cfg(vault)
        self._write(vault, "m1.0")
        body = client.get("/plan-inputs").json()
        assert [i["name"] for i in body["digest"]["assigned"]] == []

    def test_no_ignore_section_keeps_everything(self, client, vault):
        _write_config(vault)
        self._write(vault, "M1.0")
        body = client.get("/plan-inputs").json()
        assert "M1.0" in [i["name"] for i in body["digest"]["assigned"]]
