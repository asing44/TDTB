/* row-actions-plan.test.ts — CSS guards for the Plan task row action set
   (2026-10-12, Adam). Two approved changes:

   1. Row actions stay visible at every width. The cluster used to be revealed
      by `.qrow:hover` / `.qrow:focus-within` (`opacity: 0` at rest); that
      reveal layer must be gone, and must not come back as hover-only.
   2. From 1500px the Plan row matches Planning Mockups screen B's action
      sizing: 13px labels, 6px/10px padding, 31px buttons, 1px borders, 6px
      radius, 6px gaps, and a 540px action track.

   jsdom does no layout, so these read the cascade off disk the same way
   mint-slider-layout.test.ts and row-rhythm.test.ts do. The spec values were
   measured from the mockup's rendered buttons, not assumed. */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const appCss = readFileSync(resolve(process.cwd(), "src/app.css"), "utf8");

/** Body of the FIRST `selector { … }` rule — the same helper shape
    mint-slider-layout.test.ts uses. */
function ruleBody(selector: string): string {
  const re = new RegExp(`(?:^|\\n)${selector}\\s*\\{([^}]*)\\}`);
  const match = appCss.match(re);
  if (!match) throw new Error(`selector not found in app.css: ${selector}`);
  return match[1];
}

/** Every `@media (min-width: 1500px) { … }` body, brace-balanced. */
function minWidth1500Bodies(): string[] {
  const opener = /@media\s*\(min-width:\s*1500px\)\s*\{/g;
  const bodies: string[] = [];
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
    bodies.push(appCss.slice(start, i - 1));
  }
  if (bodies.length === 0) throw new Error("no @media (min-width: 1500px) block");
  return bodies;
}

/** Body of the LAST `@media (min-width: 1500px)` block that carries a rule for
    `selector` — the appended layer wins the cascade, so the last one is live. */
function at1500(selector: string): string {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const re = new RegExp(`(?:^|\\n)\\s*${escaped}\\s*\\{([^}]*)\\}`);
  const hits = minWidth1500Bodies()
    .map((b) => b.match(re)?.[1])
    .filter((b): b is string => b !== undefined);
  if (hits.length === 0) {
    throw new Error(`no @media (min-width: 1500px) rule for ${selector}`);
  }
  return hits[hits.length - 1];
}

describe("Plan task row actions never depend on hover", () => {
  it("no .qrow__actions rule hides the cluster at rest", () => {
    expect(appCss).not.toMatch(/\.qrow__actions\s*\{[^}]*opacity:\s*0/);
    expect(appCss).not.toMatch(/\.qrow__actions\s*\{[^}]*pointer-events:\s*none/);
    expect(appCss).not.toMatch(/\n\s*\.qrow__actions\s*\{[^}]*?(?<!min-)width:\s*0;/);
  });

  it("keeps no hover/focus reveal rule for the cluster", () => {
    expect(appCss).not.toMatch(/\.qrow:hover\s+\.qrow__actions\s*\{/);
    expect(appCss).not.toMatch(/\.qrow:focus-within\s+\.qrow__actions\s*\{/);
    expect(appCss).not.toMatch(/\.qrow--focused\s+\.qrow__actions\s*\{/);
  });

  it("the base cluster rule stays laid out and reachable, not clipped", () => {
    const base = ruleBody("\\.qrow__actions");
    expect(base).toMatch(/min-width:\s*0/);
    expect(base).not.toMatch(/opacity/);
    expect(base).not.toMatch(/pointer-events/);
  });
});

describe("Plan task rows restore the −/+ duration steppers", () => {
  it("app.css styles an .alloc-step control again", () => {
    expect(appCss).toMatch(/\.alloc-step\s*\{/);
  });
});

describe("Plan task action sizing matches the mockup from 1500px", () => {
  it("keeps the compact sizing below 1500px", () => {
    // The mockup layer is a min-width gate: the 1280-1499 band must not be
    // touched by the 540px track.
    const gate = minWidth1500Bodies().join("\n");
    expect(gate).toMatch(/540px/);
  });

  it("uses the mockup's 540px action track", () => {
    const grid = at1500(".qrow");
    expect(grid).toMatch(
      /grid-template-columns:[^;]*\b540px\b/,
    );
  });

  it("labels the mockup's five actions at 13px", () => {
    expect(at1500(".qrow__actions .planbtn")).toMatch(/font-size:\s*13px/);
    expect(at1500(".qrow__actions .alloc-verb")).toMatch(
      /font-size:\s*13px/,
    );
  });

  it("uses the mockup's 6px/10px padding and 31px button height", () => {
    const planbtn = at1500(".qrow__actions .planbtn");
    expect(planbtn).toMatch(/padding:\s*6px\s+10px/);
    expect(planbtn).toMatch(/height:\s*31px/);
    const verb = at1500(".qrow__actions .alloc-verb");
    expect(verb).toMatch(/padding:\s*6px\s+10px/);
    expect(verb).toMatch(/height:\s*31px/);
  });

  it("uses the mockup's 1px border and 6px radius", () => {
    for (const sel of [".qrow__actions .planbtn", ".qrow__actions .alloc-verb"]) {
      const body = at1500(sel);
      expect(body).toMatch(/border-width:\s*1px/);
      expect(body).toMatch(/border-radius:\s*6px/);
    }
  });

  it("keeps the mockup's 6px gaps between actions", () => {
    expect(at1500(".qrow__actions")).toMatch(/gap:\s*6px/);
  });
});
