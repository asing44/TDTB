/* Display rules for calendar events drawn on timelines (Set up day strip and
   the Plan tasks "Already placed" lane). Three things never belong there:
   Mint sessions that earlier commits already wrote to the calendar (they are
   the Mint lane's own content, titled "MINT : ..."), events on session
   calendars ("Session: focus", "Mint"), and the same event listed
   more than once because it sits on several calendars or was imported twice. */

import type { AnchoredBlock } from "./types";

/** "MINT : Regular: OPPD Payment Risk", optionally behind a leading emoji such
    as the yellow circle Mint adds. Matches the prefix only, case-insensitively. */
const MINT_SESSION_TITLE = /^[\s\p{Extended_Pictographic}‍️]*mint\s*:/iu;

export function isMintSessionEvent(name: string): boolean {
  return MINT_SESSION_TITLE.test(name);
}

/** Calendars that hold session items rather than commitments: "Session: focus"
    (Pittering, Putz...) and the "Mint" calendar. Their events are Mint's own
    scheduling output, not walls, so timelines leave them out. */
const SESSION_CALENDAR_TITLE = /^[\s\p{Extended_Pictographic}\u200d\ufe0f]*(session\b|mint\b)/iu;

export function isSessionCalendar(title: string | null | undefined): boolean {
  return title != null && SESSION_CALENDAR_TITLE.test(title);
}

/** Keeps one copy of each (title, start, end, length) group, in the position of
    the group's first occurrence. The kept copy is the one that COUNTS: any
    capacity class other than 'ignored' beats 'ignored' (a fixed/work wall or a
    row with no class at all outranks a silenced calendar source). Among the
    counted copies the first stays, so only the silenced duplicate is dropped. */
export function dedupeCalendarEvents<
  T extends Pick<AnchoredBlock, "name" | "start" | "end" | "durationMin" | "capacityClass">,
>(events: T[]): T[] {
  const chosen = new Map<string, T>();
  for (const e of events) {
    const key = calendarEventKey(e);
    const current = chosen.get(key);
    if (current === undefined || countsMore(e, current)) chosen.set(key, e);
  }
  return [...chosen.values()];
}

function calendarEventKey(e: Pick<AnchoredBlock, "name" | "start" | "end" | "durationMin">): string {
  return `${e.name.trim().toLowerCase()}|${e.start ?? ""}|${e.end ?? ""}|${e.durationMin}`;
}

/** True when `a` counts in planning and `b` does not, or both count and `a`
    is simply the first seen. Anything but 'ignored' ranks as counted. */
function countsMore(a: Pick<AnchoredBlock, "capacityClass">, b: Pick<AnchoredBlock, "capacityClass">): boolean {
  return (a.capacityClass ?? "fixed") !== "ignored" && b.capacityClass === "ignored";
}

/** Calendar rows for display: no Mint-session titles, no duplicates; every
    non-calendar row passes through untouched. `sessionCalendars` also drops
    events from session calendars; Plan tasks leaves that off because it
    already tucks those (ignored-class) events behind its own count. Each
    group's counted copy takes the group's first position, so replacing a
    dropped duplicate does not reshuffle the lane. */
export function displayAnchored(
  rows: AnchoredBlock[],
  opts: { sessionCalendars?: boolean } = {},
): AnchoredBlock[] {
  const shown = (r: AnchoredBlock) =>
    r.kind === "calendar" &&
    !isMintSessionEvent(r.name) &&
    !(opts.sessionCalendars && isSessionCalendar(r.calendarTitle));
  const keptByKey = new Map<string, AnchoredBlock>();
  for (const r of dedupeCalendarEvents(rows.filter(shown))) {
    keptByKey.set(calendarEventKey(r), r);
  }
  const emitted = new Set<string>();
  const out: AnchoredBlock[] = [];
  for (const r of rows) {
    if (r.kind !== "calendar") {
      out.push(r);
      continue;
    }
    if (!shown(r)) continue;
    const key = calendarEventKey(r);
    if (emitted.has(key)) continue;
    emitted.add(key);
    const kept = keptByKey.get(key);
    if (kept !== undefined) out.push(kept);
  }
  return out;
}
