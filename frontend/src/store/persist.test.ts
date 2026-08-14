/* persist.test.ts — today-only session persistence (locked decision 16):
   same-date restore, stale-date death, shadow never restored, saves on
   change. */

import { beforeEach, describe, expect, it, vi } from "vitest";
import { createStore } from "./createStore";
import { attachSessionPersistence } from "./persist";
import type { Controller } from "./controller";
import type { PlanInputs } from "../model/types";

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

function inputs(validDate: string): PlanInputs {
  return {
    validDate,
    assigned: [
      { id: "A", name: "A", path: "p/A.md", source: "vault", types: [], urgency: null, deadline: null, priorityScore: 1, blocks: 2, durationLabel: "1hr", todoistId: null },
    ],
    unassignedCandidates: [],
    staleAssigned: [],
    droppedToday: [],
    anchored: [],
    anchoredSourceFingerprint: "raw-v1",
    habitsNote: null,
    time: { now: "08:00", anchor: "08:00", effectiveEod: "22:00", eodNote: null, configEod: "22:00", totalBlocks: 28 },
    capacity: { total: 28, fixed: 0, anchored: 0, habits: 0, mint: 0, selected: 0, buffer: 0, free: 28, overassigned: false, availableForSelection: 28, remaining: "", ratio: "", legend: "", counters: "" },
    daySetup: { anchor: null, eod: null, buffering: "standard", anchored: {}, captures: { intention: "", forMeegy: "", stoic: "" }, confirmed: false },
    daySemantics: { availablePresets: [], selectedPreset: null, resolutionSource: "", enabledZones: [], effectiveAllotmentMinutes: 0, defaultAllotmentMinutes: 0, mintEnabled: false, warnings: [], errors: [], overlapPermissionsRaw: "" },
    planningConfigFingerprint: "planning-v1",
    sourceWarnings: [],
    sourceCounts: { vault: 1, todoist: 0, calendar: 0 },
    sourceHealth: "ok",
    microAdventure: { pick: null, source: "auto", pool: [], streak: 0, pendingConfirm: null },
  };
}

const LEDGER = { today: "2026-07-18", spent: 0, cap: 4, remaining: 4 };

function fakeController() {
  return {
    revalidate: vi.fn(async () => {}),
    refreshCapacity: vi.fn(async () => {}),
  } as unknown as Controller;
}

describe("attachSessionPersistence", () => {
  let storage: Storage;

  beforeEach(() => {
    storage = memStorage();
  });

  it("persists overrides/placements/sequence on state changes", () => {
    const store = createStore();
    store.dispatch({ type: "INPUTS_LOADED", inputs: inputs("2026-07-18"), ledger: LEDGER });
    attachSessionPersistence(store, fakeController(), storage);
    store.dispatch({ type: "OVERRIDE_SET", id: "A", override: { included: true, blocks: 3 } });
    const blob = JSON.parse(storage.getItem(KEY)!);
    expect(blob.validDate).toBe("2026-07-18");
    expect(blob.overrides.A).toEqual({ included: true, blocks: 3 });
    expect(blob.version).toBe(2);
  });

  it("discards incompatible v1 state instead of migrating it", () => {
    storage.setItem("tdtb-session-v1", JSON.stringify({ validDate: "2026-07-18", sequence: [{ id: "A" }] }));
    const store = createStore();
    store.dispatch({ type: "INPUTS_LOADED", inputs: inputs("2026-07-18"), ledger: LEDGER });
    const ctl = fakeController();
    attachSessionPersistence(store, ctl, storage);
    expect(storage.getItem("tdtb-session-v1")).toBeNull();
    expect(store.getState().sequence).toBeNull();
  });

  it("discards an unversioned blob under the v2 key", () => {
    storage.setItem(KEY, JSON.stringify({ validDate: "2026-07-18", sequence: [{ id: "A" }] }));
    const store = createStore();
    store.dispatch({ type: "INPUTS_LOADED", inputs: inputs("2026-07-18"), ledger: LEDGER });
    attachSessionPersistence(store, fakeController(), storage);
    expect(storage.getItem(KEY)).toBeNull();
    expect(store.getState().sequence).toBeNull();
  });

  it("restores same-date state and triggers revalidate + capacity refresh", () => {
    storage.setItem(KEY, JSON.stringify({
      version: 2,
      validDate: "2026-07-18",
      overrides: { A: { included: true, blocks: 3 } },
      placements: { A: "10:00" },
      sequence: [{ id: "A", start: "10:00", end: "11:30", zone: null, kind: "work" }],
      fingerprint: "abc123",
      anchoredSourceFingerprint: "raw-v1",
      planningConfigFingerprint: "planning-v1",
      overlapGrants: [{
        primaryId: "A", companionId: "Wall",
        primaryInterval: { start: "10:00", end: "11:30" },
        companionInterval: { start: "10:30", end: "11:00" },
        reason: "paired", planningConfigFingerprint: "planning-v1",
      }],
      pinnedRows: [{ id: "A", start: "10:00", end: "11:30", zone: null, kind: "work", wire: { id: "A", start: "10:00", end: "11:30", zone: null } }],
      pendingPinnedRows: [{ id: "A", start: "10:15", end: "11:45", zone: null, kind: "work", wire: { id: "A", start: "10:15", end: "11:45", zone: null } }],
    }));
    const store = createStore();
    store.dispatch({ type: "INPUTS_LOADED", inputs: inputs("2026-07-18"), ledger: LEDGER });
    const ctl = fakeController();
    attachSessionPersistence(store, ctl, storage);
    const s = store.getState();
    expect(s.overrides.A.blocks).toBe(3);
    expect(s.sequence?.length).toBe(1);
    expect(s.fingerprint).toBe("abc123");
    expect(s.anchoredSourceFingerprint).toBe("raw-v1");
    expect(s.planningConfigFingerprint).toBe("planning-v1");
    expect(s.overlapGrants[0].reason).toBe("paired");
    expect(s.pinnedRows[0].wire).toEqual({ id: "A", start: "10:00", end: "11:30", zone: null });
    expect(s.pendingPinnedRows[0].start).toBe("10:15");
    expect(s.seqPhase).toBe("dirty"); // must re-earn valid via the server validator
    expect(s.shadowPhase).toBe("none"); // shadow NEVER restores
    expect(s.liveArmed).toBe(false);
    expect((ctl.revalidate as any).mock.calls.length).toBe(1);
    expect((ctl.refreshCapacity as any).mock.calls.length).toBe(1);
  });

  it("date rollover: stale blob is dropped, nothing restores", () => {
    storage.setItem(KEY, JSON.stringify({
      version: 2,
      validDate: "2026-07-17",
      overrides: { A: { included: false, blocks: null } },
      placements: {},
      sequence: null,
      fingerprint: null,
      anchoredSourceFingerprint: null,
    }));
    const store = createStore();
    store.dispatch({ type: "INPUTS_LOADED", inputs: inputs("2026-07-18"), ledger: LEDGER });
    const ctl = fakeController();
    attachSessionPersistence(store, ctl, storage);
    expect(store.getState().overrides).toEqual({});
    expect((ctl.revalidate as any).mock.calls.length).toBe(0);
    // stale blob replaced by today's on next change; removed immediately:
    const raw = storage.getItem(KEY);
    expect(raw === null || JSON.parse(raw).validDate === "2026-07-18").toBe(true);
  });

  it("empty blob restores nothing", () => {
    storage.setItem(KEY, JSON.stringify({
      version: 2,
      validDate: "2026-07-18", overrides: {}, placements: {}, sequence: null, fingerprint: null,
      anchoredSourceFingerprint: null,
    }));
    const store = createStore();
    store.dispatch({ type: "INPUTS_LOADED", inputs: inputs("2026-07-18"), ledger: LEDGER });
    const ctl = fakeController();
    attachSessionPersistence(store, ctl, storage);
    expect((ctl.revalidate as any).mock.calls.length).toBe(0);
  });
});
