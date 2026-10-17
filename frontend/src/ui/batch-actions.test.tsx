/* batch-actions.test.tsx — task-actions F6: BatchBar and BatchPanel as
   standalone components over the real controller and FixtureAdapter. No live
   adapter, no billed call, no source write. Visual rendering is not covered:
   the components are unmounted until the integration slice. */

import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, waitFor } from "@testing-library/preact";

import { BatchBar, BatchPanel } from "./BatchActions";
import { makeHarness, type Harness } from "./test-harness";

afterEach(cleanup);

function mount(h: Harness) {
  return h.ui(
    <>
      <BatchBar />
      <BatchPanel />
    </>,
  );
}

function select(h: Harness, ids: string[]) {
  act(() => {
    for (const id of ids) h.controller.selectRow(id, true);
  });
}

function button(container: HTMLElement, name: string): HTMLButtonElement {
  const found = Array.from(container.querySelectorAll("button")).find(
    (b) => b.textContent?.trim() === name,
  );
  if (!found) throw new Error(`no button "${name}"`);
  return found as HTMLButtonElement;
}

function spies(h: Harness) {
  return {
    batch: vi.spyOn(h.adapter, "applyRuntimeBatch"),
    verb: vi.spyOn(h.adapter, "runtimeAction"),
    refresh: vi.spyOn(h.adapter, "refreshSources"),
  };
}

describe("BatchBar", () => {
  it("renders nothing with an empty selection and shows the count when selected", () => {
    const h = makeHarness("ready");
    const { container } = mount(h);
    expect(container.querySelector("[data-testid=batch-bar]")).toBeNull();

    select(h, ["Press", "Magic Mirror"]);

    expect(container.querySelector("[data-testid=batch-count]")?.textContent).toBe(
      "2 selected",
    );
  });

  it("Clear selection empties it and hides the bar", () => {
    const h = makeHarness("ready");
    const { container } = mount(h);
    select(h, ["Press"]);

    fireEvent.click(button(container, "Clear selection"));

    expect(h.store.getState().ui.selection).toEqual([]);
    expect(container.querySelector("[data-testid=batch-bar]")).toBeNull();
  });

  it("Select all selects every assigned row", () => {
    const h = makeHarness("ready");
    const { container } = mount(h);
    select(h, ["Press"]);

    fireEvent.click(container.querySelector<HTMLInputElement>(".batch-bar__all input")!);

    expect(h.store.getState().ui.selection.length).toBe(
      h.store.getState().inputs!.assigned.length,
    );
  });

  it("offers the batchable actions and never Delete or an exact start", () => {
    const h = makeHarness("ready");
    const { container } = mount(h);
    select(h, ["Press"]);

    const names = Array.from(container.querySelectorAll("button")).map((b) =>
      b.textContent?.trim(),
    );
    for (const want of [
      "Complete",
      "Skip today",
      "Unassign in source",
      "Hide always",
      "All-day",
      "Time block",
      "Automatic placement",
      "Allow Mint overlap",
      "Disallow Mint overlap",
    ]) {
      expect(names).toContain(want);
    }
    expect(container.querySelector("#batch-duration")).not.toBeNull();
    const text = container.textContent ?? "";
    expect(text).not.toMatch(/\bDelete\b(?! are)/);
    expect(container.querySelector("input[type=time]")).toBeNull();
    expect(names.join("|")).not.toMatch(/exact|start at|place at/i);
  });

  it("disables every control while a batch is applying", async () => {
    const h = makeHarness("ready");
    const { container } = mount(h);
    let release!: () => void;
    vi.spyOn(h.adapter, "applyRuntimeBatch").mockImplementation(
      () => new Promise((r) => (release = () => r({ batchId: "b", results: [] }))),
    );
    select(h, ["Press"]);
    fireEvent.click(button(container, "Complete"));
    await act(async () => {
      void h.controller.applyBatch();
    });

    await waitFor(() => expect(h.store.getState().batch.phase).toBe("applying"));
    const bar = container.querySelector<HTMLElement>("[data-testid=batch-bar]")!;
    for (const b of Array.from(bar.querySelectorAll("button, select, input"))) {
      expect((b as HTMLButtonElement).disabled).toBe(true);
    }
    expect(button(container, "Applying…").disabled).toBe(true);
    await act(async () => release());
  });
});

describe("source-changing review", () => {
  it("shows N of M and the excluded reasons before Apply, with zero server calls", () => {
    const h = makeHarness("ready");
    const s = spies(h);
    const { container } = mount(h);
    select(h, ["Press", "Note Processing"]);

    fireEvent.click(button(container, "Unassign in source"));

    const review = container.querySelector("[data-testid=batch-review]")!;
    expect(review.querySelector("[data-testid=batch-eligible]")?.textContent).toBe(
      "1 of 2 eligible",
    );
    expect(review.querySelector("[data-testid=batch-effects]")?.textContent).toContain(
      "Press",
    );
    expect(review.querySelector("[data-testid=batch-excluded]")?.textContent).toContain(
      "Note ProcessingCapacities rows cannot be unassigned in source",
    );
    expect(s.batch).not.toHaveBeenCalled();
    expect(s.verb).not.toHaveBeenCalled();
    expect(s.refresh).not.toHaveBeenCalled();
    expect(container.querySelector("[role=dialog]")).toBeNull();
  });

  it("honors the Day Setup gate for Skip today as a written reason", () => {
    // "fresh" leaves Day Setup unconfirmed.
    const h = makeHarness("fresh");
    expect(h.store.getState().daySetup.confirmed).toBe(false);
    const s = spies(h);
    const { container } = mount(h);
    select(h, ["Press"]);

    fireEvent.click(button(container, "Skip today"));

    expect(container.querySelector("[data-testid=batch-excluded]")?.textContent).toContain(
      "Confirm Day Setup first",
    );
    expect(button(container, "Apply to sources").disabled).toBe(true);
    expect(s.batch).not.toHaveBeenCalled();
  });

  it("moves focus to the review heading and Cancel sends nothing", async () => {
    const h = makeHarness("ready");
    const s = spies(h);
    const { container } = mount(h);
    select(h, ["Press"]);

    fireEvent.click(button(container, "Complete"));

    await waitFor(() =>
      expect(document.activeElement).toBe(container.querySelector(".batch-panel__h")),
    );
    fireEvent.click(button(container, "Cancel"));

    expect(h.store.getState().batch.phase).toBe("idle");
    expect(s.batch).not.toHaveBeenCalled();
    expect(document.activeElement).toBe(
      container.querySelector('[data-batch-action="complete"]'),
    );
  });

  it("Apply sends one batch and announces the outcome in the live region", async () => {
    const h = makeHarness("ready");
    const s = spies(h);
    const { container } = mount(h);
    select(h, ["Press", "Magic Mirror"]);
    fireEvent.click(button(container, "Complete"));

    fireEvent.click(button(container, "Apply to sources"));

    await waitFor(() => expect(h.store.getState().batch.phase).toBe("done"));
    expect(s.batch).toHaveBeenCalledTimes(1);
    const live = container.querySelector("[data-testid=batch-live]")!;
    expect(live.getAttribute("aria-live")).toBe("polite");
    expect(live.textContent).toBe("Complete: 2 done.");
    expect(container.querySelectorAll("[data-testid=batch-results] li").length).toBe(2);
  });
});

describe("results, retry, and undo", () => {
  async function applied(h: Harness, ids = ["Press", "Magic Mirror"]) {
    const view = mount(h);
    select(h, ids);
    fireEvent.click(button(view.container, "Complete"));
    fireEvent.click(button(view.container, "Apply to sources"));
    await waitFor(() => expect(h.store.getState().batch.phase).toBe("done"));
    return view;
  }

  it("keeps successes on partial failure and Retry failed re-runs only the failure", async () => {
    const h = makeHarness("ready");
    h.adapter.simulateBatchFailure(1);
    const { container } = await applied(h);

    const items = Array.from(container.querySelectorAll("[data-testid=batch-results] li"));
    expect(items.map((li) => li.getAttribute("data-status"))).toEqual(["applied", "failed"]);
    expect(items[1].textContent).toContain("forced failure");
    const id = h.store.getState().batch.id!;
    expect(h.adapter.batchExecutionCount(id)).toBe(2);

    h.adapter.simulateBatchFailure(null);
    fireEvent.click(button(container, "Retry failed (1)"));

    await waitFor(() =>
      expect(
        h.store.getState().batch.results.map((r) => r.status),
      ).toEqual(["applied", "applied"]),
    );
    expect(h.adapter.batchExecutionCount(id)).toBe(3);
    expect(container.textContent).not.toContain("Retry failed");
  });

  it("reports an undo conflict by name and does not claim a clean reversal", async () => {
    const h = makeHarness("ready");
    const { container } = await applied(h);
    h.adapter.simulateBatchUndoConflict(0);

    fireEvent.click(button(container, "Undo whole batch"));

    await waitFor(() => expect(h.store.getState().batch.undoReport).not.toBeNull());
    const report = container.querySelector("[data-testid=batch-undo-report]")!;
    expect(report.textContent).toContain("Reverted");
    expect(report.textContent).toContain("Magic Mirror");
    expect(report.textContent).toContain("Conflict");
    expect(report.textContent).toContain("Press");
    expect(report.textContent).toContain("forced undo conflict");
    expect(report.textContent).toContain("left as is");
  });

  it("reports non-reversible steps", async () => {
    const h = makeHarness("ready");
    const { container } = await applied(h);
    vi.spyOn(h.adapter, "undoRuntimeBatch").mockResolvedValue({
      batchId: h.store.getState().batch.id!,
      reverted: [],
      conflicts: [],
      nonReversible: [
        { item: 0, verb: "done", target: "todoist:6fx00xPRESS", reason: "no safe reverse" },
      ],
    });

    fireEvent.click(button(container, "Undo whole batch"));

    await waitFor(() => expect(h.store.getState().batch.undoReport).not.toBeNull());
    const report = container.querySelector("[data-testid=batch-undo-report]")!;
    expect(report.textContent).toContain("Not reversible");
    expect(report.textContent).toContain("Press");
    expect(report.textContent).toContain("no safe reverse");
  });

  it("renders results for a single-row action with an empty selection", async () => {
    const h = makeHarness("ready");
    const s = spies(h);
    const { container } = mount(h);
    expect(h.store.getState().ui.selection).toEqual([]);

    await act(async () => {
      await h.controller.runRowBatch("Press", "hideAlways");
    });

    expect(s.batch).toHaveBeenCalledTimes(1);
    expect(container.querySelector("[data-testid=batch-bar]")).toBeNull();
    const results = container.querySelector("[data-testid=batch-results]")!;
    expect(results.textContent).toContain("Press");
    expect(results.textContent).toContain("Done");
    expect(results.textContent).toContain("Hide always");
  });
});

describe("planning-only actions", () => {
  it("stage in today's plan with no source calls and accurate copy", () => {
    const h = makeHarness("ready");
    const s = spies(h);
    const { container } = mount(h);
    select(h, ["Press", "Charge GoPro"]);

    fireEvent.click(button(container, "All-day"));

    expect(s.batch).not.toHaveBeenCalled();
    expect(s.verb).not.toHaveBeenCalled();
    expect(s.refresh).not.toHaveBeenCalled();
    const panel = container.querySelector("[data-testid=batch-planning]")!;
    expect(panel.textContent).toContain("1 of 2 eligible");
    expect(panel.textContent).toContain("nothing was sent to Todoist or Capacities");
    expect(panel.textContent).toContain("Already all-day");
    expect(panel.textContent).not.toContain("Apply to sources");
    expect(h.store.getState().overrides.Press?.blocks).toBe(0);
  });

  it("duration choice stages the value without a server call", () => {
    const h = makeHarness("ready");
    const s = spies(h);
    const { container } = mount(h);
    select(h, ["Press"]);

    const sel = container.querySelector<HTMLSelectElement>("#batch-duration")!;
    sel.value = "2";
    fireEvent.change(sel);

    expect(s.batch).not.toHaveBeenCalled();
    expect(h.store.getState().overrides.Press?.blocks).toBe(2);
    expect(container.querySelector("[data-testid=batch-planning]")?.textContent).toContain(
      "60 min",
    );
  });
});
