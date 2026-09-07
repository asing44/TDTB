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

    def test_micro_adventure_singular_and_refresh_stable(self, client, vault):
        """P3-03: the selected Live/micro-adventure identity is singular,
        canonical, and stable across repeated /plan-inputs reads. A same-day
        refresh must not produce a second Live selection or swap the pick."""
        today = gather.effective_date(datetime.now())
        micro = {"id": "ma03", "idea": "Ride bike somewhere", "category": "novelty"}
        state = runstate_mod.build_runstate({"micro_adventure": micro})
        runstate_mod.write_runstate(vault, today, state)

        first = client.get("/plan-inputs")
        assert first.status_code == 200
        body1 = first.json()
        second = client.get("/plan-inputs")
        assert second.status_code == 200
        body2 = second.json()

        for body in (body1, body2):
            # singular: exactly one micro_adventure key in config, one pick
            ma_keys = [k for k in body["config"] if "micro" in k.lower()
                       or k.lower() == "live"]
            assert ma_keys == ["micro_adventure"]
            payload = body["micro_adventure"]
            assert payload["pick"] == micro
            assert payload["source"] == "override"
            # no duplicate Live identity within the payload
            ids = [p["id"] for p in payload["live_pool"]]
            assert len(ids) == len(set(ids))
            assert ids.count(micro["id"]) <= 1
            if isinstance(payload.get("pending_confirm"), dict):
                assert payload["pending_confirm"]["id"] == micro["id"]

        # refresh-stable: identical across repeated reads
        assert body1["config"]["micro_adventure"] == body2["config"]["micro_adventure"]
        assert body1["micro_adventure"] == body2["micro_adventure"]


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


# ---------------------------------------------------------------------------
# Issue #6 — route-level calendar decisions: disabled calendars, unknown
# calendars, and single-calendar canonicalization of missing calendar IDs.
# Fakes only: no live source calls; the fake store ignores query dates so the
# tests are deterministic for the route's effective today.
# ---------------------------------------------------------------------------

from calendar_bridge import CalendarInfo  # noqa: E402


CONFIG_DISABLED_CALENDARS = MINIMAL_CONFIG + """
## Disabled Calendars

| Title    |
| -------- |
| Personal |
"""


class _FakeTodoistEmpty:
    def get_filter_tasks(self, query, limit=None):
        return []


class _DecisionsStore:
    """Fake EventStore exposing an inventory; ignores queried dates."""

    def __init__(self, events, calendars):
        self._events = events
        self._calendars = calendars

    def auth_status(self):
        return "authorized"

    def calendars(self):
        return self._calendars

    def query_events(self, start, end, calendar_ids=None):
        return self._events


def _decisions_client(vault: Path, store) -> TestClient:
    p = vault / CONFIG_REL_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(CONFIG_DISABLED_CALENDARS, encoding="utf-8")
    app = main_mod.create_app(vault_root=vault)
    app.state.build_read_clients = lambda v, cfg: (_FakeTodoistEmpty(), store)
    return TestClient(app)


class TestPlanInputsCalendarDecisions:
    def test_disabled_unknown_and_known_calendars(self, vault):
        store = _DecisionsStore(
            [
                {"title": "Yoga", "start": datetime(2026, 7, 14, 9, 0),
                 "end": datetime(2026, 7, 14, 10, 0), "calendar_id": "CAL-PERS"},
                {"title": "Dentist", "start": datetime(2026, 7, 14, 11, 0),
                 "end": datetime(2026, 7, 14, 12, 0), "calendar_id": "CAL-SCHOOL"},
                {"title": "Mystery", "start": datetime(2026, 7, 14, 13, 0),
                 "end": datetime(2026, 7, 14, 14, 0), "calendar_id": "CAL-GHOST"},
            ],
            [
                CalendarInfo("Personal", "CAL-PERS", False, "Local"),
                CalendarInfo("School", "CAL-SCHOOL", False, "Local"),
            ],
        )
        body = _decisions_client(vault, store).get("/plan-inputs").json()

        # Disabled and unknown events must not become anchored blocks.
        calendar_blocks = [
            b for b in body["anchored_blocks"] if b.get("source") == "calendar"
        ]
        assert [b["Block"] for b in calendar_blocks] == ["Dentist"]

        decisions = {d["title"]: d for d in body["calendar_decisions"]}

        # Disabled calendar event: excluded with reason code.
        assert decisions["Yoga"]["decision"] == "excluded"
        assert decisions["Yoga"]["reason_code"] == "excluded_disabled_calendar"

        # Unknown calendar event: unresolved and warned with title/ID.
        mystery = decisions["Mystery"]
        assert mystery["decision"] == "unresolved"
        assert mystery["reason_code"] == "unresolved_unknown_calendar"
        warnings = " ".join(body["source_warnings"])
        assert "Mystery" in warnings
        assert "CAL-GHOST" in warnings

        # Known enabled calendar event remains included.
        dentist = decisions["Dentist"]
        assert dentist["decision"] == "included"
        assert dentist["reason_code"] == "included_timed"
        assert dentist["all_day"] is False

    def test_missing_calendar_id_single_calendar_fallback(self, vault):
        store = _DecisionsStore(
            [
                {"title": "Errand A", "start": datetime(2026, 7, 14, 9, 0),
                 "end": datetime(2026, 7, 14, 9, 30), "calendar_id": ""},
                {"title": "Errand B", "start": datetime(2026, 7, 14, 10, 0),
                 "end": datetime(2026, 7, 14, 10, 30)},
            ],
            [CalendarInfo("Only", "CAL-ONE", False, "Local")],
        )
        body = _decisions_client(vault, store).get("/plan-inputs").json()

        decisions = {d["title"]: d for d in body["calendar_decisions"]}
        for title in ("Errand A", "Errand B"):
            row = decisions[title]
            assert row["source_calendar_id"] is None
            assert row["calendar_id"] == "CAL-ONE"
            assert row["calendar_title"] == "Only"


# ---------------------------------------------------------------------------
# P3-03 — dated Drop-from-plan exclusions persist across same-day refreshes
# ---------------------------------------------------------------------------

class TestDropFromPlanRefresh:
    """A dropped item must remain absent from the digest and from the
    persisted identity index, and a later /plan-inputs refresh on the same
    day must not resurrect it (drop identity is date-scoped run state, and
    digest_index is written from the FILTERED digest)."""

    def test_dropped_item_absent_from_digest_and_index_across_refresh(
        self, client, vault
    ):
        note = vault / "50 - Operations" / "Projects" / "Press.md"
        note.parent.mkdir(parents=True, exist_ok=True)
        note.write_text(
            "---\ntype: [project]\nstatus: active\nassigned: true\n---\n\n# Press\n",
            encoding="utf-8",
        )

        first = client.get("/plan-inputs").json()
        row = next(r for r in first["digest"]["assigned"] if r["name"] == "Press")
        identity = main_mod.runtime_actions.drop_identity_of(row)
        assert identity

        # Drop it for today (date-scoped runstate exclusion, as /drop writes it).
        today = gather.effective_date(datetime.now())
        runstate_mod.update_runstate(
            vault, today,
            lambda s: s.setdefault("dropped", []).append(
                {"identity": identity, "name": "Press",
                 "dropped_at": "2026-09-05T00:00:00Z"}
            ),
        )

        # Two consecutive refreshes on the same day.
        body1 = client.get("/plan-inputs").json()
        body2 = client.get("/plan-inputs").json()

        for body in (body1, body2):
            names = [r["name"] for r in body["digest"]["assigned"]]
            assert "Press" not in names
            sugg = [r["name"] for r in body["digest"].get("suggested", [])]
            assert "Press" not in sugg
            # dropped_today still surfaces the exclusion
            assert any(d.get("identity") == identity
                       for d in body.get("dropped_today", []))

        # identity index is built from the FILTERED digest — the dropped
        # identity must not be resolvable to staging verbs after refresh.
        index = runstate_mod.read_digest_index(vault, today)
        index_identities = {
            i.get("path") or f"todoist:{i.get('todoist_id')}"
            for i in index if isinstance(i, dict)
        }
        assert identity not in index_identities
        assert all(i.get("name") != "Press" for i in index)
