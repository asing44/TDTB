/* Anchor drag on the Day strip. jsdom has no layout, so the lane's width is
   stubbed: 07:00-24:00 is 1020 minutes drawn 1020px wide, i.e. 1px per minute. */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render } from "@testing-library/preact";
import { DayStrip, type StripAnchor } from "./DayStrip";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

beforeEach(() => {
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({
    x: 0, y: 0, left: 0, top: 0, right: 1020, bottom: 34, width: 1020, height: 34,
    toJSON: () => ({}),
  });
});

const anchors: StripAnchor[] = [
  { id: "Sudsing", name: "Sudsing", start: "17:45", counted: true, reason: null, blocks: 1, movable: true },
  { id: "Night Routine", name: "Night Routine", start: "22:00", counted: true, reason: null, blocks: 2, movable: true },
  { id: "Dentist", name: "Dentist", start: "10:00", counted: true, reason: null, blocks: 1, movable: false },
  { id: "Morning Routine", name: "Morning Routine", start: "07:15", counted: false, reason: "outside frame", blocks: 3, movable: false },
];

function setup(overrides: Partial<Parameters<typeof DayStrip>[0]> = {}) {
  const onMoveAnchor = vi.fn();
  const onEditAnchor = vi.fn();
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
      onEditAnchor={onEditAnchor}
      onMoveAnchor={onMoveAnchor}
      {...overrides}
    />,
  );
  const block = (id: string) => r.container.querySelector(`[data-anchor-id="${id}"]`) as HTMLElement;
  return { r, block, onMoveAnchor, onEditAnchor };
}

const pointer = { pointerId: 1, button: 0, pointerType: "mouse" } as const;

describe("anchor drag", () => {
  it("moves a counted anchor with the pointer, snapping to 15 minutes, with a live label", () => {
    const { r, block, onMoveAnchor, onEditAnchor } = setup();
    const el = block("Sudsing");
    fireEvent.pointerDown(el, { ...pointer, clientX: 500 });
    fireEvent.pointerMove(el, { ...pointer, clientX: 530 });
    // +30px = +30min: 17:45 -> 18:15, shown while dragging
    expect(r.getByText("5:45 PM → 6:15 PM")).toBeTruthy();
    expect(parseFloat(el.style.left)).toBeCloseTo(((18 * 60 + 15 - 420) / 1020) * 100, 2);
    expect(onMoveAnchor).not.toHaveBeenCalled();
    fireEvent.pointerUp(el, { ...pointer, clientX: 530 });
    expect(onMoveAnchor).toHaveBeenCalledTimes(1);
    expect(onMoveAnchor).toHaveBeenCalledWith("Sudsing", "18:15");
    expect(r.queryByText("5:45 PM → 6:15 PM")).toBeNull();
    // the click the browser fires after a drag must not open the editor
    fireEvent.click(el);
    expect(onEditAnchor).not.toHaveBeenCalled();
  });

  it("snaps an in-between pointer position to the nearest 15 minutes", () => {
    const { block, onMoveAnchor } = setup();
    const el = block("Sudsing");
    fireEvent.pointerDown(el, { ...pointer, clientX: 100 });
    fireEvent.pointerMove(el, { ...pointer, clientX: 122 }); // 17:45 + 22 = 18:07 -> 18:00
    fireEvent.pointerUp(el, { ...pointer, clientX: 122 });
    expect(onMoveAnchor).toHaveBeenCalledWith("Sudsing", "18:00");
  });

  it("clamps to the ruler: the block never leaves 07:00-24:00", () => {
    const { block, onMoveAnchor } = setup();
    const left = block("Sudsing");
    fireEvent.pointerDown(left, { ...pointer, clientX: 900 });
    fireEvent.pointerMove(left, { ...pointer, clientX: -4000 });
    fireEvent.pointerUp(left, { ...pointer, clientX: -4000 });
    expect(onMoveAnchor).toHaveBeenLastCalledWith("Sudsing", "07:00");

    const night = block("Night Routine"); // 2 blocks = 60 min, so latest start is 23:00
    fireEvent.pointerDown(night, { ...pointer, clientX: 100 });
    fireEvent.pointerMove(night, { ...pointer, clientX: 4000 });
    fireEvent.pointerUp(night, { ...pointer, clientX: 4000 });
    expect(onMoveAnchor).toHaveBeenLastCalledWith("Night Routine", "23:00");
  });

  it("treats a press with no real movement as a click that opens the editor", () => {
    const { r, block, onMoveAnchor, onEditAnchor } = setup();
    const el = block("Sudsing");
    fireEvent.pointerDown(el, { ...pointer, clientX: 500 });
    fireEvent.pointerMove(el, { ...pointer, clientX: 502 }); // under the 4px threshold
    expect(r.queryByText(/→/)).toBeNull();
    fireEvent.pointerUp(el, { ...pointer, clientX: 502 });
    fireEvent.click(el);
    expect(onMoveAnchor).not.toHaveBeenCalled();
    expect(onEditAnchor).toHaveBeenCalledWith("Sudsing");
  });

  it("does not call onMoveAnchor when the drag ends back on the original time", () => {
    const { block, onMoveAnchor, onEditAnchor } = setup();
    const el = block("Sudsing");
    fireEvent.pointerDown(el, { ...pointer, clientX: 500 });
    fireEvent.pointerMove(el, { ...pointer, clientX: 560 });
    fireEvent.pointerMove(el, { ...pointer, clientX: 500 });
    fireEvent.pointerUp(el, { ...pointer, clientX: 500 });
    expect(onMoveAnchor).not.toHaveBeenCalled();
    fireEvent.click(el);
    expect(onEditAnchor).not.toHaveBeenCalled(); // it was still a drag gesture
  });

  it("Escape cancels an in-flight drag and restores the block", () => {
    const { r, block, onMoveAnchor } = setup();
    const el = block("Sudsing");
    const before = el.style.left;
    fireEvent.pointerDown(el, { ...pointer, clientX: 500 });
    fireEvent.pointerMove(el, { ...pointer, clientX: 560 });
    fireEvent.keyDown(el, { key: "Escape" });
    expect(r.queryByText(/→/)).toBeNull();
    expect(el.style.left).toBe(before);
    fireEvent.pointerUp(el, { ...pointer, clientX: 560 });
    expect(onMoveAnchor).not.toHaveBeenCalled();
  });

  it("ignores non-primary buttons", () => {
    const { block, onMoveAnchor } = setup();
    const el = block("Sudsing");
    fireEvent.pointerDown(el, { ...pointer, button: 2, clientX: 500 });
    fireEvent.pointerMove(el, { ...pointer, button: 2, clientX: 600 });
    fireEvent.pointerUp(el, { ...pointer, button: 2, clientX: 600 });
    expect(onMoveAnchor).not.toHaveBeenCalled();
  });
});

describe("anchor keyboard move", () => {
  it("moves 15 minutes with the arrows and an hour with Shift", () => {
    const { block, onMoveAnchor } = setup();
    const el = block("Sudsing");
    fireEvent.keyDown(el, { key: "ArrowRight" });
    expect(onMoveAnchor).toHaveBeenLastCalledWith("Sudsing", "18:00");
    fireEvent.keyDown(el, { key: "ArrowLeft" });
    expect(onMoveAnchor).toHaveBeenLastCalledWith("Sudsing", "17:30");
    fireEvent.keyDown(el, { key: "ArrowRight", shiftKey: true });
    expect(onMoveAnchor).toHaveBeenLastCalledWith("Sudsing", "18:45");
    fireEvent.keyDown(el, { key: "ArrowLeft", shiftKey: true });
    expect(onMoveAnchor).toHaveBeenLastCalledWith("Sudsing", "16:45");
  });

  it("announces the new time politely", () => {
    const { r, block } = setup();
    fireEvent.keyDown(block("Sudsing"), { key: "ArrowRight" });
    const status = r.container.querySelector('[data-testid="anchor-move-status"]')!;
    expect(status.getAttribute("role")).toBe("status");
    expect(status.textContent).toBe("Sudsing moved to 6 PM");
  });

  it("does nothing at the ruler edge", () => {
    const late: StripAnchor = { ...anchors[1], start: "23:00" }; // 2 blocks: 23:00 is the latest legal start
    const { block, onMoveAnchor } = setup({ anchors: [late] });
    const el = block("Night Routine");
    fireEvent.keyDown(el, { key: "ArrowRight", shiftKey: true });
    fireEvent.keyDown(el, { key: "ArrowRight" });
    expect(onMoveAnchor).not.toHaveBeenCalled();
    fireEvent.keyDown(el, { key: "ArrowLeft" });
    expect(onMoveAnchor).toHaveBeenCalledWith("Night Routine", "22:45");
  });

  it("leaves Enter and Space to open the editor, keeping the aria-label", () => {
    const { block, onEditAnchor } = setup();
    const el = block("Sudsing");
    expect(fireEvent.keyDown(el, { key: "Enter" })).toBe(true); // not default-prevented
    expect(fireEvent.keyDown(el, { key: " " })).toBe(true);
    fireEvent.click(el); // what the browser does for Enter/Space on a button
    expect(onEditAnchor).toHaveBeenCalledWith("Sudsing");
    expect(el.getAttribute("aria-label")).toBe("Sudsing 5:45 PM · 1");
  });
});

describe("anchors that must not move", () => {
  it("ignores pointer and keyboard on calendar-sourced and not-counted anchors", () => {
    const { r, block, onMoveAnchor, onEditAnchor } = setup();
    for (const id of ["Dentist", "Morning Routine"]) {
      const el = block(id);
      expect(el.classList.contains("strip-anchor--movable")).toBe(false);
      fireEvent.pointerDown(el, { ...pointer, clientX: 100 });
      fireEvent.pointerMove(el, { ...pointer, clientX: 300 });
      fireEvent.pointerUp(el, { ...pointer, clientX: 300 });
      fireEvent.keyDown(el, { key: "ArrowRight" });
      fireEvent.click(el);
    }
    expect(onMoveAnchor).not.toHaveBeenCalled();
    expect(onEditAnchor).toHaveBeenCalledTimes(2);
    expect(r.queryByText(/→/)).toBeNull();
    expect(block("Morning Routine").classList.contains("strip-anchor--not-counted")).toBe(true);
    expect(block("Sudsing").classList.contains("strip-anchor--movable")).toBe(true);
  });

  it("is inert without an onMoveAnchor handler", () => {
    const { block, onEditAnchor } = setup({ onMoveAnchor: undefined });
    const el = block("Sudsing");
    fireEvent.pointerDown(el, { ...pointer, clientX: 100 });
    fireEvent.pointerMove(el, { ...pointer, clientX: 200 });
    fireEvent.pointerUp(el, { ...pointer, clientX: 200 });
    fireEvent.click(el);
    expect(onEditAnchor).toHaveBeenCalledWith("Sudsing");
    expect(el.classList.contains("strip-anchor--movable")).toBe(false);
  });
});
