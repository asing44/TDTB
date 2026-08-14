/* createStore.ts — minimal subscribable store around the pure reducer. */

import { initialState, reducer, type Action, type AppState } from "./store";

export interface Store {
  getState: () => AppState;
  dispatch: (a: Action) => void;
  subscribe: (fn: () => void) => () => void;
}

export function createStore(seed: AppState = initialState): Store {
  let state = seed;
  const listeners = new Set<() => void>();
  return {
    getState: () => state,
    dispatch: (a: Action) => {
      state = reducer(state, a);
      for (const fn of listeners) fn();
    },
    subscribe: (fn: () => void) => {
      listeners.add(fn);
      return () => listeners.delete(fn);
    },
  };
}
