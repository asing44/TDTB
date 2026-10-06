/* settings-url.test.tsx — deep-link and history contract for the settings
   destination. URL parameters carry navigation only: `settings` names the
   panel, `section` names an in-panel section. Unrelated parameters and the
   fixture hash survive untouched; unknown values open nothing; Back/Forward
   restore navigation. */

import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { cleanup, act, waitFor } from "@testing-library/preact";
import { App } from "./App";
import { SettingsUrlSync, parseSettingsSearch, serializeSettingsSearch } from "./settingsUrl";
import { makeHarness } from "./test-harness";

beforeEach(() => {
  window.history.replaceState(null, "", "/");
});

afterEach(() => {
  cleanup();
  window.history.replaceState(null, "", "/");
});

function mountRoot() {
  const h = makeHarness("ready");
  const r = h.ui(
    <>
      <SettingsUrlSync />
      <App />
    </>,
  );
  return { ...h, r };
}

describe("settings URL parsing", () => {
  it("reads known panels and the optional day section", () => {
    expect(parseSettingsSearch("")).toEqual({ panel: null, section: null });
    expect(parseSettingsSearch("?settings=day")).toEqual({ panel: "day", section: null });
    expect(parseSettingsSearch("?settings=day&section=captures")).toEqual({
      panel: "day",
      section: "captures",
    });
    expect(parseSettingsSearch("?settings=capacities")).toEqual({
      panel: "capacities",
      section: null,
    });
    expect(parseSettingsSearch("?settings=tags")).toEqual({ panel: "tags", section: null });
  });

  it("opens nothing for unknown values or orphan sections", () => {
    expect(parseSettingsSearch("?settings=bogus")).toEqual({ panel: null, section: null });
    expect(parseSettingsSearch("?section=captures")).toEqual({ panel: null, section: null });
    expect(parseSettingsSearch("?settings=")).toEqual({ panel: null, section: null });
  });

  it("drops the section for non-day panels", () => {
    expect(parseSettingsSearch("?settings=capacities&section=captures")).toEqual({
      panel: "capacities",
      section: null,
    });
  });
});

describe("settings URL serialization", () => {
  it("preserves unrelated parameters and replaces only the settings keys", () => {
    expect(
      serializeSettingsSearch("?keep=1&settings=day&section=captures&other=2", {
        panel: "tags",
        section: null,
      }),
    ).toBe("?keep=1&other=2&settings=tags");
  });

  it("carries the day section only with the day panel", () => {
    expect(serializeSettingsSearch("", { panel: "day", section: "captures" })).toBe(
      "?settings=day&section=captures",
    );
    expect(serializeSettingsSearch("", { panel: "capacities", section: "captures" })).toBe(
      "?settings=capacities",
    );
  });

  it("removes both keys on close without touching unrelated parameters", () => {
    expect(
      serializeSettingsSearch("?keep=1&settings=tags&section=captures", {
        panel: null,
        section: null,
      }),
    ).toBe("?keep=1");
    expect(serializeSettingsSearch("?settings=tags", { panel: null, section: null })).toBe("");
  });
});

describe("settings URL sync", () => {
  it("opens the deep-linked panel on boot", () => {
    window.history.replaceState(null, "", "/?settings=tags");
    const h = mountRoot();
    expect(h.store.getState().ui.settingsPanel).toBe("tags");
    expect(h.store.getState().ui.tagExclusionSettingsOpen).toBe(true);
  });

  it("opens nothing for an invalid deep link", () => {
    window.history.replaceState(null, "", "/?settings=bogus");
    const h = mountRoot();
    expect(h.store.getState().ui.settingsPanel).toBeNull();
    expect(h.r.container.querySelector('[role="dialog"]')).toBeNull();
  });

  it("pushes one owned entry on open and replaces it on panel navigation", () => {
    const h = mountRoot();
    const before = window.history.length;
    h.store.dispatch({ type: "UI", patch: { settingsPanel: "day" } });
    expect(window.location.search).toBe("?settings=day");
    expect(window.history.length).toBe(before + 1);

    h.store.dispatch({ type: "UI", patch: { settingsPanel: "tags" } });
    expect(window.location.search).toBe("?settings=tags");
    expect(window.history.length).toBe(before + 1);
  });

  it("closes the panel and cleans the parameter (owned entry)", async () => {
    const h = mountRoot();
    h.store.dispatch({ type: "UI", patch: { settingsPanel: "day" } });
    h.store.dispatch({ type: "UI", patch: { settingsPanel: null } });
    await waitFor(() => expect(window.location.search).toBe(""));
    expect(h.store.getState().ui.settingsPanel).toBeNull();
  });

  it("cleans a deep-linked parameter without walking away from the app", async () => {
    window.history.replaceState(null, "", "/?settings=tags");
    const h = mountRoot();
    expect(h.store.getState().ui.settingsPanel).toBe("tags");
    h.store.dispatch({ type: "UI", patch: { settingsPanel: null } });
    await waitFor(() => expect(window.location.search).toBe(""));
  });

  it("restores navigation with Back and Forward", async () => {
    const h = mountRoot();
    h.store.dispatch({ type: "UI", patch: { settingsPanel: "day" } });
    expect(window.location.search).toBe("?settings=day");

    window.history.back();
    await waitFor(() => expect(h.store.getState().ui.settingsPanel).toBeNull());

    window.history.forward();
    await waitFor(() => expect(h.store.getState().ui.settingsPanel).toBe("day"));
  });

  it("preserves the fixture hash", () => {
    window.history.replaceState(null, "", "/#ready");
    const h = mountRoot();
    h.store.dispatch({ type: "UI", patch: { settingsPanel: "day" } });
    expect(window.location.hash).toBe("#ready");
    expect(window.location.search).toBe("?settings=day");
  });

  it("preserves unrelated query parameters", () => {
    window.history.replaceState(null, "", "/?date=2026-01-01");
    const h = mountRoot();
    h.store.dispatch({ type: "UI", patch: { settingsPanel: "day" } });
    const params = new URLSearchParams(window.location.search);
    expect(params.get("date")).toBe("2026-01-01");
    expect(params.get("settings")).toBe("day");
  });
});

describe("settings URL sync alongside the cockpit", () => {
  it("does not mount a settings dialog until a destination opens", () => {
    const h = mountRoot();
    expect(h.r.container.querySelector('[role="dialog"]')).toBeNull();
    act(() => {
      h.store.dispatch({ type: "UI", patch: { setupOpen: true } });
    });
    expect(h.r.container.querySelectorAll('[role="dialog"]').length).toBe(1);
  });

  it("keeps approval state independent while settings is open", () => {
    const h = mountRoot();
    h.store.dispatch({ type: "UI", patch: { settingsPanel: "day", approvalOpen: true } });
    expect(h.store.getState().ui.approvalOpen).toBe(true);
    expect(h.store.getState().ui.settingsPanel).toBe("day");
  });
});
