/* row-compact-1100.test.ts — CSS contract for the Plan task row reflow at
   <=1100px (Planning 2a, 2026-10-14, superseding the 2026-10-13 "bolder pass").

   Planning 2a replaces the row's stripe / due / time / actions areas with the
   real five-column model (status toggle | body | duration | priority |
   actions). At <=1100px the row re-templates into a two-line grid: the status
   toggle spans both lines at the left, the body and priority take line 1, and
   the duration control and action cluster take line 2. The older fine-pointer
   hover reveal that hid the two secondary verbs is neutralized — 2a shows all
   five row verbs as expanded icon buttons at every width. The 768-1100px
   tablet layer still keeps the duration control on one inline row.

   jsdom does no layout, so the cascade is read off disk the same way
   row-actions-plan.test.ts and row-spacing.test.ts do. The contract keeps the
   protected guarantee: the reveal never targeted `.qrow__actions`, so the
   cluster is never hidden as a whole. */

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

const compact = lastMediaBody("screen\\s+and\\s*\\(max-width:\\s*1100px\\)");
const tablet = lastMediaBody(
  "\\(min-width:\\s*768px\\)\\s*and\\s*\\(max-width:\\s*1100px\\)",
);

describe("Plan task rows compact to a two-line grid at <=1100px", () => {
  it("re-templates the row into two stack lines", () => {
    const row = ruleIn(compact, "\\.qrow");
    expect(row).toMatch(
      /grid-template-columns:\s*118px\s+minmax\(0,\s*1fr\)\s+auto/,
    );
    expect(row).toMatch(/grid-template-rows:\s*auto auto/);
    // The older named-area template is neutralized so line placement wins.
    expect(row).toMatch(/grid-template-areas:\s*none/);
  });

  it("places every row child in its column and row", () => {
    expect(ruleIn(compact, "\\.qrow__assign")).toMatch(/grid-column:\s*1/);
    expect(ruleIn(compact, "\\.qrow__assign")).toMatch(/grid-row:\s*1\s*\/\s*3/);
    expect(ruleIn(compact, "\\.qrow__body")).toMatch(/grid-column:\s*2/);
    expect(ruleIn(compact, "\\.qrow__body")).toMatch(/grid-row:\s*1/);
    expect(ruleIn(compact, "\\.qrow__priority")).toMatch(/grid-column:\s*3/);
    expect(ruleIn(compact, "\\.qrow__priority")).toMatch(/grid-row:\s*1/);
    expect(ruleIn(compact, "\\.qrow__time")).toMatch(/grid-column:\s*2/);
    expect(ruleIn(compact, "\\.qrow__time")).toMatch(/grid-row:\s*2/);
  });

  it("keeps the status toggle at the top of its spanning cell", () => {
    expect(ruleIn(compact, "\\.qrow__assign")).toMatch(/align-self:\s*start/);
  });

  it("puts the actions on the second line, right-aligned", () => {
    const actions = ruleIn(compact, "\\.qrow__actions");
    expect(actions).toMatch(/grid-column:\s*3/);
    expect(actions).toMatch(/grid-row:\s*2/);
    expect(actions).toMatch(/justify-content:\s*flex-end/);
  });

  it("keeps the duration control on one inline row on the tablet layer", () => {
    expect(ruleIn(tablet, "\\.qrow__time")).toMatch(/flex-direction:\s*row/);
    expect(ruleIn(tablet, "\\.qrow__time-readout")).toMatch(/order:\s*0/);
  });
});

describe("every row verb stays visible at <=1100px (Planning 2a)", () => {
  it("restores the two secondary verb anchors the older reveal hid", () => {
    // The Planning 2a rule groups both anchors on `.qrow__actions`, so it is
    // the only grouped selector list for these classes.
    const restored =
      appCss.match(
        /\.qrow__actions \.qrow__tip--plan,\s*\.qrow__actions \.qrow__tip--drop_from_plan\s*\{([^}]*)\}/,
      )?.[1] ?? "";
    expect(restored).toMatch(/display:\s*inline-flex/);
  });

  it("never hides the primary verb or the More trigger", () => {
    expect(appCss).not.toMatch(/\.qrow__tip--done\s*\{[^}]*display:\s*none/);
    expect(appCss).not.toMatch(/\.row-more\s*\{[^}]*display:\s*none/);
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
