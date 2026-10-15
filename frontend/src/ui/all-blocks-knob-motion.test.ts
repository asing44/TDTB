/* all-blocks-knob-motion.test.ts — the All blocks "Count today" switch knob
   moved by animating `left`, a layout property that forces a layout/paint on
   every frame. The knob must instead sit at a constant `left: 2px` and be
   translated on the on-state, so motion stays on the compositor. The
   reduced-motion guard must survive.

   jsdom does no layout, so the cascade is read off disk the same way
   alert-pills-anchor.test.ts does. */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const appCss = readFileSync(resolve(process.cwd(), "src/app.css"), "utf8");

/** Body of the first `selector { … }` rule. */
function ruleBody(selector: string): string {
  const re = new RegExp(`(?:^|\\n)\\s*${selector}\\s*\\{([^}]*)\\}`);
  const match = appCss.match(re);
  if (!match) throw new Error(`selector not found in app.css: ${selector}`);
  return match[1];
}

/** Every brace-balanced `@media … { … }` block, in source order. */
function mediaBlocks(): { query: string; body: string }[] {
  const out: { query: string; body: string }[] = [];
  const opener = /@media\s*([^{]+)\{/g;
  let match: RegExpExecArray | null;
  while ((match = opener.exec(appCss))) {
    const start = opener.lastIndex;
    let depth = 1;
    let i = start;
    while (i < appCss.length && depth > 0) {
      if (appCss[i] === "{") depth += 1;
      else if (appCss[i] === "}") depth -= 1;
      i += 1;
    }
    out.push({ query: match[1].trim(), body: appCss.slice(start, i - 1) });
    opener.lastIndex = i;
  }
  return out;
}

const switchKnob = () => ruleBody("\\.all-blocks__switch::after");
const switchKnobOn = () =>
  ruleBody('\\.all-blocks__switch\\[aria-checked="true"\\]::after');

describe("the All blocks switch knob animates with transform, not left", () => {
  it("keeps the knob at a constant left offset", () => {
    expect(switchKnob()).toMatch(/left:\s*2px/);
    expect(switchKnobOn()).not.toMatch(/left:/);
  });

  it("moves the on-state with a transform instead of a layout property", () => {
    expect(switchKnobOn()).toMatch(/transform:\s*translateX\(16px\)/);
  });

  it("never transitions `left` on the switch knob", () => {
    expect(switchKnob()).not.toMatch(/transition:\s*left/);
    const motion = mediaBlocks().filter(
      (block) =>
        block.query.includes("prefers-reduced-motion: no-preference") &&
        block.body.includes(".all-blocks__switch::after"),
    );
    expect(motion.length).toBe(1);
    expect(motion[0].body).toMatch(
      /\.all-blocks__switch::after\s*\{[^}]*transition:\s*transform/,
    );
    expect(motion[0].body).not.toMatch(/transition:\s*left/);
  });
});
