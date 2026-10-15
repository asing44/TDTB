/* Every drawn anchor block shows its name: inside when it fits, otherwise in
   an outside label (right, then left, then a stacked row under the blocks)
   that never leaves the lane and never overlaps another label or block.
   jsdom has no layout, so the lane is stubbed 700px wide and the label
   extents are read from the data-x0/data-x1 px attributes the strip emits;
   the real-browser measurement is done separately with render_check. */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render } from "@testing-library/preact";
import { DayStrip, type StripAnchor } from "./day-setup/DayStrip";

const LANE_W = 700;

beforeEach(() => {
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({
    x: 0, y: 0, left: 0, top: 0, right: LANE_W, bottom: 96, width: LANE_W, height: 96,
    toJSON: () => ({}),
  });
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

// Frame 1:15 PM - 11:45 PM: the operator screenshot with the Your day Start
// field set to 1:15 PM, where the late anchors crowd into narrow stubs.
const lateFrame: StripAnchor[] = [
  { id: "Morning Routine", name: "Morning Routine", start: "13:15", counted: false, reason: "skipped", blocks: 3, movable: false },
  { id: "Live", name: "Live", start: "14:00", counted: true, reason: null, blocks: 1, movable: true,
    window: { start: "12:00", end: "20:00" } },
  { id: "Foods Breakfast", name: "Foods Breakfast", start: "15:00", counted: true, reason: null, blocks: 2, movable: true },
  { id: "Sudsing", name: "Sudsing", start: "17:45", counted: true, reason: null, blocks: 1, movable: true },
  { id: "Foods Dinner", name: "Foods Dinner", start: "18:00", counted: true, reason: null, blocks: 2, movable: true },
  { id: "Night Routine", name: "Night Routine", start: "23:00", counted: true, reason: null, blocks: 2, movable: true },
];

// Frame 12:15 PM - 11:45 PM, as in the operator screenshot.
const crowded: StripAnchor[] = [
  { id: "Live", name: "Live", start: "12:30", counted: true, reason: null, blocks: 1, movable: true,
    window: { start: "12:30", end: "20:00" } },
  { id: "Foods Dinner", name: "Foods Dinner", start: "18:00", counted: true, reason: null, blocks: 1, movable: true },
  { id: "Sudsing", name: "Sudsing", start: "18:00", counted: true, reason: null, blocks: 1, movable: true },
  { id: "Foods Breakfast", name: "Foods Breakfast", start: "18:15", counted: true, reason: null, blocks: 1, movable: true },
  { id: "Night Routine", name: "Night Routine", start: "23:15", counted: true, reason: null, blocks: 1, movable: true },
  { id: "Morning Routine", name: "Morning Routine", start: "23:00", counted: false, reason: "skipped", blocks: 1, movable: false },
];

function strip(
  anchors: StripAnchor[],
  selectedAnchorId: string | null = "Morning Routine",
  frame: { start: string; end: string } = { start: "12:15", end: "23:45" },
) {
  return render(
    <DayStrip
      frameStart={frame.start}
      frameEnd={frame.end}
      sessions={[]}
      selectedIds={[]}
      selectableIds={[]}
      onSelectIds={() => {}}
      onToggleSession={() => {}}
      anchors={anchors}
      onSelectAnchor={() => {}}
      onMoveAnchor={() => {}}
      selectedAnchorId={selectedAnchorId}
    />,
  );
}

const labels = (c: Element) =>
  Array.from(c.querySelectorAll<HTMLElement>(".strip-anchor__label"));
const px = (el: HTMLElement) => ({ x0: Number(el.dataset.x0), x1: Number(el.dataset.x1) });

describe("anchor outside labels", () => {
  it("gives every drawn anchor a visible name: in-block or an outside label", () => {
    const r = strip(crowded);
    for (const a of crowded) {
      const block = r.container.querySelector<HTMLElement>(`[data-anchor-id="${a.id}"]`);
      expect(block, a.id).toBeTruthy();
      const inside = block?.querySelector(".strip-anchor__name")?.textContent === a.name;
      const outside = labels(r.container).some((l) => l.dataset.labelFor === a.id && l.textContent === a.name);
      expect(inside || outside, `${a.id} has no visible name`).toBe(true);
    }
  });

  it("keeps every label inside the lane", () => {
    const r = strip(crowded);
    expect(labels(r.container).length).toBeGreaterThan(0);
    for (const l of labels(r.container)) {
      const { x0, x1 } = px(l);
      expect(x0, l.textContent ?? "").toBeGreaterThanOrEqual(0);
      expect(x1, l.textContent ?? "").toBeLessThanOrEqual(LANE_W);
    }
  });

  it("never overlaps two labels, or a label and a block, on the same band", () => {
    const r = strip(crowded);
    const ls = labels(r.container);
    const blocks = Array.from(r.container.querySelectorAll<HTMLElement>("[data-anchor-id]"));
    const overlap = (a: { x0: number; x1: number }, b: { x0: number; x1: number }) =>
      a.x0 < b.x1 && b.x0 < a.x1;
    ls.forEach((a, i) => {
      ls.slice(i + 1).forEach((b) => {
        if (a.dataset.band !== b.dataset.band) return;
        expect(overlap(px(a), px(b)), `${a.textContent} vs ${b.textContent}`).toBe(false);
      });
      blocks.forEach((blk) => {
        if (blk.dataset.anchorId === a.dataset.labelFor) return;
        if (a.dataset.band !== blk.dataset.band) return;
        const left = (parseFloat(blk.style.left) / 100) * LANE_W;
        const right = left + (parseFloat(blk.style.width) / 100) * LANE_W;
        expect(overlap(px(a), { x0: left, x1: right }), `${a.textContent} over ${blk.dataset.anchorId}`).toBe(false);
      });
    });
  });

  it("dims the label of a not-counted block and keeps the tooltip on the block", () => {
    const r = strip(crowded);
    const l = labels(r.container).find((x) => x.dataset.labelFor === "Morning Routine");
    expect(l?.classList.contains("strip-anchor__label--not-counted")).toBe(true);
    const block = r.container.querySelector('[data-anchor-id="Morning Routine"]');
    expect(block?.getAttribute("title")).toContain("Morning Routine");
  });

  it("does not label the window track and adds no label for a block whose name fits inside", () => {
    const r = strip([
      { id: "Focus", name: "Focus", start: "13:00", counted: true, reason: null, blocks: 6, movable: true },
    ], null);
    expect(labels(r.container)).toHaveLength(0);
    expect(r.container.querySelector(".strip-anchor__window")).toBeNull();
    expect(r.container.querySelector(".strip-anchor__name")?.textContent).toBe("Focus");
  });

  it("flips a late block's label to the left so it never leaves the lane", () => {
    const r = strip(crowded);
    const l = labels(r.container).find((x) => x.dataset.labelFor === "Night Routine");
    expect(l?.dataset.fit).toBe("left");
  });
});

/* The operator screenshot: Start set to 1:15 PM crowds the late anchors into
   narrow stubs. Anchors now behave like calendar items: a block at or above
   the in-block floor (CAL_MIN_INSIDE_PX, shared with the calendar lane) draws
   its name INSIDE, truncated with an ellipsis; only a narrower stub keeps the
   outside label so no anchor is ever anonymous. */
describe("anchor names inside the block", () => {
  const FLOOR_PX = 44; // CAL_MIN_INSIDE_PX
  const widthPx = (el: HTMLElement) => (parseFloat(el.style.width) / 100) * LANE_W;
  const blockOf = (r: ReturnType<typeof strip>, id: string) =>
    r.container.querySelector<HTMLElement>(`[data-anchor-id="${id}"]`)!;
  const outsideOf = (r: ReturnType<typeof strip>, id: string) =>
    labels(r.container).find((l) => l.dataset.labelFor === id);
  const late = () => strip(lateFrame, "Morning Routine", { start: "13:15", end: "23:45" });

  it("draws the (truncated) name inside every block at or above the floor, never an outside label", () => {
    const r = late();
    for (const a of lateFrame) {
      const block = blockOf(r, a.id);
      const w = widthPx(block);
      if (w < FLOOR_PX) continue;
      const name = block.querySelector<HTMLElement>(".strip-anchor__name");
      expect(name?.textContent, a.id).toBe(a.name);
      const fits = w >= a.name.length * 6.6 + 20;
      // data-fit=clip is the ellipsis marker; the overflow-hidden/ellipsis
      // styling itself lives on the strip-anchor__name class.
      expect(name?.dataset.fit, a.id).toBe(fits ? "inside" : "clip");
      expect(outsideOf(r, a.id), a.id).toBeUndefined();
      if (!fits) expect(name!.scrollWidth <= name!.clientWidth, a.id).toBe(true);
    }
  });

  it("keeps the outside label only for a block under the floor", () => {
    const r = late();
    for (const a of lateFrame) {
      const block = blockOf(r, a.id);
      if (widthPx(block) >= FLOOR_PX) continue;
      expect(block.querySelector(".strip-anchor__name"), a.id).toBeNull();
      expect(outsideOf(r, a.id)?.textContent, a.id).toBe(a.name);
    }
    // The fixture actually exercises both sides of the floor.
    const widths = lateFrame.map((a) => widthPx(blockOf(r, a.id)));
    expect(widths.some((w) => w >= FLOOR_PX)).toBe(true);
    expect(widths.some((w) => w < FLOOR_PX)).toBe(true);
  });

  it("keeps the full name in title and aria-label when the in-block name truncates", () => {
    const r = late();
    for (const a of lateFrame) {
      const block = blockOf(r, a.id);
      expect(block.getAttribute("title"), a.id).toContain(a.name);
      expect(block.getAttribute("aria-label"), a.id).toContain(a.name);
    }
  });

  it("truncates a not-counted dashed block inside too, keeping the dimming", () => {
    const r = late();
    const morning = blockOf(r, "Morning Routine");
    expect(widthPx(morning)).toBeGreaterThanOrEqual(FLOOR_PX);
    expect(morning.classList.contains("strip-anchor--not-counted")).toBe(true);
    expect(morning.querySelector<HTMLElement>(".strip-anchor__name")?.dataset.fit).toBe("clip");
    expect(outsideOf(r, "Morning Routine")).toBeUndefined();
  });
});
