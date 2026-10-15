/* calendarEdit.ts — local-only edits of calendar events on the Set up day.

   An operator may move a calendar event (Start), resize it (blocks) and drop it
   (Count today) for TODAY only. The edit lives in the dated day-setup override
   (`start` + `calendarStartAtEdit`) and is never written to the calendar. This
   module holds the pure rules: whether a row counts as edited, whether the
   live calendar moved since the edit, and the overlap findings an edit can
   introduce. Overlaps are warnings, never blockers. */

import { toHHMM, toMinutes } from "./time";
import type { AnchoredBlock, AnchoredOverride } from "./types";

export interface CalendarEditState {
  /** A local start override is in force. */
  edited: boolean;
  /** The calendar's own start moved since the edit (the edit is kept). */
  changedInCalendar: boolean;
  /** The live calendar start (24h HH:MM), null when the row has none. */
  calendarStart: string | null;
}

export function calendarEditState(
  override: AnchoredOverride | undefined,
  liveStart: string | null,
): CalendarEditState {
  const edited = override?.start != null;
  const atEdit = override?.calendarStartAtEdit ?? null;
  return {
    edited,
    changedInCalendar: edited && atEdit != null && liveStart != null && liveStart !== atEdit,
    calendarStart: liveStart,
  };
}

export interface OverlapInterval {
  id: string;
  name: string;
  start: string;
  end: string;
}

function intervalOf(block: AnchoredBlock): { start: number; end: number } | null {
  if (!block.start) return null;
  const start = toMinutes(block.start);
  const end = block.end ? toMinutes(block.end) : start + block.durationMin;
  return end > start ? { start, end } : null;
}

/** Overlaps an edited, counted calendar event has with other counted
    fixed/work calendar events, counted hard anchors and selected Mint
    sessions. `blocks` are the EFFECTIVE rows (edits applied). Strings follow
    the quoted-repr style of the other client-side overlap findings. */
export function calendarEditOverlaps(args: {
  blocks: AnchoredBlock[];
  editedIds: string[];
  mint: OverlapInterval[];
}): string[] {
  const out: string[] = [];
  const counted = (b: AnchoredBlock) => b.on && !b.skipToday && !b.overlapAllowed;
  const wallsOthers = args.blocks.filter((b) => {
    if (!counted(b)) return false;
    if (b.kind === "hard") return true;
    if (b.kind !== "calendar") return false;
    const cls = b.capacityClass ?? "fixed";
    return cls === "fixed" || cls === "work";
  });
  for (const id of args.editedIds) {
    const edited = args.blocks.find((b) => b.id === id && b.kind === "calendar");
    if (!edited || !counted(edited)) continue;
    const mine = intervalOf(edited);
    if (!mine || !edited.start) continue;
    const label = `Edited calendar event '${edited.name}' (${edited.start}-${toHHMM(mine.end)})`;
    const others: Array<{ name: string; start: string; end: string; min: number; max: number }> = [];
    for (const b of wallsOthers) {
      if (b.id === id) continue;
      const iv = intervalOf(b);
      if (iv && b.start) others.push({ name: b.name, start: b.start, end: toHHMM(iv.end), min: iv.start, max: iv.end });
    }
    for (const m of args.mint) {
      others.push({ name: m.name, start: m.start, end: m.end, min: toMinutes(m.start), max: toMinutes(m.end) });
    }
    others
      .sort((a, b) => a.min - b.min || a.name.localeCompare(b.name))
      .forEach((o) => {
        if (o.min < mine.end && mine.start < o.max) {
          out.push(`${label} overlaps '${o.name}' (${o.start}-${o.end})`);
        }
      });
  }
  return out;
}
