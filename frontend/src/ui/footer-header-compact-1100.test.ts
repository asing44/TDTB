/* footer-header-compact-1100.test.ts — CSS contract for the 2026-10-13
   footer/header compaction at <=1100px.

   Measured defect (900x1000): the Plan footer stood 2-3 rows tall — the left
   cluster (Day setup / blocked reason / Day budget chip) and the right cluster
   (Copy prompt / Revalidate / Sequence & commit) each wrapped, and every button
   carried a stacked sub-label — while the header wrapped to two rows when the
   source pill said "Sources degraded · commit blocked".

   This layer makes the footer at most two rows in EVERY state at <=1100px: the
   left cluster (Day setup + Day budget chip) and the right cluster (the dock
   controls) each stay on one nowrap line, the button sub-labels collapse, and
   the blocked/status message becomes a single truncating line in the alerts
   row above the controls. The header right cluster stays one nowrap row with a
   compact, ellipsized pill.

   jsdom does no layout, so the cascade is read off disk the same way
   row-compact-1100.test.ts and responsive.test.ts do. The contract keeps the
   protected guarantees: no `visibility: hidden`, no hiding of primary
   controls, and keyboard reachability is untouched (the message and sub-labels
   remain in the DOM/accessible tree, only their presentation changes). */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const appCss = readFileSync(resolve(process.cwd(), "src/app.css"), "utf8");

/** Body of the LAST brace-balanced `@media <query> { … }` block — the appended
    layer wins the cascade, so the last one is live. */
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

const compact = lastMediaBody("\\(max-width:\\s*1100px\\)");

describe("footer collapses to at most two rows at <=1100px", () => {
  it("keeps the left and right clusters on one nowrap line", () => {
    expect(ruleIn(compact, "\\.footer__row")).toMatch(/flex-wrap:\s*nowrap/);
    expect(ruleIn(compact, "\\.footer__left")).toMatch(/flex-wrap:\s*nowrap/);
    expect(ruleIn(compact, "\\.footer__right")).toMatch(/flex-wrap:\s*nowrap/);
  });

  it("keeps the dock controls on one nowrap line inside the right cluster", () => {
    expect(ruleIn(compact, "\\.footer__right\\s+\\.dock")).toMatch(
      /flex-wrap:\s*nowrap/,
    );
    expect(ruleIn(compact, "\\.footer__right\\s+\\.dock__controls")).toMatch(
      /flex-wrap:\s*nowrap/,
    );
  });

  it("shrinks the clusters instead of wrapping them", () => {
    // nowrap alone is not enough: the clusters carry min-width:0 so the
    // budget chip and the primary action can shrink rather than overflow.
    expect(ruleIn(compact, "\\.footer__left")).toMatch(/min-width:\s*0/);
    expect(ruleIn(compact, "\\.footer__right\\s+\\.dock__controls")).toMatch(
      /min-width:\s*0/,
    );
  });

  it("collapses the button sub-labels", () => {
    const sub = ruleIn(compact, "\\.footer\\s+\\.btn__sub");
    expect(sub).toMatch(/display:\s*none/);
    expect(sub).not.toMatch(/visibility:\s*hidden/);
  });

  it("makes the blocked/status message a single truncating line", () => {
    const blocked = ruleIn(compact, "\\.footer__blocked");
    expect(blocked).toMatch(/white-space:\s*nowrap/);
    expect(blocked).toMatch(/overflow:\s*hidden/);
    expect(blocked).toMatch(/text-overflow:\s*ellipsis/);
    expect(blocked).toMatch(/min-width:\s*0/);
  });

  it("keeps the Day setup link on one line and gives the status the flexible width", () => {
    // 2026-10-13 polish: at 900px "← Day setup" wrapped to two lines and the
    // status truncated because the budget chip kept its full width. The link
    // is now nowrap+rigid, the status grows/shrinks into the leftover space,
    // and the budget chip yields its width first (ellipsizing, never hidden).
    const back = ruleIn(compact, "\\.footer__back");
    expect(back).toMatch(/white-space:\s*nowrap/);
    expect(back).toMatch(/flex:\s*none/);
    expect(ruleIn(compact, "\\.footer__blocked")).toMatch(/flex:\s*1 1 auto/);
    const budget = ruleIn(compact, "\\.footer__budget");
    expect(budget).toMatch(/min-width:\s*0/);
    expect(budget).toMatch(/overflow:\s*hidden/);
    expect(budget).toMatch(/text-overflow:\s*ellipsis/);
  });

  it("keeps the Copy prompt and Day budget chip visible", () => {
    // Neither the clipboard fallback nor the allowance chip may be hidden by
    // this layer — the operator asked for both.
    expect(compact).not.toMatch(/\.footer__budget[^{]*\{[^}]*display:\s*none/);
    expect(compact).not.toMatch(
      /\[aria-label="Copy plan prompt for an external LLM"\][^{]*\{[^}]*display:\s*none/,
    );
  });
});

describe("header stays one row with degraded sources at <=1100px", () => {
  it("keeps the header right cluster on one nowrap line", () => {
    expect(ruleIn(compact, "\\.app-header__right")).toMatch(
      /flex-wrap:\s*nowrap/,
    );
  });

  it("compacts the pill and ellipsizes its text", () => {
    const pill = ruleIn(compact, "\\.app-header__right\\s+\\.health-pill");
    expect(pill).toMatch(/min-width:\s*0/);
    expect(pill).toMatch(/overflow:\s*hidden/);
    const text = ruleIn(compact, "\\.health-pill__text");
    expect(text).toMatch(/white-space:\s*nowrap/);
    expect(text).toMatch(/text-overflow:\s*ellipsis/);
    expect(text).toMatch(/overflow:\s*hidden/);
    expect(text).toMatch(/min-width:\s*0/);
  });

  it("forces the header and its stepper onto one nowrap row", () => {
    // 2026-10-13 polish: with degraded sources the header still wrapped to a
    // second row (stepper row, then pill+Retry+Connections row). The header
    // and the stepper no longer wrap; the right cluster stays on the row and
    // the pill ellipsizes (its title carries the full sentence) instead.
    expect(ruleIn(compact, "\\.app-header")).toMatch(/flex-wrap:\s*nowrap/);
    expect(ruleIn(compact, "\\.app-stepper")).toMatch(/flex-wrap:\s*nowrap/);
  });

  it("compacts the stepper and date block to make room on the row", () => {
    expect(ruleIn(compact, "\\.app-stepper__sep")).toMatch(/width:\s*10px/);
    expect(ruleIn(compact, "\\.app-stepper__step")).toMatch(/padding:\s*4px\s+8px/);
    expect(ruleIn(compact, "\\.app-header__date")).toMatch(/min-width:\s*0/);
  });

  it("compacts Retry and Connections without hiding them", () => {
    expect(ruleIn(compact, "\\.app-header__right\\s+\\.health-pill__retry")).toMatch(
      /white-space:\s*nowrap/,
    );
    expect(ruleIn(compact, "\\.app-header__right\\s+\\.header__connections")).toMatch(
      /white-space:\s*nowrap/,
    );
    expect(compact).not.toMatch(
      /\\.health-pill__retry[^{]*\{[^}]*display:\s*none/,
    );
    expect(compact).not.toMatch(
      /\\.header__connections[^{]*\{[^}]*display:\s*none/,
    );
  });
});

describe("compaction never hides controls or uses visibility:hidden", () => {
  it("does not use visibility:hidden anywhere in the layer", () => {
    expect(compact).not.toMatch(/visibility:\s*hidden/);
  });

  it("never hides the primary controls or the health pill", () => {
    for (const selector of [
      "\\.btn--primary",
      "\\.health-pill",
      "\\.footer__back",
      "\\.header__connections",
    ]) {
      expect(compact).not.toMatch(
        new RegExp(`${selector}\\s*\\{[^}]*display:\\s*none`),
      );
    }
  });
});
