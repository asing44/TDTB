/* popoverPlacement.test — the pure viewport-fitting math behind the Sources
   details popover. Trigger rect and viewport are injected so the placement is
   deterministic and testable without layout. */

import { describe, expect, it } from "vitest";
import {
  computePopoverPlacement,
  POPOVER_EDGE,
  POPOVER_GAP,
} from "./popoverPlacement";

const viewport = (width: number, height: number) => ({ width, height });
const trigger = (top: number, left: number, width = 24, height = 20) => ({
  top,
  left,
  right: left + width,
  bottom: top + height,
  width,
  height,
});

describe("computePopoverPlacement", () => {
  it("opens upward for a trigger near the bottom when the panel fits", () => {
    const p = computePopoverPlacement(trigger(600, 20), viewport(900, 700), {
      width: 340,
      height: 500,
    });
    expect(p.placement).toBe("up");
    expect(p.top).toBe(600 - POPOVER_GAP - 500);
    expect(p.maxHeight).toBe(500);
    expect(p.left).toBe(20);
  });

  it("opens downward for a trigger near the top", () => {
    const p = computePopoverPlacement(trigger(20, 20), viewport(900, 700), {
      width: 340,
      height: 500,
    });
    expect(p.placement).toBe("down");
    expect(p.top).toBe(40 + POPOVER_GAP);
    expect(p.maxHeight).toBe(500);
  });

  it("clamps a too-tall panel to the larger side and the top edge", () => {
    const p = computePopoverPlacement(trigger(350, 20), viewport(900, 400), {
      width: 340,
      height: 500,
    });
    expect(p.placement).toBe("up");
    expect(p.top).toBe(POPOVER_EDGE);
    expect(p.top + p.maxHeight).toBeLessThanOrEqual(400 - POPOVER_EDGE);
    expect(p.maxHeight).toBe(350 - POPOVER_EDGE - POPOVER_GAP);
  });

  it("clamps horizontally so the panel stays inside the viewport", () => {
    const p = computePopoverPlacement(trigger(300, 700), viewport(900, 700), {
      width: 340,
      height: 200,
    });
    expect(p.left).toBe(900 - POPOVER_EDGE - 340);
    expect(p.left + 340).toBeLessThanOrEqual(900 - POPOVER_EDGE);
  });

  it("keeps a narrow-viewport panel inside the edge margins", () => {
    const p = computePopoverPlacement(trigger(300, 4), viewport(320, 700), {
      width: 296,
      height: 200,
    });
    expect(p.left).toBeGreaterThanOrEqual(POPOVER_EDGE);
    expect(p.left + 296).toBeLessThanOrEqual(320 - POPOVER_EDGE);
  });
});
