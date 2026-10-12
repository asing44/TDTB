/* connections-screen.test.tsx — U13 Connections screen (reference C).

   The screen composes the Capacities policy and the tag-exclusion policy into
   one page. These tests pin the composition contract: one unsaved-changes bar
   aggregating every edited slice, Discard restoring all of them, Save
   persisting them under their loaded revisions, the stale-tag notice, the
   admission preview, the invalid-rule guard (R33), unsaved-edit survival
   across the Back-to-planning link, and the absence of the Plan footer's
   primary action on this screen. */

import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, waitFor, within } from "@testing-library/preact";
import { App } from "./App";
import { makeHarness } from "./test-harness";
import { __resetConnectionsDraftCache } from "./connections/commands";
import type {
  AssignedItem,
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

const TAG_LIVE = "5a25370b-f9a0-40cf-bc3a-0cab4744913c";
const TAG_STALE = "0d194525-c5a1-4af5-bb62-202b83006b5e";

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
    assignedStructures: { RootTask: "assigned" },
    availableStructures: ["RootTask", "Other"],
    structureTitles: { RootTask: "Task", Other: "Project" },
    ...overrides,
  };
}

function tagSettings(overrides: Partial<TagExclusionSettings> = {}): TagExclusionSettings {
  return {
    version: 1,
    revision: 0,
    persisted: true,
    tags: [{ source: "capacities", spaceId: "space-1", tagId: TAG_LIVE }],
    catalog: {
      status: "complete",
      spaceId: "space-1",
      tags: [{ id: TAG_LIVE, title: "habituals" }],
      warnings: [],
    },
    ...overrides,
  };
}

function capacitiesRow(over: Partial<AssignedItem>): AssignedItem {
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
  rows: AssignedItem[] = [],
  cap: CapacitiesSettings = capSettings(),
  tag: TagExclusionSettings = tagSettings(),
) {
  const h = makeHarness("ready");
  const inputs = h.store.getState().inputs!;
  h.store.dispatch({
    type: "INPUTS_LOADED",
    inputs: { ...inputs, assigned: [...inputs.assigned.filter((r) => r.source !== "capacities"), ...rows] },
    ledger: h.store.getState().ledger!,
  });
  vi.spyOn(h.controller, "loadCapacitiesSettings").mockResolvedValue(cap);
  vi.spyOn(h.controller, "loadTagExclusionSettings").mockResolvedValue(tag);
  vi.spyOn(h.controller, "saveCapacitiesSettings").mockImplementation(async (draft) =>
    capSettings({
      revision: 1,
      nativeTaskAuto: draft.nativeTaskAuto,
      excluded: draft.excluded,
      activeStructures: draft.activeStructures,
      nativeTaskStructures: draft.nativeTaskStructures,
      activeStatuses: draft.activeStatuses,
      assignedStructures: draft.assignedStructures,
    }),
  );
  vi.spyOn(h.controller, "saveTagExclusionSettings").mockImplementation(async (draft) => ({
    settings: tagSettings({ revision: 1, tags: draft.tags }),
    refreshError: null,
  }));
  h.store.dispatch({ type: "UI", patch: { screen: "connections" } });
  const rendered = h.ui(<App />);
  return { h, rendered };
}

async function ready(rendered: ReturnType<ReturnType<typeof makeHarness>["ui"]>) {
  await waitFor(() => expect(rendered.getByRole("heading", { name: "Connections" })).toBeTruthy());
}

describe("Connections screen (U13)", () => {
  it("counts a rule, a mapping and a tag edit as three unsaved changes and Discard restores all three", async () => {
    const { rendered } = openConnections();
    await ready(rendered);

    // Rule: turn the Active status rule off.
    fireEvent.click(rendered.getByRole("checkbox", { name: /Status is active/ }));
    // Mapping: clear the RootTask property mapping.
    fireEvent.input(rendered.getByLabelText(/mapping for RootTask/i), { target: { value: "" } });
    // Tag list: remove the excluded tag.
    fireEvent.click(rendered.getByRole("button", { name: /Remove tag #habituals/i }));

    await waitFor(() => expect(rendered.getByText(/3 unsaved changes/)).toBeTruthy());

    fireEvent.click(rendered.getByRole("button", { name: "Discard" }));
    await waitFor(() => expect(rendered.getByText(/No unsaved changes/)).toBeTruthy());
    expect((rendered.getByRole("checkbox", { name: /Status is active/ }) as HTMLInputElement).checked).toBe(true);
    expect((rendered.getByLabelText(/mapping for RootTask/i) as HTMLInputElement).value).toBe("assigned");
    expect(rendered.getByRole("button", { name: /Remove tag #habituals/i })).toBeTruthy();
  });

  it("Save persists the rule, mapping and tag policies under their loaded revisions", async () => {
    const { h, rendered } = openConnections();
    await ready(rendered);

    fireEvent.click(rendered.getByRole("checkbox", { name: /Status is active/ }));
    fireEvent.input(rendered.getByLabelText(/mapping for RootTask/i), { target: { value: "" } });
    fireEvent.click(rendered.getByRole("button", { name: /Remove tag #habituals/i }));
    await waitFor(() => expect(rendered.getByText(/3 unsaved changes/)).toBeTruthy());

    fireEvent.click(rendered.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(h.controller.saveCapacitiesSettings).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(h.controller.saveTagExclusionSettings).toHaveBeenCalledTimes(1));
    expect(h.controller.saveCapacitiesSettings).toHaveBeenCalledWith(
      expect.objectContaining({
        expectedRevision: 0,
        nativeTaskAuto: expect.objectContaining({ activeEnabled: false }),
        assignedStructures: {},
      }),
    );
    expect(h.controller.saveTagExclusionSettings).toHaveBeenCalledWith({
      expectedRevision: 0,
      tags: [],
    });
    await waitFor(() => expect(rendered.getByText(/No unsaved changes/)).toBeTruthy());
  });

  it("shows the stale saved tag notice and lets it be removed", async () => {
    const { rendered } = openConnections([], capSettings(), tagSettings({
      tags: [
        { source: "capacities", spaceId: "space-1", tagId: TAG_LIVE },
        { source: "capacities", spaceId: "space-1", tagId: TAG_STALE },
      ],
    }));
    await ready(rendered);

    const stale = await waitFor(() =>
      rendered.getByText(/A saved tag no longer exists in Capacities/),
    );
    expect(stale).toBeTruthy();
    fireEvent.click(rendered.getByRole("button", { name: /Remove saved tag/i }));
    await waitFor(() =>
      expect(rendered.queryByText(/A saved tag no longer exists in Capacities/)).toBeNull(),
    );
  });

  it("previews admitted and left-out counts with a reason per item and updates as the draft changes", async () => {
    const { rendered } = openConnections([
      capacitiesRow({
        id: "Tagged",
        name: "Tagged task",
        tags: [TAG_LIVE],
        identity: "capacities:space-1:RootTask:tagged",
        capacitiesIdentity: "capacities:space-1:RootTask:tagged",
      }),
      capacitiesRow({
        id: "Mapped",
        name: "Mapped task",
        capacitiesStructureId: "RootTask",
        identity: "capacities:space-1:RootTask:mapped",
        capacitiesIdentity: "capacities:space-1:RootTask:mapped",
      }),
      capacitiesRow({
        id: "Unmapped",
        name: "Unmapped task",
        capacitiesStructureId: "Other",
        identity: "capacities:space-1:Other:row",
        capacitiesIdentity: "capacities:space-1:Other:row",
      }),
    ]);
    await ready(rendered);

    const preview = rendered.getByRole("region", { name: /Preview/i });
    await waitFor(() => expect(within(preview).getByText(/1 item admitted/)).toBeTruthy());
    expect(within(preview).getByText(/1 left out by tag/)).toBeTruthy();
    expect(within(preview).getByText(/1 waiting on mapping/)).toBeTruthy();
    expect(within(preview).getByText("Tagged task")).toBeTruthy();
    expect(within(preview).getByText("left out by tag")).toBeTruthy();
    expect(within(preview).getByText("waiting on mapping")).toBeTruthy();
    expect(within(preview).getByText("admitted")).toBeTruthy();

    // Removing the tag exclusion moves the tagged item out of "left out".
    fireEvent.click(rendered.getByRole("button", { name: /Remove tag #habituals/i }));
    await waitFor(() => expect(within(preview).getByText(/0 left out by tag/)).toBeTruthy());
    await waitFor(() => expect(within(preview).getByText(/2 items admitted/)).toBeTruthy());
  });

  it("refuses an invalid rule, states why, and keeps Save disabled (R33)", async () => {
    const { rendered } = openConnections();
    await ready(rendered);

    fireEvent.click(rendered.getByRole("checkbox", { name: /Deadline within/ }));
    fireEvent.input(rendered.getByLabelText(/Deadline horizon/i), { target: { value: "" } });

    await waitFor(() =>
      expect(rendered.getByText(/Use a nonnegative whole number/)).toBeTruthy(),
    );
    expect((rendered.getByRole("button", { name: "Save" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("returns to Plan tasks on Back to planning without losing unsaved Connections edits", async () => {
    const { h, rendered } = openConnections();
    await ready(rendered);

    fireEvent.click(rendered.getByRole("checkbox", { name: /Status is active/ }));
    await waitFor(() => expect(rendered.getByText(/1 unsaved change/)).toBeTruthy());

    fireEvent.click(rendered.getByRole("button", { name: /Back to planning/ }));
    expect(h.store.getState().ui.screen).toBe("plan");

    h.store.dispatch({ type: "UI", patch: { screen: "connections" } });
    await waitFor(() => expect(rendered.getByText(/1 unsaved change/)).toBeTruthy());
  });

  it("shows no Sequence & commit primary action on the Connections footer", async () => {
    const { rendered } = openConnections();
    await ready(rendered);
    expect(rendered.queryByText(/Sequence & commit/)).toBeNull();
    expect(rendered.getByRole("button", { name: "Discard" })).toBeTruthy();
    expect(rendered.getByRole("button", { name: "Save" })).toBeTruthy();
  });

  it("renders the structures table columns and keeps the Advanced region present", async () => {
    const { rendered } = openConnections();
    await ready(rendered);
    for (const header of ["STRUCTURE", "NATIVE RULES", "ACTIVE PULL", "ASSIGNED FLAG", "MAPPING"]) {
      expect(rendered.getByText(header)).toBeTruthy();
    }
    expect(rendered.getByText("Advanced")).toBeTruthy();
    expect(rendered.getByRole("button", { name: /Discover from Capacities/ })).toBeTruthy();
  });
});
