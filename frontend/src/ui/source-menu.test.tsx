import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, waitFor } from "@testing-library/preact";
import { SourceMenu } from "./SourceMenu";
import { useAppState } from "./context";
import { makeHarness } from "./test-harness";
import type { AssignedItem } from "../model/types";

afterEach(cleanup);

function setup(opts: { source?: "todoist" | "capacities" | "calendar"; recurring?: boolean; confirmed?: boolean; busy?: boolean; missingIdentity?: boolean } = {}) {
  const h = makeHarness("ready", (sc) => {
    if (opts.confirmed === false) {
      sc.inputs.daySetup.confirmed = false;
      sc.staged.daySetupConfirmed = false;
    }
    const item = sc.inputs.assigned.find((r) => r.id === "Press")!;
    item.isRecurring = opts.recurring ?? false;
    if (opts.source === "capacities") Object.assign(item, {
      source: "capacities", todoistId: null,
      identity: "capacities:fixture-space:custom-project:press",
    });
    if (opts.source === "calendar") Object.assign(item, { source: "calendar", identity: "calendar:unrelated", todoistId: null });
    if (opts.missingIdentity) Object.assign(item, { identity: null, todoistId: null, path: null });
  });
  if (opts.confirmed !== false) h.store.dispatch({ type: "SETUP_SAVED", daySetup: { ...h.store.getState().daySetup, confirmed: true } });
  if (opts.busy) h.store.dispatch({ type: "RUNTIME_START" });
  const batch = vi.spyOn(h.adapter, "applyRuntimeBatch");
  const verb = vi.spyOn(h.adapter, "runtimeAction");
  const rowBatch = vi.spyOn(h.controller, "runRowBatch");
  const staging = vi.spyOn(h.controller, "stagingAction");
  const policy = vi.spyOn(h.controller, "alwaysExcludeTodoist");
  const capPolicy = vi.spyOn(h.controller, "alwaysExcludeCapacitiesObject");
  function View() {
    const s = useAppState();
    const item = s.inputs!.assigned.find((r) => r.id === "Press") as AssignedItem;
    return <SourceMenu item={item} s={s} />;
  }
  const ui = h.ui(<View />);
  const trigger = ui.getByRole("button", { name: "Source ▾" });
  const open = () => fireEvent.click(trigger);
  return { ...h, ...ui, trigger, open, batch, verb, rowBatch, staging, policy, capPolicy };
}

describe("SourceMenu (unmounted component slice)", () => {
  it("has exactly the compact source actions, with no Complete", () => {
    const h = setup();
    expect(h.trigger.getAttribute("aria-haspopup")).toBe("menu");
    expect(h.trigger.getAttribute("aria-expanded")).toBe("false");
    h.open();
    expect(h.trigger.getAttribute("aria-expanded")).toBe("true");
    expect(h.getAllByRole("menuitem").map((b) => b.getAttribute("aria-label"))).toEqual([
      "Unassign in source", "Hide always", "Delete source…",
    ]);
    expect(h.queryByText("Complete")).toBeNull();
  });

  it.each([false, true])("unassign recurring=%s sends backend unassign to the stable identity immediately", async (recurring) => {
    const h = setup({ recurring });
    h.open();
    fireEvent.click(h.getByRole("menuitem", { name: recurring ? "Skip occurrence" : "Unassign in source" }));
    await waitFor(() => expect(h.batch).toHaveBeenCalledWith([
      { verb: "unassign", target: "todoist:6fx00xPRESS", args: {} },
    ]));
    expect(h.rowBatch).toHaveBeenCalledWith("Press", "unassign");
    expect(h.staging).not.toHaveBeenCalled();
    expect(h.queryByRole("menu")).toBeNull();
    expect(h.queryByText("Delete from Todoist")).toBeNull();
  });

  it.each(["todoist", "capacities"] as const)("Hide always is journaled for %s, never a policy save", async (source) => {
    const h = setup({ source });
    h.open();
    if (source === "capacities") {
      expect(h.queryByRole("menuitem", { name: "Unassign in source" })).toBeNull();
      expect(h.queryByRole("menuitem", { name: "Skip occurrence" })).toBeNull();
      expect(h.getByText("Capacities rows cannot be unassigned in source")).toBeTruthy();
    }
    fireEvent.click(h.getByRole("menuitem", { name: "Hide always" }));
    await waitFor(() => expect(h.batch).toHaveBeenCalledWith([
      { verb: "hide_always", target: source === "todoist" ? "todoist:6fx00xPRESS" : "capacities:fixture-space:custom-project:press", args: {} },
    ]));
    expect(h.rowBatch).toHaveBeenCalledWith("Press", "hideAlways");
    expect(h.policy).not.toHaveBeenCalled();
    expect(h.capPolicy).not.toHaveBeenCalled();
    expect(h.verb).not.toHaveBeenCalled();
  });

  it("Delete requires explicit individual confirmation; cancel is inert and restores focus", () => {
    const h = setup();
    h.open();
    fireEvent.click(h.getByRole("menuitem", { name: "Delete source…" }));
    expect(h.getByRole("group", { name: "Delete “Press” from Todoist?" })).toBeTruthy();
    expect(h.staging).not.toHaveBeenCalled();
    const cancel = h.getByRole("button", { name: "Cancel" });
    expect(document.activeElement).toBe(cancel);
    fireEvent.click(cancel);
    expect(h.queryByRole("group")).toBeNull();
    expect(document.activeElement).toBe(h.trigger);
    expect(h.verb).not.toHaveBeenCalled();
    expect(h.batch).not.toHaveBeenCalled();
  });

  it("Delete uses existing staging delete semantics and does not announce success on adapter failure", async () => {
    const h = setup();
    h.verb.mockRejectedValueOnce(new Error("fixture deletion refused"));
    h.open();
    fireEvent.click(h.getByRole("menuitem", { name: "Delete source…" }));
    fireEvent.click(h.getByRole("button", { name: "Delete from Todoist" }));
    await waitFor(() => expect(h.store.getState().runtimeError).toBe("fixture deletion refused"));
    expect(h.staging).toHaveBeenCalledWith("delete", "Press");
    expect(h.verb).toHaveBeenCalledWith("delete", "Press", {});
    expect(h.batch).not.toHaveBeenCalled();
    expect(h.container.textContent).not.toMatch(/deleted|success/i);
  });

  it.each([
    [{ confirmed: false }, "Confirm Day Setup first"],
    [{ busy: true }, "Source action in progress"],
    [{ source: "calendar" as const }, "Calendar source actions are not available here"],
    [{ missingIdentity: true }, "This item has no stable source identity"],
  ])("disables actions with a written accessible reason: %s", (opts, reason) => {
    const h = setup(opts);
    h.open();
    for (const button of h.getAllByRole("menuitem")) {
      expect((button as HTMLButtonElement).disabled).toBe(true);
      expect(h.container.querySelector(`#${button.getAttribute("aria-describedby")}`)?.textContent).toBe(reason);
      fireEvent.click(button);
    }
    expect(h.rowBatch).not.toHaveBeenCalled();
    expect(h.staging).not.toHaveBeenCalled();
  });

  it("supports arrows, Home/End, Escape focus return and outside close", () => {
    const h = setup();
    fireEvent.keyDown(h.trigger, { key: "ArrowUp" });
    const items = h.getAllByRole("menuitem");
    expect(document.activeElement).toBe(items[2]);
    fireEvent.keyDown(items[2], { key: "ArrowDown" });
    expect(document.activeElement).toBe(items[0]);
    fireEvent.keyDown(items[0], { key: "End" });
    expect(document.activeElement).toBe(items[2]);
    fireEvent.keyDown(items[2], { key: "Home" });
    expect(document.activeElement).toBe(items[0]);
    fireEvent.keyDown(items[0], { key: "ArrowUp" });
    expect(document.activeElement).toBe(items[2]);
    fireEvent.keyDown(items[2], { key: "Escape" });
    expect(h.queryByRole("menu")).toBeNull();
    expect(document.activeElement).toBe(h.trigger);
    h.open();
    fireEvent.mouseDown(document.body);
    expect(h.queryByRole("menu")).toBeNull();
  });

  it("Escape closes Delete confirmation before it can reach ancestor shortcuts", () => {
    const h = setup();
    h.open();
    fireEvent.click(h.getByRole("menuitem", { name: "Delete source…" }));
    const ancestor = vi.fn();
    document.addEventListener("keydown", ancestor);
    try {
      fireEvent.keyDown(h.getByRole("button", { name: "Cancel" }), { key: "Escape" });
      expect(h.queryByRole("group")).toBeNull();
      expect(document.activeElement).toBe(h.trigger);
      expect(ancestor).not.toHaveBeenCalled();
      expect(h.staging).not.toHaveBeenCalled();
    } finally { document.removeEventListener("keydown", ancestor); }
  });
});
