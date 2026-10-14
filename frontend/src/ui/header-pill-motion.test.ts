/* header-pill-motion.test.ts — the header's three controls (source-status
   pill, Refresh/Retry, Connections) must read as one pill family: a shared
   metric drives height/radius/padding/type, and the interactive controls get a
   short hover/active/focus motion while the non-interactive status pill never
   lifts. The footer alert pills share the same motion treatment.

   jsdom does no layout, so the cascade is read off disk the same way
   alert-pills-anchor.test.ts does. Header.tsx is read only to prove the shared
   class is actually wired to all three controls. */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const appCss = readFileSync(resolve(process.cwd(), "src/app.css"), "utf8");
const headerTsx = readFileSync(resolve(process.cwd(), "src/ui/Header.tsx"), "utf8");

/** Body of the FIRST `selector { … }` rule. */
function ruleBody(selector: string): string {
  const re = new RegExp(`(?:^|\\n)\\s*${selector}\\s*\\{([^}]*)\\}`);
  const match = appCss.match(re);
  if (!match) throw new Error(`selector not found in app.css: ${selector}`);
  return match[1];
}

/** Body of the `@media <query> { … }` block, brace-balanced. */
function mediaBody(query: string): string {
  const opener = new RegExp(`@media\\s*${query}\\s*\\{`);
  const match = opener.exec(appCss);
  if (!match) throw new Error(`no @media block for ${query}`);
  let depth = 1;
  let i = match.index + match[0].length;
  const start = i;
  while (i < appCss.length && depth > 0) {
    if (appCss[i] === "{") depth += 1;
    else if (appCss[i] === "}") depth -= 1;
    i += 1;
  }
  return appCss.slice(start, i - 1);
}

describe("the header's three controls share one pill metric", () => {
  it("applies the shared header-pill class to the status pill, Retry, and Connections", () => {
    expect(headerTsx).toMatch(/header-pill\s+health-pill\s/);
    expect(headerTsx).toMatch(/header-pill[^"]*health-pill__retry/);
    expect(headerTsx).toMatch(/header-pill[^"]*header__connections/);
  });

  it("declares the metric tokens once on the header right cluster", () => {
    const tokens = ruleBody("\\.app-header__right");
    expect(tokens).toMatch(/--header-pill-height:\s*32px/);
    expect(tokens).toMatch(/--header-pill-radius:\s*999px/);
    expect(tokens).toMatch(/--header-pill-pad-x:/);
    expect(tokens).toMatch(/--header-pill-border:/);
    expect(tokens).toMatch(/--header-pill-font-size:/);
    expect(tokens).toMatch(/--header-pill-font-weight:/);
    expect(tokens).toMatch(/--header-pill-line-height:/);
  });

  it("builds the shared pill from those tokens with centered content", () => {
    const pill = ruleBody("\\.header-pill");
    expect(pill).toMatch(/height:\s*var\(--header-pill-height/);
    expect(pill).toMatch(/min-height:\s*var\(--header-pill-height/);
    expect(pill).toMatch(/padding:\s*0\s+var\(--header-pill-pad-x/);
    expect(pill).toMatch(/border:\s*var\(--header-pill-border[^)]*\)\s+solid/);
    expect(pill).toMatch(/border-radius:\s*var\(--header-pill-radius/);
    expect(pill).toMatch(/font-size:\s*var\(--header-pill-font-size/);
    expect(pill).toMatch(/font-weight:\s*var\(--header-pill-font-weight/);
    expect(pill).toMatch(/line-height:\s*var\(--header-pill-line-height/);
    expect(pill).toMatch(/align-items:\s*center/);
  });

  it("keeps the status variants colour-only so they inherit the shared metric", () => {
    expect(ruleBody("\\.health-pill")).not.toMatch(/font-size:/);
    expect(ruleBody("\\.health-pill")).not.toMatch(/padding:/);
    expect(ruleBody("\\.health-pill")).not.toMatch(/border-radius:/);
    expect(ruleBody("\\.health-pill")).not.toMatch(/height:/);
    expect(ruleBody("\\.health-pill--refreshing")).not.toMatch(/font-size:/);
  });
});

describe("interactive pills animate on hover, press, and focus", () => {
  const motion = mediaBody("\\(prefers-reduced-motion:\\s*no-preference\\)");

  it("transitions colour, shadow, and transform on the interactive controls", () => {
    const transition = motion.match(/([^{}]*)\{([^}]*transition:[^}]*)\}/);
    expect(transition).not.toBeNull();
    const [, selector, body] = transition!;
    for (const target of [".health-pill__retry", ".header__connections", ".alert-pill"]) {
      expect(selector).toContain(target);
    }
    expect(selector).not.toContain(".health-pill,");
    expect(body).toMatch(/140ms\s+ease-out/);
    for (const prop of ["background-color", "border-color", "box-shadow", "transform"]) {
      expect(body).toContain(prop);
    }
  });

  it("lifts the interactive controls by 1px on hover", () => {
    const hover = motion.match(/([^{}]*)\{([^}]*translateY\(-1px\)[^}]*)\}/);
    expect(hover).not.toBeNull();
    const [, selector] = hover!;
    expect(selector).toContain(".health-pill__retry:hover");
    expect(selector).toContain(".header__connections:hover");
    expect(selector).toContain(".alert-pill:hover");
  });

  it("returns the interactive controls to rest while pressed", () => {
    const active = motion.match(/([^{}]*)\{([^}]*translateY\(0\)[^}]*)\}/);
    expect(active).not.toBeNull();
    const [, selector] = active!;
    expect(selector).toContain(":active");
  });

  it("never lifts the non-interactive status pill", () => {
    expect(motion).not.toMatch(/\.health-pill:hover/);
    expect(motion).not.toMatch(/\.header-pill:hover/);
  });

  it("keeps a visible focus-visible ring on the interactive controls", () => {
    const focus = appCss.match(/\.alert-pill:focus-visible[^{}]*\{([^}]*)\}/);
    expect(focus).not.toBeNull();
    expect(focus![1]).toMatch(/outline:\s*2px\s+solid/);
    expect(appCss).toMatch(/\.header__connections:focus-visible/);
  });
});
