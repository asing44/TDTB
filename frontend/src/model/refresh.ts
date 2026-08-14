/* refresh.ts — pure same-date source-refresh reconciliation (locked decisions
   20/21, T13). Given the previous read model + today-only shaping and a fresh
   /plan-inputs projection, computes:

   - added / removed / changed assigned rows by stable identity (name-keyed id);
   - pruned overrides/placements/staged rows for rows no longer upstream;
   - staged-row resizes where the effective duration changed upstream
     (a local duration override keeps winning — compatible shaping survives);
   - anchored Day Setup override retention: a raw spec change beneath a
     still-valid override is RETAINED and reported; an override the new spec
     can no longer satisfy is DROPPED, never silently rebased (LD 21).

   Fingerprint drift (raw + effective) is judged by the reducer — this module
   only reconciles rows. No I/O, no clocks: fully unit-testable. */

import type {
  AnchoredBlock,
  AnchoredOverride,
  AssignedItem,
  SequenceRow,
  TodayOverride,
} from "./types";
import { validateAnchoredOverride } from "./anchored";

export interface RefreshSummary {
  added: string[];
  removed: string[];
  changed: string[];
  overridesRetained: string[];
  overridesDropped: string[];
  /** True when the reducer invalidated staged sequence/shadow on fingerprint
      drift — set there, not here. */
  invalidated: boolean;
}

export function summaryHasChanges(x: RefreshSummary): boolean {
  return (
    x.added.length > 0 ||
    x.removed.length > 0 ||
    x.changed.length > 0 ||
    x.overridesRetained.length > 0 ||
    x.overridesDropped.length > 0
  );
}

export interface ReconcileArgs {
  prevAssigned: AssignedItem[];
  nextAssigned: AssignedItem[];
  prevAnchored: AnchoredBlock[];
  nextAnchored: AnchoredBlock[];
  anchoredOverrides: Record<string, AnchoredOverride>;
  frame: { anchor: string; effectiveEod: string };
  overrides: Record<string, TodayOverride>;
  placements: Record<string, string>;
  sequence: SequenceRow[] | null;
}

export interface ReconcileResult {
  summary: RefreshSummary;
  overrides: Record<string, TodayOverride>;
  placements: Record<string, string>;
  sequence: SequenceRow[] | null;
  anchoredOverrides: Record<string, AnchoredOverride>;
  /** True when any staged work row was pruned or resized. */
  sequenceTouched: boolean;
}

function assignedChanged(a: AssignedItem, b: AssignedItem): boolean {
  return (
    a.blocks !== b.blocks ||
    a.durationLabel !== b.durationLabel ||
    a.deadline !== b.deadline ||
    a.urgency !== b.urgency
  );
}

function specChanged(a: AnchoredBlock, b: AnchoredBlock): boolean {
  return (
    a.start !== b.start ||
    a.durationMin !== b.durationMin ||
    a.on !== b.on ||
    a.skipToday !== b.skipToday ||
    a.kind !== b.kind
  );
}

/** What /plan-inputs SHOULD return for this block if upstream is unchanged:
    the server applies the same Day Setup override before responding, so the
    fresh effective row only differs from this when the SOURCE drifted. Field
    mapping mirrors the store's effectiveAnchoredBlocks selector. */
function expectedEffective(prev: AnchoredBlock, o: AnchoredOverride): AnchoredBlock {
  return {
    ...prev,
    start: o.time ?? prev.start,
    durationMin: o.blocks == null ? prev.durationMin : o.blocks * 30,
    on: o.on,
    skipToday: o.skipToday,
  };
}

function addMin(hhmm: string, delta: number): string {
  const [h, m] = hhmm.split(":").map(Number);
  const t = (((h * 60 + m + delta) % 1440) + 1440) % 1440;
  return `${String(Math.floor(t / 60)).padStart(2, "0")}:${String(t % 60).padStart(2, "0")}`;
}

export function reconcileRefresh(args: ReconcileArgs): ReconcileResult {
  const prevById = new Map(args.prevAssigned.map((i) => [i.id, i]));
  const nextById = new Map(args.nextAssigned.map((i) => [i.id, i]));

  const added = args.nextAssigned.filter((i) => !prevById.has(i.id)).map((i) => i.name);
  const removedItems = args.prevAssigned.filter((i) => !nextById.has(i.id));
  const removed = removedItems.map((i) => i.name);
  const removedIds = new Set(removedItems.map((i) => i.id));
  const changed = args.nextAssigned
    .filter((i) => {
      const prev = prevById.get(i.id);
      return prev != null && assignedChanged(prev, i);
    })
    .map((i) => i.name);

  // Prune today-only shaping for rows no longer upstream (LD 20).
  const overrides: Record<string, TodayOverride> = {};
  for (const [id, o] of Object.entries(args.overrides)) {
    if (nextById.has(id)) overrides[id] = o;
  }
  const placements: Record<string, string> = {};
  for (const [id, start] of Object.entries(args.placements)) {
    if (nextById.has(id)) placements[id] = start;
  }

  // Staged rows: prune disappeared work rows; resize rows whose EFFECTIVE
  // blocks changed upstream (no local duration override to preserve them).
  let sequenceTouched = false;
  let sequence = args.sequence;
  if (sequence) {
    const out: SequenceRow[] = [];
    for (const r of sequence) {
      if (r.kind === "zone") {
        out.push(r);
        continue;
      }
      if (removedIds.has(r.id)) {
        sequenceTouched = true;
        continue;
      }
      const next = nextById.get(r.id);
      const localBlocks = overrides[r.id]?.blocks;
      if (next && localBlocks == null) {
        const prev = prevById.get(r.id);
        if (prev && prev.blocks !== next.blocks) {
          if (next.blocks === 0) {
            // Became all-day upstream: included, but no timeline row.
            sequenceTouched = true;
            delete placements[r.id];
            continue;
          }
          out.push({ ...r, end: addMin(r.start, next.blocks * 30) });
          sequenceTouched = true;
          continue;
        }
      }
      out.push(r);
    }
    sequence = out;
  }

  // Anchored Day Setup overrides (LD 21): drop for disappeared blocks; on a
  // raw spec change keep a still-valid override (report retained) and drop an
  // incompatible one (report dropped) rather than silently rebasing it.
  const prevAnchoredById = new Map(args.prevAnchored.map((b) => [b.id, b]));
  const nextAnchoredById = new Map(args.nextAnchored.map((b) => [b.id, b]));
  const anchoredOverrides: Record<string, AnchoredOverride> = {};
  const overridesRetained: string[] = [];
  const overridesDropped: string[] = [];
  for (const [id, override] of Object.entries(args.anchoredOverrides)) {
    const next = nextAnchoredById.get(id);
    if (!next) {
      overridesDropped.push(prevAnchoredById.get(id)?.name ?? id);
      continue;
    }
    if (next.kind === "calendar") {
      // T28: the one calendar override that survives is a per-day dismissal
      // (plan participation). Anything else — including a block reclassified
      // to calendar under a time/duration override — is dropped, never
      // rebased (LD19/LD21).
      if (override.skipToday === true) {
        anchoredOverrides[id] = { on: true, skipToday: true, time: null };
        overridesRetained.push(next.name);
      } else {
        overridesDropped.push(prevAnchoredById.get(id)?.name ?? id);
      }
      continue;
    }
    const prev = prevAnchoredById.get(id);
    if (prev && specChanged(expectedEffective(prev, override), next)) {
      const findings = validateAnchoredOverride(next, override, args.frame);
      if (findings.errors.length === 0) {
        anchoredOverrides[id] = override;
        overridesRetained.push(next.name);
      } else {
        overridesDropped.push(next.name);
      }
      continue;
    }
    anchoredOverrides[id] = override;
  }

  return {
    summary: {
      added,
      removed,
      changed,
      overridesRetained,
      overridesDropped,
      invalidated: false,
    },
    overrides,
    placements,
    sequence,
    anchoredOverrides,
    sequenceTouched,
  };
}
