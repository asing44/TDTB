/* Trailing-edge duration resize on read-only calendar blocks. jsdom has no
   layout, so the lane is stubbed 1px per minute like calendar-edit-strip. */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render } from "@testing-library/preact";
import { DayStrip, type StripCalendarEvent } from "./DayStrip";

const FRAME_START = 7 * 60 + 30;
const SPAN = 23 * 60 + 45 - FRAME_START;

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});
beforeEach(() => {
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({
    x: 0, y: 0, left: 0, top: 0, right: SPAN, bottom: 96, width: SPAN, height: 96,
    toJSON: () => ({}),
  });
});

const sync: StripCalendarEvent = {
  id: "Sync", name: "Sync", start: "10:00", durationMin: 30, counted: true, resizable: true,
};

function setup(calendar: StripCalendarEvent[], extra: Partial<Parameters<typeof DayStrip>[0]> = {}) {
  const onResizeEvent = vi.fn();
  const r = render(
    <DayStrip
      readOnly
      frameStart="07:30"
      frameEnd="23:45"
      sessions={[]}
      selectedIds={[]}
      selectableIds={[]}
      onSelectIds={() => {}}
      onToggleSession={() => {}}
      anchors={[]}
      calendar={calendar}
      onSelectAnchor={() => {}}
      onResizeEvent={onResizeEvent}
      {...extra}
    />,
  );
  const handle = (id: string) =>
    r.container.querySelector(`[data-anchor-id="${id}"] .strip-cal__resize`) as HTMLElement;
  const block = (id: string) =>
    r.container.querySelector(`.strip-cal[data-anchor-id="${id}"]`) as HTMLElement;
  return { r, handle, block, onResizeEvent };
}

const pointer = { pointerId: 1, button: 0, pointerType: "mouse" } as const;

describe("calendar block resize handle", () => {
  it("draws a slider only for a counted, resizable event on a strip that accepts resizes", () => {
    const a = setup([sync]);
    expect(a.handle("Sync").getAttribute("role")).toBe("slider");
    expect(a.handle("Sync").getAttribute("aria-valuenow")).toBe("30");
    cleanup();
    expect(setup([{ ...sync, resizable: false }]).handle("Sync")).toBeNull();
    cleanup();
    expect(setup([{ ...sync, counted: false }]).handle("Sync")).toBeNull();
    cleanup();
    expect(setup([sync], { onResizeEvent: undefined }).handle("Sync")).toBeNull();
  });

  it("has no handle when the frame clips the trailing edge", () => {
    expect(setup([{ ...sync, start: "23:30", durationMin: 60 }]).handle("Sync")).toBeNull();
  });

  it("drags with a 15-minute snap, previews live, and commits once on drop", () => {
    const { r, handle, block, onResizeEvent } = setup([sync]);
    const el = handle("Sync");
    fireEvent.pointerDown(el, { ...pointer, clientX: 500 });
    fireEvent.pointerMove(el, { ...pointer, clientX: 522 }); // +22 min -> 52 -> 45
    expect(parseFloat(block("Sync").style.width)).toBeCloseTo((45 / SPAN) * 100, 2);
    expect(r.getByText("30 min → 45 min")).toBeTruthy();
    expect(onResizeEvent).not.toHaveBeenCalled();
    fireEvent.pointerUp(el, { ...pointer, clientX: 522 });
    expect(onResizeEvent).toHaveBeenCalledTimes(1);
    expect(onResizeEvent).toHaveBeenCalledWith("Sync", 45);
    expect(r.getByTestId("anchor-move-status").textContent).toBe("Sync resized to 45 min");
  });

  it("never shrinks below one step and never commits an unchanged length", () => {
    const { handle, block, onResizeEvent } = setup([{ ...sync, durationMin: 60 }]);
    const el = handle("Sync");
    fireEvent.pointerDown(el, { ...pointer, clientX: 500 });
    fireEvent.pointerMove(el, { ...pointer, clientX: 100 });
    expect(parseFloat(block("Sync").style.width)).toBeCloseTo((15 / SPAN) * 100, 2);
    fireEvent.pointerMove(el, { ...pointer, clientX: 500 + 2 }); // under threshold? already moved; back to ~60
    fireEvent.pointerUp(el, { ...pointer, clientX: 502 });
    expect(onResizeEvent).not.toHaveBeenCalled();
  });

  it("abandons the gesture on Escape", () => {
    const { handle, onResizeEvent } = setup([sync]);
    const el = handle("Sync");
    fireEvent.pointerDown(el, { ...pointer, clientX: 500 });
    fireEvent.pointerMove(el, { ...pointer, clientX: 560 });
    fireEvent.keyDown(el, { key: "Escape" });
    fireEvent.pointerUp(el, { ...pointer, clientX: 560 });
    expect(onResizeEvent).not.toHaveBeenCalled();
  });

  it("resizes from the keyboard: arrows step, Shift moves an hour, floor holds", () => {
    const { handle, onResizeEvent } = setup([sync]);
    const el = handle("Sync");
    el.focus();
    fireEvent.keyDown(el, { key: "ArrowRight" });
    expect(onResizeEvent).toHaveBeenLastCalledWith("Sync", 45);
    fireEvent.keyDown(el, { key: "ArrowLeft" });
    expect(onResizeEvent).toHaveBeenLastCalledWith("Sync", 15);
    fireEvent.keyDown(el, { key: "ArrowRight", shiftKey: true });
    expect(onResizeEvent).toHaveBeenLastCalledWith("Sync", 90);
  });

  it("does not move below the floor from the keyboard", () => {
    const { handle, onResizeEvent } = setup([{ ...sync, durationMin: 15 }]);
    fireEvent.keyDown(handle("Sync"), { key: "ArrowLeft" });
    expect(onResizeEvent).not.toHaveBeenCalled();
  });

  it("uses the supplied step and ceiling", () => {
    const { handle, onResizeEvent } = setup([sync], { resizeStepMin: 30, resizeMaxMin: 60 });
    const el = handle("Sync");
    fireEvent.keyDown(el, { key: "ArrowRight" });
    expect(onResizeEvent).toHaveBeenLastCalledWith("Sync", 60);
    onResizeEvent.mockClear();
    cleanup();
    const b = setup([{ ...sync, durationMin: 60 }], { resizeStepMin: 30, resizeMaxMin: 60 });
    fireEvent.keyDown(b.handle("Sync"), { key: "ArrowRight" });
    expect(b.onResizeEvent).not.toHaveBeenCalled();
  });
});
