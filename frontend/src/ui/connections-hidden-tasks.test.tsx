/* connections-hidden-tasks.test.tsx — the Connections "Hidden tasks" rule pane.
   The pane edits the DRAFT only: Show again removes an entry, the existing
   footer Save writes the full replacement (both dimensions), and Discard
   restores the loaded list. A tag edit must never wipe the hidden tasks. */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, waitFor, within } from "@testing-library/preact";
import { App } from "./App";
import { makeHarness } from "./test-harness";
import type {
  CapacitiesSettings,
  TagExclusionSettings,
  TodoistExclusionEntry,
} from "../model/types";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const TAG_LIVE = "5a25370b-f9a0-40cf-bc3a-0cab4744913c";

const HIDDEN_ONE: TodoistExclusionEntry = {
  id: "td-1",
  name: "Water plants",
  addedAt: "2026-03-04T10:00:00.000Z",
};
const HIDDEN_TWO: TodoistExclusionEntry = {
  id: "td-2",
  name: "Call bank",
  addedAt: "2026-03-01T09:00:00.000Z",
};

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
    availableStructures: ["RootTask"],
    structureTitles: { RootTask: "Task" },
    ...overrides,
  };
}

function tagSettings(overrides: Partial<TagExclusionSettings> = {}): TagExclusionSettings {
  return {
    version: 1,
    revision: 0,
    persisted: true,
    tags: [{ source: "capacities", spaceId: "space-1", tagId: TAG_LIVE }],
    todoist: [],
    catalog: {
      status: "complete",
      spaceId: "space-1",
      tags: [{ id: TAG_LIVE, title: "habituals" }],
      warnings: [],
    },
    ...overrides,
  };
}

function openConnections(tag: TagExclusionSettings = tagSettings()) {
  const h = makeHarness("ready");
  vi.spyOn(h.controller, "loadCapacitiesSettings").mockResolvedValue(capSettings());
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
    settings: tagSettings({
      revision: 1,
      tags: draft.tags,
      todoist: draft.todoist,
    }),
    refreshError: null,
  }));
  h.store.dispatch({ type: "UI", patch: { screen: "connections" } });
  const rendered = h.ui(<App />);
  return { h, rendered };
}

async function ready(rendered: ReturnType<ReturnType<typeof makeHarness>["ui"]>) {
  await waitFor(() => expect(rendered.getByRole("heading", { name: "Connections" })).toBeTruthy());
}

const openHidden = (rendered: ReturnType<ReturnType<typeof makeHarness>["ui"]>) =>
  fireEvent.click(rendered.getByRole("button", { name: /Hidden tasks/ }));

describe("Connections · Hidden tasks", () => {
  it("lists hidden tasks with short added dates and a Show again control", async () => {
    const { rendered } = openConnections(tagSettings({ todoist: [HIDDEN_ONE, HIDDEN_TWO] }));
    await ready(rendered);

    // The nav label counts the entries.
    expect(rendered.getByRole("button", { name: "Hidden tasks (2)" })).toBeTruthy();

    openHidden(rendered);
    const pane = rendered.getByRole("region", { name: "Hidden tasks" });
    expect(within(pane).getByText("Water plants")).toBeTruthy();
    expect(within(pane).getByText("Call bank")).toBeTruthy();
    // Dates are formatted short, never the raw timestamp.
    expect(within(pane).queryByText(HIDDEN_ONE.addedAt)).toBeNull();
    expect(pane.textContent).toMatch(/[A-Z][a-z]{2} \d{1,2}, \d{4}/);
    expect(within(pane).getByRole("button", { name: "Show again Water plants" })).toBeTruthy();
    expect(within(pane).getByRole("button", { name: "Show again Call bank" })).toBeTruthy();
  });

  it("shows the empty state and the Known objects note when nothing is hidden", async () => {
    const { rendered } = openConnections();
    await ready(rendered);

    expect(rendered.getByRole("button", { name: "Hidden tasks" })).toBeTruthy();
    openHidden(rendered);
    const pane = rendered.getByRole("region", { name: "Hidden tasks" });
    expect(
      within(pane).getByText(
        "Nothing hidden. Use ⋯ > Always exclude on a task to hide it every day.",
      ),
    ).toBeTruthy();
    expect(
      within(pane).getByText("Capacities objects are managed under Known objects in Advanced."),
    ).toBeTruthy();
    expect(within(pane).queryByRole("button", { name: /Show again/ })).toBeNull();
  });

  it("Show again marks the draft dirty and Save posts unchanged tags plus the reduced list", async () => {
    const { h, rendered } = openConnections(tagSettings({ todoist: [HIDDEN_ONE, HIDDEN_TWO] }));
    await ready(rendered);
    openHidden(rendered);

    fireEvent.click(rendered.getByRole("button", { name: "Show again Water plants" }));
    expect(rendered.queryByRole("button", { name: "Show again Water plants" })).toBeNull();
    await waitFor(() => expect(rendered.getByText(/1 unsaved change/)).toBeTruthy());

    fireEvent.click(rendered.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(h.controller.saveTagExclusionSettings).toHaveBeenCalledTimes(1));
    expect(h.controller.saveTagExclusionSettings).toHaveBeenCalledWith({
      expectedRevision: 0,
      tags: [{ source: "capacities", spaceId: "space-1", tagId: TAG_LIVE }],
      todoist: [HIDDEN_TWO],
    });
    await waitFor(() => expect(rendered.getByText(/No unsaved changes/)).toBeTruthy());
  });

  it("Discard restores a shown-again hidden task", async () => {
    const { rendered } = openConnections(tagSettings({ todoist: [HIDDEN_ONE] }));
    await ready(rendered);
    openHidden(rendered);

    fireEvent.click(rendered.getByRole("button", { name: "Show again Water plants" }));
    await waitFor(() => expect(rendered.getByText(/1 unsaved change/)).toBeTruthy());

    fireEvent.click(rendered.getByRole("button", { name: "Discard" }));
    await waitFor(() =>
      expect(rendered.getByRole("button", { name: "Show again Water plants" })).toBeTruthy(),
    );
    expect(rendered.getByRole("button", { name: "Hidden tasks (1)" })).toBeTruthy();
    expect(rendered.getByText(/No unsaved changes/)).toBeTruthy();
  });

  it("a tag edit preserves the hidden-task list in the saved body", async () => {
    const { h, rendered } = openConnections(tagSettings({ todoist: [HIDDEN_ONE] }));
    await ready(rendered);

    fireEvent.click(rendered.getByRole("button", { name: "Exclude tags" }));
    fireEvent.click(rendered.getByRole("button", { name: /Remove tag #habituals/i }));
    await waitFor(() => expect(rendered.getByText(/1 unsaved change/)).toBeTruthy());

    fireEvent.click(rendered.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(h.controller.saveTagExclusionSettings).toHaveBeenCalledTimes(1));
    expect(h.controller.saveTagExclusionSettings).toHaveBeenCalledWith({
      expectedRevision: 0,
      tags: [],
      todoist: [HIDDEN_ONE],
    });
  });
});
