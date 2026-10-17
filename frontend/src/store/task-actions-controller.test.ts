/* task-actions-controller.test.ts — task-actions F3: the row UI state
   (single expansion, selection) and the three journaled row verbs
   (Skip today / Restore today / Hide always) at the store/controller layer.

   Fixture adapter only. What's asserted here is the FRONTEND contract:

   - one row is expanded at a time, by construction; selection is independent
     of expansion and is pruned when a row leaves the queue;
   - Skip today goes through the durable runtime verb `drop_from_plan` against
     the row IDENTITY (never a name-keyed `included=false` override), and the
     Skipped today list is the refreshed `droppedToday` projection;
   - Restore today reverses it through `restore_today`;
   - Hide always uses the journaled `hide_always` verb instead of editing the
     exclusion policies from the row;
   - every one of those verbs is disabled with a written reason until Day Setup
     is confirmed, so the call can never reach the server's 409.

   The server-side behavior (dated dropped[], undrop, exclusion journal) is
   backend-tested in test_skip_today.py / test_hide_always.py. */

import { describe, expect, it, vi } from "vitest";
import { createStore } from "./createStore";
import { Controller } from "./controller";
import { FixtureAdapter } from "../adapters/fixture";
import {
  DAY_SETUP_GATE_REASON,
  expandedRowId,
  isRowSelected,
  selectedRowIds,
  skippedTodayRows,
  taskActionDisabledReason,
} from "./store";
import type { DroppedItem } from "../model/types";
import type { ScenarioName } from "../fixtures/scenarios";

/** Loaded scenario ("ready" = Setup confirmed). `confirm: false` loads
    "fresh", whose Day Setup is unconfirmed — the gate the row verbs honor. */
async function harness(opts: { confirm?: boolean; scenario?: ScenarioName } = {}) {
  const confirm = opts.confirm !== false;
  const store = createStore();
  const adapter = new FixtureAdapter(opts.scenario ?? (confirm ? "ready" : "fresh"));
  const controller = new Controller(adapter, store.dispatch, store.getState);
  await controller.load();
  if (confirm) {
    store.dispatch({
      type: "SETUP_SAVED",
      daySetup: { ...store.getState().daySetup, confirmed: true },
    });
  }
  return { store, adapter, controller };
}

/** Replace the next source reads' `droppedToday` and mirror the server's own
    projection: a dropped identity leaves `assigned`. Everything else in the
    refresh result (fixed inputs, ledger) stays the fixture's. */
function stubDrops(adapter: FixtureAdapter, drops: () => DroppedItem[]) {
  const real = adapter.refreshSources.bind(adapter);
  return vi.spyOn(adapter, "refreshSources").mockImplementation(async () => {
    const result = await real();
    const entries = drops();
    const droppedNames = new Set(entries.map((d) => d.name));
    return {
      ...result,
      inputs: {
        ...result.inputs,
        assigned: result.inputs.assigned.filter((i) => !droppedNames.has(i.id)),
        droppedToday: entries.map((d) => ({ ...d })),
      },
    };
  });
}

describe("task-actions row UI state", () => {
  it("expands one row at a time — opening another collapses the first", async () => {
    const { store, controller } = await harness();
    controller.expandRow("Press");
    expect(expandedRowId(store.getState())).toBe("Press");

    controller.expandRow("Magic Mirror");
    expect(expandedRowId(store.getState())).toBe("Magic Mirror");

    controller.expandRow(null);
    expect(expandedRowId(store.getState())).toBeNull();
  });

  it("keeps selection independent of expansion, with select-all and clear", async () => {
    const { store, controller } = await harness();
    controller.expandRow("Press");
    controller.selectRow("Press", true);
    controller.selectRow("Magic Mirror", true);
    expect(selectedRowIds(store.getState())).toEqual(["Press", "Magic Mirror"]);

    // Expanding a different row never edits the selection.
    controller.expandRow("Magic Mirror");
    expect(expandedRowId(store.getState())).toBe("Magic Mirror");
    expect(selectedRowIds(store.getState())).toEqual(["Press", "Magic Mirror"]);

    controller.selectRow("Press", false);
    expect(isRowSelected(store.getState(), "Press")).toBe(false);
    expect(expandedRowId(store.getState())).toBe("Magic Mirror");

    controller.selectAllRows();
    expect(selectedRowIds(store.getState())).toEqual(
      store.getState().inputs!.assigned.map((i) => i.id),
    );
    controller.clearSelection();
    expect(selectedRowIds(store.getState())).toEqual([]);
    // Clearing the selection leaves the expanded row alone.
    expect(expandedRowId(store.getState())).toBe("Magic Mirror");
  });
});

describe("task-actions Skip today / Restore today", () => {
  it("journals drop_from_plan by identity, then restore_today reverses it", async () => {
    const { store, adapter, controller } = await harness();
    const verb = vi.spyOn(adapter, "runtimeAction");
    let drops: DroppedItem[] = [];
    stubDrops(adapter, () => drops);

    controller.expandRow("Press");
    controller.selectRow("Press", true);

    drops = [
      {
        identity: "todoist:6fx00xPRESS",
        name: "Press",
        droppedAt: "2026-07-18T09:00:00Z",
        source: "todoist",
        minutes: 75,
      },
    ];
    await controller.skipToday("Press");

    expect(verb).toHaveBeenCalledWith("drop_from_plan", "todoist:6fx00xPRESS");
    expect(store.getState().runtimeError).toBeNull();
    expect(store.getState().lastRuntimeAction).toMatchObject({
      verb: "drop_from_plan",
      status: "applied",
    });
    // The refreshed read is the source of truth: the row is out of the queue,
    // listed as skipped, and no local included=false override was invented.
    const afterSkip = store.getState();
    expect(afterSkip.inputs!.assigned.some((i) => i.id === "Press")).toBe(false);
    expect(skippedTodayRows(afterSkip)).toEqual([
      {
        identity: "todoist:6fx00xPRESS",
        name: "Press",
        droppedAt: "2026-07-18T09:00:00Z",
        source: "todoist",
        kind: null,
        minutes: 75,
      },
    ]);
    expect(afterSkip.overrides["Press"]).toBeUndefined();
    // Row UI state names rows, so a row that left the queue cannot stay
    // expanded or selected.
    expect(expandedRowId(afterSkip)).toBeNull();
    expect(selectedRowIds(afterSkip)).toEqual([]);

    drops = [];
    await controller.restoreToday("todoist:6fx00xPRESS");

    expect(verb).toHaveBeenLastCalledWith("restore_today", "todoist:6fx00xPRESS");
    const afterRestore = store.getState();
    expect(skippedTodayRows(afterRestore)).toEqual([]);
    expect(afterRestore.inputs!.assigned.some((i) => i.id === "Press")).toBe(true);
    expect(afterRestore.runtimeError).toBeNull();
  });

  it("surfaces a refused skip and never refreshes the row away", async () => {
    const { store, adapter, controller } = await harness();
    const refresh = vi.spyOn(adapter, "refreshSources");
    adapter.runtimeAction = async (verb: string, target: string) => ({
      id: "ra-1",
      verb,
      targetName: target,
      status: "failed" as const,
      error: "runstate is not writable for this item",
      duplicate: false,
    });

    await controller.skipToday("Press");

    expect(refresh).not.toHaveBeenCalled();
    expect(store.getState().runtimeError).toMatch(/not writable/);
    expect(store.getState().inputs!.assigned.some((i) => i.id === "Press")).toBe(true);
  });
});

describe("task-actions Hide always", () => {
  it("uses the journaled hide_always verb, not the exclusion-policy writers", async () => {
    const { store, adapter, controller } = await harness();
    const verb = vi.spyOn(adapter, "runtimeAction");
    const todoistPolicy = vi.spyOn(adapter, "saveTagExclusionSettings");
    const capacitiesPolicy = vi.spyOn(adapter, "saveCapacitiesSettings");

    await controller.hideAlways("Press");
    expect(verb).toHaveBeenCalledWith("hide_always", "todoist:6fx00xPRESS");

    // A Capacities row targets its canonical identity, not a Todoist id.
    await controller.hideAlways("Note Processing");
    expect(verb).toHaveBeenLastCalledWith(
      "hide_always",
      "capacities:fixture-space:custom-project:note-processing",
    );

    // The settings-screen writers stay untouched by a row action.
    expect(todoistPolicy).not.toHaveBeenCalled();
    expect(capacitiesPolicy).not.toHaveBeenCalled();
    expect(store.getState().runtimeError).toBeNull();
  });
});

describe("task-actions Day Setup gate", () => {
  it("disables skip / restore / hide / complete with a written reason", async () => {
    const { store, adapter, controller } = await harness({ confirm: false });
    const s = store.getState();
    expect(s.daySetup.confirmed).toBe(false);
    for (const verb of ["skip", "restore", "hide", "complete"] as const) {
      expect(taskActionDisabledReason(s, verb)).toBe(DAY_SETUP_GATE_REASON);
      expect(taskActionDisabledReason(s, verb)).toBe("Confirm Day Setup first");
    }

    const call = vi.spyOn(adapter, "runtimeAction");
    const refresh = vi.spyOn(adapter, "refreshSources");
    await controller.skipToday("Press");
    await controller.restoreToday("Press");
    await controller.hideAlways("Press");
    expect(call).not.toHaveBeenCalled();
    expect(refresh).not.toHaveBeenCalled();
    expect(store.getState().runtimeError).toBe(DAY_SETUP_GATE_REASON);

    // Confirming the day removes the gate from every verb.
    store.dispatch({
      type: "SETUP_SAVED",
      daySetup: { ...store.getState().daySetup, confirmed: true },
    });
    for (const verb of ["skip", "restore", "hide", "complete"] as const) {
      expect(taskActionDisabledReason(store.getState(), verb)).toBeNull();
    }
  });
});

describe("task-actions Skipped today rows", () => {
  it("projects droppedToday with the snapshot fields, null on a legacy entry", async () => {
    const { store } = await harness();
    const inputs = store.getState().inputs!;
    store.dispatch({
      type: "INPUTS_LOADED",
      inputs: {
        ...inputs,
        droppedToday: [
          { identity: "todoist:legacy", name: "Legacy drop", droppedAt: null },
          {
            identity: "capacities:fixture-space:custom-project:snap",
            name: "Snapped drop",
            droppedAt: "2026-07-18T08:00:00Z",
            source: "capacities",
            kind: "custom-project",
            minutes: 30,
          },
        ],
      },
      ledger: store.getState().ledger!,
    });

    expect(skippedTodayRows(store.getState())).toEqual([
      {
        identity: "todoist:legacy",
        name: "Legacy drop",
        droppedAt: null,
        source: null,
        kind: null,
        minutes: null,
      },
      {
        identity: "capacities:fixture-space:custom-project:snap",
        name: "Snapped drop",
        droppedAt: "2026-07-18T08:00:00Z",
        source: "capacities",
        kind: "custom-project",
        minutes: 30,
      },
    ]);
  });
});
