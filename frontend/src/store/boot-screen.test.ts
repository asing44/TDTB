/* boot-screen.test.ts — the app ALWAYS opens on the Set up day screen on a
   fresh page load. The starting step is an initial-state fact, not something
   INPUTS_LOADED or a same-date source refresh re-applies: a reload while the
   user is on Plan keeps Plan. Date rollover is a full reset and re-enters at
   the starting step. */

import { describe, expect, it } from "vitest";
import { initialState, reducer, type AppState } from "./store";
import { makeScenario, fixedInputsOf } from "../fixtures/scenarios";
import { fingerprintFixedInputs } from "../model/fingerprint";
import type { Ledger, PlanInputs } from "../model/types";

const ledger: Ledger = { today: "2026-07-18", spent: 0, cap: 4, remaining: 4 };
const AT = "2026-07-18T09:15:00.000Z";

function load(inputs: PlanInputs, from: AppState = initialState): AppState {
  return reducer(from, { type: "INPUTS_LOADED", inputs, ledger: { ...ledger } });
}

function refreshOk(s: AppState, inputs: PlanInputs): AppState {
  return reducer(s, {
    type: "SOURCE_REFRESH_OK",
    inputs,
    ledger: { ...ledger, spent: 1, remaining: 3 },
    fingerprint: fingerprintFixedInputs(fixedInputsOf(inputs)),
    anchoredSourceFingerprint: inputs.anchoredSourceFingerprint,
    planningConfigFingerprint: inputs.planningConfigFingerprint,
    at: AT,
  });
}

describe("start screen", () => {
  it("the initial store lands on the Set up day screen", () => {
    expect(initialState.ui.screen).toBe("setup");
  });

  it("the first inputs load keeps the Set up day screen", () => {
    const s = load(makeScenario("ready").inputs);
    expect(s.ui.screen).toBe("setup");
  });

  it("a saved (confirmed) day setup draft does not change the starting screen", () => {
    const inputs = makeScenario("ready").inputs;
    inputs.daySetup = { ...inputs.daySetup, confirmed: true };
    const s = load(inputs);
    expect(s.ui.screen).toBe("setup");
  });

  it("a same-date inputs reload keeps the user's current screen", () => {
    const inputs = makeScenario("ready").inputs;
    let s = load(inputs);
    s = reducer(s, { type: "UI", patch: { screen: "plan", settingsPanel: null } });
    s = load(inputs, s);
    expect(s.ui.screen).toBe("plan");
  });

  it("a same-date source refresh keeps the user's current screen", () => {
    const inputs = makeScenario("ready").inputs;
    let s = load(inputs);
    s = reducer(s, { type: "UI", patch: { screen: "plan", settingsPanel: null } });
    s = refreshOk(s, inputs);
    expect(s.ui.screen).toBe("plan");
  });

  it("a date rollover resets to the starting screen", () => {
    const first = makeScenario("ready").inputs;
    let s = load(first);
    s = reducer(s, { type: "UI", patch: { screen: "plan", settingsPanel: null } });
    const next = structuredClone(first);
    next.validDate = "2026-07-19";
    s = load(next, s);
    expect(s.validDate).not.toBe(first.validDate);
    expect(s.ui.screen).toBe("setup");
  });
});
