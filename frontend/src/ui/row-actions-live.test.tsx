/* row-actions-live.test.tsx — the parts of the row-actions contract that
   still describe the shipped product (rewritten 2026-10-16 for F7).

   F7 removed the transitional per-row More (⋯) menu. The collapsed row now
   offers exactly three labelled controls — Complete, Skip today, Expand — and
   every secondary verb moved to where the approved prototype puts it: the
   source verbs (Unassign / Skip occurrence, Hide always, Delete) on the
   expanded row's Source menu, and the planning verbs on the expanded row's
   line-1 controls (covered by row-expanded.test.tsx).

   What stays pinned here, because it is behavior that must hold NOW:
   - the row cluster is exactly Complete, Skip today, Expand, in that order;
   - source removal and Delete are reachable only through the Source menu, not
     from the row cluster or the keyboard shortcuts;
   - the verbs keep their frozen wire mapping: `done` / `unassign` /
     `hide_always` ride the journaled batch wire against the stable identity,
     Skip today stays the immediate `drop_from_plan`, and Delete stays
     individual-only behind a confirm;
   - a busy runtime disables the source-writing controls;
   - the surviving controls keep an explicit focus ring, a hit-area floor, and
     phone-width wrapping.

   Fixture adapters only: no live adapter, no billed call, no source write. */

import { afterEach, describe, expect, it } from "vitest";
import { act, cleanup, fireEvent, render, waitFor } from "@testing-library/preact";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import type { ComponentChildren } from "preact";

import { Queue } from "./Queue";
import { Ctx } from "./context";
import { createStore } from "../store/createStore";
import { Controller } from "../store/controller";
import { FixtureAdapter } from "../adapters/fixture";
import { makeScenario } from "../fixtures/scenarios";
import { makeHarness } from "./test-harness";

afterEach(cleanup);

const APP_CSS = readFileSync(resolve(process.cwd(), "src/app.css"), "utf8");

function cssBlock(selector: string): string {
  const m = APP_CSS.match(new RegExp(`\\.${selector}\\s*\\{([^}]*)\\}`));
  if (!m) throw new Error(`selector not found in app.css: .${selector}`);
  return m[1];
}

/* @testing-library/preact declares `render().container` as `Element`, so the
   query helpers accept `Element` and use only Element APIs. */
function rowFor(container: Element, name: string): HTMLElement {
  const row = Array.from(container.querySelectorAll<HTMLElement>(".qrow")).find(
    (r) => r.querySelector(".qrow__name")?.textContent === name,
  );
  if (!row) throw new Error(`no row named ${name}`);
  return row;
}

function buttonNamed(root: Element, label: string): HTMLButtonElement {
  const btn = root.querySelector<HTMLButtonElement>(
    `button[aria-label="${label}"]`,
  );
  if (!btn) throw new Error(`no button named ${label}`);
  return btn;
}

function buttonWithText(root: Element, text: string): HTMLButtonElement {
  const btn = Array.from(root.querySelectorAll<HTMLButtonElement>("button")).find(
    (b) => b.textContent?.trim() === text,
  );
  if (!btn) throw new Error(`no button reading ${text}`);
  return btn;
}

/** Expand a row and return its refreshed element. */
function expand(container: Element, name: string): HTMLElement {
  fireEvent.click(rowFor(container, name).querySelector('button[aria-label="Expand"]')!);
  return rowFor(container, name);
}

/** Open the expanded row's Source menu and return it. */
function openSource(row: HTMLElement): HTMLElement {
  const trigger = row.querySelector<HTMLButtonElement>(".source-menu__trigger");
  if (!trigger) throw new Error("no Source trigger on the row");
  fireEvent.click(trigger);
  const menu = row.querySelector<HTMLElement>('[role="menu"]');
  if (!menu) throw new Error("Source menu is not open");
  return menu;
}

/** Same fixture harness as test-harness, but with spies around the fake
    adapter's two write routes so the wire mapping is observable: per-row
    verbs ride `applyRuntimeBatch`, Skip today and Delete ride
    `runtimeAction`. */
function spyHarness() {
  const sc = makeScenario("ready");
  const store = createStore();
  store.dispatch({
    type: "INPUTS_LOADED",
    inputs: sc.inputs,
    ledger: { ...sc.ledger },
  });
  store.dispatch({
    type: "SETUP_SAVED",
    daySetup: { ...sc.inputs.daySetup, confirmed: true },
  });
  const adapter = new FixtureAdapter("ready");
  const batched: Array<{ verb: string; target: string }> = [];
  const applied = adapter.applyRuntimeBatch.bind(adapter);
  adapter.applyRuntimeBatch = async (items) => {
    for (const item of items) batched.push({ verb: item.verb, target: item.target });
    return applied(items);
  };
  const calls: Array<{ verb: string; target: string }> = [];
  const original = adapter.runtimeAction.bind(adapter);
  adapter.runtimeAction = async (verb, target, args) => {
    calls.push({ verb, target });
    return original(verb, target, args);
  };
  const controller = new Controller(adapter, store.dispatch, store.getState);
  const ui = (children: ComponentChildren) =>
    render(<Ctx.Provider value={{ store, controller }}>{children}</Ctx.Provider>);
  return { store, controller, batched, calls, ui };
}

describe("collapsed row: direct verbs", () => {
  it("offers exactly Complete, Skip today and Expand — never source removal or Delete", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    const press = rowFor(container, "Press");
    const direct = Array.from(
      press.querySelectorAll<HTMLElement>(".trow__acts > button"),
    ).map((b) => b.getAttribute("aria-label"));
    expect(direct).toEqual(["Complete", "Skip today", "Expand"]);
    // Source removal and Delete are not direct controls, and the transitional
    // More menu that used to hold them is gone.
    expect(press.querySelector('button[aria-label*="Remove from planning"]')).toBeNull();
    expect(press.querySelector('button[aria-label*="Delete"]')).toBeNull();
    expect(press.querySelector('button[aria-haspopup="menu"]')).toBeNull();
    // …the collapsed row therefore has no menu at all; the Source menu lives
    // on the expanded row.
    expect(press.querySelector('[role="menu"]')).toBeNull();
    const expanded = expand(container, "Press");
    expect(expanded.querySelector(".source-menu__trigger")).not.toBeNull();
  });

  it("keeps DOM/focus order: Complete, Skip today, Expand", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    const press = rowFor(container, "Press");
    const labels = Array.from(
      press.querySelectorAll<HTMLElement>(".trow__acts button"),
    ).map((b) => b.getAttribute("aria-label"));
    expect(labels).toEqual(["Complete", "Skip today", "Expand"]);
  });
});

describe("collapsed row: dispatch mapping is unchanged", () => {
  it("Complete fires the done verb for the row", async () => {
    const h = spyHarness();
    const { container } = h.ui(<Queue />);
    fireEvent.click(buttonNamed(rowFor(container, "Press"), "Complete"));
    await waitFor(() =>
      expect(h.batched).toEqual([{ verb: "done", target: "todoist:6fx00xPRESS" }]),
    );
    expect(h.calls).toEqual([]);
  });

  it("Skip today fires drop_from_plan against the row identity", async () => {
    const h = spyHarness();
    const { container } = h.ui(<Queue />);
    fireEvent.click(buttonNamed(rowFor(container, "Press"), "Skip today"));
    await waitFor(() =>
      expect(h.calls).toEqual([
        { verb: "drop_from_plan", target: "todoist:6fx00xPRESS" },
      ]),
    );
    expect(h.batched).toEqual([]);
  });

  it("Source-menu verbs keep their frozen wire mapping", async () => {
    const h = spyHarness();
    const { container } = h.ui(<Queue />);

    const row = expand(container, "Press");
    const menu = openSource(row);
    expect(
      Array.from(menu.querySelectorAll<HTMLElement>('[role="menuitem"]')).map((b) =>
        b.getAttribute("aria-label"),
      ),
    ).toEqual(["Unassign in source", "Hide always", "Delete source…"]);
    fireEvent.click(buttonNamed(menu, "Unassign in source"));
    await waitFor(() =>
      expect(h.batched).toEqual([
        { verb: "unassign", target: "todoist:6fx00xPRESS" },
      ]),
    );
    expect(h.calls).toEqual([]);
  });

  it("Delete stays individual-only and behind a confirm", async () => {
    const h = spyHarness();
    const { container } = h.ui(<Queue />);

    const row = expand(container, "Press");
    const menu = openSource(row);
    fireEvent.click(buttonNamed(menu, "Delete source…"));
    // Delete arms to a confirm before it fires — permanent means permanent.
    expect(h.calls).toEqual([]);
    expect(h.batched).toEqual([]);
    fireEvent.click(buttonWithText(rowFor(container, "Press"), "Delete from Todoist"));
    await waitFor(() =>
      expect(h.calls).toEqual([{ verb: "delete", target: "Press" }]),
    );
    expect(h.batched).toEqual([]);
  });
});

describe("collapsed row: busy runtime", () => {
  it("disables the source-writing controls and leaves selection local", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    const press = rowFor(container, "Press");
    act(() => {
      h.store.dispatch({ type: "RUNTIME_START" });
    });

    expect(buttonNamed(press, "Complete").disabled).toBe(true);
    expect(buttonNamed(press, "Skip today").disabled).toBe(true);
    // The Source menu's verbs are disabled with the written reason.
    const row = expand(container, "Press");
    const menu = openSource(row);
    for (const item of Array.from(menu.querySelectorAll<HTMLButtonElement>('[role="menuitem"]'))) {
      expect(item.disabled).toBe(true);
      const reasonId = item.getAttribute("aria-describedby")!;
      expect(menu.querySelector(`#${reasonId}`)?.textContent).toBe(
        "Source action in progress",
      );
    }
    // Selection is a local change, not a source verb: it keeps working.
    const check = press.querySelector<HTMLInputElement>(".trow__check")!;
    expect(check.disabled).toBe(false);
    fireEvent.click(check);
    expect(h.store.getState().ui.selection).toContain("Press");
  });
});

describe("row controls: reachability and focus targets", () => {
  it("keeps a hit-area floor on the surviving row and source controls", () => {
    // F7 retired the icon-only More trigger and its 44px menu items. The
    // surviving text-sized controls still declare a real hit area rather than
    // collapsing to their glyph height.
    expect(cssBlock("trow__btn")).toMatch(/min-height:\s*\d+px/);
    expect(cssBlock("source-menu__item")).toMatch(/min-height:\s*\d+px/);
  });

  it("keeps an explicit focus ring on the labelled controls", () => {
    expect(APP_CSS).toMatch(
      /\.trow__btn:focus-visible[^{]*\{[^}]*outline:\s*2px solid/,
    );
    expect(APP_CSS).toMatch(
      /\.trow__check:focus-visible[^{]*\{[^}]*outline:\s*2px solid/,
    );
  });

  it("raises the control height at phone width instead of clipping the cluster", () => {
    expect(APP_CSS).toMatch(
      /@media\s*\(max-width:\s*640px\)[\s\S]*?\.trow__btn\s*\{[^}]*min-height:\s*38px/,
    );
    expect(APP_CSS).toMatch(
      /@media\s*\(max-width:\s*640px\)[\s\S]*?\.trow__acts\s*\{[^}]*flex-wrap:\s*wrap/,
    );
  });
});
