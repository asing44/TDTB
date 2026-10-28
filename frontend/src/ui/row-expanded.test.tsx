/* row-expanded.test.tsx — the expanded row panel after the row redesign.
   Every control in the panel is LOCAL day state applied the moment it changes
   through controller.applyRowEdit: there is no Save/Discard footer, no staged
   chip and no duration chip strip. The panel carries the exact-minutes field,
   the All-day toggle, Remember, the Start control and the Source menu; the
   permanent May-overlap-Mint toggle lives on the collapsed row.
   Fixture adapters only: no live adapter, no billed call, no source write. */

import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent } from "@testing-library/preact";

import { Queue } from "./Queue";
import { makeHarness, type Harness } from "./test-harness";
import { EXCLUSION_REASONS } from "../model/batchEligibility";
import { effectiveBlocks } from "../store/store";

afterEach(cleanup);

function setup(mutate?: Parameters<typeof makeHarness>[1]) {
  const h = makeHarness("ready", mutate);
  const r = h.ui(<Queue />);
  return { h, container: r.container as HTMLElement };
}

function rowFor(container: Element, name: string): HTMLElement {
  const row = Array.from(container.querySelectorAll<HTMLElement>(".qrow")).find(
    (r) => r.querySelector(".qrow__name")?.textContent === name,
  );
  if (!row) throw new Error(`no row named ${name}`);
  return row;
}

function expand(container: Element, name: string): HTMLElement {
  const row = rowFor(container, name);
  fireEvent.click(row.querySelector('button[aria-label="Expand"]')!);
  return rowFor(container, name);
}

/** The panel's exact-minutes field: the only typed duration control. */
const exact = (row: Element) =>
  row.querySelector<HTMLInputElement>('input[aria-label="Exact minutes"]')!;
/** The All-day mode toggle (a pressed button, not a radio). */
const allDayToggle = (row: Element) =>
  Array.from(row.querySelectorAll<HTMLButtonElement>(".trow__tog")).find(
    (b) => b.textContent?.trim() === "All-day",
  )!;
const remember = (row: Element) =>
  row.querySelector<HTMLButtonElement>(".dux-mem")!;
/** The permanent May-overlap-Mint toggle, on the COLLAPSED row. */
const mint = (row: Element) =>
  row.querySelector<HTMLButtonElement>('button[aria-label^="May overlap Mint"]')!;
const btn = (row: Element, text: string) =>
  Array.from(row.querySelectorAll<HTMLButtonElement>("button")).find(
    (b) => b.textContent?.trim() === text,
  ) ?? null;

describe("expanded row panel", () => {
  it("renders line 1 controls without a Save/Discard step", () => {
    const { container } = setup();
    const row = expand(container, "Press");
    expect(exact(row)).not.toBeNull();
    expect(exact(row).value).toBe("");
    expect(exact(row).placeholder).toBe("Exact, e.g. 25m");
    expect(allDayToggle(row).getAttribute("aria-pressed")).toBe("false");
    expect(remember(row).getAttribute("aria-pressed")).toBe("false");
    expect(row.textContent).toContain("Auto");
    expect(btn(row, "Set time")).not.toBeNull();
    // The two shipped Save/Discard controls are gone for good.
    expect(btn(row, "Save")).toBeNull();
    expect(btn(row, "Discard")).toBeNull();
  });

  it("applies a typed exact duration and offers no staged state", () => {
    const { h, container } = setup();
    const row = expand(container, "Press");
    fireEvent.input(exact(row), { target: { value: "2h" } });
    // Enter flushes the debounce immediately.
    fireEvent.keyDown(exact(rowFor(container, "Press")), { key: "Enter" });
    expect(effectiveBlocks(h.store.getState(), "Press")).toBe(4);
    expect(h.store.getState().overrides["Press"]?.blocks).toBe(4);
    // The duration chip says where the value came from; nothing is staged.
    expect(rowFor(container, "Press").querySelector(".dux-badge")?.textContent).toContain(
      "Today",
    );
    expect(rowFor(container, "Press").textContent).not.toContain("Unsaved");
    expect(rowFor(container, "Press").querySelector(".trow__state--staged")).toBeNull();
  });

  it("all-day round trip restores the duration and clears the pin", () => {
    const { h, container } = setup();
    let row = expand(container, "Press");
    h.controller.applyRowEdit("Press", (d) => ({ ...d, pin: "10:00" }));
    row = rowFor(container, "Press");
    expect(h.store.getState().placements["Press"]).toBe("10:00");

    fireEvent.click(allDayToggle(row));
    row = rowFor(container, "Press");
    expect(allDayToggle(row).getAttribute("aria-pressed")).toBe("true");
    expect(effectiveBlocks(h.store.getState(), "Press")).toBe(0);
    expect(h.store.getState().placements["Press"] ?? null).toBeNull();

    fireEvent.click(allDayToggle(row));
    row = rowFor(container, "Press");
    expect(allDayToggle(row).getAttribute("aria-pressed")).toBe("false");
    // The All-day round trip restores the captured duration, never the pin.
    expect(effectiveBlocks(h.store.getState(), "Press")).toBe(3);
    expect(h.store.getState().placements["Press"] ?? null).toBeNull();
    expect(row.textContent).toContain("Auto");
  });

  it("gives the Mint toggle a reason only when disabled", () => {
    const { container } = setup((sc) => {
      sc.inputs.daySemantics.mintSessions = [
        { id: "Entryway Design", name: "Entryway Design", slot: "m", start: "09:00", end: "09:30" },
      ];
    });
    let row = rowFor(container, "Press");
    expect(mint(row).disabled).toBe(false);
    expect(mint(row).getAttribute("title")).not.toContain(EXCLUSION_REASONS.mintAllDay);

    const expanded = expand(container, "Press");
    fireEvent.click(allDayToggle(expanded));
    row = rowFor(container, "Press");
    expect(mint(row).disabled).toBe(true);
    expect(mint(row).getAttribute("title")).toBe(EXCLUSION_REASONS.mintAllDay);

    // A Mint session cannot itself overlap Mint.
    const session = rowFor(container, "Entryway Design");
    expect(mint(session).disabled).toBe(true);
    expect(mint(session).getAttribute("title")).toBe(EXCLUSION_REASONS.mintSession);
  });

  it("allows All-day on a Capacities row, as a local today-only state", () => {
    const { h, container } = setup();
    const row = expand(container, "Note Processing");
    const toggle = allDayToggle(row);
    expect(toggle.disabled).toBe(false);
    expect(toggle.getAttribute("aria-pressed")).toBe("false");
    expect(toggle.getAttribute("title")).toContain("Local to today's plan");

    fireEvent.click(toggle);
    expect(effectiveBlocks(h.store.getState(), "Note Processing")).toBe(0);
    expect(
      allDayToggle(rowFor(container, "Note Processing")).getAttribute("aria-pressed"),
    ).toBe("true");
  });

  it("locks All-day only for a protected Todoist native time", () => {
    const { container } = setup((sc) => {
      const press = sc.inputs.assigned.find((i) => i.id === "Press")!;
      press.scheduledStart = "19:00";
    });
    const row = expand(container, "Press");
    expect(allDayToggle(row).disabled).toBe(true);
    expect(allDayToggle(row).getAttribute("title")).toBe(
      EXCLUSION_REASONS.protectedAllDay,
    );
    expect(row.textContent).toContain(EXCLUSION_REASONS.protectedAllDay);
  });

  it("keeps the applied duration when the row is collapsed", () => {
    const { h, container } = setup();
    let row = expand(container, "Press");
    fireEvent.input(exact(row), { target: { value: "2h" } });
    fireEvent.keyDown(exact(rowFor(container, "Press")), { key: "Enter" });
    expect(effectiveBlocks(h.store.getState(), "Press")).toBe(4);
    fireEvent.click(
      rowFor(container, "Press").querySelector('button[aria-label="Collapse"]')!,
    );
    row = rowFor(container, "Press");
    expect(effectiveBlocks(h.store.getState(), "Press")).toBe(4);
    expect(row.querySelector(".dux-badge")?.textContent).toContain("Today");
  });
});

export type { Harness };
