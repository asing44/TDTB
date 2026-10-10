/* Footer — U7 page frame (KTD11). The ActionDock, FooterBanners, and the
   readiness/source-refresh affordances are composed into one footer surface:
   a left "← Day setup" back link on Plan tasks plus the blocked reason drawn
   from the existing readiness logic, and the existing dock controls (primary
   action, billed-call note, approval flow) on the right.

   No copy-prompt control exists anywhere (R20b, retired in U5). Every
   existing safety gate is preserved: the dock still disables its billed
   actions for degraded sources, spent budget, unconfirmed setup, and
   unresolved defects, and the approval drawer still owns live commit. */

import { useApp, useAppState } from "./context";
import { ActionDock } from "./ActionDock";
import { FooterBanners } from "./FooterBanners";
import { requestSetupContinue, requestSetupUndo } from "./day-setup/commands";
import { display12h } from "../model/time";
import {
  acceptableDefects,
  defectsResolved,
  dockState,
  type AppState,
} from "../store/store";

/** The one blocked reason the footer states, in the operator's words. Null
    when the plan is sequenceable — the primary action is then available. */
export function footerBlockedReason(s: AppState): string | null {
  if (!s.inputs) return null;
  if (s.inputs.sourceHealth !== "ok") return "Can't sequence until sources are healthy.";
  const state = dockState(s);
  if (state === "setup") return "Confirm day setup before sequencing.";
  if (state === "fix") {
    const n = s.validation?.hardErrors.length ?? 0;
    return `${n} blocking issue${n === 1 ? "" : "s"} — fix rows or resequence.`;
  }
  if (state === "review" && !defectsResolved(s)) {
    const n = acceptableDefects(s).length;
    return `${n} acceptable defect${n === 1 ? "" : "s"} — fix or accept as-is.`;
  }
  if (state === "budget-manual") return "Billed budget spent — manual layout stays available.";
  return null;
}

export function Footer() {
  const s = useAppState();
  const { store } = useApp();
  const onPlan = s.ui.screen === "plan";
  const onSetup = s.ui.screen === "setup";
  const blocked = footerBlockedReason(s);

  // U12: the Set up day footer is its own bar — the save state on the left and
  // Undo + the primary "Continue to tasks" on the right. Continue confirms the
  // day frame (existing day-setup contract) and advances the stepper to Plan
  // tasks. No copy-prompt control exists anywhere (R20b).
  if (onSetup && s.inputs) {
    return (
      <footer class="footer footer--setup" aria-label="Actions">
        <FooterBanners />
        <div class="footer__row">
          <div class="footer__left">
            <span class="footer__saved" role="status">
              {s.daySetup.confirmed
                ? `Saved ${display12h(s.inputs.time.now)}`
                : "Not saved yet"}
            </span>
          </div>
          <div class="footer__right">
            <button class="footer__undo" onClick={() => requestSetupUndo()}>
              Undo
            </button>
            <button
              class="btn btn--primary footer__continue"
              onClick={() => requestSetupContinue()}
            >
              Continue to tasks →
            </button>
          </div>
        </div>
      </footer>
    );
  }

  return (
    <footer class="footer" aria-label="Actions">
      <FooterBanners />
      <div class="footer__row">
        <div class="footer__left">
          {onPlan && (
            <button
              class="footer__back"
              onClick={() =>
                store.dispatch({ type: "UI", patch: { screen: "setup", settingsPanel: null } })
              }
            >
              ← Day setup
            </button>
          )}
          {blocked && (
            <span class="footer__blocked" role="status">
              {blocked}
            </span>
          )}
        </div>
        <div class="footer__right">
          <ActionDock />
        </div>
      </div>
    </footer>
  );
}
