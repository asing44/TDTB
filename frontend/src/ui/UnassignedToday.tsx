/* UnassignedToday — the "Not in plan" drawer under the urgency bands: what
   today's plan does NOT hold right now. It reads
   `assignmentRows(s).unassigned`: the local not-in-plan rows first (a row taken
   out of today's plan in this browser by the E key or the row switch), then the
   journal-derived Todoist unassigns, then the Capacities and calendar rows held
   back through the date-scoped drop.

   U3b: the section is named for what it actually is — local plan membership.
   Every row carries the row switch in its off state, worded "Add to plan", so
   putting a row back is one click and the local nature is visible in the copy.
   Nothing here writes to a source; only an upstream-held-back row still needs
   its own restore path, and the controller decides that from the row itself.

   The empty state is the whole point of the section: when every sourced row is
   in today's plan, there is nothing to render. */

import { useAppState } from "./context";
import { assignmentRows, type AssignmentRow } from "../store/store";
import { ADD_TO_PLAN_LABEL, AssignSwitch } from "./TaskRow";

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
          <span class="trow__state">Not in plan</span>
        </span>
      </div>
      {row.minutes != null && <span class="trow__dur">{row.minutes} min</span>}
      <div class="trow__acts">
        <AssignSwitch
          id={row.id}
          name={row.name}
          assigned={false}
          s={s}
          offLabel={ADD_TO_PLAN_LABEL}
          caption={ADD_TO_PLAN_LABEL}
        />
      </div>
    </li>
  );
}

export function UnassignedToday() {
  const s = useAppState();
  const rows = assignmentRows(s).unassigned;
  if (rows.length === 0) return null;
  const count = `${rows.length} not in plan`;
  return (
    <section
      class="skipped-today unassigned-today"
      id="unassigned-today"
      aria-label="Not in plan"
    >
      <div class="skipped-today__head">
        <h2>Not in plan</h2>
        <span>{count}</span>
        <span class="unassigned-today__note">
          Local to this browser — adding a row here changes nothing in Todoist or
          Capacities.
        </span>
      </div>
      <ul class="skipped-today__list">
        {rows.map((row) => (
          <UnassignedRow key={row.identity} row={row} />
        ))}
      </ul>
    </section>
  );
}
