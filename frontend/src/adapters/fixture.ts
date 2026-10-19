/* fixture.ts — deterministic fixture adapter (locked decision 12).
   Backs the mockup/approval builds. Implements a REAL pure validator and the
   capacity.py math mirror so exact-placement/edit interactions behave honestly during
   review — but no network, no writes, no clocks, no randomness beyond a
   fixed simulated latency. */

import type {
  Adapter,
  AnchoredStartSaveResult,
  DurationMemoryResetResult,
  DurationMemorySaveResult,
  RuntimeBatchItemInput,
  RuntimeBatchItemResult,
  RuntimeBatchResult,
  RuntimeBatchUndoBlocked,
  RuntimeBatchUndoReport,
  RuntimeBatchUndoReverted,
  SequenceContext,
  SequenceResult,
  SourceRefreshResult,
} from "./adapter";
import type {
  CalendarDrift,
  Capacity,
  CapacitiesCatalog,
  CapacitiesRefreshMode,
  CapacitiesRefreshStatus,
  CapacitiesRuleCapabilities,
  CapacitiesRuleNode,
  CapacitiesRuleSaveResponse,
  CapacitiesRules,
  CapacitiesSelections,
  CapacitiesSelectionsDraft,
  CapacitiesSettings,
  CapacitiesSettingsDraft,
  CapacitiesSource,
  CapacitiesSourceDraft,
  CapacitiesSourceRead,
  CommitReport,
  DaySetup,
  DaySetupSaveResult,
  FixedInputs,
  Ledger,
  MicroIdea,
  PlanInputs,
  SequenceRow,
  ShadowDiff,
  TagExclusionSettings,
  TagExclusionSettingsDraft,
  Validation,
} from "../model/types";
import { makeScenario, fixedInputsOf, type Scenario, type ScenarioName } from "../fixtures/scenarios";
import { toMinutes } from "../model/time";
import { habitsView } from "../model/habits";
import { itemIdentity, normalizeTodoistExclusionName } from "./wire";

const LATENCY_MS = 350;

function wait(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}

/** Terminal refresh phases, mirroring ``capacities_refresh.TERMINAL_PHASES``. */
const REFRESH_TERMINAL_PHASES = new Set(["complete", "failed", "cancelled", "interrupted"]);

/** Fixture mirror of ``capacities_rules.validate_rule``. Returns a bounded
    reason when the predicate is invalid for ``schema`` (or shape-invalid with
    ``schema === null``), else null. Kept honest so the mockup exercises the
    same draft-vs-active behavior the server has. */
export function fixtureRuleProblem(
  rule: unknown,
  schema: Record<string, string> | null,
  caps: CapacitiesRuleCapabilities,
): string | null {
  const ops = new Set(caps.ops);
  const valueOps = new Set(caps.valueOps);
  const numberOps = new Set(caps.numberOps);
  const dateOps = new Set(caps.dateOps);
  const equalityOps = new Set(caps.equalityOps);
  const numberKinds = new Set(caps.numberKinds);
  const dateKinds = new Set(caps.dateKinds);
  const valueKinds = new Set(caps.valueKinds);
  const reason = (message: string) => message.replace(/\s+/g, " ").slice(0, 240);

  const validate = (node: unknown, path: string): string | null => {
    if (!node || typeof node !== "object" || Array.isArray(node)) {
      return reason(`${path} must be a predicate object`);
    }
    const record = node as Record<string, unknown>;
    const combinators = ["all", "any", "not"].filter((key) => key in record);
    const hasLeaf = "prop" in record || "op" in record;
    if (combinators.length && hasLeaf) {
      return reason(`${path} mixes a combinator with a leaf`);
    }
    if (combinators.length > 1) {
      return reason(`${path} must use exactly one of all/any/not`);
    }
    if (combinators.length === 1) {
      const key = combinators[0];
      const value = record[key];
      if (key === "not") {
        if (!value || typeof value !== "object" || Array.isArray(value)) {
          return reason(`${path}.not must be a predicate object`);
        }
        return validate(value, `${path}.not`);
      }
      if (!Array.isArray(value)) return reason(`${path}.${key} must be an array of predicates`);
      for (let index = 0; index < value.length; index += 1) {
        const problem = validate(value[index], `${path}.${key}[${index}]`);
        if (problem) return problem;
      }
      return null;
    }
    if (!hasLeaf) {
      return reason(`${path} is not a predicate (needs all/any/not or prop+op)`);
    }
    const prop = record.prop;
    if (typeof prop !== "string" || prop.trim() === "") {
      return reason(`${path}.prop must be a non-empty string`);
    }
    const op = record.op;
    if (op === "matches") return reason(`${path}.op 'matches' is not supported`);
    if (typeof op !== "string" || !ops.has(op)) {
      return reason(`${path}.op must be one of ${[...ops].sort().join(", ")}`);
    }
    const values = record.values;
    if (valueOps.has(op)) {
      if (!Array.isArray(values) || values.length === 0) {
        return reason(`${path}.values must be a non-empty array for op '${op}'`);
      }
    } else if (values !== undefined && !Array.isArray(values)) {
      return reason(`${path}.values must be an array when present`);
    }
    if (schema) {
      if (!(prop in schema)) return reason(`${path} references unknown property '${prop}'`);
      const kind = schema[prop];
      if (numberOps.has(op) && !numberKinds.has(kind)) {
        return reason(`${path} op '${op}' requires a numeric property; '${prop}' is '${kind || "unknown"}'`);
      }
      if (dateOps.has(op) && !dateKinds.has(kind)) {
        return reason(`${path} op '${op}' requires a date property; '${prop}' is '${kind || "unknown"}'`);
      }
      if (equalityOps.has(op) && !valueKinds.has(kind)) {
        return reason(`${path} op '${op}' requires a value-bearing property; '${prop}' is '${kind || "unknown"}'`);
      }
    }
    return null;
  };

  return validate(rule, "rule");
}

/** capacity.py compute_capacity mirror — fixture-side only. Production renders
    /capacity-preview verbatim; this exists so fixture edits move the bar. */
export function fixtureCapacity(
  total: number,
  fixed: number,
  anchored: number,
  habits: number,
  selected: number,
  bufferingPct: number,
): Capacity {
  const rawRemaining = Math.max(0, total - fixed - anchored - habits);
  const buffer = Math.max(0, Math.ceil(rawRemaining * bufferingPct));
  const available = Math.max(0, rawRemaining - buffer);
  const free = total - fixed - anchored - habits - buffer - selected;
  const hrs = (b: number) => {
    const m = Math.abs(b) * 30;
    if (m < 60) return `${m}min`;
    return m % 60 === 0 ? `${m / 60}hr` : `${Math.floor(m / 60)}hr ${m % 60}min`;
  };
  const remaining =
    free > 0
      ? `⬆ ${hrs(free)} left · ${free} blk`
      : free === 0
        ? "⬆ fully booked · 0 blk left"
        : `⚠ ${hrs(free)} over · ${-free} blk`;
  return {
    total, fixed, anchored, habits, mint: 0, selected, buffer, free,
    overassigned: free < 0,
    availableForSelection: available,
    remaining,
    ratio: `${total - free} / ${total} blk`,
    legend: `Fixed ${fixed} · Anchored ${anchored} · Habits ${habits} · Selected ${selected} · Buffer ${buffer} · Free ${free} · Total ${total}`,
    counters: "deep: 1 / 4 · mixed: 2 / 3",
  };
}

/** Pure overlap/frame validator — the fixture stand-in for
    POST /validate-sequence. Hard errors: work-row overlaps with each other or
    with non-overlap-allowed anchored/calendar blocks; placement outside the
    anchor→eod frame. Warnings: placement in the past (before now). */
export function fixtureValidate(
  rows: SequenceRow[],
  inputs: PlanInputs,
): Validation {
  const hard: string[] = [];
  const warnings: string[] = [];
  const work = rows.filter((r) => r.kind === "work");
  const anchor = toMinutes(inputs.time.anchor);
  const eod = toMinutes(inputs.time.effectiveEod);

  const walls = inputs.anchored
    .filter((a) => !a.overlapAllowed && a.on && !a.skipToday && a.start)
    .map((a) => ({
      name: a.name,
      s: toMinutes(a.start as string),
      e: toMinutes(a.start as string) + a.durationMin,
    }));

  const span = (r: SequenceRow) => ({ s: toMinutes(r.start), e: toMinutes(r.end) });

  for (const r of work) {
    const { s, e } = span(r);
    if (s < anchor || e > eod) {
      hard.push(`'${r.id}' (${r.start}-${r.end}) is outside the day frame (${inputs.time.anchor}–${inputs.time.effectiveEod})`);
    }
    for (const w of walls) {
      if (s < w.e && w.s < e) {
        hard.push(`'${r.id}' (${r.start}-${r.end}) overlaps non-permeable anchored block '${w.name}'`);
      }
    }
  }
  for (let i = 0; i < work.length; i++) {
    for (let j = i + 1; j < work.length; j++) {
      const a = span(work[i]);
      const b = span(work[j]);
      if (a.s < b.e && b.s < a.e) {
        hard.push(`'${work[i].id}' overlaps '${work[j].id}'`);
      }
    }
  }
  const now = toMinutes(inputs.time.now);
  for (const r of work) {
    if (toMinutes(r.start) < now) {
      warnings.push(`${r.id} starts at ${r.start} — already in the past`);
    }
  }
  return { ok: hard.length === 0, hardErrors: hard, warnings };
}

export class FixtureAdapter implements Adapter {
  readonly scenario: Scenario;
  private ledger: Ledger;
  private inputs: PlanInputs;
  private drifted = false;
  private anchoredSourceDrifted = false;
  private sourceDown = false;
  private assignedDrifted = false;
  /** Pending Capacities partial-coverage state for the next reads: null means
      the scenario's own health, 0 means a full-coverage read. */
  private capacitiesDeferred: number | null = null;
  /** The source mapping record; null means none saved yet. */
  private capacitiesSource: CapacitiesSource | null = null;
  /** Deterministic discovery catalog for the mapping editor. Discovery is a
      live provider read in production and never touches the mapping record;
      the fixture stops at fixed data, echoing the requested space id. */
  private capacitiesCatalog: CapacitiesCatalog = {
    spaceId: "space-1",
    structures: [
      {
        structureId: "project",
        title: "Project",
        properties: [
          {
            propertyId: "title-prop",
            title: "Name",
            type: "title",
            writable: true,
            labelOptions: [],
          },
          {
            propertyId: "status-prop",
            title: "Status",
            type: "label",
            writable: true,
            labelOptions: [
              { id: "in-progress", title: "In Progress" },
              { id: "on-hold", title: "On Hold" },
            ],
          },
        ],
      },
      {
        // Real no-name case: the provider reports no display title, so the
        // structure id stands in. Never treated as bad data.
        structureId: "custom-project",
        title: "custom-project",
        properties: [
          {
            propertyId: "title-prop",
            title: "Name",
            type: "title",
            writable: true,
            labelOptions: [],
          },
        ],
      },
    ],
    warnings: [],
  };
  /** Deterministic per-type rules record: one schema-available structure and
      one without a published contract, so the missing-schema draft path is
      exercised rather than assumed. */
  private capacitiesRules: CapacitiesRules = {
    spaceId: "space-1",
    revision: 0,
    configured: true,
    capabilities: {
      ops: ["after", "before", "eq", "exists", "gt", "in", "lt", "truthy"],
      valueOps: ["after", "before", "eq", "gt", "in", "lt"],
      presenceOps: ["exists", "truthy"],
      numberOps: ["gt", "lt"],
      dateOps: ["after", "before"],
      equalityOps: ["eq", "in"],
      valueKinds: [
        "boolean", "date", "entity", "label", "number", "richText", "text", "title", "url",
      ],
      numberKinds: ["number"],
      dateKinds: ["date"],
      matches: false,
      schemaSource: "structure_contract",
      contractAvailable: true,
    },
    structures: [
      {
        structureId: "custom-project",
        active: null,
        draft: null,
        fallbackMinutes: null,
        mapped: true,
        schema: { "title-prop": "title", "status-prop": "label" },
        schemaAvailable: true,
      },
      {
        structureId: "0d194525-c5a1-4af5-bb62-202b83006b5e",
        active: null,
        draft: null,
        fallbackMinutes: null,
        mapped: true,
        schema: {},
        schemaAvailable: false,
      },
    ],
  };
  /** Deterministic durable selections, revision-guarded like the server. */
  private capacitiesSelections: CapacitiesSelections = {
    spaceId: "space-1",
    revision: 0,
    rulesRevision: 0,
    selections: [],
  };
  /** One in-memory refresh job. Idle (no job) with no snapshot initially. */
  private refreshPhase: string | null = null;
  private refreshOutcome: string | null = null;
  private refreshMode: CapacitiesRefreshMode | null = null;
  private refreshScope = "all";
  private refreshWarnings: string[] = [];
  private refreshGeneration = 0;
  private refreshMemberCount = 0;
  private refreshJobId = "";
  private refreshJobSeq = 0;
  private capacitiesSettings: CapacitiesSettings = {
    version: 1,
    revision: 0,
    persisted: false,
    nativeTaskAuto: {
      activeEnabled: true,
      dueEnabled: true,
      deadlineEnabled: true,
      deadlineHorizonDays: 2,
    },
    excluded: [],
    activeStructures: [],
    nativeTaskStructures: ["RootTask", "Task"],
    activeStatuses: ["active"],
    assignedStructures: {},
    assignedOnStructures: {},
    availableStructures: [
      "custom-project",
      "0d194525-c5a1-4af5-bb62-202b83006b5e",
    ],
    // Deliberately partial: one structure has an observed title and one does
    // not, so the title-with-fallback-to-id path is exercised rather than
    // assumed.
    structureTitles: {
      "0d194525-c5a1-4af5-bb62-202b83006b5e": "Project",
    },
  };
  private tagExclusionSettings: TagExclusionSettings = {
    version: 1,
    revision: 0,
    persisted: false,
    tags: [],
    todoist: [],
    catalog: {
      status: "complete",
      spaceId: "space-1",
      tags: [
        { id: "5a25370b-f9a0-40cf-bc3a-0cab4744913c", title: "habituals" },
        { id: "0d194525-c5a1-4af5-bb62-202b83006b5e", title: "chores" },
      ],
      warnings: [],
    },
  };

  constructor(name: ScenarioName) {
    this.scenario = makeScenario(name);
    this.ledger = { ...this.scenario.ledger };
    this.inputs = this.scenario.inputs;
  }

  // -- dev-only fixture controls (scenario switcher panel) ------------------
  /** Simulate an external calendar change → next readFixedInputs drifts. */
  simulateDrift(): void {
    this.drifted = true;
  }
  /** Simulate a fixed-source read failure → readFixedInputs throws. */
  simulateSourceFailure(): void {
    this.sourceDown = true;
  }
  /** Simulate raw config drift whose retained override keeps effective rows identical. */
  simulateAnchoredSourceDrift(): void {
    this.anchoredSourceDrifted = true;
  }
  /** Simulate upstream assigned-set churn: the next refresh drops the last
      assigned row, adds a new one, and doubles the first row's duration —
      exercises the added/removed/changed refresh summary (LD 20). */
  simulateAssignedDrift(): void {
    this.assignedDrifted = true;
  }
  /** Simulate a Capacities partial-coverage read: every read reports this many
      rows still deferred (0 restores full coverage), so repeated refreshes can
      model the cache converging on the remainder. */
  simulateCapacitiesCoverage(deferred: number): void {
    this.capacitiesDeferred = deferred;
  }
  /** Dev-only: finish the running refresh job so the terminal status path
      (outcome + new generation) is reachable without a real provider. */
  completeCapacitiesRefresh(outcome = "published"): void {
    if (this.refreshPhase === null || REFRESH_TERMINAL_PHASES.has(this.refreshPhase)) return;
    this.refreshPhase = "complete";
    this.refreshOutcome = outcome;
    this.refreshGeneration += 1;
    this.refreshMemberCount = 6;
  }

  /** Project the simulated Capacities coverage onto a read model, mirroring
      the adapter's budget-variant warning verbatim. */
  private applyCapacitiesCoverage(inputs: PlanInputs): PlanInputs {
    const deferred = this.capacitiesDeferred;
    if (deferred === null) return inputs;
    const others = inputs.sourceWarnings.filter(
      (warning) => !warning.startsWith("Capacities partial"),
    );
    if (deferred <= 0) return { ...inputs, sourceWarnings: others, sourceHealth: "ok" };
    return {
      ...inputs,
      sourceWarnings: [
        ...others,
        `Capacities partial — 20 evaluated · ${deferred} deferred across contributing ` +
          "structures. Content-read budget reached. Wait at least a minute, then " +
          "Refresh sources to continue.",
      ],
      sourceHealth: "degraded",
    };
  }

  /** Fold pending simulated drift into the canonical inputs so a refresh
      observes it the way production observes real upstream change. */
  private applyPendingDrift(): void {
    if (this.drifted) {
      this.drifted = false;
      this.inputs = {
        ...this.inputs,
        anchored: [
          ...this.inputs.anchored,
          {
            id: "Dentist (added externally)",
            name: "Dentist (added externally)",
            kind: "calendar",
            start: "11:00",
            end: "11:45",
            durationMin: 45,
            overlapAllowed: false,
            on: true,
            skipToday: false,
          },
        ],
      };
    }
    if (this.assignedDrifted) {
      this.assignedDrifted = false;
      const assigned = [...this.inputs.assigned];
      const first = assigned[0];
      if (first) assigned[0] = { ...first, blocks: first.blocks * 2 || 1 };
      assigned.pop();
      assigned.push({
        id: "Review quarterly notes",
        name: "Review quarterly notes",
        path: null,
        source: "todoist",
        types: ["task"],
        urgency: null,
        deadline: null,
        priorityScore: 1,
        blocks: 1,
        durationLabel: "30min",
        todoistId: "6fx004QTR",
      });
      this.inputs = { ...this.inputs, assigned };
    }
  }

  async refreshSources(): Promise<SourceRefreshResult> {
    await wait(LATENCY_MS);
    if (this.sourceDown) {
      throw new Error("source refresh failed — calendar read degraded");
    }
    this.applyPendingDrift();
    const fixed = fixedInputsOf(this.inputs);
    if (this.anchoredSourceDrifted) {
      fixed.anchoredSourceFingerprint += "-drifted";
    }
    const inputs = this.applyTodoistExclusions(
      this.applyPromotedSelections(
        this.applyCapacitiesIntake(this.applyCapacitiesCoverage(structuredClone(this.inputs))),
      ),
    );
    if (this.anchoredSourceDrifted) {
      inputs.anchoredSourceFingerprint += "-drifted";
    }
    return { inputs, fixed, ledger: { ...this.ledger } };
  }

  async loadPlanInputs(): Promise<PlanInputs> {
    await wait(LATENCY_MS);
    const inputs = this.applyCapacitiesCoverage(structuredClone(this.inputs));
    return this.applyTodoistExclusions(
      this.applyPromotedSelections(this.applyCapacitiesIntake(inputs)),
    );
  }

  /** Server-mirror of the Todoist exclusion stage: drop assigned rows whose
      task id is listed OR whose normalized title matches, and attach the
      diagnostics envelope the digest carries. The source inputs are never
      mutated — a Show again re-read brings the row back. */
  private applyTodoistExclusions(inputs: PlanInputs): PlanInputs {
    const policy = this.tagExclusionSettings.todoist;
    if (policy.length === 0) return inputs;
    const wantedIds = new Set(policy.map((entry) => entry.id));
    const wantedNames = new Set(policy.map((entry) => normalizeTodoistExclusionName(entry.name)));
    const rows: typeof inputs.assigned = [];
    const removed: Array<{ identity: string; name: string; surface: string; matched: string }> = [];
    for (const item of inputs.assigned) {
      if (item.source !== "todoist") {
        rows.push(item);
        continue;
      }
      const idMatch = item.todoistId != null && wantedIds.has(item.todoistId);
      const nameMatch = wantedNames.has(normalizeTodoistExclusionName(item.name));
      if (!idMatch && !nameMatch) {
        rows.push(item);
        continue;
      }
      removed.push({
        identity: item.identity ?? item.todoistId ?? item.id,
        name: item.name,
        surface: "assigned",
        matched: idMatch ? `id:${item.todoistId}` : `name:${item.name}`,
      });
    }
    return {
      ...inputs,
      assigned: rows,
      todoistExclusionPolicy: {
        revision: this.tagExclusionSettings.revision,
        persisted: this.tagExclusionSettings.persisted,
        mode: "todoist",
        evaluatedCounts: { assigned: inputs.assigned.length, pool: 0 },
        excludedCounts: { assigned: removed.length, pool: 0, total: removed.length },
        decisions: removed.map((row) => ({ ...row, reason: "excluded_todoist" })),
        warnings: [],
      },
    };
  }

  /** Mirror of the server's selection promotion: a selected canonical
      candidate becomes an assigned Capacities row on the NEXT read, so the
      digest (and therefore the Commit body) reflects it — a selection is
      never a UI-local promotion. */
  private applyPromotedSelections(inputs: PlanInputs): PlanInputs {
    const selected = new Set(this.capacitiesSelections.selections.map((s) => s.identity));
    if (selected.size === 0) return inputs;
    const existing = new Set(
      inputs.assigned.map((item) => item.identity).filter((id): id is string => !!id),
    );
    const candidates = inputs.capacitiesIntake?.unassignedCandidates ?? [];
    const added = candidates
      .filter((candidate) => selected.has(candidate.identity) && !existing.has(candidate.identity))
      .map((candidate) => ({
        id: candidate.name || candidate.identity,
        name: candidate.name || candidate.identity,
        path: null,
        source: "capacities" as const,
        types: ["custom-project"],
        urgency: null,
        deadline: null,
        priorityScore: 1,
        blocks: 1,
        durationLabel: "30min",
        identity: candidate.identity,
        capacitiesIdentity: candidate.identity,
        todoistId: null,
      }));
    if (added.length === 0) return inputs;
    return { ...inputs, assigned: [...inputs.assigned, ...added] };
  }

  /** Deterministic direct-intake block so the Connections review surface has
      stable canonical candidates in the mockup build. */
  private applyCapacitiesIntake(inputs: PlanInputs): PlanInputs {
    // A simulated partial read keeps the legacy warning-text coverage path:
    // emitting an ``ok`` intake block here would mask the deferrals the
    // scenario is modelling.
    if (this.capacitiesDeferred !== null && this.capacitiesDeferred > 0) return inputs;
    // The sparse scenario models the legacy intake: no direct snapshot block
    // is installed, so the fixture must not fabricate one (KTD13).
    if (this.scenario.name === "sparse") return inputs;
    const selected = new Set(this.capacitiesSelections.selections.map((s) => s.identity));
    const candidate = (identity: string, name: string, reviewReasons: string[]) => ({
      identity,
      name,
      reviewReasons,
      selected: selected.has(identity),
    });
    // Fixture-only freshness metadata so the Sources details popover renders a
    // realistic cached generation: a partial read with six skipped objects.
    // ``simulateCapacitiesCoverage(0)`` explicitly models full coverage, so
    // that path keeps the ``ok`` block the store tests rely on.
    const full = this.capacitiesDeferred === 0;
    const installedAt = Math.floor(Date.now() / 1000) - 18 * 60;
    const partialWarning =
      "Capacities partial — 168 evaluated · 6 deferred across contributing structures.";
    const skipped = [
      {
        identity: "bb95d80d-573c-4aff-8faa-9dccdd2617a1",
        title: null,
        reason: "No title",
        detail: "title property empty",
        structureId: "project",
        structureName: "Project",
        labels: ["P1", "Idle"],
        updatedAt: "2026-10-10T00:33:06.790Z",
        hint: null,
      },
      {
        identity: "3f2a1c74-9b6e-4d21-8a55-0c7e1f2d3b44",
        title: null,
        reason: "No title",
        detail: "title property missing",
        structureId: "project",
        structureName: "Project",
        labels: [],
        updatedAt: "2026-10-06T05:28:52.074Z",
        hint: null,
      },
      {
        identity: "a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d",
        title: null,
        reason: "No title",
        detail: "title property empty",
        structureId: "event",
        structureName: "Event",
        labels: ["Inbox"],
        updatedAt: null,
        hint: null,
      },
      {
        identity: "7c9e6679-7425-40de-944b-e07fc1f90ae7",
        title: null,
        reason: "No title",
        detail: "title property empty",
        structureId: "task",
        structureName: "Task",
        labels: ["Active"],
        updatedAt: null,
        hint: "Description · Habit details · Cadence:",
      },
      {
        identity: "d2b3c4a5-6f70-4812-93a4-b5c6d7e8f901",
        title: null,
        reason: "No title",
        detail: "title is whitespace",
        structureId: "task",
        structureName: "Task",
        labels: ["Todo"],
        updatedAt: null,
        hint: null,
      },
      {
        identity: "ef12ab34-cd56-4e78-9a0b-c1d2e3f4a5b6",
        title: "3D/W A on {date:LL}",
        reason: "Duration is not a number",
        detail: "durationMinutes was null",
        structureId: "press",
        structureName: "Press",
        labels: ["Active"],
        updatedAt: null,
        hint: null,
      },
    ];
    return {
      ...inputs,
      ...(full
        ? {}
        : {
            sourceWarnings: inputs.sourceWarnings.includes(partialWarning)
              ? inputs.sourceWarnings
              : [...inputs.sourceWarnings, partialWarning],
          }),
      capacitiesIntake: {
        mode: "direct",
        state: full ? "ok" : "degraded",
        generation: this.refreshGeneration > 0 ? this.refreshGeneration : 1,
        installedAt,
        typeCheckTimes: {
          "custom-project": installedAt - 2 * 3600,
          "9f8e7d6c5b4a39281706f5e4d3c2b1a0": installedAt,
        },
        coverage: full
          ? { members: 174, evaluated: 174, malformed: 0, unreadable: 0 }
          : { members: 174, evaluated: 168, malformed: 6, unreadable: 0 },
        unassignedCandidates: [
          candidate("capacities:space-1:custom-project:obj-1", "Draft launch brief", []),
          candidate("capacities:space-1:custom-project:obj-2", "Renew domain", ["overdue"]),
        ],
        ...(full ? {} : { skipped }),
      },
    };
  }

  async billedLedger(): Promise<Ledger> {
    await wait(60);
    return { ...this.ledger };
  }

  /** Issue #19 (part B): fixture mode has no committed ledger to compare, so
      it always returns the canned "nothing to show" report. Tests inject
      richer reports through their own adapter doubles. */
  async calendarDrift(): Promise<CalendarDrift> {
    await wait(40);
    return {
      status: "ok",
      day: this.inputs.validDate,
      hasCommit: false,
      reason: "no commit today",
      moved: [],
      missing: [],
      added: [],
      checkedAt: "",
    };
  }

  async capacityPreview(daySetup: DaySetup, selectedBlocks: number[]): Promise<Capacity> {
    await wait(80);
    const pct =
      daySetup.buffering === "off" ? 0 : daySetup.buffering === "minimal" ? 0.11 : 0.2;
    const c = this.inputs.capacity;
    const selected = selectedBlocks.reduce((a, b) => a + b, 0);
    let anchored = c.anchored;
    for (const [id, override] of Object.entries(daySetup.anchored)) {
      const source = this.inputs.anchored.find((a) => a.id === id);
      if (!source || source.kind === "calendar" || source.kind === "template") continue;
      if (!source.start || toMinutes(source.start) >= toMinutes(this.inputs.time.effectiveEod)) continue;
      const before = source.on && !source.skipToday ? Math.ceil(source.durationMin / 30) : 0;
      const after = override.on && !override.skipToday
        ? (override.blocks == null ? before : override.blocks)
        : 0;
      anchored += after - before;
    }
    // Habits follow the per-day overrides like the server's _habits_segment:
    // the counted minutes round up to whole blocks.
    const view = this.inputs.habits
      ? habitsView(this.inputs.habits, daySetup.habits)
      : null;
    const habits = view ? Math.ceil(view.countedMinutes / 30) : c.habits;
    return fixtureCapacity(c.total, c.fixed, anchored, habits, selected, pct);
  }

  async saveDaySetup(_daySetup: DaySetup): Promise<void> {
    await wait(LATENCY_MS);
  }

  /** U5 fixture mirrors the server's prompt-only semantics in memory: a
      captures PATCH never confirms the day. Built-in prompt keys and
      browser-defined custom keys (``custom-<id>``) are accepted; any other
      key is ignored. */
  private promptDrafts: Record<string, string> = {};
  private static readonly CUSTOM_PROMPT_KEY = /^custom-[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/;

  async savePromptDrafts(patch: Record<string, string>): Promise<DaySetupSaveResult> {
    await wait(LATENCY_MS);
    for (const [key, value] of Object.entries(patch)) {
      if (key !== "intention" && key !== "megan_nicety" && key !== "stoic_intention" &&
          !FixtureAdapter.CUSTOM_PROMPT_KEY.test(key)) continue;
      if (typeof value !== "string") continue;
      if (value === "") delete this.promptDrafts[key];
      else this.promptDrafts[key] = value;
    }
    return this.promptEcho();
  }

  private promptEcho(): DaySetupSaveResult {
    return {
      ok: true,
      daySetupConfirmed: false,
      promptWarnings: [],
    };
  }

  async loadCapacitiesSettings(): Promise<CapacitiesSettings> {
    await wait(LATENCY_MS);
    return structuredClone(this.capacitiesSettings);
  }

  async saveCapacitiesSettings(draft: CapacitiesSettingsDraft): Promise<CapacitiesSettings> {
    await wait(LATENCY_MS);
    if (draft.expectedRevision !== this.capacitiesSettings.revision) {
      throw new Error(
        `Capacities settings changed since they were read (stored revision ${this.capacitiesSettings.revision}, expected ${draft.expectedRevision})`,
      );
    }
    this.capacitiesSettings = {
      version: 1,
      revision: this.capacitiesSettings.revision + 1,
      persisted: true,
      nativeTaskAuto: { ...draft.nativeTaskAuto },
      excluded: [...draft.excluded].sort(),
      activeStructures: [...draft.activeStructures].sort(),
      nativeTaskStructures: [...draft.nativeTaskStructures].sort(),
      activeStatuses: [...draft.activeStatuses].sort(),
      assignedStructures: { ...draft.assignedStructures },
      assignedOnStructures: { ...draft.assignedOnStructures },
      availableStructures: [...this.capacitiesSettings.availableStructures],
      // Titles are read-only advisory metadata, never part of the save
      // payload, so a save carries the observed ones through unchanged.
      structureTitles: { ...this.capacitiesSettings.structureTitles },
    };
    return structuredClone(this.capacitiesSettings);
  }

  async loadCapacitiesSource(): Promise<CapacitiesSourceRead> {
    await wait(LATENCY_MS);
    return {
      source: structuredClone(this.capacitiesSource),
      persisted: this.capacitiesSource !== null,
    };
  }

  async saveCapacitiesSource(draft: CapacitiesSourceDraft): Promise<CapacitiesSourceRead> {
    await wait(LATENCY_MS);
    const currentRevision = this.capacitiesSource?.revision ?? 0;
    if (draft.expectedRevision !== currentRevision) {
      throw new Error(
        `Capacities source mapping changed since it was read (stored revision ${currentRevision}, expected ${draft.expectedRevision})`,
      );
    }
    this.capacitiesSource = {
      version: 1,
      revision: currentRevision + 1,
      spaceId: draft.spaceId,
      structures: structuredClone(draft.structures),
    };
    return { source: structuredClone(this.capacitiesSource), persisted: true };
  }

  async discoverCapacitiesSource(spaceId: string): Promise<CapacitiesCatalog> {
    await wait(LATENCY_MS);
    return structuredClone({ ...this.capacitiesCatalog, spaceId });
  }

  async loadCapacitiesRules(): Promise<CapacitiesRules> {
    await wait(LATENCY_MS);
    return structuredClone(this.capacitiesRules);
  }

  async saveCapacitiesRule(args: {
    structureId: string;
    rule: CapacitiesRuleNode;
    fallbackMinutes: number | null;
    expectedRevision: number;
  }): Promise<CapacitiesRuleSaveResponse> {
    await wait(LATENCY_MS);
    if (args.expectedRevision !== this.capacitiesRules.revision) {
      throw new Error(
        `Capacities rules changed since they were read (stored revision ${this.capacitiesRules.revision}, expected ${args.expectedRevision})`,
      );
    }
    const structure = this.capacitiesRules.structures.find(
      (s) => s.structureId === args.structureId,
    );
    if (!structure) throw new Error(`unknown structure ${args.structureId}`);
    const schema = structure.schemaAvailable ? structure.schema : null;
    let problem = fixtureRuleProblem(args.rule, schema, this.capacitiesRules.capabilities);
    let valid = problem === null;
    if (valid && schema === null) {
      valid = false;
      problem = "no discovered schema was supplied; rule saved as a draft";
    }
    const draft = structuredClone(args.rule);
    const nextStructure = {
      ...structure,
      active: valid ? draft : structure.active,
      draft,
      fallbackMinutes: args.fallbackMinutes,
    };
    this.capacitiesRules = {
      ...this.capacitiesRules,
      revision: this.capacitiesRules.revision + 1,
      structures: this.capacitiesRules.structures.map((s) =>
        s.structureId === args.structureId ? nextStructure : s,
      ),
    };
    return {
      ...structuredClone(this.capacitiesRules),
      save: {
        structureId: args.structureId,
        valid,
        reason: problem,
        active: structuredClone(nextStructure.active),
        draft,
        fallbackMinutes: args.fallbackMinutes,
        revision: this.capacitiesRules.revision,
      },
    };
  }

  private capacitiesStatus(): CapacitiesRefreshStatus {
    const terminal =
      this.refreshPhase === null || REFRESH_TERMINAL_PHASES.has(this.refreshPhase);
    const job =
      this.refreshPhase === null
        ? null
        : {
            jobId: this.refreshJobId,
            mode: this.refreshMode,
            scope: this.refreshScope,
            phase: this.refreshPhase,
            outcome: this.refreshOutcome,
            revision: 0,
            generation: this.refreshGeneration,
            startedAt: 0,
            updatedAt: 0,
            finishedAt: terminal ? 0 : null,
            progress: {
              listed: this.refreshMemberCount,
              read: this.refreshMemberCount,
              // The fixture run never waits and reads everything it listed, so
              // the additive KTD4 keys echo a completed target and no pause.
              toRead: 0,
              waiting: null,
              types: {} as Record<string, number>,
            },
            warnings: [...this.refreshWarnings],
          };
    return {
      configured: true,
      phase: this.refreshPhase,
      outcome: this.refreshOutcome,
      mode: this.refreshMode,
      scope: this.refreshPhase === null ? null : this.refreshScope,
      job,
      progress: job
        ? { ...job.progress, types: { ...job.progress.types } }
        : { listed: 0, read: 0, toRead: null, waiting: null, types: {} },
      warnings: [...this.refreshWarnings],
      coverage: {},
      snapshot: {
        present: this.refreshGeneration > 0,
        generation: this.refreshGeneration,
        revision: null,
        installedAt: null,
        memberCount: this.refreshMemberCount,
        typeCheckTimes: {},
      },
    };
  }

  async capacitiesRefreshStatus(): Promise<CapacitiesRefreshStatus> {
    await wait(40);
    return this.capacitiesStatus();
  }

  async startCapacitiesRefresh(
    mode: CapacitiesRefreshMode,
    scope: string,
  ): Promise<CapacitiesRefreshStatus> {
    await wait(60);
    if (this.refreshPhase !== null && !REFRESH_TERMINAL_PHASES.has(this.refreshPhase)) {
      throw new Error("A Capacities refresh job is already running.");
    }
    this.refreshJobSeq += 1;
    this.refreshJobId = `job-fixture-${this.refreshJobSeq}`;
    this.refreshMode = mode;
    this.refreshScope = scope;
    this.refreshPhase = "listing";
    this.refreshOutcome = null;
    this.refreshWarnings = [];
    return this.capacitiesStatus();
  }

  async cancelCapacitiesRefresh(): Promise<CapacitiesRefreshStatus> {
    await wait(60);
    if (this.refreshPhase !== null && !REFRESH_TERMINAL_PHASES.has(this.refreshPhase)) {
      this.refreshPhase = "cancelled";
      this.refreshOutcome = "cancelled";
      this.refreshWarnings = ["Refresh cancelled; partial reads were kept."];
    }
    return this.capacitiesStatus();
  }

  async loadCapacitiesSelections(): Promise<CapacitiesSelections> {
    await wait(LATENCY_MS);
    return structuredClone(this.capacitiesSelections);
  }

  async saveCapacitiesSelections(
    draft: CapacitiesSelectionsDraft,
  ): Promise<CapacitiesSelections> {
    await wait(LATENCY_MS);
    if (draft.expectedRevision !== this.capacitiesSelections.revision) {
      throw new Error(
        `Capacities selections changed since they were read (stored revision ${this.capacitiesSelections.revision}, expected ${draft.expectedRevision})`,
      );
    }
    const merged = new Map(
      this.capacitiesSelections.selections.map((s) => [s.identity, s]),
    );
    for (const identity of draft.deselect) merged.delete(identity);
    for (const entry of draft.select) {
      merged.set(entry.identity, {
        identity: entry.identity,
        acknowledged: entry.acknowledge,
        rulesRevision: this.capacitiesRules.revision,
      });
    }
    this.capacitiesSelections = {
      ...this.capacitiesSelections,
      revision: this.capacitiesSelections.revision + 1,
      rulesRevision: this.capacitiesRules.revision,
      selections: [...merged.values()],
    };
    return structuredClone(this.capacitiesSelections);
  }

  async loadTagExclusionSettings(): Promise<TagExclusionSettings> {
    await wait(LATENCY_MS);
    return structuredClone(this.tagExclusionSettings);
  }

  async saveTagExclusionSettings(
    draft: TagExclusionSettingsDraft,
  ): Promise<TagExclusionSettings> {
    await wait(LATENCY_MS);
    if (draft.expectedRevision !== this.tagExclusionSettings.revision) {
      throw new Error(
        `Tag exclusion settings changed since they were read (stored revision ${this.tagExclusionSettings.revision}, expected ${draft.expectedRevision})`,
      );
    }
    this.tagExclusionSettings = {
      version: 1,
      revision: this.tagExclusionSettings.revision + 1,
      persisted: true,
      tags: draft.tags.map((tag) => ({ ...tag })),
      todoist: draft.todoist.map((entry) => ({ ...entry })),
      catalog: structuredClone(this.tagExclusionSettings.catalog),
    };
    return structuredClone(this.tagExclusionSettings);
  }

  async saveMicroAdventure(_pick: MicroIdea | null): Promise<void> {
    await wait(LATENCY_MS);
  }

  async resolveLooseEnd(_entry: { date: string; id: string }, _done: boolean): Promise<void> {
    await wait(LATENCY_MS);
  }

  /** Duration memory (MVP): in-memory mirror of the token-guarded mutation.
      The store model is updated only from the returned result — the fixture
      never mutates its loaded inputs, matching the server-authoritative
      contract. */
  private memory = new Map<string, number>();

  async saveDurationMemory(identity: string, minutes: number): Promise<DurationMemorySaveResult> {
    await wait(LATENCY_MS);
    this.memory.set(identity, minutes);
    return { identity, minutes, source: "remembered" };
  }

  async resetDurationMemory(identity: string): Promise<DurationMemoryResetResult> {
    await wait(LATENCY_MS);
    this.memory.delete(identity);
    // Source fallback: the fixture row's resolved blocks (never mutated by a
    // save), falling back to the resolver default of 30 minutes.
    const item = this.inputs.assigned.find((i) => itemIdentity(i) === identity);
    const minutes = item ? Math.round(item.blocks * 30) : 30;
    return { identity, minutes, source: "default" };
  }

  /** Anchored-start save: the fixture "config" is its loaded inputs, so a
      save rewrites that block's configured start and the next source refresh
      reads it back — the same reload loop the real server gives. */
  async saveAnchoredStart(block: string, start: string): Promise<AnchoredStartSaveResult> {
    await wait(LATENCY_MS);
    const row = this.inputs.anchored.find((a) => a.id === block && a.kind !== "calendar");
    if (!row) throw new Error(`anchored block '${block}' is not in the repo config`);
    const previousStart = row.start;
    row.start = start;
    return { block, start, previousStart };
  }

  async autoSequence(_ctx: SequenceContext): Promise<SequenceResult> {
    await wait(LATENCY_MS * 3); // billed judgment call is visibly slower
    if (this.ledger.remaining <= 0) {
      throw new Error("billed budget exhausted (429) — manual layout available");
    }
    this.ledger = {
      ...this.ledger,
      spent: this.ledger.spent + 1,
      remaining: this.ledger.remaining - 1,
    };
    if (this.scenario.proposalError || !this.scenario.proposal) {
      throw new Error(this.scenario.proposalError ?? "sequence failed");
    }
    return { ...structuredClone(this.scenario.proposal), overlapGrants: [] };
  }

  async validateSequence(rows: SequenceRow[], _ctx: SequenceContext): Promise<Validation> {
    await wait(90);
    return fixtureValidate(rows, this.inputs);
  }

  async readFixedInputs(): Promise<FixedInputs> {
    await wait(120);
    if (this.sourceDown) {
      throw new Error("calendar source read failed — preview/commit blocked");
    }
    const fixed = fixedInputsOf(this.inputs);
    if (this.anchoredSourceDrifted) {
      fixed.anchoredSourceFingerprint += "-drifted";
    }
    if (this.drifted) {
      fixed.calendar.push({ name: "Dentist (added externally)", start: "11:00", durationMin: 45 });
    }
    return fixed;
  }

  async shadowCommit(_rows: SequenceRow[], _ctx: SequenceContext): Promise<ShadowDiff> {
    await wait(LATENCY_MS * 2);
    return structuredClone(this.scenario.shadow);
  }

  async liveCommit(_rows: SequenceRow[], _ctx: SequenceContext): Promise<CommitReport> {
    await wait(LATENCY_MS * 3);
    return structuredClone(this.scenario.commitReport);
  }

  /** T20: deterministic in-memory journal — enough for UI flows/tests. */
  private runtimeJournal: import("../model/types").RuntimeAction[] = [];

  async runtimeAction(
    verb: string, target: string, _args: Record<string, unknown> = {},
  ): Promise<import("../model/types").RuntimeAction> {
    await wait(LATENCY_MS);
    const action: import("../model/types").RuntimeAction = {
      id: `ra-fixture-${this.runtimeJournal.length + 1}`,
      verb,
      targetName: target,
      status: "applied",
      error: null,
      duplicate: false,
    };
    this.runtimeJournal.push(action);
    return { ...action };
  }

  async undoRuntimeAction(actionId: string): Promise<import("../model/types").RuntimeAction> {
    await wait(LATENCY_MS);
    const action = this.runtimeJournal.find((a) => a.id === actionId);
    if (!action) throw new Error(`unknown action ${actionId}`);
    action.status = "undone";
    return { ...action };
  }

  // -- runtime batches (B5 contract) ----------------------------------------
  /** Dev-only, deterministic switches for the batch surface: force one item
      to fail on apply/retry, and force one applied item's undo to conflict. */
  private batchFailIndex: number | null = null;
  private batchUndoConflictIndex: number | null = null;
  private batchSeq = 0;
  private batchJournal = new Map<string, RuntimeBatchItemResult[]>();
  private batchActions = new Map<string, Map<number, string>>();
  private batchUndone = new Map<string, Set<number>>();
  private batchAttempts = new Map<string, number>();

  /** Force the item at `index` to fail on the next apply/retry; null clears. */
  simulateBatchFailure(index: number | null): void {
    this.batchFailIndex = index;
  }

  /** Force the applied item at `index` to report an undo conflict; null
      clears. */
  simulateBatchUndoConflict(index: number | null): void {
    this.batchUndoConflictIndex = index;
  }

  /** Items actually executed (apply + retry) for a batch. Lets a test prove
      retry re-runs only the unsettled items. */
  batchExecutionCount(batchId: string): number {
    return this.batchAttempts.get(batchId) ?? 0;
  }

  private runBatchItem(
    index: number, item: RuntimeBatchItemInput,
  ): RuntimeBatchItemResult {
    const failed = this.batchFailIndex === index;
    return {
      index,
      verb: item.verb,
      target: item.target,
      status: failed ? "failed" : "applied",
      error: failed ? `fixture: forced failure on item ${index}` : null,
    };
  }

  async applyRuntimeBatch(
    items: RuntimeBatchItemInput[],
  ): Promise<RuntimeBatchResult> {
    await wait(LATENCY_MS);
    this.batchSeq += 1;
    const batchId = `rb-fixture-${this.batchSeq}`;
    const results = items.map((item, index) => this.runBatchItem(index, item));
    this.batchJournal.set(batchId, results);
    this.batchActions.set(
      batchId,
      new Map(
        results
          .filter((r) => r.status === "applied")
          .map((r) => [r.index, `ra-fixture-batch-${batchId}-${r.index}`]),
      ),
    );
    this.batchAttempts.set(batchId, results.length);
    return { batchId, results: results.map((r) => ({ ...r })) };
  }

  /** Re-run only the unsettled items; applied/duplicate items are reported
      as-is and are not executed again. */
  async retryRuntimeBatch(batchId: string): Promise<RuntimeBatchResult> {
    await wait(LATENCY_MS);
    const stored = this.batchJournal.get(batchId);
    if (!stored) throw new Error(`unknown batch ${batchId}`);
    let executions = this.batchAttempts.get(batchId) ?? 0;
    const results = stored.map((r) => {
      if (r.status === "applied" || r.status === "duplicate") return { ...r };
      executions += 1;
      return this.runBatchItem(r.index, r);
    });
    this.batchAttempts.set(batchId, executions);
    this.batchJournal.set(batchId, results);
    const actions = this.batchActions.get(batchId) ?? new Map<number, string>();
    for (const r of results) {
      if (r.status === "applied" && !actions.has(r.index)) {
        actions.set(r.index, `ra-fixture-batch-${batchId}-${r.index}`);
      }
    }
    this.batchActions.set(batchId, actions);
    return { batchId, results: results.map((r) => ({ ...r })) };
  }

  /** Reverse the applied items in reverse order, reporting a forced/later
      conflict per item; a second undo reports the reverted items as conflicts. */
  async undoRuntimeBatch(batchId: string): Promise<RuntimeBatchUndoReport> {
    await wait(LATENCY_MS);
    const stored = this.batchJournal.get(batchId);
    if (!stored) throw new Error(`unknown batch ${batchId}`);
    const actions = this.batchActions.get(batchId) ?? new Map<number, string>();
    const undone = this.batchUndone.get(batchId) ?? new Set<number>();
    const reverted: RuntimeBatchUndoReverted[] = [];
    const conflicts: RuntimeBatchUndoBlocked[] = [];
    for (const r of [...stored].reverse()) {
      if (r.status !== "applied") continue;
      const base = { item: r.index, verb: r.verb, target: r.target };
      if (undone.has(r.index)) {
        conflicts.push({ ...base, reason: "already undone" });
        continue;
      }
      if (this.batchUndoConflictIndex === r.index) {
        conflicts.push({
          ...base,
          reason: `fixture: forced undo conflict on item ${r.index}`,
        });
        continue;
      }
      reverted.push({ ...base, actionId: actions.get(r.index) ?? "" });
      undone.add(r.index);
    }
    this.batchUndone.set(batchId, undone);
    return { batchId, reverted, conflicts, nonReversible: [] };
  }
}
