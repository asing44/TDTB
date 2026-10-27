/* planMembership.test.ts — U1/U3a: LOCAL plan membership.

   Taking a row out of today's plan is a browser-only edit. These tests pin the
   three things that make that safe: the row leaves every inclusion reader at
   once (queue, capacity, staged row, pin, placement), the membership key is the
   STABLE SOURCE IDENTITY rather than the name-keyed row id, and an ABSENT entry
   is resolved by the day itself — the day's rule (only fixed calendar
   commitments, nothing a source sent) on a cold load, or the legacy whole-day
   "in"/"out" a saved blob carries.

   Two shapes of day appear below, because both are real:

   - `coldDay()` is the cold load: nothing curated yet, so the rule alone decides
     each row and every sourced work row waits in Unassigned.
   - `loaded()` is a day already under way (what every fixture depicts): the
     plan was built, so the legacy whole-day "in" default holds and a per-row
     edit is the only thing that can take a row out. */

import { describe, expect, it } from "vitest";
import {
  assignmentRows,
  includedItems,
  initialState,
  isIncluded,
  isPlanMember,
  membershipKeyOf,
  queueState,
  reducer,
  ruleIncludes,
  type AppState,
} from "./store";
import { taskTableSections } from "./allocatorView";
import { makeScenario, fixedInputsOf, plannedDayRestore } from "../fixtures/scenarios";
import { fingerprintFixedInputs } from "../model/fingerprint";
import type { AssignedItem, SequenceRow } from "../model/types";

/** The fixture's Critical-band Todoist row: its plan row id is its name, its
    stable identity is derived from the source id. */
const ROW = "Magic Mirror";
const ROW_IDENTITY = "todoist:6fx00xMIRROR";
/** A second row that must stay untouched by the ROW's membership decision. */
const OTHER = "Press";
const OTHER_IDENTITY = "todoist:6fx00xPRESS";

function coldDay(): AppState {
  const sc = makeScenario("ready");
  return reducer(initialState, {
    type: "INPUTS_LOADED",
    inputs: sc.inputs,
    ledger: { ...sc.ledger },
  });
}

function loaded(): AppState {
  return reducer(coldDay(), plannedDayRestore);
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

/** One row of each kind the day's rule decides on, built from the fixture day so
    the date math is the fixture's own. `todoistId: null` keeps every identity
    distinct (an identity-less row keys by its id). */
function ruleRows(validDate: string): AssignedItem[] {
  const template = makeScenario("ready").inputs.assigned[0];
  const row = (id: string, over: Partial<AssignedItem> = {}): AssignedItem => ({
    ...template,
    id,
    name: id,
    todoistId: null,
    identity: null,
    deadline: null,
    urgency: null,
    ...over,
  });
  return [
    row("Overdue row", { deadline: "2026-07-16" }),
    row("Due today row", { deadline: validDate }),
    row("Timed row", { deadline: null, scheduledStart: "09:30" }),
    row("Recurring timed row", {
      deadline: null,
      isRecurring: true,
      scheduledStart: "07:00",
    }),
    row("Calendar row", { source: "calendar", deadline: null }),
    row("Capacities row", {
      source: "capacities",
      deadline: validDate,
      identity: "capacities:cap-row",
    }),
    row("Undated row", { deadline: null }),
    row("P1 undated row", { urgency: "4", deadline: null }),
  ];
}

/** A COLD U3a day carrying one row per rule case. */
function ruleDay(): AppState {
  const sc = makeScenario("ready");
  return reducer(initialState, {
    type: "INPUTS_LOADED",
    inputs: { ...sc.inputs, assigned: ruleRows(sc.inputs.validDate) },
    ledger: { ...sc.ledger },
  });
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

describe("PLAN_MEMBERSHIP_SET — the legacy whole-day \"in\" default is a no-op", () => {
  it("leaves every row included at the legacy \"in\" default", () => {
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

/* U3a (revised 2026-10-27): on a COLD day an absent membership entry does not
   mean "in". Adam's words: "items still are already showing as pre-selected
   from Todoist instead of allowing me to select what is assigned on today".
   Assigning today is his choice, so a sourced work row waits in Unassigned
   until he adds it — overdue, due today, natively timed, recurring with a
   scheduled start, and every Capacities row alike. Priority is not a reason to
   join either. The ONE row that still starts in the plan is a fixed CALENDAR
   commitment: it is not an assignable choice, so withholding it would
   understate the day's committed time. */
describe("the day's rule leaves sourced work out and keeps calendar commitments in", () => {
  it("keeps only the calendar row in the plan on a cold day", () => {
    const s = ruleDay();
    const [overdue, dueToday, timed, recurringTimed, calendar, capacities, undated, p1Undated] =
      s.inputs!.assigned;

    expect(s.membershipDefault).toBe("rule");
    expect(s.planMembership).toEqual({});
    expect(includedItems(s).map((i) => i.id)).toEqual(["Calendar row"]);

    // Nothing a source sent is pre-selected: not even a due-today row.
    for (const sourced of [
      overdue,
      dueToday,
      timed,
      recurringTimed,
      capacities,
      undated,
      p1Undated,
    ]) {
      expect(ruleIncludes(sourced)).toBe(false);
      expect(isPlanMember(s, sourced)).toBe(false);
    }
    expect(ruleIncludes(calendar)).toBe(true);
    expect(isPlanMember(s, calendar)).toBe(true);

    // Out of the plan, and not an error: each waits in Unassigned.
    expect(isIncluded(s, "Due today row")).toBe(false);
    expect(isIncluded(s, "Undated row")).toBe(false);
  });

  it("stores an entry whenever the toggle disagrees with the rule", () => {
    const s = ruleDay();
    const [overdue, , , , calendar] = s.inputs!.assigned;

    // Echoing the rule's own answer is a no-op in both directions: the sparse
    // map must not dirty the day because a surface re-asserted the default.
    expect(setMembership(s, membershipKeyOf(overdue), false)).toBe(s);
    expect(setMembership(s, membershipKeyOf(calendar), true)).toBe(s);

    // Adding a sourced row is the day's normal direction now.
    const added = setMembership(s, membershipKeyOf(overdue), true);
    expect(added.planMembership).toEqual({ [membershipKeyOf(overdue)]: true });
    expect(isIncluded(added, overdue.id)).toBe(true);

    // And the one pre-selected row can still be taken out.
    const removed = setMembership(s, membershipKeyOf(calendar), false);
    expect(removed.planMembership).toEqual({ [membershipKeyOf(calendar)]: false });
    expect(isIncluded(removed, calendar.id)).toBe(false);
  });

  it("a legacy whole-day \"in\"/\"out\" default overrides the rule", () => {
    const s = ruleDay();
    expect(includedItems({ ...s, membershipDefault: "in" })).toHaveLength(8);
    expect(includedItems({ ...s, membershipDefault: "out" })).toHaveLength(0);
  });
});

/* U3a's other half: a row the day does not hold is not in a band. It renders in
   the Unassigned section instead of being dumped into "Out today" (that lane
   keeps only the rows an override hides for today). */
describe("a row outside today's plan leaves every lane", () => {
  const laneIds = (s: AppState): string[] =>
    taskTableSections(s).flatMap((section) => section.rows.map((r) => r.item.id));

  it("hides a row taken out of the plan and surfaces it in assignmentRows.unassigned", () => {
    const s = planned();
    expect(laneIds(s)).toContain(ROW);

    const out = setMembership(s, ROW_IDENTITY, false);

    expect(laneIds(out)).not.toContain(ROW);
    // Everything else keeps its place, including the "Out today" lane's own
    // membership: a plan edit is not an override.
    expect(laneIds(out)).toContain(OTHER);
    expect(taskTableSections(out).find((section) => section.key === "out")!.rows).toEqual([]);

    const rows = assignmentRows(out);
    expect(rows.assigned.map((r) => r.id)).not.toContain(ROW);
    const held = rows.unassigned.filter((r) => r.identity === ROW_IDENTITY);
    expect(held).toHaveLength(1);
    expect(held[0]).toMatchObject({
      id: ROW,
      identity: ROW_IDENTITY,
      name: ROW,
      source: "todoist",
      assigned: false,
    });
    // Local only: the source still carries the row and nothing was dropped.
    expect(out.inputs!.assigned.map((i) => i.id)).toContain(ROW);
    expect(out.inputs!.droppedToday).toEqual([]);
  });

  it("hides every unruled row of a cold day from the lanes", () => {
    const s = ruleDay();
    // The fixed calendar commitment is the whole plan; every sourced work row
    // waits in the drawer for him to add it.
    expect(laneIds(s)).toEqual(["Calendar row"]);
    expect(assignmentRows(s).assigned.map((r) => r.name)).toEqual(["Calendar row"]);
    expect(assignmentRows(s).unassigned.map((r) => r.name)).toEqual([
      "Overdue row",
      "Due today row",
      "Timed row",
      "Recurring timed row",
      "Capacities row",
      "Undated row",
      "P1 undated row",
    ]);
  });
});
