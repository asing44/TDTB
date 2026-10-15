/* row-actions.test.tsx — S2 of the cockpit UI feedback plan (items 1 + 5):
   the row action set is ONE coherent affordance. Planning adjustments
   (Exclude/Include today, Exact duration) are grouped apart from
   item-handling verbs (Mark complete / Leave as-is / More). Directness is
   deliberately UNCHANGED: Mark complete and Leave as-is stay direct, source
   removal and Delete stay behind More.

   2026-10-13 icon pass (operator request: "row button functionality same but
   with icons instead of words but still tooltip hover"): each row verb face
   is now an inline SVG icon. Accessible names are byte-for-byte unchanged and
   the words stay visible in the hover/focus tooltip; only the face changed.
   The More-menu ITEMS keep their word labels.

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

/** The tooltip bubble rendered for a row action button (the button's parent
    is the Tooltip anchor span; the bubble is its sibling). */
function tooltipFor(btn: HTMLElement): string {
  return (
    btn.parentElement?.querySelector('[role="tooltip"]')?.textContent ?? ""
  );
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
  it("renders the exclusion control as an icon button with the unchanged name", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    const press = rowFor(container, "Press");
    const exclude = buttonIn(press, "Exclude Press today");
    expect(exclude.getAttribute("aria-label")).toBe("Exclude Press today");
    expect(exclude.querySelector("svg")).toBeTruthy();
    expect((exclude.textContent ?? "").trim()).toBe("");
  });

  it("renders the duration editor as an icon button with the unchanged name", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    const press = rowFor(container, "Press");
    const exact = buttonIn(press, "Exact duration for Press");
    expect(exact.getAttribute("aria-label")).toBe("Exact duration for Press");
    expect(exact.querySelector("svg")).toBeTruthy();
    expect((exact.textContent ?? "").trim()).toBe("");
  });

  it("the same control reads Include today once the row is excluded", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    let press = rowFor(container, "Press");
    fireEvent.click(buttonIn(press, "Exclude Press today"));
    // Exclusion is a grouping change: the row leaves its band for the trailing
    // Out today lane, which starts collapsed (Planning 2a). Expand it before
    // re-querying — the pre-click reference is stale either way.
    fireEvent.click(container.querySelector(".band--drop") as HTMLButtonElement);
    press = rowFor(container, "Press");
    expect(press.classList.contains("qrow--excluded")).toBe(true);
    const include = buttonIn(press, "Include Press today");
    expect(include.getAttribute("aria-label")).toBe("Include Press today");
    expect(include.querySelector("svg")).toBeTruthy();
    fireEvent.click(include);
    // Re-included rows return to the Exclude label — the toggle is reversible.
    press = rowFor(container, "Press");
    expect(press.classList.contains("qrow--excluded")).toBe(false);
    expect(
      buttonIn(press, "Exclude Press today").getAttribute("aria-label"),
    ).toBe("Exclude Press today");
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
    expect(exclude.querySelector("svg")).toBeTruthy();
    expect(exclude.getAttribute("aria-label")).toBe(
      "Exclude Upper Fast — Active today",
    );
  });
});

describe("S2 row actions: scope notes and grouping", () => {
  it("carries the scope notes in the tooltip, separating today-only planning from source writes", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    const press = rowFor(container, "Press");

    const exclude = buttonIn(press, "Exclude Press today");
    fireEvent.focus(exclude);
    expect(tooltipFor(exclude)).toContain("Exclude today");
    expect(tooltipFor(exclude)).toContain("this browser");
    expect(tooltipFor(exclude)).toContain("Source unchanged");
    fireEvent.blur(exclude);

    const exact = buttonIn(press, "Exact duration for Press");
    fireEvent.focus(exact);
    expect(tooltipFor(exact)).toContain("Exact duration");
    expect(tooltipFor(exact)).toContain("source unchanged");
    fireEvent.blur(exact);

    const done = buttonIn(press, "Mark complete in source: Press");
    fireEvent.focus(done);
    expect(tooltipFor(done)).toContain("Completes it in the source");
    fireEvent.blur(done);

    const leave = buttonIn(
      press,
      "Leave as-is today without changing source: Press",
    );
    fireEvent.focus(leave);
    expect(tooltipFor(leave)).toContain("records that on the server");
    expect(tooltipFor(leave)).toContain("Source unchanged");
    fireEvent.blur(leave);
  });

  it("distinguishes Exclude today (this browser) from Leave as-is (server)", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    const press = rowFor(container, "Press");

    const exclude = buttonIn(press, "Exclude Press today");
    fireEvent.focus(exclude);
    const excludeDetail = tooltipFor(exclude);
    fireEvent.blur(exclude);

    const leave = buttonIn(
      press,
      "Leave as-is today without changing source: Press",
    );
    fireEvent.focus(leave);
    const leaveDetail = tooltipFor(leave);
    fireEvent.blur(leave);

    expect(excludeDetail).toContain("this browser");
    expect(leaveDetail).toContain("server");
    expect(excludeDetail).not.toBe(leaveDetail);
  });

  it("keeps planning adjustments apart from item-handling verbs without nesting", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    const press = rowFor(container, "Press");
    const actions = press.querySelector(".qrow__actions") as HTMLElement;
    // Planning 2a flattened the two labelled sub-clusters into one icon
    // cluster: the role=group wrappers are gone.
    expect(actions.querySelector('[role="group"]')).toBeNull();
    expect(
      press.querySelector('[role="group"][aria-label="Planning adjustments for Press"]'),
    ).toBeNull();
    expect(
      press.querySelector('[role="group"][aria-label="Item handling for Press"]'),
    ).toBeNull();
    const labels = Array.from(actions.querySelectorAll("button")).map((b) =>
      b.getAttribute("aria-label"),
    );
    // The separation survives as adjacency: the planning pair (Exclude, Exact
    // duration) sits together, between Complete and the trailing item verbs.
    const excludeAt = labels.indexOf("Exclude Press today");
    expect(excludeAt).toBeGreaterThanOrEqual(0);
    expect(labels[excludeAt + 1]).toBe("Exact duration for Press");
    expect(labels).toContain("Mark complete in source: Press");
    expect(labels).toContain("Leave as-is today without changing source: Press");
    expect(labels[labels.length - 1]).toBe("More actions for Press");
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

  it("keeps DOM/focus order: Complete, Exclude, Exact duration, Leave as-is, More", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    const press = rowFor(container, "Press");
    const labels = Array.from(
      press.querySelectorAll(".qrow__actions button"),
    ).map((b) => b.getAttribute("aria-label"));
    expect(labels).toEqual([
      "Mark complete in source: Press",
      "Exclude Press today",
      "Exact duration for Press",
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

    // The row re-mounts into the trailing Out today lane, which starts
    // collapsed (Planning 2a); expand it so these assertions read the node
    // that is actually on screen.
    fireEvent.click(container.querySelector(".band--drop") as HTMLButtonElement);
    press = rowFor(container, "Press");
    expect(press.classList.contains("qrow--excluded")).toBe(true);
    expect(
      press.querySelector(".qrow__excluded-note")?.textContent,
    ).toBe("excluded today");
    const include = buttonIn(press, "Include Press today");
    expect(include.querySelector("svg")).toBeTruthy();
    expect((include.textContent ?? "").trim()).toBe("");

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
    expect(exclude.querySelector("svg")).toBeTruthy();
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
  it("keeps the flat cluster wrapping instead of clipping", () => {
    expect(cssBlock("qrow__actions")).toMatch(/flex-wrap:\s*wrap/);
    // Planning 2a flattened the two labelled sub-clusters; the cluster itself
    // must be allowed to shrink and wrap, never clip.
    expect(cssBlock("qrow__actions")).toMatch(/min-width:\s*0/);
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

describe("S2 row actions: icon-only faces keep every word in aria + tooltip", () => {
  const faces: Array<[label: string, bubble: string]> = [
    ["Exclude Press today", "Exclude today"],
    ["Exact duration for Press", "Exact duration"],
    ["Mark complete in source: Press", "Mark complete"],
    ["Leave as-is today without changing source: Press", "Leave as-is"],
    ["More actions for Press", "More"],
  ];

  it("renders each row action as a button with an icon and no visible word label", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    const press = rowFor(container, "Press");
    for (const [label] of faces) {
      const btn = buttonIn(press, label);
      expect(btn.querySelector("svg")).toBeTruthy();
      expect((btn.textContent ?? "").trim()).toBe("");
    }
  });

  it("keeps the accessible name on the button, unchanged", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    const press = rowFor(container, "Press");
    for (const [label] of faces) {
      expect(buttonIn(press, label).getAttribute("aria-label")).toBe(label);
    }
  });

  it("shows the label and detail in the tooltip on keyboard focus", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    const press = rowFor(container, "Press");
    for (const [label, bubble] of faces) {
      const btn = buttonIn(press, label);
      fireEvent.focus(btn);
      expect(tooltipFor(btn)).toContain(bubble);
      // Label plus the second detail line — not just the one word.
      expect(tooltipFor(btn).length).toBeGreaterThan(bubble.length);
      fireEvent.blur(btn);
    }
  });

  it("shows the tooltip on pointer hover too", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    const press = rowFor(container, "Press");
    const btn = buttonIn(press, "Mark complete in source: Press");
    expect(tooltipFor(btn)).toBe("");
    fireEvent.mouseEnter(btn);
    expect(tooltipFor(btn)).toContain("Mark complete");
    expect(tooltipFor(btn)).toContain("Completes it in the source");
  });

  it("leaves the More-menu items word-labelled", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    const press = rowFor(container, "Press");
    fireEvent.click(buttonIn(press, "More actions for Press"));
    const menu = press.querySelector('[role="menu"]');
    expect(menu?.textContent).toContain("Remove from planning");
    expect(menu?.textContent).toContain("Delete");
  });
});
