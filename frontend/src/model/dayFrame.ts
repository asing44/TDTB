/* dayFrame — the one day frame both the Set up day screen and the Plan screen
   draw. Planning 2a moved the Set up frame end to the configured End field
   (`configEod`) while the Plan screen still used the server's `effectiveEod`,
   so the two timelines could disagree about the same day. Resolving the frame
   in one place keeps them identical. A Set up draft End wins while it is
   unsaved; otherwise the End field (configEod) is authoritative. */

export interface DayFrame {
  /** 24h HH:MM frame start (today's anchor). */
  start: string;
  /** 24h HH:MM frame end (the configured End field). */
  end: string;
}

export function dayFrame(
  time: { anchor: string; configEod: string },
  draft?: { anchor?: string | null; eod?: string | null },
): DayFrame {
  return {
    start: draft?.anchor ?? time.anchor,
    end: draft?.eod ?? time.configEod,
  };
}
