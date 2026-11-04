/* capacities-settings.test.tsx — the Capacities admission policy panel on
   settings schema v2.

   v1's three overlapping surfaces (automatic-task toggles, per-type
   Active/Assigned/Assigned On switches, and a bare open-status list) are gone:
   v2 states ONE rule per item type. These tests pin the replacement contract —
   the shared open/completed vocabularies, the native deadline horizon, one
   five-mode row per type, the property picker (kind-filtered, property modes
   only), the tri-state status control (status modes only), the migration
   advisory, the declaration-diagnostics advisory, the stale-declaration
   surface, and the 409/422 save failure paths — against the full-replacement
   POST body the route actually receives. */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, waitFor, within } from "@testing-library/preact";
import { CapacitiesSettingsDrawer } from "./CapacitiesSettingsDrawer";
import { makeHarness, type Harness, type HarnessRender } from "./test-harness";
import { ApiError } from "../adapters/api";
import { capacitiesSettingsToWire } from "../adapters/wire";
import type {
  AssignedItem,
  CapacitiesCatalogProperty,
  CapacitiesDeclarationDiagnostic,
  CapacitiesSettings,
  CapacitiesTypeDeclaration,
} from "../model/types";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const IDENTITY = "capacities:space-1:RootTask:task-1";
const LONG_ID = "0d194525-c5a1-4af5-bb62-202b83006b5e";

function declaration(overrides: Partial<CapacitiesTypeDeclaration> = {}): CapacitiesTypeDeclaration {
  return { mode: "off", property: null, statuses: null, ...overrides };
}

/** A schema-v2 settings document. Every field the save route replaces
    wholesale carries a non-default value so an omission fails loudly. */
function settingsFixture(overrides: Partial<CapacitiesSettings> = {}): CapacitiesSettings {
  return {
    version: 2,
    revision: 0,
    persisted: true,
    statuses: { open: ["Active"], completed: ["Completed", "Dropped"] },
    types: {},
    native: { deadlineHorizonDays: 2 },
    excluded: [],
    availableStructures: [],
    structureTitles: {},
    declarationDiagnostics: [],
    migratedFrom: null,
    ...overrides,
  };
}

/** One Capacities item in the plan inputs, so the exclusion list has a real
    identity to toggle. */
function stageCapacityItem(h: Harness) {
  const inputs = h.store.getState().inputs!;
  const item: AssignedItem = {
    ...inputs.assigned[0],
    id: "Capacity task",
    name: "Capacity task",
    source: "capacities",
    path: "capacities://space-1/task-1",
    identity: IDENTITY,
    types: ["RootTask"],
  };
  h.store.dispatch({
    type: "INPUTS_LOADED",
    inputs: {
      ...inputs,
      assigned: [...inputs.assigned.filter((r) => r.source !== "capacities"), item],
    },
    ledger: h.store.getState().ledger!,
  });
}

/** The drawer's real host path: the policy body sits behind the Repair fold
    and its second click, exactly as the operator reaches it. */
function mountDrawer(h: Harness): HarnessRender {
  h.store.dispatch({ type: "UI", patch: { settingsPanel: "repair" } });
  const rendered = h.ui(<CapacitiesSettingsDrawer />);
  fireEvent.click(rendered.getByRole("button", { name: /repair tools/i }));
  fireEvent.click(rendered.getByRole("button", { name: /show technical details/i }));
  return rendered;
}

function openPanel(overrides: Partial<CapacitiesSettings> = {}, withCapacity = false) {
  const h = makeHarness("ready");
  if (withCapacity) stageCapacityItem(h);
  const load = vi
    .spyOn(h.controller, "loadCapacitiesSettings")
    .mockResolvedValue(settingsFixture(overrides));
  const rendered = mountDrawer(h);
  return { h, rendered, load };
}

async function loaded(rendered: HarnessRender) {
  await waitFor(() =>
    expect(rendered.getByRole("heading", { name: "How each item type gets in" })).toBeTruthy(),
  );
  return rendered;
}

function admissionSelect(rendered: HarnessRender, label = "custom-project") {
  return rendered.getByLabelText(`Admission for ${label}`) as HTMLSelectElement;
}

function admissionRow(rendered: HarnessRender, label = "custom-project") {
  return rendered.getByLabelText(`Admission for ${label}`).closest(
    ".capacities-admission",
  ) as HTMLElement;
}

function vocabularyField(rendered: HarnessRender, dimension: "open" | "completed") {
  const heading = dimension === "open" ? "Open statuses" : "Completed statuses";
  return rendered.getByRole("heading", { name: heading }).closest(".field") as HTMLElement;
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

/** Provider discovery is the same read the source mapping editor uses: one
    local source read for the space id, then one discovery call. */
function stubDiscovery(h: Harness, properties: CapacitiesCatalogProperty[]) {
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

describe("CapacitiesSettingsPanel", () => {
  it("shows the loading state, then the policy with its revision, horizon, and known items", async () => {
    const h = makeHarness("ready");
    stageCapacityItem(h);
    let resolveSettings: (settings: CapacitiesSettings) => void = () => {};
    const pending = new Promise<CapacitiesSettings>((resolve) => {
      resolveSettings = resolve;
    });
    vi.spyOn(h.controller, "loadCapacitiesSettings").mockReturnValue(pending);
    const rendered = mountDrawer(h);

    const loading = rendered.getByText("Loading local policy…");
    expect(loading.getAttribute("role")).toBe("status");
    resolveSettings(
      settingsFixture({
        revision: 3,
        statuses: { open: ["Active", "In Progress"], completed: ["Dropped"] },
        native: { deadlineHorizonDays: 6 },
      }),
    );
    await loaded(rendered);

    expect(rendered.getByText("Saved (version 3)")).toBeTruthy();
    expect(rendered.getByText("Each type has one rule for getting into your plan.")).toBeTruthy();
    expect(
      (rendered.getByLabelText("Deadline coming up") as HTMLInputElement).value,
    ).toBe("6");
    const open = within(vocabularyField(rendered, "open"));
    expect(open.getByText("Active")).toBeTruthy();
    expect(open.getByText("In Progress")).toBeTruthy();
    expect(within(vocabularyField(rendered, "completed")).getByText("Dropped")).toBeTruthy();
    expect(rendered.getByText("Capacity task")).toBeTruthy();
    expect(rendered.getByText(IDENTITY)).toBeTruthy();
    expect(rendered.getByText(/not a full browser of Capacities/)).toBeTruthy();
  });

  it("explains the native rules from the section heading, by role and name", async () => {
    const { rendered } = openPanel({ availableStructures: ["custom-project"] });
    await loaded(rendered);

    const section = rendered
      .getByRole("heading", { name: "How each item type gets in" })
      .closest(".setup-section") as HTMLElement;
    const headingInfo = within(section).getByRole("button", {
      name: "What automatic task rules are",
    });
    // Reachable affordance, not a pointer-only native title=.
    expect(headingInfo.getAttribute("title")).toBeNull();
    expect(rendered.queryByRole("tooltip")).toBeNull();

    fireEvent.focus(headingInfo);
    const tooltip = rendered.getByRole("tooltip");
    expect(tooltip.textContent).toContain("Automatic task rules");
    expect(tooltip.textContent).toMatch(/built-in rules that decide which tasks come in/i);
    expect(tooltip.textContent).toMatch(/due today or earlier/i);
    expect(tooltip.textContent).toMatch(/deadline is overdue or coming up inside the deadline window/i);
    expect(tooltip.textContent).toMatch(/completed and dropped/i);
    expect(tooltip.textContent).toMatch(/follows only that one/i);
    expect(headingInfo.getAttribute("aria-describedby")).toBe(tooltip.getAttribute("id"));
    fireEvent.blur(headingInfo);
    expect(rendered.queryByRole("tooltip")).toBeNull();
    // The named modes are the section's own vocabulary, so the five-option
    // selector is the per-type affordance rather than a second toggle.
    expect(admissionSelect(rendered).options[4].textContent).toBe("Native task rules");
  });

  it("saves the full replacement draft with the loaded revision", async () => {
    const { h, rendered } = openPanel(
      {
        types: { "custom-project": declaration({ mode: "active" }) },
        availableStructures: ["custom-project"],
      },
      true,
    );
    await loaded(rendered);

    fireEvent.click(rendered.getByRole("checkbox", { name: /Available to TDTB/ }));
    fireEvent.click(rendered.getByRole("button", { name: "Remove open status Active" }));
    const open = within(vocabularyField(rendered, "open"));
    fireEvent.input(open.getByLabelText("Add an open status name"), {
      target: { value: "In Progress" },
    });
    fireEvent.click(open.getByRole("button", { name: "Add name" }));
    fireEvent.input(rendered.getByLabelText("Deadline coming up"), { target: { value: "5" } });

    const save = vi.spyOn(h.controller, "saveCapacitiesSettings");
    fireEvent.click(rendered.getByRole("button", { name: "Save Capacities settings" }));
    await waitFor(() => expect(save).toHaveBeenCalledTimes(1));

    const draft = save.mock.calls[0][0];
    expect(draft).toEqual({
      expectedRevision: 0,
      statuses: { open: ["In Progress"], completed: ["Completed", "Dropped"] },
      types: { "custom-project": { mode: "active", property: null, statuses: null } },
      native: { deadlineHorizonDays: 5 },
      excluded: [IDENTITY],
    });
    // The body the route receives, key for key.
    expect(capacitiesSettingsToWire(draft)).toEqual({
      expected_revision: 0,
      statuses: { open: ["In Progress"], completed: ["Completed", "Dropped"] },
      types: { "custom-project": { mode: "active", property: null, statuses: null } },
      native: { deadline_horizon_days: 5 },
      excluded: { [IDENTITY]: true },
    });
    await waitFor(() => expect(h.store.getState().ui.settingsPanel).toBeNull());
  });

  it("sends the vocabulary and every declaration untouched, keeping [] distinct from null", async () => {
    const { h, rendered } = openPanel({
      statuses: { open: ["Active", "In Progress"], completed: ["Dropped"] },
      types: {
        "custom-project": declaration({ mode: "native", statuses: [] }),
        Project: declaration({ mode: "active", statuses: ["Blocked"] }),
      },
      native: { deadlineHorizonDays: 4 },
      excluded: [IDENTITY],
      availableStructures: ["custom-project", "Project"],
    });
    await loaded(rendered);

    // Nothing in the policy was edited. Full replacement still demands every
    // key, or the server resets the vocabulary and switches every type off.
    const save = vi.spyOn(h.controller, "saveCapacitiesSettings");
    fireEvent.click(rendered.getByRole("button", { name: "Save Capacities settings" }));
    await waitFor(() => expect(save).toHaveBeenCalledTimes(1));

    const body = capacitiesSettingsToWire(save.mock.calls[0][0]);
    expect(body.statuses).toEqual({ open: ["Active", "In Progress"], completed: ["Dropped"] });
    expect(body.native).toEqual({ deadline_horizon_days: 4 });
    expect(body.excluded).toEqual({ [IDENTITY]: true });
    // `[]` (status admission off here) must not arrive as `null` (inherit).
    expect(body.types).toEqual({
      Project: { mode: "active", property: null, statuses: ["Blocked"] },
      "custom-project": { mode: "native", property: null, statuses: [] },
    });
  });

  it("adds and removes shared open and completed names in the outgoing body", async () => {
    const { h, rendered } = openPanel();
    await loaded(rendered);

    const open = within(vocabularyField(rendered, "open"));
    fireEvent.input(open.getByLabelText("Add an open status name"), {
      target: { value: "Waiting" },
    });
    fireEvent.click(open.getByRole("button", { name: "Add name" }));
    fireEvent.click(open.getByRole("button", { name: "Remove open status Active" }));

    const completed = within(vocabularyField(rendered, "completed"));
    fireEvent.input(completed.getByLabelText("Add an completed status name"), {
      target: { value: "Archived" },
    });
    fireEvent.click(completed.getByRole("button", { name: "Add name" }));
    fireEvent.click(completed.getByRole("button", { name: "Remove completed status Dropped" }));

    expect(open.queryByText("Active")).toBeNull();
    expect(open.getByText("Waiting")).toBeTruthy();
    expect(completed.queryByText("Dropped")).toBeNull();
    expect(completed.getByText("Archived")).toBeTruthy();

    const save = vi.spyOn(h.controller, "saveCapacitiesSettings");
    fireEvent.click(rendered.getByRole("button", { name: "Save Capacities settings" }));
    await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
    expect(save.mock.calls[0][0].statuses).toEqual({
      open: ["Waiting"],
      completed: ["Archived", "Completed"],
    });
  });

  it("shows the empty-vocabulary state instead of an empty list", async () => {
    const { rendered } = openPanel({ statuses: { open: [], completed: [] } });
    await loaded(rendered);
    expect(
      rendered.getByText(
        "No open names are configured, so inheriting types admit nothing by status.",
      ),
    ).toBeTruthy();
    expect(rendered.getByText("No completed names are configured.")).toBeTruthy();
  });

  it("sends an edited deadline horizon, and blocks a malformed one", async () => {
    const { h, rendered } = openPanel();
    await loaded(rendered);

    const horizon = rendered.getByLabelText("Deadline coming up") as HTMLInputElement;
    fireEvent.input(horizon, { target: { value: "abc" } });
    expect(rendered.getByText("Use a nonnegative whole number.")).toBeTruthy();
    const saveButton = rendered.getByRole("button", {
      name: "Save Capacities settings",
    }) as HTMLButtonElement;
    expect(saveButton.disabled).toBe(true);
    const save = vi.spyOn(h.controller, "saveCapacitiesSettings");
    fireEvent.click(saveButton);
    expect(save).not.toHaveBeenCalled();

    fireEvent.input(horizon, { target: { value: "0" } });
    expect(rendered.queryByText("Use a nonnegative whole number.")).toBeNull();
    expect(saveButton.disabled).toBe(false);
    fireEvent.click(saveButton);
    await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
    expect(save.mock.calls[0][0].native).toEqual({ deadlineHorizonDays: 0 });
    expect(capacitiesSettingsToWire(save.mock.calls[0][0]).native).toEqual({
      deadline_horizon_days: 0,
    });
  });

  it("offers the property picker only for the property modes, filtered by kind", async () => {
    const { h, rendered } = openPanel({ availableStructures: ["custom-project"] });
    const discovery = stubDiscovery(h, [TITLE_PROPERTY, BOOLEAN_PROPERTY, DATE_PROPERTY]);
    await loaded(rendered);

    // Off and the status modes declare no property, so no picker is offered.
    expect(rendered.queryByLabelText("Assignment property for custom-project")).toBeNull();
    fireEvent.change(admissionSelect(rendered), { target: { value: "active" } });
    expect(rendered.getByLabelText("Status names for custom-project")).toBeTruthy();
    expect(rendered.queryByLabelText("Assignment property for custom-project")).toBeNull();

    fireEvent.change(admissionSelect(rendered), { target: { value: "assigned" } });
    // No schema read yet: the row offers the lookup, not an empty picker.
    expect(rendered.queryByLabelText("Assignment property for custom-project")).toBeNull();
    expect(rendered.queryByLabelText("Status names for custom-project")).toBeNull();
    fireEvent.click(rendered.getByLabelText("Look up properties for custom-project"));
    const picker = await waitFor(
      () =>
        rendered.getByLabelText("Assignment property for custom-project") as HTMLSelectElement,
    );
    expect(discovery).toHaveBeenCalledTimes(1);
    // Only checkbox-kind properties are offered for the checkbox mode.
    expect(Array.from(picker.options).map((option) => option.value)).toEqual([
      "",
      "assigned-prop-id",
    ]);
    fireEvent.change(picker, { target: { value: "assigned-prop-id" } });

    const save = vi.spyOn(h.controller, "saveCapacitiesSettings");
    fireEvent.click(rendered.getByRole("button", { name: "Save Capacities settings" }));
    await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
    expect(capacitiesSettingsToWire(save.mock.calls[0][0]).types).toEqual({
      "custom-project": {
        mode: "assigned",
        property: "assigned-prop-id",
        statuses: null,
      },
    });
  });

  it("offers only date properties to the Assigned On mode", async () => {
    const { h, rendered } = openPanel({ availableStructures: ["custom-project"] });
    stubDiscovery(h, [TITLE_PROPERTY, BOOLEAN_PROPERTY, DATE_PROPERTY]);
    await loaded(rendered);

    fireEvent.change(admissionSelect(rendered), { target: { value: "assignedOn" } });
    // No schema is known yet, so the row names the missing pick, not a kind.
    expect(rendered.getByText("No property picked yet")).toBeTruthy();
    fireEvent.click(rendered.getByLabelText("Look up properties for custom-project"));
    await waitFor(() => expect(rendered.getByText("Assigned On date property")).toBeTruthy());
    const picker = (await waitFor(() =>
      rendered.getByLabelText("Assignment property for custom-project"),
    )) as HTMLSelectElement;
    expect(Array.from(picker.options).map((option) => option.value)).toEqual([
      "",
      "assigned-on-id",
    ]);
    fireEvent.change(picker, { target: { value: "assigned-on-id" } });

    const save = vi.spyOn(h.controller, "saveCapacitiesSettings");
    fireEvent.click(rendered.getByRole("button", { name: "Save Capacities settings" }));
    await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
    expect(save.mock.calls[0][0].types).toEqual({
      "custom-project": { mode: "assignedOn", property: "assigned-on-id", statuses: null },
    });
  });

  it("keeps a declared property that discovery no longer lists, with a warning option", async () => {
    const { h, rendered } = openPanel({
      types: { "custom-project": declaration({ mode: "assigned", property: "gone-prop" }) },
      availableStructures: ["custom-project"],
    });
    stubDiscovery(h, [TITLE_PROPERTY, BOOLEAN_PROPERTY]);
    await loaded(rendered);

    fireEvent.click(rendered.getByLabelText("Look up properties for custom-project"));
    const picker = (await waitFor(() =>
      rendered.getByLabelText("Assignment property for custom-project"),
    )) as HTMLSelectElement;
    // The saved id is never silently dropped: it stays selected as a warning
    // option the operator can replace or clear.
    expect(picker.value).toBe("gone-prop");
    expect(
      Array.from(picker.options).some((option) =>
        option.textContent?.includes("Missing in Capacities — pick another"),
      ),
    ).toBe(true);
    expect(rendered.getByText(/Saved property gone-prop is missing in Capacities/)).toBeTruthy();

    fireEvent.change(picker, { target: { value: "assigned-prop-id" } });
    expect(rendered.queryByText(/Saved property gone-prop is missing in Capacities/)).toBeNull();
    const save = vi.spyOn(h.controller, "saveCapacitiesSettings");
    fireEvent.click(rendered.getByRole("button", { name: "Save Capacities settings" }));
    await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
    expect(save.mock.calls[0][0].types).toEqual({
      "custom-project": { mode: "assigned", property: "assigned-prop-id", statuses: null },
    });
  });

  it("shows the status control only for the status modes", async () => {
    const { rendered } = openPanel({ availableStructures: ["custom-project"] });
    await loaded(rendered);
    expect(rendered.queryByLabelText("Status names for custom-project")).toBeNull();

    fireEvent.change(admissionSelect(rendered), { target: { value: "active" } });
    expect(rendered.getByLabelText("Status names for custom-project")).toBeTruthy();
    fireEvent.change(admissionSelect(rendered), { target: { value: "native" } });
    expect(rendered.getByLabelText("Status names for custom-project")).toBeTruthy();
    // A property mode never consults status, so an editor there would promise
    // something the evaluator ignores.
    fireEvent.change(admissionSelect(rendered), { target: { value: "assigned" } });
    expect(rendered.queryByLabelText("Status names for custom-project")).toBeNull();
    fireEvent.change(admissionSelect(rendered), { target: { value: "off" } });
    expect(rendered.queryByLabelText("Status names for custom-project")).toBeNull();
  });

  it("posts inherit and Don't use status as null and []", async () => {
    const inherit = openPanel({
      types: { "custom-project": declaration({ mode: "active" }) },
      availableStructures: ["custom-project"],
    });
    await loaded(inherit.rendered);
    expect(
      within(admissionRow(inherit.rendered)).getByText(
        "Uses your shared open list: Active.",
      ),
    ).toBeTruthy();
    const saveInherit = vi.spyOn(inherit.h.controller, "saveCapacitiesSettings");
    fireEvent.click(inherit.rendered.getByRole("button", { name: "Save Capacities settings" }));
    await waitFor(() => expect(saveInherit).toHaveBeenCalledTimes(1));
    expect(saveInherit.mock.calls[0][0].types["custom-project"].statuses).toBeNull();
    cleanup();

    const none = openPanel({
      types: { "custom-project": declaration({ mode: "active" }) },
      availableStructures: ["custom-project"],
    });
    await loaded(none.rendered);
    fireEvent.change(none.rendered.getByLabelText("Status names for custom-project"), {
      target: { value: "none" },
    });
    expect(
      within(admissionRow(none.rendered)).getByText(
        "Status is ignored for this type; only its date or checkbox rule decides.",
      ),
    ).toBeTruthy();
    const saveNone = vi.spyOn(none.h.controller, "saveCapacitiesSettings");
    fireEvent.click(none.rendered.getByRole("button", { name: "Save Capacities settings" }));
    await waitFor(() => expect(saveNone).toHaveBeenCalledTimes(1));
    expect(saveNone.mock.calls[0][0].types["custom-project"].statuses).toEqual([]);
    expect(capacitiesSettingsToWire(saveNone.mock.calls[0][0]).types["custom-project"].statuses).toEqual(
      [],
    );
  });

  it("seeds a custom status list from the shared names and posts the edited names", async () => {
    const { h, rendered } = openPanel({
      statuses: { open: ["Active", "In Progress"], completed: ["Dropped"] },
      types: { "custom-project": declaration({ mode: "active" }) },
      availableStructures: ["custom-project"],
    });
    await loaded(rendered);

    fireEvent.change(rendered.getByLabelText("Status names for custom-project"), {
      target: { value: "custom" },
    });
    const row = within(admissionRow(rendered));
    // Choosing Custom names for an inheriting declaration starts from the
    // shared list, so the operator edits names instead of an empty field.
    expect(row.getByText("Active")).toBeTruthy();
    expect(row.getByText("In Progress")).toBeTruthy();
    fireEvent.click(row.getByRole("button", { name: "Remove status Active from custom-project" }));
    fireEvent.input(row.getByLabelText("Add a status name for custom-project"), {
      target: { value: "Blocked" },
    });
    fireEvent.click(row.getByRole("button", { name: "Add name" }));
    expect(row.getByText("Blocked")).toBeTruthy();

    const save = vi.spyOn(h.controller, "saveCapacitiesSettings");
    fireEvent.click(rendered.getByRole("button", { name: "Save Capacities settings" }));
    await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
    expect(save.mock.calls[0][0].types["custom-project"]).toEqual({
      mode: "active",
      property: null,
      statuses: ["Blocked", "In Progress"],
    });
    expect(
      capacitiesSettingsToWire(save.mock.calls[0][0]).types["custom-project"].statuses,
    ).toEqual(["Blocked", "In Progress"]);
  });

  it("clears the property a mode no longer declares", async () => {
    const off = openPanel({
      types: { "custom-project": declaration({ mode: "assigned", property: "assigned-prop-id" }) },
      availableStructures: ["custom-project"],
    });
    await loaded(off.rendered);
    expect(off.rendered.getByText("assigned-prop-id")).toBeTruthy();
    fireEvent.change(admissionSelect(off.rendered), { target: { value: "off" } });
    const saveOff = vi.spyOn(off.h.controller, "saveCapacitiesSettings");
    fireEvent.click(off.rendered.getByRole("button", { name: "Save Capacities settings" }));
    await waitFor(() => expect(saveOff).toHaveBeenCalledTimes(1));
    expect(capacitiesSettingsToWire(saveOff.mock.calls[0][0]).types).toEqual({
      "custom-project": { mode: "off", property: null, statuses: null },
    });
    cleanup();

    const native = openPanel({
      types: { "custom-project": declaration({ mode: "assigned", property: "assigned-prop-id" }) },
      availableStructures: ["custom-project"],
    });
    await loaded(native.rendered);
    fireEvent.change(admissionSelect(native.rendered), { target: { value: "native" } });
    // The mode that reads no property drops the raw id entirely.
    expect(within(admissionRow(native.rendered)).queryByText("assigned-prop-id")).toBeNull();
    const saveNative = vi.spyOn(native.h.controller, "saveCapacitiesSettings");
    fireEvent.click(native.rendered.getByRole("button", { name: "Save Capacities settings" }));
    await waitFor(() => expect(saveNative).toHaveBeenCalledTimes(1));
    expect(saveNative.mock.calls[0][0].types["custom-project"]).toEqual({
      mode: "native",
      property: null,
      statuses: null,
    });
  });

  it("blocks the save while a property rule has no property, naming the type", async () => {
    const { h, rendered } = openPanel({
      types: { "custom-project": declaration({ mode: "assigned" }) },
      availableStructures: ["custom-project"],
    });
    await loaded(rendered);
    const alert = rendered.getByText(/Pick a property for custom-project/);
    expect(alert.closest('[role="alert"]')).toBeTruthy();
    const saveButton = rendered.getByRole("button", {
      name: "Save Capacities settings",
    }) as HTMLButtonElement;
    expect(saveButton.disabled).toBe(true);
    const save = vi.spyOn(h.controller, "saveCapacitiesSettings");
    fireEvent.click(saveButton);
    expect(save).not.toHaveBeenCalled();
  });

  it("keeps a declaration outside the source mapping and surfaces it", async () => {
    const { h, rendered } = openPanel({
      types: {
        "legacy-declared": declaration({ mode: "assigned", property: "legacy-prop" }),
        "custom-project": declaration({ mode: "assignedOn", property: "current-prop" }),
      },
      availableStructures: ["custom-project"],
      // A stale id never borrows a title the drawer does not consider available.
      structureTitles: { "legacy-declared": "Legacy Project" },
    });
    await loaded(rendered);

    expect(
      rendered.getByRole("heading", {
        name: "Types with a saved rule that are not set up to appear",
      }),
    ).toBeTruthy();
    expect(rendered.getByText(/kept as they are when you save/)).toBeTruthy();
    expect(rendered.getByText("legacy-declared")).toBeTruthy();
    expect(rendered.queryByText("Legacy Project")).toBeNull();

    const save = vi.spyOn(h.controller, "saveCapacitiesSettings");
    fireEvent.click(rendered.getByRole("button", { name: "Save Capacities settings" }));
    await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
    expect(save.mock.calls[0][0].types).toEqual({
      "custom-project": { mode: "assignedOn", property: "current-prop", statuses: null },
      "legacy-declared": { mode: "assigned", property: "legacy-prop", statuses: null },
    });
  });

  it("switches a saved rule off from the stale list", async () => {
    const { h, rendered } = openPanel({
      types: {
        "legacy-declared": declaration({ mode: "native" }),
        "custom-project": declaration({ mode: "active" }),
      },
      availableStructures: ["custom-project"],
    });
    await loaded(rendered);

    fireEvent.click(
      rendered.getByRole("button", { name: "Switch off the saved rule for legacy-declared" }),
    );
    expect(
      rendered.getByRole("button", { name: "Switch off the saved rule for legacy-declared" }),
    ).toBeTruthy();
    const save = vi.spyOn(h.controller, "saveCapacitiesSettings");
    fireEvent.click(rendered.getByRole("button", { name: "Save Capacities settings" }));
    await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
    expect(save.mock.calls[0][0].types["legacy-declared"]).toEqual({
      mode: "off",
      property: null,
      statuses: null,
    });
  });

  it("renders an explicit empty state when no structures are available", async () => {
    const { rendered } = openPanel({ availableStructures: [] });
    await loaded(rendered);
    expect(
      rendered.getByText(
        "Your Capacities setup lists no types yet; saved settings are kept when you save.",
      ),
    ).toBeTruthy();
    expect(rendered.queryByLabelText(/^Admission for /)).toBeNull();
  });

  it("shows observed titles beside raw ids, and the id alone when untitled", async () => {
    const { rendered } = openPanel({
      availableStructures: [LONG_ID, "custom-project"],
      structureTitles: { [LONG_ID]: "Project", "custom-project": "custom-project" },
    });
    await loaded(rendered);

    const group = within(
      rendered.getByRole("group", { name: "Capacities admission per item type" }),
    );
    const title = group.getByText("Project");
    expect(title.tagName.toLowerCase()).toBe("strong");
    // The admission row shortens a raw provider UUID; the id stays visible.
    const titledId = group.getByText("0d194525…");
    expect(titledId.tagName.toLowerCase()).toBe("code");
    expect(title.parentElement).toBe(titledId.parentElement);
    // A title that only repeats the id adds nothing, so the id appears once.
    const untitled = group.getByText("custom-project");
    expect(untitled.parentElement?.querySelector("strong")).toBeNull();
    expect(untitled.parentElement?.textContent).toBe("custom-project");
    // The title is display-only: admission still keys on the raw structure id.
    expect(rendered.getByLabelText("Admission for Project")).toBeTruthy();
    cleanup();

    const repeated = openPanel({
      availableStructures: ["root-task"],
      structureTitles: { "root-task": "root-task" },
    });
    await loaded(repeated.rendered);
    const repeatedGroup = within(
      repeated.rendered.getByRole("group", { name: "Capacities admission per item type" }),
    );
    expect(repeatedGroup.getAllByText("root-task").length).toBe(1);
    expect(repeatedGroup.getByText("root-task").parentElement?.querySelector("strong")).toBeNull();
  });

  it("lists saved exclusions outside the snapshot and lets them go", async () => {
    const { h, rendered } = openPanel({ excluded: [IDENTITY] });
    await loaded(rendered);

    expect(rendered.getByRole("heading", { name: "Saved exclusions outside this snapshot" })).toBeTruthy();
    expect(rendered.getByText(IDENTITY)).toBeTruthy();
    fireEvent.click(rendered.getByRole("button", { name: "Remove" }));

    const save = vi.spyOn(h.controller, "saveCapacitiesSettings");
    fireEvent.click(rendered.getByRole("button", { name: "Save Capacities settings" }));
    await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
    expect(save.mock.calls[0][0].excluded).toEqual([]);
  });

  it("shows the adapter's ignored-declaration notes verbatim beside the control", async () => {
    const h = makeHarness("ready");
    const inputs = h.store.getState().inputs!;
    const warning =
      "ignored Capacities assignment declaration for structure 'custom-project': " +
      "unknown property 'assigned-prop'";
    const assignedOnNote =
      "ignored Capacities Assigned On declaration for structure 'custom-project': not a date";
    const coverage =
      "Capacities partial — 20 evaluated · 51 deferred across contributing structures.";
    h.store.dispatch({
      type: "INPUTS_LOADED",
      inputs: { ...inputs, sourceWarnings: [warning, assignedOnNote, coverage] },
      ledger: h.store.getState().ledger!,
    });
    vi.spyOn(h.controller, "loadCapacitiesSettings").mockResolvedValue(
      settingsFixture({
        types: { "custom-project": declaration({ mode: "assigned", property: "assigned-prop" }) },
        availableStructures: ["custom-project"],
      }),
    );
    const rendered = mountDrawer(h);
    await loaded(rendered);

    expect(rendered.getByText("Settings we skipped")).toBeTruthy();
    // The server's own wording, passed through unchanged — never paraphrased.
    expect(rendered.getByText(warning)).toBeTruthy();
    expect(
      rendered.getByRole("heading", { name: "Notes about the “Assigned to me” date" }),
    ).toBeTruthy();
    expect(rendered.getByText(assignedOnNote)).toBeTruthy();
    // The partial-coverage warning belongs to the readiness rail, not here.
    expect(rendered.queryByText(coverage)).toBeNull();
    // The declaration stays editable so the operator can correct it.
    expect(within(admissionRow(rendered)).getByText("assigned-prop")).toBeTruthy();
  });

  it("shows load failures and offers an explicit reload", async () => {
    const h = makeHarness("ready");
    const load = vi
      .spyOn(h.controller, "loadCapacitiesSettings")
      .mockRejectedValueOnce(new Error("local settings unavailable"));
    const rendered = mountDrawer(h);

    await waitFor(() => expect(rendered.getByRole("alert")).toBeTruthy());
    expect(rendered.getByRole("alert").textContent).toContain("local settings unavailable");
    const reload = rendered.getByRole("button", { name: "Reload settings" });
    load.mockResolvedValueOnce(settingsFixture());
    fireEvent.click(reload);
    await loaded(rendered);
  });

  it("keeps the drawer open and surfaces a revision conflict", async () => {
    const { h, rendered } = openPanel();
    await loaded(rendered);
    const message =
      "Capacities settings changed since they were read; reload and retry.";
    vi.spyOn(h.controller, "saveCapacitiesSettings").mockRejectedValue(
      new ApiError(
        409,
        { code: "capacities_settings_conflict", message },
        message,
        "/settings/capacities/save",
      ),
    );

    fireEvent.click(rendered.getByRole("button", { name: "Save Capacities settings" }));
    const alert = await waitFor(() => rendered.getByRole("alert"));
    expect(alert.textContent).toContain("reload and retry");
    expect(within(alert).getByRole("button", { name: "Reload settings" })).toBeTruthy();
    // The drawer never closes on a conflict: reloading is an explicit choice.
    expect(h.store.getState().ui.settingsPanel).toBe("repair");
  });

  it("shows the 422 message as the save error, not a generic failure", async () => {
    const { h, rendered } = openPanel();
    await loaded(rendered);
    const message =
      "capacities:space-1:RootTask:task-1 declares both Assigned property 'assigned' " +
      "and Assigned On property 'assigned-on'; clear one mapping.";
    // Shaped exactly as the adapter raises it: the ApiError's message is the
    // server's own detail sentence, and the code body rides along untouched.
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
        message,
        "/settings/capacities/save",
      ),
    );

    fireEvent.click(rendered.getByRole("button", { name: "Save Capacities settings" }));
    const alert = await waitFor(() => rendered.getByRole("alert"));
    expect(alert.textContent).toContain(message);
    expect(alert.textContent).not.toContain("request failed (422)");
    expect(within(alert).getByRole("button", { name: /reload settings/i })).toBeTruthy();
    expect(h.store.getState().ui.settingsPanel).toBe("repair");
  });

  it("shows the migration advisory only when the stored file is still v1", async () => {
    const migrated = openPanel({ migratedFrom: 1 });
    await loaded(migrated.rendered);
    expect(
      migrated.rendered.getByText(
        "Your saved settings use the older format; saving rewrites them and keeps a backup.",
      ),
    ).toBeTruthy();
    cleanup();

    const current = openPanel({ migratedFrom: null });
    await loaded(current.rendered);
    expect(
      current.rendered.queryByText(
        "Your saved settings use the older format; saving rewrites them and keeps a backup.",
      ),
    ).toBeNull();
  });
});

describe("CapacitiesSettingsPanel declaration advisories", () => {
  const NOTICE_NAME =
    "Saved mappings the last published Capacities contract does not define";

  /** One server-shaped diagnostic: the reason is the backend's own sentence. */
  function diagnostic(
    structureId: string,
    structureLabel: string,
    propertyId: string,
    kind = "Assigned",
  ): CapacitiesDeclarationDiagnostic {
    return {
      structureId,
      structureLabel,
      reason:
        `${kind} declaration names property '${propertyId}', which the last published ` +
        "Capacities contract does not define; pick a live property in Settings or clear the mapping.",
    };
  }

  it("lists each diagnostic with its cached title, falling back to the id", async () => {
    const { rendered } = openPanel({
      declarationDiagnostics: [
        diagnostic("custom-project", "Custom project", "gone"),
        // No observed title: the id is the label, never an invented name.
        diagnostic(LONG_ID, "", "gone-on", "Assigned On"),
      ],
    });
    await loaded(rendered);

    const notice = rendered.getByRole("region", { name: NOTICE_NAME });
    expect(within(notice).getByText("Custom project")).toBeTruthy();
    expect(within(notice).getByText(LONG_ID)).toBeTruthy();
    expect(within(notice).getByText(/names property 'gone'/)).toBeTruthy();
    expect(within(notice).getByText(/names property 'gone-on'/)).toBeTruthy();
    expect(within(notice).getByText("2 mappings do nothing yet")).toBeTruthy();
    // Advisory only: the rule controls and the Save control stay available.
    expect(rendered.getByRole("button", { name: "Save Capacities settings" })).toBeTruthy();
  });

  it("collapses past five advisories into one bounded tail", async () => {
    const diagnostics = Array.from({ length: 7 }, (_, index) =>
      diagnostic(`structure-${index}`, index === 6 ? "" : `Structure ${index}`, `prop-${index}`),
    );
    const { rendered } = openPanel({ declarationDiagnostics: diagnostics });
    await loaded(rendered);

    const notice = rendered.getByRole("region", { name: NOTICE_NAME });
    expect(within(notice).getAllByRole("listitem").length).toBe(5);
    expect(within(notice).getByText("7 mappings do nothing yet")).toBeTruthy();
    expect(within(notice).getByText("and 2 more")).toBeTruthy();
    // The folded tail is not rendered at all, not merely hidden by CSS.
    expect(within(notice).queryByText("Structure 5")).toBeNull();
    expect(within(notice).queryByText("structure-6")).toBeNull();
  });

  it("renders nothing when no declaration drifts", async () => {
    const { rendered } = openPanel({ declarationDiagnostics: [] });
    await loaded(rendered);
    expect(rendered.queryByRole("region", { name: NOTICE_NAME })).toBeNull();
    expect(rendered.queryByText(/mappings do nothing yet/)).toBeNull();
  });
});
