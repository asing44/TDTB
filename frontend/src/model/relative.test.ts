/* relative.test — unit coverage for the small relative-age formatter the
   Sources details popover and the rail readout share. ``now`` is injected so
   the labels are deterministic. */

import { describe, expect, it } from "vitest";
import { formatRelative } from "./relative";

const NOW = new Date("2026-10-13T15:00:00");
const minus = (ms: number) => new Date(NOW.getTime() - ms);

describe("formatRelative", () => {
  it("reads just now under a minute", () => {
    expect(formatRelative(NOW, minus(20_000))).toBe("just now");
    expect(formatRelative(NOW, NOW)).toBe("just now");
  });

  it("reads minutes under an hour", () => {
    expect(formatRelative(NOW, minus(60_000))).toBe("1 min ago");
    expect(formatRelative(NOW, minus(12 * 60_000))).toBe("12 min ago");
    expect(formatRelative(NOW, minus(59 * 60_000))).toBe("59 min ago");
  });

  it("reads hours under a day", () => {
    expect(formatRelative(NOW, minus(60 * 60_000))).toBe("1 h ago");
    expect(formatRelative(NOW, minus(3 * 60 * 60_000))).toBe("3 h ago");
  });

  it("reads yesterday across the calendar boundary", () => {
    expect(formatRelative(NOW, new Date("2026-10-12T09:00:00"))).toBe("yesterday");
  });

  it("falls back to a date for older timestamps", () => {
    const then = new Date("2026-10-08T09:00:00");
    expect(formatRelative(NOW, then)).toBe(
      then.toLocaleDateString(undefined, { month: "short", day: "numeric" }),
    );
  });

  it("adds the year when the timestamp is a different year", () => {
    const then = new Date("2025-12-30T09:00:00");
    expect(formatRelative(NOW, then)).toBe(
      then.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" }),
    );
  });

  it("treats a future timestamp as just now and an invalid one as unknown", () => {
    expect(formatRelative(NOW, new Date(NOW.getTime() + 60_000))).toBe("just now");
    expect(formatRelative(NOW, new Date(Number.NaN))).toBe("unknown");
  });
});
