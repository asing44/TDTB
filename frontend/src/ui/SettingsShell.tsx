/* SettingsShell — the one settings surface. It owns the dialog chrome
   (backdrop, named modal, close control, focus management) and hosts the
   SettingsHub: a status line and five plain-language entries that open their
   panels in place.

   Exactly one settings host is mounted in App, driven by the canonical
   `ui.settingsPanel` destination (pre-hub ids are mapped by the store). Every
   panel stays mounted for the life of the shell so unsaved drafts survive
   switching entries; closing the shell unmounts them, so reopening
   re-initializes each panel from current state.

   Accessibility: the shell is the dialog `useDialog` manages (focus
   management, Tab wrap, Escape). Entries are real buttons with aria-expanded
   and aria-controls. Programmatic navigation (URLs, history, the cockpit
   shortcuts) lands focus on the destination heading, including
   `ui.settingsSection` destinations such as Captures, while pressing an entry
   keeps focus on that entry. App makes the cockpit behind the modal inert.

   The Approval drawer (safety gate) and the contextual editors stay distinct
   surfaces. Save lifecycles stay panel-owned. No global Save. */

import { useEffect, useRef } from "preact/hooks";
import { useApp, useAppState } from "./context";
import { useDialog } from "./useDialog";
import { SettingsHub } from "./SettingsHub";

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

  // Destination focus: programmatic opens and history moves land on the
  // open panel's first heading (or the section heading named by
  // `settingsSection`); pressing an entry keeps focus on it. Async panels
  // expose their heading only after their load resolves,
  // so the panel itself takes focus immediately and an observer finishes the
  // move when the heading arrives — unless the user has already moved on.
  useEffect(() => {
    if (panel === null) return;
    const key = `${panel}:${section ?? ""}`;
    if (focusedKey.current === key) return;
    const active = document.activeElement as HTMLElement | null;
    if (active?.closest(".settings-hub__toggle")) {
      focusedKey.current = key;
      return;
    }
    const host = dialog.ref.current?.querySelector<HTMLElement>(
      ".settings-hub__panel:not([hidden])",
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
        <SettingsHub />
      </div>
    </>
  );
}
