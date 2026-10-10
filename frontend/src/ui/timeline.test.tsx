/* U10 — "Already placed" lane timeline (docs/plan/2026-10-09-cached-refresh-
   technical-design.md U10; reference B-plan-tasks-1500.png).

   Timeline-specific coverage that the re-pointed list tests do not pin:
   proportional lane position, clipped/omitted-with-count blocks, overlap
   stacking, keyboard select + Escape focus return, Exclude/duration ± effect
   on the capacity card, and the empty-lane message. Classification, capacity,
   projection, and writer semantics stay pinned by calendar-capacity.test and
   feedback17.test. */

import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, waitFor } from "@testing-library/preact";

afterEach(cleanup);

import { CalendarImpact } from "./CalendarImpact";
import { Queue } from "./Queue";
import { makeHarness } from "./test-harness";
import { toMinutes } from "../model/time";
import type { AnchoredBlock } from "../model/types";

/** Day frame from the fixture: 07:30 → 23:00. */
const FRAME_START = toMinutes("07:30");
const FRAME_END = toMinutes("23:00");
const FRAME_LEN = FRAME_END - FRAME_START;

function calendarRow(over: Partial<AnchoredBlock> = {}): AnchoredBlock {
  return {
    id: "Row", name: "Row", kind: "calendar",
    start: "10:00", end: "10:30", durationMin: 30, overlapAllowed: false,
    on: true, skipToday: false, calendarId: "cal", calendarTitle: "Personal",
    capacityClass: "fixed",
    ...over,
  };
}

/** Replace the fixture calendar rows with the given set (anchors kept). */
function harnessWithCalendar(rows: AnchoredBlock[]) {
  const h = makeHarness("ready");
  const inputs = structuredClone(h.store.getState().inputs!);
  inputs.anchored = rows;
  h.store.dispatch({ type: "INPUTS_LOADED", inputs, ledger: h.store.getState().ledger! });
  return h;
}

function blockOf(container: Element, id: string): HTMLElement {
  const el = container.querySelector<HTMLElement>(`[data-block="${id}"]`);
  if (!el) throw new Error(`no block for ${id}`);
  return el;
}

describe("U10 timeline — placement", () => {
  it("places each block at its proportional start and width inside the lane", () => {
    const h = harnessWithCalendar([
      calendarRow({ id: "Early", name: "Early", start: "09:00", end: "09:30", durationMin: 30 }),
      calendarRow({ id: "Late", name: "Late", start: "14:45", end: "15:30", durationMin: 45 }),
    ]);
    const r = h.ui(<CalendarImpact />);
    const early = blockOf(r.container, "Early");
    const late = blockOf(r.container, "Late");

    const earlyLeft = ((toMinutes("09:00") - FRAME_START) / FRAME_LEN) * 100;
    const earlyWidth = (30 / FRAME_LEN) * 100;
    const lateLeft = ((toMinutes("14:45") - FRAME_START) / FRAME_LEN) * 100;
    const lateWidth = (45 / FRAME_LEN) * 100;

    expect(parseFloat(early.style.left)).toBeCloseTo(earlyLeft, 3);
    expect(parseFloat(early.style.width)).toBeCloseTo(earlyWidth, 3);
    expect(parseFloat(late.style.left)).toBeCloseTo(lateLeft, 3);
    expect(parseFloat(late.style.width)).toBeCloseTo(lateWidth, 3);
    // A later start sits further right; a longer block is wider.
    expect(parseFloat(late.style.left)).toBeGreaterThan(parseFloat(early.style.left));
    expect(parseFloat(late.style.width)).toBeGreaterThan(parseFloat(early.style.width));
  });

  it("clips a partly-outside block to the frame edge and counts wholly-outside blocks", () => {
    const h = harnessWithCalendar([
      calendarRow({ id: "Crossover", name: "Crossover", start: "07:00", end: "08:00", durationMin: 60 }),
      calendarRow({ id: "Before", name: "Before", start: "06:00", end: "06:30", durationMin: 30 }),
      calendarRow({ id: "After", name: "After", start: "23:30", end: "23:45", durationMin: 15 }),
    ]);
    const r = h.ui(<CalendarImpact />);
    const crossover = blockOf(r.container, "Crossover");
    // Clipped to the frame start: left edge at 0, width only the in-frame part.
    expect(parseFloat(crossover.style.left)).toBeCloseTo(0, 3);
    expect(parseFloat(crossover.style.width)).toBeCloseTo((30 / FRAME_LEN) * 100, 3);
    // Wholly-outside rows are omitted, and the count names them.
    expect(r.container.querySelector('[data-block="Before"]')).toBeNull();
    expect(r.container.querySelector('[data-block="After"]')).toBeNull();
    expect(r.getByText("2 blocks outside the day frame")).toBeTruthy();
  });

  it("stacks overlapping blocks in a lane so no label is covered", () => {
    const h = makeHarness("ready");
    const r = h.ui(<CalendarImpact />);
    // Fixture anchors: Morning Routine 07:45–09:05 overlaps Foods Breakfast
    // 08:30–13:00; Sudsing 17:45–18:15 overlaps Foods Dinner 18:00–20:30.
    const mr = blockOf(r.container, "Morning Routine");
    const fb = blockOf(r.container, "Foods Breakfast");
    const sudsing = blockOf(r.container, "Sudsing");
    const dinner = blockOf(r.container, "Foods Dinner");
    // Overlapping rows never share a stack row, so labels cannot cover.
    expect(mr.style.top).not.toBe(fb.style.top);
    expect(sudsing.style.top).not.toBe(dinner.style.top);
    // A row that does not overlap anything sits on the base stack row.
    const live = blockOf(r.container, "Live");
    expect(parseFloat(live.style.top)).toBe(0);
    // The lane grows to fit the deepest stack.
    const lane = r.container.querySelector(".timeline__lane--anchors .timeline__track") as HTMLElement;
    expect(parseFloat(lane.style.height)).toBeGreaterThanOrEqual(3 * 34);
  });

  it("shows an empty-lane message when the frame has rows but no calendar blocks", () => {
    const h = makeHarness("ready");
    const inputs = structuredClone(h.store.getState().inputs!);
    inputs.anchored = (inputs.anchored ?? []).filter((a) => a.kind !== "calendar");
    h.store.dispatch({ type: "INPUTS_LOADED", inputs, ledger: h.store.getState().ledger! });
    const r = h.ui(<CalendarImpact />);
    const calendarLane = r.container.querySelector(".timeline__lane--calendar")!;
    expect(calendarLane.querySelectorAll("[data-block]").length).toBe(0);
    expect(r.getByText("No calendar rows today.")).toBeTruthy();
    // The lane frame still renders (not a collapsed gap).
    expect(r.container.querySelector(".timeline__lane--calendar .timeline__track")).toBeTruthy();
  });

  it("renders the work window as a shaded zone toggled by the header control", () => {
    const h = harnessWithCalendar([calendarRow({ id: "Row", name: "Row" })]);
    const r = h.ui(<CalendarImpact />);
    expect(r.container.querySelector(".timeline__workzone")).toBeTruthy();
    fireEvent.click(r.getByLabelText("Show work window"));
    expect(r.container.querySelector(".timeline__workzone")).toBeNull();
  });
});

describe("U10 timeline — keyboard select", () => {
  it("moves focus with arrows, opens the detail row, and Escape returns focus", () => {
    const h = harnessWithCalendar([
      calendarRow({ id: "First", name: "First", start: "09:00", end: "09:30" }),
      calendarRow({ id: "Second", name: "Second", start: "11:00", end: "11:30" }),
    ]);
    const r = h.ui(<CalendarImpact />);
    const first = blockOf(r.container, "First");
    const second = blockOf(r.container, "Second");

    first.focus();
    expect(document.activeElement).toBe(first);
    // ArrowRight / ArrowLeft move focus through the lane's blocks.
    fireEvent.keyDown(first, { key: "ArrowRight" });
    expect(document.activeElement).toBe(second);
    fireEvent.keyDown(second, { key: "ArrowLeft" });
    expect(document.activeElement).toBe(first);

    // A native button's Enter activation dispatches click; the detail row opens
    // and takes focus so Escape is local.
    fireEvent.click(first);
    const detail = r.container.querySelector(".timeline__detail") as HTMLElement | null;
    expect(detail).toBeTruthy();
    expect(detail!.getAttribute("aria-label")).toBe("Detail for First");
    expect(document.activeElement).toBe(detail);

    // Escape closes the detail and returns focus to the block that opened it.
    fireEvent.keyDown(detail!, { key: "Escape" });
    expect(r.container.querySelector(".timeline__detail")).toBeNull();
    expect(document.activeElement).toBe(first);
  });
});

describe("U10 timeline — accounting actions reach the capacity card", () => {
  it("duration ± and Exclude on a block move the capacity card headline", async () => {
    const h = makeHarness("ready");
    const r = h.ui(<Queue />);
    const headline = () => r.container.querySelector(".cap-card__headline")!.textContent ?? "";
    const amount = () => parseInt(headline().match(/(\d+)\s*blk/)?.[1] ?? "NaN", 10);

    // Open an anchor block in the timeline detail row and add counted time.
    fireEvent.click(r.getByRole("button", { name: /Sudsing/ }));
    const before = amount();
    fireEvent.click(r.getByRole("button", { name: "More counted time for Sudsing (today only)" }));
    await waitFor(() => expect(amount()).toBe(before - 1));

    // Excluding the block frees its counted time back (the +1 block plus the
    // original block), so the card gains a block over the starting read.
    fireEvent.click(r.getByRole("button", { name: "Do not count Sudsing toward today's capacity" }));
    await waitFor(() => expect(amount()).toBe(before + 1));
  });
});
