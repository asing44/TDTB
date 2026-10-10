/* fixture.ts — deterministic fixture adapter (locked decision 12).
   Backs the mockup/approval builds. Implements a REAL pure validator and the
   capacity.py math mirror so exact-placement/edit interactions behave honestly during
   review — but no network, no writes, no clocks, no randomness beyond a
   fixed simulated latency. */

import type {
  Adapter,
  DurationMemoryResetResult,
  DurationMemorySaveResult,
  SequenceContext,
  SequenceResult,
  SourceRefreshResult,
} from "./adapter";
import { PromptOptinConflictError } from "./adapter";
import type {
  Capacity,
  CapacitiesCatalog,
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
import { itemIdentity } from "./wire";

const LATENCY_MS = 350;

function wait(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
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
  /** The vault-local source mapping record; null means none saved yet. */
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
    const inputs = this.applyCapacitiesCoverage(structuredClone(this.inputs));
    if (this.anchoredSourceDrifted) {
      inputs.anchoredSourceFingerprint += "-drifted";
    }
    return { inputs, fixed, ledger: { ...this.ledger } };
  }

  async loadPlanInputs(): Promise<PlanInputs> {
    await wait(LATENCY_MS);
    const inputs = this.applyCapacitiesCoverage(structuredClone(this.inputs));
    // U5 fixture mirrors the additive opt-in metadata the real read route will
    // carry once it lands; production omits it until then, and the store then
    // reports prefs unavailable rather than assuming false.
    inputs.promptOptins = {
      optins: { ...this.promptOptins },
      revision: this.promptOptinsRevision,
    };
    return inputs;
  }

  async billedLedger(): Promise<Ledger> {
    await wait(60);
    return { ...this.ledger };
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
    return fixtureCapacity(c.total, c.fixed, anchored, c.habits, selected, pct);
  }

  async saveDaySetup(_daySetup: DaySetup): Promise<void> {
    await wait(LATENCY_MS);
  }

  /** U5 fixture mirrors the server's prompt-only semantics in memory: a
      captures PATCH never confirms the day, an opt-in save is a full
      replacement guarded by the optimistic revision. */
  private promptDrafts: Record<string, string> = {};
  private promptOptins: Record<string, boolean> = {
    intention: false,
    megan_nicety: false,
    stoic_intention: false,
  };
  private promptOptinsRevision = 0;

  async savePromptDrafts(patch: Record<string, string>): Promise<DaySetupSaveResult> {
    await wait(LATENCY_MS);
    for (const [key, value] of Object.entries(patch)) {
      if (key !== "intention" && key !== "megan_nicety" && key !== "stoic_intention") continue;
      if (typeof value !== "string") continue;
      if (value === "") delete this.promptDrafts[key];
      else this.promptDrafts[key] = value;
    }
    return this.promptEcho();
  }

  async savePromptOptins(
    optins: Record<string, boolean>,
    expectedRevision: number,
  ): Promise<DaySetupSaveResult> {
    await wait(LATENCY_MS);
    if (expectedRevision !== this.promptOptinsRevision) {
      throw new PromptOptinConflictError(expectedRevision, this.promptOptinsRevision);
    }
    this.promptOptins = {
      intention: false,
      megan_nicety: false,
      stoic_intention: false,
      ...optins,
    };
    this.promptOptinsRevision += 1;
    return this.promptEcho();
  }

  private promptEcho(): DaySetupSaveResult {
    return {
      ok: true,
      daySetupConfirmed: false,
      optins: { ...this.promptOptins },
      optinsRevision: this.promptOptinsRevision,
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
      catalog: structuredClone(this.tagExclusionSettings.catalog),
    };
    return structuredClone(this.tagExclusionSettings);
  }

  async saveMicroAdventure(_pick: MicroIdea | null): Promise<void> {
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
}
