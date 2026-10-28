import { describe, expect, it } from "vitest";
import {
  ANCHOR_SNAP_MIN,
  clampAnchorStart,
  clampDuration,
  dragTargetMinutes,
  keyboardResizeMinutes,
  keyboardTargetMinutes,
  resizeTargetMinutes,
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

describe("duration resize helpers", () => {
  const base = { startMin: 600, dayStart: 450, dayEnd: 1425, laneWidthPx: 975 }; // 1px/min

  it("snaps a drag to the 15-minute grid and floors at one step", () => {
    expect(resizeTargetMinutes({ ...base, durationMin: 30, deltaPx: 22 })).toBe(45);
    expect(resizeTargetMinutes({ ...base, durationMin: 30, deltaPx: 37 })).toBe(60);
    expect(resizeTargetMinutes({ ...base, durationMin: 60, deltaPx: -500 })).toBe(15);
  });

  it("honours a coarser step, a ceiling, and the ruler end", () => {
    const blocks = { ...base, stepMin: 30, maxMin: 360 };
    expect(resizeTargetMinutes({ ...blocks, durationMin: 30, deltaPx: 20 })).toBe(60);
    expect(resizeTargetMinutes({ ...blocks, durationMin: 30, deltaPx: -400 })).toBe(30);
    expect(resizeTargetMinutes({ ...blocks, durationMin: 30, deltaPx: 900 })).toBe(360);
    expect(clampDuration(999, { startMin: 1380, dayEnd: 1425 })).toBe(45);
  });

  it("returns the current length when the lane has no width", () => {
    expect(resizeTargetMinutes({ ...base, laneWidthPx: 0, durationMin: 50, deltaPx: 80 })).toBe(50);
  });

  it("never floors a shrink above the subject's own length", () => {
    const blocks = { startMin: 600, dayEnd: 1425, stepMin: 30 };
    // A 15-minute event on the strips' 30-minute block grid: shrinking must
    // stay at 15 instead of snapping up a step and lengthening the event.
    expect(keyboardResizeMinutes(15, -1, false, blocks)).toBe(15);
    expect(resizeTargetMinutes({ ...base, stepMin: 30, durationMin: 15, deltaPx: -20 })).toBe(15);
    // The floor is the smaller of one step and the current length.
    expect(clampDuration(0, { ...blocks, currentMin: 15 })).toBe(15);
    expect(clampDuration(0, { ...blocks, currentMin: 5 })).toBe(5);
    // Growing is unchanged: one 30-minute step up from 15 is 30.
    expect(keyboardResizeMinutes(15, 1, false, blocks)).toBe(30);
  });

  it("steps by one grid line, an hour with Shift, and lands off-grid lengths on the grid", () => {
    const b = { startMin: 600, dayEnd: 1425 };
    expect(keyboardResizeMinutes(30, 1, false, b)).toBe(45);
    expect(keyboardResizeMinutes(30, -1, false, b)).toBe(15);
    expect(keyboardResizeMinutes(15, -1, false, b)).toBe(15);
    expect(keyboardResizeMinutes(30, 1, true, b)).toBe(90);
    expect(keyboardResizeMinutes(50, 1, false, { ...b, stepMin: 30 })).toBe(60);
    expect(keyboardResizeMinutes(50, -1, false, { ...b, stepMin: 30 })).toBe(30);
  });
});
