/* capacities-admission-row.test.tsx — ONE admission rule per Capacities type.

   Schema v2 collapsed the three overlapping v1 controls (native-task toggles,
   per-type Active/Assigned/Assigned On switches, a bare open-status list) into
   one row per type: a five-option rule selector plus the two controls a mode
   may need. These tests pin the row's own contract — the selector options, the
   property picker and its kind filter, the tri-state status control and its
   three wire states, the missing-property warnings, and the display identity —
   plus the pure helpers that turn a row gesture into exactly one declaration. */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render } from "@testing-library/preact";
import {
  ADMISSION_MODES,
  CapacitiesAdmissionRow,
  admissionModeOf,
  applyAdmissionMode,
  applyCustomStatusNames,
  applyStatusChoice,
  declarationOf,
  displayIdOf,
  isPropertyMode,
  propertiesForMode,
  statusChoiceOf,
  usesStatus,
  type CapacitiesAdmissionMode,
  type CapacitiesStatusChoice,
} from "./CapacitiesAdmissionRow";
import type {
  CapacitiesCatalogProperty,
  CapacitiesSettingsDraft,
} from "../model/types";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

/** A schema-v2 draft: the tri-state statuses and the shared vocabularies are
    present on every save, so the helper fixtures carry them too. */
function draftOf(overrides: Partial<CapacitiesSettingsDraft> = {}): CapacitiesSettingsDraft {
  return {
    expectedRevision: 0,
    statuses: { open: ["Active"], completed: ["Completed", "Dropped"] },
    types: {},
    native: { deadlineHorizonDays: 2 },
    excluded: [],
    ...overrides,
  };
}

function property(propertyId: string, type: string): CapacitiesCatalogProperty {
  return { propertyId, title: propertyId, type, writable: type === "boolean", labelOptions: [] };
}

const BOOLEAN = property("checkbox-prop", "boolean");
const CHECKBOX = property("checkbox-kind", "checkbox");
const DATE = property("date-prop", "date");
const DATETIME = property("datetime-prop", "datetime");
const TITLE = property("title-prop", "title");
const ALL_PROPERTIES = [TITLE, BOOLEAN, CHECKBOX, DATE, DATETIME];

function renderRow(overrides: Partial<Parameters<typeof CapacitiesAdmissionRow>[0]> = {}) {
  const props = {
    structureId: "custom-project",
    title: null,
    mode: "off" as CapacitiesAdmissionMode,
    propertyId: "",
    properties: null,
    discovering: false,
    statusChoice: "inherit" as CapacitiesStatusChoice,
    customStatuses: [] as string[],
    openStatuses: ["Active"],
    onModeChange: vi.fn(),
    onPropertyChange: vi.fn(),
    onStatusChoiceChange: vi.fn(),
    onCustomStatusesChange: vi.fn(),
    onLookUp: vi.fn(),
    ...overrides,
  };
  const rendered = render(<CapacitiesAdmissionRow {...props} />);
  return { props, rendered };
}

describe("applyAdmissionMode", () => {
  it("writes one declaration per structure and drops the property outside the property modes", () => {
    const declared = draftOf({
      types: {
        "custom-project": { mode: "assigned", property: "checkbox-prop", statuses: [] },
      },
    });

    // Off and the status/native modes declare no property, so the raw id is
    // never sent for them. The statuses tri-state is not a mode statement and
    // therefore survives the switch untouched.
    expect(applyAdmissionMode(declared, "custom-project", "off", "checkbox-prop")).toEqual(
      draftOf({
        types: { "custom-project": { mode: "off", property: null, statuses: [] } },
      }),
    );
    expect(applyAdmissionMode(declared, "custom-project", "active", "checkbox-prop")).toEqual(
      draftOf({
        types: { "custom-project": { mode: "active", property: null, statuses: [] } },
      }),
    );
    expect(applyAdmissionMode(declared, "custom-project", "native", "")).toEqual(
      draftOf({
        types: { "custom-project": { mode: "native", property: null, statuses: [] } },
      }),
    );
  });

  it("keeps the picked property for the two property modes and clears it when the picker empties", () => {
    expect(applyAdmissionMode(draftOf(), "custom-project", "assigned", "checkbox-prop")).toEqual(
      draftOf({
        types: { "custom-project": { mode: "assigned", property: "checkbox-prop", statuses: null } },
      }),
    );
    expect(applyAdmissionMode(draftOf(), "custom-project", "assignedOn", "date-prop")).toEqual(
      draftOf({
        types: { "custom-project": { mode: "assignedOn", property: "date-prop", statuses: null } },
      }),
    );
    // An empty pick is "No property": the declaration keeps its mode but
    // states no raw id, which is exactly what blocks the save.
    expect(applyAdmissionMode(draftOf(), "custom-project", "assigned", "")).toEqual(
      draftOf({
        types: { "custom-project": { mode: "assigned", property: null, statuses: null } },
      }),
    );
  });

  it("derives the mode and declaration the saved draft already describes", () => {
    expect(declarationOf(draftOf(), "custom-project")).toEqual({
      mode: "off",
      property: null,
      statuses: null,
    });
    expect(admissionModeOf(draftOf(), "custom-project")).toBe("off");
    for (const mode of ["off", "active", "assigned", "assignedOn", "native"] as const) {
      const declared = draftOf({
        types: { "custom-project": { mode, property: null, statuses: null } },
      });
      expect(admissionModeOf(declared, "custom-project")).toBe(mode);
    }
  });
});

describe("status tri-state", () => {
  it("maps the control's three states onto exactly null, [], and the names", () => {
    const inherits = draftOf({
      types: { "custom-project": { mode: "active", property: null, statuses: null } },
    });
    const off = draftOf({
      types: { "custom-project": { mode: "active", property: null, statuses: [] } },
    });

    expect(statusChoiceOf(declarationOf(inherits, "custom-project"))).toBe("inherit");
    expect(statusChoiceOf(declarationOf(off, "custom-project"))).toBe("none");
    // null and [] mean opposite things to the evaluator, so neither state may
    // be reported as the other.
    expect(statusChoiceOf({ mode: "active", property: null, statuses: ["In Progress"] })).toBe(
      "custom",
    );

    // Inherit clears back to null — not to the shared names, which would freeze
    // a copy of the vocabulary into this type.
    expect(applyStatusChoice(off, "custom-project", "inherit")).toEqual(inherits);
    expect(applyStatusChoice(inherits, "custom-project", "none")).toEqual(off);
    expect(
      applyStatusChoice(inherits, "custom-project", "custom", ["In Progress", "Active"]),
    ).toEqual(
      draftOf({
        types: {
          "custom-project": {
            mode: "active",
            property: null,
            statuses: ["In Progress", "Active"],
          },
        },
      }),
    );
  });

  it("edits the names without flipping an inherit or disabled declaration", () => {
    const names = draftOf({
      types: { "custom-project": { mode: "native", property: null, statuses: ["Blocked"] } },
    });
    expect(applyCustomStatusNames(names, "custom-project", ["Blocked", "Waiting"])).toEqual(
      draftOf({
        types: {
          "custom-project": { mode: "native", property: null, statuses: ["Blocked", "Waiting"] },
        },
      }),
    );
    // The copied list is independent of the caller's array.
    const given = ["Blocked"];
    const next = applyCustomStatusNames(names, "custom-project", given);
    given.push("Mutated");
    expect(next.types["custom-project"]?.statuses).toEqual(["Blocked"]);
  });
});

describe("propertiesForMode", () => {
  it("offers only properties of the matching kind, and nothing for a mode that reads none", () => {
    expect(propertiesForMode(ALL_PROPERTIES, "assigned").map((p) => p.propertyId)).toEqual([
      "checkbox-prop",
      "checkbox-kind",
    ]);
    expect(propertiesForMode(ALL_PROPERTIES, "assignedOn").map((p) => p.propertyId)).toEqual([
      "date-prop",
      "datetime-prop",
    ]);
    for (const mode of ["off", "active", "native"] as const) {
      expect(propertiesForMode(ALL_PROPERTIES, mode)).toEqual([]);
    }
    // No discovery yet is "nothing known", never "everything offered".
    expect(propertiesForMode(null, "assigned")).toEqual([]);
  });

  it("classifies the modes exactly once", () => {
    expect((["assigned", "assignedOn"] as const).every(isPropertyMode)).toBe(true);
    expect((["off", "active", "native"] as const).some(isPropertyMode)).toBe(false);
    expect((["active", "native"] as const).every(usesStatus)).toBe(true);
    expect((["off", "assigned", "assignedOn"] as const).some(usesStatus)).toBe(false);
  });
});

describe("displayIdOf", () => {
  it("shortens only long raw ids", () => {
    expect(displayIdOf("custom-project")).toBe("custom-project");
    expect(displayIdOf("0d194525-c5a1-4af5-bb62-202b83006b5e")).toBe("0d194525…");
  });
});

describe("CapacitiesAdmissionRow", () => {
  it("renders one five-option rule select and no mode-specific control for Off", () => {
    const { props, rendered } = renderRow({ mode: "off" });
    const select = rendered.getByLabelText("Admission for custom-project") as HTMLSelectElement;
    expect(select.value).toBe("off");
    expect(Array.from(select.options).map((option) => option.value)).toEqual(
      ADMISSION_MODES.map((option) => option.value),
    );
    expect(Array.from(select.options).map((option) => option.textContent)).toEqual([
      "Off",
      "Active by status",
      "Assigned checkbox",
      "Assigned On date",
      "Native task rules",
    ]);
    // One control per type: off declares nothing, so no picker and no status
    // control appear.
    expect(rendered.queryByLabelText("Assignment property for custom-project")).toBeNull();
    expect(rendered.queryByLabelText("Status names for custom-project")).toBeNull();
    expect(rendered.queryByRole("checkbox")).toBeNull();
    expect(
      rendered.getByText(
        "Active uses the status only; Native also uses the due date and the deadline.",
      ),
    ).toBeTruthy();

    fireEvent.change(select, { target: { value: "native" } });
    expect(props.onModeChange).toHaveBeenCalledWith("native");
  });

  it("shows the property picker only for the property modes and filters it by kind", () => {
    for (const mode of ["off", "active", "native"] as const) {
      const { rendered } = renderRow({ mode, properties: ALL_PROPERTIES });
      expect(rendered.queryByLabelText("Assignment property for custom-project")).toBeNull();
      cleanup();
    }

    const assigned = renderRow({ mode: "assigned", properties: ALL_PROPERTIES });
    expect(assigned.rendered.getByText("Assigned checkbox property")).toBeTruthy();
    const checkboxPicker = assigned.rendered.getByLabelText(
      "Assignment property for custom-project",
    ) as HTMLSelectElement;
    expect(Array.from(checkboxPicker.options).map((option) => option.value)).toEqual([
      "",
      "checkbox-prop",
      "checkbox-kind",
    ]);
    fireEvent.change(checkboxPicker, { target: { value: "checkbox-kind" } });
    expect(assigned.props.onPropertyChange).toHaveBeenCalledWith("checkbox-kind");
    cleanup();

    const on = renderRow({ mode: "assignedOn", properties: ALL_PROPERTIES });
    expect(on.rendered.getByText("Assigned On date property")).toBeTruthy();
    const datePicker = on.rendered.getByLabelText(
      "Assignment property for custom-project",
    ) as HTMLSelectElement;
    expect(Array.from(datePicker.options).map((option) => option.value)).toEqual([
      "",
      "date-prop",
      "datetime-prop",
    ]);
  });

  it("shows the declared id with a lookup button before any schema is read", () => {
    const { props, rendered } = renderRow({ mode: "assigned", propertyId: "checkbox-prop" });
    expect(rendered.queryByLabelText("Assignment property for custom-project")).toBeNull();
    expect(rendered.getByText("checkbox-prop")).toBeTruthy();
    fireEvent.click(rendered.getByLabelText("Look up properties for custom-project"));
    expect(props.onLookUp).toHaveBeenCalledTimes(1);
    // The declared id is never silently dropped before discovery answers.
    expect(rendered.queryByText("No property picked yet")).toBeNull();
  });

  it("says no property is picked yet and disables the lookup while it runs", () => {
    const { rendered } = renderRow({ mode: "assignedOn", discovering: true });
    expect(rendered.getByText("No property picked yet")).toBeTruthy();
    const lookup = rendered.getByLabelText(
      "Look up properties for custom-project",
    ) as HTMLButtonElement;
    // One discovery read at a time: the control reports it and is inert.
    expect(lookup.disabled).toBe(true);
    expect(lookup.textContent).toBe("Looking up…");
    cleanup();

    const idle = renderRow({ mode: "assignedOn" });
    const idleLookup = idle.rendered.getByLabelText(
      "Look up properties for custom-project",
    ) as HTMLButtonElement;
    expect(idleLookup.disabled).toBe(false);
    expect(idleLookup.textContent).toBe("Look up properties");
  });

  it("renders a missing declared property as a warning option, still selectable", () => {
    const { props, rendered } = renderRow({
      mode: "assignedOn",
      propertyId: "gone-prop",
      properties: [TITLE, DATE],
    });
    const picker = rendered.getByLabelText(
      "Assignment property for custom-project",
    ) as HTMLSelectElement;
    expect(picker.value).toBe("gone-prop");
    const missing = Array.from(picker.options).find((option) => option.value === "gone-prop");
    expect(missing?.textContent).toContain("Missing in Capacities — pick another");
    const warn = rendered.getByText(/Saved property gone-prop is missing in Capacities/);
    expect(warn.getAttribute("role")).toBe("status");

    // Choosing the empty option is still possible, and clears the declaration.
    fireEvent.change(picker, { target: { value: "" } });
    expect(props.onPropertyChange).toHaveBeenCalledWith("");
  });

  it("warns when discovery reports no property of the mode's kind", () => {
    const { rendered } = renderRow({ mode: "assigned", properties: [TITLE, DATE] });
    expect(
      rendered.getByText("Capacities reports no checkbox property for this type."),
    ).toBeTruthy();
    expect(rendered.queryByText(/Saved property .* is missing in Capacities/)).toBeNull();
    cleanup();

    const on = renderRow({ mode: "assignedOn", properties: [TITLE, BOOLEAN] });
    expect(on.rendered.getByText("Capacities reports no date property for this type.")).toBeTruthy();
  });

  it("asks for a property when a property mode has none picked", () => {
    const { rendered } = renderRow({ mode: "assigned", properties: [] });
    expect(
      rendered.getByText("Pick a property for this type, or choose another rule."),
    ).toBeTruthy();
    expect(
      (rendered.getByLabelText("Assignment property for custom-project") as HTMLSelectElement)
        .value,
    ).toBe("");
  });

  it("shows the tri-state status control only for the two status modes", () => {
    for (const mode of ["off", "assigned", "assignedOn"] as const) {
      const { rendered } = renderRow({ mode, properties: ALL_PROPERTIES });
      expect(rendered.queryByLabelText("Status names for custom-project")).toBeNull();
      cleanup();
    }

    for (const mode of ["active", "native"] as const) {
      const { props, rendered } = renderRow({ mode });
      const select = rendered.getByLabelText("Status names for custom-project") as HTMLSelectElement;
      expect(select.value).toBe("inherit");
      expect(Array.from(select.options).map((option) => option.value)).toEqual([
        "inherit",
        "none",
        "custom",
      ]);
      expect(Array.from(select.options).map((option) => option.textContent)).toEqual([
        "Inherit the shared list",
        "Don't use status (date or checkbox only)",
        "Custom names",
      ]);
      fireEvent.change(select, { target: { value: "none" } });
      expect(props.onStatusChoiceChange).toHaveBeenCalledWith("none");
      cleanup();
    }
  });

  it("explains what the status control currently means", () => {
    const inherit = renderRow({
      mode: "active",
      statusChoice: "inherit",
      openStatuses: ["Active", "In Progress"],
    });
    expect(
      inherit.rendered.getByText("Uses your shared open list: Active, In Progress."),
    ).toBeTruthy();
    cleanup();

    const empty = renderRow({ mode: "active", statusChoice: "inherit", openStatuses: [] });
    expect(
      empty.rendered.getByText("The shared open list is empty, so status admits nothing."),
    ).toBeTruthy();
    cleanup();

    const none = renderRow({ mode: "native", statusChoice: "none" });
    expect(
      none.rendered.getByText(
        "Status is ignored for this type; only its date or checkbox rule decides.",
      ),
    ).toBeTruthy();
  });

  it("edits custom status names as a sorted, duplicate-free list", () => {
    const { props, rendered } = renderRow({
      mode: "active",
      statusChoice: "custom",
      customStatuses: ["Active"],
    });
    expect(rendered.getByText("Active")).toBeTruthy();
    expect(
      (rendered.getByRole("button", { name: "Add name" }) as HTMLButtonElement).disabled,
    ).toBe(true);
    cleanup();

    // A custom list with no names admits nothing, and the row says so.
    const empty = renderRow({ mode: "active", statusChoice: "custom", customStatuses: [] });
    expect(
      empty.rendered.getByText("No names yet, so status admits nothing for this type."),
    ).toBeTruthy();
    cleanup();

    const editing = renderRow({
      mode: "active",
      statusChoice: "custom",
      customStatuses: ["Active"],
    });
    const input = editing.rendered.getByLabelText(
      "Add a status name for custom-project",
    ) as HTMLInputElement;
    fireEvent.input(input, { target: { value: "In Progress" } });
    fireEvent.click(editing.rendered.getByRole("button", { name: "Add name" }));
    expect(editing.props.onCustomStatusesChange).toHaveBeenCalledWith(["Active", "In Progress"]);
    // The draft field clears, so the next name starts empty.
    expect(input.value).toBe("");

    // A duplicate name is a no-op rather than a second entry.
    vi.mocked(editing.props.onCustomStatusesChange).mockClear();
    fireEvent.input(input, { target: { value: "Active" } });
    fireEvent.click(editing.rendered.getByRole("button", { name: "Add name" }));
    expect(editing.props.onCustomStatusesChange).not.toHaveBeenCalled();

    fireEvent.click(
      editing.rendered.getByRole("button", {
        name: "Remove status Active from custom-project",
      }),
    );
    expect(editing.props.onCustomStatusesChange).toHaveBeenCalledWith([]);
    expect(props.onCustomStatusesChange).not.toHaveBeenCalled();
  });

  it("shows the observed title with the shortened id, and the id alone when untitled", () => {
    const titled = renderRow({
      structureId: "0d194525-c5a1-4af5-bb62-202b83006b5e",
      title: "Project",
    });
    const strong = titled.rendered.getByText("Project");
    expect(strong.tagName.toLowerCase()).toBe("strong");
    const code = titled.rendered.getByText("0d194525…");
    expect(code.tagName.toLowerCase()).toBe("code");
    expect(strong.parentElement).toBe(code.parentElement);
    // The title is display-only: the control still names the structure.
    expect(titled.rendered.getByLabelText("Admission for Project")).toBeTruthy();
    cleanup();

    const untitled = renderRow({
      structureId: "0d194525-c5a1-4af5-bb62-202b83006b5e",
      title: null,
    });
    const onlyId = untitled.rendered.getByText("0d194525…");
    expect(onlyId.parentElement?.querySelector("strong")).toBeNull();
    expect(untitled.rendered.getByLabelText("Admission for 0d194525…")).toBeTruthy();
    cleanup();

    const short = renderRow({ structureId: "custom-project", title: null });
    const shortId = short.rendered.getByText("custom-project");
    expect(shortId.parentElement?.textContent).toBe("custom-project");
  });
});
