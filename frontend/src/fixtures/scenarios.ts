/* scenarios.ts — deterministic fixture scenarios (locked decision 12).
   Six states: fresh, ready, sequenced, conflict, commit-preview, verified.
   Data mirrors the real config shapes (config/tdtb.json anchored blocks,
   presets, palette semantics) so the approved mockup is honest about density
   and copy. No randomness, no clocks — every value is pinned. */

import type {
  AnchoredBlock,
  AssignedItem,
  Capacity,
  CommitReport,
  DaySetup,
  FixedInputs,
  Ledger,
  MicroIdea,
  PlanInputs,
  SequenceRow,
  ShadowDiff,
} from "../model/types";

export type ScenarioName =
  | "fresh"
  | "ready"
  | "sequenced"
  | "conflict"
  | "commit-preview"
  | "verified"
  | "over"
  | "sparse";

export interface Scenario {
  name: ScenarioName;
  label: string;
  inputs: PlanInputs;
  ledger: Ledger;
  /** What POST /sequence returns in this scenario (null → it fails). */
  proposal: { sequence: SequenceRow[]; warnings: string[] } | null;
  proposalError: string | null;
  shadow: ShadowDiff;
  commitReport: CommitReport;
  /** Pre-staged state applied on load (sequence already run, etc.). */
  staged: {
    daySetupConfirmed: boolean;
    sequence: SequenceRow[] | null;
    shadowCurrent: boolean;
    committed: boolean;
  };
}

const VALID_DATE = "2026-07-18";

// -- time frame --------------------------------------------------------------
// Anchor 07:30 (clock 07:22 rounded up to :15); Night Routine 23:00 pins the
// effective EOD (2h scan window before config 23:45).
const TIME = {
  now: "07:22",
  anchor: "07:30",
  effectiveEod: "23:00",
  eodNote: "Real stop 11 PM — Night Routine",
  configEod: "23:45",
  totalBlocks: 31,
};

// -- anchored blocks (config `## Anchored Lifestyle Blocks` + calendar) ------
const ANCHORED: AnchoredBlock[] = [
  { id: "Morning Routine", name: "Morning Routine", kind: "hard", start: "07:45", end: "09:05", durationMin: 80, overlapAllowed: false, on: true, skipToday: false },
  { id: "Foods Breakfast", name: "Foods Breakfast", kind: "window", start: "08:30", end: "13:00", durationMin: 45, overlapAllowed: false, on: true, skipToday: false },
  { id: "Sudsing", name: "Sudsing", kind: "hard", start: "17:45", end: "18:15", durationMin: 30, overlapAllowed: false, on: true, skipToday: false },
  { id: "Foods Dinner", name: "Foods Dinner", kind: "window", start: "18:00", end: "20:30", durationMin: 60, overlapAllowed: false, on: true, skipToday: false },
  { id: "Live", name: "Live", kind: "template", start: "12:00", end: "20:00", durationMin: 30, overlapAllowed: true, on: true, skipToday: false },
  { id: "Night Routine", name: "Night Routine", kind: "hard", start: "23:00", end: "23:45", durationMin: 45, overlapAllowed: false, on: true, skipToday: false },
  { id: "Trinoor Standup", name: "Trinoor Standup", kind: "calendar", start: "09:15", end: "09:45", durationMin: 30, overlapAllowed: false, on: true, skipToday: false, calendarId: "fixture-work", calendarTitle: "Trinoor", capacityClass: "work" },
  { id: "PHEP sync (Vlad)", name: "PHEP sync (Vlad)", kind: "calendar", start: "14:45", end: "15:30", durationMin: 45, overlapAllowed: false, on: true, skipToday: false, calendarId: "fixture-personal", calendarTitle: "Personal", capacityClass: "fixed" },
];

// -- assigned items (capacities + todoist, resolved blocks per locked decision 14)
const ASSIGNED: AssignedItem[] = [
  { id: "Magic Mirror", name: "Magic Mirror", path: null, source: "todoist", types: ["task"], urgency: "3", deadline: "2026-07-19", priorityScore: 86, blocks: 3, durationLabel: "1hr 30min", todoistId: "6fx00xMIRROR" },
  { id: "Rowe's T-shirt Redesign 2026", name: "Rowe's T-shirt Redesign 2026", path: null, source: "todoist", types: ["task"], urgency: "4", deadline: "2026-07-31", priorityScore: 91, blocks: 2, durationLabel: "1hr", todoistId: "6fx00xROWE" },
  { id: "Press", name: "Press", path: null, source: "todoist", types: ["task"], urgency: null, deadline: null, priorityScore: 64, blocks: 3, durationLabel: "1hr 15min", todoistId: "6fx00xPRESS" },
  { id: "Note Processing", name: "Note Processing", path: null, source: "capacities", types: ["interval"], urgency: null, deadline: null, priorityScore: 41, blocks: 1, durationLabel: "30min", todoistId: null, identity: "capacities:fixture-space:custom-project:note-processing", capacitiesIdentity: "capacities:fixture-space:custom-project:note-processing", capacitiesStructureId: "custom-project" },
  { id: "Entryway Design", name: "Entryway Design", path: null, source: "todoist", types: ["task"], urgency: "2", deadline: null, priorityScore: 38, blocks: 1, durationLabel: "30min", todoistId: "6fx00xENTRYWAY" },
  { id: "Review AWS module 4", name: "Review AWS module 4", path: null, source: "todoist", types: ["task"], urgency: null, deadline: "2026-07-18", priorityScore: 55, blocks: 2, durationLabel: "1hr", todoistId: "6fx001AWS" },
  { id: "Pick up prescription", name: "Pick up prescription", path: null, source: "todoist", types: ["task"], urgency: null, deadline: "2026-07-18", priorityScore: 50, blocks: 1, durationLabel: "30min", todoistId: "6fx002RX" },
  { id: "Charge GoPro", name: "Charge GoPro", path: null, source: "todoist", types: ["task"], urgency: null, deadline: null, priorityScore: 12, blocks: 0, durationLabel: "—", todoistId: "6fx003GOPRO" },
];

// -- capacity (server-verbatim shapes, numbers consistent with capacity.py) --
function cap(selected: number, spentNote?: string): Capacity {
  const total = 31;
  const fixed = 3; // Standup 1 + PHEP 2 (ceil 45m)
  const anchored = 8; // MR 3 + Sudsing 1 + Breakfast 2 + Dinner 2 (NR at eod)
  const habits = 1;
  const buffer = 4; // ceil((31-3-8-1) * 0.2)
  const free = total - fixed - anchored - habits - buffer - selected;
  const hrs = (b: number) => {
    const m = Math.abs(b) * 30;
    if (m < 60) return `${m}min`;
    return m % 60 === 0 ? `${m / 60}hr` : `${Math.floor(m / 60)}hr ${m % 60}min`;
  };
  const remaining =
    free > 0
      ? `⬆ ${hrs(free)} left · ${free} blk`
      : free === 0
        ? "⬆ fully booked · 0 blk left"
        : `⚠ ${hrs(free)} over · ${-free} blk`;
  let legend = `Fixed ${fixed} · Anchored ${anchored} · Habits ${habits} · Selected ${selected} · Buffer ${buffer} · Free ${free} · Total ${total}`;
  if (spentNote) legend += ` (${spentNote})`;
  return {
    total, fixed, anchored, habits, mint: 0, selected, buffer, free,
    overassigned: free < 0,
    availableForSelection: Math.max(0, total - fixed - anchored - habits - buffer),
    remaining,
    ratio: `${total - free} / ${total} blk`,
    legend,
    counters: "deep: 1 / 4 · mixed: 2 / 3",
  };
}

const emptySetup: DaySetup = {
  anchor: null,
  eod: null,
  buffering: "standard",
  anchored: {},
  captures: { intention: "", forMeegy: "", stoic: "" },
  confirmed: false,
};

const confirmedSetup: DaySetup = {
  anchor: "07:30",
  eod: null,
  buffering: "standard",
  anchored: {},
  captures: {
    intention: "Ship the cockpit mockup for review",
    forMeegy: "Text her about Saturday's hike start time",
    stoic: "The obstacle is the way — friction today is signal, not failure",
  },
  confirmed: true,
};

const MICRO_POOL: MicroIdea[] = [
  { id: "ma07", idea: "Watch sunset", category: "nature" },
  { id: "ma02", idea: "Call a friend you haven't talked to in a while", category: "social" },
  { id: "ma12", idea: "Practice one new skill move for 15 minutes", category: "growth" },
  { id: "ma06", idea: "Sit outside, no phone, for 15 minutes", category: "stillness" },
];

function inputs(overrides: Partial<PlanInputs> = {}): PlanInputs {
  return {
    validDate: VALID_DATE,
    assigned: ASSIGNED.map((a) => ({ ...a })),
    unassignedCandidates: [],
    staleAssigned: [],
    droppedToday: [],
    anchored: ANCHORED.map((a) => ({ ...a })),
    anchoredSourceFingerprint: "fixture-anchored-v1",
    habitsNote: "2 habits outstanding · ~30min",
    time: { ...TIME },
    capacity: cap(13),
    daySetup: { ...emptySetup },
    daySemantics: {
      availablePresets: [], selectedPreset: null, resolutionSource: "default",
      enabledZones: [], effectiveAllotmentMinutes: 0, defaultAllotmentMinutes: 0,
      mintEnabled: false, warnings: [], errors: [], overlapPermissionsRaw: "",
    },
    planningConfigFingerprint: "fixture-planning-v1",
    sourceWarnings: [],
    sourceCounts: { todoist: 3, calendar: 2 },
    sourceHealth: "ok",
    microAdventure: {
      pick: { ...MICRO_POOL[0] },
      source: "auto",
      pool: MICRO_POOL.map((p) => ({ ...p })),
      streak: 3,
      pendingConfirm: null,
    },
    ...overrides,
  };
}

// -- clean sequence proposal -------------------------------------------------
const ZONE_ROWS: SequenceRow[] = [
  { id: "Trinoor", start: "08:30", end: "17:00", zone: "Trinoor", kind: "zone" },
];

const CLEAN_SEQUENCE: SequenceRow[] = [
  { id: "Rowe's T-shirt Redesign 2026", start: "09:45", end: "10:45", zone: null, kind: "work" },
  { id: "Magic Mirror", start: "10:45", end: "12:15", zone: null, kind: "work" },
  { id: "Review AWS module 4", start: "12:30", end: "13:30", zone: null, kind: "work" },
  { id: "Note Processing", start: "13:30", end: "14:00", zone: null, kind: "work" },
  { id: "Pick up prescription", start: "16:00", end: "16:30", zone: null, kind: "work" },
  { id: "Entryway Design", start: "16:30", end: "17:00", zone: null, kind: "work" },
  { id: "Press", start: "19:00", end: "20:15", zone: null, kind: "work" },
  ...ZONE_ROWS,
];

const CLEAN_WARNINGS = ["Press placed at 7:00 PM — at its latest-start limit"];

// -- shadow diff (commit-preview) -------------------------------------------
const SHADOW: ShadowDiff = {
  entries: [
    { step: "A", system: "todoist", action: "schedule", name: "Pick up prescription", idOrPath: "todoist:8899001122", time: "16:00", durationMin: 30, classification: "would-update", detail: { from: "today (no time)", to: "16:00" } },
    { step: "A", system: "todoist", action: "schedule", name: "Review AWS module 4", idOrPath: "todoist:8899001123", time: "12:30", durationMin: 60, classification: "would-update", detail: { from: "today (no time)", to: "12:30" } },
    { step: "B", system: "calendar", action: "create-event", name: "⬜ Magic Mirror", idOrPath: "BusyCal:Blocks", time: "10:45", durationMin: 90, classification: "would-create", detail: {} },
    { step: "B", system: "calendar", action: "create-event", name: "⬜ Rowe's T-shirt Redesign 2026", idOrPath: "BusyCal:Blocks", time: "09:45", durationMin: 60, classification: "would-create", detail: {} },
    { step: "B", system: "calendar", action: "create-event", name: "⬜ Press", idOrPath: "BusyCal:Blocks", time: "19:00", durationMin: 75, classification: "would-create", detail: {} },
  ],
  unavailableSurfaces: [],
  counts: { "would-create": 3, "would-update": 2, "no-op": 0, conflict: 0, unavailable: 0 },
  notes: [],
};

const REPORT_OK: CommitReport = {
  status: "ok",
  surfaces: [
    { system: "todoist", status: "ok", detail: "2 scheduled" },
    { system: "calendar", status: "ok", detail: "3 events created" },
  ],
  verifyFailures: [],
};

// -- fixed inputs (fingerprint source) ---------------------------------------
export function fixedInputsOf(p: PlanInputs): FixedInputs {
  return {
    anchoredSourceFingerprint: p.anchoredSourceFingerprint,
    planningConfigFingerprint: p.planningConfigFingerprint,
    // FEEDBACK-04: quarantined rows are excluded from planning (contract 17) —
    // they never enter the fixed-input fingerprint, mirroring wire.ts.
    calendar: p.anchored
      .filter((a) => a.kind === "calendar" && a.capacityClass !== "quarantined")
      .map((a) => ({ name: a.name, start: a.start, durationMin: a.durationMin })),
    anchored: p.anchored
      .filter((a) => a.kind !== "calendar")
      .map((a) => ({ name: a.name, start: a.start, durationMin: a.durationMin, on: a.on, skipToday: a.skipToday })),
  };
}

// -- the six scenarios -------------------------------------------------------

function base(name: ScenarioName, label: string): Scenario {
  return {
    name,
    label,
    inputs: inputs(),
    ledger: { today: VALID_DATE, spent: 0, cap: 4, remaining: 4 },
    proposal: { sequence: CLEAN_SEQUENCE.map((r) => ({ ...r })), warnings: [...CLEAN_WARNINGS] },
    proposalError: null,
    shadow: SHADOW,
    commitReport: REPORT_OK,
    staged: { daySetupConfirmed: false, sequence: null, shadowCurrent: false, committed: false },
  };
}

export function makeScenario(name: ScenarioName): Scenario {
  switch (name) {
    case "fresh":
      return base("fresh", "Fresh day — setup pending");
    case "ready": {
      const s = base("ready", "Setup confirmed — ready to sequence");
      s.inputs.daySetup = { ...confirmedSetup };
      s.staged.daySetupConfirmed = true;
      return s;
    }
    case "sequenced": {
      const s = base("sequenced", "Clean sequence staged");
      s.inputs.daySetup = { ...confirmedSetup };
      s.staged.daySetupConfirmed = true;
      s.staged.sequence = CLEAN_SEQUENCE.map((r) => ({ ...r }));
      s.ledger = { today: VALID_DATE, spent: 1, cap: 4, remaining: 3 };
      return s;
    }
    case "conflict": {
      const s = base("conflict", "Overassigned + hard errors");
      s.inputs.daySetup = { ...confirmedSetup };
      s.staged.daySetupConfirmed = true;
      // Extra load: Deep CWEAN 4 blocks + longer AWS review pushes free negative.
      s.inputs.assigned.push({
        id: "Deep CWEAN", name: "Deep CWEAN", path: null,
        source: "capacities", types: ["interval"], urgency: null, deadline: null,
        priorityScore: 47, blocks: 4, durationLabel: "2hr", todoistId: null,
        identity: "capacities:fixture-space:custom-project:deep-cwean",
        capacitiesIdentity: "capacities:fixture-space:custom-project:deep-cwean",
        capacitiesStructureId: "custom-project",
      });
      s.inputs.capacity = cap(18);
      s.inputs.sourceWarnings = [
        "Todoist read failed (timeout) — task list may be incomplete",
      ];
      s.inputs.sourceHealth = "degraded";
      s.ledger = { today: VALID_DATE, spent: 3, cap: 4, remaining: 1 };
      // A staged manual layout with real hard errors to fix:
      s.staged.sequence = [
        { id: "Press", start: "17:30", end: "18:45", zone: null, kind: "work" },
        { id: "Magic Mirror", start: "10:45", end: "12:15", zone: null, kind: "work" },
        ...ZONE_ROWS,
      ];
      s.proposalError =
        "sequence validation failed: Press 17:30–18:45 overlaps Sudsing 17:45–18:15";
      s.proposal = null;
      return s;
    }
    case "commit-preview": {
      const s = base("commit-preview", "Shadow preview — approval pending");
      s.inputs.daySetup = { ...confirmedSetup };
      s.staged.daySetupConfirmed = true;
      s.staged.sequence = CLEAN_SEQUENCE.map((r) => ({ ...r }));
      s.staged.shadowCurrent = true;
      s.ledger = { today: VALID_DATE, spent: 1, cap: 4, remaining: 3 };
      return s;
    }
    case "verified": {
      const s = base("verified", "Committed — D1 verify clean");
      s.inputs.daySetup = { ...confirmedSetup };
      s.staged.daySetupConfirmed = true;
      s.staged.sequence = CLEAN_SEQUENCE.map((r) => ({ ...r }));
      s.staged.shadowCurrent = true;
      s.staged.committed = true;
      s.ledger = { today: VALID_DATE, spent: 2, cap: 4, remaining: 2 };
      return s;
    }
    case "over": {
      // U9: an over-capacity day — the fixed commitments (calendar, work,
      // anchors, habits) already exceed the frame, and the chosen tasks pile
      // on top. The capacity card must read over (red headline, hatched
      // overflow tail, marker inside the bar) rather than rescaling.
      const s = base("over", "Over capacity — fixed time exceeds the day");
      s.inputs.daySetup = { ...confirmedSetup };
      s.staged.daySetupConfirmed = true;
      s.inputs.capacity = {
        ...cap(13),
        total: 10,
        fixed: 8,
        anchored: 5,
        habits: 1,
        buffer: 0,
        free: -17,
        overassigned: true,
        availableForSelection: 0,
        remaining: "⚠ 8hr 30min over · 17 blk",
        ratio: "27 / 10 blk",
      };
      return s;
    }
    case "sparse": {
      // KTD13: reproduce the live state Adam saw — a stale artifact, legacy
      // sources off, no assigned rows, degraded sources, and the legacy Capacities
      // intake (no direct snapshot block). The fixture adapter must not
      // inject its synthetic direct intake here (see applyCapacitiesIntake).
      const s = base("sparse", "Sparse day — stale artifact, degraded sources");
      s.inputs.assigned = [];
      s.inputs.staleAssigned = [
        {
          name: "Old quarterly review",
          path: "50 - Operations/Projects/Old quarterly review.md",
          reason: "no activity in 21 days",
        },
        {
          name: "Draft launch brief",
          path: "50 - Operations/Pursuits/Draft launch brief.md",
          reason: "deadline has passed",
        },
      ];
      s.inputs.sourceHealth = "degraded";
      s.inputs.sourceWarnings = [
        "Todoist read failed (timeout) — task list may be incomplete",
        "Capacities refresh required — no direct snapshot is installed",
      ];
      s.inputs.sourceCounts = { todoist: 0, calendar: 0 };
      s.inputs.capacity = cap(0);
      return s;
    }
  }
}

export const SCENARIO_NAMES: ScenarioName[] = [
  "fresh",
  "ready",
  "sequenced",
  "conflict",
  "commit-preview",
  "verified",
  "over",
  "sparse",
];
