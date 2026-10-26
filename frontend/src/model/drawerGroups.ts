/* drawerGroups.ts — U6: the LOCAL not-in-plan rows are grouped by when the row
   is owed FIRST, and by where it comes from SECOND.

   FEEDBACK-20261026 (Adam's words): "group drawers by urgency / pre-defined
   commitment such as date THEN object / source types". The drawer's local
   group used to be one flat list, so a row with a fixed time and a row with no
   date at all sat next to each other. Commitment order answers the only
   question that list is asked — what does today still owe, and when.

   Tier precedence is a fixed ladder, first match wins (so a row never appears
   in two tiers):

     1. overdue            — its deadline already passed
     2. due-today          — its deadline is today
     3. fixed-time         — a native scheduledStart, or a calendar event
     4. due-7d             — its deadline is within the next seven days
     5. priority           — p1/p2 with no nearer commitment
     6. no-date            — everything else

   A row that is both timed and overdue lands in `overdue`, and a row that is
   both timed and due today lands in `due-today`: a passed or same-day
   commitment is a harder fact than a wall-clock start, so the date tiers sit
   above `fixed-time`. Priority sits below the date tiers for the same reason —
   a stated date outranks an inferred importance.

   Inside one tier the rows group by source and object type (Todoist task /
   Capacities structure title / Calendar) through the existing `kindLabel`
   wording, so the second axis never invents a new vocabulary.

   Pure: no Preact, no store, no I/O. The drawer row carries display fields
   only, so the caller supplies the source facts it already holds
   (`inputs.assigned`) through `factsOf`. */

import type { AssignmentRow } from "../store/store";
import { bandOf } from "./bands";
import { dueLabel } from "./urgency";
import type { AssignedItem, Source } from "./types";

export type DrawerTierKey =
  | "overdue"
  | "due-today"
  | "fixed-time"
  | "due-7d"
  | "priority"
  | "no-date";

export interface DrawerTierSpec {
  key: DrawerTierKey;
  label: string;
  /** The tier's own rule, in one clause — the head's title. */
  note: string;
}

/** Tier order is the commitment ladder: index 0 is the hardest fact. */
export const DRAWER_TIERS: readonly DrawerTierSpec[] = [
  { key: "overdue", label: "Overdue", note: "deadline has passed" },
  { key: "due-today", label: "Due today", note: "deadline is today" },
  {
    key: "fixed-time",
    label: "Fixed time",
    note: "a scheduled start or calendar event, with no nearer deadline",
  },
  { key: "due-7d", label: "Due in 7 days", note: "deadline within the next week" },
  { key: "priority", label: "Priority p1–p2", note: "flagged priority, no nearer date" },
  {
    key: "no-date",
    label: "No date",
    note: "no date, or one further out than seven days",
  },
] as const;

export type DrawerSourceKey = "todoist" | "capacities" | "calendar" | "other";

/** Fixed sub-group order: source first (Todoist work, then Capacities
    structures, then calendar), kinds alphabetically inside one source. */
const SOURCE_ORDER: readonly DrawerSourceKey[] = [
  "todoist",
  "capacities",
  "calendar",
  "other",
];

const SOURCE_LABEL: Record<DrawerSourceKey, string> = {
  todoist: "Todoist",
  capacities: "Capacities",
  calendar: "Calendar",
  other: "Other",
};

/** The facts the tier ladder reads. `kind` is `kindLabel(item, titles)`: a
    Capacities structure title, null for Todoist and calendar rows. */
export interface DrawerFacts {
  source: Source;
  deadline: string | null;
  urgency: string | null;
  scheduledStart: string | null;
  kind: string | null;
}

/** Facts for a row whose source item is not at hand: it has no date, no
    priority, and no object type, so it lands in the last tier. */
export const NO_FACTS: DrawerFacts = {
  source: "todoist",
  deadline: null,
  urgency: null,
  scheduledStart: null,
  kind: null,
};

export interface DrawerSourceGroup {
  /** Stable key: source, or `source:kind` for a named object type. */
  key: string;
  source: DrawerSourceKey;
  label: string;
  kind: string | null;
  rows: AssignmentRow[];
}

export interface DrawerTierGroup extends DrawerTierSpec {
  /** Rows in this tier — the honest number, not a UI-derived one. */
  count: number;
  sources: DrawerSourceGroup[];
}

/** Todoist's native scheduledStart is `HH:MM` 24h; anything else is not a
    fixed time (the wire never carries a partial or 12h value). */
const NATIVE_TIME = /^(?:[01]\d|2[0-3]):[0-5]\d$/;

/** True when the row occupies a wall-clock moment: a native scheduled start,
    or a calendar event (immutable imported truth, LD19). */
export function hasFixedTime(facts: DrawerFacts): boolean {
  if (facts.source === "calendar") return true;
  const start = (facts.scheduledStart ?? "").trim();
  return NATIVE_TIME.test(start);
}

/** The tier one row lands in. First match wins, so a row is in exactly one
    tier; see the ladder at the top of this file. */
export function drawerTierOf(facts: DrawerFacts, today: string): DrawerTierKey {
  const due = dueLabel(facts.deadline, today);
  if (due?.tone === "overdue") return "overdue";
  if (due?.tone === "today") return "due-today";
  if (hasFixedTime(facts)) return "fixed-time";
  if (due?.tone === "soon") return "due-7d";
  // bandOf reads only source + urgency (through normalizeUrgency), so the two
  // fields it needs are the whole contract here.
  const band = bandOf({ source: facts.source, urgency: facts.urgency } as AssignedItem);
  if (band === "crit" || band === "high") return "priority";
  return "no-date";
}

function sourceKeyOf(source: string): DrawerSourceKey {
  return source === "todoist" || source === "capacities" || source === "calendar"
    ? source
    : "other";
}

function sourceGroupOf(
  row: AssignmentRow,
  facts: DrawerFacts,
): { key: string; source: DrawerSourceKey; label: string; kind: string | null } {
  const source = sourceKeyOf(String(row.source));
  const kind = (facts.kind ?? row.kind ?? "").trim();
  if (source === "capacities" && kind.length > 0) {
    // A Capacities structure title is the object type Adam reads; the source
    // chip already says Capacities.
    return { key: `${source}:${kind}`, source, label: kind, kind };
  }
  return { key: source, source, label: SOURCE_LABEL[source], kind: null };
}

/** Group rows into the tier ladder, then into source/object-type sub-groups.
    Tiers and sub-groups with no rows are absent, so the caller renders only
    what holds work; the tier order is always DRAWER_TIERS order. */
export function drawerTierGroups(
  rows: readonly AssignmentRow[],
  factsOf: (row: AssignmentRow) => DrawerFacts,
  today: string,
): DrawerTierGroup[] {
  const byTier = new Map<DrawerTierKey, Map<string, DrawerSourceGroup>>();
  for (const row of rows) {
    const facts = factsOf(row);
    const tier = drawerTierOf(facts, today);
    const group = sourceGroupOf(row, facts);
    let sources = byTier.get(tier);
    if (!sources) {
      sources = new Map();
      byTier.set(tier, sources);
    }
    const existing = sources.get(group.key);
    if (existing) existing.rows.push(row);
    else
      sources.set(group.key, {
        key: group.key,
        source: group.source,
        label: group.label,
        kind: group.kind,
        rows: [row],
      });
  }
  const out: DrawerTierGroup[] = [];
  for (const spec of DRAWER_TIERS) {
    const sources = byTier.get(spec.key);
    if (!sources || sources.size === 0) continue;
    const ordered = [...sources.values()].sort((a, b) => {
      const sa = SOURCE_ORDER.indexOf(a.source);
      const sb = SOURCE_ORDER.indexOf(b.source);
      if (sa !== sb) return sa - sb;
      return a.label < b.label ? -1 : a.label > b.label ? 1 : 0;
    });
    out.push({
      ...spec,
      count: ordered.reduce((n, g) => n + g.rows.length, 0),
      sources: ordered,
    });
  }
  return out;
}
