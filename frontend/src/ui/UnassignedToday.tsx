/* UnassignedToday — the "Not in plan" drawer under the urgency bands: what
   today's plan does NOT hold right now. It reads
   `assignmentRows(s).unassigned`, whose `origin` says where each row's state
   actually lives. Three groups follow from that, and a row never carries the
   wrong affordance:

   - local (U3b): a row taken out of today's plan in this browser by the E key
     or the row switch. Nothing upstream knows, so the row keeps the switch in
     its off state, worded "Add to plan". This is the only group whose action
     is local, so it is the only one that gets a switch.
   - journal-unassign (U5): a Todoist row really unassigned at the source
     (today's runtime journal). The switch would lie here — flipping it is not
     a browser edit — so the row gets an explicit "Restore in Todoist", which
     reverses that row's own journal entry through the controller's reassign
     path. It is an undo, so one click is the whole confirmation.
   - dropped (U5): a date-scoped `drop_from_plan` entry (Capacities and
     calendar). It keeps "Restore today", the same button and the same styling
     the standalone Skipped today section uses.

   A row that first appeared AFTER the day's first load and then defaulted OUT
   carries a small "new" badge, so a mid-day arrival is not mistaken for
   something the operator set aside this morning.

   The empty state is the whole point of the section: when every sourced row is
   in today's plan, there is nothing to render. */

import { useApp, useAppState } from "./context";
import {
  assignmentRows,
  taskActionDisabledReason,
  type AssignmentRow,
  type SkippedTodayRow,
} from "../store/store";
import { ADD_TO_PLAN_LABEL, AssignSwitch } from "./TaskRow";
import { SkippedRow } from "./SkippedToday";

const SOURCE_LABEL: Record<string, string> = {
  todoist: "Todoist",
  capacities: "Capacities",
  calendar: "Calendar",
};

function sourceKey(source: string): string {
  return SOURCE_LABEL[source] ? source : "other";
}

function rowKey(identity: string): string {
  return identity.replace(/[^A-Za-z0-9_-]+/g, "-");
}

/** The badge a mid-day arrival wears. Its title says what "new" means; the
    drawer never has to explain the day's baseline in prose. */
function NewBadge() {
  return (
    <span class="unassigned-today__new" title="Appeared after today's first load">
      new
    </span>
  );
}

function SourceChip({ source }: { source: string }) {
  const key = sourceKey(source);
  return (
    <>
      <span class={`qrow__swatch qrow__swatch--${key}`} aria-hidden="true" />
      <span class={`qrow__src qrow__src--${key}`}>
        {SOURCE_LABEL[source] ?? "Other"}
      </span>
    </>
  );
}

/** Group 1: local plan membership, so the Add to plan switch is exact. */
function LocalRow({ row }: { row: AssignmentRow }) {
  const s = useAppState();
  return (
    <li class="qrow trow trow--skipped trow--unassigned" data-identity={row.identity}>
      <span class="trow__check-slot" aria-hidden="true" />
      <div class="qrow__body">
        <span class="qrow__nameline">
          <span class="qrow__name">{row.name}</span>
          {row.isNewArrival && <NewBadge />}
        </span>
        <span class="qrow__source">
          <SourceChip source={String(row.source)} />
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

/** Group 2: really unassigned at the source. `setAssigned(id, true)` is the
    public entry point that runs the controller's reassign (undo of that row's
    own journal unassign); it is never the local plan switch. */
function RemovedFromTodoistRow({ row }: { row: AssignmentRow }) {
  const { controller } = useApp();
  const s = useAppState();
  const why = taskActionDisabledReason(s, "complete");
  const whyId = `why-reassign-${rowKey(row.identity)}`;
  return (
    <li class="qrow trow trow--skipped" data-identity={row.identity}>
      <div class="qrow__body">
        <span class="qrow__nameline">
          <span class="qrow__name">{row.name}</span>
        </span>
        <span class="qrow__source">
          <SourceChip source="todoist" />
          <span class="trow__state">Unassigned in Todoist</span>
        </span>
      </div>
      {row.minutes != null && <span class="trow__dur">{row.minutes} min</span>}
      <div class="trow__acts">
        <button
          type="button"
          class="trow__btn trow__btn--restore"
          disabled={s.runtimeBusy || why !== null}
          title={why ?? "Restore in Todoist"}
          aria-describedby={why ? whyId : undefined}
          onClick={() => void controller.setAssigned(row.identity, true)}
        >
          Restore in Todoist<span class="sr-only"> {row.name}</span>
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

/** Group 3: a date-scoped drop. Reuses the Skipped today row so Restore today
    looks and behaves identically in both places. */
function DroppedRow({ row, busy }: { row: AssignmentRow; busy: boolean }) {
  const skipped: SkippedTodayRow = {
    identity: row.identity,
    name: row.name,
    droppedAt: null,
    source: row.source,
    kind: row.kind,
    minutes: row.minutes,
  };
  return <SkippedRow row={skipped} busy={busy} />;
}

function GroupHead({ label, count }: { label: string; count: number }) {
  return (
    <h3 class="unassigned-today__group-head">
      {label}
      <span>{count}</span>
    </h3>
  );
}

export function UnassignedToday() {
  const s = useAppState();
  const rows = assignmentRows(s).unassigned;
  if (rows.length === 0) return null;
  const local = rows.filter((row) => row.origin === "local");
  const removed = rows.filter((row) => row.origin === "journal-unassign");
  const dropped = rows.filter((row) => row.origin === "dropped");
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
      {local.length > 0 && (
        <ul class="skipped-today__list" data-group="local">
          {local.map((row) => (
            <LocalRow key={row.identity} row={row} />
          ))}
        </ul>
      )}
      {removed.length > 0 && (
        <div class="unassigned-today__group" data-group="removed-from-todoist">
          <GroupHead label="Removed from Todoist" count={removed.length} />
          <ul class="skipped-today__list">
            {removed.map((row) => (
              <RemovedFromTodoistRow key={row.identity} row={row} />
            ))}
          </ul>
        </div>
      )}
      {dropped.length > 0 && (
        <div class="unassigned-today__group" data-group="skipped-today">
          <GroupHead label="Skipped today" count={dropped.length} />
          <ul class="skipped-today__list">
            {dropped.map((row) => (
              <DroppedRow key={row.identity} row={row} busy={s.runtimeBusy} />
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}
