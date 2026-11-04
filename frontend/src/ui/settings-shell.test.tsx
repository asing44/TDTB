/* settings-shell.test.tsx — the Settings dialog chrome around the hub. One
   dialog; entries open their panels in place; every panel stays mounted for
   the life of the shell so drafts survive switches, and the shell unmounting
   on close re-initializes each panel from current state on reopen. The
   cockpit shortcuts (rail Settings/Captures chips, ActionDock) all land in
   the same host. Entry structure itself is covered in settings-hub.test.tsx. */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, waitFor } from "@testing-library/preact";
import { App } from "./App";
import { SettingsShell } from "./SettingsShell";
import { SettingsUrlSync } from "./settingsUrl";
import { makeHarness as stageHarness, type Harness } from "./test-harness";
import type { CapacitiesSettings, TagExclusionSettings } from "../model/types";

/** Cockpit-entry tests reach the shell through the Plan-surface shortcuts
    (rail chips, ActionDock). The app now boots on Set up day, so stage the
    scenario and explicitly land on the Plan screen. Settings-shell tests that
    render the host directly are unaffected. */
function makeHarness(
  name: Parameters<typeof stageHarness>[0],
  mutate?: Parameters<typeof stageHarness>[1],
): Harness {
  const h = stageHarness(name, mutate);
  h.store.dispatch({ type: "UI", patch: { screen: "plan", settingsPanel: null } });
  return h;
}

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
    assignedOnStructures: {},
    availableStructures: [],
    structureTitles: {},
    declarationDiagnostics: [],
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
    todoist: [],
    catalog: { status: "complete", spaceId: "space-1", tags: [], warnings: [] },
    ...overrides,
  };
}

const hubCss = readFileSync(resolve(process.cwd(), "src/ui/settings-hub.css"), "utf8");

const FOCUSABLE =
  'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

const entry = (r: ReturnType<Harness["ui"]>, name: string) =>
  r.getByRole("button", { name: new RegExp(name, "i") });

/** Open Repair connections > Repair tools > Show technical details. */
function openTechnical(r: ReturnType<Harness["ui"]>) {
  const repair = entry(r, "Repair connections");
  if (repair.getAttribute("aria-expanded") !== "true") fireEvent.click(repair);
  fireEvent.click(r.getByRole("button", { name: /repair tools/i }));
  fireEvent.click(r.getByRole("button", { name: /show technical details/i }));
}

describe("SettingsShell", () => {
  it("mounts exactly one dialog and switches panels in place", async () => {
    const h = makeHarness("ready");
    vi.spyOn(h.controller, "loadTagExclusionSettings").mockResolvedValue(tagFixture());
    h.store.dispatch({ type: "UI", patch: { settingsPanel: "freshness" } });
    const rendered = h.ui(<SettingsShell />);

    expect(rendered.container.querySelectorAll('[role="dialog"]').length).toBe(1);
    expect(rendered.queryByText("Frame")).toBeNull();

    fireEvent.click(entry(rendered, "What shows up in my plan"));
    await waitFor(() => expect(rendered.getByText("Excluded tags")).toBeTruthy());

    fireEvent.click(entry(rendered, "Change my day setup"));
    await waitFor(() => expect(rendered.getByText("Frame")).toBeTruthy());
    expect(rendered.queryByText("Excluded tags")).toBeNull();
    expect(rendered.container.querySelectorAll('[role="dialog"]').length).toBe(1);
  });

  it("retains a panel draft across switches and re-initializes it on reopen", async () => {
    const h = makeHarness("ready");
    const load = vi
      .spyOn(h.controller, "loadCapacitiesSettings")
      .mockResolvedValue(capacitiesFixture({ revision: 4 }));
    h.store.dispatch({ type: "UI", patch: { settingsPanel: "repair" } });
    const rendered = h.ui(<SettingsShell />);
    openTechnical(rendered);
    await waitFor(() =>
      expect(rendered.getByText("Automatic task rules")).toBeTruthy(),
    );

    const horizon = () =>
      (rendered.getByLabelText("Deadline coming up") as HTMLInputElement).value;
    fireEvent.input(rendered.getByLabelText("Deadline coming up"), {
      target: { value: "5" },
    });
    expect(horizon()).toBe("5");

    // Switching away and back keeps the shell mounted: the unsaved draft stays.
    fireEvent.click(entry(rendered, "Change my day setup"));
    fireEvent.click(entry(rendered, "Repair connections"));
    expect(horizon()).toBe("5");
    expect(load).toHaveBeenCalledTimes(1);

    // Closing unmounts the shell; reopening loads current state fresh.
    fireEvent.click(rendered.getByRole("button", { name: "Close settings" }));
    expect(rendered.container.querySelector('[role="dialog"]')).toBeNull();
    expect(h.store.getState().ui.settingsPanel).toBeNull();

    h.store.dispatch({ type: "UI", patch: { settingsPanel: "repair" } });
    await waitFor(() => rendered.getByRole("dialog", { name: "Settings" }));
    openTechnical(rendered);
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
    h.store.dispatch({ type: "UI", patch: { settingsPanel: "repair" } });
    const rendered = h.ui(<SettingsShell />);
    openTechnical(rendered);
    await waitFor(() =>
      expect(rendered.getByText("Automatic task rules")).toBeTruthy(),
    );

    fireEvent.input(rendered.getByLabelText("Deadline coming up"), {
      target: { value: "5" },
    });
    fireEvent.click(rendered.getByRole("button", { name: "Close settings" }));
    h.store.dispatch({ type: "UI", patch: { settingsPanel: "repair" } });
    await waitFor(() => rendered.getByRole("dialog", { name: "Settings" }));
    openTechnical(rendered);

    await waitFor(() =>
      expect(
        (rendered.getByLabelText("Deadline coming up") as HTMLInputElement).value,
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

    fireEvent.click(entry(rendered, "Repair connections"));
    expect(h.store.getState().ui.settingsPanel).toBe("repair");

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

describe("SettingsShell accessibility", () => {
  it("exposes the entries as buttons with aria-expanded/aria-controls and one open region", () => {
    const h = makeHarness("ready");
    h.store.dispatch({ type: "UI", patch: { settingsPanel: "day" } });
    const rendered = h.ui(<SettingsShell />);

    const toggles = Array.from(
      rendered.container.querySelectorAll<HTMLElement>(".settings-hub__toggle"),
    );
    expect(toggles.length).toBe(5);
    for (const toggle of toggles) {
      const expanded = toggle.getAttribute("aria-expanded") === "true";
      expect(expanded).toBe(toggle.getAttribute("data-entry") === "day");
      const region = rendered.container.querySelector(
        `#${toggle.getAttribute("aria-controls")}`,
      ) as HTMLElement;
      expect(region.getAttribute("role")).toBe("region");
      expect(region.getAttribute("aria-labelledby")).toBe(toggle.id);
      expect(region.hasAttribute("hidden")).toBe(!expanded);
    }
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

  it("keeps focus on a pressed entry instead of jumping to the heading", async () => {
    const h = makeHarness("ready");
    h.store.dispatch({ type: "UI", patch: { settingsPanel: "day" } });
    const rendered = h.ui(<SettingsShell />);
    const plan = entry(rendered, "What shows up in my plan");
    plan.focus();
    fireEvent.click(plan);
    await waitFor(() => expect(h.store.getState().ui.settingsPanel).toBe("plan"));
    expect(document.activeElement).toBe(plan);
    expect(plan.getAttribute("aria-expanded")).toBe("true");
  });

  it("lands focus on an async panel's heading when its load resolves", async () => {
    const h = makeHarness("ready");
    let resolveLoad: (value: TagExclusionSettings) => void = () => {};
    vi.spyOn(h.controller, "loadTagExclusionSettings").mockImplementation(
      () =>
        new Promise<TagExclusionSettings>((resolve) => {
          resolveLoad = resolve;
        }),
    );
    h.store.dispatch({ type: "UI", patch: { settingsPanel: "plan" } });
    const rendered = h.ui(<SettingsShell />);

    const activePanel = () =>
      rendered.container.querySelector(".settings-hub__panel:not([hidden])") as HTMLElement;
    expect(document.activeElement).toBe(activePanel());

    act(() => resolveLoad(tagFixture()));
    const heading = await waitFor(() =>
      rendered.getByRole("heading", { name: "Excluded tags" }),
    );
    await waitFor(() => expect(document.activeElement).toBe(heading));
  });

  it("closes on Escape and restores focus to the original opener", async () => {
    const h = makeHarness("ready");
    const rendered = h.ui(<App />);
    const opener = rendered.getByRole("button", { name: "Open settings" });
    opener.focus();
    fireEvent.click(opener);
    const dialog = await waitFor(() =>
      rendered.getByRole("dialog", { name: "Settings" }),
    );

    fireEvent.click(entry(rendered, "What shows up in my plan"));
    await waitFor(() => expect(h.store.getState().ui.settingsPanel).toBe("plan"));

    fireEvent.keyDown(dialog, { key: "Escape" });
    await waitFor(() => expect(h.store.getState().ui.settingsPanel).toBeNull());
    await waitFor(() => expect(document.activeElement).toBe(opener));
  });

  it("keeps the focus trap off hidden controls, folds included", () => {
    for (const panel of ["freshness", "plan", "repair", "day"] as const) {
      const h = makeHarness("ready");
      h.store.dispatch({ type: "UI", patch: { settingsPanel: panel } });
      const rendered = h.ui(<SettingsShell />);
      const dialog = rendered.getByRole("dialog", { name: "Settings" }) as HTMLElement;
      const focusables = Array.from(dialog.querySelectorAll<HTMLElement>(FOCUSABLE));
      expect(focusables.length, panel).toBeGreaterThan(1);
      for (const el of focusables) {
        expect(el.closest("[hidden]"), `${panel}: ${el.outerHTML.slice(0, 80)}`).toBeNull();
      }

      const first = focusables[0];
      const last = focusables[focusables.length - 1];
      last.focus();
      fireEvent.keyDown(dialog, { key: "Tab" });
      expect(document.activeElement).toBe(first);
      fireEvent.keyDown(dialog, { key: "Tab", shiftKey: true });
      expect(document.activeElement).toBe(last);
      rendered.unmount();
    }
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
      expect(dialog.querySelectorAll(".settings-hub__toggle").length).toBe(5);
      rendered.unmount();
    }
  });
});

describe("settings hub styling guards", () => {
  it("uses theme tokens only, so both themes apply", () => {
    expect(hubCss).not.toMatch(/#[0-9a-fA-F]{3,8}\b|\brgba?\(/);
    expect(hubCss).toMatch(/var\(--t-/);
  });

  it("adds no motion of its own under reduced motion", () => {
    expect(hubCss).not.toMatch(/animation|transition/);
  });

  it("caps the shell at narrow widths (no overlap)", () => {
    const appCss = readFileSync(resolve(process.cwd(), "src/app.css"), "utf8");
    expect(appCss).toMatch(/\.setup-drawer\s*\{[^}]*width:\s*min\(520px,\s*100vw\)/);
  });
});
