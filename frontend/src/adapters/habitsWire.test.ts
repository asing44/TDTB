import { describe, expect, it } from "vitest";
import { habitsView } from "../model/habits";
import { daySetupToWire, projectDaySetup, projectPlanInputs } from "./wire";
import type { DaySetup } from "../model/types";
import planInputs from "./contract-fixtures/plan-inputs.json";

/* Habits are an ACTIVE list (Grit owns completion): the artifact lists every
   habit in the Todoist Habits project and carries no completion accounting the
   frontend reads. Legacy blocks that still carry `done`/`outstanding` project
   fine and change nothing. */

const base: DaySetup = {
  anchor: null,
  eod: null,
  buffering: "standard",
  anchored: {},
  captures: { intention: "", forMeegy: "", stoic: "" },
  confirmed: true,
};

const LEGACY_ITEMS = [
  { minutes: 25, id: "a", content: "A" },
  { minutes: 20, id: "b", content: "B" },
  { minutes: 10, id: "c", content: "C" },
];

describe("habits items projection", () => {
  it("projects every habit in the block and ignores completion", () => {
    const inputs = projectPlanInputs({
      ...(planInputs as object),
      habits: {
        total: 3, done: 0, outstanding: 3, est_minutes: 55,
        items: LEGACY_ITEMS,
      },
    });
    expect(inputs.habits).toEqual({
      total: 3, estMinutes: 55,
      items: [{ id: "a", name: "A", minutes: 25 }, { id: "b", name: "B", minutes: 20 }, { id: "c", name: "C", minutes: 10 }],
    });
    expect(habitsView(inputs.habits, undefined).rows.map((r) => r.key)).toEqual(["a", "b", "c"]);
  });

  it("keeps every item when a legacy block still reports done and outstanding", () => {
    const inputs = projectPlanInputs({
      ...(planInputs as object),
      habits: { total: 3, done: 2, outstanding: 1, est_minutes: 55, items: LEGACY_ITEMS },
    });
    expect(inputs.habits?.items).toHaveLength(3);
    expect(inputs.habits?.done).toBeUndefined();
    expect(inputs.habits?.outstanding).toBeUndefined();
    const view = habitsView(inputs.habits, undefined);
    expect(view.habitCount).toBe(3);
    expect(view.countedMinutes).toBe(55);
    expect(view.summary).toBe("3 habits · 55min counted");
  });

  it("projects a block that omits done and outstanding entirely", () => {
    const inputs = projectPlanInputs({
      ...(planInputs as object),
      habits: { total: 1, est_minutes: 10, items: [{ id: "ok", content: "Ok", minutes: 10 }] },
    });
    expect(inputs.habits).toEqual({
      total: 1, estMinutes: 10,
      items: [{ id: "ok", name: "Ok", minutes: 10 }],
    });
  });

  it("projects an aggregate-only block with no items", () => {
    const inputs = projectPlanInputs({
      ...(planInputs as object),
      habits: { total: 3, done: 1, outstanding: 2, est_minutes: 45 },
    });
    expect(inputs.habits?.items).toEqual([]);
    expect(inputs.habits?.estMinutes).toBe(45);
  });

  it("drops malformed item rows instead of failing the plan load", () => {
    const inputs = projectPlanInputs({
      ...(planInputs as object),
      habits: {
        total: 1, done: 0, outstanding: 1, est_minutes: 10,
        items: [null, { id: "x" }, { id: "ok", content: "Ok", minutes: 10 }],
      },
    });
    expect(inputs.habits?.items).toEqual([{ id: "ok", name: "Ok", minutes: 10 }]);
  });

  it("writes a note about the active list, never completion", () => {
    const three = projectPlanInputs({
      ...(planInputs as object),
      habits: { total: 3, done: 2, outstanding: 1, est_minutes: 55, items: LEGACY_ITEMS },
    });
    expect(three.habitsNote).toBe("3 habits · ~55min");
    const one = projectPlanInputs({
      ...(planInputs as object),
      habits: { total: 1, est_minutes: 10 },
    });
    expect(one.habitsNote).toBe("1 habit · ~10min");
    const none = projectPlanInputs({ ...(planInputs as object), habits: { total: 0 } });
    expect(none.habitsNote).toBeNull();
    expect(three.habitsNote).not.toMatch(/outstanding|done/i);
  });
});

describe("habits override wire", () => {
  const overrides = {
    items: { a: { on: false }, b: { on: true, minutes: 60 } },
    total: { on: true, minutes: 120 },
  };

  it("serializes the exact override shape", () => {
    expect(daySetupToWire({ ...base, habits: overrides }).habits).toEqual(overrides);
  });

  it("omits habits when the model carries none (server preserves)", () => {
    expect("habits" in daySetupToWire(base)).toBe(false);
  });

  it("sends an empty full replace to clear every override", () => {
    expect(daySetupToWire({ ...base, habits: { items: {}, total: null } }).habits).toEqual({
      items: {},
      total: null,
    });
  });

  it("round-trips projection → wire, stale entries included", () => {
    const projected = projectDaySetup({ habits: { ...overrides, items: { ...overrides.items, gone: { on: false } } } }, true);
    expect(projected.habits).toEqual({
      items: { a: { on: false }, b: { on: true, minutes: 60 }, gone: { on: false } },
      total: { on: true, minutes: 120 },
    });
    expect(daySetupToWire(projected).habits).toEqual(projected.habits);
  });

  it("leaves habits absent when the wire has none or garbage", () => {
    expect(projectDaySetup({}, true).habits).toBeUndefined();
    expect(projectDaySetup({ habits: "x" }, true).habits).toBeUndefined();
    expect(projectDaySetup({ habits: null }, true).habits).toBeUndefined();
  });

  it("drops invalid entry fields on read", () => {
    const projected = projectDaySetup(
      { habits: { items: { a: { on: "yes", minutes: 9999 }, b: { minutes: 15 } }, total: 5 } },
      true,
    );
    expect(projected.habits).toEqual({ items: { a: {}, b: { minutes: 15 } }, total: null });
  });
});
