/* row-grid-alignment.test.tsx — CSS contract for the Plan task row grid
   (2026-10-19). After the Assigned switch landed the name column was squeezed
   by an auto-sized action cluster and rows did not line up across sources.
   The row is now fixed slots (select | name+meta 1fr | duration | actions),
   the actions are a fixed grid whose Skip today slot is reserved, the meta
   line keeps its separators, and the Unassigned section shares the column and
   the slots. jsdom does no layout, so the cascade is read off disk like
   plan-width-stability.test.tsx. */

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

const px = (s: string): number[] =>
  [...s.matchAll(/(\d+)px/g)].map((m) => parseInt(m[1], 10));

describe("Plan task rows use fixed slots around a flexible name column", () => {
  const row = topLevelBlocks("\\.qrow\\.trow").join("\n");
  const cols = /grid-template-columns:\s*([^;]+);/.exec(row)?.[1] ?? "";

  it("gives the name+meta column the slack and fixes every other slot", () => {
    expect(cols).toMatch(/^16px\s+minmax\(0,\s*1fr\)\s+66px\s+278px$/);
  });

  it("makes the actions a fixed grid sized to the row's last slot", () => {
    const acts = topLevelBlocks("\\.trow__acts").join("\n");
    expect(acts).toMatch(/display:\s*grid/);
    const slots = /grid-template-columns:\s*([^;]+);/.exec(acts)?.[1] ?? "";
    const widths = px(slots);
    expect(widths).toHaveLength(4);
    const gap = parseInt(/gap:\s*(\d+)px/.exec(acts)?.[1] ?? "0", 10);
    const total = widths.reduce((a, b) => a + b, 0) + gap * (widths.length - 1);
    expect(`${total}px`).toBe(cols.trim().split(/\s+/).pop());
  });

  it("stretches the buttons to their slot so Skip today absent still aligns", () => {
    expect(appCss).toMatch(/\.trow__acts\s*>\s*\.trow__btn\s*\{[^}]*width:\s*100%/);
  });

  it("keeps the meta separators and truncates only the trailing state", () => {
    const meta = topLevelBlocks("\\.qrow\\.trow \\.qrow__source").join("\n");
    expect(meta).toMatch(/gap:\s*8px/);
    expect(topLevelBlocks("\\.qrow\\.trow \\.qrow__source\\s*>\\s*\\*").join("\n")).toMatch(
      /flex:\s*none/,
    );
    const state = topLevelBlocks(
      "\\.qrow\\.trow \\.qrow__source\\s*>\\s*\\.trow__state",
    ).join("\n");
    expect(state).toMatch(/text-overflow:\s*ellipsis/);
    expect(state).toMatch(/min-width:\s*0/);
  });
});

describe("the Unassigned section shares the list's slots", () => {
  it("uses the same column template as an assigned row", () => {
    const rowCols = /grid-template-columns:\s*([^;]+);/.exec(
      topLevelBlocks("\\.qrow\\.trow").join("\n"),
    )?.[1];
    const un = /grid-template-columns:\s*([^;]+);/.exec(
      topLevelBlocks("\\.qrow\\.trow--unassigned").join("\n"),
    )?.[1];
    expect(un).toBe(rowCols);
  });

  it("drops the section's top margin when it sits inside the task column", () => {
    expect(appCss).toMatch(
      /\.plan-grid__groups\s*>\s*\.unassigned-today\s*\{[^}]*margin-top:\s*0/,
    );
  });

  it("lets the captioned switch span the action slots from the first one", () => {
    const cap = topLevelBlocks("\\.trow__assign--captioned").join("\n");
    expect(cap).toMatch(/grid-column:\s*1\s*\/\s*-1/);
    expect(cap).toMatch(/justify-self:\s*start/);
  });
});
