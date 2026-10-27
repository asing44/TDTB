/* more-menu-consistency.test.tsx — Planning 2a (2026-10-14): the Plan row's
   More (⋯) menu rendered every entry as ONE boxed row in a fixed group order.

   RETIRED 2026-10-16 (task-actions F7): the per-row More menu is gone. Its
   source verbs are the expanded row's Source menu (SourceMenu.tsx) and its
   planning verbs are the expanded row's line-1 controls, so the four DOM
   ordering/style tests that pinned More's item order, checkbox indicator,
   divider and armed boxes were retired with the component. Item order and
   composition of the surviving menu are pinned in source-menu.test.tsx, and
   the mounted behaviour in task-actions-integration.test.tsx. See the
   retired-test ledger, design/task-actions/retired-tests.md.

   What stays, deliberately: the CSS contract for `.row-more__*`. Those rules
   remain in app.css (operator decision: do not rewrite the stylesheet for a
   component removal), so their contract is still asserted here and cannot rot
   silently. This file is a CSS-contract guard only; it mounts nothing. */

import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const APP_CSS = readFileSync(resolve(process.cwd(), "src/app.css"), "utf8");

/** Body of the LAST rule whose selector is exactly `selector`. */
function lastRuleBody(selector: string): string {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const re = new RegExp(`(?:^|\\n)\\s*${escaped}\\s*\\{([^}]*)\\}`, "g");
  const hits = Array.from(APP_CSS.matchAll(re), (m) => m[1]);
  if (hits.length === 0) throw new Error(`selector not found in app.css: ${selector}`);
  return hits[hits.length - 1];
}

describe("More menu: CSS contract for the boxed row", () => {
  const item = lastRuleBody(".row-more__menu .row-more__item");

  it("paints one box: hairline border, shared radius, one padding", () => {
    expect(item).toMatch(/border:\s*0\.5px solid var\(--t-border\)/);
    expect(item).toMatch(/border-radius:\s*var\(--radius-sm\)/);
    expect(item).toMatch(/padding:\s*8px 10px/);
    expect(item).toMatch(/min-width:\s*0/);
  });

  it("keeps the title 14px/500 and the description 12px muted", () => {
    expect(item).toMatch(/font-size:\s*14px/);
    expect(item).toMatch(/font-weight:\s*500/);
    expect(item).toMatch(/text-align:\s*left/);
    const detail = lastRuleBody(
      ".row-more__menu .row-more__item > .row-more__item-detail",
    );
    expect(detail).toMatch(/font-size:\s*12px/);
    expect(detail).toMatch(/font-weight:\s*400/);
    expect(detail).toMatch(/color:\s*var\(--t-muted\)/);
  });

  it("gives every item the same hover, active and focus-visible state", () => {
    const hover = lastRuleBody(".row-more__menu .row-more__item:hover");
    expect(hover).toMatch(/background:\s*var\(--t-surface-2\)/);
    expect(lastRuleBody(".row-more__menu .row-more__item:active")).toBeTruthy();
    expect(
      lastRuleBody(".row-more__menu .row-more__item:focus-visible"),
    ).toMatch(/outline:/);
  });

  it("scopes the danger colour to the armed confirm, not the resting item", () => {
    // The base box owns the resting text colour, so Delete is not dimmed while
    // it is actionable.
    expect(item).toMatch(/color:\s*var\(--t-text\)/);
    const armed = lastRuleBody(
      ".row-more__menu .row-more__item.alloc-verb--armed:hover",
    );
    expect(armed).toMatch(/color:\s*var\(--c-overflow\)/);
    expect(armed).toMatch(/border-color:\s*var\(--c-overflow\)/);
    expect(armed).toMatch(/padding:\s*8px 10px/);
  });
});

/* Source menu CSS contract (source-menu.css): the 32px kebab trigger, 40px+
   item rows, a divider before the destructive item, and token-only colours so
   both themes follow. */
const SOURCE_CSS = readFileSync(resolve(process.cwd(), "src/ui/source-menu.css"), "utf8");

describe("Source menu: CSS contract", () => {
  const rule = (selector: string) => {
    const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    const m = SOURCE_CSS.match(new RegExp(`(?:^|\\n)${escaped}\\s*\\{([^}]*)\\}`));
    if (!m) throw new Error(`selector not found: ${selector}`);
    return m[1];
  };

  it("sizes the trigger to 32px like the row's icon buttons", () => {
    const t = rule(".source-menu .source-menu__trigger");
    expect(t).toMatch(/width:\s*32px/);
    expect(t).toMatch(/height:\s*32px/);
  });

  it("keeps item rows at least 40px tall and the popover fixed-position", () => {
    expect(rule(".source-menu .source-menu__item")).toMatch(/min-height:\s*(4\d|[5-9]\d)px/);
    expect(rule(".source-menu .source-menu__popover")).toMatch(/position:\s*fixed/);
  });

  it("uses theme tokens, never hex colours outside var() fallbacks", () => {
    const stripped = SOURCE_CSS.replace(/var\([^)]*\)/g, "").replace(/rgba\(0, 0, 0, [\d.]+\)/g, "");
    expect(stripped).not.toMatch(/#[0-9a-f]{3,8}\b/i);
  });
});
