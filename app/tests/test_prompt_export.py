"""U5 B2 — the Commit-only prompt export lane (``prompt_export.py``).

Unit coverage pins authorization and privacy:

- only ``megan_nicety``/``stoic_intention`` ever export; ``intention`` never;
- an opt-out or a blank draft produces no task;
- authorization is read from server opt-ins + local logical-day drafts, never
  from a request body;
- the due is the CIVIL calendar date;
- an already-``done`` receipt skips; a provider error never auto-retries; a
  corrupt receipt store blocks without creating and preserves bytes;
- no prompt text reaches a receipt, an outcome, an error, or a response.

Route coverage (``TestPromptExportRoute``) drives the real ``POST /commit``
path with a fake injected client after the established token + Day Setup
confirmation, and asserts the shadow preview writes no receipt and calls no
provider. Fixture-only: the autouse ``_isolated_app_home`` fixture puts
``TDTB_HOME`` in a per-test tmp dir.
"""
from __future__ import annotations

import sys
from datetime import date, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))
import export_receipts as er  # noqa: E402
import main as main_mod  # noqa: E402
import prompt_export as pe  # noqa: E402
import prompt_state as ps  # noqa: E402
import runstate as rs  # noqa: E402
import shadow  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent.parent / "gather"))
import tdtb_gather as gather  # noqa: E402

DAY = "2026-07-12"
SECRET = "SYNTHETIC-SECRET-7c1f-nicety"


class RecordingTodoist:
    """In-memory Todoist that records every create (content + due_date)."""

    def __init__(self, *, raise_on_create: bool = False):
        self.tasks: dict[str, dict] = {}
        self.created: list[dict] = []
        self._seq = 1000
        self.raise_on_create = raise_on_create

    def get_filter_tasks(self, filter_id_or_query, limit=None):
        return list(self.tasks.values())

    def get_task(self, task_id):
        return self.tasks[task_id]

    def create_task(self, content, project_id=None, due_string=None,
                    due_date=None, duration=None, duration_unit=None, **_):
        if self.raise_on_create:
            raise RuntimeError("provider boom")
        self._seq += 1
        tid = f"t{self._seq}"
        due = None
        if due_date is not None:
            due = {"date": due_date}
        elif due_string and "at " in due_string:
            hhmm = due_string.split("at ", 1)[1].strip()
            due = {"date": f"{DAY}T{hhmm}:00"}
        self.tasks[tid] = {"id": tid, "content": content, "due": due,
                           "project_id": project_id}
        self.created.append({"content": content, "project_id": project_id,
                             "due_date": due_date})
        return self.tasks[tid]

    def reschedule_task(self, task_id, due_string):
        hhmm = due_string.split("at ", 1)[1].strip() if "at " in due_string else None
        if hhmm:
            self.tasks[task_id]["due"] = {"date": f"{DAY}T{hhmm}:00"}
        return self.tasks[task_id]


class NoCreateTodoist:
    """A client missing ``create_task`` — the export lane must fail content-free."""

    def __init__(self, tasks=None):
        self.tasks = {t["id"]: t for t in (tasks or [])}

    def get_filter_tasks(self, filter_id_or_query, limit=None):
        return list(self.tasks.values())

    def get_task(self, task_id):
        return self.tasks[task_id]

    def reschedule_task(self, task_id, due_string):
        return self.tasks[task_id]


# ---------------------------------------------------------------------------
# Planning (pure)
# ---------------------------------------------------------------------------

class TestPlan:
    def test_only_opted_in_nonblank_drafts_plan(self):
        plans = pe.plan_prompt_exports(
            day=DAY, civil_date="2026-07-13",
            optins={"intention": True, "megan_nicety": True,
                    "stoic_intention": False},
            drafts={"intention": "private intent", "megan_nicety": "  hello  ",
                    "stoic_intention": "not opted in"},
        )
        assert [p.prompt_key for p in plans] == ["megan_nicety"]
        assert plans[0].content == "hello"  # stripped, in-memory only

    def test_intention_is_never_planned(self):
        plans = pe.plan_prompt_exports(
            day=DAY, civil_date="2026-07-13",
            optins={"intention": True}, drafts={"intention": "private intent"},
        )
        assert plans == []

    @pytest.mark.parametrize("drafts", [
        {"megan_nicety": ""}, {"megan_nicety": "   "}, {"megan_nicety": None},
        {"megan_nicety": 5}, {},
    ])
    def test_blank_or_nonstring_draft_never_plans(self, drafts):
        plans = pe.plan_prompt_exports(
            day=DAY, civil_date="2026-07-13",
            optins={"megan_nicety": True}, drafts=drafts,
        )
        assert plans == []

    def test_preview_is_content_free(self):
        preview = pe.preview_prompt_exports(
            day=DAY, civil_date="2026-07-13",
            optins={"megan_nicety": True}, drafts={"megan_nicety": SECRET},
        )
        assert preview == [{"prompt_key": "megan_nicety",
                            "action": pe.EXPORT_ACTION, "status": "planned"}]
        assert SECRET not in repr(preview)


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------

class TestRun:
    def test_creates_with_civil_due_and_inbox_routing(self):
        client = RecordingTodoist()
        outcomes = pe.run_prompt_exports(
            day=DAY, civil_date="2026-07-13", todoist=client,
            optins={"megan_nicety": True}, drafts={"megan_nicety": SECRET},
        )
        assert [o.status for o in outcomes] == [pe.OUTCOME_DONE]
        assert outcomes[0].task_id
        [created] = client.created
        assert created["content"] == SECRET
        assert created["due_date"] == "2026-07-13"  # civil date, not DAY
        assert created["project_id"] is None  # Inbox continuity default

    def test_optout_or_blank_creates_nothing(self):
        client = RecordingTodoist()
        assert pe.run_prompt_exports(
            day=DAY, civil_date="2026-07-13", todoist=client,
            optins={"megan_nicety": False}, drafts={"megan_nicety": SECRET},
        ) == []
        assert pe.run_prompt_exports(
            day=DAY, civil_date="2026-07-13", todoist=client,
            optins={"megan_nicety": True}, drafts={"megan_nicety": "  "},
        ) == []
        assert client.created == []

    def test_reads_server_state_when_not_injected(self):
        ps.save_optins(expected_revision=0, optins={"megan_nicety": True})
        ps.save_drafts(day=DAY, patch={"megan_nicety": SECRET})
        client = RecordingTodoist()
        outcomes = pe.run_prompt_exports(
            day=DAY, civil_date="2026-07-13", todoist=client,
        )
        assert [o.status for o in outcomes] == [pe.OUTCOME_DONE]
        assert client.created[0]["content"] == SECRET

    def test_duplicate_done_skips_second_attempt(self):
        client = RecordingTodoist()
        first = pe.run_prompt_exports(
            day=DAY, civil_date="2026-07-13", todoist=client,
            optins={"megan_nicety": True}, drafts={"megan_nicety": SECRET},
        )
        second = pe.run_prompt_exports(
            day=DAY, civil_date="2026-07-13", todoist=client,
            optins={"megan_nicety": True}, drafts={"megan_nicety": SECRET},
        )
        assert first[0].status == pe.OUTCOME_DONE
        assert second[0].status == pe.OUTCOME_DONE
        assert len(client.created) == 1  # no duplicate task

    def test_provider_error_never_auto_retries(self):
        client = RecordingTodoist(raise_on_create=True)
        first = pe.run_prompt_exports(
            day=DAY, civil_date="2026-07-13", todoist=client,
            optins={"megan_nicety": True}, drafts={"megan_nicety": SECRET},
        )
        assert first[0].status == pe.OUTCOME_NEEDS_REVIEW
        assert er.load_receipts().get(DAY, "megan_nicety",
                                      pe.EXPORT_ACTION).status == er.STATUS_NEEDS_REVIEW
        # A second run strands as needs_review and never touches the provider.
        client.raise_on_create = False
        second = pe.run_prompt_exports(
            day=DAY, civil_date="2026-07-13", todoist=client,
            optins={"megan_nicety": True}, drafts={"megan_nicety": SECRET},
        )
        assert second[0].status == pe.OUTCOME_NEEDS_REVIEW
        assert client.created == []

    def test_corrupt_receipt_store_blocks_without_creating(self):
        target = er.receipts_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        raw = '{"version": 1, "receipts": {"x": {}, "x": {}}}'
        target.write_text(raw, encoding="utf-8")
        client = RecordingTodoist()
        outcomes = pe.run_prompt_exports(
            day=DAY, civil_date="2026-07-13", todoist=client,
            optins={"megan_nicety": True}, drafts={"megan_nicety": SECRET},
        )
        assert [o.status for o in outcomes] == [pe.OUTCOME_BLOCKED]
        assert client.created == []
        assert target.read_text(encoding="utf-8") == raw

    def test_missing_client_blocks_planned_export(self):
        outcomes = pe.run_prompt_exports(
            day=DAY, civil_date="2026-07-13", todoist=None,
            optins={"megan_nicety": True}, drafts={"megan_nicety": SECRET},
        )
        assert [o.status for o in outcomes] == [pe.OUTCOME_BLOCKED]

    def test_client_without_create_task_fails_content_free(self):
        outcomes = pe.run_prompt_exports(
            day=DAY, civil_date="2026-07-13", todoist=NoCreateTodoist(),
            optins={"megan_nicety": True}, drafts={"megan_nicety": SECRET},
        )
        assert [o.status for o in outcomes] == [pe.OUTCOME_NEEDS_REVIEW]
        assert SECRET not in repr(outcomes[0])

    def test_no_prompt_text_reaches_receipt_or_outcome(self):
        client = RecordingTodoist()
        outcomes = pe.run_prompt_exports(
            day=DAY, civil_date="2026-07-13", todoist=client,
            optins={"megan_nicety": True, "stoic_intention": True},
            drafts={"megan_nicety": SECRET, "stoic_intention": SECRET + "-2"},
        )
        assert SECRET not in er.receipts_path().read_text(encoding="utf-8")
        assert SECRET not in repr([o.as_dict() for o in outcomes])

    def test_attach_to_report_surfaces_statuses_without_masking_ok(self):
        report = {"ok": True, "surfaces": {}}
        pe.attach_to_report(report, [
            pe.PromptExportOutcome(DAY, "megan_nicety", pe.EXPORT_ACTION,
                                   pe.OUTCOME_NEEDS_REVIEW, reason="review"),
        ])
        assert report["ok"] is True  # four-surface verdict unchanged
        assert report["prompt_exports_ok"] is False
        assert report["prompt_exports"][0]["status"] == pe.OUTCOME_NEEDS_REVIEW
        assert "content" not in report["prompt_exports"][0]


# ---------------------------------------------------------------------------
# Route level — real POST /commit with a fake injected client
# ---------------------------------------------------------------------------

@pytest.fixture
def vault(tmp_path) -> Path:
    v = tmp_path / "vault-root"
    (v / "P").mkdir(parents=True)
    (v / "P/Garage.md").write_text("---\nassigned: false\n---\nbody\n",
                                   encoding="utf-8")
    (v / "30 - Daily").mkdir(parents=True)
    (v / "30 - Daily" / f"{DAY}.md").write_text("# Journal\n", encoding="utf-8")
    cfg = v / "00 - META" / "Skill-Configs" / "tdtb-bridger.md"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(
        "## Defaults\n| Key | Value |\n|---|---|\n| eod | 11:59 PM |\n\n"
        "## Template Blocks\n### Trinoor Hours\n"
        "| Slot | Start | End |\n|---|---|---|\n"
        "| Morning | 12:00 AM | 11:59 PM |\n",
        encoding="utf-8",
    )
    rs.write_digest_index(v, date.fromisoformat(DAY),
                          [{"name": "Garage", "todoist_id": "", "path": "P/Garage.md",
                            "surface": "assigned"}])
    return v


@pytest.fixture
def client(vault) -> TestClient:
    app = main_mod.create_app(vault_root=vault)
    c = TestClient(app)
    c.app_token = app.state.token
    return c


def _auth(client: TestClient) -> dict:
    return {"X-TDTB-Token": client.app_token}


def _freeze(monkeypatch) -> None:
    monkeypatch.setattr(gather, "effective_date", lambda now: date.fromisoformat(DAY))


def _live_state(garage_live: bool):
    def _state(config, vault_root):
        tasks = []
        if garage_live:
            tasks = [{"id": "T1", "content": "Garage",
                      "due": {"datetime": f"{DAY}T09:00:00"}}]
        return {"todoist_tasks": tasks, "calendar_events": [],
                "vault_frontmatter": {"P/Garage.md": {"assigned": False}},
                "daily_note_text": "# Journal\n"}
    return _state


DIGEST = {"assigned": [{"name": "Garage", "path": "P/Garage.md"}], "suggested": []}
SEQUENCE = {"sequence": [{"id": "Garage", "start": "09:00", "end": "10:00",
                          "zone": "any"}]}


class TestPromptExportRoute:
    def _confirm(self, client) -> None:
        assert client.post("/day-setup", json={"anchor": "09:00"},
                           headers=_auth(client)).status_code == 200

    def test_live_commit_exports_after_day_confirmation(self, client, vault, monkeypatch):
        _freeze(monkeypatch)
        monkeypatch.setattr(shadow, "gather_live_state", _live_state(garage_live=False))
        ps.save_optins(expected_revision=0, optins={"megan_nicety": True})
        ps.save_drafts(day=DAY, patch={"megan_nicety": SECRET})
        todoist = RecordingTodoist()
        client.app.state.build_commit_clients = lambda v, cfg: (todoist, None)
        self._confirm(client)

        r = client.post("/commit?mode=live", headers=_auth(client),
                        json={"digest": DIGEST, "sequence": SEQUENCE, "config": {}})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["prompt_exports_ok"] is True
        assert body["prompt_exports"] == [{
            "prompt_key": "megan_nicety", "action": pe.EXPORT_ACTION,
            "status": pe.OUTCOME_DONE, "task_id": body["prompt_exports"][0]["task_id"],
            "reason": None,
        }]
        export_tasks = [c for c in todoist.created if c["content"] == SECRET]
        assert len(export_tasks) == 1
        assert export_tasks[0]["due_date"] == datetime.now().date().isoformat()
        assert export_tasks[0]["project_id"] is None
        # Content-free response + durable done receipt.
        assert SECRET not in r.text
        receipt = er.load_receipts().get(DAY, "megan_nicety", pe.EXPORT_ACTION)
        assert receipt.status == er.STATUS_DONE

    def test_repeated_live_commit_does_not_duplicate(self, client, vault, monkeypatch):
        _freeze(monkeypatch)
        monkeypatch.setattr(shadow, "gather_live_state", _live_state(garage_live=False))
        ps.save_optins(expected_revision=0, optins={"megan_nicety": True})
        ps.save_drafts(day=DAY, patch={"megan_nicety": SECRET})
        todoist = RecordingTodoist()
        client.app.state.build_commit_clients = lambda v, cfg: (todoist, None)
        self._confirm(client)
        payload = {"digest": DIGEST, "sequence": SEQUENCE, "config": {}}
        assert client.post("/commit?mode=live", headers=_auth(client),
                           json=payload).status_code == 200
        second = client.post("/commit?mode=live", headers=_auth(client), json=payload)
        assert second.status_code == 200
        assert second.json()["prompt_exports_ok"] is True
        assert len([c for c in todoist.created if c["content"] == SECRET]) == 1

    def test_browser_captures_do_not_authorize_export(self, client, vault, monkeypatch):
        _freeze(monkeypatch)
        monkeypatch.setattr(shadow, "gather_live_state", _live_state(garage_live=False))
        todoist = RecordingTodoist()
        client.app.state.build_commit_clients = lambda v, cfg: (todoist, None)
        self._confirm(client)
        spoof = "BROWSER-SPOOF-NICETY"
        r = client.post("/commit?mode=live", headers=_auth(client), json={
            "digest": DIGEST, "sequence": SEQUENCE,
            "config": {"captures": {"megan_nicety": spoof}},
            "captures": {"megan_nicety": spoof},
        })
        assert r.status_code == 200, r.text
        assert r.json()["prompt_exports"] == []
        assert all(c["content"] != spoof for c in todoist.created)
        assert spoof not in r.text
        assert not er.receipts_path().exists()

    def test_shadow_preview_is_content_free_and_writes_nothing(self, client, vault, monkeypatch):
        _freeze(monkeypatch)
        monkeypatch.setattr(shadow, "gather_live_state", _live_state(garage_live=False))
        ps.save_optins(expected_revision=0, optins={"megan_nicety": True})
        ps.save_drafts(day=DAY, patch={"megan_nicety": SECRET})

        def _boom(v, cfg):
            raise AssertionError("shadow must not build a provider client")

        client.app.state.build_commit_clients = _boom
        r = client.post("/commit?mode=shadow", headers=_auth(client),
                        json={"digest": DIGEST, "sequence": SEQUENCE, "config": {}})
        assert r.status_code == 200, r.text
        assert r.json()["prompt_exports"] == [{
            "prompt_key": "megan_nicety", "action": pe.EXPORT_ACTION,
            "status": "planned",
        }]
        assert SECRET not in r.text
        assert not er.receipts_path().exists()

    def test_missing_create_task_client_fails_content_free_no_traceback(
            self, client, vault, monkeypatch):
        _freeze(monkeypatch)
        # A live Garage task makes Step A an UPDATE, so the export lane is the
        # only caller that needs create_task.
        monkeypatch.setattr(shadow, "gather_live_state", _live_state(garage_live=True))
        ps.save_optins(expected_revision=0, optins={"megan_nicety": True})
        ps.save_drafts(day=DAY, patch={"megan_nicety": SECRET})
        client.app.state.build_commit_clients = (
            lambda v, cfg: (NoCreateTodoist([{"id": "T1", "content": "Garage",
                                              "due": {"datetime": f"{DAY}T09:00:00"}}]),
                            None)
        )
        self._confirm(client)
        r = client.post("/commit?mode=live", headers=_auth(client),
                        json={"digest": DIGEST, "sequence": SEQUENCE, "config": {}})
        assert r.status_code == 200, r.text  # no traceback/500
        body = r.json()
        assert body["ok"] is True  # four surfaces landed
        assert body["prompt_exports_ok"] is False
        assert body["prompt_exports"][0]["status"] == pe.OUTCOME_NEEDS_REVIEW
        assert SECRET not in r.text
