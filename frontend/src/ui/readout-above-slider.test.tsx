/* readout-above-slider.test.tsx — Plan task row duration column (2026-10-12
   redesign):

   The duration readout sits INLINE to the RIGHT of the slider in the one-line
   desktop row: [−] [track] [+] readout. The readout is a FIXED-width cell; a
   plain-word provenance label, when the row is not source-default, stacks on a
   second line beneath the value so every row keeps the same cell box and the
   sliders line up. The readout never wraps.

   jsdom does no layout, so ordering and membership are checked in the DOM and
   the row / nowrap / fixed-width guarantees are read off the cascade on disk,
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

/** Body of the LAST `selector { … }` rule — the appended redesign layer wins
    the cascade, so the last match is the live desktop rule. */
function lastRuleBody(selector: string): string {
  const re = new RegExp(`(?:^|\\n)\\s*${selector}\\s*\\{([^}]*)\\}`, "g");
  const matches = Array.from(appCss.matchAll(re), (m) => m[1]);
  if (matches.length === 0) throw new Error(`selector not found in app.css: ${selector}`);
  return matches[matches.length - 1];
}

/** Body of the LAST `selector { … }` rule inside the `@media (min-width: 1440px)`
    layers only (brace-balanced). The file ends with `max-width: 1439px` blocks
    that re-declare some selectors for the stacked layout, so the plain last
    match is not the live desktop rule. */
function desktopRuleBody(selector: string): string {
  const bodies: string[] = [];
  const open = /@media\s*\(min-width:\s*1440px\)\s*\{/g;
  while (open.exec(appCss) !== null) {
    let depth = 1;
    let i = open.lastIndex;
    const start = i;
    while (i < appCss.length && depth > 0) {
      if (appCss[i] === "{") depth += 1;
      else if (appCss[i] === "}") depth -= 1;
      i += 1;
    }
    bodies.push(appCss.slice(start, i - 1));
  }
  const re = new RegExp(`(?:^|\\n)\\s*${selector}\\s*\\{([^}]*)\\}`, "g");
  const hits = bodies.flatMap((b) => Array.from(b.matchAll(re), (x) => x[1]));
  if (hits.length === 0) {
    throw new Error(`no @media (min-width: 1440px) rule for ${selector}`);
  }
  return hits[hits.length - 1];
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

describe("the duration readout trails the slider", () => {
  it("puts the readout after the slider line", () => {
    const time = timeColumn();
    const readout = time.querySelector<HTMLElement>(".qrow__time-readout");
    const line = time.querySelector<HTMLElement>(".qrow__time-line");
    expect(readout).toBeTruthy();
    expect(line).toBeTruthy();
    expect(
      readout!.compareDocumentPosition(line!) & Node.DOCUMENT_POSITION_PRECEDING,
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

describe("the readout is a fixed-width cell beside the slider", () => {
  it("keeps the readout on one nowrap line", () => {
    const body = desktopRuleBody("\\.qrow__time-readout");
    expect(body).toMatch(/flex-wrap:\s*nowrap/);
    expect(body).toMatch(/white-space:\s*nowrap/);
  });

  it("lays the time column out as a row at desktop width", () => {
    const body = lastRuleBody("\\.qrow__time");
    expect(body).toMatch(/flex-direction:\s*row/);
  });

  it("stacks the value over the chip in a fixed-width cell", () => {
    const body = desktopRuleBody("\\.qrow__time-readout");
    expect(body).toMatch(/width:\s*88px/);
    expect(body).toMatch(/min-width:\s*88px/);
    expect(body).toMatch(/min-height:\s*34px/);
    expect(body).toMatch(/flex-direction:\s*column/);
    expect(body).toMatch(/align-items:\s*flex-end/);
  });

  it("holds a fixed-width value area so sliders line up between rows", () => {
    const body = desktopRuleBody("\\.qrow__time-readout \\.alloc-track__value");
    expect(body).toMatch(/width:\s*88px/);
    expect(body).toMatch(/text-align:\s*right/);
  });

  it("never lets the chip wrap or exceed the readout cell", () => {
    const body = desktopRuleBody("\\.qrow__time-readout \\.qrow__dur-label");
    expect(body).toMatch(/max-width:\s*100%/);
    expect(body).toMatch(/overflow:\s*hidden/);
    expect(ruleBody("\\.qrow__dur-label")).toMatch(/white-space:\s*nowrap/);
  });
});
