/* pie.test.ts — allocator-rewrite T8 geometry. The interesting failures here
   are geometric: a 100% slice (a degenerate SVG arc that renders nothing), a
   zero-total day, and an overassigned day where the segments exceed the
   circle. */

import { describe, expect, it } from "vitest";
import { UNALLOCATED_COLOR, pieSlices, pieSummary, wedgePath } from "./pie";
import type { Capacity } from "./types";

function cap(over: Partial<Capacity> = {}): Capacity {
  return {
    total: 20, fixed: 2, anchored: 4, habits: 0, mint: 0, selected: 4,
    buffer: 0, free: 10, overassigned: false, availableForSelection: 10,
    remaining: "", ratio: "", legend: "", counters: "", ...over,
  };
}

describe("wedgePath", () => {
  it("empty for a zero-width wedge", () => {
    expect(wedgePath(50, 50, 40, 0.3, 0.3)).toBe("");
  });

  it("empty for an inverted wedge", () => {
    expect(wedgePath(50, 50, 40, 0.6, 0.2)).toBe("");
  });

  it("a full circle renders as two arcs, not a degenerate no-op", () => {
    const d = wedgePath(50, 50, 40, 0, 1);
    expect(d.match(/A /g) ?? []).toHaveLength(2);
    expect(d).not.toContain("M 50 50"); // no centre point — it's a disc
  });

  it("a normal wedge starts at the centre", () => {
    expect(wedgePath(50, 50, 40, 0, 0.25).startsWith("M 50 50")).toBe(true);
  });

  it("sets the large-arc flag past a half turn", () => {
    expect(wedgePath(50, 50, 40, 0, 0.75)).toMatch(/A 40 40 0 1 1/);
    expect(wedgePath(50, 50, 40, 0, 0.25)).toMatch(/A 40 40 0 0 1/);
  });

  it("the first slice starts at twelve o'clock", () => {
    // 12 o'clock on a cx=50,cy=50,r=40 circle is (50, 10).
    expect(wedgePath(50, 50, 40, 0, 0.25)).toContain("L 50 10");
  });
});

describe("pieSlices", () => {
  it("null capacity yields nothing", () => {
    expect(pieSlices(null, 50, 50, 40)).toEqual([]);
  });

  it("a zero-total day yields nothing rather than dividing by zero", () => {
    expect(pieSlices(cap({ total: 0 }), 50, 50, 40)).toEqual([]);
  });

  it("drops zero-width segments instead of emitting invisible wedges", () => {
    const keys = pieSlices(cap(), 50, 50, 40).map((s) => s.key);
    expect(keys).not.toContain("habits");
    expect(keys).not.toContain("mint");
  });

  it("includes an unallocated remainder", () => {
    const slice = pieSlices(cap(), 50, 50, 40).find((s) => s.key === "unallocated");
    expect(slice?.blocks).toBe(10);
    expect(slice?.color).toBe(UNALLOCATED_COLOR);
  });

  it("fractions sum to one", () => {
    const total = pieSlices(cap(), 50, 50, 40).reduce((n, s) => n + s.fraction, 0);
    expect(total).toBeCloseTo(1);
  });

  it("keeps the engine's segment order", () => {
    const keys = pieSlices(
      cap({ habits: 2, mint: 2, buffer: 2, free: 4 }), 50, 50, 40,
    ).map((s) => s.key);
    expect(keys).toEqual([
      "fixed", "anchored", "habits", "mint", "selected", "buffer", "unallocated",
    ]);
  });

  it("a fully-booked day has no unallocated slice", () => {
    const keys = pieSlices(
      cap({ total: 10, fixed: 10, anchored: 0, selected: 0, free: 0 }),
      50, 50, 40,
    ).map((s) => s.key);
    expect(keys).toEqual(["fixed"]);
  });

  it("overassignment normalizes to the allocated total, never hidden", () => {
    // 30 blocks of segments in a 20-block day: every slice stays proportional
    // and unallocated simply vanishes.
    const slices = pieSlices(
      cap({ total: 20, fixed: 10, anchored: 10, selected: 10, free: -10 }),
      50, 50, 40,
    );
    expect(slices.map((s) => s.key)).toEqual(["fixed", "anchored", "selected"]);
    for (const s of slices) expect(s.fraction).toBeCloseTo(1 / 3);
  });

  it("localSelected overrides the server's selected, tracking the sliders", () => {
    const slice = pieSlices(cap(), 50, 50, 40, 9).find((s) => s.key === "selected");
    expect(slice?.blocks).toBe(9);
  });

  it("localSelected of zero drops the selected slice", () => {
    expect(pieSlices(cap(), 50, 50, 40, 0).map((s) => s.key))
      .not.toContain("selected");
  });

  it("negative server values are floored rather than inverting a wedge", () => {
    expect(pieSlices(cap({ fixed: -5 }), 50, 50, 40).map((s) => s.key))
      .not.toContain("fixed");
  });

  it("every emitted slice has a non-empty path", () => {
    for (const s of pieSlices(cap(), 50, 50, 40)) expect(s.d).not.toBe("");
  });

  it("colors are CSS custom properties, so themes follow tokens.css", () => {
    for (const s of pieSlices(cap(), 50, 50, 40)) {
      expect(s.color).toMatch(/^var\(--/);
    }
  });
});

describe("pieSummary", () => {
  it("names every slice with blocks and percentage", () => {
    expect(pieSummary(pieSlices(cap(), 50, 50, 40)))
      .toBe("Fixed 2 blk (10%), Anchored 4 blk (20%), Selected 4 blk (20%), Unallocated 10 blk (50%)");
  });

  it("says something useful when there is nothing to show", () => {
    expect(pieSummary([])).toBe("No capacity to allocate yet.");
  });
});
