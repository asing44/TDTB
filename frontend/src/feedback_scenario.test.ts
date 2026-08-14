/* feedback_scenario.test.ts — FEEDBACK-05 (2026-08-14): end-to-end regression
   for the reported calendar scenario.

   The reported day carried Cooking at 20:30, Foods Dinner, DCP Bark Bar
   trivia, Steelers vs Packers, assigned rows overlapping those blocks, and
   only 2 available blocks versus 16 included blocks. This suite drives the
   REAL controller (Controller + store) against the shared fixture
   (frontend/src/fixtures/feedbackScenario.ts) with fake sources and a call
   ledger, and pins the FEEDBACK-01..04 contracts end to end:

   - the fixture represents every named event and assigned row;
   - no assigned row overlaps a non-permeable wall (Cooking / DCP trivia);
   - quarantined Steelers exclusion consumes no capacity and never becomes a
     hidden wall;
   - over-assignment produces explicit infeasibility diagnostics, never silent
     placement;
   - the final merged sequence is chronological;
   - zero calendar source writer calls (liveCommit/shadowCommit/runtime verbs
     are never reached by load → setup → sequence). */

import { describe, expect, it, vi } from "vitest";
import { createStore } from "./store/createStore";
import { Controller } from "./store/controller";
import { FixtureAdapter } from "./adapters/fixture";
import { defectsResolved, effectiveAnchoredBlocks } from "./store/store";
import { calendarWalls, planOverflow } from "./model/overflow";
import { fixedInputsOf } from "./fixtures/scenarios";
import {
  FEEDBACK_ANCHORED,
  FEEDBACK_ASSIGNED,
  FEEDBACK_INCLUDED_BLOCKS,
  feedbackCapacity,
  feedbackInputs,
} from "./fixtures/feedbackScenario";
import type { AnchoredBlock, SequenceRow } from "./model/types";

function toMin(hhmm: string): number {
  const [h, m] = hhmm.split(":").map(Number);
  return h * 60 + m;
}

function harness() {
  const store = createStore();
  const adapter = new FixtureAdapter("ready");
  const controller = new Controller(adapter, store.dispatch, store.getState);
  return { store, adapter, controller };
}

/** The reported FEEDBACK-01 server shape: the immovable Magic Mirror morning
    row plus a 23:15 row, dropping the other 14 blocks of demand so overflow
    lays them out from the late 17:15 anchor. */
function reportedProposal(): {
  sequence: SequenceRow[];
  warnings: string[];
  overlapGrants: never[];
  pinnedRows: SequenceRow[];
} {
  return {
    sequence: [
      { id: "Magic Mirror", start: "10:00", end: "11:30", zone: null, kind: "work" },
      { id: "Pick up prescription", start: "23:15", end: "23:45", zone: null, kind: "work" },
    ],
    warnings: [],
    overlapGrants: [],
    pinnedRows: [
      { id: "Pick up prescription", start: "23:15", end: "23:45", zone: null, kind: "work" },
    ],
  };
}

describe("FEEDBACK-05 fixture represents the reported scenario", () => {
  it("carries every named event with the correct capacity class", () => {
    const byName = new Map(FEEDBACK_ANCHORED.map((a) => [a.name, a]));
    expect(byName.get("Cooking")?.capacityClass).toBe("fixed");
    expect(byName.get("DCP Bark Bar trivia")?.capacityClass).toBe("work");
    expect(byName.get("Steelers vs Packers")?.capacityClass).toBe("quarantined");
    // Foods Dinner is a config window, never a calendar event.
    expect(byName.get("Foods Dinner")?.kind).toBe("window");
    expect(byName.get("Foods Dinner")?.capacityClass).toBeUndefined();
  });

  it("carries the reported assigned rows: 16 included blocks vs 2 available", () => {
    expect(FEEDBACK_INCLUDED_BLOCKS).toBe(16);
    const ids = FEEDBACK_ASSIGNED.map((a) => a.id);
    expect(ids).toContain("Magic Mirror");
    expect(ids).toContain("Log hours");
    const cap = feedbackCapacity();
    expect(cap.availableForSelection).toBe(2);
    expect(cap.selected).toBe(16);
    expect(cap.free).toBe(-14);
    expect(cap.overassigned).toBe(true);
    expect(cap.remaining).toContain("over");
  });
});

describe("FEEDBACK-05 wall safety", () => {
  it("calendarWalls picks Cooking and DCP trivia, never Steelers or the dinner window", () => {
    const walls = calendarWalls(FEEDBACK_ANCHORED);
    expect(walls).toEqual([
      { start: 19 * 60, end: 20 * 60 }, // DCP Bark Bar trivia (work)
      { start: 20 * 60 + 30, end: 21 * 60 }, // Cooking (fixed)
    ]);
    // Quarantined Steelers 20:00-22:00 must not become a hidden wall.
    expect(walls.some((w) => w.start === 20 * 60 && w.end === 22 * 60)).toBe(false);
  });

  it("no assigned row overlaps a non-permeable wall in the free-gap overflow", () => {
    const walls = calendarWalls(FEEDBACK_ANCHORED);
    const plan = planOverflow(
      FEEDBACK_ASSIGNED,
      "17:15",
      (i) => i.blocks,
      walls,
    );
    for (const r of plan.rows) {
      const rs = toMin(r.start);
      const re = toMin(r.end);
      for (const w of walls) {
        expect(
          rs >= w.end || re <= w.start,
          `${r.id} (${r.start}-${r.end}) must not overlap calendar wall ${w.start}-${w.end}`,
        ).toBe(true);
      }
    }
    // 16 blocks of demand against a 2-block-available day MUST overflow into
    // explicit infeasibility — never silent placement. The tail rows (Deep
    // CWEAN last) cannot fit after walls + earlier rows.
    expect(plan.rows.length).toBeGreaterThan(0);
    expect(plan.infeasible.length).toBeGreaterThan(0);
    expect(plan.infeasible.some((f) => f.id === "Deep CWEAN")).toBe(true);
  });

  it("quarantined Steelers exclusion is not a hidden wall in the read model", () => {
    // fixedInputsOf must exclude quarantined rows from calendar commitments
    // (the fingerprint source), matching wire.ts parity.
    const fixed = fixedInputsOf(feedbackInputs());
    expect(fixed.calendar.map((c) => c.name)).toEqual([
      "DCP Bark Bar trivia",
      "Cooking",
    ]);
    expect(fixed.calendar.some((c) => c.name === "Steelers vs Packers")).toBe(false);
  });
});

describe("FEEDBACK-05 controller end-to-end (fake sources, call ledger)", () => {
  it("load → setup → sequence: chronological, wall-safe, explicit infeasibility, zero calendar writer calls", async () => {
    const { store, adapter, controller } = harness();

    vi.spyOn(adapter, "loadPlanInputs").mockResolvedValue({
      ...structuredClone(adapter.scenario.inputs),
      ...feedbackInputs(),
    });
    vi.spyOn(adapter, "autoSequence").mockResolvedValue(reportedProposal());

    // Calendar source writers: any reach means a real write would fire.
    const liveCommit = vi.spyOn(adapter, "liveCommit");
    const shadowCommit = vi.spyOn(adapter, "shadowCommit");
    const runtimeAction = vi.spyOn(adapter, "runtimeAction");
    const undoRuntimeAction = vi.spyOn(adapter, "undoRuntimeAction");

    await controller.load();
    await controller.saveDaySetup({ ...store.getState().daySetup, confirmed: true });
    await controller.autoSequence();

    const s = store.getState();
    const seq = s.sequence!;
    expect(seq.length).toBeGreaterThan(0);

    // FEEDBACK-01: the final merged sequence is chronological — Log hours
    // (overflow, laid out from the 17:15 anchor) precedes the pinned 23:15 row.
    const logHours = seq.find((r) => r.id === "Log hours")!;
    const pickup = seq.find((r) => r.id === "Pick up prescription")!;
    expect(logHours.start).toBe("17:15");
    expect(pickup.start).toBe("23:15");
    for (let i = 1; i < seq.length; i++) {
      expect(seq[i - 1].start <= seq[i].start).toBe(true);
    }
    expect(seq.indexOf(logHours)).toBeLessThan(seq.indexOf(pickup));

    // FEEDBACK-02: no assigned row overlaps a non-permeable wall.
    const walls = calendarWalls(effectiveAnchoredBlocks(s));
    expect(walls.length).toBeGreaterThan(0);
    for (const r of seq.filter((row) => row.kind === "work")) {
      const rs = toMin(r.start);
      const re = toMin(r.end);
      for (const w of walls) {
        expect(
          rs >= w.end || re <= w.start,
          `${r.id} (${r.start}-${r.end}) must not overlap calendar wall ${w.start}-${w.end}`,
        ).toBe(true);
      }
    }

    // FEEDBACK-03: over-assignment surfaces as explicit infeasibility
    // diagnostics naming rows and capacity — never silent placement.
    const warnings = s.validation?.warnings ?? [];
    expect(warnings.some((w) => w.includes("overflow infeasible"))).toBe(true);
    expect(warnings.some((w) => w.includes("Deep CWEAN"))).toBe(true);
    expect(warnings.some((w) => /block/.test(w))).toBe(true);
    expect(defectsResolved(s)).toBe(false); // gates shadow/commit until accepted

    // FEEDBACK-05: zero calendar source writer calls — load → setup →
    // sequence must never reach a commit/runtime calendar writer.
    expect(liveCommit).not.toHaveBeenCalled();
    expect(shadowCommit).not.toHaveBeenCalled();
    expect(runtimeAction).not.toHaveBeenCalled();
    expect(undoRuntimeAction).not.toHaveBeenCalled();
  }, 15000);

  it("infeasible rows are never staged over a wall — they stay out and get named", async () => {
    const { store, adapter, controller } = harness();
    // A wall from anchor to end of day: nothing can fit, so every dropped row
    // is reported and NONE staged (the hard-wall-to-midnight shape).
    vi.spyOn(adapter, "loadPlanInputs").mockResolvedValueOnce({
      ...structuredClone(adapter.scenario.inputs),
      ...feedbackInputs(),
      anchored: [
        ...FEEDBACK_ANCHORED,
        {
          id: "Full-day wall", name: "Full-day wall", kind: "calendar",
          start: "17:15", end: "23:59", durationMin: 0, overlapAllowed: false,
          on: true, skipToday: false, capacityClass: "fixed",
        } as AnchoredBlock,
      ],
    });
    vi.spyOn(adapter, "autoSequence").mockResolvedValue({
      sequence: [
        { id: "Magic Mirror", start: "10:00", end: "11:30", zone: null, kind: "work" },
      ],
      warnings: [],
      overlapGrants: [],
    });

    await controller.load();
    await controller.saveDaySetup({ ...store.getState().daySetup, confirmed: true });
    await controller.autoSequence();

    const s = store.getState();
    const overflow = (s.sequence ?? []).filter((r) => s.overflowIds.includes(r.id));
    // Nothing was staged over the wall — the infeasible rows are not placed.
    expect(overflow).toEqual([]);
    const warnings = s.validation?.warnings ?? [];
    expect(warnings.some((w) => w.includes("overflow infeasible"))).toBe(true);
    expect(warnings.some((w) => w.includes("Note Processing"))).toBe(true);
  }, 15000);
});
