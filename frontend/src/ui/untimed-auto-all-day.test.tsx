/* untimed-auto-all-day.test.tsx — A1 untimed lane, A3: say WHY a row went
   untimed. The server auto-classifies a date-only quick check-in into the
   existing all-day state (blocks === 0) and stamps `untimed_auto: true` plus
   `untimed_reason`. The row keeps the `All-day` state label, reads "Auto
   all-day" while the label is not the operator's own choice, and carries the
   server's reason in its native tooltip with the hint that a stated duration
   schedules the row (the escape hatch that beats the automatic rule).

   A manual all-day row (issue #17 "Keep all-day", or the slider's left end)
   carries no annotation and must keep reading plain "All-day" with no
   auto tooltip. Fixture adapters only: no live adapter, no billed call. */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent } from "@testing-library/preact";

afterEach(cleanup);

import { Queue } from "./Queue";
import { makeHarness } from "./test-harness";
import { effectiveBlocks } from "../store/store";
import { projectAssigned, shapeAssignedWire } from "../adapters/wire";
import type { AssignedItem } from "../model/types";

const AUTO = "Auto untimed check-in";
const MANUAL = "Manual all-day";
const REASON = "check-in name: \\bfu\\b";

function rowFor(container: Element, name: string): HTMLElement {
  const row = Array.from(container.querySelectorAll<HTMLElement>(".qrow")).find(
    (r) => r.querySelector(".qrow__name")?.textContent === name,
  );
  if (!row) throw new Error(`no row named ${name}`);
  return row;
}

const stateOf = (container: Element, name: string) =>
  rowFor(container, name).querySelector<HTMLElement>(".trow__state")!;

/** A Todoist base item plus the two rows under test: one the server classified
    as untimed (blocks 0 + the annotations), one all-day by hand (no
    annotations). */
function stage(h: ReturnType<typeof makeHarness>) {
  const inputs = structuredClone(h.store.getState().inputs!);
  const base = inputs.assigned.find((item) => item.source === "todoist")!;
  const auto: AssignedItem = {
    ...base,
    id: AUTO,
    name: AUTO,
    todoistId: "t-auto-untimed",
    scheduledStart: null,
    blocks: 0,
    durationLabel: "All day",
    untimedAuto: true,
    untimedReason: REASON,
  };
  const manual: AssignedItem = {
    ...base,
    id: MANUAL,
    name: MANUAL,
    todoistId: "t-manual-allday",
    scheduledStart: null,
    blocks: 0,
    durationLabel: "All day",
  };
  inputs.assigned = [...inputs.assigned, auto, manual];
  h.store.dispatch({
    type: "INPUTS_LOADED",
    inputs,
    ledger: { today: inputs.validDate, spent: 0, cap: 7, remaining: 7 },
  });
  return { auto, manual };
}

describe("auto all-day (untimed lane) rows", () => {
  it("labels the state Auto all-day and carries the server's reason plus the hint", () => {
    const h = makeHarness("ready");
    stage(h);
    const { container } = h.ui(<Queue />);

    const state = stateOf(container, AUTO);
    expect(state.textContent).toBe("Auto all-day");
    const title = state.getAttribute("title") ?? "";
    expect(title).toContain(REASON);
    expect(title).toContain("Set a duration to schedule it");
  });

  it("leaves a hand-made all-day row reading plain All-day with no auto tooltip", () => {
    const h = makeHarness("ready");
    stage(h);
    const { container } = h.ui(<Queue />);

    const state = stateOf(container, MANUAL);
    expect(state.textContent).toBe("All-day");
    expect(state.getAttribute("title")).toBeNull();
  });

  it("drops the marker once a duration is stated — the escape hatch wins", () => {
    const h = makeHarness("ready");
    stage(h);
    const { container } = h.ui(<Queue />);
    expect(stateOf(container, AUTO).textContent).toBe("Auto all-day");

    const more = rowFor(container, AUTO).querySelector<HTMLButtonElement>(
      'button[aria-label^="15 minutes more"]',
    )!;
    fireEvent.click(more);
    fireEvent.click(more);

    expect(effectiveBlocks(h.store.getState(), AUTO)).toBe(1);
    const state = stateOf(container, AUTO);
    expect(state.textContent).toBe("Automatic start");
    // Cleared on re-render (Preact leaves the attribute empty): the reason is
    // gone from the tooltip, not merely reworded.
    expect(state.getAttribute("title") ?? "").not.toContain(REASON);
    // The annotation itself is the server's, so the item keeps it; only the
    // all-day state is gone.
    expect(
      h.store.getState().inputs!.assigned.find((i) => i.id === AUTO)?.untimedAuto,
    ).toBe(true);
  });

  it("never consults a billed or write path for the marker", async () => {
    const h = makeHarness("ready");
    stage(h);
    const spies = {
      sequence: vi.spyOn(h.adapter, "autoSequence"),
      shadow: vi.spyOn(h.adapter, "shadowCommit"),
      live: vi.spyOn(h.adapter, "liveCommit"),
    };
    h.ui(<Queue />);
    await new Promise((resolve) => setTimeout(resolve, 150));
    for (const spy of Object.values(spies)) expect(spy).not.toHaveBeenCalled();
  });
});

describe("untimed annotations across the wire", () => {
  const ROW = {
    name: AUTO,
    todoist_id: "t-auto-untimed",
    path: "todoist://t-auto-untimed",
    source: "todoist",
    blocks: 0,
    duration: 10,
    untimed_auto: true,
    untimed_reason: REASON,
  };

  it("projects the server's annotation onto the assigned item", () => {
    const item = projectAssigned(ROW);
    expect(item.untimedAuto).toBe(true);
    expect(item.untimedReason).toBe(REASON);
  });

  it("projects an unannotated row without an auto marker", () => {
    const item = projectAssigned({ ...ROW, untimed_auto: undefined, untimed_reason: undefined });
    expect(item.untimedAuto).toBe(false);
    expect(item.untimedReason).toBeNull();
  });

  it("never sends the annotation back as a client claim", () => {
    const [shaped] = shapeAssignedWire([ROW], [{ id: AUTO, blocks: 0 }]);
    expect(shaped).not.toHaveProperty("untimed_auto");
    expect(shaped).not.toHaveProperty("untimed_reason");
    expect(shaped.blocks).toBe(0);
  });
});
