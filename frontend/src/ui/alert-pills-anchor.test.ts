/* alert-pills-anchor.test.ts — the warnings roll-up must not grow the footer.

   The detail panel used to render in flow inside .alert-pills, so opening it
   stretched the sticky footer (.footer, z-index 10) upward — with a tall pile
   to ~40vh — covering the bottom of the window. The contract: .alert-pills is
   the positioning context, and the panel is absolutely anchored above it with
   its own stacking order.

   jsdom does no layout, so the cascade is read off disk the same way
   readout-above-slider.test.tsx does. */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const appCss = readFileSync(resolve(process.cwd(), "src/app.css"), "utf8");

/** Body of the FIRST `selector { … }` rule. */
function ruleBody(selector: string): string {
  const re = new RegExp(`(?:^|\\n)\\s*${selector}\\s*\\{([^}]*)\\}`);
  const match = appCss.match(re);
  if (!match) throw new Error(`selector not found in app.css: ${selector}`);
  return match[1];
}

describe("the warnings panel floats above the footer instead of growing it", () => {
  it("makes the pill wrapper the panel's positioning context", () => {
    expect(ruleBody("\\.alert-pills")).toMatch(/position:\s*relative/);
    expect(ruleBody("\\.alert-pills__panel")).toMatch(/position:\s*absolute/);
  });

  it("opens the panel upward above the pill row", () => {
    expect(ruleBody("\\.alert-pills__panel")).toMatch(/bottom:\s*calc\(\s*100%/);
  });

  it("stacks the panel above the footer's z-index", () => {
    const footerZ = Number(ruleBody("\\.footer").match(/z-index:\s*(\d+)/)?.[1]);
    const panelZ = Number(
      ruleBody("\\.alert-pills__panel").match(/z-index:\s*(\d+)/)?.[1],
    );
    expect(footerZ).toBe(10);
    expect(panelZ).toBeGreaterThan(footerZ);
  });
});
