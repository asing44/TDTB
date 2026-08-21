/* overflow.test — deterministic placement for an overbooked day.

   Origin (2026-07-27): a 5:30 PM frame with 18 blocks of existing commitments
   in 12 blocks of room. The sequencer dropped nine rows, every one read "no
   slot found", and none of them reached Todoist / vault / calendar — so none
   reached BusyCal. Overflow gives them real times from the frame anchor. */

import { describe, expect, it } from "vitest";
import { calendarWalls, mintWalls, overflowBlocks, overflowRows, planOverflow } from "./overflow";
import type { AnchoredBlock, AssignedItem, SequenceRow } from "./types";

function toMin(hhmm: string): number {
  const [h, m] = hhmm.split(":").map(Number);
  return h * 60 + m;
}

function item(name: string, blocks: number): AssignedItem {
  return {
    id: name, name, path: null, source: "vault", types: [], urgency: null,
    deadline: null, priorityScore: 0, blocks, durationLabel: "", todoistId: null,
    labels: [],
  };
}

const blocksOf = (i: AssignedItem) => i.blocks;

function calendar(
  id: string,
  start: string | null,
  end: string | null,
  extra: Partial<AnchoredBlock> = {},
): AnchoredBlock {
  return {
    id, name: id, kind: "calendar", start, end,
    durationMin: 0, overlapAllowed: false, on: true, skipToday: false,
    ...extra,
  };
}

describe("overflowRows", () => {
  it("lays rows back to back from the anchor, keeping each duration", () => {
    const rows = overflowRows(
      [item("A", 1), item("B", 2), item("C", 0.5)],
      "17:30",
      blocksOf,
    );
    expect(rows.map((r) => [r.id, r.start, r.end])).toEqual([
      ["A", "17:30", "18:00"],
      ["B", "18:00", "19:00"],
      ["C", "19:00", "19:15"],
    ]);
  });

  it("rows never overlap EACH OTHER — that shape hard-blocks the next Send", () => {
    const rows = overflowRows([item("A", 2), item("B", 2)], "17:30", blocksOf);
    expect(rows[0].end).toBe(rows[1].start);
  });

  it("preserves caller order, which arrives importance-ordered", () => {
    const rows = overflowRows([item("Z", 1), item("A", 1)], "09:00", blocksOf);
    expect(rows.map((r) => r.id)).toEqual(["Z", "A"]);
  });

  it("all-day rows are skipped — no duration, nothing to lay out", () => {
    const rows = overflowRows([item("allday", 0), item("real", 1)], "17:30", blocksOf);
    expect(rows.map((r) => r.id)).toEqual(["real"]);
    expect(rows[0].start).toBe("17:30");
  });

  it("stops at midnight rather than wrapping — a wrapped time would be written", () => {
    const rows = overflowRows(
      [item("A", 4), item("B", 4), item("C", 4)],
      "23:00",
      blocksOf,
    );
    // 23:00 + 2h would pass midnight; the first row clamps and the rest stop.
    expect(rows).toHaveLength(1);
    expect(rows[0].start).toBe("23:00");
    expect(rows[0].end).toBe("23:59");
  });

  it("no anchor yields nothing rather than a midnight pile", () => {
    expect(overflowRows([item("A", 1)], "", blocksOf)).toEqual([]);
  });

  it("overflowBlocks totals what the overflow occupies", () => {
    const rows = overflowRows([item("A", 1), item("B", 2)], "17:30", blocksOf);
    expect(overflowBlocks(rows)).toBe(3);
  });
});

// FEEDBACK-02: overflow is wall-blind — dropped rows were laid out straight
// through non-permeable calendar events (the screenshot showed Magic Mirror
// overlapping Cooking at 20:30). The overflow layout must skip calendar walls
// the way the server validator now hard-rejects them.
describe("overflowRows wall avoidance", () => {
  it("skips a wall interval and continues after it", () => {
    const rows = overflowRows(
      [item("A", 1), item("B", 1)],
      "17:30",
      blocksOf,
      [{ start: 18 * 60, end: 19 * 60 }], // 18:00–19:00 wall
    );
    expect(rows.map((r) => [r.id, r.start, r.end])).toEqual([
      ["A", "17:30", "18:00"],
      ["B", "19:00", "19:30"],
    ]);
  });

  it("moves a row that would straddle a wall after the wall", () => {
    const rows = overflowRows(
      [item("A", 2)],
      "17:30",
      blocksOf,
      [{ start: 17 * 60 + 45, end: 18 * 60 + 30 }], // 17:45–18:30 wall
    );
    expect(rows.map((r) => [r.id, r.start, r.end])).toEqual([
      ["A", "18:30", "19:30"],
    ]);
  });

  it("jumps past a wall that contains the anchor", () => {
    const rows = overflowRows(
      [item("A", 1)],
      "17:30",
      blocksOf,
      [{ start: 17 * 60, end: 18 * 60 + 30 }], // 17:00–18:30 wall
    );
    expect(rows.map((r) => [r.id, r.start, r.end])).toEqual([
      ["A", "18:30", "19:00"],
    ]);
  });

  it("omits rows that cannot fit before a wall running to end of day", () => {
    const rows = overflowRows(
      [item("A", 1), item("B", 1)],
      "17:30",
      blocksOf,
      [{ start: 18 * 60, end: 24 * 60 }], // wall to midnight
    );
    expect(rows.map((r) => r.id)).toEqual(["A"]);
  });

  it("keeps rows non-overlapping with each other even with walls present", () => {
    const rows = overflowRows(
      [item("A", 1), item("B", 1), item("C", 1)],
      "17:30",
      blocksOf,
      [{ start: 18 * 60, end: 18 * 60 + 30 }],
    );
    for (let i = 1; i < rows.length; i++) {
      expect(toMin(rows[i].start)).toBeGreaterThanOrEqual(toMin(rows[i - 1].end));
    }
    for (const r of rows) {
      const s = toMin(r.start);
      const e = toMin(r.end);
      expect(s >= 18 * 60 + 30 || e <= 18 * 60).toBe(true); // no wall overlap
    }
  });
});

describe("calendarWalls", () => {
  it("selects only on, attending, non-ignored calendar events with times", () => {
    const anchored: AnchoredBlock[] = [
      calendar("Cooking", "20:30", "21:00", { capacityClass: "fixed" }),
      calendar("Trinoor Standup", "09:15", "09:45", { capacityClass: "work" }),
      calendar("Dismissed", "11:00", "11:30", { skipToday: true }),
      calendar("Off", "12:00", "12:30", { on: false }),
      calendar("Ignored", "13:00", "13:30", { capacityClass: "ignored" }),
      { id: "Morning Routine", name: "Morning Routine", kind: "hard", start: "07:45", end: "09:05", durationMin: 80, overlapAllowed: false, on: true, skipToday: false },
      { id: "Foods Dinner", name: "Foods Dinner", kind: "window", start: "18:00", end: "20:30", durationMin: 60, overlapAllowed: false, on: true, skipToday: false },
      { id: "Live", name: "Live", kind: "template", start: "12:00", end: "20:00", durationMin: 30, overlapAllowed: true, on: true, skipToday: false },
    ];
    expect(calendarWalls(anchored)).toEqual([
      { start: 9 * 60 + 15, end: 9 * 60 + 45 },
      { start: 20 * 60 + 30, end: 21 * 60 },
    ]);
  });

  it("derives end from durationMin when end is absent", () => {
    const anchored: AnchoredBlock[] = [
      calendar("Vague", "10:00", null, { durationMin: 45 }),
    ];
    expect(calendarWalls(anchored)).toEqual([{ start: 600, end: 645 }]);
  });

  it("drops zero-length or missing-start rows", () => {
    const anchored: AnchoredBlock[] = [
      calendar("All day", null, null, { durationMin: 0 }),
      calendar("Zero len", "10:00", "10:00", { durationMin: 0 }),
    ];
    expect(calendarWalls(anchored)).toEqual([]);
  });

  // FEEDBACK-04 (2026-08-14): quarantined rows are excluded from planning on
  // the server (contract 17) — the client overflow scan must not turn one into
  // a hidden hard wall. Only the fixed row beside it walls the interval.
  it("excludes quarantined rows — they never become hidden walls", () => {
    const anchored: AnchoredBlock[] = [
      calendar("Steelers Game", "20:00", "22:00", {
        capacityClass: "quarantined",
      }),
      calendar("Cooking", "20:30", "21:00", { capacityClass: "fixed" }),
    ];
    expect(calendarWalls(anchored)).toEqual([
      { start: 20 * 60 + 30, end: 21 * 60 },
    ]);
  });
});

// FEEDBACK-03 (2026-08-14): wall-blind overflow is replaced by free-gap
// placement. planOverflow scans gaps around non-permeable walls AND immutable
// pinned rows (`occupied`), and a row no gap can hold is reported explicitly
// (infeasible) with its need and the available capacity — never silently
// omitted, never placed over a wall or a pin.
describe("planOverflow free-gap placement", () => {
  it("places rows into free gaps around walls and occupied pinned rows", () => {
    const plan = planOverflow(
      [item("A", 1), item("B", 1)],
      "17:30",
      blocksOf,
      [{ start: 18 * 60, end: 19 * 60 }], // wall 18:00-19:00
      [{ start: 19 * 60, end: 19 * 60 + 30 }], // pinned row 19:00-19:30
    );
    expect(plan.rows.map((r) => [r.id, r.start, r.end])).toEqual([
      ["A", "17:30", "18:00"],
      ["B", "19:30", "20:00"],
    ]);
    expect(plan.infeasible).toEqual([]);
  });

  it("moves a row that would straddle a wall to the gap after it", () => {
    const plan = planOverflow(
      [item("A", 2)],
      "17:30",
      blocksOf,
      [{ start: 17 * 60 + 45, end: 18 * 60 + 30 }], // 17:45-18:30 wall
    );
    expect(plan.rows.map((r) => [r.id, r.start, r.end])).toEqual([
      ["A", "18:30", "19:30"],
    ]);
    expect(plan.infeasible).toEqual([]);
  });

  it("reports rows with no free gap as infeasible, naming need and capacity", () => {
    const plan = planOverflow(
      [item("A", 2)],
      "17:30",
      blocksOf,
      [{ start: 18 * 60, end: 24 * 60 }], // wall to midnight; only 30m free
    );
    expect(plan.rows).toEqual([]);
    expect(plan.infeasible).toHaveLength(1);
    const [f] = plan.infeasible;
    expect(f.id).toBe("A");
    expect(f.blocks).toBe(2);
    expect(f.freeBlocks).toBe(1);
    expect(f.reason).toMatch(/2 blk/);
    expect(f.reason).toMatch(/1 blk/);
    expect(f.reason).toMatch(/17:30-18:00/);
    expect(f.reason).toMatch(/calendar wall/);
  });

  it("reports a pinned row occupying the only gap as infeasible", () => {
    const plan = planOverflow(
      [item("A", 1)],
      "17:30",
      blocksOf,
      [],
      [{ start: 17 * 60, end: 24 * 60 }],
    );
    expect(plan.rows).toEqual([]);
    expect(plan.infeasible).toHaveLength(1);
    expect(plan.infeasible[0].reason).toMatch(/pinned row/);
  });

  it("keeps legacy row omission visible: overflowRows hides, planOverflow reports", () => {
    const dropped = [item("A", 1), item("B", 1)];
    const walls = [{ start: 18 * 60, end: 24 * 60 }];
    expect(overflowRows(dropped, "17:30", blocksOf, walls).map((r) => r.id)).toEqual(["A"]);
    const plan = planOverflow(dropped, "17:30", blocksOf, walls);
    expect(plan.rows.map((r) => r.id)).toEqual(["A"]);
    expect(plan.infeasible.map((f) => f.id)).toEqual(["B"]);
  });

  it("all-day rows are skipped, never infeasible", () => {
    const plan = planOverflow([item("allday", 0), item("real", 1)], "17:30", blocksOf);
    expect(plan.rows.map((r) => r.id)).toEqual(["real"]);
    expect(plan.infeasible).toEqual([]);
  });

  it("no anchor reports every row infeasible instead of a midnight pile", () => {
    const plan = planOverflow([item("A", 1)], "", blocksOf);
    expect(plan.rows).toEqual([]);
    expect(plan.infeasible).toHaveLength(1);
    expect(plan.infeasible[0].reason).toMatch(/anchor/i);
  });
});

// FEEDBACK-25: selected Mint sessions are HARD walls — overflow must not lay a
// dropped row over one (the server now hard-rejects exactly that shape), the
// same contract calendar walls and immutable pins already enforce.
describe("mintWalls (FEEDBACK-25)", () => {
  function row(
    id: string,
    start: string,
    end: string,
    wire: Record<string, unknown> | undefined,
  ): SequenceRow {
    return { id, start, end, zone: null, kind: "work", ...(wire ? { wire } : {}) };
  }

  it("extracts intervals from server session rows carrying mint_session metadata", () => {
    const rows = [
      row("Mint Morning · 08:30", "08:30", "09:00", {
        source: "schedulable", mint_session: true,
      }),
      row("Mint Morning · 09:00", "09:00", "09:30", {
        source: "schedulable", mint_session: true,
      }),
      row("Garage", "10:00", "11:00", undefined),
    ];
    expect(mintWalls(rows)).toEqual([
      { start: 8 * 60 + 30, end: 9 * 60 },
      { start: 9 * 60, end: 9 * 60 + 30 },
    ]);
  });

  it("falls back to the canonical Mint id prefix when wire metadata is absent", () => {
    const rows = [
      row("Mint Afternoon · 13:30", "13:30", "14:00", undefined),
      row("Garage", "10:00", "11:00", undefined),
    ];
    expect(mintWalls(rows)).toEqual([
      { start: 13 * 60 + 30, end: 14 * 60 },
    ]);
  });

  it("ignores non-Mint rows and zero-length rows", () => {
    const rows = [
      row("Mint Morning · 08:30", "08:30", "08:30", undefined),
      row("Minting", "09:00", "09:30", undefined), // legacy aggregate, movable
      row("Garage", "10:00", "11:00", undefined),
    ];
    expect(mintWalls(rows)).toEqual([]);
  });
});

describe("planOverflow avoids Mint walls (FEEDBACK-25)", () => {
  it("moves a dropped row that would straddle a selected Mint interval after it", () => {
    const plan = planOverflow(
      [item("A", 2)],
      "08:00",
      blocksOf,
      [{ start: 8 * 60 + 30, end: 9 * 60 }], // Mint wall 08:30-09:00
    );
    expect(plan.rows.map((r) => [r.id, r.start, r.end])).toEqual([
      ["A", "09:00", "10:00"],
    ]);
    expect(plan.infeasible).toEqual([]);
  });

  it("reports a dropped row a Mint wall to end of day makes infeasible", () => {
    const plan = planOverflow(
      [item("A", 2)],
      "08:00",
      blocksOf,
      [{ start: 8 * 60 + 30, end: 24 * 60 }], // Mint wall to midnight
    );
    expect(plan.rows).toEqual([]);
    expect(plan.infeasible).toHaveLength(1);
    expect(plan.infeasible[0].reason).toMatch(/wall/);
  });
});
