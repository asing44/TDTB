/* row-labels.test.tsx — U11 row-labeling pass (2026-10-10). TDD for the new
   behavior: the source chip vocabulary, Capacities structure-title resolution
   (falling back to "Capacities item", never a UUID), the duration provenance
   text label, and the compact one-line time-movement note on fixed rows.

   Fixture adapters only: no live adapter, no billed call, no source write. */

import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, waitFor } from "@testing-library/preact";

afterEach(cleanup);

import { Queue, kindLabel, sourceChip } from "./Queue";
import { projectAssigned } from "../adapters/wire";
import { makeHarness } from "./test-harness";
import type { AssignedItem } from "../model/types";

// The ready fixture's machine-local structure-titles cache titles this id.
const PROJECT_STRUCTURE = "0d194525-c5a1-4af5-bb62-202b83006b5e";

function item(over: Partial<AssignedItem>): AssignedItem {
  return {
    id: "X",
    name: "X",
    path: null,
    source: "todoist",
    types: [],
    urgency: null,
    deadline: null,
    priorityScore: 0,
    blocks: 1,
    durationLabel: "30min",
    todoistId: null,
    ...over,
  };
}

function rowNamed(container: Element, name: string): HTMLElement {
  const row = Array.from(
    container.querySelectorAll<HTMLElement>(".qrow"),
  ).find((r) => r.querySelector(".qrow__name")?.textContent === name);
  if (!row) throw new Error(`no row named ${name}`);
  return row;
}

describe("sourceChip: the provenance vocabulary", () => {
  it("labels todoist and capacities rows; anything else is a neutral Other", () => {
    expect(sourceChip(item({ source: "todoist" }))).toEqual({
      key: "todoist",
      label: "Todoist",
    });
    expect(sourceChip(item({ source: "capacities" }))).toEqual({
      key: "capacities",
      label: "Capacities",
    });
    expect(sourceChip(item({ source: "calendar" }))).toEqual({
      key: "calendar",
      label: "Calendar",
    });
    expect(sourceChip(item({ source: "legacy" as never }))).toEqual({
      key: "other",
      label: "Other",
    });
  });
});

describe("kindLabel: human KIND, never a raw structure id", () => {
  it("resolves a Capacities structure id through the titles cache", () => {
    const row = item({ source: "capacities", types: [PROJECT_STRUCTURE] });
    expect(kindLabel(row, { [PROJECT_STRUCTURE]: "Project" })).toBe("Project");
  });

  it("falls back to 'Capacities item' when the structure has no title", () => {
    const row = item({ source: "capacities", types: ["RootTask"] });
    expect(kindLabel(row, {})).toBe("Capacities item");
    expect(kindLabel(row, null)).toBe("Capacities item");
  });

  it("drops the redundant chip on todoist rows", () => {
    expect(kindLabel(item({ source: "todoist", types: ["todoist"] }), {})).toBeNull();
  });
});

describe("rendered rows carry the clearer labels", () => {
  it("names the source as a chip on every row and drops the TASK badge", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    const chips = Array.from((container as HTMLElement).querySelectorAll(".qrow__src"));
    expect(chips.length).toBeGreaterThan(0);
    const labels = chips.map((c) => c.textContent);
    expect(labels).toContain("Todoist");
    expect(labels).toContain("Capacities");
    expect(labels).not.toContain("Other");
    // A todoist row carries the source chip but no redundant KIND chip.
    const todoistRow = rowNamed(container, "Review AWS module 4");
    expect(todoistRow.querySelector(".qrow__src--todoist")).toBeTruthy();
    expect(todoistRow.querySelector(".qrow__type")).toBeNull();
  });

  it("renders a projected calendar row with the CALENDAR chip", () => {
    // Unit 3: the chip mapping already knew CALENDAR, but a row reached it as
    // an unidentified Todoist-like row until projectAssigned kept the source.
    const h = makeHarness("ready");
    const inputs = structuredClone(h.store.getState().inputs!);
    inputs.assigned.push(
      projectAssigned({
        name: "Team offsite",
        source: "calendar",
        assigned: true,
        blocks: 1,
        identity: "calendar:ev-1:2026-10-13",
        path: "calendar://ev-1/2026-10-13",
        event_id: "ev-1",
        calendar_id: "cal-1",
        calendar_title: "Evan & Nate",
      }),
    );
    h.store.dispatch({ type: "INPUTS_LOADED", inputs, ledger: { ...h.scenario.ledger } });
    const { container } = h.ui(<Queue />);
    const row = rowNamed(container, "Team offsite");
    expect(row.querySelector(".qrow__src--calendar")?.textContent).toBe("Calendar");
    // A calendar row carries no Capacities-style KIND chip.
    expect(row.querySelector(".qrow__type")).toBeNull();
  });

  it("resolves a Capacities structure title from the machine-local cache", async () => {
    const h = makeHarness("ready");
    const inputs = structuredClone(h.store.getState().inputs!);
    inputs.assigned.push(
      item({
        id: "Cap project",
        name: "Cap project",
        source: "capacities",
        types: [PROJECT_STRUCTURE],
      }),
      item({
        id: "Cap unknown",
        name: "Cap unknown",
        source: "capacities",
        types: ["RootTask"],
      }),
    );
    h.store.dispatch({
      type: "INPUTS_LOADED",
      inputs,
      ledger: { ...h.scenario.ledger },
    });
    const { container } = h.ui(<Queue />);
    await waitFor(() => {
      expect(rowNamed(container, "Cap project").querySelector(".qrow__type")?.textContent).toBe(
        "Project",
      );
    });
    const unknown = rowNamed(container, "Cap unknown");
    expect(unknown.querySelector(".qrow__type")?.textContent).toBe("Capacities item");
    expect(unknown.textContent).not.toMatch(/RootTask/);
  });

  it("renders no duration label on source (default) rows", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    expect(container.querySelector(".qrow__dur-label")).toBeNull();
    expect(container.querySelector(".qrow__dur-src")).toBeNull();
  });

  it("carries the fixed-row permission in the time editor, not on the row", () => {
    const h = makeHarness("ready");
    const inputs = structuredClone(h.store.getState().inputs!);
    const base = inputs.assigned.find((i) => i.source === "todoist")!;
    inputs.assigned.push({
      ...base,
      id: "M1.5",
      name: "M1.5",
      todoistId: "m15",
      isRecurring: true,
      scheduledStart: "05:15",
      blocks: 1,
      durationLabel: "30min",
    });
    h.store.dispatch({
      type: "INPUTS_LOADED",
      inputs,
      ledger: { ...h.scenario.ledger },
    });
    const { container, getByText } = h.ui(<Queue />);
    const row = () => rowNamed(container, "M1.5");
    // No inline checkbox or label is left on the collapsed row, and F7 removed
    // the More menu that used to carry the permission.
    expect(row().querySelector(".qrow__time-adjustment")).toBeNull();
    expect(row().querySelector(".qrow__time-adjustment-note")).toBeNull();
    expect(row().textContent).not.toContain("Allow move");
    expect(container.querySelector('button[aria-haspopup="menu"]')).toBeNull();
    // The permission is the expanded row's time-editor checkbox; Apply
    // commits it at once (there is no Save step).
    fireEvent.click(row().querySelector('button[aria-label="Expand"]')!);
    fireEvent.click(getByText("Set time"));
    const control = row().querySelector<HTMLInputElement>(
      '.trow__editor input[type="checkbox"]',
    )!;
    expect(control.checked).toBe(false);
    fireEvent.click(control);
    fireEvent.click(getByText("Apply"));
    expect(h.store.getState().timeAdjustmentOptIns["M1.5"]).toBe(true);
  });
});
