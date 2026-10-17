/* task-actions-drafts.test.ts — task-actions F3b: staged row drafts at the
   store/controller layer.

   Fixture adapter only (stubbed where the draft slice never needs it). What's
   asserted is the FRONTEND contract:

   - opening a draft seeds it from the committed session, and "staged" is
     exactly `diff(draft, session)` being non-empty;
   - saving applies `toOverrides` through the existing controller operations
     and drops the draft; discarding changes nothing;
   - an all-day save persists the captured duration on TodayOverride, so
     returning to a time block restores the duration (never the old pin) even
     through a real persist blob round trip;
   - a protected Todoist native time refuses a pin until the operator grants
     source-time permission. */

import { describe, expect, it, vi } from "vitest";
import { createStore } from "./createStore";
import { Controller } from "./controller";
import { attachSessionPersistence } from "./persist";
import { clearPin, setMode, setPin } from "../model/rowDraft";
import { draftIsStaged, rowFactsOf, stagedDraft } from "./store";
import type { Adapter } from "../adapters/adapter";
import type { Capacity, PlanInputs } from "../model/types";

const VALID_DATE = "2026-07-18";
const LEDGER = { today: VALID_DATE, spent: 0, cap: 4, remaining: 4 };
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

const CAPACITY: Capacity = {
  total: 28,
  fixed: 0,
  anchored: 0,
  habits: 0,
  mint: 0,
  selected: 0,
  buffer: 0,
  free: 28,
  overassigned: false,
  availableForSelection: 28,
  remaining: "",
  ratio: "",
  legend: "",
  counters: "",
};

/** One todoist row, one Capacities row, one protected native-timed row and one
    Mint session, so every draft/batch eligibility branch is reachable. */
function inputs(): PlanInputs {
  return {
    validDate: VALID_DATE,
    assigned: [
      { id: "Deep Work", name: "Deep Work", path: null, source: "todoist", types: ["task"], urgency: null, deadline: null, priorityScore: 80, blocks: 3, durationLabel: "1hr 30min", todoistId: "t-deep" },
      { id: "Focus", name: "Focus", path: null, source: "todoist", types: ["task"], urgency: null, deadline: null, priorityScore: 70, blocks: 2, durationLabel: "1hr", todoistId: "t-focus", scheduledStart: "09:00" },
      { id: "Recurring", name: "Recurring", path: null, source: "todoist", types: ["task"], urgency: null, deadline: null, priorityScore: 60, blocks: 1, durationLabel: "30min", todoistId: "t-recurring", isRecurring: true },
      { id: "Mint Row", name: "Mint Row", path: null, source: "todoist", types: ["task"], urgency: null, deadline: null, priorityScore: 50, blocks: 1, durationLabel: "30min", todoistId: "t-mint" },
      { id: "Cap Row", name: "Cap Row", path: null, source: "capacities", types: ["interval"], urgency: null, deadline: null, priorityScore: 40, blocks: 1, durationLabel: "30min", todoistId: null, identity: "capacities:space:project:cap-row" },
      { id: "All Day Only", name: "All Day Only", path: null, source: "todoist", types: ["task"], urgency: null, deadline: null, priorityScore: 30, blocks: 0, durationLabel: "All day", todoistId: "t-allday" },
    ],
    unassignedCandidates: [],
    staleAssigned: [],
    droppedToday: [],
    anchored: [],
    anchoredSourceFingerprint: "raw-v1",
    habitsNote: null,
    time: { now: "08:00", anchor: "08:00", effectiveEod: "22:00", eodNote: null, configEod: "22:00", totalBlocks: 28 },
    capacity: CAPACITY,
    daySetup: { anchor: null, eod: null, buffering: "standard", anchored: {}, captures: { intention: "", forMeegy: "", stoic: "" }, confirmed: true },
    daySemantics: {
      availablePresets: [],
      selectedPreset: null,
      resolutionSource: "",
      enabledZones: [],
      effectiveAllotmentMinutes: 0,
      defaultAllotmentMinutes: 0,
      mintEnabled: false,
      warnings: [],
      errors: [],
      overlapPermissionsRaw: "",
      mintSessions: [{ id: "Mint Row", name: "Mint Row", slot: "1", start: "12:00", end: "12:30" }],
    },
    planningConfigFingerprint: "planning-v1",
    sourceWarnings: [],
    sourceCounts: { todoist: 5, calendar: 0 },
    sourceHealth: "ok",
    microAdventure: { pick: null, source: "auto", pool: [], streak: 0, pendingConfirm: null },
  };
}

function stubAdapter() {
  return {
    runtimeAction: vi.fn(async (verb: string, target: string) => ({
      id: "ra-1",
      verb,
      targetName: target,
      status: "applied",
      error: null,
      duplicate: false,
    })),
    applyRuntimeBatch: vi.fn(),
    retryRuntimeBatch: vi.fn(),
    undoRuntimeBatch: vi.fn(),
  } as unknown as Adapter;
}

function harness() {
  const store = createStore();
  store.dispatch({ type: "INPUTS_LOADED", inputs: inputs(), ledger: LEDGER });
  const controller = new Controller(stubAdapter(), store.dispatch, store.getState);
  // Session shaping re-earns capacity/validation through the server; those are
  // not this slice's subject and would need a full adapter.
  vi.spyOn(controller, "refreshCapacity").mockResolvedValue();
  vi.spyOn(controller, "revalidate").mockResolvedValue();
  return { store, controller };
}

describe("task-actions row drafts", () => {
  it("opens a draft from the committed session and treats a real difference as staged", () => {
    const { store, controller } = harness();
    controller.placeRow("Deep Work", "10:00");
    controller.openDraft("Deep Work");

    const seeded = store.getState().drafts["Deep Work"];
    expect(seeded).toMatchObject({
      mode: "time",
      blocks: 3,
      blocksBeforeAllDay: null,
      pin: "10:00",
      allowSourceTime: false,
      mintOverlap: false,
    });
    expect(draftIsStaged(store.getState(), "Deep Work")).toBe(false);

    controller.updateDraft("Deep Work", (draft) => setMode(draft, "allday"));
    expect(draftIsStaged(store.getState(), "Deep Work")).toBe(true);
    expect(stagedDraft(store.getState(), "Deep Work")?.diff.changes).toEqual([
      { field: "mode", from: "time", to: "allday" },
      { field: "blocks", from: 3, to: 0 },
      { field: "pin", from: "10:00", to: null },
    ]);
  });

  it("saves a time block: duration and pin land on the session, draft is dropped", () => {
    const { store, controller } = harness();
    controller.openDraft("Deep Work");
    controller.updateDraft("Deep Work", (draft) => setPin(draft, rowOf(store, "Deep Work"), "11:00"));
    controller.saveDraft("Deep Work");

    expect(store.getState().overrides["Deep Work"]).toEqual({ included: true, blocks: 3 });
    expect(store.getState().placements["Deep Work"]).toBe("11:00");
    expect(store.getState().drafts["Deep Work"]).toBeUndefined();
  });

  it("discards staged shaping without touching the session", () => {
    const { store, controller } = harness();
    controller.openDraft("Deep Work");
    controller.updateDraft("Deep Work", (draft) => setMode(draft, "allday"));
    controller.discardDraft("Deep Work");

    expect(store.getState().drafts["Deep Work"]).toBeUndefined();
    expect(store.getState().overrides["Deep Work"]).toBeUndefined();
    expect(store.getState().placements["Deep Work"]).toBeUndefined();
  });

  it("all-day save captures the duration and clears the pin; time block restores the duration only", () => {
    const { store, controller } = harness();
    controller.placeRow("Deep Work", "10:00");
    controller.openDraft("Deep Work");
    controller.updateDraft("Deep Work", (draft) => setMode(draft, "allday"));
    controller.saveDraft("Deep Work");

    expect(store.getState().overrides["Deep Work"]).toEqual({
      included: true,
      blocks: 0,
      blocksBeforeAllDay: 3,
    });
    expect(store.getState().placements["Deep Work"]).toBeUndefined();

    controller.openDraft("Deep Work");
    controller.updateDraft("Deep Work", (draft) => setMode(draft, "time"));
    const restored = store.getState().drafts["Deep Work"];
    expect(restored.mode).toBe("time");
    expect(restored.blocks).toBe(3);
    expect(restored.pin).toBeNull();
  });

  it("restores the captured duration through a real persist blob round trip", () => {
    const storage = memStorage();
    const first = harness();
    const firstStore = first.store;
    attachSessionPersistence(firstStore, first.controller, storage);
    first.controller.placeRow("Deep Work", "10:00");
    first.controller.openDraft("Deep Work");
    first.controller.updateDraft("Deep Work", (draft) => setMode(draft, "allday"));
    first.controller.saveDraft("Deep Work");
    expect(JSON.parse(storage.getItem(KEY)!).overrides["Deep Work"]).toEqual({
      included: true,
      blocks: 0,
      blocksBeforeAllDay: 3,
    });

    // A reload: same date, same inputs, a brand-new store/controller.
    const second = harness();
    attachSessionPersistence(second.store, second.controller, storage);
    expect(second.store.getState().overrides["Deep Work"]?.blocksBeforeAllDay).toBe(3);

    second.controller.openDraft("Deep Work");
    expect(second.store.getState().drafts["Deep Work"].mode).toBe("allday");
    second.controller.updateDraft("Deep Work", (draft) => setMode(draft, "time"));
    second.controller.saveDraft("Deep Work");

    expect(second.store.getState().overrides["Deep Work"]).toEqual({
      included: true,
      blocks: 3,
    });
    expect(second.store.getState().placements["Deep Work"]).toBeUndefined();
  });

  it("refuses a pin on a protected native time until source time is allowed", () => {
    const { store, controller } = harness();
    controller.openDraft("Focus");
    controller.updateDraft("Focus", (draft) => setPin(draft, rowOf(store, "Focus"), "11:00"));

    expect(store.getState().drafts.Focus.pin).toBeNull();
    expect(draftIsStaged(store.getState(), "Focus")).toBe(false);

    controller.updateDraft("Focus", (draft) => ({ ...draft, allowSourceTime: true }));
    controller.updateDraft("Focus", (draft) => setPin(draft, rowOf(store, "Focus"), "11:00"));
    expect(store.getState().drafts.Focus.pin).toBe("11:00");

    controller.updateDraft("Focus", (draft) => clearPin(draft));
    expect(store.getState().drafts.Focus.pin).toBeNull();
  });
});

/** The live row facts of one fixture row — the same helper the UI calls. */
function rowOf(store: ReturnType<typeof createStore>, id: string) {
  return rowFactsOf(store.getState(), id)!;
}
