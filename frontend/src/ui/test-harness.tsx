/* test-harness — shared scenario-staging helper for UI test files (same
   staging sequence components.test.tsx uses inline). Test-only module. */

import { render } from "@testing-library/preact";
import { Ctx } from "./context";
import { createStore, type Store } from "../store/createStore";
import { Controller } from "../store/controller";
import { FixtureAdapter, fixtureValidate } from "../adapters/fixture";
import {
  makeScenario,
  fixedInputsOf,
  type Scenario,
  type ScenarioName,
} from "../fixtures/scenarios";
import { fingerprintFixedInputs } from "../model/fingerprint";
import type { ComponentChildren } from "preact";

export interface Harness {
  store: Store;
  controller: Controller;
  scenario: Scenario;
  ui: (children: ComponentChildren) => ReturnType<typeof render>;
}

/** Stage a scenario; `mutate` edits the scenario before any dispatch (e.g.
    empty the assigned list, zero the ledger). */
export function makeHarness(
  name: ScenarioName,
  mutate?: (sc: Scenario) => void,
): Harness {
  const sc = makeScenario(name);
  mutate?.(sc);
  const store = createStore();
  store.dispatch({ type: "INPUTS_LOADED", inputs: sc.inputs, ledger: { ...sc.ledger } });
  if (sc.staged.daySetupConfirmed) {
    store.dispatch({
      type: "SETUP_SAVED",
      daySetup: { ...sc.inputs.daySetup, confirmed: true },
    });
  }
  if (sc.staged.sequence) {
    store.dispatch({
      type: "SEQUENCE_OK",
      sequence: sc.staged.sequence.map((r) => ({ ...r })),
      warnings: sc.proposal?.warnings ?? [],
      fingerprint: fingerprintFixedInputs(fixedInputsOf(sc.inputs)),
      anchoredSourceFingerprint: sc.inputs.anchoredSourceFingerprint,
      ledger: { ...sc.ledger },
    });
    store.dispatch({
      type: "VALIDATED",
      validation: fixtureValidate(sc.staged.sequence, sc.inputs),
    });
  }
  if (sc.staged.shadowCurrent) {
    store.dispatch({ type: "SHADOW_OK", shadow: structuredClone(sc.shadow) });
    store.dispatch({ type: "UI", patch: { approvalOpen: true } });
  }
  if (sc.staged.committed) {
    store.dispatch({ type: "COMMIT_DONE", report: structuredClone(sc.commitReport) });
  }
  const controller = new Controller(new FixtureAdapter(name), store.dispatch, store.getState);
  const ui = (children: ComponentChildren) =>
    render(<Ctx.Provider value={{ store, controller }}>{children}</Ctx.Provider>);
  return { store, controller, scenario: sc, ui };
}
