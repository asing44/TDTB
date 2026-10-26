/* row-duration-slider.test.tsx — the collapsed plan row's duration cell is the
   inline slider again (readout above [−] range [+]), restored from the earlier
   plan-row design (d30a059 readout above the slider, 6c0807e steppers,
   f5e506a/1f8a488 one-line sizing). It stages on the row draft exactly like
   the expanded select; nothing commits until Save. */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent } from "@testing-library/preact";

afterEach(cleanup);

import { Queue } from "./Queue";
import { makeHarness } from "./test-harness";
import { effectiveBlocks } from "../store/store";

const appCss = readFileSync(resolve(process.cwd(), "src/app.css"), "utf8");
const sliderCss = readFileSync(
  resolve(process.cwd(), "src/ui/duration-slider.css"),
  "utf8",
);

function rowFor(container: Element, name: string): HTMLElement {
  const row = Array.from(container.querySelectorAll<HTMLElement>(".qrow")).find(
    (r) => r.querySelector(".qrow__name")?.textContent === name,
  );
  if (!row) throw new Error(`no row named ${name}`);
  return row;
}

const cell = (row: Element) => row.querySelector<HTMLElement>(".trow__dur--slider")!;
const range = (row: Element) =>
  row.querySelector<HTMLInputElement>('input[aria-label="Duration slider"]')!;
const label = (row: Element) => row.querySelector(".dslider__label")?.textContent;
const less = (row: Element) =>
  row.querySelector<HTMLButtonElement>('button[aria-label^="15 minutes less"]')!;
const more = (row: Element) =>
  row.querySelector<HTMLButtonElement>('button[aria-label^="15 minutes more"]')!;
const preset = (row: Element) =>
  row.querySelector<HTMLSelectElement>('select[aria-label="Duration"]')!;

describe("collapsed row duration cell", () => {
  it("renders readout above a [−] range [+] strip in the duration cell", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    const row = rowFor(container, "Press");
    const dslider = cell(row).querySelector(".dslider")!;
    const kids = Array.from(dslider.children).map((k) => k.className.split(" ")[0]);
    expect(kids.slice(0, 2)).toEqual(["dslider__label", "dslider__ctl"]);
    const ctl = dslider.querySelector(".dslider__ctl")!;
    expect(Array.from(ctl.children).map((k) => k.tagName)).toEqual([
      "BUTTON",
      "INPUT",
      "BUTTON",
    ]);
    expect(label(row)).toBe("1hr 30min");
    expect([range(row).min, range(row).max, range(row).step]).toEqual(["5", "240", "5"]);
  });

  it("stepper + stages +15 minutes and − stages −15, without committing", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    fireEvent.click(more(rowFor(container, "Press")));
    expect(h.store.getState().drafts["Press"].blocks).toBe(3.5);
    expect(h.store.getState().overrides["Press"]).toBeUndefined();
    expect(label(rowFor(container, "Press"))).toBe("1hr 45min");
    fireEvent.click(less(rowFor(container, "Press")));
    fireEvent.click(less(rowFor(container, "Press")));
    expect(h.store.getState().drafts["Press"].blocks).toBe(2.5);
    expect(label(rowFor(container, "Press"))).toBe("1hr 15min");
  });

  it("clamps the steppers at the range ends", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    fireEvent.input(range(rowFor(container, "Press")), { target: { value: "240" } });
    let row = rowFor(container, "Press");
    expect(more(row).disabled).toBe(true);
    expect(less(row).disabled).toBe(false);
    fireEvent.input(range(row), { target: { value: "5" } });
    row = rowFor(container, "Press");
    expect(less(row).disabled).toBe(true);
    expect(more(row).disabled).toBe(false);
    fireEvent.input(range(row), { target: { value: "10" } });
    fireEvent.click(less(rowFor(container, "Press")));
    expect(h.store.getState().drafts["Press"].blocks).toBeCloseTo(5 / 30, 6);
  });

  it("slider stages the same blocks as the expanded select, live readout", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    fireEvent.input(range(rowFor(container, "Press")), { target: { value: "120" } });
    const viaSlider = h.store.getState().drafts["Press"].blocks;
    expect(label(rowFor(container, "Press"))).toBe("2hr");
    expect(rowFor(container, "Press").querySelector(".trow__state--staged")).not.toBeNull();

    fireEvent.click(
      rowFor(container, "Press").querySelector('button[aria-label="Expand"]')!,
    );
    fireEvent.change(preset(rowFor(container, "Press")), { target: { value: "4" } });
    expect(h.store.getState().drafts["Press"].blocks).toBe(viaSlider);
    expect(viaSlider).toBe(4);
  });

  it("source-owned duration stays today-only: Save writes only the session override", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    fireEvent.input(range(rowFor(container, "Press")), { target: { value: "60" } });
    const item = h.store.getState().inputs!.assigned.find((i) => i.id === "Press")!;
    expect(item.blocks).toBe(3);
    fireEvent.click(
      rowFor(container, "Press").querySelector('button[aria-label="Expand"]')!,
    );
    const save = Array.from(
      rowFor(container, "Press").querySelectorAll<HTMLButtonElement>("button"),
    ).find((b) => b.textContent?.trim() === "Save")!;
    fireEvent.click(save);
    expect(h.store.getState().overrides["Press"]?.blocks).toBe(2);
    expect(effectiveBlocks(h.store.getState(), "Press")).toBe(2);
    expect(
      h.store.getState().inputs!.assigned.find((i) => i.id === "Press")!.blocks,
    ).toBe(3);
  });

  it("is inert for an all-day row and shows All day", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    const row = rowFor(container, "Charge GoPro");
    expect(label(row)).toBe("All day");
    expect(range(row).disabled).toBe(true);
    expect(less(row).disabled).toBe(true);
    expect(more(row).disabled).toBe(true);
    expect(cell(row).querySelector(".dslider")?.getAttribute("title")).toBe(
      "All day — no capacity",
    );
  });

  it("is disabled while the runtime is busy", () => {
    const h = makeHarness("ready");
    h.store.dispatch({ type: "RUNTIME_START" } as never);
    const { container } = h.ui(<Queue />);
    const row = rowFor(container, "Press");
    expect(range(row).disabled).toBe(true);
    expect(less(row).disabled).toBe(true);
    expect(more(row).disabled).toBe(true);
  });

  it("keeps a single slider per row when the row is expanded", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    fireEvent.click(
      rowFor(container, "Press").querySelector('button[aria-label="Expand"]')!,
    );
    const row = rowFor(container, "Press");
    expect(row.querySelectorAll('input[aria-label="Duration slider"]')).toHaveLength(1);
    expect(cell(row).contains(range(row))).toBe(true);
    expect(preset(row)).not.toBeNull();
  });
});

/** All `@media (width <= <px>) { … }` block bodies for a width, joined
    (app.css repeats the same width for unrelated surfaces). */
function mediaBlocks(css: string, px: number): string {
  const head = `@media (width <= ${px}px) {`;
  const out: string[] = [];
  for (let at = css.indexOf(head); at >= 0; at = css.indexOf(head, at + 1)) {
    let depth = 0;
    for (let i = css.indexOf("{", at); i < css.length; i++) {
      if (css[i] === "{") depth++;
      if (css[i] === "}" && --depth === 0) {
        out.push(css.slice(at, i + 1));
        break;
      }
    }
  }
  return out.join("\n");
}

describe("adaptive layout classes", () => {
  it("the duration column is a flexible clamp, not a fixed blank box", () => {
    expect(appCss).toMatch(
      /\.qrow\.trow--dslider\s*\{\s*--trow-dur-w:\s*clamp\(112px,\s*14vw,\s*168px\)/,
    );
    expect(appCss).toMatch(
      /\.qrow\.trow--dslider\s*\{[^}]*grid-template-columns:\s*16px minmax\(0,\s*1fr\) var\(--trow-dur-w\) 152px/,
    );
  });

  it.each([
    [1100, /clamp\(88px,\s*11vw,\s*104px\)/],
    [900, /clamp\(72px,\s*9vw,\s*84px\)/],
    [760, /--trow-dur-w:\s*56px/],
    [640, /--trow-dur-w:\s*120px/],
  ])("narrow class <=%ipx narrows the cell", (px, expected) => {
    expect(mediaBlocks(appCss, px as number)).toMatch(expected as RegExp);
  });

  it("the actions column never shrinks or wraps at any width", () => {
    expect(appCss).toMatch(/\.trow__acts\s*\{[^}]*grid-template-columns:\s*44px 32px 32px 32px/);
    expect(appCss).toMatch(
      /\.qrow\.trow--dslider > \.trow__acts\s*\{[^}]*min-width:\s*152px/,
    );
  });

  it("the cell is a size container that drops steppers, then the track", () => {
    expect(sliderCss).toMatch(/\.trow__dur--slider\s*\{[^}]*container:\s*dur \/ inline-size/);
    expect(sliderCss).toMatch(
      /@container dur \(max-width: 111px\)\s*\{[^@]*\.dslider__step\s*\{\s*display:\s*none/,
    );
    expect(sliderCss).toMatch(
      /@container dur \(max-width: 63px\)\s*\{[^@]*\.dslider__ctl\s*\{\s*display:\s*none/,
    );
  });

  it("the readout holds a fixed minimum width above the track (steady thumb)", () => {
    expect(sliderCss).toMatch(/\.dslider__label\s*\{[^}]*min-width:\s*6ch/);
    expect(sliderCss).toMatch(/\.dslider__label\s*\{[^}]*white-space:\s*nowrap/);
  });
});
