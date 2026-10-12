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

/** Keeps the first of each (title, start, end, length) group, in order. */
export function dedupeCalendarEvents<T extends Pick<AnchoredBlock, "name" | "start" | "end" | "durationMin">>(
  events: T[],
): T[] {
  const seen = new Set<string>();
  return events.filter((e) => {
    const key = `${e.name.trim().toLowerCase()}|${e.start ?? ""}|${e.end ?? ""}|${e.durationMin}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

/** Calendar rows for display: no Mint-session titles, no duplicates; every
    non-calendar row passes through untouched. `sessionCalendars` also drops
    events from session calendars; Plan tasks leaves that off because it
    already tucks those (ignored-class) events behind its own count. */
export function displayAnchored(
  rows: AnchoredBlock[],
  opts: { sessionCalendars?: boolean } = {},
): AnchoredBlock[] {
  const calendar = dedupeCalendarEvents(
    rows.filter(
      (r) =>
        r.kind === "calendar" &&
        !isMintSessionEvent(r.name) &&
        !(opts.sessionCalendars && isSessionCalendar(r.calendarTitle)),
    ),
  );
  const keep = new Set(calendar);
  return rows.filter((r) => r.kind !== "calendar" || keep.has(r));
}
