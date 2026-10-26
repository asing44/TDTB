/* allocatorView.ts — T12e: the one display order the redesigned page shares.

   The rail's budget readout, the table's per-row budget-line tracks, and the
   trim assist all agree on a single sequence of included rows: urgency bands
   (Critical → High → Everything else), importance-ordered within a band,
   needs-placement before scheduled. Cumulative spend walks that order, so
   the budget line falls on exactly one row's track and the trim assist drops
   from the exact bottom of what the user is looking at. */

import { bandOf, trimPlan, type BandKey, type TrimPlan } from "../model/bands";
import { orderByImportance } from "../model/allocator";
import {
  hierarchyBandedRows,
  type HierarchyRow,
} from "../model/allocatorView";
import type { AssignedItem } from "../model/types";
import {
  effectiveBlocks,
  includedItems,
  isIncluded,
  queueState,
  type AppState,
} from "./store";

export interface BandedRows {
  crit: AssignedItem[];
  high: AssignedItem[];
  else: AssignedItem[];
  scheduled: AssignedItem[];
  excluded: AssignedItem[];
}

/** Group + order the assigned rows for display. Needs-placement AND all-day
    rows split into urgency bands; scheduled / excluded stay trailing sections.

    All-day rows band with everything else rather than living in their own
    trailing section (2026-07-27, Adam): the section made "set this to all
    day" relocate the row you were working on to the bottom of the page. An
    all-day row keeps its place and carries an ALL DAY badge instead —
    `queueState` still reports `background`, so the export prompt groups it
    exactly as before; only this display axis changed. */
export function bandedRows(s: AppState): BandedRows {
  const out: BandedRows = {
    crit: [],
    high: [],
    else: [],
    scheduled: [],
    excluded: [],
  };
  if (!s.inputs) return out;
  const today = s.inputs.validDate;
  for (const item of orderByImportance(s.inputs.assigned, today)) {
    const state = queueState(s, item.id);
    if (state === "needs-placement" || state === "background") out[bandOf(item)].push(item);
    else out[state].push(item);
  }
  return out;
}

/** Included rows in exact display order — the sequence cumulative spend and
    the trim assist both walk. */
export function includedDisplayOrder(
  s: AppState,
): Array<{ id: string; item: AssignedItem; blocks: number }> {
  const g = bandedRows(s);
  return [...g.crit, ...g.high, ...g.else, ...g.scheduled]
    .filter((i) => isIncluded(s, i.id))
    .map((item) => ({ id: item.id, item, blocks: effectiveBlocks(s, item.id) }));
}

/** Total selectable blocks — where the budget line sits. Constant during a
    drag: the server derived free as budget − selected, so free + selected
    recovers the budget exactly (same identity liveFree leans on). */
export function budgetTotal(s: AppState): number {
  return s.capacity ? s.capacity.free + s.capacity.selected : 0;
}

/** In-flight selected spend (same substitution as liveFree callers). */
export function localSelected(s: AppState): number {
  return includedItems(s).reduce((sum, i) => sum + effectiveBlocks(s, i.id), 0);
}

/** The deterministic trim for the current state. Pure of the same state the
    table renders, so the rail card and the row flags can never disagree. */
export function trimForState(s: AppState): TrimPlan {
  return trimPlan(includedDisplayOrder(s), budgetTotal(s));
}

/** KTD12 view grouping: the task table's section membership. This is a
    DISPLAY projection only — the trim plan and sequencing order in the model
    are untouched. Planning 2a order: "Critical" is the Critical band, then
    "Fixed" (scheduled/native times), then "When there's room" (the High and
    Everything-else bands in their original trim order). "Out today" comes
    last and combines the Excluded-today rows with the dropped-today ledger so
    one reversible section owns both. */
export interface DroppedTodayRow {
  identity: string;
  name: string;
  droppedAt: string | null;
}

export interface TaskTableSection {
  key: "critical" | "room" | "fixed" | "out";
  label: string;
  sub: string;
  rows: HierarchyRow[];
  dropped: DroppedTodayRow[];
}

export function taskTableSections(s: AppState): TaskTableSection[] {
  const g = hierarchyBandedRows(s);
  const dropped = (s.inputs?.droppedToday ?? []) as DroppedTodayRow[];
  return [
    {
      key: "critical",
      label: "Critical",
      sub: "Deadline pressure — protect these",
      rows: g.crit,
      dropped: [],
    },
    {
      key: "fixed",
      label: "Fixed",
      sub: "Scheduled and native times",
      rows: g.scheduled,
      dropped: [],
    },
    {
      key: "room",
      label: "When there's room",
      sub: "High and everything else, in trim order",
      rows: [...g.high, ...g.else],
      dropped: [],
    },
    {
      key: "out",
      label: "Out today",
      sub: "Hidden for today — Include today or Undo restores",
      rows: g.excluded,
      dropped,
    },
  ];
}

/** Per-band spend for the band-header share bars. */
export function bandSpend(s: AppState, band: BandKey): number {
  const g = bandedRows(s);
  const rows = band === "else" ? g.else : g[band];
  return rows
    .filter((i) => isIncluded(s, i.id))
    .reduce((sum, i) => sum + effectiveBlocks(s, i.id), 0);
}
