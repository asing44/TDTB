import { describe, expect, it } from "vitest";
import { makeScenario } from "../fixtures/scenarios";
import { buildExecutionModel, workAllotmentUsage } from "./execution";

describe("T18h execution-first projection", () => {
  it("selects chronological Now and Next while keeping Template zones quiet", () => {
    const sc = makeScenario("sequenced");
    sc.inputs.time.now = "10:50";
    const model = buildExecutionModel({
      inputs: sc.inputs,
      sequence: sc.staged.sequence,
      overlapGrants: [],
      planningConfigFingerprint: sc.inputs.planningConfigFingerprint,
    });

    expect(model.now?.entries.map((entry) => entry.id)).toContain("Magic Mirror");
    expect(model.next?.entries.map((entry) => entry.id)).toContain("Review AWS module 4");
    expect(model.moments.flatMap((moment) => moment.entries).some((entry) => entry.id === "Live")).toBe(false);
    expect(model.zones.map((zone) => zone.name)).toContain("Trinoor");
  });

  it("groups an exact overlap grant into one primary-plus-companion cluster", () => {
    const sc = makeScenario("sequenced");
    const sequence = [
      ...(sc.staged.sequence ?? []),
      { id: "Pairing", start: "11:00", end: "11:30", zone: null, kind: "work" as const },
    ];
    const model = buildExecutionModel({
      inputs: sc.inputs,
      sequence,
      planningConfigFingerprint: sc.inputs.planningConfigFingerprint,
      overlapGrants: [{
        primaryId: "Magic Mirror",
        companionId: "Pairing",
        primaryInterval: { start: "10:45", end: "12:15" },
        companionInterval: { start: "11:00", end: "11:30" },
        reason: "Work alongside the companion block",
        planningConfigFingerprint: sc.inputs.planningConfigFingerprint,
      }],
    });

    const cluster = model.moments.find((moment) => moment.allowedOverlap);
    expect(cluster?.entries.map((entry) => entry.id)).toEqual(["Magic Mirror", "Pairing"]);
    expect(cluster?.overlapReason).toBe("Work alongside the companion block");
  });

  it("computes Work-allotment used/remaining from work inside active zones", () => {
    expect(workAllotmentUsage(
      240,
      [{ id: "Mint", start: "09:00", end: "13:00", zone: "Mint", kind: "zone" }],
      [{ id: "A", start: "09:30", end: "10:30", zone: null, kind: "work" }],
    )).toEqual({ totalMinutes: 240, usedMinutes: 60, remainingMinutes: 180, overMinutes: 0 });
  });
});
