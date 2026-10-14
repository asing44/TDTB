/* anchor-actions-icons.test.ts — CSS contract for the 2026-10-13 polish pass
   on the Anchors-card per-anchor icon actions.

   Measured defect (900px): the three actions (Skip/Include, Adjust time,
   Adjust duration) rendered as small sharp 32px squares on their own row
   under every chip. This layer matches them to the Plan row icon buttons —
   44px touch floor, `--radius-sm`, the 0.5px mixed border and the
   `--t-surface-2` fill — and moves them inline at the right end of the chip
   row above 700px, wrapping below only on narrower widths.

   jsdom does no layout, so the cascade is read off disk the same way
   row-compact-1100.test.ts and footer-header-compact-1100.test.ts do. The
   protected guarantees hold: no `visibility:hidden`, no hiding of controls,
   and the touch target keeps its 44px floor. */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const appCss = readFileSync(resolve(process.cwd(), "src/app.css"), "utf8");

/** `appCss` with every `@media { … }` block removed, so a media-scoped
    override can never shadow the base rule under test. */
function stripMedia(): string {
  let out = "";
  let i = 0;
  while (i < appCss.length) {
    const at = appCss.indexOf("@media", i);
    if (at < 0) {
      out += appCss.slice(i);
      break;
    }
    out += appCss.slice(i, at);
    const brace = appCss.indexOf("{", at);
    if (brace < 0) break;
    let depth = 1;
    let j = brace + 1;
    while (j < appCss.length && depth > 0) {
      if (appCss[j] === "{") depth += 1;
      else if (appCss[j] === "}") depth -= 1;
      j += 1;
    }
    i = j;
  }
  return out;
}

/** Body of the LAST top-level `selector { … }` rule. */
function rule(selector: string): string {
  const re = new RegExp(`(?:^|\\n)\\s*${selector}\\s*\\{([^}]*)\\}`, "g");
  const hits = Array.from(stripMedia().matchAll(re), (m) => m[1]);
  if (hits.length === 0) throw new Error(`no rule for ${selector}`);
  return hits[hits.length - 1];
}

/** Body of the LAST brace-balanced `@media <query> { … }` block. */
function lastMediaBody(query: string): string {
  const opener = new RegExp(`@media\\s*${query}\\s*\\{`, "g");
  let last: { start: number; end: number } | null = null;
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
    last = { start, end: i - 1 };
  }
  if (!last) throw new Error(`no @media block for ${query}`);
  return appCss.slice(last.start, last.end).replace(/\/\*[\s\S]*?\*\//g, "");
}

const action = "\\.anchor-actions\\s+\\.anchor-action";

describe("Anchors-card icon actions match the Plan row icon buttons", () => {
  it("uses the row icon-button sizing, radius, border, and fill tokens", () => {
    const face = rule(action);
    expect(face).toMatch(/width:\s*44px/);
    expect(face).toMatch(/min-width:\s*44px/);
    expect(face).toMatch(/min-height:\s*44px/);
    expect(face).toMatch(/border-radius:\s*var\(--radius-sm\)/);
    expect(face).toMatch(
      /border:\s*0\.5px solid color-mix\(in srgb,\s*var\(--t-border\)\s*85%,\s*transparent\)/,
    );
    expect(face).toMatch(/background:\s*var\(--t-surface-2\)/);
  });

  it("is no longer a 32px-only sharp square", () => {
    expect(rule(action)).not.toMatch(/width:\s*32px/);
  });

  it("keeps the 44px touch floor on the controls", () => {
    expect(rule(action)).toMatch(/min-height:\s*44px/);
    expect(rule(action)).not.toMatch(/visibility:\s*hidden/);
  });
});

describe("icon actions sit inline at the right end of the chip row", () => {
  it("lays the chip + actions out as one aligned head row", () => {
    const head = rule("\\.anchor-tile__head");
    expect(head).toMatch(/display:\s*flex/);
    expect(head).toMatch(/align-items:\s*center/);
    expect(rule("\\.anchor-tile__head\\s+\\.anchor-actions")).toMatch(/flex:\s*none/);
  });

  it("keeps the chip flexible so the actions stay on its right edge", () => {
    const chip = rule("\\.anchor-tile__head\\s+\\.anchor-chip");
    expect(chip).toMatch(/flex:\s*1 1 auto/);
    expect(chip).toMatch(/min-width:\s*0/);
  });

  it("wraps the actions below the chip only under 700px", () => {
    const narrow = lastMediaBody("\\(max-width:\\s*699px\\)");
    expect(narrow).toMatch(/\.anchor-tile__head\s*\{[^}]*flex-wrap:\s*wrap/);
    expect(narrow).toMatch(/\.anchor-actions\s*\{[^}]*width:\s*100%/);
    expect(narrow).not.toMatch(/visibility:\s*hidden/);
  });

  it("leaves the not-counted inline row intact", () => {
    expect(rule("\\.not-counted__item\\s+\\.anchor-actions")).toMatch(
      /display:\s*inline-flex/,
    );
  });
});
