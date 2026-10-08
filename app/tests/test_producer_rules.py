"""A2 — the deterministic producer and its CLI (TDD gate).

A2 is the first real producer: the ``tdtb-refresh`` skill fetches Todoist over
MCP and hands the raw result to ``app/producer_rules.py``. These tests pin the
rule schema (every op, the recursive combinators, Capacities forward-compat),
first-match-wins ordering, ``admit: false`` exclusion, ``pool: true`` emission,
rule-id-named failures, determinism, and both CLIs' contracts.
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "gather"))
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "tools"))

import artifact_source as art  # noqa: E402
import producer_rules as pr  # noqa: E402
import tdtb_gather as gather  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
TOOLS = REPO_ROOT / "tools"
STARTER_RULES = REPO_ROOT / "skills" / "tdtb-refresh" / "producer-rules.json"

LOGICAL_DAY = "2026-10-08"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _rules(*rules: dict) -> dict:
    return {"version": 1, "rules": list(rules)}


def _rule(rule_id="r1", source="todoist", when=None, **extra) -> dict:
    node = when if when is not None else {"prop": "id", "op": "exists"}
    rule = {"id": rule_id, "source": source, "when": node}
    rule.update(extra)
    return rule


def _task(task_id="1", content="Task", **overrides) -> dict:
    task = {"id": task_id, "content": content, "priority": 1, "labels": []}
    task.update(overrides)
    return task


def _source(*tasks: dict) -> dict:
    return {"todoist": {"status": "ok", "read_at": "2026-10-08T09:00:00-07:00",
                        "tasks": list(tasks)}}


def _cap_object(object_id="c1", title="Inbox thing", structure_id="Project",
                collections=("Inbox",), properties=None, space_id=None) -> dict:
    record = {
        "id": object_id,
        "structureId": structure_id,
        "title": title,
        "collections": list(collections),
        "tags": [],
        "properties": dict(properties or {}),
    }
    if space_id:
        record["spaceId"] = space_id
    return record


def _cap_source(*objects: dict, space_id="space-1", **entry_extra) -> dict:
    entry = {
        "status": "ok",
        "read_at": "2026-10-08T09:00:00-07:00",
        "space_id": space_id,
        "objects": list(objects),
    }
    entry.update(entry_extra)
    return {"capacities": entry}


def _cap_rule(rule_id="cap", structure="Project", when=None, **extra) -> dict:
    return _rule(rule_id=rule_id, source="capacities", structure=structure,
                 when=when if when is not None else {"prop": "id", "op": "exists"},
                 **extra)


def _evaluate(rules_document, source, logical_day=LOGICAL_DAY):
    return pr.evaluate(rules_document, source, logical_day=logical_day)


def _admitted(when, record, logical_day=LOGICAL_DAY) -> bool:
    result = _evaluate(_rules(_rule(when=when, assigned=True)), _source(record), logical_day)
    return len(result.rows) == 1


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("op,values", [
    ("eq", [1]),
    ("in", [1]),
    ("exists", None),
    ("truthy", None),
    ("lt", [1]),
    ("gt", [1]),
    ("before", ["2026-10-08"]),
    ("after", ["2026-10-08"]),
    ("matches", ["^x"]),
])
def test_validate_accepts_every_op(op, values):
    leaf = {"prop": "priority", "op": op}
    if values is not None:
        leaf["values"] = values
    document = _rules(_rule(when=leaf))
    assert pr.validate_rules(document) == []


def test_validate_accepts_recursive_all_any_not():
    document = _rules(_rule(when={
        "all": [
            {"prop": "labels", "op": "in", "values": ["@work"]},
            {"any": [
                {"prop": "priority", "op": "gt", "values": [2]},
                {"not": {"prop": "labels", "op": "in", "values": ["paused"]}},
            ]},
        ],
    }))
    assert pr.validate_rules(document) == []


def test_validate_accepts_capacities_forward_compat_rules():
    document = _rules(_rule(
        rule_id="capacities-inbox",
        source="capacities",
        structure="Project",
        duration_prop="Duration",
        when={"prop": "collections", "op": "in", "values": ["Inbox"]},
        assigned=True,
    ))
    assert pr.validate_rules(document) == []


def test_invalid_op_names_the_rule_id():
    document = _rules(_rule(rule_id="broken-rule", when={"prop": "x", "op": "nope"}))
    violations = pr.validate_rules(document)
    assert violations
    assert "broken-rule" in " ".join(violations)


def test_missing_when_names_the_rule_id():
    document = {"version": 1, "rules": [{"id": "no-when", "source": "todoist"}]}
    violations = pr.validate_rules(document)
    assert any("no-when" in v for v in violations)


def test_bad_values_names_the_rule_id():
    document = _rules(_rule(rule_id="empty-values",
                            when={"prop": "x", "op": "in", "values": []}))
    violations = pr.validate_rules(document)
    assert any("empty-values" in v for v in violations)


def test_missing_source_names_the_rule_id():
    document = _rules(_rule(rule_id="no-source", source="not-a-source"))
    violations = pr.validate_rules(document)
    assert any("no-source" in v for v in violations)


def test_duplicate_rule_ids_are_rejected():
    document = _rules(
        _rule(rule_id="same"),
        _rule(rule_id="same"),
    )
    assert any("duplicate" in v for v in pr.validate_rules(document))


def test_mixed_combinator_and_leaf_is_rejected():
    document = _rules(_rule(rule_id="mixed", when={
        "all": [{"prop": "x", "op": "exists"}],
        "prop": "y", "op": "exists",
    }))
    assert any("mixed" in v for v in pr.validate_rules(document))


def test_starter_rules_file_is_valid():
    document = json.loads(STARTER_RULES.read_text(encoding="utf-8"))
    assert pr.validate_rules(document) == []


def test_load_rules_raises_with_the_rule_id(tmp_path):
    path = tmp_path / "producer-rules.json"
    path.write_text(json.dumps(_rules(_rule(rule_id="bad-here",
                                            when={"prop": "x", "op": "bogus"}))),
                    encoding="utf-8")
    with pytest.raises(pr.RulesError) as exc:
        pr.load_rules(path)
    assert "bad-here" in " ".join(exc.value.violations)


def test_load_rules_missing_file_is_explicit(tmp_path):
    with pytest.raises(pr.RulesError) as exc:
        pr.load_rules(tmp_path / "nope.json")
    assert "not found" in str(exc.value)


# ---------------------------------------------------------------------------
# Op semantics (through the evaluator)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("label,leaf,record,expected", [
    ("eq", {"prop": "priority", "op": "eq", "values": [4]}, {"priority": 4}, True),
    ("eq-miss", {"prop": "priority", "op": "eq", "values": [3]}, {"priority": 4}, False),
    ("in-list", {"prop": "labels", "op": "in", "values": ["@work"]},
     {"labels": ["@home", "@work"]}, True),
    ("in-scalar", {"prop": "priority", "op": "in", "values": [1, 4]}, {"priority": 4}, True),
    ("in-miss", {"prop": "labels", "op": "in", "values": ["@work"]}, {"labels": ["@home"]}, False),
    ("exists", {"prop": "due", "op": "exists"}, {"due": {"date": "2026-10-08"}}, True),
    ("exists-missing", {"prop": "due", "op": "exists"}, {}, False),
    ("truthy", {"prop": "is_completed", "op": "truthy"}, {"is_completed": True}, True),
    ("truthy-false", {"prop": "is_completed", "op": "truthy"}, {"is_completed": False}, False),
    ("lt", {"prop": "priority", "op": "lt", "values": [4]}, {"priority": 2}, True),
    ("gt", {"prop": "priority", "op": "gt", "values": [2]}, {"priority": 4}, True),
    ("gt-miss", {"prop": "priority", "op": "gt", "values": [2]}, {"priority": 1}, False),
    ("before", {"prop": "due.date", "op": "before", "values": ["2026-10-09"]},
     {"due": {"date": "2026-10-08"}}, True),
    ("after", {"prop": "due.date", "op": "after", "values": ["2026-10-07"]},
     {"due": {"date": "2026-10-08"}}, True),
    ("matches", {"prop": "content", "op": "matches", "values": ["^Review"]},
     {"content": "Review PR"}, True),
    ("matches-miss", {"prop": "content", "op": "matches", "values": ["^Review"]},
     {"content": "Water plants"}, False),
])
def test_op_semantics(label, leaf, record, expected):
    assert _admitted(leaf, _task(**record)) is expected


def test_today_token_resolves_to_the_logical_day():
    task = _task(due={"date": LOGICAL_DAY})
    assert _admitted({"prop": "due.date", "op": "eq", "values": ["$today"]}, task)
    assert not _admitted(
        {"prop": "due.date", "op": "eq", "values": ["$today"]}, task,
        logical_day="2026-10-09",
    )


# ---------------------------------------------------------------------------
# Ordering, exclusion, pool
# ---------------------------------------------------------------------------

def test_first_match_wins_in_file_order():
    source = _source(_task(task_id="7", content="Do it"))
    when = {"prop": "id", "op": "eq", "values": ["7"]}

    admit_first = _evaluate(_rules(
        _rule(rule_id="first", when=when, assigned=True),
        _rule(rule_id="second", when=when, admit=False),
    ), source)
    assert [r["name"] for r in admit_first.rows] == ["Do it"]

    drop_first = _evaluate(_rules(
        _rule(rule_id="first", when=when, admit=False),
        _rule(rule_id="second", when=when, assigned=True),
    ), source)
    assert drop_first.rows == []
    assert drop_first.dropped[0].rule == "first"


def test_admit_false_excludes_and_names_the_rule():
    source = _source(_task(task_id="9", content="Skip me"))
    result = _evaluate(_rules(
        _rule(rule_id="skip", when={"prop": "id", "op": "eq", "values": ["9"]},
              admit=False),
    ), source)

    assert result.rows == []
    assert result.dropped[0].rule == "skip"
    assert result.dropped[0].name == "Skip me"
    assert result.per_rule[0].dropped == 1
    assert result.per_rule[0].matched == 1


def test_pool_true_emits_assigned_false():
    source = _source(_task(task_id="5", content="Quick one"))
    result = _evaluate(_rules(
        _rule(rule_id="pool", when={"prop": "id", "op": "eq", "values": ["5"]},
              assigned=True, pool=True),
    ), source)

    assert len(result.rows) == 1
    assert result.rows[0]["assigned"] is False
    assert result.per_rule[0].admitted == 1


def test_unmatched_record_is_dropped_with_no_rule():
    source = _source(_task(task_id="3", content="Nobody wants me"))
    result = _evaluate(_rules(
        _rule(rule_id="only-other", when={"prop": "id", "op": "eq", "values": ["999"]}),
    ), source)

    assert result.rows == []
    assert result.dropped[0].rule is None
    assert result.dropped[0].reason == "no rule matched"


def test_duplicate_identities_are_deduplicated():
    source = _source(
        _task(task_id="1", content="Once"),
        _task(task_id="1", content="Once"),
    )
    result = _evaluate(_rules(_rule(when={"prop": "id", "op": "exists"})), source)
    assert [r["name"] for r in result.rows] == ["Once"]
    assert any("duplicate" in w.lower() for w in result.warnings)


def test_name_collisions_are_disambiguated():
    source = _source(
        _task(task_id="1", content="Same"),
        _task(task_id="2", content="Same"),
    )
    result = _evaluate(_rules(_rule(when={"prop": "id", "op": "exists"})), source)
    names = [r["name"] for r in result.rows]
    assert len(names) == 2
    assert len(set(names)) == 2


# ---------------------------------------------------------------------------
# Capacities evaluation (A3)
# ---------------------------------------------------------------------------

def test_capacities_rule_requires_a_structure():
    document = _rules(_rule(rule_id="cap", source="capacities",
                            when={"prop": "id", "op": "exists"}))
    violations = pr.validate_rules(document)
    assert any("structure" in v and "cap" in v for v in violations)


def test_capacities_rule_admits_and_emits_the_canonical_row():
    rules = _rules(_cap_rule(
        rule_id="cap-inbox",
        when={"prop": "collections", "op": "in", "values": ["Inbox"]},
        assigned=True,
    ))
    result = _evaluate(rules, _cap_source(_cap_object()))

    assert len(result.rows) == 1
    row = result.rows[0]
    assert row["source"] == "capacities"
    assert row["identity"] == "capacities:space-1:Project:c1"
    assert row["path"] == "capacities://space-1/c1"
    assert row["capacities_id"] == "c1"
    assert row["capacities_structure_id"] == "Project"
    assert row["capacities_space_id"] == "space-1"
    assert row["assigned"] is True
    assert row["duration_minutes"] == 30
    assert row["blocks"] == 1


def test_capacities_duration_prop_is_read():
    properties = {"Duration": {"type": "number", "number": {"value": 45}}}
    rules = _rules(_cap_rule(
        rule_id="cap-duration",
        duration_prop="Duration",
        when={"prop": "collections", "op": "in", "values": ["Inbox"]},
        assigned=True,
    ))
    result = _evaluate(rules, _cap_source(_cap_object(properties=properties)))

    assert result.rows[0]["duration_minutes"] == 45
    assert result.rows[0]["blocks"] == 1.5


def test_capacities_duration_prop_absent_falls_back_to_the_default():
    rules = _rules(_cap_rule(
        duration_prop="Duration",
        when={"prop": "id", "op": "exists"},
        assigned=True,
    ))
    result = _evaluate(rules, _cap_source(_cap_object()))
    assert result.rows[0]["duration_minutes"] == 30


def test_capacities_pool_true_emits_assigned_false():
    rules = _rules(_cap_rule(
        rule_id="cap-pool",
        when={"prop": "id", "op": "exists"},
        assigned=True,
        pool=True,
    ))
    result = _evaluate(rules, _cap_source(_cap_object()))
    assert result.rows[0]["assigned"] is False
    assert result.per_rule[0].admitted == 1


def test_capacities_admit_false_excludes_and_names_the_rule():
    rules = _rules(_cap_rule(
        rule_id="cap-skip",
        when={"prop": "id", "op": "exists"},
        admit=False,
    ))
    result = _evaluate(rules, _cap_source(_cap_object()))
    assert result.rows == []
    assert result.dropped[0].rule == "cap-skip"
    assert result.per_rule[0].dropped == 1


def test_capacities_structure_scope_skips_other_structures():
    rules = _rules(_cap_rule(
        rule_id="cap-project",
        structure="Project",
        when={"prop": "id", "op": "exists"},
        assigned=True,
    ))
    result = _evaluate(rules, _cap_source(_cap_object(structure_id="Note")))
    assert result.rows == []
    assert result.dropped[0].rule is None
    assert result.dropped[0].reason == "no rule matched"


def test_capacities_predicate_reads_flattened_property_values():
    properties = {
        "status": {"type": "label", "label": [{"id": "Active", "name": "Active"}]},
        "Estimate": {"type": "number", "number": {"value": 90}},
    }
    rules = _rules(_cap_rule(
        rule_id="cap-active",
        when={"all": [
            {"prop": "status", "op": "in", "values": ["Active"]},
            {"prop": "Estimate", "op": "gt", "values": [60]},
        ]},
        assigned=True,
    ))
    result = _evaluate(rules, _cap_source(_cap_object(properties=properties)))
    assert len(result.rows) == 1


def test_capacities_first_match_wins_in_file_order():
    source = _cap_source(_cap_object(object_id="c9"))
    when = {"prop": "id", "op": "eq", "values": ["c9"]}
    result = _evaluate(_rules(
        _cap_rule(rule_id="first", when=when, admit=False),
        _cap_rule(rule_id="second", when=when, assigned=True),
    ), source)
    assert result.rows == []
    assert result.dropped[0].rule == "first"


def test_capacities_and_todoist_rows_coexist_and_vault_is_ignored():
    source = {
        "todoist": {"status": "ok", "tasks": [_task(task_id="1", content="T")]},
        "capacities": _cap_source(_cap_object()) ["capacities"],
        "vault": {"status": "ok", "records": [{"id": "v1", "title": "V"}]},
    }
    result = _evaluate(_rules(
        _rule(when={"prop": "id", "op": "exists"}),
        _cap_rule(rule_id="cap", when={"prop": "id", "op": "exists"}, assigned=True),
    ), source)

    assert [r["name"] for r in result.rows] == ["Inbox thing", "T"]
    assert any("vault" in w for w in result.warnings)


def test_capacities_source_deferred_rides_the_sources_block():
    rules = _rules(_cap_rule(when={"prop": "id", "op": "exists"}, assigned=True))
    source = _cap_source(_cap_object(), status="partial", deferred=7)
    document = pr.build_artifact(
        source, rules, logical_day=LOGICAL_DAY,
        generated_at="2026-10-08T09:00:00-07:00", run_id="deferred",
    )
    assert document["sources"]["capacities"]["status"] == "partial"
    assert document["sources"]["capacities"]["deferred"] == 7
    assert art.validate_artifact(document) == []


def test_capacities_row_passes_a1_and_loads_in_the_app(tmp_path):
    now = datetime.now().astimezone()
    logical_day = str(gather.effective_date(now))
    rules = _rules(_cap_rule(
        rule_id="cap-inbox",
        when={"prop": "collections", "op": "in", "values": ["Inbox"]},
        assigned=True,
    ))
    source = _cap_source(_cap_object(space_id="space-1"))
    document = pr.build_artifact(
        source, rules, logical_day=logical_day,
        generated_at=now.isoformat(timespec="seconds"), run_id="cap-load",
    )
    assert art.validate_artifact(document) == []

    target = tmp_path / art.ARTIFACT_FILENAME
    art.atomic_write_artifact(document, path=target)
    result = art.load_artifact(
        now, path=target, overlay=tmp_path / "no-overlay.json", max_age_minutes=240,
    )

    assert result.status == art.STATUS_FRESH
    row = result.rows[0]
    assert row["source"] == "capacities"
    assert row["identity"] == "capacities:space-1:Project:c1"
    assert row["capacities_id"] == "c1"


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------

def _sample_source() -> dict:
    return _source(
        _task(task_id="1", content="Call Vlad", priority=4,
              due={"date": LOGICAL_DAY}),
        _task(task_id="2", content="Water plants", labels=["@🚀10min"]),
        _task(task_id="3", content="Reminder", labels=["🔔Reminder"]),
    )


def _sample_rules() -> dict:
    return _rules(
        _rule(rule_id="drop-reminders",
              when={"prop": "labels", "op": "in", "values": ["🔔Reminder"]},
              admit=False),
        _rule(rule_id="assigned",
              when={"prop": "due.date", "op": "eq", "values": ["$today"]},
              assigned=True),
        _rule(rule_id="pool",
              when={"prop": "labels", "op": "in", "values": ["@🚀10min"]},
              pool=True),
    )


def test_evaluator_is_deterministic():
    first = _evaluate(_sample_rules(), _sample_source())
    second = _evaluate(_sample_rules(), _sample_source())

    assert json.dumps(first.rows, sort_keys=True) == json.dumps(second.rows, sort_keys=True)
    assert [d.identity for d in first.dropped] == [d.identity for d in second.dropped]
    assert [(s.id, s.matched, s.admitted, s.dropped) for s in first.per_rule] == \
        [(s.id, s.matched, s.admitted, s.dropped) for s in second.per_rule]


def test_build_artifact_is_byte_reproducible():
    kwargs = dict(logical_day=LOGICAL_DAY,
                  generated_at="2026-10-08T09:00:00-07:00", run_id="fixed")
    first = pr.build_artifact(_sample_source(), _sample_rules(), **kwargs)
    second = pr.build_artifact(_sample_source(), _sample_rules(), **kwargs)

    assert first["content_hash"] == second["content_hash"]
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


# ---------------------------------------------------------------------------
# The artifact passes A1 and loads in the app
# ---------------------------------------------------------------------------

def test_built_artifact_passes_a1_validator():
    document = pr.build_artifact(
        _sample_source(), _sample_rules(),
        logical_day=LOGICAL_DAY, generated_at="2026-10-08T09:00:00-07:00",
        run_id="a1-check",
    )
    assert art.validate_artifact(document) == []


def test_producer_artifact_loads_fresh_in_the_app(tmp_path):
    now = datetime.now().astimezone()
    logical_day = str(gather.effective_date(now))
    source = _source(
        _task(task_id="1", content="Call Vlad", priority=4,
              due={"date": logical_day}),
        _task(task_id="2", content="Water plants", labels=["@🚀10min"]),
    )
    document = pr.build_artifact(
        source, _sample_rules(),
        logical_day=logical_day,
        generated_at=now.isoformat(timespec="seconds"),
        run_id="load-check",
    )
    target = tmp_path / art.ARTIFACT_FILENAME
    art.atomic_write_artifact(document, path=target)

    result = art.load_artifact(
        now, path=target, overlay=tmp_path / "no-overlay.json", max_age_minutes=240,
    )

    assert result.status == art.STATUS_FRESH
    assert [r["name"] for r in result.rows] == ["Call Vlad", "Water plants"]
    assert result.rows[1]["assigned"] is False


# ---------------------------------------------------------------------------
# CLI round trip
# ---------------------------------------------------------------------------

def _write(path: Path, payload) -> Path:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _run_produce(rules_path: Path, source: dict, artifact: Path | None = None):
    argv = [
        sys.executable, str(TOOLS / "produce_rows.py"),
        "--rules", str(rules_path),
        "--logical-day", LOGICAL_DAY,
        "--generated-at", "2026-10-08T09:00:00-07:00",
        "--run-id", "cli-test",
    ]
    if artifact is not None:
        argv += ["--write", "--artifact", str(artifact)]
    return subprocess.run(
        argv, input=json.dumps(source), capture_output=True, text=True,
        cwd=str(REPO_ROOT),
    )


def _run_validate(path_or_stdin: str, from_stdin: str | None = None):
    argv = [sys.executable, str(TOOLS / "validate_artifact.py")]
    if from_stdin is None:
        argv.append(path_or_stdin)
        return subprocess.run(argv, capture_output=True, text=True, cwd=str(REPO_ROOT))
    argv.append("-")
    return subprocess.run(argv, input=from_stdin, capture_output=True, text=True,
                          cwd=str(REPO_ROOT))


def test_cli_round_trips_through_validate_artifact(tmp_path):
    rules_path = _write(tmp_path / "rules.json", _sample_rules())
    produced = _run_produce(rules_path, _sample_source())
    assert produced.returncode == 0, produced.stderr

    validated = _run_validate("-", from_stdin=produced.stdout)
    assert validated.returncode == 0, validated.stdout + validated.stderr
    assert "OK:" in validated.stdout

    document = json.loads(produced.stdout)
    assert art.validate_artifact(document) == []
    admission = document["admission"]
    assert admission["admitted"] == ["Call Vlad", "Water plants"]
    assert {row["id"] for row in admission["per_rule"]} == \
        {"drop-reminders", "assigned", "pool"}
    assert admission["recent_drops"][0]["name"] == "Reminder"
    assert admission["recent_drops"][0]["rule"] == "drop-reminders"


def test_cli_rejects_invalid_rules_with_the_rule_id(tmp_path):
    rules_path = _write(tmp_path / "rules.json",
                        _rules(_rule(rule_id="oops", when={"prop": "x", "op": "bogus"})))
    produced = _run_produce(rules_path, _sample_source())
    assert produced.returncode == 3
    assert "oops" in produced.stderr


def test_cli_write_uses_the_atomic_helper_and_retains_prior(tmp_path):
    rules_path = _write(tmp_path / "rules.json", _sample_rules())
    artifact = tmp_path / art.ARTIFACT_FILENAME

    first = _run_produce(rules_path, _sample_source(), artifact=artifact)
    assert first.returncode == 0, first.stderr
    assert not (tmp_path / art.PREV_ARTIFACT_FILENAME).exists()

    second_source = _source(_task(task_id="1", content="Call Vlad", priority=4,
                                  due={"date": LOGICAL_DAY}))
    second = _run_produce(rules_path, second_source, artifact=artifact)
    assert second.returncode == 0, second.stderr
    assert (tmp_path / art.PREV_ARTIFACT_FILENAME).is_file()
    assert not [p for p in tmp_path.iterdir() if p.suffix == ".tmp"]

    validated = _run_validate(str(artifact))
    assert validated.returncode == 0, validated.stdout + validated.stderr


def test_validate_artifact_cli_rejects_malformed(tmp_path):
    document = pr.build_artifact(
        _sample_source(), _sample_rules(),
        logical_day=LOGICAL_DAY, generated_at="2026-10-08T09:00:00-07:00",
        run_id="malformed",
    )
    document["schema"] = "wrong.schema"
    bad = _write(tmp_path / "bad.json", document)
    result = _run_validate(str(bad))
    assert result.returncode == 1
    assert "MALFORMED" in result.stdout
    assert "schema" in result.stdout
