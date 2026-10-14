/* Anchor drag on the Day strip. jsdom has no layout, so the lane's width is
   stubbed: the 07:30–23:45 frame is 975 minutes drawn 975px wide, i.e. 1px per
   minute, so a pixel delta is a minute delta. The strip's ruler is the frame
   itself (Planning 2a), so blocks are positioned as (min - frameStart) / span. */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render } from "@testing-library/preact";
import { DayStrip, type StripAnchor } from "./DayStrip";

const FRAME_START = 7 * 60 + 30;
const FRAME_END = 23 * 60 + 45;
const SPAN = FRAME_END - FRAME_START;

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

const anchors: StripAnchor[] = [
  { id: "Sudsing", name: "Sudsing", start: "17:45", end: "18:15", counted: true, reason: null, blocks: 1, movable: true },
  { id: "Night Routine", name: "Night Routine", start: "23:00", end: "23:45", counted: true, reason: null, blocks: 2, movable: true },
  { id: "Foods Dinner", name: "Foods Dinner", start: "18:00", end: "20:30", counted: true, reason: null, blocks: 2, movable: true },
  { id: "Dentist", name: "Dentist", start: "10:00", end: "10:30", counted: true, reason: null, blocks: 1, movable: false },
  { id: "Morning Routine", name: "Morning Routine", start: "07:45", end: "09:05", counted: false, reason: "skipped", blocks: 3, movable: false },
];

function setup(overrides: Partial<Parameters<typeof DayStrip>[0]> = {}) {
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
      onSelectAnchor={onSelectAnchor}
      onMoveAnchor={onMoveAnchor}
      {...overrides}
    />,
  );
  const block = (id: string) => r.container.querySelector(`[data-anchor-id="${id}"]`) as HTMLElement;
  return { r, block, onMoveAnchor, onSelectAnchor };
}

const pointer = { pointerId: 1, button: 0, pointerType: "mouse" } as const;

describe("anchor drag", () => {
  it("moves a counted anchor with the pointer, snapping to 15 minutes, with a live label", () => {
    const { r, block, onMoveAnchor, onSelectAnchor } = setup();
    const el = block("Sudsing");
    fireEvent.pointerDown(el, { ...pointer, clientX: 500 });
    fireEvent.pointerMove(el, { ...pointer, clientX: 530 });
    // +30px = +30min: 17:45 -> 18:15, shown while dragging
    expect(r.getByText("5:45 PM → 6:15 PM")).toBeTruthy();
    expect(parseFloat(el.style.left)).toBeCloseTo(((18 * 60 + 15 - FRAME_START) / SPAN) * 100, 2);
    expect(onMoveAnchor).not.toHaveBeenCalled();
    fireEvent.pointerUp(el, { ...pointer, clientX: 530 });
    expect(onMoveAnchor).toHaveBeenCalledTimes(1);
    expect(onMoveAnchor).toHaveBeenCalledWith("Sudsing", "18:15");
    expect(r.queryByText("5:45 PM → 6:15 PM")).toBeNull();
    // the click the browser fires after a drag must not select the anchor
    fireEvent.click(el);
    expect(onSelectAnchor).not.toHaveBeenCalled();
  });

  it("snaps an in-between pointer position to the nearest 15 minutes", () => {
    const { block, onMoveAnchor } = setup();
    const el = block("Sudsing");
    fireEvent.pointerDown(el, { ...pointer, clientX: 100 });
    fireEvent.pointerMove(el, { ...pointer, clientX: 122 }); // 17:45 + 22 = 18:07 -> 18:00
    fireEvent.pointerUp(el, { ...pointer, clientX: 122 });
    expect(onMoveAnchor).toHaveBeenCalledWith("Sudsing", "18:00");
  });

  it("clamps to the frame: the block never leaves the day frame", () => {
    const { block, onMoveAnchor } = setup();
    const dinner = block("Foods Dinner"); // 18:00-20:30, so the latest start is 21:15
    fireEvent.pointerDown(dinner, { ...pointer, clientX: 900 });
    fireEvent.pointerMove(dinner, { ...pointer, clientX: -4000 });
    fireEvent.pointerUp(dinner, { ...pointer, clientX: -4000 });
    expect(onMoveAnchor).toHaveBeenLastCalledWith("Foods Dinner", "07:30");

    fireEvent.pointerDown(dinner, { ...pointer, clientX: 100 });
    fireEvent.pointerMove(dinner, { ...pointer, clientX: 4000 });
    fireEvent.pointerUp(dinner, { ...pointer, clientX: 4000 });
    expect(onMoveAnchor).toHaveBeenLastCalledWith("Foods Dinner", "21:15");
  });

  it("treats a press with no real movement as a click that selects the anchor", () => {
    const { r, block, onMoveAnchor, onSelectAnchor } = setup();
    const el = block("Sudsing");
    fireEvent.pointerDown(el, { ...pointer, clientX: 500 });
    fireEvent.pointerMove(el, { ...pointer, clientX: 502 }); // under the 4px threshold
    expect(r.queryByText(/→/)).toBeNull();
    fireEvent.pointerUp(el, { ...pointer, clientX: 502 });
    fireEvent.click(el);
    expect(onMoveAnchor).not.toHaveBeenCalled();
    expect(onSelectAnchor).toHaveBeenCalledWith("Sudsing");
  });

  it("does not call onMoveAnchor when the drag ends back on the original time", () => {
    const { block, onMoveAnchor, onSelectAnchor } = setup();
    const el = block("Sudsing");
    fireEvent.pointerDown(el, { ...pointer, clientX: 500 });
    fireEvent.pointerMove(el, { ...pointer, clientX: 560 });
    fireEvent.pointerMove(el, { ...pointer, clientX: 500 });
    fireEvent.pointerUp(el, { ...pointer, clientX: 500 });
    expect(onMoveAnchor).not.toHaveBeenCalled();
    fireEvent.click(el);
    expect(onSelectAnchor).not.toHaveBeenCalled(); // it was still a drag gesture
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

  it("does nothing at the frame edge", () => {
    const late: StripAnchor = { ...anchors[1], start: "23:00" }; // 45min block: 23:00 is the latest legal start
    const { block, onMoveAnchor } = setup({ anchors: [late] });
    const el = block("Night Routine");
    fireEvent.keyDown(el, { key: "ArrowRight", shiftKey: true });
    fireEvent.keyDown(el, { key: "ArrowRight" });
    expect(onMoveAnchor).not.toHaveBeenCalled();
    fireEvent.keyDown(el, { key: "ArrowLeft" });
    expect(onMoveAnchor).toHaveBeenCalledWith("Night Routine", "22:45");
  });

  it("leaves Enter and Space to select the anchor, keeping the aria-label", () => {
    const { block, onSelectAnchor } = setup();
    const el = block("Sudsing");
    expect(fireEvent.keyDown(el, { key: "Enter" })).toBe(true); // not default-prevented
    expect(fireEvent.keyDown(el, { key: " " })).toBe(true);
    fireEvent.click(el); // what the browser does for Enter/Space on a button
    expect(onSelectAnchor).toHaveBeenCalledWith("Sudsing");
    expect(el.getAttribute("aria-label")).toBe("Sudsing 5:45 PM · 1");
  });
});

describe("anchors that must not move", () => {
  it("ignores pointer and keyboard on calendar-sourced and not-counted anchors", () => {
    const { r, block, onMoveAnchor, onSelectAnchor } = setup();
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
    expect(onSelectAnchor).toHaveBeenCalledTimes(2);
    expect(r.queryByText(/→/)).toBeNull();
    expect(block("Morning Routine").classList.contains("strip-anchor--not-counted")).toBe(true);
    expect(block("Sudsing").classList.contains("strip-anchor--movable")).toBe(true);
  });

  it("is inert without an onMoveAnchor handler", () => {
    const { block, onSelectAnchor } = setup({ onMoveAnchor: undefined });
    const el = block("Sudsing");
    fireEvent.pointerDown(el, { ...pointer, clientX: 100 });
    fireEvent.pointerMove(el, { ...pointer, clientX: 200 });
    fireEvent.pointerUp(el, { ...pointer, clientX: 200 });
    fireEvent.click(el);
    expect(onSelectAnchor).toHaveBeenCalledWith("Sudsing");
    expect(el.classList.contains("strip-anchor--movable")).toBe(false);
  });
});

describe("Planning 2a timeline geometry", () => {
  it("positions blocks against the frame span, not the whole day", () => {
    const { block } = setup();
    const sudsing = block("Sudsing");
    expect(parseFloat(sudsing.style.left)).toBeCloseTo(((17 * 60 + 45 - FRAME_START) / SPAN) * 100, 2);
    expect(parseFloat(sudsing.style.width)).toBeCloseTo((30 / SPAN) * 100, 2);
    // A block is as wide as its real start→end span, not blocks * 30.
    const dinner = block("Foods Dinner");
    expect(parseFloat(dinner.style.width)).toBeCloseTo((150 / SPAN) * 100, 2);
    // Night Routine ends exactly on the frame end.
    const night = block("Night Routine");
    expect(parseFloat(night.style.left) + parseFloat(night.style.width)).toBeCloseTo(100, 2);
  });

  it("lays the anchors into two sub-rows: MR/Live/Night row 0, Breakfast/Sudsing/Dinner row 1", () => {
    const rows: StripAnchor[] = [
      { id: "Morning Routine", name: "Morning Routine", start: "07:45", end: "09:05", counted: true, reason: null, blocks: 3 },
      { id: "Foods Breakfast", name: "Foods Breakfast", start: "08:30", end: "13:00", counted: true, reason: null, blocks: 2 },
      { id: "Live", name: "Live", start: "12:00", end: "20:00", counted: true, reason: null, blocks: 1 },
      { id: "Sudsing", name: "Sudsing", start: "17:45", end: "18:15", counted: true, reason: null, blocks: 1 },
      { id: "Foods Dinner", name: "Foods Dinner", start: "18:00", end: "20:30", counted: true, reason: null, blocks: 2 },
      { id: "Night Routine", name: "Night Routine", start: "23:00", end: "23:45", counted: true, reason: null, blocks: 2 },
    ];
    const { block } = setup({ anchors: rows });
    const top = (id: string) => block(id).style.top;
    expect(top("Morning Routine")).toBe("6px");
    expect(top("Live")).toBe("6px");
    expect(top("Night Routine")).toBe("6px");
    expect(top("Foods Breakfast")).toBe("52px");
    expect(top("Sudsing")).toBe("52px");
    expect(top("Foods Dinner")).toBe("52px");
  });

  it("packs overlapping anchors without hiding either: Sudsing and Dinner share a row", () => {
    const rows: StripAnchor[] = [
      { id: "Live", name: "Live", start: "12:00", end: "20:00", counted: true, reason: null, blocks: 1 },
      { id: "Sudsing", name: "Sudsing", start: "17:45", end: "18:15", counted: true, reason: null, blocks: 1 },
      { id: "Foods Dinner", name: "Foods Dinner", start: "18:00", end: "20:30", counted: true, reason: null, blocks: 2 },
    ];
    const { block } = setup({ anchors: rows });
    const sudsing = block("Sudsing");
    const dinner = block("Foods Dinner");
    // Live occupies row 0 across 18:00, so the pair has to share row 1.
    expect(sudsing.style.top).toBe("52px");
    expect(dinner.style.top).toBe("52px");
    const left = (el: HTMLElement) => parseFloat(el.style.left);
    const widthPx = (el: HTMLElement) => (parseFloat(el.style.width) / 100) * SPAN;
    // No overlap: Sudsing's visible right edge sits before Dinner's left edge.
    expect(left(sudsing) + parseFloat(sudsing.style.width)).toBeLessThan(left(dinner));
    // Both names stay legible (the 56px minimum), never one hidden behind the other.
    expect(widthPx(sudsing)).toBeGreaterThanOrEqual(55);
    expect(widthPx(dinner)).toBeGreaterThanOrEqual(55);
    expect(sudsing.querySelector(".strip-anchor__name")?.textContent).toBe("Sudsing");
    expect(dinner.querySelector(".strip-anchor__name")?.textContent).toBe("Foods Dinner");
    // The true times are preserved in the accessible labels.
    expect(sudsing.getAttribute("aria-label")).toContain("5:45 PM");
    expect(dinner.getAttribute("aria-label")).toContain("6 PM");
    expect(sudsing.getAttribute("title")).toContain("5:45 PM");
    expect(dinner.getAttribute("title")).toContain("6 PM");
  });

  it("stagger-packs three anchors that all collide on one row", () => {
    const rows: StripAnchor[] = [
      { id: "Live", name: "Live", start: "12:00", end: "22:00", counted: true, reason: null, blocks: 1 },
      { id: "Sudsing", name: "Sudsing", start: "17:45", end: "18:15", counted: true, reason: null, blocks: 1 },
      { id: "Foods Breakfast", name: "Foods Breakfast", start: "17:50", end: "19:00", counted: true, reason: null, blocks: 2 },
      { id: "Foods Dinner", name: "Foods Dinner", start: "18:00", end: "20:30", counted: true, reason: null, blocks: 2 },
    ];
    const { block } = setup({ anchors: rows });
    const order = ["Sudsing", "Foods Breakfast", "Foods Dinner"].map((id) => block(id));
    for (const el of order) expect(el.style.top).toBe("52px");
    const left = (el: HTMLElement) => parseFloat(el.style.left);
    const right = (el: HTMLElement) => left(el) + parseFloat(el.style.width);
    // Every pair is disjoint and every block clears the legibility minimum.
    expect(right(order[0])).toBeLessThan(left(order[1]));
    expect(right(order[1])).toBeLessThan(left(order[2]));
    for (const el of order) {
      expect((parseFloat(el.style.width) / 100) * SPAN).toBeGreaterThanOrEqual(55);
      expect(el.querySelector(".strip-anchor__name")?.textContent).toBeTruthy();
    }
  });

  it("shows the time only for a block under two hours, and the item count above it", () => {
    const { r, block } = setup();
    // Sudsing is 30 min: no room for the meta line.
    expect(block("Sudsing").querySelector(".strip-anchor__meta")).toBeNull();
    expect(block("Sudsing").querySelector(".strip-anchor__name")).toBeTruthy();
    // Foods Dinner is 2.5h and shows its time and item count.
    expect(block("Foods Dinner").querySelector(".strip-anchor__meta")?.textContent).toBe(
      "6:00p · 2 items",
    );
    expect(r.container.querySelector('[aria-label="Anchors"]')).toBeTruthy();
  });

  it("marks the selected anchor", () => {
    const { block } = setup({ selectedAnchorId: "Sudsing" });
    expect(block("Sudsing").classList.contains("strip-anchor--selected")).toBe(true);
    expect(block("Foods Dinner").classList.contains("strip-anchor--selected")).toBe(false);
  });

  it("renders a 90px lane label column with hour ticks inside the frame", () => {
    const { r } = setup({
      calendar: [{ id: "standup", name: "Standup", start: "09:30", durationMin: 30, counted: true }],
    });
    const labels = Array.from(r.container.querySelectorAll(".day-strip__lane-label")).map(
      (el) => el.textContent,
    );
    expect(labels).toContain("Calendar");
    expect(labels).toContain("Mint");
    expect(labels).toContain("Anchors");
    const ticks = Array.from(r.container.querySelectorAll(".day-strip__tick")).map((el) => el.textContent);
    expect(ticks[0]).toBe("8a");
    expect(ticks[ticks.length - 1]).toBe("11p");
  });
});

describe("calendar lane", () => {
  it("draws today's calendar events read-only with the label to the right of the block", () => {
    const { r } = setup({
      calendar: [
        { id: "phep", name: "PHEP sync", start: "15:00", durationMin: 60, counted: true },
        { id: "standup", name: "Standup", start: "09:30", durationMin: 30, counted: false },
      ],
    });
    const lane = r.container.querySelector('[aria-label="Calendar"]');
    expect(lane).toBeTruthy();
    expect(r.getByLabelText(/^PHEP sync 3(:00)? PM \(calendar\)$/)).toBeTruthy();
    expect(r.getByLabelText(/^Standup 9:30 AM \(calendar\)$/)).toBeTruthy();
    // Not a button: calendar rows are not editable or movable here.
    expect(lane?.querySelector("button")).toBeNull();
    // The label sits outside the block, at block-end + 8px.
    const label = lane?.querySelector(".strip-cal__label") as HTMLElement;
    expect(label.textContent).toContain("Standup 9:30a");
    expect(label.style.left).toContain("calc(");
  });

  it("clips an event that straddles the frame edge and drops one that is wholly outside", () => {
    const { r } = setup({
      calendar: [
        { id: "edge", name: "Edge", start: "07:00", durationMin: 60, counted: true },
        { id: "before", name: "Before", start: "06:00", durationMin: 30, counted: true },
      ],
    });
    const edge = r.container.querySelector('[aria-label^="Edge"]') as HTMLElement;
    expect(parseFloat(edge.style.left)).toBeCloseTo(0, 2);
    expect(parseFloat(edge.style.width)).toBeCloseTo((30 / SPAN) * 100, 2);
    expect(r.container.querySelector('[aria-label^="Before"]')).toBeNull();
  });

  it("marks a work-class event with the Mint-yellow modifier, leaving fixed blue", () => {
    const { r } = setup({
      calendar: [
        { id: "jim", name: "Jim Meet", start: "13:00", durationMin: 45, counted: true, capacityClass: "work" },
        { id: "phep", name: "PHEP sync", start: "14:45", durationMin: 45, counted: true, capacityClass: "fixed" },
      ],
    });
    const work = r.container.querySelector('[aria-label^="Jim Meet"]') as HTMLElement;
    const fixed = r.container.querySelector('[aria-label^="PHEP sync"]') as HTMLElement;
    expect(work.classList.contains("strip-cal--work")).toBe(true);
    expect(fixed.classList.contains("strip-cal--work")).toBe(false);
  });

  it("omits the lane when there are no events", () => {
    const { r } = setup({ calendar: [] });
    expect(r.container.querySelector('[aria-label="Calendar"]')).toBeNull();
  });
});

describe("mint lane", () => {
  it("shows the hatched Off today lane when there are no sessions", () => {
    const { r } = setup({ sessions: [] });
    const lane = r.container.querySelector('[aria-label="Mint sessions"]') as HTMLElement;
    expect(lane.classList.contains("day-strip__lane--mint-off")).toBe(true);
    expect(r.getByText("Off today")).toBeTruthy();
  });
});
