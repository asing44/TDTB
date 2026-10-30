import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render } from "@testing-library/preact";
import {
  CapacitiesAdmissionRow,
  admissionModeOf,
  applyAdmissionMode,
  displayIdOf,
  propertiesForMode,
  type CapacitiesAdmissionMode,
} from "./CapacitiesAdmissionRow";
import type {
  CapacitiesCatalogProperty,
  CapacitiesSettingsDraft,
} from "../model/types";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function draftOf(overrides: Partial<CapacitiesSettingsDraft> = {}): CapacitiesSettingsDraft {
  return {
    expectedRevision: 0,
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
    ...overrides,
  };
}

const BOOLEAN: CapacitiesCatalogProperty = {
  propertyId: "checkbox-prop",
  title: "Assigned",
  type: "boolean",
  writable: true,
  labelOptions: [],
};
const DATE: CapacitiesCatalogProperty = {
  propertyId: "date-prop",
  title: "Assigned on",
  type: "date",
  writable: false,
  labelOptions: [],
};
const TITLE: CapacitiesCatalogProperty = {
  propertyId: "title-prop",
  title: "Name",
  type: "title",
  writable: true,
  labelOptions: [],
};

function renderRow(overrides: Partial<Parameters<typeof CapacitiesAdmissionRow>[0]> = {}) {
  const props = {
    structureId: "custom-project",
    title: null,
    mode: "status" as CapacitiesAdmissionMode,
    declaredPropertyId: "",
    properties: null,
    discovering: false,
    declaredButOff: false,
    onModeChange: vi.fn(),
    onPropertyChange: vi.fn(),
    onLookUp: vi.fn(),
    ...overrides,
  };
  const rendered = render(<CapacitiesAdmissionRow {...props} />);
  return { props, rendered };
}

describe("applyAdmissionMode", () => {
  it("writes exactly the draft keys each mode owns", () => {
    const declared = draftOf({
      activeStructures: ["custom-project"],
      assignedStructures: { "custom-project": "checkbox-prop" },
      assignedOnStructures: {},
    });

    // Off removes the structure AND both declarations.
    expect(applyAdmissionMode(declared, "custom-project", "off", "")).toEqual(
      draftOf({ activeStructures: [] }),
    );

    // Status only keeps the structure switched on and no declaration.
    expect(applyAdmissionMode(draftOf(), "custom-project", "status", "")).toEqual(
      draftOf({ activeStructures: ["custom-project"] }),
    );

    // Requires Assigned checkbox: switched on plus the checkbox map only.
    expect(applyAdmissionMode(draftOf(), "custom-project", "assigned", "checkbox-prop")).toEqual(
      draftOf({
        activeStructures: ["custom-project"],
        assignedStructures: { "custom-project": "checkbox-prop" },
      }),
    );

    // Requires Assigned date: switched on plus the date map only.
    expect(applyAdmissionMode(draftOf(), "custom-project", "assignedOn", "date-prop")).toEqual(
      draftOf({
        activeStructures: ["custom-project"],
        assignedOnStructures: { "custom-project": "date-prop" },
      }),
    );
  });

  it("clears a declaration when the picker is emptied, keeping the type admitted", () => {
    const declared = draftOf({
      activeStructures: ["custom-project"],
      assignedStructures: { "custom-project": "checkbox-prop" },
    });
    expect(applyAdmissionMode(declared, "custom-project", "assigned", "")).toEqual(
      draftOf({ activeStructures: ["custom-project"] }),
    );
  });

  it("derives the mode the saved draft already describes", () => {
    expect(admissionModeOf(draftOf(), "custom-project")).toBe("off");
    expect(admissionModeOf(draftOf({ activeStructures: ["custom-project"] }), "custom-project")).toBe(
      "status",
    );
    expect(
      admissionModeOf(
        draftOf({ assignedStructures: { "custom-project": "checkbox-prop" } }),
        "custom-project",
      ),
    ).toBe("assigned");
    expect(
      admissionModeOf(
        draftOf({ assignedOnStructures: { "custom-project": "date-prop" } }),
        "custom-project",
      ),
    ).toBe("assignedOn");
  });
});

describe("propertiesForMode", () => {
  it("offers only properties of the matching kind", () => {
    const properties = [TITLE, BOOLEAN, DATE];
    expect(propertiesForMode(properties, "assigned").map((p) => p.propertyId)).toEqual([
      "checkbox-prop",
    ]);
    expect(propertiesForMode(properties, "assignedOn").map((p) => p.propertyId)).toEqual([
      "date-prop",
    ]);
    expect(propertiesForMode(properties, "status")).toEqual([]);
    expect(propertiesForMode(null, "assigned")).toEqual([]);
  });
});

describe("displayIdOf", () => {
  it("shortens only long raw ids", () => {
    expect(displayIdOf("custom-project")).toBe("custom-project");
    expect(displayIdOf("0d194525-c5a1-4af5-bb62-202b83006b5e")).toBe("0d194525…");
  });
});

describe("CapacitiesAdmissionRow", () => {
  it("renders one mode select, the help line, and no picker for Off or Status only", () => {
    const { rendered } = renderRow({ mode: "status" });
    const select = rendered.getByLabelText("Admission for custom-project") as HTMLSelectElement;
    expect(select.value).toBe("status");
    expect(Array.from(select.options).map((option) => option.textContent)).toEqual([
      "Off",
      "Status only",
      "Requires Assigned checkbox",
      "Requires Assigned date",
    ]);
    // One control per type: no picker and no separate Active switch.
    expect(rendered.queryByLabelText("Assignment property for custom-project")).toBeNull();
    expect(rendered.queryByRole("checkbox")).toBeNull();
    expect(
      rendered.getByText("Switching a type to a Requires mode also turns it on."),
    ).toBeTruthy();
  });

  it("shows the declared id as plain text with a lookup button before any schema is read", () => {
    const { props, rendered } = renderRow({ mode: "assigned", declaredPropertyId: "checkbox-prop" });
    expect(rendered.queryByLabelText("Assignment property for custom-project")).toBeNull();
    expect(rendered.getByText("checkbox-prop")).toBeTruthy();
    fireEvent.click(rendered.getByLabelText("Look up properties for custom-project"));
    expect(props.onLookUp).toHaveBeenCalledTimes(1);
  });

  it("renders a missing declared property as a warning option, still selectable", () => {
    const { props, rendered } = renderRow({
      mode: "assignedOn",
      declaredPropertyId: "gone-prop",
      properties: [TITLE, BOOLEAN, DATE],
    });
    const picker = rendered.getByLabelText(
      "Assignment property for custom-project",
    ) as HTMLSelectElement;
    expect(picker.value).toBe("gone-prop");
    const missing = Array.from(picker.options).find((option) => option.value === "gone-prop");
    expect(missing?.textContent).toContain("Missing in Capacities — pick another");
    expect(rendered.getByText(/Saved property gone-prop is missing in Capacities/)).toBeTruthy();

    // Choosing the empty option is still possible, and clears the declaration.
    fireEvent.change(picker, { target: { value: "" } });
    expect(props.onPropertyChange).toHaveBeenCalledWith("");
  });

  it("warns when a saved declaration is not switched on", () => {
    const { rendered } = renderRow({
      mode: "assigned",
      declaredPropertyId: "checkbox-prop",
      declaredButOff: true,
    });
    expect(rendered.getByText(/has a saved declaration but is not switched on/)).toBeTruthy();
  });
});
