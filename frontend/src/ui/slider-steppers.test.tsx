/* slider-steppers.test.tsx — Plan task row duration control.

   2026-10-14 (Planning 2a declutter): the −/+ steppers were removed from the
   row. The commands below moved, they were not dropped:

   1. Keyboard: the row's ←/→ keys walk the same stepDownBlocks/stepUpBlocks
      grid the −/+ buttons used (HALF_BLOCK steps, pinned at MIN/MAX).
   2. Pointer: the slider itself is the only control left in the column, and
      its step is still a half block.
   3. Precise entry: the Exact duration (clock, shortcut D) editor.
   4. An all-day row (0 blocks) shows "All day" over an inert track — the
      mockup's Charge GoPro row — while ←/→ and the exact editor can still
      give it time.
   5. The track must not flex while it is dragged. The readout sits on its own
      line above the slider (Planning 2a), so its text changes ("All day" →
      "15min" → "1hr" …) can no longer reflow the track or re-snap the thumb.
   6. A stored override above the shrunk range (e.g. 12 blocks saved before
      MAX_BLOCKS dropped to 10) must not disagree with the thumb.

   jsdom does no layout, so the width guard reads the cascade off disk the same
   way mint-slider-layout.test.ts and row-actions-plan.test.ts do. */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent } from "@testing-library/preact";

afterEach(cleanup);

import { Queue } from "./Queue";
import { makeHarness } from "./test-harness";
import { effectiveBlocks } from "../store/store";
import { HALF_BLOCK, MAX_BLOCKS } from "../model/allocator";

const appCss = readFileSync(resolve(process.cwd(), "src/app.css"), "utf8");

/** Body of the FIRST `selector { … }` rule — same helper shape as
    mint-slider-layout.test.ts. */
function ruleBody(selector: string): string {
  const re = new RegExp(`(?:^|\\n)\\s*${selector}\\s*\\{([^}]*)\\}`);
  const match = appCss.match(re);
  if (!match) throw new Error(`selector not found in app.css: ${selector}`);
  return match[1];
}

/** Body of the LAST `selector { … }` rule — the appended >=1440px redesign
    layer wins the cascade, so the last match is the live desktop rule. */
function lastRuleBody(selector: string): string {
  const re = new RegExp(`(?:^|\\n)\\s*${selector}\\s*\\{([^}]*)\\}`, "g");
  const matches = Array.from(appCss.matchAll(re), (m) => m[1]);
  if (matches.length === 0) throw new Error(`selector not found in app.css: ${selector}`);
  return matches[matches.length - 1];
}

function rowFor(container: HTMLElement, name: string): HTMLElement {
  const found = Array.from(container.querySelectorAll<HTMLElement>(".qrow")).find(
    (r) => r.textContent?.includes(name),
  );
  if (!found) throw new Error(`queue row not found: ${name}`);
  return found;
}

describe("the row's −/+ duration steppers are gone (Planning 2a, 2026-10-14)", () => {
  it("renders no stepper buttons around the track", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    const row = rowFor(container, "Magic Mirror");
    const line = row.querySelector<HTMLElement>(".qrow__time-line")!;
    expect(line).toBeTruthy();
    expect(line.querySelectorAll(".alloc-step")).toHaveLength(0);
    expect(line.querySelectorAll("button")).toHaveLength(0);
    // Only the track remains: the readout sits on its own line above it.
    expect(Array.from(line.children)).toHaveLength(1);
    expect(line.querySelector(".alloc-track__input")).toBeTruthy();
  });

  it("keeps the 15-minute grid on the slider and on the row's ←/→ keys", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    const row = rowFor(container, "Magic Mirror");
    const slider = row.querySelector<HTMLInputElement>(".alloc-track__input")!;
    expect(slider.step).toBe(String(HALF_BLOCK));
    expect(slider.getAttribute("aria-valuetext")).toMatch(/\(source\)$/);
    fireEvent.keyDown(row, { key: "ArrowRight" });
    expect(effectiveBlocks(h.store.getState(), "Magic Mirror")).toBe(3 + HALF_BLOCK);
    fireEvent.keyDown(row, { key: "ArrowLeft" });
    expect(effectiveBlocks(h.store.getState(), "Magic Mirror")).toBe(3);
  });

  it("reads \"All day\" over an inert track on a zero-block row", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    const row = rowFor(container, "Charge GoPro");
    const slider = row.querySelector<HTMLInputElement>(".alloc-track__input")!;
    expect(slider.disabled).toBe(true);
    expect(
      row.querySelector<HTMLElement>(".alloc-track__value")!.textContent,
    ).toBe("All day");
    // The drag-free routes to time stay: the row keys and Exact duration.
    fireEvent.keyDown(row, { key: "ArrowRight" });
    expect(effectiveBlocks(h.store.getState(), "Charge GoPro")).toBe(HALF_BLOCK);
  });
});

describe("the readout sits above the slider so it cannot reflow the track", () => {
  it("left-aligns the value and shrinks it to its text", () => {
    const body = lastRuleBody("\\.qrow__time-readout \\.alloc-track__value");
    expect(body).toMatch(/width:\s*auto/);
    expect(body).toMatch(/min-width:\s*0/);
    expect(body).toMatch(/text-align:\s*left/);
  });

  it("never wraps the value; the base value rule stays right-aligned", () => {
    const body = ruleBody("\\.qrow__time \\.alloc-track__value");
    expect(body).toMatch(/white-space:\s*nowrap/);
    const base = ruleBody("\\.alloc-track__value");
    expect(base).toMatch(/text-align:\s*right/);
  });
});

describe("a stored override above the shrunk range still agrees with the thumb", () => {
  it("clamps the readout to the same value the slider can show", () => {
    const h = makeHarness("ready");
    // 12 blocks (6hr) saved before MAX_BLOCKS dropped to 10 (5hr).
    h.store.dispatch({
      type: "OVERRIDE_SET",
      id: "Magic Mirror",
      override: { included: true, blocks: 12 },
    });
    const { container } = h.ui(<Queue />);
    const row = rowFor(container, "Magic Mirror");
    const slider = row.querySelector<HTMLInputElement>(".alloc-track__input")!;
    const readout = row.querySelector<HTMLElement>(".alloc-track__value")!;
    expect(slider.value).toBe(String(MAX_BLOCKS));
    expect(readout.textContent).toBe("5hr*");
  });
});

describe("the track takes a fixed 140px slot in the duration column", () => {
  it("is not stretched and cannot overflow its column", () => {
    // Planning 2a declutter: with the steppers gone there is nothing left for
    // the track to share the column with, so it holds the mockup's ~140px.
    const body = lastRuleBody("\\.qrow__time \\.alloc-track");
    expect(body).toMatch(/flex:\s*none/);
    expect(body).toMatch(/width:\s*140px/);
    expect(body).toMatch(/max-width:\s*100%/);
  });
});
