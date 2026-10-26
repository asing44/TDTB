/* batch-applying-guard.test.tsx — F1 from the task-actions independent
   review: one source-changing batch owns the single batch slot while its wire
   call is in flight, so no second row verb may start behind it.

   Covered here (and only here):
   - while `applyRuntimeBatch` is pending, the row's Complete and Unassign in
     Todoist… buttons are disabled and carry the written reason, and TaskRow,
     SkippedToday, SourceMenu and the controller all read that same reason;
   - a Complete click, an Unassign in Todoist… click, the C and E shortcuts, and
     direct `runRowBatch` / `skipToday` calls make ZERO additional adapter
     calls and cannot replace the applying batch with a second BATCH_SET;
   - once the pending call resolves, the verbs work again.

   Fixture adapter only: one deferred fixture call, no live adapter, no billed
   call, no source write. */

import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, waitFor } from "@testing-library/preact";

import { Queue } from "./Queue";
import { UNASSIGN_TRIGGER_LABEL } from "./ConfirmUnassign";
import { makeHarness, type Harness } from "./test-harness";
import {
  BATCH_APPLYING_REASON,
  taskActionDisabledReason,
  type TaskActionVerb,
} from "../store/store";

afterEach(cleanup);

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

/** Wrap the fixture's batch call in one deferred promise so a batch can be
    observed mid-flight. `releases` counts invocations of the wire call. */
function deferBatch(h: Harness) {
  const real = h.adapter.applyRuntimeBatch.bind(h.adapter);
  const released: string[][] = [];
  let open!: () => void;
  const gate = new Promise<void>((resolve) => {
    open = resolve;
  });
  h.adapter.applyRuntimeBatch = async (items) => {
    released.push(items.map((i) => i.verb));
    await gate;
    return real(items);
  };
  return { open, released };
}

/** Every row verb the controller exposes, spied on the identity path so a
    single-row verb would show up as a runtimeAction call. */
function spyRowVerbs(h: Harness): string[] {
  const calls: string[] = [];
  const real = h.adapter.runtimeAction.bind(h.adapter);
  h.adapter.runtimeAction = async (verb, target, args) => {
    calls.push(verb);
    return real(verb, target, args);
  };
  return calls;
}

const VERBS: TaskActionVerb[] = ["complete", "skip", "restore", "hide"];

describe("row verbs while a batch applies", () => {
  it("disables the row and refuses every second verb with the written reason", async () => {
    const h = makeHarness("ready");
    const verbs = spyRowVerbs(h);
    const { open, released } = deferBatch(h);

    // Start a real apply through the controller: the batch slot goes to
    // `applying` and stays there until the deferred call resolves.
    h.controller.selectRow("Press", true);
    h.controller.reviewBatch("complete");
    expect(h.store.getState().batch.phase).toBe("review");
    const pending = h.controller.applyBatch();
    expect(h.store.getState().batch.phase).toBe("applying");
    expect(released).toEqual([["done"]]);

    // The one answer every surface reads says why.
    for (const verb of VERBS) {
      expect(taskActionDisabledReason(h.store.getState(), verb)).toBe(
        BATCH_APPLYING_REASON,
      );
    }

    const { container } = h.ui(<Queue />);
    const press = rowFor(container, "Press");
    const complete = buttonIn(press, "Complete");
    // U4: the row's source verb is the unassign trigger; its confirm panel is
    // unreachable while the batch applies.
    const skip = buttonIn(press, UNASSIGN_TRIGGER_LABEL);
    expect(complete.disabled).toBe(true);
    expect(skip.disabled).toBe(true);
    expect(press.querySelector(".unassign__panel")).toBeNull();
    expect(complete.getAttribute("title")).toBe(BATCH_APPLYING_REASON);
    expect(skip.getAttribute("title")).toBe(BATCH_APPLYING_REASON);
    expect(press.textContent).toContain(BATCH_APPLYING_REASON);

    // Clicks, keys and the controller's own entry points: all refused.
    fireEvent.click(complete);
    fireEvent.click(skip);
    fireEvent.keyDown(press, { key: "c" });
    fireEvent.keyDown(press, { key: "e" });
    await h.controller.runRowBatch("Press", "complete");
    await h.controller.skipToday("Press");
    await h.controller.restoreToday("Press");
    await h.controller.hideAlways("Press");

    // Zero additional wire calls of either shape, and the applying batch was
    // not replaced by a second BATCH_SET.
    expect(released).toEqual([["done"]]);
    expect(verbs).toEqual([]);
    const mid = h.store.getState();
    expect(mid.batch.phase).toBe("applying");
    expect(mid.batch.action).toBe("complete");
    expect(mid.runtimeError).toBe(BATCH_APPLYING_REASON);

    // Resolve: the batch completes and the row verbs work again.
    open();
    await pending;
    await waitFor(() => expect(h.store.getState().batch.phase).toBe("done"));
    for (const verb of VERBS) {
      expect(taskActionDisabledReason(h.store.getState(), verb)).toBeNull();
    }
    const other = rowFor(container, "Magic Mirror");
    await waitFor(() => expect(buttonIn(other, "Complete").disabled).toBe(false));
    expect(buttonIn(other, UNASSIGN_TRIGGER_LABEL).disabled).toBe(false);
    expect(buttonIn(other, "Complete").getAttribute("title")).toBe("Complete");

    fireEvent.keyDown(other, { key: "c" });
    await waitFor(() => expect(released).toEqual([["done"], ["done"]]));
    expect(verbs).toEqual([]);
  });
});
