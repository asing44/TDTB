/* task-row-source-unassign.test.tsx — U4: the Todoist row's source unassign is
   renamed and DOUBLE-CONFIRMED.

   The old "Skip today" row button was never local: `runRowBatch(id,
   "unassign")` clears the task's due date in Todoist (a recurring task's due
   occurrence advances without completing). So the control must be:

   - one click → the confirm panel only, with ZERO adapter calls;
   - the panel names exactly what changes in Todoist;
   - its own labelled confirm → exactly one `unassign` for that identity, with
     every other adapter entry point untouched;
   - Cancel, Escape, and focus leaving the control → zero calls;
   - Todoist only: a Capacities or calendar row has no such control at all, so
     no Capacities write can be reached from here.

   Fixture adapters only: no live adapter, no billed call, no source write. */

import { afterEach, describe, expect, it } from "vitest";
import { act, cleanup, fireEvent, waitFor } from "@testing-library/preact";

import { Queue } from "./Queue";
import { makeHarness, type Harness } from "./test-harness";
import {
  SKIP_OCCURRENCE_CONFIRM_LABEL,
  SKIP_OCCURRENCE_TRIGGER_LABEL,
  UNASSIGN_CONFIRM_LABEL,
  UNASSIGN_TRIGGER_LABEL,
} from "./ConfirmUnassign";
import type { AssignedItem } from "../model/types";

afterEach(cleanup);

const PRESS_IDENTITY = "todoist:6fx00xPRESS";

function rowFor(container: Element, name: string): HTMLElement {
  const row = Array.from(container.querySelectorAll<HTMLElement>(".qrow")).find(
    (r) => r.querySelector(".qrow__name")?.textContent === name,
  );
  if (!row) throw new Error(`no row named ${name}`);
  return row;
}

function triggerIn(root: Element, label = UNASSIGN_TRIGGER_LABEL): HTMLButtonElement {
  const btn = root.querySelector<HTMLButtonElement>(`button[aria-label="${label}"]`);
  if (!btn) throw new Error(`no unassign control named ${label}`);
  return btn;
}

function panelIn(root: Element): HTMLElement | null {
  return root.querySelector<HTMLElement>(".unassign__panel");
}

function buttonWithText(root: Element, text: string): HTMLButtonElement {
  const btn = Array.from(root.querySelectorAll<HTMLButtonElement>("button")).find(
    (b) => (b.textContent ?? "").trim() === text,
  );
  if (!btn) throw new Error(`no button reading ${text}`);
  return btn;
}

/** Both write surfaces: the immediate runtime verbs and the journaled batch
    wire the row's unassign rides (`runRowBatch`). */
function spyAdapter(h: Harness): {
  verbs: Array<{ verb: string; target: string }>;
  batched: Array<{ verb: string; target: string }>;
} {
  const verbs: Array<{ verb: string; target: string }> = [];
  const batched: Array<{ verb: string; target: string }> = [];
  const action = h.adapter.runtimeAction.bind(h.adapter);
  h.adapter.runtimeAction = async (verb, target, args) => {
    verbs.push({ verb, target });
    return action(verb, target, args);
  };
  const batch = h.adapter.applyRuntimeBatch.bind(h.adapter);
  h.adapter.applyRuntimeBatch = async (items) => {
    for (const item of items) batched.push({ verb: item.verb, target: item.target });
    return batch(items);
  };
  return { verbs, batched };
}

function addRow(h: Harness, row: AssignedItem): void {
  const inputs = structuredClone(h.store.getState().inputs!);
  inputs.assigned.push(row);
  h.store.dispatch({
    type: "INPUTS_LOADED",
    inputs,
    ledger: { today: inputs.validDate, spent: 0, cap: 5, remaining: 5 },
  });
}

describe("the row's source unassign: one click opens the confirm, nothing else", () => {
  it("names Todoist on the control and writes nothing on the first click", () => {
    const h = makeHarness("ready");
    const spy = spyAdapter(h);
    const { container } = h.ui(<Queue />);

    const trigger = triggerIn(rowFor(container, "Press"));
    expect(trigger.textContent).toBe(UNASSIGN_TRIGGER_LABEL);
    expect(trigger.getAttribute("aria-expanded")).toBe("false");
    expect(panelIn(rowFor(container, "Press"))).toBeNull();

    fireEvent.click(trigger);

    const press = rowFor(container, "Press");
    const panel = panelIn(press)!;
    expect(panel).toBeTruthy();
    expect(triggerIn(press).getAttribute("aria-expanded")).toBe("true");
    // The panel states the exact source change, in Todoist's own terms.
    expect(panel.textContent).toContain(`Unassign “Press” in Todoist?`);
    expect(panel.textContent).toContain("Clears this task's due date in Todoist");
    expect(panel.textContent).toContain("Nothing is saved until you confirm");
    expect(buttonWithText(panel, UNASSIGN_CONFIRM_LABEL)).toBeTruthy();
    expect(buttonWithText(panel, "Cancel")).toBeTruthy();

    // Zero calls of either shape: the confirm is the only door to the adapter.
    expect(spy.verbs).toEqual([]);
    expect(spy.batched).toEqual([]);
    expect(h.store.getState().batch.phase).toBe("idle");
  });

  it("disables the control with the written Day Setup reason", () => {
    const h = makeHarness("fresh");
    const { container } = h.ui(<Queue />);
    const press = rowFor(container, "Press");
    const trigger = triggerIn(press);

    expect(trigger.disabled).toBe(true);
    expect(trigger.getAttribute("aria-describedby")).toBe("why-unassign-Press");
    expect(press.querySelector("#why-unassign-Press")?.textContent).toMatch(
      /Day Setup/,
    );
    // A disabled control cannot open the confirm at all.
    fireEvent.click(trigger);
    expect(panelIn(press)).toBeNull();
  });
});

describe("the row's source unassign: the second click is the only write", () => {
  it("sends exactly one unassign for that row, through the batch-of-one path", async () => {
    const h = makeHarness("ready");
    const spy = spyAdapter(h);
    const { container } = h.ui(<Queue />);

    fireEvent.click(triggerIn(rowFor(container, "Press")));
    fireEvent.click(buttonWithText(panelIn(rowFor(container, "Press"))!, UNASSIGN_CONFIRM_LABEL));

    await waitFor(() =>
      expect(spy.batched).toEqual([
        { verb: "unassign", target: PRESS_IDENTITY },
      ]),
    );
    // Exactly one: no duplicate on a second activation, no immediate verb.
    expect(spy.verbs).toEqual([]);
    expect(spy.batched).toHaveLength(1);
    // The panel closes on confirm; the row is a source change, not a local
    // plan-membership edit, so no browser-only override is written.
    expect(panelIn(container)).toBeNull();
    expect(h.store.getState().overrides["Press"]).toBeUndefined();
  });

  it("says Skip occurrence, and sends the same unassign verb, on a recurring row", async () => {
    const h = makeHarness("ready");
    addRow(h, {
      id: "LOOTS",
      name: "LOOTS",
      path: null,
      source: "todoist",
      types: ["todoist"],
      urgency: null,
      deadline: h.store.getState().inputs!.validDate,
      priorityScore: 1,
      blocks: 1,
      durationLabel: "30min",
      todoistId: "loots-1",
      isRecurring: true,
      scheduledStart: "12:30",
      labels: [],
    });
    const spy = spyAdapter(h);
    const { container } = h.ui(<Queue />);

    const loots = rowFor(container, "LOOTS");
    expect(triggerIn(loots, SKIP_OCCURRENCE_TRIGGER_LABEL).textContent).toBe(
      SKIP_OCCURRENCE_TRIGGER_LABEL,
    );
    fireEvent.click(triggerIn(loots, SKIP_OCCURRENCE_TRIGGER_LABEL));
    const panel = panelIn(rowFor(container, "LOOTS"))!;
    expect(panel.textContent).toContain(
      `Skip this occurrence of “LOOTS” in Todoist?`,
    );
    expect(panel.textContent).toContain("without completing it");
    fireEvent.click(buttonWithText(panel, SKIP_OCCURRENCE_CONFIRM_LABEL));
    await waitFor(() =>
      expect(spy.batched).toEqual([{ verb: "unassign", target: "todoist:loots-1" }]),
    );
  });
});

describe("the row's source unassign: every refusal closes with zero calls", () => {
  it("Cancel closes the panel and writes nothing", () => {
    const h = makeHarness("ready");
    const spy = spyAdapter(h);
    const { container } = h.ui(<Queue />);

    fireEvent.click(triggerIn(rowFor(container, "Press")));
    fireEvent.click(buttonWithText(panelIn(rowFor(container, "Press"))!, "Cancel"));

    expect(panelIn(container)).toBeNull();
    expect(spy.verbs).toEqual([]);
    expect(spy.batched).toEqual([]);
  });

  it("Escape closes the panel, returns focus, and writes nothing", () => {
    const h = makeHarness("ready");
    const spy = spyAdapter(h);
    const { container } = h.ui(<Queue />);

    fireEvent.click(triggerIn(rowFor(container, "Press")));
    const panel = panelIn(rowFor(container, "Press"))!;
    fireEvent.keyDown(panel, { key: "Escape" });

    expect(panelIn(container)).toBeNull();
    expect(document.activeElement).toBe(triggerIn(rowFor(container, "Press")));
    expect(spy.verbs).toEqual([]);
    expect(spy.batched).toEqual([]);
  });

  it("closes when focus leaves the control, with zero calls", async () => {
    const h = makeHarness("ready");
    const spy = spyAdapter(h);
    const { container } = h.ui(<Queue />);

    fireEvent.click(triggerIn(rowFor(container, "Press")));
    const panel = panelIn(rowFor(container, "Press"))!;
    const elsewhere = rowFor(container, "Magic Mirror").querySelector(".trow__check")!;
    act(() => {
      panel.dispatchEvent(
        new FocusEvent("focusout", { bubbles: true, relatedTarget: elsewhere }),
      );
    });

    await waitFor(() => expect(panelIn(container)).toBeNull());
    expect(spy.verbs).toEqual([]);
    expect(spy.batched).toEqual([]);
  });
});

describe("the row's source unassign is Todoist only", () => {
  it("gives a Capacities row no control at all and keeps the slot reserved", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    const note = rowFor(container, "Note Processing");

    expect(
      note.querySelector(`button[aria-label="${UNASSIGN_TRIGGER_LABEL}"]`),
    ).toBeNull();
    // The empty slot keeps Complete and Expand in their columns.
    expect(note.querySelectorAll(".trow__acts > .trow__slot")).toHaveLength(1);
    // The assign switch is what takes a Capacities row out of today.
    expect(note.querySelector(".trow__switch")).toBeTruthy();
  });

  it("gives a calendar row no control at all", () => {
    const h = makeHarness("ready");
    addRow(h, {
      id: "PHEP sync (Vlad)",
      name: "PHEP sync (Vlad)",
      path: null,
      source: "calendar",
      types: ["calendar"],
      urgency: null,
      deadline: null,
      priorityScore: 30,
      blocks: 2,
      durationLabel: "45min",
      todoistId: null,
    });
    const { container } = h.ui(<Queue />);
    const phelp = rowFor(container, "PHEP sync (Vlad)");

    expect(
      phelp.querySelector(`button[aria-label="${UNASSIGN_TRIGGER_LABEL}"]`),
    ).toBeNull();
    expect(phelp.querySelector(".unassign__panel")).toBeNull();
    expect(phelp.querySelectorAll(".trow__acts > .trow__slot")).toHaveLength(1);
  });
});
