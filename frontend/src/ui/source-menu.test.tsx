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
  const trigger = ui.getByRole("button", { name: "Source actions" });
  const open = () => fireEvent.click(trigger);
  return { ...h, ...ui, trigger, open, batch, verb, rowBatch, staging, policy, capPolicy };
}

describe("SourceMenu (unmounted component slice)", () => {
  it("has exactly Hide always and Delete, with no Complete, unassign or skip item", () => {
    const h = setup();
    expect(h.trigger.getAttribute("aria-haspopup")).toBe("menu");
    expect(h.trigger.getAttribute("aria-expanded")).toBe("false");
    h.open();
    expect(h.trigger.getAttribute("aria-expanded")).toBe("true");
    expect(h.getAllByRole("menuitem").map((b) => b.getAttribute("aria-label"))).toEqual([
      "Hide always", "Delete in Todoist…",
    ]);
    expect(h.queryByRole("menuitem", { name: "Unassign in source" })).toBeNull();
    expect(h.queryByRole("menuitem", { name: "Skip occurrence" })).toBeNull();
    expect(h.queryByText("Complete")).toBeNull();
    expect(h.container.textContent).not.toMatch(/unassign/i);
  });

  it.each(["todoist", "capacities", "calendar"] as const)(
    "offers the same two items for %s and never the Capacities note",
    (source) => {
      const h = setup({ source, recurring: true });
      h.open();
      expect(h.getAllByRole("menuitem").map((b) => b.getAttribute("aria-label"))).toEqual([
        "Hide always", `Delete in ${source[0].toUpperCase()}${source.slice(1)}…`,
      ]);
      expect(h.container.textContent).not.toMatch(/cannot be unassigned/i);
    },
  );

  it.each(["todoist", "capacities"] as const)("Hide always is journaled for %s, never a policy save", async (source) => {
    const h = setup({ source });
    h.open();
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
    fireEvent.click(h.getByRole("menuitem", { name: "Delete in Todoist…" }));
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
    fireEvent.click(h.getByRole("menuitem", { name: "Delete in Todoist…" }));
    fireEvent.click(h.getByRole("button", { name: "Yes, delete permanently" }));
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
    expect(items).toHaveLength(2);
    expect(document.activeElement).toBe(items[1]);
    fireEvent.keyDown(items[1], { key: "ArrowDown" });
    expect(document.activeElement).toBe(items[0]);
    fireEvent.keyDown(items[0], { key: "End" });
    expect(document.activeElement).toBe(items[1]);
    fireEvent.keyDown(items[1], { key: "Home" });
    expect(document.activeElement).toBe(items[0]);
    fireEvent.keyDown(items[0], { key: "ArrowUp" });
    expect(document.activeElement).toBe(items[1]);
    fireEvent.keyDown(items[1], { key: "Escape" });
    expect(h.queryByRole("menu")).toBeNull();
    expect(document.activeElement).toBe(h.trigger);
    h.open();
    fireEvent.mouseDown(document.body);
    expect(h.queryByRole("menu")).toBeNull();
  });

  it("Escape closes Delete confirmation before it can reach ancestor shortcuts", () => {
    const h = setup();
    h.open();
    fireEvent.click(h.getByRole("menuitem", { name: "Delete in Todoist…" }));
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

describe("SourceMenu presentation and interaction", () => {
  it("trigger is a labelled 32px icon button with menu semantics and a tooltip", () => {
    const h = setup();
    expect(h.trigger.getAttribute("aria-label")).toBe("Source actions");
    expect(h.trigger.getAttribute("title")).toBe("Source actions");
    expect(h.trigger.textContent).toBe("");
    expect(h.trigger.querySelector("svg")).not.toBeNull();
    expect(h.trigger.getAttribute("aria-haspopup")).toBe("menu");
    expect(h.trigger.getAttribute("aria-expanded")).toBe("false");
    expect(h.trigger.hasAttribute("aria-controls")).toBe(true);
    h.open();
    expect(h.trigger.getAttribute("aria-expanded")).toBe("true");
    const popover = h.container.querySelector(`#${h.trigger.getAttribute("aria-controls")}`);
    expect(popover?.querySelector('[role="menu"]')).not.toBeNull();
    // The old "Immediate actions" footer is gone as redundant.
    expect(h.container.textContent).not.toMatch(/Immediate actions/);
  });

  it("gives each item a leading icon and a one-line description, with the destructive item after a divider", () => {
    const h = setup();
    h.open();
    const [hide, del] = h.getAllByRole("menuitem");
    for (const item of [hide, del]) {
      expect(item.querySelector("svg")).not.toBeNull();
      expect(item.querySelector(".source-menu__detail")?.textContent).toBeTruthy();
    }
    expect(del.classList.contains("source-menu__item--danger")).toBe(true);
    expect(hide.nextElementSibling?.getAttribute("role")).toBe("separator");
    expect(hide.nextElementSibling?.nextElementSibling).toBe(del);
  });

  it("names the Delete item for its source", () => {
    const h = setup({ source: "capacities" });
    h.open();
    expect(h.getByRole("menuitem", { name: "Delete in Capacities…" })).toBeTruthy();
  });

  it("opens on Enter/Space click-equivalents and ArrowDown, focusing the first item", () => {
    const h = setup();
    fireEvent.keyDown(h.trigger, { key: "ArrowDown" });
    expect(h.trigger.getAttribute("aria-expanded")).toBe("true");
    expect(document.activeElement).toBe(h.getAllByRole("menuitem")[0]);
  });

  it("Tab closes the menu without refocusing the trigger", () => {
    const h = setup();
    h.open();
    fireEvent.keyDown(h.getAllByRole("menuitem")[0], { key: "Tab" });
    expect(h.queryByRole("menu")).toBeNull();
    expect(h.trigger.getAttribute("aria-expanded")).toBe("false");
  });

  it("clicking the trigger again closes the menu and keeps focus on it", () => {
    const h = setup();
    h.open();
    h.trigger.focus();
    fireEvent.click(h.trigger);
    expect(h.queryByRole("menu")).toBeNull();
    expect(document.activeElement).toBe(h.trigger);
  });

  it("outside click closes the menu and the confirm without acting", () => {
    const h = setup();
    h.open();
    fireEvent.click(document.body);
    expect(h.queryByRole("menu")).toBeNull();
    h.open();
    fireEvent.click(h.getByRole("menuitem", { name: "Delete in Todoist…" }));
    expect(h.getByRole("group")).toBeTruthy();
    fireEvent.mouseDown(document.body);
    expect(h.queryByRole("group")).toBeNull();
    expect(h.staging).not.toHaveBeenCalled();
    expect(h.verb).not.toHaveBeenCalled();
  });

  it("the delete confirm renders inside the popover, replacing the items, with zero calls until the final confirm", async () => {
    const h = setup();
    h.open();
    fireEvent.click(h.getByRole("menuitem", { name: "Delete in Todoist…" }));
    const popover = h.container.querySelector(".source-menu__popover")!;
    expect(popover.contains(h.getByRole("group"))).toBe(true);
    expect(h.queryByRole("menu")).toBeNull();
    expect(h.getByRole("button", { name: "Cancel" })).toBeTruthy();
    expect(h.staging).not.toHaveBeenCalled();
    expect(h.verb).not.toHaveBeenCalled();
    expect(h.batch).not.toHaveBeenCalled();
    // Tab swaps between the two confirm buttons instead of leaving the panel.
    const cancel = h.getByRole("button", { name: "Cancel" });
    const yes = h.getByRole("button", { name: "Yes, delete permanently" });
    expect(document.activeElement).toBe(cancel);
    fireEvent.keyDown(cancel, { key: "Tab" });
    expect(document.activeElement).toBe(yes);
    fireEvent.keyDown(yes, { key: "Tab" });
    expect(document.activeElement).toBe(cancel);
    expect(h.verb).not.toHaveBeenCalled();
    fireEvent.click(yes);
    await waitFor(() => expect(h.verb).toHaveBeenCalledWith("delete", "Press", {}));
    expect(h.staging).toHaveBeenCalledTimes(1);
  });

  it("returns focus to the trigger after Hide always runs", async () => {
    const h = setup();
    h.open();
    fireEvent.click(h.getByRole("menuitem", { name: "Hide always" }));
    await waitFor(() => expect(h.batch).toHaveBeenCalled());
    expect(h.queryByRole("menu")).toBeNull();
    expect(document.activeElement).toBe(h.trigger);
  });
});
