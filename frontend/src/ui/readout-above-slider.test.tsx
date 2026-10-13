/* readout-above-slider.test.tsx — Plan task row duration column (2026-10-12):

   Adam's capture showed the duration readout ("2hr 30min*") wrapping onto its
   own line BELOW the slider, under the − stepper. The column now stacks two
   lines: the readout (with its provenance label) first, then [−] [track] [+].
   The readout is never allowed to wrap.

   jsdom does no layout, so ordering and membership are checked in the DOM and
   the no-wrap / column / alignment guarantees are read off the cascade on
   disk, the same way slider-steppers.test.tsx does. */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { cleanup } from "@testing-library/preact";

afterEach(cleanup);

import { Queue } from "./Queue";
import { makeHarness } from "./test-harness";

const appCss = readFileSync(resolve(process.cwd(), "src/app.css"), "utf8");

/** Body of the FIRST `selector { … }` rule — same helper shape as
    slider-steppers.test.tsx. */
function ruleBody(selector: string): string {
  const re = new RegExp(`(?:^|\\n)\\s*${selector}\\s*\\{([^}]*)\\}`);
  const match = appCss.match(re);
  if (!match) throw new Error(`selector not found in app.css: ${selector}`);
  return match[1];
}

function timeColumn(): HTMLElement {
  const h = makeHarness("ready");
  const container: HTMLElement = h.ui(<Queue />).container;
  const row = Array.from(container.querySelectorAll<HTMLElement>(".qrow")).find(
    (r) => r.textContent?.includes("Magic Mirror"),
  );
  if (!row) throw new Error("queue row not found: Magic Mirror");
  const time = row.querySelector<HTMLElement>(".qrow__time");
  if (!time) throw new Error(".qrow__time not found");
  return time;
}

describe("the duration readout leads the time column", () => {
  it("puts the readout line before the slider line", () => {
    const time = timeColumn();
    const readout = time.querySelector<HTMLElement>(".qrow__time-readout");
    const line = time.querySelector<HTMLElement>(".qrow__time-line");
    expect(readout).toBeTruthy();
    expect(line).toBeTruthy();
    expect(
      readout!.compareDocumentPosition(line!) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it("holds only the value and the source label, in that order", () => {
    const readout = timeColumn().querySelector<HTMLElement>(".qrow__time-readout")!;
    const kids = Array.from(readout.children) as HTMLElement[];
    expect(kids).toHaveLength(2);
    expect(kids[0].classList.contains("alloc-track__value")).toBe(true);
    expect(kids[1].classList.contains("qrow__dur-label")).toBe(true);
    expect(kids[1].textContent).toBe("source");
  });

  it("holds −, the track, and +, in that order", () => {
    const line = timeColumn().querySelector<HTMLElement>(".qrow__time-line")!;
    const less = line.querySelector<HTMLButtonElement>(
      'button[aria-label="15 minutes less for Magic Mirror"]',
    );
    const track = line.querySelector<HTMLElement>(".alloc-track");
    const more = line.querySelector<HTMLButtonElement>(
      'button[aria-label="15 minutes more for Magic Mirror"]',
    );
    expect(less).toBeTruthy();
    expect(track).toBeTruthy();
    expect(more).toBeTruthy();
    expect(
      less!.compareDocumentPosition(track!) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(
      more!.compareDocumentPosition(track!) & Node.DOCUMENT_POSITION_PRECEDING,
    ).toBeTruthy();
    // The steppers are still the only two .alloc-step in the column.
    expect(line.querySelectorAll(".alloc-step")).toHaveLength(2);
  });
});

describe("the readout line never wraps and the column stacks", () => {
  it("keeps the readout on one nowrap line", () => {
    const body = ruleBody("\\.qrow__time-readout");
    expect(body).toMatch(/flex-wrap:\s*nowrap/);
    expect(body).toMatch(/white-space:\s*nowrap/);
  });

  it("stacks the time column vertically by default", () => {
    const body = ruleBody("\\.qrow__time");
    expect(body).toMatch(/flex-direction:\s*column/);
  });

  it("left-aligns the readout so it lines up with the − stepper", () => {
    const body = ruleBody("\\.qrow__time-readout \\.alloc-track__value");
    expect(body).toMatch(/text-align:\s*left/);
  });
});
