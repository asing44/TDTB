/* settings-shell.test.tsx — the consolidated settings host (approved variant
   A). One dialog; tabs switch panels in place; every panel body stays mounted
   for the life of the shell so drafts survive switches, and the shell
   unmounting on close re-initializes each panel from current state on reopen.
   The cockpit shortcuts (rail Settings/Captures chips, ActionDock) all land
   in the same host. Keyboard semantics and focus destinations are stage 3. */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, waitFor } from "@testing-library/preact";
import { App } from "./App";
import { SettingsShell } from "./SettingsShell";
import { SettingsUrlSync } from "./settingsUrl";
import { makeHarness } from "./test-harness";
import type { CapacitiesSettings } from "../model/types";

beforeEach(() => {
  window.history.replaceState(null, "", "/");
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
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
    availableStructures: [],
    ...overrides,
  };
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

    fireEvent.click(rendered.getByRole("button", { name: "Tag exclusions" }));
    await waitFor(() => expect(rendered.getByText("Excluded tags")).toBeTruthy());
    expect(rendered.queryByText("Native Task Auto rules")).toBeNull();

    fireEvent.click(rendered.getByRole("button", { name: "Day setup" }));
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
    fireEvent.click(rendered.getByRole("button", { name: "Tag exclusions" }));
    fireEvent.click(rendered.getByRole("button", { name: "Capacities" }));
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

    fireEvent.click(rendered.getByRole("button", { name: "Capacities" }));
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
    await waitFor(() =>
      expect(rendered.getByRole("heading", { name: "Frame" })).toBeTruthy(),
    );
    expect(h.store.getState().ui.settingsPanel).toBe("day");
    expect(rendered.container.querySelectorAll('[role="dialog"]').length).toBe(1);
  });
});
