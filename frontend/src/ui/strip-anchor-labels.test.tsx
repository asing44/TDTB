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

function strip(anchors: StripAnchor[], selectedAnchorId: string | null = "Morning Routine") {
  return render(
    <DayStrip
      frameStart="12:15"
      frameEnd="23:45"
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
