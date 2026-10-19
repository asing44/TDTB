/* UnassignedToday — KTD4: the section under the urgency bands that lists what
   is held back from today's plan. It reads `assignmentRows(s).unassigned`:
   the journal-derived Todoist unassigns first, then the Capacities and
   calendar rows held back through the date-scoped drop. Each row mirrors a
   Skipped today row (name, source chip, minutes) and carries the same
   Assigned switch as the bands, here off, to bring it back. Nothing here
   writes to a source. */

import { useAppState } from "./context";
import { assignmentRows, type AssignmentRow } from "../store/store";
import { AssignSwitch } from "./TaskRow";

const SOURCE_LABEL: Record<string, string> = {
  todoist: "Todoist",
  capacities: "Capacities",
  calendar: "Calendar",
};

function UnassignedRow({ row }: { row: AssignmentRow }) {
  const s = useAppState();
  const source = String(row.source);
  const key = SOURCE_LABEL[source] ? source : "other";
  return (
    <li class="qrow trow trow--skipped trow--unassigned" data-identity={row.identity}>
      <span class="trow__check-slot" aria-hidden="true" />
      <div class="qrow__body">
        <span class="qrow__nameline">
          <span class="qrow__name">{row.name}</span>
        </span>
        <span class="qrow__source">
          <span class={`qrow__swatch qrow__swatch--${key}`} aria-hidden="true" />
          <span class={`qrow__src qrow__src--${key}`}>
            {SOURCE_LABEL[source] ?? "Other"}
          </span>
          <span class="trow__state">Unassigned today</span>
        </span>
      </div>
      {row.minutes != null && <span class="trow__dur">{row.minutes} min</span>}
      <div class="trow__acts">
        <AssignSwitch id={row.id} name={row.name} assigned={false} s={s} />
      </div>
    </li>
  );
}

export function UnassignedToday() {
  const s = useAppState();
  const rows = assignmentRows(s).unassigned;
  if (rows.length === 0) return null;
  return (
    <section class="skipped-today unassigned-today" id="unassigned-today" aria-label="Unassigned">
      <div class="skipped-today__head">
        <h2>Unassigned</h2>
        <span>{rows.length} unassigned</span>
      </div>
      <ul class="skipped-today__list">
        {rows.map((row) => (
          <UnassignedRow key={row.identity} row={row} />
        ))}
      </ul>
    </section>
  );
}
