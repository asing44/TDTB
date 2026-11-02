/* creditedFixedDraw — shared clock time is paid for once, so the drawn fixed
   segments must shed it or the bar's day-capacity marker lands past the end of
   the day. Segments keep their own sizes in every text ledger; this is the
   drawn-geometry view only. */

import { describe, expect, it } from "vitest";
import { creditedFixedDraw } from "./capacitySegments";
import type { Capacity } from "./types";

function cap(over: Partial<Capacity> = {}): Capacity {
  return {
    total: 20, fixed: 4, anchored: 3, habits: 0, mint: 0, selected: 0,
    buffer: 0, free: 10, overassigned: false, availableForSelection: 10,
    remaining: "", ratio: "", legend: "", counters: "", overlap: 0, ...over,
  };
}

describe("creditedFixedDraw", () => {
  it("leaves both segments alone when nothing is shared", () => {
    expect(creditedFixedDraw(cap())).toEqual({ fixed: 4, anchored: 3 });
  });

  it("sheds the shared amount from anchors before calendar", () => {
    expect(creditedFixedDraw(cap({ overlap: 2 }))).toEqual({ fixed: 4, anchored: 1 });
  });

  it("spills past anchors into calendar when anchors are smaller", () => {
    expect(creditedFixedDraw(cap({ overlap: 5 }))).toEqual({ fixed: 2, anchored: 0 });
  });

  it("never pushes a segment negative on a malformed credit", () => {
    expect(creditedFixedDraw(cap({ overlap: 99 }))).toEqual({ fixed: 0, anchored: 0 });
  });

  it("treats a missing capacity read or a missing overlap as zero", () => {
    expect(creditedFixedDraw(null)).toEqual({ fixed: 0, anchored: 0 });
    expect(creditedFixedDraw(cap({ overlap: undefined }))).toEqual({ fixed: 4, anchored: 3 });
  });
});
