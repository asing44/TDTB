import { describe, expect, it } from "vitest";
import { calendarEditOverlaps, calendarEditState } from "./calendarEdit";
import type { AnchoredBlock } from "./types";

const ev = (over: Partial<AnchoredBlock> & { id: string }): AnchoredBlock => ({
  name: over.id,
  kind: "calendar",
  start: "10:00",
  end: "10:30",
  durationMin: 30,
  overlapAllowed: false,
  on: true,
  skipToday: false,
  capacityClass: "fixed",
  ...over,
});

describe("calendarEditState", () => {
  it("is unedited without a start override", () => {
    expect(calendarEditState(undefined, "19:00")).toEqual({
      edited: false,
      changedInCalendar: false,
      calendarStart: "19:00",
    });
    expect(
      calendarEditState({ on: true, skipToday: false, time: null }, "19:00").edited,
    ).toBe(false);
  });

  it("is edited and not changed while the live start matches the start at edit", () => {
    const s = calendarEditState(
      { on: true, skipToday: false, time: null, start: "20:00", calendarStartAtEdit: "19:00" },
      "19:00",
    );
    expect(s).toEqual({ edited: true, changedInCalendar: false, calendarStart: "19:00" });
  });

  it("flags changed-in-calendar only when the live start differs", () => {
    const s = calendarEditState(
      { on: true, skipToday: false, time: null, start: "20:00", calendarStartAtEdit: "19:00" },
      "19:30",
    );
    expect(s.changedInCalendar).toBe(true);
    expect(s.edited).toBe(true);
  });

  it("never flags a change without a recorded start at edit", () => {
    const s = calendarEditState(
      { on: true, skipToday: false, time: null, start: "20:00" },
      "19:30",
    );
    expect(s.changedInCalendar).toBe(false);
  });
});

describe("calendarEditOverlaps", () => {
  const edited = ev({ id: "Dentist", start: "20:00", end: "20:30" });

  it("reports an edited event overlapping a selected Mint session", () => {
    const out = calendarEditOverlaps({
      blocks: [edited],
      editedIds: ["Dentist"],
      mint: [{ id: "mint:1", name: "Mint 1", start: "20:00", end: "20:30" }],
    });
    expect(out).toEqual([
      "Edited calendar event 'Dentist' (20:00-20:30) overlaps 'Mint 1' (20:00-20:30)",
    ]);
  });

  it("reports an overlap with another counted calendar event and a hard anchor", () => {
    const out = calendarEditOverlaps({
      blocks: [
        edited,
        ev({ id: "Standup", start: "20:15", end: "20:45", durationMin: 30 }),
        ev({ id: "Foods Dinner", kind: "hard", start: "19:45", end: "20:15", durationMin: 30, capacityClass: undefined }),
      ],
      editedIds: ["Dentist"],
      mint: [],
    });
    expect(out).toHaveLength(2);
    expect(out.join("\n")).toContain("'Standup' (20:15-20:45)");
    expect(out.join("\n")).toContain("'Foods Dinner' (19:45-20:15)");
  });

  it("ignores skipped, ignored-class, touching and unedited-pair overlaps", () => {
    const out = calendarEditOverlaps({
      blocks: [
        edited,
        ev({ id: "Skipped", start: "20:00", end: "20:30", skipToday: true }),
        ev({ id: "Silenced", start: "20:00", end: "20:30", capacityClass: "ignored" }),
        ev({ id: "Before", start: "19:30", end: "20:00" }),
        ev({ id: "A", start: "12:00", end: "13:00", durationMin: 60 }),
        ev({ id: "B", start: "12:30", end: "13:30", durationMin: 60 }),
      ],
      editedIds: ["Dentist"],
      mint: [],
    });
    expect(out).toEqual([]);
  });

  it("is empty when the edited event itself is skipped today", () => {
    const out = calendarEditOverlaps({
      blocks: [{ ...edited, skipToday: true }],
      editedIds: ["Dentist"],
      mint: [{ id: "m", name: "Mint 1", start: "20:00", end: "20:30" }],
    });
    expect(out).toEqual([]);
  });
});
