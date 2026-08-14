/* refresh.test.ts — pure reconciliation for explicit source refresh (T13,
   locked decisions 20/21). No store, no adapter: rows in, rows out. */

import { describe, expect, it } from "vitest";
import { reconcileRefresh, summaryHasChanges, type ReconcileArgs } from "./refresh";
import type { AnchoredBlock, AssignedItem, SequenceRow } from "./types";

function item(id: string, blocks = 1, extra: Partial<AssignedItem> = {}): AssignedItem {
  return {
    id,
    name: id,
    path: null,
    source: "todoist",
    types: ["task"],
    urgency: null,
    todoistId: null,
    deadline: null,
    priorityScore: 10,
    blocks,
    durationLabel: `${blocks * 30}min`,
    ...extra,
  };
}

function anchored(id: string, extra: Partial<AnchoredBlock> = {}): AnchoredBlock {
  return {
    id,
    name: id,
    kind: "hard",
    start: "10:00",
    end: "11:00",
    durationMin: 60,
    overlapAllowed: false,
    on: true,
    skipToday: false,
    ...extra,
  };
}

const FRAME = { anchor: "07:30", effectiveEod: "23:00" };

function args(over: Partial<ReconcileArgs> = {}): ReconcileArgs {
  return {
    prevAssigned: [],
    nextAssigned: [],
    prevAnchored: [],
    nextAnchored: [],
    anchoredOverrides: {},
    frame: FRAME,
    overrides: {},
    placements: {},
    sequence: null,
    ...over,
  };
}

describe("assigned reconciliation by stable identity", () => {
  it("classifies added / removed / changed and reports names", () => {
    const r = reconcileRefresh(
      args({
        prevAssigned: [item("A", 1), item("B", 2), item("C", 1)],
        nextAssigned: [item("A", 1), item("B", 3), item("D", 1)],
      }),
    );
    expect(r.summary.added).toEqual(["D"]);
    expect(r.summary.removed).toEqual(["C"]);
    expect(r.summary.changed).toEqual(["B"]);
    expect(summaryHasChanges(r.summary)).toBe(true);
  });

  it("an unchanged set reports no changes", () => {
    const rows = [item("A", 1), item("B", 2)];
    const r = reconcileRefresh(args({ prevAssigned: rows, nextAssigned: rows }));
    expect(summaryHasChanges(r.summary)).toBe(false);
  });

  it("prunes overrides, placements, and staged rows for disappeared items", () => {
    const seq: SequenceRow[] = [
      { id: "A", start: "10:00", end: "10:30", zone: null, kind: "work" },
      { id: "C", start: "11:00", end: "11:30", zone: null, kind: "work" },
      { id: "Trinoor", start: "08:30", end: "17:00", zone: "Trinoor", kind: "zone" },
    ];
    const r = reconcileRefresh(
      args({
        prevAssigned: [item("A"), item("C")],
        nextAssigned: [item("A")],
        overrides: { A: { included: true, blocks: 2 }, C: { included: false, blocks: null } },
        placements: { A: "10:00", C: "11:00" },
        sequence: seq,
      }),
    );
    expect(Object.keys(r.overrides)).toEqual(["A"]);
    expect(Object.keys(r.placements)).toEqual(["A"]);
    expect(r.sequence!.map((x) => x.id)).toEqual(["A", "Trinoor"]);
    expect(r.sequenceTouched).toBe(true);
  });
});

describe("staged-row durations vs upstream duration changes", () => {
  const seq: SequenceRow[] = [
    { id: "A", start: "10:00", end: "10:30", zone: null, kind: "work" },
  ];

  it("resizes a staged row when resolved blocks changed and no local override exists", () => {
    const r = reconcileRefresh(
      args({
        prevAssigned: [item("A", 1)],
        nextAssigned: [item("A", 2)],
        sequence: seq,
      }),
    );
    expect(r.sequence![0].end).toBe("11:00");
    expect(r.sequenceTouched).toBe(true);
  });

  it("a local duration override keeps winning — row untouched", () => {
    const r = reconcileRefresh(
      args({
        prevAssigned: [item("A", 1)],
        nextAssigned: [item("A", 2)],
        overrides: { A: { included: true, blocks: 1 } },
        sequence: seq,
      }),
    );
    expect(r.sequence![0].end).toBe("10:30");
  });

  it("blocks→0 upstream (all day) drops the row and placement, keeps the item included", () => {
    const r = reconcileRefresh(
      args({
        prevAssigned: [item("A", 1)],
        nextAssigned: [item("A", 0)],
        placements: { A: "10:00" },
        sequence: seq,
      }),
    );
    expect(r.sequence).toEqual([]);
    expect(r.placements).toEqual({});
    expect(r.summary.changed).toEqual(["A"]);
  });
});

describe("anchored override retention (locked decision 21)", () => {
  const override = { on: true, skipToday: false, time: "10:30", blocks: 1 };

  it("raw spec change beneath a still-valid override → retained + reported", () => {
    const r = reconcileRefresh(
      args({
        prevAnchored: [anchored("Gym")],
        nextAnchored: [anchored("Gym", { durationMin: 90, end: "11:30" })],
        anchoredOverrides: { Gym: override },
      }),
    );
    expect(r.anchoredOverrides).toEqual({ Gym: override });
    expect(r.summary.overridesRetained).toEqual(["Gym"]);
    expect(r.summary.overridesDropped).toEqual([]);
  });

  it("positionally-out-of-window override is retained with a warning downstream (T13c)", () => {
    // Window shrinks to 12:00–13:00; the 10:30 override start now falls
    // outside it. Positional findings are warnings, not drops.
    const r = reconcileRefresh(
      args({
        prevAnchored: [anchored("Gym", { kind: "window", start: "10:00", end: "12:00" })],
        nextAnchored: [anchored("Gym", { kind: "window", start: "12:00", end: "13:00" })],
        anchoredOverrides: { Gym: override },
      }),
    );
    expect(r.anchoredOverrides).toEqual({ Gym: override });
    expect(r.summary.overridesRetained).toEqual(["Gym"]);
  });

  it("structurally-incompatible override (block reclassified to calendar) is dropped, never rebased", () => {
    const r = reconcileRefresh(
      args({
        prevAnchored: [anchored("Gym", { kind: "window", start: "10:00", end: "12:00" })],
        nextAnchored: [anchored("Gym", { kind: "calendar" })],
        anchoredOverrides: { Gym: override },
      }),
    );
    expect(r.anchoredOverrides).toEqual({});
    expect(r.summary.overridesDropped).toEqual(["Gym"]);
  });

  it("override for a disappeared block is dropped", () => {
    const r = reconcileRefresh(
      args({
        prevAnchored: [anchored("Gym")],
        nextAnchored: [],
        anchoredOverrides: { Gym: override },
      }),
    );
    expect(r.anchoredOverrides).toEqual({});
    expect(r.summary.overridesDropped).toEqual(["Gym"]);
  });

  it("unchanged upstream keeps the override silently — the server echoes the applied override back", () => {
    // /plan-inputs anchored rows are POST-apply_day_setup: on an unchanged
    // day the fresh effective row equals prev + our own override. That must
    // never read as drift (the 2026-07-20 scratch false positive).
    const r = reconcileRefresh(
      args({
        prevAnchored: [anchored("Gym")],
        nextAnchored: [anchored("Gym", { start: "10:30", durationMin: 30 })],
        anchoredOverrides: { Gym: override },
      }),
    );
    expect(r.anchoredOverrides).toEqual({ Gym: override });
    expect(summaryHasChanges(r.summary)).toBe(false);
  });
});

describe("T28 calendar dismissal retention", () => {
  it("a skip-only override on a calendar row survives refresh, sanitized", () => {
    const skipOnly = { on: true, skipToday: true, time: null };
    const r = reconcileRefresh(
      args({
        prevAnchored: [anchored("Farmers Market", { kind: "calendar" })],
        nextAnchored: [anchored("Farmers Market", { kind: "calendar" })],
        anchoredOverrides: { "Farmers Market": skipOnly },
      }),
    );
    expect(r.anchoredOverrides).toEqual({ "Farmers Market": skipOnly });
    expect(r.summary.overridesRetained).toEqual(["Farmers Market"]);
  });

  it("a non-skip override on a calendar row is still dropped", () => {
    const r = reconcileRefresh(
      args({
        prevAnchored: [anchored("Farmers Market", { kind: "calendar" })],
        nextAnchored: [anchored("Farmers Market", { kind: "calendar" })],
        anchoredOverrides: {
          "Farmers Market": { on: true, skipToday: false, time: "10:30" },
        },
      }),
    );
    expect(r.anchoredOverrides).toEqual({});
  });
});
