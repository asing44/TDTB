/* rowDraft.ts — task-actions F2: the staged per-row shaping draft.

   Pure data + transforms. No Preact, no store, no adapter, no I/O. The
   Today's-work row editor stages plan-only changes here (mode, duration, pin,
   Mint overlap, protected-source permission) and `diff` answers whether the
   row is staged and what changed. `toOverrides` projects the draft onto the
   exact controller operations a Save would fire, so the store slice can
   save/discard without re-deriving the shaping.

   Semantics ported verbatim from the approved prototype
   (design/task-actions/index.html, revision 2), which is the behavior
   authority for this slice. Two production differences are deliberate:

   - duration is `blocks` (30-minute blocks) because that is what the app's
     TodayOverride carries; the prototype's `dur` was minutes.
   - the pinned start lives in `placements[id]` (a `pin`), not on the row.

   All-day is `blocks === 0` upstream (AssignedItem.blocks), matching
   `effectiveBlocks`; there is no separate all-day flag on the wire. */

import type { Source, TodayOverride } from "./types";

export type RowMode = "time" | "allday";

/* One HH:MM wall time. Mirrors the backend/adapter format and the store's own
   protected-native check, anchored so a malformed time can never become a
   placement. */
const HHMM = /^(?:[01]\d|2[0-3]):[0-5]\d$/;

/** The upstream facts one plan row exposes to the draft. */
export interface RowFacts {
  id: string;
  source: Source;
  /** Resolved 30-minute blocks; 0 = all-day. */
  blocks: number;
  isRecurring: boolean;
  /** Todoist native wall time (HH:MM). Non-null = protected source time. */
  nativeStart: string | null;
  /** The row is a Mint session, so Mint overlap is inherent to it. */
  isMintSession: boolean;
}

/** Today's committed shaping for one row: the values a draft is diffed
    against. Deliberately self-contained — it carries the row facts too, so
    `diff` never needs a second lookup and the caller can pass one object. */
export interface RowSession {
  row: RowFacts;
  /** Committed today override (blocks 0 = all-day). */
  override: TodayOverride | null;
  /** Committed pinned start (HH:MM); null = automatic. */
  pin: string | null;
  /** `timeAdjustmentOptIns[id] === true`. */
  allowSourceTime: boolean;
  /** `mintOverlapOptIns[id] === true`. */
  mintOverlap: boolean;
}

export interface RowDraft {
  id: string;
  mode: RowMode;
  /** Current duration in 30-minute blocks. Kept through an all-day switch so
      a return to time block can restore it; never read while mode is
      `allday` (the row is 0 blocks there). */
  blocks: number;
  /** Duration captured at the moment the row switched to all-day. Null when
      the row was already all-day and never switched, so there is no prior
      duration to restore. */
  blocksBeforeAllDay: number | null;
  /** Staged start (HH:MM); null = automatic. */
  pin: string | null;
  /** Operator permission to replace a protected Todoist native time. */
  allowSourceTime: boolean;
  /** Operator grant to overlap a Mint session. */
  mintOverlap: boolean;
}

export type DraftField =
  | "mode"
  | "blocks"
  | "pin"
  | "allowSourceTime"
  | "mintOverlap";

/** One staged field difference, in the batch-undo before/after shape. */
export interface DraftChange {
  field: DraftField;
  from: string | number | boolean | null;
  to: string | number | boolean | null;
}

export interface DraftDiff {
  staged: boolean;
  changes: DraftChange[];
}

/** The operations a Save fires, mirroring the controller methods that own
    them: `setOverride`, `placeRow`/`resetPlacement`, and the two per-row
    opt-ins. `included` is always true — a draft only exists for a row still
    in today's plan; Skip/Unassign are separate verbs. */
export type RowOperation =
  | { type: "setOverride"; id: string; included: boolean; blocks: number | null }
  | { type: "placeRow"; id: string; start: string }
  | { type: "resetPlacement"; id: string }
  | { type: "setTimeAdjustmentOptIn"; id: string; allow: boolean }
  | { type: "setMintOverlapOptIn"; id: string; allow: boolean };

/** A row whose start is a protected Todoist native time. Structural so both
    the draft and the batch model can use it without importing each other's
    full row shape. */
export function isProtectedNative(row: {
  source: Source;
  nativeStart: string | null;
}): boolean {
  return row.source === "todoist" && HHMM.test(row.nativeStart ?? "");
}

/** Effective blocks a draft commits: all-day is 0, otherwise the staged
    duration. */
export function draftBlocks(draft: RowDraft): number {
  return draft.mode === "allday" ? 0 : draft.blocks;
}

/** Seed a draft from one row's upstream facts and committed session shaping.
    `row` is the authoritative upstream snapshot; `session.row` should be the
    same row (it is what `diff` compares against). */
export function createDraft(row: RowFacts, session: RowSession): RowDraft {
  const blocks = session.override?.blocks ?? row.blocks;
  const allDay = blocks <= 0;
  return {
    id: row.id,
    mode: allDay ? "allday" : "time",
    blocks,
    blocksBeforeAllDay: null,
    pin: session.pin,
    allowSourceTime: session.allowSourceTime,
    mintOverlap: session.mintOverlap,
  };
}

/** Switch scheduling mode.

    All-day captures the current duration into `blocksBeforeAllDay`, clears the
    pin, and drops the Mint-overlap and source-time grants (all-day rows have
    no start and cannot overlap Mint). Returning to time block restores the
    captured duration and leaves the pin cleared — an old pin is never
    resurrected. A row that arrived already all-day has no captured duration,
    so it restores its current (0) blocks, exactly like the prototype. */
export function setMode(draft: RowDraft, mode: RowMode): RowDraft {
  if (mode === draft.mode) return draft;
  if (mode === "allday") {
    return {
      ...draft,
      mode: "allday",
      blocksBeforeAllDay: draft.blocks,
      pin: null,
      allowSourceTime: false,
      mintOverlap: false,
    };
  }
  return {
    ...draft,
    mode: "time",
    blocks: draft.blocksBeforeAllDay ?? draft.blocks,
  };
}

/** Whether a pin may apply to this row at all. A protected native time needs
    the operator's explicit source-time permission first. */
export function canApplyPin(draft: RowDraft, row: RowFacts): boolean {
  return !isProtectedNative(row) || draft.allowSourceTime;
}

/** Stage a pinned start. A malformed time, a protected row without
    permission, or an all-day row leaves the draft untouched (the UI disables
    Apply in those states; this is the invariant behind that). */
export function setPin(draft: RowDraft, row: RowFacts, time: string): RowDraft {
  if (!HHMM.test(time)) return draft;
  if (draft.mode === "allday") return draft;
  if (!canApplyPin(draft, row)) return draft;
  return { ...draft, pin: time };
}

/** Clear the pin: the row goes back to automatic start. */
export function clearPin(draft: RowDraft): RowDraft {
  return draft.pin === null ? draft : { ...draft, pin: null };
}

/** What the draft would change against its committed session, in field order.
    `staged` false means Save has nothing to do. */
export function diff(draft: RowDraft, session: RowSession): DraftDiff {
  const baselineBlocks = session.override?.blocks ?? session.row.blocks;
  const baselineMode: RowMode = baselineBlocks <= 0 ? "allday" : "time";
  const changes: DraftChange[] = [];
  if (draft.mode !== baselineMode) {
    changes.push({ field: "mode", from: baselineMode, to: draft.mode });
  }
  const blocks = draftBlocks(draft);
  if (blocks !== baselineBlocks) {
    changes.push({ field: "blocks", from: baselineBlocks, to: blocks });
  }
  if (draft.pin !== session.pin) {
    changes.push({ field: "pin", from: session.pin, to: draft.pin });
  }
  if (draft.allowSourceTime !== session.allowSourceTime) {
    changes.push({
      field: "allowSourceTime",
      from: session.allowSourceTime,
      to: draft.allowSourceTime,
    });
  }
  if (draft.mintOverlap !== session.mintOverlap) {
    changes.push({
      field: "mintOverlap",
      from: session.mintOverlap,
      to: draft.mintOverlap,
    });
  }
  return { staged: changes.length > 0, changes };
}

/** Project the draft onto the controller calls a Save would fire, in
    application order. A null pin becomes `resetPlacement` (automatic), not a
    clear-to-source-time special case: the store re-adds a protected native
    pin on its own when the row is not opted in. */
export function toOverrides(draft: RowDraft): RowOperation[] {
  const operations: RowOperation[] = [
    {
      type: "setOverride",
      id: draft.id,
      included: true,
      blocks: draftBlocks(draft),
    },
  ];
  if (draft.pin === null) {
    operations.push({ type: "resetPlacement", id: draft.id });
  } else {
    operations.push({ type: "placeRow", id: draft.id, start: draft.pin });
  }
  operations.push({
    type: "setTimeAdjustmentOptIn",
    id: draft.id,
    allow: draft.allowSourceTime,
  });
  operations.push({
    type: "setMintOverlapOptIn",
    id: draft.id,
    allow: draft.mintOverlap,
  });
  return operations;
}
