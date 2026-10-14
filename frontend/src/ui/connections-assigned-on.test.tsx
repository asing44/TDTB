/* connections-assigned-on.test.tsx — the read-only Assigned On marker in the
   Connections screen. Pins the observable contract: discovery suggests only
   date/datetime properties for Assigned On, the legacy flag is labelled and
   dims while Assigned On is mapped, either marker alone satisfies the
   structure mapping (and clears the waiting-on-mapping preview), clearing is
   explicit, a per-structure edit never touches the other declarations, and
   the adapter's Assigned On diagnostics render verbatim. */

import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, waitFor } from "@testing-library/preact";
import { App } from "./App";
import { makeHarness } from "./test-harness";
import { __resetConnectionsDraftCache } from "./connections/commands";
import { capacitiesSettingsToWire } from "../adapters/wire";
import type {
  AssignedItem,
  CapacitiesCatalog,
  CapacitiesSettings,
  TagExclusionSettings,
} from "../model/types";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});
beforeEach(() => {
  __resetConnectionsDraftCache();
});

function capSettings(overrides: Partial<CapacitiesSettings> = {}): CapacitiesSettings {
  return {
    version: 1,
    revision: 0,
    persisted: true,
    nativeTaskAuto: {
      activeEnabled: true,
      dueEnabled: false,
      deadlineEnabled: false,
      deadlineHorizonDays: 2,
    },
    excluded: [],
    activeStructures: ["RootTask"],
    nativeTaskStructures: ["RootTask"],
    activeStatuses: ["Started"],
    assignedStructures: {},
    assignedOnStructures: {},
    availableStructures: ["RootTask", "Other"],
    structureTitles: { RootTask: "Task", Other: "Project" },
    ...overrides,
  };
}

function tagSettings(): TagExclusionSettings {
  return {
    version: 1,
    revision: 0,
    persisted: true,
    tags: [],
    todoist: [],
    catalog: { status: "complete", spaceId: "space-1", tags: [], warnings: [] },
  };
}

function capacitiesRow(over: Partial<AssignedItem> = {}): AssignedItem {
  return {
    id: "row",
    name: "row",
    path: null,
    source: "capacities",
    types: ["RootTask"],
    urgency: null,
    deadline: null,
    priorityScore: 0,
    blocks: 1,
    durationLabel: "30min",
    todoistId: null,
    identity: "capacities:space-1:RootTask:row",
    capacitiesIdentity: "capacities:space-1:RootTask:row",
    capacitiesSpaceId: "space-1",
    capacitiesStructureId: "RootTask",
    tags: [],
    ...over,
  };
}

function openConnections(
  cap: CapacitiesSettings,
  rows: AssignedItem[] = [capacitiesRow()],
  sourceWarnings: string[] = [],
) {
  const h = makeHarness("ready");
  const inputs = h.store.getState().inputs!;
  h.store.dispatch({
    type: "INPUTS_LOADED",
    inputs: {
      ...inputs,
      assigned: [...inputs.assigned.filter((r) => r.source !== "capacities"), ...rows],
      sourceWarnings,
    },
    ledger: h.store.getState().ledger!,
  });
  vi.spyOn(h.controller, "loadCapacitiesSettings").mockResolvedValue(cap);
  vi.spyOn(h.controller, "loadTagExclusionSettings").mockResolvedValue(tagSettings());
  vi.spyOn(h.controller, "saveCapacitiesSettings").mockImplementation(async (draft) =>
    capSettings({
      revision: 1,
      nativeTaskAuto: draft.nativeTaskAuto,
      excluded: draft.excluded,
      activeStructures: draft.activeStructures,
      nativeTaskStructures: draft.nativeTaskStructures,
      activeStatuses: draft.activeStatuses,
      assignedStructures: draft.assignedStructures,
      assignedOnStructures: draft.assignedOnStructures,
    }),
  );
  vi.spyOn(h.controller, "saveTagExclusionSettings").mockResolvedValue({
    settings: tagSettings(),
    refreshError: null,
  });
  h.store.dispatch({ type: "UI", patch: { screen: "connections" } });
  const rendered = h.ui(<App />);
  return { h, rendered };
}

async function ready(rendered: ReturnType<ReturnType<typeof makeHarness>["ui"]>) {
  await waitFor(() => expect(rendered.getByRole("heading", { name: "Connections" })).toBeTruthy());
}

const openStructure = (rendered: ReturnType<ReturnType<typeof makeHarness>["ui"]>) =>
  fireEvent.click(rendered.getByRole("button", { name: "Task" }));

describe("Connections Assigned On marker", () => {
  it("suggests only date and datetime properties for Assigned On after discovery", async () => {
    const { h, rendered } = openConnections(capSettings());
    await ready(rendered);
    openStructure(rendered);

    const catalog: CapacitiesCatalog = {
      spaceId: "space-1",
      structures: [
        {
          structureId: "RootTask",
          title: "Task",
          properties: [
            { propertyId: "text-prop", title: "Notes", type: "text", writable: true, labelOptions: [] },
            { propertyId: "date-prop", title: "Assigned On", type: "date", writable: true, labelOptions: [] },
            { propertyId: "datetime-prop", title: "Assigned At", type: "datetime", writable: true, labelOptions: [] },
            { propertyId: "label-prop", title: "Status", type: "label", writable: true, labelOptions: [] },
          ],
        },
      ],
      warnings: [],
    };
    vi.spyOn(h.controller, "loadCapacitiesSource").mockResolvedValue({
      persisted: true,
      source: { version: 1, revision: 0, spaceId: "space-1", structures: [] },
    });
    vi.spyOn(h.controller, "discoverCapacitiesSource").mockResolvedValue(catalog);

    fireEvent.click(rendered.getByRole("button", { name: /Discover from Capacities/ }));

    const onInput = (await waitFor(() =>
      rendered.getByLabelText("Assigned On property for RootTask"),
    )) as HTMLInputElement;
    await waitFor(() => {
      const list = document.getElementById(onInput.getAttribute("list")!)!;
      expect(Array.from(list.querySelectorAll("option")).map((option) => option.value)).toEqual([
        "date-prop",
        "datetime-prop",
      ]);
    });

    // The legacy flag still offers every discovered property.
    const legacyInput = rendered.getByLabelText("Mapping for RootTask") as HTMLInputElement;
    const legacyList = document.getElementById(legacyInput.getAttribute("list")!)!;
    expect(Array.from(legacyList.querySelectorAll("option")).map((option) => option.value)).toEqual([
      "text-prop",
      "date-prop",
      "datetime-prop",
      "label-prop",
    ]);
  });

  it("labels the legacy flag and dims it while Assigned On is mapped", async () => {
    const { rendered } = openConnections(
      capSettings({
        assignedStructures: { RootTask: "legacy-prop" },
        assignedOnStructures: { RootTask: "date-prop" },
      }),
    );
    await ready(rendered);
    openStructure(rendered);

    expect(rendered.getByText("Assigned flag (legacy)")).toBeTruthy();
    const legacy = rendered.getByLabelText("Mapping for RootTask") as HTMLInputElement;
    expect(legacy.disabled).toBe(true);
    expect(legacy.value).toBe("legacy-prop");
    expect(rendered.getByText("Ignored while Assigned On is mapped")).toBeTruthy();
    expect(rendered.getByText(/Used only when no Assigned On property is mapped/)).toBeTruthy();
    expect(
      (rendered.getByLabelText("Assigned On property for RootTask") as HTMLInputElement).value,
    ).toBe("date-prop");
  });

  it("shows the mapped hint and clears waiting-on-mapping for either marker", async () => {
    const { rendered } = openConnections(
      capSettings({ assignedStructures: { RootTask: "legacy-prop" } }),
    );
    await ready(rendered);
    openStructure(rendered);

    // The legacy marker alone satisfies the mapping...
    expect(rendered.getByText("Mapped: legacy-prop")).toBeTruthy();
    expect(rendered.getByText(/0 waiting on mapping/)).toBeTruthy();

    // ...clearing it brings the preview back to waiting...
    fireEvent.input(rendered.getByLabelText("Mapping for RootTask"), { target: { value: "" } });
    await waitFor(() => expect(rendered.getByText(/1 waiting on mapping/)).toBeTruthy());
    expect(rendered.getByText(/Not mapped/)).toBeTruthy();

    // ...and an Assigned On mapping satisfies it too.
    fireEvent.input(rendered.getByLabelText("Assigned On property for RootTask"), {
      target: { value: "date-prop" },
    });
    await waitFor(() => expect(rendered.getByText("Mapped: date-prop")).toBeTruthy());
    expect(rendered.getByText(/0 waiting on mapping/)).toBeTruthy();
  });

  it("keeps other structures' declarations when one Assigned On property is edited", async () => {
    const { h, rendered } = openConnections(
      capSettings({
        assignedStructures: { RootTask: "legacy-prop" },
        assignedOnStructures: { Other: "other-on-prop" },
      }),
    );
    await ready(rendered);
    openStructure(rendered);

    fireEvent.input(rendered.getByLabelText("Assigned On property for RootTask"), {
      target: { value: "date-prop" },
    });
    fireEvent.click(rendered.getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(h.controller.saveCapacitiesSettings).toHaveBeenCalledTimes(1),
    );

    const draft = vi.mocked(h.controller.saveCapacitiesSettings).mock.calls[0][0];
    expect(draft.assignedOnStructures).toEqual({
      Other: "other-on-prop",
      RootTask: "date-prop",
    });
    expect(draft.assignedStructures).toEqual({ RootTask: "legacy-prop" });
    const body = capacitiesSettingsToWire(draft);
    expect(body.assigned_on_structures).toEqual({
      Other: "other-on-prop",
      RootTask: "date-prop",
    });
    expect(body.assigned_structures).toEqual({ RootTask: "legacy-prop" });
  });

  it("clears the Assigned On mapping with the inline clear button", async () => {
    const { rendered } = openConnections(
      capSettings({ assignedOnStructures: { RootTask: "date-prop" } }),
    );
    await ready(rendered);
    openStructure(rendered);

    const input = rendered.getByLabelText("Assigned On property for RootTask") as HTMLInputElement;
    expect(input.value).toBe("date-prop");
    const clear = rendered.getByRole("button", {
      name: "Clear Assigned On property for RootTask",
    }) as HTMLButtonElement;
    expect(clear.disabled).toBe(false);

    fireEvent.click(clear);
    expect(input.value).toBe("");
    expect(rendered.getByText(/Not mapped/)).toBeTruthy();
    // The legacy flag is usable again once Assigned On is cleared.
    expect((rendered.getByLabelText("Mapping for RootTask") as HTMLInputElement).disabled).toBe(
      false,
    );
  });

  it("renders the adapter's Assigned On diagnostics verbatim under Advanced", async () => {
    const warning =
      "ignored Capacities Assigned On declaration for structure 'RootTask': " +
      "unknown property 'date-prop'";
    const { rendered } = openConnections(capSettings(), [capacitiesRow()], [
      warning,
      "Capacities partial — 20 evaluated.",
    ]);
    await ready(rendered);

    fireEvent.click(rendered.getByRole("button", { name: "Advanced" }));
    expect(rendered.getByText("Assigned On diagnostics in the current read")).toBeTruthy();
    expect(rendered.getByText(warning)).toBeTruthy();
    // Non-Assigned-On warnings stay out of this section.
    expect(rendered.queryByText("Capacities partial — 20 evaluated.")).toBeNull();
  });
});
