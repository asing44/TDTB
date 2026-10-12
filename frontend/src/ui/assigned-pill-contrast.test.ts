/* assigned-pill-contrast.test.ts — the Assigned pill's text must be legible on
   its accent fill in dark mode. White ink on the dark accent (#2fb98c) is
   2.49:1, below the 4.5:1 AA floor for text this size. The pill takes its ink
   from --t-on-accent, and each dark theme block must pair that ink with its
   accent above the floor. Light mode keeps the approved white ink, so only the
   dark pairs are pinned here.

   Reads tokens.css and app.css off disk, the same way mint-slider-layout.test.ts
   does. jsdom does no style resolution, so these are the regression guards. */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const tokensCss = readFileSync(resolve(process.cwd(), "src/tokens.css"), "utf8");
const appCss = readFileSync(resolve(process.cwd(), "src/app.css"), "utf8");

/** Body of a rule whose selector starts a line, e.g. `.qrow__assign--on`. */
function ruleBody(css: string, selector: string): string {
  const m = css.match(new RegExp(`(?:^|\\n)${selector}\\s*\\{([^}]*)\\}`));
  if (!m) throw new Error(`rule ${selector} not found`);
  return m[1];
}

/** Body of the `:root` block inside the prefers-color-scheme: dark media query. */
function darkMediaBody(css: string): string {
  const m = css.match(/@media \(prefers-color-scheme: dark\) \{[\s\S]*?:root \{([^}]*)\}/);
  if (!m) throw new Error("prefers-color-scheme: dark :root block not found");
  return m[1];
}

function tokenHex(body: string, name: string): string {
  const m = body.match(new RegExp(`${name}:\\s*(#[0-9a-fA-F]{6})`));
  if (!m) throw new Error(`${name} is not set in this theme block`);
  return m[1];
}

function luminance(hex: string): number {
  const h = hex.replace("#", "");
  const lin = (i: number) => {
    const c = parseInt(h.slice(i, i + 2), 16) / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * lin(0) + 0.7152 * lin(2) + 0.0722 * lin(4);
}

function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

const DARK_BLOCKS = {
  "prefers-color-scheme: dark": darkMediaBody(tokensCss),
  'data-theme="dark"': ruleBody(tokensCss, ':root\\[data-theme="dark"\\]'),
};

describe("Assigned pill text is legible on its accent in dark mode", () => {
  it("takes its ink from the on-accent token, not a fixed white", () => {
    expect(ruleBody(appCss, "\\.qrow__assign--on")).toMatch(/color:\s*var\(--t-on-accent\)/);
  });

  for (const [name, body] of Object.entries(DARK_BLOCKS)) {
    it(`clears 4.5:1 on the accent fill in the ${name} theme`, () => {
      const accent = tokenHex(body, "--t-accent");
      const ink = tokenHex(body, "--t-on-accent");
      expect(contrast(ink, accent)).toBeGreaterThanOrEqual(4.5);
    });
  }
});
