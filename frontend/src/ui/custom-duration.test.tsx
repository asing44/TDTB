/* custom-duration.test.tsx — the expanded row's exact-minutes field.
   Row redesign (2026-10-28): the duration chips and the Save/Discard footer are
   gone. A typed duration APPLIES AT ONCE through controller.applyRowEdit (exact
   minutes, blocks = minutes / 30), with one Undo step; typing is debounced
   400ms and also applies on blur/Enter. Fixture adapters only. */

import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, waitFor } from "@testing-library/preact";

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

/** The expanded row's exact-minutes field. */
const custom = (row: Element) =>
  row.querySelector<HTMLInputElement>('input[aria-label="Exact minutes"]')!;
const btn = (row: Element, text: string) =>
  Array.from(row.querySelectorAll<HTMLButtonElement>("button")).find(
    (b) => b.textContent?.trim() === text,
  ) ?? null;

function type(input: HTMLInputElement, value: string) {
  fireEvent.input(input, { target: { value } });
}

describe("typed exact duration on the expanded row", () => {
  it("Enter applies exact minutes as fractional blocks at once, no Save step", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    let row = expand(container, "Press");
    type(custom(row), "10m");
    fireEvent.keyDown(custom(row), { key: "Enter" });

    expect(h.store.getState().overrides["Press"]?.blocks).toBeCloseTo(10 / 30, 10);
    expect(effectiveBlocks(h.store.getState(), "Press")).toBeCloseTo(10 / 30, 10);
    row = rowFor(container, "Press");
    // The old Save/Discard footer is gone; the value is already the row's.
    expect(btn(row, "Save")).toBeNull();
    expect(btn(row, "Discard")).toBeNull();
    expect(row.querySelector(".dslider__label")?.textContent).toBe("10min");
    // One Undo step is offered for the edit.
    expect(row.querySelector(".dux-undo")).not.toBeNull();
  });

  it("a second typed value applies on top of the first (no Discard path)", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    let row = expand(container, "Press");
    type(custom(row), "1h 15m");
    fireEvent.keyDown(custom(row), { key: "Enter" });
    expect(h.store.getState().overrides["Press"]?.blocks).toBeCloseTo(2.5, 10);

    row = rowFor(container, "Press");
    type(custom(row), "20");
    fireEvent.keyDown(custom(row), { key: "Enter" });
    expect(h.store.getState().overrides["Press"]?.blocks).toBeCloseTo(20 / 30, 10);
  });

  it("blur applies a valid value", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    const row = expand(container, "Press");
    type(custom(row), "45min");
    fireEvent.blur(custom(row));
    expect(h.store.getState().overrides["Press"]?.blocks).toBe(1.5);
  });

  it("debounces a typed value, applying it without Enter or blur", async () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    const row = expand(container, "Press");
    type(custom(row), "45m");
    expect(h.store.getState().overrides["Press"]).toBeUndefined();
    await waitFor(
      () => expect(h.store.getState().overrides["Press"]?.blocks).toBe(1.5),
      { timeout: 2000 },
    );
  });

  it("rejects invalid input inline with an aria-described error and applies nothing", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    let row = expand(container, "Press");
    const before = effectiveBlocks(h.store.getState(), "Press");
    for (const bad of ["0", "-5", "17h", "abc"]) {
      type(custom(row), bad);
      fireEvent.keyDown(custom(row), { key: "Enter" });
      row = rowFor(container, "Press");
      const input = custom(row);
      expect(input.getAttribute("aria-invalid")).toBe("true");
      const msg = row.querySelector(`#${input.getAttribute("aria-describedby")}`);
      expect(msg?.textContent?.length).toBeGreaterThan(0);
      expect(effectiveBlocks(h.store.getState(), "Press")).toBe(before);
      expect(h.store.getState().overrides["Press"]).toBeUndefined();
    }
  });

  it("Escape reverts the typed text and clears the error", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    let row = expand(container, "Press");
    type(custom(row), "zzz");
    fireEvent.keyDown(custom(row), { key: "Enter" });
    row = rowFor(container, "Press");
    fireEvent.keyDown(custom(row), { key: "Escape" });
    row = rowFor(container, "Press");
    expect(custom(row).value).toBe("");
    expect(custom(row).getAttribute("aria-invalid")).not.toBe("true");
  });

  it("applies an exact off-grid duration that the slider cannot express", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    let row = expand(container, "Press");
    type(custom(row), "25m");
    fireEvent.keyDown(custom(row), { key: "Enter" });
    expect(h.store.getState().overrides["Press"]?.blocks).toBeCloseTo(25 / 30, 10);
    row = rowFor(container, "Press");
    // The readout keeps the exact minutes while the thumb rests on 30.
    expect(row.querySelector(".dslider__label")?.textContent).toBe("25min");
  });

  it("is disabled for an all-day row", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    const row = expand(container, "Charge GoPro");
    expect(custom(row).disabled).toBe(true);
  });
});
