/* Footer — U7 page frame (KTD11). The ActionDock, FooterBanners, and the
   readiness/source-refresh affordances are composed into one footer surface:
   a left "← Day setup" back link on Plan tasks plus the blocked reason drawn
   from the existing readiness logic, and the existing dock controls (primary
   action, billed-call note, approval flow) on the right.

   No copy-prompt control exists anywhere (R20b, retired in U5). Every
   existing safety gate is preserved: the dock still disables its billed
   actions for degraded sources, spent budget, unconfirmed setup, and
   unresolved defects, and the approval drawer still owns live commit.

   U13 adds a third footer body: on the Connections screen the bar is the
   unsaved-changes surface — the dirty count and names on the left, Discard
   and Save on the right. The Plan primary action (Sequence & commit) never
   renders there. */

import { useEffect, useState } from "preact/hooks";
import { useApp, useAppState } from "./context";
import { ActionDock } from "./ActionDock";
import { FooterBanners } from "./FooterBanners";
import { requestSetupContinue, requestSetupUndo } from "./day-setup/commands";
import {
  getConnectionsChanges,
  requestConnectionsDiscard,
  requestConnectionsSave,
  subscribeConnectionsChanges,
  type ConnectionsChangeSummary,
} from "./connections/commands";
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

/** U13: the Connections unsaved-changes bar. The count and names come from
    the Connections body over the in-module channel; the buttons only REQUEST
    save and discard, so the draft and its revision stay owned by the body. */
function ConnectionsBar() {
  const [summary, setSummary] = useState<ConnectionsChangeSummary>(getConnectionsChanges());
  useEffect(() => subscribeConnectionsChanges(setSummary), []);
  const label =
    summary.count === 0
      ? "No unsaved changes"
      : `${summary.count} unsaved change${summary.count === 1 ? "" : "s"}` +
        (summary.labels.length > 0 ? ` · ${summary.labels.join(", ")}` : "");
  return (
    <div class="footer__row">
      <div class="footer__left">
        <span class="footer__saved" role="status">{label}</span>
      </div>
      <div class="footer__right">
        <button class="btn footer__discard" onClick={() => requestConnectionsDiscard()}>
          Discard
        </button>
        <button
          class="btn btn--primary footer__save"
          onClick={() => requestConnectionsSave()}
          disabled={summary.invalid || summary.saving}
        >
          {summary.saving ? "Saving…" : "Save"}
        </button>
      </div>
    </div>
  );
}

export function Footer() {
  const s = useAppState();
  const { store } = useApp();
  const onPlan = s.ui.screen === "plan";
  const onSetup = s.ui.screen === "setup";
  const onConnections = s.ui.screen === "connections";
  const blocked = footerBlockedReason(s);

  // U13: the Connections footer is its own unsaved-changes bar. The Plan
  // primary action (Sequence & commit) never renders on this screen.
  if (onConnections) {
    return (
      <footer class="footer footer--connections" aria-label="Actions">
        <FooterBanners />
        <ConnectionsBar />
      </footer>
    );
  }

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
