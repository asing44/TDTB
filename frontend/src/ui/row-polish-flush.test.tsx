/* row-polish-flush.test.tsx — a pending exact-minutes value is flushed (not
   dropped) when the expanded panel unmounts inside the 400ms debounce. */

import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent } from "@testing-library/preact";

import { Queue } from "./Queue";
import { makeHarness } from "./test-harness";
import { effectiveBlocks } from "../store/store";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

const rowFor = (container: Element, name: string): HTMLElement => {
  const row = Array.from(container.querySelectorAll<HTMLElement>(".qrow")).find(
    (r) => r.querySelector(".qrow__name")?.textContent === name,
  );
  if (!row) throw new Error(`no row named ${name}`);
  return row;
};
const toggle = (row: Element) =>
  row.querySelector<HTMLButtonElement>("button[aria-expanded][aria-label='Expand'], button[aria-expanded][aria-label='Collapse']")!;
const exact = (row: Element) =>
  row.querySelector<HTMLInputElement>('input[aria-label="Exact minutes"]')!;

function open() {
  vi.useFakeTimers();
  const h = makeHarness("ready");
  const { container } = h.ui(<Queue />);
  fireEvent.click(toggle(rowFor(container, "Press")));
  return { h, container };
}

describe("exact-minutes flush on unmount", () => {
  it("applies a pending valid value when the row collapses in the window", () => {
    const { h, container } = open();
    fireEvent.input(exact(rowFor(container, "Press")), { target: { value: "25" } });
    expect(effectiveBlocks(h.store.getState(), "Press")).toBe(3);
    fireEvent.click(toggle(rowFor(container, "Press")));
    expect(exact(rowFor(container, "Press"))).toBeNull();
    expect(effectiveBlocks(h.store.getState(), "Press") * 30).toBeCloseTo(25, 5);
    act(() => {
      vi.advanceTimersByTime(1000);
    });
    expect(effectiveBlocks(h.store.getState(), "Press") * 30).toBeCloseTo(25, 5);
  });

  it("applies nothing for invalid text", () => {
    const { h, container } = open();
    fireEvent.input(exact(rowFor(container, "Press")), { target: { value: "2x" } });
    fireEvent.click(toggle(rowFor(container, "Press")));
    expect(h.store.getState().overrides["Press"]).toBeUndefined();
    expect(effectiveBlocks(h.store.getState(), "Press")).toBe(3);
  });

  it("applies exactly once when blur already flushed", () => {
    const { h, container } = open();
    const field = exact(rowFor(container, "Press"));
    fireEvent.input(field, { target: { value: "25m" } });
    fireEvent.blur(field);
    const after = h.store.getState();
    fireEvent.click(toggle(rowFor(container, "Press")));
    expect(h.store.getState().rowUndo).toBe(after.rowUndo);
    expect(h.store.getState().overrides).toEqual(after.overrides);
  });

  it("does not apply after Escape abandoned the text", () => {
    const { h, container } = open();
    const field = exact(rowFor(container, "Press"));
    fireEvent.input(field, { target: { value: "25m" } });
    fireEvent.keyDown(field, { key: "Escape" });
    fireEvent.click(toggle(rowFor(container, "Press")));
    expect(h.store.getState().overrides["Press"]).toBeUndefined();
  });
});
