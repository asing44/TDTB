/* adapter.ts — the swappable data boundary (locked decision 12).
   `fixture` implements deterministic scenario data for the mockup/approval
   builds; `api` (T5, not built until Adam approves the mockup) talks to the
   FastAPI endpoints. Component code only ever sees this interface. */

import type {
  Capacity,
  CapacitiesCatalog,
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

/** U5: the server's opt-in store changed since it was read. Carries both
    revisions so the caller can surface the conflict and keep the user's
    unsaved selection instead of overwriting saved preferences. */
export class PromptOptinConflictError extends Error {
  constructor(
    readonly expectedRevision: number,
    readonly currentRevision: number,
  ) {
    super(
      `prompt opt-ins changed since they were read (stored revision ${currentRevision}, expected ${expectedRevision})`,
    );
    this.name = "PromptOptinConflictError";
  }
}

/** Today-only shaping context for POST bodies (locked decision 16): the
    included assigned rows with their EFFECTIVE blocks (override or resolved).
    The API adapter maps these onto the raw wire digest rows; excluded rows
    never reach a payload. */
export interface SequenceContext {
  included: Array<{ id: string; blocks: number }>;
  /** Explicit per-item permission for moving an existing Todoist time. */
  timeAdjustmentOptIns?: Record<string, boolean>;
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
  /** GET /capacity-preview — server-verbatim numbers for proposed edits. */
  capacityPreview(daySetup: DaySetup, selectedBlocks: number[]): Promise<Capacity>;
  /** POST /day-setup — persist session/day-scoped setup, never vault config. */
  saveDaySetup(daySetup: DaySetup): Promise<void>;
  /** U5 POST /day-setup {captures} — PATCH the local prompt drafts.
      Prompt-only: never confirms the day. */
  savePromptDrafts(patch: Record<string, string>): Promise<DaySetupSaveResult>;
  /** U5 POST /day-setup {optins, optins_revision} — persist the undated
      opt-ins with optimistic concurrency. Never confirms the day. A stale
      revision throws PromptOptinConflictError. */
  savePromptOptins(
    optins: Record<string, boolean>,
    expectedRevision: number,
  ): Promise<DaySetupSaveResult>;
  /** POST /day-setup {micro_adventure} — T19 free Live override (shuffle /
      pick / custom); null clears the dated override back to the auto-pick.
      Never billed, never a history write. */
  saveMicroAdventure(pick: import("../model/types").MicroIdea | null): Promise<void>;
  /** GET /settings/capacities — local versioned policy, no provider call. */
  loadCapacitiesSettings(): Promise<CapacitiesSettings>;
  /** POST /settings/capacities/save — one token-guarded non-billed full
      replacement of the local policy. */
  saveCapacitiesSettings(draft: CapacitiesSettingsDraft): Promise<CapacitiesSettings>;
  /** GET /settings/capacities/source — tokenless read of the vault-local
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
}
