/* row-immediate-apply.test.tsx — the row redesign's core contract: a row edit
   is a LOCAL day-shaped change that lands the moment it is made, and the row
   keeps exactly ONE Undo step for it.

   There is no Save/Discard footer, no staged/"Unsaved" chip and no duration
   chip strip: every control calls controller.applyRowEdit, which writes the
   committed session immediately and records the pre-edit session in
   s.rowUndo; controller.undoRowEdit (the row's Undo button) restores it. The
   exact-minutes field is the one exception in timing only — it debounces
   typing 400ms and also flushes on Enter or blur.

   Fixture adapters only: no live adapter, no billed call, no source write. */

import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent } from "@testing-library/preact";

import { Queue } from "./Queue";
import { makeHarness } from "./test-harness";
import { effectiveBlocks } from "../store/store";
import { EXCLUSION_REASONS } from "../model/batchEligibility";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

function rowFor(container: Element, name: string): HTMLElement {
  const row = Array.from(container.querySelectorAll<HTMLElement>(".qrow")).find(
    (r) => r.querySelector(".qrow__name")?.textContent === name,
  );
  if (!row) throw new Error(`no row named ${name}`);
  return row;
}

const range = (row: Element) =>
  row.querySelector<HTMLInputElement>('input[aria-label="Duration slider"]')!;
const more = (row: Element) =>
  row.querySelector<HTMLButtonElement>('button[aria-label^="15 minutes more"]')!;
const undo = (row: Element) =>
  row.querySelector<HTMLButtonElement>('button[aria-label^="Undo last change"]');
const exact = (row: Element) =>
  row.querySelector<HTMLInputElement>('input[aria-label="Exact minutes"]')!;
const mint = (row: Element) =>
  row.querySelector<HTMLButtonElement>('button[aria-label^="May overlap Mint"]')!;
const buttonText = (row: Element, text: string) =>
  Array.from(row.querySelectorAll<HTMLButtonElement>("button")).find(
    (b) => b.textContent?.trim() === text,
  );

describe("immediate apply with a single Undo step", () => {
  it("applies a duration edit at once and Undo restores the prior value", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    const row = rowFor(container, "Press");
    expect(effectiveBlocks(h.store.getState(), "Press")).toBe(3);
    expect(undo(row)).toBeNull();

    fireEvent.click(more(row));
    // Applied immediately — nothing is waiting behind a Save.
    expect(effectiveBlocks(h.store.getState(), "Press")).toBe(3.5);
    expect(rowFor(container, "Press").querySelector(".dslider__label")?.textContent).toBe(
      "1hr 45min",
    );
    const undoBtn = undo(rowFor(container, "Press"))!;
    expect(undoBtn).not.toBeNull();

    fireEvent.click(undoBtn);
    expect(effectiveBlocks(h.store.getState(), "Press")).toBe(3);
    expect(rowFor(container, "Press").querySelector(".dslider__label")?.textContent).toBe(
      "1hr 30min",
    );
    // One step only: the row came from its source, so the undo also clears
    // the today-override the edit wrote (blocks back to null), leaving no
    // second step to walk back.
    expect(h.store.getState().overrides["Press"]?.blocks ?? null).toBeNull();
    expect(undo(rowFor(container, "Press"))).toBeNull();
  });

  it("coalesces a slider burst into one Undo step", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    fireEvent.click(more(rowFor(container, "Press")));
    expect(effectiveBlocks(h.store.getState(), "Press")).toBe(3.5);
    fireEvent.click(more(rowFor(container, "Press")));
    expect(effectiveBlocks(h.store.getState(), "Press")).toBe(4);

    fireEvent.click(undo(rowFor(container, "Press"))!);
    // A burst of edits is one step: back to the value before it started.
    expect(effectiveBlocks(h.store.getState(), "Press")).toBe(3);
    expect(undo(rowFor(container, "Press"))).toBeNull();
  });

  it("keeps one step, not a history, across separate exact-minute edits", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    fireEvent.click(rowFor(container, "Press").querySelector('button[aria-label="Expand"]')!);
    const type = (text: string) => {
      const field = exact(rowFor(container, "Press"));
      fireEvent.input(field, { target: { value: text } });
      fireEvent.keyDown(field, { key: "Enter" });
    };
    type("2h");
    expect(effectiveBlocks(h.store.getState(), "Press")).toBe(4);
    type("1h");
    expect(effectiveBlocks(h.store.getState(), "Press")).toBe(2);

    fireEvent.click(undo(rowFor(container, "Press"))!);
    // Back to the value before the LAST edit — the earlier edit is not kept.
    expect(effectiveBlocks(h.store.getState(), "Press")).toBe(4);
    expect(undo(rowFor(container, "Press"))).toBeNull();
  });

  it("drops a source-derived row's override when the change is undone", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    const item = h.store.getState().inputs!.assigned.find((i) => i.id === "Press")!;
    expect(item.blocks).toBe(3);
    expect(h.store.getState().overrides["Press"]).toBeUndefined();

    fireEvent.input(range(rowFor(container, "Press")), { target: { value: "60" } });
    expect(h.store.getState().overrides["Press"]?.blocks).toBe(2);
    expect(effectiveBlocks(h.store.getState(), "Press")).toBe(2);

    fireEvent.click(undo(rowFor(container, "Press"))!);
    expect(h.store.getState().overrides["Press"]?.blocks ?? null).toBeNull();
    // The row is back on its source estimate, not on a zeroed override.
    expect(effectiveBlocks(h.store.getState(), "Press")).toBe(3);
  });
});

describe("exact-minutes field: debounced typing, immediate Enter and blur", () => {
  it("debounces typing, then applies the parsed duration", () => {
    vi.useFakeTimers();
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    fireEvent.click(rowFor(container, "Press").querySelector('button[aria-label="Expand"]')!);
    const field = exact(rowFor(container, "Press"));

    fireEvent.input(field, { target: { value: "25m" } });
    // Still inside the 400ms window: nothing has been applied.
    expect(effectiveBlocks(h.store.getState(), "Press")).toBe(3);

    act(() => {
      vi.advanceTimersByTime(400);
    });
    expect(h.store.getState().overrides["Press"]?.blocks).toBeCloseTo(25 / 30, 5);
    expect(effectiveBlocks(h.store.getState(), "Press") * 30).toBeCloseTo(25, 5);
  });

  it("flushes the debounce on Enter", () => {
    vi.useFakeTimers();
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    fireEvent.click(rowFor(container, "Press").querySelector('button[aria-label="Expand"]')!);
    const field = exact(rowFor(container, "Press"));

    fireEvent.input(field, { target: { value: "1h10" } });
    expect(effectiveBlocks(h.store.getState(), "Press")).toBe(3);
    fireEvent.keyDown(field, { key: "Enter" });
    expect(effectiveBlocks(h.store.getState(), "Press")).toBe(70 / 30);
  });

  it("flushes the debounce on blur", () => {
    vi.useFakeTimers();
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    fireEvent.click(rowFor(container, "Press").querySelector('button[aria-label="Expand"]')!);
    const field = exact(rowFor(container, "Press"));

    fireEvent.input(field, { target: { value: "2h" } });
    expect(effectiveBlocks(h.store.getState(), "Press")).toBe(3);
    fireEvent.blur(field);
    expect(effectiveBlocks(h.store.getState(), "Press")).toBe(4);
  });

  it("rejects unparseable text without changing the row", () => {
    vi.useFakeTimers();
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    fireEvent.click(rowFor(container, "Press").querySelector('button[aria-label="Expand"]')!);
    const field = exact(rowFor(container, "Press"));

    fireEvent.input(field, { target: { value: "soon" } });
    act(() => {
      vi.advanceTimersByTime(400);
    });
    fireEvent.keyDown(field, { key: "Enter" });
    expect(effectiveBlocks(h.store.getState(), "Press")).toBe(3);
    expect(h.store.getState().overrides["Press"]).toBeUndefined();
    expect(rowFor(container, "Press").querySelector('[role="alert"]')).not.toBeNull();
  });
});

describe("permanent May overlap Mint toggle on the collapsed row", () => {
  it("applies the grant at once and toggles its pressed state", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    const before = mint(rowFor(container, "Press"));
    expect(before.getAttribute("aria-pressed")).toBe("false");
    expect(before.disabled).toBe(false);
    expect(h.store.getState().mintOverlapOptIns["Press"]).toBeUndefined();

    fireEvent.click(before);
    expect(h.store.getState().mintOverlapOptIns["Press"]).toBe(true);
    const after = mint(rowFor(container, "Press"));
    expect(after.getAttribute("aria-pressed")).toBe("true");
    expect(after.getAttribute("title")).toContain("on");

    fireEvent.click(after);
    // Turning the grant off drops the opt-in entirely.
    expect(h.store.getState().mintOverlapOptIns["Press"] ?? false).toBe(false);
    expect(mint(rowFor(container, "Press")).getAttribute("aria-pressed")).toBe("false");
  });

  it("keeps a single Undo step for the grant", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    fireEvent.click(mint(rowFor(container, "Press")));
    expect(h.store.getState().mintOverlapOptIns["Press"]).toBe(true);
    fireEvent.click(undo(rowFor(container, "Press"))!);
    expect(h.store.getState().mintOverlapOptIns["Press"] ?? false).toBe(false);
  });

  it("disables the toggle with the reason as its tooltip on an all-day row", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    const allDay = mint(rowFor(container, "Charge GoPro"));
    // "May overlap Mint" is permanent on the row but honest about why it is
    // closed: the reason rides the tooltip (and the disabled state).
    expect(allDay.disabled).toBe(true);
    expect(allDay.getAttribute("aria-pressed")).toBe("false");
    expect(allDay.getAttribute("title")).toBe(EXCLUSION_REASONS.mintAllDay);
    expect(h.store.getState().mintOverlapOptIns["Charge GoPro"]).toBeUndefined();
  });
});

describe("no staged affordances survive", () => {
  it("renders no Save/Discard footer and no Unsaved chip in the expanded panel", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    fireEvent.click(rowFor(container, "Press").querySelector('button[aria-label="Expand"]')!);
    fireEvent.input(range(rowFor(container, "Press")), { target: { value: "120" } });
    const row = rowFor(container, "Press");

    expect(buttonText(row, "Save")).toBeUndefined();
    expect(buttonText(row, "Discard")).toBeUndefined();
    expect(row.querySelector(".trow__state--staged")).toBeNull();
    expect(row.textContent).not.toMatch(/Unsaved/i);
    expect(row.textContent).not.toMatch(/Staged/i);
    // The edit already landed, so the duration reads as a today-only change.
    expect(effectiveBlocks(h.store.getState(), "Press")).toBe(4);
    expect(row.querySelector(".dux-badge")?.textContent).toContain("Today");
  });
});
