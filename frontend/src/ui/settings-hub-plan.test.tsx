/* settings-hub-plan.test.tsx — what moved from the retired Connections page
   into the hub's "What shows up in my plan?" entry: Tasks I hid (Show again
   through the shared tag-exclusion save), the plan preview strip, the item-type
   list with health dots, the Capacities sync line, and the rules draft that
   survives closing the fold. The preview fixture is the one the Connections
   preview test used, so its per-structure counts sum to the hub's totals. */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, waitFor, within } from "@testing-library/preact";
import { SettingsShell } from "./SettingsShell";
import { HUB_ENTRIES } from "./SettingsHub";
import { makeHarness } from "./test-harness";
import type {
  AssignedItem,
  CapacitiesIntake,
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
    version: 2,
    revision: 0,
    persisted: true,
    statuses: { open: ["Started"], completed: ["Completed", "Dropped"] },
    // One rule per type: RootTask is admitted by an assigned checkbox, which is
    // the only mapped type in the default fixture.
    types: {
      RootTask: { mode: "assigned", property: "assigned", statuses: null },
    },
    native: { deadlineHorizonDays: 2 },
    excluded: [],
    availableStructures: ["RootTask", "Other", "Idle"],
    structureTitles: { RootTask: "Task", Other: "Project", Idle: "Note" },
    declarationDiagnostics: [],
    migratedFrom: null,
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

function row(over: Partial<AssignedItem>): AssignedItem {
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

const PREVIEW_ROWS: AssignedItem[] = [
  row({
    id: "Tagged",
    name: "Tagged task",
    tags: [TAG_LIVE],
    identity: "capacities:space-1:RootTask:tagged",
    capacitiesIdentity: "capacities:space-1:RootTask:tagged",
  }),
  row({
    id: "Mapped",
    name: "Mapped task",
    identity: "capacities:space-1:RootTask:mapped",
    capacitiesIdentity: "capacities:space-1:RootTask:mapped",
  }),
  row({
    id: "Unmapped",
    name: "Unmapped task",
    capacitiesStructureId: null,
    identity: "capacities:space-1:Other:row",
    capacitiesIdentity: "capacities:space-1:Other:row",
  }),
];

function openPlan(opts: {
  rows?: AssignedItem[];
  cap?: CapacitiesSettings;
  tag?: TagExclusionSettings;
  intake?: Partial<CapacitiesIntake> | null;
  warnings?: string[];
  panel?: "plan" | "freshness";
} = {}) {
  const h = makeHarness("ready");
  const inputs = h.store.getState().inputs!;
  h.store.dispatch({
    type: "INPUTS_LOADED",
    inputs: {
      ...inputs,
      assigned: [...inputs.assigned.filter((r) => r.source !== "capacities"), ...(opts.rows ?? [])],
      sourceWarnings: opts.warnings ?? inputs.sourceWarnings,
      ...(opts.intake
        ? {
            capacitiesIntake: {
              mode: "direct",
              state: "ok",
              generation: 1,
              installedAt: 1_760_000_000,
              typeCheckTimes: {},
              coverage: { members: 1, evaluated: 1, malformed: 0, unreadable: 0 },
              unassignedCandidates: [],
              ...opts.intake,
            } as CapacitiesIntake,
          }
        : {}),
    },
    ledger: h.store.getState().ledger!,
  });
  vi.spyOn(h.controller, "loadCapacitiesSettings").mockResolvedValue(opts.cap ?? capSettings());
  vi.spyOn(h.controller, "loadTagExclusionSettings").mockResolvedValue(opts.tag ?? tagSettings());
  vi.spyOn(h.controller, "saveTagExclusionSettings").mockImplementation(async (draft) => ({
    settings: tagSettings({ revision: draft.expectedRevision + 1, tags: draft.tags, todoist: draft.todoist }),
    refreshError: null,
  }));
  h.store.dispatch({ type: "UI", patch: { settingsPanel: opts.panel ?? "plan" } });
  return { h, rendered: h.ui(<SettingsShell />) };
}

describe("hub · Tasks I hid", () => {
  it("lists hidden tasks with short dates and a Show again control", async () => {
    const { rendered } = openPlan({ tag: tagSettings({ todoist: [HIDDEN_ONE, HIDDEN_TWO] }) });
    const heading = await waitFor(() => rendered.getByRole("heading", { name: "Tasks I hid" }));
    const section = heading.closest("section") as HTMLElement;
    expect(within(section).getByText("Water plants")).toBeTruthy();
    expect(within(section).getByText("Call bank")).toBeTruthy();
    expect(section.textContent).toMatch(/[A-Z][a-z]{2} \d{1,2}, \d{4}/);
    expect(within(section).queryByText(HIDDEN_ONE.addedAt)).toBeNull();
    expect(within(section).getByRole("button", { name: "Show again Water plants" })).toBeTruthy();
    expect(within(section).getByRole("button", { name: "Show again Call bank" })).toBeTruthy();
  });

  it("shows the empty state when nothing is hidden", async () => {
    const { rendered } = openPlan();
    const heading = await waitFor(() => rendered.getByRole("heading", { name: "Tasks I hid" }));
    const section = heading.closest("section") as HTMLElement;
    expect(
      within(section).getByText("Nothing hidden. Use ⋯ > Always exclude on a task to hide it every day."),
    ).toBeTruthy();
    expect(within(section).queryByRole("button", { name: /Show again/ })).toBeNull();
    expect(within(section).getByText("None")).toBeTruthy();
  });

  it("counts the hidden tasks and tells the operator which save applies them", async () => {
    const { rendered } = openPlan({ tag: tagSettings({ todoist: [HIDDEN_ONE, HIDDEN_TWO] }) });
    const heading = await waitFor(() => rendered.getByRole("heading", { name: "Tasks I hid" }));
    const section = heading.closest("section") as HTMLElement;
    expect(within(section).getByText("2 hidden")).toBeTruthy();
    expect(within(section).getByText("Press Save tag exclusions to apply.")).toBeTruthy();
  });

  it("mounts the calendar defaults beside the tag exclusions and saves on request", async () => {
    const { h, rendered } = openPlan();
    const save = vi.spyOn(h.controller, "saveCalendarSettings");
    const toggle = await waitFor(() =>
      rendered.getByRole("switch", { name: "Include Work by default" }),
    );
    const list = rendered.getByRole("list", { name: "Calendars" });
    // The fixture inventory has three unlocked, unsaved calendars.
    expect(within(list).getAllByText("New")).toHaveLength(3);
    fireEvent.click(toggle);
    fireEvent.click(rendered.getByRole("button", { name: "Save calendars" }));
    await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
    expect(save.mock.calls[0][0]).toEqual({
      expectedRevision: 0,
      calendars: [
        { id: "cal-work", title: "Work", enabled: false },
        { id: "cal-family", title: "Family", enabled: true },
        { id: "cal-holidays", title: "US Holidays", enabled: true },
        { id: "cal-tdtb-focus", title: "TDTB Focus", enabled: true },
      ],
    });
    await waitFor(() => expect(within(list).queryAllByText("New")).toHaveLength(0));
  });

  it("the plan entry hint mentions calendars", () => {
    expect(HUB_ENTRIES.find((e) => e.id === "plan")!.hint).toMatch(/calendars/);
  });

  it("a tag edit preserves the hidden-task list in the saved body", async () => {
    const { h, rendered } = openPlan({ tag: tagSettings({ todoist: [HIDDEN_ONE] }) });
    await waitFor(() => rendered.getByRole("heading", { name: "Tasks I hid" }));

    fireEvent.click(rendered.getByRole("checkbox", { name: /Exclude #habituals/ }));
    fireEvent.click(rendered.getByRole("button", { name: "Save tag exclusions" }));
    await waitFor(() => expect(h.controller.saveTagExclusionSettings).toHaveBeenCalledTimes(1));
    expect(h.controller.saveTagExclusionSettings).toHaveBeenCalledWith({
      expectedRevision: 0,
      tags: [],
      todoist: [HIDDEN_ONE],
    });
  });

  it("Show again edits the draft and Save posts the tags unchanged plus the reduced list", async () => {
    const { h, rendered } = openPlan({ tag: tagSettings({ todoist: [HIDDEN_ONE, HIDDEN_TWO] }) });
    const show = await waitFor(() =>
      rendered.getByRole("button", { name: "Show again Water plants" }),
    );
    fireEvent.click(show);
    expect(rendered.queryByRole("button", { name: "Show again Water plants" })).toBeNull();
    expect(h.controller.saveTagExclusionSettings).not.toHaveBeenCalled();

    fireEvent.click(rendered.getByRole("button", { name: "Save tag exclusions" }));
    await waitFor(() => expect(h.controller.saveTagExclusionSettings).toHaveBeenCalledTimes(1));
    expect(h.controller.saveTagExclusionSettings).toHaveBeenCalledWith({
      expectedRevision: 0,
      tags: [{ source: "capacities", spaceId: "space-1", tagId: TAG_LIVE }],
      todoist: [HIDDEN_TWO],
    });
    await waitFor(() => expect(rendered.getByText("Saved.")).toBeTruthy());
    expect(rendered.getByRole("button", { name: "Show again Call bank" })).toBeTruthy();
  });

  it("a revision conflict keeps the draft and requires a reload before saving again", async () => {
    const { h, rendered } = openPlan({ tag: tagSettings({ todoist: [HIDDEN_ONE] }) });
    vi.spyOn(h.controller, "saveTagExclusionSettings").mockRejectedValueOnce(
      new Error("revision conflict"),
    );
    fireEvent.click(await waitFor(() => rendered.getByRole("button", { name: "Show again Water plants" })));
    fireEvent.click(rendered.getByRole("button", { name: "Save tag exclusions" }));
    await waitFor(() => expect(rendered.getByText("revision conflict")).toBeTruthy());
    expect(
      (rendered.getByRole("button", { name: "Save tag exclusions" }) as HTMLButtonElement).disabled,
    ).toBe(true);
    expect(rendered.getByRole("button", { name: "Reload settings" })).toBeTruthy();
  });
});

describe("hub · plan preview", () => {
  it("counts shown, waiting and left-out items (sum of the Connections per-type preview)", async () => {
    const { rendered } = openPlan({ rows: PREVIEW_ROWS });
    // Connections: Task 1 admitted + 1 left out by tag; Project 1 waiting.
    const strip = await waitFor(() => rendered.getByText(/1 shown in today's plan/));
    expect(strip.closest("p")!.textContent).toContain("1 waiting");
    expect(strip.closest("p")!.textContent).toContain("1 left out by tag");

    const details = rendered.getByText(/See each item \(3\)/).closest("details")!;
    expect(details.hasAttribute("open")).toBe(false);
    fireEvent.click(within(details).getByText(/See each item/));
    const reasonOf = (name: string) =>
      within(within(details).getByText(name).closest("li") as HTMLElement).getByText(
        /shown|waiting for setup|left out by tag/,
      ).textContent;
    expect(reasonOf("Tagged task")).toBe("left out by tag");
    expect(reasonOf("Mapped task")).toBe("shown");
    expect(reasonOf("Unmapped task")).toBe("waiting for setup");
  });

  it("is a heading-bearing region at the top of the entry", async () => {
    const { rendered } = openPlan({ rows: PREVIEW_ROWS });
    const heading = await waitFor(() => rendered.getByRole("heading", { name: "In today's plan" }));
    const panel = heading.closest(".settings-hub__panel") as HTMLElement;
    expect(panel.querySelector("section")).toBe(heading.closest("section"));
  });

  it("follows a saved tag change", async () => {
    const { rendered } = openPlan({ rows: PREVIEW_ROWS });
    await waitFor(() => rendered.getByText(/1 shown in today's plan/));
    fireEvent.click(rendered.getByRole("checkbox", { name: /Exclude #habituals/ }));
    fireEvent.click(rendered.getByRole("button", { name: "Save tag exclusions" }));
    await waitFor(() => expect(rendered.getByText(/2 shown in today's plan/)).toBeTruthy());
  });
});

describe("hub · item types", () => {
  it("lists each mapped type with a dot and one plain status line", async () => {
    const { rendered } = openPlan({
      rows: PREVIEW_ROWS,
      cap: capSettings({
        types: {
          RootTask: { mode: "assigned", property: "assigned", statuses: null },
          Idle: { mode: "off", property: null, statuses: null },
        },
      }),
    });
    await waitFor(() => rendered.getByText(/1 shown in today's plan/));
    fireEvent.click(rendered.getByRole("button", { name: /fine-tune by item type/i }));
    const list = await waitFor(() => rendered.getByRole("region", { name: "Item types" }));
    const lineOf = (label: string) =>
      (within(list).getByRole("button", { name: label }).closest("li") as HTMLElement);
    expect(lineOf("Task").textContent).toContain("Set up and bringing items in");
    expect(lineOf("Task").querySelector(".plan-types__dot")!.className).not.toMatch(/--warn|--off/);
    expect(lineOf("Project").textContent).toContain("Not set up yet");
    expect(lineOf("Project").querySelector(".plan-types__dot--warn")).toBeTruthy();
    // Schema v2 has one rule per type, so "declared but turned off" is the same
    // state as "never set up": a type whose rule is Off admits nothing.
    expect(lineOf("Note").textContent).toContain("Not set up yet");
    expect(lineOf("Note").querySelector(".plan-types__dot--warn")).toBeTruthy();
  });

  it("notes mapped types missing from the latest refresh", async () => {
    const { rendered } = openPlan({
      panel: "freshness",
      warnings: ["Capacities structure RootTask is not in the published generation; needs Refresh."],
    });
    const region = await waitFor(() => rendered.getByRole("region", { name: "Needs attention" }));
    expect(region.textContent).toContain("1 mapped types are not yet in the latest refresh");
    expect(rendered.container.querySelector(".settings-hub__summary")!.textContent).not.toContain("mapped types");
  });

  it("moves focus to the type's rule card when a row is chosen", async () => {
    const { rendered } = openPlan({
      cap: capSettings({
        availableStructures: ["custom-project"],
        structureTitles: { "custom-project": "Custom project" },
      }),
    });
    fireEvent.click(await waitFor(() => rendered.getByRole("button", { name: /fine-tune by item type/i })));
    await waitFor(
      () => expect(rendered.container.querySelector('.capacities-rule[data-structure="custom-project"]')).toBeTruthy(),
      { timeout: 5000 },
    );
    const list = await waitFor(() => rendered.getByRole("region", { name: "Item types" }));
    fireEvent.click(within(list).getByRole("button", { name: "Custom project" }));
    const card = rendered.container.querySelector('.capacities-rule[data-structure="custom-project"]');
    expect(document.activeElement).toBe(card);
  });
});

describe("hub · Capacities sync line", () => {
  it.each([
    ["ok", "Capacities is up to date."],
    ["degraded", "Capacities had a problem. Your plan keeps using the last read."],
    ["unavailable", "Capacities is unavailable right now."],
  ])("freshness entry says %s", async (state, text) => {
    const { rendered } = openPlan({ panel: "freshness", intake: { state: state as CapacitiesIntake["state"] } });
    expect(rendered.getByText(text)).toBeTruthy();
  });
});

describe("hub · rules draft survives the fold", () => {
  it("keeps an unsaved edit when the fold is closed and reopened", async () => {
    const { rendered } = openPlan();
    const fold = rendered.getByRole("button", { name: /fine-tune by item type/i });
    fireEvent.click(fold);
    await waitFor(
      () => expect(rendered.container.querySelector('[data-structure="custom-project"]')).toBeTruthy(),
      { timeout: 5000 },
    );
    const rowEl = () =>
      rendered.container.querySelector('[data-structure="custom-project"]') as HTMLElement;
    const values = within(rowEl()).getByLabelText("Values") as HTMLInputElement;
    fireEvent.input(values, { target: { value: "on-hold" } });
    await waitFor(() => expect(within(rowEl()).getByText("Unsaved draft")).toBeTruthy());

    fireEvent.click(fold);
    expect(fold.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(fold);
    expect(fold.getAttribute("aria-expanded")).toBe("true");
    expect((within(rowEl()).getByLabelText("Values") as HTMLInputElement).value).toBe("on-hold");
    expect(within(rowEl()).getByText("Unsaved draft")).toBeTruthy();
  });
});
