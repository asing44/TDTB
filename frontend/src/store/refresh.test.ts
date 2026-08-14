/* refresh.test.ts (store) — reducer + controller behavior for explicit
   source refresh (T13, locked decisions 20/21). Reducer cases build on the
   fixture scenarios like store.test.ts; controller cases drive the real
   FixtureAdapter, including its drift/failure simulators. */

import { describe, expect, it, vi } from "vitest";
import { initialState, reducer, type AppState } from "./store";
import { createStore } from "./createStore";
import { Controller } from "./controller";
import { FixtureAdapter } from "../adapters/fixture";
import { makeScenario, fixedInputsOf } from "../fixtures/scenarios";
import { fingerprintFixedInputs } from "../model/fingerprint";
import type { Ledger, PlanInputs } from "../model/types";
import { refreshSummaryText } from "../ui/ReadinessStrip";

const ledger: Ledger = { today: "2026-07-18", spent: 0, cap: 4, remaining: 4 };
const AT = "2026-07-18T09:15:00.000Z";

function sequenced(): AppState {
  const sc = makeScenario("ready");
  let s = reducer(initialState, {
    type: "INPUTS_LOADED",
    inputs: sc.inputs,
    ledger: { ...ledger },
  });
  s = reducer(s, { type: "SETUP_SAVED", daySetup: { ...sc.inputs.daySetup, confirmed: true } });
  s = reducer(s, {
    type: "SEQUENCE_OK",
    sequence: sc.proposal!.sequence,
    warnings: sc.proposal!.warnings,
    fingerprint: fingerprintFixedInputs(fixedInputsOf(sc.inputs)),
    anchoredSourceFingerprint: sc.inputs.anchoredSourceFingerprint,
    ledger: { ...ledger, spent: 1, remaining: 3 },
  });
  return s;
}

function refreshOk(
  s: AppState,
  inputs: PlanInputs,
  fp?: { fingerprint?: string; anchoredSourceFingerprint?: string },
): AppState {
  return reducer(s, {
    type: "SOURCE_REFRESH_OK",
    inputs,
    ledger: { ...ledger, spent: 1, remaining: 3 },
    fingerprint: fp?.fingerprint ?? fingerprintFixedInputs(fixedInputsOf(inputs)),
    anchoredSourceFingerprint:
      fp?.anchoredSourceFingerprint ?? inputs.anchoredSourceFingerprint,
    planningConfigFingerprint: inputs.planningConfigFingerprint,
    at: AT,
  });
}

describe("reducer: refresh lifecycle", () => {
  it("START sets loading; FAIL keeps the last good view and reports the error", () => {
    let s = sequenced();
    const before = s;
    s = reducer(s, { type: "SOURCE_REFRESH_START" });
    expect(s.refresh.phase).toBe("loading");
    s = reducer(s, { type: "SOURCE_REFRESH_FAIL", error: "calendar read degraded" });
    expect(s.refresh.phase).toBe("idle");
    expect(s.refresh.error).toBe("calendar read degraded");
    expect(s.inputs).toBe(before.inputs);
    expect(s.sequence).toBe(before.sequence);
    expect(s.seqPhase).toBe("valid");
  });

  it("no-change refresh records lastRefreshed and leaves staged phases alone", () => {
    let s = sequenced();
    s = refreshOk(s, makeScenario("ready").inputs);
    expect(s.refresh.lastRefreshed).toBe(AT);
    expect(s.refresh.summary).not.toBeNull();
    expect(s.seqPhase).toBe("valid");
    expect(s.driftNotice).toBeNull();
  });

  it("date rollover through refresh performs the full reset", () => {
    let s = sequenced();
    const rolled = makeScenario("fresh").inputs;
    rolled.validDate = "2026-07-19";
    s = refreshOk(s, rolled);
    expect(s.validDate).toBe("2026-07-19");
    expect(s.sequence).toBeNull();
    expect(s.overrides).toEqual({});
    expect(s.refresh.lastRefreshed).toBe(AT);
    expect(s.refresh.summary).toBeNull();
  });
});

describe("reducer: fixed-input drift invalidates staged approval", () => {
  it("planning-config drift keeps the last plan visible but marks it stale", () => {
    let s = sequenced();
    const next = structuredClone(makeScenario("ready").inputs);
    next.planningConfigFingerprint = "planning-v2";
    s = refreshOk(s, next);
    expect(s.sequence).not.toBeNull();
    expect(s.seqPhase).toBe("dirty");
    expect(s.driftNotice).toContain("invalidated");
    expect(s.planningConfigFingerprint).toBeNull();
  });
  it("effective fingerprint drift kills fingerprint/shadow/arm and raises the notice", () => {
    let s = sequenced();
    s = reducer(s, { type: "SHADOW_START" });
    s = reducer(s, { type: "SHADOW_OK", shadow: makeScenario("commit-preview").shadow });
    s = reducer(s, { type: "UI", patch: { approvalOpen: true } });
    s = refreshOk(s, makeScenario("ready").inputs, { fingerprint: "drifted" });
    expect(s.fingerprint).toBeNull();
    expect(s.anchoredSourceFingerprint).toBeNull();
    expect(s.shadow).toBeNull();
    expect(s.shadowPhase).toBe("none");
    expect(s.liveArmed).toBe(false);
    expect(s.seqPhase).toBe("dirty");
    expect(s.driftNotice).toContain("refresh invalidated");
    expect(s.ui.approvalOpen).toBe(false);
    expect(s.refresh.summary!.invalidated).toBe(true);
  });

  it("raw anchored-source drift alone also invalidates (locked decision 21)", () => {
    let s = sequenced();
    s = refreshOk(s, makeScenario("ready").inputs, {
      anchoredSourceFingerprint: "fixture-anchored-v1-drifted",
    });
    expect(s.fingerprint).toBeNull();
    expect(s.driftNotice).not.toBeNull();
    expect(s.refresh.summary!.invalidated).toBe(true);
  });

  it("a never-staged plan has no approval to invalidate", () => {
    const sc = makeScenario("ready");
    let s = reducer(initialState, { type: "INPUTS_LOADED", inputs: sc.inputs, ledger });
    s = refreshOk(s, sc.inputs, { fingerprint: "drifted" });
    expect(s.driftNotice).toBeNull();
    expect(s.refresh.summary!.invalidated).toBe(false);
  });
});

describe("reducer: assigned-only changes preserve placements, force revalidation", () => {
  it("removed row is pruned; surviving placements stay; plan re-enters dirty", () => {
    let s = sequenced();
    s = reducer(s, { type: "SHADOW_START" });
    s = reducer(s, { type: "SHADOW_OK", shadow: makeScenario("commit-preview").shadow });
    const next = makeScenario("ready").inputs;
    next.assigned = next.assigned.filter((i) => i.id !== "Press");
    s = refreshOk(s, next);
    expect(s.sequence!.some((r) => r.id === "Press")).toBe(false);
    expect(s.sequence!.some((r) => r.id === "Magic Mirror")).toBe(true);
    expect(s.seqPhase).toBe("dirty");
    expect(s.validation).toBeNull();
    expect(s.shadowPhase).toBe("stale");
    expect(s.liveArmed).toBe(false);
    expect(s.driftNotice).toBeNull();
    expect(s.refresh.summary!.removed).toEqual(["Press"]);
  });
});

describe("refresh summary text", () => {
  const empty = {
    added: [],
    removed: [],
    changed: [],
    overridesRetained: [],
    overridesDropped: [],
    invalidated: false,
  };
  it("clean refresh reads as no changes", () => {
    expect(refreshSummaryText(empty)).toBe("no changes");
  });
  it("fingerprint-only drift still reports the invalidation", () => {
    expect(refreshSummaryText({ ...empty, invalidated: true })).toBe(
      "staged plan invalidated",
    );
  });
  it("compact counts plus verbatim override names", () => {
    expect(
      refreshSummaryText({
        ...empty,
        added: ["A"],
        removed: ["B", "C"],
        overridesRetained: ["Gym"],
      }),
    ).toBe("1 added · 2 removed · override retained: Gym");
  });
});

describe("controller: refreshSources against the fixture adapter", () => {
  function harness() {
    const store = createStore();
    const adapter = new FixtureAdapter("ready");
    const controller = new Controller(adapter, store.dispatch, store.getState);
    return { store, adapter, controller };
  }

  it("happy path: idle → loading → idle with lastRefreshed, ledger untouched", async () => {
    const { store, controller } = harness();
    await controller.load();
    const spentBefore = store.getState().ledger!.spent;
    const p = controller.refreshSources();
    expect(store.getState().refresh.phase).toBe("loading");
    await p;
    const s = store.getState();
    expect(s.refresh.phase).toBe("idle");
    expect(s.refresh.error).toBeNull();
    expect(s.refresh.lastRefreshed).not.toBeNull();
    // No billed call: the fixture ledger only moves on autoSequence.
    expect(s.ledger!.spent).toBe(spentBefore);
  }, 15000);

  it("source failure: error surfaced, last good view intact", async () => {
    const { store, adapter, controller } = harness();
    await controller.load();
    const inputsBefore = store.getState().inputs;
    adapter.simulateSourceFailure();
    await controller.refreshSources();
    const s = store.getState();
    expect(s.refresh.error).toContain("source refresh failed");
    expect(s.inputs).toBe(inputsBefore);
  }, 15000);

  it("assigned drift: summary reports churn and staged plan goes dirty", async () => {
    const { store, adapter, controller } = harness();
    await controller.load();
    await controller.saveDaySetup({ ...store.getState().daySetup, confirmed: true });
    await controller.autoSequence();
    expect(store.getState().seqPhase).toBe("valid");
    adapter.simulateAssignedDrift();
    await controller.refreshSources();
    await vi.waitFor(() => {
      const s = store.getState();
      expect(s.refresh.summary).not.toBeNull();
    });
    const s = store.getState();
    expect(s.refresh.summary!.added).toEqual(["Review quarterly notes"]);
    expect(s.refresh.summary!.removed).toEqual(["Charge GoPro"]);
    expect(s.refresh.summary!.changed).toEqual(["Magic Mirror"]);
    expect(s.refresh.summary!.invalidated).toBe(false);
    expect(s.driftNotice).toBeNull();
    // free revalidation follow-up may still be in flight; phase is dirty or
    // already re-earned valid — never a stale "valid" without revalidation
    await vi.waitFor(() => {
      const st = store.getState();
      expect(st.seqPhase === "dirty" || st.validation !== null).toBe(true);
    });
  }, 15000);

  it("calendar drift through refresh invalidates the staged plan", async () => {
    const { store, adapter, controller } = harness();
    await controller.load();
    await controller.saveDaySetup({ ...store.getState().daySetup, confirmed: true });
    await controller.autoSequence();
    adapter.simulateDrift();
    await controller.refreshSources();
    const s = store.getState();
    expect(s.fingerprint).toBeNull();
    expect(s.driftNotice).not.toBeNull();
    expect(s.refresh.summary!.invalidated).toBe(true);
  }, 15000);

  it("refresh is refused mid-sequence/commit", async () => {
    const { store, controller } = harness();
    await controller.load();
    store.dispatch({ type: "SEQUENCE_START" });
    await controller.refreshSources();
    expect(store.getState().refresh.lastRefreshed).toBeNull();
  }, 15000);
});
