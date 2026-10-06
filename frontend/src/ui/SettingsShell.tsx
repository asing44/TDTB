/* SettingsShell — the one settings surface. It owns the dialog chrome
   (backdrop, named modal, close control, focus management) and the tab bar
   over the three settings-like panels: Day setup, Capacities, and Tag
   exclusions (approved variant A).

   Exactly one settings host is mounted in App, driven by the canonical
   `ui.settingsPanel` destination. Every panel stays mounted for the life of
   the shell so unsaved drafts survive panel switches; closing the shell
   unmounts them, so reopening re-initializes each panel from current state.

   The Approval drawer (safety gate) and the contextual editors stay distinct
   surfaces and are deliberately not built on this shell. Save lifecycles
   stay panel-owned: Setup and Capacities save then close, tag exclusions
   stays open and reports its refresh outcome, and the Live micro-adventure
   actions persist immediately. No global Save. */

import { useApp, useAppState } from "./context";
import { useDialog } from "./useDialog";
import { DaySetupPanel } from "./DaySetupPanel";
import { CapacitiesSettingsPanel } from "./CapacitiesSettingsPanel";
import { TagExclusionSettingsPanel } from "./TagExclusionSettingsPanel";
import type { SettingsPanel } from "../store/store";

const TABS: ReadonlyArray<{ id: SettingsPanel; label: string }> = [
  { id: "day", label: "Day setup" },
  { id: "capacities", label: "Capacities" },
  { id: "tags", label: "Tag exclusions" },
];

export function SettingsShell() {
  const s = useAppState();
  const { store } = useApp();
  const close = () => store.dispatch({ type: "UI", patch: { settingsPanel: null } });
  const dialog = useDialog(close);
  const panel = s.ui.settingsPanel;

  if (panel === null) return null;

  return (
    <>
      <div class="drawer-backdrop" onClick={close} />
      <div
        class="drawer setup-drawer settings-shell"
        role="dialog"
        aria-modal="true"
        aria-label="Settings"
        tabIndex={-1}
        ref={dialog.ref}
        onKeyDown={(e) => dialog.onKeyDown(e as unknown as KeyboardEvent)}
      >
        <button class="iconbtn drawer__close" onClick={close} aria-label="Close settings">
          ✕
        </button>
        <h2>Settings</h2>
        <div class="settings-tabs">
          {TABS.map((tab) => (
            <button
              key={tab.id}
              type="button"
              class={`settings-tab${panel === tab.id ? " settings-tab--active" : ""}`}
              aria-pressed={panel === tab.id}
              onClick={() => store.dispatch({ type: "UI", patch: { settingsPanel: tab.id } })}
            >
              {tab.label}
            </button>
          ))}
        </div>
        <DaySetupPanel active={panel === "day"} />
        <CapacitiesSettingsPanel active={panel === "capacities"} />
        <TagExclusionSettingsPanel active={panel === "tags"} />
      </div>
    </>
  );
}
