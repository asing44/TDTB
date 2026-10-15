import type { AnchoredBlock, AnchoredOverride } from "./types";
import { display12h, toMinutes } from "./time";

/** Minting (Trinoor work) rows get the amber `minting` semantic color instead
    of the anchored color — matched by name, covering "Mint Morning",
    "Mint Afternoon", and "🟡 Minting : Regular" (T17 step 2 lite). */
export function isMintingName(name: string): boolean {
  return /^(🟡\s*)?Mint/i.test(name.trim());
}

/** T19: the Live anchored block — same matcher as the backend's
    `shadow._is_live_block` (Step E Live→Todoist reroute). */
export function isLiveName(name: string): boolean {
  const n = name.trim().toLowerCase();
  return n === "live" || n === "⬜ live";
}

/** T19: Live blocks render their selected micro-adventure inline; every other
    block (or no pick) keeps its plain name. */
export function liveDisplayName(
  name: string,
  micro: { pick: { idea: string } | null } | null | undefined,
): string {
  if (!micro?.pick?.idea || !isLiveName(name)) return name;
  return `${name} · 🌱 ${micro.pick.idea}`;
}

export function anchoredBlocks(block: AnchoredBlock, override?: AnchoredOverride): number {
  if (override?.blocks != null) return override.blocks;
  return Math.ceil(block.durationMin / 30);
}

export function anchoredOverrideOf(
  block: AnchoredBlock,
  override?: AnchoredOverride,
): AnchoredOverride {
  return override ?? {
    on: block.on,
    skipToday: block.skipToday,
    time: null,
    blocks: null,
  };
}

/** Resolve a config anchor's effective strip start against the day frame.

    A config anchor whose counted span began but has not ended stays live and
    is clipped at the frame edge by DayStrip; a fully elapsed one sits at the
    frame edge (off unless re-included, and re-placed from the frame start when
    it is). A window-kind anchor stays live from the frame start while its
    window is open, and is elapsed once that window closed before the frame.
    Only a start AFTER the frame end stays "outside frame". Calendar rows never
    reach this. `elapsed` is the single elapsed predicate: a hard anchor whose
    counted span ended at or before the frame start, or a window/template whose
    window closed strictly before it. */
export function anchorFrameStart(
  kind: string,
  opts: {
    start: string | null;
    end: string | null;
    blocks: number;
    frameStart: string;
    frameEnd: string;
  },
): { start: string | null; afterFrame: boolean; elapsed: boolean } {
  const start = opts.start;
  if (start == null) return { start, afterFrame: false, elapsed: false };
  const startMin = toMinutes(start);
  const frameStartMin = toMinutes(opts.frameStart);
  const frameEndMin = toMinutes(opts.frameEnd);
  if (startMin > frameEndMin) return { start, afterFrame: true, elapsed: false };
  if (startMin >= frameStartMin) return { start, afterFrame: false, elapsed: false };
  // The block starts before the frame.
  const windowKind = kind === "window" || kind === "template";
  if (windowKind) {
    // Mirrors the server rule (`past_window_defaults`): the block defaults off
    // only once its window close is strictly before the frame anchor, so an
    // end exactly at the frame stays live. A window with no parseable end
    // stays live.
    const endMin = opts.end == null ? null : toMinutes(opts.end);
    return {
      start: opts.frameStart,
      afterFrame: false,
      elapsed: endMin != null && endMin < frameStartMin,
    };
  }
  const countedEndMin = startMin + Math.max(0, opts.blocks) * 30;
  if (countedEndMin > frameStartMin) {
    // Started but unfinished: stays live, clipped by the strip.
    return { start, afterFrame: false, elapsed: false };
  }
  // Fully elapsed: sits at the frame edge, re-includable.
  return { start: opts.frameStart, afterFrame: false, elapsed: true };
}

/** Mirror of the server's `past_window_defaults` at the frame on screen: a
    config anchor with NO explicit today override defaults OFF once it is
    already elapsed. An explicit on:true override re-includes it (the server's
    own rule), so callers only consult this when the override is absent.
    Shares `anchorFrameStart`, so the elapsed predicate can never drift from
    the strip's placement. */
export function anchoredDefaultOff(
  kind: string,
  opts: {
    start: string | null;
    end: string | null;
    blocks: number;
    frameStart: string;
    frameEnd: string;
  },
): boolean {
  return anchorFrameStart(kind, opts).elapsed;
}

export interface AnchoredFindings {
  /** Structural findings: block Save/Apply. Calendar immutability and missing
      or malformed start/duration only. */
  errors: string[];
  /** Positional findings: never block Save. Rendered amber inline; the server
      validator re-raises anchored-vs-frame issues at sequence time, where they
      flow into the acceptable-defect surface (locked decision 24). */
  warnings: string[];
}

/** Validate the effective same-day anchored placement. Zero blocks is legal:
    the row stays visible but occupies no capacity. Positional findings
    (day frame, window bounds, same-day end) are warnings only; structural
    findings are hard errors. Off/skipped blocks are not validated at all. */
export function validateAnchoredOverride(
  block: AnchoredBlock,
  override: AnchoredOverride,
  frame: { anchor: string; effectiveEod: string },
): AnchoredFindings {
  const none: AnchoredFindings = { errors: [], warnings: [] };
  if (!override.on || override.skipToday) return none;
  if (block.kind === "calendar") {
    return { errors: ["Calendar commitments are read-only."], warnings: [] };
  }
  const start = override.time ?? block.start;
  const blocks = anchoredBlocks(block, override);
  if (!start) return { errors: ["Start time is required."], warnings: [] };
  if (!Number.isInteger(blocks) || blocks < 0) {
    return { errors: ["Duration must use non-negative 30-minute blocks."], warnings: [] };
  }
  const warnings: string[] = [];
  const startMin = toMinutes(start);
  const durationMin = override.blocks == null ? block.durationMin : blocks * 30;
  const endMin = startMin + durationMin;
  if (endMin > 1440) warnings.push("Anchored block must end on the same day.");

  const frameStart = toMinutes(frame.anchor);
  const frameEnd = toMinutes(frame.effectiveEod);
  if (startMin < frameStart || startMin > frameEnd) {
    warnings.push(
      `Outside the day frame (${display12h(frame.anchor)}–${display12h(frame.effectiveEod)}).`,
    );
  }

  if ((block.kind === "window" || block.kind === "template") && block.start && block.end) {
    const windowStart = toMinutes(block.start);
    const windowEnd = toMinutes(block.end);
    if (windowEnd <= windowStart) {
      warnings.push("Source window ends before it starts.");
    } else {
      if (startMin < windowStart) {
        warnings.push(`Starts before the ${display12h(block.start)} window.`);
      }
      if (endMin > windowEnd) {
        warnings.push(`Runs past the window end (${display12h(block.end)}).`);
      }
    }
  }
  return { errors: [], warnings };
}
