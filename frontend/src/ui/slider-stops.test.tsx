/* slider-stops.test.tsx — the duration slider's two ends by keyboard.
   Row redesign (2026-10-28): the track runs All-day (0) | 15 min (the real
   floor) … 4 h, and the keyboard reaches both ends deterministically:
   Home -> All-day (or 15 min where All-day is closed), End -> the max. The
   −/+ steppers walk the quarter-hour notches and stop at the floor. An exact
   off-grid duration keeps its minutes while the thumb rests on the nearest
   notch. */

import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent } from "@testing-library/preact";

afterEach(cleanup);

import { Queue } from "./Queue";
import { makeHarness } from "./test-harness";
import { effectiveBlocks } from "../store/store";
import { EXCLUSION_REASONS } from "../model/batchEligibility";
import type { Scenario } from "../fixtures/scenarios";

function rowFor(container: Element, name: string): HTMLElement {
  const row = Array.from(container.querySelectorAll<HTMLElement>(".qrow")).find(
    (r) => r.querySelector(".qrow__name")?.textContent === name,
  );
  if (!row) throw new Error(`no row named ${name}`);
  return row;
}

function expand(container: Element, name: string): HTMLElement {
  fireEvent.click(rowFor(container, name).querySelector('button[aria-label="Expand"]')!);
  return rowFor(container, name);
}

const slider = (row: Element) =>
  row.querySelector<HTMLInputElement>('input[aria-label="Duration slider"]')!;
const custom = (row: Element) =>
  row.querySelector<HTMLInputElement>('input[aria-label="Exact minutes"]')!;
const allDayToggle = (row: Element) =>
  Array.from(row.querySelectorAll<HTMLButtonElement>("button")).find(
    (b) => b.textContent?.trim() === "All-day",
  )!;
const stepper = (row: Element, direction: "less" | "more") =>
  row.querySelector<HTMLButtonElement>(`button[aria-label^="15 minutes ${direction}"]`)!;

/** A native-timed Todoist row: its All-day path is closed. */
function withNativeTime(sc: Scenario): void {
  const press = sc.inputs.assigned.find((i) => i.name === "Press");
  if (!press) throw new Error("scenario is missing the Press row");
  press.scheduledStart = "19:00";
}

describe("duration slider stops", () => {
  it("Home jumps to All-day and End to the largest notch", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    let row = expand(container, "Press");

    fireEvent.keyDown(slider(row), { key: "Home" });
    expect(effectiveBlocks(h.store.getState(), "Press")).toBe(0);
    row = rowFor(container, "Press");
    expect(slider(row).value).toBe("0");
    expect(slider(row).getAttribute("aria-valuetext")).toBe("All day");
    expect(allDayToggle(row).getAttribute("aria-pressed")).toBe("true");

    fireEvent.keyDown(slider(row), { key: "End" });
    expect(effectiveBlocks(h.store.getState(), "Press")).toBe(8);
    row = rowFor(container, "Press");
    expect(slider(row).value).toBe("240");
    expect(allDayToggle(row).getAttribute("aria-pressed")).toBe("false");
  });

  it("walks the quarter-hour notches back to 15 minutes, then All-day", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    let row = expand(container, "Press");
    fireEvent.input(slider(row), { target: { value: "45" } });
    expect(effectiveBlocks(h.store.getState(), "Press")).toBe(1.5);

    row = rowFor(container, "Press");
    fireEvent.click(stepper(row, "less"));
    row = rowFor(container, "Press");
    fireEvent.click(stepper(row, "less"));
    expect(effectiveBlocks(h.store.getState(), "Press")).toBe(0.5);
    row = rowFor(container, "Press");
    expect(slider(row).value).toBe("15");
    expect(slider(row).getAttribute("aria-valuetext")).toBe("15min");

    // 15 minutes is the floor: the only notch below it is the All-day stop.
    fireEvent.click(stepper(row, "less"));
    row = rowFor(container, "Press");
    expect(effectiveBlocks(h.store.getState(), "Press")).toBe(0);
    expect(slider(row).value).toBe("0");
    expect(slider(row).getAttribute("aria-valuetext")).toBe("All day");

    fireEvent.click(stepper(row, "more"));
    expect(effectiveBlocks(h.store.getState(), "Press")).toBe(0.5);
  });

  it("keeps All-day closed at 15 minutes for a protected native time", () => {
    const h = makeHarness("ready", withNativeTime);
    const { container } = h.ui(<Queue />);
    let row = expand(container, "Press");
    expect(slider(row).min).toBe("15");
    expect(allDayToggle(row).disabled).toBe(true);
    expect(allDayToggle(row).getAttribute("title")).toBe(EXCLUSION_REASONS.protectedAllDay);

    fireEvent.keyDown(slider(row), { key: "Home" });
    row = rowFor(container, "Press");
    // Home lands on the floor, never on the closed All-day notch.
    expect(effectiveBlocks(h.store.getState(), "Press")).toBe(0.5);
    expect(slider(row).value).toBe("15");
    expect(slider(row).getAttribute("aria-valuetext")).toBe("15min");
  });

  it("preserves an exact off-grid value while the thumb sits on a notch", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    const row = expand(container, "Press");
    fireEvent.input(custom(row), { target: { value: "25m" } });
    fireEvent.keyDown(custom(row), { key: "Enter" });

    const after = rowFor(container, "Press");
    expect(effectiveBlocks(h.store.getState(), "Press")).toBeCloseTo(25 / 30, 10);
    expect(after.querySelector(".dslider__label")?.textContent).toBe("25min");
    expect(slider(after).getAttribute("aria-valuetext")).toBe("25min");
    expect(slider(after).value).toBe("30");
  });
});
