/* row-rhythm.test.ts — CSS guards for the plan-tasks row rhythm (Planning 2a,
   2026-10-14, superseding 2026-10-11).

   The operator asks are unchanged in spirit — rows read as one calm, scannable
   stack, and the work surface is narrower than the full viewport — but 2a
   answers them with compact 6x12 row cards separated by a 4px stack gap rather
   than tall vertical padding. The layer is appended at the end of app.css so
   it wins the cascade without rewriting the blocks it overrides. jsdom does no
   layout, so these read the cascade off disk the same way row-spacing.test.ts
   does. */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const appCss = readFileSync(resolve(process.cwd(), "src/app.css"), "utf8");

/** Body of the LAST top-level `selector { … }` rule. */
function lastTopLevelBlock(selector: string): string {
  const re = new RegExp(`(?:^|\\n)${selector}\\s*\\{([^}]*)\\}`, "g");
  const matches = Array.from(appCss.matchAll(re), (m) => m[1]);
  if (matches.length === 0) throw new Error(`selector not found in app.css: ${selector}`);
  return matches[matches.length - 1];
}

/** Body of the LAST top-level `.qrow { … }` rule that owns a `padding`
    declaration. The appended 2a layer splits the row across two `.qrow`
    blocks, so the plain last match is not the padding rule. */
function lastRowPadBlock(): string {
  const re = /(?:^|\n)\.qrow\s*\{([^}]*)\}/g;
  const blocks = Array.from(appCss.matchAll(re), (m) => m[1]);
  const row = blocks.filter((b) => /padding:/.test(b)).pop();
  if (!row) throw new Error("standalone .qrow padding rule not found in app.css");
  return row;
}

describe("task rows keep the compact 2a rhythm", () => {
  const row = lastRowPadBlock();

  it("the final .qrow padding rule uses the compact 6x12 padding", () => {
    expect(row).toMatch(/padding:\s*6px 12px/);
  });

  it("the row owns the five-column grid (status | body | duration | priority | actions)", () => {
    expect(row).toMatch(
      /grid-template-columns:\s*130px\s+minmax\(0,\s*1fr\)\s+200px\s+50px\s+194px/,
    );
  });

  it("separates adjacent rows with a 4px stack gap instead of tall padding", () => {
    const rows = lastTopLevelBlock("\\.tasktable__rows");
    expect(rows).toMatch(/gap:\s*4px/);
  });
});

describe("the work surface is narrower than the full viewport", () => {
  it("defines a plan column width token of 1000–1200px", () => {
    const token = lastTopLevelBlock("\\.cockpit").match(
      /--plan-max-width:\s*(\d+)px/,
    );
    expect(token).not.toBeNull();
    const px = parseInt(token?.[1] ?? "0", 10);
    expect(px).toBeGreaterThanOrEqual(1000);
    expect(px).toBeLessThanOrEqual(1200);
  });

  it("caps .queue at the token and centres it", () => {
    const queue = lastTopLevelBlock("\\.queue");
    expect(queue).toMatch(/max-width:\s*var\(--plan-max-width\)/);
    expect(queue).toMatch(/margin-inline:\s*auto/);
  });

  it("leaves the header and footer chrome full width", () => {
    expect(lastTopLevelBlock("\\.cockpit__main")).not.toMatch(/max-width/);
    expect(lastTopLevelBlock("\\.cockpit__footer")).not.toMatch(/max-width/);
  });
});
