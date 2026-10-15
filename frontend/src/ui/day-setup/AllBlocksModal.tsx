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
    <div class="all-blocks__row">
      <div class="all-blocks__row-text">
        <span class="all-blocks__name">{row.name}</span>
        <span class="all-blocks__meta">
          <span class="all-blocks__time">{row.time}</span>
          <span class="all-blocks__blocks">{blockCountLabel(row.blocks)}</span>
          <span class={statusClass(row.status)}>{row.status}</span>
        </span>
      </div>
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

export function AllBlocksModal({
  anchors,
  calendar,
  onToggleAnchor,
  onToggleCalendar,
  onResetAll,
  onClose,
}: AllBlocksModalProps) {
  const dialog = useDialog(onClose);

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
        <h3 class="all-blocks__title">All blocks</h3>
        <div class="all-blocks__body">
          <section class="all-blocks__group" aria-label="Anchors">
            <h4 class="all-blocks__group-title">Anchors</h4>
            {anchors.length === 0 ? (
              <p class="all-blocks__empty">No anchors configured.</p>
            ) : (
              anchors.map((row) => (
                <AllBlocksRowView key={row.id} row={row} onToggle={() => onToggleAnchor(row.id)} />
              ))
            )}
          </section>
          <section class="all-blocks__group" aria-label="Calendar events">
            <h4 class="all-blocks__group-title">Calendar events</h4>
            {calendar.length === 0 ? (
              <p class="all-blocks__empty">No calendar events today.</p>
            ) : (
              calendar.map((row) => (
                <AllBlocksRowView
                  key={row.id}
                  row={row}
                  onToggle={() => onToggleCalendar(row.id, !row.counting)}
                />
              ))
            )}
          </section>
        </div>
        <div class="all-blocks__footer">
          <button type="button" class="header-pill all-blocks__reset" onClick={onResetAll}>
            Reset all to config
          </button>
          <button type="button" class="btn btn--primary" onClick={onClose}>
            Done
          </button>
        </div>
      </div>
    </>
  );
}
