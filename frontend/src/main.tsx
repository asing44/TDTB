/* main.tsx — entry. Fixture builds boot a FixtureAdapter with a scenario
   selected from the URL hash; production builds construct the API adapter.
   No billed endpoint is ever called on boot (test matrix § Safety). The hash
   remains a fixture-build review aid, not a user-facing planning surface. */

import { render } from "preact";
import { useEffect, useMemo, useState } from "preact/hooks";
import "./tokens.css";
import "./app.css";
import { App, THEME_KEY } from "./ui/App";
import { Ctx } from "./ui/context";
import { createStore } from "./store/createStore";
import { Controller } from "./store/controller";
import { FixtureAdapter } from "./adapters/fixture";
import { ApiAdapter } from "./adapters/api";
import { attachSessionPersistence } from "./store/persist";
import { installSourceRefreshLifecycle } from "./store/refreshLifecycle";
import { applyStagedState } from "./fixtures/boot";
import type { ScenarioName } from "./fixtures/scenarios";
import type { Theme } from "./store/store";

declare global {
  // Injected by vite.config.ts define — true in mockup/dev builds.
  const __FIXTURE__: boolean;
}

function savedTheme(): Theme {
  try {
    const t = localStorage.getItem(THEME_KEY);
    if (t === "light" || t === "dark" || t === "system") return t;
  } catch {
    /* private mode */
  }
  return "system";
}

function scenarioFromHash(): ScenarioName {
  const h = location.hash.replace("#", "");
  const names: ScenarioName[] = [
    "fresh",
    "ready",
    "sequenced",
    "conflict",
    "commit-preview",
    "verified",
  ];
  return (names as string[]).includes(h) ? (h as ScenarioName) : "fresh";
}

function FixtureRoot() {
  const [scenario, setScenario] = useState<ScenarioName>(scenarioFromHash());
  useEffect(() => {
    const onHash = () => setScenario(scenarioFromHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);
  const { store, controller } = useMemo(() => {
    const store = createStore();
    store.dispatch({ type: "THEME_SET", theme: savedTheme() });
    const adapter = new FixtureAdapter(scenario);
    const controller = new Controller(adapter, store.dispatch, store.getState);
    void controller.load().then(() => {
      applyStagedState(adapter.scenario, store.dispatch);
    });
    // Fixture-build debug handle — lets preview verification drive states
    // from the console. Never present in production builds.
    (window as unknown as Record<string, unknown>).__tdtb = { store, controller, adapter };
    return { store, controller, adapter };
  }, [scenario]);

  return (
    <Ctx.Provider value={{ store, controller }}>
      <App />
    </Ctx.Provider>
  );
}

function ProductionRoot() {
  const { store, controller } = useMemo(() => {
    const store = createStore();
    store.dispatch({ type: "THEME_SET", theme: savedTheme() });
    const adapter = new ApiAdapter();
    const controller = new Controller(adapter, store.dispatch, store.getState);
    return { store, controller };
  }, []);

  useEffect(() => {
    let disposed = false;
    let stopPersistence: (() => void) | undefined;
    let stopRefreshLifecycle: (() => void) | undefined;

    // Boot = reads only (/plan-inputs, /billed-ledger); no billed endpoint is
    // reachable without an explicit user action (test matrix § Safety).
    void controller.load().then(() => {
      if (disposed || store.getState().loadError) return;
      stopPersistence = attachSessionPersistence(store, controller);
      stopRefreshLifecycle = installSourceRefreshLifecycle(
        () => controller.refreshSources(),
        () => store.getState().loaded && !store.getState().loadError,
        {
          onVisibility: (listener) => {
            const handler = () => listener();
            document.addEventListener("visibilitychange", handler);
            return () => document.removeEventListener("visibilitychange", handler);
          },
          isVisible: () => document.visibilityState === "visible",
          onFocus: (listener) => {
            const handler = () => listener();
            window.addEventListener("focus", handler);
            return () => window.removeEventListener("focus", handler);
          },
        },
      );
    });

    return () => {
      disposed = true;
      stopRefreshLifecycle?.();
      stopPersistence?.();
    };
  }, [controller, store]);

  return (
    <Ctx.Provider value={{ store, controller }}>
      <App />
    </Ctx.Provider>
  );
}

render(
  __FIXTURE__ ? <FixtureRoot /> : <ProductionRoot />,
  document.getElementById("root")!,
);
