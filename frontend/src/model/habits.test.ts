import { describe, expect, it } from "vitest";
import {
  HABIT_MINUTES_MAX,
  habitsView,
  parseHabitMinutes,
  withHabitEntry,
} from "./habits";
import type { HabitOverrides, HabitsInfo } from "./types";

const info: HabitsInfo = {
  total: 3,
  done: 1,
  outstanding: 2,
  estMinutes: 45,
  items: [
    { id: "a", name: "Stretch", minutes: 25 },
    { id: "b", name: "Read", minutes: 20 },
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
  it("lists per-habit rows and no total row when items exist", () => {
    const v = habitsView(info, none);
    expect(v.mode).toBe("items");
    expect(v.rows.map((r) => [r.key, r.label, r.minutes, r.on, r.edited])).toEqual([
      ["a", "Stretch", 25, true, false],
      ["b", "Read", 20, true, false],
    ]);
    expect(v.countedMinutes).toBe(45);
    expect(v.outstanding).toBe(2);
  });

  it("applies overrides to the counted minutes and marks edited rows", () => {
    const v = habitsView(info, {
      items: { a: { on: false }, b: { on: true, minutes: 60 } },
      total: null,
    });
    expect(v.rows[0]).toMatchObject({ key: "a", on: false, edited: true, minutes: 25 });
    expect(v.rows[1]).toMatchObject({ key: "b", on: true, edited: true, minutes: 60 });
    expect(v.countedMinutes).toBe(60);
  });

  it("ignores stale overrides for habits that are no longer outstanding", () => {
    const v = habitsView(info, { items: { gone: { on: false } }, total: { on: false } });
    expect(v.rows).toHaveLength(2);
    expect(v.countedMinutes).toBe(45);
  });

  it("falls back to ONE total row when the artifact has no items", () => {
    const v = habitsView(aggregateOnly, none);
    expect(v.mode).toBe("total");
    expect(v.rows).toHaveLength(1);
    expect(v.rows[0]).toMatchObject({ key: "total", label: "Habits (total)", minutes: 45, on: true });
    const off = habitsView(aggregateOnly, { items: {}, total: { on: false } });
    expect(off.countedMinutes).toBe(0);
    const adj = habitsView(aggregateOnly, { items: {}, total: { minutes: 120 } });
    expect(adj.countedMinutes).toBe(120);
    expect(adj.rows[0].edited).toBe(true);
  });

  it("is hidden with zero outstanding habits and no total", () => {
    const empty: HabitsInfo = { total: 2, done: 2, outstanding: 0, estMinutes: 0, items: [] };
    expect(habitsView(empty, none).mode).toBe("hidden");
    expect(habitsView(undefined, none).mode).toBe("hidden");
  });

  it("summarises outstanding and counted time", () => {
    expect(habitsView(info, none).summary).toBe("2 outstanding · 45min counted");
    const big: HabitsInfo = { ...info, outstanding: 3, items: [{ id: "a", name: "A", minutes: 75 }] };
    expect(habitsView(big, none).summary).toBe("3 outstanding · 1hr 15min counted");
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
