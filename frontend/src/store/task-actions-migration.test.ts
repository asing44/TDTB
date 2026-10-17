/* task-actions-migration.test.ts — task-actions F3b: the one-time persist
   migration from the retired browser-only "Exclude today" shape.

   Before task-actions, excluding a row for today was a same-date
   `included=false` override that only the browser knew about. It is now
   converted ONCE to a durable Skip today (`drop_from_plan`), and only when the
   adapter is live AND Day Setup is confirmed. Anything unconfirmed stays in
   the blob and is retried on the next load — never guessed, never lost.

   FixtureAdapter only. */

import { describe, expect, it, vi } from "vitest";
import { createStore } from "./createStore";
import { Controller } from "./controller";
import { attachSessionPersistence } from "./persist";
import { FixtureAdapter } from "../adapters/fixture";
import type { ScenarioName } from "../fixtures/scenarios";

const KEY = "tdtb-session-v2";
const VALID_DATE = "2026-07-18";

function memStorage(): Storage {
  const m = new Map<string, string>();
  return {
    getItem: (k: string) => m.get(k) ?? null,
    setItem: (k: string, v: string) => void m.set(k, v),
    removeItem: (k: string) => void m.delete(k),
    clear: () => m.clear(),
    key: () => null,
    get length() {
      return m.size;
    },
  } as Storage;
}

/** A same-date blob carrying the legacy browser-only exclusion. */
function legacyBlob(overrides: Record<string, unknown>): string {
  return JSON.stringify({
    version: 2,
    validDate: VALID_DATE,
    overrides,
    timeAdjustmentOptIns: {},
    mintOverlapOptIns: {},
    placements: {},
    sequence: null,
    fingerprint: null,
    anchoredSourceFingerprint: null,
    planningConfigFingerprint: null,
    overlapGrants: [],
    pinnedRows: [],
  });
}

async function load(scenario: ScenarioName) {
  const store = createStore();
  const adapter = new FixtureAdapter(scenario);
  const controller = new Controller(adapter, store.dispatch, store.getState);
  await controller.load();
  return { store, adapter, controller };
}

describe("legacy Exclude today -> durable Skip today", () => {
  it("does not convert without a confirmed Day Setup, and retries on the next load", async () => {
    const storage = memStorage();
    storage.setItem(KEY, legacyBlob({ Press: { included: false, blocks: null } }));

    // "fresh" loads an UNCONFIRMED Day Setup: the server would 409, so the
    // migration must not even try.
    const first = await load("fresh");
    const verb = vi.spyOn(first.adapter, "runtimeAction");
    attachSessionPersistence(first.store, first.controller, storage);
    await vi.waitFor(() => expect(first.store.getState().overrides.Press).toEqual({
      included: false,
      blocks: null,
    }));
    expect(verb).not.toHaveBeenCalled();
    expect(JSON.parse(storage.getItem(KEY)!).overrides.Press).toEqual({
      included: false,
      blocks: null,
    });

    // Next load, Day Setup confirmed: the same override converts once.
    const second = await load("ready");
    second.store.dispatch({
      type: "SETUP_SAVED",
      daySetup: { ...second.store.getState().daySetup, confirmed: true },
    });
    const secondVerb = vi.spyOn(second.adapter, "runtimeAction");
    attachSessionPersistence(second.store, second.controller, storage);

    await vi.waitFor(() =>
      expect(secondVerb).toHaveBeenCalledWith("drop_from_plan", "todoist:6fx00xPRESS"),
    );
    await vi.waitFor(() => expect(second.store.getState().overrides.Press).toBeUndefined());
    // The converted exclusion is cleared from the blob, so it cannot migrate
    // twice.
    await vi.waitFor(() =>
      expect(JSON.parse(storage.getItem(KEY)!).overrides.Press).toBeUndefined(),
    );

    // A third same-date load sees no legacy entry at all: no second verb.
    const third = await load("ready");
    third.store.dispatch({
      type: "SETUP_SAVED",
      daySetup: { ...third.store.getState().daySetup, confirmed: true },
    });
    const thirdVerb = vi.spyOn(third.adapter, "runtimeAction");
    attachSessionPersistence(third.store, third.controller, storage);
    await vi.waitFor(() => expect(third.store.getState().loaded).toBe(true));
    expect(thirdVerb).not.toHaveBeenCalled();
  });
});
