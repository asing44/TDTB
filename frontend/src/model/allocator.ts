/* allocator.ts — allocator-rewrite T7: pure model behind the block-budget
   table. No Preact, no store, no I/O — every function here is a total
   function of its arguments so the table's ordering and readout are
   unit-testable without rendering.

   Two jobs:

   1. IMPORTANCE ORDER. The staging table is the centre of the cockpit now,
      so its order has to mean something. It mirrors the server's digest
      ranking (urgency desc, overdue first, nearest deadline, then name) so a
      row doesn't sit in one place in the digest and another in the table.

   2. LIVE REMAINING. Dragging a slider must move the remaining readout
      immediately. The server's capacity is authoritative but a round-trip
      per drag frame is not an option, so free is re-derived locally from the
      one term that changed: `free + selected - localSelected` is exact,
      because the server computed free as
      `total - fixed - anchored - habits - mint - buffer - selected`. */

import type { AssignedItem, Capacity } from "./types";
import { normalizeUrgency } from "./urgency";
import { formatBlockAmount } from "./time";

/** Slider bounds. 0 = all day (visible, no capacity — locked decision 7);
    16 blocks = 8h, past any single sane work item. */
export const MIN_BLOCKS = 0;
export const MAX_BLOCKS = 16;
/** Locked decision 7: 30-minute steps, i.e. one block per notch. */
export const BLOCK_STEP = 1;
/** The ±15min row steppers shape BETWEEN the slider's notches, so they snap to
    a finer grid than the slider does. Without this they were dead controls:
    `clampBlocks(n - 0.5)` rounds a half-block straight back to `n`, so − was a
    no-op at every integer and + always jumped a full 30 minutes. The exact
    editor has always emitted fractional blocks (5-minute steps), so the wire
    and the server already speak this vocabulary. */
export const HALF_BLOCK = 0.5;

const NO_DEADLINE = "9999-12-31";

/** normalizeUrgency yields a TIER NAME, not a number — this is the only place
    that needs it ordered, so the rank lives here rather than in urgency.ts. */
const TIER_RANK: Record<string, number> = { crit: 4, high: 3, med: 2, low: 1 };

/** Same criteria, same precedence as the server's `_rank_key`: urgency desc,
    overdue first, nearest deadline, then name as the deterministic tie-break. */
export function importanceKey(
  item: AssignedItem,
  today: string,
): [number, number, string, string] {
  const tier = TIER_RANK[normalizeUrgency(item)?.tier ?? ""] ?? 0;
  const deadline = item.deadline || NO_DEADLINE;
  const overdue = item.deadline && today && item.deadline < today ? 0 : 1;
  return [-tier, overdue, deadline, item.name];
}

/** Importance-ordered copy. Stable and total: equal keys fall back to name,
    so the same input always renders in the same order. */
export function orderByImportance(
  items: AssignedItem[],
  today: string,
): AssignedItem[] {
  return [...items].sort((a, b) => {
    const ka = importanceKey(a, today);
    const kb = importanceKey(b, today);
    for (let i = 0; i < ka.length; i++) {
      if (ka[i] < kb[i]) return -1;
      if (ka[i] > kb[i]) return 1;
    }
    return 0;
  });
}

/** Clamp a value to an arbitrary block grid. Guards NaN from a hand-driven
    range event as well as out-of-range values. */
export function clampBlocksTo(blocks: number, grid: number): number {
  if (!Number.isFinite(blocks)) return MIN_BLOCKS;
  const snapped = Math.round(blocks / grid) * grid;
  return Math.min(MAX_BLOCKS, Math.max(MIN_BLOCKS, snapped));
}

/** Clamp a slider value to the block grid. */
export function clampBlocks(blocks: number): number {
  return clampBlocksTo(blocks, BLOCK_STEP);
}

/** Clamp a stepper value to the half-block (15-minute) grid. */
export function clampHalfBlocks(blocks: number): number {
  return clampBlocksTo(blocks, HALF_BLOCK);
}

/** Free blocks with the user's in-flight slider values applied. Exact rather
    than approximate — see the header note on why the substitution is safe. */
export function liveFree(cap: Capacity | null, localSelected: number): number {
  if (!cap) return 0;
  return cap.free + cap.selected - localSelected;
}

/** The live remaining readout. Keep the server's "left" / "over" vocabulary,
    but format the amount through the shared display policy so arithmetic noise
    never leaks into the cockpit. */
export function remainingLabel(free: number): string {
  const stableFree = Number.isFinite(free) && Math.abs(free) <= 1e-9 ? 0 : free;
  if (stableFree > 0) return `⬆ ${formatBlockAmount(stableFree)} left`;
  if (stableFree === 0) return "⬆ fully booked · 0 blk left";
  return `⚠ ${formatBlockAmount(Math.abs(stableFree))} over`;
}
