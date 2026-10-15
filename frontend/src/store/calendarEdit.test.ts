/* Calendar events on the Set up day strip are editable LOCALLY ONLY: a start
   override (plus the calendar's own start at edit time) persists per date
   through the existing day-setup save path and never reaches a calendar
   write. These pin the model, store and controller side. */

import { afterEach, describe, expect, it, vi } from "vitest";
import { makeHarness } from "../ui/test-harness";
import { FixtureAdapter } from "../adapters/fixture";
import { alerts, calendarEditAlerts, effectiveAnchoredBlocks } from "./store";
import { calendarWalls } from "../model/overflow";

afterEach(() => vi.restoreAllMocks());

function harness() {
  const h = makeHarness("ready");
  const inputs = structuredClone(h.store.getState().inputs!);
  inputs.anchored = [
    {
      id: "Dentist", name: "Dentist", kind: "calendar", start: "19:00", end: "19:30",
      durationMin: 30, overlapAllowed: false, on: true, skipToday: false,
      calendarId: "p", calendarTitle: "Personal", capacityClass: "fixed",
    },
    {
      id: "Standup", name: "Standup", kind: "calendar", start: "20:15", end: "20:45",
      durationMin: 30, overlapAllowed: false, on: true, skipToday: false,
      calendarId: "w", calendarTitle: "Trinoor", capacityClass: "work",
    },
  ];
  inputs.daySemantics = {
    ...inputs.daySemantics,
    mintSessions: [
      { id: "mint:eve", name: "Mint 1", slot: "Day", start: "21:00", end: "21:30" },
    ],
  };
  h.store.dispatch({ type: "INPUTS_LOADED", inputs, ledger: h.store.getState().ledger! });
  return h;
}

const dentist = (h: ReturnType<typeof harness>) =>
  effectiveAnchoredBlocks(h.store.getState()).find((a) => a.id === "Dentist")!;

describe("effectiveAnchoredBlocks — calendar start override", () => {
  it("leaves an unedited calendar row untouched (same object)", () => {
    const h = harness();
    const raw = h.store.getState().inputs!.anchored[0];
    expect(dentist(h)).toBe(raw);
  });

  it("moves the start and the end follows the duration", () => {
    const h = harness();
    h.store.dispatch({
      type: "SETUP_SAVED",
      daySetup: {
        ...h.store.getState().daySetup,
        anchored: {
          Dentist: { on: true, skipToday: false, time: null, start: "20:00", calendarStartAtEdit: "19:00" },
        },
      },
    });
    expect(dentist(h)).toMatchObject({ start: "20:00", end: "20:30", durationMin: 30 });
  });

  it("applies duration (blocks) together with the start", () => {
    const h = harness();
    h.store.dispatch({
      type: "SETUP_SAVED",
      daySetup: {
        ...h.store.getState().daySetup,
        anchored: {
          Dentist: { on: true, skipToday: false, time: null, start: "20:00", blocks: 3, calendarStartAtEdit: "19:00" },
        },
      },
    });
    expect(dentist(h)).toMatchObject({ start: "20:00", end: "21:30", durationMin: 90 });
  });

  it("walls follow the edited interval and free the original one", () => {
    const h = harness();
    h.store.dispatch({
      type: "SETUP_SAVED",
      daySetup: {
        ...h.store.getState().daySetup,
        anchored: {
          Dentist: { on: true, skipToday: false, time: null, start: "20:00", calendarStartAtEdit: "19:00" },
        },
      },
    });
    const walls = calendarWalls(effectiveAnchoredBlocks(h.store.getState()));
    expect(walls).toContainEqual({ start: 20 * 60, end: 20 * 60 + 30 });
    expect(walls).not.toContainEqual({ start: 19 * 60, end: 19 * 60 + 30 });
  });
});

describe("overlap warning reaches the footer alerts", () => {
  it("counts an edited event overlapping another event, and nothing without an edit", () => {
    const h = harness();
    expect(calendarEditAlerts(h.store.getState())).toEqual([]);
    h.store.dispatch({
      type: "SETUP_SAVED",
      daySetup: {
        ...h.store.getState().daySetup,
        anchored: {
          Dentist: { on: true, skipToday: false, time: null, start: "20:30", calendarStartAtEdit: "19:00" },
        },
      },
    });
    const lines = calendarEditAlerts(h.store.getState());
    expect(lines).toEqual([
      "Edited calendar event 'Dentist' (20:30-21:00) overlaps 'Standup' (20:15-20:45)",
    ]);
    expect(alerts(h.store.getState())).toContainEqual({ level: "warning", text: lines[0] });
  });

  it("counts a selected Mint session overlap", () => {
    const h = harness();
    h.store.dispatch({
      type: "SETUP_SAVED",
      daySetup: {
        ...h.store.getState().daySetup,
        schedulable: { minting: { on: true, n: 1, sessions: ["mint:eve"] } },
        anchored: {
          Dentist: { on: true, skipToday: false, time: null, start: "21:00", calendarStartAtEdit: "19:00" },
        },
      },
    });
    expect(calendarEditAlerts(h.store.getState())).toEqual([
      "Edited calendar event 'Dentist' (21:00-21:30) overlaps 'Mint 1' (21:00-21:30)",
    ]);
  });
});

describe("controller.saveCalendarEdit", () => {
  it("persists start + start-at-edit through saveDaySetup and calls no calendar write", async () => {
    const h = harness();
    const save = vi.spyOn(FixtureAdapter.prototype, "saveDaySetup");
    const writes = [
      vi.spyOn(FixtureAdapter.prototype, "liveCommit"),
      vi.spyOn(FixtureAdapter.prototype, "shadowCommit"),
      vi.spyOn(FixtureAdapter.prototype, "saveAnchoredStart"),
      vi.spyOn(FixtureAdapter.prototype, "autoSequence"),
    ];
    await h.controller.saveCalendarEdit("Dentist", { start: "20:00" });
    expect(save).toHaveBeenCalledTimes(1);
    const sent = save.mock.calls[0][0].anchored.Dentist;
    expect(sent).toEqual({
      on: true, skipToday: false, time: null, start: "20:00", calendarStartAtEdit: "19:00",
    });
    expect(h.store.getState().daySetup.anchored.Dentist.start).toBe("20:00");
    for (const w of writes) expect(w).not.toHaveBeenCalled();
  });

  it("keeps blocks and does not record an explicit current-run skip", async () => {
    const h = harness();
    await h.controller.saveCalendarEdit("Dentist", { blocks: 2 });
    await h.controller.saveCalendarEdit("Dentist", { start: "20:00" });
    const o = h.store.getState().daySetup.anchored.Dentist;
    expect(o).toMatchObject({ blocks: 2, start: "20:00", skipToday: false });
    expect(h.store.getState().currentRunCalendarSkips).toEqual([]);
  });

  it("re-records the live start when the operator edits again", async () => {
    const h = harness();
    await h.controller.saveCalendarEdit("Dentist", { start: "20:00" });
    const inputs = structuredClone(h.store.getState().inputs!);
    inputs.anchored[0] = { ...inputs.anchored[0], start: "19:30", end: "20:00" };
    h.store.dispatch({ type: "INPUTS_LOADED", inputs, ledger: h.store.getState().ledger! });
    await h.controller.saveCalendarEdit("Dentist", { start: "20:15" });
    expect(h.store.getState().daySetup.anchored.Dentist).toMatchObject({
      start: "20:15", calendarStartAtEdit: "19:30",
    });
  });

  it("clearing the start (Use calendar time) drops start and start-at-edit but keeps the rest", async () => {
    const h = harness();
    await h.controller.saveCalendarEdit("Dentist", { blocks: 2 });
    await h.controller.saveCalendarEdit("Dentist", { start: "20:00" });
    await h.controller.saveCalendarEdit("Dentist", { start: null });
    const o = h.store.getState().daySetup.anchored.Dentist;
    expect(o.start ?? null).toBeNull();
    expect(o.calendarStartAtEdit ?? null).toBeNull();
    expect(o.blocks).toBe(2);
  });

  it("Reset to calendar clears start, start-at-edit and duration", async () => {
    const h = harness();
    await h.controller.saveCalendarEdit("Dentist", { blocks: 2 });
    await h.controller.saveCalendarEdit("Dentist", { start: "20:00" });
    await h.controller.resetCalendarEdit("Dentist");
    const o = h.store.getState().daySetup.anchored.Dentist;
    expect(o.start ?? null).toBeNull();
    expect(o.calendarStartAtEdit ?? null).toBeNull();
    expect(o.blocks ?? null).toBeNull();
    expect(dentist(h)).toMatchObject({ start: "19:00", end: "19:30", durationMin: 30 });
  });

  it("refuses an invalid start without saving", async () => {
    const h = harness();
    const save = vi.spyOn(FixtureAdapter.prototype, "saveDaySetup");
    await h.controller.saveCalendarEdit("Dentist", { start: "" });
    await h.controller.saveCalendarEdit("Dentist", { start: "9:" });
    expect(save).not.toHaveBeenCalled();
  });
});
