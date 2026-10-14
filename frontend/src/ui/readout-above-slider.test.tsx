/* readout-above-slider.test.tsx — Plan task row duration column (Planning 2a
   redesign):

   The duration readout sits ABOVE the slider on its own line: the value is
   left-aligned over the − stepper, with a plain-word provenance label, when
   the row is not source-default, immediately after the value. The earlier
   fixed-width inline cell (an 88px box trailing the track) is retired — the
   readout no longer shares the slider's line, so its text width cannot reflow
   the track mid-drag. The readout never wraps.

   jsdom does no layout, so ordering and membership are checked in the DOM and
   the nowrap / left-alignment guarantees are read off the cascade on disk,
   the same way slider-steppers.test.tsx does. */

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

/** Body of the LAST `selector { … }` rule — the appended Planning 2a layer
    wins the cascade, so the last match is the live desktop rule. */
function lastRuleBody(selector: string): string {
  const re = new RegExp(`(?:^|\\n)\\s*${selector}\\s*\\{([^}]*)\\}`, "g");
  const matches = Array.from(appCss.matchAll(re), (m) => m[1]);
  if (matches.length === 0) throw new Error(`selector not found in app.css: ${selector}`);
  return matches[matches.length - 1];
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

describe("the duration readout leads the slider", () => {
  it("puts the readout on the line before the slider line", () => {
    const time = timeColumn();
    const readout = time.querySelector<HTMLElement>(".qrow__time-readout");
    const line = time.querySelector<HTMLElement>(".qrow__time-line");
    expect(readout).toBeTruthy();
    expect(line).toBeTruthy();
    // DOM order is readout, then the slider line — readout.compareDocumentPosition
    // returns FOLLOWING when the line comes after it.
    expect(
      readout!.compareDocumentPosition(line!) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it("holds only the value on a source (default) row", () => {
    const readout = timeColumn().querySelector<HTMLElement>(".qrow__time-readout")!;
    const kids = Array.from(readout.children) as HTMLElement[];
    expect(kids).toHaveLength(1);
    expect(kids[0].classList.contains("alloc-track__value")).toBe(true);
    expect(readout.querySelector(".qrow__dur-label")).toBeNull();
  });

  it("puts the label immediately after the value on a non-default row", () => {
    const h = makeHarness("ready");
    h.store.dispatch({
      type: "OVERRIDE_SET",
      id: "Magic Mirror",
      override: { included: true, blocks: 4 },
    });
    const container: HTMLElement = h.ui(<Queue />).container;
    const row = Array.from(
      container.querySelectorAll<HTMLElement>(".qrow"),
    ).find((r) => r.textContent?.includes("Magic Mirror"))!;
    const readout = row.querySelector<HTMLElement>(".qrow__time-readout")!;
    const kids = Array.from(readout.children) as HTMLElement[];
    expect(kids).toHaveLength(2);
    expect(kids[0].classList.contains("alloc-track__value")).toBe(true);
    expect(kids[1].classList.contains("qrow__dur-label")).toBe(true);
    expect(kids[1].textContent).toBe("today only");
    expect(kids[1].getAttribute("data-src")).toBe("memory");
  });

  it("holds only the track — the −/+ steppers are gone (Planning 2a)", () => {
    const line = timeColumn().querySelector<HTMLElement>(".qrow__time-line")!;
    const track = line.querySelector<HTMLElement>(".alloc-track");
    const slider = line.querySelector<HTMLInputElement>(".alloc-track__input");
    expect(track).toBeTruthy();
    expect(slider).toBeTruthy();
    expect(slider!.getAttribute("aria-valuetext")).toBeTruthy();
    // The track is the column's only control: no buttons share the line.
    expect(line.querySelectorAll(".alloc-step")).toHaveLength(0);
    expect(line.querySelectorAll("button")).toHaveLength(0);
    expect(Array.from(line.children)).toHaveLength(1);
  });
});

describe("the readout is a single left-aligned line above the slider", () => {
  it("keeps the readout on one nowrap line", () => {
    const body = lastRuleBody("\\.qrow__time-readout");
    expect(body).toMatch(/flex-wrap:\s*nowrap/);
    expect(body).toMatch(/white-space:\s*nowrap/);
  });

  it("stacks the time column as readout over slider at desktop width", () => {
    // The <=1100px layer re-places `.qrow__time` without touching its axis, so
    // filter to the last rule that owns the desktop placement and axis.
    const blocks = Array.from(
      appCss.matchAll(/(?:^|\n)\s*\.qrow__time\s*\{([^}]*)\}/g),
      (m) => m[1],
    );
    const desktop = blocks.filter((b) => /grid-column:\s*3/.test(b)).pop() ?? "";
    expect(desktop).toMatch(/flex-direction:\s*column/);
  });

  it("shrinks the readout to its text instead of a fixed 88px cell", () => {
    const body = lastRuleBody("\\.qrow__time-readout");
    expect(body).toMatch(/width:\s*auto/);
    expect(body).toMatch(/min-width:\s*0/);
    expect(body).toMatch(/min-height:\s*0/);
    expect(body).toMatch(/flex-direction:\s*row/);
  });

  it("left-aligns the value so its edge lines up with the track", () => {
    const body = lastRuleBody("\\.qrow__time-readout \\.alloc-track__value");
    expect(body).toMatch(/width:\s*auto/);
    expect(body).toMatch(/min-width:\s*0/);
    expect(body).toMatch(/text-align:\s*left/);
  });

  it("never lets the provenance chip wrap", () => {
    const body = ruleBody("\\.qrow__dur-label");
    expect(body).toMatch(/white-space:\s*nowrap/);
  });
});
