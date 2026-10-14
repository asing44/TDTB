/* U7 page frame — the stepper header replaces the retired ScreenNav tab pill
   and the left rail's source-health chip. These tests cover the step
   activation, keyboard navigation, the source-health pill with a
   Refresh/Retry action, and the Connections link. */

import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent } from "@testing-library/preact";
import { makeHarness } from "./test-harness";
import { Header } from "./Header";
import { fingerprintFixedInputs } from "../model/fingerprint";
import { fixedInputsOf } from "../fixtures/scenarios";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

/** Dispatch a completed refresh at a fixed instant. */
function refreshOk(h: ReturnType<typeof makeHarness>, at: string): void {
  const inputs = h.store.getState().inputs!;
  h.store.dispatch({
    type: "SOURCE_REFRESH_OK",
    inputs,
    ledger: h.store.getState().ledger!,
    fingerprint: fingerprintFixedInputs(fixedInputsOf(inputs)),
    anchoredSourceFingerprint: inputs.anchoredSourceFingerprint,
    planningConfigFingerprint: inputs.planningConfigFingerprint,
    at,
  });
}

describe("U7 stepper header", () => {
  it("renders three steps bound to ui.screen with roving tabindex", () => {
    const h = makeHarness("ready");
    const r = h.ui(<Header />);
    const tabs = r.getAllByRole("tab") as HTMLElement[];
    expect(tabs.map((t) => t.getAttribute("aria-selected"))).toEqual([
      "false",
      "true",
      "false",
    ]);
    // Accessible names are the labels; the step number/check mark is decorative.
    expect(r.getByRole("tab", { name: "Set up day" })).toBeTruthy();
    expect(r.getByRole("tab", { name: "Plan tasks" })).toBeTruthy();
    expect(r.getByRole("tab", { name: "Commit" })).toBeTruthy();
    expect(tabs[1].tabIndex).toBe(0);
    expect(tabs[0].tabIndex).toBe(-1);
    expect(tabs[2].tabIndex).toBe(-1);
  });

  it("each step activates its screen", () => {
    const h = makeHarness("ready");
    const r = h.ui(<Header />);
    fireEvent.click(r.getByRole("tab", { name: "Set up day" }));
    expect(h.store.getState().ui.screen).toBe("setup");
    fireEvent.click(r.getByRole("tab", { name: "Plan tasks" }));
    expect(h.store.getState().ui.screen).toBe("plan");
  });

  it("shows ✓ on completed steps", () => {
    const h = makeHarness("sequenced");
    const r = h.ui(<Header />);
    expect(r.getByRole("tab", { name: "Set up day" }).textContent).toContain("✓");
    expect(r.getByRole("tab", { name: "Plan tasks" }).textContent).toContain("✓");
    expect(r.getByRole("tab", { name: "Commit" }).textContent).not.toContain("✓");
  });

  it("Arrow, Home, and End move the step selection and focus", () => {
    const h = makeHarness("ready");
    const r = h.ui(<Header />);
    const tablist = r.getByRole("tablist", { name: "Planning steps" });

    fireEvent.keyDown(tablist, { key: "ArrowLeft" });
    expect(h.store.getState().ui.screen).toBe("setup");
    expect(document.activeElement?.id).toBe("step-tab-setup");

    fireEvent.keyDown(tablist, { key: "End" });
    expect(document.activeElement?.id).toBe("step-tab-commit");

    fireEvent.keyDown(tablist, { key: "Home" });
    expect(document.activeElement?.id).toBe("step-tab-setup");

    fireEvent.keyDown(tablist, { key: "ArrowRight" });
    expect(h.store.getState().ui.screen).toBe("plan");
    expect(document.activeElement?.id).toBe("step-tab-plan");
  });

  it("Commit reflects the approval/commit phase and is never a separate screen", () => {
    const h = makeHarness("commit-preview");
    const r = h.ui(<Header />);
    expect(h.store.getState().ui.screen).toBe("plan");
    expect(h.store.getState().ui.approvalOpen).toBe(true);
    expect(r.getByRole("tab", { name: "Commit" }).getAttribute("aria-selected")).toBe("true");
  });

  it("activating Commit opens the approval flow without changing the screen", () => {
    const h = makeHarness("sequenced");
    const r = h.ui(<Header />);
    fireEvent.click(r.getByRole("tab", { name: "Commit" }));
    expect(h.store.getState().ui.screen).toBe("plan");
    expect(h.store.getState().ui.approvalOpen).toBe(true);
  });

  it("shows the degraded pill with a Retry wired to the refresh lifecycle", () => {
    const h = makeHarness("ready", (sc) => {
      sc.inputs.sourceHealth = "degraded";
    });
    const r = h.ui(<Header />);
    expect(r.getByText(/Sources degraded/)).toBeTruthy();
    const spy = vi.spyOn(h.controller, "refreshSources").mockResolvedValue();
    const retry = r.getByRole("button", { name: "Retry source refresh" });
    expect(retry.textContent).toBe("Retry");
    expect((retry as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(retry);
    expect(spy).toHaveBeenCalledTimes(1);
  });

  it("offers an enabled Refresh wired to the refresh lifecycle when sources are healthy", () => {
    const h = makeHarness("ready");
    const r = h.ui(<Header />);
    expect(r.getByText(/Sources healthy/)).toBeTruthy();
    expect(r.queryByRole("button", { name: "Retry source refresh" })).toBeNull();
    const refresh = r.getByRole("button", { name: "Refresh sources" });
    expect(refresh.textContent).toBe("Refresh");
    expect((refresh as HTMLButtonElement).disabled).toBe(false);
    const spy = vi.spyOn(h.controller, "refreshSources").mockResolvedValue();
    fireEvent.click(refresh);
    expect(spy).toHaveBeenCalledTimes(1);
  });

  it("keeps the Refresh action out of the row while a refresh is in flight", () => {
    // Parity with the existing Retry: the pill becomes the progress surface
    // and the action is not shown; the controller also no-ops while loading.
    const h = makeHarness("ready");
    const r = h.ui(<Header />);
    act(() => h.store.dispatch({ type: "SOURCE_REFRESH_START" }));
    expect(r.getByText(/Sources refreshing/)).toBeTruthy();
    expect(r.queryByRole("button", { name: "Refresh sources" })).toBeNull();
    expect(r.queryByRole("button", { name: "Retry source refresh" })).toBeNull();
  });

  it("appends the refresh age and a discovery tooltip to the pill", () => {
    vi.useFakeTimers({ now: new Date("2026-10-13T15:00:00") });
    try {
      const h = makeHarness("ready");
      const r = h.ui(<Header />);
      act(() => refreshOk(h, "2026-10-13T14:42:00"));
      const pill = r.getByRole("status");
      expect(pill.textContent).toContain("Sources healthy · 18m");
      const label = pill.getAttribute("aria-label") ?? "";
      expect(label).toContain("Sources last refreshed 18m");
      expect(label).toContain("Capacity > Details > Sources");
      expect(label).not.toContain("were skipped");
      expect(pill.getAttribute("title")).toBe(label);
    } finally {
      vi.useRealTimers();
    }
  });

  it("names the advisory skipped Capacities objects in a healthy pill", () => {
    const skipped =
      "skipped Capacities object bb95d80d-573c-4aff-8faa-9dccdd2617a1 — not plannable: No title";
    const h = makeHarness("ready", (sc) => {
      sc.inputs.sourceWarnings = [skipped];
    });
    const r = h.ui(<Header />);
    const pill = r.getByRole("status");
    expect(pill.textContent).toContain("Sources healthy · 1 skipped");
    const label = pill.getAttribute("aria-label") ?? "";
    expect(label).toContain("1 Capacities objects were skipped (not plannable).");
    expect(label).toContain("They are not in the plan.");
    expect(label).toContain("Capacity > Details > Sources.");
    expect(pill.getAttribute("title")).toBe(label);
    expect(pill.className).toContain("health-pill--healthy");
    expect(r.queryByRole("button", { name: "Retry source refresh" })).toBeNull();
  });

  it("falls back to the intake skipped count when it is larger than the warnings", () => {
    const skipped =
      "skipped Capacities object bb95d80d-573c-4aff-8faa-9dccdd2617a1 — not plannable: No title";
    const h = makeHarness("ready", (sc) => {
      sc.inputs.sourceWarnings = [skipped];
      sc.inputs.capacitiesIntake = {
        mode: "direct",
        state: "ok",
        generation: 1,
        installedAt: null,
        typeCheckTimes: {},
        coverage: { members: 3, evaluated: 3, malformed: 0, unreadable: 0 },
        unassignedCandidates: [],
        skipped: [
          { identity: "a", title: null, reason: "No title", detail: "", structureId: null, structureName: null },
          { identity: "b", title: null, reason: "No title", detail: "", structureId: null, structureName: null },
          { identity: "c", title: null, reason: "No title", detail: "", structureId: null, structureName: null },
        ],
      };
    });
    const r = h.ui(<Header />);
    expect(r.getByRole("status").textContent).toContain("Sources healthy · 3 skipped");
  });

  it("Connections opens the existing connections screen", () => {
    const h = makeHarness("ready");
    const r = h.ui(<Header />);
    fireEvent.click(r.getByRole("button", { name: "Connections" }));
    expect(h.store.getState().ui.screen).toBe("connections");
  });
});

describe("Commit step when nothing is staged", () => {
  it("is aria-disabled and explains why instead of silently doing nothing", () => {
    const h = makeHarness("ready");
    const r = h.ui(<Header />);
    const commit = r.getByRole("tab", { name: "Commit" });
    expect(commit.getAttribute("aria-disabled")).toBe("true");
    expect((commit.getAttribute("title") ?? "").length).toBeGreaterThan(10);
    fireEvent.click(commit);
    expect(h.store.getState().ui.approvalOpen).toBe(false);
  });
});
