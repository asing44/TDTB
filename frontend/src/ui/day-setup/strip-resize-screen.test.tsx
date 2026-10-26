/* Set up day screen: dragging the strip's trailing-edge handle saves through
   the same paths the popover and Anchored blocks stepper use. Drives the real
   screen (App) and asserts the persisted write through the fixture adapter. */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, waitFor } from "@testing-library/preact";
import { App } from "../App";
import { FixtureAdapter } from "../../adapters/fixture";
import { makeHarness } from "../test-harness";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function screenHarness(keepCalendar: boolean) {
  const h = makeHarness("ready");
  const inputs = structuredClone(h.store.getState().inputs!);
  if (!keepCalendar) inputs.anchored = inputs.anchored.filter((a) => a.kind !== "calendar");
  inputs.daySetup = { ...inputs.daySetup, anchor: "07:30" };
  inputs.daySemantics = { ...inputs.daySemantics, mintSessions: [] };
  h.store.dispatch({ type: "INPUTS_LOADED", inputs, ledger: h.store.getState().ledger! });
  h.store.dispatch({ type: "UI", patch: { screen: "setup", settingsPanel: null } });
  return h;
}

type R = ReturnType<ReturnType<typeof makeHarness>["ui"]>;
const handle = (r: R, id: string) =>
  r.container.querySelector(`[data-resize-id="${id}"]`) as HTMLElement;
const pointer = { pointerId: 1, button: 0, pointerType: "mouse" } as const;
const SPAN = 23 * 60 + 45 - (7 * 60 + 30);

function stubLane() {
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({
    x: 0, y: 0, left: 0, top: 0, right: SPAN, bottom: 96, width: SPAN, height: 96,
    toJSON: () => ({}),
  });
}

describe("Set up day strip resize commits", () => {
  it("an anchor resize writes today's blocks through saveDaySetup", async () => {
    const h = screenHarness(false);
    stubLane();
    const save = vi.spyOn(FixtureAdapter.prototype, "saveDaySetup");
    const r = h.ui(<App />);
    const el = handle(r, "Sudsing");
    expect(el).not.toBeNull();
    const before = Number(el.getAttribute("aria-valuenow")) / 30;
    fireEvent.pointerDown(el, { ...pointer, clientX: 500 });
    fireEvent.pointerMove(el, { ...pointer, clientX: 560 }); // +60 min = +2 blocks
    fireEvent.pointerUp(el, { ...pointer, clientX: 560 });
    await waitFor(() => expect(save).toHaveBeenCalled());
    const sent = save.mock.calls.at(-1)![0].anchored.Sudsing;
    expect(sent.blocks).toBe(before + 2);
    expect(sent).toMatchObject({ on: true, skipToday: false });
    await waitFor(() =>
      expect(h.store.getState().daySetup.anchored.Sudsing?.blocks).toBe(before + 2),
    );
  });

  it("a calendar resize is a local blocks edit, never a calendar write", async () => {
    const h = screenHarness(true);
    stubLane();
    const save = vi.spyOn(FixtureAdapter.prototype, "saveDaySetup");
    const r = h.ui(<App />);
    const el = handle(r, "Trinoor Standup"); // calendar, 09:15-09:45
    expect(el).not.toBeNull();
    fireEvent.pointerDown(el, { ...pointer, clientX: 500 });
    fireEvent.pointerMove(el, { ...pointer, clientX: 530 }); // +30 min -> 2 blocks
    fireEvent.pointerUp(el, { ...pointer, clientX: 530 });
    await waitFor(() =>
      expect(h.store.getState().daySetup.anchored["Trinoor Standup"]?.blocks).toBe(2),
    );
    const sent = save.mock.calls.at(-1)![0].anchored["Trinoor Standup"];
    expect(sent).toMatchObject({ blocks: 2, skipToday: false, time: null });
    expect(sent.start ?? null).toBeNull();
  });

  it("keyboard on an anchor handle steps one block", async () => {
    const h = screenHarness(false);
    const save = vi.spyOn(FixtureAdapter.prototype, "saveDaySetup");
    const r = h.ui(<App />);
    const el = handle(r, "Sudsing");
    const before = Number(el.getAttribute("aria-valuenow")) / 30;
    fireEvent.keyDown(el, { key: "ArrowRight" });
    await waitFor(() => expect(save).toHaveBeenCalled());
    expect(save.mock.calls.at(-1)![0].anchored.Sudsing.blocks).toBe(before + 1);
  });
});
