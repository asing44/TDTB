/* strip-cal-work-color.test.ts — CSS regression for work-class calendar events
   on the day-frame strip. "Anything MINT always yellow": an event of capacity
   class 'work' (a Mint work block) is the minting family (--c-minting), not the
   calendar's together-blue (--c-event). Items of class 'fixed' stay blue.
   jsdom does no style resolution, so this reads app.css off disk the same way
   strip-mint-color.test.ts and row-spacing.test.ts do. */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const appCss = readFileSync(resolve(process.cwd(), "src/app.css"), "utf8");

/** Body of a standalone rule, matched on its exact selector + `{`. */
function rule(selector: string): string {
  const at = appCss.indexOf(`${selector} {`);
  if (at === -1) throw new Error(`${selector} rule not found in app.css`);
  return appCss.slice(at + selector.length + 2, appCss.indexOf("}", at));
}

describe("work-class calendar events on the day strip use the minting colour", () => {
  it("paints the work modifier from the minting token, not the event blue", () => {
    const body = rule(".strip-cal--work");
    expect(body).toMatch(/border-color:\s*color-mix\(in srgb,\s*var\(--c-minting\) 55%/);
    expect(body).toMatch(/background:\s*color-mix\(in srgb,\s*var\(--c-minting\) 30%/);
    expect(body).not.toMatch(/--c-event/);
  });

  it("keeps the skipped work variant yellow-tinted and dashed", () => {
    const body = rule(".strip-cal--work.strip-cal--skipped");
    expect(body).toMatch(/--c-minting/);
    expect(body).toMatch(/border-style:\s*dashed/);
    expect(body).not.toMatch(/--c-event/);
  });

  it("leaves the base calendar rule on the fixed-event blue", () => {
    const body = rule(".strip-cal");
    expect(body).toMatch(/--c-event/);
    expect(body).not.toMatch(/--c-minting/);
  });
});
