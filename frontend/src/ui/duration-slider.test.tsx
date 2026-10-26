/* duration-slider.test.tsx — the expanded row's duration range slider stages
   through the same draft path as the select and typed custom duration. */

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

const slider = (row: Element) =>
  row.querySelector<HTMLInputElement>('input[aria-label="Duration slider"]')!;
const preset = (row: Element) =>
  row.querySelector<HTMLSelectElement>('select[aria-label="Duration"]')!;
const custom = (row: Element) =>
  row.querySelector<HTMLInputElement>('input[aria-label="Custom duration"]')!;
const btn = (row: Element, text: string) =>
  Array.from(row.querySelectorAll<HTMLButtonElement>("button")).find(
    (b) => b.textContent?.trim() === text,
  ) ?? null;

describe("duration slider on the expanded row", () => {
  it("is a 5-240 minute range in 5-minute steps", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    const s = slider(expand(container, "Press"));
    expect(s.type).toBe("range");
    expect([s.min, s.max, s.step]).toEqual(["5", "240", "5"]);
  });

  it("stages the same blocks as the select and the custom field", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    let row = expand(container, "Press");
    fireEvent.input(slider(row), { target: { value: "90" } });
    const viaSlider = h.store.getState().drafts["Press"].blocks;

    row = rowFor(container, "Press");
    fireEvent.change(preset(row), { target: { value: "3" } });
    expect(viaSlider).toBe(h.store.getState().drafts["Press"].blocks);

    row = rowFor(container, "Press");
    fireEvent.input(custom(row), { target: { value: "90min" } });
    fireEvent.keyDown(custom(row), { key: "Enter" });
    expect(viaSlider).toBe(h.store.getState().drafts["Press"].blocks);
    expect(viaSlider).toBe(3);
  });

  it("stages exact minutes, updates the live label and the select", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    let row = expand(container, "Press");
    fireEvent.input(slider(row), { target: { value: "45" } });
    row = rowFor(container, "Press");
    expect(h.store.getState().drafts["Press"].blocks).toBe(1.5);
    expect(row.querySelector(".dslider__label")?.textContent).toBe("45min");
    expect(slider(row).getAttribute("aria-valuetext")).toBe("45min");
    expect(slider(row).value).toBe("45");
    expect(preset(row).selectedOptions[0].textContent).toBe("45min");
    expect(btn(row, "Save")).not.toBeNull();
  });

  it("Save commits and Discard drops the slider value", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    let row = expand(container, "Press");
    fireEvent.input(slider(row), { target: { value: "75" } });
    row = rowFor(container, "Press");
    fireEvent.click(btn(row, "Save")!);
    expect(h.store.getState().overrides["Press"]?.blocks).toBe(2.5);

    row = rowFor(container, "Press");
    fireEvent.input(slider(row), { target: { value: "30" } });
    row = rowFor(container, "Press");
    fireEvent.click(btn(row, "Discard")!);
    expect(h.store.getState().overrides["Press"]?.blocks).toBe(2.5);
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

  it("is disabled for an all-day row", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    let row = expand(container, "Press");
    fireEvent.click(row.querySelector<HTMLInputElement>('input[value="allday"]')!);
    row = rowFor(container, "Press");
    expect(slider(row).disabled).toBe(true);
    expect(preset(row).disabled).toBe(true);
  });
});
