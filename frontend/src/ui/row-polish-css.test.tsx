/* row-polish-css.test.tsx — CSS contract for the row detail polish
   (2026-10-28). Row details used to truncate ("Automatic st…") because the
   actions column sits next to the duration slider. The meta line now wraps,
   tags wrap, the title keeps two lines, and the mid-width band gives the name
   column room by narrowing the slider cell. jsdom does no layout, so the
   cascade is read off disk like row-grid-alignment.test.tsx. */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const appCss = readFileSync(resolve(process.cwd(), "src/app.css"), "utf8");
const duxCss = readFileSync(resolve(process.cwd(), "src/ui/duration-ux.css"), "utf8");

function block(selector: string): string {
  const re = new RegExp(`(?:^|\\n)${selector}\\s*\\{([^}]*)\\}`);
  return re.exec(appCss)?.[1] ?? "";
}

describe("row meta and title wrapping", () => {
  it("wraps the meta line instead of ellipsizing it", () => {
    const meta = block("\\.qrow\\.trow \\.qrow__source");
    expect(meta).toMatch(/display:\s*flex/);
    expect(meta).toMatch(/flex-wrap:\s*wrap/);
    expect(meta).toMatch(/white-space:\s*normal/);
    expect(meta).toMatch(/overflow:\s*visible/);
    expect(meta).toMatch(/text-overflow:\s*clip/);
  });

  it("lets the state text and the tags wrap inside the meta line", () => {
    const state = block("\\.qrow\\.trow \\.qrow__source\\s*>\\s*\\.trow__state");
    expect(state).toMatch(/overflow-wrap:\s*anywhere/);
    expect(state).not.toMatch(/ellipsis|overflow:\s*hidden/);
    const tags = block("\\.qrow\\.trow \\.qrow__source\\s*>\\s*\\.qrow__tags");
    expect(tags).toMatch(/flex-wrap:\s*wrap/);
    expect(tags).toMatch(/min-width:\s*0/);
  });

  it("caps the title at two lines", () => {
    const name = block("\\.qrow\\.trow \\.qrow__name");
    expect(name).toMatch(/-webkit-line-clamp:\s*2/);
    expect(name).toMatch(/-webkit-box-orient:\s*vertical/);
    expect(name).toMatch(/overflow:\s*hidden/);
    expect(name).toMatch(/white-space:\s*normal/);
  });
});

describe("mid-width slider cell", () => {
  it("narrows the duration cell to 112px between 1101px and 1500px", () => {
    const m = /@media \(1100px < width <= 1500px\)\s*\{\s*\.qrow\.trow--dslider\s*\{([^}]*)\}/.exec(
      appCss,
    );
    expect(m?.[1]).toMatch(/--trow-dur-w:\s*112px/);
  });
});

describe("retired staged and chip selectors", () => {
  it("no longer styles the staged state or the one-tap duration chips", () => {
    expect(appCss).not.toMatch(/trow__state--staged/);
    expect(duxCss).not.toMatch(/dux-chip/);
  });
});
