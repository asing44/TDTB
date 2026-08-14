/* pie.ts — allocator-rewrite T8: geometry for the allocation pie.

   Pure arc math, no Preact — the interesting failure modes here are
   geometric (a 100% slice, a zero-total day, floating-point gaps between
   adjacent arcs) and they deserve unit tests rather than a screenshot.

   The segments are the engine's own, unchanged: Fixed / Anchored / Habits /
   Mint / Selected / Buffer, plus Unallocated for what's left. Nothing is
   recomputed here — this is a second RENDERING of capacity, never a second
   opinion about it. */

import type { Capacity } from "./types";

export interface PieSlice {
  key: string;
  label: string;
  blocks: number;
  /** 0..1 share of the day. */
  fraction: number;
  /** SVG path `d` for the wedge. */
  d: string;
  color: string;
}

/** Order matches capacity.py's segment order so the pie reads clockwise in
    the same sequence as the legend and the linear bar. */
const SEGMENTS: Array<{ key: keyof Capacity & string; label: string; color: string }> = [
  { key: "fixed", label: "Fixed", color: "var(--c-event)" },
  { key: "anchored", label: "Anchored", color: "var(--c-anchored)" },
  { key: "habits", label: "Habits", color: "var(--c-habit)" },
  { key: "mint", label: "Mint", color: "var(--c-minting)" },
  { key: "selected", label: "Selected", color: "var(--c-selected)" },
  { key: "buffer", label: "Buffer", color: "var(--c-buffer)" },
];

/* Config's `free` token, not a neutral surface: the vault Color Palette
   (Skill-Configs/tdtb-bridger.md) is source of truth for every visual element,
   and it assigns green to free capacity. A surface-colored wedge also read as
   chart background rather than as unspent day. */
export const UNALLOCATED_COLOR = "var(--c-free)";

function polar(cx: number, cy: number, r: number, fraction: number): [number, number] {
  // -90° so the first slice starts at 12 o'clock rather than 3 o'clock.
  const angle = fraction * 2 * Math.PI - Math.PI / 2;
  return [cx + r * Math.cos(angle), cy + r * Math.sin(angle)];
}

function round(n: number): number {
  return Math.round(n * 1000) / 1000;
}

/** Wedge path from `start` to `end` (both 0..1 fractions of the circle).
    A wedge covering the whole circle is emitted as two half-arcs: a single
    arc whose start and end points coincide is a degenerate no-op in SVG and
    would render nothing at all on a fully-booked day.

    `innerR > 0` makes it an annular sector — the donut. The hole is real
    geometry, not a disc parked on top of a pie: an overlay would have to
    guess the surface colour behind it, and it would swallow pointer events
    the wedges need. */
export function wedgePath(
  cx: number, cy: number, r: number, start: number, end: number, innerR = 0,
): string {
  const span = end - start;
  if (span <= 0) return "";
  if (span >= 1) {
    const [tx, ty] = polar(cx, cy, r, 0);
    const [bx, by] = polar(cx, cy, r, 0.5);
    const outer = [
      `M ${round(tx)} ${round(ty)}`,
      `A ${r} ${r} 0 1 1 ${round(bx)} ${round(by)}`,
      `A ${r} ${r} 0 1 1 ${round(tx)} ${round(ty)}`,
      "Z",
    ].join(" ");
    if (innerR <= 0) return outer;
    // Second subpath wound the opposite way punches the hole under the
    // default nonzero fill rule.
    const [itx, ity] = polar(cx, cy, innerR, 0);
    const [ibx, iby] = polar(cx, cy, innerR, 0.5);
    return [
      outer,
      `M ${round(itx)} ${round(ity)}`,
      `A ${innerR} ${innerR} 0 1 0 ${round(ibx)} ${round(iby)}`,
      `A ${innerR} ${innerR} 0 1 0 ${round(itx)} ${round(ity)}`,
      "Z",
    ].join(" ");
  }
  const [sx, sy] = polar(cx, cy, r, start);
  const [ex, ey] = polar(cx, cy, r, end);
  const largeArc = span > 0.5 ? 1 : 0;
  if (innerR <= 0) {
    return [
      `M ${cx} ${cy}`,
      `L ${round(sx)} ${round(sy)}`,
      `A ${r} ${r} 0 ${largeArc} 1 ${round(ex)} ${round(ey)}`,
      "Z",
    ].join(" ");
  }
  const [isx, isy] = polar(cx, cy, innerR, start);
  const [iex, iey] = polar(cx, cy, innerR, end);
  return [
    `M ${round(isx)} ${round(isy)}`,
    `L ${round(sx)} ${round(sy)}`,
    `A ${r} ${r} 0 ${largeArc} 1 ${round(ex)} ${round(ey)}`,
    `L ${round(iex)} ${round(iey)}`,
    `A ${innerR} ${innerR} 0 ${largeArc} 0 ${round(isx)} ${round(isy)}`,
    "Z",
  ].join(" ");
}

/** Slices for one capacity reading, largest-first within the engine's fixed
    order. `localSelected` lets the pie track the in-flight slider values the
    same way the T7 remaining readout does; omit it to render the server's.

    Zero-width segments are dropped — a legend entry for "Mint 0" is noise,
    and a zero-width wedge is an invisible element with a tooltip. */
export function pieSlices(
  cap: Capacity | null,
  cx: number,
  cy: number,
  r: number,
  localSelected?: number,
  innerR = 0,
): PieSlice[] {
  if (!cap || cap.total <= 0) return [];
  const values = SEGMENTS.map((seg) => ({
    ...seg,
    blocks: Math.max(
      0,
      seg.key === "selected" && localSelected != null
        ? localSelected
        : Number(cap[seg.key] ?? 0),
    ),
  }));
  const allocated = values.reduce((sum, v) => sum + v.blocks, 0);
  // Overassignment is real and must not be hidden: when the segments exceed
  // the day, the pie normalizes to the allocated total so every slice stays
  // proportional and Unallocated simply vanishes.
  const denominator = Math.max(cap.total, allocated);
  const unallocated = Math.max(0, cap.total - allocated);

  const all = [
    ...values,
    { key: "unallocated", label: "Unallocated", color: UNALLOCATED_COLOR,
      blocks: unallocated },
  ].filter((v) => v.blocks > 0);

  const out: PieSlice[] = [];
  let cursor = 0;
  for (const v of all) {
    const fraction = v.blocks / denominator;
    out.push({
      key: v.key,
      label: v.label,
      blocks: v.blocks,
      fraction,
      d: wedgePath(cx, cy, r, cursor, Math.min(1, cursor + fraction), innerR),
      color: v.color,
    });
    cursor += fraction;
  }
  return out;
}

/** One-line text alternative for the whole chart. A pie is unreadable to a
    screen reader as geometry; this is what actually gets announced. */
export function pieSummary(slices: PieSlice[]): string {
  if (slices.length === 0) return "No capacity to allocate yet.";
  return slices
    .map((s) => `${s.label} ${s.blocks} blk (${Math.round(s.fraction * 100)}%)`)
    .join(", ");
}
