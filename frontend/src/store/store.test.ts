import { describe, expect, it } from "vitest";
import {
  alerts,
  canAutoSequence,
  effectiveAnchoredBlocks,
  canLiveCommit,
  canShadow,
  dockState,
  initialState,
  queueState,
  reducer,
  sourceHealthBlocked,
  type AppState,
} from "./store";
import { makeScenario, fixedInputsOf } from "../fixtures/scenarios";
import { fingerprintFixedInputs } from "../model/fingerprint";
import { calendarWalls } from "../model/overflow";
import type { Ledger, SequenceRow } from "../model/types";

const ledger: Ledger = { today: "2026-07-18", spent: 0, cap: 4, remaining: 4 };

function loaded(scenario = "ready" as const): AppState {
  const sc = makeScenario(scenario);
  return reducer(initialState, {
    type: "INPUTS_LOADED",
    inputs: sc.inputs,
    ledger: { ...sc.ledger },
  });
}

function sequenced(): AppState {
  const sc = makeScenario("ready");
  let s = loaded();
  s = reducer(s, { type: "SETUP_SAVED", daySetup: { ...sc.inputs.daySetup, confirmed: true } });
  s = reducer(s, {
    type: "SEQUENCE_OK",
    sequence: sc.proposal!.sequence,
    warnings: sc.proposal!.warnings,
    fingerprint: fingerprintFixedInputs(fixedInputsOf(sc.inputs)),
    anchoredSourceFingerprint: sc.inputs.anchoredSourceFingerprint,
    ledger: { ...ledger, spent: 1, remaining: 3 },
  });
  // The ready proposal carries a designed latest-start warning — an
  // acceptable defect (LD 24). Accept it so downstream gates open; the
  // acceptance lifecycle itself is pinned in accept.test.ts.
  s = reducer(s, { type: "ACCEPT_DEFECTS" });
  return s;
}

function shadowed(): AppState {
  const sc = makeScenario("commit-preview");
  let s = sequenced();
  s = reducer(s, { type: "SHADOW_START" });
  s = reducer(s, { type: "SHADOW_OK", shadow: sc.shadow });
  return s;
}

describe("date rollover", () => {
  it("a valid_date change wipes staged sequence, shadow, and overrides", () => {
    let s = shadowed();
    s = reducer(s, {
      type: "OVERRIDE_SET",
      id: "Press",
      override: { included: true, blocks: 2 },
    });
    const rolled = makeScenario("fresh");
    rolled.inputs.validDate = "2026-07-19";
    s = reducer(s, { type: "INPUTS_LOADED", inputs: rolled.inputs, ledger });
    expect(s.sequence).toBeNull();
    expect(s.shadow).toBeNull();
    expect(s.shadowPhase).toBe("none");
    expect(s.overrides).toEqual({});
    expect(s.fingerprint).toBeNull();
    expect(s.validDate).toBe("2026-07-19");
  });

  it("same-date reload keeps staged state", () => {
    let s = sequenced();
    const again = makeScenario("ready");
    s = reducer(s, { type: "INPUTS_LOADED", inputs: again.inputs, ledger });
    expect(s.sequence).not.toBeNull();
    expect(s.seqPhase).toBe("valid");
  });
});

describe("stale shadow on edit", () => {
  it("an override edit after a current shadow marks it stale and disarms live", () => {
    let s = shadowed();
    s = reducer(s, { type: "ARM_LIVE" });
    expect(s.liveArmed).toBe(true);
    s = reducer(s, {
      type: "OVERRIDE_SET",
      id: "Note Processing",
      override: { included: true, blocks: 2 },
    });
    expect(s.shadowPhase).toBe("stale");
    expect(s.liveArmed).toBe(false);
    expect(canLiveCommit(s)).toBe(false);
  });

  it("a row move after a current shadow marks it stale", () => {
    let s = shadowed();
    s = reducer(s, { type: "ROW_MOVED", id: "Magic Mirror", start: "11:00" });
    expect(s.shadowPhase).toBe("stale");
    expect(s.seqPhase).toBe("dirty");
  });

  it("a day-setup save after a current shadow marks it stale", () => {
    let s = shadowed();
    s = reducer(s, { type: "SETUP_SAVED", daySetup: s.daySetup });
    expect(s.shadowPhase).toBe("stale");
  });
});

describe("today-only overrides", () => {
  it("excluding an item drops its staged work row and dirties the plan", () => {
    let s = sequenced();
    expect(s.sequence!.some((r) => r.id === "Press" && r.kind === "work")).toBe(true);
    s = reducer(s, {
      type: "OVERRIDE_SET",
      id: "Press",
      override: { included: false, blocks: null },
    });
    expect(s.sequence!.some((r) => r.id === "Press")).toBe(false);
    expect(s.seqPhase).toBe("dirty");
    expect(queueState(s, "Press")).toBe("excluded");
  });

  it("never mutates the upstream assigned flag — inputs.assigned is untouched", () => {
    let s = loaded();
    const before = JSON.stringify(s.inputs!.assigned);
    s = reducer(s, {
      type: "OVERRIDE_SET",
      id: "Press",
      override: { included: false, blocks: null },
    });
    s = reducer(s, {
      type: "OVERRIDE_SET",
      id: "Magic Mirror",
      override: { included: true, blocks: 1 },
    });
    expect(JSON.stringify(s.inputs!.assigned)).toBe(before);
  });

  it("a duration override resizes the staged work row (sequence payload carries it)", () => {
    let s = sequenced();
    const before = s.sequence!.find((r) => r.id === "Press" && r.kind === "work")!;
    s = reducer(s, {
      type: "OVERRIDE_SET",
      id: "Press",
      override: { included: true, blocks: 4 },
    });
    const row = s.sequence!.find((r) => r.id === "Press" && r.kind === "work")!;
    expect(row.start).toBe(before.start);
    expect(row.end).not.toBe(before.end);
    const dur =
      ((Number(row.end.slice(0, 2)) * 60 + Number(row.end.slice(3))) -
        (Number(row.start.slice(0, 2)) * 60 + Number(row.start.slice(3))) + 1440) % 1440;
    expect(dur).toBe(4 * 30);
    expect(s.seqPhase).toBe("dirty");
  });

  it("an include-only override (blocks null) leaves the staged row untouched", () => {
    let s = sequenced();
    const before = s.sequence!.find((r) => r.id === "Press" && r.kind === "work")!;
    s = reducer(s, {
      type: "OVERRIDE_SET",
      id: "Press",
      override: { included: true, blocks: null },
    });
    const row = s.sequence!.find((r) => r.id === "Press" && r.kind === "work")!;
    expect(row).toEqual(before);
  });

  it("an all-day override drops the staged row and placement but stays included", () => {
    let s = sequenced();
    s = { ...s, placements: { ...s.placements, Press: "10:00" } };
    s = reducer(s, {
      type: "OVERRIDE_SET",
      id: "Press",
      override: { included: true, blocks: 0 },
    });
    expect(s.sequence!.some((r) => r.id === "Press")).toBe(false);
    expect(s.placements.Press).toBeUndefined();
    expect(queueState(s, "Press")).toBe("background");
    expect(s.overrides.Press).toEqual({ included: true, blocks: 0 });
  });

  it("a half-block override resizes a row to 15 minutes", () => {
    let s = sequenced();
    const before = s.sequence!.find((r) => r.id === "Press")!;
    s = reducer(s, {
      type: "OVERRIDE_SET",
      id: "Press",
      override: { included: true, blocks: 0.5 },
    });
    const row = s.sequence!.find((r) => r.id === "Press")!;
    const startMin = Number(before.start.slice(0, 2)) * 60 + Number(before.start.slice(3));
    const endMin = Number(row.end.slice(0, 2)) * 60 + Number(row.end.slice(3));
    expect((endMin - startMin + 1440) % 1440).toBe(15);
  });

  it("zone rows survive exclusion filtering", () => {
    let s = sequenced();
    s = reducer(s, {
      type: "OVERRIDE_SET",
      id: "Trinoor",
      override: { included: false, blocks: null },
    });
    expect(s.sequence!.some((r) => r.kind === "zone")).toBe(true);
  });
});

describe("queue states", () => {
  it("loads timed recurring tasks as immutable scheduled placements", () => {
    const s0 = loaded();
    const inputs = structuredClone(s0.inputs!);
    inputs.assigned.push({
      id: "M2.5",
      name: "M2.5",
      path: null,
      source: "todoist",
      types: ["todoist"],
      urgency: null,
      deadline: inputs.validDate,
      priorityScore: 1,
      blocks: 0.5,
      durationLabel: "15min",
      todoistId: "meds",
      isRecurring: true,
      scheduledStart: "12:00",
    });
    const s = reducer(initialState, {
      type: "INPUTS_LOADED",
      inputs,
      ledger: { today: inputs.validDate, spent: 0, cap: 5, remaining: 5 },
    });

    expect(queueState(s, "M2.5")).toBe("scheduled");
    expect(s.pendingPinnedRows).toEqual([
      expect.objectContaining({ id: "M2.5", start: "12:00", end: "12:15" }),
    ]);
  });

  it("classifies scheduled / needs-placement / excluded / background", () => {
    let s = sequenced();
    expect(queueState(s, "Magic Mirror")).toBe("scheduled");
    expect(queueState(s, "Charge GoPro")).toBe("background");
    s = reducer(s, { type: "ROW_UNPLACED", id: "Magic Mirror" });
    expect(queueState(s, "Magic Mirror")).toBe("needs-placement");
    s = reducer(s, {
      type: "OVERRIDE_SET",
      id: "Magic Mirror",
      override: { included: false, blocks: null },
    });
    expect(queueState(s, "Magic Mirror")).toBe("excluded");
  });
});

describe("row moves", () => {
  it("preserves duration when moving a row", () => {
    let s = sequenced();
    s = reducer(s, { type: "ROW_MOVED", id: "Magic Mirror", start: "13:00" });
    const row = s.sequence!.find((r) => r.id === "Magic Mirror")!;
    expect(row.start).toBe("13:00");
    expect(row.end).toBe("14:30"); // 3 blocks = 90min preserved
  });

  it("ROW_PLACED uses the override duration when set", () => {
    let s = loaded();
    s = reducer(s, {
      type: "OVERRIDE_SET",
      id: "Note Processing",
      override: { included: true, blocks: 2 },
    });
    s = reducer(s, { type: "ROW_PLACED", id: "Note Processing", start: "10:00" });
    const row = s.sequence!.find((r) => r.id === "Note Processing")!;
    expect(row.end).toBe("11:00");
  });
});

describe("fingerprint drift", () => {
  it("mismatch invalidates sequence, shadow, and armed state", () => {
    let s = shadowed();
    s = reducer(s, { type: "ARM_LIVE" });
    s = reducer(s, { type: "FINGERPRINT_MISMATCH" });
    expect(s.seqPhase).toBe("dirty");
    expect(s.shadow).toBeNull();
    expect(s.shadowPhase).toBe("none");
    expect(s.liveArmed).toBe(false);
    expect(s.fingerprint).toBeNull();
    expect(s.validation).toBeNull();
  });

  it("mismatch closes the approval drawer and raises a blocking drift alert", () => {
    let s = shadowed();
    s = reducer(s, { type: "UI", patch: { approvalOpen: true } });
    s = reducer(s, { type: "FINGERPRINT_MISMATCH" });
    expect(s.ui.approvalOpen).toBe(false);
    expect(s.driftNotice).toMatch(/nothing was written/);
    const list = alerts(s);
    expect(list[0]).toEqual({ level: "error", text: s.driftNotice });
  });

  it("drift alert clears on resequence start and on a clean revalidation", () => {
    let s = shadowed();
    s = reducer(s, { type: "FINGERPRINT_MISMATCH" });
    const reseq = reducer(s, { type: "SEQUENCE_START" });
    expect(reseq.driftNotice).toBeNull();
    const failed = reducer(s, {
      type: "VALIDATED",
      validation: { ok: false, hardErrors: ["overlap"], warnings: [] },
    });
    expect(failed.driftNotice).not.toBeNull(); // failed revalidation keeps it
    const clean = reducer(s, {
      type: "VALIDATED",
      validation: { ok: true, hardErrors: [], warnings: [] },
    });
    expect(clean.driftNotice).toBeNull();
  });
});

describe("commit gates", () => {
  it("ARM_LIVE is a no-op without a current shadow", () => {
    let s = sequenced();
    s = reducer(s, { type: "ARM_LIVE" });
    expect(s.liveArmed).toBe(false);
  });

  it("live commit requires current shadow + armed + valid sequence", () => {
    let s = shadowed();
    expect(canLiveCommit(s)).toBe(false);
    s = reducer(s, { type: "ARM_LIVE" });
    expect(canLiveCommit(s)).toBe(true);
  });

  it("COMMIT_ABORT returns to idle without a report", () => {
    let s = shadowed();
    s = reducer(s, { type: "ARM_LIVE" });
    s = reducer(s, { type: "COMMIT_START" });
    s = reducer(s, { type: "COMMIT_ABORT" });
    expect(s.commitPhase).toBe("idle");
    expect(s.commitReport).toBeNull();
    expect(s.liveArmed).toBe(false);
  });

  it("COMMIT_DONE maps report status to phase", () => {
    let s = shadowed();
    const done = reducer(s, {
      type: "COMMIT_DONE",
      report: { status: "ok", surfaces: [], verifyFailures: [] },
    });
    expect(done.commitPhase).toBe("done");
    const partial = reducer(s, {
      type: "COMMIT_DONE",
      report: { status: "partial", surfaces: [], verifyFailures: ["x"] },
    });
    expect(partial.commitPhase).toBe("partial");
  });
});

describe("dock state machine", () => {
  it("walks setup → sequence → review → preview → verified", () => {
    const sc = makeScenario("ready");
    let s = reducer(initialState, {
      type: "INPUTS_LOADED",
      inputs: { ...sc.inputs, daySetup: { ...sc.inputs.daySetup, confirmed: false } },
      ledger,
    });
    expect(dockState(s)).toBe("setup");
    s = reducer(s, { type: "SETUP_SAVED", daySetup: { ...s.daySetup, confirmed: true } });
    expect(dockState(s)).toBe("sequence");
    s = reducer(s, { type: "SEQUENCE_START" });
    expect(dockState(s)).toBe("sequencing");
    s = reducer(s, {
      type: "SEQUENCE_OK",
      sequence: sc.proposal!.sequence,
      warnings: [],
      fingerprint: "fp",
      anchoredSourceFingerprint: sc.inputs.anchoredSourceFingerprint,
      ledger: { ...ledger, spent: 1, remaining: 3 },
    });
    expect(dockState(s)).toBe("review");
    s = reducer(s, { type: "SHADOW_START" });
    s = reducer(s, { type: "SHADOW_OK", shadow: makeScenario("commit-preview").shadow });
    expect(dockState(s)).toBe("preview");
    s = reducer(s, {
      type: "COMMIT_DONE",
      report: { status: "ok", surfaces: [], verifyFailures: [] },
    });
    expect(dockState(s)).toBe("verified");
  });

  it("hard validation errors surface as fix state", () => {
    let s = sequenced();
    s = reducer(s, {
      type: "VALIDATED",
      validation: { ok: false, hardErrors: ["overlap"], warnings: [] },
    });
    expect(dockState(s)).toBe("fix");
    expect(canShadow(s)).toBe(false);
  });

  it("exhausted ledger without a valid sequence → budget-manual", () => {
    let s = loaded();
    s = reducer(s, { type: "SETUP_SAVED", daySetup: { ...s.daySetup, confirmed: true } });
    s = reducer(s, {
      type: "LEDGER_UPDATED",
      ledger: { today: "2026-07-18", spent: 4, cap: 4, remaining: 0 },
    });
    expect(dockState(s)).toBe("budget-manual");
    expect(canAutoSequence(s)).toBe(false);
  });
});

describe("alerts roll-up", () => {
  it("aggregates source warnings, validation issues, and overassignment", () => {
    const sc = makeScenario("conflict");
    let s = reducer(initialState, {
      type: "INPUTS_LOADED",
      inputs: sc.inputs,
      ledger: sc.ledger,
    });
    s = reducer(s, {
      type: "VALIDATED",
      validation: { ok: false, hardErrors: ["Press overlaps Sudsing"], warnings: ["late start"] },
    });
    const list = alerts(s);
    expect(list.some((a) => a.level === "warning" && a.text.includes("Todoist"))).toBe(true);
    expect(list.some((a) => a.level === "error" && a.text.includes("overlaps"))).toBe(true);
    expect(list.some((a) => a.text.includes("Overassigned"))).toBe(true);
  });
});

describe("sequence rows from proposal", () => {
  it("SEQUENCE_OK resets any prior shadow", () => {
    let s = shadowed();
    const sc = makeScenario("ready");
    s = reducer(s, {
      type: "SEQUENCE_OK",
      sequence: sc.proposal!.sequence as SequenceRow[],
      warnings: [],
      fingerprint: "fp2",
      anchoredSourceFingerprint: sc.inputs.anchoredSourceFingerprint,
      ledger,
    });
    expect(s.shadow).toBeNull();
    expect(s.shadowPhase).toBe("none");
    expect(s.seqPhase).toBe("valid");
  });
});

describe("T27 recurring placement immunity", () => {
  const recurringItem = {
    id: "LOOTS",
    name: "LOOTS",
    path: null,
    source: "todoist" as const,
    types: ["todoist"],
    urgency: null,
    deadline: null,
    priorityScore: 1,
    blocks: 1 / 6,
    durationLabel: "5min",
    todoistId: "loots",
    isRecurring: true,
    scheduledStart: "12:30",
  };

  it("seeds recurring pins on INPUTS_LOADED even when other pins exist", () => {
    const s0 = loaded();
    const inputs = structuredClone(s0.inputs!);
    inputs.assigned.push(structuredClone(recurringItem));
    const manualPin: SequenceRow = {
      id: "Magic Mirror", start: "09:00", end: "10:00", zone: null, kind: "work",
    };
    const withPin = { ...initialState, pendingPinnedRows: [manualPin] };
    const s = reducer(withPin, {
      type: "INPUTS_LOADED",
      inputs,
      ledger: { today: inputs.validDate, spent: 0, cap: 5, remaining: 5 },
    });
    expect(s.pendingPinnedRows).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ id: "Magic Mirror", start: "09:00" }),
        expect.objectContaining({ id: "LOOTS", start: "12:30" }),
      ]),
    );
  });

  it("queueState never offers a timed recurring row for placement, pin or no pin", () => {
    const s0 = loaded();
    const inputs = structuredClone(s0.inputs!);
    inputs.assigned.push(structuredClone(recurringItem));
    let s = reducer(initialState, {
      type: "INPUTS_LOADED",
      inputs,
      ledger: { today: inputs.validDate, spent: 0, cap: 5, remaining: 5 },
    });
    // even with the pin set forcibly cleared (fingerprint-drift path), the
    // recurring row must not fall back to needs-placement
    s = { ...s, pendingPinnedRows: [], sequence: null };
    expect(queueState(s, "LOOTS")).toBe("scheduled");
  });

  it("SEQUENCE_OK adopts the server's effective pin set when provided", () => {
    const sc = makeScenario("ready");
    let s = loaded();
    const serverPins: SequenceRow[] = [
      { id: "LOOTS", start: "12:30", end: "12:35", zone: null, kind: "work" },
    ];
    s = reducer(s, {
      type: "SEQUENCE_OK",
      sequence: sc.proposal!.sequence,
      warnings: [],
      fingerprint: fingerprintFixedInputs(fixedInputsOf(sc.inputs)),
      anchoredSourceFingerprint: sc.inputs.anchoredSourceFingerprint,
      pinnedRows: serverPins,
      ledger: { ...ledger, spent: 1, remaining: 3 },
    });
    expect(s.pinnedRows).toEqual(serverPins);
    expect(s.pendingPinnedRows).toEqual(serverPins);
  });
});

describe("T28 calendar dismissal (effectiveAnchoredBlocks)", () => {
  /* FEEDBACK-28 retry (2026-08-17): a skip is honored only when it is
     EXPLICIT current-run intent (CalendarImpact → saveAnchoredOverride,
     recorded in currentRunCalendarSkips). A persisted skip — loaded from the
     server daySetup or merged onto the raw calendar row by a previous run —
     must not suppress a current wall; the event stays visible and
     participates in planning walls until the user re-expresses the skip. */
  it("applies skipToday and the local accounting projection to calendar rows for a current-run skip", () => {
    let s = loaded();
    const cal = s.inputs!.anchored.find((a) => a.kind === "calendar")!;
    s = {
      ...s,
      currentRunCalendarSkips: [cal.id],
      daySetup: {
        ...s.daySetup,
        anchored: {
          ...s.daySetup.anchored,
          [cal.id]: { on: false, skipToday: true, time: "22:00", blocks: 9 },
        },
      },
    };
    const eff = effectiveAnchoredBlocks(s).find((a) => a.id === cal.id)!;
    expect(eff.skipToday).toBe(true);
    expect(eff.start).toBe(cal.start);        // LD19: event time immutable
    // FEEDBACK-09: blocks is a local accounting projection — the accounted
    // duration changes, the event's wall-clock window does not.
    expect(eff.durationMin).toBe(270);        // 9 blocks × 30
    expect(eff.end).toBe(cal.end);
    expect(eff.on).toBe(true);                // participation, not existence
  });

  it("a persisted skip without current-run intent does NOT suppress a calendar wall", () => {
    let s = loaded();
    const cal = s.inputs!.anchored.find((a) => a.kind === "calendar")!;
    // Persisted state: the server daySetup carries the skip, but the user has
    // not re-expressed it this run — the event must stay visible and walled.
    s = {
      ...s,
      daySetup: {
        ...s.daySetup,
        anchored: {
          ...s.daySetup.anchored,
          [cal.id]: { on: false, skipToday: true, time: null, blocks: null },
        },
      },
    };
    const eff = effectiveAnchoredBlocks(s).find((a) => a.id === cal.id)!;
    expect(eff.skipToday).toBe(false);
    expect(eff.on).toBe(true);
    expect(eff.start).toBe(cal.start);
    expect(eff.end).toBe(cal.end);
    expect(calendarWalls(effectiveAnchoredBlocks(s))).toEqual(
      expect.arrayContaining([{ start: 9 * 60 + 15, end: 9 * 60 + 45 }]),
    );
  });

  it("a server-merged persisted skip on the raw calendar row also stays visible", () => {
    let s = loaded();
    const cal = s.inputs!.anchored.find((a) => a.kind === "calendar")!;
    s = {
      ...s,
      inputs: {
        ...s.inputs!,
        anchored: s.inputs!.anchored.map((a) =>
          a.id === cal.id ? { ...a, skipToday: true } : a,
        ),
      },
    };
    const eff = effectiveAnchoredBlocks(s).find((a) => a.id === cal.id)!;
    expect(eff.skipToday).toBe(false);
    expect(calendarWalls(effectiveAnchoredBlocks(s))).toEqual(
      expect.arrayContaining([{ start: 9 * 60 + 15, end: 9 * 60 + 45 }]),
    );
  });

  it("calendar rows without an override are untouched", () => {
    const s = loaded();
    const cal = s.inputs!.anchored.find((a) => a.kind === "calendar")!;
    const eff = effectiveAnchoredBlocks(s).find((a) => a.id === cal.id)!;
    expect(eff).toEqual(cal);
  });

  it("a blocks projection alters only the accounted duration, never start/end (FEEDBACK-09)", () => {
    let s = loaded();
    const cal = s.inputs!.anchored.find((a) => a.kind === "calendar")!;
    s = reducer(s, {
      type: "SETUP_SAVED",
      daySetup: {
        ...s.daySetup,
        confirmed: true,
        anchored: {
          ...s.daySetup.anchored,
          [cal.id]: { on: true, skipToday: false, time: null, blocks: 4 },
        },
      },
    });
    const eff = effectiveAnchoredBlocks(s).find((a) => a.id === cal.id)!;
    expect(eff.durationMin).toBe(120); // 4 blocks × 30 projection
    expect(eff.start).toBe(cal.start);
    expect(eff.end).toBe(cal.end);
    expect(eff.skipToday).toBe(false);
  });
});

describe("source-health gate (selectors)", () => {
  it("sourceHealthBlocked is false when health is ok", () => {
    const s = sequenced();
    expect(s.inputs!.sourceHealth).toBe("ok");
    expect(sourceHealthBlocked(s)).toBe(false);
  });

  it("sourceHealthBlocked is true when health is degraded", () => {
    const s = sequenced();
    s.inputs!.sourceHealth = "degraded";
    expect(sourceHealthBlocked(s)).toBe(true);
  });

  it("sourceHealthBlocked is true when health is failed", () => {
    const s = sequenced();
    s.inputs!.sourceHealth = "failed";
    expect(sourceHealthBlocked(s)).toBe(true);
  });

  it("canAutoSequence is false when source health is degraded", () => {
    const s = sequenced();
    expect(canAutoSequence(s)).toBe(true); // healthy baseline
    s.inputs!.sourceHealth = "degraded";
    expect(canAutoSequence(s)).toBe(false);
  });

  it("canAutoSequence is false when source health is failed", () => {
    const s = sequenced();
    s.inputs!.sourceHealth = "failed";
    expect(canAutoSequence(s)).toBe(false);
  });

  it("canShadow is false when source health is degraded", () => {
    const s = sequenced();
    expect(canShadow(s)).toBe(true); // healthy baseline
    s.inputs!.sourceHealth = "degraded";
    expect(canShadow(s)).toBe(false);
  });

  it("canShadow is false when source health is failed", () => {
    const s = sequenced();
    s.inputs!.sourceHealth = "failed";
    expect(canShadow(s)).toBe(false);
  });

  it("canLiveCommit is false when source health is degraded", () => {
    let s = shadowed();
    s = reducer(s, { type: "ARM_LIVE" });
    expect(canLiveCommit(s)).toBe(true); // healthy baseline
    s.inputs!.sourceHealth = "degraded";
    expect(canLiveCommit(s)).toBe(false);
  });

  it("canLiveCommit is false when source health is failed", () => {
    let s = shadowed();
    s = reducer(s, { type: "ARM_LIVE" });
    s.inputs!.sourceHealth = "failed";
    expect(canLiveCommit(s)).toBe(false);
  });

  it("healthy source health does not block any selector", () => {
    const seq = sequenced();
    expect(canAutoSequence(seq)).toBe(true);
    expect(canShadow(seq)).toBe(true);
    let sh = shadowed();
    sh = reducer(sh, { type: "ARM_LIVE" });
    expect(canLiveCommit(sh)).toBe(true);
  });
});
