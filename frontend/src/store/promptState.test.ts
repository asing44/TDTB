/* U5 prompt-state store/controller tests: prompt-only saves never confirm the
   day, opt-in conflicts preserve the user's unsaved intent, and server-empty
   defaults leave export preferences explicitly unavailable. */

import { describe, expect, it } from "vitest";
import { makeHarness } from "../ui/test-harness";
import { Controller } from "./controller";
import { FixtureAdapter } from "../adapters/fixture";
import type { DaySetupSaveResult } from "../model/types";

const UNAVAILABLE_OPTIN_WARNING =
  "Prompt opt-ins could not be read; preferences are shown as unavailable.";

/** Mirrors a server echo where the opt-in store was unreadable: the block is
    omitted, so the projection must report it unavailable. */
class UnavailableOptinEchoAdapter extends FixtureAdapter {
  override async savePromptDrafts(): Promise<DaySetupSaveResult> {
    return {
      ok: true,
      daySetupConfirmed: false,
      optinsAvailable: false,
      optins: {},
      optinsRevision: 0,
      promptWarnings: [UNAVAILABLE_OPTIN_WARNING],
    };
  }
}

function withOptinMetadata(harness: ReturnType<typeof makeHarness>, revision = 3) {
  const { store } = harness;
  const inputs = {
    ...store.getState().inputs!,
    promptOptins: {
      optins: { intention: false, megan_nicety: true, stoic_intention: false },
      revision,
    },
  };
  store.dispatch({ type: "INPUTS_LOADED", inputs, ledger: { ...store.getState().ledger! } });
}

describe("U5 prompt state", () => {
  it("server empty defaults: no opt-in metadata => prefs unavailable, drafts empty", () => {
    const { store } = makeHarness("fresh");
    const s = store.getState();
    expect(s.promptOptins.available).toBe(false);
    expect(s.daySetup.captures).toEqual({ intention: "", forMeegy: "", stoic: "" });
  });

  it("plan-inputs opt-in metadata hydrates prefs as available", () => {
    const harness = makeHarness("fresh");
    withOptinMetadata(harness, 7);
    const s = harness.store.getState();
    expect(s.promptOptins.available).toBe(true);
    expect(s.promptOptins.optins.megan_nicety).toBe(true);
    expect(s.promptOptins.revision).toBe(7);
  });

  it("a prompt-only save never confirms the day", async () => {
    const { store, controller } = makeHarness("fresh");
    expect(store.getState().daySetup.confirmed).toBe(false);
    await controller.savePromptDrafts({ intention: "focus on the one thing" });
    const s = store.getState();
    expect(s.daySetup.confirmed).toBe(false);
    expect(s.daySetup.captures.intention).toBe("focus on the one thing");
    // The prompt-only echo carries the server's opt-in state.
    expect(s.promptOptins.available).toBe(true);
    expect(s.promptSave.phase).toBe("saved");
  });

  it("a successful draft save with an unavailable opt-in echo keeps prefs unavailable", async () => {
    const harness = makeHarness("fresh");
    const controller = new Controller(
      new UnavailableOptinEchoAdapter("fresh"),
      harness.store.dispatch,
      harness.store.getState,
    );
    expect(harness.store.getState().promptOptins.available).toBe(false);
    await controller.savePromptDrafts({ intention: "draft only" });
    const s = harness.store.getState();
    // The draft saved and the day stayed unconfirmed; prefs were never
    // hydrated as an all-false available map.
    expect(s.daySetup.confirmed).toBe(false);
    expect(s.daySetup.captures.intention).toBe("draft only");
    expect(s.promptOptins.available).toBe(false);
    expect(s.promptSave.phase).toBe("saved");
    expect(s.promptSave.warnings).toEqual([UNAVAILABLE_OPTIN_WARNING]);
  });

  it("an opt-in revision conflict keeps the last-known prefs (unsaved intent preserved)", async () => {
    const { store, controller } = makeHarness("fresh");
    // Seed a known revision, then make the fixture store stale relative to it.
    store.dispatch({
      type: "PROMPT_OPTINS_SAVED",
      optins: { intention: false, megan_nicety: false, stoic_intention: false },
      revision: 5,
    });
    await controller.savePromptOptins({ intention: false, megan_nicety: true, stoic_intention: false });
    const s = store.getState();
    expect(s.promptSave.conflict).toEqual({ expectedRevision: 5, currentRevision: 0 });
    // The server-known prefs are untouched; the user's unsaved choice lives in
    // the panel and is never silently overwritten.
    expect(s.promptOptins.optins.megan_nicety).toBe(false);
    expect(s.promptOptins.revision).toBe(5);
  });

  it("a successful opt-in save adopts the server echo + revision", async () => {
    const { store, controller } = makeHarness("fresh");
    await controller.savePromptOptins({ intention: false, megan_nicety: true, stoic_intention: true });
    const s = store.getState();
    expect(s.promptSave.conflict).toBeNull();
    expect(s.promptOptins.optins).toEqual({
      intention: false,
      megan_nicety: true,
      stoic_intention: true,
    });
    expect(s.promptOptins.revision).toBe(1);
  });
});
