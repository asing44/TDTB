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
  version: 2,
  revision: 0,
  persisted: true,
  statuses: { open: ["Started"], completed: ["Completed", "Dropped"] },
  types: {
    RootTask: { mode: "native", property: null, statuses: null },
  },
  native: { deadlineHorizonDays: 2 },
  excluded: [],
  availableStructures: ["RootTask", "Other"],
  structureTitles: { RootTask: "Task", Other: "Project" },
  declarationDiagnostics: [],
  migratedFrom: null,
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
    // The schema-v2 panel heads this section `capacities-sec-per-type` and
    // tags it with the store's section id. (SettingsHub's scroll target still
    // names `capacities-sec-admission-per-type`; that mismatch is a source
    // defect reported separately, not encoded here.)
    expect(heading.id).toBe("capacities-sec-per-type");
    expect(section.dataset.settingsSection).toBe("admission-per-type");
    expect(section.closest("[hidden]")).toBeNull();
  });

  it("states the one-rule-per-type model at the top of the Capacities panel", async () => {
    const h = makeHarness("ready");
    vi.spyOn(h.controller, "loadCapacitiesSettings").mockResolvedValue(CAP);
    h.store.dispatch({ type: "UI", patch: { settingsPanel: "repair", settingsSection: "admission-per-type" } });
    const rendered = h.ui(<SettingsShell />);
    const concepts = await waitFor(() =>
      rendered.getByText("Each type has one rule for getting into your plan."),
    );
    expect(concepts.className).toContain("capacities-settings__concepts");
    // Schema version 2 replaced the three overlapping concept sections with one
    // rule per type, so the old section list must not come back.
    expect(rendered.queryByRole("list", { name: "Which section to use" })).toBeNull();
    // The native-rules explanation is still reachable from the one section that
    // declares a native rule.
    expect(
      rendered.getByRole("button", { name: "What automatic task rules are" }),
    ).toBeTruthy();
  });
});
