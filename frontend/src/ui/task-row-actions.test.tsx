/* task-row-actions.test.tsx — task-actions F4a: the collapsed row's own
   contract, and the Unassign in Todoist → Skipped today → Restore today
   round trip.

   Covered here (and only here):
   - the collapsed row's controls and their accessible names;
   - one expanded row at a time, with aria-expanded following it;
   - selection independent of expansion;
   - U4's double-confirmed source unassign through the fixture adapter, the
     Skipped today section, and Restore today resolved from the drop entry's
     own identity;
   - the Day Setup gate as written reason text, not just a disabled button;
   - the C and E shortcuts acting on the row that received the key: C still
     completes in the source, E is local plan membership with no source write.

   Fixture adapters only: no live adapter, no billed call, no source write.

   One honest limit: the drop projection (`inputs.droppedToday`) is written by
   the server's plan-inputs read, and FixtureAdapter does not model it — no
   fixture control turns a `drop_from_plan` into a `dropped[]` entry. So the
   round trip publishes the read the controller would receive (the same
   `SOURCE_REFRESH_OK` shape header/failure-states tests stage), while every
   verb itself still travels through FixtureAdapter.applyRuntimeBatch. */

import { afterEach, describe, expect, it } from "vitest";
import { act, cleanup, fireEvent, waitFor } from "@testing-library/preact";

import { Queue } from "./Queue";
import { makeHarness, type Harness } from "./test-harness";
import { UNASSIGN_CONFIRM_LABEL, UNASSIGN_TRIGGER_LABEL } from "./ConfirmUnassign";
import { DAY_SETUP_GATE_REASON } from "../store/store";
import { fingerprintFixedInputs } from "../model/fingerprint";
import { fixedInputsOf } from "../fixtures/scenarios";
import type { PlanInputs } from "../model/types";

afterEach(cleanup);

/** Press' stable row identity in the fixture scenario — the runtime verb
    target, and the identity Skipped today resolves Restore today from. */
const PRESS_IDENTITY = "todoist:6fx00xPRESS";

function rowFor(container: Element, name: string): HTMLElement {
  const row = Array.from(container.querySelectorAll<HTMLElement>(".qrow")).find(
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

function panelOf(container: Element): HTMLElement | null {
  return container.querySelector<HTMLElement>('[data-slot="row-controls"]');
}

/** Wrap the fixture adapter's runtimeAction so the wire mapping is observable
    (the same spy shape row-actions-live.test.tsx uses). The row verbs ride the
    journaled batch wire (F7: `runRowBatch`), so those are spied separately. */
function spyVerbs(h: Harness): Array<{ verb: string; target: string }> {
  const calls: Array<{ verb: string; target: string }> = [];
  const original = h.adapter.runtimeAction.bind(h.adapter);
  h.adapter.runtimeAction = async (verb, target, args) => {
    calls.push({ verb, target });
    return original(verb, target, args);
  };
  return calls;
}

/** Publish the plan-inputs read the controller would get from the server.
    Used only for the drop projection the fixture adapter does not model. */
function stageServerRead(
  h: Harness,
  mutate: (inputs: PlanInputs) => void,
): void {
  const inputs = structuredClone(h.store.getState().inputs!);
  mutate(inputs);
  act(() => {
    h.store.dispatch({
      type: "SOURCE_REFRESH_OK",
      inputs,
      ledger: { ...h.store.getState().ledger! },
      fingerprint: fingerprintFixedInputs(fixedInputsOf(inputs)),
      anchoredSourceFingerprint: inputs.anchoredSourceFingerprint,
      planningConfigFingerprint: inputs.planningConfigFingerprint,
      at: "2026-10-16T09:15:00.000Z",
    });
  });
}

describe("collapsed row: the controls and their names", () => {
  it("renders a labelled selection checkbox, the Mint toggle and the three row verbs", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    const press = rowFor(container, "Press");

    const check = press.querySelector<HTMLInputElement>(".trow__check")!;
    expect(check.type).toBe("checkbox");
    // Selection is a checkbox with its own name, not the row's expand control.
    expect(check.getAttribute("aria-label")).toBe("Select Press");
    expect(check.checked).toBe(false);

    const labels = Array.from(
      press.querySelectorAll<HTMLElement>(".trow__acts > button"),
    ).map((b) => b.getAttribute("aria-label"));
    expect(labels).toEqual([
      "May overlap Mint: Press",
      "Complete",
      UNASSIGN_TRIGGER_LABEL,
      "Expand",
    ]);
    // The lead control is the permanent May-overlap-Mint toggle: a pressed
    // state exposed to assistive tech, applied the moment it is clicked.
    const mint = buttonIn(press, "May overlap Mint: Press");
    expect(mint.getAttribute("aria-pressed")).toBe("false");
    expect(mint.textContent?.trim()).toBe("Mint");
    // Complete is a compact icon button: check-mark face, name + tooltip.
    const complete = buttonIn(press, "Complete");
    expect(complete.textContent).toBe("");
    expect(complete.querySelector("svg")).toBeTruthy();
    expect(complete.getAttribute("title")).toBe("Complete");
    // The source verb is a compact icon button: no visible text, the full
    // label is its accessible name and tooltip.
    const unassign = buttonIn(press, UNASSIGN_TRIGGER_LABEL);
    expect(unassign.textContent).toBe("");
    expect(unassign.querySelector("svg")).toBeTruthy();
    expect(unassign.getAttribute("title")).toBe(UNASSIGN_TRIGGER_LABEL);

    const expand = buttonIn(press, "Expand");
    expect(expand.textContent).toBe("");
    expect(expand.querySelector("svg")).toBeTruthy();
    expect(expand.getAttribute("title")).toBe("Expand");
    expect(expand.getAttribute("aria-expanded")).toBe("false");
    // The disclosure names the panel it will fill; collapsed, it is absent.
    expect(expand.getAttribute("aria-controls")).toBe("exp-Press");
    expect(panelOf(press)).toBeNull();
  });

  it("gives every row the same four controls in the same order", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    const rows = Array.from(
      container.querySelectorAll<HTMLElement>(".qrow:not(.trow--skipped)"),
    );
    expect(rows.length).toBeGreaterThan(1);
    for (const row of rows) {
      const labels = Array.from(
        row.querySelectorAll<HTMLElement>(".trow__acts > button"),
      ).map((b) => b.getAttribute("aria-label"));
      // KTD4/U4: the source unassign is Todoist-only; the Assigned switch
      // replaces it on a Capacities or calendar row.
      const heldBySwitch = row.querySelector(".qrow__src--capacities, .qrow__src--calendar");
      // The empty unassign slot stays reserved so Complete and Expand keep
      // their column on every row.
      expect(row.querySelectorAll(".trow__acts > .trow__slot")).toHaveLength(
        heldBySwitch ? 1 : 0,
      );
      expect(labels.slice(0, 4)).toEqual(
        heldBySwitch
          ? [expect.stringMatching(/^May overlap Mint/), "Complete", "Expand"]
          : [
              expect.stringMatching(/^May overlap Mint/),
              "Complete",
              UNASSIGN_TRIGGER_LABEL,
              "Expand",
            ],
      );
    }
  });
});

describe("collapsed row: one row expands at a time", () => {
  it("expands one row and collapses the previous one", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);

    fireEvent.click(buttonIn(rowFor(container, "Press"), "Expand"));
    expect(h.store.getState().ui.expandedId).toBe("Press");
    let press = rowFor(container, "Press");
    expect(press.getAttribute("class")).toContain("trow--open");
    const collapse = buttonIn(press, "Collapse");
    expect(collapse.getAttribute("aria-expanded")).toBe("true");
    expect(collapse.textContent).toBe("");
    expect(collapse.getAttribute("title")).toBe("Collapse");
    const panel = panelOf(press)!;
    expect(panel.id).toBe("exp-Press");
    expect(panel.getAttribute("role")).toBe("group");
    // The panel is labelled by the row's own name node, never by a copy.
    expect(panel.getAttribute("aria-labelledby")).toBe("name-Press");

    // Expanding a second row collapses the first: one panel, ever.
    fireEvent.click(buttonIn(rowFor(container, "Magic Mirror"), "Expand"));
    expect(h.store.getState().ui.expandedId).toBe("Magic Mirror");
    press = rowFor(container, "Press");
    expect(panelOf(press)).toBeNull();
    expect(buttonIn(press, "Expand").getAttribute("aria-expanded")).toBe("false");
    expect(container.querySelectorAll('[data-slot="row-controls"]')).toHaveLength(1);

    // Collapsing the open row leaves nothing expanded.
    fireEvent.click(buttonIn(rowFor(container, "Magic Mirror"), "Collapse"));
    expect(h.store.getState().ui.expandedId).toBeNull();
    expect(panelOf(container)).toBeNull();
  });
});

describe("collapsed row: selection is independent of expansion", () => {
  it("selects without expanding and expands without selecting", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);

    fireEvent.click(rowFor(container, "Press").querySelector(".trow__check")!);
    expect(h.store.getState().ui.selection).toEqual(["Press"]);
    expect(h.store.getState().ui.expandedId).toBeNull();
    expect(panelOf(container)).toBeNull();

    // Expanding neither selects nor deselects.
    fireEvent.click(buttonIn(rowFor(container, "Press"), "Expand"));
    expect(h.store.getState().ui.selection).toEqual(["Press"]);
    expect(h.store.getState().ui.expandedId).toBe("Press");

    // Moving the expansion leaves the selection untouched.
    fireEvent.click(buttonIn(rowFor(container, "Magic Mirror"), "Expand"));
    expect(h.store.getState().ui.selection).toEqual(["Press"]);
    expect(h.store.getState().ui.expandedId).toBe("Magic Mirror");

    // And deselecting never collapses the open row.
    fireEvent.click(rowFor(container, "Press").querySelector(".trow__check")!);
    expect(h.store.getState().ui.selection).toEqual([]);
    expect(h.store.getState().ui.expandedId).toBe("Magic Mirror");
  });
});

describe("collapsed row: the Day Setup gate says why", () => {
  it("disables Complete and the source unassign with the reason as text", () => {
    const h = makeHarness("fresh");
    expect(h.store.getState().daySetup.confirmed).toBe(false);
    const { container } = h.ui(<Queue />);
    const press = rowFor(container, "Press");

    const complete = buttonIn(press, "Complete");
    const skip = buttonIn(press, UNASSIGN_TRIGGER_LABEL);
    expect(complete.disabled).toBe(true);
    expect(skip.disabled).toBe(true);
    // The reason is not only a title (invisible to keyboard/touch): it is a
    // description element attached to each disabled verb.
    expect(complete.title).toBe(DAY_SETUP_GATE_REASON);
    expect(skip.title).toBe(DAY_SETUP_GATE_REASON);
    expect(complete.getAttribute("aria-describedby")).toBe("why-complete-Press");
    expect(skip.getAttribute("aria-describedby")).toBe("why-unassign-Press");
    expect(
      press.querySelector("#why-complete-Press")?.textContent,
    ).toBe(DAY_SETUP_GATE_REASON);
    expect(press.querySelector("#why-unassign-Press")?.textContent).toBe(
      DAY_SETUP_GATE_REASON,
    );
    expect(press.textContent).toContain(DAY_SETUP_GATE_REASON);

    // Expanding is a local view change, so the gate never touches it.
    const expand = buttonIn(press, "Expand");
    expect(expand.disabled).toBe(false);
    fireEvent.click(expand);
    expect(h.store.getState().ui.expandedId).toBe("Press");
  });
});

describe("collapsed row: Unassign in Todoist and the Skipped today section", () => {
  it("unassigns through the adapter after the confirm, then restores from Skipped today", async () => {
    const h = makeHarness("ready");
    const calls = spyVerbs(h);
    const batched: Array<{ verb: string; target: string }> = [];
    const applyBatch = h.adapter.applyRuntimeBatch.bind(h.adapter);
    h.adapter.applyRuntimeBatch = async (items) => {
      for (const item of items) batched.push({ verb: item.verb, target: item.target });
      return applyBatch(items);
    };
    const { container } = h.ui(<Queue />);

    // U4: click 1 opens the confirm and writes nothing; click 2 unassigns.
    fireEvent.click(buttonIn(rowFor(container, "Press"), UNASSIGN_TRIGGER_LABEL));
    expect(batched).toEqual([]);
    const panel = rowFor(container, "Press").querySelector<HTMLElement>(
      ".unassign__panel",
    )!;
    fireEvent.click(
      Array.from(panel.querySelectorAll<HTMLButtonElement>("button")).find(
        (b) => b.textContent?.trim() === UNASSIGN_CONFIRM_LABEL,
      )!,
    );
    await waitFor(() =>
      expect(batched).toEqual([
        { verb: "unassign", target: PRESS_IDENTITY },
      ]),
    );
    // A source unassign is a real source write: never a browser-only exclusion.
    expect(h.store.getState().overrides["Press"]).toBeUndefined();
    // The batch of one settles before the next row verb, so the single batch
    // slot is free for the Restore below.
    await waitFor(() => expect(h.store.getState().batch.phase).toBe("done"));

    // The server's post-drop read: Press leaves `assigned` for `dropped[]`.
    stageServerRead(h, (inputs) => {
      inputs.assigned = inputs.assigned.filter((i) => i.id !== "Press");
      inputs.droppedToday = [
        {
          identity: PRESS_IDENTITY,
          name: "Press",
          droppedAt: "2026-10-16T09:10:00Z",
          source: "todoist",
          minutes: 30,
        },
      ];
    });

    const section = container.querySelector<HTMLElement>("#skipped-today")!;
    expect(section.getAttribute("aria-label")).toBe("Skipped today");
    expect(section.textContent).toContain("Skipped today");
    expect(section.textContent).toContain("1 skipped");
    const skipped = section.querySelector<HTMLElement>(
      `[data-identity="${PRESS_IDENTITY}"]`,
    )!;
    expect(skipped.textContent).toContain("Press");
    expect(skipped.textContent).toContain("30 min");
    const names = Array.from(
      container.querySelectorAll<HTMLElement>(".qrow:not(.trow--skipped) .qrow__name"),
    ).map((n) => n.textContent);
    expect(names).not.toContain("Press");

    // Restore resolves through the drop entry's own identity, not the name.
    const restore = skipped.querySelector<HTMLButtonElement>("button")!;
    expect(restore.textContent).toContain("Restore today");
    expect(restore.classList.contains("trow__btn--restore")).toBe(true);
    fireEvent.click(restore);
    await waitFor(() =>
      expect(calls).toEqual([
        { verb: "restore_today", target: PRESS_IDENTITY },
      ]),
    );
    // The refreshed read carries no drop entry, so the section goes away.
    await waitFor(() =>
      expect(container.querySelector("#skipped-today")).toBeNull(),
    );
  });
});

describe("collapsed row: C and E act on the row that received the key", () => {
  it("C completes the focused row and E takes it out of today's plan locally", async () => {
    const h = makeHarness("ready");
    const calls = spyVerbs(h);
    const batched: Array<{ verb: string; target: string }> = [];
    const applyBatch = h.adapter.applyRuntimeBatch.bind(h.adapter);
    h.adapter.applyRuntimeBatch = async (items) => {
      for (const item of items) batched.push({ verb: item.verb, target: item.target });
      return applyBatch(items);
    };
    const { container } = h.ui(<Queue />);

    const magic = rowFor(container, "Magic Mirror");
    fireEvent.keyDown(magic, { key: "c" });
    // Complete is a journaled batch of one against the stable source identity.
    await waitFor(() =>
      expect(batched).toEqual([{ verb: "done", target: "todoist:6fx00xMIRROR" }]),
    );
    expect(calls).toEqual([]);
    // F1: a row verb is refused while the batch that C started is applying, so
    // the E press below waits for the single batch slot to settle first.
    await waitFor(() => expect(h.store.getState().batch.phase).toBe("done"));

    // U3b: E is LOCAL plan membership for every source kind. The row leaves
    // today's plan in this browser and waits in Not in plan; nothing upstream
    // is written, so the verb spies stay empty.
    fireEvent.keyDown(rowFor(container, "Press"), { key: "e" });
    await waitFor(() =>
      expect(h.store.getState().planMembership).toEqual({
        [PRESS_IDENTITY]: false,
      }),
    );
    expect(calls).toEqual([]);
    expect(batched).toEqual([{ verb: "done", target: "todoist:6fx00xMIRROR" }]);
    await waitFor(() =>
      expect(container.querySelector("#unassigned-today")?.textContent).toContain(
        "Press",
      ),
    );
  });

  it("leaves the key to a control that owns it", async () => {
    const h = makeHarness("ready");
    const calls = spyVerbs(h);
    const { container } = h.ui(<Queue />);
    const press = rowFor(container, "Press");

    // A button owns Enter/Space and its own activation; the row must not
    // double-fire a verb from a keypress that started inside the control.
    fireEvent.keyDown(buttonIn(press, "Complete"), { key: "c" });
    fireEvent.keyDown(press.querySelector(".trow__check")!, { key: "e" });
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(calls).toEqual([]);
  });
});
