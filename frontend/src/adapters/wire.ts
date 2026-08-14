/* wire.ts — pure wire↔model mapping for the production API adapter (T5).
   One function per endpoint response, pinned by contract-fixture tests
   (src/adapters/contract-fixtures/*.json — captured from the real FastAPI
   routes, sanitized by construction). The assigned-only projection lives
   here: digest.suggested never crosses into component state (locked
   decision 2). Raw wire payloads stay adapter-internal for POST bodies.

   Allocator-rewrite T6 revises that constraint deliberately, for exactly two
   fields: digest.unassigned_candidates and digest.stale_assigned DO cross, as
   locked decision 8 makes the forgot-strip a first-class load-time surface.
   They are server-capped {name, path, reason} summaries — the ranked pool
   still never crosses, and projectPlanInputs still drops digest.suggested. */

import type {
  AnchoredBlock,
  AnchoredKind,
  AssignedItem,
  Capacity,
  CommitReport,
  CommitSurface,
  DaySetup,
  FixedInputs,
  ForgotItem,
  Ledger,
  PlanInputs,
  SequenceRow,
  ShadowDiff,
  ShadowClassification,
  ShadowEntry,
  SourceHealth,
  Validation,
  DayPreset,
  DaySemantics,
  MicroAdventure,
  MicroIdea,
  MintSession,
  OverlapGrant,
  SchedulableOverride,
} from "../model/types";

// Wire payloads are untyped JSON — this alias marks the boundary.
export type Wire = Record<string, any>;

// -- time helpers ------------------------------------------------------------

/** "7:45 AM" | "12:00 PM" | "09:15" → "HH:MM" 24h; null when unparseable. */
export function to24h(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const m = value.trim().match(/^(\d{1,2}):(\d{2})(?:\s*(AM|PM))?$/i);
  if (!m) return null;
  let h = Number(m[1]);
  const min = Number(m[2]);
  if (min > 59) return null;
  const ap = m[3]?.toUpperCase();
  if (ap) {
    if (h < 1 || h > 12) return null;
    if (ap === "PM" && h !== 12) h += 12;
    if (ap === "AM" && h === 12) h = 0;
  } else if (h > 23) {
    return null;
  }
  return `${String(h).padStart(2, "0")}:${String(min).padStart(2, "0")}`;
}

/** "80m" | "1h20m" | "2h" | bare minutes → minutes; null when unparseable. */
export function durationMinutes(value: unknown): number | null {
  if (value == null) return null;
  if (typeof value === "number" && Number.isFinite(value)) return Math.trunc(value);
  const s = String(value).trim();
  const hm = s.match(/(\d+)\s*h/i);
  const mm = s.match(/(\d+)\s*m/i);
  if (hm || mm) return (hm ? Number(hm[1]) : 0) * 60 + (mm ? Number(mm[1]) : 0);
  return /^\d+(\.\d+)?$/.test(s) ? Math.trunc(Number(s)) : null;
}

function minutesOf(hhmm: string): number {
  const [h, m] = hhmm.split(":").map(Number);
  return h * 60 + m;
}

/** Human duration label from blocks: 3 → "1hr 30min", 0 → "All day". */
export function blocksLabel(blocks: number): string {
  if (blocks === 0) return "All day";
  const m = Math.round(blocks * 30);
  if (m < 60) return `${m}min`;
  return m % 60 === 0 ? `${m / 60}hr` : `${Math.floor(m / 60)}hr ${m % 60}min`;
}

// -- per-endpoint projections ------------------------------------------------

export function projectAssigned(row: Wire): AssignedItem {
  const source = row.source === "todoist" ? "todoist" : "vault";
  const blocks =
    typeof row.blocks === "number" && Number.isFinite(row.blocks) ? row.blocks : 1;
  return {
    id: String(row.name),
    name: String(row.name),
    path: source === "todoist" ? null : (row.path ?? null),
    source,
    types: Array.isArray(row.types) ? row.types.map(String) : [],
    urgency: row.urgency == null ? null : String(row.urgency),
    deadline: row.deadline ?? null,
    priorityScore: Number(row.priority_score ?? 0),
    blocks,
    durationLabel: blocksLabel(blocks),
    todoistId:
      source === "todoist" && row.todoist_id != null && String(row.todoist_id) !== ""
        ? String(row.todoist_id)
        : null,
    isRecurring: source === "todoist" && row.is_recurring === true,
    scheduledStart: source === "todoist" ? to24h(row.scheduled_start) : null,
    labels: Array.isArray(row.labels) ? row.labels.map(String) : [],
    relatesTo: row.relates_to == null ? null : String(row.relates_to),
  };
}

export function projectAnchored(row: Wire): AnchoredBlock {
  const name = String(row.Block ?? row.Start ?? "(unnamed)");
  const kind: AnchoredKind =
    row.source === "calendar"
      ? "calendar"
      : row.Type === "window"
        ? row.overlap_allowed === true || String(row.overlap_allowed).toLowerCase() === "yes"
          ? "template"
          : "window"
        : "hard";
  const start = to24h(row.time) ?? to24h(row.Start);
  const end = to24h(row.End);
  let durationMin = durationMinutes(row.Duration) ?? 0;
  if (!durationMin && start && end) {
    durationMin = (minutesOf(end) - minutesOf(start) + 1440) % 1440;
  }
  return {
    id: name,
    name,
    kind,
    start,
    end,
    durationMin,
    overlapAllowed:
      row.overlap_allowed === true ||
      String(row.overlap_allowed ?? "").toLowerCase() === "yes",
    on: row.on !== false && row.skip_today !== true,
    skipToday: row.skip_today === true,
    calendarId:
      row.source === "calendar" && row.calendar_id != null
        ? String(row.calendar_id)
        : null,
    calendarTitle:
      row.source === "calendar" && row.calendar_title != null
        ? String(row.calendar_title)
        : null,
    capacityClass:
      row.source === "calendar" &&
      (row.capacity_class === "work" ||
        row.capacity_class === "ignored" ||
        row.capacity_class === "quarantined")
        ? row.capacity_class
        : row.source === "calendar"
          ? "fixed"
          : undefined,
  };
}

/** Two routes emit capacity in two shapes: /plan-inputs flattens the segments
    onto the capacity object, /capacity-preview nests them under `segments`.
    Read both, and coerce — a missing key must land as 0, never undefined.

    This was a live defect before T7/T8 and invisible: only `mint` carried the
    fallback, so after ANY duration change the other five segments became
    undefined, the segmented bar silently emptied, and the chips row filtered
    itself away on `> 0`. The server's `remaining` string stayed correct, so
    nothing looked wrong. T7's live readout computes FROM these numbers, which
    turned the silent hole into a visible "NaN blk". */
function segment(wire: Wire, key: string): number {
  return Number(wire[key] ?? wire.segments?.[key] ?? 0);
}

export function projectCapacity(wire: Wire): Capacity {
  return {
    total: Number(wire.total ?? 0),
    fixed: segment(wire, "fixed"),
    anchored: segment(wire, "anchored"),
    habits: segment(wire, "habits"),
    mint: segment(wire, "mint"),
    selected: segment(wire, "selected"),
    buffer: segment(wire, "buffer"),
    free: Number(wire.free ?? 0),
    overassigned: wire.overassigned === true,
    availableForSelection: Number(wire.available_for_selection ?? 0),
    remaining: String(wire.remaining ?? ""),
    ratio: String(wire.ratio ?? ""),
    legend: String(wire.legend ?? ""),
    counters: String(wire.counters ?? ""),
    workBusy: Number(wire.work_busy ?? wire.workBusy ?? 0),
    workOverflow: Number(wire.work_overflow ?? wire.workOverflow ?? 0),
  };
}

export function projectLedger(wire: Wire): Ledger {
  return {
    today: String(wire.today),
    spent: Number(wire.spent),
    cap: Number(wire.cap),
    remaining: Number(wire.remaining),
  };
}

export function projectDaySetup(wire: Wire, confirmed: boolean): DaySetup {
  const anchored: DaySetup["anchored"] = {};
  for (const o of wire.anchored ?? []) {
    if (o && o.id != null) {
      anchored[String(o.id)] = {
        on: o.on === true,
        skipToday: o.skip_today === true,
        time: to24h(o.time),
        blocks:
          typeof o.blocks === "number" && Number.isFinite(o.blocks)
            ? Math.max(0, Math.trunc(o.blocks))
            : null,
      };
    }
  }
  const buffering =
    wire.buffering === "minimal" || wire.buffering === "off"
      ? wire.buffering
      : "standard";
  const schedulable: Record<string, SchedulableOverride> = {};
  if (wire.schedulable && typeof wire.schedulable === "object") {
    for (const [key, raw] of Object.entries(wire.schedulable)) {
      if (!raw || typeof raw !== "object") continue;
      const value = raw as Wire;
      schedulable[key] = {
        ...(typeof value.on === "boolean" ? { on: value.on } : {}),
        ...(typeof value.n === "number" && Number.isFinite(value.n)
          ? { n: value.n }
          : {}),
        ...(Array.isArray(value.sessions)
          ? { sessions: value.sessions.map(String) }
          : {}),
      };
    }
  }
  const result: DaySetup = {
    anchor: to24h(wire.anchor),
    eod: to24h(wire.eod),
    buffering,
    anchored,
    captures: {
      intention: String(wire.intention ?? ""),
      forMeegy: String(wire.megan_nicety ?? ""),
      stoic: String(wire.stoic_intention ?? ""),
    },
    // FEEDBACK-24: confirmation is the server's explicit flag
    // (day_setup_confirmed on /plan-inputs), never inferred from "any key
    // echoed" — a skeleton runstate can echo schedulable/anchor keys without
    // the user ever confirming Day Setup.
    confirmed,
  };
  if (Object.prototype.hasOwnProperty.call(wire, "day_preset")) {
    result.dayPreset = wire.day_preset == null ? null : String(wire.day_preset);
  }
  if (Object.prototype.hasOwnProperty.call(wire, "work_allotment_minutes")) {
    result.workAllotmentMinutes = wire.work_allotment_minutes == null
      ? null
      : Number(wire.work_allotment_minutes);
  }
  if (Object.keys(schedulable).length > 0) result.schedulable = schedulable;
  return result;
}

function projectEnabledZones(raw: unknown): string[] {
  return (Array.isArray(raw) ? raw : [])
    .map((zone) =>
      typeof zone === "string" ? zone : String((zone as Wire)?.name ?? ""),
    )
    .filter(Boolean);
}

function projectPreset(wire: Wire): DayPreset {
  return {
    name: String(wire.name ?? ""),
    days: (wire.days ?? []).map(String),
    enabledZones: projectEnabledZones(wire.enabled_zones),
    workAllotmentMinutes: wire.work_allotment_minutes == null
      ? null
      : Number(wire.work_allotment_minutes),
  };
}

export function projectDaySemantics(wire: Wire): DaySemantics {
  const mintSessions = Array.isArray(wire.mint_sessions)
    ? wire.mint_sessions
        .map((raw: Wire): MintSession | null => {
          const start = to24h(raw?.start);
          const end = to24h(raw?.end);
          const id = String(raw?.id ?? "").trim();
          const name = String(raw?.name ?? "").trim();
          if (!id || !name || !start || !end) return null;
          return {
            id,
            name,
            slot: String(raw?.slot ?? name).trim(),
            start,
            end,
          };
        })
        .filter((session): session is MintSession => session !== null)
    : [];
  return {
    availablePresets: (wire.available_presets ?? []).map(projectPreset),
    selectedPreset: wire.selected_preset ? projectPreset(wire.selected_preset) : null,
    resolutionSource: String(wire.resolution_source ?? ""),
    enabledZones: projectEnabledZones(wire.enabled_zones),
    effectiveAllotmentMinutes: Number(wire.effective_allotment_minutes ?? 0),
    defaultAllotmentMinutes: Number(wire.default_allotment_minutes ?? 0),
    mintEnabled: wire.mint_enabled === true,
    warnings: (wire.warnings ?? []).map(String),
    errors: (wire.errors ?? []).map(String),
    overlapPermissionsRaw: String(wire.overlap_permissions_raw ?? ""),
    mintSessions,
  };
}

export function sourceHealthOf(warnings: string[]): SourceHealth {
  return warnings.length > 0 ? "degraded" : "ok";
}

/** Calendar is a FIXED input: a calendar degrade warning means the fixed-input
    read cannot be trusted (locked decision 17). */
export function calendarWarnings(warnings: string[]): string[] {
  return warnings.filter((w) => /calendar/i.test(w));
}

/** T19: micro_adventure projection — tolerant of the field's absence (older
    backends) and of partial shapes; degrades to a plain-Live no-pick state. */
export function projectMicroAdventure(raw: Wire | null | undefined): MicroAdventure {
  const idea = (r: Wire | null | undefined): MicroIdea | null => {
    if (!r || typeof r !== "object") return null;
    const id = String(r.id ?? "").trim();
    const text = String(r.idea ?? "").trim();
    if (!id || !text) return null;
    return { id, idea: text, category: String(r.category ?? "") };
  };
  const pick = idea(raw?.pick);
  const pending = raw?.pending_confirm;
  return {
    pick,
    source: raw?.source === "override" ? "override" : "auto",
    pool: Array.isArray(raw?.live_pool)
      ? (raw!.live_pool as Wire[]).map(idea).filter((p: MicroIdea | null): p is MicroIdea => p !== null)
      : [],
    streak: Number(raw?.streak ?? 0) || 0,
    pendingConfirm:
      pending && typeof pending === "object" && pending.id
        ? {
            date: String(pending.date ?? ""),
            id: String(pending.id),
            idea: String(pending.idea ?? ""),
          }
        : null,
  };
}

/** T6: forgot-strip rows. Server caps the list at 5 and the reason at 140
    chars; the cap is re-applied here so a stale or hand-edited payload can
    never flood the strip. A row with no name is dropped — there'd be nothing
    to act on. */
export function projectForgotList(rows: unknown): ForgotItem[] {
  if (!Array.isArray(rows)) return [];
  return rows
    .filter((r): r is Wire => !!r && typeof r === "object")
    .map((r) => ({
      name: String(r.name ?? ""),
      path: r.path ? String(r.path) : null,
      reason: String(r.reason ?? "").slice(0, 140),
    }))
    .filter((r) => r.name !== "")
    .slice(0, FORGOT_LIST_CAP);
}

export const FORGOT_LIST_CAP = 5;

/** IMP-07: rows the server excluded from today via Drop from plan. Shape is
    runstate's {identity, name, dropped_at}; the identity is canonical source
    identity (todoist:<id> / vault path). A row with no name or identity is
    dropped — there would be nothing to render or re-identify. */
export function projectDroppedToday(rows: unknown): import("../model/types").DroppedItem[] {
  if (!Array.isArray(rows)) return [];
  return rows
    .filter((r): r is Wire => !!r && typeof r === "object")
    .map((r) => ({
      identity: String(r.identity ?? ""),
      name: String(r.name ?? ""),
      droppedAt: r.dropped_at ? String(r.dropped_at) : null,
    }))
    .filter((r) => r.name !== "" && r.identity !== "");
}

export function projectPlanInputs(wire: Wire): PlanInputs {
  const warnings = (wire.source_warnings ?? []).map(String);
  const habits = wire.habits ?? {};
  const habitsNote =
    habits.total > 0
      ? `${habits.outstanding} of ${habits.total} habits outstanding · ~${habits.est_minutes}min`
      : null;
  return {
    validDate: String(wire.digest?.valid_date ?? ""),
    assigned: (wire.digest?.assigned ?? []).map(projectAssigned),
    unassignedCandidates: projectForgotList(wire.digest?.unassigned_candidates),
    staleAssigned: projectForgotList(wire.digest?.stale_assigned),
    droppedToday: projectDroppedToday(wire.dropped_today),
    anchored: (wire.anchored_blocks ?? []).map(projectAnchored),
    anchoredSourceFingerprint: String(wire.anchored_source_fingerprint ?? ""),
    habitsNote,
    time: {
      now: String(wire.time?.now ?? ""),
      anchor: String(wire.time?.anchor ?? ""),
      effectiveEod: String(wire.time?.effective_eod ?? ""),
      eodNote: wire.time?.eod_note ?? null,
      configEod: String(wire.time?.config_eod ?? ""),
      totalBlocks: Number(wire.time?.total_blocks ?? 0),
    },
    capacity: projectCapacity(wire.capacity ?? {}),
    daySetup: projectDaySetup(
      wire.day_setup ?? {},
      wire.day_setup_confirmed === true,
    ),
    daySemantics: projectDaySemantics(wire.day_semantics ?? {}),
    planningConfigFingerprint: String(wire.planning_config_fingerprint ?? ""),
    sourceWarnings: warnings,
    sourceCounts: {
      vault: Number(wire.source_counts?.vault ?? 0),
      todoist: Number(wire.source_counts?.todoist ?? 0),
      calendar: Number(wire.source_counts?.calendar ?? 0),
    },
    sourceHealth: sourceHealthOf(warnings),
    microAdventure: projectMicroAdventure(wire.micro_adventure),
  };
}

/** Fixed-input snapshot from a raw /plan-inputs payload (fingerprint source).
    Same normalization as the fixture's fixedInputsOf: calendar commitments +
    effective anchored blocks. */
export function projectFixedInputs(wire: Wire): FixedInputs {
  const anchored = (wire.anchored_blocks ?? []).map(projectAnchored);
  return {
    anchoredSourceFingerprint: String(wire.anchored_source_fingerprint ?? ""),
    planningConfigFingerprint: String(wire.planning_config_fingerprint ?? ""),
    calendar: anchored
      .filter(
        (a: AnchoredBlock) =>
          a.kind === "calendar" && a.capacityClass !== "quarantined",
      )
      .map((a: AnchoredBlock) => ({
        name: a.name,
        start: a.start,
        durationMin: a.durationMin,
        // T28: dismissal is a fixed-input change — freed interval must
        // invalidate a staged plan built with the row present.
        attending: a.skipToday !== true,
      })),
    anchored: anchored
      .filter((a: AnchoredBlock) => a.kind !== "calendar")
      .map((a: AnchoredBlock) => ({
        name: a.name,
        start: a.start,
        durationMin: a.durationMin,
        on: a.on,
        skipToday: a.skipToday,
      })),
  };
}

export function projectSequenceRow(row: Wire): SequenceRow {
  return {
    id: String(row.id),
    start: String(row.start),
    end: String(row.end),
    zone: row.zone ?? null,
    kind: row.backdrop === true ? "zone" : "work",
    wire: structuredClone(row),
  };
}

export function projectOverlapGrant(row: Wire): OverlapGrant {
  return {
    primaryId: String(row.primary_id),
    companionId: String(row.companion_id),
    primaryInterval: {
      start: String(row.primary_interval?.start ?? ""),
      end: String(row.primary_interval?.end ?? ""),
    },
    companionInterval: {
      start: String(row.companion_interval?.start ?? ""),
      end: String(row.companion_interval?.end ?? ""),
    },
    reason: String(row.reason ?? ""),
    planningConfigFingerprint: String(row.planning_config_fingerprint ?? ""),
  };
}

/** Server soft warnings arrive as dicts ({id, rule|kind, detail}) from
    sequence.validate_sequence — project the human `detail` string verbatim
    (locked decision 24 renders accepted defects verbatim). Plain strings
    (fixture adapter, block notes) pass through untouched. */
function warningText(w: unknown): string {
  if (typeof w === "string") return w;
  if (w && typeof w === "object" && "detail" in w) {
    return String((w as { detail: unknown }).detail);
  }
  return String(w);
}

export function projectSequenceResult(wire: Wire): {
  sequence: SequenceRow[];
  warnings: string[];
  overlapGrants: OverlapGrant[];
  pinnedRows?: SequenceRow[];
} {
  return {
    sequence: (wire.sequence ?? []).map(projectSequenceRow),
    warnings: (wire.warnings ?? []).map(warningText),
    overlapGrants: (wire.overlap_grants ?? []).map(projectOverlapGrant),
    // T27: the server returns the EFFECTIVE pin set (client + recurring
    // auto-pins); adopting it verbatim keeps later /validate-sequence and
    // /commit snapshot comparisons byte-exact.
    ...(Array.isArray(wire.pinned_rows)
      ? { pinnedRows: (wire.pinned_rows as Wire[]).map(projectSequenceRow) }
      : {}),
  };
}

export function projectValidation(wire: Wire): Validation {
  return {
    ok: wire.ok === true,
    hardErrors: (wire.hard_errors ?? []).map(String),
    warnings: (wire.warnings ?? []).map(warningText),
  };
}

const SHADOW_CLASSIFICATIONS: ShadowClassification[] = [
  "would-create",
  "would-update",
  "no-op",
  "conflict",
  "unavailable",
];

export function projectShadow(wire: Wire): ShadowDiff {
  const entries: ShadowEntry[] = (wire.entries ?? []).map((e: Wire) => {
    const m = e.manifest ?? {};
    return {
      step: String(m.step ?? ""),
      system: m.system,
      action: String(m.action ?? ""),
      name: String(m.name ?? ""),
      idOrPath: String(m.id_or_path ?? ""),
      time: m.time ?? null,
      durationMin: Number(m.duration_min ?? 0),
      classification: SHADOW_CLASSIFICATIONS.includes(e.classification)
        ? e.classification
        : "conflict",
      detail: e.detail ?? {},
    };
  });
  const counts = Object.fromEntries(
    SHADOW_CLASSIFICATIONS.map((c) => [c, Number(wire.counts?.[c] ?? 0)]),
  ) as ShadowDiff["counts"];
  return {
    entries,
    unavailableSurfaces: (wire.unavailable_surfaces ?? []).map(String),
    counts,
  };
}

/** T20: journal entry -> UI projection. Server-authoritative; only the
    fields the banners/undo chip need. */
export function projectRuntimeAction(wire: Wire): import("../model/types").RuntimeAction {
  return {
    id: String(wire.id ?? ""),
    verb: String(wire.verb ?? ""),
    targetName: String((wire.target as Wire)?.name ?? wire.target ?? ""),
    status: (wire.status ?? "pending") as import("../model/types").RuntimeAction["status"],
    error: wire.error == null ? null : String(wire.error),
    duplicate: wire.duplicate === true,
  };
}

export function projectCommitReport(wire: Wire): CommitReport {
  const surfaces: CommitSurface[] = Object.entries(wire.surfaces ?? {}).map(
    ([system, entry]: [string, any]) => ({
      system,
      status: entry?.status === "ok" ? "ok" : entry?.status === "skipped" ? "skipped" : "failed",
      detail: entry?.error ?? entry?.note ?? null,
    }),
  );
  const anyOk = surfaces.some((s) => s.status === "ok");
  const anyFailed = surfaces.some((s) => s.status === "failed");
  return {
    status: wire.ok === true ? "ok" : anyOk && anyFailed ? "partial" : "failed",
    surfaces,
    verifyFailures: (wire.verify_failures ?? []).map(String),
    // FEEDBACK-23: machine-canonical structured detail (24h HH:MM, raw ISO,
    // IANA timezone) travels separate from the 12h display strings — the
    // drawer formats display from these values.
    ...(Array.isArray(wire.verify_details)
      ? {
          verifyDetails: (wire.verify_details as Wire[]).map((d) => ({
            kind: d.kind === "due" ? "due" : "plain",
            name: String(d.name ?? ""),
            intent: d.intent == null ? null : String(d.intent),
            live: d.live == null ? null : String(d.live),
            liveRaw: d.live_raw == null ? null : String(d.live_raw),
            liveTimezone: d.live_timezone == null ? null : String(d.live_timezone),
            reason: String(d.reason ?? ""),
            message: String(d.message ?? ""),
          })),
        }
      : {}),
  };
}

// -- model → wire body builders ----------------------------------------------

export function daySetupToWire(d: DaySetup): Wire {
  const wire: Wire = {
    anchor: d.anchor,
    eod: d.eod,
    buffering: d.buffering,
    anchored: Object.entries(d.anchored).map(([id, o]) => ({
      id,
      on: o.on,
      skip_today: o.skipToday,
      time: o.time,
      ...(o.blocks == null ? {} : { blocks: o.blocks }),
    })),
    captures: {
      intention: d.captures.intention,
      megan_nicety: d.captures.forMeegy,
      stoic_intention: d.captures.stoic,
    },
  };
  if (Object.prototype.hasOwnProperty.call(d, "dayPreset")) wire.day_preset = d.dayPreset;
  if (Object.prototype.hasOwnProperty.call(d, "workAllotmentMinutes")) {
    wire.work_allotment_minutes = d.workAllotmentMinutes;
  }
  if (Object.prototype.hasOwnProperty.call(d, "schedulable")) {
    wire.schedulable = d.schedulable;
  }
  return wire;
}

export function rowToWire(r: SequenceRow): Wire {
  return {
    ...(r.wire ?? {}),
    id: r.id,
    start: r.start,
    end: r.end,
    zone: r.zone,
    ...(r.kind === "zone" ? { backdrop: true } : {}),
  };
}

export function grantToWire(g: OverlapGrant): Wire {
  return {
    primary_id: g.primaryId,
    companion_id: g.companionId,
    primary_interval: g.primaryInterval,
    companion_interval: g.companionInterval,
    reason: g.reason,
    planning_config_fingerprint: g.planningConfigFingerprint,
  };
}

/** Today-only shaping applied to the RAW digest rows for POST bodies:
    excluded rows drop, duration overrides replace `blocks`. Rows keep their
    wire shape verbatim otherwise (id = name, the T1 contract) — assignment
    truth is never touched (locked decision 16). */
export function shapeAssignedWire(
  rawAssigned: Wire[],
  included: Array<{ id: string; blocks: number }>,
): Wire[] {
  const byId = new Map(included.map((i) => [i.id, i.blocks]));
  return rawAssigned
    .filter((row) => byId.has(String(row.name)))
    .map((row) => ({ ...row, id: String(row.name), blocks: byId.get(String(row.name)) }));
}
