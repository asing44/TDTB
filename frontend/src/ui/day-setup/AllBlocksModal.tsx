/* AllBlocksModal — the Set up day "All blocks" surface. The drag strip draws
   only what fits the day frame, so an anchor that is skipped, fully elapsed,
   or starts after the frame end (and any calendar event outside the frame)
   cannot be reached from it. This modal lists every block in one place,
   grouped into config anchors and calendar events, each with a Count today
   switch and a status chip so the operator can toggle today's participation
   without touching config.

   It is a view over the same plumbing the panel already owns: anchor toggles
   go through DaySetupPanel's toggleAnchorSkip (draft + saveAnchoredOverride),
   calendar toggles through the participation-only override (no time/blocks),
   and Reset all to config clears every config anchor's today-only override in
   one pass. Focus, Tab wrap, Escape, and focus return come from useDialog. */

import { useDialog } from "../useDialog";

export type AllBlocksStatus =
  | "counted"
  | "skipped"
  | "outside frame"
  | "elapsed"
  | "after day end";

export interface AllBlocksRow {
  id: string;
  name: string;
  /** 12h range, e.g. "7:45 AM–9:15 AM". */
  time: string;
  blocks: number;
  status: AllBlocksStatus;
  /** The switch state: does this block count toward today's capacity? */
  counting: boolean;
}

export interface AllBlocksModalProps {
  anchors: AllBlocksRow[];
  calendar: AllBlocksRow[];
  onToggleAnchor: (id: string) => void;
  onToggleCalendar: (id: string, nextCounting: boolean) => void;
  onResetAll: () => void;
  onClose: () => void;
}

function blockCountLabel(blocks: number): string {
  return `${blocks} ${blocks === 1 ? "block" : "blocks"}`;
}

function statusClass(status: AllBlocksStatus): string {
  return `all-blocks__status all-blocks__status--${status.replace(/\s+/g, "-")}`;
}

function AllBlocksRowView({
  row,
  onToggle,
}: {
  row: AllBlocksRow;
  onToggle: () => void;
}) {
  return (
    <div class={`all-blocks__row${row.counting ? "" : " all-blocks__row--off"}`}>
      <div class="all-blocks__row-text">
        <span class="all-blocks__name">{row.name}</span>
        <span class="all-blocks__meta">
          <span class="all-blocks__time">{row.time}</span>
          <span class="all-blocks__blocks">{blockCountLabel(row.blocks)}</span>
        </span>
      </div>
      <span class={statusClass(row.status)}>{row.status}</span>
      <button
        type="button"
        role="switch"
        aria-checked={row.counting}
        aria-label={`Count ${row.name} today`}
        class="anchor-popover__switch all-blocks__switch"
        onClick={onToggle}
      />
    </div>
  );
}

function AllBlocksGroup({
  label,
  rows,
  onToggle,
}: {
  label: string;
  rows: AllBlocksRow[];
  onToggle: (row: AllBlocksRow) => void;
}) {
  const off = rows.filter((row) => !row.counting).length;
  return (
    <section class="all-blocks__group" aria-label={label}>
      <div class="all-blocks__group-head">
        <h4 class="all-blocks__group-title">{label}</h4>
        <span class="all-blocks__count">
          {rows.length}
          {off > 0 ? ` · ${off} off` : ""}
        </span>
      </div>
      {rows.map((row) => (
        <AllBlocksRowView key={row.id} row={row} onToggle={() => onToggle(row)} />
      ))}
    </section>
  );
}

export function AllBlocksModal({
  anchors,
  calendar,
  onToggleAnchor,
  onToggleCalendar,
  onResetAll,
  onClose,
}: AllBlocksModalProps) {
  const dialog = useDialog(onClose);
  const total = anchors.length + calendar.length;
  const off = [...anchors, ...calendar].filter((row) => !row.counting).length;
  const summary = total === 0 ? "" : off === 0 ? "All counting" : off === total ? "All off today" : `${off} off today`;

  return (
    <>
      <div class="drawer-backdrop" onClick={onClose} />
      <div
        class="editor all-blocks"
        role="dialog"
        aria-modal="true"
        aria-label="All blocks"
        tabIndex={-1}
        ref={dialog.ref}
        onKeyDown={(e) => dialog.onKeyDown(e as unknown as KeyboardEvent)}
      >
        <header class="all-blocks__head">
          <div class="all-blocks__headline">
            <h3 class="all-blocks__title">All blocks</h3>
            {summary ? <span class="all-blocks__summary">{summary}</span> : null}
          </div>
          <p class="all-blocks__helper">
            Turn a block off to leave it out of today's capacity. Your config is not changed.
          </p>
        </header>
        <div class="all-blocks__body">
          {total === 0 ? <p class="all-blocks__empty">No blocks to show today.</p> : null}
          {anchors.length > 0 ? (
            <AllBlocksGroup label="Anchors" rows={anchors} onToggle={(row) => onToggleAnchor(row.id)} />
          ) : null}
          {calendar.length > 0 ? (
            <AllBlocksGroup
              label="Calendar events"
              rows={calendar}
              onToggle={(row) => onToggleCalendar(row.id, !row.counting)}
            />
          ) : null}
        </div>
        <div class="all-blocks__footer">
          <button type="button" class="header-pill all-blocks__reset" onClick={onResetAll}>
            Reset all to config
          </button>
          <button type="button" class="btn btn--primary all-blocks__done" onClick={onClose}>
            Done
          </button>
        </div>
      </div>
    </>
  );
}
