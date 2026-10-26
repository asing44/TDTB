/* Already placed: dragging a calendar event's trailing edge commits through
   the same saveAnchoredOverride path as the Calendar impact stepper. */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent } from "@testing-library/preact";
import { AlreadyPlaced } from "./AlreadyPlaced";
import { makeHarness } from "./test-harness";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});
beforeEach(() => {
  // 1px per minute on a 07:30–23:45 frame.
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({
    x: 0, y: 0, left: 0, top: 0, right: 975, bottom: 96, width: 975, height: 96,
    toJSON: () => ({}),
  });
});

function harness() {
  const h = makeHarness("ready");
  const inputs = structuredClone(h.store.getState().inputs!);
  inputs.anchored = [
    {
      id: "Work sync", name: "Work sync", kind: "calendar",
      start: "09:30", end: "10:20", durationMin: 50, overlapAllowed: false,
      on: true, skipToday: false, calendarId: "work", calendarTitle: "Trinoor",
      capacityClass: "work",
    },
    {
      id: "Dentist", name: "Dentist", kind: "calendar",
      start: "13:00", end: "13:30", durationMin: 30, overlapAllowed: false,
      on: true, skipToday: false, calendarId: "p", calendarTitle: "Personal",
      capacityClass: "fixed",
    },
  ];
  h.store.dispatch({ type: "INPUTS_LOADED", inputs, ledger: h.store.getState().ledger! });
  return h;
}

const handle = (c: HTMLElement, id: string) =>
  c.querySelector(`[data-anchor-id="${id}"] .strip-cal__resize`) as HTMLElement;

describe("Already placed resize", () => {
  it("commits a keyboard resize as counted blocks through saveAnchoredOverride", () => {
    const h = harness();
    const save = vi.spyOn(h.controller, "saveAnchoredOverride").mockResolvedValue();
    const r = h.ui(<AlreadyPlaced />);
    // Work sync counts 2 blocks (50 min); the next 30-minute grid line is 60.
    fireEvent.keyDown(handle(r.container, "Work sync"), { key: "ArrowRight" });
    expect(save).toHaveBeenCalledWith(
      "Work sync",
      expect.objectContaining({ on: true, skipToday: false, time: null, blocks: 2 }),
    );
    fireEvent.keyDown(handle(r.container, "Dentist"), { key: "ArrowRight" });
    expect(save).toHaveBeenLastCalledWith(
      "Dentist",
      expect.objectContaining({ on: true, skipToday: false, time: null, blocks: 2 }),
    );
  });

  it("commits a pointer drag snapped to whole blocks, floor one block", () => {
    const h = harness();
    const save = vi.spyOn(h.controller, "saveAnchoredOverride").mockResolvedValue();
    const r = h.ui(<AlreadyPlaced />);
    const el = handle(r.container, "Dentist");
    const pointer = { pointerId: 1, button: 0, pointerType: "mouse" } as const;
    fireEvent.pointerDown(el, { ...pointer, clientX: 500 });
    fireEvent.pointerMove(el, { ...pointer, clientX: 580 }); // +80 min -> 110 -> 120
    fireEvent.pointerUp(el, { ...pointer, clientX: 580 });
    expect(save).toHaveBeenCalledTimes(1);
    expect(save).toHaveBeenCalledWith("Dentist", expect.objectContaining({ blocks: 4 }));
    save.mockClear();
    fireEvent.pointerDown(el, { ...pointer, clientX: 500 });
    fireEvent.pointerMove(el, { ...pointer, clientX: 100 });
    fireEvent.pointerUp(el, { ...pointer, clientX: 100 });
    // Already at one block: shrinking cannot change anything.
    expect(save).not.toHaveBeenCalled();
  });

  it("keeps a local start edit when the duration changes", () => {
    const h = harness();
    h.store.dispatch({
      type: "SETUP_SAVED",
      daySetup: {
        ...h.store.getState().daySetup,
        anchored: {
          ...h.store.getState().daySetup.anchored,
          Dentist: {
            on: true, skipToday: false, time: null, start: "14:00", calendarStartAtEdit: "13:00",
          },
        },
      },
    });
    const save = vi.spyOn(h.controller, "saveAnchoredOverride").mockResolvedValue();
    const r = h.ui(<AlreadyPlaced />);
    fireEvent.keyDown(handle(r.container, "Dentist"), { key: "ArrowRight" });
    expect(save).toHaveBeenCalledWith(
      "Dentist",
      expect.objectContaining({ start: "14:00", calendarStartAtEdit: "13:00", blocks: 2 }),
    );
  });
});
