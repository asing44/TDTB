/* CalendarImpact — U10. Rebuilt from the T12k card list into the mockup's
   "Already placed" lane timeline (reference B, docs/plan/2026-10-09-capacities-
   refresh-ui/reference/B-plan-tasks-1500.png).

   The band list is gone; this is now an hour axis over the day frame with
   three lanes — Calendar, Work, and Anchors. Blocks sit at their proportional
   start time and span their length; blocks that cross the frame edge are
   clipped, blocks wholly outside are omitted with a count. Overlapping blocks
   in one lane stack so no label is covered. Selecting a block (Tab/arrow then
   Enter) opens ONE inline detail row carrying the existing accounting model:
   the counted-duration stepper, Exclude from plan / Count, and close. The
   work window renders as a shaded zone with a header toggle.

   Data selectors, exclusion (skipToday), duration override (blocks), the
   native-fixed rules, and the single saveAnchoredOverride path are unchanged
   from the retired list: this is the view layer only (KTD10). Ignored calendar
   sources stay hidden with a naming note; a frame with no calendar rows shows
   an empty-lane message instead of a blank band. */

import { useCallback, useEffect, useRef, useState } from "preact/hooks";
import { useApp, useAppState } from "./context";
import { effectiveAnchoredBlocks } from "../store/store";
import { displayAnchored } from "../model/calendarEvents";
import { blocksLabel, display12h, formatBlockAmount, toMinutes } from "../model/time";
import type { AnchoredBlock, CalendarCapacityClass } from "../model/types";

/** Local accounted-duration grid for calendar rows: 30-minute blocks, floor 1
    (an attending event always counts), generous ceiling for shaping. */
const CAL_BLOCKS_MIN = 1;
const CAL_BLOCKS_MAX = 12;

/** The mockup's work-hours zone ("Work 9–5"). The frame clamps it, so a
    1:30 PM workday shades only 1:30–5 PM. Presentation-only: the toggle shows
    or hides the shading and never writes accounting state. */

type LaneKey = "calendar" | "work" | "anchors";
const LANES: { key: LaneKey; label: string }[] = [
  { key: "calendar", label: "Calendar" },
  { key: "work", label: "Work" },
  { key: "anchors", label: "Anchors" },
];

function capacityClass(row: AnchoredBlock): CalendarCapacityClass {
  return row.capacityClass ?? "fixed";
}

/** Non-permeable wall classes render the hard-block affordance (A01):
    fixed/work only — ignored/quarantined/window never wall planning. */
function isWallClass(cls: CalendarCapacityClass): boolean {
  return cls === "fixed" || cls === "work";
}

function reason(row: AnchoredBlock): string {
  if (row.skipToday) return "Excluded today";
  const cls = capacityClass(row);
  return cls === "work"
    ? "Inside work budget"
    : cls === "ignored" || cls === "quarantined"
      ? "Not counted"
      : "Fixed";
}

function countedBlocks(row: AnchoredBlock): number {
  if (
    row.skipToday ||
    capacityClass(row) === "ignored" ||
    capacityClass(row) === "quarantined"
  ) {
    return 0;
  }
  return Math.ceil(row.durationMin / 30);
}

/** Short axis label: on-the-hour ticks read "2p"/"11p"; the frame start may
    land on a half hour ("1:30"). */
function axisLabel(minutes: number): string {
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  if (m !== 0) return `${h % 12 === 0 ? 12 : h % 12}:${String(m).padStart(2, "0")}`;
  return `${h % 12 === 0 ? 12 : h % 12}${h < 12 ? "a" : "p"}`;
}

interface Placed {
  row: AnchoredBlock;
  startMin: number;
  endMin: number;
  /** Stack row inside the lane (0-based) so overlaps never cover labels. */
  stack: number;
}

/** POLISH-3: track width used before (or without) a real measurement — jsdom
    reports 0 for every getBoundingClientRect, and the first paint has no laid
    out box yet. */
const TRACK_FALLBACK_PX = 1000;
/** Label metrics: the estimate the timeline has always used for hour labels
    (11px UI text ≈ 6.4px per character) plus the block's own 2×8px padding. */
const LABEL_CHAR_PX = 6.4;
const LABEL_PAD_PX = 16;
/** A widened block is a label affordance, not a claim about duration: it never
    grows past a third of a 1920px track. */
const LABEL_MAX_PX = 180;

/** Smallest visual width that shows `name` on one line, in px. */
function labelMinWidthPx(name: string): number {
  return Math.min(name.length * LABEL_CHAR_PX + LABEL_PAD_PX, LABEL_MAX_PX);
}

/** Place one lane's rows at proportional positions. Rows wholly outside the
    frame are dropped; partly-outside rows are clipped to the frame edges.
    Overlapping rows are assigned to the first stack row that is free. */
function layoutLane(
  rows: AnchoredBlock[],
  frameStart: number,
  frameEnd: number,
  pxPerMinute: number,
): { placed: Placed[]; omitted: number } {
  const placed: Placed[] = [];
  let omitted = 0;
  const ordered = rows
    .filter((row) => row.start != null)
    .map((row) => {
      const start = toMinutes(row.start!);
      const end = row.end ? toMinutes(row.end) : start + row.durationMin;
      return { row, start, end: Math.max(end, start) };
    })
    .sort((a, b) => a.start - b.start || a.row.name.localeCompare(b.row.name));

  const stackEnds: number[] = [];
  for (const entry of ordered) {
    const startMin = Math.max(entry.start, frameStart);
    const endMin = Math.min(entry.end, frameEnd);
    if (endMin <= frameStart || startMin >= frameEnd || endMin <= startMin) {
      omitted += 1;
      continue;
    }
    // POLISH-3: a short block is widened to fit its label, so its VISUAL end
    // (not its true end) is what the next row has to clear. Using the effective
    // end keeps the widened label out of its neighbour's box by stacking the
    // row into the next sub-row instead of letting the boxes overlap.
    const effectiveEnd = Math.max(
      endMin,
      startMin + labelMinWidthPx(entry.row.name) / pxPerMinute,
    );
    let stack = stackEnds.findIndex((end) => end <= startMin);
    if (stack === -1) {
      stack = stackEnds.length;
      stackEnds.push(effectiveEnd);
    } else {
      stackEnds[stack] = effectiveEnd;
    }
    placed.push({ row: entry.row, startMin, endMin, stack });
  }
  return { placed, omitted };
}

export function CalendarImpact({ inline = false }: { inline?: boolean }) {
  const s = useAppState();
  const { controller } = useApp();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const blockRefs = useRef<Record<string, HTMLButtonElement | null>>({});
  const detailRef = useRef<HTMLDivElement | null>(null);
  // POLISH-3: the timeline's own track width drives the label min-width, so a
  // short block on a narrow lane widens less than the same block on a wide one.
  const [trackPx, setTrackPx] = useState<number | null>(null);
  const trackObserver = useRef<ResizeObserver | null>(null);
  const measureTrack = useCallback((el: HTMLDivElement | null) => {
    trackObserver.current?.disconnect();
    trackObserver.current = null;
    if (!el) return;
    const read = () => {
      const width = el.getBoundingClientRect().width;
      setTrackPx(width > 0 ? width : null);
    };
    read();
    if (typeof ResizeObserver !== "undefined") {
      const observer = new ResizeObserver(read);
      observer.observe(el);
      trackObserver.current = observer;
    }
  }, []);
  useEffect(() => () => trackObserver.current?.disconnect(), []);

  const modifier = inline ? " calendar-impact--inline" : "";

  // Move focus into the detail row when it opens so Escape is local and
  // predictable; the block that opened it is remembered for focus return.
  useEffect(() => {
    if (selectedId) detailRef.current?.focus();
  }, [selectedId]);

  if (!s.inputs) return null;
  // A truly empty frame (no rows at all) stays absent from the work surface;
  // the empty-lane message is for a frame that has other lanes but no calendar
  // rows.
  const rowsPresent =
    (s.inputs.anchored ?? []).length > 0;
  const frameStart = toMinutes(s.inputs.time.anchor);
  const frameEnd = toMinutes(s.inputs.time.effectiveEod);
  const frameLen = Math.max(1, frameEnd - frameStart);
  const pct = (minutes: number) => `${((minutes - frameStart) / frameLen) * 100}%`;

  const all = displayAnchored(effectiveAnchoredBlocks(s));
  const ignored = all.filter(
    (row) => row.kind === "calendar" && capacityClass(row) === "ignored",
  );
  const visible = all.filter(
    (row) => !(row.kind === "calendar" && capacityClass(row) === "ignored"),
  );

  const laneRows: Record<LaneKey, AnchoredBlock[]> = {
    calendar: visible.filter(
      (row) => row.kind === "calendar" && capacityClass(row) !== "work",
    ),
    work: visible.filter(
      (row) => row.kind === "calendar" && capacityClass(row) === "work",
    ),
    anchors: visible.filter((row) => row.kind !== "calendar"),
  };

  // px per minute inside the lane track (fallback keeps jsdom/first paint
  // deterministic at 1000px).
  const pxPerMinute = (trackPx ?? TRACK_FALLBACK_PX) / frameLen;

  const laid = {} as Record<LaneKey, ReturnType<typeof layoutLane>>;
  for (const lane of LANES)
    laid[lane.key] = layoutLane(laneRows[lane.key], frameStart, frameEnd, pxPerMinute);
  const omittedTotal = LANES.reduce((n, lane) => n + laid[lane.key].omitted, 0);
  const ignoredCount = ignored.length;
  if (!rowsPresent) return null;

  const selected =
    selectedId == null
      ? null
      : LANES.flatMap((lane) => laid[lane.key].placed).find(
          (p) => p.row.id === selectedId,
        ) ?? null;

  // Axis ticks: an opening tick when the frame starts off the hour, then every
  // hour boundary inside the frame.
  const ticks: number[] = [];
  if (frameStart % 60 !== 0) ticks.push(frameStart);
  for (let m = Math.ceil(frameStart / 60) * 60; m <= frameEnd; m += 60) ticks.push(m);


  /** Move focus between blocks in document order (arrow navigation), so a
    keyboard user reaches a block and Enter opens its detail row. */
  const moveFocus = (from: HTMLElement, delta: number) => {
    const blocks = Array.from(
      from.closest(".timeline")?.querySelectorAll<HTMLButtonElement>("[data-block]") ?? [],
    );
    const idx = blocks.indexOf(from as HTMLButtonElement);
    const next = blocks[idx + delta];
    next?.focus();
  };

  const closeDetail = () => {
    const id = selectedId;
    setSelectedId(null);
    if (id) blockRefs.current[id]?.focus();
  };

  const save = (row: AnchoredBlock, patch: Record<string, unknown>) => {
    const projectedBlocks = s.daySetup.anchored[row.id]?.blocks ?? null;
    void controller.saveAnchoredOverride(row.id, {
      on: true,
      skipToday: row.skipToday,
      time: null,
      ...(projectedBlocks != null ? { blocks: projectedBlocks } : {}),
      ...patch,
    });
  };

  return (
    <section class={`calendar-impact${modifier}`} aria-label="Calendar impact">
      <header class="calendar-impact__head timeline__head">
        <div class="timeline__title">
          <h2>Already placed</h2>
          <p class="timeline__sub">Commitments and anchor zones</p>
        </div>
      </header>

      <div class="timeline">
        <div class="timeline__axis" aria-hidden="true">
          <span class="timeline__lane-label" />
          <div class="timeline__axis-track" ref={measureTrack}>
            {ticks.map((m) => (
              <span key={m} class="timeline__tick" style={{ left: pct(m) }}>
                {axisLabel(m)}
              </span>
            ))}
          </div>
        </div>

        {LANES.map((lane) => {
          const { placed } = laid[lane.key];
          const rows = Math.max(1, ...placed.map((p) => p.stack + 1));
          return (
            <div key={lane.key} class={`timeline__lane timeline__lane--${lane.key}`}>
              <span class="timeline__lane-label">{lane.label}</span>
              <div class="timeline__track" style={{ height: `${rows * 34}px` }}>
                {lane.key === "calendar" && placed.length === 0 && (
                  <p class="timeline__empty">No calendar rows today.</p>
                )}
                {lane.key === "work" && (
                  <span class="timeline__note">Mint sessions sit inside work blocks</span>
                )}
                {placed.map((p) => {
                  const row = p.row;
                  const cls = capacityClass(row);
                  const isSelected = row.id === selectedId;
                  const blockedRange = `${display12h(row.start)}${row.end ? `–${display12h(row.end)}` : ""}`;
                  const labelFloor = labelMinWidthPx(row.name);
                  return (
                    <button
                      key={row.id}
                      type="button"
                      data-block={row.id}
                      ref={(el) => { blockRefs.current[row.id] = el; }}
                      class={`timeline__block timeline__block--${cls}${isSelected ? " timeline__block--selected" : ""}`}
                      style={{
                        left: pct(p.startMin),
                        width: `${((p.endMin - p.startMin) / frameLen) * 100}%`,
                        // A short block keeps its true start and proportional
                        // width; min-width widens the label area only. The
                        // exact times stay in the detail row.
                        minWidth: `${labelFloor}px`,
                        top: `${p.stack * 34}px`,
                      }}
                      title={`${row.name} · ${blockedRange}`}
                      aria-pressed={isSelected}
                      aria-label={`${row.name}, ${blockedRange}, ${cls}`}
                      onClick={() => setSelectedId(row.id)}
                      onKeyDown={(e) => {
                        if (e.key === "ArrowRight") { e.preventDefault(); moveFocus(e.currentTarget as HTMLElement, 1); }
                        else if (e.key === "ArrowLeft") { e.preventDefault(); moveFocus(e.currentTarget as HTMLElement, -1); }
                      }}
                    >
                      <span class="timeline__block-name">{row.name}</span>
                    </button>
                  );
                })}
              </div>
            </div>
          );
        })}
      </div>

      {selected && (() => {
        const row = selected.row;
        const cls = capacityClass(row);
        const attending = !row.skipToday;
        const counted = countedBlocks(row);
        const effectiveBlocks = Math.ceil(row.durationMin / 30);
        const projected = s.daySetup.anchored[row.id]?.blocks != null;
        return (
          <div
            class="timeline__detail"
            role="group"
            aria-label={`Detail for ${row.name}`}
            tabIndex={-1}
            ref={detailRef}
            onKeyDown={(e) => { if (e.key === "Escape") { e.preventDefault(); closeDetail(); } }}
          >
            <span class={`timeline__detail-dot timeline__detail-dot--${cls}`} aria-hidden="true" />
            <span class="timeline__detail-name">{row.name}</span>
            <span class="timeline__detail-meta">
              <span class="timeline__detail-time">
                {display12h(row.start)}{row.end ? `–${display12h(row.end)}` : ""}
              </span>
              <span class={`calendar-impact__class calendar-impact__class--${cls}`}>{cls}</span>
              <span class="timeline__detail-reason">{reason(row)}</span>
              {isWallClass(cls) && <span class="calendar-impact__wall">hard block</span>}
              <span class="timeline__detail-count">{formatBlockAmount(counted)} counted</span>
              {projected && <span class="calendar-impact__projection">projection</span>}
              {row.skipToday && <span class="calendar-impact__local">today only · event untouched</span>}
            </span>
            <span class="timeline__detail-actions">
              {attending && (
                <span class="calendar-impact__stepper">
                  <button
                    type="button"
                    class="iconbtn"
                    aria-label={`Less counted time for ${row.name} (today only)`}
                    disabled={s.runtimeBusy || counted <= CAL_BLOCKS_MIN}
                    onClick={() => save(row, { blocks: counted - 1 })}
                  >−</button>
                  <span class="timeline__detail-dur">{blocksLabel(effectiveBlocks)}</span>
                  <button
                    type="button"
                    class="iconbtn"
                    aria-label={`More counted time for ${row.name} (today only)`}
                    disabled={s.runtimeBusy || counted >= CAL_BLOCKS_MAX}
                    onClick={() => save(row, { blocks: counted + 1 })}
                  >+</button>
                </span>
              )}
              <button
                class="calendar-impact__attend"
                disabled={s.runtimeBusy}
                aria-label={
                  attending
                    ? `Do not count ${row.name} toward today's capacity`
                    : `Count ${row.name} toward today's capacity`
                }
                onClick={() => save(row, { skipToday: attending })}
              >
                {attending ? "Exclude from plan" : "Count"}
              </button>
              <button
                type="button"
                class="iconbtn timeline__detail-close"
                aria-label={`Close detail for ${row.name}`}
                onClick={closeDetail}
              >×</button>
            </span>
          </div>
        );
      })()}

      {(omittedTotal > 0 || ignoredCount > 0) && (
        <p class="timeline__foot">
          {omittedTotal > 0 && (
            <span class="timeline__omitted">
              {omittedTotal} {omittedTotal === 1 ? "block" : "blocks"} outside the day frame
            </span>
          )}
          {ignoredCount > 0 && (
            <span class="calendar-impact__ignored">
              {ignoredCount} ignored calendar source{ignoredCount === 1 ? "" : "s"} excluded
            </span>
          )}
        </p>
      )}
      <p class="timeline__foot-note">
        Tasks and Capacities items aren’t shown here. After commit they’re placed
        in the calendar as complete items.
      </p>
    </section>
  );
}
