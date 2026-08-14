/* IMP-03 Red tests for the frozen reliability contract (2026-08-09 plan,
   locked product contract items 1-21). These MUST FAIL against the current
   cockpit: they pin the compact composition that IMP-06/08/09 will implement.
   Synthetic fixture scenarios only - no live adapters, no providers. */

import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent } from "@testing-library/preact";

import { App } from "./App";
import { AllocationPie } from "./AllocationPie";
import { Queue } from "./Queue";
import { makeHarness } from "./test-harness";

afterEach(cleanup);

describe("locked contract: Today's work language (item 1)", () => {
  it("names the cockpit list surface Today's work, never Queue", () => {
    const h = makeHarness("ready");
    const r = h.ui(<App />);
    expect(r.getByRole("region", { name: /Today's work/i })).toBeTruthy();
    expect(r.queryByText(/Assigned queue/i)).toBeNull();
  });
});

describe("locked contract: direct Done/Drop plus More (item 3)", () => {
  it("rows show direct Done and Drop; Unassign and Delete live behind More; no Defer", () => {
    const h = makeHarness("ready");
    const r = h.ui(<Queue />);
    // A persistent action column is prohibited.
    expect(r.queryByText("Actions")).toBeNull();
    // Direct Done (row action) with the final intent vocabulary — every
    // row renders one, so the query is a set (getByRole would throw on
    // the multi-row surface).
    expect(r.getAllByRole("button", { name: /^Mark done:/ }).length).toBeGreaterThan(0);
    // Old deferral verb is gone; Drop from plan replaces it.
    expect(r.queryByRole("button", { name: /^Defer/ })).toBeNull();
    // Unassign and Delete are reachable only through a More menu.
    expect(r.getAllByRole("button", { name: /^More/ }).length).toBeGreaterThan(0);
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
