import type { MintSession, SchedulableOverride } from "./types";
import { toMinutes } from "./time";
import type { WallInterval } from "./overflow";

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

/** A session [start,end) conflicts when it intersects any wall interval —
    same half-open interval math the server's overlap validation uses. */
function sessionOverlapsWall(
  session: MintSession,
  walls: WallInterval[],
): boolean {
  if (walls.length === 0) return false;
  const start = toMinutes(session.start);
  const end = toMinutes(session.end);
  if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) {
    return false;
  }
  return walls.some((w) => end > w.start && w.end > start);
}

/** FEEDBACK-28: keep only valid saved/edited ids that do NOT overlap a fixed
    or work wall. Saved Mint state can be stale (the August 17 incident kept
    the 15:00-15:30 row across refresh), so every selection that reaches a
    judgment payload passes through this filter. */
export function wallFreeMintSessionIds(
  sessions: MintSession[],
  ids: string[],
  walls: WallInterval[] = [],
): string[] {
  const valid = validMintSessionIds(sessions, ids);
  if (walls.length === 0) return valid;
  return valid.filter((id) => {
    const session = sessions.find((candidate) => candidate.id === id);
    return session ? !sessionOverlapsWall(session, walls) : true;
  });
}

function mintSessionsFromAnchor(
  sessions: MintSession[],
  minutes: number,
  anchor?: string,
  walls: WallInterval[] = [],
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
  // Mint session or force a morning row back into the selection. Wall-
  // conflicting sessions (FEEDBACK-28) are never candidates: a Mint row must
  // not be selected over an immutable fixed/work commitment.
  return sessions.filter((session) => {
    const start = toMinutes(session.start);
    const end = toMinutes(session.end);
    return Number.isFinite(start) && Number.isFinite(end)
      && start >= anchorMinutes
      && end > start
      && !sessionOverlapsWall(session, walls);
  });
}

/** Select the first N configured sessions inside the start-anchor window.
 *
 * Mint session rows are fixed 30-minute windows. The UI therefore snaps a
 * configured 15-minute allotment to the nearest session count and lets the
 * checked-session total become the saved daily value. Without an anchor, the
 * historical first-N behavior remains the fallback for older callers.
 * FEEDBACK-28: sessions overlapping a fixed or work wall are skipped first,
 * so a wall-conflicting session never becomes a Mint commitment.
 */
export function mintSessionIdsForMinutes(
  sessions: MintSession[],
  minutes: number,
  anchor?: string,
  walls: WallInterval[] = [],
): string[] {
  const requested = Number.isFinite(minutes) ? Math.max(0, minutes) : 0;
  const count = Math.min(
    sessions.length,
    Math.max(0, Math.round(requested / MINT_SESSION_MINUTES)),
  );
  const candidates = (
    mintSessionsFromAnchor(sessions, requested, anchor, walls) ?? sessions
  ).filter((session) => !sessionOverlapsWall(session, walls));
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
 * the entire Trinoor day. FEEDBACK-28: saved ids overlapping a fixed or work
 * wall are stale and dropped before the choice is accepted.
 */
export function initialMintSessionIds(
  sessions: MintSession[],
  override: SchedulableOverride | undefined,
  allotmentMinutes: number,
  anchor?: string,
  walls: WallInterval[] = [],
): string[] {
  if (override?.on === false) return [];
  if (Array.isArray(override?.sessions)) {
    const selected = wallFreeMintSessionIds(sessions, override.sessions, walls);
    const allotment = Number.isFinite(allotmentMinutes) ? Math.max(0, allotmentMinutes) : 0;
    const isLegacyAll =
      selected.length === sessions.length &&
      selected.length > 0 &&
      allotment !== selected.length * MINT_SESSION_MINUTES;
    if (!isLegacyAll) {
      const window = mintSessionsFromAnchor(sessions, allotment, anchor, walls);
      const inWindow = window === null
        || selected.every((id) => window.some((session) => session.id === id));
      if (inWindow) return selected;
    }
  }
  return mintSessionIdsForMinutes(sessions, allotmentMinutes, anchor, walls);
}
