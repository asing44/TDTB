/* SettingsShell — the one settings surface. It owns the dialog chrome
   (backdrop, named modal, close control, focus management) and hosts the
   SettingsHub: one scrolling page of sections under a sticky jump bar.

   Exactly one settings host is mounted in App, driven by the canonical
   `ui.settingsPanel` destination (pre-hub ids are mapped by the store; the
   Habits section is `settingsPanel: "day"` + `settingsSection: "habits"`,
   deep-linked as `?settings=habits`). Every section is always mounted while
   the shell is open, so unsaved drafts survive jumping around; closing the
   shell unmounts them, so reopening re-initializes from current state.

   Accessibility: the shell is the dialog `useDialog` manages (focus
   management, Tab wrap, Escape). Jump buttons are real buttons; pressing one
   scrolls its section into view and keeps focus on the button. Programmatic
   navigation (URLs, history, the cockpit shortcuts) scrolls to the
   destination section and lands focus on its heading, including
   `ui.settingsSection` destinations such as Captures. App makes the cockpit
   behind the modal inert.

   The Approval drawer (safety gate) and the contextual editors stay distinct
   surfaces. Save lifecycles stay panel-owned. No global Save. */

import { useEffect, useRef } from "preact/hooks";
import { useApp, useAppState } from "./context";
import { useDialog } from "./useDialog";
import { HABITS_SECTION_ID, SettingsHub, hubPanelId } from "./SettingsHub";

export function SettingsShell() {
  const s = useAppState();
  const { store } = useApp();
  const close = () => store.dispatch({ type: "UI", patch: { settingsPanel: null } });
  const dialog = useDialog(close);
  const panel = s.ui.settingsPanel;
  const section = s.ui.settingsSection;
  // Navigation key whose destination focus was already delivered. Pressing
  // an entry records it too: the entry itself keeps focus by design.
  const focusedKey = useRef<string | null>(null);

  // Destination focus: programmatic opens and history moves scroll to the
  // destination section and focus its heading (or the heading of the section
  // named by `settingsSection`); pressing a jump button keeps focus on it.
  useEffect(() => {
    if (panel === null) return;
    const key = `${panel}:${section ?? ""}`;
    if (focusedKey.current === key) return;
    const active = document.activeElement as HTMLElement | null;
    if (active?.closest(".settings-hub__jump-btn")) {
      focusedKey.current = key;
      return;
    }
    const root = dialog.ref.current;
    if (!root) return;
    const isHabits = panel === "day" && section === "habits";
    const host = root.querySelector<HTMLElement>(
      `#${isHabits ? HABITS_SECTION_ID : hubPanelId(panel)}`,
    );
    if (!host) return;
    let heading: HTMLElement | null = null;
    if (section !== null && !isHabits) {
      const target = Array.from(
        host.querySelectorAll<HTMLElement>("[data-settings-section]"),
      ).find((el) => el.getAttribute("data-settings-section") === section);
      heading = target?.querySelector<HTMLElement>("h3") ?? null;
    }
    heading ??= host.querySelector<HTMLElement>("h3");
    host.scrollIntoView?.({ block: "start" });
    const el = heading ?? host;
    el.tabIndex = -1;
    el.focus({ preventScroll: true });
    focusedKey.current = key;
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
        <SettingsHub />
      </div>
    </>
  );
}
