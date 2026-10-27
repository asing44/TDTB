/* plan-empty-state.test.tsx — U3a (revised 2026-10-27): Adam's words were
   "items still are already showing as pre-selected from Todoist instead of
   allowing me to select what is assigned on today". Selecting today is his job,
   so a cold day now starts with an EMPTY plan: every Todoist and Capacities row
   waits in Not in plan (only a fixed calendar commitment starts in the plan,
   because it is not an assignable choice).

   An empty plan needs a way in, so the surface owes him one line saying where
   the items are. These tests pin that line, that it disappears the moment a row
   joins the plan, and that the drawer — not the plan lanes — holds everything at
   the start of the day.

   Fixture adapters only: no live adapter, no billed call, no source write. */

import { afterEach, describe, expect, it } from "vitest";
import { act, cleanup, fireEvent, waitFor } from "@testing-library/preact";

import { Queue } from "./Queue";
import { makeHarness, type Harness } from "./test-harness";

afterEach(cleanup);

const PROMPT = "Nothing in today's plan yet — add items from Not in plan below.";

/** Put the day's own rule in force. A fixture day restores the legacy all-in
    default (it depicts a plan already built), so this is the state a cold
    morning actually loads in: no membership entries, nothing pre-selected. */
function ruleDay(h: Harness): void {
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

function emptyPrompt(container: Element): HTMLElement | null {
  return container.querySelector<HTMLElement>("[data-plan-empty]");
}

function drawer(container: Element): HTMLElement | null {
  return container.querySelector<HTMLElement>("#unassigned-today");
}

describe("U3a: an empty plan says how to fill it", () => {
  it("prompts for a way in and leaves every sourced row in Not in plan", async () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    ruleDay(h);

    await waitFor(() => expect(emptyPrompt(container)).not.toBeNull());
    const prompt = emptyPrompt(container)!;
    // Announced, and exactly the one line — no lane furniture behind it.
    expect(prompt.getAttribute("role")).toBe("status");
    expect(prompt.textContent).toBe(PROMPT);
    expect(container.querySelector(".tasktable")).toBeNull();

    // Nothing a source sent is pre-selected: the whole queue waits below.
    const pool = drawer(container)!;
    expect(pool).not.toBeNull();
    expect(pool.textContent).toContain("8 not in plan");
    expect(pool.querySelectorAll('[role="switch"]').length).toBeGreaterThan(0);
    expect(pool.textContent).toContain("Review AWS module 4");
  });

  it("clears the prompt as soon as one row joins the plan", async () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    ruleDay(h);
    await waitFor(() => expect(emptyPrompt(container)).not.toBeNull());

    // The due-today tier's one-click settle: the smallest real way in.
    fireEvent.click(
      container.querySelector<HTMLButtonElement>('[data-tier-add-all="due-today"]')!,
    );

    await waitFor(() => expect(emptyPrompt(container)).toBeNull());
    expect(container.querySelector(".tasktable")).not.toBeNull();
    const planned = Array.from(
      container.querySelectorAll<HTMLElement>(".tasktable .qrow__name"),
    ).map((el) => el.textContent);
    expect(planned).toHaveLength(2);
    expect(planned).toEqual(
      expect.arrayContaining(["Review AWS module 4", "Pick up prescription"]),
    );
    // Still a local edit: the drawer keeps the rows he has not chosen yet.
    expect(drawer(container)?.textContent).toContain("6 not in plan");
  });
});
