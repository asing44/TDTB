import { describe, expect, it } from "vitest";
import {
  initialMintSessionIds,
  mintMinutesForSessionIds,
  mintSessionIdsForMinutes,
  wallFreeMintSessionIds,
} from "./mint";
import type { WallInterval } from "./overflow";
import type { MintSession } from "./types";

const sessions: MintSession[] = [
  { id: "mint:morning:08:30", name: "Mint Morning · 08:30", slot: "Morning", start: "08:30", end: "09:00" },
  { id: "mint:morning:09:00", name: "Mint Morning · 09:00", slot: "Morning", start: "09:00", end: "09:30" },
  { id: "mint:afternoon:13:30", name: "Mint Afternoon · 13:30", slot: "Afternoon", start: "13:30", end: "14:00" },
];

const anchoredSessions: MintSession[] = [
  { id: "mint:morning:08:30", name: "Mint Morning · 08:30", slot: "Morning", start: "08:30", end: "09:00" },
  { id: "mint:morning:09:00", name: "Mint Morning · 09:00", slot: "Morning", start: "09:00", end: "09:30" },
  { id: "mint:afternoon:13:30", name: "Mint Afternoon · 13:30", slot: "Afternoon", start: "13:30", end: "14:00" },
  { id: "mint:afternoon:14:00", name: "Mint Afternoon · 14:00", slot: "Afternoon", start: "14:00", end: "14:30" },
  { id: "mint:afternoon:14:30", name: "Mint Afternoon · 14:30", slot: "Afternoon", start: "14:30", end: "15:00" },
];

describe("Mint session/allotment sync", () => {
  it("maps a total to concrete 30-minute sessions", () => {
    expect(mintSessionIdsForMinutes(sessions, 60)).toEqual([
      "mint:morning:08:30",
      "mint:morning:09:00",
    ]);
    expect(mintMinutesForSessionIds(sessions, ["mint:afternoon:13:30"])).toBe(30);
  });

  it("selects sessions inside the start-anchor allotment window", () => {
    expect(mintSessionIdsForMinutes(anchoredSessions, 60, "13:00")).toEqual([
      "mint:afternoon:13:30",
      "mint:afternoon:14:00",
    ]);
  });

  it("rebuilds stale saved sessions from the current anchor", () => {
    expect(initialMintSessionIds(
      anchoredSessions,
      { on: true, sessions: ["mint:morning:08:30"] },
      60,
      "13:00",
    )).toEqual([
      "mint:afternoon:13:30",
      "mint:afternoon:14:00",
    ]);
  });

  it("keeps an intentional partial selection and ignores stale ids", () => {
    expect(initialMintSessionIds(
      sessions,
      { on: true, sessions: ["mint:afternoon:13:30", "stale"] },
      180,
    )).toEqual(["mint:afternoon:13:30"]);
  });

  it("rebuilds the former all-session default from the saved total", () => {
    expect(initialMintSessionIds(
      sessions,
      { on: true, sessions: sessions.map((session) => session.id) },
      60,
    )).toEqual([
      "mint:morning:08:30",
      "mint:morning:09:00",
    ]);
    expect(initialMintSessionIds(
      sessions,
      { on: true, sessions: sessions.map((session) => session.id) },
      0,
    )).toEqual([]);
  });
});

describe("FEEDBACK-28 wall-aware Mint selection", () => {
  /* The August 17 incident fixture: Mint 15:00-15:30 collides with the OPPD
     fixed wall at 15:00-15:30. The default, edited, and saved choices must
     all exclude the wall-conflicting session BEFORE any judgment payload. */
  const august17Sessions: MintSession[] = [
    { id: "mint:morning:08:30", name: "Mint Morning · 08:30", slot: "Morning", start: "08:30", end: "09:00" },
    { id: "mint:morning:09:00", name: "Mint Morning · 09:00", slot: "Morning", start: "09:00", end: "09:30" },
    { id: "mint:afternoon:13:30", name: "Mint Afternoon · 13:30", slot: "Afternoon", start: "13:30", end: "14:00" },
    { id: "mint:afternoon:14:00", name: "Mint Afternoon · 14:00", slot: "Afternoon", start: "14:00", end: "14:30" },
    { id: "mint:afternoon:14:30", name: "Mint Afternoon · 14:30", slot: "Afternoon", start: "14:30", end: "15:00" },
    { id: "mint:afternoon:15:00", name: "Mint Afternoon · 15:00", slot: "Afternoon", start: "15:00", end: "15:30" },
  ];
  const oppdWall: WallInterval[] = [{ start: 15 * 60, end: 15 * 60 + 30 }];

  it("excludes the August 17 Mint 15:00-15:30 session for the OPPD wall", () => {
    const selected = mintSessionIdsForMinutes(august17Sessions, 120, "13:00", oppdWall);
    expect(selected).not.toContain("mint:afternoon:15:00");
    // Every selected session is wall-free: 15:00 is skipped and 14:30 fills.
    expect(selected).toEqual([
      "mint:afternoon:13:30",
      "mint:afternoon:14:00",
      "mint:afternoon:14:30",
    ]);
  });

  it("default selection skips wall-conflicting sessions without losing the allotment", () => {
    // 120 minutes across 4 candidates; the 15:00 wall row is never picked.
    const selected = mintSessionIdsForMinutes(august17Sessions, 120, "13:00", oppdWall);
    expect(selected).toHaveLength(3);
    expect(selected).not.toContain("mint:afternoon:15:00");
    for (const id of selected) {
      const session = august17Sessions.find((candidate) => candidate.id === id)!;
      const start = Number(session.start.slice(0, 2)) * 60 + Number(session.start.slice(3));
      const end = Number(session.end.slice(0, 2)) * 60 + Number(session.end.slice(3));
      expect(oppdWall.every((w) => end <= w.start || w.end <= start)).toBe(true);
    }
  });

  it("rebuilds a stale saved selection that overlaps a fixed wall", () => {
    expect(initialMintSessionIds(
      august17Sessions,
      { on: true, sessions: ["mint:afternoon:13:30", "mint:afternoon:15:00"] },
      60,
      "13:00",
      oppdWall,
    )).toEqual(["mint:afternoon:13:30"]);
  });

  it("keeps a saved wall-free partial selection untouched", () => {
    expect(initialMintSessionIds(
      august17Sessions,
      { on: true, sessions: ["mint:afternoon:14:00"] },
      30,
      "13:00",
      oppdWall,
    )).toEqual(["mint:afternoon:14:00"]);
  });

  it("wallFreeMintSessionIds drops only the wall-conflicting saved ids", () => {
    expect(wallFreeMintSessionIds(
      august17Sessions,
      ["mint:afternoon:13:30", "mint:afternoon:15:00", "stale"],
      oppdWall,
    )).toEqual(["mint:afternoon:13:30"]);
  });
});
