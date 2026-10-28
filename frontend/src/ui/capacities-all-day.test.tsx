/* capacities-all-day.test.tsx — a Capacities row may go all-day.
   Row redesign (2026-10-28): the `allDayCapacities` exclusion is gone. All-day
   is a LOCAL plan state (a today-override with blocks === 0) for every row
   except a protected Todoist native time, and a Capacities all-day never writes
   to the Capacities source. The control is the expanded row's aria-pressed
   All-day toggle, which applies at once (no Save step). Fixture adapters only. */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent } from "@testing-library/preact";

afterEach(cleanup);

import { Queue } from "./Queue";
import { makeHarness } from "./test-harness";
import { effectiveBlocks } from "../store/store";

const CAPACITIES = "Note Processing";
const CAPACITIES_IDENTITY = "capacities:fixture-space:custom-project:note-processing";

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

const allDay = (row: Element) =>
  Array.from(row.querySelectorAll<HTMLButtonElement>("button")).find(
    (b) => b.textContent?.trim() === "All-day",
  )!;

/** Every path a Capacities all-day must NOT take. */
function writeSpies(h: ReturnType<typeof makeHarness>) {
  return {
    live: vi.spyOn(h.adapter, "liveCommit"),
    shadow: vi.spyOn(h.adapter, "shadowCommit"),
    sequence: vi.spyOn(h.adapter, "autoSequence"),
    batch: vi.spyOn(h.adapter, "applyRuntimeBatch"),
    saveMemory: vi.spyOn(h.adapter, "saveDurationMemory"),
    resetMemory: vi.spyOn(h.adapter, "resetDurationMemory"),
  };
}

describe("Capacities all-day", () => {
  it("turns on through the All-day toggle as a local blocks-0 override", async () => {
    const h = makeHarness("ready");
    const spies = writeSpies(h);
    const { container } = h.ui(<Queue />);
    const row = expand(container, CAPACITIES);
    expect(allDay(row).getAttribute("aria-pressed")).toBe("false");
    expect(allDay(row).disabled).toBe(false);

    fireEvent.click(allDay(row));

    const after = rowFor(container, CAPACITIES);
    expect(effectiveBlocks(h.store.getState(), CAPACITIES)).toBe(0);
    expect(h.store.getState().overrides[CAPACITIES]).toMatchObject({
      included: true,
      blocks: 0,
      blocksBeforeAllDay: 1,
    });
    expect(allDay(after).getAttribute("aria-pressed")).toBe("true");
    expect(after.classList.contains("qrow--background")).toBe(true);
    expect(after.querySelector(".trow__state")?.textContent).toBe("All-day");
    expect(after.querySelector(".dslider").getAttribute("title")).toBe("Duration All day");

    // Local only: the Capacities source value is untouched and nothing is sent.
    const source = h.store.getState().inputs!.assigned.find((i) => i.id === CAPACITIES)!;
    expect(source.blocks).toBe(1);
    await new Promise((resolve) => setTimeout(resolve, 150));
    for (const spy of Object.values(spies)) expect(spy).not.toHaveBeenCalled();
  });

  it("turns back off, restoring the Capacities estimate", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);

    let row = expand(container, CAPACITIES);
    fireEvent.click(allDay(row));
    expect(effectiveBlocks(h.store.getState(), CAPACITIES)).toBe(0);

    row = rowFor(container, CAPACITIES);
    fireEvent.click(allDay(row));

    expect(effectiveBlocks(h.store.getState(), CAPACITIES)).toBe(1);
    expect(allDay(rowFor(container, CAPACITIES)).getAttribute("aria-pressed")).toBe(
      "false",
    );
    expect(
      rowFor(container, CAPACITIES).classList.contains("qrow--background"),
    ).toBe(false);
  });

  it("also goes all-day from the slider's left-end stop", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    const row = expand(container, CAPACITIES);
    const slider = row.querySelector<HTMLInputElement>(
      'input[aria-label="Duration slider"]',
    )!;
    fireEvent.keyDown(slider, { key: "Home" });
    expect(effectiveBlocks(h.store.getState(), CAPACITIES)).toBe(0);
    expect(allDay(rowFor(container, CAPACITIES)).getAttribute("aria-pressed")).toBe(
      "true",
    );
  });

  it("keeps the row's canonical identity for any later durable action", () => {
    const h = makeHarness("ready");
    const items = h.store.getState().inputs!.assigned;
    const item = items.find((i) => i.id === CAPACITIES)!;
    expect(item.identity).toBe(CAPACITIES_IDENTITY);
  });
});
