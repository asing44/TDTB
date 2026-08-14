import { describe, expect, it } from "vitest";
import { FixtureAdapter, fixtureCapacity, fixtureValidate } from "./fixture";
import { makeScenario } from "../fixtures/scenarios";
import type { SequenceRow } from "../model/types";

describe("fixtureCapacity (capacity.py mirror)", () => {
  it("matches the canonical 6-segment math and readouts", () => {
    const c = fixtureCapacity(31, 3, 8, 1, 13, 0.2);
    expect(c.buffer).toBe(4); // ceil(19 * 0.2)
    expect(c.free).toBe(2);
    expect(c.overassigned).toBe(false);
    expect(c.remaining).toBe("⬆ 1hr left · 2 blk");
    expect(c.ratio).toBe("29 / 31 blk");
  });

  it("goes signed-negative and OVERASSIGNED when over", () => {
    const c = fixtureCapacity(31, 3, 8, 1, 18, 0.2);
    expect(c.free).toBe(-3);
    expect(c.overassigned).toBe(true);
    expect(c.remaining).toBe("⚠ 1hr 30min over · 3 blk");
  });

  it("fully booked reads exactly like the server", () => {
    const c = fixtureCapacity(31, 3, 8, 1, 15, 0.2);
    expect(c.free).toBe(0);
    expect(c.remaining).toBe("⬆ fully booked · 0 blk left");
  });
});

describe("fixtureValidate", () => {
  const inputs = makeScenario("ready").inputs;

  it("passes the clean staged sequence", () => {
    const rows = makeScenario("sequenced").staged.sequence!;
    const v = fixtureValidate(rows, inputs);
    expect(v.ok).toBe(true);
    expect(v.hardErrors).toEqual([]);
  });

  it("flags overlap with a non-overlappable anchored block", () => {
    const rows: SequenceRow[] = [
      { id: "Press", start: "17:30", end: "18:45", zone: null, kind: "work" },
    ];
    const v = fixtureValidate(rows, inputs);
    expect(v.ok).toBe(false);
    expect(v.hardErrors.join(" ")).toContain("Sudsing");
  });

  it("allows overlap with template (overlap_allowed) blocks", () => {
    const rows: SequenceRow[] = [
      { id: "Note Processing", start: "13:00", end: "13:30", zone: null, kind: "work" },
    ];
    // 13:00 overlaps the Live window (overlap allowed) but nothing hard.
    const v = fixtureValidate(rows, inputs);
    expect(v.ok).toBe(true);
  });

  it("flags work-row mutual overlap and out-of-frame placement", () => {
    const rows: SequenceRow[] = [
      { id: "A", start: "10:00", end: "11:00", zone: null, kind: "work" },
      { id: "B", start: "10:30", end: "11:30", zone: null, kind: "work" },
      { id: "C", start: "23:30", end: "23:59", zone: null, kind: "work" },
    ];
    const v = fixtureValidate(rows, inputs);
    expect(v.hardErrors.some((e) => e.includes("'A' overlaps 'B'"))).toBe(true);
    expect(v.hardErrors.some((e) => e.includes("outside the day frame"))).toBe(true);
  });

  it("ignores zone backdrop rows entirely", () => {
    const rows: SequenceRow[] = [
      { id: "Trinoor", start: "08:30", end: "17:00", zone: "Trinoor", kind: "zone" },
    ];
    const v = fixtureValidate(rows, inputs);
    expect(v.ok).toBe(true);
  });
});

describe("FixtureAdapter", () => {
  it("autoSequence charges the ledger once per call", async () => {
    const a = new FixtureAdapter("ready");
    const before = await a.billedLedger();
    expect(before.spent).toBe(0);
    await a.autoSequence({ included: [] });
    const after = await a.billedLedger();
    expect(after.spent).toBe(1);
    expect(after.remaining).toBe(3);
  });

  it("throws 429-style when the budget is exhausted", async () => {
    const a = new FixtureAdapter("ready");
    for (let i = 0; i < 4; i++) await a.autoSequence({ included: [] });
    await expect(a.autoSequence({ included: [] })).rejects.toThrow(/budget exhausted/);
  }, 20000);

  it("conflict scenario's autoSequence fails like a 422", async () => {
    const a = new FixtureAdapter("conflict");
    await expect(a.autoSequence({ included: [] })).rejects.toThrow(/overlaps Sudsing/);
  });

  it("simulateDrift changes the fixed-input read", async () => {
    const a = new FixtureAdapter("ready");
    const before = await a.readFixedInputs();
    a.simulateDrift();
    const after = await a.readFixedInputs();
    expect(after.calendar.length).toBe(before.calendar.length + 1);
  });

  it("simulateSourceFailure makes fixed-input reads throw", async () => {
    const a = new FixtureAdapter("ready");
    a.simulateSourceFailure();
    await expect(a.readFixedInputs()).rejects.toThrow(/read failed/);
  });

  it("plan inputs are assigned-only — no pool or suggested fields", async () => {
    const a = new FixtureAdapter("fresh");
    const inputs = await a.loadPlanInputs();
    expect(inputs.assigned.length).toBeGreaterThan(0);
    expect((inputs as unknown as Record<string, unknown>)["suggested"]).toBeUndefined();
    expect((inputs as unknown as Record<string, unknown>)["pool"]).toBeUndefined();
  });
});
