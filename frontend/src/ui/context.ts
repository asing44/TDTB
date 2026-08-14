/* context.ts — store/controller wiring for components. */

import { createContext } from "preact";
import { useContext, useEffect, useState } from "preact/hooks";
import type { Store } from "../store/createStore";
import type { Controller } from "../store/controller";
import type { AppState } from "../store/store";

export interface AppCtx {
  store: Store;
  controller: Controller;
}

export const Ctx = createContext<AppCtx | null>(null);

export function useApp(): AppCtx {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("Ctx missing");
  return ctx;
}

export function useAppState(): AppState {
  const { store } = useApp();
  const [state, setState] = useState(store.getState());
  useEffect(() => {
    // Re-sync on subscribe: dispatches between render and effect (e.g. a
    // fast adapter load) would otherwise be missed forever.
    setState(store.getState());
    return store.subscribe(() => setState(store.getState()));
  }, [store]);
  return state;
}
