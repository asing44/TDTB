/* App — compact planning cockpit. Desktop keeps a capacity rail beside the
   assigned-only work list, while the footer owns the final action and recovery
   affordances. Mobile stacks the same surfaces; it does not render a second
   planning view. Drawers overlay the shell. */

import { useEffect } from "preact/hooks";
import { useAppState } from "./context";
import { Rail } from "./Rail";
import { Queue } from "./Queue";
import { ActionDock } from "./ActionDock";
import { FooterBanners } from "./FooterBanners";
import { SetupDrawer } from "./SetupDrawer";
import { ApprovalDrawer } from "./ApprovalDrawer";
import { BlockEditor } from "./BlockEditor";
import { AnchoredEditor } from "./AnchoredEditor";
import { ExecutionView } from "./ExecutionView";
import { CalendarImpact } from "./CalendarImpact";

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
    return <div class="center-note">Loading plan inputs…</div>;
  }
  if (s.loadError) {
    return (
      <div class="center-note" role="alert">
        Could not load plan inputs: {s.loadError}
      </div>
    );
  }
  if (!s.inputs) return null;

  return (
    <div class="cockpit">
      <Rail />
      <main class="cockpit__main">
        {s.commitPhase === "done" && (
          <section class="verified" aria-label="Day committed">
            <h2>✅ Day committed and verified</h2>
            <div class="verified__meta">
              All surfaces ok · zero verification failures · {s.inputs.validDate}
            </div>
          </section>
        )}
        {/* T20 runtime surface — renders only once a live commit exists. Keep
            execution ahead of planning evidence after a commit: the first
            question then is what to do next, not how the plan was built. */}
        <ExecutionView />
        {/* Calendar review is compact in the default cockpit. The full
            projection-only rows remain one disclosure away. */}
        <CalendarImpact compact />
        <Queue />
      </main>
      <div class="cockpit__footer">
        <FooterBanners />
        <ActionDock />
      </div>
      {/* Drawers mount on open so their local draft state initializes from
          the CURRENT store state each time. */}
      {s.ui.setupOpen && <SetupDrawer />}
      {s.ui.approvalOpen && <ApprovalDrawer />}
      {/* key remounts the editor when the target changes while open (keyboard
          Enter on a timeline block can retarget without an intermediate close) */}
      {s.ui.editorItem && <BlockEditor key={s.ui.editorItem} />}
      {s.ui.editorAnchor && <AnchoredEditor key={s.ui.editorAnchor} />}
    </div>
  );
}

export { THEME_KEY };
