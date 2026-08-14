/* pins.ts — client-side pin hygiene before a billed request.

   `sequence.validate_pinned_rows` (server) treats two pinned rows sharing a
   minute as a HARD error, and a hard error rejects the whole `/sequence` call
   before it runs. `validate_sequence` treats the SAME overlap between the same
   two rows as a soft warning under the never-bump / LD24 policy. So a plan the
   sequencer is happy to produce becomes a plan the pin validator refuses to
   accept back — and since pins persist across same-date reloads, one such
   sequence poisons every later Send until someone clears the pins by hand.

   Adam hit exactly that on 2026-07-27: a morning sequence stacked Note
   Processing / Frequent CWEAN / Reading / Stillness at 10:45–11:15 (soft
   warnings at the time), those placements became pins, and the 17:00 re-plan
   then hard-failed with six pairwise "pinned rows … overlap" errors and no
   control on screen to undo it.

   Pruning here rather than relaxing the server rule: a pin is a promise that a
   row sits at a specific time, and two rows cannot both keep that promise in
   the same minute. The EARLIEST-starting pin of an overlapping cluster is the
   one kept — the others were never deliberately placed there, so releasing
   them back to movable is what the user meant. Deliberate pins do not collide
   in the first place. */

import { toMinutes } from "./time";
import type { SequenceRow } from "./types";

/** Drop pins that collide with an already-kept pin. Earliest start wins;
    ties break on id so the result is deterministic. Malformed rows are left
    alone — the server names them precisely, and swallowing them here would
    hide a real bug behind a silent drop. */
export function prunePins(pins: SequenceRow[]): SequenceRow[] {
  const valid = pins.filter((p) => p.start && p.end && p.end > p.start);
  const malformed = pins.filter((p) => !valid.includes(p));

  const ordered = [...valid].sort((a, b) => {
    const d = toMinutes(a.start) - toMinutes(b.start);
    return d !== 0 ? d : a.id.localeCompare(b.id);
  });

  const kept: SequenceRow[] = [];
  for (const pin of ordered) {
    const start = toMinutes(pin.start);
    const end = toMinutes(pin.end);
    const collides = kept.some(
      (k) => start < toMinutes(k.end) && toMinutes(k.start) < end,
    );
    if (!collides) kept.push(pin);
  }
  return [...kept, ...malformed];
}

/** The pins `prunePins` would drop — for telling the user what was released
    rather than silently changing their plan. */
export function droppedPins(pins: SequenceRow[]): SequenceRow[] {
  const kept = new Set(prunePins(pins).map((p) => p.id));
  return pins.filter((p) => !kept.has(p.id));
}
