import { describe, expect, it } from "vitest";
import {
  HABIT_MINUTES_MAX,
  habitsView,
  parseHabitMinutes,
  withHabitEntry,
} from "./habits";
import type { HabitOverrides, HabitsInfo } from "./types";

/* Habits are an ACTIVE list: the artifact lists every habit in the Todoist
   Habits project and completion lives in Grit, so nothing here derives
   done/outstanding counts from the wire. */

const info: HabitsInfo = {
  total: 3,
  estMinutes: 45,
  items: [
    { id: "a", name: "Stretch", minutes: 25 },
    { id: "b", name: "Read", minutes: 20 },
    { id: "c", name: "Water", minutes: 0 },
  ],
};
const aggregateOnly: HabitsInfo = { ...info, items: [] };
const none: HabitOverrides = { items: {}, total: null };

describe("parseHabitMinutes", () => {
  it("accepts whole minutes 0..480", () => {
    expect(parseHabitMinutes("0")).toBe(0);
    expect(parseHabitMinutes(" 25 ")).toBe(25);
    expect(parseHabitMinutes(String(HABIT_MINUTES_MAX))).toBe(480);
  });
  it("rejects empty, fractional, negative, text and out-of-range input", () => {
    for (const bad of ["", "  ", "1.5", "-1", "abc", "12m", "481", "1e2"]) {
      expect(parseHabitMinutes(bad)).toBeNull();
    }
  });
});

describe("habitsView", () => {
  it("lists every habit as an active row and counts them all", () => {
    const v = habitsView(info, none);
    expect(v.mode).toBe("items");
    expect(v.rows.map((r) => [r.key, r.label, r.minutes, r.on, r.edited])).toEqual([
      ["a", "Stretch", 25, true, false],
      ["b", "Read", 20, true, false],
      ["c", "Water", 0, true, false],
    ]);
    expect(v.countedMinutes).toBe(45);
    expect(v.habitCount).toBe(3);
  });

  it("keeps a habit the legacy artifact marked complete on the active list", () => {
    // The old contract filtered completed habits out of `items` and reported
    // the difference as `outstanding`; both are now ignored.
    const legacy: HabitsInfo = {
      total: 3,
      done: 2,
      outstanding: 1,
      estMinutes: 25,
      items: [
        { id: "a", name: "Stretch", minutes: 25 },
        { id: "b", name: "Read", minutes: 20 },
        { id: "c", name: "Water", minutes: 10 },
      ],
    };
    const v = habitsView(legacy, none);
    expect(v.mode).toBe("items");
    expect(v.rows.map((r) => r.key)).toEqual(["a", "b", "c"]);
    expect(v.habitCount).toBe(3);
    expect(v.countedMinutes).toBe(55);
  });

  it("applies overrides to the counted minutes and marks edited rows", () => {
    const v = habitsView(info, {
      items: { a: { on: false }, b: { on: true, minutes: 60 } },
      total: null,
    });
    expect(v.rows[0]).toMatchObject({ key: "a", on: false, edited: true, minutes: 25 });
    expect(v.rows[1]).toMatchObject({ key: "b", on: true, edited: true, minutes: 60 });
    expect(v.countedMinutes).toBe(60);
    // Switching a habit off never removes it from the active list.
    expect(v.habitCount).toBe(3);
  });

  it("ignores stale overrides for habits that are no longer listed", () => {
    const v = habitsView(info, { items: { gone: { on: false } }, total: { on: false } });
    expect(v.rows).toHaveLength(3);
    expect(v.countedMinutes).toBe(45);
  });

  it("falls back to ONE total row when the artifact has no items", () => {
    const v = habitsView(aggregateOnly, none);
    expect(v.mode).toBe("total");
    expect(v.rows).toHaveLength(1);
    expect(v.rows[0]).toMatchObject({ key: "total", label: "Habits (total)", minutes: 45, on: true });
    expect(v.habitCount).toBe(3);
    const off = habitsView(aggregateOnly, { items: {}, total: { on: false } });
    expect(off.countedMinutes).toBe(0);
    const adj = habitsView(aggregateOnly, { items: {}, total: { minutes: 120 } });
    expect(adj.countedMinutes).toBe(120);
    expect(adj.rows[0].edited).toBe(true);
  });

  it("is hidden when the artifact lists no habits and no minutes", () => {
    const empty: HabitsInfo = { total: 0, estMinutes: 0, items: [] };
    expect(habitsView(empty, none).mode).toBe("hidden");
    expect(habitsView(undefined, none).mode).toBe("hidden");
  });

  it("summarises the habit count and counted time, never completion", () => {
    expect(habitsView(info, none).summary).toBe("3 habits · 45min counted");
    const one: HabitsInfo = { total: 1, estMinutes: 30, items: [{ id: "a", name: "A", minutes: 30 }] };
    expect(habitsView(one, none).summary).toBe("1 habit · 30min counted");
    const big: HabitsInfo = { total: 2, estMinutes: 75, items: [{ id: "a", name: "A", minutes: 75 }, { id: "b", name: "B", minutes: 0 }] };
    expect(habitsView(big, none).summary).toBe("2 habits · 1hr 15min counted");
    expect(habitsView(info, none).summary).not.toMatch(/outstanding|done|left/i);
  });
});

describe("withHabitEntry", () => {
  it("stores only what differs from the default", () => {
    const off = withHabitEntry(none, "a", { on: false }, 25);
    expect(off.items).toEqual({ a: { on: false } });
    const mins = withHabitEntry(off, "a", { minutes: 40 }, 25);
    expect(mins.items).toEqual({ a: { on: false, minutes: 40 } });
  });

  it("removes the entry when the row is back at its default", () => {
    const edited: HabitOverrides = { items: { a: { on: false, minutes: 40 }, b: { minutes: 5 } }, total: null };
    const a1 = withHabitEntry(edited, "a", { on: true }, 25);
    expect(a1.items).toEqual({ a: { minutes: 40 }, b: { minutes: 5 } });
    const a2 = withHabitEntry(a1, "a", { minutes: 25 }, 25);
    expect(a2.items).toEqual({ b: { minutes: 5 } });
  });

  it("keeps stale entries it does not own", () => {
    const base: HabitOverrides = { items: { gone: { on: false } }, total: null };
    expect(withHabitEntry(base, "a", { on: false }, 25).items).toEqual({
      gone: { on: false },
      a: { on: false },
    });
  });

  it("writes and clears the total entry", () => {
    const t = withHabitEntry(none, "total", { minutes: 120 }, 45);
    expect(t.total).toEqual({ minutes: 120 });
    expect(withHabitEntry(t, "total", { minutes: 45 }, 45).total).toBeNull();
  });

  it("never mutates its input", () => {
    withHabitEntry(none, "a", { on: false }, 25);
    expect(none).toEqual({ items: {}, total: null });
  });
});
