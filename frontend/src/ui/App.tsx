/* App — U7 page frame. One bounded, centered column: the stepper header owns
   the date, step state, source health, and the Connections link; the body
   swaps the Plan tasks / Set up day / Connections screen; and one footer owns
   the back link, the blocked reason, and the primary action. The retired left
   rail is no longer a column — it lives temporarily inside the Plan tasks
   body as a collapsible "Day summary" region (U7a home; U9–U13 replace it).
   Drawers overlay the shell. */

import { useEffect } from "preact/hooks";
import { useAppState } from "./context";
import { Header } from "./Header";
import { Footer } from "./Footer";
import { Queue } from "./Queue";
import { SettingsShell } from "./SettingsShell";
import { ApprovalDrawer } from "./ApprovalDrawer";
import { BlockEditor } from "./BlockEditor";
import { AnchoredEditor } from "./AnchoredEditor";
import { ExecutionView } from "./ExecutionView";
import { DaySetupPanel } from "./DaySetupPanel";
import { ConnectionsScreen } from "./connections/ConnectionsScreen";
import "./plan-dark-contrast.css";

const THEME_KEY = "tdtb-cockpit-theme";

export function App() {
  const s = useAppState();

  // Persistent Light/Dark/System toggle (locked decision 5).
  useEffect(() => {
    const root = document.documentElement;
    if (s.theme === "system") root.removeAttribute("data-theme");
    else root.setAttribute("data-theme", s.theme);
    try {
      localStorage.setItem(THEME_KEY, s.theme);
    } catch {
      /* private mode */
    }
  }, [s.theme]);

  if (!s.loaded) {
    return <div class="center-note" role="status" aria-live="polite">Loading plan inputs…</div>;
  }
  if (s.loadError) {
    return (
      <div class="center-note" role="alert">
        Could not load plan inputs: {s.loadError}
      </div>
    );
  }
  if (!s.inputs) return null;

  // Background isolation (settings accessibility stage): while the one
  // settings shell owns focus, everything behind it — header, body, footer —
  // is inert and hidden from assistive technology. The wrapper is
  // `display: contents`, so the header, main, and footer stay the column's
  // layout children.
  const settingsOpen = s.ui.settingsPanel !== null;
  const screen = s.ui.screen;
  // The promoted screen body is used only when no contextual drawer owns the
  // viewport, so exactly one surface renders at a time.
  const promotedScreen = !settingsOpen && screen !== "plan";

  return (
    <div class="cockpit">
      <div
        class="cockpit__background"
        inert={settingsOpen ? true : undefined}
        aria-hidden={settingsOpen ? "true" : undefined}
      >
        <Header />
        {promotedScreen ? (
          <main
            class="cockpit__main cockpit__screen"
            aria-label={screen === "setup" ? "Set up day" : "Connections"}
          >
            {screen === "setup" ? (
              <DaySetupPanel active={true} variant="screen" />
            ) : (
              <ConnectionsScreen />
            )}
          </main>
        ) : (
          <main class="cockpit__main" aria-label="Today's planning evidence">
            {/* U9: the retired rail now lives inside the Queue's capacity
                card Details disclosure (KTD13) — no capacity evidence is
                lost, and the task queue still heads the body. */}
            {s.commitPhase === "done" && (
              <section class="verified" aria-label="Day committed" role="status">
                <h2>✅ Day committed and verified</h2>
                <div class="verified__meta">
                  All surfaces ok · zero verification failures · {s.inputs.validDate}
                </div>
              </section>
            )}
            {/* T20 runtime surface — renders only once a live commit exists.
                Keep execution ahead of planning evidence after a commit: the
                first question then is what to do next, not how the plan was
                built. */}
            <ExecutionView />
            {/* S5: calendar evidence is no longer a sibling section — the
                Queue renders it inline inside the work surface. */}
            <Queue />
          </main>
        )}
        <Footer />
      </div>
      {/* Drawers mount on open so their local draft state initializes from
          the CURRENT store state each time. One settings host covers the
          three settings-like panels; the Approval gate and the contextual
          editors stay distinct surfaces. */}
      {settingsOpen && <SettingsShell />}
      {s.ui.approvalOpen && <ApprovalDrawer />}
      {/* key remounts the editor when the target changes while open (keyboard
          Enter on a timeline block can retarget without an intermediate close) */}
      {s.ui.editorItem && <BlockEditor key={s.ui.editorItem} />}
      {s.ui.editorAnchor && <AnchoredEditor key={s.ui.editorAnchor} />}
    </div>
  );
}

export { THEME_KEY };
