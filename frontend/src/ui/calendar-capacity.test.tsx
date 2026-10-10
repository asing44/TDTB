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

/** U10: the timeline selects a block to reveal its inline detail row. */
function openBlock(
  r: ReturnType<ReturnType<typeof makeHarness>["ui"]>,
  name: string,
) {
  fireEvent.click(r.getByRole("button", { name: new RegExp(name) }));
}

describe("CalendarImpact", () => {
  it("places every in-frame class in a lane, hides ignored sources, and shows hard walls (FEEDBACK-09)", () => {
    const h = calendarHarness();
    const r = h.ui(<CalendarImpact />);
    const { getByText, queryByText, getByRole, queryByRole, getAllByText, container } = r;
    // Blocks render in their lanes with names visible.
    for (const name of ["Fixed appointment", "Work sync", "Skipped visit", "Declined sync"]) {
      expect(getByText(name)).toBeTruthy();
    }
    // FEEDBACK-09: ignored calendar sources (TickTick-style) are excluded
    // from the timeline — hidden, with a note naming the exclusion.
    expect(queryByText("Focus timer")).toBeNull();
    expect(getByText("1 ignored calendar source excluded")).toBeTruthy();
    // Work-class rows land in the Work lane; fixed rows in Calendar.
    expect(container.querySelector(".timeline__lane--work")?.textContent).toContain("Work sync");
    expect(container.querySelector(".timeline__lane--calendar")?.textContent).toContain("Fixed appointment");

    // Fixed: wall affordance, reason, count, and 12-hour time in the detail row.
    openBlock(r, "Fixed appointment");
    expect(getByText("Fixed")).toBeTruthy();
    expect(getByText("hard block")).toBeTruthy();
    expect(getByText("1 blk counted")).toBeTruthy();
    expect(getByText("10 AM–10:30 AM")).toBeTruthy();

    // Work: inside-budget reason, counted, hard wall.
    openBlock(r, "Work sync");
    expect(getByText("Inside work budget")).toBeTruthy();
    expect(getByText("2 blk counted")).toBeTruthy();
    expect(getAllByText("hard block").length).toBe(1);

    // FEEDBACK-28: a persisted skip stays counted and walled; exclude offered.
    openBlock(r, "Skipped visit");
    expect(getByText("2 blk counted")).toBeTruthy();
    expect(getByRole("button", { name: "Do not count Skipped visit toward today's capacity" })).toBeTruthy();
    expect(queryByRole("button", { name: "Count Skipped visit toward today's capacity" })).toBeNull();

    // Only the explicit current-run skip renders the excluded-today case.
    openBlock(r, "Declined sync");
    expect(getByText("Excluded today")).toBeTruthy();
    expect(getByText("today only · event untouched")).toBeTruthy();
    expect(getByText("0 blk counted")).toBeTruthy();
    expect(getByRole("button", { name: "Count Declined sync toward today's capacity" })).toBeTruthy();
    // Wall-clock readouts stay 12-hour with AM/PM (FEEDBACK-12).
    expect(container.textContent).toMatch(/AM|PM/);
  });

  it("uses the task-style accounting action model before Send (FEEDBACK-09)", () => {
    const h = calendarHarness();
    const save = vi.spyOn(h.controller, "saveAnchoredOverride").mockResolvedValue();
    const r = h.ui(<CalendarImpact />);
    openBlock(r, "Work sync");
    fireEvent.click(
      r.getByRole("button", { name: "Do not count Work sync toward today's capacity" }),
    );
    expect(save).toHaveBeenCalledWith(
      "Work sync",
      expect.objectContaining({ skipToday: true, time: null }),
    );
    // FEEDBACK-28: a persisted skip stays attending — it offers the exclude
    // verb, never the reverse accounting verb.
    openBlock(r, "Skipped visit");
    expect(
      r.getByRole("button", { name: "Do not count Skipped visit toward today's capacity" }),
    ).toBeTruthy();
    // Excluded rows (explicit current-run skip only) offer the reverse verb.
    openBlock(r, "Declined sync");
    fireEvent.click(r.getByRole("button", { name: "Count Declined sync toward today's capacity" }));
    expect(save).toHaveBeenLastCalledWith(
      "Declined sync",
      expect.objectContaining({ skipToday: false }),
    );
  });

  // FEEDBACK-28 (2026-08-17): only an EXPLICIT current-run skip frees a
  // calendar interval from planning walls; a persisted skip stays walled —
  // the timeline mirrors the same gate.
  it("writes persisted skips into walls and excludes only the explicit current-run skip (FEEDBACK-28)", () => {
    const h = calendarHarness();
    const eff = effectiveAnchoredBlocks(h.store.getState());
    const walls = calendarWalls(eff);
    expect(walls).toContainEqual({ start: 12 * 60, end: 13 * 60 }); // persisted Skipped visit
    expect(walls).not.toContainEqual({ start: 13 * 60, end: 13 * 60 + 30 }); // explicit Declined sync
    const r = h.ui(<CalendarImpact />);
    openBlock(r, "Declined sync");
    expect(r.getByText("Excluded today")).toBeTruthy();
    openBlock(r, "Skipped visit");
    expect(r.queryByRole("button", { name: "Count Skipped visit toward today's capacity" })).toBeNull();
  });

  it("adjusts counted duration as a local projection, never the event (FEEDBACK-09)", () => {
    const h = calendarHarness();
    const save = vi.spyOn(h.controller, "saveAnchoredOverride").mockResolvedValue();
    const r = h.ui(<CalendarImpact />);
    // Work sync is a 50-minute event → 2 blocks counted; + steps to 3.
    openBlock(r, "Work sync");
    fireEvent.click(r.getByRole("button", { name: "More counted time for Work sync (today only)" }));
    expect(save).toHaveBeenCalledWith(
      "Work sync",
      expect.objectContaining({ on: true, skipToday: false, time: null, blocks: 3 }),
    );
    // A 30-minute event counts 1 block; minus is disabled at the floor.
    openBlock(r, "Fixed appointment");
    expect(
      (
        r.getByRole("button", {
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
    openBlock(r, "Work sync");
    expect(r.queryByText("3 blk counted")).toBeTruthy();
    expect(r.queryByText("1hr 30min")).toBeTruthy();
    expect(r.queryByText("9:30 AM–10:20 AM")).toBeTruthy(); // event time unchanged
    expect(r.queryByText("projection")).toBeTruthy();
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
    const r = h.ui(<CalendarImpact />);
    openBlock(r, "Steelers Game");
    expect(r.getByText("quarantined")).toBeTruthy(); // class badge, not Fixed
    expect(r.getByText("Not counted")).toBeTruthy(); // reason — zero capacity
    expect(r.getByText("0 blk counted")).toBeTruthy();
    expect(r.getByText("2hr")).toBeTruthy();
    // The configured fixed row beside it is still accounted exactly once:
    openBlock(r, "Cooking");
    expect(r.getByText("Fixed")).toBeTruthy();
    expect(r.getByText("1 blk counted")).toBeTruthy();
    // FEEDBACK-09: only the fixed row carries the hard-wall affordance.
    expect(r.getByText("hard block")).toBeTruthy();
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
