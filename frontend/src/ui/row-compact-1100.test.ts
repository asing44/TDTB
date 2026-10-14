/* row-compact-1100.test.ts — CSS contract for the "bolder pass" row density
   fix at <=1100px (2026-10-13).

   Measured defect: at ~900px every Plan tasks row stacked four lines
   (title/source, due, duration control, actions) and stood ~190px tall. This
   layer re-templates the row to a named two-line grid (stripe | body | due on
   line 1; stripe | time | actions on line 2), folds the duration readout
   inline beside the slider, and — on fine pointers only — reveals the three
   secondary verbs (Exclude today, Exact duration, Leave as-is) on
   row hover/keyboard focus while Mark complete and More stay visible.

   jsdom does no layout, so the cascade is read off disk the same way
   row-actions-plan.test.ts and row-spacing.test.ts do. The contract keeps the
   protected guarantees: the reveal targets individual verb anchors, never
   `.qrow__actions`, so the cluster is never hidden as a whole. */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const appCss = readFileSync(resolve(process.cwd(), "src/app.css"), "utf8");

/** Body of the LAST brace-balanced `@media <query> { … }` block — the
    appended layer wins the cascade, so the last one is live. */
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

/** Body of the LAST `selector { … }` rule inside a block. */
function ruleIn(body: string, selector: string): string {
  const re = new RegExp(`(?:^|\\n)\\s*${selector}\\s*\\{([^}]*)\\}`, "g");
  const hits = Array.from(body.matchAll(re), (x) => x[1]);
  if (hits.length === 0) throw new Error(`no rule for ${selector}`);
  return hits[hits.length - 1];
}

const compact = lastMediaBody(
  "\\(min-width:\\s*768px\\)\\s*and\\s*\\(max-width:\\s*1100px\\)",
);

describe("Plan task rows compact to a two-line grid at <=1100px", () => {
  it("re-templates the row with named grid areas", () => {
    const row = ruleIn(compact, "\\.qrow");
    expect(row).toMatch(/grid-template-areas/);
    expect(row).toMatch(/"stripe\s+body\s+due"/);
    // Actions take the flexible column and time the max-content column, so a
    // dense verb set wraps inside its cell instead of clipping the slider.
    expect(row).toMatch(/"stripe\s+actions\s+time"/);
  });

  it("places every row child in its named area", () => {
    const areas: Array<[string, string]> = [
      ["\\.qrow__stripe", "stripe"],
      ["\\.qrow__body", "body"],
      ["\\.qrow__due", "due"],
      ["\\.qrow__time", "time"],
      ["\\.qrow__actions", "actions"],
    ];
    for (const [selector, area] of areas) {
      expect(ruleIn(compact, selector)).toMatch(
        new RegExp(`grid-area:\\s*${area}`),
      );
    }
  });

  it("keeps the duration control on one inline row with the readout", () => {
    const time = ruleIn(compact, "\\.qrow__time");
    expect(time).toMatch(/flex-direction:\s*row/);
    // The readout trails the slider, the desktop convention, instead of the
    // stacked readout-above-slider order it takes below 1440px.
    expect(ruleIn(compact, "\\.qrow__time-readout")).toMatch(/order:\s*0/);
  });

  it("puts the actions on the same line as the duration control", () => {
    const actions = ruleIn(compact, "\\.qrow__actions");
    expect(actions).toMatch(/grid-area:\s*actions/);
    expect(actions).toMatch(/justify-content:\s*flex-end/);
  });
});

describe("secondary row verbs reveal only on fine-pointer hover/focus", () => {
  const reveal = () =>
    lastMediaBody(
      "\\(max-width:\\s*1100px\\)\\s*and\\s*\\(hover:\\s*hover\\)\\s*and\\s*\\(pointer:\\s*fine\\)",
    );

  it("hides the three secondary verbs at rest on fine pointers", () => {
    expect(ruleIn(reveal(), "\\.qrow__tip--plan")).toMatch(/display:\s*none/);
    expect(ruleIn(reveal(), "\\.qrow__tip--drop_from_plan")).toMatch(
      /display:\s*none/,
    );
  });

  it("reveals them on row hover and on keyboard focus", () => {
    expect(reveal()).toMatch(/\.qrow:hover\s+\.qrow__tip--plan/);
    expect(reveal()).toMatch(/\.qrow:focus-within\s+\.qrow__tip--plan/);
    expect(reveal()).toMatch(/\.qrow:hover\s+\.qrow__tip--drop_from_plan/);
    expect(reveal()).toMatch(/\.qrow:focus-within\s+\.qrow__tip--drop_from_plan/);
  });

  it("never hides the primary verb or the More trigger", () => {
    expect(reveal()).not.toMatch(/\.qrow__tip--done\s*\{[^}]*display:\s*none/);
    expect(reveal()).not.toMatch(/\.row-more\s*\{[^}]*display:\s*none/);
  });
});

describe("no layout band hides the whole action cluster", () => {
  it("has no row-level action hiding or hover reveal", () => {
    expect(appCss).not.toMatch(/\.qrow__actions\s*\{[^}]*display:\s*none/);
    expect(appCss).not.toMatch(/\.qrow__actions\s*\{[^}]*opacity:\s*0/);
    expect(appCss).not.toMatch(/\.qrow__actions\s*\{[^}]*pointer-events:\s*none/);
    expect(appCss).not.toMatch(/\.qrow:hover\s+\.qrow__actions/);
    expect(appCss).not.toMatch(/\.qrow:focus-within\s+\.qrow__actions/);
    expect(appCss).not.toMatch(/@media\s*\(hover:\s*none\)/);
  });
});
