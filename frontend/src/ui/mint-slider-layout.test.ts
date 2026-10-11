/* mint-slider-layout.test.ts — the Mint blocks slider must hold still while it
   is dragged. The thumb's x and the summary row's line count both depended on
   the text beside it: each step changed "2hr" to "2hr 30min" and "Only 9 free"
   to "Only 8 free", which shifted the track under the pointer. The value fed
   back into the layout and oscillated. jsdom does no layout, so these read the
   cascade off disk the same way strip-mint-color.test.ts does. */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const appCss = readFileSync(resolve(process.cwd(), "src/app.css"), "utf8");

/** Body of the `selector { … }` rule. Matches a full selector only, so
    `.frame-summary` does not also match `.frame-summary__mint`. */
function ruleBody(selector: string): string {
  const re = new RegExp(`(?:^|\\n)${selector}\\s*\\{([^}]*)\\}`);
  const match = appCss.match(re);
  if (!match) throw new Error(`selector not found in app.css: ${selector}`);
  return match[1];
}

describe("Mint blocks slider holds still while dragged", () => {
  it("the summary row does not wrap, so the slider cannot change lines", () => {
    expect(ruleBody("\\.frame-summary")).toMatch(/flex-wrap:\s*nowrap/);
  });

  it("the slider group is anchored right, so text width cannot move the thumb", () => {
    expect(ruleBody("\\.frame-summary__blocks")).toMatch(/margin-left:\s*auto/);
  });

  it("the slider stays at the top of the row, so a taller row grows downward", () => {
    // The text beside the slider can wrap to two or three lines as its label
    // changes length. When the row is centered, every wrap moves the thumb
    // vertically. Pinning the group to the top, at the same 44px line the
    // Reset button sets, keeps the thumb's offset in the row fixed.
    expect(ruleBody("\\.frame-summary__blocks")).toMatch(/align-self:\s*flex-start/);
    expect(ruleBody("\\.frame-summary__blocks")).toMatch(/min-height:\s*44px/);
  });

  it("the slider group is never squeezed narrower than its track", () => {
    expect(ruleBody("\\.frame-summary__blocks")).toMatch(/flex-shrink:\s*0/);
  });

  it("the Reset button keeps its width, so the slider beside it does not move", () => {
    expect(ruleBody("\\.frame-summary__reset")).toMatch(/flex-shrink:\s*0/);
    expect(ruleBody("\\.frame-summary__reset")).toMatch(/white-space:\s*nowrap/);
  });

  it("the text beside the slider shrinks and wraps inside its own box", () => {
    expect(ruleBody("\\.frame-summary__mint")).toMatch(/min-width:\s*0/);
    expect(ruleBody("\\.frame-summary__free")).toMatch(/min-width:\s*0/);
  });
});
