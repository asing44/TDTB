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
});
