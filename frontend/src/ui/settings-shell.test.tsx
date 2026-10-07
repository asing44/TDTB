/* settings-shell.test.tsx — the consolidated settings host (approved variant
   A). One dialog; tabs switch panels in place; every panel body stays mounted
   for the life of the shell so drafts survive switches, and the shell
   unmounting on close re-initializes each panel from current state on reopen.
   The cockpit shortcuts (rail Settings/Captures chips, ActionDock) all land
   in the same host. Keyboard semantics and focus destinations are stage 3. */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, waitFor } from "@testing-library/preact";
import { App } from "./App";
import { SettingsShell } from "./SettingsShell";
import { SettingsUrlSync } from "./settingsUrl";
import { makeHarness } from "./test-harness";
import type { CapacitiesSettings, TagExclusionSettings } from "../model/types";

beforeEach(() => {
  window.history.replaceState(null, "", "/");
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  document.documentElement.removeAttribute("data-theme");
  window.history.replaceState(null, "", "/");
});

function capacitiesFixture(
  overrides: Partial<CapacitiesSettings> = {},
): CapacitiesSettings {
  return {
    version: 1,
    revision: 0,
    persisted: true,
    nativeTaskAuto: {
      activeEnabled: true,
      dueEnabled: true,
      deadlineEnabled: true,
      deadlineHorizonDays: 2,
    },
    excluded: [],
    activeStructures: [],
    nativeTaskStructures: ["RootTask", "Task"],
    activeStatuses: ["active"],
    assignedStructures: {},
    availableStructures: [],
    structureTitles: {},
    ...overrides,
  };
}

function tagFixture(
  overrides: Partial<TagExclusionSettings> = {},
): TagExclusionSettings {
  return {
    version: 1,
    revision: 0,
    persisted: false,
    tags: [],
    catalog: { status: "complete", spaceId: "space-1", tags: [], warnings: [] },
    ...overrides,
  };
}

// CSS guards read app.css off disk (the same convention the row-spacing and
// a11y suites use) because jsdom does no real style resolution.
const appCss = readFileSync(resolve(process.cwd(), "src/app.css"), "utf8");

function cssBlock(selector: string): string {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const m = appCss.match(new RegExp(`${escaped}\\s*\\{([^}]*)\\}`));
  if (!m) throw new Error(`selector not found in app.css: ${selector}`);
  return m[1];
}

describe("SettingsShell", () => {
  it("mounts exactly one dialog and switches panels in place", async () => {
    const h = makeHarness("ready");
    vi.spyOn(h.controller, "loadCapacitiesSettings").mockResolvedValue(
      capacitiesFixture(),
    );
    h.store.dispatch({ type: "UI", patch: { settingsPanel: "capacities" } });
    const rendered = h.ui(<SettingsShell />);

    expect(rendered.container.querySelectorAll('[role="dialog"]').length).toBe(1);
    await waitFor(() =>
      expect(rendered.getByText("Native Task Auto rules")).toBeTruthy(),
    );
    expect(rendered.queryByText("Frame")).toBeNull();

    fireEvent.click(rendered.getByRole("tab", { name: "Tag exclusions" }));
    await waitFor(() => expect(rendered.getByText("Excluded tags")).toBeTruthy());
    expect(rendered.queryByText("Native Task Auto rules")).toBeNull();

    fireEvent.click(rendered.getByRole("tab", { name: "Day setup" }));
    await waitFor(() => expect(rendered.getByText("Frame")).toBeTruthy());
    expect(rendered.queryByText("Excluded tags")).toBeNull();
    expect(rendered.container.querySelectorAll('[role="dialog"]').length).toBe(1);
  });

  it("retains a panel draft across switches and re-initializes it on reopen", async () => {
    const h = makeHarness("ready");
    const load = vi
      .spyOn(h.controller, "loadCapacitiesSettings")
      .mockResolvedValue(capacitiesFixture({ revision: 4 }));
    h.store.dispatch({ type: "UI", patch: { settingsPanel: "capacities" } });
    const rendered = h.ui(<SettingsShell />);
    await waitFor(() =>
      expect(rendered.getByText("Native Task Auto rules")).toBeTruthy(),
    );

    const horizon = () =>
      (rendered.getByLabelText("Deadline horizon") as HTMLInputElement).value;
    fireEvent.input(rendered.getByLabelText("Deadline horizon"), {
      target: { value: "5" },
    });
    expect(horizon()).toBe("5");

    // Switching away and back keeps the shell mounted: the unsaved draft stays.
    fireEvent.click(rendered.getByRole("tab", { name: "Tag exclusions" }));
    fireEvent.click(rendered.getByRole("tab", { name: "Capacities" }));
    expect(horizon()).toBe("5");
    expect(load).toHaveBeenCalledTimes(1);

    // Closing unmounts the shell; reopening loads current state fresh.
    fireEvent.click(rendered.getByRole("button", { name: "Close settings" }));
    expect(rendered.container.querySelector('[role="dialog"]')).toBeNull();
    expect(h.store.getState().ui.settingsPanel).toBeNull();

    h.store.dispatch({ type: "UI", patch: { settingsPanel: "capacities" } });
    await waitFor(() => expect(horizon()).toBe("2"));
    expect(load).toHaveBeenCalledTimes(2);
  });

  it("reopens from current state rather than the discarded draft", async () => {
    const h = makeHarness("ready");
    vi.spyOn(h.controller, "loadCapacitiesSettings")
      .mockResolvedValueOnce(capacitiesFixture())
      .mockResolvedValue(
        capacitiesFixture({
          revision: 3,
          nativeTaskAuto: {
            activeEnabled: false,
            dueEnabled: false,
            deadlineEnabled: true,
            deadlineHorizonDays: 7,
          },
        }),
      );
    h.store.dispatch({ type: "UI", patch: { settingsPanel: "capacities" } });
    const rendered = h.ui(<SettingsShell />);
    await waitFor(() =>
      expect(rendered.getByText("Native Task Auto rules")).toBeTruthy(),
    );

    fireEvent.input(rendered.getByLabelText("Deadline horizon"), {
      target: { value: "5" },
    });
    fireEvent.click(rendered.getByRole("button", { name: "Close settings" }));
    h.store.dispatch({ type: "UI", patch: { settingsPanel: "capacities" } });

    await waitFor(() =>
      expect(
        (rendered.getByLabelText("Deadline horizon") as HTMLInputElement).value,
      ).toBe("7"),
    );
  });

  it("opens the same host from the rail Settings/Captures chips", async () => {
    const h = makeHarness("ready");
    const rendered = h.ui(
      <>
        <SettingsUrlSync />
        <App />
      </>,
    );

    fireEvent.click(rendered.getByRole("button", { name: "Open settings" }));
    await waitFor(() =>
      expect(rendered.getByRole("heading", { name: "Frame" })).toBeTruthy(),
    );
    expect(rendered.container.querySelectorAll('[role="dialog"]').length).toBe(1);
    expect(h.store.getState().ui.settingsPanel).toBe("day");

    fireEvent.click(rendered.getByRole("tab", { name: "Capacities" }));
    await waitFor(() =>
      expect(rendered.getByText("Native Task Auto rules")).toBeTruthy(),
    );
    expect(h.store.getState().ui.capacitiesSettingsOpen).toBe(true);

    fireEvent.click(rendered.getByRole("button", { name: "Close settings" }));
    await waitFor(() => expect(h.store.getState().ui.settingsPanel).toBeNull());

    fireEvent.click(
      rendered.getByRole("button", { name: "Open captures in day setup" }),
    );
    expect(h.store.getState().ui.settingsPanel).toBe("day");
    expect(h.store.getState().ui.settingsSection).toBe("captures");
    expect(rendered.getByRole("heading", { name: "Captures" })).toBeTruthy();
  });

  it("keeps the ActionDock Day setup shortcut on the one host", async () => {
    const h = makeHarness("ready");
    const rendered = h.ui(<App />);
    const dockSetup = rendered.container.querySelector(
      'footer[aria-label="Actions"] button[aria-label="Open day setup"]',
    ) as HTMLButtonElement;
    expect(dockSetup).toBeTruthy();

    fireEvent.click(dockSetup);
    const frame = await waitFor(() =>
      rendered.getByRole("heading", { name: "Frame" }),
    );
    await waitFor(() => expect(document.activeElement).toBe(frame));
    expect(h.store.getState().ui.settingsPanel).toBe("day");
    expect(rendered.container.querySelectorAll('[role="dialog"]').length).toBe(1);
  });
});

describe("SettingsShell accessibility (stage 3)", () => {
  it("exposes the switcher as a named tablist with tab/panel relationships", () => {
    const h = makeHarness("ready");
    h.store.dispatch({ type: "UI", patch: { settingsPanel: "day" } });
    const rendered = h.ui(<SettingsShell />);

    expect(rendered.getByRole("tablist", { name: "Settings panels" })).toBeTruthy();
    const tabs = rendered.getAllByRole("tab") as HTMLElement[];
    expect(tabs.map((tab) => tab.textContent)).toEqual([
      "Day setup",
      "Capacities",
      "Tag exclusions",
    ]);
    for (const tab of tabs) {
      const selected = tab.getAttribute("aria-selected") === "true";
      expect(tab.getAttribute("tabindex")).toBe(selected ? "0" : "-1");
      const panel = rendered.container.querySelector(
        `#${tab.getAttribute("aria-controls")}`,
      ) as HTMLElement;
      expect(panel).toBeTruthy();
      expect(panel.getAttribute("role")).toBe("tabpanel");
      expect(panel.getAttribute("aria-labelledby")).toBe(tab.id);
    }

    // One visible panel, named by the selected tab. The hidden wrappers are
    // empty so no inactive control can enter the dialog's Tab wrap.
    const activePanel = rendered.getByRole("tabpanel") as HTMLElement;
    expect(activePanel.getAttribute("aria-labelledby")).toBe(tabs[0].id);
    const hiddenPanels = Array.from(
      rendered.container.querySelectorAll('[role="tabpanel"][hidden]'),
    ) as HTMLElement[];
    expect(hiddenPanels.length).toBe(2);
    for (const hidden of hiddenPanels) {
      expect(
        hidden.querySelectorAll(
          'button, input, select, textarea, a[href], [tabindex]:not([tabindex="-1"])',
        ).length,
      ).toBe(0);
    }
  });

  it("moves selection with Arrow/Home/End across every panel, keeping focus on the tab", async () => {
    const h = makeHarness("ready");
    vi.spyOn(h.controller, "loadCapacitiesSettings").mockResolvedValue(capacitiesFixture());
    vi.spyOn(h.controller, "loadTagExclusionSettings").mockResolvedValue(tagFixture());
    h.store.dispatch({ type: "UI", patch: { settingsPanel: "day" } });
    const rendered = h.ui(<SettingsShell />);
    const tablist = rendered.getByRole("tablist", { name: "Settings panels" });
    const tab = (name: string) => rendered.getByRole("tab", { name });

    const expectSelected = async (name: string, panelText: string) => {
      await waitFor(() =>
        expect(tab(name).getAttribute("aria-selected")).toBe("true"),
      );
      expect(document.activeElement).toBe(tab(name));
      expect(tab(name).getAttribute("tabindex")).toBe("0");
      for (const other of ["Day setup", "Capacities", "Tag exclusions"]) {
        if (other !== name) expect(tab(other).getAttribute("tabindex")).toBe("-1");
      }
      await waitFor(() => expect(rendered.getByText(panelText)).toBeTruthy());
    };

    tab("Day setup").focus();
    await expectSelected("Day setup", "Frame");

    fireEvent.keyDown(tablist, { key: "ArrowRight" });
    await expectSelected("Capacities", "Native Task Auto rules");

    fireEvent.keyDown(tablist, { key: "ArrowRight" });
    await expectSelected("Tag exclusions", "Excluded tags");

    // Wrap in both directions.
    fireEvent.keyDown(tablist, { key: "ArrowRight" });
    await expectSelected("Day setup", "Frame");

    fireEvent.keyDown(tablist, { key: "ArrowLeft" });
    await expectSelected("Tag exclusions", "Excluded tags");

    fireEvent.keyDown(tablist, { key: "Home" });
    await expectSelected("Day setup", "Frame");

    fireEvent.keyDown(tablist, { key: "End" });
    await expectSelected("Tag exclusions", "Excluded tags");

    // A pointer switch keeps focus on the clicked tab as well.
    fireEvent.click(tab("Capacities"));
    await expectSelected("Capacities", "Native Task Auto rules");
  });

  it("lands programmatic navigation on the destination heading, including Captures", async () => {
    const h = makeHarness("ready");
    const rendered = h.ui(<App />);

    const settingsChip = rendered.getByRole("button", { name: "Open settings" });
    settingsChip.focus();
    fireEvent.click(settingsChip);
    const frame = await waitFor(() =>
      rendered.getByRole("heading", { name: "Frame" }),
    );
    await waitFor(() => expect(document.activeElement).toBe(frame));

    fireEvent.click(rendered.getByRole("button", { name: "Close settings" }));
    await waitFor(() => expect(h.store.getState().ui.settingsPanel).toBeNull());
    await waitFor(() => expect(document.activeElement).toBe(settingsChip));

    // The `settingsSection` shortcut lands on the named section heading, not
    // the first heading in the panel.
    const capturesChip = rendered.getByRole("button", {
      name: "Open captures in day setup",
    });
    capturesChip.focus();
    fireEvent.click(capturesChip);
    const captures = await waitFor(() =>
      rendered.getByRole("heading", { name: "Captures" }),
    );
    await waitFor(() => expect(document.activeElement).toBe(captures));
    expect(h.store.getState().ui.settingsPanel).toBe("day");
    expect(h.store.getState().ui.settingsSection).toBe("captures");
  });

  it("lands focus on an async panel's heading when its load resolves", async () => {
    const h = makeHarness("ready");
    let resolveLoad: (value: CapacitiesSettings) => void = () => {};
    vi.spyOn(h.controller, "loadCapacitiesSettings").mockImplementation(
      () =>
        new Promise<CapacitiesSettings>((resolve) => {
          resolveLoad = resolve;
        }),
    );
    h.store.dispatch({ type: "UI", patch: { settingsPanel: "capacities" } });
    const rendered = h.ui(<SettingsShell />);

    const activePanel = () =>
      rendered.container.querySelector('[role="tabpanel"]:not([hidden])') as HTMLElement;
    expect(document.activeElement).toBe(activePanel());
    expect(rendered.getByRole("status").textContent).toContain("Loading local policy");

    act(() => resolveLoad(capacitiesFixture()));
    const heading = await waitFor(() =>
      rendered.getByRole("heading", { name: "Native Task Auto rules" }),
    );
    await waitFor(() => expect(document.activeElement).toBe(heading));
  });

  it("restores focus to the original opener when the shell closes", async () => {
    const h = makeHarness("ready");
    const rendered = h.ui(<App />);
    const opener = rendered.getByRole("button", { name: "Open settings" });
    opener.focus();
    fireEvent.click(opener);
    const dialog = await waitFor(() =>
      rendered.getByRole("dialog", { name: "Settings" }),
    );

    fireEvent.click(rendered.getByRole("tab", { name: "Tag exclusions" }));
    await waitFor(() => expect(h.store.getState().ui.settingsPanel).toBe("tags"));

    fireEvent.keyDown(dialog, { key: "Escape" });
    await waitFor(() => expect(h.store.getState().ui.settingsPanel).toBeNull());
    await waitFor(() => expect(document.activeElement).toBe(opener));
  });

  it("keeps the focus trap on the active panel's controls", () => {
    const h = makeHarness("ready");
    h.store.dispatch({ type: "UI", patch: { settingsPanel: "day" } });
    const rendered = h.ui(<SettingsShell />);
    const dialog = rendered.getByRole("dialog", { name: "Settings" }) as HTMLElement;
    const focusables = Array.from(
      dialog.querySelectorAll<HTMLElement>(
        'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
      ),
    );
    expect(focusables.length).toBeGreaterThan(1);
    expect(
      dialog.querySelectorAll(
        '[role="tabpanel"][hidden] button, [role="tabpanel"][hidden] input, [role="tabpanel"][hidden] select, [role="tabpanel"][hidden] textarea',
      ).length,
    ).toBe(0);

    const first = focusables[0];
    const last = focusables[focusables.length - 1];
    last.focus();
    fireEvent.keyDown(dialog, { key: "Tab" });
    expect(document.activeElement).toBe(first);
    fireEvent.keyDown(dialog, { key: "Tab", shiftKey: true });
    expect(document.activeElement).toBe(last);
  });

  it("isolates the cockpit behind the shell while it owns focus", async () => {
    const h = makeHarness("ready");
    const rendered = h.ui(<App />);
    const background = rendered.container.querySelector(
      ".cockpit__background",
    ) as HTMLElement;
    expect(background).toBeTruthy();
    expect(background.hasAttribute("inert")).toBe(false);
    expect(background.getAttribute("aria-hidden")).toBeNull();

    fireEvent.click(rendered.getByRole("button", { name: "Open settings" }));
    const dialog = await waitFor(() =>
      rendered.getByRole("dialog", { name: "Settings" }),
    );
    expect(background.hasAttribute("inert")).toBe(true);
    expect(background.getAttribute("aria-hidden")).toBe("true");
    expect(background.contains(dialog)).toBe(false);
    expect(background.querySelector(".rail")).toBeTruthy();
    expect(background.querySelector(".cockpit__main")).toBeTruthy();

    fireEvent.click(rendered.getByRole("button", { name: "Close settings" }));
    await waitFor(() => expect(h.store.getState().ui.settingsPanel).toBeNull());
    expect(background.hasAttribute("inert")).toBe(false);
    expect(background.getAttribute("aria-hidden")).toBeNull();
  });

  it("renders the shell under both themes", async () => {
    for (const theme of ["light", "dark"] as const) {
      const h = makeHarness("ready");
      h.store.dispatch({ type: "THEME_SET", theme });
      const rendered = h.ui(<App />);
      await waitFor(() =>
        expect(document.documentElement.getAttribute("data-theme")).toBe(theme),
      );

      fireEvent.click(rendered.getByRole("button", { name: "Open settings" }));
      const dialog = await waitFor(() =>
        rendered.getByRole("dialog", { name: "Settings" }),
      );
      expect(dialog.querySelectorAll('[role="tab"]').length).toBe(3);
      expect(rendered.getByRole("tablist", { name: "Settings panels" })).toBeTruthy();
      rendered.unmount();
    }
  });
});

describe("settings shell styling guards", () => {
  it("uses theme tokens only, so both themes apply", () => {
    for (const selector of [
      ".settings-tabs",
      ".settings-tab",
      ".settings-tab--active",
      ".settings-tab:focus-visible",
    ]) {
      const block = cssBlock(selector);
      expect(block, selector).not.toMatch(/#[0-9a-fA-F]{3,8}\b|\brgba?\(/);
      expect(block, selector).toMatch(/var\(--t-/);
    }
  });

  it("adds no motion of its own under reduced motion", () => {
    for (const selector of [
      ".settings-tabs",
      ".settings-tab",
      ".settings-tab--active",
    ]) {
      expect(cssBlock(selector), selector).not.toMatch(/animation|transition/);
    }
  });

  it("wraps the switcher and caps the shell at narrow widths (no overlap)", () => {
    expect(cssBlock(".settings-tabs")).toMatch(/flex-wrap:\s*wrap/);
    expect(cssBlock(".settings-tab")).toMatch(/min-height:\s*44px/);
    expect(appCss).toMatch(/\.setup-drawer\s*\{[^}]*width:\s*min\(520px,\s*100vw\)/);
  });
});
