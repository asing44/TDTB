import { describe, expect, it } from "vitest";
import { isMintingName, validateAnchoredOverride } from "./anchored";
import type { AnchoredBlock } from "./types";

const windowBlock: AnchoredBlock = {
  id: "Dinner", name: "Dinner", kind: "window", start: "18:00", end: "20:30",
  durationMin: 60, overlapAllowed: false, on: true, skipToday: false,
};
const frame = { anchor: "07:30", effectiveEod: "23:00" };

describe("validateAnchoredOverride", () => {
  it("accepts zero blocks as visible background capacity", () => {
    expect(validateAnchoredOverride(windowBlock, {
      on: true, skipToday: false, time: "18:00", blocks: 0,
    }, frame)).toEqual({ errors: [], warnings: [] });
  });

  it("warns (never blocks) on a window edit that ends after its source window", () => {
    const f = validateAnchoredOverride(windowBlock, {
      on: true, skipToday: false, time: "20:00", blocks: 2,
    }, frame);
    expect(f.errors).toEqual([]);
    expect(f.warnings.join(" ")).toMatch(/past the window end \(8:30 PM\)/);
  });

  it("warns (never blocks) on a start outside the day frame", () => {
    const f = validateAnchoredOverride(windowBlock, {
      on: true, skipToday: false, time: "05:00", blocks: 1,
    }, frame);
    expect(f.errors).toEqual([]);
    expect(f.warnings.join(" ")).toMatch(/Outside the day frame/);
  });

  it("skips validation entirely for skipped or off blocks", () => {
    expect(validateAnchoredOverride(windowBlock, {
      on: true, skipToday: true, time: "05:00", blocks: 2,
    }, frame)).toEqual({ errors: [], warnings: [] });
    expect(validateAnchoredOverride({ ...windowBlock, kind: "calendar" }, {
      on: false, skipToday: false, time: "05:00", blocks: 2,
    }, frame)).toEqual({ errors: [], warnings: [] });
  });

  it("warns on malformed end-before-start source windows (an effective post-save row can look like this)", () => {
    const f = validateAnchoredOverride({ ...windowBlock, start: "20:30", end: "18:00" }, {
      on: true, skipToday: false, time: "20:30", blocks: 1,
    }, frame);
    expect(f.errors).toEqual([]);
    expect(f.warnings.join(" ")).toMatch(/ends before it starts/);
  });

  it("keeps structural duration validity hard", () => {
    expect(validateAnchoredOverride(windowBlock, {
      on: true, skipToday: false, time: "18:00", blocks: -1,
    }, frame).errors).toEqual(["Duration must use non-negative 30-minute blocks."]);
  });

  it("pins Calendar immutability", () => {
    expect(validateAnchoredOverride({ ...windowBlock, kind: "calendar" }, {
      on: true, skipToday: false, time: "19:00", blocks: 1,
    }, frame).errors).toEqual(["Calendar commitments are read-only."]);
  });
});

describe("isMintingName", () => {
  it("matches Mint anchored rows and the Minting zone", () => {
    expect(isMintingName("Mint Morning")).toBe(true);
    expect(isMintingName("Mint Afternoon")).toBe(true);
    expect(isMintingName("🟡 Minting : Regular")).toBe(true);
  });
  it("rejects non-Mint blocks", () => {
    expect(isMintingName("Morning Routine")).toBe(false);
    expect(isMintingName("Sudsing")).toBe(false);
  });
});
