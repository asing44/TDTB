/* capacities-complete-skip.test.tsx — Complete on a Capacities row is Skip
   today.

   A Capacities object has no completable source: the server's `done` verb
   refuses it with "no completable source in today's manifest". Completing a
   Capacities row therefore resolves to Skip today's durable `drop_from_plan`
   path — the row leaves today's plan, lands in Skipped today, and Capacities
   itself is never written. Todoist (and every other source) keeps the exact
   `done` behavior.

   Fixture adapters only: no live adapter, no billed call, no source write. The
   drop projection is the server's read, so the round trip publishes it the way
   task-row-actions.test.tsx does. */

import { afterEach, describe, expect, it } from "vitest";
import { act, cleanup, fireEvent, waitFor } from "@testing-library/preact";

import { Queue } from "./Queue";
import { BatchPanel } from "./BatchActions";
import { makeHarness, type Harness } from "./test-harness";
import { fingerprintFixedInputs } from "../model/fingerprint";
import { fixedInputsOf } from "../fixtures/scenarios";
import type { PlanInputs } from "../model/types";

afterEach(cleanup);

/** The fixture's one Capacities object, and its stable identity. */
const NOTE = "Note Processing";
const NOTE_IDENTITY = "capacities:fixture-space:custom-project:note-processing";
/** A Todoist row, for the "nothing else changed" half of every assertion. */
const PRESS = "Press";
const PRESS_IDENTITY = "todoist:6fx00xPRESS";

const CAPACITIES_NOTE =
  "Done for today — skips this Capacities item for today; Capacities is not updated";

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

/** Observable batch wire: what each item would send to the server. */
function spyBatches(h: Harness): Array<{ verb: string; target: string }> {
  const sent: Array<{ verb: string; target: string }> = [];
  const real = h.adapter.applyRuntimeBatch.bind(h.adapter);
  h.adapter.applyRuntimeBatch = async (items) => {
    for (const item of items) sent.push({ verb: item.verb, target: item.target });
    return real(items);
  };
  return sent;
}

/** Single-row verbs ride the journaled batch wire, so a `runtimeAction` call
    would mean the wrong path was taken. */
function spyActions(h: Harness): Array<{ verb: string; target: string }> {
  const calls: Array<{ verb: string; target: string }> = [];
  const real = h.adapter.runtimeAction.bind(h.adapter);
  h.adapter.runtimeAction = async (verb, target, args) => {
    calls.push({ verb, target });
    return real(verb, target, args);
  };
  return calls;
}

/** Publish the plan-inputs read the server would return after the skip. */
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

describe("Complete on a Capacities row skips it for today", () => {
  it("sends drop_from_plan for the row's Complete button, never done", async () => {
    const h = makeHarness("ready");
    const sent = spyBatches(h);
    const actions = spyActions(h);
    const { container } = h.ui(<Queue />);

    fireEvent.click(buttonIn(rowFor(container, NOTE), "Complete"));

    await waitFor(() =>
      expect(sent).toEqual([{ verb: "drop_from_plan", target: NOTE_IDENTITY }]),
    );
    // The row verb path (batch of one) is the one Skip today already uses.
    expect(actions).toEqual([]);
    await waitFor(() => expect(h.store.getState().batch.phase).toBe("done"));
    expect(h.store.getState().batch.results.map((r) => r.verb)).toEqual([
      "drop_from_plan",
    ]);
    // The review says what the button did, in words.
    expect(h.store.getState().batch.review?.effects).toBe(
      "Skip today for 1 Capacities row (Capacities is not updated)",
    );
  });

  it("sets the row Unassigned through the C keyboard shortcut (KTD4)", async () => {
    const h = makeHarness("ready");
    const actions = spyActions(h);
    const sent = spyBatches(h);
    const { container } = h.ui(<Queue />);

    fireEvent.keyDown(rowFor(container, NOTE), { key: "c" });

    // C routes through setAssigned(false). U3a made that a LOCAL plan-membership
    // edit for a row the queue still carries, so nothing upstream is written:
    // no date-scoped drop, no batch, and Capacities is never touched.
    await waitFor(() =>
      expect(h.store.getState().planMembership).toEqual({ [NOTE_IDENTITY]: false }),
    );
    expect(actions).toEqual([]);
    expect(sent).toEqual([]);
    // The row waits in the Unassigned section, out of every band.
    await waitFor(() =>
      expect(container.querySelector("#unassigned-today")?.textContent).toContain(NOTE),
    );
  });

  it("leaves today's plan for the Unassigned section, and Re-assign brings it back", async () => {
    const h = makeHarness("ready");
    const sent = spyBatches(h);
    const actions = spyActions(h);
    const { container } = h.ui(<Queue />);

    fireEvent.click(buttonIn(rowFor(container, NOTE), "Complete"));
    await waitFor(() => expect(sent).toHaveLength(1));
    await waitFor(() => expect(h.store.getState().batch.phase).toBe("done"));
    // A skip is durable and date-scoped: never a browser-only exclusion.
    expect(h.store.getState().overrides[NOTE]).toBeUndefined();

    // The server's post-skip read moves the row into `dropped[]`.
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
    expect(section.textContent).toContain("Unassigned");
    expect(section.textContent).toContain("1 unassigned");
    expect(section.textContent).toContain("Capacities");
    // KTD4: Skipped today carries Todoist drops only.
    expect(container.querySelector("#skipped-today")).toBeNull();
    expect(section.textContent).toContain(NOTE);
    const names = Array.from(
      container.querySelectorAll<HTMLElement>(".qrow:not(.trow--skipped) .qrow__name"),
    ).map((n) => n.textContent);
    expect(names).not.toContain(NOTE);

    const reassign = section.querySelector<HTMLInputElement>('[role="switch"]')!;
    expect(reassign.getAttribute("aria-checked")).toBe("false");
    fireEvent.click(reassign);
    await waitFor(() =>
      expect(actions).toEqual([
        { verb: "restore_today", target: NOTE_IDENTITY },
      ]),
    );
  });

  it("splits a mixed batch: done for Todoist, drop_from_plan for Capacities", async () => {
    const h = makeHarness("ready");
    const sent = spyBatches(h);
    const { store, controller } = h;
    const { container } = h.ui(<BatchPanel />);

    act(() => {
      for (const id of [PRESS, NOTE]) controller.selectRow(id, true);
      controller.reviewBatch("complete");
    });

    const review = store.getState().batch.review!;
    expect(review.label).toBe("2 of 2 eligible");
    expect(review.excluded).toEqual([]);
    expect(review.effects).toBe(
      "Complete 1 row; Skip today for 1 Capacities row (Capacities is not updated)",
    );
    // The review names each row's real effect, not the clicked verb.
    const effects = container.querySelector<HTMLElement>(
      '[data-testid="batch-effects"]',
    )!;
    expect(effects.textContent).toContain("Mark done in Todoist");
    expect(effects.textContent).toContain(
      "Skip today (Capacities is not updated)",
    );

    await controller.applyBatch();

    expect(sent).toEqual([
      { verb: "done", target: PRESS_IDENTITY },
      { verb: "drop_from_plan", target: NOTE_IDENTITY },
    ]);
  });

  it("keeps done for a non-Capacities row", async () => {
    const h = makeHarness("ready");
    const sent = spyBatches(h);
    const { container } = h.ui(<Queue />);

    fireEvent.click(buttonIn(rowFor(container, PRESS), "Complete"));

    await waitFor(() =>
      expect(sent).toEqual([{ verb: "done", target: PRESS_IDENTITY }]),
    );
    await waitFor(() => expect(h.store.getState().batch.phase).toBe("done"));
    expect(h.store.getState().batch.review?.effects).toBe("Complete 1 row");
  });
});

describe("the Capacities Complete control names its real effect", () => {
  it("keeps the visible label and explains the skip in title and description", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    const note = rowFor(container, NOTE);

    const complete = buttonIn(note, "Complete");
    expect(complete.textContent).toBe("Complete");
    expect(complete.getAttribute("aria-label")).toBe("Complete");
    expect(complete.getAttribute("title")).toBe(CAPACITIES_NOTE);
    const describedBy = complete.getAttribute("aria-describedby");
    expect(describedBy).toBeTruthy();
    expect(note.querySelector(`#${describedBy}`)?.textContent).toBe(
      CAPACITIES_NOTE,
    );

    // A Todoist row is untouched: no skip wording anywhere on it.
    const pressComplete = buttonIn(rowFor(container, PRESS), "Complete");
    expect(pressComplete.getAttribute("aria-label")).toBe("Complete");
    expect(pressComplete.getAttribute("title") ?? "").toBe("");
    expect(pressComplete.getAttribute("aria-describedby")).toBeNull();
  });

  it("still says why the verb is gated before Day Setup", () => {
    const h = makeHarness("fresh");
    const { container } = h.ui(<Queue />);
    const note = rowFor(container, NOTE);
    const complete = buttonIn(note, "Complete");
    expect(complete.disabled).toBe(true);
    expect(complete.getAttribute("title")).toBe("Confirm Day Setup first");
    // The gate reason leads the description; the skip note still follows it.
    const describedBy = complete.getAttribute("aria-describedby")!.split(" ");
    expect(note.querySelector(`#${describedBy[0]}`)?.textContent).toBe(
      "Confirm Day Setup first",
    );
    expect(note.querySelector(`#${describedBy[1]}`)?.textContent).toBe(
      CAPACITIES_NOTE,
    );
  });
});
