/* row-expanded.test.tsx — task-actions F4b: the two-line expanded row panel.
   Fixture adapters only: no live adapter, no billed call, no source write. */

import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent } from "@testing-library/preact";

import { Queue } from "./Queue";
import { makeHarness, type Harness } from "./test-harness";
import { EXCLUSION_REASONS } from "../model/batchEligibility";

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

const radio = (row: Element, value: string) =>
  row.querySelector<HTMLInputElement>(`input[type="radio"][value="${value}"]`)!;
const duration = (row: Element) =>
  row.querySelector<HTMLSelectElement>('select[aria-label="Duration"]')!;
const mint = (row: Element) =>
  Array.from(row.querySelectorAll<HTMLInputElement>('input[type="checkbox"]')).find(
    (i) => i.closest("label")?.textContent?.includes("May overlap Mint"),
  )!;
const btn = (row: Element, text: string) =>
  Array.from(row.querySelectorAll<HTMLButtonElement>("button")).find(
    (b) => b.textContent?.trim() === text,
  ) ?? null;

describe("expanded row panel", () => {
  it("opens a draft and renders line 1 controls", () => {
    const { h, container } = setup();
    const row = expand(container, "Press");
    expect(h.store.getState().drafts["Press"]).toBeDefined();
    expect(radio(row, "time").checked).toBe(true);
    expect(duration(row).value).toBe("3");
    expect(row.textContent).toContain("Auto");
    expect(btn(row, "Set time")).not.toBeNull();
    expect(mint(row).disabled).toBe(false);
  });

  it("all-day round trip restores the duration and clears the pin", () => {
    const { h, container } = setup();
    let row = expand(container, "Press");
    h.controller.updateDraft("Press", (d) => ({ ...d, pin: "10:00" }));
    row = rowFor(container, "Press");
    fireEvent.click(radio(row, "allday"));
    row = rowFor(container, "Press");
    expect(duration(row).disabled).toBe(true);
    expect(h.store.getState().drafts["Press"].pin).toBeNull();
    fireEvent.click(radio(row, "time"));
    row = rowFor(container, "Press");
    const d = h.store.getState().drafts["Press"];
    expect(d.mode).toBe("time");
    expect(d.blocks).toBe(3);
    expect(d.pin).toBeNull();
    expect(duration(row).value).toBe("3");
    expect(row.textContent).toContain("Auto");
  });

  it("shows Save and Discard only while the draft is staged", () => {
    const { container } = setup();
    let row = expand(container, "Press");
    expect(btn(row, "Save")).toBeNull();
    expect(btn(row, "Discard")).toBeNull();
    fireEvent.change(duration(row), { target: { value: "4" } });
    row = rowFor(container, "Press");
    expect(btn(row, "Save")).not.toBeNull();
    expect(btn(row, "Discard")).not.toBeNull();
    fireEvent.click(btn(row, "Discard")!);
    row = rowFor(container, "Press");
    expect(btn(row, "Save")).toBeNull();
    expect(duration(row).value).toBe("3");
  });

  it("Save commits the staged duration", () => {
    const { h, container } = setup();
    let row = expand(container, "Press");
    fireEvent.change(duration(row), { target: { value: "4" } });
    row = rowFor(container, "Press");
    fireEvent.click(btn(row, "Save")!);
    expect(h.store.getState().overrides["Press"]?.blocks).toBe(4);
    expect(btn(rowFor(container, "Press"), "Save")).toBeNull();
  });

  it("gives the Mint toggle a reason only when disabled", () => {
    const { container } = setup((sc) => {
      sc.inputs.daySemantics.mintSessions = [
        { id: "Entryway Design", name: "Entryway Design", slot: "m", start: "09:00", end: "09:30" },
      ];
    });
    let row = expand(container, "Press");
    expect(mint(row).disabled).toBe(false);
    expect(row.querySelector('[id^="why-ov-"]')).toBeNull();
    fireEvent.click(radio(row, "allday"));
    row = rowFor(container, "Press");
    expect(mint(row).disabled).toBe(true);
    expect(row.textContent).toContain(EXCLUSION_REASONS.mintAllDay);
    const session = expand(container, "Entryway Design");
    expect(mint(session).disabled).toBe(true);
    expect(session.textContent).toContain(EXCLUSION_REASONS.mintSession);
  });

  it("disables all-day for a Capacities row with the batch reason", () => {
    const { container } = setup();
    const row = expand(container, "Note Processing");
    expect(radio(row, "allday").disabled).toBe(true);
    expect(radio(row, "time").disabled).toBe(false);
    expect(row.textContent).toContain(EXCLUSION_REASONS.allDayCapacities);
  });

  it("keeps the staged draft when the row is collapsed (store rule)", () => {
    const { h, container } = setup();
    const row = expand(container, "Press");
    fireEvent.change(duration(row), { target: { value: "4" } });
    fireEvent.click(rowFor(container, "Press").querySelector('button[aria-label="Collapse"]')!);
    expect(h.store.getState().drafts["Press"].blocks).toBe(4);
    expect(rowFor(container, "Press").textContent).toContain("Staged");
  });
});

export type { Harness };
