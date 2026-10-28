/* batchEligibility.test.ts — F2 batch eligibility.

   Each production decision gets its own exclusion reason, and the mixed
   selection proves the "N of M eligible" count and the excluded list. */
import { describe, expect, it } from "vitest";
import {
  BATCH_ACTIONS,
  EXCLUSION_REASONS,
  NON_BATCHABLE_ACTIONS,
  batchEligibility,
  isBatchableAction,
  unassignVerb,
  type BatchContext,
  type BatchRow,
} from "./batchEligibility";

function row(overrides: Partial<BatchRow> = {}): BatchRow {
  return {
    id: "r1",
    name: "Row one",
    source: "todoist",
    state: "planned",
    blocks: 2,
    isRecurring: false,
    nativeStart: null,
    isMintSession: false,
    pin: "10:00",
    ...overrides,
  };
}

const CONFIRMED: BatchContext = { daySetupConfirmed: true };
const UNCONFIRMED: BatchContext = { daySetupConfirmed: false };

function reasons(action: Parameters<typeof batchEligibility>[0], rows: BatchRow[]) {
  return batchEligibility(action, rows, CONFIRMED).excluded.map((e) => e.reason);
}

describe("batchEligibility source actions", () => {
  it("keeps Complete and Hide always open to every row", () => {
    const rows = [
      row({ id: "a" }),
      row({ id: "b", source: "capacities" }),
      row({ id: "c", blocks: 0 }),
    ];
    for (const action of ["complete", "hideAlways"] as const) {
      const result = batchEligibility(action, rows, UNCONFIRMED);
      expect(result.eligible).toEqual(["a", "b", "c"]);
      expect(result.excluded).toEqual([]);
    }
  });

  it("excludes Capacities rows from Unassign with a written reason", () => {
    const result = batchEligibility(
      "unassign",
      [row({ id: "cap", source: "capacities", name: "Backpack" })],
      CONFIRMED,
    );
    expect(result.eligible).toEqual([]);
    expect(result.excluded).toEqual([
      {
        id: "cap",
        name: "Backpack",
        reason: EXCLUSION_REASONS.unassignCapacities,
      },
    ]);
  });

  it("keeps recurring rows eligible for Unassign, as Skip occurrence", () => {
    const recurring = row({ id: "rec", isRecurring: true, name: "Water plants" });
    const ordinary = row({ id: "one" });
    expect(batchEligibility("unassign", [recurring, ordinary], CONFIRMED).eligible).toEqual(
      ["rec", "one"],
    );
    // The wire verb is always `unassign`; the server owns the recurring
    // advance, and "Skip occurrence" stays only as the UI label.
    expect(unassignVerb(recurring)).toBe("unassign");
    expect(unassignVerb(ordinary)).toBe("unassign");
  });
});

describe("batchEligibility Skip today", () => {
  it("refuses until Day Setup is confirmed, with the written reason", () => {
    const result = batchEligibility("skipToday", [row()], UNCONFIRMED);
    expect(result.excluded).toEqual([
      { id: "r1", name: "Row one", reason: "Confirm Day Setup first" },
    ]);
  });

  it("allows planned rows once Day Setup is confirmed", () => {
    expect(batchEligibility("skipToday", [row()], CONFIRMED).eligible).toEqual(["r1"]);
  });

  it("excludes a row already skipped today", () => {
    expect(reasons("skipToday", [row({ state: "skipped" })])).toEqual([
      EXCLUSION_REASONS.skipAlreadySkipped,
    ]);
  });
});

describe("batchEligibility scheduling actions", () => {
  it("excludes all-day rows from Set duration", () => {
    expect(reasons("duration", [row({ blocks: 0 })])).toEqual([
      EXCLUSION_REASONS.allDayNoDuration,
    ]);
    expect(batchEligibility("duration", [row()], CONFIRMED).eligible).toEqual(["r1"]);
  });

  it("excludes all-day rows and protected times from Automatic start", () => {
    expect(
      reasons("automaticPlacement", [
        row({ id: "ad", blocks: 0 }),
        row({ id: "prot", nativeStart: "14:30" }),
        row({ id: "auto", pin: null }),
        row({ id: "pinned" }),
      ]),
    ).toEqual([
      EXCLUSION_REASONS.allDayNoStart,
      EXCLUSION_REASONS.protectedAutomatic,
      EXCLUSION_REASONS.alreadyAutomatic,
    ]);
  });

  it("allows a Capacities row to go All-day; excludes protected and all-day rows", () => {
    expect(
      reasons("allDay", [
        row({ id: "cap", source: "capacities" }),
        row({ id: "prot", nativeStart: "14:30" }),
        row({ id: "ad", blocks: 0 }),
      ]),
    ).toEqual([
      EXCLUSION_REASONS.protectedAllDay,
      EXCLUSION_REASONS.alreadyAllDay,
    ]);
  });

  it("allows an ordinary time block and a Capacities row to go All-day, not an already all-day row", () => {
    const rows = [
      row({ id: "plain" }),
      row({ id: "cap", source: "capacities" }),
      row({ id: "capAD", source: "capacities", blocks: 0 }),
    ];
    const result = batchEligibility("allDay", rows, CONFIRMED);
    expect(result.eligible).toEqual(["plain", "cap"]);
    expect(result.excluded.map((e) => e.reason)).toEqual([
      EXCLUSION_REASONS.alreadyAllDay,
    ]);
  });

  it("offers Time block only to all-day rows", () => {
    expect(
      reasons("timeBlock", [row({ id: "time" }), row({ id: "ad", blocks: 0 })]),
    ).toEqual([EXCLUSION_REASONS.alreadyTimeBlock]);
    expect(
      batchEligibility(
        "timeBlock",
        [row({ id: "capAD", source: "capacities", blocks: 0 })],
        CONFIRMED,
      ).eligible,
    ).toEqual(["capAD"]);
  });
});

describe("batchEligibility Mint overlap", () => {
  it("disables the toggle for all-day, Mint-session, and skipped rows", () => {
    for (const action of ["mintAllow", "mintDisallow"] as const) {
      expect(
        reasons(action, [
          row({ id: "ad", blocks: 0 }),
          row({ id: "mint", isMintSession: true }),
          row({ id: "skip", state: "skipped" }),
          row({ id: "plain" }),
        ]),
      ).toEqual([
        EXCLUSION_REASONS.mintAllDay,
        EXCLUSION_REASONS.mintSession,
        EXCLUSION_REASONS.mintSkipped,
      ]);
    }
  });

  it("allows an ordinary planned row", () => {
    expect(batchEligibility("mintAllow", [row()], CONFIRMED).eligible).toEqual(["r1"]);
  });
});

describe("batchEligibility counts and action set", () => {
  it("reports N of M with the excluded names and reasons", () => {
    const rows = [
      row({ id: "a", name: "First" }),
      row({ id: "b", name: "Backpack", source: "capacities" }),
      row({ id: "c", name: "Pick up prescription", nativeStart: "14:30" }),
      row({ id: "d", name: "All day", blocks: 0 }),
      row({ id: "e", name: "Last" }),
    ];
    const result = batchEligibility("allDay", rows, CONFIRMED);
    expect(result.eligible).toEqual(["a", "b", "e"]);
    expect(result.excluded.map((e) => e.name)).toEqual([
      "Pick up prescription",
      "All day",
    ]);
    expect(result.counts).toEqual({ eligible: 3, excluded: 2, total: 5 });
    expect(result.label).toBe("3 of 5 eligible");
  });

  it("never batches Delete or an exact start", () => {
    expect(NON_BATCHABLE_ACTIONS).toEqual(["delete", "exactStart"]);
    for (const action of NON_BATCHABLE_ACTIONS) {
      expect(isBatchableAction(action)).toBe(false);
    }
    for (const action of BATCH_ACTIONS) {
      expect(isBatchableAction(action)).toBe(true);
    }
  });
});
