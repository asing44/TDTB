/* day-setup-narrow-header.test.ts — at 390px the Your-day header kept its
   controls on one line: the Preset select (sized by its longest option) sat on
   top of the All blocks trigger and swallowed its clicks. The narrow layout
   must let the header wrap and let the frame fields wrap inside their line.

   jsdom does no layout, so the cascade is read off disk the same way
   alert-pills-anchor.test.ts and header-pill-motion.test.ts do. The fix is
   scoped to the existing 900px breakpoint so the desktop (>=901px) layout is
   byte-identical. */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const appCss = readFileSync(resolve(process.cwd(), "src/app.css"), "utf8");

/** Body of the first `selector { … }` rule. */
function ruleBody(selector: string): string {
  const re = new RegExp(`(?:^|\\n)\\s*${selector}\\s*\\{([^}]*)\\}`);
  const match = appCss.match(re);
  if (!match) throw new Error(`selector not found in app.css: ${selector}`);
  return match[1];
}

/** Every brace-balanced `@media … { … }` block, in source order. */
function mediaBlocks(): { query: string; body: string }[] {
  const out: { query: string; body: string }[] = [];
  const opener = /@media\s*([^{]+)\{/g;
  let match: RegExpExecArray | null;
  while ((match = opener.exec(appCss))) {
    const start = opener.lastIndex;
    let depth = 1;
    let i = start;
    while (i < appCss.length && depth > 0) {
      if (appCss[i] === "{") depth += 1;
      else if (appCss[i] === "}") depth -= 1;
      i += 1;
    }
    out.push({ query: match[1].trim(), body: appCss.slice(start, i - 1) });
    opener.lastIndex = i;
  }
  return out;
}

describe("the Your-day header wraps at narrow widths", () => {
  const narrow = mediaBlocks().filter(
    (block) =>
      block.query.includes("max-width: 900px") &&
      block.body.includes(".setup-section--your-day"),
  );

  it("does not alter the single-row header at the desktop breakpoint", () => {
    // The >=901px layout stays as it was: the base rule never wraps.
    expect(ruleBody("\\.setup-section--your-day \\.your-day__head")).not.toMatch(
      /flex-wrap:\s*wrap/,
    );
  });

  it("lets the header wrap under 900px so the header controls stack", () => {
    expect(narrow.length).toBe(1);
    expect(narrow[0].body).toMatch(
      /\.setup-section--your-day \.your-day__head\s*\{[^}]*flex-wrap:\s*wrap/,
    );
  });

  it("lets the frame fields wrap under 900px instead of holding intrinsic width", () => {
    expect(narrow.length).toBe(1);
    expect(narrow[0].body).toMatch(
      /\.setup-section--your-day \.frame-fields\s*\{[^}]*flex-wrap:\s*wrap/,
    );
  });
});
