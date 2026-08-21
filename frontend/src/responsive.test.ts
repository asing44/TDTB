/* responsive.test — CSS regression for the frozen responsive contract.
   jsdom does no real style resolution and vitest stubs CSS imports, so the
   a11y suite reads tokens.css off disk (see a11y.test.tsx); this suite does
   the same for app.css to pin the IMP-10 F-1 narrow-layout fix:
   .calendar-impact__row must not keep fixed-wide grid tracks under
   @media (max-width: 767px) (116+160+64+70+92+124px + gaps cannot fit 375px),
   and the desktop row rule must stay untouched. */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const appCss = readFileSync(resolve(process.cwd(), "src/app.css"), "utf8");

/** Extract the brace-balanced body of the first block whose opening rule
    matches `open` (e.g. /@media \(max-width: 767px\)/). */
function extractBlock(css: string, open: RegExp): string | null {
  const start = css.search(open);
  if (start < 0) return null;
  const openIdx = css.indexOf("{", start);
  if (openIdx < 0) return null;
  let depth = 0;
  for (let i = openIdx; i < css.length; i++) {
    if (css[i] === "{") depth++;
    else if (css[i] === "}") {
      depth--;
      if (depth === 0) return css.slice(openIdx + 1, i);
    }
  }
  return null;
}

describe("narrow layout (≤767px) has no calendar-impact overflow (IMP-10 F-1)", () => {
  const narrow = extractBlock(appCss, /@media\s*\(max-width:\s*767px\)/);

  it("the narrow media rule exists", () => {
    expect(narrow).toBeTruthy();
  });

  it("calendar-impact row is re-templated to a minmax(0) flexible track", () => {
    // Root cause: fixed-sum tracks cannot shrink below 375px (measured
    // scrollWidth 799 vs clientWidth 375). The narrow override must size the
    // flexible column from 0 so the grid always fits its container.
    expect(narrow).toMatch(
      /\.calendar-impact__row\s*\{[^}]*grid-template-columns:\s*116px\s+minmax\(0,\s*1fr\)/,
    );
  });

  it("the attend control spans the full row width on narrow screens", () => {
    expect(narrow).toMatch(
      /\.calendar-impact__attend\s*\{[^}]*grid-column:\s*1\s*\/\s*-1/,
    );
  });

  it("the work envelope summary wraps instead of forcing a bleed", () => {
    expect(narrow).toMatch(
      /\.calendar-impact__work\s*\{[^}]*white-space:\s*normal/,
    );
  });

  it("allocator rows are re-templated to stripe + flexible column", () => {
    // The measured 799px scrollWidth came from the queue grid min
    // (3+128+256+348px + gaps), which no 375px viewport can fit.
    expect(narrow).toMatch(
      /\.qrow\s*\{[^}]*grid-template-columns:\s*3px\s+minmax\(0,\s*1fr\)/,
    );
  });

  it("the aligned column header is hidden on narrow screens", () => {
    expect(narrow).toMatch(/\.queue__cols\s*\{[^}]*display:\s*none/);
  });

  it("row actions span the full width and wrap on narrow screens", () => {
    expect(narrow).toMatch(
      /\.qrow__actions\s*\{[^}]*grid-column:\s*1\s*\/\s*-1/,
    );
    expect(narrow).toMatch(/\.qrow__actions\s*\{[^}]*flex-wrap:\s*wrap/);
  });

  it("secondary band readouts are hidden so headers fit 375px", () => {
    expect(narrow).toMatch(/\.band__over\s*\{[^}]*display:\s*none/);
  });

  it("desktop row grid is untouched (7 fixed tracks + auto)", () => {
    expect(appCss).toMatch(
      /\.calendar-impact__row\s*\{[^}]*grid-template-columns:\s*116px\s+minmax\(160px,\s*1fr\)\s+64px\s+70px\s+92px\s+124px\s+auto/,
    );
  });

  it("desktop allocator grid keeps a shrinkable action track", () => {
    expect(appCss).toMatch(
      /\.queue__cols,\s*\n\.qrow\s*\{[^}]*grid-template-columns:\s*3px\s+minmax\(0,\s*1fr\)\s+128px\s+256px\s+minmax\(0,\s*1fr\)/,
    );
  });
});
