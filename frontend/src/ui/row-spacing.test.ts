/* row-spacing.test.ts — CSS regression for the placement-row density (plan
   tdtb-row-spacing-20260817; updated for Planning 2a, 2026-10-14).

   The main allocator rows (.qrow) are 2a's compact card: 6px vertical / 12px
   horizontal padding on a five-column grid, with a 4px stack gap between rows.
   The update must not erode the shared control floors that apply outside the
   compact row (the base verb height, the base duration track and its 44px hit
   area), while the row's own controls carry deliberate 2a sizes. jsdom does no
   real style resolution, so these guards read app.css off disk the same way
   the responsive/a11y suites do. */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const appCss = readFileSync(resolve(process.cwd(), "src/app.css"), "utf8");

/** Body of the LAST `.qrow` rule that owns a `padding` declaration. The
    appended Planning 2a layer splits the row across two `.qrow` blocks, so the
    plain last match is not the padding rule. */
function rowPadRule(): string {
  const re = /(?:^|\n)\.qrow\s*\{([^}]*)\}/g;
  const blocks = Array.from(appCss.matchAll(re), (m) => m[1]);
  const row = blocks.filter((b) => /padding:/.test(b)).pop();
  if (!row) throw new Error("standalone .qrow padding rule not found in app.css");
  return row;
}

/** Body of the LAST `selector { … }` rule anywhere in the file. */
function lastRuleBody(selector: string): string {
  const re = new RegExp(`(?:^|\\n)\\s*${selector}\\s*\\{([^}]*)\\}`, "g");
  const hits = Array.from(appCss.matchAll(re), (m) => m[1]);
  if (hits.length === 0) throw new Error(`selector not found in app.css: ${selector}`);
  return hits[hits.length - 1];
}

/** Body of the first block matching a dotted selector anywhere in the file
    (used for the shared single-selector control rules). */
function firstBlock(selector: string): string {
  const m = appCss.match(new RegExp(`\\.${selector}\\s*\\{([^}]*)\\}`));
  if (!m) throw new Error(`selector not found in app.css: .${selector}`);
  return m[1];
}

describe("placement rows keep the compact 2a density", () => {
  const row = rowPadRule();

  it("the Planning 2a .qrow rule owns the padding and the five-column grid", () => {
    expect(row).toMatch(/padding:\s*6px 12px/);
    expect(row).toMatch(/grid-template-columns:/);
  });

  it("vertical padding separates adjacent rows (≥6px each side)", () => {
    const pad = row.match(/padding:\s*([^;]+);/)?.[1] ?? "";
    const parts = pad.trim().split(/\s+/);
    const vertical = parseFloat(parts[0] ?? "0");
    expect(vertical).toBeGreaterThanOrEqual(6);
  });

  it("the row keeps the compact 12px horizontal padding", () => {
    const pad = row.match(/padding:\s*([^;]+);/)?.[1] ?? "";
    const parts = pad.trim().split(/\s+/);
    expect(parts[1]).toBe("12px");
  });
});

describe("row spacing never erodes shared control touch targets", () => {
  it("the shared direct-verb base keeps the 44px floor", () => {
    expect(firstBlock("alloc-verb")).toMatch(/min-height:\s*44px/);
  });

  it("the shared row icon control base keeps the 44px floor", () => {
    expect(firstBlock("qrow__controls .iconbtn")).toMatch(/min-height:\s*44px/);
  });

  it("the shared duration track keeps its 44px hit area", () => {
    expect(firstBlock("alloc-track")).toMatch(/height:\s*44px/);
    expect(firstBlock("alloc-track")).toMatch(/min-width:\s*140px/);
    expect(firstBlock("alloc-track__input")).toMatch(/height:\s*44px/);
  });

  it("uses a compact visual thumb on the full-size keyboard range", () => {
    const thumb = appCss.match(
      /\.alloc-track__input::-webkit-slider-thumb\s*\{([^}]*)\}/,
    )?.[1] ?? "";
    expect(thumb).toMatch(/width:\s*14px/);
    expect(thumb).toMatch(/height:\s*14px/);
  });

  it("scopes the 2a row to 34px icon buttons and a 22px track", () => {
    // The compact desktop row deliberately uses smaller hit areas; the shared
    // 44px floors above still govern the wider and coarse layouts.
    const grouped = appCss.match(
      /\.qrow__actions \.planbtn:not\(\.row-more__item\),[\s\S]*?\{([^}]*)\}/,
    )?.[1] ?? "";
    expect(grouped).toMatch(/width:\s*34px/);
    expect(grouped).toMatch(/height:\s*34px/);
    expect(lastRuleBody("\\.qrow__time \\.alloc-track")).toMatch(/height:\s*22px/);
  });
});

describe("the retired inline Fixed-row permission leaves no row CSS behind", () => {
  it("re-stretches the meta line inside the 2a flex body", () => {
    // The retired >=1500px six-cell layer set `align-self: start` on
    // .qrow__source. In the 2a column-flex body that shrink-wraps the meta
    // line to max-content, so a Fixed row's checkbox and label painted over
    // the slider. The 2a rule must reset the cross-axis alignment so
    // `overflow: hidden` clips at the real column edge.
    expect(lastRuleBody("\\.qrow__source")).toMatch(/align-self:\s*auto/);
  });

  it("removes the inline control from the row entirely (More menu owns it)", () => {
    // Planning 2a cleanup (2026-10-14): "Allow move" moved into the More menu
    // as a menuitemcheckbox, so no row-level rule for it survives.
    expect(appCss).not.toMatch(/\.qrow__time-adjustment/);
  });

  it("clips the provenance chip, folded meta detail, and all-day marker", () => {
    const clipped = appCss.match(
      /\.qrow__dur-label,\s*\n\s*\.qrow__meta\.sr-only,\s*\n\s*\.qrow__allday\.sr-only\s*\{([^}]*)\}/,
    )?.[1] ?? "";
    expect(clipped).toMatch(/position:\s*absolute/);
    expect(clipped).toMatch(/width:\s*1px/);
    expect(clipped).toMatch(/clip:\s*rect\(0 0 0 0\)/);
  });

  it("frees the duration column: a 140px track and no stepper rule", () => {
    const track = lastRuleBody("\\.qrow__time \\.alloc-track");
    expect(track).toMatch(/width:\s*140px/);
    expect(track).toMatch(/flex:\s*none/);
    expect(appCss).not.toMatch(/\.qrow__time\s+\.alloc-step/);
  });

  it("stacks the readout 4px directly over the slider line", () => {
    // The <=1100px layer re-places .qrow__time without touching its axis, so
    // filter to the rule that owns the desktop placement and axis.
    const blocks = Array.from(
      appCss.matchAll(/(?:^|\n)\s*\.qrow__time\s*\{([^}]*)\}/g),
      (m) => m[1],
    );
    const desktop = blocks.filter((b) => /grid-column:\s*3/.test(b)).pop() ?? "";
    expect(desktop).toMatch(/gap:\s*4px/);
    expect(lastRuleBody("\\.qrow__time-readout")).toMatch(/line-height:\s*16px/);
  });
});

describe("queue controls stay inside the viewport", () => {
  it("lets desktop action columns shrink while row controls wrap", () => {
    const grid = appCss.match(/\.queue__cols,\s*\.qrow\s*\{([^}]*)\}/)?.[1] ?? "";
    expect(grid).toMatch(/minmax\(0,\s*1fr\)/);
    expect(grid).not.toMatch(/minmax\(348px,\s*auto\)/);
    expect(firstBlock("qrow__actions")).toMatch(/min-width:\s*0/);
    expect(firstBlock("qrow__actions")).toMatch(/flex-wrap:\s*wrap/);
  });

  it("switches to full-width two-column rows before the narrow phone layout", () => {
    // Phone width still moves the whole cluster onto its own full-width line.
    expect(appCss).toMatch(
      /@media\s*\(max-width:\s*767px\)[\s\S]*\.qrow__actions\s*\{[^}]*grid-column:\s*1\s*\/\s*-1/,
    );
    expect(appCss).toMatch(
      /@media\s*\(max-width:\s*767px\)[\s\S]*\.qrow__time\s*\{[^}]*grid-column:\s*2/,
    );
  });

  it("re-templates the row to the 2a two-line grid at <=1100px", () => {
    // Planning 2a: status spans two rows; body + priority on line 1; duration
    // and actions on line 2. See row-compact-1100.test.ts for the full contract.
    expect(appCss).toMatch(
      /@media screen and \(max-width: 1100px\)[\s\S]*?\.qrow\s*\{[\s\S]*?grid-template-columns:\s*118px minmax\(0,\s*1fr\) auto/,
    );
    expect(appCss).toMatch(
      /@media screen and \(max-width: 1100px\)[\s\S]*?\.qrow__time\s*\{[^}]*grid-column:\s*2/,
    );
  });
});

describe("the Complete row verb keeps its filled green contract", () => {
  /** The last `.qrow__actions …alloc-verb--done…` rule (the Planning 2a layer). */
  function completeRule(): { selector: string; body: string } {
    const re = /(?:^|\n)([^\n{}]*\.qrow__actions[^\n{}]*\.alloc-verb--done[^\n{}]*)\s*\{([^}]*)\}/g;
    const hits = Array.from(appCss.matchAll(re), (m) => ({ selector: m[1].trim(), body: m[2] }))
      // The base fill rule only: state variants are asserted separately.
      .filter(({ selector }) => !selector.includes(":hover"));
    if (hits.length === 0) throw new Error("qrow Complete verb rule not found in app.css");
    return hits[hits.length - 1];
  }

  /** Numeric specificity weight: ids > classes/pseudo-classes > types. */
  function weight(selector: string): number {
    const ids = (selector.match(/#[\w-]+/g) ?? []).length;
    const classes = (selector.match(/\.[\w-]+/g) ?? []).length;
    const pseudo = (selector.match(/:(?!:)[\w-]+/g) ?? []).length;
    const types = (selector.match(/(?:^|[\s>+~])([a-zA-Z][\w-]*)/g) ?? []).length;
    return ids * 100 + (classes + pseudo) * 10 + types;
  }

  it("the Complete rule outranks the shared direct-verb base and fills green", () => {
    const complete = completeRule();
    expect(weight(complete.selector)).toBeGreaterThanOrEqual(
      weight(".qrow__actions .alloc-verb:not(.row-more__item)"),
    );
    expect(complete.body).toMatch(/background:\s*#1f9d6b/);
    expect(complete.body).toMatch(/border-color:\s*#1f9d6b/);
    expect(complete.body).toMatch(/color:\s*#fff/);
  });

  it("hover deepens the Complete fill instead of falling back to the outline", () => {
    const hover = appCss.match(
      /\.qrow__actions \.alloc-verb--done:not\(\.row-more__item\):hover\s*\{([^}]*)\}/,
    )?.[1] ?? "";
    expect(hover).toMatch(/background:\s*#1a8a5d/);
  });
});
