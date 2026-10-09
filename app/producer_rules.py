"""producer_rules.py — the deterministic artifact producer (A2/A3).

A2–A3 of the artifact-contract pivot
(``docs/plan/2026-10-08-capacities-first-migration/plan.md``, section
"ARCHITECTURE PIVOT"). A2 is the FIRST REAL PRODUCER: an agent-run skill
(``skills/tdtb-refresh/SKILL.md``) reads Todoist and Capacities over MCP and
hands the raw result to THIS module, which turns it into the normalized
planning artifact ``app/artifact_source.py`` consumes. The agent fetches;
this code decides.

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

A3 adds Capacities rules (``source: "capacities"`` with ``structure`` and an
optional ``duration_prop``) and the PACED READ CURSOR at the bottom of this
module. A Capacities record is a listed object plus the typed ``properties``
the agent read for it; ``structure`` scopes the rule, and the predicate reads
plain values (typed property payloads are flattened deterministically here,
never by the agent). A Capacities row carries the canonical provider fields
(``capacities_id``, ``capacities_structure_id``, ``capacities_space_id``) and
the ``capacities:{space}:{structure}:{object}`` identity the live adapter
defines.

The cursor is a COVERAGE record, not change detection: Capacities exposes no
guaranteed ``updatedAt`` (only a property type when the structure happens to
define one), so the cache records what has been read and lets a partial run
resume. It cannot tell whether a cached object changed.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

import app_config
import artifact_source
import capacities_cache_io
import external_sources

# ---------------------------------------------------------------------------
# Contract constants
# ---------------------------------------------------------------------------

RULES_SCHEMA_VERSION = 1
PRODUCER_RULES_FILENAME = "producer-rules.json"

SOURCE_TODOIST = "todoist"
SOURCE_CAPACITIES = "capacities"
#: Agent-fetched habit data (tasks + today's completions). Not a row source:
#: the producer folds it into the artifact's top-level ``habits`` block.
SOURCE_HABITS = "habits"

#: A3 evaluates Todoist and Capacities rules.
EVALUATED_SOURCES = frozenset({SOURCE_TODOIST, SOURCE_CAPACITIES})

#: Sources that are consumed for something other than rows. They contribute no
#: rows, so they are not reported as an unimplemented row source.
NON_ROW_SOURCES = frozenset({SOURCE_HABITS})

#: Fallbacks for the habit estimate when the source JSON omits the knobs.
#: These mirror the app's historical ``habits.*`` defaults.
DEFAULT_HABIT_MINUTES = 4
DEFAULT_HABIT_GRAIN_MINUTES = 15

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

        # A Capacities rule must name the structure it scopes: without it the
        # rule could silently match an object of any structure.
        if rule.get("source") == SOURCE_CAPACITIES:
            structure = rule.get("structure")
            if not isinstance(structure, str) or not structure.strip():
                add(f"{rule_label} has source 'capacities' but no non-empty 'structure'")

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
            if source in NON_ROW_SOURCES:
                continue
            if records:
                result.warnings.append(
                    f"source {source!r} is not evaluated in A3 "
                    f"({len(records)} record(s) ignored)"
                )
            continue

        source_space = ""
        if isinstance(entry, dict):
            source_space = str(entry.get("space_id") or entry.get("spaceId") or "").strip()
        project_names = _project_names(entry) if source == SOURCE_TODOIST else {}

        for record in records:
            if not isinstance(record, dict):
                result.warnings.append(f"source {source!r} carries a non-object record; skipped")
                continue
            if source == SOURCE_TODOIST:
                record = normalize_task(record, project_names)
            space_id = source_space or str(record.get("spaceId") or "").strip()
            identity = _record_identity(source, record, space_id)
            if identity in seen_identity:
                result.warnings.append(f"duplicate {source} record {identity!r} ignored")
                continue
            seen_identity.add(identity)
            counts["records"] += 1
            name = _record_name(source, record)
            view = _predicate_record(source, record)
            rule = _first_matching_rule(rules, source, view, logical_day, record)

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
            matched_rows.append(
                _to_artifact_row(
                    source, record, assigned, identity, rule=rule, space_id=space_id
                )
            )
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


def _first_matching_rule(rules, source, view, logical_day, record):
    for rule in rules:
        if rule.get("source") != source:
            continue
        if source == SOURCE_CAPACITIES and not _structure_in_scope(rule, record):
            continue
        if _matches(rule["when"], view, logical_day):
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
    source: str,
    record: dict[str, Any],
    assigned: bool,
    identity: str,
    *,
    rule: dict[str, Any] | None = None,
    space_id: str = "",
) -> dict[str, Any]:
    """Convert one source record into the canonical artifact row.

    Todoist reuses ``external_sources._to_item`` — the app's own single
    definition of the row shape — and adds the contract-required
    ``identity``. Capacities emits the canonical provider fields the live
    adapter defines (``capacities_adapter.py`` row projection)."""
    if source == SOURCE_TODOIST:
        row = external_sources._to_item(record, assigned)
        row["identity"] = identity
        return row
    if source == SOURCE_CAPACITIES:
        return _capacities_row(record, assigned, identity, rule or {}, space_id)
    raise RulesError([f"no row converter for source {source!r}"])


# ---------------------------------------------------------------------------
# Capacities projection (A3)
# ---------------------------------------------------------------------------

#: Duration (minutes) the live adapter assumes when a rule names no duration
#: property or the property is absent. Kept identical so the artifact and the
#: adapter agree on a default row.
DEFAULT_CAPACITIES_DURATION = 30

#: A planning block is 30 minutes, exactly as the live adapter computes it.
MINUTES_PER_BLOCK = 30


def _capacities_row(
    record: dict[str, Any],
    assigned: bool,
    identity: str,
    rule: dict[str, Any],
    space_id: str,
) -> dict[str, Any]:
    """Build the canonical Capacities artifact row.

    ``capacities_id``, ``capacities_structure_id`` and ``capacities_space_id``
    plus ``identity`` mirror ``capacities_adapter``'s projection so a row
    produced here is interchangeable with the live reader's."""
    object_id = str(record.get("id") or record.get("objectId") or "").strip()
    structure_id = str(
        record.get("structureId") or record.get("structure") or rule.get("structure") or ""
    ).strip()
    title = _record_name(SOURCE_CAPACITIES, record)
    duration = _capacities_duration(record, rule.get("duration_prop"))
    blocks: int | float = duration / MINUTES_PER_BLOCK
    if isinstance(blocks, float) and blocks.is_integer():
        blocks = int(blocks)
    return {
        "id": title,
        "name": title,
        "path": f"capacities://{space_id}/{object_id}",
        "identity": identity,
        "source": SOURCE_CAPACITIES,
        "types": [structure_id] if structure_id else [],
        "urgency": None,
        "deadline": None,
        "priority_score": 0,
        "assigned": assigned,
        "duration": duration,
        "duration_minutes": duration,
        "blocks": blocks,
        "capacities_id": object_id,
        "capacities_space_id": space_id,
        "capacities_structure_id": structure_id,
    }


def _capacities_duration(record: dict[str, Any], duration_prop: Any) -> int | float:
    """Read the named duration property, flattened, or the default.

    A missing, non-numeric or negative value falls back to
    :data:`DEFAULT_CAPACITIES_DURATION` (matching the adapter's default), so a
    malformed property never fails a whole source read."""
    if not isinstance(duration_prop, str) or not duration_prop.strip():
        return DEFAULT_CAPACITIES_DURATION
    properties = record.get("properties")
    if not isinstance(properties, dict) or duration_prop not in properties:
        return DEFAULT_CAPACITIES_DURATION
    number = _as_number(_flatten_property(properties[duration_prop]))
    if number is None or number < 0:
        return DEFAULT_CAPACITIES_DURATION
    return int(number) if float(number).is_integer() else number


def _predicate_record(source: str, record: dict[str, Any]) -> dict[str, Any]:
    """The record the predicate reads. Capacities typed payloads are
    flattened to plain values so the rule vocabulary stays uniform with
    Todoist; the raw record is still what the row is built from."""
    if source == SOURCE_CAPACITIES:
        return _capacities_predicate_record(record)
    return record


def _capacities_predicate_record(record: dict[str, Any]) -> dict[str, Any]:
    view: dict[str, Any] = {}
    properties = record.get("properties")
    if isinstance(properties, dict):
        for key, payload in properties.items():
            view[str(key)] = _flatten_property(payload)
    for key in ("id", "objectId", "structureId", "spaceId", "title", "name", "path"):
        if key in record:
            view[key] = record[key]
    for key in ("collections", "tags"):
        if key in record:
            view[key] = _flatten_members(record[key])
    return view


def _flatten_property(payload: Any) -> Any:
    """Flatten one Capacities typed property payload to a plain value.

    Mirrors the live adapter's payload semantics for the types a predicate
    can meaningfully compare: text/number/boolean/url yield their value, a
    date yields its start, and label/entity yield their names or titles."""
    if not isinstance(payload, dict):
        return payload
    kind = payload.get("type")
    if not isinstance(kind, str) or kind not in payload:
        return payload
    body = payload[kind]
    if kind in {"title", "text", "richText", "number", "boolean", "url"}:
        if isinstance(body, dict) and "value" in body:
            return body["value"]
        return body
    if kind == "date":
        return body.get("start") if isinstance(body, dict) else body
    if kind in {"label", "entity"}:
        return _flatten_members(body)
    return body


def _flatten_members(value: Any) -> Any:
    """Flatten a label/entity/tag/collection list to plain name tokens."""
    if isinstance(value, dict):
        value = [value]
    if not isinstance(value, list):
        return value
    tokens: list[Any] = []
    for entry in value:
        if isinstance(entry, dict):
            tokens.append(entry.get("name") or entry.get("title") or entry.get("id"))
        else:
            tokens.append(entry)
    return [token for token in tokens if token is not None]


def _structure_in_scope(rule: dict[str, Any], record: dict[str, Any]) -> bool:
    """Whether a Capacities record belongs to the rule's named structure.

    The rule may name the structure by its id or its title; the record may
    carry either, so both sides are compared token-normalized."""
    target = _normalized(rule.get("structure"))
    if not target:
        return False
    for key in ("structureId", "structure", "structureTitle", "structureName"):
        if _normalized(record.get(key)) == target:
            return True
    return False


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
    habits = compute_habit_summary(
        source_json.get(SOURCE_HABITS), logical_day=logical_day
    )

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
        "habits": habits,
    }
    document["content_hash"] = artifact_source.compute_content_hash(sources, rows)
    return document


def _sources_block(
    source_json: dict[str, Any], result: EvaluationResult, generated_at: str
) -> dict[str, Any]:
    """The contract's ``sources`` block, one entry per fetched source.

    ``rows``/``dropped`` are post-rule tallies (an A1 fixture uses ``rows`` as
    the emitted count). ``deferred`` rides the source entry: the paced read
    cursor reports how many listed objects this run could not read, and the
    agent carries that count (and a ``partial`` status) into the source JSON."""
    block: dict[str, Any] = {}
    for name, entry in source_json.items():
        counts = result.counts.get(name, {"records": 0, "admitted": 0, "dropped": 0})
        deferred = 0
        if isinstance(entry, dict):
            status = entry.get("status", "ok")
            read_at = entry.get("read_at") or generated_at
            warnings = list(entry.get("warnings") or [])
            candidate = entry.get("deferred", 0)
            if isinstance(candidate, int) and not isinstance(candidate, bool) and candidate >= 0:
                deferred = candidate
            elif candidate not in (0, None):
                warnings.append(f"ignored non-integer source deferred {candidate!r}")
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
            "deferred": deferred,
            "warnings": warnings,
        }
    return block


# ---------------------------------------------------------------------------
# Habit summary (agent-fetched -> deterministic block)
# ---------------------------------------------------------------------------

def _habit_duration_minutes(task: dict[str, Any]) -> int | None:
    """Minutes a habit task is worth, or None when unset.

    Accepts the REST ``{"amount": N, "unit": "minute"|"day"}`` shape and the
    MCP ``"5m"/"1h30m"/"2d"`` string. A zero/absent/unparseable duration is
    None so the caller applies the per-habit fallback."""
    duration = task.get("duration")
    if isinstance(duration, str):
        duration = _mcp_duration(duration)
    if isinstance(duration, dict):
        amount = duration.get("amount")
        unit = duration.get("unit")
        if type(amount) is int and amount > 0:
            if unit == "day":
                return amount * 24 * 60
            if unit == "minute":
                return amount
    return None


def _completion_local_date(item: dict[str, Any]) -> str | None:
    """The local calendar date a completion happened, when Todoist sent one.

    ``todoist_find-completed-tasks`` defaults to a WEEK-long window, so the
    fetch alone cannot scope "done today" — the producer has to. A completion
    carries ``completedAt`` as a UTC instant while the artifact's ``logical_day``
    is a local date, so the instant is converted to local time first."""
    raw = item.get("completedAt") or item.get("completed_at")
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        parsed = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone()
    return parsed.date().isoformat()


def _habit_completed_tokens(
    entry: dict[str, Any], logical_day: str | None = None
) -> tuple[set[str], set[str]]:
    """Collect the identifiers of today's completions.

    A completion may be an id-bearing object (``task_id``/``taskId``/``id``)
    or a bare id/name string; Todoist may report a recurring habit's completed
    occurrence by content rather than the parent task id, so both ids and
    names are matched.

    A completion dated to another day is SKIPPED. The fetch window is a week by
    default, so without this a habit finished on Tuesday would read as done on
    Friday. An undated completion is still counted, because the skill is
    instructed to fetch the logical day's completions."""
    ids: set[str] = set()
    names: set[str] = set()
    completed = entry.get("completed")
    if not isinstance(completed, list):
        return ids, names
    for item in completed:
        if isinstance(item, str):
            ids.add(item.strip())
            continue
        if not isinstance(item, dict):
            continue
        day = _completion_local_date(item)
        if day is not None and logical_day is not None and day != logical_day:
            continue
        for key in ("task_id", "taskId", "id"):
            value = item.get(key)
            if value is not None:
                ids.add(str(value).strip())
        content = item.get("content") or item.get("name")
        if isinstance(content, str) and content.strip():
            names.add(content.strip())
    return ids, names


def compute_habit_summary(
    entry: Any, logical_day: str | None = None
) -> dict[str, int]:
    """Compute the ``{total, done, outstanding, est_minutes}`` habit block.

    Deterministic and total: a missing/malformed entry yields the zeroed block
    rather than raising. ``done`` counts tasks a completion references **on the
    logical day** (see :func:`_habit_completed_tokens` — the fetch window is a
    week by default, so the day is scoped here, not by the fetch).
    ``est_minutes`` sums only the OUTSTANDING tasks' durations (fallback
    ``fallback_minutes_per_habit`` where unset), rounded UP to the
    ``round_to_minutes`` grain — the same semantics the vault read used."""
    empty = dict(artifact_source.HABITS_EMPTY)
    if not isinstance(entry, dict):
        return empty
    tasks = entry.get("tasks")
    if not isinstance(tasks, list):
        tasks = _records_for(entry)
    fallback = entry.get("fallback_minutes_per_habit", DEFAULT_HABIT_MINUTES)
    if type(fallback) is not int or fallback <= 0:
        fallback = DEFAULT_HABIT_MINUTES
    grain = entry.get("round_to_minutes", DEFAULT_HABIT_GRAIN_MINUTES)
    if type(grain) is not int or grain <= 0:
        grain = DEFAULT_HABIT_GRAIN_MINUTES

    done_ids, done_names = _habit_completed_tokens(entry, logical_day)
    total = done = 0
    outstanding_minutes = 0
    for task in tasks:
        if not isinstance(task, dict):
            continue
        total += 1
        task_id = task.get("id")
        name = task.get("content") or task.get("name") or task.get("title")
        is_done = (
            (task_id is not None and str(task_id).strip() in done_ids)
            or (isinstance(name, str) and name.strip() in done_names)
        )
        if is_done:
            done += 1
            continue
        outstanding_minutes += _habit_duration_minutes(task) or fallback
    outstanding = total - done
    est = math.ceil(outstanding_minutes / grain) * grain if outstanding_minutes else 0
    return {
        "total": total,
        "done": done,
        "outstanding": outstanding,
        "est_minutes": est,
    }


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


def _project_names(entry: Any) -> dict[str, str]:
    """Map Todoist ``projectId -> project name`` from an optional project list.

    A Todoist source entry MAY carry ``projects`` alongside its ``tasks``: the
    list ``todoist_find-projects`` returns, each entry an object with ``id``
    and ``name``. The map lets a hand-editable rule match a readable project
    name instead of a raw UUID. A missing or malformed list yields ``{}``, so a
    source JSON without a project list normalizes exactly as before."""
    if not isinstance(entry, dict):
        return {}
    projects = entry.get("projects")
    if not isinstance(projects, list):
        return {}
    names: dict[str, str] = {}
    for project in projects:
        if not isinstance(project, dict):
            continue
        project_id = project.get("id")
        name = project.get("name")
        if project_id is None or not isinstance(name, str):
            continue
        key = str(project_id).strip()
        if key:
            names[key] = name
    return names


def _record_identity(source: str, record: dict[str, Any], space_id: str = "") -> str:
    if source == SOURCE_TODOIST:
        todoist_id = record.get("id")
        if todoist_id is not None and str(todoist_id).strip():
            return f"todoist:{todoist_id}"
        return f"todoist:name:{(record.get('content') or '').strip()}"
    object_id = record.get("id") or record.get("objectId")
    if object_id is not None and str(object_id).strip():
        structure_id = str(record.get("structureId") or record.get("structure") or "").strip()
        return f"capacities:{space_id}:{structure_id}:{object_id}"
    return f"capacities:name:{(record.get('title') or record.get('name') or '').strip()}"


def _record_name(source: str, record: dict[str, Any]) -> str:
    if source == SOURCE_TODOIST:
        return str(record.get("content") or "").strip()
    return str(record.get("title") or record.get("name") or "").strip()


# ---------------------------------------------------------------------------
# Todoist shape adapter (MCP -> REST)
# ---------------------------------------------------------------------------

#: MCP duration tokens, e.g. ``"5m"``, ``"1h"``, ``"1h30m"``, ``"2d"``.
_DURATION_TOKEN_RE = re.compile(r"(\d+)\s*([dhm])", re.IGNORECASE)
#: MCP priority tokens, e.g. ``"p4"`` (4 = highest, matching REST int).
_PRIORITY_TOKEN_RE = re.compile(r"p(\d+)", re.IGNORECASE)


def _is_mcp_task(record: dict[str, Any]) -> bool:
    """Whether a Todoist record is the MCP shape rather than REST.

    The MCP reader sends flat ``dueDate`` / ``deadlineDate`` / ``recurring``
    keys and a string ``priority`` (``"p4"``); the REST shape nests due under
    ``due`` and sends an int priority. A record carrying any of the flat MCP
    keys — or a string priority — is the MCP shape."""
    for key in ("dueDate", "deadlineDate", "recurring"):
        if key in record:
            return True
    return isinstance(record.get("priority"), str)


def _mcp_duration(value: Any) -> dict[str, Any] | None:
    """Parse an MCP duration string to the REST ``{unit, amount}`` shape.

    ``"5m"``/``"1h"``/``"1h30m"`` yield ``{unit: "minute", amount: N}``;
    a pure day token (``"2d"``) keeps the explicit day unit. Any unparseable
    or non-positive value returns ``None`` so the original is left intact."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    tokens = _DURATION_TOKEN_RE.findall(text)
    if not tokens:
        return None
    if len(tokens) == 1 and tokens[0][1].lower() == "d":
        amount = int(tokens[0][0])
        return {"unit": "day", "amount": amount} if amount > 0 else None
    minutes = 0
    for amount, unit in tokens:
        unit = unit.lower()
        if unit == "m":
            minutes += int(amount)
        elif unit == "h":
            minutes += int(amount) * 60
        elif unit == "d":
            minutes += int(amount) * 24 * 60
    return {"unit": "minute", "amount": minutes} if minutes > 0 else None


def _annotate_project_name(
    record: dict[str, Any], projects: dict[str, str]
) -> dict[str, Any]:
    """Add ``projectName`` when the task's ``projectId`` resolves in ``projects``.

    Purely additive and deterministic: an empty map or a task whose
    ``projectId`` is missing or absent from the map returns the record
    unchanged, so a source JSON without a project list stays byte-identical."""
    if not projects:
        return record
    project_id = record.get("projectId")
    if project_id is None:
        return record
    name = projects.get(str(project_id).strip())
    if not name:
        return record
    annotated = dict(record)
    annotated["projectName"] = name
    return annotated


def normalize_task(
    record: dict[str, Any], projects: dict[str, str] | None = None
) -> dict[str, Any]:
    """Adapt one Todoist task to the stable shape rules and rows both read.

    ``skills/tdtb-refresh/SKILL.md`` fetches Todoist over MCP, whose task shape
    differs from the REST shape ``external_sources._to_item`` was written for:
    flat ``dueDate``/``deadlineDate``, string ``priority`` (``"p4"``), a
    ``"5m"``/``"1h"`` duration string, and a top-level ``recurring`` flag. This
    fills the nested REST keys (``due``, ``duration``) so predicate and row
    building see one shape while preserving the original flat MCP keys for
    rules that address them directly.

    ``projects`` is the source's optional ``projectId -> name`` map (built by
    :func:`_project_names`). When supplied, a record whose ``projectId``
    resolves gains ``projectName`` so a rule can match the readable project
    name; an absent list or an unknown id leaves the record unchanged.

    A REST-shaped record is returned unchanged. Idempotent: normalizing an
    already-normalized record yields an equal record."""
    if not isinstance(record, dict):
        return record
    project_names = projects if isinstance(projects, dict) else {}
    if not _is_mcp_task(record):
        return _annotate_project_name(record, project_names)
    normalized = dict(record)

    priority = normalized.get("priority")
    if isinstance(priority, str):
        match = _PRIORITY_TOKEN_RE.fullmatch(priority.strip())
        if match:
            normalized["priority"] = int(match.group(1))

    due = dict(normalized.get("due")) if isinstance(normalized.get("due"), dict) else {}
    due_date = normalized.get("dueDate")
    deadline = normalized.get("deadlineDate")
    if isinstance(due_date, str) and due_date.strip():
        text = due_date.strip()
        due["date"] = text.split("T", 1)[0][:10]
        if "T" in text:
            due["datetime"] = text
    elif isinstance(deadline, str) and deadline.strip():
        # TDTB carries a single ``deadline``; a MCP deadline stands in when no
        # due date is present. The flat key is preserved so a rule can read it.
        due["date"] = deadline.strip()[:10]

    if "recurring" in normalized:
        due["is_recurring"] = bool(normalized.get("recurring"))

    duration = normalized.get("duration")
    if isinstance(duration, str):
        parsed = _mcp_duration(duration)
        if parsed is not None:
            normalized["duration"] = parsed

    if due:
        normalized["due"] = due
    return _annotate_project_name(normalized, project_names)


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


def _normalized(value: Any) -> str:
    return " ".join(_as_text(value).casefold().split())


# ---------------------------------------------------------------------------
# The paced read cursor (A3)
# ---------------------------------------------------------------------------
#
# This is a COVERAGE cursor, NOT change detection. The Capacities API exposes
# no guaranteed ``updatedAt`` (it is only a property type if the structure
# happens to define one), so the cache can say only *what has already been
# read*. It cannot tell whether a cached object changed. An object already in
# the cursor is skipped on the next listing, which lets a run that exhausted
# its read budget resume instead of restarting.
#
# Pacing is a rate-limit valve, not a tunable: the Capacities API allows 30
# requests / 60 seconds and the listing plus one shape read per structure
# spend the remainder, so 24 content reads per window keeps headroom under the
# limit (see the D5 correction in the plan).

PRODUCER_CACHE_FILENAME = "producer-cache.json"
PRODUCER_CACHE_VERSION = 1

#: Content reads per window. Headroom under the API's 30 requests / 60s.
DEFAULT_CONTENT_READ_BUDGET = 24
DEFAULT_CONTENT_READ_WINDOW_SECONDS = 60


class CursorError(ValueError):
    """Raised by the cursor helpers for an unusable argument (not for a
    missing/corrupt cache, which degrades to an empty cursor)."""


def producer_cache_path() -> Path:
    """The paced read cursor: ``state_dir()/producer-cache.json``."""
    return app_config.state_dir() / PRODUCER_CACHE_FILENAME


@dataclass
class ReadPlan:
    """Which listed objects still need a content read, and what is deferred.

    ``deferred`` counts listed objects that the cursor does not already cover
    and that did not fit this window's read budget. A non-zero ``deferred``
    means the source is ``partial`` and the next run resumes from here."""

    need_read: list[str] = field(default_factory=list)
    already_read: int = 0
    deferred: int = 0
    status: str = "ok"
    budget: int = DEFAULT_CONTENT_READ_BUDGET
    window_seconds: int = DEFAULT_CONTENT_READ_WINDOW_SECONDS
    window_remaining: int = DEFAULT_CONTENT_READ_BUDGET
    listed: int = 0
    cache_entries: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "need_read": list(self.need_read),
            "already_read": self.already_read,
            "deferred": self.deferred,
            "budget": self.budget,
            "window_seconds": self.window_seconds,
            "window_remaining": self.window_remaining,
            "listed": self.listed,
            "cache_entries": self.cache_entries,
        }


def _empty_cache() -> dict[str, Any]:
    return {"version": PRODUCER_CACHE_VERSION, "objects": {}, "window": {}}


def load_cache(path: str | Path | None = None) -> dict[str, Any]:
    """Read the cursor. A missing, unreadable or corrupt file degrades to an
    empty cursor (a re-read), never a failed run. Never raises."""
    target = Path(path) if path is not None else producer_cache_path()
    try:
        document = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _empty_cache()
    if not isinstance(document, dict):
        return _empty_cache()
    objects = document.get("objects")
    window = document.get("window")
    return {
        "version": PRODUCER_CACHE_VERSION,
        "objects": objects if isinstance(objects, dict) else {},
        "window": window if isinstance(window, dict) else {},
    }


def save_cache(cache: dict[str, Any], path: str | Path | None = None) -> Path:
    """Persist the cursor atomically, reusing the shared cache IO helper."""
    target = Path(path) if path is not None else producer_cache_path()
    capacities_cache_io.atomic_write_json(target, cache)
    return target


def _listed_entry(entry: Any) -> dict[str, str]:
    if isinstance(entry, dict):
        return {
            "id": str(entry.get("id") or entry.get("objectId") or "").strip(),
            "space_id": str(entry.get("space_id") or entry.get("spaceId") or "").strip(),
            "structure_id": str(
                entry.get("structure_id") or entry.get("structureId") or ""
            ).strip(),
        }
    return {"id": str(entry or "").strip(), "space_id": "", "structure_id": ""}


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed


def _window(
    cache: dict[str, Any], now: datetime, window_seconds: int
) -> tuple[str, int]:
    """Return the live window's start (ISO string) and read count, resetting
    an expired window in place."""
    window = cache.get("window")
    if not isinstance(window, dict):
        window = {}
    started = _parse_iso(window.get("started_at"))
    reads = window.get("reads")
    if not isinstance(reads, int) or isinstance(reads, bool) or reads < 0:
        reads = 0
    if started is None or (now - started).total_seconds() >= window_seconds:
        started = now
        reads = 0
    started_at = started.isoformat(timespec="seconds")
    cache["window"] = {"started_at": started_at, "reads": reads}
    return started_at, reads


def plan_reads(
    listed: Any,
    *,
    path: str | Path | None = None,
    cache: dict[str, Any] | None = None,
    now: datetime | None = None,
    budget: int = DEFAULT_CONTENT_READ_BUDGET,
    window_seconds: int = DEFAULT_CONTENT_READ_WINDOW_SECONDS,
) -> ReadPlan:
    """Plan the content reads this window still affords, given the cursor.

    Already-covered objects are skipped (coverage, not change detection). The
    first ``budget - reads_in_window`` uncovered objects are returned in
    ``need_read``; the rest are counted in ``deferred`` and the source is
    marked ``partial``."""
    if not isinstance(budget, int) or isinstance(budget, bool) or budget < 0:
        raise CursorError(f"budget must be a non-negative integer (got {budget!r})")
    if not isinstance(window_seconds, int) or isinstance(window_seconds, bool) or window_seconds <= 0:
        raise CursorError(f"window_seconds must be a positive integer (got {window_seconds!r})")
    now = now or datetime.now().astimezone()
    if cache is None:
        cache = load_cache(path)
    objects = cache.setdefault("objects", {})
    _, reads = _window(cache, now, window_seconds)
    remaining = max(0, budget - reads)

    entries = [_listed_entry(item) for item in (listed or [])]
    plan = ReadPlan(
        budget=budget,
        window_seconds=window_seconds,
        window_remaining=remaining,
        listed=len(entries),
        cache_entries=len(objects),
    )
    for entry in entries:
        object_id = entry["id"]
        if not object_id:
            continue
        if object_id in objects:
            plan.already_read += 1
            continue
        if len(plan.need_read) < remaining:
            plan.need_read.append(object_id)
        else:
            plan.deferred += 1
    plan.status = "partial" if plan.deferred > 0 else "ok"
    return plan


def record_reads(
    objects_read: Any,
    *,
    path: str | Path | None = None,
    cache: dict[str, Any] | None = None,
    now: datetime | None = None,
    window_seconds: int = DEFAULT_CONTENT_READ_WINDOW_SECONDS,
) -> int:
    """Record the objects whose content was read and advance the window.

    Returns the number of objects recorded. Persists the cursor atomically.
    The caller is expected to record only the objects the plan returned; the
    window therefore advances by exactly the reads the agent performed."""
    now = now or datetime.now().astimezone()
    if cache is None:
        cache = load_cache(path)
    objects = cache.setdefault("objects", {})
    _, reads = _window(cache, now, window_seconds)

    recorded = 0
    for entry in (_listed_entry(item) for item in (objects_read or [])):
        object_id = entry["id"]
        if not object_id:
            continue
        objects[object_id] = {
            "space_id": entry["space_id"],
            "structure_id": entry["structure_id"],
            "read_at": now.isoformat(timespec="seconds"),
        }
        recorded += 1
    cache["window"] = {
        "started_at": (cache.get("window") or {}).get("started_at")
        or now.isoformat(timespec="seconds"),
        "reads": reads + recorded,
    }
    save_cache(cache, path)
    return recorded
