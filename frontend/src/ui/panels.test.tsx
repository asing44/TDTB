/* panels.test — FEEDBACK-08: Results/SOW reachability from the primary dock,
   Day Setup prominence, and mouse/keyboard open-close-focus behavior for the
   two primary panels. Finish-first contracts (FEEDBACK-01..06, T21 partial
   shout, two-gate commit) stay unchanged; these tests guard the panel
   reachability and focus guarantees only. */

import { afterEach, describe, expect, it } from "vitest";
import { act, cleanup, fireEvent } from "@testing-library/preact";

afterEach(cleanup);

import { makeHarness } from "./test-harness";
import { useAppState } from "./context";
import { ActionDock } from "./ActionDock";
import { ApprovalDrawer } from "./ApprovalDrawer";
import { SetupDrawer } from "./SetupDrawer";
import { Rail } from "./Rail";

/* Mirror App.tsx's drawer lifecycle: drawers mount only while open, so the
   useDialog mount effect (initial focus) runs with the dialog present. */
function DrawerHost() {
  const s = useAppState();
  return (
    <>
      <ActionDock />
      {s.ui.approvalOpen && <ApprovalDrawer />}
      {s.ui.setupOpen && <SetupDrawer />}
    </>
  );
}

function dockWithDrawer(
  h: ReturnType<typeof makeHarness>,
): ReturnType<ReturnType<typeof makeHarness>["ui"]> {
  return h.ui(<DrawerHost />);
}

describe("FEEDBACK-08 Results/SOW reachability", () => {
  it("review state exposes a Results entry that opens the drawer labeled Preview writes", () => {
    const h = makeHarness("sequenced");
    const r = dockWithDrawer(h);
    expect(h.store.getState().ui.approvalOpen).toBe(false);
    fireEvent.click(r.getByRole("button", { name: /^Results/ }));
    expect(h.store.getState().ui.approvalOpen).toBe(true);
    expect(r.getAllByText("Preview writes").length).toBeGreaterThan(0);
  });

  it("review Results drawer explains how to build the preview instead of trapping", () => {
    const h = makeHarness("sequenced");
    const r = dockWithDrawer(h);
    fireEvent.click(r.getByRole("button", { name: /^Results/ }));
    expect(r.getByText(/Run Preview commit/)).toBeTruthy();
  });

  it("preview state labels the primary entry Results and opens the drawer", () => {
    const h = makeHarness("commit-preview");
    const r = dockWithDrawer(h);
    const results = r.getByRole("button", { name: /^Results/ });
    expect(results.className).toContain("btn--primary");
    fireEvent.click(results);
    expect(h.store.getState().ui.approvalOpen).toBe(true);
  });

  it("verified state exposes Results and the drawer reports the committed result", () => {
    const h = makeHarness("verified");
    const r = dockWithDrawer(h);
    expect(r.getByRole("button", { name: /^Results/ })).toBeTruthy();
    expect(r.getByRole("heading", { name: "Results" })).toBeTruthy();
  });

  it("sequence/setup states show no dead Results control", () => {
    for (const name of ["fresh", "ready"] as const) {
      const h = makeHarness(name);
      const r = h.ui(<ActionDock />);
      expect(r.queryByRole("button", { name: /^Results/ })).toBeNull();
      r.unmount();
    }
  });

  it("partial state keeps the T21 danger entry to the results drawer", () => {
    const h = makeHarness("commit-preview");
    h.store.dispatch({ type: "ARM_LIVE" });
    h.store.dispatch({ type: "COMMIT_START" });
    h.store.dispatch({
      type: "COMMIT_DONE",
      report: {
        status: "partial",
        surfaces: [
          { system: "todoist", status: "ok", detail: "2 scheduled" },
          { system: "calendar", status: "failed", detail: "BusyCal write refused" },
          { system: "vault", status: "skipped", detail: "aborted after calendar failure" },
        ],
        verifyFailures: ["calendar: 3 expected events, 0 found"],
      },
    });
    const r = dockWithDrawer(h);
    const entry = r.getByText("Commit incomplete — view failures").closest("button")!;
    fireEvent.click(entry);
    expect(h.store.getState().ui.approvalOpen).toBe(true);
    expect(r.getByRole("heading", { name: "Results" })).toBeTruthy();
  });
});

describe("FEEDBACK-08 Day Setup prominence", () => {
  it("pending setup shows a prominent rail chip and the dock not-confirmed hint", () => {
    const fresh = makeHarness("fresh");
    const r1 = fresh.ui(<Rail />);
    const chip = r1.getByRole("button", { name: "Open day setup — setup not confirmed" });
    expect(chip.textContent).toContain("Setup pending");
    expect(chip.className).toContain("chip--setup-pending");
    r1.unmount();

    const dock = makeHarness("fresh");
    const r2 = dock.ui(<ActionDock />);
    expect(r2.getByText("Confirm day setup")).toBeTruthy();
    expect(r2.getByText("setup not confirmed")).toBeTruthy();
  });

  it("confirmed setup collapses the rail chip to a calm state", () => {
    const ready = makeHarness("ready");
    const r = ready.ui(<Rail />);
    expect(r.getByRole("button", { name: "Open day setup" })).toBeTruthy();
    expect(r.queryByText(/Setup pending/)).toBeNull();
    expect(r.getByText("Setup ✓")).toBeTruthy();
  });
});

describe("FEEDBACK-08 open/close/focus behavior", () => {
  it("Results opens on keyboard-activated click, Escape closes, focus restores", async () => {
    const h = makeHarness("sequenced");
    const r = dockWithDrawer(h);
    const results = r.getByRole("button", { name: /^Results/ });
    results.focus();
    await act(async () => {
      fireEvent.click(results);
    });
    expect(h.store.getState().ui.approvalOpen).toBe(true);
    const dialog = r.container.querySelector('[role="dialog"]') as HTMLElement;
    expect(dialog).toBeTruthy();
    expect(dialog.contains(document.activeElement)).toBe(true);
    fireEvent.keyDown(dialog, { key: "Escape" });
    await act(async () => {});
    expect(h.store.getState().ui.approvalOpen).toBe(false);
    expect(document.activeElement).toBe(results);
  });

  it("backdrop click closes the drawer (mouse path)", () => {
    const h = makeHarness("commit-preview");
    const r = dockWithDrawer(h);
    expect(h.store.getState().ui.approvalOpen).toBe(true);
    fireEvent.click(r.container.querySelector(".drawer-backdrop") as Element);
    expect(h.store.getState().ui.approvalOpen).toBe(false);
  });

  it("Day setup opens from the dock, Escape closes, focus returns to the button", async () => {
    const h = makeHarness("fresh");
    const r = dockWithDrawer(h);
    const setup = r.getByRole("button", { name: "Open day setup" });
    setup.focus();
    await act(async () => {
      fireEvent.click(setup);
    });
    expect(h.store.getState().ui.setupOpen).toBe(true);
    const dialog = r.container.querySelector('[role="dialog"]') as HTMLElement;
    expect(dialog).toBeTruthy();
    expect(dialog.contains(document.activeElement)).toBe(true);
    fireEvent.keyDown(dialog, { key: "Escape" });
    await act(async () => {});
    expect(h.store.getState().ui.setupOpen).toBe(false);
    expect(document.activeElement).toBe(setup);
  });
});

describe("FEEDBACK-12 exact-write times render 12-hour", () => {
  it("expanded exact writes show AM/PM times, never 24-hour", () => {
    const h = makeHarness("commit-preview");
    const r = dockWithDrawer(h);
    fireEvent.click(r.getByRole("button", { name: /Exact writes/ }));
    expect(r.getByText("schedule · Pick up prescription @ 4 PM")).toBeTruthy();
    expect(r.getByText("schedule · Review AWS module 4 @ 12:30 PM")).toBeTruthy();
    expect(r.getByText("create-event · ⬜ Magic Mirror @ 10:45 AM")).toBeTruthy();
    expect(r.getByText("create-event · ⬜ Rowe's T-shirt Redesign 2026 @ 9:45 AM")).toBeTruthy();
    expect(r.getByText("create-event · ⬜ Press @ 7 PM")).toBeTruthy();
  });
});

describe("FEEDBACK-23 due verification display", () => {
  it("structured due failures render 12-hour; machine fields stay canonical", () => {
    const h = makeHarness("commit-preview");
    h.store.dispatch({ type: "ARM_LIVE" });
    h.store.dispatch({ type: "COMMIT_START" });
    h.store.dispatch({
      type: "COMMIT_DONE",
      report: {
        status: "failed",
        surfaces: [
          { system: "todoist", status: "failed", detail: "due mismatch" },
        ],
        verifyFailures: [
          "todoist: 'Press' due mismatch (intent 7 PM, live 11 PM)",
        ],
        verifyDetails: [
          {
            kind: "due",
            name: "Press",
            intent: "19:00",
            live: "23:00",
            liveRaw: "2026-07-12T23:00:00Z",
            liveTimezone: "America/New_York",
            reason: "mismatch",
            message: "todoist: 'Press' due mismatch (intent 7 PM, live 11 PM)",
          },
        ],
      },
    });
    const r = dockWithDrawer(h);
    // visible text is 12-hour with AM/PM, never raw 24h
    expect(r.getByText(/Press — due verification: intent 7 PM, live 11 PM/)).toBeTruthy();
    expect(r.queryByText(/19:00/)).toBeNull();
    expect(r.queryByText(/23:00/)).toBeNull();
    // canonical raw ISO + timezone retained as the machine field (hover)
    const titled = r.container.querySelector('[title*="America/New_York"]') as HTMLElement;
    expect(titled).toBeTruthy();
    expect(titled.getAttribute("title")).toContain("2026-07-12T23:00:00Z");
  });

  it("plain (non-due) verify failures render verbatim", () => {
    const h = makeHarness("commit-preview");
    h.store.dispatch({ type: "ARM_LIVE" });
    h.store.dispatch({ type: "COMMIT_START" });
    h.store.dispatch({
      type: "COMMIT_DONE",
      report: {
        status: "failed",
        surfaces: [
          { system: "calendar", status: "failed", detail: "write refused" },
        ],
        verifyFailures: ["calendar: 3 expected events, 0 found"],
      },
    });
    const r = dockWithDrawer(h);
    expect(r.getByText(/3 expected events, 0 found/)).toBeTruthy();
  });
});
