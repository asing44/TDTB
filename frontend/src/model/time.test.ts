import { describe, expect, it } from "vitest";
import { compactDuration, display12h } from "./time";

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
