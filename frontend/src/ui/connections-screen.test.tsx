/* connections-screen.test.tsx — U13 Connections screen (reference C).

   The screen composes the Capacities policy and the tag-exclusion policy into
   one page. These tests pin the composition contract: one unsaved-changes bar
   aggregating every edited slice, Discard restoring all of them, Save
   persisting them under their loaded revisions, the stale-tag notice, the
   admission preview (the aggregate strip plus the per-item disclosure), the
   invalid-rule guard (R33), unsaved-edit survival across the Back-to-planning
   link, and the absence of the Plan footer's primary action on this screen. */

import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, waitFor, within } from "@testing-library/preact";
import { App } from "./App";
import { makeHarness } from "./test-harness";
import { sourceHealthBySource, sourceHealthOf } from "../adapters/wire";
import { __resetConnectionsDraftCache } from "./connections/commands";
import type {
  AssignedItem,
  CapacitiesIntake,
  CapacitiesSettings,
  TagExclusionSettings,
} from "../model/types";

/* The two dot colours from ConnectionsScreen: healthy and amber. */
const GREEN = "rgb(31, 157, 107)";
const AMBER = "rgb(224, 160, 32)";

/* A fully read Capacities intake, so the Capacities dot's own amber state can
   only come from a warning rather than from the intake state. */
const HEALTHY_INTAKE: CapacitiesIntake = {
  mode: "direct",
  state: "ok",
  generation: 1,
  installedAt: 1_760_000_000,
  typeCheckTimes: {},
  coverage: { members: 20, evaluated: 20, malformed: 0, unreadable: 0 },
  unassignedCandidates: [],
};

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
    assignedOnStructures: {},
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
      assignedOnStructures: draft.assignedOnStructures,
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

const openExcludeTags = (rendered: ReturnType<ReturnType<typeof makeHarness>["ui"]>) =>
  fireEvent.click(rendered.getByRole("button", { name: "Exclude tags" }));

const openStructure = (
  rendered: ReturnType<ReturnType<typeof makeHarness>["ui"]>,
  name = "Task",
) => fireEvent.click(rendered.getByRole("button", { name }));

describe("Connections screen (U13)", () => {
  it("counts a rule, a mapping and a tag edit as three unsaved changes and Discard restores all three", async () => {
    const { rendered } = openConnections();
    await ready(rendered);

    // Rule: turn the Active status rule off.
    fireEvent.click(rendered.getByRole("switch", { name: /Status is active/ }));
    // Mapping: clear the RootTask property mapping.
    fireEvent.input(rendered.getByLabelText(/mapping for RootTask/i), { target: { value: "" } });
    // Tag list: remove the excluded tag (behind the Exclude tags nav item).
    openExcludeTags(rendered);
    fireEvent.click(rendered.getByRole("button", { name: /Remove tag #habituals/i }));

    await waitFor(() => expect(rendered.getByText(/3 unsaved changes/)).toBeTruthy());

    fireEvent.click(rendered.getByRole("button", { name: "Discard" }));
    await waitFor(() => expect(rendered.getByText(/No unsaved changes/)).toBeTruthy());
    openStructure(rendered);
    expect(
      rendered.getByRole("switch", { name: /Status is active/ }).getAttribute("aria-checked"),
    ).toBe("true");
    expect((rendered.getByLabelText(/mapping for RootTask/i) as HTMLInputElement).value).toBe("assigned");
    openExcludeTags(rendered);
    expect(rendered.getByRole("button", { name: /Remove tag #habituals/i })).toBeTruthy();
  });

  it("Save persists the rule, mapping and tag policies under their loaded revisions", async () => {
    const { h, rendered } = openConnections();
    await ready(rendered);

    fireEvent.click(rendered.getByRole("switch", { name: /Status is active/ }));
    fireEvent.input(rendered.getByLabelText(/mapping for RootTask/i), { target: { value: "" } });
    openExcludeTags(rendered);
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
        assignedOnStructures: {},
      }),
    );
    expect(h.controller.saveTagExclusionSettings).toHaveBeenCalledWith({
      expectedRevision: 0,
      tags: [],
      todoist: [],
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
    openExcludeTags(rendered);

    const stale = await waitFor(() =>
      rendered.getByText(/A saved tag no longer exists in Capacities/),
    );
    expect(stale).toBeTruthy();
    fireEvent.click(rendered.getByRole("button", { name: /Remove saved tag/i }));
    await waitFor(() =>
      expect(rendered.queryByText(/A saved tag no longer exists in Capacities/)).toBeNull(),
    );
  });

  it("scopes the preview rows and counts to the selected structure", async () => {
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
        // No explicit structure id: the identity's third segment scopes it.
        capacitiesStructureId: null,
        identity: "capacities:space-1:Other:row",
        capacitiesIdentity: "capacities:space-1:Other:row",
      }),
    ]);
    await ready(rendered);

    // Task (RootTask, mapped): Tagged is left out, Mapped is admitted, and the
    // item waiting on Other never leaks into this structure.
    const strip = rendered.getByRole("region", { name: /Preview/i });
    await waitFor(() => expect(within(strip).getByText(/1 admitted today/)).toBeTruthy());
    expect(within(strip).getByText(/0 waiting on mapping/)).toBeTruthy();
    expect(within(strip).getByText(/1 left out by tag/)).toBeTruthy();

    // The per-item rows live in the collapsed-by-default disclosure.
    const disclosure = rendered.getByText(/Preview items/).closest("details")!;
    expect(disclosure.hasAttribute("open")).toBe(false);
    fireEvent.click(within(disclosure).getByText(/Preview items/));
    expect(within(disclosure).getByText("Tagged task")).toBeTruthy();
    expect(within(disclosure).getByText("left out by tag")).toBeTruthy();
    expect(within(disclosure).getByText("Mapped task")).toBeTruthy();
    expect(within(disclosure).getByText("admitted")).toBeTruthy();
    expect(within(disclosure).queryByText("Unmapped task")).toBeNull();
    expect(within(disclosure).queryByText("waiting on mapping")).toBeNull();

    // Project (Other, unmapped): only its own waiting item shows.
    fireEvent.click(rendered.getByRole("button", { name: "Project" }));
    const otherStrip = rendered.getByRole("region", { name: /Preview/i });
    await waitFor(() => expect(within(otherStrip).getByText(/1 waiting on mapping/)).toBeTruthy());
    expect(within(otherStrip).getByText(/0 admitted today/)).toBeTruthy();
    const otherDisclosure = rendered.getByText(/Preview items/).closest("details")!;
    fireEvent.click(within(otherDisclosure).getByText(/Preview items/));
    expect(within(otherDisclosure).getByText("Unmapped task")).toBeTruthy();
    expect(within(otherDisclosure).queryByText("Mapped task")).toBeNull();

    // Removing the tag exclusion moves the tagged item out of "left out".
    openExcludeTags(rendered);
    fireEvent.click(rendered.getByRole("button", { name: /Remove tag #habituals/i }));
    openStructure(rendered);
    const freshStrip = rendered.getByRole("region", { name: /Preview/i });
    await waitFor(() => expect(within(freshStrip).getByText(/2 admitted today/)).toBeTruthy());
    expect(rendered.queryByText(/left out by tag/)).toBeNull();
  });

  it("shows the mapping hint, a structure-specific Remove name, and the Capacities sync pill", async () => {
    const { rendered } = openConnections();
    await ready(rendered);

    // Default structure is Task ("RootTask"), mapped to "assigned".
    expect(rendered.getByText("Mapped: assigned")).toBeTruthy();
    expect(rendered.getByRole("button", { name: "Remove structure Task" })).toBeTruthy();

    // An unmapped structure reads "Not mapped".
    fireEvent.click(rendered.getByRole("button", { name: "Project" }));
    expect(rendered.getByText("Not mapped")).toBeTruthy();

    // The Capacities source detail restores the aria-live sync pill.
    fireEvent.click(rendered.getByRole("button", { name: "Capacities" }));
    const pill = rendered.getByText("Capacities not synced");
    expect(pill.getAttribute("role")).toBe("status");
    expect(pill.getAttribute("aria-live")).toBe("polite");
  });

  it("refuses an invalid rule, states why, and keeps Save disabled (R33)", async () => {
    const { rendered } = openConnections();
    await ready(rendered);

    fireEvent.click(rendered.getByRole("switch", { name: /Deadline within/ }));
    fireEvent.input(rendered.getByLabelText(/Deadline horizon/i), { target: { value: "" } });

    await waitFor(() =>
      expect(rendered.getByText(/Use a nonnegative whole number/)).toBeTruthy(),
    );
    expect((rendered.getByRole("button", { name: "Save" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("returns to Plan tasks on Back to planning without losing unsaved Connections edits", async () => {
    const { h, rendered } = openConnections();
    await ready(rendered);

    fireEvent.click(rendered.getByRole("switch", { name: /Status is active/ }));
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

  it("renders the nav groups and the selected structure detail", async () => {
    const { rendered } = openConnections();
    await ready(rendered);
    for (const group of ["Sources", "Capacities structures", "Rules"]) {
      expect(rendered.getByText(group)).toBeTruthy();
    }
    for (const item of ["Todoist", "Calendar", "Capacities", "Exclude tags", "Advanced"]) {
      expect(rendered.getByRole("button", { name: item })).toBeTruthy();
    }
    expect(rendered.getByText("Capacities structure")).toBeTruthy();
    expect(rendered.getByRole("heading", { name: "Task" })).toBeTruthy();
    expect(rendered.getByRole("button", { name: /Discover from Capacities/ })).toBeTruthy();
  });

  it("reads each source's own health, so a Capacities-only warning leaves the Todoist and Calendar dots green (AE4)", async () => {
    const warning =
      "Capacities structure '05e6b2c1' is not in the published generation; needs Refresh.";
    // Every available structure is mapped, so the Capacities dot's amber comes
    // only from the warning attributed to it.
    const { h, rendered } = openConnections([], capSettings({ availableStructures: ["RootTask"] }));
    const inputs = h.store.getState().inputs!;
    h.store.dispatch({
      type: "INPUTS_LOADED",
      inputs: {
        ...inputs,
        sourceWarnings: [warning],
        sourceHealth: sourceHealthOf([warning]),
        capacitiesIntake: { ...HEALTHY_INTAKE },
      },
      ledger: h.store.getState().ledger!,
    });
    await ready(rendered);

    const dotOf = (name: string) =>
      (rendered.getByRole("button", { name }) as HTMLElement).querySelector(
        ".connections-navitem__dot",
      ) as HTMLElement;
    // A structure missing from the published generation was never read, so it
    // still blocks commit (OQ-P3) — but it is a Capacities warning, so it no
    // longer colours the Todoist or Calendar dot (R8, AE4).
    expect(dotOf("Todoist").style.background).toBe(GREEN);
    expect(dotOf("Calendar").style.background).toBe(GREEN);
    expect(dotOf("Capacities").style.background).toBe(AMBER);
    expect(
      rendered.getByText(
        "1 mapped type(s) not yet in the published generation; Refresh",
      ),
    ).toBeTruthy();

    // The status text under the source card reads that source's own health.
    const helperText = (unit: string) =>
      rendered.getByText(new RegExp(`${unit} in the current read`)).textContent ?? "";
    fireEvent.click(rendered.getByRole("button", { name: "Todoist" }));
    expect(helperText("tasks")).toContain("Healthy.");
    expect(helperText("tasks")).not.toContain("Degraded");
    fireEvent.click(rendered.getByRole("button", { name: "Calendar" }));
    expect(helperText("events")).toContain("Healthy.");
  });

  it("turns only the Todoist dot amber for a Todoist read failure (R8)", async () => {
    const warning = "Todoist read failed (timeout)";
    const { h, rendered } = openConnections([], capSettings({ availableStructures: ["RootTask"] }));
    const inputs = h.store.getState().inputs!;
    h.store.dispatch({
      type: "INPUTS_LOADED",
      inputs: {
        ...inputs,
        sourceWarnings: [warning],
        sourceHealth: sourceHealthOf([warning]),
        capacitiesIntake: { ...HEALTHY_INTAKE },
      },
      ledger: h.store.getState().ledger!,
    });
    await ready(rendered);

    const dotOf = (name: string) =>
      (rendered.getByRole("button", { name }) as HTMLElement).querySelector(
        ".connections-navitem__dot",
      ) as HTMLElement;
    expect(dotOf("Todoist").style.background).toBe(AMBER);
    expect(dotOf("Calendar").style.background).toBe(GREEN);
    expect(dotOf("Capacities").style.background).toBe(GREEN);

    const helperText = (unit: string) =>
      rendered.getByText(new RegExp(`${unit} in the current read`)).textContent ?? "";
    fireEvent.click(rendered.getByRole("button", { name: "Todoist" }));
    expect(helperText("tasks")).toContain("Degraded — the last read reported a problem.");
    fireEvent.click(rendered.getByRole("button", { name: "Calendar" }));
    expect(helperText("events")).toContain("Healthy.");
  });

  it("treats a calendar diagnostic as advisory and keeps the Calendar dot on the classifier's verdict (R8)", async () => {
    const warning = "Calendar read returned 2 unfixed events";
    // The operator made calendar diagnostics advisory (OQ-P1), and
    // ``sourceHealthBySource`` attributes only blocking warnings, so the
    // classifier currently reports the calendar source as ok. Pinned here so a
    // future attribution change is a deliberate test change, not a silent one.
    expect(sourceHealthOf([warning])).toBe("ok");
    expect(sourceHealthBySource([warning]).calendar).toBe("ok");

    const { h, rendered } = openConnections([], capSettings({ availableStructures: ["RootTask"] }));
    const inputs = h.store.getState().inputs!;
    h.store.dispatch({
      type: "INPUTS_LOADED",
      inputs: {
        ...inputs,
        sourceWarnings: [warning],
        sourceHealth: sourceHealthOf([warning]),
        capacitiesIntake: { ...HEALTHY_INTAKE },
      },
      ledger: h.store.getState().ledger!,
    });
    await ready(rendered);

    const dotOf = (name: string) =>
      (rendered.getByRole("button", { name }) as HTMLElement).querySelector(
        ".connections-navitem__dot",
      ) as HTMLElement;
    expect(dotOf("Calendar").style.background).toBe(GREEN);
    expect(dotOf("Todoist").style.background).toBe(GREEN);
    expect(dotOf("Capacities").style.background).toBe(GREEN);
  });

  it("explains what native rules are from the Admit when heading", async () => {
    const { rendered } = openConnections();
    await ready(rendered);

    const heading = rendered.getByText("Admit when");
    const info = within(heading.parentElement as HTMLElement).getByRole("button", {
      name: "What native rules are",
    });
    expect(info.getAttribute("title")).toBeNull();
    expect(rendered.queryByRole("tooltip")).toBeNull();

    // Focus opens the bubble and points the trigger at it.
    fireEvent.focus(info);
    const tooltip = rendered.getByRole("tooltip");
    expect(tooltip.textContent).toContain("Native rules");
    expect(tooltip.textContent).toMatch(/built-in admission rules/i);
    expect(tooltip.textContent).toMatch(/any one of them matches/i);
    expect(tooltip.textContent).toMatch(/completed and dropped/i);
    expect(tooltip.textContent).not.toMatch(/assigned/i);
    expect(info.getAttribute("aria-describedby")).toBe(tooltip.getAttribute("id"));

    fireEvent.blur(info);
    expect(rendered.queryByRole("tooltip")).toBeNull();

    // Hover serves pointer users from the same trigger.
    fireEvent.mouseEnter(info);
    expect(rendered.getByRole("tooltip").textContent).toMatch(/built-in admission rules/i);
    fireEvent.mouseLeave(info);
    expect(rendered.queryByRole("tooltip")).toBeNull();
  });
});
