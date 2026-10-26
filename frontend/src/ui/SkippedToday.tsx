/* SkippedToday — task-actions F4a: the "Skipped today" section from the
   approved prototype. It replaces the Out today lane for durable skips: each
   row is a dated `dropped[]` entry (store skippedTodayRows) with its name,
   source chip, minutes and a Restore today button. A legacy entry that
   stored no snapshot shows only its name — no invented chip or duration. */

import { useApp, useAppState } from "./context";
import {
  skippedTodayRows,
  taskActionDisabledReason,
  type SkippedTodayRow,
} from "../store/store";

const SOURCE_LABEL: Record<string, string> = {
  todoist: "Todoist",
  capacities: "Capacities",
  calendar: "Calendar",
};

function rowKey(identity: string): string {
  return identity.replace(/[^A-Za-z0-9_-]+/g, "-");
}

function SkippedRow({ row, busy }: { row: SkippedTodayRow; busy: boolean }) {
  const { controller } = useApp();
  const s = useAppState();
  const why = taskActionDisabledReason(s, "restore");
  const whyId = `why-restore-${rowKey(row.identity)}`;
  const source = row.source ? String(row.source) : null;
  return (
    <li class="qrow trow trow--skipped" data-identity={row.identity}>
      <div class="qrow__body">
        <span class="qrow__nameline">
          <span class="qrow__name">{row.name}</span>
        </span>
        <span class="qrow__source">
          {source && (
            <>
              <span
                class={`qrow__swatch qrow__swatch--${SOURCE_LABEL[source] ? source : "other"}`}
                aria-hidden="true"
              />
              <span class={`qrow__src qrow__src--${SOURCE_LABEL[source] ? source : "other"}`}>
                {SOURCE_LABEL[source] ?? "Other"}
              </span>
            </>
          )}
          <span class="trow__state">Skipped today</span>
        </span>
      </div>
      {row.minutes != null && <span class="trow__dur">{row.minutes} min</span>}
      <div class="trow__acts">
        <button
          type="button"
          class="trow__btn trow__btn--restore"
          disabled={busy || why !== null}
          title={why ?? undefined}
          aria-describedby={why ? whyId : undefined}
          onClick={() => void controller.restoreToday(row.identity)}
        >
          Restore today<span class="sr-only"> {row.name}</span>
        </button>
        {why && (
          <span class="sr-only" id={whyId}>
            {why}
          </span>
        )}
      </div>
    </li>
  );
}

export function SkippedToday() {
  const s = useAppState();
  const rows = skippedTodayRows(s);
  if (rows.length === 0) return null;
  return (
    <section class="skipped-today" id="skipped-today" aria-label="Skipped today">
      <div class="skipped-today__head">
        <h2>Skipped today</h2>
        <span>{rows.length} skipped</span>
      </div>
      <ul class="skipped-today__list">
        {rows.map((row) => (
          <SkippedRow key={row.identity} row={row} busy={s.runtimeBusy} />
        ))}
      </ul>
    </section>
  );
}
