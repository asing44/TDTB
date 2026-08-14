/* overflow.ts — deterministic placement for rows the sequencer had no room
   for (2026-07-27, Adam).

   On an overbooked day the sequencer drops what does not fit and the review
   reads "no slot found — it stays assigned for today" for every one of them.
   That is honest but useless: the rows are still work Adam intends to do, and
   a row with no time never reaches Todoist, the vault, or the calendar, so it
   never reaches BusyCal either.

   So a dropped row is laid out from the day-frame anchor instead, back to
   back, each keeping its own duration. These OVERLAP the day's existing
   commitments by construction — that is the point, the day is genuinely
   overbooked and this is what being 6 blocks over actually looks like. They do
   NOT overlap each other, because a pile of rows sharing one minute is both
   unreadable and the exact shape that hard-blocks the next Send
   (see model/pins.ts).

   These are ordinary work rows: they route to Todoist / vault / calendar by
   source through the normal manifest, and they really are written. Adam's
   call — everything shows up in BusyCal anyway, so a placement that is not
   written is a placement that does not exist. `validate_sequence` will flag
   the collisions as soft never-bump warnings, which is the correct reading.

   FEEDBACK-02 (2026-08-13): overflow was wall-blind — a dropped row was laid
   out straight through an imported calendar event (screenshot: Magic Mirror
   over Cooking at 20:30), and the server now hard-rejects exactly that shape.
   Overflow skips non-permeable calendar walls (`calendarWalls`) so what is
   staged here is what validation accepts. Config hard/window blocks stay on
   the LD26 acceptable-defect path: overflow may overlap them and the user
   accepts the defect, exactly as before.

   FEEDBACK-03 (2026-08-14): wall-blind overflow is gone. `planOverflow`
   scans free gaps around non-permeable calendar walls AND immutable pinned
   rows (`occupied` — the server's effective pin set), and a dropped row no
   gap can hold is reported explicitly (`infeasible`: the row, its need, and
   the free capacity) instead of silently omitted. The production path never
   stages a row over a wall or a pin, and never drops one without saying so. */

import { addMinutes, toMinutes } from "./time";
import type { AnchoredBlock, AssignedItem, SequenceRow } from "./types";

/** Minutes in a block. Mirrors the 30-min grid used everywhere else. */
const BLOCK_MIN = 30;
const END_OF_DAY = 24 * 60;

/** A blocked minute interval [start, end) overflow rows must avoid. */
export interface WallInterval {
  start: number;
  end: number;
}

/** One dropped row the free-gap scan could not place (FEEDBACK-03). */
export interface OverflowInfeasible {
  id: string;
  /** Requested duration in 30-min blocks — what could not be placed. */
  blocks: number;
  /** Largest contiguous free gap in blocks after walls, pins, and rows the
      scan already placed — the placement-relevant available capacity. */
  freeBlocks: number;
  /** Actionable human text: the gap, the need, and what blocked it. */
  reason: string;
}

/** FEEDBACK-03: the full outcome of a free-gap overflow scan. */
export interface OverflowPlan {
  rows: SequenceRow[];
  infeasible: OverflowInfeasible[];
}

/** Merge overlapping/adjacent intervals so free-gap math sees a clean union.
    Input order is arbitrary; output is sorted by start. */
function mergeIntervals(intervals: WallInterval[]): WallInterval[] {
  const sorted = intervals
    .filter((i) => i.end > i.start)
    .sort((a, b) => a.start - b.start || a.end - b.end);
  const out: WallInterval[] = [];
  for (const i of sorted) {
    const last = out[out.length - 1];
    if (last && i.start < last.end) {
      last.end = Math.max(last.end, i.end);
    } else {
      out.push({ start: i.start, end: i.end });
    }
  }
  return out;
}

/** Largest contiguous free interval in [from, END_OF_DAY) after `blocked`
    (walls + pins + already-placed rows), or null when nothing is free. */
function largestFreeGap(
  from: number,
  blocked: WallInterval[],
): { start: number; end: number } | null {
  const merged = mergeIntervals(blocked);
  let cursor = from;
  let best: { start: number; end: number } | null = null;
  for (const b of merged) {
    if (b.end <= cursor) continue;
    if (b.start > cursor) {
      const gap = { start: cursor, end: b.start };
      if (!best || gap.end - gap.start > best.end - best.start) best = gap;
    }
    cursor = Math.max(cursor, b.end);
  }
  if (cursor < END_OF_DAY) {
    const gap = { start: cursor, end: END_OF_DAY };
    if (!best || gap.end - gap.start > best.end - best.start) best = gap;
  }
  return best;
}

/** Non-permeable calendar walls from the anchored read model — the imported
    fixed/work events overflow placement must not cross. Ignored AND
    quarantined calendars wall nothing (contract 17 excludes both from
    planning); dismissed (skipToday) or off rows free their interval; config
    hard/window blocks stay on the acceptable-defect path and are
    intentionally not included here (the server treats them as soft too). */
export function calendarWalls(anchored: AnchoredBlock[]): WallInterval[] {
  const out: WallInterval[] = [];
  for (const a of anchored) {
    if (a.kind !== "calendar") continue;
    if (a.overlapAllowed) continue;
    if (a.capacityClass === "ignored" || a.capacityClass === "quarantined") {
      continue;
    }
    if (!a.on || a.skipToday) continue;
    if (!a.start) continue;
    const start = toMinutes(a.start);
    const end = a.end ? toMinutes(a.end) : start + a.durationMin;
    if (end > start) out.push({ start, end });
  }
  return out.sort((x, y) => x.start - y.start || x.end - y.end);
}

/** Selected Mint session intervals as hard walls for overflow placement
    (FEEDBACK-25). Mint rows are the server's immutable fixed rows — the
    interval they occupy is protected time: the server hard-rejects any
    assigned row overlapping one, so the free-gap scan must not stage a
    dropped row over it either. Identified by the server's mint_session wire
    marker or the canonical "Mint …" id prefix emitted for session rows; the
    legacy aggregate "Minting" row (model-movable, no session window) is not
    a wall. */
export function mintWalls(rows: SequenceRow[]): WallInterval[] {
  const out: WallInterval[] = [];
  for (const r of rows) {
    if (!(r.wire?.mint_session === true || /^Mint /i.test(r.id))) continue;
    const start = toMinutes(r.start);
    const end = toMinutes(r.end);
    if (end > start) out.push({ start, end });
  }
  return out.sort((x, y) => x.start - y.start || x.end - y.end);
}

/** Lay dropped rows out sequentially from `anchor`, preserving each row's
    duration. Caller order is preserved — it arrives importance-ordered, so
    the most important work gets the earliest slot.

    FEEDBACK-03: placement scans free gaps. Rows are emitted until the day
    runs out; a row that would start at or past midnight is NOT placed rather
    than wrapped, because a wrapped time is a wrong time and would publish as
    one. `walls` (non-permeable calendar + selected Mint intervals, default
    none) and `occupied` (immutable pinned-row intervals, default none) are
    skipped — a row that would intersect one moves to just after it. Every
    row that could not be placed is reported in `infeasible` with its need
    and the remaining free capacity; nothing is silently dropped. Rows never
    overlap EACH OTHER, a wall, or a pin; they may still overlap movable work
    and config anchored blocks — that is what an overbooked day looks like. */
export function planOverflow(
  dropped: AssignedItem[],
  anchor: string,
  blocksOf: (item: AssignedItem) => number,
  walls: WallInterval[] = [],
  occupied: WallInterval[] = [],
): OverflowPlan {
  const infeasible: OverflowInfeasible[] = [];
  const cursor0 = anchor ? toMinutes(anchor) : Number.NaN;
  if (!anchor || !Number.isFinite(cursor0)) {
    // No day anchor: nothing can be laid out, and every candidate row says
    // so instead of vanishing.
    return {
      rows: [],
      infeasible: dropped
        .filter((i) => blocksOf(i) > 0)
        .map((i) => ({
          id: i.id,
          blocks: blocksOf(i),
          freeBlocks: 0,
          reason: "no day anchor — cannot lay out dropped rows",
        })),
    };
  }

  type Blocked = WallInterval & { kind: "wall" | "pinned" };
  const blocked: Blocked[] = [
    ...walls.map((w) => ({ ...w, kind: "wall" as const })),
    ...occupied.map((o) => ({ ...o, kind: "pinned" as const })),
  ]
    .filter((b) => b.end > b.start)
    .sort((a, b) => a.start - b.start || a.end - b.end);
  const nWalls = blocked.filter((b) => b.kind === "wall").length;
  const nPinned = blocked.length - nWalls;

  const out: SequenceRow[] = [];
  let cursor = cursor0;
  for (const item of dropped) {
    const blocks = blocksOf(item);
    // 0 blocks is the all-day state: no duration, so nothing to lay out.
    if (!(blocks > 0)) continue;
    const minutes = Math.round(blocks * BLOCK_MIN);
    let placed = false;
    while (!placed) {
      if (cursor >= END_OF_DAY) break;
      const start = cursor;
      const end = Math.min(cursor + minutes, END_OF_DAY - 1);
      if (end <= start) break;
      const hit = blocked.find(
        (b) => b.end > start && b.start < end,
      );
      if (!hit) {
        out.push({
          id: item.id,
          start: addMinutes("00:00", start),
          end: addMinutes("00:00", end),
          zone: null,
          kind: "work",
        });
        cursor += minutes;
        placed = true;
      } else {
        cursor = hit.end;
      }
    }
    if (!placed) {
      // The row could not be placed anywhere. Report the largest remaining
      // free gap after walls, pins, and rows already placed by this scan —
      // the honest "available capacity" for this row.
      const gap = largestFreeGap(cursor0, [
        ...blocked.map((b) => ({ start: b.start, end: b.end })),
        ...out.map((r) => ({
          start: toMinutes(r.start),
          end: toMinutes(r.end),
        })),
      ]);
      const freeBlocks = gap
        ? Math.floor((gap.end - gap.start) / BLOCK_MIN)
        : 0;
      const need = `${blocks} ${blocks === 1 ? "block" : "blocks"}`;
      const have = `${freeBlocks} ${freeBlocks === 1 ? "block" : "blocks"}`;
      const obstacles =
        nWalls === 0 && nPinned === 0
          ? "the day runs out"
          : `after ${[
              ...(nWalls > 0
                ? [`${nWalls} calendar wall${nWalls === 1 ? "" : "s"}`]
                : []),
              ...(nPinned > 0
                ? [`${nPinned} pinned row${nPinned === 1 ? "" : "s"}`]
                : []),
            ].join(" and ")}`;
      const gapText = gap
        ? `in the largest gap (${addMinutes("00:00", gap.start)}-${addMinutes("00:00", gap.end)})`
        : "in the largest gap (none)";
      infeasible.push({
        id: item.id,
        blocks,
        freeBlocks,
        reason: `needs ${need}, only ${have} free ${gapText} — ${obstacles}`,
      });
    }
  }
  return { rows: out, infeasible };
}

/** Legacy sequential placement (kept for callers/tests that want rows only):
    the rows `planOverflow` produced. Rows that cannot fit are omitted here
    and reported by `planOverflow.infeasible` — never silently in production,
    which uses `planOverflow`. */
export function overflowRows(
  dropped: AssignedItem[],
  anchor: string,
  blocksOf: (item: AssignedItem) => number,
  walls: WallInterval[] = [],
): SequenceRow[] {
  return planOverflow(dropped, anchor, blocksOf, walls).rows;
}

/** Total blocks the overflow occupies — for telling the user how far past the
    frame this pushes, rather than letting them discover it in BusyCal. */
export function overflowBlocks(rows: SequenceRow[]): number {
  return rows.reduce(
    (sum, r) => sum + (toMinutes(r.end) - toMinutes(r.start)) / BLOCK_MIN,
    0,
  );
}
