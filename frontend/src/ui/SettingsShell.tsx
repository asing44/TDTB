/* SettingsShell — the one settings surface. It owns the dialog chrome
   (backdrop, named modal, close control, focus management) and the tablist
   over the three settings-like panels: Day setup, Capacities, and Tag
   exclusions (approved variant A).

   Exactly one settings host is mounted in App, driven by the canonical
   `ui.settingsPanel` destination. Every panel stays mounted for the life of
   the shell so unsaved drafts survive panel switches; closing the shell
   unmounts them, so reopening re-initializes each panel from current state.

   Accessibility (stage 3): the switcher is a real tablist — selected state,
   panel relationships, roving tabindex, Arrow/Home/End navigation. The
   shell is the dialog the mount effect of `useDialog` manages, so focus
   survives panel switches. Programmatic navigation (the Settings chip, the
   dock/rail Day setup shortcut, URLs, history) lands focus on the
   destination heading — including `ui.settingsSection` destinations such as
   Captures — while a tab switch keeps focus on the tab itself (ARIA tabs
   convention). Only the active panel body is rendered, so the dialog's Tab
   wrap never reaches another panel's controls, and App makes the cockpit
   behind the modal inert/aria-hidden while it owns focus.

   The Approval drawer (safety gate) and the contextual editors stay distinct
   surfaces and are deliberately not built on this shell. Save lifecycles
   stay panel-owned: Setup and Capacities save then close, tag exclusions
   stays open and reports its refresh outcome, and the Live micro-adventure
   actions persist immediately. No global Save. */

import { useEffect, useRef } from "preact/hooks";
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

const tabId = (panel: SettingsPanel) => `settings-tab-${panel}`;
const panelId = (panel: SettingsPanel) => `settings-panel-${panel}`;

export function SettingsShell() {
  const s = useAppState();
  const { store } = useApp();
  const close = () => store.dispatch({ type: "UI", patch: { settingsPanel: null } });
  const dialog = useDialog(close);
  const panel = s.ui.settingsPanel;
  const section = s.ui.settingsSection;
  const tablistRef = useRef<HTMLDivElement>(null);
  // Navigation key whose destination focus was already delivered. Tab
  // switches record it too: the tab itself keeps focus by design.
  const focusedKey = useRef<string | null>(null);

  const selectPanel = (id: SettingsPanel, focusTarget?: HTMLElement | null) => {
    store.dispatch({ type: "UI", patch: { settingsPanel: id } });
    (focusTarget ?? document.getElementById(tabId(id)))?.focus();
  };

  const onTabListKeyDown = (e: KeyboardEvent) => {
    const current = TABS.findIndex((tab) => tab.id === panel);
    let next = current;
    if (e.key === "ArrowRight") next = (current + 1) % TABS.length;
    else if (e.key === "ArrowLeft") next = (current + TABS.length - 1) % TABS.length;
    else if (e.key === "Home") next = 0;
    else if (e.key === "End") next = TABS.length - 1;
    else return;
    e.preventDefault();
    selectPanel(TABS[next].id);
  };

  // Destination focus: programmatic opens and history moves land on the
  // active panel's first heading (or the section heading named by
  // `settingsSection`); a tab switch keeps focus on its tab. Async panels
  // (Capacities, Tags) expose their heading only after their load resolves,
  // so the panel itself takes focus immediately and an observer finishes the
  // move when the heading arrives — unless the user has already moved on.
  useEffect(() => {
    if (panel === null) return;
    const key = `${panel}:${section ?? ""}`;
    if (focusedKey.current === key) return;
    const tablistEl = tablistRef.current;
    const active = document.activeElement as HTMLElement | null;
    if (active && tablistEl?.contains(active)) {
      focusedKey.current = key;
      return;
    }
    const host = dialog.ref.current?.querySelector<HTMLElement>(
      '[role="tabpanel"]:not([hidden])',
    );
    if (!host) return;
    const findHeading = (): HTMLElement | null => {
      if (section !== null) {
        const target = Array.from(
          host.querySelectorAll<HTMLElement>("[data-settings-section]"),
        ).find((el) => el.getAttribute("data-settings-section") === section);
        const heading = target?.querySelector<HTMLElement>("h3");
        if (heading) return heading;
      }
      return host.querySelector<HTMLElement>("h3");
    };
    const focusHeading = (): boolean => {
      const heading = findHeading();
      if (!heading) return false;
      heading.tabIndex = -1;
      heading.focus();
      focusedKey.current = key;
      return true;
    };
    if (focusHeading()) return;
    host.tabIndex = -1;
    host.focus();
    const observer = new MutationObserver(() => {
      if (focusedKey.current === key) return observer.disconnect();
      if (document.activeElement !== host) return observer.disconnect();
      if (focusHeading()) observer.disconnect();
    });
    observer.observe(host, { childList: true, subtree: true });
    return () => observer.disconnect();
  }, [panel, section, dialog.ref]);

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
        <div
          class="settings-tabs"
          role="tablist"
          aria-label="Settings panels"
          ref={tablistRef}
          onKeyDown={(e) => onTabListKeyDown(e as unknown as KeyboardEvent)}
        >
          {TABS.map((tab) => {
            const selected = panel === tab.id;
            return (
              <button
                key={tab.id}
                type="button"
                role="tab"
                id={tabId(tab.id)}
                class={`settings-tab${selected ? " settings-tab--active" : ""}`}
                aria-selected={selected}
                aria-controls={panelId(tab.id)}
                tabIndex={selected ? 0 : -1}
                onClick={(e) => selectPanel(tab.id, e.currentTarget as HTMLElement)}
              >
                {tab.label}
              </button>
            );
          })}
        </div>
        <div
          class="settings-tabpanel settings-tabpanel--day"
          id={panelId("day")}
          role="tabpanel"
          aria-labelledby={tabId("day")}
          hidden={panel !== "day"}
        >
          <DaySetupPanel active={panel === "day"} />
        </div>
        <div
          class="settings-tabpanel settings-tabpanel--capacities"
          id={panelId("capacities")}
          role="tabpanel"
          aria-labelledby={tabId("capacities")}
          hidden={panel !== "capacities"}
        >
          <CapacitiesSettingsPanel active={panel === "capacities"} />
        </div>
        <div
          class="settings-tabpanel settings-tabpanel--tags"
          id={panelId("tags")}
          role="tabpanel"
          aria-labelledby={tabId("tags")}
          hidden={panel !== "tags"}
        >
          <TagExclusionSettingsPanel active={panel === "tags"} />
        </div>
      </div>
    </>
  );
}
