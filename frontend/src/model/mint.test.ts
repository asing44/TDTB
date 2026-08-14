import { describe, expect, it } from "vitest";
import {
  initialMintSessionIds,
  mintMinutesForSessionIds,
  mintSessionIdsForMinutes,
} from "./mint";
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
