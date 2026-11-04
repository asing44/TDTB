import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, waitFor, within } from "@testing-library/preact";
import { CapacitiesSettingsDrawer } from "./CapacitiesSettingsDrawer";
import { makeHarness } from "./test-harness";
import { capacitiesSettingsToWire } from "../adapters/wire";
import { ApiError } from "../adapters/api";
import type {
  AssignedItem,
  CapacitiesCatalogProperty,
  CapacitiesDeclarationDiagnostic,
} from "../model/types";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function openWithCapacityRow() {
  const h = makeHarness("ready");
  const inputs = h.store.getState().inputs!;
  const capacityItem: AssignedItem = {
    ...inputs.assigned[0],
    id: "Capacity task",
    name: "Capacity task",
    source: "capacities",
    path: "capacities://space-1/task-1",
    identity: "capacities:space-1:RootTask:task-1",
    types: ["RootTask"],
  };
  h.store.dispatch({
    type: "INPUTS_LOADED",
    inputs: { ...inputs, assigned: [...inputs.assigned.filter((r) => r.source !== "capacities"), capacityItem] },
    ledger: h.store.getState().ledger!,
  });
  h.store.dispatch({ type: "UI", patch: { capacitiesSettingsOpen: true } });
  const rendered = h.ui(<CapacitiesSettingsDrawer />);
  // Hub: the policy sections sit behind "Repair tools" > "Show technical details".
  fireEvent.click(rendered.getByRole("button", { name: /repair tools/i }));
  fireEvent.click(rendered.getByRole("button", { name: /show technical details/i }));
  return { h, rendered };
}

/** The admission picker is fed by the same provider discovery the source
    mapping editor uses: one local read for the space id, one discovery call.
    These stubs answer both so no test depends on the fixture's catalog. */
function stubAdmissionDiscovery(
  h: ReturnType<typeof makeHarness>,
  properties: CapacitiesCatalogProperty[],
) {
  vi.spyOn(h.controller, "loadCapacitiesSource").mockResolvedValue({
    source: { version: 1, revision: 1, spaceId: "space-1", structures: [] },
    persisted: true,
  });
  return vi.spyOn(h.controller, "discoverCapacitiesSource").mockResolvedValue({
    spaceId: "space-1",
    warnings: [],
    structures: [{ structureId: "custom-project", title: "custom-project", properties }],
  });
}

const BOOLEAN_PROPERTY: CapacitiesCatalogProperty = {
  propertyId: "assigned-prop-id",
  title: "Assigned",
  type: "boolean",
  writable: true,
  labelOptions: [],
};
const DATE_PROPERTY: CapacitiesCatalogProperty = {
  propertyId: "assigned-on-id",
  title: "Assigned on",
  type: "date",
  writable: false,
  labelOptions: [],
};
const TITLE_PROPERTY: CapacitiesCatalogProperty = {
  propertyId: "title-prop",
  title: "Name",
  type: "title",
  writable: true,
  labelOptions: [],
};

describe("CapacitiesSettingsDrawer", () => {
  it("loads native rules and lists known Capacities identities without inventing inventory", async () => {
    const { rendered } = openWithCapacityRow();
    expect(rendered.getAllByRole("status").some((el: HTMLElement) => el.textContent?.includes("Loading local policy"))).toBe(true);
    await waitFor(() => expect(rendered.getByText("Automatic task rules")).toBeTruthy());

    expect(rendered.getByText("Known Capacities items")).toBeTruthy();
    expect(rendered.getByText("Capacity task")).toBeTruthy();
    expect(rendered.getByText("capacities:space-1:RootTask:task-1")).toBeTruthy();
    expect(rendered.getByRole("checkbox", { name: /Active status/ })).toBeTruthy();
    expect((rendered.getByLabelText("Deadline coming up") as HTMLInputElement).value).toBe("2");
    expect(rendered.getByText(/not a full browser of Capacities/)).toBeTruthy();
  });

  it("explains native rules from the section heading and the per-structure label", async () => {
    const { rendered } = openWithCapacityRow();
    await waitFor(() => expect(rendered.getByText("Automatic task rules")).toBeTruthy());

    // The affordance is reachable by role and accessible name, and it is not
    // a pointer-only native title=.
    const section = rendered.getByRole("heading", { name: "Automatic task rules" }).closest(
      ".setup-section",
    ) as HTMLElement;
    const headingInfo = within(section).getByRole("button", { name: "What automatic task rules are" });
    expect(headingInfo.getAttribute("title")).toBeNull();
    expect(rendered.queryByRole("tooltip")).toBeNull();

    // Focus produces the bubble and wires the trigger to it.
    fireEvent.focus(headingInfo);
    const tooltip = rendered.getByRole("tooltip");
    expect(tooltip.textContent).toContain("Automatic task rules");
    expect(tooltip.textContent).toMatch(/built-in rules that decide which tasks come in/i);
    expect(tooltip.textContent).toMatch(/due today or earlier/i);
    expect(tooltip.textContent).toMatch(/deadline is overdue or coming up soon/i);
    expect(tooltip.textContent).toMatch(/completed and dropped/i);
    expect(tooltip.textContent).toMatch(/follows only its own rule/i);
    // The assignment mapping is a separate concern and must not blend in here.
    expect(tooltip.textContent).not.toMatch(/assigned/i);
    expect(headingInfo.getAttribute("aria-describedby")).toBe(tooltip.getAttribute("id"));
    fireEvent.blur(headingInfo);
    expect(rendered.queryByRole("tooltip")).toBeNull();

    // The per-structure "Automatic rules on / off" label carries the same text.
    const row = rendered
      .getByRole("checkbox", { name: "Native admission for custom-project" })
      .closest(".capacities-object-row") as HTMLElement;
    const rowInfo = within(row).getByRole("button", { name: "What automatic task rules are" });
    // This trigger sits at the right edge of the panel, so its bubble is
    // right-aligned; a centred one runs past the window and clips its text.
    expect(rowInfo.closest(".tooltip-anchor")?.className).toContain("tooltip-anchor--end");
    fireEvent.focus(rowInfo);
    const rowTooltip = rendered.getByRole("tooltip");
    expect(rowTooltip.textContent).toMatch(/built-in rules that decide which tasks come in/i);
    expect(rowInfo.getAttribute("aria-describedby")).toBe(rowTooltip.getAttribute("id"));
    fireEvent.blur(rowInfo);
    expect(rendered.queryByRole("tooltip")).toBeNull();
  });

  it("saves the full replacement draft with the loaded revision and keeps exclusions local", async () => {
    const { h, rendered } = openWithCapacityRow();
    await waitFor(() => expect(rendered.getByText("Capacity task")).toBeTruthy());

    fireEvent.click(rendered.getByRole("checkbox", { name: /Available to TDTB/ }));
    fireEvent.click(rendered.getByRole("checkbox", { name: /Active status/ }));
    fireEvent.input(rendered.getByLabelText("Deadline coming up"), { target: { value: "5" } });
    const save = vi.spyOn(h.controller, "saveCapacitiesSettings");

    fireEvent.click(rendered.getByRole("button", { name: "Save Capacities settings" }));
    await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
    expect(save).toHaveBeenCalledWith({
      expectedRevision: 0,
      nativeTaskAuto: {
        activeEnabled: false,
        dueEnabled: true,
        deadlineEnabled: true,
        deadlineHorizonDays: 5,
      },
      excluded: ["capacities:space-1:RootTask:task-1"],
      activeStructures: [],
      nativeTaskStructures: ["RootTask", "Task"],
      activeStatuses: ["active"],
      assignedStructures: {},
      assignedOnStructures: {},
    });
    await waitFor(() => expect(h.store.getState().ui.capacitiesSettingsOpen).toBe(false));
  });

  it("preserves non-empty activeStructures across a native Task Auto save (round-trip regression)", async () => {
    const h = makeHarness("ready");
    const inputs = h.store.getState().inputs!;
    const capacityItem: AssignedItem = {
      ...inputs.assigned[0],
      id: "Capacity task",
      name: "Capacity task",
      source: "capacities",
      path: "capacities://space-1/task-1",
      identity: "capacities:space-1:RootTask:task-1",
      types: ["RootTask"],
    };
    h.store.dispatch({
      type: "INPUTS_LOADED",
      inputs: { ...inputs, assigned: [...inputs.assigned.filter((r) => r.source !== "capacities"), capacityItem] },
      ledger: h.store.getState().ledger!,
    });
    // The server returns a non-empty inclusion set the drawer has no editor for.
    vi.spyOn(h.controller, "loadCapacitiesSettings").mockResolvedValue({
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
      activeStructures: ["custom-project", "0d194525-c5a1-4af5-bb62-202b83006b5e"],
      nativeTaskStructures: ["Task"],
      activeStatuses: ["active", "In Progress"],
      assignedStructures: {},
      assignedOnStructures: {},
      availableStructures: ["custom-project", "0d194525-c5a1-4af5-bb62-202b83006b5e"],
      structureTitles: {},
      declarationDiagnostics: [],
    });
    h.store.dispatch({ type: "UI", patch: { capacitiesSettingsOpen: true } });
    const rendered = h.ui(<CapacitiesSettingsDrawer />);
    // Hub: the policy sections sit behind "Repair tools" > "Show technical details".
    fireEvent.click(rendered.getByRole("button", { name: /repair tools/i }));
    fireEvent.click(rendered.getByRole("button", { name: /show technical details/i }));
    await waitFor(() => expect(rendered.getByText("Automatic task rules")).toBeTruthy());

    // Toggle a native Task Auto control — the save path that, before the fix,
    // silently cleared active_structures via the full-replacement body.
    fireEvent.click(rendered.getByRole("checkbox", { name: /Active status/ }));
    const save = vi.spyOn(h.controller, "saveCapacitiesSettings");
    fireEvent.click(rendered.getByRole("button", { name: "Save Capacities settings" }));
    await waitFor(() => expect(save).toHaveBeenCalledTimes(1));

    const draft = save.mock.calls[0][0];
    expect(draft.nativeTaskAuto.activeEnabled).toBe(false); // the toggle landed
    expect(draft.activeStructures).toEqual([
      "custom-project",
      "0d194525-c5a1-4af5-bb62-202b83006b5e",
    ]);
    // The outgoing full-replacement body keeps the server's inclusion set.
    const body = capacitiesSettingsToWire(draft);
    expect(body.active_structures).toEqual({
      "custom-project": true,
      "0d194525-c5a1-4af5-bb62-202b83006b5e": true,
    });
    // The two admission keys ride along on every save too — omission would
    // reset them to the documented defaults under full replacement.
    expect(draft.nativeTaskStructures).toEqual(["Task"]);
    expect(draft.activeStatuses).toEqual(["active", "In Progress"]);
    expect(body.native_task_structures).toEqual(["Task"]);
    expect(body.active_statuses).toEqual(["In Progress", "active"]);
    // Regression guard: before the fix draftOf dropped the field, so the body
    // would have carried an empty active_structures object here.
    expect(Object.keys(body.active_structures).length).toBeGreaterThan(0);
  });

  it("admitting a type as Status only adds it to the outgoing save body", async () => {
    const { h, rendered } = openWithCapacityRow();
    await waitFor(() => expect(rendered.getByText("How each item type gets in")).toBeTruthy());

    // The row control is the only admission surface: re-query after each
    // change (Preact re-renders the row, so an old node goes stale).
    const select = () =>
      rendered.getByLabelText("Admission for custom-project") as HTMLSelectElement;
    expect(select().value).toBe("off");
    fireEvent.change(select(), { target: { value: "status" } });
    expect(select().value).toBe("status");

    const save = vi.spyOn(h.controller, "saveCapacitiesSettings");
    fireEvent.click(rendered.getByRole("button", { name: "Save Capacities settings" }));
    await waitFor(() => expect(save).toHaveBeenCalledTimes(1));

    expect(capacitiesSettingsToWire(save.mock.calls[0][0]).active_structures).toEqual({
      "custom-project": true,
    });
    expect(save.mock.calls[0][0].assignedStructures).toEqual({});
    expect(save.mock.calls[0][0].assignedOnStructures).toEqual({});
  });

  it("switching a type Off removes it from the outgoing save body", async () => {
    const h = makeHarness("ready");
    vi.spyOn(h.controller, "loadCapacitiesSettings").mockResolvedValue({
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
      activeStructures: ["custom-project"],
      nativeTaskStructures: ["RootTask", "Task"],
      activeStatuses: ["active"],
      assignedStructures: {},
      assignedOnStructures: {},
      availableStructures: ["custom-project", "0d194525-c5a1-4af5-bb62-202b83006b5e"],
      structureTitles: {},
      declarationDiagnostics: [],
    });
    h.store.dispatch({ type: "UI", patch: { capacitiesSettingsOpen: true } });
    const rendered = h.ui(<CapacitiesSettingsDrawer />);
    // Hub: the policy sections sit behind "Repair tools" > "Show technical details".
    fireEvent.click(rendered.getByRole("button", { name: /repair tools/i }));
    fireEvent.click(rendered.getByRole("button", { name: /show technical details/i }));
    await waitFor(() => expect(rendered.getByText("How each item type gets in")).toBeTruthy());

    const select = () =>
      rendered.getByLabelText("Admission for custom-project") as HTMLSelectElement;
    expect(select().value).toBe("status");
    fireEvent.change(select(), { target: { value: "off" } });
    expect(select().value).toBe("off");

    const save = vi.spyOn(h.controller, "saveCapacitiesSettings");
    fireEvent.click(rendered.getByRole("button", { name: "Save Capacities settings" }));
    await waitFor(() => expect(save).toHaveBeenCalledTimes(1));

    expect(capacitiesSettingsToWire(save.mock.calls[0][0]).active_structures).toEqual({});
  });

  it("preserves a stale active structure not present in availableStructures across a save", async () => {
    const h = makeHarness("ready");
    vi.spyOn(h.controller, "loadCapacitiesSettings").mockResolvedValue({
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
      activeStructures: ["legacy-structure", "custom-project"],
      nativeTaskStructures: ["RootTask", "Task"],
      activeStatuses: ["active"],
      assignedStructures: {},
      assignedOnStructures: {},
      availableStructures: ["custom-project"],
      structureTitles: {},
      declarationDiagnostics: [],
    });
    h.store.dispatch({ type: "UI", patch: { capacitiesSettingsOpen: true } });
    const rendered = h.ui(<CapacitiesSettingsDrawer />);
    // Hub: the policy sections sit behind "Repair tools" > "Show technical details".
    fireEvent.click(rendered.getByRole("button", { name: /repair tools/i }));
    fireEvent.click(rendered.getByRole("button", { name: /show technical details/i }));
    await waitFor(() => expect(rendered.getByText("How each item type gets in")).toBeTruthy());

    // The stale id is surfaced rather than hidden.
    expect(rendered.getByText(/Active-item types that are not set up to appear/)).toBeTruthy();
    expect(rendered.getByText("legacy-structure")).toBeTruthy();

    const save = vi.spyOn(h.controller, "saveCapacitiesSettings");
    fireEvent.click(rendered.getByRole("button", { name: "Save Capacities settings" }));
    await waitFor(() => expect(save).toHaveBeenCalledTimes(1));

    expect(save.mock.calls[0][0].activeStructures).toEqual(["legacy-structure", "custom-project"]);
    expect(capacitiesSettingsToWire(save.mock.calls[0][0]).active_structures).toEqual({
      "legacy-structure": true,
      "custom-project": true,
    });
  });

  it("renders an explicit empty state when no structures are available", async () => {
    const h = makeHarness("ready");
    vi.spyOn(h.controller, "loadCapacitiesSettings").mockResolvedValue({
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
      nativeTaskStructures: [],
      activeStatuses: [],
      assignedStructures: {},
      assignedOnStructures: {},
      availableStructures: [],
      structureTitles: {},
      declarationDiagnostics: [],
    });
    h.store.dispatch({ type: "UI", patch: { capacitiesSettingsOpen: true } });
    const rendered = h.ui(<CapacitiesSettingsDrawer />);
    // Hub: the policy sections sit behind "Repair tools" > "Show technical details".
    fireEvent.click(rendered.getByRole("button", { name: /repair tools/i }));
    fireEvent.click(rendered.getByRole("button", { name: /show technical details/i }));
    await waitFor(() => expect(rendered.getByText("How each item type gets in")).toBeTruthy());

    expect(
      rendered.getByText(
        "Your Capacities setup lists no types yet; saved settings are kept when you save.",
      ),
    ).toBeTruthy();
    expect(rendered.queryByLabelText(/^Admission for /)).toBeNull();
  });

  it("renders both admission keys, reusing the available-structure inventory", async () => {
    const h = makeHarness("ready");
    vi.spyOn(h.controller, "loadCapacitiesSettings").mockResolvedValue({
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
      nativeTaskStructures: ["custom-project"],
      activeStatuses: ["In Progress", "active"],
      assignedStructures: {},
      assignedOnStructures: {},
      availableStructures: ["custom-project", "0d194525-c5a1-4af5-bb62-202b83006b5e"],
      structureTitles: {},
      declarationDiagnostics: [],
    });
    h.store.dispatch({ type: "UI", patch: { capacitiesSettingsOpen: true } });
    const rendered = h.ui(<CapacitiesSettingsDrawer />);
    // Hub: the policy sections sit behind "Repair tools" > "Show technical details".
    fireEvent.click(rendered.getByRole("button", { name: /repair tools/i }));
    fireEvent.click(rendered.getByRole("button", { name: /show technical details/i }));
    await waitFor(() => expect(rendered.getByText("What counts as active")).toBeTruthy());

    // The structure list is the same source mapping the Active
    // pull editor already enumerates — no new discovery path.
    expect(rendered.getByText(/same list as the per-type admission control/)).toBeTruthy();
    expect(
      (rendered.getByRole("checkbox", { name: "Native admission for custom-project" }) as HTMLInputElement).checked,
    ).toBe(true);
    expect(
      (rendered.getByRole("checkbox", { name: "Native admission for 0d194525-c5a1-4af5-bb62-202b83006b5e" }) as HTMLInputElement).checked,
    ).toBe(false);
    expect(rendered.getByText("In Progress")).toBeTruthy();
    expect(rendered.getByText("active")).toBeTruthy();
  });

  it("always sends both admission keys on an untouched save", async () => {
    const { h, rendered } = openWithCapacityRow();
    await waitFor(() => expect(rendered.getByText("What counts as active")).toBeTruthy());

    // Nothing in the admission section was edited. Full replacement still
    // requires both keys to be present, or the server resets them.
    const save = vi.spyOn(h.controller, "saveCapacitiesSettings");
    fireEvent.click(rendered.getByRole("button", { name: "Save Capacities settings" }));
    await waitFor(() => expect(save).toHaveBeenCalledTimes(1));

    const body = capacitiesSettingsToWire(save.mock.calls[0][0]);
    expect(body.native_task_structures).toEqual(["RootTask", "Task"]);
    expect(body.active_statuses).toEqual(["active"]);
  });

  it("always sends the assignment declarations on an untouched save", async () => {
    const { h, rendered } = openWithCapacityRow();
    await waitFor(() => expect(rendered.getByText("How each item type gets in")).toBeTruthy());

    // Nothing in the declarations section was edited. The save route is a
    // full replacement, so the key must still ride along or the server would
    // wipe every previous declaration.
    const save = vi.spyOn(h.controller, "saveCapacitiesSettings");
    fireEvent.click(rendered.getByRole("button", { name: "Save Capacities settings" }));
    await waitFor(() => expect(save).toHaveBeenCalledTimes(1));

    expect(save.mock.calls[0][0].assignedStructures).toEqual({});
    const body = capacitiesSettingsToWire(save.mock.calls[0][0]);
    expect("assigned_structures" in body).toBe(true);
    expect(body.assigned_structures).toEqual({});
  });

  it("declares a per-structure assignment property through the picker", async () => {
    const { h, rendered } = openWithCapacityRow();
    stubAdmissionDiscovery(h, [TITLE_PROPERTY, BOOLEAN_PROPERTY, DATE_PROPERTY]);
    await waitFor(() => expect(rendered.getByText("How each item type gets in")).toBeTruthy());

    fireEvent.change(rendered.getByLabelText("Admission for custom-project"), {
      target: { value: "assigned" },
    });
    // No schema has been looked up yet: the row offers the lookup, not an
    // empty picker.
    expect(
      rendered.queryByLabelText("Assignment property for custom-project"),
    ).toBeNull();
    await waitFor(() =>
      expect(rendered.getByLabelText("Look up properties for custom-project")).toBeTruthy(),
    );
    fireEvent.click(rendered.getByLabelText("Look up properties for custom-project"));

    const picker = () =>
      rendered.getByLabelText("Assignment property for custom-project") as HTMLSelectElement;
    await waitFor(() => expect(picker()).toBeTruthy());
    // Only checkbox-kind properties are offered for the checkbox mode.
    const offered = Array.from(picker().options).map((option) => option.value);
    expect(offered).toEqual(["", "assigned-prop-id"]);
    fireEvent.change(picker(), { target: { value: "assigned-prop-id" } });
    expect(picker().value).toBe("assigned-prop-id");

    const save = vi.spyOn(h.controller, "saveCapacitiesSettings");
    fireEvent.click(rendered.getByRole("button", { name: "Save Capacities settings" }));
    await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
    expect(capacitiesSettingsToWire(save.mock.calls[0][0]).assigned_structures).toEqual({
      "custom-project": "assigned-prop-id",
    });
    // The Requires mode also switched the type on: the old silent-no-op.
    expect(save.mock.calls[0][0].activeStructures).toEqual(["custom-project"]);
    expect(save.mock.calls[0][0].assignedOnStructures).toEqual({});
  });

  it("clears a declaration when the type goes Off", async () => {
    const h = makeHarness("ready");
    vi.spyOn(h.controller, "loadCapacitiesSettings").mockResolvedValue({
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
      assignedStructures: { "custom-project": "old-prop" },
      assignedOnStructures: {},
      availableStructures: ["custom-project"],
      structureTitles: {},
      declarationDiagnostics: [],
    });
    h.store.dispatch({ type: "UI", patch: { capacitiesSettingsOpen: true } });
    const rendered = h.ui(<CapacitiesSettingsDrawer />);
    // Hub: the policy sections sit behind "Repair tools" > "Show technical details".
    fireEvent.click(rendered.getByRole("button", { name: /repair tools/i }));
    fireEvent.click(rendered.getByRole("button", { name: /show technical details/i }));
    await waitFor(() => expect(rendered.getByText("How each item type gets in")).toBeTruthy());

    // A saved declaration reads back as its Requires mode, not as a raw id.
    const select = () =>
      rendered.getByLabelText("Admission for custom-project") as HTMLSelectElement;
    expect(select().value).toBe("assigned");
    expect(rendered.getByText("old-prop")).toBeTruthy();

    fireEvent.change(select(), { target: { value: "off" } });
    expect(select().value).toBe("off");

    const save = vi.spyOn(h.controller, "saveCapacitiesSettings");
    fireEvent.click(rendered.getByRole("button", { name: "Save Capacities settings" }));
    await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
    expect(capacitiesSettingsToWire(save.mock.calls[0][0]).assigned_structures).toEqual({});
    expect(save.mock.calls[0][0].assignedOnStructures).toEqual({});
    expect(save.mock.calls[0][0].activeStructures).toEqual([]);
  });

  it("keeps a saved declaration outside the source mapping and round-trips it", async () => {
    const h = makeHarness("ready");
    vi.spyOn(h.controller, "loadCapacitiesSettings").mockResolvedValue({
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
      assignedStructures: {
        "legacy-declared": "legacy-prop",
        "custom-project": "current-prop",
      },
      assignedOnStructures: {},
      availableStructures: ["custom-project"],
      structureTitles: {},
      declarationDiagnostics: [],
    });
    h.store.dispatch({ type: "UI", patch: { capacitiesSettingsOpen: true } });
    const rendered = h.ui(<CapacitiesSettingsDrawer />);
    // Hub: the policy sections sit behind "Repair tools" > "Show technical details".
    fireEvent.click(rendered.getByRole("button", { name: /repair tools/i }));
    fireEvent.click(rendered.getByRole("button", { name: /show technical details/i }));
    await waitFor(() => expect(rendered.getByText("How each item type gets in")).toBeTruthy());

    // Retained and surfaced, never silently dropped. (The native and active
    // stale blocks use the same wording, so count rather than pick one.)
    expect(rendered.getByText(/Types with settings that are not set up to appear/)).toBeTruthy();
    expect(rendered.getAllByText(/kept as they are when you save/).length).toBeGreaterThan(0);
    expect(rendered.getByText("legacy-declared")).toBeTruthy();

    const save = vi.spyOn(h.controller, "saveCapacitiesSettings");
    fireEvent.click(rendered.getByRole("button", { name: "Save Capacities settings" }));
    await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
    expect(save.mock.calls[0][0].assignedStructures).toEqual({
      "legacy-declared": "legacy-prop",
      "custom-project": "current-prop",
    });
    expect(capacitiesSettingsToWire(save.mock.calls[0][0]).assigned_structures).toEqual({
      "custom-project": "current-prop",
      "legacy-declared": "legacy-prop",
    });
  });

  it("shows the adapter's ignored-declaration diagnostic verbatim beside the control", async () => {
    const h = makeHarness("ready");
    const inputs = h.store.getState().inputs!;
    const warning =
      "ignored Capacities assignment declaration for structure 'custom-project': " +
      "unknown property 'assigned-prop'";
    const coverage =
      "Capacities partial — 20 evaluated · 51 deferred across contributing structures.";
    h.store.dispatch({
      type: "INPUTS_LOADED",
      inputs: { ...inputs, sourceWarnings: [warning, coverage] },
      ledger: h.store.getState().ledger!,
    });
    vi.spyOn(h.controller, "loadCapacitiesSettings").mockResolvedValue({
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
      assignedStructures: { "custom-project": "assigned-prop" },
      assignedOnStructures: {},
      availableStructures: ["custom-project"],
      structureTitles: {},
      declarationDiagnostics: [],
    });
    h.store.dispatch({ type: "UI", patch: { capacitiesSettingsOpen: true } });
    const rendered = h.ui(<CapacitiesSettingsDrawer />);
    // Hub: the policy sections sit behind "Repair tools" > "Show technical details".
    fireEvent.click(rendered.getByRole("button", { name: /repair tools/i }));
    fireEvent.click(rendered.getByRole("button", { name: /show technical details/i }));
    await waitFor(() => expect(rendered.getByText("Settings we skipped")).toBeTruthy());

    // The server's own wording, passed through unchanged — never paraphrased.
    expect(rendered.getByText(warning)).toBeTruthy();
    // The partial-coverage warning belongs to the readiness rail, not here.
    expect(rendered.queryByText(coverage)).toBeNull();
    // The declaration stays editable so the operator can correct it.
    expect(
      within(
        rendered.getByLabelText("Admission for custom-project").closest(
          ".capacities-admission",
        ) as HTMLElement,
      ).getByText("assigned-prop"),
    ).toBeTruthy();
  });

  it("keeps a saved native structure outside the source mapping and round-trips it", async () => {
    const h = makeHarness("ready");
    vi.spyOn(h.controller, "loadCapacitiesSettings").mockResolvedValue({
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
      nativeTaskStructures: ["legacy-native", "custom-project"],
      activeStatuses: ["active"],
      assignedStructures: {},
      assignedOnStructures: {},
      availableStructures: ["custom-project"],
      structureTitles: {},
      declarationDiagnostics: [],
    });
    h.store.dispatch({ type: "UI", patch: { capacitiesSettingsOpen: true } });
    const rendered = h.ui(<CapacitiesSettingsDrawer />);
    // Hub: the policy sections sit behind "Repair tools" > "Show technical details".
    fireEvent.click(rendered.getByRole("button", { name: /repair tools/i }));
    fireEvent.click(rendered.getByRole("button", { name: /show technical details/i }));
    await waitFor(() => expect(rendered.getByText("What counts as active")).toBeTruthy());

    // Retained and surfaced, never silently dropped.
    expect(rendered.getByText(/Types not set up to appear \(automatic rules\)/)).toBeTruthy();
    expect(rendered.getByText("legacy-native")).toBeTruthy();

    const save = vi.spyOn(h.controller, "saveCapacitiesSettings");
    fireEvent.click(rendered.getByRole("button", { name: "Save Capacities settings" }));
    await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
    expect(save.mock.calls[0][0].nativeTaskStructures).toEqual(["legacy-native", "custom-project"]);
    expect(capacitiesSettingsToWire(save.mock.calls[0][0]).native_task_structures).toEqual([
      "custom-project",
      "legacy-native",
    ]);
  });

  it("adds and removes status values as a free-text list", async () => {
    const { h, rendered } = openWithCapacityRow();
    await waitFor(() => expect(rendered.getByText("Status values")).toBeTruthy());

    fireEvent.input(rendered.getByLabelText("Add a status value"), {
      target: { value: "In Progress" },
    });
    fireEvent.click(rendered.getByRole("button", { name: "Add status" }));
    expect(rendered.getByText("In Progress")).toBeTruthy();

    fireEvent.click(rendered.getByRole("button", { name: "Remove status active" }));
    expect(rendered.queryByText("active")).toBeNull();

    const save = vi.spyOn(h.controller, "saveCapacitiesSettings");
    fireEvent.click(rendered.getByRole("button", { name: "Save Capacities settings" }));
    await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
    expect(capacitiesSettingsToWire(save.mock.calls[0][0]).active_statuses).toEqual([
      "In Progress",
    ]);
  });

  it("shows load failures and offers an explicit reload", async () => {
    const h = makeHarness("ready");
    vi.spyOn(h.controller, "loadCapacitiesSettings").mockRejectedValue(
      new Error("local settings unavailable"),
    );
    h.store.dispatch({ type: "UI", patch: { capacitiesSettingsOpen: true } });
    const rendered = h.ui(<CapacitiesSettingsDrawer />);
    // Hub: the policy sections sit behind "Repair tools" > "Show technical details".
    fireEvent.click(rendered.getByRole("button", { name: /repair tools/i }));
    fireEvent.click(rendered.getByRole("button", { name: /show technical details/i }));

    await waitFor(() => expect(rendered.getByRole("alert")).toBeTruthy());
    expect(rendered.getByRole("alert").textContent).toContain("local settings unavailable");
    expect(rendered.getByRole("button", { name: "Reload settings" })).toBeTruthy();
  });

  it("keeps the drawer open and surfaces a save conflict", async () => {
    const { h, rendered } = openWithCapacityRow();
    await waitFor(() => expect(rendered.getByText("Capacity task")).toBeTruthy());
    vi.spyOn(h.controller, "saveCapacitiesSettings").mockRejectedValue(
      new Error("Capacities settings changed since they were read; reload and retry."),
    );

    fireEvent.click(rendered.getByRole("button", { name: "Save Capacities settings" }));
    await waitFor(() => expect(rendered.getByRole("alert")).toBeTruthy());
    expect(rendered.getByRole("alert").textContent).toContain("reload and retry");
    expect(rendered.getByRole("button", { name: "Reload settings" })).toBeTruthy();
    expect(h.store.getState().ui.capacitiesSettingsOpen).toBe(true);
  });

  it("shows observed structure titles beside the raw ids in every structure list", async () => {
    const { rendered } = openWithCapacityRow();
    const groupNames = [
      "Capacities structures admitted to the native Task Auto rules",
      "Capacities admission per item type",
    ];
    await waitFor(() =>
      expect(rendered.getByRole("group", { name: groupNames[1] })).toBeTruthy(),
    );

    for (const name of groupNames) {
      const group = within(rendered.getByRole("group", { name }));
      // The titled structure leads with its observed title...
      const title = group.getByText("Project");
      expect(title.tagName.toLowerCase()).toBe("strong");
      // ...and the id stays visible beside it, in the same row. The admission
      // row shortens a raw provider UUID; the native list keeps it whole.
      const titledId = group.getByText(
        name === "Capacities admission per item type"
          ? "0d194525…"
          : "0d194525-c5a1-4af5-bb62-202b83006b5e",
      );
      expect(titledId.tagName.toLowerCase()).toBe("code");
      expect(title.parentElement).toBe(titledId.parentElement);
      // An untitled structure shows its id alone, never a blank label.
      const untitledId = group.getByText("custom-project");
      expect(untitledId.parentElement?.querySelector("strong")).toBeNull();
      expect(untitledId.parentElement?.textContent).toBe("custom-project");
    }
    // The title is display-only: admission still keys on the raw structure id.
    expect(
      rendered.getByLabelText("Admission for Project"),
    ).toBeTruthy();
  });

  it("shows the id once when the observed title repeats it", async () => {
    const h = makeHarness("ready");
    vi.spyOn(h.controller, "loadCapacitiesSettings").mockResolvedValue({
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
      nativeTaskStructures: [],
      activeStatuses: ["active"],
      assignedStructures: {},
      assignedOnStructures: {},
      availableStructures: ["root-task"],
      // The server falls back to the id when a structure has no display
      // name, so the repeated title must render as the id, not twice.
      structureTitles: { "root-task": "root-task" },
      declarationDiagnostics: [],
    });
    h.store.dispatch({ type: "UI", patch: { capacitiesSettingsOpen: true } });
    const rendered = h.ui(<CapacitiesSettingsDrawer />);
    // Hub: the policy sections sit behind "Repair tools" > "Show technical details".
    fireEvent.click(rendered.getByRole("button", { name: /repair tools/i }));
    fireEvent.click(rendered.getByRole("button", { name: /show technical details/i }));
    const group = () =>
      within(
        rendered.getByRole("group", {
          name: "Capacities structures admitted to the native Task Auto rules",
        }),
      );
    await waitFor(() => expect(group().getByText("root-task")).toBeTruthy());

    expect(group().getAllByText("root-task").length).toBe(1);
    expect(group().getByText("root-task").parentElement?.querySelector("strong")).toBeNull();
  });

  it("keeps raw ids in stale rows even when the title map has an entry", async () => {
    const h = makeHarness("ready");
    vi.spyOn(h.controller, "loadCapacitiesSettings").mockResolvedValue({
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
      activeStructures: ["legacy-structure"],
      nativeTaskStructures: [],
      activeStatuses: ["active"],
      assignedStructures: {},
      assignedOnStructures: {},
      availableStructures: ["custom-project"],
      structureTitles: { "legacy-structure": "Legacy Project" },
      declarationDiagnostics: [],
    });
    h.store.dispatch({ type: "UI", patch: { capacitiesSettingsOpen: true } });
    const rendered = h.ui(<CapacitiesSettingsDrawer />);
    // Hub: the policy sections sit behind "Repair tools" > "Show technical details".
    fireEvent.click(rendered.getByRole("button", { name: /repair tools/i }));
    fireEvent.click(rendered.getByRole("button", { name: /show technical details/i }));
    await waitFor(() =>
      expect(rendered.getByText(/Active-item types that are not set up to appear/)).toBeTruthy(),
    );

    // The stale id is outside the source mapping, so it keeps the raw id
    // and never borrows a title the drawer does not consider available.
    expect(rendered.getByText("legacy-structure")).toBeTruthy();
    expect(rendered.queryByText("Legacy Project")).toBeNull();
  });

  it("Requires Assigned date writes only the date map and switches the type on", async () => {
    const h = makeHarness("ready");
    stubAdmissionDiscovery(h, [TITLE_PROPERTY, BOOLEAN_PROPERTY, DATE_PROPERTY]);
    vi.spyOn(h.controller, "loadCapacitiesSettings").mockResolvedValue({
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
      availableStructures: ["custom-project"],
      structureTitles: {},
      declarationDiagnostics: [],
    });
    h.store.dispatch({ type: "UI", patch: { capacitiesSettingsOpen: true } });
    const rendered = h.ui(<CapacitiesSettingsDrawer />);
    fireEvent.click(rendered.getByRole("button", { name: /repair tools/i }));
    fireEvent.click(rendered.getByRole("button", { name: /show technical details/i }));
    await waitFor(() => expect(rendered.getByText("How each item type gets in")).toBeTruthy());

    // The one-line help states the coupling that used to be silent.
    expect(
      rendered.getByText("Switching a type to a Requires mode also turns it on."),
    ).toBeTruthy();

    fireEvent.change(rendered.getByLabelText("Admission for custom-project"), {
      target: { value: "assignedOn" },
    });
    await waitFor(() =>
      expect(rendered.getByLabelText("Look up properties for custom-project")).toBeTruthy(),
    );
    fireEvent.click(rendered.getByLabelText("Look up properties for custom-project"));

    const picker = () =>
      rendered.getByLabelText("Assignment property for custom-project") as HTMLSelectElement;
    await waitFor(() => expect(picker()).toBeTruthy());
    // Only date-kind properties are offered for the date mode.
    expect(Array.from(picker().options).map((option) => option.value)).toEqual([
      "",
      "assigned-on-id",
    ]);
    fireEvent.change(picker(), { target: { value: "assigned-on-id" } });
    expect(picker().value).toBe("assigned-on-id");

    const save = vi.spyOn(h.controller, "saveCapacitiesSettings");
    fireEvent.click(rendered.getByRole("button", { name: "Save Capacities settings" }));
    await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
    expect(save.mock.calls[0][0].assignedOnStructures).toEqual({
      "custom-project": "assigned-on-id",
    });
    expect(save.mock.calls[0][0].assignedStructures).toEqual({});
    expect(save.mock.calls[0][0].activeStructures).toEqual(["custom-project"]);
  });

  it("switching a date declaration to the checkbox mode moves no id across kinds", async () => {
    const h = makeHarness("ready");
    vi.spyOn(h.controller, "loadCapacitiesSettings").mockResolvedValue({
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
      activeStructures: ["custom-project"],
      nativeTaskStructures: ["RootTask", "Task"],
      activeStatuses: ["active"],
      assignedStructures: {},
      assignedOnStructures: { "custom-project": "assigned-on-id" },
      availableStructures: ["custom-project"],
      structureTitles: {},
      declarationDiagnostics: [],
    });
    h.store.dispatch({ type: "UI", patch: { capacitiesSettingsOpen: true } });
    const rendered = h.ui(<CapacitiesSettingsDrawer />);
    fireEvent.click(rendered.getByRole("button", { name: /repair tools/i }));
    fireEvent.click(rendered.getByRole("button", { name: /show technical details/i }));
    await waitFor(() => expect(rendered.getByText("How each item type gets in")).toBeTruthy());

    const select = () =>
      rendered.getByLabelText("Admission for custom-project") as HTMLSelectElement;
    expect(select().value).toBe("assignedOn");
    fireEvent.change(select(), { target: { value: "assigned" } });

    const save = vi.spyOn(h.controller, "saveCapacitiesSettings");
    fireEvent.click(rendered.getByRole("button", { name: "Save Capacities settings" }));
    await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
    // The date declaration is cleared and nothing was invented for the
    // checkbox map: a date property id is never offered as a checkbox.
    expect(save.mock.calls[0][0].assignedOnStructures).toEqual({});
    expect(save.mock.calls[0][0].assignedStructures).toEqual({});
    expect(save.mock.calls[0][0].activeStructures).toEqual(["custom-project"]);
  });

  it("keeps a declared property Capacities no longer lists, with a warning option", async () => {
    const h = makeHarness("ready");
    stubAdmissionDiscovery(h, [TITLE_PROPERTY, BOOLEAN_PROPERTY]);
    vi.spyOn(h.controller, "loadCapacitiesSettings").mockResolvedValue({
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
      activeStructures: ["custom-project"],
      nativeTaskStructures: ["RootTask", "Task"],
      activeStatuses: ["active"],
      assignedStructures: { "custom-project": "gone-prop" },
      assignedOnStructures: {},
      availableStructures: ["custom-project"],
      structureTitles: {},
      declarationDiagnostics: [],
    });
    h.store.dispatch({ type: "UI", patch: { capacitiesSettingsOpen: true } });
    const rendered = h.ui(<CapacitiesSettingsDrawer />);
    fireEvent.click(rendered.getByRole("button", { name: /repair tools/i }));
    fireEvent.click(rendered.getByRole("button", { name: /show technical details/i }));
    await waitFor(() => expect(rendered.getByText("How each item type gets in")).toBeTruthy());

    await waitFor(() =>
      expect(rendered.getByLabelText("Look up properties for custom-project")).toBeTruthy(),
    );
    fireEvent.click(rendered.getByLabelText("Look up properties for custom-project"));
    const picker = () =>
      rendered.getByLabelText("Assignment property for custom-project") as HTMLSelectElement;
    await waitFor(() => expect(picker()).toBeTruthy());

    // The saved id is never silently dropped: it stays selected as a warning
    // option the operator can replace or clear.
    expect(picker().value).toBe("gone-prop");
    expect(
      Array.from(picker().options).some((option) =>
        option.textContent?.includes("Missing in Capacities — pick another"),
      ),
    ).toBe(true);
    expect(rendered.getByText(/Saved property gone-prop is missing in Capacities/)).toBeTruthy();

    fireEvent.change(picker(), { target: { value: "assigned-prop-id" } });
    expect(picker().value).toBe("assigned-prop-id");
    expect(rendered.queryByText(/Saved property gone-prop is missing in Capacities/)).toBeNull();

    const save = vi.spyOn(h.controller, "saveCapacitiesSettings");
    fireEvent.click(rendered.getByRole("button", { name: "Save Capacities settings" }));
    await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
    expect(save.mock.calls[0][0].assignedStructures).toEqual({
      "custom-project": "assigned-prop-id",
    });
  });
});

describe("CapacitiesSettingsDrawer declaration advisories", () => {
  /** The drawer's own hub stops: the policy sections sit behind “Repair
      tools” > “Show technical details”. */
  function openPanel(h: ReturnType<typeof makeHarness>) {
    h.store.dispatch({ type: "UI", patch: { capacitiesSettingsOpen: true } });
    const rendered = h.ui(<CapacitiesSettingsDrawer />);
    fireEvent.click(rendered.getByRole("button", { name: /repair tools/i }));
    fireEvent.click(rendered.getByRole("button", { name: /show technical details/i }));
    return rendered;
  }

  function settingsWith(diagnostics: CapacitiesDeclarationDiagnostic[]) {
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
      nativeTaskStructures: [],
      activeStatuses: ["active"],
      assignedStructures: {},
      assignedOnStructures: {},
      availableStructures: [],
      structureTitles: {},
      declarationDiagnostics: diagnostics,
    };
  }

  /** One server-shaped diagnostic: the reason is the backend's own sentence. */
  function diagnostic(
    structureId: string,
    structureLabel: string,
    propertyId: string,
    declaration = "Assigned",
  ): CapacitiesDeclarationDiagnostic {
    return {
      structureId,
      structureLabel,
      reason: `${declaration} declaration names property '${propertyId}', which the last published Capacities contract does not define; pick a live property in Settings or clear the mapping.`,
    };
  }

  const NOTICE_NAME = /Saved mappings the last published Capacities contract does not define/i;

  it("lists each diagnostic with its cached title, falling back to the id", async () => {
    const h = makeHarness("ready");
    vi.spyOn(h.controller, "loadCapacitiesSettings").mockResolvedValue(
      settingsWith([
        diagnostic("custom-project", "Custom project", "gone"),
        // No observed title: the id is the label, never an invented name.
        diagnostic("0d194525-c5a1-4af5-bb62-202b83006b5e", "", "gone-on", "Assigned On"),
      ]),
    );
    const rendered = openPanel(h);

    const notice = await waitFor(() => rendered.getByRole("region", { name: NOTICE_NAME }));
    expect(within(notice).getByText("Custom project")).toBeTruthy();
    expect(within(notice).getByText("0d194525-c5a1-4af5-bb62-202b83006b5e")).toBeTruthy();
    expect(within(notice).getByText(/names property 'gone'/)).toBeTruthy();
    expect(within(notice).getByText(/names property 'gone-on'/)).toBeTruthy();
    expect(within(notice).getByText("2 mappings do nothing yet")).toBeTruthy();
    // Advisory only: the mapping controls and the Save control stay available.
    expect(rendered.getByRole("button", { name: "Save Capacities settings" })).toBeTruthy();
  });

  it("collapses past five advisories into one bounded tail", async () => {
    const h = makeHarness("ready");
    const diagnostics = Array.from({ length: 7 }, (_, index) =>
      diagnostic(`structure-${index}`, index === 6 ? "" : `Structure ${index}`, `prop-${index}`),
    );
    vi.spyOn(h.controller, "loadCapacitiesSettings").mockResolvedValue(
      settingsWith(diagnostics),
    );
    const rendered = openPanel(h);

    const notice = await waitFor(() => rendered.getByRole("region", { name: NOTICE_NAME }));
    expect(within(notice).getAllByRole("listitem").length).toBe(5);
    expect(within(notice).getByText("7 mappings do nothing yet")).toBeTruthy();
    expect(within(notice).getByText("and 2 more")).toBeTruthy();
    // The folded tail is not rendered at all, not merely hidden by CSS.
    expect(within(notice).queryByText("Structure 5")).toBeNull();
    expect(within(notice).queryByText("structure-6")).toBeNull();
  });

  it("renders nothing when no declaration drifts", async () => {
    const h = makeHarness("ready");
    vi.spyOn(h.controller, "loadCapacitiesSettings").mockResolvedValue(settingsWith([]));
    const rendered = openPanel(h);

    await waitFor(() => expect(rendered.getByText("How each item type gets in")).toBeTruthy());
    expect(rendered.queryByRole("region", { name: NOTICE_NAME })).toBeNull();
    expect(rendered.queryByText(/mappings do nothing yet/)).toBeNull();
  });

  it("shows the 422 dual-declaration message as the save error, not a generic failure", async () => {
    const h = makeHarness("ready");
    const message =
      "capacities:space-1:RootTask:task-1 declares both Assigned property 'assigned' and Assigned On property 'assigned-on'; clear one mapping.";
    vi.spyOn(h.controller, "loadCapacitiesSettings").mockResolvedValue(
      settingsWith([diagnostic("custom-project", "Custom project", "gone")]),
    );
    // The ApiError's own message is deliberately the generic fallback: only the
    // code-narrowed 422 path can put the server's sentence in the notice.
    vi.spyOn(h.controller, "saveCapacitiesSettings").mockRejectedValue(
      new ApiError(
        422,
        {
          code: "capacities_settings_declaration_conflict",
          message,
          conflicts: [
            {
              structure_id: "custom-project",
              assigned_property: "assigned",
              assigned_on_property: "assigned-on",
            },
          ],
        },
        "request failed (422)",
        "/settings/capacities/save",
      ),
    );
    const rendered = openPanel(h);

    await waitFor(() => expect(rendered.getByText("Custom project")).toBeTruthy());
    fireEvent.click(rendered.getByRole("button", { name: "Save Capacities settings" }));

    const alert = await waitFor(() => rendered.getByRole("alert"));
    expect(alert.textContent).toContain(message);
    // The generic failure wording never replaces the server's own sentence.
    expect(alert.textContent).not.toContain("request failed (422)");
    // Still the ordinary save-error affordance: reload stays reachable.
    expect(within(alert).getByRole("button", { name: /reload settings/i })).toBeTruthy();
  });
});
