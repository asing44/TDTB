/* duration-slider.test.tsx — the collapsed row's duration range slider.
   Row redesign (2026-10-28): the slider IS the row's one duration control and
   every move APPLIES AT ONCE through controller.applyRowEdit (no Save/Discard,
   no staged chip); a burst of slider moves keeps one Undo step. The chips that
   used to mirror it are gone. */

import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent } from "@testing-library/preact";

afterEach(cleanup);

import { Queue } from "./Queue";
import { makeHarness } from "./test-harness";
import { effectiveBlocks } from "../store/store";

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
/** The expanded panel's All-day toggle (aria-pressed button, not a radio). */
const allDayToggle = (row: Element) =>
  Array.from(row.querySelectorAll<HTMLButtonElement>("button")).find(
    (b) => b.textContent?.trim() === "All-day",
  )!;
const btn = (row: Element, text: string) =>
  Array.from(row.querySelectorAll<HTMLButtonElement>("button")).find(
    (b) => b.textContent?.trim() === text,
  ) ?? null;
const step = (row: Element, label: string) =>
  row.querySelector<HTMLButtonElement>(`button[aria-label^="${label}"]`)!;

describe("duration slider on the row", () => {
  it("is a 0-240 minute range in quarter-hour steps, All-day at 0", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    const s = slider(expand(container, "Press"));
    expect(s.type).toBe("range");
    expect([s.min, s.max, s.step]).toEqual(["0", "240", "15"]);
  });

  it("applies the same blocks as the exact-minutes field", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    let row = expand(container, "Press");
    fireEvent.input(slider(row), { target: { value: "120" } });
    const viaSlider = h.store.getState().overrides["Press"]?.blocks;
    expect(viaSlider).toBe(4);

    row = rowFor(container, "Press");
    fireEvent.input(custom(row), { target: { value: "120min" } });
    fireEvent.keyDown(custom(row), { key: "Enter" });
    expect(h.store.getState().overrides["Press"]?.blocks).toBe(viaSlider);
    expect(effectiveBlocks(h.store.getState(), "Press")).toBe(4);
  });

  it("applies exact minutes at once, updates the live label, no Save step", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    let row = expand(container, "Press");
    fireEvent.input(slider(row), { target: { value: "45" } });
    row = rowFor(container, "Press");
    expect(h.store.getState().overrides["Press"]?.blocks).toBe(1.5);
    expect(row.querySelector(".dslider__label")?.textContent).toBe("45min");
    expect(slider(row).getAttribute("aria-valuetext")).toBe("45min");
    expect(slider(row).value).toBe("45");
    expect(btn(row, "Save")).toBeNull();
    expect(btn(row, "Discard")).toBeNull();
  });

  it("one Undo reverses the applied duration back to the source estimate", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    let row = expand(container, "Press");
    fireEvent.input(slider(row), { target: { value: "75" } });
    expect(h.store.getState().overrides["Press"]?.blocks).toBe(2.5);

    row = rowFor(container, "Press");
    fireEvent.click(row.querySelector<HTMLElement>(".dux-undo")!);
    expect(h.store.getState().overrides["Press"]?.blocks ?? null).toBeNull();
    expect(effectiveBlocks(h.store.getState(), "Press")).toBe(3);
  });

  it("is a native range input, so arrow keys step it (no custom key handling)", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    const s = slider(expand(container, "Press"));
    expect(s.tagName).toBe("INPUT");
    expect(s.hasAttribute("tabindex")).toBe(false);
    expect(s.disabled).toBe(false);
    expect(s.getAttribute("aria-label")).toBe("Duration slider");
  });

  it("keeps the All-day notch live and in step with the All-day toggle", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    let row = expand(container, "Press");
    expect(allDayToggle(row).getAttribute("aria-pressed")).toBe("false");
    fireEvent.click(allDayToggle(row));
    row = rowFor(container, "Press");
    expect(slider(row).value).toBe("0");
    expect(slider(row).getAttribute("aria-valuetext")).toBe("All day");
    expect(slider(row).disabled).toBe(false);
    expect(allDayToggle(row).getAttribute("aria-pressed")).toBe("true");

    // Leaving the All-day notch the same way the toggle does: a timed value.
    fireEvent.input(slider(row), { target: { value: "60" } });
    row = rowFor(container, "Press");
    expect(h.store.getState().overrides["Press"]?.blocks).toBe(2);
    expect(allDayToggle(row).getAttribute("aria-pressed")).toBe("false");
    expect(step(row, "15 minutes more").disabled).toBe(false);
  });
});
