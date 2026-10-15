/* Calendar events on the Day strip become editable (locally only) when the
   panel marks them `editable`: click selects, a horizontal drag moves them with
   a 15-minute snap clamped to the frame, and the existing label rules stay.
   jsdom has no layout, so the lane is stubbed 1px per minute like DayStrip.test. */

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

const dentist: StripCalendarEvent = {
  id: "Dentist", name: "Dentist", start: "19:00", durationMin: 30, counted: true, editable: true,
};

function setup(calendar: StripCalendarEvent[], extra: Partial<Parameters<typeof DayStrip>[0]> = {}) {
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
      anchors={[]}
      calendar={calendar}
      onSelectAnchor={onSelectAnchor}
      onMoveAnchor={onMoveAnchor}
      {...extra}
    />,
  );
  const block = (id: string) =>
    r.container.querySelector(`.strip-cal[data-anchor-id="${id}"]`) as HTMLElement;
  return { r, block, onMoveAnchor, onSelectAnchor };
}

const pointer = { pointerId: 1, button: 0, pointerType: "mouse" } as const;

describe("editable calendar block", () => {
  it("is a button that selects on click", () => {
    const { r, onSelectAnchor } = setup([dentist]);
    const b = r.getByRole("button", { name: /^Dentist 7:00 PM \(calendar\)/ });
    fireEvent.click(b);
    expect(onSelectAnchor).toHaveBeenCalledWith("Dentist");
  });

  it("stays a read-only picture when not editable or when the strip is read-only", () => {
    const a = setup([{ ...dentist, editable: false }]);
    expect(a.r.queryByRole("button", { name: /Dentist/ })).toBeNull();
    expect(a.r.getByLabelText(/^Dentist 7:00 PM \(calendar\)$/)).toBeTruthy();
    cleanup();
    const b = setup([dentist], { readOnly: true });
    expect(b.r.queryByRole("button", { name: /Dentist/ })).toBeNull();
  });

  it("drags with a 15-minute snap and a live label, committing once on drop", () => {
    const { r, block, onMoveAnchor, onSelectAnchor } = setup([dentist]);
    const el = block("Dentist");
    fireEvent.pointerDown(el, { ...pointer, clientX: 500 });
    fireEvent.pointerMove(el, { ...pointer, clientX: 537 }); // +37 min -> 19:37 -> 19:30
    expect(r.getByText("7:00 PM → 7:30 PM")).toBeTruthy();
    expect(parseFloat(el.style.left)).toBeCloseTo(((19 * 60 + 30 - FRAME_START) / SPAN) * 100, 2);
    expect(onMoveAnchor).not.toHaveBeenCalled();
    fireEvent.pointerUp(el, { ...pointer, clientX: 537 });
    expect(onMoveAnchor).toHaveBeenCalledTimes(1);
    expect(onMoveAnchor).toHaveBeenCalledWith("Dentist", "19:30");
    fireEvent.click(el); // the click that follows a drag is not a selection
    expect(onSelectAnchor).not.toHaveBeenCalled();
  });

  it("clamps the drop to the frame and keeps the whole block inside it", () => {
    const { block, onMoveAnchor } = setup([dentist]);
    const el = block("Dentist");
    fireEvent.pointerDown(el, { ...pointer, clientX: 500 });
    fireEvent.pointerMove(el, { ...pointer, clientX: 5000 });
    fireEvent.pointerUp(el, { ...pointer, clientX: 5000 });
    expect(onMoveAnchor).toHaveBeenLastCalledWith("Dentist", "23:15");
    fireEvent.pointerDown(el, { ...pointer, clientX: 500 });
    fireEvent.pointerMove(el, { ...pointer, clientX: -5000 });
    fireEvent.pointerUp(el, { ...pointer, clientX: -5000 });
    expect(onMoveAnchor).toHaveBeenLastCalledWith("Dentist", "07:30");
  });

  it("a press without travel is a click, not a move", () => {
    const { block, onMoveAnchor, onSelectAnchor } = setup([dentist]);
    const el = block("Dentist");
    fireEvent.pointerDown(el, { ...pointer, clientX: 500 });
    fireEvent.pointerUp(el, { ...pointer, clientX: 501 });
    fireEvent.click(el);
    expect(onMoveAnchor).not.toHaveBeenCalled();
    expect(onSelectAnchor).toHaveBeenCalledWith("Dentist");
  });

  it("arrow keys move by 15 minutes, shift by an hour", () => {
    const { block, onMoveAnchor } = setup([dentist]);
    fireEvent.keyDown(block("Dentist"), { key: "ArrowRight" });
    expect(onMoveAnchor).toHaveBeenLastCalledWith("Dentist", "19:15");
    fireEvent.keyDown(block("Dentist"), { key: "ArrowLeft", shiftKey: true });
    expect(onMoveAnchor).toHaveBeenLastCalledWith("Dentist", "18:00");
  });

  it("a skipped event selects but never drags", () => {
    const { block, onMoveAnchor, onSelectAnchor } = setup([{ ...dentist, counted: false }]);
    const el = block("Dentist");
    fireEvent.pointerDown(el, { ...pointer, clientX: 500 });
    fireEvent.pointerMove(el, { ...pointer, clientX: 560 });
    fireEvent.pointerUp(el, { ...pointer, clientX: 560 });
    expect(onMoveAnchor).not.toHaveBeenCalled();
    fireEvent.click(el);
    expect(onSelectAnchor).toHaveBeenCalledWith("Dentist");
  });

  it("marks an edited event and keeps the selected treatment", () => {
    const { block } = setup([{ ...dentist, edited: true }], { selectedAnchorId: "Dentist" });
    expect(block("Dentist").classList.contains("strip-cal--edited")).toBe(true);
    expect(block("Dentist").classList.contains("strip-cal--selected")).toBe(true);
    expect(block("Dentist").getAttribute("aria-label")).toMatch(/edited/);
  });

  it("keeps the outside-label rule for a short editable block", () => {
    const { r } = setup([{ ...dentist, durationMin: 15 }]);
    const label = r.container.querySelector(".strip-cal__label") as HTMLElement;
    expect(label.dataset.fit).toBe("right");
  });
});
