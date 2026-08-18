import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent } from "@testing-library/preact";

afterEach(cleanup);

import { CalendarImpact } from "./CalendarImpact";
import { AllocationPie } from "./AllocationPie";
import { Rail } from "./Rail";
import { makeHarness } from "./test-harness";
import { effectiveAnchoredBlocks } from "../store/store";
import { calendarWalls } from "../model/overflow";

function calendarHarness() {
  const h = makeHarness("ready");
  const inputs = structuredClone(h.store.getState().inputs!);
  inputs.anchored = [
    {
      id: "Fixed appointment", name: "Fixed appointment", kind: "calendar",
      start: "10:00", end: "10:30", durationMin: 30, overlapAllowed: false,
      on: true, skipToday: false, calendarId: "fixed", calendarTitle: "Personal",
      capacityClass: "fixed",
    },
    {
      id: "Work sync", name: "Work sync", kind: "calendar",
      start: "09:30", end: "10:20", durationMin: 50, overlapAllowed: false,
      on: true, skipToday: false, calendarId: "work", calendarTitle: "Trinoor",
      capacityClass: "work",
    },
    {
      id: "Focus timer", name: "Focus timer", kind: "calendar",
      start: "11:00", end: "11:30", durationMin: 30, overlapAllowed: false,
      on: true, skipToday: false, calendarId: "focus", calendarTitle: "Session: focus",
      capacityClass: "ignored",
    },
    {
      // FEEDBACK-28: a PERSISTED skip (server daySetup / merged raw row) stays
      // visible, counted, and walled until the user re-expresses it this run.
      id: "Skipped visit", name: "Skipped visit", kind: "calendar",
      start: "12:00", end: "13:00", durationMin: 60, overlapAllowed: false,
      on: false, skipToday: true, calendarId: "skip", calendarTitle: "Personal",
      capacityClass: "fixed",
    },
    {
      // FEEDBACK-28: only an EXPLICIT current-run skip is the excluded-today /
      // non-walling case (marker dispatched below after inputs load).
      id: "Declined sync", name: "Declined sync", kind: "calendar",
      start: "13:00", end: "13:30", durationMin: 30, overlapAllowed: false,
      on: true, skipToday: true, calendarId: "decline", calendarTitle: "Personal",
      capacityClass: "fixed",
    },
  ];
  inputs.capacity.workBusy = 2;
  inputs.capacity.workOverflow = 0;
  h.store.dispatch({ type: "INPUTS_LOADED", inputs, ledger: h.store.getState().ledger! });
  // Explicit current-run intent — the marker CalendarImpact's per-row toggle
  // records through saveAnchoredOverride (CALENDAR_SKIP_EXPLICIT).
  h.store.dispatch({ type: "CALENDAR_SKIP_EXPLICIT", id: "Declined sync", skipToday: true });
  return h;
}

describe("CalendarImpact", () => {
  it("explains every in-frame class, hides ignored sources, and shows hard walls (FEEDBACK-09)", () => {
    const h = calendarHarness();
    const { getByText, queryByText, queryByRole, getAllByText, getByRole, container } =
      h.ui(<CalendarImpact />);
    expect(getByText("Fixed appointment")).toBeTruthy();
    expect(getByText("Work sync")).toBeTruthy();
    // FEEDBACK-28: the persisted skip stays visible (and counted/walled —
    // asserted below); the explicit current-run skip is visible too.
    expect(getByText("Skipped visit")).toBeTruthy();
    expect(getByText("Declined sync")).toBeTruthy();
    // FEEDBACK-09: ignored calendar sources (TickTick-style) are excluded
    // from Calendar impact — hidden, with a note naming the exclusion.
    expect(queryByText("Focus timer")).toBeNull();
    expect(getByText("1 ignored calendar source excluded")).toBeTruthy();
    // Hard-wall affordance renders on fixed/work rows only — including the
    // persisted skip; only the explicit skip frees its interval from planning
    // walls (asserted via calendarWalls in the FEEDBACK-28 test below).
    expect(getAllByText("hard block").length).toBe(4);
    expect(getByText("Inside work budget")).toBeTruthy();
    // FEEDBACK-28: a persisted skip is counted and walled — reason stays
    // Fixed, blocks counted, exclude verb offered, no exclusion copy.
    expect(getAllByText("Fixed").length).toBe(2); // Fixed appointment + Skipped visit
    expect(getAllByText("2 blk counted").length).toBe(2); // Work sync + Skipped visit
    expect(getByRole("button", { name: "Do not count Skipped visit toward today's capacity" })).toBeTruthy();
    expect(queryByRole("button", { name: "Count Skipped visit toward today's capacity" })).toBeNull();
    // Only the explicit current-run skip renders the excluded-today case.
    expect(getByText("Excluded today")).toBeTruthy();
    expect(getByText("today only · event untouched")).toBeTruthy();
    expect(getByText("0 blk counted")).toBeTruthy();
    expect(getByRole("button", { name: "Count Declined sync toward today's capacity" })).toBeTruthy();
    // Accounting language, never attendance/mutation copy.
    expect(getByText(/exclusive busy time/i)).toBeTruthy();
    // FEEDBACK-12: wall-clock readouts are 12-hour with AM/PM (user
    // preference); FEEDBACK-09/11 24-hour presentation is superseded.
    expect(getByText("9:30 AM–10:20 AM")).toBeTruthy();
    expect(getByText("10 AM–10:30 AM")).toBeTruthy();
    expect(container.textContent).toMatch(/AM|PM/);
  });

  it("uses the task-style accounting action model before Send (FEEDBACK-09)", () => {
    const h = calendarHarness();
    const save = vi.spyOn(h.controller, "saveAnchoredOverride").mockResolvedValue();
    const { getByRole } = h.ui(<CalendarImpact />);
    fireEvent.click(
      getByRole("button", { name: "Do not count Work sync toward today's capacity" }),
    );
    expect(save).toHaveBeenCalledWith(
      "Work sync",
      expect.objectContaining({ skipToday: true, time: null }),
    );
    // FEEDBACK-28: a persisted skip stays attending — it offers the exclude
    // verb, never the reverse accounting verb.
    expect(
      getByRole("button", { name: "Do not count Skipped visit toward today's capacity" }),
    ).toBeTruthy();
    // Excluded rows (explicit current-run skip only) offer the reverse verb.
    fireEvent.click(getByRole("button", { name: "Count Declined sync toward today's capacity" }));
    expect(save).toHaveBeenLastCalledWith(
      "Declined sync",
      expect.objectContaining({ skipToday: false }),
    );
  });

  // FEEDBACK-28 (2026-08-17): only an EXPLICIT current-run skip frees a
  // calendar interval from planning walls; a persisted skip stays walled —
  // the impact list mirrors the same gate.
  it("writes persisted skips into walls and excludes only the explicit current-run skip (FEEDBACK-28)", () => {
    const h = calendarHarness();
    const eff = effectiveAnchoredBlocks(h.store.getState());
    const walls = calendarWalls(eff);
    expect(walls).toContainEqual({ start: 12 * 60, end: 13 * 60 }); // persisted Skipped visit
    expect(walls).not.toContainEqual({ start: 13 * 60, end: 13 * 60 + 30 }); // explicit Declined sync
    const { getByText, queryByRole } = h.ui(<CalendarImpact />);
    expect(getByText("Excluded today")).toBeTruthy(); // Declined sync reason
    expect(queryByRole("button", { name: "Count Skipped visit toward today's capacity" })).toBeNull();
  });

  it("adjusts counted duration as a local projection, never the event (FEEDBACK-09)", () => {
    const h = calendarHarness();
    const save = vi.spyOn(h.controller, "saveAnchoredOverride").mockResolvedValue();
    const { getByRole, queryByText } = h.ui(<CalendarImpact />);
    // Work sync is a 50-minute event → 2 blocks counted; + steps to 3.
    fireEvent.click(getByRole("button", { name: "More counted time for Work sync (today only)" }));
    expect(save).toHaveBeenCalledWith(
      "Work sync",
      expect.objectContaining({ on: true, skipToday: false, time: null, blocks: 3 }),
    );
    // A 30-minute event counts 1 block; minus is disabled at the floor.
    expect(
      (
        getByRole("button", {
          name: "Less counted time for Fixed appointment (today only)",
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(true);
    // A seeded projection renders adjusted accounting, a projection marker,
    // and keeps the event's wall-clock window untouched.
    act(() => {
      h.store.dispatch({
        type: "SETUP_SAVED",
        daySetup: {
          ...h.store.getState().daySetup,
          confirmed: true,
          anchored: {
            ...h.store.getState().daySetup.anchored,
            "Work sync": { on: true, skipToday: false, time: null, blocks: 3 },
          },
        },
      });
    });
    expect(queryByText("3 blk counted")).toBeTruthy();
    expect(queryByText("1hr 30min")).toBeTruthy();
    expect(queryByText("9:30 AM–10:20 AM")).toBeTruthy(); // event time unchanged
    expect(queryByText("projection")).toBeTruthy();
  });

  // FEEDBACK-04 (2026-08-14): a quarantined row is a known-but-unreviewed
  // event — it must render as excluded (quarantined badge, Not counted, zero
  // blocks) and never as Fixed like the configured fixed Cooking row beside it.
  it("labels quarantined rows excluded — never Fixed (FEEDBACK-04)", () => {
    const h = makeHarness("ready");
    const inputs = structuredClone(h.store.getState().inputs!);
    inputs.anchored = [
      {
        id: "Cooking", name: "Cooking", kind: "calendar",
        start: "20:30", end: "21:00", durationMin: 30, overlapAllowed: false,
        on: true, skipToday: false, calendarId: "cooking", calendarTitle: "Personal",
        capacityClass: "fixed",
      },
      {
        id: "Steelers Game", name: "Steelers Game", kind: "calendar",
        start: "20:00", end: "22:00", durationMin: 120, overlapAllowed: false,
        on: true, skipToday: false, calendarId: "sports", calendarTitle: "Sports",
        capacityClass: "quarantined",
      },
    ];
    h.store.dispatch({ type: "INPUTS_LOADED", inputs, ledger: h.store.getState().ledger! });
    const { getByText, getAllByText } = h.ui(<CalendarImpact />);
    expect(getByText("Steelers Game")).toBeTruthy();
    expect(getByText("quarantined")).toBeTruthy(); // class badge, not Fixed
    expect(getByText("Not counted")).toBeTruthy(); // reason — zero capacity
    expect(getByText("0 blk counted")).toBeTruthy();
    // The configured fixed row beside it is still accounted exactly once:
    expect(getByText("Fixed")).toBeTruthy();
    expect(getByText("1 blk counted")).toBeTruthy();
    expect(getAllByText("Fixed").length).toBe(1);
    // FEEDBACK-09: only the fixed row carries the hard-wall affordance.
    expect(getAllByText("hard block").length).toBe(1);
  });
});

describe("capacity language", () => {
  it("names the five rail quantities and calls the ledger Calls", () => {
    const h = calendarHarness();
    const { getByText, queryByText } = h.ui(<Rail />);
    for (const label of [
      "Day capacity",
      "Reserved before tasks",
      "Task room",
      "Chosen tasks",
      "Over by",
    ]) expect(getByText(label)).toBeTruthy();
    expect(getByText(/Calls \d+\/\d+/)).toBeTruthy();
    expect(queryByText(/Budget \d+\/\d+/)).toBeNull();
  });

  it("compares planned blocks with day capacity in the donut center (IMP-07 hierarchy)", () => {
    const h = calendarHarness();
    const { container, queryByText } = h.ui(<AllocationPie />);
    const readout = container.querySelector(".pie__readout");
    // Compact hierarchy: planned blocks / capacity context / over or
    // remaining state — the old "planned / day" treatment is gone.
    expect(readout?.textContent).toMatch(/blk/);
    expect(readout?.textContent).toMatch(/capacity/);
    expect(readout?.textContent).toMatch(/over|remaining/i);
    expect(queryByText("planned / day")).toBeNull();
  });
});
