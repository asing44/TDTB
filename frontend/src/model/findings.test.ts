import { describe, expect, it } from "vitest";
import { allowedOverlaps, defectsCovered, workOverlaps } from "./findings";
import type { OverlapGrant, SequenceRow } from "./types";

const row = (id: string, start: string, end: string, kind: "work" | "zone" = "work"): SequenceRow => ({
  id,
  start,
  end,
  zone: null,
  kind,
});

const grant = (
  primaryId: string,
  companionId: string,
  primaryInterval: { start: string; end: string },
  companionInterval: { start: string; end: string },
  reason = "driving errand rides along",
  planningConfigFingerprint = "fp",
): OverlapGrant => ({
  primaryId,
  companionId,
  primaryInterval,
  companionInterval,
  reason,
  planningConfigFingerprint,
});

describe("workOverlaps", () => {
  it("flags overlapping movable work in server quoted-repr format", () => {
    const out = workOverlaps([row("A", "09:00", "10:00"), row("B", "09:30", "10:30")]);
    expect(out).toEqual(["'A' (09:00-10:00) overlaps movable work 'B' (09:30-10:30)"]);
  });

  it("back-to-back rows do not overlap", () => {
    expect(workOverlaps([row("A", "09:00", "10:00"), row("B", "10:00", "10:30")])).toEqual([]);
  });

  it("zone/backdrop rows are permeable — never flagged", () => {
    expect(
      workOverlaps([row("A", "09:00", "10:00"), row("Trinoor", "08:00", "17:00", "zone")]),
    ).toEqual([]);
  });

  it("null sequence and singleton are clean", () => {
    expect(workOverlaps(null)).toEqual([]);
    expect(workOverlaps([row("A", "09:00", "10:00")])).toEqual([]);
  });

  it("ordering is stable regardless of input order", () => {
    const a = workOverlaps([row("B", "09:30", "10:30"), row("A", "09:00", "10:00")]);
    expect(a).toEqual(["'A' (09:00-10:00) overlaps movable work 'B' (09:30-10:30)"]);
  });

  it("triple overlap reports each pair", () => {
    const out = workOverlaps([
      row("A", "09:00", "11:00"),
      row("B", "09:30", "10:30"),
      row("C", "10:00", "12:00"),
    ]);
    expect(out).toHaveLength(3);
  });
});

describe("workOverlaps with grants (T29)", () => {
  const A = row("A", "09:00", "10:00");
  const B = row("B", "09:30", "10:30");
  const g = grant("A", "B", { start: "09:00", end: "10:00" }, { start: "09:30", end: "10:30" });

  it("an exact current grant suppresses the pair's defect", () => {
    expect(workOverlaps([A, B], [g], "fp")).toEqual([]);
  });

  it("grant orientation is irrelevant — reversed primary/companion still covers", () => {
    const rev = grant("B", "A", { start: "09:30", end: "10:30" }, { start: "09:00", end: "10:00" });
    expect(workOverlaps([A, B], [rev], "fp")).toEqual([]);
  });

  it("a stale-fingerprint grant does not suppress (LD38)", () => {
    expect(workOverlaps([A, B], [g], "other-fp")).toHaveLength(1);
    expect(workOverlaps([A, B], [g], null)).toHaveLength(1);
  });

  it("an interval mismatch does not suppress — rows moved since the grant", () => {
    const moved = row("B", "09:45", "10:45");
    expect(workOverlaps([A, moved], [g], "fp")).toHaveLength(1);
  });

  it("only the granted pair is suppressed; a third overlapping row still flags", () => {
    const C = row("C", "09:45", "10:15");
    const out = workOverlaps([A, B, C], [g], "fp");
    expect(out).toHaveLength(2); // A-C and B-C remain defects
    expect(out.every((d) => d.includes("'C'"))).toBe(true);
  });

  it("no grants argument keeps legacy behavior", () => {
    expect(workOverlaps([A, B])).toHaveLength(1);
  });
});

describe("allowedOverlaps (T29)", () => {
  const A = row("A", "09:00", "10:00");
  const B = row("B", "09:30", "10:30");
  const g = grant("A", "B", { start: "09:00", end: "10:00" }, { start: "09:30", end: "10:30" });

  it("emits one informational line per granted overlapping pair, with reason", () => {
    expect(allowedOverlaps([A, B], [g], "fp")).toEqual([
      "Allowed overlap: 'A' (09:00-10:00) with 'B' (09:30-10:30) — driving errand rides along",
    ]);
  });

  it("a granted but non-overlapping pair emits nothing", () => {
    const apart = row("B", "10:00", "10:30");
    const gApart = grant("A", "B", { start: "09:00", end: "10:00" }, { start: "10:00", end: "10:30" });
    expect(allowedOverlaps([A, apart], [gApart], "fp")).toEqual([]);
  });

  it("stale or interval-mismatched grants emit nothing", () => {
    expect(allowedOverlaps([A, B], [g], "other-fp")).toEqual([]);
    expect(allowedOverlaps([A, row("B", "09:45", "10:45")], [g], "fp")).toEqual([]);
  });

  it("null sequence and empty grants are clean", () => {
    expect(allowedOverlaps(null, [g], "fp")).toEqual([]);
    expect(allowedOverlaps([A, B], [], "fp")).toEqual([]);
  });
});

describe("defectsCovered", () => {
  it("empty current list is always covered", () => {
    expect(defectsCovered([], null)).toBe(true);
    expect(defectsCovered([], ["old"])).toBe(true);
  });

  it("unacknowledged defects are not covered", () => {
    expect(defectsCovered(["w1"], null)).toBe(false);
    expect(defectsCovered(["w1", "w2"], ["w1"])).toBe(false);
  });

  it("exact and superset acknowledgment cover; a resolved defect stays covered", () => {
    expect(defectsCovered(["w1"], ["w1"])).toBe(true);
    expect(defectsCovered(["w1"], ["w1", "w2"])).toBe(true); // w2 resolved itself
  });

  it("a NEW defect re-blocks even after acceptance", () => {
    expect(defectsCovered(["w1", "w3"], ["w1", "w2"])).toBe(false);
  });
});
