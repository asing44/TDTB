import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, waitFor, within } from "@testing-library/preact";
import { CapacitiesSettingsDrawer } from "./CapacitiesSettingsDrawer";
import { makeHarness } from "./test-harness";
import { capacitiesSettingsToWire } from "../adapters/wire";
import type { AssignedItem } from "../model/types";

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
    inputs: { ...inputs, assigned: [...inputs.assigned, capacityItem] },
    ledger: h.store.getState().ledger!,
  });
  h.store.dispatch({ type: "UI", patch: { capacitiesSettingsOpen: true } });
  const rendered = h.ui(<CapacitiesSettingsDrawer />);
  return { h, rendered };
}

describe("CapacitiesSettingsDrawer", () => {
  it("loads native rules and lists known Capacities identities without inventing inventory", async () => {
    const { rendered } = openWithCapacityRow();
    expect(rendered.getByRole("status").textContent).toContain("Loading local policy");
    await waitFor(() => expect(rendered.getByText("Native Task Auto rules")).toBeTruthy());

    expect(rendered.getByText("Known Capacities objects")).toBeTruthy();
    expect(rendered.getByText("Capacity task")).toBeTruthy();
    expect(rendered.getByText("capacities:space-1:RootTask:task-1")).toBeTruthy();
    expect(rendered.getByRole("checkbox", { name: /Active status/ })).toBeTruthy();
    expect((rendered.getByLabelText("Deadline horizon") as HTMLInputElement).value).toBe("2");
    expect(rendered.getByText(/not a provider inventory browser/)).toBeTruthy();
  });

  it("saves the full replacement draft with the loaded revision and keeps exclusions local", async () => {
    const { h, rendered } = openWithCapacityRow();
    await waitFor(() => expect(rendered.getByText("Capacity task")).toBeTruthy());

    fireEvent.click(rendered.getByRole("checkbox", { name: /Available to TDTB/ }));
    fireEvent.click(rendered.getByRole("checkbox", { name: /Active status/ }));
    fireEvent.input(rendered.getByLabelText("Deadline horizon"), { target: { value: "5" } });
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
      inputs: { ...inputs, assigned: [...inputs.assigned, capacityItem] },
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
      availableStructures: ["custom-project", "0d194525-c5a1-4af5-bb62-202b83006b5e"],
      structureTitles: {},
    });
    h.store.dispatch({ type: "UI", patch: { capacitiesSettingsOpen: true } });
    const rendered = h.ui(<CapacitiesSettingsDrawer />);
    await waitFor(() => expect(rendered.getByText("Native Task Auto rules")).toBeTruthy());

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

  it("adds a checked available structure to the outgoing save body", async () => {
    const { h, rendered } = openWithCapacityRow();
    await waitFor(() => expect(rendered.getByText("Active pull")).toBeTruthy());

    const custom = rendered.getByRole("checkbox", {
      name: "Active pull for custom-project",
    }) as HTMLInputElement;
    expect(custom.checked).toBe(false);
    fireEvent.click(custom);
    expect(custom.checked).toBe(true);

    const save = vi.spyOn(h.controller, "saveCapacitiesSettings");
    fireEvent.click(rendered.getByRole("button", { name: "Save Capacities settings" }));
    await waitFor(() => expect(save).toHaveBeenCalledTimes(1));

    expect(capacitiesSettingsToWire(save.mock.calls[0][0]).active_structures).toEqual({
      "custom-project": true,
    });
  });

  it("unchecking an active structure removes it from the outgoing save body", async () => {
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
      availableStructures: ["custom-project", "0d194525-c5a1-4af5-bb62-202b83006b5e"],
      structureTitles: {},
    });
    h.store.dispatch({ type: "UI", patch: { capacitiesSettingsOpen: true } });
    const rendered = h.ui(<CapacitiesSettingsDrawer />);
    await waitFor(() => expect(rendered.getByText("Active pull")).toBeTruthy());

    const custom = rendered.getByRole("checkbox", {
      name: "Active pull for custom-project",
    }) as HTMLInputElement;
    expect(custom.checked).toBe(true);
    fireEvent.click(custom);
    expect(custom.checked).toBe(false);

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
      availableStructures: ["custom-project"],
      structureTitles: {},
    });
    h.store.dispatch({ type: "UI", patch: { capacitiesSettingsOpen: true } });
    const rendered = h.ui(<CapacitiesSettingsDrawer />);
    await waitFor(() => expect(rendered.getByText("Active pull")).toBeTruthy());

    // The stale id is surfaced rather than hidden.
    expect(rendered.getByText(/Active structures outside this vault's mapping/)).toBeTruthy();
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
      availableStructures: [],
      structureTitles: {},
    });
    h.store.dispatch({ type: "UI", patch: { capacitiesSettingsOpen: true } });
    const rendered = h.ui(<CapacitiesSettingsDrawer />);
    await waitFor(() => expect(rendered.getByText("Active pull")).toBeTruthy());

    expect(rendered.getByText(/No Capacities structures are configured/)).toBeTruthy();
    expect(rendered.queryByRole("checkbox", { name: /Active pull for/ })).toBeNull();
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
      availableStructures: ["custom-project", "0d194525-c5a1-4af5-bb62-202b83006b5e"],
      structureTitles: {},
    });
    h.store.dispatch({ type: "UI", patch: { capacitiesSettingsOpen: true } });
    const rendered = h.ui(<CapacitiesSettingsDrawer />);
    await waitFor(() => expect(rendered.getByText("Admission vocabulary")).toBeTruthy());

    // The structure list is the same vault-local source mapping the Active
    // pull editor already enumerates — no new discovery path.
    expect(rendered.getByText(/same inventory as Active pull/)).toBeTruthy();
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
    await waitFor(() => expect(rendered.getByText("Admission vocabulary")).toBeTruthy());

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
    await waitFor(() => expect(rendered.getByText("Assigned flag declarations")).toBeTruthy());

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

  it("declares a per-structure assignment property with a text input", async () => {
    const { h, rendered } = openWithCapacityRow();
    await waitFor(() => expect(rendered.getByText("Assigned flag declarations")).toBeTruthy());

    const input = rendered.getByLabelText("Assigned property for custom-project") as HTMLInputElement;
    expect(input.value).toBe("");
    fireEvent.input(input, { target: { value: "assigned-prop-id" } });
    expect(input.value).toBe("assigned-prop-id");

    const save = vi.spyOn(h.controller, "saveCapacitiesSettings");
    fireEvent.click(rendered.getByRole("button", { name: "Save Capacities settings" }));
    await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
    expect(capacitiesSettingsToWire(save.mock.calls[0][0]).assigned_structures).toEqual({
      "custom-project": "assigned-prop-id",
    });
  });

  it("clears a declaration when its field is emptied", async () => {
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
      availableStructures: ["custom-project"],
      structureTitles: {},
    });
    h.store.dispatch({ type: "UI", patch: { capacitiesSettingsOpen: true } });
    const rendered = h.ui(<CapacitiesSettingsDrawer />);
    await waitFor(() => expect(rendered.getByText("Assigned flag declarations")).toBeTruthy());

    const input = rendered.getByLabelText("Assigned property for custom-project") as HTMLInputElement;
    expect(input.value).toBe("old-prop");
    fireEvent.input(input, { target: { value: "" } });

    const save = vi.spyOn(h.controller, "saveCapacitiesSettings");
    fireEvent.click(rendered.getByRole("button", { name: "Save Capacities settings" }));
    await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
    expect(capacitiesSettingsToWire(save.mock.calls[0][0]).assigned_structures).toEqual({});
  });

  it("keeps a saved declaration outside the vault mapping and round-trips it", async () => {
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
      availableStructures: ["custom-project"],
      structureTitles: {},
    });
    h.store.dispatch({ type: "UI", patch: { capacitiesSettingsOpen: true } });
    const rendered = h.ui(<CapacitiesSettingsDrawer />);
    await waitFor(() => expect(rendered.getByText("Assigned flag declarations")).toBeTruthy());

    // Retained and surfaced, never silently dropped. (The native and active
    // stale blocks use the same wording, so count rather than pick one.)
    expect(rendered.getByText(/Declared structures outside this vault's mapping/)).toBeTruthy();
    expect(rendered.getAllByText(/retained unchanged on save/).length).toBeGreaterThan(0);
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
      availableStructures: ["custom-project"],
      structureTitles: {},
    });
    h.store.dispatch({ type: "UI", patch: { capacitiesSettingsOpen: true } });
    const rendered = h.ui(<CapacitiesSettingsDrawer />);
    await waitFor(() => expect(rendered.getByText("Ignored declarations in the current read")).toBeTruthy());

    // The server's own wording, passed through unchanged — never paraphrased.
    expect(rendered.getByText(warning)).toBeTruthy();
    // The partial-coverage warning belongs to the readiness rail, not here.
    expect(rendered.queryByText(coverage)).toBeNull();
    // The declaration stays editable so the operator can correct it.
    expect(
      (rendered.getByLabelText("Assigned property for custom-project") as HTMLInputElement).value,
    ).toBe("assigned-prop");
  });

  it("keeps a saved native structure outside the vault mapping and round-trips it", async () => {
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
      availableStructures: ["custom-project"],
      structureTitles: {},
    });
    h.store.dispatch({ type: "UI", patch: { capacitiesSettingsOpen: true } });
    const rendered = h.ui(<CapacitiesSettingsDrawer />);
    await waitFor(() => expect(rendered.getByText("Admission vocabulary")).toBeTruthy());

    // Retained and surfaced, never silently dropped.
    expect(rendered.getByText(/Native structures outside this vault's mapping/)).toBeTruthy();
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
      "Capacities structures with a declared assignment property",
      "Capacities structures honouring an Active status pull",
    ];
    await waitFor(() =>
      expect(rendered.getByRole("group", { name: groupNames[2] })).toBeTruthy(),
    );

    for (const name of groupNames) {
      const group = within(rendered.getByRole("group", { name }));
      // The titled structure leads with its observed title...
      const title = group.getByText("Project");
      expect(title.tagName.toLowerCase()).toBe("strong");
      // ...and the raw id stays visible beside it, in the same row.
      const titledId = group.getByText("0d194525-c5a1-4af5-bb62-202b83006b5e");
      expect(titledId.tagName.toLowerCase()).toBe("code");
      expect(title.parentElement).toBe(titledId.parentElement);
      expect(titledId.parentElement?.textContent).toBe(
        "Project0d194525-c5a1-4af5-bb62-202b83006b5e",
      );
      // An untitled structure shows its id alone, never a blank label.
      const untitledId = group.getByText("custom-project");
      expect(untitledId.parentElement?.querySelector("strong")).toBeNull();
      expect(untitledId.parentElement?.textContent).toBe("custom-project");
    }
    // The title is display-only: the accessible name still keys on the id.
    expect(
      rendered.getByRole("checkbox", {
        name: "Active pull for 0d194525-c5a1-4af5-bb62-202b83006b5e",
      }),
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
      availableStructures: ["root-task"],
      // The server falls back to the id when a structure has no display
      // name, so the repeated title must render as the id, not twice.
      structureTitles: { "root-task": "root-task" },
    });
    h.store.dispatch({ type: "UI", patch: { capacitiesSettingsOpen: true } });
    const rendered = h.ui(<CapacitiesSettingsDrawer />);
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
      availableStructures: ["custom-project"],
      structureTitles: { "legacy-structure": "Legacy Project" },
    });
    h.store.dispatch({ type: "UI", patch: { capacitiesSettingsOpen: true } });
    const rendered = h.ui(<CapacitiesSettingsDrawer />);
    await waitFor(() =>
      expect(rendered.getByText(/Active structures outside this vault's mapping/)).toBeTruthy(),
    );

    // The stale id is outside this vault's mapping, so it keeps the raw id
    // and never borrows a title the drawer does not consider available.
    expect(rendered.getByText("legacy-structure")).toBeTruthy();
    expect(rendered.queryByText("Legacy Project")).toBeNull();
  });
});
