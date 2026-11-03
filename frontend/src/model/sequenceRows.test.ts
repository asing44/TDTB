import { describe, expect, it } from "vitest";
import { isSequenceRow, isWireSequenceRow, sanitizeSequenceRows } from "./sequenceRows";

describe("sequence row shape checks", () => {
  it("keeps well-formed rows and drops the rest", () => {
    const good = { id: "a", start: "09:00", end: "09:30", zone: null, kind: "work" };
    expect(
      sanitizeSequenceRows([good, { id: "b", start: "9am", end: "10:00", kind: "work" }, null, 4, { ...good, kind: "x" }]),
    ).toEqual([good]);
    expect(sanitizeSequenceRows("nope")).toEqual([]);
    expect(isSequenceRow({ ...good, id: "" })).toBe(false);
  });

  it("accepts a server-shaped row with id/start/end only", () => {
    expect(isWireSequenceRow({ id: "x", start: "09:00 AM", end: "10:00 AM" })).toBe(true);
    expect(isWireSequenceRow({ start: "09:00", end: "10:00" })).toBe(false);
    expect(isWireSequenceRow([])).toBe(false);
  });
});
