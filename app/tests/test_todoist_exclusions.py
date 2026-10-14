"""Todoist task-exclusion policy: matching, digest filtering, and the
plan-inputs/commit eligibility boundary.

Covers the pure matcher (exact id OR normalized-name equality, no partial
matches, non-Todoist rows untouched), ``build_digest`` dropping excluded rows
before ranking/allocation with the mirrored diagnostics envelope, and the live
persisted route path: a matching task is absent from ``/plan-inputs`` (digest
and digest index) and therefore from commit eligibility, which refuses it.
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "gather"))

import app_config  # noqa: E402
import artifact_source as art  # noqa: E402
import exclusion_settings as es  # noqa: E402
import main as main_mod  # noqa: E402
import runstate  # noqa: E402
import tag_exclusions as tx  # noqa: E402
import todoist_exclusions as tzx  # noqa: E402
import tdtb_gather as gather  # noqa: E402

TODAY = date(2026, 10, 8)
ADDED_AT = "2026-10-08T09:00:00+00:00"


def _entry(id: str = "9001", name: str = "Pay rent", added_at: str = ADDED_AT):
    return es.TodoistExclusion(id=id, name=name, added_at=added_at)


def _policy(entries=()) -> tx.ExclusionPolicy:
    return tx.ExclusionPolicy(
        version=es.SCHEMA_VERSION,
        revision=2,
        persisted=True,
        tags=(),
        todoist=tuple(entries),
    )


def _todoist_row(name: str, task_id: str, assigned: bool, **extra) -> dict:
    return {
        "name": name,
        "path": f"todoist://{task_id}",
        "identity": f"todoist:{task_id}",
        "source": "todoist",
        "todoist_id": task_id,
        "assigned": assigned,
        **extra,
    }


class TestMatching:
    def test_exact_id_match_excludes_both_surfaces_with_decisions(self):
        assigned = [_todoist_row("Pay rent", "9001", True)]
        pool = [_todoist_row("Pay rent", "9001", False)]

        kept_assigned, kept_pool, report = tzx.apply_todoist_exclusions(
            assigned, pool, _policy([_entry()])
        )

        assert kept_assigned == []
        assert kept_pool == []
        assert report["excluded_counts"] == {"assigned": 1, "pool": 1, "total": 2}
        assert [d["reason"] for d in report["decisions"]] == [
            "excluded_todoist", "excluded_todoist",
        ]
        assert report["decisions"][0]["surface"] == "assigned"
        assert report["decisions"][0]["identity"] == "todoist:9001"
        assert report["decisions"][0]["matched"] == {"id": "9001", "name": "Pay rent"}

    def test_name_match_casefolds_and_collapses_whitespace(self):
        rows = [_todoist_row("  pAy   RENT ", "5555", True)]

        kept, _, report = tzx.apply_todoist_exclusions(
            rows, [], _policy([_entry(name="Pay Rent")])
        )

        assert kept == []
        assert report["decisions"][0]["matched"] == {
            "id": None, "name": "  pAy   RENT ",
        }

    def test_name_match_keeps_a_series_excluded_across_ids(self):
        # The recurring occurrence has a NEW id but the same title; the name
        # rule is what keeps the series excluded after Todoist re-issues it.
        rows = [_todoist_row("Pay rent", "990099", True)]

        kept, _, _ = tzx.apply_todoist_exclusions(
            rows, [], _policy([_entry(id="9999", name="Pay rent")])
        )

        assert kept == []

    def test_partial_name_is_not_a_match(self):
        rows = [_todoist_row("Pay rent later", "9002", True)]

        kept, _, report = tzx.apply_todoist_exclusions(
            rows, [], _policy([_entry(name="Pay rent")])
        )

        assert kept == rows
        assert report["excluded_counts"]["total"] == 0
        assert report["decisions"] == []

    def test_non_todoist_row_with_the_same_name_is_kept(self):
        row = {"name": "Pay rent", "path": "50 - Ops/Pay rent.md", "source": "vault"}

        kept, _, report = tzx.apply_todoist_exclusions(
            [row], [], _policy([_entry()])
        )

        assert kept == [row]
        assert report["excluded_counts"]["total"] == 0

    def test_empty_policy_retains_everything(self):
        rows = [_todoist_row("Pay rent", "9001", True)]

        kept_assigned, kept_pool, report = tzx.apply_todoist_exclusions(
            rows, rows, _policy()
        )

        assert kept_assigned == rows
        assert kept_pool == rows
        assert report["evaluated_counts"] == {"assigned": 1, "pool": 1}
        assert report["excluded_counts"] == {"assigned": 0, "pool": 0, "total": 0}

    def test_name_matching_ignores_a_whitespace_only_row_name(self):
        rows = [_todoist_row("   ", "9001", True)]

        kept, _, report = tzx.apply_todoist_exclusions(
            rows, [], _policy([_entry()])
        )

        # The id still matches; a blank name never accidentally matches.
        assert kept == []
        assert report["decisions"][0]["matched"] == {"id": "9001", "name": None}


class TestDigest:
    def test_excluded_rows_never_reach_assigned_or_pool(self):
        assigned = [
            _todoist_row("Pay rent", "9001", True),
            _todoist_row("Call Vlad", "9003", True),
        ]
        pool = [_todoist_row("Water plants", "9002", False)]
        policy = _policy([
            _entry("9001", "Pay rent"),
            _entry("9999", "Water plants"),  # different id, same name
        ])

        digest = main_mod.build_digest(
            pool, assigned, TODAY, ["urgency"], exclusion_policy=policy
        )

        assert [r["name"] for r in digest["assigned"]] == ["Call Vlad"]
        assert digest["suggested"] == []
        assert digest["assigned_count"] == 1
        assert digest["pool_count"] == 0
        report = digest["todoist_exclusion_policy"]
        assert report["revision"] == 2
        assert report["persisted"] is True
        assert report["excluded_counts"] == {"assigned": 1, "pool": 1, "total": 2}

    def test_no_policy_means_no_todoist_envelope(self):
        digest = main_mod.build_digest([], [], TODAY, ["urgency"])

        assert "todoist_exclusion_policy" not in digest


def _client() -> TestClient:
    app = main_mod.create_app()
    client = TestClient(app)
    client.app_token = app.state.token
    return client


def _auth(client: TestClient) -> dict:
    return {"X-TDTB-Token": client.app_token}


def _artifact_doc(rows: list[dict], today: date, generated_at: str) -> dict:
    sources = {
        "todoist": {
            "status": "ok", "read_at": generated_at, "rows": len(rows),
            "dropped": 0, "deferred": 0, "warnings": [],
        },
        "capacities": {
            "status": "ok", "read_at": generated_at, "rows": 0,
            "dropped": 0, "deferred": 0, "warnings": [],
        },
    }
    return {
        "schema": art.ARTIFACT_SCHEMA,
        "version": art.ARTIFACT_VERSION,
        "generated_at": generated_at,
        "logical_day": str(today),
        "producer": {"name": "test", "version": "0.1.0", "run_id": "r1"},
        "content_hash": art.compute_content_hash(sources, rows),
        "sources": sources,
        "rows": rows,
        "admission": {"rule_set_hash": "rs-1", "admitted": [], "dropped": []},
    }


def _enable_artifact_mode() -> None:
    app_config.config_path().parent.mkdir(parents=True, exist_ok=True)
    app_config.config_path().write_text(
        json.dumps({"version": 1, "sources": {"mode": "artifact"}}),
        encoding="utf-8",
    )


class TestPlanInputsAndCommitEligibility:
    def test_excluded_task_is_absent_from_digest_index_and_commit(self):
        today = gather.effective_date(datetime.now())
        generated_at = datetime.now().astimezone().isoformat()
        rows = [
            _todoist_row("Pay rent", "9001", True, is_recurring=True),
            _todoist_row("Water plants", "9002", True),
            _todoist_row("Call Vlad", "9003", True),
            _todoist_row("  pay   RENT ", "9500", False),
            _todoist_row("Pay rent later", "9501", False),
        ]
        _enable_artifact_mode()
        art.atomic_write_artifact(_artifact_doc(rows, today, generated_at))
        client = _client()

        saved = client.post(
            "/settings/exclusions/save",
            headers=_auth(client),
            json={
                "expected_revision": 0,
                "exclusions": {
                    "tags": [],
                    "todoist": [
                        {"id": "9001", "name": "Pay rent", "added_at": ADDED_AT},
                        # Never-seen id: the name alone must cover the series.
                        {"id": "8888", "name": "Water plants", "added_at": ADDED_AT},
                    ],
                },
            },
        )
        assert saved.status_code == 200

        body = client.get("/plan-inputs").json()
        digest = body["digest"]
        assert [r["name"] for r in digest["assigned"]] == ["Call Vlad"]
        assert [r["name"] for r in digest["suggested"]] == ["Pay rent later"]
        assert digest["todoist_exclusion_policy"]["excluded_counts"] == {
            "assigned": 2, "pool": 1, "total": 3,
        }

        index = runstate.read_digest_index(today)
        indexed_names = {str(row.get("name") or "") for row in index}
        assert "Pay rent" not in indexed_names
        assert "Water plants" not in indexed_names
        assert "Call Vlad" in indexed_names

        refused = client.post(
            "/commit?mode=shadow",
            headers=_auth(client),
            json={
                "digest": {"assigned": [_todoist_row("Pay rent", "9001", True)]},
                "sequence": {"sequence": []},
            },
        )
        assert refused.status_code == 422
        detail = refused.json()["detail"]
        assert "Pay rent" in detail
        assert "Nothing was written" in detail
