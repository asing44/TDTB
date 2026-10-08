"""producer_rules.py — the deterministic artifact producer (A2).

A2 of the artifact-contract pivot
(``docs/plan/2026-10-08-capacities-first-migration/plan.md``, section
"ARCHITECTURE PIVOT"). A2 is the FIRST REAL PRODUCER: an agent-run skill
(``skills/tdtb-refresh/SKILL.md``) reads Todoist over MCP and hands the raw
result to THIS module, which turns it into the normalized planning artifact
``app/artifact_source.py`` consumes. The agent fetches; this code decides.

The split is deliberate and is the whole point of the slice:

  - the **agent** does what only an agent can do — speak MCP, resolve the
    operator's saved filters, projects and dates;
  - the **code** does what must never be judgement — apply the declarative
    rule set from ``~/.config/tdtb/producer-rules.json`` and emit the rows.

Rule evaluation is therefore pure: the same rules document and the same
fetched source JSON always yield byte-identical rows, so ``content_hash`` is
reproducible and a rerun is a no-op.

Rules file schema (``version`` 1)::

    {
      "version": 1,
      "rules": [
        {
          "id": "todoist-assigned",
          "source": "todoist",
          "when": {"all": [{"prop": "labels", "op": "in",
                            "values": ["@work"]}]},
          "assigned": true,
          "pool": false,
          "admit": true
        }
      ]
    }

``when`` is a RECURSIVE predicate: a combinator node ``{"all": [...]}``,
``{"any": [...]}`` or ``{"not": {...}}``, or a leaf ``{"prop", "op",
"values"}``. Ops: ``eq``, ``in``, ``exists``, ``truthy``, ``lt``, ``gt``,
``before``, ``after``, ``matches``. The value token ``"$today"`` resolves to
the run's ``logical_day`` so a rule file stays date-independent.

Rules are **first-match-wins in file order**: a record is decided by the first
rule whose ``source`` matches it and whose ``when`` holds. A matching rule with
``admit: false`` excludes the record (it is reported as dropped by that rule);
a record no rule matches is dropped too, with no rule id. **O1:** a rule marked
``pool: true`` emits ``assigned: false`` rows regardless of its ``assigned``.

The schema already carries Capacities rules (``source: "capacities"``,
``structure``, ``duration_prop``) so A3 needs no schema migration. A2 evaluates
Todoist only; a Capacities record arriving now is reported as ignored rather
than silently converted.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

import app_config
import artifact_source
import external_sources

# ---------------------------------------------------------------------------
# Contract constants
# ---------------------------------------------------------------------------

RULES_SCHEMA_VERSION = 1
PRODUCER_RULES_FILENAME = "producer-rules.json"

SOURCE_TODOIST = "todoist"
SOURCE_CAPACITIES = "capacities"

#: A2 evaluates Todoist rules only. Capacities evaluation is A3.
EVALUATED_SOURCES = frozenset({SOURCE_TODOIST})

OPS = frozenset({
    "eq", "in", "exists", "truthy", "lt", "gt", "before", "after", "matches",
})

#: Ops that require a non-empty ``values`` array.
VALUE_OPS = frozenset({"eq", "in", "lt", "gt", "before", "after", "matches"})

#: A leaf value equal to this token resolves to the run's ``logical_day``.
TODAY_TOKEN = "$today"

MAX_REPORTED_VIOLATIONS = 20
MAX_RECENT_DROPS = 20

#: Distinct from a present ``None`` (a real JSON null). ``exists`` is False for
#: both, but the separation keeps the intent explicit.
_MISSING = object()


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

def rules_path() -> Path:
    """The operator's rules file: ``<app home>/producer-rules.json``.

    A sibling of ``config.json`` under the same app home resolved by
    ``app_config.app_home()`` (honours ``TDTB_HOME``), so tests and the
    operator can relocate it without touching the repo."""
    return app_config.app_home() / PRODUCER_RULES_FILENAME


# ---------------------------------------------------------------------------
# Errors and result types
# ---------------------------------------------------------------------------

class RulesError(ValueError):
    """Raised by :func:`load_rules` when a rules file is missing or invalid.

    ``violations`` carries the explicit, rule-id-named messages so a caller
    (the CLI, the skill) can surface exactly which rule is broken."""

    def __init__(self, violations: list[str]):
        self.violations = list(violations)
        super().__init__("; ".join(self.violations) or "invalid producer rules")


@dataclass
class RuleStat:
    """Per-rule admission tally, reported to the operator."""

    id: str
    matched: int = 0
    admitted: int = 0
    dropped: int = 0


@dataclass
class Drop:
    """One excluded record and the rule (or absence of one) that excluded it."""

    name: str
    identity: str
    source: str
    rule: str | None
    reason: str


@dataclass
class EvaluationResult:
    """The deterministic outcome of applying one rule set to one fetch."""

    rows: list[dict[str, Any]] = field(default_factory=list)
    dropped: list[Drop] = field(default_factory=list)
    per_rule: list[RuleStat] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    counts: dict[str, dict[str, int]] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_rules(document: Any) -> list[str]:
    """Return every schema violation, each naming the offending rule id.

    An empty list means the rules document is well formed. Unknown rule keys
    are allowed so a future slice can add fields without a migration; every
    KNOWN field is type-checked, and every ``when`` node is checked
    recursively."""
    violations: list[str] = []

    def add(message: str) -> None:
        if len(violations) < MAX_REPORTED_VIOLATIONS:
            violations.append(message)

    if not isinstance(document, dict):
        return ["producer rules document is not a JSON object"]

    version = document.get("version")
    if type(version) is not int or version != RULES_SCHEMA_VERSION:
        add(f"version must be the integer {RULES_SCHEMA_VERSION} (got {version!r})")

    rules = document.get("rules")
    if not isinstance(rules, list):
        add("rules must be an array")
        return violations

    seen_ids: set[str] = set()
    for index, rule in enumerate(rules):
        label = f"rules[{index}]"
        if not isinstance(rule, dict):
            add(f"{label} must be an object")
            continue

        rule_id = rule.get("id")
        if not isinstance(rule_id, str) or not rule_id.strip():
            add(f"{label} is missing a non-empty string id")
            rule_label = label
        else:
            rule_label = f"rule {rule_id!r}"
            if rule_id in seen_ids:
                add(f"{rule_label} is a duplicate rule id")
            seen_ids.add(rule_id)

        if rule.get("source") not in artifact_source.VALID_ROW_SOURCES:
            add(
                f"{rule_label} has an invalid source {rule.get('source')!r} "
                f"(expected one of {sorted(artifact_source.VALID_ROW_SOURCES)})"
            )

        for key in ("admit", "pool", "assigned"):
            if key in rule and not isinstance(rule[key], bool):
                add(f"{rule_label} field {key!r} must be a boolean")
        for key in ("structure", "duration_prop"):
            if key in rule:
                value = rule[key]
                if not isinstance(value, str) or not value.strip():
                    add(f"{rule_label} field {key!r} must be a non-empty string")

        if "when" not in rule:
            add(f"{rule_label} is missing the required 'when' predicate")
        else:
            _validate_when(rule["when"], rule_label, "when", add)
    return violations


def _validate_when(node: Any, rule_label: str, path: str, add) -> None:
    """Validate one recursive predicate node."""
    if not isinstance(node, dict):
        add(f"{rule_label} {path} must be a predicate object")
        return

    combinators = [key for key in ("all", "any", "not") if key in node]
    has_leaf = "prop" in node or "op" in node
    if combinators and has_leaf:
        add(f"{rule_label} {path} mixes a combinator with a leaf")
        return
    if len(combinators) > 1:
        add(f"{rule_label} {path} must use exactly one of all/any/not")
        return

    if combinators:
        key = combinators[0]
        value = node[key]
        if key == "not":
            if not isinstance(value, dict):
                add(f"{rule_label} {path}.not must be a predicate object")
            else:
                _validate_when(value, rule_label, f"{path}.not", add)
            return
        if not isinstance(value, list) or not value:
            add(f"{rule_label} {path}.{key} must be a non-empty array of predicates")
            return
        for index, child in enumerate(value):
            _validate_when(child, rule_label, f"{path}.{key}[{index}]", add)
        return

    if not has_leaf:
        add(f"{rule_label} {path} is not a predicate (needs all/any/not or prop+op)")
        return

    prop = node.get("prop")
    if not isinstance(prop, str) or not prop.strip():
        add(f"{rule_label} {path}.prop must be a non-empty string")

    op = node.get("op")
    if op not in OPS:
        add(f"{rule_label} {path}.op must be one of {sorted(OPS)} (got {op!r})")
        return

    if op in VALUE_OPS:
        values = node.get("values")
        if not isinstance(values, list) or not values:
            add(f"{rule_label} {path}.values must be a non-empty array for op {op!r}")
    elif "values" in node and not isinstance(node["values"], list):
        add(f"{rule_label} {path}.values must be an array when present")


def load_rules(path: str | Path | None = None) -> dict[str, Any]:
    """Read and validate a rules file. Raises :class:`RulesError` on any
    problem, with every violation naming its rule id."""
    target = Path(path) if path is not None else rules_path()
    try:
        raw = target.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise RulesError([f"producer rules file not found at {target}"])
    except OSError as exc:
        raise RulesError([f"producer rules file unreadable at {target} ({exc})"])

    try:
        document = json.loads(raw)
    except ValueError as exc:
        raise RulesError([f"producer rules file at {target} is not valid JSON ({exc})"])

    violations = validate_rules(document)
    if violations:
        raise RulesError([f"{target}: {message}" for message in violations])
    return document


# ---------------------------------------------------------------------------
# Canonical rule-set hash
# ---------------------------------------------------------------------------

def rule_set_hash(rules_document: dict[str, Any]) -> str:
    """The ``admission.rule_set_hash``: sha256 over canonical rules JSON."""
    canonical = json.dumps(
        rules_document, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------

def evaluate(
    rules_document: dict[str, Any],
    source_json: Any,
    *,
    logical_day: str,
) -> EvaluationResult:
    """Apply ``rules_document`` to fetched source JSON. Deterministic.

    ``source_json`` is keyed by source name; each value is either a record list
    or an object carrying one under ``tasks``/``records``/``items``/``objects``
    plus optional ``status``/``read_at``/``warnings``. Records are deduplicated
    by identity (first wins) and emitted names are disambiguated, so the
    resulting ``rows`` always satisfy the A1 duplicate-name rule."""
    violations = validate_rules(rules_document)
    if violations:
        raise RulesError(violations)
    if not isinstance(source_json, dict):
        raise RulesError(["fetched source JSON must be an object keyed by source"])

    rules = rules_document["rules"]
    per_rule = [RuleStat(str(rule["id"])) for rule in rules]
    stats_by_id = {stat.id: stat for stat in per_rule}
    result = EvaluationResult(per_rule=per_rule)

    matched_rows: list[dict[str, Any]] = []
    seen_identity: set[str] = set()

    for source in sorted(source_json.keys()):
        entry = source_json[source]
        records = _records_for(entry)
        counts = result.counts.setdefault(
            source, {"records": 0, "admitted": 0, "dropped": 0}
        )

        if source not in EVALUATED_SOURCES:
            counts["records"] = len(records)
            if records:
                result.warnings.append(
                    f"source {source!r} is not evaluated in A2 "
                    f"({len(records)} record(s) ignored); Capacities evaluation lands in A3"
                )
            continue

        for record in records:
            if not isinstance(record, dict):
                result.warnings.append(f"source {source!r} carries a non-object record; skipped")
                continue
            identity = _record_identity(source, record)
            if identity in seen_identity:
                result.warnings.append(f"duplicate {source} record {identity!r} ignored")
                continue
            seen_identity.add(identity)
            counts["records"] += 1
            name = _record_name(source, record)
            rule = _first_matching_rule(rules, source, record, logical_day)

            if rule is None:
                _record_drop(result, counts, name, identity, source, None, "no rule matched")
                continue

            stat = stats_by_id[rule["id"]]
            stat.matched += 1
            if rule.get("admit", True) is False:
                stat.dropped += 1
                _record_drop(result, counts, name, identity, source, rule["id"], "excluded by rule")
                continue

            assigned = False if rule.get("pool") is True else bool(rule.get("assigned", True))
            matched_rows.append(_to_artifact_row(source, record, assigned, identity))
            stat.admitted += 1
            counts["admitted"] += 1

    # Name collisions break the A1 duplicate-name rule; disambiguate exactly as
    # the live path does before the rows ever reach the artifact.
    result.rows = external_sources.disambiguate_names([], matched_rows)
    return result


def _record_drop(result, counts, name, identity, source, rule_id, reason) -> None:
    result.dropped.append(
        Drop(name=name, identity=identity, source=source, rule=rule_id, reason=reason)
    )
    counts["dropped"] += 1


def _first_matching_rule(rules, source, record, logical_day):
    for rule in rules:
        if rule.get("source") != source:
            continue
        if _matches(rule["when"], record, logical_day):
            return rule
    return None


def _matches(node: dict[str, Any], record: dict[str, Any], logical_day: str) -> bool:
    if "all" in node:
        return all(_matches(child, record, logical_day) for child in node["all"])
    if "any" in node:
        return any(_matches(child, record, logical_day) for child in node["any"])
    if "not" in node:
        return not _matches(node["not"], record, logical_day)
    return _leaf_matches(node, record, logical_day)


def _leaf_matches(node: dict[str, Any], record: dict[str, Any], logical_day: str) -> bool:
    actual = _lookup(record, node["prop"])
    op = node["op"]
    values = [_resolve_value(value, logical_day) for value in node.get("values") or []]

    if op == "exists":
        return actual is not _MISSING and actual is not None
    if op == "truthy":
        return bool(actual) if actual is not _MISSING else False
    if actual is _MISSING:
        return False
    if op == "eq":
        return actual == values[0]
    if op == "in":
        if isinstance(actual, (list, tuple)):
            return any(item in values for item in actual)
        return actual in values
    if op in ("lt", "gt"):
        left, right = _as_number(actual), _as_number(values[0])
        if left is None or right is None:
            return False
        return left < right if op == "lt" else left > right
    if op in ("before", "after"):
        left, right = _as_date(actual), _as_date(values[0])
        if left is None or right is None:
            return False
        return left < right if op == "before" else left > right
    if op == "matches":
        text = _as_text(actual)
        for value in values:
            try:
                if re.search(str(value), text):
                    return True
            except re.error:
                continue
        return False
    return False


def _to_artifact_row(
    source: str, record: dict[str, Any], assigned: bool, identity: str
) -> dict[str, Any]:
    """Convert one source record into the canonical artifact row.

    Todoist reuses ``external_sources._to_item`` — the app's own single
    definition of the row shape — and adds the contract-required
    ``identity``. A3 adds the Capacities converter."""
    if source == SOURCE_TODOIST:
        row = external_sources._to_item(record, assigned)
        row["identity"] = identity
        return row
    raise RulesError([f"no row converter for source {source!r}"])


# ---------------------------------------------------------------------------
# Building the artifact document
# ---------------------------------------------------------------------------

def build_artifact(
    source_json: Any,
    rules_document: dict[str, Any],
    *,
    logical_day: str,
    generated_at: str,
    producer_name: str = "tdtb-refresh",
    producer_version: str = "0.1.0",
    run_id: str = "run",
    max_recent_drops: int = MAX_RECENT_DROPS,
) -> dict[str, Any]:
    """Assemble the complete ``tdtb.planning-artifact`` v1 document.

    ``content_hash`` is computed with the A1 helper over exactly the
    ``{sources, rows}`` written, so :func:`artifact_source.load_artifact`
    verifies it. The result passes :func:`artifact_source.validate_artifact`;
    callers still re-check before writing (the CLI does)."""
    result = evaluate(rules_document, source_json, logical_day=logical_day)
    sources = _sources_block(source_json, result, generated_at)
    rows = result.rows

    admission = {
        "rule_set_hash": rule_set_hash(rules_document),
        "admitted": [str(row.get("name") or "") for row in rows],
        "dropped": [drop.name for drop in result.dropped],
        "per_rule": [
            {
                "id": stat.id,
                "matched": stat.matched,
                "admitted": stat.admitted,
                "dropped": stat.dropped,
            }
            for stat in result.per_rule
        ],
        "recent_drops": [
            {
                "name": drop.name,
                "identity": drop.identity,
                "source": drop.source,
                "rule": drop.rule,
                "reason": drop.reason,
            }
            for drop in result.dropped[-max_recent_drops:]
        ],
        "warnings": list(result.warnings),
    }

    document: dict[str, Any] = {
        "schema": artifact_source.ARTIFACT_SCHEMA,
        "version": artifact_source.ARTIFACT_VERSION,
        "generated_at": generated_at,
        "logical_day": logical_day,
        "producer": {
            "name": producer_name,
            "version": producer_version,
            "run_id": run_id,
        },
        "sources": sources,
        "rows": rows,
        "admission": admission,
    }
    document["content_hash"] = artifact_source.compute_content_hash(sources, rows)
    return document


def _sources_block(
    source_json: dict[str, Any], result: EvaluationResult, generated_at: str
) -> dict[str, Any]:
    """The contract's ``sources`` block, one entry per fetched source.

    ``rows``/``dropped`` are post-rule tallies (an A1 fixture uses ``rows`` as
    the emitted count), ``deferred`` is always 0 until a slice defers."""
    block: dict[str, Any] = {}
    for name, entry in source_json.items():
        counts = result.counts.get(name, {"records": 0, "admitted": 0, "dropped": 0})
        if isinstance(entry, dict):
            status = entry.get("status", "ok")
            read_at = entry.get("read_at") or generated_at
            warnings = list(entry.get("warnings") or [])
        else:
            status = "ok"
            read_at = generated_at
            warnings = []
        if status not in artifact_source.VALID_SOURCE_STATUS:
            warnings.append(f"unrecognized source status {status!r} coerced to 'ok'")
            status = "ok"
        block[name] = {
            "status": status,
            "read_at": read_at,
            "rows": counts["admitted"],
            "dropped": counts["dropped"],
            "deferred": 0,
            "warnings": warnings,
        }
    return block


# ---------------------------------------------------------------------------
# Small deterministic helpers
# ---------------------------------------------------------------------------

def _records_for(entry: Any) -> list[Any]:
    if isinstance(entry, list):
        return list(entry)
    if isinstance(entry, dict):
        for key in ("tasks", "records", "items", "objects"):
            value = entry.get(key)
            if isinstance(value, list):
                return list(value)
    return []


def _record_identity(source: str, record: dict[str, Any]) -> str:
    if source == SOURCE_TODOIST:
        todoist_id = record.get("id")
        if todoist_id is not None and str(todoist_id).strip():
            return f"todoist:{todoist_id}"
        return f"todoist:name:{(record.get('content') or '').strip()}"
    object_id = record.get("id") or record.get("objectId")
    if object_id is not None and str(object_id).strip():
        return f"capacities:{object_id}"
    return f"capacities:name:{(record.get('title') or record.get('name') or '').strip()}"


def _record_name(source: str, record: dict[str, Any]) -> str:
    if source == SOURCE_TODOIST:
        return str(record.get("content") or "").strip()
    return str(record.get("title") or record.get("name") or "").strip()


def _lookup(record: Any, prop: str) -> Any:
    cursor = record
    for part in str(prop).split("."):
        if isinstance(cursor, dict):
            if part not in cursor:
                return _MISSING
            cursor = cursor[part]
        elif isinstance(cursor, (list, tuple)) and part.lstrip("-").isdigit():
            index = int(part)
            if index < 0 or index >= len(cursor):
                return _MISSING
            cursor = cursor[index]
        else:
            return _MISSING
    return cursor


def _resolve_value(value: Any, logical_day: str) -> Any:
    return logical_day if value == TODAY_TOKEN else value


def _as_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _as_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _as_text(value: Any) -> str:
    if isinstance(value, (list, tuple)):
        return " ".join(str(item) for item in value)
    return "" if value is None else str(value)
