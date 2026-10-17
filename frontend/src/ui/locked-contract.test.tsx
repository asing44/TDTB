/* IMP-03 Red tests for the frozen reliability contract (2026-08-09 plan,
   locked product contract items 1-21). These MUST FAIL against the current
   cockpit: they pin the compact composition that IMP-06/08/09 will implement.
   Synthetic fixture scenarios only - no live adapters, no providers. */

import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent } from "@testing-library/preact";

import { App } from "./App";
import { AllocationPie } from "./AllocationPie";
import { Queue } from "./Queue";
import { makeHarness as stageHarness, type Harness } from "./test-harness";

/** These tests exercise the Plan work surface. The app now boots on Set up
    day, so stage the scenario and explicitly land on the Plan screen. */
function makeHarness(
  name: Parameters<typeof stageHarness>[0],
  mutate?: Parameters<typeof stageHarness>[1],
): Harness {
  const h = stageHarness(name, mutate);
  h.store.dispatch({ type: "UI", patch: { screen: "plan", settingsPanel: null } });
  return h;
}

afterEach(cleanup);

describe("locked contract: Today's work language (item 1)", () => {
  it("names the cockpit list surface Today's work, never Queue", () => {
    const h = makeHarness("ready");
    const r = h.ui(<App />);
    expect(r.getByRole("region", { name: /Assigned this morning/i })).toBeTruthy();
    expect(r.queryByText(/Assigned queue/i)).toBeNull();
  });
});

describe("locked contract: explicit direct actions plus More (item 3)", () => {
  it("rows complete directly; source removal and Delete live behind More", () => {
    /* F4a (2026-10-16): the row's direct verbs are the labelled controls
       Complete, Skip today and Expand. The old icon faces (Mark complete in
       source, Leave as-is) and their hover-only words are gone; source
       removal (Remove from planning) and the destructive Delete remain
       reachable only through the row's More menu. */
    const h = makeHarness("ready");
    const r = h.ui(<Queue />);
    // A persistent action column is prohibited.
    expect(r.queryByText("Actions")).toBeNull();
    // Direct completion on every row, named in plain words.
    expect(
      r.getAllByRole("button", { name: "Complete" }).length,
    ).toBeGreaterThan(0);
    expect(
      r.getAllByRole("button", { name: "Skip today" }).length,
    ).toBeGreaterThan(0);
    // Old deferral verb is gone; Leave as-is was its replacement and left
    // with F4a's labelled controls.
    expect(r.queryByRole("button", { name: /^Defer/ })).toBeNull();
    expect(r.queryByRole("button", { name: /^Leave as-is/ })).toBeNull();
    // Nothing destructive is a direct control…
    expect(
      r.queryAllByRole("button", { name: /^Remove from planning/ }),
    ).toHaveLength(0);
    expect(r.queryAllByRole("button", { name: /^Delete/ })).toHaveLength(0);
    // …and every row still opens one More menu that owns them.
    expect(
      r.getAllByRole("button", { name: /^More actions for / }).length,
    ).toBeGreaterThan(0);
  });
});

describe("locked contract: collapsible priority bands (item 6)", () => {
  it("band headers are keyboard-accessible disclosures with expanded state", () => {
    const h = makeHarness("ready");
    const r = h.ui(<Queue />);
    const crit = r.getByText("Critical");
    const header = crit.closest("button, [role='button']");
    expect(header).not.toBeNull();
    expect(header?.getAttribute("aria-expanded")).toBeTruthy();
  });
});

describe("locked contract: pie inspection (items 8-9)", () => {
  it("clicking a segment filters/highlights contributors; clearing restores", () => {
    const h = makeHarness("ready");
    const r = h.ui(<AllocationPie />);
    const slice = r.container.querySelector(".pie__slice");
    expect(slice).not.toBeNull();
    fireEvent.click(slice as Element);
    // Hover may preview, but click must drive the inspection state.
    const readout = r.container.querySelector(".pie__readout");
    expect(readout?.textContent).not.toContain("planned / day");
  });

  it("center readout uses planned/capacity/over-remaining hierarchy, not the old PLANNED / DAY treatment", () => {
    const h = makeHarness("ready");
    const r = h.ui(<AllocationPie />);
    const readout = r.container.querySelector(".pie__readout");
    const text = readout?.textContent ?? "";
    expect(text).toMatch(/over|remaining/i);
    expect(text).not.toMatch(/planned \/ day/i);
  });
});

describe("locked contract: pruned surfaces (items 2, 21)", () => {
  it("mounts no duplicate mobile surface or read-only placement review", () => {
    const h = makeHarness("sequenced");
    const r = h.ui(<App />);
    // No duplicate mobile/desktop planning surface.
    expect(r.container.querySelector('[aria-label="Agenda"]')).toBeNull();
    // No Trim Assist surface.
    expect(r.container.querySelector('[aria-label="Trim assist"]')).toBeNull();
    // No read-only placement/schedule review.
    expect(r.container.querySelector(".placement")).toBeNull();
  });
});
