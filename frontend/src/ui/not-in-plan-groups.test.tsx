/* not-in-plan-groups.test.tsx — U5: the Not in plan drawer separates the three
   places an unassigned row's state can live, and gives each group exactly one
   correct affordance.

   - local rows keep the Add to plan switch (browser-only membership),
   - journal unassigns get "Restore in Todoist" — one click, because it is an
     undo of that row's own unassign,
   - date-scoped drops keep "Restore today".

   Fixture adapters only: no live adapter, no billed call, no source write. */

import { afterEach, describe, expect, it } from "vitest";
import { act, cleanup, fireEvent, waitFor } from "@testing-library/preact";

import { Queue } from "./Queue";
import { makeHarness, type Harness } from "./test-harness";
import { fingerprintFixedInputs } from "../model/fingerprint";
import { fixedInputsOf } from "../fixtures/scenarios";
import type { AssignedItem, PlanInputs, RuntimeAction } from "../model/types";

afterEach(cleanup);

const NOTE = "Note Processing";
const NOTE_IDENTITY = "capacities:fixture-space:custom-project:note-processing";
const PRESS = "Press";
const PRESS_IDENTITY = "todoist:6fx00xPRESS";

function rowFor(container: Element, name: string): HTMLElement {
  const row = Array.from(container.querySelectorAll<HTMLElement>(".qrow")).find(
    (r) => r.querySelector(".qrow__name")?.textContent === name,
  );
  if (!row) throw new Error(`no row named ${name}`);
  return row;
}

/** The drawer section, by its live id. */
function drawer(container: Element): HTMLElement {
  const section = container.querySelector<HTMLElement>("#unassigned-today");
  if (!section) throw new Error("no Not in plan drawer");
  return section;
}

function groupIn(section: Element, key: string): HTMLElement | null {
  return section.querySelector<HTMLElement>(`[data-group="${key}"]`);
}

function buttonIn(root: Element, label: string): HTMLButtonElement {
  const btn = Array.from(root.querySelectorAll<HTMLButtonElement>("button")).find(
    (b) => b.textContent?.replace(/\s+/g, " ").trim().startsWith(label),
  );
  if (!btn) throw new Error(`no button starting with ${label}`);
  return btn;
}

function stageServerRead(h: Harness, mutate: (inputs: PlanInputs) => void): void {
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
      at: "2026-10-26T09:15:00.000Z",
    });
  });
}

/** The day's default is the U3a rule: a row with no deadline waits in the
    drawer. The fixture day otherwise restores "in", so this is the state a
    cold-plan day runs in. */
function ruleDefaultDay(h: Harness): void {
  act(() => {
    h.store.dispatch({
      type: "SESSION_RESTORED",
      overrides: {},
      placements: {},
      sequence: null,
      fingerprint: null,
      anchoredSourceFingerprint: null,
      planMembership: {},
      membershipDefault: "rule",
    });
  });
}

describe("U5: Removed from Todoist", () => {
  it("lists a journal unassign with one-click Restore in Todoist", async () => {
    const h = makeHarness("ready");
    const reassigned: string[] = [];
    h.adapter.reassign = async (identity: string): Promise<RuntimeAction> => {
      reassigned.push(identity);
      return {
        id: "ra-2",
        verb: "unassign",
        targetName: identity,
        status: "undone",
        error: null,
        duplicate: false,
      };
    };
    const { container } = h.ui(<Queue />);

    stageServerRead(h, (inputs) => {
      inputs.assigned = inputs.assigned.filter((i) => i.id !== PRESS);
      inputs.unassignedToday = [
        {
          identity: PRESS_IDENTITY,
          name: PRESS,
          actionId: "ra-1",
          source: "todoist",
          recurring: false,
          minutes: 90,
        },
      ];
    });

    await waitFor(() => expect(groupIn(drawer(container), "removed-from-todoist")).not.toBeNull());
    const group = groupIn(drawer(container), "removed-from-todoist")!;
    expect(group.textContent).toContain("Removed from Todoist");
    expect(group.textContent).toContain(PRESS);
    expect(group.textContent).toContain("Unassigned in Todoist");
    // The local switch would be a lie here: nothing about this row is local.
    expect(group.querySelector('[role="switch"]')).toBeNull();

    const restore = buttonIn(group, "Restore in Todoist");
    expect(restore.closest(".qrow")).not.toBeNull();
    fireEvent.click(restore);

    // Exactly one reassign, against the row's own stable identity.
    await waitFor(() => expect(reassigned).toEqual([PRESS_IDENTITY]));
    // Not the local plan switch: the sparse membership map stays untouched.
    expect(h.store.getState().planMembership).toEqual({});
  });
});

describe("U5: Skipped today", () => {
  it("lists a date-scoped drop with Restore today and calls the verb once", async () => {
    const h = makeHarness("ready");
    const verbs: Array<[string, string]> = [];
    const real = h.adapter.runtimeAction.bind(h.adapter);
    h.adapter.runtimeAction = async (verb, target, args) => {
      verbs.push([verb, target]);
      return real(verb, target, args);
    };
    const { container } = h.ui(<Queue />);

    stageServerRead(h, (inputs) => {
      inputs.assigned = inputs.assigned.filter((i) => i.id !== NOTE);
      inputs.droppedToday = [
        {
          identity: NOTE_IDENTITY,
          name: NOTE,
          droppedAt: "2026-10-26T09:10:00Z",
          source: "capacities",
          minutes: 30,
        },
      ];
    });

    await waitFor(() => expect(groupIn(drawer(container), "skipped-today")).not.toBeNull());
    const group = groupIn(drawer(container), "skipped-today")!;
    expect(group.textContent).toContain("Skipped today");
    expect(group.textContent).toContain(NOTE);
    // Same chip and same Restore today button as the standalone section.
    expect(group.querySelector(".qrow__src--capacities")?.textContent).toBe("Capacities");
    expect(group.querySelector('[role="switch"]')).toBeNull();

    fireEvent.click(buttonIn(group, "Restore today"));

    await waitFor(() =>
      expect(verbs).toEqual([["restore_today", NOTE_IDENTITY]]),
    );
  });
});

describe("U5: local rows", () => {
  it("keep the Add to plan switch and offer no restore of their own", async () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);

    fireEvent.keyDown(rowFor(container, PRESS), { key: "e" });
    await waitFor(() =>
      expect(h.store.getState().planMembership).toEqual({ [PRESS_IDENTITY]: false }),
    );

    const local = groupIn(drawer(container), "local")!;
    expect(local.textContent).toContain(PRESS);
    expect(local.textContent).toContain("Add to plan");
    expect(local.querySelector('[role="switch"]')).not.toBeNull();
    // No server-held group appears for a purely local row.
    expect(groupIn(drawer(container), "removed-from-todoist")).toBeNull();
    expect(groupIn(drawer(container), "skipped-today")).toBeNull();
    expect(drawer(container).querySelector(".trow__btn--restore")).toBeNull();

    // And the switch still puts it back locally.
    fireEvent.click(local.querySelector<HTMLInputElement>('[role="switch"]')!);
    await waitFor(() => expect(h.store.getState().planMembership).toEqual({}));
    await waitFor(() => expect(container.querySelector("#unassigned-today")).toBeNull());
  });
});

describe("U5: a mid-day arrival", () => {
  const LATE = "Late arrival";
  const LATE_IDENTITY = "todoist:6fx00xLATE";

  function lateArrival(): AssignedItem {
    return {
      id: LATE,
      name: LATE,
      path: null,
      source: "todoist",
      types: ["task"],
      urgency: null,
      deadline: null,
      priorityScore: 10,
      blocks: 1,
      durationLabel: "30min",
      todoistId: "6fx00xLATE",
      identity: LATE_IDENTITY,
    };
  }

  it("wears the new badge; rows already waiting at the first load do not", async () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    ruleDefaultDay(h);

    await waitFor(() => expect(rowFor(drawer(container), PRESS)).not.toBeNull());
    // The day opened with these rows defaulting out: nothing is "new" yet.
    expect(drawer(container).querySelector(".unassigned-today__new")).toBeNull();
    expect(h.store.getState().dayStartIdentities).not.toContain(LATE_IDENTITY);

    stageServerRead(h, (inputs) => {
      inputs.assigned = [...inputs.assigned, lateArrival()];
    });

    await waitFor(() => expect(rowFor(drawer(container), LATE)).not.toBeNull());
    const late = rowFor(drawer(container), LATE);
    expect(late.querySelector(".unassigned-today__new")?.textContent).toBe("new");
    expect(late.querySelector(".unassigned-today__new")?.getAttribute("title")).toBe(
      "Appeared after today's first load",
    );
    // The old rows stay unbadged, and the arrival still carries the switch.
    expect(rowFor(drawer(container), PRESS).querySelector(".unassigned-today__new")).toBeNull();
    expect(late.querySelector('[role="switch"]')).not.toBeNull();
  });
});
