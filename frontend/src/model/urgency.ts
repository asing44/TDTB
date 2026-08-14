/* urgency + due-label normalization — T26 (2026-07-24 legibility findings).
   The gather emits vault urgency in three shapes — clean "3-high", a
   STRINGIFIED python list "['4-crit']", or null — and todoist urgency as the
   raw Todoist priority int (4 = highest). Raw literals rendered verbatim
   ("['4-crit']") are unscannable; normalize once, here, for every surface. */

import type { AssignedItem } from "./types";

export type UrgencyTier = "crit" | "high" | "med" | "low";

export interface UrgencyChip {
  text: string;
  tier: UrgencyTier | null;
}

const VAULT_TIERS: Record<string, UrgencyTier> = {
  "4": "crit",
  "3": "high",
  "2": "med",
  "1": "low",
};

/** Todoist priority int → user-facing p-level (API 4 = p1, highest). */
const TODOIST_P: Record<string, [string, UrgencyTier]> = {
  "4": ["p1", "crit"],
  "3": ["p2", "high"],
  "2": ["p3", "med"],
  "1": ["p4", "low"],
};

export function normalizeUrgency(item: AssignedItem): UrgencyChip | null {
  const raw = (item.urgency ?? "").trim();
  if (!raw || raw === "None") return null;
  if (item.source === "todoist") {
    const p = TODOIST_P[raw];
    return p ? { text: p[0], tier: p[1] } : { text: raw, tier: null };
  }
  // vault: unwrap a stringified list — "['4-crit']" → "4-crit"
  const inner = raw.replace(/^\[\s*'?/, "").replace(/'?\s*\]$/, "").split("','")[0].trim();
  if (!inner) return null;
  return { text: inner, tier: VAULT_TIERS[inner[0]] ?? null };
}

export type DueTone = "overdue" | "today" | "soon" | null;

export interface DueLabel {
  text: string;
  tone: DueTone;
}

/** Relative due label against the plan's valid date. ISO date stays in the
    tooltip; the visible label answers "how late am I" at a glance. */
export function dueLabel(deadline: string | null, validDate: string): DueLabel | null {
  if (!deadline) return null;
  const d = Date.parse(`${deadline}T00:00:00`);
  const t = Date.parse(`${validDate}T00:00:00`);
  if (Number.isNaN(d) || Number.isNaN(t)) return { text: `due ${deadline}`, tone: null };
  const days = Math.round((d - t) / 86_400_000);
  if (days < 0) return { text: `overdue ${-days}d`, tone: "overdue" };
  if (days === 0) return { text: "due today", tone: "today" };
  if (days === 1) return { text: "due tomorrow", tone: "soon" };
  if (days <= 7) return { text: `due in ${days}d`, tone: "soon" };
  return { text: `due ${deadline}`, tone: null };
}
