/* planMembership.test.ts — U1: LOCAL plan membership.

   Taking a row out of today's plan is a browser-only edit. These tests pin the
   three things that make that safe: the row leaves every inclusion reader at
   once (queue, capacity, staged row, pin, placement), the membership key is the
   STABLE SOURCE IDENTITY rather than the name-keyed row id, and the default
   ("in") is a complete no-op so U1 changes nothing a user can see. */

import { describe, expect, it } from "vitest";
import {
  includedItems,
  initialState,
  isIncluded,
  membershipKeyOf,
  queueState,
  reducer,
  type AppState,
} from "./store";
import { makeScenario, fixedInputsOf } from "../fixtures/scenarios";
import { fingerprintFixedInputs } from "../model/fingerprint";
import type { SequenceRow } from "../model/types";

/** The fixture's Critical-band Todoist row: its plan row id is its name, its
    stable identity is derived from the source id. */
const ROW = "Magic Mirror";
const ROW_IDENTITY = "todoist:6fx00xMIRROR";
/** A second row that must stay untouched by the ROW's membership decision. */
const OTHER = "Press";
const OTHER_IDENTITY = "todoist:6fx00xPRESS";

function loaded(): AppState {
  const sc = makeScenario("ready");
  return reducer(initialState, {
    type: "INPUTS_LOADED",
    inputs: sc.inputs,
    ledger: { ...sc.ledger },
  });
}

/** A valid staged plan carrying a work row, its exact pin, its pending pin,
    and a manual placement for ROW — everything an exclusion has to prune. */
function planned(): AppState {
  const sc = makeScenario("ready");
  const pin: SequenceRow = {
    id: ROW,
    start: "09:45",
    end: "11:15",
    zone: null,
    kind: "work",
  };
  let s = loaded();
  s = reducer(s, { type: "ROW_PLACED", id: OTHER, start: "19:00" });
  s = reducer(s, {
    type: "SEQUENCE_OK",
    sequence: sc.proposal!.sequence,
    warnings: sc.proposal!.warnings,
    fingerprint: fingerprintFixedInputs(fixedInputsOf(sc.inputs)),
    anchoredSourceFingerprint: sc.inputs.anchoredSourceFingerprint,
    pinnedRows: [{ ...pin }],
    pendingPinnedRows: [{ ...pin }],
    ledger: { ...sc.ledger, spent: 1, remaining: 3 },
  });
  return s;
}

function setMembership(s: AppState, identity: string, inPlan: boolean): AppState {
  return reducer(s, { type: "PLAN_MEMBERSHIP_SET", identity, in: inPlan });
}

describe("PLAN_MEMBERSHIP_SET — taking a row out of today's plan", () => {
  it("removes the row from includedItems without touching the source inputs", () => {
    const s = planned();
    const before = includedItems(s).map((i) => i.id);
    expect(before).toContain(ROW);

    const out = setMembership(s, ROW_IDENTITY, false);
    const after = includedItems(out).map((i) => i.id);

    expect(after).not.toContain(ROW);
    expect(after.length).toBe(before.length - 1);
    expect(after).toContain(OTHER);
    // A membership edit is local: the loaded source payload is the same object.
    expect(out.inputs).toBe(s.inputs);
  });

  it("drops the staged work row, its pin, its pending pin, and its placement", () => {
    const s = planned();
    expect(s.sequence!.some((r) => r.id === ROW && r.kind === "work")).toBe(true);
    expect(s.pinnedRows.some((r) => r.id === ROW)).toBe(true);
    expect(s.pendingPinnedRows.some((r) => r.id === ROW)).toBe(true);
    expect(s.placements[OTHER]).toBe("19:00");

    const out = setMembership(s, ROW_IDENTITY, false);

    expect(out.sequence!.some((r) => r.id === ROW && r.kind === "work")).toBe(false);
    expect(out.pinnedRows.some((r) => r.id === ROW)).toBe(false);
    expect(out.pendingPinnedRows.some((r) => r.id === ROW)).toBe(false);
    expect(ROW in out.placements).toBe(false);
    // Nothing else in the plan moves.
    expect(out.placements[OTHER]).toBe("19:00");
    expect(out.sequence!.some((r) => r.id === OTHER)).toBe(true);
  });

  it("keeps the permeable zone backdrop rows", () => {
    const s = planned();
    const zones = s.sequence!.filter((r) => r.kind === "zone").map((r) => r.id);
    expect(zones.length).toBeGreaterThan(0);

    const out = setMembership(s, ROW_IDENTITY, false);
    expect(out.sequence!.filter((r) => r.kind === "zone").map((r) => r.id)).toEqual(zones);
  });

  it("reports the row excluded from the queue and dirty for the staged plan", () => {
    const s = planned();
    expect(queueState(s, ROW)).not.toBe("excluded");
    expect(s.seqPhase).toBe("valid");

    const out = setMembership(s, ROW_IDENTITY, false);

    expect(queueState(out, ROW)).toBe("excluded");
    expect(isIncluded(out, ROW)).toBe(false);
    expect(out.seqPhase).toBe("dirty");
    expect(out.validation).toBeNull();
    expect(out.acceptedDefects).toBeNull();
    // The fingerprint is the source snapshot, not a plan edit: it must survive.
    expect(out.fingerprint).toBe(s.fingerprint);
    expect(out.inputs).toBe(s.inputs);
  });

  it("keys membership by stable identity, never by the name-keyed row id", () => {
    const s = loaded();
    const out = setMembership(s, ROW_IDENTITY, false);

    expect(out.planMembership).toEqual({ [ROW_IDENTITY]: false });
    expect(ROW in out.planMembership).toBe(false);
    expect(membershipKeyOf(s.inputs!.assigned.find((i) => i.id === ROW)!)).toBe(ROW_IDENTITY);
    // A different row — even one sharing the band — keeps its own membership.
    expect(isIncluded(out, OTHER)).toBe(true);
    expect(out.planMembership[OTHER_IDENTITY]).toBeUndefined();
  });

  it("keeps a row out after its row id changes, because the key is identity", () => {
    const s = setMembership(loaded(), ROW_IDENTITY, false);
    const renamed: AppState = {
      ...s,
      inputs: {
        ...s.inputs!,
        assigned: s.inputs!.assigned.map((item) =>
          item.id === ROW ? { ...item, id: "Magic Mirror (renamed)" } : item,
        ),
      },
    };

    expect(isIncluded(renamed, "Magic Mirror (renamed)")).toBe(false);
    expect(queueState(renamed, "Magic Mirror (renamed)")).toBe("excluded");
  });
});

describe("PLAN_MEMBERSHIP_SET — the default stays a no-op", () => {
  it("leaves every row included at the default \"in\"", () => {
    const s = loaded();
    expect(s.membershipDefault).toBe("in");
    expect(s.planMembership).toEqual({});
    expect(includedItems(s)).toHaveLength(s.inputs!.assigned.length);
    expect(queueState(s, ROW)).not.toBe("excluded");
  });

  it("stores nothing and changes nothing when a row is already in the plan", () => {
    const s = planned();
    const out = setMembership(s, ROW_IDENTITY, true);

    expect(out.planMembership).toEqual({});
    expect(includedItems(out).map((i) => i.id)).toEqual(includedItems(s).map((i) => i.id));
    expect(out.sequence).toBe(s.sequence);
    expect(out.seqPhase).toBe(s.seqPhase);
  });

  it("restores a row to the queue without re-staging what it lost", () => {
    const s = planned();
    const out = setMembership(s, ROW_IDENTITY, false);
    const back = setMembership(out, ROW_IDENTITY, true);

    expect(back.planMembership).toEqual({});
    expect(isIncluded(back, ROW)).toBe(true);
    expect(queueState(back, ROW)).toBe("needs-placement");
    expect(back.sequence!.some((r) => r.id === ROW && r.kind === "work")).toBe(false);
    expect(back.pinnedRows.some((r) => r.id === ROW)).toBe(false);
  });

  it("still answers \"out\" for an unmarked row when the day defaults out", () => {
    const s: AppState = { ...loaded(), membershipDefault: "out" };
    expect(includedItems(s)).toHaveLength(0);
    expect(isIncluded(s, ROW)).toBe(false);

    const explicit = setMembership(s, ROW_IDENTITY, true);
    expect(explicit.planMembership).toEqual({ [ROW_IDENTITY]: true });
    expect(isIncluded(explicit, ROW)).toBe(true);
  });
});

describe("isIncluded — the shared inclusion seam", () => {
  it("requires both gates: an override exclusion wins over local membership", () => {
    const s = loaded();
    const excluded = reducer(s, {
      type: "OVERRIDE_SET",
      id: ROW,
      override: { included: false, blocks: null },
    });
    const membershipIn = setMembership(excluded, ROW_IDENTITY, true);

    expect(isIncluded(membershipIn, ROW)).toBe(false);
    expect(queueState(membershipIn, ROW)).toBe("excluded");
    // The override map is untouched by a membership write.
    expect(excluded.overrides[ROW].included).toBe(false);
    expect(membershipIn.overrides[ROW].included).toBe(false);
  });

  it("excludes a row when either gate says out", () => {
    const s = loaded();
    const includeOverride = reducer(s, {
      type: "OVERRIDE_SET",
      id: ROW,
      override: { included: true, blocks: null },
    });
    expect(isIncluded(includeOverride, ROW)).toBe(true);
    expect(isIncluded(setMembership(includeOverride, ROW_IDENTITY, false), ROW)).toBe(false);
  });

  it("falls back to the legacy override answer for a row the queue does not carry", () => {
    const s = loaded();
    expect(isIncluded(s, "not a row")).toBe(true);
    const excluded = reducer(s, {
      type: "OVERRIDE_SET",
      id: "not a row",
      override: { included: false, blocks: null },
    });
    expect(isIncluded(excluded, "not a row")).toBe(false);
  });
});
