/* task-actions-migration.test.ts — regression guard for the RETIRED
   browser-only exclusion migration.

   Before task-actions, excluding a row for today was a same-date
   `included=false` override. A one-time pass used to convert those overrides
   into durable Skip today (`drop_from_plan`) on the next same-date restore.
   That inference is gone, because the identical persisted shape is also
   written as a local settle marker after a staged Complete / Unassign /
   Delete — so an old blob cannot prove which intent produced it. Guessing
   spent a real server write and could re-skip a row the user settled.

   Contract asserted here:
   - a restored `included=false` stays a LOCAL today-only exclusion forever;
     loading and repeated reloads never issue a runtime verb or any other
     server write on its behalf;
   - date rollover still discards the blob wholesale;
   - an EXPLICIT Skip today still journals `drop_from_plan`, and only once Day
     Setup is confirmed (the server's 409 gate is never reached);
   - all-day duration persistence is untouched by the removal.

   FixtureAdapter only. Server-side skip semantics are backend-tested. */

import { describe, expect, it, vi } from "vitest";
import { createStore } from "./createStore";
import { Controller } from "./controller";
import { attachSessionPersistence } from "./persist";
import { FixtureAdapter } from "../adapters/fixture";
import { DAY_SETUP_GATE_REASON } from "./store";
import type { ScenarioName } from "../fixtures/scenarios";

const KEY = "tdtb-session-v2";

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

/** A session blob for one valid_date, shaped exactly like persist.ts writes. */
function blob(validDate: string | null, overrides: Record<string, unknown>): string {
  return JSON.stringify({
    version: 2,
    validDate,
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

function storedOverrides(storage: Storage): Record<string, unknown> {
  const raw = storage.getItem(KEY);
  return raw ? (JSON.parse(raw).overrides ?? {}) : {};
}

/** FixtureAdapter "ready" = Setup confirmed; "fresh" = unconfirmed. */
async function load(
  scenario: ScenarioName = "ready",
  opts: { confirm?: boolean } = {},
) {
  const confirm = opts.confirm !== false;
  const store = createStore();
  const adapter = new FixtureAdapter(scenario);
  const controller = new Controller(adapter, store.dispatch, store.getState);
  await controller.load();
  if (confirm) {
    store.dispatch({
      type: "SETUP_SAVED",
      daySetup: { ...store.getState().daySetup, confirmed: true },
    });
  }
  return { store, adapter, controller, detach: () => {} };
}

/** Every adapter method that mutates server state, plus the explicit source
    refresh the retired migration used to fire. A reload must touch none. */
function spyServerWrites(adapter: FixtureAdapter) {
  return {
    runtimeAction: vi.spyOn(adapter, "runtimeAction"),
    refreshSources: vi.spyOn(adapter, "refreshSources"),
    undoRuntimeAction: vi.spyOn(adapter, "undoRuntimeAction"),
    applyRuntimeBatch: vi.spyOn(adapter, "applyRuntimeBatch"),
    retryRuntimeBatch: vi.spyOn(adapter, "retryRuntimeBatch"),
    undoRuntimeBatch: vi.spyOn(adapter, "undoRuntimeBatch"),
    shadowCommit: vi.spyOn(adapter, "shadowCommit"),
    liveCommit: vi.spyOn(adapter, "liveCommit"),
    saveDaySetup: vi.spyOn(adapter, "saveDaySetup"),
    saveDurationMemory: vi.spyOn(adapter, "saveDurationMemory"),
    saveAnchoredStart: vi.spyOn(adapter, "saveAnchoredStart"),
  };
}

function expectNoServerWrites(
  spies: ReturnType<typeof spyServerWrites>,
  why: string,
) {
  for (const [name, spy] of Object.entries(spies)) {
    expect(spy, `${why}: ${name} must not be called`).toHaveBeenCalledTimes(0);
  }
}

/** Let any fire-and-forget async work settle before asserting "not called". */
const settle = () => new Promise((resolve) => setTimeout(resolve, 80));

describe("a persisted included=false is never inferred as a durable Skip", () => {
  it("restores the same-date exclusion locally and writes nothing, on every reload", async () => {
    const storage = memStorage();
    const first = await load();
    const validDate = first.store.getState().validDate;
    storage.setItem(KEY, blob(validDate, { Press: { included: false, blocks: null } }));

    for (let reload = 1; reload <= 3; reload += 1) {
      const ctx = await load();
      const writes = spyServerWrites(ctx.adapter);
      const detach = attachSessionPersistence(ctx.store, ctx.controller, storage);

      await vi.waitFor(() =>
        expect(ctx.store.getState().overrides.Press).toEqual({
          included: false,
          blocks: null,
        }),
      );
      await settle();

      // The recovery proof: the row is still excluded locally, yet no verb,
      // refresh, or commit was ever sent. Asserting the calls (not just
      // omitting an await) is what catches a reintroduced fire-and-forget.
      expectNoServerWrites(writes, `reload ${reload}`);
      expect(ctx.store.getState().overrides.Press?.included).toBe(false);
      // Still serialized, so the exclusion survives another reload unchanged.
      expect(storedOverrides(storage).Press).toEqual({ included: false, blocks: null });
      detach();
    }
  });

  it("leaves the local exclusion alone after a staged Complete / Unassign / Delete settle marker", async () => {
    for (const verb of ["done", "unassign", "delete"] as const) {
      const storage = memStorage();
      // Settle the row for today the ordinary way: the local marker is the
      // same `included=false` shape an old Exclude-today blob carries.
      const settled = await load("verified");
      const detachWriting = attachSessionPersistence(
        settled.store,
        settled.controller,
        storage,
      );
      await settled.controller.stagingAction(verb, "Press");
      expect(settled.store.getState().overrides.Press).toEqual({
        included: false,
        blocks: null,
      });
      detachWriting();
      expect(storedOverrides(storage).Press).toEqual({ included: false, blocks: null });
      expect(settled.store.getState().validDate).toBeDefined();

      const ctx = await load("ready");
      const writes = spyServerWrites(ctx.adapter);
      const detach = attachSessionPersistence(ctx.store, ctx.controller, storage);
      await vi.waitFor(() =>
        expect(ctx.store.getState().overrides.Press?.included).toBe(false),
      );
      await settle();
      expectNoServerWrites(writes, `reload after staged ${verb}`);
      expect(storedOverrides(storage).Press).toEqual({ included: false, blocks: null });
      detach();
    }
  });
});

describe("explicit Skip today stays durable", () => {
  it("journals drop_from_plan for an explicit skip, with no local exclusion invented", async () => {
    const { store, adapter, controller } = await load();
    const verb = vi.spyOn(adapter, "runtimeAction");

    await controller.skipToday("Press");

    expect(verb).toHaveBeenCalledWith("drop_from_plan", "todoist:6fx00xPRESS");
    expect(store.getState().runtimeError).toBeNull();
    expect(store.getState().overrides.Press).toBeUndefined();
  });

  it("still honors the Day Setup gate, even with a persisted exclusion attached", async () => {
    const storage = memStorage();
    const probe = await load();
    storage.setItem(
      KEY,
      blob(probe.store.getState().validDate, { Press: { included: false, blocks: null } }),
    );

    const { store, adapter, controller } = await load("fresh", { confirm: false });
    attachSessionPersistence(store, controller, storage);
    const writes = spyServerWrites(adapter);

    await vi.waitFor(() => expect(store.getState().overrides.Press?.included).toBe(false));
    await controller.skipToday("Press");
    await settle();

    expectNoServerWrites(writes, "unconfirmed Day Setup");
    expect(store.getState().runtimeError).toBe(DAY_SETUP_GATE_REASON);
    // The persisted exclusion is untouched by the refused skip.
    expect(store.getState().overrides.Press?.included).toBe(false);
    expect(storedOverrides(storage).Press).toEqual({ included: false, blocks: null });
  });
});

describe("date rollover and duration persistence are unchanged", () => {
  it("drops a prior-date blob wholesale without restoring or writing", async () => {
    const storage = memStorage();
    storage.setItem(KEY, blob("1999-01-01", { Press: { included: false, blocks: null } }));

    const ctx = await load();
    const writes = spyServerWrites(ctx.adapter);
    attachSessionPersistence(ctx.store, ctx.controller, storage);
    await settle();

    expect(ctx.store.getState().overrides.Press).toBeUndefined();
    expect(storage.getItem(KEY)).toBeNull();
    expectNoServerWrites(writes, "stale-date blob");
  });

  it("keeps an all-day duration override across a reload", async () => {
    const storage = memStorage();
    const writing = await load();
    const detachWriting = attachSessionPersistence(
      writing.store,
      writing.controller,
      storage,
    );
    writing.controller.setOverride("Press", true, 0, { blocksBeforeAllDay: 3 });
    detachWriting();
    expect(storedOverrides(storage).Press).toEqual({
      included: true,
      blocks: 0,
      blocksBeforeAllDay: 3,
    });

    const ctx = await load();
    const writes = spyServerWrites(ctx.adapter);
    attachSessionPersistence(ctx.store, ctx.controller, storage);
    await vi.waitFor(() =>
      expect(ctx.store.getState().overrides.Press).toEqual({
        included: true,
        blocks: 0,
        blocksBeforeAllDay: 3,
      }),
    );
    await settle();
    expectNoServerWrites(writes, "duration reload");
    expect(storedOverrides(storage).Press).toEqual({
      included: true,
      blocks: 0,
      blocksBeforeAllDay: 3,
    });
  });
});
