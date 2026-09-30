import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, waitFor } from "@testing-library/preact";
import { CapacitiesSettingsDrawer } from "./CapacitiesSettingsDrawer";
import { makeHarness } from "./test-harness";
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
    });
    await waitFor(() => expect(h.store.getState().ui.capacitiesSettingsOpen).toBe(false));
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
