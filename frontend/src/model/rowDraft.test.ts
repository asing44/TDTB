/* rowDraft.test.ts — F2 per-row staged draft.

   Covers the prototype's approval-relevant behavior: the all-day round trip
   (restores duration, never the old pin), the protected-source pin gate, the
   staged diff, and the controller operations a Save projects. */
import { describe, expect, it } from "vitest";
import {
  canApplyPin,
  clearPin,
  createDraft,
  diff,
  draftBlocks,
  isProtectedNative,
  setMode,
  setPin,
  toOverrides,
  type RowDraft,
  type RowFacts,
  type RowSession,
} from "./rowDraft";

function row(overrides: Partial<RowFacts> = {}): RowFacts {
  return {
    id: "Row",
    source: "todoist",
    blocks: 2,
    isRecurring: false,
    nativeStart: null,
    isMintSession: false,
    ...overrides,
  };
}

function session(
  facts: RowFacts = row(),
  overrides: Partial<RowSession> = {},
): RowSession {
  return {
    row: facts,
    override: null,
    pin: null,
    allowSourceTime: false,
    mintOverlap: false,
    ...overrides,
  };
}

describe("createDraft", () => {
  it("seeds a time-block draft from the row and committed session", () => {
    const draft = createDraft(row(), session(row(), { pin: "14:30" }));
    expect(draft).toEqual({
      id: "Row",
      mode: "time",
      blocks: 2,
      blocksBeforeAllDay: null,
      pin: "14:30",
      allowSourceTime: false,
      mintOverlap: false,
    });
  });

  it("starts all-day when the committed override is 0 blocks", () => {
    const draft = createDraft(
      row(),
      session(row(), { override: { included: true, blocks: 0 } }),
    );
    expect(draft.mode).toBe("allday");
    expect(draft.blocks).toBe(0);
    expect(draft.blocksBeforeAllDay).toBeNull();
  });

  it("lets the committed override blocks win over the row blocks", () => {
    const draft = createDraft(
      row({ blocks: 2 }),
      session(row(), { override: { included: true, blocks: 3 } }),
    );
    expect(draft.blocks).toBe(3);
    expect(draft.mode).toBe("time");
  });
});

describe("isProtectedNative", () => {
  it("is true only for a Todoist row with a real HH:MM native time", () => {
    expect(isProtectedNative(row({ nativeStart: "14:30" }))).toBe(true);
    expect(isProtectedNative(row({ nativeStart: null }))).toBe(false);
    expect(isProtectedNative(row({ source: "capacities", nativeStart: "14:30" }))).toBe(
      false,
    );
    expect(isProtectedNative(row({ nativeStart: "2:30 PM" }))).toBe(false);
  });
});

describe("setMode", () => {
  it("captures the duration when switching to all-day", () => {
    const draft = setMode(createDraft(row(), session()), "allday");
    expect(draft.mode).toBe("allday");
    expect(draft.blocksBeforeAllDay).toBe(2);
    expect(draftBlocks(draft)).toBe(0);
  });

  it("clears the pin when switching to all-day", () => {
    const pinned = createDraft(row(), session(row(), { pin: "14:30" }));
    const draft = setMode(pinned, "allday");
    expect(draft.pin).toBeNull();
  });

  it("clears the Mint-overlap and source-time grants for all-day", () => {
    const granted = createDraft(
      row(),
      session(row(), { allowSourceTime: true, mintOverlap: true }),
    );
    const draft = setMode(granted, "allday");
    expect(draft.allowSourceTime).toBe(false);
    expect(draft.mintOverlap).toBe(false);
  });

  it("restores the previous duration and never the old pin", () => {
    const pinned = createDraft(row({ blocks: 4 }), session(row(), { pin: "14:30" }));
    const allDay = setMode(pinned, "allday");
    const back = setMode(allDay, "time");
    expect(back.mode).toBe("time");
    expect(back.blocks).toBe(4);
    expect(back.pin).toBeNull();
  });

  it("restores the duration captured at the all-day switch, not a later one", () => {
    const draft = createDraft(row({ blocks: 2 }), session(row()));
    const allDay = setMode(draft, "allday");
    // Duration is disabled while all-day, so the captured value is authoritative.
    const back = setMode(allDay, "time");
    expect(back.blocks).toBe(2);
  });

  it("is a no-op when the mode is unchanged", () => {
    const draft = createDraft(row(), session());
    expect(setMode(draft, "time")).toBe(draft);
    const allDay = setMode(draft, "allday");
    expect(setMode(allDay, "allday")).toBe(allDay);
  });

  it("keeps the draft blocks while all-day so the return can restore them", () => {
    const draft = setMode(createDraft(row({ blocks: 3 }), session()), "allday");
    expect(draft.blocks).toBe(3);
  });

  it("has no duration to restore for a row that arrived all-day", () => {
    // A source all-day row carries 0 blocks and no prior duration, so the
    // prototype-faithful return keeps 0; nothing is invented here.
    const facts = row({ blocks: 0 });
    const committed = session(facts, { override: { included: true, blocks: 0 } });
    const draft = createDraft(facts, committed);
    expect(draft.blocksBeforeAllDay).toBeNull();
    expect(setMode(draft, "time").blocks).toBe(0);
  });
});

describe("setPin / clearPin / canApplyPin", () => {
  it("allows a pin on an ordinary row", () => {
    const draft = createDraft(row(), session());
    expect(canApplyPin(draft, row())).toBe(true);
    expect(setPin(draft, row(), "09:15").pin).toBe("09:15");
  });

  it("refuses a pin on a protected native row without permission", () => {
    const facts = row({ nativeStart: "14:30" });
    const draft = createDraft(facts, session(facts));
    expect(canApplyPin(draft, facts)).toBe(false);
    expect(setPin(draft, facts, "09:15")).toBe(draft);
  });

  it("applies a pin on a protected native row once source time is allowed", () => {
    const facts = row({ nativeStart: "14:30" });
    const draft = createDraft(facts, session(facts, { allowSourceTime: true }));
    expect(canApplyPin(draft, facts)).toBe(true);
    expect(setPin(draft, facts, "09:15").pin).toBe("09:15");
  });

  it("refuses a pin on an all-day row", () => {
    const draft = setMode(createDraft(row(), session()), "allday");
    expect(setPin(draft, row(), "09:15")).toBe(draft);
  });

  it("refuses a malformed time", () => {
    const draft = createDraft(row(), session());
    expect(setPin(draft, row(), "9:15")).toBe(draft);
    expect(setPin(draft, row(), "25:00")).toBe(draft);
  });

  it("clearPin returns the row to automatic", () => {
    const draft = createDraft(row(), session(row(), { pin: "14:30" }));
    expect(clearPin(draft).pin).toBeNull();
    const automatic: RowDraft = clearPin(draft);
    expect(clearPin(automatic)).toBe(automatic);
  });
});

describe("diff", () => {
  it("reports nothing staged for an untouched draft", () => {
    const facts = row();
    const draft = createDraft(facts, session(facts));
    expect(diff(draft, session(facts))).toEqual({ staged: false, changes: [] });
  });

  it("reports a mode switch with its duration change", () => {
    const facts = row();
    const draft = setMode(createDraft(facts, session(facts)), "allday");
    const result = diff(draft, session(facts));
    expect(result.staged).toBe(true);
    expect(result.changes).toEqual([
      { field: "mode", from: "time", to: "allday" },
      { field: "blocks", from: 2, to: 0 },
    ]);
  });

  it("reports a pin change", () => {
    const facts = row();
    const draft = setPin(createDraft(facts, session(facts)), facts, "09:15");
    expect(diff(draft, session(facts)).changes).toEqual([
      { field: "pin", from: null, to: "09:15" },
    ]);
  });

  it("reports the opt-in changes", () => {
    const facts = row();
    const draft: RowDraft = {
      ...createDraft(facts, session(facts)),
      allowSourceTime: true,
      mintOverlap: true,
    };
    expect(diff(draft, session(facts)).changes).toEqual([
      { field: "allowSourceTime", from: false, to: true },
      { field: "mintOverlap", from: false, to: true },
    ]);
  });

  it("diffs against a committed override's blocks, not the row blocks", () => {
    const facts = row({ blocks: 2 });
    const committed = session(facts, { override: { included: true, blocks: 1 } });
    const draft: RowDraft = { ...createDraft(facts, committed), blocks: 3 };
    expect(diff(draft, committed).changes).toEqual([
      { field: "blocks", from: 1, to: 3 },
    ]);
  });

  it("leaves a committed all-day row unstaged when nothing changes", () => {
    const facts = row({ blocks: 2 });
    const committed = session(facts, { override: { included: true, blocks: 0 } });
    expect(diff(createDraft(facts, committed), committed)).toEqual({
      staged: false,
      changes: [],
    });
  });
});

describe("toOverrides", () => {
  it("projects a pinned time block onto the controller calls", () => {
    const facts = row();
    const draft = setPin(
      createDraft(facts, session(facts, { allowSourceTime: true })),
      facts,
      "09:15",
    );
    expect(toOverrides(draft)).toEqual([
      { type: "setOverride", id: "Row", included: true, blocks: 2 },
      { type: "placeRow", id: "Row", start: "09:15" },
      { type: "setTimeAdjustmentOptIn", id: "Row", allow: true },
      { type: "setMintOverlapOptIn", id: "Row", allow: false },
    ]);
  });

  it("projects an automatic row onto resetPlacement", () => {
    expect(toOverrides(createDraft(row(), session()))).toEqual([
      { type: "setOverride", id: "Row", included: true, blocks: 2 },
      { type: "resetPlacement", id: "Row" },
      { type: "setTimeAdjustmentOptIn", id: "Row", allow: false },
      { type: "setMintOverlapOptIn", id: "Row", allow: false },
    ]);
  });

  it("projects all-day as 0 blocks with no placement", () => {
    const draft = setMode(createDraft(row({ blocks: 3 }), session()), "allday");
    expect(toOverrides(draft)).toEqual([
      { type: "setOverride", id: "Row", included: true, blocks: 0 },
      { type: "resetPlacement", id: "Row" },
      { type: "setTimeAdjustmentOptIn", id: "Row", allow: false },
      { type: "setMintOverlapOptIn", id: "Row", allow: false },
    ]);
  });
});
