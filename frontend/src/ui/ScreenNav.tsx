/* ScreenNav — B3 promoted top-level screens: Plan tasks / Set up day /
   Connections. A real tablist with roving tabindex and Arrow/Home/End
   navigation, reusing the existing panel components as screen bodies. The
   contextual settings drawer stays a separate surface; selecting a promoted
   screen closes it so exactly one surface owns the viewport. */

import { useApp, useAppState } from "./context";
import type { Screen } from "../store/store";

const TABS: ReadonlyArray<{ id: Screen; label: string }> = [
  { id: "plan", label: "Plan tasks" },
  { id: "setup", label: "Set up day" },
  { id: "connections", label: "Connections" },
];

const tabId = (screen: Screen) => `screen-tab-${screen}`;

export function ScreenNav() {
  const s = useAppState();
  const { store } = useApp();
  const current = s.ui.screen;

  const select = (screen: Screen, focusTarget?: HTMLElement | null) => {
    store.dispatch({ type: "UI", patch: { screen, settingsPanel: null } });
    (focusTarget ?? document.getElementById(tabId(screen)))?.focus();
  };

  const onKeyDown = (e: KeyboardEvent) => {
    const index = TABS.findIndex((tab) => tab.id === current);
    let next = index;
    if (e.key === "ArrowRight") next = (index + 1) % TABS.length;
    else if (e.key === "ArrowLeft") next = (index + TABS.length - 1) % TABS.length;
    else if (e.key === "Home") next = 0;
    else if (e.key === "End") next = TABS.length - 1;
    else return;
    e.preventDefault();
    select(TABS[next].id);
  };

  return (
    <nav class="screen-nav" aria-label="Cockpit screens">
      <div class="screen-nav__tabs" role="tablist" aria-label="Screens" onKeyDown={(e) => onKeyDown(e as unknown as KeyboardEvent)}>
        {TABS.map((tab) => {
          const selected = current === tab.id;
          return (
            <button
              key={tab.id}
              type="button"
              role="tab"
              id={tabId(tab.id)}
              class={`screen-nav__tab${selected ? " screen-nav__tab--active" : ""}`}
              aria-selected={selected}
              tabIndex={selected ? 0 : -1}
              onClick={(e) => select(tab.id, e.currentTarget as HTMLElement)}
            >
              {tab.label}
            </button>
          );
        })}
      </div>
    </nav>
  );
}
