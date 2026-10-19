/* capacity-sticky.test.tsx — CSS contract for the sticky capacity pocket
   (2026-10-19). The Plan screen's right column carries the day's capacity
   story; a long task list scrolled the card out of view, so the operator
   could not watch capacity change while expanding, skipping, or assigning
   rows. On the two-column layout the card now sticks at the lane header's
   offset, capped above the sticky footer, and scrolls inside itself when the
   Details disclosure makes it tall. Stacked layouts (<=1100px) keep it in
   normal flow.

   jsdom does no layout, so the cascade is read off disk like
   row-compact-1100.test.ts and plan-width-stability.test.tsx do. */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const appCss = readFileSync(resolve(process.cwd(), "src/app.css"), "utf8");

/** Bodies of every brace-balanced `@media <query> { … }` block. app.css
    carries many 1100px/1101px layers, so a contract reads the one that owns
    the rule it asserts. */
function mediaBodies(query: string): string[] {
  const opener = new RegExp(`@media\\s*${query}\\s*\\{`, "g");
  const out: string[] = [];
  let m: RegExpExecArray | null;
  while ((m = opener.exec(appCss)) !== null) {
    let depth = 1;
    let i = m.index + m[0].length;
    const start = i;
    while (i < appCss.length && depth > 0) {
      if (appCss[i] === "{") depth += 1;
      else if (appCss[i] === "}") depth -= 1;
      i += 1;
    }
    out.push(appCss.slice(start, i - 1).replace(/\/\*[\s\S]*?\*\//g, ""));
  }
  if (out.length === 0) throw new Error(`no @media block for ${query}`);
  return out;
}

/** Body of the LAST `selector { … }` rule across every matching media block. */
function mediaRule(query: string, selector: string): string {
  const hits: string[] = [];
  for (const body of mediaBodies(query)) {
    const re = new RegExp(`(?:^|\\n)\\s*${selector}\\s*\\{([^}]*)\\}`, "g");
    hits.push(...Array.from(body.matchAll(re), (x) => x[1]));
  }
  if (hits.length === 0) throw new Error(`no rule for ${selector} in ${query}`);
  return hits[hits.length - 1];
}

/** Bodies of every top-level `selector { … }` rule. */
function topLevelBlocks(selector: string): string[] {
  const re = new RegExp(`(?:^|\\n)${selector}\\s*\\{([^}]*)\\}`, "g");
  return Array.from(appCss.matchAll(re), (x) => x[1]);
}

const WIDE = "(?:screen\\s+and\\s+)?\\(min-width:\\s*1101px\\)";
const DESKTOP = "\\(min-width:\\s*768px\\)";
const STACKED = "screen\\s+and\\s*\\(max-width:\\s*1100px\\)";

describe("the capacity card holds its place on the two-column Plan layout", () => {
  it("sticks to the scrollport top with align-self: start", () => {
    const cap = mediaRule(WIDE, "\\.plan-grid__capacity");
    expect(cap).toMatch(/(^|[\s;])position:\s*sticky/);
    expect(cap).toMatch(/(^|[\s;])align-self:\s*start/);
    // The app header is outside the .cockpit__main scrollport, so the card
    // pins flush at its top instead of leaving a header-sized dead band.
    expect(cap).toMatch(/(^|[\s;])top:\s*0(;|\s|$)/);
  });

  it("caps its height above the sticky footer and scrolls inside itself", () => {
    const cap = mediaRule(WIDE, "\\.plan-grid__capacity");
    expect(cap).toMatch(/max-height:\s*calc\(/);
    expect(cap).toMatch(/100dvh/);
    // The app header band plus the shared footer reserve are both subtracted.
    expect(cap).toMatch(/calc\(\s*100dvh\s*-\s*64px\s*-\s*var\(--footer-clearance,\s*112px\)/);
    expect(cap).toMatch(/overflow-y:\s*auto/);
  });

  it("stays in normal flow where the grid stacks (<=1100px)", () => {
    const cap = mediaRule(STACKED, "\\.plan-grid__capacity");
    expect(cap).toMatch(/order:\s*-1/);
    expect(cap).not.toMatch(/position:\s*sticky/);
    for (const base of topLevelBlocks("\\.plan-grid__capacity")) {
      expect(base).not.toMatch(/position:\s*sticky/);
    }
  });

  it("pins to the desktop shell's .cockpit__main scrollport, not a new one", () => {
    // The desktop shell (>=768px) makes the Plan column the scroller, so that
    // is the sticky scrollport the card pins against.
    expect(mediaRule(DESKTOP, "\\.cockpit__main")).toMatch(/overflow-y:\s*auto/);
    // Nothing between it and the card adds a second scroll container.
    for (const block of topLevelBlocks("\\.queue")) {
      expect(block).not.toMatch(/overflow:\s*(hidden|auto|scroll)/);
      expect(block).not.toMatch(/overflow-y:\s*(hidden|auto|scroll)/);
    }
    for (const block of topLevelBlocks("\\.plan-grid")) {
      expect(block).not.toMatch(/overflow|contain:/);
    }
    // The x-axis containment that keeps the page from scrolling sideways
    // clips only horizontally, so it adds no y scrollport of its own.
    expect(appCss).toMatch(/\.cockpit__main,\s*\.queue\s*\{[^}]*overflow-x:\s*clip/);
  });
});
