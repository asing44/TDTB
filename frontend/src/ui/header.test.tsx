/* U7 page frame — the stepper header replaces the retired ScreenNav tab pill
   and the left rail's source-health chip. These tests cover the step
   activation, keyboard navigation, the source-health pill with Retry, and the
   Connections link. */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent } from "@testing-library/preact";
import { makeHarness } from "./test-harness";
import { Header } from "./Header";

afterEach(() => cleanup());

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
    fireEvent.click(r.getByRole("button", { name: "Retry source refresh" }));
    expect(spy).toHaveBeenCalledTimes(1);
  });

  it("shows a healthy pill with no Retry when sources are healthy", () => {
    const h = makeHarness("ready");
    const r = h.ui(<Header />);
    expect(r.getByText(/Sources healthy/)).toBeTruthy();
    expect(r.queryByRole("button", { name: "Retry source refresh" })).toBeNull();
  });

  it("Connections opens the existing connections screen", () => {
    const h = makeHarness("ready");
    const r = h.ui(<Header />);
    fireEvent.click(r.getByRole("button", { name: "Connections" }));
    expect(h.store.getState().ui.screen).toBe("connections");
  });
});
