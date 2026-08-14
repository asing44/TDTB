/* T26: raw urgency literals and dead due labels were unscannable. */
import { describe, expect, it } from "vitest";
import { dueLabel, normalizeUrgency } from "./urgency";
import type { AssignedItem } from "./types";

function item(over: Partial<AssignedItem>): AssignedItem {
  return {
    id: "X", name: "X", path: null, source: "vault", types: [],
    urgency: null, deadline: null, priorityScore: 0, blocks: 1,
    durationLabel: "30min", todoistId: null,
    ...over,
  };
}

describe("normalizeUrgency", () => {
  it("unwraps the stringified vault list literal", () => {
    expect(normalizeUrgency(item({ urgency: "['4-crit']" }))).toEqual({
      text: "4-crit", tier: "crit",
    });
  });

  it("passes clean vault strings through with a tier", () => {
    expect(normalizeUrgency(item({ urgency: "3-high" }))).toEqual({
      text: "3-high", tier: "high",
    });
  });

  it("maps todoist priority ints to p-levels (4 = p1 highest)", () => {
    expect(normalizeUrgency(item({ source: "todoist", urgency: "4" }))).toEqual({
      text: "p1", tier: "crit",
    });
    expect(normalizeUrgency(item({ source: "todoist", urgency: "1" }))).toEqual({
      text: "p4", tier: "low",
    });
  });

  it("null / 'None' render nothing", () => {
    expect(normalizeUrgency(item({ urgency: null }))).toBeNull();
    expect(normalizeUrgency(item({ urgency: "None" }))).toBeNull();
  });
});

describe("dueLabel", () => {
  const TODAY = "2026-07-24";
  it("overdue shows day count with overdue tone", () => {
    expect(dueLabel("2026-07-23", TODAY)).toEqual({ text: "overdue 1d", tone: "overdue" });
  });
  it("today / tomorrow / near-week are relative", () => {
    expect(dueLabel("2026-07-24", TODAY)).toEqual({ text: "due today", tone: "today" });
    expect(dueLabel("2026-07-25", TODAY)).toEqual({ text: "due tomorrow", tone: "soon" });
    expect(dueLabel("2026-07-28", TODAY)).toEqual({ text: "due in 4d", tone: "soon" });
  });
  it("far dates keep the ISO form, toneless", () => {
    expect(dueLabel("2026-08-30", TODAY)).toEqual({ text: "due 2026-08-30", tone: null });
  });
  it("null deadline renders nothing", () => {
    expect(dueLabel(null, TODAY)).toBeNull();
  });
});
