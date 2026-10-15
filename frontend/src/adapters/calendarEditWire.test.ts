import { describe, expect, it } from "vitest";
import { daySetupToWire, projectDaySetup } from "./wire";
import type { DaySetup } from "../model/types";

const base: DaySetup = {
  anchor: null,
  eod: null,
  buffering: "standard",
  anchored: {},
  captures: { intention: "", forMeegy: "", stoic: "" },
  confirmed: true,
};

describe("calendar start override wire", () => {
  it("serializes start and calendar_start_at_edit when set", () => {
    const wire = daySetupToWire({
      ...base,
      anchored: {
        Dentist: {
          on: true,
          skipToday: false,
          time: null,
          start: "20:00",
          calendarStartAtEdit: "19:00",
        },
      },
    });
    expect(wire.anchored).toEqual([
      {
        id: "Dentist",
        on: true,
        skip_today: false,
        time: null,
        start: "20:00",
        calendar_start_at_edit: "19:00",
      },
    ]);
  });

  it("omits both fields when absent or null (clearing)", () => {
    const wire = daySetupToWire({
      ...base,
      anchored: {
        A: { on: true, skipToday: false, time: null },
        B: { on: true, skipToday: false, time: null, start: null, calendarStartAtEdit: null },
      },
    });
    for (const row of wire.anchored as Array<Record<string, unknown>>) {
      expect("start" in row).toBe(false);
      expect("calendar_start_at_edit" in row).toBe(false);
    }
  });

  it("round-trips through projectDaySetup", () => {
    const wire = {
      anchored: [
        {
          id: "Dentist",
          on: true,
          skip_today: false,
          time: null,
          start: "20:00",
          calendar_start_at_edit: "19:00",
        },
        { id: "Plain", on: true, skip_today: true, time: null },
      ],
    };
    const projected = projectDaySetup(wire, true);
    expect(projected.anchored.Dentist.start).toBe("20:00");
    expect(projected.anchored.Dentist.calendarStartAtEdit).toBe("19:00");
    expect(projected.anchored.Plain.start ?? null).toBeNull();
    expect(daySetupToWire(projected).anchored).toEqual([
      { id: "Dentist", on: true, skip_today: false, time: null, start: "20:00", calendar_start_at_edit: "19:00" },
      { id: "Plain", on: true, skip_today: true, time: null },
    ]);
  });

  it("drops an unparseable start instead of projecting garbage", () => {
    const projected = projectDaySetup(
      { anchored: [{ id: "X", on: true, skip_today: false, time: null, start: "nonsense" }] },
      true,
    );
    expect(projected.anchored.X.start ?? null).toBeNull();
  });
});
