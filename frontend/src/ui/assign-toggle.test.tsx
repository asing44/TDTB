/* assign-toggle.test.tsx — U4/U3b: the per-row plan-membership switch, the
   "Not in plan" drawer, the source unassign staying Todoist-only on Capacities
   rows (KTD4), and the E key taking a row out of today's plan with zero source
   writes. Fixture adapter only; no source write. */

import { afterEach, describe, expect, it } from "vitest";
import { act, cleanup, fireEvent, waitFor } from "@testing-library/preact";

import { Queue } from "./Queue";
import { UNASSIGN_TRIGGER_LABEL } from "./ConfirmUnassign";
import { makeHarness, type Harness } from "./test-harness";
import { fingerprintFixedInputs } from "../model/fingerprint";
import { fixedInputsOf } from "../fixtures/scenarios";
import type { PlanInputs } from "../model/types";

afterEach(cleanup);

const NOTE = "Note Processing";
const NOTE_IDENTITY = "capacities:fixture-space:custom-project:note-processing";

function rowFor(container: Element, name: string): HTMLElement {
  const row = Array.from(container.querySelectorAll<HTMLElement>(".qrow")).find(
    (r) => r.querySelector(".qrow__name")?.textContent === name,
  );
  if (!row) throw new Error(`no row named ${name}`);
  return row;
}

function switchIn(root: Element): HTMLInputElement {
  const sw = root.querySelector<HTMLInputElement>('[role="switch"]');
  if (!sw) throw new Error("no switch");
  return sw;
}

function spyAssign(h: Harness): Array<[string, boolean]> {
  const calls: Array<[string, boolean]> = [];
  h.controller.setAssigned = async (id: string, assigned: boolean) => {
    calls.push([id, assigned]);
  };
  return calls;
}

/** Every write shape a row could reach: the journaled batch wire and the
    single-row verb wire. U3b's keyboard path must produce neither. */
function spySources(h: Harness): string[] {
  const calls: string[] = [];
  const batch = h.adapter.applyRuntimeBatch.bind(h.adapter);
  h.adapter.applyRuntimeBatch = async (items) => {
    for (const item of items) calls.push(`batch:${item.verb}`);
    return batch(items);
  };
  const action = h.adapter.runtimeAction.bind(h.adapter);
  h.adapter.runtimeAction = async (verb, target, args) => {
    calls.push(`verb:${verb}`);
    return action(verb, target, args);
  };
  return calls;
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
      at: "2026-10-19T09:15:00.000Z",
    });
  });
}

describe("the plan-membership switch on a row", () => {
  it("is a switch, in-plan for a band row, and calls setAssigned(id, false)", () => {
    const h = makeHarness("ready");
    const calls = spyAssign(h);
    const { container } = h.ui(<Queue />);
    const press = rowFor(container, "Press");
    const sw = switchIn(press);
    expect(sw.getAttribute("role")).toBe("switch");
    expect(sw.getAttribute("aria-checked")).toBe("true");
    expect(sw.getAttribute("aria-label")).toBe("In today's plan: Press");
    expect(sw.disabled).toBe(false);
    fireEvent.click(sw);
    expect(calls).toEqual([["Press", false]]);
  });

  it("shows no visible caption on an in-plan row; the state rides aria-label and title", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    const press = rowFor(container, "Press");
    expect(press.querySelector(".trow__assign-text")).toBeNull();
    expect(press.textContent).not.toMatch(/In today's plan/);
    expect(press.querySelector(".trow__assign")?.getAttribute("title")).toBe(
      "In today's plan",
    );
  });

  it("has no source unassign on a Capacities row but keeps it on a Todoist row", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    // U4: the Todoist row's source verb is the double-confirmed unassign; a
    // Capacities row has none at all and the switch owns its drop instead.
    const unassign = (row: HTMLElement) =>
      row.querySelector(`button[aria-label="${UNASSIGN_TRIGGER_LABEL}"]`);
    expect(unassign(rowFor(container, NOTE))).toBeNull();
    expect(switchIn(rowFor(container, NOTE))).not.toBeNull();
    expect(unassign(rowFor(container, "Press"))).not.toBeNull();
  });

  it("is disabled with the Day Setup reason announced before confirmation", () => {
    const h = makeHarness("fresh");
    const { container } = h.ui(<Queue />);
    const press = rowFor(container, "Press");
    const sw = switchIn(press);
    expect(sw.disabled).toBe(true);
    const describedBy = sw.getAttribute("aria-describedby")!;
    expect(describedBy).toBeTruthy();
    const why = container.querySelector<HTMLElement>(`#${describedBy}`)!;
    expect(why.textContent).toMatch(/Day Setup/);
  });
});

describe("the Not in plan drawer", () => {
  it("lists a held-back Capacities row with its source chip and a Restore today control", async () => {
    const h = makeHarness("ready");
    const restored: string[] = [];
    h.controller.restoreToday = async (id: string) => {
      restored.push(id);
    };
    const { container } = h.ui(<Queue />);
    expect(container.querySelector("#unassigned-today")).toBeNull();

    stageServerRead(h, (inputs) => {
      inputs.assigned = inputs.assigned.filter((i) => i.id !== NOTE);
      inputs.droppedToday = [
        {
          identity: NOTE_IDENTITY,
          name: NOTE,
          droppedAt: "2026-10-19T09:10:00Z",
          source: "capacities",
          minutes: 30,
        },
      ];
    });

    await waitFor(() =>
      expect(container.querySelector("#unassigned-today")).not.toBeNull(),
    );
    const section = container.querySelector<HTMLElement>("#unassigned-today")!;
    expect(section.textContent).toContain(NOTE);
    expect(section.textContent).toContain("Not in plan");
    expect(section.textContent).toContain("1 not in plan");
    // The copy never implies that anything upstream changes.
    expect(section.textContent).toContain("changes nothing in Todoist");
    expect(section.querySelector(".qrow__src--capacities")?.textContent).toBe(
      "Capacities",
    );
    // Same column as the task list.
    expect(section.closest(".plan-grid__groups")).not.toBeNull();
    // U5: a `dropped[]` row is not local plan membership, so it lands in the
    // drawer's "Skipped today" group with the shared Restore today button.
    // The Add to plan switch belongs to local rows only.
    expect(
      section.querySelector('[data-group="skipped-today"] .qrow__name')
        ?.textContent,
    ).toBe(NOTE);
    expect(section.querySelector(".trow__assign-text")).toBeNull();
    expect(section.querySelector('[role="switch"]')).toBeNull();
    const restore = section.querySelector<HTMLButtonElement>(
      "button.trow__btn--restore",
    )!;
    expect(restore.textContent).toContain("Restore today");
    expect(restore.disabled).toBe(false);
    fireEvent.click(restore);
    expect(restored).toEqual([NOTE_IDENTITY]);
  });

  it("renders nothing when every sourced row is in today's plan", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    expect(container.querySelector("#unassigned-today")).toBeNull();
  });
});

describe("E takes a row out of today's plan locally", () => {
  it("moves a Todoist row into Not in plan with zero source writes", async () => {
    const h = makeHarness("ready");
    const writes = spySources(h);
    const { container } = h.ui(<Queue />);

    fireEvent.keyDown(rowFor(container, "Press"), { key: "e" });

    // Local plan membership only: no batch, no runtime verb, no adapter call.
    await waitFor(() =>
      expect(h.store.getState().planMembership).toEqual({
        "todoist:6fx00xPRESS": false,
      }),
    );
    expect(writes).toEqual([]);

    const section = container.querySelector<HTMLElement>("#unassigned-today")!;
    expect(section).not.toBeNull();
    expect(section.textContent).toContain("Press");
    const sw = switchIn(section);
    expect(sw.getAttribute("aria-checked")).toBe("false");
    expect(sw.getAttribute("aria-label")).toBe("Add to plan: Press");
    // The row left the bands; only the drawer carries it now.
    expect(
      Array.from(
        container.querySelectorAll<HTMLElement>(
          ".qrow:not(.trow--unassigned) .qrow__name",
        ),
      ).map((n) => n.textContent),
    ).not.toContain("Press");

    // Putting it back is local too, and the drawer empties itself. The map
    // stays sparse (U1): an entry that agrees with the day's default is
    // dropped rather than stored, so re-adding leaves it empty again.
    fireEvent.click(sw);
    await waitFor(() =>
      expect(h.store.getState().planMembership).toEqual({}),
    );
    await waitFor(() =>
      expect(container.querySelector("#unassigned-today")).toBeNull(),
    );
    expect(rowFor(container, "Press")).not.toBeNull();
    expect(writes).toEqual([]);
  });

  it("answers E the same way on a Capacities row", async () => {
    const h = makeHarness("ready");
    const writes = spySources(h);
    const { container } = h.ui(<Queue />);

    fireEvent.keyDown(rowFor(container, NOTE), { key: "e" });

    await waitFor(() =>
      expect(h.store.getState().planMembership).toEqual({
        [NOTE_IDENTITY]: false,
      }),
    );
    expect(writes).toEqual([]);
    expect(container.querySelector("#unassigned-today")?.textContent).toContain(
      NOTE,
    );
  });

  it("keeps C writing to the source for a Todoist row", async () => {
    const h = makeHarness("ready");
    const writes = spySources(h);
    const { container } = h.ui(<Queue />);

    fireEvent.keyDown(rowFor(container, "Press"), { key: "c" });

    await waitFor(() => expect(writes).toEqual(["batch:done"]));
  });
});
