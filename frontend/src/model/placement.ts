/* placement.ts — allocator-rewrite T10: the post-Send read-only review model.

   Locked decision 2: the canvas dies and BusyCal owns post-placement
   corrections, so what replaces the canvas's review role is a LIST, not an
   editable surface. This module answers the three questions that list has to
   answer, as pure functions:

     1. what got placed, in order, at what time;
     2. what did NOT get placed — the silent failure mode, since a dropped
        item looks identical to an item that was never staged;
     3. which overlaps were allowed and WHY (T29 reasoned grants), so an
        overlap reads as a decision rather than a bug.

   No Preact, no store. */

import type { AssignedItem, OverlapGrant, SequenceRow } from "./types";

export interface PlacedRow {
  id: string;
  start: string;
  end: string;
  zone: string | null;
  /** Grant reasons naming this row, ready to render verbatim. */
  grantReasons: string[];
}

export interface PlacementReview {
  placed: PlacedRow[];
  /** Included items with no work row — dropped by the proposal. */
  dropped: AssignedItem[];
  hardErrors: string[];
  warnings: string[];
}

/** Chronological placed rows. Zone rows are the permeable backdrop, never a
    placement, so they are excluded — showing them would double-count the day. */
export function placedRows(
  sequence: SequenceRow[] | null,
  grants: OverlapGrant[] = [],
): PlacedRow[] {
  const work = (sequence ?? []).filter((r) => r.kind === "work");
  return [...work]
    .sort((a, b) => (a.start < b.start ? -1 : a.start > b.start ? 1 : a.id < b.id ? -1 : 1))
    .map((r) => ({
      id: r.id,
      start: r.start,
      end: r.end,
      zone: r.zone,
      grantReasons: grants
        .filter((g) => g.primaryId === r.id || g.companionId === r.id)
        .map((g) => g.reason)
        .filter((reason, i, all) => reason !== "" && all.indexOf(reason) === i),
    }));
}

/** Items the user included that the proposal did not place.

    This is the whole reason the list exists: on the canvas a dropped item was
    simply absent, indistinguishable from one never staged. Named explicitly,
    a drop is reviewable. All-day (0-block) rows are excluded — they are
    deliberately unplaced, not dropped.

    `sequence === null` means NO PROPOSAL YET and yields no drops; an empty
    array means a proposal that placed nothing, and every included item really
    was dropped. Collapsing those two would show the whole staging table as
    "not placed" before the user has even pressed Send. */
export function droppedItems(
  included: AssignedItem[],
  sequence: SequenceRow[] | null,
  blocksOf: (item: AssignedItem) => number,
): AssignedItem[] {
  if (sequence === null) return [];
  const placed = new Set(
    (sequence ?? []).filter((r) => r.kind === "work").map((r) => r.id),
  );
  return included.filter((i) => !placed.has(i.id) && blocksOf(i) > 0);
}

export function buildReview(
  included: AssignedItem[],
  sequence: SequenceRow[] | null,
  grants: OverlapGrant[],
  validation: { hardErrors: string[]; warnings: string[] } | null,
  blocksOf: (item: AssignedItem) => number,
): PlacementReview {
  return {
    placed: placedRows(sequence, grants),
    dropped: droppedItems(included, sequence, blocksOf),
    hardErrors: validation?.hardErrors ?? [],
    warnings: validation?.warnings ?? [],
  };
}

/** True when there is a proposal to review at all. */
export function hasReview(review: PlacementReview): boolean {
  return (
    review.placed.length > 0 ||
    review.dropped.length > 0 ||
    review.hardErrors.length > 0 ||
    review.warnings.length > 0
  );
}
