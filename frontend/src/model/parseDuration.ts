/* parseDuration.ts — pure lenient parser for the expanded row's typed
   duration. No Preact, no store. Accepts a bare number (minutes), h/hr/hour
   and m/min/minute units, an "h:mm" clock form, and combinations such as
   "1h 15m". Returns whole minutes; the caller converts to 30-minute blocks
   (blocks = minutes / 30, the same exact conversion duration memory uses). */

/** Longest typed duration: 16 hours. */
export const MAX_CUSTOM_MINUTES = 16 * 60;

export type ParsedDuration =
  | { ok: true; minutes: number }
  | { ok: false; error: string };

const HOURS = String.raw`(\d+(?:\.\d+)?)\s*(?:h|hr|hrs|hour|hours)`;
const MINUTES = String.raw`(\d+)\s*(?:m|min|mins|minute|minutes)?`;
const UNIT_FORM = new RegExp(`^(?:${HOURS})?\\s*(?:${MINUTES})?$`, "i");
const CLOCK_FORM = /^(\d+):(\d{1,2})$/;
const BARE = /^\d+$/;

const FORMAT_HELP = "Enter a duration like 10, 45min, 1h or 1h 15m.";

export function parseDuration(text: string): ParsedDuration {
  const t = text.trim();
  if (t === "") return { ok: false, error: FORMAT_HELP };

  let minutes: number;
  const clock = CLOCK_FORM.exec(t);
  if (clock) {
    if (Number(clock[2]) >= 60) return { ok: false, error: "Minutes must be under 60 in h:mm." };
    minutes = Number(clock[1]) * 60 + Number(clock[2]);
  } else if (BARE.test(t)) {
    minutes = Number(t);
  } else {
    const m = UNIT_FORM.exec(t);
    if (!m || (m[1] === undefined && m[2] === undefined)) {
      return { ok: false, error: FORMAT_HELP };
    }
    minutes = Math.round(Number(m[1] ?? 0) * 60) + Number(m[2] ?? 0);
  }

  if (!Number.isFinite(minutes) || minutes <= 0) {
    return { ok: false, error: "Duration must be more than 0 minutes." };
  }
  if (minutes > MAX_CUSTOM_MINUTES) {
    return { ok: false, error: "Duration can be at most 16 hours." };
  }
  return { ok: true, minutes };
}

/** Whole minutes -> 30-minute blocks (the unit TodayOverride carries).
    The backend sizes placements with `int(blocks * 30)`, which truncates, and
    a plain `minutes / 30` double can land one ulp short (123 min ->
    122.99999999999999 min), so the value is nudged up until the truncating
    product is the exact minute count. Whole-block values pass through. */
export function minutesToBlocks(minutes: number): number {
  let blocks = minutes / 30;
  while (Math.floor(blocks * 30) < minutes) {
    blocks += blocks * Number.EPSILON;
  }
  return blocks;
}
