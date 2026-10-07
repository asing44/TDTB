/* capacities-source-editor.test.tsx — the Capacities source mapping editor.

   Covers the read/empty state, the full-replacement save body, the real-409
   conflict contract (retained draft, both revisions, explicit reload only),
   ordinary failures (a 500 and the route's OTHER 409), order-preserving
   value lists, explicit-only structure removal, display titles, and the
   discovery step: catalog rendering, id-titled structures, add-to-mapping
   with every role unconfigured, failure classes, and draft/save isolation. */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, waitFor, within } from "@testing-library/preact";
import { CapacitiesSettingsDrawer } from "./CapacitiesSettingsDrawer";
import { makeHarness } from "./test-harness";
import { ApiError } from "../adapters/api";
import { capacitiesSourceToWire } from "../adapters/wire";
import type {
  CapacitiesCatalog,
  CapacitiesSettings,
  CapacitiesSource,
  CapacitiesSourceRead,
  CapacitiesSourceStructure,
} from "../model/types";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

// The operator's real Project structure carries this raw property id.
const PROJECT_DURATION = "f779f78a-d434-4099-9448-a90785bc8ae5";

function settingsFixture(
  overrides: Partial<CapacitiesSettings> = {},
): CapacitiesSettings {
  return {
    version: 1,
    revision: 0,
    persisted: true,
    nativeTaskAuto: {
      activeEnabled: false,
      dueEnabled: false,
      deadlineEnabled: false,
      deadlineHorizonDays: 0,
    },
    excluded: [],
    activeStructures: [],
    nativeTaskStructures: [],
    activeStatuses: [],
    assignedStructures: {},
    availableStructures: [],
    structureTitles: {},
    ...overrides,
  };
}

/** Every field carries a distinct value so a save that dropped or defaulted
    any field fails the deep-equality assertions. */
const recordFixture: CapacitiesSource = {
  version: 1,
  revision: 4,
  spaceId: "space-1",
  structures: [
    {
      structureId: "Project",
      titleProperty: "title",
      statusProperty: "status",
      openStatusValues: ["In Progress", "On Hold", "active"],
      dateProperty: "date",
      deadlineProperty: "deadline",
      durationProperty: "duration",
      assignmentProperty: "assigned",
      assignmentValues: ["Team A", "Team B"],
      completionProperty: "completed",
      completionValue: "done",
    },
    {
      structureId: "Task",
      titleProperty: "name",
      statusProperty: null,
      openStatusValues: [],
      dateProperty: null,
      deadlineProperty: "due",
      durationProperty: null,
      assignmentProperty: null,
      assignmentValues: [],
      completionProperty: null,
      completionValue: null,
    },
  ],
};

function savedRecord(
  revision: number,
  structures: CapacitiesSource["structures"],
): CapacitiesSource {
  return { version: 1, revision, spaceId: "space-1", structures };
}

async function openEditor(
  read: CapacitiesSourceRead,
  overrides: Partial<CapacitiesSettings> = {},
) {
  const h = makeHarness("ready");
  vi.spyOn(h.controller, "loadCapacitiesSettings").mockResolvedValue(
    settingsFixture(overrides),
  );
  const loadSource = vi
    .spyOn(h.controller, "loadCapacitiesSource")
    .mockResolvedValue(read);
  h.store.dispatch({ type: "UI", patch: { capacitiesSettingsOpen: true } });
  const rendered = h.ui(<CapacitiesSettingsDrawer />);
  await waitFor(() =>
    expect(
      rendered.getByRole("heading", { name: "Capacities source mapping" }),
    ).toBeTruthy(),
  );
  return { h, rendered, loadSource };
}

describe("CapacitiesSourceEditor", () => {
  it("renders an empty state for an absent mapping instead of an error", async () => {
    const { rendered } = await openEditor({ source: null, persisted: false });

    await waitFor(() =>
      expect(
        rendered.getByText(/No Capacities source mapping has been saved/),
      ).toBeTruthy(),
    );
    // An absent record is a first visit, not a failure.
    expect(rendered.queryByRole("alert")).toBeNull();
    expect(rendered.getByLabelText("Capacities space id")).toBeTruthy();
    expect(rendered.getByRole("button", { name: "Add structure" })).toBeTruthy();
    // Nothing can be saved until a space and a structure exist.
    expect(
      (
        rendered.getByRole("button", {
          name: "Save mapping",
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(true);
  });

  it("starts a first mapping from the empty state and saves it with revision 0", async () => {
    const { h, rendered } = await openEditor({ source: null, persisted: false });
    await waitFor(() =>
      expect(
        rendered.getByText(/No Capacities source mapping has been saved/),
      ).toBeTruthy(),
    );

    fireEvent.input(rendered.getByLabelText("Capacities space id"), {
      target: { value: "space-9" },
    });
    fireEvent.click(rendered.getByRole("button", { name: "Add structure" }));
    fireEvent.input(rendered.getByLabelText("Structure ID for structure 1"), {
      target: { value: "Project" },
    });
    fireEvent.input(rendered.getByLabelText("Title property for Project"), {
      target: { value: "title" },
    });

    const save = vi.spyOn(h.controller, "saveCapacitiesSource").mockResolvedValue({
      source: savedRecord(1, [
        {
          structureId: "Project",
          titleProperty: "title",
          statusProperty: null,
          openStatusValues: [],
          dateProperty: null,
          deadlineProperty: null,
          durationProperty: null,
          assignmentProperty: null,
          assignmentValues: [],
          completionProperty: null,
          completionValue: null,
        },
      ]),
      persisted: true,
    });
    const button = rendered.getByRole("button", {
      name: "Save mapping",
    }) as HTMLButtonElement;
    expect(button.disabled).toBe(false);
    fireEvent.click(button);
    await waitFor(() => expect(save).toHaveBeenCalledTimes(1));

    expect(save.mock.calls[0][0]).toEqual({
      expectedRevision: 0,
      spaceId: "space-9",
      structures: [
        {
          structureId: "Project",
          titleProperty: "title",
          statusProperty: null,
          openStatusValues: [],
          dateProperty: null,
          deadlineProperty: null,
          durationProperty: null,
          assignmentProperty: null,
          assignmentValues: [],
          completionProperty: null,
          completionValue: null,
        },
      ],
    });
    await waitFor(() => expect(rendered.getByText("Saved revision 1")).toBeTruthy());
  });

  it("sends every field of every structure plus the loaded revision on save", async () => {
    const { h, rendered } = await openEditor({
      source: recordFixture,
      persisted: true,
    });
    await waitFor(() =>
      expect(rendered.getByLabelText("Duration property for Project")).toBeTruthy(),
    );
    expect(
      (rendered.getByLabelText("Capacities space id") as HTMLInputElement).value,
    ).toBe("space-1");

    fireEvent.input(rendered.getByLabelText("Duration property for Project"), {
      target: { value: PROJECT_DURATION },
    });

    const save = vi.spyOn(h.controller, "saveCapacitiesSource").mockResolvedValue({
      source: savedRecord(5, [
        { ...recordFixture.structures[0], durationProperty: PROJECT_DURATION },
        { ...recordFixture.structures[1] },
      ]),
      persisted: true,
    });
    fireEvent.click(rendered.getByRole("button", { name: "Save mapping" }));
    await waitFor(() => expect(save).toHaveBeenCalledTimes(1));

    const draft = save.mock.calls[0][0];
    expect(draft).toEqual({
      expectedRevision: 4,
      spaceId: "space-1",
      structures: [
        { ...recordFixture.structures[0], durationProperty: PROJECT_DURATION },
        { ...recordFixture.structures[1] },
      ],
    });
    // The outgoing wire body is a full replacement too: both structure rows
    // field-for-field, snake_case, plus the loaded expected_revision.
    expect(capacitiesSourceToWire(draft)).toEqual({
      expected_revision: 4,
      space_id: "space-1",
      structures: [
        {
          structure_id: "Project",
          title_property: "title",
          status_property: "status",
          open_status_values: ["In Progress", "On Hold", "active"],
          date_property: "date",
          deadline_property: "deadline",
          duration_property: PROJECT_DURATION,
          assignment_property: "assigned",
          assignment_values: ["Team A", "Team B"],
          completion_property: "completed",
          completion_value: "done",
        },
        {
          structure_id: "Task",
          title_property: "name",
          status_property: null,
          open_status_values: [],
          date_property: null,
          deadline_property: "due",
          duration_property: null,
          assignment_property: null,
          assignment_values: [],
          completion_property: null,
          completion_value: null,
        },
      ],
    });
  });

  it("keeps the draft on a real 409, reports both revisions, and reloads only when asked", async () => {
    const { h, rendered, loadSource } = await openEditor({
      source: recordFixture,
      persisted: true,
    });
    await waitFor(() =>
      expect(rendered.getByLabelText("Duration property for Project")).toBeTruthy(),
    );
    fireEvent.input(rendered.getByLabelText("Duration property for Project"), {
      target: { value: PROJECT_DURATION },
    });

    const save = vi.spyOn(h.controller, "saveCapacitiesSource").mockRejectedValue(
      new ApiError(
        409,
        {
          code: "capacities_source_conflict",
          message: "Mapping changed; reload and review.",
          expected_revision: 4,
          current_revision: 7,
        },
        "Mapping changed; reload and review.",
      ),
    );
    fireEvent.click(rendered.getByRole("button", { name: "Save mapping" }));

    const conflict = await waitFor(() =>
      rendered.getByText(/changed while you were editing/),
    );
    expect(conflict.textContent).toContain("loaded revision 4");
    expect(conflict.textContent).toContain("now at revision 7");
    // The failed save was not retried, and the draft is still the operator's.
    expect(save).toHaveBeenCalledTimes(1);
    expect(
      (rendered.getByLabelText("Duration property for Project") as HTMLInputElement)
        .value,
    ).toBe(PROJECT_DURATION);

    // The explicit reload re-reads the record. The server's version dropped
    // the Task row; the retained draft must still carry it.
    loadSource.mockResolvedValue({
      source: savedRecord(7, [{ ...recordFixture.structures[0] }]),
      persisted: true,
    });
    fireEvent.click(rendered.getByRole("button", { name: "Reload and review" }));
    await waitFor(() => expect(loadSource).toHaveBeenCalledTimes(2));
    const review = await waitFor(() => rendered.getByText(/Re-read the record/));
    expect(review.textContent).toContain("revision 7 with 1 structure (Project)");
    expect(rendered.queryByRole("button", { name: "Reload and review" })).toBeNull();
    expect(rendered.getByText("Saved revision 7")).toBeTruthy();
    // Nothing was dropped by the reload: both draft rows and the edit remain.
    expect(rendered.getByLabelText("Structure ID for Project")).toBeTruthy();
    expect(rendered.getByLabelText("Structure ID for Task")).toBeTruthy();
    expect(
      (rendered.getByLabelText("Duration property for Project") as HTMLInputElement)
        .value,
    ).toBe(PROJECT_DURATION);

    // A later save is a deliberate operator action against the re-read
    // revision, and still carries Task — the row the reloaded server record
    // omitted.
    save.mockResolvedValue({
      source: savedRecord(8, [
        { ...recordFixture.structures[0], durationProperty: PROJECT_DURATION },
        { ...recordFixture.structures[1] },
      ]),
      persisted: true,
    });
    fireEvent.click(rendered.getByRole("button", { name: "Save mapping" }));
    await waitFor(() => expect(save).toHaveBeenCalledTimes(2));
    const resaved = save.mock.calls[1][0];
    expect(resaved.expectedRevision).toBe(7);
    expect(resaved.structures.map((row) => row.structureId)).toEqual([
      "Project",
      "Task",
    ]);
    expect(resaved.structures[0].durationProperty).toBe(PROJECT_DURATION);
  });

  it("shows an ordinary failure for a 500 and for the route's non-conflict 409", async () => {
    const { h, rendered } = await openEditor({
      source: recordFixture,
      persisted: true,
    });
    await waitFor(() =>
      expect(rendered.getByLabelText("Duration property for Project")).toBeTruthy(),
    );
    fireEvent.input(rendered.getByLabelText("Duration property for Project"), {
      target: { value: PROJECT_DURATION },
    });

    const save = vi.spyOn(h.controller, "saveCapacitiesSource").mockRejectedValueOnce(
      new ApiError(
        500,
        {
          code: "capacities_source_storage_error",
          message:
            "Capacities source mapping could not be saved; the existing file was preserved.",
        },
        "Capacities source mapping could not be saved; the existing file was preserved.",
      ),
    );
    fireEvent.click(rendered.getByRole("button", { name: "Save mapping" }));
    await waitFor(() =>
      expect(
        rendered.getByRole("alert").textContent,
      ).toContain("the existing file was preserved"),
    );
    // A plain failure is never presented as a conflict.
    expect(rendered.queryByRole("button", { name: "Reload and review" })).toBeNull();
    expect(rendered.queryByText(/changed while you were editing/)).toBeNull();
    expect(save).toHaveBeenCalledTimes(1);
    // A failed save drops neither a structure nor the edit.
    expect(rendered.getByLabelText("Structure ID for Task")).toBeTruthy();
    expect(
      (rendered.getByLabelText("Duration property for Project") as HTMLInputElement)
        .value,
    ).toBe(PROJECT_DURATION);

    // The route's OTHER 409 (malformed storage) is a 409 but not a conflict.
    save.mockRejectedValueOnce(
      new ApiError(
        409,
        {
          code: "capacities_source_storage_error",
          message:
            "Capacities source mapping storage is malformed or unsupported; the existing file was preserved.",
        },
        "Capacities source mapping storage is malformed or unsupported; the existing file was preserved.",
      ),
    );
    fireEvent.click(rendered.getByRole("button", { name: "Save mapping" }));
    await waitFor(() =>
      expect(rendered.getByRole("alert").textContent).toContain(
        "storage is malformed or unsupported",
      ),
    );
    expect(rendered.queryByRole("button", { name: "Reload and review" })).toBeNull();
    expect(save).toHaveBeenCalledTimes(2);
  });

  it("keeps both value lists in order through an edit-and-save round trip", async () => {
    const { h, rendered } = await openEditor({
      source: recordFixture,
      persisted: true,
    });
    await waitFor(() =>
      expect(rendered.getByLabelText("Open status values for Project")).toBeTruthy(),
    );

    const openStatuses = rendered.getByLabelText(
      "Open status values for Project",
    ) as HTMLTextAreaElement;
    const assignmentValues = rendered.getByLabelText(
      "Assignment values for Project",
    ) as HTMLTextAreaElement;
    expect(openStatuses.value).toBe("In Progress\nOn Hold\nactive");
    expect(assignmentValues.value).toBe("Team A\nTeam B");

    // Deliberately non-sorted, mixed-case order: sorting or case-folding
    // would change both lists.
    fireEvent.input(openStatuses, { target: { value: "zeta\nAlpha\nmm" } });
    fireEvent.input(assignmentValues, {
      target: { value: "Second team\nFirst team" },
    });

    const save = vi.spyOn(h.controller, "saveCapacitiesSource").mockResolvedValue({
      source: savedRecord(5, [
        {
          ...recordFixture.structures[0],
          openStatusValues: ["zeta", "Alpha", "mm"],
          assignmentValues: ["Second team", "First team"],
        },
        { ...recordFixture.structures[1] },
      ]),
      persisted: true,
    });
    fireEvent.click(rendered.getByRole("button", { name: "Save mapping" }));
    await waitFor(() => expect(save).toHaveBeenCalledTimes(1));

    const draft = save.mock.calls[0][0];
    expect(draft.structures[0].openStatusValues).toEqual(["zeta", "Alpha", "mm"]);
    expect(draft.structures[0].assignmentValues).toEqual([
      "Second team",
      "First team",
    ]);
    const body = capacitiesSourceToWire(draft);
    expect(body.structures[0].open_status_values).toEqual(["zeta", "Alpha", "mm"]);
    expect(body.structures[0].assignment_values).toEqual([
      "Second team",
      "First team",
    ]);

    // The server echo round-trips into the same order on screen.
    await waitFor(() =>
      expect(
        (
          rendered.getByLabelText(
            "Open status values for Project",
          ) as HTMLTextAreaElement
        ).value,
      ).toBe("zeta\nAlpha\nmm"),
    );
  });

  it("removes a structure only through its explicit row action", async () => {
    const { h, rendered } = await openEditor({
      source: recordFixture,
      persisted: true,
    });
    await waitFor(() =>
      expect(rendered.getByLabelText("Structure ID for Task")).toBeTruthy(),
    );

    // A failed save leaves both rows exactly where they were.
    const save = vi
      .spyOn(h.controller, "saveCapacitiesSource")
      .mockRejectedValueOnce(new Error("boom"));
    fireEvent.click(rendered.getByRole("button", { name: "Save mapping" }));
    await waitFor(() => expect(rendered.getByRole("alert").textContent).toContain("boom"));
    expect(rendered.getByLabelText("Structure ID for Project")).toBeTruthy();
    expect(rendered.getByLabelText("Structure ID for Task")).toBeTruthy();

    // Only the row's own Remove button drops a structure.
    fireEvent.click(
      rendered.getByRole("button", { name: "Remove structure Project" }),
    );
    expect(rendered.queryByLabelText("Structure ID for Project")).toBeNull();
    expect(rendered.getByLabelText("Structure ID for Task")).toBeTruthy();

    save.mockResolvedValue({
      source: savedRecord(5, [{ ...recordFixture.structures[1] }]),
      persisted: true,
    });
    fireEvent.click(rendered.getByRole("button", { name: "Save mapping" }));
    await waitFor(() => expect(save).toHaveBeenCalledTimes(2));
    expect(save.mock.calls[1][0].structures.map((row) => row.structureId)).toEqual([
      "Task",
    ]);
  });

  it("shows an observed title beside the id and never renders the id twice", async () => {
    const { rendered } = await openEditor(
      { source: recordFixture, persisted: true },
      {
        availableStructures: ["Project", "Task"],
        structureTitles: { Project: "Projects", Task: "Task" },
      },
    );
    await waitFor(() =>
      expect(rendered.getByLabelText("Structure ID for Project")).toBeTruthy(),
    );
    const section = rendered
      .getByRole("heading", { name: "Capacities source mapping" })
      .closest("section") as HTMLElement;

    // The observed title leads...
    expect(within(section).getByText("Projects").tagName.toLowerCase()).toBe("strong");
    // ...while the raw id stays visible in its own field.
    expect(
      (rendered.getByLabelText("Structure ID for Project") as HTMLInputElement).value,
    ).toBe("Project");
    // A title that merely repeats the id adds nothing: the id renders once,
    // in its own field, and never as a duplicated label.
    expect(within(section).queryByText("Task")).toBeNull();
    expect(
      (rendered.getByLabelText("Structure ID for Task") as HTMLInputElement).value,
    ).toBe("Task");
  });
});

/* -- Discovery -------------------------------------------------------------- */

// The operator's real Project structure carries this raw property id.
const ASSIGNED_PROPERTY = "f779f78a-d434-4099-9448-a90785bc8ae5";

/** Shapes taken from the operator's real space: RootTask is titled "Task",
    and custom-project is the real no-name case whose reported title is its
    own structure id. */
const catalogFixture: CapacitiesCatalog = {
  spaceId: "space-1",
  structures: [
    {
      structureId: "RootTask",
      title: "Task",
      properties: [
        {
          propertyId: ASSIGNED_PROPERTY,
          title: "Assigned",
          type: "label",
          writable: true,
          labelOptions: [
            { id: "assignee-meegy", title: "Meegy" },
            { id: "assignee-adam", title: "Adam" },
          ],
        },
        {
          propertyId: "completed-prop",
          title: "Completed",
          type: "checkbox",
          writable: true,
          labelOptions: [],
        },
      ],
    },
    {
      structureId: "custom-project",
      title: "custom-project",
      properties: [
        {
          propertyId: "name-prop",
          title: "Name",
          type: "title",
          writable: true,
          labelOptions: [],
        },
        {
          propertyId: "status-prop",
          title: "Status",
          type: "label",
          writable: true,
          labelOptions: [{ id: "active", title: "Active" }],
        },
        {
          propertyId: "due-prop",
          title: "Due",
          type: "date",
          writable: false,
          labelOptions: [],
        },
      ],
    },
  ],
  warnings: [],
};

/** A row with every role unconfigured — what "Add to mapping" must produce
    when it sets nothing but the structure id. */
function unconfiguredRow(structureId: string): CapacitiesSourceStructure {
  return {
    structureId,
    titleProperty: "",
    statusProperty: null,
    openStatusValues: [],
    dateProperty: null,
    deadlineProperty: null,
    durationProperty: null,
    assignmentProperty: null,
    assignmentValues: [],
    completionProperty: null,
    completionValue: null,
  };
}

describe("CapacitiesSourceEditor discovery", () => {
  it("renders discovered ids, titles, types, and label options and never saves", async () => {
    const { h, rendered } = await openEditor({
      source: recordFixture,
      persisted: true,
    });
    await waitFor(() =>
      expect(rendered.getByLabelText("Duration property for Project")).toBeTruthy(),
    );
    const save = vi.spyOn(h.controller, "saveCapacitiesSource");
    const discover = vi
      .spyOn(h.controller, "discoverCapacitiesSource")
      .mockResolvedValue(catalogFixture);

    fireEvent.click(
      rendered.getByRole("button", { name: "Discover from Capacities" }),
    );
    await waitFor(() => expect(discover).toHaveBeenCalledTimes(1));
    // Discovery reads the draft's CURRENT space id.
    expect(discover).toHaveBeenCalledWith("space-1");

    const catalog = await waitFor(() =>
      rendered.getByRole("region", { name: "Discovered Capacities structures" }),
    );
    // Structure identity: the observed title and the raw id the mapping
    // keys on are both visible.
    expect(within(catalog).getByText("Task")).toBeTruthy();
    expect(within(catalog).getByText("RootTask")).toBeTruthy();
    // Property reference material, verbatim provider values.
    expect(within(catalog).getByText(ASSIGNED_PROPERTY)).toBeTruthy();
    expect(within(catalog).getByText("Assigned")).toBeTruthy();
    // Both label properties report their writability; the checkbox too.
    expect(within(catalog).getAllByText("label · writable").length).toBe(2);
    expect(within(catalog).getByText("checkbox · writable")).toBeTruthy();
    // Label options carry both the stored value id and its display title.
    expect(within(catalog).getByText("assignee-meegy")).toBeTruthy();
    expect(within(catalog).getByText("Meegy")).toBeTruthy();
    expect(within(catalog).getByText("assignee-adam")).toBeTruthy();
    expect(within(catalog).getByText("Adam")).toBeTruthy();
    // The second structure's properties too, including a read-only one.
    expect(within(catalog).getByText("name-prop")).toBeTruthy();
    expect(within(catalog).getByText("Name")).toBeTruthy();
    expect(within(catalog).getByText("title · writable")).toBeTruthy();
    expect(within(catalog).getByText("status-prop")).toBeTruthy();
    expect(within(catalog).getByText("active")).toBeTruthy();
    expect(within(catalog).getByText("Active")).toBeTruthy();
    expect(within(catalog).getByText("due-prop")).toBeTruthy();
    expect(within(catalog).getByText("Due")).toBeTruthy();
    expect(within(catalog).getByText("date · read-only")).toBeTruthy();

    // Discovery performs no save and adds no draft row.
    expect(save).not.toHaveBeenCalled();
    expect(rendered.getByLabelText("Structure ID for Project")).toBeTruthy();
    expect(rendered.getByLabelText("Structure ID for Task")).toBeTruthy();
  });

  it("treats an id-titled structure as a normal entry and adds it with every role unconfigured", async () => {
    const { h, rendered } = await openEditor({ source: null, persisted: false });
    await waitFor(() =>
      expect(
        rendered.getByText(/No Capacities source mapping has been saved/),
      ).toBeTruthy(),
    );
    fireEvent.input(rendered.getByLabelText("Capacities space id"), {
      target: { value: "space-1" },
    });
    vi.spyOn(h.controller, "discoverCapacitiesSource").mockResolvedValue(
      catalogFixture,
    );
    const save = vi.spyOn(h.controller, "saveCapacitiesSource");
    fireEvent.click(
      rendered.getByRole("button", { name: "Discover from Capacities" }),
    );

    const add = await waitFor(() =>
      rendered.getByRole("button", { name: "Add custom-project to mapping" }),
    );
    // The real no-name case renders as a normal entry: title and id both
    // visible, no failure reported anywhere.
    const entry = add.closest("article") as HTMLElement;
    expect(entry).toBeTruthy();
    expect(within(entry).getAllByText("custom-project").length).toBe(2);
    expect(rendered.queryByRole("alert")).toBeNull();
    expect(save).not.toHaveBeenCalled();

    fireEvent.click(add);
    await waitFor(() =>
      expect(
        rendered.getByLabelText("Structure ID for custom-project"),
      ).toBeTruthy(),
    );
    // The appended row carries the id and nothing else: even though the
    // catalog offers properties named Name/Status/Due with title/label/date
    // types, no role was inferred from any name, title, or type.
    expect(
      (rendered.getByLabelText("Structure ID for custom-project") as HTMLInputElement)
        .value,
    ).toBe("custom-project");
    expect(
      (rendered.getByLabelText("Title property for custom-project") as HTMLInputElement)
        .value,
    ).toBe("");
    expect(
      (rendered.getByLabelText("Status property for custom-project") as HTMLInputElement)
        .value,
    ).toBe("");
    expect(
      (
        rendered.getByLabelText("Assignment property for custom-project") as HTMLInputElement
      ).value,
    ).toBe("");

    // The strongest proof: the outgoing save body is the raw empty shape
    // with only the structure id set.
    save.mockResolvedValue({
      source: savedRecord(1, [unconfiguredRow("custom-project")]),
      persisted: true,
    });
    fireEvent.click(rendered.getByRole("button", { name: "Save mapping" }));
    await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
    expect(save.mock.calls[0][0]).toEqual({
      expectedRevision: 0,
      spaceId: "space-1",
      structures: [unconfiguredRow("custom-project")],
    });
  });

  it("keeps the draft byte-identical and saves nothing when discovery fails", async () => {
    const { h, rendered } = await openEditor({
      source: recordFixture,
      persisted: true,
    });
    await waitFor(() =>
      expect(rendered.getByLabelText("Duration property for Project")).toBeTruthy(),
    );
    // An unsaved operator edit makes any draft mutation observable.
    fireEvent.input(rendered.getByLabelText("Duration property for Project"), {
      target: { value: PROJECT_DURATION },
    });
    const save = vi.spyOn(h.controller, "saveCapacitiesSource");
    vi.spyOn(h.controller, "discoverCapacitiesSource").mockRejectedValue(
      new ApiError(
        429,
        {
          code: "capacities_discovery_rate_limited",
          message: "Capacities rate limited discovery; wait and retry.",
        },
        "Capacities rate limited discovery; wait and retry.",
      ),
    );

    fireEvent.click(
      rendered.getByRole("button", { name: "Discover from Capacities" }),
    );
    const alert = await waitFor(() => rendered.getByRole("alert"));
    expect(alert.textContent).toContain(
      "Capacities rate limited discovery; wait and retry.",
    );
    expect(alert.textContent).toContain("wait a moment, then try again");
    // No save, no catalog, no draft mutation.
    expect(save).not.toHaveBeenCalled();
    expect(
      rendered.queryByRole("region", { name: "Discovered Capacities structures" }),
    ).toBeNull();
    expect(
      (rendered.getByLabelText("Duration property for Project") as HTMLInputElement)
        .value,
    ).toBe(PROJECT_DURATION);
    expect(rendered.getByLabelText("Structure ID for Task")).toBeTruthy();

    // A later explicit save carries the byte-identical draft.
    save.mockResolvedValue({
      source: savedRecord(5, [
        { ...recordFixture.structures[0], durationProperty: PROJECT_DURATION },
        { ...recordFixture.structures[1] },
      ]),
      persisted: true,
    });
    fireEvent.click(rendered.getByRole("button", { name: "Save mapping" }));
    await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
    expect(save.mock.calls[0][0]).toEqual({
      expectedRevision: 4,
      spaceId: "space-1",
      structures: [
        { ...recordFixture.structures[0], durationProperty: PROJECT_DURATION },
        { ...recordFixture.structures[1] },
      ],
    });
  });

  it("classifies discovery failures from the ApiError status", async () => {
    const { h, rendered } = await openEditor({
      source: recordFixture,
      persisted: true,
    });
    await waitFor(() =>
      expect(rendered.getByLabelText("Duration property for Project")).toBeTruthy(),
    );
    const discover = vi.spyOn(h.controller, "discoverCapacitiesSource");

    // 503: credentials are missing — an operator must fix them, no retry.
    discover.mockRejectedValueOnce(
      new ApiError(
        503,
        {
          code: "capacities_discovery_credentials_unavailable",
          message: "Capacities credential is unavailable; discovery cannot run.",
        },
        "Capacities credential is unavailable; discovery cannot run.",
      ),
    );
    fireEvent.click(
      rendered.getByRole("button", { name: "Discover from Capacities" }),
    );
    const credentials = await waitFor(() => {
      const next = rendered.getByRole("alert");
      expect(next.textContent).toContain("retrying will not help");
      return next;
    });
    expect(credentials.textContent).toContain(
      "Capacities credential is unavailable; discovery cannot run.",
    );

    // 429: rate limited — wait and retry.
    discover.mockRejectedValueOnce(
      new ApiError(
        429,
        {
          code: "capacities_discovery_rate_limited",
          message: "Capacities rate limited discovery; wait and retry.",
        },
        "Capacities rate limited discovery; wait and retry.",
      ),
    );
    fireEvent.click(
      rendered.getByRole("button", { name: "Discover from Capacities" }),
    );
    const rateLimited = await waitFor(() => {
      const next = rendered.getByRole("alert");
      expect(next.textContent).toContain("wait a moment, then try again");
      return next;
    });
    expect(rateLimited.textContent).toContain(
      "Capacities rate limited discovery; wait and retry.",
    );

    // Anything else (here 502) is a generic failure.
    discover.mockRejectedValueOnce(
      new ApiError(
        502,
        {
          code: "capacities_discovery_failed",
          message: "Capacities discovery failed upstream.",
        },
        "Capacities discovery failed upstream.",
      ),
    );
    fireEvent.click(
      rendered.getByRole("button", { name: "Discover from Capacities" }),
    );
    const generic = await waitFor(() => {
      const next = rendered.getByRole("alert");
      expect(next.textContent).toContain("Discovery failed.");
      return next;
    });
    expect(generic.textContent).toContain("Capacities discovery failed upstream.");
    expect(generic.textContent).not.toContain("retrying will not help");
    expect(generic.textContent).not.toContain("wait a moment");
  });

  it("disables the discovery control while a discovery is running", async () => {
    const { h, rendered } = await openEditor({
      source: recordFixture,
      persisted: true,
    });
    await waitFor(() =>
      expect(rendered.getByLabelText("Duration property for Project")).toBeTruthy(),
    );
    let resolveDiscovery!: (catalog: CapacitiesCatalog) => void;
    vi.spyOn(h.controller, "discoverCapacitiesSource").mockReturnValue(
      new Promise<CapacitiesCatalog>((resolve) => {
        resolveDiscovery = resolve;
      }),
    );

    fireEvent.click(
      rendered.getByRole("button", { name: "Discover from Capacities" }),
    );
    await waitFor(() =>
      expect(
        (
          rendered.getByRole("button", { name: "Discovering…" }) as HTMLButtonElement
        ).disabled,
      ).toBe(true),
    );

    resolveDiscovery(catalogFixture);
    await waitFor(() =>
      expect(
        (
          rendered.getByRole("button", {
            name: "Discover from Capacities",
          }) as HTMLButtonElement
        ).disabled,
      ).toBe(false),
    );
    expect(
      rendered.getByRole("region", { name: "Discovered Capacities structures" }),
    ).toBeTruthy();
  });

  it("renders an empty catalog and its warnings without throwing", async () => {
    const { h, rendered } = await openEditor({
      source: recordFixture,
      persisted: true,
    });
    await waitFor(() =>
      expect(rendered.getByLabelText("Duration property for Project")).toBeTruthy(),
    );
    // A different space id proves discovery reads the draft's current value.
    fireEvent.input(rendered.getByLabelText("Capacities space id"), {
      target: { value: "space-42" },
    });
    const discover = vi
      .spyOn(h.controller, "discoverCapacitiesSource")
      .mockResolvedValue({
        spaceId: "space-42",
        structures: [],
        warnings: ["Some structures could not be read; this catalog is partial."],
      });

    fireEvent.click(
      rendered.getByRole("button", { name: "Discover from Capacities" }),
    );
    await waitFor(() => expect(discover).toHaveBeenCalledWith("space-42"));
    const catalog = await waitFor(() =>
      rendered.getByRole("region", { name: "Discovered Capacities structures" }),
    );
    expect(
      within(catalog).getByText("Capacities reported no structures in this space."),
    ).toBeTruthy();
    expect(
      within(catalog).getByText(
        "Some structures could not be read; this catalog is partial.",
      ),
    ).toBeTruthy();
    expect(rendered.queryByRole("alert")).toBeNull();
    // The draft still holds both rows exactly as loaded.
    expect(rendered.getByLabelText("Structure ID for Project")).toBeTruthy();
    expect(rendered.getByLabelText("Structure ID for Task")).toBeTruthy();
  });
});
