/* capacity-card-single.test.tsx — single-render guard for the Plan screen's
   capacity card (2026-11-04 audit).

   An operator screenshot showed the card's "Nothing blocking" sub-card and the
   "Marker = end of day" line apparently repeated several times down the right
   rail, with large blank gaps between the repeats. The audit hypothesis was a
   duplicated render: the card is mounted from Queue's two branches (the quiet
   day and the populated table), and opening Details mounts the whole retired
   rail inside that same card.

   Source renders the card exactly once per branch and the ledger exactly once
   inside Details, so this file pins that: the DOM must carry one card, one
   issues sub-card, one capacity line, and one rail — before and after the
   Details disclosure opens. A future second mount (a copy left behind by a
   refactor, an extra CapacityCard in the rail, a Detail body that re-renders
   the ledger twice) fails here instead of showing up as repeated blocks in a
   screenshot.

   The headless reproduction attempt (1280x800 and 1280x900, Details closed
   then open, .cockpit__main scrolled to every offset) found the same single
   set every time, so this guard tests the invariant, not a scroll-container
   fix. */

import { afterEach, describe, expect, it } from "vitest";
import { cleanup } from "@testing-library/preact";

afterEach(cleanup);

import { Queue } from "./Queue";
import { makeHarness } from "./test-harness";

/** Count only rendered cards: the Details disclosure mounts the whole rail
    body, which is the one place a duplicate could hide. */
function counts(root: HTMLElement) {
  return {
    cards: root.querySelectorAll(".cap-card").length,
    issues: root.querySelectorAll(".cap-card__issues").length,
    capacityLines: root.querySelectorAll(".cap-card__capacity").length,
    rails: root.querySelectorAll(".cap-card .rail").length,
  };
}

describe("the Plan screen renders one capacity card", () => {
  it("mounts exactly one card, one issues sub-card, and one capacity line", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    // The ledger body is mounted inside the closed disclosure (browsers hide
    // it, the DOM still holds it), so one rail is expected here too.
    expect(counts(container)).toEqual({
      cards: 1,
      issues: 1,
      capacityLines: 1,
      rails: 1,
    });
    expect(container.querySelector(".cap-card .rail")).toBeTruthy();
  });

  it("keeps one of each when the Details disclosure is open", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    const details = container.querySelector<HTMLDetailsElement>(
      ".cap-card__details",
    )!;
    details.open = true;
    // Opening Details mounts the same single rail inside the same single card;
    // it must not bring a second card, issues sub-card, or capacity line.
    expect(counts(container)).toEqual({
      cards: 1,
      issues: 1,
      capacityLines: 1,
      rails: 1,
    });
    expect(container.querySelector(".cap-card--unknown")).toBeNull();
  });

  it("renders one card on the quiet day branch too", () => {
    const h = makeHarness("ready", (sc) => {
      sc.inputs.assigned = [];
    });
    const { container } = h.ui(<Queue />);
    expect(container.querySelector(".queue__quiet")).toBeTruthy();
    expect(counts(container).cards).toBe(1);
  });
});
