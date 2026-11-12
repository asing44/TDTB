/* settings-shell.test.tsx — the Settings dialog chrome around the hub. One
   dialog holding one scrolling page; jump buttons move between sections;
   every section stays mounted for the life of the shell so drafts survive
   jumps, and the shell unmounting on close re-initializes each section from
   current state on reopen. The cockpit shortcuts (rail Settings/Captures
   chips, ActionDock) all land in the same host. Page structure itself is
   covered in settings-hub.test.tsx. */

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
    version: 2,
    revision: 0,
    persisted: true,
    statuses: { open: ["active"], completed: ["Completed", "Dropped"] },
    types: {
      RootTask: { mode: "native", property: null, statuses: null },
      Task: { mode: "native", property: null, statuses: null },
    },
    native: { deadlineHorizonDays: 2 },
    excluded: [],
    availableStructures: [],
    structureTitles: {},
    declarationDiagnostics: [],
    migratedFrom: null,
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

/** A jump-bar button by its label (Day, Habits, Plan contents, Sources, Advanced). */
const entry = (r: ReturnType<Harness["ui"]>, name: string) =>
  r.getByRole("button", { name: new RegExp(`^${name}`, "i") });

/** Advanced > Repair tools > Show technical details. */
function openTechnical(r: ReturnType<Harness["ui"]>) {
  fireEvent.click(r.getByRole("button", { name: /repair tools/i }));
  fireEvent.click(r.getByRole("button", { name: /show technical details/i }));
}

describe("SettingsShell", () => {
  it("mounts exactly one dialog with every section on one page", async () => {
    const h = makeHarness("ready");
    vi.spyOn(h.controller, "loadTagExclusionSettings").mockResolvedValue(tagFixture());
    h.store.dispatch({ type: "UI", patch: { settingsPanel: "freshness" } });
    const rendered = h.ui(<SettingsShell />);

    expect(rendered.container.querySelectorAll('[role="dialog"]').length).toBe(1);
    // Nothing hides behind a tab: Day and Plan contents render beside Sources.
    await waitFor(() => expect(rendered.getByText("Excluded tags")).toBeTruthy());
    expect(rendered.getByText("Frame")).toBeTruthy();
    expect(rendered.getByText("Capacities refresh")).toBeTruthy();

    fireEvent.click(entry(rendered, "Plan contents"));
    fireEvent.click(entry(rendered, "Day"));
    expect(rendered.getByText("Frame")).toBeTruthy();
    expect(rendered.getByText("Excluded tags")).toBeTruthy();
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
    // Schema v2 puts the one rule section in place of the retired
    // "Automatic task rules" section; the heading is the panel-ready signal.
    await waitFor(() =>
      expect(
        rendered.getByRole("heading", { name: "How each item type gets in" }),
      ).toBeTruthy(),
    );

    const horizon = () =>
      (rendered.getByLabelText("Deadline coming up") as HTMLInputElement).value;
    fireEvent.input(rendered.getByLabelText("Deadline coming up"), {
      target: { value: "5" },
    });
    expect(horizon()).toBe("5");

    // Jumping away and back keeps the shell mounted: the unsaved draft stays
    // and nothing reloads.
    const loadsBeforeJump = load.mock.calls.length;
    fireEvent.click(entry(rendered, "Day"));
    fireEvent.click(entry(rendered, "Advanced"));
    expect(horizon()).toBe("5");
    expect(load).toHaveBeenCalledTimes(loadsBeforeJump);

    // Closing unmounts the shell; reopening loads current state fresh.
    fireEvent.click(rendered.getByRole("button", { name: "Close settings" }));
    expect(rendered.container.querySelector('[role="dialog"]')).toBeNull();
    expect(h.store.getState().ui.settingsPanel).toBeNull();

    h.store.dispatch({ type: "UI", patch: { settingsPanel: "repair" } });
    await waitFor(() => rendered.getByRole("dialog", { name: "Settings" }));
    openTechnical(rendered);
    await waitFor(() => expect(horizon()).toBe("2"));
    expect(load.mock.calls.length).toBeGreaterThan(loadsBeforeJump);
  });

  it("reopens from current state rather than the discarded draft", async () => {
    const h = makeHarness("ready");
    vi.spyOn(h.controller, "loadCapacitiesSettings")
      .mockResolvedValueOnce(capacitiesFixture())
      .mockResolvedValue(
        capacitiesFixture({
          revision: 3,
          native: { deadlineHorizonDays: 7 },
        }),
      );
    h.store.dispatch({ type: "UI", patch: { settingsPanel: "repair" } });
    const rendered = h.ui(<SettingsShell />);
    openTechnical(rendered);
    // Schema v2 puts the one rule section in place of the retired
    // "Automatic task rules" section; the heading is the panel-ready signal.
    await waitFor(() =>
      expect(
        rendered.getByRole("heading", { name: "How each item type gets in" }),
      ).toBeTruthy(),
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

    fireEvent.click(entry(rendered, "Advanced"));
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
    const dayHeading = await waitFor(() =>
      rendered.getByRole("heading", { name: "Day" }),
    );
    await waitFor(() => expect(document.activeElement).toBe(dayHeading));
    expect(h.store.getState().ui.settingsPanel).toBe("day");
    expect(rendered.container.querySelectorAll('[role="dialog"]').length).toBe(1);
  });
});

describe("SettingsShell accessibility", () => {
  it("exposes a named jump nav of five buttons and five labelled sections", () => {
    const h = makeHarness("ready");
    h.store.dispatch({ type: "UI", patch: { settingsPanel: "day" } });
    const rendered = h.ui(<SettingsShell />);

    const nav = rendered.getByRole("navigation", { name: "Jump to a section" });
    const buttons = Array.from(nav.querySelectorAll<HTMLElement>(".settings-hub__jump-btn"));
    expect(buttons.length).toBe(5);
    for (const btn of buttons) {
      expect(btn.tagName).toBe("BUTTON");
      expect(btn.getAttribute("aria-current") === "location").toBe(
        btn.getAttribute("data-entry") === "day",
      );
    }
    const sections = Array.from(
      rendered.container.querySelectorAll<HTMLElement>(".settings-hub > section"),
    );
    expect(sections.length).toBe(5);
    for (const section of sections) {
      const label = section.getAttribute("aria-labelledby")!;
      expect(rendered.container.querySelector(`#${label}`), section.id).toBeTruthy();
    }
  });

  it("lands programmatic navigation on the destination heading, including Captures", async () => {
    const h = makeHarness("ready");
    const rendered = h.ui(<App />);

    const settingsChip = rendered.getByRole("button", { name: "Open settings" });
    settingsChip.focus();
    fireEvent.click(settingsChip);
    const dayHeading = await waitFor(() =>
      rendered.getByRole("heading", { name: "Day" }),
    );
    await waitFor(() => expect(document.activeElement).toBe(dayHeading));

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

  it("keeps focus on a pressed jump button instead of moving to the heading", async () => {
    const h = makeHarness("ready");
    h.store.dispatch({ type: "UI", patch: { settingsPanel: "day" } });
    const rendered = h.ui(<SettingsShell />);
    const plan = entry(rendered, "Plan contents");
    plan.focus();
    fireEvent.click(plan);
    await waitFor(() => expect(h.store.getState().ui.settingsPanel).toBe("plan"));
    expect(document.activeElement).toBe(plan);
    expect(plan.getAttribute("aria-current")).toBe("location");
  });

  it("lands focus on the destination heading at once, even while a panel still loads", async () => {
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

    const heading = rendered.getByRole("heading", { name: "Plan contents" });
    expect(document.activeElement).toBe(heading);

    // The late load fills the section in without stealing focus back.
    act(() => resolveLoad(tagFixture()));
    await waitFor(() => rendered.getByRole("heading", { name: "Excluded tags" }));
    expect(document.activeElement).toBe(heading);
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

    fireEvent.click(entry(rendered, "Plan contents"));
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
      expect(dialog.querySelectorAll(".settings-hub__jump-btn").length).toBe(5);
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
