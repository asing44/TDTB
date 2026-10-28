/* task-actions-integration.test.tsx — F7: the integrated Today's-work surface.
   This is the one file that mounts Queue with the fixture adapter and checks
   the slices together, in the operator's order:

   - the collapsed row's three controls and the expanded row's two-line panel
     (line 1 shapes the row, line 2 = Save/Discard left + Source menu right),
     with the retired per-row More menu absent everywhere;
   - selection stays local and only then does the batch bar appear;
   - a single Source-menu verb (Hide always / Unassign) reports through the
     same batch result panel as a multi-row batch, with Undo;
   - the row's confirmed source unassign lands the row in the Skipped today
     section, which restores it from there.

   Retirement note: the per-row "Always exclude" menu (always-exclude.test.tsx)
   is gone. Its assertions that must still hold NOW are re-created here against
   the Source menu: Hide always is offered for todoist and capacities rows and
   is journaled as a `hide_always` batch item against the stable source
   identity (never a direct tag-exclusion policy save), and the row's own
   path for Capacities cannot unassign. See the retired-test ledger,
   design/task-actions/retired-tests.md.

   Fixture adapters only: no live adapter, no billed call, no source write. */

import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, waitFor } from "@testing-library/preact";

afterEach(cleanup);

import { Queue } from "./Queue";
import { UNASSIGN_CONFIRM_LABEL, UNASSIGN_TRIGGER_LABEL } from "./ConfirmUnassign";
import { makeHarness, type Harness } from "./test-harness";
import { fingerprintFixedInputs } from "../model/fingerprint";
import { fixedInputsOf } from "../fixtures/scenarios";
import type { PlanInputs } from "../model/types";

const PRESS_IDENTITY = "todoist:6fx00xPRESS";
const NOTE_IDENTITY = "capacities:fixture-space:custom-project:note-processing";

function rowFor(container: Element, name: string): HTMLElement {
  const row = Array.from(container.querySelectorAll<HTMLElement>(".qrow")).find(
    (r) => r.querySelector(".qrow__name")?.textContent === name,
  );
  if (!row) throw new Error(`no row named ${name}`);
  return row;
}

function expand(container: Element, name: string): HTMLElement {
  fireEvent.click(rowFor(container, name).querySelector('button[aria-label="Expand"]')!);
  return rowFor(container, name);
}

function openSource(row: HTMLElement): HTMLElement {
  const trigger = row.querySelector<HTMLButtonElement>(".source-menu__trigger");
  if (!trigger) throw new Error("no Source trigger on the row");
  fireEvent.click(trigger);
  const menu = row.querySelector<HTMLElement>('[role="menu"]');
  if (!menu) throw new Error("Source menu is not open");
  return menu;
}

function menuItem(menu: Element, label: string): HTMLButtonElement {
  const item = menu.querySelector<HTMLButtonElement>(`button[aria-label="${label}"]`);
  if (!item) throw new Error(`no menu item labelled ${label}`);
  return item;
}

function buttonWithText(root: Element, text: string): HTMLButtonElement {
  const btn = Array.from(root.querySelectorAll<HTMLButtonElement>("button")).find(
    (b) => b.textContent?.trim() === text,
  );
  if (!btn) throw new Error(`no button reading ${text}`);
  return btn;
}

/** The batch result panel is published once the apply lands, so reading it is
    a wait, not a synchronous query. */
async function batchResults(container: Element): Promise<HTMLElement> {
  return waitFor(() => {
    const panel = container.querySelector<HTMLElement>(
      '[data-testid="batch-results"]',
    );
    if (!panel) throw new Error("batch results panel is not published yet");
    return panel;
  });
}

/** Spy the journaled batch wire the row verbs ride. */
function spyBatches(h: Harness): Array<{ verb: string; target: string }> {
  const batched: Array<{ verb: string; target: string }> = [];
  const applied = h.adapter.applyRuntimeBatch.bind(h.adapter);
  h.adapter.applyRuntimeBatch = async (items) => {
    for (const item of items) batched.push({ verb: item.verb, target: item.target });
    return applied(items);
  };
  return batched;
}

/** Spy the immediate single verbs (Delete, and the Skipped today Restore). */
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

describe("F7 integration: the row and its expanded panel", () => {
  it("expands into the two-line panel with line 2 holding the Source menu, and never a More menu", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);

    // Collapsed: the permanent Mint toggle plus three labelled controls, no
    // menu of any kind.
    const press = rowFor(container, "Press");
    expect(
      Array.from(press.querySelectorAll<HTMLElement>(".trow__acts > button")).map((b) =>
        b.getAttribute("aria-label"),
      ),
    ).toEqual([
      "May overlap Mint: Press",
      "Complete",
      UNASSIGN_TRIGGER_LABEL,
      "Expand",
    ]);
    expect(container.querySelector('button[aria-haspopup="menu"]')).toBeNull();
    expect(container.querySelector(".row-more")).toBeNull();
    expect(container.querySelector('button[aria-label^="Adjust time for"]')).toBeNull();

    const row = expand(container, "Press");
    const panel = row.querySelector<HTMLElement>(".trow__panel")!;
    expect(panel).not.toBeNull();
    const line1 = panel.querySelector<HTMLElement>(".trow__l1")!;
    const line2 = panel.querySelector<HTMLElement>(".trow__l2")!;
    expect(line1).not.toBeNull();
    expect(line2).not.toBeNull();
    // Line 1 shapes the row at once: an exact-minutes field, the All-day
    // toggle and Start. The duration slider stays in the collapsed row, and
    // May overlap Mint lives there too.
    expect(line1.querySelector('input[aria-label="Exact minutes"]')).not.toBeNull();
    expect(line1.textContent).toContain("All-day");
    expect(line1.textContent).toContain("Start");
    expect(line1.querySelector('[role="radiogroup"][aria-label="Duration"]')).toBeNull();
    expect(line1.textContent).not.toContain("May overlap Mint");
    expect(line2.querySelector(".source-menu__trigger")).not.toBeNull();
    expect(line2.lastElementChild?.classList.contains("source-menu")).toBe(true);
  });

  it("keeps line 2 to the Source menu, because edits apply at once", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    let row = expand(container, "Press");
    let line2 = row.querySelector<HTMLElement>(".trow__l2")!;
    expect(Array.from(line2.querySelectorAll("button")).map((b) => b.getAttribute("aria-label") ?? b.textContent?.trim()))
      .toEqual(["Source actions"]);

    fireEvent.input(
      row.querySelector<HTMLInputElement>('input[aria-label="Exact minutes"]')!,
      { target: { value: "2h" } },
    );
    fireEvent.keyDown(
      rowFor(container, "Press").querySelector('input[aria-label="Exact minutes"]')!,
      { key: "Enter" },
    );
    row = rowFor(container, "Press");
    line2 = row.querySelector<HTMLElement>(".trow__l2")!;
    // No Save/Discard appears: the edit already landed, and the row carries a
    // single-step Undo instead.
    expect(Array.from(line2.querySelectorAll("button")).map((el) => el.getAttribute("aria-label") ?? el.textContent?.trim())).toEqual([
      "Source actions",
    ]);
    expect(h.store.getState().overrides["Press"]?.blocks).toBe(4);
    expect(row.querySelector(".trow__state")?.textContent).not.toContain("Staged");
    expect(row.querySelector('button[aria-label="Undo last change to Press"]')).not.toBeNull();
  });
});

describe("F7 integration: selection and the batch bar", () => {
  it("shows the batch bar only once a row is selected, and selection stays local", () => {
    const h = makeHarness("ready");
    const batched = spyBatches(h);
    const { container } = h.ui(<Queue />);

    expect(container.querySelector('[data-testid="batch-bar"]')).toBeNull();
    const press = rowFor(container, "Press");
    fireEvent.click(press.querySelector<HTMLInputElement>(".trow__check")!);

    const bar = container.querySelector<HTMLElement>('[data-testid="batch-bar"]')!;
    expect(bar).not.toBeNull();
    expect(
      container.querySelector('[data-testid="batch-count"]')?.textContent,
    ).toBe("1 selected");
    expect(h.store.getState().ui.selection).toEqual(["Press"]);
    // Nothing reached a source from selecting.
    expect(batched).toEqual([]);

    // Clearing the selection takes the bar away again.
    fireEvent.click(buttonWithText(bar, "Clear selection"));
    expect(container.querySelector('[data-testid="batch-bar"]')).toBeNull();
  });
});

describe("F7 integration: one source verb reports as a batch of one", () => {
  it("Hide always is a journaled hide_always item and reports a result with Undo", async () => {
    const h = makeHarness("ready");
    const batched = spyBatches(h);
    const policy = vi.spyOn(h.controller, "alwaysExcludeTodoist");
    const capPolicy = vi.spyOn(h.controller, "alwaysExcludeCapacitiesObject");
    const { container } = h.ui(<Queue />);

    const row = expand(container, "Press");
    fireEvent.click(menuItem(openSource(row), "Hide always"));

    await waitFor(() =>
      expect(batched).toEqual([{ verb: "hide_always", target: PRESS_IDENTITY }]),
    );
    // The durable exclusion is written by the journaled verb, never by a
    // settings-screen policy save: that is what keeps it undoable.
    expect(policy).not.toHaveBeenCalled();
    expect(capPolicy).not.toHaveBeenCalled();

    const results = await batchResults(container);
    expect(results.getAttribute("aria-label")).toBe("Batch result");
    expect(results.textContent).toContain("Hide always");
    expect(results.textContent).toContain("Press");
    expect(results.textContent).toContain("1 done");

    // Undo is offered from the same panel and reports what it reversed.
    fireEvent.click(buttonWithText(results, "Undo whole batch"));
    await waitFor(() =>
      expect(
        container.querySelector('[data-testid="batch-undo-report"]')?.textContent,
      ).toContain("Reverted"),
    );
  });

  it("the Source menu offers no unassign item and no Capacities note (KTD4)", async () => {
    const h = makeHarness("ready");
    const batched = spyBatches(h);
    const { container } = h.ui(<Queue />);

    const row = expand(container, "Press");
    const menu = openSource(row);
    expect(
      Array.from(menu.querySelectorAll<HTMLElement>('[role="menuitem"]')).map((b) =>
        b.getAttribute("aria-label"),
      ),
    ).toEqual(["Hide always", "Delete in Todoist…"]);
    expect(menu.querySelector('[aria-label="Unassign in source"]')).toBeNull();
    expect(menu.querySelector('[aria-label="Skip occurrence"]')).toBeNull();
    expect(menu.textContent).not.toMatch(/unassign/i);

    // Hide always still rides the journaled batch-of-one surface.
    fireEvent.click(menuItem(menu, "Hide always"));
    await waitFor(() =>
      expect(batched).toEqual([{ verb: "hide_always", target: PRESS_IDENTITY }]),
    );
    const results = await batchResults(container);
    expect(results.textContent).toContain("Hide always");
    expect(results.textContent).toContain("Press");
    expect(buttonWithText(results, "Undo whole batch")).not.toBeNull();
  });

  it("a Capacities row offers Hide always and no unassign item or note", async () => {
    const h = makeHarness("ready");
    const batched = spyBatches(h);
    const capPolicy = vi.spyOn(h.controller, "alwaysExcludeCapacitiesObject");
    const { container } = h.ui(<Queue />);

    const row = expand(container, "Note Processing");
    const menu = openSource(row);
    expect(menu.querySelector('[aria-label="Unassign in source"]')).toBeNull();
    expect(menu.textContent).not.toMatch(/unassign/i);
    fireEvent.click(menuItem(menu, "Hide always"));

    await waitFor(() =>
      expect(batched).toEqual([{ verb: "hide_always", target: NOTE_IDENTITY }]),
    );
    expect(capPolicy).not.toHaveBeenCalled();
    await waitFor(() =>
      expect(
        container.querySelector('[data-testid="batch-results"]')?.textContent,
      ).toContain("Note Processing"),
    );
  });
});

describe("F7 integration: the confirmed source unassign and the Skipped today section", () => {
  it("unassigns through the adapter after the confirm, then restores from the Skipped today section", async () => {
    const h = makeHarness("ready");
    const calls = spyVerbs(h);
    const batched = spyBatches(h);
    const { container } = h.ui(<Queue />);

    // U4: click 1 opens the confirm and writes nothing; click 2 unassigns.
    const trigger = rowFor(container, "Press").querySelector<HTMLButtonElement>(
      `button[aria-label="${UNASSIGN_TRIGGER_LABEL}"]`,
    )!;
    fireEvent.click(trigger);
    expect(batched).toEqual([]);
    expect(calls).toEqual([]);
    fireEvent.click(
      buttonWithText(rowFor(container, "Press"), UNASSIGN_CONFIRM_LABEL),
    );
    await waitFor(() =>
      expect(batched).toEqual([{ verb: "unassign", target: PRESS_IDENTITY }]),
    );
    // A source unassign is durable and date-scoped: never a browser-only
    // exclusion, and never an immediate single verb.
    expect(h.store.getState().overrides["Press"]).toBeUndefined();
    expect(calls).toEqual([]);
    // The batch of one settles before the next row verb, so the single batch
    // slot is free for the Restore below.
    await waitFor(() => expect(h.store.getState().batch.phase).toBe("done"));

    // The server's post-unassign read moves the row into dropped[].
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
    expect(section.textContent).toContain("1 skipped");
    const skipped = section.querySelector<HTMLElement>(
      `[data-identity="${PRESS_IDENTITY}"]`,
    )!;
    expect(skipped.textContent).toContain("Press");

    fireEvent.click(skipped.querySelector<HTMLButtonElement>(".trow__acts button")!);
    await waitFor(() =>
      expect(calls).toEqual([
        { verb: "restore_today", target: PRESS_IDENTITY },
      ]),
    );
    await waitFor(() => expect(container.querySelector("#skipped-today")).toBeNull());
  });
});
