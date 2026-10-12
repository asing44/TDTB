import { describe, expect, it } from "vitest";
import {
  ANCHOR_SNAP_MIN,
  clampAnchorStart,
  dragTargetMinutes,
  keyboardTargetMinutes,
  snapMinutes,
} from "./anchorDrag";

// A 16h ruler (08:00-24:00) drawn 960px wide: exactly 1px per minute.
const ruler = { dayStart: 480, dayEnd: 1440, laneWidthPx: 960 };

describe("snapMinutes", () => {
  it("rounds to the nearest 15 minutes", () => {
    expect(ANCHOR_SNAP_MIN).toBe(15);
    expect(snapMinutes(1065)).toBe(1065); // 17:45 stays
    expect(snapMinutes(1070)).toBe(1065);
    expect(snapMinutes(1073)).toBe(1080);
    expect(snapMinutes(1072.5)).toBe(1080);
  });
});

describe("clampAnchorStart", () => {
  it("keeps the block inside the ruler, whole block included", () => {
    expect(clampAnchorStart(100, { dayStart: 480, dayEnd: 1440, lengthMin: 60 })).toBe(480);
    expect(clampAnchorStart(1430, { dayStart: 480, dayEnd: 1440, lengthMin: 60 })).toBe(1380);
    expect(clampAnchorStart(900, { dayStart: 480, dayEnd: 1440, lengthMin: 60 })).toBe(900);
  });
  it("never produces a start past 23:45 even for a tiny block", () => {
    expect(clampAnchorStart(2000, { dayStart: 480, dayEnd: 1440, lengthMin: 5 })).toBe(1425);
  });
  it("falls back to the ruler start when the block cannot fit", () => {
    expect(clampAnchorStart(600, { dayStart: 480, dayEnd: 500, lengthMin: 120 })).toBe(480);
  });
});

describe("dragTargetMinutes", () => {
  const base = { ...ruler, startMin: 1065, lengthMin: 30 };
  it("maps pixels to minutes and snaps to 15", () => {
    expect(dragTargetMinutes({ ...base, deltaPx: 30 })).toBe(1095); // +30
    expect(dragTargetMinutes({ ...base, deltaPx: 23 })).toBe(1095); // 1088 -> 1095
    expect(dragTargetMinutes({ ...base, deltaPx: 14 })).toBe(1080); // 1079 -> 1080
    expect(dragTargetMinutes({ ...base, deltaPx: 0 })).toBe(1065);
    expect(dragTargetMinutes({ ...base, deltaPx: -45 })).toBe(1020);
  });
  it("scales with the lane width", () => {
    // 480px lane = 0.5px per minute: 30px is 60 minutes
    expect(dragTargetMinutes({ ...base, laneWidthPx: 480, deltaPx: 30 })).toBe(1125);
  });
  it("clamps to the ruler on both ends", () => {
    expect(dragTargetMinutes({ ...base, deltaPx: -5000 })).toBe(480);
    expect(dragTargetMinutes({ ...base, deltaPx: 5000 })).toBe(1410); // 24:00 - 30
  });
  it("returns the start unchanged when the lane has no measurable width", () => {
    expect(dragTargetMinutes({ ...base, laneWidthPx: 0, deltaPx: 200 })).toBe(1065);
  });
});

describe("keyboardTargetMinutes", () => {
  const opts = { dayStart: 480, dayEnd: 1440, lengthMin: 30 };
  it("moves by 15 minutes, or 60 with shift", () => {
    expect(keyboardTargetMinutes(1065, 1, false, opts)).toBe(1080);
    expect(keyboardTargetMinutes(1065, -1, false, opts)).toBe(1050);
    expect(keyboardTargetMinutes(1065, 1, true, opts)).toBe(1125);
    expect(keyboardTargetMinutes(1065, -1, true, opts)).toBe(1005);
  });
  it("lands an off-grid start on the next grid line instead of skipping it", () => {
    expect(keyboardTargetMinutes(1070, 1, false, opts)).toBe(1080);
    expect(keyboardTargetMinutes(1070, -1, false, opts)).toBe(1065);
  });
  it("clamps at the ruler edges", () => {
    expect(keyboardTargetMinutes(480, -1, false, opts)).toBe(480);
    expect(keyboardTargetMinutes(1410, 1, true, opts)).toBe(1410);
  });
});
