/* drawer-css-contract.test.ts — the approval drawer's alert rows and long
   values used markup classes with no cascade rules at all (`.alerts__list`,
   `.alerts__item`, `.alerts__item--error`, `.alerts__item--warning`), and the
   flexible columns that were supposed to shrink lazily instead clipped.

   jsdom does no layout, so the cascade is read off disk the same way
   alert-pills-anchor.test.ts does. */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const appCss = readFileSync(resolve(process.cwd(), "src/app.css"), "utf8");

/** Body of the FIRST `selector { … }` rule. */
function ruleBody(selector: string): string {
  const re = new RegExp(`(?:^|\\n)\\s*${selector}\\s*\\{([^}]*)\\}`);
  const match = appCss.match(re);
  if (!match) throw new Error(`selector not found in app.css: ${selector}`);
  return match[1];
}

describe("alert items inside the approval/results drawer have real rules", () => {
  it("resets the list", () => {
    expect(ruleBody("\\.alerts__list")).toMatch(/list-style:\s*none/);
  });

  it("lays an alert item out as a wrapping flex row with an icon and tint", () => {
    const body = ruleBody("\\.alerts__item");
    expect(body).toMatch(/display:\s*flex/);
    expect(body).toMatch(/gap:\s*8px/);
    expect(body).toMatch(/padding:/);
    expect(body).toMatch(/min-width:\s*0/);
    expect(body).toMatch(/border-left:/);
  });

  it("tints error and warning items by severity", () => {
    expect(ruleBody("\\.alerts__item--error")).toMatch(/background:\s*color-mix/);
    expect(ruleBody("\\.alerts__item--warning")).toMatch(/background:\s*color-mix/);
  });

  it("fixes the icon width and lets the text wrap", () => {
    expect(appCss).toMatch(
      /\.alerts__item\s*>\s*\[aria-hidden="true"\]\s*\{[^}]*width:/,
    );
    expect(appCss).toMatch(
      /\.alerts__item\s*>\s*span:not\(\[aria-hidden="true"\]\)\s*\{[^}]*overflow-wrap:\s*anywhere/,
    );
  });
});

describe("long drawer values wrap instead of overflowing", () => {
  it("keeps the flexible write-row name shrinkable", () => {
    expect(ruleBody("\\.write-row__name")).toMatch(/min-width:\s*0/);
  });

  it("wraps long write-row paths", () => {
    expect(ruleBody("\\.write-row__path")).toMatch(/overflow-wrap:\s*anywhere/);
  });

  it("wraps long verification rows", () => {
    expect(ruleBody("\\.verify-list li")).toMatch(/overflow-wrap:\s*anywhere/);
  });
});
