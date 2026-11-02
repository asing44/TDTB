/* capacitySegments.ts — which fixed segment pays for shared clock time when
   the day is DRAWN.

   Capacity keeps every segment at its own size and reports the shared amount
   separately (`capacity.overlap`), because the operator wants to see Calendar
   2h and Anchors 3h even when they sit on the same minutes. Drawn geometry
   cannot do that: a bar or an arc stack has to partition the day exactly, or
   the day-capacity marker lands past the real end of the day. So wherever the
   day is drawn, the shared blocks come out of the anchored slice first (an
   anchor placed on top of a meeting is the usual case), then out of calendar.

   Text ledgers keep the raw numbers and name the shared amount; this helper is
   only for widths and heights. */

import type { Capacity } from "./types";

export interface FixedDraw {
  fixed: number;
  anchored: number;
}

/** Drawn sizes for the two fixed segments, with the shared clock time counted
    once — anchored first, then calendar. Credit beyond both segments (a
    malformed read) is ignored rather than pushing a segment negative. */
export function creditedFixedDraw(
  cap: Capacity | null | undefined,
): FixedDraw {
  let credit = Math.max(0, Number(cap?.overlap ?? 0));
  const anchored = Math.max(0, Number(cap?.anchored ?? 0));
  const anchoredTaken = Math.min(credit, anchored);
  credit -= anchoredTaken;
  const fixed = Math.max(0, Number(cap?.fixed ?? 0));
  const fixedTaken = Math.min(credit, fixed);
  return { fixed: fixed - fixedTaken, anchored: anchored - anchoredTaken };
}
