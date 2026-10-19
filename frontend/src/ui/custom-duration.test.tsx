/* custom-duration.test.tsx — the expanded row's Duration can be typed.
   The preset select stays; a "Custom duration" text input stages any
   parseable duration (exact minutes, blocks = minutes / 30) on the same row
   draft, and Save/Discard remain the only commit path. Fixture adapters only. */

import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent } from "@testing-library/preact";

afterEach(cleanup);

import { Queue } from "./Queue";
import { makeHarness } from "./test-harness";

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

const custom = (row: Element) =>
  row.querySelector<HTMLInputElement>('input[aria-label="Custom duration"]')!;
const preset = (row: Element) =>
  row.querySelector<HTMLSelectElement>('select[aria-label="Duration"]')!;
const btn = (row: Element, text: string) =>
  Array.from(row.querySelectorAll<HTMLButtonElement>("button")).find(
    (b) => b.textContent?.trim() === text,
  ) ?? null;

function type(input: HTMLInputElement, value: string) {
  fireEvent.input(input, { target: { value } });
}

describe("typed custom duration on the expanded row", () => {
  it("Enter stages exact minutes as fractional blocks and shows the label", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    let row = expand(container, "Press");
    type(custom(row), "10m");
    fireEvent.keyDown(custom(row), { key: "Enter" });

    expect(h.store.getState().drafts["Press"].blocks).toBeCloseTo(10 / 30, 10);
    row = rowFor(container, "Press");
    const selected = preset(row).selectedOptions[0];
    expect(selected.textContent).toBe("10min");
    expect(btn(row, "Save")).not.toBeNull();
  });

  it("Save commits the typed duration through the override path; Discard drops it", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    let row = expand(container, "Press");
    type(custom(row), "1h 15m");
    fireEvent.keyDown(custom(row), { key: "Enter" });
    row = rowFor(container, "Press");
    fireEvent.click(btn(row, "Save")!);
    expect(h.store.getState().overrides["Press"]?.blocks).toBeCloseTo(2.5, 10);

    row = rowFor(container, "Press");
    type(custom(row), "20");
    fireEvent.keyDown(custom(row), { key: "Enter" });
    row = rowFor(container, "Press");
    fireEvent.click(btn(row, "Discard")!);
    expect(h.store.getState().overrides["Press"]?.blocks).toBeCloseTo(2.5, 10);
  });

  it("blur applies a valid value", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    const row = expand(container, "Press");
    type(custom(row), "45min");
    fireEvent.blur(custom(row));
    expect(h.store.getState().drafts["Press"].blocks).toBe(1.5);
  });

  it("rejects invalid input inline with an aria-described error and stages nothing", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    let row = expand(container, "Press");
    const before = h.store.getState().drafts["Press"].blocks;
    for (const bad of ["0", "-5", "17h", "abc"]) {
      type(custom(row), bad);
      fireEvent.keyDown(custom(row), { key: "Enter" });
      row = rowFor(container, "Press");
      const input = custom(row);
      expect(input.getAttribute("aria-invalid")).toBe("true");
      const msg = row.querySelector(`#${input.getAttribute("aria-describedby")}`);
      expect(msg?.textContent?.length).toBeGreaterThan(0);
      expect(h.store.getState().drafts["Press"].blocks).toBe(before);
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

  it("the preset select still stages a duration", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    const row = expand(container, "Press");
    fireEvent.change(preset(row), { target: { value: "4" } });
    expect(h.store.getState().drafts["Press"].blocks).toBe(4);
  });

  it("is disabled for an all-day draft", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    const row = expand(container, "Charge GoPro");
    expect(custom(row).disabled).toBe(true);
  });
});
