/* row-actions-live.test.tsx — the parts of the pre-F4a row-actions contract
   that still describe the shipped product (restored 2026-10-16, F4a third
   pass). The collapsed task row now offers three labelled controls —
   Complete, Skip today, Expand — plus the transitional per-row More menu; the
   icon-only planning pair (Exclude today, Exact duration) left the row with
   the slider and the duration steppers and return in the expanded panel
   (F4b), so their assertions were retired with them (see the retired-test
   ledger, design/task-actions/retired-tests.md).

   What stays pinned here, because it is behavior that must hold NOW:
   - source removal and Delete are reachable only through More;
   - the More verbs keep their frozen wire mapping (unassign, delete + confirm);
   - direct completion still fires `done`; Skip today fires `drop_from_plan`;
   - a busy runtime disables the source-writing controls;
   - the controls keep their 44px-class floor, phone-width wrapping, and
     explicit focus ring.

   Fixture adapters only: no live adapter, no billed call, no source write. */

import { afterEach, describe, expect, it } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  waitFor,
} from "@testing-library/preact";
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

/** Open the row's More menu and return it. */
function openMore(row: HTMLElement): HTMLElement {
  const trigger = row.querySelector<HTMLButtonElement>(
    'button[aria-haspopup="menu"]',
  );
  if (!trigger) throw new Error("no More trigger on the row");
  fireEvent.click(trigger);
  const menu = row.querySelector<HTMLElement>('[role="menu"]');
  if (!menu) throw new Error("More menu is not open");
  return menu;
}

/** Same fixture harness as test-harness, but with a spy wrapped around the
    fake adapter's runtimeAction so the wire mapping is observable. */
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
  const calls: Array<{ verb: string; target: string }> = [];
  const original = adapter.runtimeAction.bind(adapter);
  adapter.runtimeAction = async (verb, target, args) => {
    calls.push({ verb, target });
    return original(verb, target, args);
  };
  const controller = new Controller(adapter, store.dispatch, store.getState);
  const ui = (children: ComponentChildren) =>
    render(<Ctx.Provider value={{ store, controller }}>{children}</Ctx.Provider>);
  return { store, controller, calls, ui };
}

describe("collapsed row: direct verbs and More", () => {
  it("keeps source removal and Delete inside More, never in the row cluster", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    const press = rowFor(container, "Press");
    const direct = Array.from(
      press.querySelectorAll<HTMLElement>(".trow__acts > button"),
    ).map((b) => b.getAttribute("aria-label"));
    expect(direct).toEqual(["Complete", "Skip today", "Expand"]);
    // Source removal and Delete are not direct controls.
    expect(press.querySelector('button[aria-label*="Remove from planning"]')).toBeNull();
    expect(press.querySelector('button[aria-label*="Delete"]')).toBeNull();
    // …they are the More menu's business.
    const menu = openMore(press);
    expect(menu.textContent).toContain("Remove from planning");
    expect(menu.textContent).toContain("Delete");
  });

  it("keeps DOM/focus order: Complete, Skip today, Expand, More", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    const press = rowFor(container, "Press");
    const labels = Array.from(
      press.querySelectorAll<HTMLElement>(".trow__acts button"),
    ).map((b) => b.getAttribute("aria-label"));
    expect(labels).toEqual([
      "Complete",
      "Skip today",
      "Expand",
      "More actions for Press",
    ]);
  });
});

describe("collapsed row: dispatch mapping is unchanged", () => {
  it("Complete fires the done verb for the row", async () => {
    const h = spyHarness();
    const { container } = h.ui(<Queue />);
    fireEvent.click(buttonNamed(rowFor(container, "Press"), "Complete"));
    await waitFor(() =>
      expect(h.calls).toEqual([{ verb: "done", target: "Press" }]),
    );
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
  });

  it("More verbs keep their frozen wire mapping, Delete behind a confirm", async () => {
    const h = spyHarness();
    const { container } = h.ui(<Queue />);

    let press = rowFor(container, "Press");
    fireEvent.click(buttonNamed(press, "More actions for Press"));
    fireEvent.click(
      buttonNamed(
        press,
        "Remove from planning and update source: Press",
      ),
    );
    await waitFor(() =>
      expect(h.calls).toEqual([{ verb: "unassign", target: "Press" }]),
    );

    press = rowFor(container, "Press");
    fireEvent.click(buttonNamed(press, "More actions for Press"));
    fireEvent.click(buttonNamed(press, "Delete permanently: Press"));
    // Delete arms to Sure? before it fires — permanent means permanent.
    expect(h.calls).toHaveLength(1);
    fireEvent.click(buttonNamed(press, "Confirm delete permanently: Press"));
    await waitFor(() =>
      expect(h.calls).toEqual([
        { verb: "unassign", target: "Press" },
        { verb: "delete", target: "Press" },
      ]),
    );
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
    expect(buttonNamed(press, "More actions for Press").disabled).toBe(true);
    // Selection is a local change, not a source verb: it keeps working.
    const check = press.querySelector<HTMLInputElement>(".trow__check")!;
    expect(check.disabled).toBe(false);
    fireEvent.click(check);
    expect(h.store.getState().ui.selection).toContain("Press");
  });
});

describe("collapsed row: reachability and focus targets", () => {
  it("keeps a 44px-class floor on the More controls", () => {
    // The row's labelled buttons are text-sized; the icon-only More trigger
    // and its menu items keep the 44px floor the retired icon cluster had.
    expect(cssBlock("alloc-verb")).toMatch(/min-height:\s*44px/);
  });

  it("keeps an explicit focus ring on the new labelled controls", () => {
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
