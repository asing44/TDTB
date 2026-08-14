import type { MintSession, SchedulableOverride } from "./types";
import { toMinutes } from "./time";

export const MINT_SESSION_MINUTES = 30;

export function validMintSessionIds(
  sessions: MintSession[],
  ids: string[],
): string[] {
  const available = new Set(sessions.map((session) => session.id));
  const seen = new Set<string>();
  return ids.filter((id) => {
    if (!available.has(id) || seen.has(id)) return false;
    seen.add(id);
    return true;
  });
}

function mintSessionsFromAnchor(
  sessions: MintSession[],
  minutes: number,
  anchor?: string,
): MintSession[] | null {
  if (!anchor) return null;
  const anchorMinutes = toMinutes(anchor);
  if (!Number.isFinite(anchorMinutes)) return null;

  const requested = Number.isFinite(minutes) ? Math.max(0, minutes) : 0;
  const count = Math.min(
    sessions.length,
    Math.max(0, Math.round(requested / MINT_SESSION_MINUTES)),
  );
  if (count === 0) return [];

  // The anchor is the lower bound. The allotment caps how many available
  // 30-minute windows can be selected; configured gaps must not consume a
  // Mint session or force a morning row back into the selection.
  return sessions.filter((session) => {
    const start = toMinutes(session.start);
    const end = toMinutes(session.end);
    return Number.isFinite(start) && Number.isFinite(end)
      && start >= anchorMinutes
      && end > start;
  });
}

/** Select the first N configured sessions inside the start-anchor window.
 *
 * Mint session rows are fixed 30-minute windows. The UI therefore snaps a
 * configured 15-minute allotment to the nearest session count and lets the
 * checked-session total become the saved daily value. Without an anchor, the
 * historical first-N behavior remains the fallback for older callers.
 */
export function mintSessionIdsForMinutes(
  sessions: MintSession[],
  minutes: number,
  anchor?: string,
): string[] {
  const requested = Number.isFinite(minutes) ? Math.max(0, minutes) : 0;
  const count = Math.min(
    sessions.length,
    Math.max(0, Math.round(requested / MINT_SESSION_MINUTES)),
  );
  const candidates = mintSessionsFromAnchor(sessions, requested, anchor) ?? sessions;
  return candidates.slice(0, count).map((session) => session.id);
}

export function mintMinutesForSessionIds(
  sessions: MintSession[],
  ids: string[],
): number {
  return validMintSessionIds(sessions, ids).length * MINT_SESSION_MINUTES;
}

/** Resolve old/inconsistent saved state into one visible checkbox selection.
 *
 * A partial saved list is treated as an intentional location choice. An old
 * "all sessions" list with a different allotment was produced by the former
 * UI default, so it is rebuilt from the total instead of silently reserving
 * the entire Trinoor day.
 */
export function initialMintSessionIds(
  sessions: MintSession[],
  override: SchedulableOverride | undefined,
  allotmentMinutes: number,
  anchor?: string,
): string[] {
  if (override?.on === false) return [];
  if (Array.isArray(override?.sessions)) {
    const selected = validMintSessionIds(sessions, override.sessions);
    const allotment = Number.isFinite(allotmentMinutes) ? Math.max(0, allotmentMinutes) : 0;
    const isLegacyAll =
      selected.length === sessions.length &&
      selected.length > 0 &&
      allotment !== selected.length * MINT_SESSION_MINUTES;
    if (!isLegacyAll) {
      const window = mintSessionsFromAnchor(sessions, allotment, anchor);
      const inWindow = window === null
        || selected.every((id) => window.some((session) => session.id === id));
      if (inWindow) return selected;
    }
  }
  return mintSessionIdsForMinutes(sessions, allotmentMinutes, anchor);
}
