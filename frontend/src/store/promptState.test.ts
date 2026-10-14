/* U5 prompt-state store/controller tests (Planning 2a): prompt-only saves
   never confirm the day, browser-defined custom keys ride the captures patch,
   and a failed save is recorded as an error rather than thrown. The opt-in
   store and its conflict path are gone. */

import { describe, expect, it, vi } from "vitest";
import { makeHarness } from "../ui/test-harness";
import { Controller } from "./controller";
import { FixtureAdapter } from "../adapters/fixture";

/** A fixture whose draft write always fails, to exercise the error path. */
class FailingDraftAdapter extends FixtureAdapter {
  override async savePromptDrafts(): Promise<never> {
    throw new Error("drafts store unavailable");
  }
}

describe("U5 prompt state", () => {
  it("plan inputs no longer carry an opt-in block", () => {
    const { store } = makeHarness("fresh");
    expect(store.getState().inputs).not.toHaveProperty("promptOptins");
    expect(store.getState().daySetup.captures).toEqual({
      intention: "",
      forMeegy: "",
      stoic: "",
    });
  });

  it("a prompt-only save never confirms the day", async () => {
    const { store, controller } = makeHarness("fresh");
    expect(store.getState().daySetup.confirmed).toBe(false);
    await controller.savePromptDrafts({ intention: "focus on the one thing" });
    const s = store.getState();
    expect(s.daySetup.confirmed).toBe(false);
    expect(s.daySetup.captures.intention).toBe("focus on the one thing");
    expect(s.promptSave.phase).toBe("saved");
  });

  it("sends browser-defined custom keys through the drafts PATCH untouched", async () => {
    const h = makeHarness("fresh");
    const adapter = new FixtureAdapter("fresh");
    const spy = vi.spyOn(adapter, "savePromptDrafts");
    const controller = new Controller(adapter, h.store.dispatch, h.store.getState);
    await controller.savePromptDrafts({ "custom-abc-1": "the rain" });
    expect(spy).toHaveBeenCalledWith({ "custom-abc-1": "the rain" });
    // The built-in captures are untouched by a custom-key patch.
    expect(h.store.getState().daySetup.captures.forMeegy).toBe("");
  });

  it("records a failed draft save as an error without throwing", async () => {
    const h = makeHarness("fresh");
    const controller = new Controller(
      new FailingDraftAdapter("fresh"),
      h.store.dispatch,
      h.store.getState,
    );
    await controller.savePromptDrafts({ intention: "draft only" });
    const s = h.store.getState();
    expect(s.promptSave.phase).toBe("error");
    expect(s.promptSave.error).toContain("drafts store unavailable");
    expect(s.daySetup.confirmed).toBe(false);
  });
});
