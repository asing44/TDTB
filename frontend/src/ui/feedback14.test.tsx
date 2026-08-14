/* FEEDBACK-14 — the ← → shortcut contract is ±15 minutes everywhere:
   the rail hint, the keyboard action, the pointer steppers, the slider grid,
   the persisted override, and the 12-hour wall-clock labels on the surface.
   No affected hint may display or apply ±30 minutes. */

import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent } from "@testing-library/preact";

afterEach(cleanup);

import { Rail } from "./Rail";
import { Queue } from "./Queue";
import { makeHarness } from "./test-harness";
import { effectiveBlocks } from "../store/store";
import { HALF_BLOCK, MAX_BLOCKS, MIN_BLOCKS } from "../model/allocator";

function rowFor(container: HTMLElement, name: string): HTMLElement {
  const found = Array.from(container.querySelectorAll<HTMLElement>(".qrow")).find(
    (r) => r.textContent?.includes(name),
  );
  if (!found) throw new Error(`queue row not found: ${name}`);
  return found;
}

describe("FEEDBACK-14: ±15 min shortcut display", () => {
  it("the rail keys card hints ±15 min and never ±30 min", () => {
    const h = makeHarness("ready");
    const { getByText, queryByText, container } = h.ui(<Rail />);
    expect(getByText("±15min")).toBeTruthy();
    expect(queryByText("±30min")).toBeNull();
    const keys = container.querySelector('[aria-label="Keyboard shortcuts"]');
    expect(keys).toBeTruthy();
    expect(keys?.textContent).toContain("← →");
    expect(keys?.textContent).toContain("±15min");
    expect(keys?.textContent).not.toContain("30min");
  });
});

describe("FEEDBACK-14: ±15 min keyboard action", () => {
  it("ArrowRight raises the effective duration by exactly 15 minutes", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    const row = rowFor(container, "Magic Mirror"); // 3 blocks
    fireEvent.keyDown(row, { key: "ArrowRight" });
    expect(effectiveBlocks(h.store.getState(), "Magic Mirror")).toBe(3 + HALF_BLOCK);
    expect(h.store.getState().overrides["Magic Mirror"]?.blocks).toBe(3 + HALF_BLOCK);
  });

  it("ArrowLeft lowers the effective duration by exactly 15 minutes", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    const row = rowFor(container, "Note Processing"); // 1 block
    fireEvent.keyDown(row, { key: "ArrowLeft" });
    expect(effectiveBlocks(h.store.getState(), "Note Processing")).toBe(1 - HALF_BLOCK);
    expect(h.store.getState().overrides["Note Processing"]?.blocks).toBe(1 - HALF_BLOCK);
  });

  it("both arrow directions step 15 minutes from the same base", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    const row = rowFor(container, "Magic Mirror"); // 3 blocks
    fireEvent.keyDown(row, { key: "ArrowRight" }); // 3.5
    fireEvent.keyDown(row, { key: "ArrowLeft" }); // back to 3
    expect(effectiveBlocks(h.store.getState(), "Magic Mirror")).toBe(3);
  });
});

describe("FEEDBACK-14: boundary behavior", () => {
  it("ArrowLeft walks 1 block → 15 min → all day and clamps at the floor", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    const row = rowFor(container, "Note Processing"); // 1 block
    fireEvent.keyDown(row, { key: "ArrowLeft" }); // 0.5 → 15 min
    expect(effectiveBlocks(h.store.getState(), "Note Processing")).toBe(HALF_BLOCK);
    fireEvent.keyDown(row, { key: "ArrowLeft" }); // 0 → all day
    expect(effectiveBlocks(h.store.getState(), "Note Processing")).toBe(MIN_BLOCKS);
    fireEvent.keyDown(row, { key: "ArrowLeft" }); // clamped at 0
    expect(effectiveBlocks(h.store.getState(), "Note Processing")).toBe(MIN_BLOCKS);
  });

  it("ArrowRight from all day lands on 15 min, not 30 min", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    const row = rowFor(container, "Charge GoPro"); // 0 blocks
    fireEvent.keyDown(row, { key: "ArrowLeft" }); // clamped at 0
    expect(effectiveBlocks(h.store.getState(), "Charge GoPro")).toBe(MIN_BLOCKS);
    fireEvent.keyDown(row, { key: "ArrowRight" });
    expect(effectiveBlocks(h.store.getState(), "Charge GoPro")).toBe(HALF_BLOCK);
  });

  it("ArrowRight clamps at the 16-block upper bound", () => {
    const h = makeHarness("ready");
    h.store.dispatch({
      type: "OVERRIDE_SET",
      id: "Magic Mirror",
      override: { included: true, blocks: MAX_BLOCKS },
    });
    const { container } = h.ui(<Queue />);
    const row = rowFor(container, "Magic Mirror");
    fireEvent.keyDown(row, { key: "ArrowRight" });
    expect(effectiveBlocks(h.store.getState(), "Magic Mirror")).toBe(MAX_BLOCKS);
  });
});

describe("FEEDBACK-14: pointer controls and persistence consistency", () => {
  it("pointer steppers share the ±15 min grid and persist the same value", () => {
    const h = makeHarness("ready");
    const { getByRole } = h.ui(<Queue />);
    fireEvent.click(getByRole("button", { name: "15 minutes more for Magic Mirror" }));
    expect(effectiveBlocks(h.store.getState(), "Magic Mirror")).toBe(3 + HALF_BLOCK);
    expect(h.store.getState().overrides["Magic Mirror"]?.blocks).toBe(3 + HALF_BLOCK);
    fireEvent.click(getByRole("button", { name: "15 minutes less for Note Processing" }));
    expect(effectiveBlocks(h.store.getState(), "Note Processing")).toBe(0.5);
    expect(h.store.getState().overrides["Note Processing"]?.blocks).toBe(0.5);
  });

  it("the slider grid matches the ±15 min steppers and keyboard", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    const slider = container.querySelector(
      'input[aria-label="Magic Mirror duration in 15-minute steps"]',
    ) as HTMLInputElement;
    expect(slider).toBeTruthy();
    expect(slider.step).toBe(String(HALF_BLOCK));
  });
});

describe("FEEDBACK-14: 12-hour labels on the tested surface", () => {
  it("scheduled row times render 12-hour with AM/PM and no bare 24-hour readout", () => {
    const h = makeHarness("sequenced");
    const { container } = h.ui(<Queue />);
    expect(container.textContent).toMatch(/10:45 AM/);
    expect(container.textContent).toMatch(/AM|PM/);
    expect(container.textContent).not.toMatch(/\b\d{2}:\d{2}\b(?!\s*(AM|PM))/);
  });
});
