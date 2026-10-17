/* queue-hint-layout.test.tsx — CSS contract for the Plan shortcut hint
   (2026-10-17). The hint is a sibling of .plan-grid, so it spanned the full
   queue width and its note ran under the capacity card, flush against the last
   row. It must stay in the task-list column, keep a gap above, and stack its
   note under the keys. jsdom does no layout, so the cascade is read off disk. */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const appCss = readFileSync(resolve(process.cwd(), "src/app.css"), "utf8");

function topLevelBlocks(selector: string): string {
  const re = new RegExp(`(?:^|\\n)${selector}\\s*\\{([^}]*)\\}`, "g");
  const out: string[] = [];
  let m: RegExpExecArray | null;
  while ((m = re.exec(appCss)) !== null) out.push(m[1]);
  return out.join("\n");
}

describe("the shortcut hint belongs to the task-list column", () => {
  it("is held to the list column beside the 380px capacity track", () => {
    expect(appCss).toMatch(
      /@media \(min-width:\s*1101px\)\s*\{\s*\.queue__hint\s*\{[^}]*max-width:\s*calc\(100%\s*-\s*380px\s*-\s*28px\)/,
    );
  });

  it("keeps a gap above so it never touches the last row", () => {
    expect(topLevelBlocks("\\.queue__hint")).toMatch(/margin-top:\s*12px/);
  });

  it("stacks the note under the keys, left aligned, never right-floated", () => {
    const note = topLevelBlocks("\\.queue__hint-note");
    expect(note).toMatch(/flex-basis:\s*100%/);
    expect(note).toMatch(/text-align:\s*left/);
    expect(note).not.toMatch(/margin-left:\s*auto/);
  });
});
