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
  { id: "Sudsing", name: "Sudsing", start: "17:45", counted: true, reason: null, blocks: 2, movable: true },
  { id: "Night Routine", name: "Night Routine", start: "23:00", counted: true, reason: null, blocks: 2, movable: true },
  { id: "Foods Dinner", name: "Foods Dinner", start: "18:00", counted: true, reason: null, blocks: 2, movable: true },
  {
    id: "Live",
    name: "Live",
    start: "12:00",
    counted: true,
    reason: null,
    blocks: 1,
    movable: true,
    window: { start: "12:00", end: "20:00" },
  },
  { id: "Dentist", name: "Dentist", start: "10:00", counted: true, reason: null, blocks: 1, movable: false },
  { id: "Morning Routine", name: "Morning Routine", start: "07:45", counted: false, reason: "skipped", blocks: 3, movable: false },
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
    const dinner = block("Foods Dinner"); // 2 blocks = 60min, so the latest start is 22:45
    fireEvent.pointerDown(dinner, { ...pointer, clientX: 900 });
    fireEvent.pointerMove(dinner, { ...pointer, clientX: -4000 });
    fireEvent.pointerUp(dinner, { ...pointer, clientX: -4000 });
    expect(onMoveAnchor).toHaveBeenLastCalledWith("Foods Dinner", "07:30");

    fireEvent.pointerDown(dinner, { ...pointer, clientX: 100 });
    fireEvent.pointerMove(dinner, { ...pointer, clientX: 4000 });
    fireEvent.pointerUp(dinner, { ...pointer, clientX: 4000 });
    expect(onMoveAnchor).toHaveBeenLastCalledWith("Foods Dinner", "22:45");
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
    const late: StripAnchor = { ...anchors[1], start: "22:45" }; // 60min block: 22:45 is the latest legal start
    const { block, onMoveAnchor } = setup({ anchors: [late] });
    const el = block("Night Routine");
    fireEvent.keyDown(el, { key: "ArrowRight", shiftKey: true });
    fireEvent.keyDown(el, { key: "ArrowRight" });
    expect(onMoveAnchor).not.toHaveBeenCalled();
    fireEvent.keyDown(el, { key: "ArrowLeft" });
    expect(onMoveAnchor).toHaveBeenCalledWith("Night Routine", "22:30");
  });

  it("leaves Enter and Space to select the anchor, keeping the aria-label", () => {
    const { block, onSelectAnchor } = setup();
    const el = block("Sudsing");
    expect(fireEvent.keyDown(el, { key: "Enter" })).toBe(true); // not default-prevented
    expect(fireEvent.keyDown(el, { key: " " })).toBe(true);
    fireEvent.click(el); // what the browser does for Enter/Space on a button
    expect(onSelectAnchor).toHaveBeenCalledWith("Sudsing");
    expect(el.getAttribute("aria-label")).toBe("Sudsing 5:45 PM · 2 blocks");
  });
});

describe("anchors that must not move", () => {
  it("ignores pointer and keyboard on calendar-sourced anchors", () => {
    const { r, block, onMoveAnchor, onSelectAnchor } = setup();
    for (const id of ["Dentist"]) {
      const el = block(id);
      expect(el.classList.contains("strip-anchor--movable")).toBe(false);
      fireEvent.pointerDown(el, { ...pointer, clientX: 100 });
      fireEvent.pointerMove(el, { ...pointer, clientX: 300 });
      fireEvent.pointerUp(el, { ...pointer, clientX: 300 });
      fireEvent.keyDown(el, { key: "ArrowRight" });
      fireEvent.click(el);
    }
    expect(onMoveAnchor).not.toHaveBeenCalled();
    expect(onSelectAnchor).toHaveBeenCalledTimes(1);
    expect(r.queryByText(/→/)).toBeNull();
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
    // A block is as wide as its COUNTED duration: blocks × 30 minutes, not the
    // source window: Sudsing is 2 blocks = 60 minutes.
    expect(parseFloat(sudsing.style.width)).toBeCloseTo((60 / SPAN) * 100, 2);
    const dinner = block("Foods Dinner");
    expect(parseFloat(dinner.style.width)).toBeCloseTo((60 / SPAN) * 100, 2);
    // Live is 1 block = 30 minutes wide even though its window is 12:00→20:00.
    const live = block("Live");
    expect(parseFloat(live.style.width)).toBeCloseTo((30 / SPAN) * 100, 2);
    // Night Routine ends exactly on the frame end.
    const night = block("Night Routine");
    expect(parseFloat(night.style.left) + parseFloat(night.style.width)).toBeCloseTo(100, 2);
  });

  it("scales the block width with the block count, including today's override", () => {
    const widthPxFor = (blocks: number) => {
      const { r } = setup({
        anchors: [{ id: "Live", name: "Live", start: "12:00", counted: true, reason: null, blocks }],
      });
      const el = r.container.querySelector('[data-anchor-id="Live"]') as HTMLElement;
      return (parseFloat(el.style.width) / 100) * SPAN;
    };
    expect(widthPxFor(1)).toBeCloseTo(30, 1);
    expect(widthPxFor(3)).toBeCloseTo(90, 1);
  });

  it("draws a window-kind anchor's window as a light non-interactive track behind the counted block", () => {
    const { r, block } = setup();
    const track = r.container.querySelector('[data-anchor-window-id="Live"]') as HTMLElement;
    expect(track).toBeTruthy();
    expect(track.tagName).toBe("SPAN");
    expect(track.getAttribute("aria-hidden")).toBe("true");
    expect(parseFloat(track.style.left)).toBeCloseTo(((12 * 60 - FRAME_START) / SPAN) * 100, 2);
    expect(parseFloat(track.style.width)).toBeCloseTo((480 / SPAN) * 100, 2);
    expect(track.style.top).toBe("6px");
    // The solid block stays the interactive element and keeps the count width.
    const live = block("Live");
    expect(live.tagName).toBe("BUTTON");
    expect(parseFloat(live.style.width)).toBeCloseTo((30 / SPAN) * 100, 2);
    // Hard anchors have no window track.
    expect(r.container.querySelector('[data-anchor-window-id="Sudsing"]')).toBeNull();
  });

  it("lays the anchors into two sub-rows, moving an overlapped block to the free row", () => {
    const rows: StripAnchor[] = [
      { id: "Morning Routine", name: "Morning Routine", start: "07:45", counted: true, reason: null, blocks: 3 },
      { id: "Foods Breakfast", name: "Foods Breakfast", start: "08:30", counted: true, reason: null, blocks: 2 },
      { id: "Live", name: "Live", start: "12:00", counted: true, reason: null, blocks: 1, window: { start: "12:00", end: "20:00" } },
      { id: "Sudsing", name: "Sudsing", start: "17:45", counted: true, reason: null, blocks: 2 },
      { id: "Foods Dinner", name: "Foods Dinner", start: "18:00", counted: true, reason: null, blocks: 2 },
      { id: "Night Routine", name: "Night Routine", start: "23:00", counted: true, reason: null, blocks: 2 },
    ];
    const { block } = setup({ anchors: rows });
    const top = (id: string) => block(id).style.top;
    expect(top("Morning Routine")).toBe("6px");
    expect(top("Live")).toBe("6px");
    expect(top("Night Routine")).toBe("6px");
    expect(top("Foods Breakfast")).toBe("52px");
    expect(top("Sudsing")).toBe("52px");
    // Sudsing 17:45×2 and Dinner 18:00×2 overlap on their shared preferred
    // row, so Dinner takes the free row 0: still never a visual overlap.
    expect(top("Foods Dinner")).toBe("6px");
  });

  it("packs the counted spans without hiding either block", () => {
    const rows: StripAnchor[] = [
      { id: "Live", name: "Live", start: "12:00", counted: true, reason: null, blocks: 1, window: { start: "12:00", end: "20:00" } },
      { id: "Sudsing", name: "Sudsing", start: "17:45", counted: true, reason: null, blocks: 2 },
      { id: "Foods Dinner", name: "Foods Dinner", start: "18:00", counted: true, reason: null, blocks: 2 },
    ];
    const { block } = setup({ anchors: rows });
    const sudsing = block("Sudsing");
    const dinner = block("Foods Dinner");
    // Sudsing 17:45–18:45 and Dinner 18:00–19:00 overlap, so Dinner moves to
    // the free row 0 (Live's window track is non-interactive background).
    expect(sudsing.style.top).toBe("52px");
    expect(dinner.style.top).toBe("6px");
    const widthPx = (el: HTMLElement) => (parseFloat(el.style.width) / 100) * SPAN;
    // Widths are the counted duration, not the source window span.
    expect(widthPx(sudsing)).toBeCloseTo(60, 1);
    expect(widthPx(dinner)).toBeCloseTo(60, 1);
    expect(sudsing.querySelector(".strip-anchor__name")?.textContent).toBe("Sudsing");
    expect(dinner.querySelector(".strip-anchor__name")?.textContent).toBe("Foods Dinner");
    // The true times and counts are preserved in the accessible labels.
    expect(sudsing.getAttribute("aria-label")).toContain("5:45 PM · 2 blocks");
    expect(dinner.getAttribute("aria-label")).toContain("6 PM · 2 blocks");
    expect(sudsing.getAttribute("title")).toContain("5:45 PM · 2 blocks");
    expect(dinner.getAttribute("title")).toContain("6 PM · 2 blocks");
  });

  it("stagger-packs blocks that must share a row, never overlapping or hiding a name", () => {
    const rows: StripAnchor[] = [
      // Night Routine occupies row 0 across the collision, forcing all three
      // foods onto row 1, where they must be staggered sideways.
      { id: "Night Routine", name: "Night Routine", start: "17:00", counted: true, reason: null, blocks: 3 },
      { id: "Sudsing", name: "Sudsing", start: "17:45", counted: true, reason: null, blocks: 2 },
      { id: "Foods Breakfast", name: "Foods Breakfast", start: "17:50", counted: true, reason: null, blocks: 2 },
      { id: "Foods Dinner", name: "Foods Dinner", start: "18:00", counted: true, reason: null, blocks: 2 },
    ];
    const { block } = setup({ anchors: rows });
    const order = ["Sudsing", "Foods Breakfast", "Foods Dinner"].map((id) => block(id));
    for (const el of order) expect(el.style.top).toBe("52px");
    const left = (el: HTMLElement) => parseFloat(el.style.left);
    const right = (el: HTMLElement) => left(el) + parseFloat(el.style.width);
    // Every pair is disjoint with a visible gap.
    expect(right(order[0])).toBeLessThan(left(order[1]));
    expect(right(order[1])).toBeLessThan(left(order[2]));
    for (const el of order) {
      // The 28px legibility floor, never the old 56px exaggeration.
      expect((parseFloat(el.style.width) / 100) * SPAN).toBeGreaterThanOrEqual(27.9);
      expect((parseFloat(el.style.width) / 100) * SPAN).toBeLessThan(56);
      expect(el.querySelector(".strip-anchor__name")?.textContent).toBeTruthy();
    }
    // The full name, time and count stay in the accessible labels.
    expect(order[0].getAttribute("aria-label")).toContain("Sudsing 5:45 PM · 2 blocks");
    expect(order[1].getAttribute("aria-label")).toContain("Foods Breakfast 5:50 PM · 2 blocks");
    expect(order[2].getAttribute("title")).toContain("6 PM · 2 blocks");
  });

  it("shows the mono time · N blocks meta when it fits, keeping the count in the label otherwise", () => {
    const wide: StripAnchor[] = [
      { id: "Focus", name: "Focus", start: "13:00", counted: true, reason: null, blocks: 6 },
      { id: "Sudsing", name: "Sudsing", start: "17:45", counted: true, reason: null, blocks: 2 },
    ];
    const { r, block } = setup({ anchors: wide });
    // 6 blocks = 180px wide: the meta line fits and counts in blocks.
    expect(block("Focus").querySelector(".strip-anchor__meta")?.textContent).toBe("1:00p · 6 blocks");
    expect(block("Focus").querySelector(".strip-anchor__name")?.textContent).toBe("Focus");
    // Sudsing's 60 counted minutes cannot fit the meta line: the name is the
    // compact label; the full time and count live in title + aria-label.
    expect(block("Sudsing").querySelector(".strip-anchor__meta")).toBeNull();
    expect(block("Sudsing").querySelector(".strip-anchor__name")).toBeTruthy();
    expect(block("Sudsing").getAttribute("aria-label")).toBe("Sudsing 5:45 PM · 2 blocks");
    expect(block("Sudsing").getAttribute("title")).toContain("5:45 PM · 2 blocks");
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
  it("draws today's calendar events read-only; a block too narrow for its title labels it outside", () => {
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
    // Standup is a 30px block: too narrow for text, so the label sits outside
    // it, at block-end + 8px.
    const label = Array.from(lane?.querySelectorAll(".strip-cal__label") ?? []).find((el) =>
      el.textContent?.includes("Standup 9:30a"),
    ) as HTMLElement;
    expect(label.getAttribute("data-fit")).toBe("right");
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

describe("calendar labels", () => {
  const lane = (r: ReturnType<typeof setup>["r"]) =>
    r.container.querySelector('[aria-label="Calendar"]') as HTMLElement;
  const labelOf = (r: ReturnType<typeof setup>["r"], text: string) =>
    Array.from(lane(r).querySelectorAll(".strip-cal__label")).find((el) =>
      el.textContent?.includes(text),
    ) as HTMLElement;

  it("draws the title inside the block when it fits", () => {
    const { r } = setup({
      calendar: [{ id: "sync", name: "Standup", start: "09:00", durationMin: 150, counted: true }],
    });
    const label = labelOf(r, "Standup 9:00a");
    expect(label.getAttribute("data-fit")).toBe("inside");
    expect(label.parentElement?.classList.contains("strip-cal")).toBe(true);
  });

  it("ellipsizes inside a block that only partly fits the title", () => {
    const { r } = setup({
      calendar: [{ id: "phep", name: "PHEP quarterly sync", start: "15:00", durationMin: 60, counted: true }],
    });
    const label = labelOf(r, "PHEP quarterly sync");
    expect(label.getAttribute("data-fit")).toBe("clip");
    expect(label.parentElement?.classList.contains("strip-cal")).toBe(true);
  });

  it("puts a too-narrow block's label outside, flipped left when the right has no room", () => {
    const { r } = setup({
      calendar: [
        { id: "early", name: "Early", start: "08:00", durationMin: 30, counted: true },
        { id: "late", name: "Late wrap-up", start: "23:15", durationMin: 30, counted: true },
      ],
    });
    expect(labelOf(r, "Early").getAttribute("data-fit")).toBe("right");
    const late = labelOf(r, "Late wrap-up");
    expect(late.getAttribute("data-fit")).toBe("left");
    // Anchored by its right edge to the block's left edge: it cannot cross the lane end.
    expect(late.style.right).toContain("calc(");
    expect(late.style.left).toBe("");
    expect(late.parentElement).toBe(lane(r));
  });

  it("keeps the real start in the title of an event clipped at the frame edge", () => {
    const { r } = setup({
      calendar: [{ id: "edge", name: "Edge", start: "07:00", durationMin: 60, counted: true }],
    });
    expect(r.container.querySelector('[aria-label^="Edge"]')?.getAttribute("title")).toBe("Edge 7 AM");
  });

  it("keeps overlapping events on separate rows so labels never collide", () => {
    const { r } = setup({
      calendar: [
        { id: "a", name: "Alpha", start: "10:00", durationMin: 30, counted: true },
        { id: "b", name: "Bravo", start: "10:10", durationMin: 30, counted: true },
      ],
    });
    expect(labelOf(r, "Alpha").style.top).not.toBe(labelOf(r, "Bravo").style.top);
  });
});

describe("hidden anchors", () => {
  it("does not draw an off anchor and notes it, opening All blocks from the note", () => {
    const onShowAllBlocks = vi.fn();
    const { r } = setup({ onShowAllBlocks });
    expect(r.container.querySelector('[data-anchor-id="Morning Routine"]')).toBeNull();
    const note = r.getByRole("button", { name: /1 hidden today/ });
    expect(note.textContent).toContain("All blocks");
    fireEvent.click(note);
    expect(onShowAllBlocks).toHaveBeenCalledTimes(1);
  });

  it("does not draw a fully elapsed anchor either, but keeps live ones", () => {
    const { r, block } = setup({
      frameStart: "13:30",
      anchors: [
        { id: "Morning Routine", name: "Morning Routine", start: "13:30", counted: true, reason: null, blocks: 3, elapsed: true },
        { id: "Sudsing", name: "Sudsing", start: "17:45", counted: true, reason: null, blocks: 2, movable: true },
      ],
    });
    expect(r.container.querySelector('[data-anchor-id="Morning Routine"]')).toBeNull();
    expect(block("Sudsing")).toBeTruthy();
    expect(r.getByText(/1 hidden today/)).toBeTruthy();
  });

  it("shows no note when everything is live", () => {
    const { r } = setup({ anchors: anchors.filter((a) => a.counted) });
    expect(r.queryByText(/hidden today/)).toBeNull();
  });

  it("keeps the selected anchor drawn so its popover stays attached", () => {
    const { block } = setup({ selectedAnchorId: "Morning Routine" });
    expect(block("Morning Routine")).toBeTruthy();
  });

  it("never draws a live block narrower than 28px, even when four overlap", () => {
    const packed: StripAnchor[] = [
      { id: "Morning Routine", name: "Morning Routine", start: "10:00", counted: true, reason: null, blocks: 4 },
      { id: "Live", name: "Live", start: "10:05", counted: true, reason: null, blocks: 1 },
      { id: "Foods Breakfast", name: "Foods Breakfast", start: "10:10", counted: true, reason: null, blocks: 1 },
      { id: "Sudsing", name: "Sudsing", start: "10:15", counted: true, reason: null, blocks: 1 },
    ];
    const { r } = setup({ anchors: packed });
    const blocks = Array.from(r.container.querySelectorAll("[data-anchor-id]")) as HTMLElement[];
    expect(blocks).toHaveLength(4);
    for (const el of blocks) {
      expect((parseFloat(el.style.width) / 100) * SPAN).toBeGreaterThanOrEqual(27.9);
    }
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
