/* FEEDBACK-18 — Clarify Results and Preview writes hierarchy. Presentation-only
   polish: the drawer gives Preview writes and Results distinct headings, status
   strips, and primary next actions; exact writes, totals, blockers, and
   verification get a clear scan order; the dock entry point is statusful in
   every applicable state. Two-gate commit, exact-write disclosure, dialog
   focus, and 12-hour times stay intact. */

import { afterEach, describe, expect, it } from "vitest";
import { act, cleanup, fireEvent } from "@testing-library/preact";
import { readFileSync, existsSync } from "node:fs";
import { resolve } from "node:path";
import { ActionDock } from "./ActionDock";
import { ApprovalDrawer } from "./ApprovalDrawer";
import { makeHarness } from "./test-harness";

afterEach(cleanup);

// Resolve app.css from the frontend root regardless of vitest cwd.
const CSS_CANDIDATES = [
  resolve(process.cwd(), "app.css"),
  resolve(process.cwd(), "frontend", "app.css"),
  resolve(process.cwd(), "src", "app.css"),
];
const CSS_PATH = CSS_CANDIDATES.find((p) => existsSync(p)) ?? CSS_CANDIDATES[0];
const APP_CSS = readFileSync(CSS_PATH, "utf8");

function previewDrawer() {
  const h = makeHarness("commit-preview");
  const r = h.ui(<ApprovalDrawer />);
  return { h, r };
}

describe("FEEDBACK-18 — Preview writes and Results have distinct headings, status, and primary actions", () => {
  it("preview state: heading, current status strip, totals heading, and arm-live primary action", () => {
    const { r } = previewDrawer();
    expect(r.getByRole("heading", { name: "Preview writes" })).toBeTruthy();

    // Distinct status: the first scan line names the phase and what was NOT
    // yet written, with the active exact-write count (fixture: 6 of 7 rows).
    const status = r.container.querySelector(".drawer-status--preview") as HTMLElement;
    expect(status).toBeTruthy();
    expect(status.textContent).toContain("Preview current");
    expect(status.textContent).toContain("6 exact writes staged");
    expect(status.textContent).toContain("nothing written yet");

    // Totals have their own scan heading.
    expect(r.getByRole("heading", { name: "Writes by surface" })).toBeTruthy();

    // Primary next action stays the two-gate arm; no Results Done action yet.
    expect(r.getByText(/arm live commit/)).toBeTruthy();
    expect(r.queryByText("Done")).toBeNull();
  });

  it("results (verified) state: heading, verified status strip, Done primary action, no commit controls", () => {
    const h = makeHarness("verified");
    h.store.dispatch({ type: "UI", patch: { approvalOpen: true } });
    const r = h.ui(<ApprovalDrawer />);
    expect(r.getByRole("heading", { name: "Results" })).toBeTruthy();

    const status = r.container.querySelector(".drawer-status--ok") as HTMLElement;
    expect(status).toBeTruthy();
    expect(status.textContent).toContain("Committed and verified");

    // The detailed verification claim FEEDBACK-13/23 pinned is unchanged.
    expect(r.getByText(/All surfaces verified — zero failures/)).toBeTruthy();

    // Results primary action is a Done close — never a commit/arm control.
    const done = r.getByText("Done").closest("button")!;
    expect(done.className).toContain("btn--primary");
    expect(r.queryByText(/arm live commit/)).toBeNull();
    expect(r.queryByText(/Commit live/)).toBeNull();

    fireEvent.click(done);
    expect(h.store.getState().ui.approvalOpen).toBe(false);
  });

  it("partial results: failure status strip with the verification-failure count", () => {
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
    const r = h.ui(<ApprovalDrawer />);
    expect(r.getByRole("heading", { name: "Results" })).toBeTruthy();

    const status = r.container.querySelector(".drawer-status--partial") as HTMLElement;
    expect(status).toBeTruthy();
    expect(status.textContent).toContain("Commit incomplete");
    expect(status.textContent).toContain("1 verification failure");
    expect(r.getByText(/3 expected events, 0 found/)).toBeTruthy();
  });

  it("verification heading precedes the verification list in scan order", () => {
    const { r } = previewDrawer();
    const h = makeHarness("verified");
    h.store.dispatch({ type: "UI", patch: { approvalOpen: true } });
    const rv = h.ui(<ApprovalDrawer />);
    const html = rv.container.innerHTML;
    expect(html.indexOf(">Verification<")).toBeGreaterThan(0);
    expect(html.indexOf(">Verification<")).toBeLessThan(html.indexOf("verify-list"));
    r.unmount();
  });
});

describe("FEEDBACK-18 — dock entry point is statusful in every applicable state", () => {
  it("preview state Results carries the exact-write count in its sub-label", () => {
    const h = makeHarness("commit-preview");
    const r = h.ui(<ActionDock />);
    const results = r.getByRole("button", { name: /^Results/ });
    expect(results.className).toContain("btn--primary");
    expect(results.textContent).toContain("6 writes ready");
  });

  it("review state Results says the preview is not built yet", () => {
    const h = makeHarness("sequenced");
    const r = h.ui(<ActionDock />);
    const results = r.getByRole("button", { name: /^Results/ });
    expect(results.textContent).toContain("preview not built yet");
  });

  it("verified state Results keeps the statement-of-work sub-label", () => {
    const h = makeHarness("verified");
    const r = h.ui(<ActionDock />);
    const results = r.getByRole("button", { name: /^Results/ });
    expect(results.textContent).toContain("statement of work");
  });
});

describe("FEEDBACK-18 — scan hierarchy CSS and narrow-width no-overlap", () => {
  it("defines drawer status strips with distinct accent borders per state", () => {
    expect(APP_CSS).toMatch(/\.drawer-status\s*\{[^}]*border-left:\s*3px solid var\(--t-accent\)/);
    expect(APP_CSS).toMatch(/\.drawer-status--preview\s*\{[^}]*border-left-color:\s*var\(--t-accent\)/);
    expect(APP_CSS).toMatch(/\.drawer-status--ok\s*\{[^}]*border-left-color:\s*var\(--c-free\)/);
    expect(APP_CSS).toMatch(/\.drawer-status--partial\s*\{[^}]*border-left-color:\s*var\(--c-overflow\)/);
  });

  it("defines the Results Done area and a class-based Back spacing", () => {
    expect(APP_CSS).toMatch(/\.drawer-done\s*\{[^}]*border-top:/);
    expect(APP_CSS).toMatch(/\.commit-zone__back\s*\{[^}]*margin-left:\s*8px/);
  });

  it("wraps the armed commit row and stacks the status strip at narrow widths", () => {
    expect(APP_CSS).toMatch(
      /@media\s*\(max-width:\s*520px\)[\s\S]*\.commit-zone\s*\{[^}]*flex-wrap:\s*wrap/,
    );
    expect(APP_CSS).toMatch(
      /@media\s*\(max-width:\s*520px\)[\s\S]*\.drawer-status\s*\{[^}]*flex-direction:\s*column/,
    );
  });
});

describe("FEEDBACK-18 — preserved FEEDBACK-13/23 contracts", () => {
  it("exact-write disclosure still collapses by default and expands on click", () => {
    const { r } = previewDrawer();
    expect(r.container.querySelectorAll(".write-row").length).toBe(0);
    fireEvent.click(r.getByRole("button", { name: /Exact writes/ }));
    expect(r.container.querySelectorAll(".write-row").length).toBeGreaterThan(0);
  });

  it("expanded exact writes stay 12-hour with AM/PM", async () => {
    const { r } = previewDrawer();
    fireEvent.click(r.getByRole("button", { name: /Exact writes/ }));
    await act(async () => {});
    expect(r.getByText("schedule · Pick up prescription @ 4 PM")).toBeTruthy();
    expect(r.getByText("create-event · ⬜ Magic Mirror @ 10:45 AM")).toBeTruthy();
    const bare = (r.container.textContent ?? "").match(/\d{1,2}:\d{2}(?!\s*(AM|PM))/g);
    expect(bare).toBeNull();
  });

  it("two-gate commit is unchanged: arm reveals the separate live commit", () => {
    const h = makeHarness("commit-preview");
    const r = h.ui(<ApprovalDrawer />);
    fireEvent.click(r.getByText(/arm live commit/));
    expect(h.store.getState().liveArmed).toBe(true);
    expect(r.getByText(/Commit live — write to all surfaces/)).toBeTruthy();
    expect(r.getByText("Back")).toBeTruthy();
  });

  it("FEEDBACK-23 due verification stays 12-hour with canonical raw fields", () => {
    const h = makeHarness("commit-preview");
    h.store.dispatch({ type: "ARM_LIVE" });
    h.store.dispatch({ type: "COMMIT_START" });
    h.store.dispatch({
      type: "COMMIT_DONE",
      report: {
        status: "failed",
        surfaces: [{ system: "todoist", status: "failed", detail: "due mismatch" }],
        verifyFailures: ["todoist: 'Press' due mismatch (intent 7 PM, live 11 PM)"],
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
    const r = h.ui(<ApprovalDrawer />);
    expect(r.getByText(/Press — due verification: intent 7 PM, live 11 PM/)).toBeTruthy();
    expect(r.queryByText(/19:00/)).toBeNull();
    const titled = r.container.querySelector('[title*="America/New_York"]') as HTMLElement;
    expect(titled).toBeTruthy();
  });
});
