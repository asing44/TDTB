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

import { useEffect, useRef, useState } from "preact/hooks";
import { useApp, useAppState } from "./context";
import { effectiveAnchoredBlocks } from "../store/store";
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

/** Place one lane's rows at proportional positions. Rows wholly outside the
    frame are dropped; partly-outside rows are clipped to the frame edges.
    Overlapping rows are assigned to the first stack row that is free. */
function layoutLane(
  rows: AnchoredBlock[],
  frameStart: number,
  frameEnd: number,
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
    let stack = stackEnds.findIndex((end) => end <= startMin);
    if (stack === -1) {
      stack = stackEnds.length;
      stackEnds.push(endMin);
    } else {
      stackEnds[stack] = endMin;
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

  const all = effectiveAnchoredBlocks(s);
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

  const laid = {} as Record<LaneKey, ReturnType<typeof layoutLane>>;
  for (const lane of LANES) laid[lane.key] = layoutLane(laneRows[lane.key], frameStart, frameEnd);
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
          <div class="timeline__axis-track">
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
                        top: `${p.stack * 34}px`,
                      }}
                      aria-pressed={isSelected}
                      aria-label={`${row.name}, ${display12h(row.start)}${row.end ? `–${display12h(row.end)}` : ""}, ${cls}`}
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
