/* The plan view links straight to the per-type admission section, which
   lives inside the Repair fold's technical details. One click sets the
   panel and section, opens the fold and shows the section. */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, waitFor } from "@testing-library/preact";
import { SettingsShell } from "./SettingsShell";
import { makeHarness } from "./test-harness";
import type { CapacitiesSettings } from "../model/types";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const CAP: CapacitiesSettings = {
  version: 1,
  revision: 0,
  persisted: true,
  nativeTaskAuto: { activeEnabled: true, dueEnabled: false, deadlineEnabled: false, deadlineHorizonDays: 2 },
  excluded: [],
  activeStructures: ["RootTask"],
  nativeTaskStructures: ["RootTask"],
  activeStatuses: ["Started"],
  assignedStructures: { RootTask: "assigned" },
  assignedOnStructures: {},
  availableStructures: ["RootTask", "Other"],
  structureTitles: { RootTask: "Task", Other: "Project" },
};

describe("hub · admission link", () => {
  it("opens the admission section from the plan view in one click", async () => {
    const h = makeHarness("ready");
    vi.spyOn(h.controller, "loadCapacitiesSettings").mockResolvedValue(CAP);
    h.store.dispatch({ type: "UI", patch: { settingsPanel: "plan" } });
    const rendered = h.ui(<SettingsShell />);
    fireEvent.click(rendered.getByRole("button", { name: "How each Capacities type gets in" }));
    expect(h.store.getState().ui.settingsPanel).toBe("repair");
    expect(h.store.getState().ui.settingsSection).toBe("admission-per-type");
    const heading = await waitFor(() => rendered.getByRole("heading", { name: "How each item type gets in" }));
    const section = heading.closest("section") as HTMLElement;
    expect(section.id || section.getAttribute("aria-labelledby")).toBeTruthy();
    expect(heading.id).toBe("capacities-sec-admission-per-type");
    expect(section.closest("[hidden]")).toBeNull();
  });

  it("names the three concepts at the top of the Capacities panel", async () => {
    const h = makeHarness("ready");
    vi.spyOn(h.controller, "loadCapacitiesSettings").mockResolvedValue(CAP);
    h.store.dispatch({ type: "UI", patch: { settingsPanel: "repair", settingsSection: "admission-per-type" } });
    const rendered = h.ui(<SettingsShell />);
    const list = await waitFor(() => rendered.getByRole("list", { name: "Which section to use" }));
    for (const name of ["How each item type gets in", "Automatic task rules", "Fine-tune by item type"]) {
      expect(list.textContent).toContain(name);
    }
  });
});
