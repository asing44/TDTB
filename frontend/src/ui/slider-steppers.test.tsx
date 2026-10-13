/* slider-steppers.test.tsx — Plan task row duration control (2026-10-12, Adam):

   1. The −/+ duration steppers return to the row, flanking the slider:
      [−] [slider] [+] readout. Each steps by HALF_BLOCK (15 min) and pins at
      the MIN/MAX bounds.
   2. The track must not flex while it is dragged. The readout's text changes
      every step ("All day" → "15min" → "1hr" …) and the old `min-width: 0`
      override let the readout size to its text, so the flex track beside it
      re-laid-out mid-drag and the native thumb re-snapped. The readout now
      holds a fixed width.
   3. A stored override above the shrunk range (e.g. 12 blocks saved before
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
import { HALF_BLOCK, MAX_BLOCKS, MIN_BLOCKS } from "../model/allocator";

const appCss = readFileSync(resolve(process.cwd(), "src/app.css"), "utf8");

/** Body of the FIRST `selector { … }` rule — same helper shape as
    mint-slider-layout.test.ts. */
function ruleBody(selector: string): string {
  const re = new RegExp(`(?:^|\\n)\\s*${selector}\\s*\\{([^}]*)\\}`);
  const match = appCss.match(re);
  if (!match) throw new Error(`selector not found in app.css: ${selector}`);
  return match[1];
}

function rowFor(container: HTMLElement, name: string): HTMLElement {
  const found = Array.from(container.querySelectorAll<HTMLElement>(".qrow")).find(
    (r) => r.textContent?.includes(name),
  );
  if (!found) throw new Error(`queue row not found: ${name}`);
  return found;
}

describe("Plan row duration steppers are restored", () => {
  it("flanks the slider with a −15 and a +15 control", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    const row = rowFor(container, "Magic Mirror");
    const steps = Array.from(
      row.querySelectorAll<HTMLButtonElement>(".qrow__time .alloc-step"),
    );
    expect(steps).toHaveLength(2);

    const less = row.querySelector<HTMLButtonElement>(
      'button[aria-label="15 minutes less for Magic Mirror"]',
    );
    const more = row.querySelector<HTMLButtonElement>(
      'button[aria-label="15 minutes more for Magic Mirror"]',
    );
    expect(less).toBeTruthy();
    expect(more).toBeTruthy();

    const track = row.querySelector(".qrow__time .alloc-track");
    expect(track).toBeTruthy();
    // [−] then track then [+]
    expect(steps[0]).toBe(less);
    expect(steps[1]).toBe(more);
    expect(
      less!.compareDocumentPosition(track!) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(
      more!.compareDocumentPosition(track!) & Node.DOCUMENT_POSITION_PRECEDING,
    ).toBeTruthy();
  });

  it("steps 15 minutes each way and persists the override", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    const row = rowFor(container, "Magic Mirror");
    const less = row.querySelector<HTMLButtonElement>(
      'button[aria-label="15 minutes less for Magic Mirror"]',
    );
    const more = row.querySelector<HTMLButtonElement>(
      'button[aria-label="15 minutes more for Magic Mirror"]',
    );
    fireEvent.click(more!);
    expect(effectiveBlocks(h.store.getState(), "Magic Mirror")).toBe(3 + HALF_BLOCK);
    fireEvent.click(less!);
    expect(effectiveBlocks(h.store.getState(), "Magic Mirror")).toBe(3);
  });

  it("disables − at all-day and + at the range ceiling", () => {
    const atMin = makeHarness("ready");
    atMin.store.dispatch({
      type: "OVERRIDE_SET",
      id: "Magic Mirror",
      override: { included: true, blocks: MIN_BLOCKS },
    });
    const minRow = atMin.ui(<Queue />).container;
    const minLess = rowFor(minRow, "Magic Mirror").querySelector<HTMLButtonElement>(
      ".alloc-step",
    );
    expect(minLess!.disabled).toBe(true);

    const atMax = makeHarness("ready");
    atMax.store.dispatch({
      type: "OVERRIDE_SET",
      id: "Magic Mirror",
      override: { included: true, blocks: MAX_BLOCKS },
    });
    const maxRow = atMax.ui(<Queue />).container;
    const maxSteps = rowFor(maxRow, "Magic Mirror").querySelectorAll<HTMLButtonElement>(
      ".alloc-step",
    );
    expect(maxSteps[maxSteps.length - 1].disabled).toBe(true);
  });
});

describe("the duration readout holds a fixed width beside the slider", () => {
  it("holds a fixed value area so sliders line up between rows", () => {
    const body = ruleBody("\\.qrow__time-readout \\.alloc-track__value");
    expect(body).toMatch(/width:\s*76px/);
    expect(body).toMatch(/min-width:\s*76px/);
    expect(body).toMatch(/text-align:\s*right/);
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

describe("the readout fits beside the track at 1101-1279px", () => {
  it("lets the track shrink to 120px so the readout fits beside it", () => {
    // The time column is 300px at 1101-1279px. Two 32px steppers, the fixed
    // readout and the gaps leave little room; a 140px track floor overflowed
    // the column, so the track keeps a 120px floor.
    const body = ruleBody("\\.qrow__time \\.alloc-track");
    expect(body).toMatch(/min-width:\s*120px/);
  });
});
