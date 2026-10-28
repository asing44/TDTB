/* keep-all-day.test.tsx — GitHub issue asing44/TDTB#17: the per-row all-day
   path. It routes the row through the existing blocks == 0 all-day state (the
   shadow's date-only `schedule-all-day` due, no timed block) and offers the
   reverse (a time block) to restore the source estimate.

   Row redesign (2026-10-28): the scheduling radios and the Save step are gone.
   The expanded row's All-day control is an aria-pressed TOGGLE BUTTON that
   applies at once (blocks == 0, one Undo step); pressing it again gives the row
   a time block back. A Capacities row may now go all-day (a local plan state, no
   source write); only a protected Todoist native time stays closed.

   Fixture adapters only: no live adapter, no billed call, no source write.
   The billed-path assertions spy the adapter's own /sequence and /commit
   methods and require zero calls. */

import { afterEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  waitFor,
} from "@testing-library/preact";
import type { ComponentChildren } from "preact";

afterEach(cleanup);

import { Queue } from "./Queue";
import { Ctx } from "./context";
import { createStore } from "../store/createStore";
import { Controller } from "../store/controller";
import { FixtureAdapter } from "../adapters/fixture";
import type { SequenceContext } from "../adapters/adapter";
import {
  makeScenario,
  restorePlannedDay,
  type Scenario,
  type ScenarioName,
} from "../fixtures/scenarios";
import { makeHarness } from "./test-harness";
import { EXCLUSION_REASONS } from "../model/batchEligibility";
import { effectiveBlocks, type Action } from "../store/store";

function rowFor(container: Element, name: string): HTMLElement {
  const row = Array.from(container.querySelectorAll<HTMLElement>(".qrow")).find(
    (r) => r.querySelector(".qrow__name")?.textContent === name,
  );
  if (!row) throw new Error(`no row named ${name}`);
  return row;
}

/** Expand a row and return its refreshed element. */
function expand(container: Element, name: string): HTMLElement {
  fireEvent.click(rowFor(container, name).querySelector('button[aria-label="Expand"]')!);
  return rowFor(container, name);
}

/** The expanded panel's All-day toggle button. */
const allDay = (row: Element) =>
  Array.from(row.querySelectorAll<HTMLButtonElement>("button")).find(
    (b) => b.textContent?.trim() === "All-day",
  )!;
const btn = (row: Element, text: string) =>
  Array.from(row.querySelectorAll<HTMLButtonElement>("button")).find(
    (b) => b.textContent?.trim() === text,
  ) ?? null;

/** The written reason the All-day toggle points at with aria-describedby. */
function allDayReason(row: Element): string | null {
  const id = allDay(row).getAttribute("aria-describedby");
  if (!id) return null;
  return row.querySelector(`#${id}`)?.textContent ?? null;
}

/** Same staging as `makeHarness`, but every dispatch is recorded so the
    action's state change can be counted. */
function recordingHarness(
  name: ScenarioName,
  mutate?: (sc: Scenario) => void,
) {
  const sc = makeScenario(name);
  mutate?.(sc);
  const store = createStore();
  const actions: Action[] = [];
  const raw = store.dispatch;
  store.dispatch = (a: Action) => {
    actions.push(a);
    raw(a);
  };
  store.dispatch({
    type: "INPUTS_LOADED",
    inputs: sc.inputs,
    ledger: { ...sc.ledger },
  });
  // Ignore the restore in the recorded-actions view: this harness counts a
  // verb's own dispatches, and the fixture day's membership is staging.
  actions.length = 0;
  restorePlannedDay(store);
  actions.length = 0;
  if (sc.staged.daySetupConfirmed) {
    store.dispatch({
      type: "SETUP_SAVED",
      daySetup: { ...sc.inputs.daySetup, confirmed: true },
    });
  }
  const adapter = new FixtureAdapter(name);
  const controller = new Controller(adapter, store.dispatch, store.getState);
  const ui = (children: ComponentChildren) =>
    render(<Ctx.Provider value={{ store, controller }}>{children}</Ctx.Provider>);
  return { store, controller, adapter, actions, ui };
}

function withNativeTime(sc: Scenario): void {
  const press = sc.inputs.assigned.find((i) => i.name === "Press");
  if (!press) throw new Error("scenario is missing the Press row");
  press.scheduledStart = "19:00";
}

describe("Keep all-day: eligibility of the expanded row's All-day toggle", () => {
  it("is enabled for a date-only Todoist row", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    const row = expand(container, "Press");
    expect(allDay(row).getAttribute("aria-pressed")).toBe("false");
    expect(allDay(row).disabled).toBe(false);
    expect(allDayReason(row)).toBeNull();
  });

  it("is disabled with the written reason for a native-timed Todoist row", () => {
    const h = makeHarness("ready", withNativeTime);
    const { container } = h.ui(<Queue />);
    const row = expand(container, "Press");
    expect(allDay(row).disabled).toBe(true);
    expect(allDayReason(row)).toBe(EXCLUSION_REASONS.protectedAllDay);
  });

  it("is enabled for a Capacities row (local plan state, no source write)", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    const row = expand(container, "Note Processing");
    expect(allDay(row).disabled).toBe(false);
    expect(allDay(row).getAttribute("aria-pressed")).toBe("false");
    expect(allDayReason(row)).toBeNull();
  });

  it("reads as already all-day for a source zero-block row and reverses", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    const row = expand(container, "Charge GoPro");
    expect(allDay(row).getAttribute("aria-pressed")).toBe("true");
    // The reverse (a time block) stays available, so the row is not a dead end.
    expect(allDay(row).disabled).toBe(false);
    expect(allDayReason(row)).toBeNull();
    expect(row.querySelector(".trow__dur .dslider__label")?.textContent).toBe("All day");
    expect(row.querySelector(".trow__state")?.textContent).toBe("All-day");
  });
});

describe("Keep all-day: the toggle applies at once", () => {
  it("writes the blocks == 0 override exactly once and renders the row all-day", () => {
    const h = recordingHarness("ready");
    const { container } = h.ui(<Queue />);
    const before = h.actions.filter((a) => a.type === "OVERRIDE_SET").length;

    let row = expand(container, "Press");
    fireEvent.click(allDay(row));

    const overrides = h.actions.filter((a) => a.type === "OVERRIDE_SET");
    expect(overrides.length - before).toBe(1);
    // The all-day edit also persists the duration it captured, so switching
    // back to a time block restores the real estimate rather than 0.
    expect(overrides[overrides.length - 1]).toEqual({
      type: "OVERRIDE_SET",
      id: "Press",
      override: { included: true, blocks: 0, blocksBeforeAllDay: 3 },
    });
    expect(effectiveBlocks(h.store.getState(), "Press")).toBe(0);

    row = rowFor(container, "Press");
    expect(row.classList.contains("qrow--background")).toBe(true);
    expect(row.querySelector(".trow__dur .dslider__label")?.textContent).toBe("All day");
    expect(row.querySelector(".trow__state")?.textContent).toBe("All-day");
    // Applied, not staged: there is no Save step to clear.
    expect(btn(row, "Save")).toBeNull();
    expect(allDay(row).getAttribute("aria-pressed")).toBe("true");
  });

  it("carries blocks 0 into the sequence payload (existing all-day wire)", async () => {
    const h = makeHarness("sequenced");
    const ctxs: SequenceContext[] = [];
    const original = h.adapter.validateSequence.bind(h.adapter);
    vi.spyOn(h.adapter, "validateSequence").mockImplementation(
      async (rows, ctx) => {
        ctxs.push(ctx);
        return original(rows, ctx);
      },
    );
    const { container } = h.ui(<Queue />);

    const row = expand(container, "Press");
    fireEvent.click(allDay(row));

    await waitFor(() => expect(ctxs.length).toBeGreaterThan(0));
    const press = ctxs[ctxs.length - 1].included.find((i) => i.id === "Press");
    expect(press?.blocks).toBe(0);
    expect(rowFor(container, "Press").classList.contains("qrow--background")).toBe(
      true,
    );
  });

  it("fires no billed /sequence or /commit call", async () => {
    const h = makeHarness("ready");
    const sequence = vi.spyOn(h.adapter, "autoSequence");
    const live = vi.spyOn(h.adapter, "liveCommit");
    const shadow = vi.spyOn(h.adapter, "shadowCommit");
    const { container } = h.ui(<Queue />);

    const row = expand(container, "Press");
    fireEvent.click(allDay(row));
    await new Promise((resolve) => setTimeout(resolve, 150));

    expect(sequence).not.toHaveBeenCalled();
    expect(live).not.toHaveBeenCalled();
    expect(shadow).not.toHaveBeenCalled();
  });
});

describe("Keep all-day: reverse path", () => {
  it("restores the source estimate by pressing All-day again", () => {
    const h = recordingHarness("ready");
    const { container } = h.ui(<Queue />);

    let row = expand(container, "Press");
    fireEvent.click(allDay(row));
    expect(effectiveBlocks(h.store.getState(), "Press")).toBe(0);

    row = rowFor(container, "Press");
    fireEvent.click(allDay(row));

    expect(effectiveBlocks(h.store.getState(), "Press")).toBe(3);
    expect(
      rowFor(container, "Press").classList.contains("qrow--background"),
    ).toBe(false);
  });
});
