/* duration-ux.test.tsx — the one-tap duration chips, the save-state chip and
   the Remember-for-next-time switch (2026-10-27 feedback: "unsure if duration
   saving is still in effect", "duration feels like I have to search and focus
   on amount"). Fixture adapters only — nothing here reaches a source. */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, waitFor } from "@testing-library/preact";
import { Queue } from "./Queue";
import { EXCLUSION_REASONS } from "../model/batchEligibility";
import { makeHarness } from "./test-harness";

afterEach(cleanup);

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

const chips = (row: Element) =>
  row.querySelector<HTMLElement>('[role="radiogroup"][aria-label="Duration"]')!;
const chip = (row: Element, minutes: number) =>
  chips(row).querySelector<HTMLButtonElement>(`[role="radio"][data-minutes="${minutes}"]`)!;
const checkedChip = (row: Element) =>
  chips(row).querySelector<HTMLElement>('[role="radio"][aria-checked="true"]')!;
/** The save-state chip inside the expanded panel (the collapsed header has its
    own copy beside the slider readout). */
const panelStatus = (row: Element) =>
  row.querySelector<HTMLElement>(".trow__panel .dux-badge");
const headerSave = (row: Element) =>
  row.querySelector<HTMLButtonElement>('button[aria-label="Save duration"]');
const headerDiscard = (row: Element) =>
  row.querySelector<HTMLButtonElement>('button[aria-label="Discard change"]');
const remember = (row: Element) =>
  Array.from(row.querySelectorAll<HTMLInputElement>('input[role="switch"]')).find((i) =>
    i.closest("label")?.textContent?.includes("Remember for next time"),
  )!;

describe("one-tap duration chips", () => {
  it("a single click stages the duration and marks it Unsaved", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    const row = expand(container, "Press");
    expect(checkedChip(row).getAttribute("data-minutes")).toBe("90"); // 3 blocks
    expect(panelStatus(row)?.getAttribute("data-state")).toBe("source");

    fireEvent.click(chip(row, 120));
    expect(h.store.getState().drafts["Press"].blocks).toBe(4);
    const after = rowFor(container, "Press");
    expect(checkedChip(after).getAttribute("data-minutes")).toBe("120");
    // The whole point of the chip: the state is unmistakable, not a tiny hint.
    expect(panelStatus(after)?.getAttribute("data-state")).toBe("unsaved");
    expect(panelStatus(after)?.textContent).toContain("Unsaved");
    expect(headerSave(after)).not.toBeNull();
  });

  it("arrow keys move to and pick the next chip; Home and End jump", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    let row = expand(container, "Press");
    fireEvent.keyDown(checkedChip(row), { key: "ArrowRight" });
    expect(h.store.getState().drafts["Press"].blocks).toBe(120 / 30);
    row = rowFor(container, "Press");
    fireEvent.keyDown(checkedChip(row), { key: "Home" });
    expect(h.store.getState().drafts["Press"].blocks).toBe(15 / 30);
    row = rowFor(container, "Press");
    // End lands on the last radio in the group, which is All-day — the chips
    // are one radiogroup, so arrow traversal covers every option.
    fireEvent.keyDown(checkedChip(row), { key: "End" });
    expect(checkedChip(rowFor(container, "Press")).getAttribute("data-chip")).toBe("allday");
    expect(h.store.getState().drafts["Press"].mode).toBe("allday");
  });

  it("an off-grid duration shows one Custom chip reporting what is staged", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    let row = expand(container, "Press");
    const custom = row.querySelector<HTMLInputElement>('input[aria-label="Custom duration"]')!;
    fireEvent.input(custom, { target: { value: "25m" } });
    fireEvent.keyDown(custom, { key: "Enter" });
    row = rowFor(container, "Press");
    expect(checkedChip(row).textContent).toBe("Custom: 25m");
    expect(h.store.getState().drafts["Press"].blocks).toBeCloseTo(25 / 30, 10);
  });

  it("keeps All-day closed on a Capacities row and says why", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    const row = expand(container, "Note Processing");
    const allDay = row.querySelector<HTMLButtonElement>('[data-chip="allday"]')!;
    expect(allDay.disabled).toBe(true);
    expect(row.textContent).toContain(EXCLUSION_REASONS.allDayCapacities);
  });
});

describe("save state and the header Save/Discard", () => {
  it("Save in the collapsed header commits the duration and confirms it", async () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    const row = expand(container, "Press");
    fireEvent.click(chip(row, 120));
    const staged = rowFor(container, "Press");
    fireEvent.click(headerSave(staged)!);
    expect(h.store.getState().overrides["Press"]?.blocks).toBe(4);
    const saved = rowFor(container, "Press");
    expect(saved.querySelector(".dux-saved")?.textContent).toContain("Saved");
    expect(headerSave(saved)).toBeNull();
    await waitFor(
      () => expect(panelStatus(rowFor(container, "Press"))?.getAttribute("data-state")).toBe("today"),
      { timeout: 2500 },
    );
  });

  it("Discard drops the staged duration and returns to the source estimate", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    const row = expand(container, "Press");
    fireEvent.click(chip(row, 120));
    const staged = rowFor(container, "Press");
    fireEvent.click(headerDiscard(staged)!);
    expect(h.store.getState().drafts["Press"]).toBeUndefined();
    expect(h.store.getState().overrides["Press"]).toBeUndefined();
    const after = rowFor(container, "Press");
    expect(panelStatus(after)?.getAttribute("data-state")).toBe("source");
    expect(checkedChip(after).getAttribute("data-minutes")).toBe("90");
  });
});

describe("Remember for next time", () => {
  it("saves durable minutes and flips the chip to Remembered", async () => {
    const h = makeHarness("ready");
    const save = vi.spyOn(h.adapter, "saveDurationMemory").mockResolvedValue({
      identity: "todoist:6fx00xPRESS",
      minutes: 90,
      source: "remembered",
    });
    const { container } = h.ui(<Queue />);
    const row = expand(container, "Press");
    fireEvent.click(remember(row));
    expect(save).toHaveBeenCalledWith("todoist:6fx00xPRESS", 90);
    await waitFor(() => {
      const item = h.store.getState().inputs!.assigned.find((r) => r.id === "Press")!;
      expect(item.durationSource).toBe("remembered");
    });
    const after = rowFor(container, "Press");
    expect(remember(after).checked).toBe(true);
    await waitFor(() =>
      expect(panelStatus(after)?.getAttribute("data-state")).toBe("remembered"),
    );
  });

  it("is closed with a reason on a Capacities row, whose duration it does not own", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    const row = expand(container, "Note Processing");
    expect(remember(row).disabled).toBe(true);
    expect(remember(row).closest("label")?.getAttribute("title")).toContain("Capacities");
  });
});
