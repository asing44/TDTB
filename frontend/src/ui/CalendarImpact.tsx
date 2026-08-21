/* CalendarImpact — T12k. Calendar evidence sits before allocator staging so
   the capacity number can be audited before a billed Send. Imported events
   remain read-only; the row actions are the task-style accounting model
   (FEEDBACK-07 A04 / FEEDBACK-09): Exclude from plan / Count (per-day
   participation) plus a local accounted-duration stepper — both projection-
   only, never event or attendance/source mutation (LD19). Ignored calendar
   sources (TickTick-style) are hidden from the impact list entirely. */

import { useApp, useAppState } from "./context";
import { effectiveAnchoredBlocks } from "../store/store";
import { blocksLabel, display12h, formatBlockAmount } from "../model/time";
import type { AnchoredBlock, CalendarCapacityClass } from "../model/types";

/** Local accounted-duration grid for calendar rows: 30-minute blocks, floor 1
    (an attending event always counts), generous ceiling for shaping. */
const CAL_BLOCKS_MIN = 1;
const CAL_BLOCKS_MAX = 12;

function minutesOf(hhmm: string): number {
  const [h, m] = hhmm.split(":").map(Number);
  return h * 60 + m;
}

function inFrame(row: AnchoredBlock, start: string, end: string): boolean {
  if (!row.start) return false;
  const a = minutesOf(row.start);
  const b = row.end ? minutesOf(row.end) : a + row.durationMin;
  return b > minutesOf(start) && a < minutesOf(end);
}

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

export function CalendarImpact({ compact = false }: { compact?: boolean }) {
  const s = useAppState();
  const { controller } = useApp();
  if (!s.inputs) return null;

  const rows = effectiveAnchoredBlocks(s)
    .filter((row) => row.kind === "calendar")
    .filter((row) => inFrame(row, s.inputs!.time.anchor, s.inputs!.time.effectiveEod))
    .sort((a, b) => (a.start ?? "").localeCompare(b.start ?? "") || a.name.localeCompare(b.name));

  // FEEDBACK-09: ignored calendar sources never occupy the impact list —
  // hidden, with a note naming the exclusion. Source data is untouched.
  const visible = rows.filter((row) => capacityClass(row) !== "ignored");
  const hiddenIgnored = rows.length - visible.length;

  if (visible.length === 0) return null;

  const workBusy = s.capacity?.workBusy ?? 0;
  const workEnvelope = s.capacity?.mint ?? 0;
  const workOverflow = s.capacity?.workOverflow ?? 0;

  const hardWalls = visible.filter((row) => isWallClass(capacityClass(row))).length;
  const quarantined = visible.filter((row) => capacityClass(row) === "quarantined").length;

  const list = (
    <ul class="calendar-impact__list">
      {visible.map((row) => {
        const attending = !row.skipToday;
        const cls = capacityClass(row);
        const counted = countedBlocks(row);
        const effectiveBlocks = Math.ceil(row.durationMin / 30);
        const projectedBlocks = s.daySetup.anchored[row.id]?.blocks ?? null;
        const projected = projectedBlocks != null;
        // One accounting save path: participation toggles keep any local
        // projection; duration steps keep attendance. Never a writer call.
        const save = (patch: Record<string, unknown>) =>
          void controller.saveAnchoredOverride(row.id, {
            on: true,
            skipToday: row.skipToday,
            time: null,
            ...(projectedBlocks != null ? { blocks: projectedBlocks } : {}),
            ...patch,
          });
        return (
          <li key={row.id} class="calendar-impact__row">
            <span class="calendar-impact__time">
              {display12h(row.start)}
              {row.end ? `–${display12h(row.end)}` : ""}
            </span>
            <span class="calendar-impact__event">
              <strong>{row.name}</strong>
              <small>
                {row.calendarTitle ?? "Unknown calendar"}
                {isWallClass(cls) && (
                  <span class="calendar-impact__wall">hard block</span>
                )}
              </small>
            </span>
            <span class={`calendar-impact__class calendar-impact__class--${cls}`}>
              {cls}
            </span>
            <span class="calendar-impact__duration">
              {blocksLabel(effectiveBlocks)}
              {projected && (
                <span class="calendar-impact__projection">projection</span>
              )}
            </span>
            <span class="calendar-impact__count">{formatBlockAmount(counted)} counted</span>
            <span class="calendar-impact__reason">{reason(row)}</span>
            <span class="calendar-impact__actions">
              {attending && (
                <span class="calendar-impact__stepper">
                  <button
                    type="button"
                    class="iconbtn"
                    aria-label={`Less counted time for ${row.name} (today only)`}
                    disabled={s.runtimeBusy || counted <= CAL_BLOCKS_MIN}
                    onClick={() => save({ blocks: counted - 1 })}
                  >−</button>
                  <button
                    type="button"
                    class="iconbtn"
                    aria-label={`More counted time for ${row.name} (today only)`}
                    disabled={s.runtimeBusy || counted >= CAL_BLOCKS_MAX}
                    onClick={() => save({ blocks: counted + 1 })}
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
                onClick={() => save({ skipToday: attending })}
              >
                {attending ? "Exclude from plan" : "Count"}
              </button>
              {row.skipToday && (
                <span class="calendar-impact__local">
                  today only · event untouched
                </span>
              )}
            </span>
          </li>
        );
      })}
    </ul>
  );

  return (
    <section class={`calendar-impact${compact ? " calendar-impact--compact" : ""}`} aria-label="Calendar impact">
      <div class="calendar-impact__head">
        <div>
          <h2>Calendar impact</h2>
          <p>
            Work meetings sit inside the work envelope; they are exclusive busy
            time, not extra task room.
          </p>
        </div>
        {(workEnvelope > 0 || workBusy > 0) && (
          <div class="calendar-impact__work" aria-label="Work envelope summary">
            <strong>{formatBlockAmount(workEnvelope)}</strong> work envelope · {formatBlockAmount(workBusy)}
            exclusive busy time
            {workOverflow > 0 ? ` · ${formatBlockAmount(workOverflow)} overflow` : ""}
          </div>
        )}
      </div>
      {hiddenIgnored > 0 && (
        <p class="calendar-impact__ignored">
          {hiddenIgnored} ignored calendar source{hiddenIgnored === 1 ? "" : "s"} excluded
        </p>
      )}
      {compact ? (
        <>
          <div class="calendar-impact__summary" role="status">
            <strong>{visible.length} calendar commitment{visible.length === 1 ? "" : "s"} in frame</strong>
            <span>{hardWalls} hard wall{hardWalls === 1 ? "" : "s"}{quarantined > 0 ? ` · ${quarantined} awaiting review` : ""}</span>
          </div>
          <details class="calendar-impact__review">
            <summary>Review calendar impact</summary>
            {list}
          </details>
        </>
      ) : list}
    </section>
  );
}
