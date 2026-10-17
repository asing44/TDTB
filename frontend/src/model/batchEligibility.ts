/* batchEligibility.ts — task-actions F2: per-action batch eligibility.

   Pure predicates. Given the selected rows and the day's context, answer
   which rows an action may touch and, for each it may not, the written reason
   the review panel shows before Apply. Nothing here writes or stages; it only
   describes, so the batch bar can render "N of M eligible" and the excluded
   names/reasons in the inline review.

   Rules ported from the approved prototype (design/task-actions/index.html,
   revision 2) and the production-plan decisions of 2026-10-16:
   - Capacities rows cannot use the Todoist all-day due path.
   - Capacities rows cannot be Unassigned in source (omitted, with reason).
   - Recurring rows get Skip occurrence instead of Unassign — a UI label only;
     the wire verb is `unassign` and the backend advances recurrence.
   - Protected native-timed rows cannot go all-day or automatic in a batch.
   - The Mint toggle is disabled for all-day, skipped, and Mint-session rows.
   - Skip today requires a confirmed Day Setup.
   - Delete and exact start are never batchable.

   No store, no Preact, no I/O. */

import type { Source } from "./types";
import { isProtectedNative } from "./rowDraft";

export type BatchAction =
  | "complete"
  | "skipToday"
  | "unassign"
  | "hideAlways"
  | "duration"
  | "automaticPlacement"
  | "allDay"
  | "timeBlock"
  | "mintAllow"
  | "mintDisallow";

export const BATCH_ACTIONS: readonly BatchAction[] = [
  "complete",
  "skipToday",
  "unassign",
  "hideAlways",
  "duration",
  "automaticPlacement",
  "allDay",
  "timeBlock",
  "mintAllow",
  "mintDisallow",
];

/** Actions deliberately never offered for a multi-selection. Exact start is
    individual-only; Delete is the one destructive verb and stays per row. */
export const NON_BATCHABLE_ACTIONS = ["delete", "exactStart"] as const;
export type NonBatchableAction = (typeof NON_BATCHABLE_ACTIONS)[number];

export function isBatchableAction(action: string): action is BatchAction {
  return (BATCH_ACTIONS as readonly string[]).includes(action);
}

export type BatchRowState = "planned" | "skipped";

/** One selectable plan row, as the batch bar sees it. */
export interface BatchRow {
  id: string;
  name: string;
  source: Source;
  state: BatchRowState;
  /** Resolved 30-minute blocks; 0 = all-day. */
  blocks: number;
  isRecurring: boolean;
  /** Todoist native wall time (HH:MM). Non-null = protected source time. */
  nativeStart: string | null;
  /** The row is a Mint session, so Mint overlap is inherent to it. */
  isMintSession: boolean;
  /** Committed pinned start (HH:MM); null = automatic. */
  pin: string | null;
}

export interface BatchContext {
  /** Day Setup's confirm flag; Skip today is refused until it is set. */
  daySetupConfirmed: boolean;
}

export interface BatchExclusion {
  id: string;
  name: string;
  reason: string;
}

export interface BatchEligibilityResult {
  action: BatchAction;
  /** Row ids the action may touch, in selection order. */
  eligible: string[];
  excluded: BatchExclusion[];
  counts: { eligible: number; excluded: number; total: number };
  /** The "N of M eligible" count line. */
  label: string;
}

/** Written refusal reasons. Exact strings: the review panel shows them and the
    tests assert them. */
export const EXCLUSION_REASONS = {
  allDayCapacities: "Capacities rows cannot use the Todoist all-day due path",
  unassignCapacities: "Capacities rows cannot be unassigned in source",
  protectedAllDay: "Protected source time; cannot go all-day from here",
  protectedAutomatic:
    "Protected source time; cannot change automatic placement here",
  allDayNoDuration: "All-day rows have no duration",
  allDayNoStart: "All-day rows have no start",
  mintAllDay: "Mint overlap is off for all-day rows",
  mintSkipped: "Skipped rows cannot change Mint overlap",
  mintSession: "Mint sessions already use Mint time",
  skipNeedsDaySetup: "Confirm Day Setup first",
  skipAlreadySkipped: "Already skipped today",
  alreadyAllDay: "Already all-day",
  alreadyTimeBlock: "Already a time block",
  alreadyAutomatic: "Already automatic",
} as const;

function reasonFor(
  action: BatchAction,
  row: BatchRow,
  context: BatchContext,
): string | null {
  const allDay = row.blocks <= 0;
  switch (action) {
    case "complete":
    case "hideAlways":
      return null;
    case "skipToday":
      if (row.state !== "planned") return EXCLUSION_REASONS.skipAlreadySkipped;
      if (!context.daySetupConfirmed) return EXCLUSION_REASONS.skipNeedsDaySetup;
      return null;
    case "unassign":
      if (row.source === "capacities") return EXCLUSION_REASONS.unassignCapacities;
      return null;
    case "duration":
      if (allDay) return EXCLUSION_REASONS.allDayNoDuration;
      return null;
    case "automaticPlacement":
      if (allDay) return EXCLUSION_REASONS.allDayNoStart;
      if (isProtectedNative(row)) return EXCLUSION_REASONS.protectedAutomatic;
      if (row.pin === null) return EXCLUSION_REASONS.alreadyAutomatic;
      return null;
    case "allDay":
      if (allDay) return EXCLUSION_REASONS.alreadyAllDay;
      if (row.source === "capacities") return EXCLUSION_REASONS.allDayCapacities;
      if (isProtectedNative(row)) return EXCLUSION_REASONS.protectedAllDay;
      return null;
    case "timeBlock":
      if (!allDay) return EXCLUSION_REASONS.alreadyTimeBlock;
      return null;
    case "mintAllow":
    case "mintDisallow":
      if (allDay) return EXCLUSION_REASONS.mintAllDay;
      if (row.isMintSession) return EXCLUSION_REASONS.mintSession;
      if (row.state !== "planned") return EXCLUSION_REASONS.mintSkipped;
      return null;
  }
}

/** The wire verb an Unassign batch resolves to. Always `unassign`: the
    server's VERBS accepts only that verb, and the backend itself advances a
    recurring task's due date instead of clearing it. "Skip occurrence" is a
    UI label for this outcome and never crosses the wire, so row kind is not
    part of the decision — the parameter stays for caller compatibility. */
export function unassignVerb(
  _row: Pick<BatchRow, "isRecurring">,
): "unassign" {
  return "unassign";
}

/** Eligibility for one action over the selected rows. */
export function batchEligibility(
  action: BatchAction,
  rows: readonly BatchRow[],
  context: BatchContext,
): BatchEligibilityResult {
  const eligible: string[] = [];
  const excluded: BatchExclusion[] = [];
  for (const row of rows) {
    const reason = reasonFor(action, row, context);
    if (reason === null) eligible.push(row.id);
    else excluded.push({ id: row.id, name: row.name, reason });
  }
  const total = rows.length;
  return {
    action,
    eligible,
    excluded,
    counts: { eligible: eligible.length, excluded: excluded.length, total },
    label: `${eligible.length} of ${total} eligible`,
  };
}
