import { describe, expect, it } from "vitest";
import {
  allowedOverlaps,
  defectsCovered,
  MINT_OVERLAP_REASON,
  mintOverlapNotes,
  overlapNotes,
  workOverlaps,
} from "./findings";
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

describe("overlapNotes — per-row grant note (04-overlap-note)", () => {
  const A = row("A", "09:00", "10:00");
  const B = row("B", "09:30", "10:30");
  const g = grant("A", "B", { start: "09:00", end: "10:00" }, { start: "09:30", end: "10:30" });

  it("names each row's partner and the grant reason, one note per row", () => {
    expect(overlapNotes([A, B], [g], "fp")).toEqual([
      { rowId: "A", partnerId: "B", reason: "driving errand rides along" },
      { rowId: "B", partnerId: "A", reason: "driving errand rides along" },
    ]);
  });

  it("a time collision with NO grant produces no note", () => {
    expect(overlapNotes([A, B], [], "fp")).toEqual([]);
    expect(overlapNotes([A, B])).toEqual([]);
  });

  it("abutting rows produce no note even when a grant exists", () => {
    const apart = row("B", "10:00", "10:30");
    const gApart = grant("A", "B", { start: "09:00", end: "10:00" }, { start: "10:00", end: "10:30" });
    expect(overlapNotes([A, apart], [gApart], "fp")).toEqual([]);
  });

  it("stale fingerprint or a moved row drops the note (exact-grant discipline)", () => {
    expect(overlapNotes([A, B], [g], "other-fp")).toEqual([]);
    expect(overlapNotes([A, B], [g], null)).toEqual([]);
    expect(overlapNotes([A, row("B", "09:45", "10:45")], [g], "fp")).toEqual([]);
  });

  it("multiple grants on one row each render", () => {
    const C = row("C", "09:15", "09:45");
    const g2 = grant("A", "C", { start: "09:00", end: "10:00" }, { start: "09:15", end: "09:45" }, "paired with C");
    const notes = overlapNotes([A, B, C], [g, g2], "fp");
    expect(notes.filter((n) => n.rowId === "A").map((n) => n.partnerId)).toEqual(["C", "B"]);
    expect(notes.filter((n) => n.rowId === "A").map((n) => n.reason)).toEqual([
      "paired with C",
      "driving errand rides along",
    ]);
  });

  it("a grant naming a row that is not on the plan is skipped, not rendered", () => {
    const ghost = grant("A", "ghost", { start: "09:00", end: "10:00" }, { start: "09:30", end: "10:30" }, "ghost grant");
    expect(() => overlapNotes([A], [ghost], "fp")).not.toThrow();
    expect(overlapNotes([A], [ghost], "fp")).toEqual([]);
  });

  it("zone/backdrop rows never carry a note", () => {
    const zone = row("Trinoor", "08:00", "17:00", "zone");
    const gZone = grant("A", "Trinoor", { start: "09:00", end: "10:00" }, { start: "08:00", end: "17:00" }, "zone grant");
    expect(overlapNotes([A, zone], [gZone], "fp")).toEqual([]);
  });
});

describe("mintOverlapNotes — operator grant note (06-may-overlap-mint-toggle)", () => {
  const mintRow = (
    id: string,
    start: string,
    end: string,
    viaMarker = false,
  ): SequenceRow => ({
    id,
    start,
    end,
    zone: null,
    kind: "work",
    ...(viaMarker ? { wire: { mint_session: true } } : {}),
  });
  const WORK = row("Press", "15:15", "16:15");
  const MINT = mintRow("Mint Afternoon", "15:00", "17:00");
  const reason = MINT_OVERLAP_REASON;

  it("(i) a granted row overlapping Mint yields one note per Mint row", () => {
    expect(mintOverlapNotes([WORK, MINT], { Press: true })).toEqual([
      { rowId: "Press", partnerId: "Mint Afternoon", reason },
    ]);
  });

  it("(i) also recognizes a Mint row by the wire marker, not just the name", () => {
    const marker = mintRow("Session Alpha", "15:00", "17:00", true);
    expect(mintOverlapNotes([WORK, marker], { Press: true })).toEqual([
      { rowId: "Press", partnerId: "Session Alpha", reason },
    ]);
  });

  it("(ii) a granted row that does not overlap Mint yields nothing", () => {
    const away = mintRow("Mint Morning", "13:00", "14:00");
    expect(mintOverlapNotes([WORK, away], { Press: true })).toEqual([]);
  });

  it("(iii) a touching boundary is abutment, not overlap", () => {
    const press = row("Press", "14:00", "15:00");
    expect(mintOverlapNotes([press, MINT], { Press: true })).toEqual([]);
  });

  it("(iv) an ungranted row overlapping Mint yields nothing", () => {
    expect(mintOverlapNotes([WORK, MINT], {})).toEqual([]);
    expect(mintOverlapNotes([WORK, MINT])).toEqual([]);
  });

  it("(v) a Mint row is never a note subject, even when itself granted", () => {
    const later = mintRow("Mint Late", "15:30", "16:00");
    expect(mintOverlapNotes([MINT, later], { "Mint Afternoon": true })).toEqual([]);
  });

  it("(vi) multiple Mint overlaps yield one note each", () => {
    const late = mintRow("Mint Late", "16:00", "17:30");
    expect(mintOverlapNotes([WORK, MINT, late], { Press: true })).toEqual([
      { rowId: "Press", partnerId: "Mint Afternoon", reason },
      { rowId: "Press", partnerId: "Mint Late", reason },
    ]);
  });

  it("null sequence and a zone row contribute nothing", () => {
    expect(mintOverlapNotes(null, { Press: true })).toEqual([]);
    const zone = row("Trinoor", "08:00", "17:00", "zone");
    expect(mintOverlapNotes([zone, MINT], { Trinoor: true })).toEqual([]);
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
