/* boot.ts — applies a scenario's pre-staged state to a fresh store after
   load, so the mockup can open directly in "sequenced", "commit-preview",
   or "verified" without replaying the interactions. Mockup-only helper —
   production always boots from the server's persisted state. */

import type { Scenario } from "./scenarios";
import { fixedInputsOf } from "./scenarios";
import { fingerprintFixedInputs } from "../model/fingerprint";
import { fixtureValidate } from "../adapters/fixture";
import type { Dispatch } from "../store/controller";

export function applyStagedState(scenario: Scenario, dispatch: Dispatch): void {
  const { staged, inputs } = scenario;
  if (staged.daySetupConfirmed) {
    dispatch({ type: "SETUP_SAVED", daySetup: { ...inputs.daySetup, confirmed: true } });
  }
  if (staged.sequence) {
    dispatch({
      type: "SEQUENCE_OK",
      sequence: staged.sequence.map((r) => ({ ...r })),
      warnings: scenario.proposal?.warnings ?? [],
      fingerprint: fingerprintFixedInputs(fixedInputsOf(inputs)),
      anchoredSourceFingerprint: inputs.anchoredSourceFingerprint,
      ledger: { ...scenario.ledger },
    });
    // Honest validation state, including conflict scenarios with hard errors.
    dispatch({ type: "VALIDATED", validation: fixtureValidate(staged.sequence, inputs) });
  }
  if (staged.shadowCurrent) {
    dispatch({ type: "SHADOW_OK", shadow: structuredClone(scenario.shadow) });
  }
  if (staged.committed) {
    dispatch({ type: "COMMIT_DONE", report: structuredClone(scenario.commitReport) });
  }
}
