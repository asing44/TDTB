/* pins.test — the client-side guard against a pin set the server will refuse.

   Regression origin (2026-07-27, Adam's 17:00 re-plan): the morning sequence
   stacked four rows at 10:45–11:15, those placements became pins, and every
   later Send hard-failed with six pairwise "pinned rows … overlap" errors that
   nothing on screen could clear. */

import { describe, expect, it } from "vitest";
import { droppedPins, prunePins } from "./pins";
import type { SequenceRow } from "./types";

function pin(id: string, start: string, end: string): SequenceRow {
  return { id, start, end, zone: null, kind: "work" };
}

describe("prunePins", () => {
  it("keeps a non-overlapping set untouched", () => {
    const pins = [pin("A", "09:00", "09:30"), pin("B", "09:30", "10:00")];
    expect(prunePins(pins).map((p) => p.id)).toEqual(["A", "B"]);
    expect(droppedPins(pins)).toEqual([]);
  });

  it("touching edges do not overlap — end == start is adjacency", () => {
    const pins = [pin("A", "09:00", "09:30"), pin("B", "09:30", "10:00")];
    expect(droppedPins(pins)).toEqual([]);
  });

  it("the real failure: four rows stacked on one slot collapse to one", () => {
    const pins = [
      pin("Note Processing", "10:45", "11:15"),
      pin("Frequent CWEAN", "10:45", "11:15"),
      pin("Reading", "10:45", "11:15"),
      pin("Stillness", "10:45", "11:15"),
    ];
    const kept = prunePins(pins);
    expect(kept).toHaveLength(1);
    // Ties break on id, so the survivor is stable across reloads rather than
    // depending on whatever order persistence happened to rehydrate.
    expect(kept[0].id).toBe("Frequent CWEAN");
    expect(droppedPins(pins).map((p) => p.id).sort()).toEqual([
      "Note Processing",
      "Reading",
      "Stillness",
    ]);
  });

  it("earliest start wins a partial overlap", () => {
    const pins = [pin("late", "09:15", "09:45"), pin("early", "09:00", "09:30")];
    expect(prunePins(pins).map((p) => p.id)).toEqual(["early"]);
  });

  it("a row that fits in the gap after a drop is still kept", () => {
    const pins = [
      pin("A", "09:00", "10:00"),
      pin("B", "09:30", "10:30"), // collides with A — dropped
      pin("C", "10:00", "10:30"), // free once B is gone
    ];
    expect(prunePins(pins).map((p) => p.id)).toEqual(["A", "C"]);
  });

  it("malformed pins pass through — the server names them precisely", () => {
    // Swallowing these would hide a real bug behind a silent drop.
    const pins = [pin("ok", "09:00", "09:30"), pin("backwards", "10:00", "09:00")];
    expect(prunePins(pins).map((p) => p.id).sort()).toEqual(["backwards", "ok"]);
  });
});
