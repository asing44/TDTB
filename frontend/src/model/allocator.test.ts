/* allocator.test.ts — allocator-rewrite T7: the pure model behind the block
   budget table. Ordering must mirror the server's _rank_key, and the live
   remaining must be the same number the server would compute, re-derived. */

import { describe, expect, it } from "vitest";
import {
  BLOCK_STEP,
  MAX_BLOCKS,
  MIN_BLOCKS,
  clampBlocks,
  importanceKey,
  liveFree,
  orderByImportance,
  remainingLabel,
} from "./allocator";
import type { AssignedItem, Capacity } from "./types";

const TODAY = "2026-07-26";

function item(name: string, over: Partial<AssignedItem> = {}): AssignedItem {
  return {
    id: name, name, path: `p/${name}.md`, source: "vault", types: [],
    urgency: null, deadline: null, priorityScore: 0, blocks: 1,
    durationLabel: "30min", todoistId: null, ...over,
  };
}

function cap(over: Partial<Capacity> = {}): Capacity {
  return {
    total: 42, fixed: 0, anchored: 0, habits: 0, mint: 0, selected: 4,
    buffer: 0, free: 38, overassigned: false, availableForSelection: 38,
    remaining: "", ratio: "", legend: "", counters: "", ...over,
  };
}

describe("orderByImportance", () => {
  it("higher urgency first — names chosen so alphabetical order OPPOSES it", () => {
    const rows = orderByImportance(
      [item("aaa", { urgency: "2-med" }), item("zzz", { urgency: "4-crit" })],
      TODAY,
    );
    expect(rows.map((r) => r.name)).toEqual(["zzz", "aaa"]);
  });

  it("ranks every vault tier, not just the extremes", () => {
    const rows = orderByImportance([
      item("d", { urgency: "1-low" }),
      item("c", { urgency: "2-med" }),
      item("b", { urgency: "3-high" }),
      item("a", { urgency: "4-crit" }),
    ], TODAY);
    expect(rows.map((r) => r.name)).toEqual(["a", "b", "c", "d"]);
  });

  it("unwraps the stringified-list urgency shape gather emits", () => {
    const rows = orderByImportance(
      [item("aaa"), item("zzz", { urgency: "['4-crit']" })], TODAY);
    expect(rows[0].name).toBe("zzz");
  });

  it("ranks a todoist priority row against a vault row", () => {
    const rows = orderByImportance([
      item("aaa", { urgency: "3-high" }),
      item("zzz", { source: "todoist", urgency: "4" }), // Todoist 4 = p1 = crit
    ], TODAY);
    expect(rows[0].name).toBe("zzz");
  });

  it("overdue outranks a same-urgency future deadline", () => {
    const rows = orderByImportance(
      [item("Later", { deadline: "2026-08-30" }),
       item("Overdue", { deadline: "2026-07-01" })],
      TODAY,
    );
    expect(rows.map((r) => r.name)).toEqual(["Overdue", "Later"]);
  });

  it("nearest deadline first among non-overdue rows", () => {
    const rows = orderByImportance(
      [item("Far", { deadline: "2026-09-01" }),
       item("Near", { deadline: "2026-07-30" })],
      TODAY,
    );
    expect(rows.map((r) => r.name)).toEqual(["Near", "Far"]);
  });

  it("undated rows sort after dated ones", () => {
    const rows = orderByImportance(
      [item("Undated"), item("Dated", { deadline: "2026-08-01" })], TODAY);
    expect(rows.map((r) => r.name)).toEqual(["Dated", "Undated"]);
  });

  it("name is the deterministic tie-break", () => {
    const rows = orderByImportance([item("b"), item("a")], TODAY);
    expect(rows.map((r) => r.name)).toEqual(["a", "b"]);
  });

  it("is stable under input shuffle", () => {
    const set = [item("a"), item("b", { urgency: "4-crit" }), item("c")];
    const first = orderByImportance(set, TODAY).map((r) => r.name);
    const second = orderByImportance([...set].reverse(), TODAY).map((r) => r.name);
    expect(first).toEqual(second);
  });

  it("does not mutate its input", () => {
    const set = [item("b"), item("a")];
    orderByImportance(set, TODAY);
    expect(set.map((r) => r.name)).toEqual(["b", "a"]);
  });

  it("tolerates an empty validDate", () => {
    expect(orderByImportance([item("a", { deadline: "2026-01-01" })], "")).toHaveLength(1);
  });

  it("importanceKey ranks urgency into a comparable number", () => {
    expect(importanceKey(item("a", { urgency: "4-crit" }), TODAY)[0]).toBe(-4);
    expect(importanceKey(item("b"), TODAY)[0]).toBeCloseTo(0);
  });
});

describe("clampBlocks", () => {
  it("snaps to the 30-minute grid", () => {
    expect(clampBlocks(2.4)).toBe(2);
    expect(clampBlocks(2.6)).toBe(3);
  });

  it("clamps both bounds", () => {
    expect(clampBlocks(-5)).toBe(MIN_BLOCKS);
    expect(clampBlocks(9999)).toBe(MAX_BLOCKS);
  });

  it("NaN degrades to the minimum rather than poisoning state", () => {
    expect(clampBlocks(Number.NaN)).toBe(MIN_BLOCKS);
  });

  it("zero survives — all-day is a real value, not an error", () => {
    expect(clampBlocks(0)).toBe(0);
  });

  it("the step is one block", () => {
    expect(BLOCK_STEP).toBe(1);
  });
});

describe("liveFree", () => {
  it("substitutes the local selection for the server's", () => {
    expect(liveFree(cap({ free: 38, selected: 4 }), 10)).toBe(32);
  });

  it("agrees with the server when nothing changed", () => {
    expect(liveFree(cap({ free: 38, selected: 4 }), 4)).toBe(38);
  });

  it("goes negative on overassignment rather than clamping", () => {
    expect(liveFree(cap({ free: 2, selected: 4 }), 20)).toBe(-14);
  });

  it("null capacity is zero, never a crash", () => {
    expect(liveFree(null, 10)).toBe(0);
  });
});

describe("remainingLabel", () => {
  it("mirrors the server's 'left' wording", () => {
    expect(remainingLabel(9)).toBe("⬆ 4hr 30min left · 9 blk");
  });

  it("mirrors the server's fully-booked wording", () => {
    expect(remainingLabel(0)).toBe("⬆ fully booked · 0 blk left");
  });

  it("mirrors the server's over wording", () => {
    expect(remainingLabel(-3)).toBe("⚠ 1hr 30min over · 3 blk");
  });

  it("uses minutes below the hour", () => {
    expect(remainingLabel(1)).toBe("⬆ 30min left · 1 blk");
  });

  it("drops the minutes part on a whole hour", () => {
    expect(remainingLabel(4)).toBe("⬆ 2hr left · 4 blk");
  });
});
