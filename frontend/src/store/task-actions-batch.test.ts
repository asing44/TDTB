/* task-actions-batch.test.ts — task-actions F3b: the one batch surface.

   FixtureAdapter only. What's asserted here is the FRONTEND contract:

   - planning-only actions (duration, all-day, time block, automatic placement,
     Mint allow/disallow) reshape the session immediately and NEVER call the
     adapter;
   - source-changing actions (Complete, Unassign/Skip occurrence, Hide always,
     Skip today) stop at review — the exact effect and the excluded names are
     shown — and send nothing until applyBatch;
   - applyBatch is ONE `applyRuntimeBatch` call whose per-item results are
     kept, so a forced partial failure keeps its successes and retryFailed
     re-runs only the failures;
   - undoBatch reports reverted items and later-edit conflicts;
   - a single Complete/Unassign/Hide always from a row runs as a batch of one,
     through the same results/undo surface.

   Server-side batch behavior is backend-tested in test_runtime_batches_api.py. */

import { describe, expect, it, vi } from "vitest";
import { createStore } from "./createStore";
import { Controller } from "./controller";
import { FixtureAdapter } from "../adapters/fixture";
import { emptyBatch } from "./store";
import type { ScenarioName } from "../fixtures/scenarios";

/** Loaded "ready" scenario with Day Setup confirmed (the server verbs' gate). */
async function harness(opts: { recurring?: boolean; scenario?: ScenarioName } = {}) {
  const store = createStore();
  const adapter = new FixtureAdapter(opts.scenario ?? "ready");
  if (opts.recurring) {
    // "Press" becomes a recurring commitment, so Unassign must resolve to
    // Skip occurrence for it.
    const real = adapter.loadPlanInputs.bind(adapter);
    vi.spyOn(adapter, "loadPlanInputs").mockImplementation(async () => {
      const plan = await real();
      return {
        ...plan,
        assigned: plan.assigned.map((item) =>
          item.id === "Press" ? { ...item, isRecurring: true } : item,
        ),
      };
    });
  }
  const controller = new Controller(adapter, store.dispatch, store.getState);
  await controller.load();
  store.dispatch({
    type: "SETUP_SAVED",
    daySetup: { ...store.getState().daySetup, confirmed: true },
  });
  return { store, adapter, controller };
}

function select(controller: Controller, ids: string[]) {
  for (const id of ids) controller.selectRow(id, true);
}

describe("task-actions planning-only batch", () => {
  it("all-day applies to the session immediately with no adapter call", async () => {
    const { store, adapter, controller } = await harness();
    const batch = vi.spyOn(adapter, "applyRuntimeBatch");
    const verb = vi.spyOn(adapter, "runtimeAction");
    select(controller, ["Press", "Magic Mirror"]);

    controller.reviewBatch("allDay");

    expect(batch).not.toHaveBeenCalled();
    expect(verb).not.toHaveBeenCalled();
    const state = store.getState();
    expect(state.batch.phase).toBe("done");
    expect(state.batch.review?.label).toBe("2 of 2 eligible");
    expect(state.batch.review?.effects).toBe("Make all-day 2 rows");
    expect(state.overrides.Press).toEqual({ included: true, blocks: 0, blocksBeforeAllDay: 3 });
    expect(state.overrides["Magic Mirror"]).toEqual({
      included: true,
      blocks: 0,
      blocksBeforeAllDay: 3,
    });
  });

  it("duration sets bytes of session shaping only, and names the value", async () => {
    const { store, adapter, controller } = await harness();
    const batch = vi.spyOn(adapter, "applyRuntimeBatch");
    select(controller, ["Press"]);

    controller.reviewBatch("duration", { blocks: 2 });

    expect(batch).not.toHaveBeenCalled();
    expect(store.getState().overrides.Press).toEqual({ included: true, blocks: 2 });
    expect(store.getState().batch.review?.effects).toBe("Set the duration of 1 row (60 min)");
  });

  it("time block restores the duration the all-day switch captured, with no pin", async () => {
    const { store, adapter, controller } = await harness();
    const batch = vi.spyOn(adapter, "applyRuntimeBatch");
    const verb = vi.spyOn(adapter, "runtimeAction");
    controller.placeRow("Press", "10:00");
    select(controller, ["Press"]);

    controller.reviewBatch("allDay");
    expect(store.getState().overrides.Press).toEqual({
      included: true,
      blocks: 0,
      blocksBeforeAllDay: 3,
    });
    // All-day is a session reshape: the reducer drops the start with it.
    expect(store.getState().placements.Press).toBeUndefined();

    controller.reviewBatch("timeBlock");

    expect(batch).not.toHaveBeenCalled();
    expect(verb).not.toHaveBeenCalled();
    // The captured duration comes back; the retired pin never does.
    expect(store.getState().overrides.Press).toEqual({ included: true, blocks: 3 });
    expect(store.getState().placements.Press).toBeUndefined();
    expect(store.getState().batch.review?.effects).toBe("Make a time block of 1 row");
  });

  it("honors eligibility: an all-day row is excluded with the written reason", async () => {
    const { store, adapter, controller } = await harness();
    const batch = vi.spyOn(adapter, "applyRuntimeBatch");
    // "Charge GoPro" is already all-day (0 blocks).
    select(controller, ["Press", "Charge GoPro"]);

    controller.reviewBatch("allDay");

    const review = store.getState().batch.review!;
    expect(batch).not.toHaveBeenCalled();
    expect(review.label).toBe("1 of 2 eligible");
    expect(review.excluded).toEqual([
      { id: "Charge GoPro", name: "Charge GoPro", reason: "Already all-day" },
    ]);
    expect(store.getState().overrides["Charge GoPro"]).toBeUndefined();
    expect(store.getState().overrides.Press?.blocks).toBe(0);
  });
});

describe("task-actions source-changing batch", () => {
  it("stops at review and sends one batch only on applyBatch", async () => {
    const { store, adapter, controller } = await harness();
    const batch = vi.spyOn(adapter, "applyRuntimeBatch");
    const refresh = vi.spyOn(adapter, "refreshSources");
    select(controller, ["Press", "Magic Mirror"]);

    controller.reviewBatch("complete");
    expect(batch).not.toHaveBeenCalled();
    expect(refresh).not.toHaveBeenCalled();
    expect(store.getState().batch.phase).toBe("review");
    expect(store.getState().batch.review?.effects).toBe("Complete 2 rows");
    expect(store.getState().batch.review?.label).toBe("2 of 2 eligible");

    await controller.applyBatch();

    expect(batch).toHaveBeenCalledTimes(1);
    expect(batch.mock.calls[0][0]).toEqual([
      { verb: "done", target: "todoist:6fx00xPRESS", args: {} },
      { verb: "done", target: "todoist:6fx00xMIRROR", args: {} },
    ]);
    const state = store.getState();
    expect(state.batch.phase).toBe("done");
    expect(state.batch.id).toBe("rb-fixture-1");
    expect(state.batch.results.map((r) => r.status)).toEqual(["applied", "applied"]);
    expect(state.batch.results.map((r) => r.id)).toEqual(["Press", "Magic Mirror"]);
    expect(refresh).toHaveBeenCalledTimes(1);
  });

  it("shows N of M and the exact excluded reasons before Apply", async () => {
    const { store, adapter, controller } = await harness();
    const batch = vi.spyOn(adapter, "applyRuntimeBatch");
    // A Capacities row cannot be unassigned in source.
    select(controller, ["Press", "Note Processing"]);

    controller.reviewBatch("unassign");

    const review = store.getState().batch.review!;
    expect(batch).not.toHaveBeenCalled();
    expect(review.label).toBe("1 of 2 eligible");
    expect(review.counts).toEqual({ eligible: 1, excluded: 1, total: 2 });
    expect(review.excluded).toEqual([
      {
        id: "Note Processing",
        name: "Note Processing",
        reason: "Capacities rows cannot be unassigned in source",
      },
    ]);
  });

  it("resolves a recurring Unassign to skip_occurrence", async () => {
    const { adapter, controller } = await harness({ recurring: true });
    const batch = vi.spyOn(adapter, "applyRuntimeBatch");
    select(controller, ["Press"]);

    controller.reviewBatch("unassign");
    await controller.applyBatch();

    expect(batch.mock.calls[0][0]).toEqual([
      { verb: "skip_occurrence", target: "todoist:6fx00xPRESS", args: {} },
    ]);
  });

  it("keeps successes on a forced partial failure and retries only the failure", async () => {
    const { store, adapter, controller } = await harness();
    adapter.simulateBatchFailure(1);
    select(controller, ["Press", "Magic Mirror"]);

    controller.reviewBatch("complete");
    await controller.applyBatch();

    const applied = store.getState().batch;
    expect(applied.results.map((r) => r.status)).toEqual(["applied", "failed"]);
    expect(applied.results[1].error).toContain("forced failure");
    const batchId = applied.id!;
    expect(adapter.batchExecutionCount(batchId)).toBe(2); // both items on apply

    // The transient cause clears; the retry must now settle only the failure.
    adapter.simulateBatchFailure(null);
    await controller.retryFailed();

    const retried = store.getState().batch;
    expect(retried.id).toBe(batchId);
    expect(retried.results.map((r) => r.status)).toEqual(["applied", "applied"]);
    // One more execution, not two: the earlier success was never re-run.
    expect(adapter.batchExecutionCount(batchId)).toBe(3);
  });

  it("reports undo conflicts instead of claiming a clean reversal", async () => {
    const { store, adapter, controller } = await harness();
    select(controller, ["Press", "Magic Mirror"]);
    controller.reviewBatch("complete");
    await controller.applyBatch();
    adapter.simulateBatchUndoConflict(0);

    await controller.undoBatch();

    const report = store.getState().batch.undoReport!;
    expect(report.reverted).toEqual(["todoist:6fx00xMIRROR"]);
    expect(report.conflicts).toEqual([
      { target: "todoist:6fx00xPRESS", reason: "fixture: forced undo conflict on item 0" },
    ]);
    expect(report.nonReversible).toEqual([]);
  });

  it("reports steps with no safe reverse as non_reversible", async () => {
    const { store, adapter, controller } = await harness();
    select(controller, ["Press", "Magic Mirror"]);
    controller.reviewBatch("complete");
    await controller.applyBatch();
    vi.spyOn(adapter, "undoRuntimeBatch").mockResolvedValue({
      batchId: store.getState().batch.id!,
      reverted: [
        {
          item: 1,
          verb: "done",
          target: "todoist:6fx00xMIRROR",
          actionId: "ra-fixture-batch-1-1",
        },
      ],
      conflicts: [],
      nonReversible: [
        {
          item: 0,
          verb: "done",
          target: "todoist:6fx00xPRESS",
          reason: "fixture: done has no safe reverse",
        },
      ],
    });

    await controller.undoBatch();

    const report = store.getState().batch.undoReport!;
    expect(report.reverted).toEqual(["todoist:6fx00xMIRROR"]);
    expect(report.conflicts).toEqual([]);
    expect(report.nonReversible).toEqual([
      { target: "todoist:6fx00xPRESS", reason: "fixture: done has no safe reverse" },
    ]);
  });

  it("runs a single row action as a batch of one through the same surface", async () => {
    const { store, adapter, controller } = await harness();
    const batch = vi.spyOn(adapter, "applyRuntimeBatch");

    await controller.runRowBatch("Press", "hideAlways");

    expect(batch).toHaveBeenCalledTimes(1);
    expect(batch.mock.calls[0][0]).toEqual([
      { verb: "hide_always", target: "todoist:6fx00xPRESS", args: {} },
    ]);
    const state = store.getState();
    expect(state.batch.phase).toBe("done");
    expect(state.batch.id).toBe("rb-fixture-1");
    expect(state.batch.results).toHaveLength(1);
    expect(state.batch.results[0]).toMatchObject({
      id: "Press",
      verb: "hide_always",
      status: "applied",
    });
  });

  it("cancelReview drops the pending review without sending anything", async () => {
    const { store, adapter, controller } = await harness();
    const batch = vi.spyOn(adapter, "applyRuntimeBatch");
    select(controller, ["Press"]);
    controller.reviewBatch("complete");
    expect(store.getState().batch.phase).toBe("review");

    controller.cancelReview();

    expect(batch).not.toHaveBeenCalled();
    expect(store.getState().batch).toEqual(emptyBatch);
  });
});
