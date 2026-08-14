/* App — cockpit shell, T12e redesign (3a spec) → IMP-07. Desktop: two-column
   grid — 280px rail (capacity, trim, pie, keys, chips) beside the banded
   allocator table — plus a full-width sticky footer (banners + dock) so the
   number being committed is never off-screen. Mobile (≤767px) stacks to a
   single column via CSS. Drawers overlay both.

   IMP-07 composition (design-validation): Rail + CalendarImpact +
   Today's work (Queue) + ActionDock footer + drawers. The retired surfaces
   are NOT mounted: MobileAgenda (duplicate mobile planning surface),
   PlacementList (read-only placement review — CalendarImpact's compact alert
   owns review routing), ForgotStrip/TrimAssist (pruned from Rail), and
   ScenarioPanel (pruned from main). ExecutionView self-gates to a live-commit
   day (brief problem 5: NOW/NEXT was dead space pre-sequence). */

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
        <CalendarImpact />
        {/* T20 runtime surface — renders only once a live commit exists. */}
        <ExecutionView />
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
