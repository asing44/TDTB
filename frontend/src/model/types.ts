/* types.ts — typed domain model for the cockpit.
   Mirrors the FastAPI contracts (app/main.py) after the assigned-only
   projection: no pool, no ranked suggested list (locked decision 2).

   Allocator-rewrite T6 narrows that rule rather than dropping it: the digest's
   two DERIVED forgot-strip lists (unassigned_candidates, stale_assigned) do
   cross, because locked decision 8 makes them a first-class load-time surface.
   They are capped {name, path, reason} summaries, not the ranked pool — the
   pool itself still never crosses.

   Sequence identity is name-keyed — `id` = the digest item's `name`
   (T1 contract in main.py). */

export type Source = "vault" | "todoist";

/** Resolved duration source label (mirrors the backend resolver's
    source_label). "remembered" means a durable server-side memory entry won;
    every other value is a deterministic source resolution. */
export type DurationSourceLabel =
  | "remembered"
  | "tag"
  | "native"
  | "preset"
  | "type"
  | "default";

export interface AssignedItem {
  id: string; // = name (name-keyed sequence identity)
  name: string;
  path: string | null;
  source: Source;
  types: string[];
  urgency: string | null;
  deadline: string | null; // ISO date
  priorityScore: number;
  /** Resolved duration in 30-min blocks (locked decision 14 precedence).
      Fractional blocks support 15-minute shaping; 0 = all day: visible,
      unscheduled, and never occupies timeline capacity. */
  blocks: number;
  /** Human duration label, e.g. "1hr 30min" or "All day". */
  durationLabel: string;
  /** Todoist task id for todoist-sourced rows; null for vault rows. Carried
      so the copy-prompt fallback can instruct update-by-id (the app's own
      commit convention) instead of duplicate creation. */
  todoistId: string | null;
  /** Canonical stable source identity (todoist:<id> or normalized vault
      path) — the key the duration-memory mutation API operates on. Absent
      only when the wire carries neither; a display name alone is never an
      identity. */
  identity?: string | null;
  /** Where the row's effective duration came from: "remembered" (durable
      server memory) or a deterministic source label. Absent for legacy
      payloads without duration-memory metadata = source-resolved behavior. */
  durationSource?: DurationSourceLabel;
  /** Recurring Todoist tasks are immutable existing commitments, not work for
      the planner to place. scheduledStart is their current Todoist wall time. */
  isRecurring?: boolean;
  scheduledStart?: string | null;
  /** Todoist labels, verbatim (T23 source context); absent/[] for vault rows. */
  labels?: string[];
  /** Obsidian parent/child relationship, preserved for sequencing context. */
  relatesTo?: string | null;
}

export type AnchoredKind = "hard" | "window" | "calendar" | "template";
/** Calendar capacity classes mirror the backend wire (T12j). `quarantined`
    is a KNOWN calendar title the user has not classified yet — excluded from
    planning capacity and walls exactly like `ignored`, but displayed under
    its own class so the UI can explain why it costs zero (FEEDBACK-04). */
export type CalendarCapacityClass = "fixed" | "work" | "ignored" | "quarantined";

export interface AnchoredBlock {
  id: string; // = name
  name: string;
  kind: AnchoredKind;
  start: string | null; // HH:MM 24h; window blocks: window open
  end: string | null; // window blocks: window close; hard: computed end
  durationMin: number;
  overlapAllowed: boolean;
  on: boolean;
  skipToday: boolean;
  /** Calendar-origin evidence preserved end-to-end by T12j. Absent for
      config-backed anchored blocks and tolerated for older fixtures. */
  calendarId?: string | null;
  calendarTitle?: string | null;
  capacityClass?: CalendarCapacityClass;
}

export interface TimeFrame {
  now: string;
  anchor: string;
  effectiveEod: string;
  eodNote: string | null;
  configEod: string;
  totalBlocks: number;
}

/** Server-verbatim capacity (capacity.py as_dict) — the cockpit renders these
    numbers and readout strings untouched (locked decision 6 + ui-revamp LD2). */
export interface Capacity {
  total: number;
  fixed: number;
  anchored: number;
  habits: number;
  mint: number;
  selected: number;
  buffer: number;
  free: number; // signed, never clamped
  overassigned: boolean;
  availableForSelection: number;
  remaining: string;
  ratio: string;
  legend: string;
  counters: string;
  /** Exclusive in-frame meeting union inside the work allocation envelope. */
  workBusy?: number;
  /** Work-busy blocks beyond the configured work allotment. */
  workOverflow?: number;
}

export interface Ledger {
  today: string;
  spent: number;
  cap: number;
  remaining: number;
}

export type Buffering = "standard" | "minimal" | "off";

export interface AnchoredOverride {
  on: boolean;
  skipToday: boolean;
  time: string | null; // HH:MM start override
  /** Today-only 30-minute blocks. 0 is legal: visible background/no capacity. */
  blocks?: number | null;
}

export interface Captures {
  intention: string;
  forMeegy: string; // megan_nicety on the wire
  stoic: string; // stoic_intention on the wire
}

export interface DaySetup {
  anchor: string | null; // HH:MM override
  eod: string | null;
  buffering: Buffering;
  anchored: Record<string, AnchoredOverride>;
  captures: Captures;
  confirmed: boolean;
  /** Absent = preserve dated override; null = reset to config. */
  dayPreset?: string | null;
  /** Integer minutes. Absent preserves, null resets, 0 disables Mint. */
  workAllotmentMinutes?: number | null;
  /** Today-only schedulable placement choices, including Mint sessions. */
  schedulable?: Record<string, SchedulableOverride>;
}

export interface MintSession {
  id: string;
  name: string;
  slot: string;
  start: string;
  end: string;
}

export interface SchedulableOverride {
  on?: boolean;
  n?: number;
  sessions?: string[];
}

export interface DayPreset {
  name: string;
  days: string[];
  enabledZones: string[];
  workAllotmentMinutes: number | null;
}

export interface DaySemantics {
  availablePresets: DayPreset[];
  selectedPreset: DayPreset | null;
  resolutionSource: string;
  enabledZones: string[];
  effectiveAllotmentMinutes: number;
  defaultAllotmentMinutes: number;
  mintEnabled: boolean;
  warnings: string[];
  errors: string[];
  overlapPermissionsRaw: string;
  mintSessions?: MintSession[];
}

export interface OverlapGrant {
  primaryId: string;
  companionId: string;
  primaryInterval: { start: string; end: string };
  companionInterval: { start: string; end: string };
  reason: string;
  planningConfigFingerprint: string;
}

/** T20: one journaled runtime action over a committed plan item. Server is
    authoritative — this is the journal entry projection the UI needs for
    status banners, the undo chip, and partial-failure states. */
export interface RuntimeAction {
  id: string;
  verb: string;
  targetName: string;
  status:
    | "applied"
    | "failed"
    | "compensated"
    | "partial"
    | "undone"
    | "undo_failed"
    | "pending";
  error: string | null;
  duplicate: boolean;
}

/** Today-only shaping (locked decisions 2/16): representable in the existing
    sequence/commit payload, persists across same-date refresh, never mutates
    upstream assignment truth. */
export interface TodayOverride {
  included: boolean;
  blocks: number | null; // null = no duration override (use resolved blocks)
}

export interface SequenceRow {
  id: string;
  start: string; // HH:MM
  end: string;
  zone: string | null;
  kind: "work" | "zone"; // zone = permeable backdrop row, never validated
  /** Original record retained so a pin can round-trip exactly. */
  wire?: Record<string, unknown>;
}

export interface Validation {
  ok: boolean;
  hardErrors: string[];
  warnings: string[];
}

export type ShadowClassification =
  | "would-create"
  | "would-update"
  | "no-op"
  | "conflict"
  | "unavailable";

export interface ShadowEntry {
  step: string;
  system: "todoist" | "vault" | "calendar";
  action: string;
  name: string;
  idOrPath: string;
  time: string | null;
  durationMin: number;
  classification: ShadowClassification;
  detail: Record<string, unknown>;
}

export interface ShadowDiff {
  entries: ShadowEntry[];
  unavailableSurfaces: string[];
  counts: Record<ShadowClassification, number>;
}

export interface CommitSurface {
  system: string;
  status: "ok" | "failed" | "skipped";
  detail: string | null;
}

export interface CommitReport {
  status: "ok" | "partial" | "failed";
  surfaces: CommitSurface[];
  verifyFailures: string[];
  /** FEEDBACK-23: machine-canonical per-failure records when the server
      provides them. The UI formats the 12-hour display from these values
      and keeps raw ISO/timezone for chasing a bad write. Absent for legacy
      payloads or non-due failures (calendar/vault/readback), where the raw
      string in verifyFailures is the display. */
  verifyDetails?: DueVerificationDetail[];
}

/** One structured commit verification failure (FEEDBACK-23). Machine fields
    stay canonical — 24h HH:MM intent/live, raw ISO due, IANA timezone — and
    are never display-formatted on the wire; display is a UI concern. */
export interface DueVerificationDetail {
  kind: "due" | "plain";
  name: string;
  intent: string | null;
  live: string | null;
  liveRaw: string | null;
  liveTimezone: string | null;
  reason: string;
  message: string;
}

export type SourceHealth = "ok" | "degraded" | "failed";

/** T19 — deterministic Live micro-adventure state (locked decision 25). */
export interface MicroIdea {
  id: string;
  idea: string;
  category: string;
}

export interface MicroAdventure {
  /** Today's effective selection — dated override when present, else the
      server's deterministic LRU auto-pick. Null = Live renders plain. */
  pick: MicroIdea | null;
  source: "auto" | "override";
  /** Eligible rotation pool, LRU order, auto-pick first, capped at 8. */
  pool: MicroIdea[];
  streak: number;
  pendingConfirm: { date: string; id: string; idea: string } | null;
}

/** T6: one forgot-strip row. Deliberately the billed AuditReport's shape, so
    the deterministic and model-derived surfaces stay interchangeable. */
export interface ForgotItem {
  name: string;
  path: string | null;
  /** Why it surfaced, in the server's words — ≤140 chars, render verbatim. */
  reason: string;
}

/** IMP-07: one row dropped from today's plan via the Drop from plan verb.
    Date-scoped server-side (runstate); the identity is the canonical source
    identity (todoist:<id> / vault path), and the row is eligible again
    tomorrow. */
export interface DroppedItem {
  identity: string;
  name: string;
  droppedAt: string | null;
}

export interface PlanInputs {
  validDate: string;
  assigned: AssignedItem[];
  /** T6 forgot-strip: pool rows carrying a "you may have forgotten this"
      signal (overdue, due today, deferred, 4-crit). Capped at 5 server-side. */
  unassignedCandidates: ForgotItem[];
  /** T6 forgot-strip: assigned rows with evidence they aren't moving. */
  staleAssigned: ForgotItem[];
  /** IMP-07: rows excluded from today via Drop from plan (date-scoped). */
  droppedToday: DroppedItem[];
  anchored: AnchoredBlock[];
  /** Raw config specs before Day Setup overrides (locked decision 21). */
  anchoredSourceFingerprint: string;
  habitsNote: string | null;
  time: TimeFrame;
  capacity: Capacity;
  daySetup: DaySetup;
  daySemantics: DaySemantics;
  planningConfigFingerprint: string;
  sourceWarnings: string[];
  sourceCounts: { vault: number; todoist: number; calendar: number };
  sourceHealth: SourceHealth;
  microAdventure: MicroAdventure;
}

/** Fixed-input snapshot for the drift fingerprint (locked decision 17):
    calendar commitments + effective anchored blocks, normalized. */
export interface FixedInputs {
  anchoredSourceFingerprint: string;
  planningConfigFingerprint?: string;
  calendar: Array<{ name: string; start: string | null; durationMin: number;
    /** T28: per-day plan participation; absent = attending. */
    attending?: boolean }>;
  anchored: Array<{
    name: string;
    start: string | null;
    durationMin: number;
    on: boolean;
    skipToday: boolean;
  }>;
}

export type QueueState =
  | "needs-placement"
  | "scheduled"
  | "excluded"
  | "background";
