/* Connections screen channel — the in-module seam between the composed
   Connections body and the shared Footer.

   Three concerns travel here, none of them global app state:
   - a save/discard REQUEST channel (the footer owns the buttons, the body
     owns the draft and the controller calls), mirroring day-setup/commands.ts;
   - a live unsaved-changes SUMMARY so the footer can name what is dirty
     without owning the draft;
   - a small draft CACHE so the lifted draft survives the Back-to-planning
     screen swap until the operator saves or discards (the requirement is
     explicit: leaving the screen must not lose unsaved Connections edits).

   The cache is written only while something is dirty; a clean screen clears
   it, so a remount after a save/discard re-reads current state. */

import type {
  CapacitiesSettings,
  CapacitiesSettingsDraft,
  TagExclusionSettings,
  TagExclusionSettingsDraft,
} from "../../model/types";

export interface ConnectionsChange {
  key: string;
  label: string;
}

export interface ConnectionsChangeSummary {
  count: number;
  labels: string[];
  /** The draft cannot be saved as-is (an invalid rule, R33). */
  invalid: boolean;
  saving: boolean;
}

export interface ConnectionsDraftCache {
  capSettings: CapacitiesSettings;
  capDraft: CapacitiesSettingsDraft;
  tagSettings: TagExclusionSettings;
  tagDraft: TagExclusionSettingsDraft;
  horizonText: string;
  statusText: string;
}

let cache: ConnectionsDraftCache | null = null;

export function readConnectionsCache(): ConnectionsDraftCache | null {
  return cache;
}

export function writeConnectionsCache(next: ConnectionsDraftCache): void {
  cache = next;
}

export function clearConnectionsCache(): void {
  cache = null;
}

/* -- save / discard requests --------------------------------------------- */

const saveHandlers = new Set<() => void>();
const discardHandlers = new Set<() => void>();

export function onConnectionsSave(fn: () => void): () => void {
  saveHandlers.add(fn);
  return () => {
    saveHandlers.delete(fn);
  };
}

export function requestConnectionsSave(): void {
  for (const fn of Array.from(saveHandlers)) fn();
}

export function onConnectionsDiscard(fn: () => void): () => void {
  discardHandlers.add(fn);
  return () => {
    discardHandlers.delete(fn);
  };
}

export function requestConnectionsDiscard(): void {
  for (const fn of Array.from(discardHandlers)) fn();
}

/* -- unsaved-changes summary --------------------------------------------- */

let summary: ConnectionsChangeSummary = {
  count: 0,
  labels: [],
  invalid: false,
  saving: false,
};
const summarySubscribers = new Set<(next: ConnectionsChangeSummary) => void>();

export function publishConnectionsChanges(
  changes: ConnectionsChange[],
  flags: { invalid?: boolean; saving?: boolean } = {},
): void {
  summary = {
    count: changes.length,
    labels: changes.map((c) => c.label),
    invalid: flags.invalid ?? false,
    saving: flags.saving ?? false,
  };
  for (const fn of Array.from(summarySubscribers)) fn(summary);
}

export function getConnectionsChanges(): ConnectionsChangeSummary {
  return summary;
}

export function subscribeConnectionsChanges(
  fn: (next: ConnectionsChangeSummary) => void,
): () => void {
  summarySubscribers.add(fn);
  fn(summary);
  return () => {
    summarySubscribers.delete(fn);
  };
}

/** Test-only reset so module state cannot leak between cases. */
export function __resetConnectionsDraftCache(): void {
  cache = null;
  summary = { count: 0, labels: [], invalid: false, saving: false };
  saveHandlers.clear();
  discardHandlers.clear();
  summarySubscribers.clear();
}
