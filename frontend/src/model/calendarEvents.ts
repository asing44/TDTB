/* Display rules for calendar events drawn on timelines (Set up day strip and
   the Plan tasks "Already placed" lane). Two things never belong there:
   Mint sessions that earlier commits already wrote to the calendar (they are
   the Mint lane's own content, titled "MINT : ..."), and the same event listed
   more than once because it sits on several calendars or was imported twice. */

import type { AnchoredBlock } from "./types";

/** "MINT : Regular: OPPD Payment Risk", optionally behind a leading emoji such
    as the yellow circle Mint adds. Matches the prefix only, case-insensitively. */
const MINT_SESSION_TITLE = /^[\s\p{Extended_Pictographic}‍️]*mint\s*:/iu;

export function isMintSessionEvent(name: string): boolean {
  return MINT_SESSION_TITLE.test(name);
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
    non-calendar row passes through untouched. */
export function displayAnchored(rows: AnchoredBlock[]): AnchoredBlock[] {
  const calendar = dedupeCalendarEvents(
    rows.filter((r) => r.kind === "calendar" && !isMintSessionEvent(r.name)),
  );
  const keep = new Set(calendar);
  return rows.filter((r) => r.kind !== "calendar" || keep.has(r));
}
