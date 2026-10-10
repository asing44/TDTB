/* B3 promoted screens: Plan tasks / Set up day / Connections are top-level
   tablist destinations that reuse the existing panels as screen bodies. */

import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent } from "@testing-library/preact";
import { makeHarness } from "./test-harness";
import { App } from "./App";
import { ScreenNav } from "./ScreenNav";

afterEach(() => cleanup());

describe("B3 promoted screens", () => {
  it("renders the three screen tabs with roving tabindex and selected state", () => {
    const h = makeHarness("ready");
    const r = h.ui(<ScreenNav />);
    const tabs = r.getAllByRole("tab") as HTMLElement[];
    expect(tabs.map((tab) => tab.textContent)).toEqual([
      "Plan tasks",
      "Set up day",
      "Connections",
    ]);
    expect(tabs[0].getAttribute("aria-selected")).toBe("true");
    expect(tabs[0].tabIndex).toBe(0);
    expect(tabs[1].tabIndex).toBe(-1);
    expect(tabs[2].tabIndex).toBe(-1);
  });

  it("ArrowRight moves the screen selection", () => {
    const h = makeHarness("ready");
    const r = h.ui(<ScreenNav />);
    fireEvent.keyDown(r.getByRole("tablist"), { key: "ArrowRight" });
    expect(h.store.getState().ui.screen).toBe("setup");
    fireEvent.keyDown(r.getByRole("tablist"), { key: "ArrowRight" });
    expect(h.store.getState().ui.screen).toBe("connections");
    fireEvent.keyDown(r.getByRole("tablist"), { key: "ArrowLeft" });
    expect(h.store.getState().ui.screen).toBe("setup");
  });

  it("switches the promoted screen body in App and returns to planning", () => {
    const h = makeHarness("ready");
    const r = h.ui(<App />);
    expect(r.getByLabelText("Today's planning evidence")).toBeTruthy();

    fireEvent.click(r.getByRole("tab", { name: "Set up day" }));
    expect(h.store.getState().ui.screen).toBe("setup");
    expect(r.getByLabelText("Set up day")).toBeTruthy();
    expect(r.getByRole("heading", { name: "Frame" })).toBeTruthy();

    fireEvent.click(r.getByRole("tab", { name: "Connections" }));
    expect(r.getByLabelText("Connections")).toBeTruthy();

    fireEvent.click(r.getByRole("tab", { name: "Plan tasks" }));
    expect(h.store.getState().ui.screen).toBe("plan");
    expect(r.getByLabelText("Today's planning evidence")).toBeTruthy();
  });

  it("selecting a promoted screen closes any contextual drawer", () => {
    const h = makeHarness("ready");
    const r = h.ui(<App />);
    h.store.dispatch({ type: "UI", patch: { settingsPanel: "day" } });
    expect(h.store.getState().ui.settingsPanel).toBe("day");
    fireEvent.click(r.getByRole("tab", { name: "Connections" }));
    expect(h.store.getState().ui.settingsPanel).toBeNull();
    expect(h.store.getState().ui.screen).toBe("connections");
  });
});
