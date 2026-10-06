/* row-actions.test.tsx — S2 of the cockpit UI feedback plan (items 1 + 5):
   the row action set is ONE coherent affordance. The unlabelled ⊘/✎ icons
   become visible "Exclude today" / "Include today" and "Exact duration"
   controls with scope notes; planning adjustments (local, reversible) are
   grouped apart from item-handling verbs (Mark complete / Leave as-is /
   More). Directness is deliberately UNCHANGED: Mark complete and Leave as-is
   stay direct, source removal and Delete stay behind More.

   Fixture adapters only: no live adapter, no billed call, no source write.
   The dispatch-mapping checks spy the fake adapter's runtimeAction so the
   labelling change provably leaves controller.stagingAction → runtimeAction
   intact. */

import { afterEach, describe, expect, it } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  waitFor,
} from "@testing-library/preact";

afterEach(cleanup);
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

const APP_CSS = readFileSync(resolve(process.cwd(), "src/app.css"), "utf8");

function cssBlock(selector: string): string {
  const m = APP_CSS.match(new RegExp(`\\.${selector}\\s*\\{([^}]*)\\}`));
  if (!m) throw new Error(`selector not found in app.css: .${selector}`);
  return m[1];
}

/* @testing-library/preact declares `render().container` as `Element`, so the
   query helpers accept `Element` and use only Element APIs. */
function rowFor(container: Element, name: string): HTMLElement {
  const rows = Array.from(container.querySelectorAll<HTMLElement>(".qrow"));
  const row = rows.find(
    (r) => r.querySelector(".qrow__name")?.textContent === name,
  );
  if (!row) throw new Error(`no row named ${name}`);
  return row;
}

function buttonIn(root: Element, label: string): HTMLButtonElement {
  const btn = root.querySelector<HTMLButtonElement>(
    `button[aria-label="${label}"]`,
  );
  if (!btn) throw new Error(`no button named ${label}`);
  return btn;
}

/** Same fixture harness as test-harness, but with a spy wrapped around the
    fake adapter's runtimeAction so wire mapping is observable. */
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

describe("S2 row actions: planning adjustments carry visible labels", () => {
  it("names exclusion Exclude today instead of the bare ⊘ icon", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    const press = rowFor(container, "Press");
    const exclude = buttonIn(press, "Exclude Press today");
    expect(exclude.textContent).toContain("Exclude today");
    expect(exclude.textContent).not.toContain("⊘");
  });

  it("names the duration editor Exact duration instead of the bare ✎ icon", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    const press = rowFor(container, "Press");
    const exact = buttonIn(press, "Exact duration for Press");
    expect(exact.textContent).toContain("Exact duration");
    expect(exact.textContent).not.toContain("✎");
  });

  it("the same control reads Include today once the row is excluded", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    let press = rowFor(container, "Press");
    fireEvent.click(buttonIn(press, "Exclude Press today"));
    // Exclusion is a grouping change: the row leaves its band for the trailing
    // "Excluded today" section, so Preact mounts a fresh DOM node there and the
    // pre-click reference is stale. Re-query before touching the row again.
    press = rowFor(container, "Press");
    expect(press.classList.contains("qrow--excluded")).toBe(true);
    const include = buttonIn(press, "Include Press today");
    expect(include.textContent).toContain("Include today");
    fireEvent.click(include);
    // Re-included rows return to the Exclude label — the toggle is reversible.
    press = rowFor(container, "Press");
    expect(press.classList.contains("qrow--excluded")).toBe(false);
    expect(buttonIn(press, "Exclude Press today").textContent).toContain(
      "Exclude today",
    );
  });

  it("keeps the exact accessible-name construction: verb + verbatim item name + today", () => {
    // The operator's annotation read "Exclude Upper Fast — Active today".
    // Queue.tsx builds verb + row.name + "today" with no status suffix
    // anywhere; the row's name simply IS "Upper Fast — Active". Names are
    // sequence identity — never strip a status word out of them.
    const h = makeHarness("ready", (sc) => {
      sc.inputs.assigned = [
        {
          ...sc.inputs.assigned[0],
          id: "Upper Fast — Active",
          name: "Upper Fast — Active",
        },
        ...sc.inputs.assigned.slice(1),
      ];
    });
    const { container, getByText } = h.ui(<Queue />);
    expect(getByText("Upper Fast — Active")).toBeTruthy();
    const exclude = buttonIn(
      container,
      "Exclude Upper Fast — Active today",
    );
    expect(exclude.textContent).toContain("Exclude today");
    expect(exclude.getAttribute("aria-label")).toBe(
      "Exclude Upper Fast — Active today",
    );
  });
});

describe("S2 row actions: scope notes and grouping", () => {
  it("shows visible scope notes that separate today-only planning from source writes", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    const press = rowFor(container, "Press");

    const exclude = buttonIn(press, "Exclude Press today");
    expect(exclude.textContent).toContain("today only");
    expect(exclude.textContent).toContain("reversible");
    expect(exclude.textContent).toContain("source unchanged");

    const exact = buttonIn(press, "Exact duration for Press");
    expect(exact.textContent).toContain("today only");
    expect(exact.textContent).toContain("source unchanged");

    const done = buttonIn(press, "Mark complete in source: Press");
    expect(done.textContent).toContain("updates source");

    const leave = buttonIn(
      press,
      "Leave as-is today without changing source: Press",
    );
    expect(leave.textContent).toContain("today only");
    expect(leave.textContent).toContain("source unchanged");
  });

  it("groups planning adjustments apart from item-handling verbs", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    const press = rowFor(container, "Press");

    const plan = press.querySelector(
      '[role="group"][aria-label="Planning adjustments for Press"]',
    );
    const item = press.querySelector(
      '[role="group"][aria-label="Item handling for Press"]',
    );
    expect(plan).toBeTruthy();
    expect(item).toBeTruthy();
    expect(
      plan!.querySelector('button[aria-label="Exclude Press today"]'),
    ).toBeTruthy();
    expect(
      plan!.querySelector('button[aria-label="Exact duration for Press"]'),
    ).toBeTruthy();
    expect(
      item!.querySelector(
        'button[aria-label="Mark complete in source: Press"]',
      ),
    ).toBeTruthy();
    expect(
      item!.querySelector(
        'button[aria-label="Leave as-is today without changing source: Press"]',
      ),
    ).toBeTruthy();
    expect(
      item!.querySelector('button[aria-label="More actions for Press"]'),
    ).toBeTruthy();
  });

  it("keeps source removal and Delete behind More, not in the labelled cluster", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    const press = rowFor(container, "Press");
    fireEvent.click(buttonIn(press, "More actions for Press"));
    const menu = press.querySelector('[role="menu"]');
    expect(menu).toBeTruthy();
    expect(menu!.textContent).toContain("Remove from planning");
    expect(menu!.textContent).toContain("Delete");
  });

  it("keeps DOM/focus order: planning toggle, duration, direct verbs, More", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    const press = rowFor(container, "Press");
    const labels = Array.from(
      press.querySelectorAll(".qrow__actions button"),
    ).map((b) => b.getAttribute("aria-label"));
    expect(labels).toEqual([
      "Exclude Press today",
      "Exact duration for Press",
      "Mark complete in source: Press",
      "Leave as-is today without changing source: Press",
      "More actions for Press",
    ]);
  });
});

describe("S2 row actions: excluded rows and busy state", () => {
  it("an excluded row stays readable and offers the reversible Include toggle", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    let press = rowFor(container, "Press");
    fireEvent.click(buttonIn(press, "Exclude Press today"));

    // The row re-mounts into the trailing "Excluded today" section; re-query
    // so these assertions read the node that is actually on screen.
    press = rowFor(container, "Press");
    expect(press.classList.contains("qrow--excluded")).toBe(true);
    expect(
      press.querySelector(".qrow__excluded-note")?.textContent,
    ).toBe("excluded today");
    const include = buttonIn(press, "Include Press today");
    expect(include.textContent).toContain("Include today");
    expect(include.textContent).toContain("source unchanged");

    fireEvent.click(include);
    press = rowFor(container, "Press");
    expect(press.classList.contains("qrow--excluded")).toBe(false);
    expect(buttonIn(press, "Exclude Press today")).toBeTruthy();
  });

  it("keeps the labels legible while a runtime verb is busy", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    const press = rowFor(container, "Press");
    act(() => {
      h.store.dispatch({ type: "RUNTIME_START" });
    });

    expect(buttonIn(press, "Mark complete in source: Press").disabled).toBe(
      true,
    );
    expect(buttonIn(press, "More actions for Press").disabled).toBe(true);
    // Planning adjustments are local selection changes, not source verbs:
    // their labels stay visible and the toggle keeps working while busy.
    const exclude = buttonIn(press, "Exclude Press today");
    expect(exclude.disabled).toBe(false);
    expect(exclude.textContent).toContain("Exclude today");
  });
});

describe("S2 row actions: dispatch mapping is unchanged", () => {
  it("direct verbs still fire done and drop_from_plan through runtimeAction", async () => {
    const h = spyHarness();
    const { container } = h.ui(<Queue />);
    let press = rowFor(container, "Press");

    fireEvent.click(buttonIn(press, "Mark complete in source: Press"));
    await waitFor(() =>
      expect(h.calls).toEqual([{ verb: "done", target: "Press" }]),
    );

    press = rowFor(container, "Press");
    fireEvent.click(
      buttonIn(
        press,
        "Leave as-is today without changing source: Press",
      ),
    );
    await waitFor(() =>
      expect(h.calls).toEqual([
        { verb: "done", target: "Press" },
        { verb: "drop_from_plan", target: "Press" },
      ]),
    );
  });

  it("planning controls never reach the adapter — exclusion is a selection toggle", () => {
    const h = spyHarness();
    const { container } = h.ui(<Queue />);
    const press = rowFor(container, "Press");

    fireEvent.click(buttonIn(press, "Exact duration for Press"));
    expect(h.store.getState().ui.editorItem).toBe("Press");
    expect(h.store.getState().ui.editorIntent).toBe("duration");

    fireEvent.click(buttonIn(press, "Exclude Press today"));
    expect(h.store.getState().overrides["Press"]).toEqual({
      included: false,
      blocks: null,
    });
    expect(h.calls).toEqual([]);
  });

  it("More verbs keep their frozen wire mapping", async () => {
    const h = spyHarness();
    const { container } = h.ui(<Queue />);
    let press = rowFor(container, "Press");

    fireEvent.click(buttonIn(press, "More actions for Press"));
    fireEvent.click(
      buttonIn(
        press,
        "Remove from planning and update source: Press",
      ),
    );
    await waitFor(() =>
      expect(h.calls).toEqual([{ verb: "unassign", target: "Press" }]),
    );

    press = rowFor(container, "Press");
    fireEvent.click(buttonIn(press, "More actions for Press"));
    fireEvent.click(buttonIn(press, "Delete permanently: Press"));
    const confirm = buttonIn(press, "Confirm delete permanently: Press");
    fireEvent.click(confirm);
    await waitFor(() =>
      expect(h.calls).toEqual([
        { verb: "unassign", target: "Press" },
        { verb: "delete", target: "Press" },
      ]),
    );
  });
});

describe("S2 row actions: responsive reachability", () => {
  it("keeps the labelled cluster wrapping instead of clipping", () => {
    expect(cssBlock("qrow__actions")).toMatch(/flex-wrap:\s*wrap/);
    expect(cssBlock("qrow__action-group")).toMatch(/flex-wrap:\s*wrap/);
    expect(cssBlock("qrow__action-group")).toMatch(/min-width:\s*0/);
    // The 1100px re-template moves the whole cluster out of the fixed
    // desktop track and onto its own full-width line.
    expect(APP_CSS).toMatch(
      /@media\s*\(max-width:\s*1100px\)[\s\S]*\.qrow__actions\s*\{[^}]*grid-column:\s*1\s*\/\s*-1/,
    );
    // Phone width already gives More its own line; keep that reachability.
    expect(APP_CSS).toMatch(
      /@media\s*\(max-width:\s*767px\)[\s\S]*\.row-more\s*\{[^}]*flex-basis:\s*100%/,
    );
  });

  it("keeps the 44px control floor on the new labelled buttons", () => {
    expect(cssBlock("planbtn")).toMatch(/min-height:\s*44px/);
    expect(cssBlock("planbtn")).toMatch(/min-width:\s*44px/);
  });
});
