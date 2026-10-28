/* row-duration-slider.test.tsx — the collapsed plan row's duration cell is the
   inline slider (readout above [−] range [+]) and it APPLIES AT ONCE: every
   move goes through controller.applyRowEdit, so the committed session changes
   immediately and the row keeps a single-step Undo. There is no staging step
   and no Save.

   FEEDBACK-2026-10-26 (Adam): the track is a quarter-hour grid whose leftmost
   notch is All-day — "slider should be 15min increments, leftmost selection is
   all-day like previous". Row redesign: the All-day notch is open for every row
   except a protected Todoist native time, and a Capacities row's All-day is a
   local today-only state with no write-back. */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent } from "@testing-library/preact";

afterEach(cleanup);

import { Queue } from "./Queue";
import { makeHarness } from "./test-harness";
import { effectiveBlocks } from "../store/store";
import { EXCLUSION_REASONS } from "../model/batchEligibility";
import type { Scenario } from "../fixtures/scenarios";

/** Any fixture row as a protected Todoist native time (the state where the
    All-day notch is closed). */
function withNativeTime(sc: Scenario): void {
  const press = sc.inputs.assigned.find((i) => i.name === "Press");
  if (!press) throw new Error("scenario is missing the Press row");
  press.scheduledStart = "19:00";
}

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
/** The expanded panel's exact-minutes field: the slider's typed sibling. */
const exact = (row: Element) =>
  row.querySelector<HTMLInputElement>('input[aria-label="Exact minutes"]')!;
const cellTitle = (row: Element) =>
  cell(row).querySelector(".dslider")?.getAttribute("title");
/** The duration's provenance chip: "Today" once a local edit has landed. */
const badge = (row: Element) => row.querySelector(".dux-badge")?.textContent;

describe("collapsed row duration cell", () => {
  it("renders readout above a [−] range [+] strip in the duration cell", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    const row = rowFor(container, "Press");
    const dslider = cell(row).querySelector(".dslider")!;
    const kids = Array.from(dslider.children).map((k) => k.className.split(" ")[0]);
    // The readout line carries the provenance chip beside the label, so the
    // two structural children are the top line and the control strip.
    expect(kids.slice(0, 2)).toEqual(["dslider__top", "dslider__ctl"]);
    expect(dslider.querySelector(".dslider__top .dslider__label")).not.toBeNull();
    const ctl = dslider.querySelector(".dslider__ctl")!;
    expect(Array.from(ctl.children).map((k) => k.tagName)).toEqual([
      "BUTTON",
      "INPUT",
      "BUTTON",
    ]);
    expect(label(row)).toBe("1hr 30min");
    // Quarter-hour notches with All-day at the leftmost one.
    expect([range(row).min, range(row).max, range(row).step]).toEqual(["0", "240", "15"]);
  });

  it("stepper + applies +15 minutes and − applies −15, at once", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    fireEvent.click(more(rowFor(container, "Press")));
    // Applied, not staged: the committed session already carries the value.
    expect(effectiveBlocks(h.store.getState(), "Press")).toBe(3.5);
    expect(h.store.getState().overrides["Press"]?.blocks).toBe(3.5);
    expect(label(rowFor(container, "Press"))).toBe("1hr 45min");
    expect(badge(rowFor(container, "Press"))).toContain("Today");
    fireEvent.click(less(rowFor(container, "Press")));
    fireEvent.click(less(rowFor(container, "Press")));
    expect(effectiveBlocks(h.store.getState(), "Press")).toBe(2.5);
    expect(label(rowFor(container, "Press"))).toBe("1hr 15min");
  });

  it("clamps the steppers at the track ends, and − from 15 lands on All-day", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    fireEvent.input(range(rowFor(container, "Press")), { target: { value: "240" } });
    let row = rowFor(container, "Press");
    expect(more(row).disabled).toBe(true);
    expect(less(row).disabled).toBe(false);
    fireEvent.input(range(row), { target: { value: "15" } });
    row = rowFor(container, "Press");
    expect(less(row).disabled).toBe(false);
    fireEvent.click(less(row));
    expect(effectiveBlocks(h.store.getState(), "Press")).toBe(0);
    row = rowFor(container, "Press");
    expect(label(row)).toBe("All day");
    expect(range(row).value).toBe("0");
    expect(less(row).disabled).toBe(true);
    expect(more(row).disabled).toBe(false);
  });

  it("applies the same blocks as the expanded exact-minutes field, live readout", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    fireEvent.input(range(rowFor(container, "Press")), { target: { value: "120" } });
    const viaSlider = effectiveBlocks(h.store.getState(), "Press");
    expect(label(rowFor(container, "Press"))).toBe("2hr");
    expect(badge(rowFor(container, "Press"))).toContain("Today");

    // The expanded field writes through the same immediate path, so typing 2h
    // lands on the identical value.
    fireEvent.click(
      rowFor(container, "Press").querySelector('button[aria-label="Expand"]')!,
    );
    const field = exact(rowFor(container, "Press"));
    fireEvent.input(field, { target: { value: "2h" } });
    fireEvent.keyDown(field, { key: "Enter" });
    expect(effectiveBlocks(h.store.getState(), "Press")).toBe(viaSlider);
    expect(viaSlider).toBe(4);
  });

  it("source-owned duration stays today-only: the edit writes only the session override", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    fireEvent.input(range(rowFor(container, "Press")), { target: { value: "60" } });
    const item = h.store.getState().inputs!.assigned.find((i) => i.id === "Press")!;
    // The today-override landed at once; the source estimate is untouched.
    expect(h.store.getState().overrides["Press"]?.blocks).toBe(2);
    expect(effectiveBlocks(h.store.getState(), "Press")).toBe(2);
    expect(item.blocks).toBe(3);
    expect(h.store.getState().inputs!.assigned.find((i) => i.id === "Press")!.blocks).toBe(
      3,
    );
  });

  it("parks an all-day row's thumb on the leftmost notch, and lets it leave", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    let row = rowFor(container, "Charge GoPro");
    expect(label(row)).toBe("All day");
    expect(range(row).value).toBe("0");
    expect(range(row).disabled).toBe(false);
    expect(less(row).disabled).toBe(true);
    expect(more(row).disabled).toBe(false);
    expect(cellTitle(row)).toBe("Duration All day");

    fireEvent.input(range(row), { target: { value: "30" } });
    row = rowFor(container, "Charge GoPro");
    expect(effectiveBlocks(h.store.getState(), "Charge GoPro")).toBe(1);
    expect(label(row)).toBe("30min");
    expect(range(row).value).toBe("30");
    expect(badge(row)).toContain("Today");
  });

  it("applies the same all-day state at notch 0 as the All-day toggle", () => {
    const viaSlider = makeHarness("ready");
    const sliderUi = viaSlider.ui(<Queue />);
    fireEvent.input(range(rowFor(sliderUi.container, "Press")), {
      target: { value: "0" },
    });
    const sliderOverride = viaSlider.store.getState().overrides["Press"];
    expect(label(rowFor(sliderUi.container, "Press"))).toBe("All day");
    expect(sliderOverride?.blocks).toBe(0);
    // The captured duration survives the all-day toggle so it can come back.
    expect(sliderOverride?.blocksBeforeAllDay).toBe(3);

    const viaToggle = makeHarness("ready");
    const toggleUi = viaToggle.ui(<Queue />);
    const expanded = rowFor(toggleUi.container, "Press");
    fireEvent.click(expanded.querySelector('button[aria-label="Expand"]')!);
    fireEvent.click(
      rowFor(toggleUi.container, "Press").querySelector<HTMLButtonElement>(
        ".trow__tog",
      )!,
    );
    expect(viaToggle.store.getState().overrides["Press"]).toEqual(sliderOverride);
    expect(label(rowFor(toggleUi.container, "Press"))).toBe("All day");
    expect(range(rowFor(toggleUi.container, "Press")).value).toBe("0");
  });

  it("leaving notch 0 applies a timed duration, not the captured one", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    fireEvent.input(range(rowFor(container, "Press")), { target: { value: "0" } });
    expect(h.store.getState().overrides["Press"]?.blocksBeforeAllDay).toBe(3);

    fireEvent.input(range(rowFor(container, "Press")), { target: { value: "45" } });
    expect(effectiveBlocks(h.store.getState(), "Press")).toBe(1.5);
    expect(label(rowFor(container, "Press"))).toBe("45min");
  });

  it("applies 15 minutes as its exact 0.5 blocks, never rounded to 30", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    fireEvent.click(
      rowFor(container, "Press").querySelector('button[aria-label="Expand"]')!,
    );
    fireEvent.input(range(rowFor(container, "Press")), { target: { value: "15" } });
    const blocks = h.store.getState().overrides["Press"]?.blocks;
    expect(blocks).toBe(0.5);
    expect(blocks! * 30).toBe(15);
    let row = rowFor(container, "Press");
    expect(label(row)).toBe("15min");
    expect(range(row).getAttribute("aria-valuetext")).toBe("15min");
    expect(effectiveBlocks(h.store.getState(), "Press") * 30).toBe(15);
    row = rowFor(container, "Press");
    expect(label(row)).toBe("15min");
    expect(range(row).value).toBe("15");
  });

  it("allows All-day on a Capacities row as a local today-only state", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    let row = rowFor(container, "Note Processing");
    // A Capacities row keeps the whole track: All-day is local, blocks === 0,
    // and nothing is written back to Capacities.
    expect(range(row).disabled).toBe(false);
    expect(range(row).min).toBe("0");
    expect(cellTitle(row)).toContain("Duration");

    fireEvent.input(range(row), { target: { value: "0" } });
    expect(effectiveBlocks(h.store.getState(), "Note Processing")).toBe(0);
    row = rowFor(container, "Note Processing");
    expect(label(row)).toBe("All day");
    expect(less(row).disabled).toBe(true);
  });

  it("keeps a protected source time resizable but closes All-day, reason as tooltip", () => {
    const h = makeHarness("ready", withNativeTime);
    const { container } = h.ui(<Queue />);
    const row = rowFor(container, "Press");
    expect(range(row).disabled).toBe(false);
    expect(range(row).min).toBe("15");
    expect(cellTitle(row)).toBe(EXCLUSION_REASONS.protectedAllDay);
  });

  it("leaves the All-day notch open on an unlocked row", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    expect(range(rowFor(container, "Press")).min).toBe("0");
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
    // The panel adds the typed field, never a second duration control.
    expect(exact(row)).not.toBeNull();
    expect(row.querySelectorAll('input[aria-label="Exact minutes"]')).toHaveLength(1);
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
      /\.qrow\.trow--dslider\s*\{[^}]*grid-template-columns:\s*18px minmax\(0,\s*1fr\) var\(--trow-dur-w\) 236px/,
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
    expect(appCss).toMatch(
      /\.trow__acts\s*\{[^}]*grid-template-columns:\s*52px 44px 36px 36px 36px/,
    );
    expect(appCss).toMatch(
      /\.qrow\.trow--dslider > \.trow__acts\s*\{[^}]*min-width:\s*236px/,
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
