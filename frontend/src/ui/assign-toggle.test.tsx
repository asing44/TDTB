/* assign-toggle.test.tsx — U4: the per-row Assigned/Unassigned switch, the
   Unassigned section, and Skip today going away on Capacities rows (KTD4).
   Fixture adapter only; no source write. */

import { afterEach, describe, expect, it } from "vitest";
import { act, cleanup, fireEvent, waitFor } from "@testing-library/preact";

import { Queue } from "./Queue";
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

describe("the Assigned switch on a row", () => {
  it("is a switch, on for an assigned row, and calls setAssigned(id, false)", () => {
    const h = makeHarness("ready");
    const calls = spyAssign(h);
    const { container } = h.ui(<Queue />);
    const press = rowFor(container, "Press");
    const sw = switchIn(press);
    expect(sw.getAttribute("role")).toBe("switch");
    expect(sw.getAttribute("aria-checked")).toBe("true");
    expect(sw.getAttribute("aria-label")).toBe("Assigned: Press");
    expect(sw.disabled).toBe(false);
    fireEvent.click(sw);
    expect(calls).toEqual([["Press", false]]);
  });

  it("has no Skip today on a Capacities row but keeps it on a Todoist row", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    const skip = (row: HTMLElement) => row.querySelector('button[aria-label="Skip today"]');
    expect(skip(rowFor(container, NOTE))).toBeNull();
    expect(switchIn(rowFor(container, NOTE))).not.toBeNull();
    expect(skip(rowFor(container, "Press"))).not.toBeNull();
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

describe("the Unassigned section", () => {
  it("lists a held-back Capacities row with its source chip and a switch to re-assign", async () => {
    const h = makeHarness("ready");
    const calls = spyAssign(h);
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
    expect(section.querySelector(".qrow__src--capacities")?.textContent).toBe(
      "Capacities",
    );
    const sw = switchIn(section);
    expect(sw.getAttribute("aria-checked")).toBe("false");
    fireEvent.click(sw);
    expect(calls).toEqual([[NOTE_IDENTITY, true]]);
  });
});
