/* Approval drawer source notes and named blockers (R5, R6, KTD8, AE2/AE3).

   Before this unit the drawer said only "Sources degraded", so a blocked
   commit named no failing read, and every non-blocking warning was invisible
   at the moment of approving writes. Now the blocker quotes the first BLOCKING
   warning verbatim, and each advisory warning is a note carrying the one
   action that clears it: Refresh sources, Open Settings, or the existing
   Sources details hint.

   Fixtures and direct store dispatches only — no network, no billed calls:
   every controller action here is a spy. */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent } from "@testing-library/preact";
import { ApprovalDrawer } from "./ApprovalDrawer";
import { SettingsShell } from "./SettingsShell";
import { makeHarness } from "./test-harness";
import { sourceHealthOf } from "../adapters/wire";

afterEach(cleanup);

/** Server-verbatim "unchanged since" notice: a stale-but-readable structure,
    which R6/AE2 keep out of the blocker list. */
const UNCHANGED =
  "Capacities structure 5c1f9a2e unchanged since 2026-10-16; Refresh to check it again.";

/** Server-verbatim failed read: R5/AE3 blocks commit and names it. */
const TODOIST_FAILED = "Todoist read failed (timeout)";

/** Server-verbatim mistyped declaration the adapter works around: an
    Assigned On note whose fix lives in Settings › Repair connections (KTD8,
    OQ-P4). */
const ASSIGNED_ON =
  'ignored Capacities Assigned On declaration for tasks — unknown property "Assigned To"';

describe("a failed read blocks commit and is named verbatim (R5, AE3)", () => {
  it("quotes the first blocking warning instead of 'Sources degraded'", () => {
    const h = makeHarness("commit-preview", (scenario) => {
      scenario.inputs.sourceWarnings = [TODOIST_FAILED];
      scenario.inputs.sourceHealth = sourceHealthOf(scenario.inputs.sourceWarnings);
    });
    const r = h.ui(<ApprovalDrawer />);

    expect(r.getByText(TODOIST_FAILED)).toBeTruthy();
    expect(r.queryByText(/Sources degraded/)).toBeNull();
    // A blocking warning is not a note: the drawer must not offer it a fix.
    expect(r.queryByRole("button", { name: "Refresh sources" })).toBeNull();
  });
});

describe("an advisory warning is a note, not a blocker (R6, AE2)", () => {
  it("shows no source blocker and a Refresh sources fix for 'unchanged since'", () => {
    const h = makeHarness("commit-preview", (scenario) => {
      scenario.inputs.sourceWarnings = [UNCHANGED];
      scenario.inputs.sourceHealth = sourceHealthOf(scenario.inputs.sourceWarnings);
    });
    const refresh = vi
      .spyOn(h.controller, "refreshSources")
      .mockResolvedValue(undefined as never);
    const r = h.ui(<ApprovalDrawer />);

    expect(r.queryByText("Blockers")).toBeNull();
    expect(r.queryByText(/Sources degraded/)).toBeNull();
    expect(r.getByText(UNCHANGED)).toBeTruthy();

    fireEvent.click(r.getByRole("button", { name: "Refresh sources" }));
    expect(refresh).toHaveBeenCalledTimes(1);
  });
});

describe("a note's fix goes where the fix actually lives (KTD8)", () => {
  it("opens the hub's Repair connections entry for an unknown Assigned On property", () => {
    const h = makeHarness("commit-preview", (scenario) => {
      scenario.inputs.sourceWarnings = [ASSIGNED_ON];
      scenario.inputs.sourceHealth = sourceHealthOf(scenario.inputs.sourceWarnings);
    });
    const r = h.ui(<ApprovalDrawer />);

    expect(r.getByText(ASSIGNED_ON)).toBeTruthy();
    fireEvent.click(r.getByRole("button", { name: "Open Settings" }));
    // A mapping/contract problem lands on the hub entry that owns the mapping,
    // and the press still closes the drawer.
    expect(h.store.getState().ui.settingsPanel).toBe("repair");
    expect(h.store.getState().ui.approvalOpen).toBe(false);

    cleanup();
    const hub = h.ui(<SettingsShell />);
    expect(hub.getByRole("heading", { name: "Repair connections" })).toBeTruthy();
  });

  it("opens the hub's freshness entry for a calendar diagnostic", () => {
    const calendar =
      "Calendar: 1 duplicate event representation(s) merged by canonical identity";
    const h = makeHarness("commit-preview", (scenario) => {
      scenario.inputs.sourceWarnings = [calendar];
      scenario.inputs.sourceHealth = sourceHealthOf(scenario.inputs.sourceWarnings);
    });
    const r = h.ui(<ApprovalDrawer />);

    expect(r.getByText(calendar)).toBeTruthy();
    fireEvent.click(r.getByRole("button", { name: "Open Settings" }));
    expect(h.store.getState().ui.settingsPanel).toBe("freshness");
    expect(h.store.getState().ui.approvalOpen).toBe(false);

    cleanup();
    const hub = h.ui(<SettingsShell />);
    expect(
      hub.getByRole("button", { name: /Is my data up to date\?/ }).getAttribute("aria-expanded"),
    ).toBe("true");
  });

  it("keeps the Skipped objects hint for a note with no single action", () => {
    const skipped =
      "skipped Capacities object bb95d80d-573c-4aff-8faa-9dccdd2617a1 — not plannable: No title";
    const h = makeHarness("commit-preview", (scenario) => {
      scenario.inputs.sourceWarnings = [skipped];
      scenario.inputs.sourceHealth = sourceHealthOf(scenario.inputs.sourceWarnings);
    });
    const r = h.ui(<ApprovalDrawer />);

    expect(r.getByText(skipped)).toBeTruthy();
    expect(r.getByText(/Capacity . Details . Sources/)).toBeTruthy();
  });
});
