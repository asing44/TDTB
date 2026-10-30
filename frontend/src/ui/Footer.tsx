/* Footer — U7 page frame (KTD11). The ActionDock, FooterBanners, and the
   readiness/source-refresh affordances are composed into one footer surface:
   a left "← Day setup" back link on Plan tasks plus the blocked reason drawn
   from the existing readiness logic, and the existing dock controls (primary
   action, billed-call note, approval flow) on the right.

   The Plan-only "Copy prompt" fallback is restored (R20b) in the ActionDock;
   it is pure-local and never a source write. Every existing safety gate is
   preserved: the dock still disables its billed actions for degraded sources,
   spent budget, unconfirmed setup, and unresolved defects, and the approval
   drawer still owns live commit. The daily LLM call allowance is shown in
   every footer variant so the cap is visible without opening Details. */

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
  if (s.inputs.assigned.length === 0) return "Nothing to plan yet — no assigned work today.";
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

/** The persistent daily LLM call allowance (backend MAX_CALLS_PER_RUN). Reads
    only the existing `s.ledger` — no extra fetch, no billing. It stays visible
    outside the collapsed Capacity Details in every footer variant, shows the
    accurate exhausted state, and states "unavailable" rather than inventing a
    count when no ledger has loaded. */
export function DayBudgetChip() {
  const s = useAppState();
  const ledger = s.ledger;
  if (!ledger) {
    return (
      <span class="chip footer__budget" role="status" aria-label="Daily LLM call allowance">
        Day budget: unavailable
      </span>
    );
  }
  const exhausted = ledger.remaining <= 0;
  return (
    <span
      class={`chip footer__budget ${exhausted ? "chip--warn" : ""}`}
      role="status"
      aria-label="Daily LLM call allowance"
    >
      Day budget: {ledger.remaining}/{ledger.cap} calls left
    </span>
  );
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
  // tasks. The Plan-only Copy prompt stays off this screen.
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
              {s.ledger ? ` · ${s.ledger.remaining}/${s.ledger.cap} calls left` : ""}
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
              Continue to plan →
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
            <span class="footer__blocked" role="status" title={blocked}>
              {blocked}
            </span>
          )}
          <DayBudgetChip />
        </div>
        <div class="footer__right">
          <ActionDock />
        </div>
      </div>
    </footer>
  );
}
