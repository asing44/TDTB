/* row-spacing.test.ts — CSS regression for the placement-row breathing-room
   polish (plan tdtb-row-spacing-20260817). The main allocator rows (.qrow)
   must carry enough vertical padding to separate adjacent rows at the
   screenshot's density, and the polish must not erode the row's touch-target
   floors or alignment. jsdom does no real style resolution, so these guards
   read app.css off disk the same way the responsive/a11y suites do. */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const appCss = readFileSync(resolve(process.cwd(), "src/app.css"), "utf8");

/** Body of the `.qrow` rule that owns the row padding. Every line-anchored
    `.qrow` block is collected — the comma-grouped `.queue__cols,\n.qrow` grid
    rule also starts a line, but it has no padding, so the padding rule is the
    one that matches the token. */
function rowRule(): string {
  const re = /(?:^|\n)\.qrow\s*\{([^}]*)\}/g;
  const blocks = Array.from(appCss.matchAll(re), (m) => m[1]);
  const row = blocks.find((b) => /padding:/.test(b));
  if (!row) throw new Error("standalone .qrow padding rule not found in app.css");
  return row;
}

/** Body of the first block matching a dotted selector anywhere in the file
    (used for the single-selector control rules). */
function firstBlock(selector: string): string {
  const m = appCss.match(new RegExp(`\\.${selector}\\s*\\{([^}]*)\\}`));
  if (!m) throw new Error(`selector not found in app.css: .${selector}`);
  return m[1];
}

describe("placement rows carry enough vertical breathing room", () => {
  const row = rowRule();

  it("the standalone .qrow rule owns the padding", () => {
    expect(row).toMatch(/padding:/);
    // The desktop grid stays in the comma-grouped .queue__cols, .qrow rule —
    // the spacing rule must not smuggle in alignment changes.
    expect(row).not.toMatch(/grid-template-columns:/);
  });

  it("vertical padding separates adjacent rows (≥6px each side)", () => {
    const pad = row.match(/padding:\s*([^;]+);/)?.[1] ?? "";
    const parts = pad.trim().split(/\s+/);
    const vertical = parseFloat(parts[0] ?? "0");
    expect(vertical).toBeGreaterThanOrEqual(6);
  });

  it("the spacing is vertical-only — horizontal padding stays 0", () => {
    const pad = row.match(/padding:\s*([^;]+);/)?.[1] ?? "";
    const parts = pad.trim().split(/\s+/);
    expect(parts[1]).toBe("0");
  });
});

describe("row spacing never erodes control touch targets", () => {
  it("direct verbs keep the 44px floor", () => {
    expect(firstBlock("alloc-verb")).toMatch(/min-height:\s*44px/);
  });

  it("row icon controls keep the 44px floor", () => {
    expect(firstBlock("qrow__controls .iconbtn")).toMatch(/min-height:\s*44px/);
  });

  it("the duration track keeps its 44px hit area", () => {
    expect(firstBlock("alloc-track")).toMatch(/height:\s*44px/);
  });

  it("the 15-minute track has enough visual room without shrinking its hit area", () => {
    expect(firstBlock("alloc-track")).toMatch(/min-width:\s*140px/);
    expect(firstBlock("alloc-track__input")).toMatch(/height:\s*44px/);
  });

  it("uses a compact visual thumb on the full-size keyboard range", () => {
    const thumb = appCss.match(
      /\.alloc-track__input::-webkit-slider-thumb\s*\{([^}]*)\}/,
    )?.[1] ?? "";
    expect(thumb).toMatch(/width:\s*10px/);
    expect(thumb).toMatch(/height:\s*10px/);
  });
});

describe("queue controls stay inside the viewport", () => {
  it("lets desktop action columns shrink while row controls wrap", () => {
    const grid = appCss.match(/\.queue__cols,\s*\.qrow\s*\{([^}]*)\}/)?.[1] ?? "";
    expect(grid).toMatch(/minmax\(0,\s*1fr\)/);
    expect(grid).not.toMatch(/minmax\(348px,\s*auto\)/);
    expect(firstBlock("qrow__actions")).toMatch(/min-width:\s*0/);
    expect(firstBlock("qrow__actions")).toMatch(/flex-wrap:\s*wrap/);
  });

  it("switches to full-width two-column rows before the narrow phone layout", () => {
    expect(appCss).toMatch(
      /@media\s*\(max-width:\s*1100px\)[\s\S]*\.qrow__actions\s*\{[^}]*grid-column:\s*1\s*\/\s*-1/,
    );
    expect(appCss).toMatch(
      /@media\s*\(max-width:\s*1100px\)[\s\S]*\.qrow__time\s*\{[^}]*grid-column:\s*2/,
    );
  });
});
