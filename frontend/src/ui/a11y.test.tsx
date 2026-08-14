/* a11y.test — automated accessibility assertions (T9 gate).
   Sweeps every fixture scenario's full App render for unnamed controls,
   unlabeled form fields, and positive tabindex; exercises dialog focus
   management (initial focus, Tab wrap, Escape, focus restore); and verifies
   WCAG AA contrast for the token text/surface pairs in both themes plus the
   reduced-motion guard — computed from tokens.css directly since jsdom
   does no real style resolution.

   IMP-07 pruned the duplicate MobileAgenda surface and the read-only
   PlacementList review (contract items 2/21), so their keyboard/landmark
   tests are gone with them. */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { act, cleanup, fireEvent } from "@testing-library/preact";

afterEach(cleanup);

import { App } from "./App";
import { Queue } from "./Queue";
import { Rail } from "./Rail";
import { makeHarness } from "./test-harness";
import { SCENARIO_NAMES } from "../fixtures/scenarios";

// -- audit helpers -----------------------------------------------------------

function accessibleName(el: HTMLElement): string {
  return (
    el.getAttribute("aria-label") ??
    el.getAttribute("aria-labelledby") ??
    el.textContent ??
    ""
  ).trim();
}

function auditContainer(container: HTMLElement): string[] {
  const problems: string[] = [];
  for (const btn of Array.from(container.querySelectorAll("button"))) {
    if (accessibleName(btn as HTMLElement) === "")
      problems.push(`button without accessible name: ${btn.outerHTML.slice(0, 80)}`);
  }
  for (const field of Array.from(
    container.querySelectorAll("input, textarea, select"),
  )) {
    const el = field as HTMLElement;
    const id = el.getAttribute("id");
    const labeled =
      el.getAttribute("aria-label") ||
      el.getAttribute("aria-labelledby") ||
      (id && container.querySelector(`label[for="${id}"]`));
    if (!labeled)
      problems.push(`form field without label: ${el.outerHTML.slice(0, 80)}`);
  }
  for (const el of Array.from(container.querySelectorAll("[tabindex]"))) {
    if (Number(el.getAttribute("tabindex")) > 0)
      problems.push(`positive tabindex: ${(el as HTMLElement).outerHTML.slice(0, 80)}`);
  }
  for (const dialog of Array.from(container.querySelectorAll('[role="dialog"]'))) {
    if (dialog.getAttribute("aria-modal") !== "true")
      problems.push("dialog without aria-modal");
    if (accessibleName(dialog as HTMLElement) === "")
      problems.push("dialog without accessible name");
  }
  return problems;
}

// -- full-app sweep across every scenario ------------------------------------

describe("control naming and labeling sweep", () => {
  it("the deleted canvas leaves no Edit-day disclosure (T13; NOW/NEXT remains gated to live commit)", () => {
    const { ui, container } = (() => {
      const h = makeHarness("sequenced");
      const r = h.ui(<App />);
      return { ui: r, container: r.container };
    })();
    // Pre-commit the execution surface stays absent (brief problem 5).
    expect(container.querySelector(".execution")).toBeNull();
    expect(ui.queryByRole("button", { name: "Edit day" })).toBeNull();
  });

  for (const name of SCENARIO_NAMES) {
    it(`${name}: all controls named, fields labeled, no positive tabindex`, () => {
      const { ui } = makeHarness(name);
      const { container } = ui(<App />);
      expect(auditContainer(container)).toEqual([]);
    });
  }

  it("open dialogs (setup, approval, block editor) pass the same audit", () => {
    const { ui, store } = makeHarness("sequenced");
    const { container } = ui(<App />);
    act(() => {
      store.dispatch({
        type: "UI",
        patch: { setupOpen: true, editorItem: "Magic Mirror" },
      });
    });
    expect(container.querySelectorAll('[role="dialog"]').length).toBe(2);
    expect(auditContainer(container)).toEqual([]);
  });
});

// -- dialog focus management -------------------------------------------------

describe("dialog focus management", () => {
  it("block editor takes focus on open and restores it on close", () => {
    const { ui, store } = makeHarness("sequenced");
    const { container } = ui(<App />);
    const opener = container.querySelector(
      'button[aria-label="Open day setup"]',
    ) as HTMLElement;
    opener.focus();
    act(() => {
      store.dispatch({ type: "UI", patch: { editorItem: "Magic Mirror" } });
    });
    const dialog = container.querySelector('[role="dialog"]') as HTMLElement;
    expect(dialog).toBeTruthy();
    expect(dialog.contains(document.activeElement)).toBe(true);

    fireEvent.keyDown(dialog, { key: "Escape" });
    expect(store.getState().ui.editorItem).toBeNull();
    expect(document.activeElement).toBe(opener);
  });

  it("Tab wraps forward and Shift-Tab wraps backward inside the dialog", () => {
    const { ui, store } = makeHarness("sequenced");
    const { container } = ui(<App />);
    act(() => {
      store.dispatch({ type: "UI", patch: { editorItem: "Magic Mirror" } });
    });
    const dialog = container.querySelector('[role="dialog"]') as HTMLElement;
    const focusables = Array.from(
      dialog.querySelectorAll<HTMLElement>(
        'button:not([disabled]), input:not([disabled])',
      ),
    );
    const first = focusables[0];
    const last = focusables[focusables.length - 1];

    last.focus();
    fireEvent.keyDown(dialog, { key: "Tab" });
    expect(document.activeElement).toBe(first);

    first.focus();
    fireEvent.keyDown(dialog, { key: "Tab", shiftKey: true });
    expect(document.activeElement).toBe(last);
  });

  it("setup and approval drawers focus their first control on open", () => {
    const setup = makeHarness("fresh");
    const r1 = setup.ui(<App />);
    act(() => {
      setup.store.dispatch({ type: "UI", patch: { setupOpen: true } });
    });
    const d1 = r1.container.querySelector('[role="dialog"]') as HTMLElement;
    expect(d1.contains(document.activeElement)).toBe(true);
    r1.unmount();

    const approval = makeHarness("commit-preview");
    const r2 = approval.ui(<App />);
    const d2 = r2.container.querySelector('[role="dialog"]') as HTMLElement;
    expect(d2.contains(document.activeElement)).toBe(true);
    fireEvent.keyDown(d2, { key: "Escape" });
    expect(approval.store.getState().ui.approvalOpen).toBe(false);
  });
});

// -- token contrast (WCAG AA) and reduced motion -----------------------------

// jsdom does no real style resolution and vitest stubs CSS imports (even
// `?raw` resolves empty), so read tokens.css off disk for the contrast math.
// cwd is the frontend package root; import.meta.url is a jsdom http: URL here.
const tokensCss = readFileSync(resolve(process.cwd(), "src/tokens.css"), "utf8");

function blockVars(block: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const m of block.matchAll(/(--[\w-]+):\s*(#[0-9a-fA-F]{6})/g)) {
    out[m[1]] = m[2];
  }
  return out;
}

function themeVars(selector: RegExp): Record<string, string> {
  const m = tokensCss.match(selector);
  if (!m) throw new Error(`selector not found in tokens.css: ${selector}`);
  return blockVars(m[1]);
}

function luminance(hex: string): number {
  const c = [1, 3, 5].map((i) => {
    const v = parseInt(hex.slice(i, i + 2), 16) / 255;
    return v <= 0.04045 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
  });
  return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
}

function ratio(fg: string, bg: string): number {
  const [l1, l2] = [luminance(fg), luminance(bg)].sort((a, b) => b - a);
  return (l1 + 0.05) / (l2 + 0.05);
}

// Body-text pairs must clear AA normal text (4.5:1).
const TEXT_PAIRS: Array<[string, string]> = [
  ["--t-text", "--t-surface"],
  ["--t-text", "--t-surface-2"],
  ["--t-muted", "--t-surface"],
  ["--t-muted", "--t-surface-2"],
  ["--t-accent-text", "--t-accent-subtle"],
  ["--t-badge-neutral-text", "--t-badge-neutral-bg"],
];

describe("token contrast (WCAG AA)", () => {
  const themes: Array<[string, Record<string, string>]> = [
    ["light", themeVars(/:root\s*\{([^}]+)\}/)],
    ["dark", themeVars(/:root\[data-theme="dark"\]\s*\{([^}]+)\}/)],
  ];
  for (const [themeName, vars] of themes) {
    it(`${themeName}: body-text pairs reach 4.5:1`, () => {
      for (const [fg, bg] of TEXT_PAIRS) {
        const fgHex = vars[fg] ?? themes[0][1][fg];
        const bgHex = vars[bg] ?? themes[0][1][bg];
        expect(fgHex, `${fg} missing`).toBeTruthy();
        expect(bgHex, `${bg} missing`).toBeTruthy();
        const r = ratio(fgHex!, bgHex!);
        expect(r, `${themeName} ${fg} on ${bg} = ${r.toFixed(2)}`).toBeGreaterThanOrEqual(4.5);
      }
    });
  }

  it("focus ring token clears 3:1 against both surfaces (non-text)", () => {
    const light = themes[0][1];
    const dark = themes[1][1];
    expect(ratio(light["--t-accent"], light["--t-surface"])).toBeGreaterThanOrEqual(3);
    expect(ratio(dark["--t-accent"], dark["--t-surface"])).toBeGreaterThanOrEqual(3);
  });
});

describe("reduced motion", () => {
  it("tokens.css zeroes animation and transition under prefers-reduced-motion", () => {
    const m = tokensCss.match(
      /@media\s*\(prefers-reduced-motion:\s*reduce\)\s*\{([\s\S]+?)\n\}/,
    );
    expect(m).toBeTruthy();
    expect(m![1]).toMatch(/animation-duration:\s*0\.01ms\s*!important/);
    expect(m![1]).toMatch(/transition-duration:\s*0\.01ms\s*!important/);
  });

  it("a global :focus-visible ring exists", () => {
    expect(tokensCss).toMatch(/:focus-visible\s*\{[^}]*outline:\s*2px solid/);
  });
});

// -- allocator table (T7) ----------------------------------------------------

describe("allocator table a11y (T7)", () => {
  it("every duration slider is a native range with a name and a spoken value", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    const sliders = Array.from(
      container.querySelectorAll('input[type="range"]'),
    ) as HTMLInputElement[];
    expect(sliders.length).toBeGreaterThan(0);
    for (const slider of sliders) {
      // Native range => arrow/Home/End keyboard operation for free.
      expect(accessibleName(slider)).not.toBe("");
      // A raw block count read aloud ("3") is meaningless; aria-valuetext
      // makes the screen reader say "1hr 30min" instead.
      expect(slider.getAttribute("aria-valuetext")).toBeTruthy();
      // Half-block notches (2026-07-27): the track runs 30min → 15min → All
      // day, matching the ± steppers rather than fighting them.
      expect(slider.getAttribute("step")).toBe("0.5");
      expect(slider.getAttribute("min")).toBe("0");
    }
  });

  it("every staging verb button names its target item", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    const verbs = Array.from(
      container.querySelectorAll("button.alloc-verb"),
    ) as HTMLElement[];
    expect(verbs.length).toBeGreaterThan(0);
    for (const verb of verbs) {
      // "Delete" alone is ambiguous in a dense table of near-identical rows.
      expect(accessibleName(verb).length).toBeGreaterThan("Delete".length);
    }
  });

  it("the live budget delta is announced, not silently repainted (T12e: rail readout)", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Rail />);
    const delta = container.querySelector(".rail-budget__delta") as HTMLElement;
    expect(delta).toBeTruthy();
    expect(delta.getAttribute("role")).toBe("status");
  });

  it("the allocator table introduces no unnamed controls", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    expect(auditContainer(container)).toEqual([]);
  });
});

// -- allocation pie (T8) -----------------------------------------------------

describe("allocation pie a11y (T8)", () => {
  it("the chart carries a text alternative, not bare geometry", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Rail />);
    const svg = container.querySelector("svg.pie__svg") as SVGElement;
    expect(svg).toBeTruthy();
    expect(svg.getAttribute("role")).toBe("img");
    // A pie is meaningless read as paths — the label must state the numbers.
    expect(svg.getAttribute("aria-label")).toMatch(/blk \(\d+%\)/);
  });

  it("each wedge names itself on hover without becoming a focus stop", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Rail />);
    const paths = Array.from(
      container.querySelectorAll("path.pie__slice"),
    ) as SVGPathElement[];
    expect(paths.length).toBeGreaterThan(0);
    for (const p of paths) {
      expect(p.querySelector("title")?.textContent).toMatch(/ — \d+(\.\d+)? blk$/);
      expect(p.getAttribute("tabindex")).toBeNull();
    }
  });

  it("the legend repeats the numbers in text for anyone who can't read colour", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Rail />);
    const values = Array.from(
      container.querySelectorAll(".pie__legend-value"),
    ) as HTMLElement[];
    expect(values.length).toBeGreaterThan(0);
    for (const v of values) expect(v.textContent).toMatch(/blk$/);
  });

  it("the rail introduces no unnamed controls", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Rail />);
    expect(auditContainer(container)).toEqual([]);
  });
});

// -- allocation pie (T8) -----------------------------------------------------
