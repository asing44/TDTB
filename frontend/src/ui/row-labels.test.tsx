/* row-labels.test.tsx — U11 row-labeling pass (2026-10-10). TDD for the new
   behavior: the source chip vocabulary, Capacities structure-title resolution
   (falling back to "Capacities item", never a UUID), the duration provenance
   dot, and the compact one-line time-movement note on fixed rows.

   Fixture adapters only: no live adapter, no billed call, no source write. */

import { afterEach, describe, expect, it } from "vitest";
import { cleanup, waitFor } from "@testing-library/preact";

afterEach(cleanup);

import { Queue, kindLabel, sourceChip } from "./Queue";
import { makeHarness } from "./test-harness";
import type { AssignedItem } from "../model/types";

// The ready fixture's machine-local structure-titles cache titles this id.
const PROJECT_STRUCTURE = "0d194525-c5a1-4af5-bb62-202b83006b5e";

function item(over: Partial<AssignedItem>): AssignedItem {
  return {
    id: "X",
    name: "X",
    path: null,
    source: "vault",
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
  it("labels todoist, capacities, and vault rows", () => {
    expect(sourceChip(item({ source: "todoist" }))).toEqual({
      key: "todoist",
      label: "TODOIST",
    });
    expect(sourceChip(item({ source: "capacities" }))).toEqual({
      key: "capacities",
      label: "CAPACITIES",
    });
    expect(sourceChip(item({ source: "vault" }))).toEqual({
      key: "vault",
      label: "VAULT",
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

  it("drops the redundant chip on todoist rows and keeps vault kinds", () => {
    expect(kindLabel(item({ source: "todoist", types: ["todoist"] }), {})).toBeNull();
    expect(kindLabel(item({ source: "vault", types: ["project"] }), {})).toBe("project");
  });
});

describe("rendered rows carry the clearer labels", () => {
  it("names the source as a chip on every row and drops the TASK badge", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    const chips = Array.from((container as HTMLElement).querySelectorAll(".qrow__src"));
    expect(chips.length).toBeGreaterThan(0);
    const labels = chips.map((c) => c.textContent);
    expect(labels).toContain("TODOIST");
    expect(labels).toContain("VAULT");
    // A todoist row carries the source chip but no redundant KIND chip.
    const todoistRow = rowNamed(container, "Review AWS module 4");
    expect(todoistRow.querySelector(".qrow__src--todoist")).toBeTruthy();
    expect(todoistRow.querySelector(".qrow__type")).toBeNull();
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

  it("speaks the duration provenance through the dot, not a pill", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    const dot = container.querySelector(".qrow__dur-src") as HTMLElement;
    expect(dot).toBeTruthy();
    expect(dot.getAttribute("aria-label")).toMatch(/Duration:/);
    expect(dot.textContent).toBe("");
  });

  it("renders the fixed-row permission as one compact line, not a one-char column", () => {
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
    const { container, getByLabelText } = h.ui(<Queue />);
    const note = rowNamed(container, "M1.5").querySelector(
      ".qrow__time-adjustment-note",
    ) as HTMLElement;
    expect(note.textContent).toContain("Protected");
    expect(note.textContent).toContain("Allow move");
    // The full sentence stays on the control for assistive tech.
    const box = getByLabelText(
      "Allow scheduler to move existing time for M1.5",
    ) as HTMLInputElement;
    expect(box.type).toBe("checkbox");
  });
});
