/* bands.ts — T12e homepage redesign: the urgency-band model behind the
   allocator table, the per-row budget-line track, and the deterministic trim
   assist. Locked design: `TDTB Cockpit 3a.dc.html` + 3a-implementation-spec.

   Three jobs, all pure (no Preact, no store, no I/O):

   1. BANDS. Rows group under Critical / High / Everything else — urgency
      replaces planning-state as the primary grouping axis, so Adam can scan
      19 rows and spot the critical ones in one pass (brief problem 3).
      Shares are CONFIG, not derived — a stated intent ("critical gets 10
      blocks of my day") the spend is judged against.

   2. TRACK. Every duration slider renders the day's ONE budget line at the
      point where cumulative spend crosses it, so the number that moves when
      a slider is dragged is on the slider itself (brief problem 1). The
      budget point falls on at most one row's track by construction.

   3. TRIM. Deterministic, free, write-nothing: drop lowest-urgency rows
      until spend fits the budget, flag them for preview. Accepting routes
      through the existing today-only exclude override — never a source
      write. */

import type { AssignedItem } from "./types";
import { normalizeUrgency, type UrgencyTier } from "./urgency";

export type BandKey = "crit" | "high" | "else";

export interface BandSpec {
  key: BandKey;
  label: string;
  note: string;
  /** Tier(s) that land in this band. */
  tiers: ReadonlyArray<UrgencyTier | null>;
  /** Intended spend share in blocks — config, not derived (3a spec). */
  share: number;
}

/** Shares are design-time config (3a spec: "Shares are config, not
    derived"). Future home: the tdtb-bridger Skill-Config note; until that
    plumbing exists these defaults ship hard-coded, matching the locked mock. */
export const BANDS: readonly BandSpec[] = [
  {
    key: "crit",
    label: "Critical",
    /* FEEDBACK-10 (A15): concise and non-alarmist — urgency semantics
       unchanged, the tier logic still routes every crit row here. */
    note: "do today",
    tiers: ["crit"],
    share: 10,
  },
  {
    key: "high",
    label: "High",
    note: "soon",
    tiers: ["high"],
    share: 8,
  },
  {
    key: "else",
    label: "Everything else",
    note: "when room",
    tiers: ["med", "low", null],
    share: 5,
  },
] as const;

export function bandOf(item: AssignedItem): BandKey {
  const tier = normalizeUrgency(item)?.tier ?? null;
  if (tier === "crit") return "crit";
  if (tier === "high") return "high";
  return "else";
}

/** Stripe/band colours — semantic urgency, shared across themes like the
    segment palette (design brief: tier colours are cross-theme constants). */
export const BAND_COLOR: Record<BandKey, string> = {
  crit: "var(--c-overflow)",
  high: "var(--c-minting)",
  else: "#9ca3af",
};

export const TIER_STRIPE: Record<string, string> = {
  crit: "var(--c-overflow)",
  high: "var(--c-minting)",
  med: "#9ca3af",
  low: "var(--t-border)",
};

export function stripeColor(item: AssignedItem): string {
  return TIER_STRIPE[normalizeUrgency(item)?.tier ?? "low"] ?? "var(--t-border)";
}

/* ---------------------------------------------------------------- track -- */

export interface TrackRender {
  /** Fill width as a 0–100 percentage of the slider's range. */
  fillPct: number;
  /** Budget-line position, 0–100, when the line falls on this row's track. */
  markPct: number | null;
  /** True when this row's spend crosses the budget line (duration label
      turns red — 3a spec). */
  pastBudget: boolean;
}

/** Where the day's budget line falls on one row's track.
    `cumBefore` = blocks spent by every included row ABOVE this one in
    display order; `budget` = total selectable blocks (free + selected,
    server-derived, constant during a drag). Mirrors the locked mock's math:
    the mark renders once the remaining budget fits inside the track's range,
    clamped to 0 when the budget is already spent. */
export function trackFor(
  cumBefore: number,
  blocks: number,
  budget: number,
  maxBlocks: number,
): TrackRender {
  const fillPct = maxBlocks > 0 ? Math.min(100, (blocks / maxBlocks) * 100) : 0;
  const remaining = budget - cumBefore;
  const onTrack = remaining <= maxBlocks;
  const markPct = onTrack
    ? Math.max(0, Math.min(100, (remaining / maxBlocks) * 100))
    : null;
  return {
    fillPct,
    markPct,
    pastBudget: markPct != null && markPct < fillPct,
  };
}

/** Layered gradient for the track (3a spec): selected fill up to the budget
    point, overflow red past it, 16% overflow tint beyond the thumb. Colours
    stay CSS custom properties so both themes come from tokens.css. */
export function trackGradient(t: TrackRender): string {
  const blue = "var(--c-selected)";
  const red = "var(--c-overflow)";
  const tint = "color-mix(in srgb, var(--c-overflow) 16%, transparent)";
  if (t.markPct == null) {
    return `linear-gradient(90deg, ${blue} 0 ${t.fillPct}%, transparent ${t.fillPct}%)`;
  }
  if (!t.pastBudget) {
    return `linear-gradient(90deg, ${blue} 0 ${t.fillPct}%, transparent ${t.fillPct}% ${t.markPct}%, ${tint} ${t.markPct}%)`;
  }
  return `linear-gradient(90deg, ${blue} 0 ${t.markPct}%, ${red} ${t.markPct}% ${t.fillPct}%, ${tint} ${t.fillPct}%)`;
}

/* ----------------------------------------------------------------- trim -- */

export interface TrimPlan {
  /** Row ids to drop, lowest urgency first. */
  drop: string[];
  /** Blocks freed by the drop. */
  freed: number;
  /** Spend after the trim. */
  after: number;
  /** True when dropping every candidate still leaves the day over budget
      (crit/high rows are never auto-dropped). */
  partial: boolean;
}

/** Deterministic trim: walk the included rows from least to most important
    (reverse of the display order the caller passes in) and drop until spend
    fits the budget. Only med/low/untiered rows are candidates — the assist
    never volunteers to drop Critical or High work; that call stays human. */
export function trimPlan(
  orderedIncluded: ReadonlyArray<{ id: string; item: AssignedItem; blocks: number }>,
  budget: number,
): TrimPlan {
  const spend = orderedIncluded.reduce((s, r) => s + r.blocks, 0);
  if (spend <= budget) return { drop: [], freed: 0, after: spend, partial: false };
  const drop: string[] = [];
  let freed = 0;
  for (let i = orderedIncluded.length - 1; i >= 0; i--) {
    const r = orderedIncluded[i];
    if (bandOf(r.item) !== "else") continue;
    if (r.blocks === 0) continue; // all-day rows occupy no capacity
    drop.push(r.id);
    freed += r.blocks;
    if (spend - freed <= budget) break;
  }
  return {
    drop,
    freed,
    after: spend - freed,
    partial: spend - freed > budget,
  };
}
