import { describe, expect, it } from "vitest";
import { MAX_CUSTOM_MINUTES, minutesToBlocks, parseDuration } from "./parseDuration";

const ok = (text: string) => {
  const r = parseDuration(text);
  return r.ok ? r.minutes : `error: ${r.error}`;
};

describe("parseDuration", () => {
  it.each([
    ["10", 10],
    ["10m", 10],
    ["10min", 10],
    ["10 mins", 10],
    ["10 minutes", 10],
    ["45min", 45],
    ["1h", 60],
    ["1hr", 60],
    ["2 hours", 120],
    ["1h 15m", 75],
    ["1h15", 75],
    ["1hr 30min", 90],
    ["1.5h", 90],
    ["1:30", 90],
    ["0:45", 45],
    ["  25  ", 25],
    ["16h", MAX_CUSTOM_MINUTES],
  ])("accepts %j as %i minutes", (text, minutes) => {
    expect(ok(text)).toBe(minutes);
  });

  it.each(["", "   ", "0", "0m", "0:00", "-5", "-1h", "abc", "10x", "1:75", "17h", "961", "1h 2h"])(
    "rejects %j with a message",
    (text) => {
      const r = parseDuration(text);
      expect(r.ok).toBe(false);
      if (!r.ok) expect(r.error.length).toBeGreaterThan(0);
    },
  );
});

describe("minutesToBlocks", () => {
  it("keeps every minute count exact under the backend's truncating blocks * 30", () => {
    for (let m = 1; m <= MAX_CUSTOM_MINUTES; m += 1) {
      const blocks = minutesToBlocks(m);
      expect(Math.floor(blocks * 30)).toBe(m);
      expect(Math.round(blocks * 30)).toBe(m);
      expect(blocks).toBeCloseTo(m / 30, 9);
    }
  });

  it("leaves whole blocks and halves untouched", () => {
    expect(minutesToBlocks(30)).toBe(1);
    expect(minutesToBlocks(45)).toBe(1.5);
  });
});
