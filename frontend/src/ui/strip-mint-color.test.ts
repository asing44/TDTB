/* strip-mint-color.test.ts — CSS regression for the day-frame strip's selected
   Mint blocks. Mint is the amber minting family (--c-minting) everywhere else
   in the cockpit, and the approved setup reference (reference/A-set-up-day)
   paints the selected 3–5 PM run amber next to its amber caption. A selected
   block drawn in the accent green reads as a different surface, so these
   guards pin the amber fill. jsdom does no style resolution, so this reads
   app.css off disk the same way row-spacing.test.ts does. */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const appCss = readFileSync(resolve(process.cwd(), "src/app.css"), "utf8");

/** Body of the standalone `.strip-slot--selected` rule. */
function selectedSlotRule(): string {
  const m = appCss.match(/(?:^|\n)\.strip-slot--selected\s*\{([^}]*)\}/);
  if (!m) throw new Error("standalone .strip-slot--selected rule not found in app.css");
  return m[1];
}

describe("selected Mint blocks on the day strip use the minting colour", () => {
  const rule = selectedSlotRule();

  it("fills the selected block with the minting token, not the accent green", () => {
    expect(rule).toMatch(/background:\s*var\(--c-minting\)/);
    expect(rule).not.toMatch(/--t-accent/);
  });

  it("keeps the selected block's border and label on the minting family", () => {
    expect(rule).toMatch(/border-color:\s*color-mix\(in srgb,\s*var\(--c-minting\)/);
    expect(rule).not.toMatch(/--t-accent-(strong|text)/);
  });
});
