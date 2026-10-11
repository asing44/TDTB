/* row-rhythm.test.ts — CSS guards for the plan-tasks row rhythm (2026-10-11).
   Two operator asks: more air between adjacent task rows, and a narrower work
   surface than the full viewport. The density layer is appended at the end of
   app.css so it wins the cascade without rewriting the blocks it overrides.
   jsdom does no layout, so these read the cascade off disk the same way
   row-spacing.test.ts does. */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const appCss = readFileSync(resolve(process.cwd(), "src/app.css"), "utf8");

/** Body of the LAST top-level `selector { … }` rule. The rhythm layer is the
    final block in app.css, so the last match is the rule that wins. */
function lastTopLevelBlock(selector: string): string {
  const re = new RegExp(`(?:^|\\n)${selector}\\s*\\{([^}]*)\\}`, "g");
  const matches = Array.from(appCss.matchAll(re), (m) => m[1]);
  if (matches.length === 0) throw new Error(`selector not found in app.css: ${selector}`);
  return matches[matches.length - 1];
}

describe("task rows carry more air between them", () => {
  const row = lastTopLevelBlock("\\.qrow");

  it("the final .qrow rule sets at least 12px of vertical padding", () => {
    const m = row.match(/padding-block:\s*(\d+(?:\.\d+)?)px/);
    expect(m).not.toBeNull();
    expect(parseFloat(m?.[1] ?? "0")).toBeGreaterThanOrEqual(12);
  });

  it("keeps the spacing vertical-only (no horizontal padding change)", () => {
    expect(row).not.toMatch(/padding-(left|right|inline)/);
    expect(row).not.toMatch(/grid-template-columns:/);
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
