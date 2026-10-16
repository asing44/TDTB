/* keep-all-day.test.tsx — GitHub issue asing44/TDTB#17: a per-row "Keep
   all-day" action in Plan tasks. It routes the row through the existing
   blocks == 0 all-day path (the shadow's date-only `schedule-all-day` due, no
   timed block) and, once that path set the state, offers the reverse ("Use a
   time block") to restore the source estimate.

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
import { makeScenario, type Scenario, type ScenarioName } from "../fixtures/scenarios";
import { makeHarness } from "./test-harness";
import { effectiveBlocks, type Action } from "../store/store";

function rowFor(container: Element, name: string): HTMLElement {
  const rows = Array.from(container.querySelectorAll<HTMLElement>(".qrow"));
  const row = rows.find(
    (r) => r.querySelector(".qrow__name")?.textContent === name,
  );
  if (!row) throw new Error(`no row named ${name}`);
  return row;
}

function openMore(container: Element, name: string): HTMLElement {
  const row = rowFor(container, name);
  const trigger = row.querySelector<HTMLButtonElement>(
    'button[aria-haspopup="menu"]',
  );
  if (!trigger) throw new Error("no More trigger on the row");
  fireEvent.click(trigger);
  const menu = row.querySelector<HTMLElement>('[role="menu"]');
  if (!menu) throw new Error("More menu is not open");
  return menu;
}

function itemByLabel(menu: Element, label: string): HTMLButtonElement | null {
  return menu.querySelector<HTMLButtonElement>(`button[aria-label="${label}"]`);
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

describe("Keep all-day: menu presence and eligibility", () => {
  it("renders enabled for a date-only Todoist row", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    const item = itemByLabel(openMore(container, "Press"), "Keep all-day for Press");
    expect(item).not.toBeNull();
    expect(item!.getAttribute("role")).toBe("menuitem");
    expect(item!.classList.contains("row-more__item")).toBe(true);
    expect(item!.disabled).toBe(false);
  });

  it("is disabled with the reason for a native-timed Todoist row", () => {
    const h = makeHarness("ready", withNativeTime);
    const { container } = h.ui(<Queue />);
    const item = itemByLabel(openMore(container, "Press"), "Keep all-day for Press");
    expect(item).not.toBeNull();
    expect(item!.disabled).toBe(true);
    expect(item!.getAttribute("title")).toBe("Has a fixed Todoist time");
    expect(item!.textContent).toContain("Has a fixed Todoist time");
  });

  it("is disabled with the reason for a Capacities row", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    const item = itemByLabel(
      openMore(container, "Note Processing"),
      "Keep all-day for Note Processing",
    );
    expect(item).not.toBeNull();
    expect(item!.disabled).toBe(true);
    expect(item!.getAttribute("title")).toBe("Capacities rows carry no Todoist due");
  });

  it("reads Already all-day for a source zero-block row", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    const item = itemByLabel(
      openMore(container, "Charge GoPro"),
      "Keep all-day for Charge GoPro",
    );
    expect(item).not.toBeNull();
    expect(item!.disabled).toBe(true);
    expect(item!.getAttribute("title")).toBe("Already all-day");
  });
});

describe("Keep all-day: the all-day state change", () => {
  it("dispatches the blocks == 0 override exactly once and renders the row all-day", () => {
    const h = recordingHarness("ready");
    const { container } = h.ui(<Queue />);
    const before = h.actions.filter((a) => a.type === "OVERRIDE_SET").length;

    fireEvent.click(
      itemByLabel(openMore(container, "Press"), "Keep all-day for Press")!,
    );

    const overrides = h.actions.filter((a) => a.type === "OVERRIDE_SET");
    expect(overrides.length - before).toBe(1);
    expect(overrides[overrides.length - 1]).toEqual({
      type: "OVERRIDE_SET",
      id: "Press",
      override: { included: true, blocks: 0 },
    });
    expect(effectiveBlocks(h.store.getState(), "Press")).toBe(0);

    const row = rowFor(container, "Press");
    expect(row.classList.contains("qrow--background")).toBe(true);
    const slider = row.querySelector<HTMLInputElement>(".alloc-track__input");
    expect(slider?.disabled).toBe(true);
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

    fireEvent.click(
      itemByLabel(openMore(container, "Press"), "Keep all-day for Press")!,
    );

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

    fireEvent.click(
      itemByLabel(openMore(container, "Press"), "Keep all-day for Press")!,
    );
    await new Promise((resolve) => setTimeout(resolve, 150));

    expect(sequence).not.toHaveBeenCalled();
    expect(live).not.toHaveBeenCalled();
    expect(shadow).not.toHaveBeenCalled();
  });
});

describe("Keep all-day: reverse path", () => {
  it("offers Use a time block to restore the source estimate", () => {
    const h = recordingHarness("ready");
    const { container } = h.ui(<Queue />);

    fireEvent.click(
      itemByLabel(openMore(container, "Press"), "Keep all-day for Press")!,
    );
    expect(effectiveBlocks(h.store.getState(), "Press")).toBe(0);

    const restore = itemByLabel(
      openMore(container, "Press"),
      "Use a time block for Press",
    );
    expect(restore).not.toBeNull();
    expect(restore!.disabled).toBe(false);
    fireEvent.click(restore!);

    expect(effectiveBlocks(h.store.getState(), "Press")).toBe(3);
    expect(
      rowFor(container, "Press").classList.contains("qrow--background"),
    ).toBe(false);
  });
});
