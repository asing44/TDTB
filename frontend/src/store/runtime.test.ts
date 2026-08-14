/* runtime.test.ts — T20 runtime-verb state + controller flow. Fixture
   adapter only; server semantics (journal, compensation) are backend-tested. */

import { describe, expect, it } from "vitest";
import {
  canRuntimeAct,
  initialState,
  reducer,
  type AppState,
} from "./store";
import { createStore } from "./createStore";
import { Controller } from "./controller";
import { FixtureAdapter } from "../adapters/fixture";
import type { RuntimeAction } from "../model/types";

const applied: RuntimeAction = {
  id: "ra-1",
  verb: "complete",
  targetName: "Press",
  status: "applied",
  error: null,
  duplicate: false,
};

function committedState(): AppState {
  return {
    ...initialState,
    commitReport: { status: "ok" } as AppState["commitReport"],
    commitPhase: "done",
  };
}

describe("runtime reducer", () => {
  it("START sets busy and clears the error", () => {
    let s = reducer(initialState, { type: "RUNTIME_FAIL", error: "boom" });
    s = reducer(s, { type: "RUNTIME_START" });
    expect(s.runtimeBusy).toBe(true);
    expect(s.runtimeError).toBeNull();
  });

  it("OK stores the journal entry and clears busy", () => {
    let s = reducer(initialState, { type: "RUNTIME_START" });
    s = reducer(s, { type: "RUNTIME_OK", action: applied });
    expect(s.runtimeBusy).toBe(false);
    expect(s.lastRuntimeAction).toEqual(applied);
  });

  it("FAIL records the error", () => {
    let s = reducer(initialState, { type: "RUNTIME_START" });
    s = reducer(s, { type: "RUNTIME_FAIL", error: "surface unavailable: calendar" });
    expect(s.runtimeBusy).toBe(false);
    expect(s.runtimeError).toMatch(/surface unavailable/);
  });

  it("UNDO_OK replaces the entry with its undone form", () => {
    let s = reducer(initialState, { type: "RUNTIME_OK", action: applied });
    s = reducer(s, {
      type: "RUNTIME_UNDO_OK",
      action: { ...applied, status: "undone" },
    });
    expect(s.lastRuntimeAction!.status).toBe("undone");
  });
});

describe("canRuntimeAct", () => {
  it("requires a live commit report", () => {
    expect(canRuntimeAct(initialState)).toBe(false);
    expect(canRuntimeAct(committedState())).toBe(true);
  });

  it("one action at a time — busy blocks", () => {
    const s = { ...committedState(), runtimeBusy: true };
    expect(canRuntimeAct(s)).toBe(false);
  });
});

describe("controller runtime flow", () => {
  it("apply then scoped undo round-trips through the adapter", async () => {
    const store = createStore();
    const controller = new Controller(
      new FixtureAdapter("verified"), store.dispatch, store.getState);
    await controller.runtimeAction("complete", "Press");
    expect(store.getState().lastRuntimeAction).toMatchObject({
      verb: "complete", targetName: "Press", status: "applied",
    });
    await controller.undoRuntimeAction();
    expect(store.getState().lastRuntimeAction!.status).toBe("undone");
  });

  it("undo without an applied action is a no-op", async () => {
    const store = createStore();
    const controller = new Controller(
      new FixtureAdapter("verified"), store.dispatch, store.getState);
    await controller.undoRuntimeAction();
    expect(store.getState().lastRuntimeAction).toBeNull();
    expect(store.getState().runtimeBusy).toBe(false);
  });

  it("adapter failure lands in runtimeError, never throws", async () => {
    const store = createStore();
    const adapter = new FixtureAdapter("verified");
    adapter.runtimeAction = async () => {
      throw new Error("surface unavailable: todoist");
    };
    const controller = new Controller(adapter, store.dispatch, store.getState);
    await controller.runtimeAction("complete", "Press");
    expect(store.getState().runtimeError).toMatch(/surface unavailable/);
    expect(store.getState().runtimeBusy).toBe(false);
  });
});
