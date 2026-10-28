/* duration-ux.test.tsx — where the row's duration comes from, and Remember.
   Row redesign (2026-10-28): there are no one-tap chips, no staging state and
   no Save/Discard footer; an edit applies at once and the status chip says
   Today / Remembered / From source. Remember is a toggle BUTTON (aria-pressed),
   not a checkbox, and it is eligible for Capacities rows too (the backend
   accepts their canonical identity). Fixture adapters only — nothing here
   reaches a source. */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, waitFor } from "@testing-library/preact";
import { Queue } from "./Queue";
import { REMEMBER_TITLE_OFF } from "./DurationRemember";
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

const slider = (row: Element) =>
  row.querySelector<HTMLInputElement>('input[aria-label="Duration slider"]')!;
/** The status chip beside the collapsed row's slider readout. */
const status = (row: Element) =>
  row.querySelector<HTMLElement>(".dslider .dux-badge");
/** Remember: a toggle button, not a labeled checkbox. */
const remember = (row: Element) => row.querySelector<HTMLButtonElement>(".dux-mem")!;
const btn = (row: Element, text: string) =>
  Array.from(row.querySelectorAll<HTMLButtonElement>("button")).find(
    (b) => b.textContent?.trim() === text,
  ) ?? null;

const CAPACITIES = "Note Processing";
const CAPACITIES_IDENTITY = "capacities:fixture-space:custom-project:note-processing";
const PRESS_IDENTITY = "todoist:6fx00xPRESS";

describe("duration status chip", () => {
  it("reads From source, then Today the moment an edit applies", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    let row = expand(container, "Press");
    expect(status(row)?.getAttribute("data-state")).toBe("source");
    expect(status(row)?.textContent).toContain("From source");
    expect(row.textContent).not.toContain("Unsaved");

    fireEvent.input(slider(row), { target: { value: "120" } });
    row = rowFor(container, "Press");
    expect(h.store.getState().overrides["Press"]?.blocks).toBe(4);
    // Applied, not staged: the chip says Today, and there is no Save step.
    expect(status(row)?.getAttribute("data-state")).toBe("today");
    expect(status(row)?.textContent).toContain("Today");
    expect(row.textContent).not.toContain("Unsaved");
    expect(btn(row, "Save")).toBeNull();
    expect(btn(row, "Discard")).toBeNull();
  });
});

describe("Remember", () => {
  it("is a toggle button with aria-pressed, not a checkbox", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    const row = expand(container, "Press");
    const button = remember(row);
    expect(button.tagName).toBe("BUTTON");
    expect(button.getAttribute("aria-pressed")).toBe("false");
    expect(button.getAttribute("title")).toBe(REMEMBER_TITLE_OFF);
    expect(row.querySelector('.trow__panel input[type="checkbox"]')).toBeNull();
    expect(row.querySelector('.trow__panel input[role="switch"]')).toBeNull();
  });

  it("saves durable minutes on press and flips the chip to Remembered", async () => {
    const h = makeHarness("ready");
    const save = vi.spyOn(h.adapter, "saveDurationMemory").mockResolvedValue({
      identity: PRESS_IDENTITY,
      minutes: 90,
      source: "remembered",
    });
    const { container } = h.ui(<Queue />);
    const row = expand(container, "Press");
    fireEvent.click(remember(row));
    expect(save).toHaveBeenCalledWith(PRESS_IDENTITY, 90);
    await waitFor(() => {
      const item = h.store.getState().inputs!.assigned.find((r) => r.id === "Press")!;
      expect(item.durationSource).toBe("remembered");
    });
    const after = rowFor(container, "Press");
    expect(remember(after).getAttribute("aria-pressed")).toBe("true");
    await waitFor(() =>
      expect(status(after)?.getAttribute("data-state")).toBe("remembered"),
    );
  });

  it("presses off to forget (one reset call, no duration write)", async () => {
    const h = makeHarness("ready");
    vi.spyOn(h.adapter, "saveDurationMemory").mockResolvedValue({
      identity: PRESS_IDENTITY,
      minutes: 90,
      source: "remembered",
    });
    const reset = vi.spyOn(h.adapter, "resetDurationMemory").mockResolvedValue({
      identity: PRESS_IDENTITY,
      minutes: 90,
      source: "tag",
    });
    const { container } = h.ui(<Queue />);
    fireEvent.click(remember(expand(container, "Press")));
    await waitFor(() =>
      expect(remember(rowFor(container, "Press")).getAttribute("aria-pressed")).toBe(
        "true",
      ),
    );
    const saved = vi.mocked(h.adapter.saveDurationMemory).mock.calls.length;
    fireEvent.click(remember(rowFor(container, "Press")));
    expect(reset).toHaveBeenCalledWith(PRESS_IDENTITY);
    expect(vi.mocked(h.adapter.saveDurationMemory).mock.calls.length).toBe(saved);
    await waitFor(() =>
      expect(status(rowFor(container, "Press"))?.getAttribute("data-state")).toBe(
        "source",
      ),
    );
  });

  it("works for a Capacities row and sends its canonical identity", async () => {
    const h = makeHarness("ready");
    const save = vi.spyOn(h.adapter, "saveDurationMemory").mockResolvedValue({
      identity: CAPACITIES_IDENTITY,
      minutes: 30,
      source: "remembered",
    });
    const { container } = h.ui(<Queue />);
    const row = expand(container, CAPACITIES);
    // The removal of the all-day/Capacities exclusion also lifted Remember's.
    expect(remember(row).disabled).toBe(false);
    expect(remember(row).getAttribute("title")).toBe(REMEMBER_TITLE_OFF);
    fireEvent.click(remember(row));
    expect(save).toHaveBeenCalledWith(CAPACITIES_IDENTITY, 30);
    await waitFor(() => {
      const item = h.store.getState().inputs!.assigned.find((r) => r.id === CAPACITIES)!;
      expect(item.durationSource).toBe("remembered");
    });
    expect(remember(rowFor(container, CAPACITIES)).getAttribute("aria-pressed")).toBe(
      "true",
    );
  });
});
