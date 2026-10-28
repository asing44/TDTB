"""U5 B2 / operator 2026-10-14 — the Commit-only prompt export lane
(``prompt_export.py``).

Unit coverage pins the new authorization and privacy contract:

- EVERY non-empty server draft exports: Intention, For Meegy, Stoic, and
  browser-defined custom prompts (``custom-<id>`` draft keys);
- the legacy per-prompt opt-in store is NO LONGER consulted (values ignored);
- a blank/absent/non-string draft produces no task;
- authorization is read from server drafts (the logical-day draft store),
  never from a request body;
- the due is the CIVIL calendar date and routing is the project_id-less Inbox;
- an already-``done`` receipt skips (idempotent, including custom keys); a
  provider error never auto-retries; a corrupt receipt store blocks without
  creating and preserves bytes;
- no prompt text reaches a receipt, an outcome, an error, or a response.

Route coverage (``TestPromptExportRoute``) drives the real ``POST /commit``
path with a fake injected client after the established token + Day Setup
confirmation, and asserts the shadow preview writes no receipt and calls no
provider. It also pins that the legacy opt-in GET/PUT routes still respond OK
while their values are ignored. Fixture-only: the autouse
``_isolated_app_home`` fixture puts ``TDTB_HOME`` in a per-test tmp dir.
"""
from __future__ import annotations

import sys
from datetime import date, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))
import capacities_cache_io  # noqa: E402
import export_receipts as er  # noqa: E402
import main as main_mod  # noqa: E402
import prompt_export as pe  # noqa: E402
import prompt_state as ps  # noqa: E402
import runstate as rs  # noqa: E402
import shadow  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent.parent / "gather"))
import tdtb_gather as gather  # noqa: E402
from tests.config_markdown import write_config_note  # noqa: E402

DAY = "2026-07-12"
CIVIL = "2026-07-13"
SECRET = "SYNTHETIC-SECRET-7c1f-nicety"
INTENTION = "SYNTHETIC-INTENTION-1a2b"
STOIC = "SYNTHETIC-STOIC-3c4d"
# A browser-defined custom prompt id shaped exactly like
# ``customPrompts.newCustomPromptId`` (``custom-<base36>-<base36>``).
CUSTOM_KEY = "custom-m1abcde-xy9"
CUSTOM_ANSWER = "SYNTHETIC-CUSTOM-ANSWER-9f0e"


class RecordingTodoist:
    """In-memory Todoist that records every create (content/description/due)."""

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
                    due_date=None, duration=None, duration_unit=None,
                    description=None, **_):
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
                           "project_id": project_id,
                           "description": description}
        self.created.append({"content": content, "project_id": project_id,
                             "due_date": due_date, "description": description})
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


class NoIdTodoist(RecordingTodoist):
    """A provider that never returns a task id, so the main todoist surface
    fails reconciliation without raising — the main report is not ok."""

    def create_task(self, content, project_id=None, due_string=None,
                    due_date=None, duration=None, duration_unit=None, **_):
        self.created.append({"content": content, "project_id": project_id,
                             "due_date": due_date})
        return {}


# ---------------------------------------------------------------------------
# Planning (pure)
# ---------------------------------------------------------------------------

class TestPlan:
    def test_every_non_empty_prompt_plans_in_canonical_order(self):
        plans = pe.plan_prompt_exports(
            day=DAY, civil_date=CIVIL,
            drafts={"intention": "private intent", "megan_nicety": "  hello  ",
                    "stoic_intention": "stoic text", CUSTOM_KEY: CUSTOM_ANSWER},
        )
        assert [p.prompt_key for p in plans] == [
            "intention", "megan_nicety", "stoic_intention", CUSTOM_KEY,
        ]
        assert plans[1].content == "hello"  # stripped, in-memory only

    def test_intention_plans_when_non_empty(self):
        plans = pe.plan_prompt_exports(
            day=DAY, civil_date=CIVIL, drafts={"intention": "private intent"},
        )
        assert [p.prompt_key for p in plans] == ["intention"]
        assert plans[0].content == "private intent"

    def test_custom_prompt_plans_from_its_draft_key(self):
        plans = pe.plan_prompt_exports(
            day=DAY, civil_date=CIVIL, drafts={CUSTOM_KEY: "  custom answer  "},
        )
        assert [p.prompt_key for p in plans] == [CUSTOM_KEY]
        assert plans[0].content == "custom answer"
        assert plans[0].action == pe.EXPORT_ACTION

    @pytest.mark.parametrize("drafts", [
        {"intention": ""}, {"intention": "   "}, {"intention": None},
        {"intention": 5}, {CUSTOM_KEY: ""}, {"megan_nicety": ""}, {},
    ])
    def test_blank_or_nonstring_draft_never_plans(self, drafts):
        assert pe.plan_prompt_exports(day=DAY, civil_date=CIVIL, drafts=drafts) == []

    def test_unrecognized_keys_are_ignored(self):
        plans = pe.plan_prompt_exports(
            day=DAY, civil_date=CIVIL,
            drafts={"unknown_prompt": "x", "customprompt": "y",
                    "custom_underscore": "z"},
        )
        assert plans == []

    def test_preview_is_content_free(self):
        preview = pe.preview_prompt_exports(
            day=DAY, civil_date=CIVIL,
            drafts={"intention": INTENTION, "megan_nicety": SECRET,
                    CUSTOM_KEY: CUSTOM_ANSWER},
        )
        assert preview == [
            {"prompt_key": "intention", "action": pe.EXPORT_ACTION,
             "status": "planned"},
            {"prompt_key": "megan_nicety", "action": pe.EXPORT_ACTION,
             "status": "planned"},
            {"prompt_key": CUSTOM_KEY, "action": pe.EXPORT_ACTION,
             "status": "planned"},
        ]
        assert SECRET not in repr(preview)
        assert INTENTION not in repr(preview)
        assert CUSTOM_ANSWER not in repr(preview)

    def test_builtin_prompts_plan_fixed_label_titles_and_text_descriptions(self):
        plans = pe.plan_prompt_exports(
            day=DAY, civil_date=CIVIL,
            drafts={"intention": INTENTION, "megan_nicety": SECRET,
                    "stoic_intention": STOIC},
        )
        assert [(p.prompt_key, p.title, p.description) for p in plans] == [
            ("intention", "Reminder", INTENTION),
            ("megan_nicety", "For Meegy", SECRET),
            ("stoic_intention", "Stoic", STOIC),
        ]
        # The private text stays on ``content`` (in memory only).
        assert [p.content for p in plans] == [INTENTION, SECRET, STOIC]
        # The label map is the pinned product contract.
        assert pe.BUILTIN_PROMPT_TITLES == {
            "intention": "Reminder",
            "megan_nicety": "For Meegy",
            "stoic_intention": "Stoic",
        }

    def test_custom_prompt_plans_text_as_title_without_a_description(self):
        plans = pe.plan_prompt_exports(
            day=DAY, civil_date=CIVIL, drafts={CUSTOM_KEY: "  custom answer  "},
        )
        [plan] = plans
        assert plan.title == "custom answer"
        assert plan.description is None
        assert plan.content == "custom answer"

    def test_skip_flag_drops_only_the_description(self):
        plans = pe.plan_prompt_exports(
            day=DAY, civil_date=CIVIL,
            drafts={"megan_nicety": SECRET, "stoic_intention": STOIC},
            description_skips={"megan_nicety": True, "stoic_intention": False},
        )
        assert [(p.prompt_key, p.title, p.description) for p in plans] == [
            ("megan_nicety", "For Meegy", None),  # still planned, no body
            ("stoic_intention", "Stoic", STOIC),  # explicit False writes it
        ]
        # Absent/omitted/non-boolean skip values default to writing.
        for skips in (None, {}, {"megan_nicety": "yes"}, {"megan_nicety": 1}):
            [plan] = pe.plan_prompt_exports(
                day=DAY, civil_date=CIVIL, drafts={"megan_nicety": SECRET},
                description_skips=skips,
            )
            assert plan.description == SECRET

    def test_skip_flag_never_applies_to_custom_prompts(self):
        [plan] = pe.plan_prompt_exports(
            day=DAY, civil_date=CIVIL, drafts={CUSTOM_KEY: CUSTOM_ANSWER},
            description_skips={CUSTOM_KEY: True},
        )
        assert plan.title == CUSTOM_ANSWER
        assert plan.description is None

    def test_preview_shape_is_unchanged_by_titles_and_descriptions(self):
        preview = pe.preview_prompt_exports(
            day=DAY, civil_date=CIVIL,
            drafts={"intention": INTENTION, "megan_nicety": SECRET,
                    CUSTOM_KEY: CUSTOM_ANSWER},
        )
        for entry in preview:
            assert set(entry) == {"prompt_key", "action", "status"}
        assert "Reminder" not in repr(preview)
        assert "For Meegy" not in repr(preview)


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------

class TestRun:
    def test_creates_with_civil_due_and_inbox_routing(self):
        client = RecordingTodoist()
        outcomes = pe.run_prompt_exports(
            day=DAY, civil_date=CIVIL, todoist=client,
            drafts={"megan_nicety": SECRET},
        )
        assert [o.status for o in outcomes] == [pe.OUTCOME_DONE]
        assert outcomes[0].task_id
        [created] = client.created
        assert created["content"] == "For Meegy"  # fixed label title
        assert created["description"] == SECRET  # prompt text is the body
        assert created["due_date"] == CIVIL  # civil date, not DAY
        assert created["project_id"] is None  # Inbox continuity default

    def test_intention_and_custom_prompts_export_to_inbox(self):
        client = RecordingTodoist()
        outcomes = pe.run_prompt_exports(
            day=DAY, civil_date=CIVIL, todoist=client,
            drafts={"intention": INTENTION, CUSTOM_KEY: CUSTOM_ANSWER},
        )
        assert [o.prompt_key for o in outcomes] == ["intention", CUSTOM_KEY]
        assert all(o.status == pe.OUTCOME_DONE for o in outcomes)
        by_content = {c["content"]: c for c in client.created}
        assert by_content["Reminder"]["project_id"] is None
        assert by_content["Reminder"]["due_date"] == CIVIL
        assert by_content["Reminder"]["description"] == INTENTION
        assert by_content[CUSTOM_ANSWER]["project_id"] is None
        assert by_content[CUSTOM_ANSWER]["due_date"] == CIVIL

    def test_blank_drafts_export_nothing(self):
        client = RecordingTodoist()
        for drafts in ({"intention": ""}, {"megan_nicety": "   "},
                       {CUSTOM_KEY: ""}, {"stoic_intention": None}, {}):
            assert pe.run_prompt_exports(
                day=DAY, civil_date=CIVIL, todoist=client, drafts=drafts,
            ) == []
        assert client.created == []

    def test_reads_server_state_when_not_injected(self):
        ps.save_drafts(day=DAY, patch={
            "intention": INTENTION, "megan_nicety": SECRET, CUSTOM_KEY: CUSTOM_ANSWER,
        })
        client = RecordingTodoist()
        outcomes = pe.run_prompt_exports(day=DAY, civil_date=CIVIL, todoist=client)
        assert [o.prompt_key for o in outcomes] == [
            "intention", "megan_nicety", CUSTOM_KEY,
        ]
        assert {c["content"] for c in client.created} == {
            "Reminder", "For Meegy", CUSTOM_ANSWER,
        }
        assert {c["description"] for c in client.created} == {
            INTENTION, SECRET, None,
        }

    def test_optin_store_is_not_consulted(self):
        # The persistent opt-in store is legacy: an explicit all-false record
        # must not suppress a non-empty prompt.
        ps.save_optins(expected_revision=0, optins={
            "intention": False, "megan_nicety": False,
            "stoic_intention": False,
        })
        ps.save_drafts(day=DAY, patch={"intention": INTENTION,
                                       "megan_nicety": SECRET})
        client = RecordingTodoist()
        outcomes = pe.run_prompt_exports(day=DAY, civil_date=CIVIL, todoist=client)
        assert [o.prompt_key for o in outcomes] == ["intention", "megan_nicety"]

    def test_custom_prompt_exports_text_as_title_without_a_description(self):
        client = RecordingTodoist()
        pe.run_prompt_exports(
            day=DAY, civil_date=CIVIL, todoist=client,
            drafts={CUSTOM_KEY: CUSTOM_ANSWER},
        )
        [created] = client.created
        assert created["content"] == CUSTOM_ANSWER
        assert created["description"] is None

    def test_skip_flag_still_creates_the_task_without_a_description(self):
        client = RecordingTodoist()
        outcomes = pe.run_prompt_exports(
            day=DAY, civil_date=CIVIL, todoist=client,
            drafts={"megan_nicety": SECRET},
            description_skips={"megan_nicety": True},
        )
        # The task is still created: a skip flag is not an export gate.
        assert [o.status for o in outcomes] == [pe.OUTCOME_DONE]
        [created] = client.created
        assert created["content"] == "For Meegy"
        assert created["description"] is None
        assert created["due_date"] == CIVIL
        assert er.load_receipts().get(
            DAY, "megan_nicety", pe.EXPORT_ACTION).status == er.STATUS_DONE

    def test_skip_flags_are_read_from_the_day_scoped_server_store(self):
        ps.save_drafts(day=DAY, patch={"megan_nicety": SECRET,
                                       "stoic_intention": STOIC})
        ps.save_description_skips(day=DAY, patch={"stoic_intention": True})
        client = RecordingTodoist()
        outcomes = pe.run_prompt_exports(day=DAY, civil_date=CIVIL, todoist=client)
        assert [o.status for o in outcomes] == [pe.OUTCOME_DONE, pe.OUTCOME_DONE]
        by_title = {c["content"]: c for c in client.created}
        assert by_title["For Meegy"]["description"] == SECRET
        assert by_title["Stoic"]["description"] is None

    def test_skip_flag_for_another_day_does_not_apply(self):
        ps.save_drafts(day=DAY, patch={"megan_nicety": SECRET})
        ps.save_description_skips(day="2026-07-11",
                                  patch={"megan_nicety": True})
        client = RecordingTodoist()
        pe.run_prompt_exports(day=DAY, civil_date=CIVIL, todoist=client)
        assert client.created[0]["description"] == SECRET

    def test_corrupt_skip_store_preserves_bytes_and_writes_the_description(self):
        target = ps.description_skips_path(DAY)
        target.parent.mkdir(parents=True, exist_ok=True)
        raw = '{"version": 1, "day": "2026-07-12", "skips": {"megan_nicety": true}, "skips": {}}'
        target.write_text(raw, encoding="utf-8")
        client = RecordingTodoist()
        outcomes = pe.run_prompt_exports(
            day=DAY, civil_date=CIVIL, todoist=client,
            drafts={"megan_nicety": SECRET},
        )
        # Fail closed to today's behavior: an unreadable skip store never
        # suppresses the description and is never repaired.
        assert [o.status for o in outcomes] == [pe.OUTCOME_DONE]
        assert client.created[0]["description"] == SECRET
        assert target.read_text(encoding="utf-8") == raw

    def test_titles_and_descriptions_never_reach_receipts_or_outcomes(self):
        client = RecordingTodoist()
        outcomes = pe.run_prompt_exports(
            day=DAY, civil_date=CIVIL, todoist=client,
            drafts={"intention": INTENTION, "megan_nicety": SECRET,
                    "stoic_intention": STOIC, CUSTOM_KEY: CUSTOM_ANSWER},
        )
        raw = er.receipts_path().read_text(encoding="utf-8")
        rendered = repr([o.as_dict() for o in outcomes])
        for secret in (SECRET, INTENTION, STOIC, CUSTOM_ANSWER):
            assert secret not in raw
            assert secret not in rendered

    def test_duplicate_done_skips_second_attempt(self):
        client = RecordingTodoist()
        first = pe.run_prompt_exports(
            day=DAY, civil_date=CIVIL, todoist=client,
            drafts={"megan_nicety": SECRET},
        )
        second = pe.run_prompt_exports(
            day=DAY, civil_date=CIVIL, todoist=client,
            drafts={"megan_nicety": SECRET},
        )
        assert first[0].status == pe.OUTCOME_DONE
        assert second[0].status == pe.OUTCOME_DONE
        assert len(client.created) == 1  # no duplicate task

    def test_custom_prompt_export_is_idempotent_with_a_receipt(self):
        client = RecordingTodoist()
        drafts = {CUSTOM_KEY: CUSTOM_ANSWER}
        first = pe.run_prompt_exports(
            day=DAY, civil_date=CIVIL, todoist=client, drafts=drafts,
        )
        second = pe.run_prompt_exports(
            day=DAY, civil_date=CIVIL, todoist=client, drafts=drafts,
        )
        assert first[0].status == pe.OUTCOME_DONE
        assert second[0].status == pe.OUTCOME_DONE
        assert len(client.created) == 1
        receipt = er.load_receipts().get(DAY, CUSTOM_KEY, pe.EXPORT_ACTION)
        assert receipt.status == er.STATUS_DONE

    def test_provider_error_never_auto_retries(self):
        client = RecordingTodoist(raise_on_create=True)
        first = pe.run_prompt_exports(
            day=DAY, civil_date=CIVIL, todoist=client,
            drafts={"megan_nicety": SECRET},
        )
        assert first[0].status == pe.OUTCOME_NEEDS_REVIEW
        assert er.load_receipts().get(DAY, "megan_nicety",
                                      pe.EXPORT_ACTION).status == er.STATUS_NEEDS_REVIEW
        # A second run strands as needs_review and never touches the provider.
        client.raise_on_create = False
        second = pe.run_prompt_exports(
            day=DAY, civil_date=CIVIL, todoist=client,
            drafts={"megan_nicety": SECRET},
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
            day=DAY, civil_date=CIVIL, todoist=client,
            drafts={"megan_nicety": SECRET},
        )
        assert [o.status for o in outcomes] == [pe.OUTCOME_BLOCKED]
        assert client.created == []
        assert target.read_text(encoding="utf-8") == raw

    def test_missing_client_blocks_planned_export(self):
        outcomes = pe.run_prompt_exports(
            day=DAY, civil_date=CIVIL, todoist=None,
            drafts={"megan_nicety": SECRET},
        )
        assert [o.status for o in outcomes] == [pe.OUTCOME_BLOCKED]

    def test_client_without_create_task_fails_content_free(self):
        outcomes = pe.run_prompt_exports(
            day=DAY, civil_date=CIVIL, todoist=NoCreateTodoist(),
            drafts={"megan_nicety": SECRET},
        )
        assert [o.status for o in outcomes] == [pe.OUTCOME_NEEDS_REVIEW]
        assert SECRET not in repr(outcomes[0])

    def test_no_prompt_text_reaches_receipt_or_outcome(self):
        client = RecordingTodoist()
        outcomes = pe.run_prompt_exports(
            day=DAY, civil_date=CIVIL, todoist=client,
            drafts={"intention": INTENTION, "megan_nicety": SECRET,
                    "stoic_intention": STOIC, CUSTOM_KEY: CUSTOM_ANSWER},
        )
        raw = er.receipts_path().read_text(encoding="utf-8")
        for secret in (SECRET, INTENTION, STOIC, CUSTOM_ANSWER):
            assert secret not in raw
            assert secret not in repr([o.as_dict() for o in outcomes])

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
# Fail-closed gate: exports only after the main commit landed ok
# ---------------------------------------------------------------------------

class TestMainFailureGate:
    def test_failed_report_skips_without_provider_or_receipt(self):
        client = RecordingTodoist()
        outcomes = pe.run_prompt_exports_for_report(
            {"ok": False, "surfaces": {"todoist": {"status": "failed"}}},
            day=DAY, civil_date=CIVIL, todoist=client,
            drafts={"megan_nicety": SECRET},
        )
        assert [o.status for o in outcomes] == [pe.OUTCOME_SKIPPED_MAIN_FAILURE]
        assert outcomes[0].prompt_key == pe.SKIPPED_MAIN_FAILURE_KEY
        assert client.created == []
        assert not er.receipts_path().exists()
        assert SECRET not in repr([o.as_dict() for o in outcomes])

    def test_non_mapping_report_also_skips(self):
        client = RecordingTodoist()
        outcomes = pe.run_prompt_exports_for_report(
            None, day=DAY, civil_date=CIVIL, todoist=client,
            drafts={"megan_nicety": SECRET},
        )
        assert [o.status for o in outcomes] == [pe.OUTCOME_SKIPPED_MAIN_FAILURE]
        assert client.created == []
        assert not er.receipts_path().exists()

    def test_ok_report_runs_the_lane(self):
        client = RecordingTodoist()
        outcomes = pe.run_prompt_exports_for_report(
            {"ok": True}, day=DAY, civil_date=CIVIL, todoist=client,
            drafts={"megan_nicety": SECRET},
        )
        assert [o.status for o in outcomes] == [pe.OUTCOME_DONE]
        assert len(client.created) == 1

    def test_attach_marks_skipped_marker_not_ok(self):
        report = {"ok": False, "surfaces": {}}
        pe.attach_to_report(report, pe.skipped_main_failure_outcomes(day=DAY))
        assert report["prompt_exports_ok"] is False
        assert report["prompt_exports"][0]["status"] == pe.OUTCOME_SKIPPED_MAIN_FAILURE
        assert SECRET not in repr(report["prompt_exports"])


# ---------------------------------------------------------------------------
# Storage I/O failure — bounded, content-free lane outcomes
# ---------------------------------------------------------------------------

def _fail_atomic_write_on(monkeypatch, fail_on: int, error: OSError):
    real = capacities_cache_io.atomic_write_json
    state = {"calls": 0}

    def _write(path, data):
        state["calls"] += 1
        if state["calls"] == fail_on:
            raise error
        return real(path, data)

    monkeypatch.setattr(capacities_cache_io, "atomic_write_json", _write)
    return state


class TestStorageFailureLane:
    def test_begin_write_failure_blocks_without_provider(self, monkeypatch):
        _fail_atomic_write_on(monkeypatch, 1, OSError(28, "No space left on device"))
        client = RecordingTodoist()
        outcomes = pe.run_prompt_exports(
            day=DAY, civil_date=CIVIL, todoist=client,
            drafts={"megan_nicety": SECRET},
        )
        assert [o.status for o in outcomes] == [pe.OUTCOME_BLOCKED]
        assert client.created == []
        assert not er.receipts_path().exists()
        assert SECRET not in repr(outcomes[0].as_dict())
        assert "No space" not in repr(outcomes[0].as_dict())

    def test_complete_write_failure_preserves_pending_and_needs_review(self, monkeypatch):
        client = RecordingTodoist()
        _fail_atomic_write_on(monkeypatch, 2, OSError(28, "No space left on device"))
        outcomes = pe.run_prompt_exports(
            day=DAY, civil_date=CIVIL, todoist=client,
            drafts={"megan_nicety": SECRET},
        )
        assert [o.status for o in outcomes] == [pe.OUTCOME_NEEDS_REVIEW]
        assert len(client.created) == 1  # provider called exactly once
        stored = er.load_receipts().get(DAY, "megan_nicety", pe.EXPORT_ACTION)
        assert stored.status == er.STATUS_PENDING  # pending preserved
        assert SECRET not in repr(outcomes[0].as_dict())

    def test_complete_write_failure_retry_never_duplicates(self, monkeypatch):
        client = RecordingTodoist()
        real = capacities_cache_io.atomic_write_json
        state = _fail_atomic_write_on(
            monkeypatch, 2, OSError(28, "No space left on device"))
        first = pe.run_prompt_exports(
            day=DAY, civil_date=CIVIL, todoist=client,
            drafts={"megan_nicety": SECRET},
        )
        assert first[0].status == pe.OUTCOME_NEEDS_REVIEW
        assert len(client.created) == 1
        monkeypatch.setattr(capacities_cache_io, "atomic_write_json", real)
        second = pe.run_prompt_exports(
            day=DAY, civil_date=CIVIL, todoist=client,
            drafts={"megan_nicety": SECRET},
        )
        assert second[0].status == pe.OUTCOME_NEEDS_REVIEW
        assert len(client.created) == 1  # no duplicate task on retry
        assert state["calls"] == 2

    def test_fail_export_write_exception_is_safe_bounded(self, monkeypatch):
        client = RecordingTodoist(raise_on_create=True)
        _fail_atomic_write_on(monkeypatch, 2, PermissionError(13, "Permission denied"))
        outcomes = pe.run_prompt_exports(
            day=DAY, civil_date=CIVIL, todoist=client,
            drafts={"megan_nicety": SECRET},
        )
        assert [o.status for o in outcomes] == [pe.OUTCOME_NEEDS_REVIEW]
        assert SECRET not in repr(outcomes[0].as_dict())
        assert "Permission" not in repr(outcomes[0].as_dict())
        stored = er.load_receipts().get(DAY, "megan_nicety", pe.EXPORT_ACTION)
        assert stored.status == er.STATUS_PENDING


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
    write_config_note(cfg,
        "## Defaults\n| Key | Value |\n|---|---|\n| eod | 11:59 PM |\n\n"
        "## Template Blocks\n### Trinoor Hours\n"
        "| Slot | Start | End |\n|---|---|---|\n"
        "| Morning | 12:00 AM | 11:59 PM |\n",
        encoding="utf-8",
    )
    rs.write_digest_index(date.fromisoformat(DAY),
                          [{"name": "Garage", "todoist_id": "", "path": "P/Garage.md",
                            "surface": "assigned"}])
    return v


@pytest.fixture
def client(vault) -> TestClient:
    app = main_mod.create_app()
    c = TestClient(app)
    c.app_token = app.state.token
    return c


def _auth(client: TestClient) -> dict:
    return {"X-TDTB-Token": client.app_token}


def _freeze(monkeypatch) -> None:
    monkeypatch.setattr(gather, "effective_date", lambda now: date.fromisoformat(DAY))


def _live_state(garage_live: bool):
    def _state(config):
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
        ps.save_drafts(day=DAY, patch={"megan_nicety": SECRET})
        todoist = RecordingTodoist()
        client.app.state.build_commit_clients = lambda cfg: (todoist, None)
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
        export_tasks = [c for c in todoist.created if c["content"] == "For Meegy"]
        assert len(export_tasks) == 1
        assert export_tasks[0]["description"] == SECRET
        assert export_tasks[0]["due_date"] == datetime.now().date().isoformat()
        assert export_tasks[0]["project_id"] is None
        # Content-free response + durable done receipt.
        assert SECRET not in r.text
        receipt = er.load_receipts().get(DAY, "megan_nicety", pe.EXPORT_ACTION)
        assert receipt.status == er.STATUS_DONE

    def test_day_scoped_skip_flag_exports_without_a_description(
            self, client, vault, monkeypatch):
        _freeze(monkeypatch)
        monkeypatch.setattr(shadow, "gather_live_state", _live_state(garage_live=False))
        ps.save_drafts(day=DAY, patch={"megan_nicety": SECRET})
        todoist = RecordingTodoist()
        client.app.state.build_commit_clients = lambda cfg: (todoist, None)
        # The skip flag is set through the same browser seam as the drafts.
        r = client.post("/day-setup", headers=_auth(client), json={
            "description_skips": {"intention": False, "megan_nicety": True,
                                  "stoic_intention": False},
        })
        assert r.status_code == 200, r.text
        assert r.json()["day_setup_confirmed"] is False
        assert r.json()["day_setup"]["description_skips"] == {
            "intention": False, "megan_nicety": True, "stoic_intention": False,
        }
        self._confirm(client)

        resp = client.post("/commit?mode=live", headers=_auth(client),
                           json={"digest": DIGEST, "sequence": SEQUENCE,
                                 "config": {}})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["prompt_exports_ok"] is True
        # The task still lands; only its description is skipped.
        [exported] = [c for c in todoist.created if c["content"] == "For Meegy"]
        assert exported["description"] is None
        assert SECRET not in resp.text

    def test_intention_and_custom_prompts_export_at_commit(
            self, client, vault, monkeypatch):
        _freeze(monkeypatch)
        monkeypatch.setattr(shadow, "gather_live_state", _live_state(garage_live=False))
        ps.save_drafts(day=DAY, patch={
            "intention": INTENTION, CUSTOM_KEY: CUSTOM_ANSWER,
        })
        todoist = RecordingTodoist()
        client.app.state.build_commit_clients = lambda cfg: (todoist, None)
        self._confirm(client)
        r = client.post("/commit?mode=live", headers=_auth(client),
                        json={"digest": DIGEST, "sequence": SEQUENCE, "config": {}})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["prompt_exports_ok"] is True
        assert [e["prompt_key"] for e in body["prompt_exports"]] == [
            "intention", CUSTOM_KEY,
        ]
        by_content = {c["content"]: c for c in todoist.created}
        assert by_content["Reminder"]["project_id"] is None
        assert by_content["Reminder"]["due_date"] == datetime.now().date().isoformat()
        assert by_content["Reminder"]["description"] == INTENTION
        assert by_content[CUSTOM_ANSWER]["project_id"] is None
        assert by_content[CUSTOM_ANSWER]["description"] is None
        assert SECRET not in r.text
        assert INTENTION not in r.text
        assert CUSTOM_ANSWER not in r.text
        assert er.load_receipts().get(
            DAY, CUSTOM_KEY, pe.EXPORT_ACTION).status == er.STATUS_DONE

    def test_legacy_optin_routes_still_ok_and_values_ignored(
            self, client, vault, monkeypatch):
        _freeze(monkeypatch)
        monkeypatch.setattr(shadow, "gather_live_state", _live_state(garage_live=False))
        # A legacy opt-in write still round-trips OK (mixed values preserved)...
        r = client.post("/day-setup", headers=_auth(client), json={
            "optins": {"intention": True, "megan_nicety": False,
                       "stoic_intention": False},
            "optins_revision": 0,
        })
        assert r.status_code == 200, r.text
        assert r.json()["optins"] == {"intention": True, "megan_nicety": False,
                                      "stoic_intention": False}
        ps.save_drafts(day=DAY, patch={"stoic_intention": STOIC})
        todoist = RecordingTodoist()
        client.app.state.build_commit_clients = lambda cfg: (todoist, None)
        self._confirm(client)
        resp = client.post("/commit?mode=live", headers=_auth(client),
                           json={"digest": DIGEST, "sequence": SEQUENCE,
                                 "config": {}})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["prompt_exports_ok"] is True
        # stoic_intention's opt-in is explicitly False, yet its non-empty draft
        # still exports: the legacy value is ignored, never a gate.
        assert [e["prompt_key"] for e in body["prompt_exports"]] == ["stoic_intention"]
        assert [c["description"] for c in todoist.created
                if c["content"] == "Stoic"]
        # The legacy GET metadata block still round-trips truthfully. Read it
        # AFTER the commit: GET /plan-inputs refreshes the digest cache, which
        # commit eligibility reads from, so it must not run before the commit.
        meta = client.get("/plan-inputs").json()["prompt_optins"]
        assert meta["optins"] == {"intention": True, "megan_nicety": False,
                                  "stoic_intention": False}
        assert meta["revision"] == 1

    def test_repeated_live_commit_does_not_duplicate(self, client, vault, monkeypatch):
        _freeze(monkeypatch)
        monkeypatch.setattr(shadow, "gather_live_state", _live_state(garage_live=False))
        ps.save_drafts(day=DAY, patch={"megan_nicety": SECRET})
        todoist = RecordingTodoist()
        client.app.state.build_commit_clients = lambda cfg: (todoist, None)
        self._confirm(client)
        payload = {"digest": DIGEST, "sequence": SEQUENCE, "config": {}}
        assert client.post("/commit?mode=live", headers=_auth(client),
                           json=payload).status_code == 200
        second = client.post("/commit?mode=live", headers=_auth(client), json=payload)
        assert second.status_code == 200
        assert second.json()["prompt_exports_ok"] is True
        assert len([c for c in todoist.created
                    if c["content"] == "For Meegy"]) == 1

    def test_browser_captures_do_not_authorize_export(self, client, vault, monkeypatch):
        _freeze(monkeypatch)
        monkeypatch.setattr(shadow, "gather_live_state", _live_state(garage_live=False))
        todoist = RecordingTodoist()
        client.app.state.build_commit_clients = lambda cfg: (todoist, None)
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
        ps.save_drafts(day=DAY, patch={"megan_nicety": SECRET})

        def _boom(cfg):
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
        ps.save_drafts(day=DAY, patch={"megan_nicety": SECRET})
        client.app.state.build_commit_clients = (
            lambda cfg: (NoCreateTodoist([{"id": "T1", "content": "Garage",
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

    def test_failed_main_report_skips_exports_and_survives(
            self, client, vault, monkeypatch):
        _freeze(monkeypatch)
        monkeypatch.setattr(shadow, "gather_live_state", _live_state(garage_live=False))
        ps.save_drafts(day=DAY, patch={"megan_nicety": SECRET})
        todoist = NoIdTodoist()
        client.app.state.build_commit_clients = lambda cfg: (todoist, None)
        self._confirm(client)
        r = client.post("/commit?mode=live", headers=_auth(client),
                        json={"digest": DIGEST, "sequence": SEQUENCE, "config": {}})
        assert r.status_code == 200, r.text
        body = r.json()
        # The partial-failure report survives with its per-surface ledger.
        assert body["ok"] is False
        assert body["surfaces"]["todoist"]["status"] == "failed"
        # No export task, no receipt, explicit content-free skip marker.
        assert body["prompt_exports_ok"] is False
        assert body["prompt_exports"][0]["status"] == pe.OUTCOME_SKIPPED_MAIN_FAILURE
        assert all(c["content"] != SECRET for c in todoist.created)
        assert len(todoist.created) == 1  # only the main surface, never an export
        assert not er.receipts_path().exists()
        assert SECRET not in r.text

    def test_unauthorized_commit_writes_no_export(self, client, vault, monkeypatch):
        _freeze(monkeypatch)
        ps.save_drafts(day=DAY, patch={"megan_nicety": SECRET})
        todoist = RecordingTodoist()
        client.app.state.build_commit_clients = lambda cfg: (todoist, None)
        r = client.post("/commit?mode=live",
                        json={"digest": DIGEST, "sequence": SEQUENCE, "config": {}})
        assert r.status_code == 403
        assert todoist.created == []
        assert not er.receipts_path().exists()
        assert SECRET not in r.text
