import type { MintSession, SchedulableOverride } from "./types";
import { toMinutes } from "./time";
import type { WallInterval } from "./overflow";

export const MINT_SESSION_MINUTES = 30;
/** Healthy Mint default: eight 30-minute sessions (four hours). */
export const MINT_DEFAULT_BLOCKS = 8;
export const MINT_DEFAULT_MINUTES = MINT_DEFAULT_BLOCKS * MINT_SESSION_MINUTES;

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
  const start = toMinutes(session.start);
  const end = toMinutes(session.end);
  // Invalid session intervals must never become selectable. This keeps stale
  // or hand-edited rows fail-closed even when there are no walls to compare.
  if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) {
    return true;
  }
  if (walls.length === 0) return false;
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
  return valid.filter((id) => {
    const session = sessions.find((candidate) => candidate.id === id);
    return session ? !sessionOverlapsWall(session, walls) : true;
  });
}

/** The Mint sessions a row can still take from the anchor: valid, starting at
    or after the anchor, and wall-free (FEEDBACK-28). The anchor is the lower
    bound, so a configured gap before it never consumes a session. Without a
    usable anchor, every valid wall-free session is eligible. */
export function eligibleMintSessions(
  sessions: MintSession[],
  anchor?: string,
  walls: WallInterval[] = [],
): MintSession[] {
  const anchorMinutes = anchor ? toMinutes(anchor) : Number.NaN;
  const lowerBound = Number.isFinite(anchorMinutes) ? anchorMinutes : null;
  return sessions.filter((session) => {
    const start = toMinutes(session.start);
    const end = toMinutes(session.end);
    if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) {
      return false;
    }
    if (lowerBound !== null && start < lowerBound) return false;
    return !sessionOverlapsWall(session, walls);
  });
}

/** The default Mint selection: every eligible session from the anchor through
    the end of the windows. The allotment does not cap this default. */
export function fillMintSessionIds(
  sessions: MintSession[],
  anchor?: string,
  walls: WallInterval[] = [],
): string[] {
  return eligibleMintSessions(sessions, anchor, walls).map((session) => session.id);
}

/** The explicit Mint cap in minutes, or null when the default fill applies.
    Only a saved numeric allotment is an explicit choice. A resolved zero from
    config or a preset stays a zero, which disables Mint. Any other resolved
    allotment only gates Mint on or off and never clips the fill. */
export function explicitMintCapMinutes(
  saved: { workAllotmentMinutes?: number | null } | undefined,
  effectiveMinutes?: number | null,
): number | null {
  if (typeof saved?.workAllotmentMinutes === "number") {
    return saved.workAllotmentMinutes;
  }
  return effectiveMinutes === 0 ? 0 : null;
}

/** Select the first N eligible sessions from the anchor, where N is the
 * requested allotment rounded to whole 30-minute sessions. The count is
 * bounded by the eligible sessions, so a request beyond the day selects them
 * all. Without an anchor, the first-N fallback applies to every valid
 * wall-free session.
 * FEEDBACK-28: sessions overlapping a fixed or work wall are never candidates,
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
  return eligibleMintSessions(sessions, anchor, walls)
    .slice(0, count)
    .map((session) => session.id);
}

export function mintMinutesForSessionIds(
  sessions: MintSession[],
  ids: string[],
): number {
  return validMintSessionIds(sessions, ids).length * MINT_SESSION_MINUTES;
}

/** Resolve saved state into one visible session selection.
 *
 * Explicit off and an explicit zero allotment both disable Mint. A saved
 * session list is an exact choice: wall-conflicting ids are dropped, and the
 * list is kept whole rather than clipped to the allotment. A saved id from
 * before the anchor is stale, so the selection is rebuilt from the anchor.
 * With a null allotment (no explicit cap) the rebuild is the default fill; a
 * numeric allotment caps it. FEEDBACK-28: saved ids overlapping a fixed or
 * work wall are stale and dropped before the choice is accepted.
 */
export function initialMintSessionIds(
  sessions: MintSession[],
  override: SchedulableOverride | undefined,
  allotmentMinutes: number | null,
  anchor?: string,
  walls: WallInterval[] = [],
): string[] {
  if (override?.on === false) return [];
  if (allotmentMinutes === 0) return [];
  if (Array.isArray(override?.sessions)) {
    const selected = wallFreeMintSessionIds(sessions, override.sessions, walls);
    const window = eligibleMintSessions(sessions, anchor, walls).map((session) => session.id);
    if (selected.every((id) => window.includes(id))) return selected;
  }
  if (allotmentMinutes === null) return fillMintSessionIds(sessions, anchor, walls);
  return mintSessionIdsForMinutes(sessions, allotmentMinutes, anchor, walls);
}
