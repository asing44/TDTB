/* Pure math for moving an anchor along the Day strip ruler. The strip draws
   anchors by start time over [dayStart, dayEnd) minutes; a drag converts a
   pixel delta to minutes, snaps to 15, and keeps the whole block on the ruler. */

export const ANCHOR_SNAP_MIN = 15;

export function snapMinutes(minutes: number, step = ANCHOR_SNAP_MIN): number {
  return Math.round(minutes / step) * step;
}

interface RulerBounds {
  dayStart: number;
  dayEnd: number;
  /** Drawn length of the block, so its end also stays on the ruler. */
  lengthMin: number;
}

export function clampAnchorStart(minutes: number, bounds: RulerBounds): number {
  const latest = Math.floor((bounds.dayEnd - bounds.lengthMin) / ANCHOR_SNAP_MIN) * ANCHOR_SNAP_MIN;
  const max = Math.max(bounds.dayStart, latest);
  return Math.min(max, Math.max(bounds.dayStart, minutes));
}

export function dragTargetMinutes(args: RulerBounds & {
  startMin: number;
  deltaPx: number;
  laneWidthPx: number;
}): number {
  const span = args.dayEnd - args.dayStart;
  if (!(args.laneWidthPx > 0) || span <= 0) return args.startMin;
  const raw = args.startMin + (args.deltaPx / args.laneWidthPx) * span;
  return clampAnchorStart(snapMinutes(raw), args);
}

/** Arrow-key move: one 15-minute grid line, or exactly an hour with Shift.
    A start that sits off the grid first lands on the adjacent grid line. */
export function keyboardTargetMinutes(
  startMin: number,
  direction: 1 | -1,
  shift: boolean,
  bounds: RulerBounds,
): number {
  const raw = shift
    ? snapMinutes(startMin + direction * 60)
    : direction > 0
      ? (Math.floor(startMin / ANCHOR_SNAP_MIN) + 1) * ANCHOR_SNAP_MIN
      : (Math.ceil(startMin / ANCHOR_SNAP_MIN) - 1) * ANCHOR_SNAP_MIN;
  return clampAnchorStart(raw, bounds);
}

/** Duration resize of a calendar event's trailing edge. The start stays put;
    the length snaps to `stepMin` (15 by default, 30 where the commit path only
    counts whole blocks), never drops below one step, and never runs past the
    ruler end or `maxMin`. */
interface ResizeBounds {
  startMin: number;
  dayEnd: number;
  stepMin?: number;
  maxMin?: number;
}

export function clampDuration(minutes: number, bounds: ResizeBounds): number {
  const step = bounds.stepMin ?? ANCHOR_SNAP_MIN;
  const room = Math.floor((bounds.dayEnd - bounds.startMin) / step) * step;
  const max = Math.max(step, Math.min(room, bounds.maxMin ?? Number.POSITIVE_INFINITY));
  return Math.min(max, Math.max(step, minutes));
}

export function resizeTargetMinutes(
  args: ResizeBounds & { durationMin: number; deltaPx: number; laneWidthPx: number; dayStart: number },
): number {
  const span = args.dayEnd - args.dayStart;
  if (!(args.laneWidthPx > 0) || span <= 0) return clampDuration(args.durationMin, args);
  const raw = args.durationMin + (args.deltaPx / args.laneWidthPx) * span;
  return clampDuration(snapMinutes(raw, args.stepMin), args);
}

/** Arrow-key resize: one step, or an hour with Shift. A length off the grid
    first lands on the adjacent grid line. */
export function keyboardResizeMinutes(
  durationMin: number,
  direction: 1 | -1,
  shift: boolean,
  bounds: ResizeBounds,
): number {
  const step = bounds.stepMin ?? ANCHOR_SNAP_MIN;
  const raw = shift
    ? snapMinutes(durationMin + direction * 60, step)
    : direction > 0
      ? (Math.floor(durationMin / step) + 1) * step
      : (Math.ceil(durationMin / step) - 1) * step;
  return clampDuration(raw, bounds);
}
