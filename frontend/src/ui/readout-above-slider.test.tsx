/* readout-above-slider.test.tsx — Plan task row duration column (2026-10-12
   redesign):

   The duration readout moved off its own line ABOVE the slider and now sits
   INLINE to the RIGHT of the slider: [−] [track] [+] readout. The value holds
   a fixed width so the sliders line up between rows; a plain-word provenance
   label, when the row is not source-default, sits immediately after the
   value. The readout never wraps.

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

describe("the readout is a fixed-width value area beside the slider", () => {
  it("keeps the readout on one nowrap line", () => {
    const body = ruleBody("\\.qrow__time-readout");
    expect(body).toMatch(/flex-wrap:\s*nowrap/);
    expect(body).toMatch(/white-space:\s*nowrap/);
  });

  it("lays the time column out as a row at desktop width", () => {
    const body = lastRuleBody("\\.qrow__time");
    expect(body).toMatch(/flex-direction:\s*row/);
  });

  it("holds a fixed-width value area so sliders line up between rows", () => {
    const body = ruleBody("\\.qrow__time-readout \\.alloc-track__value");
    expect(body).toMatch(/width:\s*76px/);
    expect(body).toMatch(/text-align:\s*right/);
  });

  it("keeps the label next to the value, left-aligned with the readout", () => {
    const body = ruleBody("\\.qrow__time-readout");
    expect(body).toMatch(/justify-content:\s*flex-start/);
    expect(body).not.toMatch(/justify-content:\s*space-between/);
  });
});
