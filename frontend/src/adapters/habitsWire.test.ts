import { describe, expect, it } from "vitest";
import { daySetupToWire, projectDaySetup, projectPlanInputs } from "./wire";
import type { DaySetup } from "../model/types";
import planInputs from "./contract-fixtures/plan-inputs.json";

const base: DaySetup = {
  anchor: null,
  eod: null,
  buffering: "standard",
  anchored: {},
  captures: { intention: "", forMeegy: "", stoic: "" },
  confirmed: true,
};

describe("habits items projection", () => {
  it("projects the aggregate and per-habit items", () => {
    const inputs = projectPlanInputs({
      ...(planInputs as object),
      habits: {
        total: 2, done: 0, outstanding: 2, est_minutes: 45,
        items: [{ minutes: 25, id: "a", content: "A" }, { minutes: 20, id: "b", content: "B" }],
      },
    });
    expect(inputs.habits).toEqual({
      total: 2, done: 0, outstanding: 2, estMinutes: 45,
      items: [{ id: "a", name: "A", minutes: 25 }, { id: "b", name: "B", minutes: 20 }],
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
