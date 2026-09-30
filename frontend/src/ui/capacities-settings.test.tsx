import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, waitFor } from "@testing-library/preact";
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
      availableStructures: ["custom-project", "0d194525-c5a1-4af5-bb62-202b83006b5e"],
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
      availableStructures: ["custom-project", "0d194525-c5a1-4af5-bb62-202b83006b5e"],
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
      availableStructures: ["custom-project"],
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
      availableStructures: [],
    });
    h.store.dispatch({ type: "UI", patch: { capacitiesSettingsOpen: true } });
    const rendered = h.ui(<CapacitiesSettingsDrawer />);
    await waitFor(() => expect(rendered.getByText("Active pull")).toBeTruthy());

    expect(rendered.getByText(/No Capacities structures are configured/)).toBeTruthy();
    expect(rendered.queryByRole("checkbox", { name: /Active pull for/ })).toBeNull();
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
});
