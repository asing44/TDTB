/* CapacityCard — U9. The Plan tasks body opens with the day's capacity story:
   a headline (over-by / remaining), one sentence naming the cause, one
   segmented bar with a hatched overflow tail and a day-capacity marker, a
   legend, and a "Details" disclosure that holds the full ledger the retired
   rail used to show (Rail is reused verbatim inside Details, so nothing the
   old ledger displayed is lost).

   The bar's segments are the engine's own numbers — Calendar (fixed),
   Work (Mint), Anchors, Habits, then the operator's chosen Tasks. Tasks are
   the additive, pre-Commit portion; the slice of Tasks past the capacity
   marker is drawn hatched, so "over" reads as the task tail that crosses the
   marker rather than a rescaled bar.

   Local edits substitute in-flight slider values through `localSelected`, the
   same substitution the table uses, so dragging a duration moves the tasks
   segment and the headline on the same frame.

   KTD7: unknown capacity (no reliable calendar data — the state the store
   already carries as a null read) is named explicitly and is never rendered
   as zero or as an over-capacity day. */

import { useAppState } from "./context";
import { includedItems } from "../store/store";
import { localSelected } from "../store/allocatorView";
import { formatBlockAmount } from "../model/time";
import { Rail } from "./Rail";

interface Segment {
  key: string;
  label: string;
  blocks: number;
  token: string;
}

export function CapacityCard() {
  const s = useAppState();
  const cap = s.capacity;

  if (!cap) {
    return (
      <section class="cap-card cap-card--unknown" aria-label="Capacity">
        <p class="cap-card__unknown" role="status">
          Capacity is unknown — no reliable calendar data today, so the day is
          neither over nor empty. Connect a calendar and refresh to score it.
        </p>
      </section>
    );
  }

  const tasks = localSelected(s);
  const fixedTime = cap.fixed + cap.mint + cap.anchored + cap.habits;
  const allocated = fixedTime + tasks;
  const total = cap.total;
  const over = Math.max(0, allocated - total);
  const remaining = Math.max(0, total - allocated);
  // Over-capacity extends past the capacity marker instead of rescaling the
  // bar, so the marker's position stays meaningful.
  const basis = Math.max(allocated, total, 1);
  const pct = (blocks: number) => `${(Math.max(0, blocks) / basis) * 100}%`;
  // The part of Tasks inside capacity stays solid; the part past the marker is
  // the hatched overflow tail.
  const solidTasks = Math.max(0, tasks - over);
  const overflow = Math.min(tasks, over);
  const taskCount = includedItems(s).length;

  const headline =
    over > 0
      ? `Over by ${formatBlockAmount(over)}`
      : remaining === 0
        ? "Fully booked"
        : `${formatBlockAmount(remaining)} left`;

  const cause =
    fixedTime > total
      ? `Fixed time already exceeds the day by ${formatBlockAmount(fixedTime - total)}.`
      : `Fixed time uses ${formatBlockAmount(fixedTime)} of the day.`;

  const segments: Segment[] = [
    { key: "calendar", label: "Calendar", blocks: cap.fixed, token: "--c-event" },
    { key: "work", label: "Work (Mint)", blocks: cap.mint, token: "--c-minting" },
    { key: "anchors", label: "Anchors", blocks: cap.anchored, token: "--c-anchored" },
    { key: "habits", label: "Habits", blocks: cap.habits, token: "--c-habit" },
    { key: "selected", label: "Tasks", blocks: solidTasks, token: "--c-selected" },
  ];

  const legend: Segment[] = [
    ...segments.slice(0, 4),
    { key: "selected", label: "Tasks", blocks: tasks, token: "--c-selected" },
  ];

  const barLabel = [
    ...segments.map((seg) => `${seg.label} ${formatBlockAmount(seg.blocks)}`),
    ...(overflow > 0 ? [`Over by ${formatBlockAmount(overflow)}`] : []),
    `Day capacity ${formatBlockAmount(total)}`,
  ].join(" · ");

  return (
    <section class="cap-card" aria-label="Capacity">
      <div class="cap-card__head">
        <p
          class={`cap-card__headline ${over > 0 ? "cap-card__headline--over" : ""}`}
          role="status"
        >
          {headline}
        </p>
        <p class="cap-card__cause">
          {cause} Your {taskCount} {taskCount === 1 ? "task" : "tasks"} add{" "}
          {formatBlockAmount(tasks)}.
        </p>
        <details class="cap-card__details">
          <summary class="cap-card__details-summary">Details</summary>
          <Rail />
        </details>
      </div>
      <div class="cap-card__barwrap">
        <div class="cap-card__bar" role="img" aria-label={barLabel}>
          {segments.map((seg) => (
            <div
              key={seg.key}
              class={`cap-card__seg cap-card__seg--${seg.key}`}
              style={{ width: pct(seg.blocks), background: `var(${seg.token})` }}
            />
          ))}
          {overflow > 0 && (
            <div class="cap-card__overflow" style={{ width: pct(overflow) }} />
          )}
        </div>
        <div class="cap-card__mark" style={{ left: `${(total / basis) * 100}%` }} />
      </div>
      <div class="cap-card__foot">
        <ul class="cap-card__legend">
          {legend.map((seg) => (
            <li key={seg.key} class="cap-card__legend-item">
              <span
                class="cap-card__swatch"
                style={{ background: `var(${seg.token})` }}
              />
              <span class="cap-card__legend-label">{seg.label}</span>
              <span class="cap-card__legend-value">
                {formatBlockAmount(seg.blocks)}
              </span>
            </li>
          ))}
        </ul>
        <p class="cap-card__capacity">
          <span class="cap-card__capacity-mark" aria-hidden="true" />
          Day capacity {formatBlockAmount(total)}
        </p>
      </div>
    </section>
  );
}
