/* adapter.ts — the swappable data boundary (locked decision 12).
   `fixture` implements deterministic scenario data for the mockup/approval
   builds; `api` (T5, not built until Adam approves the mockup) talks to the
   FastAPI endpoints. Component code only ever sees this interface. */

import type {
  CalendarDrift,
  Capacity,
  CapacitiesCatalog,
  CapacitiesRefreshMode,
  CapacitiesRefreshStatus,
  CapacitiesRuleNode,
  CapacitiesRuleSaveResponse,
  CapacitiesRules,
  CapacitiesSelections,
  CapacitiesSelectionsDraft,
  CapacitiesSettings,
  CapacitiesSettingsDraft,
  CapacitiesSourceDraft,
  CapacitiesSourceRead,
  CommitReport,
  DaySetup,
  DaySetupSaveResult,
  FixedInputs,
  Ledger,
  PlanInputs,
  SequenceRow,
  TagExclusionSettings,
  TagExclusionSettingsDraft,
  Validation,
  OverlapGrant,
} from "../model/types";

export interface SequenceResult {
  sequence: SequenceRow[];
  warnings: string[];
  overlapGrants: OverlapGrant[];
  /** T27: server's effective pin set (client pins + recurring auto-pins).
      Absent from adapters that don't compute it (fixture mode). */
  pinnedRows?: SequenceRow[];
}

/** Today-only shaping context for POST bodies (locked decision 16): the
    included assigned rows with their EFFECTIVE blocks (override or resolved).
    The API adapter maps these onto the raw wire digest rows; excluded rows
    never reach a payload. */
export interface SequenceContext {
  included: Array<{ id: string; blocks: number }>;
  /** Explicit per-item permission for moving an existing Todoist time. */
  timeAdjustmentOptIns?: Record<string, boolean>;
  /** #18: explicit per-row operator grant to overlap a selected Mint session. */
  mintOverlapOptIns?: Record<string, boolean>;
  planningConfigFingerprint?: string;
  overlapGrants?: OverlapGrant[];
  pinnedRows?: SequenceRow[];
}

/** Explicit source refresh result (locked decision 20): one fresh
    /plan-inputs read projected BOTH ways (model + fixed-input snapshot) so
    the reconciliation and the drift fingerprints share a single snapshot,
    plus the current billed ledger. */
export interface SourceRefreshResult {
  inputs: PlanInputs;
  fixed: FixedInputs;
  ledger: Ledger;
}

/** One explicit duration-memory save result (MVP): the server-authoritative
    remembered value. `source` is always "remembered" — a successful save
    makes the row durable-remembered by definition. */
export interface DurationMemorySaveResult {
  identity: string;
  minutes: number;
  source: "remembered";
}

/** One explicit duration-memory reset result (MVP): the current
    source-resolved fallback the row should now use, with its source label.
    `minutes` is null when the server found no fallback (found:false) — the
    caller must preserve authoritative state, never apply zero. */
export interface DurationMemoryResetResult {
  identity: string;
  minutes: number | null;
  source: import("../model/types").DurationSourceLabel;
}

/** One explicit anchored-start save (config write). Times are 24h HH:MM, the
    cockpit's wire-neutral form; the server stores "5:45 PM"-style text. */
export interface AnchoredStartSaveResult {
  block: string;
  start: string;
  previousStart: string | null;
}

/** One item of a POST /runtime-batches body: the same verb/target/args tuple
    /runtime-actions accepts, so a batch never invents a second vocabulary. */
export interface RuntimeBatchItemInput {
  verb: string;
  target: string;
  args?: Record<string, unknown>;
}

/** Per-item outcome status from a runtime batch. `refused` is a pre-write
    refusal (the verb is not legal for the target), `failed` is a write that
    threw, `duplicate` is an idempotent replay of an earlier action. */
export type RuntimeBatchStatus = "applied" | "failed" | "duplicate" | "refused";

/** One item's result inside a batch. `target` is the submitted target string,
    echoed by the server. */
export interface RuntimeBatchItemResult {
  index: number;
  verb: string;
  target: string;
  status: RuntimeBatchStatus;
  error: string | null;
}

/** POST /runtime-batches and /{id}/retry response: the batch id plus one
    result per submitted item, in submission order. */
export interface RuntimeBatchResult {
  batchId: string;
  results: RuntimeBatchItemResult[];
}

/** One item the batch undo reverted, with the journal action it reversed. */
export interface RuntimeBatchUndoReverted {
  item: number;
  verb: string;
  target: string;
  actionId: string;
}

/** One item the batch undo could NOT revert, with the server's reason text. */
export interface RuntimeBatchUndoBlocked {
  item: number;
  verb: string;
  target: string;
  reason: string;
}

/** POST /runtime-batches/{id}/undo response: what reverted, what a later edit
    made conflicting, and what has no safe reverse. */
export interface RuntimeBatchUndoReport {
  batchId: string;
  reverted: RuntimeBatchUndoReverted[];
  conflicts: RuntimeBatchUndoBlocked[];
  nonReversible: RuntimeBatchUndoBlocked[];
}

export interface Adapter {
  /** GET /plan-inputs, projected assigned-only (digest.suggested dropped). */
  loadPlanInputs(): Promise<PlanInputs>;
  /** Explicit source refresh (locked decision 20): exactly one GET
      /plan-inputs + one GET /billed-ledger. Never touches /gather or any
      billed/write endpoint. Throws on a degraded fixed-source read — a
      failed refresh must not poison fingerprints or the last good view. */
  refreshSources(): Promise<SourceRefreshResult>;
  /** GET /billed-ledger — server-authoritative billed budget. */
  billedLedger(): Promise<Ledger>;
  /** GET /calendar-drift — read-only comparison of today's committed blocks
      against the live Calendar. Tokenless GET; it never writes, adopts, or
      re-commits anything, and never touches a billed endpoint. */
  calendarDrift(): Promise<CalendarDrift>;
  /** GET /capacity-preview — server-verbatim numbers for proposed edits. */
  capacityPreview(daySetup: DaySetup, selectedBlocks: number[]): Promise<Capacity>;
  /** POST /day-setup — persist session/day-scoped setup, never persistent config. */
  saveDaySetup(daySetup: DaySetup): Promise<void>;
  /** U5 POST /day-setup {captures} — PATCH the local prompt drafts.
      Prompt-only: never confirms the day. Custom prompt keys (custom-<id>)
      ride the same captures patch. */
  savePromptDrafts(patch: Record<string, string>): Promise<DaySetupSaveResult>;
  /** POST /day-setup {micro_adventure} — T19 free Live override (shuffle /
      pick / custom); null clears the dated override back to the auto-pick.
      Never billed, never a history write. */
  saveMicroAdventure(pick: import("../model/types").MicroIdea | null): Promise<void>;
  /** POST /micro-adventure/loose-end {date, id, done} — the Set up day banner's
      Did it (true) / Dismiss (false) for an unconfirmed prior Live block. Writes
      only the micro-adventure log; never billed, never touches today's plan. */
  resolveLooseEnd(entry: { date: string; id: string }, done: boolean): Promise<void>;
  /** GET /settings/capacities — local versioned policy, no provider call. */
  loadCapacitiesSettings(): Promise<CapacitiesSettings>;
  /** POST /settings/capacities/save — one token-guarded non-billed full
      replacement of the local policy. */
  saveCapacitiesSettings(draft: CapacitiesSettingsDraft): Promise<CapacitiesSettings>;
  /** GET /settings/capacities/source — tokenless read of the
      source mapping record; an absent record is `{source: null,
      persisted: false}`, never an error. */
  loadCapacitiesSource(): Promise<CapacitiesSourceRead>;
  /** POST /settings/capacities/source/save — one token-guarded non-billed
      full replacement of the source mapping record. */
  saveCapacitiesSource(draft: CapacitiesSourceDraft): Promise<CapacitiesSourceRead>;
  /** POST /settings/capacities/source/discover — one token-guarded non-billed
      provider read; returns the advisory structure/property catalog. A
      failure must surface as an error, never as an empty catalog. */
  discoverCapacitiesSource(spaceId: string): Promise<CapacitiesCatalog>;
  /** GET /capacities/rules — tokenless local read of the per-type Capacities
      inclusion rules and the discovered type schema. No provider call. */
  loadCapacitiesRules(): Promise<CapacitiesRules>;
  /** POST /capacities/rules — one token-guarded save of ONE per-type rule.
      The server stores an invalid rule as a draft and preserves the prior
      active rule; a stale ``expectedRevision`` is a 409. */
  saveCapacitiesRule(args: {
    structureId: string;
    rule: CapacitiesRuleNode;
    fallbackMinutes: number | null;
    expectedRevision: number;
  }): Promise<CapacitiesRuleSaveResponse>;
  /** GET /capacities/refresh/status — truthful local job + snapshot status.
      Tokenless and provider-free. */
  capacitiesRefreshStatus(): Promise<CapacitiesRefreshStatus>;
  /** POST /capacities/refresh/start — one token-guarded paced job start
      (``refresh`` or ``rescan``). A running job is a 409. */
  startCapacitiesRefresh(
    mode: CapacitiesRefreshMode,
    scope: string,
  ): Promise<CapacitiesRefreshStatus>;
  /** POST /capacities/refresh/cancel — token-guarded cancellation of the
      running job; returns the current truthful status. */
  cancelCapacitiesRefresh(): Promise<CapacitiesRefreshStatus>;
  /** GET /capacities/selections — tokenless local read of the durable
      per-identity selections for the source space. */
  loadCapacitiesSelections(): Promise<CapacitiesSelections>;
  /** POST /capacities/selections — token-guarded merge with optimistic
      revision; a stale ``expectedRevision`` is a 409. */
  saveCapacitiesSelections(draft: CapacitiesSelectionsDraft): Promise<CapacitiesSelections>;
  /** GET /settings/exclusions — local tag-exclusion policy plus the advisory
      RootTag catalog. No provider call for the policy itself; a degraded
      catalog still returns the saved settings. */
  loadTagExclusionSettings(): Promise<TagExclusionSettings>;
  /** POST /settings/exclusions/save — one token-guarded non-billed full
      replacement of the local tag-exclusion policy. */
  saveTagExclusionSettings(draft: TagExclusionSettingsDraft): Promise<TagExclusionSettings>;
  /** POST /duration-memory/save — one token-guarded non-billed mutation
      (duration-memory MVP). Strict value validation happens BEFORE the call;
      the adapter never rounds, truncates, snaps, or coerces. */
  saveDurationMemory(identity: string, minutes: number): Promise<DurationMemorySaveResult>;
  /** POST /duration-memory/reset — one token-guarded non-billed mutation
      (duration-memory MVP). The response carries the current
      source-resolved fallback the row should apply. */
  resetDurationMemory(identity: string): Promise<DurationMemoryResetResult>;
  /** POST /config/anchored-blocks/start — one token-guarded non-billed write of
      ONE anchored block's Start into the tracked repo config. Never touches
      today's day setup. 404 unknown block, 409 shadowed by a local override,
      422 unparseable time surface as ApiError with the server message. */
  saveAnchoredStart(block: string, start: string): Promise<AnchoredStartSaveResult>;
  /** GET /config/micro-adventure/pool — read-only, tokenless: the CONFIG pool
      only (every entry, active or not, uncapped, no Capacities ideas). */
  loadMicroPool(): Promise<import("../model/types").MicroPoolItem[]>;
  /** POST /config/micro-adventure/pool — one token-guarded non-billed FULL
      REPLACE of the config pool. 422 invalid, 404 not found, 409 shadowed by
      the machine-local config / not editable, 500 write error surface as
      ApiError. Resolves with the pool as saved. */
  saveMicroPool(
    pool: import("../model/types").MicroPoolItem[],
  ): Promise<import("../model/types").MicroPoolItem[]>;
  /** POST /sequence — the ONE billed action. Never called automatically. */
  autoSequence(ctx: SequenceContext): Promise<SequenceResult>;
  /** POST /validate-sequence — deterministic, free. */
  validateSequence(rows: SequenceRow[], ctx: SequenceContext): Promise<Validation>;
  /** Re-read fixed inputs (calendar + anchored) for drift fingerprinting.
      Throws on source-read failure — a failure blocks preview/commit, it is
      never an "unchanged" fingerprint (locked decision 17). */
  readFixedInputs(): Promise<FixedInputs>;
  /** POST /commit?mode=shadow — writes nothing. Digest is shaped by ctx:
      excluded rows never reach the commit digest (T6). */
  shadowCommit(rows: SequenceRow[], ctx: SequenceContext): Promise<import("../model/types").ShadowDiff>;
  /** POST /commit?mode=live — the real writes. Only after ARM + second click. */
  liveCommit(rows: SequenceRow[], ctx: SequenceContext): Promise<CommitReport>;
  /** POST /runtime-actions (T20) — one journaled verb against one committed
      plan item; server resolves the item name to its own committed
      artifacts. Never available before a live commit. */
  runtimeAction(
    verb: string, target: string, args?: Record<string, unknown>,
  ): Promise<import("../model/types").RuntimeAction>;
  /** POST /runtime-actions/{id}/undo (T20) — one-step reverse from exact
      before-images. */
  undoRuntimeAction(actionId: string): Promise<import("../model/types").RuntimeAction>;
  /** KTD1: POST /runtime-actions/reassign {identity} — re-assign a Todoist row
      by reversing its own applied `unassign` in today's journal, restoring the
      exact prior due date. Deliberately NOT the `assign` verb: that one
      reschedules to "today", no-ops on recurring tasks, and leaves the
      unassign applied. Same journaled envelope as every other runtime action,
      so a conflict, a duplicate and a failure all surface the same way. */
  reassign(identity: string): Promise<import("../model/types").RuntimeAction>;
  /** POST /runtime-batches — apply items as one journaled batch. A per-item
      failure or refusal is REPORTED, never thrown; 409 (Day Setup not
      confirmed) and 422 (bad input) surface as ApiError through the shared
      error handling. */
  applyRuntimeBatch(items: RuntimeBatchItemInput[]): Promise<RuntimeBatchResult>;
  /** POST /runtime-batches/{id}/retry — re-run only the batch's unsettled
      items; already-applied/duplicate items are reported as-is. */
  retryRuntimeBatch(batchId: string): Promise<RuntimeBatchResult>;
  /** POST /runtime-batches/{id}/undo — reverse the applied items; a later
      edit and a non-reversible step are reported, never thrown. */
  undoRuntimeBatch(batchId: string): Promise<RuntimeBatchUndoReport>;
}
