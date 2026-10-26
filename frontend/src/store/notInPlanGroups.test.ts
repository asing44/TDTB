/* notInPlanGroups.test.ts — U5 at the store seam: where an unassigned row's
   state actually lives (`origin`), and the day's first-load baseline that makes
   a mid-day arrival distinguishable from a row that was already waiting.

   Reducer-level only: no adapter, no source write. */

import { describe, expect, it } from "vitest";
import { createStore } from "./createStore";
import { attachSessionPersistence } from "./persist";
import { assignmentRows, initialState, reducer, type AppState } from "./store";
import type { AssignedItem, PlanInputs } from "../model/types";

const DATE = "2026-07-18";
const LEDGER = { today: DATE, spent: 0, cap: 4, remaining: 4 };

/** One Todoist row with a stable identity. A `null` deadline cannot satisfy
    the U3a rule, so the row defaults OUT of the plan; a `DATE` deadline holds
    it in by itself. */
function item(id: string, deadline: string | null): AssignedItem {
  return {
    id,
    name: id,
    path: null,
    source: "todoist",
    types: ["task"],
    urgency: null,
    deadline,
    priorityScore: 1,
    blocks: 1,
    durationLabel: "30min",
    todoistId: `x${id}`,
    identity: `todoist:x${id}`,
  };
}

function inputs(assigned: AssignedItem[]): PlanInputs {
  return {
    validDate: DATE,
    assigned,
    unassignedCandidates: [],
    staleAssigned: [],
    droppedToday: [],
    anchored: [],
    anchoredSourceFingerprint: "raw-v1",
    habitsNote: null,
    time: {
      now: "08:00",
      anchor: "08:00",
      effectiveEod: "22:00",
      eodNote: null,
      configEod: "22:00",
      totalBlocks: 28,
    },
    capacity: {
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
    },
    daySetup: {
      anchor: null,
      eod: null,
      buffering: "standard",
      anchored: {},
      captures: { intention: "", forMeegy: "", stoic: "" },
      confirmed: false,
    },
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
    },
    planningConfigFingerprint: "planning-v1",
    sourceWarnings: [],
    sourceCounts: { todoist: 0, calendar: 0 },
    sourceHealth: "ok",
    microAdventure: {
      pick: null,
      source: "auto",
      pool: [],
      streak: 0,
      pendingConfirm: null,
    },
  };
}

function loaded(assigned: AssignedItem[]): AppState {
  return reducer(initialState, {
    type: "INPUTS_LOADED",
    inputs: inputs(assigned),
    ledger: LEDGER,
  });
}

function refresh(s: AppState, next: PlanInputs): AppState {
  return reducer(s, {
    type: "SOURCE_REFRESH_OK",
    inputs: next,
    ledger: LEDGER,
    fingerprint: "fp-next",
    anchoredSourceFingerprint: next.anchoredSourceFingerprint,
    at: "2026-07-18T10:00:00Z",
  });
}

describe("U5: the day's first-load baseline", () => {
  it("records the queue identities at the FIRST load and never moves them", () => {
    const first = loaded([item("A", null), item("C", DATE)]);
    expect(first.dayStartIdentities).toEqual(["todoist:xA", "todoist:xC"]);

    // A refresh that brings a new row must not move the baseline: otherwise
    // every row would read as "old" again after every source read.
    const after = refresh(first, inputs([item("A", null), item("B", null), item("C", DATE)]));
    expect(after.dayStartIdentities).toEqual(["todoist:xA", "todoist:xC"]);
  });

  it("badges only a row that arrived after the first load and defaulted out", () => {
    const first = loaded([item("A", null), item("C", DATE)]);
    const after = refresh(first, inputs([item("A", null), item("B", null), item("C", DATE)]));
    const rows = assignmentRows(after).unassigned;

    // A was waiting at load: it defaults out, but it is not new.
    expect(rows.find((r) => r.identity === "todoist:xA")).toMatchObject({
      origin: "local",
      isNewArrival: false,
    });
    // B arrived with the refresh: defaulted out AND new.
    expect(rows.find((r) => r.identity === "todoist:xB")).toMatchObject({
      origin: "local",
      isNewArrival: true,
    });
  });

  it("never badges a row whose plan membership was decided by hand", () => {
    // A day whose default holds rows IN: taking an arriving row OUT is then a
    // real edit the sparse map keeps, so the badge distinguishes it from a
    // row that merely defaulted out.
    let s = loaded([item("C", DATE)]);
    s = reducer(s, {
      type: "SESSION_RESTORED",
      overrides: {},
      placements: {},
      sequence: null,
      fingerprint: null,
      anchoredSourceFingerprint: null,
      planMembership: {},
      membershipDefault: "in",
    });
    s = refresh(s, inputs([item("B", null), item("C", DATE)]));
    expect(assignmentRows(s).unassigned).toEqual([]);

    s = reducer(s, { type: "PLAN_MEMBERSHIP_SET", identity: "todoist:xB", in: false });
    expect(s.planMembership).toEqual({ "todoist:xB": false });
    expect(assignmentRows(s).unassigned.map((r) => [r.identity, r.isNewArrival])).toEqual([
      ["todoist:xB", false],
    ]);
  });
});

describe("U5: where an unassigned row's state lives", () => {
  it("tags local, journal-unassign and dropped rows by origin", () => {
    let s = loaded([item("C", DATE)]);
    const next: PlanInputs = {
      ...inputs([item("C", DATE)]),
      unassignedToday: [
        {
          identity: "todoist:xJ",
          name: "J",
          actionId: "ra-1",
          source: "todoist",
          recurring: false,
          minutes: 30,
        },
      ],
      droppedToday: [
        {
          identity: "capacities:space:custom-project:d",
          name: "D",
          droppedAt: "2026-07-18T09:00:00Z",
          source: "capacities",
          minutes: 30,
        },
      ],
    };
    s = refresh(s, next);
    // A local edit on a row that is in by default is kept, so it is the local
    // group's only member here.
    s = reducer(s, { type: "PLAN_MEMBERSHIP_SET", identity: "todoist:xC", in: false });
    const rows = assignmentRows(s).unassigned;

    expect(rows.map((r) => [r.identity, r.origin])).toEqual([
      ["todoist:xC", "local"],
      ["todoist:xJ", "journal-unassign"],
      ["capacities:space:custom-project:d", "dropped"],
    ]);
    // Only the local group can be a mid-day arrival — and here C was already
    // waiting at the first load, so nothing is new.
    expect(rows.filter((r) => r.isNewArrival)).toEqual([]);
  });
});

// -- persistence of the baseline (additive, back-compatible) -----------------

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

function fakeController() {
  return {
    revalidate: async () => {},
    refreshCapacity: async () => {},
  } as unknown as Parameters<typeof attachSessionPersistence>[1];
}

describe("U5: the baseline across a same-date reload", () => {
  it("restores the persisted baseline instead of re-recording the reload", () => {
    const storage = memStorage();
    const day = createStore();
    day.dispatch({
      type: "INPUTS_LOADED",
      inputs: inputs([item("C", DATE)]),
      ledger: LEDGER,
    });
    // Content, so the blob is worth restoring at all — written by the
    // subscription, which only starts once persistence is attached.
    const detach = attachSessionPersistence(day, fakeController(), storage);
    day.dispatch({ type: "PLAN_MEMBERSHIP_SET", identity: "todoist:xC", in: false });
    detach();

    // The reload already sees a row that arrived later in the day.
    const reloaded = createStore();
    reloaded.dispatch({
      type: "INPUTS_LOADED",
      inputs: inputs([item("B", null), item("C", DATE)]),
      ledger: LEDGER,
    });
    expect(reloaded.getState().dayStartIdentities).toEqual([
      "todoist:xB",
      "todoist:xC",
    ]);
    const stop = attachSessionPersistence(reloaded, fakeController(), storage);

    expect(reloaded.getState().dayStartIdentities).toEqual(["todoist:xC"]);
    const rows = assignmentRows(reloaded.getState()).unassigned;
    expect(rows.find((r) => r.identity === "todoist:xB")?.isNewArrival).toBe(true);
    stop();
  });

  it("falls back to the page's own baseline when a pre-U5 blob has none", () => {
    const storage = memStorage();
    storage.setItem(
      "tdtb-session-v2",
      JSON.stringify({
        version: 2,
        validDate: DATE,
        overrides: {},
        placements: {},
        sequence: null,
        fingerprint: null,
        anchoredSourceFingerprint: null,
        planningConfigFingerprint: null,
        overlapGrants: [],
        pinnedRows: [],
        planMembership: { "todoist:xC": false },
      }),
    );
    const store = createStore();
    store.dispatch({
      type: "INPUTS_LOADED",
      inputs: inputs([item("B", null), item("C", DATE)]),
      ledger: LEDGER,
    });
    const stop = attachSessionPersistence(store, fakeController(), storage);
    expect(store.getState().dayStartIdentities).toEqual([
      "todoist:xB",
      "todoist:xC",
    ]);
    stop();
  });
});
