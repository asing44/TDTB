/* accept.test.ts — good-enough plan override (locked decision 24, T13b).
   Acceptable defects gate shadow/live commit by default; one explicit
   ACCEPT_DEFECTS records them verbatim and unblocks. Acknowledgment is
   today-only: any edit, resequence, refresh drift, or reload kills it.
   Hard blockers are never overridable by acceptance. */

import { describe, expect, it } from "vitest";
import {
  acceptableDefects,
  alerts,
  canAutoSequence,
  canLiveCommit,
  canShadow,
  defectsResolved,
  initialState,
  reducer,
  shadowBlockers,
  type AppState,
} from "./store";
import { makeScenario, fixedInputsOf } from "../fixtures/scenarios";
import { fingerprintFixedInputs } from "../model/fingerprint";
import type { Ledger, SequenceRow, ShadowDiff, Validation } from "../model/types";

const ledger: Ledger = { today: "2026-07-18", spent: 1, cap: 4, remaining: 3 };

const WARN = "⚠ past EOD — ends 23:30, effective EOD 23:00";
const clean: Validation = { ok: true, hardErrors: [], warnings: [] };

function sequenced(warnings: string[] = [WARN]): AppState {
  const sc = makeScenario("ready");
  let s = reducer(initialState, {
    type: "INPUTS_LOADED",
    inputs: sc.inputs,
    ledger: { ...sc.ledger },
  });
  s = reducer(s, { type: "SETUP_SAVED", daySetup: { ...sc.inputs.daySetup, confirmed: true } });
  s = reducer(s, {
    type: "SEQUENCE_OK",
    sequence: sc.proposal!.sequence,
    warnings,
    fingerprint: fingerprintFixedInputs(fixedInputsOf(sc.inputs)),
    anchoredSourceFingerprint: sc.inputs.anchoredSourceFingerprint,
    ledger,
  });
  return s;
}

function conflictShadow(): ShadowDiff {
  return {
    entries: [
      {
        step: "B",
        system: "todoist",
        action: "create",
        name: "Press",
        idOrPath: "123",
        time: "17:30",
        durationMin: 60,
        classification: "conflict",
        detail: {},
      },
    ],
    unavailableSurfaces: [],
    counts: { "would-create": 0, "would-update": 0, "no-op": 0, conflict: 1, unavailable: 0 },
  };
}

describe("acceptable-defect gating", () => {
  it("soft warnings block shadow preview by default", () => {
    const s = sequenced();
    expect(s.validation!.ok).toBe(true);
    expect(acceptableDefects(s)).toEqual([WARN]);
    expect(defectsResolved(s)).toBe(false);
    expect(canShadow(s)).toBe(false);
  });

  it("ACCEPT_DEFECTS records the current findings verbatim and unblocks preview", () => {
    let s = sequenced();
    s = reducer(s, { type: "ACCEPT_DEFECTS" });
    expect(s.acceptedDefects).toEqual([WARN]);
    expect(defectsResolved(s)).toBe(true);
    expect(canShadow(s)).toBe(true);
  });

  it("a defect-free plan needs no acceptance; ACCEPT_DEFECTS is a no-op", () => {
    let s = sequenced([]);
    expect(canShadow(s)).toBe(true);
    s = reducer(s, { type: "ACCEPT_DEFECTS" });
    expect(s.acceptedDefects).toBeNull();
  });

  it("a grant-covered task-task overlap is no defect and surfaces as info (T29)", () => {
    const sc = makeScenario("ready");
    let s = reducer(initialState, {
      type: "INPUTS_LOADED",
      inputs: sc.inputs,
      ledger: { ...sc.ledger },
    });
    s = reducer(s, { type: "SETUP_SAVED", daySetup: { ...sc.inputs.daySetup, confirmed: true } });
    const rows: SequenceRow[] = [
      { id: "Haircut", start: "14:00", end: "14:30", zone: null, kind: "work" },
      { id: "Return burr", start: "14:00", end: "14:30", zone: null, kind: "work" },
    ];
    s = reducer(s, {
      type: "SEQUENCE_OK",
      sequence: rows,
      warnings: [],
      fingerprint: fingerprintFixedInputs(fixedInputsOf(sc.inputs)),
      anchoredSourceFingerprint: sc.inputs.anchoredSourceFingerprint,
      planningConfigFingerprint: "pcfp",
      overlapGrants: [{
        primaryId: "Return burr",
        companionId: "Haircut",
        primaryInterval: { start: "14:00", end: "14:30" },
        companionInterval: { start: "14:00", end: "14:30" },
        reason: "ride-along",
        planningConfigFingerprint: "pcfp",
      }],
      ledger,
    });
    expect(acceptableDefects(s)).toEqual([]);
    expect(defectsResolved(s)).toBe(true);
    expect(alerts(s)).toContainEqual({
      level: "info",
      text: "Allowed overlap: 'Haircut' (14:00-14:30) with 'Return burr' (14:00-14:30) — ride-along",
    });
  });

  it("an ungranted task-task overlap remains an acceptable defect (T29)", () => {
    const sc = makeScenario("ready");
    let s = reducer(initialState, {
      type: "INPUTS_LOADED",
      inputs: sc.inputs,
      ledger: { ...sc.ledger },
    });
    s = reducer(s, { type: "SETUP_SAVED", daySetup: { ...sc.inputs.daySetup, confirmed: true } });
    const rows: SequenceRow[] = [
      { id: "Haircut", start: "14:00", end: "14:30", zone: null, kind: "work" },
      { id: "Return burr", start: "14:00", end: "14:30", zone: null, kind: "work" },
    ];
    s = reducer(s, {
      type: "SEQUENCE_OK",
      sequence: rows,
      warnings: [],
      fingerprint: fingerprintFixedInputs(fixedInputsOf(sc.inputs)),
      anchoredSourceFingerprint: sc.inputs.anchoredSourceFingerprint,
      planningConfigFingerprint: "pcfp",
      overlapGrants: [],
      ledger,
    });
    expect(acceptableDefects(s)).toHaveLength(1);
    expect(defectsResolved(s)).toBe(false);
  });

  it("overassignment is an acceptable defect", () => {
    let s = sequenced([]);
    s = reducer(s, {
      type: "CAPACITY_UPDATED",
      capacity: { ...s.capacity!, overassigned: true, free: -1.5, remaining: "1.5 blocks over" },
    });
    expect(acceptableDefects(s)).toEqual(["Overassigned — ⚠ 45min over"]);
    expect(canShadow(s)).toBe(false);
    s = reducer(s, { type: "ACCEPT_DEFECTS" });
    expect(canShadow(s)).toBe(true);
  });

  it("overlaps among movable work are acceptable defects", () => {
    let s = sequenced([]);
    const [a, b] = s.sequence!.filter((r) => r.kind === "work");
    s = {
      ...s,
      sequence: s.sequence!.map((r) =>
        r.id === b.id && r.kind === "work"
          ? { ...r, start: a.start, end: a.end }
          : r,
      ),
    };
    const defects = acceptableDefects(s);
    expect(defects.some((d) => d.includes("overlaps movable work"))).toBe(true);
    expect(canShadow(s)).toBe(false);
    s = reducer(s, { type: "ACCEPT_DEFECTS" });
    expect(canShadow(s)).toBe(true);
  });

  it("a defect that resolves itself stays covered; a NEW defect re-blocks", () => {
    let s = sequenced();
    s = reducer(s, { type: "ACCEPT_DEFECTS" });
    s = reducer(s, { type: "VALIDATED", validation: clean });
    expect(canShadow(s)).toBe(true); // resolved on its own
    s = reducer(s, {
      type: "VALIDATED",
      validation: { ok: true, hardErrors: [], warnings: [WARN, "fresh defect"] },
    });
    expect(canShadow(s)).toBe(false); // unacknowledged newcomer
  });
});

describe("acceptance is today-only review state", () => {
  const accepted = () => reducer(sequenced(), { type: "ACCEPT_DEFECTS" });

  it.each([
    [
      "an override edit",
      (s: AppState) =>
        reducer(s, { type: "OVERRIDE_SET", id: "Press", override: { included: true, blocks: 2 } }),
    ],
    ["a row move", (s: AppState) => reducer(s, { type: "ROW_MOVED", id: "Press", start: "18:00" })],
    ["a row placement", (s: AppState) => reducer(s, { type: "ROW_PLACED", id: "Press", start: "18:00" })],
    ["a row unplacement", (s: AppState) => reducer(s, { type: "ROW_UNPLACED", id: "Press" })],
    ["a day-setup save", (s: AppState) => reducer(s, { type: "SETUP_SAVED", daySetup: s.daySetup })],
    ["a resequence", (s: AppState) => reducer(s, { type: "SEQUENCE_START" })],
    ["fixed-input drift", (s: AppState) => reducer(s, { type: "FINGERPRINT_MISMATCH" })],
  ])("%s revokes acceptance", (_name, act) => {
    const s = act(accepted());
    expect(s.acceptedDefects).toBeNull();
  });

  it("SESSION_RESTORED never restores acceptance", () => {
    let s = accepted();
    s = reducer(s, {
      type: "SESSION_RESTORED",
      overrides: {},
      placements: {},
      sequence: s.sequence,
      fingerprint: s.fingerprint,
      anchoredSourceFingerprint: s.anchoredSourceFingerprint,
    });
    expect(s.acceptedDefects).toBeNull();
  });

  it("date rollover clears acceptance", () => {
    let s = accepted();
    const rolled = makeScenario("fresh");
    rolled.inputs.validDate = "2026-07-19";
    s = reducer(s, { type: "INPUTS_LOADED", inputs: rolled.inputs, ledger });
    expect(s.acceptedDefects).toBeNull();
  });
});

describe("acceptance vs source refresh", () => {
  function refreshed(s: AppState, mutate?: (inputs: typeof s.inputs) => void): AppState {
    const sc = makeScenario("ready");
    if (mutate) mutate(sc.inputs);
    return reducer(s, {
      type: "SOURCE_REFRESH_OK",
      inputs: sc.inputs,
      ledger,
      fingerprint: fingerprintFixedInputs(fixedInputsOf(sc.inputs)),
      anchoredSourceFingerprint: sc.inputs.anchoredSourceFingerprint,
      at: "2026-07-18T09:00:00Z",
    });
  }

  it("a clean no-change refresh preserves acceptance", () => {
    let s = reducer(sequenced(), { type: "ACCEPT_DEFECTS" });
    s = refreshed(s);
    expect(s.acceptedDefects).toEqual([WARN]);
  });

  it("refresh drift (changed fixed inputs) revokes acceptance", () => {
    let s = reducer(sequenced(), { type: "ACCEPT_DEFECTS" });
    s = refreshed(s, (inputs) => {
      inputs!.anchoredSourceFingerprint = "drifted";
    });
    expect(s.driftNotice).not.toBeNull();
    expect(s.acceptedDefects).toBeNull();
  });

  it("assigned churn on refresh revokes acceptance", () => {
    let s = reducer(sequenced(), { type: "ACCEPT_DEFECTS" });
    s = refreshed(s, (inputs) => {
      inputs!.assigned.push({
        id: "Newcomer", name: "Newcomer", path: null, source: "todoist",
        types: [], urgency: null, deadline: null, priorityScore: 1,
        blocks: 1, durationLabel: "30min", todoistId: "6fxNEW",
      });
    });
    expect(s.acceptedDefects).toBeNull();
  });
});

describe("hard blockers are never overridable by acceptance", () => {
  it("hard validation errors: ACCEPT_DEFECTS refuses and nothing unblocks", () => {
    let s = sequenced();
    s = reducer(s, {
      type: "VALIDATED",
      validation: { ok: false, hardErrors: ["'Press': overlaps wall"], warnings: [WARN] },
    });
    s = reducer(s, { type: "ACCEPT_DEFECTS" });
    expect(s.acceptedDefects).toBeNull();
    expect(canShadow(s)).toBe(false);
  });

  it("shadow conflicts block arm + live commit even with defects accepted", () => {
    let s = reducer(sequenced(), { type: "ACCEPT_DEFECTS" });
    s = reducer(s, { type: "SHADOW_START" });
    s = reducer(s, { type: "SHADOW_OK", shadow: conflictShadow() });
    s = reducer(s, { type: "ARM_LIVE" });
    expect(s.liveArmed).toBe(false);
    expect(canLiveCommit(s)).toBe(false);
  });

  it("unaccepted defects block ARM_LIVE at the reducer, acceptance unblocks", () => {
    let s = sequenced();
    s = reducer(s, { type: "SHADOW_START" });
    s = reducer(s, { type: "SHADOW_OK", shadow: makeScenario("commit-preview").shadow });
    s = reducer(s, { type: "ARM_LIVE" }); // direct dispatch bypassing UI
    expect(s.liveArmed).toBe(false);
    s = reducer(s, { type: "ACCEPT_DEFECTS" });
    s = reducer(s, { type: "ARM_LIVE" });
    expect(s.liveArmed).toBe(true);
    expect(canLiveCommit(s)).toBe(true);
  });

  it("stale shadow after an edit stays a blocker — the edit also revoked acceptance", () => {
    let s = sequenced();
    s = reducer(s, { type: "SHADOW_START" });
    s = reducer(s, { type: "SHADOW_OK", shadow: makeScenario("commit-preview").shadow });
    s = reducer(s, { type: "ACCEPT_DEFECTS" });
    s = reducer(s, { type: "ARM_LIVE" });
    expect(s.liveArmed).toBe(true);
    s = reducer(s, { type: "ROW_MOVED", id: "Press", start: "18:00" });
    expect(s.shadowPhase).toBe("stale");
    expect(s.acceptedDefects).toBeNull();
    expect(canLiveCommit(s)).toBe(false);
  });

  it("spent-ledger sequencing gate ignores acceptance entirely", () => {
    let s = sequenced();
    s = reducer(s, { type: "LEDGER_UPDATED", ledger: { ...ledger, spent: 4, remaining: 0 } });
    const before = canAutoSequence(s);
    s = reducer(s, { type: "ACCEPT_DEFECTS" });
    expect(canAutoSequence(s)).toBe(before);
    expect(canAutoSequence(s)).toBe(false);
  });
});

/* 2026-07-27: a live preview showed "vault conflict: # TDTB Plan" and "vault
   conflict: Phase-1 captures". Both meant one thing — today's daily note did
   not exist yet — but the blocker line named only the blocked write, so the
   cause had to be dug out of shadow.py by hand. Every CONFLICT the diff emits
   carries a `reason`; these pin that it reaches the operator. */
describe("shadow blockers name the cause, not just the blocked write", () => {
  function conflictWith(detail: Record<string, unknown>): ShadowDiff {
    const base = conflictShadow();
    return { ...base, entries: [{ ...base.entries[0], system: "vault", name: "# TDTB Plan", detail }] };
  }

  it("carries the reason through to the blocker line", () => {
    let s = sequenced();
    s = reducer(s, { type: "SHADOW_OK", shadow: conflictWith({ reason: "daily note not found" }) });
    expect(shadowBlockers(s)).toEqual([
      "vault conflict: # TDTB Plan — daily note not found",
    ]);
  });

  it("still names the write when a conflict carries no reason", () => {
    let s = sequenced();
    s = reducer(s, { type: "SHADOW_OK", shadow: conflictWith({}) });
    expect(shadowBlockers(s)).toEqual(["vault conflict: # TDTB Plan"]);
  });

  it("a non-string reason degrades to the bare line rather than rendering junk", () => {
    let s = sequenced();
    s = reducer(s, { type: "SHADOW_OK", shadow: conflictWith({ reason: { nested: true } }) });
    expect(shadowBlockers(s)).toEqual(["vault conflict: # TDTB Plan"]);
  });

  it("blocking still turns on the conflict itself, not on having a reason", () => {
    let s = sequenced();
    s = reducer(s, { type: "SHADOW_OK", shadow: conflictWith({ reason: "daily note not found" }) });
    expect(canLiveCommit(s)).toBe(false);
  });
});
