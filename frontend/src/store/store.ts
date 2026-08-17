/* store.ts — single central store: pure reducer + typed actions + selectors.
   Encodes the cockpit safety invariants (locked decisions 16/17, test matrix
   § Safety):

   - A valid_date change invalidates staged sequence, shadow, review state,
     and session overrides.
   - ANY edit (override, placement, day setup) after a current shadow marks it
     stale and disarms live commit until preview reruns.
   - Fixed-input fingerprint mismatch invalidates sequence + shadow.
   - Include/exclude and duration edits never touch upstream assignment —
     there is no action in this vocabulary that can mutate `assigned`.
   - Billed actions (sequence) gate on the server ledger; nothing here
     auto-fires them. */

import type {
  RuntimeAction,
  Capacity,
  CommitReport,
  DaySetup,
  Ledger,
  MicroIdea,
  OverlapGrant,
  PlanInputs,
  QueueState,
  SequenceRow,
  ShadowDiff,
  TodayOverride,
  Validation,
} from "../model/types";
import {
  reconcileRefresh,
  summaryHasChanges,
  type RefreshSummary,
} from "../model/refresh";
import { allowedOverlaps, defectsCovered, workOverlaps } from "../model/findings";
import { blocksLabel } from "../model/time";
import { durationSourceOf } from "../adapters/wire";

export type SeqPhase = "none" | "sequencing" | "valid" | "dirty" | "failed";
export type ShadowPhase = "none" | "loading" | "current" | "stale";
export type CommitPhase = "idle" | "committing" | "done" | "partial" | "failed";
export type RefreshPhase = "idle" | "loading";

/** Explicit source refresh (locked decision 20): loading/error/last-refreshed
    feedback plus the compact reconciliation summary of the last success. */
export interface RefreshState {
  phase: RefreshPhase;
  error: string | null;
  lastRefreshed: string | null; // ISO timestamp of last successful refresh
  summary: RefreshSummary | null;
}
export type Theme = "system" | "light" | "dark";

export interface AppState {
  loaded: boolean;
  loadError: string | null;
  validDate: string | null;
  inputs: PlanInputs | null;
  capacity: Capacity | null; // live server-verbatim numbers
  daySetup: DaySetup;
  overrides: Record<string, TodayOverride>;
  placements: Record<string, string>; // id -> start HH:MM (sequence/exact editor)
  sequence: SequenceRow[] | null;
  seqPhase: SeqPhase;
  seqError: string | null;
  /* T12 qualification: the rows a PAID /sequence returned that then failed
     hard validation. Never committable — held only so the surface can show
     the user what their billed call actually bought. */
  rejectedProposal: SequenceRow[] | null;
  /** Ids the sequencer dropped and `overflowRows` placed from the frame
      anchor instead. Labelling only — these are ordinary work rows in
      `sequence` and are written like any other (model/overflow.ts). */
  overflowIds: string[];
  validation: Validation | null;
  fingerprint: string | null; // captured when sequence landed
  anchoredSourceFingerprint: string | null; // raw anchored config at sequence time
  planningConfigFingerprint: string | null; // config snapshot for staged plan
  overlapGrants: OverlapGrant[];
  /** Exact pins persisted by the last successful /sequence snapshot. */
  pinnedRows: SequenceRow[];
  /** Manual placement constraints to send on the next explicit regeneration. */
  pendingPinnedRows: SequenceRow[];
  shadow: ShadowDiff | null;
  shadowPhase: ShadowPhase;
  liveArmed: boolean; // second-click reveal (locked decision 9)
  commitPhase: CommitPhase;
  commitReport: CommitReport | null;
  /** T20 runtime verbs: one in-flight action at a time, last journal entry
      drives the status banner + the one scoped undo chip. */
  runtimeBusy: boolean;
  lastRuntimeAction: RuntimeAction | null;
  runtimeError: string | null;
  /** Duration-memory MVP: per-item pending/error feedback for the explicit
      save and reset mutations. Purely local mutation state — never written
      into the session blob (durable memory is server-side). */
  durationMemory: Record<string, { pending: boolean; error: string | null }>;
  /** A guaranteed-no-write server rejection (409 single-flight, 422 plan
      refused): the plan stays intact and retryable — distinct from a failed
      report, where writes may have partially landed. */
  commitError: string | null;
  /** Set when external fixed-input drift invalidated the staged plan
      (locked decision 17) — rendered as a global blocking alert until the
      user resequences or revalidates. */
  driftNotice: string | null;
  /** Good-enough plan override (locked decision 24): acceptable defects
      acknowledged verbatim for the current date. null = nothing accepted.
      Today-only review state — never restored across a reload, cleared by
      any edit, resequence, or refresh drift. */
  acceptedDefects: string[] | null;
  refresh: RefreshState;
  ledger: Ledger | null;
  theme: Theme;
  ui: {
    setupOpen: boolean;
    approvalOpen: boolean;
    editorItem: string | null; // exact block editor target
    /** T12e (brief problem 7): what the editor's caller wanted — "duration"
        (✎, duration-only fields) or "place" (start pre-focused). */
    editorIntent: "duration" | "place" | null;
    editorAnchor: string | null; // non-Calendar anchored editor target
    capacityDetail: boolean;
    /** T12e trim assist: ids excluded by the last Accept trim, held so Undo
        can re-include exactly that set. Today-only UI state, never persisted. */
    trimUndo: string[] | null;
  };
}

export const emptyDaySetup: DaySetup = {
  anchor: null,
  eod: null,
  buffering: "standard",
  anchored: {},
  captures: { intention: "", forMeegy: "", stoic: "" },
  confirmed: false,
};

export const initialState: AppState = {
  loaded: false,
  loadError: null,
  validDate: null,
  inputs: null,
  capacity: null,
  daySetup: emptyDaySetup,
  overrides: {},
  placements: {},
  sequence: null,
  seqPhase: "none",
  seqError: null,
  rejectedProposal: null,
  overflowIds: [],
  validation: null,
  fingerprint: null,
  anchoredSourceFingerprint: null,
  planningConfigFingerprint: null,
  overlapGrants: [],
  pinnedRows: [],
  pendingPinnedRows: [],
  shadow: null,
  shadowPhase: "none",
  liveArmed: false,
  commitPhase: "idle",
  commitReport: null,
  runtimeBusy: false,
  lastRuntimeAction: null,
  runtimeError: null,
  durationMemory: {},
  commitError: null,
  driftNotice: null,
  acceptedDefects: null,
  refresh: { phase: "idle", error: null, lastRefreshed: null, summary: null },
  ledger: null,
  theme: "system",
  ui: { setupOpen: false, approvalOpen: false, editorItem: null, editorIntent: null, editorAnchor: null, capacityDetail: false, trimUndo: null },
};

export type Action =
  | { type: "INPUTS_LOADED"; inputs: PlanInputs; ledger: Ledger }
  | { type: "LOAD_FAILED"; error: string }
  | { type: "SETUP_SAVED"; daySetup: DaySetup }
  | { type: "OVERRIDE_SET"; id: string; override: TodayOverride }
  // T19: Live micro-adventure changed (shuffle / pick / custom / reset).
  // Free and unbilled, but it changes the commit's Live→Todoist content —
  // any current shadow preview goes stale and acceptance is revoked.
  | { type: "MICRO_SET"; pick: MicroIdea | null; source: "auto" | "override" }
  | { type: "CAPACITY_UPDATED"; capacity: Capacity }
  | { type: "SEQUENCE_START" }
  | {
      type: "SEQUENCE_OK";
      sequence: SequenceRow[];
      warnings: string[];
      fingerprint: string;
      anchoredSourceFingerprint: string;
      planningConfigFingerprint?: string;
      overlapGrants?: OverlapGrant[];
      pinnedRows?: SequenceRow[];
      pendingPinnedRows?: SequenceRow[];
      overflowIds?: string[];
      ledger: Ledger;
    }
  | {
      type: "SEQUENCE_FAIL";
      error: string;
      ledger: Ledger | null;
      rejectedProposal?: SequenceRow[] | null;
    }
  | { type: "ROW_MOVED"; id: string; start: string }
  | { type: "ROW_PLACED"; id: string; start: string } // exact editor: queue -> staged sequence
  | { type: "ROW_UNPLACED"; id: string }
  | { type: "ROW_PIN_RESET"; id: string }
  | { type: "VALIDATED"; validation: Validation }
  | { type: "SHADOW_START" }
  | { type: "SHADOW_OK"; shadow: ShadowDiff }
  | { type: "SHADOW_FAIL"; error: string }
  | { type: "FINGERPRINT_MISMATCH" }
  | {
      type: "FINGERPRINT_ADOPTED";
      fingerprint: string;
      anchoredSourceFingerprint: string;
      planningConfigFingerprint?: string;
    } // manual layout pins fresh fixed inputs
  | { type: "ACCEPT_DEFECTS" } // record current acceptable defects verbatim
  | { type: "ARM_LIVE" }
  | { type: "DISARM_LIVE" }
  | { type: "COMMIT_START" }
  | { type: "COMMIT_ABORT" } // pre-write drift/failure: back to idle, no report
  | { type: "COMMIT_REJECTED"; error: string } // 409/422: nothing written, retryable
  | { type: "COMMIT_DONE"; report: CommitReport }
  | { type: "RUNTIME_START" }
  | { type: "RUNTIME_OK"; action: RuntimeAction }
  | { type: "RUNTIME_FAIL"; error: string }
  | { type: "RUNTIME_UNDO_OK"; action: RuntimeAction }
  // Duration-memory MVP: explicit save/reset lifecycle. OK applies the
  // server-authoritative result (remembered value or source fallback) to the
  // row; FAIL preserves the last authoritative value.
  | { type: "DURATION_MEMORY_START"; id: string }
  | { type: "DURATION_MEMORY_OK"; id: string; minutes: number; source: string }
  | { type: "DURATION_MEMORY_FAIL"; id: string; error: string }
  | {
      // Same-date refresh restore (locked decision 16): today-only state
      // rehydrates; shadow/review NEVER restore — preview must rerun.
      type: "SESSION_RESTORED";
      overrides: Record<string, TodayOverride>;
      placements: Record<string, string>;
      sequence: SequenceRow[] | null;
      fingerprint: string | null;
      anchoredSourceFingerprint: string | null;
      planningConfigFingerprint?: string | null;
      overlapGrants?: OverlapGrant[];
      pinnedRows?: SequenceRow[];
      pendingPinnedRows?: SequenceRow[];
    }
  | { type: "SOURCE_REFRESH_START" }
  | {
      // Explicit refresh landed (locked decision 20). fingerprint /
      // anchoredSourceFingerprint are computed from the SAME fresh read as
      // `inputs`; the reducer reconciles rows and judges drift.
      type: "SOURCE_REFRESH_OK";
      inputs: PlanInputs;
      ledger: Ledger;
      fingerprint: string;
      anchoredSourceFingerprint: string;
      planningConfigFingerprint?: string;
      at: string; // ISO timestamp
    }
  | { type: "SOURCE_REFRESH_FAIL"; error: string }
  | { type: "LEDGER_UPDATED"; ledger: Ledger }
  | { type: "THEME_SET"; theme: Theme }
  | {
      type: "UI";
      patch: Partial<AppState["ui"]>;
    };

/** Any staged-plan edit after a current shadow makes it stale + disarms live. */
function staleShadow(s: AppState): Pick<AppState, "shadowPhase" | "liveArmed"> {
  return s.shadowPhase === "current" || s.shadowPhase === "loading"
    ? { shadowPhase: "stale", liveArmed: false }
    : { shadowPhase: s.shadowPhase, liveArmed: s.liveArmed };
}

/** Sequence exists and this edit touches plan content → dirty (revalidate). */
function dirtySeq(s: AppState): Pick<AppState, "seqPhase" | "validation"> {
  return s.seqPhase === "valid" || s.seqPhase === "dirty"
    ? { seqPhase: "dirty", validation: null }
    : { seqPhase: s.seqPhase, validation: s.validation };
}

export function reducer(s: AppState, a: Action): AppState {
  switch (a.type) {
    case "INPUTS_LOADED": {
      const dateChanged = s.validDate !== null && s.validDate !== a.inputs.validDate;
      if (dateChanged) {
        // Date rollover: staged sequence, shadow, review state, and
        // session overrides all die (locked decision 16 / state key).
        return {
          ...initialState,
          theme: s.theme,
          loaded: true,
          validDate: a.inputs.validDate,
          inputs: a.inputs,
          capacity: a.inputs.capacity,
          daySetup: a.inputs.daySetup,
          ledger: a.ledger,
        };
      }
      return {
        ...s,
        loaded: true,
        loadError: null,
        validDate: a.inputs.validDate,
        inputs: a.inputs,
        capacity: s.capacity ?? a.inputs.capacity,
        daySetup: s.daySetup.confirmed ? s.daySetup : a.inputs.daySetup,
        // T27: recurring pins always re-seed from fresh inputs — a non-empty
        // pin set (manual pins, restored session) must not suppress them.
        pendingPinnedRows: mergeRecurringPins(s.pendingPinnedRows, a.inputs),
        ledger: a.ledger,
      };
    }
    case "LOAD_FAILED":
      return { ...s, loaded: true, loadError: a.error };
    case "SETUP_SAVED":
      return {
        ...s,
        // Optional day semantics use field-presence rules. Omitted preserves
        // the dated override; explicit null resets it to config.
        daySetup: { ...s.daySetup, ...a.daySetup, confirmed: true },
        acceptedDefects: null,
        ...dirtySeq(s),
        ...staleShadow(s),
      };
    case "MICRO_SET": {
      if (!s.inputs) return s;
      return {
        ...s,
        inputs: {
          ...s.inputs,
          microAdventure: {
            ...s.inputs.microAdventure,
            pick: a.pick,
            source: a.source,
          },
        },
        acceptedDefects: null, // LD 24: any edit revokes acceptance
        ...staleShadow(s),
      };
    }
    case "OVERRIDE_SET": {
      const overrides = { ...s.overrides, [a.id]: a.override };
      // Excluding an item drops its staged row; a duration edit resizes the
      // staged row so the sequence payload carries the effective blocks
      // (locked decision 16). The plan is dirty either way.
      let sequence = s.sequence;
      if (sequence && (!a.override.included || a.override.blocks === 0)) {
        sequence = sequence.filter((r) => r.id !== a.id || r.kind === "zone");
      } else if (sequence && a.override.blocks != null) {
        sequence = sequence.map((r) =>
          r.id === a.id && r.kind === "work"
            ? { ...r, end: addMin(r.start, a.override.blocks! * 30) }
            : r,
        );
      }
      const placements = { ...s.placements };
      if (!a.override.included || a.override.blocks === 0) delete placements[a.id];
      const pendingPinnedRows = s.pendingPinnedRows
        .filter((row) => row.id !== a.id || (a.override.included && a.override.blocks !== 0))
        .map((row) => {
          if (row.id !== a.id || a.override.blocks == null) return row;
          return { ...row, end: addMin(row.start, a.override.blocks * 30) };
        });
      return {
        ...s,
        overrides,
        sequence,
        placements,
        pendingPinnedRows,
        acceptedDefects: null,
        ...dirtySeq(s),
        ...staleShadow(s),
      };
    }
    case "CAPACITY_UPDATED":
      return { ...s, capacity: a.capacity };
    case "SEQUENCE_START":
      return {
        ...s,
        seqPhase: "sequencing",
        seqError: null,
        driftNotice: null,
        acceptedDefects: null, // resequence kills any acceptance (LD 24)
      };
    case "SEQUENCE_OK":
      return {
        ...s,
        sequence: a.sequence,
        overflowIds: a.overflowIds ?? [],
        seqPhase: "valid",
        seqError: null,
        validation: { ok: true, hardErrors: [], warnings: a.warnings },
        fingerprint: a.fingerprint,
        anchoredSourceFingerprint: a.anchoredSourceFingerprint,
        planningConfigFingerprint: a.planningConfigFingerprint ?? s.inputs?.planningConfigFingerprint ?? null,
        overlapGrants: a.overlapGrants ?? [],
        pinnedRows: a.pinnedRows ?? s.pinnedRows,
        pendingPinnedRows: a.pendingPinnedRows ?? a.pinnedRows ?? s.pendingPinnedRows,
        ledger: a.ledger,
        shadow: null,
        shadowPhase: "none",
        liveArmed: false,
      };
    case "SEQUENCE_FAIL":
      return {
        ...s,
        seqPhase: "failed",
        seqError: a.error,
        ledger: a.ledger ?? s.ledger,
        rejectedProposal: a.rejectedProposal ?? null,
      };
    case "ROW_MOVED": {
      if (!s.sequence) return s;
      const sequence = s.sequence.map((r) => {
        if (r.id !== a.id || r.kind === "zone") return r;
        const dur =
          r.end && r.start
            ? (toMin(r.end) - toMin(r.start) + 1440) % 1440
            : 0;
        return { ...r, start: a.start, end: addMin(a.start, dur) };
      });
      return {
        ...s,
        sequence,
        placements: { ...s.placements, [a.id]: a.start },
        pendingPinnedRows: upsertPinnedRow(s.pendingPinnedRows, sequence, a.id),
        seqPhase: "dirty",
        validation: null,
        acceptedDefects: null,
        ...staleShadow(s),
      };
    }
    case "ROW_PLACED": {
      const item = s.inputs?.assigned.find((i) => i.id === a.id);
      if (!item) return s;
      const blocks = s.overrides[a.id]?.blocks ?? item.blocks;
      const row: SequenceRow = {
        id: a.id,
        start: a.start,
        end: addMin(a.start, blocks * 30),
        zone: null,
        kind: "work",
      };
      const base = s.sequence ?? [];
      const sequence = [...base.filter((r) => r.id !== a.id || r.kind === "zone"), row];
      return {
        ...s,
        sequence,
        placements: { ...s.placements, [a.id]: a.start },
        pendingPinnedRows: upsertPinnedRow(s.pendingPinnedRows, sequence, a.id),
        seqPhase: "dirty",
        validation: null,
        acceptedDefects: null,
        ...staleShadow(s),
      };
    }
    case "ROW_UNPLACED": {
      if (!s.sequence) return s;
      const placements = { ...s.placements };
      delete placements[a.id];
      return {
        ...s,
        sequence: s.sequence.filter((r) => r.id !== a.id || r.kind === "zone"),
        placements,
        pendingPinnedRows: s.pendingPinnedRows.filter((r) => r.id !== a.id),
        seqPhase: "dirty",
        validation: null,
        acceptedDefects: null,
        ...staleShadow(s),
      };
    }
    case "ROW_PIN_RESET":
      return {
        ...s,
        pendingPinnedRows: s.pendingPinnedRows.filter((r) => r.id !== a.id),
        acceptedDefects: null,
        ...dirtySeq(s),
        ...staleShadow(s),
      };
    case "VALIDATED":
      return {
        ...s,
        validation: a.validation,
        seqPhase: a.validation.ok ? "valid" : "dirty",
        // A clean deterministic revalidation is the manual recovery path from
        // external drift — the notice has served its purpose.
        driftNotice: a.validation.ok ? null : s.driftNotice,
      };
    case "SHADOW_START":
      return { ...s, shadowPhase: "loading", liveArmed: false };
    case "SHADOW_OK":
      return { ...s, shadow: a.shadow, shadowPhase: "current" };
    case "SHADOW_FAIL":
      return { ...s, shadowPhase: "none", liveArmed: false };
    case "FINGERPRINT_MISMATCH":
      // External drift: staged sequence + shadow are no longer trustworthy
      // (locked decision 17). Requires resequence or manual revalidation.
      // The approval drawer closes — its shadow content just died — and a
      // global blocking alert explains why nothing was written.
      return {
        ...s,
        seqPhase: s.sequence ? "dirty" : "none",
        validation: null,
        fingerprint: null,
        anchoredSourceFingerprint: null,
        planningConfigFingerprint: null,
        overlapGrants: [],
        pinnedRows: [],
        pendingPinnedRows: [],
        shadow: null,
        shadowPhase: "none",
        liveArmed: false,
        acceptedDefects: null,
        driftNotice:
          "Calendar or anchored commitments changed since this plan was staged — nothing was written. Resequence or revalidate before committing.",
        ui: { ...s.ui, approvalOpen: false },
      };
    case "FINGERPRINT_ADOPTED":
      return {
        ...s,
        fingerprint: a.fingerprint,
        anchoredSourceFingerprint: a.anchoredSourceFingerprint,
        planningConfigFingerprint: a.planningConfigFingerprint ?? s.inputs?.planningConfigFingerprint ?? null,
      };
    case "ACCEPT_DEFECTS": {
      // Good-enough override (locked decision 24): only a valid, clean-of-
      // hard-errors staged plan with actual acceptable defects can be
      // accepted; the recorded list is the current findings, verbatim.
      const defects = acceptableDefects(s);
      if (s.seqPhase !== "valid" || s.validation?.ok !== true || defects.length === 0) {
        return s;
      }
      return { ...s, acceptedDefects: defects };
    }
    case "ARM_LIVE":
      // State-level twin of the drawer's disabled arm button: a current
      // clean preview, zero shadow blockers (conflicts / unavailable
      // surfaces), and every acceptable defect either absent or explicitly
      // accepted — the UI guard alone is bypassable by direct dispatch.
      return s.shadowPhase === "current" &&
        s.validation?.ok !== false &&
        shadowBlockers(s).length === 0 &&
        defectsResolved(s)
        ? { ...s, liveArmed: true }
        : s;
    case "DISARM_LIVE":
      return { ...s, liveArmed: false };
    case "COMMIT_START":
      return { ...s, commitPhase: "committing", commitError: null };
    case "COMMIT_ABORT":
      return { ...s, commitPhase: "idle", liveArmed: false };
    case "COMMIT_REJECTED":
      // Server refused before writing anything (single-flight 409, plan
      // refusal 422): plan + shadow stay intact, live disarms, retry allowed.
      return { ...s, commitPhase: "idle", liveArmed: false, commitError: a.error };
    case "COMMIT_DONE": {
      const phase: CommitPhase =
        a.report.status === "ok"
          ? "done"
          : a.report.status === "partial"
            ? "partial"
            : "failed";
      return { ...s, commitPhase: phase, commitReport: a.report, liveArmed: false };
    }
    case "RUNTIME_START":
      return { ...s, runtimeBusy: true, runtimeError: null };
    case "RUNTIME_OK":
      return { ...s, runtimeBusy: false, lastRuntimeAction: a.action };
    case "RUNTIME_FAIL":
      return { ...s, runtimeBusy: false, runtimeError: a.error };
    case "RUNTIME_UNDO_OK":
      return { ...s, runtimeBusy: false, lastRuntimeAction: a.action };
    case "DURATION_MEMORY_START":
      return {
        ...s,
        durationMemory: { ...s.durationMemory, [a.id]: { pending: true, error: null } },
      };
    case "DURATION_MEMORY_OK": {
      // Server-authoritative result lands on the model row: the remembered
      // value (save) or the source fallback (reset) becomes the effective
      // duration. A missing/non-numeric result is ignored — the row keeps its
      // last authoritative value.
      const inputs = s.inputs;
      const done = {
        ...s.durationMemory,
        [a.id]: { pending: false, error: null },
      };
      if (!inputs || !Number.isFinite(a.minutes)) {
        return { ...s, durationMemory: done };
      }
      const blocks = a.minutes / 30;
      const assigned = inputs.assigned.map((row) =>
        row.id === a.id
          ? {
              ...row,
              blocks,
              durationLabel: blocksLabel(blocks),
              durationSource: a.source === "remembered" ? "remembered" : durationSourceOf(a.source),
            }
          : row,
      );
      return {
        ...s,
        inputs: { ...inputs, assigned },
        durationMemory: done,
        // A duration change reshapes the plan payload — dirty + stale shadow,
        // exactly like any other duration edit.
        ...dirtySeq(s),
        ...staleShadow(s),
      };
    }
    case "DURATION_MEMORY_FAIL":
      return {
        ...s,
        durationMemory: { ...s.durationMemory, [a.id]: { pending: false, error: a.error } },
      };
    case "SESSION_RESTORED":
      // Restored plan re-enters as dirty — deterministic revalidation (free)
      // must confirm it before shadow/commit are reachable again. Review
      // state (shadow, defect acceptance) deliberately never restores.
      const restoredPins = s.inputs
        ? mergeRecurringPins(a.pinnedRows ?? [], s.inputs)
        : (a.pinnedRows ?? []);
      const restoredPendingPins = s.inputs
        ? mergeRecurringPins(a.pendingPinnedRows ?? a.pinnedRows ?? [], s.inputs)
        : (a.pendingPinnedRows ?? a.pinnedRows ?? []);
      return {
        ...s,
        acceptedDefects: null,
        overrides: a.overrides,
        placements: a.placements,
        sequence: a.sequence,
        fingerprint: a.fingerprint,
        anchoredSourceFingerprint: a.anchoredSourceFingerprint,
        planningConfigFingerprint: a.planningConfigFingerprint ?? null,
        overlapGrants: a.overlapGrants ?? [],
        pinnedRows: restoredPins,
        pendingPinnedRows: restoredPendingPins,
        seqPhase: a.sequence && a.sequence.some((r) => r.kind === "work") ? "dirty" : s.seqPhase,
        validation: null,
        shadow: null,
        shadowPhase: "none",
        liveArmed: false,
      };
    case "SOURCE_REFRESH_START":
      return { ...s, refresh: { ...s.refresh, phase: "loading", error: null } };
    case "SOURCE_REFRESH_FAIL":
      // Failure keeps the last good view untouched (test matrix § Safety) —
      // only the refresh feedback surface changes.
      return { ...s, refresh: { ...s.refresh, phase: "idle", error: a.error } };
    case "SOURCE_REFRESH_OK": {
      if (s.validDate !== null && s.validDate !== a.inputs.validDate) {
        // Date rollover mid-session: full reset (locked decisions 16/20).
        return {
          ...initialState,
          theme: s.theme,
          loaded: true,
          validDate: a.inputs.validDate,
          inputs: a.inputs,
          capacity: a.inputs.capacity,
          daySetup: a.inputs.daySetup,
          pendingPinnedRows: recurringPinnedRows(a.inputs),
          ledger: a.ledger,
          refresh: { phase: "idle", error: null, lastRefreshed: a.at, summary: null },
        };
      }
      const r = reconcileRefresh({
        prevAssigned: s.inputs?.assigned ?? [],
        nextAssigned: a.inputs.assigned,
        prevAnchored: s.inputs?.anchored ?? [],
        nextAnchored: a.inputs.anchored,
        anchoredOverrides: s.daySetup.anchored,
        frame: {
          anchor: a.inputs.time.anchor,
          effectiveEod: a.inputs.time.effectiveEod,
        },
        overrides: s.overrides,
        placements: s.placements,
        sequence: s.sequence,
      });
      // Raw anchored-source or effective fixed-input drift → staged sequence,
      // shadow, and live approval all die (locked decisions 17/20/21). Only
      // judged against a staged baseline; a plan that never sequenced or
      // shadowed has no approval to invalidate.
      const drift =
        s.fingerprint !== null &&
        (a.fingerprint !== s.fingerprint ||
          a.anchoredSourceFingerprint !== s.anchoredSourceFingerprint ||
          (a.planningConfigFingerprint ?? a.inputs.planningConfigFingerprint ?? "") !==
            s.planningConfigFingerprint);
      const summary = { ...r.summary, invalidated: drift };
      const assignedTouched = summaryHasChanges(r.summary) || r.sequenceTouched;
      const base: AppState = {
        ...s,
        inputs: a.inputs,
        ledger: a.ledger,
        daySetup: s.daySetup.confirmed
          ? { ...s.daySetup, anchored: r.anchoredOverrides }
          : a.inputs.daySetup,
        overrides: r.overrides,
        placements: r.placements,
        sequence: r.sequence,
        pendingPinnedRows: mergeRecurringPins(s.pendingPinnedRows, a.inputs),
        refresh: { phase: "idle", error: null, lastRefreshed: a.at, summary },
      };
      if (drift) {
        return {
          ...base,
          seqPhase: base.sequence && base.sequence.some((x) => x.kind === "work") ? "dirty" : "none",
          validation: null,
          fingerprint: null,
          anchoredSourceFingerprint: null,
          planningConfigFingerprint: null,
          shadow: null,
          shadowPhase: "none",
          liveArmed: false,
          acceptedDefects: null,
          driftNotice:
            "Sources changed since this plan was staged — refresh invalidated the staged sequence. Resequence or revalidate before committing.",
          ui: { ...s.ui, approvalOpen: false },
        };
      }
      if (assignedTouched) {
        // Assigned-only changes: compatible placements survive, but the plan
        // must re-earn "valid" through deterministic revalidation (LD 20) and
        // any defect acceptance dies with the changed plan content.
        return { ...base, acceptedDefects: null, ...dirtySeq(base), ...staleShadow(base) };
      }
      // Clean no-change refresh: acceptance survives (LD 24 invalidates on
      // refresh DRIFT, not on a refresh that proved nothing moved).
      return base;
    }
    case "LEDGER_UPDATED":
      return { ...s, ledger: a.ledger };
    case "THEME_SET":
      return { ...s, theme: a.theme };
    case "UI":
      return { ...s, ui: { ...s.ui, ...a.patch } };
    default:
      return s;
  }
}

// -- tiny local time math (avoid import cycle with model/time) --------------
function toMin(hhmm: string): number {
  const [h, m] = hhmm.split(":").map(Number);
  return h * 60 + m;
}
function addMin(hhmm: string, delta: number): string {
  const m = (((toMin(hhmm) + delta) % 1440) + 1440) % 1440;
  return `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;
}

function recurringPinnedRows(inputs: PlanInputs): SequenceRow[] {
  return inputs.assigned
    .filter((item) => item.isRecurring && item.scheduledStart && item.blocks > 0)
    .map((item) => ({
      id: item.id,
      start: item.scheduledStart as string,
      end: addMin(item.scheduledStart as string, item.blocks * 30),
      zone: null,
      kind: "work" as const,
      wire: {
        id: item.id,
        start: item.scheduledStart,
        end: addMin(item.scheduledStart as string, item.blocks * 30),
        zone: null,
      },
    }));
}

function mergeRecurringPins(current: SequenceRow[], inputs: PlanInputs): SequenceRow[] {
  const recurringIds = new Set(
    inputs.assigned.filter((item) => item.isRecurring).map((item) => item.id),
  );
  return [
    ...current.filter((row) => !recurringIds.has(row.id)),
    ...recurringPinnedRows(inputs),
  ];
}

function upsertPinnedRow(
  pinnedRows: SequenceRow[],
  sequence: SequenceRow[],
  id: string,
): SequenceRow[] {
  const row = sequence.find((candidate) => candidate.id === id && candidate.kind === "work");
  if (!row) return pinnedRows.filter((candidate) => candidate.id !== id);
  return [...pinnedRows.filter((candidate) => candidate.id !== id), row];
}

// -- selectors ---------------------------------------------------------------

export function queueState(s: AppState, id: string): QueueState {
  const item = s.inputs?.assigned.find((i) => i.id === id);
  if (!item) return "needs-placement";
  const override = s.overrides[id];
  if (override && !override.included) return "excluded";
  const blocks = override?.blocks ?? item.blocks;
  if (blocks === 0) return "background";
  // T27: a timed recurring row is placement-immune — never offered for
  // placement even if its pin was cleared (the server re-pins it anyway).
  if (item.isRecurring && item.scheduledStart) return "scheduled";
  if (s.pendingPinnedRows.some((r) => r.id === id)) return "scheduled";
  if (s.sequence?.some((r) => r.id === id && r.kind === "work")) return "scheduled";
  return "needs-placement";
}

/** Included, capacity-consuming items (queue + capacity preview "selected"). */
export function includedItems(s: AppState) {
  return (s.inputs?.assigned ?? []).filter((i) => {
    const o = s.overrides[i.id];
    return o ? o.included : true;
  });
}

export function effectiveBlocks(s: AppState, id: string): number {
  const item = s.inputs?.assigned.find((i) => i.id === id);
  if (!item) return 0;
  return s.overrides[id]?.blocks ?? item.blocks;
}

/** Apply dated anchored overrides to the read model without pretending they
    changed upstream config. Calendar rows are immutable and never receive an
    override. */
export function effectiveAnchoredBlocks(s: AppState) {
  return (s.inputs?.anchored ?? []).map((block) => {
    if (block.kind === "calendar") {
      // T28 + FEEDBACK-09: the only overrides a calendar row honors are
      // per-day plan participation (skipToday) and the local accounting
      // projection (blocks) — time/existence stay imported truth (LD19).
      // blocks rewrites the accounted duration so Calendar impact counts it,
      // while the event's wall-clock window (start/end) never moves.
      const o = s.daySetup.anchored[block.id];
      if (!o) return block;
      const skip = o.skipToday === true;
      const durationMin = o.blocks == null ? block.durationMin : o.blocks * 30;
      return skip === block.skipToday && durationMin === block.durationMin
        ? block
        : { ...block, on: true, skipToday: skip, durationMin };
    }
    const override = s.daySetup.anchored[block.id];
    if (!override) return block;
    return {
      ...block,
      start: override.time ?? block.start,
      durationMin:
        override.blocks == null ? block.durationMin : override.blocks * 30,
      on: override.on,
      skipToday: override.skipToday,
    };
  });
}

export type DockState =
  | "setup" // day setup not yet confirmed
  | "sequence" // ready to auto-sequence (or manual)
  | "sequencing"
  | "review" // valid sequence -> shadow preview available
  | "fix" // hard errors present
  | "preview" // shadow current -> approval drawer
  | "committing"
  | "verified" // commit done, verify clean
  | "partial" // commit partial/failed
  | "budget-manual"; // ledger spent, manual path only

/** Single source for the sticky action dock (locked decision 1). */
export function dockState(s: AppState): DockState {
  if (s.commitPhase === "committing") return "committing";
  if (s.commitPhase === "done") return "verified";
  if (s.commitPhase === "partial" || s.commitPhase === "failed") return "partial";
  if (!s.daySetup.confirmed) return "setup";
  if (s.seqPhase === "sequencing") return "sequencing";
  if (s.validation && !s.validation.ok) return "fix";
  if (s.shadowPhase === "current") return "preview";
  if (s.seqPhase === "valid") return "review";
  if ((s.ledger?.remaining ?? 0) <= 0) return "budget-manual"; // seqPhase "valid" already returned above
  return "sequence";
}

export function canAutoSequence(s: AppState): boolean {
  return (
    s.daySetup.confirmed &&
    (s.ledger?.remaining ?? 0) > 0 &&
    s.seqPhase !== "sequencing" &&
    s.commitPhase === "idle"
  );
}

/** T29: the fingerprint granted overlaps are judged against — the staged
    plan's config snapshot, falling back to the loaded inputs' (same
    resolution ExecutionView uses for momentsOf clustering). */
function grantFingerprint(s: AppState): string | null {
  return s.planningConfigFingerprint ?? s.inputs?.planningConfigFingerprint ?? null;
}

/** Acceptable defects (locked decision 24): soft validation warnings,
    overlaps among movable work, overassignment — verbatim strings. Distinct
    from hard blockers (drift, source failure, shadow conflicts, stale
    shadow, spent-ledger arming), which have their own non-overridable gates.
    T29: an overlap covered by an exact current grant is reasoned, not a
    defect — it needs no acceptance and surfaces as an info alert instead. */
export function acceptableDefects(s: AppState): string[] {
  return [
    ...(s.validation?.warnings ?? []),
    ...workOverlaps(s.sequence, s.overlapGrants, grantFingerprint(s)),
    ...(s.capacity?.overassigned
      ? [`Overassigned — ${s.capacity.remaining}`]
      : []),
  ];
}

/** True when every current acceptable defect is either absent or explicitly
    accepted as-is. Gates shadow preview, arming, and live commit. */
export function defectsResolved(s: AppState): boolean {
  return defectsCovered(acceptableDefects(s), s.acceptedDefects);
}

export function canShadow(s: AppState): boolean {
  return (
    s.seqPhase === "valid" &&
    s.validation?.ok === true &&
    s.commitPhase === "idle" &&
    s.shadowPhase !== "loading" &&
    defectsResolved(s)
  );
}

/** Every CONFLICT the shadow diff emits carries a `reason` saying WHY
    (`shadow.py` — "daily note not found", "target missing: …", "unrecognized
    manifest row: …"). Dropping it produced blockers like "vault conflict: #
    TDTB Plan", which names the write that is blocked but not the cause —
    2026-07-27 that cost a round trip to work out that today's daily note
    simply did not exist yet. The data was always there; only the display
    threw it away. */
function blockerReason(detail: Record<string, unknown> | undefined): string {
  const reason = detail?.reason;
  return typeof reason === "string" ? reason.trim() : "";
}

/** Shadow-level blockers (locked decisions 9/17): conflict entries and
    unavailable surfaces in the CURRENT preview block live commit — same list
    the approval drawer renders, enforced here so state can't outrun the UI. */
export function shadowBlockers(s: AppState): string[] {
  if (!s.shadow) return [];
  return [
    ...s.shadow.entries
      .filter((e) => e.classification === "conflict")
      .map((e) => {
        const why = blockerReason(e.detail);
        return `${e.system} conflict: ${e.name}${why ? ` — ${why}` : ""}`;
      }),
    ...s.shadow.unavailableSurfaces.map((x) => `surface unavailable: ${x}`),
  ];
}

/** Live commit: current clean shadow + armed second click, nothing stale,
    zero shadow blockers, every acceptable defect accepted or absent. */
export function canLiveCommit(s: AppState): boolean {
  return (
    s.shadowPhase === "current" &&
    s.liveArmed &&
    s.validation?.ok === true &&
    s.seqPhase === "valid" &&
    s.commitPhase === "idle" &&
    shadowBlockers(s).length === 0 &&
    defectsResolved(s)
  );
}

/** T20: runtime verbs act only on a plan that actually committed (the
    manifest is what the server resolves targets against), one at a time. */
export function canRuntimeAct(s: AppState): boolean {
  return s.commitReport !== null && !s.runtimeBusy;
}

/** Global alert roll-up (locked decision 6): exact issues, one summary.
    T29 adds the non-gating "info" level for granted allowed overlaps. */
export interface Alert {
  level: "error" | "warning" | "info";
  text: string;
}

export function alerts(s: AppState): Alert[] {
  const out: Alert[] = [];
  if (s.driftNotice) out.push({ level: "error", text: s.driftNotice });
  for (const w of s.inputs?.sourceWarnings ?? [])
    out.push({ level: "warning", text: w });
  for (const e of s.validation?.hardErrors ?? [])
    out.push({ level: "error", text: e });
  for (const w of s.validation?.warnings ?? [])
    out.push({ level: "warning", text: w });
  for (const o of workOverlaps(s.sequence, s.overlapGrants, grantFingerprint(s)))
    out.push({ level: "warning", text: o });
  for (const o of allowedOverlaps(s.sequence, s.overlapGrants, grantFingerprint(s)))
    out.push({ level: "info", text: o });
  if (s.capacity?.overassigned)
    out.push({ level: "warning", text: `Overassigned — ${s.capacity.remaining}` });
  if (s.seqPhase === "failed" && s.seqError)
    out.push({ level: "error", text: s.seqError });
  if (s.commitError) out.push({ level: "error", text: s.commitError });
  for (const f of s.commitReport?.verifyFailures ?? [])
    out.push({ level: "error", text: f });
  return out;
}
