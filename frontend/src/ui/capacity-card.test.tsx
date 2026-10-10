/* U9 — Capacity headline card (KTD7/KTD10/KTD13).

   The Plan tasks body opens with one card: a headline that states over-by or
   remaining, a one-sentence cause, one segmented bar (calendar, work (Mint),
   anchors, habits, tasks) with a hatched overflow tail and a day-capacity
   marker, a legend, and a "Details" disclosure holding the full ledger the
   old rail showed. Unknown capacity (no reliable calendar data) is named
   explicitly and is never rendered as zero or over (KTD7). */

import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent } from "@testing-library/preact";

afterEach(cleanup);

import { CapacityCard } from "./CapacityCard";
import { Queue } from "./Queue";
import { makeHarness } from "./test-harness";
import { localSelected } from "../store/allocatorView";
import { formatBlockAmount } from "../model/time";

/** Force an over-capacity day by shrinking the day total under the fixed
    time the fixture already carries. */
function overHarness() {
  return makeHarness("ready", (sc) => {
    sc.inputs.capacity = {
      ...sc.inputs.capacity,
      total: 4,
      fixed: 3,
      mint: 0,
      anchored: 2,
      habits: 1,
      free: -3,
      availableForSelection: 0,
      overassigned: true,
    };
  });
}

describe("U9 capacity headline card", () => {
  it("an over-capacity day shows the over-by headline, overflow tail, and marker", () => {
    const h = overHarness();
    const s = h.store.getState();
    const { getByText, container } = h.ui(<CapacityCard />);
    const over = Math.max(0, localSelected(s) + 3 + 2 + 1 - 4);
    expect(over).toBeGreaterThan(0);
    expect(getByText(`Over by ${formatBlockAmount(over)}`)).toBeTruthy();
    expect(container.querySelector(".cap-card__overflow")).toBeTruthy();
    expect(container.querySelector(".cap-card__mark")).toBeTruthy();
  });

  it("an under-capacity day shows the remaining amount and no overflow tail", () => {
    const h = makeHarness("ready");
    const s = h.store.getState();
    const remaining = 31 - (localSelected(s) + 3 + 8 + 1);
    expect(remaining).toBeGreaterThan(0);
    const { getByText, container } = h.ui(<CapacityCard />);
    expect(getByText(`${formatBlockAmount(remaining)} left`)).toBeTruthy();
    expect(container.querySelector(".cap-card__overflow")).toBeNull();
    expect(container.querySelector(".cap-card__mark")).toBeTruthy();
  });

  it("unknown capacity is named explicitly, never rendered as zero or over (KTD7)", () => {
    const h = makeHarness("ready", (sc) => {
      (sc.inputs as { capacity: unknown }).capacity = null;
    });
    const { getByText, queryByText, container } = h.ui(<CapacityCard />);
    expect(getByText(/Capacity is unknown/i)).toBeTruthy();
    expect(container.querySelector(".cap-card__bar")).toBeNull();
    expect(queryByText(/Over by/i)).toBeNull();
    expect(queryByText(/\b0 blk\b/)).toBeNull();
  });

  it("dragging a task slider moves the tasks segment and headline together", () => {
    const h = makeHarness("ready");
    // Queue mounts the card itself now, so the slider drag and the headline
    // come from the same render (no second card instance).
    const { getByLabelText, getByText, container } = h.ui(<Queue />);
    const before = localSelected(h.store.getState());
    const tasksBefore = container.querySelector(".cap-card__seg--selected") as HTMLElement;
    const widthBefore = tasksBefore.style.width;
    fireEvent.input(getByLabelText("Magic Mirror duration in 15-minute steps"), {
      target: { value: "6" },
    });
    const after = localSelected(h.store.getState());
    expect(after).toBe(before + 3);
    const tasksAfter = container.querySelector(".cap-card__seg--selected") as HTMLElement;
    expect(tasksAfter.style.width).not.toBe(widthBefore);
    const remaining = 31 - (after + 3 + 8 + 1);
    expect(getByText(`${formatBlockAmount(remaining)} left`)).toBeTruthy();
  });

  it("legend names every segment and Details exposes every old-ledger number", () => {
    const h = makeHarness("ready");
    const { getByText, container } = h.ui(<CapacityCard />);
    // Scoped to the card's own legend: Rail's pie legend inside Details also
    // names Habits, so an unscoped query would match twice.
    const legendLabels = [
      ...container.querySelectorAll(".cap-card__legend-label"),
    ].map((el: Element) => el.textContent);
    for (const label of ["Calendar", "Work (Mint)", "Anchors", "Habits", "Tasks"]) {
      expect(legendLabels).toContain(label);
    }
    const details = container.querySelector(".cap-card__details") as HTMLDetailsElement;
    const summary = details.querySelector("summary") as HTMLElement;
    expect(summary.textContent).toContain("Details");
    // Keyboard reachable: the summary is the native focus target.
    summary.focus();
    expect(document.activeElement).toBe(summary);
    fireEvent.click(summary);
    expect(details.open).toBe(true);
    for (const label of [
      "Day capacity",
      "Reserved before tasks",
      "Task room",
      "Chosen tasks",
      "Over by",
    ]) {
      expect(getByText(label)).toBeTruthy();
    }
    // The inspection keyboard path survives inside Details.
    expect(container.querySelector(".pie__legend-item")).toBeTruthy();
  });
});
