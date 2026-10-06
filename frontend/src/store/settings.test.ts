/* settings.test.ts — the canonical settings destination contract.
   One settings host: `ui.settingsPanel` is the single source of truth and the
   legacy `*Open` flags become one-hot compatibility projections that existing
   drawers and tests keep using. Unknown/absent destinations open nothing. */

import { describe, expect, it } from "vitest";
import { createStore } from "./createStore";
import { isSettingsPanel } from "./store";

describe("canonical settings destination — legacy normalization", () => {
  it("normalizes each legacy open flag into the canonical panel", () => {
    const s = createStore();
    s.dispatch({ type: "UI", patch: { setupOpen: true } });
    expect(s.getState().ui.settingsPanel).toBe("day");

    s.dispatch({ type: "UI", patch: { capacitiesSettingsOpen: true } });
    expect(s.getState().ui.settingsPanel).toBe("capacities");

    s.dispatch({ type: "UI", patch: { tagExclusionSettingsOpen: true } });
    expect(s.getState().ui.settingsPanel).toBe("tags");
  });

  it("keeps the compatibility flags one-hot", () => {
    const s = createStore();
    s.dispatch({ type: "UI", patch: { setupOpen: true } });
    expect(s.getState().ui).toMatchObject({
      setupOpen: true,
      capacitiesSettingsOpen: false,
      tagExclusionSettingsOpen: false,
    });

    s.dispatch({ type: "UI", patch: { capacitiesSettingsOpen: true } });
    expect(s.getState().ui).toMatchObject({
      setupOpen: false,
      capacitiesSettingsOpen: true,
      tagExclusionSettingsOpen: false,
    });

    s.dispatch({ type: "UI", patch: { tagExclusionSettingsOpen: true } });
    expect(s.getState().ui).toMatchObject({
      setupOpen: false,
      capacitiesSettingsOpen: false,
      tagExclusionSettingsOpen: true,
    });
  });

  it("one legacy open replaces the previous destination (no coexisting drawers)", () => {
    const s = createStore();
    s.dispatch({ type: "UI", patch: { setupOpen: true } });
    s.dispatch({ type: "UI", patch: { tagExclusionSettingsOpen: true } });
    expect(s.getState().ui.settingsPanel).toBe("tags");
    expect(s.getState().ui.setupOpen).toBe(false);
  });

  it("closes only when the falsy legacy flag names the open panel", () => {
    const s = createStore();
    s.dispatch({ type: "UI", patch: { setupOpen: true } });

    s.dispatch({ type: "UI", patch: { tagExclusionSettingsOpen: false } });
    expect(s.getState().ui.settingsPanel).toBe("day");

    s.dispatch({ type: "UI", patch: { setupOpen: false } });
    expect(s.getState().ui.settingsPanel).toBeNull();
    expect(s.getState().ui).toMatchObject({
      setupOpen: false,
      capacitiesSettingsOpen: false,
      tagExclusionSettingsOpen: false,
    });
  });

  it("canonical writes drive every facade flag", () => {
    const s = createStore();
    s.dispatch({ type: "UI", patch: { settingsPanel: "capacities" } });
    expect(s.getState().ui).toMatchObject({
      settingsPanel: "capacities",
      setupOpen: false,
      capacitiesSettingsOpen: true,
      tagExclusionSettingsOpen: false,
    });

    s.dispatch({ type: "UI", patch: { settingsPanel: null } });
    expect(s.getState().ui).toMatchObject({
      settingsPanel: null,
      setupOpen: false,
      capacitiesSettingsOpen: false,
      tagExclusionSettingsOpen: false,
    });
  });

  it("rejects unknown destinations and opens nothing", () => {
    const s = createStore();
    s.dispatch({
      type: "UI",
      patch: { settingsPanel: "bogus" as never },
    });
    expect(s.getState().ui.settingsPanel).toBeNull();
    expect(s.getState().ui.setupOpen).toBe(false);
  });
});

describe("canonical settings destination — sections", () => {
  it("carries a section with day navigation", () => {
    const s = createStore();
    s.dispatch({
      type: "UI",
      patch: { settingsPanel: "day", settingsSection: "captures" },
    });
    expect(s.getState().ui.settingsPanel).toBe("day");
    expect(s.getState().ui.settingsSection).toBe("captures");

    // The legacy Captures entry point keeps the same destination contract.
    s.dispatch({ type: "UI", patch: { settingsPanel: null } });
    s.dispatch({
      type: "UI",
      patch: { setupOpen: true, settingsSection: "captures" },
    });
    expect(s.getState().ui.settingsPanel).toBe("day");
    expect(s.getState().ui.settingsSection).toBe("captures");
  });

  it("clears the section when the panel changes and on close", () => {
    const s = createStore();
    s.dispatch({
      type: "UI",
      patch: { settingsPanel: "day", settingsSection: "captures" },
    });
    s.dispatch({ type: "UI", patch: { settingsPanel: "tags" } });
    expect(s.getState().ui.settingsSection).toBeNull();

    s.dispatch({
      type: "UI",
      patch: { settingsPanel: "day", settingsSection: "captures" },
    });
    s.dispatch({ type: "UI", patch: { settingsPanel: null } });
    expect(s.getState().ui.settingsSection).toBeNull();
  });
});

describe("canonical settings destination — independence", () => {
  it("keeps approval and editor patches independent of settings", () => {
    const s = createStore();
    s.dispatch({
      type: "UI",
      patch: { settingsPanel: "tags", approvalOpen: true, editorItem: "Magic Mirror" },
    });
    expect(s.getState().ui).toMatchObject({
      settingsPanel: "tags",
      tagExclusionSettingsOpen: true,
      approvalOpen: true,
      editorItem: "Magic Mirror",
    });

    s.dispatch({ type: "UI", patch: { approvalOpen: false } });
    expect(s.getState().ui.settingsPanel).toBe("tags");

    s.dispatch({ type: "UI", patch: { settingsPanel: null } });
    expect(s.getState().ui.approvalOpen).toBe(false);
    expect(s.getState().ui.editorItem).toBe("Magic Mirror");
  });
});

describe("isSettingsPanel", () => {
  it("accepts exactly the three destinations", () => {
    expect(isSettingsPanel("day")).toBe(true);
    expect(isSettingsPanel("capacities")).toBe(true);
    expect(isSettingsPanel("tags")).toBe(true);
    expect(isSettingsPanel("settings")).toBe(false);
    expect(isSettingsPanel("")).toBe(false);
    expect(isSettingsPanel(null)).toBe(false);
    expect(isSettingsPanel(undefined)).toBe(false);
  });
});
