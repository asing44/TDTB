/* controller.ts — async orchestration between adapter and store.
   Owns the safety choreography the reducer alone can't express:

   - Auto sequence is ONE explicit user action; fingerprint of fixed inputs
     is captured from a fresh read at sequence time.
   - Shadow preview and live commit each re-read fixed inputs FIRST; drift →
     FINGERPRINT_MISMATCH (staged plan invalidated), read failure → blocked,
     never treated as unchanged (locked decision 17).
   - Live commit dispatches only through requestLiveCommit, which re-checks
     canLiveCommit at call time — the second-click gate (locked decision 9). */

import type { Adapter, SequenceContext, SequenceResult } from "../adapters/adapter";
import type {
  RuntimeBatchItemInput,
  RuntimeBatchResult,
} from "../adapters/adapter";
import {
  ApiError,
  apiFailurePathOf,
  capacitiesSettingsConflictOf,
  exclusionSettingsConflictOf,
  isDeterministicRefusal,
  isNetworkError,
  SEQUENCE_NETWORK_ERROR_MESSAGE,
} from "../adapters/api";
import { itemIdentity, normalizeTodoistExclusionName, projectSequenceRow } from "../adapters/wire";
import { fingerprintFixedInputs } from "../model/fingerprint";
import { isStagingVerb } from "../model/staging";
import { prunePins } from "../model/pins";
import { planOverflow, calendarWalls, mintWalls } from "../model/overflow";
import {
  explicitMintCapMinutes,
  initialMintSessionIds,
  MINT_SESSION_MINUTES,
} from "../model/mint";
import type { WallInterval } from "../model/overflow";
import { isValidHHMM, toMinutes } from "../model/time";
import {
  batchEligibility,
  effectiveRowAction,
  skipInsteadCount,
  unassignVerb,
  type BatchAction,
  type BatchRow,
} from "../model/batchEligibility";
import {
  toOverrides,
  type RowDraft,
  type RowFacts,
} from "../model/rowDraft";
import { droppedItems } from "../model/placement";
import type { AppState, Action, ShadowErrorKind } from "./store";
import {
  batchRowsOf,
  canLiveCommit,
  canAutoSequence,
  defectsResolved,
  draftIsStaged,
  draftSeedOf,
  effectiveAnchoredBlocks,
  effectiveBlocks,
  emptyBatch,
  includedItems,
  membershipKeyOf,
  mergeNativeTimedPins,
  rowFactsOf,
  sourceHealthBlocked,
  stagedDraft,
  taskActionDisabledReason,
  type BatchItemOutcome,
  type BatchReviewState,
  type BatchState,
} from "./store";
import type {
  AnchoredOverride,
  HabitOverrides,
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
  DaySetup,
  DescriptionSkips,
  FixedInputs,
  MicroIdea,
  PlanInputs,
  SequenceRow,
  TagExclusionSettings,
  TagExclusionSettingsDraft,
  TodayOverride,
  TodoistExclusionEntry,
} from "../model/types";

export type Dispatch = (a: Action) => void;
export type GetState = () => AppState;

/** How long a deferred override waits for the drag to settle before it spends
    a capacity + validate round trip. Long enough to swallow a drag's worth of
    input events, short enough that releasing the thumb feels immediate. */
const OVERRIDE_SETTLE_MS = 140;

/** Issue #19 (part B): at most one window-focus drift check per 30 seconds.
    The explicit Check calendar button is never throttled. */
export const DRIFT_FOCUS_MIN_MS = 30_000;

/** A sequence attempt that never reached POST /sequence: the day's fixed
    inputs could not be read, or the session token could not be fetched. The
    user must be told plainly that nothing was billed — the billed-attempt
    warning would be false here. */
export const SEQUENCE_NOT_REQUESTED_MESSAGE =
  "The sequence was not requested and no billed call was made.";

/** Deterministic chronological sort key for a final sequence (FEEDBACK-01).
    HH:MM strings compare lexicographically; id breaks ties so equal starts
    stay stable and match the server's merge_immutable_rows ordering. */
function byStartThenId(a: SequenceRow, b: SequenceRow): number {
  if (a.start !== b.start) return a.start < b.start ? -1 : 1;
  if (a.id !== b.id) return a.id < b.id ? -1 : 1;
  return 0;
}

/** Strict duration-memory value rule (MVP): a whole number of minutes, at
    least zero, on the 5-minute grid. Never coerces — bool, fraction,
    negative, and off-grid values are rejected exactly as typed. */
export function isValidDurationMinutes(minutes: number): boolean {
  return Number.isInteger(minutes) && minutes >= 0 && minutes % 5 === 0;
}

// -- task-actions F3b: batch contract ---------------------------------------

/** Planning-only actions reshape the session and never reach the server. */
export type PlanningBatchAction =
  | "duration"
  | "allDay"
  | "timeBlock"
  | "automaticPlacement"
  | "mintAllow"
  | "mintDisallow";

export function isPlanningBatchAction(
  action: BatchAction,
): action is PlanningBatchAction {
  return (
    action === "duration" ||
    action === "allDay" ||
    action === "timeBlock" ||
    action === "automaticPlacement" ||
    action === "mintAllow" ||
    action === "mintDisallow"
  );
}

/** The runtime verb one source-changing batch action resolves to per row.
    Unassign splits by row kind: a recurring task advances its due date (Skip
    occurrence) instead of clearing assignment. These are the server's own
    verb names (app/runtime_actions.py), never a second vocabulary. */
function batchVerb(action: BatchAction, row: BatchRow): string {
  // A Capacities row's Complete resolves to Skip today before the wire: the
  // server's `done` refuses a row with no completable source, and Capacities
  // write-back stays out of scope. Same durable `drop_from_plan` verb Skip
  // today uses, so Restore today and the batch undo surface already apply.
  switch (effectiveRowAction(action, row)) {
    case "complete":
      return "done";
    case "skipToday":
      return "drop_from_plan";
    case "hideAlways":
      return "hide_always";
    case "unassign":
      return unassignVerb(row);
    default:
      throw new Error(`${action} is a planning-only action, not a runtime verb`);
  }
}

/** Written effect line for the review panel. Exact enough to apply blind. */
const BATCH_EFFECT_LABELS: Record<BatchAction, string> = {
  complete: "Complete",
  skipToday: "Skip today for",
  unassign: "Unassign",
  hideAlways: "Hide always",
  duration: "Set the duration of",
  automaticPlacement: "Reset automatic placement for",
  allDay: "Make all-day",
  timeBlock: "Make a time block of",
  mintAllow: "Allow Mint overlap for",
  mintDisallow: "Disallow Mint overlap for",
};

function batchEffects(
  action: BatchAction,
  count: number,
  args: { blocks?: number; capacitiesSkips?: number },
): string {
  // A mixed Complete says both halves: the rows that are done in source and
  // the Capacities rows that are only skipped for today.
  const skips = action === "complete" ? (args.capacitiesSkips ?? 0) : 0;
  if (skips > 0) {
    const completes = count - skips;
    const parts: string[] = [];
    if (completes > 0) {
      parts.push(`Complete ${completes} ${completes === 1 ? "row" : "rows"}`);
    }
    parts.push(
      `Skip today for ${skips} Capacities ${skips === 1 ? "row" : "rows"} (Capacities is not updated)`,
    );
    return parts.join("; ");
  }
  const noun = count === 1 ? "row" : "rows";
  const base = `${BATCH_EFFECT_LABELS[action]} ${count} ${noun}`;
  return action === "duration" && typeof args.blocks === "number"
    ? `${base} (${Math.round(args.blocks * 30)} min)`
    : base;
}

/** Server per-item results -> store outcomes. `id` restores the row key the
    item was built from; the server only echoes the target string. */
function batchOutcomes(
  result: RuntimeBatchResult,
  itemIds: string[],
): BatchItemOutcome[] {
  return result.results.map((item) => ({
    index: item.index,
    id: itemIds[item.index] ?? item.target,
    verb: item.verb,
    target: item.target,
    status: item.status,
    error: item.error,
  }));
}

/** T28 + FEEDBACK-09: a calendar row's day-setup entry is plan participation
    (skipToday) plus a local accounting projection (blocks) — time can never
    reach an imported event (LD19), and the projection crosses so Calendar
    impact can count it today only. Omitted when absent, keeping the T28 wire
    shape byte-identical for plain attendance toggles. */
function calendarParticipation(
  o: AppState["daySetup"]["anchored"][string],
): AnchoredOverride {
  const out: AnchoredOverride = {
    on: true,
    skipToday: o.skipToday === true,
    time: null,
  };
  if (o.blocks != null) out.blocks = o.blocks;
  // Local-only start edit: kept with the calendar's start at edit time. It is
  // day-setup data on our own server, never a calendar write.
  if (o.start != null) {
    out.start = o.start;
    if (o.calendarStartAtEdit != null) out.calendarStartAtEdit = o.calendarStartAtEdit;
  }
  return out;
}

/** FEEDBACK-28: normalize Mint at every frontend boundary. Saved session
    ids can be stale after calendar changes, and the server's capacity is
    reserved from the same effective allotment represented by the setup.
    Concrete rows are filtered against fixed/work walls before on/n/sessions
    or workAllotmentMinutes leave the client. With no concrete rows, stale
    session ids are removed and the aggregate allotment is preserved. */
function normalizeMintSetup(
  inputs: PlanInputs,
  daySetup: DaySetup,
  walls: WallInterval[],
): DaySetup {
  const minting = daySetup.schedulable?.minting;
  if (!minting) return daySetup;
  const sessions = inputs.daySemantics.mintSessions ?? [];
  const hasNumericOverride =
    Object.prototype.hasOwnProperty.call(daySetup, "workAllotmentMinutes") &&
    typeof daySetup.workAllotmentMinutes === "number";
  const rawMinutes = hasNumericOverride
    ? daySetup.workAllotmentMinutes
    : inputs.daySemantics.effectiveAllotmentMinutes;
  const requested = typeof rawMinutes === "number" && Number.isFinite(rawMinutes)
    ? Math.max(0, rawMinutes)
    : 0;

  if (sessions.length === 0) {
    // An empty concrete-session set cannot satisfy a saved id list. Fall back
    // to the aggregate contract so capacity and the setup do not disagree.
    const { sessions: _staleSessions, ...withoutSessions } = minting;
    const minutes = minting.on === false ? 0 : requested;
    const out: DaySetup = {
      ...daySetup,
      schedulable: {
        ...daySetup.schedulable,
        minting: {
          ...withoutSessions,
          on: minutes > 0,
          n: minutes / MINT_SESSION_MINUTES,
        },
      },
    };
    if (hasNumericOverride) {
      out.workAllotmentMinutes = minutes;
    } else if (Object.prototype.hasOwnProperty.call(daySetup, "workAllotmentMinutes")) {
      // null means reset to the resolved config value; preserve that field
      // presence instead of turning a preset-derived zero into an override.
      out.workAllotmentMinutes = null;
    }
    return out;
  }

  // Only a saved numeric allotment (or a resolved zero) caps the default
  // fill; a configured or preset default gates Mint on and off only.
  const selected = initialMintSessionIds(
    sessions,
    minting,
    explicitMintCapMinutes(daySetup, inputs.daySemantics.effectiveAllotmentMinutes),
    daySetup.anchor ?? inputs.time.anchor,
    walls,
  );
  const minutes = selected.length * MINT_SESSION_MINUTES;
  return {
    ...daySetup,
    workAllotmentMinutes: minutes,
    schedulable: {
      ...daySetup.schedulable,
      minting: {
        ...minting,
        on: selected.length > 0,
        n: selected.length,
        sessions: selected,
      },
    },
  };
}

function sanitizeMinting(
  s: AppState,
  daySetup: AppState["daySetup"],
): AppState["daySetup"] {
  if (!s.inputs) return daySetup;
  return normalizeMintSetup(
    s.inputs,
    daySetup,
    calendarWalls(effectiveAnchoredBlocks(s)),
  );
}

/** Recovery class of a preview failure. A typed 4xx refusal can never succeed
    on a resend of the identical sequence, so the drawer offers a rebuild; a
    transport failure is worth retrying; anything else (5xx, unreadable body,
    a bare client Error) is unknown and keeps Retry — fail safe. */
function shadowFailureKind(e: unknown): ShadowErrorKind {
  if (isDeterministicRefusal(e)) return "deterministic";
  if (isNetworkError(e)) return "transport";
  return "unknown";
}

export class Controller {
  // Monotonic request tokens: rapid edits fire overlapping revalidate/capacity
  // calls; a stale response landing after a newer one must never overwrite it.
  private revalidateToken = 0;
  private capacityToken = 0;
  private overrideTimer: ReturnType<typeof setTimeout> | null = null;
  // Capacities refresh-job start signals: any surface that starts the shared
  // job through this controller (the Connections panel and the header both
  // do) announces it here, so the header's job hook can attach its bounded
  // status poll to a run it did not start.
  private capacitiesRefreshStartSignals = new Set<() => void>();
  /** Issue #19 (part B): one read-only drift check in flight at a time, and
      the timestamp of the last window-focus-triggered check. */
  private driftInFlight = false;
  private lastDriftFocusAt: number | null = null;

  constructor(
    private adapter: Adapter,
    private dispatch: Dispatch,
    private getState: GetState,
    /** Injectable clock for the focus throttle. Tests control it; the app
        uses Date.now. */
    private now: () => number = () => Date.now(),
  ) {}

  /** Today-only shaping context for adapter POST bodies: included rows with
      their effective blocks (locked decision 16). */
  private sequenceContext(pinnedRows?: SequenceRow[]): SequenceContext {
    const s = this.getState();
    const included = includedItems(s).map((i) => ({
      id: i.id,
      blocks: effectiveBlocks(s, i.id),
    }));
    // T12 qualification (2026-07-26): pins persist to localStorage and
    // rehydrate via SESSION_RESTORED with no check against the freshly loaded
    // pool, so a pin outlives its item. The server then rejects the WHOLE
    // request — "foreign pinned row" — and the block is unrecoverable from
    // the UI: the row it names is gone from the queue, so there is nothing
    // left to press. Reconcile against the set actually being sent; a pin
    // naming an item we are not sending is meaningless by construction.
    const includedIds = new Set(included.map((i) => i.id));
    const pins = prunePins(
      (pinnedRows ?? s.pinnedRows).filter((row) => includedIds.has(row.id)),
    );
    return {
      included,
      timeAdjustmentOptIns: s.timeAdjustmentOptIns,
      mintOverlapOptIns: s.mintOverlapOptIns,
      planningConfigFingerprint:
        s.planningConfigFingerprint ?? s.inputs?.planningConfigFingerprint ?? "",
      overlapGrants: s.overlapGrants,
      pinnedRows: s.inputs
        ? mergeNativeTimedPins(pins, s.inputs, s.timeAdjustmentOptIns, includedIds)
        : pins,
    };
  }

  async load(): Promise<void> {
    try {
      const [inputs, ledger] = await Promise.all([
        this.adapter.loadPlanInputs(),
        this.adapter.billedLedger(),
      ]);
      const normalizedInputs = {
        ...inputs,
        daySetup: normalizeMintSetup(inputs, inputs.daySetup, calendarWalls(inputs.anchored)),
      };
      this.dispatch({ type: "INPUTS_LOADED", inputs: normalizedInputs, ledger });
    } catch (e) {
      this.dispatch({ type: "LOAD_FAILED", error: String(e instanceof Error ? e.message : e) });
    }
  }

  /** Explicit source refresh (locked decision 20): re-GET /plan-inputs +
      /billed-ledger, reconcile, then re-earn capacity/validation through the
      deterministic free endpoints. GETs only — the adapter method cannot
      reach /gather or a billed/write endpoint. */
  async refreshSources(): Promise<void> {
    const s = this.getState();
    if (
      s.refresh.phase === "loading" ||
      s.seqPhase === "sequencing" ||
      s.shadowPhase === "loading" ||
      s.commitPhase === "committing"
    ) {
      return;
    }
    this.dispatch({ type: "SOURCE_REFRESH_START" });
    try {
      const { inputs, fixed, ledger } = await this.adapter.refreshSources();
      const normalizedInputs = {
        ...inputs,
        daySetup: normalizeMintSetup(inputs, inputs.daySetup, calendarWalls(inputs.anchored)),
      };
      this.dispatch({
        type: "SOURCE_REFRESH_OK",
        inputs: normalizedInputs,
        ledger,
        fingerprint: fingerprintFixedInputs(fixed),
        anchoredSourceFingerprint: fixed.anchoredSourceFingerprint,
        planningConfigFingerprint: fixed.planningConfigFingerprint,
        at: new Date().toISOString(),
      });
      // Free deterministic follow-ups: server-verbatim capacity for the
      // reconciled include set; surviving staged rows re-earn "valid".
      void this.refreshCapacity();
      void this.revalidate();
    } catch (e) {
      this.dispatch({
        type: "SOURCE_REFRESH_FAIL",
        error: String(e instanceof Error ? e.message : e),
      });
    }
  }

  async loadCapacitiesSettings(): Promise<CapacitiesSettings> {
    return this.adapter.loadCapacitiesSettings();
  }

  async saveCapacitiesSettings(draft: CapacitiesSettingsDraft): Promise<CapacitiesSettings> {
    return this.adapter.saveCapacitiesSettings(draft);
  }

  async loadCapacitiesSource(): Promise<CapacitiesSourceRead> {
    return this.adapter.loadCapacitiesSource();
  }

  async saveCapacitiesSource(draft: CapacitiesSourceDraft): Promise<CapacitiesSourceRead> {
    return this.adapter.saveCapacitiesSource(draft);
  }

  /** Discover the space's real structures and properties for the mapping
      editor. Read-only and advisory; a failure propagates untouched so the
      caller can tell it apart from an empty catalog. */
  async discoverCapacitiesSource(spaceId: string): Promise<CapacitiesCatalog> {
    return this.adapter.discoverCapacitiesSource(spaceId);
  }

  /** Read the per-type Capacities inclusion rules (local; no provider call). */
  async loadCapacitiesRules(): Promise<CapacitiesRules> {
    return this.adapter.loadCapacitiesRules();
  }

  /** Save ONE per-type rule. The server stores an invalid rule as a draft and
      preserves the prior active rule; a stale revision is a 409. */
  async saveCapacitiesRule(args: {
    structureId: string;
    rule: CapacitiesRuleNode;
    fallbackMinutes: number | null;
    expectedRevision: number;
  }): Promise<CapacitiesRuleSaveResponse> {
    return this.adapter.saveCapacitiesRule(args);
  }

  /** Truthful local refresh status (tokenless; no provider call). */
  async loadCapacitiesRefreshStatus(): Promise<CapacitiesRefreshStatus> {
    return this.adapter.capacitiesRefreshStatus();
  }

  /** Start one paced Refresh/Rescan job (explicit user action). Announces
      the start to subscribers after the adapter accepts it, whatever surface
      called it, so every observer can track the SAME shared job. */
  async startCapacitiesRefresh(
    mode: CapacitiesRefreshMode,
    scope: string,
  ): Promise<CapacitiesRefreshStatus> {
    const status = await this.adapter.startCapacitiesRefresh(mode, scope);
    for (const listener of [...this.capacitiesRefreshStartSignals]) {
      try {
        listener();
      } catch {
        // A listener must never make a successful start look failed.
      }
    }
    return status;
  }

  /** Subscribe to Capacities refresh-job starts from ANY surface. Returns an
      unsubscribe function; listeners run synchronously after a successful
      start. A rejected start (for example a single-flight 409) signals
      nothing — callers handle that themselves. */
  subscribeCapacitiesRefreshStart(listener: () => void): () => void {
    this.capacitiesRefreshStartSignals.add(listener);
    return () => {
      this.capacitiesRefreshStartSignals.delete(listener);
    };
  }

  /** Cancel the running refresh job; returns the current truthful status. */
  async cancelCapacitiesRefresh(): Promise<CapacitiesRefreshStatus> {
    return this.adapter.cancelCapacitiesRefresh();
  }

  /** Read the durable per-identity Capacities selections (local read). */
  async loadCapacitiesSelections(): Promise<CapacitiesSelections> {
    return this.adapter.loadCapacitiesSelections();
  }

  /** Merge selections with optimistic concurrency; a stale revision is 409.
      After a successful save the source is re-read through the SAME refresh
      path an explicit refresh uses, so the promoted selection reaches the
      server digest (and the Commit body) instead of living only in the UI.
      Returns the saved record plus the refresh outcome so the caller can say
      "Saved; planning refresh failed" rather than showing stale eligibility. */
  async saveCapacitiesSelections(
    draft: CapacitiesSelectionsDraft,
  ): Promise<{ selections: CapacitiesSelections; refreshError: string | null }> {
    const selections = await this.adapter.saveCapacitiesSelections(draft);
    await this.refreshSources();
    return { selections, refreshError: this.getState().refresh.error };
  }

  async loadTagExclusionSettings(): Promise<TagExclusionSettings> {
    return this.adapter.loadTagExclusionSettings();
  }

  /** Save the tag exclusion policy, then re-earn planning inputs through the
      SAME refresh/reconcile path as an explicit source refresh (locked
      decision 20). Returns the saved policy plus the refresh outcome so the
      drawer can report "Saved; planning refresh failed" instead of
      presenting stale eligibility as current. A save that throws never
      refreshes and never mutates store state. */
  async saveTagExclusionSettings(
    draft: TagExclusionSettingsDraft,
  ): Promise<{ settings: TagExclusionSettings; refreshError: string | null }> {
    const settings = await this.adapter.saveTagExclusionSettings(draft);
    await this.refreshSources();
    return { settings, refreshError: this.getState().refresh.error };
  }

  /** "Always exclude" for one Todoist task: add it to the persistent
      exclusion list (id OR normalized-name match) and re-earn planning
      inputs through the SAME refresh path as the tag-exclusion save. An
      entry already present (same id or normalized name) is already
      excluded: the read is adopted and no write is attempted. A 409
      stale-revision conflict reloads and retries ONCE, then surfaces. */
  async alwaysExcludeTodoist(input: {
    todoistId: string;
    name: string;
  }): Promise<{ settings: TagExclusionSettings; refreshError: string | null }> {
    const entry: TodoistExclusionEntry = {
      id: input.todoistId,
      name: input.name,
      addedAt: new Date().toISOString(),
    };
    const settings = await this.writeTodoistExclusion(entry);
    await this.refreshSources();
    return { settings, refreshError: this.getState().refresh.error };
  }

  private async writeTodoistExclusion(
    entry: TodoistExclusionEntry,
  ): Promise<TagExclusionSettings> {
    let settings = await this.adapter.loadTagExclusionSettings();
    const normalized = normalizeTodoistExclusionName(entry.name);
    const present = settings.todoist.some(
      (row) => row.id === entry.id || normalizeTodoistExclusionName(row.name) === normalized,
    );
    if (present) return settings;
    const write = (current: TagExclusionSettings) =>
      this.adapter.saveTagExclusionSettings({
        expectedRevision: current.revision,
        tags: current.tags,
        todoist: [...current.todoist, entry],
      });
    try {
      return await write(settings);
    } catch (e) {
      if (exclusionSettingsConflictOf(e) === null) throw e;
      settings = await this.adapter.loadTagExclusionSettings();
      // Retry once on the reloaded revision; a second conflict surfaces.
      return await write(settings);
    }
  }

  /** "Always exclude" for one Capacities object: add its canonical identity
      to the EXISTING per-object exclusion set and save the full settings
      policy (same route, revision guard, and refresh path as the Connections
      save). A 409 reloads and retries once, then surfaces. */
  async alwaysExcludeCapacitiesObject(
    identity: string,
  ): Promise<{ settings: CapacitiesSettings; refreshError: string | null }> {
    const settings = await this.writeCapacitiesExclusion(identity);
    await this.refreshSources();
    return { settings, refreshError: this.getState().refresh.error };
  }

  private async writeCapacitiesExclusion(
    identity: string,
  ): Promise<CapacitiesSettings> {
    let settings = await this.adapter.loadCapacitiesSettings();
    if (settings.excluded.includes(identity)) return settings;
    const write = (current: CapacitiesSettings) =>
      this.saveCapacitiesSettings({
        expectedRevision: current.revision,
        nativeTaskAuto: { ...current.nativeTaskAuto },
        excluded: [...current.excluded, identity].sort(),
        activeStructures: [...current.activeStructures],
        nativeTaskStructures: [...current.nativeTaskStructures],
        activeStatuses: [...current.activeStatuses],
        assignedStructures: { ...current.assignedStructures },
        assignedOnStructures: { ...current.assignedOnStructures },
      });
    try {
      return await write(settings);
    } catch (e) {
      if (capacitiesSettingsConflictOf(e) === null) throw e;
      settings = await this.adapter.loadCapacitiesSettings();
      return await write(settings);
    }
  }

  async saveDaySetup(daySetup: AppState["daySetup"]): Promise<void> {
    // FEEDBACK-28: the Mint rows are filtered against current effective walls
    // BEFORE any payload leaves — saved state can be stale.
    const mintSafe = sanitizeMinting(this.getState(), daySetup);
    const calendarIds = new Set(
      (this.getState().inputs?.anchored ?? [])
        .filter((a) => a.kind === "calendar")
        .map((a) => a.id),
    );
    // T28 + FEEDBACK-09: a calendar row's override is plan participation plus
    // a local accounting projection — sanitized to the {attending | not-
    // attending, projection} shape so time/duration edits can never reach an
    // imported event (LD19). An explicit skipToday:false entry is kept (not
    // dropped) so a restore beats server rows that still carry the dismissal
    // until the next source refresh.
    const anchored = Object.fromEntries(
      Object.entries(mintSafe.anchored).map(([id, o]) =>
        calendarIds.has(id)
          ? [id, calendarParticipation(o)]
          : [id, o],
      ),
    );
    const sanitized = { ...mintSafe, anchored };
    await this.adapter.saveDaySetup(sanitized);
    this.dispatch({ type: "SETUP_SAVED", daySetup: sanitized });
    await this.refreshCapacity();
  }

  /** Persist an anchor's start to the tracked repo config (every day the
      anchor runs), then re-read planning inputs through the same refresh path
      as an explicit source refresh so the configured start equals the saved
      one. A refused write (404/409/422) throws the adapter error and leaves
      state untouched. `reloaded` is true only when the refreshed inputs now
      carry the saved start; otherwise the caller must say the config was
      written but the view is stale. Today's override is not edited here. */
  async saveAnchoredStart(
    id: string,
    start: string,
  ): Promise<{ reloaded: boolean; refreshError: string | null }> {
    const block = this.getState().inputs?.anchored.find((a) => a.id === id);
    if (!block || block.kind === "calendar") {
      throw new Error(`'${id}' is not a config anchor, so its time cannot be saved`);
    }
    await this.adapter.saveAnchoredStart(block.name, start);
    await this.refreshSources();
    const now = this.getState().inputs?.anchored.find((a) => a.id === id)?.start ?? null;
    return { reloaded: now === start, refreshError: this.getState().refresh.error };
  }

  /** U5: PATCH the local prompt drafts. Captures-only, so the server treats
      it as a prompt-only write that never confirms the day. Browser-defined
      custom keys (custom-<id>) ride the same patch. */
  async savePromptDrafts(patch: Record<string, string>): Promise<void> {
    this.dispatch({ type: "PROMPT_SAVE_START" });
    try {
      const result = await this.adapter.savePromptDrafts(patch);
      const captures = {
        ...this.getState().daySetup.captures,
        ...(typeof patch.intention === "string" ? { intention: patch.intention } : {}),
        ...(typeof patch.megan_nicety === "string" ? { forMeegy: patch.megan_nicety } : {}),
        ...(typeof patch.stoic_intention === "string" ? { stoic: patch.stoic_intention } : {}),
      };
      this.dispatch({
        type: "PROMPT_DRAFTS_SAVED",
        captures,
        warnings: result.promptWarnings,
      });
    } catch (e) {
      this.dispatch({
        type: "PROMPT_SAVE_ERROR",
        error: String(e instanceof Error ? e.message : e),
      });
    }
  }

  /** Persist which built-in prompts omit their Todoist description. The map is
      a full replace (an omitted key turns that flag off) and the save is
      prompt-only: it never changes the prompt text, never confirms the day,
      and never affects WHETHER a prompt exports. The server omits the map when
      nothing is skipped, so an absent echo is stored as no skips rather than
      fabricating or persisting an all-false map. */
  async savePromptSkips(skips: DescriptionSkips): Promise<void> {
    this.dispatch({ type: "PROMPT_SAVE_START" });
    try {
      const result = await this.adapter.savePromptSkips(skips);
      this.dispatch({
        type: "PROMPT_SKIPS_SAVED",
        descriptionSkips: result.descriptionSkips ?? { forMeegy: false, stoic: false },
        warnings: result.promptWarnings,
      });
    } catch (e) {
      this.dispatch({
        type: "PROMPT_SAVE_ERROR",
        error: String(e instanceof Error ? e.message : e),
      });
    }
  }

  /** T19: persist a Live micro-adventure override (shuffle / pool pick /
      custom text), or clear it (null) back to the deterministic auto-pick —
      pool[0] is the server's LRU auto-pick by contract. Free, unbilled,
      never a history write; the history log is consumed only by the
      authorized live commit. */
  async setMicroAdventure(pick: MicroIdea | null): Promise<void> {
    await this.adapter.saveMicroAdventure(pick);
    const pool = this.getState().inputs?.microAdventure.pool ?? [];
    this.dispatch({
      type: "MICRO_SET",
      pick: pick ?? pool[0] ?? null,
      source: pick === null ? "auto" : "override",
    });
  }

  /** Set up day loose-end banner: Did it (done=true) or Dismiss (done=false)
      for the unconfirmed prior Live block. The banner clears only after the
      log write succeeds, so a failed answer leaves it in place. */
  async resolveLooseEnd(pending: { date: string; id: string }, done: boolean): Promise<void> {
    await this.adapter.resolveLooseEnd(pending, done);
    this.dispatch({ type: "LOOSE_END_RESOLVED" });
  }

  // -- explicit duration memory (MVP) ---------------------------------------

  /** Canonical mutation identity for a row: the model's projected identity,
      else derived from the wire identity fields (todoist:<id> / Capacities identity).
      Null only when the row carries neither — name alone is never an
      identity, and such a row cannot be saved or reset. */
  private identityOf(id: string): string | null {
    const item = this.getState().inputs?.assigned.find((i) => i.id === id);
    if (!item) return null;
    return item.identity ?? itemIdentity(item);
  }

  /** Task-actions F3: the row identity a runtime verb targets. A skipped row
      is no longer in `assigned` — it lives in `droppedToday` — so restore
      resolves through the drop entry by identity or by name. */
  private droppedOf(id: string) {
    return (this.getState().inputs?.droppedToday ?? []).find(
      (d) => d.identity === id || d.name === id,
    );
  }

  // -- task-actions F3: row UI state ---------------------------------------

  /** Expand one row, collapsing whatever was open (single-expand by
      construction); null collapses everything. */
  expandRow(id: string | null): void {
    this.dispatch({ type: "ROW_EXPANDED", id });
  }

  /** Select/deselect one row. Selection is independent of expansion. */
  selectRow(id: string, selected: boolean): void {
    this.dispatch({ type: "ROW_SELECTED", id, selected });
  }

  /** Select every row in today's queue. */
  selectAllRows(): void {
    const ids = (this.getState().inputs?.assigned ?? []).map((i) => i.id);
    this.dispatch({ type: "ROWS_SELECTED", ids });
  }

  clearSelection(): void {
    this.dispatch({ type: "ROWS_SELECTED", ids: [] });
  }

  /** Task-actions F3: Skip today — the durable `drop_from_plan` verb, which
      writes the dated runstate `dropped[]` entry the plan-inputs read honors.
      Deliberately no local `included=false` override: the skip is durable and
      date-scoped, and the source refresh is what moves the row into the
      Skipped today list. */
  async skipToday(id: string): Promise<void> {
    const blocked = taskActionDisabledReason(this.getState(), "skip");
    if (blocked) {
      this.dispatch({ type: "RUNTIME_FAIL", error: blocked });
      return;
    }
    const identity = this.identityOf(id);
    if (!identity) {
      this.dispatch({
        type: "RUNTIME_FAIL",
        error: "This item has no stable identity to skip.",
      });
      return;
    }
    await this.applyRowVerb("drop_from_plan", identity);
  }

  /** Task-actions F3: Restore today — the `restore_today` verb reverses the
      dated drop. On success a stale today-only exclusion is cleared, because
      a restored row hidden by a leftover `included=false` override would read
      as still skipped. */
  async restoreToday(id: string): Promise<void> {
    const blocked = taskActionDisabledReason(this.getState(), "restore");
    if (blocked) {
      this.dispatch({ type: "RUNTIME_FAIL", error: blocked });
      return;
    }
    const dropped = this.droppedOf(id);
    const identity = dropped?.identity ?? this.identityOf(id);
    if (!identity) {
      this.dispatch({
        type: "RUNTIME_FAIL",
        error: "This item has no stable identity to restore.",
      });
      return;
    }
    if (!(await this.applyRowVerb("restore_today", identity))) return;
    const key = dropped?.name ?? id;
    const override = this.getState().overrides[key];
    if (override && !override.included) {
      this.setOverride(key, true, override.blocks ?? null);
    }
  }

  /** U3a: the assignment toggle's one entry point. `id` is whatever the call
site holds — a plan row id in the bands, a runtime identity in the
Unassigned section.

  A row the loaded queue still carries is a LOCAL plan-membership edit: the
toggle dispatches PLAN_MEMBERSHIP_SET and nothing else. No batch, no runtime
verb, no reassign, no source refresh — taking work out of (or back into)
today's plan is a browser-only decision, and the row moves between the bands
and the Unassigned section by local state alone.

  A row the queue no longer carries is already held back upstream, so the
reversible path stays the journaled one it always was:

  - Todoist → Assigned: `adapter.reassign`, which reverses that row's own
    applied unassign and restores the exact prior due date (KTD1). Never the
    `assign` verb: it writes "today", no-ops on recurring tasks, and leaves
    the unassign applied.
  - Capacities or calendar → Assigned: `restore_today`.

  Those paths refresh sources, because the refreshed read — not local state —
is what restores the row. Gating is the existing `taskActionDisabledReason`,
so before Day Setup is confirmed no upstream case can reach the server's 409. */
  async setAssigned(id: string, assigned: boolean): Promise<void> {
    const s = this.getState();
    const item = s.inputs?.assigned.find((i) => i.id === id);
    if (item) {
      // LOCAL-ONLY (U3a): the row is still in the loaded queue, so its plan
      // membership is entirely ours to decide. Nothing upstream is written.
      this.dispatch({
        type: "PLAN_MEMBERSHIP_SET",
        identity: membershipKeyOf(item),
        in: assigned,
      });
      return;
    }
    const blocked = taskActionDisabledReason(this.getState(), "complete");
    if (blocked) {
      this.dispatch({ type: "RUNTIME_FAIL", error: blocked });
      return;
    }
    const heldBack = (s.inputs?.unassignedToday ?? []).find(
      (r) => r.identity === id || r.name === id,
    );
    const dropped = this.droppedOf(id);
    const source = heldBack?.source ?? dropped?.source ?? null;
    if (source === null) {
      this.dispatch({
        type: "RUNTIME_FAIL",
        error: "This item has no source to assign.",
      });
      return;
    }
    // Already held back: nothing to write, and a second unassign would be the
    // journal's duplicate no-op rather than a real action.
    if (!assigned && (heldBack || dropped)) return;
    if (source === "todoist") {
      if (!assigned) {
        await this.runRowBatch(id, "unassign");
        return;
      }
      const identity = heldBack?.identity ?? dropped?.identity ?? null;
      if (!identity) {
        this.dispatch({
          type: "RUNTIME_FAIL",
          error: "This item has no stable identity to assign.",
        });
        return;
      }
      await this.applyReassign(identity);
      return;
    }
    // Capacities and calendar rows: the existing date-scoped drop, never a
    // source write. Restore clears a stale legacy `included=false` override
    // exactly as Restore today does.
    if (assigned) await this.restoreToday(dropped?.identity ?? id);
    else await this.skipToday(id);
  }

  /** KTD1: reverse one row's own journaled unassign. The server implements
      re-assign AS an undo of that row's unassign, so success is the undo
      envelope: a 200 whose action carries status `undone`, exactly as the
      undo route returns. Anything else — a thrown error, an "task due changed
      after unassign" conflict, any other status — is a failure, and a failure
      never refreshes. */
  private async applyReassign(identity: string): Promise<boolean> {
    this.dispatch({ type: "RUNTIME_START" });
    let action: import("../model/types").RuntimeAction;
    try {
      action = await this.adapter.reassign(identity);
      this.dispatch({ type: "RUNTIME_OK", action });
    } catch (e) {
      this.dispatch({
        type: "RUNTIME_FAIL",
        error: String(e instanceof Error ? e.message : e),
      });
      return false;
    }
    if (action.status !== "undone") {
      this.dispatch({
        type: "RUNTIME_FAIL",
        error: action.error ?? `reassign did not apply (${action.status})`,
      });
      return false;
    }
    await this.refreshSources();
    return true;
  }

  /** Task-actions F3: Hide always — the journaled `hide_always` verb, which
      writes the durable exclusion AND journals it. The settings-screen writers
      (alwaysExcludeTodoist / alwaysExcludeCapacitiesObject) stay for the
      Connections and tag panels; a row action never edits those policies
      directly, so it stays undoable. */
  async hideAlways(id: string): Promise<void> {
    const blocked = taskActionDisabledReason(this.getState(), "hide");
    if (blocked) {
      this.dispatch({ type: "RUNTIME_FAIL", error: blocked });
      return;
    }
    const identity = this.droppedOf(id)?.identity ?? this.identityOf(id);
    if (!identity) {
      this.dispatch({
        type: "RUNTIME_FAIL",
        error: "This item has no stable identity to hide.",
      });
      return;
    }
    await this.applyRowVerb("hide_always", identity);
  }

  /** One journaled row verb against a row identity, then a source refresh so
      the queue and the Skipped today list read what the server now holds.
      Failure handling mirrors stagingAction: a thrown adapter error and a
      200 carrying a non-`applied` status are both failures, and a failure
      never refreshes. Returns whether the verb applied. */
  private async applyRowVerb(verb: string, identity: string): Promise<boolean> {
    this.dispatch({ type: "RUNTIME_START" });
    try {
      const action = await this.adapter.runtimeAction(verb, identity);
      this.dispatch({ type: "RUNTIME_OK", action });
    } catch (e) {
      this.dispatch({
        type: "RUNTIME_FAIL",
        error: String(e instanceof Error ? e.message : e),
      });
      return false;
    }
    const last = this.getState().lastRuntimeAction;
    if (last && last.status !== "applied") {
      this.dispatch({
        type: "RUNTIME_FAIL",
        error: last.error ?? `${verb} did not apply (${last.status})`,
      });
      return false;
    }
    await this.refreshSources();
    return true;
  }

  // -- task-actions F3b: staged row drafts ---------------------------------

  /** Open (or re-seed) one row's shaping draft from the committed session. */
  openDraft(id: string): void {
    const draft = draftSeedOf(this.getState(), id);
    if (!draft) return;
    this.dispatch({ type: "DRAFT_SET", id, draft });
  }

  /** Mutate the open draft through the pure rowDraft transforms. The updater
      receives the live row facts, so `setMode`/`setPin`/`clearPin` keep their
      own invariants (protected native time, all-day rows) instead of having
      the controller re-derive them. */
  updateDraft(
    id: string,
    update: (draft: RowDraft, row: RowFacts) => RowDraft,
  ): void {
    const s = this.getState();
    const row = rowFactsOf(s, id);
    if (!row) return;
    const draft = s.drafts[id] ?? draftSeedOf(s, id);
    if (!draft) return;
    const next = update(draft, row);
    if (next !== draft) this.dispatch({ type: "DRAFT_SET", id, draft: next });
  }

  /** Whether the row has unsaved shaping (`diff(draft, session)` non-empty). */
  isDraftStaged(id: string): boolean {
    return draftIsStaged(this.getState(), id);
  }

  /** Save the staged draft: apply its exact controller operations, then drop
      the draft. An all-day save persists the duration it captured on the
      override, so returning to a time block restores it (never the old pin)
      even after a reload. */
  saveDraft(id: string): void {
    const staged = stagedDraft(this.getState(), id);
    if (!staged) return;
    if (!staged.diff.staged) {
      this.discardDraft(id);
      return;
    }
    const draft = staged.draft;
    const captured =
      draft.mode === "allday" && draft.blocks > 0 ? draft.blocks : null;
    for (const operation of toOverrides(draft)) {
      switch (operation.type) {
        case "setOverride":
          this.setOverride(operation.id, operation.included, operation.blocks, {
            blocksBeforeAllDay: captured,
          });
          break;
        case "placeRow":
          this.placeRow(operation.id, operation.start);
          break;
        case "resetPlacement":
          /* A null draft pin means "back to automatic start". `placements[id]`
             IS the pin (`rowSessionOf`), and ROW_PIN_RESET alone never touches
             it, so an existing pin has to be released instead or the row
             still saves as Pinned. When nothing is pinned the reset is
             already the whole story. */
          if (this.getState().placements[operation.id]) {
            this.releasePlacement(operation.id);
          } else {
            this.resetPlacement(operation.id);
          }
          break;
        case "setTimeAdjustmentOptIn":
          this.setTimeAdjustmentOptIn(operation.id, operation.allow);
          break;
        case "setMintOverlapOptIn":
          this.setMintOverlapOptIn(operation.id, operation.allow);
          break;
      }
    }
    this.discardDraft(id);
  }

  /** Throw the staged shaping away: the row keeps its committed session. */
  discardDraft(id: string): void {
    this.dispatch({ type: "DRAFT_CLEARED", id });
  }

  // -- task-actions F3b: batch surface -------------------------------------

  /** Build the review for an action over the current selection. A
      source-changing action stops at `review` — nothing is sent until
      applyBatch. A planning-only action applies staged shaping to the session
      immediately, because that is free (no server call) and revertible by
      another edit. */
  reviewBatch(action: BatchAction, args: { blocks?: number } = {}): void {
    const s = this.getState();
    const rows = batchRowsOf(s);
    if (rows.length === 0) return;
    const eligibility = batchEligibility(action, rows, {
      daySetupConfirmed: s.daySetup.confirmed,
    });
    const review: BatchReviewState = {
      eligible: eligibility.eligible,
      excluded: eligibility.excluded,
      label: eligibility.label,
      counts: eligibility.counts,
      effects: batchEffects(action, eligibility.counts.eligible, {
        ...args,
        capacitiesSkips: skipInsteadCount(action, rows, eligibility.eligible),
      }),
    };
    if (isPlanningBatchAction(action)) {
      this.applyPlanningBatch(action, eligibility.eligible, args);
      this.dispatch({
        type: "BATCH_SET",
        batch: { ...emptyBatch, action, phase: "done", review },
      });
      return;
    }
    this.dispatch({
      type: "BATCH_SET",
      batch: { ...emptyBatch, action, phase: "review", review },
    });
  }

  /** Apply the reviewed source-changing batch. One POST for the whole
      selection; per-item failures are kept (never thrown away) so retry can
      run exactly those, and sources refresh after so applied rows leave the
      queue. */
  async applyBatch(): Promise<void> {
    const batch = this.getState().batch;
    if (!batch.action || batch.phase !== "review" || !batch.review) return;
    if (isPlanningBatchAction(batch.action)) return;
    await this.runBatch(batch.action, batch.review.eligible, batch.review);
  }

  /** One Complete / Unassign / Skip occurrence / Hide always from a row runs
      as a batch of one: same verbs, same per-item results, same undo surface.
      Skip today deliberately stays the F3a immediate `drop_from_plan`. */
  async runRowBatch(id: string, action: BatchAction): Promise<void> {
    if (isPlanningBatchAction(action)) return;
    const s = this.getState();
    // The gate answer also covers the applying batch (F1): a row verb started
    // mid-apply would replace the in-flight batch slot with a second one, so
    // it refuses here, before any BATCH_SET or wire call.
    const gate = taskActionDisabledReason(s, "complete");
    if (gate !== null) {
      this.dispatch({ type: "RUNTIME_FAIL", error: gate });
      return;
    }
    const row = batchRowsOf(s, [id])[0];
    if (!row) return;
    const eligibility = batchEligibility(action, [row], {
      daySetupConfirmed: s.daySetup.confirmed,
    });
    const review: BatchReviewState = {
      eligible: eligibility.eligible,
      excluded: eligibility.excluded,
      label: eligibility.label,
      counts: eligibility.counts,
      effects: batchEffects(action, eligibility.counts.eligible, {
        capacitiesSkips: skipInsteadCount(action, [row], eligibility.eligible),
      }),
    };
    if (eligibility.eligible.length === 0) {
      this.dispatch({
        type: "RUNTIME_FAIL",
        error: eligibility.excluded[0]?.reason ?? "Nothing to apply.",
      });
      this.dispatch({
        type: "BATCH_SET",
        batch: { ...emptyBatch, action, phase: "review", review },
      });
      return;
    }
    await this.runBatch(action, eligibility.eligible, review);
  }

  /** Retry only the failures, keeping every earlier success. */
  async retryFailed(): Promise<void> {
    const batch = this.getState().batch;
    if (!batch.id || batch.phase !== "done") return;
    this.dispatch({ type: "BATCH_SET", batch: { ...batch, phase: "applying" } });
    try {
      const result = await this.adapter.retryRuntimeBatch(batch.id);
      const byIndex = new Map(batch.results.map((r) => [r.index, r]));
      for (const outcome of batchOutcomes(result, batch.results.map((r) => r.id))) {
        byIndex.set(outcome.index, outcome);
      }
      this.dispatch({
        type: "BATCH_SET",
        batch: {
          ...batch,
          phase: "done",
          results: [...byIndex.values()].sort((a, b) => a.index - b.index),
        },
      });
    } catch (e) {
      this.dispatch({
        type: "RUNTIME_FAIL",
        error: String(e instanceof Error ? e.message : e),
      });
      this.dispatch({ type: "BATCH_SET", batch });
      return;
    }
    await this.refreshSources();
  }

  /** Reverse the applied items. Conflicts (a later edit) and non-reversible
      steps are REPORTED into the batch, never thrown or silently retried. */
  async undoBatch(): Promise<void> {
    const batch = this.getState().batch;
    if (!batch.id) return;
    try {
      const report = await this.adapter.undoRuntimeBatch(batch.id);
      this.dispatch({
        type: "BATCH_SET",
        batch: {
          ...batch,
          undoReport: {
            reverted: report.reverted.map((r) => r.target),
            conflicts: report.conflicts.map((c) => ({
              target: c.target,
              reason: c.reason,
            })),
            nonReversible: report.nonReversible.map((c) => ({
              target: c.target,
              reason: c.reason,
            })),
          },
        },
      });
    } catch (e) {
      this.dispatch({
        type: "RUNTIME_FAIL",
        error: String(e instanceof Error ? e.message : e),
      });
      return;
    }
    await this.refreshSources();
  }

  /** Dismiss the review (and any results) without sending anything. */
  cancelReview(): void {
    this.dispatch({ type: "BATCH_SET", batch: emptyBatch });
  }

  /** Planning-only actions write session shaping only: no adapter call, no
      review phase, no server result to undo. */
  private applyPlanningBatch(
    action: PlanningBatchAction,
    ids: string[],
    args: { blocks?: number },
  ): void {
    const s = this.getState();
    for (const id of ids) {
      switch (action) {
        case "duration": {
          const blocks = args.blocks;
          this.setOverride(
            id,
            true,
            typeof blocks === "number" && blocks > 0 ? blocks : null,
          );
          break;
        }
        case "allDay": {
          const captured = effectiveBlocks(s, id);
          this.setOverride(id, true, 0, {
            blocksBeforeAllDay: captured > 0 ? captured : null,
          });
          break;
        }
        case "timeBlock": {
          const captured = s.overrides[id]?.blocksBeforeAllDay;
          this.setOverride(
            id,
            true,
            typeof captured === "number" && captured > 0 ? captured : 1,
          );
          break;
        }
        case "automaticPlacement":
          this.resetPlacement(id);
          break;
        case "mintAllow":
          this.setMintOverlapOptIn(id, true);
          break;
        case "mintDisallow":
          this.setMintOverlapOptIn(id, false);
          break;
      }
    }
  }

  /** Build the items, send them as one batch, keep every per-item result, then
      refresh sources. A thrown adapter error keeps the review open for a
      retry of the whole selection. */
  private async runBatch(
    action: BatchAction,
    ids: string[],
    review: BatchReviewState | null,
  ): Promise<void> {
    const rows = new Map(batchRowsOf(this.getState(), ids).map((row) => [row.id, row]));
    const items: RuntimeBatchItemInput[] = [];
    const itemIds: string[] = [];
    for (const id of ids) {
      const row = rows.get(id);
      const identity = this.identityOf(id);
      if (!row || !identity) continue;
      items.push({ verb: batchVerb(action, row), target: identity, args: {} });
      itemIds.push(id);
    }
    if (items.length === 0) return;
    const applying: BatchState = { ...emptyBatch, action, review, phase: "applying" };
    this.dispatch({ type: "BATCH_SET", batch: applying });
    try {
      const result = await this.adapter.applyRuntimeBatch(items);
      this.dispatch({
        type: "BATCH_SET",
        batch: {
          ...applying,
          id: result.batchId,
          phase: "done",
          results: batchOutcomes(result, itemIds),
        },
      });
    } catch (e) {
      this.dispatch({
        type: "RUNTIME_FAIL",
        error: String(e instanceof Error ? e.message : e),
      });
      this.dispatch({ type: "BATCH_SET", batch: { ...applying, phase: "review" } });
      return;
    }
    await this.refreshSources();
  }

  private durationMemoryFail(id: string, error: string): void {
    this.dispatch({ type: "DURATION_MEMORY_FAIL", id, error });
  }

  /** Explicit durable save (MVP): exactly ONE token-guarded non-billed
      mutation. Invalid values (bool, fraction, negative, off-grid) are
      blocked with NO network call and NO snapping; success is claimed only
      after the response lands, and the remembered value then becomes the
      row's effective duration (any same-day session override is dropped so
      the queue reflects what was actually saved). */
  async saveDurationMemory(id: string, minutes: number): Promise<void> {
    const s = this.getState();
    if (!s.inputs?.assigned.some((i) => i.id === id)) return;
    if (!isValidDurationMinutes(minutes)) {
      this.durationMemoryFail(
        id,
        "Duration must be whole minutes in 5-minute steps (e.g. 45, 60, 90).",
      );
      return;
    }
    const identity = this.identityOf(id);
    if (!identity) {
      this.durationMemoryFail(id, "This item has no stable identity to remember.");
      return;
    }
    this.dispatch({ type: "DURATION_MEMORY_START", id });
    try {
      const result = await this.adapter.saveDurationMemory(identity, minutes);
      this.dispatch({
        type: "DURATION_MEMORY_OK",
        id,
        minutes: result.minutes,
        source: result.source,
      });
      // The saved value is now the model's effective duration. Drop any
      // same-day session override so the queue shows the remembered value —
      // the session override system stays independent, it just no longer
      // shadows the value the user explicitly saved.
      const override = this.getState().overrides[id];
      if (override) {
        this.setOverride(id, override.included ?? true, null);
      }
    } catch (e) {
      this.durationMemoryFail(
        id,
        String(e instanceof Error ? e.message : e),
      );
    }
  }

  /** Explicit durable reset (MVP): exactly ONE token-guarded non-billed
      mutation. On success the returned source fallback is applied to the row
      (blocks + source label) and any same-day session override is dropped;
      on failure the last authoritative value stays untouched and nothing
      claims the reset. FT-05 F2: a reset that finds no source fallback
      (minutes null) NEVER becomes zero or All day — the remembered value
      stays authoritative and a bounded failure state surfaces. */
  async resetDurationMemory(id: string): Promise<void> {
    const s = this.getState();
    if (!s.inputs?.assigned.some((i) => i.id === id)) return;
    const identity = this.identityOf(id);
    if (!identity) {
      this.durationMemoryFail(id, "This item has no stable identity to reset.");
      return;
    }
    this.dispatch({ type: "DURATION_MEMORY_START", id });
    try {
      const result = await this.adapter.resetDurationMemory(identity);
      if (result.minutes == null) {
        this.durationMemoryFail(
          id,
          "No source duration was found after reset; the previous value is preserved.",
        );
        return;
      }
      this.dispatch({
        type: "DURATION_MEMORY_OK",
        id,
        minutes: result.minutes,
        source: result.source,
      });
      const override = this.getState().overrides[id];
      if (override) {
        this.setOverride(id, override.included ?? true, null);
      }
    } catch (e) {
      this.durationMemoryFail(
        id,
        String(e instanceof Error ? e.message : e),
      );
    }
  }

  /* T18g's saveAndRegenerateDay (save + immediate billed autoSequence) was
     removed at the T12 qualification, 2026-07-26. Under the allocator spine
     the billed call belongs at an explicit Send AFTER staging — wiring it to
     the Day Setup save spent the call before the allocator surface was ever
     seen, so sliders, per-row verbs and forgot-strip assigns could only ever
     land after the money was gone. Day Setup now saves and refreshes capacity
     (both free); `autoSequence` is reached only from the action dock.
     Callers: use `saveDaySetup` — it already sets SETUP_SAVED and refreshes
     capacity, and SetupDrawer's payload carries `confirmed: true` itself. */

  async saveAnchoredOverride(
    id: string,
    override: AppState["daySetup"]["anchored"][string],
  ): Promise<void> {
    const block = this.getState().inputs?.anchored.find((a) => a.id === id);
    if (!block) return;
    // T28 + FEEDBACK-09: calendar rows accept exactly participation plus the
    // local accounting projection.
    const sanitized =
      block.kind === "calendar"
        ? calendarParticipation(override)
        : override;
    // FEEDBACK-28 (retry): CalendarImpact's per-row toggle IS the explicit
    // current-run intent. Record it so effectiveAnchoredBlocks can
    // distinguish this skip from persisted state that must not suppress a
    // current wall.
    if (block.kind === "calendar") {
      this.dispatch({
        type: "CALENDAR_SKIP_EXPLICIT",
        id,
        skipToday: sanitized.skipToday === true,
      });
    }
    const setup = this.getState().daySetup;
    await this.saveDaySetup({
      ...setup,
      anchored: { ...setup.anchored, [id]: sanitized },
      confirmed: true,
    });
  }

  /** Local-only edit of one calendar event for today: Start (HH:MM, null
      clears it) and/or Duration in 30-minute blocks (null clears it). Goes
      through the ordinary day-setup save; it never touches a calendar write
      and never records an explicit skip, so the saved skip state is kept as is.
      A start edit records the calendar's own live start for the "changed in
      calendar" check. An invalid start is refused without saving. */
  async saveCalendarEdit(
    id: string,
    patch: { start?: string | null; blocks?: number | null },
  ): Promise<void> {
    const state = this.getState();
    const block = state.inputs?.anchored.find((a) => a.id === id && a.kind === "calendar");
    if (!block) return;
    if (patch.start != null && !isValidHHMM(patch.start)) return;
    const saved = state.daySetup.anchored[id];
    const next: AnchoredOverride = {
      on: true,
      skipToday: saved?.skipToday ?? false,
      time: null,
    };
    const blocks = "blocks" in patch ? patch.blocks : saved?.blocks;
    if (blocks != null) next.blocks = blocks;
    const start = "start" in patch ? patch.start : saved?.start;
    if (start != null) {
      next.start = start;
      next.calendarStartAtEdit = block.start;
    }
    const setup = state.daySetup;
    const nextSetup = {
      ...setup,
      anchored: { ...setup.anchored, [id]: next },
      confirmed: true,
    };
    // The edit is local-only and must read back instantly on the strip and the
    // popover. `saveDaySetup` dispatches the sanitized copy after the adapter
    // round trip; this optimistic dispatch gives immediate feedback without
    // waiting on the (real, delayed) fixture/HTTP save. The final sanitized
    // dispatch still wins, so state always converges on what was persisted.
    const previousOverride = saved;
    this.dispatch({ type: "SETUP_SAVED", daySetup: nextSetup });
    try {
      await this.saveDaySetup(nextSetup);
    } catch (e) {
      // The optimistic dispatch must not survive a refused save: a phantom
      // edit reads back on the strip with no error. Roll this row back to the
      // override that was there before the edit — but only while our edit is
      // still the one on the row. A newer save (it dispatched its own override
      // and/or the sanitized result) owns the row now, and rolling it back
      // would clobber it. Then rethrow so the caller can surface the failure.
      const current = this.getState();
      if (current.daySetup.anchored[id] === next) {
        const anchored = { ...current.daySetup.anchored };
        if (previousOverride === undefined) delete anchored[id];
        else anchored[id] = previousOverride;
        this.dispatch({
          type: "SETUP_SAVED",
          daySetup: { ...current.daySetup, anchored },
        });
      }
      throw e;
    }
  }

  /** Save today's habit overrides (full replace) through the ordinary
      day-setup save. Local-only: it never touches Todoist or the calendar.
      The edit reads back instantly; a refused save rolls the overrides back to
      what was there — but only while this edit is still the current one — and
      rethrows so the caller can surface the failure. */
  async saveHabitOverrides(habits: HabitOverrides): Promise<void> {
    const setup = this.getState().daySetup;
    const previous = setup.habits;
    const nextSetup = { ...setup, habits, confirmed: true };
    this.dispatch({ type: "SETUP_SAVED", daySetup: nextSetup });
    try {
      await this.saveDaySetup(nextSetup);
    } catch (e) {
      const current = this.getState();
      if (current.daySetup.habits === habits) {
        this.dispatch({
          type: "SETUP_SAVED",
          daySetup: { ...current.daySetup, habits: previous },
        });
      }
      throw e;
    }
  }

  /** Reset to calendar: drop the local start and duration edits, keeping the
      saved Count today choice. */
  async resetCalendarEdit(id: string): Promise<void> {
    await this.saveCalendarEdit(id, { start: null, blocks: null });
  }

  /** `defer` coalesces the two follow-up round trips instead of firing them
      per event. A slider drag emits an input event per pixel, and each one
      re-validated the sequence — every resolution re-rendered the footer's
      alert list, which changed the sticky footer's height and shoved the row
      out from under the cursor. The dispatch stays synchronous either way, so
      the thumb and the readout still track the drag exactly. */
  setOverride(
    id: string,
    included: boolean,
    blocks: number | null,
    opts?: { defer?: boolean; blocksBeforeAllDay?: number | null },
  ): void {
    const override: TodayOverride = { included, blocks };
    // Only a save that switched a row to all-day carries the captured
    // duration; every other edit writes the plain shape (and clears a stale
    // capture by omitting it).
    if (opts?.blocksBeforeAllDay != null) {
      override.blocksBeforeAllDay = opts.blocksBeforeAllDay;
    }
    this.dispatch({ type: "OVERRIDE_SET", id, override });
    if (!opts?.defer) {
      this.flushOverrideEffects();
      return;
    }
    if (this.overrideTimer != null) clearTimeout(this.overrideTimer);
    this.overrideTimer = setTimeout(() => {
      this.overrideTimer = null;
      this.flushOverrideEffects();
    }, OVERRIDE_SETTLE_MS);
  }

  private flushOverrideEffects(): void {
    void this.refreshCapacity();
    void this.revalidate();
  }

  /** Allow the scheduler to move one existing Todoist time. This is a
      per-item edit, not a session-wide mode; the next validation/preview must
      re-earn trust after the permission changes. */
  setTimeAdjustmentOptIn(id: string, allow: boolean): void {
    const item = this.getState().inputs?.assigned.find((i) => i.id === id);
    if (!item || item.source !== "todoist" || !item.scheduledStart) return;
    this.dispatch({ type: "TIME_ADJUSTMENT_SET", id, allow });
    void this.revalidate();
  }

  /** #18: grant (or revoke) one assigned row's permission to overlap a Mint
      session. The grant only changes the wire flag the server admits onto a
      server-matched row; the next validation/preview must re-earn trust after
      the permission changes. */
  setMintOverlapOptIn(id: string, allow: boolean): void {
    const item = this.getState().inputs?.assigned.find((i) => i.id === id);
    if (!item) return;
    this.dispatch({ type: "MINT_OVERLAP_SET", id, allow });
    void this.revalidate();
  }

  async refreshCapacity(): Promise<void> {
    const s = this.getState();
    if (!s.inputs) return;
    const token = ++this.capacityToken;
    const selected = includedItems(s).map((i) => effectiveBlocks(s, i.id));
    const capacity = await this.adapter.capacityPreview(s.daySetup, selected);
    if (token !== this.capacityToken) return; // superseded by a newer edit
    this.dispatch({ type: "CAPACITY_UPDATED", capacity });
  }

  /** Issue #19 (part B): one read-only calendar drift check. It calls ONLY
      adapter.calendarDrift() — never a write or billed endpoint. `fromFocus`
      applies the window-focus throttle (at most one call per 30s); the
      explicit Check calendar button always runs. An in-flight check is never
      duplicated. */
  async checkCalendarDrift(opts?: { fromFocus?: boolean }): Promise<void> {
    if (this.driftInFlight) return;
    if (opts?.fromFocus) {
      const at = this.now();
      if (
        this.lastDriftFocusAt !== null &&
        at - this.lastDriftFocusAt < DRIFT_FOCUS_MIN_MS
      ) {
        return;
      }
      this.lastDriftFocusAt = at;
    }
    this.driftInFlight = true;
    try {
      const report = await this.adapter.calendarDrift();
      this.dispatch({ type: "CALENDAR_DRIFT_OK", report });
    } catch (e) {
      this.dispatch({
        type: "CALENDAR_DRIFT_FAIL",
        error: String(e instanceof Error ? e.message : e),
      });
    } finally {
      this.driftInFlight = false;
    }
  }

  /** The one billed action. Explicit user click only — never auto-fired. */
  async autoSequence(): Promise<void> {
    const s = this.getState();
    if (!canAutoSequence(s)) return;
    this.dispatch({ type: "SEQUENCE_START" });

    // Phase 1 — preflight (free). readFixedInputs refreshes the adapter's raw
    // contract after a setup save. A failure here never reached the billed
    // POST, so it must not be reported as a billing risk.
    let fixed: FixedInputs;
    try {
      fixed = await this.adapter.readFixedInputs();
    } catch (e) {
      await this.sequenceFailure(e, "preflight");
      return;
    }

    const context = this.sequenceContext(this.getState().pendingPinnedRows);
    // Use the fresh planning fingerprint from the same read for the paid
    // request; the store still reflects the prior read until SEQUENCE_OK lands.
    context.planningConfigFingerprint =
      fixed.planningConfigFingerprint ?? context.planningConfigFingerprint;

    // Phase 2 — the ONE billed request. Nothing here retries automatically:
    // a transport rejection leaves the billed outcome unknown.
    let result: SequenceResult;
    try {
      result = await this.adapter.autoSequence(context);
    } catch (e) {
      await this.sequenceFailure(e, "request");
      return;
    }

    // Phase 3 — result processing (free). A TypeError while laying out the
    // returned rows is a display bug, never a transport failure; keep any
    // rows the server did return read-only and stage nothing partial.
    let outcome: {
      sequence: SequenceRow[];
      overflowIds: string[];
      warnings: string[];
      pinnedRows: SequenceRow[];
    };
    try {
      const included = new Set(includedItems(this.getState()).map((i) => i.id));
      const injected = (r: SequenceRow) =>
        r.wire?.source === "schedulable" ||
        r.id === "Minting" ||
        r.id === "Quick Tasks" ||
        r.id === "Shivery Jigs" ||
        /^Mint /i.test(r.id);
      const rows = result.sequence.filter(
        (r) => r.kind === "zone" || included.has(r.id) || injected(r),
      );
      // Overbooked days: the sequencer drops what does not fit, and a dropped
      // row gets no time, so it reaches no surface and never appears in
      // BusyCal. Lay those out deterministically from the frame anchor
      // instead — ordinary work rows, written by source like any other
      // (Adam, 2026-07-27). Free: no second billed call.
      // FEEDBACK-02: overflow is wall-aware — non-permeable calendar walls
      // (post Day-Setup dismissals) are skipped so a staged row can never be
      // the exact calendar-wall overlap the server now hard-rejects.
      // FEEDBACK-03: placement scans free gaps — after the calendar walls AND
      // the server's effective immutable pin set (manual pins + native timed
      // auto-pins). A dropped row no gap can hold is reported as explicit
      // infeasibility in the sequence warnings (naming the row, its need,
      // and the free capacity) instead of silently omitted; it is not staged
      // and it gates shadow/commit behind LD24 acceptance like any defect.
      const post = this.getState();
      const pins = result.pinnedRows ?? post.pendingPinnedRows;
      // FEEDBACK-25: selected Mint session intervals are hard walls too —
      // the server hard-rejects any row overlapping one, so the free-gap
      // scan must not stage a dropped row over protected Mint time.
      const plan = planOverflow(
        droppedItems(includedItems(post), rows, (i) => effectiveBlocks(post, i.id)),
        post.inputs?.time.anchor ?? "",
        (i) => effectiveBlocks(post, i.id),
        [...calendarWalls(effectiveAnchoredBlocks(post)), ...mintWalls(rows)],
        (pins ?? [])
          .filter((r) => r.kind !== "zone")
          .map((r) => ({ start: toMinutes(r.start), end: toMinutes(r.end) }))
          .filter((i) => i.end > i.start),
      );
      const overflow = plan.rows;
      const infeasibleWarnings = plan.infeasible.map(
        (f) => `⚠ overflow infeasible — ${f.id}: ${f.reason}`,
      );
      // FEEDBACK-01: overflow rows are laid out from the frame anchor, which
      // on a late-anchored overbooked day is LATER than some server-validated
      // rows (the plan reaches 23:15 while overflow starts at 17:15).
      // Appending them verbatim produced a descending sequence the server
      // then rejected — 'Log hours' 17:15 after 23:15. Sort the final merged
      // sequence by start time (id tiebreak, mirroring merge_immutable_rows)
      // so what the UI stages is exactly what validation accepts; the sort
      // reorders, never drops.
      outcome = {
        sequence: [...rows, ...overflow].sort(byStartThenId),
        overflowIds: overflow.map((r) => r.id),
        warnings: [...result.warnings, ...infeasibleWarnings],
        // Prefer the server's effective pin set (client + native timed
        // auto-pins) so validate/commit snapshots stay byte-exact.
        pinnedRows: result.pinnedRows ?? this.getState().pendingPinnedRows,
      };
    } catch (e) {
      await this.sequenceFailure(
        e,
        "result",
        Array.isArray(result.sequence) && result.sequence.length > 0 ? result.sequence : null,
      );
      return;
    }

    // Phase 4 — budget refresh, independent of the plan. A failed ledger read
    // must never throw away a paid plan or read as a sequence failure.
    const ledger = await this.adapter.billedLedger().catch(() => null);
    this.dispatch({
      type: "SEQUENCE_OK",
      ...outcome,
      fingerprint: fingerprintFixedInputs(fixed),
      anchoredSourceFingerprint: fixed.anchoredSourceFingerprint,
      planningConfigFingerprint: fixed.planningConfigFingerprint,
      overlapGrants: result.overlapGrants,
      ledger,
    });
  }

  /** One truthful failure surface for the three sequence phases. Preflight and
      session-token failures say the call was never requested; a transport
      rejection on POST /sequence keeps the unknown billed outcome; a result
      layout failure keeps the returned rows read-only. */
  private async sequenceFailure(
    e: unknown,
    phase: "preflight" | "request" | "result",
    proposal: SequenceRow[] | null = null,
  ): Promise<void> {
    const ledger = await this.adapter.billedLedger().catch(() => null);
    const text = String(e instanceof Error ? e.message : e);
    if (phase === "result") {
      this.dispatch({
        type: "SEQUENCE_FAIL",
        error: `The sequencer returned a plan this app could not display (${text}). Nothing was staged — use manual placement.`,
        ledger,
        rejectedProposal: proposal,
      });
      return;
    }
    if (phase === "preflight" || apiFailurePathOf(e) === "/session-token") {
      this.dispatch({
        type: "SEQUENCE_FAIL",
        error: `Could not prepare before sequencing (${text}). ${SEQUENCE_NOT_REQUESTED_MESSAGE}`,
        ledger,
        rejectedProposal: null,
      });
      return;
    }
    // T12 qualification: a hard validation failure arrives AFTER the SDK
    // call was made and the ledger charged. The server now returns the
    // rejected body so the paid plan is at least readable instead of
    // vanishing; it is held separately from `sequence` so nothing can
    // mistake it for something committable.
    let rejectedProposal: SequenceRow[] | null = null;
    if (e instanceof ApiError && e.status === 422) {
      const detail = e.detail as { rejected_proposal?: { sequence?: unknown[] } } | undefined;
      const rows = detail?.rejected_proposal?.sequence;
      if (Array.isArray(rows) && rows.length > 0) {
        rejectedProposal = rows.map((r) => projectSequenceRow(r as never));
      }
    }
    this.dispatch({
      type: "SEQUENCE_FAIL",
      // 2026-10-15 (Firefox incident): a fetch rejection during POST
      // /sequence surfaces the browser's raw "NetworkError when attempting
      // to fetch resource." — which says nothing about the billed attempt.
      // Map it to the honest message; real HTTP errors (422/502) keep their
      // server detail untouched. A generic TypeError is not evidence of
      // transport, so it is never mapped to this message.
      error: isNetworkError(e) ? SEQUENCE_NETWORK_ERROR_MESSAGE : text,
      ledger,
      rejectedProposal,
    });
  }

  /** Deterministic revalidation (free) — after placement edits or exclusions.
      A rejected validate call must not vanish as an unhandled rejection: it is
      surfaced as a visible invalid validation so the plan stays dirty and the
      user sees why. A superseded rejection is ignored exactly like a
      superseded success — only the newest request may write state. */
  async revalidate(): Promise<void> {
    const s = this.getState();
    if (!s.sequence || s.sequence.every((r) => r.kind === "zone")) return;
    const token = ++this.revalidateToken;
    try {
      const validation = await this.adapter.validateSequence(s.sequence, this.sequenceContext());
      if (token !== this.revalidateToken) return; // superseded by a newer edit
      this.dispatch({ type: "VALIDATED", validation });
    } catch (e) {
      if (token !== this.revalidateToken) return; // superseded failure — ignore
      this.dispatch({
        type: "VALIDATED",
        validation: {
          ok: false,
          hardErrors: [`Revalidation failed: ${String(e instanceof Error ? e.message : e)}`],
          warnings: [],
        },
      });
    }
  }

  moveRow(id: string, start: string): void {
    this.dispatch({ type: "ROW_MOVED", id, start });
    void this.revalidate();
  }

  placeRow(id: string, start: string): void {
    this.dispatch({ type: "ROW_PLACED", id, start });
    void this.revalidate();
  }

  unplaceRow(id: string): void {
    this.dispatch({ type: "ROW_UNPLACED", id });
    void this.revalidate();
  }

  resetPlacement(id: string): void {
    this.dispatch({ type: "ROW_PIN_RESET", id });
  }

  /** Release a row's placement entirely: drop its pin AND take it out of the
      staged sequence, so it goes back to needs-placement and the next Send is
      free to put it somewhere else.

      Both dispatches are needed. ROW_UNPLACED clears the pin too, but bails
      early when there is no sequence — which is exactly the case for a row
      pinned by hand before any Send. ROW_PIN_RESET covers that; the second
      dispatch is a no-op when there is nothing staged. */
  releasePlacement(id: string): void {
    this.dispatch({ type: "ROW_PIN_RESET", id });
    this.dispatch({ type: "ROW_UNPLACED", id });
    void this.revalidate();
  }

  /** T20: one journaled runtime verb against one committed plan item. The
      server refuses anything not in today's manifest; every failure mode
      (surface unavailable, partial, compensated) lands in state for the
      banner — never a silent retry. */
  async runtimeAction(
    verb: string, target: string, args: Record<string, unknown> = {},
  ): Promise<void> {
    this.dispatch({ type: "RUNTIME_START" });
    try {
      const action = await this.adapter.runtimeAction(verb, target, args);
      this.dispatch({ type: "RUNTIME_OK", action });
    } catch (e) {
      this.dispatch({
        type: "RUNTIME_FAIL",
        error: String(e instanceof Error ? e.message : e),
      });
    }
  }

  /** Allocator-rewrite T3 → IMP-07: one staging-phase verb fired from a
      Today's-work row, BEFORE any commit exists. Reuses runtimeAction
      wholesale — same journal, same undo, same failure surfacing — and adds
      the one thing the staging phase needs: a source refresh on success, so
      the done/dropped/unassigned/deleted row actually leaves the queue
      instead of lingering as a stale row the user can act on twice.

      Guards the verb locally as well as server-side: offering a placement
      verb here would earn a 422 the user can do nothing about. */
  async stagingAction(verb: string, target: string): Promise<void> {
    if (!isStagingVerb(verb)) {
      this.dispatch({
        type: "RUNTIME_FAIL",
        error: `${verb} needs a committed plan item`,
      });
      return;
    }
    await this.runtimeAction(verb, target);
    const s = this.getState();
    if (s.runtimeError) return;
    // A refused or failed action comes back HTTP 200 with the failure IN the
    // journal entry — only a thrown adapter error sets runtimeError. Without
    // this check a failed verb read as success: no message, and a source
    // refresh that made the row look handled. (Caught on the scratch server:
    // a Capacities row with no writable status failed closed, correctly, and the
    // UI said nothing at all.)
    const last = s.lastRuntimeAction;
    if (last && last.status !== "applied") {
      this.dispatch({
        type: "RUNTIME_FAIL",
        error: last.error ?? `${verb} did not apply (${last.status})`,
      });
      return;
    }
    // A staged verb settles the row for TODAY — done, dropped from plan,
    // unassigned, or deleted outright, it is not part of this plan any more.
    // Say so locally instead of waiting on the source refresh: whether the
    // row actually disappears depends on the surface (a dropped Todoist task
    // leaves the Today filter; a Capacities row may not), and a verb that fires,
    // journals, and leaves the queue untouched reads as a verb that did
    // nothing. Undo re-includes it.
    this.setOverride(target, false, this.getState().overrides[target]?.blocks ?? null);
    await this.refreshSources();
  }

  /** T20: the one scoped undo — reverses the LAST applied runtime action. */
  async undoRuntimeAction(): Promise<void> {
    const last = this.getState().lastRuntimeAction;
    if (!last || last.status !== "applied") return;
    this.dispatch({ type: "RUNTIME_START" });
    try {
      const action = await this.adapter.undoRuntimeAction(last.id);
      this.dispatch({ type: "RUNTIME_UNDO_OK", action });
      // Mirror of the staging exclusion above: undoing the verb puts the row
      // back in today, or Undo would reverse the write and leave the queue
      // still showing it gone.
      const target = this.getState().inputs?.assigned.find(
        (i) => i.id === action.targetName || i.name === action.targetName,
      );
      if (target) {
        this.setOverride(target.id, true, this.getState().overrides[target.id]?.blocks ?? null);
      }
    } catch (e) {
      this.dispatch({
        type: "RUNTIME_FAIL",
        error: String(e instanceof Error ? e.message : e),
      });
    }
  }

  /** Manual-layout fingerprint capture: a validated manual plan also pins the
      fixed inputs it was built against. */
  private async captureFingerprint(): Promise<{
    fingerprint: string;
    anchoredSourceFingerprint: string;
    planningConfigFingerprint: string;
  }> {
    const fixed = await this.adapter.readFixedInputs();
    return {
      fingerprint: fingerprintFixedInputs(fixed),
      anchoredSourceFingerprint: fixed.anchoredSourceFingerprint,
      planningConfigFingerprint: fixed.planningConfigFingerprint ?? "",
    };
  }

  /** Mandatory pre-shadow / pre-commit drift check. Returns true when the
      staged plan is still valid against a FRESH fixed-input read. */
  private async fixedInputsStillValid(): Promise<boolean> {
    const s = this.getState();
    const fresh = await this.adapter.readFixedInputs(); // throws on failure → caller blocks
    const fp = fingerprintFixedInputs(fresh);
    if (s.fingerprint === null) {
      // Manual layout that never sequenced: adopt the fresh fingerprint.
      return true;
    }
    if (
      fp !== s.fingerprint ||
      fresh.anchoredSourceFingerprint !== s.anchoredSourceFingerprint
      || fresh.planningConfigFingerprint !== s.planningConfigFingerprint
    ) {
      this.dispatch({ type: "FINGERPRINT_MISMATCH" });
      return false;
    }
    return true;
  }

  async shadowPreview(): Promise<void> {
    const s = this.getState();
    // Acceptable defects gate preview until explicitly accepted (locked
    // decision 24) — controller-level twin of the dock's canShadow guard.
    // Source-health gate: a degraded/failed read is not a usable planning
    // snapshot — refuse before any network call (mirrors canShadow selector).
    if (!s.sequence || s.validation?.ok !== true || !defectsResolved(s) || sourceHealthBlocked(s)) return;
    this.dispatch({ type: "SHADOW_START" });
    try {
      if (!(await this.fixedInputsStillValid())) return;
      if (this.getState().fingerprint === null) {
        // Manual layout that never sequenced: pin the fresh fixed inputs now.
        this.dispatch({ type: "FINGERPRINT_ADOPTED", ...(await this.captureFingerprint()) });
      }
      const shadow = await this.adapter.shadowCommit(
        this.getState().sequence as SequenceRow[],
        this.sequenceContext(),
      );
      this.dispatch({ type: "SHADOW_OK", shadow });
    } catch (e) {
      this.dispatch({
        type: "SHADOW_FAIL",
        error: String(e instanceof Error ? e.message : e),
        kind: shadowFailureKind(e),
      });
    }
  }

  armLive(): void {
    this.dispatch({ type: "ARM_LIVE" });
  }

  disarmLive(): void {
    this.dispatch({ type: "DISARM_LIVE" });
  }

  /** The second click. Re-reads fixed inputs immediately before writing. */
  async requestLiveCommit(): Promise<void> {
    const s = this.getState();
    if (!canLiveCommit(s)) return;
    this.dispatch({ type: "COMMIT_START" });
    let driftOk: boolean;
    try {
      driftOk = await this.fixedInputsStillValid();
    } catch {
      // Source read failed → commit BLOCKED, not attempted (locked decision 17).
      this.dispatch({ type: "COMMIT_ABORT" });
      return;
    }
    if (!driftOk) {
      // FINGERPRINT_MISMATCH already invalidated the staged plan.
      this.dispatch({ type: "COMMIT_ABORT" });
      return;
    }
    try {
      const report = await this.adapter.liveCommit(
        s.sequence as SequenceRow[],
        this.sequenceContext(),
      );
      this.dispatch({ type: "COMMIT_DONE", report });
    } catch (e) {
      // 409 (single-flight) / 422 (plan refused) are guaranteed-no-write
      // rejections: plan stays intact and retryable. Anything else may have
      // partially written — surface it as a failed report, never swallow.
      if (e instanceof ApiError && (e.status === 409 || e.status === 422)) {
        this.dispatch({ type: "COMMIT_REJECTED", error: e.message });
        return;
      }
      this.dispatch({
        type: "COMMIT_DONE",
        report: {
          status: "failed",
          surfaces: [],
          verifyFailures: [String(e instanceof Error ? e.message : e)],
        },
      });
    }
  }
}
