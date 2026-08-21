import { describe, expect, it } from "vitest";
import {
  blocksLabel,
  compactDuration,
  display12h,
  formatBlockAmount,
  formatDurationMinutes,
} from "./time";

describe("formatBlockAmount", () => {
  it("keeps exact whole blocks as integer counts", () => {
    expect(formatBlockAmount(9)).toBe("9 blk");
  });

  it("turns fractional blocks into an hours/minutes duration", () => {
    expect(formatBlockAmount(9.166666666666668)).toBe("4hr 35min");
    expect(formatBlockAmount(2.5)).toBe("1hr 15min");
    expect(blocksLabel(9.166666666666668)).toBe("4hr 35min");
  });

  it("retains the sign for negative amounts", () => {
    expect(formatBlockAmount(-9.166666666666668)).toBe("-4hr 35min");
    expect(formatBlockAmount(-3)).toBe("-3 blk");
  });

  it("normalizes floating-point values that are effectively whole blocks", () => {
    expect(formatBlockAmount(9.000000000000002)).toBe("9 blk");
    expect(formatBlockAmount(-3.0000000000000004)).toBe("-3 blk");
  });
});

describe("compactDuration", () => {
  it("keeps sub-hour durations in minutes", () => {
    expect(compactDuration(15)).toBe("15m");
    expect(compactDuration(45)).toBe("45m");
  });
  it("renders whole hours bare", () => {
    expect(compactDuration(60)).toBe("1h");
    expect(compactDuration(240)).toBe("4h");
  });
  it("pads minutes in mixed durations", () => {
    expect(compactDuration(90)).toBe("1h30m");
    expect(compactDuration(65)).toBe("1h05m");
    expect(compactDuration(210)).toBe("3h30m");
  });
  it("rounds floating-point minutes and keeps a negative sign", () => {
    expect(compactDuration(275.00000000000006)).toBe("4h35m");
    expect(compactDuration(-90.00000000000001)).toBe("-1h30m");
  });
});

describe("formatDurationMinutes", () => {
  it("omits zero components and retains a sign", () => {
    expect(formatDurationMinutes(45)).toBe("45min");
    expect(formatDurationMinutes(75)).toBe("1hr 15min");
    expect(formatDurationMinutes(120)).toBe("2hr");
    expect(formatDurationMinutes(-75)).toBe("-1hr 15min");
  });
});

describe("display12h (authoritative user-facing format)", () => {
  it("renders morning and evening times with AM/PM", () => {
    expect(display12h("07:05")).toBe("7:05 AM");
    expect(display12h("23:15")).toBe("11:15 PM");
    expect(display12h("14:15")).toBe("2:15 PM");
  });
  it("renders noon and midnight as 12 with the right suffix", () => {
    expect(display12h("12:00")).toBe("12 PM");
    expect(display12h("00:00")).toBe("12 AM");
  });
  it("keeps minutes zero-padded when present", () => {
    expect(display12h("10:05")).toBe("10:05 AM");
    expect(display12h("12:30")).toBe("12:30 PM");
  });
  it("renders null or empty as an em dash", () => {
    expect(display12h(null)).toBe("—");
    expect(display12h("")).toBe("—");
  });
});
