/* settingsUrl.ts — history/deep-link contract for the one settings surface.
   URL parameters carry navigation only (never drafts): `settings` names the
   open panel and `section` names an in-panel destination. Unrelated
   parameters and the mockup fixture hash are preserved; unknown values open
   nothing. The store stays the source of truth — the URL is a projection that
   Back/Forward reconcile into it. */

import { useEffect } from "preact/hooks";
import { useApp } from "./context";
import { isSettingsPanel, type SettingsPanel } from "../store/store";

export const SETTINGS_PARAM = "settings";
export const SETTINGS_SECTION_PARAM = "section";

export interface SettingsUrlState {
  panel: SettingsPanel | null;
  section: string | null;
}

export function parseSettingsSearch(search: string): SettingsUrlState {
  const params = new URLSearchParams(search);
  const rawPanel = params.get(SETTINGS_PARAM);
  const panel = isSettingsPanel(rawPanel) ? rawPanel : null;
  const rawSection = params.get(SETTINGS_SECTION_PARAM);
  const section =
    panel === "day" && rawSection !== null && rawSection.trim() !== ""
      ? rawSection
      : null;
  return { panel, section };
}

export function serializeSettingsSearch(
  search: string,
  state: SettingsUrlState,
): string {
  const params = new URLSearchParams(search);
  params.delete(SETTINGS_PARAM);
  params.delete(SETTINGS_SECTION_PARAM);
  if (state.panel !== null) {
    params.set(SETTINGS_PARAM, state.panel);
    if (state.panel === "day" && state.section) {
      params.set(SETTINGS_SECTION_PARAM, state.section);
    }
  }
  const query = params.toString();
  return query ? `?${query}` : "";
}

function hrefWith(search: string): string {
  return `${window.location.pathname}${search}${window.location.hash}`;
}

function sameSettings(a: SettingsUrlState, b: SettingsUrlState): boolean {
  return a.panel === b.panel && a.section === b.section;
}

/**
 * Reconciles the settings destination with the address bar. Mount it beside
 * the cockpit (main.tsx wires both roots). Opening pushes exactly one owned
 * history entry, panel switches replace it, and closing either walks back
 * over the owned entry or removes the parameter in place. Back/Forward is
 * authoritative for navigation.
 */
export function SettingsUrlSync() {
  const { store } = useApp();

  useEffect(() => {
    const readStore = (): SettingsUrlState => {
      const ui = store.getState().ui;
      return { panel: ui.settingsPanel, section: ui.settingsSection };
    };

    // Boot: the URL is authoritative for the first paint.
    const boot = parseSettingsSearch(window.location.search);
    if (!sameSettings(readStore(), boot)) {
      store.dispatch({
        type: "UI",
        patch: { settingsPanel: boot.panel, settingsSection: boot.section },
      });
    }

    let owned = false;
    let last = readStore();
    const unsubscribe = store.subscribe(() => {
      const next = readStore();
      if (sameSettings(last, next)) return;
      last = next;
      const url = parseSettingsSearch(window.location.search);
      if (sameSettings(url, next)) {
        // Popstate (or a redundant write) already agrees with the store.
        owned = false;
        return;
      }
      if (next.panel === null) {
        if (url.panel === null) return;
        if (owned) {
          owned = false;
          window.history.back();
        } else {
          window.history.replaceState(
            null,
            "",
            hrefWith(serializeSettingsSearch(window.location.search, next)),
          );
        }
        return;
      }
      if (url.panel === null) {
        window.history.pushState(
          null,
          "",
          hrefWith(serializeSettingsSearch(window.location.search, next)),
        );
        owned = true;
      } else {
        window.history.replaceState(
          null,
          "",
          hrefWith(serializeSettingsSearch(window.location.search, next)),
        );
      }
    });

    const onPop = () => {
      owned = false;
      const url = parseSettingsSearch(window.location.search);
      const current = readStore();
      if (!sameSettings(url, current)) {
        store.dispatch({
          type: "UI",
          patch: { settingsPanel: url.panel, settingsSection: url.section },
        });
      }
    };
    window.addEventListener("popstate", onPop);
    return () => {
      unsubscribe();
      window.removeEventListener("popstate", onPop);
    };
  }, [store]);

  return null;
}
