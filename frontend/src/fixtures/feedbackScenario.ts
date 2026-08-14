/* feedbackScenario.ts — FEEDBACK-05 (2026-08-14): the complete reported
   calendar-scenario fixture.

   Represents the live screenshot day: Cooking at 20:30, Foods Dinner, DCP
   Bark Bar trivia, Steelers vs Packers, assigned rows overlapping those
   blocks, and an over-assignment shape with only 2 available blocks versus
   16 included blocks. Deterministic, no clocks, no randomness — every value
   is pinned so the backend and frontend regression suites share ONE story.

   Classification mirrors the server contract (frozen contract 17 + T12j):
   - Cooking            -> capacityClass "fixed"       (hard wall)
   - DCP Bark Bar trivia-> capacityClass "work"        (hard wall)
   - Steelers vs Packers-> capacityClass "quarantined" (excluded: no wall,
                                                         no capacity)
   - Foods Dinner       -> config window, NOT a calendar event (permeable)
*/

import type {
  AnchoredBlock,
  AssignedItem,
  Capacity,
  PlanInputs,
  TimeFrame,
} from "../model/types";

/** The reported day frame: late anchor (17:15 — the FEEDBACK-01 shape) with
    the evening commitments in play. totalBlocks 11 mirrors the backend frame
    (17:15 -> 23:00 = 11 × 30-min blocks). */
export const FEEDBACK_TIME: TimeFrame = {
  now: "17:10",
  anchor: "17:15",
  effectiveEod: "23:00",
  eodNote: "Real stop 11 PM — Night Routine",
  configEod: "23:45",
  totalBlocks: 11,
};

/** Anchored blocks: config hard/window blocks plus the reported calendar
    events with their explicit capacity classes. */
export const FEEDBACK_ANCHORED: AnchoredBlock[] = [
  { id: "Suds", name: "Suds", kind: "hard", start: "17:45", end: "18:15", durationMin: 30, overlapAllowed: false, on: true, skipToday: false },
  { id: "Foods Dinner", name: "Foods Dinner", kind: "window", start: "18:00", end: "20:30", durationMin: 60, overlapAllowed: false, on: true, skipToday: false },
  { id: "DCP Bark Bar trivia", name: "DCP Bark Bar trivia", kind: "calendar", start: "19:00", end: "20:00", durationMin: 60, overlapAllowed: false, on: true, skipToday: false, calendarId: "cal-trivia", calendarTitle: "Trivia", capacityClass: "work" },
  { id: "Steelers vs Packers", name: "Steelers vs Packers", kind: "calendar", start: "20:00", end: "22:00", durationMin: 120, overlapAllowed: false, on: true, skipToday: false, calendarId: "cal-sports", calendarTitle: "Sports", capacityClass: "quarantined" },
  { id: "Cooking", name: "Cooking", kind: "calendar", start: "20:30", end: "21:00", durationMin: 30, overlapAllowed: false, on: true, skipToday: false, calendarId: "cal-personal", calendarTitle: "Personal", capacityClass: "fixed" },
  { id: "Night Routine", name: "Night Routine", kind: "hard", start: "23:00", end: "23:45", durationMin: 45, overlapAllowed: false, on: true, skipToday: false },
];

/** Assigned rows from the reported scenario — 16 blocks of included work.
    Magic Mirror is the row the screenshot showed overlapping Cooking. */
export const FEEDBACK_ASSIGNED: AssignedItem[] = [
  { id: "Magic Mirror", name: "Magic Mirror", path: "50 - Operations/Projects/Magic Mirror.md", source: "vault", types: ["project"], urgency: "3-high", deadline: "2026-07-19", priorityScore: 86, blocks: 3, durationLabel: "1hr 30min", todoistId: null },
  { id: "Log hours", name: "Log hours", path: null, source: "todoist", types: ["task"], urgency: null, deadline: "2026-07-14", priorityScore: 20, blocks: 1, durationLabel: "30min", todoistId: "fb001LOG" },
  { id: "Note Processing", name: "Note Processing", path: "50 - Operations/Pursuits/Note Processing.md", source: "vault", types: ["interval"], urgency: null, deadline: null, priorityScore: 41, blocks: 1, durationLabel: "30min", todoistId: null },
  { id: "Pick up prescription", name: "Pick up prescription", path: null, source: "todoist", types: ["task"], urgency: null, deadline: "2026-07-14", priorityScore: 50, blocks: 1, durationLabel: "30min", todoistId: "fb002RX" },
  { id: "Press", name: "Press", path: "50 - Operations/Pursuits/Press.md", source: "vault", types: ["interval"], urgency: null, deadline: null, priorityScore: 64, blocks: 3, durationLabel: "1hr 15min", todoistId: null },
  { id: "Rowe's T-shirt Redesign 2026", name: "Rowe's T-shirt Redesign 2026", path: "50 - Operations/Projects/Rowe's T-shirt Redesign 2026.md", source: "vault", types: ["project"], urgency: "4-crit", deadline: "2026-07-31", priorityScore: 91, blocks: 2, durationLabel: "1hr", todoistId: null },
  { id: "Review AWS module 4", name: "Review AWS module 4", path: null, source: "todoist", types: ["task"], urgency: null, deadline: "2026-07-14", priorityScore: 55, blocks: 2, durationLabel: "1hr", todoistId: "fb003AWS" },
  { id: "Entryway Design", name: "Entryway Design", path: "50 - Operations/Projects/Entryway Design.md", source: "vault", types: ["project"], urgency: "2-med", deadline: null, priorityScore: 38, blocks: 1, durationLabel: "30min", todoistId: null },
  { id: "Deep CWEAN", name: "Deep CWEAN", path: "50 - Operations/Pursuits/Deep CWEAN.md", source: "vault", types: ["interval"], urgency: null, deadline: null, priorityScore: 47, blocks: 2, durationLabel: "1hr", todoistId: null },
];

/** Server-verbatim capacity for the reported shape: 2 available blocks,
    selected 16, free −14 → overassigned with an explicit ⚠ readout. */
export function feedbackCapacity(): Capacity {
  return {
    total: 11,
    fixed: 1, // Cooking only — Steelers quarantined never counts
    anchored: 5, // Suds 1 + Foods Dinner 2 + Night Routine 2 (45m → 2 blk)
    habits: 1,
    mint: 2, // max(allotment 60m, work_busy trivia 60m)
    selected: 16,
    buffer: 0,
    free: -14,
    overassigned: true,
    availableForSelection: 2,
    remaining: "⚠ 7hr over · 14 blk",
    ratio: "25 / 11 blk",
    legend: "Fixed 1 · Anchored 5 · Habits 1 · Mint 2 · Selected 16 · Buffer 0 · Free -14 · Total 11",
    counters: "deep: 1 / 4 · mixed: 2 / 3",
    workBusy: 2,
    workOverflow: 0,
  };
}

/** Full PlanInputs for the reported scenario — the frontend fixture the
    FEEDBACK-05 suite drives end to end. */
export function feedbackInputs(): PlanInputs {
  return {
    validDate: "2026-07-14",
    assigned: FEEDBACK_ASSIGNED.map((a) => ({ ...a })),
    unassignedCandidates: [],
    staleAssigned: [],
    droppedToday: [],
    anchored: FEEDBACK_ANCHORED.map((a) => ({ ...a })),
    anchoredSourceFingerprint: "feedback-anchored-v1",
    habitsNote: "1 habit outstanding · ~30min",
    time: { ...FEEDBACK_TIME },
    capacity: feedbackCapacity(),
    daySetup: {
      anchor: "17:15",
      eod: "23:00",
      buffering: "off",
      anchored: {},
      captures: { intention: "Ship the reported day cleanly", forMeegy: "", stoic: "" },
      confirmed: true,
    },
    daySemantics: {
      availablePresets: [],
      selectedPreset: null,
      resolutionSource: "default",
      enabledZones: [],
      effectiveAllotmentMinutes: 60,
      defaultAllotmentMinutes: 60,
      mintEnabled: true,
      warnings: [],
      errors: [],
      overlapPermissionsRaw: "",
    },
    planningConfigFingerprint: "feedback-planning-v1",
    sourceWarnings: [],
    sourceCounts: { vault: 5, todoist: 4, calendar: 3 },
    sourceHealth: "ok",
    microAdventure: {
      pick: { id: "ma02", idea: "Call a friend you haven't talked to in a while", category: "social" },
      source: "auto",
      pool: [{ id: "ma02", idea: "Call a friend you haven't talked to in a while", category: "social" }],
      streak: 0,
      pendingConfirm: null,
    },
  };
}

/** Total included blocks in the assigned fixture — the 16-block demand. */
export const FEEDBACK_INCLUDED_BLOCKS = FEEDBACK_ASSIGNED.reduce(
  (sum, a) => sum + a.blocks,
  0,
);
