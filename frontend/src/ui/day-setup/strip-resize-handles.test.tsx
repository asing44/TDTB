/* Trailing-edge resize handles on the Set up day strip: anchor blocks and
   editable calendar blocks. Both are button blocks, so the slider is a sibling
   laid over the block's last pixels. jsdom has no layout, so the lane is
   stubbed 1px per minute like calendar-resize-strip. */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render } from "@testing-library/preact";
import { DayStrip, type StripAnchor, type StripCalendarEvent } from "./DayStrip";

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

const sudsing: StripAnchor = {
  id: "Sudsing", name: "Sudsing", start: "18:00", blocks: 2, counted: true, reason: null,
  movable: true, resizable: true,
};
const standup: StripCalendarEvent = {
  id: "Standup", name: "Standup", start: "09:00", durationMin: 60, counted: true,
  editable: true, resizable: true,
};

function setup(
  anchors: StripAnchor[],
  calendar: StripCalendarEvent[] = [],
  extra: Partial<Parameters<typeof DayStrip>[0]> = {},
) {
  const onResizeEvent = vi.fn();
  const onMoveAnchor = vi.fn();
  const onSelectAnchor = vi.fn();
  const r = render(
    <DayStrip
      frameStart="07:30"
      frameEnd="23:45"
      sessions={[]}
      selectedIds={[]}
      selectableIds={[]}
      onSelectIds={() => {}}
      onToggleSession={() => {}}
      anchors={anchors}
      calendar={calendar}
      onSelectAnchor={onSelectAnchor}
      onMoveAnchor={onMoveAnchor}
      onResizeEvent={onResizeEvent}
      resizeStepMin={30}
      resizeMaxMin={360}
      {...extra}
    />,
  );
  const handle = (id: string) =>
    r.container.querySelector(`[data-resize-id="${id}"]`) as HTMLElement;
  const block = (id: string) =>
    r.container.querySelector(`button[data-anchor-id="${id}"]`) as HTMLElement;
  return { r, handle, block, onResizeEvent, onMoveAnchor, onSelectAnchor };
}

const pointer = { pointerId: 1, button: 0, pointerType: "mouse" } as const;

describe("anchor block resize handle", () => {
  it("is a slider sibling of the block, not nested in its button", () => {
    const { handle, block } = setup([sudsing]);
    const h = handle("Sudsing");
    expect(h.getAttribute("role")).toBe("slider");
    expect(h.getAttribute("aria-valuenow")).toBe("60");
    expect(h.getAttribute("aria-valuetext")).toBe("1 hour");
    expect(block("Sudsing").contains(h)).toBe(false);
    expect(h.parentElement).toBe(block("Sudsing").parentElement);
  });

  it("sits on the trailing edge of the block", () => {
    const { handle, block } = setup([sudsing]);
    const b = block("Sudsing");
    const right = parseFloat(b.style.left) + parseFloat(b.style.width);
    const pct = parseFloat(handle("Sudsing").style.left.replace("calc(", ""));
    expect(pct).toBeCloseTo(right, 2);
    expect(handle("Sudsing").style.left).toContain("- 6px");
  });

  it("needs a counted, resizable, in-frame anchor on a strip that accepts resizes", () => {
    expect(setup([{ ...sudsing, resizable: false }]).handle("Sudsing")).toBeNull();
    cleanup();
    expect(setup([{ ...sudsing, counted: false }]).handle("Sudsing")).toBeNull();
    cleanup();
    expect(setup([{ ...sudsing, blocks: 0 }]).handle("Sudsing")).toBeNull();
    cleanup();
    expect(setup([sudsing], [], { onResizeEvent: undefined }).handle("Sudsing")).toBeNull();
    cleanup();
    expect(setup([sudsing], [], { readOnly: true }).handle("Sudsing")).toBeNull();
    cleanup();
    // 23:00 + 2 blocks runs past the 23:45 frame end: no handle on a clipped edge.
    expect(setup([{ ...sudsing, start: "23:00" }]).handle("Sudsing")).toBeNull();
  });

  it("drags on the 30-minute grid, previews live, and commits once on drop", () => {
    const { r, handle, block, onResizeEvent, onMoveAnchor, onSelectAnchor } = setup([sudsing]);
    const h = handle("Sudsing");
    fireEvent.pointerDown(h, { ...pointer, clientX: 500 });
    fireEvent.pointerMove(h, { ...pointer, clientX: 535 }); // +35 min -> 95 -> 90
    expect(parseFloat(block("Sudsing").style.width)).toBeCloseTo((90 / SPAN) * 100, 2);
    expect(r.getByText("1 hour → 1 h 30 min")).toBeTruthy();
    expect(onResizeEvent).not.toHaveBeenCalled();
    fireEvent.pointerUp(h, { ...pointer, clientX: 535 });
    expect(onResizeEvent).toHaveBeenCalledTimes(1);
    expect(onResizeEvent).toHaveBeenCalledWith("Sudsing", 90);
    expect(r.getByTestId("anchor-move-status").textContent).toBe("Sudsing resized to 1 h 30 min");
    expect(onMoveAnchor).not.toHaveBeenCalled();
    expect(onSelectAnchor).not.toHaveBeenCalled();
  });

  it("never shrinks below one block and ignores an unchanged length", () => {
    const { handle, block, onResizeEvent } = setup([sudsing]);
    const h = handle("Sudsing");
    fireEvent.pointerDown(h, { ...pointer, clientX: 500 });
    fireEvent.pointerMove(h, { ...pointer, clientX: 100 });
    expect(parseFloat(block("Sudsing").style.width)).toBeCloseTo((30 / SPAN) * 100, 2);
    fireEvent.pointerMove(h, { ...pointer, clientX: 502 });
    fireEvent.pointerUp(h, { ...pointer, clientX: 502 });
    expect(onResizeEvent).not.toHaveBeenCalled();
  });

  it("caps the length at the supplied ceiling", () => {
    const { handle, block } = setup([sudsing], [], { resizeMaxMin: 120 });
    const h = handle("Sudsing");
    fireEvent.pointerDown(h, { ...pointer, clientX: 500 });
    fireEvent.pointerMove(h, { ...pointer, clientX: 900 });
    expect(parseFloat(block("Sudsing").style.width)).toBeCloseTo((120 / SPAN) * 100, 2);
  });

  it("abandons the gesture on Escape", () => {
    const { handle, block, onResizeEvent } = setup([sudsing]);
    const h = handle("Sudsing");
    fireEvent.pointerDown(h, { ...pointer, clientX: 500 });
    fireEvent.pointerMove(h, { ...pointer, clientX: 560 });
    fireEvent.keyDown(h, { key: "Escape" });
    fireEvent.pointerUp(h, { ...pointer, clientX: 560 });
    expect(onResizeEvent).not.toHaveBeenCalled();
    expect(parseFloat(block("Sudsing").style.width)).toBeCloseTo((60 / SPAN) * 100, 2);
  });

  it("resizes from the keyboard: arrows step a block, Shift an hour, floor holds", () => {
    const { handle, onResizeEvent } = setup([sudsing]);
    const h = handle("Sudsing");
    h.focus();
    fireEvent.keyDown(h, { key: "ArrowRight" });
    expect(onResizeEvent).toHaveBeenLastCalledWith("Sudsing", 90);
    fireEvent.keyDown(h, { key: "ArrowLeft" });
    expect(onResizeEvent).toHaveBeenLastCalledWith("Sudsing", 30);
    fireEvent.keyDown(h, { key: "ArrowRight", shiftKey: true });
    expect(onResizeEvent).toHaveBeenLastCalledWith("Sudsing", 120);
    onResizeEvent.mockClear();
    cleanup();
    const one = setup([{ ...sudsing, blocks: 1 }]);
    fireEvent.keyDown(one.handle("Sudsing"), { key: "ArrowLeft" });
    expect(one.onResizeEvent).not.toHaveBeenCalled();
  });

  it("keeps move-drag on the block body and does not let the handle start a move", () => {
    const { handle, block, onResizeEvent, onMoveAnchor, onSelectAnchor } = setup([sudsing]);
    const b = block("Sudsing");
    fireEvent.pointerDown(b, { ...pointer, clientX: 500 });
    fireEvent.pointerMove(b, { ...pointer, clientX: 530 }); // +30 min -> 18:30
    fireEvent.pointerUp(b, { ...pointer, clientX: 530 });
    expect(onMoveAnchor).toHaveBeenCalledWith("Sudsing", "18:30");
    expect(onResizeEvent).not.toHaveBeenCalled();
    onMoveAnchor.mockClear();
    const h = handle("Sudsing");
    fireEvent.pointerDown(h, { ...pointer, clientX: 500 });
    fireEvent.pointerMove(h, { ...pointer, clientX: 530 });
    fireEvent.pointerUp(h, { ...pointer, clientX: 530 });
    expect(onMoveAnchor).not.toHaveBeenCalled();
    expect(onSelectAnchor).not.toHaveBeenCalled();
    expect(onResizeEvent).toHaveBeenCalledWith("Sudsing", 90);
  });

  it("hides the handle while the block is being moved", () => {
    const { handle, block } = setup([sudsing]);
    const b = block("Sudsing");
    fireEvent.pointerDown(b, { ...pointer, clientX: 500 });
    fireEvent.pointerMove(b, { ...pointer, clientX: 530 });
    expect(handle("Sudsing")).toBeNull();
    fireEvent.pointerUp(b, { ...pointer, clientX: 530 });
    expect(handle("Sudsing")).not.toBeNull();
  });

  it("marks a narrow block so its label clears the handle, and keeps the handle", () => {
    const { handle, block } = setup([{ ...sudsing, name: "Foods Breakfast", blocks: 1 }]);
    expect(block("Sudsing").className).toContain("strip-anchor--resizable");
    expect(handle("Sudsing")).not.toBeNull();
  });
});

describe("editable calendar block resize handle", () => {
  it("is a sibling slider on a counted, resizable event and commits the new length", () => {
    const { r, handle, block, onResizeEvent, onMoveAnchor } = setup([], [standup]);
    const h = handle("Standup");
    expect(h.getAttribute("role")).toBe("slider");
    expect(block("Standup").contains(h)).toBe(false);
    fireEvent.pointerDown(h, { ...pointer, clientX: 500 });
    fireEvent.pointerMove(h, { ...pointer, clientX: 531 }); // +31 -> 91 -> 90
    expect(r.getByText("1 hour → 1 h 30 min")).toBeTruthy();
    fireEvent.pointerUp(h, { ...pointer, clientX: 531 });
    expect(onResizeEvent).toHaveBeenCalledWith("Standup", 90);
    expect(onMoveAnchor).not.toHaveBeenCalled();
  });

  it("has no handle when skipped, not resizable, or clipped by the frame", () => {
    expect(setup([], [{ ...standup, counted: false }]).handle("Standup")).toBeNull();
    cleanup();
    expect(setup([], [{ ...standup, resizable: false }]).handle("Standup")).toBeNull();
    cleanup();
    expect(setup([], [{ ...standup, start: "23:30" }]).handle("Standup")).toBeNull();
  });

  it("keeps move-drag working and Escape cancels a resize", () => {
    const { handle, block, onResizeEvent, onMoveAnchor } = setup([], [standup]);
    const b = block("Standup");
    fireEvent.pointerDown(b, { ...pointer, clientX: 500 });
    fireEvent.pointerMove(b, { ...pointer, clientX: 530 });
    fireEvent.pointerUp(b, { ...pointer, clientX: 530 });
    expect(onMoveAnchor).toHaveBeenCalledWith("Standup", "09:30");
    const h = handle("Standup");
    fireEvent.pointerDown(h, { ...pointer, clientX: 500 });
    fireEvent.pointerMove(h, { ...pointer, clientX: 560 });
    fireEvent.keyDown(h, { key: "Escape" });
    fireEvent.pointerUp(h, { ...pointer, clientX: 560 });
    expect(onResizeEvent).not.toHaveBeenCalled();
  });

  it("steps from the keyboard on the 30-minute grid", () => {
    const { handle, onResizeEvent } = setup([], [standup]);
    fireEvent.keyDown(handle("Standup"), { key: "ArrowRight" });
    expect(onResizeEvent).toHaveBeenLastCalledWith("Standup", 90);
  });
});
