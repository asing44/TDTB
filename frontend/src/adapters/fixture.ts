/* fixture.ts — deterministic fixture adapter (locked decision 12).
   Backs the mockup/approval builds. Implements a REAL pure validator and the
   capacity.py math mirror so exact-placement/edit interactions behave honestly during
   review — but no network, no writes, no clocks, no randomness beyond a
   fixed simulated latency. */

import type {
  Adapter,
  SequenceContext,
  SequenceResult,
  SourceRefreshResult,
} from "./adapter";
import type {
  Capacity,
  CommitReport,
  DaySetup,
  FixedInputs,
  Ledger,
  MicroIdea,
  PlanInputs,
  SequenceRow,
  ShadowDiff,
  Validation,
} from "../model/types";
import { makeScenario, fixedInputsOf, type Scenario, type ScenarioName } from "../fixtures/scenarios";
import { toMinutes } from "../model/time";

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
    const inputs = structuredClone(this.inputs);
    if (this.anchoredSourceDrifted) {
      inputs.anchoredSourceFingerprint += "-drifted";
    }
    return { inputs, fixed, ledger: { ...this.ledger } };
  }

  async loadPlanInputs(): Promise<PlanInputs> {
    await wait(LATENCY_MS);
    return structuredClone(this.inputs);
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

  async saveMicroAdventure(_pick: MicroIdea | null): Promise<void> {
    await wait(LATENCY_MS);
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
