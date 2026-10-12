import { describe, expect, it } from "vitest";
import {
  bandOf,
  BANDS,
  trackFor,
  trackGradient,
  trimPlan,
  stripeColor,
} from "./bands";
import type { AssignedItem } from "./types";

function item(over: Partial<AssignedItem>): AssignedItem {
  return {
    id: over.name ?? "x",
    name: over.name ?? "x",
    path: null,
    source: "todoist",
    types: [],
    urgency: null,
    deadline: null,
    priorityScore: 0,
    blocks: 1,
    durationLabel: "30min",
    todoistId: null,
    ...over,
  };
}

describe("bandOf", () => {
  it("routes todoist priorities to bands (API int, 4 = highest)", () => {
    expect(bandOf(item({ urgency: "4" }))).toBe("crit");
    expect(bandOf(item({ urgency: "3" }))).toBe("high");
    expect(bandOf(item({ urgency: "2" }))).toBe("else");
    expect(bandOf(item({ urgency: "1" }))).toBe("else");
    expect(bandOf(item({ urgency: null }))).toBe("else");
  });

  it("a Capacities row (no urgency scale) lands in the default band", () => {
    expect(bandOf(item({ source: "capacities", urgency: "4" }))).toBe("else");
  });

  it("stripe colour follows tier, untiered falls to low", () => {
    expect(stripeColor(item({ urgency: "4" }))).toBe("var(--c-overflow)");
    expect(stripeColor(item({ urgency: null }))).toBe("var(--t-border)");
  });

  it("band specs cover every tier exactly once", () => {
    const seen = BANDS.flatMap((b) => b.tiers);
    expect(new Set(seen).size).toBe(seen.length);
    expect(seen).toContain("crit");
    expect(seen).toContain("high");
    expect(seen).toContain("med");
    expect(seen).toContain("low");
    expect(seen).toContain(null);
  });

  it("band notes are concise and non-alarmist while preserving urgency (FEEDBACK-10 A15)", () => {
    const byKey = new Map(BANDS.map((b) => [b.key, b]));
    expect(byKey.get("crit")!.note).toBe("do today");
    expect(byKey.get("high")!.note).toBe("soon");
    expect(byKey.get("else")!.note).toBe("when room");
  });
});

describe("trackFor", () => {
  const MAX = 16;

  it("no mark while the remaining budget exceeds the track range", () => {
    const t = trackFor(0, 2, 23, MAX);
    expect(t.markPct).toBeNull();
    expect(t.pastBudget).toBe(false);
    expect(t.fillPct).toBeCloseTo((2 / 16) * 100);
  });

  it("marks the budget point once it falls on the track", () => {
    // 20 blocks already spent of 23 → 3 remaining → mark at 3/16
    const t = trackFor(20, 2, 23, MAX);
    expect(t.markPct).toBeCloseTo((3 / 16) * 100);
    expect(t.pastBudget).toBe(false);
  });

  it("flags a row whose spend crosses the line", () => {
    // 22 spent of 23 → 1 remaining; row takes 3 → crosses
    const t = trackFor(22, 3, 23, MAX);
    expect(t.markPct).toBeCloseTo((1 / 16) * 100);
    expect(t.pastBudget).toBe(true);
  });

  it("clamps the mark to 0 when the budget is already spent", () => {
    const t = trackFor(30, 2, 23, MAX);
    expect(t.markPct).toBe(0);
    expect(t.pastBudget).toBe(true); // everything on this row is over
  });

  it("gradient uses token custom properties only", () => {
    for (const t of [trackFor(0, 2, 23, MAX), trackFor(20, 2, 23, MAX), trackFor(22, 3, 23, MAX)]) {
      const g = trackGradient(t);
      expect(g).toMatch(/var\(--c-selected\)/);
      expect(g).not.toMatch(/#[0-9a-f]{3,8}/i); // no hard-coded hex
    }
  });
});

describe("trimPlan", () => {
  const rows = [
    { id: "c1", item: item({ name: "c1", urgency: "4" }), blocks: 3 },
    { id: "h1", item: item({ name: "h1", urgency: "3" }), blocks: 3 },
    { id: "m1", item: item({ name: "m1", urgency: "2" }), blocks: 2 },
    { id: "l1", item: item({ name: "l1", urgency: "1" }), blocks: 2 },
    { id: "l2", item: item({ name: "l2", urgency: "1" }), blocks: 2 },
  ];

  it("no-op when within budget", () => {
    expect(trimPlan(rows, 12)).toEqual({ drop: [], freed: 0, after: 12, partial: false });
  });

  it("drops from the bottom of the display order until it fits", () => {
    const p = trimPlan(rows, 10);
    expect(p.drop).toEqual(["l2"]);
    expect(p.freed).toBe(2);
    expect(p.after).toBe(10);
    expect(p.partial).toBe(false);
  });

  it("keeps dropping across candidates, skipping all-day rows", () => {
    const withAllDay = [
      ...rows.slice(0, 4),
      { id: "bg", item: item({ name: "bg", urgency: "1" }), blocks: 0 },
      rows[4],
    ];
    const p = trimPlan(withAllDay, 8);
    expect(p.drop).toEqual(["l2", "l1"]);
    expect(p.after).toBe(8);
  });

  it("never drops crit or high — reports partial instead", () => {
    const p = trimPlan(rows, 4);
    expect(p.drop).toEqual(["l2", "l1", "m1"]);
    expect(p.after).toBe(6);
    expect(p.partial).toBe(true);
  });
});
