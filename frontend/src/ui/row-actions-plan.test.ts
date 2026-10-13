/* row-actions-plan.test.ts — CSS guards for the Plan task row action set
   (2026-10-12 redesign). Two approved changes:

   1. At deskop width (>=1280px) the row is one quiet line: the action cluster
      is present but hidden at rest (opacity/pointer-events, never
      display:none) and revealed on `.qrow:hover` / `.qrow:focus-within`. Its
      grid track is reserved, so a revealed cluster never reflows the row.
      Below 1280px the cluster stays visible without a hover.
   2. The wide layout matches Planning Mockups screen B's action sizing: 13px
      labels, 6px/10px padding, 31px buttons at >=1500px, 1px borders, 6px
      radius, 6px gaps, and a 500px reserved action track.

   jsdom does no layout, so these read the cascade off disk the same way
   mint-slider-layout.test.ts and row-rhythm.test.ts do. */

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

describe("Plan task row actions reveal on hover at desktop width", () => {
  it("quiets the cluster at rest with opacity/pointer-events, not display", () => {
    expect(appCss).toMatch(/\.qrow__actions\s*\{[^}]*opacity:\s*0/);
    expect(appCss).toMatch(/\.qrow__actions\s*\{[^}]*pointer-events:\s*none/);
    expect(appCss).not.toMatch(/\.qrow__actions\s*\{[^}]*display:\s*none/);
  });

  it("reveals the cluster on hover, focus-within, and focus", () => {
    expect(appCss).toMatch(/\.qrow:hover\s+\.qrow__actions/);
    expect(appCss).toMatch(/\.qrow:focus-within\s+\.qrow__actions/);
    expect(appCss).toMatch(/\.qrow--focused\s+\.qrow__actions/);
    expect(appCss).toMatch(
      /\.qrow:hover\s+\.qrow__actions,[\s\S]*?pointer-events:\s*auto/,
    );
  });

  it("keeps the cluster visible without a hover below 1280px", () => {
    expect(appCss).toMatch(
      /@media\s*\(max-width:\s*1100px\)\s*\{\s*\.qrow__actions\s*\{[^}]*opacity:\s*1/,
    );
    // 1101-1279 keeps the cluster on its own grid line, always visible.
    expect(appCss).toMatch(
      /@media\s*\(min-width:\s*1101px\)\s+and\s+\(max-width:\s*1279px\)[\s\S]*?\.qrow__actions[\s\S]*?opacity:\s*1/,
    );
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

  it("reveals the steppers alongside the row actions", () => {
    expect(appCss).toMatch(/\.qrow__time\s+\.alloc-step\s*\{[^}]*opacity:\s*0/);
    expect(appCss).toMatch(/\.qrow:hover\s+\.qrow__time\s+\.alloc-step/);
    expect(appCss).toMatch(
      /\.qrow:focus-within\s+\.qrow__time\s+\.alloc-step/,
    );
  });
});

describe("Plan task row layout reserves the action track", () => {
  it("uses the mockup's reserved 500px action track at desktop width", () => {
    expect(appCss).toMatch(
      /grid-template-columns:\s*3px\s+96px\s+minmax\(0,\s*1fr\)\s+112px\s+300px\s+500px/,
    );
    expect(at1500(".qrow")).toMatch(/500px/);
  });

  it("puts the cluster in its own reserved grid track, never over the slider", () => {
    expect(appCss).toMatch(/\.qrow__actions\s*\{[^}]*grid-column:\s*6/);
    expect(appCss).not.toMatch(/\.qrow__actions\s*\{[^}]*position:\s*absolute/);
    expect(appCss).not.toMatch(/\.qrow__actions\s*\{[^}]*?(?<!min-)width:\s*0;/);
  });
});

describe("Plan task action sizing matches the mockup from 1500px", () => {
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
