/* plan-width-stability.test.tsx — CSS contract for the Plan screen width
   (2026-10-17). The Plan column used to size to its content: .queue sat in the
   column-flex .cockpit__main with auto inline margins and no width, so
   expanding or staging a row changed the column width. The column must have a
   definite width and a wide, token-driven cap. jsdom does no layout, so the
   cascade is read off disk like row-rhythm.test.ts. */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const appCss = readFileSync(resolve(process.cwd(), "src/app.css"), "utf8");

/** Declaration bodies of every top-level `selector { … }` rule. */
function topLevelBlocks(selector: string): string[] {
  const out: string[] = [];
  const re = new RegExp(`(?:^|\\n)${selector}\\s*\\{([^}]*)\\}`, "g");
  let m: RegExpExecArray | null;
  while ((m = re.exec(appCss)) !== null) out.push(m[1]);
  return out;
}

describe("the Plan column has a stable, wide width", () => {
  const queue = topLevelBlocks("\\.queue").join("\n");

  it("sets an explicit width so auto margins cannot shrink-to-fit", () => {
    expect(queue).toMatch(/(^|[\s;])width:\s*100%/);
  });

  it("never sizes the column to its content", () => {
    expect(queue).not.toMatch(/width:\s*(fit-content|max-content|min-content)/);
    expect(queue).not.toMatch(/inline-size:\s*(fit-content|max-content)/);
    const main = topLevelBlocks("\\.cockpit__main").join("\n");
    expect(main).not.toMatch(/align-items:\s*center/);
  });

  it("caps the column at the plan token, centred", () => {
    expect(queue).toMatch(/max-width:\s*var\(--plan-max-width\)/);
    expect(queue).toMatch(/margin-inline:\s*auto/);
  });

  it("raises the token to 1680px on wide screens", () => {
    const wide = [...appCss.matchAll(/--plan-max-width:\s*(\d+)px/g)].map((m) =>
      parseInt(m[1], 10),
    );
    expect(Math.max(...wide)).toBe(1680);
    // The last declaration (widest viewport band) is the wide cap.
    expect(wide[wide.length - 1]).toBe(1680);
  });

  it("keeps the 380px capacity column and the 1100px stack breakpoint", () => {
    expect(appCss).toMatch(
      /\.plan-grid\s*\{[^}]*grid-template-columns:\s*minmax\(0,\s*1fr\)\s*380px/,
    );
    expect(appCss).toMatch(
      /@media screen and \(max-width:\s*1100px\)\s*\{\s*\.plan-grid\s*\{[^}]*minmax\(0,\s*1fr\)\s*;/,
    );
  });
});
